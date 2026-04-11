"""
Developer LLM용 로컬 파일 읽기 툴.

edge-server/ 하위 파일을 read-only로 제공.
MCP 불필요 — Python 함수 직접 실행.
"""

from harness.config import ARTIFACT_PREFIX, PROJECT_ROOT


class ReadFileTool:
    name = "read_file"

    async def ainvoke(self, inputs: dict) -> str:
        path = inputs.get("path", "").strip()
        if not path.startswith(ARTIFACT_PREFIX):
            return f"Error: path must start with '{ARTIFACT_PREFIX}', got: {path!r}"
        full_path = PROJECT_ROOT / path
        if not full_path.exists():
            return f"Error: file not found: {path}"
        if not full_path.is_file():
            return f"Error: not a file: {path}"
        try:
            return full_path.read_text(encoding="utf-8")
        except OSError as e:
            return f"Error reading file: {e}"


def read_file_tool_dict() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file from the repository. "
                "Use this to inspect existing Helm chart files, values files, or templates "
                "before writing your output. Path must start with 'edge-server/'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path relative to project root. "
                            "Example: 'edge-server/helm/emqx/values.yaml'"
                        ),
                    }
                },
                "required": ["path"],
            },
        },
    }
