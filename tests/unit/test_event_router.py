"""event_router.py 单元测试 — 消息路由规则验证。

路由规则:
  idle_notification     → emit("idle", sender)
  shutdown_approved     → emit("shutdown:approved", sender, parsed)
  plan_approval_request → emit("plan:approval_request", sender, parsed)
  permission_request    → emit("permission:request", sender, parsed)
  task_assignment       → 静默忽略
  纯文本/未知类型       → emit("message", sender, msg)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cc_team.event_router import EventRouter
from cc_team.events import AsyncEventEmitter
from cc_team.types import InboxMessage

# ── Helpers ───────────────────────────────────────────────────


def _msg(from_: str = "worker-1", text: str = "hello") -> InboxMessage:
    return InboxMessage(
        from_=from_,
        text=text,
        timestamp="2026-02-28T10:00:00.000Z",
    )


# ── 路由规则测试 ──────────────────────────────────────────────


class TestRouteRules:
    """route() 消息类型到事件的映射。"""

    @pytest.mark.asyncio
    async def test_plain_text_emits_message(self) -> None:
        """纯文本消息 → "message" 事件。"""
        emitter = AsyncEventEmitter()
        captured: list[tuple] = []

        async def handler(sender: str, msg: InboxMessage) -> None:
            captured.append((sender, msg))

        emitter.on("message", handler)
        router = EventRouter(emitter)

        msg = _msg(text="plain text")
        await router.route(msg, None, None)

        assert len(captured) == 1
        assert captured[0][0] == "worker-1"
        assert captured[0][1].text == "plain text"

    @pytest.mark.asyncio
    async def test_idle_notification_emits_idle(self) -> None:
        """idle_notification → "idle" 事件。"""
        emitter = AsyncEventEmitter()
        captured: list[str] = []

        async def handler(sender: str) -> None:
            captured.append(sender)

        emitter.on("idle", handler)
        router = EventRouter(emitter)
        await router.route(_msg(), "idle_notification", MagicMock())

        assert captured == ["worker-1"]

    @pytest.mark.asyncio
    async def test_shutdown_approved(self) -> None:
        """shutdown_approved → "shutdown:approved" 事件（含 parsed）。"""
        emitter = AsyncEventEmitter()
        captured: list[tuple] = []

        async def handler(sender: str, parsed: object) -> None:
            captured.append((sender, parsed))

        emitter.on("shutdown:approved", handler)
        router = EventRouter(emitter)

        mock_parsed = MagicMock()
        await router.route(_msg(), "shutdown_approved", mock_parsed)

        assert captured[0][0] == "worker-1"
        assert captured[0][1] is mock_parsed

    @pytest.mark.asyncio
    async def test_plan_approval_request(self) -> None:
        """plan_approval_request → "plan:approval_request" 事件。"""
        emitter = AsyncEventEmitter()
        captured: list[tuple] = []

        async def handler(sender: str, parsed: object) -> None:
            captured.append((sender, parsed))

        emitter.on("plan:approval_request", handler)
        router = EventRouter(emitter)
        await router.route(_msg(), "plan_approval_request", MagicMock())

        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_permission_request(self) -> None:
        """permission_request → "permission:request" 事件。"""
        emitter = AsyncEventEmitter()
        captured: list[tuple] = []

        async def handler(sender: str, parsed: object) -> None:
            captured.append((sender, parsed))

        emitter.on("permission:request", handler)
        router = EventRouter(emitter)
        await router.route(_msg(), "permission_request", MagicMock())

        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_task_assignment_silenced(self) -> None:
        """task_assignment 被静默忽略。"""
        emitter = AsyncEventEmitter()
        captured: list[str] = []

        async def handler(*args: object) -> None:
            captured.append("called")

        # 监听所有可能的事件
        for evt in ["message", "idle", "shutdown:approved"]:
            emitter.on(evt, handler)

        router = EventRouter(emitter)
        await router.route(_msg(), "task_assignment", MagicMock())

        assert captured == []

    @pytest.mark.asyncio
    async def test_unknown_structured_type_emits_message(self) -> None:
        """未知结构化类型 → "message" 事件。"""
        emitter = AsyncEventEmitter()
        captured: list[str] = []

        async def handler(sender: str, msg: InboxMessage) -> None:
            captured.append(sender)

        emitter.on("message", handler)
        router = EventRouter(emitter)
        await router.route(_msg(), "unknown_type_xyz", MagicMock())

        assert captured == ["worker-1"]

    @pytest.mark.asyncio
    async def test_session_relay_routes_event(self) -> None:
        """session_relay → "session:relayed" 事件。"""
        emitter = AsyncEventEmitter()
        captured: list[tuple] = []

        async def handler(sender: str, parsed: object) -> None:
            captured.append((sender, parsed))

        emitter.on("session:relayed", handler)
        router = EventRouter(emitter)

        mock_parsed = MagicMock()
        await router.route(_msg(), "session_relay", mock_parsed)

        assert len(captured) == 1
        assert captured[0][0] == "worker-1"
        assert captured[0][1] is mock_parsed

    @pytest.mark.asyncio
    async def test_sender_extracted_from_msg(self) -> None:
        """sender 从 msg.from_ 提取。"""
        emitter = AsyncEventEmitter()
        captured: list[str] = []

        async def handler(sender: str) -> None:
            captured.append(sender)

        emitter.on("idle", handler)
        router = EventRouter(emitter)
        await router.route(_msg(from_="custom-agent"), "idle_notification", None)

        assert captured == ["custom-agent"]
