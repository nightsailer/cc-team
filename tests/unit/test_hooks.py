"""Unit tests for cc_team.hooks.stop and cc_team.hooks.statusline.

Covers stop.main():
- No session_id in hook input → silent exit
- Below threshold → no block
- Above threshold, no handoff → blocks (exit 2)
- Above threshold, with handoff → launches unified relay (mock Popen)
- Subagent (agent_id in hook input) → skip
- Escape valve: block_count >= max_block_count → allows through

Covers statusline.main():
- Always writes usage.json using native session_id
- Renders colored status bar to stdout

Covers CLI _hook subcommands:
- cct _hook stop delegates to stop.main()
- cct _hook statusline delegates to statusline.main()
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _mock_stdin(data: str = "{}") -> io.StringIO:
    """Create a StringIO to mock sys.stdin for hooks."""
    return io.StringIO(data)


def _setup_relay_dir(proj: str, session_id: str) -> Path:
    """Create relay directory for a session and return its path."""
    relay_dir = Path(proj) / ".claude" / "cct" / "relay" / session_id
    relay_dir.mkdir(parents=True, exist_ok=True)
    return relay_dir


def _setup_config(proj: str, **overrides: object) -> None:
    """Create context-relay-config.json with defaults."""
    defaults = {"threshold": 80, "max_block_count": 3, "team_name": ""}
    defaults.update(overrides)  # type: ignore[arg-type]
    config_dir = Path(proj) / ".claude" / "hooks"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "context-relay-config.json").write_text(json.dumps(defaults))


class TestStopMain:
    """cc_team.hooks.stop.main() tests."""

    def test_no_session_id_exits_silently(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No session_id in hook input → returns silently."""
        monkeypatch.setattr("sys.stdin", _mock_stdin(json.dumps({})))
        from cc_team.hooks.stop import main

        main()

    def test_below_threshold_no_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Usage below threshold → no block, exits normally."""
        sid = "test-session-below"
        proj = str(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        hook_input = json.dumps({"session_id": sid})
        monkeypatch.setattr("sys.stdin", _mock_stdin(hook_input))

        from cc_team.hooks._common import write_json

        relay_dir = _setup_relay_dir(proj, sid)
        write_json(str(relay_dir / "usage.json"), {"used_percentage": 50})
        _setup_config(proj)

        from cc_team.hooks.stop import main

        main()

    def test_above_threshold_no_handoff_blocks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Usage above threshold, no handoff.md → blocks with exit(2)."""
        sid = "test-session-block"
        proj = str(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        hook_input = json.dumps({"session_id": sid})
        monkeypatch.setattr("sys.stdin", _mock_stdin(hook_input))

        from cc_team.hooks._common import write_json

        relay_dir = _setup_relay_dir(proj, sid)
        write_json(str(relay_dir / "usage.json"), {"used_percentage": 85})
        _setup_config(proj)

        from cc_team.hooks.stop import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_above_threshold_with_handoff_launches_unified_relay(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Handoff.md exists → launches unified 'cct relay --context', exits 0."""
        sid = "test-session-relay"
        proj = str(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        hook_input = json.dumps({"session_id": sid})
        monkeypatch.setattr("sys.stdin", _mock_stdin(hook_input))

        from cc_team.hooks._common import write_json

        relay_dir = _setup_relay_dir(proj, sid)
        write_json(str(relay_dir / "usage.json"), {"used_percentage": 90})
        (relay_dir / "handoff.md").write_text("# Handoff content")
        # context.json must also exist for relay to launch.
        write_json(str(relay_dir / "context.json"), {"sessionId": sid, "mode": "standalone"})
        _setup_config(proj)

        mock_popen = MagicMock()
        with patch("cc_team.hooks.stop.subprocess.Popen", mock_popen):
            from cc_team.hooks.stop import main

            main()

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd[:2] == ["cct", "relay"]
        assert "--context" in cmd
        context_path = str(relay_dir / "context.json")
        assert context_path in cmd

    def test_subagent_skips(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """agent_id in hook input → skip (subagent), exit 0."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = json.dumps({"session_id": "sid", "agent_id": "abc123"})
        monkeypatch.setattr("sys.stdin", _mock_stdin(hook_input))

        from cc_team.hooks.stop import main

        main()

    def test_escape_valve_allows_through(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """block_count >= max_block_count → allows stop (escape valve)."""
        sid = "test-session-escape"
        proj = str(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        hook_input = json.dumps({"session_id": sid})
        monkeypatch.setattr("sys.stdin", _mock_stdin(hook_input))

        from cc_team.hooks._common import write_json

        relay_dir = _setup_relay_dir(proj, sid)
        write_json(str(relay_dir / "usage.json"), {"used_percentage": 90})
        write_json(str(relay_dir / "state.json"), {"block_count": 3})
        _setup_config(proj)

        from cc_team.hooks.stop import main

        main()

    def test_block_increments_count(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each block increments block_count in state.json."""
        sid = "test-session-count"
        proj = str(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", proj)

        hook_input = json.dumps({"session_id": sid})
        monkeypatch.setattr("sys.stdin", _mock_stdin(hook_input))

        from cc_team.hooks._common import read_json, write_json

        relay_dir = _setup_relay_dir(proj, sid)
        write_json(str(relay_dir / "usage.json"), {"used_percentage": 85})
        write_json(str(relay_dir / "state.json"), {"block_count": 0})
        _setup_config(proj)

        from cc_team.hooks.stop import main

        with pytest.raises(SystemExit):
            main()

        state = read_json(str(relay_dir / "state.json"))
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

    def test_always_writes_usage_with_native_session_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Always writes usage.json using native session_id from hook input."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("CCT_SESSION_ID", raising=False)

        input_data = self._make_input()
        monkeypatch.setattr("sys.stdin", _mock_stdin(input_data))

        from cc_team.hooks.statusline import main

        main()

        # Verify usage.json was written using native session_id path
        from cc_team.hooks._common import read_json

        usage_path = str(tmp_path / ".claude" / "cct" / "relay" / "sess-1" / "usage.json")
        usage = read_json(usage_path)
        assert usage["session_id"] == "sess-1"
        assert usage["used_percentage"] == 45.0
        assert usage["agent_name"] == ""

        # Also check stdout has status bar
        output = capsys.readouterr().out
        assert "45.0%" in output

    def test_renders_status_bar(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Renders colored status bar to stdout."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        input_data = self._make_input()
        monkeypatch.setattr("sys.stdin", _mock_stdin(input_data))

        from cc_team.hooks.statusline import main

        main()

        output = capsys.readouterr().out
        assert "45.0%" in output

    def test_no_session_id_in_data_exits_silently(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Empty session_id in input data → silent exit."""
        monkeypatch.setattr("sys.stdin", _mock_stdin())
        from cc_team.hooks.statusline import main

        main()
        assert capsys.readouterr().out == ""

    def test_parse_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Invalid JSON input → silent exit (no session_id in empty dict)."""
        monkeypatch.setattr("sys.stdin", _mock_stdin("not json"))
        from cc_team.hooks.statusline import main

        main()
        assert capsys.readouterr().out == ""


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
        monkeypatch.setattr("sys.stdin", _mock_stdin())

        from cc_team.cli import main

        main(["_hook", "statusline"])
        # Empty session_id → no output
        assert capsys.readouterr().out == ""
