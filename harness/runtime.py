"""Deploy (``apply``) + post-deploy verification (``verify_runtime``).

Two public entry points, both detection-gated on ``chart_path`` / ``docker_path``:

    apply(service, cfg)          # docker build+push → helm uninstall/upgrade
    verify_runtime(service, cfg, phase=...)
                                 # helm template workload detection →
                                 # kubectl wait 2-stage → smoke test

Preserved from the legacy ``runtime_gates.py`` (refactor.md §21):

- ``helm uninstall`` before ``helm upgrade --install`` (immutable field workaround)
- ``kubectl wait`` 2-stage: ``initial_wait_seconds`` probe → terminal-state detection
  → ``terminal_grace_seconds`` grace window
- CRD-only and batch-only (Job/CronJob) charts skip the pod readiness wait
  (parse ``helm template`` stdout for workload kinds)
- Smoke test env injection (SERVICE, NAMESPACE, RELEASE_NAME, ACTIVE_ENV, DOMAIN_SUFFIX)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import yaml

from harness import shell
from harness.config import Config, ResolvedService
from harness.static import CheckResult, _one_line, _tail


# pod container state reasons that mean "won't recover by waiting"
_TERMINAL_STATES = frozenset({
    "CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
    "Error", "OOMKilled", "InvalidImageName", "CreateContainerConfigError",
})

# workload kinds whose pods reach a steady-state ``Ready`` condition — these are
# the only ones ``kubectl wait --for=condition=Ready`` can meaningfully wait on
_LONGRUNNING_WORKLOAD_KINDS = frozenset({
    "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Pod",
})
# one-shot / scheduled workloads — their pods never become ``Ready`` (they end
# ``Succeeded``, or are deleted by a Helm hook delete-policy)
_BATCH_WORKLOAD_KINDS = frozenset({"Job", "CronJob"})
# any workload kind; a chart rendering none of these is CRD-only
_WORKLOAD_KINDS = _LONGRUNNING_WORKLOAD_KINDS | _BATCH_WORKLOAD_KINDS


# ─── helpers ─────────────────────────────────────────────────────────────────


def _result_from(name: str, r: shell.RunResult, cfg: Config) -> CheckResult:
    if r.ok:
        return CheckResult(name=name, status="pass")
    combined = (r.stdout or "") + (r.stderr or "")
    return CheckResult(
        name=name,
        status="fail",
        detail=_one_line(r.stderr or r.stdout or ""),
        log_tail=_tail(combined, cfg.logging.tail_chars),
    )


def _skip(name: str, detail: str = "prior step failed") -> CheckResult:
    return CheckResult(name=name, status="skip", detail=detail)


def _values_args(rs: ResolvedService) -> list[str]:
    args: list[str] = []
    for vf in rs.values_files():
        full = rs.chart_path / vf
        if full.exists():
            args += ["-f", str(full)]
    return args


# ─── docker ──────────────────────────────────────────────────────────────────


def _docker_image(rs: ResolvedService) -> str:
    registry = rs.registry.rstrip("/")
    prefix = f"{registry}/" if registry else ""
    return f"{prefix}{rs.service}:{rs.image_tag}"


def _docker_apply(rs: ResolvedService, cfg: Config) -> list[CheckResult]:
    if not (rs.docker_path / "Dockerfile").exists():
        return []
    if not rs.registry:
        return [
            CheckResult(
                name="docker_build",
                status="fail",
                detail="config/harness.yaml: conventions.registry is empty "
                       "(multi-arch buildx pushes the manifest list straight to a registry)",
            ),
        ]
    image = _docker_image(rs)
    platforms = ",".join(rs.build_platforms)

    # One `docker buildx build --push`: a multi-platform image is a manifest
    # list and cannot be `docker load`-ed locally, so build and push are a
    # single step. Requires a `docker-container` buildx builder (+ binfmt/QEMU
    # for foreign arches) — see README "사전 준비".
    build = shell.run(
        [
            "docker", "buildx", "build",
            "--platform", platforms,
            "-t", image,
            "--push",
            str(rs.docker_path),
        ],
        label="apply/docker_build",
    )
    return [_result_from("docker_build", build, cfg)]


# ─── helm ────────────────────────────────────────────────────────────────────


def _helm_release_exists(rs: ResolvedService) -> bool:
    r = shell.run(
        ["helm", "status", rs.release_name, "-n", rs.namespace],
        label="apply/helm_status",
    )
    return r.ok


def _helm_apply(rs: ResolvedService, cfg: Config) -> list[CheckResult]:
    if not rs.chart_path.is_dir():
        return []

    results: list[CheckResult] = []
    if _helm_release_exists(rs):
        uninst = shell.run(
            ["helm", "uninstall", rs.release_name, "-n", rs.namespace],
            label="apply/helm_uninstall",
        )
        results.append(_result_from("helm_uninstall", uninst, cfg))
        if results[-1].status == "fail":
            results.append(_skip("helm_install"))
            return results

    upgrade_cmd = [
        "helm", "upgrade", "--install", rs.release_name, str(rs.chart_path),
        "-n", rs.namespace,
        "--create-namespace",
        "--timeout", "180s",
        *_values_args(rs),
    ]
    up = shell.run(upgrade_cmd, label="apply/helm_install")
    results.append(_result_from("helm_install", up, cfg))
    return results


# ─── apply ───────────────────────────────────────────────────────────────────


def apply(service: str, cfg: Config) -> list[CheckResult]:
    """Deploy ``service``: docker build+push (if Dockerfile) → helm upgrade.

    Stops on first fail and marks subsequent steps skipped.
    """
    rs = cfg.resolve(service)
    has_helm = rs.chart_path.is_dir()
    has_docker = (rs.docker_path / "Dockerfile").exists()

    if not has_helm and not has_docker:
        return [CheckResult(
            name="artifact_detection",
            status="fail",
            detail=f"no chart at {rs.chart_path} and no Dockerfile at {rs.docker_path}/Dockerfile",
        )]

    results: list[CheckResult] = []
    if has_docker and cfg.checks.runtime.docker_build_push:
        results.extend(_docker_apply(rs, cfg))
        if any(r.status == "fail" for r in results):
            if has_helm:
                results.append(_skip("helm_install"))
            return results

    if has_helm and cfg.checks.runtime.helm_upgrade:
        results.extend(_helm_apply(rs, cfg))

    return results


# ─── verify_runtime ──────────────────────────────────────────────────────────


def _chart_workload_classes(rs: ResolvedService, cfg: Config) -> tuple[bool, bool]:
    """Parse ``helm template`` output → ``(has_longrunning, has_batch)``.

    ``has_longrunning`` is True if the chart renders any Deployment/StatefulSet/
    DaemonSet/ReplicaSet/Pod; ``has_batch`` if it renders any Job/CronJob.

    On template failure (network, missing deps, etc.) or unparseable YAML we
    default to ``(True, False)`` so kubectl wait still runs — a conservative
    choice that preserves behavior of the legacy ``runtime_gates.py``.
    """
    cmd = [
        "helm", "template", rs.release_name, str(rs.chart_path),
        "-n", rs.namespace,
        *_values_args(rs),
    ]
    r = shell.run(cmd, label="verify-runtime/helm_template", log_stdout=False)
    if not r.ok:
        return True, False
    longrunning = batch = False
    try:
        for doc in yaml.safe_load_all(r.stdout):
            if not isinstance(doc, dict):
                continue
            kind = doc.get("kind")
            if kind in _LONGRUNNING_WORKLOAD_KINDS:
                longrunning = True
            elif kind in _BATCH_WORKLOAD_KINDS:
                batch = True
    except yaml.YAMLError:
        return True, False
    return longrunning, batch


def _pods_sidecar_path() -> Path | None:
    sess = os.environ.get("HARNESS_SESSION_LOG")
    if not sess:
        return None
    stem = Path(sess).with_suffix("")
    return stem.with_name(f"{stem.name}-pods-{time.strftime('%H%M%S')}.json")


def _pods_summary(items: list) -> str:
    if not items:
        return "Pods (0): none found"
    parts: list[str] = []
    for pod in items:
        name = pod.get("metadata", {}).get("name", "?")
        phase = pod.get("status", {}).get("phase", "?")
        cstatuses = pod.get("status", {}).get("containerStatuses", []) or []
        ready = "Ready" if cstatuses and all(cs.get("ready", False) for cs in cstatuses) else "NotReady"
        parts.append(f"{name} {phase}/{ready}")
    return f"Pods ({len(items)}): " + ", ".join(parts)


def _detect_terminal_failure(
    rs: ResolvedService,
) -> tuple[bool, str]:
    """Return (is_terminal, detail). False means pods are still progressing."""
    r = shell.run(
        [
            "kubectl", "get", "pods",
            "-n", rs.namespace,
            "-l", rs.label_selector,
            "-o", "json",
        ],
        label="verify-runtime/kubectl_get_pods",
        log_stdout=False,
        stdout_sidecar=_pods_sidecar_path(),
    )
    if not r.ok:
        return False, ""
    try:
        items = json.loads(r.stdout).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        return False, ""
    shell.write_session_event(_pods_summary(items))
    parts: list[str] = []
    for pod in items:
        pname = pod.get("metadata", {}).get("name", "?")
        for cs in pod.get("status", {}).get("containerStatuses", []):
            waiting = cs.get("state", {}).get("waiting", {})
            wreason = waiting.get("reason", "")
            if wreason in _TERMINAL_STATES:
                msg = (waiting.get("message") or "")[:80]
                parts.append(f"{pname}: {wreason}" + (f" ({msg})" if msg else ""))
            terminated = cs.get("state", {}).get("terminated", {})
            treason = terminated.get("reason", "")
            if treason in _TERMINAL_STATES:
                parts.append(f"{pname}: terminated/{treason}")
    return bool(parts), "; ".join(parts[:3])


def _kubectl_wait(rs: ResolvedService, timeout_seconds: int) -> shell.RunResult:
    # NOTE: this waits on *all* pods matching ``label_selector``. A chart that
    # mixes a Deployment with a Job/CronJob (or a lingering Helm-hook Job pod)
    # under the same labels can stall here — batch pods never go ``Ready``. The
    # proper fix is to wait on workload resources (Deployment --for=
    # condition=Available) rather than pods; tracked as a follow-up refactor.
    return shell.run(
        [
            "kubectl", "wait", "pods",
            "--for=condition=Ready",
            "-n", rs.namespace,
            "-l", rs.label_selector,
            f"--timeout={timeout_seconds}s",
        ],
        timeout=timeout_seconds + 10,
        label="verify-runtime/kubectl_wait",
    )


def _kubectl_wait_staged(rs: ResolvedService, cfg: Config) -> CheckResult:
    kw = cfg.checks.runtime.kubectl_wait
    first = _kubectl_wait(rs, kw.initial_wait_seconds)
    if first.ok:
        return CheckResult(name="kubectl_wait", status="pass")

    is_terminal, detail = _detect_terminal_failure(rs)
    if is_terminal:
        combined = (first.stdout or "") + (first.stderr or "")
        return CheckResult(
            name="kubectl_wait",
            status="fail",
            detail=f"early exit: {detail or _one_line(first.stderr or first.stdout or '')}",
            log_tail=_tail(combined, cfg.logging.tail_chars),
        )
    second = _kubectl_wait(rs, kw.terminal_grace_seconds)
    return _result_from("kubectl_wait", second, cfg)


def _smoke_env(rs: ResolvedService, cfg: Config) -> dict[str, str]:
    active = cfg.active_environment()
    return {
        "SERVICE": rs.service,
        "NAMESPACE": rs.namespace,
        "RELEASE_NAME": rs.release_name,
        "ACTIVE_ENV": cfg.active_env,
        "DOMAIN_SUFFIX": active.domain_suffix,
    }


def _smoke_test(
    rs: ResolvedService,
    cfg: Config,
    path: Path,
) -> CheckResult:
    if not path.exists():
        return CheckResult(
            name="smoke_test",
            status="skip",
            detail=f"no smoke test at {path}",
        )
    r = shell.run(
        ["bash", str(path)],
        env=_smoke_env(rs, cfg),
        label="verify-runtime/smoke_test",
    )
    return _result_from("smoke_test", r, cfg)


def verify_runtime(
    service: str,
    cfg: Config,
    *,
    phase: str | None = None,
) -> list[CheckResult]:
    """Post-deploy verification: kubectl wait (2-stage) + smoke test.

    ``phase`` selects the smoke test file (combined with ``service``).
    If missing, smoke test is skipped.

    ``kubectl wait`` is skipped for CRD-only charts and for batch-only charts
    (only Job/CronJob workloads) — neither has steady-state pods to wait on —
    while the smoke test still runs.
    """
    rs = cfg.resolve(service)
    has_helm = rs.chart_path.is_dir()
    has_docker = (rs.docker_path / "Dockerfile").exists()

    if not has_helm and not has_docker:
        return [CheckResult(
            name="artifact_detection",
            status="fail",
            detail=f"no chart at {rs.chart_path} and no Dockerfile at {rs.docker_path}/Dockerfile",
        )]

    results: list[CheckResult] = []
    smoke_allowed = True
    if has_helm:
        if not cfg.checks.runtime.kubectl_wait.enabled:
            results.append(_skip("kubectl_wait", "disabled in config"))
        else:
            has_longrunning, has_batch = _chart_workload_classes(rs, cfg)
            if not has_longrunning and not has_batch:
                results.append(CheckResult(
                    name="kubectl_wait",
                    status="skip",
                    detail="CRD-only chart: no workload resources",
                ))
            elif not has_longrunning:  # only Job/CronJob workloads
                results.append(CheckResult(
                    name="kubectl_wait",
                    status="skip",
                    detail="batch-only chart (Job/CronJob): no steady-state pods to wait on",
                ))
            else:
                kw = _kubectl_wait_staged(rs, cfg)
                results.append(kw)
                if kw.status == "fail":
                    smoke_allowed = False

    if not cfg.checks.runtime.smoke_test:
        results.append(_skip("smoke_test", "disabled in config"))
    elif not has_helm:
        results.append(_skip("smoke_test", "no chart — smoke test requires a cluster-side service"))
    elif not smoke_allowed:
        results.append(_skip("smoke_test"))
    elif phase is None:
        results.append(_skip(
            "smoke_test",
            "smoke test requires --phase",
        ))
    else:
        path = cfg.smoke_test_path(service, phase)
        results.append(_smoke_test(rs, cfg, path))

    return results
