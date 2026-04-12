"""
클러스터 환경 설정 로더.
config/cluster.yaml의 active 환경 설정을 읽어 반환.
"""

from pathlib import Path
import yaml

# 프로젝트 루트 — 하네스 전체에서 공유 (harness/config.py → harness/ → GikView/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

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

# cluster.yaml 최상위 namespace 필드. 없으면 "gikview" 고정.
NAMESPACE: str = _raw.get("namespace", "gikview")
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
