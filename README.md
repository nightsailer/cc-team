[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/cc-team.svg)](https://pypi.org/project/cc-team/)

English | [中文](README.zh.md)

# cc-team

Python library + CLI for Claude Code multi-agent team orchestration.

Compatible with the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) native multi-agent team protocol — create teams, spawn agents, manage tasks, and exchange messages, all from Python or the command line.

## Features

- **Context Relay** — seamlessly refresh Team Lead or agent context without disrupting running teammates. One command handles session rotation, process restart, and automatic state recovery
- **Full protocol compatibility** — works seamlessly with Claude Code's native team system
- **Zero external dependencies** — only Python 3.10+ standard library
- **Async-first** — built on `asyncio` for concurrent agent orchestration
- **Dual interface** — use as a Python library or via the `cct` CLI
- **Event-driven** — Node.js-style `AsyncEventEmitter` for reactive programming
- **File-level locking** — safe concurrent access with `fcntl` async wrappers
- **Built-in skill reference** — `cct skill` outputs a self-contained command reference for AI agent consumption

## Installation

```bash
# Run directly without installing (recommended for CLI users)
uvx --from cc-team cct --help

# Or install globally with uv
uv tool install cc-team

# Or install with pip
pip install cc-team

# From source (for development)
pip install -e .
```

**Requirements:** Python 3.10+ and [tmux](https://github.com/tmux/tmux) installed on the system.

## Quick Start

### Python Library

```python
import asyncio
from cc_team import Controller, ControllerOptions, SpawnAgentOptions

async def main():
    # 1. Create controller and initialize team
    ctrl = Controller(ControllerOptions(
        team_name="my-project",
        description="Code analysis team",
    ))
    await ctrl.init()

    # 2. Listen for events
    async def on_message(agent_name, msg):
        print(f"[{agent_name}] {msg.text}")

    ctrl.on("message", on_message)

    # 3. Spawn an agent
    researcher = await ctrl.spawn(SpawnAgentOptions(
        name="researcher",
        prompt="Analyze the authentication module and report findings.",
        model="claude-sonnet-4-6",
    ))

    # 4. Send follow-up messages
    await researcher.send("Focus on security vulnerabilities.")

    # 5. Create and assign tasks
    task = await ctrl.create_task(
        subject="Security audit",
        description="Review auth module for vulnerabilities",
        owner="researcher",
    )

    # 6. Graceful shutdown
    await researcher.shutdown(reason="Analysis complete")
    await ctrl.shutdown()

asyncio.run(main())
```

### CLI (`cct`)

All `cct` commands can also be run via `uvx` without installation:

```bash
# uvx equivalent: replace `cct` with `uvx --from cc-team cct`
uvx --from cc-team cct --team-name my-project team create --description "Code analysis team"
```

```bash
# Create a team
cct --team-name my-project team create --description "Code analysis team"

# Spawn agents
cct --team-name my-project agent spawn \
  --name researcher \
  --prompt "Analyze the codebase for performance issues." \
  --model claude-sonnet-4-6

cct --team-name my-project agent spawn \
  --name writer \
  --prompt "Write documentation based on researcher findings."

# List agents
cct --team-name my-project agent list

# Manage tasks
cct --team-name my-project task create \
  --subject "Performance analysis" \
  --description "Profile and identify bottlenecks" \
  --owner researcher

cct --team-name my-project task list

# Send messages
cct --team-name my-project message send \
  --to researcher \
  --content "Focus on database queries" \
  --summary "DB query focus"

# Broadcast to all agents
cct --team-name my-project message broadcast \
  --content "Switching to phase 2" \
  --summary "Phase 2 start"

# Read inbox
cct --team-name my-project message read --agent researcher

# Check overall status
cct --team-name my-project status

# Print AI agent skill reference (no --team-name required)
cct skill
cct --json skill

# Graceful shutdown
cct --team-name my-project agent shutdown --name researcher --reason "Done"

# Force kill
cct --team-name my-project agent kill --name researcher

# Destroy team
cct --team-name my-project team destroy
```

### Session Management

```bash
# TL context exhausted? One-command relay — teammates keep working
cct --team-name my-project team relay

# Agent context exhausted? Same concept, same simplicity
cct --team-name my-project agent relay --name researcher

# Sync agent states after external disruption
cct --team-name my-project agent sync
```

All commands support `--json` for machine-readable output:

```bash
cct --team-name my-project --json task list

# Same with uvx
uvx --from cc-team cct --team-name my-project --json task list
```

## Architecture

```
cc-team/src/cc_team/
├── types.py              # Protocol data models (dataclass + Literal)
├── paths.py              # ~/.claude/ directory structure
├── exceptions.py         # Exception hierarchy (8 types)
├── _serialization.py     # JSON camelCase ↔ snake_case + atomic writes
├── filelock.py           # Async file lock (fcntl + exponential backoff)
├── team_manager.py       # config.json CRUD
├── task_manager.py       # Task CRUD + DAG dependency management
├── inbox.py              # Inbox file I/O
├── inbox_poller.py       # Async message polling
├── events.py             # AsyncEventEmitter
├── message_builder.py    # Structured message construction
├── event_router.py       # Event routing (decoupled from Controller)
├── tmux.py               # tmux session/pane management
├── process_manager.py    # Agent process lifecycle
├── agent_handle.py       # Agent proxy object
├── controller.py         # Central orchestrator
├── cli.py                # cct CLI entry point
└── _skill_doc.py         # AI agent skill reference document
```

**Layer dependencies (top → bottom):**

```
CLI (cli.py)
  └─ Orchestration (controller.py, agent_handle.py, event_router.py)
       └─ Communication (inbox_poller.py, message_builder.py, events.py)
       └─ Process (process_manager.py, tmux.py)
       └─ Storage (team_manager.py, task_manager.py, inbox.py)
            └─ Serialization (_serialization.py, filelock.py)
                 └─ Foundation (types.py, paths.py, exceptions.py)
```

## Core Concepts

### Controller

The central orchestrator that manages the full lifecycle of a multi-agent team. Inherits from `AsyncEventEmitter` for event-driven programming.

```python
from cc_team import Controller, ControllerOptions

ctrl = Controller(ControllerOptions(team_name="my-team"))
await ctrl.init()

# Controller emits these events:
# "message"              — agent sent a message
# "idle"                 — agent became idle
# "shutdown:approved"    — agent approved shutdown
# "plan:approval_request"— agent requests plan approval
# "permission:request"   — agent requests permission
# "task:completed"       — task marked as completed
# "agent:spawned"        — agent process started
# "agent:exited"         — agent process exited
# "error"                — error occurred
```

### AgentHandle

A proxy object for interacting with a single agent. Obtained from `Controller.spawn()` or `Controller.get_handle()`.

```python
handle = await ctrl.spawn(SpawnAgentOptions(
    name="worker",
    prompt="Your task here",
))

await handle.send("Follow-up instruction")
print(handle.is_running())    # True
await handle.shutdown()
```

### Task Management

Tasks support DAG dependency management with BFS cycle detection:

```python
task_a = await ctrl.create_task(subject="Research", description="...")
task_b = await ctrl.create_task(subject="Implement", description="...")

# task_b depends on task_a
await ctrl.task_manager.add_dependency(task_b.id, [task_a.id])

# List available (unblocked, unowned, pending) tasks
available = ctrl.task_manager.list_available()
```

### Context Relay

Claude Code agents have a 200k token context window. When exhausted, the session needs a fresh start — but running teammates must not be disrupted.

```python
# SDK: rotate session + broadcast to agents
new_session = await ctrl.relay()

# CLI: full relay (exit old TL + rotate + spawn new TL + auto-recover agents)
# cct --team-name my-project team relay
# cct --team-name my-project agent relay --name worker-1
```

The relay pattern preserves agent identity (name, type, model, color, inbox) while refreshing only the process and context. Bidirectional sync automatically recovers agents whose `isActive` flag was corrupted by Claude Code's internal sync.

### Low-Level Access

For direct file-system operations without the Controller:

```python
from cc_team import TeamManager, TaskManager, InboxIO, MessageBuilder

# Team operations
tm = TeamManager("my-team")
config = tm.read()

# Task operations
tasks = TaskManager("my-team")
task = await tasks.create(subject="Review code", description="...")

# Inbox operations
inbox = InboxIO("my-team", "researcher")
messages = inbox.read_unread()

# Message construction
builder = MessageBuilder("my-team")
await builder.send_plain("researcher", "Hello!", summary="Greeting")
```

## Protocol Compatibility

cc-team is fully compatible with Claude Code's native multi-agent team protocol:

- **Team config** — `~/.claude/teams/{team-name}/config.json`
- **Task files** — `~/.claude/tasks/{team-name}/{id}.json`
- **Inbox files** — `~/.claude/teams/{team-name}/{agent-name}.inbox.json`
- **Naming conventions** — camelCase for shutdown/plan messages, snake_case for permission messages
- **Color cycling** — 8 colors assigned by registration order: `AGENT_COLORS[index % 8]`

## References

This project is built based on the deep architecture analysis of Claude Code's native multi-agent team protocol:

- **Protocol Specification**: [claude-code-team-architecture](https://github.com/nightsailer/claude-code-team-architecture.git) — comprehensive reverse-engineering analysis covering team system, task system, agent communication, lifecycle management, tmux internals, and storage architecture.
- Protocol spec (English): [`docs/protocol-spec.en.md`](docs/protocol-spec.en.md)
- Protocol spec (中文): [`docs/protocol-spec.zh.md`](docs/protocol-spec.zh.md)

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run tests
PYTHONPATH=src python3 -m pytest tests/ --tb=short -q

# Lint
ruff check src/ tests/
```

## License

MIT
