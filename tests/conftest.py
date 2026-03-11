"""cc-team test fixtures.

Provides path isolation, timestamp control, tmux mock, TeamMember factory,
and shared test helpers (run_session_start_hook, make_relay_request).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import cc_team._serialization as ser_mod
import cc_team.inbox as inbox_mod
import cc_team.paths as paths_mod
import cc_team.team_manager as tm_mod
from cc_team._context_relay import RelayRequest
from cc_team.types import TeamMember

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
    """Read raw JSON from agent inbox file."""
    path = paths_mod.inbox_path(team_name, agent_name)
    if not path.exists():
        return []
    return json.loads(path.read_text())


# ── TeamMember factory ────────────────────────────────────────


def make_member(name: str = "worker-1", **overrides: Any) -> TeamMember:
    """Factory for TeamMember with sensible defaults.

    Args:
        name: Agent name (also used to derive agent_id).
        **overrides: Any TeamMember field to override.

    Returns:
        TeamMember instance with defaults merged with overrides.
    """
    defaults: dict[str, Any] = {
        "agent_id": f"{name}@test-team",
        "name": name,
        "agent_type": "general-purpose",
        "model": "claude-sonnet-4-6",
        "joined_at": FIXED_MS,
        "backend_id": "%1",
        "cwd": "/workspace",
        "color": "blue",
        "is_active": True,
        "backend_type": "tmux",
    }
    defaults.update(overrides)
    return TeamMember(**defaults)


# ── Shared test helpers ────────────────────────────────────────


def run_session_start_hook(hook_input: dict, env: dict | None = None) -> None:
    """Run the SessionStart hook main() with mocked stdin and optional env.

    Args:
        hook_input: JSON-serializable dict to feed as stdin.
        env: Optional env-var overrides (patched into os.environ).
    """
    stdin_data = json.dumps(hook_input)
    with patch("sys.stdin", io.StringIO(stdin_data)):
        if env:
            with patch.dict(os.environ, env, clear=False):
                from cc_team.hooks.session_start import main

                main()
        else:
            from cc_team.hooks.session_start import main

            main()


def make_relay_request(**overrides: object) -> RelayRequest:
    """Create a RelayRequest with sensible test defaults.

    Any keyword argument overrides the corresponding default field.
    """
    defaults: dict[str, object] = {
        "handoff_path": "/tmp/handoff.md",
        "model": "claude-sonnet-4-6",
        "timeout": 10,
        "cwd": "/workspace",
    }
    defaults.update(overrides)
    return RelayRequest(**defaults)  # type: ignore[arg-type]
