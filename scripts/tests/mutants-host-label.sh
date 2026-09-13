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
# 🔴 `nix/` TOO, AND IT IS NOT OPTIONAL. The suite asserts things about the
# systemd units and the collector-env activation that read `nix/home.nix`
# relative to the tree root; without it those tests fail on the COPY and the
# battery aborts at its own "ALREADY RED" baseline — a harness fault that reads
# as a repo fault. Nothing here ever mutates it.
cp -a "$SRC/nix" "$ROOT/nix"
if [ -e "$ROOT/.git" ] || [ -e "$ROOT/scripts/.git" ] || [ -e "$ROOT/nix/.git" ]; then
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

# 🔴 A SECOND MUTATION TARGET, BECAUSE HALF THE HOST-IDENTITY DEFECTS WERE NOT IN
# THE MODULE. `nix/home.nix`'s `home.activation.activityCollectorEnv` is what
# actually writes `ACTIVITY_HOST` onto a machine, and its round-1 findings — an
# append that mangled the previous line of a systemd EnvironmentFile, a silently
# skipped non-writable file, a discarded diagnosis, and a re-spelled copy of the
# module's own "does this file state a label" rule — all lived there. Suite
# section 8 runs that block for real (nix-evaluates it, executes it under a
# throwaway HOME), so mutating this file is observable by the same harness.
# Same contract as `run`: exact-name killer, applied-diff check, verified restore.
NIXF="$ROOT/nix/home.nix"
cp "$NIXF" "$T/nix.orig"
NIX_ORIG_SHA="$(sha256sum "$T/nix.orig" | cut -d' ' -f1)"
restore_nix() {
  cp "$T/nix.orig" "$NIXF"
  local now; now="$(sha256sum "$NIXF" | cut -d' ' -f1)"
  if [ "$now" != "$NIX_ORIG_SHA" ]; then
    echo "🔴 nix restore FAILED — the copy is still mutated; aborting"; exit 2
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

run_nix() { # run_nix <name> <expect: a test node name | SURVIVES> <sed-expr>
  local name="$1" want="$2" expr="$3"
  sed "$expr" "$NIXF" > "$T/mn" 2>/dev/null
  if cmp -s "$NIXF" "$T/mn"; then
    printf '  🔴 %-52s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  cp "$T/mn" "$NIXF"
  local killers; killers="$(failing)"
  restore_nix
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
# 🔴 MUT-2b IS THE HALF MUT-2 CANNOT SEE. MUT-2 removes the guard entirely; a
# guard can also be NARROWED, and until this row's killer existed the suite could
# not tell the difference. Measured twice, both times with this row's killer
# absent: narrowing the check to `... and stated == "laptop"` left the suite
# fully GREEN (25 passed as the round-0 audit measured it; 28 passed re-measured
# with the other new tests present and only the killer removed) — the #1601
# direction (stated `workbench` on a machine whose address says `laptop`, which
# is what the repo's own `.env.example` used to provision) was untested, while
# only its mirror was covered.
run 'MUT-2b conflict-check-fires-in-ONE-direction-only' \
  test_a_stated_WORKBENCH_on_a_machine_whose_ADDRESS_says_laptop_RAISES \
  's|^    if stated and derived and stated != derived:$|    if stated and derived and stated != derived and stated == "laptop":|'
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
# gets the answer instead of re-deriving it.
#
# ⚠ IT IS NOT A TRIPWIRE ON THE CONFLICT GUARD, AND THIS COMMENT SAID IT WAS.
# The deleted sentence read "a future change that WEAKENS the conflict guard
# turns this line red, which is exactly when it should". False in both available
# shapes, and measured:
#   * guard REMOVED in-source  -> the battery never reaches this line. The
#     baseline check above goes red (MUT-2's killer fails on the unmutated file)
#     and the script exits 1 at "🔴 ALREADY RED".
#   * guard NARROWED in-source -> this mutant STILL SURVIVES and the battery
#     still prints ok, because the swap remains unobservable for every input the
#     narrowed guard lets through.
# The guard's tripwires are MUT-2 and MUT-2b above, each killed by a named test.
# Do not restore the tripwire claim.
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
# 🔴 THE GUARD IS PER-HOST, AND THE OLD NAME SAID PER-CONSTANT. It is the
# whole-host `return ()` that is mutated here; a table missing ONE of a host's
# two addresses is ACCEPTED by design (safe — every surviving entry is still a
# correct (host, addr) pair, so the worst case is a refusal, never a mislabel).
run 'MUT-6 whole-host-missing-table-accepted' \
  test_a_table_missing_a_WHOLE_HOST_fails_CLOSED \
  's|^            return ()$|            pass|'
run 'MUT-7 host-role.sh-ignored-PEER_SSH-always' \
  test_the_table_FOLLOWS_the_shell_file_rather_than_being_a_copy \
  's|^    if key:$|    if False:|'
# 🔴 MUT-11 PINS THE GUARD'S WIDTH, WHICH THREE COMMENTS USED TO OVERSTATE. They
# said a reformat of ONE `*_IP_*` constant makes the parse refuse; it does not —
# the guard is per-HOST (`any`), so one dropped line yields a 3-entry PARTIAL
# table, which is accepted and is safe (every surviving entry is still a correct
# (host, addr) pair). Widening it to `all` is the change those comments described,
# and this row is what makes the corrected wording machine-checked rather than
# merely reworded: change the width and the test that documents the real
# behaviour goes red.
run 'MUT-11 per-HOST-guard-widened-to-per-RANK' \
  test_a_reformatted_SINGLE_constant_degrades_to_a_PARTIAL_table_that_cannot_MISLABEL \
  's|^        if not any((host, rank) in found for rank in _ADDR_RANKS):$|        if not all((host, rank) in found for rank in _ADDR_RANKS):|'

printf '\n== the shell entry point transcript-push.sh reads ==\n'
run 'MUT-9 refusal-on-stdout-and-exit-0' \
  test_the_shell_entry_point_prints_nothing_and_exits_nonzero_on_a_refusal \
  's|^        raise SystemExit(3)$|        print(exc)\n        raise SystemExit(0)|'

printf '\n== the module rule the ACTIVATION asks (--file-states-label) ==\n'
# 🔴 MUT-F1-5. The activation used to answer "does this file already state a
# label?" with its own grep, a THIRD copy of a rule this module exists because it
# was open-coded twice. This mutant puts a grep-shaped rule back — inside the
# module, where the activation now asks — and the table row that dies is the one
# the two copies really disagreed on: `ACTIVITY_HOST=nixos` is a stated label to
# a substring test and an INVALID one to the module, so a machine carrying a
# typo'd label would never be repaired.
run 'MUT-F1-5 --file-states-label uses a substring rule' \
  test_the_activation_and_the_MODULE_agree_on_what_states_a_label \
  's|^        _stated = _file_stated_label(_body)$|        _stated = "workbench" if "ACTIVITY_HOST=" in _body else ""|'
# MUT-F1-5b: the flag's exit-code direction. Inverted, a file that states none
# reports "states one" and the activation never derives at all.
run 'MUT-F1-5b --file-states-label exit code inverted' \
  test_running_the_activation_TWICE_leaves_exactly_one_ACTIVITY_HOST_line \
  's|^        if not _stated:$|        if _stated:|'

printf '\n== the activation that writes ACTIVITY_HOST (nix/home.nix) ==\n'
# 🔴 MUT-F1-2 IS THE AUDITOR'S OWN MUTANT, RE-RUN IN THE FIX'S HOME. Before this
# round the block asked `grep -qE '^[ ]*ACTIVITY_HOST=[^ ]'` and the guard on
# that behaviour asserted `">>" in block and "grep" in block` — under which
# neutering the pattern to `ZZZ_NEVER_MATCHES` left the suite GREEN while every
# `home-manager switch` appended another line to a systemd EnvironmentFile,
# unbounded. This row restores exactly that mutant (the neutered grep, in place
# of asking the module) and it must now die to a BEHAVIOURAL test.
run_nix 'MUT-F1-2 idempotence check neutered (the old grep)' \
  test_running_the_activation_TWICE_leaves_exactly_one_ACTIVITY_HOST_line \
  's|^    if ! .*--file-states-label.*then$|    if ! grep -qE "^[ ]*ZZZ_NEVER_MATCHES=" "$envFile"; then|'
# MUT-F1-1: the newline guard. Without it, `>>` onto a file whose last line has
# no trailing newline EXTENDS that line — mangling CLICKHOUSE_PASSWORD.
run_nix 'MUT-F1-1 trailing-newline guard removed' \
  test_the_activation_appends_to_a_file_whose_last_line_has_NO_NEWLINE \
  's|^          if \[ -s "$envFile" \] && \[ -n .*then$|          if false; then|'
# MUT-F1-4: the diagnosis discarded again, which is how a generic, false cause
# came to be printed forever on a healthy host.
run_nix 'MUT-F1-4 the module stderr discarded again' \
  test_the_activation_forwards_the_MODULES_OWN_reason_for_refusing \
  's|2>"$labelErr")"|2>/dev/null)"|'
# MUT-F1-6: back to declining the write in total silence.
run_nix 'MUT-F1-6 non-writable file skipped silently' \
  test_a_NON_WRITABLE_env_file_is_reported_rather_than_skipped_in_silence \
  's|^        echo "activity-collector: $envFile states no ACTIVITY_HOST and is not writable.*|        :|'
# 🔴 MUT-F1-3: the probe drops the sibling `host-role.sh`. `host_label.py`
# locates it via `__file__`, so the loss is SILENT — right on the mesh, quietly
# non-deriving off it. (The DERIVED ledger for the same property lives in
# test_transcript_push.py::test_the_host_label_PROBE_ships_every_file_the_module
# _OPENS_BESIDE_ITSELF; this battery only runs test_host_label_identity.py, so
# that one is mutation-checked by hand — see the PR body.)
run_nix 'MUT-F1-3 probe ships host_label.py without host-role.sh' \
  test_the_built_activation_is_the_DEPLOYED_one_and_its_probe_ships_BOTH_files \
  's|^    cp ${\.\./scripts/lib/host-role\.sh} "$out/host-role\.sh"$|    true|'

printf '\n== controls ==\n'
# 🔴 THE NEGATIVE CONTROL ON THE HARNESS: a behaviour-free edit MUST survive. If
# it kills something, the battery is keying on the file's TEXT rather than its
# CODE and every `ok` above is worthless. It is a COMMENT edit inside the module
# body, not a whitespace change, because a whitespace change can be a no-op that
# `cmp` sees and Python does not.
run 'CONTROL comment-only-edit-must-survive' SURVIVES \
  's|^#: Test/ops seam for the address probe|#: (control edit) Test/ops seam for the address probe|'
# The same negative control for the SECOND target. Section 8 nix-evaluates and
# RUNS `nix/home.nix`'s activation block, so a comment edit there changes the
# built script's bytes (and its store path) without changing its behaviour — if
# that kills a test, section 8 is keying on text, not on what the block does.
run_nix 'CONTROL nix comment-only edit must survive' SURVIVES \
  's|^    # Exit 0 = the file already states a valid label|    # (control edit) Exit 0 = the file already states a valid label|'

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL MUTANTS ACCOUNTED FOR — each killed by the test named for it, control survived"
  exit 0
fi
echo "🔴 $FAILURES MUTANT(S) UNACCOUNTED FOR — read the lines above, do not re-run and hope"
exit 1
