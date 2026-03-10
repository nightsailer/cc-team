"""Per-mode handoff templates and configurable relay prompt construction.

Provides:
- get_handoff_template(mode): stop hook prompt template per mode
- get_relay_prompt(context, content): builds the injection prompt for relay
"""

from __future__ import annotations

import os

from cc_team._relay_context import RelayContext, RelayMode

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


def get_relay_prompt(context: RelayContext, content: str) -> str:
    """Build the relay injection prompt from handoff content.

    Priority: CCT_RELAY_PROMPT_TEMPLATE env var > default template.
    The template receives ``{content}`` as a format variable.
    """
    template = os.environ.get("CCT_RELAY_PROMPT_TEMPLATE")
    if template:
        return template.format(content=content)
    return _DEFAULT_RELAY_PROMPT.format(content=content)
