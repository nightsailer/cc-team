"""paths.py 单元测试 — 路径生成规则验证。"""

from __future__ import annotations

from pathlib import Path

from cc_team import paths


class TestPathFunctions:
    """所有路径函数应基于 claude_home() 构建正确层级。"""

    def test_claude_home(self, claude_home: Path) -> None:
        """monkeypatch 后 claude_home 指向 tmp_path。"""
        result = paths.claude_home()
        assert result == claude_home
        assert result.name == ".claude"

    def test_teams_dir(self, claude_home: Path) -> None:
        assert paths.teams_dir() == claude_home / "teams"

    def test_team_dir(self, claude_home: Path) -> None:
        assert paths.team_dir("my-team") == claude_home / "teams" / "my-team"

    def test_team_config_path(self, claude_home: Path) -> None:
        result = paths.team_config_path("my-team")
        assert result == claude_home / "teams" / "my-team" / "config.json"

    def test_team_config_lock_path(self, claude_home: Path) -> None:
        result = paths.team_config_lock_path("my-team")
        assert result == claude_home / "teams" / "my-team" / "config.json.lock"

    def test_tasks_dir(self, claude_home: Path) -> None:
        result = paths.tasks_dir("my-team")
        assert result == claude_home / "tasks" / "my-team"

    def test_task_file_path(self, claude_home: Path) -> None:
        result = paths.task_file_path("my-team", "1")
        assert result == claude_home / "tasks" / "my-team" / "1.json"

    def test_tasks_lock_path(self, claude_home: Path) -> None:
        result = paths.tasks_lock_path("my-team")
        assert result == claude_home / "tasks" / "my-team" / ".lock"

    def test_inboxes_dir(self, claude_home: Path) -> None:
        result = paths.inboxes_dir("my-team")
        assert result == claude_home / "teams" / "my-team" / "inboxes"

    def test_inbox_path(self, claude_home: Path) -> None:
        result = paths.inbox_path("my-team", "researcher")
        assert result == claude_home / "teams" / "my-team" / "inboxes" / "researcher.json"

    def test_inbox_lock_path(self, claude_home: Path) -> None:
        result = paths.inbox_lock_path("my-team", "researcher")
        expected = claude_home / "teams" / "my-team" / "inboxes" / "researcher.json.lock"
        assert result == expected
