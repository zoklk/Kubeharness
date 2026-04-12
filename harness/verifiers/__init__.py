"""
검증 공통 유틸리티.

static.py와 runtime_gates.py가 공유하는 헬퍼.
"""

from pathlib import Path
from typing import Optional


def check_result(
    name: str,
    status: str,
    detail: str,
    log_dir: Optional[str] = None,
    raw: str = "",
) -> dict:
    """
    체크 결과 dict 생성 + 로그 파일 저장.

    Returns:
        {"name": str, "status": str, "detail": str, "log_path": str | None}
    """
    log_path = None
    if log_dir:
        p = Path(log_dir) / f"{name}.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw or detail, encoding="utf-8")
        log_path = str(p)
    return {"name": name, "status": status, "detail": detail, "log_path": log_path}
