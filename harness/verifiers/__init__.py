"""
검증 공통 유틸리티.

static.py와 runtime_gates.py가 공유하는 헬퍼.
"""

from pathlib import Path
from typing import Optional

from harness.config import PROJECT_ROOT, ARTIFACT_PREFIX, cluster_config


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


def values_files(chart_path: str) -> list[str]:
    """
    active 환경의 helm values 파일 목록 반환 (존재하는 파일만).
    static_verifier와 runtime_gates에서 동일 로직 공유.
    """
    active = cluster_config().get("_active", "dev")
    return [
        vf for vf in [
            f"{chart_path}/values.yaml",
            f"{chart_path}/values-{active}.yaml",
        ]
        if Path(vf).exists()
    ]


def node_log_dir(state: dict, sub: str) -> str:
    """
    phase/sub_goal/attempt_N/{sub} 구조 로그 경로.
    static_verifier("static"), runtime_verifier("runtime") 공유.
    """
    phase = state.get("current_phase", "unknown")
    name = state["current_sub_goal"]["name"]
    attempt = state.get("error_count", 0)
    return str(PROJECT_ROOT / f"logs/raw/{phase}/{name}/attempt_{attempt}/{sub}")
