#!/usr/bin/env bash
# PostToolUse hook — append a one-section entry to the current deploy
# session log for every LLM tool call that mutates state or queries the
# cluster. This is *passive visibility* — the LLM does not see the log;
# it is written by the harness runtime alone so token cost is zero.
#
# Matches (configured in settings.json):
#   Write | Edit | Task | mcp__kagent__*
#
# Read is intentionally excluded (too noisy, no side effects worth
# recording). Bash is excluded because shell.run already logs itself.
#
# Session log location is read from .harness/current-session-log, which
# `python -m harness session-path` writes on every /deploy. If the
# pointer is missing (e.g. LLM editing outside a deploy cycle) the hook
# silently no-ops so we don't create stray logs.
#
# Exit 0 always — this hook must not block tool execution.

set -euo pipefail

POINTER=".harness/current-session-log"
[[ -f "$POINTER" ]] || exit 0

SESSION_LOG="$(head -n 1 "$POINTER" | tr -d '\n')"
[[ -n "$SESSION_LOG" ]] || exit 0

PAYLOAD="$(cat)"

HARNESS_SESSION_LOG="$SESSION_LOG" HARNESS_PAYLOAD="$PAYLOAD" python3 - <<'PY' || true
import json
import os
import time
from pathlib import Path

log_path = Path(os.environ["HARNESS_SESSION_LOG"])
try:
    payload = json.loads(os.environ.get("HARNESS_PAYLOAD", "") or "{}")
except json.JSONDecodeError:
    raise SystemExit(0)

tool = payload.get("tool_name") or payload.get("tool") or "?"
tool_input = payload.get("tool_input") or {}
tool_response = payload.get("tool_response") or {}

# Build a one-line detail field appropriate to the tool kind.
detail_parts: list[str] = []
if tool in ("Write", "Edit"):
    fp = tool_input.get("file_path", "")
    if fp:
        detail_parts.append(fp)
    if tool == "Edit":
        old = tool_input.get("old_string", "") or ""
        new = tool_input.get("new_string", "") or ""
        detail_parts.append(f"lines_changed={len(old.splitlines())}->{len(new.splitlines())}")
    elif tool == "Write":
        content = tool_input.get("content", "") or ""
        detail_parts.append(f"lines={len(content.splitlines())}")
elif tool == "Task":
    sub = tool_input.get("subagent_type", "?")
    desc = tool_input.get("description", "") or ""
    detail_parts.append(f"subagent={sub}")
    if desc:
        detail_parts.append(desc[:60])
elif tool.startswith("mcp__kagent__"):
    # Keep arg summary compact — full bodies go into agent context, not here.
    args_compact = {k: v for k, v in tool_input.items() if isinstance(v, (str, int, bool))}
    if args_compact:
        detail_parts.append(json.dumps(args_compact, ensure_ascii=False)[:200])

# Result status: PostToolUse runs after execution, so presence of a
# non-empty tool_response means success; an explicit error surfaces as
# is_error in the response.
is_error = bool(tool_response.get("is_error"))
status = "error" if is_error else "ok"

ts = time.strftime("%H:%M:%S")
header = f"--- [TOOL/{tool}] {' | '.join([ts, status] + detail_parts)} ---\n"

# For Task, also dump the subagent's response body so the main-session
# audit trail captures diagnoser findings (otherwise only visible to
# the orchestrator via the Task tool return value).
body = ""
if tool == "Task":
    content = tool_response.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text += block.get("text", "")
    if text:
        cap = 4000
        if len(text) > cap:
            text = text[:cap] + f"\n[truncated: {len(text) - cap} bytes]"
        body = "<<< subagent response >>>\n" + text.rstrip("\n") + "\n<<< end >>>\n"

log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("a", encoding="utf-8") as f:
    f.write(header + body)
PY

exit 0
