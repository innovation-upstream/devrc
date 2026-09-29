---
name: clawgate
description: "Operate clawgate (Claude Code's permission router) and muster, the SEPARATE task/agent/runbook service split out of it. Status, send-a-test, push/SSE logs, deploy, approval hook, task board. Use for: clawgate, clawgate.zacx.dev, muster, clawgatectl, remote approval, the PermissionRequest hook, push notifications for permission prompts."
---

# clawgate operations

Self-hosted Go + htmx PWA routing Claude Code permission prompts to Zach's phone (it ROUTES; does
NOT gate — `telemetry.md`). 🔴 **Tasks/agents/runbooks are NO LONGER clawgate's** — that half was
extracted into a separate service, **muster**; this skill covers both, and the split is the first
thing to get right.

## 🔴 TWO SERVICES, TWO BASES — pick the base from the ROUTE, never from habit

| service | LAN base | env var | owns |
|---|---|---|---|
| **clawgate** — the permission ROUTER | `http://192.168.50.250:30302` | `CLAWGATE_API_URL` | approvals (`/requests`, `/api/send`, `/api/gate`, `/api/response/{id}`, `/api/auto-approve*`, `/api/push/*`), `/api/notify`, `/api/suggest`, `/api/attention*`, `/api/layout*`, `/api/term*`, `/api/tmux*`, `/api/transcripts*` |
| **muster** — TASK/agent/runbook service (`github.com/ZacxDev/muster`, ns `muster`) | `http://192.168.50.250:30306` | 🔴 **`CLAWGATE_TASK_API_URL`** | `/api/tasks*`, `/api/agents*`, `/agent/task*`, `/api/tags`, `/api/projects`, `/api/sessions/{id}/tasks`, the `/tasks` UI, `/agents`, `/repos`, `/runbooks` |

🔴 **A task-side path on the ROUTER base is a silent 404 — the wrong base does not error.**
Measured 2026-09-29, clawgate **0.8.65** / muster **0.2.0**:
`:30302/api/tasks|/api/agents|/agent/task|/tasks` → **404**; the same four on `:30306` → **401**
(exist, auth-gated); the reverse holds (`:30306/api/send|/api/attention` → 404); `/zzz-control`
404s on **both** (negative control). 🔑 **Tell the two 404s apart by BODY**: `404 page not found`
(text/plain, `clawgatectl` **rc 7**) = wrong base; `{"error":"task not found"}` (JSON, rc 4) = right
base, absent task. Per-route inventory + the discriminator table: `task-api.md`.
⚠ **One `CLAWGATE_HOOK_TOKEN` opens BOTH servers**, so a working token proves nothing about which
one you reached. 🔴 **Never repoint `CLAWGATE_API_URL` at muster** — `clawgate-hook.sh` reads it
for `/api/send` (`hooks.md`). `clawgatectl` picks the base per route; devrc's ledger is
`scripts/lib/clawgate_tasks.py` (`TASK_API_URL_VARS` / `ROUTER_API_URL_VARS`).

🔴 **Point-in-time state: `~/workspace/homelab-talos/containers/clawgate/HANDOFF.md` —
GREP it for the section you need, never read it whole (~190 KB, lower half superseded).**

⚠ **This skill drifts from the code in BOTH directions** — weeks EARLY once, six releases BEHIND
once. Never treat a doc claim as evidence: `clawgatectl health` for the live pin, `git grep` for the
feature.

## Reference files
`devrc/claude/skills/clawgate/reference/` → `~/.claude/skills/clawgate/` after a switch.

| file | read it when |
|---|---|
| `deploy.md` | **building + shipping a version**: manifest-vs-code + CSS-cwd traps; chart sync |
| `task-api.md` | **writing/debugging a producer** against **muster**; `clawgatectl` + exit codes; the `/api/*` inventory with its auth **and which service serves it**; tag grammar |
| `agent-dispatch.md` | debugging the agent loop; `POST /agents` (muster); the sandbox fixture; a silent non-start |
| `extension.md` | you changed the extension, or need the loaded build |
| `changelog.md` | *when* a feature landed / why a decision stands |
| `architecture.md` | changing agents / repos / runbooks / privilege / native tools |
| `internals.md` | Go code: markdown renderer, the two `taskTitle`s, migrations |
| `telemetry.md` | metrics/logs missing; adding an event; red CI check |
| `troubleshooting.md` | symptoms: push, PWA icon, stale SW, RBAC, kubeconfig |
| `hooks.md` | `PermissionRequest` semantics; defer gates; installing hooks elsewhere; Stop / 💡 |
| `agent-hardening.md` | locking down a **homelab** kubeclaw devpod (netpol: Cilium) |
| `cross-session-reach.md` | reach another session; 🔴 `term send` RUNS what you type |
| `element-references.md` | task body carries extension-picked element refs |
| `prior-work-recall.md` | the `prior work` step: hit counts, flags, why the guard is an `if` |
| `auth-doors.md` | 🔴 which door takes which credential; the retraction, measured BOTH ways |
| `chief.md` | driving chief: verbs, creds, arming |

## Flow files
`flows/` = PROCEDURES you execute (`reference/` = FACTS you verify against). A flow does not
auto-fire — something must name it.

| file | run it when |
|---|---|
| `task-authoring.md` | **CREATING a task** — pre-verify → interview → recommend → tags → confirm → create. 🔴 A PreToolUse hook DENIES a create with no `## Acceptance criteria`, or an unreadable body. Override `CLAWGATE_NO_INTERVIEW=1`. |
| `task-pickup.md` | **PICKING UP a task** — the full ritual, criteria detector, ordering trap and comment rules. See the `task pickup` section below, which owns the status gate. |

Memories: `clawgate-phase2` · `clawgate-phase3` · `clawgate-runbooks` ·
`clawgate-loop-validation` · `authelia-passkey-sso`.

## Key facts (verify before asserting)

| Thing | Value |
|---|---|
| Source | `~/workspace/homelab-talos/containers/clawgate/` (module `github.com/zacxdev/clawgate`). 🔴 **muster is its OWN repo (`github.com/ZacxDev/muster`)** — a `git grep` here is no evidence about a task route |
| Hook scripts | `hook/clawgate-hook.sh` (PermissionRequest → `/api/send`) + `hook/clawgate-stop-hook.sh` (Stop → `/api/suggest`); both read `~/.claude/clawgate.env` |
| Cluster | **workbench**. ns `clawgate` (router + its Postgres) · ns **`muster`** (tasks/agents + its OWN Postgres — `muster-postgres`, a SEPARATE database) · dispatched agents in ns **`devpod-<agent-name>`** |
| 🔴 kubeconfig is PER-HOST | never hardcode — `ls` both, take the one that EXISTS; the other is **absent** on each host. Paths + telling the hosts apart: `troubleshooting.md` |
| Image / manifest | `harbor.homelab.lan/library/clawgate:<ver>` in `clusters/workbench/apps/clawgate/deployment.yaml` (Flux from `trunk`). **muster: `harbor.homelab.lan/library/muster:<ver>@sha256:…` in `clusters/workbench/apps/muster/` — a DIGEST pin, separate version line.** Bumping one deploys nothing of the other |
| LAN URL (hook + approvals UI) | **clawgate** `http://192.168.50.250:30302` (NodePort) — 🔴 the UI needs a SESSION, `/api/*` takes the hook token; see the two-door block below |
| LAN URL (tasks + agents) | **muster** `http://192.168.50.250:30306` (NodePort) — the `/tasks` UI and every `/api/tasks*` · `/api/agents*` · `/agent/task*` route. Same hook token, DIFFERENT server |
| Public / nebula URL | `https://clawgate.zacx.dev` behind **Authelia passkey** (portal `login.zacx.dev`); laptop `http://10.42.0.10:8109` (homelab gateway) |
| Hook events | `PermissionRequest` (`CLAWGATE_REMOTE_APPROVAL=off`) + `Stop` (async, `CLAWGATE_SUGGEST=off`), both in `~/.claude/settings.json`, ON by default. 🔴 `Stop` carries OTHER hooks — **preserve every non-clawgate one**; DERIVE, never count: `jq -r '.hooks.Stop[].hooks[].command'` |
| 🔴 Machine client | **`clawgatectl`** (`nix/pkgs/tools/clawgatectl.nix`; on PATH after a switch). 🔴 **Built from a LOCAL tree, so it can be present but STALE — a behind checkout ships a binary MISSING verbs that prints help and exits 0 under a plausible version.** JSON on stdout; rc 0–8; else curl. Commands + staleness closure: `task-api.md` |

🔴 **TWO DOORS, DIFFERENT CREDENTIALS — a working hook token proves NOTHING about the UI.** `/api/*`
takes `Bearer $CLAWGATE_HOOK_TOKEN`; `/tasks*` (**on muster**) and both UIs take a **session
cookie** and refuse without one (public host: Authelia on top, whose headers clawgate does not trust).
Proving the API door open proves nothing about the human one — that is why the extension's "open in
clawgate" links were inert (PR #802).
🔴 **`POST /api/auto-approve-all` arms a global auto-approve window over EVERY future request in
EVERY project — never fire it to test a theory.**
⚠ **Measured WRONG IN BOTH DIRECTIONS — re-measure, never quote from memory.** Evidence, the
per-door table and the two corrected routes: `auth-doors.md`.

---

## prior work — has this been fought before? (run FIRST)
```bash
if command -v cairn >/dev/null; then cairn search 'clawgate' --scope homelab-talos; else echo "skipped: cairn unavailable"; fi
```
Past sessions' MEASUREMENTS — `RECALL, NOT LIVE OBSERVATION`, verify before acting. Also search
`clawgatectl` · `e2e` · `task api` · `deploy`: what a READER types, not the file you stand in. No
`cairn sync;` prefix (`search` syncs). 🔴 **Guard stays an `if`/`else`, never `&&`** (rc≠0 when
cairn is absent reads as this step FAILING; a bare `if` skips SILENTLY). `prior-work-recall.md`

## status
```bash
KC=$(ls ~/workspace/homelab-{talos,infra}/workbench-kubeconfig 2>/dev/null | head -1)  # PER-HOST
kubectl --kubeconfig $KC -n clawgate get pods -l app=clawgate -o wide
kubectl --kubeconfig $KC -n muster   get pods -o wide          # the TASK service — separate ns
clawgatectl health   # live version + uptime; rc 6 = unreachable, rc 8 = you hit the public host
curl -s http://192.168.50.250:30306/health   # muster's OWN version + pin — TWO services, two pins
```
⚠ `clawgatectl health` reports the ROUTER only. A healthy router is **not** evidence the task board is up.

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

## task pickup — "read and evaluate clawgate task N", then "local dispatch"
(The board is **muster**'s. The ritual below is unchanged; only the server it talks to moved.)
🔴 **Run `flows/task-pickup.md`** — the comment/status ritual is NOT optional and NOT a thing to be
asked for. Run it unprompted. The bash block, the criteria detector, the frozen-verdict rule, the
completion-comment shape, the ordering trap and the two-comments rule are all there.

🔴 **A hook ENFORCES this** (`clawgate-writeback-guard.py`): armed by the step-1 read, it **blocks
Stop** when work followed and a live re-read shows no `claude-code` comment since. Commenting
silences it; read-and-evaluate-only never fires. Its block message names the flow by deployed path:
`~/.claude/skills/clawgate/flows/task-pickup.md`.

🔴 **Status gate — the only place `complete` is ever yours to set.** Criteria are
**AUTHOR-SPECIFIED** only when the task body carries a `## Acceptance criteria` heading; anything
else means you **DERIVED** them, and that verdict is frozen at your first read.

| criteria | every criterion validated with evidence? | final status |
|---|---|---|
| AUTHOR-SPECIFIED | yes | **`complete`** |
| DERIVED | yes | **`ready_for_review`** — you must not grade an exam you wrote |
| either | **no** | **`ready_for_review`**, naming WHICH criterion and WHY it was not validatable |

## machine (hook-token) Task API — **muster's, on `$CLAWGATE_TASK_API_URL` (`:30306`)**
🔴 Every route below 404s on the router base. `clawgatectl` resolves it; a hand-rolled `curl` must.
🔴 **Authoring one? `flows/task-authoring.md` FIRST** — a hook denies a criteria-less create.
Read/create with `clawgatectl task ls --summary [--status open --tag t --limit n]` · `task get <id>`
· `task create --body …`; **`--summary`/`--status`/`--limit` filter SERVER-side** (re-measured 0.7.87
— was false at 0.7.85). Write status + comments with `clawgatectl task status` / `task comment`
(above). Every remaining verb (`PATCH`, `DELETE`, comment DELETE, `/api/tags`, `/api/projects`) is
still curl — `Authorization: Bearer $CLAWGATE_HOOK_TOKEN` against **`$CLAWGATE_TASK_API_URL`**
(`task-api.md`). ⚠ **`/api/notify` is NOT one of them: it is a ROUTER route** (`:30302`) and used to
be listed here — it pushes a card, it does not touch a task.
Statuses are exactly `open` / `in_progress` / `ready_for_review` / `complete` — no `dismissed`;
dismissing deletes.

🔴 **ONE path deletes a task and TEARS DOWN its live dispatched agent pod**: `DELETE /api/tasks/{id}`
(`dismissTask`; **no in-progress guard, deliberately**). It is `requireHookToken`, not
`requireSession` — which does NOT make it safer: every agent and hook here already holds that token
(`~/.claude/clawgate.env`). Why that correction runs opposite to the other one: `auth-doors.md`.
⚠ **Its automated twin is RETIRED — do not re-derive it: NOTHING destroys a task or an agent pod on
a timer.** The reaper tags `stale` instead; the 7d default is live but costs a tag (`task-api.md`).

⚠ **Tags are hard-validated: one invalid tag or unknown `runbook:` is a hard 400 that fails the whole
create** — a load-bearing wire contract producers key their retry on.
⚠ **A task body may carry extension-picked element references** — never search the selector first
(`element-references.md`).

⚠ **Task↔session threads (#357).** `GET /api/tasks/{id}/sessions` **404s BY DESIGN** (pinned by
`TestNoForwardSessionsSubRoute`) — the thread is EMBEDDED on task reads. 🔴 **Since the split, a
by-design 404 and a wrong-base 404 are the SAME observable** — before reading any task-route 404 as
"absent by design", confirm the base you used was `:30306`. So
`clawgatectl task get N | jq .sessions` answers *"which sessions worked task N"*; NOT UI-only.
🔴 Membership OVER-reports: a subagent inherits the parent's id, and a mere READ links you.
(#306's rejected-PATCH link is FIXED, live 0.7.99.) `task-api.md`

**Writing/debugging a producer? Load `task-api.md`** — per-op semantics + status codes,
409/immutability, the author allowlist, provenance, tag grammar, the route×auth inventory.

## agent dispatch — **muster's, on `:30306`**
🔴 **Current STATUS of the loop lives in `HANDOFF.md`, not here** — claims here have been superseded
within two days, twice. `agent-dispatch.md` has the sandbox fixture, the agent image's absent
toolchain and the dispatch `curl`. Durable facts only:
- **The loop DOES close unattended** (two real runs). "The 5-minute kickoff deadline is why it never
  worked" is DEAD — don't reopen it.
- **`POST /agents` is FORM-ENCODED, not JSON** (hence no `clawgatectl` verb), **is on muster
  (`:30306/agents` — the router 404s it)**, and needs a SESSION — a credential-less LAN call
  returns `401` (`auth-doors.md`). 🔴 A future webhook needs a **separate hostname**, never a path
  bypass on a public clawgate/muster host — that would put dispatch on the open net.
- ⚠ **A dispatch that cannot START surfaces almost nothing** — the agent goes `error` but the task
  stays `in_progress`, `kicked_off` stays `false`, and nothing pushes. **Read the AGENT POD LOGS
  first — ns `devpod-<agent-name>`, not ns `clawgate`** (`agent-dispatch.md`).

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
  clawgate-provisioned agents only after `make sync-chart` + rebuild + redeploy; `make check-chart`
  silently tests the wrong tree unless `~/workspace/kubeclaw` is synced FIRST.
- ⚠ **Clawgate-provisioned agents are NOT hardened by default**: chart defaults are
  `networkPolicy.enabled: false` + `tls.verify: false`; only the **pod-level** securityContext is
  empty (the container's IS set). 🔴 That netpol is **Cilium-only** and workbench — where clawgate
  provisions — has **no Cilium**, so `agent-hardening.md` is a **homelab** playbook.
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
