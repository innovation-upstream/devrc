#!/usr/bin/env bash
# Regression guards for scripts/diagnose-disk-accounting.sh.
#
# WHY THIS EXISTS. That script is ROOT-PRIVILEGED bash that had no test file, in
# a repo with no shellcheck gate. (It was 282 lines at the merge base
# `c1169e3b`, BEFORE this suite's own commit — verifiable with
# `git show c1169e3b:scripts/diagnose-disk-accounting.sh | wc -l`. That is the
# ONLY line count stated here, and it is stated because it is sha-anchored.
# 🔴 A SECOND FIGURE — "by the end of that commit the file was 414 lines" —
# stood here and is DELETED: 414 was the count at a PR-branch commit that is NOT
# an ancestor of main, so nobody on main could ever check it. (The sha is not
# reproduced here — by construction a reader cannot resolve it, and section 14
# now fails on any cited sha that is unreachable, this one included.) A positional or historical
# number is legal in this file only with a sha a reader can resolve; that rule is
# what the last four audit rounds cost. For the current size, run
# `wc -l scripts/diagnose-disk-accounting.sh` — no count is written down.)
# Every defect pinned below was real,
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
# nothing. The header of that file marks the seam. (This sentence used to quote
# the seam as `BASH_SOURCE[0] != $0`. That expression was MEASURED reachable
# from the environment and removed a round ago; the scanner in section 7 bans it
# — but the scanner only reads the SCRIPT, so this copy of the stale claim, and
# an identical one in run-tests.sh, both walked straight past it.)
#
# 🔴 WHAT THIS SUITE CANNOT COVER, stated once so nobody reads it as more than
# it is: sections 1-3, 5, 6, 6b and 8 do privileged whole-filesystem
# measurement (`dumpe2fs -h /dev/nvme0n1p2`, `find / -xdev` over root-only
# trees, `findmnt /mnt/rootcheck`). Their numbers cannot be produced without
# root on a real host, so their arithmetic is UNGUARDED here. Their `set -e`
# EXPOSURE is a different question and is covered structurally by section 7b —
# but read that section's own header for what its scan can and cannot see. It
# does NOT "sweep the whole file", which is what this sentence used to claim: it
# keeps lines by their FIRST WORD and drops any line containing `||`, so a
# pipeline headed by `for`/`done`/`printf` or guarded only in an early stage is
# invisible to it. That is why 7b also carries a second, separate ledger over
# the trailing `sort` stages, and why the script's own sweep comment lists five
# deliberate exceptions rather than two. What is guarded behaviourally is every
# defect the file's own comments record, all of which live in the transforms
# below.
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

# 🔴 ABORT AS ROOT — do not `fail` and carry on. This check used to live inside
# section 1 and only guarded the sections nested under it; §2c, which EXECUTES
# the real script under `bash -x`, sat at top level and ran regardless. Under
# root that is not a failed assertion, it is the whole privileged report:
# `dumpe2fs`, `find / -xdev` over every top-level directory, `du -sh -x
# /var/lib/docker`, and `size_breakdown`/`inode_breakdown` over /tmp — with the
# xtrace accumulating in a shell variable.
#
# MEASURED 2026-09-07 under `unshare -r` (uid 0 in a user namespace): the base
# suite printed its `FAIL: … AS ROOT` line and RAN ON, through §2c and all the
# way to section 7, rc 1 — 24.95 s here versus 22 s unprivileged, because this
# host's / is still EACCES to a userns root and its /tmp is small. What is
# therefore MEASURED is the mechanism, not a duration: the guard did not stop
# the run and the report did execute. The cost is whatever the host's /tmp
# costs, and on the host this script targets that was 78 million entries. At
# HEAD the same command takes 0.01 s.
#
# Every fixture below also DEPENDS on not being root (the 0400 and mode-000
# directories are readable by uid 0), so a root run makes the guards vacuous
# even where it does terminate.
if [ "$(id -u)" -eq 0 ]; then
  echo "  FAIL: this suite is running AS ROOT. Nothing here needs root and several"
  echo "        fixtures are inert under it. Re-run unprivileged."
  exit 1
fi

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
          report_denials _errline_count _report_unstattable _report_unreadable \
          _scan_mktemp _cleanup_temps toplevel_accountable_entries; do
  if declare -F "$fn" >/dev/null; then pass "helper $fn is sourceable"
  else fail "helper $fn is NOT defined after sourcing"; fi
done

# --------------------------------------------------------------------------- #
echo "== 1. ROOT REFUSAL: an unprivileged EXECUTION must refuse, rc 2 =="
# The whole premise of the script — an unprivileged run silently skips the trees
# it exists to measure — so a soft-fail here is worse than no script.
#
# 🔴 NO `if [ "$(id -u)" -eq 0 ]` WRAPPER HERE ANY MORE. It used to open one
# that closed at the end of §1b, which read as "the suite is root-guarded" while
# guarding only two sections; the root abort now sits at the top of the file, so
# nothing below it can run privileged. See the comment there.
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

# --------------------------------------------------------------------------- #
echo "== 4b-ii. ROUTE (a): an UNSTATTABLE entry must be COUNTED, not erased =="
# 🔴 THE DEFECT, and it was introduced by the round-1 fix for route (a). `-print0`
# still printed the NAME of an entry whose stat failed; `-printf '%D\t%p\0'`
# forces a stat to format the record and emits NOTHING when it fails.
#
# 🔴 A PER-IMPLEMENTATION TABLE OF BYTES AND EXIT CODES STOOD HERE AND IS
# DELETED — it was wrong on every column, and it is the TWIN of one deleted from
# scripts/diagnose-disk-accounting.sh in an earlier round. That round fixed the
# copy it was looking at and never grepped for the other, so the two files
# contradicted each other at one sha for a round. Sweep every site, not the site
# you are in.
# What it claimed, and what is actually true (measured against the real
# invocation, which passes `-xdev`):
#   - "`-print0` … rc 0 … needs no stat": FALSE. `-xdev` needs each entry's
#     st_dev, so it stats and exits 1 on an unreadable directory.
#   - "all three gave 114 B": FALSE. Byte counts are dominated by the fixture's
#     PATH LENGTH, so they are a figure about one fixture on one machine, not
#     about any implementation. Two builds measured 122 B and 38 B on one tree.
#   - the version labels: wrong. See the note in the script's `_depth1_nul`.
# The load-bearing half is the only thing worth stating, and it reproduced
# everywhere: `%D` emits NOTHING for an entry whose stat fails, while `-print0`
# still emits its NAME. So the entry left the size
# breakdown, the inode breakdown AND the foreign-entry listing at once — and
# `foreign_entries` then printed "none — every depth-1 entry is on the same
# filesystem as …", an affirmative claim of absence produced by a blind scan.
#
# 🔴 WHAT THIS CANNOT PIN, stated so it is not read as more than it is: route
# (b)'s "the readable sibling still produces a row" has no analogue here,
# because a fixture with SOME depth-1 entries stattable and some not is not
# constructible without root — `find` needs `x` on the parent to stat any entry,
# so mode 0400 makes ALL of them unstattable at once. What is pinned instead is
# the blind-spot report itself: the count, and the refusal to say "none".
ERR_SZ="$(size_breakdown "$ERRDIR" 2>/dev/null)"
ERR_INO="$(inode_breakdown "$ERRDIR" 2>/dev/null)"
ERR_FGN="$(foreign_entries "$ERRDIR" 2>/dev/null)"
# The fixture holds three entries (sub-a, sub-b, f) and find can stat none.
has "route (a): size_breakdown COUNTS the entries it could not stat" \
    "$ERR_SZ" "!! UNSTATTABLE: 3 depth-1 entries of $ERRDIR reached NO list above."
has "route (a): size_breakdown calls its own figures a FLOOR" "$ERR_SZ" "is a FLOOR, not a total"
# 🔴 NO EXACT COUNT FOR THE INODE HALF, on purpose. Its enumeration adds
# `-type d`, and MEASURED here GNU find answers that test from readdir's
# `d_type` where the filesystem supplies one — so the plain file `f` is filtered
# out BEFORE a stat is attempted and only two errors appear, while on a
# filesystem without `d_type` all three would. Pinning 3 (or 2) would make this
# guard a claim about $TMPDIR's filesystem. The count is pinned exactly on the
# two enumerations that have no type filter, above and below.
has "route (a): inode_breakdown COUNTS the entries it could not stat" \
    "$ERR_INO" "depth-1 entries of $ERRDIR reached NO list above."
ino_blind="$(printf '%s\n' "$ERR_INO" | sed -n 's/^  !! UNSTATTABLE: \([0-9][0-9]*\) .*/\1/p')"
case "${ino_blind:-x}" in
  ''|*[!0-9]*) fail "route (a): inode_breakdown printed no parsable UNSTATTABLE count" ;;
  *) [ "$ino_blind" -gt 0 ] && pass "route (a): inode_breakdown's unstattable count is a positive integer ($ino_blind)" \
       || fail "route (a): inode_breakdown reported an unstattable count of $ino_blind over a fixture find cannot stat" ;;
esac
has "route (a): foreign_entries COUNTS the entries it could not stat" \
    "$ERR_FGN" "!! UNSTATTABLE: 3 depth-1 entries of $ERRDIR reached NO list above."
# 🔴 THE HEADLINE: the affirmative "none" must NOT be printed over a blind scan.
lacks "route (a): foreign_entries does NOT claim every entry is on the same filesystem" \
      "$ERR_FGN" "none — every depth-1 entry is on the same filesystem"
has "route (a): foreign_entries says only that none is VISIBLE" \
    "$ERR_FGN" "none VISIBLE"
# The denial lines themselves are surfaced, not just tallied.
has "route (a): the blind-spot report quotes find's own error" "$ERR_SZ" "Permission denied"

# 🔴 POSITIVE CONTROL for the whole block: on a fixture find CAN stat, none of
# the above fires — otherwise "UNSTATTABLE is reported" would also be satisfied
# by a function that printed it unconditionally.
OK_DIR="$TMP/stattable"
mkdir -p "$OK_DIR/a" "$OK_DIR/b"; touch "$OK_DIR/a/f"
ok_sz="$(size_breakdown "$OK_DIR" 2>/dev/null)"
ok_fgn="$(foreign_entries "$OK_DIR" 2>/dev/null)"
lacks "POSITIVE CONTROL: a stattable fixture reports NO blind spot" "$ok_sz" "UNSTATTABLE"
has "POSITIVE CONTROL: a stattable fixture still lists its entries" "$ok_sz" "$OK_DIR/a"
has "POSITIVE CONTROL: a stattable fixture DOES print the affirmative 'none'" \
    "$ok_fgn" "none — every depth-1 entry is on the same filesystem as $OK_DIR"
chmod 700 "$ERRDIR"; chmod 700 "$LOCKDIR/locked"

# --------------------------------------------------------------------------- #
echo "== 4b-iii. ROUTE (b): a path du cannot READ is an UNDER-COUNT, not a total =="
# 🔴 THE ASYMMETRY THAT MADE A PRE-EXISTING `2>/dev/null` WORTH FIXING NOW.
# Route (a) ERASES an entry; route (b) keeps it and SHORTENS its number. §4b
# above pins that route (b) does not kill the run and that the readable sibling
# still produces a row — neither of which says anything about the figure printed
# for the unreadable one. With du's stderr at /dev/null nothing did. And once
# §4b-ii's `!! UNSTATTABLE` line existed, the ABSENCE of a blind-spot line
# started reading as an affirmative "nothing was missed" — a floor presented as
# a total, reached by trusting a report that never had the evidence.
#
# 🔴 THE POSITIVE CONTROL ASSERTS THE UNDER-COUNT ITSELF, not merely that du
# exits non-zero. "The blind spot is reported" would otherwise be satisfiable on
# a fixture where du in fact read everything. MEASURED over this fixture:
# 8K reported for `locked` against a true 12K, a 33% shortfall.
DUFIX="$TMP/du-partial"
mkdir -p "$DUFIX/open" "$DUFIX/locked/inner"
head -c 4096 /dev/zero > "$DUFIX/open/f"
head -c 4096 /dev/zero > "$DUFIX/locked/inner/f"
du_true="$(du -s -x --block-size=1K "$DUFIX/locked" 2>/dev/null | awk '{print $1}')"
chmod 000 "$DUFIX/locked/inner"
du_short="$(du -s -x --block-size=1K "$DUFIX/locked" 2>/dev/null | awk '{print $1}')"
case "${du_true:-x}${du_short:-x}" in
  *[!0-9]*) fail "POSITIVE CONTROL (du under-count): unparsable du output (true=[${du_true:-}] short=[${du_short:-}])" ;;
  *) [ "$du_short" -lt "$du_true" ] \
       && pass "POSITIVE CONTROL: du really under-counts the locked fixture (${du_short}K reported vs ${du_true}K true)" \
       || fail "POSITIVE CONTROL FAILED: du reported ${du_short}K against a true ${du_true}K — no under-count, so every assertion below is vacuous" ;;
esac

du_out="$(size_breakdown "$DUFIX" 2>/dev/null)"
has "route (b): size_breakdown REPORTS that du could not read a path" \
    "$du_out" "!! PARTIALLY READ: du could not read 1 path(s) under $DUFIX."
has "route (b): the under-counted figures are called FLOORS" \
    "$du_out" "The sizes above are FLOORS for those paths, not totals."
has "route (b): du's own error line is quoted, not just tallied" "$du_out" "cannot read directory"
has "route (b): the readable sibling is still listed" "$du_out" "$DUFIX/open"
# 🔴 THE TWO BLIND SPOTS MUST STAY TELLABLE APART. `find` can stat everything in
# this fixture, so the route-(a) line must NOT appear: one message doing both
# jobs would make both counts meaningless, and merging the two stderr sinks is
# exactly how that would happen.
lacks "route (b): a du failure is NOT reported as an unstattable entry" "$du_out" "UNSTATTABLE"

# 🔴 POSITIVE CONTROL for the branch: on a fixture du can read completely, the
# line is ABSENT — otherwise a function that printed it unconditionally would
# satisfy every assertion above.
ok_du="$(size_breakdown "$OK_DIR" 2>/dev/null)"
lacks "POSITIVE CONTROL: a fully readable fixture reports NO du blind spot" "$ok_du" "PARTIALLY READ"
has "POSITIVE CONTROL: that fixture still produced a size row" "$ok_du" "$OK_DIR/a"
chmod 700 "$DUFIX/locked/inner"

# --------------------------------------------------------------------------- #
echo "== 4b-iv. TEMP FILES: identifiable names, and ONE trap that covers them all =="
# 🔴 THE DEFECT. The three breakdown helpers opened `errf=$(mktemp)` — an
# anonymous `/tmp/tmp.XXXXXXXXXX` — and NONE of the three was named in the
# script's EXIT trap; only its own function's last statement removed it. The
# realistic way this run ends is not an abort but SIGINT: the run it was written
# for took ~3 h over 78 million entries. MEASURED 2026-09-07 on this host's
# bash: an EXIT trap DOES run when the shell is killed by an untrapped SIGINT —
# so the trap removed the NAMED $DENIED_LOG and left up to three unattributable
# files in /tmp, the directory under diagnosis.
# 🔴 `${REPLY:-}`, and a `declare -F` gate, so this section stays REPORTABLE
# against a tree that has no `_scan_mktemp` at all. Without them the suite dies
# on `set -u` at the first unset REPLY and every guard below it goes unmeasured
# — which is exactly the truncated-run failure this file is about, in the file
# that is about it. MEASURED on the red baseline: without this gate the run
# stopped partway through and reported only the guards it had reached; with it,
# the full set is reported. (No sha and no count: the commit was a PR-branch one
# that never became an ancestor of main, and the count is a running total that
# every added assertion invalidates.)
REPLY=
if declare -F _scan_mktemp >/dev/null; then
  _scan_mktemp probe-4biv || fail "_scan_mktemp could not create a temp file"
fi
scan_path="${REPLY:-}"
case "${scan_path##*/}" in
  disk-accounting-probe-4biv.*) pass "a scan temp file is NAMED for this script and its purpose (${scan_path##*/})" ;;
  *) fail "a scan temp file is named [${scan_path##*/}] — an operator cannot attribute it to this script" ;;
esac
[ -n "$scan_path" ] && [ -f "$scan_path" ] && pass "the named temp file really exists before cleanup" \
  || fail "_scan_mktemp reported success without creating a file"
[ -n "$scan_path" ] && rm -f "$scan_path"

# 🔴 THE END-TO-END CASE, AND IT INTERRUPTS FOR REAL. The property is "a signal
# arriving between the mktemp and the rm does not leak", so the probe stubs
# `_report_unstattable` — which `size_breakdown` calls AFTER opening both temp
# files and BEFORE removing them — to record the two paths and kill itself.
# The trap is installed by the probe because it only exists on the script's ROOT
# execute path, which this suite cannot reach; that the script really installs
# THIS function is pinned separately in section 7 (`trap _cleanup_temps EXIT`).
# What is measured here is the half a structural pin cannot reach: that the
# function removes a file opened long after the trap went up.
#
# 🔴 THE PROBE FALLS BACK FROM SIGINT TO SIGTERM, AND THE FALLBACK IS THE POINT.
# A first draft used `kill -INT` alone. It passed when this suite was run in the
# foreground and produced THREE FAILs the moment the mutation battery ran it
# from a `nohup … &` — bash sets SIGINT and SIGQUIT to SIG_IGN in a command
# started asynchronously without job control, and a non-interactive shell
# CANNOT reset a signal that was ignored on entry. MEASURED with a two-line
# stand-in (`kill -INT $$; echo SURVIVED`): it dies silently when run in the
# foreground and prints SURVIVED when run from `( … & wait )`. Left as-is, the
# leak assertion would have gone VACUOUSLY GREEN in
# exactly the runner that matters (0 files recorded as surviving because none
# was ever opened for a signal that never arrived) — which is why the "was it
# really interrupted" rows below are not decoration. SIGINT is still attempted
# first, because it is the signal the defect is about; SIGTERM is delivered the
# same way and reaches the same EXIT trap.
SIGFIX="$TMP/sigint-fixture"
mkdir -p "$SIGFIX/a"; touch "$SIGFIX/a/f"
cat > "$TMP/sigint-probe.sh" <<PROBE
set -euo pipefail
source "$SCRIPT"
if [ "\${1:-}" = "trapped" ]; then trap _cleanup_temps EXIT; fi
_report_unstattable() {
  printf '%s\n%s\n' "\$SCAN_ERRF" "\$SCAN_DUERR" > "$TMP/sigint-paths"
  kill -INT \$\$
  # Reached only when SIGINT was ignored on entry to this shell.
  echo "SIGINT-IGNORED-USING-SIGTERM"
  kill -TERM \$\$
}
size_breakdown "$SIGFIX" >/dev/null
echo "NOT-INTERRUPTED"
PROBE

# POSITIVE CONTROL FIRST — the UNTRAPPED run must LEAK, or "the trapped run left
# nothing behind" is indistinguishable from a probe that never opened a file.
# `: >` rather than `rm -f`: a probe that failed to run at all must leave an
# EMPTY ledger for the loops below to read, not a missing file — otherwise the
# redirect fails and the count guards report on nothing.
: > "$TMP/sigint-paths"
si_untrapped="$("$BASH_BIN" "$TMP/sigint-probe.sh" untrapped 2>&1)"
lacks "the probe really was interrupted (untrapped run)" "$si_untrapped" "NOT-INTERRUPTED"
leaked=0; leak_names=""
while IFS= read -r si_p; do
  [ -n "$si_p" ] || continue
  if [ -e "$si_p" ]; then leaked=$((leaked + 1)); leak_names="$leak_names ${si_p##*/}"; rm -f "$si_p"; fi
done < "$TMP/sigint-paths"
[ "$leaked" -eq 2 ] && pass "POSITIVE CONTROL: with no trap the signal leaks both temp files ($leak_names)" \
  || fail "POSITIVE CONTROL FAILED: an untrapped signal leaked $leaked of 2 temp files — the trapped run below would prove nothing"
# Informational, and it names which signal the harness could actually deliver —
# a reader of a green run should not have to guess which half was exercised.
case "$si_untrapped" in
  *SIGINT-IGNORED-USING-SIGTERM*) pass "the interrupt was delivered as SIGTERM (SIGINT is SIG_IGN in this runner)" ;;
  *) pass "the interrupt was delivered as SIGINT" ;;
esac

: > "$TMP/sigint-paths"
si_trapped="$("$BASH_BIN" "$TMP/sigint-probe.sh" trapped 2>&1)"
lacks "the probe really was interrupted (trapped run)" "$si_trapped" "NOT-INTERRUPTED"
survivors=0; survivor_names=""
while IFS= read -r si_p; do
  [ -n "$si_p" ] || continue
  if [ -e "$si_p" ]; then survivors=$((survivors + 1)); survivor_names="$survivor_names ${si_p##*/}"; rm -f "$si_p"; fi
done < "$TMP/sigint-paths"
[ "$survivors" -eq 0 ] && pass "an interrupt mid-breakdown leaves NO temp file behind in /tmp" \
  || fail "an interrupt mid-breakdown leaked $survivors temp file(s) into /tmp:$survivor_names"

# 🔴 AND EMPTY SLOTS MUST BE HARMLESS: `_cleanup_temps` runs on EVERY exit path,
# including one taken before a single mktemp. MEASURED: `rm -f ""` is rc 0 and
# silent, which is why the function needs no per-slot test — but a future
# rewrite that added one and got it wrong would end the run on the trap.
keepf="$TMP/not-a-scan-temp"; : > "$keepf"
DENIED_LOG= ; DU_ERR= ; ONROOT_LIST= ; FOREIGN_LIST= ; SCAN_ERRF= ; SCAN_DUERR=
cleanup_empty_out="$(_cleanup_temps 2>&1)"; cleanup_empty_rc=$?
eq "_cleanup_temps with every slot empty is silent" "$cleanup_empty_out" ""
[ "$cleanup_empty_rc" -eq 0 ] && pass "_cleanup_temps with every slot empty is rc 0" \
  || fail "_cleanup_temps exited $cleanup_empty_rc with nothing to remove — the EXIT trap would rewrite the run's status"
[ -f "$keepf" ] && pass "POSITIVE CONTROL: _cleanup_temps removes only what the run RECORDED" \
  || fail "_cleanup_temps removed a file it was never given — it is an rm, not a cleanup"

# --------------------------------------------------------------------------- #
echo "== 4c. the refusal branches must be REACHABLE under the real set -e =="
# 🔴 THE DEFECT: `dev=$(_dev_of "$base")` is a command substitution in an
# assignment, i.e. a CHECKED command under `set -e`. When `stat` fails the run
# died ON THAT LINE and the `COULD NOT MEASURE: no device id` branch below it —
# added in round 1 precisely so a non-measurement could not read as an empty
# directory — never printed. Exactly the `OUT=$(lsof …)` defect this file
# already records, one round later and three functions over.
#
# 🔴 AND THE SUITE COULD NOT SEE IT. Sourcing this script turns `set -e` on, so
# the suite turns it back OFF immediately after sourcing, to run at all; every §5c assertion
# about the refusal branch therefore ran WITHOUT the option that made the branch
# unreachable. Same shape as §2b: only a probe that re-enables `set -euo
# pipefail` for real can observe it.
cat > "$TMP/nodev-probe.sh" <<PROBE
set -euo pipefail
source "$SCRIPT"
size_breakdown "$TMP/no-such-base"
echo "PAST-SIZE-REFUSAL"
inode_breakdown "$TMP/no-such-base"
echo "PAST-INODE-REFUSAL"
foreign_entries "$TMP/no-such-base"
echo "PAST-FOREIGN-REFUSAL"
PROBE
nd_out="$("$BASH_BIN" "$TMP/nodev-probe.sh" 2>&1)"; nd_rc=$?
[ "$nd_rc" -eq 0 ] && pass "an unreadable base does not abort the run under set -e (rc 0)" \
  || fail "an unreadable base killed the run under set -e, rc $nd_rc — the refusal branch is unreachable"
has "the size refusal is REACHABLE under set -e" "$nd_out" "COULD NOT MEASURE: no device id for $TMP/no-such-base — NOT an empty directory (size breakdown)"
has "the inode refusal is REACHABLE under set -e" "$nd_out" "COULD NOT MEASURE: no device id for $TMP/no-such-base — NOT an empty directory (inode breakdown)"
has "the foreign refusal is REACHABLE under set -e" "$nd_out" "NOT an absence of foreign mounts"
has "the report continues past the size refusal" "$nd_out" "PAST-SIZE-REFUSAL"
has "the report continues past the inode refusal" "$nd_out" "PAST-INODE-REFUSAL"
has "the report continues past the foreign refusal" "$nd_out" "PAST-FOREIGN-REFUSAL"
# 🔴 THE TWO REFUSALS MUST BE TELLABLE APART, and the two `has` rows above are
# what pins it: before this round both printed the byte-identical string
# `COULD NOT MEASURE: no device id for <base> — NOT an empty directory`, so a
# reader of a real report could not tell which section had refused. Each row
# now names its own section, and each `has` fails if that suffix is missing.
# (An earlier draft added a third `lacks` here for a DUPLICATED size line. It
# was vacuous — the probe prints a marker between the two refusals, so the two
# lines are never adjacent and the needle could not match either way.)

# --------------------------------------------------------------------------- #
echo "== 4d. a foreign entry whose NAME contains a newline is ONE row =="
# 🔴 THE DEFECT: `foreign_entries` ended `| tr '\0' '\n' | head_n 15`, which is
# the F7 defect `split_by_device` was fixed for IN THE SAME COMMIT. /tmp is mode
# 1777, so any local process can plant a directory whose name contains a
# newline; `tr` turns it into two report rows — the second a fragment that reads
# as a real path — and `head_n` counts LINES, so one name eats two of fifteen
# slots. `report_foreign_mounts` reads its list with `read -r -d ''`; this
# function now emits one row per NUL record for the same reason.
#
# 🔴 THE DISCRIMINATOR IS THE INDENTATION, NOT THE LINE COUNT. Both forms print
# three lines for this fixture — the old one indented all three (`sed 's/^/  /'`
# over a `tr`-flattened stream), the fixed one indents one per RECORD, so the
# fragment `beta` arrives unindented as part of its own name. A line count
# cannot tell them apart; the count of INDENTED lines can.
NLDEV="$TMP/nl-foreign"
NLNAME_6D="$(printf 'alpha\nbeta')"
FGN_DEV_6D=999000004
mkdir -p "$NLDEV/$NLNAME_6D" "$NLDEV/plain"
nl_fgn="$( _dev_of() { printf '%s\n' "$FGN_DEV_6D"; }; foreign_entries "$NLDEV" 2>/dev/null )"
nl_records="$(printf '%s\n' "$nl_fgn" | grep -c '^  ')"
[ "$nl_records" -eq 2 ] && pass "two foreign directories are TWO indented records, newline name and all" \
  || fail "foreign_entries emitted $nl_records indented records for 2 entries — a name was split into an extra row"
has "the whole newline name is present, indented once" "$nl_fgn" "$(printf '  %s' "$NLDEV/$NLNAME_6D")"
has "the plain sibling is still listed" "$nl_fgn" "  $NLDEV/plain"

# 🔴 THE TRUNCATION IS NEW CODE, SO IT GETS ITS OWN GUARD. `head_n 15` counted
# LINES; the replacement counts RECORDS in the loop, which is a different
# expression that can be off by one, stop early, or forget to say it truncated.
# 20 entries: 15 printed, and the report must SAY there are 5 more rather than
# silently ending at 15 — that is the same floor-as-total failure one more time.
MANYDEV="$TMP/many-foreign"
mkdir -p "$MANYDEV"
for i in $(seq -w 1 20); do mkdir -p "$MANYDEV/entry-$i"; done
many_fgn="$( _dev_of() { printf '%s\n' "$FGN_DEV_6D"; }; foreign_entries "$MANYDEV" 2>/dev/null )"
many_rows="$(printf '%s\n' "$many_fgn" | grep -c '^  /')"
[ "$many_rows" -eq 15 ] && pass "20 foreign entries print exactly 15 rows" \
  || fail "20 foreign entries printed $many_rows rows, expected 15"
has "the truncation is STATED, not silent" "$many_fgn" "... and 5 more"
lacks "the truncated listing does not also claim 'none'" "$many_fgn" "none"

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
#
# 🔴 AN EMPTY LIST MUST SAY SO IN WORDS. These two used to assert the dropped
# case produced the EMPTY STRING — which is what a section that measured nothing
# also produces, and "an empty section is still visible" was the false claim the
# script's own comment made about it. So the assertion is now: the excluded
# entries are absent (the filter works) AND the section says it is empty (the
# floor is labelled). The guard NAMES are unchanged, because the mutation
# battery scores `tmp-size-device-filter-dropped` / `tmp-inode-…` on them.
kept_sz="$( _dev_of() { stat -c '%d' "$1" 2>/dev/null; }; size_breakdown "$DEVFIX" 2>/dev/null )"
drop_sz="$( _dev_of() { printf '%s\n' "$UNREACHABLE_DEV"; }; size_breakdown "$DEVFIX" 2>/dev/null )"
has "size_breakdown KEEPS an entry on the base's own device" "$kept_sz" "$DEVFIX/one"
lacks "size_breakdown DROPS every entry on a foreign device" "$drop_sz" "$DEVFIX/one"
has "size_breakdown SAYS its list is empty rather than printing nothing" \
    "$drop_sz" "none — no depth-1 entry of $DEVFIX is on $DEVFIX's own filesystem (NOT zero bytes)"

kept_ino="$( _dev_of() { stat -c '%d' "$1" 2>/dev/null; }; inode_breakdown "$DEVFIX" 2>/dev/null )"
drop_ino="$( _dev_of() { printf '%s\n' "$UNREACHABLE_DEV"; }; inode_breakdown "$DEVFIX" 2>/dev/null )"
has "inode_breakdown KEEPS a directory on the base's own device" "$kept_ino" "$DEVFIX/one"
lacks "inode_breakdown DROPS every directory on a foreign device" "$drop_ino" "$DEVFIX/one"
has "inode_breakdown SAYS its list is empty rather than printing nothing" \
    "$drop_ino" "none — no depth-1 DIRECTORY of $DEVFIX is on $DEVFIX's own filesystem (NOT zero inodes)"

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
top-level enumeration is NUL-safe find|xargs, and KEEPS find's stderr@find "$base" -xdev -mindepth 1 -maxdepth 1 "$@" -printf '%D\t%p\0' 2>"$errf"@the glob form dies E2BIG and set -euo pipefail takes the whole run with it; and %D forces a stat per entry, so 2>/dev/null here erases an unstattable entry from every list at once
/home candidates are compared by DEVICE, because du -x cannot do it@d=$(_dev_of "$p") || continue@du -x only stops du crossing AWAY from its start; started ON a foreign mount it walks all of it
/tmp candidates are compared by DEVICE too (defect 7, section 6d)@| { _on_device "$dev" || true; }@-xdev still LISTS a mountpoint at depth 1, so it reaches du as a starting point — the one case du -x cannot handle
du keeps -x AND its || true in the /tmp size breakdown@xargs -0 -r du -sh -x 2>>"$SCAN_DUERR" || true@INVARIANT PIN, not a regression guard, for the -x half: a second filesystem needs root, so -x cannot be checked behaviourally here and a mutant dropping it SURVIVED. The `|| true` half IS behaviourally covered, by route (b) in section 4b — this literal pins both, so read it as two claims. The redirection is part of the literal on purpose: it was `2>/dev/null` until round 3, and a du that cannot read a path is then an under-count with no marker (section 4b-iii).
du keeps -x AND its || true in the /home size breakdown@xargs -0 -r du -sh -x < "$ONROOT_LIST" 2>>"$DU_ERR" || true@same three claims for section 6c's call site; 6c is root-only, so its du-stderr capture is an INVARIANT PIN with no behavioural half
section 5's PVC listing keeps du's stderr too@xargs -0 -r du -sh --exclude=/mnt 2>>"$DU_ERR" || true@root-only INVARIANT PIN, about THIS pipeline and no other. A previous wording called it "the third du site", which asserted a COUNT of the file's du invocations; the count was wrong and the scope it implied was wider than any row here checks. `_report_unreadable`'s own comment declares which sites route du's stderr to a file and states that it says nothing about the rest.
the du blind spot reporter EXISTS@_report_unreadable@capturing a stream nothing reads is worse than discarding it — it looks like coverage. Behaviourally covered for size_breakdown in section 4b-iii. 🔴 THIS ROW PINS ONLY THAT THE NAME APPEARS, which the DEFINITION line satisfies on its own — an earlier wording claimed it pinned that the two root-only sites CALL it, and deleting BOTH call sites left the suite fully green. The call sites are counted below instead.
section 6 BRANCHES on du's status instead of discarding it@if du_out=$(du -sh -x "$d" 2>/dev/null); then du_mark=; else du_mark=@a du that cannot read a tree prints a PARTIAL total and exits 1; this row rendered that floor exactly like a complete figure, and since `_report_unreadable` landed the absence of a marker reads as "nothing was missed". Root-only, so this is an INVARIANT PIN.
section 6 PRINTS the marker it computed@"$(find "$d" -xdev -printf . 2>/dev/null | wc -c)" "$d" "$du_mark"@a variable that is set but never reaches the output is the field-that-is-not-a-guard shape: the branch above would look like coverage while every row still printed identically.
every temp file this script opens carries an identifying name@mktemp "/tmp/disk-accounting-$1.XXXXXX"@a bare `mktemp` writes /tmp/tmp.XXXXXXXXXX — an unattributable file, in the directory this script exists to diagnose
the EXIT trap names a FUNCTION, not a fixed list of files@trap _cleanup_temps EXIT@a trap naming a fixed LIST is an enumeration by eye: the three breakdown temp files were never in either version of it. This row pins the NAME only — the ORDER is a separate guard below, because a description wider than its check is how six guards in one session read as coverage while providing none.
section 5's du TOTAL is guarded, not bare@|| echo "COULD NOT MEASURE: du failed under /var/lib/rancher/k3s/storage@it printed an under-counted total, exited 1 with its message already at /dev/null, and set -e then killed sections 6..8. Root-only, so this is an INVARIANT PIN; the failure itself is measured in section 4b route (b).
section 1's dumpe2fs pipeline is guarded@|| echo "COULD NOT MEASURE: dumpe2fs read no ext4 superblock fields@$DEV is a GUESS and grep exits 1 on no match, so a wrong device killed the whole report at section 1. Root-only: INVARIANT PIN.
the root device reading cannot kill the run@ROOT_DEV=$(_dev_of /) || ROOT_DEV=@a bare VAR=$(...) is a CHECKED command under set -e; and an EMPTY root device makes split_by_device call every /home directory foreign
the three /tmp helpers survive an unreadable base@dev=$(_dev_of "$base") || dev=@same checked-assignment defect: without the `||` the run died before the COULD NOT MEASURE branch it was supposed to reach (behaviourally covered in section 4c)
truncation reads to EOF instead of closing the pipe@head_n() { awk -v n="$1" 'NR<=n'; }@awk consumes all input, so the upstream sort never takes SIGPIPE
the seam reads no variable at all@if (return 0 2>/dev/null); then@`return` cannot be poisoned from the environment; ${BASH_SOURCE[0]} could
section 6d reports what its device filter EXCLUDED@foreign_entries /tmp@dropping a foreign mount silently is the floor-presented-as-a-total failure this file exists to prevent
the device id is validated as digits before it reaches sed@_dev_is_valid "$dev"@the value is interpolated into a sed script; a / or a * would change the expression rather than fail
REQUIRED

# 🔴 ORDER, NOT JUST PRESENCE — the other half of the row above. A trap that
# names the right function but goes up AFTER the first `mktemp` does not cover
# that file on the exit path the `mktemp` line itself can take, and no
# fixed-string pin can see the difference. Line numbers are read from the
# comment-stripped copy, which is fine: only their ORDER is asserted.
trap_line="$(grep -n 'trap _cleanup_temps EXIT' "$CODE_FILE" | head_n 1 | cut -d: -f1)"
denied_line="$(grep -n 'DENIED_LOG=\$(mktemp' "$CODE_FILE" | head_n 1 | cut -d: -f1)"
case "${trap_line:-x}${denied_line:-x}" in
  *[!0-9]*) fail "the EXIT trap / first mktemp ordering cannot be read (trap=[${trap_line:-}] mktemp=[${denied_line:-}]) — one of them is gone" ;;
  *) [ "$trap_line" -lt "$denied_line" ] \
       && pass "the EXIT trap is installed BEFORE the run's first mktemp (line $trap_line < $denied_line)" \
       || fail "the EXIT trap is installed at line $trap_line, AFTER the first mktemp at $denied_line — that file is uncovered on the exit path its own creation can take" ;;
esac

# 🔴 EVERY `: > "$DU_ERR"` TRUNCATION, NOT "a truncation" — a RELATIONSHIP, so
# it fails when the set grows as well as when a guard is dropped. A redirection
# failure on a SPECIAL BUILTIN is fatal under `set -e` (MEASURED 2026-09-08,
# bash 5.3.15: `set -euo pipefail; : > /absent/x` ends the shell, rc 1), and
# NEITHER ledger in 7b below can see this shape — the sweep keys on a line's
# first word and this one's is `:`, and the line carries no `| sort`. A single
# `required` row would go green with one of the two sites unguarded, which is
# why this counts instead of pinning one literal. The numbers are DERIVED here,
# never written down: what is asserted is that they are equal and non-zero.
duerr_total="$(grep -cF ': > "$DU_ERR"' "$CODE_FILE" || true)"
duerr_guarded="$(grep -cF ': > "$DU_ERR" || echo "COULD NOT MEASURE' "$CODE_FILE" || true)"
case "${duerr_total:-x}${duerr_guarded:-x}" in
  *[!0-9]*) fail "the DU_ERR truncation ledger cannot be read (total=[${duerr_total:-}] guarded=[${duerr_guarded:-}])" ;;
  *) if [ "$duerr_total" -ge 1 ] && [ "$duerr_total" = "$duerr_guarded" ]; then
       pass "every ': > \$DU_ERR' truncation carries a guard ($duerr_guarded of $duerr_total)"
     else
       fail "not every ': > \$DU_ERR' truncation is guarded ($duerr_guarded of $duerr_total) — an unguarded one is fatal under set -e, and at section 6c it fires AFTER sections 1..6b have already printed"
     fi ;;
esac

# 🔴 CALL SITES, NOT THE DEFINITION — the fixed-string row above cannot tell
# them apart. MEASURED: deleting BOTH root-only `_report_unreadable` calls left
# the suite green, because `grep -qF -- "_report_unreadable"` is satisfied by the
# definition line alone. That is the declarations-vs-instances error: the row
# counted a SITE and read as though it covered what the site COVERS.
# Same shape as the DU_ERR ledger above — both numbers DERIVED, neither written
# down, and the assertion is a RELATIONSHIP so it fails when the set shrinks.
rru_total="$(grep -cE '_report_unreadable' "$CODE_FILE" || true)"
rru_defs="$(grep -cE '^[[:space:]]*_report_unreadable\(\)' "$CODE_FILE" || true)"
case "${rru_total:-x}${rru_defs:-x}" in
  *[!0-9]*) fail "the _report_unreadable call-site ledger cannot be read (total=[${rru_total:-}] defs=[${rru_defs:-}])" ;;
  *) rru_calls=$((rru_total - rru_defs))
     if [ "$rru_defs" -eq 1 ] && [ "$rru_calls" -ge 2 ]; then
       pass "_report_unreadable is CALLED, not merely defined ($rru_calls call site(s), 1 definition)"
     else
       fail "_report_unreadable has $rru_defs definition(s) and $rru_calls call site(s) — expected 1 and >=2. A stream captured and never reported looks exactly like coverage; the root-only sites are the ones nothing else can see"
     fi ;;
esac

# --------------------------------------------------------------------------- #
echo "== 7b. THE set -e SWEEP — an unguarded statement is a truncated report =="
# 🔴 WHY A SCANNER AND NOT MORE `required` ROWS. Twice now the set of "commands
# whose failure kills the report" was enumerated BY EYE and came up short: round
# 1 guarded the two pipelines in section 5 and left the bare `du -sh` total two
# lines below them, under a comment describing the treatment it did not have.
# A `required` row pins the site you already thought of; this pins the SHAPE, so
# the site nobody thought of is the one it catches.
#
# The scan: strip whole-line comments, join backslash continuations, keep every
# line whose FIRST word is a failure-prone external, drop those containing `||`.
# Under `set -euo pipefail` every one of those is fatal — including a pipeline
# whose LAST stage succeeds, because `pipefail` promotes any stage's status.
#
# 🔴 WHAT IT DOES NOT COVER, so nobody reads it as wider than it is: it looks at
# the first WORD only, so `VAR=$(cmd)` assignments (also checked under `set -e`)
# are invisible to it, and so is a fatal command that is not the head of its
# line. Those are handled by the `required` rows above and by the sweep comment
# in the script's own executable region, which lists the categories left open on
# purpose and why. This is a net, not a proof.
SWEEP_EXT='(find|du|ls|dumpe2fs|findmnt|lsof|stat|xargs|grep|sed|sort|uniq|wc|tr|awk|mktemp|seq|dd|id)'
sweep() { # $1 = file -> every unguarded statement-level external, one per line
  grep -v '^[[:space:]]*#' "$1" \
    | sed -e ':a' -e '/\\$/N' -e 's/\\\n/ /' -e 'ta' \
    | grep -E "^[[:space:]]*(\{ )?$SWEEP_EXT[[:space:]]" \
    | grep -v '||'
}

# 🔴 POSITIVE CONTROL FIRST, and built from lines shaped like the REAL defect —
# a bare `du -sh`, and a multi-line pipeline whose `||` would have to be on a
# continuation line. A scanner that returns "no unguarded statements" is
# indistinguishable from one wired to nothing until it has been watched to
# return a non-zero count.
SWEEP_CANARY="$TMP/sweep-canary.txt"
{
  printf '%s\n' 'du -sh /var/lib/x 2>/dev/null'
  printf '%s\n' '  ls -A /tmp 2>/dev/null | sed -E "s/x//" \'
  printf '%s\n' '    | sort | uniq -c'
  printf '%s\n' 'du -sh /guarded 2>/dev/null || echo "COULD NOT MEASURE"'
  printf '%s\n' '# du -sh /this-is-a-comment'
} > "$SWEEP_CANARY"
canary_hits="$(sweep "$SWEEP_CANARY" || true)"
canary_n="$(printf '%s\n' "$canary_hits" | grep -c . || true)"
[ "$canary_n" -eq 2 ] && pass "POSITIVE CONTROL: the sweep finds both unguarded canary statements (2)" \
  || fail "POSITIVE CONTROL FAILED: the sweep found $canary_n unguarded lines in a canary holding exactly 2 — its zero over the real script would mean nothing"
lacks "the sweep does NOT flag a guarded canary line" "$canary_hits" "/guarded"
lacks "the sweep does NOT flag a commented canary line" "$canary_hits" "/this-is-a-comment"

# 🔴 A LEDGER, failing when the set GROWS *or* SHRINKS. TWO lines in the script
# are reported and are not guarded — the count is asserted below, so read it
# there rather than trusting this sentence, which said "One line" for a round
# after the second was pinned seven lines down. The first is section 2's
# `find "$d" … | awk …` is the body of a `read … < <(…)` process substitution,
# and MEASURED 2026-09-07 a process substitution's status is NOT checked by
# `set -e` — which is exactly why denials in section 2 do not kill the run. If
# that line ever moves out of the process substitution, this ledger goes red.
sweep_hits="$(sweep "$SCRIPT" || true)"
sweep_n="$(printf '%s\n' "$sweep_hits" | grep -c . || true)"
[ "$sweep_n" -eq 2 ] && pass "exactly two statement-level externals are unguarded, as pinned" \
  || fail "the set -e sweep found $sweep_n unguarded statement-level commands, expected the 2 pinned below — a new one truncates the report with no message: [$sweep_hits]"
has "pinned #1: section 2's process-substitution find" \
    "$sweep_hits" 'find "$d" -xdev -printf'
# 🔴 PINNED #2, ADDED 2026-09-09: the `find` inside toplevel_accountable_entries().
#
# 🔴 THERE IS NO BEHAVIOURAL REASON TO LEAVE IT UNGUARDED, AND THIS COMMENT NO
# LONGER OFFERS ONE. It previously claimed that `|| true` "would MASK a short
# enumeration". That is FALSE, and it was the THIRD rationale supplied for this
# function after two others were retracted as false — which is why none is
# offered now. MEASURED, bash 5.3.15: a `find` that emits 2 records and then
# exits non-zero is consumed IDENTICALLY with and without `|| true` — 2 records,
# run continues, rc 0 both ways. Nothing reads the status (the only caller is a
# process substitution, whose exit status bash does not check), and masking
# requires a reader. The entry is here because the sweep COUNTS it, which is
# bookkeeping and needs no justification.
#
# 🔴 A clause saying "adding `|| true` would change nothing OBSERVABLE either"
# stood here and was FALSE — six lines above the assertion that disproves it.
# It makes THIS ledger go red: `sweep_n` drops to 1 and both the count and
# `pinned #2` fail. Nothing BEHAVIOURAL changes; the ledger is an observation.
# The overreach was one clause past a correct sentence, which is where these
# keep happening.
#
# 🔴 RETRACTED: this comment used to say the enumerator's stderr goes nowhere,
# "so a top-level entry that cannot be stat'd is dropped from section 2 with no
# tally", and pointed at the site ledger as recording that debt. BOTH HALVES WERE
# FALSE. The enumerator DOES NOT DROP unstattable entries, because it never stats
# them: `-name` and `-print0` at `-maxdepth 1` need only the readdir NAME, so
# there is nothing to fail. MEASURED over a fixture holding a mode-000 directory,
# a dangling symlink, a regular file and a directory — all four emitted, ZERO
# bytes on stderr. And the ledger it pointed at never listed the enumerator, so
# the pointer was circular: it sent a reader to a register that does not name it.
# The claim originated as "a FIFTH same-shape site" and propagated to four places
# before anyone checked the shape it was said to share.
# Its stderr genuinely is unredirected — that is true and is why the sweep counts
# it — but an unredirected stderr on a command that cannot fail per-entry is not
# a silent drop.
has "pinned #2: the top-level enumerator's find" \
    "$sweep_hits" 'find "$root" -mindepth 1 -maxdepth 1'

# 🔴 A SECOND LEDGER, OVER WHAT THE SWEEP ABOVE STRUCTURALLY CANNOT SEE — and it
# exists because the CLAIM and the SCAN disagreed. The script's sweep comment
# read "Everything that scan reports is guarded above EXCEPT these" and then
# listed the two `out=$(… | sort …)` captures, which a reader takes for the
# complete inventory of deliberate `set -e` exposure. It is not: the sweep keys
# on a line's FIRST WORD and drops any line containing `||`, so a pipeline
# headed by `for`/`done`/`printf` is invisible to it, and so is one whose EARLY
# stages are guarded while its LAST stage is not. THREE statement-level
# pipelines ending in an unguarded `sort` were therefore never in either the
# scan or the list.
#
# `sort` is deliberately never guarded — the whole point of `head_n` is that
# `sort` never takes SIGPIPE, and MEASURED, a `|| true` on it breaks both
# `sigpipe-head-closes-the-pipe` rows. So this ledger does not demand a guard.
# It pins the NUMBER, failing when the set GROWS *or* SHRINKS, so the comment
# and the code cannot drift apart again silently.
#
# The discriminator is positional and needs no guess: take the text AFTER the
# LAST `sort` on the joined line, and treat the site as guarded only if a `||`
# appears there. Section 6d's entry-name families is the one line where it does
# (`{ … | sort | uniq -c | sort -rn | head_n 20; } || true`).
sort_sites() { # $1 = file -> joined lines whose LAST sort stage carries no ||
  local l
  grep -v '^[[:space:]]*#' "$1" \
    | sed -e ':a' -e '/\\$/N' -e 's/\\\n/ /' -e 'ta' \
    | grep -E '\| *sort[[:space:]]' \
    | while IFS= read -r l; do
        case "${l##*sort}" in *'||'*) : ;; *) printf '%s\n' "$l" ;; esac
      done
}

# 🔴 THE CANARY EXERCISES BOTH BRANCHES, so one file is the positive control AND
# the negative one: two unguarded sort stages (a statement-level pipeline the
# first-word sweep would miss, and a multi-line `out=$(…)` capture), one guarded
# group that must be EXCLUDED, one sort-free line, one commented sort.
SORT_CANARY="$TMP/sort-canary.txt"
{
  printf '%s\n' 'done | sort -rn | head_n 5'
  printf '%s\n' 'out=$(find /b \'
  printf '%s\n' '  | sort -rh | head_n 15)'
  printf '%s\n' '{ ls /c | sort -rn | head_n 5; } || true'
  printf '%s\n' 'ls /d | wc -l'
  printf '%s\n' '# ls /e | sort -rn'
} > "$SORT_CANARY"
sort_canary_hits="$(sort_sites "$SORT_CANARY" || true)"
sort_canary_n="$(printf '%s\n' "$sort_canary_hits" | grep -c . || true)"
[ "$sort_canary_n" -eq 2 ] && pass "POSITIVE CONTROL: the sort ledger finds both unguarded canary sort stages (2)" \
  || fail "POSITIVE CONTROL FAILED: the sort ledger found $sort_canary_n unguarded sort stages in a canary holding exactly 2 — its count over the real script would mean nothing"
lacks "the sort ledger EXCLUDES a sort inside a { … } || true group" "$sort_canary_hits" "/c"
lacks "the sort ledger does NOT flag a commented sort" "$sort_canary_hits" "/e"

sort_hits="$(sort_sites "$SCRIPT" || true)"
sort_n="$(printf '%s\n' "$sort_hits" | grep -c . || true)"
[ "$sort_n" -eq 5 ] && pass "exactly five unguarded sort stages, as the script's sweep comment now says" \
  || fail "the sort ledger found $sort_n unguarded sort stages, expected the 5 the script's sweep comment enumerates — a new one is an exposure nobody wrote down: [$sort_hits]"
# Named individually, because a bare 5 could be any five. The three below are
# precisely the ones the first-word sweep cannot reach.
has "the sort ledger names section 5's PVC listing" "$sort_hits" 'du -sh --exclude=/mnt'
has "the sort ledger names section 5's inodes-per-PVC loop" "$sort_hits" 'done | sort -rn | head_n 15'
has "the sort ledger names section 6c's /home listing" "$sort_hits" '< "$ONROOT_LIST"'
has "the sort ledger names the size-breakdown capture" "$sort_hits" 'du -sh -x 2>>"$SCAN_DUERR"'
has "the sort ledger names the inode-breakdown capture" "$sort_hits" '| sort -rn | head_n 15)'

# --------------------------------------------------------------------------- #
echo "== 12. SECTION 2 ACCOUNTS FOR TOP-LEVEL FILES, NOT ONLY DIRECTORIES =="
# 🔴 The defect this pins: section 2's loop read `[ -d "$d" ] || continue`, so a
# top-level entry that was not a directory never reached `find`. MEASURED on the
# workbench 2026-09-09: `/swapfile` is a regular file of 100663328 512B blocks =
# 48.00 GiB on the same device as `/`, and it got NO ROW in section 2 at all.
#
# 🔴 WHICH NUMBER MOVED: section 2's BYTE COLUMN lost 48 GiB. Section 3's
# residual is `INODES_USED - TOTAL_INODES` — a count of INODES — so it moved by
# exactly ONE. This comment previously said the miss "shows up as unexplained
# space", which is the same false mechanism the script's own banner was corrected
# for; it survived here because the sweep that fixed the banner never reached the
# test file.
#
# The fixture uses a REGULAR FILE at the top level (the /swapfile shape), a
# directory, a symlink, a fifo, and a skip-listed name. 🔴 The symlink and the
# fifo MUST BE LISTED — see the assertions below and the reasoning with them.
# An earlier version of this paragraph said the symlink "must not" be listed,
# 25 lines above an assertion requiring that it is (MEASURED at `c68750f6`, the
# squash that landed on main: the comment sat at 1383, the assertion at 1408.
# Re-anchored from a PR-branch sha that was NOT an ancestor of main, so the
# figure it carried was unresolvable from a fresh clone — an earlier retelling said
# "eleven", a decorative specific inside a retraction about false specifics).
# A maintainer resolving
# that contradiction toward the comment would have reinstated an exclusion that
# was measured to make section 3's residual WORSE.
tl="$TMP/toplevel"
mkdir -p "$tl/realdir" "$tl/proc" "$tl/.hiddendir"
printf 'x' > "$tl/swapfile"
printf 'x' > "$tl/.hiddenfile"
ln -s realdir "$tl/linkdir"
# 🔴 NO `|| true` HERE, AND THERE IS NO BEHAVIOURAL REASON FOR THAT — this
# comment supplies none, because the two it supplied before were both false.
#   - draft 1: "with `|| true` a host without mkfifo creates no path and the
#     assertion below passes vacuously." True when the assertion was `lacks`;
#     it is `has` now, which FAILS on a missing entry either way.
#   - draft 2: "it fails HERE, naming the missing tool, instead of three lines
#     later as a confusing assertion failure." FALSE, and it assumed `set -e`.
#     🔴 THIS SUITE IS `set -uo pipefail` AT THE TOP — NO `-e`. (No line number:
#     the file grows every round and the number would rot; `grep -n '^set -' on
#     this file` answers it.) MEASURED with a
#     stand-in returning 127: guarded and unguarded agree on every outcome that
#     matters — same rc 1, same `ok:` count, the SAME single failure
#     (`a fifo is counted …`), and the same
#     🔴 NOT "byte-identical", and NOT a fixed ok-count. An earlier version of
#     this comment said both, and both were wrong: stdout differs at the lines
#     carrying random `mktemp` names, and the quoted count (221) was stale the
#     moment it was written — the same commit added assertions. Quote the
#     INVARIANT (the two variants agree), never a running total that the next
#     assertion invalidates.
#     `command not found` on stderr. Nothing fails "here"; the run continues
#     through every remaining assertion, and the assertion failure arrives in
#     BOTH variants, 15 lines later rather than three.
# Keep it unguarded because `|| true` would add noise that buys nothing. That is
# a style preference, not a mechanism, and it is stated as one.
mkfifo "$tl/afifo"

# The function emits NUL-separated names; render them one per line to assert on.
tl_out="$(toplevel_accountable_entries "$tl" | tr '\0' '\n')"

has "a top-level regular FILE is accounted for (the /swapfile shape)" "$tl_out" "$tl/swapfile"
has "a top-level directory is still accounted for"                    "$tl_out" "$tl/realdir"
lacks "a skip-listed name is excluded"                                "$tl_out" "$tl/proc"
# 🔴 THESE TWO ARE `has`, NOT `lacks`, AND THAT IS THE POINT. An earlier draft
# excluded symlinks and fifos on two rationales that were MEASURED FALSE:
# `find` without `-L` does not descend a symlink (so nothing is walked twice),
# and `-printf %b` is 0 for both (so nothing is over-counted) — while each is a
# real used inode, and section 3 reconciles INODES. Excluding them made the
# residual worse. Re-introducing either exclusion must fail here.
has "a top-level SYMLINK is counted — 1 inode, 0 blocks, find does not descend" "$tl_out" "$tl/linkdir"
has "a fifo is counted — a real used inode that section 3 must reconcile"       "$tl_out" "$tl/afifo"
# 🔴 DOTFILES. `"$root"/*` does not match them, so a top-level `.journal` and its
# entire subtree were invisible — the same defect this function exists to fix.
has "a top-level DOTFILE is accounted for"   "$tl_out" "$tl/.hiddenfile"
has "a top-level DOT-DIRECTORY is accounted for" "$tl_out" "$tl/.hiddendir"

# 🔴 THE CONTROL THAT MAKES THE ABOVE A REGRESSION TEST RATHER THAN A CONTRACT
# TEST. The helper is new, so "it lists the file" would pass vacuously against
# any implementation. This re-runs the RETIRED predicate over the same fixture
# and asserts it does NOT list the file — so the two disagree on exactly the
# entry that cost 48 GiB. Reintroducing a `-d`-only guard makes this fail.
retired_out=""
for e in "$tl"/*; do
  case "${e##*/}" in proc|sys|dev|run|mnt) continue ;; esac
  [ -d "$e" ] || continue          # <-- the retired guard, verbatim
  [ -L "$e" ] && continue
  retired_out+="$e"$'\n'
done
lacks "CONTROL: the retired -d-only guard MISSES the top-level file" "$retired_out" "$tl/swapfile"
has   "CONTROL: the retired -d-only guard did list the directory"    "$retired_out" "$tl/realdir"
# The retired guard used a bare glob, which is ALSO why it never saw dotfiles.
lacks "CONTROL: the retired glob MISSES the top-level dotfile" "$retired_out" "$tl/.hiddenfile"

# A root argument of "/" must not produce doubled slashes: "//etc" reads as a
# different path in the report and would not match any later pathspec. "//" is
# checked too — `${root%/}` strips only ONE trailing slash.
root_out="$(toplevel_accountable_entries /  | tr '\0' '\n')"
root2_out="$(toplevel_accountable_entries // | tr '\0' '\n')"
root_n="$(printf '%s\n' "$root_out"  | grep -c . || true)"
root2_n="$(printf '%s\n' "$root2_out" | grep -c . || true)"
# 🔴 POSITIVE CONTROL FIRST, AND IT IS NOT DECORATION. A `lacks "//"` assertion
# passes on EMPTY output, so on its own it cannot tell a correct enumeration from
# one that produced nothing at all. MEASURED: deleting `[ -n "$root" ] || root=/`
# makes `${root%/}` yield "" and `find ""` fail, and every doubled-slash
# assertion below then passed VACUOUSLY — a surviving mutant in a fully green
# suite. These two counts are what make the two `lacks` below mean anything.
[ "$root_n" -ge 5 ] && pass "POSITIVE CONTROL: a root of '/' enumerates ($root_n entries)" \
  || fail "a root of '/' enumerated $root_n entries — the assertions below would pass vacuously"
[ "$root2_n" -eq "$root_n" ] && pass "a root of '//' enumerates the same count as '/' ($root2_n)" \
  || fail "'//' enumerated $root2_n entries but '/' enumerated $root_n"
lacks "a root of '/' yields no doubled-slash paths"  "$root_out"  "//"
lacks "a root of '//' yields no doubled-slash paths" "$root2_out" "//"

# 🔴 A NEWLINE IN A TOP-LEVEL NAME MUST NOT SPLIT INTO PHANTOM PATHS. With a
# newline-delimited enumeration the consumer loop ran 4 iterations for 3 entries,
# and `find` then logged the two nonexistent paths into DENIED_LOG where section
# 4 classifies them as "vanished mid-scan (benign, transient)" — an under-count
# reported as harmless. NUL-delimited output is what closes that.
nl_dir="$TMP/nlroot"; mkdir -p "$nl_dir"; : > "$nl_dir/has
newline"
nl_count="$(toplevel_accountable_entries "$nl_dir" | tr -cd '\0' | wc -c)"
eq "a name containing a newline is emitted as ONE NUL-terminated record" "$nl_count" "1"

# --------------------------------------------------------------------------- #
echo "== 12b. SECTION 2'S CONSUMER IS PINNED TO THE NON-SUBSHELL FORM =="
# 🔴 The script carries a 🔴 comment saying a `| while` refactor would run the
# body in a subshell, discard TOTAL_INODES/TOTAL_DEDUPED and make section 3's
# residual compute from zero — "a wrong answer that prints cleanly". That hazard
# was asserted ONLY by comment: rewriting the consumer to a pipe left the whole
# suite green, because section 12 drives the helper in isolation and never reads
# the script's source. This ledger is what makes the comment enforceable.
consumer_src="$(grep -n 'toplevel_accountable_entries' "$SCRIPT")"
has "section 2 consumes the enumerator via process substitution" \
    "$consumer_src" 'done < <(toplevel_accountable_entries /)'
lacks "section 2 does NOT pipe the enumerator into while (subshell would drop the totals)" \
    "$consumer_src" 'toplevel_accountable_entries / |'
has "the consumer reads NUL-delimited records" \
    "$(grep -n 'read -r -d' "$SCRIPT")" "read -r -d '' d"

# --------------------------------------------------------------------------- #
echo "== 13. UNTALLIED-DROP SITE LEDGER — pinned two-way =="
# 🔴 THIS REPLACES A PROSE REGISTER THAT WAS SHORT TWICE IN TWO ROUNDS.
# The script used to carry a numbered list of the places that enumerate depth-1
# and DROP entries they cannot stat without tallying the drop. Round N found it
# missing one and added "the FIFTH site"; round N+1 found the ordinals were
# themselves the defect — a numbered list reads as CLOSED, and the section-5 PVC
# loop had never been in it. An ordinal is a claim about a SET; nothing checked
# the set, so each fix made the register more confidently wrong.
#
# The criterion: a depth-1 enumeration that drops unstattable entries with no
# tally, so the count it feeds is a FLOOR presented as a total.
#
# 🔴 THIS LEDGER FAILS WHEN THE SET GROWS *OR* SHRINKS. A new site is a new
# untallied drop nobody wrote down; a vanished one means the shape changed and
# the criterion needs re-reading. Either way a human must look. Adding a site
# here is NOT the fix for finding one — recording it is the fix; tallying it is
# a different, larger change (bash's `[ -d ]` cannot separate "not a directory"
# from "stat refused" from "unmatched glob").
# 🔴 THE SCAN JOINS LINES, because a one-line-at-a-time version had a live hole.
# Two earlier versions each shipped a hole and a sentence excusing it:
#   v1 grepped the literal `[ -d "$p" ] || continue`. An audit injected
#      `if [ ! -d "$entry" ]; then continue; fi` — suite GREEN over a live hazard.
#   v2 widened to one permissive single-line pattern and declared a MULTI-LINE
#      `if` to be a blind spot "no line-based scan can close". THAT WAS FALSE, and
#      the hole was live: a site spelled across four lines scanned as absent while
#      the ledger printed "exactly 2 … as pinned". It is closable by joining each
#      candidate line with the next few — and THIS FILE ALREADY LINE-JOINS TWICE
#      (the `sed -e ':a' -e '/\\$/N'` continuation handling in sections 7b/11), so
#      the excuse was refuted by code 300 lines away.
# The lesson, which is the reason this comment is long: a guard that names its own
# blind spot is asserting something, and the assertion needs checking like any
# other. "No scan can do this" is the single easiest claim to get wrong.
#
# 🔴 THE REMAINING BLIND SPOT IS ONE, NOT TWO: a guard delegated to a HELPER
# FUNCTION, because no textual scan follows a call. The window is 3 lines, so a
# guard spread wider than that also escapes — the canary below pins the window.
#
# 🔴 COMMENTS **AND** `echo` LINES ARE EXCLUDED, and the `echo` half is not
# hypothetical: widening the pattern immediately matched section 2's own banner,
# which QUOTES the guard while explaining the defect. A ledger that counts a
# sentence about the hazard as an instance of it is declarations-vs-instances.
#
# `drop_guard_sites <file>` → one line number per site. Shared by the ledger and
# by both canaries, so a canary cannot pass against a different implementation
# than the one under test.
drop_guard_sites() {
  awk '
    { L[NR] = $0 }
    END {
      for (i = 1; i <= NR; i++) {
        s = L[i]
        if (s ~ /^[[:space:]]*#/ || s ~ /^[[:space:]]*echo /) continue
        if (s !~ /\[\[?[^]]*-d[[:space:]]/) continue
        j = s
        for (k = i + 1; k <= i + 3 && k <= NR; k++) j = j " " L[k]
        if (j ~ /continue/) print i
      }
    }' "$1"
}
bracket_sites="$(drop_guard_sites "$SCRIPT" | tr '\n' ' ')"
bracket_n="$(printf '%s' "$bracket_sites" | wc -w)"

# 🔴 A REAL CANARY, NOT A SELF-CHECK. The previous "positive control" asserted
# `bracket_n >= 1`, which is strictly IMPLIED by the `-eq 2` below — there is no
# state where it fires and the count passes, so it detected nothing. Worse, it
# PASSED on the degradation it named: rewording one site to `[ -d "${p}" ]` left
# it green at "1 site(s)" while the count misreported a rewording as a deletion.
# This builds an INDEPENDENT fixture with a known answer, the way section 11's
# control does — running the suspect instrument over the file under test is a
# second sample of the same unknown, not a control.
canary_f="$TMP/dropguard-canary.sh"
{ printf '    [ -d "$p" ] || continue\n'
  printf '    [ -d "${q}" ] || continue\n'
  printf '    [[ -d $r ]] || continue\n'
  printf '    if [ ! -d "$s" ]; then continue; fi\n'
  # 🔴 THE MULTI-LINE SHAPE — the one a previous version declared unclosable and
  # then failed to catch on a live site. It is the whole reason for the join.
  printf '    if [ ! -d "$t" ]\n    then\n      continue\n    fi\n'
  printf '    echo "not a drop guard at all"\n'; } > "$canary_f"
canary_n="$(drop_guard_sites "$canary_f" | grep -c .)"
[ "$canary_n" -eq 5 ] \
  && pass "CANARY: the scan finds all 5 spellings, multi-line included, in a fixture holding exactly 5" \
  || fail "CANARY FAILED: found $canary_n of 5 known spellings — the count over the real script means nothing until this passes"
# 🔴 WINDOW PIN. The join looks 3 lines ahead; a guard spread wider escapes, and
# that is a real residual limit rather than a hypothetical. Pinning it means the
# window cannot be narrowed without a red suite, and the number cannot rot into
# prose that says one thing while the code does another.
canary_wide="$TMP/dropguard-canary-wide.sh"
printf '    if [ ! -d "$u" ]\n    then\n\n\n      continue\n    fi\n' > "$canary_wide"
canary_wide_n="$(drop_guard_sites "$canary_wide" | grep -c . || true)"
[ "$canary_wide_n" -eq 0 ] \
  && pass "CANARY: a guard spread beyond the 3-line join window is NOT found — the documented residual limit, pinned" \
  || fail "CANARY FAILED: expected the wide-spread guard to escape the 3-line window, found $canary_wide_n"
# 🔴 THE OTHER HALF OF THE WINDOW — without this the pin is one-sided and the
# comment above overstates it. MEASURED: the window could be NARROWED from 3 to
# 2 with the suite fully green (229 ok, 0 FAIL), because the 5-spelling canary's
# widest shape puts `continue` at i+2 and the wide canary puts it at i+4 —
# NOTHING exercised i+3. A narrowed window then made a real multi-line site
# scan as absent while the ledger printed "exactly 2 … as pinned", which is
# byte-for-byte the failure this section was built to close. This fixture puts
# `continue` at EXACTLY i+3 and asserts it is FOUND.
canary_edge="$TMP/dropguard-canary-edge.sh"
printf '    if [ ! -d "$v" ]\n    then\n      : placeholder\n      continue\n    fi\n' > "$canary_edge"
canary_edge_n="$(drop_guard_sites "$canary_edge" | grep -c . || true)"
[ "$canary_edge_n" -eq 1 ] \
  && pass "CANARY: a guard with continue at EXACTLY the window edge (i+3) IS found — the window cannot be narrowed" \
  || fail "CANARY FAILED: the window-edge guard was not found ($canary_edge_n) — the join window has been narrowed, and a multi-line site now scans as absent"
# 🔴 NEGATIVE half, and it pins the EXACT over-match that occurred. Widening the
# pattern matched section 2's banner, which quotes the guard while describing the
# defect. The filtered pipeline — not the bare regex — is what must reject it, so
# the canary exercises the pipeline.
canary_negf="$TMP/dropguard-canary-neg.sh"
{ printf '%s\n' 'echo "  dropped by a '"'"'[ -d ] || continue'"'"' guard, so it had NO ROW"'
  printf '%s\n' '# a comment mentioning [ -d "$p" ] || continue while explaining it'
  printf '%s\n' '    echo "continue past the -d flag in prose"'; } > "$canary_negf"
canary_neg="$(drop_guard_sites "$canary_negf" | grep -c . || true)"
[ "$canary_neg" -eq 0 ] \
  && pass "CANARY: prose QUOTING the guard (banner echo, comment) is not counted as a site" \
  || fail "CANARY FAILED: $canary_neg line(s) of prose about the hazard were counted as instances of it — the count is inflated"

[ "$bracket_n" -eq 2 ] \
  && pass "exactly 2 untallied-drop sites, as pinned" \
  || fail "the untallied-drop ledger found $bracket_n site(s), expected the 2 pinned below — a new one is a FLOOR presented as a total that nobody wrote down; a vanished one means the shape changed. Lines: [$bracket_sites]"

# Name them, so the failure above is actionable and so a SWAP (one site removed,
# another added) cannot pass on the count alone.
sites_ctx="$(grep -n -B2 '\[ -d "\$p" \] || continue' "$SCRIPT")"
has "pinned site: split_by_device's foreign-entry loop" \
    "$sites_ctx" 'for p in "$base"/*/* "$base"/*/.*'
has "pinned site: section 5's per-PVC inode loop" \
    "$sites_ctx" 'for p in /var/lib/rancher/k3s/storage/*'

# --------------------------------------------------------------------------- #
echo "== 14. EVERY SHA THESE FILES CITE MUST BE REACHABLE FROM main =="
# 🔴 THIS IS THE PROSE RULE, MACHINE-CHECKED. Both files carry the sentence "a
# positional or historical number is legal only with a sha a reader can resolve".
# That sentence is itself a claim nothing checked — which is the exact generator
# five audit rounds were spent on. MEASURED: after the round that WROTE the rule,
# THREE cited shas were not ancestors of main. Two survived in this very file,
# 650 and 1430 lines from the one that was deleted for the same defect; they were
# reachable from NO ref, surviving only as loose objects until the next `gc`. A
# fresh clone of main resolves none of them, so the numbers they anchor cannot be
# checked by the reader the rule exists to serve.
#
# A hex string is a candidate only if it contains a letter — that excludes the
# decimal figures these files quote (block counts, inode counts) without an
# allowlist anyone has to maintain.
#
# 🔴 IT DEGRADES TO "COULD NOT MEASURE", NEVER TO A PASS. The sandbox tier builds
# from a store copy with NO .git, so this check cannot run there; a silent green
# would be a claim about git's absence, not about the files.
sha_files="$SCRIPT $BASH_SOURCE"
if ! command -v git >/dev/null 2>&1; then
  pass "COULD NOT MEASURE: git is not on PATH — sha reachability unchecked (expected in the sandbox tier)"
elif ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  pass "COULD NOT MEASURE: $ROOT is not a git checkout — sha reachability unchecked (expected in the sandbox tier)"
else
  sha_bad=""; sha_seen=0
  for f in $sha_files; do
    for h in $(grep -oE '\b[0-9a-f]{8,40}\b' "$f" | grep -E '[a-f]' | sort -u); do
      git -C "$ROOT" cat-file -e "${h}^{commit}" 2>/dev/null || continue
      sha_seen=$((sha_seen + 1))
      git -C "$ROOT" merge-base --is-ancestor "$h" HEAD 2>/dev/null || sha_bad="$sha_bad $h"
    done
  done
  # POSITIVE CONTROL: a run that resolved NO sha proves nothing. If the files
  # cite none, that is itself the reportable state, not a pass.
  if [ "$sha_seen" -eq 0 ]; then
    fail "the sha ledger resolved ZERO commit-shas in these files — either the extraction broke or every citation was removed; a zero here is not a clean result"
  elif [ -z "$sha_bad" ]; then
    pass "all $sha_seen cited sha(s) are ancestors of HEAD — resolvable from a fresh clone"
  else
    fail "cited sha(s) NOT reachable from HEAD:$sha_bad — a reader cannot resolve the number each one anchors, which is the defect the 'only with a sha' rule exists to stop. Re-anchor to a commit on the mainline, or delete the figure"
  fi
fi

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
