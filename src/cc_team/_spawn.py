"""Shared spawn orchestration.

Extracts the 4-step agent spawn workflow so both CLI and Controller
call the same sequence:

1. Register member (allocate color + config.json + empty inbox)
2. Write initial prompt to inbox
3. Start backend process (with rollback on failure)
4. Activate: mark active + save prompt + update backend_id (single write)

Any fix to the spawn sequence is automatically applied everywhere.
"""

from __future__ import annotations

import contextlib

from cc_team.inbox import InboxIO
from cc_team.team_manager import TeamManager
from cc_team.types import (
    TEAM_LEAD_AGENT_TYPE,
    TMUX_BACKEND,
    AgentBackend,
    AgentColor,
    SpawnAgentOptions,
)


async def spawn_agent_workflow(
    team_manager: TeamManager,
    backend: AgentBackend,
    options: SpawnAgentOptions,
    *,
    team_name: str,
    cwd: str,
    lead_session_id: str,
) -> tuple[str, AgentColor | None]:
    """Execute the 5-step agent spawn workflow.

    Args:
        team_manager: Team config manager
        backend: Process backend (e.g. ProcessManager)
        options: Agent spawn configuration
        team_name: Team name for namespacing
        cwd: Effective working directory
        lead_session_id: Team Lead's session ID

    Returns:
        (backend_id, color) tuple

    Raises:
        SpawnError: if backend process creation fails (member is rolled back)
    """
    # 1. Register member (allocate color + config.json + empty inbox)
    member = await team_manager.register_member(
        name=options.name,
        agent_type=options.agent_type,
        model=options.model,
        cwd=cwd,
        plan_mode_required=options.plan_mode_required,
        backend_type=TMUX_BACKEND,
    )
    color = member.color

    # 2. Write initial prompt to inbox
    inbox = InboxIO(team_name, options.name)
    await inbox.write_initial_prompt(TEAM_LEAD_AGENT_TYPE, options.prompt)

    # 3. Start backend process (rollback on failure)
    try:
        backend_id = await backend.spawn(
            options,
            team_name=team_name,
            color=color or "",
            parent_session_id=lead_session_id,
        )
    except Exception:
        with contextlib.suppress(Exception):
            await team_manager.remove_member(options.name)
        raise

    # 4. Activate: mark active + save prompt + backend_id (single write)
    await team_manager.update_member(
        options.name,
        is_active=True,
        prompt=options.prompt,
        tmux_pane_id=backend_id,
    )

    return backend_id, color
