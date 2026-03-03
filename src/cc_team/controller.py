"""中央编排控制器。

cc-team 的核心入口，协调所有子系统:
- 团队生命周期 (init/shutdown)
- Agent 管理 (spawn/kill)
- 消息通信 (send/broadcast)
- 任务管理 (create/update/list)
- 事件系统 (on/emit)

瘦身策略: 消息构造委托给 MessageBuilder，事件路由委托给 EventRouter。
"""

from __future__ import annotations

import contextlib
import os
import uuid
from typing import Any

from cc_team._spawn import spawn_agent_workflow
from cc_team._sync import sync_member_states
from cc_team.agent_handle import AgentHandle
from cc_team.event_router import EventRouter
from cc_team.events import AsyncEventEmitter
from cc_team.exceptions import AgentNotFoundError, NotInitializedError
from cc_team.inbox_poller import InboxPoller
from cc_team.message_builder import MessageBuilder
from cc_team.process_manager import ProcessManager
from cc_team.task_manager import TaskManager
from cc_team.team_manager import TeamManager
from cc_team.types import (
    TEAM_LEAD_AGENT_TYPE,
    AgentBackend,
    AgentColor,
    ControllerOptions,
    SpawnAgentOptions,
    TaskFile,
    TaskStatus,
)


class Controller(AsyncEventEmitter):
    """cc-team 中央编排控制器。

    继承 AsyncEventEmitter 以便直接使用 on/emit。

    事件清单:
    - message(agent_name, InboxMessage)
    - idle(agent_name)
    - shutdown:approved(agent_name, ShutdownApprovedMessage)
    - plan:approval_request(agent_name, PlanApprovalRequestMessage)
    - permission:request(agent_name, PermissionRequestMessage)
    - task:completed(TaskFile)
    - agent:spawned(agent_name, backend_id)
    - agent:exited(agent_name, exit_code)
    - error(Exception)

    用法:
        ctrl = Controller(ControllerOptions(team_name="my-team"))
        await ctrl.init()
        handle = await ctrl.spawn(SpawnAgentOptions(name="agent-1", prompt="..."))
        await handle.send("Start working")
        await ctrl.shutdown()
    """

    def __init__(
        self,
        options: ControllerOptions,
        *,
        process_manager: AgentBackend | None = None,
    ) -> None:
        super().__init__()
        self._options = options
        self._initialized = False
        self._created_team = False  # init() 创建的团队在 shutdown 时销毁

        # 子系统
        self._team_manager = TeamManager(options.team_name)
        self._task_manager = TaskManager(options.team_name)
        self._process_manager: AgentBackend = process_manager or ProcessManager()
        self._message_builder = MessageBuilder(options.team_name)
        self._event_router = EventRouter(self)
        self._poller: InboxPoller | None = None

        # Agent 追踪
        self._handles: dict[str, AgentHandle] = {}
        self._session_id = options.session_id or str(uuid.uuid4())

    @property
    def team_name(self) -> str:
        return self._options.team_name

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def team_manager(self) -> TeamManager:
        return self._team_manager

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager

    @property
    def process_manager(self) -> AgentBackend:
        return self._process_manager

    # ── 生命周期 ────────────────────────────────────────────

    async def init(self) -> None:
        """初始化 Controller: 创建团队 + 启动 inbox 轮询。

        Raises:
            NotInitializedError: 重复初始化
        """
        if self._initialized:
            raise NotInitializedError("Controller already initialized")

        # 创建团队
        await self._team_manager.create(
            description=self._options.description,
            lead_model=self._options.model,
            lead_session_id=self._session_id,
            cwd=self._options.cwd or os.getcwd(),
        )

        # 注册内部事件 handler（在 poller 启动前，确保不遗漏消息）
        self.on("shutdown:approved", self._on_shutdown_approved)

        await self._start_poller()

        self._initialized = True
        self._created_team = True

    async def attach(self) -> None:
        """接管已存在的团队（不创建新团队）。

        用于 takeover 场景：Controller 连接到一个已有的团队，
        恢复 agent handles，启动 inbox 轮询，但不创建/销毁团队资源。

        Raises:
            FileNotFoundError: 团队配置不存在
            NotInitializedError: 重复初始化
        """
        if self._initialized:
            raise NotInitializedError("Controller already initialized")

        config = self._team_manager.read()
        if config is None:
            raise FileNotFoundError(f"Team '{self._options.team_name}' not found")

        # 使用配置中的 session_id
        self._session_id = config.lead_session_id

        # 注册内部事件 handler
        self.on("shutdown:approved", self._on_shutdown_approved)

        await self._start_poller()

        # Recover existing agent handles from config (verify liveness)
        await self.sync_agents()  # return value unused in attach

        self._initialized = True
        self._created_team = False

    async def shutdown(self) -> None:
        """Shutdown Controller: stop polling + terminate all agents.

        Teams created via init() are destroyed on shutdown.
        Teams attached via attach() are left intact.
        """
        if not self._initialized:
            return

        await self._stop_poller()

        # Force-kill all tracked agents via unified deregister
        for name in list(self._handles.keys()):
            await self._deregister_agent(name, force_kill=True)

        if self._created_team:
            await self._team_manager.destroy()

        self._initialized = False

    async def relay(self) -> str:
        """上下文接力：轮转 session ID + 广播通知 + 重启 poller。

        SDK 用户调用此方法进行 session 轮转。TL 进程的停止/重启
        由调用方处理（CLI 或上层应用），因为 Controller 不一定管理
        TL 进程（attach 模式下 TL 在外部 tmux 中运行）。

        Returns:
            新的 session ID

        Raises:
            NotInitializedError: Controller 未初始化
        """
        self._check_initialized()

        previous_session_id = self._session_id

        # 停止当前 poller
        await self._stop_poller()

        # 轮转 session
        self._session_id = await self._team_manager.rotate_session()

        # 广播 session_relay 到所有 active agents（poller 重启前）
        active_agents = list(self._handles.keys())
        if active_agents:
            await self._message_builder.send_session_relay(
                active_agents,
                new_session_id=self._session_id,
                previous_session_id=previous_session_id,
            )

        # 重启 poller
        await self._start_poller()

        return self._session_id

    async def _start_poller(self) -> None:
        """创建并启动 Lead inbox 轮询器。"""
        self._poller = InboxPoller(
            self._options.team_name,
            TEAM_LEAD_AGENT_TYPE,
        )
        self._poller.on_message(self._event_router.route)
        self._poller.on_error(self._on_poller_error)
        await self._poller.start()

    async def _stop_poller(self) -> None:
        """停止轮询器并释放引用。"""
        if self._poller:
            await self._poller.stop()
            self._poller = None

    def _check_initialized(self) -> None:
        if not self._initialized:
            raise NotInitializedError("Controller not initialized, call init() first")

    # ── Unified Agent State Management ─────────────────────
    #
    # _register_agent / _deregister_agent are the ONLY entry points
    # for adding/removing agents from _handles. This eliminates the
    # dual-state sync problem between _handles and _panes.

    def _register_agent(
        self,
        name: str,
        *,
        backend_id: str,
        color: AgentColor | None = None,
    ) -> AgentHandle:
        """Register an agent in Controller tracking (single entry point).

        Creates AgentHandle and stores in _handles.
        Backend (ProcessManager._panes) is already populated by spawn/track.
        """
        handle = AgentHandle(
            name,
            self,
            backend_id=backend_id,
            color=color,
        )
        self._handles[name] = handle
        return handle

    async def _deregister_agent(
        self,
        name: str,
        *,
        force_kill: bool = False,
    ) -> None:
        """Remove agent from all tracking sources (single exit point).

        Cleans up _handles, backend tracking, and config.json atomically.
        Each step is guarded so partial failures don't leave stale state.

        Args:
            name: Agent name
            force_kill: True = kill backend process; False = just untrack
        """
        if force_kill:
            with contextlib.suppress(Exception):
                await self._process_manager.kill(name)
        else:
            self._process_manager.untrack(name)
        self._handles.pop(name, None)
        with contextlib.suppress(AgentNotFoundError):
            await self._team_manager.remove_member(name)

    # ── Agent Sync ───────────────────────────────────────────

    async def sync_agents(self) -> tuple[list[AgentHandle], list[str]]:
        """Discover, recover, and cleanup agent connections (bidirectional).

        For each non-TL member with a backend_id in config.json:
        - alive + isActive=false → **recover**: set isActive=true, register handle
        - alive + isActive=true  → normal sync, register handle
        - dead  + isActive=true  → mark isActive=false
        - dead  + isActive=false → skip (no redundant write)

        Returns:
            (synced_handles, recovered_names) — synced includes recovered agents
        """
        config = self._team_manager.read()
        if config is None:
            return [], []

        result = await sync_member_states(
            self._team_manager,
            self._process_manager,
            config,
        )

        # Register handles for all alive agents
        synced: list[AgentHandle] = []
        for name in result.active + result.recovered:
            member = result.members[name]
            handle = self._register_agent(
                name,
                backend_id=member.tmux_pane_id,
                color=member.color,
            )
            synced.append(handle)

        return synced, result.recovered

    # ── Agent Management ─────────────────────────────────────

    async def spawn(self, options: SpawnAgentOptions) -> AgentHandle:
        """Spawn Agent via shared workflow + register handle + emit event.

        Steps 1-5 (register → activate → write prompt → spawn → update)
        are delegated to spawn_agent_workflow(). Controller adds:
        - _register_agent (unified state entry)
        - agent:spawned event emission

        Returns:
            AgentHandle proxy object
        """
        self._check_initialized()

        backend_id, color = await spawn_agent_workflow(
            self._team_manager,
            self._process_manager,
            options,
            team_name=self._options.team_name,
            cwd=self._options.cwd or os.getcwd(),
            lead_session_id=self._session_id,
        )

        handle = self._register_agent(
            options.name,
            backend_id=backend_id,
            color=color,
        )

        await self.emit("agent:spawned", options.name, backend_id)
        return handle

    def get_handle(self, agent_name: str) -> AgentHandle:
        """获取 Agent Handle。

        Raises:
            AgentNotFoundError: Agent 不存在
        """
        handle = self._handles.get(agent_name)
        if handle is None:
            raise AgentNotFoundError(agent_name)
        return handle

    def list_agents(self) -> list[str]:
        """返回所有 Agent 名称。"""
        return list(self._handles.keys())

    # ── AgentController Protocol 实现 ──────────────────────

    async def send_message(
        self, recipient: str, content: str, *, summary: str | None = None
    ) -> None:
        """Send a message to a specific agent.

        Raises:
            AgentNotFoundError: recipient is not a registered team member.
        """
        self._check_initialized()
        if self._team_manager.get_member(recipient) is None:
            raise AgentNotFoundError(recipient)
        await self._message_builder.send_plain(
            recipient,
            content,
            summary=summary,
        )

    async def send_shutdown_request(self, agent_name: str, reason: str) -> str:
        """发送关闭请求，返回 request_id。"""
        self._check_initialized()
        return await self._message_builder.send_shutdown_request(agent_name, reason)

    async def kill_agent(self, agent_name: str) -> None:
        """Force-terminate Agent (kill process + deregister from all tracking)."""
        self._check_initialized()
        await self._deregister_agent(agent_name, force_kill=True)

    def is_agent_running(self, agent_name: str) -> bool:
        """检查 Agent 是否存活（同步检查追踪列表）。"""
        return agent_name in self._handles

    # ── 消息 ────────────────────────────────────────────────

    async def broadcast(
        self,
        content: str,
        *,
        summary: str | None = None,
        exclude: list[str] | None = None,
    ) -> None:
        """广播消息到所有 Agent。"""
        self._check_initialized()
        recipients = [name for name in self._handles if not exclude or name not in exclude]
        await self._message_builder.broadcast(
            content,
            recipients,
            summary=summary,
        )

    async def send_plan_approval(
        self,
        agent_name: str,
        request_id: str,
        *,
        approved: bool = True,
        permission_mode: str = "default",
        feedback: str | None = None,
    ) -> None:
        """发送计划审批响应。"""
        self._check_initialized()
        await self._message_builder.send_plan_approval(
            agent_name,
            request_id,
            approved=approved,
            permission_mode=permission_mode,
            feedback=feedback,
        )

    # ── 任务 ────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        subject: str,
        description: str,
        active_form: str = "",
        owner: str | None = None,
    ) -> TaskFile:
        """创建任务。"""
        self._check_initialized()
        task = await self._task_manager.create(
            subject=subject,
            description=description,
            active_form=active_form,
            owner=owner,
        )

        # 如果有 owner，发送 task_assignment
        if owner:
            await self._message_builder.send_task_assignment(owner, task)

        return task

    async def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        owner: str | None = ...,  # type: ignore[assignment]
        **kwargs: Any,
    ) -> TaskFile:
        """更新任务。"""
        self._check_initialized()

        # 检查是否需要发送 task_assignment（owner 变更）
        old_task = self._task_manager.read(task_id)
        task = await self._task_manager.update(
            task_id,
            status=status,
            owner=owner,
            **kwargs,
        )

        # owner 变更时发送 task_assignment
        if owner is not ... and owner is not None and (old_task is None or old_task.owner != owner):
            await self._message_builder.send_task_assignment(owner, task)

        # 任务完成时发射事件
        if status == "completed":
            await self.emit("task:completed", task)

        return task

    def list_tasks(self) -> list[TaskFile]:
        """列出所有任务。"""
        return self._task_manager.list_all()

    # ── 内部事件处理 ────────────────────────────────────────

    async def _on_shutdown_approved(self, agent_name: str, _msg: Any) -> None:
        """Handle agent shutdown confirmation (graceful exit)."""
        await self._deregister_agent(agent_name, force_kill=False)

    async def _on_poller_error(self, exc: Exception, _context: str) -> None:
        """InboxPoller 异常转发到 error 事件。"""
        await self.emit("error", exc)
