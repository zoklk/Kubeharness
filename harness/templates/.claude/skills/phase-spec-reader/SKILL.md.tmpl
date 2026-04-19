---
name: phase-spec-reader
description: Rules for reading context/phases/<phase>.md and extracting a single sub_goal's requirements. Load before writing chart or Dockerfile for a new sub_goal.
---

# Phase spec reader

`context/phases/<phase>.md` describes a deployment phase (e.g.
`observability.md`) as a sequence of **sub_goals**. Each sub_goal
maps to one service that will be deployed by `/deploy <sub_goal>`.
This skill is the contract for reading those files.

## File structure

```markdown
# Phase: Observability

Narrative paragraph describing the phase.

## Sub-goal: prometheus

**service_name**: prometheus
**technology**: kube-prometheus-stack v58
**dependency**: [none]
**artifacts**: helm, docker

Requirements narrative describing what this sub_goal delivers:
- scrape namespaces X, Y
- retain data 15d
- expose /metrics on port 9090
- ...

## Sub-goal: grafana

**service_name**: grafana
...
```

## Extracting a specific sub_goal

1. **Fuzzy-match** the requested sub_goal name against `## Sub-goal:`
   headings, case-insensitively. Treat `_`, `-`, and space as
   interchangeable. If multiple match, stop and ask the user.
2. The sub_goal section runs from its heading to the next `## `
   heading at the same level (or EOF).
3. Parse these bold-field lines:

   **Required:**
   - `**service_name**: <slug>` — must be a valid k8s label value
     (`[a-z0-9-]+`). Treat mismatches as a spec bug; stop and ask.
   - `**technology**: <name> [version]` — free-form.
   - `**dependency**: [<other-sub_goal>, ...]` or `[none]`.
   - `**artifacts**: <comma-separated list from {helm, docker}>`.
     This drives which authoring skills you need:
     - contains `helm`  → load `helm-chart-author` and
       `cluster-env-inject`.
     - contains `docker` → load `docker-author`.

   **Optional (process if present):**
   - `**node_category**: <category>` — see `cluster-env-inject`.
   - `**references**: [<context/knowledge/*.md>, ...]` or `[none]`
     — see next step.

4. **Load references** (if `**references**:` is non-`[none]`): Read
   each listed `context/knowledge/*.md` file **before** writing any
   artifact. These describe clustering/storage/port/discovery
   constraints of the **technology** that are invisible from the
   upstream chart alone. Do not proceed to chart authoring until
   these are loaded.

5. **Parse the narrative bullets** below the bold fields. This is
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
- Do not read sibling sub_goals "just for context." Keep reads
  scoped to the requested sub_goal to avoid context bloat.
