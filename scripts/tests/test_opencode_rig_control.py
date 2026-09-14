"""Tests for the rig-control opencode skill and command.

WHAT IS UNDER TEST

  1. Skill SKILL.md validity — frontmatter (name, description), body sections,
     name-directory match, description length bounds.
  2. Command .md validity — frontmatter, template has $ARGUMENTS placeholder,
     shell output injection via backtick syntax.
  3. Referenced scripts exist in the repo.
  4. Color schedule file is parseable and non-empty.
  5. Skill + command are consistent — command references the same scripts the
     skill documents.

    run:  python -m pytest scripts/tests/test_opencode_rig_control.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

SKILL_DIR = Path.home() / ".config" / "opencode" / "skills" / "rig-control"
SKILL_MD = SKILL_DIR / "SKILL.md"

COMMAND_DIR = Path.home() / ".config" / "opencode" / "commands"
COMMAND_MD = COMMAND_DIR / "rig-control.md"

# Scripts referenced in the skill
SCRIPTS = [
    "scripts/rig-control.sh",
    "scripts/rig-control-fade",
    "scripts/rig-control-colors.conf",
    "scripts/rig-control-toggle",
    "scripts/i3blocks-rigcontrol",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML frontmatter from a markdown file."""
    m = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m2 = re.match(r'^(\w[\w-]*):\s*"((?:[^"\\]|\\.)*)"\s*$', line)
        if m2:
            fm[m2.group(1)] = m2.group(2)
            continue
        m2 = re.match(r"^(\w[\w-]*):\s*'([^']*)'\s*$", line)
        if m2:
            fm[m2.group(1)] = m2.group(2)
            continue
        m2 = re.match(r"^(\w[\w-]*):\s*(.+?)\s*$", line)
        if m2:
            fm[m2.group(1)] = m2.group(2)
    return fm


# --------------------------------------------------------------------------- #
# skill tests
# --------------------------------------------------------------------------- #
class TestSkillFrontmatter:
    def test_skill_file_exists(self):
        assert SKILL_MD.exists(), f"Skill file not found: {SKILL_MD}"

    def test_has_frontmatter(self):
        text = SKILL_MD.read_text()
        assert text.startswith("---"), "SKILL.md must start with YAML frontmatter"

    def test_name_matches_directory(self):
        fm = _parse_frontmatter(SKILL_MD.read_text())
        assert fm.get("name") == "rig-control", (
            f"Skill name {fm.get('name')!r} must match directory name 'rig-control'"
        )

    def test_name_format(self):
        fm = _parse_frontmatter(SKILL_MD.read_text())
        name = fm.get("name", "")
        assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name), (
            f"Name {name!r} must be lowercase alphanumeric with single-hyphen separators"
        )

    def test_description_exists(self):
        fm = _parse_frontmatter(SKILL_MD.read_text())
        assert "description" in fm, "SKILL.md must have a description"
        assert len(fm["description"]) > 0, "description must not be empty"

    def test_description_length(self):
        fm = _parse_frontmatter(SKILL_MD.read_text())
        desc = fm.get("description", "")
        assert len(desc) <= 1024, (
            f"description is {len(desc)} chars, max is 1024"
        )


class TestSkillBody:
    def _body(self) -> str:
        text = SKILL_MD.read_text()
        m = re.match(r"^---\n(.+?)\n---\n(.*)", text, re.DOTALL)
        return m.group(2) if m else ""

    def test_has_prereq_guard(self):
        body = self._body()
        assert "which openrgb" in body, "Body must include openrgb prereq guard"

    def test_has_quick_reference(self):
        body = self._body()
        assert "rig-control.sh" in body, "Body must reference rig-control.sh"
        assert "rig-control-fade" in body, "Body must reference rig-control-fade"

    def test_has_state_section(self):
        body = self._body()
        assert "rig-control/state" in body, "Body must reference state file"
        assert "list-timers" in body, "Body must reference timer listing"

    def test_has_toggle_section(self):
        body = self._body()
        assert "rig-control.sh sleep" in body, "Body must document sleep toggle"
        assert "rig-control.sh wake" in body, "Body must document wake toggle"

    def test_has_gradient_section(self):
        body = self._body()
        assert "--print" in body, "Body must document --print flag"

    def test_has_color_schedule_section(self):
        body = self._body()
        assert "rig-control-colors.conf" in body, "Body must reference color config"
        assert "HH:MM RRGGBB" in body, "Body must document color format"

    def test_has_gotchas(self):
        body = self._body()
        assert "BLOCK_BUTTON" in body, "Body must mention i3status-rust click gotcha"
        assert "set -e" in body, "Body must mention (( )) && gotcha"
        assert "device 2" in body, "Body must mention openrgb device 2"

    def test_has_files_table(self):
        body = self._body()
        assert "nix/graphical.nix" in body, "Body must reference nix unit definitions"


class TestSkillScriptsExist:
    @pytest.mark.parametrize("script", SCRIPTS)
    def test_script_exists(self, script):
        path = REPO / script
        assert path.exists(), f"Referenced script not found: {script}"


class TestColorSchedule:
    def test_colors_conf_exists(self):
        path = REPO / "scripts/rig-control-colors.conf"
        assert path.exists(), "rig-control-colors.conf not found"

    def test_colors_conf_parseable(self):
        path = REPO / "scripts/rig-control-colors.conf"
        lines = path.read_text().splitlines()
        parsed = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            assert len(parts) >= 2, f"Bad line: {line!r}"
            time_str, hex_str = parts[0], parts[1]
            assert re.match(r"^\d{1,2}:\d{2}$", time_str), f"Bad time: {time_str!r}"
            assert re.match(r"^[0-9A-Fa-f]{6}$", hex_str), f"Bad hex: {hex_str!r}"
            parsed += 1
        assert parsed >= 3, f"Expected at least 3 waypoints, got {parsed}"


# --------------------------------------------------------------------------- #
# command tests
# --------------------------------------------------------------------------- #
class TestCommandFrontmatter:
    def test_command_file_exists(self):
        assert COMMAND_MD.exists(), f"Command file not found: {COMMAND_MD}"

    def test_has_frontmatter(self):
        text = COMMAND_MD.read_text()
        assert text.startswith("---"), "Command must start with YAML frontmatter"

    def test_has_description(self):
        fm = _parse_frontmatter(COMMAND_MD.read_text())
        assert "description" in fm, "Command must have a description"
        assert len(fm["description"]) > 0, "description must not be empty"

    def test_description_length(self):
        fm = _parse_frontmatter(COMMAND_MD.read_text())
        desc = fm.get("description", "")
        assert len(desc) <= 1024, (
            f"description is {len(desc)} chars, max is 1024"
        )


class TestCommandBody:
    def _body(self) -> str:
        text = COMMAND_MD.read_text()
        m = re.match(r"^---\n(.+?)\n---\n(.*)", text, re.DOTALL)
        return m.group(2) if m else ""

    def test_has_arguments_placeholder(self):
        body = self._body()
        assert "$ARGUMENTS" in body, "Command template must use $ARGUMENTS"

    def test_has_shell_injection(self):
        body = self._body()
        assert "!`" in body, "Command must inject shell output via backtick syntax"

    def test_injects_state(self):
        body = self._body()
        assert "rig-control/state" in body, "Command must inject state file"

    def test_injects_timers(self):
        body = self._body()
        assert "list-timers" in body, "Command must inject timer listing"

    def test_injects_gradient(self):
        body = self._body()
        assert "rig-control-fade" in body, "Command must inject gradient color"

    def test_documents_actions(self):
        body = self._body()
        for action in ["sleep", "wake", "colors", "edit", "restart"]:
            assert action in body, f"Command must document action: {action}"


# --------------------------------------------------------------------------- #
# consistency: skill and command agree
# --------------------------------------------------------------------------- #
class TestConsistency:
    def test_skill_and_command_both_reference_rig_control_sh(self):
        skill_body = SKILL_MD.read_text()
        cmd_body = COMMAND_MD.read_text()
        assert "rig-control.sh" in skill_body
        assert "rig-control.sh" in cmd_body

    def test_skill_and_command_both_reference_rig_control_fade(self):
        skill_body = SKILL_MD.read_text()
        cmd_body = COMMAND_MD.read_text()
        assert "rig-control-fade" in skill_body
        assert "rig-control-fade" in cmd_body

    def test_skill_name_matches_command_name(self):
        skill_fm = _parse_frontmatter(SKILL_MD.read_text())
        cmd_name = COMMAND_MD.stem  # filename without .md
        assert skill_fm.get("name") == cmd_name, (
            f"Skill name {skill_fm.get('name')!r} != command name {cmd_name!r}"
        )
