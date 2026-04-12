"""
Developer 노드.

1. context 로드: context/prompts/developer_prompt.md (system prompt),
   context/base/conventions.md, context/base/tech_stack.md,
   context/phases/<phase>.md 의 current sub_goal 섹션
2. verification 실패 재시도 시: 실패 체크 상세 첨부, error_count 증가
3. kagent MCP developer_tools (read-only) 첨부
4. multi-turn tool loop (최대 _MAX_TOOL_TURNS 회)
5. 최종 JSON 파싱 → edge-server/ prefix 가드 후 파일 쓰기
6. dev_artifacts 업데이트 반환
"""

import json
import re
from pathlib import Path

from rich.console import Console

from harness.config import ARTIFACT_PREFIX, NAMESPACE, PROJECT_ROOT, build_cluster_env_section
from harness.llm.artifacts import scan_service_files
from harness.llm.context import extract_dependencies, read_knowledge
from harness.llm.tool_loop import run_tool_loop
from harness.mcp.kagent_client import get_kagent_tools, tools_as_chat_dicts
from harness.state import HarnessState
from harness.tools.local_tools import ReadFileTool, read_file_tool_dict

_console = Console()

_CONTEXT_DIR = PROJECT_ROOT / "context"
_PROMPT_PATH = _CONTEXT_DIR / "prompts" / "developer_prompt.md"
_ALLOWED_PREFIX = ARTIFACT_PREFIX
_MAX_TOOL_TURNS = 20

_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert Kubernetes and Helm developer. "
    "Write manifests or Helm charts according to the provided specifications. "
    "Use available tools to inspect cluster state as needed. "
    "Respond ONLY with valid JSON matching this schema exactly:\n"
    f'{{"files": [{{"path": "{_ALLOWED_PREFIX}...", "content": "..."}}], "notes": "..."}}\n'
    f"All file paths MUST start with {_ALLOWED_PREFIX}. "
    "Do not include any text outside the JSON object."
)


# ── 시스템 프롬프트 ────────────────────────────────────────────────────────────

def _load_system_prompt() -> str:
    if _PROMPT_PATH.exists():
        content = _PROMPT_PATH.read_text(encoding="utf-8").strip()
        content = content.replace("{NAMESPACE}", NAMESPACE)
        return content if content else _DEFAULT_SYSTEM_PROMPT
    return _DEFAULT_SYSTEM_PROMPT


# ── 컨텍스트 로드 ──────────────────────────────────────────────────────────────

def _read_context(name: str) -> str:
    """context/base/ 하위 파일 읽기 (conventions.md, tech_stack.md 전용)."""
    p = _CONTEXT_DIR / "base" / name
    if not p.exists():
        return f"[{name} not found]"
    return p.read_text(encoding="utf-8").replace("{NAMESPACE}", NAMESPACE)


def _read_phase(phase: str) -> str:
    """context/phases/<phase>.md 읽기."""
    p = _CONTEXT_DIR / "phases" / f"{phase}.md"
    if not p.exists():
        return f"[{phase}.md not found]"
    return p.read_text(encoding="utf-8").replace("{NAMESPACE}", NAMESPACE)


def _extract_technology_name(sub_goal_spec: str, fallback: str) -> str:
    """
    sub_goal 섹션에서 technology 필드 추출.
    형식: - **technology**: <값>  (백틱 있거나 없거나)
    못 찾으면 fallback(service_name) 반환.
    """
    m = re.search(
        r'\*\*technology\*\*\s*:\s*`?([a-z0-9][a-z0-9-]+)`?',
        sub_goal_spec,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else fallback


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


def _build_user_message(
    state: HarnessState, sub_goal_spec: str, service_name: str, technology_name: str
) -> str:
    sub_goal = state["current_sub_goal"]
    phase = sub_goal["phase"]
    name = sub_goal["name"]

    existing_files_section = _build_existing_files_section(service_name)
    smoke_tests_section = _build_smoke_tests_section(phase, name)

    parts = [
        f"## Target\nPhase: {phase}\nSub-Goal: {name}",
        f"## Conventions\n{_read_context('conventions.md')}",
        f"## Tech Stack\n{_read_context('tech_stack.md')}",
        build_cluster_env_section(include_authoring_hint=True),
        f"## Sub-Goal Specification\n{sub_goal_spec}",
    ]

    if smoke_tests_section:
        parts.append(smoke_tests_section)

    if existing_files_section:
        parts.append(existing_files_section)

    deps = extract_dependencies(sub_goal_spec)
    if deps:
        dep_list = "\n".join(f"- `{d}`" for d in deps)
        parts.append(
            "## Dependency Services\n"
            "The following services are prerequisites and already deployed in the cluster.\n"
            "Use kagent tools (`GetResources`, `GetRelease`, `GetResourceYAML`) to inspect\n"
            "their current state (labels, ports, Secret names, etc.) before writing files.\n\n"
            + dep_list
        )

    # Knowledge 주입 (Sub-Goal Spec + Smoke Tests + Existing Files + Deps 이후, Failure 이전)
    for title, content in read_knowledge(technology_name, deps):
        parts.append(f"{title}\n{content}")

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


# ── 컨텍스트 보강 ────────────────────────────────────────────────────────────



def _debug_print_context(message: str) -> None:
    """
    user message 미리보기 출력 (dim 스타일).
    섹션을 '---' 구분자로 split → 각 섹션의 헤더 + 앞 5줄 + 생략 표시.
    """
    sections = message.split("\n\n---\n\n")
    _console.print("[dim]── context preview ──────────────────────────[/dim]")
    for section in sections:
        lines = section.strip().splitlines()
        if not lines:
            continue
        header = lines[0]
        body = lines[1:]
        if not body:
            _console.print(f"  [dim]{header}[/dim]")
        elif len(body) <= 5:
            _console.print(f"  [dim]{header}[/dim]")
            for line in body:
                _console.print(f"  [dim]  {line}[/dim]")
        else:
            _console.print(f"  [dim]{header}[/dim]")
            for line in body[:5]:
                _console.print(f"  [dim]  {line}[/dim]")
            _console.print(f"  [dim]  ... (+{len(body) - 5} lines)[/dim]")
    _console.print("[dim]───────────────────────────────────────────────[/dim]")


def _build_smoke_tests_section(phase_name: str, sub_goal_name: str) -> str:
    """
    edge-server/tests/<phase_name>/smoke-test-<sub_goal_name>.sh 내용을 읽어
    'Smoke Tests' 섹션으로 반환. 파일 없으면 빈 문자열.
    """
    smoke_path = PROJECT_ROOT / f"{ARTIFACT_PREFIX}tests/{phase_name}/smoke-test-{sub_goal_name}.sh"
    if not smoke_path.exists():
        return ""
    content = smoke_path.read_text(encoding="utf-8")
    _console.print(f"  [dim]Smoke test found: {smoke_path.relative_to(PROJECT_ROOT)}[/dim]")
    return (
        "## Smoke Tests\n"
        "Runtime Verifier가 배포 후 실행할 smoke test입니다. "
        "이 테스트를 통과하도록 구현하세요.\n\n"
        f"### `smoke-test-{sub_goal_name}.sh`\n"
        f"```bash\n{content.rstrip()}\n```"
    )


def _build_existing_files_section(service_name: str) -> str:
    """
    기존 파일 경로 목록을 'Existing Files' 섹션으로 반환.
    파일 내용은 read_file 툴로 조회 — context 크기 절감.
    파일이 하나도 없으면 빈 문자열 반환.
    """
    all_files = scan_service_files(service_name)

    if not all_files:
        _console.print(f"  [dim]No existing files for '{service_name}' — fresh start[/dim]")
        return ""

    _console.print(f"  [dim]Existing files ({len(all_files)}) — use read_file tool to inspect[/dim]")

    file_list = "\n".join(f"- `{f}`" for f in all_files)
    return (
        "## Existing Files\n"
        f"The following files exist for `{service_name}`. "
        "Use the `read_file` tool to read their contents before writing.\n\n"
        + file_list
    )



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
            p.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
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
    phase_md = _read_phase(sub_goal["phase"])
    sub_goal_spec = _extract_subgoal_section(phase_md, sub_goal["name"])
    service_name = _extract_service_name(sub_goal_spec, fallback=sub_goal["name"])
    technology_name = _extract_technology_name(sub_goal_spec, fallback=service_name)

    user_message = _build_user_message(state, sub_goal_spec, service_name, technology_name)
    _debug_print_context(user_message)

    messages = [
        {"role": "system", "content": _load_system_prompt()},
        {"role": "user", "content": user_message},
    ]

    kagent_objs, kagent_dicts = await _load_tools()
    tool_objs = [*kagent_objs, ReadFileTool()]
    tools_dicts = [*kagent_dicts, read_file_tool_dict()]
    messages = await run_tool_loop(messages, tools_dicts, tool_objs, max_turns=_MAX_TOOL_TURNS)

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
    artifact_files = scan_service_files(service_name) or written_files

    return {
        "current_sub_goal": {**sub_goal, "stage": "dev", "service_name": service_name},
        "dev_artifacts": {"files": artifact_files, "notes": notes},
        "error_count": error_count,
        "sub_goal_spec": sub_goal_spec,
        "technology_name": technology_name,
        "user_hint": "",  # 이번 시도에서 소비한 hint 소거 — 다음 재시도에 누적 방지
    }
