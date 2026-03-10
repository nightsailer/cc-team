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
- [上下文接力](#上下文接力)
- [Hooks](#hooks)
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
| `process_manager` | `AgentBackend \| None` | 可选的自定义后端实例（用于测试/DI） |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `team_name` | `str` | 团队名称 |
| `session_id` | `str` | 主智能体会话 UUID |
| `team_manager` | `TeamManager` | 团队配置管理器 |
| `task_manager` | `TaskManager` | 任务管理器 |
| `process_manager` | `AgentBackend` | 进程管理器（AgentBackend 协议） |

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

#### `async attach() -> None`

连接到已有团队（不创建新团队）。用于接管场景：通过 `sync_agents()` 验证 pane 存活状态并恢复 Agent Handle，然后启动收件箱轮询。

```python
ctrl = Controller(ControllerOptions(team_name="existing-team"))
await ctrl.attach()
```

#### `async relay() -> str`

上下文中继：轮转会话 ID，向所有活跃智能体广播 `session_relay`，重启轮询器。返回新的会话 ID。调用方负责停止/重启 TL 进程。

```python
new_session_id = await ctrl.relay()
```

#### `async sync_agents() -> tuple[list[AgentHandle], list[str]]`

双向 Agent 状态同步。对 config.json 中每个有 backend_id 的非 TL 成员：
- 存活 + isActive=false → **恢复**：设置 isActive=true，注册 handle
- 存活 + isActive=true → 正常同步，注册 handle
- 死亡 + isActive=true → 标记 isActive=false
- 死亡 + isActive=false → 跳过（避免冗余写入）

返回 `(synced_handles, recovered_names)`。在 `attach()` 中自动调用。

```python
synced, recovered = await ctrl.sync_agents()
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
| `session:relayed` | `(new_session_id: str)` | 会话通过 relay 轮转 |
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

#### `list_teammates() -> list[TeamMember]`

返回除 team-lead 外的成员列表。

#### `async add_member(member: TeamMember) -> None`

添加成员。名称重复时抛出 `ValueError`。

#### `async register_member(*, name: str, agent_type: str = "general-purpose", model: str = "claude-sonnet-4-6", cwd: str = "", plan_mode_required: bool = False, backend_type: BackendType | None = None) -> TeamMember`

注册成员到 config.json + 创建空 inbox，不启动进程。颜色分配和成员插入在单次锁操作中完成。返回 `is_active=False` 的 `TeamMember`。

```python
member = await mgr.register_member(name="worker", backend_type="tmux")
```

#### `next_color(config: TeamConfig | None = None) -> AgentColor`

分配下一个颜色（基于成员数量的 8 色循环）。传入已有 config 可避免重复 I/O。

#### `async remove_member(name: str) -> None`

移除成员。未找到时抛出 `AgentNotFoundError`。

#### `async update_member(name: str, **updates) -> TeamMember`

更新成员字段。未找到时抛出 `AgentNotFoundError`。

#### `get_lead_session_id() -> str | None`

获取当前 Team Lead 的会话 ID。配置不存在时返回 `None`。

#### `async set_lead_session_id(session_id: str) -> None`

设置 Team Lead 的会话 ID。

#### `async rotate_session(new_session_id: str | None = None) -> str`

轮转 Lead 会话 ID。自动生成新 UUID4（或使用指定 ID）。返回新的会话 ID。

```python
new_sid = await mgr.rotate_session()
```

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

#### `async ensure_exists() -> None`

创建空收件箱文件（若不存在）。由 `register_member()` 调用，用于预创建 inbox 而不写入消息。

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

#### `async send_session_relay(recipients: list[str], *, new_session_id: str, previous_session_id: str) -> None`

并行广播 `session_relay` 结构化消息到多个智能体。由 `Controller.relay()` 调用。

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

#### `async spawn_lead(options: SpawnLeadOptions, *, parent_session_id: str) -> str`

在 tmux 中启动 Team Lead 进程。支持 pane 复用（`options.pane_id`）用于 relay 场景。返回 pane ID。

#### `track(agent_name: str, pane_id: str) -> None`

注册已有智能体到跟踪列表（用于 attach/sync 场景）。

#### `async send_input(agent_name: str, text: str) -> None`

向智能体的 tmux pane 发送输入文本。未被跟踪时抛出 `AgentNotFoundError`。

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

构建 Agent 的 Claude CLI 启动参数。使用 `shlex.join` 确保命令构造安全。

#### `@staticmethod build_lead_cli_args(options: SpawnLeadOptions, *, parent_session_id: str) -> list[str]`

构建 Team Lead 的 Claude CLI 启动参数。包含 `--session-id`，无 `--agent-color`。

#### `async graceful_exit(backend_id: str, *, timeout: int = 30) -> None`

向 tmux pane 发送 `/exit` 并轮询等待退出。超时则抛出 `TimeoutError`。

```python
await pm.graceful_exit("%42", timeout=30)
```

#### `async detect_ready(backend_id: str, *, timeout: int = 60) -> bool`

轮询 `detect_state` 直到 READY/WAITING_INPUT/IDLE 或超时。就绪返回 `True`，超时返回 `False`。

```python
is_ready = await pm.detect_ready("%42", timeout=60)
```

---

## 上下文接力

上下文接力模块，用于轮转 Claude Code 会话并注入交接上下文。

```python
from cc_team._context_relay import RelayRequest, RelayResult, relay_standalone, relay_lead, relay_agent
```

### RelayRequest

```python
@dataclass
class RelayRequest:
    cct_session_id: str      # CCT 会话追踪 ID
    handoff_path: str        # 交接文件路径
    model: str = "claude-sonnet-4-6"
    timeout: int = 30        # 优雅退出超时（秒）
    cwd: str = ""            # 工作目录
```

### RelayResult

```python
@dataclass
class RelayResult:
    old_backend_id: str | None   # 旧 tmux pane ID
    new_backend_id: str          # 新 tmux pane ID
    cct_session_id: str          # CCT 会话追踪 ID
    handoff_injected: bool = False  # 是否已注入交接内容
```

### 函数

#### `async relay_standalone(request, backend, backend_id, tmux) -> RelayResult`

独立 Claude 进程的上下文接力（无团队）。步骤：
1. 优雅退出旧会话
2. 在同一 pane 中构建并发送新 claude 命令
3. 等待就绪 + 注入交接内容
4. 更新历史记录

#### `async relay_lead(request, team_name) -> RelayResult`

Team Lead 的上下文接力。步骤：
1. 优雅退出旧 TL
2. 轮转会话
3. 启动新 TL（复用同一 pane）
4. 通过 TmuxManager 注入交接内容
5. 同步成员状态
6. 更新历史记录

#### `async relay_agent(request, team_name, agent_name) -> RelayResult`

Teammate 的上下文接力。步骤：
1. 优雅退出 Agent
2. 从配置中移除成员
3. 通过 spawn_agent_workflow 重新启动，交接内容作为 prompt
4. 更新历史记录

---

## Hooks

Claude Code 插件 hooks，用于自动上下文接力。

### Stop Hook（`cc_team.hooks.stop`）

两阶段交接机制：

- **阶段 1**（无交接文件）：当上下文使用率超过阈值时，阻止停止并指示编写交接文件
- **阶段 2**（交接文件存在）：在后台启动接力并允许停止

```bash
# 由 Claude Code 插件系统自动调用
cct _hook stop
```

使用 hook input 中的原生 `session_id` 定位接力目录。子 Agent 自动跳过。

### Statusline Hook（`cc_team.hooks.statusline`）

渲染彩色进度条显示上下文窗口使用率：

```bash
# 从 stdin 读取 JSON，输出格式化状态行
cct _hook statusline
```

输出格式：`[agent] ████░░░░ 45.2% | 90k/200k | $0.150 | claude-sonnet-4-6`

颜色：绿色（<60%）、黄色（60-80%）、红色（>80%）。

使用 hook input 中的原生 `session_id` 将使用数据持久化到 `relay/{session_id}/usage.json`。

### 通用工具（`cc_team.hooks._common`）

| 函数 | 说明 |
|------|------|
| `project_dir()` | CLAUDE_PROJECT_DIR，回退到 cwd |
| `read_json(path)` | 读取 JSON 文件，出错时返回 `{}` |
| `write_json(path, data)` | 写入 dict 为 JSON，自动创建目录 |
| `atomic_write_json(path, data)` | 原子写入（tmp + rename） |
| `load_config(proj)` | 读取 context-relay-config.json |
| `cct_data_dir(proj)` | CCT 项目数据目录 |
| `relay_paths(cct_session_id, proj)` | 每会话接力文件路径 |

---

## 类型定义

所有类型均可从 `cc_team` 导入：

```python
from cc_team import (
    # Literal 类型
    TaskStatus,       # "pending" | "in_progress" | "completed" | "deleted"
    PermissionMode,   # "default" | "acceptEdits" | "bypassPermissions" | "plan" | "dontAsk" | "delegate"
    AgentType,        # "general-purpose" | "Explore" | "Plan" | "Bash" | "team-lead"
    BackendType,      # "tmux" | "in-process" | "agent-sdk"
    TMUX_BACKEND,     # BackendType 常量: "tmux"
    AgentColor,       # "blue" | "green" | "yellow" | "purple" | "orange" | "pink" | "cyan" | "red"
    AGENT_COLORS,     # 8 色元组，用于循环分配
    MessageType,      # 所有结构化消息类型字符串的 Literal 联合

    # Dataclass
    TeamConfig,       # 团队 config.json 顶层结构
    TeamMember,       # 团队成员（主智能体: 8 字段，队友: 13 字段）
    InboxMessage,     # 单条收件箱消息（from_, text, timestamp, read, summary?, color?）
    TaskFile,         # 任务文件（id, subject, description, status, owner, blocks, blocked_by, ...）
    SpawnAgentOptions,# 派生配置（name, prompt, agent_type, model, ...）
    SpawnLeadOptions, # TL 派生配置（team_name, session_id, model, pane_id?）
    ControllerOptions,# Controller 初始化选项

    # 结构化消息体
    SessionRelayMessage,        # session_relay（from_, new_session_id, previous_session_id）
    TaskAssignmentMessage,      # task_assignment
    ShutdownRequestMessage,     # shutdown_request
    ShutdownApprovedMessage,    # shutdown_approved
    PlanApprovalRequestMessage, # plan_approval_request
    PlanApprovalResponseMessage,# plan_approval_response
    PermissionRequestMessage,   # permission_request
    PermissionResponseMessage,  # permission_response
    IdleNotificationMessage,    # idle_notification

    # 协议接口
    AgentController,  # AgentHandle 与 Controller 之间的依赖注入接口
    AgentBackend,     # 进程生命周期的后端抽象（tmux、SDK 等）
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

### SpawnLeadOptions

```python
@dataclass
class SpawnLeadOptions:
    team_name: str              # 团队名称
    session_id: str             # Lead 会话 UUID
    model: str = "claude-sonnet-4-6"  # LLM 模型 ID
    cwd: str = ""               # 工作目录（默认: os.getcwd()）
    permission_mode: PermissionMode | None = None
    pane_id: str | None = None  # 复用已有 pane（relay 场景）
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
    backend_id: str    # backend-specific process identifier (empty for lead)
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

### SessionRelayMessage

```python
@dataclass
class SessionRelayMessage:
    from_: str                  # 发送方（JSON: "from"）
    new_session_id: str         # 新会话 ID（JSON: newSessionId）
    previous_session_id: str    # 旧会话 ID（JSON: previousSessionId）
    timestamp: str              # ISO 8601
```

### AgentBackend（Protocol）

智能体进程生命周期的后端抽象。实现：`ProcessManager`（tmux），未来 SDK 后端。

```python
@runtime_checkable
class AgentBackend(Protocol):
    async def spawn(self, options: SpawnAgentOptions, *, team_name: str, color: str, parent_session_id: str) -> str: ...
    async def kill(self, agent_name: str) -> None: ...
    def untrack(self, agent_name: str) -> None: ...
    async def is_running(self, agent_name: str) -> bool: ...
    def track(self, agent_name: str, pane_id: str) -> None: ...
    def tracked_agents(self) -> list[str]: ...
    async def send_input(self, agent_name: str, text: str) -> None: ...
    async def graceful_exit(self, backend_id: str, *, timeout: int = 30) -> None: ...
    async def detect_ready(self, backend_id: str, *, timeout: int = 60) -> bool: ...
```

---

## CLI 参考（会话管理）

### `cct team relay`

Team Lead 上下文接力：退出旧 TL，轮转会话，启动新 TL，自动恢复 Agent 状态。

```bash
cct --team-name <name> team relay [--model <model>] [--timeout <seconds>]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--model` | `claude-sonnet-4-6` | 新 TL 的模型 |
| `--timeout` | `30` | 退出等待超时（秒） |
| `--handoff` | — | 交接文件路径，用于上下文注入 |

**输出**（JSON）：`old_session`, `new_session`, `old_backend_id`, `new_backend_id`, `agents.synced`, `agents.recovered`, `agents.inactive`

### `cct agent relay`

Teammate 上下文接力：退出旧进程，使用全新上下文重新启动。

```bash
cct --team-name <name> agent relay --name <agent> [--prompt <new-prompt>] [--timeout <seconds>]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--name` | （必填） | Agent 名称 |
| `--prompt` | （沿用原始） | Agent 的新 prompt |
| `--timeout` | `30` | 退出等待超时（秒） |
| `--handoff` | — | 交接文件路径，用于上下文注入 |

**输出**（JSON）：`name`, `old_backend_id`, `new_backend_id`, `prompt`, `color`

### `cct agent sync`

双向 Agent 状态同步：验证进程存活状态，恢复不活跃但存活的 Agent，标记已死亡的 Agent。

```bash
cct --team-name <name> agent sync
```

**输出**（JSON）：`synced`, `recovered`, `inactive`

### `cct relay`（统一接口）

统一上下文接力命令。读取 RelayContext JSON 自动判断模式（standalone/team-lead/teammate）并分派到对应执行器。

```bash
cct relay --context <path-to-context.json> [--handoff <override>] [--model <model>] [--timeout <seconds>]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--context` | （必填） | RelayContext JSON 路径（`relay/{session_id}/context.json`） |
| `--handoff` | — | 覆盖交接文件路径（默认从 context 目录读取） |
| `--model` | `claude-sonnet-4-6` | 新会话的模型 |
| `--timeout` | `30` | 退出等待超时（秒） |

Stop hook 会自动调用 `cct relay --context <path>` 启动接力。

### `cct setup`

显示插件路径或安装符号链接。

```bash
cct setup [--install]
```

| 参数 | 说明 |
|------|------|
| `--install` | 在 `~/.claude/plugins/cc-team` 创建符号链接 |

### `cct session start`

启动带接力环境变量的新 Claude 会话（`CCT_RELAY_MODE=standalone`）。

```bash
cct session start [-- <claude-args>...]
```

创建接力目录结构，设置 `CCT_RELAY_MODE` 环境变量，并用 `claude` 替换当前进程。

### 环境变量

| 变量 | 说明 |
|------|------|
| `CCT_RELAY_MODE` | 接力模式：`standalone`、`team-lead`、`teammate` |
| `CCT_TEAM_NAME` | 团队名称（配合 `CCT_RELAY_MODE` 使用） |
| `CCT_MEMBER_NAME` | 成员名称（`CCT_RELAY_MODE=teammate` 时使用） |
| `CCT_RELAY_PROMPT_TEMPLATE` | 自定义接力注入提示模板（接收 `{content}`） |
| `CCT_PROJECT_DATA_DIR` | 覆盖 CCT 数据目录（默认：`{project}/.claude/cct/`） |

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
    TeamAlreadyExistsError, # 团队已存在（由 TeamManager.create 抛出）
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
    ├── CyclicDependencyError
    └── TeamAlreadyExistsError
```

### 重要属性

| 异常 | 属性 | 类型 | 说明 |
|------|------|------|------|
| `AgentNotFoundError` | `agent_name` | `str` | 未找到的智能体名称 |
| `FileLockError` | `path` | `str` | 锁文件路径 |
| `FileLockError` | `attempts` | `int` | 获取锁的尝试次数 |
| `CyclicDependencyError` | `task_id` | `str` | 会产生循环的任务 |
| `CyclicDependencyError` | `blocked_by` | `list[str]` | 构成循环的依赖列表 |
