"""Pluggable relay executor architecture.

Defines a RelayExecutor protocol and provides TmuxExecutor as the current
implementation. The executor is selected by backend_type from RelayContext.
"""

from __future__ import annotations

from typing import Protocol

from cc_team._context_relay import (
    RelayRequest,
    RelayResult,
    _read_handoff,
    _update_history,
    relay_agent,
    relay_lead,
)
from cc_team._handoff_templates import get_relay_prompt
from cc_team._relay_context import RelayContext, RelayMode
from cc_team.process_manager import ProcessManager, _build_spawn_command, _find_claude_binary
from cc_team.tmux import TmuxManager


class RelayExecutor(Protocol):
    """Protocol for relay execution backends."""

    async def execute(self, context: RelayContext, request: RelayRequest) -> RelayResult:
        """Execute a relay operation for the given context and request."""
        ...


class TmuxExecutor:
    """Tmux-based relay executor.

    Dispatches to mode-specific relay methods based on RelayContext.mode.
    """

    async def execute(self, context: RelayContext, request: RelayRequest) -> RelayResult:
        """Execute relay based on mode: standalone, team-lead, or teammate."""
        if context.mode == RelayMode.STANDALONE:
            return await self._relay_standalone(context, request)
        elif context.mode == RelayMode.TEAM_LEAD:
            return await self._relay_lead(context, request)
        else:
            return await self._relay_agent(context, request)

    async def _relay_standalone(
        self,
        context: RelayContext,
        request: RelayRequest,
    ) -> RelayResult:
        """Standalone relay: exit old → start new with handoff as initial prompt."""
        handoff_text = _read_handoff(request.handoff_path)
        prompt = get_relay_prompt(handoff_text, source_path=request.handoff_path)

        tmux = TmuxManager()
        pm = ProcessManager(tmux=tmux)

        backend_id = context.backend_id
        if not backend_id:
            raise ValueError("No backend_id in RelayContext for standalone relay")

        # 1. Graceful exit
        await pm.graceful_exit(backend_id, timeout=request.timeout)

        # 2. Build and launch new claude with handoff as initial prompt (-p flag)
        cli_args = [_find_claude_binary(), "--model", request.model, "-p", prompt]
        cwd = request.cwd or context.project_dir
        relay_env = {"CCT_RELAY_MODE": context.mode.value}
        command = _build_spawn_command(cwd, cli_args, relay_env=relay_env)
        await tmux.send_command(backend_id, command)

        # 3. Update history
        if context.session_id:
            _update_history(context.session_id, None)

        return RelayResult(
            old_backend_id=backend_id,
            new_backend_id=backend_id,
            session_id=context.session_id,
            handoff_injected=True,
        )

    async def _relay_lead(
        self,
        context: RelayContext,
        request: RelayRequest,
    ) -> RelayResult:
        """Team lead relay: delegates to existing relay_lead function."""
        if not context.team_name:
            raise ValueError("No team_name in RelayContext for team-lead relay")
        return await relay_lead(request, context.team_name, session_id=context.session_id)

    async def _relay_agent(
        self,
        context: RelayContext,
        request: RelayRequest,
    ) -> RelayResult:
        """Teammate relay: delegates to existing relay_agent function."""
        if not context.team_name or not context.member_name:
            raise ValueError("team_name and member_name required for teammate relay")
        return await relay_agent(
            request, context.team_name, context.member_name, session_id=context.session_id
        )


# ── Registry ──────────────────────────────────────────────

_EXECUTORS: dict[str, type[RelayExecutor]] = {
    "tmux": TmuxExecutor,
}


def get_executor(backend_type: str) -> RelayExecutor:
    """Get a relay executor by backend type.

    Raises:
        ValueError: Unknown backend type.
    """
    cls = _EXECUTORS.get(backend_type)
    if cls is None:
        raise ValueError(f"Unknown backend type: {backend_type!r}")
    return cls()
