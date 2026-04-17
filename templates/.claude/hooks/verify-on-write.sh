#!/usr/bin/env bash
# PostToolUse hook — lightweight per-file feedback after Write/Edit.
#
# This is *not* a full static check; it only runs the single linter that
# matches the file's artifact kind, so the agent gets immediate feedback
# on syntax/lint errors. Full static.py runs at /deploy time.
#
#   *.yaml under a helm chart path → yamllint (excludes templates/)
#   Dockerfile*                    → hadolint
#   everything else                → no-op
#
# Exit 0 → nothing to say; let Claude continue.
# Exit 2 → surface stderr to the LLM so it can fix the file.

set -euo pipefail

CONFIG="${HARNESS_CONFIG:-config/harness.yaml}"
[[ -f "$CONFIG" ]] || exit 0

PAYLOAD="$(cat)"

# Pull tool_input.file_path + chart/docker roots from the config.
read -r FILE CHART_GLOB DOCKER_GLOB < <(
  HARNESS_CONFIG="$CONFIG" HARNESS_PAYLOAD="$PAYLOAD" python3 - <<'PY'
import json
import os
from pathlib import Path

import yaml

cfg = yaml.safe_load(Path(os.environ["HARNESS_CONFIG"]).read_text(encoding="utf-8")) or {}
conv = cfg.get("conventions", {})
ws = conv.get("workspace_dir", "workspace")
chart_glob = conv.get("chart_path", f"{ws}/helm/{{service}}").replace("{workspace}", ws)
docker_glob = conv.get("docker_path", f"{ws}/docker/{{service}}").replace("{workspace}", ws)

try:
    payload = json.loads(os.environ.get("HARNESS_PAYLOAD", "") or "{}")
except json.JSONDecodeError:
    payload = {}
file_path = (payload.get("tool_input", {}) or {}).get("file_path", "")
print(file_path or "-", chart_glob, docker_glob)
PY
)

[[ "$FILE" == "-" || -z "$FILE" ]] && exit 0

# Helper: does $1 sit under a path matching $2 (where $2 may contain {service})?
_matches_under() {
  local target="$1" root_pattern="$2"
  # Strip the {service} segment to a glob root, then see if $target starts with it.
  local root="${root_pattern%%/\{service\}*}"
  [[ "$target" == "$root"/* ]]
}

if [[ "$FILE" == *.yaml || "$FILE" == *.yml ]] && _matches_under "$FILE" "$CHART_GLOB"; then
  # Skip templates/ — Go templating is not valid YAML.
  if [[ "$FILE" == *"/templates/"* ]]; then
    exit 0
  fi
  if ! command -v yamllint >/dev/null 2>&1; then
    exit 0
  fi
  if ! yamllint -d relaxed -f parsable "$FILE" >&2; then
    exit 2
  fi
fi

if [[ "$(basename "$FILE")" == Dockerfile* ]]; then
  if ! command -v hadolint >/dev/null 2>&1; then
    exit 0
  fi
  if ! hadolint "$FILE" >&2; then
    exit 2
  fi
fi

exit 0
