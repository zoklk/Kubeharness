"""Configuration loader for ``config/harness.yaml``.

The consumer project ships a ``config/harness.yaml`` at its CWD. This module
parses it into typed dataclasses and exposes a resolver API so call sites never
need to know the raw schema layout.

Public API (see refactor.md §9):

    cfg = load_config()
    cfg.cluster.namespace
    cfg.conventions.workspace_dir
    cfg.active_env
    cfg.env("dev").domain_suffix
    cfg.env("dev").node_selectors["storage"]
    cfg.checks.static.enabled_names
    cfg.checks.runtime.kubectl_wait.initial_wait_seconds

    rs = cfg.resolve("prometheus")
    rs.release_name      # "prometheus-dev-v1"
    rs.chart_path        # Path("workspace/helm/prometheus")
    rs.docker_path       # Path("workspace/docker/prometheus")
    rs.values_files()    # [Path("values.yaml"), Path("values-dev.yaml")]

    cfg.smoke_test_path("prometheus", phase="observability", sub_goal="prometheus")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH_DEFAULT = Path("config/harness.yaml")


class ConfigError(ValueError):
    """Raised when ``config/harness.yaml`` is missing or malformed."""


# ─── dataclasses ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Cluster:
    namespace: str
    kubeconfig: str | None = None


@dataclass(frozen=True)
class Conventions:
    workspace_dir: str
    chart_path: str
    docker_path: str
    smoke_test_path: str
    release_name: str
    label_selector: str
    values_files: tuple[str, ...]
    write_allowed_globs: tuple[str, ...]
    write_denied_globs: tuple[str, ...]
    registry: str
    image_tag: str


@dataclass(frozen=True)
class Environment:
    name: str
    domain_suffix: str
    arch: str
    node_selectors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StaticChecks:
    _enabled: dict[str, bool]

    @property
    def enabled_names(self) -> list[str]:
        return [name for name, v in self._enabled.items() if v]

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)


@dataclass(frozen=True)
class KubectlWaitCheck:
    enabled: bool
    initial_wait_seconds: int
    terminal_grace_seconds: int


@dataclass(frozen=True)
class RuntimeChecks:
    docker_build_push: bool
    helm_upgrade: bool
    kubectl_wait: KubectlWaitCheck
    smoke_test: bool


@dataclass(frozen=True)
class Checks:
    static: StaticChecks
    runtime: RuntimeChecks


@dataclass(frozen=True)
class Logging:
    dir: str
    tail_chars: int
    retention_days: int


@dataclass(frozen=True)
class Orchestration:
    max_runtime_retries: int


@dataclass(frozen=True)
class ResolvedService:
    """Service-bound view of ``conventions``. All patterns expanded."""

    service: str
    namespace: str
    release_name: str
    label_selector: str
    chart_path: Path
    docker_path: Path
    registry: str
    image_tag: str
    _values_file_patterns: tuple[str, ...]
    _active_env: str

    def values_files(self) -> list[Path]:
        """Return chart-relative value file paths (caller joins with chart_path)."""
        return [
            Path(pat.format(active_env=self._active_env))
            for pat in self._values_file_patterns
        ]


@dataclass(frozen=True)
class Config:
    cluster: Cluster
    conventions: Conventions
    active_env: str
    environments: dict[str, Environment]
    checks: Checks
    logging: Logging
    orchestration: Orchestration
    _source: Path

    # ── helpers ─────────────────────────────────────────────────────────────

    def env(self, name: str) -> Environment:
        try:
            return self.environments[name]
        except KeyError:
            raise ConfigError(f"unknown environment: {name!r}") from None

    def active_environment(self) -> Environment:
        return self.env(self.active_env)

    def resolve(self, service: str) -> ResolvedService:
        c = self.conventions
        subs = {
            "workspace": c.workspace_dir,
            "service": service,
            "active_env": self.active_env,
        }
        return ResolvedService(
            service=service,
            namespace=self.cluster.namespace,
            release_name=c.release_name.format(**subs),
            label_selector=c.label_selector.format(**subs),
            chart_path=Path(c.chart_path.format(**subs)),
            docker_path=Path(c.docker_path.format(**subs)),
            registry=c.registry,
            image_tag=c.image_tag,
            _values_file_patterns=c.values_files,
            _active_env=self.active_env,
        )

    def smoke_test_path(self, service: str, phase: str, sub_goal: str) -> Path:
        c = self.conventions
        return Path(c.smoke_test_path.format(
            workspace=c.workspace_dir,
            service=service,
            phase=phase,
            sub_goal=sub_goal,
        ))


# ─── parsing ─────────────────────────────────────────────────────────────────


def _require_dict(obj: Any, key: str) -> dict:
    if not isinstance(obj, dict):
        raise ConfigError(f"{key}: expected mapping, got {type(obj).__name__}")
    return obj


def _parse(raw: dict, source: Path) -> Config:
    cluster_raw = _require_dict(raw.get("cluster", {}), "cluster")
    cluster = Cluster(
        namespace=str(cluster_raw.get("namespace") or "default"),
        kubeconfig=cluster_raw.get("kubeconfig"),
    )

    conv_raw = _require_dict(raw.get("conventions", {}), "conventions")
    conv = Conventions(
        workspace_dir=str(conv_raw.get("workspace_dir", "workspace")),
        chart_path=str(conv_raw.get("chart_path", "{workspace}/helm/{service}")),
        docker_path=str(conv_raw.get("docker_path", "{workspace}/docker/{service}")),
        smoke_test_path=str(conv_raw.get(
            "smoke_test_path",
            "{workspace}/tests/{phase}/smoke-test-{sub_goal}.sh",
        )),
        release_name=str(conv_raw.get("release_name", "{service}-{active_env}-v1")),
        label_selector=str(conv_raw.get("label_selector", "app.kubernetes.io/name={service}")),
        values_files=tuple(conv_raw.get("values_files") or ["values.yaml", "values-{active_env}.yaml"]),
        write_allowed_globs=tuple(conv_raw.get("write_allowed_globs") or [
            "{workspace}/helm/**",
            "{workspace}/docker/**",
        ]),
        write_denied_globs=tuple(conv_raw.get("write_denied_globs") or [
            "{workspace}/tests/**",
        ]),
        registry=str(conv_raw.get("registry", "")),
        image_tag=str(conv_raw.get("image_tag", "dev")),
    )

    env_raw = _require_dict(raw.get("environments", {}), "environments")
    active_env = str(env_raw.get("active", "dev"))
    environments: dict[str, Environment] = {}
    for name, body in env_raw.items():
        if name == "active":
            continue
        body = _require_dict(body, f"environments.{name}")
        environments[name] = Environment(
            name=name,
            domain_suffix=str(body.get("domain_suffix", "cluster.local")),
            arch=str(body.get("arch", "amd64")),
            node_selectors=dict(body.get("node_selectors") or {}),
        )
    if active_env not in environments:
        raise ConfigError(
            f"environments.active={active_env!r} but no such environment defined"
        )

    checks_raw = _require_dict(raw.get("checks", {}), "checks")
    static_raw = _require_dict(checks_raw.get("static", {}), "checks.static")
    static_enabled: dict[str, bool] = {}
    for name, body in static_raw.items():
        body = body if isinstance(body, dict) else {"enabled": bool(body)}
        static_enabled[name] = bool(body.get("enabled", True))
    static = StaticChecks(_enabled=static_enabled)

    runtime_raw = _require_dict(checks_raw.get("runtime", {}), "checks.runtime")
    kw_raw = _require_dict(runtime_raw.get("kubectl_wait", {}), "checks.runtime.kubectl_wait")
    kubectl_wait = KubectlWaitCheck(
        enabled=bool(kw_raw.get("enabled", True)),
        initial_wait_seconds=int(kw_raw.get("initial_wait_seconds", 60)),
        terminal_grace_seconds=int(kw_raw.get("terminal_grace_seconds", 240)),
    )
    runtime = RuntimeChecks(
        docker_build_push=bool(_require_dict(
            runtime_raw.get("docker_build_push", {"enabled": True}),
            "checks.runtime.docker_build_push",
        ).get("enabled", True)),
        helm_upgrade=bool(_require_dict(
            runtime_raw.get("helm_upgrade", {"enabled": True}),
            "checks.runtime.helm_upgrade",
        ).get("enabled", True)),
        kubectl_wait=kubectl_wait,
        smoke_test=bool(_require_dict(
            runtime_raw.get("smoke_test", {"enabled": True}),
            "checks.runtime.smoke_test",
        ).get("enabled", True)),
    )

    log_raw = _require_dict(raw.get("logging", {}), "logging")
    logging_cfg = Logging(
        dir=str(log_raw.get("dir", "logs/deploy")),
        tail_chars=int(log_raw.get("tail_chars", 2000)),
        retention_days=int(log_raw.get("retention_days", 30)),
    )

    orch_raw = _require_dict(raw.get("orchestration", {}), "orchestration")
    orchestration = Orchestration(
        max_runtime_retries=int(orch_raw.get("max_runtime_retries", 3)),
    )

    return Config(
        cluster=cluster,
        conventions=conv,
        active_env=active_env,
        environments=environments,
        checks=Checks(static=static, runtime=runtime),
        logging=logging_cfg,
        orchestration=orchestration,
        _source=source,
    )


def _resolve_path(explicit: Path | str | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get("HARNESS_CONFIG")
    if override:
        return Path(override)
    return CONFIG_PATH_DEFAULT


@lru_cache(maxsize=1)
def _load_cached(source: Path) -> Config:
    if not source.exists():
        raise ConfigError(f"config not found: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"failed to parse {source}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: top-level must be a mapping")
    return _parse(raw, source)


def load_config(path: Path | str | None = None) -> Config:
    """Load ``config/harness.yaml`` from CWD (or explicit/env-overridden path).

    Cached via ``@lru_cache``. Call :func:`load_config.cache_clear` in tests
    when swapping the config file.
    """
    return _load_cached(_resolve_path(path).resolve())


# expose cache_clear for tests
load_config.cache_clear = _load_cached.cache_clear  # type: ignore[attr-defined]
