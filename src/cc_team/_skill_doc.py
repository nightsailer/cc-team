"""Skill document for AI agent consumption.

Provides a self-contained Markdown reference covering the full cct command set,
workflow patterns, tips, and protocol compatibility. Designed to be consumed by
AI agents (e.g. via ``cct skill``).
"""

from __future__ import annotations

SKILL_DOC_VERSION: str = "0.2.0"

SKILL_SECTIONS: list[dict[str, str]] = [
    {
        "title": "Overview",
        "content": (
            "cct (Claude Code Team) is a zero-dependency CLI for orchestrating "
            "multi-agent teams. It manages teams, agents, tasks, and messaging "
            "entirely through the filesystem under ~/.claude/."
        ),
    },
    {
        "title": "Global Options",
        "content": (
            "--team-name <name>  Team name (required for most commands)\n"
            "--json              Output in JSON format\n"
            "--verbose / -v      Verbose output\n"
            "--quiet / -q        Quiet mode"
        ),
    },
    {
        "title": "Team Commands",
        "content": (
            "cct --team-name <t> team create --name <n> [--description <d>]\n"
            "  Create a new team.\n\n"
            "cct --team-name <t> team info\n"
            "  Show team info (members, description).\n\n"
            "cct --team-name <t> team destroy\n"
            "  Destroy a team and all associated resources.\n\n"
            "cct --team-name <t> team takeover [--model <m>] [--pane-id <p>] [--force]\n"
            "  Takeover team lead: rotate session + spawn new TL process.\n"
            "  Use --force to override a still-running TL. --pane-id reuses an existing pane.\n\n"
            "cct --team-name <t> team relay [--model <m>]\n"
            "  Context relay: gracefully stop old TL (/exit), rotate session, spawn new TL.\n"
            "  Waits up to 30s for old TL to exit.\n\n"
            "cct --team-name <t> team session [--rotate] [--set <id>]\n"
            "  Query or manage the lead session ID.\n"
            "  No flags: print current session ID.\n"
            "  --rotate: generate a new UUID session ID.\n"
            "  --set <id>: set a specific session ID."
        ),
    },
    {
        "title": "Agent Commands",
        "content": (
            "cct --team-name <t> agent register --name <n> [--type <type>] [--model <model>]\n"
            "  Register an agent in config.json without starting a process.\n"
            "  Creates an empty inbox. The agent is marked inactive (is_active=False).\n"
            "  Useful for external systems that manage their own processes.\n\n"
            "cct --team-name <t> agent spawn --name <n> --prompt <p> "
            "[--type <type>] [--model <model>]\n"
            "  Spawn a new agent. Registers member, writes initial prompt, "
            "starts tmux pane.\n\n"
            "cct --team-name <t> agent list\n"
            "  List all agents in the team.\n\n"
            "cct --team-name <t> agent status --name <n>\n"
            "  Show detailed status of a specific agent.\n\n"
            "cct --team-name <t> agent shutdown --name <n> [--reason <r>]\n"
            "  Send a graceful shutdown request to an agent.\n\n"
            "cct --team-name <t> agent sync\n"
            "  Verify pane liveness for all active agents.\n"
            "  Alive agents are marked as synced; dead agents are marked inactive.\n\n"
            "cct --team-name <t> agent kill --name <n>\n"
            "  Force kill an agent process and remove from team."
        ),
    },
    {
        "title": "Task Commands",
        "content": (
            "cct --team-name <t> task create --subject <s> "
            "[--description <d>] [--owner <o>]\n"
            "  Create a new task.\n\n"
            "cct --team-name <t> task list\n"
            "  List all tasks with status, owner, and blockers.\n\n"
            "cct --team-name <t> task update --id <id> "
            "[--status <s>] [--owner <o>] [--subject <s>]\n"
            "  Update task fields. At least one field required.\n\n"
            "cct --team-name <t> task complete --id <id>\n"
            "  Mark a task as completed."
        ),
    },
    {
        "title": "Message Commands",
        "content": (
            "cct --team-name <t> message send --to <agent> --content <c> "
            "[--summary <s>]\n"
            "  Send a direct message to an agent.\n\n"
            "cct --team-name <t> message broadcast --content <c> "
            "[--summary <s>]\n"
            "  Broadcast a message to all agents.\n\n"
            "cct --team-name <t> message read [--agent <a>] [--all]\n"
            "  Read inbox messages. Defaults to team-lead's inbox."
        ),
    },
    {
        "title": "Status Command",
        "content": (
            "cct --team-name <t> status\n"
            "  Show comprehensive team status: members, tasks, and statistics."
        ),
    },
    {
        "title": "Skill Command",
        "content": (
            "cct skill\n"
            "  Print this reference document. No --team-name required.\n\n"
            "cct --json skill\n"
            "  Output reference as structured JSON sections."
        ),
    },
    {
        "title": "Workflow Patterns",
        "content": (
            "1. Create a team:\n"
            "   cct --team-name proj team create --description 'My project'\n\n"
            "2. Spawn agents:\n"
            "   cct --team-name proj agent spawn --name researcher "
            "--prompt 'Analyze codebase'\n"
            "   cct --team-name proj agent spawn --name coder "
            "--prompt 'Implement features'\n\n"
            "3. Create and assign tasks:\n"
            "   cct --team-name proj task create --subject 'Research API' "
            "--owner researcher\n\n"
            "4. Monitor progress:\n"
            "   cct --team-name proj status\n\n"
            "5. Communicate:\n"
            "   cct --team-name proj message send --to coder "
            "--content 'Start on task #1'\n\n"
            "6. Cleanup:\n"
            "   cct --team-name proj agent shutdown --name researcher\n"
            "   cct --team-name proj team destroy\n\n"
            "--- Session Management ---\n\n"
            "7. Takeover (new TL replaces old):\n"
            "   cct --team-name proj team takeover --force\n\n"
            "8. Context relay (graceful TL rotation):\n"
            "   cct --team-name proj team relay\n\n"
            "9. Register external agent (no process):\n"
            "   cct --team-name proj agent register --name external-bot\n\n"
            "10. Sync agent liveness:\n"
            "   cct --team-name proj agent sync"
        ),
    },
    {
        "title": "Tips",
        "content": (
            "- All commands support --json for machine-readable output.\n"
            "- Use --quiet to suppress non-essential output.\n"
            "- The 'skill' command does not require --team-name.\n"
            "- Agent spawn rolls back member registration on process failure.\n"
            "- Task update requires at least one field (--status, --owner, "
            "or --subject).\n"
            "- Message read defaults to team-lead inbox; use --agent to "
            "read another agent's inbox.\n"
            "- All data is stored under ~/.claude/ as JSON files "
            "(no database required).\n"
            "- Use 'agent register' to pre-register members managed by "
            "external systems (SDK, custom backends).\n"
            "- Use 'agent sync' after a crash to reconcile config with "
            "actual pane liveness.\n"
            "- 'team relay' is the recommended way to rotate context — "
            "it gracefully stops the old TL before spawning a new one."
        ),
    },
    {
        "title": "Protocol Compatibility",
        "content": (
            "cct implements the Claude Code multi-agent team protocol:\n"
            "- Team config: ~/.claude/teams/<name>/config.json\n"
            "- Task files: ~/.claude/tasks/<name>/<id>.json\n"
            "- Inboxes: ~/.claude/teams/<name>/inboxes/<agent>.json\n"
            "- Field naming: camelCase in JSON, snake_case in Python\n"
            "- Timestamps: ISO 8601 strings and Unix ms integers"
        ),
    },
]


def _build_skill_doc() -> str:
    """Build the full Markdown skill document from sections."""
    lines: list[str] = [
        f"# cct Skill Reference (v{SKILL_DOC_VERSION})",
        "",
    ]
    for section in SKILL_SECTIONS:
        lines.append(f"## {section['title']}")
        lines.append("")
        lines.append(section["content"])
        lines.append("")
    return "\n".join(lines)


SKILL_DOC: str = _build_skill_doc()
