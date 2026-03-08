#!/usr/bin/env python3
"""Stop Hook: Context Relay (2-phase handoff).

Phase 1 — No handoff.md: block with handoff instructions when usage exceeds threshold.
Phase 2 — handoff.md exists: launch relay in background, allow stop.

Safety: all exceptions are caught and logged to prevent
"Stop hook error occurred" from disrupting agent work.

Usage: cct _hook stop
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone

from cc_team.hooks._common import (
    load_config,
    project_dir,
    read_json,
    relay_paths,
    write_json,
)

_DEBUG_LOG = os.path.join(
    os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()),
    "logs",
    "context-relay-debug.log",
)


def _log_error(msg: str) -> None:
    """Append error to debug log (best-effort, never raises)."""
    try:
        os.makedirs(os.path.dirname(_DEBUG_LOG), exist_ok=True)
        with open(_DEBUG_LOG, "a") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")
    except Exception:
        pass


def _find_backend_id_tmux() -> str | None:
    """Walk PID tree upward to find the tmux pane hosting this process.

    Runs ``tmux list-panes -a`` and matches against ancestor PIDs.
    Returns the pane_id (e.g. ``%42``) or None.
    """
    try:
        raw = subprocess.check_output(
            ["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{pane_id}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    # Build pid→pane_id map
    pane_map: dict[int, str] = {}
    for line in raw.strip().splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                pane_map[int(parts[0])] = parts[1]
            except ValueError:
                continue

    # Walk up the PID tree from current process
    pid = os.getpid()
    for _ in range(10):  # safety limit
        if pid in pane_map:
            return pane_map[pid]
        if pid <= 1:
            break
        # Get parent PID via ps (cross-platform)
        try:
            ppid_str = subprocess.check_output(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            pid = int(ppid_str)
        except (subprocess.SubprocessError, ValueError):
            break

    return None


def _launch_relay_background(cfg: dict) -> None:
    """Launch ``cct relay`` or ``cct team relay`` in a detached subprocess."""
    team_name = cfg.get("team_name", "")
    cmd = ["cct", "--team-name", team_name, "team", "relay"] if team_name else ["cct", "relay"]

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        _log_error(f"Failed to launch relay: {exc}")


def main() -> None:
    """Stop hook entry point."""
    cct_session_id = os.environ.get("CCT_SESSION_ID", "")
    if not cct_session_id:
        return

    proj = project_dir()
    cfg = load_config(proj)
    paths = relay_paths(cct_session_id, proj)

    # Read usage — skip if this is a subagent
    usage = read_json(paths["usage"])
    agent_name = usage.get("agent_name", "")
    if agent_name:
        return

    used_pct = usage.get("used_percentage", 0)
    handoff_exists = os.path.isfile(paths["handoff"])

    # Phase 2: handoff exists → launch relay and allow stop
    if handoff_exists:
        _launch_relay_background(cfg)
        return

    # Phase 1: no handoff yet
    if used_pct < cfg["threshold"]:
        return

    # Check escape valve
    state = read_json(paths["state"])
    block_count = state.get("block_count", 0)

    if block_count >= cfg["max_block_count"]:
        # Escape valve: allow stop after max blocks
        return

    # Block with handoff instructions
    state["block_count"] = block_count + 1
    state["triggered_pct"] = used_pct
    state["triggered_at"] = datetime.now(timezone.utc).isoformat()
    write_json(paths["state"], state)

    handoff_rel = os.path.relpath(paths["handoff"], proj)
    msg = (
        f"Context window usage at {used_pct:.1f}% (threshold {cfg['threshold']}%). "
        f"Write a handoff file to `{handoff_rel}` with sections:\n"
        f"  - ## Current Task\n"
        f"  - ## Completed Work\n"
        f"  - ## Pending Work\n"
        f"  - ## Key Context\n"
        f"  - ## Next Steps\n"
        f"Then tell the user to run /clear.\n"
        f"(Block {block_count + 1}/{cfg['max_block_count']})"
    )
    print(msg, file=sys.stderr)
    sys.exit(2)


