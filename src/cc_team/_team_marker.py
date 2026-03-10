"""Team marker file management.

A team-marker.json file records which team owns a project directory.
Used by the SessionStart hook to detect team mode without env vars
(e.g., in worktrees or sub-teammates).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


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
    path = marker_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "teamName": team_name,
        "createdAt": int(time.time() * 1000),
        "createdBy": created_by,
    }

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def read_team_marker(project_dir: str | Path) -> dict[str, Any] | None:
    """Read the team marker file. Returns None if it doesn't exist."""
    path = marker_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
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
