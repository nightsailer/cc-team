"""Integration tests for the redesigned relay mechanism.

Covers the full relay flow:
- RelayContext creation → executor dispatch → relay execution
- Unified `cct relay --context` CLI path
- SessionStart hook → context creation → stop hook → relay launch
- Mode-specific behavior (standalone, team-lead, teammate)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_team._context_relay import RelayRequest, RelayResult
from cc_team._handoff_templates import get_handoff_template, get_relay_prompt
from cc_team._relay_context import RelayContext, RelayMode
from cc_team._relay_executor import TmuxExecutor, get_executor
from cc_team.cli import _build_parser, _cmd_relay_unified

# ── Helpers ──────────────────────────────────────────────────


def _make_context(
    tmp_path: Path,
    mode: RelayMode = RelayMode.STANDALONE,
    **overrides: object,
) -> RelayContext:
    """Create and save a RelayContext to tmp_path."""
    defaults = {
        "session_id": "ses-integ-001",
        "mode": mode,
        "team_name": "integ-team" if mode != RelayMode.STANDALONE else None,
        "member_name": "worker" if mode == RelayMode.TEAMMATE else None,
        "backend_type": "tmux",
        "backend_id": "%42",
        "project_dir": str(tmp_path),
        "created_at": 1000,
        "created_by": "test",
    }
    defaults.update(overrides)
    ctx = RelayContext(**defaults)  # type: ignore[arg-type]
    ctx.save(Path(ctx.context_path))
    return ctx


def _write_handoff(ctx: RelayContext, content: str = "# Test Handoff") -> Path:
    """Write a handoff.md file in the relay directory."""
    path = Path(ctx.handoff_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# ── RelayContext Lifecycle ───────────────────────────────────


class TestRelayContextLifecycle:
    """Test RelayContext creation, persistence, and path derivation."""

    def test_context_save_load_roundtrip(self, tmp_path: Path) -> None:
        """Save context → load → fields match."""
        ctx = _make_context(tmp_path)
        loaded = RelayContext.load(ctx.context_path)
        assert loaded is not None
        assert loaded.session_id == ctx.session_id
        assert loaded.mode == ctx.mode
        assert loaded.backend_type == ctx.backend_type

    def test_relay_dir_structure(self, tmp_path: Path) -> None:
        """Context creates the expected directory layout."""
        ctx = _make_context(tmp_path)
        relay_dir = Path(ctx.relay_dir)
        assert relay_dir.exists()
        assert (relay_dir / "context.json").exists()

    def test_handoff_and_usage_paths(self, tmp_path: Path) -> None:
        """Derived paths point to correct locations."""
        ctx = _make_context(tmp_path)
        assert ctx.handoff_path.endswith("handoff.md")
        assert ctx.usage_path.endswith("usage.json")
        assert ctx.session_id in ctx.relay_dir


# ── Handoff Templates ───────────────────────────────────────


class TestHandoffTemplateIntegration:
    """Test per-mode template selection and prompt construction."""

    def test_each_mode_has_distinct_template(self) -> None:
        """All three modes produce different templates."""
        templates = {
            mode: get_handoff_template(mode)
            for mode in (RelayMode.STANDALONE, RelayMode.TEAM_LEAD, RelayMode.TEAMMATE)
        }
        assert len(set(templates.values())) == 3

    def test_relay_prompt_includes_handoff_content(self) -> None:
        """get_relay_prompt wraps handoff content correctly."""
        prompt = get_relay_prompt("Important context here")
        assert "Important context here" in prompt


# ── Executor Dispatch ────────────────────────────────────────


class TestExecutorDispatch:
    """Test executor selection and mode-based dispatch."""

    def test_tmux_executor_registered(self) -> None:
        """'tmux' backend type returns TmuxExecutor."""
        executor = get_executor("tmux")
        assert isinstance(executor, TmuxExecutor)

    @pytest.mark.asyncio
    async def test_standalone_dispatch(self, tmp_path: Path) -> None:
        """Standalone context dispatches to _relay_standalone."""
        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        _write_handoff(ctx)
        request = RelayRequest(handoff_path=ctx.handoff_path)

        executor = TmuxExecutor()
        mock_result = RelayResult(
            old_backend_id="%42",
            new_backend_id="%42",
            session_id=ctx.session_id,
            handoff_injected=True,
        )

        with patch.object(
            executor, "_relay_standalone", new_callable=AsyncMock, return_value=mock_result
        ):
            result = await executor.execute(ctx, request)

        assert result.session_id == ctx.session_id
        assert result.handoff_injected is True

    @pytest.mark.asyncio
    async def test_team_lead_dispatch(self, tmp_path: Path) -> None:
        """Team lead context dispatches to _relay_lead."""
        ctx = _make_context(tmp_path, RelayMode.TEAM_LEAD)
        _write_handoff(ctx)
        request = RelayRequest(handoff_path=ctx.handoff_path)

        executor = TmuxExecutor()
        mock_result = RelayResult(
            old_backend_id="%42",
            new_backend_id="%50",
            session_id=ctx.session_id,
            handoff_injected=True,
        )

        with patch.object(
            executor, "_relay_lead", new_callable=AsyncMock, return_value=mock_result
        ):
            result = await executor.execute(ctx, request)

        assert result.new_backend_id == "%50"

    @pytest.mark.asyncio
    async def test_teammate_dispatch(self, tmp_path: Path) -> None:
        """Teammate context dispatches to _relay_agent."""
        ctx = _make_context(tmp_path, RelayMode.TEAMMATE)
        _write_handoff(ctx)
        request = RelayRequest(handoff_path=ctx.handoff_path)

        executor = TmuxExecutor()
        mock_result = RelayResult(
            old_backend_id="%42",
            new_backend_id="%60",
            session_id=ctx.session_id,
            handoff_injected=True,
        )

        with patch.object(
            executor, "_relay_agent", new_callable=AsyncMock, return_value=mock_result
        ):
            result = await executor.execute(ctx, request)

        assert result.new_backend_id == "%60"


# ── Unified CLI Command ─────────────────────────────────────


class TestUnifiedRelayFlow:
    """Test the full `cct relay --context` CLI flow."""

    @pytest.mark.asyncio
    async def test_full_cli_flow(self, tmp_path: Path) -> None:
        """CLI: parse args → load context → dispatch executor → output."""
        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        _write_handoff(ctx, "# Full Flow Handoff\nKey decisions here.")

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(
            return_value=RelayResult(
                old_backend_id="%42",
                new_backend_id="%42",
                session_id=ctx.session_id,
                handoff_injected=True,
            )
        )

        parser = _build_parser()
        args = parser.parse_args(["relay", "--context", ctx.context_path])
        args.use_json = False

        with patch("cc_team._relay_executor.get_executor", return_value=mock_executor):
            await _cmd_relay_unified(args, ctx.context_path)

        mock_executor.execute.assert_awaited_once()
        # Verify the request was constructed from context
        call_args = mock_executor.execute.call_args
        req = call_args[0][1]
        assert req.handoff_path == ctx.handoff_path

    @pytest.mark.asyncio
    async def test_cli_with_handoff_override(self, tmp_path: Path) -> None:
        """CLI: --handoff overrides default from context."""
        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        override_path = tmp_path / "custom-handoff.md"
        override_path.write_text("# Custom")

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(
            return_value=RelayResult(
                old_backend_id="%42",
                new_backend_id="%42",
                session_id=ctx.session_id,
                handoff_injected=True,
            )
        )

        parser = _build_parser()
        args = parser.parse_args(
            [
                "relay",
                "--context",
                ctx.context_path,
                "--handoff",
                str(override_path),
            ]
        )
        args.use_json = False

        with patch("cc_team._relay_executor.get_executor", return_value=mock_executor):
            await _cmd_relay_unified(args, ctx.context_path)

        call_args = mock_executor.execute.call_args
        req = call_args[0][1]
        assert req.handoff_path == str(override_path)


# ── Stop Hook → Relay Launch ────────────────────────────────


class TestStopHookRelayIntegration:
    """Test that stop hook correctly triggers relay via context path."""

    def test_stop_hook_launches_relay_with_context(self, tmp_path: Path) -> None:
        """When handoff.md exists, stop hook launches cct relay --context."""
        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        _write_handoff(ctx)

        # Write usage.json so stop hook can read it
        usage_path = Path(ctx.usage_path)
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(json.dumps({"used_percentage": 85}))

        from cc_team.hooks.stop import _launch_relay_background

        with patch("cc_team.hooks.stop.subprocess.Popen") as mock_popen:
            _launch_relay_background(ctx.context_path)

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["cct", "relay", "--context", ctx.context_path]
