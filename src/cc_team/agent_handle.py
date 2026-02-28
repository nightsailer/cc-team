"""Agent 代理对象。

提供面向用户的 Agent 操作 API。
通过 AgentController Protocol 与 Controller 解耦（DIP）。

用法:
    handle = AgentHandle("researcher", controller)
    await handle.send("Start analyzing...")
    await handle.shutdown("Task complete")
"""

from __future__ import annotations

from cc_team.types import AgentColor, AgentController


class AgentHandle:
    """单个 Agent 的代理对象。

    封装所有对特定 Agent 的操作，委托给 Controller 执行。
    用户通过 AgentHandle 与 Agent 交互，无需直接操作 Controller。

    Args:
        name: Agent 名称
        controller: AgentController 实例（Protocol 接口）
        pane_id: tmux pane ID
        color: Agent 颜色
    """

    def __init__(
        self,
        name: str,
        controller: AgentController,
        *,
        pane_id: str = "",
        color: AgentColor | None = None,
    ) -> None:
        self._name: str = name
        self._controller: AgentController = controller
        self._pane_id: str = pane_id
        self._color: AgentColor | None = color

    @property
    def name(self) -> str:
        """Agent 名称。"""
        return self._name

    @property
    def pane_id(self) -> str:
        """tmux pane ID。"""
        return self._pane_id

    @property
    def color(self) -> AgentColor | None:
        """Agent 颜色。"""
        return self._color

    # ── 通信 ────────────────────────────────────────────────

    async def send(self, content: str, *, summary: str | None = None) -> None:
        """发送消息给此 Agent。

        Args:
            content: 消息内容
            summary: 摘要文本（可选）
        """
        await self._controller.send_message(
            self._name, content, summary=summary
        )

    # ── 生命周期 ────────────────────────────────────────────

    async def shutdown(self, reason: str = "Task complete") -> str:
        """发送关闭请求。

        Args:
            reason: 关闭原因

        Returns:
            shutdown request_id
        """
        return await self._controller.send_shutdown_request(self._name, reason)

    async def kill(self) -> None:
        """强制终止 Agent（kill-pane）。"""
        await self._controller.kill_agent(self._name)

    def is_running(self) -> bool:
        """检查 Agent 是否存活。"""
        return self._controller.is_agent_running(self._name)

    # ── 表示 ────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"AgentHandle(name={self._name!r}, pane={self._pane_id!r}, color={self._color!r})"
