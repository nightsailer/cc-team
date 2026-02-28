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
        """正常 spawn 流程：成员注册 + inbox 写入 + pane_id 更新。"""
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
