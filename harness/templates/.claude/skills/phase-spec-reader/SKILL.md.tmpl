---
name: phase-spec-reader
description: Rules for reading context/phases/<phase>.md and extracting a single service's requirements. Load before writing chart or Dockerfile for a new service.
---

# Phase spec reader

`context/phases/<phase>.md` describes a deployment phase (e.g.
`observability.md`) as a sequence of **services**. Each service
maps to one workload deployed by `/deploy <phase> <service>`.
This skill is the contract for reading those files.

## File structure

```markdown
# Phase: Observability

Narrative paragraph describing the phase.

## Service: prometheus

**technology**: kube-prometheus-stack v58
**dependency**: [none]
**artifacts**: helm, docker

Requirements narrative describing what this service delivers:
- scrape namespaces X, Y
- retain data 15d
- expose /metrics on port 9090
- ...

## Service: grafana

**technology**: ...
...
```

The service name in the heading (`## Service: <name>`) is the
single source of truth — it is the Helm release name, the chart
directory name (`workspace/helm/<name>/`), and the service label
value. There is no separate `**service_name**:` field.

## Extracting a specific service

1. **Fuzzy-match** the requested service name against `## Service:`
   headings, case-insensitively. Treat `_`, `-`, and space as
   interchangeable. If multiple match, stop and ask the user.
2. The service section runs from its heading to the next `## `
   heading at the same level (or EOF).
3. Validate the heading name is a valid k8s label value
   (`[a-z0-9-]+`). Treat mismatches as a spec bug; stop and ask.
4. Parse these bold-field lines:

   **Required:**
   - `**technology**: <name> [version]` — free-form.
   - `**dependency**: [<other-service>, ...]` or `[none]`.
   - `**artifacts**: <comma-separated list from {helm, docker}>`.
     This drives which authoring skills you need:
     - contains `helm`  → load `helm-chart-author` and
       `cluster-env-inject`.
     - contains `docker` → load `docker-author`.

   **Optional (process if present):**
   - `**node_category**: <category>` — see `cluster-env-inject`.
   - `**references**: [<context/knowledge/*.md>, ...]` or `[none]`
     — see next step.

5. **Load references** (if `**references**:` is non-`[none]`): Read
   each listed `context/knowledge/*.md` file **before** writing any
   artifact. These describe clustering/storage/port/discovery
   constraints of the **technology** that are invisible from the
   upstream chart alone. Do not proceed to chart authoring until
   these are loaded.

6. **Parse the narrative bullets** below the bold fields. This is
   where project-specific requirements live — ports and their roles,
   replica counts, resource sizes, retention, optional features.
   Sub-bullets under headings like `**Port**:` or `**리소스**:`
   (resources) carry concrete numbers you must translate into
   `containerPorts` / `Service.ports` / `resources.requests|limits`
   / `replicaCount` in the Helm chart. Do not copy narrative text
   verbatim into values — extract the data.

## What if `**artifacts**:` is missing?

Older specs omit this field. Assume `helm` only and warn the user —
the project has adopted the `**artifacts**:` convention (refactor
§21.7); the spec should be updated.

## What if `**references**:` points to a missing file?

Treat as a spec bug. Stop and ask the user whether to proceed without
the reference (risk: misconfigured chart) or abort. Do not silently
fall back to "no references."

## Do not

- Do not edit `context/phases/*.md`. These files are owned by the
  project team, not agents. Denied at the settings layer.
- Do not read sibling services "just for context." Keep reads
  scoped to the requested service to avoid context bloat.
