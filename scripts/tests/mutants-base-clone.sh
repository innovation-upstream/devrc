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
