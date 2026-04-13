"""
JSON 추출 공용 유틸리티.

developer 노드와 runtime_verifier 노드가 공유하는 JSON 파싱 로직.
"""

import json
import re


def extract_json_dict(content: str) -> dict | None:
    """
    3-strategy JSON 추출. dict 반환 시 성공, 아니면 None.

    전략 1: 전체 텍스트 직접 파싱
    전략 2: ```json...``` / ```...``` 코드 블록 추출
    전략 3: 첫 { 부터 마지막 } 까지 추출 (앞뒤 서론/후론 무시)
    """
    text = content.strip()
    candidates = [text]

    for m in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL):
        candidates.append(m.group(1).strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            continue

    return None
