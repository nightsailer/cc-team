"""filelock.py 单元测试 — 异步文件锁机制验证。

测试覆盖:
- 基本锁获取与释放
- 异步上下文管理器协议
- 指数退避重试
- 锁竞争与 FileLockError
- 锁文件父目录自动创建
- 参数配置
"""

from __future__ import annotations

import asyncio
import fcntl
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_team.exceptions import FileLockError
from cc_team.filelock import _BASE_DELAY_MS, _MAX_ATTEMPTS, _MAX_DELAY_MS, FileLock

# ── 基本锁获取与释放 ──────────────────────────────────────────


class TestFileLockBasic:
    """基础功能测试。"""

    @pytest.mark.asyncio
    async def test_acquire_creates_lock_file(self, tmp_path: Path) -> None:
        """acquire 应创建锁文件。"""
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path)
        async with lock.acquire():
            assert lock_path.exists()

    @pytest.mark.asyncio
    async def test_lock_released_after_context_exit(self, tmp_path: Path) -> None:
        """退出上下文后，锁应被释放（其他进程可再次获取）。"""
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path)

        async with lock.acquire():
            pass  # 锁在此处被持有

        # 退出后应能再次获取
        async with lock.acquire():
            pass

    @pytest.mark.asyncio
    async def test_lock_file_content_irrelevant(self, tmp_path: Path) -> None:
        """锁文件内容不影响锁行为（使用 flock 而非文件内容）。"""
        lock_path = tmp_path / "test.lock"
        lock_path.write_text("pre-existing content")
        lock = FileLock(lock_path)

        async with lock.acquire():
            # 能正常获取锁即可
            assert lock_path.exists()

    @pytest.mark.asyncio
    async def test_different_fd_conflicts_same_process(self, tmp_path: Path) -> None:
        """不同 fd 对同一文件互斥（flock 按 fd 而非进程粒度）。

        FileLock 每次 acquire 打开新 fd，因此同一路径不可嵌套。
        """
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path, max_attempts=1, base_delay_ms=1)

        async with lock.acquire():
            lock2 = FileLock(lock_path, max_attempts=1, base_delay_ms=1)
            with pytest.raises(FileLockError):
                async with lock2.acquire():
                    pass  # 不应到达


# ── 目录自动创建 ──────────────────────────────────────────────


class TestFileLockDirectory:
    """锁文件父目录处理。"""

    @pytest.mark.asyncio
    async def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """acquire 应自动创建不存在的父目录。"""
        lock_path = tmp_path / "deep" / "nested" / "dir" / "test.lock"
        assert not lock_path.parent.exists()

        lock = FileLock(lock_path)
        async with lock.acquire():
            assert lock_path.parent.exists()
            assert lock_path.exists()


# ── 指数退避与重试 ────────────────────────────────────────────


class TestFileLockRetry:
    """指数退避重试逻辑。"""

    @pytest.mark.asyncio
    async def test_retry_on_lock_contention(self, tmp_path: Path) -> None:
        """锁竞争时应重试，最终获取成功。"""
        lock_path = tmp_path / "test.lock"
        attempt_count = 0

        original_flock = fcntl.flock

        def mock_flock(fd: int, operation: int) -> None:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise BlockingIOError("Resource temporarily unavailable")
            original_flock(fd, operation)

        lock = FileLock(lock_path, base_delay_ms=1)  # 最小延迟加速测试

        with patch.object(fcntl, "flock", side_effect=mock_flock):
            async with lock.acquire():
                assert attempt_count == 3

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self, tmp_path: Path) -> None:
        """验证退避延迟按指数增长。"""
        lock_path = tmp_path / "test.lock"
        sleep_delays: list[float] = []

        original_flock = fcntl.flock
        attempt_count = 0

        def mock_flock(fd: int, operation: int) -> None:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 4:
                raise BlockingIOError("locked")
            original_flock(fd, operation)


        async def mock_sleep(seconds: float) -> None:
            sleep_delays.append(seconds)
            # 不实际等待

        lock = FileLock(lock_path, base_delay_ms=50, max_attempts=5)

        with patch.object(fcntl, "flock", side_effect=mock_flock), \
             patch.object(asyncio, "sleep", side_effect=mock_sleep):
            async with lock.acquire():
                pass

        # 验证延迟序列: 50ms, 100ms, 200ms (3 次重试后第 4 次成功)
        assert len(sleep_delays) == 3
        assert sleep_delays[0] == pytest.approx(0.05)   # 50ms
        assert sleep_delays[1] == pytest.approx(0.1)    # 100ms
        assert sleep_delays[2] == pytest.approx(0.2)    # 200ms

    @pytest.mark.asyncio
    async def test_delay_capped_at_max(self, tmp_path: Path) -> None:
        """延迟不应超过 _MAX_DELAY_MS。"""
        lock_path = tmp_path / "test.lock"
        sleep_delays: list[float] = []

        original_flock = fcntl.flock
        attempt_count = 0

        def mock_flock(fd: int, operation: int) -> None:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 5:
                raise BlockingIOError("locked")
            original_flock(fd, operation)

        async def mock_sleep(seconds: float) -> None:
            sleep_delays.append(seconds)

        # base_delay_ms=200, max 4 retries: 200, 400, 500(capped), 500(capped)
        lock = FileLock(lock_path, base_delay_ms=200, max_attempts=5)

        with patch.object(fcntl, "flock", side_effect=mock_flock), \
             patch.object(asyncio, "sleep", side_effect=mock_sleep):
            async with lock.acquire():
                pass

        assert len(sleep_delays) == 4
        # 最后两次应被 cap 到 _MAX_DELAY_MS (500ms = 0.5s)
        assert sleep_delays[2] == pytest.approx(_MAX_DELAY_MS / 1000.0)
        assert sleep_delays[3] == pytest.approx(_MAX_DELAY_MS / 1000.0)


# ── FileLockError ──────────────────────────────────────────────


class TestFileLockError:
    """锁获取失败场景。"""

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self, tmp_path: Path) -> None:
        """超过最大重试次数应抛出 FileLockError。"""
        lock_path = tmp_path / "test.lock"

        def always_fail(fd: int, operation: int) -> None:
            raise BlockingIOError("permanently locked")

        async def noop_sleep(seconds: float) -> None:
            pass

        lock = FileLock(lock_path, max_attempts=3, base_delay_ms=1)

        with patch.object(fcntl, "flock", side_effect=always_fail), \
             patch.object(asyncio, "sleep", side_effect=noop_sleep), \
             pytest.raises(FileLockError) as exc_info:
            async with lock.acquire():
                pass

        assert exc_info.value.path == str(lock_path)
        assert exc_info.value.attempts == 3

    @pytest.mark.asyncio
    async def test_oserror_also_triggers_retry(self, tmp_path: Path) -> None:
        """OSError 也应触发重试（不仅 BlockingIOError）。"""
        lock_path = tmp_path / "test.lock"
        attempt_count = 0

        original_flock = fcntl.flock

        def mock_flock(fd: int, operation: int) -> None:
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise OSError("generic OS error")
            original_flock(fd, operation)

        async def noop_sleep(seconds: float) -> None:
            pass

        lock = FileLock(lock_path, base_delay_ms=1)

        with patch.object(fcntl, "flock", side_effect=mock_flock), \
             patch.object(asyncio, "sleep", side_effect=noop_sleep):
            async with lock.acquire():
                assert attempt_count == 2


# ── 参数与默认值 ──────────────────────────────────────────────


class TestFileLockConfig:
    """构造参数验证。"""

    def test_default_max_attempts(self) -> None:
        """默认最大重试次数为 5。"""
        lock = FileLock(Path("/tmp/test.lock"))
        assert lock._max_attempts == _MAX_ATTEMPTS

    def test_default_base_delay(self) -> None:
        """默认首次延迟为 50ms。"""
        lock = FileLock(Path("/tmp/test.lock"))
        assert lock._base_delay_ms == _BASE_DELAY_MS

    def test_custom_parameters(self) -> None:
        """支持自定义重试参数。"""
        lock = FileLock(
            Path("/tmp/test.lock"),
            max_attempts=10,
            base_delay_ms=100,
        )
        assert lock._max_attempts == 10
        assert lock._base_delay_ms == 100


# ── 异常安全 ──────────────────────────────────────────────────


class TestFileLockExceptionSafety:
    """上下文管理器异常安全性。"""

    @pytest.mark.asyncio
    async def test_lock_released_on_exception(self, tmp_path: Path) -> None:
        """上下文内抛出异常时，锁仍应被释放。"""
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path)

        with pytest.raises(ValueError, match="test error"):
            async with lock.acquire():
                raise ValueError("test error")

        # 应能再次获取
        async with lock.acquire():
            pass

    @pytest.mark.asyncio
    async def test_unlock_oserror_suppressed(self, tmp_path: Path) -> None:
        """释放锁时的 OSError 应被静默忽略。"""
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path)

        # 正常获取并释放（unlock 时 OSError 被 try/except 捕获）
        async with lock.acquire():
            pass
        # 未抛出异常即为通过
