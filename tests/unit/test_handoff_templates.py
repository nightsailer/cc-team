"""Tests for handoff templates and relay prompt construction."""

from __future__ import annotations

import json
import os

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


class TestRelayPromptPriority:
    """Test 3-level priority: env var > config file > default."""

    def _write_config(self, proj: str, template: str) -> None:
        """Write a context-relay-config.json with relay_prompt_template."""
        config_path = os.path.join(proj, ".claude", "hooks", "context-relay-config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            json.dump({"relay_prompt_template": template}, f)

    def test_default_template_no_env_no_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No env var, no config file -> uses default template."""
        monkeypatch.delenv("CCT_RELAY_PROMPT_TEMPLATE", raising=False)
        # Point project dir to a non-existent path so config loading fails gracefully.
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/tmp/nonexistent-cct-test-dir")
        prompt = get_relay_prompt("hello world")
        assert "[Context Relay]" in prompt
        assert "hello world" in prompt

    def test_env_overrides_all(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: str,
    ) -> None:
        """Env var set -> uses env, ignores config and default."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        self._write_config(str(tmp_path), "Config template: {content}")
        monkeypatch.setenv("CCT_RELAY_PROMPT_TEMPLATE", "Env wins: {content}")
        prompt = get_relay_prompt("data")
        assert prompt == "Env wins: data"

    def test_config_overrides_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: str,
    ) -> None:
        """Config has template, no env var -> uses config."""
        monkeypatch.delenv("CCT_RELAY_PROMPT_TEMPLATE", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        self._write_config(str(tmp_path), "From config: {content}")
        prompt = get_relay_prompt("data")
        assert prompt == "From config: data"
        assert "[Context Relay]" not in prompt

    def test_env_overrides_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: str,
    ) -> None:
        """Both env and config set -> env wins."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        self._write_config(str(tmp_path), "Config template: {content}")
        monkeypatch.setenv("CCT_RELAY_PROMPT_TEMPLATE", "Env template: {content}")
        prompt = get_relay_prompt("data")
        assert prompt == "Env template: data"
        assert "Config" not in prompt
