"""The canonical hermetic git environment for test fixtures — ONE copy.

WHY THIS EXISTS
---------------
Several test modules fingerprint a fixture repository by hashing every file
under its root, `.git` INCLUDED and on purpose: a read that refreshes
`.git/index` leaves file contents identical and moves an mtime, so a hash blind
to `.git` would call a mutating read "unchanged" and a read-only claim would be
false in exactly the way nobody checks.

The price of that honesty is that any file GIT creates under `.git` on its own
initiative is a difference. Git's auto-maintenance creates one, transiently:

    AssertionError: the repository changed anyway
      Right contains 1 more item:
      {'.git/objects/maintenance.lock': (0, …, 'e3b0c44…')}

That has now broken CI twice, in two different modules, and each was fixed only
AFTER it fired:

    #743  tree_hash   in scripts/tests/test_handoff_doc.py
    #780  _manifest   in scripts/tests/test_analyze_service_index_restore_verify.py

🔴 A THIRD WAS ALREADY WAITING. Measured 2026-08-24 across `scripts/`: EIGHT
modules define such a helper (`tree_hash` / `_tree_hash` / `_manifest` /
`_fingerprint`) over git-repo fixtures, and only THREE pinned maintenance off.
`claude/RULES.md`: "a predicate open-coded at N sites is typically wrong at N-1
of them in the same direction, and unifying them is what makes the disagreement
audible." Unifying them is this module.

🔴 RE-MEASURED 2026-08-26: NINE modules, and the last unpinned one had already
fired. `test_hermetic_git.py::EXPECTED_MEMBERS` is the live ledger — read the
count THERE, not the sentence above, which is a dated observation and not a
claim about today. The ninth hole was `test_analyze_service_index_commit.py`'s
`_decoy_repo_with_worktree`, which built its decoy from a bare `dict(os.environ)`
while `_run` in the same module pinned maintenance off with a comment naming
this exact hazard: git spawned `git maintenance run --auto --quiet --detach`
against the decoy, and that DETACHED process created and removed
`<decoy>/.git/objects/maintenance.lock` inside the window between the test's two
fingerprints of that tree. 4/180 red under CPU load; 0/180 once pinned.

⚠ AND THE LEDGER'S DETECTOR IS NARROWER THAN THE HAZARD, stated so the count is
not read as coverage. It keys on four helper NAMES and globs
`scripts/tests/test_*.py` only. `scripts/tests/test_git_repo_isolation.py`
creates repo fixtures with no maintenance pin and snapshots their `.git` dirs;
it is safe TODAY only because `gitenv.snapshot` reads a fixed three-file set and
never walks `objects/` — widen its `extra_files` the way
`test_analyze_service_index_commit.py::_objects_files` does and that module joins
the class with nothing to notice.

🔴 THE STAKES CHANGED, WHICH IS WHY THIS IS NOT COSMETIC. Both Tekton tiers
(`devrc-pytests` AND `devrc-nodetests`) are required on `main` with
`enforce_admins: true`. A flake in any one of these modules therefore blocks
EVERY open PR, for everyone, with no admin override — and `claude/RULES.md` is
explicit that a gate which fails for reasons unrelated to the code is how people
are trained to click through a red run.

WHAT THE PINS DO, AND WHAT THEY DO NOT
--------------------------------------
`GIT_CONFIG_COUNT` / `_KEY_n` / `_VALUE_n` inject config that outranks the repo's
own. They are ENVIRONMENT variables, so every git process spawned from one that
carries them inherits them — including gits launched by a script under test.

⚠ THEY ARE NOT REDUNDANT WITH THE `/dev/null` PINS. `GIT_CONFIG_GLOBAL` /
`GIT_CONFIG_SYSTEM` / `GIT_CONFIG_NOSYSTEM` stop git READING the operator's
config; none of them turns maintenance off, because `maintenance.auto` and
`gc.auto` default to ON with no config file at all. `test_hermetic_git.py`
proves that distinction with a negative control rather than asserting it.

⚠ AND THEY SURVIVE `testlib.gitenv`. That module strips REPO POINTER variables
from the environment to stop a fixture writing into the real repo, and
`GIT_CONFIG_COUNT`/`_KEY_n`/`_VALUE_n` are on its explicit DELIBERATELY NOT
STRIPPED ledger — they inject values into whatever repo git already resolved,
they cannot change WHICH repo that is. Verified before relying on it; if that
ledger ever changes, these pins go silently inert and the flake returns.
"""
from __future__ import annotations

import os

# The maintenance half, alone, so a caller that already has its own hermetic env
# can merge just this in without inheriting opinions about identity or protocol.
#
# 🔴 KEEP `GIT_CONFIG_COUNT` IN SYNC WITH THE NUMBER OF KEY/VALUE PAIRS. git
# reads exactly COUNT pairs; a COUNT lower than the pairs present silently
# ignores the tail, which is a pin that looks correct and is not.
MAINTENANCE_OFF: dict[str, str] = {
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "maintenance.auto",
    "GIT_CONFIG_VALUE_0": "false",
    "GIT_CONFIG_KEY_1": "gc.auto",
    "GIT_CONFIG_VALUE_1": "0",
}

# Config isolation: git must not read or write the OPERATOR's real config, or a
# stray `core.hooksPath` / `commit.gpgsign` decides whether these tests can
# commit at all — and the nix sandbox and the dev host must behave identically.
CONFIG_ISOLATION: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

# A committer identity, so `git commit` works with no global config present.
IDENTITY: dict[str, str] = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@localhost",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@localhost",
}

# Never block on a credential prompt inside a test run.
NO_PROMPT: dict[str, str] = {"GIT_TERMINAL_PROMPT": "0"}

# Everything a fixture repo normally wants.
HERMETIC: dict[str, str] = {
    **CONFIG_ISOLATION,
    **IDENTITY,
    **NO_PROMPT,
    **MAINTENANCE_OFF,
}


def hermetic_git_env(base: "dict[str, str] | None" = None,
                     **overrides: str) -> dict[str, str]:
    """`base` (default a copy of `os.environ`) + `HERMETIC` + `overrides`.

    Returns a NEW dict; `os.environ` is never mutated. `overrides` wins, so a
    module needing e.g. `GIT_ALLOW_PROTOCOL` or a per-test `HOME` can add it
    without losing the pins.

    🔴 The maintenance pins are applied AFTER `base` deliberately. A caller
    passing a `base` that already carries a stale `GIT_CONFIG_COUNT` from an
    outer process would otherwise keep it and silently drop these.
    """
    env = dict(os.environ if base is None else base)
    env.update(HERMETIC)
    env.update(overrides)
    return env
