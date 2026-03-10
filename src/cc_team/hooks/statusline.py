#!/usr/bin/env python3
"""Statusline: Context Window Monitor for context-relay.

If CCT_SESSION_ID is set, writes per-session usage to relay_paths()/usage.json.
Always renders a colored progress bar to stdout.

Usage: cct _hook statusline
"""

from __future__ import annotations

import os

from cc_team.hooks._common import atomic_write_json, read_hook_input, relay_paths


def _fmt(n: int) -> str:
    """Format token count for display (e.g. 150k, 1.2M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def main() -> None:
    """Statusline hook entry point."""
    data = read_hook_input()

    session_id = data.get("session_id", "")
    if not session_id:
        return

    ctx = data.get("context_window", {})
    current = ctx.get("current_usage", {})
    ctx_size = ctx.get("context_window_size", 200_000)
    input_tok = current.get("input_tokens", 0)
    cache_create = current.get("cache_creation_input_tokens", 0)
    cache_read = current.get("cache_read_input_tokens", 0)
    total_used = input_tok + cache_create + cache_read

    used_pct = ctx.get("used_percentage")
    if used_pct is None:
        used_pct = (total_used / ctx_size * 100) if ctx_size > 0 else 0

    model = data.get("model", {}).get("display_name", "?")
    cost = data.get("cost", {}).get("total_cost_usd", 0)
    agent = data.get("agent", {})
    agent_name = agent.get("name", "")

    # ---- persist per-session usage (only when CCT_SESSION_ID is set) ----
    cct_session_id = os.environ.get("CCT_SESSION_ID", "")
    if cct_session_id:
        paths = relay_paths(cct_session_id)
        atomic_write_json(
            paths["usage"],
            {
                "used_percentage": round(used_pct, 2),
                "context_window_size": ctx_size,
                "total_used_tokens": total_used,
                "session_id": session_id,
                "agent_name": agent_name,
            },
        )

    # ---- render status bar ----
    bar_w = 20
    filled = int(bar_w * min(used_pct, 100) / 100)
    bar = "█" * filled + "░" * (bar_w - filled)

    if used_pct >= 80:
        color = "\033[31m"
    elif used_pct >= 60:
        color = "\033[33m"
    else:
        color = "\033[32m"
    reset = "\033[0m"

    prefix = f"[{agent_name}] " if agent_name else ""
    print(
        f"{prefix}{color}{bar}{reset} {used_pct:.1f}%"
        f" | {_fmt(total_used)}/{_fmt(ctx_size)}"
        f" | ${cost:.3f} | {model}"
    )
