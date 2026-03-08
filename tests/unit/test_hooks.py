"""Unit tests for cc_team.hooks.stop and cc_team.hooks.statusline.

Covers stop.main():
- No CCT_SESSION_ID → silent exit
- Below threshold → no block
- Above threshold, no handoff → blocks (exit 2)
- Above threshold, with handoff → launches relay (mock Popen)
- Subagent (agent_name set) → skip
- Escape valve: block_count >= max_block_count → allows through

Covers statusline.main():
- With CCT_SESSION_ID: writes usage.json
- Without CCT_SESSION_ID: render-only, no file write

Covers CLI _hook subcommands:
- cct _hook stop delegates to stop.main()
- cct _hook statusline delegates to statusline.main()
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestStopMain:
    """cc_team.hooks.stop.main() tests."""

    def test_no_cct_session_id_exits_silently(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No CCT_SESSION_ID env → returns silently (no error)."""
        monkeypatch.delenv("CCT_SESSION_ID", raising=False)
        from cc_team.hooks.stop import main

        # Should not raise or sys.exit
        main()

    def test_below_threshold_no_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Usage below threshold → no block, exits normally."""
        cct_sid = "test-session-below"
        proj = str(tmp_path)
        monkeypatch.setenv("CCT_SESSION_ID", cct_sid)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        # Create usage.json with low usage
        from cc_team.hooks._common import relay_paths, write_json

        paths = relay_paths(cct_sid, proj)
        write_json(paths["usage"], {"used_percentage": 50, "agent_name": ""})

        # Create config
        config_dir = Path(proj) / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 3, "team_name": ""})
        )

        from cc_team.hooks.stop import main

        main()  # Should not raise

    def test_above_threshold_no_handoff_blocks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Usage above threshold, no handoff.md → blocks with exit(2)."""
        cct_sid = "test-session-block"
        proj = str(tmp_path)
        monkeypatch.setenv("CCT_SESSION_ID", cct_sid)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        from cc_team.hooks._common import relay_paths, write_json

        paths = relay_paths(cct_sid, proj)
        write_json(paths["usage"], {"used_percentage": 85, "agent_name": ""})

        config_dir = Path(proj) / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 3, "team_name": ""})
        )

        from cc_team.hooks.stop import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_above_threshold_with_handoff_launches_relay(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Usage above threshold + handoff.md exists → launches relay, exits 0."""
        cct_sid = "test-session-relay"
        proj = str(tmp_path)
        monkeypatch.setenv("CCT_SESSION_ID", cct_sid)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        from cc_team.hooks._common import relay_paths, write_json

        paths = relay_paths(cct_sid, proj)
        write_json(paths["usage"], {"used_percentage": 90, "agent_name": ""})

        # Create handoff.md
        os.makedirs(os.path.dirname(paths["handoff"]), exist_ok=True)
        Path(paths["handoff"]).write_text("# Handoff content")

        config_dir = Path(proj) / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 3, "team_name": ""})
        )

        mock_popen = MagicMock()
        with patch("cc_team.hooks.stop.subprocess.Popen", mock_popen):
            from cc_team.hooks.stop import main

            main()  # Should not raise (exits 0)

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        assert call_args[0][0] == ["cct", "relay"]

    def test_handoff_with_team_name_launches_team_relay(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """team_name in config → launches 'cct team relay' instead."""
        cct_sid = "test-session-team-relay"
        proj = str(tmp_path)
        monkeypatch.setenv("CCT_SESSION_ID", cct_sid)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        from cc_team.hooks._common import relay_paths, write_json

        paths = relay_paths(cct_sid, proj)
        write_json(paths["usage"], {"used_percentage": 90, "agent_name": ""})
        Path(paths["handoff"]).write_text("# Handoff")

        config_dir = Path(proj) / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 3, "team_name": "my-team"})
        )

        mock_popen = MagicMock()
        with patch("cc_team.hooks.stop.subprocess.Popen", mock_popen):
            from cc_team.hooks.stop import main

            main()

        call_args = mock_popen.call_args
        assert call_args[0][0] == ["cct", "--team-name", "my-team", "team", "relay"]

    def test_subagent_skips(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """agent_name set in usage.json → skip (subagent), exit 0."""
        cct_sid = "test-session-subagent"
        proj = str(tmp_path)
        monkeypatch.setenv("CCT_SESSION_ID", cct_sid)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        from cc_team.hooks._common import relay_paths, write_json

        paths = relay_paths(cct_sid, proj)
        write_json(
            paths["usage"],
            {
                "used_percentage": 95,
                "agent_name": "worker-1",
            },
        )

        from cc_team.hooks.stop import main

        main()  # Should not raise — subagent is skipped

    def test_escape_valve_allows_through(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """block_count >= max_block_count → allows stop (escape valve)."""
        cct_sid = "test-session-escape"
        proj = str(tmp_path)
        monkeypatch.setenv("CCT_SESSION_ID", cct_sid)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        from cc_team.hooks._common import relay_paths, write_json

        paths = relay_paths(cct_sid, proj)
        write_json(paths["usage"], {"used_percentage": 90, "agent_name": ""})
        # Pre-set block_count at max
        write_json(paths["state"], {"block_count": 3})

        config_dir = Path(proj) / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 3, "team_name": ""})
        )

        from cc_team.hooks.stop import main

        main()  # Should not raise — escape valve triggered

    def test_block_increments_count(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each block increments block_count in state.json."""
        cct_sid = "test-session-count"
        proj = str(tmp_path)
        monkeypatch.setenv("CCT_SESSION_ID", cct_sid)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        from cc_team.hooks._common import read_json, relay_paths, write_json

        paths = relay_paths(cct_sid, proj)
        write_json(paths["usage"], {"used_percentage": 85, "agent_name": ""})
        write_json(paths["state"], {"block_count": 0})

        config_dir = Path(proj) / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 3, "team_name": ""})
        )

        from cc_team.hooks.stop import main

        with pytest.raises(SystemExit):
            main()

        state = read_json(paths["state"])
        assert state["block_count"] == 1


class TestStatuslineMain:
    """cc_team.hooks.statusline.main() tests."""

    def _make_input(self, **overrides: object) -> str:
        """Build statusline JSON input."""
        data = {
            "session_id": "sess-1",
            "context_window": {
                "context_window_size": 200000,
                "used_percentage": 45.0,
                "current_usage": {
                    "input_tokens": 80000,
                    "cache_creation_input_tokens": 5000,
                    "cache_read_input_tokens": 5000,
                },
            },
            "model": {"display_name": "claude-sonnet-4-6"},
            "cost": {"total_cost_usd": 0.123},
            "agent": {"name": ""},
        }
        data.update(overrides)
        return json.dumps(data)

    def test_writes_usage_with_cct_session_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With CCT_SESSION_ID set, writes usage.json to relay paths."""
        cct_sid = "test-statusline-write"
        monkeypatch.setenv("CCT_SESSION_ID", cct_sid)
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", str(tmp_path))

        input_data = self._make_input()
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(input_data))

        from cc_team.hooks.statusline import main

        main()

        # Verify usage.json was written
        from cc_team.hooks._common import read_json, relay_paths

        paths = relay_paths(cct_sid)
        usage = read_json(paths["usage"])
        assert usage["session_id"] == "sess-1"
        assert usage["used_percentage"] == 45.0
        assert usage["agent_name"] == ""

        # Also check stdout has status bar
        output = capsys.readouterr().out
        assert "45.0%" in output

    def test_render_only_without_cct_session_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without CCT_SESSION_ID, renders status bar but writes no file."""
        monkeypatch.delenv("CCT_SESSION_ID", raising=False)
        monkeypatch.setenv("CCT_PROJECT_DATA_DIR", str(tmp_path))

        input_data = self._make_input()
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(input_data))

        from cc_team.hooks.statusline import main

        main()

        # Should have rendered output
        output = capsys.readouterr().out
        assert "45.0%" in output

        # No relay directory should be created
        relay_dir = tmp_path / "relay"
        assert not relay_dir.exists()

    def test_no_session_id_in_data_exits_silently(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Empty session_id in input data → silent exit."""
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
        from cc_team.hooks.statusline import main

        main()
        assert capsys.readouterr().out == ""

    def test_parse_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Invalid JSON input → prints error indicator."""
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
        from cc_team.hooks.statusline import main

        main()
        output = capsys.readouterr().out
        assert "parse error" in output


class TestHookCLI:
    """CLI _hook subcommand integration tests."""

    def test_hook_stop_delegates_to_main(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cct _hook stop delegates to stop.main()."""
        monkeypatch.delenv("CCT_SESSION_ID", raising=False)

        from cc_team.cli import main

        # No CCT_SESSION_ID → stop.main() returns silently
        main(["_hook", "stop"])

    def test_hook_statusline_delegates_to_main(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cct _hook statusline delegates to statusline.main()."""
        monkeypatch.delenv("CCT_SESSION_ID", raising=False)
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))

        from cc_team.cli import main

        main(["_hook", "statusline"])
        # Empty session_id → no output
        assert capsys.readouterr().out == ""
