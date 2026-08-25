# Handoff: ci-flakes-and-misattribution — 2026-08-25

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **No `clawgate-task:` field on purpose.** `clawgate_handoff.sh resolve` returned **exit 5,
`NOTHING RESOLVED — 0 tasks for this session`**. Per the tool's own contract that is not a
clean bill of health: an unknown session id answers `200` with an empty array, so the result
cannot distinguish "touched no task" from "wrong id". No field written, none invented.

## Goal
Started as one line of the ccua handoff — *"fix GUARD 10's attribution message"*. It became a
thread about **guards that blame the wrong thing**: GUARD 10 blaming a test for another
session's git write, and browser-bridge tests blaming their own subject for a neighbour's
spool row. Both are now fixed; the flake family underneath is only half closed.

## State now
- Branch `main`, both hosts converged. **IN FLIGHT: devrc#824** (the `--exclude-slugs`
  salvage — next-step 1). Nothing else.
  ⚠ Hosts were converged as of the deploy note below; that was measured on 2026-08-25 and
  `main` has moved since (#806, #812, #817, #818, …). Re-run `drift-check.sh` rather than
  reading the line below as current.
- **Five PRs merged:**

| PR | squash | what |
|---|---|---|
| devrc #773 | `8ecde026` | GUARD 10 prints the KEY NAMES that moved + ranks the writer. Detection byte-for-byte unchanged. |
| devrc #779 | `188da3c3` | rescued `handoff-espanso-audit-gate.md`, untracked for two days |
| devrc #788 | `f06ef868` | CLAUDE.md said `nix build .#checks` **or** `gate.sh` — they are two TIERS |
| devrc #807 | `3e207432` | browser-bridge spool rows selected by `op`, not position |
| devrc #770 | `ebd30a62` | the ccua handoff, next-step 1 recorded DONE |

- **Deploy VERIFIED on both hosts.** `ship.sh` rc=0, both at `324693fd`, cross-host agreement
  actually COMPARED (not the `NOT COMPARED` one-host case): workbench 527 managed artifacts
  resolve / 0 dangling / 0 stale, laptop 488 / 0 / 0.
  ⚠ Workbench printed `NOTE: tree is DIRTY` — what was built there is `origin/main` **plus**
  another session's uncommitted `scripts/run-node-tests.sh` line. Deployed artifact ≠ commit
  on that host.
- **#783 open by design** — #807 fixed the assertions, NOT the root cause.
- **#778 CLOSED unmerged** — **decided 2026-08-25**, see the first entry below. No longer the
  risky one; the risk moved to *not deleting the rescue branch before #824 lands*.

## Investigations — live diagnosis state (first entry is DECIDED, the rest are open)

### ✅ DECIDED 2026-08-25 — the rescued `initiative-scan.py` WIP: one half salvaged, one half rejected on measurement
**Operator decision: salvage `--exclude-slugs` only; drop `parse_resolved`. `IN FLIGHT: devrc#824`.**
🔴 **`rescue/initiative-scan-resolved-filter` still must NOT be deleted as cleanup** — it stays
until #824 merges, and then goes deliberately, verified BY CONTENT (a squash merge is never an
ancestor), not by ancestry.

- **What made it urgent (re-verified live, not carried forward):** branch present on origin;
  `merge-base --is-ancestor 1327372d origin/main` → **no**; `git branch -a --contains` → only
  the rescue branch itself; grep on `origin/main` → **0**. And `initiative-scan.py` had taken
  **zero commits** since the rescue's parent, so the WIP still applied cleanly.
- **The two halves are independent**, which is what made a split decision possible at all:
  `--exclude-slugs` (+12 lines, explicit operator list) vs `parse_resolved` (+55 lines,
  inferred verdict, filter **on by default**).
- 🔴 **#778's own diagnosis was wrong in three ways** — found by re-measuring, not by reading
  it. Corrected on the PR: <https://github.com/innovation-upstream/devrc/pull/778#issuecomment-5413412588>
  - Method: loaded `1327372d`'s module directly, ran `parse_resolved` over handoffs
    materialized from git, attributed every hit to the arm that fired. Positive control
    (`## Status: RESOLVED`) → `True`; negative control (plain prose) → `False`.

    | corpus | scanned | flagged | `heading-marker` | `inline-status` | `PROSE-SUMMARY` |
    |---|---|---|---|---|---|
    | `199774f8` (main at #824's base) | 62 | 11 (18%) | **7** | 1 | 3 |
    | `982778ee` (the sha it cites) | 53 | 10 (19%) | **7** | 1 | 2 |

    ⚠ Both corpora are PINNED on purpose. An earlier draft of this table wrote the first row
    as "`origin/main` today" and as **8 / 1 / 2** — wrong on both counts, caught by the #826
    audit. `main` moves (it is already past `199774f8`), so an unpinned row cannot be told
    apart from doc rot; and the arm split was mis-transcribed by eyeballing a listing instead
    of counting it. `8 / 1 / 2` is not merely wrong, it is UNREACHABLE: the extra doc at
    `199774f8` is `handoff-ccua-…`, which has no marker-initial heading, so it can only be a
    prose hit — and heading could only reach 8 by counting `handoff-prompt-optimization-…`,
    which is itself the sole `inline-status` hit, forcing that column to 0. The row summed to
    11 either way, which is exactly why the arithmetic self-check did not catch it.
    🔴 The irony is the lesson: this section exists to correct #778 for publishing numbers it
    had not re-derived, and its own first draft did the same thing.

  - (a) It blames the prose scan and states *"the heading and inline `Status:` arms did **not**
    fire"*. The heading arm is the DOMINANT one — `### DONE this session`,
    `### RESOLVED: the clawgate stuck detector…`, `## CLOSED: the commit-to-main guard fail-open`.
  - (b) Its counts came from the **working tree** while citing `982778ee`: its headline example
    `handoff-ccua-waiting-flag-and-fork-close.md` does **not exist** at that sha
    (`git cat-file -e` → absent). That is the 11/55-vs-10/53 gap.
  - (c) Its suggested fix — *"a heading that **starts** with the marker"* — **preserves all 7**:
    that arm's regex already requires the marker to start the heading, so the fix is a no-op
    against every one of them.
- **Root cause (the reason the half was rejected, not just deferred):** a handoff is a
  MULTI-SECTION document. Those headings describe one investigation *inside* a doc that still
  carries live next-steps. The predicate conflates "this document mentions something finished"
  with "this initiative is finished", and that signal **does not exist in the corpus at the
  granularity it needs** — so no tightening of the marker regex reaches it. A report whose
  entire job is answering *"what am I working on"* must not hide a row on a guess.
- **Also fixed in the salvage:** the WIP's `set(raw.split(","))` never stripped, so `"a, b"`
  yielded `" b"` — it suppressed one of two while reading as though it had done both.
- **Not a dead end, recorded so nobody re-derives it:** if an inferred filter is ever wanted,
  it needs an explicit top-level status FIELD as a handoff convention. That convention does
  not exist today, and inventing it is a docs-format change, not a parser change.

### #783 — the spool defect's ROOT CAUSE is untouched
- **Symptom + exact repro:** a test reads `_wait_events(spool_dir, 1)[0]` and gets a
  neighbour's row. Seen in CI as `assert 'getHtml' == 'frames'` (#773, a change to
  `scripts/run-tests.sh`) and `assert 'getHtml' == 'type'` (#770, a change to one `.md`).
- **Observed (with values):** `conftest._isolate_activity_spool` does
  `monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path / "activity-spool"))` — a
  **process-global** env var. `spool_emit.default_spool_dir()` reads it **at emit time**
  (`scripts/collector/keylog/spool_emit.py:32-38`), and `srv.daemon_threads = True`
  (`test_server.py:352`) means `server_close()` joins nothing. The #807 audit reproduced it
  deterministically with a 2-test probe: a thread sleeping 1s then calling
  `S.emit_cmd_event(op="getHtml", outcome="timeout")` wrote into the NEXT test's spool.
- **Ruled out:** `_wait_connected` polls `/instances`, not `/health`, so no in-test diag
  emit. `tmp_path` is unique per test. Each test binds its own port 0 server. The
  `cmd_timeout` lines are the server's structured **stderr log**, not spool rows — do not
  grep the spool for that string.
- **Leading hypothesis:** confirmed, not hypothesis — emitter threads outlive their test
  while the env var is re-pointed under them.
- **Next probe:** close it at the source by joining emitter threads at teardown (or scoping
  the spool dir per-server rather than per-process). `#807` only made the ASSERTIONS robust.
  **39 `_wait_events` call sites still take a count and index by position** (AST-derived, in
  `_wait_events`' docstring: `53 total = 39 n=1 + 5 until= + 9 n>=2`, + 7 op-selected).

### The SECOND flake family — `test_browser_agent.py:558`, unfiled
- **Symptom + exact repro:** full-suite runs of `scripts/browser-bridge/tests` fail 1–2 tests
  in `test_browser_agent.py`, always at the shared subprocess hang-net at `:558`, a
  **different test each run**. All pass in isolation.
- **Observed (with values):**
  - base `5bd00189`, full suite: 1 failed — `test_tab_closed_on_opencode_error` (283s)
  - head, full suite (audit's run): 1 failed — `test_a_goal_beginning_with_a_dash_is_reachable_via_the_separator` (306s)
  - head, full suite (mine): 2 failed — `test_no_shell_string_path_remains`, `test_partial_status_is_success_exit` (376s)
  - a later full dev-host gate on the merged tree: **0 failed** (15827 collected)
- **Ruled out:** not this thread's diffs — **it fails at base too**, and a deterministic
  regression fails the SAME test, not a different one each run.
- **Leading hypothesis:** load-dependent exhaustion of the rig's stall budget, not a hang.
- **Next probe:** capture a FULL failure message, not the tail. The rig itself prints one of
  two verdicts — *"so the MACHINE is not the explanation and the wrapper genuinely hung"* vs
  *"so the MACHINE is stalled — but the stall budget for this run is exhausted"*. **File the
  issue only with that line quoted**; filing it as "flaky" without it is the same
  under-diagnosis this thread spent the evening correcting.

## Next steps (ranked)
1. ~~**Decide the fate of `rescue/initiative-scan-resolved-filter`**~~ — **DONE 2026-08-25.**
   Split decision: `--exclude-slugs` salvaged as **`IN FLIGHT: devrc#824`**, `parse_resolved`
   rejected on measurement (see the decided investigation above). **Residual, blocked on #824
   merging:** delete the rescue branch deliberately, after confirming BY CONTENT that the
   salvage landed (`git show origin/main:scripts/session-analysis/initiative-scan.py |
   grep -c parse_exclude_slugs` → 1) — never by ancestry. Closing condition: that grep, plus
   `gh pr view 824 --json mergedAt,mergeCommit`. Until both, the branch stays.
2. **Close #783 at the root** (devrc: `scripts/browser-bridge/tests/conftest.py`,
   `scripts/browser-bridge/server.py`) — join emitter threads at teardown so a re-pointed
   `ACTIVITY_SPOOL_DIR` cannot be written by a dead test's thread.
3. **Capture the browser-agent verdict line and file it** (devrc:
   `scripts/browser-bridge/tests/test_browser_agent.py:558`).
4. **Migrate the 39 remaining count-based spool call sites** (devrc: `test_server.py`) — safe
   until a neighbour is late; mechanical, but intent must be read per site.
5. **Workbench dirty-tree note** — `scripts/run-node-tests.sh` carries another session's
   uncommitted `discord-embed-ext` line. Not ours to commit; resolves itself when they do.

🔴 **This list is a WORK QUEUE WITH NO LOCK** — every `/resume` session draws from it, so a
*better* ranked list produces *more* duplicate work. **In flight right now: item 1 only, as
`IN FLIGHT: devrc#824`. Items 2–5 are unclaimed.** If you start one, mark it
`IN FLIGHT: <repo>#<pr>` here — and if you find this sentence disagreeing with the items above,
believe the items, then fix this sentence.

## Gotchas / decisions / dead-ends
- 🔴 **`scripts/gate.sh` runs the DEV-HOST tier only** — `run-tests.sh` + `run-node-tests.sh`.
  It never invokes `nix build .#checks…`, which is the sandbox tier Tekton gates on and which
  builds from a store copy with **no `.git`**. Four consecutive `GATE: RESULT=PASS` runs were
  reported on #773 as covering the merge; the sandbox tier had never been run. Now corrected
  in `CLAUDE.md` (#788). **Name the tier AND the base sha in any green claim.**
- 🔴 **A parallel worker landed mid-thread with a DIFFERENT diagnosis of the same symptom.**
  #802 (`d09038d8`) found the #1 CI failure was a **dropped** row (a data race in
  `server.py:_load_spool_emit` publishing its flag before its module). #807 found a **wrong**
  row. Both real, neither a duplicate. Their AST-derived call-site census was better than my
  grep, so the conflict was resolved by taking **their** text and adding only the half they
  did not cover. Check for a parallel worker before assuming a diagnosis is yours alone.
- 🔴 **Three of the five merges fixed FALSE CLAIMS, not logic bugs**: a CLAUDE.md line saying
  two tiers were interchangeable; a conftest docstring promising isolation it did not
  provide; a helper documented "deliberately total" that raised `AttributeError` on `null`,
  `123`, `"str"`, `[1,2]`. **Every one was found by an audit, none by a green gate.**
- 🔴 **Budget several audit rounds when the artifact is PROSE.** #773's first cut passed a
  green gate AND a mutation sweep and still shipped two 🔴s; the round that fixed them
  re-instated the exact prose-contradicts-code defect the branch existed to close. Prefer
  **deleting a duplicated sentence** (read the value from one place) over correcting it.
- **CI congestion is real and it costs verdicts.** `exited with code 255` on `step-pytests` is
  the congestion signature, not a code failure. A rerun forced into a busy window turned a
  green `nodetests` into `ERROR`. Drain first: no other pipelineruns Running, 0 Pending pods,
  **memory** < 45% on `talos-xr6-r7p` (memory, not CPU — that mistake cost one run).
- **A hand-written rerun PipelineRun needs `workspaces: [source]` and the `taskRunTemplate`** —
  copy them from an existing run or it dies `InvalidWorkspaceBindings`.
- **Mutation sweeps: verify each mutant APPLIED and PARSES.** One round produced three void
  mutants (one didn't parse and killed everything for the wrong reason, one anchor never
  matched, one applied half). Driving the sweep from Python rather than shell removed the
  quoting class entirely.
- **A green-at-base test is not regression coverage.** A large-delta fixture used
  `core.hooksPath`, which `sort -k2` places AFTER 15000 filler lines, so `grep -q` never
  exited early and no SIGPIPE occurred — it passed at base. Fixed by using `alias.*`, which
  sorts first.

## How to verify
```bash
# GUARD 10's new message, live — run any gate and read its block
nix develop ~/workspace/devrc --command bash ~/workspace/devrc/scripts/gate.sh --tier both --set all
#   expect, when a concurrent session writes the shared config:
#     "keys that moved in this file (NAMES ONLY …)"  +  "+ branch.<name>.remote"
#     "→ SHAPE: ORDINARY GIT … the target above is the SECOND"

# the spool fix is on main (content, not ancestry — squash merges are never ancestors)
git -C ~/workspace/devrc show origin/main:scripts/browser-bridge/tests/test_server.py | grep -c "def _wait_ops"   # 1
# and #802's fix is still there beside it
git -C ~/workspace/devrc show origin/main:scripts/browser-bridge/server.py | grep -c "_spool_emit_lock"          # 3

# 🔴 READ THE TWO GREPS BELOW FIRST — they decide what an empty result here MEANS.
#   greps == 0  -> #824 has NOT merged; empty here means the work is GONE. Alarm.
#   greps == 1  -> #824 merged; empty here is CORRECT — the branch was deleted on purpose.
git -C ~/workspace/devrc ls-remote --heads origin rescue/initiative-scan-resolved-filter

# the salvage — BY CONTENT, since a squash merge is never an ancestor.
# Expect 0 until #824 merges, 1 after. A 0 is only news if #824 shows as merged.
git -C ~/workspace/devrc show origin/main:scripts/session-analysis/initiative-scan.py \
  | grep -c "def parse_exclude_slugs"                              # 0 pre-merge / 1 post
# and the invariant guard that stops the rejected half being re-added
git -C ~/workspace/devrc show origin/main:scripts/session-analysis/tests/test_initiative_scan.py \
  | grep -c "test_a_handoff_that_says_DONE_is_still_reported"      # 0 pre-merge / 1 post
gh pr view 824 --repo innovation-upstream/devrc --json mergedAt,mergeCommit

# both hosts carry what is ALREADY on main (not #824, until it merges)
bash ~/workspace/devrc/scripts/drift-check.sh    # read-only; rc 0 = converged
```
