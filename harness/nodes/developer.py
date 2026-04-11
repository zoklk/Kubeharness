"""
Developer 노드.

1. context 로드: developer_prompt.md, conventions.md, tech_stack.md,
   phases/<phase>.md 의 current sub_goal 섹션
2. verification 실패 재시도 시: 실패 체크 상세 첨부, error_count 증가
3. kagent MCP developer_tools (read-only) 첨부
4. multi-turn tool loop (최대 _MAX_TOOL_TURNS 회)
5. 최종 JSON 파싱 → edge-server/ prefix 가드 후 파일 쓰기
6. dev_artifacts 업데이트 반환
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from harness.llm import client as llm
from harness.mcp.kagent_client import get_kagent_tools, tools_as_chat_dicts
from harness.state import HarnessState

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONTEXT_DIR = PROJECT_ROOT / "context"
_PROMPT_PATH = _CONTEXT_DIR / "developer_prompt.md"
_ALLOWED_PREFIX = "edge-server/"
_MAX_TOOL_TURNS = 10

_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert Kubernetes and Helm developer. "
    "Write manifests or Helm charts according to the provided specifications. "
    "Use available tools to inspect cluster state as needed. "
    "Respond ONLY with valid JSON matching this schema exactly:\n"
    '{"files": [{"path": "edge-server/...", "content": "..."}], "notes": "..."}\n'
    "All file paths MUST start with edge-server/. "
    "Do not include any text outside the JSON object."
)


# ── 시스템 프롬프트 ────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        content = _PROMPT_PATH.read_text(encoding="utf-8").strip()
        return content if content else _DEFAULT_SYSTEM_PROMPT
    return _DEFAULT_SYSTEM_PROMPT


# ── 컨텍스트 로드 ──────────────────────────────────────────────────────────────

def _read_context(name: str) -> str:
    p = _CONTEXT_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else f"[{name} not found]"


def _extract_subgoal_section(phase_md: str, sub_goal_name: str) -> str:
    """
    phase.md에서 sub_goal_name을 포함하는 heading 섹션 추출.

    - Fuzzy matching: 헤딩 텍스트에 sub_goal_name이 포함(contains)되면 매칭.
      예) '## 1. prometheus 설치' → 'prometheus' 키워드로 매칭 성공
    - 지원 레벨: # ~ #### (1~4개 #)
    - 경계 감지: 시작 레벨과 같거나 높은 레벨(# 수 ≤ 시작 레벨)의
      다음 헤딩이 나타나는 곳에서 섹션 종료. 하위 헤딩(### 등)은 섹션에 포함.
      예) ##로 시작 → 다음 ## 또는 #을 만날 때까지 (### 포함)
    - 마지막 섹션이면 파일 끝까지 포함.
    - 못 찾으면 전체 문서 반환.
    """
    pattern = re.compile(
        rf"^(#{{1,4}})\s+.*{re.escape(sub_goal_name)}.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pattern.search(phase_md)
    if not m:
        return phase_md

    level = len(m.group(1))  # # → 1, ## → 2, ### → 3, #### → 4
    # 같은 레벨 이상(# 수 ≤ level) 헤딩에서 섹션 종료
    end_pattern = re.compile(rf"^#{{{1},{level}}}\s", re.MULTILINE)
    end_m = end_pattern.search(phase_md, m.end())
    end = end_m.start() if end_m else len(phase_md)

    return phase_md[m.start():end].strip()


def _verification_summary(verification: dict) -> str:
    """
    실패 체크와 LLM 제안을 정보 밀도 높게 요약.
    pass/skip은 생략, fail만 detail 포함.
    """
    lines = [f"Stage: {verification.get('stage', 'unknown')}"]

    # static 또는 runtime 공통 checks
    for c in verification.get("checks", []):
        if c["status"] == "fail":
            lines.append(f"[FAIL] {c['name']}: {c['detail']}")

    # runtime phase1 체크
    for c in verification.get("runtime_phase1", {}).get("checks", []):
        if c["status"] == "fail":
            lines.append(f"[FAIL] runtime/{c['name']}: {c['detail']}")

    # runtime phase2 LLM 관찰 및 제안
    p2 = verification.get("runtime_phase2", {})
    if p2 and not p2.get("passed"):
        for obs in p2.get("observations", []):
            lines.append(f"[OBS] {obs.get('area', '')}: {obs.get('finding', '')}")
        for sug in p2.get("suggestions", []):
            lines.append(f"[SUGGESTION] {sug}")

    return "\n".join(lines)


def _build_user_message(state: HarnessState) -> str:
    sub_goal = state["current_sub_goal"]
    phase = sub_goal["phase"]
    name = sub_goal["name"]

    phase_md = _read_context(f"phases/{phase}.md")
    sub_goal_spec = _extract_subgoal_section(phase_md, name)

    parts = [
        f"## Target\nPhase: {phase}\nSub-Goal: {name}",
        f"## Conventions\n{_read_context('conventions.md')}",
        f"## Tech Stack\n{_read_context('tech_stack.md')}",
        f"## Sub-Goal Specification\n{sub_goal_spec}",
    ]

    verification = state.get("verification")
    if verification and not verification.get("passed"):
        parts.append(
            "## Previous Verification Failure\n"
            + _verification_summary(verification)
        )

    user_hint = state.get("user_hint", "")
    if user_hint:
        parts.append(f"## Additional Instructions from Operator\n{user_hint}")

    return "\n\n---\n\n".join(parts)


# ── tools ─────────────────────────────────────────────────────────────────────

async def _load_tools() -> tuple[list, list[dict]]:
    """kagent developer_tools 로드. 실패 시 빈 리스트로 graceful degradation."""
    try:
        tool_objs = await get_kagent_tools("developer_tools")
        return tool_objs, tools_as_chat_dicts(tool_objs)
    except Exception:
        return [], []


# ── tool loop ─────────────────────────────────────────────────────────────────

async def _execute_tools_parallel(
    tool_calls: list[dict], tool_map: dict
) -> list[tuple[dict, str]]:
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
    tool_map = {t.name: t for t in tool_objs}

    for _ in range(_MAX_TOOL_TURNS):
        resp = llm.chat(messages, tools=tools or None)

        if not resp.get("tool_calls"):
            messages.append({"role": "assistant", "content": resp.get("content", "")})
            return messages

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": resp.get("content", ""),
            "tool_calls": resp["tool_calls"],
        }
        if "_gemini_raw_content" in resp:
            assistant_msg["_gemini_raw_content"] = resp["_gemini_raw_content"]
        messages.append(assistant_msg)

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


# ── 파싱 및 파일 쓰기 ─────────────────────────────────────────────────────────

def _parse_artifacts(content: str) -> dict | None:
    """
    3-strategy JSON 추출.
    {"files": [...], "notes": "..."} 형식이면 반환, 아니면 None.
    """
    text = content.strip()
    candidates: list[str] = [text]

    for m in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL):
        candidates.append(m.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and "files" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _write_files(files: list[dict]) -> tuple[list[str], str | None]:
    """
    원자성 강화 파일 쓰기.

    Phase 1 — Pre-validation:
        - prefix 위반(_ALLOWED_PREFIX 미준수) → 조용히 skip
        - 빈 content → 조용히 skip
        - 유효 파일만 valid_files에 수집

    Phase 2 — Atomic write:
        - valid_files를 순서대로 기록
        - OSError(디스크 풀, 권한 문제 등) 발생 시 즉시 중단

    Returns:
        (written_paths, error_message | None)
        error_message가 None이면 정상 완료. 아니면 Broken State 신호.
    """
    # Phase 1: Pre-validation
    valid_files: list[tuple[str, str]] = []
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "")
        if not path.startswith(_ALLOWED_PREFIX):
            continue  # prefix 위반 — 조용히 skip
        if not content:
            continue  # 빈 content — 조용히 skip
        valid_files.append((path, content))

    # Phase 2: Write (검증 통과 파일만)
    written: list[str] = []
    for path, content in valid_files:
        try:
            p = PROJECT_ROOT / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            written.append(path)
        except OSError as e:
            return written, f"Write failed at '{path}': {e}"

    return written, None


# ── 노드 함수 ──────────────────────────────────────────────────────────────────

async def developer_node(state: HarnessState) -> dict:
    """
    LangGraph 노드. async 선언으로 상위 이벤트 루프에서 안전하게 await 가능.

    재시도 감지: verification이 존재하고 passed=False이면 error_count 증가.
    """
    sub_goal = state["current_sub_goal"]
    error_count = state.get("error_count", 0)

    # 재시도 시 error_count 증가
    verification = state.get("verification")
    if verification is not None and not verification.get("passed"):
        error_count += 1

    messages = [
        {"role": "system", "content": _load_system_prompt()},
        {"role": "user", "content": _build_user_message(state)},
    ]

    tool_objs, tools_dicts = await _load_tools()
    messages = await _run_tool_loop(messages, tools_dicts, tool_objs)

    final_content = messages[-1].get("content", "") if messages else ""
    artifacts = _parse_artifacts(final_content)

    if artifacts is None:
        written_files: list[str] = []
        notes = f"LLM response parse failed: {final_content[:300]}"
    else:
        written_files, write_error = _write_files(artifacts.get("files", []))
        notes = f"[WriteError] {write_error}" if write_error else artifacts.get("notes", "")

    return {
        "current_sub_goal": {**sub_goal, "stage": "dev"},
        "dev_artifacts": {"files": written_files, "notes": notes},
        "error_count": error_count,
    }
