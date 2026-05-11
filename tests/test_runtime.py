"""Tests for harness/runtime.py — apply + verify_runtime flows.

Shell calls are stubbed. The stub routes by ``label`` so tests can assert on
specific command outcomes (helm status / template / uninstall / wait).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import runtime, shell


class _ShellStub:
    def __init__(self):
        self.calls: list[tuple[str | None, list[str]]] = []
        self.canned: dict[str, shell.RunResult] = {}

    def set(self, label: str, **kwargs) -> None:
        defaults = {"command": [], "exit_code": 0, "stdout": "", "stderr": ""}
        defaults.update(kwargs)
        self.canned[label] = shell.RunResult(**defaults)

    def run(self, cmd, *, label=None, **_):
        self.calls.append((label, list(cmd)))
        return self.canned.get(
            label or "",
            shell.RunResult(command=list(cmd), exit_code=0, stdout="", stderr=""),
        )

    def pipe(self, *a, **_):
        raise AssertionError("pipe should not be used in runtime")


@pytest.fixture
def stub(monkeypatch):
    s = _ShellStub()
    monkeypatch.setattr(runtime.shell, "run", s.run)
    monkeypatch.setattr(runtime.shell, "pipe", s.pipe)
    return s


@pytest.fixture
def helm_chart(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    chart = tmp_path / "ws" / "helm" / "svc"
    chart.mkdir(parents=True)
    (chart / "Chart.yaml").write_text("name: svc\nversion: 0.1.0\n")
    (chart / "values.yaml").write_text("replicaCount: 1\n")
    return chart


@pytest.fixture
def docker_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "ws" / "docker" / "svc"
    d.mkdir(parents=True)
    (d / "Dockerfile").write_text("FROM alpine\n")
    return d


# ─── apply ──────────────────────────────────────────────────────────────────


def test_apply_no_artifacts_fails(cfg, tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    results = runtime.apply("nope", cfg)
    assert results[0].name == "artifact_detection"
    assert results[0].status == "fail"


def test_apply_docker_build_push(cfg, docker_dir, stub):
    results = runtime.apply("svc", cfg)
    names = [r.name for r in results]
    assert "docker_build" in names
    assert "docker_push" in names
    assert all(r.status == "pass" for r in results)
    # verify --platform flag was passed
    build_cmd = next(cmd for label, cmd in stub.calls if label == "apply/docker_build")
    assert "--platform" in build_cmd
    assert "linux/amd64" in build_cmd


def test_apply_helm_fresh_install(cfg, helm_chart, stub):
    stub.set("apply/helm_status", exit_code=1)  # release does not exist
    results = runtime.apply("svc", cfg)
    labels = [label for label, _ in stub.calls]
    # helm uninstall should NOT be called when status shows no release
    assert "apply/helm_uninstall" not in labels
    assert "apply/helm_install" in labels
    assert any(r.name == "helm_install" and r.status == "pass" for r in results)


def test_apply_helm_uninstall_when_release_exists(cfg, helm_chart, stub):
    stub.set("apply/helm_status", exit_code=0)  # release exists
    results = runtime.apply("svc", cfg)
    labels = [label for label, _ in stub.calls]
    assert "apply/helm_uninstall" in labels
    assert "apply/helm_install" in labels


def test_apply_docker_fail_skips_helm(cfg, tmp_path: Path, monkeypatch, stub):
    # set up both helm and docker
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ws" / "helm" / "svc").mkdir(parents=True)
    (tmp_path / "ws" / "helm" / "svc" / "Chart.yaml").write_text("name: svc\n")
    (tmp_path / "ws" / "docker" / "svc").mkdir(parents=True)
    (tmp_path / "ws" / "docker" / "svc" / "Dockerfile").write_text("FROM alpine\n")

    stub.set("apply/docker_build", exit_code=1, stderr="build failed")
    results = runtime.apply("svc", cfg)
    names = {r.name: r.status for r in results}
    assert names["docker_build"] == "fail"
    assert names["docker_push"] == "skip"
    assert names.get("helm_install") == "skip"


# ─── verify_runtime ──────────────────────────────────────────────────────────


_DEPLOY_YAML = (
    "apiVersion: apps/v1\n"
    "kind: Deployment\n"
    "metadata:\n  name: svc\n"
)

_CRD_YAML = (
    "apiVersion: apiextensions.k8s.io/v1\n"
    "kind: CustomResourceDefinition\n"
    "metadata:\n  name: foos.example.com\n"
)

_CRONJOB_YAML = (
    "apiVersion: batch/v1\n"
    "kind: CronJob\n"
    "metadata:\n  name: svc\n"
)

_HOOK_JOB_YAML = (
    "apiVersion: batch/v1\n"
    "kind: Job\n"
    "metadata:\n"
    "  name: svc-migrate\n"
    "  annotations:\n    helm.sh/hook: post-install\n"
)

_DEPLOY_PLUS_CRONJOB_YAML = _DEPLOY_YAML + "---\n" + _CRONJOB_YAML


def test_verify_runtime_crd_only_chart_skips_kubectl_wait(cfg, helm_chart, stub):
    stub.set("verify-runtime/helm_template", stdout=_CRD_YAML)
    results = runtime.verify_runtime("svc", cfg)
    kw = next(r for r in results if r.name == "kubectl_wait")
    assert kw.status == "skip"
    assert "CRD-only" in (kw.detail or "")


def test_verify_runtime_cronjob_only_chart_skips_wait_but_runs_smoke(
    cfg, helm_chart, stub, tmp_path
):
    stub.set("verify-runtime/helm_template", stdout=_CRONJOB_YAML)
    smoke_path = tmp_path / "ws" / "tests" / "p1"
    smoke_path.mkdir(parents=True)
    (smoke_path / "smoke-test-svc.sh").write_text("#!/bin/bash\nexit 0\n")
    results = runtime.verify_runtime("svc", cfg, phase="p1")
    kw = next(r for r in results if r.name == "kubectl_wait")
    assert kw.status == "skip"
    assert "batch-only" in (kw.detail or "")
    # batch-only skip must not block the smoke test
    smoke = next(r for r in results if r.name == "smoke_test")
    assert smoke.status == "pass"
    assert any(label == "verify-runtime/smoke_test" for label, _ in stub.calls)
    # no kubectl wait should have been attempted
    assert not any(label == "verify-runtime/kubectl_wait" for label, _ in stub.calls)


def test_verify_runtime_hook_job_only_chart_skips_wait(cfg, helm_chart, stub):
    stub.set("verify-runtime/helm_template", stdout=_HOOK_JOB_YAML)
    results = runtime.verify_runtime("svc", cfg)
    kw = next(r for r in results if r.name == "kubectl_wait")
    assert kw.status == "skip"
    assert "batch-only" in (kw.detail or "")


def test_verify_runtime_deployment_plus_cronjob_still_waits(cfg, helm_chart, stub):
    stub.set("verify-runtime/helm_template", stdout=_DEPLOY_PLUS_CRONJOB_YAML)
    stub.set("verify-runtime/kubectl_wait", exit_code=0)
    results = runtime.verify_runtime("svc", cfg)
    kw = next(r for r in results if r.name == "kubectl_wait")
    assert kw.status == "pass"
    assert any(label == "verify-runtime/kubectl_wait" for label, _ in stub.calls)


def test_verify_runtime_kubectl_wait_pass(cfg, helm_chart, stub):
    stub.set("verify-runtime/helm_template", stdout=_DEPLOY_YAML)
    stub.set("verify-runtime/kubectl_wait", exit_code=0)
    results = runtime.verify_runtime("svc", cfg)
    kw = next(r for r in results if r.name == "kubectl_wait")
    assert kw.status == "pass"


def test_verify_runtime_helm_template_failure_waits_conservatively(cfg, helm_chart, stub):
    # helm template fails → assume workloads present → kubectl wait still runs
    stub.set("verify-runtime/helm_template", exit_code=1, stderr="template error")
    stub.set("verify-runtime/kubectl_wait", exit_code=0)
    results = runtime.verify_runtime("svc", cfg)
    kw = next(r for r in results if r.name == "kubectl_wait")
    assert kw.status == "pass"
    assert any(label == "verify-runtime/kubectl_wait" for label, _ in stub.calls)


def test_verify_runtime_terminal_state_early_exit(cfg, helm_chart, stub):
    stub.set("verify-runtime/helm_template", stdout=_DEPLOY_YAML)
    # first kubectl wait fails
    stub.set("verify-runtime/kubectl_wait", exit_code=1, stderr="timed out")
    # pod listing shows CrashLoopBackOff → early exit
    pods_json = json.dumps({
        "items": [{
            "metadata": {"name": "svc-0"},
            "status": {"containerStatuses": [
                {"state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off"}}}
            ]},
        }]
    })
    stub.set("verify-runtime/kubectl_get_pods", stdout=pods_json)
    results = runtime.verify_runtime("svc", cfg)
    kw = next(r for r in results if r.name == "kubectl_wait")
    assert kw.status == "fail"
    assert "CrashLoopBackOff" in (kw.detail or "")
    # only one wait call — no grace retry
    wait_calls = [c for label, c in stub.calls if label == "verify-runtime/kubectl_wait"]
    assert len(wait_calls) == 1


def test_verify_runtime_progresses_through_grace_window(cfg, helm_chart, stub, monkeypatch):
    """First wait fails, pods still Pending → second wait runs and succeeds."""
    stub.set("verify-runtime/helm_template", stdout=_DEPLOY_YAML)
    # default canned result for label applies to both calls; tweak after first
    pods_json = json.dumps({
        "items": [{
            "metadata": {"name": "svc-0"},
            "status": {"containerStatuses": [
                {"state": {"waiting": {"reason": "ContainerCreating"}}}
            ]},
        }]
    })
    stub.set("verify-runtime/kubectl_get_pods", stdout=pods_json)

    # sequence-aware stub: first wait fails, second passes
    wait_outcomes = iter([
        shell.RunResult(command=[], exit_code=1, stdout="", stderr="timed out 5s"),
        shell.RunResult(command=[], exit_code=0, stdout="", stderr=""),
    ])
    real_run = stub.run

    def _routing_run(cmd, *, label=None, **_):
        if label == "verify-runtime/kubectl_wait":
            stub.calls.append((label, list(cmd)))
            return next(wait_outcomes)
        return real_run(cmd, label=label)
    monkeypatch.setattr(runtime.shell, "run", _routing_run)

    results = runtime.verify_runtime("svc", cfg)
    kw = next(r for r in results if r.name == "kubectl_wait")
    assert kw.status == "pass"


def test_smoke_test_skipped_without_phase(cfg, helm_chart, stub):
    stub.set("verify-runtime/helm_template", stdout=_DEPLOY_YAML)
    stub.set("verify-runtime/kubectl_wait", exit_code=0)
    results = runtime.verify_runtime("svc", cfg)
    smoke = next(r for r in results if r.name == "smoke_test")
    assert smoke.status == "skip"
    assert "--phase" in (smoke.detail or "")


def test_smoke_test_runs_when_script_exists(cfg, helm_chart, stub, tmp_path):
    stub.set("verify-runtime/helm_template", stdout=_DEPLOY_YAML)
    stub.set("verify-runtime/kubectl_wait", exit_code=0)
    smoke_path = tmp_path / "ws" / "tests" / "p1"
    smoke_path.mkdir(parents=True)
    (smoke_path / "smoke-test-svc.sh").write_text("#!/bin/bash\nexit 0\n")
    results = runtime.verify_runtime("svc", cfg, phase="p1")
    smoke = next(r for r in results if r.name == "smoke_test")
    assert smoke.status == "pass"
    # verify env vars were assembled (cmd was bash <path>)
    smoke_calls = [c for label, c in stub.calls if label == "verify-runtime/smoke_test"]
    assert smoke_calls
    assert smoke_calls[0][0] == "bash"
