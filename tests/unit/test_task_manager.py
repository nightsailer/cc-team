"""task_manager.py 单元测试 — 任务 CRUD + DAG 依赖管理。

测试覆盖:
- 任务创建（自增 ID、字段默认值）
- 任务读取（单个/列表/可用列表）
- 任务更新（字段修改、owner 哨兵值）
- 任务删除（状态标记 + 依赖清理）
- 依赖管理（双向链接、BFS 循环检测）
- 自增 ID 生成逻辑
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cc_team.paths as paths_mod
from cc_team.exceptions import CyclicDependencyError
from cc_team.task_manager import TaskManager


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 ~/.claude/ 到 tmp_path。"""
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(paths_mod, "claude_home", lambda: home)
    return home


@pytest.fixture
def mgr(isolated_home: Path) -> TaskManager:
    """创建绑定到测试团队的 TaskManager 实例。"""
    return TaskManager("test-team")


# ── 任务创建 ──────────────────────────────────────────────────


class TestTaskCreate:
    """create() 测试。"""

    @pytest.mark.asyncio
    async def test_create_returns_task(self, mgr: TaskManager) -> None:
        """create 返回带有自增 ID 的 TaskFile。"""
        task = await mgr.create(subject="First task", description="Details")
        assert task.id == "1"
        assert task.subject == "First task"
        assert task.description == "Details"
        assert task.status == "pending"

    @pytest.mark.asyncio
    async def test_auto_increment_id(self, mgr: TaskManager) -> None:
        """连续创建的任务 ID 自增。"""
        t1 = await mgr.create(subject="A", description="a")
        t2 = await mgr.create(subject="B", description="b")
        t3 = await mgr.create(subject="C", description="c")
        assert t1.id == "1"
        assert t2.id == "2"
        assert t3.id == "3"

    @pytest.mark.asyncio
    async def test_create_default_fields(self, mgr: TaskManager) -> None:
        """默认字段值正确。"""
        task = await mgr.create(subject="Test", description="d")
        assert task.active_form == ""
        assert task.owner is None
        assert task.blocks == []
        assert task.blocked_by == []
        assert task.metadata == {}

    @pytest.mark.asyncio
    async def test_create_with_optional_fields(self, mgr: TaskManager) -> None:
        """支持设置可选字段。"""
        task = await mgr.create(
            subject="Custom",
            description="d",
            active_form="Working on it",
            owner="worker-1",
            metadata={"priority": "high"},
        )
        assert task.active_form == "Working on it"
        assert task.owner == "worker-1"
        assert task.metadata == {"priority": "high"}

    @pytest.mark.asyncio
    async def test_create_persisted_to_disk(self, mgr: TaskManager) -> None:
        """创建的任务应写入磁盘。"""
        task = await mgr.create(subject="Saved", description="d")
        reloaded = mgr.read(task.id)
        assert reloaded is not None
        assert reloaded.subject == "Saved"

    @pytest.mark.asyncio
    async def test_create_makes_tasks_dir(self, mgr: TaskManager) -> None:
        """create 应自动创建 tasks 目录。"""
        assert not mgr.tasks_dir.exists()
        await mgr.create(subject="Init", description="d")
        assert mgr.tasks_dir.exists()


# ── 任务读取 ──────────────────────────────────────────────────


class TestTaskRead:
    """read() / list_all() / list_available() 测试。"""

    @pytest.mark.asyncio
    async def test_read_existing(self, mgr: TaskManager) -> None:
        """读取已有任务。"""
        created = await mgr.create(subject="X", description="d")
        task = mgr.read(created.id)
        assert task is not None
        assert task.subject == "X"

    def test_read_nonexistent(self, mgr: TaskManager) -> None:
        """读取不存在的任务返回 None。"""
        assert mgr.read("999") is None

    @pytest.mark.asyncio
    async def test_list_all_sorted(self, mgr: TaskManager) -> None:
        """list_all 按数字 ID 排序。"""
        await mgr.create(subject="C", description="d")
        await mgr.create(subject="A", description="d")
        await mgr.create(subject="B", description="d")
        tasks = mgr.list_all()
        assert [t.id for t in tasks] == ["1", "2", "3"]

    def test_list_all_empty(self, mgr: TaskManager) -> None:
        """无任务时 list_all 返回空列表。"""
        assert mgr.list_all() == []

    @pytest.mark.asyncio
    async def test_list_available_filters_correctly(self, mgr: TaskManager) -> None:
        """list_available 仅返回 pending + 无 owner + 无 blockedBy 的任务。"""
        t1 = await mgr.create(subject="Available", description="d")
        t2 = await mgr.create(subject="Owned", description="d", owner="someone")
        t3 = await mgr.create(subject="Blocked", description="d")

        # t3 被 t1 阻塞
        await mgr.add_dependency(t3.id, [t1.id])

        available = mgr.list_available()
        ids = [t.id for t in available]
        assert t1.id in ids  # pending, no owner, no blockedBy
        assert t2.id not in ids  # has owner
        assert t3.id not in ids  # has blockedBy

    @pytest.mark.asyncio
    async def test_list_available_excludes_in_progress(self, mgr: TaskManager) -> None:
        """进行中的任务不在 available 列表中。"""
        task = await mgr.create(subject="Busy", description="d")
        await mgr.update(task.id, status="in_progress")
        assert len(mgr.list_available()) == 0


# ── 任务更新 ──────────────────────────────────────────────────


class TestTaskUpdate:
    """update() 测试。"""

    @pytest.mark.asyncio
    async def test_update_status(self, mgr: TaskManager) -> None:
        """更新状态字段。"""
        task = await mgr.create(subject="T", description="d")
        updated = await mgr.update(task.id, status="in_progress")
        assert updated.status == "in_progress"

    @pytest.mark.asyncio
    async def test_update_subject(self, mgr: TaskManager) -> None:
        """更新标题字段。"""
        task = await mgr.create(subject="Old", description="d")
        updated = await mgr.update(task.id, subject="New")
        assert updated.subject == "New"

    @pytest.mark.asyncio
    async def test_update_owner_to_value(self, mgr: TaskManager) -> None:
        """设置 owner 为具体值。"""
        task = await mgr.create(subject="T", description="d")
        updated = await mgr.update(task.id, owner="worker-1")
        assert updated.owner == "worker-1"

    @pytest.mark.asyncio
    async def test_update_owner_to_none(self, mgr: TaskManager) -> None:
        """owner 设为 None（释放任务）。"""
        task = await mgr.create(subject="T", description="d", owner="worker-1")
        updated = await mgr.update(task.id, owner=None)
        assert updated.owner is None

    @pytest.mark.asyncio
    async def test_update_owner_sentinel_no_change(self, mgr: TaskManager) -> None:
        """不传 owner 时使用哨兵值 ...，不修改原值。"""
        task = await mgr.create(subject="T", description="d", owner="worker-1")
        # update 不传 owner（默认 ...），owner 不变
        updated = await mgr.update(task.id, subject="New Title")
        assert updated.owner == "worker-1"

    @pytest.mark.asyncio
    async def test_update_metadata_merge(self, mgr: TaskManager) -> None:
        """metadata 应合并而非替换。"""
        task = await mgr.create(
            subject="T", description="d", metadata={"key1": "val1"}
        )
        updated = await mgr.update(task.id, metadata={"key2": "val2"})
        assert updated.metadata == {"key1": "val1", "key2": "val2"}

    @pytest.mark.asyncio
    async def test_update_persisted(self, mgr: TaskManager) -> None:
        """更新应持久化。"""
        task = await mgr.create(subject="T", description="d")
        await mgr.update(task.id, status="completed")
        reloaded = mgr.read(task.id)
        assert reloaded is not None
        assert reloaded.status == "completed"

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self, mgr: TaskManager) -> None:
        """更新不存在的任务应抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            await mgr.update("999", status="completed")


# ── 任务删除 ──────────────────────────────────────────────────


class TestTaskDelete:
    """delete() 测试。"""

    @pytest.mark.asyncio
    async def test_delete_marks_as_deleted(self, mgr: TaskManager) -> None:
        """删除后状态变为 deleted。"""
        task = await mgr.create(subject="T", description="d")
        await mgr.delete(task.id)
        reloaded = mgr.read(task.id)
        assert reloaded is not None
        assert reloaded.status == "deleted"

    @pytest.mark.asyncio
    async def test_delete_cleans_dependency_refs(self, mgr: TaskManager) -> None:
        """删除任务应从其他任务的 blocks/blockedBy 中移除引用。"""
        t1 = await mgr.create(subject="Upstream", description="d")
        t2 = await mgr.create(subject="Downstream", description="d")
        await mgr.add_dependency(t2.id, [t1.id])

        # 验证依赖已建立
        assert t1.id in mgr.read(t2.id).blocked_by
        assert t2.id in mgr.read(t1.id).blocks

        # 删除 t1，t2 的 blockedBy 应被清理
        await mgr.delete(t1.id)
        t2_after = mgr.read(t2.id)
        assert t1.id not in t2_after.blocked_by

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, mgr: TaskManager) -> None:
        """删除不存在的任务不报错。"""
        await mgr.delete("999")  # 不应抛出异常


# ── 依赖管理 ──────────────────────────────────────────────────


class TestDependencies:
    """add_dependency() / remove_dependency() 测试。"""

    @pytest.mark.asyncio
    async def test_add_creates_bidirectional_links(self, mgr: TaskManager) -> None:
        """添加依赖创建双向链接。"""
        t1 = await mgr.create(subject="A", description="d")
        t2 = await mgr.create(subject="B", description="d")

        await mgr.add_dependency(t2.id, [t1.id])

        t1_fresh = mgr.read(t1.id)
        t2_fresh = mgr.read(t2.id)
        assert t2.id in t1_fresh.blocks
        assert t1.id in t2_fresh.blocked_by

    @pytest.mark.asyncio
    async def test_add_multiple_dependencies(self, mgr: TaskManager) -> None:
        """支持一次添加多个依赖。"""
        t1 = await mgr.create(subject="A", description="d")
        t2 = await mgr.create(subject="B", description="d")
        t3 = await mgr.create(subject="C", description="d")

        await mgr.add_dependency(t3.id, [t1.id, t2.id])

        t3_fresh = mgr.read(t3.id)
        assert set(t3_fresh.blocked_by) == {t1.id, t2.id}

    @pytest.mark.asyncio
    async def test_add_duplicate_dep_is_noop(self, mgr: TaskManager) -> None:
        """重复添加相同依赖不产生副作用。"""
        t1 = await mgr.create(subject="A", description="d")
        t2 = await mgr.create(subject="B", description="d")

        await mgr.add_dependency(t2.id, [t1.id])
        await mgr.add_dependency(t2.id, [t1.id])  # 重复

        t2_fresh = mgr.read(t2.id)
        assert t2_fresh.blocked_by.count(t1.id) == 1

    @pytest.mark.asyncio
    async def test_add_nonexistent_task_raises(self, mgr: TaskManager) -> None:
        """对不存在的任务添加依赖应抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            await mgr.add_dependency("999", ["1"])

    @pytest.mark.asyncio
    async def test_remove_dependency(self, mgr: TaskManager) -> None:
        """移除依赖清理双向链接。"""
        t1 = await mgr.create(subject="A", description="d")
        t2 = await mgr.create(subject="B", description="d")

        await mgr.add_dependency(t2.id, [t1.id])
        await mgr.remove_dependency(t2.id, [t1.id])

        t1_fresh = mgr.read(t1.id)
        t2_fresh = mgr.read(t2.id)
        assert t2.id not in t1_fresh.blocks
        assert t1.id not in t2_fresh.blocked_by

    @pytest.mark.asyncio
    async def test_remove_nonexistent_dep_is_noop(self, mgr: TaskManager) -> None:
        """移除不存在的依赖不报错。"""
        t1 = await mgr.create(subject="A", description="d")
        await mgr.remove_dependency(t1.id, ["999"])  # 不应抛出


# ── BFS 循环检测 ──────────────────────────────────────────────


class TestCycleDetection:
    """_would_create_cycle() / BFS 循环检测测试。"""

    @pytest.mark.asyncio
    async def test_direct_cycle_detected(self, mgr: TaskManager) -> None:
        """直接循环（A→B→A）应被检测。"""
        t1 = await mgr.create(subject="A", description="d")
        t2 = await mgr.create(subject="B", description="d")

        await mgr.add_dependency(t2.id, [t1.id])  # t2 depends on t1

        with pytest.raises(CyclicDependencyError) as exc_info:
            await mgr.add_dependency(t1.id, [t2.id])  # t1 depends on t2 → 循环

        assert exc_info.value.task_id == t1.id
        assert t2.id in exc_info.value.blocked_by

    @pytest.mark.asyncio
    async def test_transitive_cycle_detected(self, mgr: TaskManager) -> None:
        """传递循环（A→B→C→A）应被检测。"""
        t1 = await mgr.create(subject="A", description="d")
        t2 = await mgr.create(subject="B", description="d")
        t3 = await mgr.create(subject="C", description="d")

        await mgr.add_dependency(t2.id, [t1.id])  # B depends on A
        await mgr.add_dependency(t3.id, [t2.id])  # C depends on B

        with pytest.raises(CyclicDependencyError):
            await mgr.add_dependency(t1.id, [t3.id])  # A depends on C → 循环

    @pytest.mark.asyncio
    async def test_self_cycle_detected(self, mgr: TaskManager) -> None:
        """自引用循环（A→A）应被检测。"""
        t1 = await mgr.create(subject="A", description="d")
        with pytest.raises(CyclicDependencyError):
            await mgr.add_dependency(t1.id, [t1.id])

    @pytest.mark.asyncio
    async def test_valid_dag_no_cycle(self, mgr: TaskManager) -> None:
        """合法 DAG（菱形依赖）不应触发循环检测。

        A → B
        A → C
        B → D
        C → D
        """
        a = await mgr.create(subject="A", description="d")
        b = await mgr.create(subject="B", description="d")
        c = await mgr.create(subject="C", description="d")
        d = await mgr.create(subject="D", description="d")

        await mgr.add_dependency(b.id, [a.id])
        await mgr.add_dependency(c.id, [a.id])
        await mgr.add_dependency(d.id, [b.id])
        await mgr.add_dependency(d.id, [c.id])  # 不应抛出

        d_fresh = mgr.read(d.id)
        assert set(d_fresh.blocked_by) == {b.id, c.id}


# ── 自增 ID ──────────────────────────────────────────────────


class TestAutoIncrementID:
    """_next_id() 自增逻辑。"""

    @pytest.mark.asyncio
    async def test_id_after_gap(self, mgr: TaskManager) -> None:
        """删除中间任务后，ID 基于最大现有值继续。"""
        t1 = await mgr.create(subject="A", description="d")
        t2 = await mgr.create(subject="B", description="d")
        await mgr.delete(t1.id)  # 删除 ID=1，但文件仍存在（状态为 deleted）
        t3 = await mgr.create(subject="C", description="d")
        assert t3.id == "3"  # 基于 max(1,2) + 1

    @pytest.mark.asyncio
    async def test_first_task_id(self, mgr: TaskManager) -> None:
        """空目录的第一个 ID 为 1。"""
        task = await mgr.create(subject="First", description="d")
        assert task.id == "1"


# ── 属性访问 ──────────────────────────────────────────────────


class TestTaskManagerProperties:
    """属性测试。"""

    def test_tasks_dir_property(
        self, mgr: TaskManager, isolated_home: Path
    ) -> None:
        """tasks_dir 指向正确路径。"""
        expected = isolated_home / "tasks" / "test-team"
        assert mgr.tasks_dir == expected
