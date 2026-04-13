"""
LLM 컨텍스트 구성용 아티팩트 스캔 및 쓰기 유틸리티.
developer 노드 / runtime_verifier Phase 2에서 공유 사용.
"""

from harness.config import ARTIFACT_PREFIX, PROJECT_ROOT

_DEFAULT_SUBDIRS = ("helm", "manifests", "docker", "ebpf")

# LLM이 쓸 수 있는 하위 디렉터리 허용 목록.
# edge-server/tests/ 는 smoke test 보호를 위해 의도적으로 제외.
_ALLOWED_WRITE_SUBDIRS = ("helm/", "manifests/", "docker/", "ebpf/")


def write_files(
    files: list[dict],
    allowed_prefix: str = ARTIFACT_PREFIX,
    console=None,
) -> tuple[list[str], str | None]:
    """
    prefix 검증 후 원자적 파일 쓰기. developer_node와 runtime_verifier_node 공용.

    Phase 1 — Pre-validation:
        - prefix 위반(allowed_prefix 미준수) → drop
        - 허용 서브디렉터리(helm/manifests/docker/ebpf) 외 경로 → drop (tests/ 등 차단)
        - 빈 content → drop
        - 유효 파일만 valid_files에 수집

    Phase 2 — Atomic write:
        - valid_files를 순서대로 기록
        - OSError 발생 시 즉시 중단

    Returns:
        (written_paths, error_message | None)
        error_message가 None이면 정상 완료.
    """
    from rich.console import Console as _Console
    _con = console or _Console()

    # Phase 1: Pre-validation
    valid_files: list[tuple[str, str]] = []
    for f in files:
        path = f.get("path", "")
        content = f.get("content", "")
        if not path.startswith(allowed_prefix):
            _con.print(f"  [red]⚠ prefix violation — dropped:[/red] {path!r}")
            continue
        rel = path[len(allowed_prefix):]
        if not any(rel.startswith(sub) for sub in _ALLOWED_WRITE_SUBDIRS):
            _con.print(f"  [red]⚠ write blocked (allowed: helm/manifests/docker/ebpf) — dropped:[/red] {path!r}")
            continue
        if not content:
            _con.print(f"  [yellow]⚠ empty content — dropped:[/yellow] {path!r}")
            continue
        valid_files.append((path, content))

    # Phase 2: Write (검증 통과 파일만)
    written: list[str] = []
    for path, content in valid_files:
        try:
            p = PROJECT_ROOT / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
            written.append(path)
        except OSError as e:
            return written, f"Write failed at '{path}': {e}"

    return written, None


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
