# cc-team Architecture Design Document v1.1 (Final)

> Version: 1.1.0 | Design Date: 2026-02-28 | Revised: 2026-02-28
> Architect: architect
> Input: Protocol specification (2088 lines) + Full team review feedback (PM decisions + senior-engineer source verification + test-engineer test review)
> Status: **Final — Pending final review confirmation**
>
> Changelog (v1.0 → v1.1):
> - Changed all "directly reuse/copy" wording to "reference design approach, implement independently"
> - BFS cycle detection changed from "deferred" to "included in MVP"
> - Controller decomposition refined (MessageBuilder + EventRouter)
> - tmux send_command full implementation strategy (load-buffer command sequence)
> - Spawn flow confirmed: write inbox before starting process (no readiness detection needed)
> - Test strategy upgraded to 4 layers (added protocol compatibility layer)
> - Added testability design (timestamp factory, runner injection, mockable paths)
> - Added P0 acceptance criteria

---

## 1. Design Principles

| Principle | Application | Anti-pattern |
|-----------|------------|-------------|
| **KISS** | Flat module structure, 16 .py files | v2.1's layered sub-packages (31 files) |
| **YAGNI** | No StorageBackend / MCP / Windows / plugin system | v2.1's StorageBackend abstraction |
| **DRY** | Serialization layer handles all naming convention mapping | — |
| **SRP** | Each Manager handles only one type of data | v0.1.0 Controller at 540 lines with mixed concerns |
| **DIP** | Protocol interface decouples handle↔controller | — |
| **Zero external dependencies** | Python 3.10+ standard library only | — |

---

## 2. Product Positioning and Scope

### 2.1 Positioning

**cc-team is a Controller/Orchestrator**: creates teams, spawns Agents, orchestrates tasks and messages.
It also exposes low-level APIs for direct filesystem operations (participant mode).

### 2.2 MVP Scope

| Must Implement | Deferred / Out of Scope |
|----------------|------------------------|
| Team CRUD (config.json) | in-process backend |
| Task CRUD + DAG dependencies + BFS cycle detection | Message subscriptions |
| 10 message types send/receive | Full Permission protocol implementation |
| tmux process management | Standalone PTY backend (see §6 rationale) |
| Agent lifecycle (spawn→idle→shutdown) | Web UI / Dashboard |
| CLI (cc-agent) | TOML/YAML config files |
| Event system | in-process backend |

---

## 3. Module Architecture

### 3.1 Directory Structure

```
cc-team/
├── pyproject.toml                    # uv + ruff config
├── src/
│   └── cc_team/
│       ├── __init__.py               # Public API exports
│       │
│       │   # === Foundation Layer (zero internal dependencies) ===
│       ├── types.py                  # Protocol data models (dataclass + Literal)
│       ├── paths.py                  # ~/.claude/ path constants
│       ├── exceptions.py             # Exception hierarchy
│       │
│       │   # === Serialization Layer ===
│       ├── _serialization.py         # JSON camelCase ↔ snake_case + atomic writes
│       │
│       │   # === Storage Layer (filesystem I/O) ===
│       ├── filelock.py               # Async file lock (fcntl)
│       ├── team_manager.py           # config.json CRUD
│       ├── task_manager.py           # Task file CRUD + DAG
│       ├── inbox.py                  # Inbox file I/O
│       │
│       │   # === Communication Layer ===
│       ├── inbox_poller.py           # Async message polling
│       ├── events.py                 # AsyncEventEmitter
│       │
│       │   # === Process Layer ===
│       ├── tmux.py                   # tmux operation wrapper
│       ├── process_manager.py        # Process lifecycle management
│       │
│       │   # === Orchestration Layer ===
│       ├── agent_handle.py           # Agent proxy object
│       ├── controller.py             # Central orchestration controller
│       │
│       │   # === CLI Layer ===
│       ├── cli.py                    # cc-agent CLI entry point
│       └── _skill_doc.py            # AI agent skill reference document
│       │
│       │   # === Context Relay Layer ===
│       ├── _relay_context.py        # RelayContext + RelayMode data models
│       ├── _relay_executor.py       # RelayExecutor protocol + TmuxExecutor
│       ├── _context_relay.py        # Low-level relay functions (standalone/lead/agent)
│       ├── _handoff_templates.py    # Per-mode handoff templates + relay prompt builder (3-level priority: env > config > default)
│       ├── _team_marker.py          # team-marker.json management
│       │
│       │   # === Plugin Hooks ===
│       ├── hooks/
│       │   ├── __init__.py
│       │   ├── _common.py           # Shared hook utilities (relay_paths, config, read_hook_input)
│       │   ├── session_start.py     # SessionStart hook (creates RelayContext, resolves member via backend_id)
│       │   ├── stop.py              # Stop hook (2-phase handoff, mode-aware)
│       │   └── statusline.py        # Context window usage monitor
│
└── tests/                            # 1:1 mapped test files
├── plugin/                          # Claude Code plugin (distributable)
│   ├── .claude-plugin/
│   │   └── plugin.json             # Plugin manifest
│   └── hooks/
│       └── hooks.json              # Hook + statusline definitions
```

### 3.2 Layered Dependency Graph

```
                  cli.py
                    │
                    ▼
            _context_relay.py ───→ process_manager, team_manager, tmux, _spawn/_sync
                    │
                    ▼
              controller.py ──────→ events.py (base class)
              /    |    \    \
             ▼     ▼     ▼    ▼
     agent_handle  │  process_manager
          │        │      │
          │        │      ▼
          │        │   tmux.py
          │        │
          ▼        ▼
    (Protocol)  team_manager
                task_manager
                inbox.py
                inbox_poller.py
                    │
                    ▼
              _serialization.py ──→ types.py
              filelock.py            paths.py
                                     exceptions.py
```

**Invariants**:
- Foundation layer (types/paths/exceptions) has zero internal dependencies
- _serialization.py depends only on types.py
- Storage layer Managers are independent of each other
- agent_handle is decoupled from controller via Protocol interface
- hooks/ package runs standalone (invoked by Claude Code plugin system), depends only on _common.py

### 3.3 Changes from v0.1.0

| Change | Rationale |
|--------|-----------|
| Added `tmux.py` | Native tmux operation wrapper (split-window, kill-pane, load-buffer) |
| `process_manager.py` refactored | Changed from PTY-only to tmux-first + PTY fallback |
| `controller.py` slimmed down | Message construction logic pushed down to individual Managers |
| Added `cli.py` | cc-agent CLI entry point |
| `inbox_poller.py` added mtime optimization | Reduces unnecessary file reads |

---

## 4. Core Data Models

### 4.1 Type Definitions (types.py)

Following the design approach of v0.1.0, here is the complete type inventory:

```python
# Enum types (Literal)
TaskStatus = Literal["pending", "in_progress", "completed", "deleted"]
PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "plan", "dontAsk", "delegate"]
AgentType = Literal["general-purpose", "Explore", "Plan", "Bash", "team-lead"]
BackendType = Literal["tmux", "in-process"]
AgentColor = Literal["blue", "green", "yellow", "purple", "orange", "pink", "cyan", "red"]

# 8-color cycling constant
AGENT_COLORS: tuple[AgentColor, ...] = ("blue", "green", "yellow", "purple", "orange", "pink", "cyan", "red")

# Configuration data models
@dataclass TeamMember       # 8 (Lead) / 13 (Teammate) fields
@dataclass TeamConfig       # 5 top-level fields + members list

# Inbox messages
@dataclass InboxMessage     # 4 required + 2 optional fields

# 9 structured message types
@dataclass PlainTextMessage
@dataclass TaskAssignmentMessage
@dataclass IdleNotificationMessage
@dataclass ShutdownRequestMessage
@dataclass ShutdownApprovedMessage
@dataclass PlanApprovalRequestMessage
@dataclass PlanApprovalResponseMessage
@dataclass PermissionRequestMessage
@dataclass PermissionResponseMessage

# Tasks
@dataclass TaskFile         # 9 fields

# Interfaces
class AgentController(Protocol)  # DI interface

# Configuration options
@dataclass ControllerOptions
@dataclass SpawnAgentOptions
```

### 4.2 Serialization Strategy (_serialization.py)

```
Python dataclass (snake_case)
        │
        ├── to_json_dict() ──→ JSON dict (camelCase)
        │     └── Special handling: permission-related fields stay snake_case
        │
        └── from_json_dict() ←── JSON dict (camelCase/snake_case)
              └── Bidirectional lookup: _JSON_TO_PYTHON mapping + fallback to original key

Atomic write: tempfile.mkstemp → json.dump → fsync → os.rename
Read retry: 3 attempts, 10ms/20ms intervals (handles empty files/corrupted JSON during concurrent writes)
```

---

## 5. Filesystem Interaction Layer

### 5.1 File Lock Strategy

| Resource | Lock Type | Lock File Path | Granularity |
|----------|-----------|---------------|-------------|
| config.json | Dedicated lock file | `config.json.lock` | Single file |
| Task files | Shared lock file | `tasks/{team}/.lock` | Directory level |
| Inbox files | Dedicated lock file | `inboxes/{agent}.json.lock` | Single file |

**Lock implementation**: `fcntl.flock(LOCK_EX | LOCK_NB)` + exponential backoff retry (5 attempts, 50ms→500ms)

### 5.2 Standard Write Flow (Three-Phase Transaction)

```python
async with lock.acquire():                    # 1. LOCK
    data = read_json(path)                    # 2. READ
    # ... validate and modify data ...        # 3. VALIDATE
    atomic_write_json(path, data)             # 4. WRITE (temp+fsync+rename)
                                              # 5. UNLOCK (auto via context manager)
```

### 5.3 Atomic Write Implementation

```python
def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))     # Atomic operation
    except BaseException:
        os.unlink(tmp_path)                # Clean up temp file
        raise
```

---

## 6. Process Management

### 6.1 PTY Wrapper Deprecation Rationale (Hard Decision)

**Conclusion: The MVP will not implement a standalone PTY backend — tmux only.**

**Rationale**:

1. **Native Claude Code supports only tmux and in-process backends**. Our PTY wrapper would be a third backend type that doesn't exist in the native protocol, adding unnecessary complexity and testing burden.

2. **tmux already provides PTY**: Each tmux pane is a PTY. An additional PTY wrapper (`python3 -c` → `pty.fork` → `claude`) adds an unnecessary layer of indirection.

3. **Process tree complexity**: The PTY wrapper results in `python → python(pty wrapper) → claude`, while the tmux approach gives `tmux → zsh → claude` — cleaner and more reliable for resource reclamation.

4. **Non-tmux environment needs**: If CI/CD or other non-tmux environments are needed in the future, this can be added in v2. YAGNI.

5. **v0.1.0's PTY wrapper was implemented but unverified**: No end-to-end tests prove its consistency with Claude Code's native behavior.

**`process_manager.py` is retained**: But its responsibility changes to coordinating tmux operations and tracking process lifecycle, no longer embedding PTY scripts.

### 6.2 tmux Operation Wrapper (tmux.py)

```python
class TmuxManager:
    """tmux operation wrapper, matching Claude Code native behavior.

    Testability: Accepts runner injection; use mock in CI instead of real tmux.
    """

    def __init__(self, *, runner: Callable | None = None):
        """runner defaults to asyncio.create_subprocess_exec; inject mock for testing."""
        self._run = runner or asyncio.create_subprocess_exec

    async def split_window(self, target_pane: str | None = None) -> str:
        """Create new pane, return pane ID (e.g., %20)."""

    SEND_KEYS_THRESHOLD = 200  # tmux/zsh inherent limit, hardcoded

    async def send_command(self, pane_id: str, text: str, *, press_enter: bool = True) -> None:
        """Send command to pane, automatically selecting the best strategy.

        Short text (<200 chars and no newlines): tmux send-keys -l (literal mode)
        Long text (>=200 chars or contains newlines): tmux load-buffer + paste-buffer
        """
        # Full command sequence for long text (verified from agent-orchestrator):
        # 1. send-keys Escape (clear partial input)
        # 2. sleep(100ms)
        # 3. Write to temp file (mode=0o600)
        # 4. load-buffer -b {named-buffer} {tmpFile} (prevent race conditions)
        # 5. paste-buffer -b {named-buffer} -d -t {pane} (-d auto-deletes)
        # 6. Clean up temp file
        # 7. sleep(appropriate delay)
        # 8. send-keys Enter (if press_enter=True)

    async def kill_pane(self, pane_id: str) -> None:
        """Destroy pane."""

    async def capture_output(self, pane_id: str, lines: int = 50) -> str:
        """Capture pane output (observability)."""

    async def is_pane_alive(self, pane_id: str) -> bool:
        """Check if pane exists."""

    @staticmethod
    def is_tmux_available() -> bool:
        """Check if running in tmux environment ($TMUX)."""
```

### 6.3 Agent Spawn Flow (v1.1 Correction: Write Inbox Before Starting Process)

```
Controller.spawn(options)
    │
    ├── 1. Assign color: AGENT_COLORS[color_index % 8]; color_index += 1
    │
    ├── 2. Build CLI args: build_cli_args(options)
    │      → ["claude", "--agent-id", ..., "--agent-name", ..., ...]
    │
    ├── 3. Create tmux pane: tmux.split_window()
    │      → Obtain pane_id (e.g., %20)
    │
    ├── 4. Register member (with pane_id) to config.json
    │      → team_manager.add_member(member)  (lock-protected + atomic write)
    │      → tmuxPaneId is written once at this step, no subsequent updates needed
    │
    ├── 5. Write initial prompt to inbox (BEFORE process starts!)
    │      → inbox.write_initial_prompt(name, prompt)
    │      → Inbox is filesystem-persisted, supports "write-before-read"
    │
    ├── 6. Send startup command to pane
    │      → tmux.send_command(pane_id, claude_command)
    │      → Uses load-buffer for long commands (>200 chars)
    │      → Does not use -p flag; Claude CLI polls inbox on its own after startup
    │
    └── 7. Create AgentHandle and return
```

**Key design decision**: The prompt is written to the inbox file (persisted) in step 5. After starting in step 6, Claude CLI locates its inbox via `--agent-name` and `--team-name` and polls for messages on its own. **No readiness detection needed** — messages won't be lost because they're persisted on the filesystem. (Both v0.1.0 and claude-code-teams-mcp use this pattern.)

### 6.4 Process Lifecycle Management (process_manager.py)

```python
class ProcessManager:
    """Process lifecycle manager.

    Responsibilities:
    - Track agent_name → pane_id mapping
    - Periodically check pane liveness
    - Graceful termination (shutdown_request → wait → force kill pane)
    - Exit callback dispatch
    """

    async def spawn(self, name: str, options: SpawnAgentOptions, ...) -> str:
        """Spawn agent via tmux, return pane_id."""

    async def kill(self, name: str) -> None:
        """Force terminate via kill-pane."""

    def is_running(self, name: str) -> bool:
        """Check if pane is alive."""

    @staticmethod
    def build_cli_args(options, ...) -> list[str]:
        """Build claude CLI arguments (protocol §C.5)."""
```

### 6.5 Context Relay Architecture

Claude Code agents have a 200k token context window. When exhausted, the session
needs a fresh start — but running teammates must not be disrupted.

**The Problem**

Native Claude Code `/clear`:
- Kills all teammate tmux panes (confirmed bug)
- Session ID changes silently, config.json not updated
- Agent state corrupted (isActive=false for alive agents)

**cct's Solution: Unified Relay**

Both TL and Teammates use the same relay pattern. The unified `cct relay --context` dispatches automatically via RelayContext:

```
┌─────────────────────────────────────────────┐
│  cct relay --context <path>                  │
│                                              │
│  1. Graceful exit (/exit → poll for exit)    │
│  2. Rotate session / Preserve identity       │
│  3. Spawn fresh process with initial prompt  │
│  4. Auto-recover agent states (sync)         │
│  5. Messages preserved (file-based inbox)    │
└─────────────────────────────────────────────┘
```

For manual process lifecycle management (no context handoff), use:
- `cct team restart` — restart team lead process
- `cct agent restart --name <n>` — restart a specific agent

Key design: agent identity (name, type, model, color, inbox) is preserved
in config.json and filesystem. Only the process and context are refreshed.

**Bidirectional sync** (`sync_agents()`):
- Alive + isActive=false → recover (fix isActive pollution)
- Alive + isActive=true → normal sync
- Dead + isActive=true → mark inactive
- Dead + isActive=false → skip (no redundant write)

**Session Boot Layer**

Two CLI commands handle session initialization:

- `cct session start` — standalone mode. Sets `CCT_RELAY_MODE=standalone` and execs claude. Relay directory and context creation are handled by the SessionStart hook, not by this command.
- `cct session start-team --team-name <name>` — team-lead mode. Validates team exists, checks for stale markers, writes team-marker.json, sets `CCT_RELAY_MODE=team-lead` + `CCT_TEAM_NAME`, then execs claude.

The SessionStart hook (`hooks/session_start.py`) runs on every Claude session start and:
1. Determines relay mode (from env vars or team-marker.json fallback)
2. For fallback teammate detection, resolves `member_name` by matching `TMUX_PANE` against team config members' backend_id
3. Creates RelayContext with session metadata
4. Auto-creates worktree team-marker if team mode confirmed by env but marker is missing

**Handoff-Based Relay (v2)**

The plugin system extends relay with automatic handoff:

1. Statusline hook tracks context usage → writes to `usage.json`
2. Stop hook detects usage > threshold → blocks stop, instructs handoff file creation
3. Agent writes handoff.md with Current Task, Completed Work, Pending Work, Key Context, Next Steps
4. Next stop → stop hook launches `cct relay --context` in background
5. Relay reads handoff, exits old session, starts new one with handoff as **initial prompt** (all modes use initial prompt injection, no tmux send-keys for handoff)

**Relay prompt 3-level priority**: The handoff content is wrapped in a relay prompt template, resolved in order: (1) `CCT_RELAY_PROMPT_TEMPLATE` env var, (2) `relay_prompt_template` key in project config (`context-relay-config.json`), (3) built-in default template.

This creates a fully automatic context rotation cycle without user intervention.

---

## 7. Communication Mechanism

### 7.1 Message Sending

```python
# Plain message: content → inbox outer text (plain text)
await inbox.write(agent_name, InboxMessage(
    from_=lead_name, text=content, timestamp=now_iso, summary=summary
))

# Structured message: JSON body → inbox outer text (JSON string)
inner_body = {"type": "shutdown_request", "requestId": ..., "from": ..., "reason": ..., "timestamp": ...}
await inbox.write(agent_name, InboxMessage(
    from_=lead_name, text=json.dumps(inner_body), timestamp=now_iso
))
```

### 7.2 Message Receiving (Polling)

```python
class InboxPoller:
    async def _poll_loop(self):
        last_mtime = 0
        while self._running:
            # mtime optimization: only read file when modification time changes
            try:
                current_mtime = inbox_path.stat().st_mtime_ns
            except FileNotFoundError:
                current_mtime = 0  # File not yet created, skip
            if current_mtime > last_mtime:
                messages = await inbox.read_unread(agent_name)
                if messages:
                    events = self._to_events(messages)
                    await self._dispatch(events)
                last_mtime = current_mtime
            await asyncio.sleep(self._interval)
```

### 7.3 Message Routing (controller._handle_poll_events)

```python
match msg_type:
    case "idle_notification":      → emit("idle", agent_name)
    case "shutdown_approved":      → emit("shutdown:approved", ...) + remove_member
    case "plan_approval_request":  → emit("plan:approval_request", ...)
    case "permission_request":     → emit("permission:request", ...)
    case "task_assignment":        → pass  # Lead doesn't process its own task_assignment
    case _:                        → emit("message", agent_name, raw_msg)
```

---

## 8. Controller Slim-Down Plan

### 8.1 Current Problem

v0.1.0 Controller at 540 lines mixes:
- Lifecycle management (init/shutdown)
- Agent management (spawn)
- Message sending (send_message/broadcast/send_shutdown_request)
- Message receiving (receive_messages)
- Task operations (create_task/assign_task)
- Protocol operations (send_plan_approval/send_permission_response)
- Event routing (_handle_poll_events)

### 8.2 Slim-Down Strategy (v1.1 Update: PM-approved decomposition into 3 sub-components)

Extract the following independent components from Controller:

1. **MessageBuilder** — Construction logic for all structured messages (shutdown_request, plan_approval, permission_response, task_assignment). Unified JSON body generation + inbox write. Estimated ~80 lines.

2. **EventRouter** — Extract message routing logic from `_handle_poll_events()`. Map the match-case dispatch into an independent class, supporting event filtering and custom routing. Estimated ~60 lines.

3. **Timestamp factory** — `now_iso()` / `now_ms()` as module-level functions in `_serialization.py` (testability requirement: monkeypatch replacement during testing).

4. **Structured message sending consolidation**: `send_shutdown_request()` / `send_plan_approval()` / `send_permission_response()` share the same pattern (construct JSON body → wrap as InboxMessage → inbox.write), consolidated into MessageBuilder's `send_structured(recipient, body)` method.

5. **Spawn logic extraction**: The 7-step spawn flow extracted into a `_do_spawn()` private method; Controller.spawn() only does parameter validation and AgentHandle creation.

**Controller retains**: init/shutdown/spawn entry point/property exposure/event emission.
**Expected result**: Controller ~300 lines.

---

## 9. CLI Design (cc-agent)

### 9.1 Subcommand Structure

```bash
cc-agent [global-options] <command> [command-options]

# Team management
cc-agent team create --name <name> [--description <desc>]
cc-agent team info [--name <name>]
cc-agent team destroy --name <name>

# Agent management
cc-agent agent spawn --name <name> --prompt <prompt> [--type <type>] [--model <model>]
cc-agent agent list
cc-agent agent status --name <name>
cc-agent agent shutdown --name <name> [--reason <reason>]
cc-agent agent kill --name <name>

# Task management
cc-agent task create --subject <subject> --description <desc> [--owner <owner>]
cc-agent task list
cc-agent task update --id <id> [--status <status>] [--owner <owner>]
cc-agent task complete --id <id>

# Messaging
cc-agent message send --to <agent> --content <content> [--summary <summary>]
cc-agent message broadcast --content <content> [--summary <summary>]
cc-agent message read [--agent <name>]

# Status
cc-agent status                    # Combined status (team + agents + tasks)

# Skill reference (no --team-name required)
cc-agent skill                     # Markdown format
cc-agent --json skill              # JSON structured format
```

### 9.2 Implementation Choice

**argparse** (zero dependencies). Rationale:
- Standard library, no extra installation
- Subcommand support (`add_subparsers`)
- Consistent with zero external dependency principle
- CLI functionality is relatively simple, no need for click's rich features

### 9.3 Global Options

```bash
--team-name <name>     # Specify team (default: auto-detect or require specification)
--json                 # JSON output format (machine-readable)
--verbose              # Verbose logging
--quiet                # Silent mode
```

---

## 10. Event System

Following v0.1.0's `AsyncEventEmitter` design, event inventory:

| Event | Parameters | Trigger Condition |
|-------|-----------|-------------------|
| `message` | (agent_name, InboxMessage) | Business message received |
| `idle` | (agent_name,) | Agent enters idle state |
| `shutdown:approved` | (agent_name, ShutdownApprovedMessage) | Agent confirms shutdown |
| `plan:approval_request` | (agent_name, PlanApprovalRequestMessage) | Plan approval request |
| `permission:request` | (agent_name, PermissionRequestMessage) | Permission request |
| `task:completed` | (TaskFile,) | Task completed |
| `agent:spawned` | (agent_name, pane_id) | Agent spawn completed |
| `agent:exited` | (agent_name, exit_code) | Agent process exited |
| `error` | (Exception,) | Error occurred |

---

## 11. Exception Hierarchy

```python
class CCTeamError(Exception):         # Base class
class NotInitializedError(CCTeamError)  # Controller not initialized
class AgentNotFoundError(CCTeamError)   # Agent not found
class MessageTimeoutError(CCTeamError)  # Message receive timeout
class FileLockError(CCTeamError)        # File lock acquisition failed
class TmuxError(CCTeamError)            # tmux operation failed
class SpawnError(CCTeamError)           # Agent spawn failed
class ProtocolError(CCTeamError)        # Protocol format error
```

---

## 12. Test Strategy (v1.1 Update: 4 Layers + Testability Design)

### 12.1 Test Layers (4 Layers)

| Layer | Tools | Coverage Target | Estimated Test Cases |
|-------|-------|----------------|---------------------|
| **Unit tests** | pytest + tmp_path | Each Manager's CRUD + serialization + business logic | ~70 |
| **Protocol compatibility tests** | pytest + golden dataset | JSON roundtrip, camelCase/snake_case mapping, 9 message formats | ~30 |
| **Integration tests** | pytest + MockTmux | Multi-Manager collaboration + file locks + Controller orchestration | ~40 |
| **E2E smoke tests** | pytest + real tmux (optional) | spawn + communication + shutdown full pipeline | ~10 |

**Total**: 125-150 test cases

**Protocol compatibility tests (new layer)**:
- Golden dataset extracted from real JSON examples in protocol specification Appendix C
- Verify all dataclass → JSON → dataclass roundtrip is lossless
- Verify requestId (camelCase) vs request_id (snake_case) correct mapping
- Verify Lead (8 fields) vs Teammate (13 fields) serialization differences
- Verify plan_approval_response asymmetric fields (approve: permissionMode / reject: feedback)

### 12.2 Testability Design (v1.1 Addition)

| Injection Point | Implementation | Test Replacement |
|-----------------|---------------|-----------------|
| `paths.claude_home()` | Module-level function | monkeypatch to redirect to tmp_path |
| `_serialization.now_iso()` / `now_ms()` | Module-level function | monkeypatch to fix timestamp |
| `TmuxManager(runner=...)` | Constructor injection | Pass mock runner |
| `Controller(process_manager=...)` | Constructor injection | Pass mock ProcessManager |
| `InboxPoller.poll_once()` | Public method | Direct invocation for single poll trigger |

### 12.3 Mock Strategy

| Scenario | Mock Approach |
|----------|--------------|
| tmux operations | TmuxManager with injected mock runner |
| Filesystem | tmp_path fixture with real file operations |
| Timestamps | monkeypatch `now_iso()` / `now_ms()` |
| Claude CLI | No real Claude needed; mock script in tmux pane |
| InboxPoller callbacks | Register async mock handler |
| ProcessManager.is_running | Replace with fixed return value |
| Concurrent writes | Multiple asyncio.Tasks operating on the same file |

### 12.4 Coverage Targets

- Unit tests + protocol compatibility: >90%
- Critical path (spawn→message→shutdown): 100%
- Edge cases (lock timeout, corrupted JSON, empty inbox): >85%

---

## 13. Project Configuration (pyproject.toml)

```toml
[project]
name = "cc-team"
version = "0.1.0"
description = "Python library + CLI for Claude Code multi-agent team orchestration"
requires-python = ">=3.10"
dependencies = []  # Zero external dependencies

[project.scripts]
cc-agent = "cc_team.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py310"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "ruff"]
```

---

## 14. Implementation Priority (Coding Phase Reference)

| Phase | Module | Estimated Lines | Dependencies |
|-------|--------|----------------|-------------|
| P0 | types.py + paths.py + exceptions.py | ~400 | None |
| P0 | _serialization.py | ~370 | types.py |
| P1 | filelock.py | ~90 | exceptions.py |
| P1 | team_manager.py + task_manager.py + inbox.py | ~550 | P0 + filelock |
| P2 | events.py + inbox_poller.py | ~200 | inbox.py |
| P2 | tmux.py | ~150 | — |
| P3 | process_manager.py | ~200 | tmux.py |
| P3 | agent_handle.py | ~120 | types.Protocol |
| P4 | controller.py + message_builder.py + event_router.py | ~440 | All of P1-P3 |
| P5 | cli.py | ~250 | controller.py |
| — | **Total** | **~2870** | — |

---

## 15. Devil's Advocate Final Review: Risks and Trade-offs

### 15.1 Accepted Risks

1. **tmux-only backend**: If the user is not in a tmux environment, cc-team won't work. This is intentional — consistent with Claude Code's native behavior.

2. **Polling latency**: The 500ms default interval means worst-case message delay of 500ms. For LLM Agent response times (typically seconds), this is acceptable.

3. **File locks are not distributed locks**: fcntl.flock only guarantees mutual exclusion on a single machine. If multiple machines share `~/.claude/` over NFS, locks may fail. This is consistent with Claude Code's native behavior (also single-machine).

### 15.2 Intentionally Omitted Features

1. **Full Permission protocol implementation**: Permission requests/responses involve UI interactions that we cannot perfectly simulate in a pure CLI. We implement bypass mode first, exposing permissions as events for user handling.

2. **Plan Mode auto-approval interception**: In the native protocol, plan approval is automatic (169-878ms), which we cannot prevent. The value of plan mode lies in the "plan before execute" workflow constraint, not human approval gating.

### 15.3 Resolved Open Issues (v1.1 Update)

1. **~~Spawn readiness detection~~** → **Resolved**: Not needed. Inbox is filesystem-persisted, supporting "write-before-read". Correct flow: register config → write inbox → start process. (Confirmed by senior-engineer source verification.)

2. **~~tmux load-buffer 200-char threshold~~** → **Resolved**: Hardcoded at 200 characters, an inherent tmux/zsh limitation. Source: agent-orchestrator tmux.ts L135 + runtime-tmux L60. (Confirmed by senior-engineer source verification.)

3. **config.json concurrent write contention**: Still exists. We protect our own writes with file locks, but native Claude Code processes (which don't use our locks) may write simultaneously. This is a protocol-level limitation, consistent with native behavior.

### 15.4 BFS Cycle Detection (v1.1: Changed from "Deferred" to "Included in MVP")

**Rationale** (senior-engineer argument):
- Protocol says DAG but has no cycle prevention mechanism
- No detection = deadlock (tasks can never be claimed) + silent failure
- Implementation is only ~22 lines of BFS, O(V+E)

**Implementation location**: In `task_manager.py`'s `add_dependency()` method, validated before writing.

```python
def _would_create_cycle(self, task_id: str, blocked_by_ids: list[str]) -> bool:
    """BFS to detect if adding dependencies would create a cycle."""
    # Starting from blocked_by_ids, BFS traverse the blockedBy chain
    # If task_id is reachable, a cycle would be formed
    visited = set()
    queue = list(blocked_by_ids)
    while queue:
        current = queue.pop(0)
        if current == task_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        task = self._read_task(current)
        if task:
            queue.extend(task.blocked_by)
    return False
```

### 15.5 P0 Acceptance Criteria (v1.1 Addition)

| Acceptance Item | Specific Requirements |
|----------------|----------------------|
| Team CRUD | create/read/destroy + color assignment (8-color cycle, internal counter) |
| Task CRUD | create/read/update/delete + state machine + DAG bidirectional links + BFS cycle detection |
| Message format | 9 structured message types + requestId/request_id naming compatibility |
| Agent lifecycle | spawn → active → idle → shutdown(approve/reject) |
| Data integrity | Atomic writes (temp+fsync+rename) + file lock concurrency safety |
| Serialization roundtrip | All dataclass ↔ JSON lossless roundtrip |
| CLI basics | cc-agent team/agent/task/message subcommands operational |

---

## Appendix A: v0.1.0 Design Reference Evaluation

| Module | Lines | Reference Value | Independent Implementation Strategy |
|--------|-------|----------------|-------------------------------------|
| types.py | 317 | 9/10 | Reference design, implement independently |
| _serialization.py | 371 | 8/10 | Reference design, implement independently |
| paths.py | 58 | 10/10 | Reference design, implement independently |
| filelock.py | 88 | 8/10 | Reference design, implement independently |
| team_manager.py | 188 | 8/10 | Reference design, add tmux pane ID management |
| task_manager.py | 228 | 8/10 | Reference design, verify DAG logic correctness |
| inbox.py | 126 | 8/10 | Reference design, add write_task_assignment |
| inbox_poller.py | 117 | 7/10 | Reference design, add mtime optimization |
| events.py | 86 | 9/10 | Reference design, potentially streamline |
| agent_handle.py | 114 | 8/10 | Reference design |
| process_manager.py | 257 | 5/10 | Redesign for tmux backend |
| controller.py | 540 | 6/10 | Reference design, needs slimming |

**Hard constraint**: All code is written independently. "Reference design approach" means understanding the WHY, then closing the reference and writing from scratch. Copying code files or code snippets is prohibited.
