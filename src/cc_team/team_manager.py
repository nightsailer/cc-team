"""团队配置管理器（config.json CRUD）。

负责:
- 创建团队（初始化 config.json + 目录结构）
- 读取团队配置
- 添加/移除成员
- 销毁团队

所有写操作通过文件锁 + 原子写入保证一致性。
"""

from __future__ import annotations

from pathlib import Path

from cc_team import paths
from cc_team._serialization import (
    atomic_write_json,
    now_ms,
    read_json,
    team_config_from_dict,
    team_config_to_dict,
)
from cc_team.exceptions import AgentNotFoundError
from cc_team.filelock import FileLock
from cc_team.types import AGENT_COLORS, TEAM_LEAD_AGENT_TYPE, AgentColor, TeamConfig, TeamMember


class TeamManager:
    """config.json CRUD 管理器。

    每个实例绑定到一个团队名称。
    所有修改操作使用文件锁保护。
    """

    def __init__(self, team_name: str) -> None:
        self._team_name = team_name
        self._config_path = paths.team_config_path(team_name)
        self._lock = FileLock(paths.team_config_lock_path(team_name))

    @property
    def team_name(self) -> str:
        return self._team_name

    @property
    def config_path(self) -> Path:
        return self._config_path

    # ── 创建 ────────────────────────────────────────────────

    async def create(
        self,
        *,
        description: str = "",
        lead_name: str = "team-lead",
        lead_model: str = "claude-sonnet-4-6",
        lead_session_id: str = "",
        cwd: str = "",
    ) -> TeamConfig:
        """创建新团队，初始化 config.json 和目录结构。

        Returns:
            初始化后的 TeamConfig
        """
        team_dir = paths.team_dir(self._team_name)
        if self._config_path.exists():
            from cc_team.exceptions import TeamAlreadyExistsError
            raise TeamAlreadyExistsError(self._team_name)
        team_dir.mkdir(parents=True, exist_ok=True)

        # 创建 inboxes 目录
        paths.inboxes_dir(self._team_name).mkdir(parents=True, exist_ok=True)

        # 创建 tasks 目录
        paths.tasks_dir(self._team_name).mkdir(parents=True, exist_ok=True)

        lead_agent_id = f"{lead_name}@{self._team_name}"
        timestamp = now_ms()

        lead_member = TeamMember(
            agent_id=lead_agent_id,
            name=lead_name,
            agent_type="team-lead",
            model=lead_model,
            joined_at=timestamp,
            tmux_pane_id="",
            cwd=cwd,
        )

        config = TeamConfig(
            name=self._team_name,
            description=description,
            created_at=timestamp,
            lead_agent_id=lead_agent_id,
            lead_session_id=lead_session_id,
            members=[lead_member],
        )

        async with self._lock.acquire():
            atomic_write_json(self._config_path, team_config_to_dict(config))

        return config

    # ── 读取 ────────────────────────────────────────────────

    def read(self) -> TeamConfig | None:
        """读取当前团队配置。

        Returns:
            TeamConfig 或 None（文件不存在）
        """
        data = read_json(self._config_path)
        if data is None:
            return None
        return team_config_from_dict(data)

    def get_member(self, name: str) -> TeamMember | None:
        """按名称查找成员。"""
        config = self.read()
        if config is None:
            return None
        for member in config.members:
            if member.name == name:
                return member
        return None

    def list_members(self) -> list[TeamMember]:
        """返回所有成员列表。"""
        config = self.read()
        if config is None:
            return []
        return config.members

    def list_teammates(self) -> list[TeamMember]:
        """返回 Teammates 列表（排除 team-lead）。"""
        return [m for m in self.list_members() if m.agent_type != TEAM_LEAD_AGENT_TYPE]

    # ── 添加成员 ────────────────────────────────────────────

    async def add_member(self, member: TeamMember) -> None:
        """添加成员到团队（锁保护 + 原子写入 + 唯一性检查）。

        颜色由外部分配（通过 next_color()），不在此处处理。

        Raises:
            FileNotFoundError: 团队配置不存在
            ValueError: 成员名称已存在
        """
        async with self._lock.acquire():
            data = read_json(self._config_path)
            if data is None:
                raise FileNotFoundError(f"Team config not found: {self._config_path}")
            config = team_config_from_dict(data)
            # 唯一性检查：防止重复成员
            if any(m.name == member.name for m in config.members):
                raise ValueError(f"Member already exists: {member.name!r}")
            config.members.append(member)
            atomic_write_json(self._config_path, team_config_to_dict(config))

    def next_color(self, config: TeamConfig | None = None) -> AgentColor:
        """分配下一个颜色（8 色循环）。

        基于 config.json 中现有成员数量推算索引，
        不依赖实例状态，进程重启后仍能正确分配。

        Args:
            config: 已读取的团队配置，为 None 时自动读取。
                    当调用方已持有 config 时传入可避免重复 IO。

        Returns:
            颜色字符串
        """
        if config is None:
            config = self.read()
        member_count = len(config.members) if config else 0
        return AGENT_COLORS[member_count % len(AGENT_COLORS)]

    # ── 移除成员 ────────────────────────────────────────────

    async def remove_member(self, name: str) -> None:
        """从团队中移除成员（Agent 终止后调用）。

        Raises:
            AgentNotFoundError: 成员不存在
        """
        async with self._lock.acquire():
            data = read_json(self._config_path)
            if data is None:
                raise FileNotFoundError(f"Team config not found: {self._config_path}")
            config = team_config_from_dict(data)
            original_len = len(config.members)
            config.members = [m for m in config.members if m.name != name]
            if len(config.members) == original_len:
                raise AgentNotFoundError(name)
            atomic_write_json(self._config_path, team_config_to_dict(config))

    # ── 更新成员 ────────────────────────────────────────────

    async def update_member(self, name: str, **updates: object) -> TeamMember:
        """更新成员字段。

        Args:
            name: 成员名称
            **updates: 要更新的字段及值

        Returns:
            更新后的 TeamMember

        Raises:
            AgentNotFoundError: 成员不存在
        """
        async with self._lock.acquire():
            data = read_json(self._config_path)
            if data is None:
                raise FileNotFoundError(f"Team config not found: {self._config_path}")
            config = team_config_from_dict(data)
            target = None
            for member in config.members:
                if member.name == name:
                    target = member
                    break
            if target is None:
                raise AgentNotFoundError(name)
            for field_name, value in updates.items():
                if hasattr(target, field_name):
                    setattr(target, field_name, value)
            atomic_write_json(self._config_path, team_config_to_dict(config))
            return target

    # ── 销毁 ────────────────────────────────────────────────

    async def destroy(self) -> None:
        """销毁团队（删除 config.json、inboxes、tasks 目录）。"""
        import shutil

        team_dir = paths.team_dir(self._team_name)
        if team_dir.exists():
            shutil.rmtree(team_dir)

        tasks_dir = paths.tasks_dir(self._team_name)
        if tasks_dir.exists():
            shutil.rmtree(tasks_dir)
