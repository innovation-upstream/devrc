#!/usr/bin/env bash
#
# gate.sh — the ONE invocation surface for devrc's test gate, whose exit status
# is trustworthy.
#
# 🔴 WHY THIS EXISTS. `scripts/run-tests.sh` and `scripts/run-node-tests.sh` have
# always exited truthfully; what kept failing was READING that status. Both
# emit thousands of lines, so every consumer — CI notes, the pre-push tier,
# agents, humans — wraps them in a pipe:
#
#     bash scripts/run-tests.sh 2>&1 | tail -40 ; echo "rc=$?"
#     nix build .#checks.x86_64-linux.pytests 2>&1 | tail -60 ; echo "BUILD_RC=$?"
#
# and a pipeline's status is the LAST command's. On 2026-08-11 four agents hit
# this independently, reporting `exit code 0` over output containing
# `RESULT: FAIL` and `failed=1`; the same day's rules audit recorded
# `BUILD_RC=0` over a genuinely red `nix build`. The repo's own CLAUDE.md had to
# tell everyone to count `PASSED`/`FAILED` lines instead of reading the status
# — a workaround for a bug, promoted to house style.
#
# The fix is two-sided and BOTH sides are needed:
#
#   1. The runners now put the status IN THE CONTENT, on exactly one line
#      (`RESULT: FAIL (exit=1)`), from a single writer behind an EXIT trap. So
#      the truth survives a pipe even when someone bypasses this script.
#   2. This script removes the REASON to pipe. It sends the full output to a
#      LOG FILE (a redirect, not a pipe), prints a bounded summary — the SUMMARY
#      block and the verdict, ~40 lines — and exits with the status it actually
#      observed. Nothing here runs after the status is captured except things
#      whose own status is discarded.
#
# And then it checks the two against each other, because "the exit code is
# truthful now" is a claim like any other:
#
#   * status non-zero + content says PASS  -> FATAL, exit 90
#   * status zero     + content says FAIL  -> FATAL, exit 90
#   * status zero     + no verdict at all  -> FATAL, exit 90 (truncated run)
#   * `panic: test timed out` anywhere     -> FATAL, exit 90
#
# A disagreement is never resolved in favour of the reassuring side. Exit 90 is
# deliberately its own code: it means "this gate could not vouch for its own
# answer", which is a different finding from "the tests failed".
#
# Usage:
#   scripts/gate.sh [--tier pytest|node|both] [--set hermetic|all]
#                   [--timeout SECS] [--log-dir DIR] [ROOT]
#
#     --tier both      (default) run both runners; the gate is red if either is.
#     --set            passed through to run-tests.sh (pytest tier only).
#     --timeout SECS   wall-clock cap per tier (default 3600, 0 disables). A
#                      tier that hits it is FAIL with reason=timeout, never a
#                      hang someone reads as "still going".
#     --log-dir DIR    where full logs land (default: a mktemp -d).
#
# Env:
#   DEVRC_GATE_TIMEOUT    default for --timeout, in seconds (default 3600). The
#                         flag wins over it.
#   DEVRC_GATE_SLOTS      how many gate runs of THIS script may execute at once
#                         per (uid, slot pool) — not per box; see the caveats in
#                         the SLOT LIMITER block (default 2; 0 disables it).
#   DEVRC_GATE_SLOT_WAIT  seconds to queue for a slot before giving up and
#                         running unslotted anyway (default 600). Fail-open: the
#                         wait always ends in a run, never in a refusal.
#   DEVRC_GATE_SLOT_DIR   which slot pool to join (default: a fixed per-uid path
#                         under /tmp). Naming one also opts a pytest-nested run
#                         back INTO the limiter — see the SLOT LIMITER block.
#   DEVRC_GATE_SLOT_POOL  set BY this script, not by you: the pool a gate run
#                         already holds this process tree's place in. A
#                         descendant that would join the same pool goes inert
#                         instead of queueing behind its own ancestor.
#   DEVRC_GATE_NO_REEXEC  =1 to run against the ambient PATH instead of
#                         re-entering `nix develop`. See the RE-EXEC block.
#   DEVRC_GATE_ENV        =1 means "already inside a sanctioned gate
#                         environment", set by the flake's own shellHook. It is
#                         what the re-exec below tests for, and what the GATE
#                         banner means when it says "not in a gate environment".
#   DEVRC_GATE_REEXEC     =1 is the re-exec loop guard, set BY this script on
#                         the way into `nix develop`. Setting it by hand
#                         suppresses the re-exec exactly once, which is not what
#                         DEVRC_GATE_NO_REEXEC does and is rarely what you want.
#   PYTEST_CURRENT_TEST   read, not set: its presence means "we are running
#                         inside a pytest process", which makes the slot limiter
#                         inert by default and suppresses the re-exec.
#
# Exit: 0 = every selected tier passed and agreed with its own content.
#       1 = a tier genuinely failed.
#       2 = a usage/precondition problem in this script.
#      90 = status/content disagreement, or a truncated run: NOT a verdict.
#
# TEST SEAM: DEVRC_GATE_PYTEST_RUNNER / DEVRC_GATE_NODE_RUNNER override the
# runner paths. They exist so the negative controls in
# scripts/tests/test_gate_exit_truthfulness.py can drive this script against a
# runner that is forced red, forced to hang, or forced to LIE (exit 0 while
# printing FAIL) in seconds instead of running the real 7k-test suite. They are
# a seam for tests, not a supported way to run the gate.

set -uo pipefail

# 🔴 RESOLVE OUR OWN PATH BEFORE ANYTHING CAN `cd`. `${BASH_SOURCE[0]}` is
# whatever the caller typed, and `cd <repo>/scripts && bash gate.sh` types a
# RELATIVE one. The re-exec below runs AFTER `cd "$ROOT"`, where that relative
# path no longer resolves — measured: `exec nix develop <repo> --command bash
# gate.sh …` died `bash: gate.sh: No such file or directory`, rc=127, with no
# GATE block, no RESULT line and no instruction, i.e. outside this script's
# entire documented exit set. Before the re-exec existed the same invocation
# printed a correct, actionable FATAL. Resolving once, here, is what makes
# GATE_SELF safe to use from any working directory later in the file.
GATE_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)/$(basename "${BASH_SOURCE[0]}")"
if [ ! -f "$GATE_SELF" ]; then
  echo "gate: FATAL — cannot resolve my own path from '${BASH_SOURCE[0]}'" >&2
  exit 2
fi

TIER="both"
SET="hermetic"
TIMEOUT="${DEVRC_GATE_TIMEOUT:-3600}"
LOG_DIR=""
ROOT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tier) TIER="${2:-both}"; shift; [ $# -gt 0 ] && shift ;;
    --tier=*) TIER="${1#*=}"; shift ;;
    --set) SET="${2:-hermetic}"; shift; [ $# -gt 0 ] && shift ;;
    --set=*) SET="${1#*=}"; shift ;;
    --timeout) TIMEOUT="${2:-3600}"; shift; [ $# -gt 0 ] && shift ;;
    --timeout=*) TIMEOUT="${1#*=}"; shift ;;
    --log-dir) LOG_DIR="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
    --log-dir=*) LOG_DIR="${1#*=}"; shift ;;
    # Print the header comment up to the first line of code, rather than a
    # hardcoded range: `2,70p` silently truncated --help the moment the header
    # grew, which is a help text that lies about the flags it documents.
    -h|--help) awk 'NR>1 { if ($0 !~ /^#/) exit; print }' "$GATE_SELF" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ROOT="$1"; shift ;;
  esac
done

case "$TIER" in
  pytest|node|both) : ;;
  *) echo "gate: FATAL — unknown --tier '$TIER' (want pytest|node|both)" >&2; exit 2 ;;
esac
case "$TIMEOUT" in
  ''|*[!0-9]*) echo "gate: FATAL — --timeout must be a whole number of seconds, got '$TIMEOUT'" >&2; exit 2 ;;
esac

# --- GUARD 9: NO TEST MAY OPERATE ON THE REPO THE SUITE RUNS FROM -------------
# 🔴 BEFORE the ROOT block below, not after it: with GIT_DIR set and no
# GIT_WORK_TREE, git takes the CWD as the work tree, so
# `rev-parse --show-toplevel` returns `<repo>/scripts` and this script dies
# `exit 127` with no verdict. The 2026-08-21 incident, the reproduction, and the
# reason this block is spelled in three files rather than sourced from one are
# in `scripts/run-tests.sh`'s copy of this header. The SET is owned by
# `scripts/testlib/gitenv.py::REPO_POINTER_VARS`; every spelling is pinned
# two-way by `scripts/tests/test_git_repo_isolation.py`.
DEVRC_GIT_REPO_POINTERS=(
  GIT_DIR                            # the repository itself; beats -C
  GIT_WORK_TREE                      # the working tree
  GIT_COMMON_DIR                     # where refs/config actually live
  GIT_INDEX_FILE                     # the index a `git add` writes
  GIT_OBJECT_DIRECTORY               # where new objects are written
  GIT_ALTERNATE_OBJECT_DIRECTORIES   # extra object stores
  GIT_NAMESPACE                      # the ref namespace refs land in
  GIT_PREFIX                         # hook-injected pathspec prefix
  GIT_GRAFT_FILE                     # repo-scoped grafts
  GIT_SHALLOW_FILE                   # repo-scoped shallow list
  GIT_CONFIG                         # legacy: the file `git config` WRITES
)
# 🔴 AND GUARD 9's OWN SEAMS. `DEVRC_GITENV_PROTECT` redirects the DETECTOR at a
# different repository and `DEVRC_GITENV_MODE` decides whether it fails or
# merely reports; #683's audit measured `DEVRC_GITENV_PROTECT=":"` producing
# `protected-git-dirs=0` and a GREEN run over a real escape, and
# `=/nonexistent/x` producing a marker line that asserted coverage it did not
# have. One inherited variable defeating every layer is the bug this guard
# exists for. Owned by `testlib/gitenv.py::CONTROL_VARS`, pinned two-way.
DEVRC_GITENV_CONTROL_VARS=(
  DEVRC_GITENV_PROTECT               # which git dirs the detector watches
  DEVRC_GITENV_MODE                  # enforce | report | auto
)
unset "${DEVRC_GIT_REPO_POINTERS[@]}"
unset "${DEVRC_GITENV_CONTROL_VARS[@]}"

if [ -z "$ROOT" ]; then
  ROOT="$(git -C "$(dirname "$GATE_SELF")" rev-parse --show-toplevel 2>/dev/null || true)"
  [ -n "$ROOT" ] || ROOT="$(cd "$(dirname "$GATE_SELF")/.." && pwd)"
fi
cd "$ROOT" || { echo "gate: FATAL — cannot cd to ROOT=$ROOT" >&2; exit 2; }

PYTEST_RUNNER="${DEVRC_GATE_PYTEST_RUNNER:-$ROOT/scripts/run-tests.sh}"
NODE_RUNNER="${DEVRC_GATE_NODE_RUNNER:-$ROOT/scripts/run-node-tests.sh}"

if [ -z "$LOG_DIR" ]; then
  LOG_DIR="$(mktemp -d -t devrc-gate-XXXXXX)"
fi
mkdir -p "$LOG_DIR" || { echo "gate: FATAL — cannot create log dir $LOG_DIR" >&2; exit 2; }

# --- IS THIS A NESTED INVOCATION? ----------------------------------------------
# GATE_NESTED — "we are inside a pytest process". Same nesting signal
# `run-tests.sh` uses to force nested runs serial, and inherited by child
# processes (measured there, serial and under xdist), so it catches a test
# spawning this script through a wrapper no seam variable touches. Both features
# below consult it, for different reasons and with different consequences.
#
# 🔴 THE RUNNER SEAMS NO LONGER SUPPRESS THE RE-EXEC, AND THAT IS A FIX. There
# used to be a second flag here — GATE_SEAMED, "a test is driving this script
# against a fake runner" — sitting FIRST in the re-exec condition. Every test in
# `scripts/tests/test_gate_slots_and_reexec.py` sets those seams, so that
# short-circuit fired in all of them and the three guards behind it were never
# evaluated by anything. Measured, one mutation at a time against the whole
# file: deleting the DEVRC_GATE_NO_REEXEC guard, the DEVRC_GATE_ENV guard, or
# the DEVRC_GATE_REEXEC loop guard each left all 14 tests PASSING. A guard no
# test can reach pins nothing, and the loop guard is the one whose failure mode
# this file itself calls an unkillable fork bomb.
#
# GATE_NESTED alone still keeps the re-exec out of every ordinary suite run,
# because PYTEST_CURRENT_TEST is set in all of them; a test that wants to reach
# the guards now has to pop it deliberately, which is exactly the opt-in the
# seams were silently providing to everybody.
GATE_NESTED=0
[ -n "${PYTEST_CURRENT_TEST:-}" ] && GATE_NESTED=1

# --- RE-EXEC INTO THE REPO'S OWN DEV SHELL -------------------------------------
# 🔴 WHY. `run-tests.sh` refuses to run outside an environment carrying its
# REQUIRED_TOOLS, and prints the exact `nix develop …` line that fixes it.
# MEASURED 2026-09-08 across every gate log dir still on this box: 101 of 382
# runs (26%) died on that FATAL — `logrotate dash` missing — with the pytest
# tier never starting. Each was a human or an agent typing `scripts/gate.sh`,
# reading the instruction, and typing it again with the prefix; 91 of the 101
# ran the node tier anyway, paying that suite twice.
#
# ⚠ AN EARLIER VERSION OF THIS COMMENT SAID "100 of 100", AND THAT NUMBER WAS AN
# ARTEFACT OF HOW THE POPULATION WAS SELECTED. LOG_DIR defaults to
# `mktemp -d -t devrc-gate-XXXXXX`, so a run INSIDE `nix develop` lands under
# nix's per-shell TMPDIR and a run OUTSIDE it lands in bare /tmp. Globbing
# `/tmp/devrc-gate-*` therefore selects exactly the runs launched outside the
# dev shell — the failing kind, by construction — and 100% of them failing says
# nothing beyond that. Counting both locations: outside, 101 dirs / 101 FATALs;
# inside, 281 dirs / 0. 26% is the honest figure, and it is still roughly one
# gate invocation in four thrown away on an instruction the script can follow
# itself.
#
# The message was never wrong; making a correct instruction be re-typed by hand
# is the defect. So: if we are not already in a sanctioned gate environment and
# the repo has a flake, re-enter it and run the SAME arguments there.
#
# 🔴 THE RE-EXEC IS FULLY RESOLVED, NOT `"$@"`. ROOT may have been derived from
# the script's own location rather than typed, and LOG_DIR may be a mktemp dir
# this process just created — replaying the original argv would re-derive both
# INSIDE the new shell, where `mktemp` honours nix's per-shell TMPDIR and would
# silently choose a DIFFERENT log dir from the one just announced. Passing the
# resolved values through makes the inner run land exactly where the outer one
# said it would.
#
# 🔴 LOOP GUARD. `DEVRC_GATE_ENV=1` comes from the flake's shellHook, so the
# normal exit condition is that the inner run sees it. If that hook ever stops
# setting it, this would re-exec forever; `DEVRC_GATE_REEXEC` is set by US and
# breaks the cycle on the second pass regardless. Belt and braces, because the
# failure mode of getting it wrong is an unkillable fork bomb rather than a
# wrong answer. Opt out entirely with DEVRC_GATE_NO_REEXEC=1.
if [ "$GATE_NESTED" -eq 0 ] \
   && [ "${DEVRC_GATE_NO_REEXEC:-0}" != "1" ] \
   && [ "${DEVRC_GATE_ENV:-0}" != "1" ] \
   && [ "${DEVRC_GATE_REEXEC:-0}" != "1" ] \
   && [ -f "$ROOT/flake.nix" ] \
   && command -v nix >/dev/null 2>&1; then
  echo "gate: not in a gate environment (DEVRC_GATE_ENV unset) — re-entering \`nix develop $ROOT\`."
  echo "gate: (set DEVRC_GATE_NO_REEXEC=1 to run against the ambient PATH instead)"
  export DEVRC_GATE_REEXEC=1
  # `exec`, so there is no wrapper process to swallow the inner status — the
  # whole point of this script is that its own exit code is readable.
  exec nix develop "$ROOT" --command bash "$GATE_SELF" \
      --tier "$TIER" --set "$SET" --timeout "$TIMEOUT" --log-dir "$LOG_DIR" "$ROOT"
fi

# --- SLOT LIMITER: BOUND HOW MANY GATES RUN AT ONCE ----------------------------
# 🔴 WHY. Nothing serialised gate runs, and on a box shared by dozens of agent
# sessions they all start their own. That contention is real and its mechanism
# is uncontroversial: 30–50 concurrent full-suite runs on 24 cores oversubscribe
# the machine several times over, and the suite is full of timing-sensitive
# waits that drift toward their deadlines when it does.
#
# ⚠ WHAT THE AVAILABLE MEASUREMENT DOES AND DOES NOT ESTABLISH — READ THIS
# BEFORE QUOTING A NUMBER FROM IT. Across 237 real runs (the runner's own
# `run=NNNNs`), bucketed by how many other runs overlapped, the median gate rose
# monotonically from 14.5 min (0 others) to 48.9 min (6+ others), a ratio of
# 3.4x. That association is real. It is NOT a measurement of contention's
# magnitude, because the bucketing variable is partly a FUNCTION of the outcome:
# a longer run overlaps more runs by construction, so long runs sort themselves
# into the high-overlap buckets whether or not overlap costs anything. This is
# length-biased sampling. A null Monte Carlo — 237 runs, Poisson arrivals,
# durations drawn from a lognormal fitted to the same marginal, ZERO interaction
# between runs — reproduces the monotone shape, the 14.5 min baseline, and about
# 2.06x of the 3.4x. So the causal magnitude is NOT established by this data,
# and the earlier "3.4x, and SUPERLINEAR, therefore the excess must be spawn
# storms" inference does not follow from it: an unbiased estimate would need
# durations compared at randomised or instrumented concurrency, which nobody
# has collected.
#
# What the same window does support without that bias, because it is a count and
# not a bucketed median: 35 of the 117 red runs died on SIGTERM at the 3600s
# `--timeout` cap, spending an hour of wall clock to produce no verdict at all.
# That is the failure this limiter is built against, and it is enough on its own.
#
# 🔴 THE SLOT IS TAKEN BEFORE `run_tier`, SO QUEUE TIME IS NOT TEST TIME. The
# --timeout cap starts when the tier starts. A run that waited for a slot still
# gets its full budget; queueing can never manufacture the timeout it exists to
# prevent.
#
# 🔴 WHAT "FAIL-OPEN" DOES AND DOES NOT PROMISE — stated precisely, because the
# sentence that used to sit here ("Nothing here can change the verdict") was
# false in two ways and a test drove straight through one of them.
#   * A malformed DEVRC_GATE_SLOTS / DEVRC_GATE_SLOT_WAIT exits 2 BEFORE any
#     tier runs. That is a refusal, deliberately: a typo'd slot count must not
#     be silently read as "unlimited". Exit 2 is the script's usage code and is
#     never a verdict about the tests.
#   * Once the configuration parses, no RUNTIME condition can block: a missing
#     `flock`, an uncreatable slot dir, a lock file that cannot be opened, or a
#     wait past DEVRC_GATE_SLOT_WAIT each print what happened and run anyway.
#   * What the limiter CAN still do is DELAY the start by up to
#     DEVRC_GATE_SLOT_WAIT. A caller whose own timeout is shorter than that
#     sees the delay as a hang and kills the run — which produces no verdict,
#     the exact outcome this feature exists to reduce. That is why the default
#     wait is 600s (see below) and not an hour, and why the queue prints a
#     heartbeat instead of going silent.
#
# 🔴 WHY DEVRC_GATE_SLOT_WAIT DEFAULTS TO 600s AND NOT 3600s. The two automated
# callers of this script — the harness Bash tool (600s) and `githooks/pre-push`
# — both have their own, shorter, budgets, so an hour-long queue cannot be
# waited out by either; it can only be killed. And a long wait buys little even
# when nobody kills it: at the far end of the wait we fail open and contend
# anyway, so the marginal value of the 40th minute of queueing is the
# difference between contending now and contending later. 600s is long enough
# to absorb a tier boundary and short enough that the fail-open happens while
# the caller is still listening.
#
# 🔴 WHY 2 SLOTS. Each slotted run now sizes itself at up to 8 xdist workers
# (see `run-tests.sh`'s budget block — the cap moved 4 -> 8 in the same change),
# so 2 slots is 16 of this box's 24 cores, leaving headroom for the `nix build`
# check tier, which takes no slot at all and cannot: it runs in a sandbox with
# no access to this pool, and its concurrency is nix's own `max-jobs`. The old
# pairing of "2 slots" with "4 workers" left the box two-thirds idle on a
# fully-slotted run.
#
# The slot dir is a FIXED path, deliberately not under $TMPDIR: every real run
# is inside `nix develop`, which gives each shell its own TMPDIR, so a
# TMPDIR-relative lock would give every run its own private set of slots and
# limit nothing. Per-uid so it cannot collide across users.
#
# 🔴 ONE SLOT PER GATE TREE, NOT PER GATE PROCESS. A gate run exports
# DEVRC_GATE_SLOT_POOL naming the pool it represents, and any descendant that
# would join THAT SAME pool goes inert instead of queueing. Without this, an
# outer full-gate run holding one of two slots had its own nested children —
# the suite's meta-tests, which spawn this script — queueing for the remaining
# slot behind their own ancestor, for up to DEVRC_GATE_SLOT_WAIT, inside a
# pytest process with a much shorter timeout of its own. Measured by an
# auditor with DEVRC_GATE_SLOT_DIR exported and the pool held: the suite's own
# positive control went RED on a 120s TimeoutExpired after printing
# `all 2 slot(s) busy — queueing`. A descendant can never usefully wait for a
# lock its ancestor is holding, so the ancestor's slot bounds the whole subtree.
#
# 🔴 NESTING vs TESTABILITY, and why DEVRC_GATE_SLOT_DIR settles the rest.
# Inside a pytest process the limiter is otherwise inert by default: N xdist
# workers each spawning gate.sh would queue behind unrelated real gate runs.
# But the limiter's OWN tests run inside pytest too, so a blanket "inert when
# nested" makes the one feature whose failure mode is a hang the one feature
# nothing exercises. Naming a slot dir is the opt-in. ⚠ Those tests must name a
# pool of their OWN and must SCRUB an inherited DEVRC_GATE_SLOT_DIR rather than
# passing the operator's through — inheriting it is how the audit above turned
# an operator's exported variable into a red suite.
GATE_SLOTS="${DEVRC_GATE_SLOTS:-2}"
GATE_SLOT_WAIT="${DEVRC_GATE_SLOT_WAIT:-600}"
case "$GATE_SLOTS" in ''|*[!0-9]*) echo "gate: FATAL — DEVRC_GATE_SLOTS must be a whole number, got '$GATE_SLOTS'" >&2; exit 2 ;; esac
case "$GATE_SLOT_WAIT" in ''|*[!0-9]*) echo "gate: FATAL — DEVRC_GATE_SLOT_WAIT must be a whole number, got '$GATE_SLOT_WAIT'" >&2; exit 2 ;; esac

GATE_SLOT_HELD=""      # which slot we hold, for the report line
GATE_SLOT_FD=""        # the fd carrying the lock; closed in the tier's children
# 🔴 WHY WE HOLD NO SLOT, in words. `slot: NONE HELD` on its own conflated five
# states — limiter disabled, inert because an ancestor holds this tree's place,
# inert because nested, no `flock`/no writable pool, and "waited and gave up".
# Only the LAST of those is a claim about contention, and a reader comparing two
# runs' durations needs to know which one they are looking at.
GATE_SLOT_STATE=""
GATE_SLOT_DIR="${DEVRC_GATE_SLOT_DIR:-/tmp/devrc-gate-slots-$(id -u 2>/dev/null || echo 0)}"

acquire_slot() {
  if [ -n "${DEVRC_GATE_SLOT_POOL:-}" ] && [ "${DEVRC_GATE_SLOT_POOL}" = "$GATE_SLOT_DIR" ]; then
    GATE_SLOT_STATE="INERT — an ancestor gate run already holds this process tree's place in $GATE_SLOT_DIR"
    echo "gate: slot limiter INERT (an ancestor gate run already represents this tree in the pool)."
    return 0
  fi
  # From here on, everything we spawn is inside our subtree; claim the pool for
  # it whether or not we end up holding a lock, because a descendant queueing
  # for a pool this run already failed open on is the same deadlock in a hat.
  export DEVRC_GATE_SLOT_POOL="$GATE_SLOT_DIR"
  if [ "$GATE_NESTED" -eq 1 ] && [ -z "${DEVRC_GATE_SLOT_DIR:-}" ]; then
    GATE_SLOT_STATE="INERT — nested inside pytest and no DEVRC_GATE_SLOT_DIR named"
    echo "gate: slot limiter INERT (nested inside pytest, and no DEVRC_GATE_SLOT_DIR named)."
    return 0
  fi
  if [ "$GATE_SLOTS" -le 0 ]; then
    GATE_SLOT_STATE="DISABLED — DEVRC_GATE_SLOTS=0"
    echo "gate: slot limiter DISABLED (DEVRC_GATE_SLOTS=0)."
    return 0
  fi
  if ! command -v flock >/dev/null 2>&1; then
    GATE_SLOT_STATE="UNSLOTTED — no \`flock\` on PATH (not a contention condition)"
    echo "gate: WARNING — no \`flock\` on PATH; running WITHOUT a concurrency slot." >&2
    return 0
  fi
  if ! mkdir -p "$GATE_SLOT_DIR" 2>/dev/null; then
    GATE_SLOT_STATE="UNSLOTTED — cannot create $GATE_SLOT_DIR (not a contention condition)"
    echo "gate: WARNING — cannot create $GATE_SLOT_DIR; running WITHOUT a concurrency slot." >&2
    return 0
  fi
  local waited=0 i announced=0 opened=0 next_heartbeat=60
  while :; do
    opened=0
    for i in $(seq 1 "$GATE_SLOTS"); do
      # 🔴 ONE LITERAL fd, REOPENED PER CANDIDATE, AND NO `eval`. The previous
      # spelling built the redirection with `eval "exec ${fd}>>'$dir/slot-$i'"`,
      # which (a) broke on a slot dir containing a quote and (b) sent that
      # breakage down the same `|| continue` path as a HELD lock — so the gate
      # announced contention for a pool nobody was holding. Reopening fd 201
      # closes whatever it pointed at, so the loop leaks nothing.
      #
      # 🔴 fd 201 IS A LITERAL ON PURPOSE, not bash's `{var}>>` auto-allocation:
      # `{var}` fds carry different close-on-exec behaviour, and this fd MUST be
      # inherited by children so that `run_tier`'s explicit close is the thing
      # that decides whether an orphan can keep the slot. That close is pinned
      # by test_an_orphan_spawned_by_the_tier_does_not_keep_holding_the_slot; an
      # fd bash closed for us would make that test pass for the wrong reason.
      if ! exec 201>>"$GATE_SLOT_DIR/slot-$i.lock"; then
        continue
      fi
      opened=$((opened + 1))
      if flock -n 201; then
        GATE_SLOT_HELD="$i"
        GATE_SLOT_FD="201"
        if [ "$waited" -gt 0 ]; then
          echo "gate: acquired slot $i/$GATE_SLOTS after waiting ${waited}s."
        else
          echo "gate: acquired slot $i/$GATE_SLOTS immediately."
        fi
        return 0
      fi
    done
    # 🔴 NO `2>/dev/null` ON THIS LINE. `exec` with only redirections applies
    # them to the SHELL, permanently — `exec 201>&- 2>/dev/null` silenced every
    # later warning in this function, including the three below, and the run
    # then failed open in total silence. Guard the close on having opened
    # something instead; closing an fd that was never opened is what the
    # suppression was there for.
    [ "$opened" -gt 0 ] && exec 201>&-
    if [ "$opened" -eq 0 ]; then
      # NOT contention: we never got far enough to ask whether anyone held a
      # lock. Saying "busy" here is a wrong diagnosis, not a conservative one.
      GATE_SLOT_STATE="UNSLOTTED — could not OPEN any lock file in $GATE_SLOT_DIR (not a contention condition)"
      echo "gate: WARNING — could not open any lock file under $GATE_SLOT_DIR." >&2
      echo "  No slot is held by anyone; this is a pool problem, not contention." >&2
      echo "  Running WITHOUT a concurrency slot." >&2
      return 0
    fi
    if [ "$waited" -ge "$GATE_SLOT_WAIT" ]; then
      GATE_SLOT_STATE="UNSLOTTED — waited ${waited}s for a free slot and gave up (DEVRC_GATE_SLOT_WAIT=$GATE_SLOT_WAIT); this run CONTENDED"
      echo "gate: WARNING — all $GATE_SLOTS slot(s) still busy after ${waited}s" >&2
      echo "  (DEVRC_GATE_SLOT_WAIT=$GATE_SLOT_WAIT). Running WITHOUT a slot rather than" >&2
      echo "  blocking further — this run will contend, and its duration is not" >&2
      echo "  comparable to a slotted run's." >&2
      return 0
    fi
    if [ "$announced" -eq 0 ]; then
      echo "gate: all $GATE_SLOTS slot(s) busy — queueing (another gate is running on this box)."
      echo "gate: waiting up to ${GATE_SLOT_WAIT}s; the --timeout budget starts AFTER the slot is taken."
      announced=1
    fi
    sleep 5
    waited=$((waited + 5))
    # A queue that prints nothing for ten minutes is indistinguishable from a
    # hang, and the advice "a queued gate is WORKING, do not kill it" is only
    # followable if the run keeps saying so.
    if [ "$waited" -ge "$next_heartbeat" ]; then
      echo "gate: still queueing for a slot (${waited}s of ${GATE_SLOT_WAIT}s; this is not a hang)."
      next_heartbeat=$((next_heartbeat + 60))
    fi
  done
}

acquire_slot

# `timeout` is not universally present (busybox coreutils on this box provide
# it, the nix sandbox provides GNU's). Degrade LOUDLY rather than silently
# dropping the cap: a gate that quietly stopped enforcing its own timeout is the
# reassuring-zero shape all over again.
TIMEOUT_BIN=""
if [ "$TIMEOUT" -gt 0 ]; then
  if command -v timeout >/dev/null 2>&1; then
    TIMEOUT_BIN="timeout"
  else
    echo "gate: WARNING — no \`timeout\` on PATH; running WITHOUT a wall-clock cap." >&2
  fi
fi

GATE_FAIL=0
GATE_UNVOUCHED=0
TIER_LINES=()

# Run one runner. Everything that matters is decided from ($rc, log content).
run_tier() { # $1 = label, $2.. = command
  local label="$1"; shift
  local log="$LOG_DIR/$label.log"
  local rc reason verdict panic

  echo "gate: === $label === (full log: $log)"
  # 🔴 THE SLOT FD IS CLOSED IN THE CHILD, and this is not tidiness. A flock
  # lives on the open file DESCRIPTION, which fork/exec inherits — so a single
  # process that outlives this gate (a daemonised helper, an orphaned server, a
  # runner subprocess that escapes its group) keeps holding the slot after we
  # exit. Nobody would ever see it: the pool would just be permanently one slot
  # smaller, every later run would queue for DEVRC_GATE_SLOT_WAIT and then
  # fail-open, and the limiter would be silently off while still printing that
  # it was on. This repo has been bitten by orphans holding a resource before.
  #
  # The subshell exists ONLY to scope that close, and it ends in `exec` so it is
  # REPLACED by the command — the subshell contributes no exit status of its
  # own. 🔴 Nothing may come between this and `rc=$?`; that is the whole bug
  # this script was written for.
  if [ -n "$TIMEOUT_BIN" ]; then
    # --kill-after: a runner that ignores TERM must not outlive the cap either.
    ( [ -n "$GATE_SLOT_FD" ] && eval "exec ${GATE_SLOT_FD}>&-"
      exec "$TIMEOUT_BIN" --kill-after=30s "$TIMEOUT" "$@" ) >"$log" 2>&1
  else
    ( [ -n "$GATE_SLOT_FD" ] && eval "exec ${GATE_SLOT_FD}>&-"
      exec "$@" ) >"$log" 2>&1
  fi
  # 🔴 NOTHING may run between the command and this line. This is the whole bug
  # in one statement: an `echo`, a `tee`, a `| tail` here and the status below
  # is that command's, not the runner's.
  rc=$?

  # --- read the CONTENT, independently of the status -------------------------
  # Anchored at column 0 so a test fixture's own output (`RESULT: all good`
  # appears in the real suite) cannot be mistaken for the runner's verdict.
  verdict="$(grep -aE '^RESULT: (PASS|FAIL)' "$log" | tail -1 || true)"
  panic="$(grep -ac 'panic: test timed out' "$log" || true)"
  : "${panic:=0}"

  reason=""
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    reason="timeout after ${TIMEOUT}s"
  fi

  # --- cross-check: status vs content ----------------------------------------
  local disagree=""
  if [ "$panic" -gt 0 ]; then
    disagree="found $panic 'panic: test timed out' line(s) — the run was TRUNCATED, so every count below it is missing"
  elif [ -z "$verdict" ]; then
    if [ "$rc" -eq 0 ]; then
      disagree="exited 0 but printed NO 'RESULT:' verdict line — the runner did not reach its own summary"
    fi
  elif [ "$rc" -eq 0 ] && [ "${verdict#RESULT: FAIL}" != "$verdict" ]; then
    disagree="exited 0 while printing '$verdict'"
  elif [ "$rc" -ne 0 ] && [ "${verdict#RESULT: PASS}" != "$verdict" ]; then
    disagree="exited $rc while printing '$verdict'"
  fi

  # --- bounded summary to stdout; the full log stays on disk -----------------
  local start
  start="$(grep -an '^=\{8,\} .*SUMMARY' "$log" | tail -1 | cut -d: -f1 || true)"
  if [ -n "$start" ]; then
    sed -n "${start},\$p" "$log"
  else
    echo "gate: (no SUMMARY block in $label's log — showing its last 25 lines)"
    tail -25 "$log"
  fi

  if [ -n "$disagree" ]; then
    echo "gate: FATAL — $label $disagree." >&2
    echo "  The exit status and the printed verdict do not agree, so this gate" >&2
    echo "  cannot vouch for either. That is NOT the same finding as 'the tests" >&2
    echo "  failed' — read $log before believing any number in it." >&2
    GATE_UNVOUCHED=1
    TIER_LINES+=("UNVOUCHED  $label  exit=$rc  verdict='${verdict:-<none>}'")
  elif [ "$rc" -ne 0 ]; then
    GATE_FAIL=1
    TIER_LINES+=("FAIL  $label  exit=$rc${reason:+  ($reason)}  verdict='${verdict:-<none>}'")
  else
    TIER_LINES+=("PASS  $label  exit=0  verdict='$verdict'")
  fi
  echo
}

if [ "$TIER" = "pytest" ] || [ "$TIER" = "both" ]; then
  run_tier pytest bash "$PYTEST_RUNNER" --set "$SET" "$ROOT"
fi
if [ "$TIER" = "node" ] || [ "$TIER" = "both" ]; then
  run_tier node bash "$NODE_RUNNER" "$ROOT"
fi

echo "======================== GATE ========================"
for l in "${TIER_LINES[@]}"; do echo "  $l"; done
echo "  logs: $LOG_DIR"
# State the concurrency conditions the run actually got, because every duration
# in this log is a claim about them. "Slow" and "contended" are different
# findings and the log has to be able to tell them apart AFTER the fact.
if [ -n "$GATE_SLOT_HELD" ]; then
  echo "  slot: $GATE_SLOT_HELD of $GATE_SLOTS (concurrency was bounded)"
else
  # Name the STATE, never just the absence: only "waited and gave up" is a claim
  # about contention, and the other four read identically without this line.
  echo "  slot: NONE HELD — ${GATE_SLOT_STATE:-UNSLOTTED — reason not recorded}"
fi

# Precedence: "could not vouch" outranks "failed", which outranks "passed". A
# gate that cannot trust its own instrument must never report a plain FAIL, let
# alone a PASS.
if [ "$GATE_UNVOUCHED" -ne 0 ]; then
  echo "GATE: RESULT=UNVOUCHED exit=90"
  exit 90
fi
if [ "$GATE_FAIL" -ne 0 ]; then
  echo "GATE: RESULT=FAIL exit=1"
  exit 1
fi
echo "GATE: RESULT=PASS exit=0"
exit 0
