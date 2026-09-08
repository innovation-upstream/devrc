#!/usr/bin/env bash
# Read-only root-partition / inode breakdown, used when df disagrees with what you can
# measure. Sibling to scripts/diagnose-disk-accounting.sh; changes nothing.
#
#     bash scripts/diagnose-nix-disk.sh          # some sections need root for full data
#     sudo bash scripts/diagnose-nix-disk.sh
#
# 🔴 NO `set -e`, ON PURPOSE. Nearly every command here is allowed to fail: `find`
# exits non-zero on a permission-denied entry, `lsof` is not installed on either host,
# and a device or path may not exist. Under `set -euo pipefail` this script aborted at
# section 3 of 10 as an ordinary user and section 2 on the laptop — printing NOTHING
# about why, because `set -e` is silent and the stderr was already discarded. For a
# diagnostic, a section that says "(unavailable)" is worth more than a clean exit that
# skipped eight of them.
set -uo pipefail

# The root device is DERIVED, never hardcoded: it is nvme0n1p2 on the workbench and
# nvme0n1p1 on the laptop, and a frozen literal silently reports the wrong device.
ROOT_DEV="$(findmnt -no SOURCE / 2>/dev/null || echo unknown)"
ROOT_FS="$(findmnt -no FSTYPE / 2>/dev/null || echo unknown)"
ROOT_SIZE="$(df -h --output=size / 2>/dev/null | tail -1 | tr -d ' ')"
ROOT_USED="$(df -h --output=used / 2>/dev/null | tail -1 | tr -d ' ')"

have() { command -v "$1" >/dev/null 2>&1; }
skip() { echo "  (unavailable: $*)"; }

echo "============================================"
echo "  NixOS Root Partition Diagnosis"
echo "  ${ROOT_DEV}  (${ROOT_FS}, ${ROOT_SIZE:-?} total, ${ROOT_USED:-?} used)"
echo "  $(hostname)  $(date -Is)"
echo "============================================"
echo ""

# 1. Filesystem block stats
echo "=== 1. Filesystem block stats ==="
echo "--- df ---"
df -hT /
echo ""
echo "--- stat -f ---"
stat -f /
echo ""

# 2. dumpe2fs summary (block groups, free blocks, journal)
echo "=== 2. dumpe2fs summary ==="
if ! have dumpe2fs; then
  skip "dumpe2fs not on PATH"
elif [ "$ROOT_FS" != "ext2" ] && [ "$ROOT_FS" != "ext3" ] && [ "$ROOT_FS" != "ext4" ]; then
  skip "root is $ROOT_FS, not ext*"
else
  dumpe2fs -h "$ROOT_DEV" 2>/dev/null \
    | grep -iE 'Block count|Free blocks|Inode count|Free inodes|Journal|Reserved|Filesystem features|Mount count|Last checked|State' \
    || skip "dumpe2fs failed on $ROOT_DEV (needs root?)"
fi
echo ""

# 3. Inode usage per top-level dir
echo "=== 3. Inode count per top-level directory ==="
for d in /nix /home /var /tmp /etc /usr /root /srv /opt /boot /mnt /run; do
  if [ -d "$d" ]; then
    count=$(find "$d" 2>/dev/null | wc -l)
    printf "%12d  %s\n" "$count" "$d"
  fi
done | sort -rn
echo "  (counts are a FLOOR as non-root: unreadable dirs are skipped by find)"
echo ""

# 4. Actual file sizes per top-level dir (apparent size)
echo "=== 4. Apparent file size per top-level directory ==="
for d in /nix /home /var /tmp /etc /usr /root /srv /opt /boot /mnt; do
  if [ -d "$d" ]; then
    size=$(find "$d" -type f -printf '%s\n' 2>/dev/null | awk '{s+=$1} END {printf "%.1f", s/1073741824}')
    printf "%8sGB  %s\n" "${size:-0.0}" "$d"
  fi
done | sort -rn
echo ""

# 5. Check for deleted-but-referenced files
echo "=== 5. Deleted files still holding space ==="
if have lsof; then
  lsof +L1 2>/dev/null | head -20
  echo "Count: $(lsof +L1 2>/dev/null | wc -l)"
else
  skip "lsof not installed — try: nix-shell -p lsof --run 'lsof +L1'"
fi
echo ""

# 6. Ext4 journal
echo "=== 6. Ext4 journal ==="
ls -lh /proc/1/root/.journal 2>/dev/null || echo "  No journal file at /proc/1/root/.journal"
if have dumpe2fs; then
  dumpe2fs -h "$ROOT_DEV" 2>/dev/null | grep -i journal || skip "dumpe2fs failed on $ROOT_DEV"
else
  skip "dumpe2fs not on PATH"
fi
echo ""

# 7. Nix store breakdown
echo "=== 7. Nix store breakdown ==="
if [ -d /nix/store ]; then
  echo "Top-level entries:"
  ls /nix/store/ 2>/dev/null | wc -l
  echo "  Directories: $(ls -d /nix/store/*/ 2>/dev/null | wc -l)"
  echo "  Files:       $(ls -l /nix/store/ 2>/dev/null | grep -c '^-')"
  echo "  Symlinks:    $(ls -l /nix/store/ 2>/dev/null | grep -c '^l')"
  echo ""
  echo ".links unique data:"
  du -sh /nix/store/.links 2>/dev/null || skip "cannot read /nix/store/.links (needs root)"
  echo "Files in .links:"
  find /nix/store/.links -type f 2>/dev/null | wc -l
else
  skip "no /nix/store on this host"
fi
echo ""

# 8. /home breakdown
echo "=== 8. /home breakdown ==="
for d in "$HOME"/.local/share/Steam "$HOME"/.ollama "$HOME"/.cache "$HOME"/.config \
         "$HOME"/workspace "$HOME"/go "$HOME"/Downloads "$HOME"/hetzner-volumes; do
  if [ -d "$d" ]; then
    size=$(du -sh "$d" 2>/dev/null | awk '{print $1}')
    printf "%8s  %s\n" "${size:-?}" "$d"
  fi
done
echo ""

# 9. Swapfile
echo "=== 9. Swap ==="
swapon --show 2>/dev/null || skip "swapon failed"
ls -lh /swapfile 2>/dev/null || echo "  (no /swapfile on this host)"
echo ""

# 10. Reserved blocks
echo "=== 10. Reserved blocks for root ==="
if have tune2fs; then
  tune2fs -l "$ROOT_DEV" 2>/dev/null | grep -i 'reserved' || skip "tune2fs failed on $ROOT_DEV (needs root?)"
else
  skip "tune2fs not on PATH"
fi
echo ""

echo "============================================"
echo "  Done — all 10 sections attempted."
echo "============================================"
