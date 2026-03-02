"""inbox.py 单元测试 — Inbox 文件 I/O 验证。

测试覆盖:
- 消息写入（追加 + 新建）
- 初始 prompt 写入
- 消息读取（全部/未读）
- 标记已读（原子操作）
- 未读检测
- mtime 查询
- 路径与属性
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cc_team._serialization as ser_mod
import cc_team.inbox as inbox_mod
import cc_team.paths as paths_mod
from cc_team.inbox import InboxIO
from cc_team.types import AgentColor, InboxMessage

# ── Fixtures ──────────────────────────────────────────────────

FIXED_ISO = "2026-02-28T10:00:00.000Z"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 ~/.claude/ 到 tmp_path。"""
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(paths_mod, "claude_home", lambda: home)
    monkeypatch.setattr(ser_mod, "now_iso", lambda: FIXED_ISO)
    # inbox.py 通过 from import 持有独立引用
    monkeypatch.setattr(inbox_mod, "now_iso", lambda: FIXED_ISO)
    return home


@pytest.fixture
def inbox(isolated_home: Path) -> InboxIO:
    """创建测试用 InboxIO 实例。"""
    # 确保 inboxes 目录存在
    paths_mod.inboxes_dir("test-team").mkdir(parents=True, exist_ok=True)
    return InboxIO("test-team", "worker-1")


def _make_msg(
    from_: str = "sender",
    text: str = "hello",
    read: bool = False,
    summary: str | None = None,
    color: AgentColor | None = None,
) -> InboxMessage:
    """工厂函数：快速构建 InboxMessage。"""
    return InboxMessage(
        from_=from_,
        text=text,
        timestamp=FIXED_ISO,
        read=read,
        summary=summary,
        color=color,
    )


# ── 消息写入 ──────────────────────────────────────────────────


class TestInboxWrite:
    """write() 测试。"""

    @pytest.mark.asyncio
    async def test_write_creates_inbox_file(self, inbox: InboxIO) -> None:
        """首次 write 应创建 inbox 文件。"""
        assert not inbox.inbox_path.exists()
        await inbox.write(_make_msg())
        assert inbox.inbox_path.exists()

    @pytest.mark.asyncio
    async def test_write_appends_message(self, inbox: InboxIO) -> None:
        """多次 write 应追加消息。"""
        await inbox.write(_make_msg(text="first"))
        await inbox.write(_make_msg(text="second"))

        messages = inbox.read_all()
        assert len(messages) == 2
        assert messages[0].text == "first"
        assert messages[1].text == "second"

    @pytest.mark.asyncio
    async def test_write_preserves_fields(self, inbox: InboxIO) -> None:
        """write 应保留所有字段。"""
        msg = _make_msg(
            from_="lead",
            text="task assignment",
            summary="New task",
            color="blue",
        )
        await inbox.write(msg)

        result = inbox.read_all()[0]
        assert result.from_ == "lead"
        assert result.text == "task assignment"
        assert result.summary == "New task"
        assert result.color == "blue"
        assert result.read is False

    @pytest.mark.asyncio
    async def test_write_optional_fields_omitted(self, inbox: InboxIO) -> None:
        """无 summary/color 时不写入这些字段（协议要求）。"""
        msg = _make_msg()  # summary=None, color=None
        await inbox.write(msg)

        result = inbox.read_all()[0]
        assert result.summary is None
        assert result.color is None


# ── 初始 Prompt ──────────────────────────────────────────────


class TestInboxInitialPrompt:
    """write_initial_prompt() 测试。"""

    @pytest.mark.asyncio
    async def test_initial_prompt_creates_file(self, inbox: InboxIO) -> None:
        """初始 prompt 创建新 inbox 文件。"""
        await inbox.write_initial_prompt("lead", "Start working on task X")
        assert inbox.inbox_path.exists()

    @pytest.mark.asyncio
    async def test_initial_prompt_is_first_message(self, inbox: InboxIO) -> None:
        """初始 prompt 应作为第一条消息。"""
        await inbox.write_initial_prompt("lead", "Your mission")
        messages = inbox.read_all()
        assert len(messages) == 1
        assert messages[0].from_ == "lead"
        assert messages[0].text == "Your mission"
        assert messages[0].read is False

    @pytest.mark.asyncio
    async def test_initial_prompt_no_summary_no_color(self, inbox: InboxIO) -> None:
        """初始 prompt 无 summary 和 color（协议要求）。"""
        await inbox.write_initial_prompt("lead", "Go")
        msg = inbox.read_all()[0]
        assert msg.summary is None
        assert msg.color is None

    @pytest.mark.asyncio
    async def test_initial_prompt_overwrites_existing(self, inbox: InboxIO) -> None:
        """初始 prompt 覆盖已有内容。"""
        await inbox.write(_make_msg(text="old"))
        await inbox.write_initial_prompt("lead", "new prompt")

        messages = inbox.read_all()
        assert len(messages) == 1
        assert messages[0].text == "new prompt"

    @pytest.mark.asyncio
    async def test_initial_prompt_uses_fixed_time(self, inbox: InboxIO) -> None:
        """初始 prompt 使用 now_iso() 生成时间戳。"""
        await inbox.write_initial_prompt("lead", "test")
        msg = inbox.read_all()[0]
        assert msg.timestamp == FIXED_ISO


# ── 消息读取 ──────────────────────────────────────────────────


class TestInboxRead:
    """read_all() / read_unread() 测试。"""

    @pytest.mark.asyncio
    async def test_read_all_empty(self, inbox: InboxIO) -> None:
        """无文件时 read_all 返回空列表。"""
        assert inbox.read_all() == []

    @pytest.mark.asyncio
    async def test_read_all_returns_all_messages(self, inbox: InboxIO) -> None:
        """read_all 返回所有消息（含已读）。"""
        await inbox.write(_make_msg(text="a", read=False))
        await inbox.write(_make_msg(text="b", read=True))

        messages = inbox.read_all()
        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_read_unread_filters(self, inbox: InboxIO) -> None:
        """read_unread 仅返回 read=False 的消息。"""
        await inbox.write(_make_msg(text="unread", read=False))
        await inbox.write(_make_msg(text="read", read=True))

        unread = inbox.read_unread()
        assert len(unread) == 1
        assert unread[0].text == "unread"

    @pytest.mark.asyncio
    async def test_read_unread_does_not_mark(self, inbox: InboxIO) -> None:
        """read_unread 不修改 read 状态（只读操作）。"""
        await inbox.write(_make_msg(text="msg"))
        inbox.read_unread()
        # 再次读取仍为未读
        assert len(inbox.read_unread()) == 1


# ── 标记已读 ──────────────────────────────────────────────────


class TestInboxMarkRead:
    """mark_read() 测试。"""

    @pytest.mark.asyncio
    async def test_mark_read_returns_marked_messages(self, inbox: InboxIO) -> None:
        """mark_read 返回刚标记的消息列表。"""
        await inbox.write(_make_msg(text="a"))
        await inbox.write(_make_msg(text="b"))

        marked = await inbox.mark_read()
        assert len(marked) == 2
        assert marked[0].text == "a"
        assert marked[1].text == "b"

    @pytest.mark.asyncio
    async def test_mark_read_persists(self, inbox: InboxIO) -> None:
        """mark_read 应将消息状态持久化为已读。"""
        await inbox.write(_make_msg(text="msg"))
        await inbox.mark_read()

        # 重新读取，应无未读
        assert inbox.read_unread() == []
        all_msgs = inbox.read_all()
        assert all_msgs[0].read is True

    @pytest.mark.asyncio
    async def test_mark_read_already_read_noop(self, inbox: InboxIO) -> None:
        """所有消息都已读时 mark_read 返回空列表。"""
        await inbox.write(_make_msg(read=True))
        marked = await inbox.mark_read()
        assert marked == []

    @pytest.mark.asyncio
    async def test_mark_read_partial(self, inbox: InboxIO) -> None:
        """只标记未读消息，已读消息不受影响。"""
        await inbox.write(_make_msg(text="old", read=True))
        await inbox.write(_make_msg(text="new", read=False))

        marked = await inbox.mark_read()
        assert len(marked) == 1
        assert marked[0].text == "new"

    @pytest.mark.asyncio
    async def test_mark_read_empty_inbox(self, inbox: InboxIO) -> None:
        """空 inbox 调用 mark_read 返回空列表。"""
        marked = await inbox.mark_read()
        assert marked == []


# ── 未读检测 ──────────────────────────────────────────────────


class TestInboxHasUnread:
    """has_unread() 测试。"""

    @pytest.mark.asyncio
    async def test_has_unread_true(self, inbox: InboxIO) -> None:
        """有未读消息时返回 True。"""
        await inbox.write(_make_msg(read=False))
        assert inbox.has_unread() is True

    @pytest.mark.asyncio
    async def test_has_unread_false_all_read(self, inbox: InboxIO) -> None:
        """所有消息已读时返回 False。"""
        await inbox.write(_make_msg(read=True))
        assert inbox.has_unread() is False

    def test_has_unread_empty(self, inbox: InboxIO) -> None:
        """无文件时返回 False。"""
        assert inbox.has_unread() is False


# ── mtime 查询 ───────────────────────────────────────────────


class TestInboxMtime:
    """mtime_ns() 测试。"""

    def test_mtime_nonexistent_returns_zero(self, inbox: InboxIO) -> None:
        """文件不存在时返回 0。"""
        assert inbox.mtime_ns() == 0

    @pytest.mark.asyncio
    async def test_mtime_after_write(self, inbox: InboxIO) -> None:
        """写入后 mtime_ns 应大于 0。"""
        await inbox.write(_make_msg())
        assert inbox.mtime_ns() > 0

    @pytest.mark.asyncio
    async def test_mtime_increases_on_write(self, inbox: InboxIO) -> None:
        """连续写入后 mtime 应不减。"""
        await inbox.write(_make_msg(text="first"))
        mtime1 = inbox.mtime_ns()
        await inbox.write(_make_msg(text="second"))
        mtime2 = inbox.mtime_ns()
        assert mtime2 >= mtime1


# ── 属性 ─────────────────────────────────────────────────────


# ── ensure_exists ───────────────────────────────────────────


class TestInboxEnsureExists:
    """ensure_exists() 测试。"""

    @pytest.mark.asyncio
    async def test_ensure_exists_creates_empty_array(self, inbox: InboxIO) -> None:
        """不存在时创建包含空 JSON 数组的文件。"""
        assert not inbox.inbox_path.exists()
        await inbox.ensure_exists()
        assert inbox.inbox_path.exists()
        assert inbox.read_all() == []

    @pytest.mark.asyncio
    async def test_ensure_exists_no_overwrite(self, inbox: InboxIO) -> None:
        """已有内容时不覆盖。"""
        await inbox.write(_make_msg(text="existing"))
        await inbox.ensure_exists()
        messages = inbox.read_all()
        assert len(messages) == 1
        assert messages[0].text == "existing"

    @pytest.mark.asyncio
    async def test_ensure_exists_idempotent(self, inbox: InboxIO) -> None:
        """多次调用幂等。"""
        await inbox.ensure_exists()
        await inbox.ensure_exists()
        assert inbox.read_all() == []


# ── 属性 ─────────────────────────────────────────────────────


class TestInboxProperties:
    """属性测试。"""

    def test_inbox_path(self, inbox: InboxIO, isolated_home: Path) -> None:
        """inbox_path 指向正确路径。"""
        expected = (
            isolated_home / "teams" / "test-team" / "inboxes" / "worker-1.json"
        )
        assert inbox.inbox_path == expected
