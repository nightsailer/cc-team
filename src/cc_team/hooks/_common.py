"""Shared utilities for context-relay hooks.

Provides:
  - project_dir()       : CLAUDE_PROJECT_DIR with fallback
  - read_json/write_json/atomic_write_json : file I/O
  - read_hook_input()   : parse JSON hook input from stdin
  - load_config()       : read context-relay-config.json
  - cct_data_dir()      : CCT project data directory
  - relay_paths()       : per-session relay file paths
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile

CONFIG_REL = ".claude/hooks/context-relay-config.json"


def read_hook_input() -> dict:
    """Read and parse JSON hook input from stdin.

    Returns an empty dict on missing or invalid input.
    Used by stop and statusline hooks to consume Claude Code's hook payload.
    """
    raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def project_dir() -> str:
    """Return the Claude project directory, falling back to cwd."""
    return os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def read_json(path: str) -> dict:
    """Read a JSON file, returning {} on missing or invalid files."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_json(path: str, data: dict) -> None:
    """Write dict as JSON to *path*, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def atomic_write_json(path: str, data: dict) -> None:
    """Write JSON atomically (tmp + rename) to avoid partial reads."""
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def load_config(proj: str | None = None) -> dict:
    """Load context-relay config from the project directory.

    Config schema: {"threshold": 80, "max_block_count": 3, "team_name": "",
                    "relay_prompt_template": null}
    """
    if proj is None:
        proj = project_dir()
    cfg = read_json(os.path.join(proj, CONFIG_REL))
    result: dict = {
        "threshold": cfg.get("threshold", 80),
        "max_block_count": cfg.get("max_block_count", 3),
        "team_name": cfg.get("team_name", ""),
    }
    # Optional: custom relay prompt template (string or None).
    relay_tpl = cfg.get("relay_prompt_template")
    if relay_tpl is not None:
        result["relay_prompt_template"] = relay_tpl
    return result


def cct_data_dir(proj: str | None = None) -> str:
    """Return the CCT project data directory.

    Uses CCT_PROJECT_DATA_DIR env override, otherwise falls back to
    ``{proj}/.claude/cct/``.
    """
    env_override = os.environ.get("CCT_PROJECT_DATA_DIR")
    if env_override:
        return env_override
    if proj is None:
        proj = project_dir()
    return os.path.join(proj, ".claude", "cct")


def relay_paths(session_id: str, proj: str | None = None) -> dict:
    """Return all per-session relay file paths.

    Returns a dict with keys: dir, handoff, usage, history, state
    — all under ``cct_data_dir()/relay/{session_id}/``.
    """
    base = os.path.join(cct_data_dir(proj), "relay", session_id)
    return {
        "dir": base,
        "handoff": os.path.join(base, "handoff.md"),
        "usage": os.path.join(base, "usage.json"),
        "history": os.path.join(base, "history.json"),
        "state": os.path.join(base, "state.json"),
    }
