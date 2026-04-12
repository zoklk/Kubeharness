"""
harness/nodes/runtime_verifier.py 단위 테스트

동작 원칙:
  Phase 1 pass → LLM 호출 없음, verification.passed=True, runtime_phase2 없음
  Phase 1 fail → Phase 2 LLM 진단 실행, verification.passed=False (항상)
"""

import asyncio
import json
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

def _state(service: str = SERVICE, error_count: int = 0) -> dict:
    return {
        "current_phase": "test",
        "current_sub_goal": {"name": service, "phase": "test", "stage": "static_verify"},
        "verification": {"passed": True, "stage": "static", "checks": [], "log_dir": "logs/raw/test/myapp/attempt_0/"},
        "history": [],
        "error_count": error_count,
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


def _phase2_json(passed: bool, observations=None, suggestions=None) -> str:
    return json.dumps({
        "passed": passed,
        "observations": observations or [],
        "suggestions": suggestions or [],
    })


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
    """Phase 2 진단 suggestions가 Developer에게 전달되도록 verification에 포함된다."""
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
    """Phase 1 fail 시 state 필드: runtime_phase2 포함."""
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


def test_parse_phase2_codeblock():
    content = "```json\n" + _phase2_json(False) + "\n```"
    r = _parse_phase2(content)
    assert r["passed"] is False


def test_parse_phase2_invalid():
    r = _parse_phase2("not json at all")
    assert r["passed"] is False
    assert any("parse failed" in s for s in r["suggestions"])


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
