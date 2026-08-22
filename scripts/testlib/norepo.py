#!/usr/bin/env python3
"""A `git` interceptor that makes "no test may mutate a REAL repository" true.

🔴 THE HAZARD (MEASURED 2026-08-21, on the operator's own workbench)
---------------------------------------------------------------------
Running the Python test tier reconfigured and rewrote the operator's real
`~/workspace/devrc` clone, and pushed to the real GitHub remote. What was found
on the clone afterwards:

    core.bare = true                 on a WORKING clone -- every later
                                     `git status` failed "must be run in a work
                                     tree"
    local `main` renamed to `trunk`, HEAD left on `trunk`
    origin repointed at a pytest tmpdir that no longer existed
    fixture commits by `t <t@t>` in its history

and on the PUBLIC repo, 63 fixture commits reached `refs/heads/main` in a
26-second burst starting 19:27:57Z, leaving the default branch at a single-file
fixture tree. `trunk` and three feature branches were hit too. Nothing was lost
only because another session still had the good sha.

🔴 THE CULPRIT IS COMMITTED ON `origin/main` AND WILL FIRE AGAIN
-----------------------------------------------------------------
`scripts/repo-cos/tests/test_prescan.py::_init_clone` maps one-for-one onto the
reflog: `config user.email t@t` -> the `t <t@t>` author, `commit -m seed`,
`branch -M trunk`, `push origin HEAD:trunk`. It is ordinary, correctly written
test code -- every call binds `-C` to a `tmp_path` fixture -- which is exactly
why reading call sites found nothing to fix.

Two OTHER mechanisms were traced and both were REFUTED by measurement (see
`scripts/tests/test_no_real_repo_writes.py`, which pins the refutations so they
are not re-derived). What makes correct code dangerous is a whole class rather
than a call site:

    An ambient GIT_DIR OVERRIDES `git -C <path>`.

MEASURED, git 2.55: with `GIT_DIR` naming repo V, `git -C <elsewhere> branch -m
main trunk` renames V's branch and repoints V's HEAD, and `git -C <elsewhere>
remote set-url origin <tmp>/does-not-exist.git` repoints V's origin -- which is
byte-for-byte the shape of the damage above. Every fixture in this repo binds
`-C` or `cwd=` correctly; a single inherited variable retargets ALL of them at
once, and no call site looks wrong. GIT_WORK_TREE, GIT_INDEX_FILE,
GIT_OBJECT_DIRECTORY and GIT_COMMON_DIR are the same shape.

So auditing call sites cannot close this. `run-tests.sh` scrubs those variables
(the direct fix) AND installs this shim (the structural one), because the next
route will not be a variable anyone has heard of either.

🔴 A GUARD IS BOUNDED BY THE CHANNEL IT OBSERVES, NOT BY THE COMPLETENESS OF ITS
LIST -- AND THE SECOND FAILURE IS INVISIBLE TO ANY CARE SPENT ON THE FIRST
--------------------------------------------------------------------------------
MEASURED, git 2.55: `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_<n>` /
`GIT_CONFIG_VALUE_<n>` are NOT in `man git` and git honours them. They set
arbitrary config for one invocation and TOUCH NO FILE:

    GIT_CONFIG_GLOBAL=<decoy> GIT_CONFIG_SYSTEM=/dev/null
      git config --get core.hooksPath          -> <unset>          (guard held)
    ...plus GIT_CONFIG_COUNT=1 KEY_0=core.hooksPath VALUE_0=/tmp/PWNED-hooks
      git config --get core.hooksPath          -> /tmp/PWNED-hooks
    decoy afterwards: 0 bytes, NEVER OPENED

An enumerated ledger fails on entries nobody knew about; a CHANNEL-scoped guard
fails on mechanisms that do not use that channel at all. This one reproduces the
measured `core.hooksPath` vector while a config-FILE redirect watches a file
that is never written and a path-protection model sees no protected path. Both
config defences look green and neither is engaged.

That is the argument for asking GIT for the effective answer wherever possible
and treating `RETARGETING_ENV` as a backstop rather than as the guard.

🔴 WHY A CLEANUP STEP IS NOT AN ACCEPTABLE FIX
-----------------------------------------------
Every fixture that re-points a real repo restores it on TEARDOWN. A killed run,
an assertion that raises past the restore, a `pytest -x` that stops the session
-- each leaves the operator's clone pointed at a temp directory with no error
anywhere. This guard does not undo the write. The mutating `git` process never
starts.

🔴 A WORKTREE IS NOT CONTAINMENT -- THE COMMON DIR IS THE UNIT
---------------------------------------------------------------
"Develop this in a worktree" is the intuitive answer and it is WRONG. A linked
worktree's `.git` is a FILE pointing back into the real clone's
`.git/worktrees/<name>`, and `git rev-parse --git-common-dir` from inside it
resolves to the REAL clone's `.git`: refs, remotes, config and reflog are
shared. A push from a worktree reaches the real remote. So this file protects
COMMON DIRS, and `protected_paths()` maps every path to one -- which makes
protecting `~/workspace/devrc` automatically protect all of its agent
worktrees. The only safe place to develop against this guard is a STANDALONE
clone with `origin` removed.

🔴 PATH-VALIDATING OR ENVIRONMENT-NEUTRALISING? BOTH, AND NEITHER ALONE IS ENOUGH
---------------------------------------------------------------------------------
The question matters because the vector BYPASSES PATH ARGUMENTS ENTIRELY, so a
guard that only inspected `-C` would be a guard whose description is wider than
its reach. The honest answer, in three parts:

  1. The shim NEVER validates the path ARGUMENTS. It asks git itself --
     `rev-parse --path-format=absolute --git-common-dir`, from the same cwd and
     with the same environment -- so what it validates is the repo git WILL
     use. GIT_DIR and GIT_COMMON_DIR are therefore covered by construction:
     they change git's answer, and the shim reads that answer.
  2. That is NOT sufficient on its own. GIT_WORK_TREE, GIT_INDEX_FILE and
     GIT_OBJECT_DIRECTORY leave the repo identity alone and redirect only WHERE
     THE WRITE LANDS -- so resolution answers "the fixture", truthfully, while
     the bytes go to the operator's clone. GIT_CONFIG_COUNT is worse still: it
     writes no file at all. All of them are refused explicitly, by the
     environment check in the shim -- which is DELIBERATELY in the shim, at call
     time, because these are per-invocation variables living in the CHILD's
     environment. A check run in the plugin's process, or around a test, reads
     its OWN environment, comes back clean, and PASSES while the child is fully
     injected.
  3. `run-tests.sh` also UNSETS the whole family. That is the weakest of the
     three and is not the guard: it governs what the runner INHERITS and does
     nothing about a test that builds its own subprocess environment. It is
     kept because a run that never inherits the variable never generates the
     write at all.

So: environment-neutralising at the runner, environment-AWARE resolution in the
shim, and an explicit environment refusal for the variables resolution cannot
see. `test_no_real_repo_writes.py` arms every one of the eight vectors against a
real victim and asserts the victim is byte-unchanged -- an assertion that a
variable is UNSET would be a claim about setup, not about the guard.

🔴 AND EACH ARMED VECTOR PROVES ITS OWN DANGER. Every one runs a second,
UNGUARDED arm that must move an asserted axis. MEASURED: four of ten vectors
originally written there moved NOTHING -- `checkout -f HEAD` against a victim
already at HEAD writes nothing observable whether refused or not, so the
assertion set passed identically either way. An armed control that can only
agree with itself is the same defect as a mutant that dies for the wrong
reason.

HOW IT WORKS
------------
`install()` writes a `git` shim into a directory that `run-tests.sh` prepends to
PATH for the whole run, exactly as `testlib.nolaunch` does for the host
launchers -- the same enforcement point, for the same reason: 24 targets, of
which the non-pytest ones (HOOK_TESTS, SHELL_TESTS) no conftest can ever reach.
The shim:

  1. parses git's GLOBAL options to find the subcommand and the repo targeted;
  2. sends anything on `ALWAYS_READ` straight to the real binary with no extra
     work -- the overwhelming majority of calls, and it must stay cheap;
  3. otherwise resolves the target's `--git-common-dir` and compares it against
     the PROTECTED list;
  4. for a protected repo, forwards ONLY an enumerated read form. Everything
     else -- including a subcommand this shim has never heard of -- is refused,
     recorded, and exits 99.

🔴 IT FAILS CLOSED, AND A FALSE REFUSAL IS LOUD
------------------------------------------------
An unknown subcommand against a protected repo is REFUSED, not forwarded. The
alternative (block a known-bad list, forward the rest) fails open on the next
subcommand someone uses -- `testlib.nolaunch` makes the same argument for
`systemctl`'s verb split. The cost is that a legitimate READ not on the list is
refused; that is why every refusal prints the full argv, the cwd and this file's
path. A false refusal is one line added to `PROTECTED_READ_FORMS`, diagnosable
from the message alone.

WHAT IT DELIBERATELY STILL ALLOWS
---------------------------------
  * every mutating git operation on a repo that is NOT protected -- i.e. every
    fixture repo built under `tmp_path`. That is the positive control the runner
    requires: a guard that refuses everything is indistinguishable from a guard
    that works, so `run-tests.sh` fails a target whose legitimate git work
    stopped happening.
  * every enumerated READ of a protected repo. Tests legitimately read the tree
    under test (`git ls-files`, `git log`, `git branch --show-current`) and
    several gates are built on exactly that.

🔴 KNOWN BLIND SPOTS, STATED RATHER THAN HIDDEN
-----------------------------------------------
Interception is by PATH, so a call that spells an ABSOLUTE `/usr/bin/git`
bypasses it, as does a test that REPLACES `$PATH` outright rather than
prepending to it.

🔴 AND THE SHIM IS **NOT** INHERITED BY GIT-SPAWNED CHILDREN. Git PREPENDS its
own `libexec/git-core` to `PATH` for every process it spawns, so a bare `git`
inside anything git launches resolves to the REAL binary and never reaches this
shim -- at ANY depth. MEASURED: `git -c 'alias.x=!git -C <victim> commit …' x`
returned rc 0 with the victim commit landed. That surface is large: `!`-aliases,
hooks, `core.fsmonitor`, clean/smudge filters, `GIT_SSH_COMMAND`,
`rebase --exec`, `submodule foreach`.

What is done about it: the shim refuses the CHANNELS that INJECT such a child
(`-c alias.*=!…`, the same via `GIT_CONFIG_KEY_<n>`, and any injected value
aimed at a protected path or a network remote), and the network rule refuses the
push before an ssh child is ever spawned. What is NOT covered, and is stated
rather than implied: a `!`-alias, hook or filter ALREADY PRESENT in a repo's own
config, executed by a git command this shim forwarded. Nothing in this tree does
that, and nothing here would stop it. Nothing in this tree does either today
(`test_no_real_repo_writes.py` pins that).

The per-target control comes in TWO flavours for exactly this reason:
`via=inherited` (fired from the runner's environment) proves the shim is armed
for the RUNNER, and on its own would make every `refused=0` a statement about
the runner -- a 32-row table reading as rigorous while measuring one thing
thirty-two times. `via=plugin` (fired by `testlib.norepo_plugin` from INSIDE the
target's process) is the claim about the target. The runner counts and prints
them separately and requires both from a pytest target. The five HOOK_TESTS and
the SHELL_TEST can load no plugin, so `plugin:0` is CORRECT for them and is
labelled `via=inherited (not pytest)` rather than being summed into one number
where a dead plugin would look like a healthy non-pytest target.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .mockbin import write_exec

LOG_NAME = "git-ops.log"
PROTECTED_LIST_NAME = "protected-repos.txt"
PROTECTED_CONFIG_NAME = "protected-config.txt"
EXEC_FARM_NAME = "execfarm"

#: The handle `run-tests.sh` exports so anything under the runner can find the
#: ONE guard directory. Same shape as `nolaunch.STUB_DIR_ENV`.
GUARD_DIR_ENV = "DEVRC_TEST_GITGUARD_DIR"

#: Set for the runner's OWN per-target probe. It does not weaken the refusal --
#: the probe is refused exactly like any other write -- it only changes which
#: ledger line is written, so the control can be counted separately from a leak.
PROBE_ENV = "DEVRC_TEST_GITGUARD_PROBE"

#: Which MECHANISM fired a control: `plugin` (the target's own process, via
#: `testlib.norepo_plugin`) or `inherited` (the runner's environment). Counted
#: separately, because a runner-side control proves the shim is armed for the
#: RUNNER and says nothing about the target's process.
PROBE_VIA_ENV = "DEVRC_TEST_GITGUARD_VIA"

#: Written by `install()`: the path the repo probe should target, or empty when
#: no real repository is protected. ONE definition, read by both the runner and
#: the plugin, so they cannot disagree about what is protected.
PROBE_TARGET_NAME = "probe-target.txt"

#: The two line prefixes the shim writes and `run-tests.sh` counts. Spelled on
#: both sides of a process boundary, so `test_no_real_repo_writes.py` pins them
#: BOTH ways: a rename on one side alone would leave the accounting matching
#: nothing and reporting a clean run.
REFUSED_PREFIX = "git(refused)"
CONTROL_PREFIX = "git(control)"

#: Exit status a refused invocation takes. Deliberately NOT 0. Unlike a
#: fire-and-forget desktop launcher, a caller that asked git to WRITE and got a
#: silent success would assert against a state that was never created and fail
#: somewhere else entirely. 99 is outside the range git uses for its own
#: outcomes, so it is recognisable in a traceback.
REFUSED_EXIT = 99

#: Environment variables that RETARGET git independently of `-C` and cwd.
#: 🔴 GIT_DIR beats `-C` -- MEASURED, see the module docstring. Scrubbing these
#: is the direct fix for the one mechanism this incident proved possible, and it
#: is separate from the shim on purpose: the shim would catch the write, but a
#: run that never inherits the variable never generates it.
#:
#: 🔴 THIS LEDGER IS INCOMPLETE BY CONSTRUCTION, AND SAYING SO IS THE POINT.
#: It was enumerated from `man git`, and git honours variables the man page
#: does not document — `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_<n>` /
#: `GIT_CONFIG_VALUE_<n>` are the measured example, and they were missing from
#: this tuple until a peer session reproduced the bypass. So this list is a
#: BACKSTOP, not the guard. The guard is the shim asking git for the answer it
#: will actually use.
RETARGETING_ENV = (
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    # Undocumented in `man git`; honoured anyway. Unsetting COUNT disables the
    # whole KEY_<n>/VALUE_<n> mechanism, so one name covers all of them.
    "GIT_CONFIG_COUNT",
    # ⚠ NOT WRITE TARGETS, and deliberately NOT in the shim's refusal set: a
    # guard that refuses harmless things is a permanently-red gate. They are
    # scrubbed only so the runner's environment is predictable.
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_PREFIX",
)

#: 🔴 F3 — AN ALLOWLIST, NOT A DENYLIST. Injected config (`-c`, `--config-env`,
#: `GIT_CONFIG_KEY_<n>`) can hand the REAL git binary a program to run, and the
#: shim is not inherited by what git spawns. An earlier revision refused ONE key
#: (`alias.*=!`) while its own prose NAMED `core.fsmonitor`, clean/smudge
#: filters and `GIT_SSH_COMMAND` as surface and refused none of them.
#:
#: MEASURED against that revision: `-c core.hooksPath=<dir with a hook>` +
#: `commit` landed ALL FOUR of the incident's documented damages on a victim
#: (`core.bare` true, `main`->`trunk`, `remote.origin.url` repointed,
#: `user.email` rewritten) plus a planted file -- at rc 0, ledger delta 0.
#:
#: So: an injected key must be on this list or it is REFUSED. Membership means
#: "this key cannot name a program, a config file to load, or a write
#: destination". Every entry is a key MEASURED in use in this tree.
INJECTABLE_KEYS = (
    "user.name", "user.email", "user.signingkey",
    "init.defaultbranch",
    "core.abbrev", "core.filemode", "core.autocrlf", "core.ignorecase",
    "core.excludesfile", "core.bare", "core.logallrefupdates", "core.quotepath",
    "core.safecrlf", "core.symlinks", "core.trustctime", "core.precomposeunicode",
    "commit.gpgsign", "tag.gpgsign", "gpg.program",
    "merge.autostash", "rebase.autostash", "pull.rebase", "push.default",
    "fetch.prune", "gc.auto", "advice.detachedhead", "advice.addignoredfile",
    "diff.algorithm", "diff.renames", "status.showuntrackedfiles",
    "log.date", "i18n.logoutputencoding", "i18n.commitencoding",
    "protocol.file.allow", "protocol.ext.allow",
    "color.ui", "color.diff", "color.status", "color.branch",
    "index.version", "feature.manyfiles", "extensions.worktreeconfig",
)

#: Keys that name a HOOK OR TEMPLATE DIRECTORY. `analyze-service-index/
#: commit.sh` sets both ON PURPOSE, at an EMPTY dir, to NEUTRALISE hooks -- and
#: its suite drives that path, so refusing them outright is a permanently-red
#: gate. The distinction is MEASURABLE rather than spelled: neutralising points
#: at a directory with no executable in it; arming points at one with a hook.
HOOKDIR_KEYS = ("core.hookspath", "init.templatedir")

#: Keys whose VALUE is a repository or remote. Allowed only when the value is
#: neither a protected path nor a network remote -- checked by the same two
#: predicates the operand scanner uses, so there is one rule, not two.
LOCATION_KEY_SUFFIXES = (".url", ".pushurl")

#: The subset of `RETARGETING_ENV` the shim actually REFUSES on. Everything
#: here has been MEASURED to move an axis the battery asserts on; membership is
#: earned by that measurement, not by looking dangerous.
REFUSED_ENV = (
    "GIT_DIR",
    "GIT_COMMON_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_COUNT",
)

#: Subcommands that cannot write and cannot reach a network remote. They go
#: straight through with no repo resolution at all.
#:
#: 🔴 Membership is "cannot mutate in ANY argument form". `branch`, `config`,
#: `remote`, `tag`, `stash`, `worktree`, `notes`, `reflog`, `symbolic-ref` and
#: `submodule` all have read forms and are deliberately ABSENT: they are
#: classified per-invocation below, because an ARGUMENT must never be able to
#: promote a mutation (the `systemctl --user stop status` shape from
#: `testlib.nolaunch`).
ALWAYS_READ = (
    "annotate", "blame", "cat-file", "check-attr", "check-ignore",
    "check-ref-format", "cherry", "column", "count-objects", "describe",
    "diff", "diff-files", "diff-index", "diff-tree", "for-each-ref", "grep",
    "help", "interpret-trailers", "log", "ls-files", "ls-tree", "merge-base",
    "name-rev", "rev-list", "rev-parse", "shortlog", "show", "show-ref",
    "stripspace", "var", "verify-commit", "verify-pack",
    "verify-tag", "version", "whatchanged",
)

#: Subcommands that CONTACT A REMOTE. Refused whenever the remote they would
#: reach is not a local filesystem path -- in ANY repo, protected or not. This
#: is the only rule that covers a real clone nobody enumerated, and it is what
#: stands between a fixture and the public repo's default branch.
NETWORK_SUBCOMMANDS = ("push", "fetch", "pull", "clone", "ls-remote")

#: Read forms of the dual-mode subcommands, as `(<sub>, <sh-case-pattern>)`
#: matched against the arguments FOLLOWING the subcommand, space-prefixed (so a
#: pattern cannot match the tail of a longer flag, and a bare subcommand matches
#: the empty pattern). Adding one is a deliberate act of accounting.
PROTECTED_READ_FORMS = (
    ("config", "*--get*"),
    ("config", "*--list*"),
    ("config", "* -l*"),
    ("remote", ""),
    ("remote", " -v"),
    ("remote", " --verbose"),
    ("remote", " get-url*"),
    # `branch`: the read flags only. `-m/-M/-d/-D/-f/-c/-C/-u/--set-upstream-to`
    # and a bare positional branch name are NOT here, so they are refused --
    # `branch -m main trunk` is one half of the measured damage.
    ("branch", " --show-current*"),
    ("branch", " -r*"),
    ("branch", " -a*"),
    ("branch", " --list*"),
    ("branch", " -v"),
    ("branch", " -vv"),
    ("branch", " --contains*"),
    ("branch", " --format*"),
    ("worktree", " list*"),
    ("tag", " -l*"),
    ("tag", " --list*"),
    ("stash", " list*"),
    ("stash", " show*"),
    ("reflog", ""),
    ("reflog", " show*"),
    ("notes", " list*"),
    ("notes", " show*"),
    ("submodule", " status*"),
    ("submodule", " summary*"),
    # One-argument `symbolic-ref` READS a ref; the TWO-argument form WRITES it,
    # and that is how a HEAD gets repointed at another branch. `--delete` is
    # deliberately absent.
    ("symbolic-ref", " -q*"),
    ("symbolic-ref", " --short*"),
    ("symbolic-ref", " HEAD"),
)


def log_path(guard_dir: Path) -> Path:
    """Where the shim records refusals and the runner's per-target controls."""
    return Path(guard_dir) / LOG_NAME


def sh_pattern(pat: str) -> str:
    """Render `pat` as a POSIX-sh `case` pattern with its SPACES made literal.

    🔴 QUOTING THE WHOLE PATTERN IS THE BUG, NOT THE FIX. Inside a `case`, a
    quoted character is LITERAL -- so `'*--get*'` matches the four-character
    string `*--get*` and nothing else, while an UNQUOTED pattern containing a
    space is a syntax error that kills the whole shim. Measured: the first
    revision did both, and every branch of the guard died at parse time. So the
    spaces are quoted and the wildcards are not.
    """
    if pat == "":
        return "''"
    return pat.replace(" ", '" "')


def _read_forms_case(sub: str) -> str:
    return "|".join(sh_pattern(p) for s, p in PROTECTED_READ_FORMS if s == sub)


def _sh_list(items):
    return "|".join(items)


def shim_body(real_git: str, log: Path, protected_list: Path,
              protected_config: Path) -> str:
    """The POSIX-sh body of the `git` shim. No shebang -- write_exec owns it.

    🔴 ORDER IS THE ARCHITECTURE (F4). The read FAST PATH sits BELOW the
    injected-config scan, the environment scan and the GIT_CONFIG_COUNT scan,
    and above nothing else. An earlier revision had it FIRST, so any subcommand
    on the read list carried its injections straight to the real binary:
    MEASURED, `-c diff.external=<script> ... diff` ran the script (planted a
    file in the victim) and `GIT_DIR=<victim> ... rev-parse --absolute-git-dir`
    printed the victim's git dir -- both rc 0, both with the ledger at 0, i.e.
    a green `refused=0` row. `diff` and `rev-parse` are on the list this file
    describes as "cannot write and cannot reach a network remote"; that is true
    of the SUBCOMMAND and says nothing about what was injected alongside it.

    🔴 EVERY BRANCH EITHER `exec`s THE REAL BINARY OR REFUSES WITH A NON-ZERO
    STATUS. There is no path that returns success without having done the work.
    """
    always_read = _sh_list(sorted(set(ALWAYS_READ)))
    network = _sh_list(NETWORK_SUBCOMMANDS)
    refused_env = " ".join(v for v in REFUSED_ENV if v != "GIT_CONFIG_COUNT")
    inj = _sh_list(INJECTABLE_KEYS)
    hookdirs = _sh_list(HOOKDIR_KEYS)
    read_cases = "\n".join(
        f"    {sub}) case \"$rest\" in {_read_forms_case(sub)}) _pass \"$@\" ;; esac ;;"
        for sub in sorted({s for s, _ in PROTECTED_READ_FORMS})
    )

    return f"""
REAL='{real_git}'
LOG='{log}'
PROT='{protected_list}'
PROTCFG='{protected_config}'

_pass() {{ exec "$REAL" "$@"; }}

_refuse() {{
  reason="$1"; shift
  if [ "${{{PROBE_ENV}:-0}}" = 1 ]; then
    printf '{CONTROL_PREFIX}\\tvia=%s\\t%s\\tgit %s\\n' "${{{PROBE_VIA_ENV}:-inherited}}" "$reason" "$*" >> "$LOG" 2>/dev/null || true
  else
    printf '{REFUSED_PREFIX}\\t%s\\tgit %s\\n' "$reason" "$*" >> "$LOG" 2>/dev/null || true
  fi
  echo "GUARD 9 (scripts/testlib/norepo.py) REFUSED a git invocation." >&2
  echo "  reason : $reason" >&2
  echo "  argv   : git $*" >&2
  echo "  cwd    : $PWD" >&2
  echo "  No test may mutate a REAL repository, rewrite the operator's git" >&2
  echo "  config, hand the real git binary a program to run, or reach a" >&2
  echo "  network remote. Build the fixture under tmp_path and target it." >&2
  exit {REFUSED_EXIT}
}}

# ---- INVOKED AS `git-<verb>`? RECONSTRUCT THE EQUIVALENT `git <verb>` -------
# 🔴 GIT DISPATCHES ON argv[0]. `libexec/git-core` holds ~181 `git-<verb>`
# entries, and running one directly is an ordinary spelling -- `git-commit`,
# `git-config` -- not an exotic one. MEASURED against an earlier revision, with
# the farm installed and every other check in place:
#
#   <farm>/git-config -f <victim>/.git/config core.bale true
#     -> core.bare false -> true, rc 0
#
# i.e. the INCIDENT'S OWN SIGNATURE, with `git` never spelled once. The farm
# substituted only the literal name `git`, so all 181 verb entries pointed
# straight at the real binary and sat FIRST on PATH in exactly the alias/hook
# context the farm was added to cover.
#
# 🔴 ROUTED BY NAME, NEVER BY INODE. On this host none of those entries shares
# git's inode (they are symlinks); on another packaging they are hardlinks. The
# property that matters is "a name git dispatches on", which is satisfied by a
# hardlink, a symlink, a copy or a wrapper -- so an inode/`stat` enumeration is
# the WRONG attribute and would report a clean farm on one packaging and miss
# every entry on another.
_self=${{0##*/}}
case "$_self" in
  git-*) set -- "${{_self#git-}}" "$@" ;;
esac

# ---- parse git's GLOBAL options, stopping at the subcommand ------------------
sub=''; tgt=''; gdir=''; want=''; cvals=''
for a in "$@"; do
  case "$want" in
    C) tgt="$a"; want=''; continue ;;
    D) gdir="$a"; want=''; continue ;;
    K) cvals="$cvals
$a"; want=''; continue ;;
    E) cvals="$cvals
env:$a"; want=''; continue ;;
    X) want=''; continue ;;
  esac
  case "$a" in
    -C) want=C; continue ;;
    --git-dir) want=D; continue ;;
    --git-dir=*) gdir="${{a#--git-dir=}}"; continue ;;
    -c) want=K; continue ;;
    --config-env) want=E; continue ;;
    --config-env=*) cvals="$cvals
env:${{a#--config-env=}}"; continue ;;
    --work-tree|--namespace|--super-prefix) want=X; continue ;;
    -*) continue ;;
    *) sub="$a"; break ;;
  esac
done

# 🔴 Pathname expansion OFF: the loops below word-split unquoted `$rest` and
# `$cvals`, and an unquoted `$var` in a `for` list is GLOBBED as well as split.
set -f

# The arguments AFTER the subcommand, space-prefixed.
rest=''; seen=0
for a in "$@"; do
  if [ "$seen" = 0 ]; then
    [ "$a" = "$sub" ] && seen=1
    continue
  fi
  rest="$rest $a"
done

# ---- IS THIS PATH INSIDE A PROTECTED REPOSITORY? ----------------------------
# 🔴 `readlink -m`, not `-f` (F6): `-f` returns EMPTY for a path that does not
# exist yet, so `git clone <src> <victim>/planted` resolved to nothing and was
# ALLOWED -- MEASURED, the clone landed inside the victim. `-m` canonicalises
# without requiring existence and keeps the symlink handling that the same
# check was verified 5/5 on.
#
# 🔴 ABSOLUTE ONLY: `readlink` resolves a relative string against THIS SHIM's
# cwd, which under `run-tests.sh` IS the protected repo, so every non-path
# operand matched and 15 tests failed on a correct tree. A relative path aimed
# at a protected repo is left to the repo RESOLUTION below.
_prot_match() {{
  [ -n "$1" ] || return 1
  case "$1" in
    /*) ;;
    *) return 1 ;;
  esac
  _rp=$(readlink -m "$1" 2>/dev/null || echo "$1")
  while IFS= read -r _p; do
    [ -z "$_p" ] && continue
    case "$_rp" in
      "$_p"|"$_p"/*) return 0 ;;
    esac
  done < "$PROT"
  return 1
}}

# ---- IS THIS OPERAND A NETWORK REMOTE? --------------------------------------
# 🔴 GIT'S OWN RULE, not two globs (F2). The previous test matched `*://*` and
# `*@*:*` only, so USERLESS scp syntax -- `github.com:owner/repo`, which is
# valid and has no `@` -- matched neither and was never classified. MEASURED
# with the transport lever off: git attempted a real ssh connection to
# github.com while the ledger stayed at 0, i.e. the guard never saw it.
#
# git's rule: `://` makes it a URL; otherwise a `:` BEFORE the first `/` makes
# it scp-like. Checking the segment before the first slash is what keeps a
# REFSPEC (`HEAD:refs/heads/main`, whose colon follows no slash but whose first
# segment is `HEAD`) from being mistaken for a host -- and the operand scanner
# below only ever inspects the REPOSITORY operand anyway, never a refspec.
_is_network() {{
  case "$1" in
    file://*) return 1 ;;
    *://*) return 0 ;;
    /*|./*|../*) return 1 ;;
  esac
  case "$1" in
    */*) case "${{1%%/*}}" in *:*) return 0 ;; esac; return 1 ;;
    *:*) return 0 ;;
  esac
  return 1
}}

# ---- INJECTED CONFIG: AN ALLOWLIST (F3) -------------------------------------
_hookdir_is_armed() {{ # $1 = directory
  [ -d "$1" ] || return 1
  # 🔴 `set +f` around the glob. Pathname expansion is OFF for this whole shim
  # (the split loops need it off), and an earlier revision left it off HERE —
  # so `"$1"/*` stayed a LITERAL string, matched nothing, and every armed hook
  # directory was reported unarmed. MEASURED: the full four-damage
  # `core.hooksPath` vector still landed at rc 0 with the check "passing".
  set +f
  _armed=1
  for _h in "$1"/* "$1"/hooks/*; do
    if [ -f "$_h" ] && [ -x "$_h" ]; then _armed=0; break; fi
  done
  set -f
  return $_armed
}}

# $1 = "key=value" or "env:key=ENVVAR"; remaining args = the original argv.
_scan_cval() {{
  case "$1" in
    env:*)
      _e="${{1#env:}}"; _ck="${{_e%%=*}}"; _ev="${{_e#*=}}"
      # 🔴 NEVER `eval` AN ARGV-DERIVED STRING. `--config-env=key=VAR` puts VAR
      # on the command line, and an earlier revision did
      # `eval "_cv=\${{$_ev:-}}"` with it. MEASURED:
      #
      #   git --config-env=core.pager='V:-$(touch /tmp/X)' --version
      #     this shim  -> /tmp/X CREATED, then exit 99
      #     real git   -> exit 128, "fatal: missing environment variable
      #                   'V:-$(touch /tmp/X)'", nothing executed
      #
      # On that path the guard was STRICTLY WORSE THAN NO GUARD: it introduced
      # code execution that the unguarded system refuses, on a pure READ
      # (`--version`), before any check ran. A mitigation that adds an attack
      # surface is not a mitigation.
      #
      # The name is validated as a shell identifier and the value is read with
      # `printenv`, which does not interpret. An invalid name is REFUSED, which
      # is also what real git does with it.
      case "$_ev" in
        ''|*[!A-Za-z0-9_]*|[0-9]*)
          _refuse "--config-env names an invalid environment variable '$_ev'; real git rejects this and executes nothing, so neither will the guard" "$@" ;;
      esac
      _cv=$(printenv "$_ev" 2>/dev/null || true)
      ;;
    *) _ck="${{1%%=*}}"; _cv="${{1#*=}}" ;;
  esac
  [ "$_ck" = "$1" ] && return 0
  _lk=$(printf '%s' "$_ck" | tr 'A-Z' 'a-z')

  # A value naming a protected path or a network remote is refused whatever the
  # key is -- one rule, shared with the operand scanner.
  if [ -n "$_cv" ] && _prot_match "$_cv"; then
    _refuse "-c $_ck=$_cv points into a PROTECTED repository" "$@"
  fi
  case "$_lk" in
    *{_sh_list(LOCATION_KEY_SUFFIXES)})
      if _is_network "$_cv"; then
        _refuse "-c $_ck=$_cv is a NETWORK remote; this tier is hermetic" "$@"
      fi
      return 0 ;;
  esac

  case "$_lk" in
    {hookdirs})
      # Neutralising (an empty dir) is what commit.sh does on purpose. Arming
      # (a dir holding an executable) is the measured full-incident vector.
      if _hookdir_is_armed "$_cv"; then
        _refuse "-c $_ck=$_cv points at a directory containing an EXECUTABLE hook; git would RUN it, and the shim is not inherited by what git spawns" "$@"
      fi
      return 0 ;;
    {inj}) return 0 ;;
  esac
  _refuse "-c $_ck is not on the injectable-config allowlist (norepo.INJECTABLE_KEYS). Injected config can hand the REAL git binary a program to run -- aliases, hooks, fsmonitor, clean/smudge filters, pagers, external diff -- and this shim is NOT inherited by what git spawns. If this key genuinely cannot name a program, a config to load or a write destination, add it to the allowlist" "$@"
}}

if [ -n "$cvals" ]; then
  _oifs="$IFS"; IFS='
'
  for _c in $cvals; do
    [ -n "$_c" ] && _scan_cval "$_c" "$@"
  done
  IFS="$_oifs"
fi

# ---- THE ENVIRONMENT VECTOR -------------------------------------------------
# GIT_DIR/GIT_COMMON_DIR change the repo's IDENTITY and are covered by
# resolution below; GIT_WORK_TREE/GIT_INDEX_FILE/GIT_OBJECT_DIRECTORY leave
# identity truthful and redirect only WHERE THE WRITE LANDS, which resolution
# cannot see. All are refused here, above the fast path, because a read
# subcommand carries them just as well as a write one.
# The two `eval`s below take a HARDCODED variable name and a digit-validated
# counter respectively -- never a value from argv. That distinction is the whole
# audit: see `test_no_argv_derived_string_is_shell_interpreted`.
for _v in {refused_env}; do
  eval "_val=\\${{$_v:-}}"
  if [ -n "$_val" ] && _prot_match "$_val"; then
    _refuse "$_v points into a PROTECTED repository ($_val); it would redirect this write there whatever the arguments say" "$@"
  fi
done

# GIT_CONFIG_COUNT / KEY_<n> / VALUE_<n>: undocumented in `man git`, honoured
# anyway, and they write NO file -- so neither the config redirect nor path
# protection can see them. Routed through the SAME scanner as `-c`.
case "${{GIT_CONFIG_COUNT:-}}" in
  ''|*[!0-9]*) : ;;
  *)
    _n=0
    while [ "$_n" -lt "${{GIT_CONFIG_COUNT}}" ]; do
      eval "_ik=\\${{GIT_CONFIG_KEY_$_n:-}}"
      eval "_iv=\\${{GIT_CONFIG_VALUE_$_n:-}}"
      [ -n "$_ik" ] && _scan_cval "$_ik=$_iv" "$@"
      _n=$(( _n + 1 ))
    done
    ;;
esac

# ---- OPTIONS THAT WRITE OR EXECUTE, ON *ANY* SUBCOMMAND ---------------------
# 🔴 A VERB'S CLASSIFICATION SAYS NOTHING ABOUT ITS OPTIONS. `log` and `grep`
# are on the read list -- truthfully, as verbs -- and MEASURED against an
# earlier revision, from an ALLOWED repo:
#
#   git -C <allowed> log -p --output=<victim>/.git/config
#       -> the victim's config CLOBBERED, rc 0
#   git -C <allowed> grep -O'touch <path>' A
#       -> the command RAN, rc 0
#
# Both `exec`d on the fast path before any destination check. This is the same
# lesson `status` taught (a read that writes) with a destination attached, so
# the check sits ABOVE the fast path and applies to every subcommand.
#
# ⚠ AN ENUMERATION, AND SAID SO: these are the option spellings that carry a
# destination or a command. It is a backstop, not the guard -- the guard for a
# non-read verb is the repo resolution below. `--exec` is deliberately ABSENT:
# `git --exec-path` is our own farm's mechanism and `rebase --exec` is ordinary
# git, so refusing it would be a permanently-red gate.
_dw=''
for a in "$@"; do
  if [ -n "$_dw" ]; then
    _dw=''
    if _prot_match "$a"; then
      _refuse "an output destination ($a) inside a PROTECTED repository" "$@"
    fi
    continue
  fi
  case "$a" in
    --output|--output-directory) _dw=1 ;;
    --output=*|--output-directory=*)
      _od="${{a#*=}}"
      if _prot_match "$_od"; then
        _refuse "an output destination ($_od) inside a PROTECTED repository" "$@"
      fi
      ;;
    -O?*|--open-files-in-pager=*|--to-command=*|--upload-pack=*|--receive-pack=*)
      _refuse "$a names a COMMAND for git to run; this shim is not inherited by what git spawns, so the command would be unguarded" "$@" ;;
    -O|--open-files-in-pager|--to-command|--upload-pack|--receive-pack)
      _refuse "$a names a COMMAND for git to run; this shim is not inherited by what git spawns, so the command would be unguarded" "$@" ;;
  esac
done

# ---- FAST PATH (deliberately AFTER every scanner above) ---------------------
case "$sub" in
  ''|{always_read}) _pass "$@" ;;
esac

# ---- PROTECTED CONFIG FILES -------------------------------------------------
if [ "$sub" = config ]; then
  cfgfile=''
  case "$rest" in
    *--global*) cfgfile="${{GIT_CONFIG_GLOBAL:-$HOME/.gitconfig}}" ;;
    *--system*) cfgfile="${{GIT_CONFIG_SYSTEM:-/etc/gitconfig}}" ;;
  esac
  _cfw=''
  for a in $rest; do
    case "$_cfw" in F) cfgfile="$a"; _cfw=''; continue ;; esac
    case "$a" in
      --file|-f) _cfw=F ;;
      --file=*) cfgfile="${{a#--file=}}" ;;
    esac
  done
  if [ -n "$cfgfile" ]; then
    if _prot_match "$cfgfile"; then
      case "$rest" in
        *--get*|*--list*|*" "-l*) _pass "$@" ;;
      esac
      _refuse "config would write $cfgfile, inside a PROTECTED repository" "$@"
    fi
    rcfg=$(readlink -m "$cfgfile" 2>/dev/null || echo "$cfgfile")
    while IFS= read -r p; do
      [ -z "$p" ] && continue
      if [ "$rcfg" = "$p" ]; then
        case "$rest" in
          *--get*|*--list*|*" "-l*) _pass "$@" ;;
        esac
        _refuse "would rewrite the operator's git config file $rcfg" "$@"
      fi
    done < "$PROTCFG"
  fi
fi

# ---- SUBCOMMANDS THAT NAME A DESTINATION ------------------------------------
for a in $rest; do
  case "$a" in -*) continue ;; esac
  case "$sub" in
    worktree|submodule)
      if _prot_match "$a"; then
        _refuse "$sub would write to $a, inside a PROTECTED repository" "$@"
      fi
      ;;
  esac
done
_sgd=''; _sgw=''
for a in $rest; do
  case "$_sgw" in S) _sgd="$a"; _sgw=''; continue ;; esac
  case "$a" in
    --separate-git-dir) _sgw=S ;;
    --separate-git-dir=*) _sgd="${{a#--separate-git-dir=}}" ;;
  esac
done
if [ -n "$_sgd" ] && _prot_match "$_sgd"; then
  _refuse "--separate-git-dir would plant a git dir at $_sgd, inside a PROTECTED repository" "$@"
fi

# ---- REPOSITORY OPERANDS OF THE REMOTE SUBCOMMANDS (F1/F2) ------------------
# 🔴 THE DESTINATION, not just the URL SHAPE. An earlier revision tested the
# operand against two URL globs and `continue`d anything that looked like a
# filesystem path -- so `git -C <fixture> push <victim-bare> +HEAD:refs/heads/
# main` force-updated a protected bare repo at rc 0 with the ledger at 0.
# MEASURED. That is the incident's own damage with the guard silent.
#
# Only the REPOSITORY operand is inspected (the first non-flag after the
# subcommand; for `clone`, the source and then the destination), never a
# refspec.
case "$sub" in
  {network})
    _op1=''; _op2=''
    for a in $rest; do
      case "$a" in -*) continue ;; esac
      if [ -z "$_op1" ]; then _op1="$a"; elif [ -z "$_op2" ]; then _op2="$a"; fi
    done
    # 🔴 ONLY `clone` HAS A SECOND REPOSITORY OPERAND. For push/fetch/pull/
    # ls-remote the second operand is a REFSPEC, and `HEAD:refs/heads/main`
    # looks exactly like scp syntax to any host:path rule -- its first
    # slash-segment is `HEAD:refs`, which contains a colon. MEASURED: an
    # earlier revision of THIS fix refused every legitimate fixture push with
    # "would contact the NETWORK remote HEAD:refs/heads/main", i.e. it made
    # itself a permanently-red gate while closing F1.
    case "$sub" in
      clone) _cands="$_op1 $_op2" ;;
      *)     _cands="$_op1" ;;
    esac
    for _cand in $_cands; do
      [ -n "$_cand" ] || continue
      if _is_network "$_cand"; then
        _refuse "$sub would contact the NETWORK remote $_cand; this tier is hermetic" "$@"
      fi
      case "$_cand" in
        /*|./*|../*|file://*)
          _p="${{_cand#file://}}"
          if _prot_match "$_p"; then
            _refuse "$sub names $_cand, inside a PROTECTED repository" "$@"
          fi
          ;;
      esac
    done
    # A bare NAME resolves through config; `-c remote.<name>.url=` injections
    # were already refused by the scanner above, so a fresh `git config` here
    # cannot be fooled by one.
    case "$_op1" in
      ''|-*|*/*|*:*) : ;;
      *)
        _u=$( ( cd "${{tgt:-.}}" 2>/dev/null && "$REAL" config --get "remote.$_op1.url" 2>/dev/null ) )
        if [ -n "$_u" ]; then
          if _is_network "$_u"; then
            _refuse "$sub would contact the NETWORK remote $_u (via remote.$_op1.url); this tier is hermetic" "$@"
          fi
          _prot_match "${{_u#file://}}" && _refuse "$sub targets remote.$_op1.url=$_u, inside a PROTECTED repository" "$@"
        fi
        ;;
    esac
    # With no operand at all, every configured remote is a candidate.
    if [ -z "$_op1" ]; then
      _urls=$( ( cd "${{tgt:-.}}" 2>/dev/null && "$REAL" config --get-regexp '^remote\\..*\\.url$' 2>/dev/null ) )
      for _u in $_urls; do
        case "$_u" in remote.*) continue ;; esac
        if _is_network "$_u"; then
          _refuse "$sub would contact the NETWORK remote $_u; this tier is hermetic" "$@"
        fi
        _prot_match "${{_u#file://}}" && _refuse "$sub targets $_u, inside a PROTECTED repository" "$@"
      done
    fi
    ;;
esac

# ---- resolve the repo this invocation targets -------------------------------
case "$sub" in
  init|clone)
    dest=''
    for a in $rest; do
      case "$a" in -*) continue ;; esac
      dest="$a"
    done
    case "$dest" in
      '') : ;;
      /*) tgt="$dest" ;;
      *)  tgt="${{tgt:-.}}/$dest" ;;
    esac
    # F6: the destination need not exist yet, and `_prot_match` no longer needs
    # it to. Checked directly, because resolution below cannot `cd` into it.
    case "$dest" in
      /*) _prot_match "$dest" && _refuse "$sub would create a repository at $dest, inside a PROTECTED repository" "$@" ;;
    esac
    ;;
esac

if [ -n "$gdir" ]; then
  common=$( cd "${{tgt:-.}}" 2>/dev/null && "$REAL" --git-dir="$gdir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null )
else
  common=$( cd "${{tgt:-.}}" 2>/dev/null && "$REAL" rev-parse --path-format=absolute --git-common-dir 2>/dev/null )
fi

protected=0
rcommon=''
if [ -n "$common" ]; then
  rcommon=$(readlink -m "$common" 2>/dev/null || echo "$common")
  _prot_match "$common" && protected=1
fi

# ---- PROTECTED REPO: only an enumerated READ form is forwarded ---------------
if [ "$protected" = 1 ]; then
  # `status` is a READ that WRITES -- it rewrites `.git/index`. Refusing it
  # would be a permanently-red gate, and the write is a stat-cache refresh, so
  # it is forwarded with the flag git provides for exactly this.
  if [ "$sub" = status ]; then
    exec "$REAL" --no-optional-locks "$@"
  fi
  case "$sub" in
{read_cases}
  esac
  _refuse "would mutate the PROTECTED repository $rcommon" "$@"
fi

case "$sub" in
  status) _pass "$@" ;;
esac

_pass "$@"
"""


def install(guard_dir: Path, protected_repos, protected_config_files,
            allow_no_repos: bool = False) -> Path:
    """Write the `git` shim into `guard_dir`; return the log path.

    🔴 CALL THIS BEFORE PREPENDING `guard_dir` TO PATH. The shim resolves the
    real binary through `shutil.which`, so a guard dir already on PATH would
    make it exec ITSELF -- an infinite fork loop rather than a passthrough. The
    check below turns that ordering mistake into a named failure instead of a
    hang, exactly as `testlib.nolaunch.install` does.
    """
    guard_dir = Path(guard_dir)
    guard_dir.mkdir(parents=True, exist_ok=True)

    real = real_git() if shutil.which("git") else None
    if real is None:
        raise RuntimeError("testlib.norepo.install: no `git` on PATH to shadow.")
    if Path(real).parent.resolve() == guard_dir.resolve():
        raise RuntimeError(
            "testlib.norepo.install: `git` already resolves INSIDE the guard "
            f"dir ({real}). install() must run BEFORE {guard_dir} is put on "
            "PATH, or the shim execs itself.")
    if not protected_repos and not allow_no_repos:
        raise RuntimeError(
            "testlib.norepo.install: refusing to install a guard that protects "
            "NO repository without `allow_no_repos=True`. An empty list is a "
            "guard wired to nothing, and its zero refusals would read exactly "
            "like a clean run. Pass the flag only where you can SAY why there "
            "is no real repo -- the nix build sandbox is the one such place, "
            "and the NETWORK and CONFIG rules still apply there.")

    # 🔴 BOTH LISTS ARE REALPATH'd HERE, because the shim compares against
    # `readlink -f` output. Comparing a resolved path to an unresolved one is a
    # guard that silently matches nothing the moment any component is a symlink
    # -- and `~/.gitconfig` behind a dotfile symlink is the ordinary case on
    # this host, not an exotic one. Normalising on ONE side only is how a guard
    # reports a clean run while protecting nothing.
    prot = guard_dir / PROTECTED_LIST_NAME
    prot.write_text(
        "".join(f"{os.path.realpath(Path(p).expanduser())}\n" for p in protected_repos),
        encoding="utf-8")
    protcfg = guard_dir / PROTECTED_CONFIG_NAME
    protcfg.write_text(
        "".join(f"{os.path.realpath(Path(p).expanduser())}\n"
                for p in protected_config_files),
        encoding="utf-8")

    # The repo probe's target: a WORKING TREE among the protected paths (a
    # bare `.git` is not somewhere `git -C` can write a --local config from).
    # Empty when nothing real is protected, which is the nix build sandbox.
    target = ""
    for p in protected_repos:
        rp = Path(os.path.realpath(Path(p).expanduser()))
        if rp.name != ".git" and (rp / ".git").exists():
            target = str(rp)
            break
    (guard_dir / PROBE_TARGET_NAME).write_text(target + "\n", encoding="utf-8")

    log = log_path(guard_dir)
    write_exec(guard_dir / "git", shim_body(real, log, prot, protcfg))
    return log


def probe_target(guard_dir) -> str:
    """The path the repo probe targets, or "" when nothing real is protected."""
    try:
        return (Path(guard_dir) / PROBE_TARGET_NAME).read_text(
            encoding="utf-8").strip()
    except OSError:
        return ""


def real_git() -> str:
    """The real `git`, with any GUARD 9 shim dir removed from the search path.

    🔴 THE INSTALLER MUST NOT BE SUBJECT TO THE GUARD IT INSTALLS. `run-tests.sh`
    puts a shim first on PATH, and a nested `install()` (every test that builds
    its own guard) then resolves `git` to THAT shim. `protected_paths` asks git
    to enumerate worktrees; when the outer shim refuses that call, the protected
    set silently comes back SMALLER and the new guard protects less.

    MEASURED: a mutant that made `_prot_match` accept relative operands
    SURVIVED for exactly this reason -- it caused the outer shim to refuse
    `git worktree list` inside `protected_paths`, so the victim's work-tree
    root dropped out of the protected set and the assertion it should have
    tripped no longer applied. A mutant that disables the guard by shrinking
    what the guard covers is not an isolated mutation, and the fix is to make
    the installer independent rather than to reword the test.

    A guard dir is identified STRUCTURALLY, by the ledger file `install()`
    writes beside the shim -- not by a name pattern a future dir could miss.
    """
    parts = os.environ.get("PATH", "").split(os.pathsep)
    clean = [d for d in parts
             if d and not (Path(d) / PROTECTED_LIST_NAME).exists()]
    found = shutil.which("git", path=os.pathsep.join(clean))
    # Fall back to the ordinary lookup rather than failing: a caller with no
    # real git on PATH has a different problem, and `install()` reports it.
    return found or (shutil.which("git") or "git")


def farm_is_complete(farm, src_names) -> bool:
    """Is `farm` a COMPLETE stand-in for git's exec-path?

    🔴 THE SET, NOT THE COUNT. A count matches when one helper is swapped for
    another; only the set catches a missing or renamed one. And `git` must be
    OUR COPY, not a symlink to the binary it is meant to shadow -- a farm whose
    `git` links to the real one is a farm that guards nothing while looking
    complete.

    Extracted so it can be tested directly: inside `install_exec_farm` the
    check is unreachable on a healthy box (the build starts from a clean
    directory and always produces the right set), and an unreachable guard is
    one nobody has watched work.
    """
    farm = Path(farm)
    try:
        have = set(os.listdir(farm))
    except OSError:
        return False
    if have != set(src_names):
        return False
    # 🔴 EVERY name git dispatches on must be OUR copy, not a link to the
    # binary it is meant to shadow. Checked by NAME -- `git` and `git-*` --
    # because that is the property git uses, and it holds whether the original
    # was a hardlink, a symlink or a copy.
    for n in have:
        if n == "git" or n.startswith("git-"):
            if (farm / n).is_symlink() or not (farm / n).is_file():
                return False
    return True


def install_exec_farm(guard_dir) -> str:
    """Own `git`'s OWN libexec, so a git-SPAWNED child is guarded too.

    🔴 THIS CLOSES THE RESIDUAL THIS MODULE PREVIOUSLY ONLY DOCUMENTED. Git
    PREPENDS `libexec/git-core` to PATH for every process it spawns, so a bare
    `git` inside a `!`-alias, a hook, a clean/smudge filter or `rebase --exec`
    resolves to the REAL binary and never reaches a PATH shim. That covered the
    case where the alias or hook is ALREADY IN a repo's own config, which no
    amount of scanning the CURRENT invocation can see.

    The farm is a directory of symlinks to every entry of `git --exec-path`
    that is NOT a dispatch name, plus a copy of the shim under EVERY dispatch
    name -- `git` and each of the ~181 `git-<verb>` entries. Exported as
    GIT_EXEC_PATH, it is what git hands its children, so the child's `git` is
    ours. Copying the shim only as `git` was the earlier, WITHDRAWN version:
    git dispatches on argv[0], so `git-config` reached the real binary.

    MEASURED, with an alias in the fixture's OWN `.git/config`:
        PATH shim only   -> rc 0, the victim gained a commit  (residual)
        + the farm       -> rc 99, refused, victim unchanged  (closed)
    and ordinary work is unaffected: status / log / commit / diff /
    `rebase --help` all rc 0 through the farm.

    ⚠ THE REMAINING ESCAPES ARE **TWO**, AND THEY WERE DERIVED, NOT ASSUMED.
    Six alias-body shapes were measured against the farm:

        !git …                    guarded      (resolves through the farm)
        !sh -c 'git …'            guarded
        !env git …                guarded
        !command git …            guarded
        !/abs/path/to/git …       🔴 ESCAPES   (PATH is never consulted)
        !touch <victim>/file      🔴 ESCAPES   (never invokes git at all)

    An earlier revision of this comment claimed the residual was ONE -- the
    shell case -- and omitted the absolute-path case entirely. Owning git's
    exec path bounds what GIT will resolve for a child; it cannot bind a
    command that bypasses resolution or never asks for git.

    Returns the farm path, or "" when the exec-path cannot be resolved (the
    caller reports that rather than proceeding as if it were armed).
    """
    guard_dir = Path(guard_dir)
    shim = guard_dir / "git"
    if not shim.exists():
        return ""
    try:
        r = subprocess.run([real_git(), "--exec-path"], capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    if r.returncode != 0 or not r.stdout.strip():
        return ""
    src_dir = Path(r.stdout.strip())
    if not src_dir.is_dir():
        return ""

    # 🔴 DERIVED EVERY RUN, AND VERIFIED AS A SET. `os.listdir(src_dir)` is
    # re-read on every call, so a helper added by a git upgrade is picked up
    # automatically -- this is not a snapshot ledger. MEASURED: 189 source
    # entries, 189 in the farm, sets equal.
    #
    # 🔴 AND IT FAILS CLOSED. An earlier revision `continue`d past any entry
    # that already existed, so a pre-existing NON-symlink was left in place and
    # the farm was returned as usable anyway -- MEASURED: a planted regular
    # file at `.git-archimport-wrapped` survived and `install_exec_farm` still
    # handed back a path. That is a hole in the fix for the
    # channel-vs-list lesson, which is the same shape all over again. Entries
    # are now REPLACED, and the final set is compared against the source before
    # the farm is declared usable.
    src_names = set(os.listdir(src_dir))
    farm = guard_dir / EXEC_FARM_NAME
    if farm.exists():
        shutil.rmtree(farm, ignore_errors=True)
    try:
        farm.mkdir(parents=True, exist_ok=True)
        for name in src_names:
            if name == "git" or name.startswith("git-"):
                # 🔴 EVERY DISPATCH NAME IS OURS. Substituting only `git` left
                # ~181 `git-<verb>` entries pointing at the real binary, and
                # git dispatches on argv[0] -- `git-config -f <denied>/config`
                # landed the incident's own signature without spelling `git`.
                shutil.copy2(shim, farm / name)
                continue
            (farm / name).symlink_to(src_dir / name)
        shutil.copy2(shim, farm / "git")
    except OSError:
        return ""

    if not farm_is_complete(farm, src_names):
        return ""
    return str(farm)


def protected_paths(*starts) -> list[str]:
    """Every path that IS the repo each start sits in: its `--git-common-dir`
    AND the root of every working tree attached to it.

    🔴 THE COMMON DIR IS NOT ENOUGH, AND THE GAP IS NOT THEORETICAL. It was
    MEASURED here: with only common dirs protected, `GIT_WORK_TREE=<the
    operator's clone> git checkout -f HEAD` was ALLOWED, because the work-tree
    root is the PARENT of `<clone>/.git` and so is under no protected prefix.
    Repo identity stayed truthful and innocent while the files would have landed
    in the operator's tree. The armed-environment battery in
    `scripts/tests/test_no_real_repo_writes.py` is what caught it.

    🔴 The COMMON dir, not the git dir. `~/workspace/devrc` and every
    `.claude/worktrees/agent-*` under it resolve to the SAME common dir, so
    protecting one protects all of them -- correct, because a write in a linked
    worktree lands on the real clone's refs and config, and a push from one
    reaches the real remote.

    A path that is not a repo, or that git cannot answer for, contributes
    nothing; the caller must treat an empty result as a failure rather than as
    "nothing to protect" (`install` does).
    """
    out: list[str] = []

    def add(p) -> None:
        real = str(Path(p).resolve())
        if real not in out:
            out.append(real)

    for s in starts:
        p = Path(s).expanduser()
        if not p.is_dir():
            continue
        try:
            r = subprocess.run(
                [real_git(), "-C", str(p), "rev-parse", "--path-format=absolute",
                 "--git-common-dir"],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode != 0 or not r.stdout.strip():
            continue
        add(r.stdout.strip())
        # Every attached working tree, the main one and every linked one. A
        # linked worktree lives OUTSIDE the common dir, so it needs its own
        # entry or `GIT_WORK_TREE=<that worktree>` walks straight past.
        try:
            w = subprocess.run(
                [real_git(), "-C", str(p), "worktree", "list", "--porcelain"],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if w.returncode == 0:
            for line in w.stdout.splitlines():
                if line.startswith("worktree "):
                    add(line[len("worktree "):].strip())

        # 🔴 A PROTECTED REPO'S LOCAL REMOTES ARE PART OF IT. Pushing to the
        # bare repository a protected clone pushes to is the same damage as
        # writing the clone -- and MEASURED, it was allowed: `git -C <fixture>
        # push <victim-bare> +HEAD:refs/heads/main` force-updated it at rc 0
        # with the ledger at 0. For the operator's own clone the analogue is
        # the PUBLIC repo, which the network rule already refuses; this closes
        # the local-bare case so a caller does not have to know to pass it.
        try:
            r2 = subprocess.run(
                [real_git(), "-C", str(p), "config", "--get-regexp", r"^remote\..*\.url$"],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if r2.returncode != 0:
            continue
        for line in r2.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            url = parts[1].strip()
            if url.startswith("file://"):
                url = url[len("file://"):]
            # Only LOCAL paths: a network remote is handled by the network rule,
            # and adding one here would put a URL in a path ledger.
            if not url.startswith("/"):
                continue
            add(url)
    return out


#: Kept as the old name so an out-of-tree caller does not break silently; the
#: behaviour it described was measurably insufficient, so it forwards.
protected_common_dirs = protected_paths


def main(argv: list[str] | None = None) -> int:
    """`python -m testlib.norepo <guard-dir> <candidate-repo>...`

    Resolves each candidate to its `--git-common-dir`, installs the shim, and
    prints ONE `protected=<path>` line per repo actually protected followed by
    `guard-dir=<dir>`. `run-tests.sh` reads that count: it is the difference
    between "armed against N real repositories" and the reassuring zero of a
    guard wired to nothing, and the runner PRINTS it either way rather than
    letting a silent zero read as protection.

    `--allow-no-repos` is the nix build sandbox's door: there is genuinely no
    real clone in it, and the NETWORK and CONFIG rules still apply.
    """
    import sys as _sys

    args = list(_sys.argv[1:] if argv is None else argv)
    allow = "--allow-no-repos" in args
    args = [a for a in args if a != "--allow-no-repos"]
    if len(args) < 2:
        print("usage: python -m testlib.norepo [--allow-no-repos] "
              "<guard-dir> <candidate-repo>...", file=_sys.stderr)
        return 2
    guard_dir, candidates = Path(args[0]), args[1:]
    repos = protected_paths(*candidates)
    # 🔴 `Path.home()/".gitconfig"` IS ALWAYS PROTECTED, not just whatever
    # GIT_CONFIG_GLOBAL happens to say right now. `run-tests.sh` REDIRECTS that
    # variable at a throwaway file for the run, and deriving the protected set
    # from the redirected value would protect the throwaway and leave the
    # operator's real file unprotected — the guard would then be pointed at the
    # decoy it created. The measured incident vector is `githooks/install.sh`
    # running `git config --global core.hooksPath` with NO env var set at all,
    # which resolves to exactly this path.
    # 🔴 F5 -- ASK GIT WHERE `--global` LIVES; do not hardcode `~/.gitconfig`.
    # MEASURED on this host: `~/.gitconfig` DOES NOT EXIST. git uses
    # `${XDG_CONFIG_HOME:-~/.config}/git/config`, and `git config --global
    # --list --show-origin` reports `file:/home/<user>/.config/git/config`. The
    # ledger was therefore protecting a file that is never written while the
    # operator's real global config was unprotected -- non-exploitable here
    # only because the store path it would be reached through is read-only,
    # i.e. blocked by the filesystem rather than by this guard.
    cfg: list[str] = []

    def _add(x) -> None:
        v = str(Path(x).expanduser())
        if v not in cfg:
            cfg.append(v)

    xdg = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    _add(Path(xdg) / "git" / "config")     # where git actually looks first
    _add(Path.home() / ".gitconfig")       # the other candidate, still guarded
    _add(Path("/etc/gitconfig"))
    if os.environ.get("GIT_CONFIG_GLOBAL"):
        _add(os.environ["GIT_CONFIG_GLOBAL"])
    if os.environ.get("GIT_CONFIG_SYSTEM"):
        _add(os.environ["GIT_CONFIG_SYSTEM"])
    # ...and whatever git itself reports, with our own overrides stripped so
    # the answer is the OPERATOR's, not this run's redirect.
    try:
        _env = {k: v for k, v in os.environ.items()
                if not k.startswith("GIT_CONFIG")}
        _r = subprocess.run(
            [real_git(), "config", "--global", "--list", "--show-origin"],
            capture_output=True, text=True, timeout=15, env=_env)
        for _line in _r.stdout.splitlines():
            if _line.startswith("file:") and "\t" in _line:
                _add(_line.split("\t", 1)[0][len("file:"):])
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        install(guard_dir, repos, cfg, allow_no_repos=allow)
    except RuntimeError as exc:
        print(f"norepo: {exc}", file=_sys.stderr)
        return 2
    farm = install_exec_farm(guard_dir)
    if farm:
        print(f"exec-farm={farm}")
    else:
        print("exec-farm=UNAVAILABLE")
    for r in repos:
        print(f"protected={r}")
    for c in cfg:
        print(f"protected-config={c}")
    print(f"guard-dir={guard_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via run-tests.sh
    raise SystemExit(main())
