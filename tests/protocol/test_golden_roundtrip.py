"""协议兼容性测试 — 黄金数据集 roundtrip 验证。

确保所有 dataclass ↔ JSON 转换无损，
且输出格式与 Claude Code 原生协议 100% 一致。
"""

from __future__ import annotations

import json

from cc_team._serialization import (
    build_message_body,
    from_json_dict,
    inbox_message_from_dict,
    inbox_message_to_dict,
    parse_message_body,
    task_file_from_dict,
    task_file_to_dict,
    team_config_from_dict,
    team_config_to_dict,
    to_json_dict,
)
from cc_team.types import (
    IdleNotificationMessage,
    InboxMessage,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PlanApprovalRequestMessage,
    PlanApprovalResponseMessage,
    ShutdownApprovedMessage,
    ShutdownRequestMessage,
    TaskAssignmentMessage,
    TaskFile,
    TeamConfig,
    TeamMember,
)


# ── 黄金数据集（独立构造，基于协议规范）─────────────────────


class TestTeamConfigRoundtrip:
    """TeamConfig 序列化/反序列化 roundtrip。"""

    def _make_config(self) -> TeamConfig:
        return TeamConfig(
            name="test-team",
            description="Golden test team",
            created_at=1772193600000,
            lead_agent_id="team-lead@test-team",
            lead_session_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            members=[
                TeamMember(
                    agent_id="team-lead@test-team",
                    name="team-lead",
                    agent_type="team-lead",
                    model="claude-sonnet-4-6",
                    joined_at=1772193600000,
                    tmux_pane_id="",
                    cwd="/workspace",
                ),
                TeamMember(
                    agent_id="researcher@test-team",
                    name="researcher",
                    agent_type="general-purpose",
                    model="claude-sonnet-4-6",
                    joined_at=1772193601000,
                    tmux_pane_id="%14",
                    cwd="/workspace",
                    prompt="You are a researcher.",
                    color="blue",
                    plan_mode_required=False,
                    backend_type="tmux",
                    is_active=True,
                ),
            ],
        )

    def test_config_roundtrip(self) -> None:
        original = self._make_config()
        d = team_config_to_dict(original)
        restored = team_config_from_dict(d)

        assert restored.name == original.name
        assert restored.created_at == original.created_at
        assert restored.lead_agent_id == original.lead_agent_id
        assert len(restored.members) == len(original.members)

    def test_lead_member_roundtrip(self) -> None:
        """Lead 成员 8 字段完整保留。"""
        original = self._make_config()
        d = team_config_to_dict(original)
        restored = team_config_from_dict(d)

        lead = restored.members[0]
        assert lead.agent_id == "team-lead@test-team"
        assert lead.tmux_pane_id == ""
        assert lead.subscriptions == []
        assert lead.prompt is None  # Lead 无此字段

    def test_teammate_member_roundtrip(self) -> None:
        """Teammate 13 字段完整保留。"""
        original = self._make_config()
        d = team_config_to_dict(original)
        restored = team_config_from_dict(d)

        mate = restored.members[1]
        assert mate.prompt == "You are a researcher."
        assert mate.color == "blue"
        assert mate.plan_mode_required is False
        assert mate.backend_type == "tmux"
        assert mate.is_active is True


class TestTaskFileRoundtrip:
    """TaskFile 序列化/反序列化 roundtrip。"""

    def test_new_task_roundtrip(self) -> None:
        """新建 task（无 owner/metadata）roundtrip。"""
        original = TaskFile(id="1", subject="Do work", description="Details")
        d = task_file_to_dict(original)
        restored = task_file_from_dict(d)

        assert restored.id == original.id
        assert restored.status == "pending"
        assert restored.owner is None

    def test_assigned_task_roundtrip(self) -> None:
        original = TaskFile(
            id="2", subject="Implement", description="Details",
            status="in_progress", active_form="Implementing",
            owner="researcher", blocks=["3"], blocked_by=["1"],
            metadata={"priority": "high"},
        )
        d = task_file_to_dict(original)
        restored = task_file_from_dict(d)

        assert restored.owner == "researcher"
        assert restored.blocks == ["3"]
        assert restored.blocked_by == ["1"]
        assert restored.metadata == {"priority": "high"}


class TestInboxMessageRoundtrip:
    """InboxMessage 序列化/反序列化 roundtrip。"""

    def test_minimal_message_roundtrip(self) -> None:
        """仅 4 必选字段。"""
        original = InboxMessage(from_="lead", text="Hello", timestamp="2026-02-28T10:00:00.000Z")
        d = inbox_message_to_dict(original)
        restored = inbox_message_from_dict(d)

        assert restored.from_ == original.from_
        assert restored.text == original.text
        assert restored.read == original.read
        assert restored.summary is None
        assert restored.color is None

    def test_full_message_roundtrip(self) -> None:
        """含 summary + color。"""
        original = InboxMessage(
            from_="worker", text="Done", timestamp="t",
            read=True, summary="Completed", color="green",
        )
        d = inbox_message_to_dict(original)
        restored = inbox_message_from_dict(d)

        assert restored.summary == "Completed"
        assert restored.color == "green"


class TestStructuredMessageRoundtrip:
    """9 种结构化消息 build → parse roundtrip。"""

    def test_task_assignment_roundtrip(self) -> None:
        original = TaskAssignmentMessage(
            task_id="1", subject="Research API",
            description="Investigate endpoints",
            assigned_by="team-lead",
            timestamp="2026-02-28T10:00:00.000Z",
        )
        text = build_message_body("task_assignment", original)
        result = parse_message_body(text)
        assert result is not None
        msg_type, restored = result
        assert msg_type == "task_assignment"
        assert restored.task_id == original.task_id
        assert restored.assigned_by == original.assigned_by

    def test_idle_notification_roundtrip(self) -> None:
        original = IdleNotificationMessage(
            from_="worker",
            timestamp="2026-02-28T10:01:00.000Z",
            idle_reason="available",
            summary="[to coder] Check docs",
        )
        text = build_message_body("idle_notification", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.from_ == "worker"
        assert restored.idle_reason == "available"
        assert restored.summary == "[to coder] Check docs"

    def test_shutdown_request_roundtrip(self) -> None:
        original = ShutdownRequestMessage(
            request_id="shutdown-1772193660000@worker",
            from_="team-lead",
            reason="Task complete",
            timestamp="2026-02-28T10:01:00.000Z",
        )
        text = build_message_body("shutdown_request", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.request_id == original.request_id

    def test_shutdown_approved_roundtrip(self) -> None:
        original = ShutdownApprovedMessage(
            request_id="shutdown-1772193660000@worker",
            from_="worker",
            timestamp="2026-02-28T10:01:05.000Z",
            pane_id="%14",
            backend_type="tmux",
        )
        text = build_message_body("shutdown_approved", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.pane_id == "%14"
        assert restored.backend_type == "tmux"

    def test_plan_approval_request_roundtrip(self) -> None:
        original = PlanApprovalRequestMessage(
            from_="planner",
            timestamp="2026-02-28T10:02:00.000Z",
            plan_file_path="~/.claude/plans/test.md",
            plan_content="# Plan\n\n1. Step one\n2. Step two",
            request_id="plan_approval-1772193720000@planner@test-team",
        )
        text = build_message_body("plan_approval_request", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.plan_content == original.plan_content
        assert restored.request_id.startswith("plan_approval-")

    def test_plan_approval_response_approve_roundtrip(self) -> None:
        """approve=True: 含 permissionMode，无 feedback。"""
        original = PlanApprovalResponseMessage(
            request_id="plan_approval-123@p@t",
            approved=True,
            timestamp="2026-02-28T10:02:01.000Z",
            permission_mode="default",
        )
        text = build_message_body("plan_approval_response", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.approved is True
        assert restored.permission_mode == "default"
        assert restored.feedback is None

    def test_plan_approval_response_reject_roundtrip(self) -> None:
        """approve=False: 含 feedback，无 permissionMode。"""
        original = PlanApprovalResponseMessage(
            request_id="plan_approval-123@p@t",
            approved=False,
            timestamp="2026-02-28T10:02:01.000Z",
            feedback="Add error handling",
        )
        text = build_message_body("plan_approval_response", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.approved is False
        assert restored.feedback == "Add error handling"
        assert restored.permission_mode is None

    def test_permission_request_roundtrip(self) -> None:
        original = PermissionRequestMessage(
            request_id="perm-1772193780000-abc1234",
            agent_id="delegate-agent",
            tool_name="Bash",
            tool_use_id="toolu_test123",
            description="Run mkdir /tmp/test",
            input={"command": "mkdir /tmp/test"},
            permission_suggestions=[
                {"type": "addDirectories", "directories": ["/tmp/test"]},
            ],
        )
        text = build_message_body("permission_request", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.request_id == original.request_id
        assert restored.tool_name == "Bash"
        assert len(restored.permission_suggestions) == 1

    def test_permission_response_success_roundtrip(self) -> None:
        original = PermissionResponseMessage(
            request_id="perm-123-abc",
            subtype="success",
            response={"updated_input": {"command": "ls"}, "permission_updates": []},
        )
        text = build_message_body("permission_response", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.subtype == "success"
        assert restored.response is not None

    def test_permission_response_error_roundtrip(self) -> None:
        original = PermissionResponseMessage(
            request_id="perm-123-abc",
            subtype="error",
            error="Operation not permitted",
        )
        text = build_message_body("permission_response", original)
        result = parse_message_body(text)
        assert result is not None
        _, restored = result
        assert restored.subtype == "error"
        assert restored.error == "Operation not permitted"
        assert restored.response is None


class TestGoldenJsonParsing:
    """从黄金 JSON 样本解析 — 验证与 Claude Code 原生格式兼容。"""

    def test_parse_native_shutdown_request(self) -> None:
        """模拟 Claude Code 原生产生的 shutdown_request JSON。"""
        native_json = (
            '{"type":"shutdown_request",'
            '"requestId":"shutdown-1772193660000@researcher",'
            '"from":"team-lead",'
            '"reason":"Task complete",'
            '"timestamp":"2026-02-28T10:01:00.000Z"}'
        )
        result = parse_message_body(native_json)
        assert result is not None
        msg_type, msg = result
        assert msg_type == "shutdown_request"
        assert msg.request_id == "shutdown-1772193660000@researcher"
        assert msg.from_ == "team-lead"

    def test_parse_native_permission_request(self) -> None:
        """模拟 Claude Code 原生产生的 permission_request JSON（snake_case）。"""
        native_json = json.dumps({
            "type": "permission_request",
            "request_id": "perm-1772193780000-abc1234",
            "agent_id": "delegate-agent",
            "tool_name": "Bash",
            "tool_use_id": "toolu_abc",
            "description": "Run command",
            "input": {"command": "ls"},
            "permission_suggestions": [],
        })
        result = parse_message_body(native_json)
        assert result is not None
        msg_type, msg = result
        assert msg_type == "permission_request"
        assert msg.request_id == "perm-1772193780000-abc1234"
