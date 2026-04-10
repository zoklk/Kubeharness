"""
llm/client.py 단위 테스트

- test_anthropic_real: 실제 Anthropic API 호출 (ANTHROPIC_API_KEY 필요)
- test_anthropic_mock: anthropic SDK mock으로 인터페이스 검증
- test_openai_compat_mock: openai SDK mock으로 인터페이스 검증
- test_missing_api_key: API key 미설정 시 에러
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch


# ── 실제 API 연결 테스트 ──────────────────────────────────────────────────────

def test_anthropic_real():
    """실제 Anthropic API 호출. ANTHROPIC_API_KEY 없으면 skip."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    from harness.llm.client import chat

    result = chat([{"role": "user", "content": "Reply with exactly: OK"}])

    assert isinstance(result["content"], str)
    assert len(result["content"]) > 0
    assert result["tool_calls"] is None
    assert result["raw"] is not None
    print(f"\n[real] content: {result['content']!r}")


# ── Mock 테스트 ───────────────────────────────────────────────────────────────

def _make_anthropic_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _make_anthropic_tool_response(tool_name: str, tool_input: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.id = "tool_abc"
    block.name = tool_name
    block.input = tool_input
    response = MagicMock()
    response.content = [block]
    return response


def _make_openai_response(text: str):
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_openai_tool_response(tool_name: str, tool_args: dict):
    tc = MagicMock()
    tc.id = "call_xyz"
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(tool_args)
    msg = MagicMock()
    msg.content = ""
    msg.tool_calls = [tc]
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response


@patch("anthropic.Anthropic")
def test_anthropic_mock_text(mock_cls):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.messages.create.return_value = _make_anthropic_response("hello world")

    from importlib import reload
    import harness.llm.client as m
    reload(m)

    result = m.chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "hello world"
    assert result["tool_calls"] is None
    mock_client.messages.create.assert_called_once()


@patch("anthropic.Anthropic")
def test_anthropic_mock_tool_call(mock_cls):
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.messages.create.return_value = _make_anthropic_tool_response(
        "GetResources", {"resource_type": "pods"}
    )

    from importlib import reload
    import harness.llm.client as m
    reload(m)

    result = m.chat(
        [{"role": "user", "content": "get pods"}],
        tools=[{"name": "GetResources", "description": "...", "input_schema": {}}],
    )

    assert result["content"] == ""
    assert result["tool_calls"] is not None
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "GetResources"
    assert result["tool_calls"][0]["input"] == {"resource_type": "pods"}


@patch("anthropic.Anthropic")
def test_anthropic_system_message(mock_cls):
    """system 메시지가 별도 파라미터로 전달되는지 확인."""
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.messages.create.return_value = _make_anthropic_response("ok")

    from importlib import reload
    import harness.llm.client as m
    reload(m)

    m.chat([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "hi"},
    ])

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "You are a helpful assistant."
    assert all(msg["role"] != "system" for msg in call_kwargs["messages"])


@patch("openai.OpenAI")
def test_openai_compat_mock_text(mock_cls, tmp_path, monkeypatch):
    """openai_compat provider mock 테스트."""
    cfg_content = """
provider: openai_compat
endpoint: http://10.40.40.40:8000/v1
api_key_env: DUMMY_KEY
model: openai/gpt-oss-120b
temperature: 0.1
"""
    cfg_file = tmp_path / "llm.yaml"
    cfg_file.write_text(cfg_content)
    os.environ["DUMMY_KEY"] = "dummy"

    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _make_openai_response("hello compat")

    import harness.llm.client as m
    monkeypatch.setattr(m, "_CONFIG_PATH", cfg_file)

    result = m.chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "hello compat"
    assert result["tool_calls"] is None


@patch("openai.OpenAI")
def test_openai_compat_mock_tool_call(mock_cls, tmp_path, monkeypatch):
    cfg_content = """
provider: openai_compat
endpoint: http://10.40.40.40:8000/v1
api_key_env: DUMMY_KEY
model: openai/gpt-oss-120b
temperature: 0.1
"""
    cfg_file = tmp_path / "llm.yaml"
    cfg_file.write_text(cfg_content)
    os.environ["DUMMY_KEY"] = "dummy"

    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.chat.completions.create.return_value = _make_openai_tool_response(
        "GetResources", {"resource_type": "deployments"}
    )

    import harness.llm.client as m
    monkeypatch.setattr(m, "_CONFIG_PATH", cfg_file)

    result = m.chat(
        [{"role": "user", "content": "get deployments"}],
        tools=[{"name": "GetResources", "description": "...", "parameters": {}}],
    )

    assert result["tool_calls"] is not None
    assert result["tool_calls"][0]["name"] == "GetResources"
    assert result["tool_calls"][0]["input"] == {"resource_type": "deployments"}


@patch("anthropic.Anthropic")
def test_missing_api_key(mock_cls):
    """API key 미설정 시 EnvironmentError."""
    os.environ.pop("ANTHROPIC_API_KEY", None)

    from importlib import reload
    import harness.llm.client as m
    reload(m)

    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
        m.chat([{"role": "user", "content": "hi"}])
