"""Pin `rebase.autoStash` / `merge.autoStash` to false.

WHY THIS TEST EXISTS. RULES.md forbids `git stash` in any repo shared with
other sessions or agents: `refs/stash` lives in the COMMON git dir, so all
worktrees of a repo share ONE stack, and two parallel subagents stole each
other's work that way (2026-07-25). `guard_core.py`'s `check_git_stash`
enforces that ban -- but it matches COMMAND TEXT, and autoStash is git pushing
and popping the shared stack INTERNALLY. Nobody types `git stash`, so the guard
structurally CANNOT fire. With `pull.rebase = true` also set, every `git pull`
on a dirty tree became a silent stash push/pop against a shared stack.

MEASURED 2026-08-03: a subagent's routine `git rebase` printed "Created
autostash" against a stack holding 9 entries belonging to other sessions.

This test reads the NIX SOURCE, which is the only thing that ships. The
deployed `~/.config/git/config` is a read-only store symlink -- `git config
--global` against it fails with "Read-only file system", so a runtime fix is
not even possible, and asserting on the live file would pass on a host that had
simply never switched.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GIT_NIX = ROOT / "nix" / "programs" / "git" / "default.nix"


def _eval_git_settings():
    if not shutil.which("nix-instantiate"):
        pytest.fail(
            "nix-instantiate not on PATH. This test pins a SAFETY setting that "
            "the bash guard structurally cannot enforce; a skip here is exactly "
            "how autoStash silently comes back. Run under nix-shell / the flake "
            "gate, where `nix-instantiate` is a declared required tool."
        )
    p = subprocess.run(
        ["nix-instantiate", "--eval", "--strict", "--json", "-E",
         f"(import {GIT_NIX} {{}}).settings"],
        capture_output=True, text=True, cwd=ROOT, timeout=180,
    )
    assert p.returncode == 0, f"nix eval failed:\n{p.stderr}"
    return json.loads(p.stdout)


@pytest.mark.parametrize("key", ["rebase.autoStash", "merge.autoStash"])
def test_autostash_is_disabled(key):
    settings = _eval_git_settings()
    section, leaf = key.split(".")
    # home-manager accepts both `rebase.autoStash = x` and a nested attrset.
    val = settings.get(key, settings.get(section, {}).get(leaf)
                       if isinstance(settings.get(section), dict) else None)
    assert val is not None, (
        f"{key} is not set at all in nix/programs/git/default.nix. Unset means "
        f"git's own default applies, which for a `git pull --rebase` on a dirty "
        f"tree can still reach the repo-global stash. Set it explicitly false."
    )
    assert val is False, (
        f"{key} is {val!r}, must be False. autoStash uses the REPO-GLOBAL stash "
        f"without anyone typing `git stash`, so guard_core.py's check_git_stash "
        f"cannot see it. See RULES.md -> 'git stash is repo-GLOBAL'."
    )


def test_pull_rebase_is_still_on_so_the_hazard_is_real():
    """Non-vacuity control.

    The autoStash assertions only matter because `pull.rebase = true` routes
    every `git pull` through rebase. If that were ever turned off this test
    fails loudly rather than letting the pair above quietly become decorative.
    """
    settings = _eval_git_settings()
    val = settings.get("pull.rebase", settings.get("pull", {}).get("rebase")
                       if isinstance(settings.get("pull"), dict) else None)
    assert val is True, (
        f"pull.rebase is {val!r}. The autoStash pins above were written for a "
        f"rebase-by-default setup; re-read them against the new behaviour "
        f"rather than assuming they still cover the hazard."
    )
