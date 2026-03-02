"""JSON 序列化与反序列化工具。

职责：
1. snake_case ↔ camelCase 字段名映射
2. dataclass ↔ JSON dict 转换
3. 原子写入（temp + fsync + rename）
4. 带重试的 JSON 读取（处理并发写入期间的空文件/损坏 JSON）
5. 时间戳工厂函数（可 monkeypatch 替换，确保可测试性）

命名约定差异：
- 大多数字段: snake_case → camelCase (如 agent_id → agentId)
- permission 系列消息: 保持 snake_case (如 request_id → request_id)
- 特殊: from_ → from (Python 保留字规避)
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from cc_team.types import (
    IdleNotificationMessage,
    InboxMessage,
    PermissionRequestMessage,
    PermissionResponseMessage,
    PlanApprovalRequestMessage,
    PlanApprovalResponseMessage,
    SessionRelayMessage,
    ShutdownApprovedMessage,
    ShutdownRequestMessage,
    TaskAssignmentMessage,
    TaskFile,
    TeamConfig,
    TeamMember,
)

T = TypeVar("T")

# ── 时间戳工厂（可测试性注入点）──────────────────────────────


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。

    测试时可通过 monkeypatch 替换为固定值。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def now_ms() -> int:
    """返回当前 Unix 毫秒时间戳。

    测试时可通过 monkeypatch 替换为固定值。
    """
    return int(time.time() * 1000)


# ── 字段映射表 ──────────────────────────────────────────────
#
# Python snake_case → JSON key 的映射。
# 未列出的字段默认使用自动转换规则（见 _snake_to_camel）。

_PYTHON_TO_JSON: dict[str, str] = {
    # 特殊: Python 保留字
    "from_": "from",
    # TeamMember / TeamConfig
    "agent_id": "agentId",
    "agent_type": "agentType",
    "joined_at": "joinedAt",
    "tmux_pane_id": "tmuxPaneId",
    "plan_mode_required": "planModeRequired",
    "backend_type": "backendType",
    "is_active": "isActive",
    "lead_agent_id": "leadAgentId",
    "lead_session_id": "leadSessionId",
    "created_at": "createdAt",
    # TaskFile
    "active_form": "activeForm",
    "blocked_by": "blockedBy",
    # 结构化消息 (camelCase 族)
    "task_id": "taskId",
    "assigned_by": "assignedBy",
    "idle_reason": "idleReason",
    "request_id": "requestId",  # 默认: camelCase
    "pane_id": "paneId",
    "plan_file_path": "planFilePath",
    "plan_content": "planContent",
    "permission_mode": "permissionMode",
    # SessionRelayMessage
    "new_session_id": "newSessionId",
    "previous_session_id": "previousSessionId",
    # 结构化消息 (permission snake_case 族)
    # 这些类型中 request_id 等字段保持 snake_case
    "tool_name": "tool_name",
    "tool_use_id": "tool_use_id",
    "permission_suggestions": "permission_suggestions",
}

# 反向映射: JSON key → Python field
_JSON_TO_PYTHON: dict[str, str] = {v: k for k, v in _PYTHON_TO_JSON.items()}
# 补充: permission 系列的 snake_case 字段也需要映射
_JSON_TO_PYTHON.update({
    "from": "from_",
    # agent_id 在 permission 消息中保持 snake_case
    "agent_id": "agent_id",
    "request_id": "request_id",  # snake_case 版本
})

# Permission 系列类型集合 — 这些类型中特定字段保持 snake_case
_PERMISSION_TYPES: set[type] = {
    PermissionRequestMessage,
    PermissionResponseMessage,
}

# Permission 系列中保持 snake_case 的字段
_PERMISSION_SNAKE_FIELDS: set[str] = {
    "request_id", "agent_id", "tool_name", "tool_use_id",
    "permission_suggestions",
}


# ── dataclass → JSON dict ──────────────────────────────────


def to_json_dict(obj: Any, *, cls: type | None = None) -> dict[str, Any]:
    """Convert a dataclass instance to a JSON-compatible dict (camelCase keys).

    Uses manual fields() iteration instead of asdict() to preserve nested
    dataclass instances for correct recursive camelCase key mapping.

    Args:
        obj: dataclass instance
        cls: optional type override for naming-rule detection

    Returns:
        JSON-compatible dict
    """
    actual_cls = cls or type(obj)
    is_permission = actual_cls in _PERMISSION_TYPES
    result: dict[str, Any] = {}

    for f in fields(obj):
        value = getattr(obj, f.name)

        if value is None:
            continue

        json_key = _to_json_key(f.name, is_permission=is_permission)

        # Recursively handle nested dataclass
        if hasattr(value, "__dataclass_fields__"):
            value = to_json_dict(value)
        elif isinstance(value, list) and value and hasattr(value[0], "__dataclass_fields__"):
            value = [to_json_dict(item) for item in value]

        result[json_key] = value

    return result


def _to_json_key(python_key: str, *, is_permission: bool = False) -> str:
    """将 Python 字段名转为 JSON key。"""
    # Permission 类型中特定字段保持 snake_case
    if is_permission and python_key in _PERMISSION_SNAKE_FIELDS:
        return python_key

    # 查找映射表
    if python_key in _PYTHON_TO_JSON:
        return _PYTHON_TO_JSON[python_key]

    # 无映射则原样返回（如 name, text, read, summary, color 等）
    return python_key


# ── JSON dict → dataclass ──────────────────────────────────


def from_json_dict(cls: type[T], data: dict[str, Any]) -> T:
    """从 JSON dict 构建 dataclass 实例。

    支持 camelCase 和 snake_case 输入（双向查找）。

    Args:
        cls: 目标 dataclass 类
        data: JSON 解析后的 dict

    Returns:
        dataclass 实例
    """
    field_names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {}

    for json_key, value in data.items():
        # 尝试映射到 Python 字段名
        python_key = _to_python_key(json_key)

        # 如果映射结果不在目标类中，尝试原始 key
        if python_key not in field_names:
            if json_key in field_names:
                python_key = json_key
            else:
                # 未知字段，跳过
                continue

        # 嵌套处理: TeamConfig.members → list[TeamMember]
        if python_key == "members" and isinstance(value, list):
            value = [
                from_json_dict(TeamMember, item) if isinstance(item, dict) else item
                for item in value
            ]

        kwargs[python_key] = value

    return cls(**kwargs)


def _to_python_key(json_key: str) -> str:
    """将 JSON key 转为 Python 字段名。"""
    if json_key in _JSON_TO_PYTHON:
        return _JSON_TO_PYTHON[json_key]
    return json_key


# ── 结构化消息解析 ──────────────────────────────────────────

# 消息类型 → dataclass 映射
_MESSAGE_TYPE_MAP: dict[str, type] = {
    "task_assignment": TaskAssignmentMessage,
    "idle_notification": IdleNotificationMessage,
    "shutdown_request": ShutdownRequestMessage,
    "shutdown_approved": ShutdownApprovedMessage,
    "plan_approval_request": PlanApprovalRequestMessage,
    "plan_approval_response": PlanApprovalResponseMessage,
    "permission_request": PermissionRequestMessage,
    "permission_response": PermissionResponseMessage,
    "session_relay": SessionRelayMessage,
}

def parse_message_body(text: str) -> tuple[str, Any] | None:
    """尝试解析 InboxMessage.text 为结构化消息。

    Returns:
        (message_type, dataclass_instance) 或 None（纯文本消息）
    """
    try:
        body = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(body, dict) or "type" not in body:
        return None

    msg_type = body["type"]
    cls = _MESSAGE_TYPE_MAP.get(msg_type)
    if cls is None:
        return None

    # 移除 type 字段（不在 dataclass 中）
    body_copy = {k: v for k, v in body.items() if k != "type"}
    return msg_type, from_json_dict(cls, body_copy)


def build_message_body(msg_type: str, obj: Any) -> str:
    """将结构化消息 dataclass 序列化为 JSON 字符串（用于 InboxMessage.text）。

    自动添加 type 字段。
    """
    d = to_json_dict(obj, cls=type(obj))
    d["type"] = msg_type
    # type 字段放在最前面（可读性）
    ordered = {"type": d.pop("type"), **d}
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


# ── InboxMessage 序列化特殊处理 ─────────────────────────────


def inbox_message_to_dict(msg: InboxMessage) -> dict[str, Any]:
    """InboxMessage → JSON dict（特殊处理 from_ → from）。"""
    result: dict[str, Any] = {
        "from": msg.from_,
        "text": msg.text,
        "timestamp": msg.timestamp,
        "read": msg.read,
    }
    if msg.summary is not None:
        result["summary"] = msg.summary
    if msg.color is not None:
        result["color"] = msg.color
    return result


def inbox_message_from_dict(data: dict[str, Any]) -> InboxMessage:
    """JSON dict → InboxMessage。"""
    return InboxMessage(
        from_=data["from"],
        text=data["text"],
        timestamp=data["timestamp"],
        read=data.get("read", False),
        summary=data.get("summary"),
        color=data.get("color"),
    )


# ── 原子写入 ────────────────────────────────────────────────


def atomic_write_json(path: Path, data: Any) -> None:
    """原子写入 JSON 文件（temp + fsync + rename）。

    流程:
    1. 创建同目录临时文件
    2. 写入 JSON + flush + fsync
    3. os.replace() 原子替换（POSIX 保证）
    4. 异常时清理临时文件
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.fchmod(fd, 0o644)  # Match Claude Code native permissions
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        # 清理临时文件（忽略清理失败）
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# ── 带重试的 JSON 读取 ──────────────────────────────────────

_READ_RETRIES = 3


def read_json(path: Path, *, default: Any = None) -> Any:
    """读取 JSON 文件，带立即重试逻辑处理极端并发场景。

    由于写入使用 atomic_write_json（temp + os.replace 原子替换），
    正常情况下不会遇到损坏/空文件。重试仅作为额外防御层，
    无延迟立即重试以避免阻塞事件循环。

    Args:
        path: JSON 文件路径
        default: 文件不存在时的默认值

    Returns:
        解析后的 JSON 数据

    Raises:
        json.JSONDecodeError: 多次重试后仍无法解析
    """
    if not path.exists():
        return default

    last_error: Exception | None = None
    for _attempt in range(_READ_RETRIES):
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                # 空文件（极端并发场景下的防御检查）
                raise json.JSONDecodeError("Empty file", "", 0)
            return json.loads(text)
        except (json.JSONDecodeError, OSError) as e:
            last_error = e
            # 无延迟立即重试：atomic_write_json 使用 os.replace 原子操作，
            # 损坏窗口极短（纳秒级），无需等待

    if last_error is not None:
        raise last_error
    return default


# ── TeamConfig 序列化 ───────────────────────────────────────


def team_config_to_dict(config: TeamConfig) -> dict[str, Any]:
    """TeamConfig → JSON dict。"""
    return to_json_dict(config)


def team_config_from_dict(data: dict[str, Any]) -> TeamConfig:
    """JSON dict → TeamConfig。"""
    return from_json_dict(TeamConfig, data)


# ── TaskFile 序列化 ─────────────────────────────────────────


def task_file_to_dict(task: TaskFile) -> dict[str, Any]:
    """TaskFile → JSON dict.

    Omits empty activeForm and metadata to match native Claude Code protocol.
    """
    result = to_json_dict(task)
    # Protocol: omit when not meaningfully set
    if result.get("activeForm") == "":
        del result["activeForm"]
    if result.get("metadata") == {}:
        del result["metadata"]
    return result


def task_file_from_dict(data: dict[str, Any]) -> TaskFile:
    """JSON dict → TaskFile。"""
    return from_json_dict(TaskFile, data)
