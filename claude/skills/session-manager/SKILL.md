---
name: session-manager
description: "Live cross-host view of every tmux window on workbench + laptop — which have Claude Code running, what each is doing, which ones are WAITING ON A HUMAN (asked a question / blocked on a modal / out of context), how stale it is, plus the clawgate approval queue, recent agent sessions, and UNSENT PROMPTS. Use for: is anything waiting on me, active sessions, what's running where, tmux state across both hosts, cross-host session status, tail a tmux window, which windows are stale/idle/busy/blocked, did I leave anything half-typed / unsent."
---

# session-manager — cross-host tmux + agent activity

`scripts/session-manager`. One-shot, **read-only**, `--json`-first, both hosts.

```bash
python3 $DEVRC/scripts/session-manager --json --lean               # everything, agent-shaped
python3 $DEVRC/scripts/session-manager --host workbench --no-ch    # fast + offline
python3 $DEVRC/scripts/session-manager detail scratch7:3           # one window + prompts
python3 $DEVRC/scripts/session-manager tail scratch7:3 --plain     # scrollback, no ANSI
```

🔴 **Rows are at `report["hosts"][<"workbench"|"laptop">]["windows"]`** — not at the top
level. Roll-ups: `summary.waiting`, `summary.unsent_prompt`, `summary.status[bucket]`,
`summary.kind`, `clawgate_queue`. And `report["not_measured"]` for what this tool does **not**
see at all.

🔴 **An ABSENT summary key is not a measured zero.** `summary.status[bucket]` pre-seeds
`claude` and `shell`, so a zero in those is real and a missing `cluster` key is not. Same
trap on the other axis: `kind` (row field — **what the entity is**) and `CLASS` (table
column — **how it is counted**) are different, and `caveats.kind_scope` carries
`kinds_produced` **measured from the rows** rather than asserted.

| flag | effect |
|---|---|
| `--lean` | 🔴 **with `--json`, prefer this.** Rows trimmed to the fields that answer this tool's question, **untruncated**, every discriminator and caveat kept |
| `--host workbench\|laptop\|all` | default `all`; `tail` resolves `all` to LOCAL |
| `--claude-only` | drop the **shell** rows (a `cluster` dispatch is an agent and is KEPT). Every count then describes the FILTERED set |
| `--no-ch` | skip ClickHouse — drops the **largest non-row block** (17.4 KB below), which answers a different question |
| `--no-capture` | skip the pane scrape; **every** `waiting_probable` AND `unsent_prompt` becomes `null` (both roll-ups `null`, never `0`) |
| `--no-ledger` | skip the ledger read → **no age, no session id** on any row |
| `--fuzzyclaw` | the task-file join, **OFF by default** (see below) |

`--json`, `--no-fuzzyclaw`, `--plain`, `--stale-threshold`, `--lines`, and what each drops:
`~/.claude/skills/session-manager/reference/payload-contract.md`.

## 🔴 Which output to ask for — you are the only consumer

Measured: **0 interactive shell invocations in 30 days against 55 agent references**. An agent
reads this, and pays by the token.

One 77-row two-host scan, re-measured 2026-08-21:

| | bytes ≈ tokens | faithful? |
|---|---|---|
| table (default) | 24,658 ≈ 6.2k | ❌ **lossy** — truncated cells, tasks over the 25-char column |
| `--json --lean` | 79,008 ≈ 20k | ✅ on what it keeps |
| `--json` | 103,801 ≈ 26k | ✅ |

🔴 **`--lean` trims ROW fields ONLY** — 39 KB of that lean payload is blocks it never touches:
`clickhouse` 17.4 KB, `clawgate_queue` 12.7 KB (the queue is enumerated **uncapped**),
`caveats` 4.3 KB, `ledger` 2.8 KB, `not_measured` 1.8 KB. **So ask for `--json --lean
--no-ch`** unless you want the session history — that block is the largest single lever and it
answers a different question than "is anything waiting on me". The tri-states, `caveats`,
`clawgate_queue` and every per-host measurement status survive all three flags.
`lean_row_fields`/`lean_host_fields` travel in the payload naming what the view CARRIES, so a
key absent from a row was omitted by the view, never measured as null.

## 🔴 `waiting_probable` — is anything waiting on a HUMAN

`status: idle` merges four states that need four different actions. Each Claude pane is
scraped (one batched `capture-pane` per host) for three signals, and every row carries the
**matched line** so you can disagree with it:

| signal | means | do |
|---|---|---|
| `trailing_question` | the agent's last sentence ends in `?` | answer it |
| `selection_menu` | `❯ 1./2./3.` modal is up | press a key |
| `context_exhausted` | `ctx: 0%` | `/clear` it |

🔴 **`waiting_probable: false` means "these three were looked for and none matched" — NOT
"this window needs nothing."** Recall is partial by construction: measured on 40 live panes
2026-08-12, a window parked on `Press Enter to continue…` matches none of them, and text
typed at the `❯` prompt is deliberately **excluded** (a window one Enter away reads `no` —
the evidence, and what would justify turning it on, is in
`~/.claude/skills/session-manager/reference/waiting-signal.md`).

🔴 **`waiting_probable: null` is not `false`.** Read `waiting_status` — `ok` (scraped),
`not_claude` (never scraped: the signals are Claude-TUI shapes, so a shell's last line ending
in `?` would be a false positive), `uncaptured`, `skipped`, `error`.
`summary.waiting.probable` is likewise **`null`, never `0`**, when nothing was scraped: the
one sentence this tool must never emit is "nothing is waiting on you" off a look that never
happened.

## 🔴 `unsent_prompt` — work PARKED one Enter away (a DIFFERENT question)

Five of 79 panes held text typed at the prompt and never sent — real work, hours old — and the
one-call answer reported none of them (2026-08-15). Now on the row as `unsent_prompt` (**the
text**, so you can triage without opening the pane) + `unsent_prompt_status`, rolled up as
`summary.unsent_prompt`.

🔴 **It is NOT part of `waiting_probable` and is never summed into it.** The same sweep
measured `waiting_probable` at **11 flagged, 11 true positives, ZERO false positives** — that
precision is this tool's most valuable property, and a noisier signal folded in would destroy
exactly it. Read both:

| | means | |
|---|---|---|
| `waiting_probable` | this window is **BLOCKED** and cannot proceed without you | go unblock it |
| `unsent_prompt` | this window has **WORK PARKED** in its input box | send it, or clear it |

🔴 **A row can carry BOTH, and that is correct** — the agent asked a question and you
half-typed a reply. Separation does not mean they never co-occur; it means neither can raise
or be summed into the other.

🔴 **`unsent_prompt: null` is an empty box ONLY when `unsent_prompt_status == "ok"`.**
Statuses: `ok`, `no_input_box`, `uncaptured`, `not_claude`, `skipped`, `error`; and
`summary.unsent_prompt.count` is **`null`, never `0`**, when no box was read. `no_input_box`
is a modal or a draft taller than the box — **unmeasured**, never "nothing typed". Shell panes
are never scraped. Scoped to the pane's OWN input line (only the lines *between the two
box-drawing rules*), so scrollback or a pane displaying another session's transcript cannot
trip it.

🔴 **NEVER PASTE CAPTURED OPERATOR TEXT INTO A COMMITTED FILE.** TWO fields carry **text the
operator typed** — `unsent_prompt` (the draft) and `clickhouse.rows[].first_msg` (the opening
prompt of every recent session) — and devrc is a **PUBLIC** repo, as is every `claudedocs/`
note, commit message, PR body, comment or test fixture an agent writes into it. Report either
as a **count, a length or a shape**, never verbatim. Quoting one back to Zach in chat is fine;
writing one to a file that gets committed is not.
(`test_no_FIXTURE_DRAFT_string_appears_in_a_shipped_doc` fails on that shape — it has
happened.)

## 🔴 `not_measured` — what this tool CANNOT see, and who owns it

A blind dogfood found this tool "precise about what it measured but it does not tell a cold
reader what it did **not** measure" — while **60 open PRs** sat outside every number it
prints. `report["not_measured"]` names each such population and the **skill that answers
it**: `pull_requests` → `standup`, `mail_queue` → `mailbox`, `cluster_alerts` → `standup`,
`initiative_board` → `initiatives`, `gui_windows_outside_tmux` → `i3`.

🔴 **It is DERIVED from the report's own keys, not a written-down list.** An entry is emitted
only while the report carries no key for that population, so the day PR querying lands the
claim stops being made with no edit anywhere. `clawgate_queue` is **not** listed — that one
*is* measured.

## 🔴 `clawgate_queue` — the clawgate approval queue

🔴 **Renamed from `blocked_on_me` (2026-08-18)** — no alias is kept and a test bans the old key
at any depth; the misread it cost is in the reference. **For panes that look like they are
waiting on a human the field is `summary.waiting.probable` — a different population, never
summed with this one.**

🔴 With the `agent-ops` TUI RETIRED this is the ONLY place the queue surfaces outside the bar
pill: never answer "is anything waiting for my approval" by pointing somewhere else. Source:
the bar poller's cache, not a live API call.

- **`count` = pending + STUCK.** `pending_count` is the `{open, ready_for_review}` half;
  `stuck_count` is `in_progress` tasks whose agent looks dead — excluding those is what hid a
  dispatch dead **four hours**. Predicate `scripts/lib/clawgate_tasks.py`, shared with the poller.
- **`open` / `ready_for_review` / `stuck` ENUMERATE the queue**, each `stuck` row with its
  `reasons` and `agent_idle_secs` — **`idle 16m` and `idle 4h` are the same boolean.**
- 🔴 **`schema_ok: false` means STUCK WAS NOT MEASURED, not zero stuck** — the cache predates
  the predicate (no `switch` + `bar-status-poll` restart yet); `stuck_count` is `null` there.
- Four states: `ok` / `stale` (cache older than 300s — the poller writes every 45s) /
  `absent` / `unparseable`. The last three publish **`count: null`, never `0`**.

## `label` + `hotkey` — a name for the sessions the slot table never named

Every row carries `label` (+ `label_source`, + `hotkey`): **`codename`** if the session is a
scratch slot → else the **leaf of the pane's cwd** → else **`main`** (`fallback`).

🔴 **`hotkey` is the actionable half** — the `$mod+Shift+<k>` that gets you there — and it is
**`null` (never `""`) on tiers 2 and 3**: no binding exists, so quoting a key would send the
operator to press something and land nowhere. 🔴 **`label` is NOT an address —
`session:window` still is**; two sessions in one repo resolve to the SAME label, so quote both.

## The caveats are in the OUTPUT, not just in this file

So read them from the run, not from here. `report["caveats"]` is structured and the table
prints one footer line each **unconditionally**, so an agent that runs the script cold gets
them anyway. The vocabulary is the keys of `CAVEATS` in `scripts/session-manager` —
`claude_detection`, `fuzzyclaw_scope`, `kind_scope`, `ledger_scope`, `waiting_signal`,
`unsent_prompt`, `pane_preview` — which `measured_caveats` fills in per scan. That list is
gated both ways against the script's own `CAVEATS`.

## 🔴 Read the exit code — the two zeroes are different facts

| code | meaning |
|---|---|
| `0` | ran, found windows (**including** a partial scan where one host was unreachable) |
| `2` | usage / bad `<session>:<window>` / **`tail`: the host answered, no such window** |
| `3` | every requested host answered and the answer is a **real zero** |
| `4` | **no** host could be reached — the zero is unmeasured, not measured |
| `5` | **`tail` only**: the host answered and there is **no tmux server** on it |

Same discipline inside the payload: `hosts.<n>.reachable`/`.error` describe the
**`list-panes`** call, `.windows_measured`/`.windows_error` the **`list-windows`** call, and
`.captures_measured`/`.captures_status` the **capture batch** — three independent
measurements, and one succeeding says nothing about the others. `clickhouse.status` must be
`ok` before `rows: []` is believable. **Never read a bare count without its status.**

## fuzzyclaw is OFF by default

Measured 2026-08-12: 29 live of 401 task files and **every one read `paused`**, including a
window demonstrably running an agent — a source `CLAUDE.md` marks UNTRUSTED. Opt in with
`--fuzzyclaw`; off, every count is `null` rather than `0`. The intersection guard still runs
when you do: a task file survives only when its `window_id` is live **and** that live window's
real `(session, index)` equals the one the file recorded.

## The agent activity ledger — where age / `stale` / `claude_session_id` come from

Two writers record one file per tmux PANE into `~/.cache/agent-ledger/` — a devrc-owned Claude
hook and an opencode plugin — read per host (locally and over SSH). Row fields: `age_secs`,
`age_source` (`ledger`/`fuzzyclaw`/null — which SOURCE answered; the ledger wins), `runtime`
(`claude`/`opencode`/null — which AGENT recorded it), `ledger`, `claude_session_id`.

🔴 **Read `report["ledger"]`, never just the row.** `status` is `ok` / `partial` / `error` /
`skipped` and only the first two publish integers — the rest are `null`, never `0`;
`summary.rows_with_age` is the meter separating a `stale=0` bucket that means *nothing is
stale* from one that means *nothing has an age*.

🔴 `runtime` is not `claude`. The `claude` column is `pane_current_command =~ /claude/`, so an
opencode window reads `shell` — an agent counted as a bare prompt in every bucket.

🔴 **A null age is not age 0** — no writer has recorded that window yet.

🔴 **Records carry the tmux SERVER pid and the reader checks it**, so after a reboot a stale
`@41` cannot hand a fresh `@41` window a dead session's id. Spec:
`claudedocs/spec-agent-activity-ledger.md`.

## Where everything lives

`scripts/tests/test_session_manager.py` is the hermetic suite (mocks tmux, SSH, CH, FS);
`scripts/lib/agent_ledger.py` owns the ledger record shape. State it reads:
`~/.cache/agent-ledger/*.json` (the ledger), `~/.cache/bar-status/clawgate.json` (the queue
cache, written by `scripts/bar-status-poll`), `~/.tmux/tasks/*.json` (fuzzyclaw, UNTRUSTED),
`scripts/tmux-scratch-slots.sh` (codenames).

**Reference topics** — each costs nothing until you open it:

| load it when | file |
|---|---|
| reading `--json` field by field — every flag, summary buckets, the lean view, row/label tiers, what each caveat entry carries, ledger internals | `~/.claude/skills/session-manager/reference/payload-contract.md` |
| changing or doubting the `waiting` / `unsent_prompt` detector | `~/.claude/skills/session-manager/reference/waiting-signal.md` |
| a caller must branch on an exit code | `~/.claude/skills/session-manager/reference/exit-codes.md` |
| reading the approval queue field by field | `~/.claude/skills/session-manager/reference/clawgate-queue.md` |
| touching the fuzzyclaw task-file join | `~/.claude/skills/session-manager/reference/fuzzyclaw-guard.md` |
| writing a ClickHouse query over the session history | `~/.claude/skills/session-manager/reference/clickhouse-queries.md` |
| the SSH call, its failure taxonomy, or which host is which | `~/.claude/skills/session-manager/reference/cross-host.md` |

## Gotchas

- Both hosts report `hostname` as `nixos`; the local label comes from `ACTIVITY_HOST` (env,
  then the collector env file), defaulting to `workbench`.
- `tmux` saying *"no server running"* is a **reachable** host with zero windows, not an
  unreachable one. Keep the two separate.
- `signal` / `kill` are **not implemented, deliberately** — this tool never writes to, signals
  or kills a window, which is the only reason it is safe to point at a live machine holding
  40+ windows of real work. A `waiting` flag is a read; acting on it is not.
- Merged ≠ deployed: this file only reaches `~/.claude/skills/` on a `home-manager switch` /
  `ship.sh`. The script runs straight from the repo checkout.
