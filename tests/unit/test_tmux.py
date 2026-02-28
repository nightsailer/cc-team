"""tmux.py 单元测试 — TmuxManager 验证（mock runner）。

测试覆盖:
- split_window（返回 pane ID / 异常格式 / tmux 失败）
- kill_pane
- is_pane_alive（存在 / 不存在）
- send_command（短文本 send-keys / 长文本 load-buffer 策略）
- capture_output
- is_tmux_available
- _exec 错误处理
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cc_team.exceptions import TmuxError
from cc_team.tmux import SEND_KEYS_THRESHOLD, TmuxManager

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
    """is_pane_alive() 测试。"""

    @pytest.mark.asyncio
    async def test_alive_returns_true(self) -> None:
        """pane 存在时返回 True。"""
        runner = _make_runner(_make_proc(stdout=b"%5: [80x24]\n"))
        tmux = TmuxManager(runner=runner)
        assert await tmux.is_pane_alive("%5") is True

    @pytest.mark.asyncio
    async def test_dead_returns_false(self) -> None:
        """pane 不存在时返回 False。"""
        runner = _make_runner(
            _make_proc(stderr=b"can't find pane", returncode=1)
        )
        tmux = TmuxManager(runner=runner)
        assert await tmux.is_pane_alive("%99") is False


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
        """长文本使用 load-buffer + paste-buffer 策略。"""
        # 长文本需要: Escape + load-buffer + paste-buffer + Enter = 4 次 tmux 调用
        procs = [_make_proc() for _ in range(4)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        long_text = "x" * SEND_KEYS_THRESHOLD  # 刚好到达阈值
        await tmux.send_command("%1", long_text)

        # 验证 load-buffer 被调用
        all_calls = [call[0] for call in runner.call_args_list]
        load_buffer_found = any("load-buffer" in args for args in all_calls)
        paste_buffer_found = any("paste-buffer" in args for args in all_calls)
        assert load_buffer_found
        assert paste_buffer_found

    @pytest.mark.asyncio
    async def test_multiline_uses_load_buffer(self) -> None:
        """含换行符的文本使用 load-buffer 策略。"""
        procs = [_make_proc() for _ in range(4)]
        runner = _make_runner(*procs)
        tmux = TmuxManager(runner=runner)

        multiline_text = "line1\nline2"  # 短但含换行
        assert len(multiline_text) < SEND_KEYS_THRESHOLD
        await tmux.send_command("%1", multiline_text)

        all_calls = [call[0] for call in runner.call_args_list]
        load_buffer_found = any("load-buffer" in args for args in all_calls)
        assert load_buffer_found


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
