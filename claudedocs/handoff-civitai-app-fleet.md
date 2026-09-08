# Handoff: civitai-app-fleet — 2026-09-08

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
Get the seven Civitai App Block repos to a "ready to work" state (pinned nix
toolchain, `CLAUDE.md`, drift guards), ship that change through the whole fleet,
and capture the fleet-iteration knowledge as a devrc skill so the next bulk pass
does not re-derive it.

## State now

**Branch / PR:** `devrc` worktree at `/home/zach/workspace/devrc-fleet-skill`,
branch `zach/civitai-app-fleet`, head `f769372c`, pushed, clean. PR **#1393** is
`MERGEABLE` / **`UNSTABLE`** — no conflict, one red check.

**`origin/main` has MOVED to `4f49f5dc`** since this doc was first written:
`6d488a1b` (#1402, this doc) and `4f49f5dc` (#1403, toolchain-claim retractions).
So the "🔴 THIS DOC LANDS VIA devrc#1402" warning below is **retired — #1402
merged 2026-09-08T20:27Z** and the doc is on `main`. The local primary clone
`/home/zach/workspace/devrc` is still behind and does not have it; read it from a
current worktree or `git show origin/main:claudedocs/handoff-civitai-app-fleet.md`.

**DONE and merged — the seven app repos.** Each has a pinned flake (node 24 +
pnpm 11), `.envrc`, `.nvmrc`, a `CLAUDE.md` (ecosystem map + ranked doc
sources), an npm→pnpm conversion, and a 5-assertion toolchain drift guard:

| Repo | ready-to-work | guard backport | guard hole fix | released |
|---|---|---|---|---|
| gen-matrix | #22 | #23 | #24 | — (byte-identical bundle, deliberately unsubmitted) |
| custom-generators | #17 | #18 | #19 | 0.6.5 live |
| playable-collections | #23 | #26 | #27 | 0.2.12 live |
| model-benchmarking | #37 | #39 | #40 | 0.4.3 live |
| app-requests | #18 | had it | #19 | 0.4.1 live |
| generate-from-model | #8 | #9 | #10 | 0.2.23 live |
| sensei (base branch is `trunk`) | #66 | #67 (normalize) | — | 0.1.21 live |

All six submits built and deployed on the real platform builder; every URL
returns HTTP 200. That is the only proof that `buildCommand: pnpm run build`
works, and it is measured rather than inferred.

**The audit ladder on #1393, all rounds:**

- r1 (full, `eab2ceb6`): 2 🔴 — `fleet.py` fabricated values in three columns and
  its exit code promised coverage it lacked. Fixed in `d6815fd1`.
- r2 (delta): found the r1 fix OVER-CORRECTED — a failed fetch was folded into
  `error`, and an error row prints `!!` instead of the row, so a global fetch
  failure emitted no inventory at all. Fixed in `db08c7f0`.
- r3 (delta): found (a) the r2 blast radius was overstated 7×, (b) r2's own rule
  applied to one axis only, (c) four of the five behaviours r2 shipped were
  unpinned. Fixed in `f769372c`. **Its claims block went unposted until this
  session** — see *Gotchas*.
- r4 (delta, range `db08c7f0..f769372c`): **dispatched, result NOT YET READ.**

**IN FLIGHT — talos-infra#1456**, node 22 → 24 for the app-blocks builder.
`MERGEABLE/CLEAN`, deliberately unmerged: that builder builds EVERY tenant's app
block and Flux reconciles `trunk` in ~1 min. Needs someone who can speak for the
other tenants. (Rank 3 below.)

**DONE this session (rank 1, claimed as `civitai-app-fleet-1`):**

- **Round 3's `audit-claims` block was never posted.** That is a hard prerequisite:
  `audit-dispatch.py --round N` for N ≥ 2 **refuses** without a parseable block,
  and the previous session's stop point silently omitted it. Posted as
  [#1393 comment 5591453254](https://github.com/innovation-upstream/devrc/pull/1393#issuecomment-5591453254)
  with nine claims derived from `f769372c`'s commit message.
- **The round-3 ledger figure in that comment is measured, not the script's.**
  `audit-dispatch.py` renders `d6815fd1..HEAD`, which spans **two** commits
  (round 2's fix *and* round 3's), so its 433-line total is not round 3's payload.
  Measured over `f769372c` alone: **94 payload lines** (`fleet.py` +67/−27) and
  113 scaffolding. The comment says which method produced which number.
- **316 passed across the six affected gates — re-measured independently**, not
  taken from the commit message. Command in *How to verify*.
- **Round-4 delta brief assembled**, range `db08c7f0..f769372c` (1 commit,
  3 files, 207 lines), at
  `…/scratchpad/r4-brief.md` in this session's scratchpad.

**IN FLIGHT — three jobs, none finished at the time of writing:**

1. **The round-4 audit agent.** Dispatched against the brief above, working in its
   own worktree `/home/zach/workspace/devrc-audit-1393-r4` (detached at
   `f769372c`), told to remove it when done. Its report has NOT arrived.
2. **Pristine-`main` control run** — full suite at `4f49f5dc` in
   `/home/zach/workspace/devrc-r4-ctrl-main`, output at `…/scratchpad/ctrl.out`.
3. **Merged-tree run** — full suite at `origin/main` + `f769372c` in
   `/home/zach/workspace/devrc-r4-merged` (merge commit `5e658139`, **clean, no
   textual conflict**), output at `…/scratchpad/merged.out`.

Runs 2 and 3 exist to attribute #1393's red gate; see the open investigation below.
**Nothing has been merged.** The claim `civitai-app-fleet-1` is still held.

**Four worktrees were created this session** and all four still exist:
`devrc-audit-1393-r4` (the agent's), `devrc-r4-ctrl-main`, `devrc-r4-merged`,
`devrc-handoff-r4` (this doc). Remove them once their job is done —
`git -C ~/workspace/devrc worktree remove --force <path>`.

**Deploy/verify status, honestly:** unchanged from before. Six apps are live and
serving; the skill is still NOT live — that needs a `home-manager switch` that has
not been run. Round 4 is dispatched but unread, so the ladder's stop condition
has **not** been met and #1393 must not be merged on the strength of this doc.

## Open investigations — live diagnosis state

### devrc `main` is red — 7 pre-existing failures, undiagnosed
- **Symptom + exact repro:** `nix develop <repo> --command bash -c 'bash scripts/run-tests.sh .'`
  on a pristine `origin/main` worktree.
- **Observed (with values):** `TOTAL collected=20908 passed=20899 skipped=2 failed=7`,
  rc=1. Failures are confined to three files:
  `scripts/tests/test_analyze_service_index_backup.py` (1),
  `scripts/tests/test_analyze_service_index_escrow_verify.py` (5),
  `scripts/tests/test_opencode_engine.py` (1). One named failure:
  `test_every_decrypt_family_VERDICT_is_pinned_WHOLE`, asserting
  `'DECRYPT-FAILED' == 'ARTIFACT-CORRUPT'`.
- **Ruled out:** caused by this session's work — a pristine `origin/main`
  control run reproduces the identical 7 across the identical 3 files. via: measurement
- **Ruled out:** an xdist ordering artefact — a same-named, same-test-count dummy
  file added to pristine main produced 7, not 18. via: measurement
- **Leading hypothesis:** an `age`/`age-keygen` behaviour change; devrc#1398 is
  open about `age 1.3.2 removed the age-keygen input echo`, which is adjacent to
  the escrow/keygen failures.
- **Next probe:** `nix develop /home/zach/workspace/devrc --command bash -c 'cd /home/zach/workspace/devrc && python3 -m pytest scripts/tests/test_analyze_service_index_escrow_verify.py -q -x'`
  then compare `age --version` against what that test expects.

### A `custom-generators` 0.6.5 submission this session did not make
- **Symptom + exact repro:** `civitai app status` lists TWO `custom-generators 0.6.5`
  rows — one `approved/live` (mine), one `withdrawn`.
- **Observed (with values):** mine is `pubreq_01M1ZKMVVXSPG7D09QA5A011EH`,
  submitted ~23:2x. The withdrawn one is `pubreq_01M1ZMSA9S0B58KV77K3XXA1JX`,
  submitted 23:35 — AFTER mine, and withdrawn shortly after.
- **Ruled out:** a duplicate from this session — only one `civitai app submit`
  was run for that app, and its returned pubreq id is the live one. via: command
- **Leading hypothesis:** Zach submitted from the web UI, or another session did.
  Benign either way; the live 0.6.5 is unaffected.
- **Next probe:** ask Zach; or `civitai app status custom-generators` and check
  whether a third row appears (would indicate an automated producer).

### Three owned apps have no source repo anyone has located
- **Symptom + exact repro:** `civitai app doctor` lists `radio`,
  `cosmetic-studio` and `prompt-library` as `(approved, owner)`, but no checkout
  under `~/workspace/civit` and no `ZacxDev/*` repo matches them.
- **Observed (with values):** `gh repo list ZacxDev --limit 100` has no `radio`,
  no `cosmetic-studio`, no `prompt-library`. `ZacxDev/vitrine` exists but has NO
  `block.manifest.json` — it is a Dockerized Drizzle app, a different shape.
  `prompt-library` is `status=removed` with a BLOCKING `missing-cover`.
  A non-git scaffold exists at `~/workspace/civit/civitai-block-prompt-library`.
- **Ruled out:** `comfy` being one of these — its store description is hosted
  ComfyUI, i.e. a platform-team app rather than an app-block repo of Zach's. via: doc
- **Leading hypothesis:** they were scaffolded and submitted without a git repo,
  or live in a repo under a different owner.
- **Next probe:** `civitai app pull radio` — the CLI claims it can "Clone or sync
  your app's repository from Civitai", which would settle where the source is.

### devrc#1393's merge gate is RED — attribution IN FLIGHT, do not merge until it lands
- **Symptom + exact repro:** `gh pr view 1393 --repo innovation-upstream/devrc
  --json statusCheckRollup` → `tekton/devrc-pytests` **FAILURE**,
  `tekton/devrc-nodetests` SUCCESS. `mergeable=MERGEABLE`,
  `mergeStateStatus=UNSTABLE`.
- **Observed (with values):** neither Tekton check exposes a `targetUrl` or
  `detailsUrl` via the GraphQL rollup — both come back empty, so the log is not
  reachable from `gh` alone and the failure cannot be attributed from GitHub.
  `#1393`'s three changed files (`fleet.py`, `test_civitai_app_fleet.py`,
  `test_skill_tiers.py`) are disjoint from the three files this doc previously
  recorded as failing on `main`.
- **Ruled out:** *nothing yet.* Disjoint files are explicitly NOT safety —
  `claude/RULES.md` records the case where one side widens a function's required
  inputs and the other adds a caller, both green, `main` red on merge. via: doc
- **Ruled out:** the previous session's pristine-`main` control being reusable —
  `main` has moved two commits (#1402, #1403) since it was taken, so it is a
  hypothesis about a tree that no longer exists. via: measurement
- **Leading hypothesis:** inherited red — the same 7 pre-existing failures across
  `test_analyze_service_index_backup.py`,
  `test_analyze_service_index_escrow_verify.py` and `test_opencode_engine.py`
  that this doc already records for `main`. **Held loosely and NOT acted on.**
- **Next probe:** the two runs already launched. Compare the failing **FILE sets**
  (never scraped test ids — this doc records that scraping ids cost two confident
  wrong diagnoses):
  ```bash
  S=…/scratchpad
  grep -E "^(FAILED|ERROR) " $S/ctrl.out   | sed 's/::.*//' | sort -u
  grep -E "^(FAILED|ERROR) " $S/merged.out | sed 's/::.*//' | sort -u
  grep -E "TOTAL collected|^RESULT:" $S/ctrl.out $S/merged.out
  ```
  Identical file sets ⇒ inherited, and the merge introduces nothing. Any file in
  `merged` that is not in `ctrl` ⇒ this PR's, and the ladder is not done.

### devrc `main` is red — supersedes the block below on one point only
The 7-failure reading below was taken at a `main` that no longer exists
(`b508b684`-era; `main` is now `4f49f5dc`). The **diagnosis** — an `age` /
`age-keygen` behaviour change, devrc#1398 — is untouched and still the leading
hypothesis. Only the *count and file set* need re-reading, and the control run
above re-reads them as a side effect.

## Next steps (ranked)

1. **Finish round 4 of the audit ladder on devrc#1393, then merge it.**
   Three jobs are already running (see *State now*); read all three before acting.
   **Order matters:** (a) the audit agent's report — a clean round is the stop
   condition, and *ending the ladder is the correct outcome*, so do NOT run a
   round 5 to confirm a clean round 4; (b) the gate attribution — do not merge
   through a red gate you have not attributed; (c) only then
   `gh pr merge 1393 --repo innovation-upstream/devrc --squash`.
   If round 4 reports findings, fix them on `zach/civitai-app-fleet` in
   `/home/zach/workspace/devrc-fleet-skill`, post a `round=4` claims block
   (`audit-dispatch.py 1393 --repo innovation-upstream/devrc --round 4
   --emit-claims --audited f769372c…`), and run round 5.
   Release `claim-work --release civitai-app-fleet-1` when done or abandoned.
   forcing: gate — the PR is open behind a stop rule, and its merge check is red.
2. **`home-manager switch`, then smoke the skill against real data.**
   Until then `~/.claude/skills/civitai-app-fleet/` does not exist and the old
   `civitai-app-release/` directory lingers. After switching:
   `python3 ~/.claude/skills/civitai-app-fleet/fleet.py --no-fetch`
   forcing: none
3. **Decide talos-infra#1456** (node 22 → 24 for the app-blocks builder).
   Needs a judgement about other tenants that I could not make. Files:
   `clusters/production/apps/tekton-builds/app-blocks-pipeline.yaml`.
   forcing: none
4. **Locate `radio` / `cosmetic-studio` / `prompt-library`** — see the open
   investigation; `civitai app pull <slug>` is the cheapest probe.
   forcing: none
5. **Diagnose devrc `main`'s red tests** — see the open investigations. A
   permanently-red gate trains everyone to merge through it, and rank 1 is
   currently blocked behind exactly that. The control run launched for rank 1
   re-reads the failure set at the current `main` for free.
   forcing: gate — the merge gate on every devrc PR is currently red.

## Gotchas / decisions / dead-ends

- **`isolation: "worktree"` builds a worktree of the CWD's repo, not the
  target's.** For cross-repo fan-out, have each agent run
  `git -C <target> worktree add` itself. This is in `reference/fan-out.md`.
- **`sensei`'s default branch is `trunk`**, the other six are `main`. A brief
  hardcoding `origin/main` was handed to seven agents; only the sensei agent
  noticing saved it.
- **The submit floor is the highest version ON RECORD, not deployed** — a
  withdrawn submission still occupies it.
- **`civitai app status <slug>` returns only the NEWEST submission.** A
  withdrawn duplicate masked a healthy `building` row. Use the list view.
- **Deploy states seen live:** `-`, `building`, `deploying`, `live`, `failed`,
  `preview-live`. `queued` does NOT exist — it was invented in a first draft.
- **Two ratchets were raised then partly lowered** in devrc:
  `LISTING_TOTAL_CEILING_CHARS` 10,800→11,192→11,170 and `TIER_A_CEILING_CHARS`
  7,496→7,639→7,617. Both files say "do NOT raise"; Zach took both decisions
  with that text quoted to him. Headroom is pinned at 0 in both, so the next
  skill addition of any size reds those gates. **A third raise is almost
  certainly wrong — demote instead.**
- **Attribute a suite failure by comparing failing FILES against a pristine
  control**, never by scraping test ids out of output. Doing the latter cost two
  confident wrong diagnoses this session.
- **Read a runner's counted output, never its exit code** — a trailing `echo` in
  a wrapper replaced `run-tests.sh`'s status and reported a red suite as
  "exit code 0" twice.
- **devrc global skills are `home.file` copies** (`readlink -f` lands in
  `/nix/store`), so editing devrc does not make a skill live; that needs a
  `home-manager switch`.
- **Decision: `gen-matrix` is deliberately unsubmitted.** Its manifest and source
  are unchanged by this work, so a bump would rebuild a byte-identical bundle.

- 🔴 **THIS DOC LANDS VIA devrc#1402 AND IS NOT ON `main` UNTIL THAT MERGES.**
  The kickoff block points at
  `/home/zach/workspace/devrc/claudedocs/handoff-civitai-app-fleet.md`, and that
  path **does not exist in the primary clone** while the PR is open — the doc
  lives only on branch `docs/handoff-civitai-app-fleet` (commit `c3124df6`). If
  `/resume` cannot find it, read it from the ref instead:
  `git -C ~/workspace/devrc show origin/docs/handoff-civitai-app-fleet:claudedocs/handoff-civitai-app-fleet.md`
  — or merge #1402 first, which is the cheaper fix.
- **Why a PR rather than a direct commit:** `handoff_doc.py` refused with
  `status=behind` because the primary devrc clone was 6 commits behind `origin/main`
  AND dirty with 5 uncommitted paths belonging to another session. Fast-forwarding
  would have refused or overwritten that work, so the doc was written from a
  throwaway worktree off `origin/main` and pushed as its own branch. The primary
  clone was left byte-identical — verify with
  `git -C ~/workspace/devrc status -sb`.

- 🔴 **A round's `audit-claims` block is a PREREQUISITE, not paperwork.** Round 3
  did the work, wrote a nine-item commit message and never posted the block, and
  `audit-dispatch.py --round 4` would have **refused** (exit 2) rather than
  silently degrading into a blind full audit. Post the block in the SAME session
  that ships the fix — the next session cannot tell a missing block from a
  round that never ran.
- 🔴 **`audit-dispatch.py`'s ledger range spans MORE than the round it names.**
  `range_anchor` is the previous block's `<from>`, so a round-N brief diffs from
  what round N−1 *audited*, not what it *produced* — round 3's ledger covered two
  commits and over-reported its payload by 4.6× (433 vs 94). That is deliberate
  (a delta round re-reads the previous fix), but the "payload lines changed THIS
  round" line it asks you to fill in is **not** the number it printed. Measure
  `git show --numstat <fix-sha>` yourself.
- 🔴 **The brief's own WHERE TO WORK section can be wrong about your cwd.**
  It said "the repository this session is standing in" and told the operator to
  dispatch with `isolation: "worktree"`. This session's cwd was
  `~/workspace/civit/civitai-app-gen-matrix` — a different repo entirely — so that
  flag would have worktreed **gen-matrix**, and the agent would have audited a
  tree with no `fleet.py` in it. Same trap `reference/fan-out.md` already
  documents, arriving from the direction of a *generated brief* rather than a
  hand-written one. The fix is the standing one: have the agent run
  `git -C <target-repo> worktree add` itself.
- **`gh`'s status rollup gave no log URL for either Tekton check** — both
  `targetUrl` and `detailsUrl` are empty. Attributing a red Tekton gate on devrc
  therefore means re-running the suite locally against a control, not reading CI.
- **The primary clone `~/workspace/devrc` was left untouched** — still at
  `b508b684`, 2 behind, with 5 untracked paths belonging to another session. Only
  `git fetch` was run against it. Everything this session did happened in
  worktrees created off `origin/main`.

## How to verify

```bash
S=/tmp/claude-1000/-home-zach-workspace-civit-civitai-app-gen-matrix/f1768840-56ab-4f1f-99fb-1591210bc063/scratchpad

# the round-3 claims block is posted and parseable — round 4 refuses without it
gh pr view 1393 --repo innovation-upstream/devrc --json comments \
  --jq '.comments[].body' | grep -c 'audit-claims round=3'   # expect: 1

# the six affected gates, re-measured (this session got 316 passed in 49s)
nix develop /home/zach/workspace/devrc-fleet-skill --command bash -c \
  'python3 -m pytest scripts/tests/test_civitai_app_fleet.py \
     scripts/tests/test_skill_tiers.py scripts/tests/test_skill_descriptions.py \
     scripts/tests/test_skill_audit.py scripts/tests/test_doc_path_rot.py \
     scripts/tests/test_no_client_hostnames.py -q'
# expect: 316 passed

# round 3's payload, measured over its own commit rather than the script's range
git -C /home/zach/workspace/devrc-fleet-skill show --numstat --format= f769372c
# expect: fleet.py 67/27, test_civitai_app_fleet.py 97/0, test_skill_tiers.py 13/3

# the gate attribution — READ THE COUNTED OUTPUT, never the exit code
grep -E "TOTAL collected|^RESULT:" $S/ctrl.out $S/merged.out
grep -E "^(FAILED|ERROR) " $S/ctrl.out   | sed 's/::.*//' | sort -u > $S/ctrl.files
grep -E "^(FAILED|ERROR) " $S/merged.out | sed 's/::.*//' | sort -u > $S/merged.files
diff $S/ctrl.files $S/merged.files   # empty ⇒ inherited red, merge introduces nothing

# every app repo carries the 5-assertion guard, and sensei is on trunk
for r in civitai-app-gen-matrix civitai-app-custom-generators \
         civitai-app-playable-collections civitai-app-requests \
         civitai-app-model-benchmarking civitai-block-generate-from-model; do
  git -C ~/workspace/civit/$r show origin/main:src/toolchain-lockstep.test.ts \
    | grep -cE '^\s*it\('
done
git -C ~/workspace/civit/civitai-app-sensei show \
  origin/trunk:src/toolchain-lockstep.test.ts | grep -cE '^\s*it\('

# all six released apps serve.
# 🔴 APEX is the app-hosting apex domain and is DELIBERATELY not committed —
# devrc is a PUBLIC repo and scripts/tests/test_no_client_hostnames.py fails on a
# client subdomain literal. This line spelled it until 2026-09-08 and was RED on
# main for it. Read the apex off `civitai app doctor`, then:
APEX=<the app-hosting apex domain>
for h in app-requests generate-from-model custom-generators \
         playable-collections model-benchmarking sensei; do
  curl -sS -o /dev/null -w "$h %{http_code}\n" "https://$h.$APEX/"
done
```
