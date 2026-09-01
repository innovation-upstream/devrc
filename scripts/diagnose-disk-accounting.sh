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
# `grep -c` prints 0 AND exits 1 when there are no matches, so `|| echo 0` used to
# emit a two-line "0\n0" and every later [ -gt ] on it died with "integer expected"
# — i.e. the denial guard failed exactly when it had something to report. Count
# with a form that cannot fail, and verify it is a single integer.
DENIED=$(grep -c 'Permission denied' "$DENIED_LOG" 2>/dev/null; true)
OTHER=$(grep -c 'No such file or directory' "$DENIED_LOG" 2>/dev/null; true)
TOTAL_ERR=$(wc -l < "$DENIED_LOG" 2>/dev/null || echo 0)
case "$DENIED$OTHER" in *[!0-9]*|'') echo "!! denial counter is broken — treat every count above as UNVERIFIED"; DENIED=0; OTHER=0 ;; esac
UNCLASSIFIED=$((TOTAL_ERR - DENIED - OTHER))
echo "directories find could not read      : $DENIED"
echo "vanished mid-scan (benign, transient): $OTHER"
echo "OTHER, unclassified                  : $UNCLASSIFIED"
if [ "$DENIED" -gt 0 ]; then
  echo "!! Running as root and STILL denied — the counts above are FLOORS, not totals."
  grep 'Permission denied' "$DENIED_LOG" | head -20
fi
if [ "$UNCLASSIFIED" -gt 0 ]; then
  echo "-- unclassified errors (read these; they are not known-benign) --"
  grep -v 'Permission denied' "$DENIED_LOG" | grep -v 'No such file or directory' | head -20
fi

echo
echo "=== 5. k3s local-path PVCs (unreadable without root; the prior '1.7GB' claim) ==="
if [ -d /var/lib/rancher/k3s/storage ]; then
  du -sh --exclude=/mnt /var/lib/rancher/k3s/storage/* 2>/dev/null | sort -rh | head -30
  echo "--- total ---"
  du -sh /var/lib/rancher/k3s/storage 2>/dev/null
  echo "--- inodes per PVC (top 15) ---"
  for p in /var/lib/rancher/k3s/storage/*; do
    [ -d "$p" ] || continue
    printf '%12d  %s\n' "$(find "$p" -xdev -printf . 2>/dev/null | wc -c)" "$p"
  done | sort -rn | head -15
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
# 🔴 `du -x` only stops du CROSSING AWAY from its starting point. When the starting
# point IS a foreign mount, du walks the whole thing: the 2026-09-01 run reported
# 12T for /home/zach/hdd-20tb (/dev/sda1, xfs, 18.2T) and 1.2T for old-nix-hdd
# (/dev/sdc1) under a root filesystem that is 1.8T in total — figures a reader can
# take for root-fs usage, contradicting section 2's correct 686.8 GiB for /home.
# Walking them is also what made that run take ~3 hours. Compare each candidate's
# device against / and skip the foreign ones, then list them separately.
ROOT_DEV=$(stat -c '%D' / 2>/dev/null)
FOREIGN=""
for p in /home/*/* /home/*/.*; do
  case "${p##*/}" in .|..) continue ;; esac
  [ -d "$p" ] || continue
  d=$(stat -c '%D' "$p" 2>/dev/null) || continue
  if [ "$d" = "$ROOT_DEV" ]; then echo "$p"; else FOREIGN="$FOREIGN $p"; fi
done | tr '\n' '\0' | xargs -0 -r du -sh -x 2>/dev/null | sort -rh | head -15
echo "--- NOT on the root filesystem, so NOT part of this accounting ---"
if [ -n "$FOREIGN" ]; then
  for p in $FOREIGN; do
    printf '  %-40s %s\n' "$p" "$(findmnt -n -o SOURCE,FSTYPE,SIZE,USED --target "$p" 2>/dev/null | head -1)"
  done
else
  echo "  none"
fi

echo
echo "=== 6d. /tmp breakdown — MEASURED 2026-09-01 as the largest inode consumer ==="
echo "  78,501,285 entries / 469 GiB, 81% of this filesystem's inodes. /tmp is on"
echo "  the ROOT partition here, not tmpfs, so nothing clears it at boot."
printf 'top-level entries : %d\n' "$(ls -A /tmp 2>/dev/null | wc -l)"
echo "--- top 15 by allocated size ---"
du -sh -x /tmp/* 2>/dev/null | sort -rh | head -15
echo "--- top 15 by inode count ---"
find /tmp -xdev -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null \
  | xargs -0 -r -I{} sh -c 'printf "%12d  %s\n" "$(find "{}" -xdev -printf . 2>/dev/null | wc -c)" "{}"' \
  | sort -rn | head -15
echo "--- entry-name families (what is generating them) ---"
ls -A /tmp 2>/dev/null | sed -E 's/[0-9]{3,}.*$//; s/[A-Za-z0-9]{8,}$//' \
  | sort | uniq -c | sort -rn | head -20

echo
echo "=== 7. Deleted-but-open files ==="
# The 2026-09-01 run printed `count=-1`: the awk did NR-1 to drop lsof's header,
# but with no output at all NR is 0. A negative count also hid the distinction
# between "lsof found nothing" and "lsof did not run" — the two readings that
# matter most here, since a zero is the reassuring one.
if ! command -v lsof >/dev/null 2>&1; then
  echo "COULD NOT MEASURE: lsof not on PATH — this is NOT a zero"
else
  LSOF_OUT=$(lsof +L1 2>/dev/null); LSOF_RC=$?
  if [ -z "$LSOF_OUT" ]; then
    echo "count=0 bytes=0.0 GiB  (lsof exited $LSOF_RC with no rows — no deleted-but-open files)"
  else
    printf '%s\n' "$LSOF_OUT" | awk 'NR>1{n++; s+=$8} END {printf "count=%d bytes=%.1f GiB\n", n+0, s/1073741824}'
  fi
fi

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
