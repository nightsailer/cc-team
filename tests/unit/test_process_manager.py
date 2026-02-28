"""process_manager.py 单元测试 — Agent 进程管理验证。

测试覆盖:
- spawn（正常/pane 创建失败/命令发送失败）
- kill（正常/未追踪/pane 已退出）
- untrack
- is_running（运行中/已退出/未追踪）
- 状态查询（get_pane_id / tracked_agents）
- build_cli_args（必选参数/条件参数/权限模式映射）
- _find_claude_binary（环境变量/PATH/fallback）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_team.exceptions import AgentNotFoundError, SpawnError, TmuxError
from cc_team.process_manager import ProcessManager, _find_claude_binary
from cc_team.tmux import TmuxManager
from cc_team.types import SpawnAgentOptions

# ── Mock Helpers ──────────────────────────────────────────────


def _make_mock_tmux() -> MagicMock:
    """创建 mock TmuxManager。"""
    mock = MagicMock(spec=TmuxManager)
    mock.split_window = AsyncMock(return_value="%20")
    mock.send_command = AsyncMock()
    mock.kill_pane = AsyncMock()
    mock.is_pane_alive = AsyncMock(return_value=True)
    return mock


def _make_options(
    name: str = "worker-1",
    prompt: str = "Do the work",
    **kwargs: Any,
) -> SpawnAgentOptions:
    """创建 SpawnAgentOptions。"""
    return SpawnAgentOptions(name=name, prompt=prompt, **kwargs)  # type: ignore[arg-type]


# ── Spawn ────────────────────────────────────────────────────


class TestSpawn:
    """spawn() 测试。"""

    @pytest.mark.asyncio
    async def test_spawn_returns_pane_id(self) -> None:
        """spawn 返回 pane ID。"""
        tmux = _make_mock_tmux()
        pm = ProcessManager(tmux=tmux)

        pane_id = await pm.spawn(
            _make_options(),
            team_name="my-team",
            color="blue",
            parent_session_id="sess-1",
        )
        assert pane_id == "%20"

    @pytest.mark.asyncio
    async def test_spawn_tracks_agent(self) -> None:
        """spawn 后 agent 被追踪。"""
        tmux = _make_mock_tmux()
        pm = ProcessManager(tmux=tmux)

        await pm.spawn(
            _make_options(name="dev"),
            team_name="t",
            color="blue",
            parent_session_id="s",
        )
        assert "dev" in pm.tracked_agents()
        assert pm.get_pane_id("dev") == "%20"

    @pytest.mark.asyncio
    async def test_spawn_calls_split_then_send(self) -> None:
        """spawn 先 split_window 再 send_command。"""
        tmux = _make_mock_tmux()
        pm = ProcessManager(tmux=tmux)

        await pm.spawn(
            _make_options(),
            team_name="t",
            color="blue",
            parent_session_id="s",
        )

        tmux.split_window.assert_awaited_once()
        tmux.send_command.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_spawn_split_failure_raises_spawn_error(self) -> None:
        """split_window 失败时抛出 SpawnError。"""
        tmux = _make_mock_tmux()
        tmux.split_window = AsyncMock(side_effect=TmuxError("no tmux"))
        pm = ProcessManager(tmux=tmux)

        with pytest.raises(SpawnError, match="Failed to create tmux pane"):
            await pm.spawn(
                _make_options(),
                team_name="t",
                color="blue",
                parent_session_id="s",
            )

    @pytest.mark.asyncio
    async def test_spawn_send_failure_cleans_pane(self) -> None:
        """send_command 失败时清理 pane 并抛出 SpawnError。"""
        tmux = _make_mock_tmux()
        tmux.send_command = AsyncMock(side_effect=TmuxError("send failed"))
        pm = ProcessManager(tmux=tmux)

        with pytest.raises(SpawnError, match="Failed to send command"):
            await pm.spawn(
                _make_options(),
                team_name="t",
                color="blue",
                parent_session_id="s",
            )

        # 应尝试 kill_pane 清理
        tmux.kill_pane.assert_awaited_once_with("%20")

    @pytest.mark.asyncio
    async def test_spawn_send_failure_cleanup_failure_suppressed(self) -> None:
        """send 失败后 kill_pane 也失败时不传播清理异常。"""
        tmux = _make_mock_tmux()
        tmux.send_command = AsyncMock(side_effect=TmuxError("send failed"))
        tmux.kill_pane = AsyncMock(side_effect=TmuxError("kill failed"))
        pm = ProcessManager(tmux=tmux)

        with pytest.raises(SpawnError):
            await pm.spawn(
                _make_options(),
                team_name="t",
                color="blue",
                parent_session_id="s",
            )
        # 不应因 kill_pane 失败而抛出不同异常


# ── Kill ─────────────────────────────────────────────────────


class TestKill:
    """kill() 测试。"""

    @pytest.mark.asyncio
    async def test_kill_calls_tmux_and_untracks(self) -> None:
        """kill 调用 tmux.kill_pane 并移除追踪。"""
        tmux = _make_mock_tmux()
        pm = ProcessManager(tmux=tmux)
        await pm.spawn(
            _make_options(name="dev"),
            team_name="t", color="blue", parent_session_id="s",
        )

        await pm.kill("dev")
        tmux.kill_pane.assert_awaited_with("%20")
        assert "dev" not in pm.tracked_agents()

    @pytest.mark.asyncio
    async def test_kill_untracked_raises(self) -> None:
        """kill 未追踪的 agent 抛出 AgentNotFoundError。"""
        pm = ProcessManager(tmux=_make_mock_tmux())
        with pytest.raises(AgentNotFoundError):
            await pm.kill("nobody")

    @pytest.mark.asyncio
    async def test_kill_pane_already_dead(self) -> None:
        """pane 已退出时 kill 不报错（异常被吞）。"""
        tmux = _make_mock_tmux()
        tmux.kill_pane = AsyncMock(side_effect=TmuxError("pane not found"))
        pm = ProcessManager(tmux=tmux)
        await pm.spawn(
            _make_options(name="dev"),
            team_name="t", color="blue", parent_session_id="s",
        )

        await pm.kill("dev")  # 不应抛出
        assert "dev" not in pm.tracked_agents()


# ── Untrack ──────────────────────────────────────────────────


class TestUntrack:
    """untrack() 测试。"""

    @pytest.mark.asyncio
    async def test_untrack_removes_from_list(self) -> None:
        """untrack 从追踪列表移除。"""
        tmux = _make_mock_tmux()
        pm = ProcessManager(tmux=tmux)
        await pm.spawn(
            _make_options(name="dev"),
            team_name="t", color="blue", parent_session_id="s",
        )

        pm.untrack("dev")
        assert "dev" not in pm.tracked_agents()

    def test_untrack_nonexistent_noop(self) -> None:
        """untrack 不存在的 agent 不报错。"""
        pm = ProcessManager(tmux=_make_mock_tmux())
        pm.untrack("nobody")  # 不应抛出


# ── is_running ───────────────────────────────────────────────


class TestIsRunning:
    """is_running() 测试。"""

    @pytest.mark.asyncio
    async def test_running_agent(self) -> None:
        """追踪中的活跃 agent 返回 True。"""
        tmux = _make_mock_tmux()
        pm = ProcessManager(tmux=tmux)
        await pm.spawn(
            _make_options(name="dev"),
            team_name="t", color="blue", parent_session_id="s",
        )

        assert await pm.is_running("dev") is True

    @pytest.mark.asyncio
    async def test_untracked_agent(self) -> None:
        """未追踪的 agent 返回 False。"""
        pm = ProcessManager(tmux=_make_mock_tmux())
        assert await pm.is_running("nobody") is False

    @pytest.mark.asyncio
    async def test_dead_pane(self) -> None:
        """追踪中但 pane 已退出的 agent 返回 False。"""
        tmux = _make_mock_tmux()
        tmux.is_pane_alive = AsyncMock(return_value=False)
        pm = ProcessManager(tmux=tmux)
        await pm.spawn(
            _make_options(name="dev"),
            team_name="t", color="blue", parent_session_id="s",
        )

        assert await pm.is_running("dev") is False


# ── 状态查询 ─────────────────────────────────────────────────


class TestStateQueries:
    """状态查询方法测试。"""

    def test_initial_tracked_empty(self) -> None:
        """初始无追踪 agent。"""
        pm = ProcessManager(tmux=_make_mock_tmux())
        assert pm.tracked_agents() == []

    def test_get_pane_id_none(self) -> None:
        """未追踪的 agent 返回 None。"""
        pm = ProcessManager(tmux=_make_mock_tmux())
        assert pm.get_pane_id("nobody") is None

    def test_tmux_property(self) -> None:
        """tmux 属性返回注入的 TmuxManager。"""
        tmux = _make_mock_tmux()
        pm = ProcessManager(tmux=tmux)
        assert pm.tmux is tmux


# ── CLI 参数构建 ─────────────────────────────────────────────


class TestBuildCliArgs:
    """build_cli_args() 测试。"""

    def test_required_args(self) -> None:
        """必选参数全部包含。"""
        args = ProcessManager.build_cli_args(
            _make_options(name="dev", agent_type="general-purpose", model="claude-sonnet-4-6"),
            team_name="my-team",
            color="blue",
            parent_session_id="sess-1",
        )

        assert "--agent-id" in args
        idx = args.index("--agent-id")
        assert args[idx + 1] == "dev@my-team"

        assert "--agent-name" in args
        assert "--team-name" in args
        assert "--agent-color" in args
        assert "--parent-session-id" in args
        assert "--agent-type" in args
        assert "--model" in args

    def test_plan_mode_required(self) -> None:
        """plan_mode_required=True 时添加 --plan-mode-required。"""
        args = ProcessManager.build_cli_args(
            _make_options(plan_mode_required=True),
            team_name="t", color="blue", parent_session_id="s",
        )
        assert "--plan-mode-required" in args

    def test_plan_mode_not_required(self) -> None:
        """plan_mode_required=False 时不添加。"""
        args = ProcessManager.build_cli_args(
            _make_options(plan_mode_required=False),
            team_name="t", color="blue", parent_session_id="s",
        )
        assert "--plan-mode-required" not in args

    def test_permission_mode_default(self) -> None:
        """permission_mode="default" 时使用 --permission-mode default。"""
        args = ProcessManager.build_cli_args(
            _make_options(permission_mode="default"),
            team_name="t", color="blue", parent_session_id="s",
        )
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "default"

    def test_permission_mode_bypass(self) -> None:
        """bypassPermissions 映射为 --dangerously-skip-permissions。"""
        args = ProcessManager.build_cli_args(
            _make_options(permission_mode="bypassPermissions"),
            team_name="t", color="blue", parent_session_id="s",
        )
        assert "--dangerously-skip-permissions" in args
        assert "--permission-mode" not in args

    def test_permission_mode_delegate(self) -> None:
        """delegate 映射为 acceptEdits（兼容旧版本）。"""
        args = ProcessManager.build_cli_args(
            _make_options(permission_mode="delegate"),
            team_name="t", color="blue", parent_session_id="s",
        )
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "acceptEdits"

    def test_allowed_tools(self) -> None:
        """allowed_tools 转为多个 --allowedTools。"""
        args = ProcessManager.build_cli_args(
            _make_options(allowed_tools=["Read", "Write"]),
            team_name="t", color="blue", parent_session_id="s",
        )
        indices = [i for i, a in enumerate(args) if a == "--allowedTools"]
        assert len(indices) == 2
        tools = [args[i + 1] for i in indices]
        assert set(tools) == {"Read", "Write"}

    def test_disallowed_tools(self) -> None:
        """disallowed_tools 转为多个 --disallowedTools。"""
        args = ProcessManager.build_cli_args(
            _make_options(disallowed_tools=["Bash"]),
            team_name="t", color="blue", parent_session_id="s",
        )
        assert "--disallowedTools" in args
        idx = args.index("--disallowedTools")
        assert args[idx + 1] == "Bash"

    def test_no_optional_args(self) -> None:
        """无可选参数时不添加额外标志。"""
        args = ProcessManager.build_cli_args(
            _make_options(),
            team_name="t", color="blue", parent_session_id="s",
        )
        assert "--plan-mode-required" not in args
        assert "--permission-mode" not in args
        assert "--allowedTools" not in args
        assert "--disallowedTools" not in args


# ── _find_claude_binary ──────────────────────────────────────


class TestFindClaudeBinary:
    """_find_claude_binary() 测试。"""

    def test_env_variable_takes_priority(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CC_TEAM_CLAUDE_BIN 环境变量优先。"""
        monkeypatch.setenv("CC_TEAM_CLAUDE_BIN", "/custom/claude")
        assert _find_claude_binary() == "/custom/claude"

    def test_falls_back_to_which(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无环境变量时使用 shutil.which。"""
        monkeypatch.delenv("CC_TEAM_CLAUDE_BIN", raising=False)
        with patch("cc_team.process_manager.shutil.which", return_value="/usr/bin/claude"):
            assert _find_claude_binary() == "/usr/bin/claude"

    def test_ultimate_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """which 也找不到时 fallback 为 "claude"。"""
        monkeypatch.delenv("CC_TEAM_CLAUDE_BIN", raising=False)
        with patch("cc_team.process_manager.shutil.which", return_value=None):
            assert _find_claude_binary() == "claude"
