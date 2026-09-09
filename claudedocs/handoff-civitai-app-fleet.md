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

**devrc#1393 is MERGED** — `a30a587c`, 2026-09-09T00:39Z, squash. Verified by
CONTENT, not ancestry (a squash never makes the branch head an ancestor):
`claude/skills/civitai-app-fleet/` is on `origin/main`, the old
`claude/skills/civitai-app-release/` directory is gone, and `fleet.py` carries the
round-5 sentinels. Claim `civitai-app-fleet-1` released.

**The branch `zach/civitai-app-fleet` was deliberately NOT deleted.** The audit
ladder's claims blocks anchor on `db08c7f0`, `f769372c`, `b8cac6a8`, `6cbaca03`,
and a squash puts none of those in `main`'s history — deleting the branch would
eventually let the objects be GC'd and make five rounds of reasoning
un-re-derivable.

**🔴 THE SKILL IS STILL NOT LIVE — the merge changed nothing on this machine.**

    ~/.claude/skills/civitai-app-fleet    -> does not exist
    ~/.claude/skills/civitai-app-release  -> still present
    readlink -f .../civitai-app-release/SKILL.md
      -> /nix/store/s3shyg901…-devrc-claude-skills/civitai-app-release/SKILL.md

That `/nix/store` terminus is the arbiter: these are home-manager `home.file`
**copies**, not `mkOutOfStoreSymlink`s. `fleet.py` has never been run against real
data. This is rank 1 below and it is what converts six audit rounds into something
usable.

**DONE and merged — the seven app repos.** Each has a pinned flake (node 24 +
pnpm 11), `.envrc`, `.nvmrc`, a `CLAUDE.md` (ecosystem map + ranked doc sources),
an npm→pnpm conversion, and a 5-assertion toolchain drift guard:

| Repo | ready-to-work | guard backport | guard hole fix | released |
|---|---|---|---|---|
| gen-matrix | #22 | #23 | #24 | — (byte-identical bundle, deliberately unsubmitted) |
| custom-generators | #17 | #18 | #19 | 0.6.5 live |
| playable-collections | #23 | #26 | #27 | 0.2.12 live |
| model-benchmarking | #37 | #39 | #40 | 0.4.3 live |
| app-requests | #18 | had it | #19 | 0.4.1 live |
| generate-from-model | #8 | #9 | #10 | 0.2.23 live |
| sensei (base branch is `trunk`) | #66 | #67 (normalize) | — | 0.1.21 live |

All six submits built and deployed on the real platform builder; every URL returns
HTTP 200. That is the only proof that `buildCommand: pnpm run build` works, and it
is measured rather than inferred.

**The audit ladder is CLOSED at round 6.**

| round | found | fixed in |
|---|---|---|
| r1 | 2 🔴 — fabricated values in three columns; exit code overclaimed coverage | `d6815fd1` |
| r2 | r1 OVER-CORRECTED — a failed fetch emitted no inventory at all | `db08c7f0` |
| r3 | blast radius overstated 7×; r2's rule applied to one axis; 4/5 behaviours unpinned | `f769372c` |
| r4 | 4 — two fabricated cells, `no-vite` column overflow, platform loop run by ZERO tests | `b8cac6a8` |
| r5 | 2 🟡 — the `unread` sentinel nothing ran; the seam admission absent from the tree | `6cbaca03` |
| r6 | **CLEAN** — no 🔴, no 🟡; 4 🟢 (below) | — |

Round 6 was also the first round whose own self-reported numbers reproduced under
independent re-measurement without needing correction.

**Also shipped this session:** devrc#1405 (`131aa3b0`) — `main` was genuinely red
because the previous handoff doc spelled six client subdomains into this PUBLIC
repo. Found by the round-4 auditor while attributing #1393's gate; #1393 neither
caused nor fixed it. Watched red (1 failed) then green (18 passed).

**Deploy/verify status, honestly:** six apps live and serving, unchanged. The
skill is merged and **not installed**. Nothing in this arc ran `civitai app
status` against the real platform — every platform path in the tests goes through
the `CIVITAI_STATUS_FILE` seam.

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

### 🔴 devrc CI runs the nix build UNSANDBOXED — the gate silently lies
- **Symptom + exact repro:** `tekton/devrc-pytests` red on a commit whose identical
  derivation passes locally. `nix build <worktree>#checks.x86_64-linux.pytests`
  on the dev host vs the same drv in CI.
- **Observed (with values):** SAME derivation hash both sides —
  `/nix/store/5ymf49nizdr4vaqr75avxcci8iha7qnm-devrc-pytests.drv`. Dev host:
  `TOTAL collected=21161 passed=21159 skipped=2 failed=0`, `RESULT: PASS`. CI on
  `dde14daf`: `collected=21161 … failed=1`,
  `FAILING: test_the_allowlist_still_permits_every_local_remote_shape`, a
  `TimeoutExpired` / `returncode: -9` on `git clone` of an EMPTY LOCAL bare repo
  against a 120s budget, in `scripts/tests/test_nogit_isolation.py:945` — a file
  #1393 never touches. CI's build path was
  `/tmp/nix-build-devrc-pytests.drv-0`, **not `/build`**.
- **Ruled out:** a defect in #1393 — the re-run of the SAME sha `dde14daf` on a
  quieter cluster returned `pytests pass … failed=0` / `BOTH TIERS PASS`
  (`devrc-ci-rerun-whhpb`). via: measurement
- **Ruled out:** staleness (the branch being 18 commits behind). The branch was
  brought fully up to date and the gate stayed red; pristine `origin/main` passes
  the sandbox tier at `failed=0`. **This was my first attribution and it was
  wrong.** via: measurement
- **Ruled out:** a killed step. `step-pytests` exited **0** and printed a verdict;
  only `step-verdict` exited 1. Per the tekton skill's discriminator that is the
  "genuine single-test failure" bucket, not the exit-255 congestion bucket. via: measurement
- **Leading hypothesis:** the `tekton` skill's gotcha 6(b) — nix's sandbox is
  effectively OFF in the CI pod, so the tier is impure and timing-sensitive tests
  fail under node starvation. **`/build`'s absence is the tell**; `nix config
  show | grep '^sandbox '` reports a reassuring `true` over exactly this condition.
- **Blast radius, measured from OUTSIDE my own pushes** (the one window the skill
  says to trust): in the same hour `devrc-ci-pxfnr` (#1417) and `devrc-ci-f97rc`
  (#1408) failed the SAME unrelated test as each other
  (`test_POSITIVE_CONTROL_the_APPEND_comparison_CAN_see_the_difference`), and
  `devrc-ci-rmxjw` (#1419) also failed, while four other PRs passed.
- **Next probe:** in a live gate pod —
  `kubectl exec -n tekton-ci <gate-pod> -c step-pytests -- sh -c 'ls -d /build; nix config show | grep -E "^(sandbox|sandbox-fallback) "'`.
  🔴 The fix is a `homelab-infra` PodSecurity change and the tekton skill says all
  three obvious fixes are blocked by `baseline:latest` — read
  `devrc-ci-pipeline.yaml:147-154` before proposing anything.

### devrc `main`'s "7 pre-existing red tests" — RESOLVED, close it
The block recorded earlier in this doc is **superseded**. Those 7 failures across
`test_analyze_service_index_backup.py`,
`test_analyze_service_index_escrow_verify.py` and `test_opencode_engine.py` are
GONE at current `main` — fixed upstream. Measured twice: a full-suite control at
`4f49f5dc` showed 2 failures, neither of them those three files (one a load flake
I caused by running two 21k suites concurrently, one the hostname leak #1405
fixed); and the sandbox tier at `b3c9a93d` returned `failed=0`. **The `age` /
`age-keygen` (devrc#1398) hypothesis was never needed.** Do not re-derive it.

## Next steps (ranked)

1. **`home-manager switch`, then smoke the skill against real data.** This is the
   whole point of #1393 and it is not done — see *State now* for the `readlink`
   evidence that the skill is not installed. After switching:
   `python3 ~/.claude/skills/civitai-app-fleet/fleet.py --no-fetch` and confirm
   `~/.claude/skills/civitai-app-release/` is gone. ⚠ The switch rebuilds the whole
   home environment off a `main` that took many merges on 2026-09-08.
   forcing: gate — the merged work is inert until this runs.
2. **File the unsandboxed-CI-gate investigation above as a `homelab-infra` issue
   or PR.** A gate that reports a red on a change it cannot distinguish from a
   green one trains everyone to click through, and it flaked three unrelated PRs
   in one hour. Closing condition: a merged `homelab-infra` PR, or a written
   decision that the impurity is accepted.
   forcing: gate — the devrc merge gate currently cannot be trusted.
3. **Round 6's four 🟢s on the now-merged `fleet.py`.** The only one worth acting
   on before someone next touches that code: `lockstep_guard` renders `{:<17}` with
   `"version-lockstep"` at 16 chars — **one character of headroom** — and unlike
   `proj` its guard does not derive values from `lockstep_guard_home()`, so a new
   sentinel there reproduces round 4's column-shift defect invisibly. The other
   three are docstring-accuracy: `_inspected()` names `default_branch` as "the
   first key past both early returns" when `fetch` is; the AST key-ledger's helper
   docstring claims wider coverage than the walk provides; the four-sentinel
   taxonomy does not say which output surface shows which string.
   forcing: none
4. **Decide talos-infra#1456** (node 22 → 24 for the app-blocks builder).
   `MERGEABLE/CLEAN`, deliberately unmerged: it builds EVERY tenant's app block and
   Flux reconciles `trunk` in ~1 min. Needs someone who can speak for the other
   tenants. Files: `clusters/production/apps/tekton-builds/app-blocks-pipeline.yaml`.
   forcing: none
5. **Locate `radio` / `cosmetic-studio` / `prompt-library`** — see the open
   investigation earlier in this doc; `civitai app pull <slug>` is the cheapest probe.
   forcing: none
6. **Ask Zach about the duplicate withdrawn `custom-generators` 0.6.5 submission**
   — see the open investigation earlier in this doc. Only he can answer whether he
   submitted it from the web UI.
   forcing: none

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

- 🔴 **A red CI check on devrc may be a LYING GATE, not a bad change — and the
  cheapest proof is the DERIVATION HASH.** If `nix path-info --derivation
  <worktree>#checks.x86_64-linux.pytests` matches the `.drv` in the CI log and the
  results differ, the build is impure by definition and no amount of reading your
  diff will explain it. That one command settled in seconds what two wrong
  attributions had cost an hour.
- 🔴 **I attributed the red gate WRONGLY TWICE before measuring it.** First
  "inherited from main's 7 red tests" (main was green), then "staleness, the branch
  is 18 behind" (it stayed red after catching up). Both were coherent, both had a
  plausible mechanism, and **a theory that explains the failure is not evidence for
  it.** The error under both was reading only the DEV tier while the merge gates on
  the SANDBOX tier — a rule quoted earlier in the same session and then walked into.
- 🔴 **`gh`'s status rollup gives NO log URL for a Tekton check** — both
  `targetUrl` and `detailsUrl` come back empty. Attribution therefore means going
  to the cluster: find the PipelineRun by `.spec.params[?(@.name=="revision")]`
  (the `ci.zacx.dev/sha` label is `<none>`), then read `step-verdict`'s log. The
  step-level exit codes are the first thing to read — a `verdict` failure with
  `pytests` at exit 0 means a test failed, not that the step was killed.
- 🔴 **Branch protection on devrc `main` is currently `enforce_admins: false` with
  `required_status_checks: null`.** I told Zach merging would require lifting
  protection and affecting every open PR; **that was wrong** — I carried it from the
  tekton skill, which flags its own note as having "moved twice in one day" and says
  to re-measure. `gh api repos/innovation-upstream/devrc/branches/main/protection`
  is one command. Measure it; do not quote it.
- 🔴 **A round's `audit-claims` block is a PREREQUISITE, not paperwork.**
  `audit-dispatch.py --round N` for N ≥ 2 REFUSES (exit 2) without one, so a round
  that ships a fix and never posts its block silently blocks the next round. Round
  3 did exactly that. Post the block in the SAME session that ships the fix.
- 🔴 **`audit-dispatch.py`'s ledger range spans MORE than the round it names** —
  `range_anchor` is the previous block's `<from>`, so a round-N brief diffs from
  what round N−1 *audited*, not what it *produced*. Round 3's ledger covered two
  commits and over-reported its own payload 4.6× (433 vs 94). Measure
  `git show --numstat <fix-sha>` yourself.
- 🔴 **The generated brief's WHERE TO WORK section can be wrong about your cwd.**
  It says "the repository this session is standing in" and tells you to dispatch
  with `isolation: "worktree"`. If your cwd is a DIFFERENT repo (mine was
  `~/workspace/civit/civitai-app-gen-matrix`) that flag worktrees the wrong repo and
  hands the auditor a tree with no `fleet.py`. Have the agent run
  `git -C <target-repo> worktree add` itself.
- **`git worktree add` refuses a path another session already owns, and that
  refusal is the safety.** `devrc-handoff-close` looked like a free name and is a
  live worktree on `docs/handoff-picker-universe-close`. Note the branch is created
  BEFORE the path check fails, so a failed `add -b` leaves a stray branch behind.
- **Four subagents leaked scratch scripts into checkouts** (`probe.py`, `repro.py`,
  `mutate.py`, `render.py`, `seam.py`, `f34probe.py`). Put "scratch goes in /tmp,
  and `git status --porcelain` must be empty before you finish" in the brief.
- **Decision: the ladder stopped at round 6 rather than fixing its four 🟢s and
  running a round 7.** Fixing them would create an unaudited delta on a PR whose
  ladder had just closed — reopening exactly what the stop rule closes. They are
  rank 3 above instead.

## How to verify

```bash
# the merge landed — by CONTENT, never ancestry (squash breaks ancestry forever)
git -C ~/workspace/devrc fetch origin
git -C ~/workspace/devrc ls-tree --name-only origin/main claude/skills/civitai-app-fleet/
git -C ~/workspace/devrc ls-tree --name-only origin/main claude/skills/civitai-app-release/  # expect: empty
gh pr view 1393 --repo innovation-upstream/devrc --json state,mergedAt,mergeCommit

# 🔴 is the skill actually LIVE? /nix/store terminus = NOT live, needs a switch
readlink -f ~/.claude/skills/civitai-app-fleet/SKILL.md
ls -d ~/.claude/skills/civitai-app-release   # expect: gone, after the switch

# the six gates, on a checkout of main
nix develop ~/workspace/devrc --command bash -c \
  'python3 -m pytest scripts/tests/test_civitai_app_fleet.py \
     scripts/tests/test_skill_tiers.py scripts/tests/test_skill_descriptions.py \
     scripts/tests/test_skill_audit.py scripts/tests/test_doc_path_rot.py \
     scripts/tests/test_no_client_hostnames.py -q'
# expect: 325 passed

# the CI gate is impure — same drv, different result. THE one-command proof.
nix path-info --derivation ~/workspace/devrc#checks.x86_64-linux.pytests
# compare against the .drv named in the failing CI log; identical hash + different
# verdict = impure build, i.e. a broken gate rather than a bad change.

# all six released apps serve. APEX is NOT committed — devrc is PUBLIC and
# scripts/tests/test_no_client_hostnames.py fails on a client subdomain literal.
APEX=<the app-hosting apex domain>
for h in app-requests generate-from-model custom-generators \
         playable-collections model-benchmarking sensei; do
  curl -sS -o /dev/null -w "$h %{http_code}\n" "https://$h.$APEX/"
done
```
