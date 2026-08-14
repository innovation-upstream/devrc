# Handoff: agent-attention-tooling — 2026-08-13

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
Make *"what is being worked on, and what is waiting on me"* answerable in one call instead
of 13 pane tails, and stop turns ending without a stated next step.

## State now
- **Branch:** `main`, at `a12f101` = `origin/main`. Both hosts converged and switched
  (`ship.sh`: workbench 446 managed artifacts resolve / 0 dangling, laptop 408 / 0).
- **Working tree:** untracked only (`.envrc`, `.opencode/`, `claudedocs/proposed-rules-cut/`,
  `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`). ⚠ THIS DOC was untracked from
  before this session — stranded work; it is committed now.

**Shipped and verified live this session:**

| what | PR / sha |
|---|---|
| the agent activity ledger — closes the #419 regression | **#471** `a12f101` |

`scripts/lib/agent_ledger.py` (record, write/prune, read protocol, live-join filter) ·
`scripts/claude-hooks/agent-ledger-hook.py` (writer 1, Claude Code) ·
`scripts/session-manager` reads it **per host** · deploy + registration in `nix/home.nix` /
`register-nudge-hook.py`.

**Verified live, not merely deployed.** Before: `rows with an age: 0`, `claude_session_id` on
0 rows, no `stale` bucket. After the switch, both hosts: `AGENT LEDGER (15 live of 16)`,
`ages: 15 of 73 (ledger=15)`, `session ids: 15` — the hooks fired *naturally* as each live
session took its next turn, no manual probing. The laptop's writer is confirmed too (all four
events registered; its deployed hook wrote `claude-p23.json` from a real pane), so this is
verified on the WHOLE fleet, not half.

## Open investigations — live diagnosis state

### The clawgate stuck detector has never fired on a real wedge
- **Symptom + repo:** `scripts/lib/clawgate_tasks.py`. Five disjuncts; the whole point is catching
  a dispatch stranded in `in_progress` with a dead agent.
- **Observed:** live board has had **0 `in_progress` tasks** every time it was checked tonight
  (last: 22 tasks — 9 open, 7 ready_for_review, 6 complete). `stuck_count: 0` is therefore a
  *measured* zero, not a fired detector. Certified by 5 constructed fixtures + 37 mutants only.
- **Ruled out:** that it flags healthy dispatches — that was #439's bug (zero grace on
  `no_agent`/`not_kicked_off`/`agent_error`), fixed; grace is now 900s off `updatedAt`.
- **Leading hypothesis:** it will work, but the first real wedge is its first real test.
- **Next probe:** on the next genuinely wedged dispatch, read the reported `reasons`. **If it is
  `no_agent` rather than `not_kicked_off`, that confirms the link theory below.**

### clawgate task↔agent link is intermittent (upstream, ZacxDev/homelab-infra#316)
- **Observed:** `GET /api/tasks?summary=1` → `agent` key present on 19/19, **non-null on 0**.
  `GET /api/agents` → 2 agents, both `noteId: null`, one of them `status=running kickedOff=true`.
  Later, a different observer saw it **populated on 1 of 3** concurrent `in_progress` tasks.
- **Ruled out:** "never populated" — I filed that claim and **retracted it publicly** on #316. My
  sample had zero `in_progress` tasks, so it measured an absence in the one state where the link
  cannot exist. The issue is retitled to *intermittent*.
- **Leading hypothesis:** `NoteID` is set on the runbook dispatch path
  (`internal/api/runbooks.go:229,243`) and not on others.
- **Next probe:** during a live `in_progress` dispatch, `GET /api/tasks/{id}` and record whether
  `agent` is non-null; correlate with how that agent was dispatched.

### fuzzyclaw is deprecated but `session-manager` still depends on it
- **Observed, default view (fuzzyclaw off since #419):** `claude rows 51 | with an age: 0 |
  statuses: idle 40, busy 11` — **no `stale`** — and `claude_session_id` on 0 rows. With
  `--fuzzyclaw`: 30/30 ages, `stale 16`, 30/30 session ids.
- **So:** the default view has no age, no staleness, and no ClickHouse correlation. That is a
  regression shipped in #419 on a dogfood finding that fuzzyclaw "contributes nothing" — true of
  its `status` field (every live row read `paused`), false of the source.
- **Ruled out:** that task titles are affected — they come from pane titles and are fine.
- **Leading hypothesis / plan:** the spec is **merged and unbuilt** —
  `claudedocs/spec-agent-activity-ledger.md` (#428). Three fields matter (`last_activity`,
  `claude_session_id`, `window_id`); `window_id` needs no writer at all — `tmux list-panes -F
  '#{window_id}'` returns one for **every** pane (39/39 measured), contradicting a
  `session-manager` docstring that says otherwise.
- **Next probe:** none needed. Build it: a devrc-owned Claude hook + the existing opencode
  plugin + clawgate's `lastActivityAt`.

### `waiting` detector false-positive on a pane showing another session's output
- **Symptom + repo:** `Yarrow (Y)` window 1 flagged `waiting_probable` with matched line
  `⎿ vetr:2 ctx now: ctx: 0%` — which was **my own SSH probe output** echoed into that pane.
- **Observed:** a pane displaying another session's transcript trips `trailing_question` /
  `context_exhausted` on text that is not its own state.
- **Next probe:** decide whether to scope the signals to the pane's *own* last assistant block
  rather than any matching line in the capture.

### RESOLVED: fuzzyclaw is deprecated but `session-manager` still depends on it
- **Resolution:** the ledger shipped (#471) — spec §6 phase 1, SUPERSEDE. fuzzyclaw is
  untouched and still opt-in; both writers are live on purpose.
- **The three fields are back from a source this repo owns.** `window_id` needed no writer, as
  the spec said. The spec's claim that `tmux list-panes -a -F '#{window_id}'` returns one for
  every pane is **CONFIRMED** — measured 47/47 on the workbench — and the `session-manager`
  docstring asserting the opposite is retracted in place.
- **Still true and now the blocker for nothing:** `--fuzzyclaw` remains the only source of the
  `fuzzyclaw` row field. Phase 2 (migrate the six readers) is the next step.

### The `stale` bucket's meaning changed — watch it for a week
- **Observed:** `classify_status` lets `stale` WIN over `busy`. Before the ledger almost
  nothing had an age, so `stale` was empty by construction; now every agent window gets one.
- **Mitigation already in:** the hook writes on `PostToolUse` (throttled 30s), so a long turn
  keeps its age fresh and `stale` means genuinely stale.
- **Next probe:** after a few days, `session-manager --json | jq '.summary.status.stale'`. A
  non-trivial `stale.claude` count on windows that are demonstrably working means the
  heartbeat is not reaching them — check `age_source` on those rows first.

## Next steps (ranked)
1. **fuzzyclaw removal, phase 2** — migrate the six readers ONE at a time:
   `session-manager`, `agent-ops`, `tmux-scratch-status.sh`, `tmux-claude-counters.sh`,
   `verify-agent-work`, `validation/{reconcile,refsources}.py`. Then phase 3 (a test that
   fails if any fuzzyclaw read reappears), then phase 4 (remove the writers — never first).
2. **Writer 2 (opencode)** — `scripts/opencode/plugin/guard.js`, same record. 🔴
   `pane_filename` is already runtime-namespaced and pinned by a test, so an opencode pane
   record cannot collide with a claude one; that guard exists FOR this step.
3. **Writer 3 (clawgate agents)** + the §4 `kind` field (`tmux` | `cluster`), one table.
4. **The §5 bar inversion** — a 45s systemd-user timer writing
   `~/.cache/bar-status/sessions.json`, read by `tmux-scratch-status.sh` and
   `tmux-claude-counters.sh`. 🔴 The cache MUST carry its own timestamp and measured/unmeasured
   state; both existing caches have failed exactly there.
5. **`no_session_reason` is KNOWN INCOMPLETE** (`scripts/session-manager`) — it reasons about
   fuzzyclaw alone while the ledger is a second, winning supplier. Its docstring says so.
   Fixing it re-decides ~12 pinned branch selections including
   `test_the_unmeasured_reasons_are_PAIRWISE_DISTINCT`; attempted and reverted deliberately.
   Failure mode is bounded and safe: it can understate what is known, never assert an
   unsupported measured absence.
6. `render_caveats`' caveat CONTENT is unpinned (only the one-line-per-caveat relationship is).
   Pre-existing; the sibling `waiting` false-positive item below is still open too.

## Gotchas / decisions / dead-ends

**Instrument failures — every one produced a confident wrong zero:**
- `readlink -f` **returns a path for a file that does not exist.** Bit me twice (`~/.tmux.conf`,
  `~/.claude/hooks/register-nudge-hook.py`). Use `[ -e ]`.
- `bin/claude` is a **24K wrapper**; the real bundle is `bin/.claude-wrapped` (263M). Greps on the
  wrapper return 0 for terms that certainly exist (`stop_hook_active`). Always positive-control.
- `_turn_shape(path)` takes a **path**, not the payload dict. Passing the dict raises inside a
  fail-open `except` and returns `None` — which reads as "cannot parse any transcript".
- `MIN_MESSAGE_CHARS = 600`: short probe messages never fire the nudge.
- Testing against a **live** transcript is a moving target — it grows between calls and different
  gates fire each run. Freeze a copy.

**Repo/tooling:**
- `entryAfter ["writeBoundary"]` does **not** order against `linkGeneration` (which is itself
  writeBoundary-only). Verified on the built activation script: `activityCollectorEnv` 290,
  `browserBridgeExtension` 300, `linkGeneration` 502, `registerClaudeHooks` 546. Copying the
  obvious precedent would have been a **first-switch-only** bug.
- `ship.sh` pins `origin/main` once and verifies per host; a merge landing mid-run reads as
  divergence (`rc=11`). Seen twice. Also `rc=7` when another session has uncommitted work — it
  skips that host and leaves it as found, which is correct.
- A `Stop` hook reaches the model via `hookSpecificOutput.additionalContext` — **not** exit 2.
  Exit 2 blocks *and* raises "Stop hook error occurred" on every fire.
- The mutation-harness trap "syntax error scored as a kill" has now appeared **three times** in
  `next-step-nudge.py`'s history. Import the mutated module first; require a non-zero `failed=`
  count, not any non-zero rc.

**Decisions:**
- Suppressors are lexical and widening one can only make the hook **quieter** — a false
  suppressor costs one missed nudge, a false fire costs a wrong injection.
- A `RULES.md` line was **rejected** in favour of the hook: RULES is at ~32 KB of a 35.2 KB
  ceiling, loads every session on both hosts, and is concatenated into opencode's `AGENTS.md`.

**From three audit rounds on #471 — every one found something a green gate did not:**
- 🔴 **A caveat is a machine-readable claim, and it went stale the moment the code changed.**
  `CAVEATS["fuzzyclaw_scope"]` told every `--json` consumer that remote rows carry null
  `age_secs`/`claude_session_id` — exactly the fields the ledger adds. It survived because the
  guard that should have caught it was blinded by MY OWN fixture pin (`base_gather` is
  `use_ledger=False`, so the caveat-vs-code test could no longer see the ledger). **A test
  pinned to a fixture default goes blind when that default changes.**
- 🔴 **Keying on the window looked right and was wrong** — a tmux window can hold two claude
  panes; the throttle is session-scoped, so alternating writers never throttle. Measured: 10
  writes from two sessions in a 10s window all landed, last writer won, and the conflict
  detector could not see it (one file). Keyed per PANE, the same case produces the exact shape
  the detector already reports.
- 🔴 **That fix then introduced a worse bug**, caught only by the delta re-audit: `$TMUX_PANE`
  set + `tmux display-message` failing writes a pane-keyed record with NO `window_id`, which
  overwrote the good one IN PLACE. The pane key now requires a window.
- 🟡 **Three mutants survived round 1, all the same shape: the DECLARATION was asserted, the
  INSTANCE was not.** A set constant pinned; the argument at the call site not. One of the
  "behavioural" tests was outright vacuous (it unset `TMUX_PANE`, so one-file-per-key
  guaranteed the assertion with the throttle deleted).
- 🟡 **A conjunction pinned at one operand is not pinned** — `not (A or B)` had only the `B`
  half killable.

**Instrument failures, all mine, all caught:**
- **`"" or X` evaluates to `X`.** I wrote this mutant TWICE. It applies cleanly, imports fine,
  changes nothing, and reads as a coverage gap. A mutant that changes the text without
  changing the behaviour is one the harness cannot detect.
- **The sweep's CONTROL went red and stopped it** — the narrow per-mutant copy list was
  missing `scripts/testlib`. A sweep on a red baseline kills nothing.
- **`test_runtime_shebangs.py` caught my tmux stub writing `#!/usr/bin/env bash`** (dead in the
  nix sandbox). Use `testlib.mockbin.write_exec` — the repo's one definition, seventh site.
- **`test_no_real_launchers.py` is a TEXT scan**: a docstring merely NAMING `home-manager` puts
  the file in an acknowledged set. Re-justify with the grep; do not reword to dodge it.

**Decisions:**
- The ledger read carries the host's tmux **server pid** on its own sentinel line — one round
  trip, same instant as the records it validates. Window ids restart at `@0` when the server
  does, so without it a post-reboot `@41` inherits a dead session's id.
- The read uses `awk 1`, not `cat`: a record with no trailing newline welds onto its
  glob-neighbour and BOTH are lost (measured 3 written → 1 parsed, 2 unparseable).
- The throttle is consulted BEFORE `tmux` is spawned (the pane is free from `$TMUX_PANE`), so
  the `PostToolUse` hot path costs one process, not two.
- **`PANE_FORMAT` still carries no `#{window_id}`** — adding it would make the non-atomic
  `list-panes`/`list-windows` skew catchable and collapse most of the two-key join design.
  Deliberately deferred: it touches `parse_panes` and every pane fixture. Flagged in spec §2.

## How to verify
```bash
# the ledger, both hosts — `live of seen` per host, and the ages meter
python3 ~/workspace/devrc/scripts/session-manager --no-ch | grep -E 'AGENT LEDGER|live of|ages:'

# the writer, on THIS host, without touching the real ledger
python3 ~/.claude/hooks/agent-ledger-hook.py --selftest   # expect "1 expected, 1 observed -> PASS"

# registered on all four events
jq -r '.hooks | to_entries[] | .key as $e | .value[] | .hooks[]?
       | select(.command|test("agent-ledger")) | $e' ~/.claude/settings.json | sort

# the gate (authoritative)
cd ~/workspace/devrc && nix build .#checks.x86_64-linux.pytests -L 2>&1 | grep -E 'TOTAL collected|RESULT:'
```
