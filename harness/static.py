"""Pre-deploy static checks. No LLM, no cluster access.

Public API:

    run_static(service, cfg) -> list[CheckResult]

Checks are selected by artifact detection:

- chart_path exists   → helm group
- docker_path exists  → docker group

If neither, a single ``artifact_detection`` fail is returned.

Individual checks are gated by ``cfg.checks.static.is_enabled(name)``. Each
check shells out via :mod:`harness.shell`, so output is captured into the
active session log automatically (refactor.md §11.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from harness import shell
from harness.config import Config, ResolvedService

Status = Literal["pass", "fail", "skip"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str | None = None
    log_tail: str | None = None


# ─── helpers ─────────────────────────────────────────────────────────────────


def _tail(raw: str, n: int) -> str | None:
    if not raw:
        return None
    return raw[-n:] if len(raw) > n else raw


def _one_line(raw: str, limit: int = 240) -> str:
    for line in (raw or "").splitlines():
        s = line.strip()
        if s:
            return s[:limit]
    return "OK"


def _missing_cli(result: shell.RunResult) -> bool:
    return result.exit_code == -1 and "command not found" in result.stderr


def _from_result(name: str, r: shell.RunResult, tail_chars: int) -> CheckResult:
    if _missing_cli(r):
        return CheckResult(name=name, status="skip", detail=f"{name} CLI not installed")
    if r.ok:
        return CheckResult(name=name, status="pass")
    combined = (r.stdout or "") + (r.stderr or "")
    return CheckResult(
        name=name,
        status="fail",
        detail=_one_line(r.stderr or r.stdout or ""),
        log_tail=_tail(combined, tail_chars),
    )


# ─── individual checks ──────────────────────────────────────────────────────


def _yamllint_targets(chart_path: Path) -> list[Path]:
    """Collect *.yaml under chart, excluding templates/ (Go template syntax isn't valid YAML)."""
    out: list[Path] = []
    for f in chart_path.rglob("*.yaml"):
        if "templates" in f.relative_to(chart_path).parts:
            continue
        out.append(f)
    return sorted(out)


def check_yamllint(rs: ResolvedService, cfg: Config) -> CheckResult:
    targets = _yamllint_targets(rs.chart_path)
    if not targets:
        return CheckResult(name="yamllint", status="skip", detail="no YAML files under chart")
    r = shell.run(
        ["yamllint", "-d", "relaxed", "-f", "parsable", *[str(t) for t in targets]],
        label="static/yamllint",
    )
    return _from_result("yamllint", r, cfg.logging.tail_chars)


def _values_args(rs: ResolvedService) -> list[str]:
    args: list[str] = []
    for vf in rs.values_files():
        full = rs.chart_path / vf
        if full.exists():
            args += ["-f", str(full)]
    return args


def check_helm_lint(rs: ResolvedService, cfg: Config) -> CheckResult:
    cmd = ["helm", "lint", str(rs.chart_path), *_values_args(rs)]
    r = shell.run(cmd, label="static/helm_lint")
    return _from_result("helm_lint", r, cfg.logging.tail_chars)


def check_kubeconform(rs: ResolvedService, cfg: Config) -> CheckResult:
    helm_cmd = [
        "helm", "template", rs.release_name, str(rs.chart_path),
        "-n", rs.namespace,
        *rs.post_renderer_args(),
        *_values_args(rs),
    ]
    kcf_cmd = ["kubeconform", "-strict", "-summary", "-ignore-missing-schemas", "-"]
    r = shell.pipe(helm_cmd, kcf_cmd, label="static/kubeconform")
    return _from_result("kubeconform", r, cfg.logging.tail_chars)


# Collapse trivy's verbose table output (per-finding description + code snippet)
# into one line per misconfiguration: "<id> <severity> <file>:<start>-<end>  <message>".
# The message is finding-specific ("Container 'manager' ... should set ..."), so it
# stays actionable; the rule id reconstructs the AVD url if more detail is needed.
_TRIVY_JQ = (
    r'.Results[]? | .Target as $t | .Misconfigurations[]? '
    r'| "\(.ID) \(.Severity) \($t):\(.CauseMetadata.StartLine)-\(.CauseMetadata.EndLine)  \(.Message)"'
)


def check_trivy_config(rs: ResolvedService, cfg: Config) -> CheckResult:
    r = shell.pipe(
        ["trivy", "config", "--quiet", "--skip-version-check",
         "--format", "json", "--exit-code", "1", str(rs.chart_path)],
        ["jq", "-r", _TRIVY_JQ],
        label="static/trivy_config",
    )
    return _from_result("trivy_config", r, cfg.logging.tail_chars)


def check_gitleaks(rs: ResolvedService, cfg: Config) -> CheckResult:
    r = shell.run(
        ["gitleaks", "detect", "--source", str(rs.chart_path), "--no-git", "--exit-code", "1"],
        label="static/gitleaks",
    )
    return _from_result("gitleaks", r, cfg.logging.tail_chars)


def check_helm_dry_run_server(rs: ResolvedService, cfg: Config) -> CheckResult:
    cmd = [
        "helm", "upgrade", "--install", rs.release_name, str(rs.chart_path),
        "-n", rs.namespace,
        "--dry-run=server",
        *rs.post_renderer_args(),
        *_values_args(rs),
    ]
    r = shell.run(cmd, label="static/helm_dry_run_server")
    if _missing_cli(r):
        return CheckResult(name="helm_dry_run_server", status="skip", detail="helm not installed")
    if r.ok:
        return CheckResult(name="helm_dry_run_server", status="pass")
    combined = (r.stdout or "") + (r.stderr or "")
    return CheckResult(
        name="helm_dry_run_server",
        status="fail",
        detail=_one_line(r.stderr or r.stdout or ""),
        log_tail=_tail(combined, cfg.logging.tail_chars),
    )


def check_hadolint(rs: ResolvedService, cfg: Config) -> CheckResult:
    dockerfile = rs.docker_path / "Dockerfile"
    if not dockerfile.exists():
        return CheckResult(
            name="hadolint",
            status="fail",
            detail=f"Dockerfile not found at {dockerfile}",
        )
    r = shell.run(["hadolint", str(dockerfile)], label="static/hadolint")
    return _from_result("hadolint", r, cfg.logging.tail_chars)


def check_gitleaks_docker(rs: ResolvedService, cfg: Config) -> CheckResult:
    r = shell.run(
        ["gitleaks", "detect", "--source", str(rs.docker_path), "--no-git", "--exit-code", "1"],
        label="static/gitleaks_docker",
    )
    return _from_result("gitleaks_docker", r, cfg.logging.tail_chars)


# ─── registry + runner ──────────────────────────────────────────────────────


CheckFn = Callable[[ResolvedService, Config], CheckResult]

HELM_CHECKS: dict[str, CheckFn] = {
    "yamllint": check_yamllint,
    "helm_lint": check_helm_lint,
    "kubeconform": check_kubeconform,
    "trivy_config": check_trivy_config,
    "gitleaks": check_gitleaks,
    "helm_dry_run_server": check_helm_dry_run_server,
}

DOCKER_CHECKS: dict[str, CheckFn] = {
    "hadolint": check_hadolint,
    "gitleaks_docker": check_gitleaks_docker,
}


def run_static(service: str, cfg: Config) -> list[CheckResult]:
    """Run all enabled static checks for ``service``, detection-gated."""
    rs = cfg.resolve(service)
    has_helm = rs.chart_path.is_dir()
    has_docker = (rs.docker_path / "Dockerfile").exists()

    if not has_helm and not has_docker:
        return [CheckResult(
            name="artifact_detection",
            status="fail",
            detail=(
                f"no chart at {rs.chart_path} and no Dockerfile at "
                f"{rs.docker_path}/Dockerfile"
            ),
        )]

    results: list[CheckResult] = []
    if has_helm:
        for name, fn in HELM_CHECKS.items():
            if not cfg.checks.static.is_enabled(name):
                results.append(CheckResult(name=name, status="skip", detail="disabled in config"))
                continue
            results.append(fn(rs, cfg))
    if has_docker:
        for name, fn in DOCKER_CHECKS.items():
            if not cfg.checks.static.is_enabled(name):
                results.append(CheckResult(name=name, status="skip", detail="disabled in config"))
                continue
            results.append(fn(rs, cfg))
    return results
