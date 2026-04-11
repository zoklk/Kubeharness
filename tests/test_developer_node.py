"""
harness/nodes/developer.py 단위 테스트
LLM, kagent tools, 파일 시스템을 mock하여 노드 로직 검증.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from harness.nodes.developer import (
    developer_node,
    _parse_artifacts,
    _extract_subgoal_section,
    _write_files,
    _verification_summary,
    _build_user_message,
)

SERVICE = "prometheus"
PHASE = "monitoring"


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

def _state(service: str = SERVICE, phase: str = PHASE,
           error_count: int = 0, verification=None) -> dict:
    s = {
        "current_phase": phase,
        "current_sub_goal": {"name": service, "phase": phase, "stage": "dev"},
        "history": [],
        "error_count": error_count,
    }
    if verification is not None:
        s["verification"] = verification
    return s


def _artifacts_json(files=None, notes="") -> str:
    return json.dumps({
        "files": files or [
            {"path": f"edge-server/helm/{SERVICE}/Chart.yaml", "content": "apiVersion: v2"},
        ],
        "notes": notes,
    })


def _llm_resp(content: str, tool_calls=None):
    return {"content": content, "tool_calls": tool_calls, "raw": None}


def _fail_verification(stage: str = "static", checks=None) -> dict:
    return {
        "passed": False,
        "stage": stage,
        "checks": checks or [
            {"name": "yamllint", "status": "fail", "detail": "line 3: wrong indent"},
        ],
    }


# ── error_count 증가 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_error_count_not_incremented_on_initial_call():
    """verification 없는 첫 호출은 error_count 증가 안 함."""
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_artifacts_json())),
    ):
        result = await developer_node(_state(error_count=0))

    assert result["error_count"] == 0


@pytest.mark.asyncio
async def test_error_count_incremented_on_retry():
    """verification.passed=False이면 error_count 1 증가."""
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_artifacts_json())),
    ):
        result = await developer_node(_state(error_count=0, verification=_fail_verification()))

    assert result["error_count"] == 1


@pytest.mark.asyncio
async def test_error_count_accumulates():
    """누적 재시도 시 state error_count에서 +1."""
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_artifacts_json())),
    ):
        result = await developer_node(_state(error_count=2, verification=_fail_verification()))

    assert result["error_count"] == 3


@pytest.mark.asyncio
async def test_error_count_not_incremented_when_passed():
    """verification.passed=True이면 error_count 증가 안 함 (그래프에서 불가한 케이스지만 방어)."""
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_artifacts_json())),
    ):
        result = await developer_node(
            _state(error_count=1, verification={"passed": True, "stage": "runtime", "checks": []})
        )

    assert result["error_count"] == 1


# ── 파일 쓰기 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_files_written_to_disk(tmp_path, monkeypatch):
    """LLM이 반환한 edge-server/ 경로 파일이 실제로 기록된다."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)

    files = [
        {"path": "edge-server/helm/prometheus/Chart.yaml", "content": "apiVersion: v2\nname: prometheus"},
        {"path": "edge-server/helm/prometheus/values.yaml", "content": "replicas: 1"},
    ]
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(json.dumps({"files": files, "notes": "ok"}))),
    ):
        result = await developer_node(_state())

    assert len(result["dev_artifacts"]["files"]) == 2
    assert (tmp_path / "edge-server/helm/prometheus/Chart.yaml").exists()
    assert (tmp_path / "edge-server/helm/prometheus/values.yaml").exists()


@pytest.mark.asyncio
async def test_path_prefix_guard_rejects_outside_edge_server(tmp_path, monkeypatch):
    """edge-server/ 외부 경로는 쓰지 않고 dev_artifacts에도 포함 안 됨."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)

    files = [
        {"path": "edge-server/helm/app/Chart.yaml", "content": "ok"},
        {"path": "harness/state.py", "content": "malicious"},       # 위반
        {"path": "context/conventions.md", "content": "overwrite"},  # 위반
    ]
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(json.dumps({"files": files, "notes": ""}))),
    ):
        result = await developer_node(_state())

    written = result["dev_artifacts"]["files"]
    assert written == ["edge-server/helm/app/Chart.yaml"]
    assert not (tmp_path / "harness/state.py").exists()
    assert not (tmp_path / "context/conventions.md").exists()


@pytest.mark.asyncio
async def test_all_paths_outside_prefix_writes_nothing(tmp_path, monkeypatch):
    """모든 경로가 prefix 위반이면 files=[]."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)

    files = [{"path": "scripts/run.py", "content": "evil"}]
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(json.dumps({"files": files, "notes": ""}))),
    ):
        result = await developer_node(_state())

    assert result["dev_artifacts"]["files"] == []


# ── JSON 파싱 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_failure_returns_empty_files():
    """LLM이 JSON 아닌 응답 시 files=[], notes에 parse failed 기록."""
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp("Sorry, I cannot help with that.")),
    ):
        result = await developer_node(_state())

    assert result["dev_artifacts"]["files"] == []
    assert "parse failed" in result["dev_artifacts"]["notes"]


@pytest.mark.asyncio
async def test_codeblock_json_parsed(tmp_path, monkeypatch):
    """```json...``` 코드 블록 응답도 정상 파싱."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)

    files = [{"path": "edge-server/manifests/app/deploy.yaml", "content": "kind: Deployment"}]
    wrapped = "```json\n" + json.dumps({"files": files, "notes": "wrapped"}) + "\n```"
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(wrapped)),
    ):
        result = await developer_node(_state())

    assert len(result["dev_artifacts"]["files"]) == 1
    assert result["dev_artifacts"]["notes"] == "wrapped"


@pytest.mark.asyncio
async def test_json_with_preamble_parsed(tmp_path, monkeypatch):
    """JSON 앞뒤에 서론/후론이 있어도 추출 가능."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)

    files = [{"path": "edge-server/helm/app/Chart.yaml", "content": "apiVersion: v2"}]
    content = "Here are the files:\n" + json.dumps({"files": files, "notes": ""}) + "\nDone."
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(content)),
    ):
        result = await developer_node(_state())

    assert len(result["dev_artifacts"]["files"]) == 1


# ── 재시도 시 verification 컨텍스트 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_includes_verification_in_user_message():
    """재시도 시 user 메시지에 verification 실패 정보가 포함된다."""
    captured: list[list[dict]] = []

    def _capture_chat(messages, **kwargs):
        captured.append(messages)
        return _llm_resp(_artifacts_json())

    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", side_effect=_capture_chat),
    ):
        await developer_node(_state(verification=_fail_verification(
            checks=[{"name": "helm_lint", "status": "fail", "detail": "template error"}]
        )))

    user_content = captured[0][1]["content"]
    assert "Previous Verification Failure" in user_content
    assert "helm_lint" in user_content
    assert "template error" in user_content


@pytest.mark.asyncio
async def test_initial_call_no_verification_in_message():
    """첫 호출 시 user 메시지에 Previous Verification 섹션 없음."""
    captured: list[list[dict]] = []

    def _capture_chat(messages, **kwargs):
        captured.append(messages)
        return _llm_resp(_artifacts_json())

    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", side_effect=_capture_chat),
    ):
        await developer_node(_state())  # no verification

    user_content = captured[0][1]["content"]
    assert "Previous Verification Failure" not in user_content


# ── kagent tools 로드 실패 (graceful degradation) ──────────────────────────────

@pytest.mark.asyncio
async def test_tools_load_failure_continues():
    """kagent 연결 실패해도 노드는 tools=[] 로 계속 진행."""
    async def _raise(*args, **kwargs):
        raise ConnectionError("kagent unreachable")

    with (
        patch("harness.nodes.developer.get_kagent_tools", new=_raise),
        patch("harness.llm.client.chat", return_value=_llm_resp(_artifacts_json())),
    ):
        result = await developer_node(_state())

    assert "dev_artifacts" in result


@pytest.mark.asyncio
async def test_tools_load_inner_exception_returns_empty():
    """_load_tools 내부 예외 시 ([], []) 반환."""
    from harness.nodes.developer import _load_tools

    async def _raise(*args, **kwargs):
        raise Exception("no cluster")

    with patch("harness.nodes.developer.get_kagent_tools", new=_raise):
        objs, dicts = await _load_tools()

    assert objs == []
    assert dicts == []


# ── tool calling 루프 ─────────────────────────────────────────────────────────

class MockTool:
    def __init__(self, name: str, result: str = "pod list: ok"):
        self.name = name
        self._result = result

    async def ainvoke(self, args):
        return self._result


@pytest.mark.asyncio
async def test_tool_calling_loop(tmp_path, monkeypatch):
    """LLM이 tool call 후 최종 JSON 응답하는 루프 테스트."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)
    mock_tool = MockTool("k8s_get_resources")
    tool_dict = {"name": "k8s_get_resources", "description": "", "input_schema": {}, "parameters": {}}

    llm_responses = [
        _llm_resp("", tool_calls=[{"id": "tc1", "name": "k8s_get_resources", "input": {"resource_type": "pod"}}]),
        _llm_resp(_artifacts_json()),
    ]

    with (
        patch("harness.nodes.developer._load_tools", return_value=([mock_tool], [tool_dict])),
        patch("harness.llm.client.chat", side_effect=llm_responses),
    ):
        result = await developer_node(_state())

    assert len(result["dev_artifacts"]["files"]) == 1


@pytest.mark.asyncio
async def test_parallel_tool_calls(tmp_path, monkeypatch):
    """LLM이 한 턴에 여러 tool_calls 시 병렬 실행 후 모든 결과 포함."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)
    tool_a = MockTool("k8s_get_resources", result="pods: running")
    tool_b = MockTool("k8s_get_events", result="no events")
    tool_dicts = [
        {"name": "k8s_get_resources", "description": "", "input_schema": {}, "parameters": {}},
        {"name": "k8s_get_events", "description": "", "input_schema": {}, "parameters": {}},
    ]

    llm_responses = [
        _llm_resp("", tool_calls=[
            {"id": "tc1", "name": "k8s_get_resources", "input": {}},
            {"id": "tc2", "name": "k8s_get_events", "input": {}},
        ]),
        _llm_resp(_artifacts_json()),
    ]

    with (
        patch("harness.nodes.developer._load_tools", return_value=([tool_a, tool_b], tool_dicts)),
        patch("harness.llm.client.chat", side_effect=llm_responses) as m_chat,
    ):
        await developer_node(_state())

    second_msgs = m_chat.call_args_list[1][0][0]
    tool_results = [m for m in second_msgs if m["role"] == "tool"]
    assert len(tool_results) == 2
    assert {"tc1", "tc2"} == {m["tool_call_id"] for m in tool_results}


# ── state 필드 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_fields():
    """반환 state에 필수 필드 존재."""
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_artifacts_json(notes="done"))),
    ):
        result = await developer_node(_state())

    assert "dev_artifacts" in result
    assert "files" in result["dev_artifacts"]
    assert "notes" in result["dev_artifacts"]
    assert result["current_sub_goal"]["stage"] == "dev"
    assert result["current_sub_goal"]["name"] == SERVICE
    assert "error_count" in result


@pytest.mark.asyncio
async def test_notes_propagated():
    """LLM notes 필드가 dev_artifacts.notes에 전달된다."""
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_artifacts_json(notes="Used helm chart v2 pattern"))),
    ):
        result = await developer_node(_state())

    assert result["dev_artifacts"]["notes"] == "Used helm chart v2 pattern"


# ── _parse_artifacts 직접 테스트 ──────────────────────────────────────────────

def test_parse_artifacts_valid():
    files = [{"path": "edge-server/helm/app/Chart.yaml", "content": "ok"}]
    r = _parse_artifacts(json.dumps({"files": files, "notes": "test"}))
    assert r is not None
    assert r["files"][0]["path"] == "edge-server/helm/app/Chart.yaml"
    assert r["notes"] == "test"


def test_parse_artifacts_codeblock():
    data = {"files": [], "notes": ""}
    r = _parse_artifacts("```json\n" + json.dumps(data) + "\n```")
    assert r is not None
    assert r["files"] == []


def test_parse_artifacts_no_files_key():
    """files 키 없으면 None 반환."""
    r = _parse_artifacts(json.dumps({"result": "ok"}))
    assert r is None


def test_parse_artifacts_invalid_json():
    r = _parse_artifacts("not json at all")
    assert r is None


def test_parse_artifacts_preamble():
    data = {"files": [{"path": "edge-server/x.yaml", "content": ""}], "notes": ""}
    content = "Here you go:\n" + json.dumps(data) + "\nEnd."
    r = _parse_artifacts(content)
    assert r is not None


# ── _extract_subgoal_section 직접 테스트 ──────────────────────────────────────

def test_extract_subgoal_found():
    md = "# Phase\n\n## prometheus\n\nSpec content here.\n\n## grafana\n\nOther.\n"
    result = _extract_subgoal_section(md, "prometheus")
    assert "Spec content here" in result
    assert "grafana" not in result


def test_extract_subgoal_level3():
    md = "## services\n\n### prometheus\n\nContent.\n\n### grafana\n\nOther.\n"
    result = _extract_subgoal_section(md, "prometheus")
    assert "Content." in result
    assert "grafana" not in result


def test_extract_subgoal_not_found_returns_full():
    md = "## some-other\n\nContent.\n"
    result = _extract_subgoal_section(md, "nonexistent")
    assert result == md


def test_extract_subgoal_case_insensitive():
    md = "## Prometheus\n\nSpec.\n"
    result = _extract_subgoal_section(md, "prometheus")
    assert "Spec." in result


def test_extract_subgoal_last_section():
    """마지막 섹션이면 파일 끝까지 포함."""
    md = "## other\n\nOther.\n\n## prometheus\n\nFinal section content."
    result = _extract_subgoal_section(md, "prometheus")
    assert "Final section content." in result
    assert "Other." not in result


def test_extract_subgoal_numbered_heading():
    """'## 1. prometheus 설치' 처럼 번호/설명이 포함된 헤딩도 매칭."""
    md = "## 1. prometheus 설치\n\nSpec.\n\n## 2. grafana\n\nOther.\n"
    result = _extract_subgoal_section(md, "prometheus")
    assert "Spec." in result
    assert "grafana" not in result


def test_extract_subgoal_template_style():
    """_template.md 형식: '## Sub_goal: `prometheus`' 패턴."""
    md = (
        "## Phase 개요\n\nIntro.\n\n"
        "## Sub_goal: `prometheus`\n\n### 1. 목표 사양\n\nSpec.\n\n"
        "## Sub_goal: `grafana`\n\nOther.\n"
    )
    result = _extract_subgoal_section(md, "prometheus")
    assert "목표 사양" in result
    assert "Spec." in result
    assert "grafana" not in result


def test_extract_subgoal_includes_subsections():
    """시작 ## 섹션 내의 ### 하위 섹션을 포함."""
    md = (
        "## prometheus\n\n"
        "### Setup\n\nSetup content.\n\n"
        "### Config\n\nConfig content.\n\n"
        "## grafana\n\nOther.\n"
    )
    result = _extract_subgoal_section(md, "prometheus")
    assert "Setup content." in result
    assert "Config content." in result
    assert "grafana" not in result


def test_extract_subgoal_level1_heading():
    """# 레벨 헤딩도 매칭 가능 (확장 지원)."""
    md = "# prometheus\n\nContent.\n\n# grafana\n\nOther.\n"
    result = _extract_subgoal_section(md, "prometheus")
    assert "Content." in result
    assert "grafana" not in result


# ── _write_files 직접 테스트 (원자성) ─────────────────────────────────────────

def test_write_files_success(tmp_path, monkeypatch):
    """정상 쓰기: 성공 경로 리스트 반환, error=None."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)
    files = [
        {"path": "edge-server/helm/app/Chart.yaml", "content": "apiVersion: v2"},
        {"path": "edge-server/helm/app/values.yaml", "content": "replicas: 1"},
    ]
    written, error = _write_files(files)
    assert error is None
    assert written == [
        "edge-server/helm/app/Chart.yaml",
        "edge-server/helm/app/values.yaml",
    ]
    assert (tmp_path / "edge-server/helm/app/Chart.yaml").read_text() == "apiVersion: v2"


def test_write_files_empty_content_skipped(tmp_path, monkeypatch):
    """빈 content 파일은 pre-validation에서 skip."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)
    files = [
        {"path": "edge-server/helm/app/Chart.yaml", "content": "apiVersion: v2"},
        {"path": "edge-server/helm/app/empty.yaml", "content": ""},  # 빈 content
    ]
    written, error = _write_files(files)
    assert error is None
    assert written == ["edge-server/helm/app/Chart.yaml"]
    assert not (tmp_path / "edge-server/helm/app/empty.yaml").exists()


def test_write_files_prefix_violation_skipped(tmp_path, monkeypatch):
    """prefix 위반 경로는 pre-validation에서 skip."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)
    files = [
        {"path": "edge-server/helm/app/Chart.yaml", "content": "ok"},
        {"path": "harness/state.py", "content": "malicious"},
    ]
    written, error = _write_files(files)
    assert error is None
    assert written == ["edge-server/helm/app/Chart.yaml"]
    assert not (tmp_path / "harness/state.py").exists()


def test_write_files_oserror_stops_loop_and_returns_error(tmp_path, monkeypatch):
    """OSError 발생 시 루프 중단, 부분 성공 목록 + 에러 메시지 반환."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)
    files = [
        {"path": "edge-server/helm/app/Chart.yaml", "content": "ok"},
        {"path": "edge-server/helm/app/values.yaml", "content": "fail_here"},
        {"path": "edge-server/helm/app/deploy.yaml", "content": "never_written"},
    ]

    original_write_text = None

    def _patched_write_text(self, content, encoding="utf-8"):
        if "values.yaml" in str(self):
            raise OSError("disk full")
        self.parent.mkdir(parents=True, exist_ok=True)
        self.write_bytes(content.encode(encoding))

    monkeypatch.setattr("pathlib.Path.write_text", _patched_write_text)

    written, error = _write_files(files)
    assert error is not None
    assert "Write failed" in error
    assert "values.yaml" in error
    # Chart.yaml은 성공, values.yaml 이후는 중단
    assert "edge-server/helm/app/Chart.yaml" in written
    assert "edge-server/helm/app/values.yaml" not in written
    assert "edge-server/helm/app/deploy.yaml" not in written


def test_write_files_all_invalid_returns_empty(tmp_path, monkeypatch):
    """모든 파일이 prefix 위반 또는 빈 content이면 ([], None) 반환."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)
    files = [
        {"path": "scripts/run.py", "content": "evil"},
        {"path": "edge-server/ok.yaml", "content": ""},
    ]
    written, error = _write_files(files)
    assert written == []
    assert error is None


@pytest.mark.asyncio
async def test_node_reports_write_error_in_notes(tmp_path, monkeypatch):
    """_write_files가 에러 반환 시 notes에 [WriteError] 태그 포함."""
    monkeypatch.setattr("harness.nodes.developer.PROJECT_ROOT", tmp_path)

    def _failing_write(files):
        return ([], "Write failed at 'edge-server/x.yaml': disk full")

    files = [{"path": "edge-server/x.yaml", "content": "data"}]
    with (
        patch("harness.nodes.developer._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(json.dumps({"files": files, "notes": "original"}))),
        patch("harness.nodes.developer._write_files", side_effect=_failing_write),
    ):
        result = await developer_node(_state())

    assert "[WriteError]" in result["dev_artifacts"]["notes"]
    assert "disk full" in result["dev_artifacts"]["notes"]


# ── _verification_summary 직접 테스트 ─────────────────────────────────────────

def test_verification_summary_static_fail():
    v = {
        "passed": False,
        "stage": "static",
        "checks": [
            {"name": "yamllint", "status": "fail", "detail": "wrong indent"},
            {"name": "helm_lint", "status": "pass", "detail": "OK"},
        ],
    }
    s = _verification_summary(v)
    assert "Stage: static" in s
    assert "[FAIL] yamllint: wrong indent" in s
    assert "helm_lint" not in s  # pass는 생략


def test_verification_summary_runtime_phase1_fail():
    v = {
        "passed": False,
        "stage": "runtime",
        "checks": [],
        "runtime_phase1": {
            "passed": False,
            "checks": [
                {"name": "helm_install", "status": "fail", "detail": "immutable field"},
                {"name": "kubectl_wait", "status": "skip", "detail": "prior step failed"},
            ],
        },
    }
    s = _verification_summary(v)
    assert "[FAIL] runtime/helm_install: immutable field" in s
    assert "kubectl_wait" not in s  # skip은 생략


def test_verification_summary_runtime_phase2_suggestions():
    v = {
        "passed": False,
        "stage": "runtime",
        "checks": [],
        "runtime_phase2": {
            "passed": False,
            "observations": [{"area": "logs", "finding": "OOM detected"}],
            "suggestions": ["Increase memory limit"],
        },
    }
    s = _verification_summary(v)
    assert "[OBS] logs: OOM detected" in s
    assert "[SUGGESTION] Increase memory limit" in s


# ── _build_user_message 컨텍스트 포함 여부 ────────────────────────────────────

def test_build_user_message_contains_target(tmp_path, monkeypatch):
    """user message에 phase, sub_goal name 포함."""
    monkeypatch.setattr("harness.nodes.developer._CONTEXT_DIR", tmp_path)
    (tmp_path / "conventions.md").write_text("conv", encoding="utf-8")
    (tmp_path / "tech_stack.md").write_text("tech", encoding="utf-8")
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()
    (phases_dir / f"{PHASE}.md").write_text(f"## {SERVICE}\nSpec here.", encoding="utf-8")

    msg = _build_user_message(_state())
    assert f"Phase: {PHASE}" in msg
    assert f"Sub-Goal: {SERVICE}" in msg
    assert "Spec here." in msg
    assert "conv" in msg
    assert "tech" in msg


def test_build_user_message_missing_context_shows_placeholder(tmp_path, monkeypatch):
    """context 파일 없으면 placeholder 포함."""
    monkeypatch.setattr("harness.nodes.developer._CONTEXT_DIR", tmp_path)
    # phases 디렉토리도 없음

    msg = _build_user_message(_state())
    assert "[conventions.md not found]" in msg
    assert "[tech_stack.md not found]" in msg
