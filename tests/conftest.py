"""Common fixtures: minimal harness.yaml, temp session log, cache reset."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from harness import config as config_mod


MINIMAL_YAML = dedent(
    """
    cluster:
      namespace: test-ns

    conventions:
      workspace_dir: ws
      chart_path: "{workspace}/helm/{service}"
      docker_path: "{workspace}/docker/{service}"
      smoke_test_path: "{workspace}/tests/{phase}/smoke-test-{sub_goal}.sh"
      release_name: "{service}-{active_env}-v1"
      label_selector: "app.kubernetes.io/name={service}"
      values_files: ["values.yaml", "values-{active_env}.yaml"]
      write_allowed_globs: ["{workspace}/helm/**", "{workspace}/docker/**"]
      write_denied_globs: ["{workspace}/tests/**"]
      registry: "registry.test/myns"
      image_tag: "dev"

    environments:
      active: dev
      dev:
        domain_suffix: dev.example.local
        arch: amd64
        node_selectors:
          storage: node-a
      prod:
        domain_suffix: cluster.local
        arch: arm64
        node_selectors:
          storage: node-p

    checks:
      static:
        yamllint: {enabled: true}
        helm_lint: {enabled: true}
        kubeconform: {enabled: true}
        trivy_config: {enabled: false}
        gitleaks: {enabled: true}
        helm_dry_run_server: {enabled: false}
        hadolint: {enabled: true}
        gitleaks_docker: {enabled: true}
      runtime:
        docker_build_push: {enabled: true}
        helm_upgrade: {enabled: true}
        kubectl_wait:
          enabled: true
          initial_wait_seconds: 5
          terminal_grace_seconds: 10
        smoke_test: {enabled: true}

    logging:
      dir: "logs/deploy"
      tail_chars: 500
      retention_days: 30

    orchestration:
      max_runtime_retries: 3
    """
).strip()


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    p = tmp_path / "harness.yaml"
    p.write_text(MINIMAL_YAML, encoding="utf-8")
    return p


@pytest.fixture
def cfg(config_path: Path):
    config_mod.load_config.cache_clear()
    return config_mod.load_config(config_path)


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Ensure lru_cache doesn't leak across tests."""
    yield
    config_mod.load_config.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Auto-chdir into tmp_path so no test can leak `logs/` or `workspace/`
    into the repo root, even if the author forgets an explicit chdir.
    tmp_path is auto-removed by pytest, so the artifacts go with it.
    """
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture
def session_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "session.log"
    monkeypatch.setenv("HARNESS_SESSION_LOG", str(path))
    return path
