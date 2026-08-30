---
clawgate-task: 364
---
# Handoff: cairn-task-linkage — 2026-08-29

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
Cairn's team-facing half is tracked in ClickUp, not in devrc — and nothing named "cairn"
appears in those tickets. This session identified that cluster, measured each ticket
against what Cairn actually has, and filed the two pieces of work that follow. The
durable output is the linkage: which ClickUp ticket, which clawgate card, and what is
genuinely built versus assumed.

## State now
- **Branch / PR:** `feat/cairn-task-refs` @ **485202f8** → **innovation-upstream/devrc#1049**, OPEN.
  Contains current `origin/main` (merged 3×, `main` moved 4× during gating).
- **cg#428 is `ready_for_review`, 5 comments.** Criteria **1,2,3,5,6,7,8,9 DONE**.
  **Criterion 4 (URL resolution) NOT DONE** — Layer B, blocked on devrc#1011.
- **Shipped:** `tasks:` / `task:` front-matter schema on subsystem entries —
  `scripts/lib/subsystem_resolver.py` (`TaskRef`, `parse_task_ref`, `format_task_refs`,
  `lossy_tag_for`, block-list support in `parse_front_matter`, validation in
  `from_mapping`), read surface in `scripts/lib/subsystem_recall.py`, 77 tests in
  `scripts/tests/test_subsystem_task_refs.py`, schema documented in
  `claude/skills/analyze-service/reference/index-store.md` (hash pin updated).
- **Also carries two gate re-pins** neither side needed alone:
  `scripts/tests` floor 8217 → **10269** (`scripts/run-tests.sh`), and a skill-listing
  re-pin that turned out to duplicate #1069 and was resolved to `main`'s copy.
- **Filed:** **cg#439** — the `test_git_repo_isolation` co-tenancy flake.
- **Carried forward from the previous session** (its State-now section is replaced by this
  one, so the provenance is restated rather than lost): cg#428 and cg#429 were CREATED
  then; cg#362–365 were tagged `project:cairn` and had `repo` hand-corrected
  (363/364/365 → `innovation-upstream/devrc`, 362 → unset). **Re-probed this session: NOT
  reverted.** That correction is what rank 4's regression case watches.
- **Gates at 485202f8: BOTH TIERS GREEN LOCALLY.** dev-host `gate.sh` →
  `GATE: RESULT=PASS exit=0`; sandbox `nix build .#checks.{pytests,nodetests}` → exit 0
  (read with no pipe; negative control `.doesnotexist` → exit 1).
- ✅ **BOTH REQUIRED TEKTON CHECKS PASSED on 485202f8** — `devrc-pytests`
  `collected=18905 passed=18903 skipped=2 failed=0 (floor 18076)`, `devrc-nodetests`
  `1366/1366 (floor 1317)`. They sat `pending` for ~20 min first; a bounded poll settled
  it. Branch protection was NOT touched.
- **Deploy/verify:** nothing deployed. No `home-manager switch`; nothing in `nix/`.
  The schema is inert until something writes a `tasks:` key — `/handoff` does not yet.

## The cluster — ClickUp ↔ clawgate ↔ Cairn
All six ClickUp tickets came from the **2026-08-26 harness / knowledge-sharing meeting**
(Discord voice, ~122 min; Zach + two teammates) — the same date `handoff-cairn.md`
records as when the system was presented. They are the team-facing half of Cairn.
**None of them contains the word "cairn"**, which is why they are invisible to a
name search; the `project:cairn` tag now fixes that.

🔴 **The ClickUp ids are deliberately NOT in this public repo.** They are a real
team-workspace namespace; this file is on `main` of a PUBLIC repo. The mapping is not
lost — each mirrored card carries its ticket as a `clickup:` tag on the personal clawgate
board, so the column below is recoverable in one read:
```bash
clawgatectl task ls --summary | jq -r '.[] | select(.id>=362 and .id<=365) | "cg#\(.id) \(.tags|join(","))"'
```

| ClickUp | clawgate | what it maps to in Cairn |
|---|---|---|
| *(`clickup:` tag on the card)* | **cg#364** | the subsystem store + `cairn recall` |
| *(`clickup:` tag on the card)* | **cg#363** | `/handoff` |
| *(`clickup:` tag on the card)* | **cg#362** | `cairn who` / transcripts |
| *(`clickup:` tag on the card)* | **cg#365** | the `Claude-Session:` commit trailer |
| *(not mirrored — no card)* | — | a teammate's private-repo migration — the blocker |
| *(`clickup:` tag on the card)* | cg#256 | complete; the precedent |

## Open investigations — live diagnosis state

### Have the hand-corrected `repo` values on cg#362–365 already reverted?
- **Symptom + exact repro:** the mirror re-patches `repo` from config on every UPDATE,
  so a hand correction survives only until the ClickUp ticket's content hash moves.
- **Observed (with values):** `mirror.py:1025-1026` — `if payload.get("repo"):
  full["repo"] = payload["repo"]`, unconditional once an UPDATE is chosen.
  `mirror.py:641-644` — `plan()` returns UNCHANGED only while
  `row["content_hash"] == content_hash(payload)`, compared against the **ledger**, not
  against clawgate's live state. `repo_for()` (`:351-354`) keys on `list_id` only, and
  `by_list_id` maps the synced-team list id → `civitai/civitai`. (The literal list id is
  in `config-configmap.yaml`; it is a real workspace id, so it is not written here.)
- **Ruled out:** "it reverts on the next run" — that was my first reading and it is
  wrong; an unchanged ticket is never patched at all.
- **Leading hypothesis:** still correct as of session end (verified by re-read), and it
  will revert silently the first time any of those four ClickUp tickets is edited.
- **RE-PROBED 2026-08-29 — STILL NOT REVERTED.** Live read: cg#363/364/365 all
  `repo=innovation-upstream/devrc`, cg#362 `repo=` (unset) — exactly the hand-corrected
  values. Nothing read `civitai/civitai`. ⚠ This is a **negative** observation and it does
  NOT weaken the hypothesis: the mechanism only fires when a ticket's content hash moves,
  so "not reverted yet" is the predicted state, not a refutation. The window stays open
  until one of those four tickets is edited.
- **Next probe, verbatim:**
  ```bash
  clawgatectl task ls --summary 2>/dev/null | jq -r '.[] | select(.id>=362 and .id<=365) | "cg#\(.id) repo=\(.repo)"'
  ```
  Anything reading `civitai/civitai` has reverted; that is cg#429's regression case.

### Which session id is canonical — the commit trailer's or the transcript's?
- **Symptom:** cg#365's closing condition is "git blame a line → resolve the session →
  wake it". It breaks at the join, and breaks *silently*.
- **Observed (with values):** **76 of the last 100** commits on `origin/main` carry
  `Claude-Session: https://claude.ai/code/session_01EUzHBeB8LfTDwzCxnAJn2d`;
  **36 distinct tokens across 200 commits**, so it is a real per-session id. The
  transcript is `ee538682-d05e-41aa-8967-5a1f30554597.jsonl` — a uuid. Searching a
  session's own transcript for its own claude.ai token returned **0**.
- **Ruled out:** "the transcript carries the token, so a resolver is buildable." A
  follow-up `grep -rl` DID match that file — but only because the first grep's own
  command text had been appended to the transcript in between. **Self-contamination**;
  the `0`, taken first, is the valid reading.
- **Leading hypothesis:** two genuinely separate id spaces with no mapping on disk.
- **Next probe:** decide rather than measure — stamp the transcript uuid, stamp both, or
  have the ingestion service (cg#362) own the mapping. It is a decision, not a bug.

### CLOSED — the required Tekton checks settled green (was: do they ever settle?)
- **Observed (with values):** checks were absent entirely (`no checks reported on the
  branch`) after two pushes, then `pending` for ~20 min on head `485202f8`, then
  **both PASSED**: pytests `collected=18905 passed=18903 skipped=2 failed=0`, nodetests
  `1366/1366`. A bounded 25-minute poll settled it; branch protection was never touched.
- **Ruled out, and worth keeping:** `no checks reported on the branch` is Tekton not
  having picked the head up yet — it is NOT a pass and NOT `CLAUDE.md`'s recorded
  stuck-forever mode. Three states, not two, and only the third needs a fresh push.
- **Resolution:** ordinary queue latency. The rival (a `timeouts.tasks` run leaving
  checks pending forever) did not fire. Waiting beat reaching for the escape hatch.

### `test_git_repo_isolation` co-tenancy flake — FILED as cg#439, not open here
- **Observed:** the SAME sandbox derivation failed then passed (identical store path);
  fails on BOTH tiers; passes **5/5 in isolation**; the counted co-tenant was a `git
  fetch` from a DIFFERENT test's temp dir (`test_a_hanging_fetch_is_BOUNDE0/hangs`).
- **Ruled out:** caused by #1049 — the file is byte-identical to `origin/main`, and the
  same-derivation flip settles it independently.
- **Leading hypothesis (UNCONFIRMED, in the card):** `live_cotenants`
  (`scripts/testlib/gitenv.py:810`) excludes `_own_process_lineage()` = ANCESTORS, so a
  `git` DESCENDANT the test itself spawned is not excluded.
- **Next probe:** work cg#439, not this doc.

## Next steps (ranked)
1. ✅ **DONE 2026-08-29 — #1049 merged, verified BY CONTENT, cleanup complete.**
   `mergedAt=2026-08-30T03:05:48Z`, squash `ee5b2b7b`. All **6** changed blobs at PR head
   `485202f8` are byte-**identical** to `origin/main` (`index-store.md`,
   `subsystem_recall.py`, `subsystem_resolver.py`, `run-tests.sh`,
   `test_subsystem_resolver.py`, `test_subsystem_task_refs.py`) — the ancestry check was
   never consulted. Cleanup: `cairn-task-linkage-1` was **already released**; the base
   clone was **already** at `origin/main` (no ff-merge needed); worktree
   `~/workspace/devrc-cairn-task-refs` removed after re-verifying `dirty=0 unpushed=0` at
   the moment of removal. ⚠ **The "three audit leftovers" claim was WRONG** — there are
   **~60** worktrees under `~/workspace/devrc/.claude/worktrees/agent-*` plus ~40 sibling
   `~/workspace/devrc-*` ones. That is a real backlog with its own owner (the
   `worktree-prune` index entry, 🔴 1 OPEN); do NOT mass-remove it as cleanup for this
   effort. One of them is `locked`.
2. **IN FLIGHT — PR #1039, this doc's own branch.** It carries
   `claudedocs/handoff-cairn-task-linkage.md`, so THIS FILE does not exist on `main` until
   it lands. Its effective diff vs `main` is **this one file only** (302 lines); the other
   10 paths `gh pr view --json files` reports landed on `main` independently and are
   already identical. It is 33 commits behind `main`, which does not block (`strict:
   false`). Merge it, or the next `/resume` finds no handoff at the path the kickoff names.
3. **cg#428 Layer B — criterion 4 (URL resolution).** BLOCKED on devrc#1011. It
   IMPORTS `scripts/collector/mention_scan.py`'s `CLICKUP_TASK_URL` / `_github_url` /
   `clawgate_url`; do not write a second resolver. Files:
   `scripts/lib/subsystem_resolver.py`, `scripts/tests/test_subsystem_task_refs.py`.
4. **cg#429 — clickup-mirror per-task repo override.** repo `homelab-talos`, files
   `scripts/clickup-mirror/mirror.py`,
   `clusters/workbench/apps/clickup-mirror/config-configmap.yaml`. Run the repo-revert
   probe first (below); a revert is its regression case.
5. **Decide cg#365's canonical session id.** A decision, not a build.
6. **Put `cairn` on PATH.** Two lines in `nix/home.nix` mirroring `claim-work`
   (`:1238`, `mkOutOfStoreSymlink`) + a switch. Small, unblocked.
7. **BLOCKED — do not start:** cg#362 and cg#363 wait on the teammate's private-repo
   migration ticket. ⚠ That is the **one** redacted id with no clawgate mirror, so unlike
   the other five it is NOT recoverable from a `clickup:` tag — find it in the ClickUp
   list by its description (the private-repo migration, from the 2026-08-26 meeting).

## Gotchas / decisions / dead-ends
- 🔴 **cg#428 COLLIDES WITH OPEN PR #1011 (`feat/mention-detection-click-to-open`), and
  the claim lock structurally cannot see it.** That PR ships
  `scripts/collector/mention_scan.py` and `scripts/mention-open.py` — a ref grammar AND
  a resolver for **clawgate / GitHub / ClickUp**, the same three systems cg#428 names,
  with disambiguation already settled (`owner/repo` → GitHub, unambiguous) and constants
  `PLATFORM_CLAWGATE / PLATFORM_GITHUB / PLATFORM_CLICKUP`. cg#428's criteria 1, 3 and 4
  are the same predicate. **Import it; do not write a second one** —
  `claude/RULES.md` "one rule, one place": a predicate open-coded at N sites is wrong at
  N−1 of them. Found by `gh pr list`, invisible to `claim-work` because
  `mention-detection-2` and a Cairn front-matter slug would never collide.
- 🔴 **OPERATOR DECISION 2026-08-29 — the scope boundary, and it RETRACTS a finding.**
  *"clickup and cairn will be used by the team (including me), clawgate is only used by
  me."* Consequences: (a) the clickup-mirror's `scope.assignee_id` pin (a single operator
  id, in the ConfigMap) is
  **correct by design, not a defect** — this session first reported it as a gap ("the one
  item gating this one is the one item this board cannot show") and that was wrong;
  (b) it **closes** the "should the mirror pull everyone's tasks" question — it should
  not; (c) a Cairn entry carrying `clawgate:364` points at a board no teammate can open,
  so cg#428's resolver must say "personal board", never 404.
- 🔴 **OPERATOR DECISION — `/resume` MAY take a hard workbench-cluster dependency.**
  This reverses one of the two grounds on which `claudedocs/design-claim-by-push.md`
  rejected moving next-step items into clawgate. The other ground (it inverts the ranked
  list's primacy) still stands and is unanswered.
- 🔴 **A card for bridging `claim-work` to clawgate task ids was drafted and NOT filed —
  do not re-derive it.** Phase 0 stopped it twice over: `design-claim-by-push.md`
  rejected the parent idea outright, and **cg#383's own Assumptions section** says *"If
  the answer is instead 'move items into clawgate', that assumption is what changes — say
  so rather than half-doing both."* The only surviving variant is a pure string transform
  with no network call (`--slug-for-task 364` → `clawgate-364`). Recorded as a comment on
  cg#383, not as a new card — filing one would have duplicated an open card, which is the
  exact collision class this session was investigating.
- **The front-matter census, measured across all 131 entry files:** `service` 118,
  `aliases` 117, `sensitivity` 96, `scope` 95, `created_by` 95, `namespace` 32, `repo` 23.
  **Zero** carry task, pr/issue or session. Coverage of what exists is uneven — 36 lack
  `sensitivity`, which the reader fail-safes to `client-confidential`.
- **`DELETE /api/tasks/{id}/comments/{cid}` TOMBSTONES, it does not remove.** Returns
  HTTP 200; the row persists with `body` length **0**, so `commentCount` still counts it.
  "HTTP 200 = deleted" is an incomplete reading.
- 🔴 **A grep for a claim's wording cannot distinguish an assertion from a RETRACTION of
  it.** Verifying the cg#363 correction by grepping for the wrong claim's phrase returned
  "STILL THERE" — on the corrected comment, which quotes the phrase in order to retract
  it. Reading the bodies is what settled it.
- **The front-matter parser's line-based hazard is narrower than documented.** A wrapped
  `aliases:` (a key the parser READS and type-checks) → entry `MALFORMED`, invisible to
  index/`--ref`/`--search`. A block-style **unknown** key (`tasks:`) parses clean and is
  silently ignored. Both reproduced against the real reader with `--store <tmp>`. So the
  hazard is prospective for cg#428: it arrives the moment `tasks:` becomes a read key.
- **The clawgate tag grammar is the wrong model for a Cairn ref.** Charset
  `[a-z0-9._/-]`, one colon, 64 runes — `#` is illegal, so `github-mirror` flattens to
  `github:<slug>-<n>` with a sha1 fold-in and its own docstring calls the failure *"a
  silent correlation collapse."* `github:zacxdev-homelab-infra-429` is equally
  `zacxdev/homelab-infra#429` or `zacxdev-homelab/infra#429`. Lossy tag encodings must be
  DERIVED from the lossless ref, never parsed back into one.
- **The writeback guard fired three times (362, 364, 383) and all three dismissals were
  correct.** A triage session reads many cards while its file-writes go to scratch drafts
  for *other* cards; the guard cannot tell work-for-this-card from work-adjacent-to-it.
  It fails toward asking, which is the safe direction. The cost scales with cards touched.
- **`cairn sync` says "live" about the FETCH, not the content.** It printed `live —
  fetched just now — 129 entries` beside `seeded=2026-08-29T00:10:55Z`. Read the
  `seeded=` stamp.
- **Hosted and local Cairn stores are different and will drift.** `cairn recall` reads
  the pod (19 devrc entries); `subsystem_recall.py`, which `/resume` runs, reads this
  host's disk with no network (18). Neither is wrong.
- **The merge path was not clean at session end:** of the ten most recent open devrc PRs,
  three `BLOCKED` and the rest `UNKNOWN`. Confirm the gate is green before assuming
  cg#428 can land.

- 🔴 **OPERATOR DECISIONS 2026-08-29/30, all four explicit.** (a) **Merge** #1049,
  squash. (b) **Accept the leaked ClickUp id — do NOT rewrite the branch.** (c) **File
  the flake** → cg#439. (d) **Stop the audit ladder at round 3.**
- 🔴 **OPERATOR DECISION 2026-08-29 — REDACT THE CLICKUP IDS BEFORE THIS DOC LANDS ON
  PUBLIC `main`.** The previous session's acceptance of the leaked id (decision (b) below)
  was about a PR ref that was *already public and unrecoverable*; putting the same class
  of id onto `main` **deliberately** is a different decision and was still avoidable.
  Redacted from this file: the six `868…` ClickUp task ids, the mirror's `assignee_id`,
  and the synced-team `list_id`. Two teammates' first names went with them.
  **The redaction is LOSSLESS and that was verified, not assumed** — cg#362/363/364/365
  each carry a `clickup:868…` tag on the personal clawgate board, so the whole ClickUp
  column is one `clawgatectl task ls` away for the one reader who has that board.
  ⚠ **Deliberately NOT redacted: the `Claude-Session:` token** in the cg#365 section — it
  already appears in **9 of the last 200** commit *messages* on `origin/main`. Scrubbing
  the prose while it sits in the git log is theatre, and would have read as a guarantee
  this repo does not provide.
- 🔴 **THE "SQUASH KEEPS MAIN CLEAN" CLAIM WAS FALSE, AND IS RETRACTED.** A real
  team-workspace ClickUp id shipped in commit `c7049b11`, which is already pushed to
  `origin/feat/cairn-task-refs` and `refs/pull/1049/head` on a PUBLIC repo. GitHub
  serves that blob indefinitely and the PR ref survives branch deletion — **no merge
  strategy removes it.** Only a branch rewrite + force-push before merge would, and even
  that leaves the orphan until GC. The PR body and all comments were redacted and the
  tree is clean; `origin/main` never carried it. Operator accepted the residue.
- 🔴 **THE AUDIT LADDER: EACH ROUND'S FIX CREATED THE NEXT DEFECT. Twice.**
  R1: the block-form fix closed the corruption for CLEAN input only — an empty item
  (`- ` with trailing space, or a bare `-`) resurrected the phantom-key corruption AND
  made it worse, because `tasks` then read falsy so the entry LOADED CLEAN with
  `tasks=[]` and `--validate` printed OK.
  R2: the fix for THAT added a blank-line bound that broke **valid YAML** — verified
  against PyYAML — to prevent a hazard that does not exist (a `- ` list binding to the
  nearest preceding key IS what YAML does). Reverted; the guard moved to the outer loop.
  R3: sound. No regression over 81,160 fuzzed shapes and 122 live entries.
- 🔴 **THE ORACLE FOR "WHAT DOES THIS FRONT MATTER MEAN" IS PyYAML, NOT THE AUTHOR.**
  The guard written for the imaginary hazard asserted my own belief about a bare key,
  passed, and the parser was meanwhile disagreeing with every other reader of the same
  bytes. A test that pins the author's model cannot catch the author being wrong about
  the format. Now a differential over 8 shapes; PyYAML is `importorskip`, present in
  BOTH tiers (`gatePyEnv`, `flake.nix:100-107`), and the phantom-key half asserts
  without it.
- 🔴 **A FIXTURE DERIVED FROM THE CONSTANT UNDER TEST CANNOT SEE THAT CONSTANT CHANGE.**
  The `TAG_MAX_RUNES` boundary test built both fixtures by arithmetic from the constant,
  so `64 -> 65` survived anyway. Constant now pinned as its own assertion, fixtures
  literal. `claude/RULES.md` names this trap; it was walked into while avoiding a
  different rot.
- 🔴 **THE DRIFT CEILING FIRED ONLY ON THE MERGED TREE — the argument for gating one.**
  `origin/main` collected 10137, the branch 10112, both under the 8217+2054=10271
  ceiling; their merge collects 10319 and crosses it. With `strict: false` nothing
  checks that automatically. **And the fix has an ORDER**: pinning 10269 while the
  branch still collected 10112 would have put the branch UNDER its own new floor and
  turned its required checks red — so `main` is merged INTO the branch first. Pin a
  floor on the tree you measured.
- 🔴 **A DUPLICATE LANDED MID-SESSION: #1069 re-pinned the skill-listing measurements
  while this branch was gating**, doing exactly what commit `8ceabaa4` here did. The
  pre-merge sweep could not see it — #1069 did not exist when the sweep ran. Resolved to
  `main`'s copy; **both sides had pinned IDENTICAL values** (38 / 24 / 8,909 / 9,120 /
  13,106, ceiling 9,200), which is two independent measurements agreeing. `rerere` was
  DISABLED for that merge — it has previously replayed a stale resolution onto a floor
  line, and the file next door in this PR is a floor table.
- 🔴 **`gh pr checks` reported "no checks reported on the branch" TWICE after a push** —
  that is Tekton not having picked the head up yet, NOT a passing state and NOT the
  stuck mode. Distinguish before acting on it.
- ⚠ **TWO EQUIVALENT MUTANTS ARE DOCUMENTED IN THE SOURCE, deliberately not "fixed":**
  removing either blank-line `break` in `_block_items_from`/`_block_ends_at`, and
  narrowing the outer skip to `startswith("- ")`. Each is masked by the other guard.
  Both are named in docstrings so the next reader does not delete the load-bearing half.
- ⚠ **`git cat-file -e <ref>:<path>` failing can mean the REF is unfetched, not that
  the path is absent.** That reading nearly concluded this doc did not exist on its own
  branch. Fetch the ref first, then ask.
- ⚠ **A SCOPED NUMBER MUST CARRY ITS SCOPE.** "1,729 green" (four suites) and the
  byte-identity figures (`subsystem_recall.py --scope devrc` text output) were both true
  and both unreproducible by an auditor who ran the full suite / a different tool.
- ⚠ **`clawgatectl task create` flags are `--directory` and repeatable `--tag`** — not
  `--dir`/`--tags`. And its PreToolUse gate cannot read a `--body-file` whose path is a
  shell substitution; use a heredoc `--body`.
- **This doc's `clawgate-task:` is 364; this session's WORKED task was 428.** Left as
  364 deliberately (`clawgate_handoff.sh field` → rc 0 ⇒ leave it alone). 428 is
  cross-linked to 364.

## How to verify
```bash
# the PR and its checks
gh pr view 1049 --repo innovation-upstream/devrc --json state,mergeable,mergeStateStatus
gh pr checks 1049 --repo innovation-upstream/devrc

# both gate tiers on the branch (BOTH are required; they are different tiers)
nix develop ~/workspace/devrc-cairn-task-refs -c bash ~/workspace/devrc-cairn-task-refs/scripts/gate.sh
nix build ~/workspace/devrc-cairn-task-refs#checks.x86_64-linux.pytests --no-link; echo "rc=$?"

# the schema itself, end to end
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc-cairn-task-refs/scripts/tests/test_subsystem_task_refs.py -q

# cg#362-365 repo values have not reverted (rank 4's regression case)
clawgatectl task ls --summary 2>/dev/null | jq -r '.[] | select(.id>=362 and .id<=365) | "cg#\(.id) repo=\(.repo)"'
```
