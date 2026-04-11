"""
Runtime Verifier 노드.

Phase 1 (결정적 게이트):
    run_runtime_phase1() → helm install, kubectl wait, events, smoke test

Phase 2 (LLM 진단) — Phase 1 fail 시에만 실행:
    kagent read-only tool로 실패 원인 진단
    {"passed": false, "observations": [...], "suggestions": [...]}

Phase 1 pass → Phase 2 skip, verification.passed=True (smoke test 포함 전부 통과)
Phase 1 fail → Phase 2 진단 실행, verification.passed=False (항상)
"""

import asyncio
import json
import re
from pathlib import Path

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from harness.config import PROJECT_ROOT
from harness.llm.tool_loop import run_tool_loop
from harness.mcp.kagent_client import get_kagent_tools, tools_as_chat_dicts
from harness.state import HarnessState
from harness.verifiers.runtime_gates import run_runtime_phase1

_console = Console()


def _print_phase1(phase1: dict) -> None:
    t = Table(box=box.SIMPLE, show_header=True)
    t.add_column("check", style="dim")
    t.add_column("status")
    t.add_column("detail")
    for c in phase1.get("checks", []):
        s = c["status"]
        color = "green" if s == "pass" else ("yellow" if s == "skip" else "red")
        t.add_row(c["name"], f"[{color}]{s}[/{color}]", escape(c.get("detail", "")))
    _console.print("[bold]Runtime Phase 1[/bold]")
    _console.print(t)


def _print_phase2(phase2: dict) -> None:
    p2_status = "[green]pass[/green]" if phase2.get("passed") else "[red]fail[/red]"
    _console.print(f"[bold]Runtime Phase 2[/bold]: {p2_status}")
    for obs in phase2.get("observations", []):
        _console.print(f"  [dim]{escape(obs.get('area',''))}[/dim]: {escape(obs.get('finding',''))}")
    for sug in phase2.get("suggestions", []):
        _console.print(f"  [yellow]→ {escape(sug)}[/yellow]")


_PROMPT_PATH = PROJECT_ROOT / "context" / "harness" / "runtime_verifier_prompt.md"
_MAX_TOOL_TURNS = 5

_DEFAULT_SYSTEM_PROMPT = (
    "You are a Kubernetes deployment diagnostician. "
    "Phase 1 deterministic checks have failed. "
    "Use the available tools to investigate the root cause "
    "(pod logs, events, describe resources), then respond ONLY with "
    "valid JSON matching this schema exactly:\n"
    '{"passed": false, "observations": [{"area": str, "finding": str}], "suggestions": [str]}\n'
    "passed must always be false. Focus on actionable fix suggestions. "
    "Do not include any text outside the JSON object."
)


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _log_dir(state: HarnessState, sub: str) -> str:
    phase = state.get("current_phase", "unknown")
    name = state["current_sub_goal"]["name"]
    attempt = state.get("error_count", 0)
    return str(PROJECT_ROOT / f"logs/raw/{phase}/{name}/attempt_{attempt}/{sub}")


def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        content = _PROMPT_PATH.read_text(encoding="utf-8").strip()
        return content if content else _DEFAULT_SYSTEM_PROMPT
    return _DEFAULT_SYSTEM_PROMPT


async def _load_tools() -> tuple[list, list[dict]]:
    """kagent tools 로드. 실패 시 경고 후 빈 리스트로 graceful degradation."""
    try:
        tool_objs = await get_kagent_tools("runtime_verifier_tools")
        return tool_objs, tools_as_chat_dicts(tool_objs)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("kagent tools unavailable (runtime_verifier): %s", e)
        return [], []


def _phase1_summary(phase1: dict) -> str:
    """
    fail 항목은 detail 전체 노출, pass/skip은 이름만 표시.
    LLM이 문제 지점에 빠르게 집중하도록 정보 밀도 조절.
    """
    lines = ["## Phase 1 Results"]
    for c in phase1.get("checks", []):
        status = c["status"]
        if status == "fail":
            lines.append(f"- [FAIL] {c['name']}: {c['detail']}")
        elif status == "pass":
            lines.append(f"- [PASS] {c['name']}")
        else:  # skip
            lines.append(f"- [SKIP] {c['name']}")
    return "\n".join(lines)


def _parse_phase2(content: str) -> dict:
    """
    Robust JSON 추출. 세 가지 전략을 순서대로 시도:
    1. 전체 텍스트 직접 파싱
    2. ```json...``` / ```...``` 코드 블록 추출
    3. 첫 { 부터 마지막 } 까지 추출 (앞뒤 서론/후론 무시)
    """
    text = content.strip()
    candidates: list[str] = [text]

    # 전략 2: 코드 블록
    for m in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL):
        candidates.append(m.group(1).strip())

    # 전략 3: 첫 { ~ 마지막 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return {
                    "passed": bool(data.get("passed", False)),
                    "observations": data.get("observations", []),
                    "suggestions": data.get("suggestions", []),
                }
        except (json.JSONDecodeError, ValueError):
            continue

    return {
        "passed": False,
        "observations": [],
        "suggestions": [f"LLM response parse failed: {content[:300]}"],
    }


# ── 노드 함수 ──────────────────────────────────────────────────────────────────

async def runtime_verifier_node(state: HarnessState) -> dict:
    """
    LangGraph 노드. async 선언으로 asyncio.run() 중첩 없이
    상위 이벤트 루프(FastAPI, async LangGraph 워커 등)에서 안전하게 await 가능.
    """
    sub_goal = state["current_sub_goal"]
    service_name = sub_goal.get("service_name") or sub_goal["name"]
    runtime_log_dir = _log_dir(state, "runtime")
    log_dir_base = str(Path(runtime_log_dir).parent) + "/"

    _console.print(f"\n[cyan]⟳[/cyan]  Runtime Verifier  [{service_name}]  Phase 1 ...")

    # ── Phase 1 (subprocess → to_thread으로 이벤트 루프 블로킹 방지) ──────────
    phase1 = await asyncio.to_thread(
        run_runtime_phase1, service_name, log_dir=runtime_log_dir
    )
    _print_phase1(phase1)

    if phase1["passed"]:
        # Phase 1 완전 통과 (smoke test 포함) — LLM 진단 불필요
        return {
            "current_sub_goal": {**sub_goal, "stage": "runtime_verify"},
            "runtime_verification": {"runtime_phase1": phase1},
            "verification": {
                **state.get("verification", {}),
                "passed": True,
                "stage": "runtime",
                "runtime_phase1": phase1,
                "log_dir": log_dir_base,
            },
        }

    # ── Phase 2 (LLM 진단) — Phase 1 fail 시에만 실행 ───────────────────────
    _console.print(f"  [yellow]Phase 1 failed — starting LLM diagnosis ...[/yellow]")
    sub_goal_spec = state.get("sub_goal_spec", "")
    messages = [
        {"role": "system", "content": _load_system_prompt()},
        {
            "role": "user",
            "content": (
                f"Service: {service_name}\n"
                f"Phase: {sub_goal.get('phase', '')}\n\n"
                + (f"## Sub-Goal Specification\n{sub_goal_spec}\n\n" if sub_goal_spec else "")
                + _phase1_summary(phase1)
                + "\n\nPhase 1 failed. Use the available tools to diagnose the root cause "
                  "(check pod logs, events, describe resources). "
                  "Identify why the deployment failed and provide actionable fix suggestions. "
                  "Set passed=false in your response."
            ),
        },
    ]

    tool_objs, tools_dicts = await _load_tools()
    messages = await run_tool_loop(messages, tools_dicts, tool_objs, max_turns=_MAX_TOOL_TURNS)

    final_content = messages[-1].get("content", "") if messages else ""
    phase2 = _parse_phase2(final_content)
    _print_phase2(phase2)

    return {
        "current_sub_goal": {**sub_goal, "stage": "runtime_verify"},
        "runtime_verification": {
            "runtime_phase1": phase1,
            "runtime_phase2": phase2,
        },
        "verification": {
            **state.get("verification", {}),
            "passed": False,  # Phase 1 failed → always False
            "stage": "runtime",
            "runtime_phase1": phase1,
            "runtime_phase2": phase2,
            "log_dir": log_dir_base,
        },
    }
