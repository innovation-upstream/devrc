# The fuzzyclaw intersection guard, and the slot-conflict archaeology

Loaded only when someone touches the task-file join. `--fuzzyclaw` is **opt-in** since
2026-08-12 (29 live rows of 401 files, all reading `paused`, zero contribution to any
answer) — but the guard below still runs whenever it is turned on, and its tests still gate
every commit.

## The guard pins a RELATIONSHIP, not two independent facts

`CLAUDE.md` marks `~/.tmux/tasks/*.json` UNTRUSTED. Measured on the workbench 2026-08-11:
**400 files, 44 live windows, 357 stale (89%), 11 slot-mismatched, 32 live, 0 unparseable.**
The failure mode is staleness, not corruption — so it is filterable.

A task file survives only when **both** hold:

```
window_id is live   AND   that live window's real (session, index) == the file's
```

measured against `tmux list-windows -a -F '#{window_id}|#{window_index}|#{session_name}'`.

🔴 **Existence alone is not enough, and checking only existence was a real defect here.** An
earlier revision keyed the guard on `window_id` but joined the task onto a pane row by
`(tmux_session, window_index)` — two independent facts, never checked against each other, and
that split was documented as "deliberate". `renumber-windows` is `on`, so indexes shift under
live windows all day: of the 43 files that passed the id-only guard, only **32** still sat in
the slot they recorded, **7** named a slot now held by a *different* live window, and **5**
slots were claimed by more than one survivor (silently last-wins). Two rendered rows carried
another window's `claude_session_id` — the one carrier of the session id into ClickHouse — so
a `detail` would have pulled a stranger's prompt history.

## Consequences, all pinned by tests

- Rejections are counted **separately**: `files_stale` (the window is gone) vs
  `files_mismatched` (alive, but somewhere else now). Collapsing them hides a renumber storm.
- A slot two files both claim resolves to **nothing**. `index_tasks_by_window()` drops it and
  reports it under `fuzzyclaw.slot_conflicts`; attaching an arbitrary one of two
  contradictory records is worse than attaching none, because it reads as measured data.
- Every row carries `window_id`, so the join is auditable in the output:
  `row.window_id == row.fuzzyclaw.window_id` for every joined row.
- `filter_live_tasks()` **rejects a bare set of ids with a `TypeError`** rather than
  degrading to the old existence-only check.
- The consumed key set is a **field ledger** (`FUZZYCLAW_FIELDS`, 11 keys including
  `transcript_path`, which the original spec omitted), asserted to fail when the set grows
  *or* shrinks.

## 🔴 What the slot-conflict drop still catches — narrower than the story above, but not empty

- **GUARANTEED GONE:** two *distinct* live window ids contending for one slot. A slot belongs
  to exactly one window, so `list-windows` cannot report it. That was the shape produced by
  the *old id-only* guard, and it is the shape the 5 contested slots had.
- **STILL REACHABLE:** two task files carrying the **same** `window_id`. The files are
  `<index>.json`, not `<window_id>.json`, so nothing on disk enforces one file per window,
  and the directory is UNTRUSTED. Both duplicates pass the relationship guard (each resolves
  to the one slot their shared id really holds), collide at the index, and the conflict is
  dropped and rendered. A test drives that case end-to-end through `gather()`.

`claimants` counts **files** while `window_ids` is deduplicated, so `claimants: 2,
window_ids: ["@41"]` is exactly the duplicate-files case — not contention — and the rendered
line prints both numbers so it cannot be misread. The 400-files/400-distinct-ids reading of
2026-08-11 is a snapshot of an untrusted writer, not an invariant; do not read it forward.

## What the `row.window_id == row.fuzzyclaw.window_id` invariant does NOT buy

It holds **by construction, not by luck** — both sides derive from the same `list-windows`
snapshot — so it is documentation of the join, **not a defence**, and re-checking it at
runtime would be an unreachable guard. It does not cover the one skew that is genuinely
reachable: `list-panes` and `list-windows` are two non-atomic calls, so with
`renumber-windows on` a window can move between them and a row can pair one snapshot's pane
data with the other's window id. The pane format carries no `window_id`, so nothing catches
that. **Unguarded, and named rather than implied away.**

## The third zero: `fuzzyclaw.status`

The live-window set is **measured or `None`**, never a fabricated empty set. When it was not
measured, `status` is `"unmeasured"` and `files_live` is `null` — *not* `0`, and never
`"ok"`. Two ways to get there, both of which used to report
`files_seen: 400, files_live: 0, status: "ok"`:

| cause | what you see |
|---|---|
| `--host laptop` — the local host is never scanned, so its windows are never listed | `fuzzyclaw.error` names the unscanned local host |
| `list-panes` succeeded but `list-windows` failed | `hosts.<n>.windows_measured: false` + `windows_error`; `live_window_ids: null` |

Under `--no-fuzzyclaw` (now the default) **every one of those counts is `null`, not `0`** —
the directory is never opened, so a `0` would be a fabricated measurement. `status:
"skipped"` discriminates it, but a discriminated lie is still a lie in the count.

Same precedent and rationale as `scripts/tmux-scratch-status.sh:28-34`. If you add a second
consumer of these files, intersect there too — do not copy the fields out raw.

## The mixed-count split (2026-08-11), for context

The headline question is *"is anything waiting on me"*, and `idle` answered it with agents
and bare shells added together: 61 windows gave `idle: 17` = **12 agent windows + 5 bare
shells**, both rendered `● idle`. The row data was always right (every row carries `claude`);
the roll-up and the table conflated. The flat `summary["idle"|"busy"|"stale"|"unknown"]`
integers are **gone** — a deliberate break in the loud direction, since keeping them would
have left every reader on the mixed number while the fix sat in keys they never learned
about. `summary["status"]["idle"]["total"]` is the same number and you have to type `total`
to get it. All four buckets split, not just `idle`.
