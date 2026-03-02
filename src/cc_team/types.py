"""cc-team 协议数据模型。

定义所有 dataclass、Literal 类型和 Protocol 接口。
Python 侧统一使用 snake_case，JSON 序列化时按协议要求映射。

命名约定差异（协议层面）：
- shutdown / plan 系列消息使用 camelCase: requestId
- permission 系列消息使用 snake_case: request_id
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol, runtime_checkable

# ── Literal 类型 ────────────────────────────────────────────

TaskStatus = Literal["pending", "in_progress", "completed", "deleted"]

PermissionMode = Literal[
    "default", "acceptEdits", "bypassPermissions", "plan", "dontAsk", "delegate"
]

AgentType = Literal["general-purpose", "Explore", "Plan", "Bash", "team-lead"]

# team-lead agent type 常量，避免散布裸字符串
TEAM_LEAD_AGENT_TYPE: AgentType = "team-lead"

BackendType = Literal["tmux", "in-process", "agent-sdk"]
TMUX_BACKEND: BackendType = "tmux"

# Default LLM model, referenced by SpawnAgentOptions / SpawnLeadOptions / ControllerOptions / CLI
DEFAULT_MODEL: Final[str] = "claude-sonnet-4-6"

AgentColor = Literal["blue", "green", "yellow", "purple", "orange", "pink", "cyan", "red"]

# 8 色循环常量，按 spawn 注册顺序分配: AGENT_COLORS[index % 8]
AGENT_COLORS: tuple[AgentColor, ...] = (
    "blue", "green", "yellow", "purple", "orange", "pink", "cyan", "red"
)

MessageType = Literal[
    "task_assignment",
    "idle_notification",
    "shutdown_request",
    "shutdown_approved",
    "plan_approval_request",
    "plan_approval_response",
    "permission_request",
    "permission_response",
    "session_relay",
]


# ── 团队配置 ────────────────────────────────────────────────


@dataclass
class TeamMember:
    """团队成员（Lead 和 Teammate 共用）。

    Lead 使用前 8 个字段，Teammate 额外使用后 5 个字段。
    """

    # ── 公共字段（Lead + Teammate）──
    agent_id: str  # 格式: {name}@{team_name}
    name: str  # 人类可读名称，通信用
    agent_type: str  # "team-lead", "general-purpose", "Explore" 等
    model: str  # LLM 模型 ID
    joined_at: int  # Unix 毫秒时间戳
    tmux_pane_id: str  # tmux pane ID, lead 为空字符串
    cwd: str  # 工作目录
    subscriptions: list[str] = field(default_factory=list)  # 预留，始终空

    # ── Teammate 专有字段 ──
    prompt: str | None = None  # 初始任务指令
    color: AgentColor | None = None  # UI 颜色
    plan_mode_required: bool | None = None  # 是否强制 plan 模式
    backend_type: BackendType | None = None  # 运行后端: "tmux" 或 "in-process"
    is_active: bool | None = None  # 是否活跃


@dataclass
class TeamConfig:
    """config.json 顶层结构。"""

    name: str  # 团队名称
    description: str  # 团队描述
    created_at: int  # Unix 毫秒时间戳
    lead_agent_id: str  # Team Lead 的 agentId
    lead_session_id: str  # Team Lead 的会话 UUID
    members: list[TeamMember] = field(default_factory=list)


# ── Inbox 消息 ──────────────────────────────────────────────


@dataclass
class InboxMessage:
    """Inbox 文件中的单条消息。

    from/text/timestamp/read 为必选字段，summary/color 为可选。
    注意: Python 中 `from` 是保留字，使用 `from_` 代替。
    """

    from_: str  # 发送者名称（JSON key: "from"）
    text: str  # 消息正文或 JSON 字符串
    timestamp: str  # ISO 8601 时间戳
    read: bool = False  # 是否已消费
    summary: str | None = None  # 摘要文本（可选）
    color: AgentColor | None = None  # 发送方颜色（可选）


# ── 结构化消息内层 body ─────────────────────────────────────
#
# 这些 dataclass 表示 InboxMessage.text 中 JSON 解码后的结构。
# 分为两大命名族：
#   - camelCase 族: shutdown/plan 系列 → requestId
#   - snake_case 族: permission 系列 → request_id


@dataclass
class TaskAssignmentMessage:
    """任务分配通知（系统在 TaskUpdate(owner=X) 时自动生成）。"""

    task_id: str  # 任务 ID (JSON: taskId)
    subject: str  # 任务标题
    description: str  # 任务详情
    assigned_by: str  # 分配者名称 (JSON: assignedBy)
    timestamp: str  # ISO 8601


@dataclass
class IdleNotificationMessage:
    """Agent 空闲通知（Agent turn 结束时系统自动发送）。"""

    from_: str  # 发送方名称 (JSON: from)
    timestamp: str  # ISO 8601
    idle_reason: str | None = None  # 空闲原因 (JSON: idleReason)
    summary: str | None = None  # P2P 时额外携带


@dataclass
class ShutdownRequestMessage:
    """关闭请求（Lead → Agent）。"""

    request_id: str  # 格式: shutdown-{timestamp}@{agent} (JSON: requestId)
    from_: str  # 发送方 (JSON: from)
    reason: str  # 关闭原因
    timestamp: str  # ISO 8601


@dataclass
class ShutdownApprovedMessage:
    """关闭批准（Agent 同意后写入 Lead inbox）。"""

    request_id: str  # (JSON: requestId)
    from_: str  # (JSON: from)
    timestamp: str  # ISO 8601
    backend_id: str  # backend-specific process identifier (JSON: backendId)
    backend_type: str  # "tmux" 或 "in-process" (JSON: backendType)


@dataclass
class PlanApprovalRequestMessage:
    """计划审批请求（Agent 调用 ExitPlanMode 时系统生成）。"""

    from_: str  # 发送方 (JSON: from)
    timestamp: str  # ISO 8601
    plan_file_path: str  # plan 文件路径 (JSON: planFilePath)
    plan_content: str  # 计划内容 (JSON: planContent)
    request_id: str  # (JSON: requestId)


@dataclass
class PlanApprovalResponseMessage:
    """计划审批响应。

    approve=True 时含 permission_mode，
    approve=False 时含 feedback。
    """

    request_id: str  # (JSON: requestId)
    approved: bool  # 是否批准
    timestamp: str  # ISO 8601
    permission_mode: str | None = None  # 审批通过时的权限等级 (JSON: permissionMode)
    feedback: str | None = None  # 拒绝原因


@dataclass
class PermissionRequestMessage:
    """权限请求（snake_case 命名族）。

    Agent 以 acceptEdits 模式运行时，执行受限操作触发。
    """

    request_id: str  # (JSON: request_id, snake_case!)
    agent_id: str  # 请求方名称
    tool_name: str  # 请求使用的工具
    tool_use_id: str  # API tool_use ID
    description: str  # 工具调用描述
    input: dict[str, Any] = field(default_factory=dict)  # 工具调用参数
    permission_suggestions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PermissionResponseMessage:
    """权限响应（snake_case 命名族）。

    subtype="success" 时含 response，
    subtype="error" 时含 error。
    """

    request_id: str  # (JSON: request_id, snake_case!)
    subtype: str  # "success" 或 "error"
    response: dict[str, Any] | None = None  # 成功时的响应体
    error: str | None = None  # 拒绝原因


@dataclass
class SessionRelayMessage:
    """Session 轮转通知（Lead → Agents）。

    relay() 轮转 session 后广播给所有 active agents，
    通知新 session ID，避免 session 分裂。
    """

    from_: str  # 发送方 (JSON: from)
    new_session_id: str  # 新 session ID (JSON: newSessionId)
    previous_session_id: str  # 旧 session ID (JSON: previousSessionId)
    timestamp: str  # ISO 8601


# ── 任务 ────────────────────────────────────────────────────


@dataclass
class TaskFile:
    """任务文件 (~/.claude/tasks/{team}/{id}.json)。"""

    id: str  # 自增 ID
    subject: str  # 任务标题（祈使句）
    description: str  # 任务详情
    status: TaskStatus = "pending"
    active_form: str = ""  # 进行中显示文本 (JSON: activeForm)
    owner: str | None = None  # 负责人名称
    blocks: list[str] = field(default_factory=list)  # 本任务阻塞的下游 ID
    blocked_by: list[str] = field(default_factory=list)  # 本任务的上游依赖 ID (JSON: blockedBy)
    metadata: dict[str, Any] = field(default_factory=dict)  # 附加元数据


# ── Protocol 接口 ───────────────────────────────────────────


@runtime_checkable
class AgentController(Protocol):
    """Agent Handle 和 Controller 之间的解耦接口。

    AgentHandle 通过此 Protocol 与 Controller 交互，
    实现依赖反转（DIP）。
    """

    async def send_message(
        self, recipient: str, content: str, *, summary: str | None = None
    ) -> None:
        """发送消息到指定 Agent。"""
        ...

    async def send_shutdown_request(self, agent_name: str, reason: str) -> str:
        """发送关闭请求，返回 request_id。"""
        ...

    async def kill_agent(self, agent_name: str) -> None:
        """强制终止 Agent。"""
        ...

    def is_agent_running(self, agent_name: str) -> bool:
        """检查 Agent 是否存活。"""
        ...


@runtime_checkable
class AgentBackend(Protocol):
    """Backend abstraction for agent process lifecycle and input delivery.

    Implementations:
    - ProcessManager (tmux): split-window + send-keys
    - Future agent-sdk backend: SDK API + stdin JSON stream
    """

    async def spawn(
        self,
        options: SpawnAgentOptions,
        *,
        team_name: str,
        color: str,
        parent_session_id: str,
    ) -> str:
        """Spawn an agent process. Returns a backend-specific identifier."""
        ...

    async def kill(self, agent_name: str) -> None:
        """Force-kill an agent process.

        Raises:
            AgentNotFoundError: agent is not tracked.
        """
        ...

    def untrack(self, agent_name: str) -> None:
        """Remove agent from tracking (called when agent exits gracefully)."""
        ...

    async def is_running(self, agent_name: str) -> bool:
        """Check whether an agent process is alive."""
        ...

    def track(self, agent_name: str, backend_id: str) -> None:
        """Register an existing agent into tracking (attach/sync scenarios)."""
        ...

    def tracked_agents(self) -> list[str]:
        """Return names of all tracked agents."""
        ...

    async def send_input(self, agent_name: str, text: str) -> None:
        """Deliver input to an agent.

        tmux: send-keys to pane.
        agent-sdk: write JSON to stdin stream.

        Raises:
            AgentNotFoundError: agent is not tracked.
        """
        ...


# ── 配置选项 ────────────────────────────────────────────────


@dataclass
class SpawnAgentOptions:
    """spawn Agent 时的配置选项。"""

    name: str  # Agent 名称
    prompt: str  # 初始指令
    agent_type: str = "general-purpose"  # Agent 类型
    model: str = DEFAULT_MODEL  # LLM 模型
    cwd: str = ""  # Working directory (defaults to os.getcwd() at spawn time)
    plan_mode_required: bool = False  # 是否强制 plan 模式
    permission_mode: PermissionMode | None = None  # 权限模式
    allowed_tools: list[str] | None = None  # 工具白名单
    disallowed_tools: list[str] | None = None  # 工具黑名单


@dataclass
class SpawnLeadOptions:
    """spawn Team Lead 进程时的配置选项。"""

    team_name: str  # 团队名称
    session_id: str  # Lead 的会话 UUID
    model: str = DEFAULT_MODEL  # LLM 模型
    cwd: str = ""  # Working directory (defaults to os.getcwd() at spawn time)
    permission_mode: PermissionMode | None = None  # 权限模式
    backend_id: str | None = None  # reuse existing backend process (relay scenario)


@dataclass
class ControllerOptions:
    """Controller 初始化选项。"""

    team_name: str  # 团队名称
    description: str = ""  # 团队描述
    model: str = DEFAULT_MODEL  # Lead 默认模型
    cwd: str = ""  # 工作目录（默认 os.getcwd()）
    session_id: str = ""  # Lead 会话 ID（默认自动生成）
