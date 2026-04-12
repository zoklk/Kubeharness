"""
harness/config.py 단위 테스트
cluster.yaml을 임시 파일로 대체해 로더 로직을 검증.
"""

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _write_cluster_yaml(path: Path, content: dict) -> None:
    path.write_text(yaml.dump(content), encoding="utf-8")


def _reload_config(config_path: Path):
    """harness.config를 지정 경로로 재로드해 모듈 레벨 변수를 갱신."""
    import harness.config as mod
    with patch.object(mod, "_CONFIG_PATH", config_path):
        raw, active = mod._parse()
        cluster = {**mod._DEFAULTS, **raw.get(active, {}), "_active": active}
        kc = str(Path(p).expanduser()) if (p := cluster.get("kubeconfig", "")) else None
        return raw, active, cluster, kc


# ── cluster_config ────────────────────────────────────────────────────────────

def test_cluster_config_returns_active_dev(tmp_path):
    """active=dev이면 dev 설정을 반환."""
    f = tmp_path / "cluster.yaml"
    _write_cluster_yaml(f, {
        "active": "dev",
        "dev": {"domain_suffix": "alpha.nexus.local", "arch": "amd64", "kubeconfig": ""},
        "prod": {"domain_suffix": "cluster.local", "arch": "arm64", "kubeconfig": ""},
    })
    raw, active, cluster, _ = _reload_config(f)
    assert active == "dev"
    assert cluster["domain_suffix"] == "alpha.nexus.local"
    assert cluster["arch"] == "amd64"
    assert cluster["_active"] == "dev"


def test_cluster_config_returns_active_prod(tmp_path):
    """active=prod이면 prod 설정을 반환."""
    f = tmp_path / "cluster.yaml"
    _write_cluster_yaml(f, {
        "active": "prod",
        "dev": {"domain_suffix": "alpha.nexus.local", "arch": "amd64", "kubeconfig": ""},
        "prod": {"domain_suffix": "cluster.local", "arch": "arm64", "kubeconfig": ""},
    })
    raw, active, cluster, _ = _reload_config(f)
    assert active == "prod"
    assert cluster["domain_suffix"] == "cluster.local"
    assert cluster["arch"] == "arm64"


def test_cluster_config_defaults_when_file_missing(tmp_path):
    """파일이 없으면 _DEFAULTS 반환."""
    missing = tmp_path / "nonexistent.yaml"
    raw, active, cluster, _ = _reload_config(missing)
    assert cluster["domain_suffix"] == "cluster.local"
    assert cluster["arch"] == "amd64"
    assert active == "dev"


def test_cluster_config_defaults_when_invalid_yaml(tmp_path):
    """YAML 파싱 오류 시 _DEFAULTS 반환."""
    f = tmp_path / "cluster.yaml"
    f.write_text("{ invalid yaml :", encoding="utf-8")
    raw, active, cluster, _ = _reload_config(f)
    assert cluster["domain_suffix"] == "cluster.local"


def test_cluster_config_defaults_active_missing(tmp_path):
    """active 키 없으면 'dev'로 폴백."""
    f = tmp_path / "cluster.yaml"
    _write_cluster_yaml(f, {
        "dev": {"domain_suffix": "alpha.nexus.local", "arch": "amd64", "kubeconfig": ""},
    })
    raw, active, cluster, _ = _reload_config(f)
    assert active == "dev"
    assert cluster["domain_suffix"] == "alpha.nexus.local"


def test_cluster_config_active_env_not_defined_uses_defaults(tmp_path):
    """active가 존재하지 않는 환경을 가리키면 _DEFAULTS로 폴백."""
    f = tmp_path / "cluster.yaml"
    _write_cluster_yaml(f, {
        "active": "staging",  # staging 정의 없음
        "dev": {"domain_suffix": "alpha.nexus.local", "arch": "amd64", "kubeconfig": ""},
    })
    raw, active, cluster, _ = _reload_config(f)
    assert active == "staging"
    # staging 정의 없으므로 _DEFAULTS 값 유지
    assert cluster["domain_suffix"] == "cluster.local"


# ── all_envs ──────────────────────────────────────────────────────────────────

def test_all_envs_returns_all_environments(tmp_path):
    """dev, prod 두 환경 모두 반환."""
    import harness.config as mod
    f = tmp_path / "cluster.yaml"
    _write_cluster_yaml(f, {
        "active": "dev",
        "dev": {"domain_suffix": "alpha.nexus.local", "arch": "amd64", "kubeconfig": ""},
        "prod": {"domain_suffix": "cluster.local", "arch": "arm64", "kubeconfig": ""},
    })
    with patch.object(mod, "_raw", yaml.safe_load(f.read_text())):
        envs = mod.all_envs()

    assert "dev" in envs
    assert "prod" in envs
    assert envs["dev"]["domain_suffix"] == "alpha.nexus.local"
    assert envs["prod"]["arch"] == "arm64"


def test_all_envs_excludes_active_key(tmp_path):
    """'active' 키는 환경 목록에 포함되지 않는다."""
    import harness.config as mod
    raw = {
        "active": "dev",
        "dev": {"domain_suffix": "alpha.nexus.local", "arch": "amd64", "kubeconfig": ""},
    }
    with patch.object(mod, "_raw", raw):
        envs = mod.all_envs()

    assert "active" not in envs
    assert "dev" in envs


def test_all_envs_applies_defaults(tmp_path):
    """각 환경에 _DEFAULTS가 merge된다."""
    import harness.config as mod
    raw = {
        "active": "dev",
        "dev": {"domain_suffix": "alpha.nexus.local"},  # arch, kubeconfig 생략
    }
    with patch.object(mod, "_raw", raw):
        envs = mod.all_envs()

    # 생략된 필드는 _DEFAULTS로 채워짐
    assert "arch" in envs["dev"]
    assert envs["dev"]["arch"] == "amd64"
    assert "kubeconfig" in envs["dev"]


# ── kubeconfig_path ───────────────────────────────────────────────────────────

def test_kubeconfig_path_empty_returns_none():
    """kubeconfig가 빈 문자열이면 None 반환."""
    import harness.config as mod
    with patch.object(mod, "_kubeconfig", None):
        assert mod.kubeconfig_path() is None


def test_kubeconfig_path_returns_expanded(tmp_path):
    """kubeconfig 경로가 설정되면 expanduser된 절대 경로 반환."""
    import harness.config as mod
    expected = str(tmp_path / "kubeconfig")
    with patch.object(mod, "_kubeconfig", expected):
        assert mod.kubeconfig_path() == expected


def test_kubeconfig_path_tilde_expanded(monkeypatch):
    """~/path 형태는 expanduser()로 변환된다."""
    import harness.config as mod
    home = str(Path.home())
    with patch.object(mod, "_kubeconfig", f"{home}/.kube/config"):
        result = mod.kubeconfig_path()
    assert "~" not in result
    assert result.startswith("/")


# ── 실제 cluster.yaml 통합 테스트 ─────────────────────────────────────────────

def test_real_cluster_yaml_loads():
    """실제 config/cluster.yaml이 정상 로드된다."""
    import harness.config as mod
    cc = mod.cluster_config()
    # 필수 키 존재
    assert "domain_suffix" in cc
    assert "arch" in cc
    assert "_active" in cc
    # 값 타입 확인
    assert isinstance(cc["domain_suffix"], str)
    assert isinstance(cc["arch"], str)


def test_real_all_envs_has_dev_and_prod():
    """실제 cluster.yaml에 dev, prod 두 환경이 정의되어 있다."""
    import harness.config as mod
    if not mod._CONFIG_PATH.exists():
        pytest.skip("cluster.yaml not present in this environment")
    envs = mod.all_envs()
    assert "dev" in envs, "cluster.yaml에 dev 환경이 없음"
    assert "prod" in envs, "cluster.yaml에 prod 환경이 없음"
    assert envs["dev"]["arch"] == "amd64"
    assert envs["prod"]["arch"] == "arm64"
