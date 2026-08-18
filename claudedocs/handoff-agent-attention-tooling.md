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
- **Branch:** `main` at `5fe72e9` = `origin/main`. Both hosts converged + switched (`ship.sh`:
  workbench 435 managed artifacts resolve / 0 dangling, laptop 396 / 0).
- **Working tree:** `flake.lock`, `scripts/opencode/opencode.jsonc`,
  `scripts/tests/test_opencode_config.py` modified by a CONCURRENT session — not mine, left alone.
  Plus the long-standing untracked `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`.
- ⚠ **Heavy concurrency.** `main` moved ~10× mid-session; the base clone was checked out onto
  another session's branch (`docs/signal-chat-proposal-revision`) for hours. Re-fetch before
  assuming anything, and gate the MERGED tree, not the branch.

**Shipped and verified live this session — 6 PRs, all merged AND deployed to both hosts:**

| what | PR |
|---|---|
| clawgate write-back guard — prose lost 2/2, so gate it on the READ | **#506** |
| `--claude-only` blamed the BUILD for what the FILTER removed; fuzzyclaw phase-2 gate as rc 16 | **#513** |
| `--dismiss` now writes a tombstone — verifying a dismissal re-armed it, twice, in production | **#515** |
| unsent prompts as a FOURTH signal + `not_measured` names what the tool cannot see | **#519** |
| delete the dspy-eval capture (captured text in a PUBLIC repo) + gate the class | **#521** |
| the `.html`/`.txt` captured-content gate + the Linkerd determination written down | **#526** |

**Deploy verified at the consumer, not inferred:** both hooks byte-identical to `origin/main`
(`clawgate-writeback-guard.py` `2c63a4fd01e5`, `next-step-nudge.py` `ebc881d31e31`), registered on
`PostToolUse` (no matcher) + `Stop`, 6 Stop owners. `SuccessExitStatus=16` live on both hosts.

**Live readings at handoff time** (`session-manager --no-ch --fuzzyclaw --json`, 81 rows):
`age_source {ledger: 43, fuzzyclaw: 6, None: 32}` · `unsent_prompt {count: 9, measured: 56,
unmeasured: 25}` · `waiting {probable: 8, measured: 58}`.

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

### RESOLVED: the clawgate wedge — mechanism (b), no agent was ever dispatched
- **Was:** tasks #193/#194 sat `in_progress` 23.9h with `agent: null`; the stuck detector
  fired for the first time (`stuck_count: 2`, reasons `["no_agent"]`).
- 🔴 **The handoff's own discriminator was WRONG and is retracted.** It said `no_agent`
  rather than `not_kicked_off` would confirm the #316 link theory. `no_agent` is an
  ABSENCE and cannot separate "link broken" from "never dispatched" — following it would
  have recorded a coin flip as a diagnosis.
- **Settled by server-side route counters** (`clawgate_http_requests_total`, 30d Prometheus
  retention). The one pod serving at 20:12:22 (`clawgate-6d7779c7d9-7fxzp`, 0.7.91) served
  `PATCH /api/tasks/{id}/status` ×2 and **no `POST /agents` at all**;
  `clawgate_agent_provision_total` empty on that pod. Both controls run: task-creation
  counters saw the window (×2), and `POST /agents` is a real emitted label on 11 other pods
  — so the zero is measured, not wiring.
- **The receipt:** a local Claude session's own transcript, a two-iteration `curl` loop
  flipping both to `in_progress` — the 10 ms "batch write". "dispatch both" meant **local
  subagents**, not a devpod. It never wrote back.
- 🔴 **`no_agent` conflates THREE states, not two.** `Provisioner.Destroy` hard-deletes the
  agent row with no tombstone, and deleting a task sets `agents.note_id` NULL.
- **Recommended, not built:** split `agent_link_missing` (some agent claims this task id but
  the task read shows `agent: null`) from a residual `no_agent`. One extra API call, and it
  gives #316 a detector instead of an anecdote. `never_dispatched` vs `agent_missing` is
  **not client-observable** — it needs clawgate to write a `system` comment on dispatch.

### RETRACTED: two "defects" I reported that were misreadings
- **`DELETE /api/tasks/{id}/comments/{cid}` blanking the body** is a designed **soft delete**
  — shipped homelab-infra #318, live at 0.7.95, `retracted: true`, body redacted rather than
  filtered so the thread shows a tombstone instead of silently shortening. `retracted` is
  `omitempty` (absent on normal comments) and the reference's own `jq` recipe printed
  `.body`, so a retraction rendered as an unexplained blank. **The trap was in the docs**;
  fixed in #499.
- **"The server truncates comment bodies" (3929 → 3928 runes)** — nothing was truncated. A
  trailing newline meeting `strings.TrimSpace` server-side, compared untrimmed in the CLI.
  Real cap is 200,000 runes. I quoted the CLI's warning as fact about the server.

### The board write-back is a single point of failure — 2 for 2
- **Observed:** both tasks dispatched this session were **already shipped** — #193 by #458,
  #194 by #461, whose commit subject literally reads `(#194) (#461)`. Both cards stayed
  `open` with **zero comments**, so both were re-dispatched and paid for twice.
- **So:** git recorded it, the board never did. The pickup ritual's write-back is the only
  thing closing that loop and it failed both times, including once by me an hour after
  diagnosing it. That is evidence it needs enforcing by something other than discipline.

### `--claude-only` is a writer-3 prerequisite, and got slightly worse
- It filters on `r["claude"]` (`scripts/session-manager`) and runs BEFORE `measured_caveats`,
  so once cluster rows exist a filtered-out row is dropped **and** has its exclusion
  attributed to the build rather than to the filter.

### The fuzzyclaw phase-2 gate is OPEN-BUT-NOT-READY, and decaying on its own
- **Observed:** `drift-check.sh` rc-16 block reads `fuzzyclaw-only ages: 6 of 49 row(s) EXAMINED`
  → `NOT READY`. Was **7** earlier the same day, so the count is decaying as pre-deploy sessions
  restart. Phase 2 (deleting the fuzzyclaw readers) is safe at **0**.
- **Ruled out:** that this needs a human decision — it does not. It is now a machine check.
- **Next probe:** none. Wait. `bash scripts/drift-check.sh` reports it every run, and the
  `drift-check` systemd timer fires 4×/day.

### `waiting` false-positive on a pane showing ANOTHER session's output — STILL OPEN
- **Symptom:** a pane displaying another session's transcript trips `trailing_question` /
  `context_exhausted` on text that is not its own state. Originally seen as `Yarrow (Y)` window 1
  matching on this session's own SSH probe output echoed into that pane.
- **Observed this session:** the same *class* was independently re-derived while designing
  `unsent_prompt` — which is why `detect_unsent_prompt` reads ONLY between the two box-drawing
  rules (`_input_box_span`), and why `PANE_TAILING_A_RENDERED_BOX` exists as a fixture.
  `waiting`'s own detector was NOT given that treatment.
- **Leading hypothesis:** scope the waiting signals to the pane's own last assistant block, the
  same way the unsent detector scopes to its own input box. The helper already exists.
- **Next probe:** `python3 scripts/session-manager --no-ch --json | python3 -c 'import sys,json;
  d=json.load(sys.stdin); print([(r["window"], r["waiting_signals"]) for hv in d["hosts"].values()
  for r in (hv.get("windows") or []) if r.get("waiting_probable")])'` — then read each flagged
  pane and check whether the matched line is its own output.

### `idle`-with-no-age and `idle`-with-a-fresh-age render identically
- **Observed:** 32 of 81 rows have `age_source: None`. Measured decisively earlier: **0** of them
  are a join bug — every ageless row genuinely has no ledger record, because its Claude process
  started BEFORE the ledger shipped (`2026-08-14T04:01:41Z` is the first record; those processes
  date from Aug 5–12). Self-healing: the first tool call in such a window writes a record.
- **Ruled out:** a read/join defect (0 ageless rows had a record present), and prune (TTL is 7d).
- **Next probe:** none needed for correctness. The open question is presentational — `idle` with
  no age and `idle` with a 2-minute age are different states rendered the same.

## Next steps (ranked)
1. **Nothing is blocking.** All six PRs are merged, shipped and consumer-verified. The items
   below are follow-ups that were deliberately filed rather than fixed.
2. **The shared-function blind-spot class** — `_state_root()`'s two path components, `STATE_WORK`,
   `_dismissals_path()` in `scripts/claude-hooks/clawgate-writeback-guard.py` each rename a live
   on-disk artifact with **ZERO test movement**. Renaming the state root on a deploy would
   silently orphan every in-flight session's read anchors. `_dismissed_path` is pinned; the fix
   was applied to the instance found, not the class. One PR pinning every on-disk name literally.
3. **#513 🟡B — `summary.rows_with_age` exists and is unused.** Adding it to `drift_phase2.py`
   closes the last theoretical `age_sources` gap in ~2 lines, and the reason-token ledger will
   FORCE the token. The invariant holds on live data: `rows_with_age == total_sessions -
   age_sources["none"]`.
4. **Scope the `waiting` signals to the pane's own block** (investigation above).
5. **`initiatives_current_slugs.txt`** — 171 lines / 165 distinct project slugs, ~124 appearing
   nowhere else in the repo, in a PUBLIC repo. It is FUNCTIONAL (a routing vocabulary the tests
   measure against), so removal has real cost. Gated and pinned; hashing or truncating is the
   middle path. **Operator judgement, not a defect.**

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

**Five occurrences of ONE trap, and it is now a rule:**
🔴 **A fixture whose value EQUALS the constant it tests cannot see the difference.** Every
time, a mutant replacing a lookup with a hardcoded literal produced byte-identical output and
survived a fully green suite: `kinds_produced == ["tmux"]` off a tmux-only gather;
`kinds_enumerated` set to a value that WAS `KINDS`; `KINDS_PRODUCED_BY_CONSTRUCTION` pinned
to the same value every other assertion used; `stuck_count: 2` in every carry-forward fixture
(so `stuck > 0` → `stuck > 1` survived, and a SINGLE wedged dispatch would have dropped from
`!1?` Critical to `?` Warning); and a systemctl stub with no mode failing only the `--failed`
probe. **The third recurred INSIDE the commit that fixed the second.** The control is
mechanical: feed a value the constant CANNOT be, and watch the output move.

🔴 **A mutation that did not APPLY is not a survival.** Bit three parties: an agent's
validator used `py_compile.compile(path, cfile="/dev/null")`, which raises `FileExistsError`
on every input → 5 phantom survivors; I patched by string match, the pattern silently did not
match, and the green suite read exactly like a result; and another session landed #504 on the
same class (198/200 never ran). Assert the patch site occurs exactly once, apply by line
number when the text is fiddly, and PROVE the mutation landed before scoring it.

🔴 **A guard on PROSE is walkable by rewording — pin the WHOLE normalised string.** Four
guards were walked: two asserted words that also appear in the sentence's own STATIC prose;
banning one literal phrasing was walked by "every row **here is** a tmux pane"; banning a
term by NAME was walked by a SYNONYM ("a terminal pane… the second enumerated entity"). The
structural version ("names no member of `KINDS`") adds no kill power on its own — the
whole-string pin is what kills. Accept that a cosmetic reword fails the test.

🔴 **`grep -c` counts MENTIONS, not INSTANCES — it lied to me three times in one session.**
"agent-ops referenced 4,233 times" (it is in always-loaded context; real invocations: **0**);
"the false claims are still present" (they were fixed — the phrase now appears inside a
comment explaining that it did not); "16 vs 22 requireHookToken sites" (my count included
three comment lines and the function definition; 16 route wraps is right).

**Instrument failures, all mine:**
- A **cache-hit `nix build` returns exit 0 with NO output** — read the derivation log, never
  trust silence. Nearly read it as success twice.
- **zsh brace-expands `{'a':1,'b':2}` inside double quotes**, so a control payload I thought
  I wrote never landed; and `"$BR:scripts/…"` is eaten by history modifiers (`:s`) →
  "bad substitution". Brace it: `${BR}`.
- **A non-integral `ts` is rejected by design** (`cache_age_secs` treats it as "not from a
  poller we recognise"), so a float `time.time()` in a fixture short-circuits every case to
  `?` and looks exactly like a code bug.
- **A subprocess harness cannot distinguish a correct render from a swallowed crash** —
  `__main__` catches everything and prints a byte-identical `?` pill. Five parametrizations
  passed with `render()` replaced by `raise`. Call `render()` DIRECTLY.

**Decisions:**
- **A recorded alarm is carried forward** (operator, 2026-08-15). A frozen cache renders
  `39?` / `LEAK?` in Critical, not `?` in Warning — alerts do not resolve because the poller
  died, and the `?` already marks it unmeasured. A MEASURED quiet board still hides.
- **`RULES.md` ceiling raised, not evicted** — measured first: `skill-audit.py` found ZERO
  work-status and ZERO dated-lesson blocks, and 18 of 24 fat lines already carry
  `→ archive:` tags. Note `skill-audit.py` reports RULES.md "over target by 21,980 B"
  against a **12 KB SKILL** budget that does not govern it; the prune-skill's own 🔴 says
  report the mismatch, not execute it.
- **agent-ops TUI deleted, detector kept.** 0 invocations in 30 days; the GUI half rests on
  the operator's statement, not data (transcripts cannot see a bar-button launch).
- 🔴 **A block rename needs `i3-msg restart`.** The switch DELETES the old symlink while a
  running `i3status-rs` holds the old parsed config, so the pill is broken between `ship.sh`
  and the restart. Sequence: **merge → `ship.sh` → `i3-msg restart` → re-check the pill.**
  Now in `claude/skills/bar/SKILL.md`, generalised to any block rename/removal/`command`
  change.
- **`cp -a` of a worktree gives ZERO git isolation** — `.git` is a POINTER FILE, so a copy
  shares the real git dir, index and refs. An auditor's scratch `git commit` landed on a live
  branch (recovered, never pushed). `rm -f <copy>/.git` after any copy.

**Loose ends, not defects:**
- Two `i3status-rs` processes from Aug 4 are orphaned to systemd (`--no-init`, ppid 1,
  attached to no `i3bar`). Not killed — resolving PIDs I did not start is the documented
  sibling-kill hazard.
- `~/workspace/homelab-talos` (which IS `ZacxDev/homelab-infra` — the directory name lies)
  carries untracked `.kube/`, a `cilium-l2-announcement-policy.yaml`, and a modified
  `flake.nix`.
- GitHub Actions is **billing-blocked repo-wide** on homelab-infra; gates run on Tekton as
  `tekton/clawgate-ci`, and the workflow was cut to `workflow_dispatch` so it contributes no
  red check. Merging a `containers/clawgate` change does NOT redeploy clawgate.

**Six audit rounds this session, and EVERY fix round created the next finding. None of it was
visible to a green gate.**
- 🔴 **A fix for a false positive made the guard INERT on the incident that motivated it.**
  #506 round 2: refusing every `agent_id` event (to stop a subagent's READ arming the parent)
  also stopped a subagent's WORK counting — and the handoff records that #193/#194's work ran in
  local subagents. The guard would have been silent on its own motivating case. Fixed
  **asymmetrically**: a subagent's read does not arm, its work does count.
- 🔴 **"Equivalent mutant" hid a real one, twice.** #515: the tombstone write-order was labelled
  an equivalent-order negative control; writing it AFTER the removals leaves a window where
  `record_read` re-creates `read-<id>`, producing the exact false-promise state the PR existed to
  remove. No test distinguished the orders.
- 🔴 **A gate can be blind to its own motivating shape.** #526's `.txt` free-text rule could not
  see the slugs fixture (all 144 data lines are single tokens, 0 have whitespace) and its `.html`
  prose rule keys on the LONGEST UNINTERRUPTED TEXT RUN — measured: 5×40 and 8×25 char runs
  totalling 200 chars of prose → **0 findings**. Fixed with block aggregation + a run-count rule.

**Instrument failures — mine, and they produced confident WRONG zeros:**
- 🔴 **A marker scan looked for the wrong vocabulary and I relayed its zero as fact.** The sweep
  reported `forum-thread-page.html` as "a real scraped thread, 0 synthetic markers, 7 real
  hostnames". Measured: **150 SYNTH tokens, 144 `example.test` refs, 0 other hosts**, and the
  file's own first line declares it sanitised. Its positive control matched `html` in the same
  file — proving the file was READABLE, not that the marker patterns could MATCH.
- 🔴 **A fixture one character under a threshold.** Re-testing #526's prose rule, my runs were
  `'word '` truncated to 25, which strips to **24** — one under `MIN_CHAT_TEXT_CHARS=25`. Every
  cell read 0 and I nearly reported the fix as ineffective.
- **`openssl ec -in -` does not accept `-` as stdin at all** — a freshly generated P-256 key
  fails identically. Reading "unable to load Key" as "malformed key material" is available and
  wrong; decode the DER instead.
- **`nix build path:<worktree>#…` is a FALSE RED** — a worktree's `.git` is a *file*, `path:`
  copies it, and five pre-existing tracked-ness tests then fail `git exit 128`. Use the git flake ref.
- **`pgrep -f` self-match caught THREE agents this session**, one of them mid-PR. A loop
  `until ! pgrep -f "<pat>"` matches its own command line and can only end by timeout.

**Decisions:**
- **Git history is NOT being rewritten** (operator, 2026-08-17). The dspy capture was 333 of the
  operator's OWN truncated prompts (≤200 chars) — NOT third-party bodies, a mischaracterisation
  that reached the #521 commit message. Credential sweep of all 3,444 reachable blobs: clean.
- **Linkerd anchors: dead, no rotation** (operator: "we dont use linkerd"; measured 0 Linkerd
  namespaces across 3 reachable clusters). Four P-256 keys remain in reachable history by
  decision. Recorded in `SECRETS.md`.
- **The client workspace path in 34 tracked files is an ACCEPTED disclosure** — the client's org
  and flagship repo are both public, 141 files already carry the brand as prose, and a partial
  scrub would be worse than none (~8 additional partial-fragment carriers a full-path scrub misses).
- 🔴 **All four gates scan `git ls-files` only — none reads history.** That is what hid the
  private keys for 4–5 years. Recorded as an EXECUTABLE limitation pin
  (`test_no_captured_markup.py::test_the_gate_is_blind_to_git_history`), not prose.
- **Two briefs of mine were substantially wrong and the agents that pushed back with measurements
  were right both times.** Keep telling them to refute rather than implement.

## How to verify
```bash
# the write-back guard, deployed copy — the ORIGINAL failing path must block
G=~/.claude/hooks/clawgate-writeback-guard.py
S=verify-$$
printf '%s\n' "{\"hook_event_name\":\"PostToolUse\",\"session_id\":\"$S\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"clawgatectl task get 200\"}}" | python3 "$G" >/dev/null
printf '%s\n' "{\"hook_event_name\":\"PostToolUse\",\"session_id\":\"$S\",\"agent_id\":\"sub\",\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"/tmp/x\"}}" | python3 "$G" >/dev/null
printf '%s\n' "{\"hook_event_name\":\"Stop\",\"session_id\":\"$S\"}" | python3 "$G"   # -> decision: block
python3 "$G" --dismiss 200 --session "$S"                                             # durable: a re-read must NOT re-arm
rm -rf ~/.cache/claude-clawgate-writeback/s/*verify-*

# the phase-2 gate + unsent prompts, live (rc 16 = ACTIONABLE, not drift)
bash scripts/drift-check.sh | sed -n '/phase-2/,$p'

# the four gates (siblings must stay 134)
nix build .#checks.x86_64-linux.pytests --no-link && nix log .#checks.x86_64-linux.pytests | grep -E 'TOTAL collected|RESULT:'
# 🔴 a cache hit returns exit 0 with NO output; never use `path:<worktree>` (false red)
```
