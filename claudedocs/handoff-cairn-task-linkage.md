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
- **Branch / PR:** nothing in flight. No PR opened this session; the work was a deploy +
  two operator arming acts + one clawgate comment. Base clone on `main` at `7a2003d2`.
- 🔴 **RANK 1 IS DONE — both hosts DEPLOYED and the trailer is ARMED.** This supersedes the
  previous "DEPLOY STATUS: NOT DEPLOYED" block, which was stale in both directions: the
  workbench was already at `origin/main` and switched, and the laptop was on `911af220`,
  not the `7ed7d41a` the doc recorded.
  - `scripts/ship.sh` — laptop fast-forwarded `911af220 -> 7a2003d2`; workbench already
    there. **Cross-host agreement asserted by the tool**: both at `7a2003d2`. Managed
    artifacts resolve and are current on both (workbench 570 checked / 399 repo-sourced,
    laptop 516 / 384; 0 dangling, 0 stale).
  - **Hook half ARMED on BOTH hosts** — a `PreToolUse` entry added to each per-host
    `~/.claude/settings.json` (workbench 5→6, laptop 4→5; backups at
    `~/.claude/settings.json.bak-session-stamp`). 🔴 Registered with the absolute
    `/nix/store/9ka5…-python3-3.12.14/bin/python3.12` the sibling hooks use, **not** the
    bare `python3` the installer's printed snippet suggests — a bare command name dies
    during a `home-manager switch` while the profile is momentarily blank.
  - **Git half ARMED on BOTH hosts** — `scripts/install-session-stamp.sh --repo
    ~/workspace/devrc --apply`, writing `.git/hooks/prepare-commit-msg`.
    `core.hooksPath` was unset on both, so `.git/hooks` is what git reads.
- **Deploy/verify status — VERIFIED AT THE CONSUMER, with both controls.** Not inferred
  from the deploy:
  - the recorder fired in an **already-running** session — the `settings.json` edit took
    effect with **no restart** (state file `~/.cache/claude-session-trailer/2131466.json`
    appeared after one commit-shaped Bash call);
  - a **real `git commit`** in a throwaway repo with the hook installed produced a commit
    object carrying `Claude-Session-Id: 8cdbb099-7deb-4b2a-a34c-320fb8539a73`, readable
    back via `git log -1 --format='%(trailers:key=Claude-Session-Id,valueonly)'`;
  - the transcript exists at that uuid (460K), so `claude --resume 8cdbb099-…` is the wake
    handle. **blame → id → live session closes.**
  - **negative control:** with no recorded session the message is left **byte-identical**
    (the human-commit case). **positive control:** exactly one trailer line with one.
  - ⚠ **Scope:** verified on a throwaway repo's commit object and by exercising the **real
    installed hook** in the devrc clone against a message file. **No trailer has yet been
    observed on a commit that landed on devrc `main`** — that is rank 3.
- **Filed:** comment **570 on cg#365** (not a new card — cg#365 is open and this is its
  closing condition; filing one would have duplicated it).
- ⚠ **This checkout is shared and another session is editing it right now** — `M
  nix/graphical.nix` plus two untracked files appeared mid-session. `nix/graphical.nix`
  **is** a nix-read path, so the next `ship.sh` will classify it dirty-and-deployed.
- **Carried forward — ranks 1, 2, 5, 6 of the ORIGINAL list remain done** (devrc#1049
  `485202f8`; devrc#1039 → `bd63b7bd`; devrc#1083 → `3568530d`; devrc#1079 → `7ed7d41a`;
  devrc#1082 → `3b1a0477`), and **cg#445 is still filed and open**.
- ⚠ **The merged tree for #1083 was still never gated** — unchanged from the previous
  session, and arming does not change it.

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

### CLOSED — the coverage figure, and the instrument that produced three wrong ones
- **Observed (with values):** `Claude-Session:` trailer coverage on `origin/main` at
  `3b1a0477`, counted PER COMMIT: **47 of the last 100, 67 of the last 200**. Confirmed by
  three independent pipe-free methods and by the round-9 auditor.
- 🔴 **The instrument was the whole story.** Counting with `git show … | grep -q` under
  `set -o pipefail` UNDERCOUNTS: `grep -q` exits at the first match, `git show` can die of
  SIGPIPE, and `pipefail` promotes that to a failed pipeline — dropping a commit that DID
  match. Reproduced on the same 200 commits:
  ```
  with `set -o pipefail` + `grep -q`  ->  55   (41 at n=100)
  with `set -o pipefail` + `grep -c`  ->  67   (no early exit, no SIGPIPE)
  same loop without pipefail          ->  67
  pipe-free (one subprocess/commit)   ->  67
  ```
  It is a RACE, not a rule — only ~12 of 67 drop, which is why the wrong number lands near
  the truth and gets believed. And 55 is not even stable (55,55,55,56,55).
- **Ruled out:** "anchoring changes the count" — it does not (67 either way); anchored vs
  unanchored only matters when counting LINES.
- **Every wrong figure in the feature's history traces to that one shape**, including a
  41%/27% I adopted from an auditor without re-measuring and shipped into a docstring.

### CLOSED — GUARD 10 on this box is an ENVIRONMENT verdict, not a code one (now cg#445)
- **Observed (with values):** `gate.sh` in a worktree of the shared clone fails GUARD 10
  with *"the plugin could not show its 'git config --global' write was CONTAINED … Its zero
  is not evidence"* — `git config --global` exit 255, lock-contended after 6 attempts.
  Full 2×2, one variable at a time:

  | | worktree of shared clone | isolated clone |
  |---|---|---|
  | `main` unmodified | not measured | **PASS** |
  | branch under test | **FAIL** | **PASS** |

- 🔴 **The control that comes to hand first proves nothing.** Comparing `main`-in-an-
  isolated-clone against a branch failing in a *worktree* varies the code AND the
  environment; its PASS reads as "your diff is guilty" and sends the next person debugging
  an innocent change. Hold the environment fixed; vary only the code.
- **Ruled out:** the code. Tekton was green throughout, and `CLAUDE.md` explains why the
  tiers legitimately disagree — GUARD 10's `NOGIT_REPO_LOCAL` is EMPTY in the nix sandbox,
  so it is not the same guard there.

### CLOSED — `nix build path:<a git WORKTREE>` breaks every git call in the sandbox
- **Symptom + exact repro:** the merge-gating sandbox tier failed at GUARD 10's preflight,
  before any test ran, on a branch whose diff could not reach `git config`.
- **Observed:** a worktree's `.git` is a FILE (`gitdir: …/worktrees/<name>`), and `path:`
  copies it into the build, so `src` looks like a repo whose gitdir is unreachable:
  ```
  $ printf 'gitdir: /nonexistent/worktrees/x\n' > $D/.git
  $ GIT_CONFIG_GLOBAL=$D/gc git -C $D config --global probe.k v
  fatal: not a git repository: (null)
  ```
- 🔴 **A five-build bisect "proved" it was nixpkgs' `patchShebangs`. That was WRONG and is
  RETRACTED.** Every bisect copy was a `cp -a` with `.git` removed (per RULES.md's
  worktree-copy rule), so each step silently varied `.git` presence alongside the file
  under test. **Build the sandbox tier from a real CLONE, never from a worktree.**

### CLOSED — "which session id is canonical" was answered IN CODE, not left open
- **Resolution:** the doc carried this as an open decision ("stamp the uuid, stamp both, or
  have cg#362 own the mapping"). The merged implementation already decided, and chose the
  resolvable id. `scripts/lib/session_trailer.py` → `TRAILER_KEY = "Claude-Session-Id"` —
  a **distinct key** from the convention-driven `Claude-Session: https://claude.ai/…` —
  stamping the hook payload's `session_id`, i.e. the **transcript uuid**, which is what
  `claude --resume` takes.
- **Observed:** `scripts/git-hooks/prepare_commit_msg.py`'s docstring states it
  deliberately does NOT emit the claude.ai URL and warns against synthesising one from the
  uuid ("the resulting link would not resolve").
- **Consequence:** the two id spaces are no longer conflated — different keys, only the
  resolvable one stamped. The 2026-08-29 finding that the join broke was correct **then**
  and is now superseded. Do not re-derive it.

### 🔴 OPEN — a single-paragraph commit message is stamped with NO blank line, so git does not parse it as a trailer
- **Symptom + exact repro:** `git commit -m "test: does a real commit carry the trailer"`
  in a repo with the hook installed. The stamp lands on line 2 with no separating blank
  line; `git log -1 --format='%(trailers:key=Claude-Session-Id,valueonly)'` returns
  **empty**. The id is present as text and **invisible to every standard trailer
  consumer** — which is cg#365's entire closing condition.
- **Observed (with values):** `append_trailer` over three shapes, parsed back with `git
  interpret-trailers --parse` as an independent oracle:
  ```
  single-line      'fix: one line only\n'                   -> ''                    <-- FAILS
  with body        'fix: x\n\nbody here.\n'                  -> 'Claude-Session-Id: …'
  existing trailer 'fix: x\n\nbody.\n\nCo-Authored-By: …\n'  -> both trailers
  ```
  git's own implementation disagrees on the same input — it inserts the blank line:
  ```
  $ printf 'fix: one line only\n' | git interpret-trailers --trailer "Claude-Session-Id: X"
  fix: one line only
                            <-- blank line git adds
  Claude-Session-Id: X
  ```
- **Ruled out:** "the existing tests would have caught it" — they assert the trailer
  *text* is present, which is true in all three shapes. A test name wider than its
  assertion.
- **Leading hypothesis:** `append_trailer` appends directly instead of inserting a
  separator when the message has no trailer block to join.
- **Next probe:** none needed — it is diagnosed. Write the fix. 🔴 **Do NOT always append
  `\n\n`**: that inserts a blank line inside an existing trailer block and breaks the third
  shape, which works today. Condition is "add a separator only when there is no trailer
  block to join". Recorded in full as comment 570 on cg#365.

## Next steps (ranked)
🔴 **RANKS RENUMBERED this session** — the previous rank 1 (deploy + arm) is DONE, so
everything shifted. No live `claim-work` claims existed on this doc at renumber time
(`cairn-task-linkage-1` was taken and released within this session), so nothing was
silently re-pointed.

1. **cg#365 — fix the single-paragraph trailer separator.** Diagnosed above and in
   comment 570; the acceptance criteria are mechanical. Repo `innovation-upstream/devrc`,
   files `scripts/lib/session_trailer.py` (`append_trailer`) and its tests. Done means
   `git interpret-trailers --parse` returns the id for all three shapes, pinned by a test
   watched to fail on pre-change code (single-line RED at the base ref, green at HEAD).
   forcing: user — cg#365 is a mirrored ClickUp ticket (`868kx9ev6`) raised by Zach and
   two teammates at the 2026-08-26 harness meeting; the trailer is now ARMED on both
   hosts, so the defect is live rather than hypothetical.
2. **cg#445 — GUARD 10 is unmeasurable on the workbench**, so `gate.sh`'s pytest tier is
   red for attribution-only reasons on any run from a worktree of the shared clone.
   Unchanged this session. 🔴 **This is the THIRD rediscovery** — the index shows the same
   class found on 2026-08-22 and 2026-08-28 before that, each time costing control runs.
   forcing: gate — a required local gate is permanently red for reasons unrelated to any
   change, which `claude/RULES.md` names as worse than no gate.
3. **Confirm the trailer on real devrc commits.** The only claim this session could not
   make. `git -C ~/workspace/devrc log -8 --format='%h %(trailers:key=Claude-Session-Id,valueonly)'`
   on `main` in a day or two — agent commits should carry it. A run of blanks means the
   arming did not take across other sessions, not that the hook is broken.
   forcing: none — cheap observation, nothing external waiting.
4. **cg#428 Layer B — criterion 4 (URL resolution).** BLOCKED on devrc#1011. Must IMPORT
   `scripts/collector/mention_scan.py`'s `CLICKUP_TASK_URL` / `_github_url` /
   `clawgate_url`; do not write a second resolver. Files
   `scripts/lib/subsystem_resolver.py`, `scripts/tests/test_subsystem_task_refs.py`.
   forcing: none — blocked, and nothing external is waiting.
5. **cg#429 — clickup-mirror per-task repo override.** Repo `homelab-talos`, files
   `scripts/clickup-mirror/mirror.py`,
   `clusters/workbench/apps/clickup-mirror/config-configmap.yaml`.
   forcing: none — re-probed again this session (2026-08-30): cg#363/364/365 still read
   `innovation-upstream/devrc`, cg#362 still unset. The mechanism is real and has not fired.
6. **BLOCKED — do not start:** cg#362 and cg#363 wait on the teammate's private-repo
   migration ticket.
   forcing: none — blocked on a third party.

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

- 🔴 **THE LOCAL GATE IS RED ON THIS BOX FOR ATTRIBUTION-ONLY REASONS — cg#445, and the
  first control run was CONFOUNDED.** `gate.sh` in a worktree of the shared clone fails
  GUARD 10: *"the plugin could not show its `git config --global` write was CONTAINED …
  Its zero is not evidence"* — `git config --global` exit 255, lock-contended after 6
  attempts. Not a test failure: the instrument is refusing to vouch for itself, correctly.
  Cause is the git **common** config, shared by ~117 worktrees plus concurrent sessions.
  Measured 2×2:

  | | worktree of shared clone | isolated clone |
  |---|---|---|
  | `main` unmodified | not measured | **PASS** |
  | branch under test | **FAIL** | **PASS** |

  ⚠ **The control that comes to hand first proves nothing.** Running `main` in an
  *isolated clone* against a branch failing in a *worktree* varies the code AND the
  environment at once — its PASS reads as "your diff is guilty" and would send you
  debugging an innocent change. Hold the environment fixed and vary only the code.
  **Tekton is green throughout**, and `CLAUDE.md` explains why the tiers legitimately
  disagree: GUARD 10's `NOGIT_REPO_LOCAL` is EMPTY in the nix sandbox, so it is not the
  same guard there. Cost: two ~20-minute runs, and this is the **third** rediscovery
  (index `devrc/tests`, 2026-08-22 and 2026-08-28).
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
  already appears in the commit messages of **5 commits** on `origin/main` (unchanged
  across the last-100 and last-200 windows). ⚠ **An earlier revision of this line said
  "9 of the last 200" and that was wrong** — it was a `grep -c` **line** count, and each
  such commit repeats the token on ~3 lines, so it counted 15 lines as 9 and neither
  number was a commit count. Count commits, not lines. Scrubbing
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

- 🔴 **CARRIED FORWARD from the rank-5 decision block, which a REPLACE heading would
  otherwise have dropped — the two measurements that settled cg#365's design.**
  (a) **The hook layer's `session_id` IS the transcript uuid**: 69 of 69 per-session state
  dirs under `~/.cache/claude-clawgate-writeback/s/` are uuid-shaped, **zero** are
  `session_…` tokens. That is the mechanical confirmation that the claude.ai token in the
  commit trailer and the handle `claude --resume` takes are **disjoint id spaces**, and why
  searching a transcript for its own claude.ai token returns 0.
  (b) ⚠ **Do NOT assume uuid shape when writing it.** `scripts/lib/cairn_who.py` records 2
  of 41 windows carrying a `ses_…` token from a different runtime, and that a join assuming
  uuid shape "silently matches nothing". `session_trailer.py` treats the id as an opaque
  string throughout — validated for safety, never parsed or normalised.
- 🔴 **OPERATOR DECISION 2026-08-29 — REDACT THE CLICKUP IDS before this doc landed on
  public `main`.** Verified LOSSLESS, not assumed: cg#362/363/364/365 each carry a
  `clickup:868…` tag on the personal clawgate board. The one exception is called out in
  the doc — the unmirrored private-repo-migration ticket has no card, so it is findable by
  description only.
- 🔴 **OPERATOR DECISION 2026-08-30 — STOP THE AUDIT LADDER AT ROUND 10 AND MERGE.** The
  stop rule (a clean round) had NOT fired, and this was a deliberate override with a
  stated reason: the feature was behaviourally stable after round 9, and rounds 8-10 found
  **zero** payload defects — every finding was my verification being weaker than claimed.
  The rule had stopped measuring what it was built for.
- 🔴 **TEN ROUNDS, AND THE FINDINGS WERE ALMOST ALL IN THE PREVIOUS ROUND'S REPAIRS.**
  Worth carrying beyond this PR:
  - **The same class re-opened one axis narrower, three rounds running.** Self-repair
    clobbering hooks that were not ours: round 4 any `is_ours` match → round 5 + must name
    this checkout → round 6 + must actually fail to parse. Each fix was right about the
    case it named and left a residue beside it.
  - **The commit that REMOVES a hazard is the likeliest place to introduce its twin.**
    `is_legacy_ours` was deleted for deleting things that were not ours — and the same
    commit let the installer overwrite things that were not ours, via a different
    predicate, a few lines down.
  - **Six vacuous guards, one signature:** *prose explaining why a case is excluded,
    standing in for a test of that case.* Two I caught myself, only by running the mutant.
  - **A seventh shape: a test that arranges the right scenario and asserts the wrong
    observable.** It set up a broken `$TMPDIR` — exactly the condition needed — then
    asserted only that a string was absent, which an rc-1 abort satisfies too.
  - 🔴 **"This is an equivalent mutant, measured" is a label I introduced for honesty and
    then MISUSED within two commits** — on a `chmod` whose equivalence held only at umask
    022. A false untestability claim is worse than an unguarded guard: it tells the next
    reader not to look.
  - **A tautology I shipped:** `assert target.stat().st_dev == hooks.stat().st_dev` —
    `target` is a file INSIDE `hooks`, true on every host under every installer forever.
    It passed against the very installer it was added to catch.
  - **Fixtures mask dependencies:** deleting the load-bearing `mkdir -p "$HOOKS"` survived
    every test, because `git init` always creates `.git/hooks`.
- 🔴 **A refactor changed which promises the code kept.** Hoisting wrapper generation "up
  for reuse" made the DRY RUN create `.git/hooks` and made read-only paths abort rc 1; and
  moving a scratch file to `$TMPDIR` turned an atomic `rename(2)` into a cross-filesystem
  truncate-in-place. Neither looked like a behaviour change.
- ⚠ **`test_dry_run_changes_nothing` passed through the entire dry-run-writes-to-disk
  defect** — it asserts only that the hook FILE is absent. A test NAME wider than its
  assertion reads as coverage and delivers a slice.
- **`--emit-claims` / `audit-dispatch.py` refuses a delta round with no claims block, and
  that refusal is correct** — an empty "what was claimed fixed" turns a delta audit into a
  blind full audit that then reads as covered.
- **Known-open in #1083, deliberately:** `git commit -v` / `--cleanup=scissors` drops the
  trailer (editor-driven commits only; `-m`, which agents use, is fine); `transcript_path`
  is recorded and read by nothing; the same-pid `record()` race was analysed but never
  reproduced.

- 🔴 **OPERATOR DECISION 2026-08-30 — ARM BOTH HALVES, BOTH HOSTS, devrc only.** Asked as
  one question with the blast radius stated: `.git/hooks/` is the **common** git dir,
  shared by **123 worktrees** on this clone plus ~30 concurrently-claimed sessions. Chosen
  over host-at-a-time and over leaving it inert.
- 🔴 **THE TWO HALVES ONLY PRODUCE VALUE TOGETHER — staging them is not a risk ladder.**
  The recorder alone writes state files nobody reads; the git hook alone gets
  `lookup() → None` and leaves messages byte-identical. So "arm the safe half first" buys
  nothing: the safe half IS the inert half. It is arm-both or arm-neither.
- **What made arming defensible, checked before doing it, not after:** every path in both
  scripts is fail-open (`sys.exit(0)`, no permission decision emitted), the installed hook
  is a `/bin/sh` wrapper guarding on interpreter existence (a fix for a measured rc-1
  commit REFUSAL), the message write is `rename(2)`-atomic (a fix for a measured
  message TRUNCATION), and it is a no-op for human commits.
- 🔴 **The installer's own printed snippet uses a bare `python3` — do not paste it.** Every
  sibling hook in `settings.json` uses an absolute `/nix/store` path, because a bare name
  resolves through `~/.nix-profile`, which a `home-manager switch` blanks for ~1s. Verified
  the store path exists on BOTH hosts before using it.
- 🔴 **A `settings.json` hook edit takes effect in an ALREADY-RUNNING session** — measured,
  not assumed. Worth knowing both ways: it means arming is immediate, and it means an
  edit to that file reaches every live session on the host at once.
- 🔴 **`bash-guard.py` parses the command text BEFORE the command runs, so it cannot see a
  shell variable's value or a directory the same command is about to create.** Two
  refusals this session, both correct-by-design and both confusing at first read: `git -C
  "$T" commit` was judged against the caller's cwd (devrc/main) because `$T`'s value is
  not in the text, and `mkdir … && git -C /abs/path commit` was judged the same way
  because the path did not exist yet at parse time. Fix: create the directory in an
  EARLIER tool call and pass `-C` a literal absolute path.
- **A throwaway test repo defaults to branch `main`, which the guard blocks.** `git init -b
  probe-branch` — the ban is on the branch NAME, and a scratch repo is not exempt.
- 🔴 **`git interpret-trailers` is the right oracle for trailer questions, and it is free.**
  The single-line defect above was invisible to "is the string present?" and obvious to
  "does git parse it?". `claude/RULES.md`: the oracle for what a format means is the
  format's own implementation, not the author.
- **Filed as a COMMENT on the open card, not a new card.** cg#365's scope is exactly this
  defect. The previous session recorded the same reasoning for the `claim-work`↔clawgate
  bridge (comment on cg#383, no new card); this follows that precedent.
- ⚠ **`clawgatectl task create` is gated by a PreToolUse hook requiring a literal `##
  Acceptance criteria` heading** (level-2 ATX, not `###`, not bold, not inside a fence),
  and it routes you to `claude/skills/clawgate/flows/task-authoring.md` for the interview.
  `--body-file` with a literal path works fine for both `create` and `task comment`.
- ⚠ **`resume-state.sh` takes a PATH, and `~` inside quotes is NOT expanded.** This doc's
  own "Run this first" invocation quoted `"~/workspace/devrc/claudedocs/handoff-…"`, which
  resolved nothing: the run fell back to the newest of 89 handoffs and reconciled a
  DIFFERENT initiative, printing an explicit `!` gap saying so. Pass an unquoted or
  expanded absolute path.
- ⚠ **The doc's `#N` refs are still mostly bare, and the reconciler refuses to guess.**
  `resume-state.sh` reported `17 bare #N ref(s) could not be attributed` because the doc
  names three repos. Writing `owner/repo#N` is what makes them reconcilable.
- **This doc's `clawgate-task:` stays 364.** This session's WORKED task was **365**
  (`clawgate_handoff.sh resolve` → rc 0, `#365 role=worked`), but `field <doc>` → rc 0
  means a readable field is already present ⇒ leave it alone. Same call the previous
  session made for 428.

## How to verify
```bash
# both hosts converged (re-run any time; it is idempotent and READ-ONLY when clean)
bash ~/workspace/devrc/scripts/drift-check.sh

# the trailer is ARMED, not merely deployed — BOTH must be true, on BOTH hosts
readlink -f ~/.claude/hooks/session-stamp.py                     # -> /nix/store/... (deployed)
python3 -c "import json,os;s=json.load(open(os.path.expanduser('~/.claude/settings.json')));\
print('ARMED' if [h for h in s['hooks']['PreToolUse'] if 'session-stamp' in json.dumps(h)] else 'NOT ARMED')"
ls -l ~/workspace/devrc/.git/hooks/prepare-commit-msg           # the git half

# the round trip, end to end (what cg#365 actually asks for)
SID=$(git -C ~/workspace/devrc log -1 --format='%(trailers:key=Claude-Session-Id,valueonly)')
test -n "$SID" && ls -l ~/.claude/projects/-home-zach-workspace-devrc/"$SID".jsonl \
  && echo "wake it: claude --resume $SID"

# 🔴 the single-line gap — this is the OPEN defect; empty output on the first is the bug
printf 'fix: one line only\n' > /tmp/m1 && ~/workspace/devrc/.git/hooks/prepare-commit-msg /tmp/m1 message
git interpret-trailers --parse < /tmp/m1        # EMPTY today == the defect
printf 'fix: x\n\nbody.\n' > /tmp/m2 && ~/workspace/devrc/.git/hooks/prepare-commit-msg /tmp/m2 message
git interpret-trailers --parse < /tmp/m2        # prints the id == the working shape

# cg#362-365 repo values have not reverted (rank 5's regression case)
clawgatectl task ls --summary 2>/dev/null | jq -r '.[] | select(.id>=362 and .id<=365) | "cg#\(.id) repo=\(.repo)"'

# 🔴 the LOCAL gate must be run from an isolated CLONE, never a worktree (cg#445)
C=$(mktemp -d); git clone --quiet --no-hardlinks ~/workspace/devrc "$C"
cd "$C" && nix develop ~/workspace/devrc -c bash scripts/gate.sh
nix build "path:$C#checks.x86_64-linux.pytests" --no-link   # ONE AT A TIME
```
