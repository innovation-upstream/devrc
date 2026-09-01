---
---
# Handoff: tmux-webapp — 2026-08-26

## Goal
A **clawgate feature**: a webapp that visually organizes and gives live terminal interaction
with tmux sessions across workbench + laptop, with a composable view system agents can drive,
and an **attention queue** that surfaces sessions needing a human so Zach can jump straight in.

## Status

**Ranks 1–8, 13, 15 and 16 are ✅ DONE. Rank 14 is 🔵 IN FLIGHT as `ZacxDev/homelab-infra#632`
(open, CI running). Ranks 10, 11, 12 remain OPEN** (12 is clawgate task #463 and explicitly NOT
ours). The live-refresh work (`ZacxDev/homelab-infra#611`) is DONE, merged, and **deployed as
0.8.21**.

🔴 **DO NOT READ A VERSION FROM THIS DOC — `clawgatectl health` is the only authority.**
Measured 2026-09-01 21:51Z: server **0.8.21**, uptime 3007 s. It was 0.8.19 and 0.8.20 earlier the
same day, shipped by another session mid-work — which is the whole reason the number is derived
from the live pin and never from a doc.

✅ **RANK 8a — RE-MEASURED GREEN 2026-09-01 21:52Z, and still RECURRING.** Server 0.8.21, workbench
client 0.8.21, laptop client 0.8.21, and the cross-host round trip **moved a number**: `view create`
on the laptop → `view ls` on the workbench → `view rm`, `[] → 1 → []` (view id 8). 🔴 **A matching
label is NOT the check — the round trip is**, and this is the second consecutive session in which
the "both clients are stale again by construction" prediction was REFUTED on test. The item stays
RECURRING because nothing converges `homelab-talos`: re-run the round trip, never read this
paragraph. Source currency at that measurement: `~/workspace/homelab-talos` base clone **3 commits
behind `origin/trunk`** (repo-wide; not evaluated per-subtree).

✅ **RANK 6 REMAINS FULLY CLOSED.** `devrc#1056` merged; the sentinel is live end to end —
`GET /api/tmux/snapshot` returns `tmuxServerId` non-null for both hosts (`2509:1609459239` laptop,
`4025325:1785949442` workbench).

✅ **RANKS 8c AND 8d MERGED** — `ZacxDev/homelab-infra#591` (squash `d6dc52cf`) and `#592`
(squash `d2d2346e`), both verified on the merged tree at `d2d2346e` in BOTH tiers: go 20 ok / 0
FAIL, bats 67 ok / 0 not ok under the CI image, and `ALL LEGS PASS` on a re-run `clawgate-ci`.

🔴 **THE LIVE-REFRESH GAP IS CLOSED AND DEPLOYED — `#611`, squash `5d11d9a7`, live as 0.8.21.**
The server had broadcast `tmux.changed` on every snapshot ingest since #468 with **nothing
listening**, because #496 added a guard FORBIDDING the subscription on the premise that "clawgate
emits no such event". Git settles it: the broadcast (`32f49804`) predates the guard (`844a7350`),
so the premise was never true. Both panels now carry
`load, every 60s, sse:tmux.changed from:body, clawgate:resync from:body` — the poll retained as a
deadman, because SSE drops silently and a tab that stops updating with no signal is this
codebase's recurring failure mode.
**Verified live, not inferred:** the served `/tasks` page shows the trigger on both `#panel-tmux`
and `#panel-layout`; the SSE stream carried **2 `tmux.changed` events in a 180 s window** against
159 total events as a control.

**What the UI does today, for whoever asks next:**
- `/ui/tmux` — every window on both hosts, refreshing within ~1 s of a snapshot push. "What I have
  open is always there" is TRUE of this tab.
- `/ui/layout` — only panels you declare. A newly-opened window does **not** appear. Panels
  re-resolve against the live snapshot each read, so a closed window reports `missing` rather than
  silently rebinding.
- The remaining lag is the **2-minute host-side feeder** (`tmux-snapshot-push.timer` on the
  workbench, which collects BOTH hosts in one pass — so if it stops, both go stale together).
  Deliberately not reduced: `session-manager` makes up to 4 ssh invocations per remote host per
  run with no `ControlMaster`.
- Terminal WRITE is still `DISABLED (fail-closed)` — read-only until a token is provisioned.

### This session (2026-09-01, `/resume`)
- **Repo/branch:** `devrc` — this doc authored from a worktree off `origin/main` on
  `docs/handoff-tmux-webapp-rank14`, because the shared `~/workspace/devrc` checkout was on `main`,
  1 behind, and **dirty with another session's WIP** (`nix/programs/alacritty/default.nix`,
  `nix/system/apply-tmp-churn-retention.sh`, plus untracked `scripts/diagnose-nix-disk.sh` /
  `diagnose-disk-accounting.sh` / `output.txt` — the live `nix-disk-cleanup-1` claim).
  `homelab-talos` — work done in the PID-unique worktree `~/workspace/ht-r14-800677` off
  `origin/trunk` (`c9c6388d`).
- **DONE this session:** rank 14 built, mutation-verified and shipped as
  `ZacxDev/homelab-infra#632` (head `94cf920e`). Rank 8a re-measured green (above).
- 🔴 **NOT VERIFIED and it cannot be from here: #632's CI.** `tekton/clawgate-ci` and
  `tekton/clawgate-e2e` both **registered and were `pending`** at handoff. Neither verdict was read.
  The PR touches `containers/clawgate/**`, so the path filter DID fire — which is itself the thing
  #618/#625 wanted proven, and rank 16's live proof (`step-hook` showing TWO plan lines and
  `floor=34` / `floor=31`, never a single `1..67`) can be read off **this** run.
- **Branch verified after push, not trusted from the push message:** local HEAD ==
  `git ls-remote` == `94cf920e`, 1 commit ahead of `origin/trunk`, tree clean, no `autocommit:`
  fixture commits. `core.hooksPath` measured at push time was repo-LOCAL and pointed at
  `<repo>/.git/hooks` (sample-only, so nothing ran) — the documented volatile value.
- **Claim held:** `tmux-webapp-14` (`claim-work --release tmux-webapp-14` when #632 merges).
  The worktree `~/workspace/ht-r14-800677` is still present and clean; remove it after merge.
- **No `clawgate-task:` field is recorded for this session.** `clawgate_handoff.sh resolve` exited
  **5** — 0 tasks — with its positive control confirming the board was reachable and the token
  accepted. 🔴 That is NOT a clean bill of health: a wrong `CLAUDE_CODE_SESSION_ID` also answers 200
  with an empty array, so the reading cannot distinguish "touched no task" from "wrong id".

### How to verify rank 14 (the new guard), from `~/workspace/ht-r14-800677/containers/clawgate`
```bash
# 1. it passes on the unmutated tree (the M0 control — a kill means nothing without it)
go test ./internal/api/ -run '^TestNoPushDecisionIsReachedOnAnUnawaitedGoroutine$' -count=1 -v
# 2. it REDS on the closing condition. Wrap server.go:2160 and watch its OWN message:
#    s.notifyAgentRunning(agentName)
#      -> safeGo(s.logger, "x", func() { s.notifyAgentRunning(agentName) })
#    expect: "server.go:2160:52: BroadcastAgentChanged reaches notifyAgentRunning via safeGo"
# 3. 🔴 the discriminating control — the OLD ledger must stay GREEN under that same mutant:
go test ./internal/api/ -run '^TestEveryPushFanOutGoesThroughTheOneChokePoint$' -count=1 -v
```
🔴 **Build the gitignored CSS first or two unrelated tests red for that reason alone:**
`nix-shell -p tailwindcss --run "cd <clawgate> && tailwindcss -i web/css/input.css -o web/static/app.css --minify"`
— **`tailwindcss`, NOT `tailwindcss_4`.** Discriminator measured again this session: 41,992 B with
`.h-14` present is correct; `_4` gives 18,707 B with `.h-14` absent; the real cwd trap gives ~5 KB.

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
OPEN one. *History:* a 2026-08-26 renumbering superseded an earlier 1–7 list, so a slug minted
before that date may name a different item — do not renumber again without releasing live claims.

✅ **THE LOCK CAN NOW TELL 8a/8b/8c/8d APART — rank 13 landed 2026-09-01.**
`--slug-for … 8c` returns `tmux-webapp-8c`, distinct from `8d` and from `8`. The old advice
("prefer plain integer ranks") is retired. An unparseable rank is now rc 2 rather than a silent
drop, so a typo’d rank can no longer collapse two items onto one lock in silence.

1. ✅ **DONE 2026-08-27** — the idle reaper had never fired because it COULD NOT (`time.NewTicker`
   delivers its first tick one whole interval in). `ZacxDev/homelab-infra#457`, 0.8.7.
   forcing: none
2. ✅ **DONE 2026-08-27** — detached suggest POST, `ZacxDev/homelab-infra#451`. 8030ms → 22ms.
   forcing: none
3. ✅ **DONE 2026-08-28** — read-only `capture-pane` rendering (`devrc#992` +
   `ZacxDev/homelab-infra#496`), 0.8.10.
   forcing: none
4. ✅ **DONE 2026-08-28** — tmux snapshot ingest + host-side pusher (`ZacxDev/homelab-infra#468` +
   `devrc#974`), 0.8.8. Proof was an UNATTENDED tick.
   forcing: none
5. ✅ **DONE 2026-08-29 — `ZacxDev/homelab-infra#516`, squash `c8635976`.** `requireTerminalToken`:
   the ONLY fail-closed tier. 🔴 **The secret is still UNPROVISIONED and the surface boots DISABLED
   — correct, not a regression.** The SOPS age identity is on NEITHER host. Wired `optional: true`.
   **To arm it:** `clawgate gentoken` → `sops clusters/workbench/apps/clawgate/secrets.enc.yaml`.
   forcing: none
6. ✅ **FULLY DONE 2026-08-31 — `ZacxDev/homelab-infra#527` + `devrc#1056` (squash `ac64ccb4`).**
   Sentinel observed non-null end to end on both hosts. 🔴 **A PANEL STORES A DESCRIPTION, NOT A
   REFERENCE** — no field is both unique and stable across 79 live windows, so panels resolve
   against the live snapshot on every read. The laptop's `start_time` is Jan 2021: an OPAQUE
   EQUALITY TOKEN, never a timestamp.
   forcing: none
7. ✅ **DONE 2026-08-30 — `ZacxDev/homelab-infra#538`, squash `fb9b75e5`.** The htmx layout tab.
   🔴 **THE UI TIER CARRIES ONLY REVERSIBLE CONTROLS, BY CONSTRUCTION** — the destructive control
   was REMOVED, leaving `clawgatectl panel rm` as the only delete path.
   forcing: none
8. ✅ **DONE — all five sub-items closed.** 8a RECURRING (measured green 2026-08-31), 8b/8e done,
   8c `ZacxDev/homelab-infra#591` squash `d6dc52cf`, 8d `#592` squash `d2d2346e`. Both verified on
   the merged tree in BOTH tiers (go 20 ok/0 FAIL; bats 67 ok/0 not ok; `ALL LEGS PASS` on
   `clawgate-ci-rerun-z5wj5`). Audited post-merge — findings became ranks 14–16, not a revert.
   forcing: gate — `clawgate-e2e` was green through all four of #538's audit rounds while running
   ZERO specs touching layout. 8b and 8e closed both halves of that.
9. **There is no rank 9** — a previous revision listed one and it was never a work item (the
   operator confirmed 2026-08-27 that MEMORY.md is not used here).
   forcing: none
10. **Two guard gaps #457's ladder left open deliberately, both scaffolding-scope.**
    `RunSweeper`'s ticker survives `NewTicker`→`NewTimer` against the whole api package;
    `RunRetention` and `RunReconciler` deserve the same check (pattern:
    `TestRunAttentionReapTicksOnTheIntervalItWasGiven`). The `main()` wiring ledger pins syntax,
    not reachability — NOT closable statically.
    forcing: none
11. **The archive drawer loses its `open` after every write-triggered swap.** KNOWN, UNFIXED, noted
    in-code at `layoutArchivedDrawer`. The fix is the `taskCardScript` treatment (a once-bound
    listener re-applying `open` after settle). 🔴 `layout.spec.ts` deliberately does NOT pin the
    drawer's `open` — mutant H is asserted to PASS, so the spec will not red when someone fixes it;
    but a listener that SWALLOWS the summary's click is mutant PD, which it DOES red. Read both.
    forcing: none
12. **NOT MINE, RECORDED SO IT IS NOT LOST: 24% of the tasks board cannot be scrolled to — clawgate
    task #463.** Measured live on 0.8.19 at 1280x720: `scrollHeight` 21,997 vs `innerHeight` 720;
    at maxScroll the last card sits at `rect.top +6,510`, `inViewport:false` — **60 of 248 cards
    unreachable**. Full detail in the `clawgate` subsystem-index entry.
    forcing: incident — a shipped, measured defect on the version live on both hosts, filed as
    task #463 by the session that found it.
13. ✅ **DONE 2026-09-01.** `--slug-for` discarded a lettered sub-rank instead of rejecting it, so
    `8c`, `8d` and every lettered sub-rank of every rank minted ONE slug — and the collision was
    reported as **rc 12 “ALREADY YOURS, carry on”**, the one answer that means PROCEED. Measured
    before the fix on this doc: `8c` and `8d` both printed `tmux-webapp`.
    🔴 **The pattern was widened AND the class closed:** an unparseable rank (`8-c`, `8.1`,
    `part2`) is now a usage error, not a silent drop, because the hazard was an ignored rank rather
    than the spelling `8c`. The rank is also case-folded (`8C` == `8c`; `validate_slug` is
    lowercase-only, so unfolded it was rc 2 at claim time). 7 of 8 new cases watched RED on the
    pre-change `origin/main`; the 8th is labelled in place as an invariant guard, not counted as
    regression coverage.
    forcing: none
14. 🔵 **IN FLIGHT: `ZacxDev/homelab-infra#632`** (opened 2026-09-01, `fix/push-fanout-ledger-pins-sync-precondition`,
    head `94cf920e`, test-only, +390 in one file). Do NOT re-do it; read the PR, then merge it once
    `clawgate-ci` + `clawgate-e2e` report. Both registered and were RUNNING at handoff — no verdict
    was read, so nothing here claims one.
    **What landed:** `push_fanout_ledger_test.go` now pins the RELATIONSHIP, not a call-site count.
    Both sets are DERIVED, never spelled: push-deciders = transitive callers of `goPushBroadcast`
    (39 on trunk today), spawners = any function with a func-typed parameter and a `go` in its body
    (so a sibling of `safeGo` is covered the day it is written).
    **Measured in the worktree, not carried over from the audit that filed this:** bug = flip
    `notifyAgentRunning`'s running-status skip, so a task-linked NON-running agent pushes.
    D1 (bug alone) → `TestProvisioningPushSkipsNonRunning` **20/20 caught**, new guard silent
    (correct — not a scheduling change). D2 (bug + the call wrapped in `safeGo`) → behavioural
    **1/20 caught**, new guard **20/20 red**. 🔴 **The doc previously recorded 0/20; the measured
    value here is 1/20** — same direction, and the residual 1 is timing luck, because D2 converts a
    deterministic detector into a race. The guard's own line names the closing condition verbatim:
    `server.go:2160:52: BroadcastAgentChanged reaches notifyAgentRunning via safeGo`.
    🔴 **The decisive control: `TestEveryPushFanOutGoesThroughTheOneChokePoint` stays GREEN under
    BOTH D1 and D2.** Wrapping a CALLER moves no `push.Broadcast` call site, so the old ledger
    cannot see this — that is what made it a real gap rather than a duplicate guard.
    Mutation battery **6/6 killed**, M0 control green, restore verified by hash (M1 safeGo-wrap /
    M2 bare `go` → guard red, old ledger green; M3 seed renamed → coverage floor; M4 spawner
    detection blinded → `safeGo` floor; M5/M6 finder narrowed → finder control). A 7th mutant
    (dropping the status clause outright) was scored **INVALID, not a kill** — it left the `agents`
    import unused and did not compile.
    Anti-vacuity in the file: a 9-name coverage floor, a `safeGo` spawner floor, and a
    positive/negative control with one case per boundary claimed plus three it must stay silent on
    — including `goPushBroadcast`'s own goroutine, flagging which would make the guard permanently
    red. Known limits are stated in the file: it follows CALLS not function VALUES (so
    `notifyTaskCreated`, which hands `flushCreatedTasks` to a timer, is correctly absent — that path
    is async by design and the barrier never covered it), it is lexical and single-package, and
    names are not receiver-qualified.
    Full module at that tree: `go build` rc 0, `go vet` rc 0, `go test ./...` rc 0, **20 ok / 0
    FAIL** counted from the runner's own lines.
    forcing: regression — measured, the conversion took one bug class from a 20/20 detector to a
    1/20 one, and until #632 merges the guard that would catch the enabling refactor is unmerged.
15. ✅ **DONE 2026-09-01 — `ZacxDev/homelab-infra#625`, squash `0dd62cd9`.** All three parts.
    (a) KNOWN LIMIT 4 said no test body uses a heredoc; two do, and `/^}/ { inbody = 0 }` is shared
    by BOTH scanners, so a column-0 `}` in one blinds them for the rest of the body while EXAMINED
    stays positive and the BODIES cross-check still agrees. Measured on a probe of that shape:
    pre-change `EXAMINED 3` and **ZERO violations**; post-change the real violation is caught and
    the heredoc's own `! grep` correctly ignored as data. Heredoc tracking added at all FOUR sites
    (2 scanners x 2 suites — no shared library) from one string, asserted byte-identical.
    🔴 **Two traps it cost, both now in the code:** the scanner may not SPELL its own redirect
    operator anywhere in its program (written literally in its own regex it opened a heredoc tagged
    `A` on that line and ran blind to EOF — 33 bodies against 35 by grep; the pattern is assembled
    in `BEGIN` now), and prose describing the operator needs an explicit comment clause — which
    **SURVIVED** a green run until a fixture body pinned it, exactly the rule-no-mutant-can-kill this
    file's own header warns against.
    (b) KNOWN LIMIT 5 ENFORCED. Measured on bats 1.11.1: a function-style test with the trailing
    marker IS collected and run (2), without it is not (1). Neither scanner sees one and the BODIES
    cross-check is structurally blind (both sides count `^@test `), so they agree at the same wrong
    number. Detector carries its own positive control.
    (c) Both surviving mutants killed — one body each for egrep/fgrep/zgrep, and a `grepzilla` body
    for the TRAILING word-boundary class (only the leading one was pinned, by `pgrep`).
    Probe contract re-derived from a real run: **9 violations / BODIES 14 / EXAMINED 20**. Mutation
    battery **6/6 killed**, M0 control green, file restored by hash.
    forcing: none
16. ✅ **DONE 2026-09-01 — `ZacxDev/homelab-infra#618`, squash `b5992890`.** The `hook` leg read
    only `bats`' exit code, and `bats` on a file with zero `@test` bodies prints `1..0` and exits
    **0**. Each suite now runs separately against its own floor (34 / 31, from `bats --count`
    measuring 35 / 32 in the leg's own image). Measured with the mutation verified applied:
    pre-change `hook leg rc=0` verdict `pass`; post-change `rc=1` verdict `fail` — with `bats`
    itself exiting 0 in BOTH, so the floor is the only thing that spoke. Floors pinned by
    `scripts/tests/test_clawgate_ci_hook_floor.py` (extracts the script FROM the manifest;
    6/6 mutants killed by name). The leg's stale “11 tests” / “18 + 11” comments are gone.
    ⚠ **Deployed and verified against the DEPLOYED artifact, but no PipelineRun has run it yet** —
    `clawgate-ci` is path-filtered on `containers/clawgate/**` and this change touched neither, so
    nothing fired. The next PR touching that path is the end-to-end check: `step-hook` must show
    TWO plan lines and `floor=34` / `floor=31`. A single `1..67` means the Task did not reconcile.
    forcing: none

## Open investigations — live diagnosis state

🔴 **THE WORKBENCH STILL CANNOT PULL FROM `docker.io`. BUILD CLAWGATE IMAGES ON THE LAPTOP.**
Measured 2026-08-29, and the state is subtler than "it is broken":

- **Root cause is the LAN router, not this host.** `192.168.50.1` answers
  `registry-1.docker.io` with a PINNED 8-address set carrying a TTL of **~42,048,000 s = 487
  days** (a normal docker.io TTL is 30–60 s). Of those eight, 4 serve the correct
  `*.docker.com` certificate, **2 serve certificates for unrelated third-party sites** (old EC2
  elastic IPs Docker released and AWS reassigned), and 2 do not complete a handshake. Every
  connection round-robins, so ~half of all pulls fail TLS verification **against a different
  wrong hostname each time** — which is what makes it read as interception rather than staleness.
- **Flushing does not help**: restarting dnsmasq re-asks the router and gets the same pinned
  record back. The fix has to be "do not ask the router for this name."
- **The host-side bypass is WRITTEN AND HALF-APPLIED.** `devrc/nix/system/apply-dnsmasq-docker-io-pin.sh`
  adds `"/docker.io/1.1.1.1"` ahead of the router in `services.dnsmasq.servers`. 🔴 **The
  `/etc/nixos/configuration.nix` edit IS in place (mtime 12:27) but the rebuild that activates it
  has NOT run** — measured directly, not inferred: the **running** unit's config
  (`-C /nix/store/…-dnsmasq.conf`) contains only `server=192.168.50.1` and `server=1.1.1.1`, with
  **no** `server=/docker.io/` line, and `dig` still returns the 487-day record while `dig @1.1.1.1`
  returns a disjoint set with a 33 s TTL. **`sudo nixos-rebuild switch` is the remaining step**; I
  cannot sudo. The script is idempotent and refuses if the config has drifted.
- **Router-side is the real repair** and is untouched: clearing the stale entry on
  `192.168.50.1` fixes every machine on the LAN. The script fixes one hostname on one host.

⚠ **The laptop build path has a SECOND credential leg that is easy to misread as a broken
build.** `DOCKER_HOST=ssh://zach@10.42.0.100 docker build …` works, but **`docker push` sends
registry auth from the LOCAL client**, and the workbench's `~/.docker/config.json` has **no
`harbor.homelab.lan` entry** (only `127.0.0.1:30022` and `ghcr.io`) while the laptop's does. So
the push failed `unauthorized to access repository: library/clawgate`, which reads like a Harbor
permissions problem and is not. **Run the push ON the laptop** —
`ssh zach@10.42.0.100 'docker push harbor.homelab.lan/library/clawgate:<v>'` — the image is
already on that daemon from the build.

⚠ **The kickoff for this session pointed at an "Open investigations" section of THIS doc that
did not exist.** The docker.io diagnosis lived only in the header comment of an **untracked**
`nix/system/apply-dnsmasq-docker-io-pin.sh` — one routine `checkout` from silent deletion. Both
are fixed: the script is committed, and this section exists.

### `devrc#1056` (the tmux server sentinel) is merged-blocked, and the reason CHANGED mid-session
- **Symptom:** the producer half of rank 6 — `session-manager` publishing `tmux_server_id` per host —
  is complete, mutation-swept 8/8, verified against real tmux on both hosts (52/52 and 28/28 rows
  parsed through the real ssh path), and has been sitting open since 2026-08-29.
- **Observed (with values):** first blocker was devrc `main` red on
  `test_espanso_detect.py::test_live_existing_resolutions_not_made_ambiguous`, asserting
  `{'ask': (':acq', None, [':dacq', ':acq']), 'clarify': (':acq', None, [':dacq', ':acq'])}`.
  Discriminating control run: **the identical assertion fails on a clean `origin/main`** in an
  unrelated checkout, so it is not this diff. Claimed by another session as
  `espanso-ask-tiebreak-main-red`. **As of 2026-08-30 06:30Z the checks read
  `tekton/devrc-nodetests=ERROR` and `tekton/devrc-pytests=ERROR`, not `failure`** — per devrc's own
  CLAUDE.md that distinction matters: `error` means the gate stopped before a leg reported, i.e. a
  broken gate rather than a bad change.
- **Ruled out:** this PR's own diff. It touches `scripts/session-manager` and its test only; the
  espanso test is in `scripts/collector/keylog/`, and the full suite was green on the branch apart
  from that one pre-existing failure (`failed=1` of 18,713 collected).
- **Leading hypothesis:** two unrelated blockers in sequence — a real main-red (someone else's, being
  fixed) followed by an infrastructure error on the gate itself.
- **Next probe:** `gh pr checks 1056 --repo innovation-upstream/devrc` and, if still ERROR, read the
  PipelineRun rather than the status — a check posted as `error` with `COULD NOT RUN: <leg>` is a
  broken gate and must not be debugged against the diff.
- 🔴 **Consequence while it stays open:** `tmux_server_id` is NULL on both hosts, so the resolver's
  window-id tier disables itself (unknown sentinel ≠ agreement) and panels resolve by
  codename/name. Verified live — that is the designed degradation, not a fault. **Claim
  `tmux-webapp-6` is deliberately still held** until this lands and the sentinel is observed
  non-null end to end.

### The layout tab has never been exercised in a browser
- **Symptom:** rank 7 is a UI feature whose every claim rests on Go tests and rendered-HTML
  assertions.
- **Observed:** `clawgate-e2e` is GREEN on the merged tip — `stats: passed=118 failed=0 skipped=2
  flaky=0 rc=0` — but it ran 20 spec files (`requests`, `tasks`, `agent-chat`, `operator`,
  `routing`, `responsive`, …) and **`grep -ic layout` over the whole run log returns 0**. That green
  says nothing about this feature.
- **Ruled out:** "e2e covers it" — measured above. Also ruled out: that the run hid a failure; an
  earlier read of "118 failed" was a grep straddling the fields of `passed=118 failed=0`.
- **Leading hypothesis:** no hypothesis needed — it is simply uncovered. The specific risks are the
  htmx swap whose target contains the issuing button (`hx-disabled-elt="this"` has four precedents
  here, so low but unmeasured), a real axe scan on the new fragment, and rank 11's drawer behaviour.
- **Next probe:** write `containers/clawgate/e2e/tests/layout.spec.ts` and run `make e2e`. 🔴 **COUNT
  what runs** — without Docker, `test.skip` on `!dockerAvailable()` leaves 11 of 18 spec files and
  goes green.

### ✅ RESOLVED — `clawgate-e2e` DID register on #566, and it PASSED
🔴 **This CORRECTS the block below, which is left in place because the mistake is the lesson.**
`tekton/clawgate-e2e` → **success, "clawgate e2e passed — 124 tests, 2 skipped"** on head
`88d53d0d`. It had simply not been *scheduled yet* when I read the check list minutes after
opening the PR. **I read an absence as a fact about the pipeline when it was a fact about the
CLOCK** — the rival mechanism ("not started yet") was never named, and an empty result cannot
distinguish the two. Rank 8b's closing condition IS met.

⚠ **The same reading gives rank 8e its number, from BOTH tiers, and they agree:** CI reported
`124 tests, 2 skipped`; the local full run reported `120 passed / 4 flaky / 2 skipped` in 21.9m,
and the pipeline's own rule is `ran_ok = passed + flaky` = **124**. Applying the existing
derivation ratio (110/118 ≈ 93.2%) gives **`MIN_PASSED: 115`**. The 4 flaky were
`task-comment-delete.spec.ts:44`, `tasks.spec.ts:523`, `tasks.spec.ts:555` and
`tasks-mobile.spec.ts:854` — all pre-existing, none reachable from an e2e-only diff.

### ✅ RESOLVED — `clawgate-ci` "FAILED: go" on #566 is a NETWORK failure, not a code failure
- **Observed (with values):** the pipeline run is `clawgate-ci-nndsh` in ns `tekton-ci`, param
  `revision=88d53d0d…` (MY sha — so it could NOT be dismissed as someone else's), failing step
  `step-verdict` exit 1 over `go fail / extension pass / hook pass`. The `step-go` log ends with
  **5×** `net/http: TLS handshake timeout` fetching `github.com/jackc/pgx/v5@v5.10.0`,
  `github.com/spf13/cobra@v1.10.2` and `github.com/coder/websocket@v1.8.14` from
  `proxy.golang.org`, then `go leg rc=1`. Not one compile or test error.
- **Ruled out:** my diff. It contains **zero** Go files (`git show --name-only` on the commit), and
  the same tree is green locally: `go build ./...` rc 0, `go vet ./...` rc 0 and no output,
  `go test ./...` **20 ok packages / 0 FAIL** (the 20 is the positive control — an empty filtered
  output alone would not have proved the runner ran).
- **How to read it:** this is the same family as the documented docker.io DNS poisoning on this
  LAN — module fetches over TLS timing out. **Re-run the pipeline; do not debug the diff.**
- ⚠ `tekton/gitops-validate` on the same PR says **`COULD NOT RUN: scripts-tests`**, which this
  repo's CLAUDE.md defines as a gate that stopped before a leg reported — also not a verdict on
  the change.

### `clawgate-e2e` has not registered as a check on `ZacxDev/homelab-infra#566`
⚠ **SUPERSEDED — see the RESOLVED block immediately above. The conclusion was WRONG.** Kept
verbatim because the failure mode is worth recognising: an absent check and a not-yet-scheduled
check are byte-identical in `gh pr checks`, and I diagnosed the first without naming the second.
- **Symptom + exact repro:** `gh pr checks 566 --repo ZacxDev/homelab-infra` lists **only**
  `tekton/ux-audit-clawgate` (PENDING). The suite this PR exists to extend is not among them.
- **Observed (with values):** `gh pr view 566 --json statusCheckRollup` → exactly one row,
  `tekton/ux-audit-clawgate  PENDING`. Contrast devrc#1056, where both `tekton/devrc-pytests` and
  `tekton/devrc-nodetests` appear within minutes of a push.
- **Ruled out:** the ERROR-state class that hit devrc#1056 — that showed the checks PRESENT with
  conclusion `ERROR`. Here the check is ABSENT, which is a different failure with a different fix
  (a fresh push clears an ERROR; it will not conjure a check that never triggers).
- **Leading hypothesis:** the `clawgate-e2e` trigger is path- or event-filtered and either has not
  fired yet or does not match a PR touching only `containers/clawgate/e2e/**`. UNPROVEN.
- **Next probe:**
  ```bash
  grep -n 'interceptor\|filter\|cel\|clawgate-e2e' \
    ~/workspace/homelab-talos/clusters/homelab/apps/tekton-pipelines/triggers/clawgate-e2e-pipeline.yaml
  gh pr checks 566 --repo ZacxDev/homelab-infra
  ```
- 🔴 **Rank 8b's closing condition is "green in `clawgate-e2e`", so a check that never runs does
  NOT satisfy it** — an absent check reads as "nothing to see", which is the opposite of what it is.

### `MIN_PASSED` in the e2e pipeline is stale, and it was deliberately NOT re-derived
- **Symptom + exact repro:** `clusters/homelab/apps/tekton-pipelines/triggers/clawgate-e2e-pipeline.yaml`
  line ~550 sets `MIN_PASSED: "110"`, derived from a CI run measured at **118 passed / 2 skipped**.
  A local full run on the 8b branch collects **126**.
- **Observed (with values):** `Running 126 tests using 1 worker` (local `make e2e`, no filter; 31
  passed / 0 failed at the point this handoff was written — the run was still going). The pipeline
  header says: *"🔴 BOTH ARE MEASURED AND WILL DRIFT. Re-derive them when the suite changes size —
  from a real run, the way these were, not by adjusting until green."*
- **Ruled out:** raising it from the **local** number. The dev-host tier and the CI tier are
  different environments with different skip sets; a floor CI cannot meet is the permanently-red
  gate this repo warns about, which is strictly worse than a loose one.
- **Leading hypothesis:** the floor was ALREADY stale before 8b — 126 collected locally against a
  118-derived floor means trunk grew by ~6 tests that nobody re-derived for.
- **Next probe:** read passed/skipped off the **first `clawgate-e2e` run that includes
  `layout.spec.ts`**, then set `MIN_PASSED` to ~93% of it, the way the existing comment derives 110
  from 118. It is a `-lt` floor, so nothing is broken meanwhile.

### ⚠ SUPERSEDED — `test_subsystem_store_api.py` is FLAKY on `main`, and nothing is fixing it
🔴 **BOTH HALVES OF THIS HEADING ARE NOW FALSE, AND ITS "next probe" SENDS YOU DOWN A REFUTED
THREAD. Read `### ✅ DIAGNOSED` below before spending a minute on anything here.** The mechanism is
fsync latency, not seed/ordering, and `fix/xdist-parametrize-values-deterministic` is not the thread
to pull. Kept verbatim because the eliminations below are still true and still useful — it is the
FRAMING that was wrong, which is this doc's own documented failure mode for an open-investigation
block.
- **Symptom + exact repro:** `nix develop ~/workspace/devrc -c python3 -m pytest
  scripts/tests/test_subsystem_store_api.py -q` on a CLEAN `main` checkout.
- **Observed (with values):** `1 failed, 640 passed in 321.94s` — failing
  `TestEnumerationChannelsAreClosed::test_a_scope_FILTERED_snapshot_of_a_denied_scope_ships_nothing`.
  CI on devrc#1056 failed a **DIFFERENT** case in the same file:
  `TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-kkkk…LLLL]`.
  Two different tests across two runs ⇒ the failure moves.
- **Ruled out:** devrc#1056 as the cause — it touches only `scripts/session-manager` and
  `scripts/tests/test_session_manager.py`, neither of which this file imports, and its own 691
  tests pass on the rebased tree. Also ruled out for #1101, which changed ONE markdown file and
  was blocked by it once, then passed on a re-run.
- **Leading hypothesis:** non-deterministic parametrize values (the `[record0-kkkk…LLLL]` id shape
  is generated, not literal). A branch `fix/xdist-parametrize-values-deterministic` EXISTS in a
  local worktree — **but there is NO open PR for it** (`gh pr list --state all` matched nothing on
  `xdist|parametrize|determin`).
- 🔴 **Why this matters more here than elsewhere:** devrc is the one repo in this thread that
  genuinely enforces required checks, with `enforce_admins: true`. An intermittent failure that
  picks a different case each run will keep blocking arbitrary PRs — it blocked two of mine today.
- **Next probe:** `for i in 1 2 3; do nix develop ~/workspace/devrc -c python3 -m pytest
  scripts/tests/test_subsystem_store_api.py -q -p no:cacheprovider; done` on clean `main`, and
  record WHICH case fails each time. If the case moves, seed determinism is confirmed; then open a
  PR for the existing branch rather than starting fresh.

### ⚠ `tekton/clawgate-ci` has never once completed on the 8b branch, for two different reasons
- **Observed (with values):** on `88d53d0d`, `FAILED: go` whose `step-go` log ends in **5×**
  `net/http: TLS handshake timeout` fetching pgx/cobra/coder-websocket from `proxy.golang.org`,
  then `go leg rc=1` — not one compile or test error. On the final sha `c617bdd5`,
  `COULD NOT RUN: clawgate-ci stopped before any leg reported`.
- **Ruled out:** the diff. It contains **zero** Go files, and the same tree is green locally:
  `go build ./...` rc 0, `go vet ./...` rc 0 silent, `go test ./...` **20 ok packages / 0 FAIL**
  (the 20 is the positive control — an empty filtered output would not have proved the runner ran).
- **Leading hypothesis:** cluster congestion/preemption, the documented `tekton` skill class.
  `gitops-validate` on the same sha went from `COULD NOT RUN` to **all 8 legs passed**, which is
  the same transience from the other direction.
- **Consequence, stated plainly:** #566 was merged with `clawgate-ci` red. That is defensible here —
  homelab-infra returns **403** on branch protection so its checks are DETECTORS, not gates; the
  check that covers this change (`clawgate-e2e`) was green on the final sha; and the Go leg was
  verified locally with controls. But "clawgate-ci is green for this change" is a claim NOBODY can
  make, and it should not be inferred later from the merge.

### 🔴 `ZacxDev/homelab-infra#591` (rank 8c) — the CHANGE looks right, the EVIDENCE for it does not
- **Symptom + exact repro:** the subagent that wrote #591 reported an 8-row table of "mutant → red
  with this message". Re-running its site-3 mutant reproduces a **different failure**, in a
  different place, for a different reason.
- **Observed (with values):** in `/home/zach/workspace/ht-8c-574240/containers/clawgate`, mutating
  `internal/api/push_task.go` (exactly 1 match)
  `if a.Status != agents.StatusRunning || a.NoteID == nil {` → `if a.Status != agents.StatusRunning {`
  builds clean (rc 0), then
  `go test ./internal/api/ -run TestProvisioningPushSkipsTasklessAndOperator -count=1` → rc 1 with:
  ```
  --- FAIL: TestProvisioningPushSkipsTasklessAndOperator (0.00s)
  panic: runtime error: invalid memory address or nil pointer dereference
   ... api.(*Server).notifyAgentRunning ... push_task.go:192
   ... api.(*Server).BroadcastAgentChanged ... server.go:2160
   ... api.TestProvisioningPushSkipsTasklessAndOperator ... push_task_test.go:279
  ```
  `push_task.go:192` is `noteID := *a.NoteID`. With `NoteID: nil` seeded, removing the nil half of
  the guard nil-derefs **before** `pushTask` is ever reached. The panic lands at test line **279**
  (`srv.BroadcastAgentChanged(...)`), one line ABOVE the `awaitPushesSettled(t, srv)` on **280**.
- **Ruled out:** that the barrier itself is broken. `awaitPushesSettled` is sound by construction —
  `s.pushInFlight.Add(1)` is at `internal/api/server.go:2010` on the **caller's** goroutine with
  `defer s.pushInFlight.Done()` inside the spawned goroutine, and `goPushBroadcast` is the sole
  spawn site. It also already has its own in-repo guard,
  `TestAwaitPushesSettledWaitsForTheFanOutToFinish`, driven by a `slowPusher` that blocks until
  released. Also ruled out: that the suite is red — independent full run in that worktree is
  **20 `^ok` / 0 `^FAIL`, `go test`'s own exit 0** (counted from the runner's own lines, not piped).
- **Leading hypothesis:** the agent applied a different patch than the one its report describes, OR
  it scored the site from the panic without reading which line failed. Either way the site-3 row is
  not evidence, and 🔴 **that same mutant was the basis for the headline "the barrier is
  load-bearing" control** ("mutant 3 + barrier deleted ⇒ passes 25/25"), which a panicking test
  cannot have produced.
- **Next probe:** the agent has been sent back for (a) the literal patch text it actually applied,
  (b) a site-3 mutant that reaches `len(mp.callsOfType("task")) != 0` instead of panicking upstream
  — removing the dedupe, or admitting a task-less agent while keeping the deref safe, are the
  shapes, (c) the barrier control re-run on that corrected mutant, and (d) **a re-check of the other
  seven sites for the same failure mode** — for each, whether the red came from the test's own
  `t.Fatalf` or from a panic/compile error upstream of the barrier. Site 8
  (`TestSessionCommentDoesNotPush`) at least carries its own non-vacuous control (the machine
  endpoint on the same server DOES push), but note it also has an earlier `if d.armed()` guard at
  test line ~532 that would fire BEFORE the barrier for some mutant shapes — the classic
  "an earlier check always wins so the guard never executes" trap.

### ✅ RESOLVED — `ZacxDev/homelab-infra#591`'s evidence was UNDER-REPORTED, not wrong
🔴 **This CORRECTS the block above, which is left in place because the failure mode is the lesson.**
The site-3 mutant was **two hunks**, and the report described only the first. The omitted hunk was:
```go
-	noteID := *a.NoteID
+	var noteID int64 // MUTANT: nil-safe deref so M3 fails on the ASSERTION, not a panic
+	if a.NoteID != nil {
+		noteID = *a.NoteID
+	}
```
The mutant's own comment names the panic trap, so the author had designed around it and then
under-described the patch. My single-hunk re-run was therefore a **different mutant** — a real
INVALID one — and the panic I measured was correct about the patch I applied and not about theirs.

**Re-verified independently, on the corrected single-hunk form** (which keeps the deref reachable
by assigning the by-value local copy: `agents.Store.GetByName` returns `Agent`, not `*Agent`):
```go
-	if a.Status != agents.StatusRunning || a.NoteID == nil {
+	if a.Status != agents.StatusRunning {
 		return
 	}
+	if a.NoteID == nil {
+		a.NoteID = new(int64) // local copy; keeps the deref below reachable
+	}
```
- mutant **+** barrier → `push_task_test.go:282: task-less agent fired 1 provisioning push(es),
  want 0` — the test's own `t.Fatalf`, **0 panics** (`grep -c '^panic:'` = 0).
- mutant **−** barrier (the `awaitPushesSettled` line deleted from that test only) → `ok`, `-count=50`.
- 🔴 **Positive control for that rc 0**, because a `-run` filter matching nothing also exits 0:
  the same filter on the unmutated tree with `-v` gives **50 `--- PASS`**, and no
  `no tests to run` warning appears in either log. So the assertion is genuinely BLIND without the
  barrier, and the barrier is what catches the mutant.

All eight sites were re-classified as assertion-vs-panic-vs-compile-error: **eight died at their
own `t.Fatalf`, zero panics, zero compile failures.** Site 3 was the only one carrying this hazard,
because its guard is the only one that also protects a pointer deref. Commit amended to
`d687fcaa`, force-pushed with `--force-with-lease`; local HEAD == `origin`'s.

### ✅ RESOLVED — ranks 8c and 8d are MERGED and verified on the MERGED TREE
- **8c** — `ZacxDev/homelab-infra#591`, squash **`d6dc52cf`**. All four checks were green.
- **8d** — `ZacxDev/homelab-infra#592`, squash **`d2d2346e`**.
- **Verified on `origin/trunk` at `d2d2346e`, i.e. the tree the merges created, not either branch:**
  Go `20 ^ok / 0 ^FAIL` (rc read from `go test` itself), bats `67 ok / 0 not ok` (rc read from
  `bats` itself) with the new guard passing as `ok 35` and `ok 67` in the two suites.
- **Content-verified, never by ancestry** (a squash merge makes `--is-ancestor` false forever):
  0 `time.Sleep` left in the two Go test files on trunk; `scan_inert_negated_greps` present 5x in
  each bats suite.

🔴 **#592's `clawgate-ci` was RED at merge time and it was NOT a verdict on the change.** The run
(`clawgate-ci-czshq`, rev `751aabaa`) hit **`TaskRunTimeout`** at the task's 25m budget, consumed
in the **`go`** step, which killed `go`/`extension`/`hook`/`verdict` together — so the check text
read `COULD NOT RUN: clawgate-ci stopped before any leg reported`. Attribution, stated with its
evidence: #592's diff contains **zero Go files**, so it cannot have slowed the `go` step, and
**#591 — which does touch Go — passed the same pipeline nine minutes later** (`clawgate-ci-hsdlk`,
rev `d687fcaa`, Succeeded). `ZacxDev/homelab-infra#572` (raise that budget) is **MERGED** — 2026-08-31 18:36Z. Read the
`TaskRunTimeout` investigation below before recording it as the fix: it addresses one of the two
causes.
⚠ **The leg that never ran was `hook` — the one #592 exists to exercise** — so merging on the
"COULD NOT RUN means broken gate" convention alone would have shipped it with zero CI coverage of
the thing it changed. It was merged on a **local reproduction of that exact leg instead**: the same
`docker.io/bats/bats:1.11.1` image the pipeline uses, run on the LAPTOP (the workbench cannot pull
docker.io), `BATS_RC=0`, 67/0. That closed the agent's one self-declared unverified gap
(it had run bats 1.14.0).

### ✅ RESOLVED — the MERGED-TREE CI verdict is in, and BOTH tiers are green
The earlier note that only the dev-host tier had confirmed 8c/8d is now **superseded**.
`clawgate-ci-rerun-z5wj5` on revision **`d2d2346e`** (trunk with both merges):
```
== clawgate-ci summary ==
  go         pass
  extension  pass
  hook       pass
ALL LEGS PASS
```
All ten steps `exit=0`, and the `hook` leg — the one #592 exists to exercise and which had never
run — reaches `ok 67 no test body asserts an absence with a negated grep (use refute_grep)` with
`hook leg rc=0`. So the sandbox tier (a `cp -r` store copy with no `.git`) and the dev-host tier
now agree on the merged tree.

⚠ **It was obtained by RE-RUNNING the pipeline from the failed run's own spec**
(`kubectl -n tekton-ci get pipelinerun <failed> -o json` → strip `metadata.name`/`status`, set
`generateName`, `kubectl create`), NOT by pushing to trunk. Worth knowing: a trunk PipelineRun is
re-runnable without a commit, so a capacity-starved verdict is recoverable later.

### 🔴 `TaskRunTimeout` IN clawgate-ci HAS TWO DISTINCT CAUSES, AND `#572` ONLY FIXES ONE
- **Symptom:** both present as `tekton/clawgate-ci` red with
  `COULD NOT RUN: clawgate-ci stopped before any leg reported`.
- **Observed (with values), and they are NOT the same failure:**
  - `clawgate-ci-czshq` (rev `751aabaa`): `mint-token`, `clone`, `status-pending`, `wait-postgres`,
    `build-css` all **`exit=0 Completed`**; `go`/`extension`/`hook`/`verdict` `exit=1
    TaskRunTimeout`. The pod ran and **the budget was consumed inside the `go` step**.
  - `clawgate-ci-z5pdm` (rev `d2d2346e`): **ALL TEN steps `exit=None running`** — not one
    executed. TaskRun reason `ExceededNodeResources`, pod `0/10 Pending` for 14 min. The pod
    **never scheduled**; the 25m budget elapsed while unschedulable.
  - Cluster at the time: 9 pods Pending in `tekton-ci`, node CPU requests 16/61/55/**89**%, CPU
    limits oversubscribed to 228/493/268%. After it drained (0 pending, requests 16/61/24/16%,
    tekton-ci running 16 → 1) the identical spec passed in ~10 min.
- **Ruled out:** the diff, in both cases. #592 contains zero Go files so it cannot slow `go`, and
  #591 — which does touch Go — passed the same pipeline nine minutes after czshq failed.
- 🔴 **Consequence for `ZacxDev/homelab-infra#572` (raise the task budget 25m → 40m), MERGED
  2026-08-31 18:36Z:** it fixes the czshq shape and does **nothing** for the z5pdm shape — a longer budget just waits longer for
  a pod that never lands. **Do not let the bump be recorded as the fix for both.** The z5pdm shape
  needs scheduling headroom (requests/limits, priority class, or concurrency caps), which is a
  different change.
- **Next probe when it recurs:** read the per-step `terminated.reason` FIRST —
  `kubectl -n tekton-ci get taskruns -l tekton.dev/pipelineRun=<run> -o json`. If early steps show
  `exit=0 Completed` it is a budget problem; if every step is `exit=None running` it is a
  scheduling problem, and `kubectl get pods -A --field-selector=status.phase=Pending` plus node
  `Allocated resources` is the confirming read.

### ✅ RESOLVED — both #591 and #592 were audited post-merge; both are sound, and both leaked follow-ups
Audits run 2026-08-31, one read-only subagent each, against the merged squashes. **Neither found a
reason to revert.** Every finding below was independently re-verified here before filing — two of
the auditors' own numbers were reproduced and one was beaten.

**#591 — the barrier is correct at all eight sites, and its PRECONDITION is unpinned.**
`awaitPushesSettled` is valid only while every push DECISION is reached synchronously before the
awaited call returns (`pushInFlight.Add(1)` runs on the caller's goroutine, `server.go:2010`).
Nothing asserts that. The existing seam ledger (`push_fanout_ledger_test.go:39-43`) pins a
DIFFERENT thing — that `push.Broadcast` has one call site — which a notify helper moved into a
`safeGo` satisfies unchanged. **Measured independently, same test, 20 runs each, mutant = invert
the running-status skip AND wrap `notifyAgentRunning` in `safeGo` (the repo's own idiom, 7 existing
sites):**
```
pre-PR  time.Sleep(20ms)      -> 20/20 CAUGHT
post-PR awaitPushesSettled    ->  0/20 CAUGHT
```
The auditor measured 3/20; I measured 0/20 — same direction, stronger. ⚠ **A LOST TRIPWIRE, NOT A
LIVE BUG:** production decides these synchronously today. But it is not hypothetical — production's
coalescer flush runs on `time.AfterFunc` (`push_task.go:60`) and the tests are synchronous only
because the injected fake fires inline, so the precondition is a property of the TEST SEAM, not of
production. 🔴 My first attempt at this mutant did not compile; it was scored **INVALID**, never a
survivor. → rank 14.

**#592 — the guard reds on the real historical violation, and one of its documented premises is
false in the very file it guards.** Confirmed here: `clawgate-stop-hook.bats:860` and `:907` are
`@test` bodies opening `cat > "$TMP/bin/jq" <<'SHIM'`, while line **1306 of that same file** states
*"No test body in either suite uses one."* ⚠ **LATENT, NOT LIVE — I checked:** neither heredoc
contains a column-0 `}` today, so the scanner is not currently blind. One ordinary edit — wrapping
the shim's logic in a shell function, which puts `}` at column 0 — makes `/^}/ { inbody = 0 }` end
the body at the heredoc's brace and silently switch the scanner off for the rest of it. Both
`inbody = 0` sites (`:1080` the pre-existing sibling, `:1332` the new one) share the rule, so the
older `scan_detached_absences` goes dark on the same body. **Both instruments stay green while
blind**: `EXAMINED > 0` still holds and the `BODIES == grep -c '^@test '` equality still holds.
→ rank 15.

⚠ **Neither PR's `clawgate-ci` ever ran the leg that covers it** — #592's timed out before the
`hook` leg (see the TaskRunTimeout investigation above). The bats coverage claim for #592 rests on
a local reproduction of that leg in the same `docker.io/bats/bats:1.11.1` image, not on CI.

### ⚠ CORRECTION 2026-08-31 — the workbench CAN pull `docker.io` today, and the root cause is STILL UNFIXED
🔴 **This corrects the "THE WORKBENCH STILL CANNOT PULL FROM `docker.io`" block above in BOTH
directions. Read both halves — either one alone is wrong.**

- **The instruction "build clawgate images on the laptop" is no longer true as a hard constraint.**
  Measured on the workbench: `docker pull docker.io/bats/bats:1.11.1` **succeeded** (`Downloaded
  newer image`), and so did `docker.io/library/alpine:3.20`. I followed the old block this session
  and routed a bats run to the laptop **without testing the workbench first** — which is the
  "an open-investigation block reads as current forever" trap this doc warns about, walked into by
  the session that was reading the warning.
- 🔴 **But it is NOT fixed, and "docker.io works now" is the more dangerous wrong conclusion.**
  The router is still serving the poisoned record, unchanged:
  ```
  dig @<the LAN router> registry-1.docker.io  ->  TTL 41879636 (~485 days), 4 addresses
  dig @<a public resolver> registry-1.docker.io  ->  TTL 49, a DISJOINT set of 4
  ```
  ⚠ The addresses are deliberately NOT written down: they are ephemeral third-party allocations of
  no lasting value, this repo is PUBLIC, and `scripts/tests/test_no_public_ips.py` rejects a public
  IP literal (it caught this paragraph's first draft — an allowlist entry would have been the wrong
  fix, since every exemption there is path-scoped and must keep matching something). **Re-derive
  them with the two `dig`s above; the DISJOINTNESS and the TTL are the finding, not the values.**
  TLS against the addresses the ROUTER hands out still presents certificates for unrelated sites —
  measured one generic infrastructure CN and one wildcard CN belonging to an unrelated third-party
  domain (not named here, same reason), plus one address that completes no handshake. That is the
  documented symptom, intact.
- **The host-side pin is still NOT applied.** `server=/docker.io/` appears **0** times in the
  RUNNING dnsmasq config. `nix/system/apply-dnsmasq-docker-io-pin.sh` remains staged-not-applied
  and still needs `sudo nixos-rebuild switch`, which an agent cannot run.
- **So why do pulls work?** The system resolver is currently answering from the good upstream
  (`dig registry-1.docker.io` with no `@` returns a TTL-25 correct answer), i.e. dnsmasq is
  preferring `1.1.1.1` over the router for this name **by luck of upstream selection, not by
  configuration**. Nothing pins that. It can flip back with no change by anyone.
- 🔴 **Practical guidance, replacing the old block's:** do NOT hard-route image builds to the
  laptop as a standing rule, and do NOT delete `apply-dnsmasq-docker-io-pin.sh` as obsolete.
  **Test the pull at the moment you need it** — one `docker pull` is the whole check — and treat a
  failure as this same unfixed router bug rather than re-diagnosing it. The durable fixes are
  unchanged: the pin (needs sudo, one host) or clearing the record on `192.168.50.1` (fixes every
  machine on the LAN, and is still untouched).

### ⚠ SUPERSEDED (its hypothesis only) — `test_subsystem_store_api.py` HAS RECURRED
🔴 **This corrects the Gotchas bullet that ends "Fixed by devrc#996 (`1b1f71ad`…)" and the
open-investigation heading that says "nothing is fixing it".** #996 did not close it.
🔴 **AND ITS OWN "Leading hypothesis"/"Next probe" ARE NOW REFUTED — see `### ✅ DIAGNOSED` below.**
The recurrence recorded here is real and its ruling-out is sound; only the seed/ordering explanation
and the "pull `fix/xdist-parametrize-values-deterministic`" instruction are wrong.

- **Symptom + exact repro:** `devrc#1162` — a **one-markdown-file** PR — was blocked by
  `tekton/devrc-pytests` on
  `TestTheActorComesFromTheTOKEN::test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-…]`.
  That is the **same case name** this doc already records from `devrc#1056`, in the block above.
- **Observed (with values):** the failure is attached to head `74e39bea`
  (`FAILED: pytests — FAILING: TestTheActorComesFromTheTOKEN…`). The **immediately preceding**
  head of the same branch, `e1d1318f`, failed a DIFFERENT test
  (`test_no_unallowlisted_public_ip_literal_is_committed` — a real defect of mine, since fixed), so
  the two reds are unrelated. After a rebase with no content change beyond that fix, head
  `2fd84888` passed: `TOTAL collected=19942 passed=19939 skipped=3 failed=0`.
- **Ruled out — the diff.** #1162 touches exactly ONE file, `claudedocs/handoff-tmux-webapp.md`.
  A markdown file cannot reach a store-api test. This is the doc's own stated discriminator
  ("the discriminator that settled it was a DOCS-ONLY PR failing"), reproduced.
- **Control, and its LIMIT:** `scripts/tests/test_subsystem_store_api.py` on a clean
  `origin/main` worktree ran **3/3 green — 641 passed each — at 301.6s / 293.8s / 296.8s.** The
  tight spread rules out load inflation *in that run*. 🔴 **But it is a weaker control than it
  looks:** it ran on the DEV HOST while the failure is in the nix **sandbox tier** under CI
  concurrency, so it is a second sample of a DIFFERENT environment, not of the failing one. The
  structural argument (one markdown file) is what actually discriminates here; the 3/3 only shows
  the file is not deterministically broken.
- **Leading hypothesis:** unchanged from the original block — non-determinism that surfaces under
  concurrency, with the failing case MOVING between runs (now three distinct cases observed across
  four runs: `TestEnumerationChannelsAreClosed…`, `TestTrustedProxyOverTheRealProcess…`,
  `TestTheActorComesFromTheTOKEN…`). #996 narrowed it; it did not eliminate it.
- **Next probe:** do NOT re-derive this from the dev host again — it passes there. Reproduce in the
  tier that fails: `nix build .#checks.x86_64-linux.pytests` (ONE derivation at a time — a combined
  invocation produces false failures), repeatedly, and record which case fails each time. If the
  case keeps moving, the seed/ordering hypothesis is confirmed and
  `fix/xdist-parametrize-values-deterministic` (branch exists locally, still no PR) is the thread to
  pull.
- 🔴 **Consequence while it stays open:** devrc is the one repo here with `enforce_admins: true` and
  two required checks, so this blocks arbitrary PRs — including docs-only ones — and the only
  remedy is a fresh push. **A red on this file is not evidence about your diff.** Check the case
  name against the three above before spending any time on it.

### ✅ DIAGNOSED — the store-api gate failure is FSYNC CONTENTION, and the seed/ordering hypothesis is REFUTED
🔴 **This supersedes the two blocks above. Read `scripts/ci-repro/README.md` BEFORE re-pushing or
debugging your diff** — it is the canonical write-up and it is maintained; this block is a pointer,
not a copy.

- **The mechanism, measured:** `server.py:_replace_bytes` fsyncs the file and then the parent
  directory **inside the request, before the response is written**; fsync blocks in uninterruptible
  sleep. When one fsync exceeds `HANG_TIMEOUT` the client raises `TimeoutError` and the gate reports
  a **code failure for an I/O stall**. The suite's own classifier names it unprompted:
  `MECHANISM = SERVER_BLOCKED_IN_FSYNC`. Why CI and not here: `devrc-ci` is pinned to one node, so a
  burst of pushes stacks concurrent runs onto one machine's disk.
- **There is now an on-demand reproducer on the dev host** — `scripts/ci-repro/slowfsync.c`, an
  `LD_PRELOAD` shim, with its own instrument-validation step and a control/reproduction pair. It
  reproduced the identical test with the identical parametrisation as a real CI failure, and was
  independently re-run by an auditor.
- **Three fixes have merged** (verified by content, never by ancestry): `devrc#1181` squash
  `0c333846` (the diagnosis + reproducer), `#1190` squash `634c328a` (a raw reader racing the
  server made 12 assertions report an empty read as a SECOND response), `#1193` squash `48a5540e`
  (the hang guard SAMPLED its own arming instead of waiting for it).
- 🔴 **NOT CLOSED — and deliberately not written as "fixed", per this doc's own shelf-life rule.**
  Measured 2026-09-01 **after all three merged**: `devrc#1197` is red on
  `TestAHungRoundTripSAYSWhichSideBlocked::test_a_stall_in_the_FSYNC_region_is_NAMED` — a **fourth**
  distinct case in this file — while `#1199` passed the same tier. The honest status is: mechanism
  identified and reproducible, three contributing defects removed, **no run of consecutive greens in
  the failing tier yet**.
- 🔴 **Unchanged and still the operative advice: a red on this file is not evidence about your
  diff.** What changed is the remedy — do NOT re-derive an ordering theory, and do NOT open a PR for
  `fix/xdist-parametrize-values-deterministic`.
- ⚠ **Two fixes that look right and are not**, both written up in that README: raising
  `HANG_TIMEOUT` again (60.0 is already the symptom fix, raised from 15, and it did not hold), and
  relocating `nix-store-cache` (the stalling write lands on the step container's **ephemeral layer**,
  which that volume does not cover).

### ✅ RESOLVED — the 0.8.21 deploy, and the image-vs-pin trap it walked into
🔴 **A PIN THAT LANDS AFTER YOUR MERGE DOES NOT MEAN THE IMAGE CARRIES IT.** Measured:
`#611`'s squash `5d11d9a7` merged at **19:57**; another session's `0.8.20` pin `eed7db5a` landed at
**19:58**. Trunk therefore looked like it had shipped the change — and the running 0.8.20 page had
**0** occurrences of `sse:tmux.changed`, against a positive control of 6 other `sse:*`
subscriptions in the same page. 0.8.20's image was built BEFORE the merge. This is the documented
"an image built during review silently omits a fix that landed mid-review" shape (the reason 0.8.12
and 0.8.14 were discarded), reached from the timing side rather than the review side.
**The control that settles it costs one command: run the candidate image locally and grep its
rendered page BEFORE pushing.** Measured for 0.8.21 — 1 occurrence in the image, 0 in live 0.8.20.
Do this instead of reasoning from commit timestamps, which cannot see when an image was built.

### ✅ RESOLVED — four audit rounds on #611, and the attribution gate ended them
Round 1 (full) found the guards were **spelled, not structural**: a decoy attribute carrying the
same string let a non-subscribing panel pass the entire Go suite. Round 2 found the FIX introduced
a regression — the AST rewrite traded a walkable-but-spelling-agnostic check for a
precise-but-literal-only one, losing a shape `AutoApproveBanner` already uses deliberately
(`trigger := "…"; hx("hx-trigger", trigger)`); the PRE-fix guard caught it and the post-fix one did
not. Round 3 found the fix's own comment overclaimed. Each round found a real defect created by the
previous round's fix — three times consecutively.
🔴 **The ladder was ended by the ATTRIBUTION GATE, not by a clean round**: round 2's fixes changed
0 executable payload lines (27 comment lines in payload files — ambiguous), round 3's changed 0
payload files at all. Two consecutive zero-payload rounds ⇒ the ladder had left the PR and was
auditing scaffolding it had itself written. The final comment corrections were made directly rather
than as a round 4, because re-auditing a comment edit is the loop the gate exists to stop.

## Gotchas
- 🔴 **A PR THAT CHANGES A TEKTON PIPELINE CANNOT BE VERIFIED BY THAT PIPELINE — its green check
  is a statement about the OLD leg.** A PipelineRun executes the **deployed Task object in the
  cluster**, never the manifest on the PR branch. Measured 2026-09-01 on `#618`, which rewrote
  `clawgate-ci`'s `hook` leg: `tekton/clawgate-ci` went **green**, and reading `step-hook` of run
  `clawgate-ci-vdcqw` (revision = that PR's own head) showed **one** plan line `1..67` and **zero**
  `floor=` lines — i.e. the pre-change leg. The new leg would have printed two of each.
  🔴 **This is the same MERGED ≠ LIVE shape already recorded above for #584's `MIN_PASSED`, and it
  was walked into by a session holding that note** — because there the symptom was a stale VALUE
  after merge, and here it is a green CHECK before merge, which reads as evidence rather than as
  its absence. **Ask which object the run loaded, not whether the check is green.**
  Three consequences worth knowing before the next one:
  - **The leg that DOES run PR content is a different one.** `gitops-validate`'s `scripts-tests`
    step executes the PR's own files, so a test shipped alongside the manifest change is really
    gated; the manifest change itself is not.
  - **Verify the DEPLOYED artifact directly instead of waiting.** `kubectl -n tekton-ci get task
    <name> -o json`, pull the step's `script`, confirm it is byte-identical to what you tested,
    and run THAT in the step's own image. No cluster write, no GitHub status, and it is evidence
    about the thing that will actually execute.
  - ⚠ **A merge may trigger nothing at all.** `clawgate-ci` is path-filtered on
    `containers/clawgate/**`; #618 touched `clusters/…` and `scripts/tests/`, so no run fired and
    none ever will for that commit. An empty commit cannot re-trigger a path-filtered pipeline
    either (see the entry below). Re-running a prior PipelineRun from its own spec is the route.
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
  - ⚠ **CORRECTED 2026-08-30, by a different effort — the "so it is UNGATED" half is FALSE.**
    `clawgate-ci` genuinely does not run Playwright, but a SEPARATE Tekton check does:
    measured on homelab-infra#564, `tekton/clawgate-e2e` → `clawgate e2e passed — 122 tests,
    2 skipped`, running `make e2e` on the PR's own specs against a REAL Postgres (Tekton pods
    have no Docker daemon). Whether it BLOCKS is unmeasured — branch protection 403s on this
    private repo without GitHub Pro. The local Docker skip trap above is still real; the spec
    counts are stale (21 files / 124 collected as of this date). Canonical text now lives in
    the `clawgate` skill + its `reference/extension.md`; this bullet is left in place because
    the rest of the doc reasons from it.
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

- 🔴 **A FAILED `git worktree add` DOES NOT STOP THE NEXT `git -C <path>` — AND IT LANDED A MERGE
  COMMIT ON ANOTHER SESSION'S BRANCH.** `worktree add /home/zach/workspace/devrc-integ` failed
  `fatal: … already exists` (another session held that path for `integ/963-965`); the very next
  line, `git -C /home/zach/workspace/devrc-integ merge origin/feat/…`, ran happily **inside their
  worktree** and committed onto **their** branch. Silent: no conflict, clean tree, and `git log`
  afterwards shows exactly what you expect because you are reading the branch you landed on.
  Recovered via `git reflog` (pre-merge head) + `git reset --keep`, which refuses rather than
  destroys. **Two rules: PID-unique scratch worktree names, and branch on `worktree add`'s EXIT
  CODE before issuing one more `-C` against that path.** A generic `<repo>-integ` is precisely the
  name another session also picked.
- 🔴 **THE ZSH NO-WORD-SPLITTING TRAP RETURNED A CONFIDENT `0` FOR EVERY SCROLLBACK DEPTH — in a
  session that had already read the rule, and then AGAIN an hour later.** `panes=$(tmux
  list-panes …); for p in $panes` loops **ONCE** on the whole newline-joined string, so
  `capture-pane -t "%1\n%2\n…"` fails and every total is 0. It does not error; it reports a clean
  measurement of nothing. Only a POSITIVE CONTROL caught it — an earlier inline `for p in $(…)`
  had already measured 51,406 B for that host, so 0 was impossible. Fix: `${=var}`, a real array,
  or pipe to `bash -s`. Same family as the `gawk` silent zero this feature already paid for.
- 🔴 **A `pgrep -f` WAIT LOOP MATCHES ITS OWN COMMAND LINE AND NEVER EXITS.** Two of this
  session's `until … ! pgrep -f "<pattern>"; do sleep; done` loops spun for **45 and 62 minutes**
  on an already-loaded box, because the pattern appeared in the loop's own `/proc/<pid>/cmdline`.
  The condition can never go false. Resolve PIDs and compare `/proc/<pid>/cwd`, or wait on a
  FILE (`until grep -q DONE "$log"`), never on a pattern that contains itself.
- 🔴 **A WORD-COUNT OVER RENDERED HTML IS NOT A MEASUREMENT OF STATE — pane CONTENT can spell the
  word.** Grepping the live `/ui/tmux` page for `truncated` returned 3 and was reported as "3
  panes hit the truncation path". The read model's own status field said **0 truncated**; the
  three hits were the word appearing inside CAPTURED PANE TEXT — this very session's terminals
  discussing truncation. This is the "a guard on WORDS is walkable" rule applied to an ad-hoc
  probe: **read the STATUS FIELD, never a word count over a page that embeds arbitrary text.**
- ⚠ **`test_subsystem_store_api.py` was broadly flaky under load on 2026-08-28/29 and blocked
  EVERY devrc PR, including its own fix.** Four different tests failed across three PRs
  (`TestTrustedProxyOverTheRealProcess`, `TestTheBackstopNeverSendsASecondResponse` ×2 variants,
  `TestTheActorComesFromTheTOKEN`). 🔴 **The discriminator that settled it was a DOCS-ONLY PR
  failing** — a one-markdown-file diff cannot break a store-api test. Each passed 5–6/6 in
  isolation on clean `origin/main`, and the box was at load 18–51 from concurrent agents. 🔴 **"Fixed
  by devrc#996" IS FALSE — it recurred, and the mechanism is now DIAGNOSED as fsync contention; see
  `### ✅ DIAGNOSED` above and `scripts/ci-repro/README.md`.** #996 (`1b1f71ad`, "audit BEFORE
  responding, and serialise the audit sink") narrowed it. **If it
  recurs: run the full target on a clean `origin/main` worktree before touching your diff, and
  check whether UNRELATED targets' wall times also moved** — here a 273-test target swung
  9.56 s → 5.52 s between runs, which is load inflating everything, not one assertion.
- ⚠ **The pane-preview ratio is NOT a constant.** 2.63x measured back-to-back pre-deploy;
  **3.81x** on the first production push. It moves with pane count and screen fullness. Quote the
  cap headroom (~10.5% of 4 MB) rather than a multiplier.

- 🔴 **A MUTATION SWEEP'S KILL-ATTRIBUTION PARSER IS ITSELF AN INSTRUMENT, AND MINE WAS WRONG ON
  THE FIRST RUN.** `--- FAIL: TestFoo (0.00s)` split on whitespace puts the literal **`FAIL:`** at
  index 1 and the NAME at index 2. My sweep printed `by: FAIL:` for all 15 mutants and still
  reported a confident **15/15 killed** — the count was true and the *attribution said nothing*,
  so "killed by an unrelated test" and "killed by the guard I wrote" were indistinguishable. A
  sweep that cannot name its killer cannot tell you the guard is reachable. Fix the parser and
  **re-run before quoting the number**; also make a mutant whose patch matches ≠1 time report
  **INVALID**, never SURVIVED — an edit that never applied runs the original code.
- 🔴 **A `grep`-filtered test run reports GREP's exit status, not the suite's.**
  `go test ./... 2>&1 | grep -vE "^ok|no test files"; echo "exit=$?"` printed **`exit=1`** on a
  fully green 21-package run, because grep matched nothing. Read a verdict from the runner:
  redirect to a file, echo `$?` from `go test` itself, then COUNT `^ok` and `FAIL` lines. Same
  family as the `| tail; echo rc=$?` failure this fleet already paid for.
- ⚠ **A SUBSTRING LEAK-CHECK NEEDS A FIXTURE THAT CANNOT OCCUR IN THE MESSAGE.** A test asserting
  "the boot log never prints the secret" used the fixture token `"short"` and failed — because the
  refusal reason contains the word **short**er. `"placeholder"` fails the same way (the reason
  says "looks like a placeholder"). Pick a nonsense fixture, and note the failure was the
  instrument, not the code.
- 🔴 **`web/static/app.css` IS GITIGNORED, so a fresh worktree runs the clawgate suite two tests
  RED for a reason unrelated to any diff.** `TestOpenRoutesNoAuth` and `TestStaticAssetsServed`
  fail until `make css` runs — and `tailwindcss` is not on PATH here, so
  `nix-shell -p tailwindcss_4 --run "tailwindcss -i web/css/input.css -o web/static/app.css --minify"`
  is the actual command. This already has an entry above; it is repeated because a mutation sweep
  that widens to the full package will surface it as two mystery kills and look like a real find.

- 🔴 **A GUARD THAT SCANS ROUTE STRINGS CANNOT SEE WHAT A HANDLER DOES, AND ITS OWN POSITIVE CONTROL
  WILL TELL YOU IT WORKS.** `TestTheLayoutUIAddsNothingToTheTerminalSurface` scanned
  `mux.HandleFunc` PATTERNS and carried a control proving the string scan fired. Planting
  `exec.CommandContext(…, "tmux", "send-keys", …)` in the unauthenticated handler — applied once,
  compiling, no route string touched — left the ENTIRE suite green, that test included. The
  docstring named a property the body never checked. Fixed by AST-scanning handlers (comments
  discarded — a raw-text scan fires on this repo's own prose and has reddened the gate before).
  **Ask what the code must DO, then check the guard inspects that, not its neighbour.**
- 🔴 **A LEDGER ENTRY THAT JUSTIFIES AN EXEMPTION IS THE HIGHEST-VALUE PLACE FOR A FALSE CLAIM.**
  `allowedNonTerminalWrites` said view-scoping stopped "an unauthenticated LAN client walking 1..N
  and emptying every view". `GET /ui/layout` is on the pass-through tier and renders every panel's
  write address, so one anonymous GET hands over the whole arrangement — measured: 9 addresses
  harvested, 9/9 panels destroyed, all 200. The scoping raised a full wipe from P requests to 1+P.
  **The fix was structural, not documentary:** the irreversible control was removed, so the sentence
  that needed justifying no longer exists.
- 🔴 **THREE OF MY OWN MUTANTS WERE INVALID AND ONE ALMOST READ AS A PASS.** Two failed to compile
  and one had an anchor that matched 0 times — each printed a green suite that looks exactly like a
  survivor. **A patch that does not apply EXACTLY once, or does not compile, is INVALID — never a
  kill and never a survivor.** Assert the match count before applying, and check the build before
  scoring.
- 🔴 **A `-run` FILTER CAN EXCLUDE THE ONLY TEST THAT WOULD HAVE CAUGHT YOU — TWICE IN ONE SESSION.**
  `-run Vocab` skipped the module-wide `TestNoProducerStructCallsAFieldSessionAlone`, and I
  concluded the vocabulary scan did not cover a new package when it does. Separately,
  `-run 'Layout|layout'` never ran `TestAHostOnlyPanelIsREJECTEDByTheAPINotStored`, which was
  failing. **Run the whole package before concluding a guard has a gap.**
- 🔴 **A THEORY THAT EXPLAINS THE FAILURE IS NOT EVIDENCE FOR IT.** `clawgate-ci` and
  `clawgate-ux-audit` failed repeatedly; I attributed it to cluster load with real numbers — 22.7x
  median wall-time inflation across packages with ZERO failures, 12 concurrent PipelineRuns, nodes
  at 51–62%. All of that was true and it was the WRONG MECHANISM. Both went green the moment #509's
  advisory-lock fix became an ancestor. Every symptom was lock-shaped: a statement timeout on
  `ensure schema_migrations`, "failed to take a FREE lease", and an 11–15s stall exactly where
  `Migrate` takes its advisory lock. **Change one variable and re-measure before calling a cause.**
- 🔴 **A BARE PR NUMBER IS AMBIGUOUS ACROSS REPOS AND THE WRONG ONE RESOLVES SILENTLY.**
  `audit-dispatch.py 538` assembled a brief for `devrc#538` (a docs handoff) because that is the
  cwd's repo; the target was `ZacxDev/homelab-infra#538`. Caught only by reading the generated
  brief's TITLE. Pass `--repo owner/name` whenever the PR is not in the cwd's repo.
- 🔴 **`gofmt -w <dir>` REFORMATS PRE-EXISTING FILES AND SILENTLY WIDENS YOUR DIFF.** Seven files I
  never touched appeared in the change set as pure alignment churn. Format only the files you edited.
- ⚠ **An image built during review can silently omit a fix that landed on trunk mid-review.**
  0.8.12 and 0.8.14 were both discarded for exactly this: trunk gained a `containers/clawgate`
  commit (#503, then #509) while the PR sat, so the image would have carried a HIGHER version with
  LESS code. **Before pushing a pin, check `git log HEAD..origin/trunk -- containers/clawgate` and
  rebuild if it is non-empty.** Never re-push a mutable tag — mint a new one.
- ⚠ **`docker push` sends registry auth from the LOCAL client, not the `DOCKER_HOST` daemon.**
  `DOCKER_HOST=ssh://zach@10.42.0.100 docker push` fails `unauthorized to access repository:
  library/clawgate` because the workbench's `~/.docker/config.json` has no `harbor.homelab.lan`
  entry while the laptop's does. **Run the push ON the laptop**; the image is already there from the
  build. `docker manifest inspect` also fails on the self-signed CA — use `docker pull` to confirm
  Harbor serves a tag.
- ⚠ **A squash merge makes `merge --ff-only` refuse forever** — the branch tip is not an ancestor.
  That refusal is correct, not a fault; make a fresh worktree off `origin/trunk` instead.
- 🔴 **CARRIED FORWARD FROM RANK 3's ENTRY (2026-08-28) — two measurements that CONSTRAIN any
  future read-model work and lived only in a REPLACE section until now:**
  - **SCROLLBACK IS EXCLUDED ON A HARD BOUND.** ~**4,014 B per line fleet-wide**, so `-S -1000`
    computes to **6.13 MB** against `maxTmuxPushBytes` (4 MB) — the cap breaches at **~650
    lines/pane**. Visible screen only; `tail` serves history one window at a time.
  - **ONLY ~22% OF PANES CHANGE PER TICK** (10 of 45 over a real 120 s interval), so ~77% of the
    bytes are resent unchanged. In absolute terms that is cheap (~62 MB/day gzipped) — **the
    objection is STALENESS, not bandwidth.** That is the standing argument for an on-demand path,
    and the reason one was not built.
- 🔴 **MOVED HERE FROM `How to verify` so a status replace cannot drop them again — these are
  durable and were nearly lost in the 2026-08-30 update:**
  - **`clawgatectl` is built from the LOCAL `homelab-talos` tree**, so a behind checkout ships a
    binary missing verbs that prints help and **exits 0** under a plausible version label. Both
    hosts need `homelab-talos` current *before* a `home-manager switch`. Measured 2026-08-28: the
    laptop was **17 commits behind** while the workbench was current — `ship.sh` does NOT converge
    that repo. Live again 2026-08-30: both hosts 0.8.17 against an 0.8.18 server (rank 8a).
  - **After any deploy, check ALL pods, not `.items[0]`** — `kubectl -n clawgate get pods -l
    app=clawgate` lists a `Succeeded` leftover too, so a `.items[0]` jsonpath reports the wrong
    image. Confirm the pod is `Running` **and ready**.

- 🔴 **A version label that MATCHES is not evidence the code is there — and the first probe of it
  was VACUOUS.** `clawgatectl view list` printed the parent command's help and **exited 0**: the
  verb is **`ls`**, and cobra treats an unknown positional as help. That is precisely the
  2026-08-14 silent-no-op shape (`task status` printing help, exit 0), now at the *verification*
  layer rather than the labelling one. The real check is a round trip that MOVES A NUMBER: write
  from one host, read from the other. `view ls` returning `[]` with rc 0 is indistinguishable from
  a client wired to nothing until the count has been watched go 0 → 1 → 0.
- 🔴 **`ship.sh` rc 11 was a RACE, not a failure — `origin/main` moved between the two legs.** The
  local leg fast-forwarded to `ad891a5c`, then verified against a HEAD that had already advanced to
  `bd1572f3` (another session merging). Both hosts ended on the same sha and the immediate re-run
  was rc 0 with agreement COMPARED. **Re-run before diagnosing an rc 11** — but still read the
  per-host lines, because a genuine skip hides among greens.
- 🔴 **A clean rebase is not a clean merge, and devrc#1056's had a real semantic overlap.** It was
  34 behind; `git rebase origin/main` reported no conflict. Main's `e212415e` (#1076) had meanwhile
  replaced the session-manager row-field ledger's permissive `assert field in sm.__doc__` with a
  **SET** comparison failing in BOTH directions — and #1056 adds fields. It is fine ONLY because
  `tmux_server_id` lands on the **host** dict, not the row dict. The check that settles it is
  `git log <base>..<tip> -- <each file the branch touches>` then reading the merged region; the
  green suite alone would not have told the two apart.
- 🔴 **A Tekton check posted as `ERROR` is a broken gate, not a bad change — and only a fresh push
  clears it.** devrc#1056 sat at `ERROR`/`ERROR`, `mergeable: UNKNOWN`, no `targetUrl`. Rebase +
  force-push moved it to `pending` + `MERGEABLE`. Rebasing beats an empty commit here: afterwards
  the branch head IS the merged tree, so the gate run is a statement about what merging produces.
- 🔴 **An e2e fixture can make a guard vacuous in a way that reads exactly like a product bug.**
  `layout.spec.ts`'s control-set assertion (`['archived','collapsed','expanded']`) failed on its
  first run with `expanded` missing. Not a bug: an **expanded** card offers Collapse + Archive and
  no Expand at all, and Restore lives only on the **archived** card in the drawer. Seeded
  all-expanded — the obvious fixture — the union is `{archived, collapsed}`. Fix: one panel in
  EACH state.
- 🔴 **A panel that does not RESOLVE renders an EMPTY detail block, so "collapsed hides the detail"
  would have been true of nothing.** `layoutPanelDetail`'s `default` arm returns `g.Text("")`, so
  an `unreported` panel draws the same body expanded or collapsed. The spec therefore pushes a real
  snapshot via `POST /api/tmux/snapshot` first. ⚠ That body is **session-manager's own `--json`
  document** — a `hosts` OBJECT keyed by host name,
  `{"ts":…,"hosts":{"<h>":{"reachable":true,"windows":[…]}}}` — NOT a list of snapshots. And the
  window keys are **mixed case**: `codename` and `tmuxSessionName` camel, `window_id` /
  `window_name` / `window_index` / `claude_session_id` / `hotkey_display` snake.
- **Assert the ATTRIBUTE, not the text.** Mutant E (`data-hotkey-display` re-derived through
  `strings.ToLower`) left the visible text `Alt+i` while the attribute became `alt+i`. A text
  assertion survives it; the attribute assertion killed it, at line 184.
- **A worktree of `homelab-talos` needs `e2e/node_modules` linked.** `package-lock.json` is
  **gitignored** there, so a fresh worktree has none and a naive `cmp` against the base clone
  reports DIFFERS when the file is simply ABSENT. `ln -sfn <base>/containers/clawgate/e2e/node_modules`
  suffices — `ensure-node-modules.sh` checks the declared deps, not the directory.
- **Docker on the workbench cannot pull, but `postgres:16-alpine` is already cached**, so full-mode
  e2e runs locally without touching docker.io. `docker images | grep postgres` before assuming
  otherwise.
- ⚠ **`core.hooksPath` was set REPO-LOCALLY on `homelab-talos`** at push time
  (`/home/zach/workspace/homelab-talos/.git/hooks` — sample-only, so nothing ran). It is the
  documented volatile value: **re-measure at the moment you push**, and verify the branch afterwards
  (`local HEAD == ls-remote`, commit count) rather than trusting the push message.
- **Branch protection on devrc `main` strands a `handoff_doc.py --push`.** Recovery order is
  preserve → **verify on origin** → `reset --keep`: branch the topic, push it, confirm the sha with
  `git ls-remote`, and only then move `main`. devrc#1090 is the worked example.

- 🔴 **SIX AUDIT ROUNDS ON ONE TEST FILE, AND FIVE OF THEM FOUND THAT THE PREVIOUS ROUND'S FIX HAD
  CREATED THE NEXT DEFECT.** One class, five times: **a claim wider than the code** — a comment or
  a test NAME asserting coverage the assertion does not deliver. The three worth knowing:
  1. A "simplification" that DELETED a mutant. Replacing `count(A)===count(B)` with a
     one-directional scan was called "subsumes the count check"; it did not. An
     `<a href=".../delete" data-layout-action="archived">Delete</a>` then passed **all three
     tiers** — the spec (no htmx verb to match), the AST route scan (no route registered), and the
     HTML substring check (an anchor spells neither `hx-delete=` nor `hx-confirm`). Caught by ONE
     tier before the "improvement", ZERO after.
  2. **Erasing an assertion's own observable.** A click was followed by an unconditional
     `d.open = true` and then `expect(open).toBe(true)` — reading back the value just written. A
     `preventDefault`'d summary passed visible, passed click, passed the assertion. Fix: assert the
     FLIP (`!openBefore`), which separates all four states whichever way the underlying defect goes.
  3. **Widening one axis while narrowing another.** Moving a guard into a new test also moved it
     onto a two-state fixture, silently dropping the `StateCollapsed` arm — measured RED at the
     old commit, GREEN at the new one.
- 🔴 **A guard on a WORD is walkable by a five-character rename, and enumerating spellings does not
  fix it — it relocates it.** `[hx-delete]` missed `data-hx-delete`; widening to both then missed
  `data-hx-post`; `[onclick]` missed `onmousedown` AND an `hx-on:click` **`<div>`**. The fix that
  held was to stop listing: scan by SHAPE (interactive tag names + any attribute matched by
  PREFIX). Two enumerations in a row failed before that landed.
- 🔴 **A mutation harness that restores only in a `finally` is NOT SIGKILL-safe.** The Bash tool
  caps at 10 minutes regardless of the timeout requested; a battery was killed mid-mutation and
  left `internal/ui/layout.go` MODIFIED in the worktree. Caught on the next `git status`. **Check
  the tree after any killed run before trusting anything downstream of it**, and run long batteries
  with `run_in_background`.
- 🔴 **A version label that MATCHES is not evidence the code is there, and my first probe of it was
  VACUOUS.** `clawgatectl view list` printed the parent help and **exited 0** — the verb is `ls`,
  and cobra treats an unknown positional as help. That is the 2026-08-14 silent-no-op shape at the
  VERIFICATION layer. The real check is a round trip that MOVES A NUMBER: write from one host, read
  from the other. `view ls` returning `[]` with rc 0 is indistinguishable from a client wired to
  nothing until the count has been watched go 0 → 1 → 0.
- 🔴 **An ABSENT check and a NOT-YET-SCHEDULED check are byte-identical in `gh pr checks`.** I filed
  "clawgate-e2e never registered on #566" as an open investigation minutes after opening the PR. It
  had registered and later passed. Naming one mechanism without naming the rival is the empty-result
  trap; wait for a scheduling window before concluding a check does not exist.
- 🔴 **`ship.sh` rc 11 can be a RACE, not a failure.** `origin/main` moved between the two legs, so
  the local leg verified against a sha that had already advanced. Both hosts landed the same commit
  and the immediate re-run was rc 0. Re-run before diagnosing — but still read the per-host lines,
  because a genuine skip hides among greens.
- 🔴 **A clean rebase is not a clean merge — but check, don't assume, in BOTH directions.** The
  first rebase of `devrc#1056` had a real semantic overlap (main's `#1076` had replaced the
  row-field ledger's permissive substring test with a two-way SET comparison, and #1056 adds
  fields; it was fine only because `tmux_server_id` lands on the HOST dict, not the row dict). The
  second rebase had **zero** commits touching either file. `git log <base>..<tip> -- <each file>`
  is what tells them apart in one command.
- **Assert the ATTRIBUTE, not the text.** A mutant that re-derived `data-hotkey-display` through
  `strings.ToLower` left the visible text `Alt+i` while the attribute became `alt+i`. A text
  assertion survives it.
- **A worktree of `homelab-talos` needs `e2e/node_modules`.** `package-lock.json` is **gitignored**
  there, so a fresh worktree has none and a naive `cmp` against the base clone reports DIFFERS when
  the file is simply ABSENT.
- **Docker on the workbench cannot pull, but `postgres:16-alpine` is cached**, so full-mode e2e runs
  locally. ⚠ Each `make e2e` **leaks** a `clawgate-e2e-pg-*` container — sweep them by exact name.
- ⚠ **A FRESH `nix-build` may give a playwright-driver whose chromium revision does not match the
  pinned `@playwright/test`**, and `make e2e` then dies at browser launch. An audit round concluded
  from this that the suite CANNOT run on this host; it can — `playwright.config.ts` sets
  `executablePath` explicitly from the bundle. The skew is real and worth its own fix, but it is not
  a property of the repo.

- 🔴 **RANK 8a's CLOSING CONDITION IS NOT STABLE, AND NOTHING CONVERGES EITHER HALF.**
  `clawgatectl` needs a CURRENT `~/workspace/homelab-talos` tree **and** a `home-manager switch`,
  per host — those are separate claims and they fail independently (measured: workbench had the
  tree but not the switch; laptop had neither). `ship.sh` is scoped to `~/workspace/devrc` and
  never touches `homelab-talos`; `drift-check.sh` rc 17 REPORTS source currency but does not fix
  it. **So any "clawgatectl is current" note — including this doc's — is true only of the moment it
  was written.** Re-run the round trip instead of reading it. Fix, both hosts:
  `git -C ~/workspace/homelab-talos merge --ff-only origin/trunk` then `ship.sh`.
- 🔴 **THE SHARED devrc CHECKOUT MOVES UNDER YOU — CHECK THE BRANCH IMMEDIATELY BEFORE ANY WRITE.**
  Caught at this handoff: `~/workspace/devrc` was on **`feat/memory-detail-click`** (another
  session's branch) with their uncommitted `nix/pkgs/default.nix` in the tree, 5 behind
  `origin/main`. A `handoff_doc.py --confirm` there would have committed onto THEIR branch — no
  conflict, no error, and `git log` afterwards shows exactly what you expect because you are
  reading the branch you landed on. **Author the handoff from a worktree off `origin/main`**, which
  also sidesteps the `stale-base` refusal a behind-checkout would have triggered.
- 🔴 **A FLOOR OR A PINNED CONSTANT MAY HAVE A SECOND CALL SITE THAT BOUNDS IT — grep the NAME
  across the repo before changing the value.** For 8e that was four files: two false hits (an
  unrelated `MAX_SKIPPED_IDS` JS constant; comments only in `eventlistener.yaml`) and one real
  second site. Changing one site alone leaves a guard asserting a stale reality.
- 🔴 **RE-DERIVE A PINNED COUNT FROM THE SOURCE, AND DO NOT TRUST A PER-FILE TALLY.**
  `routing.spec.ts` **collects 5 tests but only 4 are the no-DB ones** — it splits
  `SPA tab routing (no-DB shell)` (4, unguarded) from `SPA tab routing (full mode)` (1, behind
  `guardFullMode()`). A count of collected-tests-per-file reads `SHELL_ONLY = 37` and silently
  raises the floor's lower bound. ⚠ My first attempt at that tally also hit the documented **zsh
  no-word-splitting** trap — `for b in $MULTILINE` looped ONCE on the whole string and returned a
  plausible 32 that happened to be correct. Re-derived in Python rather than keep a number watched
  arriving by luck.
- 🔴 **PROVE A BOUND FIRES BEFORE TRUSTING IT — this one was MEASURED INERT once.**
  `test_clawgate_e2e_verdict.py` records an audit that set `MIN_PASSED=2` / `MAX_SKIPPED=1000` and
  watched **every case pass**. So the 8e change was verified by mutating the MANIFEST (what a
  person reaches for when the gate reds), never the test: `MIN_PASSED=2` → reds
  `test_the_floor_sits_ABOVE_a_database_less_run`; `=126` → `test_the_floor_sits_AT_OR_BELOW_a_healthy_run`;
  `MAX_SKIPPED=1` → the ceiling rejects the permanent skips; `=1000` → `test_the_skip_ceiling_admits_
  the_permanent_skips_and_little_else`. Each fires its OWN named bound.
- 🔴 **MERGED ≠ LIVE for anything Flux reconciles — and a mid-cascade snapshot looks exactly like a
  wedged one.** After #584 merged, the LIVE Task still carried `MIN_PASSED=110`; a PipelineRun
  executes the DEPLOYED object. Worse, the first read of the Flux chain showed `tekton-operator`
  "Reconciliation in progress" with `tekton-config` and `tekton-triggers` both blocked on their
  dependency — which reads as a broken GitOps delivery path. One minute later the operator was
  Ready on my own revision: it was a normal cascade caught in flight. **Re-read a dependency chain
  before reporting it blocked.** ⚠ Also: the floor is NOT in the TriggerTemplate. The chain is
  TriggerTemplate → `pipelineRef` → **Task `clawgate-e2e`**; a grep for `MIN_PASSED` across all 13
  TriggerTemplates returns **0**, which is a FAILING POSITIVE CONTROL, not a clean zero.
- ⚠ **A `finally:`-only restore is not SIGKILL-safe, and the Bash tool caps at 10 minutes** however
  long a timeout you request. A mutation battery was killed mid-run and left `internal/ui/layout.go`
  MODIFIED in the worktree. Run long batteries with `run_in_background`, and `git status` the tree
  after any killed run before trusting anything downstream of it.

- 🔴 **A SUBAGENT'S MUTATION TABLE IS A CLAIM ABOUT A RUN NOBODY WATCHED — and re-running ONE row
  is enough to catch it.** #591 arrived with an 8-row mutant→message table, a stated INVALID mutant
  (good sign), and a barrier-removal control. Re-running a single row showed the mutant dies by
  **panic one line above the barrier**, so neither the barrier nor the assertion is reached and the
  quoted `t.Fatalf` message cannot have been emitted. The tell was cheap and structural: **read the
  mutated code for a nil deref between the guard you removed and the call you expect to fire.**
  Here removing `|| a.NoteID == nil` guarantees `*a.NoteID` panics — the mutant was unrunnable by
  construction and no amount of re-reading the report would have said so.
- 🔴 **A CROSS-REPO WORKTREE MUST NOT BE MADE WITH `isolation: "worktree"`.** That flag worktrees
  the CWD's repo (`devrc`), not the repo the task names (`homelab-talos`), and the quiet failure
  mode is the worse one: the agent silently works in the wrong tree and your model of where the
  work happened is wrong. Create the worktree yourself with
  `git -C <target-repo> worktree add <PID-unique-path> -b <branch> origin/trunk`, check the exit
  code, and hand the agent the path.
- ⚠ **`homelab-talos`'s `.envrc` CANNOT be copied verbatim into a worktree.** It renders SOPS
  secrets from `.secrets/age.key`, `.secrets/flux-sops-secret.template.yaml` and
  `.secrets/github/…` — none of which a fresh worktree has — so every `cd` into it errors. The
  worktree recipe's "copy `.envrc`, drop the credential lines" means, here, writing a one-line
  `use flake` and `direnv allow`ing that.
- ⚠ **`clawgate_handoff.sh resolve` exit 5 is not "no task" — it is "cannot distinguish".** A wrong
  `CLAUDE_CODE_SESSION_ID` answers 200 with an empty array exactly like a session that touched
  nothing. Its positive control (another session's links resolving) proves only that a CORRECT id
  WOULD have resolved.
- ⚠ **The shared `~/workspace/devrc` checkout was on `feat/memory-detail-click` and 9 behind
  `origin/main` at this handoff**, with another session's uncommitted `nix/pkgs/default.nix` in the
  tree. Authoring the handoff there would have hit `handoff_doc.py`'s `stale-base` refusal at best
  and committed onto their branch at worst. Author from a worktree off `origin/main`.

- 🔴 **A MUTATION RUNNER THAT PRINTS "THE FIRST `file.go:NNN:` MATCH" CANNOT TELL A KILL FROM A
  DEATH UPSTREAM — and its output looks identical either way.** #591's original runner grepped
  `\.go:[0-9]+:` and printed the first hit. For a real kill that is the `t.Fatalf` line; for a
  panicking mutant it is a **stack frame**, equally plausible-looking. The sweep therefore could
  not have been evidence for the claim it was used to make, whatever the result. **A sweep must
  CLASSIFY each red — assertion / panic / compile error — and say which**; a bare "died" is not a
  kill. Same family as the kill-attribution parser that printed `by: FAIL:` for all 15 mutants.
- 🔴 **UNDER-REPORTING A MULTI-HUNK MUTANT IS INDISTINGUISHABLE FROM FABRICATING THE RESULT, and
  the re-runner will measure a DIFFERENT mutant.** #591's site-3 patch was two hunks; the report
  named one. Re-running the named hunk alone produced a panic — a true measurement of a mutant
  nobody had run, which read as "the reported evidence is false". Both parties were right about
  their own patch. **State every hunk of a mutant verbatim**, and when re-verifying someone else's,
  ask for the literal patch before concluding their number was wrong.
- 🔴 **`rc=0` FROM A `-run`-FILTERED GO TEST IS NOT "IT PASSED" — a filter matching NOTHING also
  exits 0**, and `--- PASS` lines do not print without `-v`, so a count of them reads 0 for a
  perfectly good run. Pair every filtered rc 0 with a positive control on the same filter
  (`-v`, count `--- PASS`) and check for `no tests to run`. This is the go-test face of the
  documented reassuring-zero class.

- 🔴 **AN EMPTY COMMIT CANNOT RE-TRIGGER A PATH-FILTERED PIPELINE — and the non-result is
  INDISTINGUISHABLE FROM A BROKEN TRIGGER.** `clawgate-ci-pipeline.yaml:13-14` filters pushes on
  `containers/clawgate/**`. An empty commit (the `--allow-empty` flag) touches no paths, so the
  webhook is filtered out, **no PipelineRun is ever created**, and `gh pr checks` says
  `no checks reported on the '<branch>' branch` — byte-identical to a trigger that is genuinely
  broken or absent. Measured here: push `4052bc61` (empty) produced nothing; re-doing it as an
  AMEND of the real commit (`c3040726`, tree byte-identical, `git diff` against the original
  **empty**) re-listed the two `.bats` paths and the run appeared. **To re-trigger a path-filtered
  pipeline the commit must touch a filtered path** — amending the real commit does it without
  changing a byte of content.
- 🔴 **A TEKTON CHECK'S TEXT AND ITS PIPELINERUN CAN DISAGREE — read the TaskRun's STEPS.** The
  check said "stopped before any leg reported"; the PipelineRun said `Tasks Completed: 2
  (Failed: 1)`. Both were true: the task hit `TaskRunTimeout`, and Tekton marks every unfinished
  step `exit=1 TaskRunTimeout` at once, so `go`/`extension`/`hook`/`verdict` all appear failed
  while none of them produced a verdict. `kubectl -n tekton-ci get taskruns -l
  tekton.dev/pipelineRun=<run> -o json` and read the per-step `terminated.reason` — **which step
  consumed the budget is the whole diagnosis**, and it is what tells you whether your diff could
  possibly be responsible.
- ⚠ **`ssh … | tail` HANDS YOU TAIL'S EXIT STATUS, and a bats run ending in `ok 67` looks like a
  pass either way.** A remote suite run this session reported "exit code 0" that was `tail`'s. Take
  the count and the rc **on the far side** — redirect to a file there, read `$?` from the runner
  itself, and count `^ok ` / `^not ok ` lines — then read those numbers back. Same family as the
  documented `| tail; echo rc=$?` failure.
- ⚠ **The `bash-guard.py` PreToolUse hook parses HEREDOC BODIES as real commands.** A gotchas
  block quoting `git commit --amend` / `--allow-empty` was refused as "commit on branch main".
  The hook's own message names the case: write prose with the Write tool and pass it by file
  (`git commit -F <file>`, `gh pr create --body-file <file>`), which the RULES prefer anyway.

- 🔴 **TEKTON MARKS EVERY UNFINISHED STEP `TaskRunTimeout` AT ONCE, SO THE STEP LIST ALONE DOES NOT
  NAME THE CULPRIT — read `exitCode`, not just `reason`.** A timed-out task shows `go`,
  `extension`, `hook` and `verdict` all "failed" whether the pod ran for 25 minutes or never
  started. The discriminator is whether the EARLIER steps carry `exit=0 Completed`
  (ran, then blew the budget) or `exit=None` (never executed at all). Reading only the reason makes
  a scheduling failure look like a slow test suite, and sends you to optimise or re-budget the
  wrong thing.
- ⚠ **A capacity-starved PipelineRun verdict is RECOVERABLE — re-run the spec, do not push.**
  `kubectl -n tekton-ci get pipelinerun <run> -o json`, strip `metadata.name`/`uid`/
  `resourceVersion`/`creationTimestamp`/`status`, set `generateName`, `kubectl create`. This
  re-reports the GitHub status for the same revision without a commit, which matters on trunk where
  a push means a deploy.

- 🔴 **A "CANNOT DO X" NOTE IN A HANDOFF IS A CLAIM WITH A SHELF LIFE, AND OBEYING IT COSTS NOTHING
  VISIBLE — WHICH IS WHY IT NEVER GETS RE-TESTED.** This session read "the workbench cannot pull
  docker.io", routed a container run to the laptop, and the detour WORKED — so nothing anywhere
  signalled that the premise was stale. A false "cannot" is self-preserving in a way a false "can"
  is not: the latter fails loudly on first use, the former just quietly buys a workaround forever.
  **When a doc tells you a capability is missing, spend the one command to check before building
  around it** — and when the check passes, ask whether the underlying cause is fixed or merely
  masked before rewriting the note. Here it was masked: the pull worked while the router was still
  serving a 485-day poisoned record.

- 🔴 **"FIXED BY #N" IS A CLAIM WITH A SHELF LIFE, AND A FLAKE IS THE WORST PLACE TO WRITE ONE.**
  This doc recorded `test_subsystem_store_api.py` as fixed by devrc#996 and it recurred — on a
  one-markdown-file PR, on a case name the doc had already written down. The failure mode is
  specific: a flake marked fixed reads as "your diff broke it", which is the most expensive
  possible wrong reading, because the person who hits it is the one with least reason to doubt it.
  **When closing a flake, record what was MEASURED (the fix, and the runs that passed) rather than
  the verdict** — and prefer "N consecutive green runs in the failing tier" to "fixed", because the
  first is falsifiable by the next red and the second silently absorbs it.

- 🔴 **A `hx-trigger` CURLED DIRECTLY FROM A FRAGMENT ENDPOINT CANNOT SHOW ITS REFRESH BEHAVIOUR —
  the PARENT drives it.** `GET /ui/tmux` and `GET /ui/layout` are htmx FRAGMENTS; the SPA shell
  (`GET /tasks`, `/tmux`, `/layout` — all `handleIndex`) mounts them as
  `<div id="panel-tmux" hx-get="/ui/tmux" hx-trigger="load, every 60s, …">`. Curling the fragment
  shows no `hx-trigger` and reads as "this tab has no auto-refresh", which is how this session
  nearly dispatched work to add polling that had existed all along. **Fetch the page a human
  loads, not the endpoint it calls.** The same read also makes `/ui/layout`'s 318-byte empty-state
  a reassuring zero for any attribute you grep it for.
- 🔴 **`tailwindcss_4` IS NOT THE `tailwindcss` THE DEPLOY RUNBOOK MEANS, AND THE RUNBOOK'S OWN
  TRAP-DETECTOR MISREADS IT.** Measured in one worktree: `nix-shell -p tailwindcss_4` emits
  **18,707 bytes with `.h-14` absent**; `nix-shell -p tailwindcss` emits **41,992 bytes with
  `.h-14` present**. The runbook says `grep -c '\.h-14'` returning 0 means the CSS-cwd trap fired —
  under `_4` that reads as the trap when the cwd was correct. Byte count discriminates them (the
  real trap yields ~5 KB). ⚠ Low stakes for a DEPLOY — the Dockerfile builds its own CSS, so the
  IMAGE is never affected — but it makes a local gate silently wrong, and `TestStaticAssetsServed`
  cannot catch it because it asserts app.css is *served*, not that it contains anything.
- ⚠ **The deploy runbook's "the workbench's `~/.docker/config.json` has no `harbor.homelab.lan`
  entry" is STALE.** Measured 2026-09-01: `auths` = `127.0.0.1:30022`, `ghcr.io`,
  **`harbor.homelab.lan`**, and `docker push` from the workbench succeeded rc 0. The laptop-push
  workaround is no longer required for that reason. (The docker.io *pull* situation is a separate
  entry above and is masked-not-fixed.)
- 🔴 **A ROLLOUT CHECK THAT ASKS "IS ANY POD ON THE NEW VERSION" PASSES MID-ROLLOUT.** Measured:
  the first check after `flux reconcile` returned Running pods on **both** 0.8.20 and 0.8.21. The
  condition must be "every Running pod is the new image AND none is the old", not "the new image
  appears" — the sibling of the documented `.items[0]` trap, from the other direction.

- 🔴 **A SCANNER THAT READS THE FILE IT LIVES IN CANNOT SPELL ITS OWN PATTERN — and the failure is
  a SILENT TRUNCATION, not an error.** Adding heredoc tracking to the bats scanners, the awk line
  `sub(/^<<[^A-Za-z0-9_]*/, …)` matched ITSELF: the scanner opened a heredoc tagged `A` on its own
  source line and skipped to EOF. Every downstream number stayed plausible — it simply reported
  **33 test bodies where grep counted 35**. The `BODIES` equality cross-check is the only thing that
  caught it; `EXAMINED > 0` was still true. Two separate manifestations, both measured: the REGEX
  (fixed by assembling the pattern in `BEGIN`) and PROSE describing it (fixed by an explicit comment
  clause). 🔴 **And the clause SURVIVED a green mutation run** until a fixture body was written for
  it — the "rule no mutant can kill" this very file's header warns about, walked into while adding
  a rule. Generalise: **any self-referential text scanner needs its own literals assembled, and a
  cross-check that fails DIFFERENTLY is what makes the truncation visible.**
- 🔴 **A POLL LOOP THAT CANNOT PARSE ITS STATUS EXITS IMMEDIATELY AND READS AS "CONCLUDED".** A
  wait-for-CI loop embedded a python one-liner in zsh, hit a quoting `SyntaxError`, captured an
  EMPTY string, and the `case "$s" in *pending*)` guard therefore did not match — so it broke out
  after one iteration and the background task reported success having measured nothing. The
  notification said "completed"; the run had not even started. **Never let an unreadable status
  share a branch with a concluded one** — test emptiness and non-zero rc explicitly, and say
  "still running" for both. ⚠ `gh pr checks` exits **8** while checks are pending, so `rc != 0` is
  not an error either.
- ⚠ **`ship.sh` reports `DIRTY AND IN THE ARTIFACT` and it is not cosmetic.** It classifies dirty
  paths against the set nix actually READS at eval/build time, so a host can be byte-identical to
  `origin/main` by SHA while the generation it just built is `origin/main` PLUS someone's
  uncommitted WIP. Cross-host "both at <sha>" is then true and still not host parity.
- ⚠ **An ABSENT Tekton check and a NOT-YET-SCHEDULED one remain byte-identical in `gh pr checks`,
  and the wait is ~15 minutes.** `clawgate-ci` was missing from #625's list on first read; it
  registered on the 5th poll iteration and passed. The doc already records this class from #566 —
  it recurred, and waiting is the whole remedy.

- 🔴 **THE WORKING-TREE COPY OF THIS DOC WAS 103 LINES STALE AT `/resume`, AND ONLY THE
  RECONCILER SAID SO.** Measured 2026-09-01: `~/workspace/devrc/claudedocs/handoff-tmux-webapp.md`
  held **1686 lines against 1789 on `origin/main`**, and `resume-state.sh` printed
  `handoff-read: 🔴 origin/main copy` with the authoritative text dropped at
  `/tmp/resume-handoff-*.md`. Reading the tree copy would have framed the whole session on a doc
  the last session did not write. **Read the `handoff-read:` line BEFORE opening the file** — the
  two copies are byte-plausible either way, and nothing else distinguishes them.
- 🔴 **A COMPILE ERROR MAKES A MUTANT INVALID, NOT A KILL — and the one that bit here looked
  perfectly reasonable.** Dropping `a.Status != agents.StatusRunning ||` from
  `notifyAgentRunning`'s guard removed the last use of the `agents` import, so the package stopped
  building. Scored INVALID and re-cut as a one-operator flip (`!=` → `==`), which keeps the import
  referenced and produces the same behavioural bug. **A battery that does not check the build
  before scoring reports that as a survivor or a kill, both wrong.**
- 🔴 **REPORT THE NUMBER YOU MEASURED, NOT THE ONE THE DOC CARRIES.** This doc recorded the
  detector loss as **0/20**; re-measuring it in a fresh worktree gave **1/20** — same direction,
  and the difference matters, because D2 turns a deterministic detector into a RACE rather than
  switching it off. A doc's measurement is a reading from one tree at one moment; re-run it rather
  than quote it.
- 🔴 **A STRUCTURAL GUARD MUST BE SHOWN DETERMINISTIC, NOT ASSUMED SO.** "It reads the AST, so of
  course it is deterministic" is reasoning, not measurement. Run it `-count=20` under the mutant
  and count `--- FAIL:` lines: 20/20 here. That number is what makes it a replacement for a
  20/20 behavioural detector rather than a hopeful one.
- 🔴 **A NEW GUARD'S REAL PROOF IS THAT THE EXISTING ONE STAYS GREEN.** Before writing #632 the
  question "is this a duplicate of the fan-out ledger?" was answerable in one run:
  `TestEveryPushFanOutGoesThroughTheOneChokePoint` is GREEN under BOTH the bug and the
  bug+`safeGo` mutant, because wrapping a CALLER moves no `push.Broadcast` call site. **Measure the
  incumbent under your mutant before building the challenger** — if it reds, you are about to add
  a second copy of a guard that already works.
- 🔴 **DERIVE THE SET, DO NOT SPELL IT — and check the derivation on the REAL tree before
  committing to it.** The obvious shape for #632 was a hardcoded `notify*`/`pushTask` name list;
  the shape that shipped derives push-deciders as transitive callers of `goPushBroadcast` (39
  functions) and spawners as "takes a func parameter and contains a `go`" (which finds `safeGo`
  and `goPushBroadcast`, and would find a sibling helper). ⚠ **A derived set can over-reach into a
  permanently-red gate**, so it was measured on trunk FIRST with a throwaway `go run` probe — 0
  violations — before a line of the test was written. **A permanently-red gate is worse than no
  gate; spend the probe.**
- ⚠ **`goPushBroadcast` is itself a spawner, and flagging its own goroutine would make the guard
  permanently red.** It falls out correctly rather than needing a special case — the `go func(){}`
  inside it calls `s.push.Broadcast` and `onDelivered`, neither of which is a declared function in
  package `api` — but the negative control pins it explicitly, because the next person to widen
  the finder will not know that.
- ⚠ **`gofmt -l` on this package lists SIX pre-existing unformatted files** (`agent_test.go`,
  `agents_test.go`, `attention_reap_loop_test.go`, `auto_approve_persist_test.go`,
  `noop_provisioner.go`, `push_task.go`). They are on trunk, not yours. `gofmt -w` the FILE you
  edited, never the directory — the doc already records that `gofmt -w <dir>` silently widens a
  diff by seven files.
- ⚠ **`homelab-talos`'s `.envrc` is TRACKED, so the worktree recipe's "write a one-line `use
  flake`" shows up as a modified file.** Harmless until you stage it. `git -C <wt> checkout --
  .envrc` before committing, and check `git status --porcelain` shows only the file you meant.
- ⚠ **`parsePackageFilesWithPositions` and `sortedKeys` already exist in
  `task_status_ledger_test.go`** — the compiler caught the duplicate immediately, which is the
  cheap outcome. Reuse them: the shared parser owns the "0 files scanned is the failure, not the
  all-clear" check, and a second copy is exactly how that check ends up true in one ledger and
  forgotten in another.

## How to verify

```bash
clawgatectl health                 # the LIVE version; never expect a number from this doc
clawgatectl --version              # must MATCH the server on BOTH hosts
```

🔴 **A MATCHING LABEL IS NOT ENOUGH — make a number move.** The 2026-08-14 incident shipped a
correct-looking label over a binary missing the commands, and a bare `view ls` returning `[]` with
rc 0 cannot be told from a client wired to nothing. ⚠ **The verb is `ls`, not `list`** — `list`
prints help and exits 0.
```bash
ssh zach@10.42.0.100 'clawgatectl --version; clawgatectl health'   # laptop label agreement
ssh zach@10.42.0.100 'clawgatectl view create verify-probe'        # WRITE from the laptop
clawgatectl view ls                                                # READ on the workbench: 0 -> 1
clawgatectl view rm <id>; clawgatectl view ls                      # back to []
bash ~/workspace/devrc/scripts/ship.sh   # rc 0 + cross-host agreement COMPARED (not "NOT COMPARED")
```

**Rank 8b — the layout spec:**
```bash
make -C ~/workspace/homelab-talos/containers/clawgate e2e ARGS="layout.spec.ts"   # expect 2 passed
gh pr checks 566 --repo ZacxDev/homelab-infra   # 🔴 clawgate-e2e must APPEAR, not merely pass
```
Needs Docker (`postgres:16-alpine` is cached locally) or `CLAWGATE_E2E_DATABASE_URL`.

**Is rank 7 (the layout grid) actually working?**
```bash
TOK=$(grep -E '^\s*(export\s+)?CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | tail -1 | sed 's/.*=//; s/"//g')
B=http://192.168.50.250:30302
curl -s -o /dev/null -w '%{http_code}\n' "$B/ui/layout"     # 200
```
🔴 **Seed a view before trusting any absence.** An empty layout page is ~318 bytes and trivially
contains zero of everything — a "0 hx-delete" on it is a reassuring zero, not a measurement:
```bash
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"name":"verify"}' "$B/api/layout/views" >/dev/null
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"host":"laptop","codename":"wheat","windowName":"devrc"}' \
  "$B/api/layout/views/verify/panels" >/dev/null
curl -s "$B/ui/layout" > /tmp/l.html
grep -c 'hx-post'   /tmp/l.html    # POSITIVE CONTROL — must be >0, proving the scan sees controls
grep -c 'hx-delete' /tmp/l.html    # MUST be 0
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE "$B/ui/layout/views/1/panels/1"   # 404 = route gone
# clean up: DELETE /api/layout/views/<id> with the token
```

**Is rank 5's terminal tier still fail-closed?** The boot line is UNCONDITIONAL and in both
directions — its absence is the alarm, and a silent healthy surface and a silently-refusing one are
otherwise byte-identical:
```bash
KUBECONFIG=$KC_WORKBENCH kubectl -n clawgate logs -l app=clawgate --tail=-1 \
  | grep 'terminal write surface'
# expect: DISABLED (fail-closed) — CLAWGATE_TERMINAL_TOKEN is not set
```

**Did a migration actually apply?** A rollout can half-do a schema change silently:
```bash
KUBECONFIG=$KC_WORKBENCH kubectl -n clawgate logs -l app=clawgate --tail=-1 | grep 'applied migration'
```
⚠ A pod that did NOT apply one logs nothing — absence means an EARLIER pod applied it, not that it
is missing. The functional proof is stronger: create and delete a view through `/api/layout/views`,
which requires both `layout_views` and `layout_panels` to exist.

**Is the idle reaper running?** (rank 1's answer — for ~9h it was NOT, and nothing said so)
```bash
# 1. it announced itself at boot. This line is unconditional; its ABSENCE is the alarm.
KUBECONFIG=$KC_WORKBENCH kubectl -n clawgate logs -l app=clawgate --tail=-1 \
  | grep 'attention reaper: sweeping every'
# 2. it has swept something (logs ONLY when N>0, so an empty result is NOT a failure)
KUBECONFIG=$KC_WORKBENCH kubectl -n clawgate logs -l app=clawgate --tail=-1 | grep 'attention-reap:'
```
🔴 **Validate that instrument before quoting a zero** — `GET /api/attention` defaults to
`state=open`, so cross-check `?state=resolved` and `?state=all` and confirm open+resolved==all.
Measured 2026-08-27 pre-fix: open=59, resolved=31, all=90 — the filter discriminates.

**The attention feature's own proof is end-to-end, not a unit test:** trigger a real
`AskUserQuestion`, then confirm a `kind=question priority=high` row appears via
`curl -H "Authorization: Bearer $CLAWGATE_HOOK_TOKEN" http://192.168.50.250:30302/api/attention`
— and that it sorts **above** the `idle` rows. Entry fields are camelCase (`sessionId`).

**Per host, confirm the hook that will actually run:**
```bash
readlink -f "$(jq -r '.hooks.PermissionRequest[].hooks[].command' ~/.claude/settings.json | sed 's/^CLAUDE_HOST=[a-z]* //')"
```
must terminate inside `homelab-talos`, and that file must contain `raise_attention_question`.

**Rank 13 — the lock discriminates lettered sub-ranks.** `claim-work` resolves into the WORKING
TREE, so a base-clone `merge --ff-only` is what makes a fix effective; there is no switch:
```bash
for r in 8 8c 8d 8C; do printf '%s -> %s\n' "$r" \
  "$(claim-work --slug-for claudedocs/handoff-tmux-webapp.md $r)"; done
# expect tmux-webapp-8 / -8c / -8d / -8c   (8C folds; 8c != 8d != 8)
claim-work --slug-for claudedocs/handoff-tmux-webapp.md 8-c   # rc 2 — a REFUSAL, not a silent drop
```

**Ranks 15 + 16 — the bats suites and their collected-test floors, in the leg's OWN image:**
```bash
cd ~/workspace/homelab-talos/containers/clawgate
docker run --rm --entrypoint /bin/sh -v "$PWD:/c:ro" -e HOME=/tmp/bh \
  docker.io/bats/bats:1.11.1 -c 'mkdir -p /tmp/bh; apk add --no-cache jq >/dev/null
    cd /c && bats --count hook/tests/clawgate-hook.bats hook/tests/clawgate-stop-hook.bats'
# 2026-09-01: 35 and 32; the floors are measured-minus-one. Re-derive, never tune until green.
```
🔴 **Rank 16's live proof is a REAL PipelineRun, not a local run — and ONLY a PR touching
`containers/clawgate/**` produces one** (the leg is path-filtered, so a `clusters/**`-only change
triggers nothing, and a PipelineRun runs the DEPLOYED Task rather than the PR's manifest):
```bash
kubectl -n tekton-ci logs pod/<clawgate-ci-run>-clawgate-ci-pod -c step-hook | grep -E 'floor=|^1\.\.'
# expect TWO plan lines and floor=34 / floor=31. A single `1..67` means the Task did not reconcile.
```
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
