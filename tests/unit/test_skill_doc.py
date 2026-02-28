"""Unit tests for _skill_doc module.

Verifies:
- All CLI commands are referenced in the skill document
- All sections are non-empty with valid structure
- Version string follows semver format
"""

from __future__ import annotations

import re

from cc_team._skill_doc import SKILL_DOC, SKILL_DOC_VERSION, SKILL_SECTIONS


class TestSkillDocContent:
    """Verify skill document covers all CLI commands."""

    # Every subcommand that _build_parser() registers.
    EXPECTED_COMMANDS: list[str] = [
        "team create",
        "team info",
        "team destroy",
        "agent spawn",
        "agent list",
        "agent status",
        "agent shutdown",
        "agent kill",
        "task create",
        "task list",
        "task update",
        "task complete",
        "message send",
        "message broadcast",
        "message read",
        "status",
        "skill",
    ]

    def test_all_commands_present(self) -> None:
        """Every registered CLI command must appear in SKILL_DOC."""
        for cmd in self.EXPECTED_COMMANDS:
            assert cmd in SKILL_DOC, f"Command '{cmd}' missing from SKILL_DOC"


class TestSkillSections:
    """Verify section structure and content."""

    def test_sections_non_empty(self) -> None:
        """SKILL_SECTIONS must have at least one entry."""
        assert len(SKILL_SECTIONS) > 0

    def test_each_section_has_title_and_content(self) -> None:
        """Every section must have non-empty title and content."""
        for section in SKILL_SECTIONS:
            assert "title" in section, "Section missing 'title' key"
            assert "content" in section, "Section missing 'content' key"
            assert len(section["title"]) > 0, "Section title is empty"
            assert len(section["content"]) > 0, "Section content is empty"

    def test_section_titles_unique(self) -> None:
        """Section titles must be unique."""
        titles = [s["title"] for s in SKILL_SECTIONS]
        assert len(titles) == len(set(titles)), "Duplicate section titles found"


class TestSkillDocVersion:
    """Verify version string format."""

    def test_version_is_semver(self) -> None:
        """SKILL_DOC_VERSION must match semver pattern."""
        pattern = r"^\d+\.\d+\.\d+$"
        assert re.match(pattern, SKILL_DOC_VERSION), (
            f"Version '{SKILL_DOC_VERSION}' does not match semver"
        )

    def test_version_embedded_in_doc(self) -> None:
        """Version must appear in the skill document header."""
        assert SKILL_DOC_VERSION in SKILL_DOC
