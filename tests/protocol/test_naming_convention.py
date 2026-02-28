"""协议兼容性测试 — camelCase/snake_case 命名约定验证。

协议中存在历史不一致：
- shutdown / plan 系列: camelCase (requestId)
- permission 系列: snake_case (request_id)

两种格式必须同时正确处理。
"""

from __future__ import annotations

import json

from cc_team._serialization import (
    build_message_body,
    parse_message_body,
    to_json_dict,
)
from cc_team.types import (
    PermissionRequestMessage,
    PermissionResponseMessage,
    PlanApprovalRequestMessage,
    PlanApprovalResponseMessage,
    ShutdownApprovedMessage,
    ShutdownRequestMessage,
)


class TestCamelCaseOutputs:
    """shutdown / plan 系列输出 camelCase。"""

    def test_shutdown_request_uses_requestId(self) -> None:
        msg = ShutdownRequestMessage(
            request_id="shutdown-123@w", from_="lead",
            reason="done", timestamp="t",
        )
        d = to_json_dict(msg)
        assert "requestId" in d
        assert "request_id" not in d

    def test_shutdown_approved_uses_requestId(self) -> None:
        msg = ShutdownApprovedMessage(
            request_id="shutdown-123@w", from_="w",
            timestamp="t", pane_id="%14", backend_type="tmux",
        )
        d = to_json_dict(msg)
        assert "requestId" in d
        assert "paneId" in d
        assert "backendType" in d

    def test_plan_approval_request_uses_requestId(self) -> None:
        msg = PlanApprovalRequestMessage(
            from_="p", timestamp="t",
            plan_file_path="path", plan_content="content",
            request_id="plan_approval-123@p@t",
        )
        d = to_json_dict(msg)
        assert "requestId" in d
        assert "planFilePath" in d
        assert "planContent" in d

    def test_plan_approval_response_uses_requestId(self) -> None:
        msg = PlanApprovalResponseMessage(
            request_id="plan_approval-123@p@t",
            approved=True, timestamp="t",
            permission_mode="default",
        )
        d = to_json_dict(msg)
        assert "requestId" in d
        assert "permissionMode" in d


class TestSnakeCaseOutputs:
    """permission 系列输出 snake_case。"""

    def test_permission_request_uses_request_id(self) -> None:
        msg = PermissionRequestMessage(
            request_id="perm-123-abc",
            agent_id="delegate",
            tool_name="Bash",
            tool_use_id="toolu_1",
            description="Run cmd",
        )
        d = to_json_dict(msg)
        assert "request_id" in d
        assert "requestId" not in d
        assert "agent_id" in d
        assert "agentId" not in d
        assert "tool_name" in d
        assert "tool_use_id" in d
        assert "permission_suggestions" in d

    def test_permission_response_uses_request_id(self) -> None:
        msg = PermissionResponseMessage(
            request_id="perm-123-abc",
            subtype="success",
            response={"updated_input": {}, "permission_updates": []},
        )
        d = to_json_dict(msg)
        assert "request_id" in d
        assert "requestId" not in d


class TestCamelCaseParsing:
    """从 camelCase JSON 解析 shutdown/plan 消息。"""

    def test_parse_camel_case_shutdown(self) -> None:
        text = json.dumps({
            "type": "shutdown_request",
            "requestId": "shutdown-999@agent",
            "from": "lead",
            "reason": "done",
            "timestamp": "t",
        })
        result = parse_message_body(text)
        assert result is not None
        _, msg = result
        assert msg.request_id == "shutdown-999@agent"
        assert msg.from_ == "lead"

    def test_parse_camel_case_plan_response(self) -> None:
        text = json.dumps({
            "type": "plan_approval_response",
            "requestId": "plan_approval-1@p@t",
            "approved": True,
            "timestamp": "t",
            "permissionMode": "acceptEdits",
        })
        result = parse_message_body(text)
        assert result is not None
        _, msg = result
        assert msg.permission_mode == "acceptEdits"


class TestSnakeCaseParsing:
    """从 snake_case JSON 解析 permission 消息。"""

    def test_parse_snake_case_permission_request(self) -> None:
        text = json.dumps({
            "type": "permission_request",
            "request_id": "perm-999-xyz",
            "agent_id": "delegate",
            "tool_name": "Write",
            "tool_use_id": "toolu_abc",
            "description": "Write file",
            "input": {"file_path": "/tmp/test.txt"},
            "permission_suggestions": [{"type": "addDirectories", "directories": ["/tmp"]}],
        })
        result = parse_message_body(text)
        assert result is not None
        _, msg = result
        assert isinstance(msg, PermissionRequestMessage)
        assert msg.request_id == "perm-999-xyz"
        assert msg.agent_id == "delegate"
        assert msg.tool_name == "Write"

    def test_parse_snake_case_permission_response(self) -> None:
        text = json.dumps({
            "type": "permission_response",
            "request_id": "perm-999-xyz",
            "subtype": "error",
            "error": "Denied by user",
        })
        result = parse_message_body(text)
        assert result is not None
        _, msg = result
        assert isinstance(msg, PermissionResponseMessage)
        assert msg.request_id == "perm-999-xyz"
        assert msg.error == "Denied by user"


class TestBuildPreservesConvention:
    """build_message_body 输出保持正确的命名约定。"""

    def test_build_shutdown_camel_case(self) -> None:
        msg = ShutdownRequestMessage(
            request_id="shutdown-1@w", from_="lead",
            reason="done", timestamp="t",
        )
        text = build_message_body("shutdown_request", msg)
        parsed = json.loads(text)
        assert "requestId" in parsed
        assert "request_id" not in parsed

    def test_build_permission_snake_case(self) -> None:
        msg = PermissionRequestMessage(
            request_id="perm-1-abc", agent_id="d",
            tool_name="Bash", tool_use_id="t",
            description="cmd",
        )
        text = build_message_body("permission_request", msg)
        parsed = json.loads(text)
        assert "request_id" in parsed
        assert "requestId" not in parsed
        assert "agent_id" in parsed
        assert "tool_name" in parsed
