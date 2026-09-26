# SCOPE — token-efficient situational awareness for clawgate's chief agent

**Status: SCOPE ONLY.** No production code written, no PR opened, no clawgate task
created, no privilege changed, nothing deployed. Everything below is a proposal plus
the measurements that justify it.

- **Date:** 2026-09-19
- **Repos:** `ZacxDev/homelab-infra` (`/home/zach/workspace/homelab-talos`), `containers/clawgate` — read at `trunk` = `15744e013`. Plus `devrc` (`/home/zach/workspace/devrc`) where noted.
- **Live server at time of the brief's measurements:** clawgate 0.8.45
- **Trigger:** operator tapped the "Recap the fleet" quick action in chief's chat (agent 71 `zesty-stoat`, session 57). Chief answered that it had *"no visibility into any cluster"* and offered to request a `k8s-read` privilege bundle. That answer was wrong about chief's own capabilities.

---

## 0. TL;DR — what changed in my understanding of the problem

Five findings reframe the brief. Each is sourced below.

1. 🔴 **There is no recap read path, and there is no recap STORE either.** Recaps live in one
   Go process's `map[string]entry` (`internal/recap/recap.go:229`), have no migration, no
   table, no route, and are **produced only as a side effect of a human loading the tmux
   panel** — `attachRecaps` has exactly one caller, `handleTmuxPanel`
   (`internal/api/tmux_ui.go:209`), behind `requireSession`. No panel load ⇒ no recaps
   exist at all. This is a finding, not a gap to paper over: the "read path" work item is
   *give recaps a producer and a store*, not *add a route over an existing one*.

2. 🔴 **The recap generator IS chief** (`chiefRecapGenerator.Generate` →
   `Provisioner.Chat(chief, …)`, `internal/api/recap_wiring.go:113-131`). Any read verb that
   can trigger generation is re-entrant: chief's turn → `clawgatectl` → server → chief's
   own gateway. The read verb must be **cache-only, never generating**.

3. 🔴 **The brief's root cause is real but it is the SECOND of two missing chief branches,
   and the other one is the live path.** The operator's chat with chief goes through
   `chatTurn` (`internal/api/agents.go:1472-1491`), which branches
   `if a.Name == agents.OperatorName { … } else { worker }` — so chief received the WORKER
   system prompt (`agentSystemPrompt`, `internal/api/agent.go:204`: *"You are a clawgate
   worker agent … call agent_get_task …"*) and the WORKER tool set, whose
   `agent_request_privilege` is described as *"Request elevated access you don't have (e.g.
   Kubernetes)."* That is verbatim the answer chief gave. `skillsFor`/`instructionsFor`
   (`internal/agents/skill.go:135,165`) have the same missing branch, but they are rendered
   at **provision time**; `AgentInstructions`/`AgentToolDefs` are computed **per turn**.
   The per-turn seam is the cheaper fix and the one that explains the symptom.

4. 🔴 **`transcript query` — the centrepiece the brief builds item 1 on — is NOT reachable
   from chief's pod.** `GET /api/transcripts/{sessionId}/events` is registered on
   `requireHookToken` (`internal/api/transcripts.go:150`), deliberately, with a comment
   refusing the adjacency argument. Only the *verbatim tail* route is on the chief tier. So
   **item 1 requires a second tier move that the brief's item 2 does not name**, and it
   needs its own argument (§5.1 gives one: `/events` is a strict subset of what `get`
   already discloses).

5. 🔴 **The largest available win needs no route, no tier and no server deploy.** Measured
   on a live snapshot: `pane_preview` is **62.3%** of the `tmux windows` payload and
   `ledger` a further 7.5%. A client-side `--fields` projection in `clawgatectl` takes the
   same read from **250,284 B → 23,492 B** (claude windows, 13 fields, as the CLI actually
   prints it) — a **10.7× cut**, entirely inside `cmd/clawgatectl`. And `emit` indents
   unconditionally (`cmd/clawgatectl/root.go:56`), which costs +13% on the full dump and
   **+34%** once projected; there is no `--compact`.

---

## 1. Measured facts — verification results

The brief said to verify only what I intend to contradict. I re-derived two, and found
three stale claims in the repo's own prose.

### 1.1 Reproduced independently

| Claim | Result |
|---|---|
| A full `tmux windows` read is 225,526 B | **CONFIRMED.** Collected a live local snapshot (`scripts/session-manager --json --pane-preview`, both hosts, 82 windows) and re-serialised it in the CLI's own envelope: 220,839 B compact / **250,284 B as emitted (indented)**. The brief's 225,526 B sits between the two, consistent with a snapshot taken minutes apart. |
| `--grep`/`--host` filter client-side only | **CONFIRMED** (`cmd/clawgatectl/tmux.go:95-96`, `112-150`) — **but see §1.3, the framing needs a correction.** |

### 1.2 Things I found to be WRONG (or materially incomplete)

**(a) `transcript query` is presented as available to chief. It is not.**
The brief lists it under MEASURED FACTS as an existing capability without naming its tier.
`internal/api/transcripts.go:149-150`:

```go
mux.HandleFunc("GET /api/transcripts/{sessionId}",        s.requireHookOrAgentToken(s.handleTranscriptGet))
mux.HandleFunc("GET /api/transcripts/{sessionId}/events", s.requireHookToken(s.handleTranscriptEvents))
```

…with the comment at `:145-148` stating the exclusion is deliberate. `cmd/clawgatectl/agent_tier_ledger_test.go:74-80` records the same. So today chief can read a transcript
**only as up to 1 MiB of raw JSONL**, and the parsed/filtered path it should be using is
401 for it. `transcript ls` (`GET /api/transcripts/digest`, `transcripts.go:82`) is also
`requireHookToken` — chief cannot enumerate stored transcripts either; its discovery path
is `claude_session_id` off `tmux windows`.

**(b) The chief tier is ARMED in production. Repo prose says it is not, in at least three places.**
The brief's own negative control proves it: `tmux windows` → **200 from chief** and **403
from `devpod-witty-heron`**. Read against `requireHookOrAgentToken`
(`internal/api/auth.go:521-604`), that pair is only satisfiable if (i) the hook token is
enforced — otherwise the non-chief pod would also get 200 — and (ii) `ChiefWriteRefusal()`
is empty and chief's row token equals `s.auth.ChiefToken` — otherwise chief would have got
403 too. Therefore `CLAWGATE_CHIEF_TOKEN` **is** set to chief's row token. Stale prose:

- `internal/api/auth.go:477` — *"UNARMED, THE AGENT HALF ADMITS NOBODY. CLAWGATE_CHIEF_TOKEN is unset in production"*
- `internal/api/transcripts.go:142` — *"unarmed — production today — the agent half admits nobody"*
- `cmd/clawgatectl/agent_tier_ledger_test.go:146` — *"🔴 Unarmed in production today."*
- `chief_capability_reach_test.go:622` — *"with the door unarmed, which is production…"*

The same file that carries the third of those already records the correction at
`:108-116` (*"the door had been ARMED the previous day"*, measured 2026-09-18) — so the
repo contradicts itself inside one file. **Consequence for item 2: a re-tier of
`GET /api/attention` takes effect the moment it deploys.** It is not a change that sits
inert until somebody arms a door.

**(c) `cmd/clawgatectl/tmux.go:12-16` describes `/api/tmux/snapshot` as "a requireHookToken route".** Task 607 moved it to `requireHookOrAgentToken`. Stale comment; one-line fix, worth folding into whichever PR touches that file.

### 1.3 A correction to the brief's framing of the token constraint

> "`--grep`/`--host` filter **client-side only** — there is no server-side query for windows."

True as a statement about the route. But **client-side filtering costs the agent nothing**:
the agent pays for the CLI's *stdout*, not the wire. `clawgatectl tmux windows --grep foo`
is already cheap in tokens today. The real gap is different, and sharper:

- `--grep`/`--host` filter **ROWS**. There is **no field projection** — no `--fields`. A
  row-filtered read still carries every window's full `pane_preview` (up to 16 KiB each;
  `devrc scripts/session-manager:1083 MAX_PANE_PREVIEW_BYTES = 16 * 1024`).
- The chicken-and-egg is that you cannot grep for what you have not yet seen, which is
  precisely the job a recap is meant to do. So a cheap *whole-fleet* read is still needed —
  it just needs to be narrow in COLUMNS, not in rows.
- `emit` indents unconditionally and there is no `--compact`.

This changes the first PR (§6).

---

## 2. Measurements taken for this scope

All read-only. Two independent measurement sets.

### 2.1 The tmux snapshot, by field (live, both hosts, 82 windows)

Source: `scripts/session-manager --json --pane-preview` on the workbench, 2026-09-19.

| field | bytes across all rows | share |
|---|---:|---:|
| `pane_preview` | 141,623 | **62.3%** |
| `ledger` | 17,068 | 7.5% |
| `path` | 3,987 | 1.8% |
| `claude_session_id` | 3,828 | 1.7% |
| `window_name` | 2,989 | 1.3% |
| `waiting_signals` | 2,861 | 1.3% |
| `task` | 2,858 | 1.3% |
| all others (≈30 fields) | ≈52,000 | ≈23% |

Composition: 82 windows; 52 flagged `claude`; 48 carry a `claude_session_id`;
9 `waiting_probable == true`.

**As the CLI would actually print it** (its `{"windows":…,"count":…}` envelope, `json.Indent`):

| read | compact | **as emitted (indent 2)** | ≈tokens | vs full |
|---|---:|---:|---:|---:|
| `tmux windows` (today) | 220,839 | **250,284** | 62.6k | 1× |
| `--fields <13>` all windows | 26,043 | **35,415** | 8.9k | **7.1×** |
| `--fields <13> --claude` | 17,540 | **23,492** | 5.9k | **10.7×** |
| `--fields <13> --waiting` (9 rows) | 3,042 | **4,092** | 1.0k | **61×** |
| `--fields <6>` all windows | 13,002 | **17,782** | 4.4k | 14.1× |

The 13-field set used: `host, session, window_index, window_name, label, repo, status,
claude, claude_session_id, waiting_probable, waiting_status, age_secs, task`.

⚠ Indentation overhead is **1.13×** on the full dump but **1.34–1.37×** once projected —
structural whitespace dominates when values are short. A `--compact` flag is worth ~25% of
every projected read.

### 2.2 Transcript event sizes (40 most-recently-modified sessions, `~/.claude/projects`)

Method: read the last `transcript.MaxTailBytes` (1 MiB) of each file, apply
`internal/transcript`'s parse rules (meta dropped, `tool_result` not an event, thinking kept
as its own kind, per-event clamp 8000, tool-input clamp 4000, `maxEvents=400` newest-wins),
then size the `transcriptEventDTO` JSON. **These are slight OVER-estimates**: my DTO emits
every field where the real one has `omitempty`.

| shape | p50 | p90 | max | ≈p50 tokens |
|---|---:|---:|---:|---:|
| stored tail (`transcript get`, what chief can reach today) | 1,048,576 | 1,048,576 | 1,048,576 | **262k** |
| full parsed conversation | 131,306 | 185,762 | 240,741 | 33k |
| `query --limit 100` (today's DEFAULT) | 90,734 | 138,155 | 171,172 | **23k** |
| `--kind user`, full text | 9,098 | 40,303 | 50,513 | 2.3k |
| `--kind assistant`, full text | 30,088 | 44,548 | 61,124 | 7.5k |
| **anchored: last user turn + 3 before/3 after** | **6,128** | 16,782 | 25,944 | **1.5k** |
| **anchored: last user turn + next 1 event** | **691** | 8,418 | 12,851 | **0.17k** |
| **the next assistant message after the last user turn, alone** | **270** | 2,275 | 4,475 | **0.07k** |
| **index of every user turn (role + ts + 160-char preview)** | **1,194** | 2,176 | 7,046 | **0.30k** |
| events per session (after the 400 cap) | 131 | 165 | 195 | — |
| **user turns per session** | **6** | 12 | 32 | — |

Two things fall out:

- **`transcript get` is worse than the thing being designed away.** The one transcript read
  chief can reach is ~262k tokens at p50 — 4.7× the `tmux windows` dump. Chief has not been
  using it, which is consistent with it never having been told it exists.
- **Today's default `query --limit 100` is 23k tokens.** Even the parsed path is not
  token-efficient at its default. Anchored selection is 15–90× cheaper than it.
- Response envelope floor is ~400–600 B of metadata (`truncated`, `leadingPartial`,
  `trailingPartial`, `skipped`, `omittedBefore`, …) regardless of event count.

---

## 3. The recap read path — the finding

**There is no read path, and there is no stored recap.** Enumerated:

| what | where | state |
|---|---|---|
| storage | `recap.Service.cache map[string]entry` | **process memory only** |
| migration / table | — | **none** (`grep -ril recap internal/db/` → empty) |
| HTTP route | — | **none** (`routes.golden` has no recap route) |
| producer | `attachRecaps` | **one caller**: `handleTmuxPanel`, `internal/api/tmux_ui.go:209`, behind `requireSession` |
| render | `tmuxRecapTextAttr = "data-tmux-recap-text"` | `internal/ui/tmux.go:3607,3649` — an HTML attribute |
| eviction | `Service.Forget(live)` | pruned to the windows of the **last render** |
| key | `ui.TmuxCardKey(host, win)` = `host\|session\|index` | not a session id |
| read-without-generating accessor | **deliberately deleted** — `recap.go:391-396`: *"THERE IS DELIBERATELY NO `Peek` … it had no production caller"* | — |

Four consequences for a chief-facing recap read:

1. **It is not durable.** Every server restart empties it. `internal/recap` is explicitly
   an in-memory policy layer; nothing was ever meant to survive.
2. **It exists only where a human has recently looked.** The producer is an HTML page
   handler. A chief asking for fleet recaps at 03:00 with no browser open reads
   `StatePending` for all 82 windows, correctly, and has learned nothing.
3. **A read that triggers generation is re-entrant into chief.** `chiefRecapGenerator`
   calls `Provisioner.Chat` on the chief agent (a different gateway session key —
   `recapSessionName(a.Name)` = `agent:<name>:webchat:recap` — but the same pod and the same
   model connection, under `recapMaxInFlight = 4`, `recapTimeout = 90s`). A `recap ls` that
   submitted 82 requests from inside a chief turn would queue chief behind itself.
4. **Re-adding a read accessor reverses a documented deletion.** `Peek` was removed because
   it had no production caller. This work creates one. That is a legitimate reversal, but it
   must be argued in the commit rather than quietly re-added — the deletion note is the
   review it has to answer.

### The honest options (this is a FORK for the operator, §7)

| option | cost | what chief actually gets |
|---|---|---|
| **R1. Cache-only read, no generation.** Add `Service.Snapshot() []Recap` (never schedules) + `GET /api/tmux/recaps` + `clawgatectl tmux recaps`. | small; ~1 day | Recaps **only for windows the operator recently viewed**. Honest, cheap, and structurally unable to loop. Must report `state` per row so chief can say "no recap yet" instead of inventing one. |
| **R2. R1 + a background producer.** A ticker that calls `attachRecaps`-equivalent on a schedule, independent of any page load. | medium; new loop, new cadence constant, interacts with `recapMaxInFlight` | Fleet-wide recaps that exist whether or not anyone is looking. **Spends chief turns on a timer** — that is a standing inference bill nobody has priced. |
| **R3. R1 + persistence.** A `tmux_recaps` table keyed on the card key + revision. | medium-large; migration, retention (the argument in `transcript.go:248` applies — card keys churn) | Recaps survive restart. Only worth it on top of R2. |
| **R4. Do not read recaps. Give chief anchored transcript queries and let it summarise itself.** | zero recap work | Chief already IS the summariser. An anchored read (p50 **270–6,128 B**) is cheaper than the 6,000-char material `recapPrompt` feeds the generator, and chief gets the source rather than a two-sentence lossy summary of it. |

**Recommendation: R4 as the primary, R1 as a cheap complement.** R4 is what the operator's
own framing already describes — *"read session recaps … + my input messages, can tail/query
full transcript using token-efficient positional queries"*. The positional queries are the
part that carries the information; the recaps are the index. R1 supplies the index where it
happens to exist, at near-zero cost and with no loop risk. R2/R3 should be a separate
decision, after R4 has shown what chief actually needs.

⚠ **R4 does not make the "recap" half of the operator's sentence deliverable as written.**
The operator asked to read the recaps *"we already generate and display"*. They are
generated only when displayed, and are not stored. That must be said plainly before
anything is built.

---

## 4. "My input messages" — what it resolves to

The operator's words: *"read session recaps … + my input messages"*. Concretely, and
distinguished from chat:

- **Not `agent messages`.** `GET /api/agents/{name}/messages` is the operator's *chat with
  an agent*, and it is out of bounds (`requireHookToken` since it was measured to let any
  Running pod read every agent's conversation on a route keyed by an arbitrary `{name}`
  with no self-scoping — `agent_tier_ledger_test.go:108-116`). **Not re-widened by anything
  in this scope.**
- **It is the operator's `user`-role turns in tmux-session transcripts.** The store is
  `internal/transcript` (`transcript_tails`, keyed on Claude Code session id). The parser
  already isolates exactly this: `KindUser` is produced only from a `text` block (or the
  string spelling of `content`) on a `role: user` record that is **not** `isMeta`, and
  `tool_result` blocks are counted separately — `parse.go:502-507`: *"COUNTED AS ITS OWN
  THING, NOT AS `user` … the one classification error that would put a tool's output in the
  operator's own voice."* So `kind == "user"` **is** the operator's typed input, already,
  with the 91%-of-user-records tool-result case excluded.
- **Discovery of the session set:** `tmux windows` → `claude_session_id`. Measured: 48 of 82
  windows carry one. `transcript ls` would be the natural enumerator and is **not reachable**
  (§1.2a) — but it is also not needed, because the tmux read already carries the ids and is
  the read chief has to do anyway.
- **Cost:** p50 6 user turns per session. Full text of all of them: p50 **9,098 B** (p90
  40,303). A 160-char-preview index of all of them: p50 **1,194 B** (p90 2,176). For the 48
  claude windows: full ≈ 437 KB (too much), preview index ≈ 57 KB (still too much for a
  fleet sweep). **So "my input messages across the fleet" is only affordable scoped** — by
  host, by repo, by `waiting_probable`, or to the N most recently active sessions. That
  scoping belongs in the SKILL's guidance (item 3), not in a new verb.

---

## 5. Work items

Dependencies: **W1 → (none)**. **W2 → W1** (W1 gives it the projection idiom).
**W3 (item 2) → none.** **W4 (item 1) → W5**. **W5 → none** but is a privilege decision.
**W6 (item 3) → W1, W3, W4** for content, but can ship documenting only what is live.
**W7 (item 4) → none**; ideally lands early so it can witness the others.

---

### W1 — `--fields` projection and `--compact` on the CLI's read verbs
**Repo:** homelab-infra · **Files:** `cmd/clawgatectl/tmux.go`, `cmd/clawgatectl/root.go`
(`emit`) · **Routes:** none · **Tier:** none · **Deps:** none

Add to `tmux windows`: `--fields host,session,window_name,status,claude_session_id,…`
(comma list, projecting each window row; unknown field names **refused at exit 2** with the
observed field set named, never silently dropped — an unknown name that yielded an empty
column is a typo rendering as data, the same argument `transcript query` already makes for
`--kind`). Add a global `--compact` that makes `emit` marshal without `json.Indent`.

**Why this first:** it is the whole measured win (10.7× on the read chief actually needs),
it touches no route, no auth tier, no server binary, and it is revertible on its own.

**Budget:** 250,284 B → 23,492 B emitted (`--fields <13> --claude`), → 17,540 B with
`--compact`. ~62.6k → ~4.4k tokens.

⚠ **Constraint:** `internal/tmux` treats `windows` as an opaque `json.RawMessage`
(`tmux.go:180`) — the per-window schema is the devrc collector's. The projection must
therefore be **name-based over `map[string]any`, tolerant of absent keys**, and must NOT
grow a hardcoded list of "the real fields"; `tmuxWindowMatches` already takes exactly this
position (`tmux.go:194-200`: *"EVERY STRING FIELD, NOT A NAMED LIST"*). A row missing a
requested field must be emitted with that key absent, not dropped.

---

### W2 — a `--fields`-style projection is NOT proposed for `transcript query`
**Deliberately out of scope.** `transcriptEventDTO` is already a 6-field DTO and the bytes
are in `text`/`toolInput`, which are the content. What bounds those is `--max-text` /
`--max-tool-input` truncation flags — folded into W4 rather than treated as projection.

---

### W3 — re-tier `GET /api/attention` to the chief-scoped armed-only tier *(brief item 2)*
**Repo:** homelab-infra · **Deps:** none

**Exact route list. ONE route moves:**

| route | from | to | file:line |
|---|---|---|---|
| `GET /api/attention` | `requireHookToken` | `requireHookOrAgentToken` | `internal/api/attention.go:60` |

Nothing else. For reference, already on the target tier and **not touched**:
`POST /api/attention` (`:59`), `POST /api/attention/{id}/resolve` (`:61`).

**Which of the two shapes this implements — stated explicitly, because an earlier draft of
task 607 got it wrong and was refused:** this is the **chief-scoped armed-only** tier, NOT
agent-wide. Confirmed from the code, not from the ledger:
`requireHookOrAgentToken` (`internal/api/auth.go:521-604`) resolves the bearer to a row
(`GetByHooksToken`, `:544`) and then narrows it in four steps — `termwrite.ValidActor`
(`:553`), `ChiefWriteRefusal()` (`:566`), `ConstantTimeEqual(token, s.auth.ChiefToken)`
(`:578` — *"deleting this line hands the tier to the fleet"*), `ChiefActorRefusal(a.Name)`
(`:590`). All four answer **403**, not 401, because the credential is valid and simply is not
chief's. The live negative control (`devpod-witty-heron` → 403) is that branch firing. So:
**exactly one agent, chosen by the server's `CLAWGATE_CHIEF_TOKEN`, not by provisioning.**

**What it grants, stated at full width:** chief reads the operator's ENTIRE attention queue
— `Kind`, `Priority`, `Title`, **`Body`** (full question text), `Options`, `Host`,
`Project`, `SessionID`, `Cwd`, `TmuxPane`, timestamps, `RaisedBy`, `ResolvedBy`
(`internal/attention/attention.go:285-345`), with `?state=` and `?limit=` (default 100, max
500 — `:426,431`). The operator approved this knowing it.

🔴 **Two asymmetries that must be written into the code comment, because a reader will
otherwise assume symmetry with the resolve:**
- The **resolve** is scoped by ownership (`requireAttentionOwnership`, migration 0038's
  `raised_by`, and a NULL raiser is **refused**). The **list will not be scoped** — that is
  the operator's decision and the point of the grant. So chief can *see* every entry and
  *resolve* only the ones it raised. Measured 2026-09-17: 0 of 26 open entries carry a
  raiser, so the resolvable set is currently empty while the readable set is everything.
- `requireHookToken` and `requireHookOrAgentToken` both inherit **enforce-when-set**: with
  no `CLAWGATE_HOOK_TOKEN` configured, both serve everyone. So this changes nothing on an
  unconfigured deploy — and every fail-closed claim here is scoped to a server that has one.
  Production has one (§1.2b).
- ⚠ The move costs one `Agents.GetByHooksToken` **SELECT per refused request** where
  `requireHookToken` did a constant-time compare and no I/O (`auth.go:481-487`). `GET
  /api/attention` is a **polled** route reachable from the LAN NodePort. This is a
  per-request database round-trip an unauthenticated caller can cause. It is the same cost
  task 607 already accepted on four routes; naming it is part of the review, not an
  objection.

**Budget:** 26 open entries measured. `Body` is the variable. Not separately measured here
— chief should call `--state open --limit 50`, and W6's skill text should say so.

**Not in this item:** `GET /api/agents/{name}/messages` stays `requireHookToken`.
`POST /api/tmux/snapshot` stays off the tier (writing the read model is
denial-of-visibility over what the operator sees).

---

### W4 — anchored / relational transcript selection *(brief item 1, the centrepiece)*
**Repo:** homelab-infra · **Deps:** W5 (without W5 it is unreachable from chief)

#### The shape — and why

**Recommendation: new FLAGS on the existing `transcript query` verb and the existing
`/events` route, not a new verb and not a new route.**

Reasons, in order:
1. `/events` already does the expensive half — one `Transcripts.Get` + one
   `transcript.Parse` of the whole tail, server-side. An anchor is a different *slice* of
   the same parse. A second route would duplicate the read, the parse, and the whole
   metadata envelope (`truncated`, `leadingPartial`, `trailingPartial`, `skipped`,
   `malformed`, `idMismatch`) that makes a filtered view of a truncated tail honest.
2. A new verb would need its own `readVerbs` entry, its own tier ledger row, its own kind
   vocabulary copy, and its own `--help` prose — four places for the two to drift. The
   CLI's own `transcriptKindVocabulary` comment (`cmd/clawgatectl/transcript.go:22-31`)
   documents exactly that drift cost.
3. The anchor is genuinely a *filter*, in the same family as `--kind` and `--grep`, and it
   composes with them.

#### 🔴 The load-bearing constraint: there is NO STABLE EVENT ID

`transcript.Event` (`parse.go:63-89`) carries `Kind`, `Text`, `ToolName`, `ToolInput`, `At`,
`Sidechain`. **No id, no ordinal.** And an index is not stable across calls: `Parse` applies
`maxEvents = 400` **newest-wins** over a tail that is itself a moving 1 MiB window, so index
0 shifts every time the session speaks. `At` is not unique either — every block of one
record shares its record's timestamp, so a multi-block assistant turn yields 2–4 events with
identical `At`.

**Therefore the anchor must be an ORDINAL COUNTED FROM THE NEWEST END, of a ROLE
OCCURRENCE** — never an absolute index and never a bare timestamp. That is also exactly
what the operator's examples ask for (*"last agent message after user message, message
before"*): all of them are relative to the end, or relative to the Nth occurrence of a role.

#### Proposed flags

```
clawgatectl transcript query <session-id>
  --anchor user:-1          # the Nth occurrence of a KIND, counted from the newest
                            #   user:-1 = last user turn, user:-2 = the one before,
                            #   assistant:-1, tool:-1, any:-1 (= newest event)
  --before 2 --after 3      # events to include either side of the anchor, in EVENT order
  --kind user,assistant     # widened from one kind to a comma set (see below)
  --max-text 600            # truncate each event's text, REPORTING the truncation
  --max-tool-input 200      # same for toolInput
```

Composition rules, each of which is a decision a test must pin:
- `--anchor` **without** `--before/--after` returns the anchor event alone.
- `--anchor` **with** `--grep`: grep filters the candidate set *first*, then the anchor is
  resolved within it — so `--grep pytest --anchor user:-1` means "the last thing I said that
  mentioned pytest". `--before/--after` then walk the **unfiltered** event list, because the
  neighbours of a matched turn are the point; the response must say so.
- `--anchor` and `--limit` are **mutually exclusive** → exit 2. `--limit` means
  "newest N"; an anchor means "this window". Silently letting `--limit` clip an anchored
  window is how a caller reads a truncated neighbourhood as a complete one.
- `--kind` becomes a comma **set**. Today it is a single value and the server 400s an
  unknown one (`transcripts.go:455-466`); the set must keep that, per element.
- An anchor that **does not resolve** (fewer than |N| occurrences of that kind) is a
  distinct outcome: HTTP 200 with `anchor: {requested, resolved: false, occurrences: K}`
  and an empty `events`, and CLI **exit 4**. It must NOT be an empty 200 that reads as "the
  session said nothing" — the same argument `transcripts.go:455` already makes for an
  unknown kind.
- Every response keeps the existing envelope **plus** `anchor: {kind, ordinal, resolved,
  occurrences, atEventIndexFromEnd}` and **plus** `textTruncated`/`toolInputTruncated`
  counts. A truncated event that does not say it was truncated is the failure
  `parse.go:198-200` and `build_pane_preview` both already refuse.

#### Files

| file | change |
|---|---|
| `internal/api/transcripts.go` | `handleTranscriptEvents`: parse `anchor`, `before`, `after`, `maxText`, `maxToolInput`; widen `kind` to a set; anchor resolution; envelope additions |
| `cmd/clawgatectl/transcript.go` | the flags, the client-side vocabulary check, the mutual-exclusion refusals, `--help` |
| `cmd/clawgatectl/verb_shapes_test.go` | `readVerbs` entry updates (it is a two-way ledger) |

#### Budget (measured, §2.2)

| call | p50 | p90 | ≈p50 tokens |
|---|---:|---:|---:|
| `--anchor user:-1 --after 1 --kind user,assistant` | **691 B** | 8,418 | **0.17k** |
| `--anchor user:-1 --before 3 --after 3` | **6,128 B** | 16,782 | **1.5k** |
| `--anchor user:-1 --after 1 --max-text 600` | ≈**900 B** (envelope ~500 + 2 clamped events) | ≈1,700 | **0.2k** |
| `--kind user --max-text 160` (the "what have I said here" index) | **1,194 B** | 2,176 | **0.30k** |
| *today's* `--limit 100`, for contrast | 90,734 B | 138,155 | **23k** |
| *today's only chief-reachable read*, `transcript get` | 1,048,576 B | — | **262k** |

So an anchored read is **130× cheaper than today's default query** and **~1,500× cheaper
than the raw tail chief can currently reach**. With `--max-text` the p90 collapses too,
which matters more than p50: the p90 is the long assistant turn that blows a context window.

---

### W5 — re-tier `GET /api/transcripts/{sessionId}/events` *(a privilege decision the brief did not name)*
**Repo:** homelab-infra · **Deps:** none · **Blocks:** W4

| route | from | to | file:line |
|---|---|---|---|
| `GET /api/transcripts/{sessionId}/events` | `requireHookToken` | `requireHookOrAgentToken` | `internal/api/transcripts.go:150` |

**Shape: chief-scoped armed-only**, same wrapper and therefore the same four-step narrowing
as W3. Not agent-wide.

🔴 **This needs its own argument because the code explicitly refuses the adjacency one.**
`transcripts.go:145-148`: *"THE `/events` SPELLING IS DELIBERATELY NOT WIDENED … Widening a
second route because it is adjacent is how a tier grows without a decision."* And
`agent_tier_ledger_test.go:78`: *"widening it because it is adjacent would be a second grant
wearing this one's review."* Those are correct and the PR must answer them, not cite them.

**The argument that is available — and it is a strong one: `/events` discloses a STRICT
SUBSET of what chief already holds.** Compared field by field against
`handleTranscriptGet`'s response (the whole `transcript.Tail`, which chief reaches today):

- `/events` returns `sessionId`, `storedSessionId`, `host`, `project`, `cwd`, `gitBranch`,
  `receivedAt`, `updatedAt`, `tailBytes`, `truncated` — **every one of which is a column of
  the `Tail` chief already gets**, except `gitBranch`/`cwd`, which are read out of the tail's
  own records (`transcripts.go:524`, `parse.go:247`), i.e. out of bytes chief already has.
- `events[]` is a **lossy projection** of those same bytes: thinking kept, `tool_result`
  **dropped entirely** (`parse.go:502`), tool `input` **clamped to 4000 chars**
  (`maxToolInputChars`), each event's text **clamped to 8000** (`maxEventChars`), at most
  **400 events** (`maxEvents`).

So for a caller that can already GET the tail, moving `/events` onto the same tier grants
**no new information class whatsoever** — it grants a *cheaper and more truncated way to
read what it can already read in full.* The route set widens; the disclosure set does not.

⚠ **Where that argument stops.** It holds only for callers that hold the tail route. If
`GET /api/transcripts/{sessionId}` were ever narrowed back while `/events` stayed wide, the
subset argument evaporates and `/events` becomes an independent content grant. That coupling
must be written into both routes' comments and is exactly the kind of thing a two-way ledger
catches (§8, W5's coverage).

⚠ **The alternative, which I do not recommend:** keep `/events` on the hook tier and have
chief parse the raw tail client-side. Rejected because (a) it is 1 MiB over the wire per
session, (b) the CLI deliberately does not import `internal/transcript` — see
`cmd/clawgatectl/transcript.go:26-30` and the verb's own `--help`: *"Parsing it in the
caller means the parse rules live wherever the caller wrote them, which is how two readers
of one file disagree about what it says"* — and (c) it would put an anchor implementation in
the CLI where no test can compare it against the server's parse.

**This is the operator's call, and it should be asked as a single question alongside W3.**

---

### W6 — the chief branch: instructions, tools, and the skill *(brief item 3)*
**Repo:** homelab-infra · **Deps:** W1/W3/W4 for content; can ship narrower

The brief names `skillsFor`/`instructionsFor`. There are **two** missing chief branches and
the other one is the one that produced the symptom.

| # | seam | file:line | when evaluated | branches on |
|---|---|---|---|---|
| **A** | `chatTurn` → `AgentInstructions` + `AgentToolDefs` | `internal/api/agents.go:1476-1479` | **per chat turn** | `a.Name == agents.OperatorName` |
| **B** | `skillsFor` / `instructionsFor` (chart `skills` + `AGENTS.md`) | `internal/agents/skill.go:135,165` | **provision / `helm upgrade`** | `agentName == OperatorName` |

**Seam A is the fix for the reported failure.** The quick action prefills `#chat-input`
(`internal/ui/chief_panel.go:154-183` — a tap fills and focuses, it does not send) and rides
`#chat-form`'s WebSocket submit into `chatTurn`. So chief was handed
`agentSystemPrompt` — *"You are a clawgate worker agent. You have native tools to read and
update YOUR assigned task"* — and the six worker tools, one of which is
`agent_request_privilege`, described as *"Request elevated access you don't have (e.g.
Kubernetes). The human is notified and decides."* **Chief's answer was a faithful reading of
the prompt it was given.**

Seam A is also the better seam mechanically: it is a pure function of the `Agent` record,
recomputed every turn, so a fix takes effect with no re-provision and no pod restart.

**Seam B still needs fixing** (it is what an *autonomous* chief turn and a post-compaction
re-injection read), but it carries a timing trap:

🔴 **"Chief" has TWO independent definitions and neither is available where `skillsFor` runs.**
- The **auth** definition: the row whose `hooks_token` equals the server's
  `CLAWGATE_CHIEF_TOKEN` (`auth.go:489-495`). Lives in the server's auth config.
- The **agent** definition: `DisplayName == "chief"`, resolved by `chiefAgent`
  (`internal/api/chief_agent.go:35,88-110`, lowest-id tie-break). `internal/api/chief_agent.go:14-19` says outright these two are unrelated.
- `skillsFor(a.Name)` receives the **slug** (`zesty-stoat`), which is the immutable
  namespace key. `buildHelmValues` already has the whole `Agent` (`values.go:239`), so the
  cheap change is `skillsFor(a)` / `instructionsFor(a)` branching on `DisplayName`.
- ⚠ **But `DisplayName` is mutable and set AFTER provision.** Labelling an agent "chief"
  later does **not** re-render its skill; only `Dispatch` or `ReapplyProfiles` →
  `helm.Upgrade` does (`provision.go:518,547-548`). So seam B's fix needs either (i) an
  explicit re-dispatch after the label changes, documented; or (ii) a `SetDisplayName` path
  that triggers `ReapplyProfiles`. Today's chief is already display-named `chief`, so a
  re-dispatch picks it up — but the trap must be written down or the next chief silently
  gets the worker skill again.
- 🔴 **`internal/agents` must not learn the auth definition.** It has no business reading
  `CLAWGATE_CHIEF_TOKEN`; that would create a third definition of "chief". `DisplayName` is
  the right predicate for the *guidance* question, and the mismatch case (display-named
  `chief` but not the armed row, or vice versa) is a real state the skill text must survive:
  **the skill must tell chief to discover its own reach rather than assert it.**

**Content — generic verbs, no quick-action mapping.** Per the operator: *"generic verbs that
it can use to answer better, not tied to the quick actions."* So the chief guidance
enumerates capabilities and their costs, and **nothing maps the three button strings to
command sequences.** Sketch:

- what chief can read, by verb, with a **byte/token cost beside each one** — chief is the
  one making the budget decision, so it needs the numbers
- the two-step idiom: **cheap fleet read first** (`tmux ls`, then
  `tmux windows --fields … --compact`), **then** anchored transcript reads on the handful of
  sessions that matter
- `claude_session_id` off a window row is the key into `transcript query`
- 🔴 **the honest negatives**, because the failure being fixed is a false negative: no
  cluster access unless a privilege profile granted one (`AgentInstructions` already tells a
  *worker* when a kubeconfig is mounted — chief needs the same sentence); no `gh`; `curl`
  present; `attention ls` reads the queue but chief can only **resolve what it raised**;
  `transcript ls` and `agent messages` are **not** reachable
- 🔴 **"verify, do not assert"**: chief should run `clawgatectl health` and let a 403 teach
  it, rather than claiming a capability from a skill that may be older than the server
- ⚠ **do not make the skill claim a reach it cannot check.** `values.go:405` states the
  principle already: *"a skill naming a command the pod lacks is worse than the curl it
  replaces."* The mirror is equally true: a skill that says "you cannot see the cluster" on a
  pod that later gets `k8s-read` is the same defect pointing the other way. Every capability
  sentence must be conditional on a check chief can run, or phrased as "try it; a 403 means
  no."

**Ordering note:** because seam A is per-turn and seam B is per-provision, a PR that fixes
only A is **immediately effective and independently revertible**. That makes it the second
PR (§6).

---

### W7 — the operator-run live reach probe *(brief item 4)*
**Repo:** homelab-infra · **Location:** `scripts/check-chief-reach.sh` (the repo has ~20
read-only `check-*.sh` peers) · **Deps:** none · **Not in CI** (needs a live cluster)

Read-only. `kubectl exec` into the chief pod and into a **discovered** non-chief pod, run
each verb, print a matrix of (verb, route, pod, HTTP status, exit code, stdout bytes).

Requirements:
- 🔴 **Both pods, every run.** The single most valuable fact the brief carries —
  `tmux windows` 200 from chief, **403** from `devpod-witty-heron` — is a *pair*. A
  chief-only probe cannot distinguish "the tier is chief-scoped" from "the tier is
  fleet-wide", which is the exact distinction the operator refused a design over.
- 🔴 **Discover both pods; hardcode neither.** Chief is `DisplayName == "chief"` at whatever
  slug (`zesty-stoat` today, id 71 today — `chief_agent.go:30-31` forbids pinning either).
  Namespace `devpod-<slug>`, deployment `<slug>-devpod`. The control pod must be *any other
  Running devpod*; pods are ephemeral and `witty-heron` will not exist next week. **If no
  non-chief pod is Running, the probe must report `CONTROL UNAVAILABLE` and set a distinct
  code — never print a chief-only matrix as if it were the full answer.**
- 🔴 **Print bytes EXAMINED beside the verdict.** A "0 failures" from a probe that reached
  no pod is the failure, not the all-clear. Same discipline `drift-check.sh` uses (links
  examined beside links dangling).
- **Positive control:** `agent task get` must return 200 from **both** pods (each resolves
  its own row). If it does not, the probe is broken, not the tier — and every other row is
  noise.
- **Negative control:** a route that must be 401/403 from both (`GET /api/transcripts/digest`
  today) — so a probe that reports 200 for everything is visibly wired to nothing.
- **It must NOT hold any operator credential.** Its whole value is that each pod presents
  *its own* `CLAWGATE_HOOK_TOKEN`. Passing the shared token in would measure a different
  caller and silently turn every row green.
- **Never `--override`, never write.** Reads and `attention raise` are the only mutating verb
  in the set and it should be **excluded** — a probe that files attention entries trains the
  operator to ignore their own queue.
- Report **what the static ledger cannot see** in its own output (§8), so the operator reads
  the two side by side.

Exit-code vocabulary, following `drift-check.sh`'s shape: `0` matrix matches expectation ·
`10` a verb reachable from the **control** pod that must not be (the fleet-wide grant, the
loudest possible finding) · `11` a verb unreachable from **chief** that the ledger says is
reachable · `12` `CONTROL UNAVAILABLE` · `13` could not reach any pod.

---

## 6. Sequencing and the recommended first PR

| PR | item | independently revertible of | why here |
|---|---|---|---|
| **1** | **W1** — `--fields` + `--compact` | everything | The whole measured win (10.7×), zero route/tier/privilege surface, zero server coupling. If nothing else lands, chief's fleet read went from 62.6k to 4.4k tokens. |
| **2** | **W6 seam A** — chief branch in `chatTurn` | W1, W3, W4, W5 | Fixes the reported symptom directly and takes effect with no re-provision. Documents only what is live *after PR 1*, so it never claims a verb that does not exist. |
| **3** | **W7** — the reach probe | all | Lands before the tier moves so it can witness them. Its first run is the baseline the ledger is checked against. |
| **4** | **W3** — `GET /api/attention` re-tier | W4, W5, W1 | One route, one wrapper, one ledger row. The smallest possible privilege change; ship it alone so its blast radius is legible. **Needs the operator's go-ahead.** |
| **5** | **W5** — `/events` re-tier | W3 (same tier, different route) | Its own PR for the same reason task 607 gave the transcript read its own PR: a standalone yes/no on a content route. **Needs the operator's go-ahead.** |
| **6** | **W4** — anchored selection | — (depends on 5 being merged) | Dead on arrival without PR 5, so it must not be merged first. |
| **7** | **W6 seam B** — `skillsFor`/`instructionsFor` + the display-name timing note | W6 seam A | Provision-time; needs a re-dispatch to take effect, which is a deploy act. Last. |
| **8** | **R1** (optional) — cache-only recap read | all | Only if the operator wants the recap index despite §3's limits. |

**PR 4 and PR 5 must be blocked on one question to the operator, asked together (§7).**
PRs 1–3 and 6–7 need no privilege decision. PR 6 needs PR 5, so if the operator declines
W5, W4 must be re-scoped (or dropped) rather than built.

---

## 7. The FORK — one question for the operator, before PR 4

Three coupled decisions, presented as one:

1. **`GET /api/transcripts/{sessionId}/events` — re-tier it too?** The brief's item 2 names
   only `GET /api/attention`. Without `/events`, **item 1's positional queries are
   unreachable from chief** and the only transcript read it has is the 1 MiB raw tail (p50
   **262k tokens**). The argument for: `/events` is a strict lossy subset of the tail chief
   already reads (§5, W5) — no new information class, only a cheaper route. The argument
   against: the code says out loud that adjacency is not a reason, and the coupling to the
   tail route's own tier becomes load-bearing. **My recommendation: yes, with the subset
   argument written into both routes' comments and pinned by the ledger.**
2. **Recaps: R4 (anchored queries only) or R2/R3 (a background producer, ± persistence)?**
   The operator's sentence assumed recaps already exist to be read. They exist only while a
   human is looking at the panel, in one process's memory (§3). R4 gives chief the *source*
   for less than the recap's own material budget. R2 buys fleet-wide recaps at the cost of a
   standing, un-priced chief-inference bill on a timer. **My recommendation: R4 + R1.**
3. **Confirm the armed state.** Production is armed (§1.2b) contrary to four in-repo
   comments. So **PR 4 grants chief the attention queue the moment it deploys** — there is no
   "inert until armed" grace period. Worth an explicit acknowledgement, since the ledger
   prose the operator reviewed last time said the opposite.

Blast radius if all of §5 ships: chief reads the operator's entire attention queue, and
reads any stored transcript through a cheaper route it could already read expensively.
Other pods stay 403 throughout (the four-step narrowing at `auth.go:553-599`, witnessed live
by the `witty-heron` control). Nothing gains a write. **Your call to proceed.**

---

## 8. Test coverage, specified

For each item: the guard, the base it must be **red** at, the mutation that must kill it
**with that guard's own message**, and the fixture states the test must be able to build.
Base for every "red at" claim: `origin/trunk` = `15744e013`.

### 🔴 What the existing static reach ledger structurally CANNOT see

`cmd/clawgatectl/chief_capability_reach_test.go` is excellent and it is not enough. Its own
header says *"nothing here contacts a cluster"*, and its coverage block enumerates its
limits. Restated for this work, with the ones that bite here marked:

| gap | consequence for this scope |
|---|---|
| **No cluster contact.** It composes three *static* facts: `internal/api`'s route table, `internal/agents`' rendered pod env, and the credential each verb puts on the wire. | It cannot see that the server is **ARMED** — which is why four comments say "unarmed" while chief gets 200s. **Only W7 closes this.** |
| **`armedOnly` is derived from `ChiefToken` being *referenced* in the wrapper AST**, not from any live config. | Same as above. `judgeArmedOnly` answers "is this wrapper narrowed", never "is the narrowing satisfied by any row". |
| **Profile-granted credentials are invisible.** `podEnvNames` AST-parses `values.go` for static `"name": "CLAWGATE_*"` literals; `profileEnv` is `[]privilege.EnvVar` read from the **database** and appended at provision time. | A privilege profile that granted chief a credential would not appear. Stated honestly in the file already; unchanged by this work. |
| **It judges by the wrapper a route is REGISTERED with.** A handler doing its own in-body credential check carries no wrapper change. | W3 and W5 are pure wrapper swaps, so this is fine for them — but a future in-handler scoping of `GET /api/attention` would be invisible. Worth a note in the new ledger row. |
| **`routes.golden` does NOT record auth wrappers** (`testdata/routes.golden:3`). | 🔴 **A tier change does not move the golden file at all.** Anyone expecting the golden to catch W3/W5 will get a green diff. `agentRowRoutes` is the only two-way guard. |
| **No consumer-side check that `/events` and the tail route share a tier.** | The subset argument for W5 (§5) depends on it. Needs a **new** guard (W5 below). |

### W1 — `--fields` / `--compact`

- **Guard:** `TestFieldsProjectionKeepsOnlyTheRequestedKeys` — a two-host fixture snapshot
  with rows carrying **pairwise-distinct** values in every field, asserting the emitted rows
  contain exactly the requested keys and the requested values.
- **Red at base:** the flag does not exist ⇒ exit 2 on an unknown flag. Report the matrix.
- **Mutation that must kill it with W1's own message:** make the projection a **pass-through**
  (ignore `--fields`). The test must fail naming the keys that should have been dropped —
  not merely "output differs".
- 🔴 **Isolate the mutation; do not let the fixture do the work.** A fixture window carrying
  only the 13 requested fields cannot see a pass-through mutant: the projection is the
  identity on it and the test stays green. So **every fixture row must carry at least one
  field that is NOT requested, and `pane_preview` specifically** — that is the field
  carrying 62% of the bytes and the whole reason the flag exists.
- **Fixture states required:** a row **missing** a requested field (key absent, not
  `null`); a row whose `windows` blob **does not decode** (must land in
  `undecodableHosts`, exit 0, per `tmux.go:122-133`); a field name with **no occurrence in
  any row** (exit 2, naming the observed set — a positive control that the refusal can
  fire); a row with a **nested** `ledger` object (projection must not flatten it).
- **`--compact`:** `TestCompactEmitsNoIndentation` asserting **byte equality** against
  `json.Marshal` and that the total shrinks. Mutant: `--compact` ignored ⇒ must fail naming
  the indentation, and the assertion must be on bytes, not on a size **threshold** (a
  threshold passes for a payload that happens to be small).

### W3 — `GET /api/attention` re-tier

- **Guard (structural, two-way):** add the row to `agentRowRoutes`
  (`cmd/clawgatectl/agent_tier_ledger_test.go:66`) as
  `"GET /api/attention": {kinds: bothKinds, armedOnly: true, why: …}`.
  `TestTheAgentTierRouteLedgerIsTwoWay` then fails if the route joins **or leaves** the tier
  without the ledger moving. 🔴 **This is the item whose coverage needs the two-way ledger
  the brief asks about** — and the ledger already exists; the work is adding the row and the
  `why`, not building a mechanism.
- **Red at base:** revert the wrapper, keep the ledger row ⇒ the shrink arm fires with
  *"has no route on that tier"*. Revert the ledger row, keep the wrapper ⇒ the grow arm
  fires with *"🔴 GET /api/attention ADMITS A PER-AGENT ROW TOKEN AND IS NOT IN
  agentRowRoutes"*. **Both directions must be watched.**
- **Guard (behavioural):** a new `internal/api` test in the `agent_tier_test.go` family:
  `GET /api/attention` with (a) the **shared** hook token → 200, (b) **chief's** row token →
  200, (c) **another agent's** row token → **403**, (d) an **unresolvable** token → 401,
  (e) **no** token → 401.
- 🔴 **(c) is the load-bearing case and it is the one the operator refused a design over.**
  `chief_capability_reach_test.go:284-298` measured that deleting all three chief guards
  from `requireHookOrAgentToken` left the whole `cmd/clawgatectl` package `ok` while
  `internal/api` redded six tests. So **the behavioural test must live in `internal/api`**,
  and (c) must assert the **403 body string** — *"this agent is not the one this server
  admits at the agent-identified tier"* — not merely a non-200, so it dies for its own
  reason and not another guard's.
- **Mutation matrix (each must kill a NAMED assertion):** delete the
  `ConstantTimeEqual(token, s.auth.ChiefToken)` branch (`auth.go:578`) → **(c) must go
  green-to-red**; delete the `token == ""` guard (`:532`) → **(e)**; delete
  `ChiefWriteRefusal()` (`:566`) → an unarmed-server case.
- **Fixture states required** (this is where the `fakeAgents.ListSessions` lesson applies —
  a fixture that can only express one shape cannot see the defect):
  `GetByHooksToken` must be able to return **chief's row**, **a different agent's row**, an
  **error**, and a row whose `Name` **fails `termwrite.ValidActor`** (`:553`, its own 403
  with its own message). `s.auth` must be constructible **armed** and **unarmed** —
  independently of the hook token being set, because those are two separate refusals with
  two separate messages. `attention.List` must be able to return **zero entries**, **entries
  with a NULL `RaisedBy`**, and **an error**.
- 🔴 **An `armedOnly` assertion drawn only from the LEDGER proves nothing** — that was
  measured (`chief_capability_reach_test.go:284-292`). The behavioural test is what makes
  the row true.

### W5 — `/events` re-tier

- **Guard:** the same `agentRowRoutes` row, plus a **new** guard for the coupling the subset
  argument rests on: `TestTheEventsRouteShareTheTierOfTheTailItProjects` — read
  `internal/api`'s route table and assert `GET /api/transcripts/{sessionId}/events` and
  `GET /api/transcripts/{sessionId}` are registered behind the **same wrapper**, failing in
  **both** directions.
- **Why that guard and not just the row:** the argument for W5 is *"`/events` discloses a
  strict subset of the tail"*. If the tail route is ever narrowed and `/events` is not, that
  sentence becomes false and the grant becomes independent — a state no existing guard can
  see. The guard's failure message must **state the argument**, so a reader who narrows one
  route learns why the other must move with it.
- **Red at base:** at `15744e013` the two are on **different** wrappers, so the guard is red
  at base by construction. 🔴 That makes it an **invariant guard, not a regression guard**,
  for the coupling direction — label it as one. The *regression* half is the `agentRowRoutes`
  row, which is genuinely red at base in both directions.
- **Mutation:** move `/events` back to `requireHookToken` → the ledger's shrink arm **and**
  the coupling guard must both fire, with different messages.
- **Fixture states:** the route scanner must be shown to read a **real** table
  (`len(regs) < 50` → fatal, the existing instrument control at
  `chief_capability_reach_test.go:988`) — a scanner returning nothing makes both wrappers
  read as `""`, which **satisfies "same wrapper" vacuously.** 🔴 That is this guard's
  specific vacuity trap and it needs its own positive control: assert both wrapper names are
  **non-empty** before comparing them.

### W4 — anchored selection

- **Guards, server-side (`internal/api/transcripts_read_test.go` family), each a separate
  named test:**
  1. `--anchor user:-1 --before 1 --after 1` over a hand-built tail with a **known** event
     sequence returns exactly the three expected events **in event order**.
  2. `user:-2` returns the previous user turn's neighbourhood — 🔴 **this is the test that
     distinguishes an anchor from "newest N"**; without it, an implementation that ignores
     the ordinal and always returns the tail passes (1).
  3. An **unresolvable** anchor (`user:-9` in a 2-user-turn tail) → 200 with
     `anchor.resolved == false`, `occurrences == 2`, `events == []`; CLI **exit 4**.
  4. `--anchor` + `--limit` → **exit 2**, zero requests sent (the `guard_test.go`
     zero-request pattern: `TestTaskGetEmptyIDSendsNoRequest` is the precedent — the
     assertion is that **nothing reached the server**, not that it 400'd).
  5. `--max-text 20` clamps and the response **reports** `textTruncated`.
  6. `--kind user,assistant` accepts the set; `--kind user,bogus` → 400 server-side and
     exit 2 client-side, **with different messages** so a test can tell which fired (the
     existing discipline at `cmd/clawgatectl/transcript.go:141-144`).
  7. `--grep` + `--anchor`: the anchor resolves **within** the grep-matched set while
     `--before/--after` walk the **unfiltered** list.
- 🔴 **Pairwise-distinct fixture text, and distinct from any constant the assertions name.**
  A fixture whose user turns all say `"hi"` cannot see a mutant that returns the wrong
  occurrence — it survives a fully green suite. Every event in the fixture must carry
  unique, identifiable text, and **the number of user turns must not be a power-of-two
  multiple of any window size** used, or a boundary bug lands exactly on its own boundary.
- **Mutations, each of which must kill a NAMED test with its own message:** ignore the
  ordinal's sign; ignore the ordinal entirely (return newest) → **(2)**; off-by-one on
  `--before`/`--after` → **(1)**; return `anchor.resolved: true` with empty events →
  **(3)**; clamp without setting `textTruncated` → **(5)**; let `--limit` clip an anchored
  window → **(4)**.
- **Fixture states the tail builder must be able to construct** (all of these are real
  states of `internal/transcript`, and a builder that cannot express them hides a class of
  bug):
  a tail with a **leading partial** first line; one with a **trailing partial** last line
  **and** one with a garbage-but-newline-terminated last line (`parse.go:287-298` measured
  these produce different `malformed`/`trailingPartial`); a tail with the **string** spelling
  of `content` and one with the **array** spelling, in the same file; an `isMeta` user record
  (must **not** be an anchor candidate — 🔴 otherwise "my input messages" silently includes
  CLI-injected text); a `tool_result`-only user record (must not be an anchor candidate); a
  **multi-block** assistant record producing several events with the **identical** `At`
  (the timestamp-is-not-unique case that makes an index anchor wrong); a tail with **more
  than `maxEvents`** records (so the 400 cap interacts with the anchor); a **sidechain**
  record; zero user turns at all.
- 🔴 **Isolation-seam guard.** W4 spans `cmd/clawgatectl` (flags, refusals) and
  `internal/api` (resolution). Both can be hermetically green and broken together — e.g. the
  CLI sending `anchor=user:-1` while the server parses `anchor_kind`/`anchor_n`. The seam
  guard is `TestTheAnchorFlagsReachTheServerAsTheParametersItReads`: run the **real command
  tree** against the harness server (the `observeCapabilityRoutes` pattern,
  `chief_capability_reach_test.go:941`), capture the **query string on the wire**, and assert
  it against the parameter names `handleTranscriptEvents` **reads** — pinned as a two-way
  ledger so a parameter added on one side and not the other fails. A structural name check
  alone type-checks past a wrong **value**, so it must be paired with a behavioural case
  asserting the returned events.

### W6 — the chief branch

- **Guard A (per-turn, the one that matters):** `TestChiefDoesNotGetTheWorkerSystemPrompt` in
  `internal/api` — `chatTurn`'s instruction/tool selection for an agent with
  `DisplayName == "chief"` must **not** be `agentSystemPrompt` and must **not** include
  `agent_request_privilege`.
- **Red at base:** yes, at `15744e013` chief gets both. The matrix is the point: red at
  base, green at HEAD.
- 🔴 **A guard on WORDS is walkable by REWORDING.** Do **not** assert "the prompt does not
  contain the string `k8s-read`" — a reword defeats it while the defect stands. Assert the
  **STATE**: the selected instruction string is **identical to** the chief instruction
  constant (whole normalised string), and the selected tool-name **set** equals the chief
  tool set exactly — a set comparison that fails when it **grows or shrinks**.
- **Mutation:** revert the branch → the test must fail naming the *worker* prompt it got.
  Second mutation: branch on `a.Name == "chief"` instead of `DisplayName` → must fail,
  because the live chief's `Name` is `zesty-stoat` and `chief_agent.go:21-28` says a literal
  `GetByName("chief")` *"would resolve nothing on the live fleet while looking perfectly
  correct in review."* 🔴 **That mutant is the one a reviewer would let through**, so it
  needs its own named case.
- **Fixture states required:** an agent with `DisplayName == "chief"` and a **generated
  slug** (never `Name == "chief"`); an agent named `operator` (must still get the operator
  path); a plain worker; **two** agents display-named `chief` (lowest-id wins —
  `chief_agent.go:82-87`); an agent display-named `chief` whose **row token is not** the
  server's `ChiefToken` (the two-definitions mismatch — the guidance must not assert a reach
  this agent does not have); `DisplayName == "Chief"` (the resolution is
  `strings.EqualFold`, so a case-sensitive branch is a live bug).
- **Guard B (provision-time):** `TestSkillsForBranchesOnTheChiefDisplayName` in
  `internal/agents`, plus a **seam** guard that `buildHelmValues` passes the **whole
  `Agent`** — `TestBuildHelmValuesGivesTheSkillSelectorTheDisplayName`. Mutant: pass
  `a.Name` again → must fail; without it, `skillsFor` can be correct and be called with a
  value that can never satisfy it.
- **Guard C (the claim/coverage check the repo keeps needing):**
  `TestTheChiefGuidanceNamesNoVerbTheCLIDoesNotHave` — extract every
  `clawgatectl <verb>` occurrence from the chief instruction text and assert each resolves
  in the **real** command tree. This is `values.go:405`'s principle made mechanical: *"a
  skill naming a command the pod lacks is worse."* 🔴 Its **positive control** is
  load-bearing: a guard that extracted **zero** verbs would pass vacuously, so it must
  `t.Fatal` on an empty extraction and the test must report the **count** it examined.
- **Guard D:** `TestTheChiefGuidanceDoesNotNameTheQuickActions` — the three strings from
  `ui.chiefQuickActions` must **not** appear in the chief instruction text. This pins the
  operator's decision 3 structurally rather than leaving it to review. Fail message must
  quote the operator's own wording.

### W7 — the probe

Not in CI (needs a cluster), so its coverage is its **own controls**, asserted in its
output, not in a test:

- positive control (`agent task get` → 200 from **both** pods) printed as a row
- negative control (a hook-tier route → 401/403 from both) printed as a row
- **pods examined** and **verbs run** counts printed beside every verdict
- `CONTROL UNAVAILABLE` as its own exit code (12), never folded into a clean run
- 🔴 A **unit-testable** part exists and should be tested: the pod-discovery predicate and
  the matrix formatter, with fixtures for *chief present + control present*, *chief present
  + no control*, *no chief*, *two agents display-named chief*, and a pod in
  `Pending`/`Terminating` (must not be chosen as the control).
- 🔴 The probe's **expectation table must be derived from `agentRowRoutes`**, not typed
  twice. A hand-copied expectation is a second ledger that rots, and the whole point of the
  probe is to disagree with the ledger when reality does — which requires reading the
  ledger, not a copy of it.

---

## 9. What I did NOT investigate

Explicitly, so nobody reads this scope as wider than it is.

1. **PR #838** — read only as far as the brief's claim that it gates the pane *write* and is
   therefore independent of this work. I did not read the PR, its diff, or its tests. The
   independence claim rests on the brief plus the live negative control, not on my reading.
2. **Whether any of the proposed code compiles or passes.** Nothing was built, no test was
   run, no `go build`, no `go test`. Every "red at base" and every mutation in §8 is a
   **specification of what must be watched**, not a measurement. They must each be run.
3. **The devrc collector's field schema beyond what I measured.** I read
   `scripts/session-manager`'s row construction (≈lines 3160–3280) and
   `MAX_PANE_PREVIEW_BYTES`. I did not audit `CAVEATS`, the tri-state vocabularies
   (`waiting_status`, `repo_status`, `unsent_prompt_status`, `pane_preview_status`,
   `age_source`), or whether a `--fields` projection could break a consumer that reads a
   pair of fields together. 🔴 **Those pairs are load-bearing** — the file says repeatedly
   that `null` + status is a different claim from `null` + nothing — so W1's `--fields`
   should probably **refuse to project a value field without its status field**, or at
   minimum warn. I did not design that; it is an open question for W1's PR.
4. **The `e2e/` suite.** `chief-threads.spec.ts` and `chief-threads-mobile.spec.ts` exercise
   the quick actions. I did not read them and do not know whether W6 breaks them.
5. **Live measurement from inside a pod.** I re-derived the tmux payload size from a **local**
   `session-manager` run, not from `clawgatectl` inside `devpod-zesty-stoat`. I ran no
   `kubectl`, `kubectl exec`, or authenticated HTTP request against clawgate. Every reach
   claim about the live server is **the brief's measurement plus my reading of the code**.
   That is exactly the gap W7 exists to close, and it should be run before PR 4.
6. **Attention `Body` size in practice.** I read the struct and the limits (default 100, max
   500) but did not measure the live queue's bytes. W3's budget is therefore unquantified;
   it should be measured before the skill text recommends a `--limit`.
7. **Recap text length in practice.** Unmeasurable without a live server —
   `recapPrompt` asks for "at most two short sentences", but no recap is stored anywhere I
   can read. R1's budget is an estimate from the prompt, not a measurement.
8. **`internal/archive`.** `Tail.ArchiveOffset` and object-storage-backed complete
   transcripts exist. I did not investigate whether an anchored query could reach **past**
   the 1 MiB tail into the archive. If it can, W4's reach is much larger than scoped here —
   and so is its disclosure. **Open question, and it may change W5's subset argument.**
9. **Cost/rate limits of chief's model.** R2's "standing inference bill" is named as a
   concern and not quantified. `recapMaxInFlight = 4` and `recapTimeout = 90s` are the only
   numbers I read; nothing has measured chief's throughput (`recap_wiring.go:33-38` says so
   itself).
10. **Whether the operator's `~/.claude` transcript corpus is representative of the fleet's
    pushed tails.** §2.2 sampled the 40 most-recently-modified sessions on the **workbench**
    only. The laptop's 1,210 sessions were not sampled, and the *pushed* set is bounded by
    the feeder (`MaxSessionsPerPush = 128`, `MaxPushTailBytes = 4 MiB`), so the sessions
    chief can actually reach are a subset I did not enumerate. The p50/p90s should be read as
    "a recent workbench session", not "a fleet session".

---

## Appendix — measurement reproduction

```bash
# tmux payload, by field (read-only; ~2 min)
timeout 120 python3 /home/zach/workspace/devrc/scripts/session-manager \
  --json --pane-preview > /tmp/sm.json
# then sum len(json.dumps({k:v})) per key over hosts[*].windows[*]

# transcript event sizes: last 1 MiB of each of the 40 newest
# ~/.claude/projects/*/*.jsonl, parsed per internal/transcript's rules
# (meta dropped, tool_result not an event, clamps 8000/4000, maxEvents 400),
# sized as transcriptEventDTO JSON.
```

Both scripts were written to the session scratchpad and are not committed; the numbers, the
field shares and the method are recorded above so either can be rebuilt.

**Key source locations, for the next reader:**

| fact | file:line |
|---|---|
| recap in-memory cache, no store | `internal/recap/recap.go:229` |
| `Peek` deliberately deleted | `internal/recap/recap.go:391-396` |
| recap generator IS chief | `internal/api/recap_wiring.go:113-131` |
| `attachRecaps` sole caller = the HTML panel | `internal/api/tmux_ui.go:209` |
| chief resolved by `DisplayName`, not name or id | `internal/api/chief_agent.go:35,88-110` |
| the missing chief branch, per-turn | `internal/api/agents.go:1476-1479` |
| the worker prompt chief received | `internal/api/agent.go:204` |
| `agent_request_privilege`'s description | `internal/api/agent.go:219` |
| the missing chief branch, provision-time | `internal/agents/skill.go:135,165` |
| `skillsFor` called with `a.Name` only | `internal/agents/values.go:351` |
| `/events` on the hook tier, deliberately | `internal/api/transcripts.go:145-150` |
| `GET /api/attention` on the hook tier | `internal/api/attention.go:60` |
| the four-step chief narrowing | `internal/api/auth.go:553-599` |
| the two-way agent-tier ledger | `cmd/clawgatectl/agent_tier_ledger_test.go:66` |
| the static reach ledger and its stated limits | `cmd/clawgatectl/chief_capability_reach_test.go:638-673` |
| `golden` does not record wrappers | `internal/api/testdata/routes.golden:3` |
| `emit` indents unconditionally | `cmd/clawgatectl/root.go:54-64` |
| `--grep`/`--host` are client-side | `cmd/clawgatectl/tmux.go:95-96,112-150` |
| pane preview cap, 16 KiB | `devrc scripts/session-manager:1083` |
| no stable event id | `internal/transcript/parse.go:63-89`, `:221` |
| `tool_result` is not a user event | `internal/transcript/parse.go:502-507` |
