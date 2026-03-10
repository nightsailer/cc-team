"""RelayContext and RelayMode data models for the relay mechanism.

RelayContext is the single source of truth for a relay session, created once
at SessionStart and read by all subsequent hooks and commands.

RelayMode indicates whether this session is standalone, team-lead, or teammate.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class RelayMode(str, Enum):
    """Operating mode for a relay session."""

    STANDALONE = "standalone"
    TEAM_LEAD = "team-lead"
    TEAMMATE = "teammate"


# snake_case → camelCase field mapping for JSON serialization.
_FIELD_MAP: dict[str, str] = {
    "session_id": "sessionId",
    "mode": "mode",
    "team_name": "teamName",
    "member_name": "memberName",
    "backend_type": "backendType",
    "backend_id": "backendId",
    "project_dir": "projectDir",
    "created_at": "createdAt",
    "created_by": "createdBy",
}

_REVERSE_MAP: dict[str, str] = {v: k for k, v in _FIELD_MAP.items()}


@dataclass
class RelayContext:
    """Immutable context for a relay session.

    Created once at SessionStart, persisted to disk, and read by statusline,
    stop hook, and the unified relay command.
    """

    session_id: str
    mode: RelayMode
    team_name: str | None
    member_name: str | None
    backend_type: str
    backend_id: str | None
    project_dir: str
    created_at: int
    created_by: str

    # -- Derived paths --

    @property
    def relay_dir(self) -> str:
        """Base directory for this relay session's files."""
        return os.path.join(self.project_dir, ".claude", "cct", "relay", self.session_id)

    @property
    def handoff_path(self) -> str:
        return os.path.join(self.relay_dir, "handoff.md")

    @property
    def usage_path(self) -> str:
        return os.path.join(self.relay_dir, "usage.json")

    @property
    def context_path(self) -> str:
        return os.path.join(self.relay_dir, "context.json")

    # -- Serialization --

    def _to_dict(self) -> dict[str, Any]:
        """Convert to JSON-compatible dict with camelCase keys."""
        result: dict[str, Any] = {}
        for py_name, json_name in _FIELD_MAP.items():
            value = getattr(self, py_name)
            if isinstance(value, Enum):
                value = value.value
            result[json_name] = value
        return result

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> RelayContext:
        """Construct from a JSON dict with camelCase keys."""
        kwargs: dict[str, Any] = {}
        for json_name, py_name in _REVERSE_MAP.items():
            if json_name in data:
                kwargs[py_name] = data[json_name]
            elif py_name in data:
                # Also accept snake_case keys for flexibility.
                kwargs[py_name] = data[py_name]
        kwargs["mode"] = RelayMode(kwargs["mode"])
        return cls(**kwargs)

    def save(self, path: str | Path) -> None:
        """Atomically write context to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self._to_dict()
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(path))
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    @classmethod
    def load(cls, path: str | Path) -> RelayContext | None:
        """Load context from a JSON file. Returns None if file doesn't exist."""
        path = Path(path)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls._from_dict(data)
