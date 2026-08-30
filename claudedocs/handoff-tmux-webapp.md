---
---
# Handoff: tmux-webapp — 2026-08-26

## Goal
A **clawgate feature**: a webapp that visually organizes and gives live terminal interaction
with tmux sessions across workbench + laptop, with a composable view system agents can drive,
and an **attention queue** that surfaces sessions needing a human so Zach can jump straight in.

## Status

**Phases 1–4 SHIPPED. Ranks 5, 6, 7, 8a and 8b are ✅ DONE. clawgate is at 0.8.18 and
`clawgatectl` MATCHES it on both hosts. The lowest-numbered OPEN item is 8c.**

🔴 **DO NOT READ A VERSION FROM THIS DOC — `clawgatectl health` is the only authority.** It said
**0.8.18** on 2026-08-30, after nine values in four days. ⚠ **0.8.12 and 0.8.14 EXIST IN HARBOR AND
WERE NEVER DEPLOYED** — each was built, then discarded before merge because trunk gained a
`containers/clawgate` fix while the PR was in review, so the image would have carried a HIGHER
version number with LESS code. A tag existing is not a tag having shipped.

✅ **RANK 8a — `clawgatectl` is 0.8.18 on BOTH hosts.** No devrc change was needed:
`nix/pkgs/tools/clawgatectl.nix` builds from each host's LOCAL `~/workspace/homelab-talos` tree and
DERIVES its version from the Go source, so the fix was `merge --ff-only origin/trunk` on the laptop
(5 behind, its source still said 0.8.17) then `ship.sh`. Verified past the label with a cross-host
round trip — a matching label is exactly what the 2026-08-14 incident also showed.

✅ **RANK 8b — `ZacxDev/homelab-infra#566`, squash `4964d223`.** The layout tab's first browser
coverage, `containers/clawgate/e2e/tests/layout.spec.ts`. `clawgate-e2e` **125 tests, 2 skipped**
on the FINAL sha (not a stale green five commits back). Verified on `origin/trunk` by content.
🔴 **SIX audit rounds, and the first FIVE each found that the previous round's fix had created the
next defect** — five for five of one class: *a claim wider than the code*. Two were coverage
REGRESSIONS introduced while claiming an improvement. See Gotchas; the full measured record is in
the PR's six `audit-claims` comments.

**Landed this session:**
- `ZacxDev/homelab-infra#566` — rank 8b, squash **`4964d223`**.
- `innovation-upstream/devrc#1090` — squash `52fdd983`, the previous handoff, rescued from a
  local-only commit on `main` that branch protection had stranded.
- `innovation-upstream/devrc#1101` — squash **`a5bc5df6`**, this doc's previous update **plus a
  correction**: I had filed "clawgate-e2e never registered on #566" as an open investigation. It
  had registered; it simply had not been SCHEDULED yet when I read `gh pr checks` minutes after
  opening the PR. The retraction is in the doc, superseded-but-verbatim.
- `innovation-upstream/devrc#1056` — 🔄 **STILL OPEN**, rebased onto current `main` (`352f4ade`)
  and re-triggered. Claim `tmux-webapp-6` still held.

**Live at handoff:** both hosts at devrc `main`, `ship.sh` rc 0 with cross-host agreement COMPARED.
Terminal write surface still boots `DISABLED (fail-closed)`, its intended resting state.
**No `clawgate-task:` field written** — `clawgate_handoff.sh resolve` exited **5**; its positive
control shows the board answered 11 links for another session, so the board is reachable, but an
unknown session id ALSO answers 200 with an empty array. That zero is not a clean bill of health.

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
5. ✅ **DONE 2026-08-29 — `ZacxDev/homelab-infra#516`, squash `c8635976`, deployed 0.8.13.**
   `requireTerminalToken`: the ONLY fail-closed tier. 🔴 **The secret is still UNPROVISIONED and
   the surface boots DISABLED — correct, not a regression.** The SOPS age identity is on NEITHER
   host. Wired `optional: true`, because without it a missing key is a `CreateContainerConfigError`
   that stops the WHOLE pod. **To arm it:** `clawgate gentoken` → `sops
   clusters/workbench/apps/clawgate/secrets.enc.yaml`. No code or manifest change needed.
   forcing: none
6. ✅ **DONE 2026-08-29 — `ZacxDev/homelab-infra#527`, squash `7bb78908`, deployed 0.8.16.**
   🔴 **A PANEL STORES A DESCRIPTION, NOT A REFERENCE.** Measured across all 79 live windows: no
   field is both unique and stable. So panels resolve against the live snapshot on every read and
   report `resolved`/`ambiguous`/`missing`/`unreported`/`host_unreachable`.
   ⚠ **Claim `tmux-webapp-6` is STILL HELD** pending `devrc#1056`.
   forcing: none
7. ✅ **DONE 2026-08-30 — `ZacxDev/homelab-infra#538`, squash `fb9b75e5`, deployed 0.8.18.**
   The htmx layout tab. 🔴 **THE UI TIER CARRIES ONLY REVERSIBLE CONTROLS, BY CONSTRUCTION.**
   The destructive control was REMOVED — button, route, handler and ledger entry — leaving
   `clawgatectl panel rm` (requireHookToken) as the only delete path.
   forcing: none
8. **Housekeeping — 8a and 8b DONE; 8c, 8d, 8e open.**
   forcing: gate — `clawgate-e2e` was green through all four of #538's audit rounds while running
   ZERO specs touching layout. 8b closed that; 8e is the floor that same gate reads.
   - a. ✅ **DONE 2026-08-30** — `clawgatectl` 0.8.18 on both hosts. See Status.
   - b. ✅ **DONE 2026-08-30 — `ZacxDev/homelab-infra#566`, squash `4964d223`.** Two → three
     Playwright tests; `clawgate-e2e` 125/2 on the final sha; six audit rounds.
   - c. Eight sleep-based timing bets in
     `containers/clawgate/internal/api/{push_task,task_comment}_test.go` (pre-existing; mechanical
     now `awaitPushesSettled` exists).
   - d. A scanner test for in-body `! grep` — closing condition: a test in both bats suites that
     reds on a planted `! grep` assertion.
   - e. **Re-derive `MIN_PASSED` in `clawgate-e2e-pipeline.yaml`. 🔴 THE NUMBER IS NOW MEASURED
     FROM CI: `125 tests, 2 skipped` on `c617bdd5`** (it was 124 before 8b's third test landed; do
     not use that). At the file's own 110/118 ≈ 93.2% ratio that is **`MIN_PASSED: 116`**, against
     a current 110 — i.e. it tolerates losing 15 tests. ⚠ `MAX_SKIPPED` is 6 against a measured 2;
     leave it. Closing condition: 116 committed. ⚠ Also stale in the same file: the prose at
     ~:522-527 still says "120 tests in 21 files … 118 passed / 2 skipped".
     forcing: regression — the floor silently tolerates losing 15 of 125 tests.

🔴 **There is no rank 9.** A previous revision listed one — "get three portable lessons into
MEMORY.md" — and it was NOT a work item: the operator confirmed 2026-08-27 that MEMORY.md is not
used here, so nothing could ever have closed it.

10. **Two guard gaps #457's ladder left open deliberately, both scaffolding-scope.**
    - **`RunSweeper`'s ticker survives `NewTicker`→`NewTimer`** against the whole api package.
      `RunRetention` and `RunReconciler` deserve the same check. Pattern to copy:
      `TestRunAttentionReapTicksOnTheIntervalItWasGiven`.
    - **The `main()` wiring ledger pins syntax, not reachability.** NOT closable statically; the
      compensating control is the runtime entry log line, which is itself guarded.
    forcing: none
11. **The archive drawer loses its `open` after every write-triggered swap.** KNOWN, UNFIXED, noted
    in-code at `layoutArchivedDrawer` with both rejected fixes. The correct fix is the
    `taskCardScript` treatment (a once-bound listener re-applying `open` after settle).
    🔴 **8b now makes this verifiable AND has already measured the trap:** `layout.spec.ts`
    deliberately does NOT pin the drawer's `open`, and mutant H (`<details open>`, i.e. the defect
    FIXED) is asserted to PASS — so the spec will not red the day someone fixes it. But a
    once-bound listener that swallows the summary's click IS mutant PD, which the spec DOES red.
    Read both before writing the fix. Closing condition: restoring three archived panels in a
    browser without reopening the drawer between each.
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

### 🔴 `scripts/tests/test_subsystem_store_api.py` is FLAKY on `main`, and nothing is fixing it
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
  isolation on clean `origin/main`, and the box was at load 18–51 from concurrent agents. Fixed
  by devrc#996 (`1b1f71ad`, "audit BEFORE responding, and serialise the audit sink"). **If it
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
