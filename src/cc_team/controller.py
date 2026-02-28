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

from cc_team._serialization import now_ms
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
    ControllerOptions,
    SpawnAgentOptions,
    TaskFile,
    TaskStatus,
    TeamMember,
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
        process_manager: ProcessManager | None = None,
    ) -> None:
        super().__init__()
        self._options = options
        self._initialized = False

        # 子系统
        self._team_manager = TeamManager(options.team_name)
        self._task_manager = TaskManager(options.team_name)
        self._process_manager = process_manager or ProcessManager()
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
    def process_manager(self) -> ProcessManager:
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

        # 启动 Lead inbox 轮询
        self._poller = InboxPoller(
            self._options.team_name, "team-lead",
        )
        self._poller.on_message(self._event_router.route)
        self._poller.on_error(self._on_poller_error)
        await self._poller.start()

        self._initialized = True

    async def shutdown(self) -> None:
        """关闭 Controller: 停止轮询 + 终止所有 Agent + 销毁团队。"""
        if not self._initialized:
            return

        # 停止轮询
        if self._poller:
            await self._poller.stop()

        # 强制终止所有存活 Agent
        for name in list(self._handles.keys()):
            with contextlib.suppress(Exception):
                await self._process_manager.kill(name)

        # 销毁团队
        await self._team_manager.destroy()
        self._handles.clear()
        self._initialized = False

    def _check_initialized(self) -> None:
        if not self._initialized:
            raise NotInitializedError("Controller not initialized, call init() first")

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

        # 1. 分配颜色 + 注册成员
        color = self._team_manager.next_color()
        member = TeamMember(
            agent_id=f"{options.name}@{self._options.team_name}",
            name=options.name,
            agent_type=options.agent_type,
            model=options.model,
            joined_at=now_ms(),
            tmux_pane_id="",  # 稍后填充
            cwd=self._options.cwd or os.getcwd(),
            prompt=options.prompt,
            color=color,
            plan_mode_required=options.plan_mode_required,
            backend_type="tmux",
            is_active=True,
        )
        await self._team_manager.add_member(member)

        # 2. 写入初始 prompt 到 inbox（进程启动前必须就绪）
        inbox = InboxIO(self._options.team_name, options.name)
        await inbox.write_initial_prompt("team-lead", options.prompt)

        # 3. 启动进程
        try:
            pane_id = await self._process_manager.spawn(
                options,
                team_name=self._options.team_name,
                color=color,
                parent_session_id=self._session_id,
            )
        except Exception:
            # 回滚: 移除已注册的成员
            with contextlib.suppress(Exception):
                await self._team_manager.remove_member(options.name)
            raise

        # 更新成员的 pane_id
        await self._team_manager.update_member(options.name, tmux_pane_id=pane_id)

        # 4. 创建 AgentHandle
        handle = AgentHandle(
            options.name, self,
            pane_id=pane_id, color=color,
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
        """发送消息到指定 Agent。"""
        self._check_initialized()
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
