"""
kagent MCP 클라이언트 + LLM 통합 테스트

1. test_get_tools        : kagent에서 tool 목록 조회 (화이트리스트 필터 확인)
2. test_tools_chat_dicts : LangChain tool → chat() dict 변환 검증
3. test_llm_tool_loop    : LLM → tool 호출 → 결과 수신 → 최종 분석 full loop
"""

import asyncio
import subprocess
import time
import pytest
from harness.mcp.kagent_client import get_kagent_tools, tools_as_chat_dicts
from harness.llm.client import chat

LOCAL_MCP_URL = "http://localhost:18084/mcp"
LOCAL_PORT = 18084


@pytest.fixture(scope="module")
def kagent_portforward():
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", "kagent", "svc/kagent-tools",
         f"{LOCAL_PORT}:8084"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    yield
    proc.terminate()
    proc.wait()


@pytest.fixture(scope="module")
def dev_tools(kagent_portforward):
    tools = asyncio.run(get_kagent_tools("developer_tools", url_override=LOCAL_MCP_URL))
    return tools


# ── tool 목록 조회 ─────────────────────────────────────────────────────────────

def test_get_tools_developer(dev_tools):
    from harness.mcp.kagent_client import _load_config
    allowed = _load_config()["developer_tools"]
    names = [t.name for t in dev_tools]

    assert len(names) > 0
    for name in names:
        assert name in allowed, f"{name}이 화이트리스트에 없음"

    print(f"\n[developer_tools] {names}")


def test_tools_chat_dicts(dev_tools):
    dicts = tools_as_chat_dicts(dev_tools)

    assert len(dicts) == len(dev_tools)
    for d in dicts:
        assert {"name", "description", "input_schema", "parameters"} <= d.keys()
    print(f"\n[sample] {dicts[0]['name']}: {list(dicts[0]['input_schema'].get('properties', {}).keys())}")


# ── Full tool loop ────────────────────────────────────────────────────────────

def _run_tool_loop(messages: list[dict], tool_map: dict, tool_dicts: list[dict], max_turns: int = 5) -> str:
    """
    LLM → tool 실행 → 결과 피드백 → 최종 응답 루프.
    tool_map: {tool_name: LangChain tool}
    """
    current_messages = list(messages)

    for turn in range(max_turns):
        result = chat(current_messages, tools=tool_dicts)
        print(f"\n[turn {turn+1}] content={result['content']!r:.80} tool_calls={[tc['name'] for tc in (result['tool_calls'] or [])]}")

        if not result["tool_calls"]:
            return result["content"]

        # assistant turn 기록 (Gemini thinking 모델용 raw content 보존)
        assistant_msg = {
            "role": "assistant",
            "content": result["content"],
            "tool_calls": result["tool_calls"],
        }
        if "_gemini_raw_content" in result:
            assistant_msg["_gemini_raw_content"] = result["_gemini_raw_content"]
        current_messages.append(assistant_msg)

        # tool 실행 후 결과 추가 (kagent tool은 async only)
        for tc in result["tool_calls"]:
            tool = tool_map.get(tc["name"])
            if tool is None:
                tool_output = f"[ERROR] tool '{tc['name']}' not found"
            else:
                try:
                    tool_output = str(asyncio.run(tool.ainvoke(tc["input"])))
                except Exception as e:
                    tool_output = f"[ERROR] {e}"

            print(f"  → {tc['name']}({tc['input']}) → {tool_output[:120]}...")
            current_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "content": tool_output,
            })

    return "[max_turns reached]"


def test_llm_tool_loop(dev_tools):
    """
    LLM이 kagent tool을 호출하고, 실제 클러스터 결과를 받아
    분석 결과를 출력하는 전체 흐름 검증.
    """
    tool_map = {t.name: t for t in dev_tools}
    tool_dicts = tools_as_chat_dicts(dev_tools)

    messages = [{
        "role": "user",
        "content": (
            "kagent 네임스페이스에서 실행 중인 pod 목록을 조회하고, "
            "각 pod의 상태를 간략히 요약해줘."
        ),
    }]

    final = _run_tool_loop(messages, tool_map, tool_dicts)

    print(f"\n[final answer]\n{final}")
    assert isinstance(final, str) and len(final) > 0
    assert final != "[max_turns reached]", "LLM이 tool 루프를 종료하지 못함"
