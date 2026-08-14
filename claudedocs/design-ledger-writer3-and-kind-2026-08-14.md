# Design pass: ledger writer 3 (clawgate) + the spec §4 `kind` field

**Date:** 2026-08-14 · **Status:** design, no code written · **Decides:** spec §4, and
whether writer 3 is built at all · **Spec:** `claudedocs/spec-agent-activity-ledger.md`

Everything below marked *measured* was read live today. Everything else is a claim to check.

---

## 0. What changed since the handoff

`claudedocs/handoff-agent-attention-tooling.md` is stale in three ways — writer 2 shipped
(#478), and #475/#476/#477 landed after it was written. Two of its open investigations
moved today, both from one live read.

---

## 1. 🔴 The stuck detector fired on a real wedge — first time

Measured 2026-08-14 against the live board (`GET /api/tasks?summary=1`, 29 tasks):

```
stuck_count: 2   (previously 0 on every check — a measured zero, never a firing)
  #194 devrc: fix the 60s-timeout flake …   reasons: ["no_agent"]  dispatch_age 86,119s (23.9h)
  #193 devrc: consolidate the five worktree bullets …  reasons: ["no_agent"]  dispatch_age 86,119s
board: open 11 · in_progress 2 · ready_for_review 8 · complete 8
```

The detector works on real data, not only on its 5 fixtures and 37 mutants. Both tasks have
sat in `in_progress` for 23.9h with **no `updatedAt` movement**, and `GET /api/agents`
contains no agent for either.

### 🔴 But the handoff's discriminator does NOT hold, and this is the correction

The handoff said: *"on the next genuinely wedged dispatch, read the reported `reasons`. If it
is `no_agent` rather than `not_kicked_off`, that confirms the link theory."*

It is `no_agent` — and **that confirms nothing.** `no_agent` fires when `task.agent` is null,
and two mechanisms produce a null there:

| mechanism | would this board look different? |
|---|---|
| **(a)** the task↔agent link is broken upstream (#316) — an agent exists, unlinked | no |
| **(b)** no agent was ever created — the task was moved to `in_progress` without a dispatch | no |

`no_agent` is an **absence**, and an absence cannot separate two causes that both produce it —
the same trap that made me file and retract the "never populated" claim on #316.

**The evidence available today favours (b), the rival mechanism**, and therefore *weakens* the
link theory rather than confirming it:
- `/api/agents` returns **2 agents total**, ids 10 and 40, created 2026-06-06 and 2026-07-30 —
  both **predate** tasks 193/194 (created 2026-08-13T20:08). There is no unlinked candidate for
  these tasks to be linked *to*.
- Both tasks flipped to `in_progress` at `20:12:22.03` and `20:12:22.04` — **10 ms apart**,
  4 minutes after creation. That is the shape of a batch status write, not two dispatches.

⚠ Stated at the scope measured: this is one board at one instant. It does not prove the link is
healthy, and it does not prove no dispatch was attempted (an agent record could have been torn
down — the `clawgate` skill notes two task-deletion paths tear down the agent pod, though the
tasks still exist here).

**The upstream signal that WOULD separate them** — and the only thing worth spending a probe on:
the agent-pod logs for ns `devpod-<name>`, or clawgate's dispatch-side log for the
`20:08–20:12` window. If no `POST /agents` was issued for 193/194, it is (b) and the stuck
detector is correctly reporting *"claims to be in progress, nothing is working on it"* — which
is the operator-relevant fact either way.

**Actionable now, independent of the diagnosis:** two devrc tasks have been falsely `in_progress`
for a day. They need re-dispatching or closing.

---

## 2. 🔴 The spec §4 premise does not survive the live data

§4 opens: *"its primary entity **stops being a tmux window and becomes an agent run**."*

Measured, clawgate has **no agent-run entity**:

| | |
|---|---|
| `/api/agents` population | **2**, both `status: "error"`, both `noteId: null` |
| id 10 `operator` | created **2026-06-06**, still present — a long-lived worker, not a run |
| id 40 `brave-heron` | created 2026-07-30, `kickedOff: false`, dead since |
| `lastActivityAt` | present on both (the field §3 relies on) — but on a *pod*, not a run |

A clawgate **agent** is a persistent devpod namespace. The thing that is ephemeral, that starts
and finishes, that maps to "an agent run" — is the **task in `in_progress`**, i.e. the dispatch.
`scripts/lib/clawgate_tasks.py` already models exactly that: `dispatch_age_secs`,
`agent_idle_secs(task, …)`, `stuck_reasons(task, …)` all take a **task**.

**So §3's "Writer 3 — clawgate agents … `session-manager` fetches `/api/agents`" is aimed at the
wrong entity.** Building it would put 2 permanently-errored pods in the table and leave the 2
actual in-flight dispatches out.

### The correction

If cluster rows are built at all, their source is **`/api/tasks` filtered to `in_progress`**,
enriched by the embedded `agent` object for liveness — which is the read `clawgate_tasks.py`
already performs and which just fired correctly. Writer 3 collapses from *"a new fetch and a new
entity"* into *"promote the existing clawgate dispatch read into rows"*.

**Non-overlap with `blocked_on_me`, stated so it cannot drift:** `blocked_on_me` counts tasks
**waiting on the operator** (`open` + `ready_for_review`); cluster rows would carry tasks
**in flight** (`in_progress`). Disjoint by status, no double count. Without that line written
down, clawgate lands in the report three times.

---

## 3. 🔴 What §4's "one table, add a `kind` field" leaves undecided

The decision is right in spirit and **under-specified**: it names a row field but not the
container. Rows do not live in a table. They live at:

```
report["hosts"][<host>]["windows"][]        # summarize(): rows = [r for h in hosts.values() for r in h["windows"]]
```

A clawgate dispatch has no host, no session, no window index. Three ways to place it:

| option | cost |
|---|---|
| **A. synthetic host** `hosts["clawgate"]` | free pickup by every consumer — and it poisons `hosts_reachable`/`hosts_unreachable`/`windows_unmeasured`/`local_host`, all of which mean *"an SSH target answered"* |
| **B. sibling collection** `report["agents"]` | §4 rejects it, correctly: two shapes every consumer must merge by hand |
| **C. `kind` on rows + every roll-up split by `kind`** | **recommended** — the precedent is already in this file |

**C is what `summarize` already does for `claude`/`shell`.** That split exists because a mixed
integer was published and read as an agent count (measured: `idle: 17` = 12 agents + 5 shells).
A mixed tmux+cluster integer is the identical defect one axis over. The container stays
`hosts`, gaining one **explicitly non-SSH** entry whose provenance keys say so.

### Three concrete defects a naive `kind` would ship

1. 🔴 **`claude` silently buckets cluster rows as `shell`.**
   `summarize` does `bucket["claude" if r.get("claude") else "shell"] += 1`. `claude` is defined
   as `pane_current_command =~ /claude/`; a cluster row has no pane, so it is null, so **every
   cluster agent is counted as a bare shell.** This is the conflation the split was built to
   kill, reintroduced through the back door. The claude/shell split must become
   `kind`-scoped — or `claude` becomes tri-state with the roll-up reading it as such.

2. 🔴 **`classify_status` erases the state that matters.** It returns busy/idle/stale/unknown from
   `(busy, age)`, and `stale` wins over everything. Both live agents are `status: "error"` —
   mapping that through a glyph-and-age classifier renders a permanently-broken dispatch as
   `stale`, i.e. as "an agent that went quiet". `error` must survive as its own bucket or ride
   in a dedicated field.

3. 🟡 **`waiting_probable` needs a fourth `waiting_status`.** There is no pane to capture, so the
   value is `None` with a reason — `not_tmux`, enumerated alongside
   `not_claude`/`uncaptured`/`skipped`. Absent that, `_waiting_rollup`'s measured-vs-unmeasured
   accounting quietly counts cluster rows as *looked at and found fine*.

### `kind` vs the existing `runtime` — different axes, and say so

Rows already carry `runtime` (`claude` | `opencode` | null, from the ledger record). They are
**not** derivable from each other and both are needed:

- `kind` — where the row came from (a tmux pane scan vs the clawgate API). Known with certainty
  at row construction. **Never null.**
- `runtime` — which agent software, from the ledger record. **Null when no writer has recorded
  that window** — the common, meaningful case.

Two discriminants that agree ~90% of the time is how one gets derived from the other six months
later, and the null semantics is where that breaks.

---

## 4. 🔴 Does writer 3 earn its cost? — the gate before the code

Per the standing "do we need it" check, before a second round of building on this surface:

**What it buys, measured:** rows for **2** long-lived pods, both errored, neither doing work —
or, under the §2 correction, rows for **2** in-flight dispatches.

**What it costs:** a new network read in a tool that must work with the homelab down; a `kind`
axis through `fold_windows`, `summarize`, `_waiting_rollup`, `classify_status`, the table, the
lean view and `LEAN_ROW_FIELDS`; and re-deciding the claude/shell split that 18 tests assert.

**What already covers the same ground:** `blocked_on_me` (tasks needing the operator, with the
count/discriminant contract) **plus** the stuck detector, which today reported the 2 wedged
dispatches by id, title, reason and age — the exact rows writer 3 would add, in a surface that
already exists and just proved itself on live data.

**Recommendation: split the work, and do not build writer 3 now.**

1. **Land `kind` now, on tmux rows only** (`kind: "tmux"` on every row, never null; roll-ups
   split by it). This settles the entity model, is testable with no network, and makes the three
   defects above impossible to ship later by accident. It is the part agent-ops retirement and
   the §5 bar inversion actually need.
2. **Promote the stuck rows into the report** as the cheap 80% — `summary.blocked_on_me` already
   carries `stuck_count`; surfacing the stuck *rows* costs no new fetch.
3. **Gate writer 3** on a stated trigger: the `in_progress` population being routinely non-zero
   **and** `task.agent` non-null (i.e. #316 resolved). Both are false today. Re-check by reading
   `stuck_count` and `agent non-null` off one `/api/tasks` call.

### On agent-ops retirement — it does **not** gate on this

Re-read of §7 against the code: every disposition routes to `session-manager` / `standup` /
`/initiative-scan`, and none of them needs cluster rows. The one 🔴 KEEP is the **`/proc`
detector** (`scripts/i3status-agent-ops` depends on it; it is strictly more accurate than
`pane_current_command =~ /claude/`, which is why 15 windows render `? unk`). **That** is the
real gate, and it is independent of writer 3 and of `kind`. Retirement was blocked on a decision
it never needed.

---

## 5. If `kind` is built — the shape

```python
# On every row, never null. The claude/shell split becomes kind-scoped.
"kind": "tmux" | "cluster"
```

- `KINDS` a module constant, and the roll-up derived from it, so a new kind cannot exist in
  `fold_windows` and be missing from `summarize` — the rule `WAITING_SIGNALS` and
  `STUCK_REASONS` already follow here.
- `LEAN_ROW_FIELDS` gains `kind`. It is a discriminant, not duplication: the lean view's own
  rule keeps provenance and drops human-facing identity.
- `CAVEATS` gains an entry, or `ledger_scope` is amended. 🔴 A caveat is a machine-readable
  claim — `fuzzyclaw_scope` went stale the moment the ledger shipped, and the guard that should
  have caught it was blinded by a fixture default (`base_gather` is `use_ledger=False`). Any
  `kind` fixture default must not blind that test the same way.
- Verification: `kind` is 100% `tmux` until cluster rows exist, so a test asserting the split is
  **vacuous by construction** — it must be driven from a constructed `cluster` row, and watched
  to fail. A positive control reporting the pair ("N cluster rows on the control, 0 live") is
  the only honest way to publish a zero here.

## 6. Open / to verify

- Which mechanism wedged 193/194 — see §1. Needs a dispatch-side log, not another board read.
- Whether a clawgate agent record is ever deleted while its task survives. If yes, `no_agent`
  gains a third mechanism and the reason string is doing more work than its name admits.
- `/api/tasks?summary=1` carries `agent` on 0/29 today, including 0/2 `in_progress`. The
  handoff records another observer seeing 1/3 populated. Unresolved; #316 stays *intermittent*.
