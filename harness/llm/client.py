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
    elif provider == "gemini":
        return _chat_gemini(cfg, messages, tools, response_format)
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


def _chat_gemini(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None,
    response_format: dict | None,
) -> dict:
    from google import genai
    from google.genai import types

    api_key = os.environ.get(cfg.get("api_key_env", "GEMINI_API_KEY"))
    if not api_key:
        raise EnvironmentError(f"Environment variable {cfg['api_key_env']} is not set")

    client = genai.Client(api_key=api_key)
    model = cfg.get("model", "gemini-2.0-flash")
    temperature = cfg.get("temperature", 0.1)

    # system 메시지 분리
    system = None
    contents = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            role = "user" if m["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))

    # tools 변환: {"name", "description", "input_schema" or "parameters"} → FunctionDeclaration
    gemini_tools = None
    if tools:
        declarations = []
        for t in tools:
            schema = t.get("input_schema") or t.get("parameters") or {}
            declarations.append(types.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=schema,
            ))
        gemini_tools = [types.Tool(function_declarations=declarations)]

    gen_config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system,
        tools=gemini_tools,
    )

    raw = _retry(lambda: client.models.generate_content(
        model=model,
        contents=contents,
        config=gen_config,
    ))

    content = ""
    tool_calls = None

    for part in raw.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            content = part.text
        elif hasattr(part, "function_call") and part.function_call:
            if tool_calls is None:
                tool_calls = []
            fc = part.function_call
            tool_calls.append({
                "id": fc.id if hasattr(fc, "id") else fc.name,
                "name": fc.name,
                "input": dict(fc.args),
            })

    return {"content": content, "tool_calls": tool_calls, "raw": raw}


def _retry(fn, max_attempts: int = 3):
    """네트워크 에러만 재시도. JSON 파싱 실패 등 로직 에러는 즉시 raise."""
    import anthropic
    from openai import APIConnectionError, APITimeoutError
    from google.genai.errors import ServerError as GeminiServerError

    retryable = (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        APIConnectionError,
        APITimeoutError,
        GeminiServerError,
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
