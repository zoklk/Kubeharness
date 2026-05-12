"""Tests for harness/config.py — schema parsing, resolve(), env lookups."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.config import ConfigError, load_config


def test_loads_minimal(cfg):
    assert cfg.cluster.namespace == "test-ns"
    assert cfg.conventions.workspace_dir == "ws"
    assert cfg.active_env == "dev"


def test_resolve_substitutes_service_and_env(cfg):
    rs = cfg.resolve("prometheus")
    assert rs.release_name == "prometheus"
    assert rs.label_selector == "app.kubernetes.io/name=prometheus"
    assert rs.chart_path == Path("ws/helm/prometheus")
    assert rs.docker_path == Path("ws/docker/prometheus")
    assert rs.registry == "registry.test/myns"
    assert rs.image_tag == "dev"
    assert rs.build_platforms == ("linux/amd64", "linux/arm64")


def test_resolve_values_files_bind_active_env(cfg):
    vfs = cfg.resolve("prometheus").values_files()
    assert vfs == [Path("values.yaml"), Path("values-dev.yaml")]


def test_env_lookup(cfg):
    dev = cfg.env("dev")
    assert dev.domain_suffix == "dev.example.local"
    assert dev.node_selectors["storage"] == "node-a"


def test_env_unknown_raises(cfg):
    with pytest.raises(ConfigError, match="unknown environment"):
        cfg.env("staging")


def test_smoke_test_path(cfg):
    p = cfg.smoke_test_path("prometheus", phase="observability")
    assert p == Path("ws/tests/observability/smoke-test-prometheus.sh")


def test_static_checks_enabled_names(cfg):
    names = cfg.checks.static.enabled_names
    assert "yamllint" in names
    assert "trivy_config" not in names  # disabled in fixture
    assert "hadolint" in names


def test_runtime_kubectl_wait_values(cfg):
    kw = cfg.checks.runtime.kubectl_wait
    assert kw.enabled is True
    assert kw.initial_wait_seconds == 5
    assert kw.terminal_grace_seconds == 10


def test_missing_config_raises(tmp_path: Path):
    load_config.cache_clear()
    with pytest.raises(ConfigError, match="config not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_active_env_raises(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "cluster: {namespace: x}\n"
        "conventions: {workspace_dir: ws}\n"
        "environments:\n"
        "  active: staging\n"  # not defined below
        "  dev: {domain_suffix: d}\n"
        "checks:\n"
        "  static: {}\n"
        "  runtime:\n"
        "    kubectl_wait: {}\n"
        "logging: {dir: logs, tail_chars: 100, retention_days: 7}\n"
        "orchestration: {max_runtime_retries: 3}\n",
        encoding="utf-8",
    )
    load_config.cache_clear()
    with pytest.raises(ConfigError, match="no such environment"):
        load_config(bad)


def test_defaults_fill_when_fields_missing(tmp_path: Path):
    minimal = tmp_path / "min.yaml"
    minimal.write_text(
        "cluster: {namespace: x}\n"
        "conventions: {workspace_dir: ws, registry: r}\n"
        "environments:\n"
        "  active: dev\n"
        "  dev: {domain_suffix: d}\n"
        "checks:\n"
        "  static: {}\n"
        "  runtime:\n"
        "    kubectl_wait: {}\n"
        "logging: {dir: logs, tail_chars: 100, retention_days: 7}\n"
        "orchestration: {max_runtime_retries: 3}\n",
        encoding="utf-8",
    )
    load_config.cache_clear()
    c = load_config(minimal)
    # Defaults from refactor.md §9 kick in
    assert c.conventions.release_name == "{service}"
    assert c.conventions.image_tag == "latest"
    assert c.conventions.build_platforms == ("linux/amd64", "linux/arm64")
    assert c.checks.runtime.kubectl_wait.initial_wait_seconds == 60
    assert c.checks.runtime.kubectl_wait.terminal_grace_seconds == 240
