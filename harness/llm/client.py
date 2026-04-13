import os
import time
import yaml
from typing import Any

from rich.console import Console

from harness.config import PROJECT_ROOT

_CONFIG_PATH = PROJECT_ROOT / "config" / "llm.yaml"
_console = Console()


def _load_profile(profile: str = "default") -> dict:
    with open(_CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    # 하위 호환: profiles 키가 없으면 기존 플랫 구조를 그대로 반환
    if "profiles" not in raw:
        return raw
    return raw["profiles"][profile]


def get_node_profile(node_name: str) -> str:
    """node_profiles 섹션에서 노드별 프로파일 이름 반환. 없으면 'default'."""
    with open(_CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    return raw.get("node_profiles", {}).get(node_name, "default")


def get_profile_cfg(profile: str) -> dict:
    """profiles 섹션에서 프로파일 설정 반환."""
    with open(_CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    if "profiles" not in raw:
        return raw  # 하위 호환
    return raw["profiles"][profile]


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    profile: str = "default",
) -> dict:
    """
    Returns:
        {
            "content": str,
            "tool_calls": list[dict] | None,
            "raw": <provider response>
        }
    """
    cfg = _load_profile(profile)
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
    model = cfg.get("model", "claude-sonnet-4-6")
    temperature = cfg.get("temperature", 0.1)

    # system 메시지 분리 (Anthropic API는 system을 별도 파라미터로)
    # 지원 role: system / user / assistant / tool
    #   assistant: tool_calls 포함 가능 (multi-turn)
    #   tool: {"role":"tool","tool_call_id":"...","name":"...","content":"..."}
    system = None
    non_system = [m for m in messages if m["role"] != "system"]
    for m in messages:
        if m["role"] == "system":
            system = m["content"]

    # prompt_caching 활성 시 intermediate tool batch 위치 사전 수집.
    # system(1) + user_초기(1) = 이미 2개 소비 → tool batch용 남은 슬롯: 2개.
    # 마지막 2개 intermediate batch에만 cache_control 적용 (4개 한도 초과 방지).
    _TOOL_CACHE_SLOTS = 2
    if cfg.get("prompt_caching"):
        _intermediate_ends: list[int] = []  # 각 intermediate tool batch의 마지막 idx
        _scan = 0
        while _scan < len(non_system):
            if non_system[_scan]["role"] == "tool":
                _j = _scan
                while _j < len(non_system) and non_system[_j]["role"] == "tool":
                    _j += 1
                if _j < len(non_system):  # intermediate (뒤에 더 있음)
                    _intermediate_ends.append(_j - 1)
                _scan = _j
            else:
                _scan += 1
        _cacheable_ends = set(_intermediate_ends[-_TOOL_CACHE_SLOTS:])
    else:
        _cacheable_ends: set[int] = set()

    user_messages = []
    first_user_cached = False
    i = 0
    while i < len(non_system):
        m = non_system[i]
        if m["role"] == "assistant":
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
            i += 1
        elif m["role"] == "tool":
            # Anthropic: 같은 턴의 tool_result는 하나의 user 메시지로 묶어야 함.
            j = i
            while j < len(non_system) and non_system[j]["role"] == "tool":
                j += 1
            tool_results = []
            idx = i
            while idx < j:
                content = non_system[idx]["content"]
                if idx in _cacheable_ends:
                    content = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": non_system[idx]["tool_call_id"],
                    "content": content,
                })
                idx += 1
            i = j
            user_messages.append({"role": "user", "content": tool_results})
        else:
            # prompt_caching 활성 시 첫 번째 user 메시지(대형 컨텍스트)에 cache breakpoint 추가.
            # 이후 턴에서 system + 초기 user message 양쪽 모두 cache_hit 적용됨.
            if cfg.get("prompt_caching") and not first_user_cached and isinstance(m.get("content"), str):
                user_messages.append({
                    "role": "user",
                    "content": [{"type": "text", "text": m["content"], "cache_control": {"type": "ephemeral"}}],
                })
                first_user_cached = True
            else:
                user_messages.append(m)
            i += 1

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": cfg.get("max_tokens", 8096),
        "temperature": temperature,
        "messages": user_messages,
    }

    # 프롬프트 캐싱: system을 content block 리스트로 변환
    if system:
        if cfg.get("prompt_caching"):
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            kwargs["system"] = system

    if tools:
        # "type" 필드가 있는 항목은 Anthropic 내장 도구(web_search 등) → 그대로 전달
        # input_schema 없으면 parameters로 폴백 (OpenAI compat 형식 호환)
        kwargs["tools"] = [
            t if "type" in t else
            {"name": t["name"], "description": t.get("description", ""),
             "input_schema": t.get("input_schema", t.get("parameters", {}))}
            for t in tools
        ]

    retryable = (anthropic.APIConnectionError, anthropic.APITimeoutError)
    raw = _retry(lambda: client.messages.create(**kwargs), retryable, rate_limit_exc=anthropic.RateLimitError)

    # 토큰 사용량 로깅 (캐싱 활성 프로파일에서만 출력)
    if cfg.get("prompt_caching"):
        u = raw.usage
        parts = [f"in={u.input_tokens}", f"out={u.output_tokens}"]
        if getattr(u, "cache_creation_input_tokens", 0):
            parts.append(f"cache_write={u.cache_creation_input_tokens}")
        if getattr(u, "cache_read_input_tokens", 0):
            parts.append(f"cache_hit={u.cache_read_input_tokens}")
        _console.print(f"  [dim]Claude tokens: {' | '.join(parts)}[/dim]")

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
    # Anthropic 내장 도구 형식(type 필드 있음)은 FunctionDeclaration 변환 대상 제외:
    #   "web_search" → types.Tool(google_search=types.GoogleSearch()) 으로 매핑
    #   그 외 Anthropic 내장 도구는 Gemini에서 지원 안 되므로 무시
    gemini_tools = None
    if tools:
        declarations = []
        use_google_search = False
        for t in tools:
            if "type" in t:
                if t.get("name") == "web_search":
                    use_google_search = True
            else:
                schema = t.get("input_schema") or t.get("parameters") or {}
                declarations.append(types.FunctionDeclaration(
                    name=t["name"],
                    description=t.get("description", ""),
                    parameters=schema,
                ))
        tool_list = []
        if declarations:
            tool_list.append(types.Tool(function_declarations=declarations))
        if use_google_search:
            tool_list.append(types.Tool(google_search=types.GoogleSearch()))
        gemini_tools = tool_list or None

    # FunctionDeclaration + GoogleSearch 동시 사용 시 필수 옵션
    has_declarations = any(
        isinstance(t, types.Tool) and t.function_declarations
        for t in (gemini_tools or [])
    )
    has_google_search = any(
        isinstance(t, types.Tool) and t.google_search is not None
        for t in (gemini_tools or [])
    )
    tool_config = (
        types.ToolConfig(include_server_side_tool_invocations=True)
        if has_declarations and has_google_search
        else None
    )

    gen_config = types.GenerateContentConfig(
        temperature=temperature,
        system_instruction=system,
        tools=gemini_tools,
        tool_config=tool_config,
    )

    from google.genai.errors import ServerError as GeminiServerError
    import httpx
    retryable = (GeminiServerError, httpx.RemoteProtocolError)
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


def _retry(fn, retryable: tuple, max_attempts: int = 3, rate_limit_exc=None):
    """네트워크 에러 재시도. rate_limit_exc 지정 시 429를 별도 긴 대기로 재시도."""
    _RL_MAX = 5
    _RL_DELAY_BASE = 60.0  # 429는 1분 단위 토큰 한도이므로 60s 기본 대기

    delay = 1.0
    rl_count = 0
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if rate_limit_exc and isinstance(e, rate_limit_exc):
                rl_count += 1
                if rl_count > _RL_MAX:
                    raise
                wait = _RL_DELAY_BASE * rl_count
                _console.print(
                    f"  [yellow]⚠ 429 rate limit — {wait:.0f}s 후 재시도 ({rl_count}/{_RL_MAX})[/yellow]"
                )
                time.sleep(wait)
            elif isinstance(e, retryable):
                if attempt >= max_attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
                attempt += 1
            else:
                raise
