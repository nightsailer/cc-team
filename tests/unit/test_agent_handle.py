"""agent_handle.py 单元测试 — AgentHandle 代理对象验证。

测试覆盖:
- 属性访问（name / backend_id / color）
- send() 委托到 Controller
- shutdown() 委托到 Controller
- kill() 委托到 Controller
- is_running() 委托到 Controller
- __repr__
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cc_team.agent_handle import AgentHandle
from cc_team.types import AgentController

# ── Mock Controller ──────────────────────────────────────────


def _make_controller() -> MagicMock:
    """创建满足 AgentController Protocol 的 mock。"""
    ctrl = MagicMock(spec=AgentController)
    ctrl.send_message = AsyncMock()
    ctrl.send_shutdown_request = AsyncMock(return_value="shutdown-123@worker")
    ctrl.kill_agent = AsyncMock()
    ctrl.is_agent_running = MagicMock(return_value=True)
    return ctrl


# ── 属性 ─────────────────────────────────────────────────────


class TestProperties:
    """AgentHandle 属性测试。"""

    def test_name(self) -> None:
        handle = AgentHandle("worker", _make_controller())
        assert handle.name == "worker"

    def test_backend_id(self) -> None:
        handle = AgentHandle("w", _make_controller(), backend_id="%5")
        assert handle.backend_id == "%5"

    def test_backend_id_default(self) -> None:
        handle = AgentHandle("w", _make_controller())
        assert handle.backend_id == ""

    def test_color(self) -> None:
        handle = AgentHandle("w", _make_controller(), color="blue")
        assert handle.color == "blue"

    def test_color_default_none(self) -> None:
        handle = AgentHandle("w", _make_controller())
        assert handle.color is None


# ── 通信 ─────────────────────────────────────────────────────


class TestCommunication:
    """send() 委托测试。"""

    @pytest.mark.asyncio
    async def test_send_delegates(self) -> None:
        """send 委托给 controller.send_message。"""
        ctrl = _make_controller()
        handle = AgentHandle("worker", ctrl)
        await handle.send("Hello", summary="Greeting")

        ctrl.send_message.assert_awaited_once_with("worker", "Hello", summary="Greeting")

    @pytest.mark.asyncio
    async def test_send_no_summary(self) -> None:
        """send 不传 summary。"""
        ctrl = _make_controller()
        handle = AgentHandle("worker", ctrl)
        await handle.send("Hi")

        ctrl.send_message.assert_awaited_once_with("worker", "Hi", summary=None)


# ── 生命周期 ─────────────────────────────────────────────────


class TestLifecycle:
    """shutdown() / kill() / is_running() 委托测试。"""

    @pytest.mark.asyncio
    async def test_shutdown_delegates(self) -> None:
        """shutdown 委托给 controller.send_shutdown_request。"""
        ctrl = _make_controller()
        handle = AgentHandle("worker", ctrl)
        req_id = await handle.shutdown("Done")

        ctrl.send_shutdown_request.assert_awaited_once_with("worker", "Done")
        assert req_id == "shutdown-123@worker"

    @pytest.mark.asyncio
    async def test_shutdown_default_reason(self) -> None:
        """shutdown 默认原因为 "Task complete"。"""
        ctrl = _make_controller()
        handle = AgentHandle("worker", ctrl)
        await handle.shutdown()

        ctrl.send_shutdown_request.assert_awaited_once_with("worker", "Task complete")

    @pytest.mark.asyncio
    async def test_kill_delegates(self) -> None:
        """kill 委托给 controller.kill_agent。"""
        ctrl = _make_controller()
        handle = AgentHandle("worker", ctrl)
        await handle.kill()

        ctrl.kill_agent.assert_awaited_once_with("worker")

    def test_is_running_delegates(self) -> None:
        """is_running 委托给 controller.is_agent_running。"""
        ctrl = _make_controller()
        handle = AgentHandle("worker", ctrl)
        assert handle.is_running() is True

        ctrl.is_agent_running.return_value = False
        assert handle.is_running() is False


# ── 表示 ─────────────────────────────────────────────────────


class TestRepr:
    """__repr__ 测试。"""

    def test_repr_format(self) -> None:
        handle = AgentHandle("dev", _make_controller(), backend_id="%3", color="green")
        r = repr(handle)
        assert "dev" in r
        assert "%3" in r
        assert "green" in r
