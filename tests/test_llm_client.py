"""
llm/client.py 연결 확인 테스트
config/llm.yaml 설정 그대로 실제 API 호출
"""

import os
import pytest


def test_llm_connection():
    """llm.yaml 설정 기반으로 실제 API 연결 확인."""
    from harness.llm.client import chat

    try:
        result = chat([{"role": "user", "content": "Reply with one word: OK"}])
    except Exception as e:
        pytest.fail(f"LLM connection failed: {e}")

    assert isinstance(result["content"], str)
    assert len(result["content"]) > 0
    print(f"\n[response] {result['content']!r}")
