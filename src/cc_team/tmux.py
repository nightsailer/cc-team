"""Tmux operation wrapper for Claude Code multi-agent orchestration.

Encapsulates tmux operations used by Claude Code:
- split-window: Create new pane
- send-keys / load-buffer + paste-buffer: Send commands (short/long text)
- kill-pane: Destroy pane
- capture-pane: Capture output
- display-message: Query pane properties, verify liveness, notifications
- PaneState detection: Detect pane activity via regex matching
- send_enter_with_retry: Reliable Enter key delivery with verification

Key reliability features (backported from tmux_helper.py):
- C-m instead of "Enter" to avoid zsh vi-mode issues
- Named buffer + delete-buffer safety net to prevent buffer leaks
- Strict pane ID validation via _PANE_ID_RE regex

Testability: accepts runner injection; use mock in CI.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import os
import re
import tempfile
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from cc_team.exceptions import TmuxError


class ClearMode(enum.Enum):
    """Strategy for clearing partial input before pasting text.

    NONE:   No clearing — safe for fresh panes with no prior input.
    ESCAPE: Send Escape — appropriate for TUI apps (e.g. Claude Code input)
            where Escape exits the current mode.  NOT safe for zsh, which
            enters vi-command-mode and swallows the first pasted character.
    SHELL:  Send C-c + C-u — appropriate for shell prompts (bash/zsh).
            NOT safe for TUI apps where C-c may cancel an operation.
    """

    NONE = "none"
    ESCAPE = "escape"
    SHELL = "shell"


# Runner type: compatible with asyncio.create_subprocess_exec signature
Runner = Callable[..., Coroutine[Any, Any, asyncio.subprocess.Process]]

# tmux Enter key: use C-m instead of "Enter" to avoid misinterpretation
# in certain terminal/shell combinations (e.g. zsh vi-mode).
_ENTER_KEY = "C-m"

# Short text threshold: tmux/zsh intrinsic limit
SEND_KEYS_THRESHOLD = 200

# ── State detection regexes ──────────────────────────────────

# Claude Code is actively processing (Thinking, Running, etc.)
_PROCESSING_RE = re.compile(
    r"Thinking|Running…|esc to interrupt|⏺",
    re.IGNORECASE,
)
# Claude Code is waiting for queued message input
_WAITING_RE = re.compile(
    r"Press up to edit queued messages",
    re.IGNORECASE,
)
# Shell prompt ready (❯, $, ⏵ at end of line)
_READY_RE = re.compile(r"[❯$⏵]\s*$", re.MULTILINE)
# Strict pane ID format: %<digits>
_PANE_ID_RE = re.compile(r"^%\d+$")


class PaneState(enum.Enum):
    """Tmux pane activity state detected from captured output."""

    ACTIVE = "active"  # Matches _PROCESSING_RE (Thinking/Running/⏺)
    READY = "ready"  # Matches prompt (❯ / $ / ⏵)
    WAITING_INPUT = "waiting"  # Matches "Press up to edit queued messages"
    IDLE = "idle"  # No pattern matched
    UNKNOWN = "unknown"  # Capture failed


class TmuxManager:
    """Tmux operation wrapper matching Claude Code native behavior.

    Args:
        runner: Custom command executor (defaults to asyncio.create_subprocess_exec)
    """

    def __init__(self, *, runner: Runner | None = None) -> None:
        self._run: Runner = runner or asyncio.create_subprocess_exec

    # ── Pane management ────────────────────────────────────────

    async def split_window(self, *, target_pane: str | None = None) -> str:
        """Create a new pane, return pane ID (e.g. %20).

        Args:
            target_pane: Split next to this pane (default: current pane)

        Returns:
            The new pane ID

        Raises:
            TmuxError: tmux command failed
        """
        cmd = ["tmux", "split-window", "-d", "-P", "-F", "#{pane_id}"]
        if target_pane:
            cmd.extend(["-t", target_pane])
        stdout = await self._exec(cmd)
        pane_id = stdout.strip()
        if not _PANE_ID_RE.match(pane_id):
            raise TmuxError(f"Unexpected pane ID format: {pane_id!r}")
        return pane_id

    async def kill_pane(self, pane_id: str) -> None:
        """Destroy a pane."""
        await self._exec(["tmux", "kill-pane", "-t", pane_id])

    async def is_pane_alive(self, pane_id: str) -> bool:
        """Check if a pane exists using display-message (lightweight).

        Validates pane_id format first, then queries via display-message.
        """
        if not _PANE_ID_RE.match(pane_id):
            return False
        return await self.display_message(pane_id, "#{pane_id}") is not None

    # ── display-message queries ──────────────────────────────

    async def display_message(self, pane_id: str, fmt: str) -> str | None:
        """Query pane property via tmux display-message -p.

        Returns the stripped output, or None if the command fails.
        """
        try:
            stdout = await self._exec(["tmux", "display-message", "-t", pane_id, "-p", fmt])
            value = stdout.strip()
            return value if value else None
        except TmuxError:
            return None

    async def verify_pane(self, pane_id: str) -> bool:
        """Validate pane ID format and verify it is alive via display-message."""
        if not _PANE_ID_RE.match(pane_id):
            return False
        return await self.display_message(pane_id, "#{pane_id}") is not None

    async def get_pane_title(self, pane_id: str) -> str | None:
        """Get the title of a pane, or None if unavailable."""
        return await self.display_message(pane_id, "#{pane_title}")

    async def notify(self, message: str, *, duration_ms: int = 5000) -> None:
        """Send a notification via tmux display-message. Silently fails."""
        with contextlib.suppress(TmuxError):
            await self._exec(["tmux", "display-message", "-d", str(duration_ms), message])

    async def notify_pane(
        self,
        pane_id: str,
        message: str,
        *,
        verify_enter: bool = False,
    ) -> None:
        """Send text to a pane and show a notification. Silently fails.

        Args:
            pane_id: Target pane
            message: Text to send
            verify_enter: If True, use send_enter_with_retry to confirm
                the Enter key was accepted.
        """
        # Suppress all exceptions: this method is fire-and-forget.
        with contextlib.suppress(Exception):
            if verify_enter:
                content_before = await self.capture_output(pane_id)
                await self.send_command(pane_id, message, press_enter=False)
                await self.send_enter_with_retry(pane_id, content_before)
            else:
                await self.send_command(pane_id, message)

            await self.notify(message[:SEND_KEYS_THRESHOLD])

    # ── Command sending ────────────────────────────────────────

    async def send_command(
        self,
        pane_id: str,
        text: str,
        *,
        press_enter: bool = True,
        clear_mode: ClearMode = ClearMode.NONE,
    ) -> None:
        """Send text to a pane, choosing the best strategy automatically.

        Short text (<200 chars, no newlines): tmux send-keys -l
        Long text (>=200 chars or newlines): tmux load-buffer + paste-buffer

        Args:
            pane_id: Target pane ID
            text: Text to send
            press_enter: Whether to press Enter after the text
            clear_mode: How to clear partial input before sending.
                NONE:   No clearing (default, safe for fresh panes).
                ESCAPE: Send Escape (for TUI apps like Claude Code).
                SHELL:  Send C-c + C-u (for shell prompts; zsh-safe).
        """
        if clear_mode == ClearMode.ESCAPE:
            await self._clear_with_escape(pane_id)
        elif clear_mode == ClearMode.SHELL:
            await self._clear_shell_input(pane_id)

        if len(text) < SEND_KEYS_THRESHOLD and "\n" not in text:
            await self._send_keys_short(pane_id, text, press_enter=press_enter)
        else:
            await self._send_keys_long(pane_id, text, press_enter=press_enter)

    async def _send_keys_short(self, pane_id: str, text: str, *, press_enter: bool) -> None:
        """Short text: tmux send-keys -l (literal mode)."""
        await self._exec(["tmux", "send-keys", "-t", pane_id, "-l", text])
        if press_enter:
            await self._exec(["tmux", "send-keys", "-t", pane_id, _ENTER_KEY])

    async def _clear_with_escape(self, pane_id: str) -> None:
        """Clear input by sending Escape.

        Appropriate for TUI apps (e.g. Claude Code input box).
        NOT safe for zsh — Escape enters vi-command-mode and the next
        pasted character is consumed as a vi command.
        """
        await self._exec(["tmux", "send-keys", "-t", pane_id, "Escape"])
        await asyncio.sleep(0.1)

    async def _clear_shell_input(self, pane_id: str) -> None:
        """Clear partial input in a shell pane with C-c + C-u.

        Safe for bash/zsh in both emacs and vi mode.
        NOT safe for TUI apps where C-c may cancel an operation.
        """
        await self._exec(["tmux", "send-keys", "-t", pane_id, "C-c"])
        await self._exec(["tmux", "send-keys", "-t", pane_id, "C-u"])
        await asyncio.sleep(0.1)

    async def _send_keys_long(self, pane_id: str, text: str, *, press_enter: bool) -> None:
        """Long text: load-buffer + paste-buffer strategy.

        Sequence:
        1. Write text to temp file
        2. load-buffer -b {named buffer} {tmpFile}
        3. paste-buffer -b {named buffer} -d -t {pane}
        4. Clean up temp file
        5. Brief delay
        6. send-keys Enter (if press_enter)
        """
        # 1. Write to temp file
        fd, tmp_path = tempfile.mkstemp(prefix="cc-team-", suffix=".txt")
        # Declare buf_name before try so finally can reference it
        buf_name: str | None = None
        try:
            try:
                os.write(fd, text.encode("utf-8"))
            finally:
                os.close(fd)
            os.chmod(tmp_path, 0o600)

            # 2. load-buffer (named buffer to prevent races, uuid for uniqueness)
            buf_name = f"cc-team-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            await self._exec(
                [
                    "tmux",
                    "load-buffer",
                    "-b",
                    buf_name,
                    tmp_path,
                ]
            )

            # 3. paste-buffer (-d auto-deletes buffer on success)
            await self._exec(
                [
                    "tmux",
                    "paste-buffer",
                    "-b",
                    buf_name,
                    "-d",
                    "-t",
                    pane_id,
                ]
            )
        finally:
            # 4. Clean up temp file
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            # 5. Safety net: delete buffer if paste-buffer failed/was interrupted
            # (-d flag normally removes it, but we ensure no leak)
            if buf_name is not None:
                with contextlib.suppress(TmuxError):
                    await self._exec(["tmux", "delete-buffer", "-b", buf_name])

        # 6. Brief delay for paste to take effect
        await asyncio.sleep(0.05)

        # 7. Press Enter
        if press_enter:
            await self._exec(["tmux", "send-keys", "-t", pane_id, _ENTER_KEY])

    # ── Output capture ─────────────────────────────────────────

    async def capture_output(self, pane_id: str, *, lines: int = 50) -> str:
        """Capture pane output.

        Args:
            pane_id: Target pane ID
            lines: Number of lines to capture

        Returns:
            Captured text
        """
        start_line = f"-{lines}"
        return await self._exec(
            [
                "tmux",
                "capture-pane",
                "-t",
                pane_id,
                "-p",
                "-S",
                start_line,
            ]
        )

    # ── State detection ────────────────────────────────────────

    async def detect_state(self, pane_id: str) -> PaneState:
        """Detect pane activity state from captured output.

        Priority: WAITING_INPUT > ACTIVE > READY > IDLE.
        Returns UNKNOWN if capture fails.
        """
        try:
            content = await self.capture_output(pane_id)
        except TmuxError:
            return PaneState.UNKNOWN
        if not content:
            return PaneState.UNKNOWN
        if _WAITING_RE.search(content):
            return PaneState.WAITING_INPUT
        if _PROCESSING_RE.search(content):
            return PaneState.ACTIVE
        if _READY_RE.search(content):
            return PaneState.READY
        return PaneState.IDLE

    # ── Retry helpers ─────────────────────────────────────────

    async def send_enter_with_retry(
        self,
        pane_id: str,
        content_before: str,
        *,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> bool:
        """Send Enter (C-m) and verify the pane accepted it.

        Compares captured output after sending Enter with *content_before*.
        Success if the content changed OR _PROCESSING_RE matches.

        Args:
            pane_id: Target pane
            content_before: Captured output before pressing Enter
            max_retries: Maximum number of attempts
            retry_delay: Seconds to wait between retries

        Returns:
            True if Enter was accepted, False after all retries exhausted.
        """
        last = max_retries - 1
        for attempt in range(max_retries):
            try:
                await self._exec(["tmux", "send-keys", "-t", pane_id, _ENTER_KEY])
            except TmuxError:
                if attempt < last:
                    await asyncio.sleep(retry_delay)
                continue

            await asyncio.sleep(retry_delay)

            try:
                content_after = await self.capture_output(pane_id)
            except TmuxError:
                continue

            if content_after != content_before or _PROCESSING_RE.search(content_after):
                return True
        return False

    # ── Environment detection ────────────────────────────────

    @staticmethod
    def is_tmux_available() -> bool:
        """Check if running inside tmux ($TMUX env var)."""
        return bool(os.environ.get("TMUX"))

    # ── Internal helpers ────────────────────────────────────────

    async def _exec(self, cmd: list[str]) -> str:
        """Execute a tmux command, return stdout.

        Raises:
            TmuxError: Non-zero exit code
        """
        proc = await self._run(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
            raise TmuxError(
                f"tmux command failed (exit {proc.returncode}): "
                f"{' '.join(cmd)}"
                f"{': ' + err_msg if err_msg else ''}"
            )
        return stdout.decode("utf-8", errors="replace") if stdout else ""
