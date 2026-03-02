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
        assert handle.backend_id == "%20"
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
    async def test_create_task_with_owner_sends_assignment(self, ctrl: Controller) -> None:
        """创建带 owner 的任务应发送 task_assignment。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="hi"))

        await ctrl.create_task(
            subject="Feature",
            description="d",
            owner="dev",
        )

        inbox_path = paths_mod.inbox_path("test-team", "dev")
        msgs = json.loads(inbox_path.read_text())
        # 找到 task_assignment 消息
        assignments = [m for m in msgs if '"task_assignment"' in m.get("text", "")]
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
    async def test_update_task_completed_emits_event(self, ctrl: Controller) -> None:
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


# ── Attach [R1] ─────────────────────────────────────────────


class TestAttach:
    """attach() 测试。"""

    @pytest.mark.asyncio
    async def test_attach_normal(self, isolated_home: Path, mock_pm: ProcessManager) -> None:
        """attach 到已存在的团队，恢复 session_id。"""
        # 先用另一个 Controller 创建团队
        c1 = Controller(
            ControllerOptions(team_name="test-team", session_id="orig-sess"),
            process_manager=mock_pm,
        )
        await c1.init()
        await c1.spawn(SpawnAgentOptions(name="worker", prompt="hi"))
        # 保留团队但释放 controller（模拟 TL 退出）
        # 直接 stop poller + clear，不 destroy
        if c1._poller:
            await c1._poller.stop()
        c1._initialized = False

        # 用新 Controller attach
        c2 = Controller(
            ControllerOptions(team_name="test-team"),
            process_manager=mock_pm,
        )
        await c2.attach()

        assert c2._initialized is True
        assert c2.session_id == "orig-sess"
        # 应恢复 worker handle
        assert "worker" in c2.list_agents()
        # shutdown 不应 destroy 团队
        await c2.shutdown()
        # 团队仍存在
        assert c2.team_manager.read() is not None
        # 清理
        await c2.team_manager.destroy()

    @pytest.mark.asyncio
    async def test_attach_team_not_found(
        self,
        isolated_home: Path,
        mock_pm: ProcessManager,
    ) -> None:
        """attach 到不存在的团队应抛出 FileNotFoundError。"""
        c = Controller(
            ControllerOptions(team_name="ghost"),
            process_manager=mock_pm,
        )
        with pytest.raises(FileNotFoundError, match="ghost"):
            await c.attach()

    @pytest.mark.asyncio
    async def test_attach_double_raises(self, isolated_home: Path, mock_pm: ProcessManager) -> None:
        """重复 attach 抛出 NotInitializedError。"""
        from cc_team.team_manager import TeamManager

        mgr = TeamManager("test-team")
        await mgr.create(lead_session_id="s1")

        c = Controller(
            ControllerOptions(team_name="test-team"),
            process_manager=mock_pm,
        )
        await c.attach()
        with pytest.raises(NotInitializedError, match="already initialized"):
            await c.attach()
        await c.shutdown()
        await mgr.destroy()

    @pytest.mark.asyncio
    async def test_attach_restores_active_handles_only(
        self, isolated_home: Path, mock_pm: ProcessManager
    ) -> None:
        """attach 仅恢复 is_active=True 的非 TL 成员。"""
        from cc_team._serialization import now_ms
        from cc_team.team_manager import TeamManager
        from cc_team.types import TeamMember

        mgr = TeamManager("test-team")
        await mgr.create(lead_session_id="s1")
        # 添加 active agent
        await mgr.add_member(
            TeamMember(
                agent_id="a@test-team",
                name="active-agent",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                joined_at=now_ms(),
                tmux_pane_id="%10",
                cwd="/tmp",
                color="blue",
                is_active=True,
                backend_type="tmux",
            )
        )
        # 添加 inactive agent
        await mgr.add_member(
            TeamMember(
                agent_id="i@test-team",
                name="inactive-agent",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                joined_at=now_ms(),
                tmux_pane_id="%11",
                cwd="/tmp",
                color="green",
                is_active=False,
                backend_type="tmux",
            )
        )

        c = Controller(
            ControllerOptions(team_name="test-team"),
            process_manager=mock_pm,
        )
        await c.attach()
        assert "active-agent" in c.list_agents()
        assert "inactive-agent" not in c.list_agents()
        await c.shutdown()
        await mgr.destroy()


# ── Shutdown 条件 Destroy ──────────────────────────────────


class TestShutdownConditionalDestroy:
    """shutdown 条件销毁测试。"""

    @pytest.mark.asyncio
    async def test_init_shutdown_destroys_team(self, ctrl: Controller) -> None:
        """init() 后 shutdown 应销毁团队。"""
        await ctrl.init()
        await ctrl.shutdown()
        assert ctrl.team_manager.read() is None

    @pytest.mark.asyncio
    async def test_attach_shutdown_preserves_team(
        self, isolated_home: Path, mock_pm: ProcessManager
    ) -> None:
        """attach() 后 shutdown 不应销毁团队。"""
        from cc_team.team_manager import TeamManager

        mgr = TeamManager("test-team")
        await mgr.create(lead_session_id="s1")

        c = Controller(
            ControllerOptions(team_name="test-team"),
            process_manager=mock_pm,
        )
        await c.attach()
        await c.shutdown()
        assert mgr.read() is not None
        await mgr.destroy()


# ── Relay [R2] ──────────────────────────────────────────────


class TestRelay:
    """relay() 测试。"""

    @pytest.mark.asyncio
    async def test_relay_rotates_session(self, ctrl: Controller) -> None:
        """relay 应轮转 session ID。"""
        await ctrl.init()
        old_sid = ctrl.session_id
        new_sid = await ctrl.relay()
        assert new_sid != old_sid
        assert ctrl.session_id == new_sid
        # config 中也更新了
        config = ctrl.team_manager.read()
        assert config is not None
        assert config.lead_session_id == new_sid
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_relay_before_init_raises(self, ctrl: Controller) -> None:
        """未初始化时 relay 抛出 NotInitializedError。"""
        with pytest.raises(NotInitializedError):
            await ctrl.relay()


# ── Spawn 使用 register_member [R3] ──────────────────────


class TestSpawnUsesRegisterMember:
    """验证重构后 spawn 通过 register_member 注册成员。"""

    @pytest.mark.asyncio
    async def test_spawn_uses_register_member(self, ctrl: Controller) -> None:
        """spawn 应通过 register_member 注册，然后更新为 active。"""
        await ctrl.init()
        handle = await ctrl.spawn(SpawnAgentOptions(name="dev", prompt="Work"))

        member = ctrl.team_manager.get_member("dev")
        assert member is not None
        # spawn 后应为 active
        assert member.is_active is True
        # 应有颜色
        assert member.color is not None
        # pane_id 应已更新
        assert member.tmux_pane_id == "%20"
        # prompt 应已保存
        assert member.prompt == "Work"
        # handle 正常
        assert handle.name == "dev"
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_spawn_rollback_still_works(self, ctrl: Controller) -> None:
        """重构后 spawn 失败时仍能正确回滚。"""
        await ctrl.init()

        ctrl._process_manager.spawn = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("backend dead")
        )

        with pytest.raises(Exception, match="backend dead"):
            await ctrl.spawn(SpawnAgentOptions(name="doomed", prompt="hi"))

        # 成员应被回滚
        assert ctrl.team_manager.get_member("doomed") is None
        await ctrl.shutdown()


# ── Sync Agents [R5] ──────────────────────────────────────


class TestSyncAgents:
    """sync_agents() 测试。"""

    @pytest.mark.asyncio
    async def test_sync_agents_recovers_alive_panes(
        self,
        isolated_home: Path,
        mock_pm: ProcessManager,
    ) -> None:
        """存活 pane 应恢复为 handle。"""
        from cc_team.team_manager import TeamManager
        from cc_team.types import TeamMember

        mgr = TeamManager("test-team")
        await mgr.create(lead_session_id="s1")
        await mgr.add_member(
            TeamMember(
                agent_id="w@test-team",
                name="worker",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                joined_at=FIXED_MS,
                tmux_pane_id="%68",
                cwd="/tmp",
                color="blue",
                is_active=True,
                backend_type="tmux",
            )
        )

        # mock_pm 的 is_running 需要 pane 被 track
        # mock_pm 使用 mock tmux，is_pane_alive 默认返回 True
        c = Controller(
            ControllerOptions(team_name="test-team"),
            process_manager=mock_pm,
        )
        c._initialized = True

        synced = await c.sync_agents()

        assert len(synced) == 1
        assert synced[0].name == "worker"
        assert "worker" in c.list_agents()
        # ProcessManager 应有 track 记录
        assert "worker" in mock_pm.tracked_agents()
        await c.shutdown()
        await mgr.destroy()

    @pytest.mark.asyncio
    async def test_sync_agents_marks_dead_inactive(
        self,
        isolated_home: Path,
    ) -> None:
        """死亡 pane 应标记为 inactive。"""
        from cc_team.team_manager import TeamManager
        from cc_team.types import TeamMember

        mgr = TeamManager("test-team")
        await mgr.create(lead_session_id="s1")
        await mgr.add_member(
            TeamMember(
                agent_id="d@test-team",
                name="dead-agent",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                joined_at=FIXED_MS,
                tmux_pane_id="%55",
                cwd="/tmp",
                color="green",
                is_active=True,
                backend_type="tmux",
            )
        )

        # 创建 PM，pane 不存活
        dead_tmux = _mock_tmux()
        dead_tmux.is_pane_alive = AsyncMock(return_value=False)
        dead_pm = ProcessManager(tmux=dead_tmux)

        c = Controller(
            ControllerOptions(team_name="test-team"),
            process_manager=dead_pm,
        )
        c._initialized = True

        synced = await c.sync_agents()

        assert len(synced) == 0
        assert "dead-agent" not in c.list_agents()
        # config 中应标记为 inactive
        member = mgr.get_member("dead-agent")
        assert member is not None
        assert member.is_active is False
        await c.shutdown()
        await mgr.destroy()

    @pytest.mark.asyncio
    async def test_attach_uses_sync_agents(
        self,
        isolated_home: Path,
    ) -> None:
        """attach 使用 sync_agents，ghost pane 不在 handles 中。"""
        from cc_team.team_manager import TeamManager
        from cc_team.types import TeamMember

        mgr = TeamManager("test-team")
        await mgr.create(lead_session_id="s1")
        # 一个存活 + 一个死亡
        await mgr.add_member(
            TeamMember(
                agent_id="alive@test-team",
                name="alive-agent",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                joined_at=FIXED_MS,
                tmux_pane_id="%10",
                cwd="/tmp",
                color="blue",
                is_active=True,
                backend_type="tmux",
            )
        )
        await mgr.add_member(
            TeamMember(
                agent_id="ghost@test-team",
                name="ghost-agent",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                joined_at=FIXED_MS,
                tmux_pane_id="%99",
                cwd="/tmp",
                color="green",
                is_active=True,
                backend_type="tmux",
            )
        )

        # tmux mock: %10 存活, %99 死亡
        mixed_tmux = _mock_tmux()

        async def _is_alive(pane_id: str) -> bool:
            return pane_id == "%10"

        mixed_tmux.is_pane_alive = AsyncMock(side_effect=_is_alive)
        mixed_pm = ProcessManager(tmux=mixed_tmux)

        c = Controller(
            ControllerOptions(team_name="test-team"),
            process_manager=mixed_pm,
        )
        await c.attach()

        assert "alive-agent" in c.list_agents()
        assert "ghost-agent" not in c.list_agents()
        # ghost 应被标记为 inactive
        ghost = mgr.get_member("ghost-agent")
        assert ghost is not None
        assert ghost.is_active is False
        await c.shutdown()
        await mgr.destroy()


# ── Relay Broadcasts Session Relay [R7] ──────────────────


class TestRelayBroadcast:
    """relay() 广播 session_relay 测试。"""

    @pytest.mark.asyncio
    async def test_relay_broadcasts_session_relay(self, ctrl: Controller) -> None:
        """relay 应广播 session_relay 到所有 active agents。"""
        await ctrl.init()
        await ctrl.spawn(SpawnAgentOptions(name="w1", prompt="hi"))
        await ctrl.spawn(SpawnAgentOptions(name="w2", prompt="hi"))
        old_sid = ctrl.session_id

        new_sid = await ctrl.relay()

        # 检查两个 agent 的 inbox 中有 session_relay 消息
        for name in ["w1", "w2"]:
            inbox_path = paths_mod.inbox_path("test-team", name)
            msgs = json.loads(inbox_path.read_text())
            relay_msgs = [m for m in msgs if '"session_relay"' in m.get("text", "")]
            assert len(relay_msgs) >= 1
            body = json.loads(relay_msgs[0]["text"])
            assert body["type"] == "session_relay"
            assert body["newSessionId"] == new_sid
            assert body["previousSessionId"] == old_sid
        await ctrl.shutdown()

    @pytest.mark.asyncio
    async def test_relay_no_agents_no_broadcast(self, ctrl: Controller) -> None:
        """无 active agents 时 relay 不广播。"""
        await ctrl.init()
        # 不 spawn 任何 agent，relay 不应报错
        await ctrl.relay()
        await ctrl.shutdown()
