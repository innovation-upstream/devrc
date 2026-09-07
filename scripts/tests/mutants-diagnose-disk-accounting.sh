#!/usr/bin/env bash
# Mutation battery for `scripts/tests/test_diagnose_disk_accounting.sh`.
#
# Not run by CI. An author/reviewer instrument, kept IN THE TREE so
# "mutation-verified" can be RE-DERIVED instead of believed — the convention
# `mutants-claim-work.sh` and `mutants-shared-clone-sync.sh` established.
#
#   bash scripts/tests/mutants-diagnose-disk-accounting.sh
#
# 🔴 IT NEVER TOUCHES YOUR WORKING TREE. The script under mutation and the suite
# are copied into a `mktemp -d` and mutated THERE. In a repo whose rules are
# built around shared checkouts and parallel agents, an EXIT-trap restore over a
# tracked file is one SIGKILL away from a mutated file being staged by somebody
# else.
#
# 🔴 EACH MUTANT NAMES THE GUARD THAT MUST KILL IT, and the row is scored on
# whether THAT guard's own `FAIL:` line appears. "A test failed" is not enough:
# with overlapping assertions a mutant can die to a DIFFERENT guard's error and
# be scored as covered while its own assertion is unreachable. A mutant killed
# only by some other guard reports 🔴 WRONG-KILLER, not ok.
#
# 🔴 EVERY MUTATION IS AN EXACT, SINGLE-OCCURRENCE REPLACEMENT, and the count is
# CHECKED. A `sed` that silently fails to match reports the UNMUTATED file's
# behaviour — i.e. "the guard held", the most flattering possible wrong answer,
# and the way a previous sweep in this repo scored an unmutated baseline as
# killed. A `count=1` replace on a pattern occurring twice is the same hazard
# wearing the other face, so anything but exactly one occurrence is an error.
#
# 🔴 MUTATIONS ARE ISOLATED. Each edits the narrowest expression that can be
# wrong — the column reference, the `-exec` form, the `head_n` body — never a
# guard together with its enclosing condition, which dies for the wrong reason
# and proves nothing about the guard.
#
# 🔴 CONTROLS, all three:
#   * BASELINE — the unmutated copy must be green, or every row is meaningless.
#   * NEGATIVE — a deliberately broken copy must be SEEN to go red, or the
#     harness is not wired to the suite at all.
#   * SURVIVES — a behaviour-free edit that must NOT kill anything, which is what
#     proves the harness keys on BEHAVIOUR and not on the file's bytes.
#
# 🔴 PYTHONDONTWRITEBYTECODE=1 even though the file under mutation is BASH: the
# mutator is Python, and a stale `.pyc` keyed on mtime-in-whole-seconds + size is
# how a same-length edit gets scored SURVIVED without ever executing.
set -uo pipefail
export PYTHONDONTWRITEBYTECODE=1
CDPATH=
D="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" >/dev/null && pwd)"
SRC="$(cd "$D/../.." >/dev/null && pwd)"

T="$(mktemp -d /tmp/dda-mut-XXXXXX)"
trap 'rm -rf "$T"' EXIT
ROOT="$T/tree"
mkdir -p "$ROOT/scripts/tests"
cp -a "$SRC/scripts/diagnose-disk-accounting.sh" "$ROOT/scripts/"
cp -a "$SRC/scripts/tests/test_diagnose_disk_accounting.sh" "$ROOT/scripts/tests/"

SCRIPT="$ROOT/scripts/diagnose-disk-accounting.sh"
SUITE="$ROOT/scripts/tests/test_diagnose_disk_accounting.sh"
PRISTINE="$T/pristine.sh"
cp -a "$SCRIPT" "$PRISTINE"
LOG="$T/out.txt"
MUTATOR="$T/mutate.py"

cat > "$MUTATOR" <<'PY'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path, encoding="utf-8").read()
n = s.count(old)
if n != 1:
    sys.stderr.write("occurrences=%d for %r\n" % (n, old[:70]))
    sys.exit(3)
open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))
PY

restore() { cp -a "$PRISTINE" "$SCRIPT"; }
suite_run() { bash "$SUITE" >"$LOG" 2>&1; }
fails() { grep -c '^  FAIL: ' "$LOG"; }
# Did THIS guard fail? -F, because guard names carry `+`, `/` and apostrophes.
killed_by() { grep -qF "  FAIL: $1" "$LOG"; }

RC=0
apply() { # apply <old> <new>  -> 0 on an applied, single-occurrence edit
  python3 "$MUTATOR" "$SCRIPT" "$1" "$2" 2>"$T/mut.err"
}

run() { # run <name> <killer-guard-prefix> <old> <new>
  local name="$1" killer="$2"
  restore
  if ! apply "$3" "$4"; then
    printf '  %-38s 🔴 MUTATION DID NOT APPLY (%s) — result meaningless\n' \
      "$name" "$(tr -d '\n' < "$T/mut.err")"; RC=1; return
  fi
  if cmp -s "$PRISTINE" "$SCRIPT"; then
    printf '  %-38s 🔴 MUTATION IS A NO-OP — result meaningless\n' "$name"; RC=1; return
  fi
  suite_run
  local n; n="$(fails)"
  if [ "$n" -eq 0 ]; then
    printf '  %-38s 🔴 SURVIVED (0 FAIL lines)\n' "$name"; RC=1
  elif killed_by "$killer"; then
    printf '  %-38s KILLED   by its own guard (%s FAIL line(s))\n' "$name" "$n"
  else
    printf '  %-38s 🔴 WRONG-KILLER — %s FAIL line(s), none of them [%s]\n' "$name" "$n" "$killer"; RC=1
  fi
}

survives() { # survives <name> <old> <new>   — must NOT kill anything
  local name="$1"
  restore
  if ! apply "$2" "$3"; then
    printf '  %-38s 🔴 MUTATION DID NOT APPLY — result meaningless\n' "$name"; RC=1; return
  fi
  suite_run
  local n; n="$(fails)"
  if [ "$n" -eq 0 ]; then
    printf '  %-38s ok — behaviour-free edit did not kill\n' "$name"
  else
    printf '  %-38s 🔴 KILLED by a no-op edit (%s FAIL) — the suite keys on TEXT, not behaviour\n' "$name" "$n"; RC=1
  fi
}

echo "== BASELINE: the unmutated copy must be green =="
restore; suite_run
echo "   FAIL-lines=$(fails)  ok-lines=$(grep -c '^  ok: ' "$LOG")  last=[$(tail -1 "$LOG")]"
[ "$(fails)" -eq 0 ] || { echo "   🔴 baseline is RED — every row below is meaningless"; RC=1; }

echo "== NEGATIVE CONTROL: a broken copy must be SEEN to go red =="
# 🔴 The break must land INSIDE the sourceable seam. This control first APPENDED
# the override to the end of the file and reported "the harness cannot observe a
# failure at all" — correctly: the seam `return`s before the end of the file, so
# a definition after it is never evaluated when sourced. An appended-at-the-end
# negative control on a file with an early `return` tests nothing, and its
# reassuring reading is "the suite is fine".
restore
apply '# --- end of the sourceable seam ---' \
      'lsof_deleted_summary() { echo BROKEN; }
# --- end of the sourceable seam ---' \
  || { echo "   🔴 the negative control did not apply"; RC=1; }
suite_run
echo "   FAIL-lines=$(fails)  (must be non-zero)"
[ "$(fails)" -gt 0 ] || { echo "   🔴 the harness cannot observe a failure at all"; RC=1; }
restore

echo
echo "== mutants — every row must read KILLED by its own guard =="

# --- defect 1: the lsof column is resolved from the HEADER, not an index -----
run lsof-col-hardcoded-8 \
  '+L1 shape: SIZE/OFF found at col 7' \
  '{ n++; if (col) s += $col }' \
  '{ n++; if (col) s += $8 }'

run lsof-col-hardcoded-7 \
  'plain -n -P shape: same bytes, col 9' \
  '{ n++; if (col) s += $col }' \
  '{ n++; if (col) s += $7 }'

run lsof-lookup-by-position \
  '+L1 shape: SIZE/OFF found at col 7' \
  'if ($i == "SIZE/OFF") col=i' \
  'if (i == 8) col=i'

run lsof-absent-col-answers-anyway \
  'absent SIZE/OFF refuses and names the row count' \
  'if (!col) { printf "COULD NOT MEASURE: no SIZE/OFF column in lsof header (rows=%d) — NOT a zero\n", n+0; exit }' \
  'if (0) { }'

# --- defect 6: never negative; "did not run" is not "found nothing" ----------
run lsof-empty-falls-through-to-awk \
  "empty input: count=0 and lsof's exit status is carried" \
  '  if [ -z "$out" ]; then
    printf ' \
  '  if false; then
    printf '

run lsof-missing-binary-reports-a-zero \
  'lsof missing: COULD NOT MEASURE, not a count' \
  'echo "COULD NOT MEASURE: lsof not on PATH — this is NOT a zero"' \
  'echo "count=0 bytes=0.0 GiB"'

run lsof-assignment-rearms-set-e \
  'a non-zero lsof aborted the script under set -e' \
  'out="$("$LSOF_BIN" +L1 2>/dev/null)" || rc=$?' \
  'out="$("$LSOF_BIN" +L1 2>/dev/null)"; rc=$?'

# --- defect 2: root command injection ---------------------------------------
# 🔴 ISOLATED: only the traversal FORM changes. The find, the sort and the
# truncation around it are byte-identical, so the guard that dies is the one
# about executing a planted name and nothing else.
run inject-xargs-I-substitution \
  'inode_breakdown does not execute the planted name' \
  '  find "$1" -xdev -mindepth 1 -maxdepth 1 -type d \
    -exec sh -c '"'"'printf "%12d  %s\n" "$(find "$1" -xdev -printf . 2>/dev/null | wc -c)" "$1"'"'"' _ {} \; 2>/dev/null \
    | sort -rn | head_n 15' \
  '  find "$1" -xdev -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
    | xargs -I{} sh -c '"'"'printf "%12d  %s\n" "$(find "{}" -xdev -printf . 2>/dev/null | wc -c)" "{}"'"'"' \
    | sort -rn | head_n 15'

# --- defect 3: E2BIG --------------------------------------------------------
run e2big-glob-expanded-into-du \
  'size_breakdown exited' \
  '  find "$1" -xdev -mindepth 1 -maxdepth 1 -print0 2>/dev/null \
    | xargs -0 -r du -sh -x 2>/dev/null | sort -rh | head_n 15' \
  '  du -sh -x "$1"/* 2>/dev/null | sort -rh | head_n 15'

# --- the SIGPIPE route to the same "truncated scan" failure ------------------
run sigpipe-head-closes-the-pipe \
  'the run died rc' \
  'head_n() { awk -v n="$1" '"'"'NR<=n'"'"'; }' \
  'head_n() { head -n "$1"; }'

# --- defect 7: the device comparison ----------------------------------------
run home-device-comparison-dropped \
  'foreign list holds exactly the foreign-device dirs' \
  'if [ "$d" = "$root_dev" ]; then printf' \
  'if true; then printf'

# --- defect 4: the accumulator must survive the loop ------------------------
# 🔴 The HISTORICAL shape: a VARIABLE accumulator fed by a loop that is the head
# of a pipeline, so every append lands in a subshell and the caller reads back
# nothing. Only the accumulator moves; the device test above it is untouched, so
# this cannot be scored on the previous mutant's guard.
run home-foreign-list-in-a-subshell \
  'foreign report names the first foreign mount' \
  '    if [ "$d" = "$root_dev" ]; then printf '"'"'%s\0'"'"' "$p" >> "$onroot"
    else printf '"'"'%s\n'"'"' "$p" >> "$foreign"; fi
  done
}' \
  '    if [ "$d" = "$root_dev" ]; then printf '"'"'%s\0'"'"' "$p" >> "$onroot"
    else FOREIGN="${FOREIGN:-}$p"; fi
  done | cat
  printf '"'"'%s'"'"' "${FOREIGN:-}" > "$foreign"
}'

run foreign-none-branch-always-taken \
  'foreign report names the first foreign mount' \
  '  if [ -s "$1" ]; then
    while IFS= read -r p; do' \
  '  if false; then
    while IFS= read -r p; do'

# --- defect 5 + 8: denial accounting ----------------------------------------
run grep-c-with-or-echo-zero \
  'empty denial log prints three clean zero lines' \
  "denied=\$(grep -c 'Permission denied' \"\$log\" 2>/dev/null; true)" \
  "denied=\$(grep -c 'Permission denied' \"\$log\" 2>/dev/null || echo 0)"

run denial-count-pinned-to-zero \
  '3 denials counted' \
  "denied=\$(grep -c 'Permission denied' \"\$log\" 2>/dev/null; true)" \
  'denied=0'

run floors-warning-suppressed \
  'denials make the counts FLOORS, loudly' \
  '  if [ "$denied" -gt 0 ]; then
    echo "!! Running as root' \
  '  if false; then
    echo "!! Running as root'

run unclassified-folded-into-benign \
  '1 unclassified error counted' \
  'unclassified=$((total - denied - other))' \
  'unclassified=0'

# --- the root refusal and the sourceable seam -------------------------------
run root-refusal-softened \
  'non-root execution exited' \
  '  exit 2' \
  '  exit 0'

run seam-runs-the-report-on-source \
  'sourcing produces no output at all beyond the marker' \
  'if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0
fi' \
  'if false; then
  return 0
fi'

echo
echo "== SURVIVES control — a behaviour-free edit must NOT kill anything =="
survives comment-reword \
  '# Root-privileged disk accounting for the workbench root filesystem.' \
  '# Root-privileged disk accounting for the workbench root filesystem. (reworded)'

echo
if [ "$RC" -eq 0 ]; then echo "mutation battery: every mutant killed by its own guard"
else echo "mutation battery: PROBLEMS above"; fi
exit "$RC"
