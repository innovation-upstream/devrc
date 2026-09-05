#!/usr/bin/env bash
# Mutation battery for nix/system/apply-nebula-relay.sh + check-nebula-relays.sh.
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   bash scripts/tests/mutants-nebula-relay.sh          # exit 0 only if ALL ok
#   bash scripts/tests/mutants-nebula-relay.sh M-FA-1   # one mutant by name
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. `nix/system/` is copied into a `mktemp -d`,
# the mutation is applied THERE, and scripts/tests/test_nebula_relay_apply.py is
# pointed at the copy with DEVRC_TEST_NEBULA_DIR. Both scripts come from that one
# directory because apply resolves the verifier from its own location.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT AND THE MESSAGE IT MUST DIE WITH.
# "A test failed" is not enough: with overlapping assertions a mutant can die to a
# DIFFERENT test's error and be scored as covered while its own assertion is
# unreachable. A mutant whose named test does not fail, or fails without a line
# matching its expected message, reports 🔴 WRONG-KILLER.
#
# 🔴 EACH MUTATION IS APPLIED-VERIFIED. The occurrence count is asserted before
# substituting and the result is re-checked after: an unapplied edit produces a fully
# green run that reads EXACTLY like a caught mutant.
#
# 🔴 THREE CONTROLS, CHECKED FIRST.
#   CONTROL-CLEAN     — no mutation; must be fully green, or every SURVIVED is
#                       meaningless.
#   CONTROL-KILL      — a mutation known to be catchable; if it survives, the harness
#                       is not running the tests it thinks it is.
#   CONTROL-DETECTOR  — feeds the WRONG-KILLER check a message the run did NOT die
#                       with, and requires it to say NO. A detector that only ever says
#                       yes certifies everything.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1 throughout. CPython validates a cached module on
# mtime-in-whole-SECONDS plus size, so a same-length edit landing in the same second as
# the last import is invisible and the mutant is scored SURVIVED without ever running.
# Nothing here imports the scripts as modules, but the test file itself is cached, and
# the cost of being wrong about that is a silently vacuous sweep.
set -uo pipefail

export PYTHONDONTWRITEBYTECODE=1

# shellcheck disable=SC1007
HERE="$(CDPATH= cd -P -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1007
ROOT="$(CDPATH= cd -P -- "$HERE/../.." && pwd)"
SRC_DIR="$ROOT/nix/system"
TESTFILE="$ROOT/scripts/tests/test_nebula_relay_apply.py"

[ -f "$SRC_DIR/apply-nebula-relay.sh" ] || { echo "FATAL: $SRC_DIR/apply-nebula-relay.sh missing"; exit 2; }
[ -f "$SRC_DIR/check-nebula-relays.sh" ] || { echo "FATAL: $SRC_DIR/check-nebula-relays.sh missing"; exit 2; }
[ -f "$TESTFILE" ] || { echo "FATAL: $TESTFILE missing"; exit 2; }

WORK="$(mktemp -d -t mutants-nebula.XXXXXX)"
: > "$WORK/.started"        # a fixed timestamp for the /tmp sweep below

# 🔴 THE BATTERY MUST CLEAN UP AFTER ITS OWN MUTANTS. M-FC-1 restores the very bug the
# fix removed — a root script writing `/tmp/nebula-relay-pre.$$` — and that mutant has
# no cleanup, so every run left files behind. They then failed
# `test_no_predictable_tmp_path_is_live_while_the_verifier_runs` in every LATER run:
# stale residue read as a live defect. The test now subtracts a pre-run snapshot, and
# this sweep stops the pile-up at the source. It is scoped three ways — this user, this
# exact name shape, newer than this run's start — and never lets a pattern reach a kill.
cleanup() {
  find /tmp -maxdepth 1 -name 'nebula-relay-pre.*' -type f \
       -user "$(id -un)" -newer "$WORK/.started" -delete 2>/dev/null
  chmod -R u+w "$WORK" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

ONLY="${1:-}"
pass=0; fail=0

# ---------------------------------------------------------------------- machinery
# Fresh copy of nix/system into $WORK/<tag>/ and echo the directory.
fresh() {
  # 🔴 Two statements, not `local tag="$1" d="$WORK/$tag"`: bash expands every word of
  # a command BEFORE performing its assignments, so `$tag` there is still unset and
  # under `set -u` the whole function dies. CONTROL-CLEAN caught exactly that.
  local tag="$1"
  local d="$WORK/$tag"
  rm -rf "$d"; mkdir -p "$d"
  cp -a "$SRC_DIR/." "$d/"
  echo "$d"
}

# Apply one literal substitution, asserting the occurrence count before and after.
#   $1 = file  $2 = expected count  $3 = old  $4 = new
mutate() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import sys
path, want, old, new = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
s = open(path, encoding="utf-8").read()
n = s.count(old)
if n != want:
    sys.stderr.write("MUTATE-FAILED: expected %d occurrence(s) of %r, found %d\n"
                     % (want, old[:80], n))
    sys.exit(1)
out = s.replace(old, new)
open(path, "w", encoding="utf-8").write(out)
if out.count(new) < 1 or out == s:
    sys.stderr.write("MUTATE-FAILED: substitution did not take\n")
    sys.exit(1)
PY
}

# Run the test file against a (possibly mutated) nix/system copy.
#   $1 = directory, $2... = extra pytest args
run_tests() {
  local d="$1"; shift
  DEVRC_TEST_NEBULA_DIR="$d" \
    python3 -m pytest "$TESTFILE" -q -p no:randomly --no-header "$@" 2>&1
}

# Does OUT contain a FAILED line for TEST?
died_by() {  # $1 = output, $2 = test name
  printf '%s\n' "$1" | grep -q "::$2\b.*\(FAILED\|failed\)" \
    || printf '%s\n' "$1" | grep -q "^FAILED .*::$2\b"
}

# Did the named test's own failure carry the expected message?
# Re-runs JUST that test with full output, because pytest truncates long assertion
# messages in a whole-file `-q` run and grepping that run reports a false NO.
wrong_killer_check() {  # $1 = dir, $2 = test, $3 = expected substring
  local out
  out=$(run_tests "$1" -k "$2" -vv --tb=long)
  printf '%s\n' "$out" | grep -Fq -- "$3"
}

report() {  # $1 = name, $2 = verdict, $3 = detail
  case "$2" in
    ok)          echo "  ✅ $1 — $3"; pass=$((pass+1)) ;;
    survived)    echo "  🔴 $1 — SURVIVED: $3"; fail=$((fail+1)) ;;
    wrongkiller) echo "  🔴 $1 — WRONG-KILLER: $3"; fail=$((fail+1)) ;;
    broken)      echo "  🔴 $1 — HARNESS: $3"; fail=$((fail+1)) ;;
  esac
}

# One mutant.
#   $1 name  $2 file(apply|check)  $3 count  $4 old  $5 new  $6 killer-test  $7 message
mutant() {
  local name="$1" which="$2" cnt="$3" old="$4" new="$5" killer="$6" msg="$7"
  [ -n "$ONLY" ] && [ "$ONLY" != "$name" ] && return 0
  local d f out
  d=$(fresh "$name")
  case "$which" in
    apply) f="$d/apply-nebula-relay.sh" ;;
    check) f="$d/check-nebula-relays.sh" ;;
    *) report "$name" broken "unknown file selector '$which'"; return ;;
  esac
  if ! mutate "$f" "$cnt" "$old" "$new"; then
    report "$name" broken "the mutation did not apply"; return
  fi
  out=$(run_tests "$d")
  if ! printf '%s\n' "$out" | grep -qE '[0-9]+ failed'; then
    report "$name" survived "the whole file stayed green (killer was $killer)"; return
  fi
  if ! died_by "$out" "$killer"; then
    report "$name" wrongkiller "$killer did NOT fail; something else did:
$(printf '%s\n' "$out" | grep '^FAILED' | sed 's/^/      /')"
    return
  fi
  if ! wrong_killer_check "$d" "$killer" "$msg"; then
    report "$name" wrongkiller "$killer failed, but not with '$msg'"; return
  fi
  report "$name" ok "killed by $killer with its own message"
}

# ------------------------------------------------------------------------ controls
echo "== controls =="
CTL=$(fresh control-clean)
CTL_OUT=$(run_tests "$CTL")
if printf '%s\n' "$CTL_OUT" | grep -qE '^[0-9]+ passed'; then
  echo "  ✅ CONTROL-CLEAN — $(printf '%s\n' "$CTL_OUT" | grep -E '^[0-9]+ passed')"
  pass=$((pass+1))
else
  echo "  🔴 CONTROL-CLEAN — the UNMUTATED copy is not green; every result below is meaningless"
  printf '%s\n' "$CTL_OUT" | tail -25
  exit 2
fi

mutant "CONTROL-KILL" apply 1 \
  'echo "=== DONE ==="' 'echo "=== FINISHED ==="' \
  test_happy_path_patches_activates_then_persists \
  'assert "=== DONE ===" in r.stdout'

# CONTROL-DETECTOR: the same mutation, checked against a message the run canNOT have
# produced. The detector must say NO here; one that always says yes certifies nothing.
if [ -z "$ONLY" ] || [ "$ONLY" = "CONTROL-DETECTOR" ]; then
  D=$(fresh control-detector)
  if mutate "$D/apply-nebula-relay.sh" 1 'echo "=== DONE ==="' 'echo "=== FINISHED ==="'; then
    if wrong_killer_check "$D" test_happy_path_patches_activates_then_persists \
         'THIS STRING IS NOT IN ANY ASSERTION IN THIS REPO'; then
      echo "  🔴 CONTROL-DETECTOR — the detector matched a message that cannot be there"
      fail=$((fail+1))
    else
      echo "  ✅ CONTROL-DETECTOR — says NO to a message the run did not produce"
      pass=$((pass+1))
    fi
  else
    echo "  🔴 CONTROL-DETECTOR — HARNESS: mutation did not apply"; fail=$((fail+1))
  fi
fi
echo

# ------------------------------------------------------------------------- mutants
echo "== mutants =="

# --- F-A: the rollback message must distinguish three states -----------------------
mutant "M-FA-1-switch-attempted-inverted" apply 1 \
  'elif [ "$SWITCH_ATTEMPTED" = "1" ]; then' \
  'elif [ "$SWITCH_ATTEMPTED" = "0" ]; then' \
  test_switch_failure_after_a_good_test_does_not_claim_nothing_persisted \
  'assert "THE PROFILE MAY HAVE MOVED" in r.stderr'

mutant "M-FA-2-activated-flag-never-set" apply 1 \
  $'nixos-rebuild test\nACTIVATED=1' \
  $'nixos-rebuild test\nACTIVATED=0' \
  test_verifier_failure_after_a_good_test_says_activated_not_persisted \
  'assert "ACTIVATED, NOT PERSISTED" in r.stderr'

mutant "M-FA-3-persist-first" apply 1 \
  $'TEST_ATTEMPTED=1\nnixos-rebuild test' \
  $'TEST_ATTEMPTED=1\nnixos-rebuild switch' \
  test_happy_path_patches_activates_then_persists \
  'assert rig.log("rebuild") == ["test", "switch"]'

mutant "M-FA-4-test-failure-claims-never-activated" apply 1 \
  'elif [ "$TEST_ATTEMPTED" = "1" ]; then' \
  'elif [ "$TEST_ATTEMPTED" = "9" ]; then' \
  test_rebuild_test_failure_persists_nothing \
  'assert "NOT PERSISTED" in r.stderr'

# --- F-B: the anchor is scoped to the named network --------------------------------
mutant "M-FB-1-anchor-count-unscoped" apply 1 \
  '  NR < s || NR > e { prev = $0; next }' \
  '  NR < 1 { prev = $0; next }' \
  test_net_without_the_anchor_does_not_reach_into_another_network \
  'assert "cannot locate exactly one nebula settings block to patch inside" in r.stderr'

# 🔴 READ THE DEATH, NOT JUST THE RED. This mutant is killed, but NOT by the assertion
# that names the wrong block: with two anchors in the file the unscoped awk makes TWO
# insertions, so the pass's own `n != 1` END guard exits 3 and the run aborts rc 1
# before anything is written. So the range guard in the patch pass is DEFENCE IN DEPTH,
# not the thing that prevents a wrong write — the scoped anchor count above it already
# does that, and no input that gets past the anchor count can make this guard the
# difference between a right and a wrong write. It is scored on the message it really
# dies with, so nobody reads this line as coverage it does not provide.
mutant "M-FB-2-patch-pass-unscoped" apply 1 \
  'if (NR-1 >= s && NR <= e && prev == "    settings = {"' \
  'if (prev == "    settings = {"' \
  test_the_insertion_lands_in_the_named_network_not_the_first_anchor \
  'ABORT: the patch pass did not make exactly one insertion'

mutant "M-FB-3-locator-accepts-many" apply 1 \
  'if len(starts) != 1:' \
  'if len(starts) < 0:' \
  test_unknown_network_aborts \
  'assert "found 0 line(s) matching" in r.stderr'

# --- F-C: no predictable /tmp path -------------------------------------------------
mutant "M-FC-1-predictable-tmp" apply 1 \
  'PRE="$SCRATCH/pre.out"' \
  'PRE="/tmp/nebula-relay-pre.$$"' \
  test_no_predictable_tmp_path_is_live_while_the_verifier_runs \
  'a predictable /tmp path was live while the verifier ran'

# --- F-D: a missing backup must be loud --------------------------------------------
mutant "M-FD-1-silent-rollback-skip" apply 1 \
  '  else
    # 🔴 NEVER SILENT.' \
  '  elif false; then
    # 🔴 NEVER SILENT.' \
  test_missing_backup_fails_loudly_and_says_the_config_is_still_patched \
  'ROLLBACK FAILED'

# `&&` -> `||` short-circuits TRUE whenever the backup exists, so "ROLLED BACK" is
# printed without the cp ever running. The missing-backup test still passes (its backup
# is gone, so the cp does run and fails); the test that sees it is the one asserting the
# config was actually restored.
mutant "M-FD-2-cp-status-ignored" apply 1 \
  'if [ -f "$BAK" ] && cp -p "$BAK" "$CFG"; then' \
  'if [ -f "$BAK" ] || cp -p "$BAK" "$CFG"; then' \
  test_rebuild_test_failure_persists_nothing \
  'the config must be restored'

# --- F-E: a symlinked $CFG is refused ----------------------------------------------
mutant "M-FE-1-symlink-accepted" apply 1 \
  'if [ "$cfg_real" != "$CFG" ]; then' \
  'if false; then' \
  test_symlinked_config_is_refused_and_survives \
  'the symlink must still be a symlink'

# --- F-G: the verifier's FAIL output reaches the operator ---------------------------
mutant "M-FG-1-fail-output-swallowed" apply 1 \
     '     echo "  the verifier'"'"'s finding, in full -- read the cost note before continuing:"
     sed '"'"'s/^/    | /'"'"' "$PRE"' \
     '     :' \
  test_the_verifiers_fail_output_reaches_the_operator \
  'assert "EGRESSES THE RELAY" in r.stdout'

# --- F-I: the retry is narrower than rc 2 ------------------------------------------
mutant "M-FI-1-retry-on-any-rc2" apply 1 \
  "if [ \"\$rc\" = \"2\" ] && grep -qx 'REASON: unit-process-disagree' \"\$out\"; then" \
  'if [ "$rc" = "2" ]; then' \
  test_a_different_rc2_does_not_earn_a_restart \
  'an unreadable -config must NOT earn a restart'

mutant "M-FI-2-reason-line-dropped" check 1 \
  '  echo "REASON: $token" >&2' \
  '  :' \
  test_deferred_restart_is_retried \
  'assert r.returncode == 0'

# --- F-F: the parser's block terminator is load-bearing ----------------------------
mutant "M-FF-1-terminator-removed" check 1 \
  '            if not line.startswith((" ", "\t")):   # next top-level key ends the block' \
  '            if False:   # MUTANT' \
  test_check_script_self_test_passes \
  'terminator: later top-level am_relay is OUTSIDE FAILED'

# --- declared survivors -------------------------------------------------------------
# A mutant listed here is asserted to SURVIVE, with the reason written down. If one
# starts dying, the justification is wrong and the battery says so — the same shape as
# CONTROL-DETECTOR, one level up: a claim about coverage that can itself go red.
declared_survivor() {  # $1 name  $2 file  $3 count  $4 old  $5 new  $6 why
  local name="$1" which="$2" cnt="$3" old="$4" new="$5" why="$6"
  [ -n "$ONLY" ] && [ "$ONLY" != "$name" ] && return 0
  local d f out
  d=$(fresh "$name")
  case "$which" in
    apply) f="$d/apply-nebula-relay.sh" ;;
    check) f="$d/check-nebula-relays.sh" ;;
  esac
  if ! mutate "$f" "$cnt" "$old" "$new"; then
    report "$name" broken "the mutation did not apply"; return
  fi
  out=$(run_tests "$d")
  if printf '%s\n' "$out" | grep -qE '[0-9]+ failed'; then
    echo "  🔴 $name — DECLARED SURVIVOR BUT DIED; the justification below is wrong:"
    echo "      $why"
    printf '%s\n' "$out" | grep '^FAILED' | sed 's/^/      /'
    fail=$((fail+1))
  else
    echo "  ⚪ $name — survives as declared: $why"
    pass=$((pass+1))
  fi
}

declared_survivor "M-X-1-line-count-guard-off" apply 1 \
  '[ "$added" = "4" ]' \
  '[ "$added" != "999" ]' \
  "belt-and-braces. The awk pass already exits 3 unless it made EXACTLY ONE insertion,
      and one insertion is always four lines, so no input reachable through this
      script's own guards can make \$added anything but 4. The check is kept because it
      is the only one that would notice awk itself changing; it is NOT coverage, and
      calling it covered would be the F-F mistake again."

# --- the patch itself ---------------------------------------------------------------
mutant "M-X-2-backup-overwrite-allowed" apply 1 \
  'cp -p "$CFG" "$BAK"' \
  'true' \
  test_happy_path_patches_activates_then_persists \
  'assert len(backups) == 1'

echo
echo "== summary: pass=$pass fail=$fail =="
[ "$fail" = "0" ]
