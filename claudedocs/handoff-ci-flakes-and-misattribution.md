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
- Branch `main`, both hosts converged. **Nothing in flight.**
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
- **#778 CLOSED unmerged** — see the first open investigation; this is the risky one.

## Open investigations — live diagnosis state

### 🔴 The rescued `initiative-scan.py` WIP now has exactly ONE copy, and its PR was closed
- **Symptom + exact repro:** `origin/rescue/initiative-scan-resolved-filter` @ `1327372d` is
  the only surviving copy of a `--exclude-slugs` / resolved-filter feature. Deleting that
  branch destroys the work.
- **Observed (with values):**
  - working tree: `grep -c "exclude-slugs\|def parse_resolved" scripts/session-analysis/initiative-scan.py` → **0** (the WIP was discarded)
  - on `origin/main`: same grep → **0**
  - `git branch --contains 1327372d` excluding the rescue branch → **0**
  - `git merge-base --is-ancestor 1327372d origin/main` → **no**
  - `git ls-remote --heads origin rescue/initiative-scan-resolved-filter` → **1** (present)
  - PR #778 `closedAt=2026-08-25T00:50:23Z`, **comments=0**
- **Ruled out:** it did not land on `main` under another name (grep above); it is not on any
  other local branch.
- **Leading hypothesis:** whoever closed #778 did not know the branch had become the sole
  copy — it was a draft, and the working-tree original was cleaned independently.
- **Next probe:** ask the operator whether the feature is wanted. If yes, reopen/rebase; if
  no, say so explicitly so the branch can be deleted deliberately rather than tidied away.
  🔴 Do NOT delete that branch as cleanup.

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
1. **Decide the fate of `rescue/initiative-scan-resolved-filter`** (devrc, branch only — no
   files in `main`). Sole copy; PR #778 closed with no comment. Cheapest item, highest
   irreversibility.
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
*better* ranked list produces *more* duplicate work. Nothing above is in flight as of this
writing; if you start one, mark it `IN FLIGHT: <repo>#<pr>` here.

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

# the rescued WIP still exists — 🔴 if this prints nothing, the work is GONE
git -C ~/workspace/devrc ls-remote --heads origin rescue/initiative-scan-resolved-filter

# both hosts carry it
bash ~/workspace/devrc/scripts/drift-check.sh    # read-only; rc 0 = converged
```
