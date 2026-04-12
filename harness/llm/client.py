import os
import time
import yaml
from typing import Any

from harness.config import PROJECT_ROOT

_CONFIG_PATH = PROJECT_ROOT / "config" / "llm.yaml"


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
    # 지원 role: system / user / assistant / tool
    #   assistant: tool_calls 포함 가능 (multi-turn)
    #   tool: {"role":"tool","tool_call_id":"...","name":"...","content":"..."}
    system = None
    user_messages = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        elif m["role"] == "assistant":
            parts: list[Any] = []
            if m.get("content"):
                parts.append({"type": "text", "text": m["content"]})
            for tc in (m.get("tool_calls") or []):
                parts.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                })
            user_messages.append({"role": "assistant", "content": parts or m.get("content", "")})
        elif m["role"] == "tool":
            user_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }],
            })
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
        kwargs["tools"] = [
            {"name": t["name"], "description": t.get("description", ""), "input_schema": t["input_schema"]}
            for t in tools
        ]

    retryable = (anthropic.APIConnectionError, anthropic.APITimeoutError)
    raw = _retry(lambda: client.messages.create(**kwargs), retryable)

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
        kwargs["tools"] = [
            {"type": "function", "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["parameters"],
            }}
            for t in tools
        ]
    if response_format:
        kwargs["response_format"] = response_format

    from openai import APIConnectionError, APITimeoutError
    retryable = (APIConnectionError, APITimeoutError)
    raw = _retry(lambda: client.chat.completions.create(**kwargs), retryable)

    msg = raw.choices[0].message
    content = msg.content or ""
    tool_calls = None
    if msg.tool_calls:
        import json
        tool_calls = []
        for tc in msg.tool_calls:
            # 안전한 JSON 파싱 (빈 문자열이면 {} 반환)
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {} # 모델이 잘못된 JSON을 뱉었을 경우

            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "input": args,
            })

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
    # 지원 role: system / user / assistant / tool
    #   assistant: tool_calls 포함 가능 (multi-turn)
    #   tool: {"role":"tool","tool_call_id":"...","name":"...","content":"..."}
    system = None
    contents = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        elif m["role"] == "assistant":
            # thinking 모델은 thought_signature가 있는 raw content를 그대로 사용해야 함
            if "_gemini_raw_content" in m:
                contents.append(m["_gemini_raw_content"])
            else:
                parts = []
                if m.get("content"):
                    parts.append(types.Part(text=m["content"]))
                for tc in (m.get("tool_calls") or []):
                    parts.append(types.Part(
                        function_call=types.FunctionCall(
                            id=tc["id"],
                            name=tc["name"],
                            args=tc["input"],
                        )
                    ))
                contents.append(types.Content(role="model", parts=parts))
        elif m["role"] == "tool":
            contents.append(types.Content(
                role="user",
                parts=[types.Part(
                    function_response=types.FunctionResponse(
                        id=m["tool_call_id"],
                        name=m["name"],
                        response={"result": m["content"]},
                    )
                )],
            ))
        else:
            role = "user"
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

    from google.genai.errors import ServerError as GeminiServerError
    retryable = (GeminiServerError,)
    raw = _retry(lambda: client.models.generate_content(
        model=model,
        contents=contents,
        config=gen_config,
    ), retryable)

    content = ""
    tool_calls = None

    for part in raw.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            content += part.text
        elif hasattr(part, "function_call") and part.function_call:
            if tool_calls is None:
                tool_calls = []
            fc = part.function_call
            tool_calls.append({
                "id": fc.id if hasattr(fc, "id") else fc.name,
                "name": fc.name,
                "input": dict(fc.args),
            })

    return {
        "content": content,
        "tool_calls": tool_calls,
        "raw": raw,
        "_gemini_raw_content": raw.candidates[0].content,
    }


def _retry(fn, retryable: tuple, max_attempts: int = 3):
    """네트워크 에러만 재시도. JSON 파싱 실패 등 로직 에러는 즉시 raise."""
    delay = 1.0
    for attempt in range(max_attempts):
        try:
            return fn()
        except retryable:
            if attempt == max_attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
