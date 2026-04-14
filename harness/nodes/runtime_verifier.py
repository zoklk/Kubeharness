"""
Runtime Verifier 노드.

Phase 1 (결정적 게이트):
    run_runtime_phase1() → helm install, kubectl wait, smoke test

Phase 2 (LLM 진단) — Phase 1 fail 시에만 실행:
    kagent read-only tool + ReadFileTool로 실패 원인 진단 + 파일 수정
    {"passed": false, "observations": [...], "suggestions": [...], "files": [...]}

Phase 1 pass → Phase 2 skip, verification.passed=True (smoke test 포함 전부 통과)
Phase 1 fail → Phase 2 진단 실행, verification.passed=False (항상)
             → phase2.files 비어 있지 않으면 파일 쓰기 → 그래프가 자가 루프
"""

import asyncio
from pathlib import Path

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from harness.config import ARTIFACT_PREFIX, HARNESS_ROOT, NAMESPACE, PROJECT_ROOT, build_cluster_env_section
from harness.llm import client as llm
from harness.llm.artifacts import scan_service_files, write_files as _shared_write_files
from harness.llm.client import get_node_profile, get_profile_cfg
from harness.llm.context import extract_dependencies, read_knowledge
from harness.llm.tool_loop import request_json_response, run_tool_loop
from harness.llm.json_utils import extract_json_dict
from harness.mcp.kagent_client import get_kagent_tools, load_node_tools, tools_as_chat_dicts
from harness.state import HarnessState
from harness.tools.local_tools import ReadFileTool, read_file_tool_dict
from harness.verifiers import node_log_dir
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
    if phase2.get("files"):
        _console.print(f"  [cyan]→ {len(phase2['files'])} file(s) to write[/cyan]")


_PROMPT_PATH = HARNESS_ROOT / "context" / "prompts" / "runtime_verifier_prompt.md"
_MAX_TOOL_TURNS = 5

_PHASE2_SCHEMA = (
    '{"passed": false, '
    '"failure_source": "implementation"|"smoke_test"|"environment", '
    '"observations": [{"area": "...", "finding": "..."}], '
    '"suggestions": ["..."], '
    '"files": [{"path": "edge-server/...", "content": "..."}]}'
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a Kubernetes deployment diagnostician. "
    "Phase 1 deterministic checks have failed. "
    "Use the available tools to investigate the root cause "
    "(pod logs, events, describe resources), then respond ONLY with "
    "valid JSON matching this schema exactly:\n"
    '{"passed": false, "failure_source": "implementation"|"smoke_test"|"environment", '
    '"observations": [{"area": str, "finding": str}], "suggestions": [str], '
    f'"files": [{{"path": "{ARTIFACT_PREFIX}...", "content": "full file content"}}]}}\n'
    "passed must always be false. "
    "failure_source: 'implementation' if the deployment code/config is wrong; "
    "'smoke_test' if the test script itself has a bug (wrong command, wrong assumption, wrong auth); "
    "'environment' if the issue is outside the deployment (cluster, DNS, network). "
    "When failure_source='smoke_test', set files=[] and explain the test bug in suggestions. "
    "files is optional — include only when you need to modify files to fix the issue. "
    "Use read_file tool to check current file content before writing. "
    "Do not include any text outside the JSON object."
)


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        content = _PROMPT_PATH.read_text(encoding="utf-8").strip()
        content = content.replace("{NAMESPACE}", NAMESPACE)
        return content if content else _DEFAULT_SYSTEM_PROMPT
    return _DEFAULT_SYSTEM_PROMPT


async def _load_tools() -> tuple[list, list[dict]]:
    """kagent tools 로드. 실패 시 경고 후 빈 리스트로 graceful degradation."""
    return await load_node_tools("runtime_verifier_tools", "runtime_verifier", _console)


def _write_files(files: list[dict]) -> tuple[list[str], str | None]:
    """artifacts.write_files 위임."""
    return _shared_write_files(files, console=_console)


def _artifact_files_listing(service_name: str) -> str:
    """Phase 2 LLM에게 서비스 아티팩트 파일 목록 제공 — 제안 시 정확한 파일 경로 참조용."""
    all_files = scan_service_files(service_name, subdirs=("helm", "docker"))
    if not all_files:
        return ""
    return "\n\n## Artifact Files\n" + "\n".join(f"- `{f}`" for f in all_files)


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
    Robust JSON 추출 (extract_json_dict 위임).
    failure_source 정규화 및 기본값 로직 유지.
    """
    data = extract_json_dict(content)
    if data is not None:
        raw_source = data.get("failure_source", "")
        failure_source = raw_source if raw_source in ("implementation", "smoke_test", "environment") else "implementation"
        return {
            "passed": bool(data.get("passed", False)),
            "failure_source": failure_source,
            "observations": data.get("observations", []),
            "suggestions": data.get("suggestions", []),
            "files": data.get("files", []),
        }
    return {
        "passed": False,
        "observations": [],
        "suggestions": [f"LLM response parse failed: {content[:300]}"],
        "files": [],
    }


def _build_verifier_user_message(
    service_name: str,
    sub_goal: dict,
    sub_goal_spec: str,
    phase1: dict,
    knowledge_parts: list,
    user_hint: str,
) -> str:
    content = (
        f"Service: {service_name}\n"
        f"Phase: {sub_goal.get('phase', '')}\n\n"
        + (f"## Sub-Goal Specification\n{sub_goal_spec}\n\n" if sub_goal_spec else "")
        + build_cluster_env_section(include_authoring_hint=False) + "\n\n"
        + _phase1_summary(phase1)
        + _artifact_files_listing(service_name)
        + ("".join(f"\n\n{title}\n{c}" for title, c in knowledge_parts) if knowledge_parts else "")
        + "\n\nPhase 1 failed. Use the available tools to diagnose the root cause "
          "(check pod logs, events, describe resources). "
          "When you identify a fix, **write the corrected files directly** in the `files` field: "
          "call `read_file` first to get the current content, then include the full corrected content. "
          "The harness will write the files and re-deploy automatically — this is a self-loop, not a handoff. "
          "Only fall back to `suggestions` text if you genuinely cannot determine the fix. "
          "Reference exact file paths from ## Artifact Files above. "
          "Set passed=false in your response."
    )
    if user_hint:
        content += f"\n\n## Additional Instructions from Operator\n{user_hint}"
    return content


# ── 노드 함수 ──────────────────────────────────────────────────────────────────

async def runtime_verifier_node(state: HarnessState) -> dict:
    """
    LangGraph 노드. async 선언으로 asyncio.run() 중첩 없이
    상위 이벤트 루프(FastAPI, async LangGraph 워커 등)에서 안전하게 await 가능.

    Phase 1 pass → END (그래프 종료)
    Phase 1 fail → Phase 2 LLM 진단 → 파일 쓰기(있으면) → 자가 루프
    """
    sub_goal = state["current_sub_goal"]
    service_name = sub_goal.get("service_name") or sub_goal["name"]
    runtime_log_dir = node_log_dir(state, "runtime")
    log_dir_base = str(Path(runtime_log_dir).parent) + "/"
    runtime_retry_count = state.get("runtime_retry_count", 0)
    user_hint = state.get("user_hint", "") or ""

    _console.print(
        f"\n[cyan]⟳[/cyan]  Runtime Verifier  [{service_name}]  Phase 1"
        f"  (runtime_retry={runtime_retry_count}) ..."
    )

    # ── Phase 1 (subprocess → to_thread으로 이벤트 루프 블로킹 방지) ──────────
    phase1 = await asyncio.to_thread(
        run_runtime_phase1, service_name, sub_goal["name"], sub_goal["phase"], log_dir=runtime_log_dir
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
    technology_name = state.get("technology_name") or service_name
    deps = extract_dependencies(sub_goal_spec)
    knowledge_parts = read_knowledge(technology_name, deps)

    user_message = _build_verifier_user_message(
        service_name, sub_goal, sub_goal_spec, phase1, knowledge_parts, user_hint
    )

    messages = [
        {"role": "system", "content": _load_system_prompt()},
        {"role": "user", "content": user_message},
    ]

    phase2_profile = get_node_profile("runtime_verifier_phase2")
    web_cfg = get_profile_cfg(phase2_profile).get("web_search", {})

    # web_search: web_cfg 존재 시 항상 활성화 (error_count 조건 제거)
    web_search_active = bool(web_cfg)

    tool_objs, tools_dicts = await _load_tools()
    # ReadFileTool 추가: Phase 2 LLM이 파일 수정 전 현재 내용을 읽을 수 있도록
    tool_objs = [*tool_objs, ReadFileTool()]
    tools_dicts = [*tools_dicts, read_file_tool_dict()]

    if web_search_active:
        tools_dicts = tools_dicts + [{
            "type": web_cfg.get("tool_type", "web_search_20260209"),
            "name": "web_search",
            "max_uses": web_cfg.get("max_uses", 5),
        }]

    phase2_max_turns = get_profile_cfg(phase2_profile).get("max_tool_turns", _MAX_TOOL_TURNS)

    messages = await run_tool_loop(
        messages, tools_dicts, tool_objs,
        max_turns=phase2_max_turns,
        profile=phase2_profile,
    )

    final_content = messages[-1].get("content", "") if messages else ""

    if extract_json_dict(final_content) is None:
        data, messages = request_json_response(messages, phase2_profile, _PHASE2_SCHEMA)
        if data is not None:
            final_content = messages[-1].get("content", "")

    phase2 = _parse_phase2(final_content)

    _print_phase2(phase2)

    # Phase 2 파일 쓰기 (파일 수정 제안이 있는 경우)
    files_to_write = phase2.get("files", [])
    if files_to_write:
        written, write_err = _write_files(files_to_write)
        if write_err:
            _console.print(f"  [red]⚠ File write error: {write_err}[/red]")
        elif written:
            _console.print(f"  [green]✓ Phase 2 wrote {len(written)} file(s)[/green]")
            for p in written:
                _console.print(f"    [green]✓[/green] {p}")

    # files는 state에 저장하지 않음 (bloat 방지)
    phase2_for_state = {k: v for k, v in phase2.items() if k != "files"}

    return {
        "current_sub_goal": {**sub_goal, "stage": "runtime_verify"},
        "runtime_verification": {
            "runtime_phase1": phase1,
            "runtime_phase2": phase2_for_state,
        },
        "verification": {
            **state.get("verification", {}),
            "passed": False,  # Phase 1 failed → always False → 그래프 자가 루프
            "stage": "runtime",
            "failure_source": phase2.get("failure_source", "implementation"),
            "runtime_phase1": phase1,
            "runtime_phase2": phase2_for_state,
            "log_dir": log_dir_base,
        },
        "runtime_retry_count": runtime_retry_count + 1,
        "user_hint": "",  # 소비 — 다음 루프에 누적 방지
    }
