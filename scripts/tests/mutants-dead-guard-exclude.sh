#!/usr/bin/env bash
# Mutation battery for the `exclude` registry status.
#
# 🔴 EACH MUTANT NAMES THE TEST THAT MUST KILL IT, AND THE ASSERTION MESSAGE OR
# ERROR THAT MUST APPEAR. "a test failed" is not the claim -- a mutant that dies
# to a DIFFERENT guard's error is green for the wrong reason and stays green
# when the guard under test is deleted. Every mutation here is the NARROWEST
# expression that can be wrong.
#
# Runs against a /tmp COPY, never the working tree, and under
# PYTHONDONTWRITEBYTECODE=1 -- CPython validates a cached module on
# mtime-in-whole-seconds plus size, so a same-length edit landing in the same
# second imports the ORIGINAL bytecode and is scored SURVIVED without ever
# executing.
#
#   bash scripts/tests/mutants-dead-guard-exclude.sh
set -uo pipefail

SRC_REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
[ -n "$SRC_REPO" ] && [ -d "$SRC_REPO" ] || { echo "cannot resolve repo root"; exit 1; }

WORK="$(mktemp -d /tmp/mut-dgx-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
cp -a "$SRC_REPO/scripts" "$WORK/scripts"
cp -a "$SRC_REPO/.git" "$WORK/.git" 2>/dev/null || true
SCAN="$WORK/scripts/dead-guard-scan.py"
TESTS="$WORK/scripts/tests/test_dead_guard_scan.py"
ORIG="$WORK/orig.py"
cp "$SCAN" "$ORIG"

export PYTHONDONTWRITEBYTECODE=1

pass=0; fail=0

# $1 name  $2 test-selector  $3 must-appear-in-output  $4 python-sed-expr
mutate () {
  local name="$1" sel="$2" want="$3" expr="$4"
  cp "$ORIG" "$SCAN"
  python3 - "$SCAN" "$expr" <<'PY'
import sys, json
path, expr = sys.argv[1], sys.argv[2]
old, new = json.loads(expr)
src = open(path, encoding="utf-8").read()
n = src.count(old)
if n != 1:
    sys.stderr.write(f"MUTATION NOT UNIQUE ({n} occurrences): {old!r}\n")
    sys.exit(9)
open(path, "w", encoding="utf-8").write(src.replace(old, new))
PY
  if [ $? -ne 0 ]; then
    echo "HARNESS-ERROR $name -- mutation did not apply uniquely"
    fail=$((fail+1)); cp "$ORIG" "$SCAN"; return
  fi
  local out
  out="$(cd "$WORK" && python3 -m pytest "$TESTS" -q -p no:cacheprovider \
         -k "$sel" 2>&1)"
  # 🔴 NO `printf ... | grep -q` HERE. Under `set -o pipefail`, `grep -q` exits
  # as soon as it matches, printf takes SIGPIPE on anything larger than the
  # 64 KiB pipe buffer, and the PIPELINE reports failure -- so a mutant that was
  # killed, with the wanted text plainly in the output, is scored SURVIVED, and
  # only the LOUD ones (big output) are misscored. Two of these were wrong that
  # way on the first run. Bash string matching has no pipe and no exit code.
  local nfail=0 line
  while IFS= read -r line; do
    case "$line" in FAILED\ *) nfail=$((nfail+1));; esac
  done <<< "$out"
  if [ "$nfail" -gt 0 ] && [[ "$out" == *"$want"* ]]; then
    echo "KILLED   $name  ($nfail failing, matched: $want)"
    pass=$((pass+1))
  else
    echo "SURVIVED $name  ($nfail failing; wanted output containing: $want)"
    printf '%s\n' "$out" | tail -25
    fail=$((fail+1))
  fi
  cp "$ORIG" "$SCAN"
}

# --- POSITIVE CONTROL FOR THE HARNESS ITSELF -------------------------------
# A mutation we KNOW is caught, so a batch of SURVIVEDs cannot be a fact about
# the runner (a wrong -k filter, a stale copy, a bad grep flavour).
mutate "control-exclude-status-never-matches" \
  "exclude or malformed or excluded or handed_to_pytest" \
  "test_an_excluded_file_is_NOT_handed_to_pytest_at_all" \
  '["r[\"status\"] != \"exclude\"", "r[\"status\"] != \"EXCLUDE-NO-SUCH-STATUS\""]'

# --- the subtraction ------------------------------------------------------
mutate "guard-files-ignores-the-drop-set" \
  "exclude or excluded or handed_to_pytest" \
  "test_an_excluded_file_is_NOT_handed_to_pytest_at_all" \
  '["and p not in drop", "and p not in ()"]'

mutate "scan-passes-no-exclusions-to-guard-files" \
  "exclude or excluded" \
  "test_WITH_an_exclude_row_the_surviving_file_is_collected_and_MEASURED" \
  '["excluded=[e[\"path\"] for e in excl])", "excluded=[])"]'

# --- the glob validator, clause by clause ---------------------------------
mutate "separator-check-inverted-to-a-pass" \
  "malformed_exclude_selector" \
  "test_a_malformed_exclude_selector_FAILS_LOUDLY_AT_LOAD_TIME" \
  '["    if not sep:\n", "    if False:\n"]'

mutate "charset-check-dropped" \
  "malformed_exclude_selector" \
  "test_a_malformed_exclude_selector_FAILS_LOUDLY_AT_LOAD_TIME" \
  '["not _GLOB_CHARS.match(glob)", "False"]'

mutate "charset-admits-whitespace" \
  "malformed_exclude_selector" \
  "test_a_malformed_exclude_selector_FAILS_LOUDLY_AT_LOAD_TIME" \
  '["^[A-Za-z0-9_.*?/\\[\\]-]+$", "^[A-Za-z0-9_.*?/\\[\\] -]+$"]'

mutate "empty-segment-check-dropped" \
  "malformed_exclude_selector" \
  "test_a_malformed_exclude_selector_FAILS_LOUDLY_AT_LOAD_TIME" \
  '["or \"\" in glob.split(\"/\")", "or False"]'

mutate "dotdot-check-dropped" \
  "malformed_exclude_selector" \
  "test_a_malformed_exclude_selector_FAILS_LOUDLY_AT_LOAD_TIME" \
  '["or \"..\" in glob.split(\"/\")", "or False"]'

mutate "reason-length-floor-removed" \
  "malformed_exclude_selector" \
  "test_a_malformed_exclude_selector_FAILS_LOUDLY_AT_LOAD_TIME" \
  '["_MIN_REASON = 40", "_MIN_REASON = 0"]'

mutate "validator-never-runs-at-load-time" \
  "malformed_exclude_selector" \
  "test_a_malformed_exclude_selector_FAILS_LOUDLY_AT_LOAD_TIME" \
  '["if row[\"status\"] == \"exclude\":", "if False:"]'

# --- the artifact ---------------------------------------------------------
mutate "census-drops-the-excluded-rows" \
  "recorded_in_the_census or MATCHING_NOTHING" \
  "test_an_excluded_file_is_recorded_in_the_census_WITH_ITS_REASON" \
  '["for e in sorted(excluded, key=lambda e: (e[\"rel\"], e[\"glob\"])):", "for e in []:"]'

mutate "census-excluded-row-loses-its-reason" \
  "recorded_in_the_census" \
  "test_an_excluded_file_is_recorded_in_the_census_WITH_ITS_REASON" \
  '["excluded\\t{_tsv(e['\''reason'\''])}", "excluded\\t-"]'

mutate "zero-match-exclude-row-is-dropped" \
  "MATCHING_NOTHING" \
  "test_an_exclude_glob_matching_NOTHING_still_lands_in_the_census" \
  '["            out.append(dict(base, path=None, rel=\"-\"))\n            continue\n", "            continue\n"]'

# --- the census header contract -------------------------------------------
mutate "header-skip-goes-back-to-exact-membership" \
  "OLD_census_header" \
  "test_an_OLD_census_header_revision_does_not_survive_as_a_DATA_line" \
  '["if line.startswith(\"#\") and not _PROVENANCE.match(line):", "if line in _CENSUS_HEADER:"]'

# Its own reason, and a DIFFERENT test from the mutant above: a _PROVENANCE
# that never matches makes every `#` line header, so the per-repo provenance
# notes are deleted rather than kept -- the exact regression devrc #842's
# round-4 fix was written for.
mutate "provenance-lines-are-swallowed-as-header" \
  "KEEPS_another_repos_provenance" \
  "test_scanning_one_repo_KEEPS_another_repos_provenance_line" \
  '["_PROVENANCE = re.compile(r\"^# (\\S+/\\S+) measured under \")", "_PROVENANCE = re.compile(r\"^# NEVER-MATCHES \")"]'

echo
echo "killed=$pass  survived/errors=$fail"
[ "$fail" -eq 0 ]
