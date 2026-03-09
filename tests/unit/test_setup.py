"""Unit tests for cct setup command.

Covers:
- Default: prints install instructions
- --install: merges hooks into settings.local.json
- _merge_hooks_into_settings: merge logic
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_team.cli import _CCT_HOOKS_CONFIG, _merge_hooks_into_settings, main


class TestMergeHooksIntoSettings:
    """_merge_hooks_into_settings unit tests."""

    def test_creates_new_file(self, tmp_path: Path) -> None:
        """Creates settings file from scratch when it does not exist."""
        settings = tmp_path / ".claude" / "settings.local.json"
        result = _merge_hooks_into_settings(settings)

        assert result["status"] == "installed"
        data = json.loads(settings.read_text())
        assert data["hooks"] == _CCT_HOOKS_CONFIG["hooks"]
        assert data["statusLine"] == _CCT_HOOKS_CONFIG["statusLine"]

    def test_preserves_existing_keys(self, tmp_path: Path) -> None:
        """Existing settings keys are preserved after merge."""
        settings = tmp_path / "settings.local.json"
        settings.write_text(json.dumps({"env": {"FOO": "bar"}, "other": 123}))

        _merge_hooks_into_settings(settings)

        data = json.loads(settings.read_text())
        assert data["env"] == {"FOO": "bar"}
        assert data["other"] == 123
        assert data["hooks"] == _CCT_HOOKS_CONFIG["hooks"]

    def test_already_configured(self, tmp_path: Path) -> None:
        """Returns already_configured when hooks match exactly."""
        settings = tmp_path / "settings.local.json"
        existing = {**_CCT_HOOKS_CONFIG, "env": {"X": "1"}}
        settings.write_text(json.dumps(existing))

        result = _merge_hooks_into_settings(settings)
        assert result["status"] == "already_configured"

    def test_overwrites_stale_hooks(self, tmp_path: Path) -> None:
        """Replaces outdated hooks config with current version."""
        settings = tmp_path / "settings.local.json"
        settings.write_text(json.dumps({"hooks": {"Stop": []}, "statusLine": {}}))

        result = _merge_hooks_into_settings(settings)
        assert result["status"] == "installed"
        data = json.loads(settings.read_text())
        assert data["hooks"] == _CCT_HOOKS_CONFIG["hooks"]


class TestSetup:
    """cct setup CLI tests."""

    def test_prints_instructions(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Default mode prints install instructions."""
        main(["setup"])

        output = capsys.readouterr().out
        assert "cct setup --install" in output

    def test_prints_instructions_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        """JSON mode returns hint."""
        main(["--json", "setup"])

        data = json.loads(capsys.readouterr().out)
        assert "hint" in data

    def test_install_writes_settings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--install writes hooks into settings.local.json."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        main(["setup", "--install"])

        settings = tmp_path / ".claude" / "settings.local.json"
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert data["hooks"] == _CCT_HOOKS_CONFIG["hooks"]
        assert "installed" in capsys.readouterr().out.lower()

    def test_install_idempotent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Second --install is a no-op."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        main(["setup", "--install"])
        main(["setup", "--install"])

        output = capsys.readouterr().out
        assert "already configured" in output.lower()
