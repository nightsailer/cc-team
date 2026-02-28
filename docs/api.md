# cc-team API Reference

## Table of Contents

- [Controller](#controller)
- [AgentHandle](#agenthandle)
- [TeamManager](#teammanager)
- [TaskManager](#taskmanager)
- [InboxIO](#inboxio)
- [InboxPoller](#inboxpoller)
- [MessageBuilder](#messagebuilder)
- [AsyncEventEmitter](#asynceventemitter)
- [ProcessManager](#processmanager)
- [Types](#types)
- [Exceptions](#exceptions)

---

## Controller

Central orchestrator for multi-agent team lifecycle. Inherits from `AsyncEventEmitter`.

```python
from cc_team import Controller, ControllerOptions
```

### Constructor

```python
Controller(
    options: ControllerOptions,
    *,
    process_manager: ProcessManager | None = None,
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `options` | `ControllerOptions` | Team name, description, model, cwd, session_id |
| `process_manager` | `ProcessManager \| None` | Optional custom PM instance (for testing) |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `team_name` | `str` | Team name |
| `session_id` | `str` | Lead agent session UUID |
| `team_manager` | `TeamManager` | Access to team config manager |
| `task_manager` | `TaskManager` | Access to task manager |
| `process_manager` | `ProcessManager` | Access to process manager |

### Methods

#### `async init() -> None`

Initialize the controller: create team, register lead, start inbox polling.

**Must be called before any other method.**

```python
ctrl = Controller(ControllerOptions(team_name="my-team"))
await ctrl.init()
```

#### `async shutdown() -> None`

Shut down the controller: stop polling, kill all agents, destroy team.

```python
await ctrl.shutdown()
```

#### `async spawn(options: SpawnAgentOptions) -> AgentHandle`

Spawn a new agent with 5-step flow:
1. Assign color + register member
2. Write initial prompt to inbox
3. Start tmux process
4. Update pane ID in config
5. Create AgentHandle

Includes rollback on failure (removes registered member if spawn fails).

```python
handle = await ctrl.spawn(SpawnAgentOptions(
    name="researcher",
    prompt="Analyze the codebase",
    model="claude-sonnet-4-6",
))
```

#### `get_handle(agent_name: str) -> AgentHandle`

Get an existing agent's handle. Raises `AgentNotFoundError` if not found.

#### `list_agents() -> list[str]`

Return names of all registered agents.

#### `async kill_agent(agent_name: str) -> None`

Force-kill an agent process (tmux kill-pane).

#### `is_agent_running(agent_name: str) -> bool`

Check if an agent is alive (synchronous tracking list check).

#### `async send_message(recipient: str, content: str, *, summary: str | None = None) -> None`

Send a message to a specific agent.

#### `async send_shutdown_request(agent_name: str, reason: str) -> str`

Send a graceful shutdown request. Returns the `request_id`.

#### `async broadcast(content: str, *, summary: str | None = None, exclude: list[str] | None = None) -> None`

Broadcast a message to all agents. Optionally exclude specific agents.

#### `async send_plan_approval(agent_name: str, request_id: str, *, approved: bool = True, permission_mode: str = "default", feedback: str | None = None) -> None`

Respond to a plan approval request from an agent.

#### `async create_task(*, subject: str, description: str, active_form: str = "", owner: str | None = None) -> TaskFile`

Create a task. If `owner` is set, automatically sends a `task_assignment` message.

#### `async update_task(task_id: str, *, status: TaskStatus | None = None, owner: str | None = ..., **kwargs) -> TaskFile`

Update a task. Owner changes trigger `task_assignment` messages. Completion triggers `task:completed` event.

#### `list_tasks() -> list[TaskFile]`

List all tasks.

### Events

| Event | Handler Signature | Description |
|-------|-------------------|-------------|
| `message` | `(agent_name: str, msg: InboxMessage)` | Agent sent a message |
| `idle` | `(agent_name: str)` | Agent became idle |
| `shutdown:approved` | `(agent_name: str, msg: ShutdownApprovedMessage)` | Agent approved shutdown |
| `plan:approval_request` | `(agent_name: str, msg: PlanApprovalRequestMessage)` | Agent requests plan approval |
| `permission:request` | `(agent_name: str, msg: PermissionRequestMessage)` | Agent requests permission |
| `task:completed` | `(task: TaskFile)` | Task completed |
| `agent:spawned` | `(agent_name: str, pane_id: str)` | Agent process started |
| `agent:exited` | `(agent_name: str, exit_code: int)` | Agent process exited |
| `error` | `(exc: Exception)` | Error occurred |

---

## AgentHandle

Proxy object for a single agent. Obtained from `Controller.spawn()` or `Controller.get_handle()`.

```python
from cc_team import AgentHandle
```

### Constructor

```python
AgentHandle(
    name: str,
    controller: AgentController,
    *,
    pane_id: str = "",
    color: AgentColor | None = None,
)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `name` | `str` | Agent name |
| `pane_id` | `str` | tmux pane ID |
| `color` | `AgentColor \| None` | Assigned UI color |

### Methods

#### `async send(content: str, *, summary: str | None = None) -> None`

Send a message to this agent.

#### `async shutdown(reason: str = "Task complete") -> str`

Send a graceful shutdown request. Returns `request_id`.

#### `async kill() -> None`

Force-kill the agent process.

#### `is_running() -> bool`

Check if the agent is alive.

---

## TeamManager

CRUD manager for `config.json`. All write operations are protected by file locks.

```python
from cc_team import TeamManager
```

### Constructor

```python
TeamManager(team_name: str)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `team_name` | `str` | Team name |
| `config_path` | `Path` | Path to config.json |

### Methods

#### `async create(*, description: str = "", lead_name: str = "team-lead", lead_model: str = "claude-sonnet-4-6", lead_session_id: str = "", cwd: str = "") -> TeamConfig`

Create a new team. Initializes `config.json`, inbox directory, and task directory.

#### `read() -> TeamConfig | None`

Read current team configuration. Returns `None` if not found.

#### `get_member(name: str) -> TeamMember | None`

Find a member by name.

#### `list_members() -> list[TeamMember]`

Return all team members.

#### `async add_member(member: TeamMember) -> None`

Add a member. Raises `ValueError` on duplicate name.

#### `next_color() -> str`

Allocate the next color (8-color cycling based on member count).

#### `async remove_member(name: str) -> None`

Remove a member. Raises `AgentNotFoundError` if not found.

#### `async update_member(name: str, **updates) -> TeamMember`

Update member fields. Raises `AgentNotFoundError` if not found.

#### `async destroy() -> None`

Destroy the team and all associated directories.

---

## TaskManager

Task CRUD with DAG dependency management. All writes use directory-level file locks.

```python
from cc_team import TaskManager
```

### Constructor

```python
TaskManager(team_name: str)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `tasks_dir` | `Path` | Path to task files directory |

### Methods

#### `async create(*, subject: str, description: str, active_form: str = "", owner: str | None = None, metadata: dict | None = None) -> TaskFile`

Create a task with auto-incrementing ID.

#### `read(task_id: str) -> TaskFile | None`

Read a single task. Returns `None` if not found.

#### `list_all() -> list[TaskFile]`

List all tasks, sorted by ID.

#### `list_available() -> list[TaskFile]`

List claimable tasks (pending + no owner + empty blockedBy).

#### `async update(task_id: str, *, status: TaskStatus | None = None, subject: str | None = None, description: str | None = None, active_form: str | None = None, owner: str | None = ..., metadata: dict | None = None) -> TaskFile`

Update task fields. Uses `...` sentinel for owner to distinguish "don't update" from "set to None".

#### `async delete(task_id: str) -> None`

Delete a task (marks as `deleted` and cleans up dependency links).

#### `async add_dependency(task_id: str, blocked_by_ids: list[str]) -> None`

Add dependencies (bidirectional linking + BFS cycle detection). Raises `CyclicDependencyError` on cycles.

#### `async remove_dependency(task_id: str, blocked_by_ids: list[str]) -> None`

Remove dependencies (bidirectional cleanup).

---

## InboxIO

Inbox file I/O with per-inbox file locking.

```python
from cc_team import InboxIO
```

### Constructor

```python
InboxIO(team_name: str, agent_name: str)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `inbox_path` | `Path` | Path to inbox JSON file |

### Methods

#### `async write(message: InboxMessage) -> None`

Append a message to the inbox file (creates if absent).

#### `async write_initial_prompt(from_name: str, prompt: str) -> None`

Write the initial prompt as the first message (protocol-required, no summary/color).

#### `read_all() -> list[InboxMessage]`

Read all messages.

#### `read_unread() -> list[InboxMessage]`

Read unread messages (does not mark as read).

#### `async mark_read() -> list[InboxMessage]`

Mark all unread messages as read. Returns the newly marked messages.

#### `has_unread() -> bool`

Check if there are unread messages.

#### `mtime_ns() -> int`

Get inbox file modification time in nanoseconds. Returns `0` if file doesn't exist.

---

## InboxPoller

Async inbox poller with mtime optimization and structured message parsing.

```python
from cc_team import InboxPoller
```

### Constructor

```python
InboxPoller(
    team_name: str,
    agent_name: str,
    *,
    interval: float = 0.5,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `team_name` | `str` | — | Team name |
| `agent_name` | `str` | — | Agent to poll inbox for |
| `interval` | `float` | `0.5` | Poll interval in seconds |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `running` | `bool` | Whether the poller is active |

### Methods

#### `on_message(handler) -> None`

Register a message handler.

```python
async def handler(msg: InboxMessage, msg_type: str | None, parsed: Any | None):
    # msg_type: "shutdown_request", "idle_notification", etc. or None for plain text
    # parsed: structured message dataclass or None
    pass

poller.on_message(handler)
```

#### `on_error(handler) -> None`

Register an error handler.

```python
async def handler(exc: Exception, context: str):
    pass

poller.on_error(handler)
```

#### `async start() -> None`

Start the polling loop.

#### `async stop() -> None`

Stop the polling loop.

#### `async poll_once() -> list[InboxMessage]`

Trigger a single poll manually (useful for testing). Returns processed messages.

---

## MessageBuilder

Structured message constructor for all protocol message types.

```python
from cc_team import MessageBuilder
```

### Constructor

```python
MessageBuilder(team_name: str, lead_name: str = "team-lead")
```

### Methods

#### `async send_plain(recipient: str, content: str, *, summary: str | None = None, from_name: str | None = None, color: str | None = None) -> None`

Send a plain text message.

#### `async send_shutdown_request(recipient: str, reason: str) -> str`

Send a shutdown request. Returns `request_id`.

#### `async send_task_assignment(recipient: str, task: TaskFile) -> None`

Send a task assignment notification.

#### `async send_plan_approval(recipient: str, request_id: str, *, approved: bool = True, permission_mode: str = "default", feedback: str | None = None) -> None`

Send a plan approval/rejection response.

#### `async broadcast(content: str, recipients: list[str], *, summary: str | None = None, from_name: str | None = None) -> None`

Broadcast a message to multiple agents.

---

## AsyncEventEmitter

Node.js-style async event emitter with concurrent handler execution.

```python
from cc_team import AsyncEventEmitter
```

### Constructor

```python
AsyncEventEmitter()
```

### Methods

#### `on(event: str, handler: EventHandler) -> None`

Register a persistent event handler.

#### `once(event: str, handler: EventHandler) -> None`

Register a one-time event handler (auto-removed after first trigger).

#### `off(event: str, handler: EventHandler) -> None`

Remove a specific event handler.

#### `remove_all_listeners(event: str | None = None) -> None`

Remove all handlers for a specific event, or all events if `event` is `None`.

#### `async emit(event: str, *args) -> bool`

Fire an event, concurrently executing all handlers. Returns `True` if any handler was invoked.

#### `listener_count(event: str) -> int`

Return the number of handlers for an event.

#### `event_names() -> list[str]`

Return all registered event names.

---

## ProcessManager

Agent process lifecycle manager using tmux backend.

```python
from cc_team.process_manager import ProcessManager
```

### Constructor

```python
ProcessManager(*, tmux: TmuxManager | None = None)
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `tmux` | `TmuxManager` | Access to tmux manager |

### Methods

#### `async spawn(options: SpawnAgentOptions, *, team_name: str, color: str, parent_session_id: str) -> str`

Start a Claude agent in a tmux pane. Returns the pane ID.

#### `async kill(agent_name: str) -> None`

Force-kill an agent. Raises `AgentNotFoundError` if not tracked.

#### `untrack(agent_name: str) -> None`

Remove an agent from the tracking list (for agents that exited on their own).

#### `async is_running(agent_name: str) -> bool`

Check if an agent process is alive.

#### `get_pane_id(agent_name: str) -> str | None`

Get an agent's tmux pane ID.

#### `tracked_agents() -> list[str]`

Return all tracked agent names.

#### `@staticmethod build_cli_args(options: SpawnAgentOptions, *, team_name: str, color: str, parent_session_id: str) -> list[str]`

Build Claude CLI startup arguments. Uses `shlex.join` for safe command construction.

---

## Types

All types are importable from `cc_team`:

```python
from cc_team import (
    # Literal types
    TaskStatus,       # "pending" | "in_progress" | "completed" | "deleted"
    PermissionMode,   # "default" | "acceptEdits" | "bypassPermissions" | "plan" | "dontAsk" | "delegate"
    AgentType,        # "general-purpose" | "Explore" | "Plan" | "Bash" | "team-lead"
    BackendType,      # "tmux" | "in-process"
    AgentColor,       # "blue" | "green" | "yellow" | "purple" | "orange" | "pink" | "cyan" | "red"
    AGENT_COLORS,     # 8-color tuple for cycling assignment

    # Dataclasses
    TeamConfig,       # Team config.json top-level structure
    TeamMember,       # Team member (lead: 8 fields, teammate: 13 fields)
    InboxMessage,     # Single inbox message (from_, text, timestamp, read, summary?, color?)
    TaskFile,         # Task file (id, subject, description, status, owner, blocks, blocked_by, ...)
    SpawnAgentOptions,# Spawn configuration (name, prompt, agent_type, model, ...)
    ControllerOptions,# Controller initialization options

    # Protocol interface
    AgentController,  # DI interface between AgentHandle and Controller
)
```

### SpawnAgentOptions

```python
@dataclass
class SpawnAgentOptions:
    name: str                              # Agent name
    prompt: str                            # Initial instruction
    agent_type: str = "general-purpose"    # Agent type
    model: str = "claude-sonnet-4-6"       # LLM model ID
    plan_mode_required: bool = False       # Force plan mode
    permission_mode: PermissionMode | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
```

### ControllerOptions

```python
@dataclass
class ControllerOptions:
    team_name: str              # Team name
    description: str = ""       # Team description
    model: str = "claude-sonnet-4-6"  # Lead default model
    cwd: str = ""               # Working directory (default: os.getcwd())
    session_id: str = ""        # Lead session UUID (default: auto-generated)
```

### TeamMember

```python
@dataclass
class TeamMember:
    # Common fields (lead + teammate)
    agent_id: str       # Format: {name}@{team_name}
    name: str           # Human-readable name
    agent_type: str     # "team-lead", "general-purpose", etc.
    model: str          # LLM model ID
    joined_at: int      # Unix millisecond timestamp
    tmux_pane_id: str   # tmux pane ID (empty for lead)
    cwd: str            # Working directory
    subscriptions: list[str] = []  # Reserved, always empty

    # Teammate-only fields
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
    from_: str          # Sender name (JSON key: "from")
    text: str           # Message body or JSON string
    timestamp: str      # ISO 8601 timestamp
    read: bool = False  # Whether consumed
    summary: str | None = None
    color: AgentColor | None = None
```

### TaskFile

```python
@dataclass
class TaskFile:
    id: str                        # Auto-incrementing ID
    subject: str                   # Task title (imperative form)
    description: str               # Detailed description
    status: TaskStatus = "pending"
    active_form: str = ""          # Progress spinner text
    owner: str | None = None
    blocks: list[str] = []         # Downstream task IDs
    blocked_by: list[str] = []     # Upstream dependency IDs
    metadata: dict = {}
```

---

## Exceptions

All exceptions inherit from `CCTeamError`:

```python
from cc_team import (
    CCTeamError,            # Base exception for all cc-team errors
    NotInitializedError,    # Controller not initialized (init not called or already shutdown)
    AgentNotFoundError,     # Agent not found in team (has .agent_name attribute)
    MessageTimeoutError,    # Message receive timeout
    FileLockError,          # Lock acquisition failed (has .path, .attempts attributes)
    TmuxError,              # tmux operation failed
    SpawnError,             # Agent spawn process failed
    ProtocolError,          # Protocol format error (JSON parse failure, missing fields)
    CyclicDependencyError,  # Cyclic task dependency (has .task_id, .blocked_by attributes)
)
```

### Exception Hierarchy

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

### Notable Attributes

| Exception | Attribute | Type | Description |
|-----------|-----------|------|-------------|
| `AgentNotFoundError` | `agent_name` | `str` | Name of the missing agent |
| `FileLockError` | `path` | `str` | Path of the lock file |
| `FileLockError` | `attempts` | `int` | Number of acquisition attempts |
| `CyclicDependencyError` | `task_id` | `str` | Task that would create the cycle |
| `CyclicDependencyError` | `blocked_by` | `list[str]` | Dependencies that form the cycle |
