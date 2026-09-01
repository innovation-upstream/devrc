---
---
# Handoff: nix-disk-cleanup — 2026-08-31

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Diagnose and resolve disk pressure on the workbench NixOS host (root partition `/dev/nvme0n1p2`, 1.8TB). The host was at 87% usage with ~228G free. The session freed ~200G through cleanup, then investigated why the filesystem reports 1.5TB used while only ~600GB of data is measurable.

## State now
- Branch: `handoff-nix-disk-v3` (worktree off `origin/handoff-nix-disk-cleanup`). The doc has **never been merged to `main` and has no PR**.
- Claim held: `nix-disk-cleanup-1` (`claim-work.sh --release nix-disk-cleanup-1` when done).
- No clawgate task. `clawgate_handoff.sh resolve` exited **5 — NOTHING RESOLVED**, with its positive control confirming the board is reachable. That is not a clean bill of health: a wrong session id also answers 200 with an empty array. No `clawgate-task:` field recorded.
- Live disk state re-measured 2026-09-01: `df -h /` = 1.8T size, **1.4T used, 315G avail, 82%**; `df -i /` = **96,660,275 inodes used** of 122,036,224.

### What's DONE this session (session 3)
1. **REFUTED the session-2 "SOLVED / CONFIRMED" diagnosis.** See the investigation block below. The ~900GB was never metadata.
2. **Identified the instrument defect that produced it** — `scripts/diagnose-nix-disk.sh:28` runs `find "$d" 2>/dev/null` unprivileged.
3. **Wrote `scripts/diagnose-disk-accounting.sh`** (root-required; refuses non-root with rc 2). Counts inodes *and* allocated blocks with `-xdev`, excludes the `/mnt/rootcheck` bind mount, drills per-PVC into `/var/lib/rancher/k3s/storage`, and reports its own denial count + the inode residual rather than hiding them. Folds in the old script's nix-store/.links and /home breakdowns.
4. **Corrected the rank-1 arithmetic** — `tune2fs -m 1` frees 74.5 GiB of *available* space, not 93 GB of *used* space.

### What's IN FLIGHT
- An unprivileged per-directory inode+byte scan of the readable trees. `/var` and `/root` returned; `/tmp`, `/usr`, `/etc`, `/opt`, `/srv`, `/boot`, `/home`, `/nix` still running. Output accumulates at `<scratchpad>/inode-scan.txt`, terminated by a `SCAN-COMPLETE` line. It cannot answer the question on its own — the root-only trees are exactly the blind spot — but it bounds `/home` and `/nix`, which are user-readable.
- `scripts/diagnose-disk-accounting.sh` has **never been run as root**. Everything it would report is unmeasured.
- Deploy/verify status: nothing deployed. No `tune2fs` run. The filesystem is untouched by this session.

## Open investigations — live diagnosis state

### ~900GB gap between measured data and df-reported usage — SOLVED
- **Symptom:** `df` reports 1.5TB used on `/dev/nvme0n1p2`. Measured data accounts for ~600GB. Gap: ~900GB.
- **Observed (with values):**
  - Filesystem: 488M blocks total, 103M free, 24M reserved (93GB), 1GB journal
  - Inodes: 98M used, 23M free (122M total)
  - `find /nix/store | wc -l` = 10M inodes
  - `find /home | wc -l` = 10M inodes
  - `find /var | wc -l` = <1M inodes
  - Total found: ~20M inodes
  - `/nix/store` unique data (.links): 142GB
  - `/nix/store` narSize in DB: 188GB
  - `/home` total: ~200GB
  - `/swapfile`: 48GB
  - `/var`: 6GB
  - `/tmp`: 193K entries (17K nix-develop dirs, 16K nix-shell dirs, 229 stale .so files)
  - nix database (db.sqlite + WAL): 305MB
- **Ruled out (CORRECTED from prior session):**
  - ~~Not hidden data under RO mount~~ — **DISPROVED.** NixOS populates `/nix/store` during boot, then applies RO mount. The RO mount shows current filesystem state, doesn't hide old data. `nix-collect-garbage` deletes through the underlying RW filesystem. Evidence: RO mount is `32 31 259:8 /nix/store /nix/store ro,... - ext4 /dev/nvme0n1p2 rw` — device is `rw`, mount is `ro`.
  - Not orphaned store paths (only 6 found, tiny)
  - Not deleted-but-open files (lsof +L1 = 0)
  - Not `/mnt/rootcheck` (it's a bind mount of `/`, same device 259:8, no extra data)
  - Not k3s PVCs (1.7GB total in /var/lib/rancher/k3s)
  - Not `.drv` files (88k top-level files = 0.5GB)
- **Root cause (CONFIRMED):** The ~900GB gap is **ext4 metadata overhead** for a filesystem with 98M inodes:
  - 98M inodes × 4KB minimum block allocation = ~392GB just for block alignment
  - Extent trees, inode tables, block bitmaps for 98M files
  - 93GB reserved blocks (5% of partition)
  - The nix store alone has 10M inodes, each requiring its own inode structure (256 bytes) and at least one 4KB data block
- **Verified by:** `stat -f /` shows 480M blocks total, `dumpe2fs` confirms 24M reserved blocks, inode count matches `df -i`
- **Accounting:**
  - Reserved blocks: 93GB
  - Journal: 1GB
  - `/nix/store`: ~246GB
  - `/home`: ~200GB
  - `/swapfile`: 48GB
  - `/var` + `/etc` + other: ~8GB
  - `/tmp`: ~2GB
  - **Total accounted: ~598GB**
  - **Filesystem used: ~1500GB**
  - **Ext4 overhead (metadata, block alignment): ~902GB**

### Nix store 1.2TB estimate was wrong
- **Earlier claim:** "nix store is 1.2TB — two-thirds of the disk"
- **Actual:** The nix store unique data is ~142GB (.links) + ~3GB metadata = ~145GB. The 1.2TB estimate was based on sampling that didn't account for hardlink dedup correctly.

### ~900GB gap between measured data and df-reported usage — SOLVED
- **Symptom:** `df` reports 1.5TB used on `/dev/nvme0n1p2`. Measured data accounts for ~600GB. Gap: ~900GB.
- **Observed (with values):**
  - Filesystem: 488M blocks total, 103M free, 24M reserved (93GB), 1GB journal
  - Inodes: 98M used, 23M free (122M total)
  - `find /nix/store | wc -l` = 10M inodes
  - `find /home | wc -l` = 10M inodes
  - `find /var | wc -l` = <1M inodes
  - Total found: ~20M inodes
  - `/nix/store` unique data (.links): 142GB
  - `/nix/store` narSize in DB: 188GB
  - `/home` total: ~200GB
  - `/swapfile`: 48GB
  - `/var`: 6GB
  - `/tmp`: 193K entries (17K nix-develop dirs, 16K nix-shell dirs, 229 stale .so files)
  - nix database (db.sqlite + WAL): 305MB
- **Ruled out (CORRECTED from prior session):**
  - ~~Not hidden data under RO mount~~ — **DISPROVED.** NixOS populates `/nix/store` during boot, then applies RO mount. The RO mount shows current filesystem state, doesn't hide old data. `nix-collect-garbage` deletes through the underlying RW filesystem. Evidence: RO mount is `32 31 259:8 /nix/store /nix/store ro,... - ext4 /dev/nvme0n1p2 rw` — device is `rw`, mount is `ro`.
  - Not orphaned store paths (only 6 found, tiny)
  - Not deleted-but-open files (lsof +L1 = 0)
  - Not `/mnt/rootcheck` (it's a bind mount of `/`, same device 259:8, no extra data)
  - Not k3s PVCs (1.7GB total in /var/lib/rancher/k3s)
  - Not `.drv` files (88k top-level files = 0.5GB)
- **Root cause (CONFIRMED):** The ~900GB gap is **ext4 metadata overhead** for a filesystem with 98M inodes:
  - 98M inodes × 4KB minimum block allocation = ~392GB just for block alignment
  - Extent trees, inode tables, block bitmaps for 98M files
  - 93GB reserved blocks (5% of partition)
  - The nix store alone has 10M inodes, each requiring its own inode structure (256 bytes) and at least one 4KB data block
- **Verified by:** `stat -f /` shows 480M blocks total, `dumpe2fs` confirms 24M reserved blocks, inode count matches `df -i`
- **Accounting:**
  - Reserved blocks: 93GB
  - Journal: 1GB
  - `/nix/store`: ~246GB
  - `/home`: ~200GB
  - `/swapfile`: 48GB
  - `/var` + `/etc` + other: ~8GB
  - `/tmp`: ~2GB
  - **Total accounted: ~598GB**
  - **Filesystem used: ~1500GB**
  - **Ext4 overhead (metadata, block alignment): ~902GB**

### Nix store 1.2TB estimate was wrong
- **Earlier claim:** "nix store is 1.2TB — two-thirds of the disk"
- **Actual:** The nix store unique data is ~142GB (.links) + ~3GB metadata = ~145GB. The 1.2TB estimate was based on sampling that didn't account for hardlink dedup correctly.

### The "~902GB ext4 metadata overhead" root cause is REFUTED — the gap is unmeasured FILES
- **Symptom + exact repro:** `df -h /` reports ~1.4T used on `/dev/nvme0n1p2` while the session-2 measurements accounted for only ~600GB. Session 2 closed this as "ext4 metadata overhead for 98M inodes" and marked it SOLVED/CONFIRMED.
- **Why that cannot be right (mechanism):** ext4 metadata — inode tables, block bitmaps, group descriptors, extent trees — consumes **blocks**, never **used inodes**. `df -i` IUsed = inode count − free inodes, and ext4 reserves only ~11 system inodes (root=2, resize=7, journal=8, lost+found=11). So **every one of the 96.3M used inodes is a real file, directory or symlink.** "98M inodes exist but `find` only sees 20M, therefore the rest is metadata" inverts the meaning of the counter.
- **Observed (with values), computed from the superblock figures in the session-2 `output.txt`:**
  - blocks used = 488,115,343 − 103,340,948 = 384,774,395 × 4096 = **1467.8 GiB**
  - inodes used = 122,036,224 − 26,762,541 = **95,273,683**
  - inode tables = 122,036,224 × 256B = **29.1 GiB** (preallocated; counted as used blocks)
  - block bitmaps = 488,115,343 / 8 = **0.06 GiB**
  - journal = **1.0 GiB** (`Total journal size: 1024M`)
  - **TOTAL static ext4 metadata ≈ 30 GiB — not 902 GiB.** ⚠ inode size 256B is ASSUMED; the session-2 `dumpe2fs` grep did not capture `Inode size`. Even at 512B it is 58 GiB.
- **The instrument defect that produced the wrong answer:** `scripts/diagnose-nix-disk.sh:28` is `count=$(find "$d" 2>/dev/null | wc -l)`, run unprivileged. Every root-only tree is skipped **silently**. Measured 2026-09-01 as user `zach`:
  - `find /var -xdev` → **980 inodes, 6.1 GiB, 32 "Permission denied"**
  - `find /root -xdev` → **1 inode, 0.0 GiB, 1 "Permission denied"**
  - That 6.1 GiB is *exactly* the doc's "`/var`: 6GB". It is a floor produced by 32 blocked directories, not a measurement.
  - Confirmed unreadable as `zach`: `/var/lib/rancher/k3s/storage` (drwx------), `/var/lib/kubelet`, `/var/lib/docker`, `/root`, `/var/lib/private`. The "Not k3s PVCs (1.7GB total)" elimination was made through this same blind instrument and does not hold.
  - `output.txt` also shows the one run that *did* have root (`sudo ./scripts/diagnose-nix-disk.sh`) **died at `/nix` (9,786,593 inodes)** and never reached `/var` or `/home` — so the doc's `/var` and `/home` figures came from unprivileged runs.
- **Independent confirmation of the fs totals (no root needed):** kubelet `stats/summary` on node `nixos` — `KUBECONFIG=$KC_WORKBENCH kubectl get --raw /api/v1/nodes/nixos/proxy/stats/summary` — reports node fs **1424 GiB used, inodesUsed 96,292,562**. ⚠ Its per-volume rows are USELESS here: every local-path PVC reports the whole-filesystem figures (all ~1424 GiB / ~96.3M), because a local-path volume is a plain directory and kubelet stats the filesystem. Do not read those rows as PVC sizes.
- **Ruled out:** the metadata explanation (above). Everything session 2 ruled out via unprivileged `find` is **back on the table** — k3s PVCs especially.
- **Leading hypothesis:** ~86M unaccounted inodes live in the root-only trees, with `/var/lib/rancher/k3s/storage` (media-stack: `stash-generated`, `stash-metadata`, `qbittorrent-config`, plus supabase/postgres volumes) the prime candidate — a stash generated-media tree is exactly the shape that produces tens of millions of small files. UNMEASURED.
- **Next probe, verbatim:**
  ```bash
  sudo /home/zach/workspace/devrc/scripts/diagnose-disk-accounting.sh 2>&1 | tee /tmp/disk-accounting.txt
  ```
  Read section 3 (RESIDUAL) first: near zero ⇒ the byte column above it is the answer. Read section 4 before believing section 3 — a nonzero denial count means the numbers are still floors.

### Correction: `tune2fs -m 1` does not reclaim 93GB of used space
- **The doc's claim:** "Reclaim 93GB reserved blocks — `sudo tune2fs -m 1 /dev/nvme0n1p2`. Instant, safe." and it counted 93GB of reserved blocks inside its "~598GB accounted **used**".
- **Observed:** reserved blocks are **free-but-unavailable**, not used. `df` already reflects this: Size 1.8T, Used 1.4T, Avail 315G — 1831 − 1424 = 407 GiB free, minus 93.1 GiB reserved = 314 GiB avail. ✓
- **Consequence 1:** the session-2 accounting double-counted — putting reserved blocks in the "used" column makes the real unexplained gap *larger* than the doc states, not smaller.
- **Consequence 2:** 5%→1% of 488,115,343 blocks = 19,524,613 × 4096 = **74.5 GiB moved into Avail**. `Used` does not move at all. The "93GB reclaim" figure is wrong in both amount and kind.
- **Reversible:** `sudo tune2fs -m 5 /dev/nvme0n1p2` restores it.

## Next steps (ranked)
1. **Run the root diagnostic and find the ~86M files.** `sudo /home/zach/workspace/devrc/scripts/diagnose-disk-accounting.sh` on the workbench. Claude cannot sudo (devrc `CLAUDE.md`), so this is an operator step; paste the output back. Everything below rank 3 is guesswork until this runs.
   forcing: none
2. **Land this correction.** Branch `handoff-nix-disk-v3` carries the doc + `scripts/diagnose-disk-accounting.sh`; `handoff-nix-disk-cleanup` has no PR. Open one so the refutation is not stranded on an unmerged branch — and so the next `/resume` does not act on the refuted diagnosis.
   forcing: none
3. **Optional margin change:** `sudo tune2fs -m 1 /dev/nvme0n1p2` → +74.5 GiB Avail, reversible with `-m 5`. Independent of the diagnosis. Not urgent at 82% / 315G free; it trims the root-fs safety reserve from 93 GiB to 18.6 GiB on a filesystem that also backs k3s PVCs.
   forcing: none
4. **Unmount the leftover `/mnt/rootcheck` bind mount.** `sudo umount /mnt/rootcheck && sudo rmdir /mnt/rootcheck`. It consumes no space, but it makes every non-`-xdev` traversal double-count the entire filesystem — including `scripts/diagnose-nix-disk.sh:26`, which lists `/mnt`.
   forcing: none
5. **Delete or fix `scripts/diagnose-nix-disk.sh`.** It is untracked in the working tree and is the instrument that produced the wrong answer (unprivileged `find … 2>/dev/null`, no `-xdev`, `/mnt` included). `scripts/diagnose-disk-accounting.sh` supersedes it.
   forcing: none
6. **Then, and only then, decide on capacity work** — automatic GC config, moving `/nix` to its own partition, Attic. Ranks 3–5 of the session-2 list. All of them were premised on the refuted diagnosis; re-derive them from the root scan.
   forcing: none

## Gotchas / decisions / dead-ends
- **RO mount hypothesis was WRONG.** A prior session hypothesized that data written to `/nix/store` before the RO mount was applied was "hidden" by the mount. This was disproved: NixOS populates the store during boot, then mounts it RO. The RO mount shows current filesystem state; `nix-collect-garbage` operates through the underlying RW filesystem. The 75M "missing" inodes were not hidden — they are ext4 metadata overhead.
- **`e2fsck` would NOT have helped.** It checks filesystem consistency, not space reclamation. Running it would have cost downtime for no benefit. The filesystem state is `clean` (from `dumpe2fs`).
- `nix-store --optimise` freed only 11.4G on a 1.2TB store — the store was already well-deduplicated. The earlier estimate of "25-35% savings" was wrong for this store.
- `nix-collect-garbage --delete-older-than 30d` is the correct safe approach (confirmed via man pages).
- `/mnt/rootcheck` is a bind mount of `/` (same device 259:8), NOT a copy. It does not consume extra space.
- The `.links` directory (146GB) contains the unique file data after hardlink dedup. Store paths hardlink to it. `du` counts hardlinks multiple times; `df` does not.
- narSize in the nix database is the NAR serialization size, not on-disk size. For this store, the ratio is ~0.97 (disk slightly less than nar due to hardlinks).
- `du -sh /nix/store` times out (~10 minutes) due to 10M+ inodes. Use `ncdu` or sampling for faster estimates.
- **Key lesson:** Don't trust `find | wc -l` counts as "hidden data". On a filesystem with 98M inodes, `find` traversing the directory tree returns ~20M because most inodes are metadata overhead, not files in the tree. `df` counts blocks allocated for metadata structures that `find` never visits.

- **RO mount hypothesis was WRONG.** A prior session hypothesized that data written to `/nix/store` before the RO mount was applied was "hidden" by the mount. This was disproved: NixOS populates the store during boot, then mounts it RO. The RO mount shows current filesystem state; `nix-collect-garbage` operates through the underlying RW filesystem. The 75M "missing" inodes were not hidden — they are ext4 metadata overhead.
- **`e2fsck` would NOT have helped.** It checks filesystem consistency, not space reclamation. Running it would have cost downtime for no benefit. The filesystem state is `clean` (from `dumpe2fs`).
- `nix-store --optimise` freed only 11.4G on a 1.2TB store — the store was already well-deduplicated. The earlier estimate of "25-35% savings" was wrong for this store.
- `nix-collect-garbage --delete-older-than 30d` is the correct safe approach (confirmed via man pages).
- `/mnt/rootcheck` is a bind mount of `/` (same device 259:8), NOT a copy. It does not consume extra space.
- The `.links` directory (146GB) contains the unique file data after hardlink dedup. Store paths hardlink to it. `du` counts hardlinks multiple times; `df` does not.
- narSize in the nix database is the NAR serialization size, not on-disk size. For this store, the ratio is ~0.97 (disk slightly less than nar due to hardlinks).
- `du -sh /nix/store` times out (~10 minutes) due to 10M+ inodes. Use `ncdu` or sampling for faster estimates.
- **Key lesson:** Don't trust `find | wc -l` counts as "hidden data". On a filesystem with 98M inodes, `find` traversing the directory tree returns ~20M because most inodes are metadata overhead, not files in the tree. `df` counts blocks allocated for metadata structures that `find` never visits.

- 🔴 **The "Key lesson" bullet above is WRONG and is kept only as the corrected record.** It reads: *"On a filesystem with 98M inodes, `find` returns ~20M because most inodes are metadata overhead."* Inodes are never metadata overhead — `df -i` used-inodes counts files, directories and symlinks. `find` returned ~20M because it was run **unprivileged** and silently skipped every root-only tree. Do not re-derive the metadata theory from that bullet.
- **This doc's sections are duplicated.** The session-2 write ran `handoff_doc.py --new-effort` against a doc that already existed on `handoff-nix-disk-cleanup`, so "~900GB gap — SOLVED", "Nix store 1.2TB estimate was wrong" and the whole Gotchas list each appear twice. Cosmetic; do not "fix" it by rewriting the file by hand — `handoff_doc.py` owns this doc's writes.
- **Claude cannot `sudo`** (devrc `CLAUDE.md`). Every root-level probe in this investigation is an operator step. That constraint is *why* the unprivileged `find` was reached for in the first place — the fix is to stage a root script and hand it over, not to substitute a measurement that cannot see the answer.
- **`find … 2>/dev/null` is the anti-pattern.** Send stderr to a file and count `Permission denied`; a scan that reports a number with no denial count is a floor presented as a total.

## How to verify
1. **The refutation, without root:** `df -i /` (used inodes ≫ what any `find` sees) and `find /var -xdev -printf . 2>/tmp/d | wc -c; grep -c 'Permission denied' /tmp/d` — 980 inodes against 32 denials.
2. **The metadata arithmetic:** `dumpe2fs -h /dev/nvme0n1p2 | grep -iE 'Inode size|Inode count|journal size'` — inode-count × inode-size + block-count/8 + journal is tens of GiB.
3. **The tune2fs figure:** after `tune2fs -m 1`, `df -h /` Avail rises ~74.5 GiB and **Used does not change**. If Used drops, this analysis is wrong.
4. **The script's own controls:** run it as a normal user — it must exit 2 and write nothing. Section 4 must print a denial count; a run that reports 0 denials *and* a large residual is the instrument failing, not a clean result.
