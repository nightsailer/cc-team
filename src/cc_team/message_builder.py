"""结构化消息构造器。

从 Controller 中提取的消息构造逻辑。
负责构建所有结构化消息的 JSON body 并写入 inbox。

支持的消息类型:
- shutdown_request
- task_assignment
- plan_approval_response
- permission_response (暂不实现完整逻辑)
"""

from __future__ import annotations

from cc_team._serialization import build_message_body, now_iso, now_ms
from cc_team.inbox import InboxIO
from cc_team.types import (
    InboxMessage,
    PlanApprovalResponseMessage,
    ShutdownRequestMessage,
    TaskAssignmentMessage,
    TaskFile,
)


class MessageBuilder:
    """结构化消息构造器。

    Args:
        team_name: 团队名称
        lead_name: Team Lead 名称
    """

    def __init__(self, team_name: str, lead_name: str = "team-lead") -> None:
        self._team_name = team_name
        self._lead_name = lead_name

    # ── 消息发送 ────────────────────────────────────────────

    async def send_plain(
        self,
        recipient: str,
        content: str,
        *,
        summary: str | None = None,
        from_name: str | None = None,
        color: str | None = None,
    ) -> None:
        """发送纯文本消息。"""
        sender = from_name or self._lead_name
        msg = InboxMessage(
            from_=sender,
            text=content,
            timestamp=now_iso(),
            summary=summary,
            color=color,
        )
        inbox = InboxIO(self._team_name, recipient)
        await inbox.write(msg)

    async def send_shutdown_request(
        self,
        recipient: str,
        reason: str,
    ) -> str:
        """发送关闭请求，返回 request_id。"""
        timestamp = now_iso()
        request_id = f"shutdown-{now_ms()}@{recipient}"

        body = ShutdownRequestMessage(
            request_id=request_id,
            from_=self._lead_name,
            reason=reason,
            timestamp=timestamp,
        )

        msg = InboxMessage(
            from_=self._lead_name,
            text=build_message_body("shutdown_request", body),
            timestamp=timestamp,
        )
        inbox = InboxIO(self._team_name, recipient)
        await inbox.write(msg)

        return request_id

    async def send_task_assignment(
        self,
        recipient: str,
        task: TaskFile,
    ) -> None:
        """发送任务分配通知。"""
        body = TaskAssignmentMessage(
            task_id=task.id,
            subject=task.subject,
            description=task.description,
            assigned_by=self._lead_name,
            timestamp=now_iso(),
        )

        msg = InboxMessage(
            from_=self._lead_name,
            text=build_message_body("task_assignment", body),
            timestamp=now_iso(),
        )
        inbox = InboxIO(self._team_name, recipient)
        await inbox.write(msg)

    async def send_plan_approval(
        self,
        recipient: str,
        request_id: str,
        *,
        approved: bool = True,
        permission_mode: str = "default",
        feedback: str | None = None,
    ) -> None:
        """发送计划审批响应。"""
        body = PlanApprovalResponseMessage(
            request_id=request_id,
            approved=approved,
            timestamp=now_iso(),
            permission_mode=permission_mode if approved else None,
            feedback=feedback if not approved else None,
        )

        msg = InboxMessage(
            from_=self._lead_name,
            text=build_message_body("plan_approval_response", body),
            timestamp=now_iso(),
        )
        inbox = InboxIO(self._team_name, recipient)
        await inbox.write(msg)

    async def broadcast(
        self,
        content: str,
        recipients: list[str],
        *,
        summary: str | None = None,
        from_name: str | None = None,
    ) -> None:
        """广播消息到多个 Agent。"""
        for recipient in recipients:
            await self.send_plain(
                recipient, content,
                summary=summary, from_name=from_name,
            )
