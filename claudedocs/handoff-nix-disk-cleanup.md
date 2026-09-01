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
- **PR OPEN: devrc#1227** on branch `handoff-nix-disk-v3` (head `df6f0346`, current main merged in). Not merged.
- Claim held: `nix-disk-cleanup-1`. No clawgate task.
- The investigation is RESOLVED (`/tmp`, 78.5M entries / 469 GiB). The remaining work is landing the fix and taking the reclaim.
- 🔴 **`nix/system/apply-tmp-churn-retention.sh` is MODIFIED in the base clone `~/workspace/devrc` and must stay that way until #1227 merges.** The working copy is the FIXED version (byte-identical to the branch); `main` still carries the broken `m:7d`. A `git checkout` of that file silently restores a rule set that ages no directory AND removes `--emit-rules`, which is the command the operator was given.

### What's DONE this session
1. Refuted the "ext4 metadata" diagnosis; measured the real answer.
2. Wrote `scripts/diagnose-disk-accounting.sh`, ran it (operator, as root), then fixed three defects its first run exposed.
3. Audited `apply-tmp-churn-retention.sh` against `tmpfiles.d(5)` — found the `m:` vs `mM:` defect that made the never-applied fix inert.
4. Fixed `scripts/tests/test_tmp_churn_retention.py` after the gate caught 10 failures I introduced.
5. Gated the MERGED tree on both tiers, all green.

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

## Next steps (ranked)
1. **Take the `/tmp` reclaim — no rebuild needed, and the rebuild is BLOCKED anyway.** `sudo bash nix/system/apply-tmp-churn-retention.sh --emit-rules > /tmp/churn.conf`, inspect with `sudo systemd-tmpfiles --dry-run --clean /tmp/churn.conf 2>&1 | tail` (read BOTH streams — "Would remove" is on stderr), then `sudo systemd-tmpfiles --clean /tmp/churn.conf`. Measure `df -i /` before and after; that difference is the only real answer to how much `/tmp` was holding.
   forcing: none
2. **Merge #1227** once Tekton settles. Both tiers are green locally on the merged tree. `/audit-pr 1227` has NOT been run and is recommended — this PR produced two retracted claims and one caught regression.
   forcing: none
3. **`sudo nixos-rebuild boot` + reboot** to make the rules durable. `switch` is blocked by a `switchInhibitors` pre-switch check on an unrelated `dbus -> broker` channel migration; `boot` skips it. Do NOT use `NIXOS_NO_CHECK=1` on a box running k3s.
   forcing: none
4. **Decide whether `/var/lib/docker` (57 GB, 1.64M inodes) is live** — k3s uses containerd. `docker ps -a`, `docker system df`.
   forcing: none
5. **Housekeeping:** `sudo umount /mnt/rootcheck && sudo rmdir /mnt/rootcheck` · `sudo rmdir '/&&'` · delete untracked `scripts/diagnose-nix-disk.sh` and `output.txt` · `claim-work.sh --release nix-disk-cleanup-1`.
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

## How to verify
1. **The reclaim:** `df -i /` before and after the `--clean`. Used inodes should drop by millions.
2. **The rules are live** (after a reboot): `systemd-tmpfiles --cat-config | grep 'mM:7d'` returns 8 rules, and `grep -c ' m:7d'` returns 0.
3. **The gate:** re-run both `nix build .#checks.x86_64-linux.{pytests,nodetests}` ONE AT A TIME on a merged tree and read the `TOTAL`/`RESULT:` lines, never the exit code.
4. **The flake attribution:** if `devrc-pytests` is red, read the gate pod log and check the failing test against `test_subsystem_store_api.py` before attributing it to any branch.
