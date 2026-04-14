"""
클러스터 환경 설정 로더.
config/cluster.yaml의 active 환경 설정을 읽어 반환.
"""

import os
from pathlib import Path
import yaml

# HARNESS_ROOT: harness 코드가 있는 디렉터리 (harness/config.py → harness/ → GikView/)
HARNESS_ROOT = Path(__file__).resolve().parent.parent

# PROJECT_ROOT: 프로젝트 파일이 있는 디렉터리
# HARNESS_PROJECT_DIR 환경변수로 오버라이드 가능, 기본값은 sibling workspace/
_env = os.environ.get("HARNESS_PROJECT_DIR", "")
PROJECT_ROOT = Path(_env) if _env else HARNESS_ROOT.parent / "workspace"

_CONFIG_PATH = PROJECT_ROOT / "config" / "cluster.yaml"

# 컨벤션 상수 — 하네스 전체에서 공유
ARTIFACT_PREFIX = "edge-server/"  # Developer가 쓸 수 있는 경로 prefix


def release_name(service_name: str) -> str:
    """Helm release 이름 컨벤션: <service>-dev-v1"""
    return f"{service_name}-dev-v1"


def label_selector(service_name: str) -> str:
    """kubectl label selector 컨벤션: app.kubernetes.io/name=<service>"""
    return f"app.kubernetes.io/name={service_name}"

_DEFAULTS = {
    "domain_suffix": "cluster.local",
    "arch": "amd64",
    "kubeconfig": "",
}


def _parse() -> tuple[dict, str]:
    """YAML을 1회 파싱해 (raw_data, active_env) 반환."""
    if not _CONFIG_PATH.exists():
        return {}, "dev"
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}, "dev"
    if not isinstance(data, dict):
        return {}, "dev"
    return data, data.get("active", "dev")


# 모듈 로드 시 YAML 1회만 파싱
_raw, _active = _parse()

# cluster.yaml 최상위 namespace 필드. 없으면 "custom" 고정.
NAMESPACE: str = _raw.get("namespace", "custom")
_cluster: dict = {**_DEFAULTS, **_raw.get(_active, {}), "_active": _active}
_kubeconfig: str | None = (
    str(Path(p).expanduser())
    if (p := _cluster.get("kubeconfig", ""))
    else None
)


def cluster_config() -> dict:
    """현재 활성 클러스터 설정 반환."""
    return _cluster


def all_envs() -> dict[str, dict]:
    """dev, prod 등 모든 환경 설정을 {name: config} 형태로 반환."""
    return {
        key: {**_DEFAULTS, **val}
        for key, val in _raw.items()
        if key != "active" and isinstance(val, dict)
    }


def kubeconfig_path() -> str | None:
    """명시적 kubeconfig 경로. 비어있으면 None 반환."""
    return _kubeconfig


def build_cluster_env_section(include_authoring_hint: bool = True) -> str:
    """Cluster Environments 마크다운 섹션 반환.

    include_authoring_hint=True: developer용 (values-{env}.yaml 작성 요구사항 포함)
    include_authoring_hint=False: verifier용 (도메인/환경 정보만)
    """
    active_env = cluster_config().get("_active", "dev")
    envs = all_envs()
    env_rows = "\n".join(
        f"| `{n}` | `{c['domain_suffix']}` | `{c['arch']}` |"
        for n, c in envs.items()
    )
    env_detail = "\n".join(
        f"### `{n}` (`values-{n}.yaml`)\n"
        f"- domain_suffix: `{c['domain_suffix']}`\n"
        f"- arch: `linux/{c['arch']}`\n"
        f"- DNS example: `<service>-headless.{NAMESPACE}.svc.{c['domain_suffix']}`"
        for n, c in envs.items()
    )
    authoring = (
        f"\n**You MUST write {', '.join(f'`values-{e}.yaml`' for e in envs)} for EVERY service.**\n"
        "Each file overrides environment-specific values (domain, arch, resources).\n"
        if include_authoring_hint else ""
    )
    return (
        f"## Cluster Environments\n"
        f"**Active for testing**: `{active_env}` "
        f"(Static/Runtime Verifier will use `values-{active_env}.yaml`)\n"
        f"{authoring}\n"
        f"| env | domain_suffix | arch |\n"
        f"|-----|--------------|------|\n"
        f"{env_rows}\n\n"
        f"{env_detail}"
    )
