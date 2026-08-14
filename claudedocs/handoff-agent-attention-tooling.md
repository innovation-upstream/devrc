# Handoff: agent-attention-tooling — 2026-08-14

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
- **Branch:** `main`, at `df12bdf` = `origin/main`. Both hosts converged and switched
  (`ship.sh`: workbench 431 managed artifacts resolve / 0 dangling, laptop 393 / 0), and the
  deployed skill + tool were exercised on BOTH hosts, not just rolled out.
- **Working tree:** ⚠ the base clone is DIRTY with work that is NOT mine and NOT in any PR —
  `nix/i3/config.nix` (a `for_window [class="Espanso"] floating enable` rule) and
  `scripts/tmux-scratch-status.sh` (per-session window counts on the scratch slots, plus an
  em-dash→`--` mangling that looks unintended). Untracked: `.envrc`, `.opencode/`,
  `claudedocs/proposal-tmux-server-multiplexing.md`, `claudedocs/proposed-rules-cut/`,
  `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`. **`ship.sh` says `tree is DIRTY`
  on BOTH hosts** — it still converged, but this is stranded work one `checkout` from deletion.

**Shipped and verified live this session:**

| what | PR / sha |
|---|---|
| the agent activity ledger — closes the #419 regression | **#471** `a12f101` |
| writer 2 (opencode) | **#478** `1797d42` |
| the `kind` entity axis + the design pass gating writer 3 | **#482** `df12bdf` |

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

### RESOLVED: the clawgate stuck detector HAS now fired on a real wedge
- **Fired 2026-08-14** against the live board: `stuck_count: 2` — tasks **#193** and **#194**,
  reasons `["no_agent"]`, dispatch age **86,119s (23.9h)**, `updatedAt` unmoved since
  2026-08-13T20:12. Not a fixture and not a mutant. Both were re-opened
  (`PATCH /api/tasks/{id}/status` → `open`); the board then read `in_progress: 0` and the
  detector re-ran to `stuck_count: 0`.
- 🔴 **THE HANDOFF'S DISCRIMINATOR WAS WRONG AND IS RETRACTED.** It said *"if the reason is
  `no_agent` rather than `not_kicked_off`, that confirms the link theory"*. It was `no_agent`,
  and that **confirms nothing**: `no_agent` fires when `task.agent` is null, and BOTH the link
  theory (#316: an agent exists, unlinked) and "no agent was ever dispatched" produce a null
  there. An absence cannot separate two causes that both produce it — the same trap that made
  me file and retract the "never populated" claim on #316.
- **Evidence favours the RIVAL mechanism**, so this WEAKENS the link theory: `/api/agents`
  held **2 agents total** (ids 10, 40; created 2026-06-06 and 2026-07-30) — both PREDATE tasks
  193/194, so there was no unlinked candidate to link to. And both tasks flipped to
  `in_progress` **10 ms apart**, which is a batch status write, not two dispatches.
- **Next probe (the only one worth spending):** the dispatch-side log or the `devpod-<name>`
  pod logs for the 20:08–20:12 window. If no `POST /agents` was issued, it is the rival
  mechanism and the detector is correctly reporting *"claims to be in progress, nothing is
  working on it"* — which is the operator-relevant fact either way.

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

🔴 **Writer 3 is GATED, not next — and spec §4's premise is wrong.** Design pass with the
measurements: `claudedocs/design-ledger-writer3-and-kind-2026-08-14.md`. In short: §4 says the
primary entity "becomes an agent run", but clawgate has **no agent-run entity** — `/api/agents`
is 2 long-lived devpods, both `status: error`, one created 2026-06-06. The ephemeral thing is
the **task in `in_progress`**, which `scripts/lib/clawgate_tasks.py` already models and which
just reported the 2 wedged dispatches correctly. So if cluster rows are ever built, their
source is `/api/tasks` filtered to `in_progress`, **not** `/api/agents`.
**Trigger to un-gate:** the `in_progress` population routinely non-zero **and** `task.agent`
non-null (#316 resolved). Both false today; re-check with one `/api/tasks?summary=1` call.

1. **fuzzyclaw removal, phase 2** — migrate the readers ONE at a time. `tmux-scratch-status.sh`
   is already done (#475 deleted the marker rather than migrating it). Remaining:
   `session-manager`, `agent-ops`, `tmux-claude-counters.sh`, `verify-agent-work`,
   `validation/{reconcile,refsources}.py`. Then phase 3 (a test that fails if any fuzzyclaw
   read reappears), then phase 4 (remove the writers — never first).
2. **agent-ops retirement does NOT gate on writer 3 or on `kind`** — re-read of spec §7 against
   the code: every disposition routes to `session-manager` / `standup` / `/initiative-scan`.
   The one 🔴 KEEP is the **`/proc` detector** (`scripts/i3status-agent-ops` depends on it, and
   it is strictly more accurate than `pane_current_command =~ /claude/`, which is why rows
   render `? unk`). Moving that to a shared module IS the real gate.
3. **Writer-3 prerequisites, tracked from the #482 audits** (same defect class as `kind`, left
   open at the sites it did not consolidate):
   - 🔴 `--claude-only` filters on `r["claude"]` and runs BEFORE `measured_caveats`, so a
     cluster row would be dropped AND have its exclusion attributed to the build rather than
     to the filter.
   - The `CLASS` column and every roll-up are class-generic now, but nothing else is.
4. **The §5 bar inversion** — a 45s systemd-user timer writing `~/.cache/bar-status/sessions.json`.
   🔴 The cache MUST carry its own timestamp and measured/unmeasured state; both existing caches
   have failed exactly there. Note #475 deleted the `●` waiting marker outright (the operator
   does not use it), so this now has one fewer consumer — re-justify before building.
5. **`no_session_reason` is KNOWN INCOMPLETE** (`scripts/session-manager`) — it reasons about
   fuzzyclaw alone while the ledger is a second, winning supplier. Failure mode is bounded and
   safe: it can understate what is known, never assert an unsupported measured absence.
6. `render_caveats`' caveat CONTENT is **now pinned** for `kind_scope` (whole-string, five
   branches) but NOT for the other four caveats. The `waiting` false-positive item below is
   still open.

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

**From SIX audit rounds on #482 — ten findings, and SIX were defects in the VERIFICATION,
not in the code. Every one passed a full green gate.**
- 🔴 **A guard on PROSE is walkable by rewording, twice over.** `"tmux" in line and "cluster"
  in line` was satisfied by the sentence's own STATIC prose, so neither computed slot was ever
  read. Replacing it with a ban on one literal phrasing was then walked by *"every row HERE IS
  a tmux pane"*. Banning KINDS-by-name was walked by a SYNONYM (*"a terminal pane… the second
  enumerated entity"*). **Only the WHOLE-STRING PIN kills these.** Accept that a cosmetic
  reword fails the test — that is the trade.
- 🔴 **A FIXTURE WHOSE VALUE EQUALS THE CONSTANT CANNOT SEE THE DIFFERENCE.** Hit THREE times:
  `kinds_produced == ["tmux"]` off a tmux-only gather (a hardcoded `["tmux"]` is identical);
  `kinds_enumerated == ["tmux","cluster"]` (that IS `KINDS`); and
  `KINDS_PRODUCED_BY_CONSTRUCTION == ("tmux",)`. The third recurred **in the commit that fixed
  the second.** The control is mechanical: monkeypatch/pass a value the constant CANNOT equal
  and watch the output move.
- 🔴 **EACH FIX CREATED THE NEXT FINDING, four rounds running.** Making the caveat measured
  meant a zero-row scan measures `[]`, and `[] or ["tmux"]` is `["tmux"]` — a literal
  masquerading as a measurement, one function downstream of its own fix. Consolidating two
  sentence builders made a tail say "this scan" under a head saying "no scan measured here".
  **Delta-re-audit every fix round; do not assume closure.**
- 🔴 **A LITERAL IN A CONSTANT CAN MAKE ITS OWN FIX UNREACHABLE.** `CAVEATS` carried
  `kinds_produced: ["tmux"]`, so the bare-constant render took the MEASURED branch and the
  reworded honest branch was dead code — while the false sentence stayed live and a comment
  asserted a path its code could not take.
- 🔴 **"DEAD" MEANS DEAD THROUGH ONE ENTRY POINT.** An audit called a filter unreachable
  because its input derives from `gather`; `render_table` is also called on a bare report, and
  removing it raised `KeyError`. Verify reachability per CALLER, not per function.
- 🔴 **A PURITY CLAIM IS ONLY AS TRUE AS ITS DEEPEST SHARED OBJECT.** `{k: dict(v)}` left every
  nested dict/list shared with the module constant, and the test asserted a top-level REBIND —
  exactly one level too shallow to see it.
- 🔴 **`cp -a` OF A WORKTREE GIVES ZERO GIT ISOLATION.** `.git` is a POINTER FILE; the copy
  shares the real git dir, index, refs and reflog. An auditor's scratch `git commit` landed on
  the live branch (recovered, never pushed). **`rm -f <copy>/.git` after every `cp -a`.** RULES'
  "a worktree isolates a working DIRECTORY only", in a new shape.
- 🟡 A dropped `sorted()` is killed only on the runs whose PYTHONHASHSEED disagrees. Pin the
  seed in a subprocess **plus a positive control** asserting that seed still orders the raw set
  the other way — otherwise the guard is a coin flip.

## How to verify
```bash
# the ledger, both hosts — `live of seen` per host, and the ages meter
python3 ~/workspace/devrc/scripts/session-manager --no-ch | grep -E 'AGENT LEDGER|live of|ages:'

# the writer, on THIS host, without touching the real ledger
python3 ~/.claude/hooks/agent-ledger-hook.py --selftest   # expect "1 expected, 1 observed -> PASS"

# registered on all four events
jq -r '.hooks | to_entries[] | .key as $e | .value[] | .hooks[]?
       | select(.command|test("agent-ledger")) | $e' ~/.claude/settings.json | sort

# the gate (authoritative). 🔴 A CACHE HIT RETURNS EXIT 0 WITH NO OUTPUT — read the log,
# never trust silence, and never read a piped exit code.
nix build ~/workspace/devrc#checks.x86_64-linux.pytests -L --no-link 2>&1 \
  | grep -E 'TOTAL collected|RESULT:'

# the entity axis, live — every row tmux, and NO cluster key in any bucket
python3 ~/workspace/devrc/scripts/session-manager --no-ch --json \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["summary"]["kind"], d["summary"]["status"]["idle"])'

# the clawgate board + the stuck detector, one call
curl -s -H "Authorization: Bearer $CLAWGATE_HOOK_TOKEN" \
  "${CLAWGATE_API_URL:-http://192.168.50.250:30302}/api/tasks?summary=1" \
  | python3 -c 'import sys,json,collections; t=json.load(sys.stdin); print(collections.Counter(r["status"] for r in t)); print("agent non-null:", sum(1 for r in t if r.get("agent")))'
```
