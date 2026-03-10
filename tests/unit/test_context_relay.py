"""Unit tests for cc_team._context_relay — core relay logic.

Covers:
- relay_lead(): rotate + spawn_lead + sync + initial prompt
- relay_agent(): remove + respawn with handoff prompt
- _update_history(): appends correctly
- _read_handoff() / get_relay_prompt()
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_team._context_relay import (
    RelayRequest,
    _read_handoff,
    _update_history,
    relay_agent,
    relay_lead,
)
from cc_team._handoff_templates import get_relay_prompt
from cc_team.tmux import PaneState

# ── Helpers ────────────────────────────────────────────────


def _make_request(**overrides: object) -> RelayRequest:
    defaults = {
        "handoff_path": "/tmp/handoff.md",
        "model": "claude-sonnet-4-6",
        "timeout": 10,
        "cwd": "/workspace",
    }
    defaults.update(overrides)
    return RelayRequest(**defaults)  # type: ignore[arg-type]


def _make_mock_tmux() -> MagicMock:
    mock = MagicMock()
    mock.send_command = AsyncMock()
    mock.detect_state = AsyncMock(return_value=PaneState.READY)
    mock.is_pane_alive = AsyncMock(return_value=True)
    mock.kill_pane = AsyncMock()
    return mock


# ── _read_handoff / get_relay_prompt ───────────────────────


class TestReadHandoff:
    """_read_handoff() tests."""

    def test_reads_file_content(self, tmp_path: Path) -> None:
        """Reads file content correctly."""
        f = tmp_path / "handoff.md"
        f.write_text("# My Handoff\nDetails here.")
        assert _read_handoff(str(f)) == "# My Handoff\nDetails here."

    def test_raises_on_missing_file(self) -> None:
        """Raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            _read_handoff("/nonexistent/path.md")


class TestGetRelayPrompt:
    """get_relay_prompt() tests."""

    def test_wraps_content_with_header(self) -> None:
        """Wraps content in relay context header."""
        result = get_relay_prompt("Hello", source_path="/path/to/file.md")
        assert "[Context Relay]" in result
        assert "Hello" in result
        assert "/path/to/file.md" in result
        assert "Continue working" in result

    def test_without_source_path(self) -> None:
        """Works without source_path."""
        result = get_relay_prompt("Hello")
        assert "[Context Relay]" in result
        assert "Hello" in result
        assert "Source:" not in result


# ── _update_history ────────────────────────────────────────


class TestUpdateHistory:
    """_update_history() tests."""

    def test_appends_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Appends session entry to history file."""
        import cc_team.paths as paths_mod

        monkeypatch.setattr(paths_mod, "claude_home", lambda: tmp_path)

        _update_history("cct-1", "cc-session-1")

        history_path = tmp_path / "relay-history.json"
        assert history_path.exists()
        entries = json.loads(history_path.read_text())
        assert len(entries) == 1
        assert entries[0]["session_id"] == "cct-1"
        assert entries[0]["new_cc_session_id"] == "cc-session-1"

    def test_appends_multiple(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple calls append to existing history."""
        import cc_team.paths as paths_mod

        monkeypatch.setattr(paths_mod, "claude_home", lambda: tmp_path)

        _update_history("cct-1", "cc-1")
        _update_history("cct-2", "cc-2")

        entries = json.loads((tmp_path / "relay-history.json").read_text())
        assert len(entries) == 2


# ── relay_lead ─────────────────────────────────────────────


class TestRelayLead:
    """relay_lead() tests."""

    @pytest.mark.asyncio
    async def test_rotate_spawn_sync(self, tmp_path: Path) -> None:
        """relay_lead calls graceful_exit, rotate, spawn_lead with prompt, sync."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text("# Lead Handoff")

        request = _make_request(handoff_path=str(handoff))

        from cc_team.types import TEAM_LEAD_AGENT_TYPE, TeamConfig, TeamMember

        mock_member = TeamMember(
            agent_id="team-lead@test",
            name=TEAM_LEAD_AGENT_TYPE,
            agent_type=TEAM_LEAD_AGENT_TYPE,
            model="claude-sonnet-4-6",
            joined_at=0,
            backend_id="%5",
            cwd="/workspace",
        )
        mock_config = TeamConfig(
            name="test-team",
            description="test",
            created_at=0,
            lead_agent_id="team-lead@test",
            lead_session_id="old-sid",
            members=[mock_member],
        )

        mock_mgr = MagicMock()
        mock_mgr.read.return_value = mock_config
        mock_mgr.rotate_session = AsyncMock(return_value="new-sid")
        mock_mgr.update_member = AsyncMock()

        mock_pm = MagicMock()
        mock_pm.graceful_exit = AsyncMock()
        mock_pm.spawn_lead = AsyncMock(return_value="%5")
        mock_pm.detect_ready = AsyncMock(return_value=True)

        mock_tmux = _make_mock_tmux()

        with (
            patch("cc_team._context_relay.TmuxManager", return_value=mock_tmux),
            patch("cc_team._context_relay.ProcessManager", return_value=mock_pm),
            patch("cc_team._context_relay.TeamManager", return_value=mock_mgr),
            patch(
                "cc_team._context_relay.sync_member_states",
                new_callable=AsyncMock,
            ),
            patch("cc_team._context_relay._update_history"),
        ):
            result = await relay_lead(request, "test-team")

        mock_pm.graceful_exit.assert_awaited_once()
        mock_mgr.rotate_session.assert_awaited_once()
        mock_pm.spawn_lead.assert_awaited_once()
        assert result.new_backend_id == "%5"
        assert result.handoff_injected is True

    @pytest.mark.asyncio
    async def test_lead_relay_passes_prompt_to_spawn_lead(self, tmp_path: Path) -> None:
        """relay_lead passes handoff prompt via SpawnLeadOptions.prompt, NOT _inject_handoff."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text("# Lead Handoff Content")

        request = _make_request(handoff_path=str(handoff))

        from cc_team.types import TEAM_LEAD_AGENT_TYPE, TeamConfig, TeamMember

        mock_member = TeamMember(
            agent_id="team-lead@test",
            name=TEAM_LEAD_AGENT_TYPE,
            agent_type=TEAM_LEAD_AGENT_TYPE,
            model="claude-sonnet-4-6",
            joined_at=0,
            backend_id="%5",
            cwd="/workspace",
        )
        mock_config = TeamConfig(
            name="test-team",
            description="test",
            created_at=0,
            lead_agent_id="team-lead@test",
            lead_session_id="old-sid",
            members=[mock_member],
        )

        mock_mgr = MagicMock()
        mock_mgr.read.return_value = mock_config
        mock_mgr.rotate_session = AsyncMock(return_value="new-sid")
        mock_mgr.update_member = AsyncMock()

        mock_pm = MagicMock()
        mock_pm.graceful_exit = AsyncMock()
        mock_pm.spawn_lead = AsyncMock(return_value="%5")

        mock_tmux = _make_mock_tmux()

        with (
            patch("cc_team._context_relay.TmuxManager", return_value=mock_tmux),
            patch("cc_team._context_relay.ProcessManager", return_value=mock_pm),
            patch("cc_team._context_relay.TeamManager", return_value=mock_mgr),
            patch(
                "cc_team._context_relay.sync_member_states",
                new_callable=AsyncMock,
            ),
            patch("cc_team._context_relay._update_history"),
        ):
            await relay_lead(request, "test-team")

        # Verify spawn_lead received options with prompt field set
        spawn_call = mock_pm.spawn_lead.call_args
        spawn_options = spawn_call[0][0]  # first positional arg = SpawnLeadOptions
        assert spawn_options.prompt != ""
        assert "[Context Relay]" in spawn_options.prompt
        assert "Lead Handoff Content" in spawn_options.prompt


# ── relay_agent ────────────────────────────────────────────


class TestRelayAgent:
    """relay_agent() tests."""

    @pytest.mark.asyncio
    async def test_remove_and_respawn(self, tmp_path: Path) -> None:
        """relay_agent removes old member and respawns with handoff."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text("# Agent Handoff")

        request = _make_request(handoff_path=str(handoff))

        from cc_team.types import TeamConfig, TeamMember

        mock_member = TeamMember(
            agent_id="worker@test",
            name="worker",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            joined_at=0,
            backend_id="%10",
            cwd="/workspace",
            is_active=True,
        )
        mock_config = TeamConfig(
            name="test-team",
            description="test",
            created_at=0,
            lead_agent_id="team-lead@test",
            lead_session_id="lead-sid",
            members=[mock_member],
        )

        mock_mgr = MagicMock()
        mock_mgr.get_member.return_value = mock_member
        mock_mgr.remove_member = AsyncMock()
        mock_mgr.read.return_value = mock_config

        mock_pm = MagicMock()
        mock_pm.graceful_exit = AsyncMock()

        mock_tmux = _make_mock_tmux()
        mock_spawn = AsyncMock(return_value=("%20", "blue"))

        with (
            patch("cc_team._context_relay.TmuxManager", return_value=mock_tmux),
            patch("cc_team._context_relay.ProcessManager", return_value=mock_pm),
            patch("cc_team._context_relay.TeamManager", return_value=mock_mgr),
            patch("cc_team._context_relay.spawn_agent_workflow", mock_spawn),
            patch("cc_team._context_relay._update_history"),
        ):
            result = await relay_agent(request, "test-team", "worker")

        mock_pm.graceful_exit.assert_awaited_once_with("%10", timeout=10)
        mock_mgr.remove_member.assert_awaited_once_with("worker")
        mock_spawn.assert_awaited_once()
        assert result.new_backend_id == "%20"
        assert result.handoff_injected is True  # prompt is the handoff
