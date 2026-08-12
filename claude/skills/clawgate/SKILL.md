---
name: clawgate
description: "Operate clawgate — the self-hosted Claude Code approval UI (plus Tasks/agents/runbooks). Status, send-a-test, push/SSE logs, build and deploy a version, toggle the approval hook, manage credentials/QR. Use for: clawgate, clawgate.zacx.dev, remote approval, the PermissionRequest approval hook, push notifications for permission prompts."
---

# clawgate operations

Self-hosted Go + htmx PWA routing Claude Code permission prompts to Zach's phone for approve /
approve-with-comment / deny. It has since grown into the **agent dispatch loop**: Tasks/Repos/Agents
on Postgres, agent self-service + privilege profiles + an Operator, runbooks with approval gates,
and a machine Task API producers post work into for one-tap Dispatch.

🔴 **Point-in-time state: `/home/zach/workspace/homelab-talos/containers/clawgate/HANDOFF.md` —
GREP it for the section you need, never read it whole (~190 KB / ~46k tokens, lower half superseded).**

⚠ **This skill drifts from the code in BOTH directions** — 0.7.72 was documented here weeks before
it reached `trunk`; on 2026-08-12 it was six releases BEHIND (live 0.7.85). Never treat a doc claim
as evidence: check the LIVE pin + `git grep` the feature.

## Reference files
`devrc/claude/skills/clawgate/reference/` (also `~/.claude/skills/clawgate/` after a switch).

| file | read it when |
|---|---|
| `deploy.md` | **building + shipping a version**: manifest-vs-code + CSS-cwd traps; chart sync |
| `task-api.md` | **writing/debugging a producer**; tag grammar; access model |
| `agent-dispatch.md` | debugging the agent loop; `POST /agents`; the sandbox fixture; a silent non-start |
| `extension.md` | you changed the browser extension, or need to know which build is loaded |
| `changelog.md` | *when* a feature landed / why an old decision stands (0.2.x → 0.7.79; live is ahead) |
| `architecture.md` | changing agents / repos / runbooks / privilege / native tools / env / e2e |
| `internals.md` | changing Go code: markdown renderer, the two `taskTitle`s, migrations |
| `telemetry.md` | metrics/logs missing; adding an event; a red CI check |
| `troubleshooting.md` | symptom index: push, PWA icon, stale SW, RBAC, kubeconfig, agent model |
| `hooks.md` | `PermissionRequest` semantics; the defer gates; installing hooks elsewhere; Stop / 💡 |
| `agent-hardening.md` | locking down a **homelab** kubeclaw devpod (netpol needs Cilium) |
| `element-references.md` | a task body carries extension-picked element references |

Memories: `clawgate-phase2` · `clawgate-phase3` · `clawgate-runbooks` ·
`clawgate-loop-validation` · `authelia-passkey-sso`.

## Key facts (verify against current state before asserting)

| Thing | Value |
|---|---|
| Source | `~/workspace/homelab-talos/containers/clawgate/` (module `github.com/zacxdev/clawgate`) |
| Hook scripts | `hook/clawgate-hook.sh` (PermissionRequest → `/api/send`) + `hook/clawgate-stop-hook.sh` (Stop → `/api/suggest`); both read `~/.claude/clawgate.env` (`CLAWGATE_API_URL`, `CLAWGATE_HOOK_TOKEN`) |
| Cluster | **workbench**, ns `clawgate`; dispatched agents land in ns **`devpod-<agent-name>`** |
| 🔴 kubeconfig is PER-HOST — never hardcode one; `ls` both, take the one that EXISTS | workbench `.250` → `~/workspace/homelab-talos/workbench-kubeconfig`; laptop `.155` → `~/workspace/homelab-infra/workbench-kubeconfig`. The other is **absent** on each host. Why, plus telling the hosts apart: `troubleshooting.md`. |
| Image / manifest | `harbor.homelab.lan/library/clawgate:<ver>`, pinned in `clusters/workbench/apps/clawgate/deployment.yaml` (Flux from `trunk`) |
| LAN URL (hook + UI) | `http://192.168.50.250:30302` (NodePort) — **OPEN, no auth**; machine endpoints still need the token |
| Public / nebula URL | `https://clawgate.zacx.dev` behind **Authelia passkey** (portal `login.zacx.dev`); laptop `http://10.42.0.10:8109` via the homelab gateway |
| Hook events | `PermissionRequest` (`CLAWGATE_REMOTE_APPROVAL=off`) + `Stop` (async, `CLAWGATE_SUGGEST=off`), both in `~/.claude/settings.json`, ON by default. 🔴 The `Stop` array also carries **two** unrelated hooks (`tmux/task-hook.sh`, `claude-notify.py`) — **preserve both** |

🔴 **clawgate has NO human auth of its own** (since 0.7.37): `requireSession` is a pass-through no-op,
so **the LAN NodePort is fully unauthenticated** — including `DELETE /tasks/{id}` and 🔴 **`POST
/api/auto-approve-all`**, which arms a timed global window auto-approving **every** future request in
**every** project *and* sweeps the pending queue (checkpoints excepted). `requireHookToken` is
**enforce-when-set**: an empty token opens the machine endpoints too. Model: `task-api.md`.

---

## status
```bash
KC=$(ls /home/zach/workspace/homelab-{talos,infra}/workbench-kubeconfig 2>/dev/null | head -1)  # PER-HOST
kubectl --kubeconfig $KC -n clawgate get pods -l app=clawgate -o wide
curl -sf --max-time 5 http://192.168.50.250:30302/health; echo   # LAN health + live version
```

## send a test
Creates a real pending request (card + Web Push) via the hook token on the open LAN NodePort; for
delivery, tail logs for `push: delivered ... to N device(s)`.
```bash
HOOK=$(grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2)
curl -sf -X POST http://192.168.50.250:30302/api/send -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $HOOK" \
  -d '{"type":"permission","tool":"Bash","command":"echo test","host":"nixos","project":"clawgate"}' | jq .
```

## logs (push / SSE / subscriptions)
```bash
KC=$(ls /home/zach/workspace/homelab-{talos,infra}/workbench-kubeconfig 2>/dev/null | head -1)  # PER-HOST
kubectl --kubeconfig $KC -n clawgate logs -f deploy/clawgate | grep --line-buffered -iE 'subscription|delivered|push:|request created|decision recorded|could not'
```
(Run under the Monitor tool for a live watch that notifies the user as events land.)

## deploy a new version
🔴 **GitOps from `trunk`: committing deploys the MANIFEST, not container CODE — silently.** The
manifest pins an immutable literal tag with **no Flux image automation**, so a commit under
`containers/clawgate/**` reconciles cleanly and **changes nothing that is running**. `git log` is NOT
evidence the code is live; the live pin and `/health` are. **Load `deploy.md` first** —
version-from-the-live-pin, the ONE commit path (a worktree off `origin/trunk`; never `git add -A`),
test gate, build/push, pin bump, the CSS-cwd trap that fakes ~25 e2e failures, the chart-sync trap.

## machine (hook-token) Task API
Producers post under `/api/tasks*` (+ `/api/notify`, `/api/tags`) with `Authorization: Bearer
$CLAWGATE_HOOK_TOKEN` or `X-Clawgate-Token`. Statuses are exactly `open` / `in_progress` /
`ready_for_review` / `complete` — no `dismissed`; dismissing deletes.

🔴 **Two paths delete a task, both TEARING DOWN its live dispatched agent pod** (shared
`dismissTask` → `Provisioner.Destroy`; **no in-progress guard, deliberately**):
- `DELETE /api/tasks/{id}` — unauthenticated on the LAN (above).
- 🔴 **the idle-task reaper — its automated twin**: the daily sweep dismisses any task untouched for
  `defaultTaskReapAfter` = **7d**, and `CLAWGATE_TASK_TTL` is **unset in the deployment**, so that
  default is LIVE — an abandoned dispatch loses its agent pod after a week (`off`/`0` disables).

⚠ **Tags are hard-validated: one invalid tag or unknown `runbook:` is a hard 400 that fails the whole
create** — a load-bearing wire contract producers key their retry on.
⚠ **A task body may carry extension-picked element references** — never search the selector first
(`element-references.md`).

**Writing/debugging a producer? Load `task-api.md`** — per-op semantics + status codes,
409/immutability, the comment-author allowlist, provenance headers, the tag grammar.

## agent dispatch
🔴 **Current STATUS of the loop lives in `HANDOFF.md`, not here** — status claims written here have
been superseded within two days, twice. `agent-dispatch.md` has the sandbox fixture, the agent
image's absent toolchain and the `curl` form. Durable facts only:
- **The loop DOES close unattended** (two real runs). "The 5-minute kickoff deadline is why it never
  worked" is a DEAD theory — don't reopen it.
- **`POST /agents` is FORM-ENCODED, not JSON**, behind the no-op `requireSession` → **no auth on the
  LAN NodePort**. 🔴 `clawgate.zacx.dev` is gated ONLY at the Authelia edge, so a future webhook needs
  a **separate hostname** — a path bypass there puts agent dispatch on the open internet.
- ⚠ **A dispatch that cannot START surfaces almost nothing.** `provisioningStuckTimeout` (15m,
  `internal/agents/reconcile.go`) marks the **agent** `error`, but the **task stays `in_progress`**,
  `kicked_off` stays `false`, and nothing pushes. **Read the AGENT POD LOGS first — ns
  `devpod-<agent-name>`, not ns `clawgate`.** The clawgate-side "gateway not ready after 3m0s" is a
  symptom, not the cause, and not a provisioning flake.

## hook management
- On by default, global. Off for one session: `CLAWGATE_REMOTE_APPROVAL=off`. Inspect:
  `jq '.hooks.PermissionRequest' ~/.claude/settings.json`.
- Tests, from `containers/clawgate/`: `nix-shell -p bats jq --run 'bats hook/tests/*.bats'`.
- **Fail-safe by design**: any error / timeout / unreachable server → defer to the terminal, as if
  the hook were absent; a clawgate outage never blocks Claude Code. ⚠ It also defers **without ever
  contacting the server** when `permission_mode` is `bypassPermissions`/`plan`, or the tool is
  `AskUserQuestion` — so "no card appeared" is not evidence of an outage.
- `hooks.md`: the full gate list, the exact JSON, why an approver comment is **record-only**,
  installing hooks on another host, and the Stop / 💡 hook.

## 🔴 gotchas (don't relearn these)
- 🔴 **Public routing rule**: clawgate runs on WORKBENCH but is fronted by the homelab + production
  nebula gateways, whose nginx block must `proxy_pass` to the workbench **NodePort IP
  `http://192.168.50.250:30302`** — NOT a `.svc.cluster.local` name, which doesn't resolve there and
  **crashes nginx, taking down ALL nebula-routed services**. Edit additively; a reload restarts
  `nebula-gateway` on **both** clusters. Mechanics: `architecture.md`.
- **The vendored kubeclaw chart is embedded at BUILD time** — a kubeclaw release reaches
  clawgate-provisioned agents only after `make sync-chart` + rebuild + redeploy, and `make
  check-chart` silently tests the wrong tree unless `~/workspace/kubeclaw` is synced FIRST.
- ⚠ **Clawgate-provisioned agents are NOT hardened by default**: chart defaults are
  `networkPolicy.enabled: false` + `tls.verify: false` (→ `NODE_TLS_REJECT_UNAUTHORIZED=0`). The
  **container** securityContext IS set (`allowPrivilegeEscalation: false`, seccomp `RuntimeDefault`);
  only the **pod-level** one is empty. 🔴 That netpol is **Cilium-only** and workbench — where
  clawgate provisions — has **no Cilium**, so `agent-hardening.md` is a **homelab** playbook.
- **Alloy has no auto-reloader** — if clawgate metrics vanish from homelab Prometheus, restart Alloy
  first (`telemetry.md`).
- **Red GitHub Actions checks on `homelab-infra` are NOISE** (billing-blocked); the real gate is the
  Tekton `clawgate-ci` pipeline — see the `tekton` skill before touching CI. 🔴 **But `clawgate-ci`
  does NOT run Playwright — browser-layer changes are UNGATED.** Run `make e2e` locally and
  **count**: **11 of the 18 spec files, 77 of 113 tests, `test.skip` on `!dockerAvailable()`** — a
  "green" run without Docker executed barely a third of the suite.
- 🔴 **The browser extension does NOT ship via Flux — merging to `trunk` deploys NOTHING.** Brave
  loads it unpacked in place from `~/workspace/clawgate-extension/containers/clawgate/extension`
  (branch `clawgate-ext-local`), same path on **BOTH hosts**; deploy = `merge --ff-only origin/trunk`
  on both + reload Brave. 🔴 **Brave profiles load extensions independently from DIFFERENT paths, so
  the profile in front of you proves nothing** — and agents can't read `brave://`. Never call a
  version or hotkey live without the `Preferences` sweep in `extension.md`, which also carries the 🔴
  `git restore --worktree` trap.
