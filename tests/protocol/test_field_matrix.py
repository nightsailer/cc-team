"""协议兼容性测试 — 字段存在性矩阵验证。

验证各种消息和配置对象中可选字段的有无与协议规范一致。
"""

from __future__ import annotations

from cc_team._serialization import (
    inbox_message_to_dict,
    task_file_to_dict,
    to_json_dict,
)
from cc_team.types import (
    InboxMessage,
    PlanApprovalResponseMessage,
    TaskFile,
    TeamMember,
)


class TestLeadMemberFieldMatrix:
    """Lead 成员字段存在性。"""

    def _make_lead(self) -> TeamMember:
        return TeamMember(
            agent_id="team-lead@t",
            name="team-lead",
            agent_type="team-lead",
            model="m",
            joined_at=1000,
            tmux_pane_id="",
            cwd="/",
        )

    def test_lead_no_color(self) -> None:
        d = to_json_dict(self._make_lead())
        assert "color" not in d

    def test_lead_no_prompt(self) -> None:
        d = to_json_dict(self._make_lead())
        assert "prompt" not in d

    def test_lead_no_plan_mode(self) -> None:
        d = to_json_dict(self._make_lead())
        assert "planModeRequired" not in d

    def test_lead_no_backend_type(self) -> None:
        d = to_json_dict(self._make_lead())
        assert "backendType" not in d

    def test_lead_no_is_active(self) -> None:
        d = to_json_dict(self._make_lead())
        assert "isActive" not in d

    def test_lead_tmux_pane_id_empty_string(self) -> None:
        """Lead tmuxPaneId 为空字符串，不是 null/缺失。"""
        d = to_json_dict(self._make_lead())
        assert d["tmuxPaneId"] == ""


class TestTeammateMemberFieldMatrix:
    """Teammate 成员含全部 13 个字段。"""

    def test_teammate_has_all_13_fields(self) -> None:
        mate = TeamMember(
            agent_id="r@t", name="r", agent_type="general-purpose",
            model="m", joined_at=1000, tmux_pane_id="%14", cwd="/",
            prompt="Work", color="blue",
            plan_mode_required=False, backend_type="tmux", is_active=True,
        )
        d = to_json_dict(mate)
        expected_keys = {
            "agentId", "name", "agentType", "model", "joinedAt",
            "tmuxPaneId", "cwd", "subscriptions",
            "prompt", "color", "planModeRequired", "backendType", "isActive",
        }
        assert expected_keys == set(d.keys())


class TestTaskFieldMatrix:
    """Task 文件字段存在性。"""

    def test_new_task_no_owner(self) -> None:
        """新建 task 无 owner（协议中 undefined，不是 null）。"""
        task = TaskFile(id="1", subject="S", description="D")
        d = task_file_to_dict(task)
        assert "owner" not in d

    def test_new_task_has_empty_metadata(self) -> None:
        """metadata 为空 dict 时仍输出。"""
        task = TaskFile(id="1", subject="S", description="D")
        d = task_file_to_dict(task)
        assert "metadata" in d
        assert d["metadata"] == {}

    def test_assigned_task_has_owner(self) -> None:
        task = TaskFile(id="1", subject="S", description="D", owner="worker")
        d = task_file_to_dict(task)
        assert "owner" in d
        assert d["owner"] == "worker"


class TestInboxFieldMatrix:
    """Inbox 消息字段存在性矩阵。"""

    def test_initial_prompt_no_summary_no_color(self) -> None:
        """初始 prompt: 无 summary，无 color。"""
        msg = InboxMessage(
            from_="team-lead",
            text="You are a researcher.",
            timestamp="2026-02-28T10:00:00.000Z",
        )
        d = inbox_message_to_dict(msg)
        assert "summary" not in d
        assert "color" not in d

    def test_lead_to_agent_has_summary_no_color(self) -> None:
        """Lead→Agent: 有 summary，无 color（Lead 无颜色）。"""
        msg = InboxMessage(
            from_="team-lead",
            text="Please check",
            timestamp="2026-02-28T10:00:00.000Z",
            summary="Follow up",
        )
        d = inbox_message_to_dict(msg)
        assert "summary" in d
        assert "color" not in d

    def test_agent_to_lead_has_summary_and_color(self) -> None:
        """Agent→Lead: 有 summary + color。"""
        msg = InboxMessage(
            from_="researcher",
            text="Done",
            timestamp="2026-02-28T10:01:00.000Z",
            summary="Task complete",
            color="blue",
        )
        d = inbox_message_to_dict(msg)
        assert d["summary"] == "Task complete"
        assert d["color"] == "blue"


class TestPlanApprovalFieldAsymmetry:
    """plan_approval_response 字段非对称性。"""

    def test_approve_has_permission_mode_no_feedback(self) -> None:
        msg = PlanApprovalResponseMessage(
            request_id="plan_approval-123@p@t",
            approved=True,
            timestamp="t",
            permission_mode="default",
        )
        d = to_json_dict(msg)
        assert "permissionMode" in d
        assert "feedback" not in d

    def test_reject_has_feedback_no_permission_mode(self) -> None:
        msg = PlanApprovalResponseMessage(
            request_id="plan_approval-123@p@t",
            approved=False,
            timestamp="t",
            feedback="Please revise",
        )
        d = to_json_dict(msg)
        assert "feedback" in d
        assert "permissionMode" not in d


class TestPermissionRequestHasColor:
    """permission_request 外层 inbox 有 color（与 plan_approval_request 不同）。"""

    def test_permission_inbox_with_color(self) -> None:
        """permission_request 对应的 InboxMessage 含 color。"""
        inbox_msg = InboxMessage(
            from_="delegate-agent",
            text='{"type":"permission_request","request_id":"perm-1-abc"}',
            timestamp="t",
            color="green",  # permission_request 有 color
        )
        d = inbox_message_to_dict(inbox_msg)
        assert "color" in d

    def test_plan_approval_inbox_without_color(self) -> None:
        """plan_approval_request 对应的 InboxMessage 无 color（异常行为，需兼容）。"""
        inbox_msg = InboxMessage(
            from_="planner",
            text='{"type":"plan_approval_request","requestId":"plan_approval-1@p@t"}',
            timestamp="t",
            # 无 color
        )
        d = inbox_message_to_dict(inbox_msg)
        assert "color" not in d
