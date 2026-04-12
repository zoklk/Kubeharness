"""
LLM tool loop 공통 유틸리티.

developer와 runtime_verifier가 공유하는 tool_call 실행 루프.
"""

import asyncio
import json
from typing import Any

from rich.console import Console

from harness.llm import client as llm

_console = Console()


async def _execute_tools_parallel(
    tool_calls: list[dict],
    tool_map: dict,
    tool_timeout: int = 60,
) -> list[tuple[dict, str]]:
    """LLM이 요청한 tool_calls를 asyncio.gather로 병렬 실행."""
    async def _exec_one(tc: dict) -> tuple[dict, str]:
        tool = tool_map.get(tc["name"])
        if tool is None:
            return tc, f"Unknown tool: {tc['name']}"
        try:
            return tc, str(await asyncio.wait_for(tool.ainvoke(tc["input"]), timeout=tool_timeout))
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
    tool_timeout: int = 60,
) -> list[dict]:
    """
    LLM이 tool_calls를 반환하는 동안 실행 루프.
    한 턴에 여러 tool_calls가 있으면 asyncio.gather로 병렬 실행.
    max_turns 회 후 tools 없이 최종 응답 요청.
    항상 마지막 메시지가 role=assistant인 상태로 반환.
    """
    tool_map = {t.name: t for t in tool_objs}

    for _ in range(max_turns):
        resp = llm.chat(messages, tools=tools or None)

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

        results = await _execute_tools_parallel(resp["tool_calls"], tool_map, tool_timeout)
        for tc, result_str in results:
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "content": result_str,
            })

    # max turns 초과 → tools 없이 최종 응답 요청
    resp = llm.chat(messages)
    messages.append({"role": "assistant", "content": resp.get("content", "")})
    return messages
