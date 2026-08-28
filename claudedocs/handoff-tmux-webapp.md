---
---
# Handoff: tmux-webapp — 2026-08-26

## Goal
A **clawgate feature**: a webapp that visually organizes and gives live terminal interaction
with tmux sessions across workbench + laptop, with a composable view system agents can drive,
and an **attention queue** that surfaces sessions needing a human so Zach can jump straight in.

## Status

**Phase 1 (the attention queue) is SHIPPED, DEPLOYED and VERIFIED LIVE. Phases 2–6 are untouched.**

*How the design got here (carried forward):* settled 2026-08-26 across two rounds — a greenfield
session, then an audit that reopened four decisions — then a re-platform onto clawgate that resolved
three of them outright. The sections below from `## Platform` down are that design, still current.

- **clawgate 0.8.3 is live** — built, pushed (`sha256:c07436c3…`), pinned, Flux-reconciled,
  pod `Running`/ready. Verified via `clawgatectl health` through the LAN NodePort, not from the
  rollout's own success claim.
- **`ZacxDev/homelab-infra#422`** — the attention queue — merged as **`5a008e4f`**.
  **`#427`** (one-line `buildVersion` fix) merged as **`f2f8cb7e`**;
  **`#432`** was closed after fast-forwarding onto the branch to keep one reviewable PR.
- **`innovation-upstream/devrc#890`** — this doc's audited rewrite — merged as **`f53ef8a6`**.
- **Both hosts are on clawgatectl 0.8.3** with the `attention` verb, converged via `scripts/ship.sh`
  (both at `d3875d64`).
- **Verified through the real path, not inferred:** a genuine `AskUserQuestion` produced a
  `kind=question priority=high` entry sorted **above ten `idle` rows** — the priority-ordering fix
  demonstrated in production. Repeated from the laptop after its hook was fixed: entry id 149,
  `host=laptop`, hook `exit=0` with empty stdout (still defers). Test entry resolved afterwards.

**What the feature is:** attention entries (migration `0026`), kinds `question` (high) / `idle`
(low), raised by the `AskUserQuestion` path, the Stop hook, and `clawgatectl attention
raise|ls|resolve`; surfaced in an htmx tab and pushed via the pre-existing `POST /api/notify`.
🔴 An entry is **not** a decision object — no approve/deny, it carries a *destination*.

**The laptop had a five-month-old hook.** Its `PermissionRequest` hook was registered at
`~/.claude/clawgate-hook.sh` — a regular file (not a symlink; `readlink -f` resolves to itself),
byte-identical to commit `03efe4ed`, **clawgate 0.3.1**, mtime 2026-06-06. Its Stop hook *was* on
the repo path, so the idle path worked while the **question path was dead** — the exact use case
this feature exists for, silently, on one host. Repointed at the repo path via `jq` against a
timestamped backup (21 hooks / 7 keys preserved, one-line diff). The stale 0.3.1 copy is still on
disk, now referenced by nothing.

**Seven audit rounds ran; the ladder stopped when a round came back clean, never on a verdict.**
Three compounding defects would have made the queue bury the questions it exists to surface
(idle never reaped + priority absent from the sort + a 100-row limit taking the *oldest*).
**Nine separate instruments were caught measuring nothing**, including three harnesses built to
check earlier ones, one that scored an *unmutated* tree as SURVIVED, and a CI timer that had both
fire-and-forget tests passing on literally nothing. Two of those predated this work and were found
only because the **merged tree** was gated instead of the branch.

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
3. **Decide the terminal widget (audit finding A4 — still open).** clawgate vendors only two
   hand-written JS files (~3.7 KB) and no third-party bundle, so xterm.js would be the first.
   Recommendation on record: ship read-only `capture-pane` rendering first; if adopted, vendor and
   `go:embed` it, never a CDN — clawgate must work on an offline LAN.
4. **Host-side tmux agent** (phase 2). `devrc`, a new `systemd.user.services` unit on both hosts
   holding an **outbound** connection to clawgate. Source from `scripts/session-manager --json`,
   which already SSHes to the laptop and runs `list-panes -a` + `list-windows -a` on both hosts —
   do not write a second collector.
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
