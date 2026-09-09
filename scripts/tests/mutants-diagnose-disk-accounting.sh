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
  '    | { xargs -0 -r -n1 sh -c '"'"'printf "%12d  %s\n" "$(find "$1" -xdev -printf . 2>/dev/null | wc -c)" "$1"'"'"' _ || true; } \' \
  '    | { xargs -0 -I{} sh -c '"'"'printf "%12d  %s\n" "$(find "{}" -xdev -printf . 2>/dev/null | wc -c)" "{}"'"'"' || true; } \'

# --- defect 3: E2BIG --------------------------------------------------------
# 🔴 THE KILLER MOVED, AND SAYING WHY IS THE POINT. It used to be
# `size_breakdown exited`: the glob died E2BIG, the failure was the LAST thing
# the function did, so its rc reached the caller. Round 2 put the pipeline in a
# `out=$(…)` capture with four statements after it, so the function now returns
# `rm`'s 0 whatever the pipeline did, and no rc assertion can ever see this
# mutant. What it CANNOT do any more is quietly return a truncated list: the
# empty `out` prints the explicit "none" line and the row-count guard sees 1 row
# where it demands 15. MEASURED WRONG-KILLER under the old name, 15 FAIL lines,
# none of them the rc one.
run e2big-glob-expanded-into-du \
  'size_breakdown returned' \
  '  out=$(_depth1_nul "$base" "$SCAN_ERRF" \
    | { _on_device "$dev" || true; } \
    | { xargs -0 -r du -sh -x 2>>"$SCAN_DUERR" || true; } \
    | sort -rh | head_n 15)' \
  '  out=$(du -sh -x "$base"/* 2>/dev/null | sort -rh | head_n 15)'

# --- the SIGPIPE route to the same "truncated scan" failure ------------------
# 🔴 TWO ROWS, ONE MUTATION. `head_n` is shared, so a single edit breaks both
# breakdowns — but they are scored on DIFFERENT probes' guards, which is the
# only way to show that the inode half is covered at all. It was not: the
# fixture held 17,550 `touch`ed FILES and `inode_breakdown` filters `-type d`,
# so its call in the probe returned zero rows and the whole row was killed by
# `size_breakdown` alone.
run sigpipe-head-closes-the-pipe \
  'the run died rc' \
  'head_n() { awk -v n="$1" '"'"'NR<=n'"'"'; }' \
  'head_n() { head -n "$1"; }'

run sigpipe-head-closes-the-pipe-inode \
  'the inode breakdown died rc' \
  'head_n() { awk -v n="$1" '"'"'NR<=n'"'"'; }' \
  'head_n() { head -n "$1"; }'

# --- the `head` BAN WAS SPELLED, NOT STRUCTURAL ------------------------------
# 🔴 The banned pattern read `| *head -[0-9]`, which demands a digit right after
# `head -`. All three long-form spellings below SURVIVED a full green suite;
# only `head -15` was caught. The pattern is now `| *head  *-`.
run head-long-form-n-20 \
  "no bare '| head -N' truncating a pipeline" \
  '    | sort -rh | head_n 15' \
  '    | sort -rh | head -n 20'

run head-long-form-n-30 \
  "no bare '| head -N' truncating a pipeline" \
  '    | sort -rn | head_n 15' \
  '    | sort -rn | head -n 30'

run head-long-form-n-15-section-5 \
  "no bare '| head -N' truncating a pipeline" \
  '| sort -rh | head_n 30' \
  '| sort -rh | head -n 15'

# The spelling the OLD pattern already caught, kept as the contrast case: both
# spellings must die, or the widening traded one blind spot for another.
run head-short-form-15 \
  "no bare '| head -N' truncating a pipeline" \
  '    | sort -rh | head_n 15' \
  '    | sort -rh | head -15'

# --- routes 3 and 4: a find or a du that fails mid-scan ----------------------
# 🔴 ISOLATED PER STAGE. Removing both `|| true`s at once would prove only that
# "something" is tolerated; each row removes exactly one, and the two are killed
# by fixtures that isolate one stage each (0400 dir -> find rc 1 with output;
# mode-000 subdirectory -> du rc 1 -> xargs rc 123, find rc 0).
# 🔴 ONE ROW, NOT TWO. There used to be `find-error-aborts-the-run` and
# `inode-find-error-aborts-the-run`, one per breakdown. Round 2 consolidated the
# three depth-1 enumerations into `_depth1_nul` — a predicate open-coded at three
# sites was wrong at all three — so there is now ONE `|| true` to remove and one
# mutant that removes it. Two rows applying the identical edit would have looked
# like twice the coverage.
run depth1-find-error-aborts-the-run \
  'a failing find/du aborted the run' \
  '  find "$base" -xdev -mindepth 1 -maxdepth 1 "$@" -printf '"'"'%D\t%p\0'"'"' 2>"$errf" || true' \
  '  find "$base" -xdev -mindepth 1 -maxdepth 1 "$@" -printf '"'"'%D\t%p\0'"'"' 2>"$errf"'

run du-failure-aborts-the-run \
  'a failing find/du aborted the run' \
  '    | { xargs -0 -r du -sh -x 2>>"$SCAN_DUERR" || true; } \
    | sort -rh | head_n 15)' \
  '    | xargs -0 -r du -sh -x 2>>"$SCAN_DUERR" \
    | sort -rh | head_n 15)'

# 🔴 THE F-1 MUTANT, and the one the round-1 fix would have SURVIVED. `%D` makes
# find stat every entry; with the stderr at /dev/null an entry it cannot stat is
# erased from all three sections at once and `foreign_entries` says "none".
run depth1-stderr-discarded \
  'route (a): size_breakdown COUNTS the entries it could not stat' \
  'find "$base" -xdev -mindepth 1 -maxdepth 1 "$@" -printf '"'"'%D\t%p\0'"'"' 2>"$errf" || true' \
  'find "$base" -xdev -mindepth 1 -maxdepth 1 "$@" -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true'

run unstattable-count-pinned-to-zero \
  'route (a): foreign_entries COUNTS the entries it could not stat' \
  '  n=$(grep -c . "$1" 2>/dev/null; true)' \
  '  n=0'

# 🔴 THE ANCHOR LINE ABOVE IS PART OF THE MUTATION, and it has to be: round 3
# gave `_report_unreadable` the same `[ "$n" -gt 0 ] || return 0` guard, so the
# bare expression now occurs TWICE and the battery correctly refused to apply it
# (`occurrences=2`). The `local` line pins it to `_report_unstattable` alone —
# a `count=1` replace on a two-occurrence pattern is the failure this file's
# header is about, and the check is what caught it.
run unstattable-report-suppressed \
  'route (a): inode_breakdown COUNTS the entries it could not stat' \
  '  local n="$1" base="$2" errf="$3"
  [ "$n" -gt 0 ] || return 0' \
  '  local n="$1" base="$2" errf="$3"
  [ "$n" -gt 0 ] && return 0'

# 🔴 THE AFFIRMATIVE "none" OVER A BLIND SCAN — the sentence the round-1 report
# printed while three entries were missing from every list.
run foreign-none-outranks-its-blind-spot \
  'route (a): foreign_entries does NOT claim every entry is on the same filesystem' \
  '    if [ "$blind" -gt 0 ]; then' \
  '    if false; then'

# The F-3 shape, restored verbatim: a `tr`-flattened stream indented per LINE.
# 🔴 The killer is the guard's FAIL text, not its `pass` text — the two are
# worded differently here, and this row was scored WRONG-KILLER first time round
# for exactly that reason, which is the SECOND time this file has made that
# mistake (see `foreign-list-one-per-line` below).
run foreign-entries-tr-flattened \
  'foreign_entries emitted' \
  '  while IFS= read -r -d '"''"' p; do
    n=$((n + 1))
    [ "$n" -gt 15 ] || printf '"'"'  %s\n'"'"' "$p"
  done < <(_depth1_nul "$base" "$SCAN_ERRF" | _not_on_device "$dev")' \
  '  out=$(_depth1_nul "$base" "$SCAN_ERRF" | _not_on_device "$dev" | tr '"'"'\0'"'"' '"'"'\n'"'"' | head_n 15)
  [ -z "$out" ] || { printf '"'"'%s\n'"'"' "$out" | sed '"'"'s/^/  /'"'"'; n=1; }'

# 🔴 THE CHECKED ASSIGNMENT. Three sites share the shape, so the mutation
# carries the line BELOW it to stay a single occurrence and to keep the edit
# inside `size_breakdown` alone.
run dev-assignment-rearms-set-e \
  'an unreadable base killed the run under set -e' \
  '  dev=$(_dev_of "$base") || dev=
  if ! _dev_is_valid "$dev"; then
    echo "COULD NOT MEASURE: no device id for $base — NOT an empty directory (size breakdown)"' \
  '  dev=$(_dev_of "$base")
  if ! _dev_is_valid "$dev"; then
    echo "COULD NOT MEASURE: no device id for $base — NOT an empty directory (size breakdown)"'

# An empty list must SAY it is empty; printing nothing is what a non-measurement
# also does, and "an empty section is still visible" was a false claim.
run size-empty-list-prints-nothing \
  'size_breakdown SAYS its list is empty rather than printing nothing' \
  '    echo "  none — no depth-1 entry of $base is on $base'"'"'s own filesystem (NOT zero bytes)"' \
  '    :'

run inode-empty-list-prints-nothing \
  'inode_breakdown SAYS its list is empty rather than printing nothing' \
  '    echo "  none — no depth-1 DIRECTORY of $base is on $base'"'"'s own filesystem (NOT zero inodes)"' \
  '    :'

# --- the `set -e` sweep: unguarded statements in the ROOT-ONLY region ---------
# 🔴 These three sites cannot be reached without root, so they are scored on
# STRUCTURAL guards — the `required` rows and section 7b's sweep. That is
# labelled here rather than left for a reader to assume: the behaviour is
# measured in section 4b (route (b)) and in the probes above; what these rows
# prove is that the pins actually fire.
run k3s-du-total-unguarded \
  "section 5's du TOTAL is guarded, not bare" \
  '  du -sh /var/lib/rancher/k3s/storage 2>/dev/null \
    || echo "COULD NOT MEASURE: du failed under /var/lib/rancher/k3s/storage — any total it printed is a FLOOR"' \
  '  du -sh /var/lib/rancher/k3s/storage 2>/dev/null'

run dumpe2fs-pipeline-unguarded \
  "section 1's dumpe2fs pipeline is guarded" \
  'Last checked'"'"' \
  || echo "COULD NOT MEASURE: dumpe2fs read no ext4 superblock fields from $DEV — set DEV=<device> if that is the wrong partition"' \
  'Last checked'"'"

# 🔴 THE ONE THE `required` TABLE DOES NOT PIN — only the 7b sweep sees it, which
# is the whole reason 7b exists rather than a tenth `required` row.
run tmp-name-families-unguarded \
  'the set -e sweep found' \
  '{ ls -A /tmp 2>/dev/null | sed -E '"'"'s/[0-9]{3,}.*$//; s/[A-Za-z0-9]{8,}$//'"'"' \
  | sort | uniq -c | sort -rn | head_n 20; } || true' \
  'ls -A /tmp 2>/dev/null | sed -E '"'"'s/[0-9]{3,}.*$//; s/[A-Za-z0-9]{8,}$//'"'"' \
  | sort | uniq -c | sort -rn | head_n 20'

# --- the record-counted truncation that replaced `head_n 15` ------------------
# 🔴 NEW EXPRESSION, NEW MUTANTS. `head_n 15` counted LINES; the loop counts
# RECORDS, which is a different thing that can be off by one or forget to say it
# truncated — and a listing that stops at 15 without saying so is the same
# floor-presented-as-a-total this file is about.
run foreign-truncation-off-by-one \
  '20 foreign entries printed' \
  '    [ "$n" -gt 15 ] || printf '"'"'  %s\n'"'"' "$p"' \
  '    [ "$n" -gt 16 ] || printf '"'"'  %s\n'"'"' "$p"'

run foreign-truncation-not-stated \
  'the truncation is STATED, not silent' \
  '  if [ "$n" -gt 15 ]; then
    printf '"'"'  ... and %d more\n'"'"' "$((n - 15))"' \
  '  if false; then
    printf '"'"'  ... and %d more\n'"'"' "$((n - 15))"'

run root-dev-reading-unguarded \
  'the root device reading cannot kill the run' \
  'ROOT_DEV=$(_dev_of /) || ROOT_DEV=' \
  'ROOT_DEV=$(_dev_of /)'

# --- defect 7, the /tmp half: -xdev LISTS a foreign mountpoint at depth 1 -----
run tmp-size-device-filter-dropped \
  'size_breakdown DROPS every entry on a foreign device' \
  '  out=$(_depth1_nul "$base" "$SCAN_ERRF" \
    | { _on_device "$dev" || true; } \' \
  '  out=$(_depth1_nul "$base" "$SCAN_ERRF" \
    | { sed -z -n '"'"'s/^[0-9]*\t//p'"'"' || true; } \'

run tmp-inode-device-filter-dropped \
  'inode_breakdown DROPS every directory on a foreign device' \
  '  out=$(_depth1_nul "$base" "$SCAN_ERRF" -type d \
    | { _on_device "$dev" || true; } \' \
  '  out=$(_depth1_nul "$base" "$SCAN_ERRF" -type d \
    | { sed -z -n '"'"'s/^[0-9]*\t//p'"'"' || true; } \'

# 🔴 STRUCTURAL PIN, and it is labelled as one. A second filesystem needs root,
# so `du -x` cannot be checked behaviourally in this suite; a mutant dropping it
# SURVIVED before the `required` row was added.
run du-x-dropped-from-the-tmp-breakdown \
  'du keeps -x AND its || true in the /tmp size breakdown' \
  '    | { xargs -0 -r du -sh -x 2>>"$SCAN_DUERR" || true; } \' \
  '    | { xargs -0 -r du -sh 2>>"$SCAN_DUERR" || true; } \'

run du-x-dropped-from-the-home-breakdown \
  'du keeps -x AND its || true in the /home size breakdown' \
  '{ xargs -0 -r du -sh -x < "$ONROOT_LIST" 2>>"$DU_ERR" || true; }' \
  '{ xargs -0 -r du -sh < "$ONROOT_LIST" 2>>"$DU_ERR" || true; }'

# The device READING itself: hex vs decimal is a silent total mis-classification.
run dev-of-returns-hex-not-decimal \
  '_dev_of / agrees with stat -c %d / (decimal)' \
  "_dev_of() { stat -c '%d' \"\$1\" 2>/dev/null; }" \
  "_dev_of() { stat -c '%D' \"\$1\" 2>/dev/null; }"

# An empty device reading must REFUSE, not print an empty section.
run absent-device-prints-an-empty-section \
  'an unreadable base REFUSES rather than printing an empty size section' \
  '    echo "COULD NOT MEASURE: no device id for $base — NOT an empty directory (size breakdown)"
    return 0' \
  '    return 0'

# 🔴 The device filter's own blind spot: excluding a foreign mount is right,
# excluding it SILENTLY is the failure this file catalogues. Section 6c prints
# the same listing for /home; 6d had no equivalent until the filter created the
# need for one.
run foreign-entries-lists-nothing \
  '6d NAMES the entries it excluded (first)' \
  '    [ "$n" -gt 15 ] || printf '"'"'  %s\n'"'"' "$p"' \
  '    [ "$n" -gt 15 ] || :'

run foreign-entries-inverted-filter \
  '_not_on_device is the exact complement of _on_device' \
  '_not_on_device() { sed -z -n "/^$1\t/!{s/^[0-9]*\t//;p;}"; }' \
  '_not_on_device() { sed -z -n "s/^[0-9]*\t//p"; }'

run foreign-entries-none-branch-never \
  'POSITIVE CONTROL: an all-on-device base prints the explicit' \
  '  elif [ "$n" -eq 0 ]; then' \
  '  elif false; then'

# The digits predicate: it guards a value that reaches a `sed` SCRIPT.
run dev-is-valid-accepts-anything-non-empty \
  '_dev_is_valid accepted [/etc/passwd]' \
  '_dev_is_valid() { case "$1" in '"''"'|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }' \
  '_dev_is_valid() { case "$1" in '"''"') return 1 ;; *) return 0 ;; esac; }'

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
    else printf '"'"'%s\0'"'"' "$p" >> "$foreign"; fi
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
    while IFS= read -r -d '"''"' p; do' \
  '  if false; then
    while IFS= read -r -d '"''"' p; do'

# --- the foreign list must be NUL-separated, like the on-root one ------------
# 🔴 It was one-per-line, read with `while IFS= read -r`, so a directory name
# containing a newline split into two rows in the report — the second of which
# reads as a real path. /home is user-writable and this runs under sudo.
# The killer is the guard's FAIL text, not its `pass` text — this row was scored
# WRONG-KILLER first time round because the two are worded differently here.
run foreign-list-one-per-line \
  'the foreign list holds' \
  '    else printf '"'"'%s\0'"'"' "$p" >> "$foreign"; fi' \
  '    else printf '"'"'%s\n'"'"' "$p" >> "$foreign"; fi'

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
  'if (return 0 2>/dev/null); then' \
  'if false; then'

# 🔴 THE STRONGEST MUTANT IN THIS FILE: it reinstates the EXACT expression that
# shipped, `[ "${BASH_SOURCE[0]}" != "$0" ]`. bash imports BASH_SOURCE from the
# environment as a scalar, so an EXECUTED run with a stray BASH_SOURCE took the
# sourced branch and hit `return` at top level — exit 2, no report, and 2 is
# also this script's "you forgot sudo" status. Note the killer: the rc-2 check
# still PASSES under this mutant, so only the guard that asserts the refusal
# MESSAGE can see it. That is the whole reason the defect was invisible.
run seam-reads-bash-source-again \
  'the poisoned run refuses for the RIGHT reason' \
  'if (return 0 2>/dev/null); then' \
  'if [ "${BASH_SOURCE[0]}" != "$0" ]; then'

# The same mutant, scored on the OTHER surface it breaks: `bash < script` leaves
# BASH_SOURCE unset and `set -u` kills the guard's own line, rc 1. Base ran fine
# that way, so this half is a genuine regression rather than a latent hazard.
run seam-reads-bash-source-again-stdin \
  'bash < script exited' \
  'if (return 0 2>/dev/null); then' \
  'if [ "${BASH_SOURCE[0]}" != "$0" ]; then'

# --- the LSOF_BIN seam must not widen what a ROOT run executes ---------------
# 🔴 `LSOF_BIN=${LSOF_BIN:-lsof}` used to sit ABOVE the seam, i.e. on the
# EXECUTE path, so an inherited `LSOF_BIN=/anything` was what root would run.
# Base hardcoded `lsof`. Nothing pinned it and the mutant SURVIVED.
run lsof-bin-honoured-on-the-execute-path \
  'the execute path DISCARDS an inherited LSOF_BIN' \
  'LSOF_BIN=lsof
' \
  'LSOF_BIN=${LSOF_BIN:-lsof}
'

# --- round 3: du's stderr, the second and different blind spot ---------------
# 🔴 ROUTE (b) KEEPS THE ENTRY AND SHORTENS ITS NUMBER, which is why these are
# not scored on the route-(a) guards. MEASURED on the §4b-iii fixture: 8K
# printed against a true 12K, with no marker of any kind while du's stderr went
# to /dev/null.
run du-stderr-discarded \
  'route (b): size_breakdown REPORTS that du could not read a path' \
  '    | { xargs -0 -r du -sh -x 2>>"$SCAN_DUERR" || true; } \
    | sort -rh | head_n 15)' \
  '    | { xargs -0 -r du -sh -x 2>/dev/null || true; } \
    | sort -rh | head_n 15)'

run du-blind-spot-not-reported \
  "route (b): du's own error line is quoted, not just tallied" \
  '  _report_unreadable "$base" "$SCAN_DUERR"' \
  '  :'

# 🔴 THE TWO SINKS ARE NOT INTERCHANGEABLE, and this is the mutant that says so:
# handing the du report find's stderr yields a count of 0 over a fixture find can
# stat completely, i.e. an affirmative silence.
run du-report-reads-finds-stderr \
  'route (b): size_breakdown REPORTS that du could not read a path' \
  '  _report_unreadable "$base" "$SCAN_DUERR"' \
  '  _report_unreadable "$base" "$SCAN_ERRF"'

# --- round 3: the temp files nothing named and no trap covered ---------------
run scan-temp-file-anonymous \
  'a scan temp file is named [' \
  '_scan_mktemp() { REPLY=$(mktemp "/tmp/disk-accounting-$1.XXXXXX") || { REPLY=; return 1; }; }' \
  '_scan_mktemp() { REPLY=$(mktemp) || { REPLY=; return 1; }; }'

# 🔴 THE HISTORICAL SHAPE, restored exactly: a cleanup that names the files
# somebody thought of. Killed only by the SIGINT probe, because every other
# guard here removes its own temp files on the happy path.
run cleanup-forgets-the-scan-temps \
  'an interrupt mid-breakdown leaked' \
  '  rm -f "${DENIED_LOG:-}" "${DU_ERR:-}" "${ONROOT_LIST:-}" "${FOREIGN_LIST:-}" \
        "${SCAN_ERRF:-}" "${SCAN_DUERR:-}"' \
  '  rm -f "${DENIED_LOG:-}" "${ONROOT_LIST:-}" "${FOREIGN_LIST:-}"'

# 🔴 STRUCTURAL PIN, labelled as one. The trap line is in the ROOT-ONLY region,
# so no fixture can reach it; what the SIGINT probe measures is the FUNCTION, and
# what this row proves is that the script really installs that function rather
# than a hand-written list.
run trap-named-as-a-fixed-list \
  'the EXIT trap names a FUNCTION, not a fixed list of files' \
  'trap _cleanup_temps EXIT' \
  "trap 'rm -f \"\$DENIED_LOG\"' EXIT"

# 🔴 THE ORDER HALF, scored on the ordering guard rather than the name pin: the
# trap still names `_cleanup_temps`, so the `required` row above stays GREEN and
# only the line-number comparison can see it.
run trap-installed-after-the-first-mktemp \
  'the EXIT trap is installed at line' \
  'trap _cleanup_temps EXIT
DENIED_LOG=$(mktemp /tmp/disk-accounting-denied.XXXXXX)' \
  'DENIED_LOG=$(mktemp /tmp/disk-accounting-denied.XXXXXX)
trap _cleanup_temps EXIT'

run k3s-du-listing-stderr-discarded \
  "section 5's PVC listing keeps du's stderr too" \
  '| { xargs -0 -r du -sh --exclude=/mnt 2>>"$DU_ERR" || true; } | sort -rh | head_n 30' \
  '| { xargs -0 -r du -sh --exclude=/mnt 2>/dev/null || true; } | sort -rh | head_n 30'

# --- round 3: the trailing-sort ledger, both directions ----------------------
# 🔴 SHRINK AND GROW, because a ledger asserted only one way is half a ledger.
# Neither of these is visible to the first-word sweep: the guarded form still
# contains `||` and the added one is inside a `$(…)` in a printf argument.
run sort-ledger-site-guarded-away \
  'the sort ledger found' \
  '| { xargs -0 -r du -sh --exclude=/mnt 2>>"$DU_ERR" || true; } | sort -rh | head_n 30' \
  '| { xargs -0 -r du -sh --exclude=/mnt 2>>"$DU_ERR" || true; } | { sort -rh || true; } | head_n 30'

run sort-ledger-site-added \
  'the sort ledger found' \
  "printf 'store paths     : %d\\n' \"\$(ls /nix/store/ 2>/dev/null | wc -l)\"" \
  "printf 'store paths     : %d\\n' \"\$(ls /nix/store/ 2>/dev/null | sort | wc -l)\""

# --- round 4: section 6's du printed a FLOOR with no marker ------------------
# 🔴 TWO MUTANTS, ISOLATED, because the site has TWO ways to be wrong and one
# guard each. Neither mutant touches the other's literal, so a row that goes red
# proves its OWN assertion is reachable rather than that "something failed".
# Section 6 is root-only, so both are INVARIANT PINS with no behavioural half —
# what a fixture CAN show, and did (2026-09-08, a mode-000 subdirectory): the
# expression prints 20K against a true 24K and now appends the marker, rc 0.
run section6-du-status-not-read \
  "section 6 BRANCHES on du's status instead of discarding it" \
  $'    if du_out=$(du -sh -x "$d" 2>/dev/null); then du_mark=; else du_mark=\'  !! FLOOR — du could not read all of it\'; fi' \
  $'    du_out=$(du -sh -x "$d" 2>/dev/null) || true; du_mark='

# 🔴 The mirror: the branch stays, so the row above is still GREEN, and the only
# thing that can see this is the pin on the printf's argument list. A marker
# computed and never printed is the field-that-is-not-a-guard shape.
run section6-marker-computed-but-not-printed \
  'section 6 PRINTS the marker it computed' \
  '"$(find "$d" -xdev -printf . 2>/dev/null | wc -c)" "$d" "$du_mark"' \
  '"$(find "$d" -xdev -printf . 2>/dev/null | wc -c)" "$d"'

# --- round 4: the `: > "$DU_ERR"` truncations, fatal and invisible ------------
# 🔴 SCORED ON THE LEDGER, and nothing else in the suite can reach it: §7b's
# first-word sweep cannot see a line headed by `:` and the sort ledger needs a
# `| sort`, so before this ledger existed BOTH sites could lose their guard with
# the suite fully green. Section 6c's copy is the one that matters — it fires
# after sections 1..6b have printed.
run duerr-truncation-unguarded-6c \
  "not every ': > \$DU_ERR' truncation is guarded" \
  ': > "$DU_ERR" || echo "COULD NOT MEASURE: could not truncate du'"'"'s stderr file — any PARTIALLY READ count below may include paths from an earlier section"
{ xargs -0 -r du -sh -x < "$ONROOT_LIST"' \
  ': > "$DU_ERR"
{ xargs -0 -r du -sh -x < "$ONROOT_LIST"'

# 🔴 THE OTHER DIRECTION OF THE SAME LEDGER — a NEW unguarded truncation, which
# is the shape a future edit actually adds. It must go red for GROWTH too, or
# the ledger is half a ledger.
run duerr-truncation-added-unguarded \
  "not every ': > \$DU_ERR' truncation is guarded" \
  '  _report_unreadable /var/lib/rancher/k3s/storage "$DU_ERR"' \
  '  _report_unreadable /var/lib/rancher/k3s/storage "$DU_ERR"
  : > "$DU_ERR"'

echo
echo "== the top-level enumerator (section 2's entry set) =="
# 🔴 The defect these pin: section 2 read `[ -d "$d" ] || continue`, so a
# top-level entry that was not a directory got no row. /swapfile is 48.00 GiB on
# this host and was absent from the byte column entirely.
# 🔴 `-type d` GOES IN THE PRINT BRANCH, and the first draft of this row put it
# after `-maxdepth 1` instead — where it is INERT and the row SURVIVED, saying
# nothing about the guard. In `\( … \) -prune -o -print0`, a non-directory simply
# FAILS the left side and falls through to `-o -print0`, so it is printed anyway.
# MEASURED on a fixture holding one dir and one file: the inert form emitted
# both. A mutant must be the narrowest expression that can actually be wrong.
run toplevel-directories-only \
  "a top-level regular FILE is accounted for (the /swapfile shape)" \
  '    -o -print0' \
  '    -o -type d -print0'

# 🔴 Not cosmetic: a newline-delimited enumeration splits a name containing a
# newline into phantom paths, which `find` then logs as "vanished mid-scan
# (benign, transient)" — an under-count reported as harmless.
run toplevel-newline-delimited \
  "a name containing a newline is emitted as ONE NUL-terminated record" \
  '    -o -print0' \
  '    -o -print'

# 🔴 The `//` normalisation. Its guard is a COUNT, not a string match: deleting
# this line makes `find ""` fail, and a bare "no doubled slashes" assertion then
# passes on empty output — a survivor in a fully green suite, measured.
run toplevel-root-normalisation-dropped \
  "a root of '/' enumerated 0 entries" \
  '  [ -n "$root" ] || root=/' \
  '  :'

echo
echo "== SURVIVES control — a behaviour-free edit must NOT kill anything =="
survives comment-reword \
  '# Root-privileged disk accounting for the workbench root filesystem.' \
  '# Root-privileged disk accounting for the workbench root filesystem. (reworded)'

echo
if [ "$RC" -eq 0 ]; then echo "mutation battery: every mutant killed by its own guard"
else echo "mutation battery: PROBLEMS above"; fi
exit "$RC"
