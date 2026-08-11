---
name: clawgate
description: "Operate clawgate — the self-hosted Claude Code approval UI (plus Tasks/agents/runbooks). Status, send-a-test, push/SSE logs, build and deploy a version, toggle the approval hook, manage credentials/QR. Use for: clawgate, clawgate.zacx.dev, remote approval, the PermissionRequest approval hook, push notifications for permission prompts."
---

# clawgate operations

Self-hosted Go + htmx + Tailwind PWA that routes Claude Code permission prompts to Zach's phone for
approve / approve-with-comment / deny (it replaced the dead Telegram notify-bot). It has since grown
into the **agent dispatch loop**: Tasks/Repos/Agents tabs on Postgres, agent self-service + privilege
profiles + an Operator, runbooks with approval checkpoints, and a machine Task API that producers
(task-spec drafter, repo-cos, mail-actions) post work into for one-tap Dispatch.

🔴 **Authoritative point-in-time state is `/home/zach/workspace/homelab-talos/containers/clawgate/HANDOFF.md` — read it first.**

⚠ **This skill can be AHEAD of the code.** 0.7.72 was fully documented here weeks before it was
committed to `trunk`. Before building on or deploying a documented feature, verify it's really in
trunk: check the LIVE pin + `git grep` the feature. Don't trust the doc.

## Reference files
Repo-absolute at `/home/zach/workspace/devrc/claude/skills/clawgate/reference/`; also deployed to
`~/.claude/skills/clawgate/reference/` after a home-manager switch.

| file | read it when |
|---|---|
| `reference/deploy.md` | **building + shipping a version** — full runbook, manifest-vs-code trap, CSS-cwd trap, chart sync |
| `reference/task-api.md` | **writing/debugging a producer** — Task API surface, tag grammar, auth model, token consumers |
| `reference/agent-dispatch.md` | dispatching/debugging the agent loop (`POST /agents`, sandbox fixture, silent non-start) |
| `reference/extension.md` | you changed the browser extension, or need to know which build is really loaded |
| `reference/changelog.md` | *when* a feature landed or why an old decision was made (0.2.x → 0.7.79) |
| `reference/architecture.md` | changing agents / repos / runbooks / privilege / native tools / env / e2e |
| `reference/internals.md` | changing Go code — markdown renderer, the two `taskTitle`s, migrations |
| `reference/telemetry.md` | metrics/logs missing, adding an event, or a red CI check |
| `reference/troubleshooting.md` | something is broken — symptom index (push, PWA icon, stale SW, RBAC, agent model) |
| `reference/hooks.md` | `PermissionRequest` semantics, installing hooks on another host, the Stop / 💡 path |
| `reference/agent-hardening.md` | locking down a kubeclaw agent devpod (securityContext, netpol, FQDN allowlist) |
| `reference/element-references.md` | a task body has extension-picked element references to find in source |

Session memories: `clawgate-phase2` · `clawgate-phase3` · `clawgate-runbooks` ·
`clawgate-loop-validation` · `clawgate-version-before-build` · `authelia-passkey-sso` ·
`openclaw-exec-sandbox-strips-env`.

## Key facts (verify against current state before asserting)

| Thing | Value |
|---|---|
| Source | `/home/zach/workspace/homelab-talos/containers/clawgate/` (Go module `github.com/zacxdev/clawgate`) |
| Hook scripts | `hook/clawgate-hook.sh` (PermissionRequest → `/api/send`) + `hook/clawgate-stop-hook.sh` (Stop → `/api/suggest`, async/fail-safe) |
| Hook config | `~/.claude/clawgate.env` (`CLAWGATE_API_URL`, `CLAWGATE_HOOK_TOKEN`) — shared by both hooks |
| Cluster | homelab-talos **workbench**, namespace `clawgate` |
| 🔴 kubeconfig is PER-HOST — never hardcode it; `ls` both, take the one that EXISTS | Measured 2026-08-02 on both hosts: laptop `.155` → `~/workspace/homelab-infra/workbench-kubeconfig`; workbench `.250` → `~/workspace/homelab-talos/workbench-kubeconfig`. The other is **absent** on each host, so a missing file means "wrong host", not "wrong doc". (Origin was renamed homelab-talos → homelab-infra; the local repo dir is still `~/workspace/homelab-talos` — repo checkout ≠ kubeconfig dir.) Both hosts are hostname `nixos`; the IP or `browser whoami` disambiguates. |
| Image | `harbor.homelab.lan/library/clawgate:<ver>` (the live pinned tag is in the deployment) |
| Deploy manifest | `homelab-talos/clusters/workbench/apps/clawgate/deployment.yaml` (Flux GitOps from branch `trunk`) |
| LAN URL (hook + UI) | `http://192.168.50.250:30302` (NodePort) / `clawgate.workbench.lan` — **OPEN, no auth** (trusted LAN); machine endpoints still need the bearer token |
| Public URL (phone) | `https://clawgate.zacx.dev` — fronted by **Authelia 4.39 passwordless passkey** (portal `login.zacx.dev`, user `zach`) |
| Nebula URL (laptop) | `http://10.42.0.10:8109` (homelab gateway → clawgate; hook-token auth) |
| Hook events | `PermissionRequest` (kill-switch `CLAWGATE_REMOTE_APPROVAL=off`) + `Stop` (async, kill-switch `CLAWGATE_SUGGEST=off`) — both in `~/.claude/settings.json`, ON by default; the `Stop` array also carries an unrelated tmux task-hook (**preserve it**) |

🔴 **clawgate has NO human auth of its own** (since 0.7.37): `requireSession` is a literal
pass-through no-op (`internal/api/auth.go`), so **the LAN NodePort is fully unauthenticated —
including `DELETE /tasks/{id}`**; `requireHookToken` is **enforce-when-set**, so an empty
`CLAWGATE_HOOK_TOKEN` opens the machine endpoints too. Publicly, Authelia's passkey is the only
gate. Full access model + the three `CLAWGATE_HOOK_TOKEN` consumers and their rotation coupling:
`reference/task-api.md`.

---

## status
```bash
KC=/home/zach/workspace/homelab-infra/workbench-kubeconfig   # PER-HOST — see Key facts
kubectl --kubeconfig $KC -n clawgate get pods -l app=clawgate -o wide
kubectl --kubeconfig $KC -n clawgate get pod -l app=clawgate -o jsonpath='{.items[0].spec.containers[0].image}{"\n"}'   # live image/version
curl -sf --max-time 5 http://192.168.50.250:30302/health; echo                                                          # LAN health
# pending count + push subscription count (from logs):
kubectl --kubeconfig $KC -n clawgate logs deploy/clawgate --tail=200 | grep -iE 'subscription stored|delivered' | tail -3
```

## send a test
Creates a real pending request (card + Web Push), via the hook token on the open LAN NodePort:
```bash
B=http://192.168.50.250:30302
HOOK=$(grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2)
curl -sf -X POST "$B/api/send" -H 'Content-Type: application/json' -H "Authorization: Bearer $HOOK" \
  -d '{"type":"permission","tool":"Bash","command":"echo test","host":"nixos-desktop","project":"clawgate","context":["clawgate self-test"]}' | jq .
```
To confirm native delivery, tail logs for `push: delivered ... to N device(s)`.

## logs (push / SSE / subscription activity)
```bash
KC=/home/zach/workspace/homelab-infra/workbench-kubeconfig   # PER-HOST — see Key facts
kubectl --kubeconfig $KC -n clawgate logs -f deploy/clawgate | grep --line-buffered -iE 'subscription stored|delivered|push:|request created|decision recorded|could not'
```
(For a live watch that notifies the user as events arrive, use the Monitor tool with this command.)

## deploy a new version
🔴 **homelab-talos is GitOps from `trunk`: committing IS deploying the MANIFEST — but NOT container
CODE, and the difference is silent.** `deployment.yaml` pins an immutable literal tag and there is
**no Flux image automation**, so a commit under `containers/clawgate/**` reconciles cleanly and
**changes nothing that is running**. Only a pin bump after a build+push deploys. `git log` on `trunk`
is NOT evidence a code change is live; the live pin and `/health` are.

🔴 **ONE commit path: a dedicated git WORKTREE off `origin/trunk`** — never the main checkout, which
is permanently dirty (`.sops.yaml`, secrets, WIP). **Never `git add -A`**; stage explicit clawgate
paths.

**Load `reference/deploy.md` before you build or ship anything** — the full runbook
(version-from-the-live-pin, test gate, build/push, pin bump, reconcile, cleanup, base-clone re-sync),
the CSS-cwd trap that fakes ~25 e2e failures, the pristine-trunk baseline rule, and the chart-sync
trap.

## machine (hook-token) Task API
Producers post Tasks/cards under `/api/tasks*` (+ `/api/notify`, `/api/tags`) with
`Authorization: Bearer $CLAWGATE_HOOK_TOKEN` or `X-Clawgate-Token`. Statuses are exactly `open` /
`in_progress` / `ready_for_review` / `complete` — there is **no `dismissed`**; dismissing deletes.

🔴 **`DELETE /api/tasks/{id}` shares `dismissTask`, so it TEARS DOWN a live dispatched agent pod**
(`Provisioner.Destroy`). There is **no in-progress guard — deliberately**. Delete a dispatched task
only if you mean to kill its agent.

⚠ **Tags are hard-validated: one invalid tag or unknown `runbook:` is a hard 400 that fails the whole
create** — and that 400 is a load-bearing wire contract producers key their retry on.
⚠ **A task body may contain browser-extension element references** — do NOT search the selector
first; work domain/path → adjacent text → selector → accessible name
(`reference/element-references.md`).

**Writing or debugging a producer? Load `reference/task-api.md`** — the full op table with per-op
semantics and status codes, the 409/immutability rules, the comment-author allowlist, provenance
headers, the tag grammar, and the auth/access model.

## agent dispatch
🔴 **Current STATUS of the loop lives in `containers/clawgate/HANDOFF.md`, not here** — status claims
written into this skill have been superseded within two days, twice. Durable facts only:
- **The loop DOES close unattended** (verified 2026-07-30/31, two runs, 138 s / 149 s, clone → PR →
  `ready_for_review`). "The 5-minute kickoff deadline is why it never worked" is a DEAD theory — it
  was dormant because nothing was dispatched. Don't open with "make the timeout configurable".
- **`POST /agents` is FORM-ENCODED, not JSON**, behind the no-op `requireSession` → **no auth on the
  LAN NodePort**. 🔴 `clawgate.zacx.dev` is protected ONLY by the Authelia edge, so any future
  webhook needs a **separate hostname**, never a path bypass there — a bypass puts unauthenticated
  agent dispatch on the internet.
- ⚠ **A dispatch that cannot START fails SILENTLY** — task stays `in_progress`, `kicked_off` stays
  `false`, nothing surfaces it. **Read the POD LOGS first**; the clawgate-side "gateway not ready
  after 3m0s" message is the symptom, not the cause, and is not a provisioning flake.

Running or debugging a dispatch (the `ZacxDev/clawgate-loop-sandbox` fixture, the agent image's
absent toolchain, the `curl` form): `reference/agent-dispatch.md`.

## hook management
- The PermissionRequest hook is global + on by default. Disable for one session: launch with
  `CLAWGATE_REMOTE_APPROVAL=off`. Inspect: `jq '.hooks.PermissionRequest' ~/.claude/settings.json`.
- Hook test suite:
  `cd /home/zach/workspace/homelab-talos/containers/clawgate && nix-shell -p bats jq --run 'bats hook/tests/clawgate-hook.bats'`.
- **Fail-safe by design**: any error / timeout / unreachable server → the hook defers to the
  terminal, i.e. behaves as if it were absent. A clawgate outage never blocks Claude Code.
- Full `PermissionRequest` semantics (exact JSON, why an approver comment is **record-only** with no
  channel back to the model), installing the hooks on another host, and the Stop / 💡 Suggestions
  hook incl. its `bats hook/tests/clawgate-stop-hook.bats` suite: `reference/hooks.md`.

## 🔴 gotchas (do not relearn the hard way)
- 🔴 **Public routing rule**: clawgate runs on WORKBENCH but is fronted by the homelab + production
  nebula gateways. The homelab gateway nginx block must `proxy_pass` to the workbench **NodePort IP
  `http://192.168.50.250:30302`**, NOT a `.svc.cluster.local` name — a cross-cluster DNS name
  doesn't resolve there and **crashes nginx** (`emerg: host not found in upstream`), **taking down
  ALL nebula-routed services**. Edit those shared configs additively and carefully; a reload means
  restarting `nebula-gateway` on **both** clusters (WS/SSE config lives on both). Mechanics:
  `homelab-talos/claudedocs/nebula-production-to-homelab-routing.md` + `reference/architecture.md`.
- **The vendored kubeclaw chart is embedded at BUILD time** — a kubeclaw release doesn't reach
  clawgate-provisioned agents until `make sync-chart` + rebuild + redeploy clawgate, and
  `make check-chart` silently tests the wrong tree unless `~/workspace/kubeclaw` is synced FIRST
  (`reference/deploy.md`).
- **Alloy has no auto-reloader** — if clawgate metrics vanish from homelab Prometheus, restart it
  first (`reference/telemetry.md`).
- **Red GitHub Actions checks on `homelab-infra` are NOISE** (billing-blocked repo-wide); the real
  gate is the Tekton `clawgate-ci` pipeline — see the `tekton` skill before touching CI.
  🔴 **But `clawgate-ci` does NOT run Playwright — browser-layer changes are UNGATED by CI.** Run
  `make e2e` locally and **count** the results: `tasks.spec.ts` `test.skip`s the whole file without
  Docker, so a "green" run can mean 17 tests never executed.
- 🔴 **The browser extension does NOT ship via Flux — merging to `trunk` deploys NOTHING.** Brave
  loads it unpacked from a flat copy at `~/clawgate-extension` **on the workbench**, and does not
  hot-reload it. **A missing `.synced-from` stamp means someone hand-copied it out of band** — that
  is how a build that never passed CI ran live for ~11 hours while `trunk` looked clean. Check the
  stamp before trusting any claim about which build is loaded → `reference/extension.md`.
