"""cc-team: Python library + CLI for Claude Code multi-agent team orchestration."""

from __future__ import annotations

__version__ = "0.1.0"

# 公开 API — 用户级导入
from cc_team.agent_handle import AgentHandle
from cc_team.controller import Controller
from cc_team.events import AsyncEventEmitter
from cc_team.exceptions import (
    AgentNotFoundError,
    CCTeamError,
    CyclicDependencyError,
    FileLockError,
    MessageTimeoutError,
    NotInitializedError,
    ProtocolError,
    SpawnError,
    TmuxError,
)
from cc_team.inbox import InboxIO
from cc_team.inbox_poller import InboxPoller
from cc_team.message_builder import MessageBuilder
from cc_team.task_manager import TaskManager
from cc_team.team_manager import TeamManager
from cc_team.types import (
    AGENT_COLORS,
    AgentColor,
    AgentController,
    AgentType,
    BackendType,
    ControllerOptions,
    InboxMessage,
    PermissionMode,
    SpawnAgentOptions,
    TaskFile,
    TaskStatus,
    TeamConfig,
    TeamMember,
)

__all__ = [
    # Core
    "Controller",
    "AgentHandle",
    "AsyncEventEmitter",
    # Managers
    "TeamManager",
    "TaskManager",
    "InboxIO",
    "InboxPoller",
    "MessageBuilder",
    # Types
    "TeamConfig",
    "TeamMember",
    "InboxMessage",
    "TaskFile",
    "SpawnAgentOptions",
    "ControllerOptions",
    "AgentController",
    # Literals
    "TaskStatus",
    "PermissionMode",
    "AgentType",
    "BackendType",
    "AgentColor",
    "AGENT_COLORS",
    # Exceptions
    "CCTeamError",
    "NotInitializedError",
    "AgentNotFoundError",
    "MessageTimeoutError",
    "FileLockError",
    "TmuxError",
    "SpawnError",
    "ProtocolError",
    "CyclicDependencyError",
]
