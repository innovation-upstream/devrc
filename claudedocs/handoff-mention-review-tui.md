# Handoff: mention-review-tui — 2026-09-14

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Replace the neovim+octo PR-review surface (`nvim-octo`) with a purpose-built Go/Bubble Tea
TUI (`mention-review`), because the operator finds the octo commits/files/hunks diff-reading
experience too heavy. Scope is **PR review and merge only** — issues, discussions,
notifications and repo browse are dropped.
- **closing-condition:** `judgement` — the operator uses `mention-review` for a REAL PR
  review, on a real screen, and says it beats octo at reading a diff. Nothing headless closes
  this; Phase 4 (deleting `nvim-octo`) is gated on it by the proposal's own rollback section.

## State now
- **Branch / PR:** `feat/mention-review-phase1` → **devrc#1698**, OPEN, head `21e5683b`,
  rebased onto current `main`, 0 behind. CI re-running at time of writing.
- **Spec is merged:** `claudedocs/proposal-pr-review-tui.md` (#1696, squash `02b54fe4`),
  ~1,300 lines. It is the authority for Phases 2–4; read it before continuing.
- **Merged this session (all verified by content on `origin/main`, not by ancestry):**
  - **#1666** `0325668c` — closed out the octo legend arc.
  - **#1686** `c05e4f2d` — octo diff legend gained vim's own `]c`/`[c` + fold motions in a
    separately-labelled section, `\vs`/`\C` promoted, `file_panel` follows terminal height.
    **Shipped to BOTH hosts** (`ship.sh` rc=0, both at `c05e4f2d`).
  - **#1696** `02b54fe4` — the proposal.
- **#1698 contains:** Phase 0 spike + Phase 1 read-only slice + a **third gate tier**
  (`scripts/run-go-tests.sh`), 45 files. Then four fixes from `the-algorithm` (below).
- **Deploy status, stated honestly:** `mention-review` is **NOT deployed and NOT on PATH**.
  The Alacritty click path still spawns `nvim-octo`. Nothing about the new TUI has been seen
  on a screen by anyone.

**Phase 0's three kill criteria all PASSED** (none tripped):
- query: **1 HTTP round trip**, counted with a `RoundTripper` wrapper. 663ms / 536ms / 402ms.
- auth: go-gh v2.16.0; measured as a triple — real config `OK`, no config `NO TOKEN`,
  bogus `GH_TOKEN` → 401 `TOKEN REJECTED`.
- renderer: **318µs @1k · 347µs @4k · 342µs @10k**, flat. Control: `SoftWrap=true` → 21.55ms
  @10k (63×), proving the benchmark reads the buffer.

## Open investigations — live diagnosis state

### The Go tier has NO CI leg — ~7,000 lines run only when a human types the command
- as-of: 2026-09-14
- **Symptom + exact repro:** `gh pr checks 1698 --repo innovation-upstream/devrc` lists
  `tekton/devrc-pytests`, `tekton/devrc-nodetests`, `tekton/devrc-cairn-client-runs` and
  **no** `tekton/devrc-gotests`.
- **Observed (with values):** `gh api /repos/innovation-upstream/devrc/commits/<sha>/statuses
  --jq '[.[].context]|unique'` returns exactly those three contexts. `flake.nix` DOES define
  a `gotests` check output and documents honestly that it "may be built by nobody".
  `scripts/main-status-watch.py` contains no tier names at all.
- **Ruled out:** that the deadman covers it — `scripts/main-green-check.sh:424` read
  `for tier in pytests nodetests; do`, Go absent. **FIXED in #1698**, now reads
  `pytests nodetests gotests`. via: measurement
- **Ruled out:** that the tier is simply unwired locally — `scripts/gate.sh --tier go`
  works and the runner is 357 lines, the smallest of the three. via: command
- **Leading hypothesis:** the Tekton pipeline hardcodes its legs and lives in the infra
  repo (`devrc-ci-pipeline.yaml`), not in devrc — so no change inside devrc can add the leg.
- **Next probe:** find `devrc-ci-pipeline.yaml` in the infra repo and add a `gotests` leg
  mirroring the `nodetests` one. Closes when `gh pr checks` on ANY devrc PR lists
  `tekton/devrc-gotests`.

## Next steps (ranked)
1. **Merge devrc#1698 once CI is green.** All four `the-algorithm` fixes are in and were
   re-verified independently (matrix below). The only expected reds are inherited. Repo: devrc.
   forcing: gate — the PR is the gate for Phases 2–4; nothing proceeds until it lands.
2. **Answer proposal §12.4 before Phase 2: inline diff-line comments, or PR-level only?**
   Inline needs review-thread positioning against the diff and is materially more work.
   Phase 2 (write actions) cannot be specced without it. Repo: devrc.
   forcing: user — only the operator can answer it.
3. **Add a `gotests` leg to `devrc-ci-pipeline.yaml`** in the infra repo (see the open
   investigation above). Repo: infra (NOT devrc).
   forcing: gate — ~7,000 lines and 119 Go tests currently have zero PR-time coverage.
4. **Phase 2 — write actions** (comment · approve · request changes · submit review · merge),
   all behind the confirmation ledger of proposal §3.7. Reintroduce the write-intent ledger
   deleted in #1698. Repo: devrc.
   forcing: none
5. **Phase 3 — speed work**: local-clone probe, PR-ref fetch RACING the API, bounded on-disk
   cache. 🔴 Must be a RACE, not a preference — see Gotchas. Repo: devrc.
   forcing: none

## Defects (batched)
- `keys_test.go`'s structural half compares `Dispatch()` against `FullHelp()`/`ShortHelp()`,
  both built from the SAME literal — it asserts the keymap agrees with itself.
- Three guard descriptions are wider than their implementations:
  `test_mention_review.py:122` (claims it pins the tier set; does not check
  `main-green-check.sh`), `internal/ui/words.go:179-184` (claims the ledger covers every
  constructor; nothing asserts completeness), `test_mention_review.py:361` (claims a slug
  check; there is none).
- `theme_test.go` (185 lines) and `words.go::MeaningBearingStates()` (55 lines) are inert —
  a mistyped palette key survives both. Deletable as follow-up.
- `internal/ui/run.go:34` cited a non-existent `run_test.go` — FIXED in #1698.

## Gotchas / decisions / dead-ends
- 🔴 **"Local git first (~0ms)" is WRONG on the FIRST read, and that framing came from me.**
  MEASURED: `git fetch` of a PR ref = **0.84–0.90s**, vs a REST diff at 0.66–0.90s. The local
  diff AFTER the fetch is **0.01–0.02s**. So local's value is the 2nd..Nth read, and Phase 3
  must implement a **race**, not a preference.
- 🔴 **Only ~8.4% of clickable repos have a local clone** — 33 distinct origin remotes under
  `~/workspace` vs **371** in `~/.config/mention-open/known_repos.json`. The API is the
  MAJORITY path, not the fallback.
- 🔴 **The performance problem was never the editor.** nvim-octo starts in **0.08s warm /
  0.58s cold**. The cost is GitHub round-trips at 0.5–0.9s each, made sequentially per
  navigation. A rewrite making the same sequential calls would feel identical — the win is
  prefetch/cache/parallelism.
- 🔴 **`the-algorithm` has a gap this PR proved.** `keys_test.go` passes step 1 easily (the
  stale-footer hazard is real and incident-backed) and is still nearly worthless — only a
  mutation revealed it. The skill has no step saying *test the guard you just defended*.
  A mutation pass belongs BETWEEN steps 1 and 2. Also, step 5's "the fix for over-guarding is
  NEVER another guard" would wrongly forbid finishing the half-built deadman wiring.
- 🔴 **Three inherited reds looked like this PR's own, in one session.** Each time the branch
  was behind `main` and the failing test named a file the diff never touched:
  `test_NO_TRACKED_FILE_ASSERTS…`, `test_every_mutation_anchor…`, and
  `test_no_client_subdomain_literal_is_committed` (a client hostname in
  `handoff-cairn-oss-multi-instance.md`, scrubbed on main by #1700/#1705). **Read the failing
  test's NAME and ask whether the diff can reach it, before debugging anything.**
- **`]h`/`[h` from the proposal are NOT implementable** — `bubbles/key` matches a single
  `KeyPressMsg.String()`; two-key sequences need a pending-key state machine the proposal
  never budgeted. #1698 uses `]`/`[` (hunk) and `}`/`{` (file). Proposal §3.1 is wrong here.
- **Deliberate divergence from upstream octo:** `mention-review` keeps the argv contract
  `<owner/repo> <number>` with exits 64/65/66, so `mention-open.py` needs no change. The
  executable NAME is spelled at four test-pinned sites.
- 🔴 **Auth hazard, live:** `go-gh` shells out to `gh auth token --secure-storage`;
  cli/cli#14370 reports that can return a DIFFERENT account's token, and this host's
  `hosts.yml` carries **two** github.com users (verified). Mitigation shipped: `viewer
  { login }` appears in the Overview panel and must appear in every confirmation string.
- **`gh-dash` is a negative result** — 12.5k stars, 4 years, Bubble Tea v2, and it has never
  solved in-pane diff rendering; it shells out to a pager. Closest prior art, and it is a
  warning rather than a model.
- **The write-intent ledger deletion is a PHASING judgement, not a defect removal.** It was
  documented as Phase-2 scaffolding. The argument for deleting: the Intent *seam* must exist
  from day one, but the *ledger* is purely additive. Phase 2 must reintroduce it.
- ⚠ **A subagent ran `pkill -x nvim`** — box-wide by name. It may have killed an editor of
  the operator's. Unresolved whether it cost anything. Every subsequent brief forbids
  `pkill -f`/`-x` outright and requires PID resolution with a `/proc/<pid>/cwd` check.
- ⚠ **74 `devrc-*` worktrees are registered in this clone**, and a completed agent's worktree
  still holds `feat/mention-review-phase1`, which forced a detached rebase. A stale worktree
  holds its branch repo-globally at whatever commit it stopped on.

## How to verify
```bash
# The four algorithm fixes, all re-verified independently on 2026-09-14:
#  1. RUNNERS registration — scripts/tests/test_git_repo_isolation.py:120
#  2. deadman tier loop    — scripts/main-green-check.sh:424 reads "pytests nodetests gotests"
#  3. ledger deleted       — grep -c 'LedgerViolations\|IntentTypeName' intents.go  → 0
#  4. movement coverage    — see the mutation below

W=<a worktree at feat/mention-review-phase1>
S="$W/nix/pkgs/tools/mention-review/src"

# Go suite (5 packages green)
nix develop "$W" -c bash -c "cd '$S' && go clean -testcache && go test ./..."

# 🔴 FIX 4's ACCEPTANCE CRITERION — invert the four movement arms in
# internal/ui/app.go:283-290 (PageUp/PageDown swap, Top<->Bottom) and confirm
# movement_test.go goes RED. Observed: 3 tests, 10 assertions, literal values:
#   "one ctrl+d from the top -> diffCur = 0, want 10"
#   "G from line 100 -> diffCur = 0, want 201"
# POSITIVE CONTROL (proves the suite discriminates): mutate
# internal/udiff/udiff.go:309  h.LineIndex > from  ->  >= from
#   kills 3 tests across 2 packages.

# The deployed octo click path (the CURRENT surface — still what a click opens).
# 🔴 Do NOT grep bin/nvim-octo; it returns 0 on a HEALTHY deploy. Four store hops on:
AT=$(readlink -f ~/.config/alacritty/alacritty.toml)
MO=$(grep -oE '/nix/store/[a-z0-9]+-alacritty-mention-open' "$AT" | head -1)
NO=$(grep -oE '/nix/store/[a-z0-9]+-nvim-octo' "$MO" | head -1)
NV=$(grep -oE '/nix/store/[a-z0-9]+-neovim-[0-9.]+' "$NO"/bin/nvim-octo | head -1)
IL=$(grep -aoE '/nix/store/[a-z0-9]+-init\.lua' "$NV"/bin/nvim | head -1)
IV=$(grep -oE '/nix/store/[a-z0-9]+-init\.vim' "$IL" | head -1)
OI=$(grep -oE '/nix/store/[a-z0-9]+-octo-init\.lua' "$IV" | head -1)
grep -c 'NATIVE_MOTIONS' "$OI"        # 2 as shipped in #1686
```
