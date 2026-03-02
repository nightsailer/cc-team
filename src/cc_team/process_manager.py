"""进程生命周期管理器。

负责:
- 通过 tmux spawn Agent 进程
- 追踪 agent_name → pane_id 映射
- 检查进程存活状态
- 优雅/强制终止
- 构建 Claude CLI 参数

不内嵌 PTY 脚本，完全依赖 tmux。
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil

from cc_team.exceptions import AgentNotFoundError, SpawnError, TmuxError
from cc_team.tmux import TmuxManager
from cc_team.types import TEAM_LEAD_AGENT_TYPE, PermissionMode, SpawnAgentOptions, SpawnLeadOptions

# 团队协议激活必需的环境变量前缀
_ENV_PREFIX = "CLAUDECODE=1 CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 "


class ProcessManager:
    """Agent 进程生命周期管理器。

    Args:
        tmux: TmuxManager 实例（可注入 mock）
    """

    def __init__(self, *, tmux: TmuxManager | None = None) -> None:
        self._tmux = tmux or TmuxManager()
        self._panes: dict[str, str] = {}  # agent_name → pane_id

    @property
    def tmux(self) -> TmuxManager:
        return self._tmux

    # ── Spawn ───────────────────────────────────────────────

    async def spawn(
        self,
        options: SpawnAgentOptions,
        *,
        team_name: str,
        color: str,
        parent_session_id: str,
    ) -> str:
        """在 tmux pane 中启动 Claude Agent。

        Args:
            options: Agent 配置
            team_name: 团队名称
            color: 分配的颜色
            parent_session_id: Lead 的会话 ID

        Returns:
            tmux pane ID

        Raises:
            SpawnError: 启动失败
        """
        try:
            pane_id = await self._tmux.split_window()
        except TmuxError as e:
            raise SpawnError(f"Failed to create tmux pane: {e}") from e

        # Build CLI command with required env vars for team protocol activation.
        # Without these, Claude Code won't initialize inbox read/write logic.
        cli_args = self.build_cli_args(
            options,
            team_name=team_name,
            color=color,
            parent_session_id=parent_session_id,
        )
        command = _build_spawn_command(options.cwd, cli_args)

        await self._send_to_pane(pane_id, command, owned_pane=True)

        self._panes[options.name] = pane_id
        return pane_id

    # ── Internal Helpers ──────────────────────────────────────

    def _require_pane(self, agent_name: str) -> str:
        """Look up pane ID for *agent_name*, raise if not tracked."""
        pane_id = self._panes.get(agent_name)
        if pane_id is None:
            raise AgentNotFoundError(agent_name)
        return pane_id

    # ── 外部注册 ────────────────────────────────────────────

    def track(self, agent_name: str, pane_id: str) -> None:
        """注册已有 agent 到追踪列表（attach/sync 场景）。"""
        self._panes[agent_name] = pane_id

    # ── 终止 ────────────────────────────────────────────────

    async def kill(self, agent_name: str) -> None:
        """强制终止 Agent（kill-pane）。

        Raises:
            AgentNotFoundError: Agent 不在追踪列表中
        """
        pane_id = self._require_pane(agent_name)

        with contextlib.suppress(TmuxError):
            await self._tmux.kill_pane(pane_id)

        del self._panes[agent_name]

    def untrack(self, agent_name: str) -> None:
        """从追踪列表中移除（Agent 自行退出时调用）。"""
        self._panes.pop(agent_name, None)

    # ── 状态查询 ────────────────────────────────────────────

    async def is_running(self, agent_name: str) -> bool:
        """检查 Agent 进程是否存活。"""
        pane_id = self._panes.get(agent_name)
        if pane_id is None:
            return False
        return await self._tmux.is_pane_alive(pane_id)

    def get_pane_id(self, agent_name: str) -> str | None:
        """获取 Agent 的 pane ID。"""
        return self._panes.get(agent_name)

    def tracked_agents(self) -> list[str]:
        """返回所有被追踪的 Agent 名称。"""
        return list(self._panes.keys())

    # ── Input Delivery ──────────────────────────────────────

    async def send_input(self, agent_name: str, text: str) -> None:
        """Send input text to an agent's tmux pane.

        Raises:
            AgentNotFoundError: agent is not tracked.
        """
        pane_id = self._require_pane(agent_name)
        await self._tmux.send_command(pane_id, text)

    # ── CLI 参数构建 ────────────────────────────────────────

    @staticmethod
    def build_cli_args(
        options: SpawnAgentOptions,
        *,
        team_name: str,
        color: str,
        parent_session_id: str,
    ) -> list[str]:
        """构建 Claude CLI 启动参数（协议 §C.5）。

        Returns:
            命令行参数列表
        """
        # 查找 claude 可执行文件
        claude_path = _find_claude_binary()

        args = [
            claude_path,
            "--agent-id", f"{options.name}@{team_name}",
            "--agent-name", options.name,
            "--team-name", team_name,
            "--agent-color", color,
            "--parent-session-id", parent_session_id,
            "--agent-type", options.agent_type,
            "--model", options.model,
        ]

        # 条件性参数
        if options.plan_mode_required:
            args.append("--plan-mode-required")

        if options.permission_mode is not None:
            _add_permission_args(args, options.permission_mode)

        if options.allowed_tools:
            for tool in options.allowed_tools:
                args.extend(["--allowedTools", tool])

        if options.disallowed_tools:
            for tool in options.disallowed_tools:
                args.extend(["--disallowedTools", tool])

        return args

    @staticmethod
    def build_lead_cli_args(
        options: SpawnLeadOptions,
        *,
        parent_session_id: str,
    ) -> list[str]:
        """构建 Team Lead CLI 启动参数。

        与 build_cli_args 的区别:
        - 有 --session-id（TL 独有）
        - 无 --agent-color（TL 不需要颜色）
        - agent-type 固定 "team-lead"

        Returns:
            命令行参数列表
        """
        claude_path = _find_claude_binary()

        tl = TEAM_LEAD_AGENT_TYPE
        args = [
            claude_path,
            "--agent-id", f"{tl}@{options.team_name}",
            "--agent-name", tl,
            "--team-name", options.team_name,
            "--parent-session-id", parent_session_id,
            "--agent-type", tl,
            "--model", options.model,
            "--session-id", options.session_id,
        ]

        if options.permission_mode is not None:
            _add_permission_args(args, options.permission_mode)

        return args

    async def spawn_lead(
        self,
        options: SpawnLeadOptions,
        *,
        parent_session_id: str,
    ) -> str:
        """在 tmux 中启动 Team Lead 进程。

        支持复用已有 pane（relay 场景）或自动 split_window。

        Args:
            options: TL 配置
            parent_session_id: 父级 session ID（通常等于 options.session_id）

        Returns:
            tmux pane ID

        Raises:
            SpawnError: 启动失败
        """
        # 获取或创建 pane
        if options.pane_id:
            pane_id = options.pane_id
            # 验证 pane 存活
            if not await self._tmux.is_pane_alive(pane_id):
                raise SpawnError(f"Pane {pane_id} is not alive, cannot reuse")
        else:
            try:
                pane_id = await self._tmux.split_window()
            except TmuxError as e:
                raise SpawnError(f"Failed to create tmux pane: {e}") from e

        # 构建并发送命令
        cli_args = self.build_lead_cli_args(
            options,
            parent_session_id=parent_session_id,
        )
        command = _build_spawn_command(options.cwd, cli_args)

        await self._send_to_pane(pane_id, command, owned_pane=not options.pane_id)

        self._panes[TEAM_LEAD_AGENT_TYPE] = pane_id
        return pane_id

    async def _send_to_pane(
        self, pane_id: str, command: str, *, owned_pane: bool
    ) -> None:
        """发送命令到 pane，失败时按所有权清理。

        Args:
            pane_id: 目标 pane
            command: 要发送的命令
            owned_pane: 如果为 True，send 失败时 kill 该 pane
        """
        try:
            await self._tmux.send_command(pane_id, command)
        except TmuxError as e:
            if owned_pane:
                with contextlib.suppress(TmuxError):
                    await self._tmux.kill_pane(pane_id)
            raise SpawnError(f"Failed to send command to pane: {e}") from e


def _build_spawn_command(cwd: str, cli_args: list[str]) -> str:
    """构建 cd + env vars + CLI 命令字符串。"""
    agent_cwd = shlex.quote(cwd or os.getcwd())
    return f"cd {agent_cwd} && {_ENV_PREFIX}" + shlex.join(cli_args)


def _find_claude_binary() -> str:
    """查找 claude 可执行文件路径。

    优先级:
    1. CC_TEAM_CLAUDE_BIN 环境变量
    2. PATH 中的 claude
    """
    env_path = os.environ.get("CC_TEAM_CLAUDE_BIN")
    if env_path:
        return env_path

    which_result = shutil.which("claude")
    if which_result:
        return which_result

    return "claude"  # fallback


def _add_permission_args(args: list[str], mode: PermissionMode) -> None:
    """添加权限模式相关的 CLI 参数。"""
    if mode == "bypassPermissions":
        args.append("--dangerously-skip-permissions")
    elif mode == "delegate":
        # delegate 映射为 acceptEdits（兼容旧版本）
        args.extend(["--permission-mode", "acceptEdits"])
    else:
        args.extend(["--permission-mode", mode])
