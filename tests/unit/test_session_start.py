"""Unit tests for cct session start command.

Covers:
- Generates UUID CCT_SESSION_ID
- Creates relay directory structure
- Initializes history.json
- Calls os.execvpe with correct args and env
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSessionStart:
    """cct session start tests."""

    def test_generates_uuid_and_calls_execvpe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generates UUID, inits history, calls os.execvpe."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", str(tmp_path / "cct"))

        mock_execvpe = MagicMock()
        mock_uuid = "test-uuid-1234"

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch("cc_team.cli.uuid.uuid4", return_value=MagicMock(__str__=lambda self: mock_uuid)),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
        ):
            from cc_team.cli import main

            main(["--quiet", "session", "start"])

        # Verify os.execvpe was called
        mock_execvpe.assert_called_once()
        call_args = mock_execvpe.call_args
        assert call_args[0][0] == "/usr/bin/claude"  # binary
        assert call_args[0][1][0] == "/usr/bin/claude"  # argv[0]

        # Verify env includes CCT_SESSION_ID
        env = call_args[0][2]
        assert env["CCT_SESSION_ID"] == mock_uuid

    def test_creates_relay_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Creates relay directory and history.json."""
        data_dir = tmp_path / "cct"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", str(data_dir))

        mock_uuid = "test-uuid-5678"

        with (
            patch("cc_team.cli.os.execvpe", MagicMock()),
            patch("cc_team.cli.uuid.uuid4", return_value=MagicMock(__str__=lambda self: mock_uuid)),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="claude",
            ),
        ):
            from cc_team.cli import main

            main(["--quiet", "session", "start"])

        # Verify relay directory was created
        relay_dir = data_dir / "relay" / mock_uuid
        assert relay_dir.is_dir()

        # Verify history.json was initialized
        history = relay_dir / "history.json"
        assert history.exists()
        data = json.loads(history.read_text())
        assert "sessions" in data
        assert "created_at" in data

    def test_passthrough_args(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Passthrough args are forwarded to claude binary."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", str(tmp_path / "cct"))

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch("cc_team.cli.uuid.uuid4", return_value=MagicMock(__str__=lambda self: "uuid")),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="claude",
            ),
        ):
            from cc_team.cli import main

            main(["--quiet", "session", "start", "--", "--model", "opus"])

        call_args = mock_execvpe.call_args[0]
        argv = call_args[1]
        assert "--model" in argv
        assert "opus" in argv
