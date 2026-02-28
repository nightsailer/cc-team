"""CCT CLI 全流程集成测试。

基于 specs/cct-tests.md 规格:
1. 建立新的 TEAM 团队
2. 加入一个工程师和一个测试员
3. 分别测试:
   (a) 新增一个队员
   (b) 下线一个队员
   (c) 解散团队

每新增一个队员，均向其发送一个消息并得到回馈（inbox 可读 = 消息通过验证）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from conftest import read_inbox

import cc_team.paths as paths_mod
from cc_team.cli import _build_parser

# ── 常量 ─────────────────────────────────────────────────────

TEAM_NAME = "integration-test"

# isolated_home fixture 由 tests/conftest.py 提供


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def parser():
    """共享 argparse 解析器。"""
    return _build_parser()


@pytest.fixture
def mock_process_manager():
    """Mock ProcessManager，避免真实 tmux 依赖。"""
    with patch("cc_team.process_manager.ProcessManager") as MockPM:
        mock_pm = MockPM.return_value
        mock_pm.spawn = AsyncMock(return_value="%0")
        yield mock_pm


@pytest.fixture
def mock_tmux():
    """Mock TmuxManager，避免真实 tmux 依赖。"""
    with patch("cc_team.tmux.TmuxManager") as MockTmux:
        mock_tmux = MockTmux.return_value
        mock_tmux.kill_pane = AsyncMock()
        yield mock_tmux


# ── 辅助函数 ─────────────────────────────────────────────────


async def _run_cmd(parser, argv: list[str]) -> None:
    """解析并执行 CLI 命令。"""
    args = parser.parse_args(argv)
    await args.func(args)


def _team_args(*extra: str) -> list[str]:
    """构建带 --team-name 的命令行参数。"""
    return ["--team-name", TEAM_NAME, "--json", *extra]


def _read_inbox(team_name: str, agent_name: str) -> list[dict]:
    """读取 agent inbox 文件内容（委托给 conftest.read_inbox）。"""
    return read_inbox(team_name, agent_name)


# ── 全流程测试 ───────────────────────────────────────────────


class TestFullLifecycle:
    """CCT CLI 完整生命周期集成测试。

    按 specs/cct-tests.md 规格，验证团队从创建到解散的全链路。
    """

    @pytest.mark.asyncio
    async def test_step1_create_team(
        self, isolated_home: Path, parser, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """步骤 1: 建立新的 TEAM 团队。"""
        await _run_cmd(parser, _team_args(
            "team", "create", "--name", TEAM_NAME, "--description", "集成测试团队",
        ))

        output = json.loads(capsys.readouterr().out)
        assert output["name"] == TEAM_NAME
        assert output["description"] == "集成测试团队"
        # Lead 应自动注册为第一个成员
        assert len(output["members"]) == 1
        assert output["members"][0]["name"] == "team-lead"

    @pytest.mark.asyncio
    async def test_step2_spawn_engineer_and_tester(
        self,
        isolated_home: Path,
        parser,
        mock_process_manager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """步骤 2: 加入一个工程师和一个测试员，并验证消息通达。"""
        # 创建团队
        await _run_cmd(parser, _team_args(
            "team", "create", "--name", TEAM_NAME, "--description", "测试",
        ))
        capsys.readouterr()  # 清空输出

        # Spawn 工程师
        mock_process_manager.spawn = AsyncMock(return_value="%1")
        await _run_cmd(parser, _team_args(
            "agent", "spawn", "--name", "engineer",
            "--prompt", "你是工程师，负责编写代码",
            "--type", "general-purpose",
        ))
        engineer_out = json.loads(capsys.readouterr().out)
        assert engineer_out["name"] == "engineer"
        assert engineer_out["pane_id"] == "%1"

        # Spawn 测试员
        mock_process_manager.spawn = AsyncMock(return_value="%2")
        await _run_cmd(parser, _team_args(
            "agent", "spawn", "--name", "tester",
            "--prompt", "你是测试员，负责编写测试",
            "--type", "general-purpose",
        ))
        tester_out = json.loads(capsys.readouterr().out)
        assert tester_out["name"] == "tester"
        assert tester_out["pane_id"] == "%2"

        # 验证: 工程师 inbox 应有初始 prompt
        engineer_inbox = _read_inbox(TEAM_NAME, "engineer")
        assert len(engineer_inbox) == 1
        assert engineer_inbox[0]["text"] == "你是工程师，负责编写代码"

        # 验证: 测试员 inbox 应有初始 prompt
        tester_inbox = _read_inbox(TEAM_NAME, "tester")
        assert len(tester_inbox) == 1
        assert tester_inbox[0]["text"] == "你是测试员，负责编写测试"

        # 向工程师发送消息
        await _run_cmd(parser, _team_args(
            "message", "send", "--to", "engineer",
            "--content", "请开始实现登录模块",
            "--summary", "任务指令",
        ))
        capsys.readouterr()  # 清空 message send 输出

        # 验证: 工程师 inbox 应有 2 条消息
        engineer_inbox = _read_inbox(TEAM_NAME, "engineer")
        assert len(engineer_inbox) == 2
        assert engineer_inbox[1]["text"] == "请开始实现登录模块"
        assert engineer_inbox[1]["summary"] == "任务指令"

        # 向测试员发送消息
        await _run_cmd(parser, _team_args(
            "message", "send", "--to", "tester",
            "--content", "请为登录模块编写测试",
            "--summary", "测试任务",
        ))
        capsys.readouterr()  # 清空 message send 输出

        # 验证: 测试员 inbox 应有 2 条消息
        tester_inbox = _read_inbox(TEAM_NAME, "tester")
        assert len(tester_inbox) == 2
        assert tester_inbox[1]["text"] == "请为登录模块编写测试"

        # 验证: 团队成员列表正确（lead + engineer + tester）
        await _run_cmd(parser, _team_args("agent", "list"))
        agents = json.loads(capsys.readouterr().out)
        names = {a["name"] for a in agents}
        assert names == {"engineer", "tester"}  # agent list 不含 lead（仅 Teammate）

    @pytest.mark.asyncio
    async def test_step3a_add_new_member(
        self,
        isolated_home: Path,
        parser,
        mock_process_manager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """步骤 3a: 新增一个队员，验证消息通达。"""
        # 建立团队 + 初始成员
        await _run_cmd(parser, _team_args(
            "team", "create", "--name", TEAM_NAME, "--description", "测试",
        ))
        capsys.readouterr()

        mock_process_manager.spawn = AsyncMock(return_value="%1")
        await _run_cmd(parser, _team_args(
            "agent", "spawn", "--name", "engineer",
            "--prompt", "工程师", "--type", "general-purpose",
        ))
        capsys.readouterr()

        # 新增队员: reviewer
        mock_process_manager.spawn = AsyncMock(return_value="%3")
        await _run_cmd(parser, _team_args(
            "agent", "spawn", "--name", "reviewer",
            "--prompt", "你是代码审查员",
            "--type", "general-purpose",
        ))
        reviewer_out = json.loads(capsys.readouterr().out)
        assert reviewer_out["name"] == "reviewer"

        # 验证: reviewer inbox 有初始 prompt
        reviewer_inbox = _read_inbox(TEAM_NAME, "reviewer")
        assert len(reviewer_inbox) == 1
        assert reviewer_inbox[0]["text"] == "你是代码审查员"

        # 发送消息验证通达
        await _run_cmd(parser, _team_args(
            "message", "send", "--to", "reviewer",
            "--content", "请审查 PR #42",
            "--summary", "审查请求",
        ))
        capsys.readouterr()  # 清空 message send 输出

        reviewer_inbox = _read_inbox(TEAM_NAME, "reviewer")
        assert len(reviewer_inbox) == 2
        assert reviewer_inbox[1]["text"] == "请审查 PR #42"

        # 验证团队成员数（lead + engineer + reviewer = config 中 3 人）
        await _run_cmd(parser, _team_args("team", "info"))
        team_info = json.loads(capsys.readouterr().out)
        assert len(team_info["members"]) == 3

    @pytest.mark.asyncio
    async def test_step3b_kill_member(
        self,
        isolated_home: Path,
        parser,
        mock_process_manager,
        mock_tmux,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """步骤 3b: 下线（kill）一个队员。"""
        # 建立团队 + 两个成员
        await _run_cmd(parser, _team_args(
            "team", "create", "--name", TEAM_NAME, "--description", "测试",
        ))
        capsys.readouterr()

        mock_process_manager.spawn = AsyncMock(return_value="%1")
        await _run_cmd(parser, _team_args(
            "agent", "spawn", "--name", "engineer",
            "--prompt", "工程师", "--type", "general-purpose",
        ))
        capsys.readouterr()

        mock_process_manager.spawn = AsyncMock(return_value="%2")
        await _run_cmd(parser, _team_args(
            "agent", "spawn", "--name", "tester",
            "--prompt", "测试员", "--type", "general-purpose",
        ))
        capsys.readouterr()

        # Kill 测试员
        await _run_cmd(parser, [
            "--team-name", TEAM_NAME, "--quiet",
            "agent", "kill", "--name", "tester",
        ])

        # 验证: tester 已从团队移除
        await _run_cmd(parser, _team_args("team", "info"))
        team_info = json.loads(capsys.readouterr().out)
        member_names = [m["name"] for m in team_info["members"]]
        assert "tester" not in member_names
        assert "engineer" in member_names
        assert "team-lead" in member_names

        # 验证: tmux kill_pane 被调用
        mock_tmux.kill_pane.assert_called_once_with("%2")

    @pytest.mark.asyncio
    async def test_step3c_destroy_team(
        self,
        isolated_home: Path,
        parser,
        mock_process_manager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """步骤 3c: 解散团队。"""
        # 建立团队 + 成员
        await _run_cmd(parser, _team_args(
            "team", "create", "--name", TEAM_NAME, "--description", "测试",
        ))
        capsys.readouterr()

        mock_process_manager.spawn = AsyncMock(return_value="%1")
        await _run_cmd(parser, _team_args(
            "agent", "spawn", "--name", "engineer",
            "--prompt", "工程师", "--type", "general-purpose",
        ))
        capsys.readouterr()

        # 确认团队目录和任务目录存在
        team_dir = paths_mod.team_dir(TEAM_NAME)
        tasks_dir = paths_mod.tasks_dir(TEAM_NAME)
        assert team_dir.exists()
        assert tasks_dir.exists()

        # 解散团队
        await _run_cmd(parser, [
            "--team-name", TEAM_NAME, "--quiet",
            "team", "destroy",
        ])

        # 验证: 团队目录已删除
        assert not team_dir.exists()
        assert not tasks_dir.exists()

        # 验证: team info 应报错（团队不存在）
        with pytest.raises(SystemExit):
            await _run_cmd(parser, _team_args("team", "info"))


# ── 消息通达验证 ─────────────────────────────────────────────


class TestMessageVerification:
    """验证消息发送与回馈机制。

    每新增一个队员，发送消息并通过 inbox 读取验证。
    """

    @pytest.mark.asyncio
    async def test_message_roundtrip(
        self,
        isolated_home: Path,
        parser,
        mock_process_manager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """消息发送 → inbox 写入 → 读取验证。"""
        # 创建团队
        await _run_cmd(parser, _team_args(
            "team", "create", "--name", TEAM_NAME, "--description", "消息测试",
        ))
        capsys.readouterr()

        # Spawn agent
        mock_process_manager.spawn = AsyncMock(return_value="%1")
        await _run_cmd(parser, _team_args(
            "agent", "spawn", "--name", "worker",
            "--prompt", "初始任务",
            "--type", "general-purpose",
        ))
        capsys.readouterr()

        # 发送消息
        await _run_cmd(parser, _team_args(
            "message", "send", "--to", "worker",
            "--content", "请汇报进度",
            "--summary", "进度查询",
        ))
        capsys.readouterr()

        # 通过 CLI 读取 inbox 验证
        await _run_cmd(parser, _team_args(
            "message", "read", "--agent", "worker", "--all",
        ))
        messages = json.loads(capsys.readouterr().out)

        # 应有 2 条: 初始 prompt + 后续消息
        assert len(messages) == 2
        assert messages[0]["text"] == "初始任务"
        assert messages[0]["from"] == "team-lead"
        assert messages[1]["text"] == "请汇报进度"
        assert messages[1]["summary"] == "进度查询"

    @pytest.mark.asyncio
    async def test_broadcast_to_all_agents(
        self,
        isolated_home: Path,
        parser,
        mock_process_manager,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """广播消息到所有 agent，验证各 inbox 均收到。"""
        # 创建团队 + 两个 agent
        await _run_cmd(parser, _team_args(
            "team", "create", "--name", TEAM_NAME, "--description", "广播测试",
        ))
        capsys.readouterr()

        for name, pane in [("alpha", "%1"), ("beta", "%2")]:
            mock_process_manager.spawn = AsyncMock(return_value=pane)
            await _run_cmd(parser, _team_args(
                "agent", "spawn", "--name", name,
                "--prompt", f"{name} 初始任务",
            ))
            capsys.readouterr()

        # 广播
        await _run_cmd(parser, _team_args(
            "message", "broadcast",
            "--content", "全员注意：代码冻结",
            "--summary", "紧急通知",
        ))

        # 验证: 两个 agent 的 inbox 都有广播消息
        for name in ("alpha", "beta"):
            inbox = _read_inbox(TEAM_NAME, name)
            assert len(inbox) == 2  # 初始 prompt + 广播
            assert inbox[1]["text"] == "全员注意：代码冻结"
            assert inbox[1]["summary"] == "紧急通知"
