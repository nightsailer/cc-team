"""cct: Claude Code 多 Agent 团队编排 CLI。

基于 argparse（零外部依赖），提供子命令:
- team: 团队管理 (create/info/destroy)
- agent: Agent 管理 (spawn/list/status/shutdown/kill)
- task: 任务管理 (create/list/update/complete)
- message: 消息 (send/broadcast/read)
- status: 综合状态
- skill: AI 智能体技能参考文档

用法:
    cct --team-name my-team team create --description "My project"
    cct --team-name my-team agent spawn --name researcher --prompt "Analyze code"
    cct --team-name my-team task list
    cct --team-name my-team status
    cct skill
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import pathlib
import sys
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from cc_team.types import DEFAULT_MODEL

if TYPE_CHECKING:
    from cc_team._context_relay import RelayRequest, RelayResult
    from cc_team.team_manager import TeamManager
    from cc_team.types import TeamConfig, TeamMember

# ── 输出辅助 ──────────────────────────────────────────────


def _json_out(data: Any) -> None:
    """JSON 格式输出到 stdout。"""
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _error(msg: str) -> None:
    """输出错误到 stderr。"""
    print(f"Error: {msg}", file=sys.stderr)


def _require_team(args: argparse.Namespace) -> str:
    """获取并验证 --team-name，缺失时报错退出。"""
    name = getattr(args, "team_name", None)
    if not name:
        _error("--team-name is required")
        sys.exit(1)
    return name


def _require_member(team: str, name: str) -> TeamMember:
    """Validate that *name* is a registered member of *team*, exit 1 if not."""
    from cc_team.team_manager import TeamManager

    mgr = TeamManager(team)
    member = mgr.get_member(name)
    if member is None:
        _error(f"Member '{name}' not found in team '{team}'")
        sys.exit(1)
    return member


# ── team 子命令 ───────────────────────────────────────────


async def _cmd_team_create(args: argparse.Namespace) -> None:
    from cc_team.exceptions import TeamAlreadyExistsError
    from cc_team.team_manager import TeamManager

    name = args.name or _require_team(args)
    mgr = TeamManager(name)
    try:
        config = await mgr.create(
            description=args.description,
            lead_session_id=str(uuid.uuid4()),
            cwd=os.getcwd(),
        )
    except TeamAlreadyExistsError:
        _error(f"Team '{name}' already exists. Destroy it first or use a different name.")
        sys.exit(1)
    if args.use_json:
        from cc_team._serialization import team_config_to_dict

        _json_out(team_config_to_dict(config))
    else:
        print(f"Team '{config.name}' created.")


async def _cmd_team_info(args: argparse.Namespace) -> None:
    from cc_team.team_manager import TeamManager

    name = _require_team(args)
    mgr = TeamManager(name)
    config = mgr.read()
    if config is None:
        _error(f"Team '{name}' not found")
        sys.exit(1)
    if args.use_json:
        from cc_team._serialization import team_config_to_dict

        _json_out(team_config_to_dict(config))
    else:
        print(f"Team: {config.name}")
        print(f"Description: {config.description}")
        print(f"Members ({len(config.members)}):")
        for m in config.members:
            color = f" [{m.color}]" if m.color else ""
            active = " (active)" if m.is_active else ""
            print(f"  - {m.name} ({m.agent_type}){color}{active}")


async def _cmd_team_destroy(args: argparse.Namespace) -> None:
    from cc_team.team_manager import TeamManager

    name = _require_team(args)
    mgr = TeamManager(name)
    await mgr.destroy()
    if not args.quiet:
        print(f"Team '{name}' destroyed.")


async def _require_config(name: str) -> tuple[TeamManager, TeamConfig]:
    """读取并验证团队配置，返回 (TeamManager, TeamConfig)。不存在时 exit(1)。"""
    from cc_team.team_manager import TeamManager as _TM

    mgr = _TM(name)
    config = mgr.read()
    if config is None:
        _error(f"Team '{name}' not found.")
        sys.exit(1)
    return mgr, config


async def _spawn_new_lead(
    mgr: TeamManager,
    name: str,
    model: str,
    backend_id: str | None = None,
) -> tuple[str, str]:
    """Rotate session + spawn new TL + update member. Returns (backend_id, session_id)."""
    from cc_team.process_manager import ProcessManager
    from cc_team.types import TEAM_LEAD_AGENT_TYPE, SpawnLeadOptions

    new_sid = await mgr.rotate_session()
    pm = ProcessManager()
    options = SpawnLeadOptions(
        team_name=name,
        session_id=new_sid,
        model=model,
        cwd=os.getcwd(),
        backend_id=backend_id,
    )
    new_bid = await pm.spawn_lead(options, parent_session_id=new_sid)
    await mgr.update_member(TEAM_LEAD_AGENT_TYPE, backend_id=new_bid)
    return new_bid, new_sid


async def _cmd_team_takeover(args: argparse.Namespace) -> None:
    from cc_team.tmux import TmuxManager
    from cc_team.types import TEAM_LEAD_AGENT_TYPE

    name = _require_team(args)
    mgr, config = await _require_config(name)

    # 检查 TL 是否已运行（从已读取的 config 中查找）
    lead = next((m for m in config.members if m.name == TEAM_LEAD_AGENT_TYPE), None)
    if lead and lead.backend_id:
        tmux = TmuxManager()
        if await tmux.is_pane_alive(lead.backend_id):
            if not getattr(args, "force", False):
                _error(
                    f"Team Lead is still running in pane {lead.backend_id}. "
                    "Use --force to override."
                )
                sys.exit(1)
            else:
                print(f"Warning: overriding existing TL in pane {lead.backend_id}")

    backend_id, new_sid = await _spawn_new_lead(
        mgr,
        name,
        args.model,
        getattr(args, "backend_id", None),
    )

    if args.use_json:
        _json_out({"backend_id": backend_id, "session_id": new_sid})
    else:
        print(f"Takeover complete: backend={backend_id}, session={new_sid}")


async def _graceful_exit_pane(pane_id: str, timeout: int, label: str = "pane") -> None:
    """Send /exit and poll until the pane dies, or exit(1) on timeout."""
    from cc_team.process_manager import ProcessManager

    pm = ProcessManager()
    try:
        await pm.graceful_exit(pane_id, timeout=timeout)
    except TimeoutError:
        _error(f"{label} {pane_id} did not exit within {timeout}s")
        sys.exit(1)


def _build_relay_request(args: argparse.Namespace) -> RelayRequest:
    """Build a RelayRequest from CLI args (shared by all relay handoff paths)."""
    from cc_team._context_relay import RelayRequest

    return RelayRequest(
        handoff_path=args.handoff,
        model=getattr(args, "model", DEFAULT_MODEL),
        timeout=args.timeout,
        cwd=os.getcwd(),
    )


def _print_relay_result(
    args: argparse.Namespace,
    result: RelayResult,
    *,
    label: str = "Relay",
    extra_json: dict[str, object] | None = None,
) -> None:
    """Format and print a RelayResult (shared by all relay handoff paths)."""
    if args.use_json:
        data: dict[str, object] = {
            "old_backend_id": result.old_backend_id,
            "new_backend_id": result.new_backend_id,
            "session_id": result.session_id,
            "handoff_injected": result.handoff_injected,
        }
        if extra_json:
            data.update(extra_json)
        _json_out(data)
    else:
        old = result.old_backend_id or "N/A"
        print(f"{label} complete:")
        print(f"  Backend: {old} → {result.new_backend_id}")
        print(f"  Handoff injected: {result.handoff_injected}")


async def _cmd_team_relay(args: argparse.Namespace) -> None:
    name = _require_team(args)

    # When --handoff is provided, delegate to _context_relay.relay_lead
    handoff = getattr(args, "handoff", None)
    if handoff:
        from cc_team._context_relay import relay_lead

        request = _build_relay_request(args)
        try:
            result = await relay_lead(request, name)
        except (FileNotFoundError, TimeoutError) as e:
            _error(str(e))
            sys.exit(1)
        _print_relay_result(args, result, label="Team relay")
        return

    # Original logic (no handoff)
    from cc_team._sync import sync_member_states
    from cc_team.process_manager import ProcessManager
    from cc_team.types import TEAM_LEAD_AGENT_TYPE

    mgr, config = await _require_config(name)
    old_session = config.lead_session_id

    # Step 1: graceful exit of old TL
    lead = next(
        (m for m in config.members if m.name == TEAM_LEAD_AGENT_TYPE),
        None,
    )
    if lead and lead.backend_id:
        await _graceful_exit_pane(
            lead.backend_id,
            args.timeout,
            label="TL pane",
        )

    # Step 2: rotate session + spawn new TL
    new_bid, new_sid = await _spawn_new_lead(mgr, name, args.model)

    # Step 3: wait for new TL init + agent state recovery
    _TL_INIT_WAIT_SECONDS = 5
    await asyncio.sleep(_TL_INIT_WAIT_SECONDS)

    pm = ProcessManager()
    fresh_config = mgr.read()
    if fresh_config:
        sync_result = await sync_member_states(mgr, pm, fresh_config)
    else:
        from cc_team._sync import SyncResult

        sync_result = SyncResult()

    if args.use_json:
        _json_out(
            {
                "old_session": old_session,
                "new_session": new_sid,
                "old_backend_id": lead.backend_id if lead else "",
                "new_backend_id": new_bid,
                "agents": {
                    "synced": sync_result.active,
                    "recovered": sync_result.recovered,
                    "inactive": sync_result.newly_inactive,
                },
            }
        )
    else:
        print("Relay complete:")
        print(f"  Session: {old_session[:8]} → {new_sid[:8]}")
        old_bid = lead.backend_id if lead else "N/A"
        print(f"  Backend: {old_bid} → {new_bid}")
        n_active = len(sync_result.active)
        print(
            f"  Agents: {len(sync_result.recovered)} recovered, "
            f"{n_active} active, "
            f"{len(sync_result.newly_inactive)} inactive"
        )


async def _cmd_team_session(args: argparse.Namespace) -> None:
    name = _require_team(args)
    mgr, config = await _require_config(name)

    if args.rotate:
        new_sid = await mgr.rotate_session()
        if args.use_json:
            _json_out({"session_id": new_sid})
        else:
            print(f"Session rotated: {new_sid}")
    elif args.set_id:
        await mgr.set_lead_session_id(args.set_id)
        if args.use_json:
            _json_out({"session_id": args.set_id})
        else:
            print(f"Session set: {args.set_id}")
    else:
        if args.use_json:
            _json_out({"session_id": config.lead_session_id})
        else:
            print(config.lead_session_id)


# ── agent 子命令 ──────────────────────────────────────────


async def _cmd_agent_register(args: argparse.Namespace) -> None:
    team = _require_team(args)
    mgr, _config = await _require_config(team)

    member = await mgr.register_member(
        name=args.name,
        agent_type=args.type,
        model=args.model,
        cwd=os.getcwd(),
    )

    if args.use_json:
        _json_out(
            {
                "name": member.name,
                "color": member.color,
                "active": member.is_active,
            }
        )
    else:
        print(f"Agent '{member.name}' registered (color={member.color})")


async def _cmd_agent_spawn(args: argparse.Namespace) -> None:
    from cc_team._spawn import spawn_agent_workflow
    from cc_team.process_manager import ProcessManager
    from cc_team.types import SpawnAgentOptions

    team = _require_team(args)
    mgr, config = await _require_config(team)

    agent_cwd = args.cwd if hasattr(args, "cwd") and args.cwd else os.getcwd()

    options = SpawnAgentOptions(
        name=args.name,
        prompt=args.prompt,
        agent_type=args.type,
        model=args.model,
        cwd=agent_cwd,
    )

    backend_id, color = await spawn_agent_workflow(
        mgr,
        ProcessManager(),
        options,
        team_name=team,
        cwd=agent_cwd,
        lead_session_id=config.lead_session_id,
    )

    if args.use_json:
        _json_out(
            {
                "name": options.name,
                "backend_id": backend_id,
                "color": color or "",
            }
        )
    else:
        print(f"Agent '{options.name}' spawned (backend={backend_id}, color={color or ''})")


async def _cmd_agent_list(args: argparse.Namespace) -> None:
    from cc_team.team_manager import TeamManager

    team = _require_team(args)
    mgr = TeamManager(team)
    members = mgr.list_teammates()
    if args.use_json:
        _json_out(
            [
                {
                    "name": m.name,
                    "type": m.agent_type,
                    "model": m.model,
                    "color": m.color,
                    "active": m.is_active,
                }
                for m in members
            ]
        )
    else:
        if not members:
            print("(no agents)")
            return
        for m in members:
            color = f" [{m.color}]" if m.color else ""
            active = " (active)" if m.is_active else ""
            print(f"  {m.name} ({m.agent_type}, {m.model}){color}{active}")


async def _cmd_agent_status(args: argparse.Namespace) -> None:
    team = _require_team(args)
    member = _require_member(team, args.name)
    if args.use_json:
        from cc_team._serialization import to_json_dict

        _json_out(to_json_dict(member))
    else:
        print(f"Name:   {member.name}")
        print(f"Type:   {member.agent_type}")
        print(f"Model:  {member.model}")
        print(f"Active: {'yes' if member.is_active else 'no'}")
        print(f"Color:  {member.color or 'none'}")
        print(f"Backend: {member.backend_id or 'N/A'}")


async def _cmd_agent_shutdown(args: argparse.Namespace) -> None:
    from cc_team.message_builder import MessageBuilder

    team = _require_team(args)
    _require_member(team, args.name)
    builder = MessageBuilder(team)
    req_id = await builder.send_shutdown_request(args.name, args.reason)
    if args.use_json:
        _json_out({"request_id": req_id, "target": args.name})
    else:
        print(f"Shutdown request sent to '{args.name}' (request_id={req_id})")


async def _cmd_agent_sync(args: argparse.Namespace) -> None:
    from cc_team._sync import sync_member_states
    from cc_team.process_manager import ProcessManager

    team = _require_team(args)
    mgr, config = await _require_config(team)

    result = await sync_member_states(mgr, ProcessManager(), config)

    if not args.use_json and not args.quiet:
        for name in result.active:
            bid = result.members[name].backend_id
            print(f"{name}: backend {bid} alive → synced")
        for name in result.recovered:
            bid = result.members[name].backend_id
            print(f"{name}: backend {bid} alive → recovered")
        for name in result.newly_inactive:
            print(f"{name}: dead → marked inactive")

    if args.use_json:
        _json_out(
            {
                "synced": result.active,
                "recovered": result.recovered,
                "inactive": result.newly_inactive,
            }
        )
    elif not args.quiet:
        parts = [f"{len(result.active)} synced"]
        if result.recovered:
            parts.append(f"{len(result.recovered)} recovered")
        parts.append(f"{len(result.newly_inactive)} inactive")
        print(f"Agents: {', '.join(parts)}")


async def _cmd_agent_relay(args: argparse.Namespace) -> None:
    """Context relay for a teammate: exit old process + respawn."""
    team = _require_team(args)

    # When --handoff is provided, delegate to _context_relay.relay_agent
    handoff = getattr(args, "handoff", None)
    if handoff:
        from cc_team._context_relay import relay_agent

        request = _build_relay_request(args)
        try:
            result = await relay_agent(request, team, args.name)
        except (FileNotFoundError, ValueError, TimeoutError) as e:
            _error(str(e))
            sys.exit(1)
        _print_relay_result(args, result, label="Agent relay", extra_json={"name": args.name})
        return

    # Original logic (no handoff)
    from cc_team._spawn import spawn_agent_workflow
    from cc_team.process_manager import ProcessManager
    from cc_team.types import SpawnAgentOptions

    mgr, config = await _require_config(team)

    # Look up member from already-loaded config
    member = next(
        (m for m in config.members if m.name == args.name),
        None,
    )
    if member is None:
        _error(f"Member '{args.name}' not found in team '{team}'")
        sys.exit(1)

    if not member.backend_id:
        _error(f"Agent '{args.name}' has no backend process to relay")
        sys.exit(1)

    old_backend = member.backend_id

    # Step 1: graceful exit of old agent
    await _graceful_exit_pane(
        old_backend,
        args.timeout,
        label="Agent pane",
    )

    # Step 2: remove old member from config
    await mgr.remove_member(args.name)

    # Step 3: respawn with preserved config (or new prompt)
    prompt = args.prompt or member.prompt or "Continue working"
    agent_cwd = member.cwd or os.getcwd()

    options = SpawnAgentOptions(
        name=member.name,
        prompt=prompt,
        agent_type=member.agent_type,
        model=member.model,
        cwd=agent_cwd,
    )

    pm = ProcessManager()
    new_backend, color = await spawn_agent_workflow(
        mgr,
        pm,
        options,
        team_name=team,
        cwd=agent_cwd,
        lead_session_id=config.lead_session_id,
    )

    prompt_status = "new" if args.prompt else "preserved from original"

    if args.use_json:
        _json_out(
            {
                "name": args.name,
                "old_backend_id": old_backend,
                "new_backend_id": new_backend,
                "prompt": prompt_status,
                "color": color or "",
            }
        )
    else:
        print("Agent relay complete:")
        print(f"  Old backend: {old_backend}")
        print(f"  New backend: {new_backend}")
        print(f"  Prompt: ({prompt_status})")


async def _cmd_agent_kill(args: argparse.Namespace) -> None:
    from cc_team.team_manager import TeamManager
    from cc_team.tmux import TmuxManager

    team = _require_team(args)
    mgr = TeamManager(team)
    member = mgr.get_member(args.name)
    if member is None:
        _error(f"Agent '{args.name}' not found in team '{team}'")
        sys.exit(1)

    # 终止 tmux pane
    if member.backend_id:
        tmux = TmuxManager()
        with contextlib.suppress(Exception):
            await tmux.kill_pane(member.backend_id)

    # 从团队中移除成员
    await mgr.remove_member(args.name)

    if not args.quiet:
        print(f"Agent '{args.name}' killed.")


# ── task 子命令 ───────────────────────────────────────────


async def _cmd_task_create(args: argparse.Namespace) -> None:
    from cc_team.task_manager import TaskManager

    team = _require_team(args)
    mgr = TaskManager(team)
    task = await mgr.create(
        subject=args.subject,
        description=args.description,
        owner=getattr(args, "owner", None),
    )
    if args.use_json:
        from cc_team._serialization import task_file_to_dict

        _json_out(task_file_to_dict(task))
    else:
        print(f"Task #{task.id} created: {task.subject}")


async def _cmd_task_list(args: argparse.Namespace) -> None:
    from cc_team.task_manager import TaskManager

    team = _require_team(args)
    mgr = TaskManager(team)
    tasks = mgr.list_all()
    if args.use_json:
        from cc_team._serialization import task_file_to_dict

        _json_out([task_file_to_dict(t) for t in tasks])
    else:
        if not tasks:
            print("(no tasks)")
            return
        for t in tasks:
            owner = f" @{t.owner}" if t.owner else ""
            blocked = f" [blocked by {','.join(t.blocked_by)}]" if t.blocked_by else ""
            print(f"  #{t.id} [{t.status:12s}] {t.subject}{owner}{blocked}")


async def _cmd_task_update(args: argparse.Namespace) -> None:
    from cc_team.task_manager import TaskManager

    team = _require_team(args)
    mgr = TaskManager(team)
    kwargs: dict[str, Any] = {}
    if args.status:
        kwargs["status"] = args.status
    if args.owner is not None:
        # --owner "" 表示取消分配
        kwargs["owner"] = args.owner if args.owner else None
    if args.subject:
        kwargs["subject"] = args.subject
    if not kwargs:
        _error("At least one update field required (--status, --owner, --subject)")
        sys.exit(1)
    task = await mgr.update(args.id, **kwargs)
    if args.use_json:
        from cc_team._serialization import task_file_to_dict

        _json_out(task_file_to_dict(task))
    else:
        print(f"Task #{task.id} updated: status={task.status}, owner={task.owner or '-'}")


async def _cmd_task_complete(args: argparse.Namespace) -> None:
    from cc_team.task_manager import TaskManager

    team = _require_team(args)
    mgr = TaskManager(team)
    task = await mgr.update(args.id, status="completed")
    if args.use_json:
        from cc_team._serialization import task_file_to_dict

        _json_out(task_file_to_dict(task))
    else:
        print(f"Task #{task.id} completed.")


# ── message 子命令 ────────────────────────────────────────


async def _cmd_msg_send(args: argparse.Namespace) -> None:
    from cc_team.message_builder import MessageBuilder

    team = _require_team(args)
    _require_member(team, args.to)
    builder = MessageBuilder(team)
    await builder.send_plain(args.to, args.content, summary=args.summary)
    if args.use_json:
        _json_out({"ok": True, "to": args.to})
    elif not args.quiet:
        print(f"Message sent to '{args.to}'.")


async def _cmd_msg_broadcast(args: argparse.Namespace) -> None:
    from cc_team.message_builder import MessageBuilder
    from cc_team.team_manager import TeamManager

    team = _require_team(args)
    mgr = TeamManager(team)
    recipients = [m.name for m in mgr.list_members()]
    if not recipients:
        _error("No agents in team to broadcast to")
        sys.exit(1)
    builder = MessageBuilder(team)
    await builder.broadcast(args.content, recipients, summary=args.summary)
    if args.use_json:
        _json_out({"ok": True, "recipients": recipients})
    elif not args.quiet:
        print(f"Broadcast sent to {len(recipients)} agents.")


async def _cmd_msg_read(args: argparse.Namespace) -> None:
    from cc_team.inbox import InboxIO

    team = _require_team(args)
    agent = args.agent or "team-lead"
    inbox = InboxIO(team, agent)
    messages = inbox.read_all() if args.all else inbox.read_unread()
    if args.use_json:
        from cc_team._serialization import inbox_message_to_dict

        _json_out([inbox_message_to_dict(m) for m in messages])
    else:
        if not messages:
            print("(no messages)")
            return
        for m in messages:
            flag = "[read]" if m.read else "[NEW]"
            summary = f" ({m.summary})" if m.summary else ""
            print(f"  {flag} {m.timestamp} from {m.from_}{summary}:")
            # 截断长消息
            text = m.text if len(m.text) <= 120 else m.text[:117] + "..."
            print(f"    {text}")


# ── status 综合命令 ───────────────────────────────────────


async def _cmd_status(args: argparse.Namespace) -> None:
    from cc_team.task_manager import TaskManager
    from cc_team.team_manager import TeamManager

    team = _require_team(args)
    t_mgr = TeamManager(team)
    config = t_mgr.read()
    if config is None:
        _error(f"Team '{team}' not found")
        sys.exit(1)

    tk_mgr = TaskManager(team)
    tasks = tk_mgr.list_all()

    if args.use_json:
        from cc_team._serialization import task_file_to_dict, team_config_to_dict

        _json_out(
            {
                "team": team_config_to_dict(config),
                "tasks": [task_file_to_dict(t) for t in tasks],
            }
        )
        return

    # 团队概览
    print(f"=== Team: {config.name} ===")
    if config.description:
        print(f"Description: {config.description}")

    # Agent 列表
    print(f"\nAgents ({len(config.members)}):")
    for m in config.members:
        active = "active" if m.is_active else "inactive"
        color = f" [{m.color}]" if m.color else ""
        print(f"  {m.name:20s} {m.agent_type:20s} [{active}]{color}")

    # 任务统计
    pending = sum(1 for t in tasks if t.status == "pending")
    in_progress = sum(1 for t in tasks if t.status == "in_progress")
    completed = sum(1 for t in tasks if t.status == "completed")
    summary = f"{pending} pending, {in_progress} in-progress, {completed} completed"
    print(f"\nTasks ({len(tasks)}: {summary}):")
    for t in tasks:
        owner = f" @{t.owner}" if t.owner else ""
        print(f"  #{t.id} [{t.status:12s}] {t.subject}{owner}")


# ── skill 命令 ────────────────────────────────────────────


async def _cmd_skill(args: argparse.Namespace) -> None:
    from cc_team._skill_doc import SKILL_DOC, SKILL_DOC_VERSION, SKILL_SECTIONS

    if args.use_json:
        _json_out(
            {
                "version": SKILL_DOC_VERSION,
                "sections": SKILL_SECTIONS,
            }
        )
    else:
        print(SKILL_DOC)


# ── relay 命令（standalone）────────────────────────────────


async def _cmd_relay(args: argparse.Namespace) -> None:
    """Standalone context relay: exit old process + start new with handoff."""
    from cc_team._context_relay import RelayRequest, relay_standalone
    from cc_team.process_manager import ProcessManager
    from cc_team.tmux import TmuxManager

    if not args.backend_id:
        _error("--backend-id is required for standalone relay")
        sys.exit(1)

    request = RelayRequest(
        handoff_path=args.handoff,
        model=args.model,
        timeout=args.timeout,
        cwd=os.getcwd(),
    )

    tmux = TmuxManager()
    pm = ProcessManager(tmux=tmux)

    try:
        result = await relay_standalone(request, pm, args.backend_id, tmux)
    except (FileNotFoundError, TimeoutError) as e:
        _error(str(e))
        sys.exit(1)

    _print_relay_result(args, result)


# ── setup 命令 ─────────────────────────────────────────────


_CCT_HOOKS_CONFIG: dict[str, object] = {
    "hooks": {
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "cct _hook stop",
                        "timeout": 30,
                    }
                ]
            }
        ]
    },
    "statusLine": {
        "type": "command",
        "command": "cct _hook statusline",
        "padding": 2,
    },
}


def _merge_hooks_into_settings(settings_path: pathlib.Path) -> dict[str, str]:
    """Merge CCT hooks/statusLine into a settings JSON file.

    Reads the existing file (or starts from {}), adds the hooks and
    statusLine keys, and writes back.  Other keys are preserved.

    Returns:
        {"status": "installed"|"already_configured", "path": str(settings_path)}
    """
    existing: dict[str, object] = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Check if already configured
    if (
        existing.get("hooks") == _CCT_HOOKS_CONFIG["hooks"]
        and existing.get("statusLine") == _CCT_HOOKS_CONFIG["statusLine"]
    ):
        return {"status": "already_configured", "path": str(settings_path)}

    existing["hooks"] = _CCT_HOOKS_CONFIG["hooks"]
    existing["statusLine"] = _CCT_HOOKS_CONFIG["statusLine"]

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"status": "installed", "path": str(settings_path)}


async def _cmd_setup(args: argparse.Namespace) -> None:
    """Install CCT hooks into project settings, or show instructions."""
    if getattr(args, "install", False):
        proj = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
        settings_path = pathlib.Path(proj) / ".claude" / "settings.local.json"
        result = _merge_hooks_into_settings(settings_path)

        if args.use_json:
            _json_out(result)
        else:
            if result["status"] == "already_configured":
                print(f"CCT hooks already configured in {result['path']}")
            else:
                print(f"CCT hooks installed into {result['path']}")
    else:
        if args.use_json:
            _json_out({"hint": "Run 'cct setup --install' to configure hooks"})
        else:
            print("Install CCT hooks into project settings:")
            print("  cct setup --install")
            print()
            print("This adds Stop hook and statusLine to .claude/settings.local.json")


# ── session 命令 ───────────────────────────────────────────


def _cmd_session_start(args: argparse.Namespace) -> None:
    """Start a new Claude session with CCT_SESSION_ID set.

    This is synchronous — os.execvpe replaces the current process.
    """
    from cc_team.hooks._common import relay_paths
    from cc_team.process_manager import _find_claude_binary

    cct_sid = str(uuid.uuid4())
    proj = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

    # Create relay directory and init history.json
    paths = relay_paths(cct_sid, proj)
    os.makedirs(paths["dir"], exist_ok=True)

    history_data = {"sessions": [], "created_at": datetime.now(timezone.utc).isoformat()}
    with open(paths["history"], "w") as f:
        json.dump(history_data, f, indent=2)

    # Prepare env with CCT_SESSION_ID
    env = os.environ.copy()
    env["CCT_SESSION_ID"] = cct_sid

    # Passthrough args for claude
    passthrough = getattr(args, "claude_args", []) or []
    # Strip leading "--" if present (from argparse REMAINDER)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    claude_bin = _find_claude_binary()
    if not getattr(args, "quiet", False):
        print(f"Starting session: {cct_sid}", file=sys.stderr)

    os.execvpe(claude_bin, [claude_bin, *passthrough], env)


# ── Parser 构建 ───────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cct",
        description="Claude Code multi-agent team orchestration CLI",
    )
    # 全局选项（dest 避免与 json 模块冲突）
    parser.add_argument(
        "--team-name",
        dest="team_name",
        help="Team name (required for most commands)",
    )
    parser.add_argument("--json", dest="use_json", action="store_true", help="JSON output format")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode")

    sub = parser.add_subparsers(dest="command")

    # ── team ──────────────────────────────────
    team_p = sub.add_parser("team", help="Team management")
    team_p.set_defaults(func=lambda _: (team_p.print_help(), sys.exit(1)))
    team_sub = team_p.add_subparsers(dest="team_action")

    tc = team_sub.add_parser("create", help="Create a new team")
    tc.add_argument("--name", help="Team name (overrides --team-name)")
    tc.add_argument("--description", default="", help="Team description")
    tc.set_defaults(func=_cmd_team_create)

    ti = team_sub.add_parser("info", help="Show team info")
    ti.set_defaults(func=_cmd_team_info)

    td = team_sub.add_parser("destroy", help="Destroy a team and all resources")
    td.set_defaults(func=_cmd_team_destroy)

    tko = team_sub.add_parser("takeover", help="Takeover team lead (rotate session + spawn TL)")
    tko.add_argument("--model", default=DEFAULT_MODEL, help="Model for team lead")
    tko.add_argument("--pane-id", dest="backend_id", help="Reuse existing tmux pane")
    tko.add_argument("--force", action="store_true", help="Force takeover even if TL is running")
    tko.set_defaults(func=_cmd_team_takeover)

    trl = team_sub.add_parser(
        "relay",
        help="Context relay (stop old TL + rotate session + spawn new TL)",
    )
    trl.add_argument("--model", default=DEFAULT_MODEL, help="Model for new team lead")
    trl.add_argument("--timeout", type=int, default=30, help="Exit wait timeout in seconds")
    trl.add_argument("--handoff", help="Path to handoff file for context injection")
    trl.set_defaults(func=_cmd_team_relay)

    tss = team_sub.add_parser("session", help="Query or rotate team lead session ID")
    tss.add_argument("--rotate", action="store_true", help="Generate new UUID session ID")
    tss.add_argument("--set", dest="set_id", help="Set specific session ID")
    tss.set_defaults(func=_cmd_team_session)

    # ── agent ─────────────────────────────────
    agent_p = sub.add_parser("agent", help="Agent management")
    agent_p.set_defaults(func=lambda _: (agent_p.print_help(), sys.exit(1)))
    agent_sub = agent_p.add_subparsers(dest="agent_action")

    areg = agent_sub.add_parser("register", help="Register an agent without starting a process")
    areg.add_argument("--name", required=True, help="Agent name")
    areg.add_argument(
        "--type",
        default="general-purpose",
        help="Agent type (default: general-purpose)",
    )
    areg.add_argument("--model", default=DEFAULT_MODEL, help="Model ID")
    areg.set_defaults(func=_cmd_agent_register)

    asp = agent_sub.add_parser("spawn", help="Spawn a new agent")
    asp.add_argument("--name", required=True, help="Agent name")
    asp.add_argument("--prompt", required=True, help="Initial prompt for the agent")
    asp.add_argument(
        "--type",
        default="general-purpose",
        help="Agent type (default: general-purpose)",
    )
    asp.add_argument("--model", default=DEFAULT_MODEL, help="Model ID")
    asp.set_defaults(func=_cmd_agent_spawn)

    al = agent_sub.add_parser("list", help="List all agents")
    al.set_defaults(func=_cmd_agent_list)

    ast = agent_sub.add_parser("status", help="Show agent status")
    ast.add_argument("--name", required=True, help="Agent name")
    ast.set_defaults(func=_cmd_agent_status)

    asd = agent_sub.add_parser("shutdown", help="Send shutdown request to agent")
    asd.add_argument("--name", required=True, help="Agent name")
    asd.add_argument("--reason", default="CLI shutdown request", help="Shutdown reason")
    asd.set_defaults(func=_cmd_agent_shutdown)

    arl = agent_sub.add_parser(
        "relay",
        help="Context relay: exit + respawn agent with fresh context",
    )
    arl.add_argument("--name", required=True, help="Agent name")
    arl.add_argument("--prompt", help="New prompt (default: reuse original)")
    arl.add_argument("--model", default=DEFAULT_MODEL, help="Model for respawned agent")
    arl.add_argument("--timeout", type=int, default=30, help="Exit wait timeout (seconds)")
    arl.add_argument("--handoff", help="Path to handoff file for context injection")
    arl.set_defaults(func=_cmd_agent_relay)

    asyn = agent_sub.add_parser(
        "sync",
        help="Sync agents: verify pane liveness, recover or mark inactive",
    )
    asyn.set_defaults(func=_cmd_agent_sync)

    ak = agent_sub.add_parser("kill", help="Force kill an agent process")
    ak.add_argument("--name", required=True, help="Agent name")
    ak.set_defaults(func=_cmd_agent_kill)

    # ── task ──────────────────────────────────
    task_p = sub.add_parser("task", help="Task management")
    task_p.set_defaults(func=lambda _: (task_p.print_help(), sys.exit(1)))
    task_sub = task_p.add_subparsers(dest="task_action")

    tkc = task_sub.add_parser("create", help="Create a new task")
    tkc.add_argument("--subject", required=True, help="Task subject")
    tkc.add_argument("--description", default="", help="Task description")
    tkc.add_argument("--owner", help="Task owner (agent name)")
    tkc.set_defaults(func=_cmd_task_create)

    tkl = task_sub.add_parser("list", help="List all tasks")
    tkl.set_defaults(func=_cmd_task_list)

    tku = task_sub.add_parser("update", help="Update a task")
    tku.add_argument("--id", required=True, help="Task ID")
    tku.add_argument(
        "--status", choices=["pending", "in_progress", "completed", "deleted"], help="New status"
    )
    tku.add_argument("--owner", help="New owner (empty string to unassign)")
    tku.add_argument("--subject", help="New subject")
    tku.set_defaults(func=_cmd_task_update)

    tkd = task_sub.add_parser("complete", help="Mark a task as completed")
    tkd.add_argument("--id", required=True, help="Task ID")
    tkd.set_defaults(func=_cmd_task_complete)

    # ── message ───────────────────────────────
    msg_p = sub.add_parser("message", help="Messaging operations")
    msg_p.set_defaults(func=lambda _: (msg_p.print_help(), sys.exit(1)))
    msg_sub = msg_p.add_subparsers(dest="msg_action")

    ms = msg_sub.add_parser("send", help="Send a direct message")
    ms.add_argument("--to", required=True, help="Recipient agent name")
    ms.add_argument("--content", required=True, help="Message content")
    ms.add_argument("--summary", help="Short summary for preview")
    ms.set_defaults(func=_cmd_msg_send)

    mb = msg_sub.add_parser("broadcast", help="Broadcast to all agents")
    mb.add_argument("--content", required=True, help="Message content")
    mb.add_argument("--summary", help="Short summary for preview")
    mb.set_defaults(func=_cmd_msg_broadcast)

    mr = msg_sub.add_parser("read", help="Read agent inbox messages")
    mr.add_argument("--agent", help="Agent name (default: team-lead)")
    mr.add_argument("--all", action="store_true", help="Show all messages including read")
    mr.set_defaults(func=_cmd_msg_read)

    # ── status ────────────────────────────────
    st = sub.add_parser("status", help="Show comprehensive team status")
    st.set_defaults(func=_cmd_status)

    # ── relay (standalone, no --team-name) ──
    rl = sub.add_parser(
        "relay",
        help="Standalone context relay (requires CCT_SESSION_ID env)",
    )
    rl.add_argument("--handoff", required=True, help="Path to handoff file")
    rl.add_argument("--backend-id", dest="backend_id", help="Target tmux pane ID")
    rl.add_argument("--model", default=DEFAULT_MODEL, help="Model for new session")
    rl.add_argument("--timeout", type=int, default=30, help="Exit wait timeout (seconds)")
    rl.set_defaults(func=_cmd_relay)

    # ── setup (no --team-name required) ─────
    su = sub.add_parser("setup", help="Show plugin path or install symlink")
    su.add_argument("--install", action="store_true", help="Install plugin symlink")
    su.set_defaults(func=_cmd_setup)

    # ── session ──────────────────────────────
    sess_p = sub.add_parser("session", help="Session management")
    sess_p.set_defaults(func=lambda _: (sess_p.print_help(), sys.exit(1)))
    sess_sub = sess_p.add_subparsers(dest="session_action")

    ss = sess_sub.add_parser("start", help="Start Claude with CCT_SESSION_ID")
    ss.add_argument(
        "claude_args",
        nargs=argparse.REMAINDER,
        help="Arguments to pass through to claude",
    )
    ss.set_defaults(func=_cmd_session_start, is_sync=True)

    # ── skill (no --team-name required) ──────
    sk = sub.add_parser("skill", help="Print AI agent skill reference document")
    sk.set_defaults(func=_cmd_skill)

    # ── _hook (internal, called by plugin hooks.json) ──
    hook_p = sub.add_parser("_hook", help=argparse.SUPPRESS)
    hook_p.set_defaults(func=lambda _: (hook_p.print_help(), sys.exit(1)))
    hook_sub = hook_p.add_subparsers(dest="hook_action")

    hook_stop = hook_sub.add_parser("stop", help="Stop hook handler")
    hook_stop.set_defaults(func=_cmd_hook_stop, is_sync=True)

    hook_sl = hook_sub.add_parser("statusline", help="Statusline hook handler")
    hook_sl.set_defaults(func=_cmd_hook_statusline, is_sync=True)

    return parser


# ── _hook handlers ────────────────────────────────────────


def _run_hook_safely(hook_module: str) -> None:
    """Run a hook main() with error suppression.

    Hooks must never crash Claude Code. All non-SystemExit exceptions —
    including import errors — are logged to the debug log and silently
    swallowed so they never propagate to the CLI's top-level handler.
    """
    try:
        import importlib

        mod = importlib.import_module(f"cc_team.hooks.{hook_module}")
        mod.main()
    except SystemExit:
        raise
    except Exception:
        import traceback

        try:
            from cc_team.hooks.stop import _log_error

            _log_error(f"UNCAUGHT in {hook_module}:\n{traceback.format_exc()}")
        except Exception:
            pass  # Even logging failed — swallow silently


def _cmd_hook_stop(_args: argparse.Namespace) -> None:
    """Delegate to cc_team.hooks.stop.main() within the correct venv."""
    _run_hook_safely("stop")


def _cmd_hook_statusline(_args: argparse.Namespace) -> None:
    """Delegate to cc_team.hooks.statusline.main() within the correct venv."""
    _run_hook_safely("statusline")


# ── 入口 ──────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """CLI 入口点。通过 pyproject.toml [project.scripts] 注册为 cct。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    try:
        if getattr(args, "is_sync", False):
            args.func(args)
        else:
            asyncio.run(args.func(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:
        if getattr(args, "verbose", False):
            import traceback

            traceback.print_exc()
        _error(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
