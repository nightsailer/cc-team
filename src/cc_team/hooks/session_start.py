"""SessionStart hook — unified RelayContext creator.

Called at the start of every Claude Code session (standalone or team).
Reads env vars (CCT_RELAY_MODE, CCT_TEAM_NAME, CCT_MEMBER_NAME) or
falls back to the team-marker.json file to determine the relay mode,
then persists a RelayContext to disk for use by all subsequent hooks
and the relay command.

Fallback logic (no env vars):
- Reads team-marker.json to detect team mode.
- Resolves member_name by matching TMUX_PANE against team config members'
  backend_id (design doc §4.3).

Worktree marker auto-creation (design doc §4.4):
- If env vars confirm team mode (team-lead or teammate) but no marker file
  exists, auto-creates it so that sub-teammates launched in the worktree
  can fall back to the marker for mode detection.
"""

from __future__ import annotations

import os
import time

from cc_team._relay_context import RelayContext, RelayMode
from cc_team._team_marker import read_team_marker, write_team_marker
from cc_team.hooks._common import cct_data_dir, project_dir, read_hook_input


def _detect_backend_id() -> str | None:
    """Detect the tmux pane ID hosting this process.

    Returns the pane ID (e.g. '%42') or None if not running in tmux.
    """
    # TMUX_PANE is set by tmux for processes inside a pane.
    return os.environ.get("TMUX_PANE")


def _resolve_member_name(team_name: str | None, backend_id: str | None = None) -> str | None:
    """Match current tmux pane ID against team config members to find member_name.

    Design doc §4.3: resolve member identity by matching backend_id (tmux pane
    ID) against the team configuration. Do NOT use agent_type for matching as
    it is optional and unreliable.

    Args:
        team_name: Team to search in.
        backend_id: Pane ID to match. Falls back to TMUX_PANE env var if None.

    Returns:
        The member name if a matching backend_id is found, None otherwise.
    """
    if backend_id is None:
        backend_id = os.environ.get("TMUX_PANE")
    if not backend_id or not team_name:
        return None

    from cc_team.team_manager import TeamManager

    mgr = TeamManager(team_name)
    config = mgr.read()
    if config is None:
        return None

    for member in config.members:
        if member.backend_id == backend_id:
            return member.name
    return None


def _determine_mode(
    proj: str,
) -> tuple[RelayMode, str | None, str | None, bool]:
    """Determine relay mode from env vars or marker file fallback.

    Returns:
        (mode, team_name, member_name, marker_exists)
        marker_exists is True if a team-marker.json was found on disk.
    """
    relay_mode = os.environ.get("CCT_RELAY_MODE")

    if relay_mode:
        mode = RelayMode(relay_mode)
        team_name = os.environ.get("CCT_TEAM_NAME")
        member_name = os.environ.get("CCT_MEMBER_NAME")
        marker = read_team_marker(proj)
        return mode, team_name, member_name, marker is not None

    # Fallback: check team-marker.json
    marker = read_team_marker(proj)
    if marker is not None:
        team_name = marker.get("teamName")
        backend_id = _detect_backend_id()
        # Resolve member_name by matching backend_id against team config members.
        member_name = _resolve_member_name(team_name, backend_id)
        return RelayMode.TEAMMATE, team_name, member_name, True

    # Default: standalone
    return RelayMode.STANDALONE, None, None, False


def main() -> None:
    """Hook entry point — create RelayContext if not already present."""
    hook_input = read_hook_input()
    session_id = hook_input.get("session_id")

    if not session_id:
        return  # No session_id available, skip.

    proj = project_dir()

    # Build context path and check if already exists (get-or-create pattern).
    relay_dir = os.path.join(cct_data_dir(proj), "relay", session_id)
    context_path = os.path.join(relay_dir, "context.json")

    if os.path.exists(context_path):
        return  # Already created, skip.

    mode, team_name, member_name, marker_exists = _determine_mode(proj)
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

    # Auto-create team-marker in worktree if team mode confirmed by env but
    # no marker exists (design doc §4.4). This allows sub-teammates launched
    # in the same worktree to fall back to the marker for mode detection.
    if mode in (RelayMode.TEAM_LEAD, RelayMode.TEAMMATE) and team_name and not marker_exists:
        write_team_marker(proj, team_name, created_by="session-start-hook-worktree")
