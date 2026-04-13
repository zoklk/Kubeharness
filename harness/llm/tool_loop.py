"""
LLM tool loop 공통 유틸리티.

developer와 runtime_verifier가 공유하는 tool_call 실행 루프.
"""

import asyncio
import json
from typing import Any

from rich.console import Console

from harness.llm import client as llm
from harness.llm.client import get_profile_cfg
from harness.llm.json_utils import extract_json_dict

_console = Console()


async def _execute_tools_parallel(
    tool_calls: list[dict],
    tool_map: dict,
    tool_timeout: int = 60,
    tool_result_max_chars: int = 3000,
) -> list[tuple[dict, str]]:
    """LLM이 요청한 tool_calls를 asyncio.gather로 병렬 실행."""
    async def _exec_one(tc: dict) -> tuple[dict, str]:
        tool = tool_map.get(tc["name"])
        if tool is None:
            return tc, f"Unknown tool: {tc['name']}"
        try:
            result = str(await asyncio.wait_for(tool.ainvoke(tc["input"]), timeout=tool_timeout))
            if tc["name"] != "read_file" and len(result) > tool_result_max_chars:
                result = result[-tool_result_max_chars:]
            return tc, result
        except asyncio.TimeoutError:
            return tc, f"Tool timed out after {tool_timeout}s"
        except Exception as e:
            return tc, f"Tool error: {e}"

    return list(await asyncio.gather(*[_exec_one(tc) for tc in tool_calls]))


async def run_tool_loop(
    messages: list[dict],
    tools: list[dict],
    tool_objs: list,
    max_turns: int,
    profile: str = "default",
) -> list[dict]:
    """
    LLM이 tool_calls를 반환하는 동안 실행 루프.
    한 턴에 여러 tool_calls가 있으면 asyncio.gather로 병렬 실행.
    max_turns 회 후 tools 없이 최종 응답 요청.
    항상 마지막 메시지가 role=assistant인 상태로 반환.
    """
    cfg = get_profile_cfg(profile)
    tool_timeout = cfg.get("tool_timeout", 60)
    tool_result_max_chars = cfg.get("tool_result_max_chars", 3000)

    tool_map = {t.name: t for t in tool_objs}

    for _ in range(max_turns):
        resp = llm.chat(messages, tools=tools or None, profile=profile)

        if not resp.get("tool_calls"):
            messages.append({"role": "assistant", "content": resp.get("content", "")})
            return messages

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

        results = await _execute_tools_parallel(
            resp["tool_calls"], tool_map, tool_timeout, tool_result_max_chars
        )
        for tc, result_str in results:
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "content": result_str,
            })

    # max turns 초과 → tools 없이 최종 응답 요청
    resp = llm.chat(messages, profile=profile)
    messages.append({"role": "assistant", "content": resp.get("content", "")})
    return messages


def request_json_response(
    messages: list[dict],
    profile: str,
    schema_hint: str,
    max_retries: int = 2,
) -> tuple[dict | None, list[dict]]:
    """
    LLM 응답 JSON 파싱 실패 시 재요청. tool_loop 이후 호출.

    messages(대화 히스토리 전체)에 user 재요청 메시지를 append해
    llm.chat()을 최대 max_retries회 호출한다.
    성공 시 (parsed_dict, updated_messages), 실패 시 (None, updated_messages).
    """
    for attempt in range(1, max_retries + 1):
        _console.print(
            f"  [dim]⟳ JSON 재요청 {attempt}/{max_retries} ...[/dim]"
        )
        messages.append({
            "role": "user",
            "content": (
                "Your previous response was not valid JSON. "
                "Respond ONLY with a JSON object matching this schema — "
                "no preamble, no markdown, no explanation:\n"
                f"{schema_hint}"
            ),
        })
        resp = llm.chat(messages, profile=profile)
        content = resp.get("content", "")
        messages.append({"role": "assistant", "content": content})

        data = extract_json_dict(content)
        if data is not None:
            _console.print(f"  [dim green]✓ JSON 재요청 성공 (attempt {attempt})[/dim green]")
            return data, messages

        _console.print(f"  [dim yellow]✗ JSON 재요청 실패 (attempt {attempt})[/dim yellow]")

    return None, messages
