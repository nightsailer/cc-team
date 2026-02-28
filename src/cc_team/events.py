"""异步事件发射器。

轻量级 Node.js EventEmitter 风格实现，支持异步 handler。
零外部依赖。

用法:
    emitter = AsyncEventEmitter()
    emitter.on("message", my_handler)
    await emitter.emit("message", agent_name, msg)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine


# Handler 类型: 接受任意参数的异步函数
EventHandler = Callable[..., Coroutine[Any, Any, None]]


class AsyncEventEmitter:
    """异步事件发射器。

    特性:
    - 支持 on/off/once 注册模式
    - emit 并发执行所有 handler
    - handler 异常不影响其他 handler，并通过 "error" 事件报告
    - error 事件的 handler 异常被静默忽略（防止无限递归）
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[EventHandler]] = defaultdict(list)
        self._once_handlers: set[EventHandler] = set()  # 直接持有引用，避免 id() GC 复用

    def on(self, event: str, handler: EventHandler) -> None:
        """注册事件 handler（持久）。"""
        self._listeners[event].append(handler)

    def once(self, event: str, handler: EventHandler) -> None:
        """注册一次性事件 handler（触发后自动移除）。"""
        self._listeners[event].append(handler)
        self._once_handlers.add(handler)

    def off(self, event: str, handler: EventHandler) -> None:
        """移除事件 handler。"""
        handlers = self._listeners.get(event)
        if handlers:
            try:
                handlers.remove(handler)
                self._once_handlers.discard(handler)
            except ValueError:
                pass

    def remove_all_listeners(self, event: str | None = None) -> None:
        """移除指定事件或所有事件的 handler。"""
        if event is None:
            self._listeners.clear()
            self._once_handlers.clear()
        elif event in self._listeners:
            for h in self._listeners[event]:
                self._once_handlers.discard(h)
            del self._listeners[event]

    async def emit(self, event: str, *args: Any) -> bool:
        """触发事件，并发执行所有 handler。

        Returns:
            True 如果有 handler 被执行
        """
        handlers = self._listeners.get(event)
        if not handlers:
            return False

        # 拷贝列表，避免 handler 中修改 listeners 导致迭代问题
        handlers_copy = list(handlers)

        # 收集 once handler 待移除
        to_remove: list[EventHandler] = []
        tasks: list[asyncio.Task[None]] = []

        for handler in handlers_copy:
            if handler in self._once_handlers:
                to_remove.append(handler)

            task = asyncio.create_task(self._safe_call(event, handler, *args))
            tasks.append(task)

        # 等待所有 handler 完成
        if tasks:
            await asyncio.gather(*tasks)

        # 移除 once handler
        for handler in to_remove:
            self.off(event, handler)

        return True

    def listener_count(self, event: str) -> int:
        """返回指定事件的 handler 数量。"""
        return len(self._listeners.get(event, []))

    def event_names(self) -> list[str]:
        """返回所有已注册事件名。"""
        return [k for k, v in self._listeners.items() if v]

    async def _safe_call(self, event: str, handler: EventHandler, *args: Any) -> None:
        """安全调用 handler，捕获异常并通过 error 事件报告。"""
        try:
            await handler(*args)
        except Exception as exc:
            if event == "error":
                # error 事件的 handler 异常被静默忽略，防止无限递归
                pass
            else:
                # 非 error 事件的异常通过 "error" 事件报告
                await self.emit("error", exc, event, handler)
