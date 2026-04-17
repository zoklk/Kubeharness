"""Kubeharness CLI — ``python -m harness <subcommand>``.

Five subcommands (refactor.md §10.4):

- ``init``            — scaffold templates/ into a consumer project
- ``verify-static``   — pre-deploy checks (yamllint, helm lint, kubeconform, ...)
- ``apply``           — docker build+push and/or helm upgrade
- ``verify-runtime``  — kubectl wait + smoke test
- ``session-path``    — print a canonical session-log path (no file IO)

All three verification subcommands emit a single JSON object on stdout
(refactor.md §11.1) and exit with:

- ``0``  everything passed
- ``1``  at least one check failed
- ``2``  configuration / environment error (bad YAML, missing CLI, etc.)

Session log selection, in precedence order:

1. ``--session-log <path>`` flag (CLI argument).
2. ``$HARNESS_SESSION_LOG`` env var.
3. Auto-generated ``logging.dir/<ts>-<service>-<stage>-standalone.log``.

Whichever wins is exported into the child environment so ``shell.run``
appends to it. The JSON response echoes the final path under ``session_log``.

The orchestrator subagent uses ``session-path`` once per deploy to get a
shared log path, then passes it via ``--session-log`` to every subsequent
subcommand — avoiding the bash-level env-prefix that Claude Code's
permission matcher doesn't recognize as ``python -m harness``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from harness import runtime, static
from harness.config import Config, ConfigError, load_config
from harness.shell import write_session_event
from harness.static import CheckResult


# ─── JSON helpers ────────────────────────────────────────────────────────────


def _summarize(checks: Sequence[CheckResult]) -> str:
    passed = sum(1 for c in checks if c.status == "pass")
    failed = sum(1 for c in checks if c.status == "fail")
    skipped = sum(1 for c in checks if c.status == "skip")
    return f"{passed} passed, {failed} failed, {skipped} skipped"


def _overall_passed(checks: Sequence[CheckResult]) -> bool:
    return all(c.status != "fail" for c in checks)


def _emit(
    *,
    service: str,
    stage: str,
    checks: Sequence[CheckResult],
    session_log: Path | None,
) -> int:
    passed = _overall_passed(checks)
    payload: dict[str, Any] = {
        "service": service,
        "stage": stage,
        "summary": _summarize(checks),
        "passed": passed,
        "session_log": str(session_log) if session_log else None,
        "checks": [asdict(c) for c in checks],
    }
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0 if passed else 1


def _config_error(msg: str) -> int:
    json.dump(
        {"error": "config", "message": msg},
        sys.stdout,
        indent=2,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 2


# ─── session log management ──────────────────────────────────────────────────


def _default_session_log_path(cfg: Config, stage: str, service: str) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    root = Path(cfg.logging.dir)
    return root / f"{ts}-{service}-{stage}-standalone.log"


def _prepare_session_log(
    cfg: Config,
    stage: str,
    service: str,
    override: str | None = None,
) -> Path:
    """Return the session log path, creating a default one if caller didn't set it.

    Precedence: ``override`` (CLI flag) > ``$HARNESS_SESSION_LOG`` > auto-generated.
    The chosen path is exported into the environment so ``shell.run`` appends to it.
    """
    if override:
        path = Path(override)
        os.environ["HARNESS_SESSION_LOG"] = str(path)
    else:
        existing = os.environ.get("HARNESS_SESSION_LOG")
        if existing:
            path = Path(existing)
        else:
            path = _default_session_log_path(cfg, stage, service)
            os.environ["HARNESS_SESSION_LOG"] = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ─── subcommand handlers ─────────────────────────────────────────────────────


def _cmd_verify_static(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        return _config_error(str(e))
    log = _prepare_session_log(cfg, "verify-static", args.service, args.session_log)
    write_session_event(f"=== verify-static {args.service} @ {time.strftime('%Y%m%d-%H%M%S')} ===")
    checks = static.run_static(args.service, cfg)
    write_session_event(f"[verify-static] summary: {_summarize(checks)}")
    return _emit(service=args.service, stage="verify-static", checks=checks, session_log=log)


def _cmd_apply(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        return _config_error(str(e))
    log = _prepare_session_log(cfg, "apply", args.service, args.session_log)
    write_session_event(f"=== apply {args.service} @ {time.strftime('%Y%m%d-%H%M%S')} ===")
    checks = runtime.apply(args.service, cfg)
    write_session_event(f"[apply] summary: {_summarize(checks)}")
    return _emit(service=args.service, stage="apply", checks=checks, session_log=log)


def _cmd_verify_runtime(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        return _config_error(str(e))
    log = _prepare_session_log(cfg, "verify-runtime", args.service, args.session_log)
    write_session_event(f"=== verify-runtime {args.service} @ {time.strftime('%Y%m%d-%H%M%S')} ===")
    checks = runtime.verify_runtime(
        args.service, cfg, phase=args.phase, sub_goal=args.sub_goal,
    )
    write_session_event(f"[verify-runtime] summary: {_summarize(checks)}")
    return _emit(
        service=args.service, stage="verify-runtime", checks=checks, session_log=log,
    )


def _cmd_session_path(args: argparse.Namespace) -> int:
    """Print a canonical per-deploy session log path. Does not create the file."""
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        return _config_error(str(e))
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = Path(cfg.logging.dir) / f"{ts}-{args.service}.log"
    sys.stdout.write(str(path) + "\n")
    sys.stdout.flush()
    return 0


def _cmd_session_event(args: argparse.Namespace) -> int:
    """Append a single free-form line to ``--session-log``.

    The orchestrator uses this for audit-trail events (e.g. retry counter,
    approvals granted) that are not subprocess output. This exists so the
    orchestrator does not need ``echo``/``printf`` Bash permissions.
    """
    os.environ["HARNESS_SESSION_LOG"] = str(Path(args.session_log))
    write_session_event(args.message)
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    from harness import init as init_mod  # lazy: stdlib-only
    try:
        init_mod.run_init(
            dest=Path(args.dest).resolve(),
            project_name=args.name,
            workspace_dir=args.workspace,
            force=args.force,
        )
    except init_mod.InitError as e:
        json.dump({"error": "init", "message": str(e)}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 2
    return 0


# ─── argparse setup ──────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m harness",
        description="Kubeharness: deterministic k8s verify/apply CLI.",
    )
    p.add_argument(
        "--config",
        help="Path to config/harness.yaml (default: ./config/harness.yaml).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Scaffold templates into a consumer project.")
    init.add_argument("--dest", default=".", help="Destination directory (default: .)")
    init.add_argument("--name", default=None, help="Project name (default: basename of dest)")
    init.add_argument("--workspace", default="workspace",
                      help="Workspace directory name (default: workspace)")
    init.add_argument("--force", action="store_true",
                      help="Overwrite existing files")
    init.set_defaults(func=_cmd_init)

    session_log_help = (
        "Append to this session log path instead of auto-generating one. "
        "Overrides $HARNESS_SESSION_LOG. Used by deploy-orchestrator to share "
        "one log across verify-static → apply → verify-runtime."
    )

    vs = sub.add_parser("verify-static", help="Run pre-deploy static checks.")
    vs.add_argument("--service", required=True)
    vs.add_argument("--session-log", dest="session_log", default=None, help=session_log_help)
    vs.set_defaults(func=_cmd_verify_static)

    ap = sub.add_parser("apply", help="Build/push docker image and/or run helm upgrade.")
    ap.add_argument("--service", required=True)
    ap.add_argument("--session-log", dest="session_log", default=None, help=session_log_help)
    ap.set_defaults(func=_cmd_apply)

    vr = sub.add_parser("verify-runtime", help="Post-deploy verification (kubectl wait + smoke).")
    vr.add_argument("--service", required=True)
    vr.add_argument("--phase", default=None, help="Phase name (needed for smoke test path).")
    vr.add_argument("--sub-goal", dest="sub_goal", default=None,
                    help="Sub-goal name (needed for smoke test path).")
    vr.add_argument("--session-log", dest="session_log", default=None, help=session_log_help)
    vr.set_defaults(func=_cmd_verify_runtime)

    sp = sub.add_parser(
        "session-path",
        help="Print a canonical session log path for the orchestrator.",
    )
    sp.add_argument("--service", required=True)
    sp.set_defaults(func=_cmd_session_path)

    se = sub.add_parser(
        "session-event",
        help="Append a free-form line to a session log (used by the orchestrator).",
    )
    se.add_argument("--session-log", dest="session_log", required=True)
    se.add_argument("--message", required=True)
    se.set_defaults(func=_cmd_session_event)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
