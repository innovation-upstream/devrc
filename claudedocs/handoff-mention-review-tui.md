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
- 🔴 **THE SPEC IS `claudedocs/proposal-pr-review-tui.md`** (#1696, squash `02b54fe4`, ~1,300
  lines), merged to `main`. **It is the authority for Phases 2–4 — read it before continuing.**
  Its §12 open questions and §11 phasing are what the ranked list below draws from.
- **Earlier merges in this arc's prehistory:** `#1666` `0325668c` (closed the octo legend arc);
  `#1686` `c05e4f2d` (octo's own diff legend gained vim's `]c`/`[c` + fold motions, `\vs`/`\C`
  promoted) — **shipped to BOTH hosts**, `ship.sh` rc=0, and that is the surface a click still
  opens today.
- 🔴 **PHASE 0 + PHASE 1 ARE MERGED.** `devrc#1698` → squash **`7b827d13`**. Verified on
  `origin/main` by CONTENT (not ancestry): `movement_test.go` present, `scripts/run-go-tests.sh`
  present, `scripts/main-green-check.sh:424` reads `for tier in pytests nodetests gotests`.
- **This doc's own first revision merged too** — `devrc#1713`, squash `4b6c42a3`.
- ⚠ **#1698 was merged with CI still PENDING**, on the operator's explicit "merge now". The
  basis was a LOCAL run on the rebased tree: Go suite 5/5 packages ok, 199 passed across
  `test_runtime_shebangs.py`, `test_gate_reexec.py`, `test_git_repo_isolation.py`,
  `test_main_green_check.py`, `test_no_client_hostnames.py`. **That is a narrower claim than a
  green CI run** — no Tekton leg ever reported on the final head `f6103dc9`.
- **Deploy status, unchanged and still honest:** `mention-review` is **NOT deployed, NOT on
  PATH, and has never been seen on a screen.** The Alacritty click path still spawns
  `nvim-octo`. Phase 4 (retirement) remains gated on the operator using it for a real review.
- **Next unit of work is Phase 2** (write actions), plus the `gotests` CI leg in
  **homelab-talos** — see the ranked list.

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

## Next steps (ranked)
1. **Add the `gotests` leg to `devrc-ci-pipeline.yaml`** in **homelab-talos** (NOT devrc).
   ~7,000 Go lines and 119 Go tests have zero PR-time coverage. See the investigation above for
   the path and the trunk-is-deploy constraint. Repo: homelab-talos.
   forcing: gate — merged code with no gate on it.
2. 🔴 **CONFIRM OR REVERSE THE §12.4 ASSUMPTION BEFORE PHASE 2 CODE EXISTS.** The operator said
   "proceed" without answering directly, so Phase 2 is being specced **PR-level comments only,
   NO inline diff-line positioning**. That is an ASSUMPTION recorded as one, not an answer.
   Reversing it before code is cheap; after Phase 2 lands it is a rework. Repo: devrc.
   forcing: user — only the operator can settle it.
3. **Phase 2 — write actions**: comment · approve · request changes · submit review · merge,
   all behind the confirmation ledger of proposal §3.7. **Reintroduce the write-intent ledger**
   deleted in #1698 (`Confirmed`/`NotConfirmed`/`LedgerViolations`/`Write()`), which was removed
   as a phasing judgement on the explicit understanding that Phase 2 brings it back. Repo: devrc.
   forcing: none
4. **Phase 3 — speed work**: local-clone probe, PR-ref fetch **RACING** the API, bounded on-disk
   cache (0600), per-commit diff. 🔴 A race, never a preference — see Gotchas. Repo: devrc.
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

## How to verify
```bash
# Phase 0+1 are on main — verify by CONTENT, never by ancestry (squash merges break ancestry):
git -C ~/workspace/devrc cat-file -e origin/main:nix/pkgs/tools/mention-review/src/internal/ui/movement_test.go
git -C ~/workspace/devrc cat-file -e origin/main:scripts/run-go-tests.sh
git -C ~/workspace/devrc show origin/main:scripts/main-green-check.sh | sed -n '424p'
#   -> for tier in pytests nodetests gotests; do

# The Go tier (still the ONLY thing that runs these 119 tests at PR time: nothing):
nix develop ~/workspace/devrc -c bash scripts/run-go-tests.sh .

# 🔴 THE MOVEMENT COVERAGE — the live defect #1698 closed. Invert the four arms in
# internal/ui/app.go:283-290 (PageUp/PageDown swap, Top<->Bottom); movement_test.go
# MUST go red with literal values ("G from line 100 -> diffCur = 0, want 201").
# POSITIVE CONTROL: internal/udiff/udiff.go:309  h.LineIndex > from -> >= from
#   kills 3 tests across 2 packages.

# 🔴 THE GATE LEDGER — remove the continuation join in
# scripts/tests/test_gate_reexec.py (`body = re.sub(r"\\\n[ \t]*", " ", body)`);
# test_the_derivation_sees_a_refusal_list_WRAPPED_ACROSS_LINES MUST go red.

# Has the gotests leg landed yet? (rank 1's closing condition)
gh pr checks <any open devrc PR> | grep gotests   # non-empty == done
```
