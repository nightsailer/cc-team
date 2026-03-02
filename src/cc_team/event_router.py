"""事件路由器。

从 Controller 中提取的消息路由逻辑。
将 InboxPoller 收到的消息映射为事件发射。

路由规则:
  idle_notification     → emit("idle", agent_name)
  shutdown_approved     → emit("shutdown:approved", agent_name, msg)
  plan_approval_request → emit("plan:approval_request", agent_name, msg)
  permission_request    → emit("permission:request", agent_name, msg)
  task_assignment       → pass (Lead 不处理自己的 task_assignment)
  其他/纯文本           → emit("message", agent_name, raw_msg)
"""

from __future__ import annotations

from typing import Any

from cc_team.events import AsyncEventEmitter
from cc_team.types import InboxMessage


class EventRouter:
    """消息路由器。

    将 InboxPoller 的回调转换为 AsyncEventEmitter 事件。

    Args:
        emitter: 事件发射器实例
    """

    def __init__(self, emitter: AsyncEventEmitter) -> None:
        self._emitter = emitter

    async def route(
        self,
        msg: InboxMessage,
        msg_type: str | None,
        parsed: Any | None,
    ) -> None:
        """路由单条消息到对应事件。

        此方法设计为 InboxPoller.on_message 的 handler。

        Args:
            msg: 原始 InboxMessage
            msg_type: 结构化消息类型，纯文本为 None
            parsed: 解析后的 dataclass，纯文本为 None
        """
        sender = msg.from_

        if msg_type is None:
            # 纯文本消息
            await self._emitter.emit("message", sender, msg)
            return

        match msg_type:
            case "idle_notification":
                await self._emitter.emit("idle", sender)

            case "shutdown_approved":
                await self._emitter.emit("shutdown:approved", sender, parsed)

            case "plan_approval_request":
                await self._emitter.emit("plan:approval_request", sender, parsed)

            case "permission_request":
                await self._emitter.emit("permission:request", sender, parsed)

            case "session_relay":
                await self._emitter.emit("session:relayed", sender, parsed)

            case "task_assignment":
                # Lead 不处理自己收到的 task_assignment
                pass

            case _:
                # 未知结构化消息作为普通消息处理
                await self._emitter.emit("message", sender, msg)
