#!/usr/bin/env bash
# Mutation battery for `scripts/claim-work.sh` — the claim-by-push lock.
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed.
#
#   bash scripts/tests/mutants-claim-work.sh          # exit 0 only if ALL ok
#
# 🔴 IT EXISTS BECAUSE THE PREVIOUS SWEEP DID NOT. Round 2 of this file reported
# 25 mutants with three "SURVIVED by design" rows, and the driver was never
# committed — so nobody could re-check a single row, including the three that
# claimed to be deliberate. `scripts/tests/mutants-dead-guard.sh` and
# `mutants-base-clone.sh` already established the convention; this follows it.
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. Everything is mutated inside a
# `mktemp -d` copy. In a repo whose rules are built around shared checkouts and
# parallel agents, an EXIT-trap restore over a tracked file is one SIGKILL or one
# concurrent `git add` away from a mutated file being staged by somebody else.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT. "A test failed" is not enough:
# with overlapping assertions a mutant can die to a DIFFERENT test's error and be
# scored as covered while its own assertion is unreachable. A mutant killed only
# by some other test reports 🔴 WRONG-KILLER, not ok.
#
# 🔴 EACH MUTATION IS DIFFED AGAINST THE ORIGINAL BEFORE IT RUNS. A `sed` that
# silently fails to match reports the UNMUTATED file's behaviour — i.e. "the
# guard held", the most flattering possible wrong answer, and the exact way a
# previous sweep in this repo scored an unmutated baseline as "killed".
#
# 🔴 TWO CONTROLS, BOTH MANDATORY:
#   * the unmutated BASELINE must be green (else every row is meaningless);
#   * a `SURVIVES` row — a behaviour-free edit that must NOT kill anything, which
#     is what proves the harness keys on BEHAVIOUR and not on the file's text.
#   * plus `already-caught-positive-control`, a mutant known to be covered, so a
#     harness wired to nothing cannot report a clean sweep.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1 even though the file under mutation is BASH: the
# pytest module and `testlib` are Python, and a stale `.pyc` keyed on
# mtime-in-whole-seconds + size is how a same-length edit gets scored SURVIVED
# without ever executing. Cheap, and the failure it prevents is silent.
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SRC="$(cd "$D/../.." && pwd)"

T="$(mktemp -d /tmp/claim-work-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"

mkdir -p "$ROOT/scripts/tests" \
         "$ROOT/claudedocs" \
         "$ROOT/claude/skills/handoff/reference" \
         "$ROOT/claude/skills/resume" \
         "$ROOT/nix"
# `cp -a` on the script so its EXECUTABLE bit survives —
# `test_the_script_is_executable` reads it.
cp -a "$SRC/scripts/claim-work.sh" "$ROOT/scripts/"
cp -a "$SRC/scripts/testlib" "$ROOT/scripts/"
cp -a "$SRC/scripts/tests/test_claim_work.py" "$ROOT/scripts/tests/"
cp -a "$SRC/claudedocs/design-claim-by-push.md" "$ROOT/claudedocs/"
cp -a "$SRC/claude/RULES.md" "$ROOT/claude/"
cp -a "$SRC/claude/skills/handoff/reference/shared-queue.md" \
      "$ROOT/claude/skills/handoff/reference/"
cp -a "$SRC/claude/skills/resume/SKILL.md" "$ROOT/claude/skills/resume/"
# The prose ledgers reach further than the module under mutation: `nix/home.nix`
# (the cross-runtime deploy claim) and the handoff SKILL body are both read.
cp -a "$SRC/claude/skills/handoff/SKILL.md" "$ROOT/claude/skills/handoff/"
cp -a "$SRC/nix/home.nix" "$ROOT/nix/"

# 🔴 A `cp -a` of a WORKTREE would carry its `.git` POINTER FILE, and a git
# command inside the copy would then act on the REAL repository. Nothing above
# copies `.git`; this asserts it rather than assuming it.
if [ -e "$ROOT/.git" ]; then
  echo "🔴 the copy carries a .git — refusing to run"; exit 2
fi
find "$ROOT" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

SCRIPT="$ROOT/scripts/claim-work.sh"
SUITE="$ROOT/scripts/tests/test_claim_work.py"
cp -a "$SCRIPT" "$T/script.orig"
ORIG_SHA="$(sha256sum "$T/script.orig" | cut -d' ' -f1)"
restore() { cp -a "$T/script.orig" "$SCRIPT"; }

FAILURES=0

# failed test names, one per line. Read the CONTENT — never an exit code.
# 🔴 A SUITE THAT NEVER RAN YIELDS ZERO `FAILED` LINES, i.e. "clean", so a
# harness wired to nothing would score every mutant as SURVIVED and every
# SURVIVES control as ok. Count the collected tests and refuse below a floor.
# The floor catches COLLAPSE, not growth — deliberately far under the real count.
MIN_TESTS=60
failing() {
  local out n f total
  out="$(PYTHONDONTWRITEBYTECODE=1 python3 -m pytest "$SUITE" -q --no-header --tb=no \
    -p no:cacheprovider 2>/dev/null)"
  n="$(sed -n 's/^\([0-9]*\) passed.*/\1/p;s/^[0-9]* failed, \([0-9]*\) passed.*/\1/p' <<<"$out" | tail -1)"
  f="$(sed -n 's/^\([0-9]*\) failed.*/\1/p' <<<"$out" | tail -1)"
  total=$(( ${n:-0} + ${f:-0} ))
  if [ "$total" -lt "$MIN_TESTS" ]; then
    echo "__HARNESS_BROKE__ only $total test(s) ran (floor $MIN_TESTS)"
    return
  fi
  # Parametrised ids are `name[param]`; the class stops at `[`, which collapses
  # every case of one test onto its base name — what `want` is spelled as.
  sed -n 's/^FAILED [^:]*::\([A-Za-z0-9_]*\).*/\1/p' <<<"$out" | sort -u
}

run() { # run <name> <expect: a test node name | SURVIVES> <sed-expr>
  local name="$1" want="$2" expr="$3"
  sed "$expr" "$SCRIPT" > "$T/m" 2>/dev/null
  if cmp -s "$SCRIPT" "$T/m"; then
    printf '  🔴 %-46s MUTATION DID NOT APPLY — result meaningless\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  # 🔴 `cat >` AND NOT `cp -a`, AND THE SURVIVES CONTROL IS WHAT FOUND THAT.
  # `sed … > "$T/m"` creates the mutant with the shell's default mode, and a
  # `cp -a` of it carried that mode onto the script — so every single mutant also
  # failed `test_the_script_is_executable`, an artefact of the HARNESS with
  # nothing to do with the mutation. The KILLED rows still named their intended
  # killer, so the sweep looked fine; only the behaviour-free control, which must
  # kill NOTHING, made the defect visible. Redirecting into the existing file
  # leaves its mode alone.
  cat "$T/m" > "$SCRIPT"
  local killers; killers="$(failing)"
  restore
  if grep -q __HARNESS_BROKE__ <<<"$killers"; then
    printf '  🔴 %-46s HARNESS BROKE — %s\n' "$name" "$killers"
    FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = SURVIVES ]; then
    if [ -z "$killers" ]; then
      printf '  ok %-46s SURVIVED as required (control)\n' "$name"; return
    fi
    printf '  🔴 %-46s CONTROL KILLED by %s — not measuring behaviour\n' \
      "$name" "$(tr '\n' ',' <<<"$killers")"; FAILURES=$((FAILURES+1)); return
  fi
  if [ "$want" = "SURVIVES_BY_DESIGN" ]; then
    if [ -z "$killers" ]; then
      printf '  ok %-46s SURVIVED (documented redundancy)\n' "$name"; return
    fi
    printf '  ⚠  %-46s killed by %s — the documented redundancy no longer holds; update the note\n' \
      "$name" "$(tr '\n' ',' <<<"$killers")"; FAILURES=$((FAILURES+1)); return
  fi
  if [ -z "$killers" ]; then
    printf '  🔴 %-46s SURVIVED — no test failed\n' "$name"
    FAILURES=$((FAILURES+1)); return
  fi
  if grep -qx "$want" <<<"$killers"; then
    # Extras are REPORTED, not swallowed: a mutant that also kills half the suite
    # is usually a mutation wider than the guard it claims to isolate.
    local extra; extra="$(grep -vx "$want" <<<"$killers" | tr '\n' ',' | sed 's/,$//')"
    if [ -n "$extra" ]; then
      printf '  ok %-46s killed by %s  (also: %s)\n' "$name" "$want" "$extra"
    else
      printf '  ok %-46s killed by %s\n' "$name" "$want"
    fi
    return
  fi
  printf '  🔴 %-46s WRONG-KILLER — died to: %s (wanted %s)\n' \
    "$name" "$(tr '\n' ',' <<<"$killers")" "$want"; FAILURES=$((FAILURES+1))
}

printf 'mutating a COPY at %s (your worktree is untouched)\n' "$ROOT"
printf 'baseline (must be empty): '
b="$(failing)"; [ -z "$b" ] && echo "clean" || { echo "🔴 ALREADY RED: $b"; exit 1; }

printf '\n== the OWNER TOKEN: both directions of the round-2 bug (must be KILLED) ==\n'
# The too-strict half, reproduced exactly: key the clone component off the
# literal ident dir instead of the clone's git-common-dir.
run 'clone-half-is-the-literal-cwd' \
  test_release_from_a_SUBDIRECTORY_is_still_the_owners_own_claim \
  's@^owner_clone_part="\$(git_ -C "\$IDENT_REPO" rev-parse.*@owner_clone_part="$IDENT_REPO"@'
# The too-loose half, reproduced exactly: key the host component off `uname -n`,
# which is `nixos` on BOTH machines in this fleet.
run 'host-half-is-uname-not-machine-id' \
  test_two_hosts_produce_different_owner_tokens \
  's@^  OWNER_HOST_PART="machine-id:\$machine_id"@  OWNER_HOST_PART="hostname:$MY_HOST"@'
# Drop the clone component entirely: every session on one host becomes one owner.
run 'clone-half-dropped-from-the-token' \
  test_two_different_clones_on_one_host_are_different_owners \
  's@^\$owner_clone_part"$@CONSTANT-CLONE"@'
# The machine-id read must tolerate an absent file. Without `2>/dev/null || true`
# the substitution fails and `set -e` aborts the whole run — a tool whose
# contract is "never block a resume" dying because /etc/machine-id is missing.
run 'absent-machine-id-file-aborts-the-run' \
  test_a_missing_machine_id_file_degrades_instead_of_collapsing_every_token \
  's@^machine_id=.*$@machine_id="$(head -n1 -- "$MACHINE_ID_FILE")"@'
# The token must reach the commit. A constant trailer means nobody owns anything.
run 'owner-id-trailer-is-a-constant' \
  test_release_refuses_another_sessions_live_claim_unless_forced \
  's@^owner-id: \$MY_OWNER_ID$@owner-id: CONSTANT@'

printf '\n== the LEGACY tier, which is the one that is actually LIVE (must be KILLED) ==\n'
# All three claims on the real origin are `cwd:`-format. Narrowing the legacy
# accept back to the literal ident dir locks their holder out from a worktree.
run 'legacy-clone-root-accept-removed' \
  test_a_legacy_cwd_claim_is_releasable_from_a_worktree_of_that_clone \
  's@^  \[ -n "\$MY_CLONE_ROOT" \] && \[ "\$legacy" = "\$MY_CLONE_ROOT" \]$@  false@'
# …and widening it to "any legacy claim is mine" must fail the other leg.
run 'legacy-accept-widened-to-everyone' \
  test_a_legacy_cwd_claim_is_releasable_from_a_worktree_of_that_clone \
  's@\[ "\$legacy" = "\$MY_CLONE_ROOT" \]@true@'

printf '\n== the SUBJECT is not a place to write trailers (must be KILLED) ==\n'
run 'subject-control-chars-allowed' \
  test_a_subject_carrying_a_control_character_is_a_usage_error \
  's@if \[\[ \$SUBJECT =~ .*; then@if false; then@'
run 'both-trailer-guards-removed' \
  test_a_forged_subject_cannot_shadow_the_ownership_trailers \
  's@^    | git_ interpret-trailers --parse 2>/dev/null \\$@    | cat \\@;s@v=\$0 } END { if (v != "") print v }@print; exit }@'
run 'flag-swallowed-as-the-subject' \
  test_a_flag_swallowed_as_the_subject_is_a_usage_error \
  's@        -\*) die_usage "--subject needs text@        -zzz) die_usage "--subject needs text@'

printf '\n== rc 12: already-mine is a BRANCH, not a printed field (must be KILLED) ==\n'
run 'already-mine-returns-rc-10' \
  test_re_claiming_your_own_item_is_its_own_rc_and_says_carry_on \
  's@^    return "\$RC_MINE"$@    return "$RC_TAKEN"@'
run 'already-mine-branch-never-taken' \
  test_re_claiming_your_own_item_is_its_own_rc_and_says_carry_on \
  's@^  if \[ "\$mine" -eq 1 \]; then$@  if false; then@'
# Precedence: rc 12 must be decided BEFORE the stale return, or a stale claim of
# your own reads rc 11 and the "carry on" answer is lost.
run 'stale-outranks-mine' \
  test_re_claiming_your_own_item_is_its_own_rc_and_says_carry_on \
  's@^  if is_stale "\$age"; then$@  if is_stale "$age"; then return "$RC_TAKEN_STALE"; fi; if false; then@'

printf '\n== GUARD 9: an exported GIT_DIR beats -C (must be KILLED) ==\n'
run 'repo-pointer-unset-removed' \
  test_an_exported_GIT_DIR_cannot_make_the_lock_inert \
  's@^unset "\${DEVRC_GIT_REPO_POINTERS.*@: no unset@'

printf '\n== the positive control: a mutant already known to be covered ==\n'
run 'already-caught-positive-control' \
  test_list_flags_a_stale_claim_and_leaves_a_fresh_one_unflagged \
  's@^is_stale() { \[ "\$1" -gt @is_stale() { [ "$1" -lt @'

printf '\n== EXPECTED SURVIVORS, each with its reason ==\n'
# 🔴 `claim_field` has TWO independent defences against a prepended forgery: the
# structural `interpret-trailers --parse`, and taking the LAST match rather than
# the first. The subject is always FIRST in the message, so either one alone
# defeats everything the CLI can produce — which is why mutating either alone
# SURVIVES and only `both-trailer-guards-removed` above goes red. Recorded here
# so nobody reads the survivor as a coverage gap. (The structural read is not
# redundant in general: leg 2 of the forged test covers a legacy ref where a
# line scan finds a forged key our trailer block does not contain at all — and
# that is why REMOVING it alone still kills nothing, while removing BOTH does.)
run 'trailer-scan-takes-the-FIRST-match' SURVIVES_BY_DESIGN \
  's@v=\$0 } END { if (v != "") print v }@print; exit }@'
# The `!= "unknown"` belt on the owner-id comparison. `git hash-object` over a
# here-string cannot fail once `git` is on PATH — which the script already
# checked — so the "unknown" value is not reachable in any test, and the guard is
# defence-in-depth against a future refactor, NOT coverage. Labelled, not counted.
run 'owner-id-unknown-belt-removed' SURVIVES_BY_DESIGN \
  's@ && \[ "\$MY_OWNER_ID" != "unknown" \]@@'

printf '\n== the SURVIVES control: a behaviour-free edit must kill nothing ==\n'
run 'comment-only-edit' SURVIVES \
  's@^# claim-work — an ATOMIC@# claim-work -- an ATOMIC@'

printf '\n== the script was restored byte-identical ==\n'
NOW_SHA="$(sha256sum "$SCRIPT" | cut -d' ' -f1)"
if [ "$NOW_SHA" = "$ORIG_SHA" ]; then
  printf '  ok sha256 %s\n' "$NOW_SHA"
else
  printf '  🔴 RESTORE FAILED: %s != %s\n' "$NOW_SHA" "$ORIG_SHA"
  FAILURES=$((FAILURES+1))
fi

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL OK"
  exit 0
fi
echo "$FAILURES row(s) not ok"
exit 1
