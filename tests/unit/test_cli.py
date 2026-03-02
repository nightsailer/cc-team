"""cli.py 单元测试 — CLI 子命令和入口验证。

测试覆盖:
- main() 入口行为（无命令、异常处理）
- _require_team 验证
- agent spawn（正常流程 + 回滚）
- agent kill（正常 + agent 不存在）
- team create/info/destroy
- task update 无字段报错
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from conftest import FIXED_MS

import cc_team.paths as paths_mod
from cc_team.cli import _build_parser, main
from cc_team.team_manager import TeamManager
from cc_team.types import TeamMember

# isolated_home fixture 由 tests/conftest.py 提供


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
async def team(isolated_home: Path) -> TeamManager:
    """创建一个测试团队（异步 fixture）。"""
    mgr = TeamManager("test-team")
    await mgr.create(description="test")
    return mgr


# ── main() 入口 ────────────────────────────────────────────


class TestMainEntry:
    """main() 入口行为测试。"""

    def test_no_command_exits_1(self) -> None:
        """无子命令时 exit(1)。"""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    def test_unknown_command_exits(self) -> None:
        """未知命令时 exit。"""
        with pytest.raises(SystemExit):
            main(["--team-name", "t", "nonexistent"])

    def test_keyboard_interrupt_exits_130(self) -> None:
        """KeyboardInterrupt 应 exit(130)。"""
        with patch("cc_team.cli.asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = KeyboardInterrupt
            with pytest.raises(SystemExit) as exc_info:
                main(["--team-name", "t", "team", "info"])
            assert exc_info.value.code == 130

    def test_generic_exception_exits_1(self) -> None:
        """通用异常应 exit(1)。"""
        with patch("cc_team.cli.asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = RuntimeError("boom")
            with pytest.raises(SystemExit) as exc_info:
                main(["--team-name", "t", "team", "info"])
            assert exc_info.value.code == 1


# ── _require_team ───────────────────────────────────────────


class TestRequireTeam:
    """_require_team 验证。"""

    def test_missing_team_name_exits(self) -> None:
        """缺失 --team-name 时 exit(1)。"""
        with pytest.raises(SystemExit) as exc_info:
            # team info 需要 --team-name
            main(["team", "info"])
        assert exc_info.value.code == 1


# ── agent spawn ─────────────────────────────────────────────


class TestAgentSpawn:
    """agent spawn CLI 测试。"""

    @pytest.mark.asyncio
    async def test_spawn_team_not_found_exits(self, isolated_home: Path) -> None:
        """团队不存在时 exit(1)。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "ghost-team",
            "agent", "spawn", "--name", "a", "--prompt", "hi",
        ])
        with pytest.raises(SystemExit):
            await args.func(args)

    @pytest.mark.asyncio
    async def test_spawn_normal_flow(
        self, team: TeamManager, isolated_home: Path
    ) -> None:
        """Normal spawn flow: register member + write inbox + update backend_id."""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "agent", "spawn", "--name", "dev", "--prompt", "Do work",
        ])

        # Mock ProcessManager（惰性导入在函数内部，需 patch 源模块）
        with patch("cc_team.process_manager.ProcessManager") as MockPM:
            mock_pm = MockPM.return_value
            mock_pm.spawn = AsyncMock(return_value="%42")

            await args.func(args)

        # 成员应已注册
        member = team.get_member("dev")
        assert member is not None
        assert member.tmux_pane_id == "%42"

        # inbox 应有初始 prompt
        inbox_path = paths_mod.inbox_path("test-team", "dev")
        assert inbox_path.exists()
        msgs = json.loads(inbox_path.read_text())
        assert msgs[0]["text"] == "Do work"

    @pytest.mark.asyncio
    async def test_spawn_failure_rollback(
        self, team: TeamManager, isolated_home: Path
    ) -> None:
        """进程启动失败时，成员应被回滚。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "agent", "spawn", "--name", "doomed", "--prompt", "hi",
        ])

        with patch("cc_team.process_manager.ProcessManager") as MockPM:
            mock_pm = MockPM.return_value
            mock_pm.spawn = AsyncMock(side_effect=RuntimeError("tmux broken"))

            with pytest.raises(RuntimeError, match="tmux broken"):
                await args.func(args)

        # 成员应被移除（回滚）
        assert team.get_member("doomed") is None


# ── agent register ──────────────────────────────────────────


class TestAgentRegister:
    """agent register CLI 测试。"""

    @pytest.mark.asyncio
    async def test_register_team_not_found_exits(self, isolated_home: Path) -> None:
        """团队不存在时 exit(1)。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "ghost-team",
            "agent", "register", "--name", "bot1",
        ])
        with pytest.raises(SystemExit):
            await args.func(args)

    @pytest.mark.asyncio
    async def test_register_normal_flow(
        self, team: TeamManager, isolated_home: Path,
    ) -> None:
        """正常 register：config 写入 + inbox 创建 + 无 pane。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "agent", "register", "--name", "bot1",
        ])
        await args.func(args)

        # 成员应已注册
        member = team.get_member("bot1")
        assert member is not None
        assert member.is_active is False
        assert member.tmux_pane_id == ""
        assert member.color is not None

        # inbox 应存在且为空数组
        import cc_team.paths as paths_mod
        inbox_path = paths_mod.inbox_path("test-team", "bot1")
        assert inbox_path.exists()
        msgs = json.loads(inbox_path.read_text())
        assert msgs == []

    @pytest.mark.asyncio
    async def test_register_json_output(
        self, team: TeamManager, isolated_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json 输出 JSON 格式。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team", "--json",
            "agent", "register", "--name", "bot2",
        ])
        await args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["name"] == "bot2"
        assert data["active"] is False
        assert "color" in data

    @pytest.mark.asyncio
    async def test_register_then_message_send(
        self, team: TeamManager, isolated_home: Path,
    ) -> None:
        """register 后可通过 message send 发送消息。"""
        from cc_team.message_builder import MessageBuilder

        # 先 register
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "agent", "register", "--name", "bot3",
        ])
        await args.func(args)

        # 然后发送消息
        builder = MessageBuilder("test-team")
        await builder.send_plain("bot3", "hello from lead")

        # 验证消息写入
        import cc_team.paths as paths_mod
        inbox_path = paths_mod.inbox_path("test-team", "bot3")
        msgs = json.loads(inbox_path.read_text())
        texts = [m["text"] for m in msgs]
        assert "hello from lead" in texts

    @pytest.mark.asyncio
    async def test_register_custom_type_and_model(
        self, team: TeamManager, isolated_home: Path,
    ) -> None:
        """支持 --type 和 --model 自定义参数。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "agent", "register", "--name", "explorer",
            "--type", "Explore", "--model", "claude-haiku-4-5-20251001",
        ])
        await args.func(args)

        member = team.get_member("explorer")
        assert member is not None
        assert member.agent_type == "Explore"
        assert member.model == "claude-haiku-4-5-20251001"


# ── agent kill ──────────────────────────────────────────────


class TestAgentKill:
    """agent kill CLI 测试。"""

    @pytest.mark.asyncio
    async def test_kill_not_found_exits(
        self, team: TeamManager, isolated_home: Path
    ) -> None:
        """kill 不存在的 agent 应 exit(1)。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "agent", "kill", "--name", "ghost",
        ])
        with pytest.raises(SystemExit):
            await args.func(args)

    @pytest.mark.asyncio
    async def test_kill_normal_flow(
        self, team: TeamManager, isolated_home: Path
    ) -> None:
        """正常 kill 流程：kill_pane + remove_member。"""
        # 先添加一个成员
        member = TeamMember(
            agent_id="victim@test-team",
            name="victim",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            joined_at=FIXED_MS,
            tmux_pane_id="%99",
            cwd="/tmp",
            is_active=True,
            backend_type="tmux",
        )
        await team.add_member(member)

        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team", "--quiet",
            "agent", "kill", "--name", "victim",
        ])

        with patch("cc_team.tmux.TmuxManager") as MockTmux:
            mock_tmux = MockTmux.return_value
            mock_tmux.kill_pane = AsyncMock()

            await args.func(args)

        # 成员应已被移除
        assert team.get_member("victim") is None

    @pytest.mark.asyncio
    async def test_kill_pane_exception_still_removes_member(
        self, team: TeamManager, isolated_home: Path
    ) -> None:
        """kill_pane 异常时仍应从团队移除成员。"""
        member = TeamMember(
            agent_id="dead@test-team",
            name="dead-agent",
            agent_type="general-purpose",
            model="claude-sonnet-4-6",
            joined_at=FIXED_MS,
            tmux_pane_id="%88",
            cwd="/tmp",
            is_active=True,
            backend_type="tmux",
        )
        await team.add_member(member)

        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team", "--quiet",
            "agent", "kill", "--name", "dead-agent",
        ])

        with patch("cc_team.tmux.TmuxManager") as MockTmux:
            mock_tmux = MockTmux.return_value
            mock_tmux.kill_pane = AsyncMock(side_effect=Exception("pane gone"))

            await args.func(args)

        # 即使 kill_pane 失败，成员仍应被移除
        assert team.get_member("dead-agent") is None


# ── task update ─────────────────────────────────────────────


class TestTaskUpdate:
    """task update CLI 测试。"""

    def test_no_update_fields_exits(self, isolated_home: Path) -> None:
        """无更新字段时 exit(1)。"""
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--team-name", "test-team",
                "task", "update", "--id", "1",
            ])
        assert exc_info.value.code == 1


# ── JSON 输出格式 ───────────────────────────────────────────


class TestJsonOutput:
    """--json 输出格式验证。"""

    @pytest.mark.asyncio
    async def test_team_create_json(
        self, isolated_home: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """team create --json 输出 JSON。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "json-team", "--json",
            "team", "create", "--name", "json-team", "--description", "test",
        ])
        await args.func(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["name"] == "json-team"

    @pytest.mark.asyncio
    async def test_agent_list_empty_json(
        self, team: TeamManager, isolated_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """agent list --json 空列表输出 JSON 数组。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team", "--json",
            "agent", "list",
        ])
        await args.func(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)


# ── skill 命令 ─────────────────────────────────────────────


class TestSkillCommand:
    """skill subcommand tests."""

    @pytest.mark.asyncio
    async def test_skill_markdown_output(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """skill outputs Markdown reference document."""
        parser = _build_parser()
        args = parser.parse_args(["skill"])
        await args.func(args)
        captured = capsys.readouterr()
        assert "# cct Skill Reference" in captured.out
        assert "team create" in captured.out

    @pytest.mark.asyncio
    async def test_skill_json_output(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json skill outputs structured JSON with version and sections."""
        parser = _build_parser()
        args = parser.parse_args(["--json", "skill"])
        await args.func(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "version" in data
        assert "sections" in data
        assert isinstance(data["sections"], list)
        assert len(data["sections"]) > 0
        # Every section has title and content
        for section in data["sections"]:
            assert "title" in section
            assert "content" in section

    def test_skill_no_team_name_required(self) -> None:
        """skill must work without --team-name."""
        parser = _build_parser()
        args = parser.parse_args(["skill"])
        # Should have func set (not fall through to help)
        assert hasattr(args, "func")
        # team_name should be None (not required)
        assert args.team_name is None


# ── team takeover ──────────────────────────────────────────


class TestTeamTakeover:
    """team takeover CLI 测试。"""

    @pytest.mark.asyncio
    async def test_takeover_team_not_found_exits(self, isolated_home: Path) -> None:
        """团队不存在时 exit(1)。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "ghost", "team", "takeover",
        ])
        with pytest.raises(SystemExit):
            await args.func(args)

    @pytest.mark.asyncio
    async def test_takeover_normal_flow(
        self, team: TeamManager, isolated_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """正常 takeover：轮转 session + spawn TL + 更新 pane。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "team", "takeover",
        ])

        with patch("cc_team.process_manager.ProcessManager") as MockPM:
            mock_pm = MockPM.return_value
            mock_pm.spawn_lead = AsyncMock(return_value="%50")

            await args.func(args)

        # session 应已轮转
        config = team.read()
        assert config is not None
        assert config.lead_session_id != ""

        # TL backend_id should be updated
        lead = team.get_member("team-lead")
        assert lead is not None
        assert lead.tmux_pane_id == "%50"

        captured = capsys.readouterr()
        assert "Takeover complete" in captured.out

    @pytest.mark.asyncio
    async def test_takeover_tl_running_no_force_exits(
        self, team: TeamManager, isolated_home: Path,
    ) -> None:
        """TL 已运行且无 --force 时 exit(1)。"""
        # Update TL's backend_id first
        await team.update_member("team-lead", tmux_pane_id="%42")

        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "team", "takeover",
        ])

        with patch("cc_team.tmux.TmuxManager") as MockTmux:
            mock_tmux = MockTmux.return_value
            mock_tmux.is_pane_alive = AsyncMock(return_value=True)

            with pytest.raises(SystemExit):
                await args.func(args)

    @pytest.mark.asyncio
    async def test_takeover_force_overrides_running_tl(
        self, team: TeamManager, isolated_home: Path,
    ) -> None:
        """--force 强制接管已运行的 TL。"""
        await team.update_member("team-lead", tmux_pane_id="%42")

        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "team", "takeover", "--force",
        ])

        with patch("cc_team.tmux.TmuxManager") as MockTmux, \
             patch("cc_team.process_manager.ProcessManager") as MockPM:
            mock_tmux = MockTmux.return_value
            mock_tmux.is_pane_alive = AsyncMock(return_value=True)
            mock_pm = MockPM.return_value
            mock_pm.spawn_lead = AsyncMock(return_value="%60")

            await args.func(args)

        lead = team.get_member("team-lead")
        assert lead is not None
        assert lead.tmux_pane_id == "%60"


# ── team relay ─────────────────────────────────────────────


class TestTeamRelay:
    """team relay CLI 测试。"""

    @pytest.mark.asyncio
    async def test_relay_team_not_found_exits(self, isolated_home: Path) -> None:
        """团队不存在时 exit(1)。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "ghost", "team", "relay",
        ])
        with pytest.raises(SystemExit):
            await args.func(args)

    @pytest.mark.asyncio
    async def test_relay_normal_flow(
        self, team: TeamManager, isolated_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """正常 relay：退出旧 TL + 轮转 session + spawn 新 TL。"""
        old_config = team.read()
        assert old_config is not None
        old_sid = old_config.lead_session_id

        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "team", "relay",
        ])

        with patch("cc_team.process_manager.ProcessManager") as MockPM:
            mock_pm = MockPM.return_value
            mock_pm.spawn_lead = AsyncMock(return_value="%70")

            await args.func(args)

        # session 应已轮转
        config = team.read()
        assert config is not None
        assert config.lead_session_id != old_sid

        # TL pane 已更新
        lead = team.get_member("team-lead")
        assert lead is not None
        assert lead.tmux_pane_id == "%70"

        captured = capsys.readouterr()
        assert "Relay complete" in captured.out


# ── team session ───────────────────────────────────────────


class TestTeamSession:
    """team session CLI 测试。"""

    @pytest.mark.asyncio
    async def test_session_query(
        self, team: TeamManager, isolated_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无参数查询当前 session ID。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "team", "session",
        ])
        await args.func(args)
        captured = capsys.readouterr()
        config = team.read()
        assert config is not None
        assert config.lead_session_id in captured.out

    @pytest.mark.asyncio
    async def test_session_rotate(
        self, team: TeamManager, isolated_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--rotate 生成新 UUID。"""
        old_config = team.read()
        assert old_config is not None
        old_sid = old_config.lead_session_id

        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "team", "session", "--rotate",
        ])
        await args.func(args)

        config = team.read()
        assert config is not None
        assert config.lead_session_id != old_sid
        captured = capsys.readouterr()
        assert "Session rotated" in captured.out

    @pytest.mark.asyncio
    async def test_session_set(
        self, team: TeamManager, isolated_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--set 指定值。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "team", "session", "--set", "my-custom-id",
        ])
        await args.func(args)

        config = team.read()
        assert config is not None
        assert config.lead_session_id == "my-custom-id"
        captured = capsys.readouterr()
        assert "Session set" in captured.out

    @pytest.mark.asyncio
    async def test_session_not_found_exits(self, isolated_home: Path) -> None:
        """团队不存在时 exit(1)。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "ghost",
            "team", "session",
        ])
        with pytest.raises(SystemExit):
            await args.func(args)


# ── agent sync ────────────────────────────────────────────


class TestAgentSync:
    """agent sync CLI 测试。"""

    @pytest.mark.asyncio
    async def test_sync_team_not_found_exits(self, isolated_home: Path) -> None:
        """团队不存在时 exit(1)。"""
        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "ghost",
            "agent", "sync",
        ])
        with pytest.raises(SystemExit):
            await args.func(args)

    @pytest.mark.asyncio
    async def test_sync_normal_flow(
        self, team: TeamManager, isolated_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """正常 sync：存活 → synced，死亡 → inactive。"""
        # 添加一个 active agent
        member = TeamMember(
            agent_id="w@test-team", name="worker-1",
            agent_type="general-purpose", model="claude-sonnet-4-6",
            joined_at=FIXED_MS, tmux_pane_id="%68", cwd="/tmp",
            color="blue", is_active=True, backend_type="tmux",
        )
        await team.add_member(member)
        # 添加一个 dead agent
        dead_member = TeamMember(
            agent_id="d@test-team", name="dead-agent",
            agent_type="general-purpose", model="claude-sonnet-4-6",
            joined_at=FIXED_MS, tmux_pane_id="%55", cwd="/tmp",
            color="green", is_active=True, backend_type="tmux",
        )
        await team.add_member(dead_member)

        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team",
            "agent", "sync",
        ])

        with patch("cc_team.tmux.TmuxManager") as MockTmux:
            mock_tmux = MockTmux.return_value

            async def _is_alive(pane_id: str) -> bool:
                return pane_id == "%68"

            mock_tmux.is_pane_alive = AsyncMock(side_effect=_is_alive)

            await args.func(args)

        captured = capsys.readouterr()
        assert "worker-1" in captured.out
        assert "synced" in captured.out
        assert "dead-agent" in captured.out
        assert "inactive" in captured.out

        # dead-agent 应标记为 inactive
        dead = team.get_member("dead-agent")
        assert dead is not None
        assert dead.is_active is False

    @pytest.mark.asyncio
    async def test_sync_json_output(
        self, team: TeamManager, isolated_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--json 输出 JSON 格式。"""
        member = TeamMember(
            agent_id="w@test-team", name="worker-1",
            agent_type="general-purpose", model="claude-sonnet-4-6",
            joined_at=FIXED_MS, tmux_pane_id="%68", cwd="/tmp",
            color="blue", is_active=True, backend_type="tmux",
        )
        await team.add_member(member)

        parser = _build_parser()
        args = parser.parse_args([
            "--team-name", "test-team", "--json",
            "agent", "sync",
        ])

        with patch("cc_team.tmux.TmuxManager") as MockTmux:
            mock_tmux = MockTmux.return_value
            mock_tmux.is_pane_alive = AsyncMock(return_value=True)

            await args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "synced" in data
        assert "inactive" in data
        assert "worker-1" in data["synced"]
