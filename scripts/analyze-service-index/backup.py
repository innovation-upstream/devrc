#!/usr/bin/env python3
"""Encrypted OFF-MACHINE backup for the /analyze-service index store.

WHAT THIS CLOSES
----------------
`~/.claude/analyze-service-index/<scope>/` is one independent git repository per
scope. `commit.sh` (the hourly autocommit) gives every scope local history, which
defends against exactly one failure mode: an agent's Write clobbering a file that
a previous run had already committed.

It defends against NOTHING ELSE. Measured on the workbench 2026-08-21: 10 scope
repos, every one of them `remote = none`, and no copy of any of them anywhere off
this disk. A disk failure, a filesystem corruption, or an `rm -rf` of the store
root loses the whole thing permanently, history included — the history lives
*inside* the thing being destroyed. The content is not re-derivable: it records
gotchas, retracted theories and measurements that were true at a moment.

The laptop's store is DIVERGENT content, not a copy. It is a second thing to
lose, not a backup of the first.

WHY BUNDLES, AND WHY NO REMOTE
------------------------------
🔴 Every scope README states the no-remote invariant, and `commit.sh` is built
around it (its systemd unit runs with `PrivateNetwork=true` so the invariant is
enforced by the namespace, not by prose). A `git bundle` preserves that
literally: it is a single file containing the full object graph and all refs,
produced by a command that only READS the repository. No remote is added, no
push happens, no `git config` is written, and this script never runs a git
subcommand outside `_READ_ONLY_SUBCOMMANDS` below.

That read-only-ness is not asserted, it is measured: the test suite checksums a
synthetic store before and after a full run and requires byte identity, and
separately requires that every scope still reports zero remotes.

🔴 READ-ONLY IS ONLY HALF THE QUESTION — THE OTHER HALF IS *WHICH REPOSITORY*
------------------------------------------------------------------------------
Every git call below is `git -C <scope> …`, and `-C` is the weakest possible
claim about where a command lands: **GIT_DIR OVERRIDES IT**. MEASURED 2026-08-23
(git 2.55.0) on the pre-fix tree, sole variable `GIT_DIR` pointed at a decoy
repo, driving this CLI end to end under the unit's own environment shape:

    control (no leak)  rc=0  bundle head: <sha> refs/heads/trunk
    GIT_DIR=<decoy>    rc=0  bundle head: <sha> refs/heads/foreign-branch

A perfectly read-only run, of the wrong repository, encrypted and uploaded
off-box under a green timer. Being read-only is what makes it *worse*, not
better: nothing looks damaged afterwards. `_git_env()` therefore strips the
repo-pointer family before every invocation; see its docstring, and
`testlib/gitenv.py` for the incident that established the mechanism.

WHY age, WHEN MINIO IS SELF-HOSTED
----------------------------------
🔴 The store is client-confidential and the scope READMEs say the content never
leaves the machine. MinIO being on the operator's own homelab makes that *nearly*
true, not true. Encrypting to the operator's age identity before the bytes leave
the process makes it literally true: MinIO stores ciphertext it cannot read, and
a compromised or misconfigured bucket policy discloses nothing.

The identity is the one that ALREADY EXISTS — the SOPS age key used across the
homelab repos (`~/workspace/homelab-talos/.secrets/age.key`, recipient
`age1g0nf…dj64t` in that repo's `.sops.yaml`). No new key is minted, because a
backup encrypted to a key the operator does not already keep alive is a backup
that will not decrypt when it is finally needed. The RECIPIENT is DERIVED from
the identity file at run time rather than hardcoded here: devrc is public, and
more importantly a hardcoded recipient can silently drift from the key the
operator actually holds, producing archives nobody can open. Deriving it makes
"the thing we encrypt to" and "the thing we can decrypt with" the same fact.

🔴 EVERY EMPTY OUTCOME IS LOUD
------------------------------
A backup system that reports success having stored nothing is worse than no
backup system, because it is also a false all-clear. So:

  * a store directory that EXISTS but holds no scopes  -> failure
  * a scope whose repository holds zero commits        -> failure
  * a bundle that does not pass `git bundle verify`    -> failure, not uploaded
  * an upload whose read-back size/etag disagrees      -> failure
  * a run that backed up zero scopes                   -> failure

The single exception is an ABSENT store: a host that has never run
/analyze-service has nothing to lose, and failing there would make the timer a
permanently-red gate on the laptop (RULES.md: "a permanently-red gate is worse
than no gate"). That path prints its reason and exits 0, and it is the only one
that may.

Usage:
    backup.py [--store DIR] [--bucket B] [--keep N] [--no-upload] [--print-plan]

  --print-plan   pure text; reads the store, writes nothing anywhere
  --no-upload    bundle + verify + encrypt, then stop. WITHOUT --work-dir the
                 artifacts are built in a private temp dir that is DELETED on
                 exit, so nothing survives the run; WITH --work-dir they are left
                 there for inspection. Used for a read-only smoke run against the
                 real store.

🔴 THE PLAINTEXT BUNDLE IS A FULL, UNENCRYPTED COPY OF A CLIENT-CONFIDENTIAL
SCOPE. It exists only between `git bundle create` and `age`, and it is unlinked
on EVERY path out of that window including the failure paths. Everything this
script creates is mode 0600 inside a 0700 work directory — `PrivateTmp` contains
the default case under the unit, but the documented `--no-upload --work-dir X`
smoke run against the real store has no namespace around it at all.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

# 🔴 THE REPO-POINTER LEDGER IS NOT SPELLED IN THIS FILE, ON PURPOSE.
# `scripts/testlib/gitenv.py::REPO_POINTER_VARS` OWNS the set of environment
# variables that decide WHICH repository a git command lands in. `commit.sh`
# (the sibling program) has to re-spell it as a bash array because bash cannot
# import; this file is Python and can use the owner directly, which is strictly
# stronger — there is no copy to drift. claude/RULES.md → "One rule, one place".
#
# THE IMPORT IS A HARD FAILURE, NEVER A SILENT DEGRADE. A `try: … except
# ImportError: POINTERS = ()` fallback would turn a missing ledger into a backup
# that runs with the defect back in place and still reports success — the exact
# false all-clear this whole file is built against. The unit mounts
# `%h/workspace/devrc/scripts` read-only (nix/home.nix), so `testlib/` is on
# disk beside this script in every environment that runs it; if it ever is not,
# the run must stop and say so.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from testlib.gitenv import REPO_POINTER_VARS, strip_repo_pointers  # noqa: E402
except ImportError as _exc:  # pragma: no cover - a deployment fault, not a code path
    raise ImportError(
        f"analyze-service-index/backup.py cannot import the git repo-pointer "
        f"ledger from {_SCRIPTS_DIR / 'testlib' / 'gitenv.py'} ({_exc}). That "
        f"ledger is what stops a leaked GIT_DIR aiming this program — which has "
        f"a network and uploads what it bundles off-box — at a FOREIGN "
        f"repository. Refusing to run without it: a backup that silently drops "
        f"the strip would bundle and upload somebody else's history under a "
        f"green timer."
    ) from _exc

PROG = "analyze-service-index-backup"

DEFAULT_STORE = Path.home() / ".claude" / "analyze-service-index"
DEFAULT_BUCKET = "analyze-service-index-backups"
DEFAULT_KEEP = 14
DEFAULT_IDENTITY = Path.home() / "workspace" / "homelab-talos" / ".secrets" / "age.key"

# Everything created by this script: a 0700 work directory holding 0600 files.
# The bundle is plaintext history and the ciphertext is still a backup of
# confidential content; neither may be world- or group-readable, and the
# `--no-upload --work-dir X` smoke run has no PrivateTmp around it.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

# 🔴 THE READ-ONLY ALLOWLIST. Every git invocation in this file goes through
# `_git()`, which refuses a subcommand that is not in here. This is the guard
# that makes "the store is treated as read-only" a property of the code rather
# than of the author's care — a future edit that reaches for `git gc`, `git
# remote add` or `git config` fails at the call, not in review.
#
# It is pinned by the test suite as an EXACT SET, not a subset: widening it is a
# deliberate act that has to be argued for in a diff, and shrinking it below
# what the code needs breaks the tests that exercise those paths.
_READ_ONLY_SUBCOMMANDS = frozenset(
    {"bundle", "rev-list", "remote", "rev-parse", "for-each-ref"})

# 🔴 A VERB IS NOT GRANULAR ENOUGH, AND THE COMMENT ABOVE USED TO CLAIM IT WAS.
# `remote` and `bundle` are DISPATCHERS: the verb reads, the SUBVERB decides.
# MEASURED 2026-08-22 against the verb-only allowlist, on a synthetic scope:
#
#   git remote add exfil file:///tmp/x   -> rc=0, `.git/config` gained a
#                                           [remote "exfil"] section
#   git bundle unbundle <foreign.bundle> -> rc=0, a foreign packfile written
#                                           into the scope's object store
#
# Both are writes to the only copy of the data, both were allowed, and the
# allowlist comment above named `git remote add` as an example of what it
# refused. Neither has a call site today and the unit's BindReadOnlyPaths would
# have blocked them — but a guard narrower than its own description is the kind
# that gets trusted at exactly the moment it stops being true.
#
# So the allowlist is a (verb, subverb) pair set. The subverb is the first
# argument after the verb that is not an option, which is where git's own
# grammar puts it for both dispatchers. `None` means "the bare verb, options
# only": `git remote` LISTS remotes and is the form `scope_remotes()` uses,
# while `git bundle` with no subverb is a usage error and is not permitted.
# Pinned as an exact mapping by the test suite, same as the set above.
_ALLOWED_SUBVERBS: dict[str, frozenset] = {
    "bundle": frozenset({"create", "verify"}),
    "remote": frozenset({None}),
}


class BackupError(RuntimeError):
    """A failure that must make the whole run non-zero."""


def _private_dir(d: Path) -> Path:
    """Create (or tighten) `d` to 0700.

    `mkdir(mode=…)` is not enough: it is masked by the umask AND it does nothing
    at all when the directory already exists, which is the case every run after
    the first with an explicit `--work-dir`. The chmod is unconditional for that
    reason.
    """
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, _DIR_MODE)
    return d


# --------------------------------------------------------------------------- #
# git — read-only by construction
# --------------------------------------------------------------------------- #
# 🔴 PROCESS-GLOBAL, AND DELIBERATELY NEVER RESET. `_git_env()` is rebuilt for
# every git invocation, so without this the announcement below would print once
# per call and bury the backup's own output. The consequence for TESTS is the
# part worth naming: an in-process assertion on the announcement is
# ORDER-DEPENDENT — whichever test runs first consumes the one line for that
# name. So the behavioural pins on it (`test_the_leak_is_ANNOUNCED_…`,
# `test_the_announcement_is_ONCE_PER_NAME_…`) drive a SUBPROCESS, where the set
# starts empty by construction. A unit-level test must snapshot and restore it.
_POINTERS_ANNOUNCED: set[str] = set()


def _git_env() -> dict:
    """The environment every git invocation in this file runs under.

    Two independent jobs, and the docstring used to claim only one of them while
    naming neither correctly. It said this environment "makes git structurally
    incapable of writing to the repo" — a claim WIDER than the code: nothing here
    made git incapable of writing (the allowlist in `_git` and the unit's
    `BindReadOnlyPaths` do that), and nothing here said anything about WHICH
    repository git resolves, which was the half that was actually missing.

    1. WHICH REPOSITORY — `strip_repo_pointers`, and it is the load-bearing one.
       ------------------------------------------------------------------------
       🔴 `git -C <scope>` is the WEAKEST possible claim about where a command
       lands: **GIT_DIR OVERRIDES IT**. MEASURED 2026-08-23 (git 2.55.0) driving
       the pre-fix CLI end to end against a decoy repo, sole variable `GIT_DIR`,
       under the unit's own environment shape (`env -i` plus PATH/HOME):

           control (no leak)  rc=0  bundle head: <sha> refs/heads/trunk
           GIT_DIR=<decoy>    rc=0  bundle head: <sha> refs/heads/foreign-branch

       The run printed a successful backup of a repository it was never pointed
       at. This program has NO `PrivateNetwork`, `Wants=network-online.target`,
       and uploads what it bundles to MinIO — so where the committer's version of
       this defect (#721) corrupted local history, this one EXFILTRATES.

       🔴 AND THE RESTORE REHEARSAL IN `bundle_scope` CANNOT CATCH IT. Its
       `want_names` is read with `_git(scope, "for-each-ref")` — through the same
       poisoned environment — so both sides of the comparison report the decoy's
       refs and agree. That is a second sample of the unknown, not a control;
       nothing downstream of it may be cited as covering this.

       The SET is not chosen here — `testlib/gitenv.py::REPO_POINTER_VARS` owns
       it (see the import banner at the top of this file).

    2. WHICH CONFIG, AND NO PROMPTS — the four keys below.
       ------------------------------------------------------------------------
       These decide what git READS, not what it may write:

      GIT_OPTIONAL_LOCKS=0   DEFENCE IN DEPTH, and stated at the scope it was
                             measured. Some git reads (`git status` is the
                             standard example) refresh and REWRITE `.git/index`
                             as a side effect, which would move the store's
                             mtimes without changing a byte of content. MEASURED
                             2026-08-21 against a repo with a deliberately stale
                             index: none of the four subcommands in
                             `_READ_ONLY_SUBCOMMANDS` touches the index with this
                             set to 0 OR to 1, while `git status` in the same
                             probe DID rewrite it. So today this variable changes
                             nothing — it is here for the subcommand somebody
                             adds later, not because the current set needs it.
                             It is NOT what makes the store read-only; the
                             allowlist and the unit's BindReadOnlyPaths are.
      GIT_CONFIG_NOSYSTEM=1  no /etc/gitconfig
      GIT_CONFIG_GLOBAL      no ~/.gitconfig — so the operator's own config can
      GIT_CONFIG_SYSTEM      neither change what we produce nor be written to.
      GIT_TERMINAL_PROMPT=0  never block a timer-driven run on a prompt.

    🔴 EVERY git subprocess in this file takes its environment from HERE — `_git`
    and `_git_scratch` both, pinned by
    `test_every_git_invocation_takes_its_environment_from_git_env`. A future call
    site that builds its own `dict(os.environ)` reintroduces the defect above and
    that test is what fails.
    """
    env = dict(os.environ)
    # 🔴 THE STRIP. Its ORDER relative to the `update` below is NOT load-bearing
    # — the two sets are disjoint, and a mutation moving this call after the
    # update was swept and SURVIVED, correctly. What matters is that it runs at
    # all, and that it is handed `env` (the copy) rather than a throwaway: a
    # mutant passing `dict(env)` leaves every pointer in place with the call
    # still visibly there, and is killed by the parametrised regression test.
    stripped = strip_repo_pointers(env)
    _announce_stripped_pointers(stripped)
    env.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _announcing_program() -> str:
    """The program actually running, for the announcement's prefix.

    🔴 NOT `PROG`. `_git_env()` is SHARED: `restore-verify.py` imports this
    module and calls `_git_env()` / `_git_scratch` directly (it deliberately
    reuses them rather than copying the guards). Hardcoding `PROG` therefore
    announced a leak hit during a RESTORE VERIFICATION under the BACKUP's name,
    sending whoever read the journal to the wrong program and the wrong unit.
    `sys.argv[0]`'s basename distinguishes them without either program having to
    register anything.
    """
    try:
        name = Path(sys.argv[0]).name
    except (IndexError, TypeError, ValueError):  # pragma: no cover - argv is a list
        return PROG
    return name or PROG


def _announce_stripped_pointers(stripped: dict) -> None:
    """Say ONCE per variable that the caller's environment was aimed elsewhere.

    Silence would be the wrong shape for this file: a run whose environment
    carried `GIT_DIR=<somebody else's repo>` is a run whose *caller* is broken,
    and the strip fixes this program's behaviour without fixing theirs. Printing
    it makes the leak visible in the journal instead of only in a diff nobody
    reads. Once per name, because `_git_env()` is rebuilt for every invocation
    and a per-call line would bury the run's own output — see the note on
    `_POINTERS_ANNOUNCED` for what that costs a test.
    """
    for name in sorted(stripped):
        if name in _POINTERS_ANNOUNCED:
            continue
        _POINTERS_ANNOUNCED.add(name)
        print(
            f"{_announcing_program()}: STRIPPED {name}={stripped[name]!r} from "
            f"the git environment. It decides WHICH repository a git command "
            f"lands in and it OVERRIDES `git -C`; inherited here it would have "
            f"aimed this program at that repository. Fix the caller — nothing "
            f"in normal operation sets it.",
            file=sys.stderr,
        )


def _subverb(args: tuple[str, ...]) -> str | None:
    """The dispatcher subverb in `args`, or None for a bare verb.

    git's grammar for both dispatchers this script allowlists puts the subverb
    first among the non-option arguments (`git bundle create -q <file> …`,
    `git remote -v`), so the first argument after the verb that does not start
    with `-` IS the subverb. Returning None for `git remote` is meaningful, not
    a miss: that form lists remotes and is the one `scope_remotes()` uses.
    """
    for a in args[1:]:
        if not a.startswith("-"):
            return a
    return None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run one read-only git subcommand against `repo`.

    Refuses anything outside `_READ_ONLY_SUBCOMMANDS`. `gc.auto=0` stops git
    from deciding, mid-read, that this repository is due a repack — which is a
    write, would be perfectly reasonable behaviour, and would silently break the
    read-only guarantee this whole file rests on. `core.hooksPath=/dev/null`
    stops a hook in the store from running under the backup's privileges.
    """
    if not args or args[0] not in _READ_ONLY_SUBCOMMANDS:
        raise BackupError(
            f"refusing git subcommand {args[0] if args else '(none)'!r}: the "
            f"/analyze-service index store is READ-ONLY to this script. "
            f"Allowed: {sorted(_READ_ONLY_SUBCOMMANDS)}. If a new subcommand is "
            f"genuinely needed, prove it cannot write and add it to "
            f"_READ_ONLY_SUBCOMMANDS in the same commit as the test that pins it."
        )
    verb = args[0]
    if verb in _ALLOWED_SUBVERBS:
        sub = _subverb(args)
        if sub not in _ALLOWED_SUBVERBS[verb]:
            raise BackupError(
                f"refusing `git {verb} {sub}`: {sub!r} is not a READ-ONLY mode of "
                f"`git {verb}`. The verb is allowlisted; its subverbs are not all "
                f"reads — `git remote add` writes .git/config and `git bundle "
                f"unbundle` writes objects into the repository, both against the "
                f"only copy of the data. Allowed here: "
                f"{sorted(s for s in _ALLOWED_SUBVERBS[verb] if s is not None)}"
                f"{' (or the bare verb)' if None in _ALLOWED_SUBVERBS[verb] else ''}."
            )
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "core.hooksPath=" + os.devnull, *args],
        capture_output=True, text=True, env=_git_env(),
    )


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def discover_scopes(store: Path) -> list[Path]:
    """Every scope repository directly under `store`, sorted by name.

    A scope is a real directory (never a symlink — `commit.sh` refuses those for
    the same reason, and following one here would bundle a repository from
    outside the store) that contains a `.git`.
    """
    out: list[Path] = []
    for child in sorted(store.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_symlink() or not child.is_dir():
            continue
        if (child / ".git").exists():
            out.append(child)
    return out


def commit_count(scope: Path) -> int:
    p = _git(scope, "rev-list", "--count", "--all")
    if p.returncode != 0:
        raise BackupError(
            f"{scope.name}: could not count commits (git rev-list rc="
            f"{p.returncode}): {p.stderr.strip()}. A scope whose history cannot "
            f"be READ must never be reported as a scope that is EMPTY."
        )
    return int(p.stdout.strip() or "0")


def scope_remotes(scope: Path) -> list[str]:
    """🔴 TEST-ONLY. THERE IS NO PRODUCTION CALL SITE, AND THAT IS DELIBERATE.

    Nothing on the run path calls this. It exists so the test suite can assert
    the no-remote invariant from OUTSIDE the code under test
    (`test_no_scope_gains_a_remote`), and its dead-code status is pinned by
    `test_scope_remotes_has_no_production_call_site` so this docstring cannot
    quietly become false in either direction.

    Say so here because the surrounding module docstring talks at length about
    "WHY NO REMOTE", and a reader skimming for the control that ENFORCES it will
    land on this function. It enforces nothing. What actually holds the invariant
    on the run path is `_git`'s (verb, subverb) allowlist — `git remote add` is
    refused there — plus the unit's `BindReadOnlyPaths`. A post-hoc check here
    would in any case be an unreachable guard: this program has no code that
    could add a remote, so a run-path assertion that none appeared could never
    fail, and claude/RULES.md rates a guard that cannot fail as worse than none
    because it reads as coverage while providing none.
    """
    p = _git(scope, "remote")
    return [ln for ln in p.stdout.split("\n") if ln.strip()]


# --------------------------------------------------------------------------- #
# age
# --------------------------------------------------------------------------- #
# 🔴 THE RESOLUTION ORDER LIVES HERE ONCE, as data. `resolve_identity()`
# delegates to `resolve_identity_with_source()` rather than repeating the loop:
# a second copy of this order is a second thing to get wrong, and the two would
# drift silently because both return a plausible path.
IDENTITY_ENV_VARS = ("ASIB_AGE_IDENTITY", "SOPS_AGE_KEY_FILE")

# What chose the identity, when nothing overrode it. A sentinel STRING rather
# than None so every consumer formats one type, and so a message can never read
# "chosen by None".
IDENTITY_SOURCE_DEFAULT = "the built-in default"


def resolve_identity_with_source() -> tuple[Path, str]:
    """The operator's age identity file, AND what chose it.

    Order: ASIB_AGE_IDENTITY -> SOPS_AGE_KEY_FILE (the handle the homelab repos
    already use, per SECRETS.md) -> the default homelab-talos path.

    🔴 THE SECOND ELEMENT IS NOT COSMETIC. `SOPS_AGE_KEY_FILE` is exported by
    unrelated client work, and every age identity file is the SAME SIZE (fixed
    -width `# created:`, `# public key:` and key lines), so a comparison against
    the wrong one fails with two equal byte counts and looks exactly like a
    damaged escrow. A consumer that reports only the PATH leaves the operator
    unable to tell "your escrow is corrupt" from "you compared the wrong file" —
    and the remedies are opposites. MEASURED 2026-08-25: that is precisely what
    happened, and the advice a path-only message gave would have overwritten a
    good escrow with an unrelated key.
    """
    for var in IDENTITY_ENV_VARS:
        v = os.environ.get(var)
        if v:
            return Path(v), f"${var}"
    return DEFAULT_IDENTITY, IDENTITY_SOURCE_DEFAULT


def resolve_identity() -> Path:
    """Path only. See `resolve_identity_with_source()` for the order."""
    return resolve_identity_with_source()[0]


def resolve_recipient(identity: Path) -> str:
    """The age recipient to encrypt to, DERIVED from the identity we can decrypt with.

    `ASIB_AGE_RECIPIENT` overrides — but only to a public key, and it is still
    checked for shape, because a typo here produces archives that encrypt fine
    and decrypt never.
    """
    override = os.environ.get("ASIB_AGE_RECIPIENT")
    if override:
        if not re.fullmatch(r"age1[0-9a-z]{58}", override.strip()):
            raise BackupError(
                f"ASIB_AGE_RECIPIENT={override!r} is not a well-formed age public "
                f"key (expected age1 + 58 base32 chars). Refusing to encrypt to "
                f"it: a backup encrypted to a malformed recipient is a backup "
                f"that cannot be restored, and nothing downstream would notice."
            )
        return override.strip()

    if not identity.is_file():
        raise BackupError(
            f"no age identity at {identity}. This backup encrypts to the "
            f"operator's EXISTING SOPS age key; it deliberately does not mint a "
            f"new one, because a key nobody keeps alive is a backup nobody can "
            f"open. Set ASIB_AGE_IDENTITY or SOPS_AGE_KEY_FILE to the key file "
            f"(see SECRETS.md)."
        )
    if shutil.which("age-keygen") is None:
        raise BackupError(
            "age-keygen is not on PATH — it is declared in nix/pkgs/default.nix "
            "and set explicitly on the systemd unit's PATH; add it there rather "
            "than working around it."
        )
    p = subprocess.run(["age-keygen", "-y", str(identity)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise BackupError(
            f"could not derive an age recipient from {identity} (rc="
            f"{p.returncode}): {p.stderr.strip()}"
        )
    recipient = p.stdout.strip()
    if not recipient.startswith("age1"):
        raise BackupError(
            f"age-keygen -y on {identity} produced {recipient!r}, which is not an "
            f"age public key. Refusing to encrypt to it."
        )
    return recipient


def encrypt(plain: Path, cipher: Path, recipient: str) -> None:
    if shutil.which("age") is None:
        raise BackupError(
            "age is not on PATH — it is declared in nix/pkgs/default.nix and set "
            "explicitly on the systemd unit's PATH; add it there rather than "
            "working around it. Uploading the plaintext bundle instead is NOT an "
            "acceptable fallback and this script will not do it."
        )
    p = subprocess.run(
        ["age", "--encrypt", "--recipient", recipient, "--output", str(cipher), str(plain)],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise BackupError(f"age encryption of {plain.name} failed (rc={p.returncode}): "
                          f"{p.stderr.strip()}")
    if not cipher.is_file() or cipher.stat().st_size == 0:
        raise BackupError(
            f"age reported success but {cipher} is missing or empty. An empty "
            f"ciphertext uploads happily and restores never."
        )
    # Ciphertext, but still a backup of client-confidential content sitting on a
    # real filesystem under `--no-upload --work-dir X`. age creates it 0644.
    os.chmod(cipher, _FILE_MODE)


# --------------------------------------------------------------------------- #
# bundling
# --------------------------------------------------------------------------- #
def _git_scratch(cwd: Path, *args: str, forbidden: Path) -> subprocess.CompletedProcess:
    """git in a THROWAWAY directory — never the store.

    The allowlist on `_git` exists because that function is pointed at the only
    copy of the data. This one is pointed at a directory we created moments ago
    and are about to delete, so it may run writing subcommands (`clone`).

    🔴 `forbidden` IS THE REFUSAL, AND IT IS REQUIRED AND KEYWORD-ONLY. An
    earlier version of this docstring claimed the function "refuses to run
    anywhere near the store" and no such check existed — a comment describing a
    guard that is not there is worse than no guard, because it stops the next
    person looking. Making the parameter mandatory is what makes the refusal
    structural: a later call site cannot inherit it by forgetting it, because
    omitting it is a TypeError at the call rather than a silently unguarded
    write. Pass the STORE ROOT; `cwd` may be neither it nor anything under it.
    """
    c = Path(cwd).resolve()
    f = Path(forbidden).resolve()
    if c == f or f in c.parents:
        raise BackupError(
            f"refusing to run `git {' '.join(args[:2])}` with cwd {c}: that is "
            f"inside the /analyze-service index store ({f}). `_git_scratch` may "
            f"run WRITING subcommands, so the only thing keeping it safe is "
            f"where it is aimed — and aiming it at the store would put a "
            f"writing git inside the only copy of the data this script exists "
            f"to protect."
        )
    return subprocess.run(
        ["git", "-c", "gc.auto=0", "-c", "core.hooksPath=" + os.devnull,
         "-c", "protocol.file.allow=always", "-C", str(cwd), *args],
        capture_output=True, text=True, env=_git_env(),
    )


def _refs(run: subprocess.CompletedProcess) -> set[str]:
    """`{"<refname> <objectname>"}` from a `for-each-ref` in that format."""
    return {ln.strip() for ln in run.stdout.splitlines() if ln.strip()}




def bundle_scope(scope: Path, out: Path, work_dir: Path) -> None:
    """`git bundle create --all`, then PROVE the bundle restores, before upload.

    🔴 `git bundle verify` IS NOT AN INTEGRITY CHECK, AND READS EXACTLY LIKE ONE.
    Measured 2026-08-21, git 2.x, on a 504-byte bundle with a single byte flipped
    in the middle of the packfile:

        git bundle verify <corrupted>  ->  rc=0
        "<oid> HEAD"
        "The bundle records a complete history."

    It validates the bundle HEADER and the PREREQUISITES. It does not walk the
    pack, so bit rot, a truncated write or a partial copy all pass it — and it
    says "complete history" while doing so. A backup pipeline that stops there
    uploads corrupt archives under a green verdict, which is worse than not
    checking at all: it manufactures confidence. The same byte flip fails a
    clone at rc=128, `error: index-pack died`.

    So the real check is a RESTORE REHEARSAL: clone the bundle into a throwaway
    bare repository — which forces `index-pack` to validate every object's hash
    and the pack checksum — and then compare the restored ref set and commit
    count against the source. That is the same standard applied to the upload
    read-back, and for the same reason: the only evidence a backup is restorable
    is having restored it.

    `bundle verify` is kept in front of it because it is cheap and its message
    for a genuine prerequisite problem is clearer than a clone failure's.
    """
    # Own the scratch directory here too. Without this the rehearsal clone
    # failed with "cannot change to <work_dir>: No such file or directory" and
    # was reported as "the bundle could NOT BE RESTORED FROM" — a guard blaming
    # data corruption for a missing directory, which is a message that names the
    # wrong cause at the exact moment someone is deciding whether their backups
    # are intact.
    _private_dir(work_dir)
    store = scope.parent

    p = _git(scope, "bundle", "create", str(out), "--all")
    if p.returncode != 0:
        raise BackupError(
            f"{scope.name}: `git bundle create` failed (rc={p.returncode}): "
            f"{p.stderr.strip()}"
        )
    if not out.is_file() or out.stat().st_size == 0:
        raise BackupError(f"{scope.name}: bundle {out} is missing or empty after create")
    # Plaintext history of a client-confidential scope, on a real filesystem
    # under the documented `--no-upload --work-dir X` smoke run. git creates it
    # 0644 under the operator's umask.
    os.chmod(out, _FILE_MODE)

    v = _git(scope, "bundle", "verify", str(out))
    if v.returncode != 0:
        raise BackupError(
            f"{scope.name}: `git bundle verify` REJECTED the bundle (rc="
            f"{v.returncode}): {v.stderr.strip() or v.stdout.strip()}. Not "
            f"uploading it — an unverified bundle is a claim, not a backup."
        )

    rehearsal = work_dir / f".rehearse-{scope.name}.git"
    if rehearsal.exists():
        shutil.rmtree(rehearsal)
    try:
        # 🔴 `--mirror`, NOT `--bare`. MEASURED 2026-08-22 on a scope carrying
        # `refs/notes/commits` and `refs/weird/thing` beside `refs/heads/trunk`,
        # from a bundle created with `--all` that declared all three:
        #
        #   git clone --bare   <bundle>  ->  restored refs/heads/trunk ONLY
        #   git clone --mirror <bundle>  ->  restored all three
        #
        # `--bare` uses the default `+refs/heads/*:refs/heads/*` refspec and
        # SILENTLY DROPS everything else. Two consequences, both bad. It is not
        # a restore rehearsal at all for those refs — the bundle holds them and
        # the rehearsal never checks they come back. And because the comparison
        # below is against what the bundle DECLARES, a scope that ever gained a
        # notes ref would fail its backup on every run, forever: a
        # permanently-red gate on a disaster-recovery job. `--mirror` restores
        # `+refs/*:refs/*`, which is what "this bundle can be restored from"
        # has to mean for a backup.
        c = _git_scratch(work_dir, "clone", "--mirror", "--quiet",
                         str(out), str(rehearsal), forbidden=store)
        if c.returncode != 0:
            raise BackupError(
                f"{scope.name}: the bundle passed `git bundle verify` but could "
                f"NOT BE RESTORED FROM (clone rc={c.returncode}): "
                f"{c.stderr.strip()}. `bundle verify` does not walk the packfile, "
                f"so this is what a corrupted or truncated bundle looks like. Not "
                f"uploading it."
            )

        fmt = "--format=%(refname) %(objectname)"
        got = _refs(_git_scratch(rehearsal, "for-each-ref", fmt, forbidden=store))

        # 🔴 COMPLETENESS BY REF **NAME**, NOT BY (name, objectname) PAIR — AND
        # THE DIFFERENCE IS A DATA-LOSS BUG, NOT A STYLE CHOICE.
        #
        # An earlier version compared the pairs. Nothing locks the scope against
        # the hourly `analyze-service-index-commit` while this runs, and the
        # window being compared across contains a FULL CLONE of the scope — so
        # one ordinary commit landing in it moved `refs/heads/trunk`, the pair
        # comparison mismatched, and the run reported "the bundle restores to a
        # DIFFERENT set of refs". The scope lost that day's backup and the
        # operator was told their archive was corrupt while it cloned cleanly.
        # REPRODUCED 2026-08-22 with a single commit injected between
        # `bundle create` and the comparison.
        #
        # Names do not race, because `commit.sh` only ever MOVES an existing
        # branch: it never creates or deletes a ref (it has no code that does,
        # and no network to fetch one). Which of two adjacent commits the
        # snapshot captured is not a property a backup needs to have; which REFS
        # it captured is.
        #
        # What still pins the objects: the `--mirror` clone above runs
        # `index-pack`, which validates every object's hash and the pack
        # checksum, so a bundle whose bytes do not reconstruct cannot reach this
        # point at all.
        #
        # 🔴 AN OBJECT-LEVEL COMPARISON AGAINST THE BUNDLE'S OWN HEADER WAS
        # TRIED HERE AND REMOVED. `git bundle list-heads` gives a race-free
        # reference, and comparing the restore against it looked like a
        # strictly-better check. A mutation sweep scored it SURVIVED: under a
        # `--mirror` clone git writes exactly the refs the header declares, so
        # no input can make the two disagree while the clone succeeds. Same
        # verdict, and the same reason, as the commit-count comparison noted
        # below — a guard that cannot fail reads as coverage while providing
        # none, which is worse than not being there.
        want_names = {ln.split(" ", 1)[0]
                      for ln in _refs(_git(scope, "for-each-ref", fmt))}
        got_names = {ln.split(" ", 1)[0] for ln in got}
        if got_names != want_names:
            raise BackupError(
                f"{scope.name}: the bundle restores to a DIFFERENT set of refs "
                f"than the scope holds. Missing from the restore: "
                f"{sorted(want_names - got_names)}; unexpected in the restore: "
                f"{sorted(got_names - want_names)}. Not uploading it."
            )

        # NOTE: there is deliberately NO separate commit-count comparison here
        # either — the second guard removed from this function for being
        # unreachable, and for the same measured reason. The `--mirror` clone
        # validates every object, so a count against the RESTORE cannot differ
        # from the bundle; and a count against the LIVE SCOPE would be worse
        # than merely unreachable, because it is exactly the race described
        # above, reintroduced under a different name.
    finally:
        shutil.rmtree(rehearsal, ignore_errors=True)


# --------------------------------------------------------------------------- #
# upload boundary
# --------------------------------------------------------------------------- #
def resolve_kubeconfig() -> Path | None:
    """Which kubeconfig reaches the homelab from THIS host.

    🔴 The two hosts reach the same cluster by different files, and both stores
    need backing up: the workbench has the checked-out
    `homelab-talos/homelab-kubeconfig`, the laptop reaches it over nebula via
    `~/.kube/homelab-nebula.yaml` (SECRETS.md, `$KC_NEBULA`). Hardcoding the
    workbench's path in the unit would leave the laptop's — DIVERGENT, equally
    unrecoverable — store with no off-machine copy while the workbench looked
    healthy. That is the exact silent gap this whole change exists to close, so
    the resolution is done here where it can adapt, not in the unit where it
    cannot.

    An already-set KUBECONFIG always wins: the operator and the unit both set it
    deliberately.
    """
    existing = os.environ.get("KUBECONFIG")
    if existing and Path(existing).is_file():
        return Path(existing)
    for candidate in (
        Path.home() / "workspace" / "homelab-talos" / "homelab-kubeconfig",
        Path.home() / ".kube" / "homelab-nebula.yaml",
    ):
        if candidate.is_file():
            os.environ["KUBECONFIG"] = str(candidate)
            return candidate
    return None


class MinioUploader:
    """The real object store: the homelab `minio-archive` tenant.

    🔴 Deliberately a THIN wrapper over `scripts/mail-actions/_minio.py` rather
    than a second implementation. That module already owns how this host reaches
    the tenant — the `kubectl port-forward` to `svc/minio` in namespace
    `minio-archive`, the credential read from the `minio-archive-config` secret's
    `config.env`, the path-style client — and a second convention for the same
    endpoint is a defect: it is one more thing to rotate, and the copy that
    nobody exercises is the one that is broken when it is needed.

    stat/list/remove are not on `MinioArchive`'s surface, so they go through its
    `.client`. That reuses the connection, credentials and port-forward while
    leaving another subsystem's file untouched.
    """

    def __init__(self, bucket: str):
        self.bucket = bucket
        self._ctx = None
        self._mc = None

    def __enter__(self):
        if resolve_kubeconfig() is None and not os.environ.get("MINIO_ARCHIVE_ENDPOINT"):
            raise BackupError(
                "no kubeconfig reaches the homelab from this host (looked at "
                "KUBECONFIG, ~/workspace/homelab-talos/homelab-kubeconfig and "
                "~/.kube/homelab-nebula.yaml) and MINIO_ARCHIVE_ENDPOINT is not "
                "set. Refusing to continue: without a route to the tenant there "
                "is nowhere to put the backup, and a run that discovers that "
                "AFTER reporting success is the false all-clear this script "
                "exists to prevent."
            )
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mail-actions"))
        from _minio import MinioArchive  # noqa: E402  (lazy: needs the minio pkg)

        self._ctx = MinioArchive()
        self._mc = self._ctx.__enter__()
        # 🔴 `__exit__` is NOT called when `__enter__` raises, so anything after
        # the inner enter must clean up after itself or the `kubectl
        # port-forward` child outlives the run. `ensure_bucket` is a live call
        # to the tenant and can absolutely raise (S3Error, a dead forward, a
        # credential that no longer works) — leaving a forwarder holding a local
        # port until the unit's TimeoutStartSec kills the cgroup.
        try:
            self._mc.ensure_bucket(self.bucket)
        except BaseException:
            self._ctx.__exit__(*sys.exc_info())
            self._ctx = None
            self._mc = None
            raise
        return self

    def __exit__(self, *exc):
        if self._ctx is not None:
            self._ctx.__exit__(*exc)

    def put(self, key: str, data: bytes) -> None:
        self._mc.put_object(self.bucket, key, data, "application/octet-stream")

    def stat(self, key: str) -> tuple[int, str]:
        st = self._mc.client.stat_object(self.bucket, key)
        return int(st.size), str(st.etag or "").strip('"')

    def list(self, prefix: str) -> list[str]:
        return [o.object_name for o in
                self._mc.client.list_objects(self.bucket, prefix=prefix, recursive=True)]

    def remove(self, key: str) -> None:
        self._mc.client.remove_object(self.bucket, key)


def upload_and_verify(uploader, key: str, data: bytes) -> None:
    """Upload, then READ BACK and compare. A 200 is not evidence the bytes landed.

    🔴 The read-back is the whole point of this function. `put_object` returning
    without raising says the client was satisfied; it says nothing about what is
    now in the bucket. We compare the size always, and the etag whenever it looks
    like a plain single-part MD5 (32 hex chars, no `-N` multipart suffix) —
    a multipart etag is a digest of digests and is NOT comparable to the
    object's MD5, so treating it as one would either fail every large upload or,
    worse, be "fixed" later by dropping the check.
    """
    uploader.put(key, data)
    try:
        size, etag = uploader.stat(key)
    except Exception as exc:  # noqa: BLE001 — any read-back failure is a failed backup
        raise BackupError(
            f"upload of {key} reported success but the object could not be read "
            f"back ({exc.__class__.__name__}: {exc}). Treating it as a FAILED "
            f"backup: an upload nobody confirmed is indistinguishable from one "
            f"that never happened."
        ) from exc

    if size != len(data):
        raise BackupError(
            f"upload of {key} did not land intact: sent {len(data)} bytes, the "
            f"store reports {size}."
        )
    if re.fullmatch(r"[0-9a-f]{32}", etag):
        local = hashlib.md5(data).hexdigest()  # noqa: S324 — S3 etag, not a security digest
        if etag != local:
            raise BackupError(
                f"upload of {key} landed with the wrong content: etag {etag} != "
                f"local md5 {local}."
            )


# --------------------------------------------------------------------------- #
# retention
# --------------------------------------------------------------------------- #
def prune(uploader, prefix: str, keep: int, just_uploaded: str) -> list[str]:
    """Keep the newest `keep` objects under `prefix`; delete the rest.

    Keys embed a UTC timestamp in a fixed-width sortable form, so lexical order
    IS chronological order and no per-object stat is needed.

    🔴 Three refusals, all of which are the difference between retention and
    data loss.

    MEMBERSHIP. If the listing does not contain the object we just uploaded, the
    view we are about to prune from is not the bucket we just wrote to — a wrong
    prefix, a stale list, a different bucket — and deleting from it would remove
    real backups on the strength of a listing we have already caught being
    wrong.

    🔴 SURVIVAL, WHICH IS NOT THE SAME CLAIM AND IS THE ONE THAT MATTERS.
    Membership only says the new object is *somewhere* in the listing; it says
    nothing about whether it is in the set about to be DELETED. Lexical order is
    chronological order only while the clock moves forward, and it does not
    always: an NTP correction, or a laptop RTC after a suspend, steps it
    BACKWARDS. Today's key then sorts OLDEST, and every subsequent run uploads,
    verifies, prints "verified", and immediately deletes its own upload —
    membership satisfied throughout. The bucket silently stops advancing while
    the timer stays green and the log says the backup succeeded, which is the
    exact false all-clear this file exists to prevent. Deleting what we just
    wrote is never the correct outcome of a retention pass, whatever the clock
    says, so it is refused outright rather than reordered around.

    KEEP. `keep` below 1 is refused: a retention policy that keeps nothing is a
    delete-everything policy wearing a retention policy's name.
    """
    if keep < 1:
        raise BackupError(f"--keep must be at least 1, got {keep}: a retention "
                          f"policy that keeps zero backups is not a retention policy.")
    keys = sorted(uploader.list(prefix))
    if just_uploaded not in keys:
        raise BackupError(
            f"refusing to prune {prefix}: the object just uploaded "
            f"({just_uploaded}) is not in the listing of {len(keys)} object(s). "
            f"The listing does not describe the bucket that was just written to, "
            f"so pruning from it could delete real backups."
        )
    doomed = keys[:-keep] if len(keys) > keep else []
    if just_uploaded in doomed:
        raise BackupError(
            f"refusing to prune {prefix}: retention would DELETE THE OBJECT "
            f"THIS RUN JUST UPLOADED ({just_uploaded}). Its key sorts among the "
            f"{len(doomed)} oldest of {len(keys)}, which means this host's clock "
            f"has moved backwards relative to the existing backups (an NTP "
            f"correction, or an RTC restored after suspend). Pruning here would "
            f"leave the run reporting a verified backup that no longer exists, "
            f"and would do so on every future run — a bucket that silently "
            f"stops advancing behind a green timer. Fix the clock; nothing has "
            f"been deleted."
        )
    for k in doomed:
        uploader.remove(k)
    return doomed


# --------------------------------------------------------------------------- #
# naming
# --------------------------------------------------------------------------- #
def host_label() -> str:
    """Which machine this store came from.

    🔴 Both machines are hostname `nixos` (see MEMORY.md / SECRETS.md), and the
    two stores are DIVERGENT content. Without a distinct label per host they
    would share a key prefix and silently evict each other under retention —
    turning the backup into a second way to lose the data.
    """
    for var in ("ASIB_HOST", "ACTIVITY_HOST"):
        v = os.environ.get(var)
        if v and v.strip():
            return re.sub(r"[^A-Za-z0-9._-]", "-", v.strip())
    return re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname() or "unknown")


def object_key(host: str, scope: str, when: datetime) -> str:
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    return f"{host}/{scope}/{stamp}.bundle.age"


def scope_prefix(host: str, scope: str) -> str:
    return f"{host}/{scope}/"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run(store: Path, *, bucket: str, keep: int, upload: bool,
        work_dir: Path, uploader_factory=None) -> int:
    """Back up every scope. Returns the number of scopes backed up (never 0)."""
    if not store.exists():
        # The ONLY empty outcome permitted to be a success. See module docstring.
        print(f"{PROG}: no store at {store} — nothing to back up", file=sys.stderr)
        return -1

    if not store.is_dir():
        raise BackupError(f"{store} exists but is not a directory")

    scopes = discover_scopes(store)
    if not scopes:
        raise BackupError(
            f"{store} EXISTS but contains no scope repositories. That is not an "
            f"empty backup, it is an unexplained one: the directory being there "
            f"means /analyze-service has run on this host, so a store with "
            f"nothing in it means the scopes were removed, not that there was "
            f"never anything to save. Refusing to report a successful backup of "
            f"nothing."
        )

    # `run()` owns its scratch directory. It used to rely on the caller having
    # made it, which meant `git bundle create` failed with "Unable to create
    # …/x.bundle.lock: No such file or directory" — a message that names the
    # bundle and points at git, for a fault that is neither. 0700 because a
    # plaintext bundle and a rehearsal clone of a client-confidential scope both
    # live here, and `--work-dir` may be an ordinary directory on a real disk.
    _private_dir(work_dir)

    identity = resolve_identity()
    recipient = resolve_recipient(identity)
    host = host_label()
    when = datetime.now(timezone.utc)

    ctx = None
    uploader = None
    if upload:
        factory = uploader_factory or (lambda: MinioUploader(bucket))
        ctx = factory()
        uploader = ctx.__enter__()

    done = 0
    failures: list[str] = []
    try:
        for scope in scopes:
            try:
                n = commit_count(scope)
                if n == 0:
                    raise BackupError(
                        f"{scope.name}: the scope repository has ZERO commits. "
                        f"`git bundle create --all` cannot make a bundle from it, "
                        f"and a scope skipped quietly here is a scope with no "
                        f"off-machine copy that the run would still call a "
                        f"success. Either the hourly autocommit has not run since "
                        f"the scope was created, or its history was destroyed — "
                        f"both need a human."
                    )

                plain = work_dir / f"{scope.name}.bundle"
                cipher = work_dir / f"{scope.name}.bundle.age"
                try:
                    bundle_scope(scope, plain, work_dir)
                    encrypt(plain, cipher, recipient)
                finally:
                    # 🔴 THE PLAINTEXT BUNDLE DIES HERE, ON EVERY PATH. It is a
                    # complete unencrypted copy of a client-confidential scope,
                    # and the whole design rests on the content being encrypted
                    # BEFORE it exists anywhere durable. `finally`, not the
                    # success path: a failed encryption is exactly when a
                    # plaintext copy is most likely to be left behind and least
                    # likely to be noticed. PrivateTmp contains the default case
                    # under the unit; the documented `--no-upload --work-dir X`
                    # smoke run against the real store has no namespace at all.
                    plain.unlink(missing_ok=True)
                data = cipher.read_bytes()

                if uploader is not None:
                    key = object_key(host, scope.name, when)
                    upload_and_verify(uploader, key, data)
                    pruned = prune(uploader, scope_prefix(host, scope.name), keep, key)
                    print(f"{PROG}: {scope.name}: {n} commits -> {len(data)} bytes "
                          f"-> {key} (verified; pruned {len(pruned)})")
                else:
                    print(f"{PROG}: {scope.name}: {n} commits -> {cipher} "
                          f"({len(data)} bytes, verified, NOT uploaded)")
                done += 1
            except Exception as exc:  # noqa: BLE001 — see below; this is the point
                # One scope failing must not stop the others being backed up —
                # but it MUST make the run fail. A partial backup reported as a
                # success is the false all-clear this file exists to prevent.
                #
                # 🔴 `Exception`, NOT `BackupError`. This handler used to catch
                # only the latter, and the comment above claimed isolation the
                # code did not provide: `uploader.put/list/remove` go through
                # the minio client, which raises `S3Error` and urllib3
                # connection errors — neither a BackupError. One scope hitting a
                # dead port-forward therefore escaped this handler AND `run()`
                # entirely, abandoning every scope after it in the alphabet and
                # handing the operator a bare traceback. The scopes are
                # independent by construction; nothing about scope N failing
                # says anything about scope N+1, so the isolation has to be as
                # wide as the failures that can actually arrive.
                #
                # The class name is recorded because for a non-BackupError the
                # type IS most of the diagnosis (`S3Error` vs `TimeoutError` vs
                # `FileNotFoundError` are three different faults), and str() on
                # them is frequently uninformative.
                label = (str(exc) if isinstance(exc, BackupError)
                         else f"{type(exc).__name__}: {exc}")
                failures.append(label)
                print(f"{PROG}: FAILED {scope.name}: {label}", file=sys.stderr)
                if not isinstance(exc, BackupError):
                    traceback.print_exc()
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)

    if failures:
        raise BackupError(
            f"{len(failures)} of {len(scopes)} scope(s) failed to back up; "
            f"{done} succeeded. First failure: {failures[0]}"
        )
    if done == 0:
        raise BackupError(
            f"backed up 0 of {len(scopes)} scope(s) without recording a failure. "
            f"This is a bug in the backup script itself; refusing to exit 0."
        )
    return done


def print_plan(store: Path, bucket: str, keep: int) -> None:
    """Pure text. Reads the store; writes nothing, anywhere, ever."""
    print(f"store:     {store}")
    print(f"bucket:    {bucket}")
    print(f"keep:      {keep} per scope")
    print(f"host:      {host_label()}")
    print("remote:    NONE — no git remote is added to any scope, ever")
    print("encrypt:   age, to the operator's existing SOPS identity, BEFORE upload")
    # Written from what the code DOES, not from what the design intended. An
    # earlier version of this line said "git bundle verify before upload", which
    # was true and badly incomplete: `bundle verify` passes a byte-flipped
    # bundle at rc=0, so it is the restore rehearsal that carries the claim.
    print("verify:    bundle verify, then a RESTORE REHEARSAL (bare clone + ref")
    print("           comparison) — `git bundle verify` alone passes a corrupted")
    print("           bundle at rc=0; then a read-back of size/etag after upload")
    # 🔴 Every check above is UPSTREAM of encryption and upload. Say so, and say
    # what closes the other half — a reader deciding whether these backups are
    # trustworthy stops at this output, and "verified" here means "the bundle I
    # built restores", never "the object in the bucket does".
    print("after:     everything above happens BEFORE encryption and upload.")
    print("           The artifact AS STORED is verified by a separate run of")
    print("           scripts/analyze-service-index/restore-verify.py")
    identity = resolve_identity()
    print(f"identity:  {identity} ({'present' if identity.is_file() else 'MISSING'})")
    if not store.exists():
        print(f"scope:     (no store at {store})")
        return
    scopes = discover_scopes(store)
    if not scopes:
        print(f"scope:     (none found under {store}) — this would FAIL the run")
        return
    for s in scopes:
        try:
            n = commit_count(s)
        except BackupError:
            n = -1
        note = " — ZERO COMMITS, would FAIL the run" if n == 0 else ""
        print(f"scope:     {s.name} ({n} commits){note}")
        print(f"  key:     {object_key(host_label(), s.name, datetime.now(timezone.utc))}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__)
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--bucket", default=os.environ.get("ASIB_BUCKET", DEFAULT_BUCKET))
    ap.add_argument("--keep", type=int, default=int(os.environ.get("ASIB_KEEP", DEFAULT_KEEP)))
    ap.add_argument("--no-upload", action="store_true",
                    help="bundle+verify+encrypt only, no upload. WITH --work-dir "
                         "the encrypted artifact is left there; WITHOUT it, "
                         "everything is built in a private temp dir that is "
                         "deleted on exit and nothing survives the run. The "
                         "plaintext bundle is unlinked either way.")
    ap.add_argument("--work-dir", type=Path, default=None,
                    help="where to build artifacts, created 0700 (default: a "
                         "private temp dir, deleted on exit)")
    ap.add_argument("--print-plan", action="store_true",
                    help="print what would happen; write nothing")
    args = ap.parse_args(argv)

    try:
        if args.print_plan:
            print_plan(args.store, args.bucket, args.keep)
            return 0

        if args.work_dir is not None:
            _private_dir(args.work_dir)
            n = run(args.store, bucket=args.bucket, keep=args.keep,
                    upload=not args.no_upload, work_dir=args.work_dir)
        else:
            with tempfile.TemporaryDirectory(prefix="asi-backup.") as td:
                n = run(args.store, bucket=args.bucket, keep=args.keep,
                        upload=not args.no_upload, work_dir=Path(td))
        if n < 0:
            return 0
        print(f"{PROG}: backed up {n} scope(s)")
        return 0
    except BackupError as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — the timer's failure signal, not a swallow
        # 🔴 NOT EVERY FAILURE IS A BackupError, and the ones that are not are
        # the ones nobody wrote a message for. `S3Error` from the minio client,
        # a urllib3 connection error, a `FileNotFoundError` for a binary the
        # unit's PATH forgot — each previously reached the interpreter's default
        # handler, which prints a traceback with no statement of what it means
        # for the BACKUP. The traceback is kept, because for these the type and
        # the stack ARE the diagnosis; what is added is the sentence that says
        # this run backed nothing up. Still exit 1: `OnFailure` on the unit is
        # what turns this into a notification.
        traceback.print_exc()
        print(f"{PROG}: FAILED with an unexpected {type(exc).__name__}: {exc}. "
              f"Nothing was backed up on this run, or only part of it was — "
              f"treat this as NO backup for today and read the traceback above.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
