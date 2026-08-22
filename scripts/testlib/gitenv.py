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
that runs 14,000 of our own.

🔴 WHAT WAS **NOT** ESTABLISHED, and the correction matters because it was
relayed onward as fact. #683's body claimed `githooks/pre-push` handing down
`GIT_DIR` explained why *pushing* triggered the corruption. **Measured on git
2.55.0, as a PAIR: a `git push` from a `GIT_DIR`-free parent gives `pre-push`
only `GIT_EXEC_PATH` and `GIT_PREFIX` (plus the caller's own `GIT_AUTHOR_*` /
`GIT_CONFIG_*`) — NO `GIT_DIR`; the same push with `GIT_DIR` exported by the
caller passes it straight through.** The old `GIT_DIR=…` line in that
hook was a route only if some OUTER caller had already exported the name (bash
keeps an already-exported name exported across a reassignment); the hook's own
comment stated that precondition correctly and the PR body dropped it. A live
scan of the box found 46 processes carrying some `GIT_*` variable and **0**
carrying `GIT_DIR` (13 unreadable). **THE ROOT CAUSE — what exported `GIT_DIR`
into that gate run — IS STILL UNKNOWN.** The rename is correct hygiene and the
strip below closes the mechanism whatever set it; neither identifies the
setter. Do not cite the pre-push rename as the diagnosis.

And the fixture VALUES are the tell that identifies the MECHANISM (an inherited
`GIT_DIR`) rather than merely being consistent with it: the tmpdir paths
written into the clone's config are the tests' OWN correct values. The tests
computed the right thing and git wrote it into the wrong repository.

WHAT THIS MODULE OWNS. `REPO_POINTER_VARS` below is the ONE owner of the set.
It is re-spelled as a bash array at the top of `run-tests.sh`,
`run-node-tests.sh`, `gate.sh` and `githooks/tests-on-push.sh` — each BEFORE
that script resolves its own ROOT — and `scripts/tests/test_git_repo_isolation.py`
pins every spelling against this tuple in both directions, plus the ordering.
Four shell copies rather than one sourced file because `testlib/runner_patch.py`
writes a patched COPY of `run-tests.sh` into a tmp dir that ~15 tests drive, and
a copy cannot source a sibling `lib/` that was never copied with it (measured:
the sourced version turned all fifteen into a FATAL):

  1. `REPO_POINTER_VARS` — the environment variables that decide WHICH
     repository a git command lands in. Stripping them is the FIX.
  2. `CONTROL_VARS` — this guard's OWN seams. They are on the runners' unset
     list for the same reason as the pointers: an inherited
     `DEVRC_GITENV_PROTECT` used to redirect the whole detector, silently, with
     the marker line still reporting a healthy `protected-git-dirs=1`.
  3. `protected_git_dirs()` / `snapshot()` / `diff_snapshots()` — the
     DETECTOR: a content fingerprint of the host repo's ref VALUES, HEAD and
     config that `gitenv_plugin` compares around every test, so the NEXT escape
     — by any mechanism, in any test — is attributed to the test that caused it
     instead of being found days later on the remote.
  4. `attribution_evidence()` / `MODE_*` — 🔴 the ANSWER TO "WHO ELSE WRITES
     HERE". See "ATTRIBUTION" below; it is the difference between a guard and a
     permanently-red gate.

🔴 ATTRIBUTION — why the detector has two modes, measured 2026-08-22.

The detector watches a repository it does not own. On the operator's box that
clone has **dozens of concurrent writers**: during one 40-minute audit it gained
two branches, lost one, and fast-forwarded `main` twice, and the `drift-check`
systemd timer runs `git fetch origin` against it four times a day (whose
`gc --auto` used to emit *hundreds* of `DELETED refs/…` lines under a banner
saying the incident had recurred). A guard that fires on that, and asserts "test
X MUTATED a git repository" at maximum confidence, is worse than no guard:
claude/RULES.md rates a permanently-red gate as one that trains everyone to
click through, and during an incident it actively misdirects.

Three independent changes, all mechanical, none a reword:

  a. THE FINGERPRINT IS REF **VALUES**, NOT REF **FILES**. `packed-refs` and
     the loose `refs/` tree are parsed into one name -> object-id map, so
     `git gc` / `git pack-refs` — which move every loose ref into `packed-refs`
     while changing nothing about the repository's state — produce EXACTLY ZERO
     deltas. That is the single loudest false-positive class, removed
     structurally rather than filtered. `logs/HEAD` is gone for the same
     reason: it is a pure derivative (every state it witnesses is also
     witnessed by HEAD or the ref map) that `gc`'s reflog expiry rewrites on
     its own.
  b. POSITIVE EVIDENCE OF ANOTHER WRITER DOWNGRADES THE SESSION. Two probes,
     both facts rather than inferences: live processes whose CWD sits inside a
     protected work tree and are not our own ancestors (`live_cotenants`), and
     a delta observed in a window when NO test body was running (the plugin's
     idle probe, plus a settle re-read taken at the moment of any delta). Once
     either fires, this repository is *known* to have another writer,
     attribution is *known* to be impossible, and the detector reports instead
     of failing — loudly, with the full delta and a session-end count.
  c. THE VIOLATION MESSAGE LEADS WITH "ANOTHER PROCESS WROTE HERE". It used to
     lead with the incident. See `violation_message`.

Prevention (the strip) is untouched by any of this and does not depend on the
mode. Report mode weakens ATTRIBUTION, never the fix.

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
# 🔴 ORDERED and EXACT. Each of the four shell entry points spells the same
# names in a `DEVRC_GIT_REPO_POINTERS` array — they have to, because the
# non-pytest targets (HOOK_TESTS, SHELL_TESTS, the node tier) never load a
# pytest plugin, and because an inherited GIT_DIR corrupts each runner's ROOT
# resolution before any Python runs. `test_git_repo_isolation.py::
# test_the_shell_and_python_pointer_ledgers_agree` is parametrised over all
# four and fails if any diverges from this tuple in EITHER direction. Adding a
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

# TEST SEAM. A `os.pathsep`-separated list of git dirs to protect INSTEAD of the
# discovered host repo. It exists so the controls in test_git_repo_isolation.py
# can drive a nested pytest against a throwaway repo and watch this guard go
# red — a guard nobody has watched fail proves nothing. It is a seam for tests,
# not a supported way to run the suite.
#
# 🔴 AND IT IS VALIDATED, LOUDLY. Measured on #683's version: `PROTECT=":"` gave
# `protected-git-dirs=0` and a GREEN run while the escaping test really created
# its branch, and `PROTECT=/nonexistent/x` gave `protected-git-dirs=1` — a
# marker line reporting healthy coverage — and a GREEN run over the same real
# mutation. One inherited variable defeated the entire detection half, inside
# the fix for one inherited variable defeating everything. `resolve_protect_env`
# now raises rather than degrading, and the runners unset it (CONTROL_VARS).
PROTECT_ENV = "DEVRC_GITENV_PROTECT"

# TEST SEAM. Forces the detector's mode instead of deciding it from evidence.
# The nested controls pin it so their verdict cannot depend on how busy the
# host repository happens to be while the suite runs.
MODE_ENV = "DEVRC_GITENV_MODE"

MODE_ENFORCE = "enforce"   # a delta FAILS the test it was observed under
MODE_REPORT = "report"     # a delta is printed in full; nothing fails
MODE_AUTO = "auto"         # enforce until another writer is PROVEN (default)
MODES = (MODE_AUTO, MODE_ENFORCE, MODE_REPORT)

# 🔴 This guard's OWN environment seams, on the runners' unset list beside
# REPO_POINTER_VARS and pinned two-way by the same test. Finding B of #683's
# audit: no runner cleared PROTECT_ENV, so a single inherited value silently
# redirected every layer of the detector.
CONTROL_VARS: tuple[str, ...] = (PROTECT_ENV, MODE_ENV)

# The marker the plugin prints once per pytest session, and `run-tests.sh`
# COUNTS it per target: it is the only way to tell "this target loaded GUARD 9
# and saw nothing" apart from "this target never loaded GUARD 9" — the
# reassuring zero that RULES.md's positive-control rule is about. Spelled on
# both sides of a process boundary, so it is pinned by the test file.
SESSION_MARKER = "gitenv(session)"

# Printed instead of failing, in report mode. Deliberately does NOT carry
# VIOLATION_TOKEN: a control that asserts the token must not be satisfiable by
# an unattributed observation.
OBSERVED_MARKER = "gitenv(observed)"

# Printed once, when another writer is PROVEN and the session downgrades.
FOREIGN_MARKER = "gitenv(foreign-writer)"

# The token every ATTRIBUTED violation message carries. Asserted verbatim by the
# reachability control, so a control cannot pass off a DIFFERENT guard's error
# as this one's.
VIOLATION_TOKEN = "DEVRC-GITENV-VIOLATION"


class GitEnvConfigError(RuntimeError):
    """A GUARD 9 seam was set to something that cannot be honoured.

    Raised, never degraded-around: the failure mode this replaces is a green run
    under a marker line that claimed coverage the detector did not have.
    """


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


def requested_mode(env: "dict[str, str] | os._Environ | None" = None) -> str:
    """`MODE_ENV`, validated. An unknown value RAISES rather than defaulting.

    Defaulting on a typo is how `DEVRC_GITENV_MODE=enfore` would silently buy a
    report-only session that reads as enforcement.
    """
    target = os.environ if env is None else env
    if MODE_ENV not in target:
        return MODE_AUTO
    raw = target[MODE_ENV].strip()
    if raw not in MODES:
        raise GitEnvConfigError(
            f"{MODE_ENV}={target[MODE_ENV]!r} is not one of {MODES}. GUARD 9 will "
            "not guess: an unrecognised mode would silently buy a report-only "
            "session that reads as enforcement."
        )
    return raw


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

    🔴 It is also, by construction, the dir SHARED with every sibling worktree
    and therefore with every sibling agent (claude/RULES.md → "a worktree
    isolates a working DIRECTORY only"). Watching it is right — an escape lands
    there — but it is precisely the case where attribution is impossible, which
    is what `attribution_evidence()` and report mode exist for. The answer is
    not to stop watching; it is to stop *asserting* when another writer is
    proven.
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


def _looks_like_a_git_dir(path: Path) -> bool:
    """`path` is a directory git would accept as a repository.

    `HEAD` plus one of `refs`/`objects` — the same shape `git rev-parse` uses.
    Deliberately not "the directory exists": `/nonexistent/x` and `/tmp` are
    both things a mis-set seam has actually pointed at.
    """
    if not path.is_dir():
        return False
    return (path / "HEAD").is_file() and (
        (path / "refs").is_dir() or (path / "objects").is_dir())


def resolve_protect_env(env: "dict[str, str] | os._Environ | None" = None) -> "list[Path] | None":
    """`PROTECT_ENV`, validated. `None` when unset — anything else is honoured
    exactly or RAISES.

    🔴 THE FAILURE THIS REPLACES was silent and reassuring, both directions
    measured on #683's code with the same escaping test:

        PROTECT=<real>/.git   -> protected-git-dirs=1, RED  (correct)
        PROTECT=":"           -> protected-git-dirs=0, GREEN, branch created
        PROTECT=/nonexistent/x-> protected-git-dirs=1, GREEN, branch created

    The third is the worst: the marker line asserted coverage over a path that
    cannot hold a repository. An unresolvable seam is now a LOUD failure, never
    a green with a marker.
    """
    target = os.environ if env is None else env
    if PROTECT_ENV not in target:
        return None
    raw = target[PROTECT_ENV]
    entries = [p for p in raw.split(os.pathsep) if p.strip()]
    if not entries:
        raise GitEnvConfigError(
            f"{PROTECT_ENV}={raw!r} is set but names no path. GUARD 9 will not "
            "fall back to discovery: on #683's code that spelling produced "
            "`protected-git-dirs=0` and a GREEN run over a real escape. Unset "
            "the variable to get discovery, or give it a git dir."
        )
    resolved: list[Path] = []
    bad: list[str] = []
    for entry in entries:
        path = Path(entry.strip())
        try:
            path = path.resolve()
        except OSError:
            bad.append(f"{entry} (cannot resolve)")
            continue
        if not _looks_like_a_git_dir(path):
            bad.append(f"{entry} (not a git dir: needs HEAD + refs/ or objects/)")
            continue
        if path not in resolved:
            resolved.append(path)
    if bad:
        raise GitEnvConfigError(
            f"{PROTECT_ENV} names {len(bad)} path(s) GUARD 9 cannot protect:\n  "
            + "\n  ".join(bad)
            + "\n\nThis is a hard failure by design. A detector pointed at a "
              "path that cannot hold a repository reports `protected-git-dirs="
              f"{len(entries)}` and stays green through any amount of damage — "
              "measured, on the version this replaces."
        )
    return resolved


def protected_git_dirs(starts: "list[Path] | None" = None) -> "list[Path]":
    """Every git dir the suite must leave byte-identical.

    Two roots by default and BOTH are load-bearing — pinned by
    `test_the_cwd_root_is_load_bearing` and `test_the_module_root_is_load_bearing`,
    which is what a bare "both are needed" comment was not:

      * `Path.cwd()` — what `run-tests.sh` cd's to and what a bare `pytest`
        inherits. It is the ONLY root when the runner is invoked from a
        different repository than the one holding this file.
      * this file's own directory — which survives a `cwd` that is not in any
        repository at all (a tmp rootdir; every nested control in the test file
        runs that way).

    A worktree contributes its common dir as well.

    `PROTECT_ENV` replaces the discovery entirely, and is VALIDATED — see
    `resolve_protect_env`.
    """
    override = resolve_protect_env()
    if override is not None:
        return override

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


# The per-git-dir files whose CONTENT is part of the repository's identity, on
# top of the ref VALUE map below. `index` is deliberately absent: a plain
# `git status` rewrites it as a racy-timestamp refresh, and a detector that reds
# on a READ is a permanently-red gate (claude/RULES.md).
#
# 🔴 EVERY ENTRY HERE IS PINNED BY A MUTATION THAT ONLY IT CAN SEE
# (`test_every_fingerprint_component_is_load_bearing`) — finding C of #683's
# audit was that dropping `HEAD`, `packed-refs`, or `ORIG_HEAD`+`logs/HEAD`
# each survived a fully green suite:
#   * `config`    — `git config user.name T` (the incident's config damage)
#   * `HEAD`      — `git symbolic-ref HEAD refs/heads/other` (the incident
#                   repointed HEAD at `trunk`); moves HEAD and no ref
#   * `ORIG_HEAD` — `git reset -q HEAD`; moves nothing else
#
# `logs/HEAD` was REMOVED rather than pinned. It is a pure derivative — every
# state it can witness is also witnessed by `HEAD` or the ref map — and `git gc`
# rewrites it on its own during reflog expiry (measured), i.e. it contributed
# false positives and no unique coverage. `packed-refs` was likewise removed as
# a hashed FILE and folded into `_ref_values`, where it belongs: see below.
_GIT_DIR_FILES = ("config", "HEAD", "ORIG_HEAD")

_LABEL_MISSING = "<absent>"

_REF_KEY = "::ref::"
_FILE_KEY = "::file::"


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


def ref_values(git_dir: Path) -> "dict[str, str]":
    """`refs/…` -> object id, from `packed-refs` AND the loose tree, loose wins.

    🔴 VALUES, NOT FILES, and this is the single biggest false-positive
    reduction in GUARD 9. Hashing the loose `refs/` tree made `git gc` and
    `git pack-refs` — which move every loose ref into `packed-refs` and change
    nothing about what the repository CONTAINS — emit one `DELETED` line per
    branch: on the operator's clone, hundreds of them, under a banner claiming
    the 2026-08-21 incident had recurred. `drift-check.sh` runs `git fetch` on a
    6-hourly timer against exactly that clone and `fetch` triggers `gc --auto`
    (its own comment says so), so that was not a hypothetical.

    Reading the packed side is also what makes the incident's WORST act visible
    at all: `refs/heads/main` was DELETED, and on a repo whose refs have been
    packed that deletion is a `packed-refs` rewrite with no loose file anywhere.
    """
    refs: dict[str, str] = {}

    packed = git_dir / "packed-refs"
    try:
        text = packed.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            refs[parts[1].strip()] = parts[0]

    root = git_dir / "refs"
    if root.is_dir():
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if name.endswith(".lock"):
                    # A transient write-in-progress, not a ref. Hashing it makes
                    # the detector race with any concurrent git.
                    continue
                p = Path(dirpath) / name
                try:
                    rel = p.relative_to(git_dir).as_posix()
                except ValueError:  # pragma: no cover - os.walk stays under root
                    continue
                try:
                    refs[rel] = p.read_text(encoding="utf-8", errors="replace").strip()
                except OSError as exc:
                    refs[rel] = f"<unreadable: {exc.__class__.__name__}>"
    return refs


def snapshot(git_dirs: "list[Path]", extra_files: "list[Path] | None" = None) -> "dict[str, str]":
    """A content fingerprint of every protected repository.

    Content hashes rather than `stat`: an mtime is a claim about WHEN, and two
    writes inside one timestamp tick would report SAME. The set is small (three
    files plus the resolved refs per repo), so exactness is affordable.
    """
    out: dict[str, str] = {}
    for git_dir in git_dirs:
        for rel in _GIT_DIR_FILES:
            out[f"{git_dir}{_FILE_KEY}{rel}"] = _digest(git_dir / rel)
        for name, value in ref_values(git_dir).items():
            out[f"{git_dir}{_REF_KEY}{name}"] = value
    for path in extra_files or []:
        out[f"{path}{_FILE_KEY}"] = _digest(path)
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


# --------------------------------------------------------------------------- #
# 3. ATTRIBUTION: is this repository ours to blame a test for?
# --------------------------------------------------------------------------- #
def _own_process_lineage() -> "set[int]":
    """This process and its ancestors, so the co-tenant probe never counts us.

    Read from `/proc/<pid>/stat` field 4 (PPid). A sibling agent is NOT an
    ancestor, which is the whole point — that is the case we must be able to
    see.
    """
    lineage: set[int] = set()
    pid = os.getpid()
    for _ in range(64):  # a lineage longer than this is a loop, not a tree
        if pid <= 0 or pid in lineage:
            break
        lineage.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            # comm can contain spaces and parentheses; PPid is the field after
            # the final ')'.
            after = stat[stat.rindex(")") + 1:].split()
            pid = int(after[1])
        except (OSError, ValueError, IndexError):
            break
    return lineage


def live_cotenants(git_dirs: "list[Path]") -> "list[str]":
    """Live processes, not our own ancestors, sitting inside a protected repo.

    POSITIVE EVIDENCE, not a heuristic: if another process's CWD is inside the
    work tree of a repository we are about to fingerprint, that repository has
    another user and any delta we see is unattributable. On the operator's box
    this is the normal state — 30+ Claude sessions were live in that one clone
    while #683 was being audited.

    Returns `pid:comm` strings (bounded), empty when there is no such evidence.
    An unreadable `/proc` yields an empty list: absence of evidence leaves the
    detector in ENFORCE mode, which is the direction that keeps the guard sharp.
    """
    roots: list[Path] = []
    for git_dir in git_dirs:
        # The work tree is the git dir's parent for a normal `.git`; also accept
        # the git dir itself so a bare repo is covered.
        for candidate in (git_dir.parent, git_dir):
            if candidate not in roots:
                roots.append(candidate)
    if not roots:
        return []

    proc = Path("/proc")
    if not proc.is_dir():
        return []
    mine = _own_process_lineage()
    found: list[str] = []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in mine:
            continue
        try:
            cwd = (entry / "cwd").resolve()
        except OSError:
            continue  # 13 of them were unreadable on the audited box; not evidence
        for root in roots:
            try:
                cwd.relative_to(root)
            except ValueError:
                continue
            try:
                comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                comm = "?"
            found.append(f"{pid}:{comm}")
            break
        if len(found) >= 8:  # a count of 8 and a count of 300 mean the same thing
            break
    return found


def attribution_evidence(git_dirs: "list[Path]") -> "list[str]":
    """Reasons this session cannot attribute a delta to a test. Empty == it can.

    Kept separate from the probes themselves so the plugin can add reasons it
    learns LATER (a delta in an idle window, a settle re-read that moved again)
    to the same list and word them the same way.
    """
    reasons: list[str] = []
    cotenants = live_cotenants(git_dirs)
    if cotenants:
        reasons.append(
            "live processes are sitting inside a protected repository "
            f"(cwd), and none of them is ours: {', '.join(cotenants)}"
        )
    return reasons


# --------------------------------------------------------------------------- #
# 4. WORDING
# --------------------------------------------------------------------------- #
def _preamble(git_dirs: "list[Path]", deltas: "list[str]") -> str:
    listed = "\n".join(f"    {d}" for d in git_dirs) or "    (none discovered)"
    return (
        "Protected git dir(s):\n"
        f"{listed}\n"
        "\n"
        "What moved:\n" + "\n".join(deltas) + "\n"
    )


def observation_message(where: str, deltas: "list[str]", git_dirs: "list[Path]",
                        reasons: "list[str]") -> str:
    """Report mode's wording. 🔴 Deliberately WITHOUT `VIOLATION_TOKEN`.

    A control that asserts the token must not be satisfiable by an observation
    nobody could attribute — otherwise the reachability proof degrades into
    "something printed something".
    """
    why = "\n".join(f"    - {r}" for r in reasons) or "    - (none recorded)"
    return (
        f"{OBSERVED_MARKER}: a protected git repository changed during "
        f"{where}, and GUARD 9 CANNOT SAY WHO DID IT.\n"
        "\n"
        f"{_preamble(git_dirs, deltas)}"
        "\n"
        "Why this is a report and not a failure:\n"
        f"{why}\n"
        "\n"
        "GUARD 9's PREVENTION half (the `REPO_POINTER_VARS` strip) is unaffected "
        "and still in force; only ATTRIBUTION is impossible here. If you expected "
        "this repository to have exactly one writer, that expectation is the bug — "
        f"re-run where it holds, or pin `{MODE_ENV}={MODE_ENFORCE}`."
    )


def violation_message(where: str, deltas: "list[str]", git_dirs: "list[Path]") -> str:
    """The one place an ATTRIBUTED GUARD 9 failure is worded.

    Carries `VIOLATION_TOKEN` so a control can assert THIS guard fired and not a
    neighbour's error (claude/RULES.md → "prove it REACHABLE"), and names the
    remediation, because the reader is about to conclude the harness is flaky.

    🔴 HYPOTHESIS 1 IS "SOMETHING ELSE WROTE HERE", and the order is the fix.
    The first version led with the incident, so a `git branch` run by any of the
    dozens of concurrent sessions on the operator's box produced *"test X
    MUTATED a git repository that is not its own tmpdir … This is the
    2026-08-21 incident's shape"* — maximum confidence, wrong subject. This
    message is only reached when the co-tenant probe, the idle probe and a
    settle re-read ALL found no other writer, and it says so, because the reader
    needs to know which of those to distrust.
    """
    return (
        f"{VIOLATION_TOKEN}: a protected git repository changed during {where}.\n"
        "\n"
        f"{_preamble(git_dirs, deltas)}"
        "\n"
        "Check, in this order:\n"
        "  1. 🔴 DID SOMETHING ELSE WRITE TO THIS REPOSITORY? By far the most\n"
        "     common cause, and the one GUARD 9 cannot see from inside: another\n"
        "     agent session, an editor, a `git fetch` timer (`drift-check` runs\n"
        "     one 4x/day), a `gc`. This message is only printed when no process\n"
        "     was found sitting in the repo, the delta held still across a\n"
        "     re-read, and nothing moved while no test was running — but all\n"
        "     three are ABSENCE of evidence. `git reflog` dates the change and\n"
        "     names the operation.\n"
        f"  2. Was one of {', '.join(REPO_POINTER_VARS[:4])}… set in the environment?\n"
        "     `scripts/testlib/gitenv.py` strips them; something re-set one AFTER\n"
        "     the plugin loaded (a monkeypatch.setenv, a conftest, a wrapper).\n"
        "  3. Does the failing test run git with a path it did not build under\n"
        "     `tmp_path` — e.g. a repo root derived from `__file__`? That is the\n"
        "     one escape the strip cannot close.\n"
        "  4. If 2 or 3 holds, this is the 2026-08-21 incident's shape: a gate run\n"
        "     rewrote `refs/heads/main` with fixture commits, deleted the branch,\n"
        "     rewrote `core.hooksPath` and `remote.origin.url`, and pushed fixture\n"
        "     refs to the production remote. The fixtures were NOT sloppy — they\n"
        "     all passed `-C <tmp_path>`; an inherited GIT_DIR overrides `-C`.\n"
        "     Restore this checkout before doing anything else."
    )
