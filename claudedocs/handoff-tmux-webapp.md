---
---
# Handoff: tmux-webapp — 2026-08-26

## Goal
Build a webapp that visually organizes and provides live terminal interaction with tmux sessions across two machines (workbench + laptop). Single unified view. Deployed to the homelab cluster.

## State now
- Branch: `main` on `devrc` (no PR yet)
- Nothing built yet — this is a greenfield project, designed this session
- 2 untracked files on workbench (unrelated: `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`, `scripts/dl-router/tests/load_test_store.sh`)
- 2 modified tracked files (unrelated: `claudedocs/close-the-loop/STATE.md`, `claudedocs/the-algorithm-applied-2026-06-17.md`)
- Drift: rc17 on workbench — `homelab-talos/containers/clawgate` built source 1 behind `origin/trunk`. Fix: `git -C $HOMELAB pull --ff-only` then `home-manager switch`

## Architecture decisions (this session)
- **Backend**: Python stdlib `http.server` (no new frameworks — codebase convention, all 6 existing servers use stdlib)
- **Frontend**: Vanilla JS + CSS, xterm.js for live terminal panes
- **Transport**: REST for read model, WebSocket for live terminal I/O and real-time updates
- **Multi-host**: Single backend on workbench, SSH to laptop for its tmux data
- **Deploy**: Homelab cluster (Kubernetes), not just a systemd user service

## tmux API surface to wrap
| Command | Purpose |
|---|---|
| `list-sessions -F '...'` | Read model: all sessions with metadata |
| `list-windows -t <s> -F '...'` | Windows per session |
| `list-panes -t <s>:<w> -F '...'` | Panes per window |
| `capture-pane -t <target> -p` | Live pane content for previews |
| `send-keys -t <target>` | Send input to a pane (live interaction) |
| `rename-session` / `rename-window` | Organize |
| `swap-window` / `move-window` | Reorder/regroup |
| `kill-session` / `kill-window` | Cleanup |
| `new-session` / `new-window` | Create |

## What exists today (research findings)
- **No web frameworks** in codebase — all servers are stdlib `BaseHTTPRequestHandler` + `ThreadingHTTPServer`
- **6 existing servers** follow a consistent pattern: stdlib Python, systemd user service, loopback-bound
  - `browser-bridge` (8787), `dl-router` (8791), `initiatives-viewer` (8899), `present-serve` (8900), `browser-activity-receiver` (8787), `subsystem-store-api`
- **Nix deployment pattern** well-established in `nix/home.nix`: `systemd.user.services.<name>` blocks with `Type="simple"`, pinned Python interpreter, `X-Restart-Triggers`
- **Python 3.12** is the standard for services (`pkgs.python312`)
- **Node.js 26** available for frontend tooling if needed
- **tmux config** (.tmux.conf): 222 lines, no custom socket, 20 scratchpad slots, auto-rename shows `●` for claude processes, Gruvbox theme
- **Two hosts**: workbench (workbench) and laptop (192.168.50.155)

## Current tmux state (snapshot)
**Workbench**: 22 sessions, 2 attached (`datapacket-talos-2`, `scratch13`)
**Laptop**: 15 sessions, 2 attached (`vetr`, `scratch5`), all created at same time (Aug 25 ~21:35, likely bulk restore)

## Next steps (ranked)
1. **Prototype the backend** — `scripts/tmux-webapp/server.py`: stdlib HTTP server with REST endpoints for session/window/pane listing. SSH to laptop for remote tmux data. (`scripts/tmux-webapp/`)
2. **Add WebSocket layer** — live terminal I/O via `send-keys` + `capture-pane` streaming, real-time session list updates
3. **Build the frontend** — vanilla HTML/JS/CSS. Session grid layout, window sub-cards, pane mini-maps, xterm.js for live interaction
4. **Add organization ops** — rename, move, swap, kill endpoints + UI controls
5. **Deploy to homelab cluster** — Kubernetes manifest (Deployment + Service), nix derivation or raw YAML in `nix/system/`
6. **Add to `nix/home.nix`** — systemd user service for local dev, or cluster deploy as primary target

## Gotchas / decisions / dead-ends
- No new web frameworks — stdlib only (codebase-wide convention)
- No framework = no build step on frontend either (vanilla JS)
- Multi-host SSH: backend must handle laptop connectivity (SSH key auth assumed)
- tmux `capture-pane` is the expensive call — debounce/batch for multiple panes
- Deploying to homelab cluster means it's a K8s service, not just local — need container image or nix-based deployment
- Laptop sessions all created at same timestamp (bulk restore?) — may need special handling

## How to verify
- `curl localhost:<port>/api/sessions` returns JSON with workbench sessions
- `curl localhost:<port>/api/sessions?host=laptop` returns laptop sessions via SSH
- Browser shows session grid with live pane previews
- Clicking a pane opens xterm.js terminal that can type into the tmux pane
- Deployed to homelab: accessible via cluster IP/ingress
