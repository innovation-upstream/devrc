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
- **Branch / PR:** nothing in flight for this effort. Everything merged.
- 🔴 **RANK 2 IS DONE — cg#445 is fixed and merged.** devrc#1148 → squash **`8e8ee3bc`**,
  verified BY CONTENT on `origin/main` (`held_count` and `_config_write_lock` both present),
  never by ancestry.
  - **The card's stated mechanism was WRONG, and that is why it was rediscovered three
    times.** It blamed the git **common dir's** config shared by ~117 worktrees — an
    environmental fact, therefore unfixable, which is where 2026-08-22, 08-28 and 08-30 all
    stopped. The contended file was the guard's **own** `GIT_CONFIG_GLOBAL` target with a
    fixed filename (`guard_dir / "gitconfig"`), and `nogit_plugin.py`'s own comment said so.
  - 🔴 **The obvious fix (per-pid filename) would have broken GUARD 10 on EVERY target.**
    `scripts/run-tests.sh` builds that path independently in shell as `$NOGIT_CONFIG` and
    tests it for **equality** (`:4562`), and counts control keys in that **one** file
    (`:3023`). Neither greps for `guard_config_path`. Shipped instead: an advisory `flock`
    on a separate `<guard-dir>/gitconfig.pylock` — never `.lock`, which is git's own lock
    name for the same file.
  - **Four audit rounds, ending clean.** Payload lines per fix round: **113 → 69 → 35 → 0 →
    0**. Rounds 1–3 each found a defect created by the previous round's fix; round 4 found
    nothing and both stop conditions fired together (clean round + two consecutive
    zero-payload rounds).
  - **The three prior rediscoveries were 2026-08-22, 08-28 and 08-30**, each costing
    ~20-minute control runs, all recorded in the `devrc/tests` index entry.
- 🔴 **Cairn's integration is planned in its OWN doc — do not re-derive it here.**
  `claudedocs/plan-cairn-integration.md` (merged `093a63db`): 11 decisions from an alignment
  session — Cairn is a **per-person agent re-entry cache, multi-tenant, one store per
  person**, hosted pod canonical, local disk a read-through cache, automatic capture, age-out
  on read recency, opt-in sharing gated by `sensitivity:`. Phases 0–4 live there.
  **Store state measured 2026-08-31: workbench 146 entries / 15 scopes, laptop 47 / 12; 22
  distinct scopes of which 5 overlap and 7 are laptop-only.** The figures in the
  `subsystem-index` skill are from 2026-08-27 and are stale.
- ✅ **GATED ON THE MERGED TREE, three tiers** — `origin/main 74b427d5` + `21176d1d`, built
  in a real clone (never a worktree — `nix build path:<worktree>` breaks every git call in
  the sandbox), one at a time:
  - sandbox `pytests` → `/nix/store/gzx25hxsyzz8…` GREEN
  - sandbox `nodetests` → `/nix/store/s112ljw56ww0…` GREEN
  - dev-host `gate.sh --tier pytest` → `GATE: RESULT=PASS exit=0`, 19914 passed / 0 failed
  - 🔴 **The two sandbox tiers prove MERGE SAFETY ONLY.** `NOGIT_REPO_LOCAL` is empty in the
    nix sandbox, so GUARD 10 is not the same guard there. Only the dev-host tier is evidence
    about cg#445 — keep those two claims separate.
  - **The GUARD 10 evidence, on the merged tree:** **zero** occurrences of `unmeasured` or
    `lock-contended` in the whole run (grepped explicitly — that value is *pass-shaped* if
    you only read an exit code); `config-control` equals `plugin` on **all 38 targets**; and
    `scripts/validation/tests`, the exact target cg#445 named, reports `config-control=4
    plugin=4`.
- **cg#365 and cg#445 are both at `ready_for_review`, awaiting the operator's `complete`
  call** — neither body carries a `## Acceptance criteria` heading, so per the status gate
  an agent may not mark them complete.
- **The CI diagnosis went to cg#348 as comment 593**, not a new card — cg#348 already covered
  the priority mechanism, and cg#337/#303 are adjacent.
- ⚠ **The base clone `~/workspace/devrc` is on `main`, behind `origin/main`, with another
  session's uncommitted work** (`M nix/pkgs/default.nix`, plus untracked
  `output.txt`, `scripts/diagnose-nix-disk.sh`, `scripts/tests/test_opencode_rig_control.py`).
  Theirs to sync, not this effort's.

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

### 🔴 OPEN — a stale `<cfg>.lock` is self-inflicted and nothing cleans it up
- **Symptom + exact repro:** `_git`'s own `timeout=LOCK_TIMEOUT` SIGKILLs git; git does not
  remove its lockfile on SIGKILL. **One** timed-out `git config --global` write therefore
  poisons the guard dir for every remaining session and every remaining target.
- **Observed (with values):** measured at HEAD with the mutex working perfectly — after one
  timed-out write the guard dir holds `['gitconfig', 'gitconfig.lock']`, and the very next
  session gets `git config --global exited 255 — still lock-contended after 6 attempts`.
  `command grep` over `run-tests.sh` + the plugin found **no** stale-lock handling anywhere.
- **Ruled out:** that this is a regression from the mutex — it is not. Serialised writes are
  *faster*, so a timeout is strictly **less** likely at HEAD than at base. Pre-existing.
- **Leading hypothesis:** it is a real, unclosed hole. The mutex made it *legible*, not gone.
- **Next probe:** decide whether to close it (unlink a `<cfg>.lock` older than N seconds
  before the first attempt, with the risk that a live git write is mid-flight) or leave it
  documented. Worth its own card if it recurs.

### 🔴 CLOSED AS UNRECOVERABLE — which mechanism caused the 2026-08-30 red
- **Observed:** three distinct mechanisms produce a **byte-identical** `still lock-contended
  after 6 attempts` verdict: (a) genuine contention, (b) the mutex not held (fail-open), (c)
  a stale `<cfg>.lock`. The original incident was diagnosed from exactly that string.
- **Ruled out:** recovering it. No discriminator was recorded at the time and the run's
  artefacts are long gone (Tekton retention is `keep: 20`, hourly).
- **Resolution:** not knowable. The fix is verified against the mechanism it addresses; it is
  **not** proof about that specific incident, and the doc should not imply otherwise. This is
  the repo's own "an EMPTY RESULT cannot distinguish two mechanisms" rule landing on a
  diagnosis. **Closed forward:** the verdict now prints `mutex=held N/6,
  stale-git-lock=PRESENT|absent`, so a fourth rediscovery names its own mechanism.

## Next steps (ranked)
🔴 **NUMBERING IS STABLE — done items stay as tombstones, never removed or renumbered.**
The rank is half a `claim-work` slug's identity. Ranks 1, 2 and 3 are closed.

1. ✅ **DONE — cg#365's trailer separator.** Merged `ec102d00`. **Do not re-work.**
   forcing: none — closed.
2. ✅ **DONE — cg#445, GUARD 10 unmeasurable.** Merged `8e8ee3bc`, four audit rounds, gated
   on the merged tree across three tiers. `ready_for_review`; the `complete` call is the
   operator's. **Do not re-work.**
   forcing: none — closed.
3. ✅ **DONE — trailer confirmed on a real devrc commit** (`fa64c986`). **Do not re-work.**
   forcing: none — closed.
4. ⚠ **cg#428 — RE-CHECK BEFORE TOUCHING.** Was "BLOCKED on devrc#1011"; measured 2026-08-31
   it is **`ready_for_review`** with 5 comments — another session worked it. Read the card
   first. If anything remains it must IMPORT `scripts/collector/mention_scan.py`'s
   `CLICKUP_TASK_URL` / `_github_url` / `clawgate_url`, never write a second resolver.
   forcing: none — likely already delivered; verify rather than assume.
5. **cg#429 — clickup-mirror per-task repo override.** Repo `homelab-talos`, files
   `scripts/clickup-mirror/mirror.py`,
   `clusters/workbench/apps/clickup-mirror/config-configmap.yaml`.
   forcing: none — re-probed 2026-08-30: cg#363/364/365 still read
   `innovation-upstream/devrc`, cg#362 still unset. Real mechanism, has not fired.
6. **BLOCKED — do not start:** cg#362 and cg#363 wait on the teammate's private-repo
   migration ticket.
   forcing: none — blocked on a third party.
7. 🔴 **cg#348 — make the CI reporter distinguish capacity from a broken gate.** Repo
   `zacxdev/homelab-infra`, files `devrc-ci-pipeline.yaml` (the `clone` step and `post_leg`
   in the `report` finally task). Diagnosis and ranked fixes are in **comment 593**.
   🔴 The fix **recovers no lost check — it makes the loss legible**; the commit message must
   say so or a reviewer reads it as a reliability fix.
   forcing: gate — measured 2026-08-31, **2 of 28 terminal PR-gate runs (7.1%) lost a
   REQUIRED check to pure queueing**, and with `enforce_admins: true` those PRs are BLOCKED
   until someone pushes again. Three capacity outcomes and one real defect currently post the
   same string.
8. **Cairn integration, phase 0** — reconcile the two conflicting writers of the subsystem
   store before automatic capture multiplies the inconsistency. Plan and all 11 decisions:
   `claudedocs/plan-cairn-integration.md` (merged `093a63db`). Do not re-derive them here.
   forcing: none — nothing external is waiting.

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

- 🔴 **A WORKTREE SHARES `.git/hooks` WITH THE CLONE — which is how rank 3 closed itself.**
  `install-session-stamp.sh` writes into the **common** git dir, so the hook applies to
  every worktree of that clone, including one created after the install. The commit that
  landed this doc (`fa64c986`, made by `handoff_doc.py` from a fresh worktree) was stamped
  and parses. That is the same 123-worktree reach that made arming a blast-radius decision
  — read it both ways: it is why the feature works everywhere, and why a defect in it
  would too.
- ⚠ **`handoff_doc.py` runs git from inside Python, so no PreToolUse hook sees its commit**
  — including the never-commit-to-`main` guard. This session's doc was landed from a
  worktree on a topic branch **by hand**, because devrc forbids committing to `main` and
  the tool would have pushed to whatever branch the checkout sat on. Check
  `git branch --show-current` yourself before `--confirm --push`.

- 🔴 **A RANKED LIST GOES STALE UNDER YOU, AND THE STALENESS IS INVISIBLE.** Two entries in
  this doc were wrong within a day of being written: rank 1 was shipped, and rank 4's
  "BLOCKED on devrc#1011" became `ready_for_review` because **another session worked the
  card while this effort was in flight**. Neither is detectable by reading the doc — both
  read as current forever. `claim-work` cannot see it either: it locks an item, it does
  not notice the item finishing elsewhere. **Re-read the live card state for every ranked
  item before drawing from this queue**, not just the one you intend to take.
- 🔴 **`git branch -r --contains` IS NOT AUTHORITATIVE — it reads a LOCAL tracking ref
  that can be stale, and it said a commit was safe on a branch that no longer exists on
  the remote.** Used to check whether a diverging commit was preserved, it named
  `origin/feat/memory-detail-click`; `git ls-remote origin` returned **nothing** for that
  branch. The commit was in fact preserved — under a *differently named* branch whose PR
  had already squash-merged, which only `git ls-remote` + a **content** comparison against
  `origin/main` could establish. Ancestry says "not merged" after every squash, forever.
  **Ask the remote, then compare content.**
- 🔴 **RE-CHECK THE BRANCH AT THE MOMENT YOU ACT, NOT AT SESSION START — a shared checkout
  MOVES.** `git status -sb` at session start said `main`; hours later the base clone was
  standing on `feat/memory-detail-click`, switched by another session. A
  `git merge --ff-only origin/main` run on that assumption failed with
  `Not possible to fast-forward` and was misread as "`main` has diverged" — it had not;
  local `main` was simply behind. **The proposed remedy, `git reset --keep origin/main`,
  would have moved the FEATURE BRANCH's pointer, not `main`** — `--keep` protects
  uncommitted changes, it does not protect you from being on the wrong branch. Caught only
  because the operator asked "is that safe?".
- ⚠ **The devrc CI gate is degraded in a way that reads as a code defect**, and it will
  interfere with landing anything here. PR-leg pipeline pods **preempt** main-leg pods
  (priority 0 vs `ci-bulk` −10000, same pinned node), and single-node queueing cost **2 of
  28 PR-gate runs (7.1%) a required check** on 2026-08-31 — versus 1 lost to preemption.
  🔴 **Three capacity outcomes and one real defect all post the SAME string**
  (`COULD NOT RUN: <leg>`), which is exactly why a red check there gets debugged as a diff
  problem. A run on `main`'s own tip failed this way with **no test ever executing**.
  Diagnosis and ranked fixes are on the clawgate card filed for it; the cheapest fix makes
  the loss **legible**, it does not recover a lost check.
- **`ready_for_review` vs `complete` on a mirrored ClickUp card:** cg#365's body carries a
  prose "Closing condition", not a `## Acceptance criteria` heading, so the status gate
  says an agent may not mark it `complete` — it derives criteria it would then be grading
  itself against. Left at `ready_for_review` deliberately.

- 🔴 **`git checkout -- <path>` DESTROYED UNCOMMITTED WORK TWICE IN ONE SESSION, both times
  while restoring after a mutation test.** It reverts to the COMMITTED state, so fixes that
  were not yet committed simply vanished. The second time was caught **only** because a
  brand-new test failed *unmutated* and its message showed the OLD verdict format — without
  that test I would have committed a fix that was not there. **Commit before mutating, and
  mutate a `cp -a` copy** (`rm -f <copy>/.git` first — a worktree's `.git` is a FILE pointing
  at the real git dir). I had put this exact warning in a subagent's brief and then walked
  into it myself.
- 🔴 **A PREDICATE OPEN-CODED IN TWO LANGUAGES IS INVISIBLE TO A ONE-LANGUAGE GREP.** I
  grepped `guard_config_path`/`CONFIG_NAME` (Python), found three consumers, and briefed
  "nothing requires sharing". Two more existed in `run-tests.sh`, which builds the same path
  in **shell** as `$NOGIT_CONFIG`. The proposed fix would have broken GUARD 10 on every
  target. **Ask what OTHER language constructs this value before declaring a consumer list
  complete.**
- 🔴 **THE AUDIT LADDER FOUND DEFECTS IN MY OWN FIXES, TWICE, AND BOTH WERE THE SHAPE THE FIX
  WAS MEANT TO PREVENT.** Round 2: the `mutex=` field I added to make failures legible
  sampled ONE retry attempt while reading as the whole run. Round 3: my fix for that had a
  **surviving mutant** (`held_count += 0`) because the fixture could only ever produce `0`
  and I asserted that literal — `claude/RULES.md`'s "a fixture derived from the constant
  under test", walked into *while fixing a mutation-coverage defect*. **Budget for the ladder
  to audit the repair, not just the original.**
- 🔴 **`install()`-style helpers that set process-wide state must not be called from a test.**
  My round-2 tests called `nogit_plugin.install()` in-process; it rewrites five env vars and
  its own header says it is NEVER UNDONE, so the redirect leaked into **42 later tests**,
  which then verified containment against a redirect an unrelated test installed. The file
  already had the right pattern (`monkeypatch.setenv`). Round 4 reproduced the leak from
  scratch as a positive control before confirming the fix.
- 🔴 **I DELETED ANOTHER SESSION'S AUDIT REFS.** Sweeping `refs/audit/*` to clean up my own
  two, I removed **eight** belonging to a live PR #1119 ladder. Recoverable (that branch was
  intact on origin) but not mine to touch. **Filter to your own refs by name.**
- 🔴 **`git branch -r --contains` IS NOT AUTHORITATIVE** — it reads a local tracking ref and
  named a branch `git ls-remote` says does not exist. Ask the remote, then compare CONTENT.
- 🔴 **RE-CHECK THE BRANCH AT THE MOMENT YOU ACT.** `git status -sb` said `main` at session
  start; hours later the shared base clone stood on a feature branch. A `merge --ff-only` on
  that assumption was misread as "main has diverged" — it had not. The remedy that reading
  suggested (`reset --keep origin/main`) would have moved the FEATURE BRANCH's pointer.
  Caught only because the operator asked "is that safe?".
- ⚠ **Backticks in a `git commit -m` body are COMMAND SUBSTITUTION** — a bullet silently lost
  its content. Use `-F <file>`.
- ⚠ **zsh does not word-split unquoted parameters** — `set -- $r` in a loop read the whole
  string as one field and printed four zeros. Use explicit values or a real array.
- **Filing on an already-covered card beats minting a new one.** The CI diagnosis went to
  cg#348 as a comment because the duplicate sweep found cg#348/#337/#303 already open on it;
  a fourth card would have been the exact collision class this whole effort investigated.

## How to verify
```bash
# rank 2 landed, BY CONTENT (squash — never ancestry)
git -C ~/workspace/devrc show origin/main:scripts/testlib/nogit_plugin.py | grep -c held_count

# 🔴 GUARD 10 is actually measuring — the dev-host tier ONLY; the nix sandbox is NOT this guard
nix develop ~/workspace/devrc -c bash ~/workspace/devrc/scripts/gate.sh --tier pytest > /tmp/g.log 2>&1
grep -cE "unmeasured|lock-contended" /tmp/g.log        # MUST be 0 — this is the failure mode
grep -E "config-control=" /tmp/g.log | awk '{c="";p="";for(i=1;i<=NF;i++){if($i~/^config-control=/){split($i,a,"=");c=a[2]}if($i~/^plugin=/){split($i,b,"=");p=b[2]}}; print $1, (c==p?"OK":"*** MISMATCH ***")}'

# the trailer works through the DEPLOYED hook — reproduce the original symptom
printf 'fix: one line only\n' > /tmp/t1 && ~/workspace/devrc/.git/hooks/prepare-commit-msg /tmp/t1 message
git interpret-trailers --parse < /tmp/t1     # must print Claude-Session-Id; EMPTY was the bug

# 🔴 every ranked item's LIVE state — this doc goes stale under you
clawgatectl task ls --summary | jq -r '.[] | select([348,362,363,364,365,428,429,439,445]|index(.id)) | "cg#\(.id) \(.status)"'
```
