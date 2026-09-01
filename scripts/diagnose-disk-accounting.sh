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
TOTAL_INODES=0
printf '%14s %12s  %s\n' "INODES" "GiB(alloc)" "PATH"
for d in /*; do
  case "$d" in
    /proc|/sys|/dev|/run|/mnt) continue ;;
  esac
  [ -d "$d" ] || continue
  [ -L "$d" ] && continue
  read -r n blocks < <(
    find "$d" -xdev -printf '%b\n' 2>>"$DENIED_LOG" \
      | awk '{n++; b+=$1} END {print n+0, b+0}'
  )
  gib=$(echo "$blocks" | awk '{printf "%.1f", $1*512/1073741824}')
  printf '%14d %12s  %s\n' "$n" "$gib" "$d"
  TOTAL_INODES=$((TOTAL_INODES + n))
done

echo
echo "=== 3. Residual — the number the whole question turns on ==="
printf 'inodes counted  : %d\n' "$TOTAL_INODES"
printf 'inodes used (fs): %d\n' "$INODES_USED"
printf 'RESIDUAL        : %d\n' "$((INODES_USED - TOTAL_INODES))"
echo "A residual near zero means the tree is fully accounted for and the byte column"
echo "above is the real answer. A large residual means something is STILL unmeasured —"
echo "read the denial report below before drawing any conclusion from it."

echo
echo "=== 4. Blind-spot report (positive control) ==="
DENIED=$(grep -c 'Permission denied' "$DENIED_LOG" 2>/dev/null || echo 0)
OTHER=$(grep -vc 'Permission denied' "$DENIED_LOG" 2>/dev/null || echo 0)
echo "directories find could not read : $DENIED"
echo "other find errors               : $OTHER"
if [ "$DENIED" -gt 0 ]; then
  echo "!! Running as root and STILL denied — the counts above are floors, not totals."
  grep 'Permission denied' "$DENIED_LOG" | head -20
fi
[ "$OTHER" -gt 0 ] && grep -v 'Permission denied' "$DENIED_LOG" | head -20

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
echo "=== 6c. /home breakdown (top 15 by allocated size) ==="
du -sh -x /home/*/.* /home/*/* 2>/dev/null | sort -rh | head -15

echo
echo "=== 7. Deleted-but-open files ==="
lsof +L1 2>/dev/null | awk 'NR>1{s+=$8} END {printf "count=%d bytes=%.1f GiB\n", NR-1, s/1073741824}'

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
