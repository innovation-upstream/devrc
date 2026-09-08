#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  NixOS Root Partition Diagnosis"
echo "  /dev/nvme0n1p2  (1.8TB, ~1.4TB used)"
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
dumpe2fs -h /dev/nvme0n1p2 2>/dev/null | grep -iE 'Block count|Free blocks|Inode count|Free inodes|Journal|Reserved|Filesystem features|Mount count|Last checked|State'
echo ""

# 3. Inode usage per top-level dir
echo "=== 3. Inode count per top-level directory ==="
for d in /nix /home /var /tmp /etc /usr /root /srv /opt /boot /mnt /run; do
  if [ -d "$d" ]; then
    count=$(find "$d" 2>/dev/null | wc -l)
    printf "%12d  %s\n" "$count" "$d"
  fi
done | sort -rn
echo ""

# 4. Actual file sizes per top-level dir (apparent size)
echo "=== 4. Apparent file size per top-level directory ==="
for d in /nix /home /var /tmp /etc /usr /root /srv /opt /boot /mnt; do
  if [ -d "$d" ]; then
    size=$(find "$d" -type f -printf '%s\n' 2>/dev/null | awk '{s+=$1} END {printf "%.1f", s/1073741824}')
    printf "%8sGB  %s\n" "$size" "$d"
  fi
done | sort -rn
echo ""

# 5. Check for deleted-but-referenced files
echo "=== 5. Deleted files still holding space ==="
lsof +L1 2>/dev/null | head -20
echo "Count: $(lsof +L1 2>/dev/null | wc -l)"
echo ""

# 6. Check ext4 journal size
echo "=== 6. Ext4 journal ==="
ls -lh /proc/1/root/.journal 2>/dev/null || echo "No journal at /proc/1/root/.journal"
dumpe2fs -h /dev/nvme0n1p2 2>/dev/null | grep -i journal
echo ""

# 7. Nix store breakdown
echo "=== 7. Nix store breakdown ==="
echo "Top-level entries:"
ls /nix/store/ 2>/dev/null | wc -l
echo "  Directories: $(ls -d /nix/store/*/ 2>/dev/null | wc -l)"
echo "  Files:       $(ls -l /nix/store/ 2>/dev/null | grep -c '^-')"
echo "  Symlinks:    $(ls -l /nix/store/ 2>/dev/null | grep -c '^l')"
echo ""
echo ".links unique data:"
du -sh /nix/store/.links 2>/dev/null
echo "Files in .links:"
find /nix/store/.links -type f 2>/dev/null | wc -l
echo ""

# 8. /home breakdown
echo "=== 8. /home breakdown ==="
for d in /home/zach/.local/share/Steam /home/zach/.ollama /home/zach/.cache /home/zach/.config /home/zach/workspace /home/zach/go /home/zach/Downloads /home/zach/hetzner-volumes; do
  if [ -d "$d" ]; then
    size=$(du -sh "$d" 2>/dev/null | awk '{print $1}')
    printf "%8s  %s\n" "$size" "$d"
  fi
done
echo ""

# 9. Swapfile
echo "=== 9. Swapfile ==="
ls -lh /swapfile 2>/dev/null
echo ""

# 10. Reserved blocks
echo "=== 10. Reserved blocks for root ==="
tune2fs -l /dev/nvme0n1p2 2>/dev/null | grep -i 'reserved'
echo ""

echo "============================================"
echo "  Done. Review output above."
echo "============================================"
