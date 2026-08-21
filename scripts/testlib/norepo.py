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


def shim_body(real_git: str, log: Path, protected_list: Path,
              protected_config: Path) -> str:
    """The POSIX-sh body of the `git` shim. No shebang -- write_exec owns it.

    🔴 EVERY BRANCH EITHER `exec`s THE REAL BINARY OR REFUSES WITH A NON-ZERO
    STATUS. There is no path that returns success without having done the work:
    a git shim that silently no-opped a `commit` would leave the test asserting
    against a commit that does not exist, and the failure would point anywhere
    but here.
    """
    always_read = "|".join(sorted(set(ALWAYS_READ)))
    network = "|".join(NETWORK_SUBCOMMANDS)
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

# $1 = reason, rest = the original argv.
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
  echo "  config, or reach a network remote. On 2026-08-21 this tier set" >&2
  echo "  core.bare on the operator's clone, renamed its main branch and put" >&2
  echo "  ~40 fixture commits over the PUBLIC repo's default branch." >&2
  echo "  Build the fixture repo under tmp_path and target it explicitly." >&2
  echo "  A legitimate READ belongs in norepo.PROTECTED_READ_FORMS." >&2
  exit {REFUSED_EXIT}
}}

# ---- parse git's GLOBAL options, stopping at the subcommand ------------------
# Only -C and --git-dir change WHICH repo is targeted. -c/--work-tree/
# --namespace/--config-env/--super-prefix take a value that must be skipped so
# it cannot be mistaken for the subcommand.
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
    # 🔴 `-c key=value` is CAPTURED, not skipped. Skipping it is how
    # `git -c alias.x='!git -C <victim> commit' x` and
    # `git -c remote.origin.url=<github> push` both walked straight past an
    # earlier revision of this shim -- MEASURED, victim commit landed and the
    # push reached GitHub.
    -c) want=K; continue ;;
    --config-env) want=E; continue ;;
    --config-env=*) cvals="$cvals
env:${{a#--config-env=}}"; continue ;;
    --work-tree|--namespace|--super-prefix) want=X; continue ;;
    -*) continue ;;
    *) sub="$a"; break ;;
  esac
done

# ---- FAST PATH ---------------------------------------------------------------
case "$sub" in
  ''|{always_read}) _pass "$@" ;;
esac

# 🔴 PATHNAME EXPANSION OFF from here down. The loops below deliberately
# word-split `$rest` and `$urls`, and an unquoted `$var` in a `for` list is
# GLOBBED as well as split -- so a `*` inside a refspec or a path would expand
# against the cwd and the guard would inspect filenames instead of arguments.
# `exec` resets this for the real binary, so nothing downstream sees it.
set -f

# 🔴 IS THIS PATH INSIDE A PROTECTED REPOSITORY? One definition, used by BOTH
# the resolved-repo check and the environment check below. A prefix match, not
# equality: a linked worktree's git dir is `<protected>/.git/worktrees/<name>`,
# and an equality test would report it as a different, unprotected repository.
_prot_match() {{
  [ -n "$1" ] || return 1
  # 🔴 ABSOLUTE PATHS ONLY, and this is a CORRECTNESS rule, not a shortcut.
  # `readlink -f` resolves a relative string against THIS SHIM's cwd -- which
  # under `run-tests.sh` IS the protected repo. So every non-path operand
  # matched: `worktree list` flagged the word `list`, `worktree add -b topic`
  # flagged `topic`, and `-c user.email=t@t` flagged `t@t`. MEASURED: 15 tests
  # failed on a correct tree, i.e. this guard made itself a permanently-red gate
  # -- the failure mode claude/RULES.md says trains everyone to click through.
  #
  # ⚠ RESIDUAL, stated rather than hidden: a RELATIVE path aimed at a protected
  # repo is not matched here. git resolves it against the CHILD's cwd, which
  # this shim cannot know; the repo RESOLUTION below covers the cwd case, and
  # every environment variable and injected config value that matters in
  # practice is absolute.
  case "$1" in
    /*) ;;
    *) return 1 ;;
  esac
  _rp=$(readlink -f "$1" 2>/dev/null || echo "$1")
  while IFS= read -r _p; do
    [ -z "$_p" ] && continue
    case "$_rp" in
      "$_p"|"$_p"/*) return 0 ;;
    esac
  done < "$PROT"
  return 1
}}

# ---- GLOBAL `-c` / `--config-env` INJECTION ---------------------------------
# 🔴 THE SAME CONFIG VALUES, ON THE COMMAND LINE. These reach git without
# touching a file and without changing the resolved repo, so neither the config
# redirect nor repo resolution can see them. MEASURED against an earlier
# revision of this shim, from an INNOCENT fixture:
#
#   git -c 'alias.x=!git -C <victim> commit --allow-empty -m PWNED' x
#     -> rc 0, the victim gained a commit
#   git -c remote.origin.url=git@github.com:<real>/devrc.git push origin HEAD:main
#     -> "To github.com:<real>/devrc.git  ! [rejected]" -- it REACHED GitHub
#
# 🔴 AND THE ALIAS CASE IS WHY THE PATH SHIM ALONE IS NOT ENOUGH. Git PREPENDS
# its own `libexec/git-core` to PATH for every child it spawns, so the `git`
# inside a `!`-alias resolves to the REAL binary and never sees this shim. The
# shim is NOT inherited by git-spawned children; the module docstring says so
# explicitly rather than implying coverage we do not have.
# $1 = "key=value" (or "env:key=ENVVAR"); remaining args = the original argv.
_scan_cval() {{
  case "$1" in
    env:*)
      _e="${{1#env:}}"; _ck="${{_e%%=*}}"; _ev="${{_e#*=}}"
      eval "_cv=\\${{$_ev:-}}"
      ;;
    *) _ck="${{1%%=*}}"; _cv="${{1#*=}}" ;;
  esac
  [ "$_ck" = "$1" ] && return 0
  case "$_ck" in
    alias.*)
      case "$_cv" in
        "!"*) _refuse "-c $_ck is a SHELL-ESCAPE alias ($_cv). Git prepends its own libexec to PATH, so the git inside it would NOT be guarded" "$@" ;;
      esac
      ;;
  esac
  if [ -n "$_cv" ] && _prot_match "$_cv"; then
    _refuse "-c $_ck=$_cv points into a PROTECTED repository; this channel writes no file, so the config redirect cannot see it" "$@"
  fi
  case "$_cv" in
    *://*|*@*:*) _refuse "-c $_ck=$_cv is a NETWORK remote; this tier is hermetic" "$@" ;;
  esac
}}
if [ -n "$cvals" ]; then
  _oifs="$IFS"; IFS='
'
  for _c in $cvals; do
    [ -n "$_c" ] && _scan_cval "$_c"
  done
  IFS="$_oifs"
fi

# ---- THE ENVIRONMENT VECTOR, WHICH RESOLUTION ALONE DOES NOT COVER -----------
# 🔴 THIS IS THE HALF THAT MAKES THE GUARD ENVIRONMENT-AWARE RATHER THAN
# PATH-VALIDATING, AND IT IS NOT REDUNDANT WITH THE RESOLUTION BELOW.
#
# Two different kinds of variable, and only one of them is caught by asking git
# which repo it is in:
#
#   * GIT_DIR / GIT_COMMON_DIR change the repo's IDENTITY. `rev-parse
#     --git-common-dir` below inherits them and answers with the repo git will
#     actually use, so those are already covered — that is why the guard
#     delegates resolution to git instead of parsing `-C` itself.
#
#   * GIT_WORK_TREE / GIT_INDEX_FILE / GIT_OBJECT_DIRECTORY /
#     GIT_ALTERNATE_OBJECT_DIRECTORIES do NOT. They leave the repo identity
#     alone and redirect WHERE THE WRITE LANDS. With cwd in a throwaway fixture
#     and GIT_WORK_TREE naming the operator's clone, `git checkout -f` or
#     `git reset --hard` writes files into the OPERATOR'S WORKING TREE while
#     every path argument, and the resolved repo, look entirely innocent.
#     MEASURED, and pinned by `test_no_real_repo_writes.py`'s armed-environment
#     battery.
#
# `run-tests.sh` unsets all of them, but that is a claim about SETUP: it stops
# what the runner INHERITS, and does nothing about a test that builds its own
# subprocess environment. So the shim refuses them here as well. The two layers
# are deliberately not the same fix.
for _v in GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE GIT_INDEX_FILE \
          GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES; do
  eval "_val=\\${{$_v:-}}"
  if [ -n "$_val" ] && _prot_match "$_val"; then
    _refuse "$_v points into a PROTECTED repository ($_val); it would redirect this write there whatever the arguments say" "$@"
  fi
done

# ---- THE CHANNEL A FILE-WATCHING GUARD CANNOT SEE, EVEN IN PRINCIPLE ---------
# 🔴 GIT_CONFIG_COUNT / GIT_CONFIG_KEY_<n> / GIT_CONFIG_VALUE_<n> are NOT in
# `man git`, and git honours them. They set arbitrary config for ONE invocation
# and TOUCH NO FILE. MEASURED on git 2.55 against an earlier revision of this
# very shim:
#
#   GIT_CONFIG_GLOBAL=<decoy> GIT_CONFIG_SYSTEM=/dev/null
#     git config --get core.hooksPath            -> <unset>        (guard held)
#   ...plus GIT_CONFIG_COUNT=1 KEY_0=core.hooksPath VALUE_0=/tmp/PWNED-hooks
#     git config --get core.hooksPath            -> /tmp/PWNED-hooks
#   decoy file afterwards: 0 bytes, NEVER OPENED
#
# So the `GIT_CONFIG_GLOBAL` redirect above was not merely incomplete for this
# vector — it was watching a channel the vector does not use. A guard is bounded
# by the CHANNEL it observes, not by the completeness of its list.
#
# 🔴 THIS CHECK IS DELIBERATELY HERE, IN THE SHIM, AT CALL TIME. These variables
# are per-invocation and live in the CHILD's environment, so a check run in the
# plugin's process — or around a test — reads its OWN environment, comes back
# clean, and PASSES while the child is fully injected. The shim is the only
# place that sees the environment the guarded invocation will actually use.
#
# 🔴 AND IT IS SCOPED, NOT A BLANKET BAN. `scripts/analyze-service-index/
# commit.sh` uses exactly this mechanism ON PURPOSE to NEUTRALISE hooks
# (`GIT_CONFIG_KEY_0=core.hooksPath` at an empty dir), and its suite drives that
# path. Refusing all of it would be a permanently-red gate. What is refused is
# an injected value aimed INTO a protected path, or at a network remote.
#
# ⚠ RESIDUAL, STATED RATHER THAN HIDDEN: an injected value pointing somewhere
# harmless-to-us (a tmpdir) is still arbitrary config, and `core.hooksPath` at a
# tmpdir is arbitrary CODE EXECUTION during the run. That is outside this
# guard's mandate (the operator's repo) and inside `commit.sh`'s legitimate
# usage, so it is permitted here and documented rather than silently allowed.
case "${{GIT_CONFIG_COUNT:-}}" in
  ''|*[!0-9]*) : ;;
  *)
    _n=0
    while [ "$_n" -lt "${{GIT_CONFIG_COUNT}}" ]; do
      eval "_ik=\\${{GIT_CONFIG_KEY_$_n:-}}"
      eval "_iv=\\${{GIT_CONFIG_VALUE_$_n:-}}"
      # 🔴 THE SAME SCANNER as the `-c` form. It had its own copy of the checks
      # and was missing the alias case, so `alias.y=!git -C <victim> commit`
      # injected through the environment landed a commit on the victim while
      # the byte-identical `-c` form was refused. One predicate, one place.
      [ -n "$_ik" ] && _scan_cval "$_ik=$_iv" "$@"
      _n=$(( _n + 1 ))
    done
    ;;
esac

# The arguments AFTER the subcommand, space-prefixed, for the read-form match.
rest=''; seen=0
for a in "$@"; do
  if [ "$seen" = 0 ]; then
    [ "$a" = "$sub" ] && seen=1
    continue
  fi
  rest="$rest $a"
done

# ---- PROTECTED CONFIG FILES --------------------------------------------------
# --global / --system reach ~/.gitconfig and /etc/gitconfig without touching any
# repo at all, so no amount of repo protection sees them. githooks/install.sh
# writes the global one for real, so this is a live shape, not a hypothetical.
if [ "$sub" = config ]; then
  cfgfile=''
  case "$rest" in
    *--global*) cfgfile="${{GIT_CONFIG_GLOBAL:-$HOME/.gitconfig}}" ;;
    *--system*) cfgfile="${{GIT_CONFIG_SYSTEM:-/etc/gitconfig}}" ;;
  esac
  # 🔴 `--file <path>` / `-f <path>` names a config file DIRECTLY, in a repo the
  # resolver reports as innocent. MEASURED: `git -C <fixture> config --file
  # <victim>/.git/config core.bare true` set core.bare on the victim -- one of
  # the two actual incident damages -- and was NOT refused.
  _cfw=''
  for a in $rest; do
    case "$_cfw" in F) cfgfile="$a"; _cfw=''; continue ;; esac
    case "$a" in
      --file|-f) _cfw=F ;;
      --file=*) cfgfile="${{a#--file=}}" ;;
    esac
  done
  if [ -n "$cfgfile" ] && _prot_match "$cfgfile"; then
    case "$rest" in
      *--get*|*--list*|*" "-l*) _pass "$@" ;;
    esac
    _refuse "config --file/-f targets $cfgfile, inside a PROTECTED repository" "$@"
  fi
  if [ -n "$cfgfile" ]; then
    rcfg=$(readlink -f "$cfgfile" 2>/dev/null || echo "$cfgfile")
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

# ---- resolve the repo this invocation targets --------------------------------
# 🔴 --git-common-dir, NEVER --git-dir: a linked worktree's git dir is a
# subdirectory of the real clone's, and its refs/remotes/config ARE the real
# clone's. Resolving the per-worktree dir would report a worktree of a protected
# repo as unprotected -- the exact misconception that makes "just use a
# worktree" the intuitive and wrong containment answer for this bug.
#
# `init` and `clone` name a DESTINATION that may not be a repo yet, so resolve
# from there. `git init <dir>` on a path that IS already a repo is how a working
# clone acquires core.bare = true.
# 🔴 SUBCOMMANDS THAT NAME A DESTINATION INSIDE A DENIED TREE. The repo they
# RUN in is innocent; the path they WRITE to is not. MEASURED, both unrefused:
#   git init --separate-git-dir <victim>/planted-gitdir <newrepo>  -> planted
#   git -C <fixture> worktree add --detach <victim>/planted-worktree -> planted
for a in $rest; do
  case "$a" in
    -*) continue ;;
  esac
  case "$sub" in
    worktree|submodule)
      if _prot_match "$a"; then
        _refuse "$sub would write to $a, inside a PROTECTED repository" "$@"
      fi
      ;;
  esac
done
_sgd=''
_sgw=''
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

case "$sub" in
  init|clone)
    dest=''
    for a in $rest; do
      case "$a" in
        -*) continue ;;
        *) dest="$a" ;;
      esac
    done
    # 🔴 THE DESTINATION IS RELATIVE TO `-C`, NOT TO THIS SHIM'S CWD. `git -C X
    # init .` means "init X", because git chdirs to X BEFORE reading the
    # positional. MEASURED as a FALSE POSITIVE: a fixture running
    # `git -C <tmp_path>/plain-repo init -q -b trunk .` was refused, because the
    # `.` was resolved against the runner's cwd -- the repo root -- and landed
    # on the protected repo. A guard that refuses a legitimate fixture is a
    # permanently-red gate, which is the failure mode that trains everyone to
    # click through. GUARD 9's own per-target accounting is what surfaced it.
    case "$dest" in
      '') : ;;
      /*) tgt="$dest" ;;
      *)  tgt="${{tgt:-.}}/$dest" ;;
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
  rcommon=$(readlink -f "$common" 2>/dev/null || echo "$common")
  _prot_match "$common" && protected=1
fi

# ---- NETWORK REMOTES: refused in ANY repo, protected or not ------------------
case "$sub" in
  {network})
    urls=$( ( cd "${{tgt:-.}}" 2>/dev/null && "$REAL" config --get-regexp '^remote\\..*\\.url$' 2>/dev/null ) )
    urls="$urls $rest"
    for u in $urls; do
      case "$u" in
        file://*|/*|./*|../*) continue ;;
        *://*) _refuse "would contact the NETWORK remote $u; this tier is hermetic" "$@" ;;
        *@*:*) _refuse "would contact the NETWORK remote $u; this tier is hermetic" "$@" ;;
      esac
    done
    ;;
esac

# ---- PROTECTED REPO: only an enumerated READ form is forwarded ---------------
if [ "$protected" = 1 ]; then
  # 🔴 `status` is a READ that WRITES: it refreshes and rewrites `.git/index`.
  # MEASURED on a protected repo -- index bytes and mtime both changed. It is
  # far too common in this suite to refuse (that would be a permanently-red
  # gate), and the write is a cache refresh that cannot lose data, so the shim
  # forwards it with `--no-optional-locks`, which git provides for exactly this
  # and which was MEASURED to leave the index byte-identical with identical
  # output.
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

    real = shutil.which("git")
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
                ["git", "-C", str(p), "rev-parse", "--path-format=absolute",
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
                ["git", "-C", str(p), "worktree", "list", "--porcelain"],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if w.returncode == 0:
            for line in w.stdout.splitlines():
                if line.startswith("worktree "):
                    add(line[len("worktree "):].strip())
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
    cfg: list[str] = []
    for p in (Path.home() / ".gitconfig",
              Path(os.environ.get("GIT_CONFIG_GLOBAL") or Path.home() / ".gitconfig"),
              Path("/etc/gitconfig"),
              Path(os.environ.get("GIT_CONFIG_SYSTEM") or "/etc/gitconfig")):
        s = str(p)
        if s not in cfg:
            cfg.append(s)
    try:
        install(guard_dir, repos, cfg, allow_no_repos=allow)
    except RuntimeError as exc:
        print(f"norepo: {exc}", file=_sys.stderr)
        return 2
    for r in repos:
        print(f"protected={r}")
    for c in cfg:
        print(f"protected-config={c}")
    print(f"guard-dir={guard_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via run-tests.sh
    raise SystemExit(main())
