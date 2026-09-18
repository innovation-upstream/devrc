# Handoff: stop-hooks-impact — 2026-09-18

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
Grade the impact of Claude Code's seven Stop hooks against real usage, and act on the
grades. The grading itself is DONE; two changes shipped from it.
- **closing-condition:** `judgement` — Zach reads a per-hook `fired / total` table
  computed from `activity.events` `source='hook'` over ≥14 days of accumulation, and
  rules on the three untouched recommendations below (wire the clawgate guard into
  the telemetry, confirm `handoff-write-guard` on the laptop, retire `task-hook.sh`).
  That table is the grading question answered from a query instead of a 7.5 GB scan.

## State now
- Branch / PR: `main`, clean tree. `origin/main` = `338ac869` (#1764 landed from another
  session after this work; no overlap — verified, and `MAX_BLOCKS = 1` survives it).
- **No `clawgate-task:` field**: `clawgate_handoff.sh resolve` exited **5, NOTHING
  RESOLVED**. An unknown session id answers 200 with an empty array, so that zero cannot
  distinguish "touched no task" from "wrong id". It is not a clean bill of health.

**DONE and SHIPPED this session** (both merged, both converged to BOTH hosts, both
verified on the *deployed* artifacts rather than the repo):
- **#1755 → `cdc4b641`** — `clawgate-writeback-guard.py` `MAX_BLOCKS = 2 → 1`. Rung 2
  now emits an operator-visible `systemMessage` instead of blocking. Live copy on both
  hosts resolves to one store path carrying `MAX_BLOCKS = 1`.
- **#1749 → `5c561270`** — new `scripts/claude-hooks/hook_telemetry.py`, emitting one
  `source='hook' kind='stop-decision'` row per Stop, wired into `next-step-nudge.py` and
  `handoff-write-guard.py`. Live copy carries the `if not rows:` hot-path fix.
- **`claudedocs/clawgate-writeback-rung2-corpus-scan.py`** — the corpus scanner behind
  the rung-2 decision, committed so the number is re-runnable rather than a claim in a
  docstring. Uses `transcript_search.iter_transcripts` (the shared enumerator).

**Deploy status: deployed AND observed in production.** `scripts/ship.sh` converged both
hosts to `5c561270` (cross-host agreement confirmed, not two independent greens). First
real rows, 2026-09-18 05:10–05:20Z, both hosts emitting:

```
handoff-write-guard    fired 0 / 22 stops   0.00%
next-step-nudge        fired 1 / 20 stops   5.00%
```
Negative control: 482 rows in the same window, 42 of them `source='hook'` — the filter is
not inert. Entities in production are real handoff slugs and session UUIDs only.

## Next steps (ranked)
1. **Let the telemetry accumulate, then compute the real grades.** After ≥14 days,
   `fired / total` per hook from `source='hook'` answers the question this whole effort
   was opened with, as one query. Today's 42-row sample is far too small to read.
   forcing: none
2. **Wire `clawgate-writeback-guard.py` into `hook_telemetry`.** Deliberately deferred so
   #1749 and #1755 stayed disjoint; it is the one guard with no decision rows. Repo
   `devrc`, files `scripts/claude-hooks/clawgate-writeback-guard.py` + its test.
   forcing: none
3. **Confirm `handoff-write-guard` is registered on the LAPTOP.** It is the best-performing
   hook (+72pp lift) and covered only 487 of 842 sessions because it registered 2026-08-30.
   Read the laptop's `~/.claude/settings.json` `Stop` array. forcing: none
4. **Retire `scripts/tmux-task-hook.sh` / `~/.config/tmux/task-hook.sh`.** 20,708 fires,
   zero output, superseded by `agent-ledger-hook.py` per that hook's own docstring. Read
   the `drift-check.sh` rc-16 phase-2 gate FIRST — it is the authority on when the
   fuzzyclaw readers may go, not this doc. forcing: none

## Defects (batched)
- Round 4 audits on #1749 and #1755 were **deliberately not run** (operator call,
  2026-09-18): rounds 1–3 findings were prose-quality rather than correctness, and the
  deterministic remedy is in — every site now names its constant instead of paraphrasing
  it. Both PRs then passed the full `nix build` sandbox tier, which is the check local
  runs structurally cannot give.
- Telemetry `entity` residual accepted as-is (operator call): a snake/kebab-case basename
  passes `_sanitize` unchanged and ships whole. Bounded — own authed ClickHouse, ≤120
  chars, a name someone chose. No cap, no hash; the false "shapes prose cannot take"
  claim was removed instead.

## Gotchas / decisions / dead-ends
- 🔴 **The exit-127 hook failures are PRE-FIX HISTORY, not an open bug — do not re-probe.**
  All four Stop-hook 127s are at `2026-08-20T17:49:20Z` and every one registered with a
  BARE `python3`. `claudedocs/handoff-hook-interpreter-pinning.md` already diagnosed this
  (a `home-manager switch` writes TWO profile generations; the intermediate one drops every
  `home.packages` binary for ~1s) and fixed it by pinning the absolute store path — which is
  what `~/.claude/settings.json` carries today. Its proof timestamp, `2026-08-20T17:52:00.994Z`
  on `bash-guard.py`, is in the same corpus. ⚠ RESIDUAL, different family: five SessionStart
  127s on 2026-09-11 for `fuzzyclaw hook session-start` and `base-clone-staleness.sh` — bare
  command names, still unpinned. fuzzyclaw is being retired (next step 4), so this may vanish
  on its own.
- 🔴 **`activity.events` does NOT record hook invocations** — the `claude` source tails the
  same transcripts but keeps only `prompt`/`command`/`session-summary`, dropping hook
  attachments. Before #1749 the only surface was `stop_hook_summary` records in
  `~/.claude/projects/**/*.jsonl`. Grepping `activity.events` for a hook name returns
  sessions *talking about* the hook.
- 🔴 **The 100%-compliance trap.** A regex over the transcript tail to test "did the session
  comply" is ALWAYS TRUE: each guard's own re-fired message contains the very strings that
  define compliance (`clawgatectl task comment`, `handoff_doc.py`). Measured 170/170 =
  100.0% with the naive scan vs 92/170 structurally. **Count only assistant-issued
  `tool_use` blocks.**
- 🔴 **A clawgate blocking message can name SEVERAL task ids** — 13 of 170 (7.6%), up to
  four. `re.search` captures only the first, and a sibling task's write-back then
  misclassifies the dropped task as a re-arm. This produced a wrong "31 wrong for 1 right"
  that stood for three rounds. Correct split: **37 ladders, 35 re-arm, 2 legitimate, both
  of which produced a write-back.**
- 🔴 **Any new `*.jsonl` walk fails `test_the_jsonl_glob_site_ledger_is_pinned_two_way`.**
  `scripts/lib/transcript_search.py` is the one enumerator; a hand-rolled `os.walk` is
  refused by CI. `iter_transcripts` yields SESSION transcripts only — measured, all 170
  clawgate blocking errors and all 37 fire-2 ladders sit in session transcripts, zero in
  subagent ones, so using it changes nothing here.
- 🔴 **Re-anchoring the writeback guard on the status flip is BACKWARDS** — investigated and
  rejected. `writeback_state()` short-circuits on `CLOSED_STATUSES` *before* the comment
  scan, so the flip already satisfies it; anchoring on it would DELETE the comment arm and
  fire ~60× more. Do not re-derive this.
- **`preventedContinuation` is `false` on all 20,708 `stop_hook_summary` records** even
  though exit-2 blocks demonstrably work (98.8% / 94.9% of blocking errors are followed by
  an assistant turn in the same turn). The field is not wired to exit-2; don't read it as
  "the guard didn't block".
- **CI reds here were mostly NOT the PR.** `main` moved six times during this session
  (`e70f7c26 → … → 338ac869`); two of three red pytests legs were inherited failures
  already fixed upstream, and one (`NO CAPACITY`) was the gate never starting. Read the
  failing test name and run it against a clean `origin/main` checkout before debugging.
- **My own measurements needed three corrections, all flattering the recommendation**
  (1-in-32 → 2-in-37; "once per six weeks" → 2/month, wrong window; a scanner docstring
  claiming "both hosts" when it walks only the local corpus). The agents' discipline was
  better than mine. Prefer the committed scanner to any number quoted in prose.

## How to verify
```bash
# the deployed artifact is the new one (readlink is the arbiter, never a diff)
readlink -f ~/.claude/hooks/hook_telemetry.py
grep -n '^MAX_BLOCKS' "$(readlink -f ~/.claude/hooks/clawgate-writeback-guard.py)"   # => 1

# the emitter works, driven the way Claude Code drives it (VIA THE SYMLINK — resolving it
# to a per-file store path breaks the sibling import and silently yields 0 rows)
D=$(mktemp -d); printf '{"hook_event_name":"Stop","session_id":"verify","transcript_path":"/nonexistent.jsonl","cwd":"/tmp"}' \
  | ACTIVITY_SPOOL_DIR="$D" python3 ~/.claude/hooks/handoff-write-guard.py >/dev/null; wc -l < "$D"/current.log   # => 1

# production: fired WITH a denominator (creds per the `activity` skill)
# SELECT text AS hook, countIf(JSONExtractString(toString(payload),'decision')='fired') AS fired,
#        count() AS stops FROM activity.events WHERE source='hook' GROUP BY hook
```
