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
- [Context Relay](#context-relay)
- [Hooks](#hooks)
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
| `process_manager` | `AgentBackend \| None` | Optional custom backend instance (for testing/DI) |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `team_name` | `str` | Team name |
| `session_id` | `str` | Lead agent session UUID |
| `team_manager` | `TeamManager` | Access to team config manager |
| `task_manager` | `TaskManager` | Access to task manager |
| `process_manager` | `AgentBackend` | Access to process manager (AgentBackend protocol) |

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

#### `async attach() -> None`

Attach to an existing team (without creating a new one). Used for takeover scenarios: connects to an existing team, recovers agent handles by verifying pane liveness via `sync_agents()`, and starts inbox polling.

```python
ctrl = Controller(ControllerOptions(team_name="existing-team"))
await ctrl.attach()
```

#### `async relay() -> str`

Context relay: rotate session ID, broadcast `session_relay` to all active agents, restart poller. Returns the new session ID. The caller is responsible for stopping/restarting the TL process.

```python
new_session_id = await ctrl.relay()
```

#### `async sync_agents() -> tuple[list[AgentHandle], list[str]]`

Bidirectional agent state sync. For each non-TL member with a backend_id in config.json:
- Alive + isActive=false → **recover**: set isActive=true, register handle
- Alive + isActive=true → normal sync, register handle
- Dead + isActive=true → mark isActive=false
- Dead + isActive=false → skip (no redundant write)

Returns `(synced_handles, recovered_names)`. Called automatically during `attach()`.

```python
synced, recovered = await ctrl.sync_agents()
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
| `session:relayed` | `(new_session_id: str)` | Session rotated via relay |
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

#### `list_teammates() -> list[TeamMember]`

Return members excluding team-lead.

#### `async add_member(member: TeamMember) -> None`

Add a member. Raises `ValueError` on duplicate name.

#### `async register_member(*, name: str, agent_type: str = "general-purpose", model: str = "claude-sonnet-4-6", cwd: str = "", plan_mode_required: bool = False, backend_type: BackendType | None = None) -> TeamMember`

Register a member to config.json + create an empty inbox, without starting a process. Color allocation and member insertion are atomic (single lock). Returns the registered `TeamMember` with `is_active=False`.

```python
member = await mgr.register_member(name="worker", backend_type="tmux")
```

#### `next_color(config: TeamConfig | None = None) -> AgentColor`

Allocate the next color (8-color cycling based on member count). Pass an existing config to avoid redundant I/O.

#### `async remove_member(name: str) -> None`

Remove a member. Raises `AgentNotFoundError` if not found.

#### `async update_member(name: str, **updates) -> TeamMember`

Update member fields. Raises `AgentNotFoundError` if not found.

#### `get_lead_session_id() -> str | None`

Get the current team lead session ID. Returns `None` if config not found.

#### `async set_lead_session_id(session_id: str) -> None`

Set the team lead session ID.

#### `async rotate_session(new_session_id: str | None = None) -> str`

Rotate the lead session ID. Generates a new UUID4 (or uses the provided ID). Returns the new session ID.

```python
new_sid = await mgr.rotate_session()
```

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

#### `async ensure_exists() -> None`

Create an empty inbox file if it doesn't exist. Used by `register_member()` to pre-create the inbox without writing a message.

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

#### `async send_session_relay(recipients: list[str], *, new_session_id: str, previous_session_id: str) -> None`

Broadcast a `session_relay` structured message to multiple agents in parallel. Used by `Controller.relay()`.

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

#### `async spawn_lead(options: SpawnLeadOptions, *, parent_session_id: str) -> str`

Start a Team Lead process in a tmux pane. Supports pane reuse (`options.pane_id`) for relay scenarios. Returns the pane ID.

#### `track(agent_name: str, pane_id: str) -> None`

Register an existing agent into the tracking list (used for attach/sync scenarios).

#### `async send_input(agent_name: str, text: str) -> None`

Send input text to an agent's tmux pane. Raises `AgentNotFoundError` if not tracked.

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

Build Claude CLI startup arguments for agents. Uses `shlex.join` for safe command construction.

#### `@staticmethod build_lead_cli_args(options: SpawnLeadOptions, *, parent_session_id: str) -> list[str]`

Build Claude CLI startup arguments for Team Lead. Includes `--session-id`, no `--agent-color`.

#### `async graceful_exit(backend_id: str, *, timeout: int = 30) -> None`

Send `/exit` to a tmux pane and poll until the pane dies. Raises `TimeoutError` if the pane does not exit within the timeout.

```python
await pm.graceful_exit("%42", timeout=30)
```

#### `async detect_ready(backend_id: str, *, timeout: int = 60) -> bool`

Poll `detect_state` until READY/WAITING_INPUT/IDLE or timeout. Returns `True` if a ready-like state was detected, `False` on timeout.

```python
is_ready = await pm.detect_ready("%42", timeout=60)
```

---

## Context Relay

Context relay module for rotating Claude Code sessions with handoff context injection.

```python
from cc_team._context_relay import RelayRequest, RelayResult, relay_standalone, relay_lead, relay_agent
```

### RelayRequest

```python
@dataclass
class RelayRequest:
    cct_session_id: str      # CCT session tracking ID
    handoff_path: str        # Path to handoff file
    model: str = "claude-sonnet-4-6"
    timeout: int = 30        # Graceful exit timeout (seconds)
    cwd: str = ""            # Working directory
```

### RelayResult

```python
@dataclass
class RelayResult:
    old_backend_id: str | None   # Previous tmux pane ID
    new_backend_id: str          # New tmux pane ID
    cct_session_id: str          # CCT session tracking ID
    handoff_injected: bool = False  # Whether handoff content was injected
```

### Functions

#### `async relay_standalone(request, backend, backend_id, tmux) -> RelayResult`

Context relay for a standalone Claude process (no team). Steps:
1. Graceful exit old session
2. Build new claude command and send to same pane
3. Wait for readiness + inject handoff content
4. Update history

#### `async relay_lead(request, team_name) -> RelayResult`

Context relay for team lead. Steps:
1. Graceful exit old TL
2. Rotate session
3. Spawn new TL (reuse same pane)
4. Inject handoff via TmuxManager
5. Sync member states
6. Update history

#### `async relay_agent(request, team_name, agent_name) -> RelayResult`

Context relay for a teammate. Steps:
1. Graceful exit agent
2. Remove member from config
3. Respawn via spawn_agent_workflow with handoff as prompt
4. Update history

---

## Hooks

Claude Code plugin hooks for automatic context relay.

### Stop Hook (`cc_team.hooks.stop`)

Two-phase handoff mechanism:

- **Phase 1** (no handoff file): When context usage exceeds threshold, blocks stop with instructions to write a handoff file
- **Phase 2** (handoff exists): Launches relay in background and allows stop

```bash
# Invoked automatically by Claude Code plugin system
cct _hook stop
```

Requires `CCT_SESSION_ID` environment variable. Skips execution for subagents.

### Statusline Hook (`cc_team.hooks.statusline`)

Renders a colored progress bar showing context window usage:

```bash
# Reads JSON from stdin, outputs formatted status line
cct _hook statusline
```

Output format: `[agent] ████░░░░ 45.2% | 90k/200k | $0.150 | claude-sonnet-4-6`

Colors: green (<60%), yellow (60-80%), red (>80%).

When `CCT_SESSION_ID` is set, persists usage data to `relay_paths()/usage.json`.

### Common Utilities (`cc_team.hooks._common`)

| Function | Description |
|----------|-------------|
| `project_dir()` | CLAUDE_PROJECT_DIR with cwd fallback |
| `read_json(path)` | Read JSON file, return `{}` on error |
| `write_json(path, data)` | Write dict as JSON with mkdir |
| `atomic_write_json(path, data)` | Atomic write (tmp + rename) |
| `load_config(proj)` | Read context-relay-config.json |
| `cct_data_dir(proj)` | CCT project data directory |
| `relay_paths(cct_session_id, proj)` | Per-session relay file paths |

---

## Types

All types are importable from `cc_team`:

```python
from cc_team import (
    # Literal types
    TaskStatus,       # "pending" | "in_progress" | "completed" | "deleted"
    PermissionMode,   # "default" | "acceptEdits" | "bypassPermissions" | "plan" | "dontAsk" | "delegate"
    AgentType,        # "general-purpose" | "Explore" | "Plan" | "Bash" | "team-lead"
    BackendType,      # "tmux" | "in-process" | "agent-sdk"
    TMUX_BACKEND,     # BackendType constant: "tmux"
    AgentColor,       # "blue" | "green" | "yellow" | "purple" | "orange" | "pink" | "cyan" | "red"
    AGENT_COLORS,     # 8-color tuple for cycling assignment
    MessageType,      # Literal union of all structured message type strings

    # Dataclasses
    TeamConfig,       # Team config.json top-level structure
    TeamMember,       # Team member (lead: 8 fields, teammate: 13 fields)
    InboxMessage,     # Single inbox message (from_, text, timestamp, read, summary?, color?)
    TaskFile,         # Task file (id, subject, description, status, owner, blocks, blocked_by, ...)
    SpawnAgentOptions,# Spawn configuration (name, prompt, agent_type, model, ...)
    SpawnLeadOptions, # TL spawn configuration (team_name, session_id, model, pane_id?)
    ControllerOptions,# Controller initialization options

    # Structured message bodies
    SessionRelayMessage,        # session_relay (from_, new_session_id, previous_session_id)
    TaskAssignmentMessage,      # task_assignment
    ShutdownRequestMessage,     # shutdown_request
    ShutdownApprovedMessage,    # shutdown_approved
    PlanApprovalRequestMessage, # plan_approval_request
    PlanApprovalResponseMessage,# plan_approval_response
    PermissionRequestMessage,   # permission_request
    PermissionResponseMessage,  # permission_response
    IdleNotificationMessage,    # idle_notification

    # Protocol interfaces
    AgentController,  # DI interface between AgentHandle and Controller
    AgentBackend,     # Backend abstraction for process lifecycle (tmux, SDK, etc.)
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

### SpawnLeadOptions

```python
@dataclass
class SpawnLeadOptions:
    team_name: str              # Team name
    session_id: str             # Lead session UUID
    model: str = "claude-sonnet-4-6"  # LLM model ID
    cwd: str = ""               # Working directory (default: os.getcwd())
    permission_mode: PermissionMode | None = None
    pane_id: str | None = None  # Reuse existing pane (relay scenario)
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
    backend_id: str    # backend-specific process identifier (empty for lead)
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

### SessionRelayMessage

```python
@dataclass
class SessionRelayMessage:
    from_: str                  # Sender (JSON: "from")
    new_session_id: str         # New session ID (JSON: newSessionId)
    previous_session_id: str    # Old session ID (JSON: previousSessionId)
    timestamp: str              # ISO 8601
```

### AgentBackend (Protocol)

Backend abstraction for agent process lifecycle. Implementations: `ProcessManager` (tmux), future SDK backends.

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

## CLI Reference (Session Management)

### `cct team relay`

Context relay for Team Lead: exit old TL, rotate session, spawn new TL, and auto-recover agent states.

```bash
cct --team-name <name> team relay [--model <model>] [--timeout <seconds>]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--model` | `claude-sonnet-4-6` | Model for new TL |
| `--timeout` | `30` | Exit wait timeout in seconds |
| `--handoff` | — | Path to handoff file for context injection |

**Output** (JSON): `old_session`, `new_session`, `old_backend_id`, `new_backend_id`, `agents.synced`, `agents.recovered`, `agents.inactive`

### `cct agent relay`

Context relay for a teammate: exit old process, respawn with fresh context.

```bash
cct --team-name <name> agent relay --name <agent> [--prompt <new-prompt>] [--timeout <seconds>]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--name` | (required) | Agent name |
| `--prompt` | (reuse original) | New prompt for the agent |
| `--timeout` | `30` | Exit wait timeout in seconds |
| `--handoff` | — | Path to handoff file for context injection |

**Output** (JSON): `name`, `old_backend_id`, `new_backend_id`, `prompt`, `color`

### `cct agent sync`

Bidirectional agent state sync: verify process liveness, recover inactive-but-alive agents, mark dead agents.

```bash
cct --team-name <name> agent sync
```

**Output** (JSON): `synced`, `recovered`, `inactive`

### `cct relay`

Standalone context relay (no team). Requires `CCT_SESSION_ID` environment variable.

```bash
cct relay --handoff <path> --backend-id <pane-id> [--model <model>] [--timeout <seconds>]
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--handoff` | (required) | Path to handoff file |
| `--backend-id` | (required) | Target tmux pane ID |
| `--model` | `claude-sonnet-4-6` | Model for new session |
| `--timeout` | `30` | Exit wait timeout in seconds |

### `cct setup`

Show plugin path or install symlink.

```bash
cct setup [--install]
```

| Parameter | Description |
|-----------|-------------|
| `--install` | Create symlink at `~/.claude/plugins/cc-team` |

### `cct session start`

Start a new Claude session with `CCT_SESSION_ID` environment variable set.

```bash
cct session start [-- <claude-args>...]
```

Creates relay directory structure and replaces the current process with `claude`.

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
    TeamAlreadyExistsError, # Team already exists (raised by TeamManager.create)
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
    ├── CyclicDependencyError
    └── TeamAlreadyExistsError
```

### Notable Attributes

| Exception | Attribute | Type | Description |
|-----------|-----------|------|-------------|
| `AgentNotFoundError` | `agent_name` | `str` | Name of the missing agent |
| `FileLockError` | `path` | `str` | Path of the lock file |
| `FileLockError` | `attempts` | `int` | Number of acquisition attempts |
| `CyclicDependencyError` | `task_id` | `str` | Task that would create the cycle |
| `CyclicDependencyError` | `blocked_by` | `list[str]` | Dependencies that form the cycle |
