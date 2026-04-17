"""Tests for harness/cli.py — JSON shape, exit codes, session log handling.

The heavy lifting (static.run_static / runtime.apply / runtime.verify_runtime)
is monkeypatched so these tests focus on argparse wiring + JSON envelope.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from harness import cli
from harness.static import CheckResult


def _capture(monkeypatch) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    return buf


def _run(monkeypatch, argv: list[str]) -> tuple[int, dict]:
    buf = _capture(monkeypatch)
    code = cli.main(argv)
    return code, json.loads(buf.getvalue())


@pytest.fixture
def stubbed_static(monkeypatch):
    def _fake(service, cfg):
        return [
            CheckResult(name="yamllint", status="pass"),
            CheckResult(name="helm_lint", status="pass"),
        ]
    monkeypatch.setattr(cli.static, "run_static", _fake)


@pytest.fixture
def stubbed_apply(monkeypatch):
    def _fake(service, cfg):
        return [
            CheckResult(name="helm_install", status="pass"),
        ]
    monkeypatch.setattr(cli.runtime, "apply", _fake)


@pytest.fixture
def stubbed_verify_runtime(monkeypatch):
    def _fake(service, cfg, *, phase=None, sub_goal=None):
        return [
            CheckResult(name="kubectl_wait", status="pass"),
            CheckResult(
                name="smoke_test", status="skip",
                detail=f"phase={phase} sub_goal={sub_goal}",
            ),
        ]
    monkeypatch.setattr(cli.runtime, "verify_runtime", _fake)


# ─── exit codes / envelope ───────────────────────────────────────────────────


def test_verify_static_exit_zero_on_pass(cfg, config_path, stubbed_static, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HARNESS_SESSION_LOG", raising=False)
    code, payload = _run(monkeypatch, [
        "--config", str(config_path),
        "verify-static", "--service", "svc",
    ])
    assert code == 0
    assert payload["service"] == "svc"
    assert payload["stage"] == "verify-static"
    assert payload["passed"] is True
    assert len(payload["checks"]) == 2
    # default session log path created under logging.dir
    assert payload["session_log"]
    assert "verify-static" in payload["session_log"]


def test_verify_static_exit_one_on_fail(cfg, config_path, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HARNESS_SESSION_LOG", raising=False)

    def _fake(service, cfg):
        return [CheckResult(name="helm_lint", status="fail", detail="boom")]
    monkeypatch.setattr(cli.static, "run_static", _fake)

    code, payload = _run(monkeypatch, [
        "--config", str(config_path),
        "verify-static", "--service", "svc",
    ])
    assert code == 1
    assert payload["passed"] is False
    assert payload["checks"][0]["status"] == "fail"


def test_config_error_exits_two(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "no-such.yaml"
    code, payload = _run(monkeypatch, [
        "--config", str(missing),
        "verify-static", "--service", "svc",
    ])
    assert code == 2
    assert payload["error"] == "config"
    assert "config not found" in payload["message"]


def test_apply_subcommand_wires_runtime_apply(
    cfg, config_path, stubbed_apply, monkeypatch, tmp_path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HARNESS_SESSION_LOG", raising=False)
    code, payload = _run(monkeypatch, [
        "--config", str(config_path),
        "apply", "--service", "svc",
    ])
    assert code == 0
    assert payload["stage"] == "apply"


def test_verify_runtime_passes_phase_and_sub_goal(
    cfg, config_path, stubbed_verify_runtime, monkeypatch, tmp_path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HARNESS_SESSION_LOG", raising=False)
    code, payload = _run(monkeypatch, [
        "--config", str(config_path),
        "verify-runtime", "--service", "svc",
        "--phase", "p1", "--sub-goal", "svc",
    ])
    assert code == 0
    smoke = next(c for c in payload["checks"] if c["name"] == "smoke_test")
    assert "phase=p1" in smoke["detail"]
    assert "sub_goal=svc" in smoke["detail"]


def test_existing_session_log_env_is_honored(
    cfg, config_path, stubbed_static, monkeypatch, tmp_path,
):
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "my-session.log"
    monkeypatch.setenv("HARNESS_SESSION_LOG", str(log))
    code, payload = _run(monkeypatch, [
        "--config", str(config_path),
        "verify-static", "--service", "svc",
    ])
    assert code == 0
    assert payload["session_log"] == str(log)


def test_default_session_log_created_under_logging_dir(
    cfg, config_path, stubbed_static, monkeypatch, tmp_path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HARNESS_SESSION_LOG", raising=False)
    code, payload = _run(monkeypatch, [
        "--config", str(config_path),
        "verify-static", "--service", "svc",
    ])
    assert code == 0
    log = Path(payload["session_log"])
    # logging.dir from conftest MINIMAL_YAML is "logs/deploy"
    assert "logs/deploy" in str(log) or "logs\\deploy" in str(log)
    assert log.parent.exists()


# ─── argparse guardrails ─────────────────────────────────────────────────────


def test_missing_subcommand_errors(monkeypatch):
    # argparse exits with SystemExit(2) when 'required=True' subcommand is missing
    with pytest.raises(SystemExit):
        cli.main([])


def test_apply_requires_service(monkeypatch):
    with pytest.raises(SystemExit):
        cli.main(["apply"])
