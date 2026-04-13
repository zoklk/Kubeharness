"""
harness/nodes/runtime_verifier.py 단위 테스트

동작 원칙:
  Phase 1 pass → LLM 호출 없음, verification.passed=True, runtime_phase2 없음
  Phase 1 fail → Phase 2 LLM 진단 실행, verification.passed=False (항상)
               → phase2.files 있으면 파일 쓰기, runtime_retry_count 증가
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from harness.nodes.runtime_verifier import (
    runtime_verifier_node,
    _parse_phase2,
    _phase1_summary,
    PROJECT_ROOT,
)

SERVICE = "myapp"


# ── 공통 헬퍼 ─────────────────────────────────────────────────────────────────

def _state(service: str = SERVICE, error_count: int = 0, runtime_retry_count: int = 0,
           user_hint: str = "") -> dict:
    return {
        "current_phase": "test",
        "current_sub_goal": {"name": service, "phase": "test", "stage": "static_verify"},
        "verification": {"passed": True, "stage": "static", "checks": [], "log_dir": "logs/raw/test/myapp/attempt_0/"},
        "history": [],
        "error_count": error_count,
        "runtime_retry_count": runtime_retry_count,
        "user_hint": user_hint,
    }


def _phase1_pass() -> dict:
    return {
        "passed": True,
        "checks": [
            {"name": "helm_install", "status": "pass", "detail": "OK", "log_path": None},
            {"name": "kubectl_wait", "status": "pass", "detail": "OK", "log_path": None},
            {"name": "kubectl_events", "status": "pass", "detail": "no warnings", "log_path": None},
            {"name": "smoke_test", "status": "skip", "detail": "no smoke test", "log_path": None},
        ],
    }


def _phase1_fail(reason: str = "helm error") -> dict:
    return {
        "passed": False,
        "checks": [
            {"name": "helm_install", "status": "fail", "detail": reason, "log_path": None},
            {"name": "kubectl_wait", "status": "skip", "detail": "prior step failed", "log_path": None},
            {"name": "kubectl_events", "status": "skip", "detail": "prior step failed", "log_path": None},
            {"name": "smoke_test", "status": "skip", "detail": "prior step failed", "log_path": None},
        ],
    }


def _llm_resp(content: str, tool_calls=None):
    return {"content": content, "tool_calls": tool_calls, "raw": None}


def _phase2_json(passed: bool, observations=None, suggestions=None, files=None) -> str:
    data = {
        "passed": passed,
        "observations": observations or [],
        "suggestions": suggestions or [],
    }
    if files is not None:
        data["files"] = files
    return json.dumps(data)


# ── Phase 1 pass: LLM 호출 없이 즉시 통과 ────────────────────────────────────

@pytest.mark.asyncio
async def test_phase1_pass_no_llm():
    """Phase 1 완전 통과 시 LLM 호출 없음, passed=True, runtime_phase2 없음."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1",
              return_value=_phase1_pass()) as m_p1,
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])) as m_tools,
        patch("harness.llm.client.chat") as m_chat,
    ):
        result = await runtime_verifier_node(_state())

    m_p1.assert_called_once()
    m_tools.assert_not_called()
    m_chat.assert_not_called()

    assert result["verification"]["passed"] is True
    assert result["verification"]["stage"] == "runtime"
    assert "runtime_phase1" in result["verification"]
    assert "runtime_phase2" not in result["verification"]
    assert result["current_sub_goal"]["stage"] == "runtime_verify"


@pytest.mark.asyncio
async def test_phase1_pass_checks_preserved():
    """Phase 1 pass 결과가 verification에 보존된다."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_pass()),
        patch("harness.llm.client.chat"),
    ):
        result = await runtime_verifier_node(_state())

    p1 = result["verification"]["runtime_phase1"]
    assert p1["passed"] is True
    assert any(c["name"] == "helm_install" for c in p1["checks"])


# ── Phase 1 fail: Phase 2 LLM 진단 실행 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_phase1_fail_runs_phase2():
    """Phase 1 fail → Phase 2 LLM 진단 실행, runtime_phase2 결과 포함."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1",
              return_value=_phase1_fail("immutable field")) as m_p1,
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])) as m_tools,
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_phase2_json(False, suggestions=["fix the chart"]))) as m_chat,
    ):
        result = await runtime_verifier_node(_state())

    m_p1.assert_called_once_with(SERVICE, SERVICE, "test", log_dir=str(PROJECT_ROOT / "logs/raw/test/myapp/attempt_0/runtime"))
    m_tools.assert_called_once()
    m_chat.assert_called()

    assert result["verification"]["passed"] is False
    assert result["verification"]["stage"] == "runtime"
    assert "runtime_phase1" in result["verification"]
    assert "runtime_phase2" in result["verification"]
    assert result["current_sub_goal"]["stage"] == "runtime_verify"


@pytest.mark.asyncio
async def test_phase1_fail_detail_preserved():
    """Phase 1 fail detail이 runtime_phase1에 보존된다."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1",
               return_value=_phase1_fail("immutable field")),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_phase2_json(False))),
    ):
        result = await runtime_verifier_node(_state())

    p1 = result["verification"]["runtime_phase1"]
    helm_check = next(c for c in p1["checks"] if c["name"] == "helm_install")
    assert "immutable field" in helm_check["detail"]


@pytest.mark.asyncio
async def test_phase1_fail_phase2_always_false():
    """Phase 1 fail 시 Phase 2가 passed=true를 반환해도 verification.passed는 False."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1",
              return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_phase2_json(passed=True))),
    ):
        result = await runtime_verifier_node(_state())

    assert result["verification"]["passed"] is False


@pytest.mark.asyncio
async def test_phase1_fail_suggestions_forwarded():
    """Phase 2 진단 suggestions가 verification에 포함된다."""
    suggestions = ["Increase memory limit to 512Mi", "Check EMQX_NODE__NAME env var"]
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1",
              return_value=_phase1_fail("kubectl_wait timed out")),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_phase2_json(
                  False,
                  observations=[{"area": "pod", "finding": "OOMKilled"}],
                  suggestions=suggestions,
              ))),
    ):
        result = await runtime_verifier_node(_state())

    p2 = result["verification"]["runtime_phase2"]
    assert p2["suggestions"] == suggestions
    assert any(o["finding"] == "OOMKilled" for o in p2["observations"])


# ── runtime_retry_count 증가 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_runtime_retry_count_incremented_on_fail():
    """Phase 1 fail 시 runtime_retry_count가 1 증가한다."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_phase2_json(False))),
    ):
        result = await runtime_verifier_node(_state(runtime_retry_count=0))

    assert result["runtime_retry_count"] == 1


@pytest.mark.asyncio
async def test_runtime_retry_count_accumulates():
    """runtime_retry_count가 누적된다."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_phase2_json(False))),
    ):
        result = await runtime_verifier_node(_state(runtime_retry_count=2))

    assert result["runtime_retry_count"] == 3


@pytest.mark.asyncio
async def test_runtime_retry_count_not_in_pass_result():
    """Phase 1 pass 시 runtime_retry_count는 반환 state에 없다."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_pass()),
        patch("harness.llm.client.chat"),
    ):
        result = await runtime_verifier_node(_state())

    assert "runtime_retry_count" not in result


# ── user_hint 소비 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_hint_consumed_on_fail():
    """Phase 1 fail 시 user_hint가 빈 문자열로 소거된다."""
    captured: list[list[dict]] = []

    def _capture(messages, **kwargs):
        captured.append(messages)
        return _llm_resp(_phase2_json(False))

    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", side_effect=_capture),
    ):
        result = await runtime_verifier_node(_state(user_hint="increase memory to 1Gi"))

    assert result["user_hint"] == ""
    # user_hint가 user message에 포함됐는지 확인
    user_msg = captured[0][1]["content"]
    assert "increase memory to 1Gi" in user_msg


@pytest.mark.asyncio
async def test_user_hint_injected_into_message():
    """user_hint가 Phase 2 user message에 포함된다."""
    captured: list[list[dict]] = []

    def _capture(messages, **kwargs):
        captured.append(messages)
        return _llm_resp(_phase2_json(False))

    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", side_effect=_capture),
    ):
        await runtime_verifier_node(_state(user_hint="check the DNS config"))

    user_msg = captured[0][1]["content"]
    assert "Additional Instructions from Operator" in user_msg
    assert "check the DNS config" in user_msg


# ── Phase 2 파일 쓰기 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase2_files_written(tmp_path, monkeypatch):
    """Phase 2 응답에 files 포함 시 파일이 실제로 기록된다."""
    monkeypatch.setattr("harness.llm.artifacts.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("harness.nodes.runtime_verifier._shared_write_files.__module__", None, raising=False)

    files = [{"path": "edge-server/helm/myapp/values.yaml", "content": "replicas: 2\n"}]
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_phase2_json(False, files=files))),
        patch("harness.nodes.runtime_verifier._write_files", return_value=(["edge-server/helm/myapp/values.yaml"], None)) as m_write,
    ):
        result = await runtime_verifier_node(_state())

    m_write.assert_called_once_with(files)
    # files should NOT be in state (excluded from phase2_for_state)
    assert "files" not in result["verification"]["runtime_phase2"]


@pytest.mark.asyncio
async def test_phase2_no_files_no_write():
    """Phase 2 응답에 files 없으면 _write_files 호출 안 함."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_phase2_json(False))),
        patch("harness.nodes.runtime_verifier._write_files") as m_write,
    ):
        await runtime_verifier_node(_state())

    m_write.assert_not_called()


@pytest.mark.asyncio
async def test_phase2_files_not_stored_in_state():
    """Phase 2 files 내용은 state에 저장되지 않는다 (bloat 방지)."""
    files = [{"path": "edge-server/helm/myapp/values.yaml", "content": "x: 1"}]
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_phase2_json(False, files=files))),
        patch("harness.nodes.runtime_verifier._write_files", return_value=([], None)),
    ):
        result = await runtime_verifier_node(_state())

    p2 = result["verification"]["runtime_phase2"]
    assert "files" not in p2


# ── Phase 2 JSON 파싱 실패 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase2_parse_failure_treated_as_fail():
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat",
              return_value=_llm_resp("Sorry, I cannot provide analysis right now.")),
    ):
        result = await runtime_verifier_node(_state())

    assert result["verification"]["passed"] is False
    p2 = result["verification"]["runtime_phase2"]
    assert p2["passed"] is False
    assert any("parse failed" in s for s in p2["suggestions"])


@pytest.mark.asyncio
async def test_phase2_json_retry_succeeds():
    """Phase 2 parse 실패 → retry → 성공 시 retry 결과를 사용."""
    good_json = _phase2_json(False, suggestions=["fix the chart"])
    call_responses = [
        _llm_resp("Sorry, I cannot provide analysis."),  # run_tool_loop 최종 응답 (parse 실패)
        _llm_resp(good_json),                            # retry 직접 호출 (성공)
    ]
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", side_effect=call_responses),
    ):
        result = await runtime_verifier_node(_state())

    assert result["verification"]["passed"] is False
    p2 = result["verification"]["runtime_phase2"]
    assert not any("parse failed" in s for s in p2["suggestions"])
    assert "fix the chart" in p2["suggestions"]


@pytest.mark.asyncio
async def test_phase2_json_retry_also_fails():
    """retry도 parse 실패 시 원래 parse-failure 결과를 유지."""
    call_responses = [
        _llm_resp("Sorry, I cannot provide analysis."),
        _llm_resp("Still not JSON either."),
    ]
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", side_effect=call_responses),
    ):
        result = await runtime_verifier_node(_state())

    assert result["verification"]["passed"] is False
    p2 = result["verification"]["runtime_phase2"]
    assert any("parse failed" in s for s in p2["suggestions"])


@pytest.mark.asyncio
async def test_phase2_codeblock_json_parsed():
    """```json ... ``` 코드 블록으로 감싼 응답도 파싱 가능."""
    wrapped = "```json\n" + _phase2_json(False, suggestions=["fix it"]) + "\n```"
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(wrapped)),
    ):
        result = await runtime_verifier_node(_state())

    p2 = result["verification"]["runtime_phase2"]
    assert "fix it" in p2["suggestions"]


# ── kagent tools 로드 실패 (graceful degradation) ──────────────────────────────

@pytest.mark.asyncio
async def test_tools_load_failure_continues():
    """kagent 연결 실패해도 Phase 2는 tools=[] 로 계속 진행."""
    async def _raise(*args, **kwargs):
        raise ConnectionError("kagent unreachable")

    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier.get_kagent_tools", new=_raise),
        patch("harness.llm.client.chat",
              return_value=_llm_resp(_phase2_json(False))),
    ):
        result = await runtime_verifier_node(_state())

    # tools 없이도 Phase 2가 완료되어야 함
    assert "runtime_phase2" in result["verification"]


@pytest.mark.asyncio
async def test_tools_load_inner_exception_returns_empty():
    """_load_tools 내부에서 예외 발생 시 ([], []) 반환."""
    from harness.nodes.runtime_verifier import _load_tools

    async def _raise(*args, **kwargs):
        raise Exception("no cluster")

    with patch("harness.nodes.runtime_verifier.get_kagent_tools", new=_raise):
        objs, dicts = await _load_tools()
    assert objs == []
    assert dicts == []


# ── tool calling 루프 ─────────────────────────────────────────────────────────

class MockTool:
    """asyncio.run(tool.ainvoke(...)) 을 지원하는 mock tool."""
    def __init__(self, name: str, result: str = "pod list: myapp-xxx"):
        self.name = name
        self._result = result

    async def ainvoke(self, args):
        return self._result


@pytest.mark.asyncio
async def test_tool_calling_loop():
    """LLM이 tool call 후 최종 JSON 응답하는 루프 테스트."""
    mock_tool = MockTool("k8s_get_resources")
    tool_dict = {"name": "k8s_get_resources", "description": "", "input_schema": {}, "parameters": {}}

    llm_responses = [
        _llm_resp("", tool_calls=[{"id": "tc1", "name": "k8s_get_resources", "input": {"resource_type": "pod"}}]),
        _llm_resp(_phase2_json(False, observations=[{"area": "pod", "finding": "CrashLoopBackOff"}])),
    ]

    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([mock_tool], [tool_dict])),
        patch("harness.llm.client.chat", side_effect=llm_responses),
    ):
        result = await runtime_verifier_node(_state())

    assert result["verification"]["passed"] is False
    p2 = result["verification"]["runtime_phase2"]
    assert any(o["finding"] == "CrashLoopBackOff" for o in p2["observations"])


@pytest.mark.asyncio
async def test_parallel_tool_calls():
    """LLM이 한 턴에 여러 tool_calls 요청 시 병렬 실행 후 모든 결과가 메시지에 포함."""
    tool_a = MockTool("k8s_get_resources", result="pods: running")
    tool_b = MockTool("k8s_get_events", result="no events")
    tool_dicts = [
        {"name": "k8s_get_resources", "description": "", "input_schema": {}, "parameters": {}},
        {"name": "k8s_get_events", "description": "", "input_schema": {}, "parameters": {}},
    ]

    llm_responses = [
        # 한 턴에 두 tool 동시 요청
        _llm_resp("", tool_calls=[
            {"id": "tc1", "name": "k8s_get_resources", "input": {}},
            {"id": "tc2", "name": "k8s_get_events", "input": {}},
        ]),
        _llm_resp(_phase2_json(False)),
    ]

    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([tool_a, tool_b], tool_dicts)),
        patch("harness.llm.client.chat", side_effect=llm_responses) as m_chat,
    ):
        result = await runtime_verifier_node(_state())

    # 두 번째 chat 호출 메시지에 두 tool result가 모두 포함되어야 함
    second_msgs = m_chat.call_args_list[1][0][0]
    tool_results = [m for m in second_msgs if m["role"] == "tool"]
    assert len(tool_results) == 2
    tool_ids = {m["tool_call_id"] for m in tool_results}
    assert "tc1" in tool_ids and "tc2" in tool_ids
    assert result["verification"]["passed"] is False


@pytest.mark.asyncio
async def test_tool_unknown_returns_error_message():
    """존재하지 않는 tool 호출 시 에러 메시지를 tool result로 반환."""
    tool_dict = {"name": "k8s_get_resources", "description": "", "input_schema": {}, "parameters": {}}

    llm_responses = [
        _llm_resp("", tool_calls=[{"id": "tc1", "name": "nonexistent_tool", "input": {}}]),
        _llm_resp(_phase2_json(False)),
    ]

    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [tool_dict])),
        patch("harness.llm.client.chat", side_effect=llm_responses) as m_chat,
    ):
        await runtime_verifier_node(_state())

    # 두 번째 chat 호출 메시지에 tool result가 포함되어야 함
    second_call_msgs = m_chat.call_args_list[1][0][0]
    tool_result = next(m for m in second_call_msgs if m["role"] == "tool")
    assert "Unknown tool" in tool_result["content"]


# ── state 필드 및 log_dir ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_fields_phase1_pass():
    """Phase 1 pass 시 state 필드: runtime_phase2 없음."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_pass()),
        patch("harness.llm.client.chat"),
    ):
        result = await runtime_verifier_node(_state())

    assert "runtime_verification" in result
    assert "runtime_phase1" in result["runtime_verification"]
    assert "runtime_phase2" not in result["runtime_verification"]
    assert result["current_sub_goal"]["stage"] == "runtime_verify"


@pytest.mark.asyncio
async def test_state_fields_phase1_fail():
    """Phase 1 fail 시 state 필드: runtime_phase2 포함, runtime_retry_count 증가."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_phase2_json(False))),
    ):
        result = await runtime_verifier_node(_state())

    assert "runtime_verification" in result
    assert "runtime_phase1" in result["runtime_verification"]
    assert "runtime_phase2" in result["runtime_verification"]
    assert result["current_sub_goal"]["stage"] == "runtime_verify"
    assert result["runtime_retry_count"] == 1
    assert result["user_hint"] == ""


@pytest.mark.asyncio
async def test_log_dir_format_pass():
    """Phase 1 pass 시 log_dir 올바른 포맷."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_pass()),
        patch("harness.llm.client.chat"),
    ):
        result = await runtime_verifier_node(_state(error_count=2))

    assert result["verification"]["log_dir"] == str(PROJECT_ROOT / "logs/raw/test/myapp/attempt_2") + "/"


@pytest.mark.asyncio
async def test_log_dir_format_fail():
    """Phase 1 fail 시 log_dir 올바른 포맷."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.llm.client.chat", return_value=_llm_resp(_phase2_json(False))),
    ):
        result = await runtime_verifier_node(_state(error_count=1))

    assert result["verification"]["log_dir"] == str(PROJECT_ROOT / "logs/raw/test/myapp/attempt_1") + "/"


# ── _parse_phase2 직접 테스트 ─────────────────────────────────────────────────

def test_parse_phase2_valid():
    content = _phase2_json(True, [{"area": "pod", "finding": "ok"}], ["good"])
    r = _parse_phase2(content)
    assert r["passed"] is True
    assert r["observations"][0]["area"] == "pod"
    assert r["suggestions"] == ["good"]
    assert r["files"] == []


def test_parse_phase2_with_files():
    """files 필드가 파싱 결과에 포함된다."""
    files = [{"path": "edge-server/helm/emqx/values.yaml", "content": "x: 1"}]
    content = _phase2_json(False, suggestions=["fix it"], files=files)
    r = _parse_phase2(content)
    assert r["files"] == files


def test_parse_phase2_no_files_returns_empty_list():
    """files 필드 없는 응답은 빈 리스트를 반환한다."""
    content = _phase2_json(False, suggestions=["check something"])
    r = _parse_phase2(content)
    assert r["files"] == []


def test_parse_phase2_codeblock():
    content = "```json\n" + _phase2_json(False) + "\n```"
    r = _parse_phase2(content)
    assert r["passed"] is False


def test_parse_phase2_invalid():
    r = _parse_phase2("not json at all")
    assert r["passed"] is False
    assert any("parse failed" in s for s in r["suggestions"])
    assert r["files"] == []


def test_parse_phase2_preamble_and_suffix():
    """LLM이 JSON 앞뒤에 서론/후론을 붙여도 추출 가능."""
    content = "Here is my analysis:\n" + _phase2_json(True) + "\nHope this helps!"
    r = _parse_phase2(content)
    assert r["passed"] is True


def test_parse_phase2_thinking_then_json():
    """Thinking... 서론 이후 코드 블록으로 JSON 제공하는 패턴."""
    content = "Thinking about the pods...\n```json\n" + _phase2_json(False) + "\n```"
    r = _parse_phase2(content)
    assert r["passed"] is False


# ── _phase1_summary 직접 테스트 ───────────────────────────────────────────────

def test_phase1_summary_pass_only_shows_name():
    """pass 항목은 이름만, fail 항목은 detail까지 포함."""
    summary = _phase1_summary(_phase1_pass())
    assert "[PASS] helm_install" in summary
    assert "[PASS] kubectl_wait" in summary
    assert "[SKIP] smoke_test" in summary


def test_phase1_summary_fail_shows_detail():
    """fail 항목은 detail이 summary에 포함되어야 함."""
    phase1 = _phase1_fail("immutable field error on ConfigMap")
    summary = _phase1_summary(phase1)
    assert "[FAIL] helm_install" in summary
    assert "immutable field error on ConfigMap" in summary
    assert "[SKIP] kubectl_wait" in summary


# ── Phase 2 knowledge 주입 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_phase2_knowledge_injected_in_message():
    """technology_name의 knowledge가 Phase 2 user message에 포함된다."""
    captured: list[list[dict]] = []

    def _capture_chat(messages, **kwargs):
        captured.append(messages)
        return _llm_resp(_phase2_json(False))

    state = _state()
    state["technology_name"] = "emqx"
    state["sub_goal_spec"] = "- **dependency**: 없음"

    knowledge = [
        ("## Technology Knowledge: emqx", "DNS suffix: alpha.nexus.local for dev"),
    ]
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.nodes.runtime_verifier.read_knowledge", return_value=knowledge),
        patch("harness.llm.client.chat", side_effect=_capture_chat),
    ):
        await runtime_verifier_node(state)

    user_msg = captured[0][1]["content"]
    assert "Technology Knowledge: emqx" in user_msg
    assert "DNS suffix: alpha.nexus.local for dev" in user_msg


@pytest.mark.asyncio
async def test_phase2_no_knowledge_message_structure_unchanged():
    """knowledge 없으면 기존 메시지 구조 그대로 — Phase 2 동작에 영향 없음."""
    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.nodes.runtime_verifier.read_knowledge", return_value=[]),
        patch("harness.llm.client.chat", return_value=_llm_resp(_phase2_json(False))),
    ):
        result = await runtime_verifier_node(_state())

    assert result["verification"]["passed"] is False
    assert "runtime_phase2" in result["verification"]


@pytest.mark.asyncio
async def test_phase2_knowledge_includes_deps():
    """sub_goal_spec의 dependency가 read_knowledge에 전달된다."""
    state = _state()
    state["technology_name"] = "myapp"
    state["sub_goal_spec"] = "- **dependency**: `step-ca`, `emqx`"

    with (
        patch("harness.nodes.runtime_verifier.run_runtime_phase1", return_value=_phase1_fail()),
        patch("harness.nodes.runtime_verifier._load_tools", return_value=([], [])),
        patch("harness.nodes.runtime_verifier.read_knowledge", return_value=[]) as m_rk,
        patch("harness.llm.client.chat", return_value=_llm_resp(_phase2_json(False))),
    ):
        await runtime_verifier_node(state)

    m_rk.assert_called_once_with("myapp", ["step-ca", "emqx"])
