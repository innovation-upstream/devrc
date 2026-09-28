#!/usr/bin/env bash
# Regression suite for base-clone-staleness.sh.
#
# Every case here corresponds to a defect that actually shipped, or to a fixture
# mistake that produced a confident FALSE PASS. Read the comments before deleting
# a case — several look redundant and are not.
#
# Fixtures are synthetic (a local bare "remote" + clones), so the suite is
# offline, deterministic, and every blob is known. It deliberately does NOT clone
# the real repo: the first version of these tests did, and two of them passed
# vacuously because the fixture's own origin fed the hook a baseline that moved.
#
#   bash scripts/tests/test_base_clone_staleness.sh        run
#   bash scripts/tests/test_base_clone_staleness.sh -v     show hook output per case
#
# Also run by `scripts/run-tests.sh` as a SHELL_TESTS target — a suite nothing
# invokes is a guard that reports no failures.
#
# 🔴 Validate the harness itself before trusting a green run: break the hook on
# purpose (e.g. make the guard always skip) and confirm cases go RED. A suite you
# have never watched fail is a claim about your shell, not about the code. The
# documented mutation control:
#
#   bash scripts/tests/mutants-base-clone.sh
#
# That is the battery, in-tree, with each mutant's EXPECTED verdict beside it so a
# regression in the suite's own strength is visible rather than inferred. It also
# diffs every mutant against the original before running it: a `sed` that silently
# fails to match reports the UNMUTATED file's behaviour, which reads as "the guard
# held" -- the most flattering possible wrong answer, and one that actually happened
# during authoring.
#
# ⚠️ This comment used to hardcode "must report FAIL 1", then "FAIL 5". Both went
# stale within a day as cases were added, and a stale expected count makes the next
# reader distrust a working harness. Do not reintroduce a number here -- the battery
# owns the expectations.
#
# 🔴 Two mutants are expected to SURVIVE, and the battery says so out loud: `rm -f`
# -> `rm -rf` on the file, and dropping the absolute-path guard. Both are
# unreachable by construction today. Labelling them beats pretending they are
# pinned -- and note that bounding the rmdir climb once took `rmdir` -> `rm -rf`
# OFF the board silently, which is why case 9g exists.
#
# 🔴 NO CASE HERE MAY DEPEND ON WHO WON A LOCK. Case 7 used to: two of its four
# assertions were PROGRESS claims about a concurrent pair, so sustained
# `.git/index.lock` contention took them red for a reason that is not a defect. They
# are now integrity assertions (winner-independent) plus a SERIAL run that must
# converge, and the contention path itself is driven deterministically in case 7b by
# holding the lock. This suite gates a hook that runs at every session start on this
# host; a flake in it trains people to re-run rather than read. Case 7's own comment
# carries the 220-trial measurement behind that rewrite.
#
# 🔴 HOOK defaults to the REPO copy, resolved relative to THIS FILE — never to a
# deployed per-host path under ~/.claude/. A default pointing at the deployed
# copy makes the tracked suite grade an UNTRACKED file: the gate goes green
# against something that is not in the commit, and stays green after the tracked
# hook is edited. The `HOOK=` override is deliberate and load-bearing — the
# mutation control above depends on it.

set -uo pipefail

# 🔴 `CDPATH=` first: with CDPATH set in the caller's environment `cd <dir>`
# PRINTS the resolved directory, so `$(cd … && pwd)` yields a TWO-LINE value and
# HOOK below becomes an unopenable path. `run-tests.sh` unsets it for its
# children, but this suite is also run by hand — and this exact trap already cost
# this repo a red gate whose message pointed at the wrong thing entirely
# (test_release_wrapper.sh; see the CDPATH comment in run-tests.sh).
CDPATH=
_SUITE_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
HOOK="${HOOK:-$_SUITE_DIR/../claude-hooks/base-clone-staleness.sh}"
if [ ! -f "$HOOK" ]; then
  printf 'test_base_clone_staleness: hook not found at %s\n' "$HOOK" >&2
  exit 2
fi
VERBOSE=0; [ "${1:-}" = "-v" ] && VERBOSE=1
TMP="$(mktemp -d /tmp/bcs-test-XXXXXX)"
PASS=0; FAIL=0
GIT_ID=(-c user.email=t@test -c user.name=test -c commit.gpgsign=false)

# 🔴 ABORT TRIPWIRE. A previous run of this suite exited 1 and its output was
# discarded, and the two exits are indistinguishable after the fact: `exit 1` from the
# summary (an assertion failed, and the FAIL lines say which) versus a mid-file death
# (`set -u` on an unbound variable, a helper that fatals), where the summary is never
# printed and there is nothing to read. An exit status is an EMPTY RESULT -- it cannot
# distinguish the two mechanisms -- so this makes the second case SAY so instead of
# looking like the first.
#
# ⚠️ The abort line deliberately does NOT contain the string `FAIL <n>`:
# `mutants-base-clone.sh` parses the LAST line for exactly that and would otherwise
# score an aborted suite as a killed mutant. Spelled `failed=`, it lands in that
# battery's HARNESS BROKE branch, which is the correct verdict for an abort.
_SUMMARY_PRINTED=0
cleanup() {
  local rc=$?
  if [ "$_SUMMARY_PRINTED" = 0 ]; then
    printf '\ntest_base_clone_staleness: ABORTED before the summary -- passed=%d failed=%d (exit %d).\n' \
           "$PASS" "$FAIL" "$rc"
    printf 'The summary line was never reached, so this is NOT an assertion failure. Re-run with -v.\n'
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }

# Assert a precondition. A precondition that does not hold makes the case
# VACUOUS, not passing -- this is what turned two earlier tests green while
# measuring nothing.
pre() { if [ "$2" != "$3" ]; then bad "PRECONDITION $1 (got '$2', want '$3') -- case is vacuous"; return 1; fi; return 0; }

run_hook() { # run_hook <dir> [env assignments...]
  local d="$1"; shift
  echo '{}' | env -C "$d" "$@" bash "$HOOK" 2>&1
}

blob()   { git -C "$1" hash-object "$2" 2>/dev/null; }
upblob() { git -C "$1" rev-parse "origin/fixture:$2" 2>/dev/null; }

# ---------------------------------------------------------------------------
# Fixture: a bare remote, a "primary clone" (work), and a second clone (author)
# used to advance upstream. Branch is `fixture`, never main/master.
# ---------------------------------------------------------------------------
mkfixture() { # mkfixture <name> -> echoes the work dir
  local n="$1" r="$TMP/$1-remote.git" w="$TMP/$1-work" a="$TMP/$1-author"
  git init -q --bare "$r"
  git clone -q "$r" "$w" 2>/dev/null
  git -C "$w" checkout -q -b fixture
  mkdir -p "$w/.claude/skills/demo"
  printf 'CLAUDE v1\n' > "$w/CLAUDE.md"
  printf 'demo skill v1\n' > "$w/.claude/skills/demo/SKILL.md"
  git -C "$w" add CLAUDE.md .claude/skills/demo/SKILL.md
  git -C "$w" "${GIT_ID[@]}" commit -qm c1
  git -C "$w" push -q origin fixture
  git -C "$w" branch -q --set-upstream-to=origin/fixture fixture
  git clone -q -b fixture "$r" "$a" 2>/dev/null
  echo "$w"
}

# Advance upstream by one commit. Pass the CLAUDE.md body; optionally create a
# brand-new skill file (the case that used to be misreported as FAILED).
advance() { # advance <name> <claude-body> [newfile-relpath]
  local n="$1" body="$2" newfile="${3:-}" a="$TMP/$1-author"
  printf '%s\n' "$body" > "$a/CLAUDE.md"
  printf '%s\n' "$body-skill" > "$a/.claude/skills/demo/SKILL.md"
  git -C "$a" add CLAUDE.md .claude/skills/demo/SKILL.md
  if [ -n "$newfile" ]; then
    mkdir -p "$a/$(dirname "$newfile")"
    printf 'brand new upstream file\n' > "$a/$newfile"
    git -C "$a" add "$newfile"
  fi
  git -C "$a" "${GIT_ID[@]}" commit -qm "advance $body"
  git -C "$a" push -q origin fixture
}

# Remove a path upstream. The path must already exist there, and the work clone
# must already have received it -- otherwise the case is vacuous (there is nothing
# on disk for the prune to act on), which is why case 9 asserts that first.
advance_delete() { # advance_delete <name> <relpath>
  local a="$TMP/$1-author"
  git -C "$a" rm -q "$2"
  git -C "$a" "${GIT_ID[@]}" commit -qm "delete $2"
  git -C "$a" push -q origin fixture
}

say() { printf '\n== %s\n' "$1"; }
vecho() { [ "$VERBOSE" = 1 ] && printf '     | %s\n' "$1"; return 0; }

# ---------------------------------------------------------------------------
say "1. refreshes a stale context file, and CREATES one that is new upstream"
# Defect: `git hash-object` fatals on a path missing locally, and the empty
# result was classified FAILED -- so a newly-added upstream skill was never
# created in a stale clone, and the report called it an error.
W=$(mkfixture t1)
advance t1 "CLAUDE v2" ".claude/skills/newskill/SKILL.md"
git -C "$W" fetch -q origin fixture
pre "CLAUDE.md is stale" "$(blob "$W" CLAUDE.md)" "$(git -C "$W" rev-parse HEAD:CLAUDE.md)" && {
  pre "new file absent locally" "$([ -e "$W/.claude/skills/newskill/SKILL.md" ] && echo yes || echo no)" "no" && {
    OUT=$(run_hook "$W"); vecho "$OUT"
    check "stale CLAUDE.md refreshed to upstream" "$(blob "$W" CLAUDE.md)" "$(upblob "$W" CLAUDE.md)"
    check "new upstream file created"             "$([ -e "$W/.claude/skills/newskill/SKILL.md" ] && echo yes || echo no)" "yes"
    check "new file has upstream content"         "$(blob "$W" .claude/skills/newskill/SKILL.md)" "$(upblob "$W" .claude/skills/newskill/SKILL.md)"
    check "no FAILED bucket"                      "$(grep -c 'FAILED' <<<"$OUT")" "0"
  }
}

# ---------------------------------------------------------------------------
say "2. refreshes AGAIN after upstream moves a second time"
# 🔴 The self-blocking defect. `git checkout <ref> -- <path>` writes the INDEX
# too, so after one refresh the path differs from HEAD forever; a guard keyed on
# "differs from HEAD" then skipped it on every later run and the hook worked
# exactly once per file. An idempotence test CANNOT see this -- re-running with
# nothing new upstream is a no-op either way. Upstream must move between runs.
W=$(mkfixture t2)
advance t2 "CLAUDE v2"; git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null
V2=$(blob "$W" CLAUDE.md)
pre "first refresh landed v2" "$V2" "$(upblob "$W" CLAUDE.md)" && {
  pre "path now differs from HEAD (the trap)" "$(git -C "$W" diff --name-only HEAD -- CLAUDE.md)" "CLAUDE.md" && {
    advance t2 "CLAUDE v3"; git -C "$W" fetch -q origin fixture
    pre "v3 differs from v2" "$([ "$(upblob "$W" CLAUDE.md)" != "$V2" ] && echo yes || echo no)" "yes" && {
      OUT=$(run_hook "$W"); vecho "$OUT"
      check "second refresh landed v3" "$(blob "$W" CLAUDE.md)" "$(upblob "$W" CLAUDE.md)"
    }
  }
}

# ---------------------------------------------------------------------------
say "3. NEVER clobbers unique local work"
W=$(mkfixture t3)
advance t3 "CLAUDE v2"; git -C "$W" fetch -q origin fixture
printf 'UNIQUE-LOCAL-SENTINEL-do-not-destroy\n' >> "$W/.claude/skills/demo/SKILL.md"
BEFORE=$(blob "$W" .claude/skills/demo/SKILL.md)
pre "sentinel content is not upstream's" "$([ "$BEFORE" != "$(upblob "$W" .claude/skills/demo/SKILL.md)" ] && echo yes || echo no)" "yes" && {
  OUT=$(run_hook "$W"); vecho "$OUT"
  check "local edit preserved"      "$(blob "$W" .claude/skills/demo/SKILL.md)" "$BEFORE"
  check "sentinel text intact"      "$(grep -c 'UNIQUE-LOCAL-SENTINEL' "$W/.claude/skills/demo/SKILL.md")" "1"
  check "reported as skipped"       "$(grep -c 'SKIPPED' <<<"$OUT")" "1"
  check "CLAUDE.md still refreshed" "$(blob "$W" CLAUDE.md)" "$(upblob "$W" CLAUDE.md)"
}

# ---------------------------------------------------------------------------
say "4. a locally DELETED context file is restored"
# Flip side of the protection rule: absent locally == nothing to protect. Worth
# pinning because it surprises people -- deleting a skill only in the primary
# clone does nothing durable.
W=$(mkfixture t4)
advance t4 "CLAUDE v2"; git -C "$W" fetch -q origin fixture
rm -f "$W/.claude/skills/demo/SKILL.md"
pre "file is gone" "$([ -e "$W/.claude/skills/demo/SKILL.md" ] && echo yes || echo no)" "no" && {
  OUT=$(run_hook "$W"); vecho "$OUT"
  check "deleted file restored" "$([ -e "$W/.claude/skills/demo/SKILL.md" ] && echo yes || echo no)" "yes"
}

# ---------------------------------------------------------------------------
say "5. BASE_CLONE_NO_REFRESH=1 writes nothing (with a positive control)"
# A "no write" result is meaningless unless the same fixture DOES write without
# the flag -- otherwise a hook wired to nothing passes this case.
W=$(mkfixture t5)
advance t5 "CLAUDE v2"; git -C "$W" fetch -q origin fixture
B0=$(blob "$W" CLAUDE.md)
OUT=$(run_hook "$W" BASE_CLONE_NO_REFRESH=1); vecho "$OUT"
check "opt-out: file untouched"    "$(blob "$W" CLAUDE.md)" "$B0"
check "opt-out: reason is accurate" "$(grep -c 'opted out' <<<"$OUT")" "1"
check "opt-out: no false 'local edits' claim" "$(grep -c 'local edits' <<<"$OUT")" "0"
OUT=$(run_hook "$W")
check "POSITIVE CONTROL: writes without the flag" "$(blob "$W" CLAUDE.md)" "$(upblob "$W" CLAUDE.md)"

# ---------------------------------------------------------------------------
say "6. stays silent where it must"
W=$(mkfixture t6)
check "current clone -> no output" "$(run_hook "$W" | wc -c)" "0"
git -C "$W" worktree add -q --detach "$TMP/t6-wt" HEAD 2>/dev/null
check "linked worktree -> no output" "$(run_hook "$TMP/t6-wt" | wc -c)" "0"
mkdir -p "$TMP/not-a-repo"
check "non-repo -> no output" "$(run_hook "$TMP/not-a-repo" | wc -c)" "0"

# ---------------------------------------------------------------------------
say "7. concurrent session starts do not corrupt or wedge the clone"
# Per-file checkout took .git/index.lock once per file, so two simultaneous runs
# collided repeatedly (measured FAILED 12/12; a per-file retry barely helped --
# the contention is sustained, not a narrow window). The refresh is now ONE
# batched checkout.
#
# 🔴 NO ASSERTION HERE MAY DEPEND ON WHO WON THE LOCK, and two of them used to.
# This case asserted `CLAUDE.md ended correct` and `new file ended present`, which
# are PROGRESS claims: they hold only if at least one of the two runs completed a
# checkout, so under sustained contention they go red for a reason that is not a
# defect. This suite gates a hook that runs at every session start on this host, and
# a flake in it trains people to re-run rather than read.
#
# What replaces them is strictly stronger, not weaker:
#   * INTEGRITY, which no lock winner can change: every path holds EITHER its
#     pre-run blob OR upstream's, never a third value. The old assertion conflated
#     integrity with progress and therefore pinned neither -- a TORN write landing a
#     partial blob would have satisfied it if upstream happened to win.
#   * PROGRESS, moved to a SERIAL run afterwards, where it is deterministic. That
#     also pins a claim the report makes and nothing tested: contention "is
#     transient and self-heals on the next session".
# Case 7b then drives the contention deterministically instead of hoping for it.
#
# ⚠️ MEASURED, so the removal is not speculative: the genuine race does NOT
# reproduce at this fixture's scale -- 220 trials of this exact pattern at
# parallelism 2, 4, 8, 16 and 32, loadavg 26, produced 0 red progress assertions and
# 0 leftover locks. The mechanism is nonetheless real, and 7b reaches it by holding
# the lock rather than by racing for it.
W=$(mkfixture t7)
advance t7 "CLAUDE v2" ".claude/skills/another/SKILL.md"
git -C "$W" fetch -q origin fixture
C_PRE=$(blob "$W" CLAUDE.md)
( run_hook "$W" >/dev/null 2>&1 ) & ( run_hook "$W" >/dev/null 2>&1 ) & wait
check "no index.lock left behind" "$([ -e "$W/.git/index.lock" ] && echo yes || echo no)" "no"
check "repo still usable"         "$(git -C "$W" status --short >/dev/null 2>&1 && echo yes || echo no)" "yes"
# Winner-independent: old blob or upstream's blob, nothing else.
C_POST=$(blob "$W" CLAUDE.md)
check "CLAUDE.md is un-torn (pre-run blob or upstream's, never a third)" \
      "$([ "$C_POST" = "$C_PRE" ] || [ "$C_POST" = "$(upblob "$W" CLAUDE.md)" ] && echo yes || echo no)" "yes"
check "the new file is absent or byte-exact (never partial)" \
      "$([ ! -e "$W/.claude/skills/another/SKILL.md" ] \
         || [ "$(blob "$W" .claude/skills/another/SKILL.md)" = "$(upblob "$W" .claude/skills/another/SKILL.md)" ] \
         && echo yes || echo no)" "yes"
# 🔴 PROGRESS, asserted where it is deterministic: one more run with nothing to
# contend against. Whatever the pair did or did not achieve, the next session start
# must converge -- which is exactly what the FAILED bucket's report promises.
run_hook "$W" >/dev/null 2>&1
check "a SERIAL run afterwards converges: CLAUDE.md correct" \
      "$(blob "$W" CLAUDE.md)" "$(upblob "$W" CLAUDE.md)"
check "a SERIAL run afterwards converges: new file present" \
      "$([ -e "$W/.claude/skills/another/SKILL.md" ] && echo yes || echo no)" "yes"

say "7b. index.lock held by someone else: refuse honestly, corrupt nothing, recover next run"
# 🔴 The contention path, DETERMINISTIC. Case 7 above cannot reach it -- 220 trials
# of genuine concurrency reached it zero times -- so the behaviour under a lock it
# cannot take was, until this case, asserted by nothing at all. Holding the lock for
# the whole run makes every attempt fail (batch, retry, per-path, per-path retry),
# which is the "both runs lost" end state without racing anything.
#
# Three claims, and the honest-report one is the load-bearing one: a hook that could
# not write must SAY so. Reporting a refresh it did not perform is the failure this
# whole file exists to prevent, one level up.
W=$(mkfixture t7b)
advance t7b "CLAUDE v2" ".claude/skills/another/SKILL.md"
git -C "$W" fetch -q origin fixture
C_PRE=$(blob "$W" CLAUDE.md)
: > "$W/.git/index.lock"
pre "the lock is held before the run" \
    "$([ -e "$W/.git/index.lock" ] && echo yes || echo no)" "yes" && {
  OUT=$(run_hook "$W"); vecho "$OUT"
  check "nothing was written while the lock was held" "$(blob "$W" CLAUDE.md)" "$C_PRE"
  # 3 is the approved set this fixture produces: a stale CLAUDE.md, a stale
  # demo/SKILL.md, and the new another/SKILL.md. The COUNT is asserted rather than
  # mere presence of the word so that a hook silently narrowing what it attempts
  # cannot pass by failing on fewer paths.
  check "the report says FAILED 3 rather than claiming a refresh" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'FAILED 3')" "1"
  check "it does NOT claim to have refreshed anything" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'refreshed')" "0"
  # 🔴 The hook must never remove a lock it did not take -- that is somebody else's
  # git operation in flight, and deleting it is how a concurrent index gets corrupted.
  check "someone else's index.lock is left alone" \
        "$([ -e "$W/.git/index.lock" ] && echo yes || echo no)" "yes"
  rm -f "$W/.git/index.lock"
  # 🔴 POSITIVE CONTROL and the recovery claim in one: with the lock released the very
  # next run converges. Without this, "nothing was written" is the same observation
  # whether the lock stopped the write or the hook is simply inert.
  run_hook "$W" >/dev/null 2>&1
  check "POSITIVE CONTROL: the next run converges once the lock is released" \
        "$(blob "$W" CLAUDE.md)" "$(upblob "$W" CLAUDE.md)"
}

# ---------------------------------------------------------------------------
say "8. output is valid JSON with the SessionStart contract"
W=$(mkfixture t8)
advance t8 "CLAUDE v2"; git -C "$W" fetch -q origin fixture
OUT=$(run_hook "$W")
check "parses as JSON"        "$(jq -e . <<<"$OUT" >/dev/null 2>&1 && echo yes || echo no)" "yes"
check "carries systemMessage" "$(jq -r 'has("systemMessage")' <<<"$OUT" 2>/dev/null)" "true"
check "hookEventName correct" "$(jq -r '.hookSpecificOutput.hookEventName' <<<"$OUT" 2>/dev/null)" "SessionStart"

# ---------------------------------------------------------------------------
say "9. a path DELETED upstream is pruned here, not reported FAILED forever"
# Defect (2026-08-24): `checkout $UP -- $p` cannot deliver a deletion -- the
# pathspec matches nothing in $UP -- so the batch AND the per-file retry both
# failed and the path landed in FAILED on every session start, forever, while the
# file stayed on disk and kept loading into agent context. Exact mirror of the
# ADDED-path bug in case 1, and the same wrong bucket.
# Real instance: .claude/skills/check-clickup-addressed/ was removed from
# talos-infra (it moved to devrc); the clone went on serving a 437-line retired
# SKILL.md against the 562-line canonical one.
W=$(mkfixture t9)
advance t9 "CLAUDE v2" ".claude/skills/doomed/SKILL.md"
git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null 2>&1                      # phase 1: the clone receives it
pre "doomed skill exists before the delete" \
    "$([ -e "$W/.claude/skills/doomed/SKILL.md" ] && echo yes || echo no)" "yes" && {
  advance_delete t9 ".claude/skills/doomed/SKILL.md"
  git -C "$W" fetch -q origin fixture
  OUT=$(run_hook "$W")
  vecho "$OUT"
  check "deleted-upstream file is GONE locally" \
        "$([ -e "$W/.claude/skills/doomed/SKILL.md" ] && echo yes || echo no)" "no"
  check "reported as pruned, NOT failed" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'pruned 1 deleted upstream')" "1"
  check "no FAILED bucket for it" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'FAILED')" "0"
  # The emptied skill dir must not be left behind looking installed.
  check "emptied skill dir cleaned up" \
        "$([ -d "$W/.claude/skills/doomed" ] && echo yes || echo no)" "no"
  # ...but a directory that still holds content must survive. `rmdir` refuses a
  # non-empty dir, which is exactly why the cleanup uses it rather than `rm -r`.
  check "sibling skill dir untouched" \
        "$([ -e "$W/.claude/skills/demo/SKILL.md" ] && echo yes || echo no)" "yes"
}

say "9b. a deleted path holding UNIQUE LOCAL work is NOT pruned"
# The prune must inherit the same protection as an overwrite. A local edit whose
# blob appears NOWHERE in upstream history is unique human work; deleting it
# would be strictly worse than the bug being fixed.
#
# ⚠️ HONEST LABEL: this is NOT regression coverage for the FAILED-bucket defect --
# it passes on pre-change code too, because there the file survived by never being
# pruned at all. It guards the NEW capability instead, and it is a killing guard
# for it: mutating `if [ "$known" = no ]` to `[ "$known" = no ] && [ "$deleted" = no ]`
# deletes the file and takes all three assertions red. Reachable, and it fires for
# its own reason.
W=$(mkfixture t9b)
advance t9b "CLAUDE v2" ".claude/skills/precious/SKILL.md"
git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null 2>&1
printf 'HAND-WRITTEN LOCAL NOTES NOBODY ELSE HAS\n' > "$W/.claude/skills/precious/SKILL.md"
pre "local edit is genuinely unrecoverable" \
    "$(git -C "$W" rev-list --all --objects 2>/dev/null | grep -c "$(blob "$W" .claude/skills/precious/SKILL.md)")" "0" && {
  advance_delete t9b ".claude/skills/precious/SKILL.md"
  git -C "$W" fetch -q origin fixture
  OUT=$(run_hook "$W")
  vecho "$OUT"
  check "unique local work SURVIVES the prune" \
        "$([ -e "$W/.claude/skills/precious/SKILL.md" ] && echo yes || echo no)" "yes"
  check "content is still the local edit" \
        "$(cat "$W/.claude/skills/precious/SKILL.md")" "HAND-WRITTEN LOCAL NOTES NOBODY ELSE HAS"
  check "reported as SKIPPED, not pruned" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'SKIPPED')" "1"
}

say "9c. BASE_CLONE_NO_REFRESH=1 does not prune either (with a positive control)"
# Report-only must mean report-only for deletions too, or the opt-out silently
# stops protecting the one operation that cannot be undone by a re-run.
#
# ⚠️ HONEST LABEL: the two opt-out assertions are INVARIANT guards -- they pass on
# pre-change code, where nothing was pruned under any flag. The POSITIVE CONTROL at
# the end is the only real regression assertion in this case, and it is why the
# other two are not vacuous: without it, "the file is still there" is the same
# observation whether the opt-out works or the prune is simply broken.
W=$(mkfixture t9c)
advance t9c "CLAUDE v2" ".claude/skills/optout/SKILL.md"
git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null 2>&1
advance_delete t9c ".claude/skills/optout/SKILL.md"
git -C "$W" fetch -q origin fixture
OUT=$(run_hook "$W" BASE_CLONE_NO_REFRESH=1)
vecho "$OUT"
check "opt-out: file untouched" \
      "$([ -e "$W/.claude/skills/optout/SKILL.md" ] && echo yes || echo no)" "yes"
check "opt-out: no false 'pruned' claim" \
      "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'pruned')" "0"
# 🔴 Without this control the case above passes even if the prune never worked at
# all -- "the file is still there" is the SAME observation as a broken feature.
OUT=$(run_hook "$W")
check "POSITIVE CONTROL: prunes without the flag" \
      "$([ -e "$W/.claude/skills/optout/SKILL.md" ] && echo yes || echo no)" "no"

say "9d. a path the LOCAL branch ADDED (never upstream) is NEVER pruned"
# 🔴 The defect this case exists for shipped in the first cut of the prune and was
# caught in review. `cat-file -e "$UP:$p"` answers "is this ABSENT upstream", not
# "was it DELETED upstream" -- and those differ for a path upstream never had.
# Combined with the `cur = HEAD:$p` recoverability shortcut (correct for a refresh,
# where matching HEAD means untouched-locally; WRONG for a prune, where it means
# only "committed here"), a skill authored on this branch and not yet pushed was
# deleted -- and re-deleted every session, while the report said it was "GONE
# upstream on purpose ... find where it moved rather than restoring it".
#
# 🔴 EVERY other case 9 fixture reaches the absent-upstream state via
# `advance_delete`, so all of them are indistinguishable to `cat-file -e` and the
# suite was STRUCTURALLY BLIND to this. The fixture below is the only one that
# creates the path locally and never pushes it, which is what makes it able to see.
W=$(mkfixture t9d)
mkdir -p "$W/.claude/skills/homegrown"
printf 'authored here, never pushed\n' > "$W/.claude/skills/homegrown/SKILL.md"
git -C "$W" add .claude/skills/homegrown/SKILL.md
git -C "$W" "${GIT_ID[@]}" commit -qm "local-only skill"
advance t9d "CLAUDE v2"        # upstream moves for an unrelated reason
git -C "$W" fetch -q origin fixture
pre "local-only skill is absent upstream" \
    "$(git -C "$W" cat-file -e "origin/fixture:.claude/skills/homegrown/SKILL.md" 2>/dev/null && echo yes || echo no)" "no" && {
  OUT=$(run_hook "$W")
  vecho "$OUT"
  check "locally-authored skill SURVIVES" \
        "$([ -e "$W/.claude/skills/homegrown/SKILL.md" ] && echo yes || echo no)" "yes"
  check "content untouched" \
        "$(cat "$W/.claude/skills/homegrown/SKILL.md" 2>/dev/null)" "authored here, never pushed"
  check "not reported as pruned" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'pruned')" "0"
  # ...and it must stay gone-proof across repeated session starts, which is how the
  # original defect presented: restoring the file just got it deleted again.
  run_hook "$W" >/dev/null 2>&1; run_hook "$W" >/dev/null 2>&1
  check "still present after 3 session starts" \
        "$([ -e "$W/.claude/skills/homegrown/SKILL.md" ] && echo yes || echo no)" "yes"
}

say "9e. the rmdir climb stops at the REFRESH_PATHS root"
# Unbounded, the climb walked out of its own scope: with the only skill deleted
# upstream it removed `.claude/skills` AND `.claude`. Empty and harmless, but
# `.claude` is not a path this hook may touch.
#
# ⚠️ FIXTURE TRAP, hit while writing this: deleting the `demo` skill only in the
# WORK TREE does not empty `.claude/skills` -- `demo` is still upstream, so the same
# hook run REFRESHES it straight back, the directory stays non-empty, the climb
# stops one level early and the case PASSES on the broken hook for the wrong
# reason. Every skill must be deleted UPSTREAM, and the precondition below asserts
# the directory really is empty before the two root checks mean anything.
W=$(mkfixture t9e)
advance t9e "CLAUDE v2" ".claude/skills/solo/SKILL.md"
git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null 2>&1
advance_delete t9e ".claude/skills/solo/SKILL.md"
advance_delete t9e ".claude/skills/demo/SKILL.md"
git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null 2>&1
pre "skills tree really is empty of files" \
    "$(find "$W/.claude/skills" -type f 2>/dev/null | wc -l)" "0" && {
  check "emptied skill dir removed"    "$([ -d "$W/.claude/skills/solo" ] && echo yes || echo no)" "no"
  check ".claude/skills root survives" "$([ -d "$W/.claude/skills" ] && echo yes || echo no)" "yes"
  check ".claude survives"             "$([ -d "$W/.claude" ] && echo yes || echo no)" "yes"
}

say "9g. an UNTRACKED sibling is never deleted, and it stops the directory cleanup"
# The prune removes tracked files git named; it must never touch untracked local
# content (build cache, scratch notes). `rmdir` is what enforces that -- it refuses
# a non-empty directory -- and this is the case that PINS the choice.
#
# 🔴 This case exists because bounding the climb at the REFRESH_PATHS root (9e)
# SILENTLY REMOVED the coverage that used to kill `rmdir` -> `rm -rf`: with the
# bound in place that mutant can no longer reach a sibling SKILL dir, so it
# survived a fully green suite. A fix round taking a mutant off the board is
# exactly the regression an audit round is for. The distinguishing case has to sit
# INSIDE the bound -- an untracked file in the pruned skill's own directory.
#
# It is also the real-world shape: on the clone that motivated this change the
# retired skill dir held .pytest_cache/ and __pycache__/, which correctly survived.
W=$(mkfixture t9g)
advance t9g "CLAUDE v2" ".claude/skills/withjunk/SKILL.md"
git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null 2>&1
printf 'local scratch, never committed\n' > "$W/.claude/skills/withjunk/NOTES.local"
advance_delete t9g ".claude/skills/withjunk/SKILL.md"
git -C "$W" fetch -q origin fixture
pre "untracked sibling is genuinely untracked" \
    "$(git -C "$W" status --porcelain --untracked-files=all -- .claude/skills/withjunk/NOTES.local | cut -c1-2)" "??" && {
  OUT=$(run_hook "$W")
  vecho "$OUT"
  check "tracked file pruned"          "$([ -e "$W/.claude/skills/withjunk/SKILL.md" ] && echo yes || echo no)" "no"
  check "UNTRACKED sibling SURVIVES"   "$([ -e "$W/.claude/skills/withjunk/NOTES.local" ] && echo yes || echo no)" "yes"
  check "its content is intact"        "$(cat "$W/.claude/skills/withjunk/NOTES.local" 2>/dev/null)" "local scratch, never committed"
  check "non-empty dir NOT removed"    "$([ -d "$W/.claude/skills/withjunk" ] && echo yes || echo no)" "yes"
}

say "9f. a filename containing '..' is not misclassified as an escape attempt"
# `*..*` as a substring match rejects an ordinary `v1..v2.md`, sending it to the
# FAILED bucket forever -- the exact misclassification this change exists to fix.
# Only a `..` COMPONENT can escape.
W=$(mkfixture t9f)
advance t9f "CLAUDE v2" ".claude/skills/dotty/v1..v2.md"
git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null 2>&1
pre "dotted file arrived" "$([ -e "$W/.claude/skills/dotty/v1..v2.md" ] && echo yes || echo no)" "yes" && {
  advance_delete t9f ".claude/skills/dotty/v1..v2.md"
  git -C "$W" fetch -q origin fixture
  OUT=$(run_hook "$W")
  vecho "$OUT"
  check "dotted filename pruned, not FAILED" \
        "$([ -e "$W/.claude/skills/dotty/v1..v2.md" ] && echo yes || echo no)" "no"
  check "no FAILED bucket for a dotted name" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'FAILED')" "0"
}

# ---------------------------------------------------------------------------
# Fixture: the AGENTS.md convention. CLAUDE.md is a STUB whose entire payload is
# `@AGENTS.md`; the substance is AGENTS.md. Measured on a cairn clone: CLAUDE.md
# 267 bytes, AGENTS.md 31,330 bytes.
#
# 🔴 The stub is what makes this shape able to SEE the defect. Every other fixture
# here advances CLAUDE.md, so with AGENTS.md absent from REFRESH_PATHS the hook
# still refreshed something and still emitted a report -- structurally blind. Here
# the stub is left byte-identical on purpose, so a hook that syncs only CLAUDE.md
# has literally nothing to do while 31 KB of authoritative instructions stay stale.
mkfixture_agents() { # mkfixture_agents <name> -> echoes the work dir
  local r="$TMP/$1-remote.git" w="$TMP/$1-work" a="$TMP/$1-author"
  git init -q --bare "$r"
  git clone -q "$r" "$w" 2>/dev/null
  git -C "$w" checkout -q -b fixture
  mkdir -p "$w/.claude/skills/demo"
  printf '@AGENTS.md\n' > "$w/CLAUDE.md"
  printf 'AGENTS v1: the rules that actually load\n' > "$w/AGENTS.md"
  printf 'demo skill v1\n' > "$w/.claude/skills/demo/SKILL.md"
  git -C "$w" add CLAUDE.md AGENTS.md .claude/skills/demo/SKILL.md
  git -C "$w" "${GIT_ID[@]}" commit -qm c1
  git -C "$w" push -q origin fixture
  git -C "$w" branch -q --set-upstream-to=origin/fixture fixture
  git clone -q -b fixture "$r" "$a" 2>/dev/null
  echo "$w"
}

# Advance AGENTS.md ONLY. CLAUDE.md is deliberately not touched -- a stub does not
# change, and a fixture that moved it too would pass on the broken hook.
advance_agents() { # advance_agents <name> <agents-body>
  local a="$TMP/$1-author"
  printf '%s\n' "$2" > "$a/AGENTS.md"
  git -C "$a" add AGENTS.md
  git -C "$a" "${GIT_ID[@]}" commit -qm "advance agents"
  git -C "$a" push -q origin fixture
}

say "10. AGENTS.md is refreshed in a repo where CLAUDE.md is only a stub"
# Defect: REFRESH_PATHS was (CLAUDE.md .claude/skills), so in an AGENTS.md repo the
# hook refreshed the one file that never changes and never refreshed the one that
# loads. The exact failure the hook exists to prevent, walked around by a filename.
# RED at the parent of the REFRESH_PATHS change, GREEN with it.
W=$(mkfixture_agents t10)
advance_agents t10 "AGENTS v2: the substance moved and the stub did not"
git -C "$W" fetch -q origin fixture
pre "AGENTS.md is stale" \
    "$([ "$(blob "$W" AGENTS.md)" != "$(upblob "$W" AGENTS.md)" ] && echo yes || echo no)" "yes" && {
  # 🔴 The precondition that makes the case DISCRIMINATING rather than merely
  # passing: the stub is already current, so CLAUDE.md alone gives the hook nothing.
  pre "the CLAUDE.md stub is already current (so it cannot carry the case)" \
      "$(blob "$W" CLAUDE.md)" "$(upblob "$W" CLAUDE.md)" && {
    OUT=$(run_hook "$W"); vecho "$OUT"
    check "stale AGENTS.md refreshed to upstream" \
          "$(blob "$W" AGENTS.md)" "$(upblob "$W" AGENTS.md)"
    check "content is upstream's, read from disk" \
          "$(cat "$W/AGENTS.md")" "AGENTS v2: the substance moved and the stub did not"
    check "report names AGENTS.md as refreshed" \
          "$(jq -r '.hookSpecificOutput.additionalContext' <<<"$OUT" 2>/dev/null | grep -c '^    AGENTS.md$')" "1"
    # The scope sentence is DERIVED from REFRESH_PATHS rather than restated. A
    # hardcoded "CLAUDE.md and .claude/skills/**" fails this -- which is the point:
    # a report that under-states what it synced is a stale claim in the same class
    # as the bug above.
    check "scope sentence names AGENTS.md (derived, not restated)" \
          "$(jq -r '.hookSpecificOutput.additionalContext' <<<"$OUT" 2>/dev/null | grep -c 'Only .*AGENTS\.md.* are synced')" "1"
    check "no FAILED bucket" "$(grep -c 'FAILED' <<<"$OUT")" "0"
  }
}

say "10b. pruning a TOP-LEVEL entry does not climb out of the repo"
# 🔴 The constraint the REFRESH_PATHS comment states, pinned. The rmdir climb is
# bounded by EXACT string compare against the array, so a new entry's spelling is
# load-bearing. For a top-level FILE `dirname` yields `.` and the climb's own
# `[ "$d" != "." ]` condition ends it before the first rmdir -- the entry is never
# consulted as a bound. This asserts that shape holds rather than reasoning about
# it, and it is the assertion that would catch a future nested entry spelled wrong.
#
# ⚠️ HONEST LABEL, measured: against the pre-change hook the first two assertions go
# RED (AGENTS.md was in no REFRESH_PATHS entry, so nothing was pruned) and the last
# three are INVARIANT guards -- they pass there too, because a hook that prunes
# nothing also climbs nowhere. They are not vacuous: the first two are what prove
# the prune path is REACHED, which is the only thing that makes "the root survived"
# an observation about the climb rather than about a feature that never ran.
W=$(mkfixture_agents t10b)
advance_delete t10b AGENTS.md
git -C "$W" fetch -q origin fixture
pre "AGENTS.md present here, absent upstream" \
    "$([ -e "$W/AGENTS.md" ] && echo yes || echo no)=$(git -C "$W" cat-file -e origin/fixture:AGENTS.md 2>/dev/null && echo yes || echo no)" \
    "yes=no" && {
  OUT=$(run_hook "$W"); vecho "$OUT"
  check "deleted-upstream AGENTS.md is GONE locally" \
        "$([ -e "$W/AGENTS.md" ] && echo yes || echo no)" "no"
  check "reported as pruned, NOT failed" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'pruned 1 deleted upstream')" "1"
  check "the repo root still exists" \
        "$([ -d "$W" ] && echo yes || echo no)" "yes"
  check "the .git dir was not climbed into" \
        "$([ -d "$W/.git" ] && echo yes || echo no)" "yes"
  check "unrelated siblings untouched" \
        "$([ -e "$W/CLAUDE.md" ] && [ -e "$W/.claude/skills/demo/SKILL.md" ] && echo yes || echo no)" "yes"
}

# ---------------------------------------------------------------------------
# Fixture: a repo that tracks its own agent WIRING -- `.claude/settings.json` plus
# `.claude/hooks/*.py` -- beside the docs and the skills. That is the shape of a real
# repo in this fleet, and it is the shape REFRESH_PATHS missed: it carried
# `.claude/skills` and neither of the two paths that EXECUTE.
#
# 🔴 The docs are left byte-identical on purpose, for the reason case 10's stub is.
# Every fixture above advances CLAUDE.md, so a hook whose array lacks the wiring
# paths still had something to refresh and still emitted a populated report -- the
# suite was structurally blind. Here a hook that syncs only docs and skills has
# literally nothing to do while the guard that runs on every session start stays
# stale.
mkfixture_wiring() { # mkfixture_wiring <name> -> echoes the work dir
  local r="$TMP/$1-remote.git" w="$TMP/$1-work" a="$TMP/$1-author"
  git init -q --bare "$r"
  git clone -q "$r" "$w" 2>/dev/null
  git -C "$w" checkout -q -b fixture
  mkdir -p "$w/.claude/hooks" "$w/.claude/skills/demo"
  printf '@AGENTS.md\n' > "$w/CLAUDE.md"
  printf 'AGENTS v1\n' > "$w/AGENTS.md"
  printf '{"hooks":{"PreToolUse":[]},"v":1}\n' > "$w/.claude/settings.json"
  printf '# guard v1\nimport sys; sys.exit(0)\n' > "$w/.claude/hooks/guard.py"
  printf 'demo skill v1\n' > "$w/.claude/skills/demo/SKILL.md"
  git -C "$w" add CLAUDE.md AGENTS.md .claude/settings.json .claude/hooks/guard.py \
                  .claude/skills/demo/SKILL.md
  git -C "$w" "${GIT_ID[@]}" commit -qm c1
  git -C "$w" push -q origin fixture
  git -C "$w" branch -q --set-upstream-to=origin/fixture fixture
  git clone -q -b fixture "$r" "$a" 2>/dev/null
  echo "$w"
}

# Advance the WIRING only. The docs are deliberately not touched.
advance_wiring() { # advance_wiring <name> <tag> [newfile-relpath]
  local a="$TMP/$1-author" tag="$2" newfile="${3:-}"
  printf '{"hooks":{"PreToolUse":[]},"v":"%s"}\n' "$tag" > "$a/.claude/settings.json"
  printf '# guard %s\nimport sys; sys.exit(0)\n' "$tag" > "$a/.claude/hooks/guard.py"
  git -C "$a" add .claude/settings.json .claude/hooks/guard.py
  if [ -n "$newfile" ]; then
    mkdir -p "$a/$(dirname "$newfile")"
    printf 'brand new upstream file\n' > "$a/$newfile"
    git -C "$a" add "$newfile"
  fi
  git -C "$a" "${GIT_ID[@]}" commit -qm "advance wiring $tag"
  git -C "$a" push -q origin fixture
}

say "11. the tracked, EXECUTING wiring is refreshed: .claude/settings.json and .claude/hooks"
# Defect: REFRESH_PATHS was (CLAUDE.md AGENTS.md .claude/skills). A repo-local hook
# that silently fails to fire is indistinguishable from one that allows, so a stale
# `.claude/hooks/*.py` has no symptom at all -- strictly worse than a stale doc,
# which at least reads oddly. RED at the parent of the REFRESH_PATHS change, GREEN
# with it.
W=$(mkfixture_wiring t11)
advance_wiring t11 "v2" ".claude/hooks/newguard.py"
git -C "$W" fetch -q origin fixture
pre "settings.json is stale" \
    "$([ "$(blob "$W" .claude/settings.json)" != "$(upblob "$W" .claude/settings.json)" ] && echo yes || echo no)" "yes" && {
  pre "the hook script is stale" \
      "$([ "$(blob "$W" .claude/hooks/guard.py)" != "$(upblob "$W" .claude/hooks/guard.py)" ] && echo yes || echo no)" "yes" && {
    # 🔴 What makes the case DISCRIMINATING rather than merely passing: with the docs
    # current, an array holding only docs and skills gives the hook nothing to do.
    pre "the docs are already current (so they cannot carry the case)" \
        "$(blob "$W" CLAUDE.md)=$(blob "$W" AGENTS.md)" \
        "$(upblob "$W" CLAUDE.md)=$(upblob "$W" AGENTS.md)" && {
      OUT=$(run_hook "$W"); vecho "$OUT"
      check "stale settings.json refreshed to upstream" \
            "$(blob "$W" .claude/settings.json)" "$(upblob "$W" .claude/settings.json)"
      check "stale hook script refreshed to upstream" \
            "$(blob "$W" .claude/hooks/guard.py)" "$(upblob "$W" .claude/hooks/guard.py)"
      check "hook content is upstream's, read from disk" \
            "$(head -1 "$W/.claude/hooks/guard.py")" "# guard v2"
      check "a NEW upstream hook is created here" \
            "$([ -e "$W/.claude/hooks/newguard.py" ] && echo yes || echo no)" "yes"
      check "report names settings.json as refreshed" \
            "$(jq -r '.hookSpecificOutput.additionalContext' <<<"$OUT" 2>/dev/null | grep -c '^    \.claude/settings\.json$')" "1"
      check "report names the hook script as refreshed" \
            "$(jq -r '.hookSpecificOutput.additionalContext' <<<"$OUT" 2>/dev/null | grep -c '^    \.claude/hooks/guard\.py$')" "1"
      # The scope sentence is DERIVED from REFRESH_PATHS, so it cannot under-state
      # what the hook now touches. A report claiming a narrower scope than the array
      # is a stale claim in the same class as the bug itself.
      check "scope sentence names settings.json (derived, not restated)" \
            "$(jq -r '.hookSpecificOutput.additionalContext' <<<"$OUT" 2>/dev/null | grep -c 'Only .*\.claude/settings\.json.* are synced')" "1"
      check "scope sentence names .claude/hooks (derived, not restated)" \
            "$(jq -r '.hookSpecificOutput.additionalContext' <<<"$OUT" 2>/dev/null | grep -c 'Only .*\.claude/hooks.* are synced')" "1"
      check "no FAILED bucket" "$(grep -c 'FAILED' <<<"$OUT")" "0"
    }
  }
}

say "11b. a hook script DELETED upstream is pruned, and .claude/hooks is not climbed out of"
# Mirror of case 9 and 10b for the new entries. A retired guard left on disk keeps
# being REGISTERED by whatever settings.json says, so the prune matters more here
# than it does for a doc.
#
# `.claude/hooks` is a DIRECTORY entry, so `dirname .claude/hooks/doomed.py` is an
# EXACT member of the bound set and the climb stops before any rmdir -- the same
# shape as `.claude/skills` in 9e, which is why the emptied directory survives.
W=$(mkfixture_wiring t11b)
advance_wiring t11b "v2" ".claude/hooks/doomed.py"
git -C "$W" fetch -q origin fixture
run_hook "$W" >/dev/null 2>&1                      # phase 1: the clone receives it
pre "doomed hook exists before the delete" \
    "$([ -e "$W/.claude/hooks/doomed.py" ] && echo yes || echo no)" "yes" && {
  advance_delete t11b ".claude/hooks/doomed.py"
  advance_delete t11b ".claude/hooks/guard.py"
  git -C "$W" fetch -q origin fixture
  OUT=$(run_hook "$W")
  vecho "$OUT"
  check "deleted-upstream hook is GONE locally" \
        "$([ -e "$W/.claude/hooks/doomed.py" ] && echo yes || echo no)" "no"
  check "the second deleted hook is GONE too" \
        "$([ -e "$W/.claude/hooks/guard.py" ] && echo yes || echo no)" "no"
  check "reported as pruned, NOT failed" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'pruned 2 deleted upstream')" "1"
  check "no FAILED bucket for them" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'FAILED')" "0"
  # ⚠️ FIXTURE PRECONDITION, the 9e trap: ".claude/hooks survived" says something
  # about the BOUND only if the directory is genuinely empty. Non-empty, `rmdir`
  # refuses regardless and the case passes on an unbounded climb.
  pre "the hooks dir really is empty of files" \
      "$(find "$W/.claude/hooks" -type f 2>/dev/null | wc -l)" "0" && {
    check ".claude/hooks root survives an emptying prune" \
          "$([ -d "$W/.claude/hooks" ] && echo yes || echo no)" "yes"
    check ".claude survives" "$([ -d "$W/.claude" ] && echo yes || echo no)" "yes"
  }
  check "unrelated siblings untouched" \
        "$([ -e "$W/.claude/settings.json" ] && [ -e "$W/.claude/skills/demo/SKILL.md" ] && echo yes || echo no)" "yes"
}

# ---------------------------------------------------------------------------
# Fixture: `.claude/` holds ONE tracked file and nothing else. This is the only shape
# that can see the NESTED FILE hazard, and it needs its own fixture for exactly the
# reason 9e's does: with `hooks/` or `skills/` also present, `rmdir` refuses
# `.claude` whatever the bound says, and the case passes on a broken climb.
mkfixture_settings_only() { # mkfixture_settings_only <name> -> echoes the work dir
  local r="$TMP/$1-remote.git" w="$TMP/$1-work" a="$TMP/$1-author"
  git init -q --bare "$r"
  git clone -q "$r" "$w" 2>/dev/null
  git -C "$w" checkout -q -b fixture
  mkdir -p "$w/.claude"
  printf 'CLAUDE v1\n' > "$w/CLAUDE.md"
  printf '{"hooks":{},"v":1}\n' > "$w/.claude/settings.json"
  git -C "$w" add CLAUDE.md .claude/settings.json
  git -C "$w" "${GIT_ID[@]}" commit -qm c1
  git -C "$w" push -q origin fixture
  git -C "$w" branch -q --set-upstream-to=origin/fixture fixture
  git clone -q -b fixture "$r" "$a" 2>/dev/null
  echo "$w"
}

say "11c. pruning the NESTED FILE entry does not remove .claude itself"
# 🔴 THE CASE THE NEW ENTRY'S SHAPE MAKES NECESSARY, and it pins a MEASURED escape
# rather than a cautionary one. The rmdir climb is bounded by EXACT string compare.
# For a DIRECTORY entry the array alone is enough -- `dirname
# .claude/skills/x/SKILL.md` climbs to `.claude/skills`, an exact member. For a FILE
# entry one level down it is NOT: `dirname .claude/settings.json` -> `.claude`, which
# is no entry, so with the array as the only bound the climb rmdirs `.claude`.
# Measured on this fixture against exactly that hook: `.claude` REMOVED -- the escape
# 9e exists for, re-introduced one level down. CLIMB_BOUNDS therefore carries each
# entry's PARENT as well, DERIVED from the array rather than hand-listed.
#
# ⚠️ HONEST LABEL, measured at three points rather than two, because the two halves
# of this case go red against DIFFERENT hooks and neither ref alone shows both:
#   * against the PRE-CHANGE array, the two prune assertions go RED (the path is in no
#     entry, so the diff never names it) and `.claude SURVIVES` is an INVARIANT guard
#     -- it passes there, because a hook that prunes nothing also climbs nowhere.
#   * against the array WIDENED but the climb still bounded on REFRESH_PATHS alone,
#     the prune assertions pass and `.claude SURVIVES` is the ONLY failing assertion
#     in the whole suite: 82 PASS / 1 FAIL. That is what makes it a killing guard for
#     the derived bound, and why the two halves are asserted separately rather than as
#     one state.
W=$(mkfixture_settings_only t11c)
advance_delete t11c .claude/settings.json
git -C "$W" fetch -q origin fixture
pre "settings.json present here, absent upstream" \
    "$([ -e "$W/.claude/settings.json" ] && echo yes || echo no)=$(git -C "$W" cat-file -e origin/fixture:.claude/settings.json 2>/dev/null && echo yes || echo no)" \
    "yes=no" && {
  pre ".claude holds nothing else (so rmdir cannot be refused for the wrong reason)" \
      "$(find "$W/.claude" -mindepth 1 ! -name settings.json | wc -l)" "0" && {
    OUT=$(run_hook "$W"); vecho "$OUT"
    check "deleted-upstream settings.json is GONE locally" \
          "$([ -e "$W/.claude/settings.json" ] && echo yes || echo no)" "no"
    check "reported as pruned, NOT failed" \
          "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'pruned 1 deleted upstream')" "1"
    check ".claude SURVIVES an emptying prune of a nested FILE entry" \
          "$([ -d "$W/.claude" ] && echo yes || echo no)" "yes"
    check "the repo root and .git were not climbed into" \
          "$([ -d "$W/.git" ] && [ -e "$W/CLAUDE.md" ] && echo yes || echo no)" "yes"
  }
}

say "11d. a locally-modified settings.json is SKIPPED, never clobbered"
# 🔴 The protection property, asserted for the NEW entries rather than inherited by
# assumption. Clobbering somebody's local hook wiring is the worst thing this change
# could introduce: unlike a doc the file EXECUTES, and a silently reverted PreToolUse
# entry is a guard that stops firing with no symptom.
#
# The hook's test is RECOVERABILITY, not dirtiness -- does the working copy's blob
# already exist in $UP's recent history FOR THIS PATH. A hand-edited settings.json
# exists nowhere upstream, so it must land in SKIPPED.
#
# The refreshed hook script in the SAME run is the POSITIVE CONTROL: without it,
# "settings.json still holds my edit" is the same observation whether the protection
# works or the path was never in the array at all.
#
# ⚠️ HONEST LABEL, measured against the pre-change array: the two PRESERVED assertions
# are INVARIANT guards there (the file survived by never being considered), and the
# three that go RED are the two report assertions and the positive control. So the
# protection property is pinned by the REPORT naming the skip, not by the file's
# survival -- survival alone cannot tell a working guard from an absent entry.
W=$(mkfixture_wiring t11d)
advance_wiring t11d "v2"
git -C "$W" fetch -q origin fixture
printf '{"hooks":{"PreToolUse":[{"LOCAL-ONLY-WIRING-do-not-destroy":1}]}}\n' > "$W/.claude/settings.json"
LOCAL=$(blob "$W" .claude/settings.json)
pre "the local edit is genuinely unrecoverable from history" \
    "$(git -C "$W" rev-list --all --objects 2>/dev/null | grep -c "$LOCAL")" "0" && {
  OUT=$(run_hook "$W"); vecho "$OUT"
  check "locally-edited settings.json PRESERVED" \
        "$(blob "$W" .claude/settings.json)" "$LOCAL"
  check "its wiring text is intact" \
        "$(grep -c 'LOCAL-ONLY-WIRING-do-not-destroy' "$W/.claude/settings.json")" "1"
  check "reported as SKIPPED, naming the path" \
        "$(jq -r '.hookSpecificOutput.additionalContext' <<<"$OUT" 2>/dev/null | grep -c '^    \.claude/settings\.json$')" "1"
  check "the skip is reported under SKIPPED (local edits)" \
        "$(jq -r '.systemMessage' <<<"$OUT" 2>/dev/null | grep -c 'SKIPPED 1 (local edits)')" "1"
  # 🔴 POSITIVE CONTROL: the clean sibling in the SAME run was refreshed, so the
  # protection above is a claim about the guard and not about a hook that did nothing.
  check "POSITIVE CONTROL: the clean hook script WAS refreshed" \
        "$(blob "$W" .claude/hooks/guard.py)" "$(upblob "$W" .claude/hooks/guard.py)"
}

_SUMMARY_PRINTED=1
printf '\n=====================================\n'
printf 'PASS %d   FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
