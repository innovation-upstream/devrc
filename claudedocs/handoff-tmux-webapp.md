---
---
# Handoff: tmux-webapp — 2026-08-26

## Goal
Build a webapp that visually organizes and provides live terminal interaction with tmux
sessions across two machines (workbench + laptop). Single unified view.

## State now
- Branch: `handoff-tmux-webapp` on `devrc` (this doc). Nothing else built — no code, no PR.
- 🔴 **This doc was AUDITED on 2026-08-26 after the design session.** Four of its original
  decisions did not survive contact with the repo; they are marked **REOPENED** below and the
  evidence is in "Audit findings". Do not implement a REOPENED decision without resolving it.
- 2 untracked + 2 modified tracked files on the workbench, all unrelated
  (`nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`,
  `scripts/dl-router/tests/load_test_store.sh`, `claudedocs/close-the-loop/STATE.md`,
  `claudedocs/the-algorithm-applied-2026-06-17.md`).
- Drift (re-measured 2026-08-26, supersedes this doc's first draft):
  `drift-check.sh` → **rc 10 — laptop `main` is BEHIND `origin/main` by 2 commits, needs a
  ship.** The earlier `rc17 clawgate-srcDir` note is **stale**; `[srcrepo] compared=2 same=2
  differing=0` today. Fix: `scripts/ship.sh`.

## Audit findings (2026-08-26) — read before implementing

Each finding names the file that contradicts the decision. Verify before acting; these were
measured against `origin/main` at `5426fe54`.

**A1 🔴 REOPENED — WebSocket contradicts the stdlib constraint that justifies every other
decision.** Python's stdlib has no WebSocket server; `http.server` cannot speak RFC 6455. This
repo has already stood at this exact fork and written the answer down:
`scripts/browser-bridge/server.py:23-26` — *"Long-poll (vs a hand-rolled stdlib WebSocket) was
chosen because the whole rendezvous is then pure `http.server` + `threading` and is FULLY
unit-testable with stdlib alone against an in-process fake extension — no new pip deps."*
browser-bridge is the closest analogue in the codebase (live bidirectional
browser ↔ local-process channel) and it **rejected** WebSocket on precisely the grounds this
doc used to justify stdlib. The only RFC6455 mentions anywhere in the repo
(`server.py:19`, `browser-bridge/README.md:147`) are that same decision restated. Either adopt
long-poll, or state explicitly that this workload justifies breaking
the constraint — but the constraint and the transport cannot both stand as written.

**A2 🔴 REOPENED — the deploy target is the wrong machine, and this exact pattern has a
measured failure here.** tmux sockets are local unix sockets on the workbench and the laptop.
Neither is a homelab node — homelab and workbench are **separate clusters with separate
kubeconfigs** (`$KC_HOMELAB` vs `$KC_WORKBENCH`). A homelab pod can only reach tmux by SSH-ing
to both hosts, which contradicts this doc's own "single backend on workbench, SSH to laptop".
And the precedent is recorded at `nix/home.nix:3021-3022`: *"Building hosted reach ahead of a
reader is the shape that left the subsystem-store-api dead on arrival."* The established
pattern for a workbench-hosted UI is `initiatives-viewer` — a `systemd.user.services.<name>`
unit, `serverMode`-gated, bound to the workbench's own LAN address.

**A3 🔴 REOPENED — `send-keys` under the inherited trust model is unauthenticated RCE on both
machines.** The API surface below lists `send-keys` and the doc never mentions auth. The prior
art it would copy is deliberately unauthenticated: `scripts/initiatives/viewer.py:4203` —
*"Deliberately UNAUTHENTICATED: the viewer binds LAN/localhost only."* That trust model was
chosen for a **read-only** page. `send-keys` into an arbitrary pane is arbitrary command
execution as your user, on the workbench **and** the laptop. This needs an explicit decision,
not inheritance.
  - Mitigating fact, and know why it is not a control: 8899/8900 are **not** in
    `networking.firewall.allowedTCPPorts`, so off-host SYNs are dropped — measured from the
    laptop 2026-08-25, `8899 CLOSED, 8900 CLOSED` (`nix/home.nix:3005-3014`). A copied bind is
    therefore workbench-only *by firewall accident of the pattern*, not by design — and the
    original k8s-ingress plan would have removed it.

**A4 🔴 REOPENED — xterm.js contradicts "vanilla JS, no build step".** xterm.js is **not** in
this repo (the only `xterm` greps that hit are substring noise — `"xterm"` as a probe string in
a security test) and there is **no frontend build step anywhere** under `scripts/`. Adopting it
means vendoring a third-party bundle — the first in the repo — or adding a build. Either is a
departure from the constraint stated one line above it in the original draft. Decide it out
loud.

**A5 The port table was wrong.** The draft listed browser-bridge **and**
browser-activity-receiver both on 8787 — two servers on one port, which is the tell. Declared
and measured (`nix/home.nix:3030-3032`, `ss -lptn` 2026-08-25): **8787** activity-receiver,
**8788** browser-bridge, **8791** dl-router, **8793**, **8899** initiatives-viewer, **8900**
present-serve, **8931**. Note a new server's port is **test-gated**:
`scripts/tests/test_present_units.py` pins declared ports under `nix/`, so picking one is a
change with a test, not a free choice.

**A6 The laptop address was the unroutable one.** `192.168.50.155` is LAN-only, same-network.
The routable target used by every other consumer is **`zach@10.42.0.100`** (nebula).
`scripts/session-manager:364-372` carries a 🔴 warning that `10.42.0.10` is the homelab
*gateway*, not the laptop, and that getting this wrong **does not fail loudly**.

**A7 Step 1 would have reinvented a 4,395-line tested collector.** See "Build on what exists".

**A8 A related proposal is unmentioned and would invalidate the read model.**
`claudedocs/proposal-tmux-server-multiplexing.md` (2026-08-14, Draft) proposes **per-i3-workspace
tmux servers**. A read model that assumes one tmux server per host breaks under it. If that
proposal is live, this design depends on it; if it is dead, say so here.

## Build on what exists (do not reinvent)

| Need | Already exists | Where |
|---|---|---|
| Cross-host tmux read model | `session-manager` SSHes to the laptop, runs `tmux list-panes -a` + `list-windows -a` on **both** hosts, emits `--json` as `report["hosts"][{workbench,laptop}]["windows"]`. 4,395 lines, test-covered. | `scripts/session-manager`, `scripts/tests/test_session_manager.py` |
| Live bidirectional browser ↔ local-process channel on stdlib | `browser-bridge`'s long-poll command queue (`GET /poll` blocks ~25s → 204 → re-poll; `POST /cmd` enqueues, `POST /result` returns) | `scripts/browser-bridge/server.py` |
| A workbench-hosted LAN web UI as a nix-managed unit | `initiatives-viewer` — `systemd.user.services`, `serverMode`-gated, `X-Restart-Triggers`, pinned python312 | `nix/home.nix:2922+`, `scripts/initiatives/viewer.py` |
| Claude-session detection inside panes | `claude_sessions.py` (shared; also feeds the ▦ bar pill) | `scripts/lib/claude_sessions.py` |

Hazards `session-manager` already encodes that a fresh collector would rediscover:
- `list-panes` and `list-windows` are **two non-atomic calls** — the join can tear.
- `reachable` / `error` describe the **first** call only; one call succeeding says nothing
  about the other.

Two more from the analyze-service index (**recall — verify before relying on**):
- tmux **window ids restart at `@0` when the tmux server restarts**, so a row needs the server
  pid as a sentinel or a post-reboot `@41` inherits a dead session's identity and a multi-day age.
- `$TMUX_PANE` can be set while `tmux display-message` **fails**, landing a partial record on
  top of a good one and silently un-joining the window.

## Architecture decisions
- **Backend**: Python stdlib `http.server` — **stands**. Codebase convention; all existing
  servers are stdlib `BaseHTTPRequestHandler` + `ThreadingHTTPServer`.
- **Frontend**: Vanilla JS + CSS — **stands**. Terminal widget is **REOPENED (A4)**.
- **Transport**: **REOPENED (A1)** — long-poll vs WebSocket vs "break the constraint".
- **Multi-host**: single backend on the workbench, laptop over SSH at **`zach@10.42.0.100`**
  — stands, corrected address (A6). Source the data from `session-manager` (A7).
- **Deploy**: **REOPENED (A2)** — `systemd.user.services` on the workbench (the
  `initiatives-viewer` shape) vs a cluster deploy, and if a cluster, **which** one.
- **Auth**: **UNDECIDED, and required before `send-keys` ships (A3).**

## tmux API surface to wrap
| Command | Purpose |
|---|---|
| `list-sessions -F '...'` | Read model: all sessions with metadata |
| `list-windows -t <s> -F '...'` | Windows per session |
| `list-panes -t <s>:<w> -F '...'` | Panes per window |
| `capture-pane -t <target> -p` | Live pane content for previews |
| `send-keys -t <target>` | Send input to a pane — **gated on A3** |
| `rename-session` / `rename-window` | Organize |
| `swap-window` / `move-window` | Reorder/regroup |
| `kill-session` / `kill-window` | Cleanup — **gated on A3** |
| `new-session` / `new-window` | Create |

## What exists today (verified 2026-08-26 against `5426fe54`)
- **No web frameworks** — all servers are stdlib `BaseHTTPRequestHandler` + `ThreadingHTTPServer`.
- **6 existing servers**, ports corrected per A5.
- **Nix deployment pattern** well established: **25** `systemd.user.services` blocks in
  `nix/home.nix`, `Type="simple"`, pinned interpreter, `X-Restart-Triggers`.
- **Python 3.12** is the service standard (27 `python312` references in `nix/home.nix`).
- **Node.js 26** available (`pkgs.nodejs_26`) — but see A4: no frontend build step exists today.
- **`.tmux.conf`**: 222 lines, no custom socket, 20 scratchpad slots, auto-rename shows `●` for
  claude processes, Gruvbox theme.
- **Two hosts**: workbench, and the laptop at `zach@10.42.0.100`.
- **tmux snapshot**: workbench 22 sessions / 2 attached; laptop 15 sessions / 2 attached, all
  created within the same minute (likely a bulk restore — may need special handling).

## Next steps (re-ranked)
1. **Resolve the four REOPENED decisions (A1–A4)** — transport, deploy target, auth model,
   terminal widget. They are upstream of every line of code; picking wrong costs the prototype.
   *(Zach has further project criteria to add — resolve these together with those.)*
2. **Ship the laptop** — `scripts/ship.sh` clears the live rc 10. Unrelated to this work, but
   it is the standing drift and it is one command.
3. **Prototype the read model as a thin layer over `session-manager --json`** — decide
   import-as-library vs shell-out vs fork-the-helpers, then `scripts/tmux-webapp/server.py`
   serving `/api/sessions`. No new collector.
4. **Live-interaction layer** — per A1's outcome; `capture-pane` streaming + input, behind A3's
   auth decision.
5. **Frontend** — session grid, window sub-cards, pane mini-maps; terminal widget per A4.
6. **Organization ops** — rename / move / swap / kill endpoints + UI, destructive ones behind A3.
7. **Deploy** — per A2's outcome. If it is the `initiatives-viewer` shape: a
   `systemd.user.services` block in `nix/home.nix` plus a port that satisfies
   `scripts/tests/test_present_units.py`.

## Gotchas / decisions / dead-ends
- No new backend frameworks — stdlib only (codebase-wide convention). This is what makes A1 a
  real fork rather than a preference.
- `capture-pane` is the expensive call — debounce/batch across panes.
- The laptop is nebula-only and often not on the network; `session-manager` already buys a
  timeout for that case rather than hanging.
- Do not bind `192.168.50.94` — that is a homelab node hosting the kube-apiserver and
  NodePorts; it is not assignable on the workbench and binding it **crash-loops the unit**. It
  already cost `initiatives-viewer` an outage (`nix/home.nix:3024-3028`).
- A new file must be `git add`ed or the flake silently omits it from the deploy.

## How to verify
- `curl localhost:<port>/api/sessions` returns JSON with workbench sessions.
- `curl 'localhost:<port>/api/sessions?host=laptop'` returns laptop sessions over SSH — and
  returns a *timeout error*, not a hang, when the laptop is off-nebula.
- Cross-check the response against `session-manager --json` for the same instant; a divergence
  means the wrapper is re-deriving rather than delegating.
- Browser shows the session grid with live pane previews.
- Clicking a pane opens a terminal that can type into the tmux pane — **only after A3**.
- Whatever A2 decides: confirm the unit is `active` (not `activating`) **and** that the process
  holding the port is the one the unit started (`ss -lptn 'sport = :<port>'` → PID →
  `/proc/<pid>/cgroup`). A deploy reporting success is a claim about the deploy, not the consumer.
