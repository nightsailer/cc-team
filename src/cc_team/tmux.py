"""tmux 操作封装。

封装 Claude Code 原生使用的 tmux 操作:
- split-window: 创建新 pane
- send-keys: 发送命令（短文本）
- load-buffer + paste-buffer: 发送长命令
- kill-pane: 销毁 pane
- capture-pane: 捕获输出
- list-panes: 检查 pane 存活

可测试性: 接受 runner 注入，CI 中用 mock 替代真实 tmux。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from typing import Any, Callable, Coroutine

from cc_team.exceptions import TmuxError

# Runner 类型: 兼容 asyncio.create_subprocess_exec 签名
Runner = Callable[..., Coroutine[Any, Any, asyncio.subprocess.Process]]

# 短文本阈值: tmux/zsh 固有限制
SEND_KEYS_THRESHOLD = 200


class TmuxManager:
    """tmux 操作封装，匹配 Claude Code 原生行为。

    Args:
        runner: 自定义命令执行函数（默认 asyncio.create_subprocess_exec）
    """

    def __init__(self, *, runner: Runner | None = None) -> None:
        self._run: Runner = runner or asyncio.create_subprocess_exec

    # ── Pane 管理 ───────────────────────────────────────────

    async def split_window(self, *, target_pane: str | None = None) -> str:
        """创建新 pane，返回 pane ID (如 %20)。

        Args:
            target_pane: 在哪个 pane 旁边分割（默认当前 pane）

        Returns:
            新 pane 的 ID

        Raises:
            TmuxError: tmux 命令失败
        """
        cmd = ["tmux", "split-window", "-d", "-P", "-F", "#{pane_id}"]
        if target_pane:
            cmd.extend(["-t", target_pane])
        stdout = await self._exec(cmd)
        pane_id = stdout.strip()
        if not pane_id.startswith("%"):
            raise TmuxError(f"Unexpected pane ID format: {pane_id!r}")
        return pane_id

    async def kill_pane(self, pane_id: str) -> None:
        """销毁 pane。"""
        await self._exec(["tmux", "kill-pane", "-t", pane_id])

    async def is_pane_alive(self, pane_id: str) -> bool:
        """检查 pane 是否存在。"""
        try:
            await self._exec(["tmux", "list-panes", "-t", pane_id])
            return True
        except TmuxError:
            return False

    # ── 命令发送 ────────────────────────────────────────────

    async def send_command(
        self,
        pane_id: str,
        text: str,
        *,
        press_enter: bool = True,
    ) -> None:
        """发送命令到 pane，自动选择最佳策略。

        短文本 (<200字符且无换行): tmux send-keys -l
        长文本 (>=200字符或含换行): tmux load-buffer + paste-buffer

        Args:
            pane_id: 目标 pane ID
            text: 要发送的文本
            press_enter: 是否在末尾按 Enter
        """
        if len(text) < SEND_KEYS_THRESHOLD and "\n" not in text:
            await self._send_keys_short(pane_id, text, press_enter=press_enter)
        else:
            await self._send_keys_long(pane_id, text, press_enter=press_enter)

    async def _send_keys_short(
        self, pane_id: str, text: str, *, press_enter: bool
    ) -> None:
        """短文本: tmux send-keys -l（字面模式）。"""
        await self._exec(["tmux", "send-keys", "-t", pane_id, "-l", text])
        if press_enter:
            await self._exec(["tmux", "send-keys", "-t", pane_id, "Enter"])

    async def _send_keys_long(
        self, pane_id: str, text: str, *, press_enter: bool
    ) -> None:
        """长文本: load-buffer + paste-buffer 策略。

        完整命令序列:
        1. send-keys Escape（清除部分输入）
        2. sleep(100ms)
        3. 写入临时文件
        4. load-buffer -b {命名缓冲区} {tmpFile}
        5. paste-buffer -b {命名缓冲区} -d -t {pane}
        6. 清理临时文件
        7. sleep(适当延迟)
        8. send-keys Enter（如果 press_enter）
        """
        # 1. 清除部分输入
        await self._exec(["tmux", "send-keys", "-t", pane_id, "Escape"])

        # 2. 短暂延迟
        await asyncio.sleep(0.1)

        # 3. 写入临时文件
        fd, tmp_path = tempfile.mkstemp(prefix="cc-team-", suffix=".txt")
        try:
            try:
                os.write(fd, text.encode("utf-8"))
            finally:
                os.close(fd)
            os.chmod(tmp_path, 0o600)

            # 4. load-buffer（命名缓冲区防竞态，uuid 保证唯一性）
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

            # 5. paste-buffer（-d 自动删除缓冲区）
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
            # 6. 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        # 7. 适当延迟
        await asyncio.sleep(0.05)

        # 8. 按 Enter
        if press_enter:
            await self._exec(["tmux", "send-keys", "-t", pane_id, "Enter"])

    # ── 输出捕获 ────────────────────────────────────────────

    async def capture_output(self, pane_id: str, *, lines: int = 50) -> str:
        """捕获 pane 输出。

        Args:
            pane_id: 目标 pane ID
            lines: 捕获行数

        Returns:
            捕获的文本
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

    # ── 环境检测 ────────────────────────────────────────────

    @staticmethod
    def is_tmux_available() -> bool:
        """检查是否在 tmux 环境中（$TMUX 环境变量）。"""
        return bool(os.environ.get("TMUX"))

    # ── 内部辅助 ────────────────────────────────────────────

    async def _exec(self, cmd: list[str]) -> str:
        """执行 tmux 命令，返回 stdout。

        Raises:
            TmuxError: 命令退出码非零
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
