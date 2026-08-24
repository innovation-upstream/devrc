#!/usr/bin/env python3
"""Apply the devrc skill-listing TIER LEDGER to a host's ~/.claude/settings.json.

WHAT THIS IS FOR
----------------
Every skill's `name` + `description` loads on EVERY session, under a budget of
1% of the context window measured IN CHARACTERS. Over budget, Claude Code
silently strips descriptions starting with the skills invoked least -- taking
with them the very trigger keywords that make a skill auto-fire.

`skillOverrides` in settings.json makes that cost per-skill opt-in. A skill set
to `name-only` costs ~12 chars instead of ~350, stays `/name`-invocable and stays
callable by the Skill tool; what it loses is the ROUTING PROSE that makes it fire
from a described symptom. `claude/skill-tiers.json` is the ledger of that call,
one line per skill, with a rationale on every tier-B entry.

🔴 IT DEFAULTS TO DRY-RUN. Nothing is written without `--apply`. Applying the
ledger is an operator act on a per-host, unmanaged file -- not something that
happens as a side effect of a merge or a switch.

WHY A SCRIPT AND NOT A NIX MODULE
---------------------------------
The same reason `scripts/sync-claude-permissions.py` is one, and it is stated in
`nix/home.nix` twice: `~/.claude/settings.json` is per-host and deliberately
UNMANAGED, and Claude Code itself rewrites it every time a permission prompt is
answered. Anything nix owned here would be clobbered on the next "allow", or
would clobber the operator's own answers.

🔴 THE MERGE ORDER MAKES THIS FILE THE WEAKEST SCOPE
----------------------------------------------------
Settings merge user -> project -> local -> flag -> policy, later wins, per-key
deep merge. `~/.claude/settings.json` is the LOWEST-precedence ordinary scope, so
a `skillOverrides` entry in any project's `.claude/settings.json` or
`.claude/settings.local.json` silently beats this ledger for that skill, in that
repo, with no error anywhere. This script cannot fix that -- those files are not
devrc's -- so it goes looking for them and says so LOUDLY. A warning here is the
only thing standing between "the ledger is applied" and "the ledger is applied
except where it is not".

🔴 A PLUGIN SKILL CAN NEVER BE TIERED
-------------------------------------
The override resolver hard-returns `"on"` for `source === "plugin"` before it
looks at `skillOverrides` at all, so an entry naming a plugin skill is DEAD
CONFIG that reads as coverage. devrc's own skills load as `source:
"userSettings"`, and this ledger is pinned two-way against devrc's own tree
(`reconcile` below, and `scripts/tests/test_skill_tiers.py`), so a plugin name
cannot get an entry without failing as a phantom. The plugin scan below is belt
and braces on top of that structural guarantee, not the guarantee itself.

WHAT IT WRITES
--------------
Strictly additive within one key. It merges the ledger's tier-B entries into
`skillOverrides`, never clobbering the key, never removing or rewriting an entry
it did not put there, and never touching any other key. Tier A is written as
ABSENCE, not as `"on"` -- `on` is already the default, so emitting it would add a
line per skill that says nothing and turn every tier-A flip into a write.

An existing entry whose value DISAGREES with the ledger is reported and left
alone unless `--force-value` is passed: a hand-set `off` is somebody's decision,
and quietly promoting it to `name-only` would re-enable a skill they hid.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

import skill_tiers  # noqa: E402

DEFAULT_SETTINGS = Path.home() / ".claude" / "settings.json"
DEFAULT_PROJECT_ROOTS = (Path.home() / "workspace",)
CLAUDE_JSON = Path.home() / ".claude.json"
PLUGINS_DIR = Path.home() / ".claude" / "plugins"


# --------------------------------------------------------------------------- #
# The scopes that can beat this one
# --------------------------------------------------------------------------- #

def _project_dirs(roots, claude_json: Path | None) -> list[Path]:
    """Every directory that could hold a project-scope settings file.

    Two independent sources, unioned rather than chosen between, because each
    misses cases the other sees: the roots glob finds a checkout Claude Code has
    never opened, and `~/.claude.json`'s `projects` keys find one that lives
    outside every root.

    🔴 `claude_json` is a PARAMETER, not the module constant read directly. The
    live file names the operator's real checkouts, so a hardcoded read makes this
    function unable to be driven against a fixture -- and the control that proves
    the scan can see an overriding project would then be measuring the operator's
    machine instead of its own fixture. Pass `None` to consult only `roots`.
    """
    found: set[Path] = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_dir():
                    found.add(child)
            except OSError:
                # 🔴 `is_dir()` STATS, and a stat can be REFUSED. Measured on the
                # first live run with `--project-root /tmp`: systemd's
                # `systemd-private-*` directories are mode 700 and owned by root,
                # so the scan died with a traceback partway through -- after
                # printing the ledger summary, which reads as a completed run.
                # A directory this process cannot see is a gap in coverage, not a
                # reason to abort; the FILE COUNT the caller prints is what makes
                # the gap visible.
                continue
    if claude_json is not None:
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
            for key in (data.get("projects") or {}):
                found.add(Path(key))
        except (OSError, ValueError, AttributeError):
            # Absent or unreadable is a FACT about coverage, reported by the
            # caller -- never folded into "no overriding projects found".
            pass
    return sorted(found)


def overriding_scopes(names, roots,
                      claude_json: Path | None = None
                      ) -> tuple[list[tuple[Path, str, str]], int]:
    """-> ([(file, skill, value)], number of settings files actually READ).

    🔴 The count is returned with the findings and printed beside them. An empty
    finding list from a scan that opened zero files is indistinguishable from a
    clean sweep, and is exactly how a check wired to nothing reads as a pass.
    """
    hits: list[tuple[Path, str, str]] = []
    read = 0
    wanted = set(names)
    for proj in _project_dirs(roots, claude_json):
        for leaf in ("settings.json", "settings.local.json"):
            path = proj / ".claude" / leaf
            try:
                if not path.is_file():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # Same reasoning as `_project_dirs`: an unreadable or unparseable
                # project settings file is a gap in coverage, visible in the file
                # count, and never a reason to abort the whole run.
                continue
            read += 1
            over = data.get("skillOverrides")
            if not isinstance(over, dict):
                continue
            for name, value in sorted(over.items()):
                if name in wanted:
                    hits.append((path, name, str(value)))
    return hits, read


def plugin_skill_names() -> set[str]:
    """Skill names provided by installed plugins, best effort.

    Absence is not evidence of none -- see the caller, which says which.
    """
    names: set[str] = set()
    if not PLUGINS_DIR.is_dir():
        return names
    for skill_md in PLUGINS_DIR.glob("**/skills/*/SKILL.md"):
        names.add(skill_md.parent.name)
    return names


# --------------------------------------------------------------------------- #

def merge(existing: dict, wanted: dict, force_value: bool):
    """-> (merged, added, conflicting, foreign).

    added:       entries this run would write
    conflicting: present with a DIFFERENT value (left alone unless --force-value)
    foreign:     present in the file for a skill this ledger does not tier
    """
    merged = dict(existing)
    added, conflicting = [], []
    for name, value in sorted(wanted.items()):
        current = existing.get(name)
        if current is None:
            merged[name] = value
            added.append((name, value))
        elif str(current) != value:
            conflicting.append((name, str(current), value))
            if force_value:
                merged[name] = value
    foreign = sorted(n for n in existing if n not in wanted)
    return merged, added, conflicting, foreign


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS,
                    help="settings.json to update (default: ~/.claude/settings.json)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. WITHOUT THIS NOTHING IS WRITTEN.")
    ap.add_argument("--force-value", action="store_true",
                    help="overwrite an existing entry whose value disagrees with "
                         "the ledger (default: report it and leave it alone)")
    ap.add_argument("--project-root", type=Path, action="append", default=None,
                    help="directory whose immediate children are scanned for "
                         "project-scope settings that would beat this file "
                         "(repeatable; default: ~/workspace)")
    args = ap.parse_args(argv)
    roots = args.project_root or list(DEFAULT_PROJECT_ROOTS)

    # --- 1. the ledger must agree with the shipped tree, both ways ------------
    try:
        ledger = skill_tiers.load_ledger()
    except (OSError, ValueError) as exc:
        print(f"REFUSING TO WRITE — {skill_tiers.LEDGER_PATH} is unusable: {exc}",
              file=sys.stderr)
        return 2
    try:
        skills = skill_tiers.shipped_skills()
    except RuntimeError as exc:
        print(f"REFUSING TO WRITE — cannot enumerate the shipped skills: {exc}",
              file=sys.stderr)
        return 2

    broken = skill_tiers.unparseable()
    if broken:
        print("REFUSING TO WRITE — these SKILL.md files have no readable "
              f"frontmatter, so they are not in the listing at all: {broken}",
              file=sys.stderr)
        return 2

    untiered, phantom = skill_tiers.reconcile(ledger, skills)
    if untiered or phantom:
        print("REFUSING TO WRITE — the ledger and the shipped skills disagree.",
              file=sys.stderr)
        for n in untiered:
            print(f"  shipped but NOT tiered: {n}  ({skills[n][0]})", file=sys.stderr)
        for n in phantom:
            print(f"  tiered but NOT shipped: {n}  (renamed? retired? a plugin "
                  "skill, which can never be tiered?)", file=sys.stderr)
        print("  Add or remove the entry in claude/skill-tiers.json. Flipping a "
              "skill is one line.", file=sys.stderr)
        return 2

    wanted = skill_tiers.expected_overrides(ledger)
    a_names = skill_tiers.tier_a_names(ledger)
    print(f"ledger: {len(a_names)} tier A (full description), "
          f"{len(wanted)} tier B (name-only), {len(skills)} skills total")
    print(f"  tier A costs {skill_tiers.tier_a_chars(ledger, skills):,} chars; "
          f"devrc's whole listing under this ledger, "
          f"{skill_tiers.devrc_listing_chars(ledger, skills):,} chars "
          "(a FLOOR on the listing — the non-devrc entries are real and this "
          "repo cannot see them)")

    # --- 2. a plugin name in the ledger is dead config ------------------------
    plugin_names = plugin_skill_names()
    if plugin_names:
        collide = sorted(set(ledger) & plugin_names)
        print(f"  plugin skills: {len(plugin_names)} found under {PLUGINS_DIR}, "
              f"{len(collide)} colliding with a ledger entry")
        if collide:
            print(f"  ⚠ these ledger entries share a name with a PLUGIN skill: "
                  f"{collide}. The resolver hard-returns \"on\" for a plugin "
                  "skill, so such an override never takes effect.", file=sys.stderr)
    else:
        print(f"  plugin skills: NOT SCANNED — {PLUGINS_DIR} is absent or empty "
              "(so this run cannot say a ledger entry is not a dead plugin "
              "override; the two-way pin above is what actually prevents it)")

    # --- 3. the scopes that silently beat this one ----------------------------
    hits, files_read = overriding_scopes(wanted, roots, CLAUDE_JSON)
    known = "yes" if CLAUDE_JSON.is_file() else "NOT READABLE"
    print(f"  project/local scopes examined: {files_read} settings file(s) under "
          f"{[str(r) for r in roots]}; ~/.claude.json's project list: {known}")
    if hits:
        print("  🔴 THESE SCOPES BEAT THIS FILE. Settings merge user -> project "
              "-> local, later wins, per-key: for each skill below, the ledger "
              "is OVERRULED inside that project.", file=sys.stderr)
        for path, name, value in hits:
            print(f"     {path}: {name} = {value}", file=sys.stderr)
        print("     Fix them THERE, or accept that the ledger does not hold in "
              "those repos.", file=sys.stderr)

    # --- 4. the write ---------------------------------------------------------
    path = args.settings
    if not path.exists():
        print(f"{path} does not exist — nothing to merge into.", file=sys.stderr)
        print("  Claude Code writes this file on first run; start it once, then "
              "re-run this script.", file=sys.stderr)
        return 3
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"{path} is not readable JSON ({exc}) — refusing to touch it.",
              file=sys.stderr)
        return 4
    if not isinstance(data, dict):
        print(f"{path} is not a JSON object — refusing to touch it.", file=sys.stderr)
        return 4
    existing = data.get("skillOverrides")
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        print(f"{path}: `skillOverrides` is not an object — refusing to touch it.",
              file=sys.stderr)
        return 4

    merged, added, conflicting, foreign = merge(existing, wanted, args.force_value)
    print(f"{path}: {len(existing)} existing override(s), {len(added)} to add.")
    for name, value in added:
        why = ledger[name].get("why", "")
        print(f"  + {name}: {value}" + (f"   — {why}" if why else ""))
    for name, current, value in conflicting:
        verb = "OVERWRITING" if args.force_value else "LEAVING ALONE"
        print(f"  ! {name}: file says {current!r}, ledger says {value!r} "
              f"— {verb}" + ("" if args.force_value else " (--force-value to change)"))
    for name in foreign:
        print(f"  = {name}: {existing[name]!r} — not tiered by the ledger, untouched")

    if merged == existing:
        print("  nothing to do — already in sync.")
        return 0
    if not args.apply:
        print("  DRY RUN — nothing written. Re-run with --apply to write.")
        return 0

    data["skillOverrides"] = merged
    backup = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%dT%H%M%S"))
    shutil.copy2(path, backup)
    body = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    print(f"  wrote {path} ({len(merged)} override(s)). Backup: {backup}")
    print("  🔴 Claude Code reads settings.json at STARTUP — restart any running "
          "session before expecting the listing to change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
