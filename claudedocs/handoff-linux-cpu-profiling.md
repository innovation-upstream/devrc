# Handoff: linux-cpu-profiling

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed.

## Goal
Replace Windows CPU-Z with Linux/NixOS equivalents — research, install, verify.

**Status: the tooling half is DONE and landed as a PR. The "next step" the first
draft of this doc ranked #1 is RETRACTED — see below.**

## State now
- `feat/cpu-profiling-tools` → **PR #1135** — `nix/pkgs/default.nix` adds `inxi` + `cpu-x`.
- Both binaries verified on PATH on the workbench after `home-manager switch`.
- Not yet merged; the **sandbox tier has not been run on the branch**, and neither
  has the merged tree. `scripts/gate.sh` alone is the dev-host tier and does not
  cover `nix build .#checks.x86_64-linux.{pytests,nodetests}`.

## 🔴 RETRACTED: "RAM is running 35% below spec — free performance"

The first draft of this doc led with a ⚠️ finding that the DDR5 is rated 5600 MT/s
but running at 3600, blamed a disabled XMP/EXPO profile, and ranked "enable EXPO in
BIOS" as next step #1. **The measurement is right and the diagnosis is wrong.**

**The values (still true, re-measured):** all four DIMMs report `spec 5600 MT/s,
actual 3600 MT/s`. 4 × 32 GB = 128 GB.

**What the draft got wrong:**

1. **"Not a per-DIMM issue (all 4 identical)" is false.** `inxi -max` shows *two
   different part numbers*: DIMMA1/DIMMB1 are `CP32G56C46U5` (Crucial **Pro**),
   DIMMA2/DIMMB2 are `CT32G56C46U5` (Crucial). This is a **mixed kit**, never
   validated as a set by the vendor. The draft's central elimination was the
   one thing it should have checked.

2. **`CT32G56C46U5` is 2Rx8 — dual rank**, CL46 @ 1.1V. So the config is
   4 DIMMs × dual rank = **8 ranks**, the heaviest possible load on an AM5
   memory controller. AM5 derates hard here: ~3600 MT/s is the *documented,
   expected* result for 2DPC dual-rank, not a fault. The observed number is
   exactly that.

3. **There is likely no EXPO profile to enable.** CL46 @ 1.1V *is* the JEDEC
   DDR5-5600 profile. That 5600 rating is the module's JEDEC speed at 1 DIMM per
   channel — it is not an overclock profile sitting switched off in the BIOS.

**So:** 3600 MT/s is the memory controller behaving to spec for 8 ranks of DDR5,
not money left on the table. Pushing 4 × dual-rank to 5600 is an out-of-spec
overclock, on a **mixed kit**, on a workstation that runs GPU inference and holds
~66 GB resident. The realistic outcomes are "won't POST" or "unstable under
memory pressure" — a bad trade for a box whose job is to stay up.

If it is still wanted, the honest framing is *an experiment with a rollback plan*
(clear CMOS), not a free win — and the lever is a manual multiplier / EXPO Tweaked
step-up (4000 → 4400 → …) with a memory test at each stop, not a single toggle.
Better value for the same effort: drop to 2 × 32 GB if 64 GB ever suffices, which
would run at full 5600.

## Findings that DO hold (workbench, AMD Ryzen 9 9900X, re-measured)
- **CPU** — Zen 5, 12C/24T, boosting ~5.36 GHz. Full AVX-512 + AES-NI + SHA-NI.
  Cache bandwidth L1 124 / L2 112 / L3 87 GB/s. 71.6 °C under load — fine.
- **All major CPU vulnerabilities** report Not affected or mitigated.
- **GPU** — RTX 5080 (GB203), driver 590.48.01, 34 °C idle. Plus the Granite Ridge iGPU.
- **Storage** — 7 drives, ~31 TiB, 55% used. The Samsung 950 PRO dates from 2015;
  worth knowing what is on it. *(carried from the first draft, not re-measured)*

## 🔴 The working tree held THREE efforts, not one
The first draft said the uncommitted changes were "`nix/pkgs/default.nix` +
`scripts/memory-detail`" and ranked "commit them and push" as step #2. Following
that literally would have committed **half of an unrelated feature** — the script
without its `nix/graphical.nix` wiring and without its tests.

What was actually in the tree, now separated and pushed so nothing can be lost:

| effort | branch | state |
|---|---|---|
| CPU profiling tools | `feat/cpu-profiling-tools` | **PR #1135** |
| memory-detail bar feature | *(none — see below)* | **LANDED as #1138** by its own owner |
| stranded rig-control test | `test/opencode-rig-control` | pushed, **must not merge as-is** |

- **`scripts/memory-detail`** had been `git add`ed by the previous session purely to
  unblock its own `home-manager switch`. The real cause was `nix/graphical.nix` —
  also an uncommitted edit by someone else — adding a `home.file` that sources
  `../scripts/memory-detail`; the flake copies tracked files only. So the CPU-Z
  session was blocked by an unrelated in-flight change and resolved it by staging
  that change. The doc then recorded the workaround as if it were the diagnosis.
  Its 20 tests pass, including under a different `$HOME`, so they are hermetic.

- **`scripts/tests/test_opencode_rig_control.py`** is the test file PR **#1134**
  describes but does not contain — that PR carries only its handoff doc, and its
  title claims "34 tests", which is exactly this file's count.
  🔴 **It must not merge unchanged:** it resolves the skill under
  `Path.home()/".config"/"opencode"/skills/rig-control`, the *deployed*
  home-manager output rather than the repo. Measured: **34 passed** with the real
  `$HOME`, **27 failed / 7 passed** with an empty one. The nix check derivations
  build from a store copy with a different `$HOME`, so this would turn
  `tekton/devrc-pytests` — a **required** check — red for every open PR. It
  hard-fails; it does not skip.

## Next steps (ranked)
1. **Merge PR #1135** — run both tiers on the merged tree first
   (`nix build .#checks.x86_64-linux.pytests`, then `.nodetests`, **one at a
   time** — a combined invocation produces false failures). Forcing: none.
2. ~~Decide the fate of `feat/bar-memory-detail`~~ — **CLOSED, no action.** The
   effort had an owner after all: it landed on `main` as **#1138** while this
   work was in flight. The preservation branch was byte-identical to what landed
   (verified per file, not by ancestry) and has been deleted. The judgement that
   held: it was pushed as a BRANCH and never opened as a PR, so rescuing another
   session's work created no duplicate review.
3. **Fix `test/opencode-rig-control` before it goes near the suite** — point it at
   `claude/skills/` instead of the deployed path, or skip cleanly when that path is
   absent. Then it can join #1134. Forcing: none.
4. **RAM: decide whether to experiment at all** — see the retraction above. Default
   recommendation is *no*. Forcing: user.

## Gotchas / decisions / dead-ends
- `fastfetch` was evaluated and dropped — `inxi` covers the same ground in more detail.
- The previous session's opencode dispatch for this research failed on a bash-guard
  block; the research was done directly instead.
- The base clone `~/workspace/devrc` is **2 commits behind `origin/main` with a dirty
  tree**, so `scripts/ship.sh` would **skip this host** and leave it as found. That
  skip hides among greens — read every per-host line, not the final verdict.
- 🔴 A handoff doc written to `/tmp` is not saved work. The first draft of this one
  lived at `/tmp/opencode/handoff-linux-cpu-profiling.md` while its own kickoff block
  pointed at `claudedocs/handoff-linux-cpu-profiling.md`, which did not exist. The
  `/handoff` step that commits the doc had been skipped.

## How to verify
```bash
inxi -Fxz               # whole-machine dump
inxi -max               # per-DIMM part numbers — this is what showed the mixed kit
cpu-x --dump | head -80 # CPU-Z-style cache/clock detail
```
