"""Subprocess runner + session-log append.

Every external command in kubeharness goes through :func:`run` (or :func:`pipe`
for shell-style pipelines). When ``HARNESS_SESSION_LOG`` is set in the
environment, stdout/stderr are appended to that file with a section header —
this is how ``/deploy`` gets "one session = one log" (refactor.md §11.2).
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RunResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _augment_path(env: dict[str, str]) -> None:
    """Prepend ~/.local/bin so ``pipx``-installed CLIs (kubeconform, etc.) resolve."""
    local_bin = str(Path.home() / ".local" / "bin")
    current = env.get("PATH", "")
    if local_bin not in current.split(os.pathsep):
        env["PATH"] = local_bin + os.pathsep + current if current else local_bin


def _build_env(extra: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    _augment_path(env)
    if extra:
        env.update(extra)
    return env


def _session_log_path() -> Path | None:
    p = os.environ.get("HARNESS_SESSION_LOG")
    return Path(p) if p else None


def _append_session(
    label: str | None,
    command_str: str,
    result: RunResult,
    *,
    log_stdout: bool = True,
    stdout_sidecar: Path | None = None,
) -> None:
    path = _session_log_path()
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"--- [{label or 'cmd'}] $ {command_str} ---\n"
    body_parts: list[str] = []
    if result.stdout:
        if not log_stdout:
            if stdout_sidecar is not None:
                body_parts.append(f"[stdout -> {stdout_sidecar}]")
            else:
                body_parts.append(f"[stdout suppressed: {len(result.stdout)} bytes]")
        else:
            body_parts.append(result.stdout.rstrip("\n"))
    if result.stderr:
        body_parts.append(result.stderr.rstrip("\n"))
    body = ("\n".join(body_parts) + "\n") if body_parts else ""
    footer = f"[exit {result.exit_code}] (duration: {result.duration:.2f}s)\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(header + body + footer)


def run(
    cmd: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: int | None = None,
    label: str | None = None,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    log_stdout: bool = True,
    stdout_sidecar: Path | None = None,
) -> RunResult:
    """Execute ``cmd`` and return a :class:`RunResult`.

    If ``HARNESS_SESSION_LOG`` is set, stdout/stderr are appended to that file
    with a section header regardless of exit code. No per-tool log files.

    ``log_stdout=False`` replaces the stdout body in the session log with a
    one-line placeholder — use for commands whose stdout is large but
    re-derivable from repo state (e.g. ``helm template``). ``stdout_sidecar``,
    if set, additionally writes raw stdout to that file and references it in
    the session log, for commands whose stdout is needed for later analysis
    but too large to embed inline (e.g. ``kubectl get pods -o json``).
    """
    command_str = " ".join(cmd)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=_build_env(env),
            input=stdin,
        )
        result = RunResult(
            command=list(cmd),
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired:
        result = RunResult(
            command=list(cmd),
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            duration=time.monotonic() - started,
        )
    except FileNotFoundError as e:
        result = RunResult(
            command=list(cmd),
            exit_code=-1,
            stdout="",
            stderr=f"command not found: {e}",
            duration=time.monotonic() - started,
        )
    if stdout_sidecar is not None and result.stdout:
        stdout_sidecar.parent.mkdir(parents=True, exist_ok=True)
        stdout_sidecar.write_text(result.stdout, encoding="utf-8")
    _append_session(
        label,
        command_str,
        result,
        log_stdout=log_stdout,
        stdout_sidecar=stdout_sidecar if (stdout_sidecar and result.stdout) else None,
    )
    return result


def pipe(
    cmd1: list[str],
    cmd2: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: int | None = None,
    label: str | None = None,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run ``cmd1 | cmd2`` and return the result of ``cmd2``.

    Used by ``helm template | kubeconform`` (refactor.md §21.1). stderr from
    both stages is concatenated into the result.
    """
    command_str = " ".join(cmd1) + " | " + " ".join(cmd2)
    started = time.monotonic()
    full_env = _build_env(env)

    p1 = p2 = None
    stdout_b = stderr_b = b""
    p1_stderr_b = b""
    exit_code = -1
    err_msg: str | None = None

    try:
        p1 = subprocess.Popen(
            cmd1,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=full_env,
        )
        p2 = subprocess.Popen(
            cmd2,
            stdin=p1.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=full_env,
        )
        assert p1.stdout is not None
        p1.stdout.close()  # let p1 receive SIGPIPE if p2 exits early
        stdout_b, stderr_b = p2.communicate(timeout=timeout)
        try:
            _, p1_stderr_b = p1.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            p1.kill()
            _, p1_stderr_b = p1.communicate()
        exit_code = p2.returncode
    except subprocess.TimeoutExpired:
        err_msg = f"Command timed out after {timeout}s"
    except FileNotFoundError as e:
        err_msg = f"command not found: {e}"
    finally:
        for p in (p2, p1):
            if p is not None and p.poll() is None:
                p.kill()
                p.wait()

    if err_msg:
        result = RunResult(
            command=[command_str],
            exit_code=-1,
            stdout="",
            stderr=err_msg,
            duration=time.monotonic() - started,
        )
    else:
        combined_stderr = (
            p1_stderr_b.decode("utf-8", errors="replace")
            + stderr_b.decode("utf-8", errors="replace")
        )
        result = RunResult(
            command=[command_str],
            exit_code=exit_code,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=combined_stderr,
            duration=time.monotonic() - started,
        )
    _append_session(label, command_str, result)
    return result


def write_session_event(message: str) -> None:
    """Append a free-form line to the current session log (if any).

    Used by CLI / orchestrator to record non-command events (e.g. retry counter,
    approval granted).
    """
    path = _session_log_path()
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(message.rstrip("\n") + "\n")
