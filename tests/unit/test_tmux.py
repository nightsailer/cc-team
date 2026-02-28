"""Unit tests for tmux.py — TmuxManager verification (mock runner).

Coverage:
- _ENTER_KEY: C-m used in both short and long text paths
- Buffer cleanup: delete-buffer safety net (success/failure/paste error)
- split_window: pane ID return, strict format validation, tmux failure
- kill_pane
- is_pane_alive: display-message based, format validation
- send_command: short text send-keys / long text load-buffer / clear modes
- capture_output
- is_tmux_available
- detect_state: all PaneState values, priority, failure
- Pane ID validation: strict regex matching
- display_message, verify_pane, get_pane_title, notify
- send_enter_with_retry: content change, pattern match, max retries, send failure
- _exec error handling
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cc_team.exceptions import TmuxError
from cc_team.tmux import (
    _ENTER_KEY,
    SEND_KEYS_THRESHOLD,
    ClearMode,
    PaneState,
    TmuxManager,
)

# ── Mock Process Helper ──────────────────────────────────────


def _make_proc(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> asyncio.subprocess.Process:
    """创建 mock Process 对象。"""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


def _make_runner(*results: asyncio.subprocess.Process) -> AsyncMock:
    """创建 mock runner，按序返回多个 Process。"""
    runner = AsyncMock()
    runner.side_effect = list(results)
    return runner


# ── _ENTER_KEY constant ──────────────────────────────────────


class TestEnterKey:
    """Verify C-m is used instead of Enter in both short and long text paths."""

    @pytest.mark.asyncio
    async def test_short_text_uses_c_m(self) -> None:
        """Short text path sends C-m instead of Enter."""
        runner = _make_runner(_make_proc(), _make_proc())
        tmux = TmuxManager(runner=runner)
        await tmux.send_command("%1", "echo hi")

        enter_call = runner.call_args_list[-1][0]
        assert _ENTER_KEY in enter_call
        assert "Enter" not in enter_call

    @pytest.mark.asyncio
    async def test_long_text_uses_c_m(self) -> None:
        """Long text path sends C-m instead of Enter."""
        # load-buffer + paste-buffer + delete-buffer + C-m = 4 calls
        procs = [_make_proc() for _ in range(4)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)
        await tmux.send_command("%1", "x" * SEND_KEYS_THRESHOLD)

        enter_call = runner.call_args_list[-1][0]
        assert _ENTER_KEY in enter_call
        assert "Enter" not in enter_call


# ── Buffer cleanup safety net ────────────────────────────────


class TestBufferCleanup:
    """Verify delete-buffer safety net in _send_keys_long."""

    @pytest.mark.asyncio
    async def test_delete_buffer_called_on_success(self) -> None:
        """delete-buffer is called in finally block even on success."""
        # load-buffer + paste-buffer + delete-buffer = 3 calls (no enter)
        procs = [_make_proc() for _ in range(3)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)
        await tmux.send_command("%1", "x" * SEND_KEYS_THRESHOLD, press_enter=False)

        all_calls = [call[0] for call in runner.call_args_list]
        assert any("delete-buffer" in args for args in all_calls)

    @pytest.mark.asyncio
    async def test_delete_buffer_called_on_paste_failure(self) -> None:
        """delete-buffer is called even when paste-buffer fails."""
        # load-buffer OK + paste-buffer FAIL + delete-buffer OK
        procs = [
            _make_proc(),  # load-buffer
            _make_proc(stderr=b"paste failed", returncode=1),  # paste-buffer
            _make_proc(),  # delete-buffer (safety net)
        ]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        with pytest.raises(TmuxError, match="paste failed"):
            await tmux.send_command(
                "%1", "x" * SEND_KEYS_THRESHOLD, press_enter=False,
            )

        all_calls = [call[0] for call in runner.call_args_list]
        assert any("delete-buffer" in args for args in all_calls)

    @pytest.mark.asyncio
    async def test_delete_buffer_failure_suppressed(self) -> None:
        """delete-buffer failure in finally block is silently suppressed."""
        # load-buffer + paste-buffer + delete-buffer(fails) + C-m = 4 calls
        procs = [
            _make_proc(),  # load-buffer
            _make_proc(),  # paste-buffer
            _make_proc(stderr=b"no buffer", returncode=1),  # delete-buffer
            _make_proc(),  # C-m
        ]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        # Should not raise despite delete-buffer failure
        await tmux.send_command("%1", "x" * SEND_KEYS_THRESHOLD)

        all_calls = [call[0] for call in runner.call_args_list]
        assert any("delete-buffer" in args for args in all_calls)


# ── split_window ─────────────────────────────────────────────


class TestSplitWindow:
    """split_window() 测试。"""

    @pytest.mark.asyncio
    async def test_returns_pane_id(self) -> None:
        """成功时返回 pane ID。"""
        runner = _make_runner(_make_proc(stdout=b"%20\n"))
        tmux = TmuxManager(runner=runner)
        pane_id = await tmux.split_window()
        assert pane_id == "%20"

    @pytest.mark.asyncio
    async def test_with_target_pane(self) -> None:
        """指定 target_pane 时 -t 参数正确传递。"""
        runner = _make_runner(_make_proc(stdout=b"%21\n"))
        tmux = TmuxManager(runner=runner)
        await tmux.split_window(target_pane="%10")

        args = runner.call_args
        cmd_parts = args[0]  # positional args
        assert "-t" in cmd_parts
        t_idx = list(cmd_parts).index("-t")
        assert cmd_parts[t_idx + 1] == "%10"

    @pytest.mark.asyncio
    async def test_unexpected_format_raises(self) -> None:
        """返回值不以 % 开头时抛出 TmuxError。"""
        runner = _make_runner(_make_proc(stdout=b"bad-format\n"))
        tmux = TmuxManager(runner=runner)
        with pytest.raises(TmuxError, match="Unexpected pane ID"):
            await tmux.split_window()

    @pytest.mark.asyncio
    async def test_tmux_failure_raises(self) -> None:
        """tmux 命令失败时抛出 TmuxError。"""
        runner = _make_runner(
            _make_proc(stderr=b"no tmux server", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        with pytest.raises(TmuxError, match="tmux command failed"):
            await tmux.split_window()


# ── kill_pane ────────────────────────────────────────────────


class TestKillPane:
    """kill_pane() 测试。"""

    @pytest.mark.asyncio
    async def test_kill_calls_tmux(self) -> None:
        """kill_pane 调用 tmux kill-pane。"""
        runner = _make_runner(_make_proc())
        tmux = TmuxManager(runner=runner)
        await tmux.kill_pane("%5")

        args = runner.call_args[0]
        assert "kill-pane" in args
        assert "%5" in args


# ── is_pane_alive ────────────────────────────────────────────


class TestIsPaneAlive:
    """is_pane_alive() tests — now uses display-message internally."""

    @pytest.mark.asyncio
    async def test_alive_returns_true(self) -> None:
        """Returns True when display-message returns valid pane ID."""
        runner = _make_runner(_make_proc(stdout=b"%5\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.is_pane_alive("%5") is True

    @pytest.mark.asyncio
    async def test_dead_returns_false(self) -> None:
        """Returns False when display-message fails."""
        runner = _make_runner(
            _make_proc(stderr=b"can't find pane", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        assert await tmux.is_pane_alive("%99") is False

    @pytest.mark.asyncio
    async def test_invalid_format_returns_false(self) -> None:
        """Returns False for invalid pane ID format (no tmux call)."""
        runner = _make_runner()
        tmux = TmuxManager(runner=runner)
        assert await tmux.is_pane_alive("bad-id") is False
        runner.assert_not_called()


# ── send_command ─────────────────────────────────────────────


class TestSendCommand:
    """send_command() 策略分发测试。"""

    @pytest.mark.asyncio
    async def test_short_text_uses_send_keys(self) -> None:
        """短文本使用 send-keys -l 策略。"""
        # 短文本需要 2 次调用: send-keys -l + send-keys Enter
        runner = _make_runner(_make_proc(), _make_proc())
        tmux = TmuxManager(runner=runner)

        short_text = "echo hello"
        assert len(short_text) < SEND_KEYS_THRESHOLD
        await tmux.send_command("%1", short_text)

        # 第一次: send-keys -l
        first_call_args = runner.call_args_list[0][0]
        assert "send-keys" in first_call_args
        assert "-l" in first_call_args
        assert short_text in first_call_args

    @pytest.mark.asyncio
    async def test_short_text_no_enter(self) -> None:
        """press_enter=False 时不发送 Enter。"""
        runner = _make_runner(_make_proc())
        tmux = TmuxManager(runner=runner)
        await tmux.send_command("%1", "text", press_enter=False)

        # 应只有 1 次调用（无 Enter）
        assert runner.call_count == 1

    @pytest.mark.asyncio
    async def test_long_text_uses_load_buffer(self) -> None:
        """Long text uses load-buffer + paste-buffer strategy."""
        # load-buffer + paste-buffer + delete-buffer + C-m = 4 tmux calls
        procs = [_make_proc() for _ in range(4)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        long_text = "x" * SEND_KEYS_THRESHOLD
        await tmux.send_command("%1", long_text)

        all_calls = [call[0] for call in runner.call_args_list]
        assert any("load-buffer" in args for args in all_calls)
        assert any("paste-buffer" in args for args in all_calls)

    @pytest.mark.asyncio
    async def test_long_text_no_escape_by_default(self) -> None:
        """Long text path must never send Escape (triggers zsh vi-mode)."""
        procs = [_make_proc() for _ in range(4)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        await tmux.send_command("%1", "x" * SEND_KEYS_THRESHOLD)

        all_calls = [call[0] for call in runner.call_args_list]
        for call_args in all_calls:
            assert "Escape" not in call_args, f"Escape found in call: {call_args}"

    @pytest.mark.asyncio
    async def test_no_clear_by_default(self) -> None:
        """Default clear_mode=NONE sends no clearing keys."""
        procs = [_make_proc() for _ in range(4)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        await tmux.send_command("%1", "x" * SEND_KEYS_THRESHOLD)

        all_calls = [call[0] for call in runner.call_args_list]
        for call_args in all_calls:
            assert "C-c" not in call_args, "C-c found with NONE clear_mode"
            assert "Escape" not in call_args, "Escape found with NONE clear_mode"

    @pytest.mark.asyncio
    async def test_clear_mode_shell_sends_ctrl_c_ctrl_u(self) -> None:
        """ClearMode.SHELL sends C-c + C-u (safe for bash/zsh)."""
        # C-c + C-u + load-buffer + paste-buffer + delete-buffer + C-m = 6 calls
        procs = [_make_proc() for _ in range(6)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        await tmux.send_command(
            "%1", "x" * SEND_KEYS_THRESHOLD, clear_mode=ClearMode.SHELL,
        )

        all_calls = [call[0] for call in runner.call_args_list]
        assert "C-c" in all_calls[0], f"Expected C-c first, got {all_calls[0]}"
        assert "C-u" in all_calls[1], f"Expected C-u second, got {all_calls[1]}"

    @pytest.mark.asyncio
    async def test_clear_mode_escape_sends_escape(self) -> None:
        """ClearMode.ESCAPE sends Escape (for TUI apps like Claude Code)."""
        # Escape + load-buffer + paste-buffer + delete-buffer + C-m = 5 calls
        procs = [_make_proc() for _ in range(5)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        await tmux.send_command(
            "%1", "x" * SEND_KEYS_THRESHOLD, clear_mode=ClearMode.ESCAPE,
        )

        all_calls = [call[0] for call in runner.call_args_list]
        assert "Escape" in all_calls[0], f"Expected Escape first, got {all_calls[0]}"
        # Must NOT contain C-c
        for call_args in all_calls:
            assert "C-c" not in call_args, "C-c found in ESCAPE mode"

    @pytest.mark.asyncio
    async def test_clear_mode_shell_with_short_text(self) -> None:
        """ClearMode.SHELL also works with short text path."""
        # C-c + C-u + send-keys -l + Enter = 4 tmux calls
        procs = [_make_proc() for _ in range(4)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        await tmux.send_command(
            "%1", "echo hi", clear_mode=ClearMode.SHELL,
        )

        all_calls = [call[0] for call in runner.call_args_list]
        assert "C-c" in all_calls[0]
        assert "C-u" in all_calls[1]
        assert "-l" in all_calls[2]

    @pytest.mark.asyncio
    async def test_multiline_uses_load_buffer(self) -> None:
        """Multiline text uses load-buffer strategy."""
        # load-buffer + paste-buffer + delete-buffer + C-m = 4 tmux calls
        procs = [_make_proc() for _ in range(4)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        multiline_text = "line1\nline2"
        assert len(multiline_text) < SEND_KEYS_THRESHOLD
        await tmux.send_command("%1", multiline_text)

        all_calls = [call[0] for call in runner.call_args_list]
        assert any("load-buffer" in args for args in all_calls)


# ── capture_output ───────────────────────────────────────────


class TestCaptureOutput:
    """capture_output() 测试。"""

    @pytest.mark.asyncio
    async def test_returns_captured_text(self) -> None:
        """返回捕获的输出文本。"""
        runner = _make_runner(_make_proc(stdout=b"$ hello world\n"))
        tmux = TmuxManager(runner=runner)
        output = await tmux.capture_output("%1")
        assert "hello world" in output

    @pytest.mark.asyncio
    async def test_custom_lines_param(self) -> None:
        """自定义行数参数传递正确。"""
        runner = _make_runner(_make_proc(stdout=b""))
        tmux = TmuxManager(runner=runner)
        await tmux.capture_output("%1", lines=100)

        args = runner.call_args[0]
        assert "-100" in args


# ── is_tmux_available ────────────────────────────────────────


class TestIsTmuxAvailable:
    """is_tmux_available() 测试。"""

    def test_available_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """$TMUX 设置时返回 True。"""
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
        assert TmuxManager.is_tmux_available() is True

    def test_unavailable_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """$TMUX 未设置时返回 False。"""
        monkeypatch.delenv("TMUX", raising=False)
        assert TmuxManager.is_tmux_available() is False


# ── detect_state ─────────────────────────────────────────────


class TestDetectState:
    """detect_state() state detection tests."""

    @pytest.mark.asyncio
    async def test_active_thinking(self) -> None:
        """Detect ACTIVE when output contains 'Thinking'."""
        runner = _make_runner(_make_proc(stdout=b"Thinking about...\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%1") == PaneState.ACTIVE

    @pytest.mark.asyncio
    async def test_active_running(self) -> None:
        """Detect ACTIVE when output contains 'Running\u2026'."""
        runner = _make_runner(_make_proc(stdout="Running\u2026 tests\n".encode()))
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%1") == PaneState.ACTIVE

    @pytest.mark.asyncio
    async def test_active_esc_to_interrupt(self) -> None:
        """Detect ACTIVE when output contains 'esc to interrupt'."""
        runner = _make_runner(_make_proc(stdout=b"Processing esc to interrupt\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%1") == PaneState.ACTIVE

    @pytest.mark.asyncio
    async def test_waiting_input(self) -> None:
        """Detect WAITING_INPUT for queued message prompt."""
        runner = _make_runner(
            _make_proc(stdout=b"Press up to edit queued messages\n")
        )
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%1") == PaneState.WAITING_INPUT

    @pytest.mark.asyncio
    async def test_waiting_takes_priority_over_active(self) -> None:
        """WAITING_INPUT takes priority over ACTIVE when both match."""
        output = b"Thinking... Press up to edit queued messages\n"
        runner = _make_runner(_make_proc(stdout=output))
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%1") == PaneState.WAITING_INPUT

    @pytest.mark.asyncio
    async def test_ready_dollar_prompt(self) -> None:
        """Detect READY when output ends with $ prompt."""
        runner = _make_runner(_make_proc(stdout=b"user@host:~$ \n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%1") == PaneState.READY

    @pytest.mark.asyncio
    async def test_idle(self) -> None:
        """Detect IDLE when no patterns match."""
        runner = _make_runner(_make_proc(stdout=b"some random output\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%1") == PaneState.IDLE

    @pytest.mark.asyncio
    async def test_unknown_on_empty_output(self) -> None:
        """Return UNKNOWN when capture returns empty string."""
        runner = _make_runner(_make_proc(stdout=b""))
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%1") == PaneState.UNKNOWN

    @pytest.mark.asyncio
    async def test_unknown_on_capture_failure(self) -> None:
        """Return UNKNOWN when capture raises TmuxError."""
        runner = _make_runner(
            _make_proc(stderr=b"can't find pane", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        assert await tmux.detect_state("%99") == PaneState.UNKNOWN


# ── Pane ID validation ──────────────────────────────────────


class TestPaneIdValidation:
    """Verify strict pane ID format validation in split_window."""

    @pytest.mark.asyncio
    async def test_valid_pane_id(self) -> None:
        """Accept valid pane ID like %20."""
        runner = _make_runner(_make_proc(stdout=b"%20\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.split_window() == "%20"

    @pytest.mark.asyncio
    async def test_rejects_percent_without_digits(self) -> None:
        """Reject pane ID that is just '%' with no digits."""
        runner = _make_runner(_make_proc(stdout=b"%\n"))
        tmux = TmuxManager(runner=runner)
        with pytest.raises(TmuxError, match="Unexpected pane ID"):
            await tmux.split_window()

    @pytest.mark.asyncio
    async def test_rejects_percent_with_alpha(self) -> None:
        """Reject pane ID with letters like %abc."""
        runner = _make_runner(_make_proc(stdout=b"%abc\n"))
        tmux = TmuxManager(runner=runner)
        with pytest.raises(TmuxError, match="Unexpected pane ID"):
            await tmux.split_window()

    @pytest.mark.asyncio
    async def test_rejects_no_percent_prefix(self) -> None:
        """Reject pane ID without % prefix."""
        runner = _make_runner(_make_proc(stdout=b"20\n"))
        tmux = TmuxManager(runner=runner)
        with pytest.raises(TmuxError, match="Unexpected pane ID"):
            await tmux.split_window()


# ── display_message ──────────────────────────────────────────


class TestDisplayMessage:
    """display_message() tests."""

    @pytest.mark.asyncio
    async def test_returns_value(self) -> None:
        """Returns stripped output on success."""
        runner = _make_runner(_make_proc(stdout=b"%5\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.display_message("%5", "#{pane_id}") == "%5"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self) -> None:
        """Returns None when tmux command fails."""
        runner = _make_runner(
            _make_proc(stderr=b"no pane", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        assert await tmux.display_message("%99", "#{pane_id}") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty(self) -> None:
        """Returns None when output is empty."""
        runner = _make_runner(_make_proc(stdout=b"  \n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.display_message("%5", "#{pane_id}") is None


# ── verify_pane ─────────────────────────────────────────────


class TestVerifyPane:
    """verify_pane() tests."""

    @pytest.mark.asyncio
    async def test_valid_pane(self) -> None:
        """Returns True for valid, alive pane."""
        runner = _make_runner(_make_proc(stdout=b"%5\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.verify_pane("%5") is True

    @pytest.mark.asyncio
    async def test_invalid_format(self) -> None:
        """Returns False for invalid pane ID format."""
        runner = _make_runner()
        tmux = TmuxManager(runner=runner)
        assert await tmux.verify_pane("bad") is False

    @pytest.mark.asyncio
    async def test_dead_pane(self) -> None:
        """Returns False when display-message fails."""
        runner = _make_runner(
            _make_proc(stderr=b"no pane", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        assert await tmux.verify_pane("%99") is False


# ── get_pane_title ──────────────────────────────────────────


class TestGetPaneTitle:
    """get_pane_title() tests."""

    @pytest.mark.asyncio
    async def test_returns_title(self) -> None:
        """Returns pane title string."""
        runner = _make_runner(_make_proc(stdout=b"my-pane-title\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.get_pane_title("%5") == "my-pane-title"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self) -> None:
        """Returns None when pane does not exist."""
        runner = _make_runner(
            _make_proc(stderr=b"no pane", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        assert await tmux.get_pane_title("%99") is None


# ── notify ──────────────────────────────────────────────────


class TestNotify:
    """notify() tests."""

    @pytest.mark.asyncio
    async def test_calls_display_message(self) -> None:
        """Sends display-message with duration."""
        runner = _make_runner(_make_proc())
        tmux = TmuxManager(runner=runner)
        await tmux.notify("hello")

        args = runner.call_args[0]
        assert "display-message" in args
        assert "-d" in args
        assert "5000" in args
        assert "hello" in args

    @pytest.mark.asyncio
    async def test_failure_is_suppressed(self) -> None:
        """Failure in display-message does not raise."""
        runner = _make_runner(
            _make_proc(stderr=b"no server", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        # Should not raise
        await tmux.notify("test")


# ── send_enter_with_retry ────────────────────────────────────


class TestSendEnterWithRetry:
    """send_enter_with_retry() tests."""

    @pytest.mark.asyncio
    async def test_success_on_content_change(self) -> None:
        """Returns True when content changes after pressing Enter."""
        # send-keys C-m + capture-pane = 2 calls
        runner = _make_runner(
            _make_proc(),  # send-keys C-m
            _make_proc(stdout=b"new output\n"),  # capture-pane
        )
        tmux = TmuxManager(runner=runner)
        result = await tmux.send_enter_with_retry(
            "%1", "old output", retry_delay=0,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_success_on_processing_pattern(self) -> None:
        """Returns True when _PROCESSING_RE matches (even if content same)."""
        content = "Thinking about the problem"
        runner = _make_runner(
            _make_proc(),  # send-keys C-m
            _make_proc(stdout=content.encode()),  # capture-pane
        )
        tmux = TmuxManager(runner=runner)
        # Pass the same content as before — should still succeed via regex
        result = await tmux.send_enter_with_retry(
            "%1", content, retry_delay=0,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_failure_after_max_retries(self) -> None:
        """Returns False when content never changes after max retries."""
        same_content = b"unchanged content\n"
        # Each retry: send-keys + capture = 2 calls, 3 retries = 6 calls
        procs = []
        for _ in range(3):
            procs.append(_make_proc())  # send-keys C-m
            procs.append(_make_proc(stdout=same_content))  # capture-pane
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)
        result = await tmux.send_enter_with_retry(
            "%1", "unchanged content\n", max_retries=3, retry_delay=0,
        )
        assert result is False
        # Verify all retries were attempted
        assert runner.call_count == 6

    @pytest.mark.asyncio
    async def test_send_failure_retries(self) -> None:
        """Retries when send-keys fails, succeeds on next attempt."""
        # Attempt 1: send-keys FAIL
        # Attempt 2: send-keys OK + capture (changed)
        procs = [
            _make_proc(stderr=b"send failed", returncode=1),  # send fail
            _make_proc(),  # send-keys C-m (retry)
            _make_proc(stdout=b"new output\n"),  # capture-pane
        ]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)
        result = await tmux.send_enter_with_retry(
            "%1", "old output", retry_delay=0,
        )
        assert result is True


# ── _exec 错误处理 ───────────────────────────────────────────


class TestExecError:
    """_exec() 错误场景。"""

    @pytest.mark.asyncio
    async def test_nonzero_exit_includes_stderr(self) -> None:
        """非零退出码时 TmuxError 包含 stderr 内容。"""
        runner = _make_runner(
            _make_proc(stderr=b"session not found", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        with pytest.raises(TmuxError, match="session not found"):
            await tmux._exec(["tmux", "list-sessions"])

    @pytest.mark.asyncio
    async def test_nonzero_exit_no_stderr(self) -> None:
        """非零退出码但无 stderr 时不报 stderr 内容。"""
        runner = _make_runner(_make_proc(returncode=2))
        tmux = TmuxManager(runner=runner)
        with pytest.raises(TmuxError, match="exit 2"):
            await tmux._exec(["tmux", "test"])
