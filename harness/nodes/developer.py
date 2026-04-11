"""
Developer 노드.

1. context 로드: context/harness/developer_prompt.md (system prompt),
   context/inject/conventions.md, context/inject/tech_stack.md,
   context/phases/<phase>.md 의 current sub_goal 섹션
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

from rich.console import Console

from harness.config import NAMESPACE, all_envs, cluster_config
from harness.llm import client as llm
from harness.mcp.kagent_client import get_kagent_tools, tools_as_chat_dicts
from harness.state import HarnessState

_console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONTEXT_DIR = PROJECT_ROOT / "context"
_PROMPT_PATH = _CONTEXT_DIR / "harness" / "developer_prompt.md"
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
    """inject/ 우선, 없으면 context/ 루트에서 탐색 (phases/ 등 하위 경로 포함)."""
    p = _CONTEXT_DIR / "inject" / name
    if not p.exists():
        p = _CONTEXT_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else f"[{name} not found]"


def _extract_service_name(sub_goal_spec: str, fallback: str) -> str:
    """
    sub_goal 섹션에서 service_name 필드 추출.
    형식: - **service_name**: <값>  (백틱 있거나 없거나)
    못 찾으면 fallback(sub_goal["name"]) 반환.
    """
    m = re.search(
        r'\*\*service_name\*\*\s*:\s*`?([a-z0-9][a-z0-9-]+)`?',
        sub_goal_spec,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else fallback


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


def _build_user_message(state: HarnessState, sub_goal_spec: str, service_name: str) -> str:
    sub_goal = state["current_sub_goal"]
    phase = sub_goal["phase"]
    name = sub_goal["name"]

    existing_files_section = _build_existing_files_section(service_name)

    active_env = cluster_config().get("_active", "dev")
    envs = all_envs()

    env_rows = "\n".join(
        f"| `{env_name}` | `{cfg['domain_suffix']}` | `{cfg['arch']}` |"
        for env_name, cfg in envs.items()
    )
    env_detail = "\n".join(
        f"### `{env_name}` (`values-{env_name}.yaml`)\n"
        f"- domain_suffix: `{cfg['domain_suffix']}`\n"
        f"- arch: `linux/{cfg['arch']}`\n"
        f"- DNS example: `<service>-headless.{NAMESPACE}.svc.{cfg['domain_suffix']}`"
        for env_name, cfg in envs.items()
    )

    values_files_required = ", ".join(f"`values-{e}.yaml`" for e in envs)

    parts = [
        f"## Target\nPhase: {phase}\nSub-Goal: {name}",
        f"## Conventions\n{_read_context('conventions.md')}",
        f"## Tech Stack\n{_read_context('tech_stack.md')}",
        (
            f"## Cluster Environments\n"
            f"**Active for testing**: `{active_env}` "
            f"(Static/Runtime Verifier will use `values-{active_env}.yaml`)\n\n"
            f"**You MUST write {values_files_required} for EVERY service.**\n"
            f"Each file overrides environment-specific values (domain, arch, resources).\n\n"
            f"| env | domain_suffix | arch |\n"
            f"|-----|--------------|------|\n"
            f"{env_rows}\n\n"
            f"{env_detail}"
        ),
        f"## Sub-Goal Specification\n{sub_goal_spec}",
    ]

    if existing_files_section:
        parts.append(existing_files_section)

    deps = _extract_dependencies(sub_goal_spec)
    if deps:
        dep_list = "\n".join(f"- `{d}`" for d in deps)
        parts.append(
            "## Dependency Services\n"
            "The following services are prerequisites and already deployed in the cluster.\n"
            "Use kagent tools (`GetResources`, `GetRelease`, `GetResourceYAML`) to inspect\n"
            "their current state (labels, ports, Secret names, etc.) before writing files.\n\n"
            + dep_list
        )

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
    """kagent developer_tools 로드. 실패 시 경고 후 빈 리스트로 graceful degradation."""
    try:
        tool_objs = await get_kagent_tools("developer_tools")
        return tool_objs, tools_as_chat_dicts(tool_objs)
    except Exception as e:
        _console.print(f"  [yellow]⚠ kagent tools unavailable (developer): {e}[/yellow]")
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

        # ── tool call 터미널 출력 ──────────────────────────────────────────────
        for tc in resp["tool_calls"]:
            preview = json.dumps(tc.get("input", {}), ensure_ascii=False)
            if len(preview) > 120:
                preview = preview[:117] + "..."
            _console.print(f"  [dim cyan]⟳ tool[/dim cyan] [bold]{tc['name']}[/bold]  [dim]{preview}[/dim]")

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


# ── 컨텍스트 보강 ────────────────────────────────────────────────────────────

def _extract_dependencies(sub_goal_spec: str) -> list[str]:
    """
    sub_goal 섹션에서 dependency 서비스명 목록 추출.
    형식: - **dependency**: `emqx`, `step-ca`  또는  `없음`
    못 찾거나 없음이면 빈 리스트 반환.
    """
    m = re.search(r'\*\*dependency\*\*\s*:\s*(.+)', sub_goal_spec)
    if not m:
        return []
    value = m.group(1).strip()
    if "없음" in value:
        return []
    return re.findall(r'`([^`]+)`', value)


def _build_existing_files_section(service_name: str) -> str:
    """
    현재 서비스의 기존 파일 내용을 'Existing Files' 섹션으로 반환.
    파일이 하나도 없으면 빈 문자열 반환.
    """
    def _read_dir(base_rel: str) -> list[tuple[str, str]]:
        """base_rel(PROJECT_ROOT 상대) 하위 모든 파일을 (rel_path, content) 로 반환."""
        base = PROJECT_ROOT / base_rel
        if not base.is_dir():
            return []
        result = []
        for p in sorted(base.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(PROJECT_ROOT))
                try:
                    result.append((rel, p.read_text(encoding="utf-8")))
                except OSError:
                    result.append((rel, "(read error)"))
        return result

    def _format_files(files: list[tuple[str, str]]) -> str:
        parts = []
        for rel, content in files:
            parts.append(f"#### `{rel}`\n```\n{content}\n```")
        return "\n\n".join(parts)

    # 현재 서비스 디렉토리들 (dependency와 무관하게 항상 스캔)
    current_files: list[tuple[str, str]] = []
    for sub in ("helm", "manifests", "docker", "ebpf"):
        current_files.extend(_read_dir(f"edge-server/{sub}/{service_name}"))

    # 터미널 출력
    if current_files:
        _console.print(f"  [dim]Existing files injected into context ({len(current_files)}):[/dim]")
        for rel, _ in current_files:
            _console.print(f"    [green]✓[/green] {rel}")
    else:
        _console.print(f"  [dim]No existing files for '{service_name}' — fresh start[/dim]")

    if not current_files:
        return ""
    return (
        "## Existing Files\n\n"
        f"### Current Service (`{service_name}`)\n\n"
        + _format_files(current_files)
    )


def _collect_existing_files(service_name: str) -> list[str]:
    """
    LLM이 파일을 쓰지 않았을 때 폴백.
    edge-server/{helm,manifests,docker}/<service_name>/ 디렉토리를 스캔해
    PROJECT_ROOT 상대 경로 목록 반환.
    """
    candidates = [
        PROJECT_ROOT / f"edge-server/helm/{service_name}",
        PROJECT_ROOT / f"edge-server/manifests/{service_name}",
        PROJECT_ROOT / f"edge-server/docker/{service_name}",
        PROJECT_ROOT / f"edge-server/ebpf/{service_name}",
    ]
    files: list[str] = []
    for base in candidates:
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    files.append(str(p.relative_to(PROJECT_ROOT)))
    return files


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
            _console.print(f"  [red]⚠ prefix violation — dropped:[/red] {path!r}")
            continue
        if not content:
            _console.print(f"  [yellow]⚠ empty content — dropped:[/yellow] {path!r}")
            continue
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
    sub_goal_spec을 state에 캐시하여 하위 노드(runtime_verifier)에서 재사용 가능.
    user_hint는 사용 후 state에서 소거(빈 문자열)하여 다음 재시도에 누적되지 않도록 함.
    """
    sub_goal = state["current_sub_goal"]
    _console.print(f"\n[cyan]⟳[/cyan]  Developer  [{sub_goal['name']}]")
    error_count = state.get("error_count", 0)

    # 재시도 시 error_count 증가
    verification = state.get("verification")
    if verification is not None and not verification.get("passed"):
        error_count += 1

    # sub_goal_spec 추출 및 캐시 (runtime_verifier Phase 2에서 재사용)
    phase_md = _read_context(f"phases/{sub_goal['phase']}.md")
    sub_goal_spec = _extract_subgoal_section(phase_md, sub_goal["name"])
    service_name = _extract_service_name(sub_goal_spec, fallback=sub_goal["name"])

    messages = [
        {"role": "system", "content": _load_system_prompt()},
        {"role": "user", "content": _build_user_message(state, sub_goal_spec, service_name)},
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

    # 쓰기 완료 후 서비스 디렉토리 전체 스캔 (기존 + 새로 쓴 파일 모두 포함)
    # 디렉토리가 아직 없는 완전 신규 서비스면 written_files 폴백
    artifact_files = _collect_existing_files(service_name) or written_files

    return {
        "current_sub_goal": {**sub_goal, "stage": "dev", "service_name": service_name},
        "dev_artifacts": {"files": artifact_files, "notes": notes},
        "error_count": error_count,
        "sub_goal_spec": sub_goal_spec,
        "user_hint": "",  # 이번 시도에서 소비한 hint 소거 — 다음 재시도에 누적 방지
    }
