"""
kagent MCP 클라이언트

langchain-mcp-adapters를 사용해 kagent-tools MCP 서버에서
화이트리스트 tool을 가져온다.

주의: MultiServerMCPClient는 반드시 async with로 사용해야 함.
직접 인스턴스화 후 get_tools()를 호출하면 initialize 핸드셰이크가
누락되어 서버가 요청을 거부한다.
"""

from typing import Any
import yaml
from langchain_mcp_adapters.client import MultiServerMCPClient

from harness.config import PROJECT_ROOT

_CONFIG_PATH = PROJECT_ROOT / "config" / "kagent.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


async def get_kagent_tools(role: str, url_override: str | None = None) -> list:
    """
    kagent MCP 서버에서 role에 해당하는 화이트리스트 tool을 반환한다.

    Args:
        role: "developer_tools" | "runtime_verifier_tools"
        url_override: 테스트용 로컬 URL (e.g. http://localhost:18084/mcp)

    Returns:
        LangChain BaseTool 리스트 (화이트리스트 필터링 완료)
    """
    cfg = _load_config()
    url = url_override or cfg["url"]
    allowed_names: list[str] = cfg[role]

    client = MultiServerMCPClient({
        "kagent": {
            "url": url,
            "transport": "streamable_http",
        }
    })
    all_tools = await client.get_tools()
    return [t for t in all_tools if t.name in allowed_names]


def tools_as_chat_dicts(tools: list) -> list[dict]:
    """
    LangChain BaseTool 리스트를 harness/llm/client.py chat()이
    받는 dict 형식으로 변환한다.

    반환 dict는 anthropic(input_schema)과 gemini/openai(parameters)
    양쪽 키를 모두 포함하므로, _chat_* 함수가 알아서 사용한다.
    """
    result = []
    for tool in tools:
        raw = tool.args_schema
        if isinstance(raw, dict):
            schema = raw
        elif raw is not None:
            try:
                schema = raw.model_json_schema()
            except AttributeError:
                schema = raw.schema()
        else:
            schema = {"type": "object", "properties": {}}
        schema = {k: v for k, v in schema.items() if k not in ("title", "$defs")}

        result.append({
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": schema,   # anthropic
            "parameters": schema,     # gemini / openai_compat
        })
    return result
