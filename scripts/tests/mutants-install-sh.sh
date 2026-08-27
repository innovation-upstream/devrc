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
# 🔴 TWO CONTROLS, CHECKED FIRST. CONTROL-CLEAN (no mutation) must be fully
# green — if it is not, every SURVIVED below is meaningless. CONTROL-KILL is a
# mutation known to be catchable — if it survives, the harness is not running
# the tests it thinks it is.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
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
  if ! grep -qF -- "$msg" "$one"; then
    echo "    🔴 WRONG-KILLER: '$killer' failed, but NOT with its own message"
    echo "       expected to see: $msg"
    tail -12 "$one" | sed 's/^/       /'
    fail=$((fail+1)); return
  fi
  echo "    ok — killed by $killer, with its own message"
  pass=$((pass+1))
}

echo "=== CONTROL-CLEAN (no mutation — must be fully green) ==="
CLEAN="$(fresh_copy)"
clean_out="$(run_tests "$CLEAN")"
if printf '%s' "$clean_out" | grep -qE "^[0-9]+ passed" && \
   ! printf '%s' "$clean_out" | grep -q "failed"; then
  echo "    ok — $(printf '%s' "$clean_out" | grep -oE '[0-9]+ passed' | head -1) against an unmutated copy"
  pass=$((pass+1))
else
  echo "    🔴 CONTROL-CLEAN IS NOT GREEN — every result below is meaningless."
  echo "$clean_out" | tail -15 | sed 's/^/       /'
  exit 1
fi

echo "=== CONTROL-KILL (a mutation known to be catchable) ==="
KDIR="$(fresh_copy)"
if mutate "$KDIR" 1 'git config --file "$1" core.hooksPath "$DIR"' \
                    'git config --file "$1" core.hooksPath "/bogus/killable"'; then
  kout="$(run_tests "$KDIR")"
  if printf '%s' "$kout" | grep -q "failed"; then
    echo "    ok — the harness does observe a broken installer"
    pass=$((pass+1))
  else
    echo "    🔴 CONTROL-KILL SURVIVED — the harness is not running these tests."
    echo "$kout" | tail -10 | sed 's/^/       /'
    exit 1
  fi
else
  echo "    🔴 CONTROL-KILL could not be applied"; exit 1
fi

echo "=== MUTANTS ==="

# M1 — the fallback never fires. This is the defect itself, re-injected.
case_mutant "M1 fallback branch disabled" \
  "test_the_installer_succeeds_when_the_global_config_is_read_only" \
  "installer failed on a home-manager host" \
  1 \
  'elif [ "$PICK" != "$HOME/.gitconfig" ] && err2="$(_set_hookspath "$HOME/.gitconfig" 2>&1)"; then' \
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

echo
echo "=== RESULT: ok=$pass  problems=$fail ==="
[ "$fail" -eq 0 ] || exit 1
