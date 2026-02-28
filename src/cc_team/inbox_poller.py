"""异步 Inbox 消息轮询器。

以固定间隔轮询 inbox 文件，检测新消息并分发事件。
使用 mtime 优化减少不必要的文件读取。

用法:
    poller = InboxPoller(team_name, agent_name, interval=0.5)
    poller.on_message(my_handler)
    poller.on_error(my_error_handler)
    await poller.start()
    # ... later
    await poller.stop()
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from cc_team._serialization import parse_message_body
from cc_team.inbox import InboxIO
from cc_team.types import InboxMessage

# Handler 类型
MessageHandler = Callable[[InboxMessage, str | None, Any | None], Coroutine[Any, Any, None]]
ErrorHandler = Callable[[Exception, str], Coroutine[Any, Any, None]]


class InboxPoller:
    """异步 inbox 轮询器。

    特性:
    - mtime 优化: 仅在文件修改时间变化时读取
    - 自动标记已读
    - 结构化消息自动解析
    - 支持注册多个 handler
    - 异常通过 error handler 报告（不静默吞掉）

    Args:
        team_name: 团队名称
        agent_name: Agent 名称
        interval: 轮询间隔（秒），默认 0.5
    """

    def __init__(
        self,
        team_name: str,
        agent_name: str,
        *,
        interval: float = 0.5,
    ) -> None:
        self._inbox = InboxIO(team_name, agent_name)
        self._interval = interval
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_mtime: int = 0
        self._handlers: list[MessageHandler] = []
        self._error_handlers: list[ErrorHandler] = []

    @property
    def running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        """注册消息 handler。

        Handler 签名: async def handler(msg: InboxMessage, msg_type: str | None, parsed: Any | None)
          - msg: 原始 InboxMessage
          - msg_type: 结构化消息类型（如 "shutdown_request"），纯文本为 None
          - parsed: 解析后的 dataclass 实例，纯文本为 None
        """
        self._handlers.append(handler)

    def on_error(self, handler: ErrorHandler) -> None:
        """注册错误 handler。

        Handler 签名: async def handler(exc: Exception, context: str)
          - exc: 异常实例
          - context: 异常发生的上下文描述（如 "poll", "dispatch"）
        """
        self._error_handlers.append(handler)

    # ── 生命周期 ────────────────────────────────────────────

    async def start(self) -> None:
        """启动轮询循环。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """停止轮询循环。"""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def poll_once(self) -> list[InboxMessage]:
        """手动触发单次轮询（用于测试）。

        Returns:
            本次处理的消息列表
        """
        return await self._do_poll(force=True)

    # ── 轮询实现 ────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """主轮询循环。"""
        while self._running:
            try:
                await self._do_poll()
            except Exception as exc:
                # 轮询异常不中断循环，但通过 error handler 报告
                await self._report_error(exc, "poll")
            await asyncio.sleep(self._interval)

    async def _do_poll(self, *, force: bool = False) -> list[InboxMessage]:
        """单次轮询。

        Args:
            force: 忽略 mtime 检查强制读取

        Returns:
            处理的消息列表
        """
        # mtime 优化: 文件未修改则跳过
        current_mtime = self._inbox.mtime_ns()
        if not force and current_mtime <= self._last_mtime:
            return []

        # 标记已读并获取新消息
        messages = await self._inbox.mark_read()
        if not messages:
            # 文件变更但无新消息，重新获取最新 mtime 避免无效重读
            self._last_mtime = self._inbox.mtime_ns()
            return []

        # 分发消息（全部完成后才更新 mtime，异常时保留旧值以便重试）
        for msg in messages:
            await self._dispatch(msg)

        # 更新 mtime 为 mark_read 后的实际值，避免因写操作变更 mtime 导致下轮无效重读
        self._last_mtime = self._inbox.mtime_ns()
        return messages

    async def _dispatch(self, msg: InboxMessage) -> None:
        """分发单条消息到所有 handler。"""
        # 尝试解析结构化消息
        parsed_result = parse_message_body(msg.text)
        if parsed_result is not None:
            msg_type, parsed_obj = parsed_result
        else:
            msg_type, parsed_obj = None, None

        for handler in self._handlers:
            try:
                await handler(msg, msg_type, parsed_obj)
            except Exception as exc:
                # handler 异常不影响其他 handler，但通过 error handler 报告
                await self._report_error(exc, "dispatch")

    async def _report_error(self, exc: Exception, context: str) -> None:
        """报告异常到所有 error handler。"""
        for handler in self._error_handlers:
            try:
                await handler(exc, context)
            except Exception:
                # error handler 自身的异常被静默忽略，防止无限递归
                pass
