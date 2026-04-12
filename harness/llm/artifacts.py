"""
LLM 컨텍스트 구성용 아티팩트 스캔 유틸리티.
developer 노드 / runtime_verifier Phase 2에서 공유 사용.
"""

from harness.config import ARTIFACT_PREFIX, PROJECT_ROOT

_DEFAULT_SUBDIRS = ("helm", "manifests", "docker", "ebpf")


def scan_service_files(
    service_name: str,
    subdirs: tuple[str, ...] = _DEFAULT_SUBDIRS,
) -> list[str]:
    """
    edge-server/{subdirs}/<service_name>/ 하위 파일 경로 목록 반환.
    경로는 PROJECT_ROOT 기준 상대 경로.
    """
    files: list[str] = []
    for sub in subdirs:
        base = PROJECT_ROOT / f"{ARTIFACT_PREFIX}{sub}/{service_name}"
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    files.append(str(p.relative_to(PROJECT_ROOT)))
    return files
