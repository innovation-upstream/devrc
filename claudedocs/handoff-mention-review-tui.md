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
- 🔴 **THE ARC IS CLOSED** — the operator used `mention-review` for a real review **on a real
  screen** and reported **"done, working"** (2026-09-18); that is the `judgement` line this doc
  froze at round 1. ⚠ The condition also says *"beats octo at reading a diff"*, and
  `"done, working"` carries no comparison. Pre-existing gap.
- ✅ **CARRIED FORWARD — durable, would otherwise be dropped by the next replace:**
  `#1793` → `cee56910` (picker promotion, THREE gates narrower than authorised: promotes only
  when the ordering RAN, its top row is not already the pane guess, and that row is
  `CLASS_PLAUSIBLE` **and** uniquely separated) · `#1781` → `8d0984ba` · `#1772` → `2b131bf2` ·
  `#1773` → `97c20d06` · `#1775` → `66e51d90` · **`#1812` → `6ee5ff68`**. 🔴 **`--version`
  CANNOT distinguish builds** — verify by content with a positive control. **The picker is live
  with NO switch** — the Alacritty wrapper execs `scripts/mention-open.py` from the WORKING TREE.
- ✅ **SYMPTOM 1 — the arc's one unmet objective — IS DECIDED AND HALF-LANDED.** Operator,
  2026-09-20: **(D) then (A), (B) REFUSED**.
  - **`#1812` MERGED as squash `6ee5ff68` (2026-09-21)**, `--delete-branch` NOT passed. Payload
    is ONE executable line: `--bind="esc:print-query+abort"`, so an ESC records `queried`.
    🔴 **Verified by CONTENT, never ancestry** (a squash is never an ancestor of its branch):
    `git diff 17b6ff96 origin/main -- scripts/mention-open.py scripts/tests/test_mention_open.py`
    is EMPTY, positive control `git grep -c 'def ' origin/main -- scripts/mention-open.py` = 61.
    Four Tekton legs green **on that exact head sha**, read per-context from
    `/commits/<sha>/statuses` — never the roll-up, which maps `error` onto `failure`.
  - **`#1813` PUSHED, awaiting CI — head now `8c5fda78`** (was `9c4bb252`), base auto-retargeted
    to `main`. Every row carries its RANK (`1`,`2`,… / `-` unranked / `?` unstamped-bug), with
    `--nth=2..` keeping the marker out of fzf's haystack. Net payload vs `main`: **3 files,
    +554/−21**.
- 🔴 **`#1813` WAS `CONFLICTING`/`DIRTY`, NOT MERELY "NO CI POSTED" — the previous update of this
  doc had that wrong, and a kickoff line propagated it.** Its branch predated three of the
  parent's newest commits (`b17cf93f`, `26228216`, `17b6ff96`), so it conflicted against its own
  base before `main` ever entered it. Merged parent-tip-then-`main` in a throwaway worktree.
- ✅ **Merged tree verified locally: `648 passed, 2 skipped`** across `test_mention_open` +
  `test_mention_scan` + `test_mutation_battery_anchors`, at base `6ee5ff68`. That is **7 above
  the `641 passed, 2 skipped` the pre-`#1813` tree gives** — a number that did not move would
  mean the branch's own tests never ran. Collection: **643 → 650**. ⚠ **Dev-host tier ONLY**;
  the `nix build` sandbox tier that Tekton runs was NOT run here, and the two are blind to
  different things.
  🔴 **AN EARLIER VERSION OF THIS BULLET SAID `640/2` AND A DELTA OF 8, AND IT WAS FALSE —
  I COPIED IT OUT OF THIS DOC'S OWN `How to verify` BLOCK INSTEAD OF MEASURING IT.** That
  block's `640 passed / 2 skipped; collection … = 642` was true of an OLDER tree and had gone
  stale; restating it beside a freshly measured 648 dressed a stored number as a measurement.
  Caught by `/audit-pr` round 0 on `#1820`, which measured 641/2 two ways; confirmed here by a
  third full run and by collection counts. **This doc already carries the general rule — "A
  STORED MEASUREMENT IS NOT A LIVE ONE" — and the trap is that the stored number was in THIS
  FILE, where it reads as the project's own answer rather than as a claim to re-derive.**
- ⚠ **(A) SHIPPED NARROWER THAN OPTION (A) AS WRITTEN — rank only, no class column**
  (`a70839af`). Option (A) offered *"class **and/or** rank"*; shipping both was the implementer's
  reading, and `/audit-pr` round 0 logged the class field as UNATTRIBUTED. **The OPERATOR chose
  rank** — that is the author of record for the narrowing, and a sha is not. Measured: the
  click-time class of the picked row is `below` **56** vs `plausible` **17**, so a class column
  would read "the ranker does not trust this" on the WANTED row ~3 times in 4. 🔴 **Do not
  "restore" the class column on the strength of that deviation** — it was declined, not omitted.
- ⚠ **`#1812` MERGED WITHOUT ITS ROUND-3 DELTA AUDIT, AND THE LADDER'S OWN RULE SAYS A CLEAN
  ROUND ENDS IT — round 2 was NOT clean.** The merge followed this doc's rank 1 and the
  operator's kickoff, and the round-3 item was `forcing: none`/advisory; recording it so the
  gap is a decision on the record rather than an omission nobody noticed.
- **No `clawgate-task:` field** — `clawgate_handoff.sh resolve` exited **5** again. Its positive
  control (2 links for a different session) shows the board is reachable, but a wrong session id
  also answers 200 with an empty array, so this is NOT a clean bill of health.
- 🔴 **METHODOLOGY LESSONS ARE IN THE CAIRN INDEX, NOT HERE** — `devrc/scripts` and
  `devrc/tests`, both 2026-09-21, carrying the fzf contract (abort is FOUR keys; bind ACTION
  ORDER is load-bearing and a reorder is silent; `--nth` excludes a marker from MATCHING but not
  RANKING). `cairn recall --repo ~/workspace/devrc`.

## Open investigations — live diagnosis state

### EVICTED — six CLOSED blocks (2026-09-21, byte ceiling)
- Every block below had been RETRACTED AS LIVE by its own author and carried a
  `Next probe: none — closed.`: the two `gotests` CI-leg blocks (located, then
  resolved), `main` RED from a skill prune that cut text two-way ledgers pin
  (`#1756` → `61faa675`), the eight re-anchored mutation rows KILLING, ranks 1
  and 3, and `main` red on `test_opencode_engine` (`#1804` → `7ef01c05`, whose
  lesson — a stale-looking pin where the PINNED version was the good one — is the
  one worth remembering). 🔴 Evicted because this update put the doc **216 B over**
  its 65,536 B ceiling and `test_no_handoff_doc_exceeds_its_budget` fails for
  EVERYONE, not just this PR. The playbook's own order: evict what has CLOSED
  first, and raising the number is LAST. History: `git log -p` this file.
- ⚠ **Carried forward from the replaced status header, because it is a DECISION and
  not a status:** §12.4 is ANSWERED — PR-level comments only, **no inline diff-line
  positioning** (operator, 2026-09-15); and the 422 arc is CLOSED (`#1761` →
  `1ff1bd6e`) — the renderer discarded `errors[]`.

### EVICTED — two superseded blocks (2026-09-20, byte ceiling)
- The 2026-09-14 "Go tier has NO CI leg" block (superseded by the 09-15 block below,
  which is itself retired by the 09-16 RESOLVED one) and the 2026-09-18 "eight rows
  APPLY but are not known to KILL" block (retired by the RESOLVED block that records
  its apply-vs-kill correction). 🔴 Evicted because this doc was 229 B from its
  ceiling and the playbook says evict what has CLOSED before anything else — the
  surviving RESOLVED blocks carry both outcomes. History: `git log -p` this file.

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

### EVICTED — the 422 diagnosis block (2026-09-21, byte ceiling)
- The 2026-09-18 block "The 422's actual cause was never determined". 🔴 Evicted by `/audit-pr`
  round 0 on `#1820`, which measured it as the doc's last CLOSED-but-retained block (1,168 B)
  while headroom was 1,294 B. Its conclusion is already carried above: the 422 arc is CLOSED
  (`#1761` → `1ff1bd6e`, the renderer discarded `errors[]`). Its ruled-outs — a read failure, a
  merge-method problem, and that the `picks.jsonl` timeline PROVES base-recompute (it does not:
  two entries prove two OPENS, not two merge presses) — are in `git log -p` on this file.
- 🔴 **THE ONE LIVE INSTRUCTION IT CARRIED, KEPT because eviction must not delete it:** the cause
  was never established and the body is unrecoverable, but the shipped renderer now surfaces
  `errors[]`, so **the next occurrence names its own reason. If it recurs, capture the card
  verbatim before anything else** — that single string settles it.

### 🔴 Why right-aligning the rank marker moves fzf's ranking — MECHANISM UNKNOWN
- as-of: 2026-09-20
- **Symptom + exact repro:** rendering the rank `f"{rank:>3}"` instead of `f"{rank:<3}"` changes
  fzf's output order. 120-row synthetic corpus, 20 queries,
  `fzf --filter <q> -i --tiebreak=end --nth=2..`, marker present in both arms.
- **Observed (with values):** right-aligned — top-1 changed on **7 of 20**, tail on 13 of 20.
  Left-aligned — top-1 on **0 of 20**, tail on 1 of 20. Match SET identical 20/20 both. fzf
  0.74.4. via: measurement
- **Ruled out:** that the marker becomes MATCHABLE under right alignment — this was the shipped
  explanation and it is FALSE. A query matching only the marker digits returns **0 rows under
  BOTH alignments**; positive control, 1 row with `--nth` dropped. via: measurement
- **Ruled out:** that field-1 WIDTH explains it. Width genuinely does reach fzf's positional
  tiebreak (identical field-2.. text at widths 3 vs 6 inverts; input order once equalised, and
  under `--tiebreak=index`) — but `:<3` and `:>3` are the SAME width, so offsets are unchanged
  between the two alignments. via: measurement
- **Leading hypothesis:** none. Recorded as unknown deliberately — the first explanation read as
  well as a true one and was wrong; a second invented under pressure would be a hypothesis
  wearing a comment's clothes.
- **Next probe:** `fzf --filter` both alignments over a corpus where every rank has the SAME
  digit count (ranks 100–199). Difference vanishes ⇒ digit-count variation is the cause; persists
  at constant digits AND width ⇒ it is in fzf's scorer, worth reporting upstream.

## Next steps (ranked)
1. **SHIP symptom 1 to both hosts and verify it by CONTENT.** ✅ The merge half is CLOSED —
   `#1813` merged as `7f39fda7` off head `8c5fda78` with all four Tekton legs green
   (`pytests` collected **23,831**, up 28 from `#1812`'s 23,803, so the branch's own tests
   demonstrably ran). What REMAINS is `scripts/ship.sh` and the verification, which had not been
   done when this was written. Repo: devrc. 🔴 **Read every per-host line, never the final
   verdict** — one skip hides among greens. 🔴 **`--version` cannot answer "is it deployed"**:
   `bin/mention-review` is a `makeWrapper` script, so follow `exec -a` to
   `bin/.mention-review-wrapped` and grep THAT, positive control first. ⚠ The picker half
   (`scripts/mention-open.py`) is live with **no switch** — the Alacritty wrapper execs the
   working tree — so a `git pull` of the primary clone already activates it; the switch is for
   the Go binary. ⚠ Do not ship while an agent is mid-run in this repo: a switch blanks
   `~/.nix-profile` for ~31–35 s and every bare-command invocation dies "command not found".
   forcing: user — the operator chose (D) then (A) on 2026-09-20; shipping is that decision
   reaching the machine they actually use.
2. **Round 3 delta audit of `#1813`, scoped to round 2's fixes plus the two merge commits.**
   Round 2 returned findings, so the ladder's rule says another round follows; a clean round
   ends it. Range is now `2f9af4f8..8c5fda78` (was `..9c4bb252`) and **includes `b116179f` +
   `8c5fda78`, which are merges** — a delta audit over a merge range reads differently, so state
   the range you actually gave it. Post the `audit-claims` block BEFORE dispatching: a delta with
   no parseable block is REFUSED, and a MISSING INTERMEDIATE one does NOT refuse — it silently
   anchors older and widens the range. Repo: devrc.
   forcing: none — advisory; nothing external waits on it.
3. **Decide whether `below`-dominance is the real defect.** The picked row's class is `below` 56 /
   `plausible` 17, and the proposal flags that NOT DIAGNOSED and *"a bigger finding than symptom
   1"*. Cheap mechanical test already exists: re-run the causal replay with the class term demoted
   below distance/score and read top-1 against the 12.7% observed. Untested mechanism worth trying
   first: many clicked `#N` are clawgate/ClickUp ids, not GitHub numbers, so no repo's range is
   relevant. Repo: devrc.
   forcing: regression — the shipped sort key ranks by a term that disagrees with 77% of real
   picks; #1813 makes that disagreement visible without fixing it.
## Defects (batched)
- 🔴 **NO `LICENSE` FILE WHILE THE DERIVATION CLAIMS MIT** —
  `nix/pkgs/tools/mention-review/default.nix:146` declares `licenses.mit` with nothing backing
  it, and the repo is PUBLIC.
- 🔴 **Agent worktrees accumulate in this clone and a stale one holds its branch repo-globally.**
  🔴 **NO COUNT WRITTEN HERE** — every count this bullet has carried went stale within hours
  (`181+` was already `348` registered when `/audit-pr` round 0 re-derived it), and unlike
  `drift-check.sh` rc 17 below there is **no deadman**: `scripts/worktree-prune` exists but no
  timer runs it. Count it yourself — `git -C $DEVRC worktree list --porcelain | grep -c '^worktree '`
  — then run the pruner.
- 🔴 **TEST-ISOLATION SEAM: every `ranges=` test in `test_mention_open.py` consumes
  `MO.PICKS_PATH`, which no autouse fixture redirects** — they read the operator's real
  `~/.config/mention-open/picks.jsonl`. Found by `/audit-pr` round 4, **pre-existing**, inert
  for those tests (Tier B sits below class and distance and cannot move a uniquely-PLAUSIBLE
  top row). Worth closing on its own.
- ⚠ **A COMMENT ON `main` COUNTS THREE GUARDS AND THEN LISTS TWO.** `scripts/mention-open.py`
  (search `THREE guards catch it`) reads *"THREE guards catch it … (the whole-string
  `EXPECTED_PICKER_SH` pin, and `_ESC_PRINTS_QUERY` …), but **neither** was CHOSEN for that."*
  The parenthetical enumerates two and the conclusion says "neither" — the correction to THREE
  landed in the lead sentence and was never carried into the list. **Pre-existing on `main`,
  byte-identical there — NOT a merge artifact** (checked, because it first surfaced inside a
  conflict resolution and read like one). Name the third guard or revert the count.
- ⚠ **`drift-check.sh` rc 17 on BOTH hosts, and the two have DIVERGED from each other** on
  `homelab-talos/containers/clawgate`. 🔴 **No ranked item, and NO COUNTS WRITTEN HERE** — the
  deadman reports it every 6 h with live numbers, and an earlier draft's counts were stale
  within hours. Run the checker.
- Pre-existing and known: `ghapi_test.go`/`argv_test.go` not `gofmt`-clean (confirmed on `main`
  at `2b131bf2`); four scanner limits documented in `detailrouting_test.go`; two guards at
  `scripts/tests/test_mention_review.py:122` and `:361` claiming more than they check.

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

- 🔴 **THE #1761 LADDER RAN FIVE ROUNDS (0–4) AND EVERY FINDING WAS CREATED OR MISSED BY THE
  ROUND BEFORE IT.** Round 0 questioned the requirement and killed a guard that was structurally
  unable to fire; round 1 found a token leak on a write path round 0's fix had just created;
  round 2 found a mutant surviving a green suite; round 3 found the same class in an ALIAS shape
  plus a fifth unbounded path; round 4 found two silent doors in the scanner round 3 built.
  **None would have been found by reading the diff.** This is the documented shape, not a sign
  anything went wrong — and it is why the stop condition is a clean round, never a round count.
- 🔴 **THE LADDER WAS STOPPED ON THE ATTRIBUTION NUMBER, NOT A CLEAN ROUND — say so.** Round 4's
  fixes changed **0 executable payload lines against 229 test lines**; round 3's changed **1
  against 635**. The trend across the ladder was `+275 → +150 → +52 → 0` payload. Round 4's
  findings were defects in the instrument round 3 had built. That is the `#498` shape the
  attribution gate exists to catch, and the operator chose to fix-and-merge rather than run a
  round 5 that would have audited test infrastructure for test infrastructure.
- 🔴 **A GUARD BUILT FROM ITS OWN CONSTANTS SURVIVES A MUTANT THAT CHANGES THEM.** `maxDetailRunes`
  400→4000 left the suite GREEN because the assertion read `len(runes) != maxDetailRunes` — phrased
  in terms of the very constant the mutant moved. Fixed with an absolute ceiling (`cardCeiling =
  600`) not made of that constant. **Ask of every new assertion: is its expectation made of the
  thing under test?**
- 🔴 **A FAKE THAT ANSWERS EVERY FIELD CANNOT SEE A QUERY THAT STOPPED ASKING.** Deleting
  `state merged` from `MergeableQuery` SURVIVED a green suite because the merge fake answered all
  fields regardless of the selection set. Fixed by having the fake parse the query document. The
  SAME blindness then turned out to be open on the PANEL query one function away — that mutant
  also survived, and killed the terminal-PR guard silently. **Ask which surface your fixture does
  NOT load.**
- 🔴 **AND THE FIX FOR THAT WAS ITSELF WALKABLE BY AN ALIAS.** With both fakes parsing queries,
  `prState: state` STILL survived — a real GraphQL server keys an aliased selection by the ALIAS,
  the fakes keyed by the field NAME. The document already contained an alias (`rollup:
  commits(last:1)`). Both extractors now return `field → response key`.
- 🔴 **AN AST SCANNER IS AN INSTRUMENT AND NEEDS ITS OWN CONTROLS.** `detailrouting_test.go`
  enforces that every `APIError.Detail` is routed through `c.detail` — and shipped seeing ONLY a
  keyed `Detail:` inside a composite literal. Two ordinary Go shapes walked straight through:
  `ae.Detail = "…" + err.Error()` after construction, and a POSITIONAL literal (which also still
  counted toward the anti-degenerate floor, so it RAISED the floor while checking nothing). Both
  now error. **Verify a scanner by injecting the thing it must catch, in the idioms a maintainer
  would actually write.**
- 🔴 **A LEDGER KEYED ON A WORD IS WALKABLE BY CHOOSING THAT WORD.** The scanner's exemption
  ledger keyed on the rendered expression text, so `name := "…" + err.Error()` in a DIFFERENT
  file and function was absolved by an entry whose reason was written about `write.go`'s argv.
  Keys are now `file.go:Recv.Func: expr`. **An excuse must not be able to travel.**
- 🔴 **THE SAME COMPLETENESS SENTENCE WAS FALSE FOUR TIMES IN ONE FILE, EACH TIME REWRITTEN BY
  THE ROUND THAT FIXED THE LAST ONE.** `query.go` now records the tally itself. The ending was
  NOT a fifth rewording: the sentences were replaced with a statement of the MECHANISM — what the
  scanner parses, what it requires, what it cannot see. **If a guard has lost its reason, write
  that it has none; reaching for a better one is what regenerates the error.**
- 🔴 **REDACT BEFORE YOU NORMALISE, NOT JUST BEFORE YOU CLIP.** A brief asked for
  `clipDetail(c.redact(x))` at the merge refusal; that would have done NOTHING, because
  `NormalizeMergeable` UPPERCASES the server string first and `redact` matches the exact token —
  so a lowercase credential shipped whole with its case changed. Only a case-insensitive
  assertion caught it. Redaction now happens before the uppercase (`query.go:542`).
- 🔴 **CONSOLIDATION SILENTLY CHANGED A CLASSIFICATION.** Merging two duplicated GraphQL error
  decoders into one helper removed both callers' own `errors` field, so a non-array `errors`
  stopped being `unreadable response` and became a confident, FALSE `NOT FOUND` — sending the
  operator to fix a token permission that was fine. Consolidation is right; **check whether the
  merged helper collapses a case either original distinguished.**
- 🔴 **A GENERIC API MESSAGE IS AN EMPTY RESULT.** "Unprocessable Entity" is consistent with
  several mechanisms and identifies none; GitHub puts the discriminating reason in `errors[]`.
  The first fix had to be the one that makes the failure self-diagnosing — building the guard
  first would have been building on an undiagnosed failure.
- ⚠ **A `gopls` "undefined: X / not in GOROOT" storm from an agent worktree is a WORKSPACE
  artifact, not a broken tree.** It appeared after every fix round in this arc (the worktree is
  not in a `go.work`). Each time, `go build ./...` and the full tier were clean. **Do not report
  it as a compile failure; build the pushed branch and read the runner's own `RESULT:` line.**
- ⚠ **A subagent's self-reported green is a claim.** Every round's numbers were re-run in this
  session's own worktree before being repeated, and every decisive mutant was re-applied here
  rather than relayed. Two agent-reported figures did not reproduce (a 5,117-rune measurement
  that moves with the ephemeral port; a payload count of +54/−18 that was +52/−16 — the latter
  was THIS session's error, from reading a `--remerge-diff` log spanning an extra commit).
- ⚠ **A SIGPIPE from `| head` killed a mutation script before its restore step**, leaving a
  mutant in the working tree. Caught by the next full run and restored from a `cp` backup, and
  the pushed commit was verified clean — but it is a silent-commit hazard. **Do not pipe a
  mutation battery through `head`.**
- **`--version` is not a deploy check when the version did not move.** The whole arc shipped
  under `0.3.0`. The store path changes (nix rebuilds on source hash) but the label does not, so
  a deploy must be verified by grepping the WRAPPED binary for a string only the new code has,
  with a positive control first.

- 🔴 **RETRACTED — "the FIRST read is already at the floor and cannot be meaningfully improved"
  and "Phase 3 cannot improve the first read" (both above, in this section) ARE FALSE. MEASURED
  AND REFUTED 2026-09-18: `1,212 ms → 735 ms`, a 39% cut on the FIRST read.** Those sentences
  were written from a `~1.0s` time-to-first-paint reading that never distinguished *first frame
  on screen* from *diff readable*. The cold open was making **two sequential round-trips**:
  `App.Init()` returned one command, and the REST diff was emitted only inside `case PRLoaded:`.
  Owner/name/num come from argv and were known at `Init()` time — nothing forced the
  serialization. 🔴 **The retracted wording had been promoted into the RANKED LIST as an
  instruction to read it before scoping Phase 3** — i.e. the false claim was actively steering
  the next session away from the win. Deleted there in this same update.
- 🔴 **A STORED MEASUREMENT IS NOT A LIVE ONE, AND THAT ERROR COST A REVERSED DECISION.** An
  audit argued the skeleton-paint half of Phase 3 was worthless by reasoning from the proposal's
  *recorded* M5/M6 figures (GraphQL 0.54–0.69 s, REST 0.66 s) to conclude REST was the long pole
  so the early-diff path never fires. I relayed that to the operator as established and they
  decided on it. Then the legs were **timed live**: `t_rest` **666 ms** vs `t_graphql` **855 ms**
  — REST first in 5 of 5, so the path fires on *every* open. `922 − 735 = 187` closes against
  `855 − 666 = 189` to within 2 ms. The decision was reversed on the new data. **Re-take a
  measurement before inferring a mechanism from it.**
- ✅ **THE THREE-WAY LATENCY TABLE, interleaved, n=5, private tmux socket, 12–16 ms poll, every
  reading `matched=1`:** base in series **1,212 ms** · concurrency only **922 ms** ·
  concurrency + skeleton **735 ms**. `t_first_frame` 277 / 285 / 287 ms is a **null** — no
  consistent direction, and an earlier "+26 ms in 5 of 5" was run-ordering, not the skeleton.
  ⚠ **Interleave the conditions; do not run blocks.** Two base blocks minutes apart gave medians
  of 1,190 ms and 847 ms — the host drifts faster than one block of five.
- ⚠ **The leg inversion is a fact about THIS HOST AT THIS MOMENT, n=5.** It justifies the
  skeleton, but it is not a permanent property of GitHub's API: if the REST leg ever becomes the
  long pole again, the skeleton's 187 ms goes to zero **without anything failing**. Said in
  `ReadIntents`' comment, repeated here.
- 🔴 **THE PICKER RANKING HAD LANDED AND WAS RUNNING — IT WAS DEGRADING WITH USE.** The operator
  reported it "doesn't seem to have landed or be working". It shipped in #1509/#1569 and a click
  on `#1761` genuinely ranked 4 repos above 391. The real defect was in the sort key
  `(klass, -score, distance)`: **every repo ever picked earns a non-zero score**, so a warming
  log let more and more rows outrank Tier A's correct first choice. Causal replay, 115 picks over
  6.7 days: top-1 **82.5% → 43.1% → 33.3%** across the log. **"Wrong repo on top" was a TREND,
  not a static bug.** Fix: `(klass, distance, -score)` — Tier B separates only what Tier A
  cannot. top-1 `62.6% → 73.0%`, top-3 `→ 96.5%`, mean rank `3.18 → 2.75`.
- ⚠ **DISCLOSED REGRESSION in the picker fix:** on a **cold** log the old key is better
  (82.5% vs 68.4%). The log only grows so the warm regime is operative — but if `picks.jsonl` is
  ever wiped, ranking is worse for a while. Do not "reset" that file casually.
- **No constant was tuned, and the sweep is why.** Confidence floors at 0.5/1.0/2.0/5.0 all give
  **62.6%**, identical to the shipped key, exactly as the half-life sweep was inert. **A sweep
  that moves nothing is evidence the STRUCTURE is wrong, not that the tuning needs more range.**
- 🔴 **THE AUDIT LADDER RAN FIVE ROUNDS (0–4) AND ENDED ON A CLEAN ROUND. Real code defects were
  found in rounds 1 and 2 ONLY; rounds 3 and 4 were about PROSE.** Executable payload lines per
  round: **23 / 8 / 0**. Round 2 found four false load-bearing claims in round 1's own fix, three
  tilted to make the fix look better justified. Round 3 found the round-2 fix had **reintroduced
  the exact defect it fixed** (a comment crediting a renamed test) in the artifact it edited in
  the same commit. ⚠ **The attribution gate never fired** — comments in shipped source count as
  payload under the classification chosen at round 1 and held to, so the count never reached 0.
  **The clean round is what ended it.**
- 🔴 **THREE MUTATION-HARNESS DEFECTS, EACH REPORTING SUCCESS WHILE DOING LESS THAN CLAIMED.**
  (a) a mutant that **stopped compiling** scored SURVIVED off an empty failing-set; (b) a mutant
  that **panicked** killed the whole test binary so its intended killer never ran; (c) a restore
  list naming `scripts/collector/session-tailer.py` when the file is
  `scripts/collector/claude/session-tailer.py` — a safety net with a hole exactly where the
  failures land, which would have reported CLEAN while skipping the dirty file. Fixes:
  build-failure and bare panic now record `<did-not-run>` rather than survival; killers run
  **isolated**; and the restore moved OUT of a `finally` inside the killable process into a
  **wrapper** that restores by explicit path and then verifies with an **independent
  `git status`**. Paths taken from the battery's own constants, never retyped.
- 🔴 **A `finally` CANNOT RESTORE A PROCESS THAT IS KILLED — three times now, and the THIRD
  names the mechanism the first two did not.** Each time the battery died and left a one-line
  mutation in a working tree, a silent-commit hazard; the tell is exactly one dirty path.
  **The third (2026-09-19) was caused by the HARNESS, not the battery: the Bash tool caps a
  call at 10 minutes and SILENTLY CLAMPS a larger `timeout` argument**, so a 2,400 s run was
  SIGTERM'd (exit 143) mid-battery, leaving a live `emit_click`/`surface` reordering mutant in
  `scripts/mention-open.py`. 🔴 **The fix is not a bigger `timeout` value — it is
  `run_in_background`, which is not subject to the cap.** The base clone was CLEAN only because
  the run was in a throwaway worktree; that isolation is what made it harmless. Restore by
  explicit path, then verify with an INDEPENDENT `git status` scoped to the battery's own
  paths.
- 🔴 **`pgrep -f <pattern>` MATCHED ITS OWN SHELL AND I READ IT AS EVIDENCE — twice, by two
  different actors in one session.** `pgrep -c -f 'mutation_battery'` returned a non-zero count
  with **no battery running**; "it's still running, just slow" was never a measurement. Resolve
  PIDs and read `/proc/<pid>/cmdline` and `/proc/<pid>/cwd`.
- 🔴 **THREE OF MY OWN INSTRUMENTS RETURNED CONFIDENT WRONG NUMBERS THIS SESSION**, and the
  positive control caught every one. (a) `grep 'func readPair'` returned 0 on a **successful**
  merge — the symbol is `func (a App) ReadIntents()`, a method with a receiver; the negative
  control beside it was worthless until re-run with a pattern proven to fire. (b) A privacy scan
  using naive substring containment flagged a 3-character repo name inside the token `tier_b`.
  (c) A per-file test-count comparison joined nothing because pytest emits rootdir-relative
  paths and the worktree lives *inside* the repo — every row read `0 -> N`.
- 🔴 **A COUNT OF DECLARATIONS IS NOT A COUNT OF INSTANCES, AND IT PRODUCED A FALSE ALARM.**
  Phase 4's collected-test total dropped by **221** while the deletion was described as "67 + 8
  tests". I read that as 146 tests missing. `test_nvim_octo.py` has 67 `def test_` functions that
  **parameterize to 213 collected**; 213 + 8 = 221 exactly, and a per-file diff (with a positive
  control: 235 files joined) showed no other file moved.
- 🔴 **A FLOOR CANNOT SEE A SHRINK THAT STAYS ABOVE IT, AT ANY VALUE.** `scripts/devhost-tests`
  held 15 tests against a `TARGET_FLOORS` entry of 6; deleting an 8-test file left 7, so the
  gate stayed silent. It was **already** silent in the other direction — the entry's comment
  describes a 7-test target, so the 8 tests had arrived without updating either number. Stated
  in the entry, **not closed**: closing it needs an exact pin, a different mechanism.
- 🔴 **A REPO'S OWN INSTRUCTION CAN BE WRONG.** `test_no_real_launchers.py` carried
  `⚠ DELETE THIS ROW IN PHASE 4`. Deleting it turns the suite **red**: the scan is text over
  `scripts/`, `mention-open.py` still carries the `nvim-octo` comments (which must NOT be
  scrubbed — that is the "guard on WORDS walkable by REWORDING" move), so the hit survives
  retirement and an unacknowledged hit fails. The row stays.
- ⚠ **Lazy nix overlays mean a dangling PRODUCER does not fail evaluation.** I briefed that
  deleting `nix/pkgs/tools/nvim-octo/` while leaving `nvimOctoOverlay` would break the flake.
  **Measured false:** `nix build --dry-run` succeeded with the directory gone and the overlay
  restored. Forcing the attribute *does* error, which is the positive control. The evaluator
  catches a dangling **consumer** and is blind to a dangling **producer**.
- ✅ **OPEN-SOURCE EVALUATION — NOT YET, and the reason is TIMING, not the code.** The Go module
  is genuinely portable: zero personal paths/hostnames/usernames, XDG-clean config, one optional
  runtime binary (`xdg-open`, non-fatal), plain `go build` works, 6,208 payload lines against
  **10,560 test lines (1.70×)**. Against: no LICENSE (see Defects), a `go.mod` that explicitly
  disclaims being fetchable (rename across 43 imports), a hardcoded gruvbox palette pinned by
  test to a nix file that would not ship, an argv contract `64/65/66` **inherited from the tool
  Phase 4 just deleted**, and dependencies resolving via `charm.land/` with one indirect dep on
  an untagged pseudo-version. **The real argument is that the version worth publishing — one
  that opens instantly — only existed as of today.** Revisit after a few more real reviews.
  Estimated 4–6 days to a credible v0.1.0; ~1 day for a portfolio drop.
- ⚠ **CI capacity is not the standing non-issue this repo's notes claim.** All four legs on one
  head returned `NO CAPACITY: <leg> — the gate never started (queued past its deadline)` against
  a recorded baseline of once in 80 heads. Three agents pushing heads in parallel is a plausible
  cause. It cleared on its own; re-trigger by merging `main` in (which also gates the merged
  tree) rather than an empty commit.

- **The `| tail` exit-code trap fired on `drift-check.sh`** — CLAUDE.md already documents it for
  `nix build`; the one NEW clause is that the pipe also **cut off the per-host block**, which is
  the half this repo tells you to read instead of the verdict. Redirect a checker to a file.
- 🔴 **A STORED DEPLOY READING IS A HYPOTHESIS ABOUT *NOW*.** This doc's rank 1 asserted a store
  path and `ReadIntents`=0 as the reason to ship. Both were false by the time anyone read it —
  a switch had happened in between. The ranked item was still worth doing, but for payload the
  doc never mentioned. **Re-measure the claim a ranked item rests on before you act on it; a
  correct action reached through a false premise still teaches the wrong lesson.**
- **`claim-work` renumbering is only safe against a MEASURED empty claim set.** Ranks 1 and 3
  closed, so rank 2 became rank 1 — legitimate only because `claim-work --list` showed **no live
  `mention-review-tui-*` claim** at that moment. The rank is half a claim's identity; re-ranking
  without that check silently re-points every live claim.
- **The mutation battery's `--only` takes a COMMA LIST and adds P1 automatically.** Bare
  positional ids are silently ignored and start a ~90-row sweep. There is **no `--help`** —
  passing it runs the control, aborts (`baseline is red or collected nothing`) and restores. The
  banner it prints (`FILTERED RUN … evidence about those rows and nothing else`) is the thing to
  quote, because it scopes the claim for you.

- 🔴 **THE SESSION'S METHODOLOGY LESSONS ARE IN THE CAIRN INDEX, NOT HERE — this doc is at its
  byte ceiling and an index entry costs nothing until recalled.** `cairn recall --repo
  ~/workspace/devrc`, entries **`devrc/scripts`** and **`devrc/tests`**, both dated 2026-09-20.
  They carry, with values: `ORDER_APPLIED` means a table was READ not that it RANKED (`sorted`
  is stable, so an uninformative table reproduces alphabetical order); the three gates and the
  per-`#N` tie measurements; the case-insensitivity fix; **five silent mutation-anchor
  disarmings in one arc**; a sweep being only a claim about the dimension the fixtures can see;
  isolating a mutation per conjunct; and the checkpoint-and-restore ownership rule.
- 🔴 **`/audit-pr` ROUND 0 FOUND A 🔴 IN A DOCS-ONLY PR, AND DELETED A RANKED ITEM.** On `#1781`
  it caught a sentence applying the **subtree** unit to a behind-count and the **repo** unit to
  dirtiness in one breath (`cleanSource (/. + srcDir)` reads `containers/clawgate` alone, and
  that subtree was 0-dirty on both hosts). It also killed a ranked item with the right
  question: **does something else already check this against reality?**
- ⚠ **`--emit-claims` REFUSES a `round=0` block, correctly** — round 0 fixes nothing, so an
  anchorable block would let the next delta attribute the whole change to it. Record round 0's
  verdict as PROSE; the ladder starts at the round that first FIXES something.
- **The `#1793` ladder, for anyone weighing whether to run one:** rounds 0–4, findings
  `1🔴 / 1🔴+6🟡 / 3🟡+3🟢 / 2🟡+2🟢 / 1🟢`, payload `— / 116 / 34 / 29 / 15`, executable
  `— / 52–66 / ~4 / 0 / 0`. 🔴 **Every round found the PREVIOUS round's PROSE claiming more
  than its TESTS delivered** — round 2 named it and then did it itself. Stopped by **operator
  decision** at round 4 on the attribution gate's SUBSTANCE (two consecutive zero-executable
  rounds) rather than its letter (comments in shipped source are payload under the round-1
  classification, so no round hit zero). ⚠ An earlier ledger figure of "~35 executable for
  round 1" **does not re-derive** — it is 66 counting both sides of modified lines, 52 counting
  additions only.
- ⚠ **AN ALARM REFUTED BY ITS OWN CONTROL — "6,612 detections vs 0 clicks" on the workbench.**
  `mention-detected` is emitted by `session-tailer.py` scanning **Claude transcripts**; it is
  not the operator clicking and was never upstream of a click. The 5.7× detection gap tracks
  the 7.8× Claude-session gap (452 vs 58). **Two unrelated quantities compared as a funnel.**
  The operator clicks on the laptop; there is no workbench defect. 🔴 And MEASURE THE HOST THE
  FEATURE RUNS ON — the ranking reachability was first measured on the workbench, where
  `picks.jsonl` is absent, so Tier B contributed nothing vs 117 rows on the laptop.

- 🔴 **THE LADDER'S RECURRING FINDING IS A FALSE CLAIM REPLACED BY A DIFFERENTLY FALSE ONE, AND
  IT HAPPENED FOUR TIMES THIS SESSION — THREE THE SAME SHAPE:** an unmeasured EXCLUSIVITY claim.
  *"Ctrl-C is the only remaining uncovered ending"* (false — `abort` is four keys, plus `ctrl-d`
  on an empty query); *"`queried == False` is UNAMBIGUOUSLY an ESC on an untouched picker,
  nothing else can produce it"* (false — a whitespace-only query, or type-then-delete, produces
  it); *"its ONLY Ctrl-C sentence"* (false — seven-plus). **Each was written in the commit that
  retracted the previous one. The reflex to write "nothing else can" is the defect.**
- 🔴 **A "POSITIVE CONTROL" CAN PASS ON A TIMEOUT.** The Ctrl-C arm of the real-fzf ESC test
  asserted `out == b""` only; a key that does NOT abort yields the IDENTICAL `(b"", 130)`,
  differing only in wall time (8.6s vs 0.5s), which nothing checked. A control must assert the
  child ENDED. Full write-up: cairn `devrc/tests`, 2026-09-21.
- 🔴 **A MUTATION ANCHOR CAN BE DISARMED BY A ONE-CLAUSE EDIT.** Gating `picked_ordered` on
  `ORDER_APPLIED` took K88's anchor to 0x, where a row reports SURVIVED while testing nothing.
  The anchor guard caught it; re-anchoring is NOT enough — RE-RUN the row, because a matching
  anchor is not the same claim as a killing row.
- 🔴 **A CLEAN `git merge` LEFT A FUNCTION DEFINED TWICE.** Merging #1812 into #1813 carried both
  copies of `_counter`; bodies identical, so 491 tests passed while Python silently used the
  second — dead code reading as live, the semantic conflict a clean merge does not catch.
- 🔴 **A HUNG PTY TEST PRODUCES ZERO OUTPUT, WHICH THROUGH A PIPE READS AS A SILENT PASS.**
  `waitpid` before closing the pty ⇒ fzf blocks writing its redraw and never exits; pytest killed
  at 540s, rc 124, no output, and `| tail` reported `rc=0` (that rc is `tail`'s). I then leaked
  that process for ~2h and an auditor found it. Redirect, don't pipe; kill by RESOLVED PID after
  checking `/proc/<pid>/cwd` matches EXACTLY.
- ⚠ **A ROUND-0 AUDIT CAN BE CONFIDENTLY WRONG ABOUT AUTHORSHIP.** It reported the operator never
  chose (D), having searched user-authored FREE TEXT and scoped its claim to that prefilter. The
  decision arrived through the STRUCTURED question tool and is a `[user]` record in the
  transcript. Refuted publicly on the PR; attribution stays. **Deleting a true attribution on a
  false finding is the worse error.**
- ⚠ **§1.4 OF THE PROPOSAL CONTRADICTS THE CLICK TELEMETRY, SO ITS "the ranking is not the weak
  link" IS IN DOUBT.** It reports *"104 plausible, 5 below"* over 109 picks; the click-time dim
  says the opposite ratio. They measure different things — §1.4 replays `picks.jsonl` against the
  table **as it stands now**, the dim is recorded **at click time**, so a repo whose `max_ref`
  has grown reads `plausible` retrospectively. **Mechanism likely, NOT proven:** no historical
  table snapshots exist.

- 🔴 **THE PTY READINESS COMMENT IS SCOPED TO ITS CORPUS, AND THE TWO CORPORA DISAGREE — do not
  "simplify" it back to one number.** `_fzf_interactive_first_row`'s comment now states the
  prompt-only race rate against the corpus each reading was taken on, because `#1812`'s and
  `#1813`'s texts contradicted each other on the merged tree. Here `_eponymous_corpus` derives
  its rows from `picker_rows()` and calls `MO.stamp_picker_markers`, so it CARRIES the rank
  marker and the rate is **11–12 of 20**; on the pre-`#1813` tree it was a hardcoded marker-free
  f-string and `/audit-pr` round 2 measured **0 of 80**. The marker's WIDTH is not the variable
  (12/20 at the shipped 5-byte rank-only width vs 11–12 at the ~16-byte class+rank one); what
  separates the two readings is the whole change of corpus, and **nothing isolates the marker
  inside it** — so the comment records that as unknown rather than naming a cause.
- **`rerere` is enabled in this clone (`rerere.enabled=true`), and this doc already records it
  replaying a resolution from a different merge.** Disable it **per command**
  (`git -c rerere.enabled=false merge …`) — NEVER `git config --local`, because worktree config
  writes the **common** git dir and would change the shared clone for every session. 🔴 This is
  the ONE lesson from that merge not already covered by `claude/RULES.md`; the others (squash vs
  ancestry, a clean merge that is not a clean merge, two-way pins, stacked-merge doc checks) are
  `RULES.md` §Git Workflow and the cairn `devrc/scripts` entry. **They were written out HERE and
  cut by `/audit-pr` round 0** — 66% of that update's growth, into a doc whose own 🔴 rule 68
  lines above says methodology goes to cairn. Do not re-add them.

## How to verify
```bash
# 🔴 `#1812` landed — by CONTENT, never ancestry (a squash is never an ancestor).
#   POSITIVE CONTROL FIRST or the emptiness below is a fact about the instrument.
git -C $DEVRC grep -c 'def ' origin/main -- scripts/mention-open.py        # must be non-zero
git -C $DEVRC diff 17b6ff96 origin/main -- \
  scripts/mention-open.py scripts/tests/test_mention_open.py               # must be EMPTY

# `#1813`'s CI, per-context on its own head — NEVER `/commits/<sha>/status` (the roll-up maps
#   `error` onto `failure`, so it reads RED for a merely superseded run).
H=$(gh pr view 1813 --json headRefOid --jq .headRefOid); echo "${H}"   # expect 8c5fda78…
gh api "/repos/innovation-upstream/devrc/commits/${H}/statuses" \
  --jq '.[] | "\(.context)\t\(.state)\t\(.updated_at)"' | sort -u

# The suite on the merged tree, scope NAMED so the number reproduces: 648 passed / 2 skipped at
#   base 6ee5ff68 (collection 650) — ABOVE the 641/2 (collection 643) the pre-#1813 tree gives,
#   which is the point. 🔴 RE-DERIVE BOTH SIDES; do not copy either forward. The figure that
#   stood here before was 640/2 and 642 collected, true of an older tree and stale by the time
#   it was quoted beside a fresh number.
nix develop $DEVRC -c python3 -m pytest \
  $DEVRC/scripts/tests/{test_mention_open,test_mention_scan,test_mutation_battery_anchors}.py -q

# 🔴 The two pinned picker strings must stay IDENTICAL — one hit in EACH file.
git -C $DEVRC grep -n 'bind="esc:print-query+abort" --nth=2\.\.' -- \
  scripts/mention-open.py scripts/tests/test_mention_open.py
```
