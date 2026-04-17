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
3. Parse these bold-field lines (all required):
   - `**service_name**: <slug>` — must be a valid k8s label value
     (`[a-z0-9-]+`). Treat mismatches as a spec bug; stop and ask.
   - `**technology**: <name> [version]` — free-form.
   - `**dependency**: [<other-sub_goal>, ...]` or `[none]`.
   - `**artifacts**: <comma-separated list from {helm, docker}>`.
     This drives which authoring skills you need:
     - contains `helm`  → load `helm-chart-author` and
       `cluster-env-inject`.
     - contains `docker` → load `docker-author`.
4. The narrative paragraph below the bold fields is the functional
   requirement. Keep it in your working memory while authoring; do
   not copy it verbatim into chart values.

## What if `**artifacts**:` is missing?

Older specs omit this field. Assume `helm` only and warn the user —
the project has adopted the `**artifacts**:` convention (refactor
§21.7); the spec should be updated.

## Do not

- Do not edit `context/phases/*.md`. These files are owned by the
  project team, not agents. Denied at the settings layer.
- Do not read sibling sub_goals "just for context." Keep reads
  scoped to the requested sub_goal to avoid context bloat.
