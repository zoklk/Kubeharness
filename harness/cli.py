"""Kubeharness CLI — ``python -m harness <subcommand>``.

Four subcommands (refactor.md §10.4):

- ``init``            — scaffold templates/ into a consumer project
- ``verify-static``   — pre-deploy checks (yamllint, helm lint, kubeconform, ...)
- ``apply``           — docker build+push and/or helm upgrade
- ``verify-runtime``  — kubectl wait + smoke test

All three verification subcommands emit a single JSON object on stdout
(refactor.md §11.1) and exit with:

- ``0``  everything passed
- ``1``  at least one check failed
- ``2``  configuration / environment error (bad YAML, missing CLI, etc.)

``HARNESS_SESSION_LOG``: if set, every external command appends to that file;
the JSON response echoes the path under ``session_log``. If unset, a default
per-stage log is created under ``logging.dir`` and set in the child processes'
environment before delegating so the whole stage lands in one file.
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
    return all(c.status != "fail" for c in checks) and any(
        c.status == "pass" for c in checks
    )


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


def _prepare_session_log(cfg: Config, stage: str, service: str) -> Path:
    """Return the session log path, creating a default one if caller didn't set it."""
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
    log = _prepare_session_log(cfg, "verify-static", args.service)
    write_session_event(f"=== verify-static {args.service} @ {time.strftime('%Y%m%d-%H%M%S')} ===")
    checks = static.run_static(args.service, cfg)
    write_session_event(f"[verify-static] summary: {_summarize(checks)}")
    return _emit(service=args.service, stage="verify-static", checks=checks, session_log=log)


def _cmd_apply(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        return _config_error(str(e))
    log = _prepare_session_log(cfg, "apply", args.service)
    write_session_event(f"=== apply {args.service} @ {time.strftime('%Y%m%d-%H%M%S')} ===")
    checks = runtime.apply(args.service, cfg)
    write_session_event(f"[apply] summary: {_summarize(checks)}")
    return _emit(service=args.service, stage="apply", checks=checks, session_log=log)


def _cmd_verify_runtime(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        return _config_error(str(e))
    log = _prepare_session_log(cfg, "verify-runtime", args.service)
    write_session_event(f"=== verify-runtime {args.service} @ {time.strftime('%Y%m%d-%H%M%S')} ===")
    checks = runtime.verify_runtime(
        args.service, cfg, phase=args.phase, sub_goal=args.sub_goal,
    )
    write_session_event(f"[verify-runtime] summary: {_summarize(checks)}")
    return _emit(
        service=args.service, stage="verify-runtime", checks=checks, session_log=log,
    )


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

    vs = sub.add_parser("verify-static", help="Run pre-deploy static checks.")
    vs.add_argument("--service", required=True)
    vs.set_defaults(func=_cmd_verify_static)

    ap = sub.add_parser("apply", help="Build/push docker image and/or run helm upgrade.")
    ap.add_argument("--service", required=True)
    ap.set_defaults(func=_cmd_apply)

    vr = sub.add_parser("verify-runtime", help="Post-deploy verification (kubectl wait + smoke).")
    vr.add_argument("--service", required=True)
    vr.add_argument("--phase", default=None, help="Phase name (needed for smoke test path).")
    vr.add_argument("--sub-goal", dest="sub_goal", default=None,
                    help="Sub-goal name (needed for smoke test path).")
    vr.set_defaults(func=_cmd_verify_runtime)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
