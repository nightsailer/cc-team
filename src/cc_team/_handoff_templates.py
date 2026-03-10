"""Per-mode handoff templates and configurable relay prompt construction.

Provides:
- get_handoff_template(mode): stop hook prompt template per mode
- get_relay_prompt(content): builds the injection prompt for relay
"""

from __future__ import annotations

import os

from cc_team._relay_context import RelayMode

# Default handoff templates per mode.
# These are the prompts shown by the stop hook when asking the user to
# write a handoff file before the context relay.

_TEMPLATES: dict[RelayMode, str] = {
    RelayMode.STANDALONE: (
        "Your context window is nearly full. Please write a handoff document to "
        "``{handoff_path}`` summarizing:\n"
        "- Current task and progress\n"
        "- Key decisions made\n"
        "- Next steps and open questions\n"
        "This will be passed to your next session automatically."
    ),
    RelayMode.TEAM_LEAD: (
        "Your context window is nearly full. Please write a handoff document to "
        "``{handoff_path}`` summarizing:\n"
        "- Team status and active members\n"
        "- Current task assignments and progress\n"
        "- Coordination decisions and blockers\n"
        "- Next steps for the team\n"
        "This will be passed to the next team lead session."
    ),
    RelayMode.TEAMMATE: (
        "Your context window is nearly full. Please write a handoff document to "
        "``{handoff_path}`` summarizing:\n"
        "- Your assigned task and current progress\n"
        "- Files modified and key changes\n"
        "- Remaining work and blockers\n"
        "This will be passed to your replacement agent."
    ),
}

# Default relay prompt template — wraps handoff content for injection.
_DEFAULT_RELAY_PROMPT = (
    "[Context Relay] Handoff from previous session.\n"
    "---\n"
    "{content}\n"
    "---\n"
    "Continue working based on the above context."
)


def get_handoff_template(mode: RelayMode) -> str:
    """Return the stop hook prompt template for the given mode."""
    return _TEMPLATES[mode]


def get_relay_prompt(content: str, *, source_path: str = "") -> str:
    """Build the relay injection prompt from handoff content.

    Priority: CCT_RELAY_PROMPT_TEMPLATE env var > default template.
    Uses str.replace to avoid format-string injection from handoff content.

    Args:
        content: The handoff text to embed.
        source_path: Optional path to the handoff file (included as Source line).
    """
    template = os.environ.get("CCT_RELAY_PROMPT_TEMPLATE")
    if not template:
        template = _DEFAULT_RELAY_PROMPT
    result = template.replace("{content}", content)
    if source_path:
        # Insert source line after the header.
        result = result.replace(
            "[Context Relay] Handoff from previous session.\n",
            f"[Context Relay] Handoff from previous session.\nSource: {source_path}\n",
        )
    return result
