"""Tests for harness/shell.py — session log append, missing command, pipe."""

from __future__ import annotations

from pathlib import Path

from harness import shell


def test_run_captures_stdout(session_log: Path):
    r = shell.run(["printf", "hello"], label="test/echo")
    assert r.ok
    assert r.stdout == "hello"
    content = session_log.read_text()
    assert "[test/echo]" in content
    assert "hello" in content
    assert "[exit 0]" in content


def test_run_appends_on_failure(session_log: Path):
    r = shell.run(["sh", "-c", "echo oops 1>&2; exit 3"], label="test/fail")
    assert not r.ok
    assert r.exit_code == 3
    content = session_log.read_text()
    assert "oops" in content
    assert "[exit 3]" in content


def test_run_missing_command(session_log: Path):
    r = shell.run(["__does_not_exist__"], label="test/missing")
    assert r.exit_code == -1
    assert "command not found" in r.stderr


def test_run_timeout(session_log: Path):
    r = shell.run(["sleep", "2"], timeout=0, label="test/timeout")
    # timeout=0 rejects immediately
    assert r.exit_code == -1
    assert "timed out" in r.stderr


def test_run_without_session_log_does_not_error(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("HARNESS_SESSION_LOG", raising=False)
    r = shell.run(["printf", "ok"])
    assert r.ok
    assert r.stdout == "ok"


def test_pipe_helm_template_style(session_log: Path):
    r = shell.pipe(
        ["printf", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n"],
        ["grep", "kind:"],
        label="test/pipe",
    )
    assert r.ok
    assert "kind: ConfigMap" in r.stdout


def test_write_session_event(session_log: Path):
    shell.write_session_event("[orchestrator] started")
    assert "[orchestrator] started" in session_log.read_text()


def test_run_log_stdout_false_suppresses_body(session_log: Path):
    r = shell.run(
        ["sh", "-c", 'printf "$PAYLOAD"'],
        env={"PAYLOAD": "SECRET_BODY_OUTPUT"},
        label="test/quiet",
        log_stdout=False,
    )
    assert r.ok
    assert r.stdout == "SECRET_BODY_OUTPUT"
    content = session_log.read_text()
    assert "SECRET_BODY_OUTPUT" not in content
    assert "[stdout suppressed: 18 bytes]" in content


def test_run_stdout_sidecar_writes_file_and_logs_pointer(
    session_log: Path, tmp_path: Path
):
    sidecar = tmp_path / "pods.json"
    r = shell.run(
        ["sh", "-c", 'printf "$PAYLOAD"'],
        env={"PAYLOAD": "SIDECAR_ONLY_PAYLOAD"},
        label="test/sidecar",
        log_stdout=False,
        stdout_sidecar=sidecar,
    )
    assert r.ok
    assert sidecar.read_text() == "SIDECAR_ONLY_PAYLOAD"
    content = session_log.read_text()
    assert "SIDECAR_ONLY_PAYLOAD" not in content
    assert f"[stdout -> {sidecar}]" in content


def test_custom_env_merged_with_os_env(session_log: Path, monkeypatch):
    monkeypatch.setenv("OUTER", "set-outside")
    r = shell.run(
        ["sh", "-c", "echo $OUTER $INJECTED"],
        env={"INJECTED": "from-kwarg"},
        label="test/env",
    )
    assert r.ok
    assert "set-outside from-kwarg" in r.stdout
