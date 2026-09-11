#!/usr/bin/env bash
# Root-privileged disk accounting for the workbench root filesystem.
#
# WHY THIS EXISTS: its predecessor `scripts/diagnose-nix-disk.sh` ran
# `find "$d" 2>/dev/null` as an unprivileged user. Every root-only tree (/root,
# /var/lib/docker, /var/lib/kubelet, /var/lib/private,
# /var/lib/rancher/k3s/storage) is skipped SILENTLY by that, so its inode and
# byte counts are floors, not totals — and the 2026-08-31 handoff read the
# resulting shortfall as "ext4 metadata overhead". ext4 does not consume *used*
# inodes for metadata, so that reading cannot be right: every used inode is a
# real file or directory.
#
# 🔴 THAT PREDECESSOR IS DELETED (2026-09-09) — do not go looking for it. It was
# committed to main by #1412 without anyone noticing it had already been
# superseded, then deleted once the question was actually asked. It had no tests,
# and no code referenced it. (Its ROOT run was never seen to finish — it died at
# /nix; `claudedocs/handoff-nix-disk-cleanup.md` is the record. Unprivileged runs
# DID complete, so "never ran to completion" is wider than the evidence.)
#
# 🔴 NO SUBSUMPTION MAPPING IS GIVEN HERE, DELIBERATELY. Two have been written
# and BOTH were wrong; a third is not worth your trust. Read the two files if you
# need to know what moved — `git show c68750f6^:scripts/diagnose-nix-disk.sh`.
# The retracted drafts, recorded so nobody derives a fourth:
#   - draft 1: "every one of its ten sections is subsumed here." False — it
#     claimed a total nobody had checked.
#   - draft 2: a per-section map plus a loss list. Wrong in four places, measured
#     2026-09-10: it said the predecessor grepped the FULL `dumpe2fs -h` output
#     (it greps a fixed 10-term list, exactly like section 1 — only the LISTS
#     differ); it listed `Free blocks`/`Free inodes` as lost, though both are read
#     and USED here via `stat -f -c %f/%d`; it claimed "reserved-block count and
#     journal size … is what the residual arithmetic actually consumes", when the
#     only dumpe2fs field any arithmetic reads is `Inode size`; and it kept a
#     sentence from draft 1 ("the one thing it displayed that this file does not
#     is `swapon --show`") that its own loss list six lines above contradicted.
# The lesson generalises past this comment: a hand-derived correspondence between
# two programs is a CLAIM, and the fact that the previous draft of it was wrong is
# better information than any new draft.
#
# What IS solid, because it was measured rather than mapped: chasing the one
# display difference — the predecessor ran `swapon --show` and `ls -lh /swapfile`
# — is what surfaced the `/swapfile` defect fixed in
# toplevel_accountable_entries().
#
# This script must run as root. It counts what the previous one could not, and it
# reports its own blind spots (denied directories) instead of hiding them.
#
#   sudo ./scripts/diagnose-disk-accounting.sh
#
set -euo pipefail

# =============================================================================
# SOURCEABLE SEAM — everything down to the `return` below is pure text/tree
# transforms that need NO root, so `scripts/tests/test_diagnose_disk_accounting.sh`
# can drive them against fixtures. The defects each one carries a comment about
# were all shipped once and were invisible to the merge gate because this file
# had no test and this repo has no shellcheck gate.
#
# `source`ing this file defines the helpers and RUNS NOTHING. The seam test is
# `(return 0 2>/dev/null)`: at the top level of a subshell, `return` SUCCEEDS
# when the enclosing shell is executing a sourced file and FAILS otherwise, and
# the diagnostic is discarded, so neither branch prints anything.
#
# 🔴 IT USED TO READ `[ "${BASH_SOURCE[0]}" != "$0" ]`, and the sentence that
# stood here — "the guard is not reachable from the environment" — was FALSE.
# bash imports BASH_SOURCE from the environment as an ordinary scalar, so
# `env 'BASH_SOURCE=(nope)' bash scripts/diagnose-disk-accounting.sh` took the
# SOURCED branch and hit `return` at top level: rc 2 and no report at all — and
# rc 2 is this script's own "you forgot sudo" status, so two unrelated causes
# shared one exit code. `bash < script` / `bash -s` was the mirror image:
# BASH_SOURCE unset, `set -u`, dead on the guard's own line.
#
# MEASURED for the replacement (2026-09-07, bash 5.3 on this host): `bash FILE`,
# `./FILE`, `bash < FILE`, `bash -s < FILE` and `env 'BASH_SOURCE=(nope)' bash
# FILE` all take the EXECUTE branch; `source FILE` and `bash -c 'source FILE'`
# take the sourced branch, with or without a poisoned BASH_SOURCE. `return`
# reads no variable, so there is no variable left to poison. What is NOT
# claimed: a caller that deliberately `source`s this file gets the no-op branch
# — that is the seam doing its job, not a bypass.
#
# 🔴 `set -euo pipefail` above executes at SOURCE time and leaks into the
# sourcing shell. The test suite re-asserts its own options immediately after
# sourcing; anything else that sources this must do the same.
# =============================================================================

# Print the first $1 lines of stdin. Behaviourally `head -n $1`, minus the
# SIGPIPE it induces UPSTREAM.
#
# 🔴 `<producer> | sort | head -N` IS A SIZE-DEPENDENT ABORT UNDER `pipefail`.
# `head` exits after N lines; as soon as the producer's output exceeds one pipe
# buffer it blocks on the next write, takes SIGPIPE and exits 141, `pipefail`
# promotes that to the pipeline's status and `set -e` kills the whole run — with
# NO message, because nothing wrote one. MEASURED at two sizes on this host:
# 40 entries SURVIVE (sort's output fits a single write that completes before
# head exits, rc 0), 20,000 entries DIE rc 141. The real /tmp had 171,886, so
# every `head` below was a live abort waiting on the directory it summarised.
# That is the same "truncated scan reported as a total" the E2BIG comment
# further down describes, reached by a second route.
#
# awk reads to EOF, so the producer never sees a closed pipe.
head_n() { awk -v n="$1" 'NR<=n'; }

# Summarise `lsof +L1` output read on STDIN. $1 = lsof's exit status, used only
# in the no-rows message.
#
# 🔴 DO NOT HARDCODE THE COLUMN. The original summed $8, which under `+L1` is
# NLINK — 0 for every row by the definition of +L1 — so it printed a hard
# `bytes=0.0 GiB` on every run: the reassuring reading, in the very section
# rewritten to stop emitting a misleading number. Its "two rows give
# bytes=2.0 GiB" control passed only against a fixture shaped to the code.
#
# $7 is correct for `+L1` on this host (MEASURED 2026-09-02: header is
# `COMMAND PID USER FD TYPE DEVICE SIZE/OFF NLINK NODE NAME`) — but the index is
# NOT stable across invocations: plain `lsof -n -P` here emits TID and TASKCMD
# too, putting SIZE/OFF at $9. A fixed index is a latent version dependency, so
# find the column by NAME from the header and fail loudly if it is absent.
#
# 🔴 And never `NR-1` to drop the header. The 2026-09-01 run printed
# `count=-1`: with no output at all NR is 0. A negative count also hid the
# distinction between "lsof found nothing" and "lsof did not run" — the two
# readings that matter most here, since a zero is the reassuring one.
lsof_deleted_summary() {
  local rc="${1:-0}" out
  out="$(cat)"
  if [ -z "$out" ]; then
    printf 'count=0 bytes=0.0 GiB  (lsof exited %s with no rows — no deleted-but-open files)\n' "$rc"
    return 0
  fi
  printf '%s\n' "$out" | awk '
    NR==1 { for (i=1; i<=NF; i++) if ($i == "SIZE/OFF") col=i; next }
    { n++; if (col) s += $col }
    END {
      if (!col) { printf "COULD NOT MEASURE: no SIZE/OFF column in lsof header (rows=%d) — NOT a zero\n", n+0; exit }
      printf "count=%d bytes=%.1f GiB (SIZE/OFF=col %d, resolved from the header)\n", n+0, s/1073741824, col
    }'
}

# Section 7's whole body. `$LSOF_BIN` exists so the suite can drive both the
# not-on-PATH branch and the rows branch without planting a binary.
#
# 🔴 `OUT=$(lsof …); RC=$?` IS UNREACHABLE UNDER `set -e`. lsof documents exit 1
# when it finds nothing, and a command substitution in an assignment is a
# CHECKED command: `set -e` kills the whole script on that line, mid-report,
# with the message already eaten by `2>/dev/null`. Sections 7 and 8 then never
# print and the run ends looking complete. So the no-rows message this function
# carries — the one written specifically to stop a zero being mistaken for a
# non-measurement — could never actually be reached. `|| rc=$?` keeps the status
# without arming `set -e`.
#
# 🔴 `LSOF_BIN` IS SET AT THE SEAM, AND ONLY THE SOURCED BRANCH HONOURS AN
# OVERRIDE. It used to be `LSOF_BIN=${LSOF_BIN:-lsof}` here, ABOVE the seam,
# which is on the execute path: an inherited `LSOF_BIN=/anything` was then what
# a ROOT run executed. In a script whose entire premise is that a local
# unprivileged process must not be able to influence a root run, a test seam
# that widens what root executes is backwards. The execute branch now pins
# `LSOF_BIN=lsof` unconditionally and discards whatever the environment said.
report_deleted_open_files() {
  local out rc=0
  if ! command -v "$LSOF_BIN" >/dev/null 2>&1; then
    echo "COULD NOT MEASURE: lsof not on PATH — this is NOT a zero"
    return 0
  fi
  out="$("$LSOF_BIN" +L1 2>/dev/null)" || rc=$?
  if [ -z "$out" ]; then
    lsof_deleted_summary "$rc" </dev/null
  else
    printf '%s\n' "$out" | lsof_deleted_summary "$rc"
  fi
}

# Device id of $1 IN DECIMAL, or empty. Its own function so the suite can
# substitute a fixture-controlled device map without needing two real
# filesystems.
#
# 🔴 DECIMAL (`%d`), NOT `%D`. `stat -c '%D'` is HEX; `find -printf '%D'` is
# DECIMAL. The two breakdowns below compare a `stat` reading of the base against
# find's per-entry reading, so a hex/decimal mismatch would silently classify
# EVERY entry as foreign and print an empty section — the reassuring reading of
# a broken instrument. One format, one helper, every comparison.
_dev_of() { stat -c '%d' "$1" 2>/dev/null; }

# Is $1 a usable device id? ONE place, because three callers below need the same
# test — `size_breakdown`, `inode_breakdown` and `foreign_entries` — and a
# predicate open-coded at three sites is typically wrong at two of them.
#
# 🔴 DIGITS, not merely non-empty. The value is interpolated into a `sed` script
# by `_on_device` below, so a `/` or a `*` would silently change the expression
# rather than fail. `stat -c '%d'` cannot produce one today — which is an
# argument for checking it in one place, not for trusting it at three.
_dev_is_valid() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

# Read a NUL-separated `<device>\t<path>` stream (find -printf '%D\t%p\0') on
# stdin and emit, NUL-separated, only the paths whose device is $1.
#
# `sed -z` because the whole point of the NUL stream is names containing
# newlines; a `while read` loop would be a `stat` fork per entry, and /tmp had
# 171,886 top-level entries.
_on_device() { sed -z -n "s/^$1\t//p"; }

# The mirror image, for the blind-spot report: the depth-1 entries of the same
# stream that are NOT on device $1.
_not_on_device() { sed -z -n "/^$1\t/!{s/^[0-9]*\t//;p;}"; }

# The depth-1 enumeration all three /tmp sections share, in ONE place.
# $1 = base, $2 = the file find's stderr is kept in, $3.. = extra find
# predicates (`-type d` for the inode breakdown).
#
# 🔴 `%D` MAKES find STAT EVERY ENTRY, AND A FAILED STAT EMITS NO RECORD AT ALL.
# The `-print0` form this replaced still printed the NAME when the stat failed;
# `-printf '%D\t%p\0'` prints nothing, so such an entry vanishes from the size
# breakdown AND the inode breakdown AND the foreign-entry listing at once — and
# `foreign_entries` then affirmatively printed "none".
#
# 🔴 RE-MEASURED 2026-09-07 (round 3) BECAUSE THE FIGURES THAT STOOD HERE COULD
# NOT ALL HAVE BEEN TRUE. They read "116 bytes (4.10.0) / 119 bytes (4.11.0)"
# over ONE fixture — but `-print0` emits the paths it FOUND, not the binary's
# own path, so two builds cannot disagree about the byte count of one fixture.
# One fixture, `/tmp/…/fix/denied` (a 35-character base, mode 0400, holding
# three entries), THREE implementations, each run twice:
#
# THE LOAD-BEARING CONTRAST, and it is the only thing stated here because it is
# the only part that reproduced on every implementation tried:
#
#   -printf '%D\t%p\0'  →  ZERO bytes. The entry is ERASED from the output.
#   -print0             →  the names are still emitted.
#
# Both forms exit non-zero and both write to stderr; `%D` is what loses the DATA,
# which is why this function captures stderr rather than discarding it.
#
# 🔴 EVERYTHING ELSE THAT STOOD HERE WAS WRONG, and it is deleted rather than
# re-measured, because a per-implementation table of byte counts and exit codes
# is a claim nothing checks and everything invalidates. What it got wrong:
#   - "`-print0` exits **0** … it needs no stat". FALSE for the form this
#     function uses: `-xdev` needs each entry's st_dev, so it DOES stat, and it
#     exits 1 on a directory it cannot read. (Without `-xdev` there is no stat
#     and rc is 0 — which is how the wrong claim got written: the probe dropped
#     the flag the function actually passes.)
#   - "the byte counts AGREE across builds". FALSE — measured on one fixture,
#     GNU findutils emitted 332 B and bfs 108 B.
#   - the version labels. The bash PATH resolves `find` to **bfs**, not GNU, and
#     both GNU builds on this host are 4.11.0, not 4.10.0.
# A figure about one fixture on one build is not evidence about this function.
#
# Root is NOT immune, which is what makes this worth code rather than a note. A
# FUSE mountpoint not mounted `allow_other` (an AppImage's /tmp/.mount_*, gvfs,
# sshfs), or an entry on a device answering ESTALE/EIO, fails `stat` for uid 0
# too — and /tmp is exactly where those live.
#
# So the stderr is KEPT, in a file, and all three of THIS function's callers
# report the count. Same reason section 2's find writes to $DENIED_LOG instead of
# /dev/null: a scan that reports a number with no denial count is a FLOOR
# presented as a total.
#
# 🔴 "ALL THREE OF THIS FUNCTION'S CALLERS", NOT "EVERY CALLER" — the wider
# wording stood here for a round and was false.
#
# 🔴 THE UNTALLIED-DROP SITES ARE NOT NUMBERED HERE ANY MORE, AND THAT IS THE
# FIX. A prose register of them has now been SHORT TWICE in consecutive audit
# rounds: one round found it missing `toplevel_accountable_entries()` and added
# it as "the FIFTH site"; the next round found the ordinals themselves were the
# defect, because a numbered list reads as CLOSED and the section-5 PVC loop had
# never been in it. An ordinal is a claim about a SET, and nothing was checking
# the set — so each fix made the register more confidently wrong.
#
# The CRITERION, which is what a maintainer actually needs:
#   a depth-1 enumeration that drops entries it cannot stat, WITHOUT tallying
#   the drop — so the count it feeds is a FLOOR presented as a total.
# The membership is enumerated and pinned two-way by
# `scripts/tests/test_diagnose_disk_accounting.sh` ("UNTALLIED-DROP SITE
# LEDGER"), which fails when the set GROWS or SHRINKS and prints the sites.
# 🔴 READ THE TEST FOR THE LIST. Do not re-count them here; that is the mistake
# this paragraph exists to stop.
#
# Why they are recorded rather than fixed: bash's file tests cannot separate the
# three reasons `[ -d "$p" ]` says no — not a directory, stat refused, or an
# unmatched glob left the pattern itself — so a count needs a different
# enumeration, not a `+ 1`. What limits the damage at `split_by_device` is that
# `report_foreign_mounts`'s empty branch does not read as a clean result: it
# tells the reader outright that an empty list on a host with foreign mounts
# under /home is a BUG. The section-5 PVC loop has no such backstop.
#
# The `|| true` here covers route (a) ONLY — find's own rc 1 — of the two aborts
# `size_breakdown`'s comment describes. Route (b), `xargs` rc 123, happens one
# stage later and is guarded at each caller's `xargs`.
_depth1_nul() {
  local base="$1" errf="$2"; shift 2
  find "$base" -xdev -mindepth 1 -maxdepth 1 "$@" -printf '%D\t%p\0' 2>"$errf" || true
}

# How many diagnostic lines the stderr file $1 holds. ALWAYS a single integer
# and never empty — the callers compare it with `-gt`, and the `grep -c` /
# `|| echo 0` two-line-zero defect `report_denials` carries a comment about is
# the same hazard one function over.
#
# 🔴 IT USED TO BE CALLED `_unstattable_count`, and the name became a false
# description the moment a second kind of stderr went through it: du's. It
# counts LINES IN A FILE and knows nothing about why they are there. Which blind
# spot a count represents is decided by which file you hand it, and that is the
# reporting function's business, not this one's.
_errline_count() {
  local n
  n=$(grep -c . "$1" 2>/dev/null; true)
  case "$n" in ''|*[!0-9]*) echo 0 ;; *) echo "$n" ;; esac
}

# Print the blind spot the count in $1 represents, or nothing. $2 = the base
# being measured, $3 = find's stderr file. Loud on purpose: an entry that reached
# no list is the difference between a floor and a total.
_report_unstattable() {
  local n="$1" base="$2" errf="$3"
  [ "$n" -gt 0 ] || return 0
  printf '  !! UNSTATTABLE: %d depth-1 entries of %s reached NO list above.\n' "$n" "$base"
  printf '     Every figure for %s here is a FLOOR, not a total. First lines:\n' "$base"
  # `|| true`: this pipeline is the LAST command of the function, so its status
  # is the function's status, and the function is called at statement level in
  # all three sections — i.e. the same `set -e` exposure the sweep above the
  # executable region is about, one level of indirection down.
  head_n 5 < "$errf" | sed 's/^/       /' || true
}

# The same for du's OWN stderr. $1 = the base, $2 = du's stderr file.
#
# 🔴 A DIFFERENT BLIND SPOT FROM `_report_unstattable`, and this is the half
# that was still at `/dev/null`. An entry find CAN stat but du cannot fully READ
# is not erased — it is listed above WITH A NUMBER, and the number is short.
# MEASURED 2026-09-07 over `base/{open,locked/inner}`, each holding one 4 KiB
# file, with `inner` mode 000: `du -sh -x` printed `8.0K` for `locked` against a
# true `12K` — a 33% under-count presented as a total — exited 1 (so `xargs`
# exited 123), and with `2>/dev/null` on that stage nothing in the report said
# so. `xargs` itself adds no line of its own here, so this count is du's alone.
#
# 🔴 AND THE `2>/dev/null` GOT WORSE WHEN `_report_unstattable` LANDED, which is
# why a pre-existing discard is fixed in this round. Before it, silence meant
# nothing either way; now the ABSENCE of a `!! UNSTATTABLE` line reads as an
# affirmative "nothing was missed" — the same floor-presented-as-a-total
# reading, arrived at by trusting a report that never had the evidence.
#
# 🔴 EXACTLY WHICH SITES THIS COVERS — a DECLARED scope, in the sense
# `split_by_device` below uses the phrase. It fires only for a caller that gave
# du a stderr FILE — as written today, `size_breakdown`'s own capture, section
# 5's PVC listing and section 6c's /home listing. Every OTHER `du` in this file
# still sends its stderr to `/dev/null`, and this report says nothing whatever
# about them.
#
# Do NOT take a number from this comment. Round 4's finding was a sentence
# elsewhere that counted these sites and counted them wrong, so what is written
# here is the derivation instead of the answer:
#
#   grep -n '_report_unreadable' scripts/diagnose-disk-accounting.sh   # callers
#   grep -nE '(^|[^#[:alnum:]_])du ' scripts/diagnose-disk-accounting.sh \
#     | grep -v ':[[:space:]]*#'                                       # du sites
#
# For any du site that comes back and is not one of the callers, silence is not
# evidence of a complete figure. Read that site's own comment for what it says
# instead.
_report_unreadable() {
  local base="$1" errf="$2" n
  n=$(_errline_count "$errf")
  [ "$n" -gt 0 ] || return 0
  printf '  !! PARTIALLY READ: du could not read %d path(s) under %s.\n' "$n" "$base"
  printf '     The sizes above are FLOORS for those paths, not totals. First lines:\n'
  head_n 5 < "$errf" | sed 's/^/       /' || true
}

# Open a temp file for this run and leave its path in $REPLY. $1 is a kind tag.
#
# 🔴 THE TEMPLATE IS THE POINT. Three
# `errf=$(mktemp)` calls used to open ANONYMOUS `/tmp/tmp.XXXXXXXXXX` files —
# in /tmp, the directory this script exists to diagnose — and NONE of the three
# was in the EXIT trap: only its own function's last statement removed it. The
# realistic way this run ends is not an abort but SIGINT, because the run this
# script was written for took ~3 h over 78 million entries, and MEASURED
# 2026-09-07 on bash 5.3.15 an EXIT trap DOES run when the shell is killed by an
# untrapped SIGINT. So the trap removed $DENIED_LOG, which is named, and left up
# to three files that nothing could attribute to anything.
#
# It is NOT the only `mktemp` in the file — the four in the executable region
# below write their own identifying templates inline, because they are opened
# once, at top level, where a helper would only hide them. What every one of
# them shares is the naming convention and `_cleanup_temps`.
#
# 🔴 `/tmp` IS HARDCODED, NOT `${TMPDIR:-/tmp}`, AND THAT IS THE DELIBERATE HALF
# OF A BEHAVIOUR CHANGE. The bare `mktemp` this replaced HONOURED `$TMPDIR`; the
# template does not. That is the safer direction for this file specifically:
# `$DENIED_LOG` already hardcodes /tmp, and this script's whole premise is that a
# local unprivileged process must not influence a root run — an inherited
# `TMPDIR=/anything` deciding where a root run writes its scan stderr is the
# `LSOF_BIN` lesson (see the seam) wearing a different name. The cost is real and
# is accepted: a non-root CALLER — i.e. the test suite — now writes into the
# system /tmp rather than into its own sandbox, and when a deliberately broken
# copy of this script dies between the `mktemp` and the `rm` (which is what the
# mutation battery does, once per mutant) the files stay there. They are mode
# 0600 and NAMED — the point of this whole change, because the name is how they
# get noticed and swept at all.
#
# 🔴 THEY ARE NOT ALL EMPTY, which is what this said for a round. MEASURED
# 2026-09-08 on this host: leftovers of both kinds were present and some held a
# fixture's captured stderr, e.g. `du: cannot read directory
# '/tmp/tmp.XXXXXXXXXX/du-errors/locked': Permission denied`. Fixture noise
# rather than host data, and 0600 keeps it out of another user's reach — but
# "empty" was simply the wrong word. No count is written down here, because the
# number is whatever the last interrupted sweep happened to leave;
# `ls -l /tmp/disk-accounting-*` is the answer.
#
# 🔴 AND THE HALF THAT WAS NEVER RECORDED: hardcoding /tmp also removes `TMPDIR`
# as an ESCAPE ROUTE, on exactly the failure this script is pointed at.
# `$DENIED_LOG` and `$DU_ERR` are opened at the top of the executable region and
# are deliberately unguarded (see the exception list above section 1), so a root
# filesystem out of inodes — the condition being diagnosed — ends the run on
# those two lines with a blank report. The exception list accepts the blank
# report as the honest outcome; what it does not say, and this does, is that the
# bare `mktemp` this replaced would have honoured an inherited `$TMPDIR` and the
# template cannot. Pointing this run's scratch at another filesystem now means
# editing this file.
_scan_mktemp() { REPLY=$(mktemp "/tmp/disk-accounting-$1.XXXXXX") || { REPLY=; return 1; }; }

# Remove every temp file the run currently holds open. It reads the variables at
# CALL time, so ONE `trap` installed before the first `mktemp` covers every
# later one.
#
# 🔴 THAT IS THE WHOLE FIX. What it replaces was a trap that had to be WIDENED
# BY HAND once section 6c opened two more files — and the three the breakdowns
# open were simply never added to either version of it. A trap naming a fixed
# list is an enumeration done by eye, which is the failure this file has already
# recorded twice for `set -e` sites. MEASURED: `rm -f ""` is silent and rc 0, so
# a slot that is unset costs nothing and needs no test around it.
_cleanup_temps() {
  rm -f "${DENIED_LOG:-}" "${DU_ERR:-}" "${ONROOT_LIST:-}" "${FOREIGN_LIST:-}" \
        "${SCAN_ERRF:-}" "${SCAN_DUERR:-}"
}

# Top-level entries of $1 that are ON $1's OWN FILESYSTEM, largest first.
#
# 🔴 NOT `du -sh -x "$1"/*`. MEASURED 2026-09-02: /tmp held 171,886 top-level
# entries = ~4.32 MiB of argv against an ARG_MAX of 2,097,152, so the glob dies
# E2BIG. `2>/dev/null` swallows the message and `set -euo pipefail` then kills
# the WHOLE SCRIPT mid-section — sections 6d-inodes, 7 and 8 never run, and the
# report ends with no error. That is the "truncated scan reported as a total"
# failure this very script exists to prevent.
#
# 🔴 `-xdev` DOES LIST FOREIGN MOUNTPOINTS AT DEPTH 1 — it only stops find
# DESCENDING past them. So a mount under /tmp arrives here as a starting point
# for `du -sh -x`, which is the one case `du -x` cannot handle (see
# `split_by_device` below: started ON a foreign mount, du walks all of it), and
# its whole size lands in a figure an operator reads as root-fs /tmp usage.
# Defect 7 was fixed for /home in section 6c and left standing here. Same fix:
# compare each candidate's device against the base's and drop the foreign ones.
# `-x` is KEPT as well — belt and braces for anything mounted BELOW depth 1.
#
# 🔴 BOTH STAGES ARE `|| true`, AND THEY ARE TWO DIFFERENT ABORTS.
#   (a) `find` exits 1 when an entry disappears between readdir and stat. On the
#       host this was written for /tmp holds ~270,000 churning entries, and
#       section 4's own counter calls those ENOENTs "benign, transient".
#   (b) `xargs` exits 123 when ANY `du` it ran exited 1 — which is what happens
#       when the entry vanishes a moment later, or is simply unreadable.
# Either one is eaten by `2>/dev/null`, promoted by `pipefail` and fatal under
# `set -e`: sections 6d-inodes, 7 and 8 never print and the report ends with no
# error. MEASURED 2026-09-07: (a) rc 1, (b) rc 123, each reproducible on its own.
# That is the same truncated-scan failure as E2BIG and SIGPIPE, reached by a
# third and a fourth route.
#
# 🔴 THE GUARDS ARE PER-STAGE, AND THE CAPTURE CARRIES NONE. An earlier draft of
# this round wrapped the whole `out=$(…)` in `|| true` instead. It works — and it
# destroys the only thing that could tell you it works: with an outer guard,
# DELETING any inner one changes nothing observable, and MEASURED, both `|| true`
# mutants in the battery went WRONG-KILLER. A guard whose removal is invisible is
# not a guard.
#
# 🔴 AND `sort` IS DELIBERATELY THE ONE STAGE LEFT UNGUARDED — the mirror image
# of the same trap. `_on_device` gained a `|| true` here (the audit's note that
# it was the one stage without one), but putting one on `sort` MEASURED as
# breaking BOTH `sigpipe-head-closes-the-pipe` rows: the whole point of `head_n`
# is that `sort` never takes SIGPIPE, so masking `sort`'s status is exactly what
# makes a reinstated `head -n` invisible. What stays exposed is a `sort` that
# fails for its OWN reasons — no space for its temp files, say — and that is the
# same exposure the statement-level pipeline had before this round, not a new
# one. It is listed with the other deliberate exceptions above section 1.
#
# 🔴 THIS COMMENT USED TO END: "What is tolerated is per-ENTRY failure; a total
# failure is still visible, as an empty section." BOTH HALVES WERE FALSE, and
# the round that wrote them is the round that falsified them. Per-entry failure
# was not tolerated but SILENTLY ERASED — `-printf '%D\t%p\0'` emits no record
# for an entry it cannot stat (see `_depth1_nul`) — and an empty section was not
# "visible" but indistinguishable from a clean directory, because nothing said
# which of the two it was. Both are fixed below: the stderr is counted and
# reported, and the empty list says in words that it is empty.
#
# 🔴 `dev=$(…) || dev=`, NOT a bare assignment. A command substitution in an
# assignment is a CHECKED command under `set -e`, so a failing `stat` killed the
# whole run ON THIS LINE and the refusal below never printed — the same
# unreachable-message defect this file records for `OUT=$(lsof …)`, reintroduced
# by the round that added the refusal. MEASURED 2026-09-07: under the script's
# own `set -euo pipefail`, `size_breakdown /tmp/<absent>` exited 1 having
# printed nothing at all. The suite could not see it: a suite that sources this
# file must turn `set -e` back OFF to run, so it took the refusal branch either
# way. The probe in section 4c of the suite runs `set -e` for real.
#
# 🔴 TWO STDERR SINKS, NOT ONE, AND THEY ARE NOT INTERCHANGEABLE. find's holds
# the entries that reached NO list; du's holds the paths that ARE listed but
# short. Merging them into one file would make one count out of two different
# claims and print whichever message happened to be wired to it — read
# `_report_unreadable` for what the du half measured.
size_breakdown() {
  local base="$1" dev blind out
  dev=$(_dev_of "$base") || dev=
  if ! _dev_is_valid "$dev"; then
    echo "COULD NOT MEASURE: no device id for $base — NOT an empty directory (size breakdown)"
    return 0
  fi
  _scan_mktemp size-find-stderr || { echo "COULD NOT MEASURE: no temp file for $base's size breakdown"; return 0; }
  SCAN_ERRF=$REPLY
  _scan_mktemp size-du-stderr || {
    rm -f "$SCAN_ERRF"; SCAN_ERRF=
    echo "COULD NOT MEASURE: no temp file for $base's size breakdown"; return 0; }
  SCAN_DUERR=$REPLY
  out=$(_depth1_nul "$base" "$SCAN_ERRF" \
    | { _on_device "$dev" || true; } \
    | { xargs -0 -r du -sh -x 2>>"$SCAN_DUERR" || true; } \
    | sort -rh | head_n 15)
  blind=$(_errline_count "$SCAN_ERRF") || blind=0
  if [ -n "$out" ]; then
    printf '%s\n' "$out"
  else
    echo "  none — no depth-1 entry of $base is on $base's own filesystem (NOT zero bytes)"
  fi
  _report_unstattable "$blind" "$base" "$SCAN_ERRF"
  _report_unreadable "$base" "$SCAN_DUERR"
  rm -f "$SCAN_ERRF" "$SCAN_DUERR"; SCAN_ERRF=; SCAN_DUERR=
}

# Inode count per top-level DIRECTORY of $1 that is on $1's OWN filesystem,
# largest first.
#
# 🔴 NOT `xargs -I{} sh -c '… "{}" …'`. That substitutes the directory NAME into
# a shell string, and /tmp is mode 1777, and this script demands sudo — so any
# local process could plant a directory whose name is a command and get it run
# AS ROOT. VERIFIED 2026-09-02: a dir named `evil";echo PWNED-AS-$(id -un) >&2;"x`
# made the old pipeline print PWNED-AS-zach.
#
# `xargs -0 -r -n1 sh -c '…' _` is the SAFE xargs form and `-I{}` is the unsafe
# one; the difference is not cosmetic. `-n1 … _` appends the name to sh's argv,
# where it lands in "$1" and is never parsed as text. `-I{}` splices it into the
# script STRING before sh ever sees it. This used to be `find … -exec sh -c '…'
# _ {} \;`, which is equally safe; it changed only because the device filter has
# to sit between the enumeration and the per-directory walk.
#
# 🔴 NO `2>/dev/null` ON THE xargs STAGE, deliberately. The inner `find` already
# has its own; a blanket one here would swallow whatever the per-directory shell
# writes to stderr — and the historical defect's own proof of execution was a
# planted name printing `PWNED-AS-root` to STDERR. MEASURED: with `2>/dev/null`
# added back, the injection mutant in the battery is scored WRONG-KILLER,
# because the guard that watches for the expansion can no longer see it. A
# `2>/dev/null` that hides the evidence for the guard above it is not tidiness.
#
# The device filter and the two `|| true`s are the same two defects as
# `size_breakdown` above; read its comment.
#
# ONE stderr sink here, not two: this breakdown runs no `du` — the per-directory
# walk is a `find … -printf .` whose own `2>/dev/null` is inside the inner shell
# — so there is no second stream to keep and `_report_unreadable` has nothing to
# say. Stated, because an inconsistency between two adjacent functions otherwise
# reads as an omission.
inode_breakdown() {
  local base="$1" dev blind out
  dev=$(_dev_of "$base") || dev=
  if ! _dev_is_valid "$dev"; then
    echo "COULD NOT MEASURE: no device id for $base — NOT an empty directory (inode breakdown)"
    return 0
  fi
  _scan_mktemp inode-find-stderr || { echo "COULD NOT MEASURE: no temp file for $base's inode breakdown"; return 0; }
  SCAN_ERRF=$REPLY
  out=$(_depth1_nul "$base" "$SCAN_ERRF" -type d \
    | { _on_device "$dev" || true; } \
    | { xargs -0 -r -n1 sh -c 'printf "%12d  %s\n" "$(find "$1" -xdev -printf . 2>/dev/null | wc -c)" "$1"' _ || true; } \
    | sort -rn | head_n 15)
  blind=$(_errline_count "$SCAN_ERRF") || blind=0
  if [ -n "$out" ]; then
    printf '%s\n' "$out"
  else
    echo "  none — no depth-1 DIRECTORY of $base is on $base's own filesystem (NOT zero inodes)"
  fi
  _report_unstattable "$blind" "$base" "$SCAN_ERRF"
  rm -f "$SCAN_ERRF"; SCAN_ERRF=
}

# The depth-1 entries of $1 that the two breakdowns above SKIPPED, because they
# are on another filesystem.
#
# 🔴 THIS EXISTS BECAUSE THE DEVICE FILTER CREATED A BLIND SPOT. Excluding a
# foreign mount is right — its size is not root-fs usage — but excluding it
# SILENTLY is the exact failure this whole file catalogues: a floor presented as
# a total. Section 6c already prints the same listing for /home, and its "none
# found" branch is deliberately loud for the same reason. A section that drops
# what it could not count without saying so is worse than one that never tried.
#
# 🔴 ONE ROW PER NUL RECORD — never `tr '\0' '\n' | head_n 15`, which is what
# this function was written with in round 1. It reintroduced, in the same
# commit that fixed it
# for `split_by_device`, the defect `report_foreign_mounts` reads its list with
# `read -r -d ''` to avoid: /tmp is mode 1777, so any local process can create a
# directory whose name contains a newline, `tr` turns it into two report rows —
# the second of which reads as a real path — and `head_n`, which counts LINES,
# lets that one name eat two of the fifteen slots.
#
# 🔴 AND THE "none" BRANCH MUST NOT OUTRANK ITS OWN BLIND SPOT. An entry find
# could not stat is absent from this listing too (see `_depth1_nul`), so "none"
# is only honest when the enumeration saw everything. When it did not, say so.
foreign_entries() {
  local base="$1" dev blind p n=0
  dev=$(_dev_of "$base") || dev=
  if ! _dev_is_valid "$dev"; then
    echo "  COULD NOT MEASURE: no device id for $base — NOT an absence of foreign mounts"
    return 0
  fi
  _scan_mktemp foreign-find-stderr || { echo "  COULD NOT MEASURE: no temp file for $base's foreign-entry listing"; return 0; }
  SCAN_ERRF=$REPLY
  while IFS= read -r -d '' p; do
    n=$((n + 1))
    [ "$n" -gt 15 ] || printf '  %s\n' "$p"
  done < <(_depth1_nul "$base" "$SCAN_ERRF" | _not_on_device "$dev")
  blind=$(_errline_count "$SCAN_ERRF") || blind=0
  if [ "$n" -gt 15 ]; then
    printf '  ... and %d more\n' "$((n - 15))"
  elif [ "$n" -eq 0 ]; then
    if [ "$blind" -gt 0 ]; then
      echo "  none VISIBLE — and the line below says why that is NOT 'no foreign mounts'"
    else
      echo "  none — every depth-1 entry is on the same filesystem as $base"
    fi
  fi
  _report_unstattable "$blind" "$base" "$SCAN_ERRF"
  rm -f "$SCAN_ERRF"; SCAN_ERRF=
}

# Partition the directories under $1 by filesystem: on-root candidates into $2,
# foreign ones into $3. BOTH are NUL-separated — $2 because it is fed to
# `xargs -0`, $3 because it used to be one-per-line and a directory named with
# an embedded newline then split into two garbled rows in the report. Wrong
# report line rather than wrong execution, but /home is user-writable and this
# script is run under sudo, so "the operator reads a line I chose" is not a
# property worth leaving to chance. `report_foreign_mounts` reads it with
# `read -r -d ''`. $4 is the root device id.
#
# 🔴 `du -x` only stops du CROSSING AWAY from its starting point. When the
# starting point IS a foreign mount, du walks the whole thing: the 2026-09-01
# run reported 12T for /home/zach/hdd-20tb (/dev/sda1, xfs, 18.2T) and 1.2T for
# old-nix-hdd (/dev/sdc1) under a root filesystem that is 1.8T in total —
# figures a reader can take for root-fs usage. Walking them is also what made
# that run take ~3 hours. So compare each candidate's device against / and skip
# the foreign ones, then list them separately.
#
# 🔴 The loop must NOT be the head of a pipeline, and the accumulator must not be
# a VARIABLE. It was both, so `FOREIGN=` was assigned in a SUBSHELL and was empty
# by the time the listing read it — on a host with five foreign filesystems under
# /home the report affirmatively printed "none". The exclusion half worked and the
# listing half could never fire, so the code was narrower than its own comment.
# shellcheck SC2030/SC2031 flags exactly this; the repo has no shellcheck gate,
# so nothing caught it.
#
# 🔴 KNOWN, DECLARED BLIND SPOT — a site of the shape `_depth1_nul`'s comment
# describes, and one this round did NOT fix. (NO ORDINAL: the membership of that
# set is machine-checked in the test suite's UNTALLIED-DROP SITE LEDGER, and a
# number written here is exactly the stale claim that ledger replaced. An earlier
# version said "the fourth site" — 260 lines below the paragraph forbidding it.) `[ -d "$p" ]` says no
# for three different reasons — not a directory, `stat` refused, or the glob
# matched nothing and left its own pattern — and bash's file tests cannot tell
# them apart, so `continue` silently drops a /home directory root cannot stat (a
# gvfs or sshfs mount under /home/<user>/… is exactly that) with no count. It is
# NOT fixed here because a count needs a different enumeration, and inventing one
# inside an audit-fix round is how every previous round of this ladder produced
# the next round's finding. What is claimed instead is only what holds: the
# empty branch of `report_foreign_mounts` below does not present itself as a
# clean host.
split_by_device() {
  local base="$1" onroot="$2" foreign="$3" root_dev="$4" p d
  : > "$onroot"
  : > "$foreign"
  for p in "$base"/*/* "$base"/*/.*; do
    case "${p##*/}" in .|..) continue ;; esac
    [ -d "$p" ] || continue
    d=$(_dev_of "$p") || continue
    [ -n "$d" ] || continue
    if [ "$d" = "$root_dev" ]; then printf '%s\0' "$p" >> "$onroot"
    else printf '%s\0' "$p" >> "$foreign"; fi
  done
}

# List the foreign mounts recorded by split_by_device. The "none" branch has to
# stay loud: an empty list here was, historically, the SUBSHELL BUG, not a clean
# host.
report_foreign_mounts() {
  local p
  if [ -s "$1" ]; then
    while IFS= read -r -d '' p; do
      printf '  %-40s %s\n' "$p" "$(findmnt -n -o SOURCE,FSTYPE,SIZE,USED --target "$p" 2>/dev/null | head_n 1)"
    done < "$1"
  else
    echo "  none found — on a host with foreign mounts under /home this is a BUG, not a clean result"
  fi
}

# Section 4's whole body: classify the stderr `find` was told to keep.
#
# 🔴 `grep -c` prints 0 AND exits 1 when there are no matches, so `|| echo 0`
# used to emit a two-line "0\n0" and every later [ -gt ] on it died with
# "integer expected" — i.e. the denial guard failed exactly when it had
# something to report. Count with a form that cannot fail, and verify it is a
# single integer.
#
# 🔴 A scan that reports a number with no denial count is a FLOOR presented as a
# total. That is why section 2's find sends stderr to a LOG and not to
# /dev/null, and why this section is a positive control rather than a footnote.
report_denials() {
  local log="$1" denied other total unclassified
  denied=$(grep -c 'Permission denied' "$log" 2>/dev/null; true)
  other=$(grep -c 'No such file or directory' "$log" 2>/dev/null; true)
  total=$(wc -l < "$log" 2>/dev/null || echo 0)
  case "$denied$other" in
    *[!0-9]*|'')
      echo "!! denial counter is broken — treat every count above as UNVERIFIED"
      denied=0; other=0 ;;
  esac
  unclassified=$((total - denied - other))
  echo "directories find could not read      : $denied"
  echo "vanished mid-scan (benign, transient): $other"
  echo "OTHER, unclassified                  : $unclassified"
  if [ "$denied" -gt 0 ]; then
    echo "!! Running as root and STILL denied — the counts above are FLOORS, not totals."
    grep 'Permission denied' "$log" | head_n 20 || true
  fi
  if [ "$unclassified" -gt 0 ]; then
    echo "-- unclassified errors (read these; they are not known-benign) --"
    grep -v 'Permission denied' "$log" | grep -v 'No such file or directory' | head_n 20 || true
  fi
}

# Enumerate the top-level entries of a filesystem root that section 2 must
# account for, NUL-SEPARATED — not one per line.
#
# 🔴 CONSUME IT WITH `read -r -d ''`, NEVER a plain `read`. `find -print0` emits
# NO newlines at all, so a `while IFS= read -r d` loop receives all 17 top-level
# entries as ONE record: one garbage path gets walked, every other entry silently
# vanishes, and section 3's residual becomes the whole filesystem. This comment
# said "one per line" for a day while the body already emitted NUL — the sentence
# that describes an interface is part of the interface.
#
# 🔴 THIS EXISTS BECAUSE SECTION 2 SKIPPED TOP-LEVEL *FILES*. The loop read
# `[ -d "$d" ] || continue`, so every top-level entry that was not a directory
# was dropped before `find` ever saw it. MEASURED 2026-09-09 on the workbench:
# `/swapfile` is a top-level REGULAR FILE of 100663328 512B blocks = 48.00 GiB,
# on the same device as `/` (`/dev/nvme0n1p2`), and it got no row in section 2 at
# all.
#
# 🔴 BE PRECISE ABOUT WHICH NUMBER MOVED — an earlier draft of this comment said
# "section 3's residual was short by 48 GiB", and that is FALSE in a way that
# matters. Section 3 is `INODES_USED - TOTAL_INODES`: a count of INODES. There is
# no byte residual at all — the TOTAL row prints an empty byte column. So the
# omission cost section 2's BYTE COLUMN 48 GiB (the column section 3's own text
# calls "the real answer"), and moved section 3's residual by exactly ONE inode.
# Saying "the residual was short by 48 GiB" teaches an operator to read a
# four-digit inode residual as gigabytes, in the one script whose section 3
# exists to stop exactly that misreading.
#
# The predecessor `diagnose-nix-disk.sh` never reconciled it either — it walked a
# HARDCODED directory list, so `/swapfile` was in none of its inode or byte
# totals. It did print `ls -lh /swapfile` in its section 9, so the number was on
# screen; it was never counted.
#
# 🔴 NO TYPE FILTER, DELIBERATELY — every entry is emitted except the skip list.
# An earlier draft excluded symlinks and non-regular files and gave two reasons.
# BOTH WERE MEASURED FALSE (2026-09-09, findutils on this host), so they are
# recorded here as retracted rather than replaced, because reaching for a third
# justification is what produced the first two:
#   - "-d/-f follow symlinks, so a symlink to a directory would be walked twice."
#     `find <symlink>` WITHOUT `-L` does not descend: 1 line vs 21953 with `-L`.
#     The double-walk cannot happen, so the exclusion bought nothing.
#   - "fifos/sockets/device nodes hold an inode but no data blocks, so counting
#     them as storage over-counts." `-printf %b` reports 0 for a fifo AND for a
#     symlink, so including them adds ZERO bytes — while each one is a real used
#     inode. Section 3 reconciles INODES, so excluding them made its residual
#     LARGER, which is the direction that comment claimed to be avoiding.
# Emitting everything is therefore both simpler and strictly more accurate.
#
# `find -mindepth 1 -maxdepth 1` rather than a glob, for three reasons a glob
# cannot give at once: it matches DOTFILES (`"$root"/*` does not, so a top-level
# `.journal` and its whole subtree were invisible — the same defect this function
# exists to fix, one flag away); it emits NUL-separated names, so an entry
# containing a newline cannot split into phantom paths that `find` then reports
# as "vanished mid-scan (benign)"; and an EMPTY DIRECTORY yields nothing rather
# than a literal unmatched glob. (An empty *argument* is a different case: `find ""`
# ERRORS, which is what the `[ -n "$root" ] || root=/` line below prevents.)
toplevel_accountable_entries() {
  local root="${1:-/}"
  # "/" -> "" would make find's operand empty; "//" -> "/" normalises the
  # doubled-slash spelling, which would otherwise print as "//etc".
  root="${root%/}"
  [ -n "$root" ] || root=/
  find "$root" -mindepth 1 -maxdepth 1 \
    \( -name proc -o -name sys -o -name dev -o -name run -o -name mnt \) -prune \
    -o -print0
}

# --- end of the sourceable seam ---------------------------------------------
# See the header for why this is `(return …)` and not `${BASH_SOURCE[0]}`, and
# for what was measured. The `LSOF_BIN` override is deliberately INSIDE this
# branch: only a sourced run may choose the binary.
if (return 0 2>/dev/null); then
  LSOF_BIN=${LSOF_BIN:-lsof}
  return 0
fi
LSOF_BIN=lsof

if [ "$(id -u)" -ne 0 ]; then
  echo "FATAL: must run as root — an unprivileged run silently skips the trees" >&2
  echo "       this script exists to measure. Re-run with sudo." >&2
  exit 2
fi

# 🔴 THE `set -e` SWEEP, AND WHAT IT DELIBERATELY LEAVES OPEN. Twice now an
# enumeration of "commands whose failure kills the report" was done BY EYE and
# missed a site (round 1 covered the pipelines and missed section 5's bare
# `du -sh`). It is now done mechanically — but read the next paragraph before
# reading the exception list as an inventory.
#
# 🔴 WHAT THE SWEEP CAN SEE, EXACTLY. It joins backslash continuations, strips
# whole-line comments, keeps every line whose FIRST WORD (after an optional
# `{ `) is one of find/du/ls/dumpe2fs/findmnt/lsof/stat/xargs/grep/sed/sort/
# uniq/wc/tr/awk/mktemp/seq/dd/id, and drops any line containing `||`. Two
# consequences, and the second is why the list below grew this round:
#   (i) A line headed by anything else — `for`, `done`, `printf`, `echo`, a
#       `VAR=$(…)` assignment — is INVISIBLE to it, whatever it pipes into.
#   (ii) ONE `||` anywhere on the joined line silences the WHOLE line, so a
#       pipeline whose early stages are guarded is dropped even when its LAST
#       stage is not.
# So "everything the sweep reports is guarded" is a true sentence about a narrow
# scan, not a statement about the file. The exceptions below are what a reading
# BY HAND found on top of it; §7b of the suite pins the sweep's own result as a
# ledger, and pins the trailing-`sort` inventory separately for the same reason.
#
# Left open on purpose, each checked by hand:
#   * `X=$((…))` — MEASURED: an arithmetic assignment is rc 0 even when the
#     expression evaluates to 0, so it is not in the class at all.
#   * `read … < <(find … | awk …)` in section 2 — MEASURED: a process
#     substitution's status is NOT checked by `set -e`, which is why denials in
#     section 2 do not kill the run.
#   * EVERY unguarded trailing `sort`, of which there are FIVE, not the two a
#     previous wording implied. `sort` is deliberately never guarded: the whole
#     point of `head_n` is that `sort` never takes SIGPIPE, so masking its status
#     is exactly what would make a reinstated `head -n` invisible (MEASURED — it
#     broke both `sigpipe-head-closes-the-pipe` rows). Two sit inside
#     `out=$(… | sort … | head_n …)` captures in the breakdowns; THREE more are
#     statement-level pipelines that reason (i) or (ii) above hides from the
#     sweep — section 5's PVC listing and its inodes-per-PVC loop, and section
#     6c's /home listing. What is exposed in all five is a `sort` that fails for
#     its OWN reasons (no space for its temp files), which is the exposure these
#     pipelines have always had rather than anything this round introduced.
#   * `DENIED_LOG=$(mktemp …)`, `DU_ERR=$(mktemp …)` and the five `stat -f`
#     reads in section 1 — these abort BEFORE any figure is printed, so they
#     cannot leave a floor looking like a total. They end the run with a blank
#     report, which is wrong-looking rather than reassuring. That is the
#     criterion, and it is the only reason they are not guarded.
DEV=${DEV:-/dev/nvme0n1p2}
# 🔴 THE TRAP GOES UP FIRST, BEFORE ANY mktemp, and it names a FUNCTION rather
# than a list. `_cleanup_temps` reads the variables when it RUNS, so this one
# line covers every temp file opened later — including the ones the breakdowns
# open per call, which no hand-written list ever covered, and the pair at
# section 6c, which used to need the trap re-installed by hand.
trap _cleanup_temps EXIT
DENIED_LOG=$(mktemp /tmp/disk-accounting-denied.XXXXXX)
DU_ERR=$(mktemp /tmp/disk-accounting-root-du-stderr.XXXXXX)

echo "============================================"
echo "  Root-fs accounting — $DEV"
echo "  $(date -Is)"
echo "============================================"

echo
echo "=== 1. Ground truth from the superblock ==="
BS=$(stat -f -c %S /)
BLOCKS_TOTAL=$(stat -f -c %b /)
BLOCKS_FREE=$(stat -f -c %f /)
INODES_TOTAL=$(stat -f -c %c /)
INODES_FREE=$(stat -f -c %d /)
BLOCKS_USED=$((BLOCKS_TOTAL - BLOCKS_FREE))
INODES_USED=$((INODES_TOTAL - INODES_FREE))
printf 'block size      : %d\n' "$BS"
printf 'blocks used     : %d  (%.1f GiB)\n' "$BLOCKS_USED" "$(echo "$BLOCKS_USED $BS" | awk '{print $1*$2/1073741824}')"
printf 'inodes used     : %d\n' "$INODES_USED"
echo
echo "--- static ext4 metadata (this is the ONLY 'overhead' that is not files) ---"
# 🔴 `|| echo`, NOT a bare pipeline. `$DEV` is a GUESS (`/dev/nvme0n1p2` unless
# the caller overrides it) and `grep` exits 1 when nothing matches, so on a host
# where the guess is wrong — or where dumpe2fs is not installed — this pipeline
# returns 1, `set -e` kills the run HERE, and the operator is left holding the
# "blocks used / inodes used" figures printed three lines above with sections
# 2-8 missing and no error. MEASURED 2026-09-07 in an isolated probe of these
# two lines with `DEV=/dev/nope-xyz`: the pipeline returned 1 under
# `set -euo pipefail` and the next statement never ran. Same class as the
# `du -sh` in section 5 below.
dumpe2fs -h "$DEV" 2>/dev/null | grep -iE 'Inode size|Inode count|Block count|Reserved block count|Journal size|Filesystem state|Last checked' \
  || echo "COULD NOT MEASURE: dumpe2fs read no ext4 superblock fields from $DEV — set DEV=<device> if that is the wrong partition"
INODE_SIZE=$(dumpe2fs -h "$DEV" 2>/dev/null | awk -F: '/^Inode size/{gsub(/ /,"",$2);print $2}') || INODE_SIZE=
if [ -n "${INODE_SIZE:-}" ]; then
  echo "$INODES_TOTAL $INODE_SIZE" | awk '{printf "inode TABLES    : %.1f GiB (preallocated, counted as used blocks)\n", $1*$2/1073741824}'
fi
echo "$BLOCKS_TOTAL" | awk '{printf "block bitmaps   : ~%.2f GiB\n", $1/8/1073741824}'
echo "NOTE: static metadata for this fs is tens of GiB, NOT hundreds. A multi-hundred-GiB"
echo "      shortfall is unmeasured FILES, never metadata."

echo
echo "=== 2. Inodes and allocated bytes per top-level entry ==="
echo "  Top-level FILES are listed, not just directories — /proc /sys /dev /run /mnt"
echo "  are still pruned, so this is not literally every entry. /swapfile is a regular"
echo "  file allocating 48.00 GiB on this fs (measured 2026-09-09) and used to be"
echo "  dropped by a '[ -d ] || continue' guard, so it had NO ROW AT ALL here and its"
echo "  48 GiB was absent from the byte column below — the column section 3 calls"
echo "  'the real answer'. Section 3's residual counts INODES, so that same omission"
echo "  moved the residual by exactly 1, not by 48 GiB. See"
echo "  toplevel_accountable_entries()."
echo "  -xdev: stays on the root fs. /mnt/rootcheck is EXCLUDED — it is a bind mount"
echo "  of / and would double-count the entire filesystem."
echo "  HARDLINKS ARE DEDUPED. find visits every LINK, so a naive '%b' sum counts a"
echo "  hardlinked file once per link — /nix/store is ~1.46M files hardlinked into"
echo "  .links, and the 2026-09-01 run over-counted by 32.3M entries / ~664 GiB that"
echo "  way, producing a NEGATIVE residual. Each inode is now counted once."
TOTAL_INODES=0
TOTAL_DEDUPED=0
printf '%14s %12s %10s  %s\n' "INODES" "GiB(alloc)" "dup-links" "PATH"
while IFS= read -r -d '' d; do
  # %y=type %n=link count %i=inode %b=512B blocks. Only non-directories with more
  # than one link go in the seen[] hash, so it holds multiply-linked FILES only —
  # directories always have nlink>1 and appear exactly once in find's output.
  read -r n blocks dups < <(
    find "$d" -xdev -printf '%y %n %i %b\n' 2>>"$DENIED_LOG" \
      | awk '{
          if ($1 != "d" && $2 > 1) { if (seen[$3]++) { dup++; next } }
          n++; b += $4
        }
        END {print n+0, b+0, dup+0}'
  )
  gib=$(echo "$blocks" | awk '{printf "%.1f", $1*512/1073741824}') || gib=
  printf '%14d %12s %10d  %s\n' "$n" "$gib" "$dups" "$d"
  TOTAL_INODES=$((TOTAL_INODES + n))
  TOTAL_DEDUPED=$((TOTAL_DEDUPED + dups))
# 🔴 `done < <(...)`, NOT `... | while`. A pipeline runs the loop body in a
# SUBSHELL, so TOTAL_INODES/TOTAL_DEDUPED would be discarded at `done` and
# section 3's residual would be computed from zero — a wrong answer that prints
# cleanly. Process substitution keeps the loop in this shell.
done < <(toplevel_accountable_entries /)
printf '%14d %12s %10d  TOTAL\n' "$TOTAL_INODES" "" "$TOTAL_DEDUPED"
echo "  dup-links = extra directory entries pointing at an already-counted inode."
echo "  A zero in that column for a tree you KNOW is hardlinked (/nix) means the"
echo "  dedup is not running — treat it as instrument failure, not a clean result."
echo
echo "  🔴 STILL DOUBLE-COUNTED, and dedup cannot fix it: BIND MOUNTS of the same"
echo "  device. /var/lib/kubelet bind-mounts the k3s local-path PVC directories, so"
echo "  that data is counted under BOTH /var/lib/kubelet and"
echo "  /var/lib/rancher/k3s/storage. -xdev does not help — same device. Section 5"
echo "  and section 6 print the two figures separately so you can subtract."

echo
echo "=== 3. Residual — the number the whole question turns on ==="
printf 'inodes counted  : %d\n' "$TOTAL_INODES"
printf 'inodes used (fs): %d\n' "$INODES_USED"
printf 'RESIDUAL        : %d\n' "$((INODES_USED - TOTAL_INODES))"
echo "A residual near zero means the tree is fully accounted for and the byte column"
echo "above is the real answer. A large residual means something is STILL unmeasured —"
echo "read the denial report below before drawing any conclusion from it."
echo "A NEGATIVE residual means over-counting, never hidden data: hardlinks not"
echo "deduped (see the dup-links column) or bind-mounted data counted under two paths."
echo "A small positive residual is expected — the tree moves while this runs."

echo
echo "=== 4. Blind-spot report (positive control) ==="
report_denials "$DENIED_LOG"

echo
echo "=== 5. k3s local-path PVCs (unreadable without root; the prior '1.7GB' claim) ==="
if [ -d /var/lib/rancher/k3s/storage ]; then
  # Same NUL-safe enumeration as size_breakdown, for the same reason: a glob
  # expanded into `du` is an E2BIG waiting for the directory to grow, and this
  # one is bounded only by how many PVCs the node happens to hold. Same two
  # `|| true`s too — find exits 1 on a PVC directory that is unlinked mid-scan,
  # xargs exits 123 when a `du` under it does, and either kills the report.
  #
  # No device filter here, unlike section 6d: these are k3s local-path PVC
  # directories, which are by construction on the node's own filesystem. If that
  # ever stops being true this needs the same treatment.
  #
  # 🔴 du's stderr goes to $DU_ERR, not /dev/null, for the reason
  # `_report_unreadable` carries: a PVC directory du cannot fully read is listed
  # here with an UNDER-COUNTED size and no marker, and once the breakdowns
  # started reporting their blind spots the absence of a marker started reading
  # as "nothing was missed". Truncated per site so one site's count cannot be
  # attributed to another.
  #
  # 🔴 `|| echo`, NOT a bare `: >`. A redirection failure on a SPECIAL BUILTIN
  # is fatal under `set -e` — MEASURED 2026-09-08 on bash 5.3.15:
  # `set -euo pipefail; : > /absent/x` ends the shell, rc 1, nothing after it
  # runs; the same line with `|| echo` is caught and the run continues. This
  # shape is in NEITHER ledger §7b keeps: the sweep's regex reads a line's first
  # word and this line's is `:`, and it carries no `| sort`. It also cannot be a
  # bare `|| true` — a file that did not truncate still holds an EARLIER site's
  # paths, and `_report_unreadable` would then attribute them to this one, which
  # is the "one count standing for two claims" the two-sinks comment is about.
  : > "$DU_ERR" || echo "COULD NOT MEASURE: could not truncate du's stderr file — any PARTIALLY READ count below may include paths from an earlier section"
  { find /var/lib/rancher/k3s/storage -xdev -mindepth 1 -maxdepth 1 -print0 2>/dev/null || true; } \
    | { xargs -0 -r du -sh --exclude=/mnt 2>>"$DU_ERR" || true; } | sort -rh | head_n 30
  _report_unreadable /var/lib/rancher/k3s/storage "$DU_ERR"
  echo "--- total ---"
  # 🔴 `|| echo`, NOT a bare `du`. This site sat directly under the two guarded
  # pipelines above, under a comment that described the treatment it did not
  # have. `du` prints an UNDER-COUNTED total and exits 1 when a PVC directory is
  # unlinked between readdir and stat, or is simply unreadable — MEASURED
  # 2026-09-07 over a directory holding a mode-000 subdirectory: `12K` printed,
  # rc 1 — and with the message already at /dev/null `set -e` then killed the
  # run, so sections 6, 6b, 6c, 6d, 7 and 8 never printed. A floor presented as
  # a total, and then no error. The `|| echo` labels the floor as one.
  du -sh /var/lib/rancher/k3s/storage 2>/dev/null \
    || echo "COULD NOT MEASURE: du failed under /var/lib/rancher/k3s/storage — any total it printed is a FLOOR"
  echo "--- inodes per PVC (top 15) ---"
  for p in /var/lib/rancher/k3s/storage/*; do
    [ -d "$p" ] || continue
    printf '%12d  %s\n' "$(find "$p" -xdev -printf . 2>/dev/null | wc -c)" "$p"
  done | sort -rn | head_n 15
else
  echo "absent — NOT the same as zero"
fi

echo
echo "=== 6. Other root-only trees the unprivileged scan could not see ==="
for d in /root /var/lib/docker /var/lib/containerd /var/lib/kubelet /var/lib/private; do
  if [ -d "$d" ]; then
    # 🔴 du's STATUS IS READ HERE, AND THE MARKER IS WHAT IT BUYS. A `du` that
    # cannot fully read a tree prints a PARTIAL total on stdout and exits 1 —
    # a stale NFS/CSI mount under /var/lib/kubelet is the realistic case on a
    # k3s node. With the status thrown away and the message already at
    # /dev/null, this row rendered that floor EXACTLY like a complete figure,
    # and since `_report_unreadable` landed the absence of a marker reads as an
    # affirmative "nothing was missed" (see its comment). This site has no
    # stderr FILE to report a path count from, so what it can say is that the
    # number is a floor.
    # `if du_out=$(…)`, not a bare assignment: a command in an `if` condition
    # is not checked by `set -e`, so a failing du marks the row instead of
    # ending the report — the same checked-assignment reasoning as section 6c's
    # `ROOT_DEV` reading, reached by an `if` rather than an `||`. (Spelled
    # WITHOUT quoting that line verbatim: the mutation battery matches raw text,
    # and a comment reproducing a mutant's literal makes the mutation ambiguous
    # and scores it MUTATION DID NOT APPLY. That is how this comment was first
    # written, and the occurrence check caught it.)
    if du_out=$(du -sh -x "$d" 2>/dev/null); then du_mark=; else du_mark='  !! FLOOR — du could not read all of it'; fi
    printf '%10s %12d inodes  %s%s\n' \
      "$(printf '%s\n' "$du_out" | awk '{print $1}')" \
      "$(find "$d" -xdev -printf . 2>/dev/null | wc -c)" "$d" "$du_mark"
  else
    printf '%10s %12s          %s\n' absent - "$d"
  fi
done

echo
echo "=== 6b. Nix store breakdown (hardlink-aware) ==="
echo "  du counts each hardlink once per run, so .links is the unique-data figure."
du -sh /nix/store/.links 2>/dev/null || echo ".links absent"
printf 'files in .links : %d\n' "$(find /nix/store/.links -xdev -printf . 2>/dev/null | wc -c)"
printf 'store paths     : %d\n' "$(ls /nix/store/ 2>/dev/null | wc -l)"

echo
echo "=== 6c. /home breakdown — ROOT FILESYSTEM ONLY (top 15 by allocated size) ==="
# The device split and the foreign listing both live in `split_by_device` /
# `report_foreign_mounts` above; read their comments for the `du -x` and
# subshell-accumulator defects they exist to prevent.
# `|| ROOT_DEV=` then a digits check, for the reason `size_breakdown` carries a
# comment about: a bare `VAR=$(…)` is a CHECKED command under `set -e`, so a
# failing `stat` would kill the run here rather than reach a refusal. An EMPTY
# root device is worse than no section — `split_by_device` compares each
# candidate against it, so every directory under /home would come back "foreign"
# and 6c would print a confident list of foreign mounts that are nothing of the
# kind.
ROOT_DEV=$(_dev_of /) || ROOT_DEV=
if ! _dev_is_valid "$ROOT_DEV"; then
  echo "COULD NOT MEASURE: no device id for / — skipping 6c rather than calling every /home directory foreign"
else
# 🔴 NO TRAP WIDENING HERE ANY MORE. This used to read "Create BOTH before
# widening the trap: if the second mktemp failed while the trap still named only
# $DENIED_LOG, the first temp file leaked on that exit path" — a hazard that only
# existed because the trap named a fixed LIST. `_cleanup_temps` reads these two
# variables when it runs, so the single trap at the top of the executable region
# already covers them, in either order and however many of them got created.
ONROOT_LIST=$(mktemp /tmp/disk-accounting-home-onroot.XXXXXX) \
  && FOREIGN_LIST=$(mktemp /tmp/disk-accounting-home-foreign.XXXXXX) || exit 3
split_by_device /home "$ONROOT_LIST" "$FOREIGN_LIST" "$ROOT_DEV"
# `|| true` for the same reason as section 6d: xargs exits 123 when any `du` it
# ran exited 1, which is what a /home directory unlinked mid-scan produces, and
# `pipefail` + `set -e` would take the whole report with it. du's stderr is KEPT
# (see section 5 and `_report_unreadable`): a /home directory du could not fully
# read is listed here short, and silence is not evidence that none was.
# Guarded for the reason section 5's copy carries — and this is the site that
# makes it matter: here the truncation runs AFTER sections 1 through 6b have
# already printed, so an unguarded failure ends the report mid-way with figures
# above it and no message, which is precisely the criterion the exception list
# above section 1 uses to decide a site may stay unguarded.
: > "$DU_ERR" || echo "COULD NOT MEASURE: could not truncate du's stderr file — any PARTIALLY READ count below may include paths from an earlier section"
{ xargs -0 -r du -sh -x < "$ONROOT_LIST" 2>>"$DU_ERR" || true; } | sort -rh | head_n 15
_report_unreadable /home "$DU_ERR"
echo "--- NOT on the root filesystem, so NOT part of this accounting ---"
report_foreign_mounts "$FOREIGN_LIST"
fi

echo
echo "=== 6d. /tmp breakdown — MEASURED 2026-09-01 as the largest inode consumer ==="
echo "  78,501,285 entries / 469 GiB, 81% of this filesystem's inodes. /tmp is on"
echo "  the ROOT partition here, not tmpfs, so nothing clears it at boot."
printf 'top-level entries : %d\n' "$(ls -A /tmp 2>/dev/null | wc -l)"
echo "--- top 15 by allocated size ---"
size_breakdown /tmp
echo "--- top 15 by inode count ---"
inode_breakdown /tmp
# 🔴 "NOT on /TMP'S OWN filesystem", not "not on the root filesystem". The three
# helpers above compare each entry against `stat -c '%d' /tmp`, NOT against
# $ROOT_DEV, so on a host where /tmp is its own mount this heading named the
# wrong filesystem. (On the host this targets they are the same device — 6d's
# own text says /tmp is on the root partition — which is exactly why a wrong
# heading here could sit unnoticed.)
echo "--- NOT on /tmp's own filesystem, so EXCLUDED from the two lists above ---"
foreign_entries /tmp
echo "--- entry-name families (what is generating them) ---"
# `|| true` for the same reason as section 5's `du`: `ls` exits 2 if /tmp cannot
# be read, `pipefail` promotes it and `set -e` would end the report one line
# before section 7. Found by the same mechanical sweep, not by eye.
{ ls -A /tmp 2>/dev/null | sed -E 's/[0-9]{3,}.*$//; s/[A-Za-z0-9]{8,}$//' \
  | sort | uniq -c | sort -rn | head_n 20; } || true

echo
echo "=== 7. Deleted-but-open files ==="
report_deleted_open_files

echo
echo "=== 8. Leftover diagnostic mounts ==="
if findmnt -n /mnt/rootcheck >/dev/null 2>&1; then
  echo "/mnt/rootcheck is STILL MOUNTED (bind of /, left over from the 2026-08-31 session)."
  echo "  It consumes no space, but it makes every non--xdev traversal double-count."
  echo "  Remove it with:  umount /mnt/rootcheck && rmdir /mnt/rootcheck"
else
  echo "/mnt/rootcheck not mounted — good"
fi

echo
echo "============================================"
echo "  Done."
echo "============================================"
