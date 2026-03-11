"""Team marker file management.

A team-marker.json file records which team owns a project directory.
Used by the SessionStart hook to detect team mode without env vars
(e.g., in worktrees or sub-teammates).
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

_PANE_ID_RE = re.compile(r"^%\d+$")


class TeamMarkerConflictError(Exception):
    """Raised when a project already has an active team marker."""


def marker_path(project_dir: str | Path) -> Path:
    """Return the path to the team marker file."""
    return Path(project_dir) / ".claude" / "cct" / "team-marker.json"


def write_team_marker(
    project_dir: str | Path,
    team_name: str,
    *,
    created_by: str = "cct-session-start-team",
) -> None:
    """Write a team marker file atomically."""
    from cc_team._serialization import atomic_write_json

    path = marker_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "teamName": team_name,
        "createdAt": int(time.time() * 1000),
        "createdBy": created_by,
    }
    atomic_write_json(path, data)


def read_team_marker(project_dir: str | Path) -> dict[str, Any] | None:
    """Read the team marker file. Returns None if it doesn't exist."""
    path = marker_path(project_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def remove_team_marker(project_dir: str | Path) -> None:
    """Remove the team marker file. No-op if it doesn't exist."""
    path = marker_path(project_dir)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def check_stale_marker(
    project_dir: str | Path,
    team_alive_fn: Any | None = None,
) -> dict[str, Any] | None:
    """Check for a stale team marker.

    Returns:
        None if no marker exists.
        The marker dict if the team is stale (not alive).

    Raises:
        TeamMarkerConflictError: if the marker exists and the team is still alive.
    """
    marker = read_team_marker(project_dir)
    if marker is None:
        return None
    if team_alive_fn and team_alive_fn(marker["teamName"]):
        raise TeamMarkerConflictError(f"Active team '{marker['teamName']}' exists in this project")
    return marker


def _is_pane_alive_sync(pane_id: str) -> bool:
    """Check if a tmux pane is alive (synchronous, for use in non-async contexts).

    Uses ``tmux display-message`` to verify the pane exists.
    """
    if not _PANE_ID_RE.match(pane_id):
        return False
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", pane_id, "-p", "#{pane_id}"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() != b""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def make_team_alive_fn() -> Any:
    """Create a team_alive_fn suitable for check_stale_marker.

    Returns a callable ``(team_name: str) -> bool`` that reads the team config
    and checks whether the team lead's tmux pane is still alive.
    """
    from cc_team.team_manager import TeamManager
    from cc_team.types import TEAM_LEAD_AGENT_TYPE

    def _is_alive(team_name: str) -> bool:
        mgr = TeamManager(team_name)
        config = mgr.read()
        if config is None:
            return False
        lead = next(
            (m for m in config.members if m.name == TEAM_LEAD_AGENT_TYPE),
            None,
        )
        if lead is None or not lead.backend_id:
            return False
        return _is_pane_alive_sync(lead.backend_id)

    return _is_alive
