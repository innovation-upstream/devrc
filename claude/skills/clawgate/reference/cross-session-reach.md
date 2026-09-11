# Reaching another Claude Code session

Query what sessions exist → resolve one → read it → message it. All four already ship in
`clawgatectl`; nothing here needs a new transport. Every command below was RUN on 2026-09-11
against server **0.8.31** and the outputs are real (text redacted where it was the operator's).

---

## 0. Which tool — the harness or clawgate?

They are not substitutes and the choice is not stylistic.

| use | when |
|---|---|
| harness `SendMessage` (+ `ListAgents`) | a LIVE peer agent or session the harness can already see. It is a real message into another agent's turn loop — typed, delivered, replied to. Names are the address. |
| clawgate `term` / `transcript` / `tmux` | **cross-host** reach; a session this process never spawned and the harness does not list; or you want to **read** a session rather than talk to it. |

🔴 **Prefer the harness when it can see the target.** A `SendMessage` is a message to an *agent*;
a `term send` is **keystrokes into a terminal** and cannot be anything gentler. Reach for clawgate
when the harness genuinely cannot reach.

🔴 **Neither one launders a permission decision.** `SendMessage`'s own contract forbids asking a
peer to do what your session was denied. `term send` is the sharper version of the same hazard: it
executes **as the operator**, in a real shell, subject to *none* of this session's PreToolUse
hooks. If a guard blocked you, a `term send` typing that same command is the blocked action, not a
workaround. Route it back to the operator.

---

## 1. The data model — read this before trusting any answer

**clawgate does not talk to tmux.** A collector on each host POSTs a snapshot; every read is as
fresh as the last push and **no fresher**. Dead collector ⇒ a perfectly well-formed, perfectly
stale document, and nothing inside the payload says so.

- **`receivedAt` is the SERVER's clock** — the one staleness signal a wrong or stopped host clock
  cannot corrupt. `capturedAt` is the host's and can lie. **Read `receivedAt` first.**
- **Cadence, measured on this host:** `tmux-snapshot-push.timer` every **2 min**,
  `transcript-push.timer` every **5 min** (`systemctl --user list-timers`). Pusher:
  `scripts/tmux-snapshot-push.sh`.
- **So a just-created window is legitimately absent, and that is not a bug.** Measured: a tmux
  session created at ~20:58:40Z first appeared in the snapshot received **21:01:50Z** — about two
  push cycles. `windowCount` went 55 → 56 at that push.

```bash
clawgatectl tmux ls
```
```json
{"hosts":[
 {"capturedAt":"2026-09-11T20:57:40Z","error":"","host":"laptop",
  "reachable":true,"receivedAt":"2026-09-11T20:57:50.230589Z","windowCount":32},
 {"capturedAt":"2026-09-11T20:57:40Z","error":"","host":"workbench",
  "reachable":true,"receivedAt":"2026-09-11T20:57:50.230589Z","windowCount":55}]}
```

Cheap staleness guard before acting on any window:

```bash
clawgatectl tmux ls | jq -r '.hosts[] | "\(.host)\t\(.receivedAt)\treachable=\(.reachable)\t\(.error)"'
```

---

## 2. Query — what sessions exist

`tmux ls` = one row per host, small enough to poll. `tmux windows` = one flat array of every
window, each stamped with its host.

```bash
clawgatectl tmux windows --host workbench --grep clawgate \
  | jq '.windows | map({host,tmuxSessionName,window_index,pane_id,codename,label,path,status,runtime})'
```

`--host` and `--grep` filter **client-side** over the one snapshot response; there is no
server-side window query.

🔴 **`--grep` is a substring over EVERY string field of a window — including `pane_preview` and
`task`.** So a match is **not** evidence the window is working on that thing; it can be matching
scroll-back text. Measured: `--grep clawgate` returned 4 workbench windows, one of which was *this
session* purely because the word appeared in its pane preview. **Confirm on a structural field
(`path`, `repo`, `tmuxSessionName`), never on the grep hit alone.**

---

## 3. Resolve — the identity fields, and which of them is a key

Measured over all **87** windows on 2 hosts:

| field | example | populated | use |
|---|---|---|---|
| `host` | `workbench` | 87/87 | **half the key** |
| `pane_id` | `%42` | 87/87 | **the other half** — what `term send` takes |
| `window_id` / `window_index` | `@42` / `1` | 87/87 | display; NOT a send target |
| `tmuxSessionName` | `scratch13` | 87/87 | confirmation |
| `label` | `mango` | 87/87 | confirmation |
| `codename` | `mango` | 69/87 | confirmation only — **not unique** |
| `claude_session_id` | `5c0f925a-…` | 60/87 | the transcript join key |
| `path` | `/home/zach/workspace/…` | 87/87 | **best identity confirmation** |
| `repo` | `civitai-gpu-fleet` | 31/87 | often `repo_status: "unmeasured"` |
| `claude` / `runtime` | `true` / `claude` | 61 claude=true | is it a Claude session at all |
| `status`, `busy`, `task`, `waiting_probable`, `unsent_prompt`, `pane_preview` | — | — | what it is doing |

⚠ **The wire key is camelCase `tmuxSessionName`.** Querying `.session` returns null for all 87 and
reads as a confident "100% missing". Verify a field name against a live payload before quoting it.

🔴 **`pane_id` alone is NOT unique — the key is `(host, pane_id)`.** Measured: 87 windows carry only
**56 distinct `pane_id`s** but **87 distinct `host|pane_id`s**; `%10`–`%14` and many more exist on
both hosts. This is why `--host` is required on every `term` verb, and why a pane id copied without
its host can execute on the wrong machine.

🔴 **`codename` does not identify a window.** Measured: `Gold` covers **9** windows on workbench,
`wheat` 8, `orange` 5 — and `Vapor` and `orange` each exist on **both** hosts. It is a confirmation
signal, never a selector.

⚠ `claude: true` (61) and `claude_session_id != null` (60) disagree by one — a Claude process can be
running before its session id is known. Branch on the field you actually need.

**Finding your own row** (useful so you never mistake yourself for the target): this session's id is
the last path segment of its scratchpad directory, and it appears verbatim as `claude_session_id`.

```bash
clawgatectl tmux windows | jq '.windows[] | select(.pane_id=="%42" and .host=="workbench")
  | del(.pane_preview, .ledger)'
```
```json
{"claude":true,"claude_session_id":"5c0f925a-125a-473e-ad51-c1700c5f74e4","codename":"mango",
 "host":"workbench","label":"mango","pane_id":"%42","path":"/home/zach/workspace/civit/datapacket-talos",
 "repo":null,"repo_status":"unmeasured","runtime":"claude","status":"unknown",
 "task":"◐ Proceed with merge","tmuxSessionName":"scratch13","window_id":"@42","window_index":"1"}
```

⚠ **Never round-trip a `tmux windows` payload through a shell variable in zsh.** `pane_preview`
carries raw control bytes and zsh's builtin `echo` expands backslash escapes, so `echo "$W" | jq`
dies with *"Invalid string: control characters … must be escaped"* on data that was valid JSON.
Redirect to a file and read the file.

---

## 4. Read — one session's transcript

A transcript is the **tail** of a session's JSONL pushed by a feeder. It is not the whole session:
`truncated`, `leadingPartial`, `trailingPartial` say so, and every verb reports them.

```bash
clawgatectl transcript ls | jq '{count: (.sessions|length), first: .sessions[0]}'
```
```json
{"count":259,"first":{"sessionId":"8152b7fc-51f5-451f-9a04-22e93ad6f1d4",
 "contentHash":"541eb82…","updatedAt":"2026-09-11T20:56:18Z","tailBytes":195538}}
```

**Window → transcript** is `claude_session_id`. Measured join rate: **59 of 60** window session ids
were stored. The miss is expected — the feeder has not pushed yet, or retention swept it — and
surfaces as **exit 4**, not an empty success:

```
$ clawgatectl transcript get 00000000-0000-0000-0000-000000000000   # rc=4
clawgatectl: GET /api/transcripts/…: not found — no transcript stored for that session id
```
Exit **7** is different: the ROUTE is absent, i.e. this `clawgatectl` is newer than the server.

🔴 **Prefer `query` to `get`.** `get` hands back Claude Code's own record format — two spellings of
`content`, sidechain records, thinking blocks, and tool RESULTS that are most of the bytes. `query`
runs the server's parser, the same one the web chat view uses, so two readers cannot disagree about
what the file says.

```bash
clawgatectl transcript query 5c0f925a-125a-473e-ad51-c1700c5f74e4 --kind assistant --limit 3 \
  | jq '{sessionId,storedSessionId,host,project,cwd,gitBranch,receivedAt,tailBytes,
         truncated,leadingPartial,trailingPartial,idMismatch,parsedEvents,matched,skippedTotal}'
```
```json
{"sessionId":"5c0f925a-…","storedSessionId":"5c0f925a-…","host":"workbench","project":"talos",
 "cwd":"/home/zach/workspace/civit/datapacket-talos","gitBranch":"trunk",
 "receivedAt":"2026-09-11T20:56:23.056869Z","tailBytes":196517,"truncated":true,
 "leadingPartial":false,"trailingPartial":null,"idMismatch":false,
 "parsedEvents":43,"matched":16,"skippedTotal":137}
```

- `--kind` is one of `assistant|thinking|tool|user`; `--limit` keeps the **newest** (default 100).
- 🔴 `--grep` searches the tool **name and input** as well as the text — it has to: a tool event
  carries an empty text by construction, so a text-only search answers "no matches" for a session
  that ran that tool forty times.
- The response reports what it left out (`omittedBefore`, `matched`, `skipped`, `malformed`,
  `idMismatch`). **A filtered view of a truncated tail is not a claim about the whole session** —
  `truncated: true` above is the normal case, not an anomaly.

**This is also your second identity channel.** The envelope carries `host`, `cwd`, `project` and
`gitBranch` from the transcript itself, independent of the tmux snapshot. Confirming those against
the window's `path`/`host` before you write is the cheapest way to know you resolved the right
session.

---

## 5. Message — `term send`, and why it is not a chat

🔴 **`term send` is arbitrary command execution as the operator, in a live pane.** It queues a
`tmux send-keys`. The text lands as **keystrokes in a real terminal**, and by default it then
presses **Enter**, so the pane *executes* it. There is no undo, no confirmation, and the pane may
belong to a human mid-task.

Measured, into a pane created for this purpose — the text was typed **and ran**:

```
/tmp echo CLAWGATE-DOC-PROBE-OK-20260911
CLAWGATE-DOC-PROBE-OK-20260911
```

### The ritual — resolve, confirm, then send

Never pattern-match your way to a target and never take "the first result".

1. **Resolve** a specific window with `tmux windows`, filtered on a structural field.
2. **Confirm identity** on at least two of `path` / `tmuxSessionName` / `label` / `codename`, and
   — if it is a Claude session — cross-check `cwd`/`host` from `transcript query` (§4).
3. **Check `receivedAt`** (§1). A stale snapshot can name a pane that no longer exists.
4. **Read before you write.** Look at `pane_preview`, `busy`, `status`, `waiting_probable` and
   `unsent_prompt`. Typing into a pane with an unsent prompt in it appends to the operator's
   half-written text.
5. **Then** send, with `--idempotency-key`.

```bash
clawgatectl term send --host workbench --pane '%60' \
  --text 'echo CLAWGATE-DOC-PROBE-OK-20260911' \
  --idempotency-key 'doc-probe-20260911-a'
```
```json
{"created":true,"write":{"id":"b60juB9ekllLhBC8D1Hn1Q","idempotencyKey":"doc-probe-20260911-a",
 "kind":"send-keys","tier":"token","host":"workbench","pane":"%60",
 "text":"echo CLAWGATE-DOC-PROBE-OK-20260911","submit":true,
 "state":"pending","createdAt":"2026-09-11T21:02:23.426757Z","expiresAt":"2026-09-11T21:03:53.426757Z"}}
```

- `--pane` must be a tmux **pane id** `%N`. A `session:window.pane` target is refused **client- and
  server-side** because it resolves fuzzily and can execute in the wrong pane (measured: `rc=2`,
  *"pane must be a tmux pane id of the form %N"*). Missing `--pane` is also `rc=2`.
- `--no-submit` types the text **without** pressing Enter. The default presses it — the operator's
  standing decision — so **a repeat is a repeated EXECUTION**. That is why the queue is at-most-once
  and why `--idempotency-key` exists.
- **`--idempotency-key` verified:** re-running the identical command returned
  `{"created": false, "id": "b60juB9ekllLhBC8D1Hn1Q", "state": "delivered"}` — the **original** row,
  and the pane showed exactly **one** execution. Always pass one.

### 🔴 A 200 means QUEUED, never RAN

clawgate cannot touch either host's tmux socket (the pod has no hostPath, hostNetwork, hostPID or
nodeName). A host agent claims the row and executes it, at most once. **`term ls` is where you read
what happened; the closing evidence is the text appearing in the pane, not the send's exit code.**

```bash
clawgatectl term ls --limit 3 \
  | jq '.writes[] | select(.idempotencyKey=="doc-probe-20260911-a")
        | {tier,host,pane,submit,state,claimedAt,claimedBy,completedAt,error}'
```
```json
{"tier":"token","host":"workbench","pane":"%60","submit":true,"state":"delivered",
 "claimedAt":"2026-09-11T21:02:23.711575Z","claimedBy":"workbench:3379836",
 "completedAt":"2026-09-11T21:02:23.746320Z","error":null}
```
Delivered 320 ms after queueing — but **the TTL is 90 s** (measured: uniform across every row in the
ledger), so an unclaimed write dies quietly.

**States, with what each actually means — every one of these was read off the live ledger:**

| state | measured cause | means |
|---|---|---|
| `delivered` | right host, right pane | it ran |
| `expired` | **wrong `--host`** — rows targeting `nixos` and `nonexistent-scratch-host-phase1`, `claimedBy: null` | nobody ever claimed it; nothing ran |
| `failed` | right host, nonexistent pane (`%999998`) — `claimedBy` set | the agent tried and tmux refused |
| `abandoned` | — | delivery is **UNKNOWN**, not "nothing ran" |
| `pending` / `claimed` | — | in flight |

🔴 **`--host` takes clawgate's host label, not `hostname`.** On this box `hostname` prints `nixos`
while clawgate knows it as `workbench` — and the ledger holds a real `nixos`-addressed write from
2026-09-06 that simply **expired**. Take the value from `tmux ls`.

### `term launch` — a process-spawn primitive

Opens a **new** tmux window at `--cwd`, optionally typing `--text` into it. `--cwd` must be absolute
and contain no `..` (the server enforces both; the value goes to tmux's own `-c`, so a relative path
would resolve against wherever the host agent happens to be). `--tmux-session` is a tmux session
name, **not** a Claude Code session.

Verified: `kind: "new-session"`, `state: "delivered"`, and a new window appeared at index 2 with the
text executed in it. It shares the fail-closed tier with `term send` because it spawns a process.

### 🔴 Attribution — what the ledger can and cannot tell you

`tier` discriminates the **door**, not the caller: it holds exactly two values, `token` (any machine
caller) and `browser` (the web UI). Measured over the whole ledger after this file's own probes
landed — 13 rows: 7 `browser`, 6 `token`. There is **one shared `CLAWGATE_TERMINAL_TOKEN`**, so
every machine caller files `tier: "token"` and is **indistinguishable from every other machine
caller**. The two writes issued while writing this file sit in there as plain `token` rows: nothing
in the ledger says they came from an agent, from this session, or from a doc probe.

**Do not claim sends are attributable per session or per agent. They are not.** `claimedBy`
(`workbench:3379836`) identifies the *host agent that executed* the write, never its author. A
per-agent token is the open design question, not a thing that exists.

---

## 6. Credentials — two secrets, one fail-closed tier

| verbs | credential | server behaviour |
|---|---|---|
| every READ (`health`, `tmux`, `transcript`, `task`, `agent`, `attention`, `view`, `panel`) | `CLAWGATE_HOOK_TOKEN` | **enforce-when-set** — an unconfigured server serves them |
| `term send` / `term launch` / **`term ls`** | `CLAWGATE_TERMINAL_TOKEN` | **fail-CLOSED** — a server with no terminal token serves nothing there |

They are different secrets and **neither falls back to the other**. The hook token is handed to
every hook script on both hosts and rendered into every dispatched agent pod's environment; these
routes are arbitrary command execution. So **a dispatched agent pod carries the hook token and NOT
this one — read verbs work there, `term` verbs refuse, and that is the intended arrangement.**

⚠ **`term ls` is a READ on the fail-closed tier by choice, not by the ledger's rule** — its rows
carry the operator's literal text, which may contain a secret. **Project fields with `jq`; never
dump the rows raw.**

Two refusals, and they mean different things:

| rc | meaning |
|---|---|
| **2** | this CLIENT has no terminal token — **nothing was sent** |
| **9** | the SERVER refuses to serve the surface at all (`requireTerminalToken` is unarmed); your credential was never examined |

Verified rc=2 with the token genuinely absent:
```
clawgatectl: refusing to send a terminal write with no terminal credential. … Nothing was sent.
If you are running inside a dispatched agent pod: that pod carries CLAWGATE_API_URL and
CLAWGATE_HOOK_TOKEN only, by design. Read verbs work there; these do not.
```
⚠ Setting `CLAWGATE_TERMINAL_TOKEN=` **empty does not disarm the client** — `~/.claude/clawgate.env`
still supplies it and the call succeeds (measured rc=0). To test the unarmed path, point
`--env-file` at an empty file and `env -u` the variable.

---

## 7. Failure modes, one line each

| symptom | cause | check |
|---|---|---|
| a window you just made is absent | push cadence — up to ~2 min | `clawgatectl tmux ls` → `receivedAt` |
| everything looks plausible but is hours old | collector dead; payload still well-formed | `receivedAt`, and `reachable`/`error` per host |
| send returns 200, nothing happens | 200 = QUEUED | `term ls` → `state` |
| `expired`, `claimedBy: null` | wrong `--host` (e.g. `hostname`'s value) | take `host` from `tmux ls` |
| `failed`, `claimedBy` set | pane does not exist on that host | re-resolve `(host, pane_id)` |
| `rc=2` on a `term` verb | no terminal token **here** | nothing was sent |
| `rc=9` on a `term` verb | surface unarmed **on the server** | credential never examined |
| `rc=4` on `transcript` | session not stored (feeder/retention) | `transcript ls` |
| `rc=7` on any verb | route absent — client newer than server | `clawgatectl health` |
| `--grep` hit that is not the right window | grep matches `pane_preview` | confirm on `path`/`repo` |
| jq "control characters must be escaped" | zsh `echo` mangled the payload | redirect to a file |
