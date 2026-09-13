#!/usr/bin/env bash
# Mutation battery for `scripts/lib/host_label.py` — the module that answers
# WHICH MACHINE IS THIS. Its wrong answer is silent and routes work to the wrong
# host (#1601), so "the guards are covered" has to be re-derivable, not believed.
#
#   nix develop -c bash scripts/tests/mutants-host-label.sh
#
# 🔴 THE SHAPE IS `mutants-worktree-prune.sh`'s, DELIBERATELY — same `run()`
# contract, same controls, same summary parse. There is one thing to learn here,
# not thirteen. Read that file's header for the reasoning behind each rule; only
# what is SPECIFIC to this module is repeated below.
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. Every mutant is written into a COPY of
# `scripts/` under `mktemp -d`, and the copy is asserted to carry no `.git` —
# a `cp -a` of a worktree carries a `.git` POINTER FILE, and a git command
# inside such a copy acts on the REAL repository.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT. "A test failed" is not
# enough: this suite has overlapping assertions (several tests call
# `local_host_label`), so a mutant can die to a DIFFERENT test's error and be
# scored covered while its own assertion is never reached. A mutant killed only
# by some other test reports 🔴 WRONG-KILLER, not ok.
#
# 🔴 EACH MUTANT IS DIFFED AGAINST THE ORIGINAL BEFORE IT RUNS. A `sed` that
# silently fails to match reports the UNMUTATED file's behaviour — "the guard
# held", the most flattering possible wrong answer. Real risk here: this module
# is mostly comment, and several patterns anchor on lines a reword would move.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1 IS LOAD-BEARING, NOT HYGIENE. CPython validates a
# cached module on mtime-in-whole-SECONDS + size, so a same-length edit landing
# in the same second as the last import is invisible: the run would import the
# ORIGINAL bytecode and the mutant would be scored SURVIVED without ever
# executing. `if False:` substitutions below are close to same-length.
#
# 🔴 THE SUMMARY LINE IS PARSED, NEVER "THE LAST LINE OF THE OUTPUT" — the
# direnv/devshell banner is the last line of a merged stream.
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/hostlabel-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"
mkdir -p "$ROOT"
cp -a "$SRC/scripts" "$ROOT/scripts"
if [ -e "$ROOT/.git" ] || [ -e "$ROOT/scripts/.git" ]; then
  echo "🔴 the copy carries a .git — refusing to run"; exit 2
fi
find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

MOD="$ROOT/scripts/lib/host_label.py"
SUITE="$ROOT/scripts/tests/test_host_label_identity.py"
cp "$MOD" "$T/mod.orig"
ORIG_SHA="$(sha256sum "$T/mod.orig" | cut -d' ' -f1)"
restore() {
  cp "$T/mod.orig" "$MOD"
  # 🔴 VERIFIED, not assumed. A battery that leaves a mutant in place scores
  # every LATER mutant against a doubly-broken module.
  local now; now="$(sha256sum "$MOD" | cut -d' ' -f1)"
  if [ "$now" != "$ORIG_SHA" ]; then
    echo "🔴 restore FAILED — the copy is still mutated; aborting"; exit 2
  fi
}

FAILURES=0

# 🔴 A SUITE THAT NEVER RAN YIELDS ZERO `FAILED` LINES, i.e. "clean". Reading
# only FAILED lines cannot tell "no test failed" from "pytest died at
# collection", and the SURVIVES control would then report ok over a harness
# wired to nothing. So COUNT the collected tests and refuse below a floor. The
# floor catches COLLAPSE, not growth, so it sits under the real count.
MIN_TESTS=20
failing() {
  local out n f total
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" \
    -q --no-header --tb=no -p no:cacheprovider -p no:randomly 2>/dev/null)"
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $MIN_TESTS)"
    return
  fi
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out"
}

run() { # run <name> <expect: a test node name | SURVIVES> <sed-expr>
  local name="$1" want="$2" expr="$3"
  sed "$expr" "$MOD" > "$T/m" 2>/dev/null
  if cmp -s "$MOD" "$T/m"; then
    printf '  🔴 %-52s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  cp "$T/m" "$MOD"
  local killers; killers="$(failing)"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-52s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = SURVIVES ]; then
    if [ -z "$killers" ]; then
      printf '  ok %-52s SURVIVED as required (control)\n' "$name"; return
    fi
    printf '  🔴 %-52s CONTROL KILLED by %s — not measuring behaviour\n' \
      "$name" "$(tr '\n' ',' <<<"$killers")"; FAILURES=$((FAILURES+1)); return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-52s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  if grep -qx "$want" <<<"$killers"; then
    printf '  ok %-52s killed by %s\n' "$name" "$want"; return
  fi
  printf '  🔴 %-52s WRONG-KILLER — died to: %s (wanted %s)\n' \
    "$name" "$(tr '\n' ',' <<<"$killers")" "$want"; FAILURES=$((FAILURES+1))
}

printf 'mutating a COPY at %s (your worktree is untouched)\n' "$ROOT"
printf 'baseline (must be empty): '
b="$(failing)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }

printf '\n== the defect itself: the workbench default ==\n'
# MUT-1. THE POSITIVE CONTROL FOR THE WHOLE BATTERY: a mutant everyone knows
# must die, restoring the exact literal #1601 removed.
run 'MUT-1 unresolved-returns-workbench' \
  test_nothing_determines_it_REFUSES_instead_of_saying_workbench \
  's|^    raise HostLabelUnresolved($|    return "workbench"\n&|'

printf '\n== the cross-check and the precedence ==\n'
run 'MUT-2 conflict-check-dropped' \
  test_a_stated_label_the_address_contradicts_RAISES \
  's|^    if stated and derived and stated != derived:$|    if False:|'
# 🔴 MUT-3 IS AN *EQUIVALENT* MUTANT, AND IT IS KEPT RATHER THAN DELETED.
# It was written expecting a kill, SURVIVED, and the survival is CORRECT — not a
# coverage gap. Swapping the two returns in `local_host_label` is unobservable
# because the conflict guard three lines above has already refused the only
# input that could tell them apart:
#   stated set, derived None      -> `if derived:` is falsy either way
#   stated None, derived set      -> `if stated:` is falsy either way
#   both set and EQUAL            -> same value whichever returns
#   both set and DIFFERENT        -> HostLabelConflict, neither return runs
# So the env-over-file-over-address precedence is enforced by
# `stated_host_label` and by the conflict guard, NOT by the order of these two
# returns. Recording that is the point: the next reader who writes this mutant
# gets the answer instead of re-deriving it, and a future change that WEAKENS
# the conflict guard turns this line red, which is exactly when it should.
run 'MUT-3 derived-preferred-over-stated (EQUIVALENT)' SURVIVES \
  's|^    if stated:$|    if derived:\n        return derived\n    if stated:|'
# MUT-3b is the precedence that IS observable: env over file, inside
# `stated_host_label`, where no guard subsumes it.
run 'MUT-3b file-preferred-over-the-environment' \
  test_the_environment_still_wins \
  's|^    if v in HOST_NAMES:$|    if False:|'
run 'MUT-10 invalid-label-passed-through' \
  test_an_invalid_label_is_still_ignored_rather_than_passed_through \
  's|^    if v in HOST_NAMES:$|    if v:|'

printf '\n== the address signal ==\n'
# 🔴 MUT-4 is the one a naive fix gets wrong: EVERY real machine holds two of
# its own addresses, so without the per-label dedupe the multi-host refusal
# fires on the workbench and the laptop alike — i.e. the fix refuses everywhere.
run 'MUT-4 per-label-dedupe-removed' \
  test_holding_BOTH_of_one_hosts_addresses_is_ordinary_not_a_conflict \
  's|^        if label in hits:$|        if False:|'
run 'MUT-5 multi-host-picks-the-first-instead-of-refusing' \
  test_holding_TWO_hosts_addresses_REFUSES_rather_than_picking_one \
  's|^    if len(hits) > 1:$|    if False:|'
run 'MUT-8 bind-probe-always-true' \
  test_the_bind_probe_answers_truthfully_about_THIS_machine \
  's|^def _bind_holds_address(addr: str) -> bool:$|&\n    return True|'

printf '\n== the shared table (host-role.sh is the owner) ==\n'
run 'MUT-6 partial-table-returned-instead-of-()' \
  test_a_table_missing_a_HOST_fails_CLOSED \
  's|^            return ()$|            pass|'
run 'MUT-7 host-role.sh-ignored-PEER_SSH-always' \
  test_the_table_FOLLOWS_the_shell_file_rather_than_being_a_copy \
  's|^    if key:$|    if False:|'

printf '\n== the shell entry point transcript-push.sh reads ==\n'
run 'MUT-9 refusal-on-stdout-and-exit-0' \
  test_the_shell_entry_point_prints_nothing_and_exits_nonzero_on_a_refusal \
  's|^        raise SystemExit(3)$|        print(exc)\n        raise SystemExit(0)|'

printf '\n== controls ==\n'
# 🔴 THE NEGATIVE CONTROL ON THE HARNESS: a behaviour-free edit MUST survive. If
# it kills something, the battery is keying on the file's TEXT rather than its
# CODE and every `ok` above is worthless. It is a COMMENT edit inside the module
# body, not a whitespace change, because a whitespace change can be a no-op that
# `cmp` sees and Python does not.
run 'CONTROL comment-only-edit-must-survive' SURVIVES \
  's|^#: Test/ops seam for the address probe|#: (control edit) Test/ops seam for the address probe|'

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL MUTANTS ACCOUNTED FOR — each killed by the test named for it, control survived"
  exit 0
fi
echo "🔴 $FAILURES MUTANT(S) UNACCOUNTED FOR — read the lines above, do not re-run and hope"
exit 1
