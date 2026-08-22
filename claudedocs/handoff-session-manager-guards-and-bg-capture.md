# Handoff: session-manager-guards-and-bg-capture — 2026-08-22

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
Evaluate the `session-manager` skill, fix what the evaluation found, and land the
already-open background-command-capture instrumentation. All three closed this session;
what remains is one **unfixed defect found in a different skill** (below) and one new
ClickUp ticket nobody has started.

## State now
- **Branch / PR:** nothing in flight. `devrc` base clone is on a **detached HEAD** with
  `CLAUDE.md` staged-modified (another session's work — not mine, left alone).
- **DONE — devrc #693, merged `38c3e030`, shipped, verified at the consumer.**
  `session-manager`'s NEVER-PASTE rule covered `unsent_prompt` but not
  `clickhouse.rows[].first_msg` — the opening prompt of every recent session, ~17 KB of
  operator-typed text in a *default* scan, in a PUBLIC repo. Widened in `SKILL.md`,
  `reference/waiting-signal.md` and `reference/clickhouse-queries.md`, and the width is now
  **checked**: `_OPERATOR_TEXT_FIELDS` in `scripts/tests/test_session_manager.py` is an
  asserted ledger, every member must be named in the core's rule, each bound to the code
  that produces it (`sm.SQL_RECENT_SESSIONS`, `sm.LEAN_ROW_FIELDS`).
  Same PR: the cost table was stale >2×. Re-measured on a 77-row two-host scan — table
  24,658 B, `--json --lean` **79,008 B (~20k tok, not the quoted ~9.6k)**, `--json`
  103,801 B. Cause is structural: `--lean` trims ROW fields only, and 39 KB of the lean
  payload is blocks it never touches (`clickhouse` 17.4 KB, `clawgate_queue` 12.7 KB
  uncapped, `caveats` 4.3 KB, `ledger` 2.8 KB, `not_measured` 1.8 KB). `--no-ch` promoted
  into the core flag table; recommendation is now `--json --lean --no-ch`.
  SKILL.md 16,200 → 16,191 B — under its ceiling **without raising `MAX_BYTES`**.
- **DONE — devrc #665, merged `d8afe31f`, shipped, verified on live traffic.**
  Backgrounded-command capture (ClickUp `868ktvqf9`). Gated on the **merged** tree, not the
  PR branch: pytest `14576 collected / 14574 passed / 0 failed` (floor 13400), node
  `1119 pass` (floor 1098), `GATE: RESULT=PASS`. Deployed and registered on `PreToolUse` +
  `PostToolUse` on **both** hosts. Live log
  `~/.local/state/claude-bg-command-capture/commands.jsonl` — **2,323 rows, 74 backgrounded**
  as of writing. 🔴 This does **not** close `868ktvqf9`: it is instrumentation so the next
  hit carries evidence, and the standing instruction is no fix attempt until then.
- **DONE — ClickUp `868kv67jf` created** (image-width ladder consolidation), split out of
  `868kuam02` and cross-linked from it.
- **Deploy state at time of writing (drifted since my ship — other sessions' merges, not a
  regression):** `origin/main` `2d1ba0d7` · workbench `d8afe31f` · laptop `3d84cd7c`.
  Both hosts carry #693 and #665. A plain `scripts/ship.sh` re-converges.

## Open investigations — live diagnosis state

### `check-clickup-addressed`'s "nobody is on it and someone is waiting" flag fires over comments you already answered
- **Symptom + exact repro:**
  `python3 $DATAPACKET/.claude/skills/check-clickup-addressed/scripts/check-addressed.py --limit 5`
  emitted, in **Needs a decision**:
  `868kuam02: @Ellie King is WAITING — the ticket is to do, and the task ID appears in NO
  transcript, so no work exists anywhere. Commented 2d ago; nobody has answered. Read it.`
- **Observed (with values):** `node ~/.claude/skills/clickup/query.mjs comments 868kuam02 --threads`
  shows three comments — Ellie King **Aug 19 09:10 PM** and **Aug 19 09:18 PM**, then
  **Zachary Lowden Aug 21 01:52 PM**, a long substantive reply answering her decision
  question. The newest comment on the ticket is the operator's own, ~2 days *after* Ellie.
  The checker's own report line reads
  `newest comment [2026-08-20 02:18] @Ellie King` — i.e. it named Ellie's as newest.
- **Ruled out:** *not* a timezone artifact — Aug 19 21:18 local == Aug 20 02:18 UTC, which is
  the timestamp the tool printed, so the two agree on Ellie's comment and simply never
  consider the later one. *Not* the prior-run exclusion (that drops transcripts, not
  comments). *Not* a stale cache — reproduced against a live API read minutes later.
- **Leading hypothesis:** `recent-comments.py` filters out the operator's own comments by
  design (correct for "what has someone said to *me*"), and the waiting flag then consumes
  that filtered list as "newest comment" while looking **only at transcripts** for evidence
  of an answer. A reply posted *in the ticket* is therefore invisible to it. The flag's own
  inputs contain the disproof and it never reads them. Same shape as the `session-manager`
  defect fixed in #693: a guard narrower than the question it appears to answer.
- **Next probe:** run verbatim —
  `grep -n "UNANSWERED_COMMENT_DAYS" -A 30 $DATAPACKET/.claude/skills/check-clickup-addressed/scripts/check-addressed.py`
  — and confirm whether the predicate consults the unfiltered comment list at all. If it does
  not, the fix is to compare against the ticket's newest comment **including** the operator's,
  and add the case to `test/test_corpus.py` **before** changing code (that skill's standing
  rule: score it, don't argue from the example).

## Next steps (ranked)
1. **Fix the waiting-flag false positive above** in `check-clickup-addressed` — it is the
   single flag most likely to send someone at a ticket that is already handled, and it is
   ~one predicate. Add the labelled case to `test/test_corpus.py` first.
2. **`868kv67jf` — image-width ladder.** Start with scope item 3: the two app copies
   (`apps/creator-studio/…`, `apps/moderator/…`) contain **neither** `OPTIMIZED_WIDTH_THRESHOLD`
   nor `shouldForceOptimized`, which `src/client-utils/edge-url.ts` documents as "load-bearing
   and deliberately shared". Decide whether that is a bug in those apps or an overclaiming
   comment — it decides whether the ticket is cleanup or a bug fix.
3. **`868ktvqf9`** — nothing to do but wait for the next hit; the capture is now armed and
   proven on live traffic. When it fires, read `commands.jsonl` for the verbatim command.
4. **Re-run `scripts/ship.sh`** if you want both hosts back at `origin/main`; they are two
   and three commits behind respectively from unrelated merges.

## Gotchas / decisions / dead-ends
- 🔴 **`ship.sh` prints `rc=7` in its output while the shell exit code can be `0`** — a
  trailing `echo` in a compound command swallows it. Read every per-host line, never the
  final verdict alone. It skipped the workbench for hours this session.
- 🔴 **A devrc worktree holding the `main` branch blocks `ship.sh` on the workbench**
  (`fatal: 'main' is already used by worktree at …`). `/home/zach/workspace/devrc-r3-pushctl`
  was pinning `main` at `9667fb8b` with an **emptied index** (270 bytes ⇒ 953 phantom staged
  deletions of files that exist on disk and on `origin/main`) and a reflog of throwaway
  commits named `seed` and `c`. Fixed with `git -C <wt> checkout --detach`, which frees the
  branch name and preserves HEAD sha and index. **That worktree still exists, detached.**
- 🔴 **The base clone was `core.bare=true` for part of this session** — every work-tree git
  operation returned `fatal: this operation must be run in a work tree` while the files were
  intact. It was repaired by something else at 19:10 the same day. Do not assume; if git
  refuses, check `git config --local --get core.bare` before concluding anything.
- **Decision: reproduce the positive control instead of `/audit-pr` on #665.** A green suite
  says nothing about whether the hook captures. Three controls were run, each moving the
  number: positive (backgrounded → 1 row, command byte-identical), negative (plain foreground
  → **0** rows), discriminator (foreground + masking marker → 1 row). The PR's own
  `--selftest` was run too but not trusted alone.
- **The hook takes effect in ALREADY-RUNNING sessions** — no restart. Measured: it captured a
  command issued 40 s after the switch, from a session started hours earlier.
- **Dead end:** grepping for duplicate `TARGET_FLOORS` entries in `run-tests.sh` reports false
  positives — each value appears once in a comment explaining it and once in the array.
- **`clawgate_handoff.sh resolve` returned exit 5** (nothing resolved) for this session, so
  **no `clawgate-task:` field is recorded**. Per the tool's own contract that is *not* a clean
  bill of health: an unknown session id answers `200` with an empty array, so this cannot
  distinguish "touched no task" from "wrong id".

## How to verify
```bash
# #693 — the widened rule is live on BOTH hosts, and the old narrow wording is gone
grep -c 'clickhouse.rows\[\].first_msg' ~/.claude/skills/session-manager/SKILL.md   # 1
grep -c 'NEVER PASTE A CAPTURED DRAFT'  ~/.claude/skills/session-manager/SKILL.md   # 0
ssh zach@192.168.50.155 'grep -c "clickhouse.rows\[\].first_msg" ~/.claude/skills/session-manager/SKILL.md'  # 1

# the guard actually fails when the rule narrows (mutation-tested, 4 mutants)
nix-shell -p 'python3.withPackages(ps: [ps.pytest])' --run \
  'cd ~/workspace/devrc && PYTHONDONTWRITEBYTECODE=1 python -m pytest \
   scripts/tests/test_session_manager.py scripts/tests/test_session_manager_skill_size.py -q'

# #665 — capture is armed and firing on real traffic
python3 -c "import json,os;f=os.path.expanduser('~/.local/state/claude-bg-command-capture/commands.jsonl');\
r=[json.loads(l) for l in open(f) if l.strip()];print('rows',len(r),'bg',sum(1 for x in r if x.get('background')))"

# the full gate (authoritative; run it in the repo's own dev shell)
nix develop ~/workspace/devrc --command bash ~/workspace/devrc/scripts/gate.sh --tier both --set all
```
