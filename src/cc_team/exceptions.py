"""cc-team 异常层级。

所有自定义异常继承自 CCTeamError，方便调用方统一捕获。
"""

from __future__ import annotations


class CCTeamError(Exception):
    """cc-team 所有异常的基类。"""


class NotInitializedError(CCTeamError):
    """Controller 尚未初始化（未调用 init 或已 shutdown）。"""


class AgentNotFoundError(CCTeamError):
    """指定的 Agent 不存在于当前团队中。"""

    def __init__(self, name: str) -> None:
        self.agent_name = name
        super().__init__(f"Agent not found: {name!r}")


class MessageTimeoutError(CCTeamError):
    """消息接收等待超时。"""


class FileLockError(CCTeamError):
    """文件锁获取失败（超过最大重试次数）。"""

    def __init__(self, path: str, attempts: int) -> None:
        self.path = path
        self.attempts = attempts
        super().__init__(f"Failed to acquire lock on {path!r} after {attempts} attempts")


class TmuxError(CCTeamError):
    """tmux 操作失败（命令返回非零退出码或 tmux 不可用）。"""


class SpawnError(CCTeamError):
    """Agent 启动流程失败。"""


class ProtocolError(CCTeamError):
    """协议格式错误（JSON 解析失败、缺少必选字段等）。"""


class CyclicDependencyError(CCTeamError):
    """任务依赖形成循环（违反 DAG 约束）。"""

    def __init__(self, task_id: str, blocked_by: list[str]) -> None:
        self.task_id = task_id
        self.blocked_by = blocked_by
        super().__init__(
            f"Adding dependency {blocked_by} to task {task_id!r} would create a cycle"
        )
