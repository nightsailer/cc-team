"""Unit tests for 'cct session start-team' command.

Covers:
- Sets CCT_RELAY_MODE=team-lead env
- Sets CCT_TEAM_NAME env
- Does NOT set CCT_SESSION_ID
- Writes team marker
- Calls check_stale_marker
- Stale marker with active team aborts
- Stale marker with dead team warns and cleans
- Requires --team-name
- Validates team exists
- Execs claude with passthrough args
- Does not create relay directory
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cc_team._team_marker import read_team_marker, write_team_marker


def _make_team_config(team_name: str, tmp_path: Path) -> None:
    """Create a minimal team config on disk so TeamManager.read() succeeds."""
    from cc_team._serialization import atomic_write_json, now_ms, team_config_to_dict
    from cc_team.types import TeamConfig, TeamMember

    config = TeamConfig(
        name=team_name,
        description="test team",
        created_at=now_ms(),
        lead_agent_id=f"team-lead@{team_name}",
        lead_session_id="",
        members=[
            TeamMember(
                agent_id=f"team-lead@{team_name}",
                name="team-lead",
                agent_type="team-lead",
                model="claude-sonnet-4-6",
                joined_at=now_ms(),
                backend_id="",
                cwd=str(tmp_path),
            ),
        ],
    )
    from cc_team import paths

    config_path = paths.team_config_path(team_name)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(config_path, team_config_to_dict(config))


class TestSessionStartTeam:
    """Test the 'cct session start-team' command."""

    def test_sets_relay_mode_team_lead(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Env must contain CCT_RELAY_MODE=team-lead."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        _make_team_config("test-team", tmp_path)

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
        ):
            from cc_team.cli import main

            main(["--team-name", "test-team", "--quiet", "session", "start-team"])

        env = mock_execvpe.call_args[0][2]
        assert env["CCT_RELAY_MODE"] == "team-lead"

    def test_sets_team_name_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Env must contain CCT_TEAM_NAME=<name>."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        _make_team_config("my-project", tmp_path)

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
        ):
            from cc_team.cli import main

            main(["--team-name", "my-project", "--quiet", "session", "start-team"])

        env = mock_execvpe.call_args[0][2]
        assert env["CCT_TEAM_NAME"] == "my-project"

    def test_does_not_set_cct_session_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CCT_SESSION_ID must NOT be set (design doc: only native session_id)."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))
        # Make sure it's not already in the environment
        monkeypatch.delenv("CCT_SESSION_ID", raising=False)

        _make_team_config("test-team", tmp_path)

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
        ):
            from cc_team.cli import main

            main(["--team-name", "test-team", "--quiet", "session", "start-team"])

        env = mock_execvpe.call_args[0][2]
        assert "CCT_SESSION_ID" not in env

    def test_writes_team_marker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Must write team-marker.json before exec."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        _make_team_config("test-team", tmp_path)

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
        ):
            from cc_team.cli import main

            main(["--team-name", "test-team", "--quiet", "session", "start-team"])

        # Verify team marker was written
        marker = read_team_marker(tmp_path)
        assert marker is not None
        assert marker["teamName"] == "test-team"

    def test_calls_check_stale_marker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Must call check_stale_marker before proceeding."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        _make_team_config("test-team", tmp_path)

        mock_check = MagicMock(return_value=None)

        with (
            patch("cc_team.cli.os.execvpe", MagicMock()),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
            patch("cc_team._team_marker.check_stale_marker", mock_check),
        ):
            from cc_team.cli import main

            main(["--team-name", "test-team", "--quiet", "session", "start-team"])

        mock_check.assert_called_once()

    def test_stale_marker_active_team_aborts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If active team exists, abort with error."""
        from cc_team._team_marker import TeamMarkerConflictError

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        _make_team_config("test-team", tmp_path)

        with (
            patch("cc_team.cli.os.execvpe", MagicMock()) as mock_exec,
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
            patch(
                "cc_team._team_marker.check_stale_marker",
                side_effect=TeamMarkerConflictError("Active team 'old-team' exists"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from cc_team.cli import main

            main(["--team-name", "test-team", "--quiet", "session", "start-team"])

        assert exc_info.value.code == 1
        mock_exec.assert_not_called()

    def test_stale_marker_dead_team_warns_and_cleans(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """If stale marker, warn + clean + proceed."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        _make_team_config("test-team", tmp_path)

        # Write a stale marker from a different team
        write_team_marker(tmp_path, "old-dead-team")

        stale_marker = {"teamName": "old-dead-team"}

        with (
            patch("cc_team.cli.os.execvpe", MagicMock()),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
            patch("cc_team._team_marker.check_stale_marker", return_value=stale_marker),
        ):
            from cc_team.cli import main

            main(["--team-name", "test-team", "session", "start-team"])

        # Should have warned on stderr
        captured = capsys.readouterr()
        assert "old-dead-team" in captured.err
        assert "stale" in captured.err.lower() or "Warning" in captured.err

    def test_requires_team_name(self) -> None:
        """Must error if --team-name not provided."""
        with pytest.raises(SystemExit) as exc_info:
            from cc_team.cli import main

            main(["session", "start-team"])

        # Should exit with error (argparse error or our validation)
        assert exc_info.value.code != 0

    def test_validates_team_exists(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Team config must exist (cct team create must have been run)."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        # Do NOT create team config — it should fail validation
        with (
            patch("cc_team.cli.os.execvpe", MagicMock()) as mock_exec,
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from cc_team.cli import main

            main(["--team-name", "nonexistent-team", "--quiet", "session", "start-team"])

        assert exc_info.value.code == 1
        mock_exec.assert_not_called()

    def test_execs_claude_with_passthrough_args(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Remaining args passed to os.execvpe."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        _make_team_config("test-team", tmp_path)

        mock_execvpe = MagicMock()

        with (
            patch("cc_team.cli.os.execvpe", mock_execvpe),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
        ):
            from cc_team.cli import main

            main(
                [
                    "--team-name",
                    "test-team",
                    "--quiet",
                    "session",
                    "start-team",
                    "--",
                    "--model",
                    "opus",
                    "--verbose",
                ]
            )

        call_args = mock_execvpe.call_args[0]
        assert call_args[0] == "/usr/bin/claude"
        argv = call_args[1]
        assert argv[0] == "/usr/bin/claude"
        assert "--model" in argv
        assert "opus" in argv
        assert "--verbose" in argv

    def test_does_not_create_relay_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Relay dir creation is SessionStart hook's job, not CLI's."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_DATA_DIR", str(tmp_path / ".cct"))

        _make_team_config("test-team", tmp_path)

        with (
            patch("cc_team.cli.os.execvpe", MagicMock()),
            patch(
                "cc_team.process_manager._find_claude_binary",
                return_value="/usr/bin/claude",
            ),
        ):
            from cc_team.cli import main

            main(["--team-name", "test-team", "--quiet", "session", "start-team"])

        # Relay directory should NOT be created by the CLI command
        relay_dir = tmp_path / ".claude" / "cct" / "relay"
        assert not relay_dir.exists()
