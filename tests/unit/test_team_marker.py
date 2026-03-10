"""Tests for team marker file management."""

from cc_team._team_marker import (
    marker_path,
    read_team_marker,
    remove_team_marker,
    write_team_marker,
)


class TestTeamMarker:
    def test_write_and_read(self, tmp_path):
        write_team_marker(tmp_path, "my-team")
        marker = read_team_marker(tmp_path)
        assert marker is not None
        assert marker["teamName"] == "my-team"
        assert "createdAt" in marker

    def test_read_nonexistent(self, tmp_path):
        assert read_team_marker(tmp_path) is None

    def test_remove(self, tmp_path):
        write_team_marker(tmp_path, "my-team")
        remove_team_marker(tmp_path)
        assert read_team_marker(tmp_path) is None

    def test_remove_nonexistent_is_noop(self, tmp_path):
        remove_team_marker(tmp_path)  # should not raise

    def test_marker_path(self, tmp_path):
        p = marker_path(tmp_path)
        assert str(p).endswith(".claude/cct/team-marker.json")
