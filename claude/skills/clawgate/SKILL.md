---
name: clawgate
description: "Operate clawgate — the self-hosted Claude Code permission ROUTER. Status, send-a-test, push/SSE logs, deploy, the approval hook. Use for: clawgate, clawgate.zacx.dev, remote approval, the PermissionRequest hook, push notifications for permission prompts. Tasks/agents/runbooks are the separate `muster`."
---

# clawgate operations

Self-hosted Go + htmx PWA routing Claude Code permission prompts to Zach's phone (it ROUTES; does
NOT gate — `telemetry.md`).

## 🔴 THE TASK HALF IS NOT CLAWGATE'S — it is `muster`, on a DIFFERENT base
Tasks/Repos/Agents, agent dispatch and runbooks were **extracted into a separate service,
`muster`** (`github.com/ZacxDev/muster`, ns `muster`), with its own repo, Postgres, image and
version line. 🔴 **`/api/tasks*`, `/api/agents*`, `/agent/task*` and the `/tasks` UI are on
`http://192.168.50.250:30306` (`$CLAWGATE_TASK_API_URL`), NOT on the router's
`http://192.168.50.250:30302` (`$CLAWGATE_API_URL`)** — and on the wrong base they do not error,
they **404**. ⚠ One `CLAWGATE_HOOK_TOKEN` opens BOTH, so a working token proves nothing about which
server you reached; 🔴 **never repoint `CLAWGATE_API_URL` at muster** — `clawgate-hook.sh` reads it
for `/api/send` (`hooks.md`). **Doing anything with tasks, agents or runbooks? LOAD THE `muster`
SKILL** — it owns the base table, the 404-body discriminator, the pickup ritual and the status
gate. devrc's ledger is `scripts/lib/clawgate_tasks.py`
(`TASK_API_URL_VARS` / `ROUTER_API_URL_VARS`).

🔴 **Point-in-time state: `~/workspace/homelab-talos/containers/clawgate/HANDOFF.md` —
GREP it for the section you need, never read it whole (~190 KB, lower half superseded).**

⚠ **This skill drifts from the code in BOTH directions** — weeks EARLY once, six releases BEHIND
once. Never treat a doc claim as evidence: `clawgatectl health` for the live pin, `git grep` for the
feature.

## Reference files
`devrc/claude/skills/clawgate/reference/` → `~/.claude/skills/clawgate/` after a switch.
⚠ Several of these are **muster's** and the `muster` skill routes to them; they were not moved when
the service was.

| file | read it when |
|---|---|
| `deploy.md` | **building + shipping a version**: manifest-vs-code + CSS-cwd traps; chart sync |
| `extension.md` | you changed the extension, or need the loaded build |
| `changelog.md` | *when* a feature landed / why a decision stands |
| `architecture.md` | the public nginx routing; changing agents / repos / runbooks / privilege |
| `internals.md` | Go code: markdown renderer, the two `taskTitle`s, migrations |
| `telemetry.md` | metrics/logs missing; adding an event; red CI check |
| `troubleshooting.md` | symptoms: push, PWA icon, stale SW, RBAC, kubeconfig |
| `hooks.md` | `PermissionRequest` semantics; defer gates; installing hooks elsewhere; Stop / 💡 |
| `cross-session-reach.md` | reach another session; 🔴 `term send` RUNS what you type |
| `prior-work-recall.md` | the `prior work` step: hit counts, flags, why the guard is an `if` |
| `auth-doors.md` | 🔴 which door takes which credential; the retraction, measured BOTH ways |
| `chief.md` | driving chief: verbs, creds, arming — ⚠ it SPANS both services |

Memories: `clawgate-phase2` · `clawgate-phase3` · `clawgate-runbooks` ·
`clawgate-loop-validation` · `authelia-passkey-sso`.

## Key facts (verify before asserting)

| Thing | Value |
|---|---|
| Source | `~/workspace/homelab-talos/containers/clawgate/` (module `github.com/zacxdev/clawgate`) — **the router only**; muster is its OWN repo |
| Hook scripts | `hook/clawgate-hook.sh` (PermissionRequest → `/api/send`) + `hook/clawgate-stop-hook.sh` (Stop → `/api/suggest`); both read `~/.claude/clawgate.env` |
| Cluster | **workbench**, ns `clawgate` (router + its Postgres); ns `muster` is a different service with a different database |
| 🔴 kubeconfig is PER-HOST | never hardcode — `ls` both, take the one that EXISTS; the other is **absent** on each host. Paths + telling the hosts apart: `troubleshooting.md` |
| Image / manifest | `harbor.homelab.lan/library/clawgate:<ver>`, pinned in `clusters/workbench/apps/clawgate/deployment.yaml` (Flux from `trunk`). Bumping it deploys nothing of muster |
| LAN URL (hook + UI) | `http://192.168.50.250:30302` (NodePort) — 🔴 the UI needs a SESSION, `/api/*` takes the hook token; see the two-door block below |
| Public / nebula URL | `https://clawgate.zacx.dev` behind **Authelia passkey** (portal `login.zacx.dev`); laptop `http://10.42.0.10:8109` (homelab gateway) |
| Hook events | `PermissionRequest` (`CLAWGATE_REMOTE_APPROVAL=off`) + `Stop` (async, `CLAWGATE_SUGGEST=off`), both in `~/.claude/settings.json`, ON by default. 🔴 `Stop` carries OTHER hooks — **preserve every non-clawgate one**; DERIVE, never count: `jq -r '.hooks.Stop[].hooks[].command'` |
| 🔴 Machine client | **`clawgatectl`** (`nix/pkgs/tools/clawgatectl.nix`; on PATH after a switch). 🔴 **Built from a LOCAL tree, so it can be present but STALE — a behind checkout ships a binary MISSING verbs that prints help and exits 0 under a plausible version.** JSON on stdout; rc 0–8; else curl |

🔴 **TWO DOORS, DIFFERENT CREDENTIALS — a working hook token proves NOTHING about the UI.** `/api/*`
takes `Bearer $CLAWGATE_HOOK_TOKEN`; the UI takes a **session cookie** and answers `303 → /login`
without one (public host: Authelia on top, whose headers clawgate does not trust).
Proving the API door open proves nothing about the human one — that is why the extension's "open in
clawgate" links were inert (PR #802).
🔴 **`POST /api/auto-approve-all` arms a global auto-approve window over EVERY future request in
EVERY project — never fire it to test a theory.**
⚠ **Measured WRONG IN BOTH DIRECTIONS — re-measure, never quote from memory.** Evidence, the
per-door table and the two corrected routes: `auth-doors.md`.

---

## prior work — has this been fought before? (run FIRST)
```bash
$DEVRC/scripts/cairn-ops/read.sh search 'clawgate' --scope homelab-talos --if-available
```
Past sessions' MEASUREMENTS — `RECALL, NOT LIVE OBSERVATION`, verify before acting. Also search
`clawgatectl` · `e2e` · `task api` · `deploy`: what a READER types, not the file you stand in.
🔴 **`--if-available` makes an absent client a SKIP at rc 0** — the default is a refusal, which
is right for a mandated check and wrong for a preflight. `prior-work-recall.md`

## status
```bash
KC=$(ls ~/workspace/homelab-{talos,infra}/workbench-kubeconfig 2>/dev/null | head -1)  # PER-HOST
kubectl --kubeconfig $KC -n clawgate get pods -l app=clawgate -o wide
clawgatectl health   # live version + uptime; rc 6 = unreachable, rc 8 = you hit the public host
```
⚠ `clawgatectl health` reports the ROUTER only. A healthy router is **not** evidence the task board
is up — that is muster's `/health`, on its own base (`muster` skill).

## send a test
⚠ **No `clawgatectl` verb for `/api/send`** — stays curl. Creates a real pending request (card + Web
Push) via the hook token on the open LAN NodePort; for delivery, tail logs for
`push: delivered ... to N device(s)`.
```bash
HOOK=$(grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2)
curl -sf -X POST http://192.168.50.250:30302/api/send -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $HOOK" \
  -d '{"type":"permission","tool":"Bash","command":"echo test","host":"nixos","project":"clawgate"}' | jq .
```

## logs (push / SSE / subscriptions)
```bash
# $KC as in `status` above — PER-HOST, never hardcoded
kubectl --kubeconfig $KC -n clawgate logs -f deploy/clawgate | grep --line-buffered -iE 'subscription|delivered|push:|request created|decision recorded|could not'
```
(Run under Monitor for a live watch that notifies as events land.)

## deploy a new version
🔴 **GitOps from `trunk`: committing deploys the MANIFEST, not container CODE — silently.** The pin
is a literal tag with no image automation, so `git log` is NOT evidence the code is live; the live
pin and `clawgatectl health` are. **Load `deploy.md` first** —
version-from-the-live-pin, the ONE commit path (worktree off `origin/trunk`; never `git add -A`),
test gate, build/push, pin bump, the CSS-cwd trap that fakes ~25 e2e failures, chart sync.
⚠ `deploy.md` ships the **router**; muster has a separate repo, image and digest pin.

## hook management
- On by default, global. Off for one session: `CLAWGATE_REMOTE_APPROVAL=off`. Inspect:
  `jq '.hooks.PermissionRequest' ~/.claude/settings.json`.
- Tests, from `containers/clawgate/`: `nix-shell -p bats jq --run 'bats hook/tests/*.bats'`.
- **Fail-safe by design**: any error/timeout/unreachable server → defer to the terminal, so an
  outage never blocks Claude Code. ⚠ It also defers **without contacting the server** on
  `permission_mode` `bypassPermissions`/`plan` or tool `AskUserQuestion` — so "no card appeared" is
  not evidence of an outage.
- `hooks.md`: the full gate list, the exact JSON, why an approver comment is **record-only**.

## 🔴 gotchas
- 🔴 **Public routing rule**: clawgate runs on WORKBENCH, fronted by the homelab + production nebula
  gateways whose nginx must `proxy_pass` to the **NodePort IP `http://192.168.50.250:30302`**
  — NOT a `.svc.cluster.local` name, which doesn't resolve there and **crashes nginx, taking down ALL
  nebula-routed services**. Edit additively; a reload restarts `nebula-gateway` on **both** clusters
  (`architecture.md`).
- **The vendored kubeclaw chart is embedded at BUILD time** — a kubeclaw release reaches
  provisioned agents only after `make sync-chart` + rebuild + redeploy; `make check-chart`
  silently tests the wrong tree unless `~/workspace/kubeclaw` is synced FIRST.
- **Alloy has no auto-reloader** — if clawgate metrics vanish from homelab Prometheus, restart Alloy
  first (`telemetry.md`).
- **Red GitHub Actions checks on `homelab-infra` are NOISE**; real checks: Tekton (`tekton` skill).
  🔴 **`clawgate-ci` runs no Playwright — but `clawgate-e2e`, a SEPARATE check, DOES; "the browser
  layer is UNGATED" was FALSE.** ⚠ RUNS is not BLOCKS; `make e2e` without Docker self-skips most
  full-mode files and reads green — **COUNT**. `extension.md`.
- 🔴 **The browser extension does NOT ship via Flux — merging to `trunk` deploys NOTHING.** Brave
  loads it unpacked from `~/workspace/clawgate-extension/containers/clawgate/extension` (branch
  `clawgate-ext-local`, same path on **BOTH hosts**); deploy = `merge --ff-only origin/trunk` on both
  + reload Brave. 🔴 **Brave profiles load extensions from DIFFERENT paths, so the profile in front of
  you proves nothing** — and agents can't read `brave://`: never call a version or hotkey live
  without `extension.md`'s `Preferences` sweep (+ its 🔴 `git restore --worktree` trap).
