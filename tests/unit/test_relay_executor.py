"""Tests for RelayExecutor protocol and TmuxExecutor implementation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_team._context_relay import RelayRequest, RelayResult
from cc_team._relay_context import RelayContext, RelayMode
from cc_team._relay_executor import TmuxExecutor, get_executor


def _make_ctx(
    mode: RelayMode = RelayMode.STANDALONE,
    backend_id: str | None = "%42",
    team_name: str | None = None,
    member_name: str | None = None,
) -> RelayContext:
    return RelayContext(
        session_id="ses-001",
        mode=mode,
        team_name=team_name,
        member_name=member_name,
        backend_type="tmux",
        backend_id=backend_id,
        project_dir="/tmp/proj",
        created_at=1000,
        created_by="test",
    )


def _make_request(handoff_path: str = "/tmp/handoff.md") -> RelayRequest:
    return RelayRequest(
        handoff_path=handoff_path,
        model="claude-sonnet-4-6",
        timeout=10,
        cwd="/workspace",
    )


class TestRelayExecutorRegistry:
    def test_get_tmux_executor(self) -> None:
        executor = get_executor("tmux")
        assert isinstance(executor, TmuxExecutor)

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown backend"):
            get_executor("unknown")


class TestTmuxExecutorStandalone:
    @pytest.mark.asyncio
    async def test_standalone_relay_dispatches(self, tmp_path) -> None:
        """Standalone mode dispatches to _relay_standalone."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text("# Standalone Handoff")

        ctx = _make_ctx(RelayMode.STANDALONE, backend_id="%42")
        request = _make_request(str(handoff))

        executor = TmuxExecutor()

        with (
            patch.object(executor, "_relay_standalone", new_callable=AsyncMock) as mock_relay,
        ):
            mock_relay.return_value = RelayResult(
                old_backend_id="%42",
                new_backend_id="%42",
                session_id="ses-001",
                handoff_injected=True,
            )
            result = await executor.execute(ctx, request)

        mock_relay.assert_awaited_once_with(ctx, request)
        assert result.new_backend_id == "%42"
        assert result.handoff_injected is True

    @pytest.mark.asyncio
    async def test_standalone_relay_uses_initial_prompt(self, tmp_path) -> None:
        """Standalone relay passes handoff as -p flag, NOT via send-keys injection."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text("# Standalone Handoff")

        ctx = _make_ctx(RelayMode.STANDALONE, backend_id="%42")
        request = _make_request(str(handoff))

        executor = TmuxExecutor()
        mock_tmux = MagicMock()
        mock_tmux.send_command = AsyncMock()
        mock_pm = MagicMock()
        mock_pm.graceful_exit = AsyncMock()

        with (
            patch("cc_team._relay_executor.TmuxManager", return_value=mock_tmux),
            patch("cc_team._relay_executor.ProcessManager", return_value=mock_pm),
            patch("cc_team._relay_executor._find_claude_binary", return_value="/usr/bin/claude"),
            patch("cc_team._relay_executor._update_history"),
        ):
            result = await executor._relay_standalone(ctx, request)

        # Verify graceful exit was called
        mock_pm.graceful_exit.assert_awaited_once_with("%42", timeout=10)

        # Verify send_command was called exactly once (for launching new process)
        mock_tmux.send_command.assert_awaited_once()
        spawn_command = mock_tmux.send_command.call_args[0][1]

        # The spawn command must include -p flag with the prompt
        assert " -p " in spawn_command
        assert "[Context Relay]" in spawn_command

        # Verify result
        assert result.handoff_injected is True
        assert result.new_backend_id == "%42"

    @pytest.mark.asyncio
    async def test_standalone_relay_does_not_use_inject_handoff(self, tmp_path) -> None:
        """Confirm _inject_handoff is not imported or used in _relay_executor."""
        import cc_team._relay_executor as mod

        # _inject_handoff must not be accessible from the module
        assert not hasattr(mod, "_inject_handoff")


class TestTmuxExecutorTeamLead:
    @pytest.mark.asyncio
    async def test_lead_relay(self, tmp_path) -> None:
        """Lead: exit → rotate → spawn → inject → sync → update history."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text("# Lead Handoff")

        ctx = _make_ctx(RelayMode.TEAM_LEAD, team_name="my-team")
        request = _make_request(str(handoff))

        executor = TmuxExecutor()

        with (
            patch.object(executor, "_relay_lead", new_callable=AsyncMock) as mock_relay,
        ):
            mock_relay.return_value = RelayResult(
                old_backend_id="%42",
                new_backend_id="%42",
                session_id="ses-001",
                handoff_injected=True,
            )
            result = await executor.execute(ctx, request)

        mock_relay.assert_awaited_once_with(ctx, request)
        assert result.handoff_injected is True


class TestTmuxExecutorTeammate:
    @pytest.mark.asyncio
    async def test_agent_relay(self, tmp_path) -> None:
        """Agent: exit → remove → spawn with prompt → update history."""
        handoff = tmp_path / "handoff.md"
        handoff.write_text("# Agent Handoff")

        ctx = _make_ctx(RelayMode.TEAMMATE, team_name="my-team", member_name="worker")
        request = _make_request(str(handoff))

        executor = TmuxExecutor()

        with (
            patch.object(executor, "_relay_agent", new_callable=AsyncMock) as mock_relay,
        ):
            mock_relay.return_value = RelayResult(
                old_backend_id="%10",
                new_backend_id="%20",
                session_id="ses-001",
                handoff_injected=True,
            )
            result = await executor.execute(ctx, request)

        mock_relay.assert_awaited_once_with(ctx, request)
        assert result.new_backend_id == "%20"
