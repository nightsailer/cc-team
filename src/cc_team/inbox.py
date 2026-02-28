"""Inbox 文件 I/O。

负责:
- 写入消息到 inbox 文件（追加到 JSON 数组）
- 读取未读消息
- 标记消息已读
- 写入初始 prompt

所有写操作通过 per-inbox 文件锁保护。
路径: ~/.claude/teams/{team_name}/inboxes/{agent_name}.json
"""

from __future__ import annotations

from pathlib import Path

from cc_team import paths
from cc_team._serialization import (
    atomic_write_json,
    inbox_message_from_dict,
    inbox_message_to_dict,
    now_iso,
    read_json,
)
from cc_team.filelock import FileLock
from cc_team.types import InboxMessage


class InboxIO:
    """Inbox 文件读写管理器。

    每个实例绑定到一个团队 + Agent 名称。
    """

    def __init__(self, team_name: str, agent_name: str) -> None:
        self._team_name = team_name
        self._agent_name = agent_name
        self._inbox_path = paths.inbox_path(team_name, agent_name)
        self._lock = FileLock(paths.inbox_lock_path(team_name, agent_name))

    @property
    def inbox_path(self) -> Path:
        return self._inbox_path

    # ── 写入 ────────────────────────────────────────────────

    async def write(self, message: InboxMessage) -> None:
        """追加消息到 inbox 文件。

        如果 inbox 文件不存在则创建新文件。
        """
        async with self._lock.acquire():
            messages = self._read_raw()
            messages.append(inbox_message_to_dict(message))
            self._write_raw(messages)

    async def write_initial_prompt(self, from_name: str, prompt: str) -> None:
        """写入初始 prompt 作为第一条消息。

        协议要求: 无 summary 和 color 字段。
        """
        msg = InboxMessage(
            from_=from_name,
            text=prompt,
            timestamp=now_iso(),
            read=False,
        )
        async with self._lock.acquire():
            # 初始 prompt 创建新文件（覆盖）
            self._write_raw([inbox_message_to_dict(msg)])

    # ── 读取 ────────────────────────────────────────────────

    def read_all(self) -> list[InboxMessage]:
        """读取所有消息。"""
        raw = self._read_raw()
        return [inbox_message_from_dict(d) for d in raw]

    def read_unread(self) -> list[InboxMessage]:
        """读取所有未读消息（不标记已读）。"""
        return [m for m in self.read_all() if not m.read]

    async def mark_read(self) -> list[InboxMessage]:
        """标记所有未读消息为已读，返回刚标记的消息列表。

        原子操作: 读取 → 标记 → 写回。
        """
        async with self._lock.acquire():
            raw = self._read_raw()
            marked: list[InboxMessage] = []
            changed = False
            for item in raw:
                if not item.get("read", False):
                    item["read"] = True
                    changed = True
                    marked.append(inbox_message_from_dict(item))
            if changed:
                self._write_raw(raw)
            return marked

    def has_unread(self) -> bool:
        """检查是否有未读消息。"""
        raw = self._read_raw()
        return any(not item.get("read", False) for item in raw)

    def mtime_ns(self) -> int:
        """获取 inbox 文件的最后修改时间（纳秒）。

        文件不存在时返回 0。用于 InboxPoller 的 mtime 优化。
        """
        try:
            return self._inbox_path.stat().st_mtime_ns
        except FileNotFoundError:
            return 0

    # ── 内部辅助 ────────────────────────────────────────────

    def _read_raw(self) -> list[dict]:
        """读取 inbox 文件原始 JSON 数组。"""
        data = read_json(self._inbox_path, default=[])
        if not isinstance(data, list):
            return []
        return data

    def _write_raw(self, messages: list[dict]) -> None:
        """写入 inbox 文件（原子写入）。"""
        atomic_write_json(self._inbox_path, messages)
