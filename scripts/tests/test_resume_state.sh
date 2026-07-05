#!/usr/bin/env bash
# Unit tests for resume-state.sh extraction heuristics.
# Sources the script (guarded main won't run) and asserts the pure functions on
# fixture handoff text. Exits non-zero on any failure.
#   run: bash scripts/tests/test_resume_state.sh
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
source "$HERE/../resume-state.sh"

FAIL=0
pass(){ printf '  ok  %s\n' "$1"; }
fail(){ printf '  FAIL %s\n' "$1"; FAIL=1; }

# assert that `$2` (multiline actual) equals sorted expected `$3`
eq(){ # name  actual  expected
  if [ "$2" = "$3" ]; then pass "$1"; else
    fail "$1"; printf '     expected: [%s]\n     actual:   [%s]\n' "$3" "$2"
  fi
}
# assert `$2` (multiline) contains a line exactly equal to `$3`
has(){ grep -qxF "$3" <<<"$2" && pass "$1" || { fail "$1"; printf '     missing line: [%s] in\n[%s]\n' "$3" "$2"; }; }
# assert `$2` does NOT contain a line exactly equal to `$3`
lacks(){ grep -qxF "$3" <<<"$2" && { fail "$1"; printf '     unexpected line: [%s]\n' "$3"; } || pass "$1"; }

FIX='## Handoff
Branch/PR: on feat/api-pool-hol-blocking. PRs #415 and #433 MERGED.
#478 OPEN — doc update. See https://github.com/civitai/talos-infra/pull/219 for the pool fix.
Also touched zach/rightsize-drain and fix/metrics-server-probe.
A stray prose ref like #5 must NOT be a PR. And version v1.2 mentions #7 too.
Deployments: civitai-dp-prod-api-primary and metrics-server crashloop.'

echo "== extract_prs =="
PRS=$(extract_prs "$FIX")
# #415,#433,#478 (>=2 digits) + 219 (from the pull/ URL); NOT #5 or #7
eq "prs (sorted, deduped, incl pull-url, excl 1-digit)" "$PRS" "$(printf '219\n415\n433\n478')"
lacks "single-digit #5 not a PR" "$PRS" "5"
lacks "single-digit #7 not a PR" "$PRS" "7"
has  "pull/NNN url extracted"    "$PRS" "219"

echo "== extract_branches =="
BR=$(extract_branches "$FIX")
has "feat/ branch"  "$BR" "feat/api-pool-hol-blocking"
has "zach/ branch"  "$BR" "zach/rightsize-drain"
has "fix/ branch"   "$BR" "fix/metrics-server-probe"

echo "== extract_tokens =="
TOK=$(extract_tokens "$FIX")
has "real deploy token present"  "$TOK" "civitai-dp-prod-api-primary"
has "hyphenated token intact"    "$TOK" "metrics-server"

echo "== handoff_says_inflight =="
TMP=$(mktemp); printf '%s\n' "$FIX" > "$TMP"
# #478 is framed "OPEN" -> in-flight; #415 is framed "MERGED" -> not in-flight
if handoff_says_inflight 478 "$TMP"; then pass "478 framed in-flight (OPEN)"; else fail "478 framed in-flight (OPEN)"; fi
if handoff_says_inflight 415 "$TMP"; then fail "415 framed merged (should NOT be in-flight)"; else pass "415 not framed in-flight"; fi
rm -f "$TMP"

echo
if [ "$FAIL" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILURES"; exit 1; fi
