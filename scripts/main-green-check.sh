#!/usr/bin/env bash
#
# main-green-check — PASSIVE deadman answering one question, unattended:
#
#     "is the CURRENT tip of origin/main actually green?"
#
# It REPORTS. It never fixes, never pushes, never reverts.
#
# ── WHY THIS EXISTS ───────────────────────────────────────────────────────────
# Branch protection on this repo is OFF by a live operator decision (see
# devrc/CLAUDE.md), so a commit can land straight on `main` with nothing running
# against it. That is not hypothetical and the rate is MEASURED — twice in one
# session, 2026-09-03:
#
#   a720d30d  "espanso", one line, no PR. Changed `:acq`'s label so
#             `recom`/`recommend` matched two snippets and attribution returned
#             None. `main` red on BOTH tiers.
#   a451abc0  "espanso", direct to main, no PR. SWAPPED `:acq`'s `label` and
#             `replace`, which removed the collision a guard asserted existed and
#             red-ed `main` a second time.
#
# 🔴 BOTH TIMES THE ONLY DETECTOR WAS A HUMAN RUNNING THE GATE BY HAND, HOURS
# LATER, INCIDENTALLY. That is the gap this closes. `main` being red is not the
# interesting part — `main` being red *and nobody knowing* is.
#
# ── WHY IT RUNS THE REAL SUITE ────────────────────────────────────────────────
# The second break was a GUARD WHOSE PREMISE DIED, not a regression: the thing it
# asserted was simply gone at the source. A distilled check ("did attribution
# regress?") would have sailed past it. Only running what the gate actually runs
# catches both shapes, so that is what this does.
#
# ── WHY IT RUNS THE NIX SANDBOX TIER, NOT THE DEV-HOST TIER ───────────────────
# 🔴 MEASURED 2026-09-03 on ONE tree (main + #1261), which is the whole argument:
#
#   dev-host tier (scripts/gate.sh)   4 failed — 2 in scripts/tests, 2 in
#                                     scripts/browser-bridge/tests
#   nix sandbox tier (SAME tree)      collected=21013 passed=21010 failed=0
#
# All four dev-host failures were `subprocess.TimeoutExpired` at a fixed 300 s
# cap with returncode -9 — never an assertion — while the box sat at load 22-29
# with other sessions' `opencode`/`npm` processes on it. An untouched target
# (`scripts/collector/tests`, 273 tests) went 4.49 s -> 177.26 s in the same
# window: a 39x inflation, which is contention's signature and not a defect's.
#
# A deadman wired to the tier that emits those would toast on them, and
# `claude/RULES.md` is explicit that a permanently-red gate is WORSE than no gate
# because it trains everyone to click through. So this uses the hermetic tier.
# ⚠ HONESTLY SCOPED, AND NOW MEASURED THE OTHER WAY TOO: the sandbox runs on the
# same box and is NOT immune to load. On 2026-09-03 at load 48 a sandbox run of
# this very branch failed `test_live_cotenants_does_not_count_this_process` — a
# test in a family already recorded as flaky, in a subsystem this branch does not
# touch. The discriminator was wall time, per claude/RULES.md: FIVE untouched
# targets inflated 11x-20x against the previous passing run of the same tree
# (task-spec-drafter 4.4s->89.6s, validation 4.6s->81.3s, repo-cos 2.4s->39.6s),
# while the target that actually failed moved only 1.2x. Load inflates EVERY
# target; a failed assertion inflates exactly one.
# 🔴 SO THE RETRY BELOW IS LOAD-BEARING, NOT BELT-AND-BRACES. The hermetic tier
# is much better than the dev-host one here, not perfect, and a deadman wired to
# a single hermetic run WOULD have toasted on that. One retry plus a FLAKE
# verdict that is reported rather than hidden is what makes it usable.
#
# ── HOW IT AVOIDS BEING EXPENSIVE ─────────────────────────────────────────────
# MEMOIZED ON THE SHA. A verdict is recorded against the exact commit it was
# computed for; if origin/main has not moved since, this exits immediately having
# run nothing. `main` moved 5 times during one session, so this is ~1-2 real runs
# a day rather than one per timer fire.
#
# ── PASSIVE MEANS PASSIVE ─────────────────────────────────────────────────────
# 🔴 This script must NEVER touch the operator's checkout. It maintains its OWN
# clone under $MAIN_GREEN_CACHE and does every fetch/checkout/build there. The
# only thing it reads from the invoking repo is `remote get-url origin`, so it
# asks the same remote the operator pushes to. `scripts/tests/test_main_green_
# check.py` enforces that statically: every `git -C` in this file must target the
# cache clone, never $DEVRC.
#
# 🔴 It also never creates a worktree in the operator's clone. `git worktree add`
# writes to the COMMON git dir (refs, config, worktree registry) and is therefore
# a repo-GLOBAL mutation of a checkout other sessions are using — see
# claude/RULES.md "Git Workflow". A private clone has none of that reach.
#
# ── EXIT CODES ────────────────────────────────────────────────────────────────
#    0  GREEN — origin/main's tip passed, or was already verified, or a red did
#       not reproduce (FLAKE). Every 0 prints WHICH of those it is; they are
#       different facts and are never collapsed.
#   10  RED, REPRODUCED — ran twice, failed twice. This is the toast.
#   11  COULD NOT MEASURE — no network, no nix, clone unusable, gate produced no
#       verdict. 🔴 NOT a pass and NOT a red. A deadman that reports "clean"
#       when it could not look is the failure mode this whole file exists to
#       avoid, so this is its own code and it is never folded into 0.
#   12  BLIND — could not measure N times CONSECUTIVELY (MAIN_GREEN_BLIND_
#       ESCALATE, default 6 ≈ 24h at a 4h timer).
#       🔴 WHY THIS EXISTS AS A SEPARATE CODE. Making 11 a systemd success stops
#       a transient network blip from toasting — and, on its own, lets this
#       deadman go blind FOREVER in silence, which is the same shape as the bug
#       it was built to catch. drift-check learned this the hard way (its rc 18:
#       "setting no code was right per run and wrong forever"), so the ladder is
#       copied rather than reinvented. A single measured run resets the streak.
#    2  usage / precondition problem in this script.
#
# ── OPTIONS ───────────────────────────────────────────────────────────────────
#   --force    ignore the memo and re-run even when the tip has not moved.
#   --status   print the last recorded verdict and exit. Does not take the lock,
#              so it works while a run is in flight.
#   -h/--help  this header.
#
# TEST SEAM: MAIN_GREEN_GATE_CMD overrides the gate invocation (it is passed the
# checkout path and the tier name). MAIN_GREEN_CACHE overrides the cache root.
# MAIN_GREEN_REMOTE overrides which remote is cloned. All three exist so the
# tests can drive every arm hermetically without a 20-minute build; none is read
# anywhere else, and `scripts/tests/test_main_green_check.py` pins that the
# PRODUCTION path (no seam set) still resolves the operator's own origin.
set -uo pipefail

RC_GREEN=0
RC_RED=10
RC_UNMEASURED=11
RC_BLIND=12
RC_USAGE=2

BLIND_ESCALATE="${MAIN_GREEN_BLIND_ESCALATE:-6}"
# 🔴 CONTENTION IS ALSO A NON-MEASUREMENT, and round 1 left this door open while
# its own comment condemned the whole class. A lock held by a hand-run that hung
# makes every fire print "another run holds the lock" and exit 0 — silent, a
# systemd success, touching no ladder — so the deadman can be blind indefinitely
# with nothing saying so. Lower than BLIND_ESCALATE because a legitimately
# overlapping run resolves within one or two intervals.
CONTENTION_ESCALATE="${MAIN_GREEN_CONTENTION_ESCALATE:-3}"

CACHE_ROOT="${MAIN_GREEN_CACHE:-$HOME/.cache/main-green}"
CLONE="$CACHE_ROOT/repo"
STATE="$CACHE_ROOT/state"
LOCK="$CACHE_ROOT/lock"
LOGDIR="$CACHE_ROOT/logs"
CONTENTION_FILE="$CACHE_ROOT/contention-streak"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_REPO="$(dirname "$SCRIPT_DIR")"

say() { printf 'main-green: %s\n' "$*"; }
die() { say "$*"; exit "$RC_USAGE"; }

# ── the blind ladder ──────────────────────────────────────────────────────────
# Kept in its OWN file, deliberately: an unmeasured run must not overwrite the
# last real verdict. "I could not look today" and "main was red when I last
# looked" are different facts and the second must survive the first.
STREAK_FILE="$CACHE_ROOT/blind-streak"
# 🔴 SANITISED, AND THE BLAST RADIUS OF NOT DOING SO WAS NOT THE LADDER. An
# unvalidated value reaches `$(( ... + 1 ))`; a non-integer makes that a bash
# ARITHMETIC SYNTAX ERROR, which aborts `unmeasured_exit` WITHOUT exiting — and
# every call site is inside an `if ... fi`, so the script CONTINUES past a guard
# that just fired. Measured with a streak file of `1 2`: a failed clone was
# announced and then ignored, the run gated an EMPTY sha, and it exited 10
# reporting `RED, REPRODUCED — origin/main  failed BOTH attempts` with a blank
# author. A corrupt counter turned a total failure to fetch into a DND-defeating
# accusation about `main`.
read_count() {
  local f="$1" s=0
  [ -f "$f" ] && s="$(cat "$f" 2>/dev/null || echo 0)"
  # 🔴 DO NOT STRIP INTERNAL WHITESPACE. An earlier draft did, and it turned the
  # corrupt value `1 2` into a perfectly plausible `12` — making a garbage
  # counter look like a real streak. `$(cat ...)` already drops trailing
  # newlines; anything else containing a space is corrupt and reads as 0.
  case "$s" in ''|*[!0-9]*) s=0 ;; esac
  # 🔴 A DIGITS-ONLY CHECK IS NARROWER THAN THE HAZARD. `08` and `09` ARE all
  # digits and sail through the case above — and bash reads a leading zero as
  # OCTAL, so `$(( 08 + 1 ))` is `value too great for base`: the SAME arithmetic
  # abort the sanitiser exists to stop. MEASURED at 31864127 with a streak of
  # `08`: the clone failed, the fetch failed, the sha was EMPTY, and the run
  # printed `✅ GREEN — origin/main  passed both sandbox tiers` and exited 0.
  # A deadman reporting a green it never measured is worse than no deadman.
  # A length cap first, because a huge literal overflows the same arithmetic.
  [ "${#s}" -gt 6 ] && s=0
  s=$(( 10#$s ))          # force base ten; 10#08 == 8
  echo "$s"
}
read_streak() { read_count "$STREAK_FILE"; }
reset_streak() { echo 0 >"$STREAK_FILE" 2>/dev/null || true; }
unmeasured_exit() {
  local n
  n=$(( $(read_streak) + 1 ))
  echo "$n" >"$STREAK_FILE" 2>/dev/null || true
  say "  consecutive could-not-measure runs: $n (escalates at $BLIND_ESCALATE)"
  if [ "$n" -ge "$BLIND_ESCALATE" ]; then
    say "🔴 BLIND — $n consecutive runs could not measure anything."
    say "  This deadman has been unable to look for that long, so its silence has"
    say "  meant NOTHING over that whole window. Escalated on purpose: a scope"
    say "  that can never be evaluated, and never escalates, reads as clean forever."
    exit "$RC_BLIND"
  fi
  exit "$RC_UNMEASURED"
}


FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --force)  FORCE=1 ;;   # ignore the memo and re-run even on an unchanged sha
    --status) STATUS_ONLY=1 ;;
    # 🔴 COMPUTED FROM WHERE THE HEADER ENDS, not a literal. A hardcoded range
    # silently truncates --help the moment the header grows past it — which had
    # already happened once (`2,80p`), and "bump the constant" re-rots on the
    # next edit. `set -uo pipefail` is the first line after the header.
    -h|--help)
      _hdr=$(grep -n '^set -uo pipefail' "${BASH_SOURCE[0]}" | head -1 | cut -d: -f1)
      # 🔴 A ZERO THAT MEANS NOTHING IS THE FAILURE THIS FILE ARGUES AGAINST, and
      # the computed range introduced one. Reword the sentinel and `grep` matches
      # nothing, `_hdr` is empty, `$(( _hdr - 1 ))` is -1, and `sed -n "2,-1p"`
      # errors to stderr while --help prints ZERO lines and exits 0. MEASURED.
      # The literal it replaced merely TRUNCATED the help; this deleted it and
      # reported success. Refuse instead.
      [ -n "$_hdr" ] || die "cannot locate the end of the header (the 'set -uo pipefail' sentinel moved)"
      sed -n "2,$(( _hdr - 1 ))p" "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
STATUS_ONLY="${STATUS_ONLY:-0}"

mkdir -p "$CACHE_ROOT" "$LOGDIR" || die "cannot create $CACHE_ROOT"

# ── single-flight ─────────────────────────────────────────────────────────────
# A run can take 20 minutes; the timer fires more often than that. Overlapping
# runs would fight over the clone and over the state file, and two nix builds of
# the same derivation contend in the store (devrc/CLAUDE.md records that a
# CONCURRENT pair produced two FALSE failures that a sequential pair did not).
# 🔴 BEFORE THE LOCK, DELIBERATELY. `--status` only READS the recorded
# verdict, and the moment you most want it is while a 20-minute run is in
# flight — which is precisely when taking the lock first would block it.
if [ "$STATUS_ONLY" = "1" ]; then
  if [ -f "$STATE" ]; then say "last verdict:"; sed 's/^/  /' "$STATE"
  else say "no verdict recorded yet"; fi
  exit "$RC_GREEN"
fi

exec 9>"$LOCK" || die "cannot open $LOCK"
if ! flock -n 9; then
  # 🔴 A FAILED `flock` IS NOT PROOF OF CONTENTION — VALIDATE THE INSTRUMENT.
  # `flock -n` returns non-zero for ENOLCK (a filesystem without working locks:
  # NFS without lockd, some overlay/9p mounts), a missing binary and EBADF, and
  # none of those is another run holding the lock. This arm used to answer
  # `exit 0` GREEN for all of them: silent, permanent, a systemd success, and it
  # never touches the blind ladder — the "goes blind in silence" shape the
  # ladder exists to close, reached through a different door.
  #
  # POSITIVE CONTROL: take a lock NOBODY holds. If that works, `flock` works
  # here and the failure above really was contention. If it does not, we have
  # measured nothing and must say so.
  _probe="$CACHE_ROOT/.flock-probe.$$"
  if ( exec 8>"$_probe" && flock -n 8 ) 2>/dev/null; then
    rm -f "$_probe"
    _cn=$(( $(read_count "$CONTENTION_FILE") + 1 ))
    echo "$_cn" >"$CONTENTION_FILE" 2>/dev/null || true
    if [ "$_cn" -ge "$CONTENTION_ESCALATE" ]; then
      say "🔴 $_cn consecutive runs skipped for CONTENTION — a lock held that long"
      say "  has made this deadman blind, and exiting 0 would keep it silent."
      say "  Look for a hand-run or a wedged run holding $LOCK."
      unmeasured_exit
    fi
    say "another run holds the lock — nothing to do" \
        "($_cn consecutive; escalates at $CONTENTION_ESCALATE)"
    exit "$RC_GREEN"
  fi
  rm -f "$_probe"
  say "COULD NOT MEASURE — flock failed on a lock NOBODY holds, so 'another run"
  say "  holds the lock' cannot be distinguished from a broken lock. Reporting"
  say "  GREEN here would be a silent, permanent, systemd-success pass."
  unmeasured_exit
fi

rm -f "$CONTENTION_FILE" 2>/dev/null || true   # we hold the lock: not contended

# ── where do we look? the remote the OPERATOR pushes to, read from their repo ──
# A read. Never a write. If this script is run from somewhere without a git
# remote we cannot know what to check, and that is UNMEASURED, not clean.
REMOTE_URL="${MAIN_GREEN_REMOTE:-$(git -C "$SRC_REPO" remote get-url origin 2>/dev/null || true)}"
if [ -z "$REMOTE_URL" ]; then
  say "COULD NOT MEASURE — no 'origin' remote on $SRC_REPO, so there is no"
  say "  branch to check. This is not a clean bill of health."
  unmeasured_exit
fi

read_state() { [ -f "$STATE" ] && cat "$STATE" || echo ""; }
state_field() { read_state | awk -v k="$1" '$1==k {print $2; exit}'; }


# ── keep the private clone current ────────────────────────────────────────────
if [ ! -d "$CLONE/.git" ]; then
  say "first run — cloning $REMOTE_URL into $CLONE (this is NOT your checkout)"
  if ! git clone --quiet "$REMOTE_URL" "$CLONE" 2>"$LOGDIR/clone.err"; then
    say "COULD NOT MEASURE — clone failed:"
    sed 's/^/  /' "$LOGDIR/clone.err" | head -5
    unmeasured_exit
  fi
fi

if ! git -C "$CLONE" fetch --quiet origin 2>"$LOGDIR/fetch.err"; then
  say "COULD NOT MEASURE — fetch failed (network? credentials?):"
  sed 's/^/  /' "$LOGDIR/fetch.err" | head -5
  say "  🔴 This is NOT 'main is green'. Nothing was checked."
  unmeasured_exit
fi

# The mainline is DERIVED, never hardcoded: this script is meant to be copyable
# to a repo whose mainline is `trunk`, and a hardcoded `main` would silently
# check a branch that does not exist and report a reassuring nothing.
MAINLINE="$(git -C "$CLONE" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
MAINLINE="${MAINLINE:-main}"
SHA="$(git -C "$CLONE" rev-parse "origin/$MAINLINE" 2>/dev/null || true)"
if [ -z "$SHA" ]; then
  say "COULD NOT MEASURE — cannot resolve origin/$MAINLINE in $CLONE"
  unmeasured_exit
fi
SUBJECT="$(git -C "$CLONE" log -1 --format=%s "$SHA" 2>/dev/null | cut -c1-72)"

# ── the memo ──────────────────────────────────────────────────────────────────
LAST_SHA="$(state_field sha)"
LAST_VERDICT="$(state_field verdict)"
if [ "$FORCE" != "1" ] && [ -n "$LAST_SHA" ] && [ "$LAST_SHA" = "$SHA" ]; then
  case "$LAST_VERDICT" in
    green|flake)
      say "GREEN (already verified) — origin/$MAINLINE is still ${SHA:0:8}, verdict=$LAST_VERDICT"
      say "  nothing was re-run; --force overrides"
      reset_streak; exit "$RC_GREEN" ;;
    red)
      say "🔴 RED (already measured, unchanged) — origin/$MAINLINE ${SHA:0:8} was red and"
      say "  has not moved since. Nobody has fixed it."
      say "  subject: $SUBJECT"
      reset_streak; exit "$RC_RED" ;;
  esac
fi

# ── check out the tip, in OUR clone ───────────────────────────────────────────
if ! git -C "$CLONE" checkout --quiet --detach "$SHA" 2>"$LOGDIR/checkout.err"; then
  say "COULD NOT MEASURE — cannot check out $SHA in the cache clone:"
  sed 's/^/  /' "$LOGDIR/checkout.err" | head -5
  unmeasured_exit
fi

# ── run one tier, hermetically ────────────────────────────────────────────────
# 🔴 ONE AT A TIME, never in a single `nix build` invocation. devrc/CLAUDE.md
# records the measurement: a COMBINED build of both derivations reported 2
# failures (`SQLite database is busy`, `database is locked`) that the SAME tree
# built SEQUENTIALLY did not. A combined GREEN is trustworthy; a combined RED is
# not. So we never create that ambiguity in the first place.
run_tier() {
  local tier="$1" attempt="$2" log
  log="$LOGDIR/${tier}.attempt${attempt}.log"
  if [ -n "${MAIN_GREEN_GATE_CMD:-}" ]; then
    "$MAIN_GREEN_GATE_CMD" "$CLONE" "$tier" >"$log" 2>&1
    return $?
  fi
  # 🔴 THE FLAG LIVES HERE, NOT IN THE UNIT'S `Environment=`. The first draft
  # passed it as `NIX_CONFIG=experimental-features = nix-command flakes`, and
  # systemd WHITESPACE-SPLITS `Environment=` — a hazard nix/home.nix already
  # documents ~1100 lines above that edit. Measured with `systemd-analyze
  # verify`: three "Invalid environment assignment, ignoring:" lines, leaving
  # NIX_CONFIG as the bare word `experimental-features`, which makes nix itself
  # hard-error `syntax error in configuration line` on EVERY invocation — worse
  # than omitting it, since this host's /etc/nix/nix.conf already enables both.
  # On the command line there is nothing for a unit file to re-parse, and the
  # script works identically by hand, from systemd, or from cron.
  # 🔴 `--no-warn-dirty` IS LOAD-BEARING FOR THE CACHED ARM, not cosmetic. A
  # dirty tree makes nix emit `warning: Git tree '...' is dirty` — measured at
  # 141 bytes — which makes a CACHED build's output NON-empty, so it reads as a
  # truncated run (UNMEASURED) and ladders to BLIND about a green `main`. The
  # detached clone should never be dirty, and `--no-link` is what keeps it that
  # way (a `result` symlink would dirty it); this makes the inference hold even
  # if something does dirty it.
  nix --extra-experimental-features "nix-command flakes" \
      build "$CLONE#checks.x86_64-linux.$tier" -L --no-link --no-warn-dirty >"$log" 2>&1
  return $?
}

# The verdict is read from the runner's own RESULT: line where there is one, not
# from the exit status alone — devrc/CLAUDE.md records four agents reporting
# `exit 0` over content saying `RESULT: FAIL`. Both must agree, and a build that
# produced NO verdict at all is UNMEASURED rather than either answer.
tier_verdict() {
  local log="$1" rc="$2"
  if grep -q "RESULT: FAIL" "$log" 2>/dev/null; then echo red; return; fi
  if grep -q "RESULT: PASS" "$log" 2>/dev/null; then
    [ "$rc" -eq 0 ] && echo green || echo disagree
    return
  fi

  # ── no `RESULT:` line at all ────────────────────────────────────────────────
  # 🔴 A NON-ZERO EXIT WITH NO VERDICT IS A BROKEN GATE, NOT A BROKEN `main`.
  # This arm used to return `red`, and that is the worst answer this file can
  # give: the red path names the commit's AUTHOR and fires a do-not-disturb-
  # defeating toast saying "main is broken RIGHT NOW", with an EMPTY "Failing
  # tests:" list. Measured — a `nix` that cannot start (see the NIX_CONFIG
  # incident in nix/home.nix) exits 1 silently and produced exactly that
  # accusation against an innocent commit, every 4h, memoized so it repeated.
  # A genuinely failing suite cannot reach here: it exits non-zero AND prints
  # `RESULT: FAIL`, which the first arm catches.
  if [ "$rc" -ne 0 ]; then echo noverdict; return; fi

  # ── rc 0 and no verdict — TWO DIFFERENT FACTS, and they must not be merged ──
  # 🔴 EMPTY output is the CACHED case, and it is GREEN. `-L` streams a log only
  # while a build RUNS; an already-realised derivation is not rebuilt, so nix
  # prints nothing and exits 0. That is not an absence of evidence — a check
  # derivation RUNS the suite, so nix exiting 0 means it built, which means the
  # tests passed. Treating it as "no verdict" made the MODAL path report COULD
  # NOT MEASURE: devrc/CLAUDE.md tells every merger to build these same two
  # derivations on the merged tree before merging, so the tip this deadman
  # checks is usually already realised — six such runs ladder to a BLIND toast
  # about a perfectly green `main`, and `--force` was broken by construction.
  #
  # NON-EMPTY output with no verdict is a TRUNCATED run and stays unmeasured.
  [ -s "$log" ] && echo noverdict || echo cached
}

# 🔴 SETS A GLOBAL; it does NOT echo its verdict.
# The obvious shape here is `v=$(attempt_all_tiers 1)`, and it is wrong: command
# substitution captures stdout, so every per-tier progress line — including the
# DISAGREE and no-verdict warnings, the two most important things this script can
# say — would be swallowed and thrown away by the caller. The operator would see
# a bare verdict with no account of how it was reached. Caught by
# `test_status_and_content_DISAGREEING_...` asserting the warning reaches stdout.
ATTEMPT_VERDICT=""
attempt_all_tiers() {
  local attempt="$1" tier rc v overall=green
  for tier in pytests nodetests; do
    run_tier "$tier" "$attempt"; rc=$?
    v="$(tier_verdict "$LOGDIR/${tier}.attempt${attempt}.log" "$rc")"
    say "  attempt $attempt · $tier · rc=$rc · verdict=$v"
    # 🔴 EVERY DOWNGRADE-TO-UNMEASURED IS GUARDED BY `overall = green`, AND THE
    # MISSING GUARD ON `disagree` MADE THE VERDICT ORDER-DEPENDENT. Measured:
    # pytests `RESULT: FAIL` + nodetests status/content disagreement resolved to
    # rc 11 — a systemd SUCCESS, no toast — while the same two tiers in the
    # other order resolved to rc 10. A genuinely red `main` was reported as
    # nothing at all, which is the one outcome this whole file exists to
    # prevent. `red` outranks `unmeasured`: not knowing about ONE tier cannot
    # unlearn what another tier positively measured.
    case "$v" in
      red)       overall=red ;;
      cached)    say "  ↳ $tier was already realised — nix rebuilt nothing, which for a check derivation IS a pass" ;;
      disagree)  say "  🔴 status/content DISAGREE on $tier — not resolving that in favour of the reassuring side"
                 [ "$overall" = green ] && overall=unmeasured ;;
      noverdict) say "  🔴 $tier printed no RESULT: line — a truncated or failed-to-start run is not a pass"
                 [ "$overall" = green ] && overall=unmeasured ;;
    esac
  done
  ATTEMPT_VERDICT="$overall"
}

say "checking origin/$MAINLINE ${SHA:0:8} — $SUBJECT"
attempt_all_tiers 1
FIRST="$ATTEMPT_VERDICT"

VERDICT="$FIRST"
if [ "$FIRST" = "red" ]; then
  # 🔴 THE RETRY IS NOT "run until green". It runs EXACTLY ONCE more, and a red
  # that clears is recorded as a FLAKE rather than as a pass — the distinction is
  # the whole point, because a flake that is silently swallowed is how a real
  # break gets absorbed into the noise.
  say "🔴 red on attempt 1 — re-running ONCE to separate a real break from a load flake"
  attempt_all_tiers 2
  SECOND="$ATTEMPT_VERDICT"
  if [ "$SECOND" = "green" ]; then
    VERDICT=flake
  elif [ "$SECOND" = "red" ]; then
    VERDICT=red
  else
    VERDICT=unmeasured
  fi
fi

# ── record, then report ───────────────────────────────────────────────────────
{
  echo "sha $SHA"
  echo "verdict $VERDICT"
  echo "mainline $MAINLINE"
  echo "when $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$STATE.tmp" && mv "$STATE.tmp" "$STATE"

# A run that reached a verdict — ANY verdict, red included — is a run that could
# see. Only `unmeasured` leaves the streak standing.
[ "$VERDICT" = "unmeasured" ] || reset_streak

case "$VERDICT" in
  green)
    say "✅ GREEN — origin/$MAINLINE ${SHA:0:8} passed both sandbox tiers"
    exit "$RC_GREEN" ;;
  flake)
    say "✅ GREEN (FLAKE ABSORBED) — ${SHA:0:8} failed attempt 1 and passed attempt 2."
    say "  Not toasted, and NOT hidden: logs in $LOGDIR. If this recurs on the same"
    say "  test, it is a timing dependency to fix, not a flake to re-run."
    exit "$RC_GREEN" ;;
  red)
    say "🔴 RED, REPRODUCED — origin/$MAINLINE ${SHA:0:8} failed BOTH attempts."
    say "  subject: $SUBJECT"
    say "  author:  $(git -C "$CLONE" log -1 --format='%an <%ae>' "$SHA" 2>/dev/null)"
    say "  landed:  $(git -C "$CLONE" log -1 --format=%cI "$SHA" 2>/dev/null)"
    say "  🔴 main is broken RIGHT NOW and branch protection is off, so nothing"
    say "     else is going to tell you. Failing tests:"
    sed 's/\x1b\[[0-9;]*m//g' "$LOGDIR"/*.attempt2.log 2>/dev/null \
      | grep -E "^\s+FAIL\s+|^FAILED" | sed 's/^/    /' | head -12
    say "  full logs: $LOGDIR"
    exit "$RC_RED" ;;
  *)
    say "COULD NOT MEASURE — the gate did not return a usable verdict for ${SHA:0:8}."
    say "  🔴 This is NOT 'main is green'. Read $LOGDIR before believing anything."
    unmeasured_exit ;;
esac
