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
- **Branch / PR:** nothing in flight from this session. No code was written; the work
  products are clawgate cards and comments.
- 🔴 **The session STARTED on `feat/flake-lock-and-discord-ext` and ENDED on `main`** —
  the branch changed underneath it, unobserved, in a shared checkout. Nothing was
  committed while it was wrong. This is the `claude/RULES.md` "re-check WHICH branch
  before ANY write" class, and it fired for real here.
- **Created (clawgate):**
  - **cg#428** — `cairn: subsystem entries carry no task ref — add a lossless
    multi-system tasks: key (clickup, github, clawgate first-class)`. repo
    `innovation-upstream/devrc`, dir `scripts/lib`, tags `project:cairn devrc tooling`.
  - **cg#429** — `clickup-mirror: per-task repo override`. repo `homelab-talos`, dir
    `scripts/clickup-mirror`, tags `tooling infra tech-debt`.
- **Mutated (clawgate), all verified by re-read:** cg#362–365 tagged `project:cairn`;
  `repo` corrected (363/364/365 → `innovation-upstream/devrc`, 362 → unset);
  one comment each; cg#364 cross-linked to cg#428; cg#383 given fresh measurement.
- **cg#363's comment was REPLACED, not appended to** — the original asserted a defect
  that the operator's scope answer disproved. Comment 457 deleted, 467 posted carrying
  an explicit retraction.
- **Nothing was written to ClickUp.** Verified after the fact: `No comments on this
  task.` on the tickets sampled. Writeback is live but its scope is ritual
  pickup/completion comments, status transitions and PR links — not arbitrary comments.
- **Deploy/verify:** nothing deployed. No code changed in any repo.

## The cluster — ClickUp ↔ clawgate ↔ Cairn
All six ClickUp tickets came from the **2026-08-26 harness / knowledge-sharing meeting**
(Discord voice, ~122 min; Zach + Justin + Koen) — the same date `handoff-cairn.md`
records as when the system was presented. They are the team-facing half of Cairn.
**None of them contains the word "cairn"**, which is why they are invisible to a
name search; the `project:cairn` tag now fixes that.

| ClickUp | clawgate | what it maps to in Cairn |
|---|---|---|
| `868kx9eut` | **cg#364** | the subsystem store + `cairn recall` |
| `868kx9ety` | **cg#363** | `/handoff` |
| `868kx9et9` | **cg#362** | `cairn who` / transcripts |
| `868kx9ev6` | **cg#365** | the `Claude-Session:` commit trailer |
| `868kx9evj` | — *(not mirrored)* | Justin's private-repo migration — the blocker |
| `868kp7fe6` | cg#256 | complete; the precedent |

## Open investigations — live diagnosis state

### Have the hand-corrected `repo` values on cg#362–365 already reverted?
- **Symptom + exact repro:** the mirror re-patches `repo` from config on every UPDATE,
  so a hand correction survives only until the ClickUp ticket's content hash moves.
- **Observed (with values):** `mirror.py:1025-1026` — `if payload.get("repo"):
  full["repo"] = payload["repo"]`, unconditional once an UPDATE is chosen.
  `mirror.py:641-644` — `plan()` returns UNCHANGED only while
  `row["content_hash"] == content_hash(payload)`, compared against the **ledger**, not
  against clawgate's live state. `repo_for()` (`:351-354`) keys on `list_id` only, and
  `by_list_id` maps `901111220963` → `civitai/civitai`.
- **Ruled out:** "it reverts on the next run" — that was my first reading and it is
  wrong; an unchanged ticket is never patched at all.
- **Leading hypothesis:** still correct as of session end (verified by re-read), and it
  will revert silently the first time any of those four ClickUp tickets is edited.
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

## Next steps (ranked)
1. **Read PR #1011 FIRST, then build cg#428 on its grammar.** repo `devrc`, files
   `scripts/lib/subsystem_recall.py`, `subsystem_touch.py`, `scripts/cairn`,
   `scripts/tests/test_subsystem_*`. See the 🔴 collision under Gotchas — this is the
   single highest-value item and its first task is *not* writing a parser.
   ⚠ **IN FLIGHT: innovation-upstream/devrc#1011**, and #1033/#998 touch the same files.
2. **cg#429 — clickup-mirror per-task repo override.** repo `homelab-talos`, files
   `scripts/clickup-mirror/mirror.py`, `clusters/workbench/apps/clickup-mirror/config-configmap.yaml`.
   Run the first open-investigation probe before starting; a revert is its regression case.
3. **Decide cg#365's canonical session id.** A decision, not a build. Gates the git-hook
   card. The operator's "hard cluster dependency is acceptable" answer widens the options.
4. **Put `cairn` on PATH.** `scripts/cairn` is tracked and executable with tests, and
   `grep`ing all of `nix/` for "cairn" returns **nothing** — no deploy wiring exists.
   Two lines in `nix/home.nix` mirroring `claim-work` (`:1238`, an
   `mkOutOfStoreSymlink`) plus a switch. Small, unblocked, and it is why the tool is
   invisible today.
5. **BLOCKED — do not start:** cg#362 and cg#363 both wait on ClickUp `868kx9evj`
   (Justin's private-repo migration). Not on the clawgate board, correctly (see below).

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
  me."* Consequences: (a) the clickup-mirror's `scope.assignee_id: 81593871` is
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

## How to verify
```bash
# the two cards exist, with intact bodies and a criteria heading
for id in 428 429; do clawgatectl task get $id | jq -r '"cg#\(.id) \(.status) repo=\(.repo) criteria=\(if (.body|test("(?m)^## Acceptance criteria")) then "YES" else "NO" end)"'; done

# the four cluster cards carry the tag, and their repo has not reverted
clawgatectl task ls --summary 2>/dev/null | jq -r '.[] | select(.id>=362 and .id<=365) | "cg#\(.id) repo=\(.repo) tags=\((.tags//[])|join(","))"'

# ClickUp was not written to
node ~/.claude/skills/clickup/query.mjs comments 868kx9eut   # expect: No comments on this task.

# the collision this doc is most concerned with
gh pr view 1011 --repo innovation-upstream/devrc --json files --jq '.files[].path'
```
