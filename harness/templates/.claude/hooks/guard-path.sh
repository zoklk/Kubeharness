#!/usr/bin/env bash
# PreToolUse hook — block writes outside the configured workspace.
#
# Reads Claude Code's tool payload from stdin (JSON), extracts
# `tool_input.file_path`, and checks it against the glob lists in
# `config/harness.yaml`:
#   conventions.workspace_dir
#   conventions.write_allowed_globs
#   conventions.write_denied_globs
#
# Exit 0  → allow  (path matches an allow-glob and no deny-glob)
# Exit 2  → block  (stderr carries a one-line reason for Claude)

set -euo pipefail

CONFIG="${HARNESS_CONFIG:-config/harness.yaml}"
if [[ ! -f "$CONFIG" ]]; then
  echo "guard-path: $CONFIG not found — cannot evaluate write permission" >&2
  exit 2
fi

PAYLOAD="$(cat)"

HARNESS_CONFIG="$CONFIG" HARNESS_PAYLOAD="$PAYLOAD" python3 - <<'PY'
import fnmatch
import json
import os
import sys
from pathlib import Path

import yaml  # pyyaml is a kubeharness dependency — always available.

cfg = yaml.safe_load(Path(os.environ["HARNESS_CONFIG"]).read_text(encoding="utf-8")) or {}
conv = cfg.get("conventions", {})
workspace_dir = conv.get("workspace_dir", "workspace")
allowed = [p.replace("{workspace}", workspace_dir) for p in (conv.get("write_allowed_globs") or [])]
denied = [p.replace("{workspace}", workspace_dir) for p in (conv.get("write_denied_globs") or [])]

try:
    payload = json.loads(os.environ.get("HARNESS_PAYLOAD", "") or "{}")
except json.JSONDecodeError:
    sys.exit(0)

file_path = (payload.get("tool_input", {}) or {}).get("file_path", "")
if not file_path:
    sys.exit(0)

try:
    rel = str(Path(file_path).resolve().relative_to(Path.cwd().resolve()))
except ValueError:
    rel = file_path

for pat in denied:
    if fnmatch.fnmatch(rel, pat):
        print(f"guard-path: blocked by deny-glob {pat!r}: {rel}", file=sys.stderr)
        sys.exit(2)

if allowed and not any(fnmatch.fnmatch(rel, pat) for pat in allowed):
    print(f"guard-path: {rel!r} is outside allowed workspace globs", file=sys.stderr)
    sys.exit(2)

sys.exit(0)
PY
