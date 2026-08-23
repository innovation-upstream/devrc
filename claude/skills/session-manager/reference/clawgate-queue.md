# `clawgate_queue` — the field ledger and the archaeology

Loaded when you are reading the approval-queue block field by field, or want the
evidence behind the rename and the `stuck` predicate. The SKILL body carries the claims
a consumer must act on; this is the full text behind them.

_Moved verbatim out of `SKILL.md` on 2026-08-21, when the body was cut from 23,233 B. The core keeps every load-bearing claim below in compressed form; this is the wording it was cut from, and the evidence behind it._

## 🔴 `clawgate_queue` — the clawgate approval queue

🔴 **Renamed from `blocked_on_me` (2026-08-18).** The old name read as "everything waiting on
you" and was measured doing exactly that damage: a caveat in this tool's own payload already
said *"reading it as one is the misread this entry exists to prevent"*, and a reader made that
misread anyway and shipped it into a brief for three subagents. A field name is read a hundred
times for every once its caveat is, so the name changed rather than the wording.
**For panes that look like they are waiting on a human, the field is `summary.waiting.probable`
— a different population, never summed with this one.** No alias is kept: the old key is gone,
and a test bans it at any depth in the payload, because a key that silently means something
else is worse than a key that is absent.

Read from the bar poller's cache. It is here because an accurate cross-reference once cost
real signal: a dogfooding agent read that the (now retired) `agent-ops` TUI had no JSON API,
correctly preferred this script, never opened agent-ops, and **missed 11 pending approvals —
four of them credential-exposure or cross-user-data-leak.** 🔴 With that TUI gone this
section is the ONLY place the queue surfaces outside the bar pill, so the lesson binds
harder, not less: never answer an "is anything waiting for my approval" question by pointing
somewhere else.

- **`count` = pending + STUCK.** `pending_count` is the `{open, ready_for_review}` half;
  `stuck_count` is `in_progress` tasks whose agent looks dead. The two always travel with
  the total. 🔴 Excluding `in_progress` ("an agent is working = not on the human") is exactly
  what hid a dispatch whose agent had been dead **four hours** — invisible on every surface,
  because this script reads the poller's cache rather than the API. The predicate is
  `scripts/lib/clawgate_tasks.py`, shared with the poller.
- **`open` / `ready_for_review` / `stuck` ENUMERATE the queue** — a count that moves without
  naming what moved is how three finished tasks appeared in no list anywhere. Each `stuck`
  row carries `reasons` (`no_agent` / `agent_error` / `not_kicked_off` / `agent_idle` /
  `activity_unknown`) and `agent_idle_secs`: **`idle 16m` and `idle 4h` are the same
  boolean** and only one is worth acting on. Idle time cannot see progress *within* an
  in-flight turn, so it errs toward a false alarm — the intended direction.
- **`count` is the measurement; `detail` is not.** Nothing here parses it. Schema-2 detail
  states its own cap (`(+N more)`); a schema-1 string truncated silently to ~6 ids and has
  dropped `ready_for_review` items.
- 🔴 **`schema_ok: false` means STUCK WAS NOT MEASURED, not zero stuck** — the cache came
  from a poller predating the predicate (i.e. `home-manager switch` + restart
  `bar-status-poll` has not happened yet). `stuck_count` is `null` in that case.
- Four states: `ok` / `stale` (cache older than 300s — the poller writes every 45s) /
  `absent` / `unparseable`. The last three publish **`count: null`, never `0`**.
- ⚠ **`agent-ops` used to be the titled enumeration and it is RETIRED** — the tmux popup on
  `$mod+i` / `prefix+A` / the ▦ bar button. Do not send a reader there. **This tool replaced
  it**: `clawgate_queue.open[]` / `.ready_for_review[]` / `.stuck[]` each carry `{id, title}`
  (`stuck` also `reasons` + `agent_idle_secs`), read out of the poller's cache — so the titles
  are here, no API call needed. Measured 2026-08-21: 63 / 19 / 5 rows, every one titled. The
  bar's clawgate pill carries only the live count (`22!2` = 22 needing you, 2 stuck), which is
  why the pill alone is not an answer. The one part of agent-ops worth keeping, its `/proc`
  walk that finds a `claude` buried under a wrapper shell, now lives in
  `scripts/lib/claude_sessions.py` and feeds the bar's Claude-runs pill.
