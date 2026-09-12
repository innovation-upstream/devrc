#!/usr/bin/env bash
# ledger-check.sh — the FAST pre-merge screen for the one class of failure that
# lands on `main` and reddens it for everybody: a repo-census guard broken by a
# file arriving without its ledger row.
#
# WHY THIS EXISTS
# ---------------
# `main` went RED TWICE in one session, hours each, from that single shape:
#
#   * `_KILL_MENTION_LEDGER`  (scripts/claude-hooks/tests/test_guard_core.py)
#   * `_OWN_BOUND_LEDGER`     (scripts/tests/test_runner_bound_ledger.py)
#
# Neither author could have known, and neither existing surface could have told
# them. `scoped-tests.sh` maps a diff to the tests that NAME what you changed,
# and a brand-new file names nothing. The full pytest tier would have caught
# both — and its median is ~20 minutes, so nobody runs it before merging.
#
# 🔴 WHAT IT RUNS IS DERIVED, NOT LISTED. `scripts/testlib/census_scan.py`
# computes, from the AST, every test whose verdict depends on the repo's FILE
# SET — because a hand-kept list of "the ledger tests" is itself a ledger and
# would go stale exactly the way the thing it is guarding did. Read that
# module's header for the derivation, what it deliberately over-includes, and
# the four things it structurally cannot see.
#
# 🔴 THIS IS NOT A GATE, AND IT MUST NOT BE QUOTED AS ONE. It prints
# `SCOPE: LEDGERS`. It runs one narrow class and says NOTHING about the rest of
# the suite; `scripts/gate.sh --tier both` is the only thing that does. A pass
# here means "no repo-census guard is red on this tree", full stop.
#
# 🔴 IT SEES THE TRACKED SET. The census guards go through
# `testlib.public_ip_scan.repo_files`, which prefers `git ls-files` — so a NEW
# FILE YOU HAVE NOT `git add`ED IS INVISIBLE to most of them, and this check
# will pass on a tree that goes red the moment you commit. `git add` first.
# (CLAUDE.md already requires that for a different reason: an un-added file is
# silently omitted from the flake.)
#
# EXIT CODES
#   0  every derived census test passed
#   1  at least one failed — read the named test
#   2  usage / the tree is not this repo
#   3  COULD NOT MEASURE — the derivation returned implausibly little, or
#      pytest produced no parseable verdict. NOT a pass.
set -uo pipefail

# 🔴 `>/dev/null` on the `cd`: with CDPATH set in the operator's environment,
# `cd` ECHOES the directory it landed in, so ROOT became "<path>\n<path>" and
# every path built from it missed. It fails loudly here; in a script that only
# reads files it would fail as "not found".
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null && pwd)"

# 🔴 A FLOOR, NOT A HOPE. The derivation walking into a refactor that moves
# every census behind an unresolvable indirection would return a short list,
# and a short list passes in seconds while checking nothing — the "reassuring
# zero from a harness wired to nothing" this repo's rules name. Measured at
# `10ae4da0`: 240 nodeids. The floor is deliberately far below that so ordinary
# churn never trips it, and far above zero so a broken derivation cannot pass.
MIN_NODEIDS="${DEVRC_LEDGER_MIN_NODEIDS:-60}"

JOBS="${DEVRC_LEDGER_JOBS:-12}"
LIST_ONLY=0
EXTRA=()

usage() {
  sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  echo
  echo "usage: scripts/ledger-check.sh [--list] [-j N] [-- <extra pytest args>]"
  echo "  --list         print the derived nodeids and exit 0 WITHOUT running them"
  echo "                 (a listing is not a verdict)"
  echo "  -j N           xdist workers (default ${JOBS}; 0 disables xdist)"
  echo "env: DEVRC_LEDGER_MIN_NODEIDS (floor, default 60), DEVRC_LEDGER_JOBS"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST_ONLY=1; shift ;;
    -j) JOBS="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; EXTRA=("$@"); break ;;
    *) echo "ledger-check: unknown argument '$1'" >&2; usage >&2; exit 2 ;;
  esac
done

if [ ! -f "$ROOT/scripts/testlib/census_scan.py" ]; then
  echo "ledger-check: FATAL — $ROOT does not look like this repo." >&2
  exit 2
fi

# --- re-exec into the dev shell if pytest is not importable --------------------
# Same reason `gate.sh` does it: `.envrc` is `use opencode`, which carries no
# pytest, so the common case is an operator running this from a direnv shell and
# getting `No module named pytest` — which reads as "the suite is unrunnable"
# rather than "you are in the wrong shell".
if ! python3 -m pytest --version >/dev/null 2>&1; then
  if [ "${DEVRC_LEDGER_NO_REEXEC:-0}" = "1" ]; then
    echo "ledger-check: FATAL — no pytest on this interpreter and re-exec is off." >&2
    echo "  Run:  nix develop $ROOT -c bash scripts/ledger-check.sh" >&2
    exit 2
  fi
  if ! command -v nix >/dev/null 2>&1; then
    echo "ledger-check: FATAL — no pytest, and no \`nix\` to re-exec into." >&2
    exit 2
  fi
  echo "ledger-check: no pytest here — re-execing into the repo dev shell."
  export DEVRC_LEDGER_NO_REEXEC=1
  exec nix develop "$ROOT" -c bash "${BASH_SOURCE[0]}" "$@"
fi

# --- derive --------------------------------------------------------------------
T0=$(date +%s%N)
NODEID_FILE="$(mktemp -t ledger-check-nodeids.XXXXXX)"
STATS_FILE="$(mktemp -t ledger-check-stats.XXXXXX)"
cleanup() { rm -f "$NODEID_FILE" "$STATS_FILE"; }
trap cleanup EXIT

if ! python3 "$ROOT/scripts/testlib/census_scan.py" "$ROOT" >"$NODEID_FILE" 2>/dev/null; then
  echo "ledger-check: COULD NOT MEASURE — the derivation itself failed." >&2
  echo "RESULT: COULD-NOT-MEASURE (exit=3)"
  exit 3
fi
python3 "$ROOT/scripts/testlib/census_scan.py" "$ROOT" --stats >"$STATS_FILE" 2>/dev/null || true
T1=$(date +%s%N)

NODEIDS=()
while IFS= read -r _l; do [ -n "$_l" ] && NODEIDS+=("$_l"); done <"$NODEID_FILE"

echo "ledger-check: $(cat "$STATS_FILE")"
echo "ledger-check: derived ${#NODEIDS[@]} nodeid(s) in $(( (T1 - T0) / 1000000 ))ms"

if [ "${#NODEIDS[@]}" -lt "$MIN_NODEIDS" ]; then
  echo "ledger-check: COULD NOT MEASURE — the derivation returned ${#NODEIDS[@]}" >&2
  echo "  nodeid(s), below the floor of ${MIN_NODEIDS}. A short list runs fast and" >&2
  echo "  checks nothing, so this is NOT a pass. Either the census guards moved" >&2
  echo "  behind an indirection census_scan.py cannot resolve — in which case fix" >&2
  echo "  the derivation, do not lower the floor — or the tree is not this repo." >&2
  echo "RESULT: COULD-NOT-MEASURE (exit=3)"
  exit 3
fi

if [ "$LIST_ONLY" -eq 1 ]; then
  printf '%s\n' "${NODEIDS[@]}"
  echo "ledger-check: LISTED ONLY — nothing ran. A listing is not a verdict."
  exit 0
fi

# --- run -----------------------------------------------------------------------
PYTEST_ARGS=(-q -p no:cacheprovider --no-header)
if [ "$JOBS" != "0" ]; then
  PYTEST_ARGS+=(-n "$JOBS" --dist load)
fi
# The four isolation plugins `run-tests.sh` loads for every target. Three of the
# directories this selection reaches have no conftest of their own, so relying
# on conftest registration alone would leave them unprotected — the per-directory
# drift `testlib/nolaunch.py` exists to prevent.
for _p in nolaunch_plugin spool_plugin nogit_plugin gitenv_plugin; do
  PYTEST_ARGS+=(-p "testlib.$_p")
done

export PYTHONPATH="$ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}"
# A stale `.pyc` keyed on whole-second mtime can serve the PREVIOUS body of a
# file this run is meant to judge.
export PYTHONDONTWRITEBYTECODE=1

OUT_FILE="$(mktemp -t ledger-check-out.XXXXXX)"
cleanup() { rm -f "$NODEID_FILE" "$STATS_FILE" "$OUT_FILE"; }

T2=$(date +%s%N)
( cd "$ROOT" && python3 -m pytest "${PYTEST_ARGS[@]}" "${EXTRA[@]}" "${NODEIDS[@]}" ) \
  2>&1 | tee "$OUT_FILE"
rc="${PIPESTATUS[0]}"
T3=$(date +%s%N)

DERIVE_MS=$(( (T1 - T0) / 1000000 ))
RUN_MS=$(( (T3 - T2) / 1000000 ))
echo "ledger-check: derive ${DERIVE_MS}ms + run ${RUN_MS}ms = $(( (DERIVE_MS + RUN_MS) / 1000 ))s wall"

# 🔴 READ THE CONTENT, NOT THE EXIT CODE. A run killed before pytest wrote a
# summary exits non-zero for a reason that is not "a test failed", and a wrapper
# can swallow the status entirely. The summary line is the verdict; the exit
# status is a cross-check, and a disagreement is COULD NOT MEASURE.
SUMMARY="$(grep -Eo '[0-9]+ (passed|failed|error)' "$OUT_FILE" | tail -5 | tr '\n' ' ')"
if [ -z "$SUMMARY" ]; then
  echo "ledger-check: COULD NOT MEASURE — pytest printed no parseable summary" >&2
  echo "  (exit=$rc). That is not a pass; read the output above." >&2
  echo "SCOPE: LEDGERS"
  echo "RESULT: COULD-NOT-MEASURE (exit=3)"
  exit 3
fi

echo "SCOPE: LEDGERS  (derived repo-census tests only — NOT a gate)"
if [ "$rc" -eq 0 ]; then
  echo "RESULT: PASS (exit=0)  [$SUMMARY]"
  echo "  This says no repo-census guard is red on this tree. It says NOTHING"
  echo "  about the rest of the suite — that is scripts/gate.sh --tier both."
  exit 0
fi
echo "RESULT: FAIL (exit=$rc)  [$SUMMARY]"
echo "  A repo-census guard is red. The usual cause is a file that landed"
echo "  without its ledger row — add the row, do not widen the guard."
exit 1
