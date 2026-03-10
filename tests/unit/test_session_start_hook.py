"""Tests for the SessionStart hook — creates RelayContext at session boot."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_team._relay_context import RelayContext, RelayMode


class TestSessionStartHook:
    """Test the SessionStart hook creates RelayContext correctly."""

    def _run_hook(self, hook_input: dict, env: dict | None = None) -> None:
        """Helper: run the hook main() with mocked stdin and env."""
        import io

        stdin_data = json.dumps(hook_input)
        with patch("sys.stdin", io.StringIO(stdin_data)):
            if env:
                with patch.dict(os.environ, env, clear=False):
                    from cc_team.hooks.session_start import main

                    main()
            else:
                from cc_team.hooks.session_start import main

                main()

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
