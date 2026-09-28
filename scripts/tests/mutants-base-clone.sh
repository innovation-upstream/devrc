#!/usr/bin/env bash
# Mutation battery for base-clone-staleness.sh's prune path.
#
# Not run by CI -- it is an author/reviewer instrument, kept in-tree so the claim
# "mutation-verified" can be re-derived instead of believed.
#
#   bash scripts/tests/mutants-base-clone.sh
#
# 🔴 Each mutant is DIFFED against the original before it is run. A `sed` that
# silently fails to match reports the UNMUTATED file's behaviour, which reads as
# "the guard held" -- the most flattering possible wrong answer. That happened once
# during authoring (a `grep -c` verification printed 0 while the mutant plainly
# ran), which is why the check here is a diff and not a pattern count.
set -uo pipefail
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
HOOKSRC="$D/../claude-hooks/base-clone-staleness.sh"
SUITE="$D/test_base_clone_staleness.sh"
T="$(mktemp -d /tmp/bcs-mut-XXXXXX)"; trap 'rm -rf "$T"' EXIT

run() { # run <name> <expect: KILLED|SURVIVES> <sed-expr>
  local name="$1" expect="$2" expr="$3" m="$T/$1.sh"
  sed "$expr" "$HOOKSRC" > "$m"
  if cmp -s "$HOOKSRC" "$m"; then
    printf '  %-34s 🔴 MUTATION DID NOT APPLY — result would be meaningless\n' "$name"
    return
  fi
  local out fails
  out="$(HOOK="$m" bash "$SUITE" 2>&1 | tail -1)"
  fails="$(sed -n 's/.*FAIL \([0-9]*\)/\1/p' <<<"$out")"
  # 🔴 A suite that dies before printing its summary yields an EMPTY count, and
  # `${fails:-0}` would score that as SURVIVES -- a harness crash reported as "the
  # guard held". Say so instead of defaulting.
  if [ -z "$fails" ]; then
    printf '  %-34s 🔴 HARNESS BROKE — no summary line (got: %s)\n' "$name" "${out:-<no output>}"
    return
  fi
  local got="KILLED"; [ "$fails" -eq 0 ] && got="SURVIVES"
  local mark="ok "; [ "$got" != "$expect" ] && mark="🔴 "
  printf '  %s%-32s %-9s (%s kills) expected %s\n' "$mark" "$name" "$got" "${fails:-?}" "$expect"
}

printf 'baseline: '; bash "$SUITE" 2>&1 | tail -1

printf '\n== detection and containment (must be KILLED) ==\n'
run 'drop-deletion-detection'  KILLED \
  's|if ! git -C "$ROOT" cat-file -e "$UP:$p" 2>/dev/null; then|if false; then|'
run 'ungate-HEAD-shortcut'     KILLED \
  's|if \[ "$deleted" = no \] \\|if [ true ] \\|'
run 'strip-unique-work-guard'  KILLED \
  's|if \[ "$known" = no \]; then|if [ "$known" = no ] \&\& [ "$deleted" = no ]; then|'
run 'unbound-rmdir-climb'      KILLED \
  's|\[ "$_bounded" = yes \] \&\& break|:|'
run 'rmdir-to-rm-rf'           KILLED \
  's|rmdir "$ROOT/$d" 2>/dev/null|rm -rf "$ROOT/$d" 2>/dev/null|'
run 'dotdot-substring-guard'   KILLED \
  's|\*/\.\./\*) failed+=("$p"); continue ;;|*..*) failed+=("$p"); continue ;;|'
run 'hoist-prune-above-optout' KILLED \
  's|if \[ "${BASE_CLONE_NO_REFRESH:-0}" = "1" \]; then|if false; then|'
run 'break-recoverability-scan' KILLED \
  's|rev-list -n 100|rev-list -n 0|'
# 🔴 REACHABILITY PROOF for case 7b, which is otherwise the only case in the suite
# with no mutant of its own. This makes the batch retry unconditionally "succeed", so
# the report claims a refresh it did not perform -- the one lie this hook must never
# tell, and INVISIBLE to every other case, because outside lock contention the
# checkout genuinely succeeds and the mutant changes nothing observable. Expect the
# kills to come from 7b's two report assertions and from nowhere else.
run 'claim-refresh-without-writing' KILLED \
  's#^     || git -C "$ROOT" checkout "$UP" -- "${approved\[@\]}" >/dev/null 2>&1; then$#     || true; then#'
# 🔴 The REFRESH_PATHS regressions, mechanised: each mutant IS a previous state of
# the path list, so a KILLED verdict here is the red half of that entry's red->green
# matrix re-derived on demand instead of quoted from a commit message.
#
# ⚠️ Two rows rather than one, because the arrays are not nested states of one list and
# a single mutant cannot attribute both. Restoring the OLDEST array is also the only
# way to re-derive case 10's red half now that later entries exist.
run 'drop-agents-md-entry'     KILLED \
  's|^REFRESH_PATHS=(CLAUDE.md AGENTS.md .claude/settings.json .claude/hooks .claude/skills)$|REFRESH_PATHS=(CLAUDE.md .claude/skills)|'
run 'drop-claude-wiring-entries' KILLED \
  's|^REFRESH_PATHS=(CLAUDE.md AGENTS.md .claude/settings.json .claude/hooks .claude/skills)$|REFRESH_PATHS=(CLAUDE.md AGENTS.md .claude/skills)|'
# 🔴 ISOLATE THE MUTATION, and the isolation is the whole point of this row. The two
# above take the array back, so the climb has nothing new to bound and this guard is
# never REACHED by them -- it would survive both while doing nothing. This mutant
# leaves the array widened and removes ONLY the derived parent, which is the naive
# nested-file entry: `dirname .claude/settings.json` -> `.claude`, in no array entry,
# so the climb rmdirs `.claude` itself. Measured: exactly ONE assertion in the whole
# suite fails (case 11c's `.claude SURVIVES`), which is what makes that assertion a
# killing guard for the bound rather than an invariant guard about a hook that pruned
# nothing.
run 'drop-derived-climb-bound' KILLED \
  's#\[ "$_dup" = no \] \&\& CLIMB_BOUNDS+=("$_p")#:#'

printf '\n== unreachable-by-construction backstops (SURVIVES is EXPECTED, not a gap) ==\n'
printf '   `hash-object` fatals on a directory so one never reaches the prune, and\n'
printf '   `git diff --name-only` never emits a `..` component. These are defence in\n'
printf '   depth against a future change UPSTREAM of the loop, not pinned behaviour.\n'
run 'rm-f-to-rm-rf'            SURVIVES 's|rm -f "$ROOT/$p"|rm -rf "$ROOT/$p"|'
# 🔴 ISOLATE THE MUTATION. The obvious pattern here --
#   s|/\*) failed+=("$p"); continue ;;|...|
# -- also matches the TAIL of the `*/../*)` arm, so it disables the `..`-component
# guard at the same time and the SURVIVES verdict would be about two guards rather
# than the one it names. Anchor to the line start so only the absolute-path arm moves.
run 'drop-absolute-path-guard' SURVIVES 's|^    /\*) failed+=("$p"); continue ;;$|    /*) : ;;|'
