---
---
# Handoff: tmux-webapp — 2026-08-26

## Goal
A **clawgate feature**: a webapp that visually organizes and gives live terminal interaction
with tmux sessions across workbench + laptop, with a composable view system agents can drive,
and an **attention queue** that surfaces sessions needing a human so Zach can jump straight in.

## Status

**Phase 1 (the attention queue) is SHIPPED. Phase 2 (the tmux read model) is COMPLETE — BOTH
halves shipped, deployed, and observed running UNATTENDED. Rank 3: BOTH HALVES ARE WRITTEN.
The rendering half is MERGED (`ZacxDev/homelab-infra#496`, squash `844a7350`); the data path
is OPEN as `innovation-upstream/devrc#992`. NEITHER IS DEPLOYED. Phases 4–6 untouched.**

🔴 **RANK 3 IS MERGED-BUT-NOT-DEPLOYED, WHICH IS NOT DONE.** Two independent reasons, and
BOTH must clear before anyone calls it done:
- **The UI is not running.** clawgate's image pin is an immutable literal with no Flux image
  automation, so #496 reconciled cleanly and **changed nothing serving**. Shipping is build +
  push image + bump the pin (and `cmd/clawgatectl/client.go`'s `buildVersion` with it — see
  the runbook gotcha below). `clawgatectl health` is the evidence, never `git log`.
- **The screens are not flowing.** #992 is still open, and until it merges AND `ship.sh` runs,
  the deployed collector emits no `pane_preview` at all — so every card will render
  `no preview field (collector predates it)`. That string is the tab telling the truth about a
  half-deployed feature, and it is what you should EXPECT to see first.

🔴 **DO NOT READ AN EMPTY-LOOKING GRID AS A BROKEN RENDERER** until you have confirmed the
workbench collector is passing `--pane-preview`. The renderer distinguishes those cases ON
PURPOSE — `disabled` (flag not passed), the empty status (collector predates the field), and
`ok` — so read the note under each card rather than inferring from a blank pane. That
distinction is the whole point of the status field and it is what makes this debuggable.

🔴 **DO NOT READ A VERSION FROM THIS DOC** — `clawgatectl health` is the only authority. It said
**0.8.9** on 2026-08-28, the THIRD value this line carried in three readings that day
(0.8.7 → 0.8.8 shipped here → 0.8.9 from a concurrent session, mid-session). The snapshot
routes were re-verified on 0.8.9 after that supersession (200 with token, 401 without).

*How the design got here (carried forward):* settled 2026-08-26 across two rounds — a greenfield
session, then an audit that reopened four decisions — then a re-platform onto clawgate that resolved
three of them outright. The sections below from `## Platform` down are that design, still current.

**Shipped, newest first:**
- **`innovation-upstream/devrc#992`** — ⚠ **OPEN, NOT MERGED** — rank 3's data path.
  `session-manager` publishes each Claude pane's visible screen as `pane_preview` +
  `pane_preview_status`, from the capture batch that ALREADY ran and threw the screen away, so
  it costs zero extra tmux work. Opt-in `--pane-preview`; the pusher is the one caller that
  passes it. 🔴 **No server change was needed** — migration 0027's "unknown fields preserved
  verbatim" contract held, verified end-to-end against live 0.8.9 (POST 322 KB → 200; GET
  returned 46 rows `ok` / 26 `not_claude` with the `session`→`tmuxSessionName` rename intact).
  Gates: BOTH sandbox tiers green on the branch AND on the MERGED tree (`devrc-pytests` 18,289
  passed / 0 failed; `devrc-nodetests` 1300/1300).
- **`innovation-upstream/devrc#974`** — the host-side pusher, phase 2's remaining half — merged as
  **`f0308e46`** and shipped to both hosts. `scripts/tmux-snapshot-push.sh` + a serverMode-gated
  systemd timer (2 min). ✅ **VERIFIED BY AN UNATTENDED TICK**, which is the only proof that counts
  here: every earlier success was a hand-run. Journal `pushed …B … HTTP 200` at 2m0s spacing with
  `Result=success`, and `receivedAt` advancing `20:02:52 → 20:04:52` while the push count went 3 → 4.
- **`innovation-upstream/devrc#970`** — merged as **`8ca23613`**. ⚠ Its kickoff named head sha
  `e5773b1c`, which had **never been pushed** and existed only in a local worktree — so GitHub's
  head was `6e2ced2d` throughout, and that, not a Tekton fault, is why no PipelineRun ever appeared
  for it. The kickoff also said to remove that worktree, which would have **destroyed the audit-pr
  fix**; it was pushed first.
- **`ZacxDev/homelab-infra#468`** — the tmux snapshot ingest, phase 2's server half — merged as
  **`32f49804`**, then DEPLOYED as **0.8.8** (pin `628a963a`). `POST/GET /api/tmux/snapshot`,
  migration `0027`, and `internal/tmux`, which owns the vocabulary boundary. 🔴 Merging it had
  changed nothing running — the image pin is an immutable literal with no Flux image automation —
  which is exactly why it sat inert until the pin moved.
- **`ZacxDev/homelab-infra#457`** — the idle reaper's own cadence — merged as `3a66e3e0`, deployed
  as **0.8.7** (pin `cc51b9b4`) and verified over 4.5h of production sweeps. See rank 1.
- **`#451`** (detached suggest POST) merged as `a38360a5`, live on BOTH hosts (hook files verified
  byte-identical, `f793ab9c…`).
- **`#422`** (the attention queue) merged as `5a008e4f`; **`#427`** (one-line `buildVersion` fix) as
  `f2f8cb7e`; **`#432`** closed after fast-forwarding onto the branch to keep one reviewable PR.
- **`innovation-upstream/devrc#890`** — this doc's audited rewrite — merged as `f53ef8a6`;
  **`#959`** (ranks 1/3/4 + the lessons below) as `660e0671`.

**Phase 1 was verified through the real path, not inferred (carried forward):** a genuine
`AskUserQuestion` produced a `kind=question priority=high` entry sorted **above ten `idle` rows** —
the priority-ordering fix demonstrated in production. Repeated from the laptop after its hook was
fixed: entry id 149, `host=laptop`, hook `exit=0` with empty stdout (still defers). Test entry
resolved afterwards.

**What the feature is:** attention entries (migration `0026`), kinds `question` (high) / `idle`
(low), raised by the `AskUserQuestion` path, the Stop hook, and `clawgatectl attention
raise|ls|resolve`; surfaced in an htmx tab and pushed via the pre-existing `POST /api/notify`.
🔴 An entry is **not** a decision object — no approve/deny, it carries a *destination*.

⚠ **SUPERSEDED READING, kept for the reasoning below it** — the pod and version named here
rolled several times the same day. **Live health at handoff (2026-08-28 09:53Z):** pod
`clawgate-7c78695584-mcc74`, image `0.8.7`, `restarts=0`, up since 07:21:45Z — it **rolled cleanly**
during the session, which wiped the earlier sweep history with the old pod. The queue is bounded:
`open=23`, **`idle_past_4h=0`** (baseline before the fix: 64 open / 21 eligible). 🔴 **The reaper
has logged no `attention-reap: resolved` line since that roll, and that is CORRECT, not a
regression** — the pass is silent when it resolves nothing, and nothing has been eligible. This is
exactly the case the unconditional boot line exists for: `07:21:46 attention reaper: sweeping every
30m0s …` is the only thing distinguishing a healthy silent sweep from a reaper that never started.

**The laptop had a five-month-old hook.** Its `PermissionRequest` hook was registered at
`~/.claude/clawgate-hook.sh` — a regular file (not a symlink; `readlink -f` resolves to itself),
byte-identical to commit `03efe4ed`, **clawgate 0.3.1**, mtime 2026-06-06. Its Stop hook *was* on
the repo path, so the idle path worked while the **question path was dead** — the exact use case
this feature exists for, silently, on one host. Repointed at the repo path via `jq` against a
timestamped backup (21 hooks / 7 keys preserved, one-line diff). The stale 0.3.1 copy is still on
disk, now referenced by nothing.

**Seven audit rounds ran on #422; the ladder stopped when a round came back clean, never on a
verdict.** Three compounding defects would have made the queue bury the questions it exists to
surface (idle never reaped + priority absent from the sort + a 100-row limit taking the *oldest*).
**Nine separate instruments were caught measuring nothing**, including three harnesses built to
check earlier ones, one that scored an *unmutated* tree as SURVIVED, and a CI timer that had both
fire-and-forget tests passing on literally nothing. Two of those predated this work and were found
only because the **merged tree** was gated instead of the branch.

🔴 **#468 ran FIVE more rounds, and the pattern there is the finding: every round found a defect the
PREVIOUS round's fix had introduced.** An atomicity fix caused a 30%-reproducible deadlock
(randomised map order vs row locks); fixing that left a guard three separate mutants walked through;
and `UseNumber`, added so a nanosecond timestamp would not be corrupted, re-opened a read
amplification that a 32-char cap missed (`1e100000` is 8 chars) and `ParseFloat` then closed only
half of (it returns `0, nil` on UNDERFLOW). Round 5 was the first with **no behaviour defect**.
⚠ **Stopped there — ONE round short of the mechanical two-zero-payload gate** — because round 5 was
auditing test code round 4 wrote, and round 6 would have audited test code round 5 wrote. That is a
judgement, not the rule; a sixth round is a legitimate thing to ask for.

**Lowest-numbered OPEN item is rank 3** (read-only `capture-pane` rendering). ⚠ Its
`🔴 DEPENDENCY — cannot be built before item 4` paragraph is **SATISFIED as of 2026-08-28**: item 4
immediately below it is ✅ DONE, and the delivery path it was waiting for now exists and runs every
2 minutes. Nothing else about rank 3 changed; it still needs no further decisions.

## Platform: this is a clawgate feature
| | |
|---|---|
| Source | `~/workspace/homelab-talos/containers/clawgate/` (Go, module `github.com/zacxdev/clawgate`) |
| Live version | **0.8.5** (ReplicaSet image, 2026-08-27 21:17Z) — ⚠ this row has been stale twice; read it from the cluster, never from here |
| Cluster / ns | **workbench**, ns `clawgate`; GitOps from `trunk` |
| Stack | Go + **htmx** (136 `hx-*` attributes in `internal/ui/*.go`; UI is Go-built HTML, **not** template files) |
| LAN URL | `http://192.168.50.250:30302` (NodePort) — 🔴 **no human auth** |
| Public URL | `https://clawgate.zacx.dev` behind **Authelia passkey** |

### What the re-platform resolved for free
- **Transport (was A1).** clawgate **already terminates WebSockets** server-side —
  `coder/websocket v1.8.14`, `websocket.Accept(...)` at `internal/api/agents.go:1132` — and
  **already does SSE**. The Python-stdlib constraint that made this a fork does not exist in Go.
  Use WS for terminal I/O, SSE (or htmx polling) for the read model.
- **Frontend framework (was part of A4).** htmx is the established pattern; no build step, no
  SPA. Only the terminal widget remains open.
- **Deploy target (was A2), partially.** The workbench cluster is the right *cluster* — it runs
  on the workbench itself. It is **not** sufficient on its own; see the next section.

## 🔴 The blocker the re-platform did NOT resolve
The clawgate deployment has **no `hostPath`, no `hostNetwork`, no `hostPID`, no `nodeName`**
(`clusters/workbench/apps/clawgate/deployment.yaml`). tmux sockets are unix sockets on the
workbench and laptop **hosts**. A pod cannot see them.

**RESOLVED — host-side agent per host.** A small systemd user service on each host owns the tmux
socket and holds an **outbound** connection to clawgate:

```
workbench host                     laptop host
  tmux socket                        tmux socket
      |                                  |
  tmux-agent (systemd user)          tmux-agent
      |  outbound WS/long-poll           |
      +----------> clawgate pod <--------+
                  (workbench cluster)
                   UI + queue + API
```

Why this shape: symmetric across both hosts, **no inbound access to either machine**, no
privileged pod, no SSH credential living in a pod on an unauthenticated LAN surface. It also
mirrors the rendezvous pattern already running here (`browser-bridge`), and `session-manager` is
already the host-side collector — see "Build on what exists".

## 🔴 Auth — the highest-stakes decision
**`send-keys` is arbitrary command execution as your user, on both machines.** Measured in
source, not assumed:
- `internal/api/auth.go:40-42` — `requireSession` is a literal `return next`. **Human auth was
  removed**; the LAN is "treated as trusted-open" and the public path relies on the Authelia
  forward-auth edge.
- `internal/api/auth.go:51-54` — `requireHookToken` **fails OPEN** when no token is configured
  ("left open for back-compat").
- The LAN NodePort already exposes unauthenticated `DELETE /tasks/{id}` and
  `POST /api/auto-approve-all`.

**RESOLVED — a real auth check on the tmux WRITE surface**, independent of `requireSession`.

🔴 **It must FAIL CLOSED.** Do **not** reuse `requireHookToken` for terminal writes: its
enforce-when-set semantics mean an unset token silently yields an open remote shell on both
machines. A dedicated wrapper that refuses to serve when unconfigured is the requirement, and
the difference between the two is the whole control.

Reads may follow existing clawgate conventions. Writes — `send-keys`, `kill-*`, `new-*` — go
behind the fail-closed wrapper.

## Structured dynamic UI — persisted layout model
**RESOLVED — layout lives in Postgres**, because that is the only shape where an agent can
genuinely compose a view (client-side layout is unaddressable from `clawgatectl`).

```
view    id, name, owner, layout(grid|stack)
 panel  id, view_id, position, size,
        target{host, session, window, pane},
        state{expanded | collapsed | archived}
```

Both the htmx UI and `clawgatectl` mutate the same rows, so a human drag and an agent call are
the same operation. Sketch of the agent surface:

```
clawgatectl view create "deploys"
clawgatectl view add-panel deploys --host workbench --session <name>
clawgatectl panel collapse <id>
```

## Attention queue
**RESOLVED — three raisers.** The point is a queue Zach can scan and jump into.

| Raiser | State today |
|---|---|
| **Agent asks a question** | 🔴 **Silent today.** `hook/clawgate-hook.sh:79` explicitly defers `AskUserQuestion` to the terminal **without contacting the server**, because a question is not allow/deny-routable. **This hook must change** — it is the primary use case. |
| **Agent stopped, ready for next prompt** | Stop hook already fires (`/api/suggest`, writes `cc_sessions`). Route it into the queue as a lower-priority "idle, awaiting prompt" entry. ⚠ It was ~96% dead — 23,937 payload failures vs 921 successes since 2026-06-14 — and **both chokepoints are now fixed** (`--rawfile` + `--data-binary @file`). Do not re-derive that bug. |
| **Explicit `clawgatectl` verb** | New. An agent deliberately raises with a reason. |

🔴 **A queue entry is NOT a decision object.** The existing request card carries approve/deny;
an attention entry carries **no decision and a destination** (jump to this session). Model it as
a distinct entity or the approve/deny UI leaks into it.

**Push already exists**: `POST /api/notify` — `{title, body, host, project}`, push-only, no
request card, hook-token auth (`internal/api/server.go:347`). The attention primitive does not
need building, only wiring.

⚠ **Known coverage gap, accepted deliberately.** `session-manager`'s passive waiting-detection
was considered and **declined**. All three chosen raisers depend on an agent cooperating or a
hook firing, so an agent that **hangs, crashes, or is killed** raises nothing. The passive
source is read-only and can be added later if the queue proves to miss cases.

## 🔴 Vocabulary collision — settle before routes and verbs are baked
clawgate **already has a `session`**: `cc_sessions` + `task_sessions` (migration 0023), with
`GET /api/sessions/{id}/tasks`. It means a **Claude Code session**. A tmux session is a
different thing. Two entities called `session` in one API is a defect that gets permanent the
moment routes and `clawgatectl` verbs ship. Namespace the new one (`term:` / `tmux_session`)
before writing the first route.

## Build on what exists (do not reinvent)
| Need | Already exists | Where |
|---|---|---|
| Cross-host tmux read model | `session-manager` SSHes to the laptop, runs `tmux list-panes -a` + `list-windows -a` on **both** hosts, emits `--json` as `report["hosts"][{workbench,laptop}]["windows"]`. 4,395 lines, test-covered. | `devrc/scripts/session-manager` |
| Push notification | `POST /api/notify` | `internal/api/server.go:347` |
| WebSocket termination | `coder/websocket`, origin-checked | `internal/api/agents.go:1132` |
| Rendezvous pattern for a host agent | `browser-bridge`'s outbound long-poll command queue | `devrc/scripts/browser-bridge/server.py` |
| Claude-session detection in panes | `claude_sessions.py` | `devrc/scripts/lib/claude_sessions.py` |

Hazards `session-manager` already encodes that a fresh collector would rediscover:
- `list-panes` and `list-windows` are **two non-atomic calls** — the join can tear.
- `reachable` / `error` describe the **first** call only.

From the analyze-service index (**recall — verify before relying on**):
- tmux **window ids restart at `@0` when the tmux server restarts**; a row needs the server pid
  as a sentinel or a post-reboot `@41` inherits a dead session's identity and a multi-day age.
- `$TMUX_PANE` can be set while `tmux display-message` **fails**, landing a partial record on
  top of a good one and silently un-joining the window.

## Still open
- **A4 — the terminal widget.** clawgate vendors only two hand-written JS files
  (`filter-toggle.js`, `tag-normalize.js`, ~3.7 KB total) and **no third-party bundle**.
  xterm.js would be the first. Go's `embed` makes it mechanically easy, and it must be
  **vendored, not CDN** — clawgate is reachable on an offline LAN. Alternative: ship read-only
  `capture-pane` rendering first and defer xterm.js until interaction proves needed.
- **The "jump in" action is cross-machine and under-specified.** From a phone, "jump into the
  session" cannot attach a terminal — it has to be the web terminal. At the workbench, focusing
  the real tmux window is better (`window-triage` already resolves codenames/hotkeys). These are
  two different actions behind one button; design them explicitly.

## Next steps (ranked)

🔴 **Ranks are STABLE and are NOT renumbered when an item completes** — the rank is half a claim's
identity (`claim-work --slug-for claudedocs/handoff-tmux-webapp.md <rank>`), so re-ranking silently
re-points every live claim. A finished item stays in place marked ✅ DONE; take the lowest-numbered
open one, not "the first in the list". *History, carried forward:* the 2026-08-26 renumbering
superseded an earlier 1–7 list, so a claim slug minted before that date may name a different item —
do not renumber again without releasing the live claims first.

1. ✅ **DONE 2026-08-27 — it had never fired because it COULD NOT. `ZacxDev/homelab-infra#457`.**
   Not an observation task: the reaper was inert in production for the whole of its life.

   **Mechanism.** The reap's only caller was `retentionPass`, whose only caller was
   `RunRetention`'s **24h** ticker — and `time.NewTicker` delivers its FIRST tick one whole
   interval in. Sweeping once required a pod to survive 24h. Measured: since the feature shipped
   (0.8.3, ReplicaSet created `2026-08-27T16:00:08Z`) the longest-lived pod managed **5h02m**
   (0.8.4 got 15m, 0.8.5 2h41m, `restarts=0`); clawgate deploys far oftener than daily. The live
   pod's log held **zero** `retention:` lines. 🔴 The leader lease was **ruled out** as a rival
   mechanism rather than assumed innocent — `leader(background-loops): acquired lease` appears 30s
   after boot. The ticker was the sole blocker.

   **The second defect, which outlives the restart story:** even a pod that never restarted swept
   every 24h against a 4h window — an entry sat up to **6× its own window** past eligibility. This
   doc used to call the 4h constant "the single knob". It never was; the sweep interval was.

   **Fix:** a dedicated leader-gated `RunAttentionReap` on `api.AttentionReapInterval` (30m), with
   `retentionPass` still calling the same extracted pass daily as a backstop — one implementation,
   two schedulers. Four audit rounds; the ladder stopped on the **attribution gate** (two
   consecutive rounds whose fixes changed zero payload lines), not on a verdict.

   🔴 **The diagnostic tell has CHANGED — the old string no longer exists.** Two lines now:
   - at boot, unconditionally: `attention reaper: sweeping every 30m0s for idle entries not seen for 4h0m0s`
   - on a sweep that resolved something: `attention-reap: resolved N idle attention entry(ies) not seen for 4h0m0s`

   The prefix moved off `retention:` deliberately: two schedulers drive the pass now, so a
   `retention:` line could no longer say *which* swept. And the **entry line is the load-bearing
   one** — the pass is silent when it resolves nothing, so without it a pod whose reaper never
   started and a pod sweeping a healthy empty queue emit byte-identical logs. That
   indistinguishability is exactly what hid this bug, and "0 log lines" was the evidence for it.

   **SHIPPED — clawgate 0.8.7 is live** (`3a66e3e0` merged; pin `cc51b9b4`; Flux reconciled; pod
   `clawgate-7c78695584-zqdjf` Running/ready). ⚠ The pin had moved to **0.8.6 under me** mid-session
   (a concurrent ship), so 0.8.7 was derived from the LIVE pin re-read at the moment of acting —
   and 0.8.6 was confirmed **by content** to predate the merge and not contain the fix.
   First evidence in this feature's history that the reaper exists at runtime:
   `02:04:10 attention reaper: sweeping every 30m0s …` followed by
   `02:04:40 leader(background-loops): acquired lease`.

   **Forced end-to-end validation (2026-08-28), because waiting 30m is not the only option — but
   restarting the pod is NOT a way to force it: that RESETS the ticker and makes it strictly
   worse.** There is no on-demand route. So the deployed tree was run against a throwaway Postgres
   with **one line changed** (the interval to 5s) and **synthetic** rows — never copied production
   entries, which carry captured session text. Result:
   `attention-reap: resolved 3 idle attention entry(ies) not seen for 4h0m0s`, taking exactly the
   3 stale `idle` rows while 2 fresh `idle`, a stale `question` and a stale `manual` all survived,
   with `created_at` backdated 30 days on **every** row so the reap provably keys on `updated_at`.
   A negative control ran first: two sweeps with nothing stale resolved nothing and logged nothing.
   🔴 **This closes a real gap — every unit test runs against a FAKE store, so the `pgstore` SQL had
   never been exercised by the loop against a real Postgres.** 🔴 **But it changed the very variable
   the bug was about (the interval), so it is NOT a substitute for observing the 30m production
   tick** — that is what the boot line's `every 30m0s`, the AST cadence guard and the `main()`
   ledger are for.

   ✅ **VERIFIED IN PRODUCTION 2026-08-28 — the behaviour nobody had ever observed.** Six sweeps
   over 4.5h, every one landing on exactly `:04:10` / `:34:10` (boot 02:04:10 + n×30m0s), so the
   cadence is measured over many ticks rather than inferred from one:
   `02:34:10` resolved **23**, then 8 / 1 / 2 / 5 / 11 at 04:04, 05:04, 05:34, 06:04, 06:34.
   The bound is holding, checked against the DB and not just the log: open **64 → 11**, resolved
   45 → 107, **idle entries still past the 4h window = 0**, and both `question` rows preserved.
   Steady state ~11 against `attentionPanelLimit` 100 — the runaway this feature was losing to is
   closed.

   Claim `tmux-webapp-1` released.
2. ✅ **DONE 2026-08-27 — `ZacxDev/homelab-infra#451`, merged as `a38360a5`. Deployed and verified
   on BOTH hosts.** The suggest POST is detached (payload renamed to a **sibling of `WORKDIR`**,
   fork, child `rm`s before it logs). Claim `tmux-webapp-2` released.
   **Measured independently, old hook vs new, against a server that accepts and never replies:
   8030/8031/8037 ms → 22/21/21 ms.** Verified live through the real path after each pull:
   workbench `exit 0`, empty stdout, **28 ms**; laptop `exit 0`, empty stdout, **199 ms** (its
   endpoint is the homelab gateway over nebula, not a LAN NodePort — the extra ~170 ms is the
   route, not the fork). Both logged `suggest sent ok` from the detached child; no scratch leaked.
   ⚠ The two hosts pulled at different moments, so they carry the detach at different commits
   (`a38360a5` / `6cda752c`) — `drift-check.sh` will report that correctly and it is not a defect.
3. ✅ **DECIDED 2026-08-28 (Zach): read-only `capture-pane` rendering first.** A4 is closed as a
   decision; the BUILD is blocked on item 4 — see the dependency below.

   🔴 **The premise this item carried for two sessions was FALSE, and it was the whole argument.**
   It read "clawgate vendors only two hand-written JS files (~3.7 KB) and **no third-party bundle**,
   so xterm.js would be the first." Measured 2026-08-28: the two hand-written files are real but
   live at `internal/ui/js/{filter-toggle,tag-normalize}.js`, individually `go:embed`ed — and
   **`web/static/vendor/` already holds FIVE third-party bundles totalling ~245 KB**
   (`htmx.min.js` 51 KB, `faro-web-sdk.iife.js` 93 KB, `faro-web-tracing.iife.js` 82 KB,
   `idiomorph-ext.min.js` 10 KB, `sse.js` 9 KB), every one referenced from the Go-built HTML and
   swept up by `//go:embed static` in `web/static.go`. So xterm.js would be the **sixth**, and the
   "no precedent" objection does not exist. Vendoring is a solved, offline-safe pattern here: drop
   the file in `web/static/vendor/`, reference it, done — no build step, no CDN.

   **The decision survives on a BETTER reason, which this item never connected to itself:** an
   interactive terminal means `send-keys`, which is **arbitrary command execution as Zach on both
   machines**. Item 5's fail-closed auth wrapper is then a hard prerequisite, so "vendor xterm.js"
   is not a library choice — it drags the highest-stakes decision in this project forward. Read-only
   has no write path and therefore no such coupling.

   🔴 **DEPENDENCY — SATISFIED.** Item 4 shipped the delivery path (2-minute push). The old
   text is preserved below for the measurement, which still holds: `hostNetwork/hostPID/hostIPC`
   all false, `nodeName` null, only volume a PVC — the pod has no path to a host tmux socket, so
   `capture-pane` output must be *delivered* from the host. (Distroless, no shell: probe via the
   spec, not `kubectl exec`.)

   **HALF DONE 2026-08-28 — the DATA PATH is open as `innovation-upstream/devrc#992`; the
   RENDERING is not written.** Claim `tmux-webapp-3` is still HELD, deliberately, because the
   item is not finished.

   🔴 **THE DOC SAID THIS ITEM "NEEDS NO FURTHER DECISIONS" AND THAT WAS WRONG.** It had never
   said WHERE the pane text rides. Measuring first surfaced a real fork — fold it into the
   existing 2-minute push, or build an on-demand rendezvous — that would have been costly to
   get wrong across two repos. **Zach chose snapshot-carried previews.** The measurement, on
   72 live panes (45 workbench + 27 laptop), is what settled the rest:
   - today's push **110,806 B**; visible capture of all 72 panes **249,042 B raw**; JSON
     escaping/structure overhead **+38.9%**; gzip 5.3x.
   - 🔴 **SCROLLBACK IS EXCLUDED ON A HARD BOUND.** Each extra line costs **~4,014 B
     fleet-wide**, so `-S -1000` computes to **6.13 MB** against `maxTmuxPushBytes` (4 MB) and
     the cap breaches at **~650 lines/pane**. Visible screen only; `tail` serves history.
   - 🔴 **ONLY 22% OF PANES CHANGE PER TICK** (10 of 45 over a real 120 s interval), so 77% of
     the bytes are resent unchanged. In absolute terms that is cheap (62 MB/day gzipped) — **the
     objection is STALENESS, not bandwidth.** A 2-minute-old preview is right for "which session
     needs me" and useless for watching output scroll. That is the argument for an on-demand
     path later, and the reason it was NOT built now.
   - shipped shape: **46 Claude panes** carry the text (36 workbench + 10 laptop); the other 26
     are shells and report `not_claude`, never an empty screen. Live back-to-back: **122,731 B
     without, 322,204 with = 2.63x**, **7.7% of the cap**. Per-pane cap 16 KiB ≈ 2.4x the
     largest real pane observed (6,616 B).

   **THE RENDERING HALF IS MERGED — `ZacxDev/homelab-infra#496`, squash `844a7350`.** A Tmux
   tab: `internal/ui/tmux.go` (`TmuxPanel` + `RenderTmux`), `internal/api/tmux_ui.go`, and
   `GET /tmux` + `GET /ui/tmux`. 15 tests, mutation-swept 11/11.
   - 🔴 **NO DECISION SURFACE, and it is a HARD PREREQUISITE, not a style choice.** A write
     surface here is `send-keys` = arbitrary command execution as Zach on both machines, and
     **item 5's fail-closed wrapper does not exist yet**. `TestTmuxGridCarriesNoDecisionSurface`
     asserts the RENDERED HTML has no form/button/hx-post — against output, not source, because
     a helper that emits a button is still a button. **Do not add one before item 5.**
   - 🔴 **THE TAB NEEDED FIVE REGISTRY ENTRIES AND THE GUARDS CAUGHT THE TWO I MISSED**:
     `tabKeys`, `tabHeadings`, the sidebar, **the JS `TABS` array** and **the JS `HEADINGS`
     map`**. `TestAttentionTabIsWiredIntoEveryTabList` caught the JS array; the axe contrast
     scan caught two `text-slate-500` uses (4.24:1). Adding a seventh tab? Expect all five.
   - 🔴 **THE PANEL POLLS (60s) RATHER THAN AWAITING AN SSE EVENT.** Every sibling panel
     refreshes on a `*.changed` nudge, but this read model is fed by an OUTSIDE agent and
     clawgate emits no such event — an `sse:tmux.changed` trigger would look like working code
     and never fire. Pinned by a test.
   - **The two HUMAN routes are registered unconditionally**, outside `registerTmuxRoutes`'
     nil-store guard, because `Page` renders the panel on every boot; a store-less boot would
     otherwise paint a tab whose panel 404s. The MACHINE routes stay behind the guard.

   **WHAT REMAINS: THE DEPLOY, AND IT IS TWO INDEPENDENT SHIPS.** (a) clawgate image build +
   pin bump (+ `buildVersion`) to make the tab exist; (b) #992 merged + `ship.sh` to make the
   screens flow. Closing condition: the Tmux tab renders ≥1 pane whose status is `ok` — not
   merely that the tab loads, because it loads perfectly well showing "collector predates the
   field" on every card.
4. ✅ **DONE 2026-08-28 — BOTH HALVES SHIPPED, DEPLOYED AND OBSERVED RUNNING UNATTENDED.**
   Server: `ZacxDev/homelab-infra#468` (`32f49804`), deployed as clawgate **0.8.8**
   (`628a963a`). Pusher: `innovation-upstream/devrc#974`, squash-merged **`f0308e46`**,
   shipped to both hosts (`ship.sh` — cross-host agreement verified at one sha).
   Claim `tmux-webapp-4` released.

   🔴 **THE PROOF IS AN UNATTENDED TICK, NOT A HAND-RUN PUSH.** Every earlier success in
   this item's history was me invoking the script. Measured after the switch, with no human
   action: journal shows successive `pushed …B … HTTP 200` at 14:58:47 / 15:00:49 / 15:02:5x
   / 15:04:5x (2m0s apart, `Result=success`, `ExecMainStatus=0`), and `receivedAt` advanced
   `20:02:52 → 20:04:52` across a tick while the push count went 3 → 4. Read model:
   workbench 45 windows / 34 with `runtime`, laptop 27 / 10.

   ⚠ **Live clawgate is now 0.8.9** — another session shipped it mid-work. My 0.8.8 deploy is
   superseded, not undone; the snapshot routes were re-verified on 0.8.9 (200 with token, 401
   without). **Read the live pin, never this line.**

   ⚠ The timer is `serverMode`-gated to the workbench and the laptop correctly shows
   `linked`-not-enabled. That is CORRECTNESS, not scoping: `session-manager --json` collects
   BOTH hosts from the workbench, so two reporters would fight over every row.

   🔴 **Two decisions here were made AGAINST this doc, on measurement.** (a) It specified a unit on
   EACH host; `session-manager --json` already collects BOTH from the workbench (it SSHes to the
   laptop) in **~970 ms**, returning 43 workbench + 28 laptop windows — so ONE agent, and the schema
   is per-host so a second reporter can be added later with no server change. (b) It sketched a
   WebSocket; rank 3 deferred the interactive terminal, so a persistent socket buys nothing today
   and adds reconnect/backoff state to get wrong. **Periodic HTTPS POST.**

   **What shipped:** `POST/GET /api/tmux/snapshot` behind `requireHookToken`, migration `0027`
   (latest-per-host, no reaper — a snapshot is current-state, so there is nothing to retain and no
   reaper to get wrong, which is the mistake `attention_entries` paid for), and `internal/tmux`,
   which exists to own the VOCABULARY BOUNDARY: session-manager spells its tmux session `session`,
   so the collision arrives WITH THE PAYLOAD and is renamed to `tmuxSessionName` at ingest.

   **The pusher, as shipped.** `scripts/tmux-snapshot-push.sh` + a `serverMode`-gated systemd
   user timer (`OnUnitActiveSec=2min`), posting `session-manager --json` VERBATIM; the server
   normalises, so the host stays a dumb pipe and the vocabulary rename lives where it is
   tested. Cadence measured, not guessed: 1298 / 1196 / 1193 ms per collection, ~1% duty
   cycle. ⚠ Its real per-run cost is up to FOUR ssh invocations (no `ControlMaster`) plus one
   ClickHouse query — ~2,880 handshakes/day, not the 720 an earlier estimate implied.

   🔴 **Distinct exit codes are the ONLY alarm it has** — 2 no creds, 3 collector failed,
   4 transport, 5 non-2xx, 6 torn collection. It deliberately wires **no** `OnFailure` toast:
   that toast defeats do-not-disturb and is justified by ~1 firing in 9 days, so at this
   cadence a sustained outage would fire it 720×/day and burn down the channel. ⚠ **The
   compensating control is `/standup` reading the failed-unit list, NOT read-model staleness**
   — nothing reads `GET /api/tmux/snapshot` outside clawgate's own tests, so `receivedAt`
   staleness is recorded and unread. That covers the exit codes and NOT an exit-0-achieving-
   nothing, which is why the redirect and unmeasured-zero cases are handled in the script.

   🔴 **Four audit rounds; every one found a defect the previous round's fix introduced.**
   Round 2: `rc=$?` after `if ! cmd` is always 0 (so every failure logged `rc=0`, worst on a
   timeout, which has empty stderr too); success tested `HTTP < 400`, so a 3xx meant curl
   returned the redirect, nothing was stored, and it logged "pushed" and exited 0; and
   `windows_measured` — the discriminant `session-manager` computes precisely to separate an
   unmeasured zero from a real one — was being discarded at the last place that still had it,
   so a reachable-but-unenumerated host would overwrite a good 44-window snapshot with a
   false zero. Round 3's fix then **dropped `gawk`** as "unused": `agent_ledger` runs `awk 1`,
   `awk` exists only in gawk, and the `2>/dev/null; exit 0` hides the error while the `echo`
   sentinel still prints — 34 of 45 windows silently lost `runtime`. Round 3 also broke BOTH
   required checks with a **pure prose change**, because `launcher_scan.py` reads raw text and
   the comment named a hazardous binary. Round 4 caught `30[0-9]` failing to exclude 304.

   🔴 **A mutation sweep certified two of these as covered when they were not.** The `rc=0`
   mutant SURVIVED a green file (nothing asserted the number), and the 304 mutant reported
   *killed* against a test that was **already red** — a mutant dies for free when the baseline
   is red, and I had never run that test unmutated. Final: 24 mutants, 24 killed, with the
   controls fixed. **The sweeps found less than the auditor did.**

   **Five audit rounds ran. Every round found a defect the PREVIOUS round's fix introduced** —
   round 1 an atomicity bug; fixing it caused a 30%-reproducible deadlock (randomised map order vs
   row locks); fixing THAT left a guard three separate mutants walked through; and `UseNumber`,
   added so a nanosecond timestamp is not corrupted, re-opened an amplification that a 32-char cap
   missed (`1e100000` is 8 chars) and `ParseFloat` then closed only half of (it returns `0, nil` on
   UNDERFLOW). Round 5 was the first with **no behaviour defect**. Stopped there — one round short
   of the mechanical two-zero-payload gate — because round 5 was auditing test code round 4 wrote.
   Residual amplification measured **8.1x**, ratio-bounded, down from 1,107x.
5. **Fail-closed terminal-write auth wrapper** — before any write endpoint exists, not after.
   `internal/api/auth.go`. 🔴 Must NOT reuse `requireHookToken`: it is enforce-when-set
   (`auth.go:51-54`), so an unset token would silently yield an open remote shell on both machines.
6. **Layout schema + `clawgatectl view`/`panel` verbs** (phase 3) — views/panels/targets/state in
   Postgres, so a human drag and an agent call are the same operation.
7. **The grid UI** (phase 4), then organization ops behind item 5's auth.
8. **Housekeeping, cheap:** eight more sleep-based timing bets in
   `containers/clawgate/internal/api/{push_task,task_comment}_test.go` (pre-existing; mechanical now
   the `awaitPushesSettled` barrier exists); and a scanner test for in-body `! grep` — closing
   condition: a test in both bats suites that reds on a planted `! grep` assertion.

🔴 **There is no rank 9. A previous revision listed one — "get three portable lessons into
`MEMORY.md`" — and it was NOT a work item: the operator confirmed 2026-08-27 that MEMORY.md is not
used here, so nothing could ever have closed it.** Removed deliberately rather than left to be
re-read by every resume. The lessons themselves are kept where they belong: the clawgate-specific
ones in the subsystem index (`homelab-talos/clawgate.md`), and the two generic ones — bats `! grep`
inertness and the zsh `EPOCHREALTIME` trap — in this doc's own **Gotchas**, which appends and
survives. Nothing was lost by dropping the item; only the false claim that work was outstanding.

10. **Two guard gaps #457's ladder left open deliberately, both scaffolding-scope.** Recorded here
    because the ladder stopped on the attribution gate (two consecutive zero-payload rounds), not
    because these were closed.
    - **`RunSweeper`'s ticker survives `NewTicker`→`NewTimer`** against the whole api package —
      i.e. the same one-shot defect class, still unguarded on a *different* loop. Pre-existing and
      unrelated to #457, found incidentally. `RunRetention` and `RunReconciler` are worth the same
      check. The structural pattern to copy now exists:
      `TestRunAttentionReapTicksOnTheIntervalItWasGiven`. Closing condition: a mutation of each
      loop's ticker reds a named test.
    - **The `main()` wiring ledger pins syntax, not reachability.** Wrapping a loop's start in a
      condition false in production (`if os.Getenv(...)=="1"`) keeps it green — verified. NOT
      closable statically, because the start is legitimately inside `if pool != nil`; banning
      enclosing conditions would red the real code. Documented in-test. The compensating control is
      the runtime entry log line, which is itself now guarded.

**Parked with the operator (not work items until answered):**
- Seam tests **skip in `clawgate-ci`** — the Go image has no `jq`. Closing it edits a pipeline every
  PR in the repo runs. `TestSeamASlowSuggestPostCostsTheHookNothing` — the only test driving *real*
  curl at a *real* server — is therefore ungated.
- `ZacxDev/homelab-infra` has **no branch protection at all** (the API 403s — needs GitHub Pro or a
  public repo). Nothing there is mechanically required; every merge rests on the reader.
- The **passive backstop was declined**: all three raisers need an agent to cooperate or a hook to
  fire, so an agent that hangs, crashes or is killed raises nothing — and those strand longest.
  `session-manager`'s waiting-detection is read-only and cheap to add if the queue misses cases.

## Gotchas
- 🔴 **A FAILED `git worktree add` DOES NOT STOP THE NEXT `git -C <path>` — AND I LANDED A
  MERGE ON ANOTHER SESSION'S BRANCH THIS WAY.** `worktree add /home/zach/workspace/devrc-integ`
  failed with `fatal: … already exists` (another session was using that path for
  `integ/963-965`); the very next line, `git -C /home/zach/workspace/devrc-integ merge
  origin/feat/…`, ran happily **inside their worktree** and committed a merge onto **their**
  branch. It is silent: no conflict, clean tree, and `git log` afterwards shows exactly what
  you expect, because you are reading the branch you landed on. Recovered via `git reflog`
  (pre-merge head `ea9811ed`) + `git reset --keep`, which refuses rather than destroys.
  **Two rules: give every scratch worktree a PID-UNIQUE name, and branch on `worktree add`'s
  EXIT CODE before issuing one more `-C` command against that path.** A generic name like
  `<repo>-integ` is exactly the one another session also picked.
- 🔴 **THE ZSH NO-WORD-SPLITTING TRAP RETURNED A CONFIDENT `0` FOR EVERY SCROLLBACK DEPTH —
  in a session that had already read the rule.** `panes=$(tmux list-panes …); for p in $panes`
  loops **ONCE** on the whole newline-joined string, so `capture-pane -t "%1\n%2\n…"` fails and
  every total is 0. It does not error; it reports a clean measurement of nothing. The tell was
  a POSITIVE CONTROL: an earlier inline `for p in $(…)` had already measured 51,406 B for the
  same host, so a 0 was impossible. Fix: `${=var}`, a real array, or pipe to `bash -s`. 🔴
  **Never quote a zero from a shell loop you have not positive-controlled** — this is the same
  family as the `gawk` silent zero this feature already paid for once.
- ⚠ **`test_subsystem_store_api.py::TestTrustedProxyOverTheRealProcess::test_THE_DEFECT_the_five_forged_attempts_are_CHARGED_TO_THE_FORGER`
  is a live ~15% flake under load, and its signature is `assert 4 == 5`** ("expected at least 5
  `store-api audit` lines, got 4"). Its own comment records 3/20 red locally plus two
  consecutive reds in the nix sandbox. Measured 2026-08-28 while gating #992 on a box at load
  18–51 from concurrent agents: branch full-target **2 green / 1 red**, `origin/main` green,
  the test alone **6/6 on both trees**, and BOTH sandbox tiers green. 🔴 **Do not re-run and
  move on — run the discriminating control** (full target on a clean `origin/main` worktree)
  and check whether unrelated targets' wall times also moved; here a 273-test target swung
  9.56 s → 5.52 s between runs, which is load inflating everything, not one assertion.
- 🔴 **A TRUNCATED READ, WRITTEN DOWN, IS INDISTINGUISHABLE FROM A FACT — and I did it TWICE in one
  session.** A `jq … | head -2` made the laptop look like it had no clawgate Stop hook registered
  (it does), and a `print(sorted(d.keys())[:8])` made session-manager look like it emits no
  timestamp — that claim then went into a 🔴 migration comment as "measured", and it was wrong by
  17 days (`ts` was added 2026-08-11). Both truncations were MINE, in the instrument, and both read
  back as findings about the world. **Never slice the output of the command you are about to quote**
  — and when a claim is going into a comment as a measurement, re-run it unsliced.
- 🔴 **A BOUND NEEDS BOTH HALVES, AND A FIXTURE DERIVED FROM THE CONSTANT PINS NOTHING.** Measured
  repeatedly on #468: asserting only what a bound REJECTS let an off-by-one silently narrow it, and
  asserting only what it ADMITS let the branch be deleted. Worse, a reject-side fixture built as
  `strings.Repeat("0", MaxNumberLiteralLen)` scales WITH the constant — setting it to 1,000,000,000
  survived the whole suite while the test allocated 1 GB fixtures and passed. Pin a literal value,
  and pin the constant itself.
- 🔴 **A GUARD ON A CALL'S PRESENCE IS NOT A GUARD ON ITS EFFECT.** Four successive attempts to pin
  one ordering requirement were each walked through: asserting the helper (deleting the call site
  passed), asserting the call exists (dropping the assignment and moving it below the loop both
  passed), asserting SOME loop ranges over it (a decoy loop passed), and OR-ing across loops (a
  second unsorted loop passed). What finally held was quantifying over EVERY write loop and binding
  the check to the loop that actually writes. **Ask what the code must DO, then assert that.**
- 🔴 **A PERIODIC SWEEP'S INTERVAL MUST BE SMALLER THAN THE PROCESS LIFETIME, AND A TICKER'S FIRST
  TICK LANDS ONE WHOLE INTERVAL IN.** `time.NewTicker(24h)` in a pod that lives 5h fires **never**,
  and every surface reads healthy: the code is correct, the unit tests pass, the leader lease
  acquires, the deploy reports success. This is a general shape, not a Go detail — any cron/ticker
  whose period exceeds the lifetime of the thing running it is dead code with no error anywhere.
  🔴 **Two numbers, and neither alone tells you it works:** how STALE a row must be to be taken
  (the window) and how OFTEN anything looks (the sweep). A 4h window swept every 24h leaves an
  entry 6× its own window. Ask which one your test pins — every test here pinned the window.
- 🔴 **A test that calls the pass DIRECTLY cannot see a scheduling defect.** Every existing test of
  this reaper drove `retentionPass` by hand, which is exactly why a reaper that had never executed
  looked fully covered. Deleting the `go srv.RunAttentionReap(...)` line from `main.go` left the
  whole module green — verified as a surviving mutant. The wiring in `main()` needs its own ledger;
  `containers/clawgate/{leader_wiring,background_loops_wiring,version_pin}_test.go` are the
  in-repo convention for source-level pinning of things no behavioural test can reach.
- 🔴 **CADENCE CANNOT BE PINNED ON A WALL CLOCK — measured, both directions.** Asserting "N ticks
  within a fixed sleep" is an assertion about the *scheduler*: `time.Ticker` DROPS ticks under CPU
  contention, and that helper was observed landing exactly on its floor. Loosening it to
  "reaches N eventually" then made interval-scaling mutants (×2…×40 — a 1h-to-20h production sweep)
  ALL survive; a bound tight enough to catch ×10 is tight enough to flake. There is no good value.
  Pin it **structurally** instead (AST-assert the ticker is driven by the bare `interval`
  parameter): deterministic, zero wall time, and it killed the whole class.
- ⚠ **The deploy runbook's `DOCKER_HOST=ssh://zach@192.168.50.250` is HOST-DEPENDENT and fails on
  the workbench.** `192.168.50.250` **is** the workbench, so running it there SSHes to itself and
  dies with `ssh_askpass: exec(): No such file or directory` → `Too many authentication failures`,
  which reads like a broken credential rather than a wrong host. From the workbench just use the
  local daemon — it is the same daemon the ssh transport was reaching for. The runbook line is
  correct *from the laptop*. Check `ip -4 addr | grep 192.168.50.250` before believing the error.
- ⚠ **A stress test that spawns busy-loops must reap them by RESOLVED PID.** An audit round's
  `kill %1 %2 …` job-control cleanup failed; 74 `while :; do :; done` shells reparented to init and
  saturated ~11 cores for 45 minutes — corrupting the very timing measurements the next round then
  reported. Confirm each PID's `/proc/<pid>/cmdline` before killing, and never let a pattern reach
  `pkill -f`.
- 🔴 **Committing to `trunk` deploys the MANIFEST, not the container CODE.** The image pin is an
  immutable literal tag with no Flux image automation, so a commit under `containers/clawgate/**`
  reconciles cleanly and **changes nothing running**. Shipping = build + push image + bump pin.
  `clawgatectl health` is the evidence, never `git log`.
- 🔴 **`clawgate-ci` does NOT run Playwright — this feature is almost entirely browser-layer, so
  it is UNGATED.** Run `make e2e` locally and **count**: without Docker, `test.skip` on
  `!dockerAvailable()` leaves **11 of 18 spec files / 77 of 113 tests** and goes green.
- 🔴 **`clawgatectl` is built from a LOCAL working tree of homelab-talos**, so it can be present
  but STALE — a behind checkout ships a binary **missing verbs that prints help and exits 0**
  under a plausible version label. This already happened (0.7.95, no `task status`). New verbs
  inherit it: an agent calling `clawgatectl view add-panel` on a stale host gets help and a
  success exit. rc 7 covers *binary newer than server*; the dangerous inverse needs its own
  guard. Both hosts need the rebuild.
- Do not bind `192.168.50.94` — a homelab node; binding it **crash-loops the unit** and already
  cost `initiatives-viewer` an outage.
- Public routing must `proxy_pass` to the NodePort IP `http://192.168.50.250:30302`, never a
  `.svc.cluster.local` name — that doesn't resolve there and **crashes nginx, taking down all
  nebula-routed services**.
- Laptop `main` is 2 commits behind `origin/main` (`drift-check.sh` rc 10) — `scripts/ship.sh`.

- 🔴 **Merging is NOT deploying here, and the hooks invert that rule.** The image pin is an immutable
  literal tag with no Flux image automation, so a `containers/clawgate/**` commit reconciles cleanly
  and changes nothing running. **But the hook scripts are read from a working tree** —
  `~/.claude/settings.json` points at
  `/home/zach/workspace/homelab-talos/containers/clawgate/hook/*.sh` — so a plain `git pull` makes
  them live instantly for every Claude Code session on that host, with no switch and nothing gating
  it. **Deploy the server first.** The reverse degrades safely (404 → `exit 0`, no output; there is
  a test) but the feature silently does nothing.
- 🔴 **A host's hook registration is NOT uniform — check `readlink -f`, never assume.** The workbench
  pointed at the repo; the laptop pointed at a stale private copy. Same feature, same pull, opposite
  outcomes, and the broken one looked healthy.
- 🔴 **The deploy runbook's step 3 is incomplete.** It bumps only `deployment.yaml`, but
  `TestDeployPinMatchesClientBuildVersion` also requires `cmd/clawgatectl/client.go`'s
  `buildVersion`. Bumping one reddens `trunk` for **every PR in the repo** — which is exactly how it
  was found (#427). Independently documented upstream as devrc #923.
- 🔴 **`! grep -q X f` is INERT under bats errexit** unless it is the last line of a test. A mutant
  restoring an `Authorization: Bearer` header survived a fully green run. Use `refute_grep`, which
  carries its own positive control as a test.
- 🔴 **busybox `date +%s%N` silently DROPS `%N`** — no error, just bare epoch seconds. In the bats CI
  image (`bats/bats:1.11.0`, BusyBox 1.36.1) the timer therefore read 0 ms for everything and **both
  fire-and-forget tests passed vacuously on the tier that gates the merge**. Use bash's
  `EPOCHREALTIME`. A positive control is what exposed it.
- 🔴 **A squash merge NEVER makes the branch head an ancestor**, so `--is-ancestor` reads "not
  merged" forever and blocks cleanup of merged worktrees. Verify by CONTENT (`gh pr view --json
  mergedAt,mergeCommit` plus a file diff), never by ancestry.
- **A smoke test can be structurally blind.** The first 0.8.3 smoke returned `404` on
  `/api/attention` and looked like a missing feature; `registerAttentionRoutes` returns early with
  no DB. Re-run against a real Postgres: 200, 200, and the image applied migration 26 itself.
- **Build `app.css` from inside `containers/clawgate/`** or Tailwind's relative globs resolve against
  the wrong tree and emit ~5 KB with no utility classes. `TestOpenRoutesNoAuth` and
  `TestStaticAssetsServed` are **not** "known-red" — they fail only when that build was skipped.
- **Rejected:** WebSocket-on-Python-stdlib, a homelab-cluster deploy, and a new cross-host collector
  — see this doc's audit findings A1/A2/A7. The re-platform onto clawgate resolved A1 and A2
  outright (clawgate already terminates WebSockets and already does SSE).

- 🔴 **The Bash tool's shell is ZSH, which has no `EPOCHREALTIME`** — so `${EPOCHREALTIME/./}`
  expands to EMPTY and every arithmetic timing built on it silently reports **0 ms for everything**,
  including cases that genuinely take 8 seconds. Measured 2026-08-27 while verifying #451: three
  successive "measurements" read 0 ms and only the POSITIVE CONTROL (the old hook, which *must* be
  slow) exposed it. Use `date +%s%N` (GNU date is present on the NixOS hosts; it is **busybox** date
  in the bats CI image that drops `%N`) and validate the timer itself — `sleep 2` must measure
  ~2000 ms — before quoting any number.
- 🔴 **`clawgate-stop-hook.sh` sources `~/.claude/clawgate.env` with `set -a`, so THE FILE BEATS THE
  ENVIRONMENT** (`:82`) — the opposite of `clawgatectl`'s documented precedence (file → env →
  flag). Exporting `CLAWGATE_API_URL` to point a probe somewhere harmless therefore does nothing,
  and the probe silently hits **production**. Override `CLAWGATE_CONF_FILE` instead. Measured: a
  latency probe meant for an unroutable address POSTed to the live server and left a stray `idle`
  entry in the real queue.
- ⚠ **`192.0.2.1` (RFC 5737 TEST-NET-1) does NOT reliably black-hole** — on the workbench `curl`
  fails it instantly with rc 28 rather than waiting out `--max-time`. For a reproducible "server
  never answers" instrument, run a local listener that accepts and never replies, and validate it
  (a plain `curl --max-time 8` against it must take ~8000 ms) before trusting a hook measurement.
- **Re-verify a repo's branch/divergence state at the MOMENT you act on it, not from an earlier
  survey.** Measured 2026-08-27: a `git reset --keep origin/main` recovery was proposed for a
  diverged devrc `main`, and by the time it was approved another session had switched that checkout
  to a feature branch — the command would have reset *that branch* and destroyed its pointer. The
  divergence had also already been fixed by its owner. One `git branch --show-current` caught it.

- 🔴 **AN AUDIT SUBAGENT'S LOAD GENERATORS LEAK, AND IT HAS NOW HAPPENED TWICE IN ONE SESSION —
  treat it as a property of the briefing, not as bad luck.** Both times a stress test spawned
  `while :; do :; done` shells and its cleanup (`kill %1 %2 …` once, `kill $LOADPIDS` the other)
  failed to reap them; they reparented to init and ran on. Measured: **74 orphans saturating ~11
  cores for 45 minutes**, then **20 more at ~87% CPU each for 6h17m — roughly 17 cores**. The first
  batch corrupted the very timing measurements the NEXT audit round then reported, so the cost is
  not just CPU: it silently poisons the evidence. 🔴 Two agents reported "no load generators
  spawned"/"all cleaned up" while these were running — a subagent's own cleanup claim is not
  evidence. **Sweep for them yourself at session end**: `ps -eo pid,ppid,comm` for `ppid==1` zsh,
  confirm each via `/proc/<pid>/cmdline`, kill by RESOLVED pid, never let a pattern reach `pkill -f`.
  🔴 The durable fix landed in the **`audit-pr` skill's briefing** (record spawned PIDs, reap by
  PID, sweep yourself afterwards, and give every container/scratch dir a UNIQUE name+port) — that is
  its home, not this queue, because it is a lesson about our own tooling rather than about clawgate.
- 🔴 **A TRUNCATED READ, WRITTEN DOWN, IS INDISTINGUISHABLE FROM A FACT — done TWICE this session,
  both times by me, both times in the instrument.** A `jq … | head -2` made the laptop look like it
  had no clawgate Stop hook registered (it does), and a `print(sorted(d.keys())[:8])` made
  session-manager look like it emits no timestamp — that one went into a 🔴 migration comment as
  "measured" and was wrong by 17 days (`ts` was added 2026-08-11). **Never slice the output of the
  command you are about to quote**, and re-run unsliced before a claim becomes a comment.
- 🔴 **A BOUND NEEDS BOTH HALVES, AND A FIXTURE DERIVED FROM THE CONSTANT PINS NOTHING.** Asserting
  only what a bound REJECTS let an off-by-one silently narrow it; asserting only what it ADMITS let
  the branch be deleted. And a reject-side fixture built as `strings.Repeat("0", MaxLen)` scales
  WITH the constant — raising it to 1,000,000,000 survived the whole suite while the test allocated
  1 GB fixtures and passed in 13s. Pin a literal value, and pin the constant itself.
- 🔴 **A GUARD ON A CALL'S PRESENCE IS NOT A GUARD ON ITS EFFECT.** Four successive attempts to pin
  ONE ordering requirement were each walked through: assert the helper (deleting the call site
  passed), assert the call exists (dropping the assignment, and moving it below the loop, both
  passed), assert SOME loop ranges over it (a decoy loop passed), OR across loops (a second unsorted
  loop passed). What held was quantifying over EVERY write loop and binding the check to the loop
  that actually writes.
- ⚠ **`gh pr view --json mergeable` answers `UNKNOWN` for a while after a push** — GitHub is still
  computing it. It is not a conflict; re-read it a few seconds later before acting.

- 🔴 **A MUTATION CAN REPORT `killed` AGAINST A TEST THAT WAS ALREADY RED — it dies for free,
  and the sweep looks clean.** Measured here: a 304-handling test was added and the sweep run
  immediately after; the mutant "died" because the test was failing *with and without* it. The
  shipped pattern `30[0-9]` had never excluded 304 at all. The missing control is the cheap
  one — **run the new test UNMUTATED first and watch it pass** — and a sweep's own `M0` control
  does not cover it if `M0` exercises a *different* test. Report the pair (green baseline,
  red mutant) or the kill means nothing.
- 🔴 **A GREEN MUTATION SWEEP IS A CLAIM ABOUT THE MUTATIONS YOU IMAGINED, AND MINE OMITTED THE
  WHOLE DIAGNOSTIC SURFACE.** An auditor mutated `COLLECT_RC=$?` to a literal `0` and it
  SURVIVED a fully green file, because every test asserted *that* a failure was reported and
  none asserted the *number*. The shipped code was byte-equivalent to an undetectable mutant.
  Across this work the sweeps totalled 24/24 killed and still found **less than the adversarial
  reader did**.
- 🔴 **`if ! cmd; then rc=$?` IS ALWAYS 0** — it reads the status of the *negated* pipeline,
  which is 0 exactly when the branch is taken. Worst on the likeliest path: `timeout` gives
  status 124 *and* empty stderr, so the log line carries no information whatsoever. Capture
  outside the condition (`set +e; cmd; rc=$?; set -e`).
- 🔴 **`HTTP < 400` IS NOT SUCCESS.** Without `-L`, a 3xx means curl returns the redirect and
  the server stores nothing — while the script logs "pushed" and exits 0. `000` and an EMPTY
  status do the same: `[ "" -ge 400 ]` prints "integer expected" and is FALSE, and **`set -e`
  does not fire for a test used as an `if` condition** (measured). Match 2xx explicitly.
- 🔴 **`env -i` IS STRICTER THAN systemd, so a probe built on it measures a condition the unit
  never runs in.** `Environment=` **ADDS to** the user-manager environment rather than replacing
  it. I diagnosed a live `TMUX_TMPDIR` bug this way and was wrong; what exposed it was
  `drift-check.service` having run fine for weeks under the same declared env — corroborating
  evidence disagreeing with my conclusion, not my own re-check. Use
  `systemd-run --user --wait --collect --pipe` to observe the real thing. ⚠ systemd expands
  `${VAR-DEFAULT}` in `ExecStart` itself and does not understand the `-` default, which yields
  false "unset" readings.
- 🔴 **TRIMMING A COPIED PATH LIST WITHOUT RUNNING THE CHILD IS HOW A SILENT ZERO GETS BORN.**
  Dropping `gawk` as "unused" broke the agent ledger: `agent_ledger.read_command` runs `awk 1`,
  `awk` lives ONLY in gawk (coreutils has none), and its `2>/dev/null; exit 0` swallows the
  error while the `echo` sentinel still prints — so the parser sees a well-formed ledger
  reporting ZERO. Measured 0 vs 34 of 45 windows carrying `runtime`. rc 0 and a plausible
  payload either way, and the laptop leg was unaffected, so the result looked *correct*.
  `gnugrep`/`iproute2` genuinely were unused; the difference was only ever discoverable by
  running the collector without each one.
- 🔴 **A PURE PROSE CHANGE CAN RED THE MERGE GATE.** `testlib/launcher_scan.py` scans top-level
  `scripts/` files as RAW TEXT with **no comment stripping**, so merely *naming* a hazardous
  binary in a comment registers that script as reaching it. A one-line explanatory comment put
  `test_no_real_launchers.py` red on both tiers. Describe the mechanism without spelling the
  binary.
- 🔴 **DO NOT RUN AN AUDIT AGENT AND THE GATING SANDBOX AT THE SAME TIME.**
  `test_live_cotenants_sees_another_process_in_the_repo` asserts a fresh tmp repo has no
  tenants; a concurrent agent running `git` in the repo reds it (`['90235:git'] == []`). I
  contaminated my own gate this way and it passed cleanly once the box was quiet — the
  "round N's leak corrupts round N+1's evidence" shape, applied to the gate instead of a
  timing probe.
- ⚠ **THE ZSH `MULTIOS` TRAP BIT AGAIN, in a session that had already read the rule.**
  `cmd 2>&1 >/dev/null | head` delivers **stdout**, not stderr, so a "no skew warning" reading
  was of the wrong stream. Give each stream its own file and read both.
- ⚠ **OBSOLETE AS OF `ZacxDev/homelab-infra#496` — kept because the REASONING below still
  governs, and because this doc carried the claim for weeks.** The Tmux tab now reads
  `GET /api/tmux/snapshot` and renders `receivedAt` as a per-host age badge that turns red past
  6 minutes (three missed feeder ticks). 🔴 **But a MERGED reader is not a RUNNING one** — the
  image pin has no Flux automation, so until the pin moves the sentence below is still literally
  true in production. Re-read it as: the staleness signal is now rendered, and is still not an
  ALARM — nothing pages anyone, a human has to look at the tab. The original text:
  **nothing read `GET /api/tmux/snapshot`** outside clawgate's own tests — no UI, no page,
  no script. So the read model's `receivedAt` staleness was **recorded and unread**, and any
  design that names it as a compensating control is naming a control that does not exist. The
  feeder's real alarm is its distinct non-zero exit codes landing in the user manager's
  failed-unit list, which `/standup` reads — that covers the codes and **NOT** a run that exits
  0 having achieved nothing, which is why the redirect and unmeasured-zero cases had to be
  fixed in the script rather than left to monitoring.
- ⚠ **The pusher's real per-run cost is ~4x the obvious estimate:** `session-manager --json`
  makes up to FOUR ssh invocations per remote host (list-panes, list-windows, the capture
  batch, the ledger read) with no `ControlMaster` — ~2,880 handshakes/day, not 720 — plus one
  ClickHouse query per run. `--lean` is the lever if that ever matters, at the cost of the
  verbatim/dumb-pipe property.

## How to verify

```bash
clawgatectl health                 # read the LIVE version; do not expect a number from this doc
clawgatectl attention ls           # expect JSON; `unknown command "attention"` + exit 0 = stale binary
```

**Is the idle reaper actually running?** (rank 1's answer — for ~9h it was NOT, and nothing said so)
```bash
# 1. it announced itself at boot. This line is unconditional; its ABSENCE is the alarm.
KUBECONFIG=$KC_WORKBENCH kubectl -n clawgate logs -l app=clawgate --tail=-1 \
  | grep 'attention reaper: sweeping every'
# 2. it has swept something (only logs when N>0, so an empty result is NOT a failure)
KUBECONFIG=$KC_WORKBENCH kubectl -n clawgate logs -l app=clawgate --tail=-1 | grep 'attention-reap:'
# 3. the queue is bounded. Count OPEN idle rows past the 4h window — a healthy
#    steady state is ~30, and anything climbing toward attentionPanelLimit (100) means
#    the sweep is not keeping up.
curl -s -H "Authorization: Bearer $CLAWGATE_HOOK_TOKEN" http://192.168.50.250:30302/api/attention \
  | python3 -c 'import json,sys,datetime as d
now=d.datetime.now(d.timezone.utc); rows=json.load(sys.stdin)
idle=[r for r in rows if r["kind"]=="idle"]
old=[r for r in idle if (now-d.datetime.fromisoformat(r["updatedAt"].replace("Z","+00:00"))).total_seconds()>4*3600]
print(f"open={len(rows)} idle={len(idle)} idle_past_4h={len(old)}")'
```
🔴 **Validate this instrument before quoting a zero** — `GET /api/attention` defaults to
`state=open`, so cross-check `?state=resolved` and `?state=all` and confirm open+resolved==all.
Measured 2026-08-27 pre-fix: open=59, resolved=31, all=90 — the filter discriminates.
- 🔴 **`clawgatectl` is built from the LOCAL `homelab-talos` tree**, so a behind checkout ships a
  binary missing verbs that prints help and **exits 0** under a plausible version label. Both hosts
  need `homelab-talos` current *before* a `home-manager switch`.
- **The feature's own proof** is end-to-end, not a unit test: trigger a real `AskUserQuestion`, then
  confirm a `kind=question priority=high` row appears via
  `curl -H "Authorization: Bearer $CLAWGATE_HOOK_TOKEN" http://192.168.50.250:30302/api/attention`
  — and that it sorts **above** the `idle` rows. Entry fields are camelCase (`sessionId`), not
  snake_case.
- **Per host**, confirm the hook that will actually run:
  `readlink -f "$(jq -r '.hooks.PermissionRequest[].hooks[].command' ~/.claude/settings.json | sed 's/^CLAUDE_HOST=[a-z]* //')"`
  must terminate inside `homelab-talos`, and that file must contain `raise_attention_question`.
- After any deploy: the pod is `Running` **and ready** — `kubectl -n clawgate get pods -l
  app=clawgate` lists a `Succeeded` leftover too, so a `.items[0]` jsonpath reports the wrong image.
## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **This doc spans TWO repos.** The design lives in `devrc`; all the code lives in
`homelab-talos` (remote `ZacxDev/homelab-infra`) under `containers/clawgate/`.
