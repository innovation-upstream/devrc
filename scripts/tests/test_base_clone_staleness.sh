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
#   sed 's/rev-list -n 100/rev-list -n 0/' scripts/claude-hooks/base-clone-staleness.sh > /tmp/m.sh
#   HOOK=/tmp/m.sh bash scripts/tests/test_base_clone_staleness.sh   # must report FAIL 5
#
# ⚠️ That count was FAIL 1 until case 9 was added (2026-08-24). Breaking the
# recoverability scan also strands the prune cases, which depend on phase 1 having
# refreshed the file first. The kills are: case 2 "second refresh landed v3", and
# four in case 9/9c. If you add cases, RE-RUN this control and update the number --
# a stale expected count makes the next reader distrust a working harness.
# A second mutant, aimed at the prune specifically (must report FAIL 5, all in 9/9c):
#   sed 's/if ! git -C "$ROOT" cat-file -e "$UP:$p" 2>\/dev\/null; then/if false; then/' \
#     scripts/claude-hooks/base-clone-staleness.sh > /tmp/mA.sh
# 🔴 Confirm the sed ACTUALLY matched before believing a red -- a mutation that
# silently fails to apply reports the unmutated hook's result.
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

cleanup() { rm -rf "$TMP"; }
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
# batched checkout. This case pins the invariants, not the failure count, which
# is inherently racy.
W=$(mkfixture t7)
advance t7 "CLAUDE v2" ".claude/skills/another/SKILL.md"
git -C "$W" fetch -q origin fixture
( run_hook "$W" >/dev/null 2>&1 ) & ( run_hook "$W" >/dev/null 2>&1 ) & wait
check "no index.lock left behind" "$([ -e "$W/.git/index.lock" ] && echo yes || echo no)" "no"
check "repo still usable"         "$(git -C "$W" status --short >/dev/null 2>&1 && echo yes || echo no)" "yes"
check "CLAUDE.md ended correct"   "$(blob "$W" CLAUDE.md)" "$(upblob "$W" CLAUDE.md)"
check "new file ended present"    "$([ -e "$W/.claude/skills/another/SKILL.md" ] && echo yes || echo no)" "yes"

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

printf '\n=====================================\n'
printf 'PASS %d   FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
