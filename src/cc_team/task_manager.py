"""任务管理器（任务文件 CRUD + DAG 依赖）。

负责:
- 任务 CRUD（每个任务一个 JSON 文件）
- 自增 ID 管理
- blocks/blockedBy 双向链接维护
- BFS 循环依赖检测
- 状态机约束

存储: ~/.claude/tasks/{team_name}/{id}.json
锁: ~/.claude/tasks/{team_name}/.lock（目录级共享锁）
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from cc_team import paths
from cc_team._serialization import (
    atomic_write_json,
    read_json,
    task_file_from_dict,
    task_file_to_dict,
)
from cc_team.exceptions import CyclicDependencyError
from cc_team.filelock import FileLock
from cc_team.types import TaskFile, TaskStatus


class TaskManager:
    """任务 CRUD + DAG 依赖管理器。

    每个实例绑定到一个团队。
    所有修改操作使用目录级文件锁保护。
    """

    def __init__(self, team_name: str) -> None:
        self._team_name = team_name
        self._tasks_dir = paths.tasks_dir(team_name)
        self._lock = FileLock(paths.tasks_lock_path(team_name))

    @property
    def tasks_dir(self) -> Path:
        return self._tasks_dir

    # ── 创建 ────────────────────────────────────────────────

    async def create(
        self,
        *,
        subject: str,
        description: str,
        active_form: str = "",
        owner: str | None = None,
        metadata: dict | None = None,
    ) -> TaskFile:
        """创建新任务，返回带自增 ID 的 TaskFile。"""
        async with self._lock.acquire():
            self._tasks_dir.mkdir(parents=True, exist_ok=True)
            task_id = self._next_id()
            task = TaskFile(
                id=task_id,
                subject=subject,
                description=description,
                active_form=active_form,
                owner=owner,
                metadata=metadata or {},
            )
            self._write_task(task)
            return task

    # ── 读取 ────────────────────────────────────────────────

    def read(self, task_id: str) -> TaskFile | None:
        """读取单个任务。"""
        return self._read_task(task_id)

    def list_all(self) -> list[TaskFile]:
        """列出所有任务（按 ID 排序）。"""
        if not self._tasks_dir.exists():
            return []
        tasks: list[TaskFile] = []
        for path in sorted(self._tasks_dir.glob("*.json")):
            if path.name == ".lock":
                continue
            data = read_json(path)
            if data is not None:
                tasks.append(task_file_from_dict(data))
        # 按数字 ID 排序
        tasks.sort(key=lambda t: int(t.id) if t.id.isdigit() else 0)
        return tasks

    def list_available(self) -> list[TaskFile]:
        """列出可认领的任务（pending + 无 owner + blockedBy 为空）。"""
        return [
            t for t in self.list_all()
            if t.status == "pending" and not t.owner and not t.blocked_by
        ]

    # ── 更新 ────────────────────────────────────────────────

    async def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        subject: str | None = None,
        description: str | None = None,
        active_form: str | None = None,
        owner: str | None = ...,  # type: ignore[assignment]  # sentinel for "not provided"
        metadata: dict | None = None,
    ) -> TaskFile:
        """更新任务字段。

        owner 使用 ... 作为哨兵值区分"不更新"和"设为 None"。

        Raises:
            FileNotFoundError: 任务不存在
        """
        async with self._lock.acquire():
            task = self._read_task(task_id)
            if task is None:
                raise FileNotFoundError(f"Task not found: {task_id}")

            if status is not None:
                task.status = status
            if subject is not None:
                task.subject = subject
            if description is not None:
                task.description = description
            if active_form is not None:
                task.active_form = active_form
            if owner is not ...:
                task.owner = owner  # type: ignore[assignment]
            if metadata is not None:
                task.metadata.update(metadata)

            self._write_task(task)
            return task

    # ── 删除 ────────────────────────────────────────────────

    async def delete(self, task_id: str) -> None:
        """删除任务（标记为 deleted 状态 + 清理依赖链接）。"""
        async with self._lock.acquire():
            task = self._read_task(task_id)
            if task is None:
                return

            task.status = "deleted"
            self._write_task(task)

            # 从其他任务的 blocks/blockedBy 中移除引用
            self._remove_dependency_refs(task_id)

    # ── 依赖管理 ────────────────────────────────────────────

    async def add_dependency(
        self,
        task_id: str,
        blocked_by_ids: list[str],
    ) -> None:
        """添加依赖关系（双向链接 + BFS 循环检测）。

        Args:
            task_id: 被阻塞的任务 ID
            blocked_by_ids: 阻塞它的上游任务 ID 列表

        Raises:
            CyclicDependencyError: 添加依赖后会形成循环
            FileNotFoundError: 任务不存在
        """
        async with self._lock.acquire():
            task = self._read_task(task_id)
            if task is None:
                raise FileNotFoundError(f"Task not found: {task_id}")

            # 过滤掉已存在的依赖
            new_deps = [d for d in blocked_by_ids if d not in task.blocked_by]
            if not new_deps:
                return

            # BFS 循环检测
            if self._would_create_cycle(task_id, new_deps):
                raise CyclicDependencyError(task_id, new_deps)

            # 双向链接: task.blockedBy += new_deps, upstream.blocks += task_id
            task.blocked_by.extend(new_deps)
            self._write_task(task)

            for dep_id in new_deps:
                dep = self._read_task(dep_id)
                if dep is not None and task_id not in dep.blocks:
                    dep.blocks.append(task_id)
                    self._write_task(dep)

    async def remove_dependency(
        self,
        task_id: str,
        blocked_by_ids: list[str],
    ) -> None:
        """移除依赖关系（双向清理）。"""
        async with self._lock.acquire():
            task = self._read_task(task_id)
            if task is None:
                return

            task.blocked_by = [d for d in task.blocked_by if d not in blocked_by_ids]
            self._write_task(task)

            for dep_id in blocked_by_ids:
                dep = self._read_task(dep_id)
                if dep is not None and task_id in dep.blocks:
                    dep.blocks.remove(task_id)
                    self._write_task(dep)

    # ── BFS 循环检测 ────────────────────────────────────────

    def _would_create_cycle(self, task_id: str, new_blocked_by: list[str]) -> bool:
        """BFS 检测添加依赖后是否形成循环。

        从 new_blocked_by 出发，沿 blockedBy 链 BFS 遍历。
        如果能到达 task_id，说明会形成循环。
        """
        visited: set[str] = set()
        queue = deque(new_blocked_by)
        while queue:
            current = queue.popleft()
            if current == task_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            dep = self._read_task(current)
            if dep is not None:
                queue.extend(dep.blocked_by)
        return False

    # ── 内部辅助 ────────────────────────────────────────────

    def _next_id(self) -> str:
        """生成下一个自增 ID（扫描现有文件）。"""
        max_id = 0
        if self._tasks_dir.exists():
            for path in self._tasks_dir.glob("*.json"):
                stem = path.stem
                if stem.isdigit():
                    max_id = max(max_id, int(stem))
        return str(max_id + 1)

    def _read_task(self, task_id: str) -> TaskFile | None:
        """读取单个任务文件。"""
        path = paths.task_file_path(self._team_name, task_id)
        data = read_json(path)
        if data is None:
            return None
        return task_file_from_dict(data)

    def _write_task(self, task: TaskFile) -> None:
        """写入单个任务文件。"""
        path = paths.task_file_path(self._team_name, task.id)
        atomic_write_json(path, task_file_to_dict(task))

    def _remove_dependency_refs(self, task_id: str) -> None:
        """从所有任务中移除对指定任务的依赖引用。"""
        if not self._tasks_dir.exists():
            return
        for path in self._tasks_dir.glob("*.json"):
            if path.name == ".lock":
                continue
            data = read_json(path)
            if data is None:
                continue
            task = task_file_from_dict(data)
            changed = False
            if task_id in task.blocks:
                task.blocks.remove(task_id)
                changed = True
            if task_id in task.blocked_by:
                task.blocked_by.remove(task_id)
                changed = True
            if changed:
                self._write_task(task)
