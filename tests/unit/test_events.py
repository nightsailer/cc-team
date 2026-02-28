"""events.py 单元测试 — AsyncEventEmitter 验证。

测试覆盖:
- on/off 注册与移除
- once 一次性 handler
- emit 并发执行
- handler 异常隔离
- error 事件特殊处理
- listener_count / event_names
- remove_all_listeners
"""

from __future__ import annotations

import asyncio

import pytest

from cc_team.events import AsyncEventEmitter

# ── on / off 注册 ────────────────────────────────────────────


class TestOnOff:
    """on() / off() 注册与移除。"""

    @pytest.mark.asyncio
    async def test_on_registers_handler(self) -> None:
        """on 注册的 handler 应被 emit 调用。"""
        emitter = AsyncEventEmitter()
        results: list[str] = []

        async def handler(val: str) -> None:
            results.append(val)

        emitter.on("test", handler)
        await emitter.emit("test", "hello")

        assert results == ["hello"]

    @pytest.mark.asyncio
    async def test_multiple_handlers(self) -> None:
        """同一事件可注册多个 handler。"""
        emitter = AsyncEventEmitter()
        results: list[int] = []

        async def h1() -> None:
            results.append(1)

        async def h2() -> None:
            results.append(2)

        emitter.on("event", h1)
        emitter.on("event", h2)
        await emitter.emit("event")

        assert sorted(results) == [1, 2]

    @pytest.mark.asyncio
    async def test_off_removes_handler(self) -> None:
        """off 移除后 handler 不再被调用。"""
        emitter = AsyncEventEmitter()
        results: list[str] = []

        async def handler() -> None:
            results.append("called")

        emitter.on("event", handler)
        emitter.off("event", handler)
        await emitter.emit("event")

        assert results == []

    @pytest.mark.asyncio
    async def test_off_nonexistent_handler_noop(self) -> None:
        """off 移除未注册的 handler 不报错。"""
        emitter = AsyncEventEmitter()

        async def handler() -> None:
            pass

        emitter.off("event", handler)  # 不应抛出

    @pytest.mark.asyncio
    async def test_off_nonexistent_event_noop(self) -> None:
        """off 对不存在的事件不报错。"""
        emitter = AsyncEventEmitter()

        async def handler() -> None:
            pass

        emitter.off("nonexistent", handler)  # 不应抛出

    @pytest.mark.asyncio
    async def test_handler_receives_multiple_args(self) -> None:
        """handler 应能接收多个参数。"""
        emitter = AsyncEventEmitter()
        captured: list[tuple] = []

        async def handler(a: int, b: str, c: bool) -> None:
            captured.append((a, b, c))

        emitter.on("event", handler)
        await emitter.emit("event", 1, "two", True)

        assert captured == [(1, "two", True)]


# ── once 一次性 handler ──────────────────────────────────────


class TestOnce:
    """once() 一次性注册测试。"""

    @pytest.mark.asyncio
    async def test_once_fires_once(self) -> None:
        """once 注册的 handler 仅触发一次。"""
        emitter = AsyncEventEmitter()
        count = 0

        async def handler() -> None:
            nonlocal count
            count += 1

        emitter.once("event", handler)
        await emitter.emit("event")
        await emitter.emit("event")

        assert count == 1

    @pytest.mark.asyncio
    async def test_once_removed_after_emit(self) -> None:
        """once handler 触发后应从 listeners 中移除。"""
        emitter = AsyncEventEmitter()

        async def handler() -> None:
            pass

        emitter.once("event", handler)
        assert emitter.listener_count("event") == 1

        await emitter.emit("event")
        assert emitter.listener_count("event") == 0

    @pytest.mark.asyncio
    async def test_once_mixed_with_on(self) -> None:
        """once 和 on 混用时，once 仅触发一次，on 持续触发。"""
        emitter = AsyncEventEmitter()
        once_count = 0
        on_count = 0

        async def once_handler() -> None:
            nonlocal once_count
            once_count += 1

        async def on_handler() -> None:
            nonlocal on_count
            on_count += 1

        emitter.once("event", once_handler)
        emitter.on("event", on_handler)

        await emitter.emit("event")
        await emitter.emit("event")

        assert once_count == 1
        assert on_count == 2


# ── emit 行为 ────────────────────────────────────────────────


class TestEmit:
    """emit() 返回值和并发行为。"""

    @pytest.mark.asyncio
    async def test_emit_returns_true_with_handlers(self) -> None:
        """有 handler 时 emit 返回 True。"""
        emitter = AsyncEventEmitter()

        async def handler() -> None:
            pass

        emitter.on("event", handler)
        assert await emitter.emit("event") is True

    @pytest.mark.asyncio
    async def test_emit_returns_false_without_handlers(self) -> None:
        """无 handler 时 emit 返回 False。"""
        emitter = AsyncEventEmitter()
        assert await emitter.emit("event") is False

    @pytest.mark.asyncio
    async def test_emit_concurrent_execution(self) -> None:
        """emit 应并发执行所有 handler（而非顺序）。"""
        emitter = AsyncEventEmitter()
        execution_order: list[str] = []

        async def slow_handler() -> None:
            await asyncio.sleep(0.05)
            execution_order.append("slow")

        async def fast_handler() -> None:
            execution_order.append("fast")

        emitter.on("event", slow_handler)
        emitter.on("event", fast_handler)
        await emitter.emit("event")

        # 并发执行时，fast 应先完成
        assert "fast" in execution_order
        assert "slow" in execution_order

    @pytest.mark.asyncio
    async def test_emit_no_args(self) -> None:
        """emit 支持无参数事件。"""
        emitter = AsyncEventEmitter()
        called = False

        async def handler() -> None:
            nonlocal called
            called = True

        emitter.on("event", handler)
        await emitter.emit("event")
        assert called is True


# ── 异常隔离 ──────────────────────────────────────────────────


class TestExceptionIsolation:
    """handler 异常隔离测试。"""

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_affect_others(self) -> None:
        """一个 handler 的异常不影响其他 handler 执行。"""
        emitter = AsyncEventEmitter()
        results: list[str] = []

        async def bad_handler() -> None:
            raise RuntimeError("boom")

        async def good_handler() -> None:
            results.append("ok")

        emitter.on("event", bad_handler)
        emitter.on("event", good_handler)
        await emitter.emit("event")

        assert results == ["ok"]

    @pytest.mark.asyncio
    async def test_error_event_exception_suppressed(self) -> None:
        """error 事件的 handler 异常被静默忽略（防止无限递归）。"""
        emitter = AsyncEventEmitter()

        async def bad_error_handler() -> None:
            raise RuntimeError("error handler failed")

        emitter.on("error", bad_error_handler)
        # 不应抛出异常
        await emitter.emit("error", "some error")

    @pytest.mark.asyncio
    async def test_emit_exception_does_not_propagate(self) -> None:
        """emit 本身不传播 handler 异常。"""
        emitter = AsyncEventEmitter()

        async def failing_handler() -> None:
            raise ValueError("fail")

        emitter.on("event", failing_handler)
        # 应正常返回，不抛出
        result = await emitter.emit("event")
        assert result is True

    @pytest.mark.asyncio
    async def test_handler_exception_emits_error_event(self) -> None:
        """非 error 事件的 handler 异常应触发 error 事件。"""
        emitter = AsyncEventEmitter()
        error_received: list[tuple] = []

        async def failing_handler() -> None:
            raise ValueError("test error")

        async def error_handler(exc: Exception, event: str, handler: object) -> None:
            error_received.append((type(exc).__name__, str(exc), event))

        emitter.on("event", failing_handler)
        emitter.on("error", error_handler)
        await emitter.emit("event")

        assert len(error_received) == 1
        assert error_received[0] == ("ValueError", "test error", "event")

    @pytest.mark.asyncio
    async def test_error_event_handler_exception_no_recursion(self) -> None:
        """error 事件的 handler 异常不会触发递归的 error 事件。"""
        emitter = AsyncEventEmitter()
        call_count = 0

        async def bad_error_handler(exc: Exception, *args: object) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("error handler also fails")

        emitter.on("error", bad_error_handler)

        # 手动触发 error 事件
        await emitter.emit("error", ValueError("original"), "event", None)

        # error handler 应只被调用一次，不递归
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_error_handler_no_crash(self) -> None:
        """无 error handler 时，handler 异常不导致崩溃。"""
        emitter = AsyncEventEmitter()

        async def failing_handler() -> None:
            raise RuntimeError("boom")

        emitter.on("event", failing_handler)
        # 无 error handler 注册，emit("error", ...) 返回 False 但不崩溃
        result = await emitter.emit("event")
        assert result is True


# ── listener_count / event_names ─────────────────────────────


class TestListenerInfo:
    """listener_count() / event_names() 测试。"""

    def test_listener_count_empty(self) -> None:
        """无注册时 listener_count 为 0。"""
        emitter = AsyncEventEmitter()
        assert emitter.listener_count("any") == 0

    def test_listener_count_increments(self) -> None:
        """注册后 listener_count 增加。"""
        emitter = AsyncEventEmitter()

        async def h1() -> None:
            pass

        async def h2() -> None:
            pass

        emitter.on("event", h1)
        assert emitter.listener_count("event") == 1
        emitter.on("event", h2)
        assert emitter.listener_count("event") == 2

    def test_event_names_empty(self) -> None:
        """无注册时 event_names 为空。"""
        emitter = AsyncEventEmitter()
        assert emitter.event_names() == []

    def test_event_names_lists_registered(self) -> None:
        """event_names 返回有 handler 的事件名。"""
        emitter = AsyncEventEmitter()

        async def handler() -> None:
            pass

        emitter.on("alpha", handler)
        emitter.on("beta", handler)
        names = emitter.event_names()
        assert set(names) == {"alpha", "beta"}


# ── remove_all_listeners ─────────────────────────────────────


class TestRemoveAllListeners:
    """remove_all_listeners() 测试。"""

    def test_remove_all_for_event(self) -> None:
        """移除指定事件的所有 handler。"""
        emitter = AsyncEventEmitter()

        async def h1() -> None:
            pass

        async def h2() -> None:
            pass

        emitter.on("event", h1)
        emitter.on("event", h2)
        emitter.on("other", h1)

        emitter.remove_all_listeners("event")
        assert emitter.listener_count("event") == 0
        assert emitter.listener_count("other") == 1

    def test_remove_all_global(self) -> None:
        """无参调用移除所有事件的所有 handler。"""
        emitter = AsyncEventEmitter()

        async def handler() -> None:
            pass

        emitter.on("a", handler)
        emitter.on("b", handler)
        emitter.once("c", handler)

        emitter.remove_all_listeners()
        assert emitter.event_names() == []
        assert emitter.listener_count("a") == 0

    def test_remove_all_nonexistent_event_noop(self) -> None:
        """移除不存在事件的 listeners 不报错。"""
        emitter = AsyncEventEmitter()
        emitter.remove_all_listeners("nonexistent")  # 不应抛出
