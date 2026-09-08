#!/usr/bin/env bash
# Root-privileged disk accounting for the workbench root filesystem.
#
# WHY THIS EXISTS: scripts/diagnose-nix-disk.sh ran `find "$d" 2>/dev/null` as an
# unprivileged user. Every root-only tree (/root, /var/lib/docker,
# /var/lib/kubelet, /var/lib/private, /var/lib/rancher/k3s/storage) is skipped
# SILENTLY by that, so its inode and byte counts are floors, not totals — and the
# 2026-08-31 handoff read the resulting shortfall as "ext4 metadata overhead".
# ext4 does not consume *used* inodes for metadata, so that reading cannot be
# right: every used inode is a real file or directory.
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
# `foreign_entries` then affirmatively printed "none". MEASURED 2026-09-07 over
# a mode-0400 directory holding three entries, on the two GNU findutils builds
# this script can actually resolve, both rc 1: 4.10.0 (the system PATH, what a
# non-interactive `bash` and therefore a `sudo` run gets) `-print0` 116 bytes /
# `-printf '%D\t%p\0'` 0 bytes; 4.11.0 (the nix dev shell) 119 bytes / 0 bytes.
# Not specific to one build. (Not measured on `bfs`, which is only an
# interactive zsh alias here and never what this script runs.)
#
# Root is NOT immune, which is what makes this worth code rather than a note. A
# FUSE mountpoint not mounted `allow_other` (an AppImage's /tmp/.mount_*, gvfs,
# sshfs), or an entry on a device answering ESTALE/EIO, fails `stat` for uid 0
# too — and /tmp is exactly where those live.
#
# So the stderr is KEPT, in a file, and every caller reports the count. Same
# reason section 2's find writes to $DENIED_LOG instead of /dev/null: a scan
# that reports a number with no denial count is a FLOOR presented as a total.
#
# The `|| true` here covers route (a) ONLY — find's own rc 1 — of the two aborts
# `size_breakdown`'s comment describes. Route (b), `xargs` rc 123, happens one
# stage later and is guarded at each caller's `xargs`.
_depth1_nul() {
  local base="$1" errf="$2"; shift 2
  find "$base" -xdev -mindepth 1 -maxdepth 1 "$@" -printf '%D\t%p\0' 2>"$errf" || true
}

# How many entries the enumeration whose stderr is in $1 could not stat. ALWAYS
# a single integer and never empty — the callers compare it with `-gt`, and the
# `grep -c` / `|| echo 0` two-line-zero defect `report_denials` carries a
# comment about is the same hazard one function over.
_unstattable_count() {
  local n
  n=$(grep -c . "$1" 2>/dev/null; true)
  case "$n" in ''|*[!0-9]*) echo 0 ;; *) echo "$n" ;; esac
}

# Print the blind spot the count in $1 represents, or nothing. $2 = the base
# being measured, $3 = the stderr file. Loud on purpose: an entry that reached
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
size_breakdown() {
  local base="$1" dev errf blind out
  dev=$(_dev_of "$base") || dev=
  if ! _dev_is_valid "$dev"; then
    echo "COULD NOT MEASURE: no device id for $base — NOT an empty directory (size breakdown)"
    return 0
  fi
  errf=$(mktemp) || { echo "COULD NOT MEASURE: no temp file for $base's size breakdown"; return 0; }
  out=$(_depth1_nul "$base" "$errf" \
    | { _on_device "$dev" || true; } \
    | { xargs -0 -r du -sh -x 2>/dev/null || true; } \
    | sort -rh | head_n 15)
  blind=$(_unstattable_count "$errf") || blind=0
  if [ -n "$out" ]; then
    printf '%s\n' "$out"
  else
    echo "  none — no depth-1 entry of $base is on $base's own filesystem (NOT zero bytes)"
  fi
  _report_unstattable "$blind" "$base" "$errf"
  rm -f "$errf"
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
inode_breakdown() {
  local base="$1" dev errf blind out
  dev=$(_dev_of "$base") || dev=
  if ! _dev_is_valid "$dev"; then
    echo "COULD NOT MEASURE: no device id for $base — NOT an empty directory (inode breakdown)"
    return 0
  fi
  errf=$(mktemp) || { echo "COULD NOT MEASURE: no temp file for $base's inode breakdown"; return 0; }
  out=$(_depth1_nul "$base" "$errf" -type d \
    | { _on_device "$dev" || true; } \
    | { xargs -0 -r -n1 sh -c 'printf "%12d  %s\n" "$(find "$1" -xdev -printf . 2>/dev/null | wc -c)" "$1"' _ || true; } \
    | sort -rn | head_n 15)
  blind=$(_unstattable_count "$errf") || blind=0
  if [ -n "$out" ]; then
    printf '%s\n' "$out"
  else
    echo "  none — no depth-1 DIRECTORY of $base is on $base's own filesystem (NOT zero inodes)"
  fi
  _report_unstattable "$blind" "$base" "$errf"
  rm -f "$errf"
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
  local base="$1" dev errf blind p n=0
  dev=$(_dev_of "$base") || dev=
  if ! _dev_is_valid "$dev"; then
    echo "  COULD NOT MEASURE: no device id for $base — NOT an absence of foreign mounts"
    return 0
  fi
  errf=$(mktemp) || { echo "  COULD NOT MEASURE: no temp file for $base's foreign-entry listing"; return 0; }
  while IFS= read -r -d '' p; do
    n=$((n + 1))
    [ "$n" -gt 15 ] || printf '  %s\n' "$p"
  done < <(_depth1_nul "$base" "$errf" | _not_on_device "$dev")
  blind=$(_unstattable_count "$errf") || blind=0
  if [ "$n" -gt 15 ]; then
    printf '  ... and %d more\n' "$((n - 15))"
  elif [ "$n" -eq 0 ]; then
    if [ "$blind" -gt 0 ]; then
      echo "  none VISIBLE — and the line below says why that is NOT 'no foreign mounts'"
    else
      echo "  none — every depth-1 entry is on the same filesystem as $base"
    fi
  fi
  _report_unstattable "$blind" "$base" "$errf"
  rm -f "$errf"
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
# `du -sh`). It is now done mechanically: join backslash continuations, then
# take every line in this file whose first word is one of
# find/du/ls/dumpe2fs/findmnt/lsof/stat/xargs/grep/sed/sort/uniq/wc/tr/awk/
# mktemp/date/seq/dd/id, plus every `VAR=$(…)`, and require a `||` (or a
# trailing `; true` inside the substitution, which has the same effect).
# Everything that scan reports is guarded above EXCEPT these, each checked by
# hand and left open on purpose:
#   * `X=$((…))` — MEASURED: an arithmetic assignment is rc 0 even when the
#     expression evaluates to 0, so it is not in the class at all.
#   * `read … < <(find … | awk …)` in section 2 — MEASURED: a process
#     substitution's status is NOT checked by `set -e`, which is why denials in
#     section 2 do not kill the run.
#   * `out=$(… | sort … | head_n …)` in the two breakdowns — every stage but
#     `sort` carries its own `|| true`, and `sort` deliberately does not; the
#     reason, and what it measured, is in `size_breakdown`'s comment. What is
#     exposed is a `sort` that fails for its own reasons (no space for its temp
#     files), which is the same exposure the statement-level pipeline had before
#     this round rather than a new one.
#   * `DENIED_LOG=$(mktemp …)` and the five `stat -f` reads in section 1 — these
#     abort BEFORE any figure is printed, so they cannot leave a floor looking
#     like a total. They end the run with a blank report, which is wrong-looking
#     rather than reassuring. That is the criterion, and it is the only reason
#     they are not guarded.
DEV=${DEV:-/dev/nvme0n1p2}
DENIED_LOG=$(mktemp /tmp/disk-accounting-denied.XXXXXX)
trap 'rm -f "$DENIED_LOG"' EXIT

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
echo "=== 2. Inodes and allocated bytes per top-level directory ==="
echo "  -xdev: stays on the root fs. /mnt/rootcheck is EXCLUDED — it is a bind mount"
echo "  of / and would double-count the entire filesystem."
echo "  HARDLINKS ARE DEDUPED. find visits every LINK, so a naive '%b' sum counts a"
echo "  hardlinked file once per link — /nix/store is ~1.46M files hardlinked into"
echo "  .links, and the 2026-09-01 run over-counted by 32.3M entries / ~664 GiB that"
echo "  way, producing a NEGATIVE residual. Each inode is now counted once."
TOTAL_INODES=0
TOTAL_DEDUPED=0
printf '%14s %12s %10s  %s\n' "INODES" "GiB(alloc)" "dup-links" "PATH"
for d in /*; do
  case "$d" in
    /proc|/sys|/dev|/run|/mnt) continue ;;
  esac
  [ -d "$d" ] || continue
  [ -L "$d" ] && continue
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
done
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
  { find /var/lib/rancher/k3s/storage -xdev -mindepth 1 -maxdepth 1 -print0 2>/dev/null || true; } \
    | { xargs -0 -r du -sh --exclude=/mnt 2>/dev/null || true; } | sort -rh | head_n 30
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
    printf '%10s %12d inodes  %s\n' \
      "$(du -sh -x "$d" 2>/dev/null | awk '{print $1}')" \
      "$(find "$d" -xdev -printf . 2>/dev/null | wc -c)" "$d"
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
# Create BOTH before widening the trap: if the second mktemp failed while the trap
# still named only $DENIED_LOG, the first temp file leaked on that exit path.
ONROOT_LIST=$(mktemp) && FOREIGN_LIST=$(mktemp) || { rm -f "${ONROOT_LIST:-}"; exit 3; }
trap 'rm -f "$DENIED_LOG" "$ONROOT_LIST" "$FOREIGN_LIST"' EXIT
split_by_device /home "$ONROOT_LIST" "$FOREIGN_LIST" "$ROOT_DEV"
# `|| true` for the same reason as section 6d: xargs exits 123 when any `du` it
# ran exited 1, which is what a /home directory unlinked mid-scan produces, and
# `pipefail` + `set -e` would take the whole report with it.
{ xargs -0 -r du -sh -x < "$ONROOT_LIST" 2>/dev/null || true; } | sort -rh | head_n 15
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
