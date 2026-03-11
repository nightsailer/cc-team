"""Tests for the SessionStart hook — creates RelayContext at session boot."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import run_session_start_hook

from cc_team._relay_context import RelayContext, RelayMode
from cc_team._team_marker import read_team_marker, write_team_marker


class TestSessionStartHook:
    """Test the SessionStart hook creates RelayContext correctly."""

    def _run_hook(self, hook_input: dict, env: dict | None = None) -> None:
        """Helper: run the hook main() with mocked stdin and env."""
        run_session_start_hook(hook_input, env=env)

    def test_creates_standalone_context_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When CCT_RELAY_MODE=standalone, creates standalone context."""
        monkeypatch.setenv("CCT_RELAY_MODE", "standalone")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = {"session_id": "ses-001"}

        self._run_hook(hook_input)

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-001" / "context.json"
        assert ctx_path.exists()
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.STANDALONE
        assert ctx.session_id == "ses-001"
        assert ctx.team_name is None
        assert ctx.member_name is None

    def test_creates_team_lead_context_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When CCT_RELAY_MODE=team-lead + CCT_TEAM_NAME, creates TL context."""
        monkeypatch.setenv("CCT_RELAY_MODE", "team-lead")
        monkeypatch.setenv("CCT_TEAM_NAME", "alpha")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = {"session_id": "ses-002"}

        self._run_hook(hook_input)

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-002" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAM_LEAD
        assert ctx.team_name == "alpha"

    def test_creates_teammate_context_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When CCT_RELAY_MODE=teammate + CCT_TEAM_NAME + CCT_MEMBER_NAME."""
        monkeypatch.setenv("CCT_RELAY_MODE", "teammate")
        monkeypatch.setenv("CCT_TEAM_NAME", "alpha")
        monkeypatch.setenv("CCT_MEMBER_NAME", "researcher")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = {"session_id": "ses-003"}

        self._run_hook(hook_input)

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-003" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAMMATE
        assert ctx.team_name == "alpha"
        assert ctx.member_name == "researcher"

    def test_fallback_reads_marker_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No env vars but marker exists -> creates teammate context."""
        monkeypatch.delenv("CCT_RELAY_MODE", raising=False)
        monkeypatch.delenv("CCT_TEAM_NAME", raising=False)
        monkeypatch.delenv("CCT_MEMBER_NAME", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Write team marker
        from cc_team._team_marker import write_team_marker

        write_team_marker(tmp_path, "beta-team")

        hook_input = {"session_id": "ses-004"}

        self._run_hook(hook_input)

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-004" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAMMATE
        assert ctx.team_name == "beta-team"

    def test_fallback_no_marker_creates_standalone(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No env vars, no marker -> standalone."""
        monkeypatch.delenv("CCT_RELAY_MODE", raising=False)
        monkeypatch.delenv("CCT_TEAM_NAME", raising=False)
        monkeypatch.delenv("CCT_MEMBER_NAME", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = {"session_id": "ses-005"}

        self._run_hook(hook_input)

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-005" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.STANDALONE

    def test_skips_if_context_already_exists(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If context.json already exists, does nothing."""
        monkeypatch.setenv("CCT_RELAY_MODE", "standalone")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Pre-create context
        existing = RelayContext(
            session_id="ses-006",
            mode=RelayMode.STANDALONE,
            team_name=None,
            member_name=None,
            backend_type="tmux",
            backend_id=None,
            project_dir=str(tmp_path),
            created_at=999,
            created_by="pre-existing",
        )
        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-006" / "context.json"
        existing.save(ctx_path)

        hook_input = {"session_id": "ses-006"}
        self._run_hook(hook_input)

        # Verify not overwritten
        loaded = RelayContext.load(ctx_path)
        assert loaded is not None
        assert loaded.created_at == 999
        assert loaded.created_by == "pre-existing"

    def test_no_session_id_skips(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If hook input has no session_id, skip gracefully."""
        monkeypatch.setenv("CCT_RELAY_MODE", "standalone")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = {}  # No session_id

        self._run_hook(hook_input)

        # No context created
        relay_dir = tmp_path / ".claude" / "cct" / "relay"
        assert not relay_dir.exists() or not any(relay_dir.iterdir())


def _make_mock_team_config(members: list[tuple[str, str]]) -> MagicMock:
    """Create a mock TeamConfig with members having (name, backend_id) pairs."""
    from cc_team.types import TeamMember

    mock_config = MagicMock()
    mock_members = []
    for name, backend_id in members:
        m = TeamMember(
            agent_id=f"{name}@test-team",
            name=name,
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            joined_at=1000,
            backend_id=backend_id,
            cwd="/tmp",
        )
        mock_members.append(m)
    mock_config.members = mock_members
    return mock_config


class TestFallbackMemberResolution:
    """Test member_name resolution via backend_id matching in fallback path."""

    def _run_hook(self, hook_input: dict) -> None:
        """Helper: run the hook main() with mocked stdin."""
        run_session_start_hook(hook_input)

    def test_marker_exists_resolves_member_by_pane_id(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Marker + team config with matching pane -> member_name set."""
        monkeypatch.delenv("CCT_RELAY_MODE", raising=False)
        monkeypatch.delenv("CCT_TEAM_NAME", raising=False)
        monkeypatch.delenv("CCT_MEMBER_NAME", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_PANE", "%42")

        write_team_marker(tmp_path, "test-team")

        mock_config = _make_mock_team_config(
            [
                ("team-lead", ""),
                ("researcher", "%42"),
                ("coder", "%43"),
            ]
        )
        mock_mgr = MagicMock()
        mock_mgr.read.return_value = mock_config

        hook_input = {"session_id": "ses-resolve-1"}

        with patch("cc_team.team_manager.TeamManager.read", return_value=mock_config):
            self._run_hook(hook_input)

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-resolve-1" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAMMATE
        assert ctx.team_name == "test-team"
        assert ctx.member_name == "researcher"

    def test_marker_exists_no_matching_pane(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Marker exists but pane doesn't match any member -> member_name=None."""
        monkeypatch.delenv("CCT_RELAY_MODE", raising=False)
        monkeypatch.delenv("CCT_TEAM_NAME", raising=False)
        monkeypatch.delenv("CCT_MEMBER_NAME", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.setenv("TMUX_PANE", "%99")

        write_team_marker(tmp_path, "test-team")

        mock_config = _make_mock_team_config(
            [
                ("team-lead", ""),
                ("researcher", "%42"),
            ]
        )
        mock_mgr = MagicMock()
        mock_mgr.read.return_value = mock_config

        hook_input = {"session_id": "ses-resolve-2"}

        with patch("cc_team.team_manager.TeamManager.read", return_value=mock_config):
            self._run_hook(hook_input)

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-resolve-2" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAMMATE
        assert ctx.team_name == "test-team"
        assert ctx.member_name is None

    def test_marker_exists_no_tmux(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Not in tmux (no TMUX_PANE) -> member_name=None."""
        monkeypatch.delenv("CCT_RELAY_MODE", raising=False)
        monkeypatch.delenv("CCT_TEAM_NAME", raising=False)
        monkeypatch.delenv("CCT_MEMBER_NAME", raising=False)
        monkeypatch.delenv("TMUX_PANE", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        write_team_marker(tmp_path, "test-team")

        hook_input = {"session_id": "ses-resolve-3"}

        # No need to mock TeamManager since _resolve_member_name returns None
        # early when TMUX_PANE is unset.
        self._run_hook(hook_input)

        ctx_path = tmp_path / ".claude" / "cct" / "relay" / "ses-resolve-3" / "context.json"
        ctx = RelayContext.load(ctx_path)
        assert ctx is not None
        assert ctx.mode == RelayMode.TEAMMATE
        assert ctx.team_name == "test-team"
        assert ctx.member_name is None


class TestWorktreeMarkerAutoCreate:
    """Test worktree marker auto-creation in main()."""

    def _run_hook(self, hook_input: dict) -> None:
        """Helper: run the hook main() with mocked stdin."""
        run_session_start_hook(hook_input)

    def test_env_team_mode_no_marker_creates_marker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CCT_RELAY_MODE=team-lead + no marker -> marker auto-created."""
        monkeypatch.setenv("CCT_RELAY_MODE", "team-lead")
        monkeypatch.setenv("CCT_TEAM_NAME", "alpha")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Verify no marker exists before
        assert read_team_marker(tmp_path) is None

        hook_input = {"session_id": "ses-wt-1"}
        self._run_hook(hook_input)

        # Marker should be auto-created
        marker = read_team_marker(tmp_path)
        assert marker is not None
        assert marker["teamName"] == "alpha"
        assert marker["createdBy"] == "session-start-hook-worktree"

    def test_env_teammate_mode_no_marker_creates_marker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CCT_RELAY_MODE=teammate + no marker -> marker auto-created."""
        monkeypatch.setenv("CCT_RELAY_MODE", "teammate")
        monkeypatch.setenv("CCT_TEAM_NAME", "alpha")
        monkeypatch.setenv("CCT_MEMBER_NAME", "coder")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        assert read_team_marker(tmp_path) is None

        hook_input = {"session_id": "ses-wt-2"}
        self._run_hook(hook_input)

        marker = read_team_marker(tmp_path)
        assert marker is not None
        assert marker["teamName"] == "alpha"
        assert marker["createdBy"] == "session-start-hook-worktree"

    def test_env_standalone_no_marker_no_creation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Standalone mode -> no marker created."""
        monkeypatch.setenv("CCT_RELAY_MODE", "standalone")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = {"session_id": "ses-wt-3"}
        self._run_hook(hook_input)

        # No marker should be created
        assert read_team_marker(tmp_path) is None

    def test_marker_already_exists_no_overwrite(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Marker exists -> not overwritten."""
        monkeypatch.setenv("CCT_RELAY_MODE", "team-lead")
        monkeypatch.setenv("CCT_TEAM_NAME", "alpha")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        # Pre-create marker with a different created_by
        write_team_marker(tmp_path, "alpha", created_by="original-creator")
        original_marker = read_team_marker(tmp_path)
        assert original_marker is not None
        original_created_at = original_marker["createdAt"]

        hook_input = {"session_id": "ses-wt-4"}
        self._run_hook(hook_input)

        # Marker should NOT be overwritten
        marker = read_team_marker(tmp_path)
        assert marker is not None
        assert marker["createdBy"] == "original-creator"
        assert marker["createdAt"] == original_created_at

    def test_team_mode_no_team_name_no_marker(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Team mode but no team_name -> no marker created."""
        monkeypatch.setenv("CCT_RELAY_MODE", "team-lead")
        monkeypatch.delenv("CCT_TEAM_NAME", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        hook_input = {"session_id": "ses-wt-5"}
        self._run_hook(hook_input)

        # No marker should be created (team_name is None)
        assert read_team_marker(tmp_path) is None
