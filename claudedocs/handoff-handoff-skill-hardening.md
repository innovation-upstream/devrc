# Handoff: handoff-skill-hardening — 2026-08-24

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` exited **5**
(NOTHING RESOLVED, 0 tasks). An unknown session id answers 200 with an empty array, so
that cannot distinguish "this session touched no task" from "the id is wrong" — it is not
a clean bill of health, and the skill forbids minting a task to fill the blank.

## Goal
Review the `/handoff` skill, then fix what the review found. It grew into four merged PRs:
a real defect in the skill, a size gate, the retirement of its last prompt, and — because
each blocked the merge — three CI flakes in two distinct classes.

## State now
- **Branch:** `main`, at `fb0b8417`, base clone synced. Both hosts converged and switched.
- **DONE — all merged and deployed:**
  - `#764` (`afbb8936`) — `/handoff`: the NEW-doc path never got committed; size gate added; the step-5 y/N retired.
  - `#780` (`c5d75c2c`) — git-maintenance flake in `test_analyze_service_index_restore_verify.py`.
  - `#787` (`78c180ef`) — that flake as a CLASS: `scripts/testlib/hermetic_git.py`, 8 modules, ledger guard `scripts/tests/test_hermetic_git.py`.
  - `#790` (`532807c1`) — `/handoff` step 4 extracted to `claude/skills/subsystem-index/`.
  - `#791` (`9f8d5824`) — browser-bridge spool-wait race (`_wait_events` count-as-proxy).
- **Deploy VERIFIED against the live artifact, not the rollout:** both hosts at `fb0b8417`;
  `~/.claude/skills/handoff/SKILL.md` is 22,189 B and names `subsystem-index` ×2;
  `~/.claude/skills/subsystem-index/{SKILL.md,reference/index-write.md}` present;
  `handoff/reference/index-write.md` gone.
- **This document is itself the end-to-end test of `#764`+`#790`** — written via the new
  flow (scratch file → step 5 sole writer → no prompt), crossing into the extracted skill
  at step 4. That crossing is the one thing 77 pins could not prove.
- **IN FLIGHT / left deliberately undone:** see Next steps. Nothing is half-applied.

## Open investigations — live diagnosis state

### Is the devrc CI tier systemically flaky, or did I just hit three?
- **Symptom + exact repro:** three separate CI failures blocked merges today, none caused
  by the diff under test. Both Tekton tiers are required with `enforce_admins: true`, so
  any one of them blocks every open PR with no admin override.
- **Observed (with values):**
  - `devrc-ci-7w4kx` (PR #764) — `test_analyze_service_index_restore_verify.py:2190`,
    `AssertionError: the repository changed anyway`, extra item
    `.git/objects/maintenance.lock`. Steps: pytests **exit 0**, nodetests **exit 0**,
    `verdict` exit 1 — the legs passed, the verdict step reported the 1 failed test.
  - `devrc-ci-dhsn6` (PR #790) — `scripts/browser-bridge/tests/test_server.py:2564`,
    `assert 0 == 1  where 0 = len([])`. Same run's stderr contains
    `{"event":"throttled","op":"tabs","reason":"rate_limited"}` — the server DID throttle;
    only the `cmd` event had reached the spool inside the 3 s deadline.
  - Retained PipelineRun tally, `kubectl -n tekton-ci get pipelineruns`: **133 devrc-ci
    runs, 86 Succeeded / 47 Failed = 35% failed.**
- **Ruled out:**
  - "the diff broke it" — both branches touched neither failing module; both trees passed
    the full local gate (`gate.sh --tier both --set hermetic`, `RESULT=PASS`).
  - "same root cause" — different mechanisms: a git background-maintenance lock file vs a
    spool-write race behind a fixed timeout.
- **Leading hypothesis:** unproven. **35% is a CEILING on a flake rate, not a value** — it
  is undecomposed and includes genuinely red branches, the exit-255 congestion the `tekton`
  skill documents, and infra faults. Several of those runs are also mine from today, so
  measuring now samples a window I helped cause — the exact contamination
  `claude/skills/tekton/SKILL.md` gotcha 3 warns about.
- **Next probe:** decompose the 47 failures by cause, from a window with none of my own
  pushes in it:
  ```bash
  KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pipelineruns -o json \
    | jq -r '.items[] | select(.metadata.name|startswith("devrc-ci-"))
             | [.metadata.name, .status.conditions[0].reason,
                (.status.completionTime//"")] | @tsv' | sort -k3
  ```
  then for each Failed run read `step-verdict` and classify: real red / exit-255 congestion
  / flake. Only that separates "the tier is flaky" from "I had a bad day".

## Next steps (ranked)
1. **Land or discard the stranded `scripts/discord-embed-ext/` work** — 13 untracked files
   (extension + tests) plus one uncommitted line in `scripts/run-node-tests.sh` adding
   `"scripts/discord-embed-ext/tests|3|50"`. Idle since 2026-08-23 23:13, never pushed, and
   the session that made it is gone (no process has the base clone as cwd). It blocked the
   base clone's fast-forward today. The line was preserved and re-applied onto the updated
   file by hand; the WIP copy is at
   `/tmp/claude-1000/.../scratchpad/run-node-tests.WIP.sh` (`c574ddaf27135b4c`) — **that
   scratchpad is session-scoped and will not survive; re-derive from `git diff` instead.**
   🔴 Landing it is a review decision, not cleanup: the suite line references a directory
   the flake source does not carry.
2. **Reconcile `/analyze-service` with `subsystem-index`** — both write the same store and
   their protocols MATERIALLY CONFLICT. `analyze-service/reference/write-back.md` gates the
   append behind an `append this to the index? (y/N)` that `subsystem-index` declares
   retired, and uses `Write` where `subsystem-index` mandates `Edit` anchored on
   `## Nuance / work-history` (a whole-file retype silently loses a concurrent append).
   `subsystem-index/SKILL.md` hardcodes `created_by: handoff`, correct only while it has one
   caller. Two documents each claim to be *the* protocol.
3. **Convert the 15 remaining count-as-proxy `_wait_events` sites** in
   `scripts/browser-bridge/tests/test_server.py` to `until=`. Latent instances of the #791
   flake; deliberately not done there because it means editing a 407-test file to fix a
   one-line race.
4. **Decompose the CI failure rate** — the open investigation above.
5. **Watch the listing budget: 68 chars free.** `test_skill_descriptions.py` ratchet.
   `subsystem-index` cost 182 and took no eviction. The next skill genuinely needs one.

## Gotchas / decisions / dead-ends
- 🔴 **The dominant defect shape this session was a guard whose SCOPE narrowed while its
  wording stayed wide.** Found four times, never by the suite: my size gate had two mutants
  ride through green (`MIN_HEADROOM_BYTES` was dead at every value; `_size()`'s unit was
  unpinned because every fixture was ASCII); and the #790 extraction left
  `test_the_retracted_claim_is_NOT_still_asserted` checking the two files the prose had
  LEFT — an audit proved it by appending the retracted sentence and watching a full green
  suite. **A file move can decapitate a guard without editing a line of it.**
- 🔴 **Cold reads caught what no test could, twice.** With the suite green, step 5 still
  said *"step 4's index write is gated"* (retired 2026-08-15) and the kickoff sentence named
  *"step 4's confirm gate"* (gone). Prose staleness is invisible to pins that assert the
  prose exists.
- **`devrc-pytests` is required now, not just `devrc-nodetests`** — `contexts` = both,
  `enforce_admins: true`, `strict: false`. `CLAUDE.md` and the `tekton` skill both said
  otherwise today and were fixed by #771/#777 while this session ran. Read the contexts,
  never the prose: `gh api /repos/innovation-upstream/devrc/branches/main/protection --jq .required_status_checks`
- **Worktrees share the common git dir, so `origin/main` moves under you.** Mid-merge,
  `git diff origin/main` reported a 181-line file as deleted that nobody deleted — `MERGE_HEAD`
  was two commits behind the ref by then. **Merge a pinned sha, never a ref name.**
- **Removing a prompt COSTS bytes.** Retiring the step-5 y/N grew `handoff/SKILL.md`,
  because what replaces it (which refusals now carry the protection) has to be written down.
  Three trim passes to pay for it BROKE A PIN; the ceiling was raised instead, then ratcheted
  down 48,600 → 24,000 by the extraction.
- **Dead end, retracted:** the first survey of the maintenance-flake class reported "3 of 8
  pinned". Wrong — `test_analyze_service_index_commit.py` matched on `GIT_CONFIG_COUNT`
  appearing in a *docstring*. It was 2 of 8. **A grep for a token is not a check for a pin;**
  the ledger uses AST.

## How to verify
```bash
# 1. the gate, on the current tree — the authoritative instrument
nix develop /home/zach/workspace/devrc --command \
  bash /home/zach/workspace/devrc/scripts/gate.sh --tier both --set hermetic
#    expect: GATE: RESULT=PASS exit=0, and read the per-target table, not the verdict

# 2. the extraction is DEPLOYED (not merely merged — nix decides this, git does not)
grep -c subsystem-index ~/.claude/skills/handoff/SKILL.md          # expect 2
test -f ~/.claude/skills/subsystem-index/SKILL.md && echo deployed
test -f ~/.claude/skills/handoff/reference/index-write.md || echo "old ref gone"

# 3. the flake class stayed closed — every member still uses the shared pin
nix-shell -p python3Packages.pytest --run \
  "python3 -m pytest /home/zach/workspace/devrc/scripts/tests/test_hermetic_git.py -q"

# 4. both hosts actually carry it
bash /home/zach/workspace/devrc/scripts/drift-check.sh
```
