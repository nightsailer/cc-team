"""Context relay: rotate Claude Code sessions with handoff as initial prompt.

Provides two relay entry points:
- relay_lead: team lead session rotation with agent sync
- relay_agent: teammate context relay (exit + respawn)

Each reads a handoff file, gracefully exits the old process, and spawns a new
one with the handoff content as the initial prompt.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from cc_team._spawn import spawn_agent_workflow
from cc_team._sync import sync_member_states
from cc_team.process_manager import ProcessManager
from cc_team.team_manager import TeamManager
from cc_team.tmux import TmuxManager
from cc_team.types import (
    DEFAULT_MODEL,
    TEAM_LEAD_AGENT_TYPE,
    SpawnAgentOptions,
    SpawnLeadOptions,
)

# ── Data classes ────────────────────────────────────────


@dataclass
class RelayRequest:
    """Input parameters for a context relay operation."""

    handoff_path: str
    model: str = DEFAULT_MODEL
    timeout: int = 30
    cwd: str = ""


@dataclass
class RelayResult:
    """Output of a context relay operation."""

    old_backend_id: str | None
    new_backend_id: str
    session_id: str
    handoff_injected: bool = False


# ── Shared helpers ──────────────────────────────────────


def _read_handoff(path: str) -> str:
    """Read handoff file content.

    Raises:
        FileNotFoundError: handoff file does not exist.
    """
    return Path(path).read_text(encoding="utf-8")


def _update_history(
    session_id: str,
    new_cc_session_id: str | None,
    proj: str | None = None,
) -> None:
    """Append a relay entry to history.json.

    History file lives at ~/.claude/teams/{proj}/history.json if proj is given,
    otherwise at ~/.claude/relay-history.json.
    """
    from cc_team import paths

    if proj:
        history_path = paths.team_dir(proj) / "history.json"
    else:
        history_path = paths.claude_home() / "relay-history.json"

    history_path.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    if history_path.exists():
        try:
            entries = json.loads(history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []

    entries.append(
        {
            "session_id": session_id,
            "new_cc_session_id": new_cc_session_id,
            "timestamp": int(time.time() * 1000),
        }
    )

    history_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Entry points ────────────────────────────────────────


async def relay_lead(
    request: RelayRequest,
    team_name: str,
    *,
    session_id: str = "",
) -> RelayResult:
    """Context relay for team lead: exit + rotate session + respawn with initial prompt.

    Steps:
    1. Graceful exit old TL
    2. Rotate session
    3. Spawn new TL with handoff as initial prompt via spawn_lead
    4. Sync member states
    5. Update history
    """
    from cc_team._handoff_templates import get_relay_prompt

    handoff_text = _read_handoff(request.handoff_path)
    formatted = get_relay_prompt(handoff_text, source_path=request.handoff_path)

    tmux = TmuxManager()
    pm = ProcessManager(tmux=tmux)
    mgr = TeamManager(team_name)

    # Find TL backend_id
    config = mgr.read()
    if config is None:
        raise FileNotFoundError(f"Team '{team_name}' not found")

    lead = next(
        (m for m in config.members if m.name == TEAM_LEAD_AGENT_TYPE),
        None,
    )
    old_backend_id = lead.backend_id if lead else None

    # 1. Graceful exit
    if old_backend_id:
        await pm.graceful_exit(old_backend_id, timeout=request.timeout)

    # 2. Rotate session
    new_sid = await mgr.rotate_session()

    # 3. Spawn new TL with handoff as initial prompt
    options = SpawnLeadOptions(
        team_name=team_name,
        session_id=new_sid,
        model=request.model,
        cwd=request.cwd or os.getcwd(),
        backend_id=old_backend_id if old_backend_id else None,
        prompt=formatted,
    )
    new_backend_id = await pm.spawn_lead(options, parent_session_id=new_sid)
    await mgr.update_member(TEAM_LEAD_AGENT_TYPE, backend_id=new_backend_id)

    # 4. Sync member states
    fresh_config = mgr.read()
    if fresh_config:
        await sync_member_states(mgr, pm, fresh_config)

    # 5. Update history
    if session_id:
        _update_history(session_id, new_sid, proj=team_name)

    return RelayResult(
        old_backend_id=old_backend_id,
        new_backend_id=new_backend_id,
        session_id=session_id,
        handoff_injected=True,
    )


async def relay_agent(
    request: RelayRequest,
    team_name: str,
    agent_name: str,
    *,
    session_id: str = "",
) -> RelayResult:
    """Context relay for a teammate: exit + remove + respawn with handoff.

    Steps:
    1. Graceful exit agent
    2. Remove member from config
    3. Respawn via spawn_agent_workflow with handoff as prompt
    4. Update history
    """
    from cc_team._handoff_templates import get_relay_prompt

    handoff_text = _read_handoff(request.handoff_path)
    formatted = get_relay_prompt(handoff_text, source_path=request.handoff_path)

    tmux = TmuxManager()
    pm = ProcessManager(tmux=tmux)
    mgr = TeamManager(team_name)

    # Look up member
    member = mgr.get_member(agent_name)
    if member is None:
        raise ValueError(f"Agent '{agent_name}' not found in team '{team_name}'")

    old_backend_id = member.backend_id

    # 1. Graceful exit
    if old_backend_id:
        await pm.graceful_exit(old_backend_id, timeout=request.timeout)

    # 2. Remove old member
    await mgr.remove_member(agent_name)

    # 3. Respawn with handoff as prompt
    config = mgr.read()
    if config is None:
        raise FileNotFoundError(f"Team '{team_name}' not found after removal")

    options = SpawnAgentOptions(
        name=member.name,
        prompt=formatted,
        agent_type=member.agent_type,
        model=request.model or member.model,
        cwd=member.cwd or request.cwd or os.getcwd(),
    )

    new_backend_id, _ = await spawn_agent_workflow(
        mgr,
        pm,
        options,
        team_name=team_name,
        cwd=options.cwd,
        lead_session_id=config.lead_session_id,
    )

    # 4. Update history
    if session_id:
        _update_history(session_id, None, proj=team_name)

    return RelayResult(
        old_backend_id=old_backend_id,
        new_backend_id=new_backend_id,
        session_id=session_id,
        handoff_injected=True,  # prompt itself is the handoff
    )
