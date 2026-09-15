---
clawgate-task: 375
---
# Handoff: tmux-webapp — 2026-08-26

## Goal
A **clawgate feature**: a webapp that visually organizes and gives live terminal interaction
with tmux sessions across workbench + laptop, with a composable view system agents can drive,
and an **attention queue** that surfaces sessions needing a human so Zach can jump straight in.
- **closing-condition:** `check` — `clawgatectl task get 593` reports `ready_for_review` with
  all ten acceptance criteria evidenced in a card comment (each regression test watched RED at
  its base commit and green at HEAD). 🔴 **FROZEN AT ROUND 1.** Task 595 (the carousel, bulk
  expand/collapse, bulk Chat/Raw) is a NEW arc and does NOT extend this one; neither does any
  audit finding on the PRs this arc produced. Added 2026-09-15 — this doc predated rule (m) and
  had been UNANSWERABLE against a closing condition for its whole life.

## Status

**This session RESUMED: it merged the in-flight PR and dispatched the next batch. Neither the arc nor task 593 is finished.**

### clawgate task 593 — items 1, 2, 3, 9 DONE and MERGED; items 4–8 IN FLIGHT
- **`ZacxDev/homelab-infra#824` MERGED** (squash `81b10cddc`) — item 1: session groups default to **collapsed**; group key moved to `cg.tmux.v3.group.*` storing EXPANDED state (absent = collapsed). `ACKPREFIX` deliberately left at v2. Includes the **parked-draft summary badge** (`data-tmux-group-parked`), closing a regression item 1 itself introduced.
- **`ZacxDev/homelab-infra#826` MERGED 2026-09-15T16:40:49Z** (squash **`248a0c4ed`**) — items 2, 3, 9. The previous session left it OPEN with `clawgate-e2e` RED at head `04cef09c3` (9 failed / 216 passed) and `6fad0e252` pushed as the repair but **never re-run**. This session re-read the checks: **all four green on `6fad0e252`** — `clawgate-e2e` **225 passed / 2 skipped** (the full 34-spec tier), `clawgate-ci`, `gitops-validate` (10 legs), `ux-audit-clawgate`.
  🔴 **The squash was verified BY CONTENT, never by ancestry.** All six files the branch touched are byte-identical between the branch head and `origin/trunk`. `origin/trunk..HEAD` still lists four commits — that is the documented squash artefact, NOT unmerged work, and reading it as unmerged is what would have stopped the worktree being cleaned up.
  Worktree `~/workspace/homelab-tmux593b` REMOVED, branch deleted locally and on the remote, `homelab-talos` base clone fast-forwarded to `248a0c4ed`.
- **Items 4–8 IN FLIGHT** — branch `feat/tmux-593-items-4-8`, worktree `~/workspace/homelab-tmux593c`, based on `248a0c4ed`. Branch was pushed EMPTY before any work, so a concurrent session can see it. Claim `clawgate-593-items-4-8` is HELD — release it when the PR lands.
- **Task 595** holds the carousel, bulk expand/collapse and bulk Chat/Raw. Do 593 first.

### 🔴 THREE OPERATOR DECISIONS, TAKEN BEFORE THE WORK — do not re-litigate
1. **Item 6 (`selection_menu`)** → filter ONLY the `selection_menu` signal out of the waiting-evidence line, and ONLY when the card also renders structured question options. `trailing_question` and `context_exhausted` always survive; `selection_menu` still renders when no options are shown. Chosen over "drop it unconditionally" and "drop the whole evidence block", both offered, because the block exists so the operator can DISAGREE with a scraped `waiting: true` verdict and the `needsHuman` gate is deliberately wider than `== TriageWaiting`.
2. **Item 4 (structured tool cards)** → a tool-name table in `internal/ui` for exactly Bash/Edit/AskUserQuestion, with raw-JSON fallback for unrecognised tools, parse failures, and the payloads `clampToolInput` truncated mid-token at 4000 chars. Chosen over "raw always available behind a toggle" and "generic key/value for all tools".
3. **Item 5 (max height)** → clamp the tmux card's pretty panel (`max-h-64`, mirroring its Raw sibling at `tmux.go:2656` but WITHOUT `flex-col-reverse` — a transcript reads top-down) AND move `open full session →` ABOVE the transcript. `/session/{id}` is deliberately NOT clamped. Chosen over "clamp only" and "lazy-load older messages on scroll-up".

### devrc — the doc's own size gate
- **`docs/tmux-webapp-evict-closed`** (commit `61ab9487`) — this doc stood at **245,697 B against a 245,760 B allowance: 63 B**. 21 of 40 CLOSED `### ` blocks demoted verbatim to `claudedocs/refs/tmux-webapp-closed-investigations.md`; **245,697 → 198,795 B**. The allowance was NOT raised (playbook step 4, and it was not needed).

### 🔴 NOT VERIFIED
Items 4–8 are being implemented as this doc is written. **No gate has been run on `feat/tmux-593-items-4-8`, no PR exists, and nothing about criteria 4–8 is claimed.** Criterion 10 is satisfied for items 1, 2, 3, 9 only.

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
| **Agent asks a question** | ✅ **LIVE — and this row said "🔴 Silent today" until 2026-09-04, when a probe refuted it.** `raise_attention_question` (`hook/clawgate-hook.sh:102`) fires on `AskUserQuestion` (`:201`) and files an entry; the terminal prompt is unaffected either way. The old text was true when written and outlived the fix. **Measured live: 36 open entries — 2 `question`, 34 `idle`.** What is still missing is not the RAISE but the ANSWER — see ranks 28–30. |
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


🔴 **Ranks 1–47, 50–52, 55, 58, 61–62 are CLOSED and were DEMOTED — verbatim, not deleted:** `claudedocs/refs/tmux-webapp-closed-ranks.md`. They are lessons rather than status, which is why they were demoted and not dropped. 🔴 Rank 18 joined them 2026-09-14 when `ZacxDev/homelab-infra#820` merged — an item completed AFTER a sweep must be evicted in the same change that closes it, or the queue offers finished work to the next session. This doc stood at 327,624 B of a 327,680 B budget (56 B), and the next routine handoff write would have turned `test_no_handoff_doc_exceeds_its_budget` red on `main` for everyone; `claudedocs/refs/` is exempt from that test, `claudedocs/handoff-*.md` is not.

🔴 **A SECOND SWEEP 2026-09-14 EVICTED SIX MORE — 9, 17, 25, 26, 42, 45 — AND EVERY ONE HAD BEEN CLOSED FOR DAYS WHILE STILL READING AS OPEN.** Six of the fifteen entries the queue advertised were finished or fictional. **The mechanism is measured, not guessed: the first sweep keyed on each rank's HEADING LINE — a heading carrying `✅`, `DONE` or `CLOSED` — and every closure recorded only in a rank's BODY survived it**, predicting eviction for 58 of 62 ranks. Of the four exceptions, 53 is a false positive of the predicate (`WORKBENCH IS NOW DONE` in the heading, laptop half genuinely open); 61's closure was written as a `### ✅ RESOLVED` heading in the **Open investigations** section, a THIRD location; and 46 and 62 carried no marker anywhere and were evicted anyway — 🔴 **why is NOT explained, and an earlier draft of this sentence guessed "closed by the sweep session itself", which was false for two of the three it named.** What IS established is the false-NEGATIVE direction: **no rank with a body-only marker was ever evicted**, measured over all 62.

🔴 **THE MARKER LIVES IN THREE PLACES, AND A SCAN THAT READS ONLY THE RANK BODY MISSES THE THIRD.** Heading line → 58 of 62, evicted. Body only → 17, 25, 26, **survived**. A `###` heading in another section → 61, evicted. Nowhere at all → **42, 45, 46, 62** — the sweep caught 46 and 62 and missed 42 and 45. So **the no-marker class is FOUR, not two**, and a body scan alone would have left two of them. A session claimed 42, re-derived from source that all three residuals were already gone, and paid a full recon round for it. **When you sweep: read each rank's WHOLE body, read the `###` headings elsewhere in the doc, and re-measure anything still carrying no marker against the code.** The deterministic version — a test failing when a live-queue rank carries `✅ DONE`/`CLOSED` — does not exist yet; it would have caught 17, 25, 26 and 61, and it needs care, because rank 53 would be its first false positive.

🔴 **The surviving numbering is SPARSE ON PURPOSE — do not renumber and do not reuse an evicted number.** A rank is half a `claim-work` claim's identity (`claim-work --slug-for <this doc> <rank>`), so renumbering silently re-points every live claim, and reusing an evicted number points a new claim at closed work.

48. **Re-spec task 521 around a per-agent token.** `tier` discriminates the DOOR (`token` vs
   `browser`), not the caller; one shared `CLAWGATE_TERMINAL_TOKEN` makes every machine caller
   identical. `requireAgentToken` (`internal/api/agent.go:30-46`) already resolves a per-agent
   token to a named row — that is the shape. Blocked until 375 closes.
   forcing: security — 521 concentrates send-keys-into-any-pane plus process launch behind a chat
   box, on an auth tier whose attribution is measured non-existent.
49. **Task 522** needs 521 AND an LLM-client decision — clawgate's `go.mod` carries no
   anthropic/openai dependency, so an "agent" there is a kubeclaw pod it provisions.
   forcing: none

53. **Measure criterion 1 of task 519 on the LAPTOP — WORKBENCH IS NOW DONE, laptop is not.**
    ⏳ **RE-VERIFIED STILL BLOCKED 2026-09-12T17:3xZ, and the block is unchanged:** the laptop's
    freshest Claude pane is `%29` (`vetr`) at **35,378s ≈ 9.8h** old (`last_activity_ts`
    07:32:49Z); 9 claude panes there, next freshest 15.7h. Nothing to measure until a human types
    on that machine — an idle host still cannot demonstrate stream latency, so this needs the
    operator, not a fix.
    🔴 The workbench number exists: **2.0s and 3.1s**, induced-append, measured twice with
    `.opencode-dispatch/tmux-ui-verify/scratch/measure519.py` (in devrc, git-ignored) — it uses
    THIS KIND OF SESSION as the subject, because a Claude Code session appends to its own
    `.jsonl` on every tool call, so the append is guaranteed rather than waited for. Both
    timestamps are taken by one process on one machine, so no cross-host clock is involved. Both
    are far under the 30s htmx fallback and the 300s bulk push, which is what makes it the
    stream. **Re-run that same script from a session ON THE LAPTOP and the laptop number
    follows** — no clawgate change needed, only an active session there.
    ORIGINAL: Measure criterion 1 of task 519 on the LAPTOP. The stream is confirmed delivering from
    that host (4 sessions with `reason: accepted` and real byte offsets on
    `GET /api/transcripts/stream/cursors`), but there is no latency NUMBER because no laptop
    session has been active since 03:37Z — an idle host cannot demonstrate stream latency. The
    probe: one active Claude turn on the laptop, then re-run the tmux x transcript join and look
    for a 0-5s gap the way the workbench sessions show. Repo: none — it is a measurement.
    forcing: gate — task 519's own closing condition names both hosts and a measured number, and
    that is the only criterion still unmet.
54. **The operator-facing half of 519/517/518 — `/ui/tmux` needs a signed-in human.** Measured:
    `/ui/tmux` returns 401 without a session, with `/health` 200 as the control, so no shell can
    exercise it. Three cards are waiting on one visit: 519 (seconds-fresh session view), 517
    (reply-delivery state renders, `#754` merged), 518 (JSONL chat view renders, `#753` merged).
    forcing: user — all three cards name an operator observation in their closing condition.

56. **Decide whether 519's criterion 1 should keep naming the RENDER hop.** What is measured is
    propagation to clawgate's READ MODEL (2.0s/3.1s). The criterion says "visible on
    `/session/<id>`". The page carries `hx-trigger="… sse:transcript.changed …"` but the render
    hop itself is unmeasured, and an earlier attempt in this arc FAILED by treating that
    attribute as behaviour. Either measure the render or reword the criterion to the read model.
    forcing: none
57. **Task 519 criterion 4 — the deliberately-induced stream gap — has never been tested.**
    Present evidence is incidental only (idle laptop sessions carrying rows newer than their last
    host activity). Inducing a real gap means stopping the host agent or rolling the pod, which
    is a deploy-class action; decide whether it is worth it before doing it.
    forcing: none
59. **PR B — the grid's free-form reply delivery axis**, parked at `hold/grid-freeform-axis`
    (`aa11176c8`, ~460 payload lines + a JS hydrator + a new authed route + a new store read).
    🔴 **Cut it from `trunk`, NEVER stacked on a merged branch** (this repo's CLAUDE.md bans stacked
    PRs by name). Round 0 on #811 judged it worth its own review: by its author's own statement the
    axis exists only in the tab that sent the reply and **goes blank on reload**. The alternative it
    names — a per-session actor on the write row, letting the render-time lookup be "newest write
    for this (host,pane) BY THIS SESSION" — would fix the reload gap too, but changes the meaning of
    an audited credential column. Decide that before building.
    forcing: none
60. **The other half of the delivery freeze: the axis still sticks when the host agent DIES.**
    `pending`+stale → `failed` (90 s TTL) and `claimed`+stale → `unknown` (10 min grace) are
    TIME-driven; nothing broadcasts them (correctly — no row moves) and `#panel-attention` has no
    poll. The session page (30 s) and tmux grid (60 s) self-heal; the attention panel does not.
    Pre-existing, and the post-fix frozen text (`sending`) is the safer error — but "stop the axis
    freezing" closed one half of the class.
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

🔴 **CLOSED investigations are DEMOTED, not deleted — verbatim in `claudedocs/refs/tmux-webapp-closed-investigations.md`.** Every block whose thread reached a verdict was moved there when this doc came within 63 B of its size ceiling. They are lessons, not status, which is why they were demoted and not dropped. ⚠ A `refs/` file is NOT indexed by `handoff_search` — search will not surface them, so **this pointer is the only index into that file**. 🔴 **That is exactly why the COUNT and the list of threads are deliberately NOT restated here.** Both are derived facts that go stale in the very commit that evicts the next block, and this pointer has already been wrong that way: it read *"21 blocks"* and enumerated five threads while the file held **24**, because the commit that evicted the extra three did not touch it. List them at the moment you need them, which also cannot go stale: `grep -n '^### ' claudedocs/refs/tmux-webapp-closed-investigations.md`.

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

### The ux-audit funnel walk still SKIPS `auto-approve-armed`, and the arming failure is unexplained

- **Symptom + exact repro:** `make ux-audit` (or `tekton/clawgate-ux-audit`) on any recent revision.
  The funnel walk logs `ux-audit SKIP auto-approve-armed (/): could not reach the state:
  expect(locator).toBeVisible() failed / Locator:
  locator('#auto-approve-banner').getByText('Auto-approve ALL is ON') / Error: element(s) not found`.
  The walk then CONTINUES — that view is non-core, record-and-skip.
- **Observed (with values):** present on `clawgate-ux-audit-pnnkw` (trunk `29504948`, BEFORE the tmux
  page work) and again on the post-fix re-run `clawgate-ux-audit-rerun-9nntr` (`963f768c`), which
  reported `12 view-rows, 1 skipped` with the funnel test itself PASSING. So the arming failure
  survives `#667`'s walk fix and predates it.
- **Ruled out:** that `#667` introduced it — the same skip appears on trunk `29504948`, which
  predates the PR. via: measurement
- **Ruled out:** that it is the rank-17 health-check budget — that failure reads `clawgate health
  check did not pass on port <N> within 15000ms` and kills the run before the walk; this one occurs
  with the budget cleared (`captured one view in 934ms`) and lets the walk continue. via: measurement
- **Ruled out:** that the arming controls were removed by `#667` — `[data-global-aa]`
  (`components.go:1258`), the `aria-label="Turn off auto-approve all"` button (`:1275`) and
  `[data-global-aa-control]` as a `Details`+`Summary` (`:1308`) all still render. via: code
- **Leading hypothesis:** the arming POST lands but the banner text is not observable when the walk
  reads it — i.e. an observation race on `#auto-approve-banner`, not a failure to arm. UNPROVEN.
  🔴 If the POST *does* land, the server is left ARMED, which is exactly the state `#667`'s disarm
  reconciliation now cleans up unconditionally — that is why the walk survives it.
- **Next probe:** instrument the arming step the way the disarm step now is — print the SERVER's
  answer (`GET /ui/auto-approve-banner`) beside the walk's DOM-derived one, immediately after the
  duration click, and compare. The disarm probe shape is already in
  `e2e/ux-audit/clawgate-funnel.audit.ts`; reuse it rather than inventing one.

### `open_window`'s read-back rejects a window it already created — intermittent, mechanism identified

- **Symptom + exact repro:** `test_the_EXACT_session_still_works` and
  `test_a_cwd_that_does_not_exist_is_REFUSED_not_opened_in_HOME` fail
  intermittently against REAL tmux with
  `AssertionError: tmux did not report the new window's identity (got '%2\tscratch20')`.
  **Measured 2 failures in 10 runs**, one of them on a completely UNMUTATED tree,
  so it is independent of the arming change. Wall time on the failing run was
  37.69 s against a 37.23 s control — **not a load flake by the wall-time test.**
- **Observed (with values):** `NEW_WINDOW_FORMAT` is
  `"#{pane_id}\t#{session_name}\t#{pane_current_path}"` (`scripts/tmux-reply-agent:389`).
  `scripts/tmux-reply-agent:573` does `out.strip().splitlines()[0].split("\t")`
  and rejects anything that is not 3 fields. `'%2\tscratch20\t'.strip()` is
  `'%2\tscratch20'` — **2 fields** — and that is byte-identical to the observed
  failure string. So an empty trailing `#{pane_current_path}` collapses a
  well-formed response and the window is rejected AFTER tmux already created it:
  the caller gets an error and a stray window is left behind, and a retry would
  make a second one.
- **Ruled out:** load flake — wall times of the failing and control runs are
  within 0.5 s of each other, and the whole-suite times did not move. `via: measurement`
- **Ruled out:** caused by the rank-32 arming change — it reproduces on an
  unmutated tree, and `scripts/tmux-reply-agent` has zero changed lines in the
  later audit ranges. `via: measurement`
- **Ruled out:** a malformed format string — 8/8 direct probes of
  `tmux new-window -P -F '#{pane_id}\t#{window_id}\t#{session_name}' -t '=scratch20:'`
  against a private `-L` socket returned a well-formed 3-field line. `via: command`
- **Leading hypothesis:** `pane_current_path` is transiently empty immediately
  after `new-window`, before the pane's process cwd is readable. ⚠ **INFERRED,
  NOT FORCED** — I could not make tmux produce an empty field on demand. The
  byte-exact match is strong; the trigger condition is not reproduced.
- **Next probe:** run
  `tmux -L <probe> new-window -P -F '#{pane_id}\t#{session_name}\t#{pane_current_path}' -c <dir>`
  in a tight loop under load and count empty third fields; or instrument
  `run_tmux` to log the raw bytes on the 3-field check failing. The fix is
  one line either way — split BEFORE stripping, or `out.strip("\n")` — but
  **do not fix it blind**: pin the empty-field case in a test first, because a
  stub tmux that always succeeds is exactly what hid this class before.
- **Scope:** PRE-EXISTING, from `#1324`. Lives in `open_window`, which is rank
  31's "start a session" path — **not** the `send-keys` reply path — so it does
  not block arming. It IS in a now-live surface.
- **Closing condition:** the empty-`pane_current_path` case is pinned by a test
  that fails on today's code, the fix merged, and the two named tests run 20×
  without a failure.

### `ExecStart` runs the working tree, not the audited store copy

- **Observed:** `nix/home.nix:3871` — `ExecStart = … %h/workspace/devrc/scripts/tmux-reply-agent`;
  `_load_tmux_text_policy()` resolves `scripts/lib/tmux_text_policy.py` relative
  to `__file__`. `X-Restart-Triggers` pin the STORE copies, so the trigger fires
  only on a switch — but the process execs the checkout.
- **Why it matters now:** arming is what makes it live. Any session's
  `git checkout` in `~/workspace/devrc`, plus any restart (crash, reboot, the
  `sd-switch` Stop/Start above), silently swaps the text-policy validator on the
  running process with no review and no `home-manager switch`.
- **Ruled out:** introduced by this PR — it is the repo's house pattern (~20 units
  use `%h/workspace/devrc/…`) and the comment-stripped nix diff for the whole PR
  is 0 lines. `via: measurement`
- **Closing condition:** a decision recorded on the PR or in this doc — either
  accept it explicitly for this unit, or point `ExecStart` at the store copy and
  confirm the agent still resolves its text policy.

### `test_readiness_reopens_on_transient_tab_gone` hangs intermittently in the full-suite run

- **Symptom + exact repro:** during `scripts/run-tests.sh` on the merged tree,
  `scripts/browser-bridge/tests` reported `914 passed / 1 failed` with
  `subprocess.TimeoutExpired: Command '[…/scripts/browser-bridge/browser-agent',
  'read the page']' timed out after 60.0 seconds`.
- **Observed (with values):** the test's own message says *"Spawning 10 trivial
  processes on this machine just now took 0.24s (idle reference 0.10s; stall
  threshold 0.80s), so the MACHINE is not the explanation and the wrapper
  genuinely hung"*. ⚠ That control measures **process-spawn latency only** — not
  lock contention and not I/O — so it does not exonerate a loaded box the way it
  reads. At the time, four other sessions were running
  `nix build …checks…pytests` concurrently, load ~36 on 24 cores, 45 GiB swap in use.
- **Ruled out — caused by this PR:** the diff touches four files
  (`nix/home.nix`, `scripts/tmux-reply-agent`, and two test files) and **zero**
  browser-bridge lines; `git diff --stat origin/main...HEAD -- scripts/browser-bridge/`
  is empty. `via: measurement`
- **Ruled out — a deterministic break on this tree:** the same test at
  `origin/main` passed 1/1 in 1.94 s, and on the PR branch in isolation **5/5**.
  `via: command`
- **Leading hypothesis:** a load-sensitive hang in `browser-agent`'s startup path.
  A branch named `fix/browser-agent-warm-lock-stall` already exists in this repo,
  so the class is known and owned elsewhere.
- **Next probe:** if it recurs in the gate, run
  `nix develop <repo> -c python3 -m pytest <repo>/scripts/browser-bridge/tests/ -k readiness -q`
  under deliberate load and capture the wrapper state dir the failure names
  (`…/popen-gw*/test_readiness_reopens_on_tran0/scratch`) rather than re-running.
- 🔴 **Do NOT merge past a SECOND occurrence.** One red plus these controls is a
  flake outside the diff; twice is a blocker, even outside the diff — a gate you
  re-roll until green is not a gate.
- **Re-run result:** on the next full tier-1 run (head `3d470502`) this test
  PASSED and the suite was `21,936 / 21,939, 0 failed`. So: one hang, one clean
  full run, 5/5 isolated on the branch, 1/1 at `origin/main`. Consistent with the
  load-hang hypothesis; NOT a second occurrence.

### `devrc-ci` pytests red on `test_mjs_parses[attachments.mjs]` — CONTENTION, not a parse error
- **Symptom + exact repro:** `tekton/devrc-pytests` on `devrc#1353` (a ONE-markdown-file
  diff) reports `FAILING: test_mjs_parses[attachments.mjs]`, 2 failed of 22,002.
- **Observed (with values):** the failure is `subprocess.TimeoutExpired`, not a
  `SyntaxError` — `["node", "--check", ".../claude/skills/clickup/api/attachments.mjs"]`
  did not return within `orig_timeout = 30`, with `stdout_seq = []` and
  `stderr_seq = []`. Node produced NOTHING; it never got to parse the file.
- **Ruled out:** a real syntax error in `attachments.mjs` — the same test on plain
  `origin/main` in a clean worktree gives `34 passed in 1.24s`. via: command
- **Ruled out:** caused by this PR — the diff is `claudedocs/handoff-tmux-webapp.md`
  alone, the branch is **0 commits behind `origin/main`**, and `attachments.mjs`
  was last touched by an unrelated commit (`9004af88`). via: measurement
- **Leading hypothesis:** the same node-contention class as the `clawgate-e2e`
  health-check reds seen repeatedly today — a 30s budget for `node --check` on a
  small file is only exceeded when the box cannot schedule node at all. This
  session was concurrently running a docker build, an image push and several
  playwright suites.
- **Next probe:** `devrc-ci-rerun-qrcm5` was created from `devrc-ci-92rmf`'s own
  spec on the identical revision `e85b1aecb`; read its verdict. A green re-run on
  the same revision completes the attribution. If it reds again on the SAME test,
  the contention reading is wrong and the 30s timeout in
  `scripts/tests/test_skill_mjs_parses.py:70` is the thing to look at.

### `ZacxDev/homelab-infra#749`'s four checks are REGISTERED but NONE is terminal

- **Symptom + exact repro:** `gh pr checks 749 --repo ZacxDev/homelab-infra`
  at handoff time.
- **Observed (with values):** all four gates present and all four `pending` —
  `tekton/clawgate-ci`, `tekton/clawgate-e2e`, `tekton/gitops-validate`,
  `tekton/ux-audit-clawgate`. `gh pr view` = `OPEN MERGEABLE UNSTABLE`, head
  `f54aae80a`.
- **Ruled out:** "the rollup is empty, so this repo has no CI for the change" —
  it was 2 of 4 sixty seconds after `gh pr create` and 4 of 4 shortly after, i.e.
  the early reading was an unregistered rollup, not an absence. `via: measurement`
- **Ruled out:** "pending on this PR is the rank-17/18 platform signature" — no
  check has REPORTED yet, so there is nothing to attribute; the signature is a
  RED, not a pending. `via: code`
- **Leading hypothesis:** ordinary queueing. The minimum-count rule is satisfied
  structurally (4 registered, the floor for this repo is ≥1 and in practice 4),
  and none is terminal, so "settled" is a claim nobody can make yet.
- **Next probe:** `gh pr checks 749 --repo ZacxDev/homelab-infra | awk -F'\t' '{print $1" | "$2}'`
  — 🔴 read it with `-F'\t'`; check NAMES contain spaces, so `$2` from a default
  `awk` is a fragment of the name and every terminal/busy test is then applied to
  the wrong field. Require every check to hold a TERMINAL conclusion; treat an
  EMPTY conclusion as busy, not as settled. On a red, read WHICH test failed and
  which NODE the PipelineRun landed on before debugging the diff (ranks 17, 18).

### ⚠ Task 524's closing condition is blocked on an interactive prompt, not on a defect

- **Symptom + exact repro:** launch a session from the clawgate UI; the window never
  acquires a `claude_session_id`, so 524's mechanical closing condition cannot pass.
- **Observed (with values):** window `@58` on `workbench`/`scratch` is in the read
  model with `claude=true` and `claude_session_id` empty across **6 snapshots / 6
  minutes**; `window_activity` frozen at the launch instant `1788840435`; **0** new
  `~/.claude/projects/**.jsonl` files in 12 minutes; the pane renders Claude Code's
  trust-folder prompt (`1. Yes, I trust this folder`).
- **Ruled out — the PATH defect 524 fixed.** The same pane's PATH carries 15 entries
  with `claude` resolvable at `/home/zach/.nix-profile/bin`; before the fix it was
  the unit's 3 entries. The launcher works. `via: measurement`
- **Leading hypothesis:** Claude Code does not create a session until the trust
  prompt is answered, so a FIRST launch into an untrusted directory can never
  satisfy the condition as written.
- **Next probe:** answer the prompt in `@58` (operator decision — it is a security
  gate), or re-launch into an already-trusted directory, then
  `curl -s -H "Authorization: Bearer $TOK" $B/api/tmux/snapshot` and read
  `claude_session_id` for that `window_id`. `tmux kill-window -t @58` once read.

### 🔴 `agent-pods` Kustomization is Flux-SUSPENDED and has been since 2026-06-07 — task 375 cannot close

- **Symptom + exact repro:** `#769` merged, Flux has the revision, the HelmRelease reports
  `Ready=True` — and the pod still runs the OLD image. `KUBECONFIG=$KC_HOMELAB kubectl get
  kustomization agent-pods -n flux-system -o json | python3 -c "import json,sys;
  d=json.load(sys.stdin); print(d['spec'].get('suspend'), d['status']['lastAppliedRevision'])"`
- **Observed (with values):** `suspend: true`; `lastAppliedRevision`
  `trunk@sha1:c8faceea74f034b28fb0291900361a52df8b84ed`, a revision dated **2026-06-06 20:27
  -0500**. Field owner is `manager=flux, op=Update, time=2026-06-07T01:58:20Z` — read with
  `--show-managed-fields`, which is required; `kubectl get -o json` strips them by default and a
  check that omits it reads an empty list and proves nothing. The suspension lands **31 minutes
  after** the revision it pinned.
- **Ruled out — prune danger.** 36 agent dirs were deleted from git during the suspension
  (56,474 deletions over 120 files, the Clankup decommission) and **ZERO of their namespaces are
  live**; only 2 `devpod-*` namespaces exist at all (`devpod-initiatives`, `devpod-task-drafter`).
  So `prune: true` would delete nothing live. Instrument validated first: both known-live agents
  match the `devpod-<dir>` pattern, and a looser substring test also returns 0. `via: measurement`
- **Ruled out — "everything under that path is inert AND visibly broken".** It reports
  `Ready=True ReconciliationSucceeded` forever, because a suspended Kustomization keeps reporting
  its last successful apply. The only tell is that its `lastAppliedRevision` differs from every
  sibling's. `via: measurement`
- **Ruled out — the HelmRelease is unmanaged.** `initiatives-agent` (ns `flux-system`) carries NO
  kustomize ownership labels and pulls its chart from a SEPARATE `kubeclaw` GitRepository, so
  helm-controller keeps upgrading it (**v1725** revisions) against a moving chart while its spec
  stays frozen at June. The object looks maximally alive while being deaf to git. `via: measurement`
- **Leading hypothesis (INFERENCE, not measurement — labelled):** the suspension ends a two-hour
  clawgate sprint (0.3.19→0.3.25) whose commits include *"apply granted-profile env + kubeconfig
  to agents"*; commits stop dead at that point. Reads as agent provisioning moving from Flux to
  clawgate. **Nothing in git, no task and no doc records it** — `suspend` is absent from
  `clusters/homelab/flux-system/root-kustomizations/system/agent-pods.yaml`, which has exactly one
  commit ever. Because the `flux` CLI owns the field under SSA, kustomize-controller can never
  remove it. `via: assumed`
- **Next probe:** decide whether to resume. `KUBECONFIG=$KC_HOMELAB kubectl patch kustomization
  agent-pods -n flux-system --type=merge -p '{"spec":{"suspend":false}}'` then watch
  `lastAppliedRevision` advance and the pod roll. Afterwards the one command that settles 375 —
  note `sh -c` is load-bearing and the cluster is **homelab**, not workbench:
  ```bash
  POD=$(kubectl --kubeconfig "$KC_HOMELAB" -n devpod-initiatives get pods -o jsonpath='{.items[0].metadata.name}')
  kubectl --kubeconfig "$KC_HOMELAB" -n devpod-initiatives exec "$POD" -c agent \
    -- sh -c 'command -v clawgatectl && clawgatectl --version; command -v python3'
  ```
  Expect `/usr/local/bin/clawgatectl` + `0.8.31`, with `python3` as the positive control.
  Rollback is `image.tag: "2026.6.11-py"`.

### 🔴 `test_guard_core.py::test_every_kill_server_call_site_in_the_repo_is_classified` is RED on devrc `main`

- **Symptom + exact repro:** any devrc PR's `devrc-pytests` can carry this failure. Reproduce on a
  clean checkout: `git worktree add --detach /tmp/x origin/main && (cd /tmp/x && python3 -m pytest
  scripts/claude-hooks/tests/test_guard_core.py -k test_every_kill_server_call_site_in_the_repo_is_classified)`
- **Observed (with values):** `added: ['claudedocs/handoff-tmux-scratchpad-bar-statusline.md']` —
  a file that mentions a wide tmux kill and is not entered in `_KILL_MENTION_LEDGER`
  (`scripts/claude-hooks/tests/test_guard_core.py:2525`).
- **Ruled out — caused by #1515.** Control run on clean `origin/main` at `687dd7a7` fails the
  identical test with no PR involved; #1515 touches only `claude/skills/clawgate/**`.
  `via: measurement`
- **Next probe:** add the file to `_KILL_MENTION_LEDGER` with a classification (it is prose — a
  handoff write-up), in its own PR.

### Task 519 criterion 1 on the laptop — is the stream down, or is the host just idle?

- **Symptom + exact repro:** the laptop's transcripts in clawgate look badly stale.
  `clawgatectl tmux windows` (gives `host` + `claude_session_id`) joined against
  `clawgatectl transcript ls` (gives `updatedAt`), 2026-09-12T04:54Z.
- **Observed (with values):**
  - `workbench` — 52 of 53 claude sessions carry a transcript; freshest **0s** behind the
    newest row in the store; 12 under 60s.
  - `laptop` — 7 of 7 carry one; freshest **4431s** behind, median **6064s**, **0** under 300s.
  - Stream cursors, `GET /api/transcripts/stream/cursors`, 288 rows joined to host:
    `workbench` 52 `accepted`; **`laptop` 4 `accepted` with real byte offsets**, 3
    `unknown-offset`.
  - Host-vs-server gap (`ledger.last_activity_ts` minus clawgate `updatedAt`), per session:
    workbench n=52 max **+107s**, laptop n=7 max **-14s**; sessions on either host whose own
    host knew of activity >5min ahead of clawgate: **0**.
  - Newest laptop session activity of any kind: **03:37Z**, ~78 minutes before the read.
- **Ruled out:** *the laptop stream is down / disconnected.* Every host-vs-server gap on the
  laptop is negative — clawgate's row is at-or-newer than the laptop's own stamp on all 7
  sessions, so nothing the laptop knew about failed to arrive; and 4 laptop sessions carry
  `reason: accepted` cursors, which is the stream path's own verdict rather than the bulk
  push's. via: measurement
- **Ruled out:** *a 5-minute-bulk-push explanation for the freshest laptop sample.* It is NOT
  ruled out — the -183s sample on session `11cb641d` is equally consistent with the bulk push
  and with the stream, so it settles nothing and must not be quoted as a latency figure.
  via: measurement
- **Ruled out:** *reading staleness alone as evidence about the stream.* A stale transcript has
  two causes — stream down, or host idle — and staleness separates neither. This is the empty-
  result error and it produced a confident wrong reading in this very session before the
  discriminator was run. via: measurement
- **Leading hypothesis:** the laptop stream is healthy and the host has simply been idle for
  ~78 minutes. Criterion 1 is unmeasurable there until someone types.
- **Next probe:** take one active Claude Code turn on the laptop, then immediately re-run the
  join and read the gap for that session's id:
  `clawgatectl tmux windows` + `clawgatectl transcript ls`, match on `claude_session_id`,
  compute `ledger.last_activity_ts - updatedAt`. Expect 0-5s, as the workbench sessions show.

### Card 519's laptop number — blocked on an idle machine, not on a defect

- **Symptom + exact repro:** criterion 1 names both hosts; only the workbench has a number.
- **Observed (with values):** workbench induced-append propagation **2.0s** (`4017d45aa288 →
  e623cd93c18c`) and **3.1s** (`34027a2cffad → c244ba39da53`). Laptop: 7 sessions, freshest
  transcript 4431s behind, median 6064s, 0 under 300s; newest laptop activity of any kind
  `03:37Z`. Stream cursors: laptop 4 × `reason: accepted` with real byte offsets, 3
  `unknown-offset`. Host-vs-server gap: workbench n=52 max **+107s**, laptop n=7 max **−14s**;
  sessions on either host whose host knew of activity >5 min ahead of clawgate: **0**.
- **Ruled out:** *the laptop stream is down.* Every laptop gap is negative (clawgate at-or-newer
  than the host's own stamp) and four sessions carry `accepted` stream cursors. via: measurement
- **Ruled out:** *staleness alone can answer this.* It cannot — stale has two causes, idle and
  broken, and it took a control host plus the cursor `reason` field to separate them. This
  produced a confident WRONG reading in-session before the discriminator was run. via: measurement
- **Ruled out:** *the −183s sample on laptop session `11cb641d` is a latency figure.* It is
  equally explained by the 300s bulk push and settles nothing. via: measurement
- **Leading hypothesis:** the laptop behaves like the workbench and will show a low-single-digit
  number the moment anything is typed there.
- **Next probe:** run `.opencode-dispatch/tmux-ui-verify/scratch/measure519.py` from a Claude Code
  session ON THE LAPTOP. It needs no argument — it makes its own append.

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

- 🔴 **A `git show <ref>:<path>` WITHOUT `-C` READS THE CWD'S REPO, AND THE WRONG-REPO ANSWER IS A
  CONFIDENT ZERO.** Content-verifying the #632 squash, four `git show origin/trunk:<file> | grep -c`
  calls ran from `~/workspace/devrc`, which has no `origin/trunk`: each printed `fatal: invalid
  object name` to stderr and **`0`** to stdout, and the zeros lined up under a heading that said
  CONTENT VERIFICATION. The negative control was 0 too, so it agreed. **Re-run with `-C <repo>` and
  pair every such count with a probe that MUST be non-zero** — the pattern that catches this is a
  positive control, never a tidier-looking zero.
- 🔴 **A CONTENT CHECK IS A CLAIM ABOUT YOUR PATTERN FIRST.** Verifying the #1224 squash,
  `grep -c 'caught \*\*1/20\*\*'` returned 0 — the doc says `**1/20 caught**`, the index bullet says
  `caught **1/20**`, and the pattern was copied from the wrong one. Cross-checked with a
  regex-free `str.count` in python (fails differently) plus a negative control: 4 real occurrences.
  **Never report an absence from a single pattern in a single tool.**
- 🔴 **MERGING MAKES YOUR OWN FRESHLY-WRITTEN HANDOFF WRONG, AND THE WRONG PART IS AN INSTRUCTION.**
  `#1224` landed saying rank 14 was `IN FLIGHT … read the PR, then merge it`; `#632` merged one
  minute later, so the canonical doc carried a live instruction to do something already done. A
  stale FACT is survivable — `resume-state.sh` prints `PR … MERGED` as a DRIFT line — but a stale
  IMPERATIVE is read as a work item. **If you merge after writing the handoff, correct the ranked
  item in the same session.**

- 🔴 **MY OWN VERIFICATION INSTRUMENT WAS WRONG THREE TIMES IN ONE SESSION, AND EVERY TIME IT
  RETURNED A CONFIDENT ZERO.** (a) `git show <ref>:<path> | grep -c` run **without `-C`** read the
  cwd's repo, which had no `origin/trunk` — four counts printed `0` under a heading reading CONTENT
  VERIFICATION, and the negative control agreed because it was 0 too. (b) A `grep -c` pattern copied
  from the wrong document (`caught **1/20**` vs the doc's `**1/20 caught**`) reported an absence
  that was 4 occurrences. (c) **Twice**, a word-scan for `KNOWN FALSE POSITIVE` over Go source
  returned 6-of-11 where the truth was 9-of-11, because the phrase is split across string
  concatenation (`"KNOWN FALSE "+ "POSITIVE"`). Two independent scans agreed, which read as
  corroboration and was **the same blind spot sampled twice**. 🔴 **Pair every count with a probe
  that MUST be non-zero, and read one hit by eye before quoting the number.** For Go message text,
  join `"…"+ "…"` before searching or the scan is a guard on a word.
- 🔴 **A COMMENT CAN BE WRONG ABOUT THE LANGUAGE, AND THEN IT SILENCES A REAL DEFECT.** A guard
  excluded `:=` on the stated grounds that *"at function scope it does not compile"*. Go's
  redeclaration rule contradicts that: a short variable declaration MAY redeclare a variable from
  the same block — **the parameter list included** — when at least one variable on the left is new.
  Proven standalone (`p := &interval; interval, tuned := interval*10, true` → `param via pointer:
  5h0m0s | same variable: true`) and on the real loop (`go build` rc 0, `go vet` rc 0, guard `ok` —
  SURVIVED). A comment asserting something the spec contradicts is worse than no comment: it tells
  the next reader not to look.
- 🔴 **FOUR CONSECUTIVE AUDIT ROUNDS HIT ONE SHAPE — the fix for a false positive SILENCED the arm
  that caught the real thing.** Rounds 3–6 of #637, every time. The method that misses it is
  re-running the arm's ORIGINAL mutant: that cannot see a shape the narrowing NEWLY stranded. **For
  every narrowing or widening, construct a NEW mutant in the direction the change moved**, and
  report those rows separately from the original-mutant rows. Adopting that rule is what surfaced
  the next two findings.
- 🔴 **`clawgate health check did not pass on port <N> within 15000ms` IS AN INFRASTRUCTURE FLAKE,
  NOT YOUR DIFF — and it reds TWO of the four clawgate checks.** Four controls settled it on #637,
  and any one alone would have been weak: (1) the failing TEST moves between runs — ux-audit failed
  `settle-budget` on one commit and the approval funnel on another, where `settle-budget` passed in
  1.6s; (2) the verdict ALTERNATES across commits that changed only string literals in one Go test
  file; (3) `clawgate-e2e` also failed on `b2fecf49`, a commit already merged to trunk and not from
  that PR; (4) passing runs clear the budget by **854ms against 15000ms** — a race, not a margin.
  **Recovery, and it re-reports the GitHub status for the same revision without a commit:** capture
  the PipelineRun spec, strip `metadata.name`/`uid`/`resourceVersion`/`creationTimestamp`/`status`,
  set `generateName`, `kubectl create`. **Assert the `revision` param in the script** rather than
  trusting the capture. Both re-runs went green; all four checks passed before the merge.
- ⚠ **A `_test.go` file can BE the payload.** For #637 the guard IS what the PR ships, so the
  attribution gate must not score it zero for its extension — doing so would have read every round
  as zero-payload and stopped a ladder that was working. The gate's own rule says to name each file
  payload or scaffolding and that ambiguous is not zero.
- 🔴 **A CLEAN AUDIT ROUND ENDS THE LADDER — and the auditor may tell you otherwise.** #637's round 8
  returned zero findings and then wrote *"one more clean round would close it"*, conflating the
  findings-keyed stop rule with the two-consecutive-ZERO-PAYLOAD attribution gate. They are
  different mechanisms: the first clean round is the last one, and re-confirming it is explicitly
  forbidden.
- ⚠ **Prose in a failure MESSAGE and prose in a source COMMENT have different audiences.** Two known
  false positives were documented 200 lines from the failure; the person who hits them is reading a
  red CI leg, not the test file. Moving the routing into the messages is what converts "the guard
  reds on my correct code" from *reach for the delete key* into *here is the repair*. Nine of eleven
  arms now carry it; the other two are bare because nothing has been measured for them, and
  inventing a shape would be the same defect one level up.

- 🔴 **"IDENTICAL TO `origin/main`" IS NOT "CURRENT" WHEN THE UPDATE IS IN FLIGHT.** `/resume`'s
  reconciler correctly reported this doc identical to `origin/main` and it was STALE anyway: the
  rank-10 update sat in an unmerged `devrc#1230` for eight hours with both required checks already
  green. The reconciler cannot see an open PR against the doc; only `gh pr list --state open` can.
  Rank 12 had been closed-as-refuted a full day before #1230 was written and #1230 still called it
  OPEN, so the stale line was about to land a third time. **Sweep for open PRs against the handoff
  before trusting a freshness verdict about it.**
- 🔴 **`audit-dispatch.py <n>` RESOLVES AGAINST THE CWD'S REPO AND WILL SILENTLY AUDIT THE WRONG
  PR.** Run from `devrc`, `audit-dispatch.py 660` assembled a full brief for
  `innovation-upstream/devrc#660` — an unrelated PR about a skill-body byte count — whose WHERE TO
  WORK section asserted the PR lived in devrc and prescribed `isolation: "worktree"`. Dispatched, it
  would have audited the wrong change confidently. **Always pass `--repo owner/name` for a
  cross-repo PR**; with it, the brief correctly flags CROSS-REPO, forbids the isolation flag, and
  prescribes NOTHING for the toolchain rather than inventing devrc commands.
- 🔴 **A CONTROL TABLE THAT COMPARES FAILURE MESSAGES MUST FIRST CHECK EACH RUN REACHED THE SAME
  STAGE.** I diagnosed a ux-audit red as "a new failure mode unique to this revision" from three
  runs' messages. Two of those runs had died at the health check and never reached the walk at all —
  their silence about that failure mode was not evidence of its absence. The defect was pre-existing
  on trunk. **"Not seen elsewhere" and "not reachable elsewhere" are the same observation** — this
  is the empty-result rule applied to one's own control.
- 🔴 **A FIXTURE CAN BE STRUCTURALLY TOO SMALL TO EXECUTE THE BEHAVIOUR ITS OWN ASSERTION NAMES.**
  `sort.SliceStable → sort.Slice` SURVIVED a stability guard because Go's `sort.Slice` uses
  insertion sort below n=12 — which IS stable — and the fixture had 4 groups / 3 members. **And the
  obvious repair does not work either: an ALL-EQUAL-rank fixture stays blind at ANY size** (pdqsort
  has an all-equal fast path; measured byte-identical at 21 and at 40). Interleaved ranks past n=12
  is the discriminator.
- ⚠ **One of the two named mutants there is a genuine EQUIVALENT mutant, not a gap.** Once the group
  comparator became `(Rank, Project)` over project-keyed groups it is a strict total order, so
  `sort.Slice` and `sort.SliceStable` are provably identical for any input and no fixture can
  separate them. The guard that replaces it drops the `Project` tie-break instead, and dies.
- 🔴 **`make e2e` AND `make ux-audit` ARE DIFFERENT TIERS WITH DIFFERENT `testDir`s** —
  `playwright.config.ts` is `testDir: './tests'`, `playwright.ux-audit.config.ts` is
  `testDir: "./ux-audit"`. A commit touching only the walks is STRUCTURALLY INVISIBLE to `make e2e`,
  and a green there says nothing about them. Run both, and say which you ran.
- 🔴 **The clawgate e2e harness rebuilds the Go binary every run but builds `web/static/app.css`
  ONLY WHEN MISSING.** So a mutant that swaps one Tailwind utility for another reads SURVIVED
  because the new rule was never generated. Force a CSS rebuild before trusting any CSS mutation
  result. (`tailwindcss`, never `tailwindcss_4` — ~42.7 KB with `.h-14` present is correct.)
- ⚠ **The clawgate task-authoring PreToolUse hook fails CLOSED on a body it cannot read**, which is
  correct and cost three attempts: it cannot see through `--body "$(cat <<'EOF' …)"` shell
  substitution or a `$VAR` path. Write the body with the Write tool, pass a LITERAL path to
  `--body-file`. A blocked command does not run its own heredoc, so the file the next attempt
  expects will not exist.
- ⚠ **The editor reported a Go syntax error on three separate occasions that did not exist on the
  pushed branch** — stale mid-edit worktree snapshots. Always check the branch before believing one.
  Worth checking anyway: the JS in `internal/ui/*.go` lives in Go raw string literals, where a
  backtick in a comment terminates the literal — a real near-miss this session, caught by the
  compiler.

- 🔴 **THE STALE-DOC DRIFT THIS FILE KEEPS RE-CREATING FIRED AGAIN, IN A NEW SHAPE.** The previous
  session recorded it as "identical to `origin/main` is not current when the update is in flight".
  This time the working-tree copy was not identical at all — it was **124 lines behind** — because
  the shared base clone sits on an unrelated feature branch that predates the last two handoff
  merges. Both shapes have the same tell and the same fix: **read the copy `resume-state.sh`
  names, and author from a worktree cut fresh off `origin/main`.** The failure mode is silent in
  both directions — reading the stale copy loses ranks 19–23 entirely (they exist only on
  `origin/main`), and *writing* from it makes `handoff_doc.py` merge into a 2078-line base and
  replace the committed 2202-line document.
- **`clawgatectl --version`, never `clawgatectl version`.** The bare subcommand errors with
  `unknown command "version" for "clawgatectl"`, which reads like a broken or half-deployed
  client and is not. Both hosts answered `0.8.23`.
- **A matching version triple is still not rank 8a's check.** Server, workbench client and laptop
  client all reading 0.8.23 says nothing on its own; the round trip that moves a number is the
  check, and it is cheap: `view create` on the laptop, `view ls` on the workbench, `view rm`.
- **The deployed-vs-merged split is now measurable in one command and worth keeping.** Probing the
  live `/ui/tmux` for `<details>` discriminates "grouping deployed" from "grouping merged" without
  reading a version at all — which matters because the version number moved twice for reasons
  unrelated to this feature.

- 🔴 **`rev-parse --show-toplevel` IS THE WRONG CALL FOR "WHICH REPO IS THIS PATH IN", AND IT IS THE
  ONE EVERY READER REACHES FOR.** On a linked worktree it returns the WORKTREE, so a change built on
  it measurably does nothing. `git -C <path> rev-parse --path-format=absolute --git-common-dir`
  resolves to the MAIN clone's `.git`, whose parent is the repo. Measured: `devrc-agentlock` and
  `devrc-ho` both → `devrc`; `/tmp` and `$HOME` → nothing, correctly.
- 🔴 **THE BASE MOVES FASTER THAN A GATE RUNS HERE, SO A MERGED-TREE RESULT CANNOT BE BANKED.**
  `origin/main` moved **five times** during one session (`a7dac5bd` → `1d2c4bd6` → `2882d2c7` →
  `d86b4e45` → `fd68d48c` → `2b1d3552`), and `homelab-infra` `trunk` three times inside a single
  audit. Chasing it is unsatisfiable and produces a permanently-unmergeable PR, which is its own
  anti-pattern. **What worked:** gate the merged tree, then at the merge moment re-read the base and
  reason about the SPECIFIC delta rather than re-running blind. On the one that mattered, main had
  bumped a floor in `run-tests.sh` — the runner the gate itself uses — and the check was that the
  bumped target (`browser-bridge`, 716→867, collecting 912) was one the diff adds no tests to.
- 🔴 **A PROBE OF A PARTIAL ROUTE IS NOT EVIDENCE ABOUT THE WHOLE PAGE.** `/ui/tmux` renders the tab
  partial with no header, so it can answer about the tmux grouping and is structurally blind to the
  header controls. One probe of it was read as covering two PRs; it covered one. **Ask which route
  renders the thing you are asking about before quoting a zero.**
- 🔴 **`e2e/tests/helpers/server.ts` REBUILDS `web/static/app.css` ONLY WHEN MISSING — check its
  SIZE, never its existence.** A stale stylesheet made a 390 px overflow read **0 for the wrong
  reason** (the element was `display:none` at every width), and a wrong-cwd tailwind run emitted
  **5,594 bytes instead of 43,173** and produced a confident 11-test RED including specs the diff
  could not reach; the tell was a **143 ms** build against a normal ~1 s. Also: the repo pins
  tailwind **v3** — `nix-shell -p tailwindcss` gives 43,173, `tailwindcss_4` gives ~20,5xx because
  v4 ignores `tailwind.config.js`.
- 🔴 **A FAILED FULL-MODE e2e FIXTURE LEAKS ITS `clawgate-e2e-pg-*` CONTAINER, AND IT IS
  SELF-REINFORCING** — leaked containers starve `startPostgres`'s 30 s readiness deadline until
  *every* full-mode spec fails in setup, and each timeout leaks another. 18 had accumulated (oldest
  166 min) and clearing them is what produced the first complete 168-passing run. ⚠ `docker ps`
  **omits `created`-state containers** — use `-a`, or a "0 containers" reading is a claim about the
  view, not the box.
- 🔴 **A DECISION TABLE WITHOUT A POSITIVE CONTROL IS NOT EVIDENCE.** The safety question for the
  auto-approve work was "did the fix change a permission decision" — answered by a 48-row table
  (global × project × query, healthy vs wedged store) being sha256-identical across base and head,
  **and** by a deliberate mutant moving 12 of those 48 rows. Without the second half, an identical
  table and a harness wired to nothing look the same.
- ⚠ **Two agents in ONE worktree is a self-inflicted collision, and a cwd filter cannot separate
  them.** A re-gate agent was pointed at a worktree while its previous occupant was still resumed;
  no file was clobbered, but a later cleanup sweep filtering on "cwd is my worktree" killed a
  sibling agent's live Playwright run. Worktree isolation does not help when both agents are IN the
  same worktree — give each its own, and if no filter leaves a set you are confident in, kill
  nothing.

- 🔴 **"DO WE NEED IT" IS A QUESTION THE DOC CANNOT ASK ITSELF, AND THE ANSWER WAS NO THREE TIMES
  OUT OF FOUR.** Before working any ranked item, check whether it is already done, already being
  done, or dismissable — the cost is minutes and the alternative is rebuilding finished work.
  Measured here: 17 was in flight elsewhere, 23(b) was already fixed, 23(c) closed under a criterion
  it had carried since it was filed. **An audit will never surface this** — it scopes to the diff.
- 🔴 **`claim-work` CANNOT SEE AN UNCLAIMED DUPLICATE; ONLY THE `gh pr list` SWEEP CAN.** Nobody had
  claimed rank 17, so the lock was silent while another session built it. The sweep is not a
  fallback for a degraded run — it is the only instrument that covers this class, and it must be
  run unconditionally.
- 🔴 **A FILE-LEVEL `grep -c` CANNOT ANSWER A SAME-ELEMENT QUESTION, AND A STOP-CONDITION BUILT ON
  ONE WILL HALT CORRECT WORK.** The safety rule "these routes must not be widened because their
  width pairs with the sidebar offset" is right; the check derived from it — "stop if the file
  contains `lg:pl-72`" — is wrong, because every one of the five documents contains it. What
  separates them is whether the offset sits on the SAME element as the width (`operator.go:72`,
  unsafe) or on a wrapper above it (`task_detail.go:76` with `Main(` at `:86`, safe — the shell's
  own shape since #667). **State the criterion, then build the check to match the criterion, not
  the prose.**
- ⚠ **A red check is a claim about a (tree, base, node) TRIPLE here.** `clawgate-ci` now schedules
  across the homelab cluster AND the new Hetzner runner, so "which node" joins "which test moved"
  as a discriminator. Read the PipelineRun's `nodeName` before debugging a diff.

- 🔴 **A PIPE EATS AN EXIT STATUS, AND IT ATE ONE IN THIS SESSION WHILE THE RULE WAS ON SCREEN.**
  `clawgate_handoff.sh field <doc> | head -3; echo $?` printed **0** — `head`'s status — and was
  read as "a task field is already recorded". The real answer was **rc 1, no field at all**. Capture
  first (`out=$(cmd 2>&1); rc=$?`), then print. The failure is silent and reads as a clean answer,
  which is why knowing the rule is not sufficient.
- 🔴 **A RANKED QUEUE OUTLIVES ITS ITEMS, AND NEITHER THE DOC NOR THE LOCK CAN SAY SO.** Asked "do
  we need 23 and 17?", the answer for three of four sub-items was no: 17 was **being built by
  another session** (`homelab-infra#685`), 23b was **already fixed**, 23c was **dismissable under
  its own stated criterion**. Only 23a was work, and it took ten minutes. `claim-work` cannot see an
  unclaimed duplicate — nobody claims an item they are not working on — so the unconditional
  `gh pr list --state open` sweep is the only instrument that covers it. **Ask "do we still need
  this?" before drawing any item; an audit never will, because it scopes to a diff.**
- 🔴 **AN INVERTED EXPECTATION IS EXACTLY WHEN NOT TO CLASSIFY A RED.** On `#690`, the leg this doc
  had called broken all session (`ux-audit-clawgate`) **passed**, and the two expected to pass
  failed. Reading them showed `clawgate-e2e`'s single failure was `tasks-mobile.spec.ts:531` dying
  in FIXTURE SETUP with `clawgate health check did not pass on port <N> within 15000ms` on three
  different ports — rank 17's signature, before the test body ran, on a diff that swapped two CSS
  classes. **Read every red; do not sort them by prior.**
- ⚠ **`clawgate-ci` now schedules across the homelab cluster AND the new Hetzner runner**
  (`clawgate-ci-g4gcm` landed on `talos-xr6-r7p` *and* `tekton-ci-1`). "Which node" has joined
  "which test moved" as a discriminator for a `go`-leg red — read the PipelineRun's `nodeName`.

- 🔴 **THE WORKBENCH CAN PULL `docker.io` AGAIN — the standing "build clawgate images on the LAPTOP"
  instruction is STALE.** Measured this session: `docker pull alpine:3.20` from the workbench
  **succeeds**, and harbor is reachable. 0.8.26 was built here. The old constraint was a router DNS
  record with a 487-day TTL; whatever cleared it, the detour to the laptop is no longer needed.
  ⚠ `deploy.md` also prescribes `DOCKER_HOST=ssh://zach@192.168.50.250` — that address IS the
  workbench, so from a workbench session the hop is a no-op and ssh to it fails (`publickey`).
- 🔴 **A MISSING CHECK READS EXACTLY LIKE A PASSING ONE.** `#715` showed **3 green checks instead of
  4** — `clawgate-e2e` never fired, because it is path-filtered on `containers/clawgate/**` and a
  trunk-merge changed nothing there. Counting the checks is what caught it. Resolved by comparing
  **subtree OIDs**: the clawgate tree was byte-identical to the head that had passed e2e with 188
  tests, with a negative control proving the comparison discriminates.
- 🔴 **A REMEMBERED BYTE COUNT CAN FAIL *BECAUSE* THE FEATURE IS PRESENT.** `app.css` on `trunk` is
  **43,401**; a branch that adds classes legitimately differs (`#715` = 43,994, `#712` = 44,284).
  Asserting the pinned number on a branch would fail *when the new classes are there*. Compare
  against a baseline you build yourself, never a number from a doc.
- 🔴 **`git diff A..B` LISTS EVERY DIFFERENCE, NOT WHAT B ADDED.** Used to ask "what did trunk gain",
  it reported 21 clawgate files that were in fact `#715`'s own additions. **Three-dot
  (`A...B`) asks the right question** — the real answer was 0.
- 🔴 **`gh pr view --json headRefOid` SERVES A STALE HEAD right after a push.** It reported the old
  sha and read exactly like a failed push. `git ls-remote` is authoritative; the ahead/behind count
  `git rev-list --left-right --count origin/<branch>...HEAD` is the check that settles it.
- 🔴 **A CHECK NAMED `FAILED: scripts-tests` while the step EXITED 0.** Every step passed except
  `verdict`, which was reading a genuinely-failed leg recorded inside it. Two true observations
  (pattern-matching a remembered "pre-existing" failure; the documented text-vs-step disagreement)
  each pointed AWAY from the cause. It was only found by running the tests **on trunk directly**:
  they pass there, and the PR's base predated **#723**, the fix that retries flaky
  `registry.npmjs.org` / `proxy.golang.org` fetches.
- **gitleaks flagged a SYNTHETIC test fixture** (`generic-api-key`, entropy 3.83) in the very test
  guarding the browser-tier text leak. Fixed by making the fixture low-entropy rather than buying a
  baseline entry — the gate's own output says an unmatched finding *"needs a real look, not a
  re-point"*. Verified the change did not make the test vacuous (it still fails on its own assertion
  with the redaction neutralised) and that gitleaks still flags the **old** value as a positive
  control.
- 🔴 **THE AUDIT LADDER'S OWN LESSON, in one line: guards that pinned a RELATIONSHIP survived;
  guards that pinned a WORD failed.** Four rounds on `#711` found a CSRF hole, then that its fix was
  bypassable by **DNS rebinding**, then that the replacement covered two of four fields. The route
  ledger produced **seven distinct escapes**, each found while fixing the previous one — a
  concatenated pattern, a `Handle` registration, `http.HandlerFunc` misread as the wrapper, a
  file-scoped alias map, a host-qualified pattern, a percent-escaped path, and wildcard/subtree
  patterns. It is now closed by an argument about the pattern's **literal head**, not a list.
- 🔴 **THREE ARTIFACTS DESCRIBED WORK THAT WAS NEVER PUBLISHED**, all one root cause — verifying the
  local file instead of the published thing: a `git add` that silently aborted while `git commit`
  succeeded; a local rebase whose validation commit was never pushed (so a PR body described code
  absent from the PR); and a PR-body edit that never reached the live body. Each was caught by
  someone reading the **published** artifact. `git rev-list --left-right --count origin/<b>...HEAD`
  is the check that sees the second; `gh pr view --json body` sees the third.
- **The isolation seam paid for itself repeatedly.** `#715`'s reply control was rendering as
  **usable against a server answering 503** on the merged tree, because `#711` round 3 added a third
  switch after `#715` was written — caught only because its guard drives BOTH the rendered verdict
  and a real request through the real mux. `#712` had the same defect independently, and its own
  seam test was covering one direction only (the *usable* state was never asserted).
- **Deliberate: `CLAWGATE_TERMINAL_UI_HOSTS` carries three names** — `clawgate.zacx.dev` (behind the
  Authelia passkey edge), `192.168.50.250` (LAN NodePort, **no human auth**) and `10.42.0.30`
  (nebula). Dropping the LAN address would harden the surface at the cost of the workbench browser.
</content>
</invoke>

- 🔴 **2026-09-06 — A POSITIVE CONTROL THAT MUTATES IN ONE DIRECTION GOES VACUOUS
  THE MOMENT THE THING IT GUARDS FLIPS, AND IT GOES VACUOUS *GREEN*.**
  `test_the_wanted_by_guard_can_SEE_an_inverted_flag` proved the guard could see a
  flag flip by doing `src.replace("enableTmuxReplyAgent = false;", "… = true;")`
  and asserting the result reads `true`. Arming the agent makes that `replace`
  match **nothing**: `flipped` then equals `src`, and the control asserts
  `"true" == "true"` about the **unmutated** file — it passes while observing
  nothing, in a suite where every other test also passes. The `assert flipped !=
  src` line two lines above it is the only thing that catches this, and only
  because a previous session put it there for the *other* mutant. **General
  shape: any control built as `replace(<current value>, <other value>)` is
  silently disarmed by a change to `<current value>`** — the direction of the
  mutation is a dependency on the state under test, and nothing re-derives it.
  Fixed by inverting it to `true → false`, which is now the hazard direction, and
  by asserting `!= src` on both arms.
- 🔴 **2026-09-06 — FLIP A CONFIG GUARD, DO NOT DELETE IT; THE SYMMETRY IS THE
  PRODUCT.** `test_the_agent_unit_SHIPS_DISABLED` pinned `false`. The tempting
  read on arming is "the guard has served its purpose, drop it". What it actually
  enforces is that the armed state is a **deliberate, reviewed edit in both
  directions**: while it read `false`, arming meant editing the test; now it reads
  `true`, disarming means editing the test. A drive-by revert to `false` would
  stop every queued reply executing **while the server kept accepting writes and
  the UI kept looking healthy** — silent, and exactly what a config guard is for.
- 🔴 **2026-09-06 — WHEN A ONE-LINE FLAG FLIP LANDS, `git grep` THE FLAG NAME AND
  THE PROSE THAT DESCRIBES ITS STATE, BOTH.** Grepping `enableTmuxReplyAgent`
  finds the guards; it does **not** find `SHIPPED DISABLED`, which is the phrase
  three of the five falsified comments are written in. Two greps, not one — the
  second is for the words a human used to describe the state, not the identifier.
- ⚠ **2026-09-06 — `scripts/gate.sh` run from a plain shell exits `3` on the
  pytest leg, and 3 is a PRECONDITION failure, not a test failure.** The log says
  so and prints the fix (`nix develop <repo> --command bash <repo>/scripts/run-tests.sh <repo>`).
  Do not read that 3 as a red suite; the node leg in the same run passed 1449 tests.
- ⚠ **2026-09-06 — the documented `| tail` trap fired again, unchanged.**
  `bash scripts/gate.sh … 2>&1 | tail -35; echo "GATE_RC=$?"` printed
  **`GATE_RC=0`** directly beneath the runner's own **`GATE: RESULT=FAIL exit=1`**.
  The status belongs to `tail`. Read the `RESULT:` lines; never the piped code.
- **`nix eval` of a worktree needs `path:<worktree>`, not the repo root.** The
  first eval silently answered for `~/workspace/devrc` (flag still `false`) and
  returned `{"WantedBy":[]}` — which looked like the change not working. It became
  the negative control instead, but only because the second eval named the
  worktree explicitly.

- 🔴 **2026-09-06 — A MUTATION BATTERY WHOSE SCRATCH ROOT DOES NOT EXIST SCORES
  EVERY MUTANT "KILLED", AND THAT LOOKS EXACTLY LIKE A WORKING FIX.** Measured:
  a battery re-run to confirm a widened AST guard never created its `$B` root, so
  every `cp -a` failed, pytest ran against nonexistent paths, and all seven
  injections scored KILLED — **four known survivors becoming zero survivors**,
  the precise shape of success. The ONLY thing that distinguished it was that the
  **negative control was also KILLED** when a healthy control must pass. A battery
  reporting a clean sweep with no passing control has measured nothing. Assert the
  copy exists, assert the file under test exists, and require a real verdict line
  before scoring any mutant.
- 🔴 **2026-09-06 — A FIX THAT DOCUMENTS A HAZARD CAN CREATE IT.** Round 1 wrote a
  disarm example into `nix/home.nix`'s comments —
  `#     enableTmuxReplyAgent = false;` — while retracting a claim that a
  substring-matching control could go vacuously green. That comment's `  #     `
  prefix ENDS IN SPACES, so the two-space-prefixed literal matches inside it, and
  the control then passes about a file whose flag was never mutated. The
  retraction and the thing it denied shipped in one commit. **When a fix adds
  prose QUOTING the code it guards, re-run the guard against the new prose.**
- 🔴 **2026-09-06 — A GUARD'S DESCRIPTION IS A COVERAGE CLAIM; CHECK THE BODY IS
  AS WIDE AS THE SENTENCE.** An AST pin whose docstring said "every spawn must
  take its argv from `tmux_bin()`" matched only literal `subprocess.<verb>(…)`
  attribute calls. Measured survivors: `from subprocess import run as _r`,
  `from subprocess import run`, `import subprocess as _sp`, `os.posix_spawn`.
  With one live the suite was 183 passed. Resolve import bindings, or narrow the
  sentence.
- 🔴 **2026-09-06 — DELETING A WORD TO GREEN A TEXT SCAN CAN DELETE A GUARANTEE.**
  Adding `systemctl --user stop tmux-reply-agent` to a docstring made
  `test_no_real_launchers.py` see a new file reaching an acknowledged binary. The
  tempting fix is to reword. That ledger's own `session-write` entry forbids it —
  and here the word was the ONLY written rollback for a surface that executes
  commands. Acknowledge with evidence, or write the pin; do not reword.
  ⚠ **And an acknowledgement can BLIND the guard it is filed under**: `hazard_hits`
  returns a FILE set, so once a file is in it, a real call site added to that file
  changes nothing. Measured: injecting a genuine
  `subprocess.run(["systemctl", …])` left the suite at 77 passed.
- ⚠ **2026-09-06 — a count kept in prose beside what it counts DRIFTS, and fixing
  it at one site makes two sites disagree.** A prose-mention tally was corrected
  in one dict entry and left stale in another entry of the SAME dict. The fix is
  to remove the running total, not to renumber a third time.

- 🔴 **2026-09-06 — A GUARD CAN BE WALKED BY ITS OWN NEIGHBOUR'S ERROR MESSAGE.**
  `test_an_UNREADABLE_pane_current_path_still_REFUSES` asserted `"directory" in
  err.lower()`. That word appears in the refusal AND in the `same_directory`
  MISMATCH message four lines below it, so **deleting the refusal branch outright
  left the test green** — the fallthrough supplied a message containing the word.
  ⚠ Worse, the mutant's verdict depended on an UNPINNED dimension: it died when
  pytest ran from `/home/zach` and survived from `/tmp` or the repo root, and the
  gate runs from the repo root. Pin the WHOLE normalised string, and assert the
  neighbour's distinctive phrase is ABSENT.
- 🔴 **2026-09-06 — THE ATTRIBUTION GATE IS WHAT ENDS AN AUDIT LADDER THAT KEEPS
  FINDING REAL THINGS.** Four rounds, every headline a defect in the PREVIOUS
  round's corrective prose. Rounds 2–4 each changed **0 payload lines**; the
  findings were all in guards the ladder itself had just written. Round 5 would
  have audited round 4's fix to round 3's fix. Measure
  `git log --numstat --format= --remerge-diff <audited>..HEAD --not <base>` and
  stop on two consecutive zero-payload rounds — do not stop on "safe to merge",
  and do not keep going on "it keeps finding things".
- 🔴 **2026-09-06 — A MUTATION BATTERY WHOSE SCRATCH ROOT DOES NOT EXIST SCORES
  EVERY MUTANT "KILLED".** Measured: `$B` was never created, every `cp -a` failed,
  pytest ran against nonexistent paths, and four known survivors "became" zero —
  the exact shape of a working fix. The **negative control was also KILLED**, and
  that was the only tell. Assert the copy exists, assert the file under test
  exists, and require a real verdict line before scoring any mutant.
- 🔴 **2026-09-06 — `scripts/tests/test_tmux_reply_agent.py` IS NOW ADVERSARIAL TO
  NAIVE OUTPUT PARSING.** Its prose contains the literal string `1 passed` (in a
  comment documenting a finding), and pytest ECHOES test source on failure — so a
  battery that substring-matches `"1 passed"` scores a FAILING run as PASSED. An
  auditor hit this mid-run and had to discard a pass. Anchor on
  `^1 (passed|failed)` and use `--tb=no`.
- ⚠ **2026-09-06 — a test's own load control can exonerate a machine it did not
  measure.** browser-bridge's hang-net reports process-spawn latency and concludes
  "the MACHINE is not the explanation". Spawn latency is not lock contention and
  not I/O; on a box running four concurrent nix check derivations that conclusion
  is not supported by what it measured.

- 🔴 **`clawgatectl health` IS NOT A DEPLOY CHECK FOR A MERGE.** It read `0.8.27`
  both before and after all three UI PRs merged — the version had moved for an
  unrelated reason while they sat unmerged. A version that CHANGED is not evidence
  that YOUR change shipped. Verify by CONTENT against the running pod.
- 🔴 **`clawgate` has NO Flux image automation** — the pin is an immutable literal
  tag, so merging to `trunk` reconciles cleanly and changes NOTHING that is
  running. Shipping is four deliberate steps (build, push, bump BOTH pins, commit).
- ⚠ **The "workbench cannot pull from `docker.io` — build on the LAPTOP" note is
  STALE.** Measured 2026-09-07: `docker build` on the workbench succeeded on the
  first attempt, rc 0. Following that note would have moved the build to the
  laptop for nothing. The note dates from 2026-08-29; re-measure before routing
  around it.
- **Both version pins must move together.** `deployment.yaml` and
  `cmd/clawgatectl/client.go`'s `buildVersion`; `TestDeployPinMatchesClientBuildVersion`
  was watched to genuinely `=== RUN` and `--- PASS`, not merely report `ok`.
- 🔴 **CARRIED FORWARD FROM THE REPLACED STATUS BLOCK — the write gate flagged
  these as durable lines a `Status` replace would delete, and it was right.**
  - 🔴 **RANK 8a IS RECURRING, NOT CLOSED.** Nothing converges `homelab-talos`, so
    both hosts' `clawgatectl` can drift from the server. Re-run the cross-host round
    trip (`view create` on the laptop → `view ls` on the workbench → `view rm`,
    watching a number MOVE); a matching version label is NOT the check. ⚠ The client
    version is `clawgatectl --version` — the bare `version` subcommand is
    `unknown command`, which reads like a broken client.
  - **Rank 32's flag was measured as a PAIR, in both directions:** `false` →
    `Install.WantedBy = []`, `true` → `["default.target"]`, both from `nix eval` on
    the real flake. One reading asserted twice is not the same claim.
  - **Rank 32's audit ladder closed on the ATTRIBUTION GATE, not on a clean round** —
    4 rounds, the last 3 changing 0 payload lines. Worth knowing before anyone reads
    "4 rounds" as "4 rounds of findings".

- **The CSS cwd trap is real and has a cheap tell:** `app.css` built from inside
  `containers/clawgate/` came to **44,975 bytes**; ~5 KB means the trap fired.

- 🔴 **2026-09-07 — `handoff-tmux-webapp.md` IN THE devrc CLONE WAS 2 COMMITS
  BEHIND `origin/main`, AND READING IT WOULD HAVE PRODUCED A WRONG SESSION.** The
  working copy still said `0.8.28`, still listed rank 39 (auth) as OPEN, and still
  described the LAN surface as authenticating nobody — all three superseded by
  `#1357`/`#1358`. The kickoff message was the only thing that disagreed with the
  file, which is the tell. **Read the doc from the ref** (`git show
  origin/main:claudedocs/handoff-tmux-webapp.md`) or fast-forward first; the base
  clone is write-only for worktree-based work and falls behind silently. Left
  unfixed it is also a `status=stale-base` refusal from `handoff_doc.py` at the
  END of the session, after all the work.
- 🔴 **2026-09-07 — A GUARD CAN BE WALKED BY A UNIT, AND BY AN IMPORT ALIAS.** Two
  independent predicates in `containers/clawgate/`, both written explicitly to be
  unwalkable, both walkable by a NAME: `aaOffScreen` tested
  `strings.HasSuffix(v, "px")` so `left:-437rem` — the same 6,992px, the same
  nowhere — was skipped in silence; `aaEvictsIn` tested `pkg.Name == "maps"` so
  `import m "maps"` evaded it **in a file whose import block SAYS the answer.**
  The general shape: when a predicate reads a DERIVED surface (the call site, the
  declaration's suffix) while a DEFINING surface exists (the import block, the
  CSS unit table), the defining one is the one to read. Ask what the value MEANS,
  not how it is spelled.
- 🔴 **2026-09-07 — FIXING A LEAK CAN DESTROY THE ONLY COPY OF THE EVIDENCE.**
  Redaction and logging are one obligation, not a fix plus a nicety: the three
  auto-approve toggles that rendered the pgx DSN into the operator's banner logged
  it **nowhere**, so redacting alone would have deleted the driver error from the
  system entirely. **Before redacting anything, grep for where else it is
  recorded** — and if the answer is "nowhere", the log line is part of the fix.
- 🔴 **2026-09-07 — `internal/ui/auto_approve_header_test.go` WAS gofmt-CLEAN AT
  BASE WHILE 17 OTHER FILES IN THE MODULE ARE NOT.** So a repo-wide `gofmt -l`
  says nothing about whether YOUR change left a file dirty — the baseline is
  per-file. Check the file's own base state (`git show
  origin/trunk:<path> | gofmt -l /dev/stdin`, or a scratch copy) before deciding a
  listing is pre-existing.
- ⚠ **2026-09-07 — the module's Go tests need `web/static/app.css`, which is a
  gitignored BUILD ARTIFACT**, so a fresh worktree fails 3 tests in
  `internal/ui` + `internal/api` for a reason that has nothing to do with the
  diff. `nix-shell -p tailwindcss --run 'tailwindcss -i web/css/input.css -o
  web/static/app.css --minify'` **from inside `containers/clawgate/`**. Measured
  this session: **45,275 bytes** — consistent with the recorded ~44,975 tell, so
  the cwd trap did not fire; ~5 KB means it did.
- ⚠ **2026-09-07 — LSP diagnostics in a homelab-talos worktree are PHANTOM and
  loud.** Every `internal/...` import reported `cannot find package … in GOROOT`,
  plus a false `"errors" imported and not used` on a file that calls `errors.Is`.
  They resolve against the primary clone's module view. `go build ./...` /
  `go vet` are the arbiter; do not "fix" code to satisfy them.

- 🔴 **2026-09-08 — A VERSION-PIN BUMP INSIDE A FEATURE PR CREATES AN
  ImagePullBackOff THE INSTANT IT MERGES.** `#751` bumped `deployment.yaml` and
  `cmd/clawgatectl/client.go` to `0.8.30` as part of its own change — correct for
  `TestDeployPinMatchesClientBuildVersion` — but nothing builds an image on merge, so
  Flux reconciled a pin with no image behind it and the cluster sat in
  ImagePullBackOff for ~80 minutes. The old pod kept serving, so it was a stuck
  rollout, not an outage. **The pin bump and the image build are ONE operation and
  must not be split across a merge boundary.**
- 🔴 **2026-09-08 — AN EMPTY COMMIT IS THE WRONG RE-TRIGGER IN THIS REPO, AND IT
  MANUFACTURES A FALSE GREEN.** `clawgate-ci-pipeline.yaml:13-14` filters on *push
  touching `containers/clawgate/**`*, so a zero-file commit matches nothing and
  `clawgate-ci`, `clawgate-e2e` and `ux-audit-clawgate` never fire — while
  `gitops-validate` registers anyway, because its `ci-changed-paths.py` reads an
  absent path list as "unknown → run". Result: one check passes, `mergeStateStatus`
  reads **CLEAN**, and the PR looks mergeable with three quarters of the gate unrun.
  Re-run the PipelineRun from its own spec instead.
- 🔴 **2026-09-08 — `gh pr checks` CAN REPORT A STALE ALL-PASS ON THE CORRECT HEAD
  SHA, SECONDS AFTER A PUSH, AND BOTH DOCUMENTED RULES MISS IT.** Measured on `#754`:
  immediately after pushing, the rollup showed **4/4 pass** against the new head — the
  minimum-count rule and the all-terminal rule BOTH passed, and it was the parent
  commit's verdict. The real checks reset to pending ~45s later. **The discriminator
  is elapsed time versus pipeline duration**: Tekton cannot run in seconds. Wait for
  the rollup to RESET before believing it.
  ⚠ And the minimum is now **5**, not 4 — `comic-flex-ci` joined the rollup.
- 🔴 **2026-09-08 — RANK 18's NODE IS WRONG NOW; THE MECHANISM SURVIVES.** That entry
  pins `talos-uvh-gtj` as the bad node (0-pass/14-fail). Measured this session:
  `uvh-gtj` **passed twice** and the failure landed on **`talos-xr6-r7p`**, which has
  tasks **#411** and **#431** open against it. Device-isolated I/O contention still
  explains it — `dbtest: created … from template in 1m26.403s` against sub-second
  locally — but *"check whether it ran on uvh-gtj"* now mis-attributes. **Read the
  node from the PipelineRun; do not carry the name forward.**
  🔴 The decisive control is a re-run **from the run's own spec**: same commit, same
  pipeline, different node → pass. That separates the platform from the diff in a way
  no amount of re-reading the diff can.
- 🔴 **2026-09-08 — FOUR MERGED-TREE DEFECTS IN ONE BATCH, NONE VISIBLE FROM ANY PR.**
  Five branches, each 4/4 green, produced: two BUILD failures (517's and 523's code
  calling `RenderChatPage`/`ChatPagePath`, which 518 renamed — zero shared files), a
  **duplicate migration number** (516 and 517 both added `0032`; `version INT PRIMARY
  KEY` plus a `current` read once before the loop ⇒ the second INSERT dies and the pod
  **fails to start**), and one **semantic** conflict that compiled fine (523's test
  pinned a sentence 518 rewrote). ⚠ The migration one is NOT silent — a pre-existing
  guard on trunk catches it — but no PR's CI runs the merged tree, so it would land
  red on trunk. **Build the integration branch and run the suite there before merging
  a batch.**
- ⚠ **2026-09-08 — a rename sweep is unsafe when the new name CONTAINS the old.**
  Replacing `ChatView{` → `SessionChatView{` turned already-correct files into
  `SessionSessionChatView`. Six files were damaged and repaired; the check that
  settled it was diffing each against trunk for byte-identity, not re-reading the sed.
- ⚠ **2026-09-08 — three separate agents independently invented the same `serverEnv`
  e2e fixture.** That is a signal about the harness, not a coincidence: the terminal
  write surface cannot be armed from outside, so every spec that wants to click Send
  has to build its own server.

- 🔴 **`tier` discriminates the DOOR, not the CALLER.** Measured on the live ledger: 6 `browser`
  / 3 `token`, non-forgeable (a POST to the token route carrying `{"tier":"browser"}` stored
  `tier:"token"`). But one shared terminal token means a chief write is indistinguishable from a
  host-agent write. Do not claim per-session attribution.
- 🔴 **Task 180's premise is STALE.** clawgate is `RollingUpdate`, not `Recreate` — verified on the
  live deployment and in `clusters/workbench/apps/clawgate/deployment.yaml:60`, which carries a
  rollback comment describing how to revert *to* `Recreate`. Two agents were briefed with the old
  claim by me and it fed into their risk assessments.
- 🔴 **clawgate runs on the WORKBENCH cluster, not homelab.** `ns clawgate` does not exist on
  `$KC_HOMELAB`. The devpod agents are on homelab. Two different clusters; I briefed this wrong once.
- 🔴 **`.envrc` is NOT tracked in devrc** (it IS in homelab-infra). Do not carry the assumption across.
- 🔴 **Only `(host, pane_id)` uniquely identifies a window.** Measured over 87: `pane_id` 56
  distinct, `window_id` 56, `codename` 19 of 69 **with 18 windows carrying none**. `codename` is
  unusable as a selector. The wire key for the tmux session is camelCase **`tmuxSessionName`** —
  querying `session` returns a confident "88/88 missing".
- 🔴 **`term send` is subject to NONE of the calling session's PreToolUse hooks.** Found live: a
  a wide tmux kill was blocked by a guard, and a send would have bypassed it. That is the
  blocked action, not a workaround.
- 🔴 **The clawgate skill is a home-manager `home.file` COPY** (`readlink -f` lands in
  `/nix/store`), so merging #1515 does not make it live — that needs `home-manager switch`.
- 🔴 **SKILL.md has a byte-exact ratchet at 15,665** (`test_the_skill_did_not_grow`), rule: any
  addition needs an eviction in the SAME commit. It is a DIFFERENT instrument from
  `scripts/skill-audit.py` (12,038 budget / 40,960 cap) — reading the wrong gate and finding
  headroom is indistinguishable from having it, until CI.
- 🔴 **A poll must bind to the head SHA.** After a force-push the PREVIOUS head's terminal checks
  linger, so a poll reading them settles on another commit's verdict. Use ONE
  `gh pr view --json headRefOid,statusCheckRollup` call and abort if HEAD moves. Also: `gh pr
  checks` prints `pass`/`fail`, **not** `success`/`failure`, and prints prose ("no checks reported
  on the branch") when the rollup is empty — a naive line-count reads that as checks. Minimum 4
  for homelab-infra, 3 for devrc.
- 🔴 **The devrc gate names only ONE failing test even when several targets fail.** Read the
  per-target `FAIL  <path>  (…failed=N…)` lines; the summary line is not the set.
- 🔴 **Verified-in-isolation is the vacuous green.** #1483's merge-blocker was a fixture shebang
  tripping a cross-file guard; three audit rounds ran the changed file alone (50/50 green) and
  none could see it. Three separate PRs this session were blocked by cross-file ledger guards.
- **Scoping a sentence to make a claim true is what keeps failing.** #1483's ladder converged only
  when the guarantee moved INTO the code (a union that is a superset by construction), after two
  rounds of replacing one false absolute with another.
- **A poll timing out is not evidence about the PR** — check the PipelineRun's real
  start/completion times. devrc's pytests tier genuinely runs 20–55 min; a status timestamp is when
  the status was POSTED, not the run duration.

- 🔴 **A guard can be tripped by PROSE THAT DOCUMENTS IT.** Three reds in one day on devrc
  `main`, all from handoff docs, one of them a doc whose only content was writing up the
  guard. When a check scans every tracked file for a *word*, every future write-up about it
  is a future red. Scope such a check to the text that can actually execute, and let a
  shape-aware check cover prose.
- 🔴 **A regex without a left word boundary matches inside another word** — `kill-session`
  lives inside `s·kill-session`. My own diagnostic grep reproduced the same bug while
  hunting it, which is how it hid: the tool and the guard agreed, and both were wrong.
- 🔴 **A duplicate dict key is silent in Python.** Adding a ledger entry another session had
  already added shadowed theirs with no error. Only the mutation check (drop it → nothing
  changes) revealed it was doing no work.
- 🔴 **A mutation planted in an UNTRACKED file scores SURVIVED.** The scanner iterates
  `git ls-files`; appending to a path git does not know produces a green that means nothing.
  Verify the target is tracked-and-modified (`git status --porcelain <path>`) before reading
  the verdict.
- 🔴 **`git commit -F` with a heredoc is parsed by the bash guard as real commands.** A commit
  message quoting a banned command is itself blocked. Write the message with the Write tool
  and pass the file — which the repo's rules prefer anyway.
- **A `gh pr checks` poll must bind to the head SHA in ONE json call.** After a force-push the
  previous head's terminal checks linger; a poll reading them settles on another commit's
  verdict. Mine printed `SETTLED n=4` over a rollup that was actually empty.

- 🔴 **A MERGED clawgate commit is NOT a deployed one, and nothing says so.** clawgate's image
  pin is a literal tag with no image automation, so committing deploys the MANIFEST, not the
  code. Seven merged commits — `#771` among them — sat inert for two days. The only thing that
  noticed was the producer itself, logging `transcript streaming failed this poll … HTTP 404 to
  a read` and carrying on, because stream failures are deliberately isolated from the write path
  and the 5-minute bulk push kept feeding the read model. **Two days inert, no outage, no alarm.**
  After merging anything here, re-read the pin and `clawgatectl health` — `merged` is not evidence.
- 🔴 **A Flux Kustomization SUSPENDED BY HAND reports `Ready=True ReconciliationSucceeded`
  forever.** `agent-pods` was suspended via the flux CLI on 2026-06-07T01:58Z, with `suspend`
  absent from git and therefore unremovable by kustomize-controller under SSA. Everything merged
  under `clusters/homelab/apps/agent-pods/` since June was silently inert, and nothing in git, no
  task and no doc recorded it. **The only tell was that its `lastAppliedRevision` differed from
  every sibling's** — compare that across Kustomizations, never the Ready condition.
- 🔴 **`GET /api/transcripts/stream/cursors` wants `X-Clawgate-Token`, not `X-Hook-Token`.** The
  wrong header returns `401 {"error":"invalid or missing hook token"}`, which reads exactly like
  a credential problem rather than a header-name problem. `auth.go:583-588` accepts
  `Authorization: Bearer` or `X-Clawgate-Token` and nothing else. Negative control for the route
  itself: an absent route returns 404, so 401 does mean "exists, needs auth".
- 🔴 **`gh pr checks` says `no checks reported on the branch` for a minute or so after a push,
  and that is not an absence.** Seen again 2026-09-12 on #1515 seconds after pushing; three
  Tekton checks registered shortly after. For devrc the expected minimum is **3**
  (`devrc-pytests`, `devrc-nodetests`, `devrc-cairn-client-runs`) — assert a minimum count rather
  than reading an empty rollup as "no CI".
- 🔴 **A `KILLED` Tekton verdict is not a code failure and must not be read as one.** #1549's
  three gates all reported `KILLED: … the gate pod died at or after step pytests
  (preempted/evicted/OOM/timeout)`. That PR's red said nothing whatsoever about its content; the
  content question had to be settled by running the tests in a clean worktree instead.
- 🔴 **`claim-work` reports "THIS SESSION (you already hold it)" for claims made by EARLIER
  sessions in the same clone.** Ownership is `/etc/machine-id` + `git rev-parse --git-dir`, so
  every session running in the devrc primary clone shares one owner id — rc 12 means "this
  host+worktree", not "this conversation". Two of this session's three relevant claims were
  minted hours earlier by other sessions and read as mine. A genuinely concurrent peer shows a
  DIFFERENT owner-id (measured: `8070ae169ba8` vs `3e776e148119`), which is the only reliable tell.
- 🔴 **The ranked list in this doc is 979 lines and is a REPLACE section.** A delta that includes
  `## Next steps (ranked)` without carrying the whole history forward DELETES every completed
  rank — and the ranks are load-bearing, because a rank is half a `claim-work` slug. Build the
  section by reading lines out of the existing doc and editing anchors in place, never by
  retyping it. `handoff_doc.py`'s durable-drop warning is the backstop, not the plan.

- 🔴 **`gh pr merge … | tail` reports TAIL's exit status.** An `rc=$?` after that pipe prints `0`
  for a failed merge. **Verify a merge by CONTENT** — `gh pr view --json state,mergedAt,mergeCommit`
  plus `git show origin/main:<path>` / `git cat-file -e` — never by the piped status. Same family
  as the squash-merge ancestry trap already in `claude/RULES.md`.
- 🔴 **A `devrc-pytests` red can be a `node --check` TIMEOUT, which is a statement about the pod.**
  `test_mjs_parses[attachments.mjs]` and `[checklists.mjs]` both failed with
  `subprocess.TimeoutExpired … timed out after 30 seconds` in a 784s run, on a PR that changed one
  markdown file and added no `.mjs`. `node --check` has no legitimate slow path. Evidence went to
  clawgate card **515** (the saturation class) rather than a new card. The tell is a failing test
  that names a file your diff never touched — read the gate pod's own log
  (`kubectl logs -n tekton-ci <pipelinerun>-gate-pod`), do not re-run blind.
- 🔴 **A PR red can be a STALE BASE measured in MINUTES, not days.** #1515 was refreshed at
  00:00:22Z; #1567, which fixes the exact test it then failed, merged at 00:07:05Z. Seven minutes.
  **`git merge-base --is-ancestor <fix-sha> <pr-head>`** answers it in one command — do that before
  theorising about the code.
- 🔴 **`claim-work` says "THIS SESSION (you already hold it)" for claims made by EARLIER sessions in
  the same clone.** Ownership is `/etc/machine-id` + `git rev-parse --git-dir`, so every session in
  the devrc primary clone shares one owner id; rc 12 means "this host+worktree", not "this
  conversation". A genuinely concurrent peer shows a DIFFERENT owner-id (measured `8070ae169ba8`
  vs `3e776e148119`).
- 🔴 **The lock cannot catch a duplicate when the two sides derive DIFFERENT SLUGS for one job.**
  #1549 was worked concurrently under `devrc-kill-ledger-scope-executables` and
  `ci-flakes-and-misattribution-11`; both sessions reached the same verdict and one was wasted.
  The `gh pr list` sweep is what sees this, not the lock.
- 🔴 **A dispatched opencode run writing to the system temp directory is auto-rejected and DIES.**
  Two of three browser dispatches died exactly that way — the second **despite a brief that both
  forbade it and supplied a pre-created in-project scratch directory**. A prose prohibition does
  not reliably stop it. If a browser measurement matters, drive it from a session that controls
  its own writes.
- 🔴 **"The tmux overview grid does not auto-refresh" is REFUTED — do not re-file it.** Source at
  `origin/trunk`, `containers/clawgate/internal/ui/tmux.go:238` (`hx-target="#panel-tmux"`) carries
  `hx-trigger="load, every 60s, sse:tmux.changed from:body, clawgate:resync from:body,
  clawgate:termwrite from:body"`. The observation behind the claim waited **30 seconds against a
  60-second poll** — a window shorter than the period under test — and the run also reported its
  tab was **hidden and therefore throttled**, which suppresses htmx timers regardless. Two
  independent artifacts, one non-defect. `.opencode-dispatch/tmux-ui-verify/findings.md` carries
  the retraction inline.
- 🔴 **A tmux CODENAME IS NOT UNIQUE.** `mango` named three panes at once — laptop `%8`, workbench
  `%42` (a live working pane) and workbench `%75` (the scratch one). Disambiguate by host + window
  index, and when acting on a reply control select it by `data-reply-entry`, never by position.
- ⚠ **The tmux read model lags reality.** After the operator closed pane `%75`, the snapshot still
  listed it — "as fresh as the last push", exactly as `clawgatectl tmux` warns. Do not read its
  absence or presence as live truth.

- 🔴 **A `pgrep`/`grep -f` on a process name MATCHES YOUR OWN COMMAND LINE.** Counting "2 live
  playwright processes" was **my own grep**, and that phantom count is the only thing that stopped
  five orphaned containers being cleaned for hours. The rule is in `RULES.md` and was walked into
  anyway. Use a real ownership signal (here: client connections), never a name pattern.
- 🔴 **An IDENTICAL failure across N independent targets is a fact about your INSTRUMENT.** A
  connection probe returned `<unreachable>` on all five containers; the cause was `-U postgres`
  while the harness creates a `clawgate` role. Read as data it would have reported five wedged
  databases. Validate, then re-read.
- 🔴 **A claim about a FIXTURE is not a claim about the HARNESS.** "e2e has no host agent, so
  `queued` is terminal by construction" was true of the fixture and false of the harness —
  `reply-delivery.spec.ts` already sets `CLAWGATE_TERMINAL_TOKEN` and can play the agent itself, and
  `task-sse-regroup.spec.ts` already existed to close that seam class. This was asserted twice in
  prose before a Round 0 audit found the counter-evidence in the same directory.
- 🔴 **An agent killed mid-mutation leaves the MUTATION APPLIED, and it looks like ordinary work.**
  One died right after "now apply the decoy mutation"; its worktree held `attention.go` with the SSE
  subscription stripped from `hx-trigger` and re-spelled on `data-note`. Committing it would have
  silently unsubscribed the attention panel while the old spelled guard stayed green. **On resume,
  diff every uncommitted file and classify it before trusting any of it.**
- **Sweep the SHAPE, not the site.** Told to fix one walkable guard, a sweep found **five** of the
  same shape (#817), and four more docstrings claiming a hand-maintained list "fails if the set
  GROWS" when it measurably does not.
- **Round 0 earns its place.** It split #811 (92-line fix vs ~460-line axis), retired six pieces of
  dead code on #803, and refuted the "cannot verify without deploying" premise. It is the only round
  that asks whether the change should EXIST.
- **Deploy doc rot, unfixed:** `deploy.md` says to build via `DOCKER_HOST=ssh://zach@192.168.50.250`
  — but a session running ON the workbench IS that daemon, and the ssh form fails
  `Too many authentication failures`. Its CSS sanity figure "~36 KB" is stale-low; the real output is
  **45,762 bytes**. Also: use the repo-pinned Tailwind **v3** from `node_modules`, not
  `nix-shell -p tailwindcss`, which now ships v4.

- 🔴 **`devrc` `main`'s red `pytests` gate is a MOVING SET, not one stuck test — so naming "the"
  pre-existing failure implies a stability that does not hold.** The close-out block above names
  `test_the_skill_names_the_same_DEPLOYED_path_the_hook_prints`, measured failing at `origin/main`
  on 2026-09-14. Hours later, on `devrc#1685`, the named verdict was a **different** test:
  `test_no_test_writes_a_usr_bin_env_shebang_at_runtime` (`failed=2`, collected 23,703). Both are
  real and both pre-exist. **A session that checks "is it still that test?" will get a different
  answer and may conclude its own change broke the gate.** The discriminator is always the same and
  costs one command — run the SPECIFIC failing file in a detached worktree of pristine `origin/main`:
  `git -C $DEVRC worktree add --detach /tmp/ctl origin/main` then
  `(cd /tmp/ctl && nix-shell -p "(python3.withPackages(ps:[ps.pyyaml ps.pytest]))" --run 'python3 -m pytest -q scripts/tests/<file>.py')`.
- ⚠ **Two ways that control itself returns a non-answer, both hit while doing it:** (a) a broad
  `-k <name> scripts/` collects modules needing `psycopg2` and dies `INTERNALERROR ... 3 errors` —
  that is the instrument, not a verdict; (b) grepping for the test NAME finds a file that merely
  *mentions* it (`scripts/stale-base-triage.py`), and pytest then prints **`no tests ran`** — a zero
  that proves nothing. Grep for `def <test_name>` to get the defining file, and treat `no tests ran`
  as "wrong target", never as "passes".
- 🔴 **An item completed AFTER an eviction sweep must be evicted in the change that closes it.**
  Rank 18 survived the 2026-09-14 sweep legitimately (it was open then), was closed by
  `ZacxDev/homelab-infra#820`, and then sat in the ranked queue as available work until `devrc#1685`.
  A `/resume` session would have `claim-work`'d and redone it. The eviction note in
  `## Next steps (ranked)` now carries this rule; the failure mode is that closing and evicting are
  two different actions and only the first feels like finishing.

- 🔴 **`./e2e/run.sh <one-spec>` IS NOT THE e2e TIER.** Quoted as one twice this session. It hid a 9-test break in a sibling spec AND left `trunk` broken by `#824`. The full suite is 34 spec files / ~225 tests / **~32 min**; it needs `nohup` + a wait loop, not a foreground call (the tool call times out at 10m).
- 🔴 **`tekton/clawgate-e2e` EXISTS and DOES run Playwright** — separate from `tekton/clawgate-ci`, which is Go-only. An audit claimed CI runs no e2e; that is false and cost a wrong attribution. **Read all four checks on a clawgate PR.**
- 🔴 **A fresh clawgate worktree has no `web/static/app.css`** (gitignored), which reds `TestTheStylesheetCarriesNoNewTestOnlyClasses` until tailwind is run. Environment, not a defect.
- 🔴 **A Go raw string cannot contain a backtick, escaped or not.** `tmuxGroupScript`/`tmuxViewScript` are backtick-delimited raw strings; putting `` `foo` `` in a comment inside them breaks the build. Cost two build failures.
- ⚠ **`| tail` eats the exit status, and the harness's own "exit code 0" is the PIPELINE's.** A background `pytest … | tail` was reported as `exit code 0` over a real `1 failed, 875 passed`. Capture `rc=$?` on the command's own line and read the summary; quote the PAIR.
- ⚠ **A Go `0 passed / 0 failed` is a COMPILE failure, not a clean run.**
- **Deleting a feature means deleting its tests — and keeping what they KNEW.** Item 9 removed 6; each site carries a note recording the discovery, so it is not paid for twice.
- **Five of my claims were falsified by audits this session**, four of one shape: asserting a property of a guard without running it.

- 🔴 **A RECON AGENT'S "IT DOES NOT EXIST IN THE REPO" CAN BE TRUE OF THE SOURCE AND FALSE OF THE BEHAVIOUR.** Asked to locate item 6's `selection_menu`, a thorough read-only agent grepped the whole worktree case-insensitively, found **zero** hits, and reported the item needed re-pointing. It was right about the grep and wrong about the system: `selection_menu` is a **`session-manager` waiting-signal NAME that arrives in the DATA**, reaches the UI as `TmuxWaitingSignal.Signal` (`internal/ui/tmux.go:202-203`) and is rendered by `waitingEvidence` (`:2956`, emitted `:2608`). Taking the conclusion at face value would have cost a round trip to the operator asking what the item meant. **When a grep says a user-visible string is absent, ask where the string is PRODUCED before concluding the feature is absent** — a value that is data on this side of a wire is a literal on the other.
- 🔴 **`origin/trunk..HEAD` LISTING COMMITS IS NOT EVIDENCE OF UNMERGED WORK AFTER A SQUASH.** It listed all four of #826's commits minutes after the squash landed, and `git diff --stat origin/trunk HEAD` showed 794 deletions on top — both readings say "do not delete this worktree, work would be lost". Both are artefacts: the deletions were trunk's OWN newer commits in files the branch predates. The discriminating check is per-file and takes one loop — for each file the branch changed, `git diff --quiet origin/trunk HEAD -- <file>`; all six were byte-identical. **Verify a squash by CONTENT, and scope the diff to the files the branch actually touched**, or trunk's unrelated movement reads as your work going missing.
- 🔴 **THE HANDOFF SIZE GATE'S PLAYBOOK FORBIDS THE OBVIOUS FIX.** `## Gotchas` is by a wide margin the largest section of this document — larger than every other section combined — and moving it is what a size-driven read reaches for first. The playbook explicitly refuses: *"DO NOT satisfy this by deleting an open investigation, a gotcha or a ruled-out theory"*, because those are the sections whose whole value is that a future session does not repeat the work. **Read the playbook's ORDER before picking a target** — but step 1 (evict what has CLOSED) is a **body-level, sometimes cross-file judgement, not a heading scan**: `d0d0dddd` is the worked example, where the previous sweep was enumerated from `^### ` headings and missed three blocks whose closure was recorded only in a BODY, one of them declared in a different file entirely. Read each block, not its heading. On this document step 1 freed **46,902 B** in `61ab9487` (245,697 → 198,795) and a further **6,377 B** in `d0d0dddd` (204,328 → 197,951). ⚠ **Both figures are PAST TENSE and neither is a ranking of what is left**: `d0d0dddd` asserts every block still in `## Open investigations` is open, so step 1's REMAINING yield here is not those numbers — measure before reaching for it again. Pruning `## Gotchas` is still worth doing, but it is separating each imperative from its worked example, one at a time — judgement work, not a size exercise, and it must not be done under deadline. ⚠ **The per-section byte figures are deliberately NOT written down here.** They are derived measurements that go stale in the same commit that edits the sections — this bullet once carried `133,129 B / 54%` and `89,982 B`, and both were already wrong in the PR that shipped them. That is the precedent `scripts/lib/handoff_budget.py` sets for the ceiling itself (*"THE CURRENT SIZES ARE DELIBERATELY NOT WRITTEN DOWN HERE"*). Measure at the moment you need it: `LC_ALL=C awk '/^## /{s=$0} {n[s]+=length($0)+1} END{for(k in n) printf "%8d  %s\n", n[k], k}' claudedocs/handoff-tmux-webapp.md | sort -rn` (`LC_ALL=C` is what makes `length()` count bytes rather than characters).
- **The doc-size ceiling lives in `scripts/lib/handoff_budget.py`, not in the test that owns the assertions.** `scripts/tests/test_handoff_doc_size.py` imports `MAX_BYTES`/`GRANDFATHER_STEP`/`GRANDFATHERED` from there; grepping the test for `MAX_BYTES =` finds the import, not the value. ⚠ And the test module cannot be imported outside the dev shell (`import pytest` at module scope), so `nix develop ~/workspace/devrc -c python3` or a plain grep of the lib is the way to read a constant.
- **`claudedocs/refs/` is exempt from the size ceiling because the scanner globs `handoff-*.md`** — a refs file does not match the pattern. ⚠ That exemption is also why a demoted block is invisible to `handoff_search`: the pointer left behind in the doc is the only route back to it.

## How to verify

```bash
# 🔴 THE CURRENT WORKTREE IS 593c. `~/workspace/homelab-tmux593b` was REMOVED when #826 merged.
cd ~/workspace/homelab-tmux593c/containers/clawgate

# a fresh worktree has NO web/static/app.css (gitignored) -> TestTheStylesheetCarriesNoNewTestOnlyClasses
# reds until tailwind runs. Use the repo-pinned v3 from node_modules, NOT `nix-shell -p tailwindcss` (v4).
npm run build:css

# the FULL e2e tier — NOT one spec. ~32 min; run it detached (the tool call times out at 10m).
nohup ./e2e/run.sh > /tmp/e2e-593c.log 2>&1 &
# wait on CONTENT, never on a pipe's exit code:
until grep -qE '^\s+[0-9]+ (passed|failed)' /tmp/e2e-593c.log; do sleep 30; done
grep -E '^\s+[0-9]+ (passed|failed|flaky|skipped)' /tmp/e2e-593c.log
grep -oE 'tests/[a-z0-9-]+\.spec\.ts' /tmp/e2e-593c.log | sort -u | wc -l   # expect 34

# the Go tier, counted rather than read off `ok` (a 0/0 is a COMPILE failure, not a clean run)
go test ./internal/ui/ -count=1 -v 2>&1 | grep -c '^--- PASS'   # baseline at 248a0c4ed is 511
go test ./... -count=1

# all FOUR checks on the PR — clawgate-e2e is the one that catches this class
gh pr checks <n> --repo ZacxDev/homelab-infra

# this doc's own size gate, before any /handoff write
stat -c '%s' ~/workspace/devrc/claudedocs/handoff-tmux-webapp.md   # allowance is in scripts/lib/handoff_budget.py

# the live queue and its lock, before touching any rank
git -C ~/workspace/devrc show origin/main:claudedocs/handoff-tmux-webapp.md \
  | sed -n '/^## Next steps (ranked)/,/^## Open investigations/p' | grep -oE '^[0-9]+\.'
claim-work --list
```
## Run this first — the index, one read-only command
```bash
cairn recall --repo ~/workspace/devrc
```
🔴 **The old spelling here (`python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py`) is a
DEAD PATH as of `devrc#1508` (2026-09-11)** — devrc's five forked reader modules were deleted and
the reader now ships in the pinned `cairn` package. Run bare, it exits 4 refusing an undateable
store. `cairn recall` syncs first, then runs the same reader, so the answer carries its own
freshness. This was the FIRST command in this doc and it had stopped working.

Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ **This doc spans TWO repos.** The design lives in `devrc`; all the code lives in
`homelab-talos` (remote `ZacxDev/homelab-infra`) under `containers/clawgate/`.
