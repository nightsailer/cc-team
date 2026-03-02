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

from cc_team.agent_handle import AgentHandle
from cc_team.event_router import EventRouter
from cc_team.events import AsyncEventEmitter
from cc_team.exceptions import AgentNotFoundError, NotInitializedError
from cc_team.inbox import InboxIO
from cc_team.inbox_poller import InboxPoller
from cc_team.message_builder import MessageBuilder
from cc_team.process_manager import ProcessManager
from cc_team.task_manager import TaskManager
from cc_team.team_manager import TeamManager
from cc_team.types import (
    TEAM_LEAD_AGENT_TYPE,
    TMUX_BACKEND,
    AgentBackend,
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
    - agent:spawned(agent_name, pane_id)
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
            raise FileNotFoundError(
                f"Team '{self._options.team_name}' not found"
            )

        # 使用配置中的 session_id
        self._session_id = config.lead_session_id

        # 注册内部事件 handler
        self.on("shutdown:approved", self._on_shutdown_approved)

        await self._start_poller()

        # 从 config 恢复已有 agent handles（验证 pane 存活状态）
        await self.sync_agents()

        self._initialized = True
        self._created_team = False

    async def shutdown(self) -> None:
        """关闭 Controller: 停止轮询 + 终止所有 Agent。

        仅当 Controller 通过 init() 创建了团队时才销毁团队资源。
        通过 attach() 接管的团队不会被销毁。
        """
        if not self._initialized:
            return

        # 停止轮询
        await self._stop_poller()

        # 强制终止所有存活 Agent
        for name in list(self._handles.keys()):
            with contextlib.suppress(Exception):
                await self._process_manager.kill(name)

        # 仅 init() 创建的团队在 shutdown 时销毁
        if self._created_team:
            await self._team_manager.destroy()

        self._handles.clear()
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
            self._options.team_name, TEAM_LEAD_AGENT_TYPE,
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

    # ── Agent 同步 ──────────────────────────────────────────

    async def sync_agents(self) -> list[AgentHandle]:
        """发现并恢复/清理 agent 连接。

        对 config.json 中每个非 TL active 成员：
        1. 有 pane_id → 检查 tmux pane 是否存活
           - 存活 → 创建 handle + 注册到 ProcessManager.track()
           - 死亡 → update_member(is_active=False)
        2. 无 pane_id → 跳过（register-only 成员）

        Returns:
            恢复的 AgentHandle 列表
        """
        config = self._team_manager.read()
        if config is None:
            return []

        synced: list[AgentHandle] = []
        for member in config.members:
            if member.agent_type == TEAM_LEAD_AGENT_TYPE:
                continue
            if not member.is_active:
                continue
            if not member.tmux_pane_id:
                continue

            # Always track first so is_running can resolve the pane
            self._process_manager.track(member.name, member.tmux_pane_id)
            alive = await self._process_manager.is_running(member.name)
            if not alive:
                self._process_manager.untrack(member.name)
                await self._team_manager.update_member(
                    member.name, is_active=False,
                )
                continue

            handle = AgentHandle(
                member.name, self,
                pane_id=member.tmux_pane_id,
                color=member.color,
            )
            self._handles[member.name] = handle
            synced.append(handle)

        return synced

    # ── Agent 管理 ──────────────────────────────────────────

    async def spawn(self, options: SpawnAgentOptions) -> AgentHandle:
        """Spawn Agent（5 步流程）。

        正确顺序（确保进程启动前数据已就绪）:
        1. 分配颜色 + 注册成员到 config.json
        2. 写入初始 prompt 到 inbox
        3. 启动进程（tmux pane 创建 + 命令发送）
        4. 创建 AgentHandle
        5. 发射 agent:spawned 事件

        如果步骤 3 失败，回滚步骤 1-2。

        Returns:
            AgentHandle 代理对象
        """
        self._check_initialized()

        # 1. 注册成员（分配颜色 + 写入 config.json + 创建空 inbox）
        member = await self._team_manager.register_member(
            name=options.name,
            agent_type=options.agent_type,
            model=options.model,
            cwd=self._options.cwd or os.getcwd(),
            plan_mode_required=options.plan_mode_required,
            backend_type=TMUX_BACKEND,
        )

        # register_member sets is_active=False; spawn marks active + saves prompt
        await self._team_manager.update_member(
            options.name, is_active=True, prompt=options.prompt,
        )

        # 2. Write initial prompt to inbox (must be ready before process start)
        inbox = InboxIO(self._options.team_name, options.name)
        await inbox.write_initial_prompt(TEAM_LEAD_AGENT_TYPE, options.prompt)

        # 3. Start process
        try:
            pane_id = await self._process_manager.spawn(
                options,
                team_name=self._options.team_name,
                color=member.color or "",
                parent_session_id=self._session_id,
            )
        except Exception:
            # Rollback: remove registered member
            with contextlib.suppress(Exception):
                await self._team_manager.remove_member(options.name)
            raise

        # Update member's pane_id
        await self._team_manager.update_member(options.name, tmux_pane_id=pane_id)

        # 4. Create AgentHandle
        handle = AgentHandle(
            options.name, self,
            pane_id=pane_id, color=member.color,
        )
        self._handles[options.name] = handle

        # 5. 发射事件
        await self.emit("agent:spawned", options.name, pane_id)
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
            recipient, content, summary=summary,
        )

    async def send_shutdown_request(self, agent_name: str, reason: str) -> str:
        """发送关闭请求，返回 request_id。"""
        self._check_initialized()
        return await self._message_builder.send_shutdown_request(agent_name, reason)

    async def kill_agent(self, agent_name: str) -> None:
        """强制终止 Agent。"""
        self._check_initialized()
        await self._process_manager.kill(agent_name)
        self._handles.pop(agent_name, None)
        await self._team_manager.remove_member(agent_name)

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
        recipients = [
            name for name in self._handles
            if not exclude or name not in exclude
        ]
        await self._message_builder.broadcast(
            content, recipients, summary=summary,
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
            agent_name, request_id,
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
            task_id, status=status, owner=owner, **kwargs,
        )

        # owner 变更时发送 task_assignment
        if (
            owner is not ...
            and owner is not None
            and (old_task is None or old_task.owner != owner)
        ):
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
        """处理 Agent 关闭确认。"""
        self._process_manager.untrack(agent_name)
        self._handles.pop(agent_name, None)
        with contextlib.suppress(AgentNotFoundError):
            await self._team_manager.remove_member(agent_name)

    async def _on_poller_error(self, exc: Exception, _context: str) -> None:
        """InboxPoller 异常转发到 error 事件。"""
        await self.emit("error", exc)
