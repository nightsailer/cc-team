"""controller.py 单元测试 — 中央编排器验证。

测试覆盖:
- init（团队创建 + 轮询启动 + 重复初始化）
- shutdown（停止轮询 + 终止 Agent + 销毁团队）
- spawn（Agent 生命周期）
- 消息发送（send_message / broadcast / plan_approval）
- 任务管理（create_task / update_task + task_assignment 自动发送）
- AgentController Protocol 实现
- 属性访问
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import cc_team._serialization as ser_mod
import cc_team.inbox as inbox_mod
import cc_team.message_builder as mb_mod
import cc_team.paths as paths_mod
import cc_team.team_manager as tm_mod
from cc_team.controller import Controller
from cc_team.exceptions import AgentNotFoundError, NotInitializedError
from cc_team.process_manager import ProcessManager
from cc_team.tmux import TmuxManager
from cc_team.types import ControllerOptions, SpawnAgentOptions

# ── Fixtures ──────────────────────────────────────────────────

FIXED_ISO = "2026-02-28T10:00:00.000Z"
FIXED_MS = 1772193600000


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 ~/.claude/ 到 tmp_path。"""
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(paths_mod, "claude_home", lambda: home)
    monkeypatch.setattr(ser_mod, "now_iso", lambda: FIXED_ISO)
    monkeypatch.setattr(ser_mod, "now_ms", lambda: FIXED_MS)
    monkeypatch.setattr(inbox_mod, "now_iso", lambda: FIXED_ISO)
    monkeypatch.setattr(mb_mod, "now_iso", lambda: FIXED_ISO)
    monkeypatch.setattr(mb_mod, "now_ms", lambda: FIXED_MS)
    monkeypatch.setattr(tm_mod, "now_ms", lambda: FIXED_MS)
    return home


def _mock_tmux() -> MagicMock:
    """创建 mock TmuxManager。"""
    mock = MagicMock(spec=TmuxManager)
    mock.split_window = AsyncMock(return_value="%20")
    mock.send_command = AsyncMock()
    mock.kill_pane = AsyncMock()
    mock.is_pane_alive = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_pm(isolated_home: Path) -> ProcessManager:
    """创建带 mock tmux 的 ProcessManager。"""
    return ProcessManager(tmux=_mock_tmux())


@pytest.fixture
def ctrl(isolated_home: Path, mock_pm: ProcessManager) -> Controller:
    """创建 Controller 实例（未初始化）。"""
    return Controller(
        ControllerOptions(
            team_name="test-team",
            description="unit test",
            session_id="sess-001",
        ),
        process_manager=mock_pm,
    )


# ── 初始化 ───────────────────────────────────────────────────


class TestInit:
    """init() 测试。"""

    @pytest.mark.asyncio
    async def test_init_creates_team(self, ctrl: Controller) -> None:
        """init 应创建团队。"""
        await ctrl.init()
        config = ctrl.team_manager.read()
        assert config is not None
        assert config.name == "test-team"
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_init_sets_session_id(self, ctrl: Controller) -> None:
        """init 使用指定的 session_id。"""
        await ctrl.init()
        assert ctrl.session_id == "sess-001"
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_double_init_raises(self, ctrl: Controller) -> None:
        """重复 init 抛出 NotInitializedError。"""
        await ctrl.init()
        with pytest.raises(NotInitializedError, match="already initialized"):
            await ctrl.init()
        await ctrl.shutdown()


# ── 关闭 ─────────────────────────────────────────────────────


class TestShutdown:
    """shutdown() 测试。"""

    @pytest.mark.asyncio
    async def test_shutdown_destroys_team(self, ctrl: Controller) -> None:
        """shutdown 应销毁团队。"""
        await ctrl.init()
        await ctrl.shutdown()
        assert ctrl.team_manager.read() is None

    @pytest.mark.asyncio
    async def test_shutdown_without_init_noop(self, ctrl: Controller) -> None:
        """未初始化时 shutdown 不报错。"""
        await ctrl.shutdown()  # 不应抛出

    @pytest.mark.asyncio
    async def test_shutdown_kills_agents(self, ctrl: Controller) -> None:
        """shutdown 应终止所有 Agent。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="a", prompt="hi"))
        await ctrl.spawn(SpawnAgentOptions(name="b", prompt="hi"))

        await ctrl.shutdown()
        assert ctrl.list_agents() == []


# ── Spawn ────────────────────────────────────────────────────


class TestSpawn:
    """spawn() 测试。"""

    @pytest.mark.asyncio
    async def test_spawn_returns_handle(self, ctrl: Controller) -> None:
        """spawn 返回 AgentHandle。"""
        await ctrl.init()
        handle = await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="Work"))
        assert handle.name == "dev"
        assert handle.pane_id == "%20"
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_spawn_registers_member(self, ctrl: Controller) -> None:
        """spawn 应将成员注册到 config.json。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="Work"))

        member = ctrl.team_manager.get_member("dev")
        assert member is not None
        assert member.agent_type == "general-purpose"
        assert member.color is not None
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_spawn_writes_initial_prompt(self, ctrl: Controller) -> None:
        """spawn 应写入初始 prompt 到 inbox。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="Do the task"))

        inbox_path = paths_mod.inbox_path("test-team", "dev")
        assert inbox_path.exists()
        msgs = json.loads(inbox_path.read_text())
        assert msgs[0]["text"] == "Do the task"
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_spawn_before_init_raises(self, ctrl: Controller) -> None:
        """未初始化时 spawn 抛出 NotInitializedError。"""
        with pytest.raises(NotInitializedError):
            await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="hi"))

    @pytest.mark.asyncio
    async def test_spawn_emits_event(self, ctrl: Controller) -> None:
        """spawn 应发射 agent:spawned 事件。"""
        await ctrl.init()
        captured: list[tuple] = []

        async def handler(name: str, pane: str) -> None:
            captured.append((name, pane))

        ctrl.on("agent:spawned", handler)
        await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="hi"))

        assert len(captured) == 1
        assert captured[0][0] == "dev"
        await ctrl.shutdown()


# ── Agent 管理 ───────────────────────────────────────────────


class TestAgentManagement:
    """get_handle() / list_agents() / is_agent_running() 测试。"""

    @pytest.mark.asyncio
    async def test_get_handle(self, ctrl: Controller) -> None:
        """获取已 spawn 的 handle。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="hi"))
        handle = ctrl.get_handle("dev")
        assert handle.name == "dev"
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_get_handle_not_found(self, ctrl: Controller) -> None:
        """获取不存在的 handle 抛出异常。"""
        await ctrl.init()
        with pytest.raises(AgentNotFoundError):
            ctrl.get_handle("nobody")
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_list_agents(self, ctrl: Controller) -> None:
        """list_agents 返回所有 agent 名。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="a", prompt="hi"))
        await ctrl.spawn(SpawnAgentOptions(name="b", prompt="hi"))
        assert set(ctrl.list_agents()) == {"a", "b"}
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_is_agent_running(self, ctrl: Controller) -> None:
        """is_agent_running 检查追踪列表。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="hi"))
        assert ctrl.is_agent_running("dev") is True
        assert ctrl.is_agent_running("ghost") is False
        await ctrl.shutdown()


# ── 消息 ─────────────────────────────────────────────────────


class TestMessages:
    """send_message() / broadcast() / send_plan_approval() 测试。"""

    @pytest.mark.asyncio
    async def test_send_message(self, ctrl: Controller) -> None:
        """send_message writes to inbox for a registered member."""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="agent-1", prompt="hi"))
        await ctrl.send_message("agent-1", "Hello", summary="Hi")

        inbox_path = paths_mod.inbox_path("test-team", "agent-1")
        msgs = json.loads(inbox_path.read_text())
        # First message is the spawn prompt, second is our "Hello"
        assert any(m["text"] == "Hello" for m in msgs)
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_send_message_to_nonexistent_raises(self, ctrl: Controller) -> None:
        """P5 regression: send_message to nonexistent member raises AgentNotFoundError."""
        await ctrl.init()
        with pytest.raises(AgentNotFoundError):
            await ctrl.send_message("ghost", "Hello")
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_send_message_before_init(self, ctrl: Controller) -> None:
        """send_message before init raises NotInitializedError."""
        with pytest.raises(NotInitializedError):
            await ctrl.send_message("a", "hi")

    @pytest.mark.asyncio
    async def test_broadcast(self, ctrl: Controller) -> None:
        """broadcast 到所有 Agent。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="a", prompt="hi"))
        await ctrl.spawn(SpawnAgentOptions(name="b", prompt="hi"))

        await ctrl.broadcast("Alert!", summary="Important")

        for name in ["a", "b"]:
            inbox_path = paths_mod.inbox_path("test-team", name)
            msgs = json.loads(inbox_path.read_text())
            # 初始 prompt + broadcast = 2 条或仅 broadcast（取决于 inbox 覆盖行为）
            texts = [m["text"] for m in msgs]
            assert "Alert!" in texts
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_broadcast_with_exclude(self, ctrl: Controller) -> None:
        """broadcast 排除指定 Agent。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="a", prompt="hi"))
        await ctrl.spawn(SpawnAgentOptions(name="b", prompt="hi"))

        await ctrl.broadcast("Secret", exclude=["b"])

        a_path = paths_mod.inbox_path("test-team", "a")
        b_path = paths_mod.inbox_path("test-team", "b")
        a_msgs = json.loads(a_path.read_text())
        a_texts = [m["text"] for m in a_msgs]
        assert "Secret" in a_texts

        # b 的 inbox 应只有初始 prompt，无 broadcast
        b_msgs = json.loads(b_path.read_text())
        b_texts = [m["text"] for m in b_msgs]
        assert "Secret" not in b_texts
        await ctrl.shutdown()


# ── 任务管理 ─────────────────────────────────────────────────


class TestTaskManagement:
    """create_task() / update_task() / list_tasks() 测试。"""

    @pytest.mark.asyncio
    async def test_create_task(self, ctrl: Controller) -> None:
        """创建任务。"""
        await ctrl.init()
        task = await ctrl.create_task(subject="Build", description="Details")
        assert task.id == "1"
        assert task.subject == "Build"
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_create_task_with_owner_sends_assignment(
        self, ctrl: Controller
    ) -> None:
        """创建带 owner 的任务应发送 task_assignment。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="hi"))

        await ctrl.create_task(
            subject="Feature", description="d", owner="dev",
        )

        inbox_path = paths_mod.inbox_path("test-team", "dev")
        msgs = json.loads(inbox_path.read_text())
        # 找到 task_assignment 消息
        assignments = [
            m for m in msgs
            if '"task_assignment"' in m.get("text", "")
        ]
        assert len(assignments) >= 1
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_update_task(self, ctrl: Controller) -> None:
        """更新任务状态。"""
        await ctrl.init()
        task = await ctrl.create_task(subject="T", description="d")
        updated = await ctrl.update_task(task.id, status="completed")
        assert updated.status == "completed"
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_update_task_completed_emits_event(
        self, ctrl: Controller
    ) -> None:
        """任务完成时发射 task:completed 事件。"""
        await ctrl.init()
        captured: list[str] = []

        async def handler(task: object) -> None:
            captured.append("done")

        ctrl.on("task:completed", handler)
        task = await ctrl.create_task(subject="T", description="d")
        await ctrl.update_task(task.id, status="completed")

        assert captured == ["done"]
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_list_tasks(self, ctrl: Controller) -> None:
        """列出任务。"""
        await ctrl.init()
        await ctrl.create_task(subject="A", description="d")
        await ctrl.create_task(subject="B", description="d")
        assert len(ctrl.list_tasks()) == 2
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_task_before_init_raises(self, ctrl: Controller) -> None:
        """未初始化时操作任务抛出异常。"""
        with pytest.raises(NotInitializedError):
            await ctrl.create_task(subject="T", description="d")


# ── 属性 ─────────────────────────────────────────────────────


class TestControllerProperties:
    """属性测试。"""

    def test_team_name(self, ctrl: Controller) -> None:
        assert ctrl.team_name == "test-team"

    def test_session_id(self, ctrl: Controller) -> None:
        assert ctrl.session_id == "sess-001"

    def test_team_manager(self, ctrl: Controller) -> None:
        assert ctrl.team_manager is not None

    def test_task_manager(self, ctrl: Controller) -> None:
        assert ctrl.task_manager is not None

    def test_process_manager(self, ctrl: Controller, mock_pm: ProcessManager) -> None:
        assert ctrl.process_manager is mock_pm


# ── Spawn 失败回滚 [CRIT-2] ─────────────────────────────────


class TestSpawnRollback:
    """spawn 进程启动失败时应回滚成员注册 (CRIT-2 验证)。"""

    @pytest.mark.asyncio
    async def test_spawn_failure_removes_member(self, ctrl: Controller) -> None:
        """进程启动失败时，注册的成员应被移除。"""
        await ctrl.init()

        # Mock spawn at the AgentBackend protocol level
        ctrl._process_manager.spawn = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("backend dead")
        )

        with pytest.raises(Exception, match="backend dead"):
            await ctrl.spawn(SpawnAgentOptions(name="doomed", prompt="hi"))

        # 成员不应留在 config.json
        member = ctrl.team_manager.get_member("doomed")
        assert member is None
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_spawn_failure_does_not_leave_handle(self, ctrl: Controller) -> None:
        """进程启动失败后，handles 中不应有残留。"""
        await ctrl.init()

        ctrl._process_manager.spawn = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("no backend")
        )

        with pytest.raises(RuntimeError):
            await ctrl.spawn(SpawnAgentOptions(name="ghost", prompt="hi"))

        assert "ghost" not in ctrl.list_agents()
        await ctrl.shutdown()


# ── Shutdown Approved Handler [CRIT-3] ──────────────────────


class TestShutdownApproved:
    """_on_shutdown_approved 事件处理 (CRIT-3 验证)。"""

    @pytest.mark.asyncio
    async def test_shutdown_approved_removes_handle(self, ctrl: Controller) -> None:
        """shutdown:approved 事件触发后，handle 应被移除。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="worker", prompt="hi"))
        assert ctrl.is_agent_running("worker") is True

        # 模拟 shutdown:approved 事件
        await ctrl.emit("shutdown:approved", "worker", {"request_id": "r1"})

        assert ctrl.is_agent_running("worker") is False
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_approved_untracks_process(self, ctrl: Controller) -> None:
        """shutdown:approved 应 untrack 进程。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="worker", prompt="hi"))
        assert "worker" in ctrl.process_manager.tracked_agents()

        await ctrl.emit("shutdown:approved", "worker", {})

        assert "worker" not in ctrl.process_manager.tracked_agents()
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_approved_removes_team_member(self, ctrl: Controller) -> None:
        """shutdown:approved 应从 config.json 移除成员。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="worker", prompt="hi"))
        assert ctrl.team_manager.get_member("worker") is not None

        await ctrl.emit("shutdown:approved", "worker", {})

        assert ctrl.team_manager.get_member("worker") is None
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_approved_unknown_agent_noop(self, ctrl: Controller) -> None:
        """对未知 agent 的 shutdown:approved 不崩溃。"""
        await ctrl.init()
        # 不应抛出任何异常
        await ctrl.emit("shutdown:approved", "phantom", {})
        await ctrl.shutdown()


# ── Kill Agent ──────────────────────────────────────────────


class TestKillAgent:
    """kill_agent() 完整流程测试。"""

    @pytest.mark.asyncio
    async def test_kill_agent_full_flow(self, ctrl: Controller) -> None:
        """kill_agent 应 kill 进程 + 移除 handle + 移除成员。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="victim", prompt="hi"))

        await ctrl.kill_agent("victim")

        # handle 已移除
        assert "victim" not in ctrl.list_agents()
        # 成员已移除
        assert ctrl.team_manager.get_member("victim") is None

    @pytest.mark.asyncio
    async def test_kill_agent_before_init_raises(self, ctrl: Controller) -> None:
        """未初始化时 kill_agent 抛出 NotInitializedError。"""
        with pytest.raises(NotInitializedError):
            await ctrl.kill_agent("anyone")


# ── Poller Error 转发 ───────────────────────────────────────


class TestPollerError:
    """_on_poller_error 事件转发测试。"""

    @pytest.mark.asyncio
    async def test_poller_error_emits_error_event(self, ctrl: Controller) -> None:
        """_on_poller_error 应将异常转发到 error 事件。"""
        await ctrl.init()
        captured: list[Exception] = []

        async def error_handler(exc: Exception) -> None:
            captured.append(exc)

        ctrl.on("error", error_handler)

        exc = RuntimeError("poll failed")
        await ctrl._on_poller_error(exc, "poll")

        assert len(captured) == 1
        assert captured[0] is exc
        await ctrl.shutdown()


# ── Send Shutdown Request ───────────────────────────────────


class TestSendShutdownRequest:
    """send_shutdown_request() 测试。"""

    @pytest.mark.asyncio
    async def test_send_shutdown_request_writes_inbox(self, ctrl: Controller) -> None:
        """send_shutdown_request 应写入目标 agent inbox。"""
        await ctrl.init()
        req_id = await ctrl.send_shutdown_request("target-agent", "time to stop")

        inbox_path = paths_mod.inbox_path("test-team", "target-agent")
        msgs = json.loads(inbox_path.read_text())
        assert any('"shutdown_request"' in m.get("text", "") for m in msgs)
        assert isinstance(req_id, str) and len(req_id) > 0
        await ctrl.shutdown()


# ── Update Task Owner Change ────────────────────────────────


class TestUpdateTaskOwnerChange:
    """update_task owner 变更时发送 task_assignment 测试。"""

    @pytest.mark.asyncio
    async def test_update_task_owner_sends_assignment(self, ctrl: Controller) -> None:
        """owner 从 None 变为 dev 时应发送 task_assignment。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="hi"))
        task = await ctrl.create_task(subject="T", description="d")

        await ctrl.update_task(task.id, owner="dev")

        inbox_path = paths_mod.inbox_path("test-team", "dev")
        msgs = json.loads(inbox_path.read_text())
        assignments = [m for m in msgs if '"task_assignment"' in m.get("text", "")]
        assert len(assignments) >= 1
        await ctrl.shutdown()
