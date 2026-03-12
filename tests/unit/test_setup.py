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

from cc_team.cli import (
    _CCT_HOOKS_CONFIG,
    _check_setup_status,
    _merge_hooks_into_settings,
    _remove_hooks_from_settings,
    main,
)


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


class TestCheckSetupStatus:
    """_check_setup_status unit tests."""

    def test_not_installed(self, tmp_path: Path) -> None:
        """Returns not_installed when settings file does not exist."""
        settings = tmp_path / ".claude" / "settings.local.json"
        result = _check_setup_status(settings)
        assert result["status"] == "not_installed"
        assert result["file_exists"] is False

    def test_installed(self, tmp_path: Path) -> None:
        """Returns installed when hooks and statusLine match."""
        settings = tmp_path / "settings.local.json"
        settings.write_text(json.dumps(dict(_CCT_HOOKS_CONFIG)))
        result = _check_setup_status(settings)
        assert result["status"] == "installed"
        assert result["hooks_match"] is True
        assert result["statusline_match"] is True

    def test_outdated(self, tmp_path: Path) -> None:
        """Returns outdated when both keys present but don't match."""
        settings = tmp_path / "settings.local.json"
        settings.write_text(json.dumps({"hooks": {"Old": []}, "statusLine": {"old": True}}))
        result = _check_setup_status(settings)
        assert result["status"] == "outdated"
        assert result["hooks_match"] is False

    def test_partial(self, tmp_path: Path) -> None:
        """Returns partial when only one key is present."""
        settings = tmp_path / "settings.local.json"
        settings.write_text(json.dumps({"hooks": _CCT_HOOKS_CONFIG["hooks"]}))
        result = _check_setup_status(settings)
        assert result["status"] == "partial"
        assert result["hooks_match"] is True
        assert result["statusline_installed"] is False

    def test_corrupt_json(self, tmp_path: Path) -> None:
        """Returns not_installed on corrupt JSON."""
        settings = tmp_path / "settings.local.json"
        settings.write_text("not json")
        result = _check_setup_status(settings)
        assert result["status"] == "not_installed"
        assert result["file_exists"] is True


class TestSetup:
    """cct setup CLI tests."""

    def test_default_shows_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Default mode (no flags) shows current setup status."""
        main(["setup"])

        output = capsys.readouterr().out
        assert "status" in output.lower()

    def test_default_json_shows_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        """JSON mode returns status object."""
        main(["--json", "setup"])

        data = json.loads(capsys.readouterr().out)
        assert "status" in data

    def test_default_installed_shows_ok(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Default mode shows 'up to date' when hooks are installed."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        main(["setup", "--install"])
        capsys.readouterr()  # discard install output

        main(["setup"])
        output = capsys.readouterr().out
        assert "up to date" in output.lower()

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

    def test_uninstall_removes_hooks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--uninstall removes CCT hooks from settings."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        main(["setup", "--install"])
        main(["setup", "--uninstall"])

        output = capsys.readouterr().out
        assert "removed" in output.lower()

        settings = tmp_path / ".claude" / "settings.local.json"
        # File deleted because no other keys remained
        assert not settings.exists()

    def test_uninstall_preserves_other_keys(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--uninstall preserves non-CCT settings."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        settings = tmp_path / ".claude" / "settings.local.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        existing = {**_CCT_HOOKS_CONFIG, "env": {"KEY": "val"}}
        settings.write_text(json.dumps(existing))

        main(["setup", "--uninstall"])

        data = json.loads(settings.read_text())
        assert "hooks" not in data
        assert "statusLine" not in data
        assert data["env"] == {"KEY": "val"}

    def test_uninstall_not_configured(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--uninstall on clean project prints not found."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        main(["setup", "--uninstall"])

        output = capsys.readouterr().out
        assert "not found" in output.lower()

    def test_uninstall_json(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--uninstall --json returns proper status."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        main(["setup", "--install"])
        capsys.readouterr()  # discard install output
        main(["--json", "setup", "--uninstall"])

        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "uninstalled"

    def test_install_and_uninstall_mutually_exclusive(self) -> None:
        """Cannot pass both --install and --uninstall."""
        with pytest.raises(SystemExit):
            main(["setup", "--install", "--uninstall"])


class TestRemoveHooksFromSettings:
    """_remove_hooks_from_settings unit tests."""

    def test_no_file(self, tmp_path: Path) -> None:
        """Returns not_configured when file does not exist."""
        settings = tmp_path / "settings.local.json"
        result = _remove_hooks_from_settings(settings)
        assert result["status"] == "not_configured"

    def test_removes_cct_keys(self, tmp_path: Path) -> None:
        """Removes hooks and statusLine keys."""
        settings = tmp_path / "settings.local.json"
        existing = {**_CCT_HOOKS_CONFIG, "env": {"A": "1"}}
        settings.write_text(json.dumps(existing))

        result = _remove_hooks_from_settings(settings)
        assert result["status"] == "uninstalled"

        data = json.loads(settings.read_text())
        assert "hooks" not in data
        assert "statusLine" not in data
        assert data["env"] == {"A": "1"}

    def test_deletes_empty_file(self, tmp_path: Path) -> None:
        """Deletes file when it becomes empty after removal."""
        settings = tmp_path / "settings.local.json"
        settings.write_text(json.dumps(dict(_CCT_HOOKS_CONFIG)))

        result = _remove_hooks_from_settings(settings)
        assert result["status"] == "uninstalled"
        assert not settings.exists()

    def test_not_configured(self, tmp_path: Path) -> None:
        """Returns not_configured when no CCT hooks present."""
        settings = tmp_path / "settings.local.json"
        settings.write_text(json.dumps({"env": {"X": "1"}}))

        result = _remove_hooks_from_settings(settings)
        assert result["status"] == "not_configured"

    def test_partial_match_removes_matched(self, tmp_path: Path) -> None:
        """Removes only matching CCT keys (e.g. hooks match but statusLine differs)."""
        settings = tmp_path / "settings.local.json"
        data = {"hooks": _CCT_HOOKS_CONFIG["hooks"], "statusLine": {"custom": True}}
        settings.write_text(json.dumps(data))

        result = _remove_hooks_from_settings(settings)
        assert result["status"] == "uninstalled"

        remaining = json.loads(settings.read_text())
        assert "hooks" not in remaining
        assert remaining["statusLine"] == {"custom": True}
