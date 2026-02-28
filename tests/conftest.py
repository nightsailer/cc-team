"""cc-team 测试全局 fixtures。

提供路径隔离、时间戳控制、tmux mock 等测试基础设施。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cc_team._serialization as ser_mod
import cc_team.inbox as inbox_mod
import cc_team.paths as paths_mod
import cc_team.team_manager as tm_mod

# ── 路径隔离 ────────────────────────────────────────────────


@pytest.fixture
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将 ~/.claude/ 重定向到 tmp_path 下的隔离目录。

    所有通过 paths 模块获取的路径都指向此临时目录，
    避免测试污染真实文件系统。
    """
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(paths_mod, "claude_home", lambda: home)
    return home


# ── 时间戳控制 ──────────────────────────────────────────────

FIXED_ISO = "2026-02-28T10:00:00.000Z"
FIXED_MS = 1772193600000


@pytest.fixture
def fixed_time(monkeypatch: pytest.MonkeyPatch) -> dict[str, int | str]:
    """固定时间戳输出，确保测试结果确定性。"""
    monkeypatch.setattr(ser_mod, "now_iso", lambda: FIXED_ISO)
    monkeypatch.setattr(ser_mod, "now_ms", lambda: FIXED_MS)
    return {"iso": FIXED_ISO, "ms": FIXED_MS}


# ── 完整隔离（路径 + 时间戳） ─────────────────────────────────


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 ~/.claude/ 到 tmp_path，固定所有时间戳源。

    覆盖 ser_mod / inbox_mod / tm_mod 中的时间戳函数，
    确保跨模块时间戳一致。供 CLI 和集成测试使用。
    """
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(paths_mod, "claude_home", lambda: home)
    monkeypatch.setattr(ser_mod, "now_iso", lambda: FIXED_ISO)
    monkeypatch.setattr(ser_mod, "now_ms", lambda: FIXED_MS)
    monkeypatch.setattr(inbox_mod, "now_iso", lambda: FIXED_ISO)
    monkeypatch.setattr(tm_mod, "now_ms", lambda: FIXED_MS)
    return home


# ── 测试辅助函数 ──────────────────────────────────────────────


def read_inbox(team_name: str, agent_name: str) -> list[dict]:
    """读取 agent inbox 文件原始 JSON。"""
    path = paths_mod.inbox_path(team_name, agent_name)
    if not path.exists():
        return []
    return json.loads(path.read_text())
