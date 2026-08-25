"""The seam guard for `testlib.hermetic_git` — the git-maintenance flake class.

WHY A LEDGER AND NOT A PER-MODULE ASSERTION
-------------------------------------------
`claude/RULES.md`: *"'Verified in isolation' is the new vacuous green — the
defect lives in the SEAM nobody owns … a seam guard must pin a RELATIONSHIP, not
a component — an asserted ledger of every writer/caller, failing when the set
GROWS or SHRINKS."*

That is exactly this class. Each module that fingerprints a git fixture was
individually correct and individually tested; what nobody owned was the fact
that ALL of them must pin maintenance off. Measured: eight such modules, three
pinned — and both of those three were pinned only AFTER the flake fired in CI
(#743, #780). A per-module assertion cannot see module nine.

So the ledger below fails in BOTH directions:

  * a NEW module defining a tree-hashing helper that does not use
    `testlib.hermetic_git` — the case that produced #743 and #780;
  * a module LEAVING the set — which is fine, but must be a decision someone
    made, not a rename nobody noticed.

WHAT THIS DOES NOT COVER, stated so a green here is not over-read: it checks
that each module REFERENCES the shared pins, not that every git call inside it
routes through them. A module could import `hermetic_git` and still build one
stray env by hand. `test_the_pins_actually_reach_git` covers the shared
constant behaviourally; the per-call routing is not machine-checked.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib import hermetic_git  # noqa: E402

GIT = "git"

# The helper names that indicate "this module fingerprints a directory tree".
# Derived from the real corpus rather than invented: these are the four spellings
# actually in use across `scripts/`.
HELPER_NAMES = {"tree_hash", "_tree_hash", "_manifest", "_fingerprint"}

# 🔴 THE LEDGER. Every test module that fingerprints a GIT fixture and therefore
# must pin maintenance off. Measured 2026-08-24. Adding a module here without
# making it use `testlib.hermetic_git` fails the test below; adding one that uses
# it and forgetting this list fails it too.
EXPECTED_MEMBERS = {
    "test_analyze_service_index_backup.py",
    "test_analyze_service_index_commit.py",
    "test_analyze_service_index_restore_verify.py",
    "test_handoff_doc.py",
    "test_service_recon.py",
    "test_subsystem_recall.py",
    "test_subsystem_touch.py",
}


def _defines_a_tree_helper(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return False
    return any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name in HELPER_NAMES
        for n in ast.walk(tree)
    )


def _touches_git_fixtures(path: Path) -> bool:
    """Does the module build git repositories? A tree-hashing helper over a
    non-git directory has no maintenance to disarm."""
    t = path.read_text(encoding="utf-8", errors="ignore")
    return '"init"' in t or "git init" in t or "GIT_CONFIG" in t


def _live_members() -> set[str]:
    return {
        p.name
        for p in sorted((REPO_ROOT / "scripts" / "tests").glob("test_*.py"))
        if _defines_a_tree_helper(p) and _touches_git_fixtures(p)
    }


def test_the_scan_finds_something():
    """🔴 POSITIVE CONTROL. A ledger built by a scan that walked nothing reports
    a clean set and an empty diff — indistinguishable from compliance. If this
    ever returns zero, the detector is broken, not the repo."""
    live = _live_members()
    assert len(live) >= 5, (
        f"the tree-helper scan found only {len(live)} module(s): {sorted(live)}. "
        f"That is too few to be real — HELPER_NAMES or the glob is wrong, and a "
        f"zero here would read as 'everything complies'."
    )


def test_the_ledger_matches_reality():
    """Fails when the set GROWS or SHRINKS — see the module docstring."""
    live = _live_members()
    added = sorted(live - EXPECTED_MEMBERS)
    gone = sorted(EXPECTED_MEMBERS - live)
    assert not added, (
        f"\n\nNEW module(s) fingerprint a git fixture: {added}\n"
        f"  Each must pin git's background maintenance off, or a transient\n"
        f"  .git/objects/maintenance.lock will read as a repository change and\n"
        f"  flake CI — which now blocks EVERY merge, both Tekton tiers being\n"
        f"  required with enforce_admins: true.\n"
        f"  Fix: merge `hermetic_git.MAINTENANCE_OFF` into the module's git env,\n"
        f"  then add it to EXPECTED_MEMBERS in this file.\n"
        f"  See scripts/testlib/hermetic_git.py."
    )
    assert not gone, (
        f"\n\nmodule(s) left the ledger: {gone}\n"
        f"  Either the helper was renamed (update HELPER_NAMES), the module was\n"
        f"  deleted (drop it from EXPECTED_MEMBERS), or the scan broke. A silent\n"
        f"  shrink is how a member stops being covered without anyone deciding."
    )


@pytest.mark.parametrize("member", sorted(EXPECTED_MEMBERS))
def test_every_member_uses_the_shared_pins(member: str):
    """Each ledger member must REFERENCE the shared module, not re-spell the
    pins. Four hand-rolled copies is what produced a class with 5 of 8 wrong."""
    src = (REPO_ROOT / "scripts" / "tests" / member).read_text(encoding="utf-8")
    assert "hermetic_git" in src, (
        f"{member} fingerprints a git fixture but does not reference "
        f"testlib.hermetic_git. Merge `hermetic_git.MAINTENANCE_OFF` into its "
        f"git environment; do not re-spell GIT_CONFIG_COUNT locally."
    )


def test_the_pins_actually_reach_git(tmp_path):
    """🔴 BEHAVIOURAL, on a REAL git process — the dict cannot be misspelled in
    an interesting way, so a structural check would prove nothing.

    🔴 THE NEGATIVE CONTROL IS THE LOAD-BEARING HALF: it re-queries with ONLY the
    GIT_CONFIG_COUNT injection stripped, leaving the /dev/null pins in place, so
    exactly one variable moves. Without it, a git that defaulted maintenance off
    by itself would satisfy the assertions while the pins did nothing.
    """
    repo = tmp_path / "r"
    repo.mkdir()
    env = hermetic_git.hermetic_git_env()
    subprocess.run([GIT, "init", "-q", "-b", "main", str(repo)],
                   check=True, capture_output=True, env=env)

    def cfg(key: str, e: dict) -> str:
        # `--default ''` so an UNSET key exits 0 rather than 1 — otherwise a
        # broken pin fails on the exit status and the message names the query.
        return subprocess.run(
            [GIT, "-C", str(repo), "config", "--get", "--default", "", key],
            capture_output=True, text=True, env=e,
        ).stdout.strip()

    assert cfg("maintenance.auto", env) == "false"
    assert cfg("gc.auto", env) == "0"

    stripped = {
        k: v for k, v in env.items()
        if not k.startswith(("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_",
                             "GIT_CONFIG_VALUE_"))
    }
    assert cfg("maintenance.auto", stripped) == "", (
        "git reports maintenance.auto with the injection removed, so the "
        "assertions above are green for some other reason and prove nothing "
        "about these pins"
    )


def test_the_count_matches_the_pairs():
    """🔴 GIT READS EXACTLY `GIT_CONFIG_COUNT` PAIRS. A count lower than the
    pairs present silently ignores the tail — a pin that looks right and is not,
    and the failure mode is invisible because the visible keys still work."""
    pairs = sum(1 for k in hermetic_git.MAINTENANCE_OFF if k.startswith("GIT_CONFIG_KEY_"))
    values = sum(1 for k in hermetic_git.MAINTENANCE_OFF if k.startswith("GIT_CONFIG_VALUE_"))
    count = int(hermetic_git.MAINTENANCE_OFF["GIT_CONFIG_COUNT"])
    assert pairs == values == count, (
        f"GIT_CONFIG_COUNT={count} but there are {pairs} key(s) and {values} "
        f"value(s). git will read only the first {count}."
    )


def test_hermetic_env_does_not_mutate_os_environ():
    """The builder returns a NEW dict. Mutating os.environ would leak the pins
    into every later subprocess in the run, including ones deliberately testing
    unpinned behaviour."""
    before = dict(os.environ)
    hermetic_git.hermetic_git_env(HOME="/nowhere")
    assert dict(os.environ) == before


def test_overrides_win_but_pins_survive_a_stale_base():
    """A caller's override must win; a stale GIT_CONFIG_COUNT in `base` must
    NOT, or a pin is silently dropped by an outer process's leftovers.

    ⚠ The passthrough key is deliberately NOT `PATH`. `launcher_scan` flags any
    dict literal binding PATH inside `scripts/tests` and pins the set in
    `test_no_real_launchers.py::test_every_path_clobbering_site_is_pinned` —
    correctly, since a test that REPLACES PATH drops the launcher-stub dir and
    real `systemd-run`/`notify-send`/`rofi` become reachable. This dict is a
    synthetic argument to a pure function and launches nothing, so the right fix
    was to stop looking like a clobber, NOT to add a benign entry to a safety
    ledger — an allowlist that accumulates harmless exceptions is how the next
    real one gets waved through.
    """
    got = hermetic_git.hermetic_git_env(
        base={"GIT_CONFIG_COUNT": "99", "UNRELATED_PASSTHROUGH": "/x"}, HOME="/h"
    )
    assert got["GIT_CONFIG_COUNT"] == hermetic_git.MAINTENANCE_OFF["GIT_CONFIG_COUNT"]
    assert got["HOME"] == "/h"
    assert got["UNRELATED_PASSTHROUGH"] == "/x"
