"""Shared agent state sync logic.

Extracted to avoid triplicating the bidirectional sync algorithm across
Controller.sync_agents(), CLI ``agent sync``, and CLI ``team relay``.

The core loop checks each non-TL member's process liveness and reconciles
config.json ``isActive`` with reality:

- alive + isActive=false -> **recover**: set isActive=true
- alive + isActive=true  -> normal (already consistent)
- dead  + isActive=true  -> mark isActive=false
- dead  + isActive=false -> skip (no redundant write)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cc_team.types import TEAM_LEAD_AGENT_TYPE

if TYPE_CHECKING:
    from cc_team.team_manager import TeamManager
    from cc_team.types import AgentBackend, TeamConfig, TeamMember


@dataclass
class SyncResult:
    """Categorised output of :func:`sync_member_states`."""

    active: list[str] = field(default_factory=list)
    """Alive agents that were already marked active."""

    recovered: list[str] = field(default_factory=list)
    """Alive agents that were inactive -> recovered to active."""

    newly_inactive: list[str] = field(default_factory=list)
    """Dead agents that were active -> marked inactive."""

    members: dict[str, TeamMember] = field(default_factory=dict)
    """Map of agent name -> TeamMember for all *alive* agents (active + recovered)."""


async def sync_member_states(
    mgr: TeamManager,
    pm: AgentBackend,
    config: TeamConfig,
) -> SyncResult:
    """Bidirectional agent state sync (stateless, no handle registration).

    Callers decide how to consume the result:
    - ``Controller.sync_agents()`` registers AgentHandles for alive agents.
    - CLI commands format and display the categorised names.

    Args:
        mgr: TeamManager for reading/writing config members.
        pm: AgentBackend for checking process liveness.
        config: Snapshot of team config to iterate over.

    Returns:
        SyncResult with categorised agent names.
    """
    result = SyncResult()

    for member in config.members:
        if member.agent_type == TEAM_LEAD_AGENT_TYPE:
            continue
        if not member.tmux_pane_id:
            continue

        # Track first so is_running can resolve the backend id
        pm.track(member.name, member.tmux_pane_id)
        alive = await pm.is_running(member.name)

        if alive:
            if not member.is_active:
                await mgr.update_member(member.name, is_active=True)
                result.recovered.append(member.name)
            else:
                result.active.append(member.name)
            result.members[member.name] = member
        else:
            pm.untrack(member.name)
            if member.is_active:
                await mgr.update_member(member.name, is_active=False)
                result.newly_inactive.append(member.name)

    return result
