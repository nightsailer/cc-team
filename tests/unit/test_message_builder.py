"""message_builder.py 单元测试 — 结构化消息构造验证。

测试覆盖:
- send_plain（纯文本 / 可选字段）
- send_shutdown_request（request_id 格式）
- send_task_assignment（任务字段映射）
- send_plan_approval（批准/拒绝）
- broadcast（多接收者）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cc_team.paths as paths_mod
import cc_team._serialization as ser_mod
import cc_team.inbox as inbox_mod
import cc_team.message_builder as mb_mod
from cc_team.message_builder import MessageBuilder
from cc_team.types import TaskFile


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
    # 创建 inboxes 目录
    paths_mod.inboxes_dir("test-team").mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture
def builder(isolated_home: Path) -> MessageBuilder:
    return MessageBuilder("test-team", lead_name="boss")


def _read_inbox(team: str, agent: str) -> list[dict]:
    """读取 inbox 文件原始 JSON。"""
    path = paths_mod.inbox_path(team, agent)
    if not path.exists():
        return []
    return json.loads(path.read_text())


# ── send_plain ───────────────────────────────────────────────


class TestSendPlain:
    """send_plain() 测试。"""

    @pytest.mark.asyncio
    async def test_sends_message(self, builder: MessageBuilder) -> None:
        """发送纯文本消息。"""
        await builder.send_plain("worker-1", "Hello there")
        msgs = _read_inbox("test-team", "worker-1")
        assert len(msgs) == 1
        assert msgs[0]["from"] == "boss"
        assert msgs[0]["text"] == "Hello there"

    @pytest.mark.asyncio
    async def test_with_summary(self, builder: MessageBuilder) -> None:
        """包含 summary 字段。"""
        await builder.send_plain("w", "hi", summary="Quick note")
        msgs = _read_inbox("test-team", "w")
        assert msgs[0]["summary"] == "Quick note"

    @pytest.mark.asyncio
    async def test_with_color(self, builder: MessageBuilder) -> None:
        """包含 color 字段。"""
        await builder.send_plain("w", "hi", color="blue")
        msgs = _read_inbox("test-team", "w")
        assert msgs[0]["color"] == "blue"

    @pytest.mark.asyncio
    async def test_custom_from_name(self, builder: MessageBuilder) -> None:
        """自定义 from_name。"""
        await builder.send_plain("w", "hi", from_name="custom")
        msgs = _read_inbox("test-team", "w")
        assert msgs[0]["from"] == "custom"


# ── send_shutdown_request ────────────────────────────────────


class TestSendShutdownRequest:
    """send_shutdown_request() 测试。"""

    @pytest.mark.asyncio
    async def test_returns_request_id(self, builder: MessageBuilder) -> None:
        """返回格式化的 request_id。"""
        req_id = await builder.send_shutdown_request("agent-1", "done")
        assert req_id.startswith("shutdown-")
        assert "@agent-1" in req_id

    @pytest.mark.asyncio
    async def test_message_in_inbox(self, builder: MessageBuilder) -> None:
        """消息写入到目标 inbox。"""
        await builder.send_shutdown_request("agent-1", "done")
        msgs = _read_inbox("test-team", "agent-1")
        assert len(msgs) == 1
        body = json.loads(msgs[0]["text"])
        assert body["type"] == "shutdown_request"
        assert body["reason"] == "done"
        assert body["from"] == "boss"


# ── send_task_assignment ────────────────────────────────────


class TestSendTaskAssignment:
    """send_task_assignment() 测试。"""

    @pytest.mark.asyncio
    async def test_writes_task_info(self, builder: MessageBuilder) -> None:
        """任务信息正确写入 inbox。"""
        task = TaskFile(
            id="42", subject="Build feature", description="Details here",
        )
        await builder.send_task_assignment("worker-1", task)

        msgs = _read_inbox("test-team", "worker-1")
        assert len(msgs) == 1
        body = json.loads(msgs[0]["text"])
        assert body["type"] == "task_assignment"
        assert body["taskId"] == "42"
        assert body["subject"] == "Build feature"
        assert body["assignedBy"] == "boss"


# ── send_plan_approval ──────────────────────────────────────


class TestSendPlanApproval:
    """send_plan_approval() 测试。"""

    @pytest.mark.asyncio
    async def test_approve(self, builder: MessageBuilder) -> None:
        """批准计划。"""
        await builder.send_plan_approval("agent-1", "req-1", approved=True)
        msgs = _read_inbox("test-team", "agent-1")
        body = json.loads(msgs[0]["text"])
        assert body["type"] == "plan_approval_response"
        assert body["approved"] is True
        assert "permissionMode" in body

    @pytest.mark.asyncio
    async def test_reject_with_feedback(self, builder: MessageBuilder) -> None:
        """拒绝计划含反馈。"""
        await builder.send_plan_approval(
            "agent-1", "req-1", approved=False, feedback="Need more detail",
        )
        msgs = _read_inbox("test-team", "agent-1")
        body = json.loads(msgs[0]["text"])
        assert body["approved"] is False
        assert body["feedback"] == "Need more detail"
        assert "permissionMode" not in body


# ── broadcast ────────────────────────────────────────────────


class TestBroadcast:
    """broadcast() 测试。"""

    @pytest.mark.asyncio
    async def test_sends_to_all_recipients(self, builder: MessageBuilder) -> None:
        """广播到多个接收者。"""
        await builder.broadcast("Alert!", ["a", "b", "c"])
        for name in ["a", "b", "c"]:
            msgs = _read_inbox("test-team", name)
            assert len(msgs) == 1
            assert msgs[0]["text"] == "Alert!"

    @pytest.mark.asyncio
    async def test_empty_recipients_noop(self, builder: MessageBuilder) -> None:
        """空接收列表不操作。"""
        await builder.broadcast("Alert!", [])  # 不应抛出
