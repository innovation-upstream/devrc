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
# 🔴 `chmod -R u+rwX` BEFORE the `rm -rf`. Section 4b deliberately builds
# unreadable fixture directories (mode 0400 / 000) so that `find` and `du` fail
# for real, and `rm -rf` cannot unlink through them. Without this the temp tree
# survives every run that dies before the fixture is chmodded back — a leak that
# looks like nothing and accumulates.
trap 'chmod -R u+rwX "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT

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
          inode_breakdown _dev_of _dev_is_valid _on_device _not_on_device \
          foreign_entries split_by_device report_foreign_mounts \
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

  # ------------------------------------------------------------------------- #
  echo "== 1b. THE SEAM IS NOT REACHABLE FROM THE ENVIRONMENT =="
  # 🔴 THE DEFECT: the seam used to be `[ "${BASH_SOURCE[0]}" != "$0" ]`, and the
  # script's own header asserted that "the guard is not reachable from the
  # environment". MEASURED FALSE. bash imports BASH_SOURCE from the environment
  # as an ordinary scalar, so a stray `BASH_SOURCE` in the env made an EXECUTED
  # run take the sourced branch and `return` at top level — rc 2, no report.
  # rc 2 is also this script's "you forgot sudo" status, so the two causes were
  # indistinguishable to the operator. `bash < script` was the mirror image:
  # BASH_SOURCE unset under `set -u`, dead on the guard's own line, rc 1.
  #
  # 🔴 EACH CASE ASSERTS THE STATUS **AND** THE MESSAGE. rc 2 alone cannot tell
  # "refused because not root" from "returned at top level", which is the whole
  # reason this defect was invisible — so the refusal text is what discriminates.
  env_err="$(env 'BASH_SOURCE=(nope)' "$BASH_BIN" "$SCRIPT" 2>&1 >/dev/null)"; env_rc=$?
  [ "$env_rc" -eq 2 ] && pass "a poisoned BASH_SOURCE still reaches the root refusal (rc 2)" \
    || fail "env BASH_SOURCE=... execution exited $env_rc, expected 2"
  has "the poisoned run refuses for the RIGHT reason" "$env_err" "must run as root"
  lacks "the poisoned run does not 'return' at top level" "$env_err" "can only 'return'"

  # `bash < FILE` and `bash -s < FILE`: BASH_SOURCE is UNSET, so the old guard
  # died on `set -u` before printing anything. Base ran fine this way, which
  # makes this a genuine (narrow) regression the old form introduced.
  stdin_err="$("$BASH_BIN" < "$SCRIPT" 2>&1 >/dev/null)"; stdin_rc=$?
  [ "$stdin_rc" -eq 2 ] && pass "bash < script reaches the root refusal (rc 2)" \
    || fail "bash < script exited $stdin_rc, expected 2"
  has "the stdin run refuses for the RIGHT reason" "$stdin_err" "must run as root"
  lacks "the stdin run does not die on an unbound BASH_SOURCE" "$stdin_err" "unbound variable"

  dash_s_err="$("$BASH_BIN" -s < "$SCRIPT" 2>&1 >/dev/null)"; dash_s_rc=$?
  [ "$dash_s_rc" -eq 2 ] && pass "bash -s < script reaches the root refusal (rc 2)" \
    || fail "bash -s < script exited $dash_s_rc, expected 2"

  # 🔴 THE OTHER HALF, or the fix above would be satisfied by a seam that never
  # takes the sourced branch at all — which would silently make every helper
  # assertion in this file a claim about a script that had already run the whole
  # report. A poisoned BASH_SOURCE must NOT stop a genuine `source` working.
  poisoned_src="$(env 'BASH_SOURCE=(nope)' "$BASH_BIN" -c "source '$SCRIPT' 2>&1; echo STILL-SOURCES")"
  eq "a genuine source still no-ops, poisoned environment and all" \
     "$poisoned_src" "STILL-SOURCES"
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
echo "== 2c. LSOF_BIN is a SOURCED-ONLY seam; a root run must not inherit it =="
# 🔴 THE DEFECT: `LSOF_BIN=${LSOF_BIN:-lsof}` sat ABOVE the seam, so it was
# honoured on the EXECUTE path too — an inherited `LSOF_BIN=/anything` was what
# a ROOT run would then execute. Base hardcoded `lsof`. In a script whose whole
# premise is that a local unprivileged process must not be able to influence a
# root run, a TEST seam that widens what root executes is backwards.
#
# 🔴 OBSERVED THROUGH `bash -x`, because the execute path exits 2 at the root
# check before it prints anything of its own. xtrace shows the assignment as it
# is evaluated, so this is the REAL script executed exactly as an operator would
# (minus sudo) — not a copy, not a stub.
PWNED_LSOF="/pwned-lsof-$$"
xt="$(env "LSOF_BIN=$PWNED_LSOF" "$BASH_BIN" -x "$SCRIPT" 2>&1 >/dev/null)"
has "the execute path pins LSOF_BIN=lsof" "$xt" "LSOF_BIN=lsof"
lacks "the execute path DISCARDS an inherited LSOF_BIN" "$xt" "$PWNED_LSOF"

# 🔴 POSITIVE CONTROL. Without it, "the override is ignored" would also be
# satisfied by a seam that ignores it EVERYWHERE — which would quietly make
# section 2b's fake-lsof probe untestable rather than fixed.
srcd_lsof="$(env "LSOF_BIN=$PWNED_LSOF" "$BASH_BIN" -c "source '$SCRIPT'; printf '%s' \"\${LSOF_BIN}\"")"
eq "POSITIVE CONTROL: the SOURCED path still honours LSOF_BIN" "$srcd_lsof" "$PWNED_LSOF"
srcd_default="$("$BASH_BIN" -c "unset LSOF_BIN; source '$SCRIPT'; printf '%s' \"\${LSOF_BIN}\"")"
eq "the sourced path defaults to lsof when nothing overrides it" "$srcd_default" "lsof"

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
  # 🔴 THE FIXTURE MUST CONTAIN DIRECTORIES, and it used not to. It was
  # `touch`ed FILES only, and `inode_breakdown` filters `-type d` — so it
  # returned ZERO rows over this fixture, the `inode_breakdown "$BIG"` call in
  # the SIGPIPE probe below measured nothing, and the whole battery row was
  # killed by `size_breakdown` alone. Vacuous, and it read as coverage.
  #
  # 600 is not arbitrary: each row is ~12 + 2 + ~220 bytes, so 600 rows is
  # ~140 KiB of `sort` output against a 64 KiB pipe buffer. Under 258 rows the
  # whole output fits one write that completes before `head` exits and the
  # SIGPIPE abort does NOT fire — the same size-dependence measured for
  # `size_breakdown` at 40 vs 20,000 entries. A fixture under the buffer would
  # certify the defect absent.
  NDIRS=600
  seq 1 "$NDIRS" | awk -v d="$BIG" -v p="$PAD" '{printf "%s/%sd%05d\n", d, p, $1}' | xargs -d'\n' mkdir -p
  made="$(find "$BIG" -mindepth 1 -maxdepth 1 | wc -l)"
  want=$(( NEED + NDIRS ))
  [ "$made" -eq "$want" ] && pass "fixture built: $made top-level entries, $NDIRS of them directories (ARG_MAX=$ARGMAX)" \
    || fail "fixture build produced $made entries, wanted $want"

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

  # 🔴 NON-VACUITY CHECK FOR THE FIXTURE ITSELF, not for the code. If this ever
  # reads 0, every `inode_breakdown "$BIG"` in this file is measuring an empty
  # pipeline and says nothing about `inode_breakdown`.
  ino_out="$(inode_breakdown "$BIG" 2>&1)"; ino_rc=$?
  [ "$ino_rc" -eq 0 ] && pass "inode_breakdown survives $made entries (rc 0)" \
    || fail "inode_breakdown exited $ino_rc on $made entries"
  ino_lines="$(printf '%s\n' "$ino_out" | grep -c . )"
  [ "$ino_lines" -eq 15 ] && pass "inode_breakdown returns its top-15 rows over the big fixture" \
    || fail "inode_breakdown returned $ino_lines rows, expected 15 — the fixture has no directories, so every inode_breakdown call over it is VACUOUS"

  # 🔴 THE SECOND ROUTE TO THE SAME FAILURE, and it is LIVE, not historical.
  # `<producer> | sort | head -N` under `pipefail` is a SIZE-DEPENDENT abort:
  # head exits after N lines, the producer blocks on its next write, takes
  # SIGPIPE and exits 141, and `set -e` kills the run with NO message at all.
  # MEASURED AT TWO POINTS on this host — 40 entries survive rc 0 (sort's whole
  # output fits one write that completes before head exits), 20,000 die rc 141 —
  # so a small-fixture test would have certified the defect as absent. The real
  # /tmp had 171,886 entries. This probe runs the script's own `set -euo
  # pipefail` over the big fixture and demands the marker AFTER it.
  # 🔴 TWO PROBES, ONE PER BREAKDOWN. A single probe calling both would let
  # `size_breakdown` kill every mutant on its own and leave `inode_breakdown`'s
  # own truncation unguarded — which is exactly what the combined probe did
  # while the fixture held no directories.
  cat > "$TMP/sigpipe-probe.sh" <<PROBE
set -euo pipefail
source "$SCRIPT"
size_breakdown "$BIG" >/dev/null
echo "NEXT-SECTION-WAS-REACHED"
PROBE
  sp_out="$("$BASH_BIN" "$TMP/sigpipe-probe.sh" 2>&1)"; sp_rc=$?
  [ "$sp_rc" -eq 0 ] && pass "the size breakdown does not SIGPIPE the run away (rc 0 at $made entries)" \
    || fail "the run died rc $sp_rc walking $made entries — a truncated scan with no message"
  has "the report continues past the /tmp size breakdown" "$sp_out" "NEXT-SECTION-WAS-REACHED"

  cat > "$TMP/sigpipe-inode-probe.sh" <<PROBE
set -euo pipefail
source "$SCRIPT"
inode_breakdown "$BIG" >/dev/null
echo "INODE-SECTION-WAS-REACHED"
PROBE
  ip_out="$("$BASH_BIN" "$TMP/sigpipe-inode-probe.sh" 2>&1)"; ip_rc=$?
  [ "$ip_rc" -eq 0 ] && pass "the inode breakdown does not SIGPIPE the run away (rc 0 over $NDIRS directories)" \
    || fail "the inode breakdown died rc $ip_rc over $NDIRS directories — a truncated scan with no message"
  has "the report continues past the /tmp inode breakdown" "$ip_out" "INODE-SECTION-WAS-REACHED"
fi

# --------------------------------------------------------------------------- #
echo "== 4b. a find or a du that FAILS mid-scan must not kill the run =="
# 🔴 THE THIRD AND FOURTH ROUTES to the same truncated-scan failure, and both
# are LIVE on the host this script targets, where /tmp holds ~270,000 churning
# top-level entries and section 4's own counter already calls the resulting
# ENOENTs "vanished mid-scan (benign, transient)".
#   (a) GNU `find` exits 1 when an entry disappears between readdir and stat.
#   (b) `xargs` exits 123 when ANY command it ran exited 1-125 — the same
#       vanished entry, one step later, reached through `du`.
# `2>/dev/null` eats the message, `pipefail` promotes the status and `set -e`
# kills the run: sections 6d-inodes, 7 and 8 never print and the report ends
# with NO error. Same failure as E2BIG and SIGPIPE, two more ways in.
#
# 🔴 DETERMINISTIC FIXTURES — no background deleter, so no flake:
#   ERRDIR is mode 0400. readdir succeeds (r) but stat of each entry needs x, so
#   `find` errors per entry and exits 1. Isolates route (a).
#   LOCKDIR holds a mode-000 subdirectory. `find` stats it fine and exits 0;
#   `du` cannot read it and exits 1, so `xargs` exits 123 — while the readable
#   sibling still produces a row. Isolates route (b), WITH partial output.
# Both depend on not being root, which section 1 already refuses to paper over,
# and each carries its own positive control below.
ERRDIR="$TMP/find-errors"
mkdir -p "$ERRDIR/sub-a" "$ERRDIR/sub-b"; touch "$ERRDIR/f"
chmod 400 "$ERRDIR"
LOCKDIR="$TMP/du-errors"
mkdir -p "$LOCKDIR/locked" "$LOCKDIR/open"; touch "$LOCKDIR/open/f"
chmod 000 "$LOCKDIR/locked"

find "$ERRDIR" -xdev -mindepth 1 -maxdepth 1 -printf '%D\t%p\0' >/dev/null 2>&1; a_rc=$?
[ "$a_rc" -ne 0 ] && pass "POSITIVE CONTROL (a): find really exits $a_rc on the 0400 fixture" \
  || fail "POSITIVE CONTROL (a) FAILED: find exited 0 over the 0400 fixture — every route-(a) assertion below is vacuous"
du -sh -x "$LOCKDIR/locked" >/dev/null 2>&1; b_rc=$?
[ "$b_rc" -ne 0 ] && pass "POSITIVE CONTROL (b): du really exits $b_rc on the mode-000 subdirectory" \
  || fail "POSITIVE CONTROL (b) FAILED: du exited 0 on an unreadable directory — every route-(b) assertion below is vacuous"

cat > "$TMP/finderr-probe.sh" <<PROBE
set -euo pipefail
source "$SCRIPT"
size_breakdown "$ERRDIR" >/dev/null
echo "PAST-SIZE-OVER-A-FAILING-FIND"
inode_breakdown "$ERRDIR" >/dev/null
echo "PAST-INODE-OVER-A-FAILING-FIND"
size_breakdown "$LOCKDIR" >/dev/null
echo "PAST-SIZE-OVER-A-FAILING-DU"
inode_breakdown "$LOCKDIR" >/dev/null
echo "PAST-INODE-OVER-A-FAILING-DU"
PROBE
fe_out="$("$BASH_BIN" "$TMP/finderr-probe.sh" 2>&1)"; fe_rc=$?
# 🔴 The FAIL text must not share a prefix with section 4's SIGPIPE probe
# ("the run died rc …"): the mutation battery scores each row on whether ITS OWN
# guard's FAIL line appeared, matched as a fixed-string prefix, so two guards
# with one prefix would let a mutant be credited to the wrong one.
[ "$fe_rc" -eq 0 ] && pass "a failing find/du does not abort the run (rc 0)" \
  || fail "a failing find/du aborted the run, rc $fe_rc — a truncated scan with no message"
has "route (a): the report continues past a failing find in size_breakdown" "$fe_out" "PAST-SIZE-OVER-A-FAILING-FIND"
has "route (a): the report continues past a failing find in inode_breakdown" "$fe_out" "PAST-INODE-OVER-A-FAILING-FIND"
has "route (b): the report continues past a failing du in size_breakdown" "$fe_out" "PAST-SIZE-OVER-A-FAILING-DU"
has "route (b): the report continues past a failing du in inode_breakdown" "$fe_out" "PAST-INODE-OVER-A-FAILING-DU"

# 🔴 SURVIVING IS NOT ENOUGH — the readable entries must still be REPORTED, or
# the tolerance would have turned one truncated scan into another.
lock_out="$(size_breakdown "$LOCKDIR" 2>/dev/null)"
has "route (b): the readable sibling still produces a row" "$lock_out" "$LOCKDIR/open"
chmod 700 "$ERRDIR"; chmod 700 "$LOCKDIR/locked"

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
#
# 🔴 DECIMAL, NOT HEX. `_dev_of` is `stat -c '%d'` and the /tmp breakdowns
# compare it against `find -printf '%D'`, which is decimal. `stat -c '%D'` is
# HEX: had the helper kept it, every candidate would compare unequal and both
# breakdowns would print an EMPTY section — the reassuring reading of a broken
# instrument. So this checks the FORMAT, not just that the helper answers.
real_dev="$(stat -c '%d' / 2>/dev/null)"
eq "_dev_of / agrees with stat -c %d / (decimal)" "$(_dev_of /)" "$real_dev"
eq "_dev_of agrees with find's own %D for the same path" \
   "$(_dev_of "$TMP")" "$(find "$TMP" -maxdepth 0 -printf '%D')"
mkdir -p "$TMP/sibling"
eq "_dev_of is stable for two paths on one filesystem" \
   "$(_dev_of "$TMP/sibling")" "$(stat -c '%d' "$TMP")"

HOMEFIX="$TMP/homefix"
mkdir -p "$HOMEFIX/zach/onroot-a" "$HOMEFIX/zach/foreign-b" \
         "$HOMEFIX/zach/.onroot-hidden" "$HOMEFIX/other/foreign-c"
touch "$HOMEFIX/zach/a-plain-file"
# Fixture device map, in the DECIMAL form `_dev_of` really returns. The two
# values are distinct from each other and far outside the range `stat` could
# return for a real path here, so a mutant that dropped the comparison and kept
# everything (or dropped everything) is visibly wrong. `999000001` is the
# stand-in for a foreign disk, `999000002` for root.
FOREIGN_DEV=999000001
ROOTISH_DEV=999000002
_dev_of() {
  case "$1" in
    */foreign-b|*/foreign-c) printf '%s\n' "$FOREIGN_DEV" ;;
    *) printf '%s\n' "$ROOTISH_DEV" ;;
  esac
}
ON="$TMP/onroot.lst"; FG="$TMP/foreign.lst"
split_by_device "$HOMEFIX" "$ON" "$FG" "$ROOTISH_DEV"

# 🔴 `LC_ALL=C`. Under the host's en_US.UTF-8 collation `sort` ignores leading
# punctuation, so `.onroot-hidden` sorts AFTER `onroot-a`; under the C locale
# the nix build sandbox uses, it sorts before. Left to the ambient locale this
# comparison passes on one tier and fails on the other — the config-blind-suite
# failure, in the harness rather than the subject.
on_list="$(tr '\0' '\n' < "$ON" | LC_ALL=C sort)"
fg_list="$(tr '\0' '\n' < "$FG" | LC_ALL=C sort)"
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
fg_nul_count="$(tr -dc '\0' < "$FG" | wc -c)"
[ "$fg_nul_count" -eq 2 ] && pass "foreign list is NUL-separated too (2 records)" \
  || fail "foreign list has $fg_nul_count NUL separators, expected 2"

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
echo "== 5b. a directory name with a NEWLINE is ONE foreign record, not two =="
# 🔴 THE DEFECT: `split_by_device` wrote the on-root list NUL-separated (it is
# fed to `xargs -0`) but the FOREIGN list one-per-line, read back with
# `while IFS= read -r`. A directory named with an embedded newline therefore
# split into two rows in the report — and the second row is a fragment that
# reads as a real path. /home is user-writable and this script runs under sudo,
# so which lines the operator reads is not a thing to leave to chance.
NLFIX="$TMP/nlfix"
NLNAME="$(printf 'two\nlines')"
mkdir -p "$NLFIX/u/$NLNAME"
_dev_of() { case "$1" in "$NLFIX"/u/*) printf '%s\n' "$FOREIGN_DEV" ;; *) printf '%s\n' "$ROOTISH_DEV" ;; esac; }
split_by_device "$NLFIX" "$TMP/nl-on.lst" "$TMP/nl-fg.lst" "$ROOTISH_DEV"
nl_records="$(tr -dc '\0' < "$TMP/nl-fg.lst" | wc -c)"
[ "$nl_records" -eq 1 ] && pass "one directory with a newline in its name is ONE record" \
  || fail "the foreign list holds $nl_records records for a single directory — the name was split"
nl_out="$(report_foreign_mounts "$TMP/nl-fg.lst" 2>/dev/null)"
has "the foreign report prints the whole name, not a fragment" "$nl_out" "$NLFIX/u/$NLNAME"

# --------------------------------------------------------------------------- #
echo "== 5c. section 6d must DROP a foreign mount listed at depth 1 =="
# 🔴 DEFECT 7, THE HALF THAT WAS LEFT STANDING. `-xdev` only stops find
# DESCENDING past a mount; it still LISTS the mountpoint itself at depth 1. So a
# mount under /tmp arrived as a STARTING POINT for `du -sh -x` — precisely the
# case `du -x` cannot handle (see section 5(a)) — and its entire size landed in
# a figure the report labels as root-fs /tmp usage. Section 6c fixed this for
# /home; 6d, the section an operator actually acts on, did not.
#
# 🔴 A SECOND FILESYSTEM NEEDS ROOT, so what is stubbed is the device READING,
# not the filesystem: `_dev_of` is the same seam section 5 uses, while
# `find -printf '%D'` inside the helper still reports the fixture's REAL device.
# Pointing `_dev_of` at a value no entry can have is therefore behaviourally
# identical to every entry being a foreign mount — and pointing it at the real
# `stat` reading is the on-root case. Both directions are asserted, so a mutant
# that drops the filter AND a mutant that drops everything are both visible.
DEVFIX="$TMP/devfix"
mkdir -p "$DEVFIX/one" "$DEVFIX/two" "$DEVFIX/three"
touch "$DEVFIX/one/f" "$DEVFIX/two/f" "$DEVFIX/three/f"
UNREACHABLE_DEV=999000003

# The redefinitions live inside the command substitutions, which are subshells,
# so neither leaks into the rest of this file.
kept_sz="$( _dev_of() { stat -c '%d' "$1" 2>/dev/null; }; size_breakdown "$DEVFIX" 2>/dev/null )"
drop_sz="$( _dev_of() { printf '%s\n' "$UNREACHABLE_DEV"; }; size_breakdown "$DEVFIX" 2>/dev/null )"
has "size_breakdown KEEPS an entry on the base's own device" "$kept_sz" "$DEVFIX/one"
eq  "size_breakdown DROPS every entry on a foreign device" "$drop_sz" ""

kept_ino="$( _dev_of() { stat -c '%d' "$1" 2>/dev/null; }; inode_breakdown "$DEVFIX" 2>/dev/null )"
drop_ino="$( _dev_of() { printf '%s\n' "$UNREACHABLE_DEV"; }; inode_breakdown "$DEVFIX" 2>/dev/null )"
has "inode_breakdown KEEPS a directory on the base's own device" "$kept_ino" "$DEVFIX/one"
eq  "inode_breakdown DROPS every directory on a foreign device" "$drop_ino" ""

# 🔴 AND A MISSING DEVICE READING MUST REFUSE, not print an empty section. With
# the filter in place, "no rows" is the same output for "everything is foreign"
# and "I could not read the base at all" — and the second is a non-measurement.
nodev_sz="$( _dev_of() { stat -c '%d' "$1" 2>/dev/null; }; size_breakdown "$TMP/absent-$$" 2>/dev/null )"
has "an unreadable base REFUSES rather than printing an empty size section" \
    "$nodev_sz" "COULD NOT MEASURE: no device id for"
nodev_ino="$( _dev_of() { stat -c '%d' "$1" 2>/dev/null; }; inode_breakdown "$TMP/absent-$$" 2>/dev/null )"
has "an unreadable base REFUSES rather than printing an empty inode section" \
    "$nodev_ino" "COULD NOT MEASURE: no device id for"

# 🔴 EXCLUDING SILENTLY IS ITSELF A DEFECT. The device filter is right to drop a
# foreign mount — its size is not root-fs usage — but a section that drops what
# it could not count without saying so is the floor-presented-as-a-total failure
# this whole file catalogues. 6c prints the same listing for /home, with a
# deliberately loud "none" branch; 6d now does too.
fe_foreign="$( _dev_of() { printf '%s\n' "$UNREACHABLE_DEV"; }; foreign_entries "$DEVFIX" 2>/dev/null )"
has "6d NAMES the entries it excluded (first)" "$fe_foreign" "$DEVFIX/one"
has "6d NAMES the entries it excluded (second)" "$fe_foreign" "$DEVFIX/two"
lacks "6d does not claim 'none' while it is excluding things" "$fe_foreign" "none — every depth-1 entry"
# POSITIVE CONTROL for the branch above: the "none" line CAN fire, so the
# `lacks` is not an assertion about dead code.
fe_none="$( _dev_of() { stat -c '%d' "$1" 2>/dev/null; }; foreign_entries "$DEVFIX" 2>/dev/null )"
has "POSITIVE CONTROL: an all-on-device base prints the explicit 'none' line" \
    "$fe_none" "none — every depth-1 entry is on the same filesystem as $DEVFIX"
lacks "the 'none' case names no entry" "$fe_none" "$DEVFIX/one"
fe_nodev="$( _dev_of() { stat -c '%d' "$1" 2>/dev/null; }; foreign_entries "$TMP/absent-$$" 2>/dev/null )"
has "an unreadable base REFUSES rather than claiming no foreign mounts" \
    "$fe_nodev" "NOT an absence of foreign mounts"

# 🔴 THE DEVICE ID IS INTERPOLATED INTO A sed SCRIPT, so it must be digits, not
# merely non-empty. `stat -c '%d'` cannot produce a `/` today; the predicate is
# in one place so that stays true for all three callers rather than at two of
# them.
_dev_is_valid 12345 && pass "_dev_is_valid accepts a decimal device id" \
  || fail "_dev_is_valid rejected a plain decimal device id"
dev_bad_ok=1
for bad in '' '/etc/passwd' '12*3' '12 3' 'abc' '0x8040'; do
  if _dev_is_valid "$bad"; then fail "_dev_is_valid accepted [$bad]"; dev_bad_ok=0; fi
done
# 🔴 Conditional, not an unconditional `pass` after a loop that can fail — an
# `ok:` line printed next to its own `FAIL:` reads as coverage while providing
# none, which is the failure mode this whole suite is written against.
[ "$dev_bad_ok" -eq 1 ] && pass "_dev_is_valid rejects empty, hex, and every sed-metacharacter shape tried"

# The filter itself, driven directly: a NUL-separated `<device>\t<path>` stream.
# Hand-built, so the assertion does not depend on find's behaviour at all.
# 🔴 `printf '%s\t%s\0' a b …`, NEVER a single format string with `\0` in it.
# `printf '111\t/a/keep\0222\t…'` reads `\0222` as the OCTAL escape 0o222, so
# the record separator silently becomes byte 0x92 and the fixture stops being a
# NUL stream at all — which is how this assertion was red on its first run.
printf '%s\t%s\0' 111 /a/keep 222 /a/drop 111 '/a/keep two' > "$TMP/ondev.in"
on_dev_out="$(_on_device 111 < "$TMP/ondev.in" | tr '\0' '\n')"
eq "_on_device keeps exactly the wanted device, stripping the prefix" "$on_dev_out" \
   "$(printf '/a/keep\n/a/keep two\n')"
printf '%s\t%s\0' 111 "$NLNAME" 222 /b/x > "$TMP/ondev2.in"
on_dev_nul="$(_on_device 111 < "$TMP/ondev2.in" | tr -dc '\0' | wc -c)"
[ "$on_dev_nul" -eq 1 ] && pass "_on_device emits one NUL record for a path containing a newline" \
  || fail "_on_device emitted $on_dev_nul records for one path with a newline"

# The inverse, over the SAME stream: the two must partition it, so a mutant that
# made one of them match everything is visible from either side.
not_dev_out="$(_not_on_device 111 < "$TMP/ondev.in" | tr '\0' '\n')"
eq "_not_on_device is the exact complement of _on_device" "$not_dev_out" "/a/drop"

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
printf '%s\n' 'du -sh -x /tmp/* | xargs -I{} awk "{ s += $8 } END { print NR - 1 }" | head -n 20; X=$(lsof +L1); Y=$(grep -c foo bar || echo 0); if [ "${BASH_SOURCE[0]}" != "$0" ]; then :; fi' > "$CANARY_FILE"

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
#
# 🔴 THE `head` PATTERN IS SPELLING-INSENSITIVE ON PURPOSE, and it used not to
# be. It read `| *head -[0-9]`, which demands a DIGIT immediately after `head -`
# — so `head -n 20`, the POSIX-preferred spelling, walked straight through.
# MEASURED: `head -n 20`, `head -n 30` and `head -n 15` all SURVIVED the
# mutation battery while `head -15` was caught. `| *head  *-` (BRE: `|`,
# optional spaces, `head`, one-or-more spaces, `-`) matches every spelling and
# still does not match `head_n`, which is the sanctioned helper. The canary line
# above deliberately spells it `head -n 20`, so narrowing this pattern back
# trips the SCANNER-BROKEN branch instead of silently reporting ok.
#
# 🔴 `BASH_SOURCE` is banned outright rather than pinned to a shape. A guard on
# a specific expression is walkable by rewriting the expression; the hazard is
# that the seam consults an environment-importable VARIABLE at all.
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
no bare '| head -N' truncating a pipeline (SIGPIPE 141)@| *head  *-@head exits after N lines and the producer takes SIGPIPE; pipefail promotes 141 and set -e kills the run silently. Use head_n.
no BASH_SOURCE seam test (reachable from the environment)@BASH_SOURCE@bash imports BASH_SOURCE from the env as a scalar, so an EXECUTED run took the sourced branch and returned at top level: rc 2, no report, indistinguishable from the not-root refusal
BANNED

while IFS='@' read -r name lit why; do
  [ -n "${name:-}" ] || continue
  required "$name" "$lit" "$why"
done <<'REQUIRED'
section 2 keeps find's stderr instead of discarding it@2>>"$DENIED_LOG"@a scan reporting a number with no denial count is a floor presented as a total
lsof column is resolved by NAME from the header@if ($i == "SIZE/OFF") col=i@the index is not stable: +L1 puts it at 7, plain -n -P at 9
an absent SIZE/OFF column REFUSES rather than reporting a zero@COULD NOT MEASURE: no SIZE/OFF column in lsof header@a zero here is the reassuring reading of a broken instrument
the /tmp inode walk passes the name as an ARGUMENT@xargs -0 -r -n1 sh -c 'printf "%12d  %s\n" "$(find "$1" -xdev -printf . 2>/dev/null | wc -c)" "$1"' _@"$1" is an argv slot; "{}" under xargs -I is text the shell parses
top-level enumeration is NUL-safe find|xargs, not a glob@find "$base" -xdev -mindepth 1 -maxdepth 1 -printf '%D\t%p\0'@the glob form dies E2BIG and set -euo pipefail takes the whole run with it
/home candidates are compared by DEVICE, because du -x cannot do it@d=$(_dev_of "$p") || continue@du -x only stops du crossing AWAY from its start; started ON a foreign mount it walks all of it
/tmp candidates are compared by DEVICE too (defect 7, section 6d)@| _on_device "$dev"@-xdev still LISTS a mountpoint at depth 1, so it reaches du as a starting point — the one case du -x cannot handle
du keeps -x in the /tmp size breakdown@xargs -0 -r du -sh -x 2>/dev/null || true@INVARIANT PIN, not a regression guard: a second filesystem needs root, so -x cannot be checked behaviourally here. A mutant dropping it SURVIVED.
du keeps -x in the /home size breakdown@xargs -0 -r du -sh -x < "$ONROOT_LIST" 2>/dev/null || true@same invariant pin for section 6c's call site
truncation reads to EOF instead of closing the pipe@head_n() { awk -v n="$1" 'NR<=n'; }@awk consumes all input, so the upstream sort never takes SIGPIPE
the seam reads no variable at all@if (return 0 2>/dev/null); then@`return` cannot be poisoned from the environment; ${BASH_SOURCE[0]} could
section 6d reports what its device filter EXCLUDED@foreign_entries /tmp@dropping a foreign mount silently is the floor-presented-as-a-total failure this file exists to prevent
the device id is validated as digits before it reaches sed@_dev_is_valid "$dev"@the value is interpolated into a sed script; a / or a * would change the expression rather than fail
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
