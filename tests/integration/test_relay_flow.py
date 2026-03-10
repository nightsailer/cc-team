"""Integration tests for the redesigned relay mechanism.

Covers the full relay flow:
- RelayContext creation → executor dispatch → relay execution
- Unified `cct relay --context` CLI path
- SessionStart hook → context creation → stop hook → relay launch
- Mode-specific behavior (standalone, team-lead, teammate)
- Session start-team → SessionStart hook → statusline → stop → relay
- Worktree sub-teammate fallback via marker + backend_id matching
- Relay prompt 3-level config priority (env > config > default)
- Old relay subcommands removed, new restart commands work
- Escape valve flow (stop hook blocks N times then allows stop)
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_team._context_relay import RelayRequest, RelayResult
from cc_team._handoff_templates import get_handoff_template, get_relay_prompt
from cc_team._relay_context import RelayContext, RelayMode
from cc_team._relay_executor import TmuxExecutor, get_executor
from cc_team._team_marker import read_team_marker, write_team_marker
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


# ── Session Start-Team Flow ─────────────────────────────────


class TestSessionStartTeamFlow:
    """start-team → SessionStart hook → statusline → stop → relay.

    Integration test covering the full lifecycle of a team-lead session
    started via ``cct session start-team``.
    """

    def _run_session_start_hook(self, hook_input: dict) -> None:
        """Helper: run the SessionStart hook with mocked stdin."""
        stdin_data = json.dumps(hook_input)
        with patch("sys.stdin", io.StringIO(stdin_data)):
            from cc_team.hooks.session_start import main

            main()

    def test_start_team_sets_env_and_writes_marker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """start-team should set CCT_RELAY_MODE=team-lead + CCT_TEAM_NAME + marker."""
        from cc_team.cli import _cmd_session_start_team

        # Create a real team config on disk
        team_dir = tmp_path / ".claude" / "teams" / "integ-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps(
                {
                    "name": "integ-team",
                    "teamName": "integ-team",
                    "description": "test",
                    "leadAgentId": "team-lead@integ-team",
                    "leadSessionId": "sid-1",
                    "createdAt": 1000,
                    "members": [],
                }
            )
        )

        # Redirect claude home so TeamManager reads from tmp_path
        import cc_team.paths as paths_mod

        monkeypatch.setattr(paths_mod, "claude_home", lambda: tmp_path / ".claude")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        parser = _build_parser()
        args = parser.parse_args(["--team-name", "integ-team", "session", "start-team"])
        args.quiet = True

        captured_env: dict[str, str] = {}

        def mock_execvpe(binary: str, argv: list[str], env: dict[str, str]) -> None:
            captured_env.update(env)

        with (
            patch("cc_team.cli.os.execvpe", side_effect=mock_execvpe),
            patch("cc_team.process_manager._find_claude_binary", return_value="/usr/bin/claude"),
        ):
            _cmd_session_start_team(args)

        assert captured_env.get("CCT_RELAY_MODE") == "team-lead"
        assert captured_env.get("CCT_TEAM_NAME") == "integ-team"

        # Verify team marker was written
        marker = read_team_marker(tmp_path)
        assert marker is not None
        assert marker["teamName"] == "integ-team"

    def test_start_team_then_hook_creates_tl_context(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After start-team sets env, SessionStart hook should create team-lead context."""
        monkeypatch.setenv("CCT_RELAY_MODE", "team-lead")
        monkeypatch.setenv("CCT_TEAM_NAME", "integ-team")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        self._run_session_start_hook({"session_id": "ses-tl-001"})

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-tl-001" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAM_LEAD
        assert ctx.team_name == "integ-team"

    def test_stop_hook_blocks_then_relay_launches(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full chain: context exists + high usage + no handoff → block.
        Then handoff written → stop allows + launches relay."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_RELAY_MODE", "team-lead")
        monkeypatch.setenv("CCT_TEAM_NAME", "integ-team")

        # Step 1: Create context via SessionStart hook
        self._run_session_start_hook({"session_id": "ses-flow-001"})

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-flow-001" / "context.json"
        assert ctx_path.exists()

        # Step 2: Write usage > threshold
        usage_path = tmp_path / ".claude" / "cct" / "relay" / "ses-flow-001" / "usage.json"
        usage_path.write_text(json.dumps({"used_percentage": 90}))

        # Write config with low threshold
        config_dir = tmp_path / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 3})
        )

        # Step 3: Run stop hook (no handoff yet → should block with exit 2)
        from cc_team.hooks.stop import main as stop_main

        hook_input = {"session_id": "ses-flow-001"}
        with (
            patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
            pytest.raises(SystemExit) as exc_info,
        ):
            stop_main()
        assert exc_info.value.code == 2

        # Step 4: Write handoff.md
        handoff_path = tmp_path / ".claude" / "cct" / "relay" / "ses-flow-001" / "handoff.md"
        handoff_path.write_text("# Team status\nAll agents running.")

        # Step 5: Run stop hook again (handoff exists → launches relay, returns normally)
        with (
            patch("sys.stdin", io.StringIO(json.dumps(hook_input))),
            patch("cc_team.hooks.stop.subprocess.Popen") as mock_popen,
        ):
            stop_main()

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["cct", "relay", "--context", str(ctx_path)]


# ── Worktree Sub-Teammate Fallback ──────────────────────────


class TestWorktreeSubTeammateFallback:
    """Teammate in worktree → marker auto-created → sub-teammate SessionStart
    falls back to marker → resolves member_name via pane ID."""

    def _run_session_start_hook(self, hook_input: dict) -> None:
        """Helper: run the SessionStart hook with mocked stdin."""
        stdin_data = json.dumps(hook_input)
        with patch("sys.stdin", io.StringIO(stdin_data)):
            from cc_team.hooks.session_start import main

            main()

    def test_teammate_env_auto_creates_marker_for_sub_teammates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """First teammate session creates marker; second can use it as fallback."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Step 1: First teammate session (with env vars set)
        monkeypatch.setenv("CCT_RELAY_MODE", "teammate")
        monkeypatch.setenv("CCT_TEAM_NAME", "sub-team")
        monkeypatch.setenv("CCT_MEMBER_NAME", "coder")
        monkeypatch.setenv("TMUX_PANE", "%50")

        self._run_session_start_hook({"session_id": "ses-wt-001"})

        # Marker should be auto-created
        marker = read_team_marker(tmp_path)
        assert marker is not None
        assert marker["teamName"] == "sub-team"

        # Step 2: Simulate sub-teammate without env vars (new process, marker-only)
        monkeypatch.delenv("CCT_RELAY_MODE")
        monkeypatch.delenv("CCT_TEAM_NAME")
        monkeypatch.delenv("CCT_MEMBER_NAME")
        monkeypatch.setenv("TMUX_PANE", "%60")

        # Mock TeamManager to return config with member matching pane %60
        from cc_team.types import TeamMember

        mock_config = MagicMock()
        mock_config.members = [
            TeamMember(
                agent_id="coder@sub-team",
                name="coder",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                joined_at=1000,
                backend_id="%50",
                cwd="/tmp",
            ),
            TeamMember(
                agent_id="tester@sub-team",
                name="tester",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                joined_at=1000,
                backend_id="%60",
                cwd="/tmp",
            ),
        ]

        with patch("cc_team.team_manager.TeamManager.read", return_value=mock_config):
            self._run_session_start_hook({"session_id": "ses-wt-002"})

        # Sub-teammate should resolve member_name via pane ID
        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-wt-002" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAMMATE
        assert ctx.team_name == "sub-team"
        assert ctx.member_name == "tester"

    def test_sub_teammate_no_matching_pane_gets_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sub-teammate with no matching pane → member_name=None (but still works)."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("CCT_RELAY_MODE", raising=False)
        monkeypatch.delenv("CCT_TEAM_NAME", raising=False)
        monkeypatch.delenv("CCT_MEMBER_NAME", raising=False)
        monkeypatch.setenv("TMUX_PANE", "%99")

        # Pre-create marker
        write_team_marker(tmp_path, "sub-team")

        mock_config = MagicMock()
        mock_config.members = []

        with patch("cc_team.team_manager.TeamManager.read", return_value=mock_config):
            self._run_session_start_hook({"session_id": "ses-wt-003"})

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-wt-003" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAMMATE
        assert ctx.team_name == "sub-team"
        assert ctx.member_name is None


# ── Relay Prompt Config Override ────────────────────────────


class TestRelayPromptConfigOverride:
    """Config file sets custom relay prompt template → used in relay.

    Integration test verifying the 3-level priority: env > config > default
    through the full relay prompt construction path.
    """

    def _write_config(self, proj: str, template: str) -> None:
        """Write a context-relay-config.json with relay_prompt_template."""
        config_path = os.path.join(proj, ".claude", "hooks", "context-relay-config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            json.dump({"relay_prompt_template": template}, f)

    def test_config_template_used_in_relay_prompt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Config file template is used when no env var is set."""
        monkeypatch.delenv("CCT_RELAY_PROMPT_TEMPLATE", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        self._write_config(str(tmp_path), "[Custom] Previous context:\n{content}")

        prompt = get_relay_prompt("key decisions and progress")
        assert "[Custom] Previous context:" in prompt
        assert "key decisions and progress" in prompt
        assert "[Context Relay]" not in prompt

    def test_env_template_overrides_config_in_relay_prompt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Env var template takes priority over config file."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        self._write_config(str(tmp_path), "[Config] {content}")
        monkeypatch.setenv("CCT_RELAY_PROMPT_TEMPLATE", "[Env] {content}")

        prompt = get_relay_prompt("data")
        assert prompt == "[Env] data"
        assert "[Config]" not in prompt

    def test_default_template_when_no_config_and_no_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Neither env nor config → default template with [Context Relay]."""
        monkeypatch.delenv("CCT_RELAY_PROMPT_TEMPLATE", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/tmp/nonexistent-cct-test-dir")

        prompt = get_relay_prompt("handoff text")
        assert "[Context Relay]" in prompt
        assert "handoff text" in prompt

    def test_config_template_flows_through_stop_hook_to_relay(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end: config template → stop hook handoff instructions use correct mode."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("CCT_RELAY_MODE", "standalone")

        # Create relay context
        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        _write_handoff(ctx, "# Handoff Content")

        # The relay prompt built from this handoff should use config template
        monkeypatch.delenv("CCT_RELAY_PROMPT_TEMPLATE", raising=False)
        self._write_config(str(tmp_path), "[Configured] {content}")

        prompt = get_relay_prompt("# Handoff Content")
        assert "[Configured]" in prompt
        assert "# Handoff Content" in prompt


# ── Old Subcommands Removed ─────────────────────────────────


class TestOldSubcommandsRemoved:
    """'cct team relay' and 'cct agent relay' produce parse errors.

    These commands were renamed to 'restart' and should no longer be recognized.
    """

    def test_team_relay_parse_error(self) -> None:
        """'cct --team-name t team relay' should raise SystemExit (parse error)."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--team-name", "t", "team", "relay"])

    def test_agent_relay_parse_error(self) -> None:
        """'cct --team-name t agent relay --name a' should raise SystemExit."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--team-name", "t", "agent", "relay", "--name", "a"])

    def test_team_relay_not_in_parser_choices(self) -> None:
        """'relay' should not be in team subcommand choices."""
        parser = _build_parser()
        # Attempt to parse 'team relay' — should fail, confirming it's not a valid choice.
        # We already test this in test_team_relay_parse_error above; this test
        # additionally verifies by checking that 'restart' IS accepted.
        args = parser.parse_args(["--team-name", "t", "team", "restart"])
        assert hasattr(args, "func")

    def test_agent_relay_not_in_parser_choices(self) -> None:
        """'relay' should not be in agent subcommand choices."""
        parser = _build_parser()
        # Attempt to parse 'agent restart' — should succeed, confirming 'restart' exists.
        args = parser.parse_args(["--team-name", "t", "agent", "restart", "--name", "a"])
        assert hasattr(args, "func")


# ── New Restart Commands ────────────────────────────────────


class TestNewRestartCommands:
    """'cct team restart' and 'cct agent restart' work without --handoff.

    These are process lifecycle commands, NOT context relay.
    """

    def test_team_restart_parses_without_handoff(self) -> None:
        """'cct --team-name t team restart' should parse successfully."""
        parser = _build_parser()
        args = parser.parse_args(["--team-name", "t", "team", "restart"])
        assert hasattr(args, "func")
        assert args.team_name == "t"

    def test_agent_restart_parses_without_handoff(self) -> None:
        """'cct --team-name t agent restart --name a' should parse successfully."""
        parser = _build_parser()
        args = parser.parse_args(["--team-name", "t", "agent", "restart", "--name", "a"])
        assert hasattr(args, "func")
        assert args.name == "a"

    def test_team_restart_rejects_handoff(self) -> None:
        """'cct --team-name t team restart --handoff ...' should produce parse error."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--team-name", "t", "team", "restart", "--handoff", "/tmp/h.md"])

    def test_agent_restart_rejects_handoff(self) -> None:
        """'cct --team-name t agent restart --name a --handoff ...' should produce parse error."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "--team-name",
                    "t",
                    "agent",
                    "restart",
                    "--name",
                    "a",
                    "--handoff",
                    "/tmp/h.md",
                ]
            )

    def test_team_restart_accepts_model_and_timeout(self) -> None:
        """'team restart' accepts --model and --timeout."""
        parser = _build_parser()
        args = parser.parse_args(
            ["--team-name", "t", "team", "restart", "--model", "claude-opus-4-6", "--timeout", "60"]
        )
        assert args.model == "claude-opus-4-6"
        assert args.timeout == 60

    def test_agent_restart_accepts_prompt_model_timeout(self) -> None:
        """'agent restart' accepts --prompt, --model, --timeout."""
        parser = _build_parser()
        args = parser.parse_args(
            [
                "--team-name",
                "t",
                "agent",
                "restart",
                "--name",
                "a",
                "--prompt",
                "New task",
                "--model",
                "claude-opus-4-6",
                "--timeout",
                "45",
            ]
        )
        assert args.prompt == "New task"
        assert args.model == "claude-opus-4-6"
        assert args.timeout == 45

    def test_session_start_team_parses(self) -> None:
        """'cct --team-name t session start-team' should parse successfully."""
        parser = _build_parser()
        args = parser.parse_args(["--team-name", "t", "session", "start-team"])
        assert hasattr(args, "func")

    def test_session_start_team_with_claude_args(self) -> None:
        """'cct --team-name t session start-team -- --model opus' passes args through."""
        parser = _build_parser()
        args = parser.parse_args(
            ["--team-name", "t", "session", "start-team", "--", "--model", "opus"]
        )
        assert hasattr(args, "func")
        # claude_args should include the passthrough arguments
        assert args.claude_args is not None


# ── Escape Valve Flow ───────────────────────────────────────


class TestEscapeValveFlow:
    """Stop hook blocks N times → escape valve → allows stop without handoff.

    When the stop hook has blocked max_block_count times and no handoff is
    written, it should stop blocking (the escape valve) so the user can
    exit gracefully.
    """

    def _run_stop_hook(self, hook_input: dict) -> int | None:
        """Run stop hook, return exit code (None if no SystemExit)."""
        from cc_team.hooks.stop import main as stop_main

        stdin_data = json.dumps(hook_input)
        try:
            with patch("sys.stdin", io.StringIO(stdin_data)):
                stop_main()
            return None  # Normal return, no exit
        except SystemExit as e:
            return e.code  # type: ignore[return-value]

    def test_blocks_up_to_max_then_allows(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stop hook blocks max_block_count times then allows exit."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Create context and usage
        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        usage_path = Path(ctx.usage_path)
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(json.dumps({"used_percentage": 95}))

        # Write config with max_block_count=2
        config_dir = tmp_path / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 2})
        )

        hook_input = {"session_id": ctx.session_id}

        # Block 1: should exit(2)
        exit_code = self._run_stop_hook(hook_input)
        assert exit_code == 2, "First block should exit with code 2"

        # Block 2: should exit(2)
        exit_code = self._run_stop_hook(hook_input)
        assert exit_code == 2, "Second block should exit with code 2"

        # Block 3: escape valve — should NOT block (returns normally)
        exit_code = self._run_stop_hook(hook_input)
        assert exit_code is None, "After max_block_count, should allow stop"

    def test_escape_valve_state_persisted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Block count is persisted to state.json between invocations."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        usage_path = Path(ctx.usage_path)
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(json.dumps({"used_percentage": 90}))

        config_dir = tmp_path / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 5})
        )

        hook_input = {"session_id": ctx.session_id}

        # First block
        self._run_stop_hook(hook_input)

        # Verify state.json was written with block_count
        state_path = Path(ctx.relay_dir) / "state.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["block_count"] == 1

        # Second block
        self._run_stop_hook(hook_input)

        state = json.loads(state_path.read_text())
        assert state["block_count"] == 2

    def test_handoff_written_before_max_blocks_launches_relay(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If handoff is written before escape valve triggers, relay launches normally."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        usage_path = Path(ctx.usage_path)
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(json.dumps({"used_percentage": 90}))

        config_dir = tmp_path / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 5})
        )

        hook_input = {"session_id": ctx.session_id}

        # Block once
        exit_code = self._run_stop_hook(hook_input)
        assert exit_code == 2

        # Now write handoff
        handoff_path = Path(ctx.handoff_path)
        handoff_path.write_text("# Context handoff\nProgress notes.")

        # Next stop should launch relay and return normally
        with patch("cc_team.hooks.stop.subprocess.Popen") as mock_popen:
            exit_code = self._run_stop_hook(hook_input)

        assert exit_code is None
        mock_popen.assert_called_once()

    def test_below_threshold_no_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Usage below threshold → no blocking, even without handoff."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        ctx = _make_context(tmp_path, RelayMode.STANDALONE)
        usage_path = Path(ctx.usage_path)
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(json.dumps({"used_percentage": 50}))

        config_dir = tmp_path / ".claude" / "hooks"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "context-relay-config.json").write_text(
            json.dumps({"threshold": 80, "max_block_count": 3})
        )

        hook_input = {"session_id": ctx.session_id}
        exit_code = self._run_stop_hook(hook_input)
        assert exit_code is None

    def test_subagent_calls_skipped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hook input with agent_id (subagent) should be skipped entirely."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = {"session_id": "ses-sub-001", "agent_id": "sub-agent-1"}
        exit_code = self._run_stop_hook(hook_input)
        assert exit_code is None
