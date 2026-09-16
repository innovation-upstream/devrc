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
- 🔴 **THE SPEC IS `claudedocs/proposal-pr-review-tui.md`** (#1696, `02b54fe4`), merged.
  **Authority for Phases 3–4 — read it before continuing.**
- **Lineage, all merged and verified by content on `origin/main`:** `#1666` `0325668c` (closed
  the octo legend arc) · `#1686` `c05e4f2d` (octo's OWN diff legend gained vim's `]c`/`[c` +
  fold motions — still the surface a click opens) · `#1696` `02b54fe4` (the proposal) ·
  **`#1698` `7b827d13` (Phase 0 + Phase 1 + the Go gate tier)** · `#1723` `e49bbace` (Phase 2).
- ✅ **PHASE 2 IS MERGED AND DEPLOYED.** `devrc#1723` → squash **`e49bbace`**. Five write verbs
  (comment · approve · request changes · submit review · merge) behind the §3.7 confirmation
  ledger. The write-intent ledger #1698 deleted is **reinstated and no longer vacuous**.
  All four CI legs green at merge.
- ✅ **THE `gotests` CI LEG IS LIVE — rank 1 of the previous revision is CLOSED by its own
  mechanical condition.** `ZacxDev/homelab-infra#827` → `a05f355da` on `trunk` (GitOps, so the
  merge WAS the deploy). Measured on the next devrc PR: `tekton/devrc-gotests pass — TOTAL:
  pass=119`, and on #1723 itself **pass=231**. The 119 Go tests that had zero PR-time coverage
  now gate PRs.
- ✅ **DEPLOYED TO BOTH HOSTS.** `ship.sh` rc=0, 2 hosts compared, both at `e49bbace`,
  0 dangling / 0 stale. `mention-review` **0.1.0 → 0.2.0** on PATH on workbench AND laptop;
  argv contract verified live (`64` no-args / `65` bad repo / `66` bad number, read without a
  pipe). Phase-2 strings confirmed present in the real binary on both.
- 🔴 **THE CLICK PATH IS STILL `nvim-octo`, DELIBERATELY.** `REVIEW_EXE` unchanged; the
  Alacritty wrapper still resolves `…-nvim-octo`. This is the first code that can approve and
  merge on the operator's behalf and it has **never been seen on a screen**, so it is run by
  hand (`mention-review <owner/repo> <N>`) rather than forced into the daily click. Rollback
  stays one line either way.
- **§12.4 IS ANSWERED, not assumed:** **PR-level comments only, no inline diff-line
  positioning.** Operator decision 2026-09-15. Nothing in the code computes a diff position.
- **The `pkill -x nvim` incident cost nothing** — operator confirmed no editor was lost. The
  prohibition stays in every dispatch brief regardless.

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

### The `gotests` CI leg — pipeline LOCATED, change not yet made (supersedes the 2026-09-14 block above)
- as-of: 2026-09-15
- **Supersedes** the earlier block of the same subject; its "Next probe" said *find the
  pipeline*. It is found. Everything else in that block still holds.
- **Symptom + exact repro:** `gh pr checks <any devrc PR>` lists `tekton/devrc-pytests`,
  `tekton/devrc-nodetests`, `tekton/devrc-cairn-client-runs` and **no** `tekton/devrc-gotests`.
- **Observed (with values):** the pipeline is `clusters/homelab/apps/tekton-pipelines/triggers/
  devrc-ci-pipeline.yaml` in **homelab-talos**, alongside `devrc-ci-triggertemplate.yaml`.
  Confirmed present. The deadman half is now CLOSED on main (`gotests` in the tier loop), so
  `main` is covered every 4 hours; **PR-time coverage is still zero.** via: command
- **Ruled out:** that the leg could be added from inside devrc — it cannot; the pipeline is a
  different repo. via: command
- 🔴 **Constraint that changes how this is done:** `homelab-talos` is GitOps-reconciled from
  `trunk`, and its own `CLAUDE.md` declares that **committing to the main branch IS deploying**
  — the one repo with that exception. A pipeline edit there is a live CI change, not a PR that
  waits for review.
- **Next probe:** add a `gotests` leg mirroring the `nodetests` one in
  `devrc-ci-pipeline.yaml`. **Closes when `gh pr checks` on any devrc PR lists
  `tekton/devrc-gotests`.**

### RESOLVED — the `gotests` CI leg (retires the 2026-09-15 block above)
- as-of: 2026-09-16
- **Retracted as live.** The leg exists, posts, and passes. Closing condition met exactly as
  written: `gh pr checks` lists `tekton/devrc-gotests`. Everything below is history.
- **Observed (with values):** `tekton/devrc-gotests pass — TOTAL: pass=119 fail=0 ran=119
  (global floor 111)` on the first devrc PR after the merge; `pass=231` on #1723.
- **Ruled out:** that a fourth sequential leg would blow the gate's 60m budget on day one —
  the four legs on #1723 all reported green. ⚠ NOT a general claim: measured over 21 retained
  `devrc-ci-*-gate` TaskRuns, two runs were already at **55.9m and 58.1m** against the 60m cap,
  with `pytests` alone at 49.1m and 46.0m. Watch this. via: measurement

### 🔴 No write verb has ever executed against real GitHub
- as-of: 2026-09-16
- **Symptom + exact repro:** n/a — an untested path in shipped code, not a defect. The five
  write verbs are exercised only against in-process fakes and `httptest`.
- **Observed (with values):** the whole Go suite (231 tests) passes inside `nix build`'s
  **network-less sandbox**, which is itself the proof no test reaches a real host. Four
  additional locks: `App.runner` is `nil` in pure tests; the one end-to-end test asserts the
  write ledger is *exactly* `[PostComment … body="ok"]`; `http.DefaultTransport` is replaced in
  both network-reaching packages by a loopback-only transport; `cmd/*` is exempt with a stated
  reason. **I verified the transport lock myself** — disarming it yields
  `the guard let a request to api.github.com THROUGH`.
- **Ruled out:** that the guard is vacuous — mutated it and watched the negative control fire.
  ⚠ My FIRST mutant did not compile (orphaned `fmt`), which is not a result; a compiling
  variant is what produced the kill. via: measurement
- **Leading hypothesis:** none. `LiveRunner`'s three delegations and `ghapi`'s three endpoints
  are plain code paths that have simply never run live.
- **Next probe:** open a throwaway PR in a scratch repo and drive `c` (comment) then `m`
  (merge) against it. **Operator-only** — an agent must not run a live write verb.

## Next steps (ranked)
1. 🔴 **USE `mention-review` FOR A REAL REVIEW.** `mention-review <owner/repo> <N>` — it is on
   PATH on both hosts at 0.2.0. This is the arc's closing condition and nothing headless can
   substitute. Press `?` for the legend, read a diff, and form a view on whether it beats octo
   at commits/files/hunks. Repo: devrc.
   forcing: user — the closing condition names the operator reading real evidence.
2. **Phase 3 — the speed work**: local-clone probe, PR-ref fetch **RACING** the API, bounded
   on-disk cache (0600), per-commit diff. 🔴 A RACE, never a preference — see Gotchas. Repo: devrc.
   forcing: none
3. **Phase 4 — retirement**, its own PR: delete `nix/pkgs/tools/nvim-octo/` (3 files),
   `test_nvim_octo.py` (67 tests), `test_nvim_octo_diff_motions.py` (8), the `nvimOctoOverlay`,
   and `luajit` from `REQUIRED_TOOLS`. 🔴 **GATED ON RANK 1** — ship only after the operator has
   used the TUI for a real review and said so. Repo: devrc.
   forcing: none

## Defects (batched)
- Two guards still claim more than they check: `scripts/tests/test_mention_review.py:122`
  (claims it pins the tier set; does not check `main-green-check.sh`) and `:361` (claims a slug
  check; there is none). The third — `internal/ui/words.go:179-184` — was CLOSED by Phase 2.
- `keys_test.go`'s structural half compares `Dispatch()` against `FullHelp()`/`ShortHelp()`,
  both built from the SAME literal — it asserts the keymap agrees with itself.
- Round 0 on homelab#827 left three deletion candidates, all PRE-EXISTING, none blocking:
  D1 the 13-site per-leg `*-context` param chain (consolidating it would make a fifth leg
  nearly free AND structurally kill the status-clobber hazard); D2/D3 per-leg duplication in
  `test_devrc_notify_empty_context_guard.py` and `test_nix_cache_persistence.py`.
- The pipeline's `enforce_admins: true` belief survives at ~8 PRE-EXISTING sites in
  `devrc-ci-pipeline.yaml` and `ci-priority-classes.yaml`. The two lines #827 added were
  corrected; the rest were deliberately left (widening an untestable prod-pipeline diff).

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

- 🔴 **`test_no_test_writes_a_usr_bin_env_shebang_at_runtime` IS WIDER THAN ITS NAME.** It
  rejects **any** self-written shebang, not just `/usr/bin/env` — MEASURED: replacing
  `#!/usr/bin/env bash` with `#!/bin/sh` in a test fixture was still RED. The only remedy is the
  one it names, `testlib.mockbin.write_exec`, which owns the shebang so a call site cannot
  supply one. And the shebang could not simply be deleted: `_env_vars_gate_sh_reads` computes
  `body_start` over `range(1, …)`, so line 0 is skipped by construction.
- 🔴 **FOUR INHERITED REDS AND ONE SELF-CAUSED, AND THE SELF-CAUSED ONE CAME LAST.** Three
  branch-behind-main reds in a row (`test_NO_TRACKED_FILE_ASSERTS…`, `test_every_mutation_
  anchor…`, `test_no_client_subdomain_literal…`) trained the reflex "the red is not ours"; the
  fourth WAS ours — `c5570bbc`'s fixture writing its own shebang. **The pattern is the hazard:
  check whether the diff can reach the failing test EVERY time, including the time after three
  consecutive noes.**
- 🔴 **A guard that a change ADDS TO can be blinded by the addition.** `c5570bbc`: adding three
  names to `gate.sh`'s refusal loop wrapped it onto a second line, and the ledger's harvester
  anchored its regex on ONE line — so it matched nothing and silently stopped seeing
  `DEVRC_TARGETS` and `MIN_TESTS`, the two it existed to catch. *"The regex found no loop" and
  "the loop reads nothing" are the same empty set, so a blind harvester reports FULL COVERAGE.*
  It only failed loudly by accident. The fix joins continuations before scanning and adds a
  control pinned against a **synthetic** script, so `gate.sh` reformatting its own loop cannot
  make the control vacuous.
- **Re-prove a control after editing the fixture it runs on.** Changing the synthetic script to
  `write_exec` changed what the control is made of; that it still PASSED is a different claim
  from that it can still go RED. Re-ran the continuation-join mutation and confirmed it fails.
- ⚠ **A merge on local evidence is not a merge on CI.** #1698 landed with all three Tekton legs
  `pending`. Locally verified, and that distinction is recorded here rather than smoothed over.

- 🔴 **THE WRAPPER-GREP TRAP FIRED TWICE IN ONE SESSION, THE SECOND TIME AFTER I WROTE IT UP.**
  `bin/mention-review` is a **423-byte `makeWrapper` script**, not the Go binary — so grepping
  it for Phase-2 strings returned **0 on a correct deploy**, on both hosts, reading exactly like
  a failed ship. Identical in shape to `bin/nvim-octo`, which this same arc had already
  diagnosed and documented hours earlier. **What caught it was the POSITIVE CONTROL**: grepping
  for `usage: mention-review`, a string the binary demonstrably prints, also returned 0 — so the
  zero was a fact about the instrument. Follow `exec -a "$0" …` to `bin/.mention-review-wrapped`
  and grep THAT. **Never quote a zero from a `bin/` path without a positive control.**
- 🔴 **`the-algorithm` HAS A GAP THIS ARC MEASURED, AND IT IS WORTH FIXING IN THE SKILL.**
  Step 1 (question the requirement) and step 2 (delete) cannot see a guard that is
  *well-defended but narrower than its defence*. `keys_test.go` passes step 1 easily — the
  stale-footer hazard is real and incident-backed — and is still nearly worthless; only a
  MUTATION revealed it. **A mutation pass belongs BETWEEN steps 1 and 2.** Separately, step 5's
  "the fix for over-guarding is NEVER another guard" read literally would have forbidden
  finishing the half-built deadman wiring, which was the right thing to do.
- 🔴 **FOUR INHERITED REDS AND ONE SELF-CAUSED, AND THE SELF-CAUSED CAME LAST.** Three
  branch-behind-main reds trained the reflex "the red is not ours"; the fourth WAS ours
  (a fixture writing its own shebang). **Check whether the diff can reach the failing test
  EVERY time, including after three consecutive noes.**
- 🔴 **`test_no_test_writes_a_usr_bin_env_shebang_at_runtime` IS WIDER THAN ITS NAME** — it
  rejects ANY self-written shebang, `#!/bin/sh` included (measured). The only remedy is
  `testlib.mockbin.write_exec`, which owns the shebang.
- **A guard that a change ADDS TO can be blinded by the addition.** Adding three names to
  `gate.sh`'s refusal loop wrapped it onto a second line; the ledger's harvester anchored its
  regex on ONE line, matched nothing, and silently stopped seeing the two variables it existed
  to catch — *"the regex found no loop" and "the loop reads nothing" are the same empty set.*
- **Phase 2 design call worth arguing with:** §3.7 said the merge method "is read from config,
  never guessed" but named no config. `internal/cfg` now splits **absent → declared default**
  from **present-but-unreadable → refuse**. The Lua could not make that split (it read a
  third-party config); ours is our own.
- **`ctrl+d` sends in compose, not `ctrl+s`** — `ctrl+s` is XOFF on a terminal that has not
  cleared IXON, and whether raw-mode setup clears it is a host termios property this program
  cannot assert.
- **Comment has NO confirmation**, per §3.7's additive-and-reversible table. §10.2 still
  requires the acting identity on screen, so the compose bar carries `as <login>` and that
  string is pinned.

## How to verify
```bash
# 🔴 RANK 1 — the closing condition. On PATH on both hosts at 0.2.0:
mention-review <owner/repo> <N>        # e.g. innovation-upstream/devrc 1723
#   ? legend · g? in a diff · \C commits · read a diff · form a view vs octo

# Phase 2 is on main — verify by CONTENT, never ancestry (squash breaks ancestry):
git -C ~/workspace/devrc cat-file -e origin/main:nix/pkgs/tools/mention-review/src/internal/ui/write.go

# 🔴 THE DEPLOYED BINARY — follow the WRAPPER, and run the POSITIVE CONTROL FIRST.
# `bin/mention-review` is a makeWrapper SCRIPT; grepping it returns 0 on a HEALTHY deploy.
B=$(readlink -f "$(command -v mention-review)")
R=$(grep -oE '/nix/store/[a-z0-9]+-mention-review[^/]*/bin/\.mention-review-wrapped' "$B" | tail -1)
grep -ac 'usage: mention-review' "$R"    # POSITIVE CONTROL — must be 1, else your probe is wrong
grep -ac 'cannot be undone' "$R"         # 1 == Phase 2 write verbs present

# The argv contract — read WITHOUT a pipe, a pipe eats the status:
out=$(mention-review 2>&1); echo $?      # 64 ; bad repo -> 65 ; bad number -> 66

# The network lock (proves no test can reach GitHub): disarm loopbackOnlyTransport in
# internal/ui/nonet_test.go and the control fires —
#   "the guard let a request to api.github.com THROUGH"
# ⚠ a mutant that does not COMPILE is not a result; keep `fmt` used.

# Is the CI leg still live?
gh pr checks <any open devrc PR> | grep gotests
```
