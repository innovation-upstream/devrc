"""GUARD 9 — the suite may not operate on the git repository it RUNS FROM.

🔴 WHY THIS FILE EXISTS — measured 2026-08-21, on the operator's real clone AND
on the real GitHub remote.

A gate run rewrote `refs/heads/main` with fixture commits (`seed`, `base`,
`ahead`, `local side`, `un-pushed work stranded on main`, `autocommit: N
change(s) in the some-scope analyze-service index`), created the fixture
branches `side`/`topic`/`trunk`/`master`/`only-branch`/`feat/behind-too`,
DELETED `refs/heads/main` outright, repointed `HEAD` at `trunk`, wrote
`core.bare=true`, `user.name=T`, `user.email=t@example.invalid`, a
`core.hooksPath` pointing into `pytest-0/test_install_does_not_depend_o0/…`
and a `remote.origin.url` pointing into `pytest-0/test_fetch_failure_is_rc40/…`
— and then a push carried fixture refs to the production remote.

THE MECHANISM, reproduced exactly on a throwaway clone:

    export GIT_DIR=<clone>/.git
    git -C <tmp>/work checkout -q -b topic   -> creates `topic` in <clone>
    git -C <tmp>/work branch  -D  main       -> DELETES <clone>'s main
    git -C <tmp>/work config user.name T     -> writes <clone>/.git/config

🔴 `GIT_DIR` OVERRIDES `-C`. Every fixture in this repo is hygienic in the way
everyone checks for — it passes `-C <tmp_path>/…` and never runs bare `git` —
and every one of them is defeated by one inherited environment variable. The
repo already knew this: `scripts/claude-hooks/guard_core.py` judges a
`GIT_DIR=` prefix IN ADDITION to a resolving `-C` for exactly this reason
("even though `GIT_DIR` OVERRIDES the working directory"). The knowledge lived
in the hook that polices the OPERATOR's commands and nowhere in the harness
that runs 7,000 of our own.

And the fixture VALUES are the tell that identifies the mechanism rather than
merely being consistent with it: the tmpdir paths written into the clone's
config are the tests' OWN correct values. The tests computed the right thing
and git wrote it into the wrong repository.

WHAT THIS MODULE OWNS. `REPO_POINTER_VARS` below is the ONE owner of the set.
It is re-spelled as a bash array at the top of `run-tests.sh`,
`run-node-tests.sh` and `gate.sh` — each BEFORE that script resolves its own
ROOT — and `scripts/tests/test_git_repo_isolation.py` pins every spelling
against this tuple in both directions, plus the ordering. Three shell copies
rather than one sourced file because `testlib/runner_patch.py` writes a patched
COPY of `run-tests.sh` into a tmp dir that ~15 tests drive, and a copy cannot
source a sibling `lib/` that was never copied with it (measured: the sourced
version turned all fifteen into a FATAL):

  1. `REPO_POINTER_VARS` — the environment variables that decide WHICH
     repository a git command lands in. Stripping them is the FIX.
  2. `protected_git_dirs()` / `snapshot()` / `diff_snapshots()` — the
     DETECTOR: a content fingerprint of the host repo's refs, HEAD and config
     that `gitenv_plugin` compares around every test, so the NEXT escape —
     by any mechanism, in any test, including one nobody has thought of — is
     attributed to the test that caused it instead of being found days later
     on the remote.

DELIBERATELY NOT STRIPPED, and the reason, so the next reader does not have to
re-derive it:

  * `GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM` — these point git AWAY from the
    operator's real config; several fixtures set them on purpose. They cannot
    change which repository a `-C` resolves to.
  * `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_n` / `GIT_CONFIG_VALUE_n` — they
    inject config VALUES into whatever repo git already resolved. Same reason.
  * `GIT_AUTHOR_*` / `GIT_COMMITTER_*` — identity, not location.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# 1. THE LEDGER: what redirects git at a repository
# --------------------------------------------------------------------------- #
# 🔴 ORDERED and EXACT. Each of the three shell runners spells the same names
# in a `DEVRC_GIT_REPO_POINTERS` array — they have to, because the non-pytest
# targets (HOOK_TESTS, SHELL_TESTS, the node tier) never load a pytest plugin,
# and because an inherited GIT_DIR corrupts each runner's ROOT resolution before
# any Python runs. `test_git_repo_isolation.py::
# test_the_shell_and_python_pointer_ledgers_agree` is parametrised over all
# three and fails if any diverges from this tuple in EITHER direction. Adding a
# name here without adding it there would leave those targets unprotected while
# this file's docstring claimed otherwise.
REPO_POINTER_VARS: tuple[str, ...] = (
    "GIT_DIR",                            # the repository itself; beats -C
    "GIT_WORK_TREE",                      # the working tree
    "GIT_COMMON_DIR",                     # where refs/config actually live
    "GIT_INDEX_FILE",                     # the index a `git add` writes
    "GIT_OBJECT_DIRECTORY",               # where new objects are written
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",   # extra object stores
    "GIT_NAMESPACE",                      # the ref namespace refs land in
    "GIT_PREFIX",                         # hook-injected pathspec prefix
    "GIT_GRAFT_FILE",                     # repo-scoped grafts
    "GIT_SHALLOW_FILE",                   # repo-scoped shallow list
    "GIT_CONFIG",                         # legacy: the file `git config` WRITES
)

# The marker the plugin prints once per pytest session. `run-tests.sh` does not
# count it today, but it is the only way to tell "this target loaded GUARD 9 and
# saw nothing" apart from "this target never loaded GUARD 9" — the reassuring
# zero that RULES.md's positive-control rule is about. Spelled on both sides of
# a process boundary, so it is pinned by the test file.
SESSION_MARKER = "gitenv(session)"

# The token every violation message carries. Asserted verbatim by the
# reachability control, so a control cannot pass off a DIFFERENT guard's error
# as this one's.
VIOLATION_TOKEN = "DEVRC-GITENV-VIOLATION"

# TEST SEAM. A colon-separated list of git dirs to protect INSTEAD of the
# discovered host repo. It exists so the controls in test_git_repo_isolation.py
# can drive a nested pytest against a throwaway repo and watch this guard go
# red — a guard nobody has watched fail proves nothing. It is a seam for tests,
# not a supported way to run the suite.
PROTECT_ENV = "DEVRC_GITENV_PROTECT"


def strip_repo_pointers(env: "dict[str, str] | os._Environ | None" = None) -> "dict[str, str]":
    """Remove every `REPO_POINTER_VARS` entry from `env` (default `os.environ`).

    Returns what was removed, so the caller can REPORT it. Mutating
    `os.environ` in place is the point: a fixture that builds a subprocess
    environment with `dict(os.environ)` — which is what every git fixture in
    this repo does — copies the sanitised mapping, so the fix reaches fixtures
    that have never heard of this module and fixtures not yet written.
    """
    target = os.environ if env is None else env
    removed: dict[str, str] = {}
    for name in REPO_POINTER_VARS:
        if name in target:
            removed[name] = target[name]
            del target[name]
    return removed


# --------------------------------------------------------------------------- #
# 2. THE DETECTOR: which repositories must not move
# --------------------------------------------------------------------------- #
def resolve_git_dir(start: Path) -> "Path | None":
    """The git dir governing `start`, walking up — no subprocess, no `git`.

    Deliberately pure Python. Asking `git rev-parse` would make the DETECTOR
    depend on the same binary and the same environment the detector exists to
    police, and it would silently return nothing on a host where `git` is
    missing — an unarmed detector that still prints a clean report.

    Handles a WORKTREE, where `.git` is a FILE holding `gitdir: <path>`
    (claude/RULES.md → "any COPY you make OF it").
    """
    try:
        start = start.resolve()
    except OSError:
        return None
    for candidate in (start, *start.parents):
        dot = candidate / ".git"
        if dot.is_dir():
            return dot
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return None
            if text.startswith("gitdir:"):
                target = Path(text.split(":", 1)[1].strip())
                if not target.is_absolute():
                    target = (candidate / target)
                try:
                    return target.resolve()
                except OSError:
                    return target
    return None


def common_dir_of(git_dir: Path) -> "Path | None":
    """A linked worktree's SHARED git dir — where refs and config really live.

    `git_dir/commondir` holds the path (usually `../..`). Without this, a suite
    running inside a worktree would fingerprint the per-worktree HEAD and miss
    every ref and config write, which all land in the common dir.
    """
    marker = git_dir / "commondir"
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    target = Path(text)
    if not target.is_absolute():
        target = git_dir / target
    try:
        resolved = target.resolve()
    except OSError:
        return None
    return resolved if resolved != git_dir else None


def global_config_paths() -> "list[Path]":
    """The user-level git config files a `git config --global` would write.

    Included in the fingerprint because the incident's config damage
    (`user.name`, `core.hooksPath`, `remote.origin.url`) is the same class of
    escape as the ref damage, and a `--global` write leaves the repo itself
    untouched — so a guard watching only refs would report a clean run.
    """
    home = Path(os.path.expanduser("~"))
    xdg = os.environ.get("XDG_CONFIG_HOME")
    xdg_dir = Path(xdg) if xdg else home / ".config"
    real = [home / ".gitconfig", xdg_dir / "git" / "config"]

    override = os.environ.get("GIT_CONFIG_GLOBAL")
    if not override:
        return real

    # 🔴 A REDIRECT BY THE HARNESS IS NOT A CONFIG TO PROTECT — measured
    # 2026-08-22 when GUARD 10 (`testlib/nogit_plugin.py`) landed beside this
    # one. GUARD 10 isolates git by pointing `GIT_CONFIG_GLOBAL` at a scratch
    # `gitconfig` under its own run dir and letting tests write there; that file
    # is SUPPOSED to change — it exists so the operator's config does not. Watching
    # it made this guard report `DEVRC-GITENV-VIOLATION: CHANGED …/gitconfig` in
    # EVERY target, i.e. one guard calling the other's correct behaviour an
    # incident. Neither PR could see it alone; only the merged tree has both.
    #
    # So a scratch redirect is skipped — and `real` is watched INSTEAD OF, never
    # in addition to nothing. That is the strictly stronger reading: the old code
    # returned early on ANY override, so once something redirected the variable
    # a direct write to `~/.gitconfig` (not via `--global`) went unwatched. It is
    # the operator's files that matter, and they are watched either way.
    ov = Path(override)
    guard_dir = os.environ.get("DEVRC_TEST_GIT_GUARD_DIR")
    if guard_dir:
        try:
            ov.resolve().relative_to(Path(guard_dir).resolve())
        except (ValueError, OSError):
            pass          # points somewhere else — a real config, watch it
        else:
            return real   # session-owned scratch: watch the operator's, not this
    return [ov]


def protected_git_dirs(starts: "list[Path] | None" = None) -> "list[Path]":
    """Every git dir the suite must leave byte-identical.

    Two roots by default and both are needed: the CWD (what `run-tests.sh` cd's
    to, and what a bare `pytest` inherits) and this file's own location (which
    survives a runner invoked from somewhere else entirely). A worktree
    contributes its common dir as well.

    `DEVRC_GITENV_PROTECT` replaces the discovery entirely — see PROTECT_ENV.
    """
    override = os.environ.get(PROTECT_ENV)
    if override:
        return [Path(p) for p in override.split(os.pathsep) if p]

    if starts is None:
        starts = [Path.cwd(), Path(__file__).resolve().parent]

    found: list[Path] = []
    for start in starts:
        git_dir = resolve_git_dir(start)
        if git_dir is None:
            continue
        for candidate in (git_dir, common_dir_of(git_dir)):
            if candidate is not None and candidate not in found:
                found.append(candidate)
    return found


# The per-git-dir files whose content IS the repository's identity for our
# purposes. `index` is deliberately absent: a plain `git status` rewrites it as
# a racy-timestamp refresh, and a detector that reds on a READ is a
# permanently-red gate (claude/RULES.md), which trains everyone to ignore it.
# Every damage class from the incident lands in one of these instead —
# commits/branch creates/deletes and HEAD moves in `refs/`+`HEAD`+`packed-refs`,
# and `core.bare`/`user.*`/`core.hooksPath`/`remote.origin.url` in `config`.
_GIT_DIR_FILES = ("config", "HEAD", "packed-refs", "ORIG_HEAD", "logs/HEAD")
_GIT_DIR_TREES = ("refs",)

_LABEL_MISSING = "<absent>"


def _digest(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            h = hashlib.sha256()
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return _LABEL_MISSING
    except OSError as exc:  # unreadable is a CHANGE we must not hide
        return f"<unreadable: {exc.__class__.__name__}>"


def snapshot(git_dirs: "list[Path]", extra_files: "list[Path] | None" = None) -> "dict[str, str]":
    """A content fingerprint of every protected repository.

    Content hashes rather than `stat`: an mtime is a claim about WHEN, and two
    writes inside one timestamp tick would report SAME. The set is small (five
    files plus the loose refs per repo), so exactness is affordable.
    """
    out: dict[str, str] = {}
    for git_dir in git_dirs:
        for rel in _GIT_DIR_FILES:
            out[f"{git_dir}/{rel}"] = _digest(git_dir / rel)
        for tree in _GIT_DIR_TREES:
            root = git_dir / tree
            if not root.is_dir():
                out[f"{git_dir}/{tree}/"] = _LABEL_MISSING
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    p = Path(dirpath) / name
                    out[str(p)] = _digest(p)
    for path in extra_files or []:
        out[str(path)] = _digest(path)
    return out


def diff_snapshots(before: "dict[str, str]", after: "dict[str, str]") -> "list[str]":
    """Human-readable deltas, one line each. Empty list == nothing moved."""
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        was = before.get(key, _LABEL_MISSING)
        now = after.get(key, _LABEL_MISSING)
        if was == now:
            continue
        if was == _LABEL_MISSING:
            lines.append(f"  CREATED  {key}")
        elif now == _LABEL_MISSING:
            lines.append(f"  DELETED  {key}")
        else:
            lines.append(f"  CHANGED  {key}  {was[:12]} -> {now[:12]}")
    return lines


def violation_message(where: str, deltas: "list[str]", git_dirs: "list[Path]") -> str:
    """The one place a GUARD 9 failure is worded.

    Carries `VIOLATION_TOKEN` so a control can assert THIS guard fired and not a
    neighbour's error (claude/RULES.md → "prove it REACHABLE"), and names the
    remediation, because the reader is about to conclude the harness is flaky.
    """
    listed = "\n".join(f"    {d}" for d in git_dirs) or "    (none discovered)"
    return (
        f"{VIOLATION_TOKEN}: {where} MUTATED a git repository that is not its own tmpdir.\n"
        "\n"
        "Protected git dir(s):\n"
        f"{listed}\n"
        "\n"
        "What moved:\n" + "\n".join(deltas) + "\n"
        "\n"
        "🔴 This is the 2026-08-21 incident's shape. On that day a gate run rewrote\n"
        "`refs/heads/main` with fixture commits, deleted the branch, rewrote\n"
        "`core.hooksPath` and `remote.origin.url`, and pushed fixture refs to the\n"
        "production remote. The fixtures were NOT sloppy — they all passed\n"
        "`-C <tmp_path>`; an inherited GIT_DIR simply overrides `-C`.\n"
        "\n"
        "Check, in this order:\n"
        f"  1. Was one of {', '.join(REPO_POINTER_VARS[:4])}… set in the environment?\n"
        "     `scripts/testlib/gitenv.py` strips them; something re-set one AFTER\n"
        "     the plugin loaded (a monkeypatch.setenv, a conftest, a wrapper).\n"
        "  2. Does the failing test run git with a path it did not build under\n"
        "     `tmp_path` — e.g. a repo root derived from `__file__`?\n"
        "  3. Restore this checkout before doing anything else: `git reflog` is the\n"
        "     one-command diagnosis for a branch that looks like it moved backwards."
    )
