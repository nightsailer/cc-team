#!/usr/bin/env python3
"""Stop Hook: Context Relay (2-phase handoff).

Phase 1 — No handoff.md: block with handoff instructions when usage exceeds threshold.
Phase 2 — handoff.md exists: launch relay in background, allow stop.

Uses native session_id from hook input and reads RelayContext for mode selection.

Safety: all exceptions are caught and logged to prevent
"Stop hook error occurred" from disrupting agent work.

Usage: cct _hook stop
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
from datetime import datetime, timezone

from cc_team._relay_context import RelayContext, RelayMode
from cc_team.hooks._common import (
    load_config,
    project_dir,
    read_hook_input,
    read_json,
    relay_paths,
    write_json,
)


@functools.lru_cache(maxsize=1)
def _debug_log_path() -> str:
    """Return debug log path, evaluated lazily on first call."""
    return os.path.join(
        os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()),
        "logs",
        "context-relay-debug.log",
    )


def _log_error(msg: str) -> None:
    """Append error to debug log (best-effort, never raises)."""
    try:
        log_path = _debug_log_path()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")
    except Exception:
        pass


def _launch_relay_background(context_path: str) -> None:
    """Launch unified ``cct relay --context`` in a detached subprocess."""
    cmd = ["cct", "relay", "--context", context_path]
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        _log_error(f"Failed to launch relay: {exc}")


def _get_handoff_instructions(
    relay_ctx: RelayContext | None,
    used_pct: float,
    threshold: int,
    block_count: int,
    max_block_count: int,
    handoff_rel: str,
) -> str:
    """Build the handoff instruction message based on mode."""
    from cc_team._handoff_templates import get_handoff_template

    mode = relay_ctx.mode if relay_ctx else RelayMode.STANDALONE

    # Use per-mode template if available, with fallback to generic
    try:
        handoff_path_display = f"`{handoff_rel}`"
        template = get_handoff_template(mode)
        instructions = template.format(handoff_path=handoff_path_display)
    except (KeyError, IndexError):
        instructions = (
            f"Write a handoff file to `{handoff_rel}` with sections:\n"
            f"  - ## Current Task\n"
            f"  - ## Completed Work\n"
            f"  - ## Pending Work\n"
            f"  - ## Key Context\n"
            f"  - ## Next Steps"
        )

    return (
        f"Context window usage at {used_pct:.1f}% (threshold {threshold}%). "
        f"{instructions}\n"
        f"Then tell the user to run /clear.\n"
        f"(Block {block_count + 1}/{max_block_count})"
    )


def main() -> None:
    """Stop hook entry point."""
    hook_input = read_hook_input()

    # Skip subagent calls — agent_id is present only in subagent hook input.
    if hook_input.get("agent_id"):
        return

    # Use native session_id from hook input.
    session_id = hook_input.get("session_id", "")
    if not session_id:
        return

    proj = project_dir()
    cfg = load_config(proj)

    # Compute relay paths using native session_id.
    paths = relay_paths(session_id, proj)
    handoff_path = paths["handoff"]
    usage_path = paths["usage"]
    state_path = paths["state"]
    context_path = os.path.join(paths["dir"], "context.json")

    # Phase 2: handoff exists → launch relay and allow stop
    if os.path.isfile(handoff_path):
        if os.path.isfile(context_path):
            _launch_relay_background(context_path)
        else:
            _log_error(f"handoff exists but context.json missing: {context_path}")
        return

    # Phase 1: no handoff yet — check usage threshold
    usage = read_json(usage_path)
    used_pct = usage.get("used_percentage", 0)
    if used_pct < cfg["threshold"]:
        return

    # Check escape valve
    state = read_json(state_path)
    block_count = state.get("block_count", 0)

    if block_count >= cfg["max_block_count"]:
        return

    # Block with mode-aware handoff instructions
    state["block_count"] = block_count + 1
    state["triggered_pct"] = used_pct
    state["triggered_at"] = datetime.now(timezone.utc).isoformat()
    write_json(state_path, state)

    # Load RelayContext only when needed (deferred from hot path).
    relay_ctx = RelayContext.load(context_path)

    handoff_rel = os.path.relpath(handoff_path, proj)
    msg = _get_handoff_instructions(
        relay_ctx,
        used_pct,
        cfg["threshold"],
        block_count,
        cfg["max_block_count"],
        handoff_rel,
    )
    print(msg, file=sys.stderr)
    sys.exit(2)
