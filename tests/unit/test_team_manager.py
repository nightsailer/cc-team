"""team_manager.py 单元测试 — 团队配置 CRUD 验证。

测试覆盖:
- 团队创建（config.json + 目录结构）
- 配置读取（正常/不存在）
- 成员添加/查找/列表
- 成员移除（正常/不存在）
- 成员更新
- 颜色分配（8 色循环）
- 团队销毁
- 并发锁保护（原子写入）
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_member

import cc_team._serialization as ser_mod
import cc_team.paths as paths_mod
import cc_team.team_manager as tm_mod
from cc_team.exceptions import AgentNotFoundError
from cc_team.team_manager import TeamManager
from cc_team.types import AGENT_COLORS

# ── Fixtures ──────────────────────────────────────────────────

FIXED_MS = 1772193600000
FIXED_ISO = "2026-02-28T10:00:00.000Z"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 ~/.claude/ 到 tmp_path。"""
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(paths_mod, "claude_home", lambda: home)
    monkeypatch.setattr(ser_mod, "now_ms", lambda: FIXED_MS)
    monkeypatch.setattr(ser_mod, "now_iso", lambda: FIXED_ISO)
    # team_manager 通过 from import 持有独立引用，需要额外 patch
    monkeypatch.setattr(tm_mod, "now_ms", lambda: FIXED_MS)
    return home


@pytest.fixture
def manager(isolated_home: Path) -> TeamManager:
    """创建绑定到测试团队的 TeamManager 实例。"""
    return TeamManager("test-team")


# ── 团队创建 ──────────────────────────────────────────────────


class TestTeamCreate:
    """team.create() 测试。"""

    @pytest.mark.asyncio
    async def test_create_returns_config(self, manager: TeamManager) -> None:
        """create 应返回包含 lead 的 TeamConfig。"""
        config = await manager.create(description="unit test team")
        assert config.name == "test-team"
        assert config.description == "unit test team"
        assert config.created_at == FIXED_MS
        assert config.lead_agent_id == "team-lead@test-team"

    @pytest.mark.asyncio
    async def test_create_initializes_lead_member(self, manager: TeamManager) -> None:
        """创建的团队应包含一个 lead 成员。"""
        config = await manager.create()
        assert len(config.members) == 1
        lead = config.members[0]
        assert lead.name == "team-lead"
        assert lead.agent_type == "team-lead"
        assert lead.backend_id == ""
        assert lead.joined_at == FIXED_MS

    @pytest.mark.asyncio
    async def test_create_writes_config_json(self, manager: TeamManager) -> None:
        """create 应在磁盘上写入 config.json。"""
        await manager.create()
        assert manager.config_path.exists()

    @pytest.mark.asyncio
    async def test_create_makes_directories(
        self, manager: TeamManager, isolated_home: Path
    ) -> None:
        """create 应创建 team、inboxes、tasks 目录。"""
        await manager.create()
        assert paths_mod.team_dir("test-team").exists()
        assert paths_mod.inboxes_dir("test-team").exists()
        assert paths_mod.tasks_dir("test-team").exists()

    @pytest.mark.asyncio
    async def test_create_custom_lead(self, manager: TeamManager) -> None:
        """支持自定义 lead 名称和模型。"""
        config = await manager.create(
            lead_name="commander",
            lead_model="claude-opus-4-6",
            cwd="/workspace",
        )
        lead = config.members[0]
        assert lead.name == "commander"
        assert lead.model == "claude-opus-4-6"
        assert lead.cwd == "/workspace"
        assert config.lead_agent_id == "commander@test-team"


# ── 配置读取 ──────────────────────────────────────────────────


class TestTeamRead:
    """read() / get_member() / list_members() 测试。"""

    @pytest.mark.asyncio
    async def test_read_existing_config(self, manager: TeamManager) -> None:
        """读取已创建的团队配置。"""
        await manager.create(description="readable team")
        config = manager.read()
        assert config is not None
        assert config.name == "test-team"
        assert config.description == "readable team"

    def test_read_nonexistent_returns_none(self, manager: TeamManager) -> None:
        """未创建的团队 read 返回 None。"""
        assert manager.read() is None

    @pytest.mark.asyncio
    async def test_get_member_by_name(self, manager: TeamManager) -> None:
        """按名称查找成员。"""
        await manager.create()
        member = manager.get_member("team-lead")
        assert member is not None
        assert member.agent_type == "team-lead"

    @pytest.mark.asyncio
    async def test_get_member_not_found(self, manager: TeamManager) -> None:
        """查找不存在的成员返回 None。"""
        await manager.create()
        assert manager.get_member("ghost") is None

    def test_get_member_no_config(self, manager: TeamManager) -> None:
        """无 config.json 时 get_member 返回 None。"""
        assert manager.get_member("anyone") is None

    @pytest.mark.asyncio
    async def test_list_members(self, manager: TeamManager) -> None:
        """list_members 返回所有成员。"""
        await manager.create()
        members = manager.list_members()
        assert len(members) == 1
        assert members[0].name == "team-lead"

    def test_list_members_no_config(self, manager: TeamManager) -> None:
        """无 config.json 时 list_members 返回空列表。"""
        assert manager.list_members() == []


# ── 成员添加 ──────────────────────────────────────────────────


class TestTeamAddMember:
    """add_member() 测试。"""

    @pytest.mark.asyncio
    async def test_add_member_increases_count(self, manager: TeamManager) -> None:
        """添加成员后 members 数量增加。"""
        await manager.create()
        new_member = make_member(
            "worker-1", agent_type="general-purpose", model="claude-sonnet-4-6"
        )
        await manager.add_member(new_member)

        members = manager.list_members()
        assert len(members) == 2
        assert members[1].name == "worker-1"

    @pytest.mark.asyncio
    async def test_add_member_persisted(self, manager: TeamManager) -> None:
        """添加的成员应持久化到磁盘。"""
        await manager.create()
        member = make_member(
            "dev",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            backend_id="%2",
        )
        await manager.add_member(member)

        # 重新读取验证持久化
        config = manager.read()
        assert config is not None
        names = [m.name for m in config.members]
        assert "dev" in names

    @pytest.mark.asyncio
    async def test_add_member_no_config_raises(self, manager: TeamManager) -> None:
        """无 config.json 时 add_member 应抛出 FileNotFoundError。"""
        member = make_member(
            "ghost",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            backend_id="",
            cwd="",
        )
        with pytest.raises(FileNotFoundError):
            await manager.add_member(member)


# ── 颜色分配 ──────────────────────────────────────────────────


class TestColorAllocation:
    """next_color() 基于成员数量的 8 色循环测试。"""

    @pytest.mark.asyncio
    async def test_color_based_on_member_count(self, manager: TeamManager) -> None:
        """颜色应基于当前成员数量分配（0 成员 → 第 0 色）。"""
        await manager.create()
        # create 后已有 1 个 lead 成员
        assert manager.next_color() == AGENT_COLORS[1]

    @pytest.mark.asyncio
    async def test_color_increments_with_members(self, manager: TeamManager) -> None:
        """添加成员后颜色索引递增。"""
        await manager.create()
        colors = []
        for i in range(8):
            color = manager.next_color()
            colors.append(color)
            member = make_member(
                f"agent-{i}",
                agent_id=f"agent-{i}@test-team",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                backend_id=f"%{i + 10}",
                cwd="/tmp",
            )
            await manager.add_member(member)
        # 每次添加成员后 next_color 应返回不同颜色（前 8 个不重复）
        assert len(set(colors)) == 8

    @pytest.mark.asyncio
    async def test_color_wraps_around(self, manager: TeamManager) -> None:
        """超过 8 成员后颜色循环回第 1 个。"""
        await manager.create()
        # 添加 8 个成员（加上 lead 共 9 个）
        for i in range(8):
            member = make_member(
                f"agent-{i}",
                agent_id=f"agent-{i}@test-team",
                agent_type="general-purpose",
                model="claude-sonnet-4-6",
                backend_id=f"%{i + 10}",
                cwd="/tmp",
            )
            await manager.add_member(member)
        # 9 个成员: 9 % 8 = 1 → AGENT_COLORS[1]
        assert manager.next_color() == AGENT_COLORS[1]

    def test_no_config_returns_first_color(self, manager: TeamManager) -> None:
        """无 config 时返回第一个颜色（索引 0）。"""
        assert manager.next_color() == AGENT_COLORS[0]


# ── 成员移除 ──────────────────────────────────────────────────


class TestTeamRemoveMember:
    """remove_member() 测试。"""

    @pytest.mark.asyncio
    async def test_remove_existing_member(self, manager: TeamManager) -> None:
        """移除已有成员后 members 数量减少。"""
        await manager.create()
        member = make_member(
            "temp",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            backend_id="%3",
        )
        await manager.add_member(member)
        assert len(manager.list_members()) == 2

        await manager.remove_member("temp")
        assert len(manager.list_members()) == 1

    @pytest.mark.asyncio
    async def test_remove_nonexistent_raises(self, manager: TeamManager) -> None:
        """移除不存在的成员应抛出 AgentNotFoundError。"""
        await manager.create()
        with pytest.raises(AgentNotFoundError) as exc_info:
            await manager.remove_member("nobody")
        assert exc_info.value.agent_name == "nobody"

    @pytest.mark.asyncio
    async def test_remove_no_config_raises(self, manager: TeamManager) -> None:
        """无 config.json 时 remove_member 应抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            await manager.remove_member("anyone")


# ── 成员更新 ──────────────────────────────────────────────────


class TestTeamUpdateMember:
    """update_member() 测试。"""

    @pytest.mark.asyncio
    async def test_update_single_field(self, manager: TeamManager) -> None:
        """更新单个字段。"""
        await manager.create()
        updated = await manager.update_member("team-lead", cwd="/new/path")
        assert updated.cwd == "/new/path"

    @pytest.mark.asyncio
    async def test_update_multiple_fields(self, manager: TeamManager) -> None:
        """同时更新多个字段。"""
        await manager.create()
        updated = await manager.update_member(
            "team-lead",
            model="claude-opus-4-6",
            cwd="/updated",
        )
        assert updated.model == "claude-opus-4-6"
        assert updated.cwd == "/updated"

    @pytest.mark.asyncio
    async def test_update_persisted(self, manager: TeamManager) -> None:
        """更新应持久化到磁盘。"""
        await manager.create()
        await manager.update_member("team-lead", cwd="/persisted")

        config = manager.read()
        assert config is not None
        assert config.members[0].cwd == "/persisted"

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, manager: TeamManager) -> None:
        """更新不存在的成员应抛出 AgentNotFoundError。"""
        await manager.create()
        with pytest.raises(AgentNotFoundError):
            await manager.update_member("nobody", cwd="/x")

    @pytest.mark.asyncio
    async def test_update_no_config_raises(self, manager: TeamManager) -> None:
        """无 config.json 时 update_member 应抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            await manager.update_member("anyone", cwd="/x")


# ── 团队销毁 ──────────────────────────────────────────────────


class TestTeamDestroy:
    """destroy() 测试。"""

    @pytest.mark.asyncio
    async def test_destroy_removes_team_dir(
        self, manager: TeamManager, isolated_home: Path
    ) -> None:
        """destroy 应删除 team 目录。"""
        await manager.create()
        assert paths_mod.team_dir("test-team").exists()

        await manager.destroy()
        assert not paths_mod.team_dir("test-team").exists()

    @pytest.mark.asyncio
    async def test_destroy_removes_tasks_dir(
        self, manager: TeamManager, isolated_home: Path
    ) -> None:
        """destroy 应删除 tasks 目录。"""
        await manager.create()
        assert paths_mod.tasks_dir("test-team").exists()

        await manager.destroy()
        assert not paths_mod.tasks_dir("test-team").exists()

    @pytest.mark.asyncio
    async def test_destroy_removes_team_marker(
        self, manager: TeamManager, isolated_home: Path, tmp_path: Path
    ) -> None:
        """destroy with project_dir removes team-marker.json."""
        from cc_team._team_marker import read_team_marker, write_team_marker

        await manager.create()
        write_team_marker(tmp_path, "test-team")
        assert read_team_marker(tmp_path) is not None

        await manager.destroy(project_dir=tmp_path)
        assert read_team_marker(tmp_path) is None

    @pytest.mark.asyncio
    async def test_destroy_preserves_relay_data_by_default(
        self, manager: TeamManager, isolated_home: Path, tmp_path: Path
    ) -> None:
        """Default destroy preserves relay data directory."""
        await manager.create()

        # Create relay data directory with some content
        relay_dir = tmp_path / ".claude" / "cct" / "relay"
        relay_dir.mkdir(parents=True)
        session_dir = relay_dir / "session-001"
        session_dir.mkdir()
        (session_dir / "context.json").write_text('{"key": "value"}')

        await manager.destroy(project_dir=tmp_path)

        # Relay data should still exist
        assert relay_dir.exists()
        assert (session_dir / "context.json").exists()

    @pytest.mark.asyncio
    async def test_destroy_cleans_relay_data_when_requested(
        self, manager: TeamManager, isolated_home: Path, tmp_path: Path
    ) -> None:
        """clean_relay_data=True removes the relay directory."""
        await manager.create()

        # Create relay data directory with some content
        relay_dir = tmp_path / ".claude" / "cct" / "relay"
        relay_dir.mkdir(parents=True)
        session_dir = relay_dir / "session-001"
        session_dir.mkdir()
        (session_dir / "context.json").write_text('{"key": "value"}')
        (session_dir / "handoff.md").write_text("# Handoff")

        await manager.destroy(project_dir=tmp_path, clean_relay_data=True)

        # Relay data should be removed
        assert not relay_dir.exists()

    @pytest.mark.asyncio
    async def test_destroy_clean_relay_data_no_relay_dir_is_noop(
        self, manager: TeamManager, isolated_home: Path, tmp_path: Path
    ) -> None:
        """clean_relay_data=True when relay dir does not exist is a no-op."""
        await manager.create()

        # No relay directory exists
        relay_dir = tmp_path / ".claude" / "cct" / "relay"
        assert not relay_dir.exists()

        # Should not raise
        await manager.destroy(project_dir=tmp_path, clean_relay_data=True)

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_is_noop(self, manager: TeamManager) -> None:
        """Destroying a non-existent team should not raise."""
        await manager.destroy()  # Should not raise

    @pytest.mark.asyncio
    async def test_read_after_destroy_returns_none(self, manager: TeamManager) -> None:
        """Read after destroy returns None."""
        await manager.create()
        await manager.destroy()
        assert manager.read() is None


# ── 属性访问 ──────────────────────────────────────────────────


class TestTeamManagerProperties:
    """属性和构造函数测试。"""

    def test_team_name_property(self, manager: TeamManager) -> None:
        """team_name 属性返回绑定的团队名。"""
        assert manager.team_name == "test-team"

    def test_config_path_property(self, manager: TeamManager, isolated_home: Path) -> None:
        """config_path 指向正确的路径。"""
        expected = isolated_home / "teams" / "test-team" / "config.json"
        assert manager.config_path == expected


# ── 唯一性检查 [P2] ──────────────────────────────────────────


class TestMemberUniqueness:
    """add_member 重复成员名唯一性检查。"""

    @pytest.mark.asyncio
    async def test_duplicate_name_raises_value_error(self, manager: TeamManager) -> None:
        """重复添加同名成员应抛出 ValueError。"""
        await manager.create()
        member = make_member(
            "dup-agent",
            agent_id="dup@test-team",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            backend_id="%10",
            cwd="/tmp",
        )
        await manager.add_member(member)

        # 再次添加相同名称
        duplicate = make_member(
            "dup-agent",  # 同名,
            agent_id="dup-2@test-team",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            backend_id="%11",
            cwd="/tmp",
        )
        with pytest.raises(ValueError, match="already exists"):
            await manager.add_member(duplicate)

    @pytest.mark.asyncio
    async def test_duplicate_does_not_corrupt_config(self, manager: TeamManager) -> None:
        """重复添加失败后 config.json 不被污染。"""
        await manager.create()
        member = make_member(
            "worker",
            agent_id="w@test-team",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            backend_id="%10",
            cwd="/tmp",
        )
        await manager.add_member(member)
        count_before = len(manager.list_members())

        duplicate = make_member(
            "worker",
            agent_id="w2@test-team",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            backend_id="%11",
            cwd="/tmp",
        )
        with pytest.raises(ValueError):
            await manager.add_member(duplicate)

        assert len(manager.list_members()) == count_before


# ── 跨实例颜色稳定性 [P2] ─────────────────────────────────


class TestColorCrossInstanceStability:
    """next_color 跨不同 TeamManager 实例返回一致结果。"""

    @pytest.mark.asyncio
    async def test_two_instances_same_color(
        self, manager: TeamManager, isolated_home: Path
    ) -> None:
        """两个不同实例对同一团队调用 next_color 返回相同值。"""
        await manager.create()
        color_a = manager.next_color()

        # 创建全新实例
        manager_b = TeamManager("test-team")
        color_b = manager_b.next_color()

        assert color_a == color_b

    @pytest.mark.asyncio
    async def test_cross_instance_after_add(
        self, manager: TeamManager, isolated_home: Path
    ) -> None:
        """实例 A 添加成员后，实例 B 能感知到颜色变化。"""
        await manager.create()
        member = make_member(
            "agent-x",
            agent_id="x@test-team",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            backend_id="%10",
            cwd="/tmp",
        )
        await manager.add_member(member)

        # 全新实例应该看到 2 个成员
        manager_b = TeamManager("test-team")
        # 实例 A 和 B 的 next_color 应一致
        assert manager.next_color() == manager_b.next_color()


# ── update_member 无效字段 [P3] ──────────────────────────────


class TestUpdateMemberInvalidField:
    """update_member 传入不存在的字段时被静默忽略。"""

    @pytest.mark.asyncio
    async def test_invalid_field_silently_ignored(self, manager: TeamManager) -> None:
        """传入不存在的字段名不报错也不影响其他更新。"""
        await manager.create()
        updated = await manager.update_member(
            "team-lead",
            cwd="/valid-update",
            nonexistent_field="should-be-ignored",
        )
        assert updated.cwd == "/valid-update"
        assert (
            not hasattr(updated, "nonexistent_field")
            or getattr(updated, "nonexistent_field", None) is None
        )


# ── Session 管理 [R4] ──────────────────────────────────────


class TestSessionManagement:
    """get_lead_session_id / set_lead_session_id / rotate_session 测试。"""

    @pytest.mark.asyncio
    async def test_get_lead_session_id(self, manager: TeamManager) -> None:
        """创建后可获取 lead session ID。"""
        await manager.create(lead_session_id="sess-abc")
        assert manager.get_lead_session_id() == "sess-abc"

    def test_get_lead_session_id_no_config(self, manager: TeamManager) -> None:
        """无 config 时返回 None。"""
        assert manager.get_lead_session_id() is None

    @pytest.mark.asyncio
    async def test_set_lead_session_id(self, manager: TeamManager) -> None:
        """set 后 get 返回新值。"""
        await manager.create(lead_session_id="old-sess")
        await manager.set_lead_session_id("new-sess")
        assert manager.get_lead_session_id() == "new-sess"

    @pytest.mark.asyncio
    async def test_set_lead_session_id_no_config_raises(self, manager: TeamManager) -> None:
        """无 config 时 set 抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            await manager.set_lead_session_id("any")

    @pytest.mark.asyncio
    async def test_rotate_session_auto_uuid(self, manager: TeamManager) -> None:
        """rotate_session 无参数时自动生成 UUID4。"""
        await manager.create(lead_session_id="old")
        new_sid = await manager.rotate_session()
        assert new_sid != "old"
        assert len(new_sid) == 36  # UUID4 格式
        assert manager.get_lead_session_id() == new_sid

    @pytest.mark.asyncio
    async def test_rotate_session_explicit_id(self, manager: TeamManager) -> None:
        """rotate_session 指定 ID 时使用该值。"""
        await manager.create(lead_session_id="old")
        new_sid = await manager.rotate_session("explicit-id")
        assert new_sid == "explicit-id"
        assert manager.get_lead_session_id() == "explicit-id"


# ── register_member [R3] ──────────────────────────────────


class TestRegisterMember:
    """register_member() 测试。"""

    @pytest.mark.asyncio
    async def test_register_member_writes_config_and_inbox(
        self, manager: TeamManager, isolated_home: Path
    ) -> None:
        """register_member 应写入 config.json 并创建空 inbox。"""
        await manager.create()
        member = await manager.register_member(name="bot1")

        # 成员已注册
        found = manager.get_member("bot1")
        assert found is not None
        assert found.agent_type == "general-purpose"
        assert found.model == "claude-sonnet-4-6"
        assert found.is_active is False
        assert found.backend_id == ""
        assert found.color is not None

        # 返回值一致
        assert member.name == "bot1"
        assert member.color == found.color

        # inbox 文件已创建
        inbox_path = paths_mod.inbox_path("test-team", "bot1")
        assert inbox_path.exists()
        import json

        assert json.loads(inbox_path.read_text()) == []

    @pytest.mark.asyncio
    async def test_register_member_duplicate_raises(self, manager: TeamManager) -> None:
        """重复注册同名成员应抛出 ValueError。"""
        await manager.create()
        await manager.register_member(name="dup")
        with pytest.raises(ValueError, match="already exists"):
            await manager.register_member(name="dup")

    @pytest.mark.asyncio
    async def test_register_member_color_allocation(self, manager: TeamManager) -> None:
        """连续注册应分配不同颜色。"""
        await manager.create()
        m1 = await manager.register_member(name="a1")
        m2 = await manager.register_member(name="a2")
        m3 = await manager.register_member(name="a3")
        colors = [m1.color, m2.color, m3.color]
        assert len(set(colors)) == 3  # 三个不同颜色

    @pytest.mark.asyncio
    async def test_register_member_custom_params(self, manager: TeamManager) -> None:
        """支持自定义 agent_type、model、backend_type。"""
        await manager.create()
        member = await manager.register_member(
            name="custom",
            agent_type="Explore",
            model="claude-opus-4-6",
            cwd="/workspace",
            backend_type="agent-sdk",
        )
        assert member.agent_type == "Explore"
        assert member.model == "claude-opus-4-6"
        assert member.cwd == "/workspace"
        assert member.backend_type == "agent-sdk"

    @pytest.mark.asyncio
    async def test_register_member_no_config_raises(self, manager: TeamManager) -> None:
        """无 config.json 时 register_member 应抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            await manager.register_member(name="ghost")
