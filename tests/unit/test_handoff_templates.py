"""Tests for handoff templates and relay prompt construction."""

from __future__ import annotations

import pytest

from cc_team._handoff_templates import get_handoff_template, get_relay_prompt
from cc_team._relay_context import RelayMode


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
        prompt = get_relay_prompt("handoff content here")
        assert "handoff content here" in prompt

    def test_relay_prompt_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CCT_RELAY_PROMPT_TEMPLATE", "Custom: {content}")
        prompt = get_relay_prompt("handoff content")
        assert prompt == "Custom: handoff content"

    def test_relay_prompt_default_template(self) -> None:
        prompt = get_relay_prompt("test content")
        assert "test content" in prompt
        assert "[Context Relay]" in prompt
