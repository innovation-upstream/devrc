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
# `source`ing this file defines the helpers and RUNS NOTHING: BASH_SOURCE[0] is
# always this file, while $0 is the sourcing shell's own name, so they differ
# only when sourced. Executing it (`bash …`/`./…`) makes them equal and the
# script proceeds exactly as before — the guard is not reachable from the
# environment, so no stray variable can silently turn a real run into a no-op.
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
LSOF_BIN=${LSOF_BIN:-lsof}
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

# Top-level entries of $1, largest first.
#
# 🔴 NOT `du -sh -x "$1"/*`. MEASURED 2026-09-02: /tmp held 171,886 top-level
# entries = ~4.32 MiB of argv against an ARG_MAX of 2,097,152, so the glob dies
# E2BIG. `2>/dev/null` swallows the message and `set -euo pipefail` then kills
# the WHOLE SCRIPT mid-section — sections 6d-inodes, 7 and 8 never run, and the
# report ends with no error. That is the "truncated scan reported as a total"
# failure this very script exists to prevent.
size_breakdown() {
  find "$1" -xdev -mindepth 1 -maxdepth 1 -print0 2>/dev/null \
    | xargs -0 -r du -sh -x 2>/dev/null | sort -rh | head_n 15
}

# Inode count per top-level DIRECTORY of $1, largest first.
#
# 🔴 NOT `xargs -I{} sh -c '… "{}" …'`. That substitutes the directory NAME into
# a shell string, and /tmp is mode 1777, and this script demands sudo — so any
# local process could plant a directory whose name is a command and get it run
# AS ROOT. VERIFIED 2026-09-02: a dir named `evil";echo PWNED-AS-$(id -un) >&2;"x`
# made the old pipeline print PWNED-AS-zach. -exec with "$1" passes the name as
# an ARGUMENT, never as text to parse.
inode_breakdown() {
  find "$1" -xdev -mindepth 1 -maxdepth 1 -type d \
    -exec sh -c 'printf "%12d  %s\n" "$(find "$1" -xdev -printf . 2>/dev/null | wc -c)" "$1"' _ {} \; 2>/dev/null \
    | sort -rn | head_n 15
}

# Device id of $1, or empty. Its own function so the suite can substitute a
# fixture-controlled device map without needing two real filesystems.
_dev_of() { stat -c '%D' "$1" 2>/dev/null; }

# Partition the directories under $1 by filesystem: on-root candidates into $2
# (NUL-separated, ready for `xargs -0`), foreign ones into $3 (one per line).
# $4 is the root device id.
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
    else printf '%s\n' "$p" >> "$foreign"; fi
  done
}

# List the foreign mounts recorded by split_by_device. The "none" branch has to
# stay loud: an empty list here was, historically, the SUBSHELL BUG, not a clean
# host.
report_foreign_mounts() {
  local p
  if [ -s "$1" ]; then
    while IFS= read -r p; do
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
    grep 'Permission denied' "$log" | head_n 20
  fi
  if [ "$unclassified" -gt 0 ]; then
    echo "-- unclassified errors (read these; they are not known-benign) --"
    grep -v 'Permission denied' "$log" | grep -v 'No such file or directory' | head_n 20 || true
  fi
}

# --- end of the sourceable seam ---------------------------------------------
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "FATAL: must run as root — an unprivileged run silently skips the trees" >&2
  echo "       this script exists to measure. Re-run with sudo." >&2
  exit 2
fi

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
dumpe2fs -h "$DEV" 2>/dev/null | grep -iE 'Inode size|Inode count|Block count|Reserved block count|Journal size|Filesystem state|Last checked'
INODE_SIZE=$(dumpe2fs -h "$DEV" 2>/dev/null | awk -F: '/^Inode size/{gsub(/ /,"",$2);print $2}')
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
  gib=$(echo "$blocks" | awk '{printf "%.1f", $1*512/1073741824}')
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
  # one is bounded only by how many PVCs the node happens to hold.
  find /var/lib/rancher/k3s/storage -xdev -mindepth 1 -maxdepth 1 -print0 2>/dev/null \
    | xargs -0 -r du -sh --exclude=/mnt 2>/dev/null | sort -rh | head_n 30
  echo "--- total ---"
  du -sh /var/lib/rancher/k3s/storage 2>/dev/null
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
ROOT_DEV=$(_dev_of /)
# Create BOTH before widening the trap: if the second mktemp failed while the trap
# still named only $DENIED_LOG, the first temp file leaked on that exit path.
ONROOT_LIST=$(mktemp) && FOREIGN_LIST=$(mktemp) || { rm -f "${ONROOT_LIST:-}"; exit 3; }
trap 'rm -f "$DENIED_LOG" "$ONROOT_LIST" "$FOREIGN_LIST"' EXIT
split_by_device /home "$ONROOT_LIST" "$FOREIGN_LIST" "$ROOT_DEV"
xargs -0 -r du -sh -x < "$ONROOT_LIST" 2>/dev/null | sort -rh | head_n 15
echo "--- NOT on the root filesystem, so NOT part of this accounting ---"
report_foreign_mounts "$FOREIGN_LIST"

echo
echo "=== 6d. /tmp breakdown — MEASURED 2026-09-01 as the largest inode consumer ==="
echo "  78,501,285 entries / 469 GiB, 81% of this filesystem's inodes. /tmp is on"
echo "  the ROOT partition here, not tmpfs, so nothing clears it at boot."
printf 'top-level entries : %d\n' "$(ls -A /tmp 2>/dev/null | wc -l)"
echo "--- top 15 by allocated size ---"
size_breakdown /tmp
echo "--- top 15 by inode count ---"
inode_breakdown /tmp
echo "--- entry-name families (what is generating them) ---"
ls -A /tmp 2>/dev/null | sed -E 's/[0-9]{3,}.*$//; s/[A-Za-z0-9]{8,}$//' \
  | sort | uniq -c | sort -rn | head_n 20

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
