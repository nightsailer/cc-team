"""_serialization.py 单元测试 — 序列化/反序列化 + 原子写入。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_team._serialization import (
    atomic_write_json,
    build_message_body,
    from_json_dict,
    inbox_message_from_dict,
    inbox_message_to_dict,
    now_iso,
    now_ms,
    parse_message_body,
    read_json,
    task_file_from_dict,
    task_file_to_dict,
    team_config_from_dict,
    team_config_to_dict,
    to_json_dict,
)
from cc_team.types import (
    InboxMessage,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PlanApprovalResponseMessage,
    SessionRelayMessage,
    ShutdownRequestMessage,
    TaskFile,
    TeamConfig,
    TeamMember,
)

# ── snake_case ↔ camelCase 映射 ─────────────────────────────


class TestKeyMapping:
    """字段名映射测试。"""

    def test_snake_to_camel_basic(self) -> None:
        """agent_id → agentId。"""
        member = TeamMember(
            agent_id="a@t",
            name="a",
            agent_type="general-purpose",
            model="m",
            joined_at=0,
            tmux_pane_id="",
            cwd="/",
        )
        d = to_json_dict(member)
        assert "agentId" in d
        assert "agent_id" not in d

    def test_from_preserves_as_from_(self) -> None:
        """from_ → from (Python 保留字处理)。"""
        msg = InboxMessage(from_="lead", text="hi", timestamp="t")
        d = inbox_message_to_dict(msg)
        assert "from" in d
        assert "from_" not in d

    def test_permission_fields_stay_snake_case(self) -> None:
        """permission 系列 request_id 保持 snake_case。"""
        msg = PermissionRequestMessage(
            request_id="perm-123-abc",
            agent_id="delegate",
            tool_name="Bash",
            tool_use_id="toolu_1",
            description="Run cmd",
        )
        d = to_json_dict(msg)
        assert "request_id" in d  # snake_case 保持
        assert "requestId" not in d
        assert "agent_id" in d
        assert "tool_name" in d
        assert "tool_use_id" in d
        assert "permission_suggestions" in d

    def test_shutdown_request_id_is_camel_case(self) -> None:
        """shutdown 系列 request_id → requestId (camelCase)。"""
        msg = ShutdownRequestMessage(
            request_id="shutdown-123@worker",
            from_="lead",
            reason="done",
            timestamp="t",
        )
        d = to_json_dict(msg)
        assert "requestId" in d
        assert "request_id" not in d


# ── to_json_dict / from_json_dict ───────────────────────────


class TestJsonDictConversion:
    """dataclass ↔ JSON dict 转换。"""

    def test_none_fields_excluded(self) -> None:
        """值为 None 的可选字段不出现在 JSON dict 中。"""
        task = TaskFile(id="1", subject="S", description="D")
        d = to_json_dict(task)
        assert "owner" not in d  # owner=None → 不输出

    def test_empty_list_included(self) -> None:
        """空列表仍然输出。"""
        task = TaskFile(id="1", subject="S", description="D")
        d = to_json_dict(task)
        assert "blocks" in d
        assert d["blocks"] == []

    def test_empty_dict_included(self) -> None:
        """Empty dict (metadata) still present in raw to_json_dict output."""
        task = TaskFile(id="1", subject="S", description="D")
        d = to_json_dict(task)
        assert "metadata" in d
        assert d["metadata"] == {}

    def test_from_json_dict_missing_optional(self) -> None:
        """JSON 中缺失可选字段时使用默认值。"""
        data = {"id": "1", "subject": "S", "description": "D"}
        task = from_json_dict(TaskFile, data)
        assert task.owner is None
        assert task.status == "pending"

    def test_from_json_dict_camel_case_input(self) -> None:
        """从 camelCase JSON 正确映射到 snake_case 字段。"""
        data = {
            "agentId": "a@t",
            "name": "a",
            "agentType": "general-purpose",
            "model": "m",
            "joinedAt": 1000,
            "tmuxPaneId": "%14",
            "cwd": "/",
            "subscriptions": [],
            "isActive": True,
            "backendType": "tmux",
            "color": "blue",
            "planModeRequired": False,
            "prompt": "Do work",
        }
        member = from_json_dict(TeamMember, data)
        assert member.agent_id == "a@t"
        assert member.is_active is True
        assert member.backend_type == "tmux"

    def test_unknown_fields_ignored(self) -> None:
        """JSON 中未知字段被安全忽略。"""
        data = {"id": "1", "subject": "S", "description": "D", "unknownField": 42}
        task = from_json_dict(TaskFile, data)
        assert task.id == "1"

    def test_nested_members_roundtrip(self) -> None:
        """TeamConfig nested TeamMember list roundtrip conversion."""
        config = TeamConfig(
            name="t",
            description="d",
            created_at=1000,
            lead_agent_id="team-lead@t",
            lead_session_id="uuid",
            members=[
                TeamMember(
                    agent_id="team-lead@t",
                    name="team-lead",
                    agent_type="team-lead",
                    model="m",
                    joined_at=1000,
                    tmux_pane_id="",
                    cwd="/",
                ),
            ],
        )
        d = team_config_to_dict(config)
        restored = team_config_from_dict(d)
        assert len(restored.members) == 1
        assert restored.members[0].agent_id == "team-lead@t"

    def test_nested_members_camel_case_keys(self) -> None:
        """P0-a regression: nested members must use camelCase keys."""
        config = TeamConfig(
            name="t",
            description="d",
            created_at=1000,
            lead_agent_id="team-lead@t",
            lead_session_id="uuid",
            members=[
                TeamMember(
                    agent_id="worker@t",
                    name="worker",
                    agent_type="general-purpose",
                    model="sonnet",
                    joined_at=2000,
                    tmux_pane_id="%5",
                    cwd="/home",
                    is_active=True,
                    backend_type="tmux",
                ),
            ],
        )
        d = team_config_to_dict(config)
        member_dict = d["members"][0]
        # Must be camelCase, not snake_case
        assert "agentId" in member_dict
        assert "agent_id" not in member_dict
        assert "agentType" in member_dict
        assert "agent_type" not in member_dict
        assert "joinedAt" in member_dict
        assert "joined_at" not in member_dict
        assert "tmuxPaneId" in member_dict
        assert "tmux_pane_id" not in member_dict
        assert "isActive" in member_dict
        assert "is_active" not in member_dict
        assert "backendType" in member_dict
        assert "backend_type" not in member_dict

    def test_team_lead_member_no_null_fields(self) -> None:
        """P1 regression: team-lead member must NOT contain teammate-only fields."""
        config = TeamConfig(
            name="t",
            description="d",
            created_at=1000,
            lead_agent_id="team-lead@t",
            lead_session_id="uuid",
            members=[
                TeamMember(
                    agent_id="team-lead@t",
                    name="team-lead",
                    agent_type="team-lead",
                    model="m",
                    joined_at=1000,
                    tmux_pane_id="",
                    cwd="/",
                    # prompt, color, plan_mode_required, backend_type, is_active
                    # are all None by default
                ),
            ],
        )
        d = team_config_to_dict(config)
        member_dict = d["members"][0]
        # None fields must be omitted entirely, not serialized as null
        for key in ("prompt", "color", "planModeRequired", "backendType", "isActive"):
            assert key not in member_dict, f"Unexpected key '{key}' in team-lead member"


# ── InboxMessage 特殊序列化 ─────────────────────────────────


class TestInboxMessageSerialization:
    """InboxMessage 的 from_ → from 特殊处理。"""

    def test_to_dict_minimal(self) -> None:
        msg = InboxMessage(from_="lead", text="hi", timestamp="t")
        d = inbox_message_to_dict(msg)
        assert d == {"from": "lead", "text": "hi", "timestamp": "t", "read": False}
        assert "summary" not in d
        assert "color" not in d

    def test_to_dict_with_optional(self) -> None:
        msg = InboxMessage(
            from_="worker",
            text="done",
            timestamp="t",
            summary="Task done",
            color="blue",
        )
        d = inbox_message_to_dict(msg)
        assert d["summary"] == "Task done"
        assert d["color"] == "blue"

    def test_from_dict(self) -> None:
        data = {"from": "lead", "text": "hi", "timestamp": "t", "read": True}
        msg = inbox_message_from_dict(data)
        assert msg.from_ == "lead"
        assert msg.read is True
        assert msg.summary is None

    def test_from_dict_with_optional(self) -> None:
        data = {
            "from": "w",
            "text": "x",
            "timestamp": "t",
            "read": False,
            "summary": "s",
            "color": "green",
        }
        msg = inbox_message_from_dict(data)
        assert msg.summary == "s"
        assert msg.color == "green"


# ── 结构化消息解析 ──────────────────────────────────────────


class TestParseMessageBody:
    """parse_message_body 从 text 解析结构化消息。"""

    def test_plain_text_returns_none(self) -> None:
        assert parse_message_body("Hello world") is None

    def test_json_without_type_returns_none(self) -> None:
        assert parse_message_body('{"key": "value"}') is None

    def test_unknown_type_returns_none(self) -> None:
        assert parse_message_body('{"type": "unknown_type"}') is None

    def test_parse_shutdown_request(self) -> None:
        text = json.dumps(
            {
                "type": "shutdown_request",
                "requestId": "shutdown-123@w",
                "from": "lead",
                "reason": "done",
                "timestamp": "t",
            }
        )
        result = parse_message_body(text)
        assert result is not None
        msg_type, msg = result
        assert msg_type == "shutdown_request"
        assert isinstance(msg, ShutdownRequestMessage)
        assert msg.request_id == "shutdown-123@w"

    def test_parse_permission_request(self) -> None:
        """permission 系列的 snake_case 字段正确解析。"""
        text = json.dumps(
            {
                "type": "permission_request",
                "request_id": "perm-123-abc",
                "agent_id": "delegate",
                "tool_name": "Bash",
                "tool_use_id": "toolu_1",
                "description": "Run cmd",
                "input": {"command": "ls"},
                "permission_suggestions": [],
            }
        )
        result = parse_message_body(text)
        assert result is not None
        msg_type, msg = result
        assert msg_type == "permission_request"
        assert isinstance(msg, PermissionRequestMessage)
        assert msg.request_id == "perm-123-abc"


class TestBuildMessageBody:
    """build_message_body 构建 JSON 字符串。"""

    def test_type_field_first(self) -> None:
        """type 字段在 JSON 输出中排在最前。"""
        msg = ShutdownRequestMessage(
            request_id="shutdown-1@w",
            from_="lead",
            reason="done",
            timestamp="t",
        )
        text = build_message_body("shutdown_request", msg)
        parsed = json.loads(text)
        keys = list(parsed.keys())
        assert keys[0] == "type"
        assert parsed["type"] == "shutdown_request"

    def test_permission_keeps_snake_case(self) -> None:
        """permission 消息中 request_id 保持 snake_case。"""
        msg = PermissionResponseMessage(
            request_id="perm-1-abc",
            subtype="success",
            response={"updated_input": {}, "permission_updates": []},
        )
        text = build_message_body("permission_response", msg)
        parsed = json.loads(text)
        assert "request_id" in parsed
        assert "requestId" not in parsed


# ── 原子写入 ────────────────────────────────────────────────


class TestAtomicWriteJson:
    """atomic_write_json 原子写入测试。"""

    def test_write_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "test.json"
        atomic_write_json(target, {"key": "value"})
        assert target.exists()
        data = json.loads(target.read_text())
        assert data == {"key": "value"}

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        """父目录不存在时自动创建。"""
        target = tmp_path / "deep" / "nested" / "file.json"
        atomic_write_json(target, [1, 2, 3])
        assert target.exists()

    def test_write_compact_format(self, tmp_path: Path) -> None:
        """JSON 使用紧凑格式 (无空格)。"""
        target = tmp_path / "compact.json"
        atomic_write_json(target, {"a": 1, "b": 2})
        content = target.read_text()
        assert " " not in content  # 紧凑格式无空格

    def test_write_no_temp_files_remain(self, tmp_path: Path) -> None:
        """No temp files remain after successful write."""
        target = tmp_path / "clean.json"
        atomic_write_json(target, {"ok": True})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_write_permissions_644(self, tmp_path: Path) -> None:
        """P2 regression: created files must have 0o644 permissions."""
        target = tmp_path / "perms.json"
        atomic_write_json(target, {"key": "value"})
        mode = target.stat().st_mode & 0o777
        assert mode == 0o644, f"Expected 0o644, got {oct(mode)}"


# ── 带重试读取 ──────────────────────────────────────────────


class TestReadJson:
    """read_json 带重试的 JSON 读取。"""

    def test_read_valid_file(self, tmp_path: Path) -> None:
        target = tmp_path / "valid.json"
        target.write_text('{"key": "value"}')
        result = read_json(target)
        assert result == {"key": "value"}

    def test_read_nonexistent_returns_default(self, tmp_path: Path) -> None:
        target = tmp_path / "missing.json"
        assert read_json(target) is None
        assert read_json(target, default=[]) == []

    def test_read_empty_file_retries_then_fails(self, tmp_path: Path) -> None:
        """空文件重试后仍失败抛出 JSONDecodeError。"""
        target = tmp_path / "empty.json"
        target.write_text("")
        with pytest.raises(json.JSONDecodeError):
            read_json(target)


# ── 时间戳工厂 ──────────────────────────────────────────────


class TestTimestampFactory:
    """时间戳工厂函数格式验证。"""

    def test_now_iso_format(self) -> None:
        """ISO 8601 格式，以 Z 结尾，毫秒精度。"""
        result = now_iso()
        assert result.endswith("Z")
        assert "T" in result
        # 毫秒精度: .XXX 格式
        parts = result.split(".")
        assert len(parts) == 2
        ms_part = parts[1].rstrip("Z")
        assert len(ms_part) == 3

    def test_now_ms_is_integer(self) -> None:
        result = now_ms()
        assert isinstance(result, int)
        assert result > 1_700_000_000_000  # 2023 年之后的毫秒级时间戳


# ── TaskFile 序列化 ─────────────────────────────────────────


class TestTaskFileSerialization:
    """TaskFile 序列化特殊处理。"""

    def test_new_task_no_owner(self) -> None:
        """新建 task 无 owner 字段（协议中 undefined）。"""
        task = TaskFile(id="1", subject="S", description="D")
        d = task_file_to_dict(task)
        assert "owner" not in d

    def test_assigned_task_has_owner(self) -> None:
        task = TaskFile(id="1", subject="S", description="D", owner="worker")
        d = task_file_to_dict(task)
        assert d["owner"] == "worker"

    def test_camel_case_keys(self) -> None:
        task = TaskFile(
            id="1",
            subject="S",
            description="D",
            active_form="Working",
            blocked_by=["2"],
        )
        d = task_file_to_dict(task)
        assert "activeForm" in d
        assert "blockedBy" in d
        assert "active_form" not in d

    def test_empty_active_form_omitted(self) -> None:
        """P3 regression: empty activeForm must be omitted from task dict."""
        task = TaskFile(id="1", subject="S", description="D")
        d = task_file_to_dict(task)
        assert "activeForm" not in d

    def test_empty_metadata_omitted(self) -> None:
        """P3 regression: empty metadata must be omitted from task dict."""
        task = TaskFile(id="1", subject="S", description="D")
        d = task_file_to_dict(task)
        assert "metadata" not in d

    def test_non_empty_active_form_kept(self) -> None:
        """Non-empty activeForm is preserved."""
        task = TaskFile(id="1", subject="S", description="D", active_form="Working")
        d = task_file_to_dict(task)
        assert d["activeForm"] == "Working"

    def test_non_empty_metadata_kept(self) -> None:
        """Non-empty metadata is preserved."""
        task = TaskFile(id="1", subject="S", description="D", metadata={"k": "v"})
        d = task_file_to_dict(task)
        assert d["metadata"] == {"k": "v"}

    def test_roundtrip(self) -> None:
        original = TaskFile(
            id="3",
            subject="Test",
            description="Desc",
            status="in_progress",
            active_form="Testing",
            owner="tester",
            blocks=["4"],
            blocked_by=["1", "2"],
            metadata={"priority": "high"},
        )
        d = task_file_to_dict(original)
        restored = task_file_from_dict(d)
        assert restored.id == original.id
        assert restored.status == original.status
        assert restored.owner == original.owner
        assert restored.blocks == original.blocks
        assert restored.blocked_by == original.blocked_by
        assert restored.metadata == original.metadata


# ── 原子写入异常清理 [P2] ──────────────────────────────────


class TestAtomicWriteExceptionCleanup:
    """atomic_write_json 异常时清理临时文件。"""

    def test_exception_cleans_temp_file(self, tmp_path: Path) -> None:
        """json.dump 抛出异常时，临时文件应被清理。"""
        target = tmp_path / "fail.json"

        # 传入不可序列化的对象来触发异常
        class Unserializable:
            pass

        with pytest.raises(TypeError):
            atomic_write_json(target, {"bad": Unserializable()})

        # 目标文件不应被创建
        assert not target.exists()
        # 临时文件不应残留
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_exception_does_not_corrupt_existing(self, tmp_path: Path) -> None:
        """写入失败时不应损坏已有文件。"""
        target = tmp_path / "existing.json"
        atomic_write_json(target, {"original": True})

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            atomic_write_json(target, {"bad": Unserializable()})

        # 原始文件应完好无损
        data = json.loads(target.read_text())
        assert data == {"original": True}


# ── read_json 重试行为 [P3] ──────────────────────────────────


class TestReadJsonRetryBehavior:
    """read_json 损坏 JSON 重试后失败。"""

    def test_corrupted_json_retries_then_fails(self, tmp_path: Path) -> None:
        """真正损坏的 JSON 经过 3 次重试后抛出 JSONDecodeError。"""
        target = tmp_path / "corrupted.json"
        target.write_text('{"key": invalid_value}')
        with pytest.raises(json.JSONDecodeError):
            read_json(target)

    def test_read_json_retries_on_transient_corruption(self, tmp_path: Path) -> None:
        """模拟暂态损坏：第一次失败，后续成功。"""
        target = tmp_path / "transient.json"
        # 先写入有效 JSON
        target.write_text('{"ok": true}')

        call_count = 0
        original_read_text = Path.read_text

        def patched_read_text(self_path: Path, encoding: str = "utf-8", **kw: str) -> str:
            nonlocal call_count
            if str(self_path) == str(target):
                call_count += 1
                if call_count == 1:
                    return "{broken"  # 第一次返回损坏
            return original_read_text(self_path, encoding=encoding, **kw)

        import unittest.mock

        with unittest.mock.patch.object(Path, "read_text", patched_read_text):
            result = read_json(target)

        assert result == {"ok": True}
        assert call_count >= 1


# ── build_message_body 其他消息类型 [P3] ─────────────────────


class TestBuildMessageBodyExtended:
    """build_message_body 对更多消息类型的验证。"""

    def test_plan_approval_response_approve(self) -> None:
        """plan_approval_response 审批通过的序列化。"""
        msg = PlanApprovalResponseMessage(
            request_id="plan-1",
            approved=True,
            timestamp="2026-02-28T10:00:00.000Z",
            permission_mode="default",
        )
        text = build_message_body("plan_approval_response", msg)
        parsed = json.loads(text)
        assert parsed["type"] == "plan_approval_response"
        assert parsed["requestId"] == "plan-1"
        assert parsed["approved"] is True

    def test_plan_approval_response_reject_with_feedback(self) -> None:
        """plan_approval_response 拒绝并携带 feedback。"""
        msg = PlanApprovalResponseMessage(
            request_id="plan-2",
            approved=False,
            timestamp="2026-02-28T10:00:00.000Z",
            feedback="Need more detail",
        )
        text = build_message_body("plan_approval_response", msg)
        parsed = json.loads(text)
        assert parsed["approved"] is False
        assert parsed["feedback"] == "Need more detail"

    def test_session_relay_roundtrip(self) -> None:
        """session_relay 序列化/反序列化 roundtrip。"""
        msg = SessionRelayMessage(
            from_="team-lead",
            new_session_id="new-uuid-123",
            previous_session_id="old-uuid-456",
            timestamp="2026-03-02T10:00:00.000Z",
        )
        text = build_message_body("session_relay", msg)
        parsed_json = json.loads(text)
        # 检查 camelCase 序列化
        assert parsed_json["type"] == "session_relay"
        assert parsed_json["newSessionId"] == "new-uuid-123"
        assert parsed_json["previousSessionId"] == "old-uuid-456"
        assert parsed_json["from"] == "team-lead"
        # 反序列化
        result = parse_message_body(text)
        assert result is not None
        msg_type, restored = result
        assert msg_type == "session_relay"
        assert isinstance(restored, SessionRelayMessage)
        assert restored.new_session_id == "new-uuid-123"
        assert restored.previous_session_id == "old-uuid-456"
        assert restored.from_ == "team-lead"
