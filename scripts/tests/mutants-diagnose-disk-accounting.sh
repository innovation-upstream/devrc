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
run e2big-glob-expanded-into-du \
  'size_breakdown exited' \
  '  { find "$base" -xdev -mindepth 1 -maxdepth 1 -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true; } \
    | _on_device "$dev" \
    | { xargs -0 -r du -sh -x 2>/dev/null || true; } \
    | sort -rh | head_n 15' \
  '  du -sh -x "$base"/* 2>/dev/null | sort -rh | head_n 15'

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
run find-error-aborts-the-run \
  'a failing find/du aborted the run' \
  '  { find "$base" -xdev -mindepth 1 -maxdepth 1 -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true; } \
    | _on_device "$dev" \
    | { xargs -0 -r du -sh -x 2>/dev/null || true; } \' \
  '  find "$base" -xdev -mindepth 1 -maxdepth 1 -printf '"'"'%D\t%p\0'"'"' 2>/dev/null \
    | _on_device "$dev" \
    | { xargs -0 -r du -sh -x 2>/dev/null || true; } \'

run du-failure-aborts-the-run \
  'a failing find/du aborted the run' \
  '    | { xargs -0 -r du -sh -x 2>/dev/null || true; } \
    | sort -rh | head_n 15' \
  '    | xargs -0 -r du -sh -x 2>/dev/null \
    | sort -rh | head_n 15'

run inode-find-error-aborts-the-run \
  'a failing find/du aborted the run' \
  '  { find "$base" -xdev -mindepth 1 -maxdepth 1 -type d -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true; } \' \
  '  find "$base" -xdev -mindepth 1 -maxdepth 1 -type d -printf '"'"'%D\t%p\0'"'"' 2>/dev/null \'

# --- defect 7, the /tmp half: -xdev LISTS a foreign mountpoint at depth 1 -----
run tmp-size-device-filter-dropped \
  'size_breakdown DROPS every entry on a foreign device' \
  '  { find "$base" -xdev -mindepth 1 -maxdepth 1 -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true; } \
    | _on_device "$dev" \' \
  '  { find "$base" -xdev -mindepth 1 -maxdepth 1 -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true; } \
    | sed -z -n '"'"'s/^[0-9]*\t//p'"'"' \'

run tmp-inode-device-filter-dropped \
  'inode_breakdown DROPS every directory on a foreign device' \
  '  { find "$base" -xdev -mindepth 1 -maxdepth 1 -type d -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true; } \
    | _on_device "$dev" \' \
  '  { find "$base" -xdev -mindepth 1 -maxdepth 1 -type d -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true; } \
    | sed -z -n '"'"'s/^[0-9]*\t//p'"'"' \'

# 🔴 STRUCTURAL PIN, and it is labelled as one. A second filesystem needs root,
# so `du -x` cannot be checked behaviourally in this suite; a mutant dropping it
# SURVIVED before the `required` row was added.
run du-x-dropped-from-the-tmp-breakdown \
  'du keeps -x in the /tmp size breakdown' \
  '    | { xargs -0 -r du -sh -x 2>/dev/null || true; } \' \
  '    | { xargs -0 -r du -sh 2>/dev/null || true; } \'

run du-x-dropped-from-the-home-breakdown \
  'du keeps -x in the /home size breakdown' \
  '{ xargs -0 -r du -sh -x < "$ONROOT_LIST" 2>/dev/null || true; }' \
  '{ xargs -0 -r du -sh < "$ONROOT_LIST" 2>/dev/null || true; }'

# The device READING itself: hex vs decimal is a silent total mis-classification.
run dev-of-returns-hex-not-decimal \
  '_dev_of / agrees with stat -c %d / (decimal)' \
  "_dev_of() { stat -c '%d' \"\$1\" 2>/dev/null; }" \
  "_dev_of() { stat -c '%D' \"\$1\" 2>/dev/null; }"

# An empty device reading must REFUSE, not print an empty section.
run absent-device-prints-an-empty-section \
  'an unreadable base REFUSES rather than printing an empty size section' \
  '    echo "COULD NOT MEASURE: no device id for $base — NOT an empty directory"
    return 0
  fi
  { find "$base" -xdev -mindepth 1 -maxdepth 1 -printf' \
  '    return 0
  fi
  { find "$base" -xdev -mindepth 1 -maxdepth 1 -printf'

# 🔴 The device filter's own blind spot: excluding a foreign mount is right,
# excluding it SILENTLY is the failure this file catalogues. Section 6c prints
# the same listing for /home; 6d had no equivalent until the filter created the
# need for one.
run foreign-entries-lists-nothing \
  '6d NAMES the entries it excluded (first)' \
  '  if [ -n "$out" ]; then
    printf '"'"'%s\n'"'"' "$out" | sed '"'"'s/^/  /'"'"'' \
  '  if false; then
    printf '"'"'%s\n'"'"' "$out" | sed '"'"'s/^/  /'"'"''

run foreign-entries-inverted-filter \
  '_not_on_device is the exact complement of _on_device' \
  '_not_on_device() { sed -z -n "/^$1\t/!{s/^[0-9]*\t//;p;}"; }' \
  '_not_on_device() { sed -z -n "s/^[0-9]*\t//p"; }'

run foreign-entries-none-branch-always \
  'POSITIVE CONTROL: an all-on-device base prints the explicit' \
  '  out=$({ find "$base" -xdev -mindepth 1 -maxdepth 1 -printf '"'"'%D\t%p\0'"'"' 2>/dev/null || true; } \
        | _not_on_device "$dev" | tr '"'"'\0'"'"' '"'"'\n'"'"' | head_n 15)' \
  '  out=$(printf '"'"'%s'"'"' "always-something")'

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

echo
echo "== SURVIVES control — a behaviour-free edit must NOT kill anything =="
survives comment-reword \
  '# Root-privileged disk accounting for the workbench root filesystem.' \
  '# Root-privileged disk accounting for the workbench root filesystem. (reworded)'

echo
if [ "$RC" -eq 0 ]; then echo "mutation battery: every mutant killed by its own guard"
else echo "mutation battery: PROBLEMS above"; fi
exit "$RC"
