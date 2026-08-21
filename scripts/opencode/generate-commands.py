#!/usr/bin/env python3
"""Generate opencode command files from Claude Code skill definitions.

Reads SKILL.md files from a source directory, extracts frontmatter (name,
description) and body, and writes corresponding .md command files to an
output directory. Each command gets hints=["$ARGUMENTS"] so the TUI
autocomplete surfaces skill-sourced commands.

Usage:
    python3 generate-commands.py <source-skills-dir> <output-commands-dir>
"""

import os
import sys
import re
from pathlib import Path

# Exit code for "you pointed the output at a path home-manager owns". Its own
# code, distinct from the usage/missing-source `1`, so a test can assert THIS
# guard fired rather than "the script failed somehow".
EXIT_MANAGED_OUTPUT = 3

# The store prefix that makes a symlink a nix deployment. Structural, not
# spelled: nothing here hardcodes `opencode` or `commands`, so a different
# managed output directory is covered on the day someone points at one. A
# variable only so the test suite can build a fixture tree it fully controls —
# a real /nix/store cannot be written to by a test.
STORE_PREFIX = os.environ.get("GENERATE_COMMANDS_STORE_PREFIX", "/nix/store/")


def home_manager_manifest() -> Path | None:
    """The current home-manager generation's `home-files` tree, if there is one.

    This is the AUTHORITATIVE answer to "does home-manager intend to own this
    path": every leaf under it is a path the active generation links into $HOME.
    Absent in the nix build sandbox (no profile, HOME=/homeless-shelter), which
    is exactly why the guard below is inert there and live on a real host.
    """
    home = os.environ.get("HOME")
    if not home:
        return None
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    p = Path(state) / "nix" / "profiles" / "home-manager" / "home-files"
    return p if p.is_dir() else None


def managed_output_reason(out_dir: Path) -> str | None:
    """Why writing into `out_dir` would fight home-manager — or None if it would not.

    🔴 THIS IS THE WHOLE POINT OF THE FILE'S GUARD. On 2026-08-19 an agent ran
    this generator with the output directory pointed at the LIVE
    ~/.config/opencode/commands (its three previous runs used /tmp/test-commands).
    It replaced 34 home-manager symlinks with plain regular files, and
    home-manager could not take them back: `force = true` only suppresses the
    COLLISION CHECK, and the link step SKIPS a target that is a regular file
    whose content is identical to the source ("Skipping '$targetPath' as it is
    identical"). So every file that stayed byte-identical was owned by the wrong
    writer permanently, across arbitrarily many switches — 18 of them were still
    regular files two days and two generations later.

    TWO INDEPENDENT SIGNALS, because either can be unavailable:
      * the MANIFEST — the path is declared by the active generation. Survives
        the case where the directory has already been fully overwritten and no
        symlink is left on disk to notice.
      * the DISK — an entry in the directory is already a store symlink.
        Survives the case where the profile is unreadable or does not exist.
    """
    resolved = out_dir.resolve() if out_dir.exists() else out_dir.absolute()

    manifest = home_manager_manifest()
    if manifest is not None:
        home = Path(os.environ["HOME"]).resolve()
        try:
            rel = resolved.relative_to(home)
        except ValueError:
            rel = None
        if rel is not None and os.path.lexists(manifest / rel):
            return (
                f"the active home-manager generation declares $HOME/{rel} "
                f"(it is a leaf under {manifest})"
            )

    if out_dir.is_dir():
        for entry in sorted(out_dir.iterdir()):
            if entry.is_symlink() and os.readlink(entry).startswith(STORE_PREFIX):
                return (
                    f"{entry} is already a home-manager symlink into "
                    f"{STORE_PREFIX}"
                )

    return None


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

    # 🔴 REFUSE A HOME-MANAGER-MANAGED OUTPUT DIRECTORY, BEFORE THE mkdir.
    # Checked first so the refusal cannot itself create the directory it is
    # complaining about. In the nix build this is inert by construction: `$out`
    # is a fresh empty store path and there is no home-manager profile in the
    # sandbox, so neither signal can fire.
    reason = managed_output_reason(output_dir)
    if reason is not None:
        print(
            f"Error: refusing to write into {output_dir} — {reason}.\n"
            "  home-manager owns that path. Writing regular files over its symlinks\n"
            "  is NOT undone by the next switch: `force = true` only suppresses the\n"
            "  collision check, and the link step skips a target whose content is\n"
            "  already identical. The files stay owned by the wrong writer forever.\n"
            "  Generate into a temp directory instead, e.g. /tmp/test-commands, and\n"
            "  let the `opencodeCommands` nix derivation deploy the real thing.",
            file=sys.stderr,
        )
        sys.exit(EXIT_MANAGED_OUTPUT)

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
