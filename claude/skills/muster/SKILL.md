---
name: muster
description: "Operate muster — the task board / agent / runbook service split OUT of clawgate onto `:30306`. Use for: muster, a clawgate task, \"read and evaluate task N\", dispatch an agent, runbooks, `clawgatectl task`, a task route that 404s. Approvals are `clawgate`."
---

# muster — the task board, agent dispatch and runbooks

Tasks/Repos/Agents, agent dispatch, runbooks with their approval gates and the machine Task API
producers post work into were **extracted out of clawgate** (the permission router) into their own
service, **muster** — own repo, namespace, Postgres, image and version line. Everything here is
muster's; the approval half is the **`clawgate`** skill, and the split is the first thing to get
right.

## 🔴 TWO SERVICES, TWO BASES — pick the base from the ROUTE, never from habit

| service | LAN base | env var | owns |
|---|---|---|---|
| **muster** — TASK/agent/runbook service (`github.com/ZacxDev/muster`, ns `muster`) | `http://192.168.50.250:30306` | 🔴 **`CLAWGATE_TASK_API_URL`** | `/api/tasks*`, `/api/agents*`, `/agent/task*`, `/api/tags`, `/api/projects`, `/api/sessions/{id}/tasks`, the `/tasks` UI, `/agents`, `/repos`, `/runbooks` |
| **clawgate** — the permission ROUTER | `http://192.168.50.250:30302` | `CLAWGATE_API_URL` | approvals (`/requests`, `/api/send`, `/api/gate`, `/api/response/{id}`, `/api/auto-approve*`, `/api/push/*`), `/api/notify`, `/api/suggest`, `/api/attention*`, `/api/layout*`, `/api/term*`, `/api/tmux*`, `/api/transcripts*` |

🔴 **A task-side path on the ROUTER base is a silent 404 — the wrong base does not error.**
Measured 2026-09-29, clawgate **0.8.65** / muster **0.2.0**: the task paths above answer **404** on
`:30302` and **401** on `:30306` (they exist, auth-gated); the reverse holds for the router's;
`/zzz-control` 404s on **both** (negative control). 🔑 **Tell the two 404s apart by BODY**:
`404 page not found` (text/plain, `clawgatectl` **rc 7**) = wrong base; `{"error":"task not found"}`
(JSON, rc 4) = right base, absent task — so **rc 7 on a `task`/`agent` verb is a base-URL symptom
until proven otherwise.** ⚠ **One `CLAWGATE_HOOK_TOKEN` opens BOTH servers**, so a working token
proves nothing about which one you reached. 🔴 **Never repoint `CLAWGATE_API_URL` at muster** —
`clawgate-hook.sh` reads it for `/api/send`. `clawgatectl` picks the base per route; devrc's ledger
is `scripts/lib/clawgate_tasks.py` (`TASK_API_URL_VARS` / `ROUTER_API_URL_VARS`). Per-route
inventory + the full discriminator table: `task-api.md`.

⚠ **Every code cite and every `0.7.x`/`0.8.x` measurement in those files PREDATES the extraction** —
best record of what the behaviour WAS, but each per-route semantic is now an unre-measured claim
about muster `0.2.0`. Re-probe `:30306` before betting on one (`task-api.md`).

## Reference + flow files
🔴 **They live under the `clawgate` skill's directory, not this one** — muster was extracted from
clawgate and its documentation has not been moved with it. The paths below are the DEPLOYED ones,
and every bare `<name>.md` cited below means `~/.claude/skills/clawgate/reference/<name>.md`.

| file | read it when |
|---|---|
| `~/.claude/skills/clawgate/reference/task-api.md` | **writing/debugging a producer**; `clawgatectl` + exit codes; the `/api/*` inventory with its auth **and which service serves it**; tag grammar |
| `~/.claude/skills/clawgate/reference/agent-dispatch.md` | debugging the agent loop; `POST /agents`; the sandbox fixture; a silent non-start |
| `~/.claude/skills/clawgate/reference/architecture.md` | changing agents / repos / runbooks / privilege / native tools; muster's OWN Postgres |
| `~/.claude/skills/clawgate/reference/auth-doors.md` | 🔴 which door takes which credential; the retraction, measured BOTH ways |
| `~/.claude/skills/clawgate/reference/agent-hardening.md` | locking down a **homelab** kubeclaw devpod (netpol: Cilium) |
| `~/.claude/skills/clawgate/reference/element-references.md` | a task body carries extension-picked element refs |
| `~/.claude/skills/clawgate/reference/chief.md` | driving chief: verbs, creds, arming — ⚠ it SPANS both services |
| `~/.claude/skills/clawgate/reference/troubleshooting.md` | the four AGENT symptoms — and muster's RBAC: SA `muster`, ClusterRole `muster-agents`, **not** `clawgate-agents` |
| `~/.claude/skills/clawgate/reference/internals.md` | Go code: the two `taskTitle`s, migrations — **look in muster's tree, not clawgate's** |

## Flow files
`flows/` = PROCEDURES you execute (`reference/` = FACTS you verify against). A flow does not
auto-fire — something must name it.

| file | run it when |
|---|---|
| `~/.claude/skills/clawgate/flows/task-authoring.md` | **CREATING a task** — pre-verify → interview → recommend → tags → confirm → create. 🔴 A PreToolUse hook DENIES a create with no `## Acceptance criteria`, or an unreadable body. Override `CLAWGATE_NO_INTERVIEW=1`. |
| `~/.claude/skills/clawgate/flows/task-pickup.md` | **PICKING UP a task** — the full ritual, criteria detector, ordering trap and comment rules. See `task pickup` below, which owns the status gate. |

## Key facts (verify before asserting)

| Thing | Value |
|---|---|
| Source | 🔴 **its OWN repo, `github.com/ZacxDev/muster`** — a `git grep` in `containers/clawgate` is no evidence about a task route |
| Cluster | **workbench**, ns **`muster`** — tasks/agents + its OWN Postgres (`muster-postgres`, a SEPARATE database from the router's) · dispatched agents in ns **`devpod-<agent-name>`** |
| Image / manifest | `harbor.homelab.lan/library/muster:<ver>@sha256:…` in `clusters/workbench/apps/muster/` — a **DIGEST** pin on a separate version line. Bumping clawgate deploys nothing of muster |
| LAN URL | `http://192.168.50.250:30306` (NodePort) — the `/tasks` UI and every `/api/tasks*` · `/api/agents*` · `/agent/task*` route |
| 🔴 Machine client | **`clawgatectl`** (`nix/pkgs/tools/clawgatectl.nix`; on PATH after a switch), also spelled `muster`. 🔴 **Built from a LOCAL tree, so it can be present but STALE — a behind checkout ships a binary MISSING verbs that prints help and exits 0 under a plausible version.** JSON on stdout; rc 0–8; else curl. Staleness closure: `task-api.md` |
| 🔴 kubeconfig is PER-HOST | never hardcode — `ls` both, take the one that EXISTS (`troubleshooting.md`) |

🔴 **TWO DOORS — a working hook token proves NOTHING about the UI.** `/api/*` takes
`Bearer $CLAWGATE_HOOK_TOKEN`; `/tasks*` and the UI take a **session cookie**. muster refuses with
`401` + JSON where the router redirects `303 → /login`, so a redirect-vs-401 check is NOT reusable
across the two. ⚠ Measured WRONG IN BOTH DIRECTIONS — re-measure: `auth-doors.md`.

## status
```bash
KC=$(ls ~/workspace/homelab-{talos,infra}/workbench-kubeconfig 2>/dev/null | head -1)  # PER-HOST
kubectl --kubeconfig $KC -n muster get pods -o wide
curl -s http://192.168.50.250:30306/health   # muster's OWN version + pin
```
⚠ `clawgatectl health` reports the **ROUTER** only. A healthy router is **not** evidence the task
board is up, and vice versa.

## task pickup — "read and evaluate clawgate task N", then "local dispatch"
🔴 **Run `~/.claude/skills/clawgate/flows/task-pickup.md`** — the comment/status ritual is NOT
optional and NOT a thing to be asked for. Run it unprompted. The bash block, the criteria detector,
the frozen-verdict rule, the completion-comment shape and the ordering trap are all there.

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

## machine (hook-token) Task API — on `$CLAWGATE_TASK_API_URL` (`:30306`)
🔴 Every route below 404s on the router base. `clawgatectl` resolves it; a hand-rolled `curl` must.
🔴 **Authoring one? `flows/task-authoring.md` FIRST** — a hook denies a criteria-less create.
Read/create with `clawgatectl task ls --summary [--status open --tag t --limit n]` · `task get <id>`
· `task create --body …`; **`--summary`/`--status`/`--limit` filter SERVER-side** (re-measured 0.7.87
— was false at 0.7.85). Write with `clawgatectl task status` / `task comment`. Every remaining verb
(`PATCH`, `DELETE`, comment DELETE, `/api/tags`, `/api/projects`) is still curl —
`Authorization: Bearer $CLAWGATE_HOOK_TOKEN` against **`$CLAWGATE_TASK_API_URL`** (`task-api.md`).
⚠ **`/api/notify` is NOT one of them: it is a ROUTER route** and used to be listed here — it pushes
a card, it does not touch a task. Statuses are exactly `open` / `in_progress` /
`ready_for_review` / `complete` — no `dismissed`; dismissing deletes.

🔴 **ONE path deletes a task and TEARS DOWN its live dispatched agent pod**: `DELETE /api/tasks/{id}`
(`dismissTask`; **no in-progress guard, deliberately**). It is `requireHookToken`, not
`requireSession` — which does NOT make it safer: every agent and hook here already holds that token
(`auth-doors.md`). ⚠ **Its automated twin is RETIRED — do not re-derive it: NOTHING destroys a task
or an agent pod on a timer.** The reaper tags `stale` instead (`task-api.md`).

⚠ **Tags are hard-validated: one invalid tag or unknown `runbook:` is a hard 400 that fails the whole
create** — a load-bearing wire contract producers key their retry on.
⚠ **A task body may carry extension-picked element references** — never search the selector first
(`element-references.md`).

⚠ **Task↔session threads (#357).** `GET /api/tasks/{id}/sessions` **404s BY DESIGN** (pinned by
`TestNoForwardSessionsSubRoute`) — the thread is EMBEDDED on task reads, so
`clawgatectl task get N | jq .sessions` answers *"which sessions worked task N"*. 🔴 **Since the
split, a by-design 404 and a wrong-base 404 are the SAME observable** — before reading any
task-route 404 as "absent by design", confirm the base you used was `:30306`. 🔴 Membership
OVER-reports: a subagent inherits the parent's id, and a mere READ links you.

**Writing/debugging a producer? Load `task-api.md`** — per-op semantics + status codes,
409/immutability, the author allowlist, provenance, tag grammar, the route×auth inventory.

## agent dispatch
🔴 **Current STATUS of the loop lives in `~/workspace/homelab-talos/containers/clawgate/HANDOFF.md`,
not here** — claims here have been superseded within two days, twice; GREP it, never read it whole
(~190 KB). `agent-dispatch.md` has the sandbox fixture, the agent image's absent toolchain and the
dispatch `curl`. Durable only:
- **The loop DOES close unattended** (two real runs). "The 5-minute kickoff deadline is why it never
  worked" is DEAD — don't reopen it.
- **`POST /agents` is FORM-ENCODED, not JSON** (hence no `clawgatectl` verb), is on
  `http://192.168.50.250:30306/agents` (**the router 404s it**), and needs a SESSION — a
  credential-less LAN call returns `401` (`auth-doors.md`). 🔴 A future webhook needs a **separate
  hostname**, never a path bypass on a public clawgate/muster host — that would put dispatch on the
  open net.
- ⚠ **A dispatch that cannot START surfaces almost nothing** — the agent goes `error` but the task
  stays `in_progress`, `kicked_off` stays `false`, and nothing pushes. **Read the AGENT POD LOGS
  first — ns `devpod-<agent-name>`, not ns `clawgate`** (`agent-dispatch.md`).
- ⚠ **Provisioned agents are NOT hardened by default** — chart defaults are
  `networkPolicy.enabled: false` + `tls.verify: false`, and that netpol is **Cilium-only** while
  workbench (where muster provisions) has **no Cilium** — so `agent-hardening.md` is **homelab**.

⚠ **These docs drift from the code in BOTH directions** — weeks EARLY once, six releases BEHIND
once. Never treat a doc claim as evidence: `/health` for the live pin, `git grep` for the feature.
