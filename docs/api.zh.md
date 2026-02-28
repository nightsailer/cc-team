# cc-team API 参考文档

## 目录

- [Controller](#controller)
- [AgentHandle](#agenthandle)
- [TeamManager](#teammanager)
- [TaskManager](#taskmanager)
- [InboxIO](#inboxio)
- [InboxPoller](#inboxpoller)
- [MessageBuilder](#messagebuilder)
- [AsyncEventEmitter](#asynceventemitter)
- [ProcessManager](#processmanager)
- [类型定义](#类型定义)
- [异常](#异常)

---

## Controller

多智能体团队生命周期的中央编排器。继承自 `AsyncEventEmitter`。

```python
from cc_team import Controller, ControllerOptions
```

### 构造函数

```python
Controller(
    options: ControllerOptions,
    *,
    process_manager: ProcessManager | None = None,
)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `options` | `ControllerOptions` | 团队名称、描述、模型、工作目录、会话 ID |
| `process_manager` | `ProcessManager \| None` | 可选的自定义进程管理器实例（用于测试） |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `team_name` | `str` | 团队名称 |
| `session_id` | `str` | 主智能体会话 UUID |
| `team_manager` | `TeamManager` | 团队配置管理器 |
| `task_manager` | `TaskManager` | 任务管理器 |
| `process_manager` | `ProcessManager` | 进程管理器 |

### 方法

#### `async init() -> None`

初始化 Controller：创建团队、注册主智能体、启动收件箱轮询。

**必须在调用其他方法之前调用。**

```python
ctrl = Controller(ControllerOptions(team_name="my-team"))
await ctrl.init()
```

#### `async shutdown() -> None`

关闭 Controller：停止轮询、终止所有智能体、销毁团队。

```python
await ctrl.shutdown()
```

#### `async spawn(options: SpawnAgentOptions) -> AgentHandle`

通过 5 步流程派生新智能体：
1. 分配颜色 + 注册成员
2. 将初始提示写入收件箱
3. 启动 tmux 进程
4. 更新配置中的 pane ID
5. 创建 AgentHandle

失败时自动回滚（移除已注册的成员）。

```python
handle = await ctrl.spawn(SpawnAgentOptions(
    name="researcher",
    prompt="Analyze the codebase",
    model="claude-sonnet-4-6",
))
```

#### `get_handle(agent_name: str) -> AgentHandle`

获取已有智能体的句柄。若未找到则抛出 `AgentNotFoundError`。

#### `list_agents() -> list[str]`

返回所有已注册智能体的名称列表。

#### `async kill_agent(agent_name: str) -> None`

强制终止智能体进程（tmux kill-pane）。

#### `is_agent_running(agent_name: str) -> bool`

检查智能体是否存活（同步跟踪列表检查）。

#### `async send_message(recipient: str, content: str, *, summary: str | None = None) -> None`

向指定智能体发送消息。

#### `async send_shutdown_request(agent_name: str, reason: str) -> str`

发送优雅关闭请求。返回 `request_id`。

#### `async broadcast(content: str, *, summary: str | None = None, exclude: list[str] | None = None) -> None`

向所有智能体广播消息。可选择排除特定智能体。

#### `async send_plan_approval(agent_name: str, request_id: str, *, approved: bool = True, permission_mode: str = "default", feedback: str | None = None) -> None`

响应智能体的计划审批请求。

#### `async create_task(*, subject: str, description: str, active_form: str = "", owner: str | None = None) -> TaskFile`

创建任务。若设置了 `owner`，会自动发送 `task_assignment` 消息。

#### `async update_task(task_id: str, *, status: TaskStatus | None = None, owner: str | None = ..., **kwargs) -> TaskFile`

更新任务。变更 owner 会触发 `task_assignment` 消息，任务完成会触发 `task:completed` 事件。

#### `list_tasks() -> list[TaskFile]`

列出所有任务。

### 事件

| 事件 | 处理器签名 | 说明 |
|------|-----------|------|
| `message` | `(agent_name: str, msg: InboxMessage)` | 智能体发送了消息 |
| `idle` | `(agent_name: str)` | 智能体进入空闲状态 |
| `shutdown:approved` | `(agent_name: str, msg: ShutdownApprovedMessage)` | 智能体批准了关闭请求 |
| `plan:approval_request` | `(agent_name: str, msg: PlanApprovalRequestMessage)` | 智能体请求计划审批 |
| `permission:request` | `(agent_name: str, msg: PermissionRequestMessage)` | 智能体请求权限 |
| `task:completed` | `(task: TaskFile)` | 任务已完成 |
| `agent:spawned` | `(agent_name: str, pane_id: str)` | 智能体进程已启动 |
| `agent:exited` | `(agent_name: str, exit_code: int)` | 智能体进程已退出 |
| `error` | `(exc: Exception)` | 发生错误 |

---

## AgentHandle

单个智能体的代理对象。通过 `Controller.spawn()` 或 `Controller.get_handle()` 获取。

```python
from cc_team import AgentHandle
```

### 构造函数

```python
AgentHandle(
    name: str,
    controller: AgentController,
    *,
    pane_id: str = "",
    color: AgentColor | None = None,
)
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 智能体名称 |
| `pane_id` | `str` | tmux pane ID |
| `color` | `AgentColor \| None` | 分配的 UI 颜色 |

### 方法

#### `async send(content: str, *, summary: str | None = None) -> None`

向此智能体发送消息。

#### `async shutdown(reason: str = "Task complete") -> str`

发送优雅关闭请求。返回 `request_id`。

#### `async kill() -> None`

强制终止智能体进程。

#### `is_running() -> bool`

检查智能体是否存活。

---

## TeamManager

`config.json` 的 CRUD 管理器。所有写操作均受文件锁保护。

```python
from cc_team import TeamManager
```

### 构造函数

```python
TeamManager(team_name: str)
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `team_name` | `str` | 团队名称 |
| `config_path` | `Path` | config.json 的路径 |

### 方法

#### `async create(*, description: str = "", lead_name: str = "team-lead", lead_model: str = "claude-sonnet-4-6", lead_session_id: str = "", cwd: str = "") -> TeamConfig`

创建新团队。初始化 `config.json`、收件箱目录和任务目录。

#### `read() -> TeamConfig | None`

读取当前团队配置。未找到时返回 `None`。

#### `get_member(name: str) -> TeamMember | None`

按名称查找成员。

#### `list_members() -> list[TeamMember]`

返回所有团队成员。

#### `async add_member(member: TeamMember) -> None`

添加成员。名称重复时抛出 `ValueError`。

#### `next_color() -> str`

分配下一个颜色（基于成员数量的 8 色循环）。

#### `async remove_member(name: str) -> None`

移除成员。未找到时抛出 `AgentNotFoundError`。

#### `async update_member(name: str, **updates) -> TeamMember`

更新成员字段。未找到时抛出 `AgentNotFoundError`。

#### `async destroy() -> None`

销毁团队及所有关联目录。

---

## TaskManager

带有 DAG 依赖管理的任务 CRUD。所有写操作使用目录级文件锁。

```python
from cc_team import TaskManager
```

### 构造函数

```python
TaskManager(team_name: str)
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `tasks_dir` | `Path` | 任务文件目录路径 |

### 方法

#### `async create(*, subject: str, description: str, active_form: str = "", owner: str | None = None, metadata: dict | None = None) -> TaskFile`

创建任务，自动递增 ID。

#### `read(task_id: str) -> TaskFile | None`

读取单个任务。未找到时返回 `None`。

#### `list_all() -> list[TaskFile]`

列出所有任务，按 ID 排序。

#### `list_available() -> list[TaskFile]`

列出可认领的任务（状态为 pending + 无 owner + blockedBy 为空）。

#### `async update(task_id: str, *, status: TaskStatus | None = None, subject: str | None = None, description: str | None = None, active_form: str | None = None, owner: str | None = ..., metadata: dict | None = None) -> TaskFile`

更新任务字段。使用 `...` 哨兵值区分"不更新 owner"与"将 owner 设为 None"。

#### `async delete(task_id: str) -> None`

删除任务（标记为 `deleted` 并清理依赖链接）。

#### `async add_dependency(task_id: str, blocked_by_ids: list[str]) -> None`

添加依赖关系（双向链接 + BFS 环检测）。检测到循环时抛出 `CyclicDependencyError`。

#### `async remove_dependency(task_id: str, blocked_by_ids: list[str]) -> None`

移除依赖关系（双向清理）。

---

## InboxIO

带有逐收件箱文件锁的收件箱文件 I/O。

```python
from cc_team import InboxIO
```

### 构造函数

```python
InboxIO(team_name: str, agent_name: str)
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `inbox_path` | `Path` | 收件箱 JSON 文件路径 |

### 方法

#### `async write(message: InboxMessage) -> None`

向收件箱文件追加消息（文件不存在时自动创建）。

#### `async write_initial_prompt(from_name: str, prompt: str) -> None`

将初始提示写为第一条消息（协议必需，无 summary/color）。

#### `read_all() -> list[InboxMessage]`

读取所有消息。

#### `read_unread() -> list[InboxMessage]`

读取未读消息（不标记为已读）。

#### `async mark_read() -> list[InboxMessage]`

将所有未读消息标记为已读。返回新标记的消息列表。

#### `has_unread() -> bool`

检查是否存在未读消息。

#### `mtime_ns() -> int`

获取收件箱文件的修改时间（纳秒）。文件不存在时返回 `0`。

---

## InboxPoller

带有 mtime 优化和结构化消息解析的异步收件箱轮询器。

```python
from cc_team import InboxPoller
```

### 构造函数

```python
InboxPoller(
    team_name: str,
    agent_name: str,
    *,
    interval: float = 0.5,
)
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `team_name` | `str` | — | 团队名称 |
| `agent_name` | `str` | — | 要轮询收件箱的智能体 |
| `interval` | `float` | `0.5` | 轮询间隔（秒） |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `running` | `bool` | 轮询器是否处于活跃状态 |

### 方法

#### `on_message(handler) -> None`

注册消息处理器。

```python
async def handler(msg: InboxMessage, msg_type: str | None, parsed: Any | None):
    # msg_type: "shutdown_request"、"idle_notification" 等，纯文本时为 None
    # parsed: 结构化消息 dataclass 或 None
    pass

poller.on_message(handler)
```

#### `on_error(handler) -> None`

注册错误处理器。

```python
async def handler(exc: Exception, context: str):
    pass

poller.on_error(handler)
```

#### `async start() -> None`

启动轮询循环。

#### `async stop() -> None`

停止轮询循环。

#### `async poll_once() -> list[InboxMessage]`

手动触发单次轮询（适用于测试）。返回已处理的消息列表。

---

## MessageBuilder

所有协议消息类型的结构化消息构建器。

```python
from cc_team import MessageBuilder
```

### 构造函数

```python
MessageBuilder(team_name: str, lead_name: str = "team-lead")
```

### 方法

#### `async send_plain(recipient: str, content: str, *, summary: str | None = None, from_name: str | None = None, color: str | None = None) -> None`

发送纯文本消息。

#### `async send_shutdown_request(recipient: str, reason: str) -> str`

发送关闭请求。返回 `request_id`。

#### `async send_task_assignment(recipient: str, task: TaskFile) -> None`

发送任务分配通知。

#### `async send_plan_approval(recipient: str, request_id: str, *, approved: bool = True, permission_mode: str = "default", feedback: str | None = None) -> None`

发送计划审批/拒绝响应。

#### `async broadcast(content: str, recipients: list[str], *, summary: str | None = None, from_name: str | None = None) -> None`

向多个智能体广播消息。

---

## AsyncEventEmitter

Node.js 风格的异步事件发射器，支持并发处理器执行。

```python
from cc_team import AsyncEventEmitter
```

### 构造函数

```python
AsyncEventEmitter()
```

### 方法

#### `on(event: str, handler: EventHandler) -> None`

注册持久事件处理器。

#### `once(event: str, handler: EventHandler) -> None`

注册一次性事件处理器（首次触发后自动移除）。

#### `off(event: str, handler: EventHandler) -> None`

移除特定事件处理器。

#### `remove_all_listeners(event: str | None = None) -> None`

移除指定事件的所有处理器；若 `event` 为 `None` 则移除全部事件的处理器。

#### `async emit(event: str, *args) -> bool`

触发事件，并发执行所有处理器。若有处理器被调用则返回 `True`。

#### `listener_count(event: str) -> int`

返回指定事件的处理器数量。

#### `event_names() -> list[str]`

返回所有已注册的事件名称。

---

## ProcessManager

基于 tmux 后端的智能体进程生命周期管理器。

```python
from cc_team.process_manager import ProcessManager
```

### 构造函数

```python
ProcessManager(*, tmux: TmuxManager | None = None)
```

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `tmux` | `TmuxManager` | tmux 管理器 |

### 方法

#### `async spawn(options: SpawnAgentOptions, *, team_name: str, color: str, parent_session_id: str) -> str`

在 tmux pane 中启动 Claude 智能体。返回 pane ID。

#### `async kill(agent_name: str) -> None`

强制终止智能体。未被跟踪时抛出 `AgentNotFoundError`。

#### `untrack(agent_name: str) -> None`

将智能体从跟踪列表中移除（用于自行退出的智能体）。

#### `async is_running(agent_name: str) -> bool`

检查智能体进程是否存活。

#### `get_pane_id(agent_name: str) -> str | None`

获取智能体的 tmux pane ID。

#### `tracked_agents() -> list[str]`

返回所有被跟踪的智能体名称。

#### `@staticmethod build_cli_args(options: SpawnAgentOptions, *, team_name: str, color: str, parent_session_id: str) -> list[str]`

构建 Claude CLI 启动参数。使用 `shlex.join` 确保命令构造安全。

---

## 类型定义

所有类型均可从 `cc_team` 导入：

```python
from cc_team import (
    # Literal 类型
    TaskStatus,       # "pending" | "in_progress" | "completed" | "deleted"
    PermissionMode,   # "default" | "acceptEdits" | "bypassPermissions" | "plan" | "dontAsk" | "delegate"
    AgentType,        # "general-purpose" | "Explore" | "Plan" | "Bash" | "team-lead"
    BackendType,      # "tmux" | "in-process"
    AgentColor,       # "blue" | "green" | "yellow" | "purple" | "orange" | "pink" | "cyan" | "red"
    AGENT_COLORS,     # 8 色元组，用于循环分配

    # Dataclass
    TeamConfig,       # 团队 config.json 顶层结构
    TeamMember,       # 团队成员（主智能体: 8 字段，队友: 13 字段）
    InboxMessage,     # 单条收件箱消息（from_, text, timestamp, read, summary?, color?）
    TaskFile,         # 任务文件（id, subject, description, status, owner, blocks, blocked_by, ...）
    SpawnAgentOptions,# 派生配置（name, prompt, agent_type, model, ...）
    ControllerOptions,# Controller 初始化选项

    # 协议接口
    AgentController,  # AgentHandle 与 Controller 之间的依赖注入接口
)
```

### SpawnAgentOptions

```python
@dataclass
class SpawnAgentOptions:
    name: str                              # 智能体名称
    prompt: str                            # 初始指令
    agent_type: str = "general-purpose"    # 智能体类型
    model: str = "claude-sonnet-4-6"       # LLM 模型 ID
    plan_mode_required: bool = False       # 强制计划模式
    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
```

### ControllerOptions

```python
@dataclass
class ControllerOptions:
    team_name: str              # 团队名称
    description: str = ""       # 团队描述
    model: str = "claude-sonnet-4-6"  # 主智能体默认模型
    cwd: str = ""               # 工作目录（默认: os.getcwd()）
    session_id: str = ""        # 主智能体会话 UUID（默认: 自动生成）
```

### TeamMember

```python
@dataclass
class TeamMember:
    # 公共字段（主智能体 + 队友）
    agent_id: str       # 格式: {name}@{team_name}
    name: str           # 人类可读名称
    agent_type: str     # "team-lead"、"general-purpose" 等
    model: str          # LLM 模型 ID
    joined_at: int      # Unix 毫秒时间戳
    tmux_pane_id: str   # tmux pane ID（主智能体为空）
    cwd: str            # 工作目录
    subscriptions: list[str] = []  # 保留字段，始终为空

    # 队友专属字段
    prompt: str | None = None
    color: AgentColor | None = None
    plan_mode_required: bool | None = None
    backend_type: BackendType | None = None
    is_active: bool | None = None
```

### InboxMessage

```python
@dataclass
class InboxMessage:
    from_: str          # 发送者名称（JSON 键名: "from"）
    text: str           # 消息正文或 JSON 字符串
    timestamp: str      # ISO 8601 时间戳
    read: bool = False  # 是否已消费
    summary: str | None = None
    color: AgentColor | None = None
```

### TaskFile

```python
@dataclass
class TaskFile:
    id: str                        # 自增 ID
    subject: str                   # 任务标题（祈使句式）
    description: str               # 详细描述
    status: TaskStatus = "pending"
    active_form: str = ""          # 进度旋转器文本
    owner: str | None = None
    blocks: list[str] = []         # 下游任务 ID
    blocked_by: list[str] = []     # 上游依赖 ID
    metadata: dict = {}
```

---

## 异常

所有异常继承自 `CCTeamError`：

```python
from cc_team import (
    CCTeamError,            # cc-team 所有错误的基类
    NotInitializedError,    # Controller 未初始化（未调用 init 或已关闭）
    AgentNotFoundError,     # 团队中未找到智能体（包含 .agent_name 属性）
    MessageTimeoutError,    # 消息接收超时
    FileLockError,          # 锁获取失败（包含 .path、.attempts 属性）
    TmuxError,              # tmux 操作失败
    SpawnError,             # 智能体派生过程失败
    ProtocolError,          # 协议格式错误（JSON 解析失败、缺少字段）
    CyclicDependencyError,  # 循环任务依赖（包含 .task_id、.blocked_by 属性）
)
```

### 异常层级

```
Exception
└── CCTeamError
    ├── NotInitializedError
    ├── AgentNotFoundError
    ├── MessageTimeoutError
    ├── FileLockError
    ├── TmuxError
    ├── SpawnError
    ├── ProtocolError
    └── CyclicDependencyError
```

### 重要属性

| 异常 | 属性 | 类型 | 说明 |
|------|------|------|------|
| `AgentNotFoundError` | `agent_name` | `str` | 未找到的智能体名称 |
| `FileLockError` | `path` | `str` | 锁文件路径 |
| `FileLockError` | `attempts` | `int` | 获取锁的尝试次数 |
| `CyclicDependencyError` | `task_id` | `str` | 会产生循环的任务 |
| `CyclicDependencyError` | `blocked_by` | `list[str]` | 构成循环的依赖列表 |
