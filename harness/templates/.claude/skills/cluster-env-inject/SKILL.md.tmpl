---
name: cluster-env-inject
description: Rules for filling values-dev.yaml / values-prod.yaml from the environments matrix in config/harness.yaml. Load when populating env-specific chart values.
---

# Cluster env inject

The project's env matrix lives in `config/harness.yaml` under
`environments.*`. Per-env values files (`values-dev.yaml`,
`values-prod.yaml`) translate that matrix into chart input. This
skill keeps the translation consistent.

## Where to read the matrix

```yaml
environments:
  active: dev
  dev:
    domain_suffix: dev.example.local
    arch: amd64
    node_selectors:
      storage: node-a
      monitoring: node-b
  prod:
    domain_suffix: cluster.local
    arch: arm64
    node_selectors:
      storage: node-p1
      monitoring: node-p2
```

## What belongs in `values-<env>.yaml`

Only values that differ by env:

- `nodeSelector` — pick from `environments.<env>.node_selectors`.
  **Resolution order**:
  1. If the sub_goal declares `**node_category**: <key>` in its
     phase doc, use that key directly. This is the deterministic path.
  2. Otherwise, infer from `technology` / service description
     (a prometheus-like service → `monitoring`; a ceph/minio-like
     service → `storage`). This is best-effort.
  3. If the resolved key is not in `node_selectors`, emit a warning
     and omit the nodeSelector block (do not guess a hostname).
- `ingress.hosts` / `externalUrl` — suffix with
  `environments.<env>.domain_suffix`.
- `replicaCount`, resource limits — if they differ between envs.
- `image.tag` — **only** when the service has a different release
  per env. Otherwise leave it in `values.yaml`.

Shared defaults (container names, default ports, image repo base)
belong in `values.yaml`, not the per-env file.

## Shape consistency

- Keep the top-level key order identical across
  `values.yaml` / `values-dev.yaml` / `values-prod.yaml` so diffs
  read cleanly.
- Use the same indentation width. yamllint in relaxed mode
  tolerates deeper indents but new drift makes reviews painful.

## Example: a service that schedules on storage nodes

```yaml
# values-dev.yaml
nodeSelector:
  kubernetes.io/hostname: node-a     # from environments.dev.node_selectors.storage

ingress:
  hosts:
    - host: svc.dev.example.local    # uses environments.dev.domain_suffix
      paths:
        - path: /
          pathType: Prefix
```

```yaml
# values-prod.yaml
nodeSelector:
  kubernetes.io/hostname: node-p1    # from environments.prod.node_selectors.storage

ingress:
  hosts:
    - host: svc.cluster.local
      paths:
        - path: /
          pathType: Prefix
```

## Do not

- Do not duplicate values in `values-dev.yaml` that are already in
  `values.yaml` unless overriding.
- Do not hardcode domain suffixes or node names in
  `templates/**` — the override must come from values so prod
  swaps in cleanly.
- Do not introduce a third env (`values-staging.yaml`) without
  first adding `staging` to `environments.*` in
  `config/harness.yaml`.
