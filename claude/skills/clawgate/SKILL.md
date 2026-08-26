---
name: clawgate
description: "Operate clawgate — the self-hosted Claude Code permission router (plus Tasks/agents/runbooks). Status, send-a-test, push/SSE logs, build and deploy a version, toggle the approval hook, manage credentials/QR. Use for: clawgate, clawgate.zacx.dev, remote approval, the PermissionRequest approval hook, push notifications for permission prompts."
---

# clawgate operations

Self-hosted Go + htmx PWA routing Claude Code permission prompts to Zach's phone (it ROUTES;
does NOT gate — `telemetry.md`), grown into the **agent dispatch loop**: Tasks/Repos/Agents on Postgres,
agent self-service + privilege profiles + an Operator, runbooks with approval gates, and a machine
Task API producers post work into for one-tap Dispatch.

🔴 **Point-in-time state: `~/workspace/homelab-talos/containers/clawgate/HANDOFF.md` —
GREP it for the section you need, never read it whole (~190 KB, lower half superseded).**

⚠ **This skill drifts from the code in BOTH directions** — weeks EARLY once, six releases BEHIND
once. Never treat a doc claim as evidence: `clawgatectl health` for the live pin, `git grep` for
the feature.

## Reference files
`devrc/claude/skills/clawgate/reference/` (→ `~/.claude/skills/clawgate/` after a switch).

| file | read it when |
|---|---|
| `deploy.md` | **building + shipping a version**: manifest-vs-code + CSS-cwd traps; chart sync |
| `task-api.md` | **writing/debugging a producer**; `clawgatectl` + exit codes; **the 23-route `/api/*` inventory with its auth**; tag grammar |
| `agent-dispatch.md` | debugging the agent loop; `POST /agents`; the sandbox fixture; a silent non-start |
| `extension.md` | you changed the extension, or need to know which build is loaded |
| `changelog.md` | *when* a feature landed / why an old decision stands |
| `architecture.md` | changing agents / repos / runbooks / privilege / native tools / e2e |
| `internals.md` | changing Go code: markdown renderer, the two `taskTitle`s, migrations |
| `telemetry.md` | metrics/logs missing; adding an event; a red CI check |
| `troubleshooting.md` | symptoms: push, PWA icon, stale SW, RBAC, kubeconfig, agent model |
| `hooks.md` | `PermissionRequest` semantics; the defer gates; installing hooks elsewhere; Stop / 💡 |
| `agent-hardening.md` | locking down a **homelab** kubeclaw devpod (netpol needs Cilium) |
| `element-references.md` | a task body carries extension-picked element refs |

## Flow files
`flows/` = PROCEDURES you execute (`reference/` = FACTS you verify against). A flow does not
auto-fire — something must name it.

| file | run it when |
|---|---|
| `task-authoring.md` | **CREATING a task** — pre-verify → interview → recommend → tags → confirm → create. 🔴 A PreToolUse hook DENIES a create with no `## Acceptance criteria`, or an unreadable body. Override `CLAWGATE_NO_INTERVIEW=1`. |
| `task-pickup.md` | **PICKING UP a task** — "read and evaluate clawgate task N", then "local dispatch": read → evaluate → pre-start comment → `in_progress` → work → ONE completion comment → status. Carries the criteria detector, the ordering trap and the comment rules. 🔴 A Stop hook BLOCKS on a missing write-back and names this file. |

Memories: `clawgate-phase2` · `clawgate-phase3` · `clawgate-runbooks` ·
`clawgate-loop-validation` · `authelia-passkey-sso`.

## Key facts (verify before asserting)

| Thing | Value |
|---|---|
| Source | `~/workspace/homelab-talos/containers/clawgate/` (module `github.com/zacxdev/clawgate`) |
| Hook scripts | `hook/clawgate-hook.sh` (PermissionRequest → `/api/send`) + `hook/clawgate-stop-hook.sh` (Stop → `/api/suggest`); both read `~/.claude/clawgate.env` |
| Cluster | **workbench**, ns `clawgate`; dispatched agents in ns **`devpod-<agent-name>`** |
| 🔴 kubeconfig is PER-HOST — never hardcode; `ls` both, take the one that EXISTS | workbench `.250` → `~/workspace/homelab-talos/workbench-kubeconfig`; laptop `.155` → `~/workspace/homelab-infra/workbench-kubeconfig`. The other is **absent** on each host. Telling the hosts apart: `troubleshooting.md`. |
| Image / manifest | `harbor.homelab.lan/library/clawgate:<ver>`, pinned in `clusters/workbench/apps/clawgate/deployment.yaml` (Flux from `trunk`) |
| LAN URL (hook + UI) | `http://192.168.50.250:30302` (NodePort) — **OPEN, no auth**; machine endpoints still need the token |
| Public / nebula URL | `https://clawgate.zacx.dev` behind **Authelia passkey** (portal `login.zacx.dev`); laptop `http://10.42.0.10:8109` (homelab gateway) |
| Hook events | `PermissionRequest` (`CLAWGATE_REMOTE_APPROVAL=off`) + `Stop` (async, `CLAWGATE_SUGGEST=off`), both in `~/.claude/settings.json`, ON by default. 🔴 The `Stop` array also carries **two** unrelated hooks (`tmux/task-hook.sh`, `claude-notify.py`) — **preserve both** |
| 🔴 Machine client | **`clawgatectl`** (devrc `nix/pkgs/tools/clawgatectl.nix`; on PATH after a switch). 🔴 **Built from a LOCAL working tree of homelab-talos, so it can be present but STALE** — a behind checkout ships a binary MISSING verbs that prints help and **exits 0** under a plausible version label. JSON on stdout only; rc 0–8. **Every other route is still curl.** Commands, config, the staleness closure and the skew note: `task-api.md` |

🔴 **clawgate has NO human auth of its own** (since 0.7.37): `requireSession` is a pass-through no-op,
so **the LAN NodePort is fully unauthenticated** — including `DELETE /tasks/{id}` and 🔴 **`POST
/api/auto-approve-all`** (arms a global auto-approve window over **every** future request in
**every** project + sweeps the pending queue; checkpoints excepted). `requireHookToken` is
**enforce-when-set**: an empty token opens the machine endpoints too. All four wrappers across all
120 routes: `task-api.md`.

---

## status
```bash
KC=$(ls /home/zach/workspace/homelab-{talos,infra}/workbench-kubeconfig 2>/dev/null | head -1)  # PER-HOST
kubectl --kubeconfig $KC -n clawgate get pods -l app=clawgate -o wide
clawgatectl health   # live version + uptime; rc 6 = unreachable, rc 8 = you hit the public host
```

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
is an immutable literal tag with **no Flux image automation**, so a commit under
`containers/clawgate/**` reconciles cleanly and **changes nothing that is running**. `git log` is NOT
evidence the code is live; the live pin and `clawgatectl health` are. **Load `deploy.md` first** —
version-from-the-live-pin, the ONE commit path (worktree off `origin/trunk`; never `git add -A`),
test gate, build/push, pin bump, the CSS-cwd trap that fakes ~25 e2e failures, chart sync.

## task pickup — "read and evaluate clawgate task N", then "local dispatch"
🔴 **Run `flows/task-pickup.md`** — the comment/status ritual is NOT optional and NOT a thing to be
asked for. Run it unprompted. The bash block, the criteria detector, the frozen-verdict rule, the
completion-comment shape, the ordering trap and the two-comments rule are all there.

🔴 **A hook ENFORCES this** (`~/.claude/hooks/clawgate-writeback-guard.py`): armed by the
step-1 **read**, it **blocks Stop** when work followed (edit/commit/push/PR) and a **live** re-read
shows no `claude-code` comment since. Read-and-evaluate-only never fires; commenting silences it.
Its block message names `~/.claude/skills/clawgate/flows/task-pickup.md` by path.

🔴 **Status gate — the only place `complete` is ever yours to set.** Criteria are
**AUTHOR-SPECIFIED** only when the task body carries a `## Acceptance criteria` heading; anything
else means you **DERIVED** them, and that verdict is frozen at your first read.

| criteria | every criterion validated with evidence? | final status |
|---|---|---|
| AUTHOR-SPECIFIED | yes | **`complete`** |
| DERIVED | yes | **`ready_for_review`** — you must not grade an exam you wrote |
| either | **no** | **`ready_for_review`**, naming WHICH criterion and WHY it was not validatable |

## machine (hook-token) Task API
🔴 **Authoring one? `flows/task-authoring.md` FIRST** — a hook denies a criteria-less create.
Read/create with `clawgatectl task ls --summary [--status open --tag t --limit n]` · `task get <id>`
· `task create --body …`; **`--summary`/`--status`/`--limit` filter SERVER-side** — NOT true at
0.7.85, re-measured live 0.7.87 on 2026-08-13. Write status + comments with `clawgatectl task
status` / `task comment` (above). Every remaining verb (`PATCH` content/tags, `DELETE`, comment
DELETE, `/api/tags`, `/api/projects`, `/api/notify`) is still curl with `Authorization: Bearer
$CLAWGATE_HOOK_TOKEN` or `X-Clawgate-Token`. Statuses are exactly `open` / `in_progress` /
`ready_for_review` / `complete` — no `dismissed`; dismissing deletes.

🔴 **ONE path deletes a task and TEARS DOWN its live dispatched agent pod**: `DELETE /api/tasks/{id}`
(`dismissTask`; **no in-progress guard, deliberately**), unauthenticated on the LAN (above).
⚠ **Its automated twin is RETIRED — do not re-derive it.** Since **0.7.96** (`cf529d41`, live) the
daily idle-task reaper **tags `stale` + posts a system comment** instead of calling `dismissTask`, so
**nothing destroys a task or an agent pod on a timer**. `CLAWGATE_TASK_TTL` is still **unset in the
deployment**, so the 7d default is LIVE — it now costs a tag, not the task (`off`/`0` disables).

⚠ **Tags are hard-validated: one invalid tag or unknown `runbook:` is a hard 400 that fails the whole
create** — a load-bearing wire contract producers key their retry on.
⚠ **A task body may carry extension-picked element references** — never search the selector first
(`element-references.md`).

⚠ **Task↔session threads (#357).** `GET /api/tasks/{id}/sessions` **404s BY DESIGN** (pinned by
`TestNoForwardSessionsSubRoute`) — the thread is EMBEDDED on task reads. So
`clawgatectl task get N | jq .sessions` answers *"which sessions worked task N"*; NOT UI-only.
🔴 Membership OVER-reports: a subagent inherits the parent's id, and a mere READ links you.
(#306's rejected-PATCH link is FIXED, live 0.7.99.) `task-api.md`

**Writing/debugging a producer? Load `task-api.md`** — per-op semantics + status codes,
409/immutability, the author allowlist, provenance, tag grammar, the route×auth inventory.

## agent dispatch
🔴 **Current STATUS of the loop lives in `HANDOFF.md`, not here** — claims here have been superseded
within two days, twice. `agent-dispatch.md` has the sandbox fixture, the agent image's absent
toolchain and the dispatch `curl`. Durable facts only:
- **The loop DOES close unattended** (two real runs). "The 5-minute kickoff deadline is why it never
  worked" is DEAD — don't reopen it.
- **`POST /agents` is FORM-ENCODED, not JSON** (hence no `clawgatectl` verb), behind the no-op
  `requireSession` → **no auth on the LAN NodePort**. 🔴 A future webhook needs a **separate
  hostname**, never a path bypass on `clawgate.zacx.dev` — that puts dispatch on the open internet.
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
- **Red GitHub Actions checks on `homelab-infra` are NOISE** (billing-blocked); the real gate is
  Tekton `clawgate-ci` (`tekton` skill). 🔴 **But `clawgate-ci` does NOT run Playwright —
  browser-layer changes are UNGATED.** Run `make e2e` locally and **count**: without Docker,
  `test.skip` on `!dockerAvailable()` leaves **11 of 18 spec files / 77 of 113 tests**, green.
- 🔴 **The browser extension does NOT ship via Flux — merging to `trunk` deploys NOTHING.** Brave
  loads it unpacked from `~/workspace/clawgate-extension/containers/clawgate/extension` (branch
  `clawgate-ext-local`, same path on **BOTH hosts**); deploy = `merge --ff-only origin/trunk` on both
  + reload Brave. 🔴 **Brave profiles load extensions from DIFFERENT paths, so the profile in front of
  you proves nothing** — and agents can't read `brave://`: never call a version or hotkey live
  without `extension.md`'s `Preferences` sweep (+ its 🔴 `git restore --worktree` trap).
