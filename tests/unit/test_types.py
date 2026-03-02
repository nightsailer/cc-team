"""types.py 单元测试 — 数据模型 schema 验证。"""

from __future__ import annotations

from cc_team.types import (
    AGENT_COLORS,
    AgentController,
    IdleNotificationMessage,
    InboxMessage,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PlanApprovalRequestMessage,
    PlanApprovalResponseMessage,
    ShutdownApprovedMessage,
    ShutdownRequestMessage,
    SpawnAgentOptions,
    TaskAssignmentMessage,
    TaskFile,
    TeamConfig,
    TeamMember,
)

# ── TeamMember ──────────────────────────────────────────────


class TestTeamMember:
    """TeamMember dataclass 测试。"""

    def test_lead_member_has_8_required_fields(self) -> None:
        """Lead 成员仅使用公共字段，Teammate 专有字段为 None。"""
        lead = TeamMember(
            agent_id="team-lead@my-team",
            name="team-lead",
            agent_type="team-lead",
            model="claude-sonnet-4-6",
            joined_at=1772193600000,
            tmux_pane_id="",
            cwd="/workspace",
        )
        assert lead.agent_id == "team-lead@my-team"
        assert lead.tmux_pane_id == ""  # Lead 为空字符串
        assert lead.subscriptions == []  # 预留字段始终空
        # Teammate 专有字段全部为 None
        assert lead.prompt is None
        assert lead.color is None
        assert lead.plan_mode_required is None
        assert lead.backend_type is None
        assert lead.is_active is None

    def test_teammate_member_has_13_fields(self) -> None:
        """Teammate 使用全部 13 个字段。"""
        mate = TeamMember(
            agent_id="researcher@my-team",
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
        )
        assert mate.prompt == "You are a researcher."
        assert mate.color == "blue"
        assert mate.plan_mode_required is False
        assert mate.backend_type == "tmux"
        assert mate.is_active is True


# ── TeamConfig ──────────────────────────────────────────────


class TestTeamConfig:
    """TeamConfig dataclass 测试。"""

    def test_creation_with_required_fields(self) -> None:
        config = TeamConfig(
            name="test-team",
            description="A test team",
            created_at=1772193600000,
            lead_agent_id="team-lead@test-team",
            lead_session_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        )
        assert config.name == "test-team"
        assert config.lead_agent_id == "team-lead@test-team"
        assert config.members == []  # 默认空列表

    def test_lead_agent_id_format(self) -> None:
        """leadAgentId 格式必须为 team-lead@{team_name}。"""
        config = TeamConfig(
            name="my-team",
            description="",
            created_at=0,
            lead_agent_id="team-lead@my-team",
            lead_session_id="uuid-here",
        )
        assert config.lead_agent_id == f"team-lead@{config.name}"


# ── InboxMessage ────────────────────────────────────────────


class TestInboxMessage:
    """InboxMessage dataclass 测试。"""

    def test_required_fields_only(self) -> None:
        """仅 4 个必选字段时，summary/color 为 None。"""
        msg = InboxMessage(
            from_="team-lead",
            text="Hello",
            timestamp="2026-02-28T10:00:00.000Z",
        )
        assert msg.read is False  # 默认值
        assert msg.summary is None
        assert msg.color is None

    def test_all_fields(self) -> None:
        """含全部 6 个字段。"""
        msg = InboxMessage(
            from_="researcher",
            text="Done",
            timestamp="2026-02-28T10:01:00.000Z",
            read=True,
            summary="Task complete",
            color="blue",
        )
        assert msg.summary == "Task complete"
        assert msg.color == "blue"
        assert msg.read is True


# ── TaskFile ────────────────────────────────────────────────


class TestTaskFile:
    """TaskFile dataclass 测试。"""

    def test_new_task_defaults(self) -> None:
        """新建任务默认值：status=pending, owner=None, 空列表。"""
        task = TaskFile(id="1", subject="Do something", description="Details")
        assert task.status == "pending"
        assert task.active_form == ""
        assert task.owner is None
        assert task.blocks == []
        assert task.blocked_by == []
        assert task.metadata == {}

    def test_task_with_owner(self) -> None:
        task = TaskFile(
            id="2",
            subject="Implement",
            description="Details",
            status="in_progress",
            owner="researcher",
        )
        assert task.owner == "researcher"
        assert task.status == "in_progress"


# ── AGENT_COLORS ────────────────────────────────────────────


class TestAgentColors:
    """颜色常量测试。"""

    def test_has_8_colors(self) -> None:
        assert len(AGENT_COLORS) == 8

    def test_color_order(self) -> None:
        expected = ("blue", "green", "yellow", "purple", "orange", "pink", "cyan", "red")
        assert expected == AGENT_COLORS

    def test_cycling_formula(self) -> None:
        """验证 AGENT_COLORS[index % 8] 循环分配。"""
        assert AGENT_COLORS[0] == "blue"
        assert AGENT_COLORS[7] == "red"
        assert AGENT_COLORS[8 % 8] == "blue"  # 第 9 个循环回 blue
        assert AGENT_COLORS[3] == "purple"  # 第 4 个是 purple


# ── SpawnAgentOptions ───────────────────────────────────────


class TestSpawnAgentOptions:
    """SpawnAgentOptions dataclass 测试。"""

    def test_defaults(self) -> None:
        opts = SpawnAgentOptions(name="worker", prompt="Do work")
        assert opts.agent_type == "general-purpose"
        assert opts.model == "claude-sonnet-4-6"
        assert opts.plan_mode_required is False
        assert opts.permission_mode is None
        assert opts.allowed_tools is None
        assert opts.disallowed_tools is None


# ── 结构化消息 dataclass ────────────────────────────────────


class TestStructuredMessages:
    """9 种结构化消息 dataclass 基本创建测试。"""

    def test_task_assignment(self) -> None:
        msg = TaskAssignmentMessage(
            task_id="1",
            subject="Research",
            description="Do research",
            assigned_by="team-lead",
            timestamp="2026-02-28T10:00:00.000Z",
        )
        assert msg.assigned_by == "team-lead"  # 注意: assigned_by 非 from_

    def test_idle_notification_minimal(self) -> None:
        msg = IdleNotificationMessage(
            from_="worker",
            timestamp="2026-02-28T10:00:00.000Z",
        )
        assert msg.idle_reason is None
        assert msg.summary is None

    def test_idle_notification_p2p(self) -> None:
        """P2P 通信后的 idle 通知含 summary。"""
        msg = IdleNotificationMessage(
            from_="worker",
            timestamp="2026-02-28T10:00:00.000Z",
            idle_reason="available",
            summary="[to coder] Check the API",
        )
        assert msg.summary == "[to coder] Check the API"

    def test_shutdown_request(self) -> None:
        msg = ShutdownRequestMessage(
            request_id="shutdown-1772193660000@worker",
            from_="team-lead",
            reason="Task complete",
            timestamp="2026-02-28T10:00:00.000Z",
        )
        assert msg.request_id.startswith("shutdown-")

    def test_shutdown_approved(self) -> None:
        msg = ShutdownApprovedMessage(
            request_id="shutdown-1772193660000@worker",
            from_="worker",
            timestamp="2026-02-28T10:00:00.000Z",
            backend_id="%14",
            backend_type="tmux",
        )
        assert msg.backend_id == "%14"
        assert msg.backend_type == "tmux"

    def test_plan_approval_request(self) -> None:
        msg = PlanApprovalRequestMessage(
            from_="planner",
            timestamp="2026-02-28T10:00:00.000Z",
            plan_file_path="~/.claude/plans/test.md",
            plan_content="# Plan\n\n1. Step one",
            request_id="plan_approval-1772193720000@planner@test-team",
        )
        assert msg.request_id.startswith("plan_approval-")

    def test_plan_approval_response_approve(self) -> None:
        """approve=True 含 permission_mode，无 feedback。"""
        msg = PlanApprovalResponseMessage(
            request_id="plan_approval-123@p@t",
            approved=True,
            timestamp="2026-02-28T10:00:00.000Z",
            permission_mode="default",
        )
        assert msg.approved is True
        assert msg.permission_mode == "default"
        assert msg.feedback is None

    def test_plan_approval_response_reject(self) -> None:
        """approve=False 含 feedback，无 permission_mode。"""
        msg = PlanApprovalResponseMessage(
            request_id="plan_approval-123@p@t",
            approved=False,
            timestamp="2026-02-28T10:00:00.000Z",
            feedback="Add error handling",
        )
        assert msg.approved is False
        assert msg.feedback == "Add error handling"
        assert msg.permission_mode is None

    def test_permission_request(self) -> None:
        msg = PermissionRequestMessage(
            request_id="perm-1772193780000-abc1234",
            agent_id="delegate",
            tool_name="Bash",
            tool_use_id="toolu_test",
            description="Run command",
        )
        assert msg.request_id.startswith("perm-")
        assert msg.input == {}
        assert msg.permission_suggestions == []

    def test_permission_response_success(self) -> None:
        msg = PermissionResponseMessage(
            request_id="perm-123-abc",
            subtype="success",
            response={"updated_input": {}, "permission_updates": []},
        )
        assert msg.subtype == "success"
        assert msg.error is None

    def test_permission_response_error(self) -> None:
        msg = PermissionResponseMessage(
            request_id="perm-123-abc",
            subtype="error",
            error="Operation not permitted",
        )
        assert msg.subtype == "error"
        assert msg.response is None


# ── AgentController Protocol ────────────────────────────────


class TestAgentControllerProtocol:
    """Protocol 接口可运行时检查。"""

    def test_runtime_checkable(self) -> None:
        """AgentController 应标记为 @runtime_checkable。"""

        class FakeController:
            async def send_message(
                self, recipient: str, content: str, *, summary: str | None = None
            ) -> None: ...
            async def send_shutdown_request(self, agent_name: str, reason: str) -> str:
                return ""
            async def kill_agent(self, agent_name: str) -> None: ...
            def is_agent_running(self, agent_name: str) -> bool:
                return False

        assert isinstance(FakeController(), AgentController)
