# Kubeharness — Design

Internal architecture of the `kubeharness` package. Reading order: goals
→ responsibilities → data flow → extension points. For day-to-day
usage, see the top-level `README.md`.

This document replaces the legacy `context/docs/harness_internals.md`
which described the pre-refactor LangGraph harness.

---

## 1. Design goals

1. **Deterministic CLI, no LLM inside.** The harness owns verifiable
   side effects (run a check, run a deploy, wait for a pod). All
   reasoning happens in an external agent CLI (Claude Code, Codex).
2. **Pipeline-stage layout.** Files are grouped by *when* they run,
   not by *which tool* they wrap. Pre-deploy → `static.py`, deploy +
   post-deploy → `runtime.py`. Maintainers hunting for "the check
   that runs after deploy" open one file.
3. **Tool neutrality.** The Python CLI, `config/harness.yaml`,
   `AGENTS.md`, and the hook scripts work under any agent CLI.
   `.claude/` is a Claude-Code-specific wiring example shipped as
   templates; it's not a runtime dependency.
4. **No hardcoding of project values.** Namespace, release naming,
   chart paths, image tags, enabled checks, timeouts — every
   consumer-variable knob lives in `config/harness.yaml`. Code,
   skills, hooks, slash commands all read the YAML.
5. **One `/deploy` = one log file.** Every subprocess during a deploy
   cycle appends to a single `$HARNESS_SESSION_LOG`. Ad-hoc per-tool
   log files are anti-feature.
6. **Minimalism.** No empty scaffolding, no preemptive abstractions,
   no modules "just in case." Six Python files total, one per
   responsibility.

---

## 2. Module responsibilities

```
harness/
├── __init__.py
├── __main__.py     # thin entrypoint → cli.main
├── config.py       # YAML schema, resolve(service), env lookup, @lru_cache
├── shell.py        # the only subprocess.run; session-log append
├── static.py       # pre-deploy checks + registry + detection gate
├── runtime.py      # apply + verify_runtime (kubectl wait, smoke test)
├── cli.py          # argparse, JSON envelope, exit codes, subcommand dispatch
└── init.py         # template scaffolding, {{var}} substitution
```

Six modules. If a change doesn't fit one of them cleanly, the shape of
the change is probably wrong.

### `config.py`

Parses `config/harness.yaml` into typed dataclasses and exposes the
substitution primitives. Key API:

```python
cfg = load_config()                             # @lru_cache'd
cfg.resolve("prometheus").release_name          # "prometheus-dev-v1"
cfg.resolve("prometheus").chart_path            # Path("workspace/helm/prometheus")
cfg.resolve("prometheus").values_files()        # [Path("values.yaml"), Path("values-dev.yaml")]
cfg.env("dev").domain_suffix                    # from environments.dev
cfg.active_environment()                        # shortcut — env(cfg.active_env)
cfg.smoke_test_path("svc", "p1", "svc")         # Path("workspace/tests/p1/smoke-test-svc.sh")
cfg.checks.static.is_enabled("yamllint")        # bool
cfg.checks.runtime.kubectl_wait.initial_wait_seconds  # int
```

Substitution tokens resolved here: `{service}`, `{active_env}`,
`{workspace}`, `{phase}`, `{sub_goal}`.

Defaults (refactor.md §9): if the YAML omits `release_name`,
`image_tag`, `kubectl_wait.initial_wait_seconds`, etc., defaults fill
in. See `_parse` in `config.py`.

### `shell.py`

Single entry point for every subprocess. Everything else in the
package — static checks, helm calls, kubectl wait — goes through
`shell.run` or `shell.pipe`. The module owns:

- **timeouts** (default 120s, override per call)
- **session log append** — if `HARNESS_SESSION_LOG` is set, each
  invocation appends a block:
  ```
  --- [label] $ cmd ---
  <stdout>
  <stderr>
  [exit N] (duration: 1.23s)
  ```
- **missing-CLI handling** — returns `exit_code=-1` + stderr
  `"command not found: X"` instead of raising. Callers can classify
  as `skip` without exception handling.
- **PATH augmentation** — prepends `~/.local/bin` so tools installed
  via `pip install --user` or language-specific package managers are
  found.

`shell.pipe(a, b)` runs `a | b` with the same logging contract —
used by `helm template | kubeconform`.

### `static.py`

Eight individual checks, each a function `(ResolvedService, Config)
→ CheckResult`, plus a registry grouped by artifact kind:

```
HELM_CHECKS   = {yamllint, helm_lint, kubeconform, trivy_config,
                 gitleaks, helm_dry_run_server}
DOCKER_CHECKS = {hadolint, gitleaks_docker}
```

`run_static(service, cfg)` is the public entry:

1. Detect artifacts: `chart_path.is_dir()` and/or `docker_path/Dockerfile`.
2. No artifacts → single `artifact_detection` fail.
3. For each enabled check in the matching group, call the function.
4. Return `list[CheckResult]` — no raises, no short-circuit.

`CheckResult` is a frozen dataclass: `name`, `status` ∈ {pass, fail,
skip}, optional `detail` (one-line human summary), optional
`log_tail` (last N characters of combined stdout+stderr, N from
`logging.tail_chars`).

### `runtime.py`

Two public entry points, both detection-gated the same way as
`static.py`.

**`apply(service, cfg)`** — deploy flow:

1. If `docker/<svc>/Dockerfile` exists: `docker build --platform
   linux/{arch}` (arch from active env) → `docker push`. Stops on
   build failure; downstream stages become `skip`.
2. If `helm/<svc>/` exists:
   a. `helm status <release> -n <ns>` to detect an existing release.
   b. If it exists: `helm uninstall` first. This handles the common
      case where the previous chart had an immutable field (e.g.
      `spec.selector`) that `helm upgrade` refuses to change. A
      clean uninstall-then-install is the simplest deterministic fix.
   c. `helm upgrade --install --create-namespace --timeout 60s` with
      all `values.yaml` + `values-<active_env>.yaml` passed as `-f`.

No `--wait` on `helm upgrade`. Pod readiness is the job of
`verify_runtime`, which has richer signals.

**`verify_runtime(service, cfg, *, phase, sub_goal)`** — post-deploy:

1. **CRD-only chart detection.** Run `helm template` and parse the
   rendered YAML. If no document has `kind` in `{Deployment,
   StatefulSet, DaemonSet, ReplicaSet, Job, CronJob, Pod}` the
   chart is CRD-only and `kubectl_wait` is skipped. On template
   failure we default to "has workloads" — a conservative choice
   preserving legacy behavior.

2. **Staged kubectl wait.** Two-stage wait pattern:
   - first probe: `kubectl wait pods --for=condition=Ready
     --timeout=<initial_wait_seconds>s`
   - if it times out, check pod state via `kubectl get pods -o json`.
     Any container in a `_TERMINAL_STATES` reason (CrashLoopBackOff,
     ImagePullBackOff, Error, OOMKilled, …) → **early exit fail**.
     No point waiting further; the pod won't recover.
   - if pods are merely Pending/ContainerCreating, run a second
     `kubectl wait` with `terminal_grace_seconds` — gives slow
     pullers and complex initContainers more time.

   Default timings: 60s first probe, 240s grace (tuneable per
   project via `checks.runtime.kubectl_wait.*`).

3. **Smoke test.** If `cfg.smoke_test_path(service, phase, sub_goal)`
   exists, run it as `bash <path>` with these env vars:
   - `SERVICE`, `NAMESPACE`, `RELEASE_NAME`, `ACTIVE_ENV`, `DOMAIN_SUFFIX`

   Skipped if `phase` or `sub_goal` is missing (CLI flags not set) —
   the CLI can't guess which smoke test matches the service.

### `cli.py`

argparse with four subcommands: `init`, `verify-static`, `apply`,
`verify-runtime`. Each verification command:

- loads config (exit code `2` on `ConfigError`)
- sets up `$HARNESS_SESSION_LOG` if the caller hasn't
- writes a session-start event to the log
- calls into `static.run_static` / `runtime.apply` /
  `runtime.verify_runtime`
- emits a JSON envelope on stdout:
  ```json
  {
    "service": "...",
    "stage": "verify-static",
    "summary": "3 passed, 1 failed, 0 skipped",
    "passed": false,
    "session_log": "logs/deploy/....log",
    "checks": [{"name": "...", "status": "...", "detail": "...", "log_tail": "..."}]
  }
  ```
- exits `0` if all non-skipped checks passed, `1` if any failed, `2`
  for config errors.

The envelope is the sole communication channel with the calling
agent. Nothing important goes to stderr — everything lives in the
session log.

### `init.py`

Stdlib-only template copier. Walks `templates/` relative to the
installed package, copies files to `--dest`, and performs three
substitutions on any file whose name ends in `.tmpl`:

- `{{project_name}}` ← `--name` (or `basename(dest)`)
- `{{workspace_dir}}` ← `--workspace` (default `workspace`)
- `{{kubeharness_version}}` ← `importlib.metadata.version("kubeharness")`

After substitution the `.tmpl` suffix is stripped. Non-`.tmpl` files
are copied verbatim. Existing files are skipped unless `--force`.

Deliberately no Jinja2 / templating engine — the three variables
above are the entire surface area.

---

## 3. Data flow: one `/deploy` cycle

```
Main session / user
  │  runs /deploy <svc>
  ▼
deploy-orchestrator  (subagent — LLM lives here)
  │  sets HARNESS_SESSION_LOG = logs/deploy/<ts>-<svc>.log
  │
  ├─►  python -m harness verify-static --service <svc>  ──►  JSON to stdout
  │         └── shell.run writes subprocess output to the session log
  │
  ├─►  python -m harness apply --service <svc>          ──►  JSON to stdout
  │         └── docker build+push, helm uninstall+upgrade (if applicable)
  │
  ├─►  python -m harness verify-runtime --service <svc> --phase P --sub-goal G
  │         └── helm template parse, kubectl_wait_staged, smoke_test
  │
  │  on runtime fail:
  ├─►  Task(subagent_type="runtime-diagnoser", prompt=<summary + log path>)
  │         └── diagnoser reads the session log via Read, queries kagent,
  │             returns JSON with observations + proposed_files
  │
  │  on proposed_files non-empty:
  ├─►  Write(...) for each — triggers `ask` permission → human approval
  │
  │  retry_count += 1, loop from verify-static (max_runtime_retries)
  │
  ▼
return {passed, retries, session_log, last_stage, summary}
```

All steps append to the single session log; the CLI JSON carries a
pointer, never the bulk text.

---

## 4. Template substitution model

Two substitution passes with different timing:

- **Init-time (`{{var}}`).** Resolved once, by `harness/init.py`, when
  the consumer runs `python -m harness init`. Output is static in
  their repo. Files using this must end in `.tmpl`; the suffix is
  stripped after substitution. Used for project-identity values that
  never change after scaffold (`{{project_name}}`, `{{workspace_dir}}`).

- **Runtime (`{service}`, `{active_env}`, `{workspace}`, …).** Resolved
  every call by `harness/config.py` inside `resolve()`. These live in
  `config/harness.yaml` as literal strings. Changing them in the
  YAML takes effect on the next CLI invocation; no scaffold re-run
  needed.

Rule of thumb: if a value might change per deploy or per env, use the
runtime form. If it's baked into the project layout, use the init
form.

---

## 5. Extension points

### Adding a new static check

1. Write `check_<name>(rs, cfg) -> CheckResult` in `static.py`.
2. Add it to `HELM_CHECKS` or `DOCKER_CHECKS` (whichever artifact it
   consumes).
3. Add the enabled flag to `config/harness.yaml.example.tmpl`.
4. Add a test in `tests/test_static.py` using the `_ShellStub`
   pattern.

The check **must** go through `shell.run` / `shell.pipe`. Direct
`subprocess` calls break the session-log contract.

### Adding a new environment

1. Add `environments.<env>: {domain_suffix, arch, node_selectors}`
   to `config/harness.yaml`.
2. Set `environments.active` to the new env (or pass via CLI flag —
   not yet implemented; see refactor.md §20).
3. Add a matching `values-<env>.yaml` in each chart.

No code changes needed. `cfg.env(name)` reads the matrix generically.

### Adding a new subcommand

Resist the urge. Four subcommands (`init`, `verify-static`, `apply`,
`verify-runtime`) are the orthogonal operations; every plausible new
verb composes from them. If you genuinely need a new verb, wire it in
`cli._build_parser` and its handler stays in `cli.py` unless it needs
reusable helpers.

---

## 6. Session log contract

- **Path**: `$HARNESS_SESSION_LOG`. The CLI creates a default under
  `logging.dir/<ts>-<service>-<stage>-standalone.log` when the env
  var isn't set.
- **Per-command block**:
  ```
  --- [label] $ <argv> ---
  <stdout, may be empty>
  <stderr, may be empty>
  [exit N] (duration: X.XXs)
  ```
- **Event lines** (`shell.write_session_event`): opaque strings
  written verbatim. Used by the CLI and the orchestrator subagent
  (`=== deploy svc @ ts ===`, `[orchestrator] applied N file(s)`).
- **Retention**: `logging.retention_days` is declared in the schema
  but pruning is not yet implemented (future work).

The session log is authoritative for post-mortem. The CLI JSON
response is a summary; it truncates long output to
`logging.tail_chars`. Never truncate the log file itself.

---

## 7. What's deliberately absent

- **No state machine / no retry loop inside Python.** The
  orchestrator subagent owns the retry cycle; if you find yourself
  wanting to port it into Python, the LLM-outside boundary is about
  to leak.
- **No LLM API client.** `anthropic`, `openai`, `google-genai` are
  not dependencies. Adding one is an architecture-level change, not
  a feature.
- **No MCP client.** `.claude/settings.json` declares the kagent MCP
  endpoint for subagents; the Python CLI does not speak MCP.
- **No plugin loader.** Checks are a closed set registered in
  `HELM_CHECKS` / `DOCKER_CHECKS`. The consumer customizes via the
  `enabled` flag, not by loading extensions.

If a future requirement pushes against these boundaries, revisit the
refactor doc before writing code.
