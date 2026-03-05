"""Unit tests for cct setup command.

Covers:
- Default: prints plugin directory path
- --install: creates symlink at expected location
- _find_plugin_dir: installed vs editable mode
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cc_team.cli import _find_plugin_dir, main


class TestFindPluginDir:
    """_find_plugin_dir resolution tests."""

    def setup_method(self) -> None:
        """Clear lru_cache before each test."""
        _find_plugin_dir.cache_clear()

    def test_installed_mode(self, tmp_path: Path) -> None:
        """Returns package-internal plugin/ when it exists."""
        pkg_dir = tmp_path / "cc_team"
        pkg_dir.mkdir()
        plugin_dir = pkg_dir / "plugin"
        plugin_dir.mkdir()
        cli_py = pkg_dir / "cli.py"
        cli_py.touch()

        with patch("cc_team.cli.__file__", str(cli_py)):
            result = _find_plugin_dir()
        assert result == str(plugin_dir)

    def test_editable_mode(self, tmp_path: Path) -> None:
        """Falls back to project_root/plugin/ in editable/dev mode."""
        src = tmp_path / "src" / "cc_team"
        src.mkdir(parents=True)
        cli_py = src / "cli.py"
        cli_py.touch()
        project_plugin = tmp_path / "plugin"
        # Note: dir may not exist yet in dev checkout; function returns path regardless
        with patch("cc_team.cli.__file__", str(cli_py)):
            result = _find_plugin_dir()
        assert result == str(project_plugin)


class TestSetup:
    """cct setup tests."""

    def test_prints_plugin_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Default mode prints plugin directory path."""
        with patch("cc_team.cli._find_plugin_dir", return_value="/fake/plugin"):
            main(["setup"])

        output = capsys.readouterr().out
        assert "/fake/plugin" in output
        assert "install" in output.lower()  # should mention install instructions

    def test_prints_plugin_path_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        """JSON mode returns plugin_dir key."""
        with patch("cc_team.cli._find_plugin_dir", return_value="/fake/plugin"):
            main(["--json", "setup"])

        import json

        output = json.loads(capsys.readouterr().out)
        assert output["plugin_dir"] == "/fake/plugin"

    def test_install_creates_symlink(self, tmp_path: Path) -> None:
        """--install creates symlink from ~/.claude/plugins/cc-team to plugin dir."""
        fake_home = tmp_path / "home"
        fake_plugin = tmp_path / "plugin"
        fake_plugin.mkdir()

        with (
            patch("cc_team.cli._find_plugin_dir", return_value=str(fake_plugin)),
            patch("pathlib.Path.home", return_value=fake_home),
        ):
            main(["setup", "--install"])

        link = fake_home / ".claude" / "plugins" / "cc-team"
        assert link.is_symlink()
        assert str(link.resolve()) == str(fake_plugin.resolve())

    def test_install_existing_link_noop(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--install with existing link is a no-op."""
        fake_home = tmp_path / "home"
        plugins_dir = fake_home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True)
        link = plugins_dir / "cc-team"
        link.symlink_to(tmp_path)

        with (
            patch("cc_team.cli._find_plugin_dir", return_value=str(tmp_path)),
            patch("pathlib.Path.home", return_value=fake_home),
        ):
            main(["setup", "--install"])

        output = capsys.readouterr().out
        assert "already exists" in output.lower()
