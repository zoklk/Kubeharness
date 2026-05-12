"""Tests for harness/static.py — detection gating + check execution.

External CLIs (yamllint, helm, kubeconform, ...) are replaced with a stub that
records calls and returns canned RunResults. This keeps the suite hermetic and
fast while still exercising the registry + detection + disabled-skip logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import shell, static
from harness.static import CheckResult


class _ShellStub:
    """Drop-in replacement for ``shell.run`` / ``shell.pipe``."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.defaults: dict[str, shell.RunResult] = {}

    def set(self, label: str, result: shell.RunResult) -> None:
        self.defaults[label] = result

    def _result(self, label: str | None) -> shell.RunResult:
        return self.defaults.get(
            label or "",
            shell.RunResult(command=[], exit_code=0, stdout="", stderr=""),
        )

    def run(self, cmd, *, label=None, **_):
        self.calls.append(list(cmd))
        return self._result(label)

    def pipe(self, cmd1, cmd2, *, label=None, **_):
        self.calls.append(list(cmd1) + ["|"] + list(cmd2))
        return self._result(label)


@pytest.fixture
def stub(monkeypatch):
    s = _ShellStub()
    monkeypatch.setattr(static.shell, "run", s.run)
    monkeypatch.setattr(static.shell, "pipe", s.pipe)
    return s


@pytest.fixture
def helm_chart(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    chart = tmp_path / "ws" / "helm" / "svc"
    chart.mkdir(parents=True)
    (chart / "Chart.yaml").write_text("name: svc\nversion: 0.1.0\n")
    (chart / "values.yaml").write_text("replicaCount: 1\n")
    (chart / "values-dev.yaml").write_text("replicaCount: 1\n")
    (chart / "templates").mkdir()
    (chart / "templates" / "deploy.yaml").write_text("# go template")
    return chart


@pytest.fixture
def docker_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "ws" / "docker" / "svc"
    d.mkdir(parents=True)
    (d / "Dockerfile").write_text("FROM alpine\n")
    return d


# ─── detection ───────────────────────────────────────────────────────────────


def test_no_artifacts_fails_with_detection(cfg, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    results = static.run_static("nope", cfg)
    assert len(results) == 1
    assert results[0].name == "artifact_detection"
    assert results[0].status == "fail"


def test_helm_only_triggers_helm_checks(cfg, helm_chart, stub):
    results = static.run_static("svc", cfg)
    names = [r.name for r in results]
    assert "yamllint" in names
    assert "helm_lint" in names
    assert "kubeconform" in names
    assert "hadolint" not in names  # no Dockerfile


def test_docker_only_triggers_docker_checks(cfg, docker_dir, stub):
    results = static.run_static("svc", cfg)
    names = [r.name for r in results]
    assert "hadolint" in names
    assert "gitleaks_docker" in names
    assert "helm_lint" not in names


def test_disabled_check_reports_skip(cfg, helm_chart, stub):
    # trivy_config is disabled in the fixture YAML
    results = static.run_static("svc", cfg)
    trivy = next(r for r in results if r.name == "trivy_config")
    assert trivy.status == "skip"
    assert "disabled" in (trivy.detail or "")


# ─── result mapping ─────────────────────────────────────────────────────────


def test_failure_produces_detail_and_log_tail(cfg, helm_chart, stub):
    stub.set(
        "static/helm_lint",
        shell.RunResult(
            command=["helm", "lint"],
            exit_code=1,
            stdout="",
            stderr="Error: values-dev.yaml:3 invalid",
        ),
    )
    results = static.run_static("svc", cfg)
    helm_lint = next(r for r in results if r.name == "helm_lint")
    assert helm_lint.status == "fail"
    assert "invalid" in (helm_lint.detail or "")
    assert "values-dev.yaml:3" in (helm_lint.log_tail or "")


def test_missing_cli_is_skipped(cfg, helm_chart, stub):
    stub.set(
        "static/kubeconform",
        shell.RunResult(
            command=[], exit_code=-1,
            stdout="", stderr="command not found: kubeconform",
        ),
    )
    results = static.run_static("svc", cfg)
    kcf = next(r for r in results if r.name == "kubeconform")
    assert kcf.status == "skip"


def test_yamllint_excludes_templates_dir(cfg, helm_chart, stub):
    static.run_static("svc", cfg)
    # find the yamllint call
    yamllint_calls = [c for c in stub.calls if c and c[0] == "yamllint"]
    assert yamllint_calls, "yamllint not invoked"
    files = yamllint_calls[0]
    for f in files:
        assert "templates/" not in f


def test_values_files_are_passed_to_helm_lint(cfg, helm_chart, stub):
    static.run_static("svc", cfg)
    helm_lint_calls = [c for c in stub.calls if c and c[:2] == ["helm", "lint"]]
    assert helm_lint_calls
    joined = " ".join(helm_lint_calls[0])
    assert "values.yaml" in joined
    assert "values-dev.yaml" in joined


def test_kubeconform_uses_post_renderer_when_script_present(cfg, helm_chart, stub):
    script = helm_chart / "post-render.sh"
    script.write_text("#!/bin/sh\ncat\n")
    script.chmod(0o755)
    static.run_static("svc", cfg)
    tmpl_calls = [c for c in stub.calls if c and c[:2] == ["helm", "template"]]
    assert tmpl_calls
    assert "--post-renderer" in tmpl_calls[0]
    assert tmpl_calls[0][tmpl_calls[0].index("--post-renderer") + 1] == str(script.resolve())


def test_kubeconform_no_post_renderer_when_script_absent(cfg, helm_chart, stub):
    static.run_static("svc", cfg)
    tmpl_calls = [c for c in stub.calls if c and c[:2] == ["helm", "template"]]
    assert tmpl_calls
    assert "--post-renderer" not in tmpl_calls[0]
