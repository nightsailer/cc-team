"""异步文件锁（fcntl）。

基于 fcntl.flock(LOCK_EX | LOCK_NB) + 指数退避重试。
仅支持 Unix 系统。

用法:
    lock = FileLock(Path("/tmp/my.lock"))
    async with lock.acquire():
        # 持有锁期间的操作
        ...
"""

from __future__ import annotations

import asyncio
import fcntl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from cc_team.exceptions import FileLockError

# 默认重试参数
_MAX_ATTEMPTS = 5
_BASE_DELAY_MS = 50  # 50ms → 100ms → 200ms → 400ms（指数退避）
_MAX_DELAY_MS = 500


class FileLock:
    """异步文件锁。

    使用 fcntl.flock 实现非阻塞独占锁，
    配合指数退避重试策略处理锁竞争。

    Args:
        path: 锁文件路径（如 config.json.lock）
        max_attempts: 最大重试次数
        base_delay_ms: 首次重试延迟（毫秒）
    """

    def __init__(
        self,
        path: Path,
        *,
        max_attempts: int = _MAX_ATTEMPTS,
        base_delay_ms: int = _BASE_DELAY_MS,
    ) -> None:
        self._path = path
        self._max_attempts = max_attempts
        self._base_delay_ms = base_delay_ms

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """获取独占文件锁（异步上下文管理器）。

        Raises:
            FileLockError: 超过最大重试次数仍无法获取锁
        """
        # 确保锁文件父目录存在
        self._path.parent.mkdir(parents=True, exist_ok=True)

        fd = None
        try:
            fd = open(self._path, "w")
            await self._try_lock(fd)
            yield
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                fd.close()

    async def _try_lock(self, fd: object) -> None:
        """带指数退避的锁获取尝试。"""
        delay_ms = self._base_delay_ms
        for attempt in range(1, self._max_attempts + 1):
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[union-attr]
                return  # 获取成功
            except (BlockingIOError, OSError):
                if attempt == self._max_attempts:
                    raise FileLockError(str(self._path), attempt)
                await asyncio.sleep(min(delay_ms, _MAX_DELAY_MS) / 1000.0)
                delay_ms *= 2  # 指数退避
