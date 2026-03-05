"""Unit tests for cc_team.hooks._common — shared hook utilities.

Covers:
- relay_paths() structure and path construction
- cct_data_dir() env override and fallback
- load_config() defaults and custom values
- project_dir() env usage
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cc_team.hooks._common import (
    cct_data_dir,
    load_config,
    project_dir,
    read_json,
    relay_paths,
    write_json,
)


class TestProjectDir:
    """project_dir() tests."""

    def test_uses_env_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLAUDE_PROJECT_DIR env takes priority."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/custom/project")
        assert project_dir() == "/custom/project"

    def test_falls_back_to_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to os.getcwd() when env is absent."""
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        assert project_dir() == os.getcwd()


class TestCctDataDir:
    """cct_data_dir() tests."""

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CCT_PROJECT_DATA_DIR env override takes priority."""
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", "/override/data")
        assert cct_data_dir() == "/override/data"

    def test_fallback_to_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to {proj}/.claude/cct/ when env is absent."""
        monkeypatch.delenv("CCT_PROJECT_DATA_DIR", raising=False)
        result = cct_data_dir("/my/project")
        assert result == "/my/project/.claude/cct"

    def test_fallback_uses_project_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When proj=None, uses project_dir() fallback."""
        monkeypatch.delenv("CCT_PROJECT_DATA_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/env/project")
        result = cct_data_dir()
        assert result == "/env/project/.claude/cct"


class TestRelayPaths:
    """relay_paths() tests."""

    def test_returns_all_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns dict with all 5 expected keys."""
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", "/data")
        paths = relay_paths("session-123")
        assert set(paths.keys()) == {"dir", "handoff", "usage", "history", "state"}

    def test_paths_under_session_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All paths are under cct_data_dir/relay/{session_id}/."""
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", "/data")
        paths = relay_paths("abc-def")
        base = "/data/relay/abc-def"
        assert paths["dir"] == base
        assert paths["handoff"] == os.path.join(base, "handoff.md")
        assert paths["usage"] == os.path.join(base, "usage.json")
        assert paths["history"] == os.path.join(base, "history.json")
        assert paths["state"] == os.path.join(base, "state.json")

    def test_with_explicit_proj(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit proj parameter is used for path construction."""
        monkeypatch.delenv("CCT_PROJECT_DATA_DIR", raising=False)
        paths = relay_paths("sid-1", proj="/my/proj")
        assert paths["dir"] == "/my/proj/.claude/cct/relay/sid-1"


class TestLoadConfig:
    """load_config() tests."""

    def test_defaults_when_missing(self, tmp_path: Path) -> None:
        """Returns defaults when config file does not exist."""
        cfg = load_config(str(tmp_path))
        assert cfg["threshold"] == 80
        assert cfg["max_block_count"] == 3
        assert cfg["team_name"] == ""

    def test_reads_custom_values(self, tmp_path: Path) -> None:
        """Reads custom config values from file."""
        config_dir = tmp_path / ".claude" / "hooks"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "context-relay-config.json"
        config_file.write_text(
            json.dumps(
                {
                    "threshold": 90,
                    "max_block_count": 5,
                    "team_name": "my-team",
                }
            )
        )
        cfg = load_config(str(tmp_path))
        assert cfg["threshold"] == 90
        assert cfg["max_block_count"] == 5
        assert cfg["team_name"] == "my-team"

    def test_partial_config_fills_defaults(self, tmp_path: Path) -> None:
        """Missing keys are filled with defaults."""
        config_dir = tmp_path / ".claude" / "hooks"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "context-relay-config.json"
        config_file.write_text(json.dumps({"threshold": 95}))
        cfg = load_config(str(tmp_path))
        assert cfg["threshold"] == 95
        assert cfg["max_block_count"] == 3
        assert cfg["team_name"] == ""


class TestReadWriteJson:
    """read_json() and write_json() tests."""

    def test_read_missing_file(self) -> None:
        """Returns empty dict for missing file."""
        assert read_json("/nonexistent/path.json") == {}

    def test_write_then_read(self, tmp_path: Path) -> None:
        """write_json then read_json round-trips correctly."""
        path = str(tmp_path / "test.json")
        write_json(path, {"key": "value"})
        assert read_json(path) == {"key": "value"}

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        """write_json creates parent directories."""
        path = str(tmp_path / "a" / "b" / "c.json")
        write_json(path, {"nested": True})
        assert read_json(path) == {"nested": True}
