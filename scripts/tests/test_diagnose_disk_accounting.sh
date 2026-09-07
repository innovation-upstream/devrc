#!/usr/bin/env bash
# Regression guards for scripts/diagnose-disk-accounting.sh.
#
# WHY THIS EXISTS. That script is 282 lines of ROOT-PRIVILEGED bash with no test
# file, in a repo with no shellcheck gate. Every defect pinned below was real,
# shipped, and invisible to the merge gate — including a root COMMAND INJECTION
# reachable by any local process (via a planted /tmp directory name) and an
# E2BIG abort that silently truncated the report it exists to produce. The two
# facts compose: nothing here was ever executed by a gate, so nothing here was
# ever wrong in a way anybody could see.
#
#   run: bash scripts/tests/test_diagnose_disk_accounting.sh
#   (registered in run-tests.sh SHELL_TESTS — an unrun shell test is not a gate)
#
# 🔴 NOTHING HERE NEEDS ROOT, and that is a design constraint, not a convenience.
# The script refuses to run as non-root (rc 2) by design, so the only way to
# cover it in a gate that runs as an ordinary user is to drive the pure
# transforms and the traversal helpers directly. `diagnose-disk-accounting.sh`
# was refactored to make them callable: sourcing it defines the helpers and runs
# nothing (BASH_SOURCE[0] != $0). The header of that file marks the seam.
#
# 🔴 WHAT THIS SUITE CANNOT COVER, stated once so nobody reads it as more than
# it is: sections 1-3, 5, 6, 6b and 8 do privileged whole-filesystem
# measurement (`dumpe2fs -h /dev/nvme0n1p2`, `find / -xdev` over root-only
# trees, `findmnt /mnt/rootcheck`). Their numbers cannot be produced without
# root on a real host, so their arithmetic is UNGUARDED here. What is guarded is
# every defect the file's own comments record, all of which live in the
# transforms below.
set -uo pipefail

# 🔴 `CDPATH=` and `>/dev/null` on the `cd`: with CDPATH set in the environment
# (it is, on this host) `cd` ECHOES its target, so a bare `$(cd … && pwd)`
# captures two lines. Same reasoning as test_cleanup_disk_gate.sh, which was red
# on its first run for exactly this.
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
SCRIPT="$ROOT/scripts/diagnose-disk-accounting.sh"
FAILED=0

fail() { echo "  FAIL: $*"; FAILED=1; }
pass() { echo "  ok: $*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 🔴 Resolve the interpreter ONCE, absolutely, from the RUNNING shell. `$BASH`
# is what is executing this file; `command -v bash` would resolve through the
# stub dir `run-tests.sh` prepends to PATH for the whole gate run. A path that
# turns out unfindable dies rc 127, which reads as "the script under test
# failed" rather than "the harness broke" — the exact misreading
# test_cleanup_disk_gate.sh records.
BASH_BIN="${BASH:-}"
[ -x "$BASH_BIN" ] || { echo "  FAIL: cannot resolve the running bash"; exit 1; }

[ -f "$SCRIPT" ] || { echo "  FAIL: $SCRIPT does not exist"; exit 1; }

# assert stdout (arg 2) equals expected (arg 3), byte for byte
eq() {
  if [ "$2" = "$3" ]; then pass "$1"; else
    fail "$1"; printf '     expected: [%s]\n     actual:   [%s]\n' "$3" "$2"
  fi
}
# assert haystack (arg 2) contains needle (arg 3) as a substring
has() {
  case "$2" in *"$3"*) pass "$1" ;;
    *) fail "$1"; printf '     missing: [%s]\n     in:      [%s]\n' "$3" "$2" ;;
  esac
}
# assert haystack (arg 2) does NOT contain needle (arg 3)
lacks() {
  case "$2" in *"$3"*) fail "$1"; printf '     unexpected: [%s]\n     in: [%s]\n' "$3" "$2" ;;
    *) pass "$1" ;;
  esac
}

# --------------------------------------------------------------------------- #
echo "== 0. THE SEAM: sourcing defines helpers and MEASURES NOTHING =="
# A source that ran the report would (a) take hours and (b) make every assertion
# below a claim about a live filesystem instead of a fixture.
src_out="$(set -uo pipefail; source "$SCRIPT" 2>&1; echo "SOURCED-OK")"
eq "sourcing produces no output at all beyond the marker" "$src_out" "SOURCED-OK"

# shellcheck source=/dev/null
source "$SCRIPT"
# 🔴 The script's own `set -euo pipefail` executed during that source and is now
# ACTIVE IN THIS SHELL. Left alone, the first assertion whose command returns
# non-zero would abort the suite mid-run and report a truncated PASS list —
# which is this very script's headline failure mode, reproduced in its test.
set +e
set -uo pipefail

for fn in lsof_deleted_summary report_deleted_open_files size_breakdown \
          inode_breakdown _dev_of split_by_device report_foreign_mounts \
          report_denials; do
  if declare -F "$fn" >/dev/null; then pass "helper $fn is sourceable"
  else fail "helper $fn is NOT defined after sourcing"; fi
done

# --------------------------------------------------------------------------- #
echo "== 1. ROOT REFUSAL: an unprivileged EXECUTION must refuse, rc 2 =="
# The whole premise of the script — an unprivileged run silently skips the trees
# it exists to measure — so a soft-fail here is worse than no script.
if [ "$(id -u)" -eq 0 ]; then
  fail "this suite is running AS ROOT; the refusal check below cannot fire. Run it unprivileged."
else
  refuse_err="$("$BASH_BIN" "$SCRIPT" 2>&1 >/dev/null)"; refuse_rc=$?
  [ "$refuse_rc" -eq 2 ] && pass "non-root execution exits 2" \
    || fail "non-root execution exited $refuse_rc, expected 2"
  has "refusal names root on stderr" "$refuse_err" "must run as root"
fi

# --------------------------------------------------------------------------- #
echo "== 2. lsof column resolution — resolved from the HEADER, never an index =="
# 🔴 THE DEFECT: the original summed \$8. Under `+L1` that is NLINK, which is 0
# for every row BY THE DEFINITION of +L1, so it printed a hard `bytes=0.0 GiB`
# forever — the reassuring reading, in the section rewritten to stop emitting a
# misleading number. MEASURED on this host: \$8 -> 0.0 GiB; header-driven -> 2.5
# GiB over 2,440 rows.
#
# 🔴 TWO FIXTURES, NOT ONE, and the reason is mechanical: `lsof +L1` puts
# SIZE/OFF at column 7 here, while plain `lsof -n -P` on the SAME host emits TID
# and TASKCMD too and puts it at column 9. A single fixture cannot tell a
# header-driven lookup from a hardcoded index that happens to match it. Every
# other column in both fixtures sums to 0.0 GiB (NLINK is 0, PID/NODE are small,
# DEVICE and the text columns are non-numeric), so 2.5 is reachable ONLY from
# the right column — the fixture values are deliberately far from the constant.
L1_HEADER='COMMAND PID USER FD TYPE DEVICE SIZE/OFF NLINK NODE NAME'
L1_ROWS='bash 1234 zach 3r REG 259,8 1073741824 0 111 /tmp/gone-1 (deleted)
node 5678 zach 7u REG 259,8 1610612736 0 222 /tmp/gone-2 (deleted)'

out="$(printf '%s\n%s\n' "$L1_HEADER" "$L1_ROWS" | lsof_deleted_summary 0)"
eq "+L1 shape: SIZE/OFF found at col 7, 2.5 GiB over 2 rows" "$out" \
   "count=2 bytes=2.5 GiB (SIZE/OFF=col 7, resolved from the header)"

# The SAME two sizes, shifted two columns right by TID/TASKCMD. A fixed index
# gives 0.0 here; only a name lookup still reports 2.5.
PLAIN_HEADER='COMMAND PID TID TASKCMD USER FD TYPE DEVICE SIZE/OFF NODE NAME'
PLAIN_ROWS='bash 1234 1234 bash zach 3r REG 259,8 1073741824 111 /tmp/a
node 5678 5679 node zach 7u REG 259,8 1610612736 222 /tmp/b'
out="$(printf '%s\n%s\n' "$PLAIN_HEADER" "$PLAIN_ROWS" | lsof_deleted_summary 0)"
eq "plain -n -P shape: same bytes, col 9" "$out" \
   "count=2 bytes=2.5 GiB (SIZE/OFF=col 9, resolved from the header)"

# 🔴 REFUSAL, not a zero. A header with no SIZE/OFF at all is the version-skew
# case the name lookup exists for; answering it with a number would be worse
# than the fixed index it replaced.
NO_SIZE_HEADER='COMMAND PID USER FD TYPE DEVICE NLINK NODE NAME'
out="$(printf '%s\nbash 1 z 3r REG 259,8 0 111 /tmp/a\nnode 2 z 4r REG 259,8 0 112 /tmp/b\nsh 3 z 5r REG 259,8 0 113 /tmp/c\n' \
       "$NO_SIZE_HEADER" | lsof_deleted_summary 0)"
eq "absent SIZE/OFF refuses and names the row count" "$out" \
   "COULD NOT MEASURE: no SIZE/OFF column in lsof header (rows=3) — NOT a zero"
lacks "refusal emits NO byte figure at all" "$out" "bytes="

# 🔴 NEVER NEGATIVE. The 2026-09-01 run printed `count=-1` because the awk did
# NR-1 to drop the header and NR is 0 on empty input. A negative count also hid
# "lsof found nothing" vs "lsof did not run" — and the zero is the reassuring
# one, so the two must be told apart in WORDS, not by a number.
out="$(printf '' | lsof_deleted_summary 1)"
eq "empty input: count=0 and lsof's exit status is carried" "$out" \
   "count=0 bytes=0.0 GiB  (lsof exited 1 with no rows — no deleted-but-open files)"
lacks "empty input never prints a negative count" "$out" "count=-"

out="$(printf '%s\n' "$L1_HEADER" | lsof_deleted_summary 0)"
eq "header-only input: count=0, not -1" "$out" \
   "count=0 bytes=0.0 GiB (SIZE/OFF=col 7, resolved from the header)"

# --------------------------------------------------------------------------- #
echo "== 2b. section 7's own branches: not-on-PATH, and the set -e trap =="
# `command -v` on a name that cannot exist. This is the branch that must never
# be confused with a zero.
out="$(LSOF_BIN="lsof-does-not-exist-$$" report_deleted_open_files 2>&1)"
eq "lsof missing: COULD NOT MEASURE, not a count" "$out" \
   "COULD NOT MEASURE: lsof not on PATH — this is NOT a zero"

# 🔴 THE NO-ROWS MESSAGE USED TO BE UNREACHABLE. lsof documents exit 1 when it
# finds nothing — the ordinary case — and `OUT=$(lsof …); RC=$?` is a CHECKED
# command under `set -e`, so the script died on that line, mid-report, with the
# message already eaten by `2>/dev/null`. Sections 7 and 8 never printed and the
# run ended looking complete. This probe runs the real `set -euo pipefail`
# and asserts the script gets PAST section 7 — the marker is the whole point.
cat > "$TMP/lsof-probe.sh" <<PROBE
set -euo pipefail
source "$SCRIPT"
fake_lsof() { return 1; }   # exits non-zero with no output, exactly like lsof
LSOF_BIN=fake_lsof
report_deleted_open_files
echo "SECTION-8-WAS-REACHED"
PROBE
probe_out="$("$BASH_BIN" "$TMP/lsof-probe.sh" 2>&1)"; probe_rc=$?
[ "$probe_rc" -eq 0 ] && pass "a non-zero lsof does not kill the run (rc 0)" \
  || fail "a non-zero lsof aborted the script under set -e (rc $probe_rc)"
has "no-rows message is REACHABLE" "$probe_out" \
    "count=0 bytes=0.0 GiB  (lsof exited 1 with no rows"
has "the report continues past section 7" "$probe_out" "SECTION-8-WAS-REACHED"

# --------------------------------------------------------------------------- #
echo "== 3. ROOT COMMAND INJECTION via a planted directory name =="
# 🔴 THE DEFECT: `xargs -I{} sh -c '… "{}" …'` substitutes the filename into a
# SHELL STRING. /tmp is mode 1777 and this script's header mandates sudo, so any
# local process could plant a directory whose name is a command and have it run
# AS ROOT. `find -exec sh -c '… "$1"' _ {} \;` passes the name as an ARGUMENT.
#
# 🔴 GREP FOR THE EXPANSION, NEVER THE WORD. The fixture's own name contains the
# string `PWNED`, so grepping for that gives a guaranteed false red. The only
# thing that distinguishes execution from mere printing is `$(id -un)` having
# been REPLACED by the user's name.
INJ="$TMP/inj"
EVIL='evil";echo PWNED-AS-$(id -un) >&2;"x'
mkdir -p "$INJ/$EVIL" "$INJ/benign"
touch "$INJ/benign/f1" "$INJ/benign/f2"
WHO="$(id -un)"
EXPANSION="PWNED-AS-$WHO"

# 🔴 POSITIVE CONTROL FIRST. A "no injection observed" result is worthless
# unless this instrument has been watched to observe one. The old, vulnerable
# pipeline is run here verbatim against the SAME fixture; if it does not leak
# the expansion, the fixture is inert and every assertion below is vacuous.
vuln_out="$(find "$INJ" -xdev -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
  | xargs -I{} sh -c 'printf "%12d  %s\n" "$(find "{}" -xdev -printf . 2>/dev/null | wc -c)" "{}"' 2>&1)"
has "POSITIVE CONTROL: the OLD xargs -I{} form DOES expand \$(id -un)" \
    "$vuln_out" "$EXPANSION"

inj_out="$(inode_breakdown "$INJ" 2>&1)"
lacks "inode_breakdown does not execute the planted name" "$inj_out" "$EXPANSION"
# ... and it still does its job: the name survives VERBATIM, quotes and all.
has "the planted directory is still listed, byte-for-byte" "$inj_out" "$INJ/$EVIL"
has "the benign directory's inodes are counted" "$inj_out" "$INJ/benign"

# The size half of section 6d walks the same attacker-writable directory. Its
# 🔴 INVARIANT GUARD, labelled as one and NOT counted as regression coverage:
# `du` never starts a shell, so no mutation of `size_breakdown` short of adding
# `sh -c` could make this go red. It pins that the pipeline stays shell-free —
# real, but it is not a test of a bug that existed. The line below it IS a
# regression guard: a `du` fed a mangled name reports the wrong directory.
size_out="$(size_breakdown "$INJ" 2>&1)"
lacks "INVARIANT: size_breakdown starts no shell for the planted name" "$size_out" "$EXPANSION"
has "size_breakdown lists the planted directory verbatim" "$size_out" "$INJ/$EVIL"

# --------------------------------------------------------------------------- #
echo "== 4. E2BIG: a top-level glob aborts on a real /tmp; find -print0 does not =="
# 🔴 THE DEFECT: `du -sh -x /tmp/*` on this host. MEASURED 2026-09-02: 171,886
# top-level entries = ~4.32 MiB of argv against ARG_MAX 2,097,152, so the glob
# dies E2BIG; `2>/dev/null` swallows the message and `set -euo pipefail` then
# kills the WHOLE SCRIPT mid-section. Sections 6d-inodes, 7 and 8 never run and
# the report ends with no error — a truncated scan reported as a total, which is
# the failure this script exists to prevent.
#
# The fixture is sized from the LIVE ARG_MAX rather than a magic number, so it
# stays a real E2BIG on a host with a different limit instead of quietly
# becoming a no-op.
ARGMAX="$(getconf ARG_MAX 2>/dev/null || echo 2097152)"
PAD="$(printf 'x%.0s' $(seq 1 200))"
NAMELEN=$(( ${#TMP} + 220 ))
NEED=$(( (ARGMAX * 2 / NAMELEN) + 1 ))
BIG="$TMP/big"
mkdir -p "$BIG"
if [ "$NEED" -gt 60000 ]; then
  fail "COULD NOT MEASURE: ARG_MAX=$ARGMAX would need $NEED fixture files; refusing to build them. This guard did NOT run."
else
  seq 1 "$NEED" | awk -v d="$BIG" -v p="$PAD" '{printf "%s/%s%06d\n", d, p, $1}' | xargs -d'\n' touch
  made="$(find "$BIG" -mindepth 1 -maxdepth 1 | wc -l)"
  [ "$made" -eq "$NEED" ] && pass "fixture built: $made top-level entries (ARG_MAX=$ARGMAX)" \
    || fail "fixture build produced $made entries, wanted $NEED"

  # 🔴 POSITIVE CONTROL. If the glob does NOT die here, the fixture is under the
  # limit and the assertion below proves nothing about E2BIG.
  glob_err="$( { du -sh -x "$BIG"/* >/dev/null; } 2>&1 )"; glob_rc=$?
  [ "$glob_rc" -ne 0 ] && pass "POSITIVE CONTROL: the glob form really does die (rc $glob_rc)" \
    || fail "POSITIVE CONTROL FAILED: the glob survived $made entries — the E2BIG assertion below is vacuous"
  has "POSITIVE CONTROL names the E2BIG error" "$glob_err" "Argument list too long"

  big_out="$(size_breakdown "$BIG" 2>&1)"; big_rc=$?
  [ "$big_rc" -eq 0 ] && pass "size_breakdown survives $made entries (rc 0)" \
    || fail "size_breakdown exited $big_rc on $made entries"
  big_lines="$(printf '%s\n' "$big_out" | grep -c . )"
  [ "$big_lines" -eq 15 ] && pass "size_breakdown still returns its top-15 rows" \
    || fail "size_breakdown returned $big_lines rows, expected 15"

  # 🔴 THE SECOND ROUTE TO THE SAME FAILURE, and it is LIVE, not historical.
  # `<producer> | sort | head -N` under `pipefail` is a SIZE-DEPENDENT abort:
  # head exits after N lines, the producer blocks on its next write, takes
  # SIGPIPE and exits 141, and `set -e` kills the run with NO message at all.
  # MEASURED AT TWO POINTS on this host — 40 entries survive rc 0 (sort's whole
  # output fits one write that completes before head exits), 20,000 die rc 141 —
  # so a small-fixture test would have certified the defect as absent. The real
  # /tmp had 171,886 entries. This probe runs the script's own `set -euo
  # pipefail` over the big fixture and demands the marker AFTER it.
  cat > "$TMP/sigpipe-probe.sh" <<PROBE
set -euo pipefail
source "$SCRIPT"
size_breakdown "$BIG" >/dev/null
inode_breakdown "$BIG" >/dev/null
echo "NEXT-SECTION-WAS-REACHED"
PROBE
  sp_out="$("$BASH_BIN" "$TMP/sigpipe-probe.sh" 2>&1)"; sp_rc=$?
  [ "$sp_rc" -eq 0 ] && pass "the breakdowns do not SIGPIPE the run away (rc 0 at $made entries)" \
    || fail "the run died rc $sp_rc walking $made entries — a truncated scan with no message"
  has "the report continues past the /tmp breakdown" "$sp_out" "NEXT-SECTION-WAS-REACHED"
fi

# --------------------------------------------------------------------------- #
echo "== 5. /home split by DEVICE, and the foreign list must survive the loop =="
# 🔴 TWO DEFECTS MEET HERE.
#  (a) `du -x` does NOT mean "only this filesystem" — it stops du crossing AWAY
#      from its starting point, so started ON a foreign mount it walks the whole
#      thing. The 2026-09-01 run reported 12T for /home/zach/hdd-20tb under a
#      1.8T root filesystem, and walking 13T of external disk is what made that
#      run take ~3 hours. The fix compares `stat -c '%D'` per candidate.
#  (b) The loop was the HEAD OF A PIPELINE with a VARIABLE accumulator, so the
#      foreign list was assigned in a SUBSHELL and read back empty — the report
#      printed a confident "none" on a host with foreign mounts under /home.
#      SC2030/SC2031; no shellcheck gate here, so nothing caught it.
#
# The real-`stat` check runs FIRST, on purpose: the fixture cases below replace
# `_dev_of`, and if that replacement came first this suite would be asserting
# against its own stub and would pass with the script's implementation deleted.
real_dev="$(stat -c '%D' / 2>/dev/null)"
eq "_dev_of / agrees with stat -c %D /" "$(_dev_of /)" "$real_dev"
mkdir -p "$TMP/sibling"
eq "_dev_of is stable for two paths on one filesystem" \
   "$(_dev_of "$TMP/sibling")" "$(stat -c '%D' "$TMP")"

HOMEFIX="$TMP/homefix"
mkdir -p "$HOMEFIX/zach/onroot-a" "$HOMEFIX/zach/foreign-b" \
         "$HOMEFIX/zach/.onroot-hidden" "$HOMEFIX/other/foreign-c"
touch "$HOMEFIX/zach/a-plain-file"
# Fixture device map. `801` is the real device id of /dev/sda1 on the host this
# defect was measured on; `10308` is root's. Distinct from each other and from
# any value `stat` could return for these paths, so a mutant that dropped the
# comparison and kept everything (or dropped everything) is visibly wrong.
_dev_of() {
  case "$1" in
    */foreign-b|*/foreign-c) printf '801\n' ;;
    *) printf '10308\n' ;;
  esac
}
ON="$TMP/onroot.lst"; FG="$TMP/foreign.lst"
split_by_device "$HOMEFIX" "$ON" "$FG" "10308"

# 🔴 `LC_ALL=C`. Under the host's en_US.UTF-8 collation `sort` ignores leading
# punctuation, so `.onroot-hidden` sorts AFTER `onroot-a`; under the C locale
# the nix build sandbox uses, it sorts before. Left to the ambient locale this
# comparison passes on one tier and fails on the other — the config-blind-suite
# failure, in the harness rather than the subject.
on_list="$(tr '\0' '\n' < "$ON" | LC_ALL=C sort)"
fg_list="$(LC_ALL=C sort < "$FG")"
eq "on-root list holds exactly the root-device dirs (hidden included)" "$on_list" \
   "$(printf '%s\n%s\n' "$HOMEFIX/zach/.onroot-hidden" "$HOMEFIX/zach/onroot-a")"
eq "foreign list holds exactly the foreign-device dirs" "$fg_list" \
   "$(printf '%s\n%s\n' "$HOMEFIX/other/foreign-c" "$HOMEFIX/zach/foreign-b")"
lacks "a plain FILE is not treated as a directory" "$on_list$fg_list" "a-plain-file"
# The two `eq`s above are also the `.`/`..` guard: without the case-skip, the
# `/home/*/.*` half of the glob puts `$HOMEFIX/zach/.` and `$HOMEFIX/zach/..`
# (both real directories) into the on-root list and both comparisons go red.
# NUL separation is what makes `xargs -0` safe for names with spaces/newlines.
nul_count="$(tr -dc '\0' < "$ON" | wc -c)"
[ "$nul_count" -eq 2 ] && pass "on-root list is NUL-separated (2 records)" \
  || fail "on-root list has $nul_count NUL separators, expected 2"

# 🔴 THE ACCUMULATOR GUARD, stated behaviourally: with foreign mounts present the
# report must NAME them. "none found" here was the subshell bug, and it is the
# reassuring reading.
fm_out="$(report_foreign_mounts "$FG" 2>/dev/null)"
has "foreign report names the first foreign mount" "$fm_out" "$HOMEFIX/zach/foreign-b"
has "foreign report names the second foreign mount" "$fm_out" "$HOMEFIX/other/foreign-c"
lacks "foreign report does NOT claim 'none found'" "$fm_out" "none found"

# POSITIVE CONTROL for the branch above: it CAN say none, so the `lacks` is not
# an assertion about a branch that never fires.
: > "$TMP/empty.lst"
none_out="$(report_foreign_mounts "$TMP/empty.lst" 2>/dev/null)"
has "POSITIVE CONTROL: an empty list does print the loud 'none' line" \
    "$none_out" "none found — on a host with foreign mounts under /home this is a BUG"

# --------------------------------------------------------------------------- #
echo "== 6. denial accounting — a count with no denial figure is a FLOOR =="
# 🔴 DEFECT: `grep -c` prints `0` AND exits 1, so `$(grep -c … || echo 0)` yields
# a TWO-LINE "0\n0" and every later integer test dies with "integer expected" —
# a guard that fails precisely when it has something to report.
: > "$TMP/denied-empty.log"
d_out="$(report_denials "$TMP/denied-empty.log" 2>"$TMP/d.err")"; d_rc=$?
d_err="$(cat "$TMP/d.err")"
[ "$d_rc" -eq 0 ] && pass "empty denial log: rc 0" || fail "empty denial log: rc $d_rc"
eq "empty denial log prints three clean zero lines" "$d_out" \
   "$(printf 'directories find could not read      : 0\nvanished mid-scan (benign, transient): 0\nOTHER, unclassified                  : 0')"
eq "empty denial log writes NOTHING to stderr" "$d_err" ""
lacks "empty denial log does not trip the broken-counter guard" "$d_out" "denial counter is broken"

cat > "$TMP/denied.log" <<'EOF'
find: ‘/var/lib/kubelet/pods/x’: Permission denied
find: ‘/var/lib/docker/overlay2/y’: Permission denied
find: ‘/root/.cache/z’: Permission denied
find: ‘/tmp/vanished-1’: No such file or directory
find: ‘/tmp/vanished-2’: No such file or directory
find: ‘/proc/9999/task’: Input/output error
EOF
d_out="$(report_denials "$TMP/denied.log" 2>&1)"
has "3 denials counted" "$d_out" "directories find could not read      : 3"
has "2 transient vanishings counted" "$d_out" "vanished mid-scan (benign, transient): 2"
has "1 unclassified error counted" "$d_out" "OTHER, unclassified                  : 1"
has "denials make the counts FLOORS, loudly" "$d_out" \
    "!! Running as root and STILL denied — the counts above are FLOORS, not totals."
has "the denied paths are listed, not just counted" "$d_out" "/var/lib/kubelet/pods/x"
has "the unclassified error is surfaced verbatim" "$d_out" "Input/output error"
lacks "a known-benign line is not reported as unclassified" "$d_out" \
      "$(printf -- '-- unclassified errors (read these; they are not known-benign) --\nfind: ‘/tmp/vanished-1’')"

# POSITIVE CONTROL: the broken-counter branch CAN fire, so the `lacks` above is
# not an assertion about dead code. A missing log makes `grep -c` print nothing.
d_out="$(report_denials "$TMP/no-such-log-$$" 2>/dev/null)"
has "POSITIVE CONTROL: an unreadable log trips the broken-counter guard" \
    "$d_out" "!! denial counter is broken — treat every count above as UNVERIFIED"

# --------------------------------------------------------------------------- #
echo "== 7. SOURCE GUARDS — the anti-patterns must not reappear ANYWHERE =="
# These are structural, and they are deliberately a second net rather than the
# primary one: a behavioural guard covers the helper it was extracted from, but
# the script has six more traversals a future edit could rebuild the same defect
# in. Each pin names the whole normalised expression, not a keyword.
# 🔴 COMMENTS ARE STRIPPED FIRST. Every defect below is DESCRIBED in a comment
# in the script ("NOT `du -sh -x /tmp/*`", "NOT `xargs -I{} …`"), so a scan over
# the raw file matches its own documentation and reports the defect present in a
# clean file — a guaranteed red that would be "fixed" by deleting the comments.
# Only whole-line comments are removed; a trailing comment on a code line stays,
# which errs toward a false red rather than a false green.
#
# 🔴 THE STRIPPED SOURCE GOES TO A FILE, AND THE SCANS READ THE FILE. The obvious
# `printf '%s\n' "$CODE" | grep -q …` is a RACE under `pipefail`: `grep -q` exits
# on its FIRST match, and if it wins that race `printf` takes SIGPIPE, the
# pipeline reports 141 and the `if` reads a successful match as "not found".
# Measured here — the same unmodified tree returned 71 ok on one run and one
# spurious FAIL ("the script no longer contains …") on the next. A flaky guard
# whose failure mode is "the thing you are looking for is missing" is worse than
# no guard: it trains a reader to re-run until green. No pipe, no race.
CODE_FILE="$TMP/code-no-comments.txt"
grep -v '^[[:space:]]*#' "$SCRIPT" > "$CODE_FILE"

# 🔴 POSITIVE CONTROL FOR THE SCANNER ITSELF. A `banned` check that can never
# match is indistinguishable from a clean file, and both print "ok". Feed it a
# line that MUST match every banned pattern before trusting a single zero.
CANARY_FILE="$TMP/canary.txt"
printf '%s\n' 'du -sh -x /tmp/* | xargs -I{} awk "{ s += $8 } END { print NR - 1 }" | head -15; X=$(lsof +L1); Y=$(grep -c foo bar || echo 0)' > "$CANARY_FILE"

banned() { # name  BRE  why
  local name="$1" pat="$2" why="$3"
  if ! grep -q -- "$pat" "$CANARY_FILE"; then
    fail "$name — SCANNER BROKEN: the pattern [$pat] does not even match the canary line, so its zero means nothing"
    return
  fi
  if grep -q -- "$pat" "$CODE_FILE"; then
    fail "$name — matched [$pat] in code: $why"
  else
    pass "$name"
  fi
}
required() { # name  fixed-string  why
  if grep -qF -- "$2" "$CODE_FILE"; then pass "$1"
  else fail "$1 — the script no longer contains [$2]: $3"; fi
}

# 🔴 The field separator is `@`, NOT `|` — one of the banned patterns is itself
# a `||` alternation guard and splitting on `|` shredded it into three fields on
# this table's first run, which made the pattern a prefix that matched nothing
# and reported the check as ok.
while IFS='@' read -r name pat why; do
  [ -n "${name:-}" ] || continue
  banned "$name" "$pat" "$why"
done <<'BANNED'
no 'xargs -I' anywhere (root command injection)@xargs -I@-I substitutes a filename into a SHELL STRING; /tmp is 1777 and this script demands sudo
no glob expanded into du (E2BIG)@du [^#]*\*@a top-level glob over /tmp is ~4.3 MiB of argv against ARG_MAX 2097152
no fixed column index summed out of lsof@s *+= *\$[0-9]@$8 under +L1 is NLINK, 0 by definition — a hard 0.0 GiB forever
no NR-1 header strip (goes to -1 on empty input)@NR *- *1@NR is 0 with no output at all, and a negative count hides 'did not run'
no 'grep -c ... || echo 0' (emits a two-line zero)@grep -c[^|]*|| *echo@grep -c prints 0 AND exits 1; the fallback then appends a second line
no unchecked assignment from lsof under set -e@=\$(lsof@a command substitution in an assignment is CHECKED; lsof exits 1 when it finds nothing
no bare '| head -N' truncating a pipeline (SIGPIPE 141)@| *head -[0-9]@head exits after N lines and the producer takes SIGPIPE; pipefail promotes 141 and set -e kills the run silently. Use head_n.
BANNED

while IFS='@' read -r name lit why; do
  [ -n "${name:-}" ] || continue
  required "$name" "$lit" "$why"
done <<'REQUIRED'
section 2 keeps find's stderr instead of discarding it@2>>"$DENIED_LOG"@a scan reporting a number with no denial count is a floor presented as a total
lsof column is resolved by NAME from the header@if ($i == "SIZE/OFF") col=i@the index is not stable: +L1 puts it at 7, plain -n -P at 9
an absent SIZE/OFF column REFUSES rather than reporting a zero@COULD NOT MEASURE: no SIZE/OFF column in lsof header@a zero here is the reassuring reading of a broken instrument
the /tmp inode walk passes the name as an ARGUMENT@-exec sh -c 'printf "%12d  %s\n" "$(find "$1" -xdev -printf . 2>/dev/null | wc -c)" "$1"' _ {} \;@"$1" is an argv slot; "{}" is text the shell parses
top-level enumeration is NUL-safe find|xargs, not a glob@find "$1" -xdev -mindepth 1 -maxdepth 1 -print0@the glob form dies E2BIG and set -euo pipefail takes the whole run with it
candidates are compared by DEVICE, because du -x cannot do it@d=$(_dev_of "$p") || continue@du -x only stops du crossing AWAY from its start; started ON a foreign mount it walks all of it
truncation reads to EOF instead of closing the pipe@head_n() { awk -v n="$1" 'NR<=n'; }@awk consumes all input, so the upstream sort never takes SIGPIPE
REQUIRED

# --------------------------------------------------------------------------- #
# 🔴 DO NOT print `RESULT: PASS (exit=0)` here — that grammar is RESERVED to
# run-tests.sh's EXIT trap, and a second writer of it lets a red run report green
# through the gate's own truth-telling channel. See test_result_grammar_is_reserved.py.
echo
if [ "$FAILED" -eq 0 ]; then
  echo "diagnose-disk-accounting guards: all checks passed"; exit 0
else
  echo "diagnose-disk-accounting guards: FAILURES above"; exit 1
fi
