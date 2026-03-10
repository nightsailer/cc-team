"""Tests for RelayContext and RelayMode data models."""

import json

from cc_team._relay_context import RelayContext, RelayMode


class TestRelayMode:
    def test_enum_values(self):
        assert RelayMode.STANDALONE == "standalone"
        assert RelayMode.TEAM_LEAD == "team-lead"
        assert RelayMode.TEAMMATE == "teammate"


class TestRelayContext:
    def test_create_standalone(self):
        ctx = RelayContext(
            session_id="abc-123",
            mode=RelayMode.STANDALONE,
            team_name=None,
            member_name=None,
            backend_type="tmux",
            backend_id="%42",
            project_dir="/tmp/proj",
            created_at=1000,
            created_by="cct-cli",
        )
        assert ctx.mode == RelayMode.STANDALONE
        assert ctx.team_name is None

    def test_create_team_lead(self):
        ctx = RelayContext(
            session_id="abc-123",
            mode=RelayMode.TEAM_LEAD,
            team_name="my-team",
            member_name=None,
            backend_type="tmux",
            backend_id="%42",
            project_dir="/tmp/proj",
            created_at=1000,
            created_by="cct-cli",
        )
        assert ctx.team_name == "my-team"
        assert ctx.member_name is None

    def test_create_teammate(self):
        ctx = RelayContext(
            session_id="abc-123",
            mode=RelayMode.TEAMMATE,
            team_name="my-team",
            member_name="researcher",
            backend_type="tmux",
            backend_id="%10",
            project_dir="/tmp/proj",
            created_at=1000,
            created_by="session-start-hook",
        )
        assert ctx.member_name == "researcher"

    def test_serialize_roundtrip(self, tmp_path):
        ctx = RelayContext(
            session_id="abc-123",
            mode=RelayMode.TEAM_LEAD,
            team_name="my-team",
            member_name=None,
            backend_type="tmux",
            backend_id="%42",
            project_dir="/tmp/proj",
            created_at=1000,
            created_by="cct-cli",
        )
        path = tmp_path / "context.json"
        ctx.save(path)
        loaded = RelayContext.load(path)
        assert loaded == ctx

    def test_serialize_camel_case_keys(self, tmp_path):
        ctx = RelayContext(
            session_id="abc-123",
            mode=RelayMode.STANDALONE,
            team_name=None,
            member_name=None,
            backend_type="tmux",
            backend_id=None,
            project_dir="/tmp/proj",
            created_at=1000,
            created_by="cct-cli",
        )
        path = tmp_path / "context.json"
        ctx.save(path)
        data = json.loads(path.read_text())
        assert "sessionId" in data
        assert "backendType" in data
        assert "createdAt" in data

    def test_load_nonexistent_returns_none(self, tmp_path):
        result = RelayContext.load(tmp_path / "missing.json")
        assert result is None

    def test_context_dir_path(self):
        ctx = RelayContext(
            session_id="abc-123",
            mode=RelayMode.STANDALONE,
            team_name=None,
            member_name=None,
            backend_type="tmux",
            backend_id=None,
            project_dir="/tmp/proj",
            created_at=1000,
            created_by="cct-cli",
        )
        expected = "/tmp/proj/.claude/cct/relay/abc-123"
        assert ctx.relay_dir == expected

    def test_handoff_path(self):
        ctx = RelayContext(
            session_id="abc-123",
            mode=RelayMode.STANDALONE,
            team_name=None,
            member_name=None,
            backend_type="tmux",
            backend_id=None,
            project_dir="/tmp/proj",
            created_at=1000,
            created_by="cct-cli",
        )
        assert ctx.handoff_path.endswith("abc-123/handoff.md")

    def test_usage_path(self):
        ctx = RelayContext(
            session_id="abc-123",
            mode=RelayMode.STANDALONE,
            team_name=None,
            member_name=None,
            backend_type="tmux",
            backend_id=None,
            project_dir="/tmp/proj",
            created_at=1000,
            created_by="cct-cli",
        )
        assert ctx.usage_path.endswith("abc-123/usage.json")
