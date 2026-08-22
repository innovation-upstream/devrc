#!/usr/bin/env python3
"""A `git` interceptor that lets READS through and BLOCKS WRITES to any repo
outside the test's own tmpdir.

🔴 THE HAZARD (MEASURED — the real clone's own reflog, 2026-08-20)
------------------------------------------------------------------
The suite drove `git` against `~/workspace/devrc` itself. Not the remote only:
the local reflog records the writes landing in the operator's working clone.

    trunk@{0}: Branch: renamed refs/heads/main to refs/heads/trunk
    trunk@{1}: commit: seed
    trunk@{2..4}: commit: c

HEAD's reflog puts the last legitimate operation at 13:48:11 (`merge
origin/main`), the `main → trunk` rename at 14:21:35 local = **19:21:35Z**, and
HEAD on a single-file `f` tree at 14:38:36. The remote push storm — ~40 pushes
onto `refs/heads/main` in 43 seconds, plus four other branches — began
**19:28:14Z, about seven minutes AFTER the local rename**. The local damage came
first and the pushes followed it.

That ordering is the whole diagnosis. It rules out an independently-poisoned
remote and pins the mechanism as "something resolved a repo for itself, then
pushed what it found". It is also why a guard scoped to `push`, or to remotes,
would have been useless: the entire first phase is LOCAL.

The observed damage spans `commit`, `branch -m`, `config` (`core.bare=true`),
`remote set-url` and `push`. So this guard is NOT "no `remote set-url`" — it is
"no WRITE to a repo this test does not own", by whatever verb.

🔴 AND THE REPO IT WRITES TO IS NOT THE ONE ITS ARGUMENTS NAME
---------------------------------------------------------------
The obvious reading of the above is "some caller passes the wrong path", and it
is WRONG. The reproduced mechanism is an ambient `GIT_DIR`:

    $ GIT_DIR=<victim>/.git git -C <fixture> rev-parse --show-toplevel
    <fixture>
    $ GIT_DIR=<victim>/.git git -C <fixture> commit --allow-empty -m PWNED
    rc=0, and <victim>'s commit count went 1 -> 2.

`-C` is not protective. Every audit that cleared this suite on "absolute `-C`,
therefore fixture-scoped" — two of them, one a mechanical sweep — was measuring
a property that does not bound anything. The culprit is
`scripts/repo-cos/tests/test_prescan.py::_init_clone`, whose steps map
one-for-one onto the reflog above (`config user.email t@t`, `commit -m seed`,
`branch -M trunk`, `push origin HEAD:trunk`), and every one of them is a
well-formed `git -C <tmp clone> …`.

So the target of a write is computed from the ARGUMENTS **and** the
ENVIRONMENT, in sub-classes that do not cover each other. `GIT_LOCATION_ENV`
carries the ledger and the measurement that decides membership.

🔴 IT IS INVISIBLE BY CONSTRUCTION
-----------------------------------
The remote repoint was observed only by running `git remote -v` in the real
clone WHILE the suite ran; the test restores the URL afterwards, so every
after-the-fact check reports a clean repo. A guard that inspects state after
the run therefore cannot see this class at all — the interception has to happen
at the moment of the call, which is what this module does.

🔴 WHY A PATH SHIM, AND WHY PATH ALONE IS NOT ENOUGH
-----------------------------------------------------
A `git` that damages something is very often run by a GRANDCHILD. The suite
spawns shell scripts, and those scripts run `git`; a `subprocess.Popen` patched
inside the pytest process never sees any of it. Only a shim inherited through
the environment can.

⚠ An earlier revision of this file claimed that being "first on PATH" made the
shim unavoidable "at any depth". **That claim was FALSE and is now closed by
`exec_path_farm()` — do not re-derive it.** MEASURED:

    $ git -c 'alias.p=!echo PATH[0]=$(echo $PATH | cut -d: -f1)' p
    PATH[0]=/nix/store/…-git-2.55.0/libexec/git-core

git PREPENDS its own `libexec/git-core` to `PATH` for every child it spawns,
and that directory contains a real `git` binary. So every alias, hook, filter,
`core.fsmonitor`, `GIT_SSH_COMMAND`, `rebase --exec` and `submodule foreach`
child resolved `git` to the REAL binary no matter where the shim sat:

    $ git -C <fixture> -c 'alias.x=!git -C <victim> commit …' x
    rc 0, and the victim received the commit.

The fix is to make git prepend a directory THIS module owns: `exec_path_farm()`
builds a directory of symlinks to every real `git-*` helper plus the shim as
`git`, and `GIT_EXEC_PATH` points at it. Measured through the farm, PATH[0]
inside an alias is the farm and `git` resolves to the shim. See
`_exec_path_export()` for why the shim re-exports it on every call.

The in-process layer (`gitwrite_plugin._patch_subprocess`) exists to catch an
ABSOLUTE-path `git` launched by the pytest process, which no PATH-based
mechanism can shadow.

🔴 WHY THE SPLIT IS BY VERB **AND** BY TARGET, AND WHY IT FAILS LOUD
---------------------------------------------------------------------
`nolaunch` has two stub shapes and this is deliberately the `systemctl` one,
not the record-only one — for `systemctl`'s exact stated reason: **never
fabricate an answer**. Git is asked questions constantly, and this suite asks
them OF THE REAL REPO on purpose: the content gates
(`testlib/captured_text_scan.py`, `public_ip_scan.py`, `client_host_scan.py`,
`lib/service_recon.py`) all shell out to `git -C <repo-root> ls-files -z`.
Swallowing those with exit 0 and empty stdout would make every one of them scan
**nothing** and pass — the silent-zero failure, manufactured by the guard
itself. So reads always exec the real binary, wherever they point.

Writes are the mirror image. A write that is swallowed with exit 0 tells the
caller it succeeded, so the test proceeds and asserts against a repo that never
changed — green, and meaningless. A blocked write must therefore be LOUD: a
distinctive message on stderr and a distinctive exit code (`BLOCK_EXIT`), so it
surfaces as a failure naming this policy rather than as a puzzling git error.

The verb ledger is an ALLOWLIST of reads, so an unknown verb — including one
git gains in a future release — is treated as a write. Fail-closed is the right
direction to err in, because a false negative costs the operator's `main`.

🔴 BUT FAIL-CLOSED IS NOT FREE, AND PRETENDING IT IS ALMOST SANK THIS FILE.
An earlier revision of this paragraph claimed a false positive costs "one line
added to a ledger". Measured on the first armed run of the full suite, false
positives cost: 590 refusals of `GIT_CONFIG_GLOBAL=/dev/null`, three red tests
whose only sin was building a replacing `env=` dict, one refusal of every
`git init <tmp_path>` (judged by cwd), and one refusal of every
fixture-to-fixture `push`. A guard in that state is removed within a day, and
removing it restores the hazard in full — so a false positive and a false
negative differ in cost far less than the asymmetry suggests. Every one of
those was found by a POSITIVE control; none was visible to a negative one.

🔴 THE SECOND VECTOR: THE TARGET REPO IS NOT THE ONLY WAY OUT
--------------------------------------------------------------
A `push` from a repo that IS inside the tmpdir still reaches whatever its
`origin` points at. That is not hypothetical here: the contained clone prepared
for this work had an `origin` pointing at the operator's real local clone, so a
fixture pushing to `origin` would have written into their actual refs.

🔴 THE THIRD VECTOR: CONFIG INJECTION MOVES THE DESTINATION WITHOUT NAMING IT
------------------------------------------------------------------------------
`-c key=value`, `--config-env=key=VAR` and the `GIT_CONFIG_COUNT` /
`GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>` family all inject configuration
for one invocation. MEASURED, against the revision of this file that ignored
them entirely:

    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath \
      GIT_CONFIG_VALUE_0=<victim>/hooks \
      git -C <fixture> commit --allow-empty -m hookprobe
    -> rc 0, ALLOWED, the hook EXECUTED, artefact written inside the victim.

    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.url \
      GIT_CONFIG_VALUE_0=<victim>   git -C <fixture> push origin HEAD:pwned
    -> rc 0, ALLOWED, `refs/heads/pwned` created in the victim.

It is NOT blanket-refused, and that is the whole design constraint:
`scripts/analyze-service-index/commit.sh:247` legitimately exports
`GIT_CONFIG_COUNT=2` with `core.hooksPath` / `init.templateDir` aimed at an
empty `mktemp -d`, precisely to NEUTRALISE hooks, and two tests pin it. So
injected keys are classified — see `GIT_CONFIG_PATH_KEYS` and
`GIT_CONFIG_CMD_KEYS` — and a path-valued key is bounds-checked like any other
destination. The mktemp dir is under `$TMPDIR` and passes; the victim-aimed one
does not.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from .mockbin import write_exec

# ---------------------------------------------------------------------------
# The ledger of READ-ONLY git verbs.
#
# 🔴 An ALLOWLIST, pinned by scripts/tests/test_no_real_git_remote.py, so it
# fails when the set GROWS or SHRINKS. Anything absent is a write and is
# blocked outside the test's own roots — that includes verbs git has not
# shipped yet, which is the point: a blocklist fails OPEN on the next verb
# someone reaches for, and `remote` was exactly such a verb before this file.
#
# Membership rule, applied literally: a verb belongs here only if it CANNOT
# modify the repository, its config, its refs, its index, its worktree, or a
# remote. "Usually read-only" is not the test — `remote` and `config` both
# READ in their bare forms and write with an argument, so neither is listed
# and both are decided by target instead.
#
# 🔴 TWO ENTRIES WERE REMOVED BECAUSE THEY VIOLATED THAT RULE, and the test
# that was supposed to catch them could not:
#   * `hash-object` writes a loose object with `-w`. MEASURED against a denied
#     victim: `git -C <victim> hash-object -w <file>` exited 0 and the victim's
#     loose-object count went 6 -> 7. It is now a DUAL verb — the read form is
#     the one without `-w`.
#   * `symbolic-ref` REPOINTS a ref with two arguments and DELETES one with
#     `-d`. MEASURED against a denied victim: `git -C <victim> symbolic-ref -q
#     HEAD refs/heads/trunk` exited 0 and moved the victim's `.git/HEAD` and
#     `.git/logs/HEAD`. The one-argument form is a read; the verb is now DUAL.
#   * `interpret-trailers --in-place <file>` REWRITES THE FILE IT IS GIVEN, so
#     the verb can write anywhere the caller can name — it is DUAL too.
#   * `status` refreshes and REWRITES `<repo>/.git/index`. MEASURED: the
#     victim's index hash moved `63e1e299594b -> 59551aa2642b`. It stays a read
#     — the suite runs it against the real repo on purpose — but the shim now
#     always execs it as `git --no-optional-locks status`, which MEASURED
#     leaves the index byte-identical and produces byte-identical stdout. See
#     `GIT_LOCK_FREE_READ_VERBS`.
# ---------------------------------------------------------------------------
GIT_READ_VERBS = (
    "status", "log", "show", "diff", "cat-file", "ls-files", "ls-tree",
    "ls-remote", "rev-parse", "rev-list", "describe", "blame", "grep",
    "shortlog", "show-ref", "for-each-ref", "merge-base",
    "name-rev", "check-ignore", "check-attr", "count-objects", "verify-pack",
    "var", "help", "version", "annotate", "whatchanged", "cherry",
    "diff-tree", "diff-index", "diff-files", "patch-id",
    "get-tar-commit-id", "check-mailmap", "column",
)

# 🔴 Read verbs that touch the index unless told not to. `--no-optional-locks`
# is prepended for these, ALWAYS — not only when the target is out of bounds,
# because deciding that would cost a `rev-parse` subprocess on the hottest path
# in the suite. MEASURED on git 2.55.0: identical stdout, and the victim's
# `.git/index` unchanged.
GIT_LOCK_FREE_READ_VERBS = ("status",)

# Read-only global FLAGS. 🔴 SCANNED ONLY BEFORE THE VERB.
#
# MEASURED: the previous revision scanned the WHOLE argv for these, so any
# `-h` anywhere — including as the VALUE of an option — took the exec fast path
# with no bounds check at all:
#
#     git -C <victim> commit --allow-empty -m -h   -> rc 0, TWO victim commits
#
# `-m -h` means "commit with the message `-h`". Only tokens before the verb can
# be git's own global options, so that is the only place they are honoured now.
# `<verb> -h` / `<verb> --help` is handled separately, and only when it is the
# single remaining token, which is exactly the form that prints usage and exits.
GIT_READ_FLAGS = ("--version", "--help", "-h", "--exec-path", "--html-path",
                  "--man-path", "--info-path")

# 🔴 Verbs that write to a DESTINATION rather than to the local repo. For
# these, being inside an allowed root is NOT sufficient — the destination is
# resolved and checked as well. See the module docstring's second vector.
GIT_REMOTE_WRITE_VERBS = ("push",)

# 🔴 Verbs whose target is a POSITIONAL ARGUMENT, not the current repo.
#
# Found by the positive control, not by reading the docs: with the guard's
# first draft, `git init /tmp/<fixture>` was REFUSED, because the resolver used
# the process's cwd — and a suite run from the repo root has the repo as its
# cwd. Every fixture that builds itself a clone would have gone red, which is
# the permanently-red gate claude/RULES.md warns about.
GIT_TARGET_IS_ARG_VERBS = ("init", "clone")

# Options that consume the NEXT argv entry, for the two verbs above. Any option
# spelled `--opt=value` needs no entry: it carries its value inline.
GIT_INIT_VALUE_OPTS = (
    "-b", "--initial-branch", "--separate-git-dir", "--template",
    "--object-format", "--ref-format", "--shared",
)
GIT_CLONE_VALUE_OPTS = (
    "-b", "--branch", "-o", "--origin", "-u", "--upload-pack", "--reference",
    "--reference-if-able", "--separate-git-dir", "--depth", "--template",
    "-c", "--config", "--filter", "--shallow-since", "--shallow-exclude",
    "-j", "--jobs", "--bundle-uri", "--server-option", "--recurse-submodules",
)

# 🔴 Verbs that READ in one form and WRITE in another. The verb alone cannot
# decide them, so neither the read ledger nor the write path may own them.
#
# Found by the positive control, again: `git remote -v`, `git config --get …`
# and `git branch --show-current` are the commands this very policy's own
# containment check runs, and the first draft REFUSED all three — while the
# suite itself reads `remote get-url origin`, `config --get
# remote.origin.url`, `branch -a --format=…` and `worktree list --porcelain`
# against the real repo on purpose. Listing them as reads would have failed
# OPEN on `remote set-url`, which is the incident; leaving them as writes goes
# permanently red. The only honest answer is to classify the FORM.
#
# `_dual_is_read_body` implements it as an ALLOWLIST of read forms — an
# unrecognised form is a write. It runs only when the target is already out of
# bounds, so a misclassification cannot affect a legitimate fixture write.
GIT_DUAL_VERBS = ("remote", "config", "branch", "tag", "worktree",
                  "submodule", "stash", "notes", "hash-object",
                  "symbolic-ref", "interpret-trailers", "fsck")

# 🔴 THE ENVIRONMENT IS PART OF THE TARGET — `-C` IS NOT PROTECTIVE.
#
# MEASURED, and it invalidated this module's first design outright:
#
#     $ GIT_DIR=<victim>/.git git -C <fixture> rev-parse --show-toplevel
#     <fixture>                      <- the innocent answer
#     $ GIT_DIR=<victim>/.git git -C <fixture> commit --allow-empty -m PWNED
#     rc=0, and <victim>'s commit count went 1 -> 2.
#
# The defence is therefore NOT to enumerate argument shapes. It is to ask GIT
# ITSELF where it is about to write, and to check the listed variables
# explicitly on top of that.
#
# 🔴 EVERY ENTRY MUST BE A PATH THAT RECEIVES BYTES, and membership is decided
# by ARMING THE VARIABLE WITH THE GUARD REMOVED and watching the victim move —
# never by asking whether the guard refuses it. THREE names have now been
# removed for failing that test:
#   * `GIT_NAMESPACE` is a REF NAMESPACE, not a filesystem path.
#   * `GIT_CEILING_DIRECTORIES` RESTRICTS repository discovery; treating it as
#     a destination inverts its meaning and refuses the safe case.
#   * `GIT_ALTERNATE_OBJECT_DIRECTORIES` is a READ-ONLY *additional* object
#     store. It survived the first cull and the test written to prevent
#     exactly this recurrence PINNED IT AS CORRECT. MEASURED with it armed at
#     the victim and the guard removed:
#
#         GIT_ALTERNATE_OBJECT_DIRECTORIES=<victim>/.git/objects \
#           git -C <fixture> commit --allow-empty -m PWNED
#         -> rc 0; the new object landed in <fixture>/.git/objects/c4/…
#            and a full byte manifest of the victim was UNCHANGED.
#
#     Counting it as "defended" is what inflated this policy's headline number.
#     `test_every_env_ledger_entry_is_a_live_write_vector` now EXERCISES each
#     entry with the guard removed instead of asserting membership, so a
#     non-vector cannot be pinned as correct again.
GIT_LOCATION_ENV = (
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_INDEX_FILE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
)

# 🔴 Environment variables whose value is a COMMAND git will EXECUTE.
#
# MEASURED against the previous revision: a permitted READ became arbitrary
# code execution with the victim as target.
#
#     GIT_EXTERNAL_DIFF=<script writing into the victim> git -C <fixture> diff
#     -> rc 0, ALLOWED, and <victim>/EXTDIFF-RAN was created.
#
# A command string is not a path, so it cannot be bounds-checked. These are
# refused outright when NON-EMPTY. An EMPTY value is allowed and is not an
# oversight: emptying one of these is how a caller DISARMS the hook, which is
# the opposite of the hazard. Grepped across `scripts/`: no caller sets any of
# these to a non-empty value, so the false-positive cost is zero today.
#
# `GIT_EDITOR` / `GIT_PAGER` are deliberately ABSENT. `GIT_PAGER=cat` and
# `GIT_EDITOR=true` are the standard hermetic-harness idioms, they run only in
# interactive flows this suite never reaches, and refusing them would be a
# permanently-red gate for no measured hazard.
#
# 🔴 `GIT_SSH_COMMAND` IS ABSENT TOO, AND THAT WAS MEASURED RATHER THAN
# REASONED. It was on this list for one armed run of `scripts/tests` and cost
# NINE red tests: `scripts/resume-state.sh:327` sets
# `GIT_SSH_COMMAND='ssh -oBatchMode=yes -oConnectTimeout=5 …'` around its
# `fetch`, which is a caller doing the right thing (refusing to hang a test
# runner on an ssh prompt). The refusal did not read as a policy violation
# either — it surfaced as `[fetch failed; compared against refs already on
# disk]` inside resume-state's own output, and the tests failed on a
# DIFFERENT assertion four layers away.
#
# The residual hazard is small and is covered elsewhere: `GIT_SSH_COMMAND`
# executes only for an ssh transport, a `push` to a non-local URL is already
# refused as "not a local path", and an ssh `fetch` only reads. The four names
# that remain have ZERO callers anywhere under `scripts/`.
GIT_EXEC_ENV = ("GIT_EXTERNAL_DIFF", "GIT_ASKPASS", "GIT_PROXY_COMMAND",
                "GIT_SSH")

# 🔴 Injected-config keys whose VALUE IS A PATH THAT RECEIVES BYTES or that
# git will read executable content out of. Bounds-checked like any other
# destination — NOT refused.
#
# The bounds check rather than a refusal is forced by a real caller:
# `scripts/analyze-service-index/commit.sh:247` exports
# `GIT_CONFIG_COUNT=2` with `core.hooksPath` and `init.templateDir` both aimed
# at an empty `mktemp -d` under `$TMPDIR`, to neutralise hooks. That dir is
# inside an allowed root and passes; `<victim>/hooks` does not.
#
# Matched case-insensitively and as GLOBS, because git config keys are
# case-insensitive in their section and variable parts and several of these
# carry a middle subsection.
GIT_CONFIG_PATH_KEYS = (
    "core.hookspath", "init.templatedir", "core.worktree", "include.path",
    "includeif.*.path", "core.excludesfile", "core.attributesfile",
    "commit.template",
)

# 🔴 Injected-config keys whose VALUE IS A COMMAND. Refused when non-empty, for
# the same reason as `GIT_EXEC_ENV`: a command string cannot be bounds-checked.
#
# EMPTY IS ALLOWED, and that is load-bearing rather than lenient:
# `scripts/task-spec-drafter/ticket-status:249` passes `-c core.fsmonitor=`
# as a HARDENING measure — an empty value disables the hook. Refusing it would
# have gone red on a caller that was doing the right thing.
GIT_CONFIG_CMD_KEYS = (
    "alias.*", "core.fsmonitor", "core.editor", "core.pager",
    "core.sshcommand", "core.askpass", "sequence.editor", "diff.external",
    "diff.*.textconv", "diff.*.command", "filter.*.clean", "filter.*.smudge",
    "filter.*.process", "credential.helper", "uploadpack.packobjectshook",
    "gpg.program", "gpg.*.program", "merge.*.driver", "protocol.*.command",
)

# 🔴 Paths that are never a repository and never worth protecting.
#
# MEASURED: `/dev/null` accounted for **590 of the 593** firings on the first
# armed run of the full suite — `GIT_CONFIG_GLOBAL=/dev/null` is the standard
# idiom for "this test has no global git config", used throughout this tree and
# by every hermetic harness. Refusing it would have made the policy
# permanently red across `scripts/tests` on day one.
#
# It is an exemption for a SINK, not a widening of the roots: `/dev/null`
# discards what is written to it, so a config write aimed there damages
# nothing. `GIT_CONFIG_GLOBAL=<victim>/.gitconfig` is still refused.
ALWAYS_SAFE_PATHS = ("/dev/null",)

LOG_NAME = "git-calls.log"

# The exit code a blocked write returns. Distinctive on purpose: git itself
# uses 1/128/129, so a test failing with 99 is unambiguously this policy and
# not a git error the reader has to diagnose.
BLOCK_EXIT = 99

# The stderr banner. Pinned verbatim by the guard test — a policy whose
# message can drift is a policy whose firing cannot be asserted on.
BANNER = "DEVRC-NO-REAL-GIT VIOLATION"

# Where `install()` is told which roots a write may target. Read by the STUB at
# call time (not baked in) so a test can widen the roots for its own fixture
# without reinstalling the shim.
ROOTS_ENV = "DEVRC_TEST_GIT_ALLOWED_ROOTS"
STUB_DIR_ENV = "DEVRC_TEST_GIT_STUB_DIR"

# 🔴 Roots that are ALWAYS refused, checked BEFORE the allow-list and winning
# over it. This is what keeps the policy from being inert in the tier that
# gates merges.
#
# The allow-list is "the tmp roots a test may write in", and on the dev host
# that correctly excludes `~/workspace/devrc`. But in the nix build sandbox the
# source tree is unpacked UNDER `$TMPDIR` — so an allow-list alone would place
# the repo under test INSIDE an allowed root, and every write this policy
# exists to stop would be permitted in exactly the tier that gates merges.
DENY_ROOTS_ENV = "DEVRC_TEST_GIT_DENIED_ROOTS"

# 🔴 THERE IS NO ENV-READABLE MODE ANY MORE, AND THAT IS THE FIX.
#
# This module used to read `DEVRC_TEST_GIT_MODE`; `audit` recorded a violation
# and let it through. MEASURED: `DEVRC_TEST_GIT_MODE=audit git -C <victim>
# commit --allow-empty -m AUDITPWN` returned 0 and the victim received the
# commit — a single environment variable, inherited from anywhere, silently
# disarmed the whole policy. The test that was meant to prevent it asserted
# only that a `${VAR:-block}` string appeared in the generated body, which is
# satisfied by a shim in audit mode.
#
# Audit behaviour is now a BAKE-TIME argument to `install()`, so nothing an
# inherited environment can carry will turn the guard off. `MODE_ENV` remains
# defined only so the plugin can scrub a stale value and a test can assert the
# shim ignores it.
MODE_ENV = "DEVRC_TEST_GIT_MODE"
MODE_BLOCK = "block"
MODE_AUDIT = "audit"

# 🔴 The handshake. `<stub>/git --devrc-gitwrite-handshake` prints this magic, the
# POLICY FINGERPRINT, and the roots the shim was BAKED with.
#
# It exists because `gitwrite_plugin._inherited_stub_dir()` used to adopt any
# directory whose `DEVRC_TEST_GIT_STUB_DIR` contained a FILE NAMED `git` and
# then skip installation entirely. MEASURED with that variable pointed at a
# plain passthrough script: a write to the repo-under-test landed rc 0 with no
# banner, and the session marker was written anyway — the run reported
# "1 passed" with no guard present at all. A filename is a spelled guard,
# walkable by anything called `git`.
HANDSHAKE_FLAG = "--devrc-gitwrite-handshake"
HANDSHAKE_MAGIC = "DEVRC-NOGIT-HANDSHAKE"

# The subdirectory of the stub dir that becomes `GIT_EXEC_PATH`. Deliberately
# NOT the stub dir itself: the farm holds ~184 symlinks named `git-*`, and
# putting those on PATH[0] would shadow any same-named tool for every process
# in the session. The farm is reached only through `GIT_EXEC_PATH`.
EXEC_PATH_DIR = "execpath"


def log_path(stub_dir: Path) -> Path:
    """Where the shim records every intercepted `git`, one line per call."""
    return Path(stub_dir) / LOG_NAME


def exec_path_dir(stub_dir: Path) -> Path:
    """The `GIT_EXEC_PATH` farm belonging to a stub dir."""
    return Path(stub_dir) / EXEC_PATH_DIR


# --------------------------------------------------------------------------- #
# sh helpers
# --------------------------------------------------------------------------- #
def _exec_path_export(stub_dir: Path) -> str:
    """sh that re-exports `GIT_EXEC_PATH` at the top of every shim call.

    🔴 RE-EXPORTED ON EVERY CALL, not only set once by the plugin, for the same
    reason the roots are baked in: a child built with a REPLACING `env=` dict
    keeps `PATH` (or it could not find git at all) and drops everything else.
    Without this line such a child reaches the shim, the shim execs the real
    git, and the real git prepends its OWN libexec — restoring the escape for
    every grandchild.
    """
    farm = exec_path_dir(stub_dir)
    return (
        f'if [ -x "{farm}/git" ]; then\n'
        f'  GIT_EXEC_PATH="{farm}"\n'
        '  export GIT_EXEC_PATH\n'
        'fi\n'
    )


def _injected_config_body() -> str:
    """sh that collects every config key injected for THIS invocation.

    Sets `inj` to newline-separated `key=value` records, gathered from
    `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` and from the
    `-c key=value` / `--config-env=key=VAR` global options.

    🔴 `man git` does NOT document the `GIT_CONFIG_COUNT` family, and this
    binary honours it. A ledger built from the manual page was therefore
    structurally unable to see the single largest injection vector, which is
    why the shim asks git itself for the identity half AND parses this half
    explicitly.
    """
    return (
        'collect_env_config() {\n'
        '  ec_n="${GIT_CONFIG_COUNT:-}"\n'
        '  [ -n "$ec_n" ] || return 0\n'
        # 🔴 MIRROR GIT'S OWN PARSER, THEN FAIL CLOSED ON THE REST.
        #
        # git reads this with `strtoumax`, which SKIPS LEADING WHITESPACE and
        # ACCEPTS A LEADING `+`. An all-digits `case` does not, and the
        # difference is a live bypass rather than a nit — MEASURED against the
        # first revision of this function, where `=1` was refused and both of
        # these were not:
        #
        #     GIT_CONFIG_COUNT=+1  GIT_CONFIG_KEY_0=core.hooksPath …  -> rc 0
        #     GIT_CONFIG_COUNT=' 1' GIT_CONFIG_KEY_0=core.hooksPath … -> rc 0
        #
        # git honoured the injection and the hook ran inside the victim, while
        # this scan read "not a number" and collected NOTHING — the worst
        # possible direction, because an unparseable count silently emptied the
        # candidate set instead of growing it.
        #
        # So: strip what git strips, then demand digits — and if what is left
        # is still not a count, REFUSE. "I cannot parse this and git might"
        # is not a reason to proceed.
        '  while :; do\n'
        '    case "$ec_n" in\n'
        "      ' '*|'\t'*) ec_n=\"${ec_n#?}\" ;;\n"
        '      *) break ;;\n'
        '    esac\n'
        '  done\n'
        '  ec_n="${ec_n#+}"\n'
        '  case "$ec_n" in\n'
        '    ""|*[!0-9]*)\n'
        '      allowed=0\n'
        '      why="GIT_CONFIG_COUNT is set to something this shim cannot '
        'parse as a count; git may still honour it, so the injected keys '
        'cannot be checked"\n'
        '      return 0 ;;\n'
        '  esac\n'
        '  ec_i=0\n'
        # Bounded so a hostile or corrupt count cannot spin forever.
        '  [ "$ec_n" -gt 1000 ] && ec_n=1000\n'
        '  while [ "$ec_i" -lt "$ec_n" ]; do\n'
        '    eval "ec_k=\\${GIT_CONFIG_KEY_$ec_i:-}"\n'
        '    eval "ec_v=\\${GIT_CONFIG_VALUE_$ec_i:-}"\n'
        '    if [ -n "$ec_k" ]; then\n'
        '      inj="$inj$ec_k=$ec_v\n"\n'
        '    fi\n'
        '    ec_i=$((ec_i+1))\n'
        '  done\n'
        '}\n'
        # Lowercase a key so the case-globs below can be written once. git
        # config keys are case-insensitive in the section and variable parts.
        'lc_key() { printf \'%s\' "$1" | tr \'[:upper:]\' \'[:lower:]\'; }\n'
    )


def _classify_injected_body() -> str:
    """sh: `classify_injected` — walks `$inj`, refusing command keys and
    appending path keys to `$cands`.

    Sets `allowed=0` and `why` for a command-valued key with a non-empty value.
    """
    path_globs = "|".join(GIT_CONFIG_PATH_KEYS)
    cmd_globs = "|".join(GIT_CONFIG_CMD_KEYS)
    return (
        'classify_injected() {\n'
        '  [ -n "$inj" ] || return 0\n'
        '  ci_old="$IFS"\n'
        # One record per LINE. IFS is set to newline only, so a value
        # containing spaces survives intact — the previous revision's
        # `for line in $info` split on the default IFS and would have shredded
        # any path with a space in it.
        '  IFS="\n"\n'
        '  for ci_rec in $inj; do\n'
        '    IFS="$ci_old"\n'
        '    [ -n "$ci_rec" ] || { IFS="\n"; continue; }\n'
        '    ci_k="${ci_rec%%=*}"\n'
        '    ci_v="${ci_rec#*=}"\n'
        '    ci_lk=$(lc_key "$ci_k")\n'
        '    case "$ci_lk" in\n'
        f'      {cmd_globs})\n'
        # 🔴 EMPTY IS ALLOWED — emptying one of these DISABLES the hook, which
        # is what `task-spec-drafter/ticket-status` does on purpose.
        '        if [ -n "$ci_v" ]; then\n'
        '          allowed=0\n'
        '          why="config \'$ci_k\' names a COMMAND git would execute; a '
        'command cannot be bounded to a directory"\n'
        '          IFS="$ci_old"\n'
        '          return 0\n'
        '        fi ;;\n'
        f'      {path_globs})\n'
        '        [ -n "$ci_v" ] && cands="$cands:$ci_v" ;;\n'
        '    esac\n'
        '    IFS="\n"\n'
        '  done\n'
        '  IFS="$ci_old"\n'
        '}\n'
    )


def _exec_env_body() -> str:
    """sh: refuse when any `GIT_EXEC_ENV` variable carries a non-empty value."""
    checks = "".join(
        f'  if [ -n "${{{v}:-}}" ]; then\n'
        f'    allowed=0\n'
        f'    why="{v} names a COMMAND git would execute; a command cannot be '
        'bounded to a directory"\n'
        '    return 0\n'
        '  fi\n'
        for v in GIT_EXEC_ENV)
    return 'check_exec_env() {\n' + checks + '  return 0\n}\n'


def _push_destination_body(real: str) -> str:
    """POSIX-sh that resolves a `push`'s destination to a local path, if any.

    Sets `dest` to a filesystem path when the push target is a local repo, and
    leaves it empty when the target is a URL this check does not bound (ssh://,
    https://, git@host:path). Empty means "not a local path", NOT "safe" — the
    caller treats an unresolvable destination as a violation, because a push
    that leaves the machine is exactly what destroyed the branch.

    🔴 THE RESOLVER IS `config --get`, NOT `remote get-url`, AND THE DIFFERENCE
    IS A LIVE BYPASS. The previous revision used `remote get-url`, whose answer
    ignores injected config entirely. MEASURED on git 2.55.0, against a repo
    whose `origin` is `/tmp/ORIGINAL.git`, with `remote.origin.url` injected as
    `/tmp/INJECTED.git`:

        via GIT_CONFIG_COUNT      via -c
        remote get-url origin    /tmp/ORIGINAL.git   /tmp/ORIGINAL.git   BLIND
        ls-remote --get-url      /tmp/ORIGINAL.git   /tmp/ORIGINAL.git   BLIND
        config --get remote…url  /tmp/INJECTED.git   /tmp/INJECTED.git   SEES IT

    `push` itself honours the injection, so the old resolver checked one URL
    while git used another: `-c remote.origin.url=<victim> push origin
    HEAD:refs/heads/pwned` exited 0 and created the branch in the victim. This
    falsifies the previous revision's claim that asking git accounts for the
    `GIT_CONFIG_*` family — it depends entirely on WHICH command is asked.

    🔴 AND THE INJECTED VALUE IS READ OFF THE ARGV, NOT OUT OF A CHILD. The
    sharper fact above — that `config --get` sees the injection — is TRUE BUT
    INSUFFICIENT: a fresh child process does not carry the caller's `-c` at
    all, so forwarding it is an extra thing to remember and a resolver that
    forgets is silently blind again. `inj_get` answers from the record this
    shim already parsed off its own command line, with no process at all. The
    child query survives only as the fallback for the un-injected case.

    `pushurl` is consulted first because git prefers it when present.
    """
    return (
        # Last-wins, matching git: a key repeated in `-c` or in the
        # GIT_CONFIG_COUNT array takes its final value.
        'inj_get() {\n'
        '  ig_k=$(lc_key "$1")\n'
        '  ig_out=""\n'
        '  ig_old="$IFS"\n'
        '  IFS="\n"\n'
        '  for ig_rec in $inj; do\n'
        '    IFS="$ig_old"\n'
        '    [ -n "$ig_rec" ] || { IFS="\n"; continue; }\n'
        '    ig_rk=$(lc_key "${ig_rec%%=*}")\n'
        '    [ "$ig_rk" = "$ig_k" ] && ig_out="${ig_rec#*=}"\n'
        '    IFS="\n"\n'
        '  done\n'
        '  IFS="$ig_old"\n'
        '  printf \'%s\' "$ig_out"\n'
        '}\n'
        'push_dest() {\n'
        '  pv="$1"; pc="$2"; shift 2\n'
        # 🔴 SKIP TO THE VERB FIRST — the same measured bug as dual_is_read,
        # in a second place. The value of `-C` is a non-flag token, so "the
        # first non-flag argument" is the -C PATH, not the remote. Without
        # this, `git -C <path> push origin trunk` resolved its remote to the
        # literal string "push", and every legitimate fixture-to-fixture push
        # was refused. Found only by the positive control.
        '  while [ "$#" -gt 0 ]; do\n'
        '    a="$1"; shift\n'
        '    case "$a" in -*) continue ;; esac\n'
        '    [ "$a" = "$pv" ] && break\n'
        '  done\n'
        '  rem=""\n'
        '  for a in "$@"; do\n'
        '    case "$a" in -*) continue ;; esac\n'
        '    rem="$a"; break\n'
        '  done\n'
        # No remote named: git uses the branch's configured remote, defaulting
        # to `origin`. Ask git rather than guessing.
        '  if [ -z "$rem" ]; then\n'
        '    rem=$(gitq remote 2>/dev/null | head -n 1)\n'
        '  fi\n'
        '  [ -n "$rem" ] || rem=origin\n'
        '  case "$rem" in\n'
        '    */*|.|..) url="$rem" ;;\n'
        '    *)\n'
        # 🔴 THE INJECTED VALUE FIRST, ANSWERED WITHOUT A CHILD PROCESS.
        '      url=$(inj_get "remote.$rem.pushurl")\n'
        '      [ -n "$url" ] || url=$(inj_get "remote.$rem.url")\n'
        '      [ -n "$url" ] || url=$(gitq config --get "remote.$rem.pushurl" '
        '2>/dev/null)\n'
        '      [ -n "$url" ] || url=$(gitq config --get "remote.$rem.url" '
        '2>/dev/null)\n'
        # Last resort only. `remote get-url` is blind to injected config, so it
        # can never be the primary answer — but it does resolve `insteadOf`
        # rewrites that `config --get` returns raw, and an empty destination is
        # treated as a violation, so falling back here can only ever make the
        # check see MORE.
        '      [ -n "$url" ] || url=$(gitq remote get-url "$rem" 2>/dev/null)\n'
        '      ;;\n'
        '  esac\n'
        '  case "$url" in\n'
        '    ""|*://*|*@*:*) printf \'\' ;;\n'
        '    *) printf \'%s\' "$url" ;;\n'
        '  esac\n'
        '}\n'
    )


def _gitq_body(real: str) -> str:
    """sh: `gitq …` — ask the REAL git a question in the caller's own context.

    🔴 THE INJECTED `-c` OPTIONS ARE FORWARDED. Without that, every question
    the shim asks is answered from a DIFFERENT configuration than the one the
    real call will run under, which is precisely how the push-destination
    bypass worked. `GIT_CONFIG_*` needs no forwarding — it is inherited.
    """
    return (
        'gitq() {\n'
        '  if [ -n "$cdir" ]; then\n'
        f'    "{real}" -C "$cdir" $cargs "$@"\n'
        '  else\n'
        f'    "{real}" $cargs "$@"\n'
        '  fi\n'
        '}\n'
    )


def _dual_is_read_body() -> str:
    """POSIX-sh defining `dual_is_read <verb> <argv…>` — 0 when the form READS.

    🔴 AN ALLOWLIST OF READ FORMS, so an unrecognised form is a write. The
    direction matters: a `remote` form this function does not recognise must
    land on `set-url`'s side of the line, not `get-url`'s.

    It is consulted ONLY after the target has already been judged out of
    bounds, which bounds the damage of getting one of these wrong: a form
    misclassified as a write costs a false red on a read of the real repo, and
    can never permit a write inside a fixture.
    """
    return (
        'dual_is_read() {\n'
        '  dv="$1"; shift\n'
        # 🔴 DISCARD EVERYTHING UP TO AND INCLUDING THE VERB FIRST.
        # Measured: without this, `git -C <path> remote -v` had `<path>` — the
        # VALUE of -C — as its first non-flag token, so every dual verb was
        # classified from the wrong word and five legitimate reads were
        # refused. The positive control is what caught it.
        '  while [ "$#" -gt 0 ]; do\n'
        '    a="$1"; shift\n'
        '    case "$a" in -*) continue ;; esac\n'
        '    [ "$a" = "$dv" ] && break\n'
        '  done\n'
        '  dsub=""\n'
        '  dpos=0\n'
        '  for a in "$@"; do\n'
        '    case "$a" in -*) continue ;; esac\n'
        '    dpos=$((dpos+1))\n'
        '    [ "$dpos" -eq 1 ] && dsub="$a"\n'
        '  done\n'
        '  case "$dv" in\n'
        '    remote)\n'
        '      case "$dsub" in\n'
        '        ""|show|get-url) return 0 ;;\n'
        '        *) return 1 ;;\n'
        '      esac ;;\n'
        # `config` splits on its ACTION flag, then on arity: `config KEY` reads,
        # `config KEY VALUE` writes. Arity is the subtle one — it is how
        # `core.bare true` differs from `core.bare`, and getting it backwards
        # would wave through the exact config write seen in the incident.
        '    config)\n'
        '      for a in "$@"; do\n'
        '        case "$a" in\n'
        '          --get|--get-all|--get-regexp|--get-urlmatch|--get-color|'
        '--get-colorbool|-l|--list) return 0 ;;\n'
        '          --unset|--unset-all|--replace-all|--add|--edit|-e|'
        '--rename-section|--remove-section|--set-all) return 1 ;;\n'
        '        esac\n'
        '      done\n'
        '      [ "$dpos" -le 1 ] && return 0\n'
        '      return 1 ;;\n'
        # 🔴 `hash-object` WRITES with `-w`. It sat on the READ ledger, and
        # MEASURED against a denied victim it exited 0 while the victim's
        # loose-object count went 6 -> 7. Everything else about it is a read.
        '    hash-object)\n'
        '      for a in "$@"; do\n'
        '        case "$a" in -w|--stdin-paths) return 1 ;; esac\n'
        '      done\n'
        '      return 0 ;;\n'
        # 🔴 `symbolic-ref <name>` READS; `symbolic-ref <name> <ref>` REPOINTS
        # and `-d` deletes. Decided by COUNTING positionals rather than by
        # matching a spelling — measured, `symbolic-ref -q HEAD
        # refs/heads/trunk` moved a denied victim's HEAD while the verb sat on
        # the READ ledger, and `-q` is only one of several flags that could
        # have carried it.
        '    symbolic-ref)\n'
        '      for a in "$@"; do\n'
        '        case "$a" in -d|--delete) return 1 ;; esac\n'
        '      done\n'
        '      [ "$dpos" -le 1 ] && return 0\n'
        '      return 1 ;;\n'
        # `interpret-trailers --in-place <file>` rewrites the file it is given,
        # anywhere the caller can name it. Every other form writes to stdout.
        '    interpret-trailers)\n'
        '      for a in "$@"; do\n'
        '        case "$a" in --in-place) return 1 ;; esac\n'
        '      done\n'
        '      return 0 ;;\n'
        # `fsck` inspects; `--lost-found` writes `.git/lost-found` when there
        # is anything dangling to put there. MEASURED on a clean repo: all
        # three of `fsck`, `fsck --lost-found` and `fsck --connectivity-only`
        # left the repo byte-identical — so the verb is a read in practice and
        # refusing it outright was a false positive. It is DUAL rather than
        # ledgered because "wrote nothing on a clean repo" is not the ledger's
        # membership rule, and `--lost-found` on a repo with dangling objects
        # does write.
        '    fsck)\n'
        '      for a in "$@"; do\n'
        '        case "$a" in --lost-found) return 1 ;; esac\n'
        '      done\n'
        '      return 0 ;;\n'
        # `branch` reads when it LISTS: no positional, and no flag that moves,
        # copies, deletes or re-points anything.
        '    branch)\n'
        '      dskip=0\n'
        '      dn=0\n'
        '      for a in "$@"; do\n'
        '        if [ "$dskip" -eq 1 ]; then dskip=0; continue; fi\n'
        '        case "$a" in\n'
        '          -m|-M|-d|-D|-c|-C|--move|--copy|--delete|--set-upstream|'
        '--set-upstream-to|--unset-upstream|--edit-description|-f|--force)\n'
        '            return 1 ;;\n'
        '          --contains|--no-contains|--merged|--no-merged|--points-at|'
        '--sort|--format)\n'
        '            dskip=1; continue ;;\n'
        '          -*) continue ;;\n'
        '        esac\n'
        '        dn=$((dn+1))\n'
        '      done\n'
        '      [ "$dn" -eq 0 ] && return 0\n'
        '      return 1 ;;\n'
        '    tag)\n'
        '      for a in "$@"; do\n'
        '        case "$a" in\n'
        '          -d|--delete|-a|-s|-u|-m|-F|-f|--force|--create-reflog)\n'
        '            return 1 ;;\n'
        '        esac\n'
        '      done\n'
        '      [ "$dpos" -eq 0 ] && return 0\n'
        '      for a in "$@"; do\n'
        '        case "$a" in\n'
        '          -l|--list|-n|-n[0-9]*|--contains|--points-at|--sort|'
        '--format|--format=*|--merged|--no-merged) return 0 ;;\n'
        '        esac\n'
        '      done\n'
        '      return 1 ;;\n'
        '    worktree)\n'
        '      case "$dsub" in list) return 0 ;; *) return 1 ;; esac ;;\n'
        # `submodule foreach` runs an arbitrary command, so it is NOT a read
        # however harmless the command looks.
        '    submodule)\n'
        '      case "$dsub" in status|summary) return 0 ;; *) return 1 ;; esac ;;\n'
        '    stash)\n'
        '      case "$dsub" in list|show) return 0 ;; *) return 1 ;; esac ;;\n'
        # A bare `git notes` LISTS, so the empty sub-command is a read. `stash`
        # deliberately does NOT get the same treatment: a bare `git stash` is
        # `stash push`, which writes.
        '    notes)\n'
        '      case "$dsub" in ""|list|show) return 0 ;; *) return 1 ;; esac ;;\n'
        '  esac\n'
        '  return 1\n'
        '}\n'
    )


def _extra_destination_body() -> str:
    """sh: `collect_extra_dests <verb> <argv…>` — append the destinations a
    verb writes to that are NOT the repository it is run in.

    🔴 EVERY ONE OF THESE WAS A MEASURED BYPASS of the previous revision,
    because the repo the command runs in was an in-bounds fixture, so the
    bounds check passed and `dual_is_read` was never even consulted:

        git -C <fixture> config --file <victim>/.git/config core.bare true
            -> rc 0, the victim's config was rewritten
        git -C <fixture> config --global user.name pwned
            -> rc 0, wrote $HOME/.gitconfig, unbounded
        git init --separate-git-dir <victim>/stolen.git <allowed>/new
        git clone --separate-git-dir <victim>/stolen2.git <src> <allowed>/cl
            -> rc 0, a whole git dir created inside the victim
        git -C <fixture> worktree add <victim>/wt
            -> rc 0, the victim received a worktree
        git -C <fixture> archive --output <victim>/stolen.tar HEAD
        git -C <fixture> bundle create <victim>/stolen.bundle HEAD
            -> rc 0, both files written inside the victim
    """
    return (
        'collect_extra_dests() {\n'
        '  xv="$1"; shift\n'
        '  xwant=""\n'
        '  xseen=0\n'
        '  xsub=""\n'
        '  xnpos=0\n'
        '  for a in "$@"; do\n'
        '    if [ -n "$xwant" ]; then\n'
        '      cands="$cands:$a"\n'
        '      xwant=""\n'
        '      continue\n'
        '    fi\n'
        '    if [ "$xseen" -eq 0 ]; then\n'
        '      [ "$a" = "$xv" ] && xseen=1\n'
        '      continue\n'
        '    fi\n'
        # `--separate-git-dir` applies to init and clone and is the only one
        # that can appear before the verb-specific handling below.
        '    case "$a" in\n'
        '      --separate-git-dir) xwant=1; continue ;;\n'
        '      --separate-git-dir=*) cands="$cands:${a#--separate-git-dir=}"; '
        'continue ;;\n'
        '    esac\n'
        '    case "$xv" in\n'
        '      config)\n'
        '        case "$a" in\n'
        '          -f|--file) xwant=1; continue ;;\n'
        '          --file=*) cands="$cands:${a#--file=}"; continue ;;\n'
        # `--global` and `--system` name no path at all, so the destination has
        # to be reconstructed the way git does it. Both are added; a path that
        # does not exist canonicalises to itself and is checked all the same.
        '          --global)\n'
        '            if [ -n "${GIT_CONFIG_GLOBAL:-}" ]; then\n'
        '              cands="$cands:$GIT_CONFIG_GLOBAL"\n'
        '            else\n'
        '              cands="$cands:${XDG_CONFIG_HOME:-$HOME/.config}/git/'
        'config:$HOME/.gitconfig"\n'
        '            fi\n'
        '            continue ;;\n'
        '          --system)\n'
        '            if [ -n "${GIT_CONFIG_SYSTEM:-}" ]; then\n'
        '              cands="$cands:$GIT_CONFIG_SYSTEM"\n'
        '            else\n'
        '              cands="$cands:/etc/gitconfig"\n'
        '            fi\n'
        '            continue ;;\n'
        '        esac ;;\n'
        '      archive|format-patch)\n'
        '        case "$a" in\n'
        '          -o|--output|--output-directory) xwant=1; continue ;;\n'
        '          --output=*) cands="$cands:${a#--output=}"; continue ;;\n'
        '          --output-directory=*) '
        'cands="$cands:${a#--output-directory=}"; continue ;;\n'
        '        esac ;;\n'
        '    esac\n'
        '    case "$a" in -*) continue ;; esac\n'
        '    xnpos=$((xnpos+1))\n'
        '    if [ "$xnpos" -eq 1 ]; then xsub="$a"; continue; fi\n'
        # The FIRST positional after the sub-command is the destination for
        # `bundle create <file>` and `worktree add|move <path>`.
        '    if [ "$xnpos" -eq 2 ]; then\n'
        '      case "$xv/$xsub" in\n'
        '        bundle/create|worktree/add|worktree/move|worktree/repair)\n'
        '          cands="$cands:$a" ;;\n'
        '      esac\n'
        '    fi\n'
        '  done\n'
        '}\n'
    )


def _bounds_helpers_body() -> str:
    """POSIX-sh: `canon_of <path>` and `bounds_verdict <path>`.

    Split out of the main body because the target is no longer a single path.
    With `GIT_DIR` in play a write has SEVERAL destinations — the git dir, the
    worktree, the object dir, the index — and any one of them landing outside
    the allowed roots is a violation, so each is canonicalised and checked by
    the same code.
    """
    return (
        # Canonicalise, resolving symlinks and `..`, WITHOUT requiring the path
        # to exist: `git init` and `git clone` name a directory that is not
        # there yet, and `GIT_INDEX_FILE` names a file. A path that cannot be
        # canonicalised at all is returned unchanged rather than dropped —
        # dropping it would silently remove a candidate from the check.
        # 🔴 RELATIVE OPERANDS RESOLVE AGAINST `cwdbase` — the directory git
        # will actually be in (`-C`, else `$PWD`) — NOT against `base`.
        #
        # `base` is REASSIGNED for `init`/`clone` to the path they will create,
        # so anchoring here would resolve `--separate-git-dir=../victim/x`
        # against the new repo instead of against the cwd, and land the check
        # one directory away from where the bytes go. An absolute-only matcher
        # is worse still: `config --file ../victim/.git/config` and `worktree
        # add ../victim/wt` are the same capability as their absolute twins and
        # must not be a different code path.
        'canon_of() {\n'
        '  cp_="$1"\n'
        '  case "$cp_" in /*) ;; *) cp_="$cwdbase/$cp_" ;; esac\n'
        '  cd_=$(cd "$cp_" 2>/dev/null && pwd -P) && { printf \'%s\' "$cd_"; '
        'return 0; }\n'
        '  cpar_=$(dirname "$cp_"); cbas_=$(basename "$cp_")\n'
        '  cd_=$(cd "$cpar_" 2>/dev/null && pwd -P) && { printf \'%s/%s\' '
        '"$cd_" "$cbas_"; return 0; }\n'
        # 🔴 NOTHING IN THE PATH RESOLVES. Returning it RAW is only safe while
        # it contains no `..` — a lexical prefix match on a path with `..` in
        # it reports the wrong directory, and reports it as INNOCENT.
        #
        # MEASURED, and found in this file rather than by an auditor:
        #
        #     git init -q  <allowed>/does-not-exist/deeper/../../../victim/planted
        #     -> rc 0, and `<victim>/planted` was created.
        #
        # `does-not-exist` makes both `cd`s fail, so the raw string came back,
        # and the raw string starts with `<allowed>/` — so it matched an
        # allowed root while `realpath` puts it inside the victim. `clone` did
        # the same. This is the `git diff --quiet` shape from claude/RULES.md:
        # a comparison against an operand that is not there reports SAME, not
        # MISSING. An unresolvable path is protected-UNKNOWN, never
        # protected-NO.
        #
        # A path with no `..` is still returned raw, deliberately: `git init
        # a/b/c` creates its intermediate directories, so the third fallback is
        # the NORMAL case for it, and nothing after the last existing prefix
        # can be a symlink. Only `..` can walk out.
        '  case "$cp_" in\n'
        '    */../*|*/..) printf \'//DEVRC-NOGIT-UNRESOLVABLE%s\' "$cp_" '
        '; return 0 ;;\n'
        '  esac\n'
        '  printf \'%s\' "$cp_"\n'
        '}\n'
        # 🔴 PRINTS A VERDICT, IT DOES NOT RETURN ONE — and the caller demands
        # the literal string "ok".
        #
        # An exit-code version of this failed OPEN and was caught doing it: the
        # helpers had not been emitted into the stub, so `out_of_bounds` was a
        # `command not found` returning 127, "non-zero" read as "in bounds",
        # and the shim let a write through with rc=0. Every negative control
        # would have gone green against a guard that had ceased to exist.
        'bounds_verdict() {\n'
        '  ob_="$1"\n'
        '  ob_ok_=0\n'
        '  case "$ob_" in\n'
        f'    {"|".join(ALWAYS_SAFE_PATHS)}) printf \'ok\'; return ;;\n'
        '  esac\n'
        '  oifs_="$IFS"; IFS=":"\n'
        '  for r in $roots; do\n'
        '    [ -n "$r" ] || continue\n'
        '    case "$ob_" in "$r"|"$r"/*) ob_ok_=1; break ;; esac\n'
        '  done\n'
        # Deny wins over allow, always — that ordering is what keeps the policy
        # live in the nix sandbox, where the tree under test sits inside
        # $TMPDIR and would otherwise be inside an allowed root.
        '  for r in $denied; do\n'
        '    [ -n "$r" ] || continue\n'
        '    case "$ob_" in "$r"|"$r"/*) ob_ok_=0; break ;; esac\n'
        '  done\n'
        '  IFS="$oifs_"\n'
        '  [ "$ob_ok_" -eq 1 ] && printf \'ok\' || printf \'bad\'\n'
        '}\n'
    )


# --------------------------------------------------------------------------- #
# the shim body
# --------------------------------------------------------------------------- #
def git_body(real: str, log: Path, allowed_roots: str = "",
             denied_roots: str = "", stub_dir: Path | str = "",
             mode: str = MODE_BLOCK) -> str:
    """The POSIX-sh body of the `git` shim. No shebang — write_exec owns it.

    The parse mirrors git's own: skip leading global options, take the FIRST
    non-flag token as the verb, and decide on that. `-C <dir>`, `--git-dir=`,
    `--work-tree=` and `-c key=value` are captured while skipping, because they
    are what move the target away from the process's cwd.

    🔴 THE TARGET IS RESOLVED, NOT ASSUMED. `rev-parse` is asked (via the REAL
    binary, by absolute path, so this cannot recurse), and the answer is
    canonicalised with `pwd -P`. Comparing the raw `-C` string against the
    allowed roots would be walkable by `-C /tmp/x/../../home/zach/workspace/
    devrc`, and by a symlinked tmpdir.

    🔴 `mode` IS A BAKE-TIME ARGUMENT. It used to be read from the
    environment, where `DEVRC_TEST_GIT_MODE=audit` silently disarmed the whole
    policy — measured, rc 0, victim commit. Nothing an inherited environment
    carries can change it now.
    """
    verbs = "|".join(v for v in GIT_READ_VERBS
                     if v not in GIT_LOCK_FREE_READ_VERBS)
    lockfree = "|".join(GIT_LOCK_FREE_READ_VERBS)
    flags = "|".join(GIT_READ_FLAGS)
    remote_write = "|".join(GIT_REMOTE_WRITE_VERBS)
    audit = "1" if mode == MODE_AUDIT else "0"
    return (
        _exec_path_export(Path(stub_dir)) +
        _bounds_helpers_body() +
        _dual_is_read_body() +
        _injected_config_body() +
        _classify_injected_body() +
        _exec_env_body() +
        _extra_destination_body() +
        _gitq_body(real) +
        _push_destination_body(real) +
        _refuse_body(real) +
        f'log="{log}"\n'
        f'real="{real}"\n'
        f'audit={audit}\n'
        # 🔴 THE ROOTS ARE BAKED IN AT INSTALL TIME; THE ENV ONLY WIDENS THEM.
        #
        # MEASURED on the full suite: three tests build a REPLACING `env=`
        # dict for a perfectly hermetic `git init` in their own tmp_path.
        # `PATH` still reaches the shim, so the shim ran — with no roots at
        # all, and refused a legitimate fixture. Fail-closed was the right
        # DIRECTION and still the wrong ANSWER.
        f'roots="${{{ROOTS_ENV}:-{allowed_roots}}}"\n'
        # 🔴 DENY IS A UNION, NOT AN OVERRIDE — asymmetric on purpose. A test
        # may widen what it is allowed to write; nothing may un-deny the tree
        # under test, including an empty or absent variable.
        f'denied="{denied_roots}${{{DENY_ROOTS_ENV}:+:${{{DENY_ROOTS_ENV}}}}}"\n'
        '\n'
        # ---- the handshake: answered before anything else ----
        # 🔴 It must NOT be forwardable to the real binary, or a plain `git`
        # would answer it with an error and a caller could misread the failure.
        # 🔴 THE BAKED LITERALS, NOT `$roots` / `$denied`. Those two carry the
        # ENV override, so a handshake reading them would report whatever the
        # asking process happens to export — which tells the caller nothing
        # about the shim. `_inherited_stub_dir()` needs the shim's OWN floor:
        # deny is `baked ∪ env`, so a baked floor containing the tree under
        # test is a property no environment can take away.
        'if [ "${1:-}" = "' + HANDSHAKE_FLAG + '" ]; then\n'
        f'  printf \'{HANDSHAKE_MAGIC} %s\\n\' "{policy_fingerprint()}"\n'
        f'  printf \'roots=%s\\n\' \'{allowed_roots}\'\n'
        f'  printf \'denied=%s\\n\' \'{denied_roots}\'\n'
        f'  printf \'audit=%s\\n\' "{audit}"\n'
        '  exit 0\n'
        'fi\n'
        '\n'
        # ---- parse leading global options: the target, the verb, and -c ----
        'cdir=""\n'
        'gdir=""\n'
        'wtree=""\n'
        'verb=""\n'
        'want=""\n'
        'inj=""\n'
        'cargs=""\n'
        'nglobal=0\n'
        'for a in "$@"; do\n'
        '  if [ -n "$want" ]; then\n'
        '    case "$want" in\n'
        '      C) if [ -n "$cdir" ]; then case "$a" in /*) cdir="$a" ;; '
        '*) cdir="$cdir/$a" ;; esac; else cdir="$a"; fi ;;\n'
        '      gitdir) gdir="$a" ;;\n'
        '      worktree) wtree="$a" ;;\n'
        # 🔴 `-c key=value` is config injection and is collected, not skipped.
        # The previous revision spelled this `-c) want=skip`, which is how
        # `-c core.hooksPath=<victim>/hooks` and `-c remote.origin.url=<victim>`
        # both reached the real binary unexamined.
        '      cfg) inj="$inj$a\n"; cargs="$cargs -c $a" ;;\n'
        '    esac\n'
        '    want=""\n'
        # 🔴 COUNTS ARGV ENTRIES, NOT OPTIONS. An option's VALUE is an argv
        # entry too, and counting only the options made `nglobal` smaller than
        # the number of tokens before the verb — so the read-flag scan below
        # stopped early and `git -c k=v --version` was classified as a write.
        # Fail-closed, but a false positive on a command that prints and exits.
        '    nglobal=$((nglobal+1))\n'
        '    continue\n'
        '  fi\n'
        '  case "$a" in\n'
        '    -C) want=C ;;\n'
        '    --git-dir) want=gitdir ;;\n'
        '    --work-tree) want=worktree ;;\n'
        '    --git-dir=*) gdir="${a#--git-dir=}" ;;\n'
        '    --work-tree=*) wtree="${a#--work-tree=}" ;;\n'
        '    -c) want=cfg ;;\n'
        # `--config-env=key=VAR` takes its VALUE from the named variable.
        '    --config-env=*)\n'
        '      ce="${a#--config-env=}"\n'
        '      cek="${ce%%=*}"; cev="${ce#*=}"\n'
        '      eval "cevv=\\${$cev:-}"\n'
        '      inj="$inj$cek=$cevv\n"\n'
        '      cargs="$cargs $a" ;;\n'
        '    -*) ;;\n'
        '    *) verb="$a"; break ;;\n'
        '  esac\n'
        '  nglobal=$((nglobal+1))\n'
        'done\n'
        '\n'
        # ---- position-independent read flags, BEFORE the verb only ----
        # See GIT_READ_FLAGS for the `commit -m -h` measurement that forced the
        # scan to stop at the verb.
        'gi=0\n'
        'for a in "$@"; do\n'
        '  [ "$gi" -ge "$nglobal" ] && break\n'
        '  gi=$((gi+1))\n'
        '  case "$a" in\n'
        f'    {flags})\n'
        '      printf \'git(read-flag) %s\\n\' "$*" >> "$log"\n'
        '      exec "$real" "$@" ;;\n'
        '  esac\n'
        'done\n'
        '\n'
        # ---- `<verb> -h` / `<verb> --help` as the SINGLE remaining token ----
        # This is the form that prints usage and exits without touching a repo.
        # Requiring it to be the last and only token is what keeps `commit -m -h`
        # out: there, `-h` is the VALUE of `-m`.
        'if [ "$#" -ge 2 ]; then\n'
        '  eval "lastarg=\\${$#}"\n'
        '  eval "prevarg=\\${$(($#-1))}"\n'
        '  case "$lastarg" in\n'
        '    -h|--help)\n'
        '      if [ "$prevarg" = "$verb" ]; then\n'
        '        printf \'git(read-help) %s\\n\' "$*" >> "$log"\n'
        '        exec "$real" "$@"\n'
        '      fi ;;\n'
        '  esac\n'
        'fi\n'
        '\n'
        # ---- no verb at all: `git` (or only flags) prints usage and exits ----
        # It writes nothing, so refusing it would be a false positive — and a
        # confusing one, since `git` alone answering "VIOLATION" reads as the
        # guard being broken.
        'if [ -z "$verb" ]; then\n'
        '  printf \'git(no-verb) %s\\n\' "$*" >> "$log"\n'
        '  exec "$real" "$@"\n'
        'fi\n'
        '\n'
        'allowed=1\n'
        'why=""\n'
        'cands=""\n'
        '\n'
        # ---- config/env INJECTION, checked before the read fast path ----
        # 🔴 BEFORE the read path on purpose. `core.fsmonitor` and
        # `GIT_EXTERNAL_DIFF` both fire during `status` and `diff`, which are
        # reads — so a check placed after the read dispatch would never see the
        # measured GIT_EXTERNAL_DIFF execution at all.
        'collect_env_config\n'
        'check_exec_env\n'
        '[ "$allowed" -eq 1 ] && classify_injected\n'
        'if [ "$allowed" -eq 0 ]; then\n'
        '  canon="(config injection)"\n'
        '  refuse "$@"\n'
        'fi\n'
        '\n'
        # ---- READ verbs exec the real binary, wherever they point ----
        'case "$verb" in\n'
        f'  {lockfree})\n'
        # 🔴 `--no-optional-locks` PREPENDED. `status` rewrites the target's
        # `.git/index`; measured, the victim's index hash moved. With the flag
        # the index is byte-identical and stdout is unchanged.
        '    printf \'git(read-nolock) %s\\n\' "$*" >> "$log"\n'
        '    exec "$real" --no-optional-locks "$@" ;;\n'
        f'  {verbs})\n'
        '    printf \'git(read) %s\\n\' "$*" >> "$log"\n'
        '    exec "$real" "$@" ;;\n'
        'esac\n'
        '\n'
        # ---- resolve the EFFECTIVE target repo ----
        'base="$cdir"\n'
        '[ -n "$base" ] || base="$PWD"\n'
        # The directory git will run in. `base` is about to be reassigned for
        # init/clone; this one is not, and it is what every relative operand
        # resolves against. See `canon_of`.
        'cwdbase="$base"\n'
        'case "$cwdbase" in /*) ;; *) cwdbase="$PWD/$cwdbase" ;; esac\n'
        'pathverb=0\n'
        '\n'
        # `init` / `clone` are handed the path they will CREATE. Using the cwd
        # for these refuses every fixture that builds itself a clone from a
        # suite whose cwd is the repo — measured, and the reason this block
        # exists.
        'case "$verb" in\n'
        f'  {"|".join(GIT_TARGET_IS_ARG_VERBS)})\n'
        '    if [ "$verb" = "clone" ]; then\n'
        f'      valopts="{" ".join(GIT_CLONE_VALUE_OPTS)}"\n'
        '      wantpos=2\n'
        '    else\n'
        f'      valopts="{" ".join(GIT_INIT_VALUE_OPTS)}"\n'
        '      wantpos=1\n'
        '    fi\n'
        '    seen=0\n'
        '    npos=0\n'
        '    src=""\n'
        '    tgt=""\n'
        '    skipnext=0\n'
        '    for a in "$@"; do\n'
        '      if [ "$skipnext" -eq 1 ]; then skipnext=0; continue; fi\n'
        '      if [ "$seen" -eq 0 ]; then\n'
        '        [ "$a" = "$verb" ] && seen=1\n'
        '        continue\n'
        '      fi\n'
        '      case "$a" in\n'
        '        --*=*) continue ;;\n'
        '        -*)\n'
        '          for v in $valopts; do\n'
        '            if [ "$a" = "$v" ]; then skipnext=1; break; fi\n'
        '          done\n'
        '          continue ;;\n'
        '      esac\n'
        '      npos=$((npos+1))\n'
        '      [ "$npos" -eq 1 ] && src="$a"\n'
        '      if [ "$npos" -eq "$wantpos" ]; then tgt="$a"; break; fi\n'
        '    done\n'
        # `git clone <src>` with no destination writes to <cwd>/<basename src>;
        # `git init` with no path writes to the cwd.
        '    if [ -z "$tgt" ] && [ "$verb" = "clone" ] && [ -n "$src" ]; then\n'
        '      tgt="${src%/}"\n'
        '      tgt="${tgt##*/}"\n'
        '      tgt="${tgt%.git}"\n'
        '    fi\n'
        '    if [ -n "$tgt" ]; then\n'
        '      case "$tgt" in\n'
        '        /*) base="$tgt" ;;\n'
        '        *) base="$base/$tgt" ;;\n'
        '      esac\n'
        '    fi\n'
        '    pathverb=1\n'
        '    ;;\n'
        'esac\n'
        'top=""\n'
        'if [ "$pathverb" -eq 1 ]; then\n'
        '  top="$base"\n'
        'elif [ -n "$wtree" ]; then\n'
        '  top="$wtree"\n'
        'elif [ -n "$gdir" ]; then\n'
        '  top="$gdir"\n'
        'else\n'
        '  top=$("$real" -C "$base" rev-parse --show-toplevel 2>/dev/null) '
        '|| top=""\n'
        'fi\n'
        '[ -n "$top" ] || top="$base"\n'
        'canon=$(canon_of "$top")\n'
        '\n'
        # ---- gather EVERY place this write could land ----
        'cands="$canon$cands"\n'
        'if [ "$pathverb" -eq 0 ]; then\n'
        # 🔴 ASK GIT, DO NOT INFER — this is the IDENTITY half. Answered in the
        # SAME cwd and environment (and, via `gitq`, with the SAME injected
        # `-c` options) the real call will use.
        '  info=$(gitq rev-parse --path-format=absolute '
        '--git-dir --git-common-dir --git-path objects 2>/dev/null) || info=""\n'
        # 🔴 A LINE-WISE read, not `for line in $info`. The previous revision
        # word-split on the default IFS while every other list here is
        # colon-split, so a repository path containing a space produced two
        # bogus candidates and lost the real one.
        '  oifs2="$IFS"\n'
        '  IFS="\n"\n'
        '  for line in $info; do\n'
        '    [ -n "$line" ] && cands="$cands:$line"\n'
        '  done\n'
        '  IFS="$oifs2"\n'
        'fi\n'
        # 🔴 BOTH `--git-dir` AND `--work-tree`, ALWAYS — not whichever one
        # `top` happened to take. The `top` chain picks work-tree over git-dir,
        # so `--git-dir=<victim>/.git --work-tree=<allowed>` would have been
        # judged entirely on the allowed half. They are two destinations, and
        # the check is over a SET; the same matcher that bounds the env twins
        # bounds these.
        '[ -n "$gdir" ] && cands="$cands:$gdir"\n'
        '[ -n "$wtree" ] && cands="$cands:$wtree"\n'
        # ---- destinations that are not the repo the command runs in ----
        'collect_extra_dests "$verb" "$@"\n'
        # 🔴 THE ENVIRONMENT, EXPLICITLY — the BYTE-REDIRECTION half, and NOT
        # redundant with the identity half above. `GIT_WORK_TREE=<victim>
        # git -C <innocent fixture> checkout -f HEAD` leaves identity
        # completely truthful while the FILES land in the victim's tree.
        + "".join(
            f'[ -n "${{{v}:-}}" ] && cands="$cands:${{{v}}}"\n'
            for v in GIT_LOCATION_ENV) +
        '\n'
        # ---- EVERY candidate must be in bounds ----
        # 🔴 DEDUPED. `canon_of` and `bounds_verdict` each cost a subshell
        # fork, and the candidate set repeats heavily: `--git-dir` and
        # `--git-common-dir` are the SAME path for every non-linked worktree,
        # and the resolved toplevel is usually its parent. Skipping a repeat
        # cannot change the verdict — the check is a conjunction over the set.
        #
        # ⚠ THE TWO `rev-parse` CALLS ARE **NOT** MERGEABLE, and that was
        # measured rather than assumed. `rev-parse --path-format=absolute
        # --show-toplevel --git-dir --git-common-dir --git-path objects`
        # returns them all in one process — but in a BARE repo, and under
        # `GIT_DIR=<a bare repo>`, `--show-toplevel` makes the whole call exit
        # 128 with "this operation must be run in a work tree", so the shim
        # would lose EVERY candidate in exactly the redirection shape it exists
        # to catch. Two calls it is.
        'oldifs="$IFS"\n'
        'IFS=":"\n'
        'seencands=":"\n'
        'for c in $cands; do\n'
        '  [ -n "$c" ] || continue\n'
        '  case "$seencands" in *":$c:"*) continue ;; esac\n'
        '  seencands="$seencands$c:"\n'
        '  cc=$(canon_of "$c")\n'
        # Demand the literal "ok". Anything else — "bad", empty output from a
        # helper that failed, a killed subshell — refuses the call.
        '  if [ "$(bounds_verdict "$cc")" != "ok" ]; then\n'
        '    allowed=0\n'
        '    why="write would land in $cc, outside the allowed roots"\n'
        '    break\n'
        '  fi\n'
        'done\n'
        'IFS="$oldifs"\n'
        '\n'
        # ---- a push must ALSO have an allowed destination ----
        'case "$verb" in\n'
        f'  {remote_write})\n'
        '    dest=$(push_dest "$verb" "$cdir" "$@")\n'
        '    if [ -z "$dest" ]; then\n'
        '      allowed=0\n'
        '      why="push destination is not a local path (it leaves this '
        'machine)"\n'
        '    else\n'
        '      dcanon=$(canon_of "$dest")\n'
        '      if [ "$(bounds_verdict "$dcanon")" != "ok" ]; then\n'
        '        allowed=0\n'
        '        why="push destination $dcanon is outside the allowed roots"\n'
        '      fi\n'
        '    fi ;;\n'
        'esac\n'
        '\n'
        'if [ "$allowed" -eq 1 ]; then\n'
        '  printf \'git(write-ok) %s :: %s\\n\' "$canon" "$*" >> "$log"\n'
        '  exec "$real" "$@"\n'
        'fi\n'
        '\n'
        # The target is out of bounds. A DUAL verb may still be in its READING
        # form — `git remote -v`, `git config --get …`, `git branch -a
        # --format=…`, `git worktree list` — all of which this suite runs
        # against the real repo deliberately. Consulting the classifier only
        # HERE means it can never rescue an actual write.
        #
        # 🔴 It may NOT rescue a call refused for config injection, which is
        # why that path calls `refuse` directly above rather than falling
        # through: `git -c alias.x=<cmd> config --get foo` is a "read form"
        # whose alias would still have executed.
        'case "$verb" in\n'
        f'  {"|".join(GIT_DUAL_VERBS)})\n'
        '    if dual_is_read "$verb" "$@"; then\n'
        '      printf \'git(read-dual) %s :: %s\\n\' "$canon" "$*" >> "$log"\n'
        '      exec "$real" "$@"\n'
        '    fi ;;\n'
        'esac\n'
        'refuse "$@"\n'
    )


def _refuse_body(real: str) -> str:
    """sh: `refuse <argv…>` — the loud, distinctive, fail-closed exit.

    🔴 TAKES THE ARGV RATHER THAN READING A GLOBAL. In audit mode it must
    re-exec the ORIGINAL command, and a function called with no arguments has
    an empty `$@` — it would have exec'd a bare `git`, silently turning every
    audited violation into a usage message and making an audit run's log a
    record of commands that never ran.
    """
    return (
        'refuse() {\n'
        '  printf \'git(blocked) %s :: %s\\n\' "$canon" "$*" >> "$log"\n'
        '  if [ "$audit" -eq 1 ]; then\n'
        f'    exec "{real}" "$@"\n'
        '  fi\n'
        f'  printf \'%s\\n\' "{BANNER}" >&2\n'
        '  printf \'  a test tried to run a git WRITE against a repo it does '
        'not own.\\n\' >&2\n'
        '  printf \'  verb:    %s\\n\' "$verb" >&2\n'
        '  printf \'  target:  %s\\n\' "$canon" >&2\n'
        '  printf \'  reason:  %s\\n\' "$why" >&2\n'
        '  printf \'  argv:    git %s\\n\' "$*" >&2\n'
        '  printf \'  cwd:     %s\\n\' "$PWD" >&2\n'
        '  printf \'  allowed: %s\\n\' "$roots" >&2\n'
        '  printf \'  This is scripts/testlib/gitwrite.py. The suite once drove '
        'commit/branch -m/\\n\' >&2\n'
        '  printf \'  push against the operator\\047s real clone and destroyed '
        'main on the\\n\' >&2\n'
        '  printf \'  remote. Point this call at your own tmp_path, or widen '
        f'{ROOTS_ENV}.\\n\' >&2\n'
        f'  exit {BLOCK_EXIT}\n'
        '}\n'
    )


def policy_fingerprint() -> str:
    """A hash of THIS policy's shim, with every install-specific value blanked.

    🔴 The handshake token. It is what makes `_inherited_stub_dir()` a content
    check rather than a filename check: any shim generated by this module
    answers with the same value, a shim generated by an OLDER revision answers
    with a different one, and a foreign `git` answers with nothing at all.

    Computed over the body with the real-binary path, log path, roots and stub
    dir replaced by placeholders, so two installs of the same policy agree
    while a change to the policy's LOGIC does not.
    """
    body = _skeleton_body()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _skeleton_body() -> str:
    """The shim body with every install-specific value replaced by a marker.

    Kept separate from `git_body` so `policy_fingerprint()` cannot recurse into
    it — `git_body` embeds the fingerprint, so hashing `git_body`'s own output
    would be self-referential.
    """
    parts = [
        _bounds_helpers_body(),
        _dual_is_read_body(),
        _injected_config_body(),
        _classify_injected_body(),
        _exec_env_body(),
        _extra_destination_body(),
        _push_destination_body("|REAL|"),
        _gitq_body("|REAL|"),
        _refuse_body("|REAL|"),
        repr(GIT_READ_VERBS), repr(GIT_LOCK_FREE_READ_VERBS),
        repr(GIT_READ_FLAGS), repr(GIT_DUAL_VERBS), repr(GIT_LOCATION_ENV),
        repr(GIT_EXEC_ENV), repr(GIT_REMOTE_WRITE_VERBS),
        repr(GIT_TARGET_IS_ARG_VERBS), repr(ALWAYS_SAFE_PATHS),
        str(BLOCK_EXIT), BANNER, HANDSHAKE_MAGIC,
    ]
    return "\n".join(parts)


def exec_path_farm(stub_dir: Path, real_git: str) -> Path | None:
    """Build the `GIT_EXEC_PATH` directory: every real helper, plus THE SHIM.

    🔴 THIS IS WHAT CLOSES THE PATH ESCAPE, and the escape was real. MEASURED:

        $ git -c 'alias.p=!echo PATH[0]=$(echo $PATH|cut -d: -f1)' p
        PATH[0]=/nix/store/…-git-2.55.0/libexec/git-core

    git prepends its exec-path to `PATH` for every child it spawns, and that
    directory ships a real `git`. So "first on PATH" was never true for a
    grandchild of git itself, and `git -C <fixture> -c 'alias.x=!git -C
    <victim> commit …' x` wrote the victim a commit with the shim installed.

    Pointing `GIT_EXEC_PATH` at a farm makes git prepend THIS directory
    instead. Measured through the farm: PATH[0] inside an alias is the farm,
    and `git` there is the shim. The ~184 helpers are symlinked so git still
    finds `git-remote-https`, `git-sh-setup` and the rest.

    Returns None when the exec path cannot be read — the caller then leaves
    `GIT_EXEC_PATH` alone rather than pointing git at an incomplete farm, which
    would break git outright instead of guarding it.
    """
    import subprocess
    try:
        out = subprocess.run([real_git, "--exec-path"], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    src = out.stdout.strip()
    if out.returncode != 0 or not src or not Path(src).is_dir():
        return None
    farm = exec_path_dir(stub_dir)
    farm.mkdir(parents=True, exist_ok=True)
    for entry in Path(src).iterdir():
        # `git` itself is the one name the farm must NOT inherit — replacing it
        # with the shim is the entire point.
        if entry.name == "git":
            continue
        link = farm / entry.name
        if link.is_symlink() or link.exists():
            continue
        try:
            link.symlink_to(entry)
        except OSError:  # pragma: no cover — a name that cannot be linked
            continue
    shim = farm / "git"
    if not shim.exists():
        # A RELATIVE link, so the farm keeps working if the stub dir is moved
        # or bind-mounted somewhere else.
        shim.symlink_to(Path("..") / "git")
    return farm


def install(stub_dir: Path, allowed: str | None = None,
            denied: str | None = None, mode: str = MODE_BLOCK) -> Path:
    """Write the `git` shim into `stub_dir`; return the log path.

    🔴 CALL THIS BEFORE PREPENDING `stub_dir` TO PATH — the shim resolves the
    real binary through `shutil.which`, so a stub dir already on PATH would
    make it exec ITSELF forever. The assertion below turns that ordering
    mistake into a named failure instead of a hang.
    """
    stub_dir = Path(stub_dir)
    stub_dir.mkdir(parents=True, exist_ok=True)
    log = log_path(stub_dir)

    real_git = shutil.which("git")
    if real_git is None:
        # Nothing to shadow and nothing to forward to. Fabricating git's
        # answers is the one thing this module must never do, so it installs
        # nothing and says so.
        return log
    if Path(real_git).parent == stub_dir:
        raise AssertionError(
            "install() resolved git to its own stub dir — it was called AFTER "
            "stub_dir was put on PATH, and the shim would exec itself forever")
    if Path(real_git).parent == exec_path_dir(stub_dir):
        raise AssertionError(
            "install() resolved git to its own GIT_EXEC_PATH farm — the farm "
            "is on PATH, which it must never be")
    write_exec(stub_dir / "git",
               git_body(real_git, log,
                        allowed_roots=default_roots() if allowed is None
                        else allowed,
                        denied_roots=denied_roots() if denied is None
                        else denied,
                        stub_dir=stub_dir, mode=mode))
    exec_path_farm(stub_dir, real_git)
    return log


def recorded(stub_dir: Path) -> list[str]:
    """Every intercepted git call so far, as `git(<class>) …` lines."""
    log = log_path(stub_dir)
    if not log.exists():
        return []
    return [ln for ln in log.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def blocked(stub_dir: Path) -> list[str]:
    """The recorded git calls that were REFUSED (or would have been, in audit)."""
    return [ln for ln in recorded(stub_dir) if ln.startswith("git(blocked)")]


def handshake(stub_dir: Path) -> dict[str, str] | None:
    """Ask the shim in `stub_dir` to prove it is THIS policy's.

    Returns the parsed answer, or None when the directory holds something that
    is not this policy's shim — a foreign `git`, an older revision, or nothing.

    🔴 A CONTENT CHECK, NOT A FILENAME CHECK. The previous revision adopted any
    directory containing a file called `git` and skipped installation; measured
    with a plain passthrough script there, a write to the repo-under-test
    landed rc 0 with no banner and the session still reported "1 passed".
    """
    import subprocess
    exe = Path(stub_dir) / "git"
    if not exe.exists():
        return None
    try:
        out = subprocess.run([str(exe), HANDSHAKE_FLAG], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    lines = out.stdout.splitlines()
    if not lines or not lines[0].startswith(HANDSHAKE_MAGIC + " "):
        return None
    got = lines[0].split(" ", 1)[1].strip()
    if got != policy_fingerprint():
        return None
    answer = {"fingerprint": got}
    for ln in lines[1:]:
        if "=" in ln:
            k, v = ln.split("=", 1)
            answer[k.strip()] = v.strip()
    return answer


def default_roots(*extra: os.PathLike | str) -> str:
    """The `ROOTS_ENV` value for a session: the tmp roots a test may write in.

    TMPDIR is included because pytest's `tmp_path` lives under it, and because
    the nix build sandbox puts its whole world there. Each root is
    canonicalised — an uncanonicalised root cannot match a canonicalised
    target, which would block every legitimate write instead of allowing it.
    """
    roots: list[str] = []
    for cand in (*extra, os.environ.get("TMPDIR"), "/tmp"):
        if not cand:
            continue
        try:
            r = str(Path(cand).resolve())
        except OSError:  # pragma: no cover — unresolvable path
            continue
        if r not in roots:
            roots.append(r)
    return ":".join(roots)


def repo_root() -> Path:
    """The tree this suite is running out of — `scripts/testlib/` → repo root.

    🔴 DERIVED FROM THIS FILE'S OWN LOCATION, never from `git rev-parse` and
    never from a `$DEVRC`-shaped env var. Both of those are resolutions the
    incident showed can point somewhere else: `rev-parse` answers about the
    process's cwd, and an env var that is unset — or, the case that hid,
    present-but-EMPTY under `${VAR:-default}` — falls back to
    `~/workspace/devrc`. `__file__` is the one anchor that cannot be redirected
    by the environment the guard is supposed to be policing.
    """
    return Path(__file__).resolve().parents[2]


def denied_roots(*extra: os.PathLike | str) -> str:
    """The `DENY_ROOTS_ENV` value: roots no test may write to, ever.

    The tree under test, plus anything a caller adds. Canonicalised for the
    same reason the allow roots are — an uncanonicalised deny root silently
    matches nothing, which is a deny-list that denies nothing.
    """
    roots: list[str] = []
    for cand in (repo_root(), *extra):
        if not cand:
            continue
        try:
            r = str(Path(cand).resolve())
        except OSError:  # pragma: no cover — unresolvable path
            continue
        if r not in roots:
            roots.append(r)
    return ":".join(roots)


def main(argv: list[str] | None = None) -> int:
    """`python -m testlib.gitwrite <dir>` — install the shim, print the log path.

    The same door `testlib.nolaunch` opens for `scripts/run-tests.sh`: one
    install for a whole run, before any target starts, so the NON-pytest
    targets (which load no plugin) are covered by the same code rather than by
    a second implementation of it.
    """
    import sys as _sys
    args = list(_sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m testlib.gitwrite <stub-dir>", file=_sys.stderr)
        return 2
    print(install(Path(args[0])))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via the plugin
    raise SystemExit(main())
