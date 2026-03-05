# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-03-05

### Added

- **Controller** — central orchestrator for multi-agent team lifecycle management
- **AgentHandle** — proxy object for interacting with individual agents
- **TeamManager** — team config CRUD operations (`config.json`)
- **TaskManager** — task CRUD with DAG dependency management and BFS cycle detection
- **InboxIO / InboxPoller** — inbox file I/O and async message polling
- **MessageBuilder** — structured message construction (plain, shutdown, plan, permission)
- **AsyncEventEmitter** — Node.js-style event system for reactive programming
- **EventRouter** — decoupled event routing from Controller
- **ProcessManager / TmuxManager** — agent process lifecycle via tmux
- **Context Relay** — seamless session rotation for Team Lead and agents (`team relay` / `agent relay`)
- **Bidirectional Sync** — automatic recovery of agents with corrupted `isActive` flags
- **File-level locking** — async `fcntl` wrappers with exponential backoff
- **`cct` CLI** — full command-line interface covering team, agent, task, message, and status operations
- **Skill reference** — `cct skill` outputs self-contained command reference for AI agent consumption
- **Full Claude Code protocol compatibility** — team config, task files, inbox files, naming conventions, color cycling

[0.1.0]: https://github.com/nightsailer/cc-team/releases/tag/v0.1.0
