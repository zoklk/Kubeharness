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
from typing import Any

from harness.llm import client as llm
from harness.mcp.kagent_client import get_kagent_tools, tools_as_chat_dicts
from harness.state import HarnessState
from harness.verifiers.runtime_gates import run_runtime_phase1

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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
        return _PROMPT_PATH.read_text(encoding="utf-8")
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


async def _execute_tools_parallel(tool_calls: list[dict], tool_map: dict) -> list[tuple[dict, str]]:
    """LLM이 요청한 tool_calls를 asyncio.gather로 병렬 실행."""
    async def _exec_one(tc: dict) -> tuple[dict, str]:
        tool = tool_map.get(tc["name"])
        if tool is None:
            return tc, f"Unknown tool: {tc['name']}"
        try:
            return tc, str(await tool.ainvoke(tc["input"]))
        except Exception as e:
            return tc, f"Tool error: {e}"

    return list(await asyncio.gather(*[_exec_one(tc) for tc in tool_calls]))


async def _run_tool_loop(
    messages: list[dict],
    tools: list[dict],
    tool_objs: list,
) -> list[dict]:
    """
    LLM이 tool_calls를 반환하는 동안 실행 루프.
    한 턴에 여러 tool_calls가 있으면 asyncio.gather로 병렬 실행.
    최대 _MAX_TOOL_TURNS 회 후 tools 없이 최종 응답 요청.
    항상 마지막 메시지가 role=assistant인 상태로 반환.
    """
    tool_map = {t.name: t for t in tool_objs}

    for _ in range(_MAX_TOOL_TURNS):
        resp = llm.chat(messages, tools=tools or None)  # sync; blocks loop but acceptable

        if not resp.get("tool_calls"):
            messages.append({"role": "assistant", "content": resp.get("content", "")})
            return messages

        # assistant turn (gemini raw content 포함)
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": resp.get("content", ""),
            "tool_calls": resp["tool_calls"],
        }
        if "_gemini_raw_content" in resp:
            assistant_msg["_gemini_raw_content"] = resp["_gemini_raw_content"]
        messages.append(assistant_msg)

        # 모든 tool_calls를 한 번에 병렬 실행 (await, 이벤트 루프 양보)
        results = await _execute_tools_parallel(resp["tool_calls"], tool_map)
        for tc, result_str in results:
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "content": result_str,
            })

    # max turns 초과 → tools 없이 최종 응답 요청
    resp = llm.chat(messages)
    messages.append({"role": "assistant", "content": resp.get("content", "")})
    return messages


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

    # ── Phase 1 (subprocess → to_thread으로 이벤트 루프 블로킹 방지) ──────────
    phase1 = await asyncio.to_thread(
        run_runtime_phase1, service_name, log_dir=runtime_log_dir
    )

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
    messages = await _run_tool_loop(messages, tools_dicts, tool_objs)

    final_content = messages[-1].get("content", "") if messages else ""
    phase2 = _parse_phase2(final_content)

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
