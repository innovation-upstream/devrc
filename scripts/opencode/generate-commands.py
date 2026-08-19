#!/usr/bin/env python3
"""Generate opencode command files from Claude Code skill definitions.

Reads SKILL.md files from a source directory, extracts frontmatter (name,
description) and body, and writes corresponding .md command files to an
output directory. Each command gets hints=["$ARGUMENTS"] so the TUI
autocomplete surfaces skill-sourced commands.

Usage:
    python3 generate-commands.py <source-skills-dir> <output-commands-dir>
"""

import sys
import re
from pathlib import Path


def parse_skill(skill_path: Path) -> dict | None:
    """Parse a SKILL.md file into frontmatter dict + body string.

    Returns None if the file has no valid frontmatter (matches opencode's
    behavior: a SKILL.md with NO frontmatter is DROPPED from the listing).
    """
    text = skill_path.read_text(encoding="utf-8")

    # YAML frontmatter is delimited by --- at line start
    m = re.match(r"^---\n(.+?)\n---\n(.*)", text, re.DOTALL)
    if not m:
        return None

    raw_fm, body = m.group(1), m.group(2)

    # Parse simple key: value pairs (no nested YAML needed for our use case).
    # Handles unquoted, double-quoted (with escapes), and single-quoted values.
    fm = {}
    for line in raw_fm.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # key: "value with \"escapes\""
        m2 = re.match(r'^(\w[\w-]*):\s*"((?:[^"\\]|\\.)*)"\s*$', line)
        if m2:
            fm[m2.group(1)] = m2.group(2)
            continue
        # key: 'single-quoted value'
        m2 = re.match(r"^(\w[\w-]*):\s*'([^']*)'\s*$", line)
        if m2:
            fm[m2.group(1)] = m2.group(2)
            continue
        # key: unquoted value
        m2 = re.match(r'^(\w[\w-]*):\s*(.+?)\s*$', line)
        if m2:
            fm[m2.group(1)] = m2.group(2)

    if "name" not in fm or "description" not in fm:
        return None

    return {"name": fm["name"], "description": fm["description"], "body": body}


def generate_command(skill: dict) -> str:
    """Generate a command .md file from a parsed skill."""
    # Escape any existing frontmatter delimiters in the body to avoid parsing issues
    body = skill["body"].rstrip("\n")

    return f"""---
description: {skill["description"]}
---
{body}
"""


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <source-skills-dir> <output-commands-dir>", file=sys.stderr)
        sys.exit(1)

    source_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])

    if not source_dir.is_dir():
        print(f"Error: source directory {source_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    skipped = 0

    for skill_dir in sorted(source_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        skill = parse_skill(skill_md)
        if skill is None:
            print(f"  SKIP {skill_dir.name}: no valid frontmatter", file=sys.stderr)
            skipped += 1
            continue

        command_md = output_dir / f"{skill['name']}.md"
        command_md.write_text(generate_command(skill), encoding="utf-8")
        generated += 1

    print(f"Generated {generated} commands, skipped {skipped}", file=sys.stderr)


if __name__ == "__main__":
    main()
