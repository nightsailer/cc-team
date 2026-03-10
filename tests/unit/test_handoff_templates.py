"""Tests for handoff templates and relay prompt construction."""

from __future__ import annotations

import pytest

from cc_team._handoff_templates import get_handoff_template, get_relay_prompt
from cc_team._relay_context import RelayContext, RelayMode


def _make_ctx(mode: RelayMode = RelayMode.STANDALONE) -> RelayContext:
    return RelayContext(
        session_id="test-123",
        mode=mode,
        team_name="my-team" if mode != RelayMode.STANDALONE else None,
        member_name="worker" if mode == RelayMode.TEAMMATE else None,
        backend_type="tmux",
        backend_id=None,
        project_dir="/tmp/proj",
        created_at=1000,
        created_by="test",
    )


class TestHandoffTemplates:
    def test_standalone_template_exists(self) -> None:
        template = get_handoff_template(RelayMode.STANDALONE)
        assert "task" in template.lower() or "context" in template.lower()

    def test_team_lead_template_exists(self) -> None:
        template = get_handoff_template(RelayMode.TEAM_LEAD)
        assert template != get_handoff_template(RelayMode.STANDALONE)

    def test_teammate_template_exists(self) -> None:
        template = get_handoff_template(RelayMode.TEAMMATE)
        assert template != get_handoff_template(RelayMode.STANDALONE)

    def test_all_modes_return_nonempty(self) -> None:
        for mode in RelayMode:
            assert len(get_handoff_template(mode)) > 0


class TestRelayPrompt:
    def test_relay_prompt_includes_content(self) -> None:
        ctx = _make_ctx()
        prompt = get_relay_prompt(ctx, "handoff content here")
        assert "handoff content here" in prompt

    def test_relay_prompt_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CCT_RELAY_PROMPT_TEMPLATE", "Custom: {content}")
        ctx = _make_ctx()
        prompt = get_relay_prompt(ctx, "handoff content")
        assert prompt == "Custom: handoff content"

    def test_relay_prompt_standalone_mode(self) -> None:
        ctx = _make_ctx(RelayMode.STANDALONE)
        prompt = get_relay_prompt(ctx, "test content")
        assert "test content" in prompt

    def test_relay_prompt_team_lead_mode(self) -> None:
        ctx = _make_ctx(RelayMode.TEAM_LEAD)
        prompt = get_relay_prompt(ctx, "test content")
        assert "test content" in prompt
