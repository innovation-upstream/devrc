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

# 🔴 TWO MORE MUTATION TARGETS, BECAUSE THE MODULE IS NOT WHERE THE REMAINING
# HOST-IDENTITY DEFECTS LIVE.
#
#   * `scripts/collector/collector.py` — the ONE consumer that did not derive.
#     Its guards are scored against `scripts/collector/tests/test_collector.py`
#     (`failing_collector`), because that is the suite that owns them.
#   * `nix/home.nix` — the collector's DEPLOYMENT: the two `lib/` files placed
#     beside the daemon and the unit's restart triggers. Neither is observable
#     from the collector suite, so those rows are scored against the two derived
#     ledgers in `scripts/tests/test_transcript_push.py` (`failing_ledger`).
#
# ⚠ AN EARLIER REVISION MUTATED `nix/home.nix` AND SCORED IT AGAINST A SUITE THAT
# NIX-BUILT THE ACTIVATION BLOCK FOR REAL. That harness could not run in
# `checks.pytests` — nested `nix-build` with `<nixpkgs>`, no store realisation in
# the sandbox — so every guard it carried was invisible to the merge gate. The
# activation it guarded is gone; nothing here builds anything.
#
# Same contract as `run` throughout: exact-name killer, applied-diff check,
# verified restore.
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

COLL="$ROOT/scripts/collector/collector.py"
cp "$COLL" "$T/coll.orig"
COLL_ORIG_SHA="$(sha256sum "$T/coll.orig" | cut -d' ' -f1)"
restore_coll() {
  cp "$T/coll.orig" "$COLL"
  local now; now="$(sha256sum "$COLL" | cut -d' ' -f1)"
  if [ "$now" != "$COLL_ORIG_SHA" ]; then
    echo "🔴 collector restore FAILED — the copy is still mutated; aborting"; exit 2
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

# The collector suite. Its own floor, for the same reason as the one above: a
# collection error yields zero FAILED lines, i.e. "clean".
COLL_SUITE="$ROOT/scripts/collector/tests/test_collector.py"
MIN_COLL_TESTS=30
failing_collector() {
  local out n f total
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$COLL_SUITE" \
    -q --no-header --tb=no -p no:cacheprovider -p no:randomly 2>/dev/null)"
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_COLL_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total collector test(s) ran (floor $MIN_COLL_TESTS)"
    return
  fi
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out"
}

# 🔴 THE DEPLOYMENT LEDGERS, SELECTED BY EXACT NODE ID. `test_transcript_push.py`
# is a heavy suite (it stands up servers and drives the real push script) and
# these two tests are pure file reads, so the whole file is not run per mutant.
# A `-k` filter would be a second spelling that can silently select nothing; two
# explicit node ids plus a floor of exactly 2 cannot.
LEDGERS=(
  "$ROOT/scripts/tests/test_transcript_push.py::test_the_ACTIVITY_COLLECTOR_triggers_on_the_host_identity_files_IT_LOADS"
  "$ROOT/scripts/tests/test_transcript_push.py::test_the_host_identity_pair_is_DEPLOYED_beside_the_collector"
)
failing_ledger() {
  local out n f total
  out="$(cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "${LEDGERS[@]}" \
    -q --no-header --tb=no -p no:cacheprovider -p no:randomly 2>/dev/null)"
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -ne 2 ]; then
    echo "__HARNESS_BROKE__ $total ledger test(s) ran, want exactly 2"
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
  local killers; killers="$(failing_ledger)"
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

run_coll() { # run_coll <name> <expect: a test node name | SURVIVES> <sed-expr>
  local name="$1" want="$2" expr="$3"
  sed "$expr" "$COLL" > "$T/mc" 2>/dev/null
  if cmp -s "$COLL" "$T/mc"; then
    printf '  🔴 %-52s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  cp "$T/mc" "$COLL"
  local killers; killers="$(failing_collector)"
  restore_coll
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
printf 'baseline host_label suite (must be empty): '
b="$(failing)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }
printf 'baseline collector suite (must be empty): '
b="$(failing_collector)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }
printf 'baseline deployment ledgers (must be empty): '
b="$(failing_ledger)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }

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

printf '\n== the collector: derive, and DEGRADE rather than crash ==\n'
# 🔴 MUT-C1 IS #1601 IN THE ONE CONSUMER THAT STILL HAD IT. Absent
# `ACTIVITY_HOST`, `from_env` used to answer `""`, under which `parse_line`
# leaves emit's `host=$(hostname)` — `nixos` on BOTH machines — on every row.
run_coll 'MUT-C1 the derive is dropped (back to "")' \
  test_an_ABSENT_ACTIVITY_HOST_is_DERIVED_from_an_address_this_machine_holds \
  's|^                           or _derive_host_label(e)),$|                           or ""),|'
# 🔴 MUT-C2 IS THE ONE THIS WHOLE REWORK'S LIVE RISK RESTS ON. `local_host_label`
# RAISES on a machine it cannot name; narrow the catch so that escapes and the
# `Restart=always` daemon does not start at all — a far worse outcome than the
# mislabelled column the PR set out to fix. `KeyError` is not in
# `HostLabelError`'s bases, so an unresolvable machine propagates.
run_coll 'MUT-C2 the degrade path is dead (daemon crashes)' \
  test_an_UNIDENTIFIABLE_machine_DEGRADES_rather_than_crashing_the_daemon \
  's|^    except Exception as exc:  # noqa: BLE001|    except KeyError as exc:  # noqa: BLE001|'
# MUT-C2b: the catch narrowed to the module's OWN exception class. An
# unidentifiable machine is then still handled, but a `lib/` that failed to
# deploy raises ImportError and takes the daemon down — the deployment mistake
# the broad catch exists for. `HostLabelError` subclasses RuntimeError.
run_coll 'MUT-C2b the catch narrowed to HostLabelError only' \
  test_a_BROKEN_host_label_module_DEGRADES_rather_than_crashing_the_daemon \
  's|^    except Exception as exc:  # noqa: BLE001|    except RuntimeError as exc:  # noqa: BLE001|'
# 🔴 MUT-C3: precedence — AND ITS KILLER IS NOT THE TEST WHOSE NAME SAYS SO.
# Scored first against `test_an_explicit_ACTIVITY_HOST_still_WINS_over_the_
# derivation`, this mutant SURVIVED, and correctly: `_derive_host_label` passes
# `env` through to `local_host_label`, which raises `HostLabelConflict` on a
# stated label its address contradicts, and the broad catch turns that into `""`
# — so the expression falls through to the environment whichever order it is in.
# The two orders differ on exactly ONE input, an INVALID stated label, which the
# module ignores and this daemon passes through. That is the row below.
run_coll 'MUT-C3 the derivation preempts an explicit ACTIVITY_HOST' \
  test_an_INVALID_ACTIVITY_HOST_is_still_passed_through_UNCHANGED \
  's|^            host_override=(e.get("ACTIVITY_HOST", "").strip()$|            host_override=(_derive_host_label(e) or e.get("ACTIVITY_HOST", "").strip()|'
# 🔴 MUT-C6 AND MUT-C7 ARE THE DEPLOYED LAYOUT, WHICH NO REPO-LAYOUT TEST TOUCHES.
# C6 drops the `lib/` candidate that only exists on a host; C7 resolves the
# symlink, which walks out of ~/.config/activity-collector into /nix/store and
# loses the sibling. Both are green in the repo and inert on both machines.
run_coll 'MUT-C6 the deployed lib/ candidate is dropped' \
  test_the_DEPLOYED_symlink_layout_can_derive_the_host_label \
  's|^    os.path.join(_SELF_DIR, "lib"),                    # deployed: …/lib$|    # (mutant) the deployed candidate is gone|'
run_coll 'MUT-C7 realpath instead of abspath (resolves into the store)' \
  test_the_DEPLOYED_symlink_layout_can_derive_the_host_label \
  's|^_SELF_DIR = os.path.dirname(os.path.abspath(__file__))$|_SELF_DIR = os.path.dirname(os.path.realpath(__file__))|'
# The negative control for THIS target: a comment-only edit must survive.
run_coll 'CONTROL collector comment-only edit must survive' SURVIVES \
  's|^# WHICH MACHINE IS THIS (#1601)$|# (control edit) WHICH MACHINE IS THIS (#1601)|'

printf '\n== the collector DEPLOYMENT (nix/home.nix) ==\n'
# MUT-C4: the restart triggers lose the host-identity pair. The daemon is
# long-lived, so it would keep stamping every row from a stale address table.
# 🔴 RANGE-ADDRESSED TO THE COLLECTOR UNIT. That trigger line is spelled
# identically in FOUR units; a bare `s|…|…|` would mutate all of them at once,
# which is a different (and much wider) mutant than the one this row claims to
# score.
run_nix 'MUT-C4 the collector unit drops the identity triggers' \
  test_the_ACTIVITY_COLLECTOR_triggers_on_the_host_identity_files_IT_LOADS \
  '/^  systemd\.user\.services\.activity-collector = {$/,/^  };$/s|^        "${\.\./scripts/lib/host_label\.py}"$|        "${../scripts/collector/emit}"|'
# 🔴 MUT-C5: the `lib/` files are not DEPLOYED beside the daemon. The switch
# succeeds and the collector silently stops deriving — it catches the
# ImportError by design, so nothing about the unit's status says so.
run_nix 'MUT-C5 host-role.sh is not deployed beside the collector' \
  test_the_host_identity_pair_is_DEPLOYED_beside_the_collector \
  's|^  home\.file\.".config/activity-collector/lib/host-role.sh".source =$|  home.file.".config/activity-collector/lib/UNUSED-host-role.sh".source =|'

printf '\n== controls ==\n'
# 🔴 THE NEGATIVE CONTROL ON THE HARNESS: a behaviour-free edit MUST survive. If
# it kills something, the battery is keying on the file's TEXT rather than its
# CODE and every `ok` above is worthless. It is a COMMENT edit inside the module
# body, not a whitespace change, because a whitespace change can be a no-op that
# `cmp` sees and Python does not.
run 'CONTROL comment-only-edit-must-survive' SURVIVES \
  's|^#: Test/ops seam for the address probe|#: (control edit) Test/ops seam for the address probe|'
# The same negative control for the nix target: the two ledgers read `home.nix`
# as TEXT, so a comment edit is exactly the change that would expose a guard
# keying on prose rather than on the declarations it claims to check.
run_nix 'CONTROL nix comment-only edit must survive' SURVIVES \
  's|^  # 🔴 THE TWO FILES THAT ANSWER "WHICH MACHINE IS THIS", DEPLOYED BESIDE THE$|  # (control edit) THE TWO FILES THAT ANSWER "WHICH MACHINE IS THIS", BESIDE THE|'

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL MUTANTS ACCOUNTED FOR — each killed by the test named for it, control survived"
  exit 0
fi
echo "🔴 $FAILURES MUTANT(S) UNACCOUNTED FOR — read the lines above, do not re-run and hope"
exit 1
