#!/usr/bin/env bash
# Mutation battery for `githooks/install.sh` — the read-only-global fix (#905).
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   bash scripts/tests/mutants-install-sh.sh          # exit 0 only if ALL ok
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. The whole `githooks/` directory is
# copied into a `mktemp -d` and the mutation is applied THERE; the tests are
# pointed at the copy with `DEVRC_TEST_INSTALLER`. `install.sh` derives `$DIR`
# from its own location, so a copy installs the copy's path and the test file
# derives `GITHOOKS` from the same place — one source, not two constants that
# would agree until the day this ran.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT, AND THE MESSAGE IT MUST DIE
# WITH. "A test failed" is not enough: with overlapping assertions a mutant can
# die to a DIFFERENT test's error and be scored as covered while its own
# assertion is unreachable. A mutant whose named test does not fail, or fails
# without its own message, reports 🔴 WRONG-KILLER.
#
# 🔴 EACH MUTATION IS APPLIED-VERIFIED BEFORE IT RUNS. The occurrence count is
# asserted before substituting and re-counted after: an unapplied `sed` produces
# a fully green run that reads EXACTLY like a caught mutant.
#
# 🔴 THREE CONTROLS, CHECKED FIRST, AND EACH ONE HAS CAUGHT A REAL FAULT HERE.
#   CONTROL-CLEAN     — no mutation; must be fully green, or every SURVIVED
#                       below is meaningless.
#   CONTROL-KILL      — a mutation known to be catchable; if it survives, the
#                       harness is not running the tests it thinks it is.
#   CONTROL-DETECTOR  — feeds the WRONG-KILLER check a message the run did NOT
#                       die with and requires it to say NO. A detector that only
#                       ever says yes certifies everything; this one did, once.
set -uo pipefail

# 🔴 `CDPATH=` is load-bearing here too — see githooks/install.sh's $DIR comment.
# Without it, an exported CDPATH made this battery unrunnable rather than wrong:
# HERE became two lines and the file check below died `FATAL: /githooks/install.sh
# missing`. A harness that cannot start reports nothing, so the failure is loud —
# but a harness whose paths are half-resolved is exactly how a sweep scores
# SURVIVED for a mutant it never executed.
# shellcheck disable=SC1007  # `CDPATH= cd` is a deliberate prefix assignment, not a typo
HERE="$(CDPATH= cd -P -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1007
ROOT="$(CDPATH= cd -P -- "$HERE/../.." && pwd)"
SRC_DIR="$ROOT/githooks"
TESTFILE="$ROOT/scripts/tests/test_githooks_install_readonly_global.py"

[ -f "$SRC_DIR/install.sh" ] || { echo "FATAL: $SRC_DIR/install.sh missing"; exit 2; }
[ -f "$TESTFILE" ] || { echo "FATAL: $TESTFILE missing"; exit 2; }

WORK="$(mktemp -d -t mutants-install-sh.XXXXXX)"
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

pass=0; fail=0

# Run the test file against a (possibly mutated) copy of githooks/.
#   $1 = directory holding install.sh
# Prints the pytest tail; returns pytest's status.
run_tests() {
  DEVRC_TEST_INSTALLER="$1/install.sh" \
    python -m pytest "$TESTFILE" -q -p no:randomly --no-header 2>&1
}

# Re-run ONE test with full, untruncated assertion output.
#
# 🔴 THIS IS NOT A CONVENIENCE — it is the fix for a measured false verdict.
# The first version of this battery grepped the whole-file `-q` run for the
# killer's message. pytest TRUNCATES a long assertion message (these embed the
# installer's entire stdout+stderr), so the message was absent from the output
# and two genuinely-killed mutants were reported 🔴 WRONG-KILLER. Grepping a
# rendering you do not control is a dependency on its FORMAT: "no match" meant
# "pytest elided it", not "the test died for another reason".
run_one() {
  DEVRC_TEST_INSTALLER="$1/install.sh" \
    python -m pytest "$TESTFILE::$2" -vv --tb=long -p no:randomly --no-header 2>&1
}

# case2 <label> <killer> <msg> <n1> <from1> <to1> <n2> <from2> <to2>
#
# 🔴 WHY A TWO-SUBSTITUTION MUTANT EXISTS AT ALL. Every other mutant here
# removes a single defence and dies. The CDPATH hazard is guarded TWICE and
# INDEPENDENTLY — `CDPATH= cd -P --` stops $DIR going multi-line, and the
# `case` refusal stops a multi-line $DIR reaching the config write — so each
# single-line mutant SURVIVES because the other defence still holds the
# property. MEASURED, both directions: reverting only the hardening → the guard
# refuses (rc=1, "single-line path to a real directory"); neutering only the
# guard → $DIR is single-line
# and it never fires. Scoring either as a coverage gap would be wrong, and
# shipping them as permanent 🔴 would train everyone to ignore this battery.
# What IS worth pinning is that the test notices the state where NEITHER holds
# — the exact shape that shipped. That needs both edits at once.
case_mutant2() {
  local label="$1" killer="$2" msg="$3"
  local n1="$4" from1="$5" to1="$6" n2="$7" from2="$8" to2="$9"
  echo "── $label"
  local dir; dir="$(fresh_copy)"
  if ! mutate "$dir" "$n1" "$from1" "$to1"; then fail=$((fail+1)); return; fi
  if ! mutate "$dir" "$n2" "$from2" "$to2"; then fail=$((fail+1)); return; fi

  local out="$WORK/out2.$$"; run_tests "$dir" > "$out" 2>&1
  if ! grep -q "FAILED.*::$killer" "$out"; then
    echo "    🔴 SURVIVED: '$killer' did not fail"
    tail -6 "$out" | sed 's/^/       /'
    fail=$((fail+1)); return
  fi
  local one="$WORK/one2.$$"; run_one "$dir" "$killer" > "$one" 2>&1
  if ! _died_with "$one" "$msg"; then
    echo "    🔴 WRONG-KILLER: '$killer' failed, but NOT with its own message"
    echo "       expected to see: $msg"
    tail -12 "$one" | sed 's/^/       /'
    fail=$((fail+1)); return
  fi
  echo "    ok — killed by $killer, with its own message"
  pass=$((pass+1))
}

fresh_copy() {  # -> path of a pristine githooks copy
  local d; d="$(mktemp -d "$WORK/case.XXXXXX")"
  cp -R "$SRC_DIR" "$d/githooks"
  chmod -R u+w "$d/githooks"
  printf '%s\n' "$d/githooks"
}

# mutate <dir> <expected_count> <literal-from> <literal-to>
# 🔴 APPLIED-VERIFICATION lives here, not in the caller.
#
# 🔴 COUNTED IN PYTHON, NOT WITH `grep -c`. `grep -c -F` counts matching LINES
# and treats a multi-line pattern as SEVERAL patterns, so the M5 anchor (a
# two-line function body) counted 16 and the mutant was scored NOT-APPLIED. A
# substring count is what "occurrences" means here; grep cannot express it.
mutate() {
  local dir="$1" want="$2" from="$3" to="$4"
  python3 - "$dir/install.sh" "$want" "$from" "$to" <<'PY'
import sys
p, want, a, b = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
s = open(p, encoding="utf-8").read()
before = s.count(a)
if before != want:
    print(f"    🔴 NOT-APPLIED: expected {want} occurrence(s) of the anchor, "
          f"found {before}")
    sys.exit(1)
s = s.replace(a, b)
if s.count(a) != 0:
    print("    🔴 NOT-APPLIED: anchor still present after substitution")
    sys.exit(1)
open(p, "w", encoding="utf-8").write(s)
PY
}

# Did the run FAIL with this exact message?
#
# 🔴 ONLY `^E ` LINES. pytest's `--tb=long` ECHOES THE TEST SOURCE, and every
# message this battery looks for is a string literal in that source — so a grep
# over the whole output matched whenever the named test failed for ANY reason,
# and the detector was INERT. MEASURED: with the fallback branch disabled,
# `test_the_effectiveness_check_refuses_a_shadowed_write` died on its SECOND
# assertion, yet the whole-output grep still "found" the FIRST assertion's
# message — at line 29, in the echoed source. `^E ` is pytest's marker for the
# assertion output itself, and it rejects that case while still matching the
# message the test really died with (both directions measured).
#
# This is the SAME CLASS as the truncation bug this battery already fixed —
# parsing a rendering whose format was never pinned — in a new shape.
_died_with() {  # $1 = pytest output file, $2 = expected message
  grep '^E ' "$1" | grep -qF -- "$2"
}

# case <label> <killer-test> <own-message> <count> <from> <to>
case_mutant() {
  local label="$1" killer="$2" msg="$3" want="$4" from="$5" to="$6"
  echo "── $label"
  local dir; dir="$(fresh_copy)"
  if ! mutate "$dir" "$want" "$from" "$to"; then fail=$((fail+1)); return; fi

  # Phase 1 — the named test must be among the whole-file failures.
  local out="$WORK/out.$$"; run_tests "$dir" > "$out" 2>&1
  if ! grep -q "FAILED.*::$killer" "$out"; then
    echo "    🔴 SURVIVED: '$killer' did not fail"
    tail -6 "$out" | sed 's/^/       /'
    fail=$((fail+1)); return
  fi
  # Phase 2 — re-run it ALONE, untruncated, and require ITS OWN message.
  local one="$WORK/one.$$"; run_one "$dir" "$killer" > "$one" 2>&1
  if ! _died_with "$one" "$msg"; then
    echo "    🔴 WRONG-KILLER: '$killer' failed, but NOT with its own message"
    echo "       expected to see: $msg"
    tail -12 "$one" | sed 's/^/       /'
    fail=$((fail+1)); return
  fi
  echo "    ok — killed by $killer, with its own message"
  pass=$((pass+1))
}

# 🔴 EVERY CHECK BELOW GREPS A FILE, NEVER `printf … | grep -q`.
#
# MEASURED, and it produced a confident FALSE VERDICT: `set -o pipefail` is on,
# and `grep -q` EXITS AT THE FIRST MATCH. When the match appears early in a large
# output, `printf` still has tens of kilobytes to write, takes SIGPIPE, and the
# pipeline returns 141 — so a pipeline that FOUND its pattern reports failure.
# CONTROL-KILL was scored SURVIVED against a run with 10 genuinely failing tests
# (`### grep: NO MATCH rc=141`, output 23 751 bytes).
#
# 🔴 AND IT IS WORSE IN THE NEGATED FORM: CONTROL-CLEAN used
# `! printf … | grep -q failed`, where the SIGPIPE turns into a report of "no
# failures" — a FALSE GREEN on a suite that was actually red, which would have
# certified every mutant below against a broken baseline.
#
# 🔴 MY FIRST CONTROL FOR THIS MISSED IT, and the reason is worth keeping: I put
# the pattern at the END of the test string, which is precisely the position that
# CANNOT produce SIGPIPE — grep must read everything to find it, so printf always
# completes. A control has to place the match EARLY to exercise the hazard.
echo "=== CONTROL-CLEAN (no mutation — must be fully green) ==="
CLEAN="$(fresh_copy)"
clean_out="$WORK/clean.out"; run_tests "$CLEAN" > "$clean_out" 2>&1
if grep -qE "^[0-9]+ passed" "$clean_out" && ! grep -q "failed" "$clean_out"; then
  echo "    ok — $(grep -oE '[0-9]+ passed' "$clean_out" | head -1) against an unmutated copy"
  pass=$((pass+1))
else
  echo "    🔴 CONTROL-CLEAN IS NOT GREEN — every result below is meaningless."
  tail -15 "$clean_out" | sed 's/^/       /'
  exit 1
fi

echo "=== CONTROL-KILL (a mutation known to be catchable) ==="
KDIR="$(fresh_copy)"
if mutate "$KDIR" 1 'git config --file "$1" core.hooksPath "$DIR"' \
                    'git config --file "$1" core.hooksPath "/bogus/killable"'; then
  kout="$WORK/kill.out"; run_tests "$KDIR" > "$kout" 2>&1
  if grep -q "failed" "$kout"; then
    echo "    ok — the harness does observe a broken installer"
    pass=$((pass+1))
  else
    echo "    🔴 CONTROL-KILL SURVIVED — the harness is not running these tests."
    tail -10 "$kout" | sed 's/^/       /'
    exit 1
  fi
else
  echo "    🔴 CONTROL-KILL could not be applied"; exit 1
fi

echo "=== CONTROL-DETECTOR (can the WRONG-KILLER check say NO?) ==="
# 🔴 A DETECTOR THAT ONLY EVER SAYS YES IS NOT A DETECTOR. The previous version
# of this battery said yes unconditionally (see `_died_with`), so every "killed
# with its own message" verdict below was unearned. This builds a run that fails
# for a KNOWN, DIFFERENT reason and requires the detector to reject the message
# that does NOT belong to it, then accept the one that does. Both directions, or
# the eleven mutant results below mean nothing.
DDIR="$(fresh_copy)"
if mutate "$DDIR" 1 \
    'elif [ "$PICK" != "$FALLBACK" ] && err2="$(_set_hookspath "$FALLBACK" 2>&1)"; then' \
    'elif false; then'; then
  dprobe="$WORK/detector.out"
  run_one "$DDIR" "test_the_effectiveness_check_refuses_a_shadowed_write" > "$dprobe" 2>&1
  # It dies on its SECOND assertion. The FIRST assertion's message is a literal
  # in the echoed source — the exact string that fooled the old detector.
  foreign="reported success while core.hooksPath resolved"
  actual="install failed, but not with the effectiveness check's own message"
  if _died_with "$dprobe" "$foreign"; then
    echo "    🔴 DETECTOR IS INERT — it matched a message the test did NOT die with."
    echo "       Every mutant verdict below would be unearned."; exit 1
  fi
  if ! _died_with "$dprobe" "$actual"; then
    echo "    🔴 DETECTOR IS DEAF — it rejected the message the test DID die with."
    tail -12 "$dprobe" | sed 's/^/       /'; exit 1
  fi
  echo "    ok — rejects a foreign message, accepts the true one"
  pass=$((pass+1))
else
  echo "    🔴 CONTROL-DETECTOR could not be applied"; exit 1
fi

echo "=== MUTANTS ==="

# M1 — the fallback never fires. This is the defect itself, re-injected.
case_mutant "M1 fallback branch disabled" \
  "test_the_installer_succeeds_when_the_global_config_is_read_only" \
  "installer failed on a home-manager host" \
  1 \
  'elif [ "$PICK" != "$FALLBACK" ] && err2="$(_set_hookspath "$FALLBACK" 2>&1)"; then' \
  'elif false; then'

# M2 — "wrote" is accepted as "installed". The narrowest expression that can be
# wrong is the comparison itself, so only that is mutated.
case_mutant "M2 effectiveness check disabled" \
  "test_the_effectiveness_check_refuses_a_shadowed_write" \
  "reported success while core.hooksPath resolved" \
  1 \
  'if [ "$effective" != "$DIR" ]; then' \
  'if [ "$effective" != "$effective" ]; then'

# M3 — the explicit-override refusal is removed, so the fallback escapes the
# file the caller nominated. Must die on the ESCAPE assertion, not on the rc one.
case_mutant "M3 GIT_CONFIG_GLOBAL refusal removed" \
  "test_an_explicit_GIT_CONFIG_GLOBAL_is_never_escaped" \
  "ESCAPED the nominated GIT_CONFIG_GLOBAL" \
  1 \
  'elif [ -n "${GIT_CONFIG_GLOBAL:-}" ]; then' \
  'elif false; then'

# M4 — --uninstall stops checking whose value it is about to remove.
case_mutant "M4 uninstall ownership check removed" \
  "test_uninstall_leaves_a_hooks_path_it_did_not_write" \
  "removed a core.hooksPath belonging to someone else" \
  1 \
  '[ "$current" = "$DIR" ] || continue' \
  'true'

# M5 — --uninstall searches only the XDG file again, so it cannot find a value
# the install put in the fallback.
case_mutant "M5 uninstall no longer searches ~/.gitconfig" \
  "test_uninstall_reverts_the_fallback_file" \
  "STILL in effect after --uninstall" \
  1 \
  '  printf '"'"'%s\n'"'"' "$HOME/.gitconfig"
  _xdg_config
}' \
  '  _xdg_config
}'

# M6 — the audit flag file stops being seeded (consequence 2 of #905).
case_mutant "M6 audit env seeding skipped" \
  "test_the_audit_env_file_is_seeded_on_a_read_only_host" \
  "the flag file was not seeded" \
  1 \
  'if [ ! -f "$CONF" ]; then' \
  'if false; then'

# M7 — the stamp is never written, so a file we DID create looks foreign and
# --uninstall can no longer clean it up.
case_mutant "M7 creation stamp never written" \
  "test_uninstall_removes_only_a_gitconfig_it_created_itself" \
  "but --uninstall left it behind" \
  1 \
  'if [ -n "$CREATED_FILE" ]; then _stamp_marker "$CREATED_FILE"; fi' \
  'if false; then _stamp_marker "$CREATED_FILE"; fi'

# M8 — provenance replaced by SIZE, i.e. the audited defect re-injected verbatim.
case_mutant "M8 provenance check reverted to a size test" \
  "test_uninstall_keeps_an_operator_created_gitconfig" \
  "DELETED a ~/.gitconfig the operator created" \
  1 \
  '&& _only_devrc_marker "$f"; then' \
  '&& [ ! -s "$f" ]; then'

# M9 — a failed install stops rolling back the file it created.
case_mutant "M9 failed-install rollback removed" \
  "test_a_failed_install_rolls_back_the_file_it_created" \
  "left behind the ~/.gitconfig it created" \
  1 \
  '  if [ -n "$CREATED_FILE" ]; then
    git config --file "$CREATED_FILE" --unset core.hooksPath 2>/dev/null || true' \
  '  if false; then
    git config --file "$CREATED_FILE" --unset core.hooksPath 2>/dev/null || true'

# M10 — the repo-discovery ceiling is dropped, so a TMPDIR inside a checkout
# poisons the effectiveness read with a repo-local value.
case_mutant "M10 GIT_CEILING_DIRECTORIES dropped" \
  "test_the_effective_read_is_not_poisoned_by_a_repo_around_TMPDIR" \
  "the effectiveness read found a REPO-LOCAL" \
  1 \
  'GIT_CEILING_DIRECTORIES="$(dirname "$scratch")" \' \
  'GIT_CEILING_DIRECTORIES="" \'

# M11 — the HOME diagnostic is removed and `set -u` aborts instead.
case_mutant "M11 unset-HOME diagnostic removed" \
  "test_an_unset_HOME_gives_a_diagnostic_not_an_unbound_variable" \
  "expected a diagnostic about HOME" \
  1 \
  'if [ -z "${HOME:-}" ]; then' \
  'if false; then'

# M12 — BOTH CDPATH defences removed at once, reproducing the pre-fix shape that
# corrupted ~/.gitconfig host-wide. See case_mutant2's header for why this one
# mutant takes two edits while every other mutant here takes one.
case_mutant2 "M12 both CDPATH defences removed (the shape that shipped)" \
  "test_an_exported_CDPATH_cannot_corrupt_the_global_config" \
  "corrupted the global git config" \
  1 \
  'DIR="$(CDPATH= cd -P -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' \
  'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' \
  1 \
  '_reject_bad_dir "$DIR"' \
  ':'

echo
echo "=== RESULT: ok=$pass  problems=$fail ==="
[ "$fail" -eq 0 ] || exit 1
