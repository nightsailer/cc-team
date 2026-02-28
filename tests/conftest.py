"""cc-team 测试全局 fixtures。

提供路径隔离、时间戳控制、tmux mock 等测试基础设施。
"""

from __future__ import annotations

import pytest
from pathlib import Path

import cc_team.paths as paths_mod
import cc_team._serialization as ser_mod


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
