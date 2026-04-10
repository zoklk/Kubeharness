import os
import time
import yaml
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "llm.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
) -> dict:
    """
    Returns:
        {
            "content": str,
            "tool_calls": list[dict] | None,
            "raw": <provider response>
        }
    """
    cfg = _load_config()
    provider = cfg.get("provider", "anthropic")

    if provider == "anthropic":
        return _chat_anthropic(cfg, messages, tools, response_format)
    elif provider == "openai_compat":
        return _chat_openai_compat(cfg, messages, tools, response_format)
    else:
        raise ValueError(f"Unknown provider: {provider}")


def _chat_anthropic(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None,
    response_format: dict | None,
) -> dict:
    import anthropic

    api_key = os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
    if not api_key:
        raise EnvironmentError(f"Environment variable {cfg['api_key_env']} is not set")

    client = anthropic.Anthropic(api_key=api_key)
    model = cfg.get("model", "claude-sonnet-4-20250514")
    temperature = cfg.get("temperature", 0.1)

    # system 메시지 분리 (Anthropic API는 system을 별도 파라미터로)
    system = None
    user_messages = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            user_messages.append(m)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 8096,
        "temperature": temperature,
        "messages": user_messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    # JSON schema 강제: Anthropic은 response_format 미지원 → 프롬프트에 위임 (호출자 책임)
    # response_format은 openai_compat용으로만 활용

    raw = _retry(lambda: client.messages.create(**kwargs))

    content = ""
    tool_calls = None

    for block in raw.content:
        if block.type == "text":
            content = block.text
        elif block.type == "tool_use":
            if tool_calls is None:
                tool_calls = []
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })

    return {"content": content, "tool_calls": tool_calls, "raw": raw}


def _chat_openai_compat(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None,
    response_format: dict | None,
) -> dict:
    from openai import OpenAI

    api_key = os.environ.get(cfg.get("api_key_env", "OPENAI_API_KEY"), "dummy")
    endpoint = cfg.get("endpoint", "")
    if not endpoint:
        raise ValueError("endpoint must be set for openai_compat provider")

    client = OpenAI(api_key=api_key, base_url=endpoint)
    model = cfg.get("model", "gpt-4")
    temperature = cfg.get("temperature", 0.1)

    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
    if response_format:
        kwargs["response_format"] = response_format

    raw = _retry(lambda: client.chat.completions.create(**kwargs))

    msg = raw.choices[0].message
    content = msg.content or ""
    tool_calls = None
    if msg.tool_calls:
        import json
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.function.name,
                "input": json.loads(tc.function.arguments),
            }
            for tc in msg.tool_calls
        ]

    return {"content": content, "tool_calls": tool_calls, "raw": raw}


def _retry(fn, max_attempts: int = 3):
    """네트워크 에러만 재시도. JSON 파싱 실패 등 로직 에러는 즉시 raise."""
    import anthropic
    from openai import APIConnectionError, APITimeoutError

    retryable = (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        APIConnectionError,
        APITimeoutError,
    )
    delay = 1.0
    for attempt in range(max_attempts):
        try:
            return fn()
        except retryable as e:
            if attempt == max_attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
