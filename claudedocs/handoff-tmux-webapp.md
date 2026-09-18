---
clawgate-task: 595
---
# Handoff: tmux-webapp — 2026-08-26

## Goal
A **clawgate feature**: a webapp that visually organizes and gives live terminal interaction
with tmux sessions across workbench + laptop, with a composable view system agents can drive,
and an **attention queue** that surfaces sessions needing a human so Zach can jump straight in.
- **closing-condition:** `check` — `clawgatectl task get 602` and `clawgatectl task get 603`
  both report `complete`. Round 2's operator feedback is fully tracked on those two cards; the
  perf-audit outcome lands on one of them or on a third card named here when it does.
  🔴 **FROZEN AT ROUND 2.** A round-3 browser judgement is a **NEW arc**, not another round of
  this one — that is the rule this doc has now exercised twice, and the rabbit-hole audit
  (75 days / 299 arcs) measured what ignoring it costs: round-1 objectives met by round 1–7,
  arcs then running 13–23 rounds off a queue growing 2–6 items a round.
  ✅ **The PREVIOUS closing-condition — a `judgement`, "Zach opens `https://clawgate.zacx.dev/tmux`
  and says whether the tmux page is right" — was MET, TWICE.** Round 1 (on `0.8.36`) returned
  seven defects, shipped as `#832` / `0.8.37`. Round 2 (on `0.8.38`, 2026-09-16) returned six
  items, now cards **602** and **603** plus one investigation. Both rounds answered "not right,
  here is what is wrong" — which is the judgement being rendered, not withheld.
  It is REPLACED rather than deleted because the work round 2 generated is the next arc on the
  same effort; the old line would otherwise make `/resume` report ADDRESSED and stop.
  ⚠ The front-matter `clawgate-task: 595` is left as-is — 595 is `complete`; this session's
  own linked rows resolved as `created`/`read` only (rc 6, no WORKED task), so nothing was
  recorded over it. The arc's LIVE cards are 602 and 603.

## Status

**Round 2 of the operator's tmux-page feedback SHIPPED, and the chief tier went from
built-but-switched-off to ARMED, DEPLOYED and EXERCISED — all in one session.** Live: clawgate
**0.8.41**.

⚠ Front matter still reads `clawgate-task: 595` and was deliberately left alone. This session
resolved **7 WORKED tasks** (rc 6) — 595, 593, 517, 518, 375, 521, 522 — so there is no single
owner to record; the existing readable field stands rather than being guessed at.

### Releases cut and verified this session
| version | pin commit | what |
|---|---|---|
| `0.8.39` | `5c8243a75` | tmux round 2 (`#837`), transcript archive (`#835`), chief auth ledgers (`#836`) |
| `0.8.40` | `2273a9dab`… (`d42bd6ca4`) | task 607 — `#839` re-tier + `#840` transcript grant |
| `0.8.41` | `2273a9dab` | task 522 — chief slide-out + per-window recap (`#842`) |

Each verified the same way: image proved to carry the code **before** the pin moved (markers + a
positive and a negative control), smoke container answered the version, **running pod digest ==
pushed digest**, 1/1/1, old ReplicaSets at 0, consumer `/health` answered.

### 🔴 The chief door is ARMED — and proven, not merely configured
Commit `70d689337`. Banner: `AGENT-IDENTIFIED door: CONFIGURED`.
- Credential is agent **id 71**'s `hooks_token` — canonical name **`zesty-stoat`**, displayName
  `chief`, namespace `devpod-zesty-stoat`. 🔴 **The canonical name is NOT `chief`** (`POST /agents`
  generates it and it is the namespace identity); `#842` ships a fixture with a **decoy agent
  slugged `chief`** so a slug lookup fails loudly. Resolve by id or displayName.
- Verified distinct from the hook and terminal tokens **before** writing — the server refuses to
  serve if they collide. Written with `sops set`, read back from the ciphertext, all five
  pre-existing keys confirmed intact.
- **Exercised, not assumed**: `GET /api/tmux/snapshot` and `GET /api/transcripts/{id}` both return
  **200 on the chief token, byte-identical to the hook token** (178,910 B and 1,132,239 B), junk
  bearer 401. And from **inside chief's own pod**, `clawgatectl tmux ls` returns the live read model.
- The pod holds only `CLAWGATE_API_URL` + `CLAWGATE_HOOK_TOKEN` (**`#838` is still held**), and that
  hook value IS row 71's own token — which is why the existing verb reaches the new tier.

### Cards closed
**602** `complete` · **593**, **517**, **518**, **375**, **595** `complete` · **607**, **522**,
**603**, **521** `ready_for_review`. 🔴 593/517/518 were closed by **operator decision** — none
carries author-written criteria, so the gate would have held them; what closed them is two real
browser review rounds.

### 🔴 Merged but NOT yet exercised by a human
`0.8.41`'s slide-out and recap have never been used. Live DOM confirms they RENDER — 79 cards, **79
recap elements**, `#chief-panel` present (positive control `#panel-tmux` 1, negative 0) — but
rendering is not working. **An opencode dogfood run is IN FLIGHT** (see Open investigations).

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


🔴 **Ranks 1–47, 50–52, 55, 58, 61–62 are CLOSED and were DEMOTED — verbatim, not deleted:** `claudedocs/refs/tmux-webapp-closed-ranks.md`. They are lessons rather than status, which is why they were demoted and not dropped. 🔴 Rank 18 joined them 2026-09-14 when `ZacxDev/homelab-infra#820` merged — an item completed AFTER a sweep must be evicted in the same change that closes it, or the queue offers finished work to the next session.

🔴 **A SECOND SWEEP 2026-09-14 EVICTED SIX MORE — 9, 17, 25, 26, 42, 45 — AND EVERY ONE HAD BEEN CLOSED FOR DAYS WHILE STILL READING AS OPEN.** Six of the fifteen entries the queue advertised were finished or fictional. **The mechanism is measured, not guessed: the first sweep keyed on each rank's HEADING LINE — a heading carrying `✅`, `DONE` or `CLOSED` — and every closure recorded only in a rank's BODY survived it**, predicting eviction for 58 of 62 ranks. Of the four exceptions, 53 is a false positive of the predicate (`WORKBENCH IS NOW DONE` in the heading, laptop half genuinely open); 61's closure was written as a `### ✅ RESOLVED` heading in the **Open investigations** section, a THIRD location; and 46 and 62 carried no marker anywhere and were evicted anyway — 🔴 **why is NOT explained, and an earlier draft of this sentence guessed "closed by the sweep session itself", which was false for two of the three it named.** What IS established is the false-NEGATIVE direction: **no rank with a body-only marker was ever evicted**, measured over all 62.

🔴 **THE MARKER LIVES IN THREE PLACES, AND A SCAN THAT READS ONLY THE RANK BODY MISSES THE THIRD.** Heading line → 58 of 62, evicted. Body only → 17, 25, 26, **survived**. A `###` heading in another section → 61, evicted. Nowhere at all → **42, 45, 46, 62**. **When you sweep: read each rank's WHOLE body, read the `###` headings elsewhere in the doc, and re-measure anything still carrying no marker against the code.** 🔴 **This class recurred THIS session in a different section** — the `#1718` eviction set was enumerated from HEADINGS and left three closed investigations behind, one of whose closure was declared in a *different file*, so no same-file scan could have found it.

🔴 **The surviving numbering is SPARSE ON PURPOSE — do not renumber and do not reuse an evicted number.** A rank is half a `claim-work` claim's identity (`claim-work --slug-for <this doc> <rank>`), so renumbering silently re-points every live claim, and reusing an evicted number points a new claim at closed work.

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
    forcing: gate — task 519's own closing condition names both hosts and a measured number, and
    that is the only criterion still unmet.
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



68. **Grade the four `ready_for_review` cards — 607, 522, 603, 521 — on the LIVE page.** Every one
    ends there for the same reason: a criterion only the operator can close. 522's closing condition
    names *"the operator on the live page"*; 603's "load earlier" has never been clicked; 607's
    criteria were written by a `claude-code` session (see its comment 1515) so grading them here
    would be self-grading. `clawgate.zacx.dev/tmux` on `0.8.41`.
    forcing: user — the operator asked for chief to be usable and these are the cards that say so.
69. **Decide `ZacxDev/homelab-infra#838` now that 607 has landed.** It is a HELD draft that renders
    `CLAWGATE_CHIEF_TOKEN` into **every** agent pod. When it was held the credential opened ONE
    approval-gated route; after 607 it opens **tmux windows, attention raise/resolve and the
    transcript read**, and the transcript return is ~1.1 MB of raw JSONL whose majority is tool
    RESULTS — file contents and command output. 🔴 That is the "review it once against its FINAL
    blast radius" moment the hold was created for. Its base was retargeted to `trunk` and the stack
    hazard is recorded on the PR.
    forcing: security — it widens a credential that reaches session content, to the whole fleet.
70. **`devrc/claude/skills/clawgate/reference/chief.md` is now FALSE.** Its table says the four
    capabilities answer 401 from a pod; since `0.8.40` they answer 200 for the chief row. It was
    correct until today and deliberately not fixed pre-merge — fixing it early would have made it
    wrong. Needs a devrc PR, and the content depends on what `#838` decides.
    forcing: regression — a shipped doc now contradicts the shipped code.
🔴 **Rank 71 — "read the opencode dogfood REPORT" — is CLOSED and EVICTED in this change.** There
is no REPORT: the run died ~3 minutes in on a rejected write to the system temp dir, having reached
capabilities 1–4 of 8. The verdict, the four PROVEN rows, the four COULD-NOT-TEST rows and the
correction of its one apparent defect are in `claudedocs/refs/tmux-webapp-closed-investigations.md`.
🔴 The number is NOT reusable — a rank is half a `claim-work` identity.

## Open investigations — live diagnosis state

🔴 **CLOSED investigations are DEMOTED, not deleted — verbatim in `claudedocs/refs/tmux-webapp-closed-investigations.md`.** Every block whose thread reached a verdict was moved there when this doc came within 63 B of its size ceiling. They are lessons, not status, which is why they were demoted and not dropped. ⚠ A `refs/` file is NOT indexed by `handoff_search` — search will not surface them, so **this pointer is the only index into that file**. 🔴 **That is exactly why the COUNT and the list of threads are deliberately NOT restated here.** Both are derived facts that go stale in the very commit that evicts the next block, and this pointer has already been wrong that way: it read *"21 blocks"* and enumerated five threads while the file held **24**, because the commit that evicted the extra three did not touch it. List them at the moment you need them, which also cannot go stale: `grep -n '^### ' claudedocs/refs/tmux-webapp-closed-investigations.md`.

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

### Choppy scrolling on `/tmux` — full page AND inside a card's chat, on `0.8.38`
- as-of: 2026-09-16
- **Symptom + exact repro:** operator scrolls `https://clawgate.zacx.dev/tmux` on the LAPTOP
  (tab `bw://laptop/work/484078947`). Scrolling is choppy both at page level and inside a
  card's chat panel. Reported 2026-09-16 against live `0.8.38`.
- **Observed (with values):** the originally-reported symptom was *"the periodic refresh resets
  chat scroll position"*. 🔴 `0.8.37` shipped a fix for exactly that (`#832` items 3+7:
  `hx-preserve` on the chat clamp), and the symptom is reported again ON a build containing it.
  Asked which axis and which direction, the operator answered **not sure — and reframed it as a
  performance problem** ("scrolling is choppy, full page and in chat"), which is a different
  claim from a scroll RESET.
- **Ruled out:** nothing yet. via: assumed
- **Leading hypothesis:** two rival mechanisms, and an empty/ambiguous report cannot separate
  them — (a) `hx-preserve` is failing for this card, so the clamp is a fresh element each swap
  and `toEnd` re-scrolls it; the invalidation is deliberate — `tmuxChatClampID`
  (`internal/ui/tmux.go:3536`) includes the resolved Claude session id, so a session id that
  flaps or arrives late changes the id every render and defeats the preserve. Or (b) it is not a
  reset at all but **paint/compositing cost** during scroll — many `box-shadow`/`backdrop-filter`/
  `sticky` elements inside a scroll container, plus a full `#panel-tmux` innerHTML swap every 60s
  that re-executes every in-body `<script>` (`tmux.go:2938` documents that re-execution).
  The source itself names a third, explicitly unmeasured: a conversation that SHRINKS between
  refreshes makes the browser clamp `scrollTop` (`tmux.go:3852-3856`).
- **Next probe:** the dispatched perf audit — DOM/compositing counts, long tasks and frame
  intervals over a real window, and the swap size — each with a positive control, run against
  that tab. 🔴 **Do not fix anything until it reports which mechanism the numbers support**; a
  theory that explains the symptom is not evidence for it, and one fix for this symptom has
  already shipped and not resolved it.

## Gotchas
🔴 **Dated evidence for this section — the measurements, run ids, byte counts, PR numbers, the
incident narratives and the superseded reasoning — lives verbatim in
`claudedocs/refs/tmux-webapp-gotcha-evidence.md`** — the whole section as it stood at `d6e94dd2`,
byte-identical. What is below is the RULES. When one of them surprises you, read the story there
before deciding it does not apply.

- 🔴 **A PR THAT CHANGES A TEKTON PIPELINE CANNOT BE VERIFIED BY THAT PIPELINE — its green check is
  a statement about the OLD leg.** A PipelineRun executes the **deployed Task object in the
  cluster**, never the manifest on the PR branch. Same MERGED ≠ LIVE shape as a stale value after a
  merge, and it was walked into by a session holding that note — because there the symptom was a
  stale VALUE *after*, and here it is a green CHECK *before*, which reads as evidence rather than as
  its absence. **Ask which object the run loaded, not whether the check is green.** The leg that
  DOES run PR content is `gitops-validate`'s `scripts-tests` step, so a test shipped alongside a
  manifest change is really gated while the manifest change is not. **Verify the DEPLOYED artifact
  directly** — `kubectl -n tekton-ci get task <name> -o json`, pull the step's `script`, confirm it
  is byte-identical to what you tested, and run THAT in the step's own image. ⚠ And **a merge may
  trigger nothing at all**, because `clawgate-ci` is path-filtered on `containers/clawgate/**`.
- 🔴 **A FAILED `git worktree add` DOES NOT STOP THE NEXT `git -C <path>` — AND IT LANDED A MERGE
  COMMIT ON ANOTHER SESSION'S BRANCH.** The `add` failed `fatal: … already exists` because another
  session held that path; the very next `git -C <path> merge …` ran happily **inside their worktree**
  and committed onto **their** branch. Silent: no conflict, clean tree, and `git log` afterwards
  shows exactly what you expect, because you are reading the branch you landed on. Recovered via
  `git reflog` + `git reset --keep`. **Two rules: give every scratch worktree a PID-UNIQUE name, and
  branch on `worktree add`'s EXIT CODE before issuing one more `-C` command against that path.** A
  generic `<repo>-integ` is exactly the name another session also picked.
- 🔴 **THE ZSH NO-WORD-SPLITTING TRAP RETURNS A CONFIDENT `0`, AND IT BIT TWICE IN ONE SESSION THAT
  HAD ALREADY READ THE RULE.** `panes=$(tmux list-panes …); for p in $panes` loops **ONCE** on the
  whole newline-joined string, so the per-item command fails and every total is 0. It does not error;
  it reports a clean measurement of nothing. Only a POSITIVE CONTROL caught it — an earlier inline
  `for p in $(…)` had already measured a non-zero total, so a 0 was impossible. Fix: `${=var}`, a
  real array, or pipe to `bash -s`. 🔴 **Never quote a zero from a shell loop you have not
  positive-controlled.**
- 🔴 **A TRUNCATED READ, WRITTEN DOWN, IS INDISTINGUISHABLE FROM A FACT — and it happened TWICE in
  one session, both times in the instrument.** A `jq … | head -2` made a host look like it had no
  clawgate Stop hook registered (it does), and a `print(sorted(d.keys())[:8])` made
  session-manager look like it emits no timestamp — that claim then went into a 🔴 migration
  comment as "measured", and it was wrong by 17 days. **Never slice the output of the command you
  are about to quote** — and when a claim is going into a comment as a measurement, re-run it
  unsliced.
- 🔴 **A BOUND NEEDS BOTH HALVES, AND A FIXTURE DERIVED FROM THE CONSTANT PINS NOTHING.** Asserting
  only what a bound REJECTS let an off-by-one silently narrow it; asserting only what it ADMITS let
  the branch be deleted. Worse, a reject-side fixture built as `strings.Repeat("0", MaxLen)` scales
  WITH the constant — raising it to 1,000,000,000 survived the whole suite while the test allocated
  1 GB fixtures and passed. **Pin a literal value, and pin the constant itself.**
- 🔴 **A GUARD ON A CALL'S PRESENCE IS NOT A GUARD ON ITS EFFECT.** Four successive attempts to pin
  ONE ordering requirement were each walked through: asserting the helper (deleting the call site
  passed), asserting the call exists (dropping the assignment, and moving it below the loop, both
  passed), asserting SOME loop ranges over it (a decoy loop passed), and OR-ing across loops (a
  second unsorted loop passed). What held was quantifying over EVERY write loop and binding the
  check to the loop that actually writes. **Ask what the code must DO, then assert that.**
- 🔴 **A PERIODIC SWEEP'S INTERVAL MUST BE SMALLER THAN THE PROCESS LIFETIME, AND A TICKER'S FIRST
  TICK LANDS ONE WHOLE INTERVAL IN.** A `time.NewTicker(24h)` in a pod that lives 5h fires **never**,
  and every surface reads healthy: the code is correct, the unit tests pass, the leader lease
  acquires, the deploy reports success. A general shape, not a Go detail — any cron/ticker whose
  period exceeds the lifetime of the thing running it is dead code with no error anywhere. 🔴 **Two
  numbers, and neither alone tells you it works:** how STALE a row must be to be taken (the window)
  and how OFTEN anything looks (the sweep). Ask which one your test pins — every test here pinned
  the window.
- 🔴 **A test that calls the pass DIRECTLY cannot see a scheduling defect.** Every existing test of
  this reaper drove `retentionPass` by hand, which is exactly why a reaper that had never executed
  looked fully covered — deleting the `go srv.RunAttentionReap(...)` line from `main.go` left the
  whole module green. The wiring in `main()` needs its own ledger;
  `containers/clawgate/{leader_wiring,background_loops_wiring,version_pin}_test.go` are the in-repo
  convention for source-level pinning of things no behavioural test can reach.
- 🔴 **CADENCE CANNOT BE PINNED ON A WALL CLOCK — measured, both directions.** Asserting "N ticks
  within a fixed sleep" is an assertion about the *scheduler*: `time.Ticker` DROPS ticks under CPU
  contention. Loosening it to "reaches N eventually" then made interval-scaling mutants ALL
  survive; a bound tight enough to catch ×10 is tight enough to flake. There is no good value. Pin
  it **structurally** instead (AST-assert the ticker is driven by the bare `interval` parameter):
  deterministic, zero wall time, and it killed the whole class.
- ⚠ **`DOCKER_HOST=ssh://zach@192.168.50.250` IS HOST-DEPENDENT AND FAILS ON THE WORKBENCH —
  and `deploy.md` still prescribes it.** `192.168.50.250` **is** the workbench, so running it there
  SSHes to itself and dies `ssh_askpass: … No such file or directory` → `Too many authentication
  failures`, which reads like a broken credential rather than a wrong host. From the workbench just
  use the local daemon — the same daemon the ssh transport was reaching for. Check
  `ip -4 addr | grep 192.168.50.250` before believing the error.
- 🔴 **COMMITTING TO `trunk` DEPLOYS THE MANIFEST, NOT THE CONTAINER CODE, AND NOTHING SAYS SO.**
  clawgate's image pin is an immutable literal tag with **no Flux image automation**, so a commit
  under `containers/clawgate/**` reconciles cleanly and **changes nothing running** — seven merged
  commits once sat inert for two days with no outage and no alarm. Shipping is four deliberate steps:
  build, push, bump BOTH pins, commit. **After merging anything here, re-read the pin and
  `clawgatectl health` — `merged` is not evidence, and `git log` is not evidence.**
- 🔴 **BUT THE HOOKS INVERT THAT RULE — they are read from a WORKING TREE.**
  `~/.claude/settings.json` points at
  `/home/zach/workspace/homelab-talos/containers/clawgate/hook/*.sh`, so a plain `git pull` makes
  them live instantly for every Claude Code session on that host, with no switch and nothing gating
  it. **Deploy the server first.** The reverse degrades safely (404 → `exit 0`, no output; there is
  a test) but the feature silently does nothing.
- 🔴 **`clawgate-ci` does NOT run Playwright, but `tekton/clawgate-e2e` DOES — they are different
  checks and an audit once claimed otherwise, which cost a wrong attribution.** `clawgate-ci` is
  Go-only. `clawgate-e2e` runs `make e2e` on the PR's own specs against a REAL Postgres. **Read all
  four checks on a clawgate PR.** ⚠ Locally the trap is the other way: without Docker,
  `test.skip` on `!dockerAvailable()` silently drops a large fraction of the specs and the run goes
  green — **count** what ran.
- 🔴 **`clawgatectl` IS BUILT FROM A LOCAL WORKING TREE OF `homelab-talos`, SO IT CAN BE PRESENT
  BUT STALE — and a stale binary ships MISSING VERBS THAT PRINT HELP AND EXIT 0** under a plausible
  version label. New verbs inherit it. rc 7 covers *binary newer than server*; the dangerous inverse
  has no guard. 🔴 **Nothing converges this and the two halves fail independently** — the tree must
  be current AND a `home-manager switch` must have run, per host; `ship.sh` is scoped to
  `~/workspace/devrc`, and `drift-check.sh` rc 17 REPORTS source currency without fixing it. **So
  any "clawgatectl is current" note — including this doc's — is true only of the moment it was
  written.** Fix, both hosts: `git -C ~/workspace/homelab-talos merge --ff-only origin/trunk`, then
  `ship.sh`. 🔴 **A matching version triple is NOT the check, and neither is a version label** — the
  check is a round trip that MOVES A NUMBER: `view create` on the laptop → `view ls` on the
  workbench → `view rm`. ⚠ The verb is **`ls`**, not `list` (cobra treats an unknown positional as
  help and exits 0), and the version is **`clawgatectl --version`**, never the bare `version`
  subcommand, which errors `unknown command` and reads like a broken client.
- Do not bind `192.168.50.94` — a homelab node; binding it **crash-loops the unit** and already
  cost `initiatives-viewer` an outage.
- Public routing must `proxy_pass` to the NodePort IP `http://192.168.50.250:30302`, never a
  `.svc.cluster.local` name — that doesn't resolve there and **crashes nginx, taking down all
  nebula-routed services**.
- 🔴 **A host's hook registration is NOT uniform — check `readlink -f`, never assume.** One host
  pointed at the repo; the other pointed at a stale private copy. Same feature, same pull, opposite
  outcomes, and the broken one looked healthy.
- 🔴 **BOTH VERSION PINS MUST MOVE TOGETHER — `deployment.yaml` AND
  `cmd/clawgatectl/client.go`'s `buildVersion`.** `TestDeployPinMatchesClientBuildVersion` requires
  it, and bumping one reddens `trunk` for **every PR in the repo** — which is exactly how it was
  found. The deploy runbook's step 3 bumps only the first. ⚠ Watch that test genuinely `=== RUN`
  and `--- PASS`, not merely report `ok`.
- 🔴 **A VERSION-PIN BUMP INSIDE A FEATURE PR CREATES AN `ImagePullBackOff` THE INSTANT IT MERGES.**
  Nothing builds an image on merge, so Flux reconciles a pin with no image behind it — one such
  bump left the cluster stuck for ~80 minutes (the old pod kept serving, so a stuck rollout, not an
  outage). **The pin bump and the image build are ONE operation and must not be split across a
  merge boundary.**
- 🔴 **`! grep -q X f` is INERT under bats errexit** unless it is the last line of a test. A mutant
  restoring an `Authorization: Bearer` header survived a fully green run. Use `refute_grep`, which
  carries its own positive control as a test.
- 🔴 **VALIDATE A TIMER BEFORE QUOTING ANY NUMBER FROM IT — two instruments here silently report
  0 ms for everything.** The Bash tool's shell is ZSH, which has **no `EPOCHREALTIME`**, so
  `${EPOCHREALTIME/./}` expands to EMPTY and every arithmetic timing built on it reads 0 — including
  cases that genuinely take 8 seconds. And busybox `date +%s%N` silently **DROPS `%N`**, so in the
  bats CI image the timer read 0 ms and both fire-and-forget tests passed vacuously **on the tier
  that gates the merge**. Use GNU `date +%s%N` on the NixOS hosts and bash's `EPOCHREALTIME` in the
  bats image — and make `sleep 2` measure ~2000 ms before believing anything. A POSITIVE CONTROL
  exposed both.
- 🔴 **A squash merge NEVER makes the branch head an ancestor**, so `--is-ancestor` reads "not
  merged" forever, blocks cleanup of merged worktrees, and makes `merge --ff-only` refuse forever.
  That refusal is correct, not a fault — make a fresh worktree off `origin/trunk` instead. Verify
  by CONTENT (`gh pr view --json mergedAt,mergeCommit` plus a file diff), never by ancestry.
- **A smoke test can be structurally blind.** The first smoke of a release returned `404` on
  `/api/attention` and looked like a missing feature; `registerAttentionRoutes` returns early with
  no DB. Re-run against a real Postgres.
- 🔴 **THE CSS TOOLCHAIN IS THE SINGLE BIGGEST SOURCE OF MYSTERY REDS HERE — five traps, each
  producing a confident wrong answer.** (a) **`web/static/app.css` IS GITIGNORED**, so a fresh
  worktree reds `TestOpenRoutesNoAuth`, `TestStaticAssetsServed`,
  `TestTheStylesheetCarriesNoNewTestOnlyClasses` and three tests across `internal/ui`+`internal/api`
  for a reason unrelated to any diff — environment, not a defect. (b) **Use the repo-pinned Tailwind
  v3 from `node_modules` (`npm run build:css`), NOT `nix-shell -p tailwindcss`**, which now ships
  v4, ignores `tailwind.config.js` and emits a much smaller sheet with `.h-14` ABSENT — which the
  runbook's own trap-detector then misreads as the cwd trap. (c) **Build from INSIDE
  `containers/clawgate/`** or Tailwind's relative globs resolve against the wrong tree; **the tell
  is the BYTE COUNT, not the grep** — a correct sheet is ~43–46 KB, ~5 KB means the cwd trap fired.
  (d) **`e2e/tests/helpers/server.ts` rebuilds `app.css` ONLY WHEN MISSING — check its SIZE, never
  its existence**, or a stale sheet reds specs the diff cannot reach and makes a CSS mutant read
  SURVIVED because the rule was never generated; a suspiciously fast build is the other tell.
  (e) 🔴 **A REMEMBERED BYTE COUNT CAN FAIL *BECAUSE* THE FEATURE IS PRESENT** — a branch that adds
  classes legitimately differs from trunk's number. **Build your own baseline; never assert a number
  from a doc** (`deploy.md`'s own figure is stale-low).
- **Rejected designs, do not re-propose:** WebSocket-on-Python-stdlib, a homelab-cluster deploy,
  and a new cross-host collector (audit findings A1/A2/A7). The re-platform onto clawgate resolved
  A1 and A2 outright — clawgate already terminates WebSockets and already does SSE.
- 🔴 **`clawgate-stop-hook.sh` sources `~/.claude/clawgate.env` with `set -a`, so THE FILE BEATS THE
  ENVIRONMENT** (`:82`) — the opposite of `clawgatectl`'s documented precedence (file → env →
  flag). Exporting `CLAWGATE_API_URL` to point a probe somewhere harmless therefore does nothing,
  and the probe silently hits **production**: one latency probe meant for an unroutable address
  POSTed to the live server and left a stray `idle` entry in the real queue. Override
  `CLAWGATE_CONF_FILE` instead.
- ⚠ **`192.0.2.1` (RFC 5737 TEST-NET-1) does NOT reliably black-hole** — on the workbench `curl`
  fails it instantly with rc 28 rather than waiting out `--max-time`. For a reproducible "server
  never answers" instrument, run a local listener that accepts and never replies, and validate it
  (a plain `curl --max-time 8` against it must take ~8000 ms) before trusting a hook measurement.
- 🔴 **THE SHARED devrc CHECKOUT MOVES UNDER YOU — RE-VERIFY A REPO'S BRANCH AND DIVERGENCE STATE
  AT THE MOMENT YOU ACT ON IT, NOT FROM AN EARLIER SURVEY.** Measured twice: a
  `git reset --keep origin/main` recovery was proposed for a diverged `main`, and by the time it was
  approved another session had switched that checkout to a feature branch — the command would have
  reset *that* branch and destroyed its pointer (and the divergence had already been fixed by its
  owner). Separately, the clone was found on another session's branch with their uncommitted files in
  the tree, where a `handoff_doc.py --confirm` would have committed onto THEIR branch — no conflict,
  no error, and `git log` afterwards shows exactly what you expect. One `git branch --show-current`
  catches the whole class. **Author the handoff from a worktree cut off `origin/main`**, which also
  sidesteps the `stale-base` refusal a behind-checkout triggers.
- 🔴 **AN AUDIT SUBAGENT'S LOAD GENERATORS LEAK — treat it as a property of the briefing, not as
  bad luck.** Twice in one session a stress test spawned busy-loop shells whose job-control cleanup
  failed to reap them; they reparented to init and saturated most of the box for **45 minutes**, then
  **6h17m**. The first batch corrupted the very timing measurements the NEXT audit round then
  reported, so the cost is not just CPU: **it silently poisons the evidence.** 🔴 Two agents reported
  "no load generators spawned"/"all cleaned up" while these were running — **a subagent's own cleanup
  claim is not evidence.** **Sweep for them yourself at session end**: `ps -eo pid,ppid,comm` for
  `ppid==1` zsh, confirm each via `/proc/<pid>/cmdline`, kill by RESOLVED pid, never let a pattern
  reach `pkill -f`. The durable fix lives in the **`audit-pr` skill's briefing**.
- ⚠ **`gh pr view --json mergeable` answers `UNKNOWN` for a while after a push** — GitHub is still
  computing it. It is not a conflict; re-read it a few seconds later before acting.
- 🔴 **A MUTATION CAN REPORT `killed` AGAINST A TEST THAT WAS ALREADY RED — it dies for free, and
  the sweep looks clean.** A test was added and the sweep run immediately after; the mutant "died"
  because the test was failing *with and without* it, and the shipped pattern had never excluded
  the case at all. The missing control is the cheap one — **run the new test UNMUTATED first and
  watch it pass** — and a sweep's own `M0` control does not cover it if `M0` exercises a
  *different* test. **Report the pair (green baseline, red mutant) or the kill means nothing.**
- 🔴 **A GREEN MUTATION SWEEP IS A CLAIM ABOUT THE MUTATIONS YOU IMAGINED, AND MINE OMITTED THE
  WHOLE DIAGNOSTIC SURFACE.** An auditor mutated `COLLECT_RC=$?` to a literal `0` and it SURVIVED a
  fully green file, because every test asserted *that* a failure was reported and none asserted the
  *number*. The shipped code was byte-equivalent to an undetectable mutant. Across this work the
  sweeps totalled 24/24 killed and still found **less than the adversarial reader did**.
- 🔴 **`if ! cmd; then rc=$?` IS ALWAYS 0** — it reads the status of the *negated* pipeline, which
  is 0 exactly when the branch is taken. Worst on the likeliest path: `timeout` gives status 124
  *and* empty stderr, so the log line carries no information whatsoever. Capture outside the
  condition (`set +e; cmd; rc=$?; set -e`).
- 🔴 **`HTTP < 400` IS NOT SUCCESS.** Without `-L`, a 3xx means curl returns the redirect and the
  server stores nothing — while the script logs "pushed" and exits 0. `000` and an EMPTY status do
  the same: `[ "" -ge 400 ]` prints "integer expected" and is FALSE, and **`set -e` does not fire
  for a test used as an `if` condition** (measured). Match 2xx explicitly.
- 🔴 **`env -i` IS STRICTER THAN systemd, so a probe built on it measures a condition the unit never
  runs in.** `Environment=` **ADDS to** the user-manager environment rather than replacing it. A
  live `TMUX_TMPDIR` bug was diagnosed this way and the diagnosis was wrong; what exposed it was
  corroborating evidence disagreeing with the conclusion, not a re-check. Use
  `systemd-run --user --wait --collect --pipe` to observe the real thing. ⚠ systemd expands
  `${VAR-DEFAULT}` in `ExecStart` itself and does not understand the `-` default, which yields
  false "unset" readings.
- 🔴 **TRIMMING A COPIED PATH LIST WITHOUT RUNNING THE CHILD IS HOW A SILENT ZERO GETS BORN.**
  Dropping `gawk` as "unused" broke the agent ledger: `agent_ledger.read_command` runs `awk 1`,
  `awk` lives ONLY in gawk, and its `2>/dev/null; exit 0` swallows the error while the `echo`
  sentinel still prints — so the parser sees a well-formed ledger reporting ZERO. rc 0 and a
  plausible payload either way, and one host's leg was unaffected, so the result looked *correct*.
  The difference was only ever discoverable by running the collector without each entry.
- 🔴 **A PURE PROSE CHANGE CAN RED THE MERGE GATE, AND A GUARD CAN BE TRIPPED BY PROSE THAT
  DOCUMENTS IT.** `testlib/launcher_scan.py` scans top-level `scripts/` files as RAW TEXT with **no
  comment stripping**, so merely *naming* a hazardous binary in a comment registers that script as
  reaching it — one explanatory comment put `test_no_real_launchers.py` red on both tiers, and three
  reds in one day on `main` came from handoff docs, one of them a doc whose only content was writing
  up the guard. **When a check scans every tracked file for a *word*, every future write-up about it
  is a future red:** scope such a check to text that can actually execute, and let a shape-aware
  check cover prose.
- 🔴 **DO NOT RUN AN AUDIT AGENT AND THE GATING SANDBOX AT THE SAME TIME.**
  `test_live_cotenants_sees_another_process_in_the_repo` asserts a fresh tmp repo has no tenants; a
  concurrent agent running `git` in the repo reds it. I contaminated my own gate this way and it
  passed cleanly once the box was quiet — the "round N's leak corrupts round N+1's evidence" shape,
  applied to the gate instead of a timing probe.
- ⚠ **THE ZSH `MULTIOS` TRAP BIT AGAIN, in a session that had already read the rule.**
  `cmd 2>&1 >/dev/null | head` delivers **stdout**, not stderr, so a "no skew warning" reading was
  of the wrong stream. Give each stream its own file and read both.
- ⚠ **THE TMUX SNAPSHOT'S STALENESS SIGNAL IS RENDERED BUT IS NOT AN ALARM — a design that names it
  as a compensating control is naming a control that does not exist.** The Tmux tab reads
  `GET /api/tmux/snapshot` and renders `receivedAt` as a per-host age badge that turns red past
  6 minutes, but nothing pages anyone: a human has to look at the tab. 🔴 And a MERGED reader is not
  a RUNNING one — with no Flux image automation, the badge is only live once the pin moves. The
  feeder's real alarm is its distinct non-zero exit codes landing in the user manager's failed-unit
  list, which `/standup` reads — that covers the codes and **NOT** a run that exits 0 having achieved
  nothing, which is why the redirect and unmeasured-zero cases had to be fixed in the script.
- ⚠ **The pusher's real per-run cost is ~4x the obvious estimate:** `session-manager --json` makes
  up to FOUR ssh invocations per remote host (list-panes, list-windows, the capture batch, the
  ledger read) with no `ControlMaster`, plus one ClickHouse query per run. `--lean` is the lever if
  that ever matters, at the cost of the verbatim/dumb-pipe property.
- 🔴 **A `pgrep`/`grep -f` ON A PROCESS NAME MATCHES YOUR OWN COMMAND LINE — and both failure modes
  have fired here.** As a WAIT LOOP it never exits: two `until … ! pgrep -f "<pattern>"` loops spun
  for **45 and 62 minutes**, because the pattern appeared in the loop's own `/proc/<pid>/cmdline` and
  the condition can never go false. As a COUNT it invents processes: a phantom "2 live playwright
  processes" was my own grep, and it is the only thing that stopped five orphaned containers being
  cleaned for hours. The rule is in `RULES.md` and was walked into anyway. **Resolve PIDs and compare
  `/proc/<pid>/cwd`, wait on a FILE (`until grep -q DONE "$log"`), or use a real ownership signal —
  never a name pattern.**
- 🔴 **A WORD-COUNT OVER RENDERED HTML IS NOT A MEASUREMENT OF STATE — pane CONTENT can spell the
  word.** Grepping the live `/ui/tmux` page for `truncated` returned 3 and was reported as "3 panes
  hit the truncation path"; the read model's own status field said **0**, and the three hits were
  the word appearing inside CAPTURED PANE TEXT. **Read the STATUS FIELD, never a word count over a
  page that embeds arbitrary text.**
- ⚠ **`test_subsystem_store_api.py` IS A LIVE FLAKE UNDER LOAD and once blocked EVERY devrc PR,
  including its own fix.** Four different tests failed across three PRs; each passed 5–6/6 in
  isolation on clean `origin/main`. 🔴 **The discriminator that settled it was a DOCS-ONLY PR
  failing** — a one-markdown-file diff cannot break a store-api test. The mechanism is DIAGNOSED as
  fsync contention (see `### ✅ DIAGNOSED` above and `scripts/ci-repro/README.md`); the earlier
  "fixed by devrc#996" claim **IS FALSE — it recurred.** 🔴 **Do not re-run and move on — run the
  discriminating control:** the full target on a clean `origin/main` worktree, plus a check of
  whether UNRELATED targets' wall times also moved. Load inflates EVERY test in a run; a failed
  assertion inflates exactly one.
- ⚠ **The pane-preview ratio is NOT a constant.** It moves with pane count and screen fullness.
  Quote the cap headroom rather than a multiplier.
- 🔴 **A MUTATION SWEEP'S KILL-ATTRIBUTION PARSER IS ITSELF AN INSTRUMENT, AND MINE WAS WRONG ON THE
  FIRST RUN.** `--- FAIL: TestFoo (0.00s)` split on whitespace puts the literal **`FAIL:`** at index
  1 and the NAME at index 2, so the sweep printed `by: FAIL:` for all 15 mutants and still reported a
  confident 15/15 killed — the count was true and the *attribution said nothing*, making "killed by
  an unrelated test" and "killed by the guard I wrote" indistinguishable. **A sweep that cannot name
  its killer cannot tell you the guard is reachable.** Fix the parser and **re-run before quoting the
  number**; and make a mutant whose patch matches ≠1 time report **INVALID**, never SURVIVED.
- 🔴 **A PIPE EATS AN EXIT STATUS, AND IT HAS DONE SO SEVEN SEPARATE TIMES HERE — twice while the
  rule was on screen, and once mid-audit-round.** Each reads as a clean answer, which is why knowing
  the rule is not sufficient. Measured across `go test … | grep -v`, `ssh … | tail`,
  `<cmd> | head -3`, `gate.sh … | tail`, `gh pr merge … | tail` and a background `pytest … | tail`:
  every one reported success or `0` over a real failure, once printing `GATE_RC=0` directly beneath
  the runner's own `GATE: RESULT=FAIL exit=1` and once `pipeline_rc=0` over a red pipeline. **Capture first (`out=$(cmd 2>&1); rc=$?`), then print — and read the
  runner's own `RESULT:` line, never the piped code.** Take the count and the rc **on the far side**
  of an ssh. **Verify a merge by CONTENT** (`gh pr view --json state,mergedAt,mergeCommit` plus
  `git show origin/main:<path>` / `git cat-file -e`), never by the piped status.
- ⚠ **A SUBSTRING LEAK-CHECK NEEDS A FIXTURE THAT CANNOT OCCUR IN THE MESSAGE.** A test asserting
  "the boot log never prints the secret" used the fixture token `"short"` and failed — because the
  refusal reason contains the word **short**er. `"placeholder"` fails the same way. Pick a nonsense
  fixture, and note the failure was the instrument, not the code.
- 🔴 **A GUARD THAT SCANS ROUTE STRINGS CANNOT SEE WHAT A HANDLER DOES, AND ITS OWN POSITIVE CONTROL
  WILL TELL YOU IT WORKS.** A test scanned `mux.HandleFunc` PATTERNS and carried a control proving
  the string scan fired. Planting `exec.CommandContext(…, "tmux", "send-keys", …)` in the
  unauthenticated handler — applied once, compiling, no route string touched — left the ENTIRE suite
  green, that test included. The docstring named a property the body never checked. Fixed by
  AST-scanning handlers with comments discarded. **Ask what the code must DO, then check the guard
  inspects that, not its neighbour.**
- 🔴 **A LEDGER ENTRY THAT JUSTIFIES AN EXEMPTION IS THE HIGHEST-VALUE PLACE FOR A FALSE CLAIM.**
  `allowedNonTerminalWrites` said view-scoping stopped "an unauthenticated LAN client walking 1..N
  and emptying every view". `GET /ui/layout` is on the pass-through tier and renders every panel's
  write address, so one anonymous GET hands over the whole arrangement — measured: 9 addresses
  harvested, 9/9 panels destroyed, all 200. The scoping raised a full wipe from P requests to 1+P.
  **The fix was structural, not documentary:** the irreversible control was removed, so the
  sentence that needed justifying no longer exists.
- 🔴 **A PATCH THAT DOES NOT APPLY EXACTLY ONCE, OR DOES NOT COMPILE, IS INVALID — never a kill and
  never a survivor.** Three of my own mutants were invalid and one almost read as a pass: two failed
  to compile and one had an anchor that matched 0 times, each printing a green suite that looks
  exactly like a survivor. The compile error is the subtle one — dropping a condition removed the
  last use of an import, so the package stopped building; re-cut as a one-operator flip (`!=` → `==`)
  it keeps the import referenced and produces the same behavioural bug. **Assert the match count
  before applying, and check the build before scoring.**
- 🔴 **A `-run` FILTER CAN EXCLUDE THE ONLY TEST THAT WOULD HAVE CAUGHT YOU — TWICE IN ONE SESSION**,
  and **`rc=0` FROM A FILTERED GO TEST IS NOT "IT PASSED"**, because a filter matching NOTHING also
  exits 0. `--- PASS` lines do not print without `-v`, so counting them reads 0 for a perfectly good
  run. **Run the whole package before concluding a guard has a gap**, pair every filtered rc 0 with a
  positive control on the same filter, and check for `no tests to run`.
- 🔴 **A THEORY THAT EXPLAINS THE FAILURE IS NOT EVIDENCE FOR IT.** Repeated CI failures were
  attributed to cluster load with real numbers — 22.7x median wall-time inflation across packages
  with ZERO failures, 12 concurrent PipelineRuns, nodes at 51–62%. All of it was true and it was
  the WRONG MECHANISM: everything went green the moment an advisory-lock fix became an ancestor,
  and every symptom had been lock-shaped all along. **Change one variable and re-measure before
  calling a cause.**
- 🔴 **A BARE PR NUMBER IS AMBIGUOUS ACROSS REPOS AND THE WRONG ONE RESOLVES SILENTLY —
  `audit-dispatch.py <n>` RESOLVES AGAINST THE CWD'S REPO AND WILL SILENTLY AUDIT THE WRONG PR.**
  Run from `devrc`, it assembled a full brief for an unrelated devrc PR whose WHERE TO WORK section
  asserted the PR lived in devrc and prescribed `isolation: "worktree"`; dispatched, it would have
  audited the wrong change confidently. Caught only by reading the generated brief's TITLE. **Always
  pass `--repo owner/name` for a cross-repo PR** — with it the brief correctly flags CROSS-REPO and
  forbids the isolation flag.
- 🔴 **`gofmt -w <dir>` REFORMATS PRE-EXISTING FILES AND SILENTLY WIDENS YOUR DIFF** — seven files
  never touched appeared in one change set as pure alignment churn. **Format only the files you
  edited.** ⚠ And a repo-wide `gofmt -l` says nothing about whether YOUR change left a file dirty,
  because the baseline is **per-file**: this package has many pre-existing unformatted files on
  trunk, while the one file under edit was clean at base. Check the file's own base state
  (`git show origin/trunk:<path> | gofmt -l /dev/stdin`) before deciding a listing is pre-existing.
- ⚠ **An image built during review can silently omit a fix that landed on trunk mid-review.** Two
  images were discarded for exactly this: trunk gained a `containers/clawgate` commit while the PR
  sat, so the image would have carried a HIGHER version with LESS code. **Before pushing a pin,
  check `git log HEAD..origin/trunk -- containers/clawgate` and rebuild if it is non-empty.** Never
  re-push a mutable tag — mint a new one.
- ⚠ **`docker push` sends registry auth from the LOCAL client, not the `DOCKER_HOST` daemon** — so
  a push can fail `unauthorized` because of the *client's* `~/.docker/config.json`. ⚠ **The
  runbook's claim that the workbench has no `harbor.homelab.lan` entry is STALE**: measured, the
  workbench's `auths` carries it and `docker push` from there succeeds rc 0, so the laptop-push
  workaround is no longer required for that reason. `docker manifest inspect` still fails on the
  self-signed CA — use `docker pull` to confirm Harbor serves a tag.
- 🔴 **Two durable measurements that CONSTRAIN any future read-model work:**
  - **SCROLLBACK IS EXCLUDED ON A HARD BOUND.** At ~4 KB per line fleet-wide, `-S -1000` computes
    well past `maxTmuxPushBytes` (4 MB) — the cap breaches at **~650 lines/pane**. Visible screen
    only; `tail` serves history one window at a time.
  - **ONLY ~22% OF PANES CHANGE PER TICK**, so ~77% of the bytes are resent unchanged. In absolute
    terms that is cheap — **the objection is STALENESS, not bandwidth.** That is the standing
    argument for an on-demand path, and the reason one was not built.
- 🔴 **`ship.sh` rc 11 CAN BE A RACE, NOT A FAILURE** — `origin/main` moved between the two legs, so
  the local leg verified against a sha that had already advanced. Both hosts landed the same commit
  and the immediate re-run was rc 0 with agreement COMPARED. **Re-run before diagnosing an rc 11 —
  but still read the per-host lines, because a genuine skip hides among greens.**
- ⚠ **`ship.sh` reports `DIRTY AND IN THE ARTIFACT` and it is not cosmetic.** It classifies dirty
  paths against the set nix actually READS at eval/build time, so a host can be byte-identical to
  `origin/main` by SHA while the generation it just built is `origin/main` PLUS someone's
  uncommitted WIP. Cross-host "both at <sha>" is then true and still not host parity.
- 🔴 **A CLEAN REBASE IS NOT A CLEAN MERGE — but check, don't assume, in BOTH directions.** One
  rebase reported no conflict over a real semantic overlap: `main` had replaced a ledger's permissive
  `assert field in sm.__doc__` with a **SET** comparison failing in BOTH directions, while the branch
  adds fields; it was fine ONLY because the new field lands on the **host** dict, not the row dict.
  The next rebase of the same branch had **zero** commits touching either file.
  `git log <base>..<tip> -- <each file the branch touches>`, then reading the merged region, tells
  them apart in one command; the green suite alone would not have.
- 🔴 **A Tekton check posted as `ERROR` is a broken gate, not a bad change — and only a fresh push
  clears it.** A PR sat at `ERROR`/`ERROR`, `mergeable: UNKNOWN`, no `targetUrl`; rebase +
  force-push moved it to `pending` + `MERGEABLE`. **Rebasing beats an empty commit here**:
  afterwards the branch head IS the merged tree, so the gate run is a statement about what merging
  produces.
- 🔴 **An e2e fixture can make a guard vacuous in a way that reads exactly like a product bug.** A
  control-set assertion (`['archived','collapsed','expanded']`) failed with `expanded` missing —
  not a bug: an **expanded** card offers Collapse + Archive and no Expand at all, and Restore lives
  only on the **archived** card. Seeded all-expanded — the obvious fixture — the union is
  `{archived, collapsed}`. Fix: one panel in EACH state.
- 🔴 **A panel that does not RESOLVE renders an EMPTY detail block, so "collapsed hides the detail"
  would have been true of nothing.** `layoutPanelDetail`'s `default` arm returns `g.Text("")`, so an
  `unreported` panel draws the same body expanded or collapsed — the spec therefore pushes a real
  snapshot via `POST /api/tmux/snapshot` first. ⚠ That body is **session-manager's own `--json`
  document** — a `hosts` OBJECT keyed by host name,
  `{"ts":…,"hosts":{"<h>":{"reachable":true,"windows":[…]}}}` — NOT a list of snapshots. And the
  window keys are **mixed case**: `codename` and `tmuxSessionName` camel, `window_id` /
  `window_name` / `window_index` / `claude_session_id` / `hotkey_display` snake.
- **Assert the ATTRIBUTE, not the text.** A mutant that re-derived `data-hotkey-display` through
  `strings.ToLower` left the visible text `Alt+i` while the attribute became `alt+i`. A text
  assertion survives it; the attribute assertion killed it.
- **A worktree of `homelab-talos` needs `e2e/node_modules` linked.** `package-lock.json` is
  **gitignored** there, so a fresh worktree has none and a naive `cmp` against the base clone
  reports DIFFERS when the file is simply ABSENT.
  `ln -sfn <base>/containers/clawgate/e2e/node_modules` suffices — `ensure-node-modules.sh` checks
  the declared deps, not the directory.
- 🔴 **A FAILED FULL-MODE e2e FIXTURE LEAKS ITS `clawgate-e2e-pg-*` CONTAINER, AND IT IS
  SELF-REINFORCING** — leaked containers starve
  `startPostgres`'s 30 s readiness deadline until *every* full-mode spec fails in setup, and each
  timeout leaks another; 18 had accumulated before clearing them produced the first complete run.
  Sweep them by exact name. ⚠ `docker ps` **omits `created`-state containers** — use `-a`, or a
  "0 containers" reading is a claim about the view, not the box.
- ⚠ **`core.hooksPath` was set REPO-LOCALLY on `homelab-talos`** at push time. It is the documented
  volatile value: **re-measure at the moment you push**, and verify the branch afterwards (local
  HEAD == `ls-remote`, commit count) rather than trusting the push message.
- **Branch protection on devrc `main` strands a `handoff_doc.py --push`.** Recovery order is
  preserve → **verify on origin** → `reset --keep`: branch the topic, push it, confirm the sha with
  `git ls-remote`, and only then move `main`.
- 🔴 **SIX AUDIT ROUNDS ON ONE TEST FILE, AND FIVE OF THEM FOUND THAT THE PREVIOUS ROUND'S FIX HAD
  CREATED THE NEXT DEFECT.** One class, five times: **a claim wider than the code** — a comment or a
  test NAME asserting coverage the assertion does not deliver. The three shapes: a "simplification"
  that DELETED a mutant (replacing `count(A)===count(B)` with a one-directional scan was called
  "subsumes the count check"; it did not, and a crafted anchor then passed all three tiers — caught
  by ONE tier before the "improvement", ZERO after); **erasing an assertion's own observable** (a
  click followed by an unconditional `d.open = true` then `expect(open).toBe(true)` — reading back
  the value just written; **assert the FLIP**, `!openBefore`); and **widening one axis while
  narrowing another** (moving a guard into a new test also moved it onto a two-state fixture,
  silently dropping an arm).
- 🔴 **A guard on a WORD is walkable by a five-character rename, and enumerating spellings does not
  fix it — it relocates it.** `[hx-delete]` missed `data-hx-delete`; widening to both then missed
  `data-hx-post`; `[onclick]` missed `onmousedown` AND an `hx-on:click` **`<div>`**. The fix that
  held was to stop listing: **scan by SHAPE** (interactive tag names + any attribute matched by
  PREFIX). Two enumerations in a row failed before that landed.
- 🔴 **A mutation harness that restores only in a `finally` is NOT SIGKILL-safe, and the Bash tool
  caps at 10 minutes** however long a timeout you request. A battery was killed mid-mutation and
  left `internal/ui/layout.go` MODIFIED in the worktree. **Check the tree after any killed run
  before trusting anything downstream of it**, and run long batteries with `run_in_background`.
- 🔴 **AN ABSENT CHECK AND A NOT-YET-SCHEDULED CHECK ARE BYTE-IDENTICAL IN `gh pr checks`, AND THE
  WAIT IS UP TO ~15 MINUTES.** Filed as an open investigation once, minutes after opening a PR; it
  had registered and later passed — and it recurred twice more. Naming one mechanism without naming
  the rival is the empty-result trap. **Assert a MINIMUM CHECK COUNT rather than reading an empty
  rollup as "no CI"**: 4 for homelab-infra, 3 for devrc (`devrc-pytests`, `devrc-nodetests`,
  `devrc-cairn-client-runs`). ⚠ `gh pr checks` exits **8** while checks are pending, so `rc != 0` is
  not an error either.
- ⚠ **A FRESH `nix-build` may give a playwright-driver whose chromium revision does not match the
  pinned `@playwright/test`**, and `make e2e` then dies at browser launch. An audit round concluded
  from this that the suite CANNOT run on this host; it can — `playwright.config.ts` sets
  `executablePath` explicitly from the bundle. The skew is real and worth its own fix, but it is not
  a property of the repo.
- 🔴 **A FLOOR OR A PINNED CONSTANT MAY HAVE A SECOND CALL SITE THAT BOUNDS IT — grep the NAME
  across the repo before changing the value.** One such grep spanned four files: two false hits and
  one real second site. Changing one site alone leaves a guard asserting a stale reality.
- 🔴 **RE-DERIVE A PINNED COUNT FROM THE SOURCE, AND DO NOT TRUST A PER-FILE TALLY.**
  `routing.spec.ts` **collects 5 tests but only 4 are the no-DB ones** — it splits an unguarded
  describe from one behind `guardFullMode()`. A count of collected-tests-per-file silently raises a
  floor's lower bound. ⚠ My first attempt at that tally also hit the **zsh no-word-splitting** trap
  and returned a plausible number that happened to be correct by luck. Re-derive in Python.
- 🔴 **PROVE A BOUND FIRES BEFORE TRUSTING IT — one here was MEASURED INERT.**
  `test_clawgate_e2e_verdict.py` records an audit that set `MIN_PASSED=2` / `MAX_SKIPPED=1000` and
  watched **every case pass**. Verify by mutating the MANIFEST (what a person reaches for when the
  gate reds), never the test, and check **each bound fires its OWN named test**.
- 🔴 **MERGED ≠ LIVE for anything Flux reconciles — and a mid-cascade snapshot looks exactly like a
  wedged one.** After a merge the LIVE Task still carried the old value, because a PipelineRun
  executes the DEPLOYED object. Worse, the first read of the Flux chain showed `tekton-operator`
  "Reconciliation in progress" with two dependants blocked — which reads as a broken GitOps delivery
  path, and one minute later it was Ready: a normal cascade caught in flight. **Re-read a dependency
  chain before reporting it blocked.** ⚠ Also: the floor is NOT in the TriggerTemplate — the chain is
  TriggerTemplate → `pipelineRef` → **Task `clawgate-e2e`**, and a grep for `MIN_PASSED` across all
  TriggerTemplates returns **0**, which is a FAILING POSITIVE CONTROL, not a clean zero.
- 🔴 **A SUBAGENT'S MUTATION TABLE IS A CLAIM ABOUT A RUN NOBODY WATCHED — and re-running ONE row is
  enough to catch it.** One arrived with an 8-row mutant→message table, a stated INVALID mutant (a
  good sign) and a barrier-removal control; re-running a single row showed the mutant dies by
  **panic one line above the barrier**, so neither the barrier nor the assertion is reached and the
  quoted `t.Fatalf` message cannot have been emitted. The tell was cheap and structural: **read the
  mutated code for a nil deref between the guard you removed and the call you expect to fire.**
- 🔴 **A CROSS-REPO WORKTREE MUST NOT BE MADE WITH `isolation: "worktree"`.** That flag worktrees
  the CWD's repo, not the repo the task names, and the quiet failure mode is the worse one: the
  agent silently works in the wrong tree and your model of where the work happened is wrong. Create
  it yourself with `git -C <target-repo> worktree add <PID-unique-path> -b <branch> origin/trunk`,
  check the exit code, and hand the agent the path.
- ⚠ **`homelab-talos`'s `.envrc` CANNOT be copied verbatim into a worktree, and it is TRACKED.** It
  renders SOPS secrets from `.secrets/…` that a fresh worktree does not have, so every `cd` into it
  errors; the recipe's "copy `.envrc`, drop the credential lines" means writing a one-line
  `use flake` and `direnv allow`ing that. Because the file is tracked, that shows up as a modified
  file — harmless until you stage it. `git -C <wt> checkout -- .envrc` before committing, and check
  `git status --porcelain` shows only the file you meant.
- ⚠ **`clawgate_handoff.sh resolve` exit 5 is not "no task" — it is "cannot distinguish".** A wrong
  `CLAUDE_CODE_SESSION_ID` answers 200 with an empty array exactly like a session that touched
  nothing. Its positive control (another session's links resolving) proves only that a CORRECT id
  WOULD have resolved.
- 🔴 **A MUTATION RUNNER THAT PRINTS "THE FIRST `file.go:NNN:` MATCH" CANNOT TELL A KILL FROM A
  DEATH UPSTREAM — and its output looks identical either way.** For a real kill that line is the
  `t.Fatalf`; for a panicking mutant it is a **stack frame**, equally plausible-looking. The sweep
  therefore could not have been evidence for the claim it was used to make, whatever the result.
  **A sweep must CLASSIFY each red — assertion / panic / compile error — and say which**; a bare
  "died" is not a kill.
- 🔴 **UNDER-REPORTING A MULTI-HUNK MUTANT IS INDISTINGUISHABLE FROM FABRICATING THE RESULT, and the
  re-runner will measure a DIFFERENT mutant.** A two-hunk patch was reported as one; re-running the
  named hunk alone produced a panic — a true measurement of a mutant nobody had run, which read as
  "the reported evidence is false". Both parties were right about their own patch. **State every
  hunk of a mutant verbatim**, and when re-verifying someone else's, ask for the literal patch
  before concluding their number was wrong.
- 🔴 **AN EMPTY COMMIT IS THE WRONG RE-TRIGGER IN THIS REPO — IT MANUFACTURES A FALSE GREEN, AND
  THE NON-RESULT IS INDISTINGUISHABLE FROM A BROKEN TRIGGER.** `clawgate-ci-pipeline.yaml:13-14`
  filters pushes on `containers/clawgate/**`, so a zero-file commit matches nothing and
  `clawgate-ci`, `clawgate-e2e` and `ux-audit-clawgate` never fire — while `gitops-validate`
  registers anyway, because its `ci-changed-paths.py` reads an absent path list as "unknown → run".
  Result: one check passes, `mergeStateStatus` reads **CLEAN**, and the PR looks mergeable with three
  quarters of the gate unrun; `gh pr checks` meanwhile says `no checks reported`. **To re-trigger,
  the commit must touch a filtered path** — amending the real commit does it without changing a byte
  — or re-run the PipelineRun from its own spec.
- 🔴 **TEKTON MARKS EVERY UNFINISHED STEP `TaskRunTimeout` AT ONCE, SO A CHECK'S TEXT AND ITS
  PIPELINERUN CAN DISAGREE AND THE STEP LIST ALONE DOES NOT NAME THE CULPRIT — read `exitCode`, not
  just `reason`.** `go`, `extension`, `hook` and `verdict` all appear failed whether the pod ran 25
  minutes or never started. The discriminator is whether the EARLIER steps carry `exit=0 Completed`
  (ran, then blew the budget) or `exit=None` (never executed). Reading only the reason makes a
  scheduling failure look like a slow suite. `kubectl -n tekton-ci get taskruns -l
  tekton.dev/pipelineRun=<run> -o json`, then the per-step `terminated.reason` — **which step
  consumed the budget is the whole diagnosis**, and it is what tells you whether your diff could
  possibly be responsible.
- ⚠ **The `bash-guard.py` PreToolUse hook parses HEREDOC BODIES as real commands**, so a gotchas
  block or a commit message quoting a banned command is itself blocked. Write prose with the Write
  tool and pass it by file (`git commit -F <file>`, `gh pr create --body-file <file>`), which the
  RULES prefer anyway.
- ⚠ **A capacity-starved or flaked PipelineRun verdict is RECOVERABLE — re-run the spec, do not
  push.** `kubectl -n tekton-ci get pipelinerun <run> -o json`, strip
  `metadata.name`/`uid`/`resourceVersion`/`creationTimestamp`/`status`, set `generateName`,
  `kubectl create`. **Assert the `revision` param in the script** rather than trusting the capture.
  This re-reports the GitHub status for the same revision without a commit, which matters on trunk
  where a push means a deploy.
- 🔴 **A "CANNOT DO X" NOTE IN A HANDOFF IS A CLAIM WITH A SHELF LIFE, AND OBEYING IT COSTS NOTHING
  VISIBLE — WHICH IS WHY IT NEVER GETS RE-TESTED.** A session read "the workbench cannot pull
  docker.io", routed a container run to the laptop, and the detour WORKED — so nothing signalled that
  the premise was stale. **A false "cannot" is self-preserving in a way a false "can" is not:** the
  latter fails loudly on first use, the former quietly buys a workaround forever. **When a doc tells
  you a capability is missing, spend the one command to check before building around it** — and when
  the check passes, ask whether the underlying cause is fixed or merely masked before rewriting the
  note.
- 🔴 **"FIXED BY #N" IS A CLAIM WITH A SHELF LIFE, AND A FLAKE IS THE WORST PLACE TO WRITE ONE.**
  This doc recorded a store-api flake as fixed and it recurred — on a one-markdown-file PR, on a
  case name the doc had already written down. A flake marked fixed reads as "your diff broke it",
  the most expensive possible wrong reading, because the person who hits it has least reason to
  doubt it. **When closing a flake, record what was MEASURED rather than the verdict** — prefer
  "N consecutive green runs in the failing tier" to "fixed", because the first is falsifiable by
  the next red and the second silently absorbs it.
- 🔴 **A `hx-trigger` CURLED DIRECTLY FROM A FRAGMENT ENDPOINT CANNOT SHOW ITS REFRESH BEHAVIOUR —
  the PARENT drives it.** `GET /ui/tmux` and `GET /ui/layout` are htmx FRAGMENTS; the SPA shell
  (`GET /tasks`, `/tmux`, `/layout` — all `handleIndex`) mounts them as
  `<div id="panel-tmux" hx-get="/ui/tmux" hx-trigger="load, every 60s, …">`. Curling the fragment
  shows no `hx-trigger` and reads as "this tab has no auto-refresh", which is how one session nearly
  dispatched work to add polling that existed all along. **Fetch the page a human loads, not the
  endpoint it calls.**
- 🔴 **A ROLLOUT CHECK THAT ASKS "IS ANY POD ON THE NEW VERSION" PASSES MID-ROLLOUT**, and the
  sibling trap runs the other way: **after any deploy, check ALL pods, not `.items[0]`** — a
  `Succeeded` leftover in the list makes a `.items[0]` jsonpath report the wrong image. The
  condition must be "every Running pod is the new image AND none is the old", and the pod must be
  `Running` **and ready**.
- 🔴 **A SCANNER THAT READS THE FILE IT LIVES IN CANNOT SPELL ITS OWN PATTERN — and the failure is
  a SILENT TRUNCATION, not an error.** An awk heredoc-tracking rule matched ITSELF: the scanner
  opened a heredoc on its own source line and skipped to EOF. Every downstream number stayed
  plausible — it simply reported 33 test bodies where grep counted 35 — and `EXAMINED > 0` was still
  true; only a `BODIES` equality cross-check caught it. Both the REGEX and the PROSE describing it
  manifested it, and the prose clause SURVIVED a green mutation run until a fixture body was written
  for it. **Any self-referential text scanner needs its own literals assembled (in `BEGIN`), and a
  cross-check that fails DIFFERENTLY is what makes the truncation visible.**
- 🔴 **A POLL LOOP THAT CANNOT PARSE ITS STATUS EXITS IMMEDIATELY AND READS AS "CONCLUDED".** A
  wait-for-CI loop embedded a python one-liner in zsh, hit a quoting `SyntaxError`, captured an
  EMPTY string, and the `case "$s" in *pending*)` guard therefore did not match — so it broke out
  after one iteration and the background task reported success having measured nothing. The
  notification said "completed"; the run had not even started. **Never let an unreadable status
  share a branch with a concluded one** — test emptiness and non-zero rc explicitly, and say
  "still running" for both.
- 🔴 **THE STALE-DOC DRIFT THIS FILE KEEPS RE-CREATING HAS FIRED IN FOUR SHAPES, AND EVERY ONE IS
  SILENT IN BOTH DIRECTIONS.** The working copy has been 103 lines behind, 124 behind, 2 commits
  behind — and once byte-**identical** to `origin/main` and STALE anyway, because the real update
  sat in an unmerged PR for hours with both checks green. **Three rules, and the identical case
  needs the third:** read the copy `resume-state.sh` names — the `handoff-read:` line, BEFORE
  opening the file, because the two copies are byte-plausible either way and a disagreeing kickoff
  message is the only other tell; author from a worktree cut fresh off `origin/main` (`git show
  origin/main:<path>` if you only need to read); and **sweep `gh pr list --state open` for a PR
  against the handoff before trusting any freshness verdict.** Reading a stale copy loses whole
  ranks; *writing* from one makes `handoff_doc.py` merge into the short base and REPLACE the
  committed document; leaving it is a `status=stale-base` refusal at the END of the session.
- 🔴 **REPORT THE NUMBER YOU MEASURED, NOT THE ONE THE DOC CARRIES.** This doc recorded a detector
  loss as **0/20**; re-measuring it in a fresh worktree gave **1/20** — same direction, and the
  difference matters, because it turns a deterministic detector into a RACE rather than switching it
  off. A doc's measurement is a reading from one tree at one moment; re-run it rather than quote it.
- 🔴 **A STRUCTURAL GUARD MUST BE SHOWN DETERMINISTIC, NOT ASSUMED SO.** "It reads the AST, so of
  course it is deterministic" is reasoning, not measurement. Run it `-count=20` under the mutant and
  count `--- FAIL:` lines. That number is what makes it a replacement for a 20/20 behavioural
  detector rather than a hopeful one.
- 🔴 **A NEW GUARD'S REAL PROOF IS THAT THE EXISTING ONE STAYS GREEN.** "Is this a duplicate of the
  fan-out ledger?" was answerable in one run: the incumbent is GREEN under BOTH the bug and the
  bug+`safeGo` mutant, because wrapping a CALLER moves no `push.Broadcast` call site. **Measure the
  incumbent under your mutant before building the challenger** — if it reds, you are about to add a
  second copy of a guard that already works.
- 🔴 **DERIVE THE SET, DO NOT SPELL IT — and check the derivation on the REAL tree before committing
  to it.** The obvious shape was a hardcoded name list; the shape that shipped derives push-deciders
  as transitive callers of `goPushBroadcast` and spawners as "takes a func parameter and contains a
  `go`", which also finds a sibling helper. ⚠ **A derived set can over-reach into a permanently-red
  gate**, so it was measured on trunk FIRST with a throwaway `go run` probe before a line of the test
  was written. **A permanently-red gate is worse than no gate; spend the probe.** ⚠ `goPushBroadcast`
  is itself a spawner and falls out correctly rather than needing a special case, but the negative
  control pins that explicitly, because the next person to widen the finder will not know it.
- ⚠ **Check whether a helper already exists before writing it** — `parsePackageFilesWithPositions`
  and `sortedKeys` were already in `task_status_ledger_test.go`. The shared parser owns the
  "0 files scanned is the failure, not the all-clear" check, and a second copy is exactly how that
  check ends up true in one ledger and forgotten in another.
- 🔴 **MY OWN VERIFICATION INSTRUMENT WAS WRONG FOUR TIMES IN ONE SESSION, AND EVERY TIME IT
  RETURNED A CONFIDENT ZERO.** (a) `git show <ref>:<path> | grep -c` run **without `-C`** read the
  cwd's repo, which had no `origin/trunk` — four counts printed `0` under a heading reading CONTENT
  VERIFICATION, and the negative control agreed because it was 0 too. (b) A `grep -c` pattern copied
  from the wrong document reported an absence that was 4 occurrences. (c) **Twice**, a word-scan over
  Go source undercounted because the phrase is split across string concatenation
  (`"KNOWN FALSE "+ "POSITIVE"`); two independent scans agreed, which read as corroboration and was
  **the same blind spot sampled twice**. (d) A scan matching a section heading EXACTLY returned `0`
  because the real heading carries a suffix (`## Open investigations — live diagnosis state`).
  🔴 **Pair every count with a probe that MUST be non-zero,
  read one hit by eye before quoting the number, and cross-check with a tool that fails
  differently** (a regex-free `str.count`). For Go message text, join `"…"+ "…"` before searching or
  the scan is a guard on a word.
- 🔴 **A COMMENT CAN BE WRONG ABOUT THE LANGUAGE, AND THEN IT SILENCES A REAL DEFECT.** A guard
  excluded `:=` on the stated grounds that *"at function scope it does not compile"*. Go's
  redeclaration rule contradicts that: a short variable declaration MAY redeclare a variable from
  the same block — **the parameter list included** — when at least one variable on the left is new.
  Proven standalone and on the real loop (`go build` rc 0, `go vet` rc 0, guard `ok` — SURVIVED).
  **A comment asserting something the spec contradicts is worse than no comment: it tells the next
  reader not to look.**
- 🔴 **FOUR CONSECUTIVE AUDIT ROUNDS HIT ONE SHAPE — the fix for a false positive SILENCED the arm
  that caught the real thing.** The method that misses it is re-running the arm's ORIGINAL mutant:
  that cannot see a shape the narrowing NEWLY stranded. **For every narrowing or widening, construct
  a NEW mutant in the direction the change moved**, and report those rows separately from the
  original-mutant rows. Adopting that rule is what surfaced the next two findings.
- 🔴 **`clawgate health check did not pass on port <N> within 15000ms` IS AN INFRASTRUCTURE FLAKE,
  NOT YOUR DIFF — and it reds TWO of the four clawgate checks.** Four controls settled it and any
  one alone would have been weak: (1) the failing TEST moves between runs; (2) the verdict
  ALTERNATES across commits that changed only string literals in one Go test file; (3) it also
  failed on a commit already merged to trunk and not from that PR; (4) passing runs clear the budget
  by **854 ms against 15,000 ms** — a race, not a margin. Recover by re-running the PipelineRun from
  its own spec (above).
- ⚠ **A `_test.go` file can BE the payload.** Where the guard IS what the PR ships, the attribution
  gate must not score it zero for its extension — doing so would read every round as zero-payload
  and stop a ladder that was working. Name each file payload or scaffolding; ambiguous is not zero.
- 🔴 **A CLEAN AUDIT ROUND ENDS THE LADDER — and the auditor may tell you otherwise.** One round
  returned zero findings and then wrote *"one more clean round would close it"*, conflating the
  findings-keyed stop rule with the two-consecutive-ZERO-PAYLOAD attribution gate. They are
  different mechanisms: the first clean round is the last one, and re-confirming it is explicitly
  forbidden.
- ⚠ **Prose in a failure MESSAGE and prose in a source COMMENT have different audiences.** Two known
  false positives were documented 200 lines from the failure; the person who hits them is reading a
  red CI leg, not the test file. Moving the routing into the messages is what converts "the guard
  reds on my correct code" from *reach for the delete key* into *here is the repair*. Where nothing
  has been measured, leave the arm bare — inventing a shape would be the same defect one level up.
- 🔴 **A CONTROL TABLE THAT COMPARES FAILURE MESSAGES MUST FIRST CHECK EACH RUN REACHED THE SAME
  STAGE.** A red was diagnosed as "a new failure mode unique to this revision" from three runs'
  messages; two of those runs had died at the health check and never reached the walk at all, so
  their silence about that failure mode was not evidence of its absence. The defect was pre-existing
  on trunk. **"Not seen elsewhere" and "not reachable elsewhere" are the same observation.**
- 🔴 **A FIXTURE CAN BE STRUCTURALLY TOO SMALL TO EXECUTE THE BEHAVIOUR ITS OWN ASSERTION NAMES.**
  `sort.SliceStable → sort.Slice` SURVIVED a stability guard because Go's `sort.Slice` uses
  insertion sort below n=12 — which IS stable — and the fixture had 4 groups / 3 members. **And the
  obvious repair does not work either: an ALL-EQUAL-rank fixture stays blind at ANY size** (pdqsort
  has an all-equal fast path; measured byte-identical at 21 and at 40). Interleaved ranks past n=12
  is the discriminator. ⚠ One of the two named mutants there is a genuine EQUIVALENT mutant, not a
  gap: once the comparator became `(Rank, Project)` over project-keyed groups it is a strict total
  order, so the two sorts are provably identical for any input.
- 🔴 **`make e2e` AND `make ux-audit` ARE DIFFERENT TIERS WITH DIFFERENT `testDir`s** —
  `playwright.config.ts` is `testDir: './tests'`, `playwright.ux-audit.config.ts` is
  `testDir: "./ux-audit"`. A commit touching only the walks is STRUCTURALLY INVISIBLE to `make e2e`,
  and a green there says nothing about them. **Run both, and say which you ran.**
- 🔴 **`./e2e/run.sh <one-spec>` IS NOT THE e2e TIER.** Quoted as one twice in one session; it hid a
  9-test break in a sibling spec AND left `trunk` broken. The full suite is ~34 spec files /
  ~225 tests / **~32 min**; it needs `nohup` + a wait loop, not a foreground call (the tool call
  times out at 10 minutes).
- ⚠ **The clawgate task-authoring PreToolUse hook fails CLOSED on a body it cannot read**, which is
  correct and cost three attempts: it cannot see through `--body "$(cat <<'EOF' …)"` shell
  substitution or a `$VAR` path. Write the body with the Write tool, pass a LITERAL path to
  `--body-file`. A blocked command does not run its own heredoc, so the file the next attempt
  expects will not exist.
- ⚠ **EDITOR AND LSP DIAGNOSTICS IN A `homelab-talos` WORKTREE ARE PHANTOM AND LOUD.** A Go syntax
  error was reported on three separate occasions that did not exist on the pushed branch (stale
  mid-edit snapshots), and every `internal/...` import reported `cannot find package … in GOROOT`
  plus a false `"errors" imported and not used` on a file that calls `errors.Is` — they resolve
  against the primary clone's module view. **`go build ./...` / `go vet` are the arbiter; do not
  "fix" code to satisfy them.**
- **The deployed-vs-merged split is measurable in one command and worth keeping.** Probing the live
  `/ui/tmux` for `<details>` discriminates "grouping deployed" from "grouping merged" without
  reading a version at all — which matters because the version number moved twice for reasons
  unrelated to this feature.
- 🔴 **`rev-parse --show-toplevel` IS THE WRONG CALL FOR "WHICH REPO IS THIS PATH IN", AND IT IS THE
  ONE EVERY READER REACHES FOR.** On a linked worktree it returns the WORKTREE, so a change built on
  it measurably does nothing. `git -C <path> rev-parse --path-format=absolute --git-common-dir`
  resolves to the MAIN clone's `.git`, whose parent is the repo.
- 🔴 **THE BASE MOVES FASTER THAN A GATE RUNS HERE, SO A MERGED-TREE RESULT CANNOT BE BANKED.**
  `origin/main` moved **five times** during one session, and `homelab-infra` `trunk` three times
  inside a single audit. Chasing it is unsatisfiable and produces a permanently-unmergeable PR.
  **What worked:** gate the merged tree, then at the merge moment re-read the base and reason about
  the SPECIFIC delta rather than re-running blind. On the one that mattered, main had bumped a floor
  in `run-tests.sh` — the runner the gate itself uses — and the check was that the bumped target was
  one the diff adds no tests to.
- 🔴 **A PROBE OF A PARTIAL ROUTE IS NOT EVIDENCE ABOUT THE WHOLE PAGE.** `/ui/tmux` renders the tab
  partial with no header, so it can answer about the tmux grouping and is structurally blind to the
  header controls. One probe of it was read as covering two PRs; it covered one. **Ask which route
  renders the thing you are asking about before quoting a zero.**
- 🔴 **A DECISION TABLE WITHOUT A POSITIVE CONTROL IS NOT EVIDENCE.** "Did the fix change a
  permission decision" was answered by a 48-row table (global × project × query, healthy vs wedged
  store) being sha256-identical across base and head, **and** by a deliberate mutant moving 12 of
  those 48 rows. Without the second half, an identical table and a harness wired to nothing look the
  same.
- ⚠ **Two agents in ONE worktree is a self-inflicted collision, and a cwd filter cannot separate
  them.** A re-gate agent was pointed at a worktree while its previous occupant was still resumed;
  no file was clobbered, but a later cleanup sweep filtering on "cwd is my worktree" killed a
  sibling agent's live Playwright run. **Worktree isolation does not help when both agents are IN
  the same worktree — give each its own, and if no filter leaves a set you are confident in, kill
  nothing.**
- 🔴 **"DO WE NEED IT" IS A QUESTION THE DOC CANNOT ASK ITSELF, A RANKED QUEUE OUTLIVES ITS ITEMS,
  AND THE ANSWER WAS NO THREE TIMES OUT OF FOUR.** Of four sub-items drawn: one was **being built by
  another session**, one was **already fixed**, one was **dismissable under its own stated
  criterion**; only the fourth was work, and it took ten minutes. **Before working any ranked item,
  check whether it is already done, already being done, or dismissable.** 🔴 **`claim-work` CANNOT
  SEE AN UNCLAIMED DUPLICATE**, because nobody claims an item they are not working on — so the
  unconditional `gh pr list --state open` sweep is the only instrument covering this class and
  **must be run unconditionally**. **An audit will never surface any of it — it scopes to the
  diff.**
- 🔴 **A FILE-LEVEL `grep -c` CANNOT ANSWER A SAME-ELEMENT QUESTION, AND A STOP-CONDITION BUILT ON
  ONE WILL HALT CORRECT WORK.** The safety rule "these routes must not be widened because their
  width pairs with the sidebar offset" is right; the check derived from it — "stop if the file
  contains `lg:pl-72`" — is wrong, because every one of the five documents contains it. What
  separates them is whether the offset sits on the SAME element as the width (unsafe) or on a
  wrapper above it (safe — the shell's own shape). **State the criterion, then build the check to
  match the criterion, not the prose.**
- 🔴 **AN INVERTED EXPECTATION IS EXACTLY WHEN NOT TO CLASSIFY A RED.** On one PR the leg this doc
  had called broken all session **passed**, and the two expected to pass failed — and reading them
  showed the failure was a fixture-setup health-check flake on a diff that swapped two CSS classes.
  **Read every red; do not sort them by prior.**
- 🔴 **THE STANDING "BUILD CLAWGATE IMAGES ON THE LAPTOP" INSTRUCTION IS STALE — THE WORKBENCH CAN
  PULL `docker.io` AND BUILD.** Measured twice, on separate dates: `docker pull` succeeded, harbor
  was reachable, a release was built there, and a later `docker build` succeeded rc 0 on the first
  attempt. The old constraint was a router DNS record with a 487-day TTL. Following the note would
  have moved a build to the laptop for nothing.
- 🔴 **A MISSING CHECK READS EXACTLY LIKE A PASSING ONE.** One PR showed **3 green checks instead of
  4** — `clawgate-e2e` never fired, because it is path-filtered on `containers/clawgate/**` and a
  trunk-merge changed nothing there. **Counting the checks is what caught it.** Resolved by comparing
  **subtree OIDs**, with a negative control proving the comparison discriminates.
- 🔴 **`git diff A..B` LISTS EVERY DIFFERENCE, NOT WHAT B ADDED.** Used to ask "what did trunk gain",
  it reported 21 clawgate files that were in fact the branch's own additions. **Three-dot (`A...B`)
  asks the right question** — the real answer was 0.
- 🔴 **`gh pr view --json headRefOid` SERVES A STALE HEAD right after a push.** It reported the old
  sha and read exactly like a failed push. `git ls-remote` is authoritative; the ahead/behind count
  `git rev-list --left-right --count origin/<branch>...HEAD` is the check that settles it.
- 🔴 **A CHECK CAN BE NAMED `FAILED: <leg>` WHILE THAT STEP EXITED 0.** Every step passed except
  `verdict`, which was reading a genuinely-failed leg recorded inside it. Two true observations
  (pattern-matching a remembered "pre-existing" failure; the documented text-vs-step disagreement)
  each pointed AWAY from the cause. It was found only by running the tests **on trunk directly**:
  they pass there, and the PR's base predated the fix that retries flaky `registry.npmjs.org` /
  `proxy.golang.org` fetches.
- **gitleaks flagged a SYNTHETIC test fixture** (`generic-api-key`, entropy 3.83) in the very test
  guarding the browser-tier text leak. Fixed by making the fixture low-entropy rather than buying a
  baseline entry — the gate's own output says an unmatched finding *"needs a real look, not a
  re-point"*. Verified the change did not make the test vacuous and that gitleaks still flags the
  **old** value as a positive control.
- 🔴 **THE AUDIT LADDER'S OWN LESSON, in one line: guards that pinned a RELATIONSHIP survived; guards
  that pinned a WORD failed.** Four rounds on one PR found a CSRF hole, then that its fix was
  bypassable by **DNS rebinding**, then that the replacement covered two of four fields. The route
  ledger produced **seven distinct escapes**, each found while fixing the previous one — a
  concatenated pattern, a `Handle` registration, `http.HandlerFunc` misread as the wrapper, a
  file-scoped alias map, a host-qualified pattern, a percent-escaped path, and wildcard/subtree
  patterns. It is now closed by an argument about the pattern's **literal head**, not a list.
- 🔴 **THREE ARTIFACTS DESCRIBED WORK THAT WAS NEVER PUBLISHED**, all one root cause — verifying the
  local file instead of the published thing: a `git add` that silently aborted while `git commit`
  succeeded; a local rebase whose validation commit was never pushed (so a PR body described code
  absent from the PR); and a PR-body edit that never reached the live body. Each was caught by
  someone reading the **published** artifact.
  `git rev-list --left-right --count origin/<b>...HEAD` sees the second; `gh pr view --json body`
  sees the third.
- **The isolation seam paid for itself repeatedly.** One PR's reply control was rendering as
  **usable against a server answering 503** on the merged tree, because a sibling PR's later round
  added a third switch after the first was written — caught only because its guard drives BOTH the
  rendered verdict and a real request through the real mux. Another PR had the same defect
  independently, and its own seam test was covering one direction only.
- **Deliberate: `CLAWGATE_TERMINAL_UI_HOSTS` carries three names** — `clawgate.zacx.dev` (behind the
  Authelia passkey edge), `192.168.50.250` (LAN NodePort, **no human auth**) and `10.42.0.30`
  (nebula). Dropping the LAN address would harden the surface at the cost of the workbench browser.
- 🔴 **A POSITIVE CONTROL THAT MUTATES IN ONE DIRECTION GOES VACUOUS THE MOMENT THE THING IT GUARDS
  FLIPS, AND IT GOES VACUOUS *GREEN*.** A control proved a guard could see a flag flip by doing
  `src.replace("<flag> = false;", "… = true;")` and asserting the result reads `true`. Arming the
  flag makes that `replace` match **nothing**: `flipped` equals `src`, and the control asserts
  `"true" == "true"` about the **unmutated** file — passing while observing nothing. **General shape:
  any control built as `replace(<current value>, <other value>)` is silently disarmed by a change to
  `<current value>`** — the direction of the mutation is a dependency on the state under test, and
  nothing re-derives it. Fix by inverting it to the hazard direction and asserting `!= src` on
  **both** arms.
- 🔴 **FLIP A CONFIG GUARD, DO NOT DELETE IT; THE SYMMETRY IS THE PRODUCT.** A test pinning a
  ship-disabled flag invites "the guard has served its purpose, drop it" on arming. What it actually
  enforces is that the armed state is a **deliberate, reviewed edit in both directions**: while it
  read `false`, arming meant editing the test; now it reads `true`, disarming means editing the
  test. A drive-by revert would stop every queued reply executing **while the server kept accepting
  writes and the UI kept looking healthy** — silent, and exactly what a config guard is for.
- 🔴 **WHEN A ONE-LINE FLAG FLIP LANDS, `git grep` THE FLAG NAME AND THE PROSE THAT DESCRIBES ITS
  STATE, BOTH.** Grepping the identifier finds the guards; it does **not** find `SHIPPED DISABLED`,
  which is the phrase three of five falsified comments were written in. **Two greps, not one** — the
  second is for the words a human used to describe the state.
- ⚠ **`scripts/gate.sh` run from a plain shell exits `3` on the pytest leg, and 3 is a PRECONDITION
  failure, not a test failure.** The log says so and prints the fix. Do not read that 3 as a red
  suite.
- **`nix eval` of a worktree needs `path:<worktree>`, not the repo root.** The first eval silently
  answered for `~/workspace/devrc` and returned a result that looked like the change not working. It
  became the negative control instead, but only because the second eval named the worktree
  explicitly.
- 🔴 **A MUTATION BATTERY WHOSE SCRATCH ROOT DOES NOT EXIST SCORES EVERY MUTANT "KILLED", AND THAT
  LOOKS EXACTLY LIKE A WORKING FIX.** The `$B` root was never created, so every `cp -a` failed,
  pytest ran against nonexistent paths, and all seven injections scored KILLED — **four known
  survivors becoming zero survivors**, the precise shape of success. The ONLY tell was that the
  **negative control was also KILLED** when a healthy control must pass. **A battery reporting a
  clean sweep with no passing control has measured nothing.** Assert the copy exists, assert the file
  under test exists, and require a real verdict line before scoring any mutant.
- 🔴 **A FIX THAT DOCUMENTS A HAZARD CAN CREATE IT.** A round wrote a disarm example into
  `nix/home.nix`'s comments while retracting a claim that a substring-matching control could go
  vacuously green. That comment's prefix ENDS IN SPACES, so the two-space-prefixed literal matches
  inside it, and the control then passes about a file whose flag was never mutated — the retraction
  and the thing it denied shipped in one commit. **When a fix adds prose QUOTING the code it guards,
  re-run the guard against the new prose.**
- 🔴 **A GUARD'S DESCRIPTION IS A COVERAGE CLAIM; CHECK THE BODY IS AS WIDE AS THE SENTENCE.** An AST
  pin whose docstring said "every spawn must take its argv from `tmux_bin()`" matched only literal
  `subprocess.<verb>(…)` attribute calls. Measured survivors: `from subprocess import run as _r`,
  `from subprocess import run`, `import subprocess as _sp`, `os.posix_spawn` — with one live the
  suite was still green. **Resolve import bindings, or narrow the sentence.**
- 🔴 **DELETING A WORD TO GREEN A TEXT SCAN CAN DELETE A GUARANTEE.** Adding
  `systemctl --user stop <unit>` to a docstring made `test_no_real_launchers.py` see a new file
  reaching an acknowledged binary. The tempting fix is to reword; that ledger's own `session-write`
  entry forbids it — and here the word was the ONLY written rollback for a surface that executes
  commands. **Acknowledge with evidence, or write the pin; do not reword.** ⚠ And an acknowledgement
  can BLIND the guard it is filed under: `hazard_hits` returns a FILE set, so once a file is in it, a
  real call site added to that file changes nothing.
- ⚠ **A count kept in prose beside what it counts DRIFTS, and fixing it at one site makes two sites
  disagree.** A tally was corrected in one dict entry and left stale in another entry of the SAME
  dict. **The fix is to remove the running total, not to renumber a third time.**
- 🔴 **A GUARD CAN BE WALKED BY ITS OWN NEIGHBOUR'S ERROR MESSAGE.** A test asserted
  `"directory" in err.lower()`; that word appears in the refusal AND in a MISMATCH message four
  lines below it, so **deleting the refusal branch outright left the test green** — the fallthrough
  supplied a message containing the word. ⚠ Worse, the mutant's verdict depended on an UNPINNED
  dimension: it died when pytest ran from `/home/zach` and survived from `/tmp` or the repo root,
  and the gate runs from the repo root. **Pin the WHOLE normalised string, and assert the
  neighbour's distinctive phrase is ABSENT.**
- 🔴 **A GUARD CAN ALSO BE WALKED BY A UNIT AND BY AN IMPORT ALIAS.** Two independent predicates,
  both written explicitly to be unwalkable, both walkable by a NAME: one tested
  `strings.HasSuffix(v, "px")`, so `left:-437rem` — the same nowhere — was skipped in silence; the
  other tested `pkg.Name == "maps"`, so `import m "maps"` evaded it **in a file whose import block
  SAYS the answer.** **When a predicate reads a DERIVED surface (the call site, the declaration's
  suffix) while a DEFINING surface exists (the import block, the CSS unit table), read the defining
  one. Ask what the value MEANS, not how it is spelled.**
- 🔴 **THE ATTRIBUTION GATE IS WHAT ENDS AN AUDIT LADDER THAT KEEPS FINDING REAL THINGS.** Four
  rounds, every headline a defect in the PREVIOUS round's corrective prose; rounds 2–4 each changed
  **0 payload lines**, and round 5 would have audited round 4's fix to round 3's fix. Measure
  `git log --numstat --format= --remerge-diff <audited>..HEAD --not <base>` and **stop on two
  consecutive zero-payload rounds** — do not stop on "safe to merge", and do not keep going on "it
  keeps finding things". ⚠ Read "4 rounds" as rounds, not as 4 rounds of findings.
- 🔴 **`scripts/tests/test_tmux_reply_agent.py` IS ADVERSARIAL TO NAIVE OUTPUT PARSING.** Its prose
  contains the literal string `1 passed` (in a comment documenting a finding), and pytest ECHOES
  test source on failure — so a battery that substring-matches `"1 passed"` scores a FAILING run as
  PASSED. An auditor hit this mid-run and had to discard a pass. **Anchor on `^1 (passed|failed)` and
  use `--tb=no`.**
- ⚠ **A test's own load control can exonerate a machine it did not measure.** browser-bridge's
  hang-net reports process-spawn latency and concludes "the MACHINE is not the explanation". Spawn
  latency is not lock contention and not I/O; on a box running four concurrent nix check derivations
  that conclusion is not supported by what it measured.
- 🔴 **`clawgatectl health` IS NOT A DEPLOY CHECK FOR A MERGE.** It read the same version both before
  and after three UI PRs merged — the version had moved for an unrelated reason while they sat
  unmerged. **A version that CHANGED is not evidence that YOUR change shipped.** Verify by CONTENT
  against the running pod.
- **A flag's effect is measured as a PAIR, in both directions** — `false` → `Install.WantedBy = []`,
  `true` → `["default.target"]`, both from `nix eval` on the real flake. One reading asserted twice
  is not the same claim.
- 🔴 **FIXING A LEAK CAN DESTROY THE ONLY COPY OF THE EVIDENCE.** Redaction and logging are one
  obligation, not a fix plus a nicety: the toggles that rendered a pgx DSN into the operator's
  banner logged it **nowhere**, so redacting alone would have deleted the driver error from the
  system entirely. **Before redacting anything, grep for where else it is recorded** — and if the
  answer is "nowhere", the log line is part of the fix.
- 🔴 **FOUR MERGED-TREE DEFECTS IN ONE BATCH, NONE VISIBLE FROM ANY PR.** Five branches, each 4/4
  green, produced two BUILD failures (two branches calling a symbol a third renamed — **zero shared
  files**), a **duplicate migration number** (`version INT PRIMARY KEY` plus a `current` read once
  before the loop ⇒ the second INSERT dies and the pod **fails to start**), and one **semantic**
  conflict that compiled fine. ⚠ The migration one is NOT silent — a pre-existing guard on trunk
  catches it — but no PR's CI runs the merged tree, so it would land red on trunk. **Build the
  integration branch and run the suite there before merging a batch.**
- ⚠ **A rename sweep is unsafe when the new name CONTAINS the old.** Replacing `ChatView{` →
  `SessionChatView{` turned already-correct files into `SessionSessionChatView`. Six files were
  damaged and repaired; the check that settled it was diffing each against trunk for byte-identity,
  not re-reading the sed.
- ⚠ **Three separate agents independently invented the same `serverEnv` e2e fixture.** That is a
  signal about the harness, not a coincidence: the terminal write surface cannot be armed from
  outside, so every spec that wants to click Send has to build its own server.
- 🔴 **`tier` discriminates the DOOR, not the CALLER.** Non-forgeable (a POST to the token route
  carrying `{"tier":"browser"}` stored `tier:"token"`), but one shared terminal token means a chief
  write is indistinguishable from a host-agent write. **Do not claim per-session attribution.**
- 🔴 **clawgate is `RollingUpdate`, not `Recreate`** — verified on the live deployment and in
  `clusters/workbench/apps/clawgate/deployment.yaml:60`, which carries a rollback comment describing
  how to revert *to* `Recreate`. Two agents were briefed with the old claim and it fed into their
  risk assessments.
- 🔴 **clawgate runs on the WORKBENCH cluster, not homelab.** `ns clawgate` does not exist on
  `$KC_HOMELAB`. The devpod agents are on homelab. Two different clusters; briefed wrong once.
- 🔴 **`.envrc` is NOT tracked in devrc** (it IS in homelab-infra). Do not carry the assumption
  across.
- 🔴 **Only `(host, pane_id)` uniquely identifies a window.** Measured over 87 windows: `codename`
  covered 19 of 69 **with 18 carrying none**, so it is unusable as a selector. The wire key for the
  tmux session is camelCase **`tmuxSessionName`** — querying `session` returns a confident
  "88/88 missing". 🔴 **And a tmux CODENAME IS NOT UNIQUE**: `mango` named three panes at once,
  across both hosts. Disambiguate by host + window index, and when acting on a reply control select
  it by `data-reply-entry`, never by position.
- ⚠ **The tmux read model lags reality.** After the operator closed a pane, the snapshot still listed
  it — "as fresh as the last push", exactly as `clawgatectl tmux` warns. Do not read its absence or
  presence as live truth.
- 🔴 **`term send` is subject to NONE of the calling session's PreToolUse hooks.** Found live: a wide
  tmux kill was blocked by a guard, and a send would have bypassed it. That is the blocked action,
  not a workaround.
- 🔴 **The clawgate skill is a home-manager `home.file` COPY** (`readlink -f` lands in `/nix/store`),
  so merging a change to it does not make it live — that needs `home-manager switch`.
- 🔴 **`SKILL.md` has a byte-exact ratchet** (`test_the_skill_did_not_grow`); rule: any addition
  needs an eviction in the SAME commit. It is a **DIFFERENT instrument** from
  `scripts/skill-audit.py` (budget/cap) — reading the wrong gate and finding headroom is
  indistinguishable from having it, until CI.
- 🔴 **A POLL MUST BIND TO THE HEAD SHA, IN ONE JSON CALL.** After a force-push the PREVIOUS head's
  terminal checks linger, so a poll reading them settles on another commit's verdict — one printed
  `SETTLED n=4` over a rollup that was actually empty. Use ONE
  `gh pr view --json headRefOid,statusCheckRollup` and abort if HEAD moves. Also: `gh pr checks`
  prints `pass`/`fail`, **not** `success`/`failure`, and prints prose when the rollup is empty,
  which a naive line-count reads as checks.
- 🔴 **`gh pr checks` CAN REPORT A STALE ALL-PASS ON THE CORRECT HEAD SHA, SECONDS AFTER A PUSH, AND
  BOTH DOCUMENTED RULES MISS IT.** The rollup showed 4/4 pass against the new head — the
  minimum-count rule and the all-terminal rule BOTH passed — and it was the PARENT commit's verdict;
  the real checks reset to pending ~45 s later. **The discriminator is elapsed time versus pipeline
  duration**: Tekton cannot run in seconds. **Wait for the rollup to RESET before believing it.**
- 🔴 **The devrc gate names only ONE failing test even when several targets fail.** Read the
  per-target `FAIL  <path>  (…failed=N…)` lines; the summary line is not the set.
- 🔴 **Verified-in-isolation is the vacuous green.** One merge-blocker was a fixture shebang tripping
  a cross-file guard; three audit rounds ran the changed file alone (50/50 green) and none could see
  it. Three separate PRs in one session were blocked by cross-file ledger guards.
- **Scoping a sentence to make a claim true is what keeps failing.** That ladder converged only when
  the guarantee moved INTO the code (a union that is a superset by construction), after two rounds
  of replacing one false absolute with another.
- **A poll timing out is not evidence about the PR** — check the PipelineRun's real
  start/completion times. devrc's pytests tier genuinely runs 20–55 min; a status timestamp is when
  the status was POSTED, not the run duration.
- 🔴 **A regex without a left word boundary matches inside another word** — `kill-session` lives
  inside `s·kill-session`. My own diagnostic grep reproduced the same bug while hunting it, which is
  how it hid: the tool and the guard agreed, and both were wrong.
- 🔴 **A duplicate dict key is silent in Python.** Adding a ledger entry another session had already
  added shadowed theirs with no error. Only the mutation check (drop it → nothing changes) revealed
  it was doing no work.
- 🔴 **A mutation planted in an UNTRACKED file scores SURVIVED.** The scanner iterates
  `git ls-files`; appending to a path git does not know produces a green that means nothing. Verify
  the target is tracked-and-modified (`git status --porcelain <path>`) before reading the verdict.
- 🔴 **A Flux Kustomization SUSPENDED BY HAND reports `Ready=True ReconciliationSucceeded` forever.**
  `agent-pods` was suspended via the flux CLI on 2026-06-07, with `suspend` absent from git and
  therefore unremovable by kustomize-controller under SSA. Everything merged under
  `clusters/homelab/apps/agent-pods/` since June was silently inert, and nothing in git, no task and
  no doc recorded it. **The only tell was that its `lastAppliedRevision` differed from every
  sibling's — compare that across Kustomizations, never the Ready condition.**
- 🔴 **`GET /api/transcripts/stream/cursors` wants `X-Clawgate-Token`, not `X-Hook-Token`.** The
  wrong header returns `401 {"error":"invalid or missing hook token"}`, which reads exactly like a
  credential problem rather than a header-name problem. `auth.go:583-588` accepts
  `Authorization: Bearer` or `X-Clawgate-Token` and nothing else. Negative control for the route
  itself: an absent route returns 404, so 401 does mean "exists, needs auth".
- 🔴 **A `KILLED` Tekton verdict is not a code failure and must not be read as one.** Three gates on
  one PR all reported `KILLED: … the gate pod died at or after step pytests
  (preempted/evicted/OOM/timeout)`. That red said nothing whatsoever about the PR's content; the
  content question had to be settled by running the tests in a clean worktree instead.
- 🔴 **`claim-work` reports "THIS SESSION (you already hold it)" for claims made by EARLIER sessions
  in the same clone.** Ownership is `/etc/machine-id` + `git rev-parse --git-dir`, so every session
  in the devrc primary clone shares one owner id — **rc 12 means "this host+worktree", not "this
  conversation"**. A genuinely concurrent peer shows a DIFFERENT owner-id, the only reliable tell.
  🔴 **And the lock cannot catch a duplicate when the two sides derive DIFFERENT SLUGS for one job**
  — one item was worked concurrently under two slugs and one session's work was wasted. The
  `gh pr list` sweep is what sees that, not the lock.
- 🔴 **The ranked list in this doc is a REPLACE section.** A delta that includes
  `## Next steps (ranked)` without carrying the whole history forward DELETES every completed rank —
  and the ranks are load-bearing, because a rank is half a `claim-work` slug. **Build the section by
  reading lines out of the existing doc and editing anchors in place, never by retyping it.**
  `handoff_doc.py`'s durable-drop warning is the backstop, not the plan.
- 🔴 **A `devrc-pytests` red can be a `node --check` TIMEOUT, which is a statement about the pod.**
  Two `test_mjs_parses[…]` cases failed with `subprocess.TimeoutExpired … after 30 seconds` on a PR
  that changed one markdown file and added no `.mjs`; `node --check` has no legitimate slow path.
  **The tell is a failing test that names a file your diff never touched** — read the gate pod's own
  log (`kubectl logs -n tekton-ci <pipelinerun>-gate-pod`), do not re-run blind.
- 🔴 **A PR red can be a STALE BASE measured in MINUTES, not days.** One PR was refreshed at
  00:00:22Z; the fix for the exact test it then failed merged at 00:07:05Z. Seven minutes.
  **`git merge-base --is-ancestor <fix-sha> <pr-head>`** answers it in one command — do that before
  theorising about the code.
- 🔴 **A dispatched opencode run writing to the system temp directory is auto-rejected and DIES.**
  Two of three browser dispatches died exactly that way — the second **despite a brief that both
  forbade it and supplied a pre-created in-project scratch directory**. A prose prohibition does not
  reliably stop it. If a browser measurement matters, drive it from a session that controls its own
  writes.
- 🔴 **"The tmux overview grid does not auto-refresh" is REFUTED — do not re-file it.** Source at
  `origin/trunk`, `containers/clawgate/internal/ui/tmux.go:238` (`hx-target="#panel-tmux"`) carries
  `hx-trigger="load, every 60s, sse:tmux.changed from:body, clawgate:resync from:body,
  clawgate:termwrite from:body"`. The observation behind the claim waited **30 seconds against a
  60-second poll** — a window shorter than the period under test — and the run also reported its tab
  was **hidden and therefore throttled**, which suppresses htmx timers regardless. Two independent
  artifacts, one non-defect.
- 🔴 **An IDENTICAL failure across N independent targets is a fact about your INSTRUMENT.** A
  connection probe returned `<unreachable>` on all five containers; the cause was `-U postgres`
  while the harness creates a `clawgate` role. Read as data it would have reported five wedged
  databases. **Validate, then re-read.**
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
  same shape, and four more docstrings claiming a hand-maintained list "fails if the set GROWS" when
  it measurably does not.
- **Round 0 earns its place.** It split one PR (92-line fix vs ~460-line axis), retired six pieces of
  dead code on another, and refuted a "cannot verify without deploying" premise. **It is the only
  round that asks whether the change should EXIST.**
- 🔴 **`devrc` `main`'s red `pytests` gate is a MOVING SET, not one stuck test — so naming "the"
  pre-existing failure implies a stability that does not hold.** Measured hours apart, the named
  verdict was a **different** test each time; both were real and both pre-existed, so **a session
  that checks "is it still that test?" will get a different answer and may conclude its own change
  broke the gate.** The discriminator costs one command — run the SPECIFIC failing file in a
  detached worktree of pristine `origin/main`:
  `git -C $DEVRC worktree add --detach /tmp/ctl origin/main` then
  `(cd /tmp/ctl && nix-shell -p "(python3.withPackages(ps:[ps.pyyaml ps.pytest]))" --run 'python3 -m pytest -q scripts/tests/<file>.py')`.
  ⚠ **Two ways that control returns a non-answer:** a broad `-k <name> scripts/` collects modules
  needing `psycopg2` and dies `INTERNALERROR` — the instrument, not a verdict; and grepping the test
  NAME finds a file that merely *mentions* it, after which pytest prints **`no tests ran`**. **Grep
  for `def <test_name>`, and treat `no tests ran` as "wrong target", never as "passes".**
- 🔴 **An item completed AFTER an eviction sweep must be evicted in the change that closes it.** One
  rank survived a sweep legitimately (it was open then), was closed by a PR in another repo, and
  then sat in the ranked queue as available work — a `/resume` session would have `claim-work`'d and
  redone it. **Closing and evicting are two different actions and only the first feels like
  finishing.**
- 🔴 **A Go raw string cannot contain a backtick, escaped or not.** `tmuxGroupScript`/`tmuxViewScript`
  are backtick-delimited raw strings; putting a backticked word in a comment inside them breaks the
  build. Cost two build failures, and one near-miss caught only by the compiler.
- ⚠ **A Go `0 passed / 0 failed` is a COMPILE failure, not a clean run.**
- **Deleting a feature means deleting its tests — and keeping what they KNEW.** One item removed 6;
  each site carries a note recording the discovery, so it is not paid for twice.
- 🔴 **A RECON AGENT'S "IT DOES NOT EXIST IN THE REPO" CAN BE TRUE OF THE SOURCE AND FALSE OF THE
  BEHAVIOUR.** A thorough read-only agent grepped the whole worktree case-insensitively for
  `selection_menu`, found **zero** hits, and reported the item needed re-pointing. It was right about
  the grep and wrong about the system: the name arrives in the DATA as a `session-manager` waiting
  signal, reaches the UI as `TmuxWaitingSignal.Signal` (`internal/ui/tmux.go:202-203`) and is
  rendered by `waitingEvidence` (`:2956`, emitted `:2608`). **When a grep says a user-visible string
  is absent, ask where the string is PRODUCED before concluding the feature is absent** — a value
  that is data on this side of a wire is a literal on the other.
- 🔴 **`origin/trunk..HEAD` LISTING COMMITS IS NOT EVIDENCE OF UNMERGED WORK AFTER A SQUASH.** It
  listed all of a merged PR's commits minutes after the squash landed, and `git diff --stat` showed
  794 deletions on top — both readings say "do not delete this worktree, work would be lost". Both
  are artefacts: the deletions were trunk's OWN newer commits in files the branch predates. The
  discriminating check is per-file and takes one loop — for each file the branch changed,
  `git diff --quiet origin/trunk HEAD -- <file>`. **Verify a squash by CONTENT, and scope the diff
  to the files the branch actually touched**, or trunk's unrelated movement reads as your work going
  missing.
- 🔴 **`git worktree add -b <branch> origin/<x>` SETS THE UPSTREAM TO `origin/<x>`, SO A BARE
  `git push` TARGETS THE WRONG BRANCH.** Caught once with `main` as the target, one command before a
  scripted `--push` would have committed straight to it. **Re-point immediately after creating any
  worktree:** `git push -u origin <your-branch>`.
- 🔴 **AN `audit-claims` BLOCK WITH NO NUMBERED LINES SILENTLY WIDENS THE NEXT ROUND'S RANGE.**
  `audit-dispatch.py` anchors on the newest block it can PARSE, so one unparseable block made the
  following round's range span two rounds' fixes. **It is announced on stderr, once, and nowhere in
  the brief** — a wider range reads as a perfectly ordinary delta. Read stderr before dispatching,
  and repost a numbered block rather than working around it.
- 🔴 **"NOTHING READS IT" IS A CLAIM ABOUT THE READERS YOU THOUGHT OF.** An audit established that
  nothing reads `handoff_budget.py`'s trailing size comments — true of every consumer reading dict
  **values**, and false: a mutation-battery row anchored on the whole ledger line INCLUDING the
  comment, and `test_mutation_battery_anchors.py` counts that anchor **as text**. Deleting them
  would have taken that row to **0x — SURVIVED while testing nothing**; it was caught only because
  the deletion brief demanded a positive control instead of inheriting the claim. **Before deleting
  anything, ask what reads the file as SOURCE, not just what imports it.**
- 🔴 **THE HANDOFF SIZE GATE'S PLAYBOOK FORBIDS THE OBVIOUS FIX.** Moving `## Gotchas` wholesale is
  what a size-driven read reaches for first, and the playbook explicitly refuses: *"DO NOT satisfy
  this by deleting an open investigation, a gotcha or a ruled-out theory"* — those are the sections
  whose whole value is that a future session does not repeat the work. **Read the playbook's ORDER
  before picking a target** — but step 1 (evict what has CLOSED) is a **body-level, sometimes
  cross-file judgement, not a heading scan**: one sweep here was enumerated from `^### ` headings and
  missed three blocks whose closure was recorded only in a BODY, one of them declared in a different
  file entirely. **Read each block, not its heading.** Step 1 freed 46,902 B and then 6,377 B more on
  this document — ⚠ both past tense, and neither a ranking of what is left; measure before reaching
  for it again. Step 2 (demote dated EVIDENCE, keep the imperative) is what this section then had
  done to it — every rule stayed, every story moved to
  `claudedocs/refs/tmux-webapp-gotcha-evidence.md`, and ~25 rules turned out to have been written
  down two to six times each. **It is judgement work one entry at a time, not a size exercise.**
  ⚠ **An eviction is justified by the RATCHET DIRECTION, not by headroom** — raising the allowance
  one step would have bought MORE headroom off a one-integer diff.
- 🔴 **A RESTATED DERIVED NUMBER IN A DOC IS A DEFECT WAITING TO HAPPEN, AND THE FIX IS DELETION,
  NEVER A BETTER DESCRIPTION.** This doc has been bitten four separate times: the bullet above once
  carried a frozen `133,129 B / 54%` that was already wrong in the PR that shipped it, and three
  successive descriptions of a ledger's size comments went the same way. **Remove the number and
  point at how to measure it** —
  `LC_ALL=C awk '/^## /{s=$0} {n[s]+=length($0)+1} END{for(k in n) printf "%8d  %s\n", n[k], k}' claudedocs/handoff-tmux-webapp.md | sort -rn`
  (`LC_ALL=C` is what makes `length()` count bytes rather than characters).
- 🔴 **`/handoff` APPENDS AND NOTHING EVER DEDUPES, SO A LONG-LIVED DOC ACCUMULATES THE SAME RULE IN
  DIFFERENT WORDS — AND THAT, NOT VERBOSITY, IS WHERE THE BYTES ARE.** Measured on this section:
  ~25 rules had been written down two to SIX times each over weeks, and merging them — **always
  keeping the WIDEST wording, never the shortest** — accounted for ~35 KB of the ~40 KB one prune
  recovered, while rewriting the 45 fattest entries to imperative-plus-tell yielded only 3,329 B.
  **Reach for duplication before compression**: compression cuts into the rules, deduplication does
  not. The instrument is a jaccard sweep over every pair of top-level bullets, and its zero means
  nothing without a POSITIVE CONTROL — the same sweep scores 24 pairs above 0.30 on the pre-prune
  section and 0 after. ⚠ A duplicate can arrive in the SAME write that you are pruning against: one
  `/handoff` append re-stated two rules the section already carried, so re-run the sweep over the
  merged result, not over the old text.
- 🔴 **THE GRANDFATHERED ALLOWANCE IS `ceil(size / GRANDFATHER_STEP) * GRANDFATHER_STEP`, SO HEADROOM
  DEPENDS ENTIRELY ON WHERE THE DOC LANDS INSIDE A STEP — AND JUST UNDER A BOUNDARY IS THE WORST
  PLACE TO STOP.** A prune that ends 2 KB below a boundary ratchets the allowance to within 2 KB of
  the doc, which is **less than one `/handoff` write**: measured over the last ten writes to this
  document, each added **4–11 KB**. The next author then has to either ratchet UP (which the ledger
  requires be justified in the commit message) or do eviction work on a doc that was just pruned —
  the permanently-red-gate shape. **Check where the size lands in the step before you stop, and
  prefer stopping just ABOVE a boundary** — the same bytes on disk, a whole step of working margin.
  ⚠ Never hand-compute the allowance: change the doc, let the size test fail, and paste the ledger
  line it prints.
- 🔴 **A DEMOTION IS SAFE ONLY IF THE SINK IS PROVED TO HOLD THE SOURCE — DO NOT TRUST THE EDIT.**
  Slice the pre-prune text into `claudedocs/refs/` by line range, then assert it mechanically: the
  sink's body BYTE-IDENTICAL to the pre-prune section (sha256 both), every pre-prune line present,
  a POSITIVE control (lines the doc no longer carries ARE in the sink) and a NEGATIVE control (an
  invented line is in neither, and flipping one character inside the sink makes the check go red).
  Without that negative control, a checker wired to nothing reports the same clean zero.
- **The doc-size ceiling lives in `scripts/lib/handoff_budget.py`, not in the test that owns the
  assertions.** `scripts/tests/test_handoff_doc_size.py` imports
  `MAX_BYTES`/`GRANDFATHER_STEP`/`GRANDFATHERED` from there; grepping the test for `MAX_BYTES =`
  finds the import, not the value. ⚠ And the test module cannot be imported outside the dev shell
  (`import pytest` at module scope), so `nix develop ~/workspace/devrc -c python3` or a plain grep of
  the lib is the way to read a constant.
- **`claudedocs/refs/` is exempt from the size ceiling because the scanner globs `handoff-*.md`** — a
  refs file does not match the pattern, so a 1 MB refs file is pinned as silent. ⚠ That exemption is
  also why a demoted block is invisible to `handoff_search`: the pointer left behind in the doc is
  the only route back to it. 🔴 **So the demotion target is UNCAPPED and UNSEARCHABLE, and the
  ladder RELOCATES bytes rather than retiring them** — this arc's refs files grew 80,353 → 115,045 B
  in a single day while the gated number fell.

- 🔴 **CLAWGATE HAS NO IMAGE AUTOMATION — MERGING TO `trunk` DEPLOYS NOTHING, SILENTLY.** Three PRs' worth of task 593 sat inert on `trunk` while the cluster kept pulling `0.8.34`. The pin is an immutable literal tag and there is no `ImageRepository`/`ImagePolicy`. **`git log` is not evidence a code change is live; `clawgatectl health` and the live pin are.**
- 🔴 **PROVE THE IMAGE CARRIES YOUR CODE BEFORE PUSHING IT.** A pin landing after your merge does not mean the image was built from it — that cost a release at 0.8.20. Cheap method that worked here: `docker create` the candidate, `docker cp` the binary out, and grep it for a literal only your change introduces — with a **positive control** (a literal that predates it) and a **negative control** (an invented string). Measured for 0.8.35: `data-tool-detail-shape` 1, `data-tmux-chat-clamp` 1, control 3, negative 0.
- 🔴 **`deploy.md` IS DOC-ROTTED IN THREE PLACES, ALL MEASURED THIS SESSION.** (a) It says to build via `DOCKER_HOST=ssh://zach@192.168.50.250` — a session running ON the workbench IS that daemon and the ssh form fails. (b) It says `nix-shell -p tailwindcss`, which now ships **v4**; the repo pins **v3** in `package.json`, so use `./node_modules/.bin/tailwindcss`. (c) Its CSS sanity figure "~36 KB" is stale-low — the real output is **45,895 B**. ✅ And its docker.io warning is now WRONG in the good direction: `docker pull docker.io/library/alpine` **succeeds from the workbench**, so a clawgate build no longer has to run on the laptop.
- 🔴 **A FRESH CLAWGATE WORKTREE HAS NO `node_modules`** (gitignored) and no `web/static/app.css`. Symlink `node_modules` from the base clone rather than reinstalling — and **`rm` the symlink, never the target**, before `worktree remove --force`.
- 🔴 **THE HEADING-ONLY SWEEP FAILED AGAIN, IN A NEW SECTION.** The `#1718` eviction set was enumerated from `### ` HEADINGS, so three investigations whose closure was recorded only in their BODY were left behind — and **one of those had its closure declared in a DIFFERENT FILE**, so no same-file scan could ever have found it. This is the identical predicate failure `claudedocs/refs/tmux-webapp-closed-ranks.md` records for the rank sweep. **Read bodies, and read the other file.**
- 🔴 **"NOTHING READS IT" IS A CLAIM ABOUT THE READERS YOU THOUGHT OF.** An audit established that nothing reads `handoff_budget.py`'s trailing size comments — true of every consumer reading dict **values**, and false: a mutation-battery row anchored on the whole ledger line INCLUDING the comment, and the anchors test counts that anchor **as text**. Deleting them would have taken it to **0x — SURVIVED while testing nothing.** Caught only because the brief demanded a positive control instead of inheriting the claim. **Ask what reads the file as SOURCE, not just what imports it.**
- 🔴 **AN `audit-claims` BLOCK WITH NO NUMBERED LINES SILENTLY WIDENS THE NEXT ROUND'S RANGE.** `audit-dispatch.py` anchors on the newest block it can PARSE, so an unparseable round-3 block made round 4's range span two rounds' fixes. **It is announced on stderr, once, and nowhere in the brief** — a wider range reads as a perfectly ordinary delta. Read stderr before dispatching.
- 🔴 **A PROSE LADDER RECURSES RATHER THAN CONVERGING, AND THE ATTRIBUTION GATE CANNOT STOP IT.** Five rounds on `#1718`, every one finding its finding in prose a previous round wrote; four about the same eleven comments, each round replacing a falsified description with a new one. The only thing that ended the class was **deleting the subject matter**. On a docs PR the payload is ~100% of every diff by construction, so the two-consecutive-zero-payload gate is structurally inert — the prose escape hatch and its ladder-authored measurement are the only stop.
- 🔴 **AN EMPTY RESULT CANNOT DISTINGUISH TWO MECHANISMS.** `#1718`'s four checks read `pending` for hours and I named it as the documented `timeouts.tasks` shape. The discriminating read refuted it: the PipelineRuns were **Succeeding**, zero pods pending, and one was literally `Cancelled` — **each of my own six pushes superseded the previous run before it could report.** Read `kubectl -n tekton-ci get pipelinerun` before naming a cause for a missing verdict.
- ⚠ **`clawgatectl` is nix-built from the LOCAL tree, so after a deploy it prints `note: server 0.8.35, clawgatectl built for 0.8.34` until a `home-manager switch`.** `deploy.md` says that note means you forgot to bump `client.go` — here both pins moved in one commit and `TestDeployPinMatchesClientBuildVersion` passed, so on THIS host it means the installed binary predates the commit. Two causes, one symptom.

- 🔴 **The `#1718` audit ladder is CLOSED at round 5 — do not re-open it.** Five rounds, every one finding its finding in prose a previous round wrote; four about the same eleven `handoff_budget.py` comments, which were ultimately DELETED rather than described a fifth time. Stopped on the prose escape hatch at **26/36 (72%) ladder-authored pre-image lines**. The rationale is a PR comment on `#1718` and is the authority. *(Carried here from `## Status` 2026-09-15 — it is a standing instruction, not status, and a REPLACE section would have deleted it.)*
- 🔴 **THIS DOC'S OWN `How to verify` BLOCK CARRIED A COMMAND THAT RETURNS A FALSE ZERO, AND IT
  WAS THE ONE PRESCRIBED FOR THE MOST LOAD-BEARING READ IN THE NEXT ARC.** It said
  `git grep -n 'func wantOpen' -- containers/clawgate/internal/ui/tmux.go`. That returns
  **nothing** — `wantOpen` is a **JavaScript** function inside the backtick-delimited Go raw
  string returned by `tmuxGroupScript()` (`:2173`), not a Go declaration; so are `setExpanded`
  (`:2019`) and `applyViews` (`:2362`, in `tmuxViewScript()`). A session running the doc's own
  instruction gets a clean empty result and concludes the policy function does not exist —
  immediately after the doc told it `wantOpen` is "THE POLICY, AND THE ONLY DEFINITION OF IT".
  **A `func <name>` grep is a claim about the LANGUAGE the symbol is written in.** When a repo
  embeds one language inside another's string literals, grep for the bare name first and let the
  hit tell you what it is. Generalises past Go: the same shape hides every symbol in an embedded
  SQL, shell or template string.
- 🔴 **A CARD'S `## Context from recon` SECTION ROTS FASTER THAN THE CARD, AND ITS OWN "VERIFY, DO
  NOT TRUST" WARNING IS NOT ENOUGH — VERIFY MEANS RE-DERIVE THE NUMBERS, NOT RE-READ THE PROSE.**
  All five of 595's quoted line numbers were stale within days, moved by the three PRs of the
  task it was explicitly split from (`tmuxHostTabs` 1332→1296, `tmuxHostTabPanel` 1374→1338,
  `tmuxSessionSection` 1659→1638, `tmuxWindowCard` 2430→**2512**, `tmuxGroupScript` 1748→**1941**).
  The card even says "Do 593 first" — so the staleness was *scheduled by the card itself* and
  nobody re-ran the greps. **Any line number written into a card or a doc is a measurement with a
  shelf life measured in merges; re-derive the whole set in one grep before quoting any of it.**
- 🔴 **A DERIVED COUNT IN A CLOSING CONDITION GOES STALE THE MOMENT THE CARD GROWS, AND THE
  CLOSING CONDITION IS THE WORST PLACE FOR ONE.** This doc's `closing-condition` said "all **12**
  acceptance criteria" in three places while the card carried **16** — comment 1428 added 13–16
  hours before the doc was written, and the same doc's own 593 table names bulk Chat/Raw as 595's
  scope. So the doc contained both the right scope and the wrong count. **An arc would have
  closed with a quarter of it unbuilt, and the close-check would have read GREEN.** Count the
  criteria at the moment you check, from the card's three surfaces (body + every
  `## Acceptance criteria — additions` comment); never carry a criteria count in prose.
- ⚠ **A handoff's front-matter `clawgate-task:` is not updated by writing a new closing
  condition.** This doc's front matter still read `593` — a card that is `ready_for_review` and
  finished — while the closing condition named 595, so `resume-state.sh` dutifully reconciled the
  *finished* card and reported it, and the live card sat unmentioned. The two live in different
  places and nothing cross-checks them. **When an arc hands over to a new card, move the front
  matter in the same delta as the closing condition** — `clawgate_handoff.sh resolve` names the
  worked task and prints the exact line to record.
- ⚠ **`git worktree add -b <b> origin/trunk` sets the new branch's upstream to `origin/trunk`**,
  so a bare `git push` targets trunk — on this repo that is a deploy. Re-point immediately with
  `git push -u origin <b>`, which also publishes the branch for the duplicate-work sweep. (The
  rule was already in this section for `origin/<x>`; recording that it fired again, on `trunk`,
  in the ordinary course of setting up a feature branch.)

- 🔴 **`POST /agents` GENERATES the agent name; the `name` form field belongs to the RENAME route.**
  Creating "chief" produced `zesty-stoat`; `POST /agents/{id}/name` then sets `displayName` only —
  the canonical name is immutable because it is the namespace + ServiceAccount identity
  (`devpod-zesty-stoat`). Nothing server-side keys on the name, only the token, so this is cosmetic
  — but any lookup keyed on the string `chief` is wrong, and `#842` ships a decoy agent slugged
  `chief` to make that fail loudly.
- 🔴 **`action=save` provisions at 0 replicas.** That is how to mint an identity that holds a
  credential without running a pod. `action=dispatch` kicks off immediately.
- 🔴 **A grep COUNT is not a finding — twice in one session it nearly became one.** `data-tmux-bulk-view`
  returned 3 files on `origin/trunk` after `#837` removed it (all comments and absence-assertions —
  a test that forbids a string necessarily contains it), and `archive_start` returned 2 hits in the
  0.8.40 image after D4 dropped the column (both explanatory SQL comments). **Read the hits.**
- 🔴 **A red check's control expires.** `tekton/gitops-validate` was red on `trunk` itself for most
  of this session, which made it safe to ignore — and by merge time `trunk` was GREEN, so the stale
  control would have waved `#842`'s red through. The real answer: `#842`'s branch lacked **exactly
  one** trunk commit, `6f798e0ef` (`#841`), which fixes the very leg that was failing, and the PR
  touched zero files under the scanned directory. **Re-measure a control at the moment you use it.**
- 🔴 **`open` DISCARDS its url when the session already owns a tab** — it returns the owned tab. A
  "fresh tab" control built on it reported STILL ANONYMOUS for a browser that was signed in. Use
  `nav` to move an existing tab.
- 🔴 **The `browser` skill's `reference/` tree does NOT ship to opencode.**
  `~/.config/opencode/skills/browser/` holds exactly `SKILL.md` + the CLI. A brief that lets the
  agent follow the skill's own `reference/<topic>.md` pointers dies ~16 s in as `external_directory`.
  Name `scripts/browser-bridge/reference/` relative to `--dir` instead.
- ⚠ **`opencode-dispatch preflight` scans PROSE textually** — it refused a brief over the URL path
  `/tmux`, read as a filesystem path. Write such fragments as prose.
- ⚠ **A pipe eats the exit status** — bit this session three times (`go build | head` reading
  `head`'s rc, `nix build`, `preflight | tail`). Capture with `out=$(cmd); rc=$?`.
- 🔴 **A sweep in the wrong TIER silently skips.** `internal/attention` runs 63 tests with
  `CLAWGATE_TEST_DATABASE_URL` and 31 without — **30 skipped** inside a run reporting "25 packages
  ok", which is how a mutant survived undetected. Name the tier in any mutation claim.
- ⚠ **An agent's cleanup claim is not evidence.** `#842`'s author said its worktree `node_modules`
  was a symlink into the base clone; it was a real directory. `rm -f` on a directory is a harmless
  no-op, so nothing was lost — but the removal ran on an unverified claim.

## How to verify
```bash
# what is actually deployed (the consumer's own answer, never the pin)
curl -s http://192.168.50.250:30302/health

# is the chief door armed, and does it say so?
KC=/home/zach/workspace/homelab-talos/workbench-kubeconfig
kubectl --kubeconfig $KC -n clawgate logs deploy/clawgate --since=10m | grep "AGENT-IDENTIFIED door"

# chief's capabilities, from INSIDE its own pod — the end-to-end proof
P=$(kubectl --kubeconfig $KC -n devpod-zesty-stoat get pods -o jsonpath='{.items[0].metadata.name}')
kubectl --kubeconfig $KC -n devpod-zesty-stoat exec "$P" -c agent -- clawgatectl tmux ls | head -20

# the dogfood run
tail -n 60 /home/zach/workspace/devrc/.opencode-dispatch/20260917-201600-chief-capability-dogfood.log
```
🔴 `/ui/tmux` needs a signed-in human — no curl reaches it. The workbench Brave `work` profile is
now signed in to the LAN address, so the bridge can read it; the public name still needs a passkey.
🔴 **`clawgatectl health` reports the SERVER. A pin bump is not a deploy and a deploy is not a
consumer** — check the running pod's `imageID` against the digest that was pushed.
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
