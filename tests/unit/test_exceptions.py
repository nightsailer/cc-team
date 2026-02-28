"""exceptions.py 单元测试 — 异常层级验证。"""

from __future__ import annotations

import pytest

from cc_team.exceptions import (
    AgentNotFoundError,
    CCTeamError,
    CyclicDependencyError,
    FileLockError,
    MessageTimeoutError,
    NotInitializedError,
    ProtocolError,
    SpawnError,
    TmuxError,
)


class TestExceptionHierarchy:
    """所有自定义异常继承自 CCTeamError。"""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            NotInitializedError,
            AgentNotFoundError,
            MessageTimeoutError,
            FileLockError,
            TmuxError,
            SpawnError,
            ProtocolError,
            CyclicDependencyError,
        ],
    )
    def test_inherits_from_base(self, exc_cls: type) -> None:
        assert issubclass(exc_cls, CCTeamError)

    def test_base_can_catch_all(self) -> None:
        """基类 except CCTeamError 可捕获所有子异常。"""
        with pytest.raises(CCTeamError):
            raise TmuxError("tmux not found")


class TestAgentNotFoundError:
    """AgentNotFoundError 携带 agent_name 属性。"""

    def test_stores_agent_name(self) -> None:
        exc = AgentNotFoundError("researcher")
        assert exc.agent_name == "researcher"
        assert "researcher" in str(exc)


class TestFileLockError:
    """FileLockError 携带 path 和 attempts 属性。"""

    def test_stores_lock_info(self) -> None:
        exc = FileLockError("/tmp/test.lock", 5)
        assert exc.path == "/tmp/test.lock"
        assert exc.attempts == 5
        assert "5 attempts" in str(exc)


class TestCyclicDependencyError:
    """CyclicDependencyError 携带依赖信息。"""

    def test_stores_dependency_info(self) -> None:
        exc = CyclicDependencyError("3", ["1", "2"])
        assert exc.task_id == "3"
        assert exc.blocked_by == ["1", "2"]
        assert "cycle" in str(exc).lower()
