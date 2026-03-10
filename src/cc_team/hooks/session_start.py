"""SessionStart hook — unified RelayContext creator.

Called at the start of every Claude Code session (standalone or team).
Reads env vars (CCT_RELAY_MODE, CCT_TEAM_NAME, CCT_MEMBER_NAME) or
falls back to the team-marker.json file to determine the relay mode,
then persists a RelayContext to disk for use by all subsequent hooks
and the relay command.
"""

from __future__ import annotations

import os
import time

from cc_team._relay_context import RelayContext, RelayMode
from cc_team._team_marker import read_team_marker
from cc_team.hooks._common import project_dir, read_hook_input


def _detect_backend_id() -> str | None:
    """Detect the tmux pane ID hosting this process.

    Returns the pane ID (e.g. '%42') or None if not running in tmux.
    """
    # TMUX_PANE is set by tmux for processes inside a pane.
    return os.environ.get("TMUX_PANE")


def _determine_mode() -> tuple[RelayMode, str | None, str | None]:
    """Determine relay mode from env vars or marker file fallback.

    Returns:
        (mode, team_name, member_name)
    """
    relay_mode = os.environ.get("CCT_RELAY_MODE")

    if relay_mode:
        mode = RelayMode(relay_mode)
        team_name = os.environ.get("CCT_TEAM_NAME")
        member_name = os.environ.get("CCT_MEMBER_NAME")
        return mode, team_name, member_name

    # Fallback: check team-marker.json
    proj = project_dir()
    marker = read_team_marker(proj)
    if marker is not None:
        team_name = marker.get("teamName")
        # No env vars means this is likely a sub-teammate in a worktree.
        return RelayMode.TEAMMATE, team_name, None

    # Default: standalone
    return RelayMode.STANDALONE, None, None


def main() -> None:
    """Hook entry point — create RelayContext if not already present."""
    hook_input = read_hook_input()
    session_id = hook_input.get("session_id")

    if not session_id:
        return  # No session_id available, skip.

    proj = project_dir()

    # Build context path and check if already exists (get-or-create pattern).
    relay_dir = os.path.join(proj, ".claude", "cct", "relay", session_id)
    context_path = os.path.join(relay_dir, "context.json")

    if os.path.exists(context_path):
        return  # Already created, skip.

    mode, team_name, member_name = _determine_mode()
    backend_id = _detect_backend_id()

    ctx = RelayContext(
        session_id=session_id,
        mode=mode,
        team_name=team_name,
        member_name=member_name,
        backend_type="tmux",
        backend_id=backend_id,
        project_dir=proj,
        created_at=int(time.time() * 1000),
        created_by="session-start-hook",
    )

    ctx.save(context_path)
