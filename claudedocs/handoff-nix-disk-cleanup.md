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
- 🔴 **RANK 15 IS PART-DONE AND `#1409` IS NOT MERGED.** Do not report it as merged. The claim
  `nix-disk-cleanup-15` is HELD by this session; `nix-disk-cleanup-13` is still HELD and is
  released only when `#1409` lands.
- 🔴 **THE TEKTON RED IS GONE — resolved the STRONG way, not by signature match.** The
  handoff asked to "confirm the red is still the #863 flake". Better evidence arrived:
  the **unchanged** head `e1536984` was re-run as `devrc-ci-mkpb5` (16:16:32Z → 16:43:57Z)
  and **both legs pass**, counts read rather than inferred from the `Succeeded` reason:
  - `tekton/devrc-pytests` → `collected=21143 passed=21141 skipped=2 failed=0` (floor 20342 = 28 per-target floors)
  - `tekton/devrc-nodetests` → `suites=5 files=41 tests=1449 pass=1449 fail=0` (floor 1367)
  - `gh pr view 1409` → `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`.
  Same commit, same pipeline, opposite result ⇒ flake, established without needing the
  signature to match. **There is no longer a gate to "merge through"**, so that hazard is moot.
- **The merged tree was built and inspected, and it is NOT what Tekton tested.** `main` moved
  **48 commits** under this PR. Worktree `/home/zach/workspace/devrc-merge-1409-f61a6666`
  = `origin/main` (`176f412b`) + `e1536984`, merged with `--no-ff`; `ort` auto-merged, rc 0,
  clean tree.
- **IN FLIGHT, NOT FINISHED:** a merged-tree run over the overlap surface —
  `scratchpad/merged-overlap-gate.sh` → `scratchpad/merged-overlap.txt`, 8 targets, one target
  per pytest invocation. At handoff time it is still on **target 1 of 8** and the terminator
  `OVERLAP-RUN-DONE` is **ABSENT**, so nothing in that file may be read as a result.
- 🔴 **THE FULL `gate.sh` WAS NOT RUN, AND THE HOST NEVER WENT QUIET.** Load rose
  **49.03 → 62.50 → 66.87** across the session with **9 → 13 concurrent `pytest scripts/tests`
  runs from other sessions**. `gate.sh` caps at 3600 s and has already been SIGTERM'd twice at
  this load. Running it would have produced an infrastructure timeout, not a verdict.
- 🔴 **CARRIED FORWARD — the audit ladder is CLOSED at 5 rounds; do NOT dispatch another.** Every
  round found something real, but rounds 4–5 found things about guards the ladder itself had
  written. Round 4's auditor stated it: *"not one finding is about the shipped classifier's
  behaviour."* Stop criterion recorded in `e1536984` and in the PR's round-4 comment. **Nothing
  this session found reopens it** — the two merged-tree items below are gate/merge questions, not
  audit findings.
- **CARRIED FORWARD — what `#1409` ships**, since the next session merges it without re-reading the
  audit: classifier re-keyed 3 → 9 markers, grouped PRE-AUTH / KEY-PROVEN / post-auth;
  `AGE_REFUSED_TRUNCATED` and `AGE_REFUSED_HEADER_MAC` added to the closed set;
  `AGE_REFUSALS_{PRE_AUTH,POST_AUTH,KEY_PROVEN}` exported so the consumer branches on sets it does
  not spell; `ARTIFACT-CORRUPT` gains a second message; `restore-verify.py`'s operator message now
  reads the classification; `SECRETS.md`'s recovery table pinned against the tuple both ways.
- **CARRIED FORWARD, unchanged — Workbench + laptop both applied and live**: 7 `mM:7d` rules,
  0 stale, on each. Workbench live since the **2026-09-06 18:01 reboot**, reclaim held at
  **67.2M inodes against the 96.1M baseline** (74% disk). Laptop live since **2026-09-07 17:54**
  via `preflight-tmp-churn-host.sh --init` (generation 247).
- **No clawgate task, again** — `clawgate_handoff.sh resolve` → **rc 5** with its positive control
  green (2 links for another session). A wrong id also answers 200 with an empty array, so this is
  not a clean bill of health and **no field is recorded**.

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

### RESOLVED — the missing space and inodes are in `/tmp`
- **Answer:** `/tmp` holds **78,501,285 entries / 469.1 GiB — 81% of this filesystem's inodes.** It is on the **root partition, not tmpfs**, so nothing clears it at boot. 173,346 top-level entries.
- **Full per-directory table (root run, naive counter — see the caveat below):**

  | path | entries | GiB | path | entries | GiB |
  |---|---:|---:|---|---:|---:|
  | `/tmp` | 78,501,285 | 469.1 | `/var` | 2,785,594 | 535.9 |
  | `/home` | 37,693,686 | 686.8 | `/root` | 1,103 | 26.2 |
  | `/nix` | 10,402,665 | 373.9 | `/etc` | 3,928 | 0.1 |

- **Independent cross-check — a COMPLETED unprivileged run, same day:** `/tmp` 77,750,210 / 467.0 GiB (28 denials) · `/home` 37,985,716 / 690.9 GiB (**0** denials) · `/nix` 10,541,985 / 376.2 GiB (2 denials) · `/var` 980 / 6.1 GiB (**32** denials) · `/root` 1 / 0.0 (1 denial). Agreement within ~1% wherever the tree was readable.
- 🔴 **The instrument defect is TWO mechanisms, and the permissions one is the SMALLER.** This corrects the session-3 analysis recorded above, which named only the first:
  - **(a) Silent permission denials** — explains `/var` (536 GiB read as 6.1) and `/root` (26.2 GiB read as 0). Real, and now measured: 32 and 1 denials.
  - **(b) TRUNCATED SCANS RECORDED AS TOTALS** — explains the doc's two biggest errors, and it is the larger. `/home` had **zero** denials and `/tmp` only 28, yet session 2 recorded `/home` ≈ 200 GB (actual 691) and `/tmp` = 193K entries (actual 77.75M). Nothing blocked those reads; the scans did not finish. Session 3 hit the same failure three times: a `find` over `/tmp` ran 1h00m without completing, and a scan piping through `sort` buffers all output so an empty file looks like a dead job when it is still running. **A per-directory count is not a result until the loop that produced it has printed its terminator.**
- **Ruled out — ext4 metadata as the missing space.** 30.2 GiB total, against the 902 GiB the session-2 doc attributed to it. `Inode size: 256` read from the superblock, so inode tables = 122,036,224 × 256 B = 29.1 GiB, block bitmaps = 488,115,343/8 = 0.06 GiB, journal = 1.0 GiB.
    via: measurement
- **Ruled out — k3s PVCs as the INODE sink.** 256 GB of bytes, but the largest PVC by inode count (`clickhouse-data`) holds only 101,338; the whole store is a rounding error against `/tmp`'s 78.5M. They remain a real BYTE consumer — this eliminates them as the answer to the inode question only.
    via: measurement
- **Ruled out — hidden data under the read-only `/nix/store` mount.** Session 2 disproved the mechanism (the device is `rw`, only the mount is `ro`); the per-directory numbers now confirm it independently, since `/nix` measures 10.4M entries against 102,474 store paths and 147 GB of unique `.links` data with no shortfall to hide.
    via: measurement
- **Two session-2 eliminations that were wrong the OTHER way:**
  - *"Not k3s PVCs (1.7GB total)"* — actually **256 GB**: `comfyui-models` 165G, `sglang-models` 48G, `clickhouse-data` 15G, `promptver-worker-model-cache` 14G, `whisparr-config` 6.6G.
  - `/var/lib/docker` — **57 GB / 1,640,840 inodes**, never measured at all. k3s uses containerd; whether docker is used here is unchecked.
- 🔴 **`/var/lib/kubelet` reports 176 GB but it is NOT additional data** — it bind-mounts the k3s local-path PVC directories, same device, so `-xdev` does not exclude it and that data is counted under both paths. Do not add 176 to 256.

### Instrument caveats that survive — read before quoting any byte figure above
- **Both runs used the NAIVE counter**, so every GiB and entry figure in the table is an **upper bound**: `find` visits every hardlink. `/nix` is the extreme case — 373.9 GiB counted against **147 GB of unique data** in `.links` (1,459,576 files, 102,474 store paths). Column sum 2092.1 GiB vs 1427.8 GiB actually used; the ~664 GiB excess is hardlinks plus the kubelet bind-mount.
- **RESIDUAL came out −32,329,967** — negative, i.e. over-counting, never hidden data. Cause confirmed as hardlink double-counting.
- **Fixed in `9ef89fa7`**, verified against `du -sx` on a fixture with one 100 KiB file hardlinked 4×: deduped **116 KiB / 5 entries**, `du -sx` **116 KiB**, naive **416 KiB / 8 entries**. A `dup-links` column now shows the correction per directory; **a zero there for `/nix` means the dedup is not running.**
- **Also fixed in `9ef89fa7`:** the denial guard died with `[: 0\n0: integer expected` (line 93) because `grep -c` prints `0` *and* exits 1, so `|| echo 0` emitted two lines. Harmless only because the count was genuinely zero — with real denials it would have suppressed the list. And section 6c printed nothing: `/home/*/.*` expands to `.` and `..`.
- **A re-run is needed for true byte figures.** Everything above is sound for *where* the mass is and useless for *exactly how much*.
- **The filesystem moved during this session — three readings, so quote one with its timestamp, never as "the" value:** session-2 `output.txt` = 384,774,395 blocks used (1467.8 GiB), 95,273,683 inodes · session-3 mid-session `df` = 1.4T used / 315G avail / 82%, **96,660,275** inodes · root run 13:01 −05:00 = 374,283,766 blocks (**1427.8 GiB**), **97,058,969** inodes. Inodes rose ~1.8M while bytes fell ~40 GiB — consistent with `/tmp` churn, and a reason not to treat any single `df` as the baseline for a cleanup claim.

### `/home` breakdown, and a section-6c defect that made it unreadable
- **`/home` on the ROOT filesystem is ~370 GB**, top entries: `workspace` 167G · `.ollama` 63G · `.local` 45G · `.cache` 36G · `.npm` 13G · `go` 13G · `hetzner-volumes` 6.4G · `.claude` 6.4G · `.nuget` 5.8G · `Downloads` 4.8G · `.var` 4.1G · `.config` 3.9G · `.rancher` 1.7G.
- 🔴 **The 12T and 1.2T lines in the run's section 6c are NOT root-filesystem usage.** `/home/zach/hdd-20tb` is `/dev/sda1` (xfs, 18.2T, dev 801) and `/home/zach/old-nix-hdd` is `/dev/sdc1` (xfs, 3.6T, dev 821); root is dev 10308 and 1.8T in total. Also foreign: `old-nix-ssd` (`/dev/sdb2`), `workspace/fast` (`/dev/nvme1n1p1`, 2.5T used), `workspace/nvme-2tb` (`/dev/nvme3n1p1`, 556G).
- **Cause:** `du -x` only stops du crossing AWAY from its starting point; when the starting point IS a foreign mount, du walks all of it. Fixed in `bd0f0c20` by comparing each candidate's device against `/` and listing foreign mounts separately. Controls: hdd-20tb / old-nix-hdd / old-nix-ssd excluded, `.ollama` kept, 94 root-fs entries retained.
    via: measurement
- **This also explains the ~3-hour runtime** — 13T of external disk was being walked for a `/home` breakdown. The re-run will be far quicker.
- **Useful cross-check that falls out of it:** `du` (dedups hardlinks) gives ~370 GB for `/home`'s top 15, while section 2's naive `find` gave 686.8 GiB. The ratio is consistent with the hardlink inflation already documented, and is independent evidence the `bd0f0c20`/`9ef89fa7` dedup was the right correction.

### 🔴 RETRACTION — "section 6c printed nothing" was false, and was diagnosed from truncated output
- **The claim, recorded above and in `9ef89fa7`:** section 6c printed nothing because `/home/*/.*` expands to `.` and `..`.
- **It is wrong.** The section had simply not run yet at the point the output was read; the old glob worked and produced the table above.
- **How it happened:** a partial paste of a still-running script was read as a complete result — *the same failure this doc already records as the session's primary lesson*, committed as a fix while writing that lesson down. The enumeration change is harmless and was kept; its stated reason was false, and the real 6c defect (cross-filesystem) was shared by both the old and new versions.
- **Generalisation worth carrying:** knowing the failure mode does not protect you from it. The defence is mechanical, not attentional — **never diagnose from output whose producer has not exited.** Check for the terminator, or the process, before reading a section as absent.
    via: measurement

### `/tmp` — the answer, still un-triaged
- 78,501,285 entries / 469 GiB, 81% of this filesystem's inodes, 173,346 top-level entries, on the ROOT partition not tmpfs so nothing clears it at boot.
- **Three independent runs agree on the readable trees and disagree wildly on the blocked one** — root vs two unprivileged: `/tmp` 78,501,285 / 78,021,477 / 77,750,210 · `/home` 37,693,686 / 37,942,259 / 37,985,716 · `/nix` 10,402,665 / 10,155,398 / 10,541,985 · **`/var` 2,785,594 / 21,400 / 980**.
- 🔴 **An unprivileged count of a permission-blocked tree is not merely a floor, it is an UNSTABLE floor** — two runs by the same user minutes apart differed 20× on `/var`. So it cannot be used as a consistent lower bound, nor compared across runs to infer growth, which is exactly what a capacity investigation reaches for.
    via: measurement

### UNMEASURED this run — deleted-but-open files
- Section 7 printed `count=-1`: the awk did `NR-1` to drop lsof's header, but with no output `NR` is 0. **That is not a zero and must not be read as one** — it also collapsed "lsof found nothing" with "lsof did not run".
- Fixed in `bd0f0c20`: a not-on-PATH branch printing COULD NOT MEASURE, an empty-output branch reporting 0 alongside lsof's exit code, and a count that cannot go negative. Controls: old code reproduces `-1` on empty input, new code gives 0, and two rows give `count=2 bytes=2.0 GiB`.
- **So session 2's `lsof +L1 = 0` elimination has NOT been re-confirmed as root.** It stands on session 2's evidence alone.

### Gate status — both tiers green on the MERGED tree, Tekton pending
- **Sandbox tier** (`nix build .#checks.x86_64-linux.<d>`, the tier Tekton runs, built ONE AT A TIME — a combined invocation produces false failures):
  - `pytests` → `RESULT: PASS (exit=0)`, `TOTAL collected=20364 passed=20361 skipped=3 failed=0` (floor 18404)
  - `nodetests` → `RESULT: PASS (exit=0)`, `TOTAL suites=5 files=41 tests=1449 pass=1449 fail=0` (floor 1367)
- **Dev-host tier** (`scripts/gate.sh`): same counts, PASS.
- Merged-tree base at gate time: `14b00c3f`. Main has since moved to `80625392`+; every mover touched only `claudedocs/*`, **disjoint** from this PR's four files, so the result still applies. Verified by file-set intersection, not by the merge exiting 0 — a clean `git merge` is not a clean merge.
- 🔴 **`nix build` reporting BUILD OK is a claim about the BUILD, not about tests running.** Read the counts. And nix prefixes every line `devrc-pytests> `, so a `grep '^RESULT:'` finds NOTHING on a passing run — that empty grep nearly got reported as "no verdict".
- **`gate.sh` first attempt reported `pytest RESULT: FAIL (exit=3)` — a MISSING ENVIRONMENT, not a code failure** (`logrotate` off PATH; the runner refuses rather than silently skipping). Fix is to run inside `nix develop <repo>`; `.envrc` is `use opencode`, so direnv alone never provides the gate toolchain. That same run printed `GATE_RC=0` beside `GATE: RESULT=FAIL exit=1` — the pipe ate the status.
    via: measurement

### The Tekton `devrc-pytests` failure on this PR is a KNOWN FLAKE — do not attribute it to this branch
- **Observed:** run `devrc-ci-sjxn5` on `0d606bac`, exactly one failure: `scripts/tests/test_subsystem_store_api.py::TestTheBackstopNeverSendsASecondResponse::test_an_exception_AFTER_the_response_sends_NO_second_response`. `TOTAL collected=20364 passed=20360 skipped=3 failed=1`.
- **Already documented in this repo:** `claudedocs/handoff-hook-interpreter-pinning.md:78` — *"Known flakes, do not attribute to a branch"* — and `claudedocs/handoff-find-session-live-first.md:338`, which records occurrence 5 (`devrc-ci-29tv4`) as **the same test class**, signature `AssertionError: the PUT sent a second response too: b''`. Third signature across five test classes, all the same in-process round-trip. Owned by `#863`.
- **Controls run before concluding:** this PR touches 4 files, none in that subsystem · the test passed **12/12** locally in isolation, with a `--collect-only` positive control showing 1 collected rather than 0 · the full sandbox derivation passed locally · a different recent run (`devrc-ci-x9rff`, sha `bf433490`) failed a DIFFERENT test in the SAME file.
    via: measurement
- **Next probe if it recurs:** read the gate pod log directly rather than the check state — `KUBECONFIG=$KC_HOMELAB kubectl logs -n tekton-ci <pipelinerun>-gate-pod --all-containers`, and match the PipelineRun to a sha via `.spec.params[?(@.name=="revision")]`.

### RECLAIM COMPLETE — measured before/after on the workbench
- `sudo systemd-tmpfiles --clean` with the ledger's rules, 2026-09-03:
  - inodes used **96,135,443 → 84,836,132** (−11,299,311) · inode use 79% → **70%**
  - available **308G → 406G** (+98 GB) · disk use 83% → **77%**
- 🔴 **It does not shrink the top-level directory count, by design.** `/tmp` top-level went 171,906 → 182,416. tmpfiles `e` cleans a matched directory's CONTENTS and never removes the directory itself, so **43,708 ledger-matched stubs remain** (`nix-develop-*` 21,092, `nix-shell.*` 19,973, `run3.*` 1,266, …) and accumulate every cycle. Closing that needs a different mechanism than `e`; not attempted.
    via: measurement
- `homelab-talos-prs-*` now matches **0 directories**, consistent with it having been a dead rule all along.

### 🔴 THE EVICTION FEATURE WAS DELETED — do not rebuild it
- **What it did:** removed an earlier revision's `m:7d` rule lines from `systemd.tmpfiles.rules` in `/etc/nixos`, as root, by regex.
- **Its premise was FALSE.** It defended against a stale line ABOVE the corrected one winning (systemd takes the first line per path). But insertion is `src.replace(anchor, anchor + block, 1)` — new rules always land at the TOP, above any survivor — so **this script cannot produce the ordering eviction defended against**. A surviving stale line is cruft plus one log line, not a dead rule. The measurement that justified eviction came from a hand-edited config, not from this code path.
    via: measurement
- **What it cost — two 🔴 regressions across three rounds, each introduced by the fix for the previous finding:**
  - an unanchored substring splice silently commented out an unrelated live `d /srv/critical` rule while leaving the stale one, then printed `evicted 1`, `inserted 7 of 7`, exited 0, and passed its own post-write verifier;
  - a whole-line scan for the closing bracket walked past a list closed as `"rule" ];` into a following `systemd.user.tmpfiles.rules` and deleted an entry there, reporting it as evicted "from systemd.tmpfiles.rules";
  - duplicate spans chopped `"d /srv/critical …"` to `t -"` — invalid Nix — while printing success.
- **Replaced by** `nix/system/patch-tmp-churn-stale-lines-2026-09-04.md`: two reviewable edits per host. Verified by applying both to a copy of the workbench's LIVE `/etc/nixos` — all five checks pass and the script is then a byte-identical no-op.
- 🔴 **The inserter is now APPEND-ONLY and must stay that way.** The reasoning is written into the script itself so it survives this doc.

### Live host state — the workbench IS applied, contrary to what the script long claimed
- `/etc/nixos/configuration.nix` carries 8 `mM:7d` rules, the stale `mtime-ONLY ageing (m:)` header sentence, and the withdrawn `homelab-talos-prs-*` line. The script asserted "the workbench is unapplied today" — **false**, and that assumption is what hid the header defect for two rounds.
- 🔴 **`/etc/tmpfiles.d/00-nixos.conf` carries NONE of them** — the config is edited but never activated, because `nixos-rebuild switch` is blocked by a `switchInhibitors` check on an unrelated `dbus -> broker` channel migration. Use `nixos-rebuild boot` + reboot. Do NOT use `NIXOS_NO_CHECK=1` on a box running k3s.
- **Withdrawing a glob from the repo ledger does not remove it from an applied host.** Nothing does; that is step 2 of the hand patch.
- **The laptop's `/etc/nixos` is UNMEASURED** from here. The patch doc carries the five checks to run there.
    via: measurement

### CLOSED — the whole effort, with the numbers
- Started from a handoff asserting the ~900 GB gap was **SOLVED: ext4 metadata overhead**. Refuted: ext4 metadata consumes BLOCKS, never USED INODES, so 96.3M used inodes were 96.3M real files. Static metadata computed from the superblock with `Inode size: 256` measured = **~30 GiB, not 902**.
- **The answer was `/tmp`**: 78,501,285 entries / 469 GiB, **81% of the filesystem's inodes**, on the root partition rather than tmpfs.
- **Two mechanisms produced the wrong answer, and the second is the larger:** (a) an unprivileged `find … 2>/dev/null` skipping every root-only tree — `/var` read 980 inodes / 6.1 GiB with 32 denials against 2,785,594 / 535.9 GiB as root; (b) **truncated scans recorded as totals** — `/home` had ZERO denials and was still recorded at ~200 GB against 691 actual.
    via: measurement
- **The bug in the fix:** `tmpfiles.d(5)` age-by is case-sensitive — lower-case selects the timestamp for FILES, upper-case for DIRECTORIES. `m:7d` therefore aged no directory at all: it emptied every matched tree and removed none of them, on a filesystem whose problem is inodes.
    via: measurement
- 🔴 **`e` cleans a matched directory's CONTENTS and never removes the directory itself.** After the reclaim, `/tmp` top-level went UP (171,906 → 182,416) and **43,708 ledger-matched stubs remain**. Closing that needs a different mechanism than `e`; not attempted.
    via: measurement

### 🔴 THE EVICTION FEATURE WAS DELETED — do not rebuild it
- It removed stale `m:7d` rule lines from `/etc/nixos` as root, by regex. Three adversarial audit rounds; removed rather than fixed.
- **Its premise was FALSE.** It defended against a stale line ABOVE the corrected one winning. But insertion is `src.replace(anchor, anchor + block, 1)` — new rules always land at the TOP — so **the script cannot produce the ordering eviction defended against**. A survivor is cruft plus one log line, not a dead rule.
    via: measurement
- **Cost: two 🔴 regressions in three rounds, each introduced by the fix for the previous finding** — an unanchored splice that silently commented out an unrelated live `d /srv/critical` rule while leaving the stale one; a whole-line bracket scan that walked into a following `systemd.user.tmpfiles.rules` and deleted an entry there. Both printed success and passed their own post-write verifier.
    via: measurement
- The inserter is now **APPEND-ONLY**, with that reasoning written into the script so it outlives this doc.

### Live host state — the workbench IS applied; the script long claimed otherwise
- `/etc/nixos/configuration.nix` carries 8 `mM:7d` rules, the stale `mtime-ONLY ageing (m:)` header sentence, and the withdrawn `homelab-talos-prs-*` line. **Withdrawing a glob from the repo ledger does not remove it from an applied host** — that is what rank 1 fixes.
- 🔴 **`nixos-rebuild switch` is BLOCKED** by a `switchInhibitors` pre-switch check on an unrelated `dbus-implementation : dbus -> broker` channel migration. Use `boot` + reboot. Do NOT use `NIXOS_NO_CHECK=1` on a k3s box. The printed rollback (`cp -a BAK CFG && nixos-rebuild switch`) is also unexecutable in that state — its own switch hits the same inhibitor.
    via: measurement
- **The laptop's `/etc/nixos` is UNMEASURED** from the workbench.
- ⚠ **`/tmp` has already regrown**: available 406G → 367G (79%) within hours of the reclaim. Nothing throttles it until the rules are live.

### Is the daily reap cadence sufficient under build load? — UNRESOLVED, sampler in flight
- **Symptom + exact repro:** the reap is daily (18:16 CDT); the question is whether `/tmp` can re-fill faster than that under real build load. Reproduce by sampling `df -i /`, `df -k /` and `ls -U /tmp | wc -l` on a fixed interval across a build.
- **Observed (with values):** sampler at `scratchpad/churn-sample.sh`, 5-minute interval:
  - `19:43:25Z` inodes **67,693,096** · avail **482,245,348 KB** · `/tmp` top-level **263,976**
  - `19:48:25Z` inodes **68,058,181** · avail **480,538,360 KB** · `/tmp` top-level **263,998**
  - ⇒ **+365,085 inodes and −1.63 GiB in 5 minutes** (~73k inodes/min).
  - 🔴 **`/tmp` top-level moved only +22 while inodes moved +365k** — the churn is INSIDE existing directories, not new top-level entries.
- **Ruled out — that this is idle baseline.** The samples were taken while this session's own subagent was running `nix build` and the full devrc suite, which is precisely the `nix-develop-*`/`nix-shell.*` generator. The figure is a PEAK-LOAD reading, not a baseline one.
    via: measurement
- **Ruled out — that the top-level stub count is what grows under load.** +22 entries against +365,085 inodes over the same interval.
    via: measurement
- **Leading hypothesis:** at ~73k inodes/min sustained, the 54.7M free inodes are ~12.5 hours of continuous building against a 24-hour reap cadence — so a heavy build day could approach the inode wall before the next reap. **UNCONFIRMED: this rests on two samples taken under atypical load.**
- **Next probe, verbatim:** read the completed curve and compare a build window against an idle one —
  ```bash
  cat /tmp/claude-1000/-home-zach-workspace-homelab-talos/c8eedffa-36a5-41b3-b250-99d5d2851435/scratchpad/churn.tsv
  ```
  Confirm the terminator line `SAMPLER-DONE` is present before reading it as complete — this doc's own primary lesson. If the scratchpad is gone, re-run a sampler across a known-idle hour; the two rates are the whole question.

### `df -i` and `--output` are mutually exclusive — a sampler that silently recorded an empty column
- **Symptom + exact repro:** `df -i --output=iused /` → `df: options -i and --output are mutually exclusive`, exit non-zero.
- **Observed (with values):** the first sampler build wrote `2026-09-07T19:42:34Z\t\t482957336\t263974` — a well-formed TSV row with an **empty** inode field, for an hour, with no error surfaced because the failure went to a discarded stderr.
- **Ruled out — that the column was zero.** It was empty, not `0`; the command never produced a value at all.
    via: measurement
- **Fix:** parse both reads positionally from plain `df` (`df -i / | awk 'NR==2{print $3}'`), and run a **positive control** printing both values before trusting an hour of collection.
- **Generalisation:** this is the same family as this doc's `NR-1` → −1 and `grep -c` → `"0\n0"` findings — an instrument recording a field it never measured, failing toward a value that reads as data.

### ANSWERED — the churn curve ran to completion, and it INVERTS this effort's framing
- **Symptom + exact repro:** whether the daily 18:16 CDT reap can outpace `/tmp` regrowth. Sampled `df -i /`, `df -k /` and `ls -U /tmp | wc -l` every 5 minutes for 55 minutes, 12 points, terminator line present (`SAMPLER-DONE`) so the run is readable as complete rather than truncated.
- **Observed (with values), 2026-09-07 19:43:25Z → 20:38:26Z:**

  | metric | start | end | delta | extrapolated | headroom | time to wall |
  |---|---:|---:|---:|---:|---:|---:|
  | inodes used | 67,693,096 | 68,404,278 | +711,182 | **+18.6M/day** | 54.7M free | ~2.9 days |
  | avail (KB) | 482,245,348 | 460,816,188 | −21,429,160 | **−490 GiB/day** | 439 GiB | **<1 day** |
  | `/tmp` top-level | 263,976 | 264,281 | +305 | **+8k/day** | — | — |

  - The inode series **OSCILLATES** — peak 68,495,522 at 20:08, trough 67,343,314 at 19:53, i.e. a 1.15M swing inside the window. Net drift is upward but a single interval is worthless: an early two-point read of this same series gave `+365,085 in 5 min` (≈105M/day) and the very next point was **−714,867**. Transient build churn is released, not accumulated.
  - The **avail** series is the near-monotonic one. That is the signal to watch, not inodes.
- 🔴 **The finding that matters: under load, BYTES are ~3x tighter than INODES — the opposite of this effort's premise.** The whole investigation was framed as an inode problem, correctly, for the ACCUMULATED state (`/tmp` held 81% of this filesystem's inodes). For the RATE the ranking inverts: the daily reap comfortably covers inodes (it cleared ~17M in one pass against +18.6M/day), while the byte side has under a day of margin at this rate. **A capacity alarm built on `df -i` alone would not have seen this.**
- **Ruled out — that the daily cadence is insufficient for INODES.** +18.6M/day against a reap measured at ~17M/pass plus `nix-gc` deleting 11,263 store paths / 29.1 GiB nightly. Roughly balanced, with ~2.9 days of headroom as the buffer.
    via: measurement
- **Ruled out — that the top-level stub count is a growth driver.** +305 entries in 55 minutes (~8k/day) against +711,182 inodes over the same window. This independently confirms rank 6 as non-urgent.
    via: measurement
- 🔴 **SCOPE, stated because the number is meaningless without it: the entire window ran under a heavy build.** This session's own subagent ran for 3,764 s — essentially exactly this window — executing a full `nix build` of both sandbox checks plus a 22,003-test suite. **Every rate above is a PEAK-LOAD figure and none of them is a baseline.** Two earlier readings of this same series were wrong in opposite directions (one extrapolated a spike as a trend, one read the rebound as a refutation); this one is right only because it names the load it was taken under.
- **Next probe, if anyone wants the baseline:** re-run the same 12-point sampler across a known-idle hour and compare. The two rates are the whole remaining question, and nothing else about this effort depends on the answer.
  ```bash
  for i in $(seq 1 12); do
    printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$(df -i / | awk 'NR==2{print $3}')" \
      "$(df -k / | awk 'NR==2{print $4}')" "$(ls -U /tmp | wc -l)"
    [ "$i" -lt 12 ] && sleep 300
  done; echo SAMPLER-DONE
  ```

### `#1370` merge is gated on a Tekton run that had not settled at handoff time
- **Symptom + exact repro:** `gh pr checks 1370 --repo innovation-upstream/devrc` → both checks `pending` as of this handoff. The branch is `feat/preflight-tmp-churn-host` @ `2fe861d4`.
- **Observed (with values):** on the merged tree (this branch + current main), `scripts/tests` collects **12977, passes 12977, fails 0** — measured locally via `nix develop /home/zach/workspace/devrc -c bash scripts/run-tests.sh --targets scripts/tests`. The 7 age/opencode failures that were red on this branch an hour earlier are **gone**, because `#1392` landed on main.
- **Ruled out — that #1370's earlier red was its own.** Its three files (`nix/system/preflight-tmp-churn-host.sh`, `scripts/run-tests.sh`, `scripts/tests/test_preflight_tmp_churn_host.py`) have **zero overlap** with the failing files, and the failures were the declared drift set.
    via: measurement
- **Next probe, verbatim:**
  ```bash
  gh pr checks 1370 --repo innovation-upstream/devrc
  gh pr merge 1370 --repo innovation-upstream/devrc --squash --delete-branch
  # then verify by CONTENT, never ancestry — a squash is never an ancestor of its base:
  git -C /home/zach/workspace/devrc fetch origin main -q
  git -C /home/zach/workspace/devrc cat-file -e origin/main:scripts/tests/test_preflight_tmp_churn_host.py && echo present
  ```

### `apply-tmp-churn-retention.sh`'s documented rollback is a no-op on a symlinked config — OPEN, filed not fixed
- **Symptom + exact repro:** `nix/system/apply-tmp-churn-retention.sh:276` does `cp -a "$CFG" "$BAK"`. `cp -a` implies `-d`, so when `/etc/nixos/configuration.nix` is a symlink — the standard layout for anyone keeping their NixOS config in a git repo — the "backup" is a symlink to the file about to be edited, and the documented rollback restores nothing.
- **Observed (with values):** measured end to end on the sibling script during the `#1370` audit — config left edited-and-broken with **no recoverable copy anywhere**, and the error message directing the operator to a backup pointing at the corruption.
- **Ruled out — that this is only a documentation defect.** It is live on that script's `--apply` path, not just its footer text.
    via: measurement
- **Next probe:** change it to `cp -aL` (or refuse on `[[ -L "$CFG" ]]`) plus a fixture test asserting the backup is not a symlink. `#1370` mitigated the reachable path from its own side and deliberately did not touch that file.

### Tekton `devrc-pytests` is RED on #1409 and it is NOT that branch
- **Symptom + exact repro:** `gh pr checks 1409 --repo innovation-upstream/devrc` → `tekton/devrc-pytests fail`, `FAILING: TestARefusedWriteIsIndistinguishableFromAnAbsentOne.test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_dif…`.
- **Observed (with values):** the failing class lives in `scripts/tests/test_subsystem_store_api.py` — the same file this doc already records as a known flake owned by `#863` ("third signature across five test classes, all the same in-process round-trip"). This is a fourth class name in that family. `tekton/devrc-nodetests` passes: `suites=5 files=41 tests=1449 pass=1449 fail=0`.
- **Ruled out — that `#1409` causes it.** That PR touches five files, none in that subsystem, and every in-tree referrer of `restore-verify` lives under `scripts/tests`.
    via: command
- **Leading hypothesis:** the `#863` in-process round-trip flake recurring; `claudedocs/handoff-devrc-ci-red` (PR #1389) separately claims devrc-ci pytests is red on `main` itself.
- **Next probe:** read the gate pod log rather than the check state — `KUBECONFIG=$KC_HOMELAB kubectl logs -n tekton-ci <pipelinerun>-gate-pod --all-containers`.

### The full gate has not been run against `#1409` round 5 — saturation, not a red
- **Symptom + exact repro:** `nix develop /home/zach/workspace/devrc-age-markers -c bash scripts/gate.sh --tier both` — twice SIGTERM'd at its own 3600 s cap: `FAIL pytest exit=124 (timeout after 3600s)` / `RESULT: FAIL (exit=143)`.
- **Observed (with values):** load 60–98 all session with 6+ concurrent gates from other sessions. Round 3's run reached 24 of 29 targets; round 4's reached 8. `scripts/tests` inflated **22:51 → 37:08 → 53:19** across runs — the whole target, which is the load signature rather than one assertion. Round 4's run showed 3 failed in `scripts/tests`, ALL three `subprocess.TimeoutExpired: … timed out after 120 seconds` on a NESTED `run-tests.sh` (`test_a_pinned_skip_whose_TARGET_did_not_run_does_not_count`, `test_a_partial_run_is_declared_where_gate_sh_actually_LOOKS`, `test_the_subset_note_reports_N_of_the_FULL_set_not_N_of_N`). All three pass in isolation: `3 passed in 200s`.
- **Ruled out — that those 3 are a code failure.** The exception is a nested-run timeout, not an assertion, and they pass alone.
    via: measurement
- **Ruled out — that round 5 is unmeasured.** The two owning suites are 450 passed on both age binaries; rounds 3 and 4 between them covered all 29 pytest targets (round 3: 24 in-gate + 6 separately; round 4: 8 in-gate + 21 separately at 6161 passed / 2 skipped / 0 failed).
    via: measurement
- **Next probe, verbatim** — run when `cat /proc/loadavg` is under ~20:
  ```bash
  cd /home/zach/workspace/devrc-age-markers && nix develop . -c bash scripts/gate.sh --tier both
  ```

### The merged tree is UNGATED — Tekton gated the BRANCH, and the base moved 48 commits
- **Symptom + exact repro:** `gh pr checks 1409` is green, but Tekton's `revision` param is
  `e1536984` — the PR head, not `origin/main` + the PR. RULES: *"Gate on the MERGED tree, not the
  PR branch."* Reproduce with
  `KUBECONFIG=$KC_HOMELAB kubectl get pipelinerun -n tekton-ci devrc-ci-mkpb5 -o json | python3 -c 'import json,sys;print({p["name"]:p.get("value") for p in json.load(sys.stdin)["spec"]["params"]})'`.
- **Observed (with values):** merge-base `01956bf0`; `origin/main` `176f412b`; **48 commits** on
  main since. **Two of the PR's five files were edited on main, on the same subject:**
  - `SECRETS.md` ← `03d7e0ad` (#1398) *"age 1.3.2 removed the age-keygen input echo"* and `9300f234` (#1406)
  - `scripts/tests/test_analyze_service_index_escrow_verify.py` ← `4f49f5dc` (#1403) *"three claims #1392 left in the tree were false or stale"*
  - Both auto-merged by `ort` with no conflict. Inspected: main's correction survives
    (`age ≤ 1.3.1` present in merged `SECRETS.md`; `c45ccfd2` present in the merged test), and
    #1409's additions survive (`AGE_REFUSED_HEADER_MAC`, `AGE_REFUSED_TRUNCATED`,
    `AGE_REFUSALS_{PRE_AUTH,POST_AUTH,KEY_PROVEN}` all present).
- **Ruled out — that the merge re-introduced the FALSE sentence main retracted.** The string
  `login shell v1.3.1, dev shell v1.3.2` counts **1 on `origin/main` AND 1 on `e1536984`** in that
  file (plus 1 in `test_analyze_service_index_backup.py` on both). Merged line 5087 reads
  `…(login shell v1.3.1, dev shell v1.3.2)". They` — main's own retraction **quoting the sentence
  it replaces**, exactly as `4f49f5dc`'s message says it does. The merge introduced nothing.
    via: measurement
- **Ruled out — that the stale drift floor was carried forward a THIRD time.** This doc records
  `run-tests.sh`'s `"scripts/tests|N"` being merged wrongly twice. Here the PR does **not** touch
  `run-tests.sh`, so the merge takes main's value. Anchored counts: `origin/main` → `13026`
  (1 candidate, line 1737); PR head → `12927` (line 1698); merged tree takes **13026**.
    via: measurement
- **Ruled out — a disjoint-file break via a main-side CALLER of the changed scripts.** 24 in-tree
  referrers of `escrow-verify`/`restore-verify`; intersected against main's 48-commit file set →
  **9 hits**, of which the code/test ones are `test_analyze_service_index_backup.py`,
  `test_analyze_service_index_escrow_verify.py`, `test_store_root_ledger.py`,
  `test_subsystem_touch.py`, `run-tests.sh`. All are inside the in-flight run's target set.
    via: measurement
- **Leading hypothesis:** the merged tree is green — main's 48 commits were each gated on merge and
  the overlap is doc/test text rather than call-signature change. **UNCONFIRMED: the run has not finished.**
- **Next probe, verbatim** — confirm the terminator BEFORE reading it as complete:
  ```bash
  O=/tmp/claude-1000/-home-zach-workspace-homelab-talos/f61a6666-fa21-45c9-b691-838f7bee60c2/scratchpad/merged-overlap.txt
  grep -c OVERLAP-RUN-DONE "$O"   # must be 1
  grep -E '^### TARGET|passed|failed|error' "$O"
  ```
  If the scratchpad is gone, re-create the merged worktree and re-run
  `scratchpad/merged-overlap-gate.sh` (its target list is derived, not guessed — see the
  referrer intersection above).

### `main` retracted the premise behind this doc's own "verify on BOTH age binaries" step
- **Symptom + exact repro:** this doc's `How to verify` step 3 says *"the gate tier is v1.3.2;
  production resolves v1.3.1"* and prescribes symlinking the login-shell `age` to test v1.3.1.
- **Observed (with values):** `4f49f5dc` (#1403, merged to main 2026-09-08) states, measured on
  both hosts: non-interactive zsh, login zsh, login bash **and** the flake devShell all resolve
  **age v1.3.2**; nothing puts v1.3.1 on a PATH (v1.3.1 derivations remain in the store, unreferenced).
  It explicitly retracts *"Both versions are installed on this host today (login shell v1.3.1,
  dev shell v1.3.2)"* as FALSE in three places.
- **Ruled out — that this invalidates `#1409`.** Its audit measured the three re-keyed marker
  strings **byte-identical on v1.3.1 and v1.3.2**, so the classifier does not depend on which
  binary answers.
    via: doc
- **Consequence, not yet applied:** `How to verify` step 3's v1.3.1 half now describes a
  configuration that does not exist on these hosts. It is corrected in this update's
  `How to verify`; the *reason* the redaction guard stays is unchanged and stronger — `4f49f5dc`
  measured that v1.3.2 still echoes on the **recipient** path (`unknown recipient type: %q`).
- **Next probe:** `readlink -f $(command -v age); age --version` in a LOGIN shell on each host,
  before anyone re-derives the two-binary story.

## Next steps (ranked)
🔴 **Ranks 1–14 keep their original meaning and numbering** — the rank is half a `claim-work`
slug's identity, so renumbering silently re-points live claims. **Ranks 1–7, 9, 11 and 13 are
closed; 8, 10, 12, 14 remain; 15 is PART-DONE and its remainder is now rank 16.**

1. **DONE — workbench patched and verified live.**
   forcing: none
2. **DONE — laptop applied and verified live 2026-09-07.**
   forcing: none
3. **DONE — rebuild/reboot**; `nixos-rebuild switch` unblocked on both hosts.
   forcing: none
4. **ANSWERED — `/var/lib/docker` is live and in use.**
   forcing: none
5. **DONE — `#1366` MERGED** (`ffac18f8`). Claim released.
   forcing: none
6. **WON'T-FIX — the `/tmp` directory stubs.** 52,049 = 0.08% of inodes, ~8k/day.
   forcing: none
7. **ANSWERED — the churn curve ran.** Daily cadence holds for inodes; bytes are tighter under load.
   forcing: none
8. **Prune the `devrc/diagnose-disk-accounting` index entry** — a stale `OPEN:` bullet sits beside
   its own `RESOLVED`. Partly done: `/mnt/rootcheck` recorded as no longer mounted.
   forcing: none
9. **DONE — `#1366` audited (4 rounds), `#1370` audited (1 round).**
   forcing: none
10. **Confirm the laptop's first reap actually happened** — timer armed for 2026-09-07 18:54 CDT,
    its `NextElapseUSecRealtime` read EMPTY, no session has checked.
    `ssh zach@192.168.50.155 'systemctl status systemd-tmpfiles-clean.service; df -i /'` — expect
    `status=0/SUCCESS` and inodes below the **24,749,866 / 117,236-top-level** baseline.
    forcing: none
11. **DONE — `#1370` MERGED** (`b29cde5e`), Tekton green, verified by content.
    forcing: none
12. **Fix `apply-tmp-churn-retention.sh:276` (`cp -a` → `cp -aL`)** — its documented rollback is a
    no-op on a symlinked config. Repo `innovation-upstream/devrc`, one file plus a fixture test
    asserting the backup is not a symlink. Closing condition: that PR merged.
    forcing: none
13. **DONE — `#1392` audited.** Fix is `#1409`; ladder closed on the attribution criterion.
    forcing: none
14. **Delete the stale `/tmp/disk-accounting-*` leftovers** — 19 seen, 2 non-empty (84 B / 203 B).
    Resolve by ownership and mtime, never a blanket `rm`, and not while a battery may be running.
    forcing: none
15. **PART-DONE — the Tekton half is CLOSED, the gate half is NOT.** `devrc-ci-mkpb5` re-ran the
    unchanged head `e1536984` and both legs pass (21143 collected / 21141 passed / 0 failed;
    1449/1449), so the red was a flake and `mergeStateStatus` is `CLEAN`. **`#1409` is still OPEN
    and unmerged.** Remainder is rank 16.
    forcing: none
16. **Finish rank 15: read the merged-tree overlap run, then merge `#1409` and release TWO claims.**
    Repo `innovation-upstream/devrc`, branch `fix/age-refusal-markers-post-header` @ `e1536984`.
    **IN FLIGHT: innovation-upstream/devrc#1409.** Order: (a) confirm `OVERLAP-RUN-DONE` in
    `scratchpad/merged-overlap.txt` and that all 8 targets show `rc=0` — if the scratchpad is gone,
    re-run it; (b) if the host is ever quiet (`cat /proc/loadavg` under ~20), run the real full gate
    `nix develop /home/zach/workspace/devrc-merge-1409-f61a6666 -c bash scripts/gate.sh --tier both`
    on the **merged** worktree, not the branch; (c) `gh pr merge 1409 --repo innovation-upstream/devrc --squash --delete-branch`;
    (d) verify BY CONTENT, never ancestry:
    `git -C /home/zach/workspace/devrc fetch origin main -q && git -C /home/zach/workspace/devrc grep -c AGE_REFUSED_HEADER_MAC origin/main -- '*.py'` → non-zero;
    (e) `claim-work --release nix-disk-cleanup-15` **and** `claim-work --release nix-disk-cleanup-13`;
    (f) `git -C /home/zach/workspace/devrc worktree remove /home/zach/workspace/devrc-merge-1409-f61a6666`.
    Closing condition: `#1409` merged and the content grep is non-zero.
    forcing: gate — the merged tree is ungated; Tekton gated the branch only, and the base moved 48 commits.

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

- 🔴 **A truncated scan reported as a total is what produced this whole false diagnosis** — a bigger factor than the permission denials. `find` over `/tmp` here takes **over an hour**. A loop that pipes through `sort` prints nothing until it finishes, so "empty output file" and "dead job" are indistinguishable — three scans were declared dead in session 3 while still running, and two of them later completed with correct data. Write per-item results incrementally, end with an explicit terminator line, and never read a partial file as a result.
- **`find` does not dedupe hardlinks; `du` does** (within one invocation). For allocated bytes use `du -sx`, or dedupe by inode as `9ef89fa7` does. `du` across two invocations does not dedupe between them.
- **Bind mounts of the same device defeat `-xdev`.** `/var/lib/kubelet` and `/mnt/rootcheck` both re-expose data already counted elsewhere on `/dev/nvme0n1p2`.
- **`grep -c` prints `0` and exits `1`** on no match, so `$(grep -c … || echo 0)` yields a two-line `"0\n0"` that breaks every later integer test — a guard that fails precisely when it has something to report.
- **kubelet `stats/summary` is useless per-PVC on local-path volumes** — a local-path volume is a plain directory, so every volume reports the whole filesystem's figures. Its node-level `fs.usedBytes`/`inodesUsed` are good, and were the first independent confirmation here.

- 🔴 **`du -x` does not mean "only this filesystem".** It stops du crossing AWAY from its starting point; started ON a foreign mount it walks the whole mount. To restrict to one filesystem, compare `stat -c '%D'` against the target device per candidate.
- 🔴 **Knowing a failure mode does not protect you from it.** This session wrote "a truncated scan reported as a total is what produced this whole false diagnosis" into the doc, and then committed a fix for a non-existent defect diagnosed from truncated output. The defence has to be mechanical — check the producer has exited before reading its output as complete.
- **`NR-1` to strip a header yields −1 on empty input.** Any "count" that can go negative is hiding the difference between "nothing found" and "did not run", and the zero is usually the reassuring reading.
- **This host has 6 non-root filesystems mounted under `/home`** (`sda1`, `sdb2`, `sdc1`, `nvme1n1p1`, `nvme3n1p1` plus root) — any `/home` figure must say which filesystem it is about.

- 🔴 **I restructured a script without checking whether it had tests.** `scripts/tests/test_tmp_churn_retention.py` was already in the branch, one `grep -rl` away, and the gate caught 10 failures I caused. **RETRACTION:** commit `94d8a1e5` claimed "nothing tied [the two rule lists] together" — false; `test_the_shell_verification_ledger_equals_the_python_rule_ledger` compared them as sets. The consolidation is still worth having (structural beats test-enforced) but it closed no unguarded gap.
- 🔴 **Knowing a failure mode does not prevent it — FOUR instances in one session, every one with a written rule.** (a) diagnosed a "section printed nothing" defect from output pasted mid-stream, while writing that exact lesson into this doc; (b) `2>/dev/null | grep -c` returned a clean 0 for a rule removing 1,066 entries, because the output is on stderr; (c) a wait loop `while pgrep -f "run-tests.sh"` matched its OWN command line and hung forever — the rule names almost this exact example; (d) a check-watcher matched `*pending*` against `PENDING` and declared the gate settled while both checks ran. **All four failed TOWARD a reassuring answer, which is why none announced itself.** The defence is structural, not attentional.
- **`pgrep -f 'nix build'` matched ANOTHER SESSION's build** on `devrc-merged`. Killing by pattern would have killed a sibling agent's work. Resolve PIDs and confirm each `/proc/<pid>/cmdline` carries your own session id first.
- **A concurrent `nix build` of the same derivation is a contention risk**: a green under contention is trustworthy, a red is not until re-checked alone.

- 🔴 **A fix round introduces the next defect — measured three times running here.** Rounds 1→2→3 each shipped a regression created by the previous round's fix, in root-privileged code, every one reporting success. The ladder is what caught them; a two-round cap would have shipped the splice bug.
- 🔴 **Hand-verification is not coverage.** Two round-3 mutants SURVIVED the first sweep because I had checked those behaviours by hand with fixtures and written no tests. The tests came second and the mutants then died.
- 🔴 **A mutation that fails to apply scores as SURVIVED.** One `sed`-built mutant never matched; the "survivor" was never tested. Assert the text actually changed before believing a survivor.
- 🔴 **`tmpfiles` `e` acts on a DIRECTORY's contents and silently ignores a plain file** — a glob whose matches are files is a dead rule that reaps nothing and reports nothing.
- **A guard keyed on prose dies when the prose is reworded.** The anti-duplication guard tested the header's first line; rewording the header made it read "absent" and prepend a second header. Key on a stable constant.
- **`e` never removes the matched directory itself** — the reclaim empties trees and leaves the skeleton.

- 🔴 **A fix round introduces the next defect — three consecutive times here**, all in root-privileged code, every one reporting success and passing its own verifier. The audit ladder caught all three; a two-round cap would have shipped the splice bug.
- 🔴 **SIX times this session an instrument reported a result it had not measured.** Truncated output read as complete; a `2>/dev/null` zero read as clean when the tool writes to stderr; `pgrep -f` matching its own shell in a wait loop (hangs forever) and matching a SIBLING session's `nix build`; a `*pending*` case-fold miss declaring the gate settled while both checks ran; a mutation whose sed never matched scoring as SURVIVED; and a `--rebuild` flag error that looked exactly like a test failure. Direction varied — the constant is reading a verdict without confirming the instrument did any work.
- 🔴 **Hand-verification is not coverage.** Two round-3 mutants survived because the behaviour had been checked by hand with fixtures and no test written. The tests came second; the mutants then died.
- 🔴 **`tmpfiles` `e` ignores plain files silently** — a glob whose matches are files is a dead rule that reaps nothing and reports nothing. `homelab-talos-prs-*` was one for its whole life; the coverage table hid it by labelling a file count "dirs".
- **A guard keyed on prose dies when the prose is reworded** — the anti-duplication guard tested the header's first line, so rewording it made the guard read "absent" and prepend a second header.
- **`du -x` does not mean "only this filesystem"** — started ON a foreign mount it walks the whole mount. Compare `stat -c '%D'` per candidate.
- **`/tmp/*` is a latent E2BIG abort here** — 171,906 entries = 4.5 MB argv against ARG_MAX 2,097,152; under `set -euo pipefail` it kills the script mid-run with the message swallowed.
- **`main` is protected in NAME ONLY** (`required_status_checks` absent, `enforce_admins: false`, deliberate) — your own gate run is the only gate, so name the tier and base sha in any claim.
- **Tekton posts `error` = "KILLED: the gate pod died … Not a code failure"** — infrastructure, not your diff.

- 🔴 **`nixos-rebuild switch` is NO LONGER BLOCKED — this doc's "use `boot` + reboot" workaround is obsolete on BOTH hosts.** A switch succeeded on the workbench 2026-09-07 13:24 CDT: `/run/current-system` and `/nix/var/nix/profiles/system` (→ `system-389-link`) both moved while `/run/booted-system` stayed at the 09-06 18:01 boot. The journal shows `nixos-rebuild-switch-to-configuration.service ... Deactivated successfully` in 7.05s with **no `NIXOS_NO_CHECK`** and **no `switchInhibitors` message**, and **k3s was never restarted** (`ActiveEnterTimestamp` still `2026-09-06 18:02:20`).
  **Mechanism, and it is the satisfying one:** the blocker was a `switchInhibitors` check on the *pending* `dbus → broker` channel migration. `systemctl show dbus -p FragmentPath` now returns `/etc/systemd/system/dbus-broker.service` on **both** hosts — the reboot COMPLETED the migration, which removed the inhibitor. The laptop is also clear (`booted == current`, dbus-broker), so it can `switch` without a reboot too.
- 🔴 **The patch script's own advice is WRONG on the laptop.** It prints *"(or run with `--dry-run`, which needs no privileges)"*, but `--dry-run` still reads `$CFG`, and the two hosts differ: workbench `/etc/nixos/configuration.nix` is `-rw-r--r--`, the **laptop's is `-rw-------` root-only**. So on the laptop the dry run needs `sudo` too. Worth fixing in the script's message — a "needs no privileges" claim that is host-dependent is the same shape as the rest of this investigation's instrument defects.
- **The laptop was NEVER patched — not "patched but unrebuilt".** `/etc/nixos/configuration.nix` mtime is **Aug 24**; the tmpfiles work is from Sep 3–4, and `systemd-tmpfiles --cat-config | grep -c 'mM:7d'` there is **0**. That settles the ambiguity this doc left open. The laptop is also under no pressure: **56% disk, 23% inodes**, though `/tmp` IS on its root filesystem (116,968 top-level entries), so it has the same problem shape at smaller scale.
- **The unruled `/tmp` prefixes are real but NEGLIGIBLE — measured, so nobody builds a rule set for them.** ~212k of the 264,614 top-level entries match no ledger rule (`tmp*` 76k, `cgparent-` 21k, `apk-retry-` 16k, `fx-excerpt-` 15k, `dockerfile-` 9k, `cbf-` 9k, `gh-status-response.` 8k, `devrc-report-` 7k, `bap-` 4k, `resume-handoff-` 1.4k, `devrc-marker-` 2k). Sampling 40 subtrees per prefix and extrapolating: the **whole uncovered set is ~0.7M inodes / ~3 GiB** — ~1% of inodes. Most are stubs holding 1–6 inodes each.
- **`homelab-talos-prs-*`: the withdrawal was right AND the entries are real.** This doc says the glob "now matches 0 directories" — true, and still true. There are **2,358 `homelab-talos-prs-*` REGULAR FILES** in `/tmp` today (1.1–1.3 KB each, oldest 11 days). That is exactly the `e`-ignores-plain-files mechanism this doc records: the rule was dead for its whole life, withdrawing it changed nothing, and nothing reaps the files.
- **`/mnt/rootcheck` is no longer mounted** (`findmnt` → not mounted). The reboot took it. That closes one of the two `OPEN:` bullets on the `devrc/diagnose-disk-accounting` index entry; the `/var/lib/kubelet` bind-mount double-count is unaffected and still stands.
- 🔴 **zsh does not word-split, and it cost a silent wrong answer here.** `for f in $s` over a multi-word capture loops ONCE on the whole string, so a per-file `stat` loop printed `ERR` for every prefix and looked like a permissions problem. `while read -r f` gave the right answer immediately. This repo's RULES name this exact trap; knowing it did not prevent it — the sixth instance of that pattern in this effort.
- **`pgrep -f` matched this session's own shell**, as the RULES predict, when checking whether the sampler was alive. The sampler's real pid was distinguishable only by reading the command lines. Never let such a pattern reach `pkill`.
- **Coordination, checked and clean:** PR `innovation-upstream/devrc#1361` and claim `nebula-pre-departure-hardening-2` also target the laptop's nix config, so it was worth checking for collision. `#1361` touches only repo-tracked `nix/system/apply-nebula-*.sh` — **no shared file** with the tmpfiles patch. Host-level ordering on the laptop is still worth sequencing, but there is no merge hazard.

- 🔴 **Rank 5 SHIPPED as `innovation-upstream/devrc#1366`, and building it found FOUR defects this doc never named — including a shipped fix that was INERT.** Independently re-verified in a separate worktree off the branch, not taken on the subagent's report: suite 71 assertions green, and the 19-mutant battery reproduced **19/19 KILLED by their own guard**, baseline 71 ok / 0 FAIL, negative control **red at 6 FAIL**, and a behaviour-free `comment-reword` control correctly killing nothing. Both controls watched, which is what makes the battery evidence rather than a claim.
  - 🔴 **`LSOF_OUT=$(lsof +L1 2>/dev/null); LSOF_RC=$?` is `set -e`-FATAL, so the fix recorded in this doc for the `count=-1` defect could NEVER EXECUTE.** An assignment from a command substitution is a *checked* command, and `lsof` exits 1 when it finds nothing — the ordinary case. The no-rows message written specifically to stop a zero being read as a measurement was unreachable, and sections 7–8 never printed at all. `|| rc=$?` fixes it. **This is the same family as this doc's own `NR-1` → −1 finding, shipped by the fix for it.**
  - 🔴 **A THIRD abort route this doc's list of eight never names: `<producer> | sort | head -N` under `pipefail` is SIZE-DEPENDENT.** Measured: **40 entries survive rc 0, 20,000 die rc 141**, silently. Real `/tmp` had 171,886 at the time. Nine sites. Fixed with `head_n() { awk -v n="$1" 'NR<=n'; }` — read to EOF instead of closing the pipe.
  - **A second E2BIG instance:** `du -sh --exclude=/mnt /var/lib/rancher/k3s/storage/*` in section 5.
  - **The `xargs -I{}` injection mechanism is NOT what the index entry implies.** GNU `xargs -I` **strips double quotes from its input**, so with `"{}"` the `;` never becomes a statement separator — the leak is **command substitution** (`$(id -un)` expanding inside the surviving quotes) and it lands on **stdout, in the printed path**, not stderr. Unquoted `{}` gives the full statement injection on stderr. Arbitrary root command execution either way, and the "grep for the EXPANSION, never the word" rule is doubly load-bearing because the expansion appears in the stdout path too.
  - **Two `set -e`-fatal `dumpe2fs` lines are REPORTED, NOT FIXED** — they execute only under root, and a change to them cannot be verified from here. Sections 1, 2, 3, 5, 6, 6b and 8 remain unguarded for the same reason; the suite header says so rather than letting a reader assume coverage it does not have.
- 🔴 **The subagent found a race in its OWN harness, which is worth more than any single guard.** `printf "$CODE" | grep -q` lets `grep -q` win and SIGPIPE the `printf`, so under `pipefail` a SUCCESSFUL MATCH is reported as "not found" — the same unmodified tree gave 71 ok on one run and a spurious `FAIL: … the script no longer contains …` on the next. Both scans now read from a file. **A mutation battery whose scanner is nondeterministic scores mutants at random**; validate the instrument before reading its verdict.
- **Four mutants first scored `WRONG-KILLER` and were FIXED, not waved through** — the battery had named each guard's *pass* wording as the killer rather than its *fail* wording. That is the "a mutant killed by a different guard's error is not a kill" rule catching a real scoring bug.

- **CARRIED FORWARD from the status header, because these are measurements and it is not.** The workbench reclaim, with its numbers and its attribution caveat: operator ran the patch + `nixos-rebuild` + reboot 2026-09-06, boot at **18:01** (`who -b`); all five of this doc's own `How to verify` checks passed at **2026-09-07T19:27Z** and again after the 13:24 CDT switch; `systemd-tmpfiles-clean.service` ran **2026-09-06 18:16→20:11 exit 0**, costing 1h54m wall / 9m28s CPU / **64 GB memory peak** / 103.4 GB read / 24.2 GB written on a node also running k3s. Movement since the post-reclaim reading: inodes **84.8M → 67.2M**, available **367G → 462G**, **79% → 74%**.
  ⚠ 🔴 **That improvement is NOT cleanly attributable to tmpfiles alone, and must never be quoted as a tmpfiles figure.** A rival mechanism fired in the same window: `nix-gc.service` at 2026-09-07 00:02 deleted **11,263 store paths / 29.1 GiB**, followed by `nix-optimise` at 00:56. What is established is that the tmpfiles run happened, completed, and read 103.4 GB doing it — not how the 96 GB splits between the two.
    via: measurement
- 🔴 **`apply-tmp-churn-stale-lines-2026-09-04.sh --dry-run` exits 0 with "nothing to do" ON AN UNAPPLIED HOST — and my predicted exit 3 was WRONG.** Measured on the laptop 2026-09-07. The script is honest (it prints the discriminating `grep -c 'mM:7d'` itself and says the green is not "already patched"), but the exit code and wording both read as success. This is the RULES shape *"a wrapper that reports 'nothing to do' instead of erroring is how a green gets believed"* — met in the wild, in this effort's own tooling. `nix/system/preflight-tmp-churn-host.sh` now runs that grep plus three other reads and CLASSIFIES into five states rather than leaving an exit code to stand in for an answer.
- 🔴 **A `|| echo` fallback turned a PERMISSION DENIAL into a reassuring "precondition would PASS", and I nearly reported it.** Probing the laptop's readable `/etc/nixos` backups, `grep -n "systemd.tmpfiles" "$f" || echo "  none — precondition 1 would PASS"` printed the PASS line because grep **failed with `Permission denied`**, not because it found nothing — the file I had selected with `ls -1t | head -1` was mode 0600. Caught only by reading the stderr sitting directly above my own reassuring line. **Same family as this doc's `grep -c` → `"0\n0"` and `NR-1` → −1 findings, and the direction is the constant: an error became the answer that let the work proceed.** Fix: separate READABILITY from CONTENT — test `[ -r "$f" ]` first, enumerate what is actually readable, and report grep's exit code beside its count so a zero is attributable.
    via: measurement
- **The laptop's `/etc/nixos` is mostly UNREADABLE to a non-root probe, and unevenly so** — of 5 `.nix` files and 12 `configuration.nix.bak-*`, the live config and 5 backups are 0600 while 7 older backups are 0644. The newest readable copy was **2026-06-13 (14,893 B)** against a live **2026-08-24 (16,722 B)**, so any precondition read from it was a hypothesis about a 2.5-month-old snapshot, ~1.8 KB behind. It happened to be right; **the actual safety was the script re-checking both preconditions at runtime and refusing**, not the pre-check.
- **`--init`'s design is deliberately the inverse of the deleted eviction feature.** It NEVER rewrites or deletes a line — it appends a new top-level attribute before the file's final `}`, behind three refusing preconditions (no `systemd.tmpfiles` anywhere; last non-blank line is exactly `}`; `nix-instantiate --parse` succeeds after the edit or the backup is restored *and re-parsed*). Fixture-verified in both directions before it ever ran: 7 rules inserted with **all original lines still present**, conflict and bad-tail fixtures both refused at exit 3, and a deliberately malformed block failed the parse, fired the restore, and left the file **byte-identical** (`cmp -s` clean).
    via: measurement
- **`cp -a` preservation is a usable cross-check on a backup's identity.** The laptop's `configuration.nix.bak-20260907-175418` reads **16,722 B, mtime Aug 24** — size AND mtime identical to the pre-edit live file measured earlier in the session, which is positive evidence the backup is the original rather than a re-stamped copy.
- ⚠ **`systemctl show <timer> -p NextElapseUSecRealtime --value` returned EMPTY on the laptop** while `is-active` said `active`. So the timer being armed is verified; its next-fire time is only relayed from the run's own output. A property that answers empty is not a reading — do not quote the time as independently confirmed.

- 🔴 **A silent nixpkgs bump INVERTED A DISASTER-RECOVERY VERDICT, and it surfaced only as "the gate is red".** `age` 1.3.1 → 1.3.2 **creates `--output` lazily, on the first successful write**. I reproduced it independently: over a 4 KiB payload with a flipped payload byte, `age 1.3.1` leaves `out.txt` **present** (size 0, stderr `failed to decrypt and authenticate payload chunk`), `age 1.3.2` leaves **no file at all**. The escrow verifier used exactly that presence to mean *"age authenticated the header, so the escrow key is FINE and the BACKUP is corrupt"* (`ARTIFACT-CORRUPT`). Under 1.3.2 the same tampered backup reads as `DECRYPT-FAILED` — **the verdict that gets a healthy DR key rotated.** Every artifact this subsystem produces is under 64 KiB, so the whole population was affected. Fixed in `#1392` by re-keying to age's own three refusal strings; the weakening (a substring match on another tool's prose) is stated in code and `SECRETS.md` rather than quietly re-greened, and an unrecognised refusal reaches neither strong verdict.
    via: measurement
- 🔴 **A GUARD THAT SILENTLY STOPPED GUARDING: `age-keygen` 1.3.2 no longer echoes its input, so the secret-redaction test went VACUOUS.** 1.3.1 leaked the secret on **6 of 17** manglings (three the ledger never listed); 1.3.2 on **0**. Proven vacuous, not merely unnecessary: re-interpolating `p.stderr` **survives** `test_NO_pubkey_failure_message_EVER_carries_key_material`. The risk is still live because the login shell runs 1.3.1. Both positive controls moved onto a stub reproducing 1.3.1's stderr.
    via: measurement
- 🔴 **A TEST'S OWN ERROR MESSAGE GAVE THE WRONG DIAGNOSIS, AND I RELAYED IT WITHOUT CHECKING.** `test_opencode_engine.py` said the usual cause is a stale imperative profile entry and prescribed `nix profile remove opencode` + `home-manager switch`. **Measured false here:** `nix profile remove opencode` → *"does not match any packages in the profile"*; the **home-manager generation itself** ships it (`home-manager-generation/home-path/bin/opencode → …opencode-1.18.29`); it is plain `pkgs.opencode` at `nix/home.nix:120` with no flake input; and `nix eval` of `homeConfigurations.zach.config.home.packages` says a switch today builds **`opencode-1.18.29`** — the version already installed. The prescribed fix was a provable no-op. That discriminating check is now written into the assertion message.
    via: measurement
- 🔴 **TOOLS DIFFER BETWEEN THE INTERACTIVE SHELL AND THE DEV SHELL, AND THE GATE RUNS IN THE DEV SHELL.** Measured: `find` is **bfs 4.1.1** interactively and **GNU findutils** in `nix develop`; `age` is **v1.3.1** interactively and **v1.3.2** in the dev shell. Any version claim must name the shell it was measured in. This is the same family as the `%D` finding below and it is why the age drift was invisible from a normal terminal.
    via: measurement
- 🔴 **A CLEAN `git merge` CARRIED A STALE DRIFT FLOOR FORWARD — TWICE — AND NOTHING FLAGGED IT.** `scripts/run-tests.sh`'s `"scripts/tests|N"` is exactly the value a textually-clean merge preserves wrongly. First merge: three-way conflict where **all three candidates were wrong** (base 12793, ours 12848, main's 12820) because main had moved 26 commits — merged tree collected **12966** → floor **12916**. Second merge (after `#1366`/`#1392` landed): **rc 0, no conflict at all**, and the carried-forward 12916 was stale against a tree collecting **12977** → floor **12927**. Both re-derived by sourcing the gate's own `_suggested_floor` out of `run-tests.sh` and feeding it the measured count — never arithmetic across a conflict, which both sides' comments explicitly warn against.
    via: measurement
- 🔴 **THE SHARED CLONE SWITCHED BRANCHES MID-SESSION.** `/home/zach/workspace/devrc` was on `main` and is now on `feat/nct6683-fans-bar` — another session's work. `handoff_doc.py` pushes **wherever the checkout sits**, so this handoff was written from a dedicated worktree (`devrc-handoff` off `origin/main`) rather than the base clone. Check `git branch --show-current` before any write to that clone.
- ⚠ **Two near-misses from my own greps, both of which would have read as reassuring:**
  - Verifying `#1392` merged, a content check against a **guessed path** (`scripts/lib/analyze_service_index/escrow_verify.py`) returned a clean **`0`** — indistinguishable from "the merge did not land". The real path is `scripts/analyze-service-index/escrow-verify.py`; only a fallback `git grep` caught it.
  - `bash clawgate_handoff.sh field <doc> | head -2; echo rc=$?` printed **0** because the **pipe ate the status** — the exact trap the `/handoff` skill documents. The real rc was 1.
- ⚠ **`$sha:path` in zsh is eaten as a `:s` history modifier** — `git show $sha:scripts/diagnose-disk-accounting.sh` silently became `c1169e3bounting.sh`. Brace it: `${sha}:path`. Hit despite the rule naming this exact case.
- **The laptop's live `--init` run was SAFE, but on two axes by luck rather than by check.** The `#1370` audit later found three 🔴; each was checked against what actually ran: 🔴1 (read-only mode can edit `/etc/nixos` via an old retention script) did not fire because the laptop's devrc `38bd8edd` **contains** `d8fe0bce` — confirmed twice, since no `bak-tmp-churn-*` exists either; 🔴3 (symlinked config) missed because that config is a regular file; 🟡12 (mode downgrade) changed nothing because it was already 0600.
- **The laptop's config was edited by ANOTHER SESSION 22 minutes after mine**, and the coordination check I ran earlier predicted it: `configuration.nix.bak-drop443-20260907-181625` is the nebula drop-443 work (generation 248). My rules survived it — 7 `mM:7d` still live. Git-level overlap was nil; the host-level sequencing worked out.

- ⚠ **`grep … | head -1` READ THE WRONG LINE TWICE IN ONE SESSION, and both times the wrong answer looked plausible.** Verifying `#1370`'s merge, `grep -oE '"scripts/tests\|[0-9]+"' | head -1` returned **`"scripts/tests|4976"`** — a figure from a *comment example* at `run-tests.sh:1481`, not the live `TARGET_FLOORS` entry at `:1698` (`12927`). Four lines in that file mention `scripts/tests|`. Earlier the same shape produced a bare `0` from a **guessed path** while verifying `#1392`, which reads exactly like "the merge did not land". **Anchor the pattern (`^  "scripts/tests\|`), and print how many candidates matched** — a first-match on an unanchored pattern is a claim about ordering, not about content.
- **Merge order mattered and was derived, not guessed.** `#1370` was red on Tekton with 7 failures it did not cause; `#1392` was green and was the fix for exactly those. Merging `#1392` first turned `#1370` green without touching it — confirmed on the merged tree before either merge (`scripts/tests` 12977 collected / 12977 passed / **0 failed**, where the same tree had shown the 7 drift failures an hour earlier).

- 🔴 **TWO `age` BINARIES ARE INSTALLED AND THEY BEHAVE DIFFERENTLY.** Login shell = **v1.3.1**; `nix develop` = **v1.3.2**, which creates `--output` LAZILY on the first successful write (so a tampered artifact under 64 KiB leaves NO file, where 1.3.1 left one). The gate runs 1.3.2; the operator's `check-escrow.sh` nix-shell resolves 1.3.1. **Any claim about age must name which binary it was measured on**, and a guard that fires on only one of them is a defect.
- 🔴 **`bad header MAC` PROVES THE ESCROWED KEY WORKS.** The discriminating control, both binaries, re-measured at scale by an auditor (336 wrong-identity header corruptions → ZERO `bad header MAC`, against 39/37 from the right-identity control): the SAME damaged blob gives `bad header MAC` with the right identity and `no identity matched any of the recipients` with the wrong one. age reaches the header's integrity check only after an identity has unwrapped a recipient stanza. It is neither pre-auth nor post-auth — `ARTIFACT-CORRUPT`'s usual wording asserts "age authenticated the header", which is what a MAC failure means it did NOT do.
- **The marker table's ORDER is load-bearing**: every pre-auth marker precedes every `AGE_REFUSALS_KEY_PROVEN` one, because age v1.3.2 NESTS clauses (`failed to read header: parsing age header: unexpected EOF reading intro`) on 21 of 168 in-header offsets. v1.3.1 nests on 0 of 168.
- **A fixture that flips a byte of the `--- <mac>` line's BASE64 TEXT is ~5% vacuous** — it sometimes lands on an illegal character and age fails earlier, on the closing line, with a different classification. Decode, flip, re-encode.
- 🔴 **Running a gate target outside `gate.sh`:** `testlib` needs `PYTHONPATH=<repo>/scripts`, and you must invoke **ONE TARGET PER pytest RUN** — basenames collide across targets (`test_db_schema.py` is in both `signal/` and `mail-actions/`), so a combined invocation dies at collection. The runner does it per-target for exactly this reason.
- 🔴 **NEVER restore a mutation battery's files with `git checkout --`.** Mine reverted the round's own UNCOMMITTED edits instead of the temporary copies, so every mutant afterwards died on an unrelated guard — **including the must-survive control**. The control dying is the only thing that exposed it; a battery without one would have reported 5-for-5 kills that proved nothing. Restore by `cp -a`, prove it with `cmp`, and put EVERY file you mutate in the backup set (the test files too).
- 🔴 **A guard can pass on prose ABOUT the thing it guards.** A table check that asked whether each marker appeared anywhere in a comment block was satisfied by the ⚠ note below the table that happens to NAME the marker; deleting the row survived. Extract rows structurally, compare the ANSWER column, and check both directions — a row with the right string and the wrong classification is how the founding bug got written. The same guard also had to learn: pin the exit code beside the cause, and REJECT duplicate rows rather than last-wins (a duplicate inserted above the real table shadowed it).
- **A five-round ladder where every round found something real can still need stopping.** Rounds 4–5 found real defects *in guards the ladder itself had just written*; round 5 could not produce a single red-at-base test because every fix was a guard widening, an unreachable-path hardening, or prose. The attribution judgement — "is this finding about the shipped thing or about my own scaffolding?" — is what ends it, not a clean round.
- **`gate.sh` has a 3600 s wall-clock cap and reports the timeout as `RESULT: FAIL (exit=143)` / `exit=124`.** Under contention that is an infrastructure timeout, not a test failure — check for real `FAILED` lines before attributing it.

- 🔴 **`xargs -0 command grep` SILENTLY MEASURES NOTHING — it reported ZERO referrers where there
  are 24.** `xargs` execs its argument as a **binary**, and `command` is a shell **builtin**, so
  every invocation died `127`; `2>/dev/null` ate the error and the empty result read as a clean
  "no consumers of the changed code". Caught only by a positive control that HAD to match and
  didn't. Use `G=$(command -v grep); … | xargs -0 "$G"`. **This is the same family as this doc's
  `NR-1` → −1, `grep -c` → `"0\n0"`, and `df -i --output` findings, and the direction is again the
  constant: the error became the answer that let the work proceed.** Note the bitter detail —
  the `command grep` habit exists *because* this host's `grep` is a ugrep function that is
  `.gitignore`-blind, i.e. a correct fix for one instrument defect created another.
    via: measurement
- 🔴 **A `Succeeded` PipelineRun is a claim about the RUN, not about tests executing.** Read
  `gh pr checks`' description text, which carries the real counts
  (`collected=21143 passed=21141 skipped=2 failed=0 (floor: 20342)`). A green with a collected
  count far below the floor is a different failure wearing the same colour.
- **A flake is better disproved by RE-RUNNING THE SAME COMMIT than by matching the failure
  signature.** This doc's rank 15 asked for a signature match; the re-run gave a strictly stronger
  result — same commit, same pipeline, opposite verdict — and cost nothing, because another
  session had already triggered it. Check for an in-flight PipelineRun on your exact head
  (`kubectl get pipelinerun -n tekton-ci -o json`, filter on `.spec.params[?(@.name=="revision")]`)
  **before** re-deriving an old red.
- ⚠ **`$ref:path` in zsh is eaten as a `:s` history modifier** — `git show $ref:scripts/tests/x.py`
  became `origin/mainrow_verify.py`. Brace it: `${ref}:path`. Hit **again** this session despite
  this doc already recording the identical trap; the defence is mechanical (always brace), not
  attentional.
- **The base clone `/home/zach/workspace/devrc` was on `main` and clean this session** (176f412b =
  `origin/main`), unlike the prior session where it had been switched to another session's branch.
  Check `git -C … branch --show-current` before any write regardless — `handoff_doc.py` pushes
  wherever the checkout sits.
- **A fresh `devrc` worktree has NO `.envrc`** (it is untracked in this repo), so `direnv` provides
  nothing and any gate target must be invoked explicitly under `nix develop <worktree>`. This is the
  opposite of `homelab-talos`, where `.envrc` IS tracked — do not carry the habit across repos.
- **"When the host is quiet" was not achievable this session and may not be next.** Load went
  49 → 62 → 67 with 9 → 13 concurrent sibling gates over ~30 minutes. If a full `gate.sh` is
  required, either accept a scheduled quiet window or gate on the targeted overlap set and **say
  which one ran** — never let a targeted run be reported as "the gate passed".

## How to verify
1. **Both tmpfiles hosts still live:** `systemd-tmpfiles --cat-config | grep -c 'mM:7d'` → **7**;
   `grep -c ' m:7d'` → **0**. No root needed. Laptop: wrap in `ssh -t zach@192.168.50.155 '...'`.
2. **The three original PRs landed** (by CONTENT — a squash is never an ancestor of its base):
   ```bash
   git -C /home/zach/workspace/devrc fetch origin main -q
   git -C /home/zach/workspace/devrc cat-file -e origin/main:scripts/tests/test_diagnose_disk_accounting.sh   # 1366
   git -C /home/zach/workspace/devrc cat-file -e origin/main:scripts/tests/test_preflight_tmp_churn_host.py   # 1370
   git -C /home/zach/workspace/devrc grep -c classify_age_refusal origin/main -- '*.py'                       # 1392
   ```
3. **`#1409` has NOT landed yet** — this must still be **0** until rank 16 merges it, and non-zero after:
   ```bash
   git -C /home/zach/workspace/devrc fetch origin main -q
   git -C /home/zach/workspace/devrc grep -c AGE_REFUSED_HEADER_MAC origin/main -- '*.py'
   ```
4. **`#1409`'s two owning suites.** 🔴 **The old two-binary instruction is WITHDRAWN** — `4f49f5dc`
   (#1403) measured that every shell on both hosts resolves **age v1.3.2** and nothing puts v1.3.1
   on a PATH, so the v1.3.1 leg tests a configuration that no longer exists. Run the gate tier only:
   ```bash
   cd /home/zach/workspace/devrc-age-markers
   PYTHONDONTWRITEBYTECODE=1 nix develop . -c python3 -m pytest \
     scripts/tests/test_analyze_service_index_escrow_verify.py \
     scripts/tests/test_analyze_service_index_restore_verify.py -q
   ```
   → **450 passed**.
5. **`bad header MAC` really does prove the key** — decode the `--- <mac>` line, flip a bit,
   re-encode, decrypt with the right and the wrong identity → right = `age: error: bad header MAC`,
   wrong = `age: error: no identity matched any of the recipients`.
6. **The workbench reclaim held:** `df -i /` → used inodes materially below the 96.1M baseline
   (67.2M on 2026-09-07); `df -h /` → Use% ≤ 77%.
7. **One command classifies either tmpfiles host** (workbench is the known-good control — expect
   `applied-and-live — 7`):
   ```bash
   cd ~/workspace/devrc && git fetch -q origin main && \
     git show origin/main:nix/system/preflight-tmp-churn-host.sh > /tmp/pf.sh && sudo bash /tmp/pf.sh
   ```
