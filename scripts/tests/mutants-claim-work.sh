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
ROWS=0

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
  ROWS=$((ROWS+1))
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
# The too-strict half, reproduced exactly: key the worktree component off the
# literal ident dir instead of the worktree's git-dir.
run 'worktree-half-is-the-literal-cwd' \
  test_release_from_a_SUBDIRECTORY_is_still_the_owners_own_claim \
  's@^owner_worktree_part="\$(git_ -C "\$IDENT_REPO" rev-parse.*@owner_worktree_part="$IDENT_REPO"@'
# 🔴 ROUND 3'S BUG, AS A MUTANT: widen the worktree half back to
# `--git-common-dir`, which every linked worktree of a clone shares. That made an
# unrelated sibling agent's claim of a peer's live slug return rc 12 "carry on".
run 'worktree-half-widened-to-git-common-dir' \
  test_a_concurrent_fanout_of_worktrees_gets_exactly_one_winner_and_no_carry_on \
  's@\(^owner_worktree_part="\$(git_ -C "\$IDENT_REPO" rev-parse .*\)--git-dir@\1--git-common-dir@'
# …and the same widening seen through the DESTRUCTIVE verb, so the two readers of
# `claim_is_mine` are each pinned by a mutant of their own. Round 3's note said
# the token gated only these verbs; it was the verdict path that broke.
run 'worktree-half-widened--release-side' \
  test_two_worktrees_of_one_clone_are_DIFFERENT_owners \
  's@\(^owner_worktree_part="\$(git_ -C "\$IDENT_REPO" rev-parse .*\)--git-dir@\1--git-common-dir@'
# 🔴 THE SILENT FALLBACK (round 4, B2). Deleting the warning must go red: a probe
# failure reinstates the round-2 cwd-keyed token, and the symptom it produces —
# rc 10 on your own claim — is indistinguishable from somebody else holding it.
run 'degraded-git-dir-probe-goes-quiet' \
  test_an_unreadable_git_dir_probe_SAYS_it_degraded \
  's@^  warn "could not read this directory.s git dir@  : "@'
# The too-loose half, reproduced exactly: key the host component off `uname -n`,
# which is `nixos` on BOTH machines in this fleet.
run 'host-half-is-uname-not-machine-id' \
  test_two_hosts_produce_different_owner_tokens \
  's@^  OWNER_HOST_PART="machine-id:\$machine_id"@  OWNER_HOST_PART="hostname:$MY_HOST"@'
# Drop the worktree component entirely: every session on one host becomes one owner.
run 'worktree-half-dropped-from-the-token' \
  test_two_different_clones_on_one_host_are_different_owners \
  's@^\$owner_worktree_part"$@CONSTANT-CLONE"@'
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
# 🔴 MY_CLONE_ROOT must keep coming from `--git-common-dir` even though the TOKEN
# narrowed to `--git-dir`. Deriving it from the git dir gives a linked worktree
# `<clone>/.git/worktrees/<name>`, which matches no `*/.git` case, so
# MY_CLONE_ROOT is EMPTY and the three live claims become unreleasable from a
# worktree — the stuck lock, on the refs that exist today.
run 'clone-root-derived-from-the-worktree-git-dir' \
  test_a_legacy_cwd_claim_is_releasable_from_a_worktree_of_that_clone \
  's@\(^owner_common_part="\$(git_ -C "\$IDENT_REPO" rev-parse .*\)--git-common-dir@\1--git-dir@'
# 🔴 THE LEGACY HOST CHECK (round 4, B4). It was outside the previous sweep's
# closed set while being the tier that is ACTUALLY live.
run 'legacy-host-check-removed' \
  test_a_legacy_cwd_claim_from_another_HOST_is_not_yours \
  's@^  \[ -n "\$h" \] && \[ "\$h" = "\$MY_HOST" \] || return 1$@  :@'

printf '\n== the TRAILER READ must not inherit the callers git config (KILLED) ==\n'
# 🔴 `interpret-trailers` obeys `trailer.*`; the awk line scan it replaced did
# not. `trailer.separators = '"'"'='"'"'` in the CALLER's repo made every ownership read
# empty ⇒ rc 10 on your own 0-second-old claim. Dropping `-C "$WS"` puts the
# caller's repo-local config back in the stack.
run 'trailer-read-runs-in-the-callers-repo' \
  test_the_callers_trailer_config_cannot_lock_the_owner_out \
  's@; git_ -C "\$WS" -c trailer.separators=:@; git_ -c trailer.separators=:@'
# …and dropping the global/system neutralisation, which `-C` cannot do.
run 'trailer-read-keeps-the-global-config-layer' \
  test_the_callers_trailer_config_cannot_lock_the_owner_out \
  's@( export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1; @( @'

printf '\n== the SUBJECT is not a place to write trailers (must be KILLED) ==\n'
run 'subject-control-chars-allowed' \
  test_a_subject_carrying_a_control_character_is_a_usage_error \
  's@if \[\[ \$SUBJECT =~ .*; then@if false; then@'
run 'both-trailer-guards-removed' \
  test_a_forged_subject_cannot_shadow_the_ownership_trailers \
  's@^    | ( export GIT_CONFIG_GLOBAL=.*interpret-trailers --parse 2>/dev/null ) \\$@    | cat \\@;s@v=\$0 } END { if (v != "") print v }@print; exit }@'
# 🔴 AND THE STRUCTURAL READ ON ITS OWN, which the note below used to assert kills
# nothing. It does. Isolated so the row means what it says: only the
# `interpret-trailers` pipeline is replaced; the last-match awk is untouched.
run 'structural-trailer-read-removed' \
  test_a_forged_subject_cannot_shadow_the_ownership_trailers \
  's@^    | ( export GIT_CONFIG_GLOBAL=.*interpret-trailers --parse 2>/dev/null ) \\$@    | cat \\@'
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
# 🔴 THE COMMENT THAT USED TO BE HERE WAS WRONG, AND WRONG IN THE DIRECTION THAT
# STOPS ANYONE LOOKING. It said the structural `interpret-trailers --parse` read
# "alone still kills nothing" — i.e. that it was pure redundancy behind the
# last-match rule. Measured in round 4: removing it on its own goes RED. The
# structural read is genuinely covered; it is the LAST-MATCH half that is the
# documented survivor. Keeping the wrong note would have told a maintainer an
# uncovered guard was covered and a covered guard was not — both directions at
# once, out of one sentence. The isolated removal is now a KILLED row of its own
# (`structural-trailer-read-removed`, above), not a claim in a comment.
#
# The remaining survivor: `claim_field` has TWO defences against a prepended
# forgery, and the SUBJECT is always FIRST in the message, so taking the LAST
# match is defeated by nothing the CLI can produce once the structural read is in
# place. Mutating it alone therefore survives, by design.
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
# 🔴 THE SUMMARY STATES ITS OWN SCOPE. "ALL OK" read as a claim about the script;
# it is a claim about the rows ABOVE and nothing else. Round 4 found two live
# guards outside the closed set — the legacy tier's `host:` check (on the tier
# that is ACTUALLY live: all three claims on the real origin are `cwd:`-format)
# and the git-dir probe's fallback — and the previous summary reported ALL OK over
# both. Both are covered now; the point is that the WORDING must not re-create the
# gap for the next one. `claude/RULES.md`: a green sweep is only a claim about the
# mutations you IMAGINED.
printf 'scope: %d mutant(s), enumerated in this file. NOT a claim about any guard\n' "$ROWS"
printf '       this file does not name — add a row when you add a guard.\n'
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL OK (for the $ROWS mutant(s) above)"
  exit 0
fi
echo "$FAILURES of $ROWS row(s) not ok"
exit 1
