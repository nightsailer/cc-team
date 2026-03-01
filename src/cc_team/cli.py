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
import sys
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cc_team.types import TeamMember

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


# ── agent 子命令 ──────────────────────────────────────────


async def _cmd_agent_spawn(args: argparse.Namespace) -> None:
    from cc_team._serialization import now_ms
    from cc_team.inbox import InboxIO
    from cc_team.process_manager import ProcessManager
    from cc_team.team_manager import TeamManager
    from cc_team.types import SpawnAgentOptions, TeamMember

    team = _require_team(args)
    mgr = TeamManager(team)

    # 验证团队存在
    config = mgr.read()
    if config is None:
        _error(f"Team '{team}' not found. Create it first with: cct team create")
        sys.exit(1)

    agent_cwd = args.cwd if hasattr(args, "cwd") and args.cwd else os.getcwd()

    options = SpawnAgentOptions(
        name=args.name,
        prompt=args.prompt,
        agent_type=args.type,
        model=args.model,
        cwd=agent_cwd,
    )

    # 1. 分配颜色 + 注册成员
    color = mgr.next_color(config)
    member = TeamMember(
        agent_id=f"{options.name}@{team}",
        name=options.name,
        agent_type=options.agent_type,
        model=options.model,
        joined_at=now_ms(),
        tmux_pane_id="",
        cwd=agent_cwd,
        prompt=options.prompt,
        color=color,
        plan_mode_required=options.plan_mode_required,
        backend_type="tmux",
        is_active=True,
    )
    await mgr.add_member(member)

    # 2. 写入初始 prompt
    inbox = InboxIO(team, options.name)
    await inbox.write_initial_prompt("team-lead", options.prompt)

    # 3. 启动进程
    pm = ProcessManager()
    try:
        pane_id = await pm.spawn(
            options,
            team_name=team,
            color=color,
            parent_session_id=config.lead_session_id,
        )
    except Exception:
        # 回滚: 移除已注册的成员
        with contextlib.suppress(Exception):
            await mgr.remove_member(options.name)
        raise

    # 更新 pane_id
    await mgr.update_member(options.name, tmux_pane_id=pane_id)

    if args.use_json:
        _json_out({"name": options.name, "pane_id": pane_id, "color": color})
    else:
        print(f"Agent '{options.name}' spawned (pane={pane_id}, color={color})")


async def _cmd_agent_list(args: argparse.Namespace) -> None:
    from cc_team.team_manager import TeamManager

    team = _require_team(args)
    mgr = TeamManager(team)
    members = mgr.list_teammates()
    if args.use_json:
        _json_out([
            {"name": m.name, "type": m.agent_type, "model": m.model,
             "color": m.color, "active": m.is_active}
            for m in members
        ])
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
        print(f"Pane:   {member.tmux_pane_id or 'N/A'}")


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
    if member.tmux_pane_id:
        tmux = TmuxManager()
        with contextlib.suppress(Exception):
            await tmux.kill_pane(member.tmux_pane_id)

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
        _json_out({
            "team": team_config_to_dict(config),
            "tasks": [task_file_to_dict(t) for t in tasks],
        })
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
        _json_out({
            "version": SKILL_DOC_VERSION,
            "sections": SKILL_SECTIONS,
        })
    else:
        print(SKILL_DOC)


# ── Parser 构建 ───────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cct",
        description="Claude Code multi-agent team orchestration CLI",
    )
    # 全局选项（dest 避免与 json 模块冲突）
    parser.add_argument(
        "--team-name", dest="team_name",
        help="Team name (required for most commands)",
    )
    parser.add_argument("--json", dest="use_json", action="store_true", help="JSON output format")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode")

    sub = parser.add_subparsers(dest="command")

    # ── team ──────────────────────────────────
    team_p = sub.add_parser("team", help="Team management")
    team_sub = team_p.add_subparsers(dest="team_action")

    tc = team_sub.add_parser("create", help="Create a new team")
    tc.add_argument("--name", help="Team name (overrides --team-name)")
    tc.add_argument("--description", default="", help="Team description")
    tc.set_defaults(func=_cmd_team_create)

    ti = team_sub.add_parser("info", help="Show team info")
    ti.set_defaults(func=_cmd_team_info)

    td = team_sub.add_parser("destroy", help="Destroy a team and all resources")
    td.set_defaults(func=_cmd_team_destroy)

    # ── agent ─────────────────────────────────
    agent_p = sub.add_parser("agent", help="Agent management")
    agent_sub = agent_p.add_subparsers(dest="agent_action")

    asp = agent_sub.add_parser("spawn", help="Spawn a new agent")
    asp.add_argument("--name", required=True, help="Agent name")
    asp.add_argument("--prompt", required=True, help="Initial prompt for the agent")
    asp.add_argument(
        "--type", default="general-purpose",
        help="Agent type (default: general-purpose)",
    )
    asp.add_argument("--model", default="claude-sonnet-4-6", help="Model ID")
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

    ak = agent_sub.add_parser("kill", help="Force kill an agent process")
    ak.add_argument("--name", required=True, help="Agent name")
    ak.set_defaults(func=_cmd_agent_kill)

    # ── task ──────────────────────────────────
    task_p = sub.add_parser("task", help="Task management")
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
    tku.add_argument("--status", choices=["pending", "in_progress", "completed", "deleted"],
                     help="New status")
    tku.add_argument("--owner", help="New owner (empty string to unassign)")
    tku.add_argument("--subject", help="New subject")
    tku.set_defaults(func=_cmd_task_update)

    tkd = task_sub.add_parser("complete", help="Mark a task as completed")
    tkd.add_argument("--id", required=True, help="Task ID")
    tkd.set_defaults(func=_cmd_task_complete)

    # ── message ───────────────────────────────
    msg_p = sub.add_parser("message", help="Messaging operations")
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

    # ── skill (no --team-name required) ──────
    sk = sub.add_parser("skill", help="Print AI agent skill reference document")
    sk.set_defaults(func=_cmd_skill)

    return parser


# ── 入口 ──────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """CLI 入口点。通过 pyproject.toml [project.scripts] 注册为 cct。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    try:
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
