"""~/.claude/ 路径常量管理。

所有文件系统路径通过本模块集中管理，方便测试时 monkeypatch 替换根目录。
"""

from __future__ import annotations

from pathlib import Path


def claude_home() -> Path:
    """返回 Claude 主目录，默认 ~/.claude。

    测试时可通过 monkeypatch 替换此函数指向 tmp_path。
    """
    return Path.home() / ".claude"


# ── 团队相关路径 ────────────────────────────────────────────


def teams_dir() -> Path:
    """~/.claude/teams/"""
    return claude_home() / "teams"


def team_dir(team_name: str) -> Path:
    """~/.claude/teams/{team_name}/"""
    return teams_dir() / team_name


def team_config_path(team_name: str) -> Path:
    """~/.claude/teams/{team_name}/config.json"""
    return team_dir(team_name) / "config.json"


def team_config_lock_path(team_name: str) -> Path:
    """~/.claude/teams/{team_name}/config.json.lock"""
    return team_dir(team_name) / "config.json.lock"


# ── 任务相关路径 ────────────────────────────────────────────


def tasks_dir(team_name: str) -> Path:
    """~/.claude/tasks/{team_name}/"""
    return claude_home() / "tasks" / team_name


def task_file_path(team_name: str, task_id: str) -> Path:
    """~/.claude/tasks/{team_name}/{task_id}.json"""
    return tasks_dir(team_name) / f"{task_id}.json"


def tasks_lock_path(team_name: str) -> Path:
    """~/.claude/tasks/{team_name}/.lock（目录级共享锁）"""
    return tasks_dir(team_name) / ".lock"


# ── Inbox 相关路径 ──────────────────────────────────────────


def inboxes_dir(team_name: str) -> Path:
    """~/.claude/teams/{team_name}/inboxes/"""
    return team_dir(team_name) / "inboxes"


def inbox_path(team_name: str, agent_name: str) -> Path:
    """~/.claude/teams/{team_name}/inboxes/{agent_name}.json"""
    return inboxes_dir(team_name) / f"{agent_name}.json"


def inbox_lock_path(team_name: str, agent_name: str) -> Path:
    """~/.claude/teams/{team_name}/inboxes/{agent_name}.json.lock"""
    return inboxes_dir(team_name) / f"{agent_name}.json.lock"
