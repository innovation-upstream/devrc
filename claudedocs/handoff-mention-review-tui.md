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
- 🔴 **THE SPEC IS `claudedocs/proposal-pr-review-tui.md`** (#1696, `02b54fe4`). ⚠ Its rollback
  section is now WRONG — see the click-path bullet.
- **Lineage, verified by content on `origin/main`:** `#1696` `02b54fe4` (proposal) · `#1698`
  `7b827d13` (Phase 0+1 + the Go gate tier) · `#1723` `e49bbace` (Phase 2) · `#1728` `ba0c5aeb`
  and `#1729` `b34cdbe0` (this doc) · **`#1734` `588b6625` (the click flip)** · **`#1741`
  `b2a8e8ab` (J/K diff scroll)** · **`#1748` `ece01d4f` (the Files tree)**.
- ✅ **THE CLICK PATH IS `mention-review` NOW — `nvim-octo` IS SPAWNED BY NOTHING.** `REVIEW_EXE`
  and the Alacritty wrapper's `makeBinPath` both name it; a two-way ledger in
  `test_mention_open.py` fails if they disagree. Verified live under the wrapper's OWN PATH:
  `tui_available() = True` resolving to the deployed store path, with the negative control
  `which("nvim-octo") = None`.
  🔴 **ROLLBACK IS A REVERT PLUS A `home-manager switch`, NOT "flip one line".** Octo was on no
  PATH but that wrapper's, so flipping `REVIEW_EXE` back alone spawns a binary that is not
  installed — a terminal that flashes and vanishes. Its derivation and 75 tests are untouched
  (this was NOT Phase 4), so the revert is clean.
- ✅ **SHIPPED TO BOTH HOSTS, `mention-review 0.3.0`**, at `61faa675`: 618/577 managed artifacts,
  0 dangling, 0 stale, `2 hosts compared`. The deployed binary carries the tree on both, checked
  with a positive AND a negative control.
- ✅ **What a click now gives you:** the directory tree (`h`/`l`/`enter`, directories above files
  at every level, single-child chains compacted), `J`/`K` panning the diff 3 lines from any
  panel, and the collapse state + cursor surviving a write-triggered re-read (restored BY PATH).
- ✅ **THE `gotests` CI LEG IS LIVE** (`ZacxDev/homelab-infra#827` → `a05f355da` on `trunk`;
  GitOps, so the merge WAS the deploy). Measured `pass=119` on the first devrc PR after it,
  `pass=231` on #1723, `pass=293` on #1748 — the Go tests gate PRs, and `gotests` is the leg
  that carried every TUI change this session.
- **§12.4 IS ANSWERED, not assumed:** PR-level comments only, no inline diff-line positioning.
  Operator decision 2026-09-15. Nothing in the code computes a diff position.
- **The `pkill -x nvim` incident cost nothing** — operator confirmed no editor was lost. The
  prohibition stays in every dispatch brief regardless, and none of this session's five
  dispatches used a process-name pattern.
- ⚠ **Supersedes the 0.2.0 deploy line:** both hosts were verified at `mention-review` 0.2.0 on
  2026-09-16; they are now at **0.3.0**, re-verified the same way (wrapper → wrapped binary,
  positive AND negative control).
- 🔴 **THE CLOSING CONDITION IS STILL OPEN.** The operator said *"it looks good"* about an
  AGENT's driven report and asked for it on the click path — a strong signal, and NOT the line
  this doc froze: a REAL review, on a real screen, said to beat octo at reading a diff. Nothing
  in this session closes it.
- ⚠ **`ece01d4f` was merged over an UNATTRIBUTED red** (`pytests`, 5 of 23,895) on the operator's
  explicit "get it shipped". `gotests` — the leg that covers it — was green at `pass=293`. The
  red turned out to be the `main` outage below, reachable by no Go diff.

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

### RESOLVED — `main` was RED for hours; a skill PRUNE cut text that two-way ledgers pin
- as-of: 2026-09-17
- **Retracted as live.** Fixed by `#1756` → `61faa675`, shipped to both hosts. History below.
- **Symptom + exact repro:** `nix build .#checks.x86_64-linux.pytests` → 17 failures; a dev-host
  run of the three handoff suites → 13. Every PR's `pytests` leg red regardless of its diff.
- **Observed (with values):** `d2844af6` (#1750) pruned `claude/skills/handoff/SKILL.md`
  27,419 → 19,421 B and removed **all 8 refusal markers** across rules (j) and (k) —
  `[no via: field]`, `[no forcing: field]`, `[unknown kind`, `[unparsed`, `[fenced]` — which
  `scripts/lib/handoff_doc.py` still prints. 17 pinned sentences total. Assertion text:
  *"the module prints '[no via: field]' for rule (k) and claude/skills/handoff/SKILL.md never
  mentions it — an executor hits an undocumented marker at the moment it is about to push."*
  via: measurement
- **Ruled out:** that PR #1748 (the Files tree) caused it — the sandbox tier reproduced all 17
  on a tree built BEFORE that merge, and the PR touches zero `.md` and nothing outside
  `internal/ui`. via: measurement
- **Ruled out:** that it was a TWO-TIER divergence — **this was MY first diagnosis and it was
  WRONG.** I tracked the one test CI NAMED (`test_every_red_paragraph_is_a_ledger_rule…`),
  which genuinely passes on the dev host; the other 16 fail in BOTH tiers. A CI summary names
  ONE failure, never the failure set. via: measurement
- **Ruled out:** that restoring the text would breach the byte ceiling and force an eviction —
  I presented that as a fork needing an operator decision BEFORE measuring it. Budget is
  `MAX_BYTES 21,200 − MIN_HEADROOM 900 = 20,300`; the file was at 19,421, so there were **879
  bytes** free. There was no fork. via: measurement
- **Next probe:** none — closed. ⚠ Residual: the fix lands at **20,088 B, 212 bytes of
  headroom**. The squeeze that caused this is paid down, not resolved.

## Next steps (ranked)
1. 🔴 **USE `mention-review` FOR A REAL REVIEW — just click a `repo#N` mention.** It is the
   click path on both hosts at 0.3.0; nothing needs running by hand any more. This is the arc's
   closing condition and nothing headless substitutes: an agent has confirmed it renders,
   navigates, paints in ~1.0s and survives a write, which is evidence FOR the judgement, not the
   judgement. Repo: devrc.
   forcing: user — the closing condition names the operator reading real evidence.
2. **Phase 3 — the speed work**: local-clone probe, PR-ref fetch **RACING** the API, bounded
   on-disk cache (0600), per-commit diff. ⚠ Read the ~1.0s first-paint measurement in Gotchas
   BEFORE scoping: the FIRST read is already at the API floor, so the win is the 2nd..Nth.
   Repo: devrc.
   forcing: none
3. **Phase 4 — retirement**, its own PR: delete `nix/pkgs/tools/nvim-octo/` (3 files),
   `test_nvim_octo.py` (67 tests), `test_nvim_octo_diff_motions.py` (8), the `nvimOctoOverlay`,
   `luajit` from `REQUIRED_TOOLS`, and the PROSE-ONLY `nvim-octo` row in
   `test_no_real_launchers.py`. 🔴 **GATED ON RANK 1.** Repo: devrc.
   forcing: none

## Defects (batched)
- 🔴 **`scripts/lib/handoff_doc.py`'s pins have only 212 B of headroom** in
  `claude/skills/handoff/SKILL.md` (20,088 of a 20,300 budget). The next addition needs an
  eviction in the SAME commit — the exact squeeze that produced the outage above. The durable
  fix is moving narrative into `claude/skills/handoff/reference/*.md`, which cost 0 until loaded.
- 🔴 **169 agent worktrees are registered in this clone**, oldest 2026-08-13. A stale worktree
  holds its branch repo-globally: it forced a detached rebase earlier in this arc and blocked a
  branch checkout twice today. `scripts/worktree-prune` exists. Not run — removing 169 at once
  is high blast radius and some may belong to other sessions.
- `movement_test.go`'s `paging_UP_onto_src` case cannot catch a dir-row-moves-the-diff mutant:
  `src`'s first DIFF-ORDER descendant is the file the cursor was already in. Two of three
  subtests catch it. Pre-existing, reported rather than papered over.
- Two guards still claim more than they check: `scripts/tests/test_mention_review.py:122`
  (claims it pins the tier set; does not check `main-green-check.sh`) and `:361` (claims a slug
  check; there is none). The third — `internal/ui/words.go:179-184` — was CLOSED by Phase 2.
- `keys_test.go`'s OLD structural half was replaced in #1748 — ⚠ and the handoff's description
  of that defect was WRONG: it said the guard "asserts the keymap agrees with itself", but
  `Dispatch()` and `FullHelpFor()` are separately maintained, so a missing legend entry DID
  fail. The real gap was narrower: a binding present in both whose action nothing implements
  (`act()` falls through to `move()`, which ignores unknown actions). Fixed with a behavioural
  ledger over 22 reachable states.
- Round 0 on homelab#827 left three deletion candidates, all PRE-EXISTING, none blocking (D1 the
  13-site per-leg `*-context` param chain; D2/D3 per-leg duplication in
  `test_devrc_notify_empty_context_guard.py` and `test_nix_cache_persistence.py`).

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

- ✅ **MEASURED 2026-09-16 — TIME-TO-FIRST-PAINT IS ~1.0s, AND THAT VINDICATES THE ARC'S
  CENTRAL FINDING.** Three runs against the 8-file PR #1726: **2.0s / 1.0s / 1.0s** (100ms poll
  granularity, so read as ~0.9–2.0s). Against this doc's own earlier measurement of a REST diff
  at **0.66–0.90s**, first paint is essentially **one API round-trip** — the program adds
  almost nothing on top of the network. 🔴 **Consequence for Phase 3: the FIRST read is already
  at the floor and cannot be meaningfully improved.** The race/cache buys the 2nd..Nth read.
- 🔴 **I MANUFACTURED A 32.5s FALSE MEASUREMENT AND ALMOST REPORTED IT.** A `tmux send-keys 'q'`
  sent WITHOUT `Enter` left a stray `q` on the shell's command line; the next `send-keys` then
  ran `qmention-review …` → command not found → my poll loop ran to its 300×0.1s cap and I
  recorded **32,489 / 32,633 / 32,452 ms** as "time-to-first-paint". **The tell was that all
  three were identical AND equal to the loop's own ceiling** — a measurement that reproduces
  perfectly because it is measuring the instrument. **Fix that generalises: make the poll
  loop carry a `matched=0/1` flag and print it**, so "found it" and "gave up" stop sharing an
  output. A timing harness with no match flag reports a timeout as a number.
- 🔴 **`tmux kill-session`/`kill-server` on the DEFAULT socket is blocked by `bash-guard.py`,
  and correctly** — it takes the operator's live panes and every Claude conversation in them.
  To drive a TUI headlessly, make your OWN server: `tmux -L <probe> new-session -d …` then
  `tmux -L <probe> kill-server`. `kill-pane`/`kill-window` are allowed on the shared server;
  `kill-session` has no permitted spelling there.
- **Driving this TUI from an agent is practical and worth repeating.** Detached tmux on a
  private socket + `capture-pane -p` reads the rendered frame; `send-keys` drives it; nothing
  touches the operator's screen. 🔴 **Read the PANE TITLE (`> 4 Diff <path>`) for state, not a
  fixed pane line** — a line-number probe conflates the sticky file header with viewport
  content and will invent findings. I flagged a title/header "disagreement" that was NOT a bug:
  on a short file the viewport does not need to scroll, so line 2 is still the top of the
  document while the title correctly names the current file.
- **What the TUI does better than octo, observed rather than designed:** (a) `?` expands the
  FOOTER into a 5-column key table instead of opening a modal — the legend never covers the
  diff, which is the exact complaint the octo legend arc was about; (b) the pane title tracks
  the current file through `}`/`{`, so you always know where you are without the file panel;
  (c) `STATE`/`REVIEW`/`MERGE`/`CHECKS`/`THREAD` answer the review questions in one block;
  (d) `VIEWER as ZacxDev` is on screen AND in the footer — the two-account `hosts.yml` hazard
  is visibly mitigated, not merely documented.
- 🔴 **AN AGENT MUST NOT PRESS A WRITE KEY, AND THAT LIMIT IS LOAD-BEARING.** This session drove
  the TUI read-only — `?`/`j`/`k`/`g`/`G`/`]`/`[`/`}`/`q` only; `c`/`a`/`R`/`v`/`m` never sent.
  The five write verbs act on real GitHub as the operator.
- ⚠ **`gh pr view --json mergeable` returned `UNKNOWN` immediately before merging #1728**,
  because `main` had just moved (#1726 landed). It is a RECOMPUTE, not a conflict: it resolved
  to `MERGEABLE`/`CLEAN` on the next poll. **Poll it — never merge on a stale `CLEAN`, and
  never read `UNKNOWN` as a blocker.**
- ⚠ **#1628 carries a RED `tekton/devrc-pytests` that its own diff cannot reach** — failing
  test `test_concurrent_sessions_all_emit_their_control_key` (2 failed of 23,096 collected)
  against a gzip-header fix, on a branch **108 commits behind `main`**. This is the FIFTH
  instance of the inherited-red pattern already recorded in this doc. The pattern is now so
  well attested that the cheap move is mechanical: **read the failing test's name and the
  branch's distance from `main` BEFORE reading the diff.**

- 🔴 **A GUARD EXPRESSED THROUGH ITS OWN CONSTANTS IS INVARIANT UNDER SWAPPING THEM.**
  `TestTheChevronsAreOnScreenAndSurviveColourRemoval` asserted via `chevronExpanded`/
  `chevronCollapsed`, so **I swapped their VALUES and the whole package stayed GREEN** — every
  expanded directory rendering `▸` and every collapsed one `▾`, the open/closed state backwards,
  suite passing. `rowShape` spells `"dir-open"`/`"dir-closed"`, so the shape tests were blind
  too. Fix: assert the literal bytes once. **Ask of any text guard: can it pass while the hazard
  exists in a different spelling?**
- 🔴 **RESTORING PRUNED PROSE: PUNCTUATION AND PLACEMENT ARE LOAD-BEARING.** `TestSkillAndModule
  Agree` pins the WHOLE NORMALISED CLAUSE. My restore added a period after `it**` and sat one
  line above another paragraph with no blank line, so the normaliser JOINED them — it cleared 5
  failures and INTRODUCED 2. Derive the expected text from the test/module constant
  (`_FENCED_LEGEND`, `_NO_PROMOTE`), never from memory or composed prose.
- 🔴 **`nix build … | tail` EXITED 0 WITH NO VERDICT AT ALL**, beside an
  `error (ignored): SQLite database … is busy`. The build had not reported. Read the runner's
  own `RESULT:` out of `nix log <drv>` with a positive control (count the `passed` lines) —
  never the piped status. Both sandbox runs this session had to be read that way.
- 🔴 **MEASURE BEFORE PRESENTING A FORK.** I offered the operator a choice between "restore the
  text" and "relax the ledger" on the belief that the byte ceiling made them exclusive. It
  didn't — 879 bytes were free. A fork presented without measuring costs a decision the operator
  should never have been asked for.
- 🔴 **`ship.sh` rc=7 SKIPPED THE WORKBENCH while printing healthy per-host lines.** A working
  copy of `claude/skills/audit-pr/SKILL.md` blocked the fast-forward. **Byte-identical to a
  NEWER commit (`origin/main`) proves PRE-APPLIED content, not WIP** — the mirror of RULES.md's
  stale-orphan rule, and equally safe to discard. Hash before deciding; copy aside anyway.
  **Read every per-host line, never the final verdict.**
- 🔴 **FLIPPING `REVIEW_EXE` BROKE 22 TESTS THAT HAD NOTHING TO DO WITH THE TUI.**
  `tui_available()` is `shutil.which(REVIEW_EXE)`, so every test asserting a click reaches the
  BROWSER was really asserting *"the review TUI is not installed here"* — an invariant the file's
  comment CLAIMED and nothing enforced. True by accident while octo was on no PATH; false the
  moment `mention-review` entered `home.packages`. ⚠ **The two tiers would have DISAGREED** —
  the dev host has the binary, the `nix build` sandbox does not — so the same commit was RED
  locally and GREEN in CI. Now pinned by an autouse fixture.
- 🔴 **THE FIX ROUND'S OWN PROSE IS THE LIKELIEST NEXT FINDING, AND IT HAPPENED TWICE.** A
  commit whose whole subject was "a comment must not assert what it has not established" asserted
  `moveIn` is called "in `stepKey`" — MEASURED, `stepKey` ends at line 312 and the call is at 508
  inside `move()`. It came from MY brief wording it loosely; the fixer checked, found it
  imprecise, and substituted a DIFFERENT wrong name. **If you cannot state a true reason, write
  that it has none.**
- ✅ **MEASURED — time-to-first-paint ~1.0s** (3 runs, 8-file PR, 100ms poll). Against this doc's
  own 0.66–0.90s REST-diff figure that is essentially ONE round-trip: the program adds almost
  nothing. Phase 3 cannot improve the first read.
- 🔴 **I MANUFACTURED A 32.5s FALSE MEASUREMENT.** A `tmux send-keys 'q'` with no `Enter` left a
  stray `q` on the command line, so the next send ran `qmention-review …`; my poll loop ran to
  its 300×0.1s cap and I recorded the cap as the number. **The tell was three identical readings
  equal to the ceiling.** Any bounded wait returns a plausible duration on failure unless success
  is recorded separately — carry a `matched=0/1` flag and PRINT it.
- **Driving the TUI from an agent works and is repeatable:** detached tmux on a PRIVATE socket
  (`tmux -L <probe>`), `capture-pane -p` to read frames, `send-keys` to drive. Read the PANE
  TITLE (`> 4 Diff <path>`) for state, never a fixed line number — a line probe conflates the
  sticky file header with viewport content and invents findings. `kill-session`/`kill-server` on
  the DEFAULT socket is blocked by a hook, correctly.
- 🔴 **AN AGENT MUST NOT PRESS A WRITE KEY** (`c`/`a`/`R`/`v`/`m`) — they act on real GitHub as
  the operator. Every dispatch this session carried that, and none was pressed.

## How to verify
```bash
# 🔴 RANK 1 — the closing condition. Just click a repo#N mention in the terminal.
#   ? legend · j/k line · J/K pan diff · ]/[ hunk · }/{ file · h/l/enter tree · tab panel · q
#   ⚠ press j once before your first ] — see Defects.

# The click path, end to end, WITHOUT raising a window (the predicate a click runs):
W=$(grep -oE '/nix/store/[a-z0-9]+-alacritty-mention-open' \
      "$(readlink -f ~/.config/alacritty/alacritty.toml)" | head -1)
WPATH=$(grep -oP '(?<=export PATH=)[^\n]*' "$W" | head -1 | sed 's/:\$PATH$//')
env -i PATH="$WPATH" python3 -c "
import importlib.util,os,shutil
s=importlib.util.spec_from_file_location('mo',os.path.expanduser('~/workspace/devrc/scripts/mention-open.py'))
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
print('REVIEW_EXE',m.REVIEW_EXE,'| tui_available',m.tui_available(),
      '| NEG CONTROL nvim-octo',shutil.which('nvim-octo'))"
#   -> mention-review | True | None

# The DEPLOYED binary — follow the WRAPPER; `bin/mention-review` is a makeWrapper SCRIPT and
# grepping IT returns 0 on a HEALTHY deploy. Positive control FIRST.
B=$(readlink -f "$(command -v mention-review)")
R=$(grep -oE '/nix/store/[a-z0-9]+-mention-review[^/]*/bin/\.mention-review-wrapped' "$B"|tail -1)
grep -ac 'usage: mention-review' "$R"   # POSITIVE CONTROL — must be 1
mention-review --version                # 0.3.0

# The Go tier (the leg that gates this code at PR time):
nix develop ~/workspace/devrc -c bash scripts/run-go-tests.sh .   # internal/ui pass>=197

# 🔴 A SANDBOX verdict is NOT the piped exit code — one exited 0 having reported nothing:
nix build .#checks.x86_64-linux.pytests --no-link   # build ALONE; concurrent = false failures
nix log /nix/store/<drv>.drv | grep -E 'RESULT:|SCOPE:'
```
