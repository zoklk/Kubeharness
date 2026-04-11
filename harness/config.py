"""
클러스터 환경 설정 로더.
config/cluster.yaml의 active 환경 설정을 읽어 반환.
"""

from pathlib import Path
import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "cluster.yaml"

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
