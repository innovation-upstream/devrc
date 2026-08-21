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
ENVIRONMENT, in two sub-classes that do not cover each other:

  * IDENTITY redirection (`GIT_DIR`, `GIT_COMMON_DIR`) — git reports a
    different repo. Caught by asking git which repo it will actually use.
  * BYTE redirection (`GIT_WORK_TREE`, `GIT_INDEX_FILE`,
    `GIT_OBJECT_DIRECTORY`, the alternates, `GIT_CONFIG_GLOBAL/SYSTEM`) —
    identity stays innocent and TRUTHFUL and only the destination moves.
    `GIT_WORK_TREE=<victim> git -C <fixture> checkout -f HEAD` exits 0 and
    writes the victim's working tree. **Resolving the repo cannot see this.**

See `GIT_LOCATION_ENV` for the ledger and the two entries that were wrong.

🔴 IT IS INVISIBLE BY CONSTRUCTION
-----------------------------------
The remote repoint was observed only by running `git remote -v` in the real
clone WHILE the suite ran; the test restores the URL afterwards, so every
after-the-fact check reports a clean repo. A guard that inspects state after
the run therefore cannot see this class at all — the interception has to happen
at the moment of the call, which is what this module does.

WHY A PATH SHIM AND NOT A `subprocess` MONKEYPATCH
--------------------------------------------------
The same reason `testlib/nolaunch.py` is a PATH shim, and it is decisive here
rather than merely tidy: a `git` that damages something is very often run by a
GRANDCHILD. The suite spawns shell scripts, and those scripts run `git`; a
`subprocess.Popen` patched inside the pytest process never sees any of it. Only
a shim that is first on `PATH` — inherited by every descendant, at any depth —
can. The in-process layer exists too, mirroring `nolaunch_plugin`'s L2, but
purely to catch an ABSOLUTE-path `git` that PATH cannot shadow.

⚠ An earlier revision of this paragraph named `scripts/drift-check.sh`'s
`ssh … bash -s` leg (where `DRIFT_REPO` is unset and
`${DRIFT_REPO:-$HOME/workspace/devrc}` resolves to the real clone) as the
motivating caller. That theory is RETRACTED and should not be re-derived: the
test harness's stub `ssh` writes the payload to `/dev/null` and never executes
it, and the payloads are read-only regardless. The empty-override sites in
`drift-check.sh` and `ship.sh` are a genuine latent hazard — all five use `:-`,
which falls back on EMPTY as well as unset — but no caller passes an empty
value, so they were not the mechanism. The PATH-shim argument stands on the
grandchild point alone.

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
fixture pushing to `origin` would have written into their actual refs. Location
of the *source* repo therefore does not bound the blast radius of a push, and
`_push_destination_body` resolves the destination URL and applies the same
root check to it.
"""
from __future__ import annotations

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
# ---------------------------------------------------------------------------
GIT_READ_VERBS = (
    "status", "log", "show", "diff", "cat-file", "ls-files", "ls-tree",
    "ls-remote", "rev-parse", "rev-list", "describe", "blame", "grep",
    "shortlog", "show-ref", "symbolic-ref", "for-each-ref", "merge-base",
    "name-rev", "check-ignore", "check-attr", "count-objects", "verify-pack",
    "var", "help", "version", "annotate", "whatchanged", "cherry",
    "diff-tree", "diff-index", "diff-files", "hash-object", "patch-id",
    "get-tar-commit-id", "check-mailmap", "column", "interpret-trailers",
)

# Read-only global FLAGS: they answer and exit whatever else is on the line,
# so they are safe in any position (unlike a verb, which is positional).
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
# the permanently-red gate claude/RULES.md warns about: the guard would have
# been switched off within a day and the real hazard would be back.
#
# `init` and `clone` CREATE a repo at a path they are handed, so the path is
# what must be bounded. The option tables below exist because the target is
# "the first positional after the verb" only once options with VALUES are
# skipped — `git init -b main` would otherwise resolve its target to `main`,
# reintroducing the same false positive in a narrower shape.
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
                  "submodule", "stash", "notes")

# 🔴 THE ENVIRONMENT IS PART OF THE TARGET — `-C` IS NOT PROTECTIVE.
#
# MEASURED, and it invalidated this module's first design outright:
#
#     $ GIT_DIR=<victim>/.git git -C <fixture> rev-parse --show-toplevel
#     <fixture>                      <- the innocent answer
#     $ GIT_DIR=<victim>/.git git -C <fixture> rev-parse --absolute-git-dir
#     <victim>/.git                  <- where the write ACTUALLY goes
#
#     $ GIT_DIR=<victim>/.git git -C <fixture> commit --allow-empty -m PWNED
#     rc=0, and <victim>'s commit count went 1 -> 2.
#
# So an ambient `GIT_DIR` redirects the refs and objects while `--show-toplevel`
# still reports the directory `-C` names. A guard that resolves the target from
# the ARGUMENTS is bypassed completely, and — this is the part that matters —
# so is every audit that concluded "absolute `-C`, therefore fixture-scoped".
# Two independent reviews of the suite reached that conclusion by checking the
# wrong property.
#
# This is the reproduced mechanism of the incident: replaying a repo-cos
# fixture with `GIT_DIR` naming a victim reproduced all four observed damages —
# a `commit`, the `main → trunk` rename, a `user.email` rewrite and a push to
# the victim's own origin.
#
# The defence is therefore NOT to enumerate argument shapes. It is to ask GIT
# ITSELF where it is about to write (`rev-parse --absolute-git-dir` and friends,
# which honour every redirection vector including ones not listed below), and
# to check the listed variables explicitly on top of that.
#
# 🔴 THIS LEDGER IS KNOWN-INCOMPLETE, BY MEASUREMENT. It was built from the
# installed git's own `man git` — 76 `GIT_*` names — and that page does NOT
# document `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>`,
# which this git demonstrably honours (injecting `user.name` through them
# works). A family the binary obeys was absent from the page describing it, so
# the list below cannot be trusted as closed — which is exactly why git's own
# answer is the primary check and this ledger is only the belt.
#
# 🔴 EVERY ENTRY MUST BE A PATH THAT RECEIVES BYTES. Two names were removed
# after the first armed run, because being git-related is not the membership
# rule:
#   * `GIT_NAMESPACE` is a REF NAMESPACE, not a filesystem path. A real
#     namespace ("foo") would have been canonicalised into `$base/foo` and
#     refused — a pure false positive on a variable that cannot redirect a
#     write anywhere.
#   * `GIT_CEILING_DIRECTORIES` RESTRICTS repository discovery. It only ever
#     narrows what git will touch, so treating it as a destination inverts its
#     meaning and refuses the safe case.
# Both were "caught" by a negative control that armed them at a victim PATH —
# which proves only that the check reads variables, not that those variables
# are write targets. The controls were measuring the guard, not the hazard.
GIT_LOCATION_ENV = (
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",  # colon-separated
    "GIT_INDEX_FILE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
)

# 🔴 Paths that are never a repository and never worth protecting.
#
# MEASURED: `/dev/null` accounted for **590 of the 593** firings on the first
# armed run of the full suite — `GIT_CONFIG_GLOBAL=/dev/null` is the standard
# idiom for "this test has no global git config", used throughout this tree and
# by every hermetic harness. Refusing it would have made the policy
# permanently red across `scripts/tests` on day one, and a permanently-red gate
# gets switched off.
#
# It is an exemption for a SINK, not a widening of the roots: `/dev/null`
# discards what is written to it, so a config write aimed there damages
# nothing. `GIT_CONFIG_GLOBAL=<victim>/.gitconfig` is still refused, which is
# the case the negative control actually cares about.
ALWAYS_SAFE_PATHS = ("/dev/null",)

LOG_NAME = "git-calls.log"

# The exit code a blocked write returns. Distinctive on purpose: git itself
# uses 1/128/129, so a test failing with 99 is unambiguously this policy and
# not a git error the reader has to diagnose.
BLOCK_EXIT = 99

# The stderr banner. Pinned verbatim by the guard test — a policy whose
# message can drift is a policy whose firing cannot be asserted on.
BANNER = "DEVRC-NO-REAL-GIT VIOLATION"

# Where `install()` is told which roots a write may target, and which mode to
# run in. Read by the STUB at call time (not baked in) so a test can widen the
# roots for its own fixture without reinstalling the shim.
ROOTS_ENV = "DEVRC_TEST_GIT_ALLOWED_ROOTS"
MODE_ENV = "DEVRC_TEST_GIT_MODE"
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
# That is the two-tier hazard from claude/RULES.md, arriving through the guard
# rather than around it: the dev host would be protected and CI structurally
# blind, so a violation could merge green and only fire on someone's machine.
#
# The deny root is the repo the suite is running out of, resolved at session
# start. It makes "never write to the tree under test" true in BOTH tiers by
# the same code, and it is why the negative control can be aimed at a real
# clone's root without that clone needing to live outside /tmp.
DENY_ROOTS_ENV = "DEVRC_TEST_GIT_DENIED_ROOTS"

# `block` (default) refuses the write. `audit` records it and lets it through.
#
# 🔴 `audit` EXISTS TO BE MEASURED WITH, NOT TO BE SHIPPED WITH. It is how the
# blast radius of this policy was established before it was turned on (every
# git call in the suite, with its resolved target, logged and classified). A
# test pins `block` as the default precisely so an audit run cannot be left on
# by accident — a guard silently in audit mode is a guard that never fires.
MODE_BLOCK = "block"
MODE_AUDIT = "audit"


def log_path(stub_dir: Path) -> Path:
    """Where the shim records every intercepted `git`, one line per call."""
    return Path(stub_dir) / LOG_NAME


def _push_destination_body(real: str) -> str:
    """POSIX-sh that resolves a `push`'s destination to a local path, if any.

    Sets `dest` to a filesystem path when the push target is a local repo, and
    leaves it empty when the target is a URL this check does not bound (ssh://,
    https://, git@host:path). Empty means "not a local path", NOT "safe" — the
    caller treats an unresolvable destination as a violation, because a push
    that leaves the machine is exactly what destroyed the branch.
    """
    return (
        'push_dest() {\n'
        '  pv="$1"; pc="$2"; shift 2\n'
        # 🔴 SKIP TO THE VERB FIRST — the same measured bug as dual_is_read,
        # in a second place. The value of `-C` is a non-flag token, so "the
        # first non-flag argument" is the -C PATH, not the remote. Without
        # this, `git -C <path> push origin trunk` resolved its remote to the
        # literal string "push", `remote get-url push` failed, the destination
        # came back empty, and every legitimate fixture-to-fixture push was
        # refused. Two sites, one misreading — found only by the positive
        # control, since blocking is what the negative controls assert.
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
        '    if [ -n "$pc" ]; then\n'
        f'      rem=$("{real}" -C "$pc" remote 2>/dev/null | head -n 1)\n'
        '    else\n'
        f'      rem=$("{real}" remote 2>/dev/null | head -n 1)\n'
        '    fi\n'
        '  fi\n'
        '  [ -n "$rem" ] || rem=origin\n'
        '  case "$rem" in\n'
        '    */*|.|..) url="$rem" ;;\n'
        '    *)\n'
        '      if [ -n "$pc" ]; then\n'
        f'        url=$("{real}" -C "$pc" remote get-url "$rem" 2>/dev/null)\n'
        '      else\n'
        f'        url=$("{real}" remote get-url "$rem" 2>/dev/null)\n'
        '      fi ;;\n'
        '  esac\n'
        # An empty result means "not a LOCAL path" — never "safe". The caller
        # treats an empty destination as a violation, because a push that
        # leaves the machine is exactly what destroyed the branch.
        '  case "$url" in\n'
        '    ""|*://*|*@*:*) printf \'\' ;;\n'
        '    *) printf \'%s\' "$url" ;;\n'
        '  esac\n'
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
        # refused. The positive control is what caught it; the negative
        # controls were all still green, because misreading the sub-command
        # errs toward "write" and blocking is what they assert.
        '  while [ "$#" -gt 0 ]; do\n'
        '    a="$1"; shift\n'
        '    case "$a" in -*) continue ;; esac\n'
        '    [ "$a" = "$dv" ] && break\n'
        '  done\n'
        # The first non-flag token after the verb — the sub-command for the
        # verbs that have one.
        '  dsub=""\n'
        '  dpos=0\n'
        '  for a in "$@"; do\n'
        '    case "$a" in -*) continue ;; esac\n'
        '    dpos=$((dpos+1))\n'
        '    [ "$dpos" -eq 1 ] && dsub="$a"\n'
        '  done\n'
        '  case "$dv" in\n'
        # `remote` / `remote -v` / `remote show` / `remote get-url` read.
        # add, remove, rename, set-url, set-head, set-branches, prune, update
        # all write, and so does anything not named here.
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
        '    notes)\n'
        '      case "$dsub" in list|show) return 0 ;; *) return 1 ;; esac ;;\n'
        '  esac\n'
        '  return 1\n'
        '}\n'
    )


def _bounds_helpers_body() -> str:
    """POSIX-sh: `canon_of <path>` and `out_of_bounds <path>`.

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
        'canon_of() {\n'
        '  cp_="$1"\n'
        '  case "$cp_" in /*) ;; *) cp_="$base/$cp_" ;; esac\n'
        '  cd_=$(cd "$cp_" 2>/dev/null && pwd -P) && { printf \'%s\' "$cd_"; '
        'return 0; }\n'
        '  cpar_=$(dirname "$cp_"); cbas_=$(basename "$cp_")\n'
        '  cd_=$(cd "$cpar_" 2>/dev/null && pwd -P) && { printf \'%s/%s\' '
        '"$cd_" "$cbas_"; return 0; }\n'
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
        #
        # With a printed verdict, a helper that is missing, crashes, or is
        # killed produces empty output, which is not "ok", so the call is
        # refused. The failure direction is a property of the shape rather than
        # of remembering to check.
        'bounds_verdict() {\n'
        '  ob_="$1"\n'
        '  ob_ok_=0\n'
        # A sink that discards writes is safe wherever it lives.
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


def git_body(real: str, log: Path, allowed_roots: str = "",
             denied_roots: str = "") -> str:
    """The POSIX-sh body of the `git` shim. No shebang — write_exec owns it.

    The parse mirrors git's own: skip leading global options, take the FIRST
    non-flag token as the verb, and decide on that. `-C <dir>`, `--git-dir=`
    and `--work-tree=` are captured while skipping, because they are what move
    the target away from the process's cwd — and a bare `git` with neither is
    the first-class suspect, since it silently targets whatever directory the
    caller happens to be in.

    🔴 THE TARGET IS RESOLVED, NOT ASSUMED. `rev-parse --show-toplevel` is
    asked (via the REAL binary, by absolute path, so this cannot recurse), and
    the answer is canonicalised with `pwd -P`. Comparing the raw `-C` string
    against the allowed roots would be walkable by `-C /tmp/x/../../home/zach/
    workspace/devrc`, and by a symlinked tmpdir — both of which resolve to a
    real repo while spelling something that passes a prefix match.
    """
    verbs = "|".join(GIT_READ_VERBS)
    flags = "|".join(GIT_READ_FLAGS)
    remote_write = "|".join(GIT_REMOTE_WRITE_VERBS)
    return (
        _bounds_helpers_body() +
        _dual_is_read_body() +
        _push_destination_body(real) +
        f'log="{log}"\n'
        f'real="{real}"\n'
        f'mode="${{{MODE_ENV}:-{MODE_BLOCK}}}"\n'
        # 🔴 THE ROOTS ARE BAKED IN AT INSTALL TIME; THE ENV ONLY WIDENS THEM.
        #
        # Reading the policy purely from the environment made it depend on
        # those variables surviving into every descendant — and they do not.
        # MEASURED on the full suite: three tests build a REPLACING `env=`
        # dict (`{HOME, GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM, PATH}`) for a
        # perfectly hermetic `git init` in their own tmp_path. `PATH` still
        # reaches the shim, so the shim ran — with no roots at all, and
        # refused a legitimate fixture. Fail-closed was the right DIRECTION
        # and still the wrong ANSWER: the guard would have gone red on every
        # future env-replacing test, and a permanently-red gate gets switched
        # off.
        #
        # So the installed defaults are the FLOOR and the variables are an
        # override for a test that needs to widen its own roots.
        f'roots="${{{ROOTS_ENV}:-{allowed_roots}}}"\n'
        # 🔴 DENY IS A UNION, NOT AN OVERRIDE — asymmetric on purpose. A test
        # may widen what it is allowed to write; nothing may un-deny the tree
        # under test, including an empty or absent variable.
        f'denied="{denied_roots}${{{DENY_ROOTS_ENV}:+:${{{DENY_ROOTS_ENV}}}}}"\n'
        '\n'
        # ---- position-independent read flags: answer and exit ----
        'for a in "$@"; do\n'
        '  case "$a" in\n'
        f'    {flags})\n'
        '      printf \'git(read-flag) %s\\n\' "$*" >> "$log"\n'
        '      exec "$real" "$@" ;;\n'
        '  esac\n'
        'done\n'
        '\n'
        # ---- parse leading global options for the target, then the verb ----
        'cdir=""\n'
        'gdir=""\n'
        'wtree=""\n'
        'verb=""\n'
        'want=""\n'
        'for a in "$@"; do\n'
        '  if [ -n "$want" ]; then\n'
        '    case "$want" in\n'
        '      C) if [ -n "$cdir" ]; then case "$a" in /*) cdir="$a" ;; '
        '*) cdir="$cdir/$a" ;; esac; else cdir="$a"; fi ;;\n'
        '      gitdir) gdir="$a" ;;\n'
        '      worktree) wtree="$a" ;;\n'
        '    esac\n'
        '    want=""\n'
        '    continue\n'
        '  fi\n'
        '  case "$a" in\n'
        '    -C) want=C ;;\n'
        '    --git-dir) want=gitdir ;;\n'
        '    --work-tree) want=worktree ;;\n'
        '    --git-dir=*) gdir="${a#--git-dir=}" ;;\n'
        '    --work-tree=*) wtree="${a#--work-tree=}" ;;\n'
        '    -c) want=skip ;;\n'
        '    -*) ;;\n'
        '    *) verb="$a"; break ;;\n'
        '  esac\n'
        'done\n'
        '\n'
        # ---- no verb at all: `git` (or only flags) prints usage and exits ----
        # It writes nothing, so refusing it would be a false positive — and a
        # confusing one, since `git` alone answering "VIOLATION" reads as the
        # guard being broken. Stated rather than left implicit, because
        # `nolaunch`'s systemctl stub takes the OPPOSITE choice for a bare
        # invocation and the difference is deliberate: a bare `systemctl` LISTS
        # UNITS (an answer that would have to be fabricated), while a bare
        # `git` only prints its own usage.
        'if [ -z "$verb" ]; then\n'
        '  printf \'git(no-verb) %s\\n\' "$*" >> "$log"\n'
        '  exec "$real" "$@"\n'
        'fi\n'
        '\n'
        # ---- READ verbs exec the real binary, wherever they point ----
        'case "$verb" in\n'
        f'  {verbs})\n'
        '    printf \'git(read) %s\\n\' "$*" >> "$log"\n'
        '    exec "$real" "$@" ;;\n'
        'esac\n'
        '\n'
        # ---- resolve the EFFECTIVE target repo ----
        'base="$cdir"\n'
        '[ -n "$base" ] || base="$PWD"\n'
        'pathverb=0\n'
        '\n'
        # `init` / `clone` are handed the path they will CREATE. Using the cwd
        # for these refuses every fixture that builds itself a clone from a
        # suite whose cwd is the repo — measured, and the reason this block
        # exists. Skip the verb, skip options and their values, and take the
        # positional: the 1st for `init`, the 2nd for `clone` (the 1st being
        # the SOURCE, which is only read).
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
        # `git init` with no path writes to the cwd. Both are resolved rather
        # than waved through — a clone with no destination, run from the repo,
        # lands IN the repo.
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
        'gitargs=""\n'
        '[ -n "$cdir" ] && gitargs="-C $cdir"\n'
        'top=""\n'
        'if [ "$pathverb" -eq 1 ]; then\n'
        # `base` already IS the path init/clone will create. Asking rev-parse
        # would answer about an ENCLOSING repo instead — for `git init
        # /tmp/fix` run from the repo, that is the repo, which is precisely the
        # false positive this branch exists to remove.
        '  top="$base"\n'
        'elif [ -n "$wtree" ]; then\n'
        '  top="$wtree"\n'
        'elif [ -n "$gdir" ]; then\n'
        '  top="$gdir"\n'
        'else\n'
        '  top=$("$real" -C "$base" rev-parse --show-toplevel 2>/dev/null) '
        '|| top=""\n'
        'fi\n'
        # No toplevel: `git init` / `git clone` into a not-yet-repo. The
        # directory the write would CREATE is the thing to bound, so fall back
        # to the base path rather than passing it unchecked.
        '[ -n "$top" ] || top="$base"\n'
        'canon=$(canon_of "$top")\n'
        '\n'
        # ---- gather EVERY place this write could land ----
        # 🔴 Not one path. `GIT_DIR` sends refs and objects somewhere the
        # worktree path does not mention, so the check is over a SET.
        # The list is COLON-separated, which is git's own convention for these
        # variables (`GIT_ALTERNATE_OBJECT_DIRECTORIES` already is one), so a
        # single split handles both. A path containing a literal colon cannot
        # be expressed in those variables by git either, so nothing is lost.
        'cands="$canon"\n'
        'if [ "$pathverb" -eq 0 ]; then\n'
        # 🔴 ASK GIT, DO NOT INFER — this is the IDENTITY half.
        # These are answered in the SAME cwd and environment the real call will
        # use, so they already account for GIT_DIR, GIT_COMMON_DIR,
        # GIT_CONFIG_* injection, core.worktree, discovery and any redirection
        # vector this file has not enumerated — including the GIT_CONFIG_COUNT
        # family, which `man git` does not document but this binary honours.
        # `--git-common-dir` is asked separately from `--git-dir` because a
        # LINKED worktree has its own git dir and a SHARED common dir, and a
        # write can land in either.
        '  info=$("$real" -C "$base" rev-parse --path-format=absolute '
        '--git-dir --git-common-dir --git-path objects 2>/dev/null) || info=""\n'
        '  for line in $info; do\n'
        '    [ -n "$line" ] && cands="$cands:$line"\n'
        '  done\n'
        'fi\n'
        # 🔴 THE ENVIRONMENT, EXPLICITLY — this is the BYTE-REDIRECTION half,
        # and it is NOT redundant with the identity half above.
        #
        # `GIT_WORK_TREE=<victim> git -C <innocent fixture> checkout -f HEAD`
        # leaves identity completely truthful — the resolved repo IS the
        # fixture — while the FILES land in the victim's working tree. An
        # identity-based check passes it and the bytes are still written. The
        # same holds for GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, the alternates,
        # and the GIT_CONFIG_GLOBAL/SYSTEM pair. Neither half covers the other.
        + "".join(
            f'[ -n "${{{v}:-}}" ] && cands="$cands:${{{v}}}"\n'
            for v in GIT_LOCATION_ENV) +
        '\n'
        # ---- EVERY candidate must be in bounds ----
        'allowed=1\n'
        'why=""\n'
        'oldifs="$IFS"\n'
        'IFS=":"\n'
        'for c in $cands; do\n'
        '  [ -n "$c" ] || continue\n'
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
        # The destination gets the SAME check as every other candidate — one
        # helper, so the deny-wins ordering and the fail-closed verdict cannot
        # drift between the two call sites. A push FROM a legitimate fixture
        # clone INTO the real repo is the exact shape the contained clone was
        # built to prevent: its `origin` pointed at the operator\'s working
        # clone.
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
        # against the real repo deliberately. Refusing those makes the policy
        # permanently red; consulting the classifier only HERE means it can
        # never rescue an actual write.
        'case "$verb" in\n'
        f'  {"|".join(GIT_DUAL_VERBS)})\n'
        '    if dual_is_read "$verb" "$@"; then\n'
        '      printf \'git(read-dual) %s :: %s\\n\' "$canon" "$*" >> "$log"\n'
        '      exec "$real" "$@"\n'
        '    fi ;;\n'
        'esac\n'
        '\n'
        f'printf \'git(blocked) %s :: %s\\n\' "$canon" "$*" >> "$log"\n'
        f'if [ "$mode" = "{MODE_AUDIT}" ]; then\n'
        '  exec "$real" "$@"\n'
        'fi\n'
        f'printf \'%s\\n\' "{BANNER}" >&2\n'
        'printf \'  a test tried to run a git WRITE against a repo it does '
        'not own.\\n\' >&2\n'
        'printf \'  verb:    %s\\n\' "$verb" >&2\n'
        'printf \'  target:  %s\\n\' "$canon" >&2\n'
        'printf \'  reason:  %s\\n\' "$why" >&2\n'
        'printf \'  argv:    git %s\\n\' "$*" >&2\n'
        'printf \'  cwd:     %s\\n\' "$PWD" >&2\n'
        'printf \'  allowed: %s\\n\' "$roots" >&2\n'
        'printf \'  This is scripts/testlib/nogit.py. The suite once drove '
        'commit/branch -m/\\n\' >&2\n'
        'printf \'  push against the operator\\047s real clone and destroyed '
        'main on the\\n\' >&2\n'
        'printf \'  remote. Point this call at your own tmp_path, or widen '
        '%s.\\n\' "'
        + ROOTS_ENV + '" >&2\n'
        f'exit {BLOCK_EXIT}\n'
    )


def install(stub_dir: Path, allowed: str | None = None,
            denied: str | None = None) -> Path:
    """Write the `git` shim into `stub_dir`; return the log path.

    🔴 CALL THIS BEFORE PREPENDING `stub_dir` TO PATH — the shim resolves the
    real binary through `shutil.which`, so a stub dir already on PATH would
    make it exec ITSELF forever. The assertion below turns that ordering
    mistake into a named failure instead of a hang. (Same trap, same guard, as
    `nolaunch.install()`.)
    """
    stub_dir = Path(stub_dir)
    stub_dir.mkdir(parents=True, exist_ok=True)
    log = log_path(stub_dir)

    real_git = shutil.which("git")
    if real_git is None:
        # Nothing to shadow and nothing to forward to. Fabricating git's
        # answers is the one thing this module must never do (see the
        # docstring), so it installs nothing and says so.
        return log
    if Path(real_git).parent == stub_dir:
        raise AssertionError(
            "install() resolved git to its own stub dir — it was called AFTER "
            "stub_dir was put on PATH, and the shim would exec itself forever")
    # Baked in so the policy survives a child built with a REPLACING `env=`.
    # See the `roots=` / `denied=` lines in `git_body` for the measurement.
    write_exec(stub_dir / "git",
               git_body(real_git, log,
                        allowed_roots=default_roots() if allowed is None
                        else allowed,
                        denied_roots=denied_roots() if denied is None
                        else denied))
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
    """`python -m testlib.nogit <dir>` — install the shim, print the log path.

    The same door `testlib.nolaunch` opens for `scripts/run-tests.sh`: one
    install for a whole run, before any target starts, so the NON-pytest
    targets (which load no plugin) are covered by the same code rather than by
    a second implementation of it.
    """
    import sys as _sys
    args = list(_sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m testlib.nogit <stub-dir>", file=_sys.stderr)
        return 2
    print(install(Path(args[0])))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via the plugin
    raise SystemExit(main())
