"""Unit tests for cct session start command.

Covers:
- Sets CCT_RELAY_MODE=standalone
- Calls os.execvpe with correct args and env
- Does NOT set CCT_SESSION_ID
- Does NOT create relay directory (SessionStart hook's job)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSessionStart:
    """cct session start tests."""

    def test_sets_relay_mode_standalone(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sets CCT_RELAY_MODE=standalone in env passed to execvpe."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="claude",
            ),
        ):
            from cc_team.cli import main

            main(["--quiet", "session", "start"])

        env = mock_execvpe.call_args[0][2]
        assert env["CCT_RELAY_MODE"] == "standalone"

    def test_does_not_set_cct_session_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CCT_SESSION_ID must NOT be set (removed by design)."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("CCT_SESSION_ID", raising=False)

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="claude",
            ),
        ):
            from cc_team.cli import main

            main(["--quiet", "session", "start"])

        env = mock_execvpe.call_args[0][2]
        assert "CCT_SESSION_ID" not in env

    def test_does_not_create_relay_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Relay dir creation is SessionStart hook's job, not CLI's."""
        data_dir = tmp_path / "cct"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", str(data_dir))

        with (
            patch("cc_team.cli.os.execvpe", MagicMock()),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="claude",
            ),
        ):
            from cc_team.cli import main

            main(["--quiet", "session", "start"])

        # Relay directory should NOT be created by session start
        relay_dir = data_dir / "relay"
        assert not relay_dir.exists()

    def test_calls_execvpe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Calls os.execvpe with claude binary."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
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

    def test_passthrough_args(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Passthrough args are forwarded to claude binary."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
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
