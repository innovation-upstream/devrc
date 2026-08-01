---
name: clawgate
description: Operate clawgate — the self-hosted Claude Code permission-approval UI that replaced the Telegram flow. Status, send-a-test, push/SSE logs, build+deploy a new version, toggle the approval hook, manage credentials/QR, and the push/PWA/icon troubleshooting playbook. Use when the user mentions clawgate, remote approval, clawgate.zacx.dev, the PermissionRequest approval hook, or push notifications for Claude Code permission prompts.
---

# clawgate operations

Self-hosted Go + gomponents + htmx + Tailwind PWA that routes Claude Code permission prompts to
Zach's phone for approve / approve-with-comment / deny (it replaced the dead Telegram notify-bot).
It has since grown into the **agent dispatch loop**: Tasks/Repos/Agents tabs on Postgres, agent
self-service + privilege profiles + an Operator, runbooks with approval checkpoints, and a durable
machine Task API that producers (task-spec drafter, repo-cos, mail-actions) post work into for
one-tap Dispatch.

🔴 **Authoritative point-in-time state is `/home/zach/workspace/homelab-talos/containers/clawgate/HANDOFF.md` — read it first.**

⚠ **This skill can be AHEAD of the code.** 0.7.72 was fully documented here weeks before it was
committed to `trunk`. Before building on or deploying a documented feature, verify it's really in
trunk: check the LIVE pin + `git grep` the feature. Don't trust the doc.

## Reference files
Repo-absolute path `/home/zach/workspace/devrc/claude/skills/clawgate/reference/`; they also deploy
to `~/.claude/skills/clawgate/reference/` after a home-manager switch.

| file | read it when |
|---|---|
| `reference/changelog.md` | you need *when* a feature landed or why an old decision was made (0.2.x → 0.7.79) |
| `reference/architecture.md` | changing agents / repos / runbooks / privilege / native tools / env / e2e; Phase 2–4 detail |
| `reference/internals.md` | changing Go code — the markdown renderer, the two `taskTitle`s, tag grammar, migrations |
| `reference/telemetry.md` | metrics/logs missing, adding an event, or a red CI check |
| `reference/troubleshooting.md` | something is broken — symptom-first index (push, PWA icon, stale SW, RBAC, agent model) |
| `reference/hooks.md` | installing hooks on another host, or the Stop / 💡 Suggestions path |
| `reference/agent-hardening.md` | locking down any kubeclaw agent devpod (securityContext, networkPolicy, FQDN allowlist) |

Session memories: `clawgate-phase2` · `clawgate-phase3` · `clawgate-runbooks` ·
`clawgate-loop-validation` · `clawgate-version-before-build` · `authelia-passkey-sso` ·
`openclaw-exec-sandbox-strips-env`.

## Key facts (verify against current state before asserting)

| Thing | Value |
|---|---|
| Source | `/home/zach/workspace/homelab-talos/containers/clawgate/` (Go module `github.com/zacxdev/clawgate`) |
| Hook scripts | `hook/clawgate-hook.sh` (PermissionRequest → `/api/send`) + `hook/clawgate-stop-hook.sh` (Stop → `/api/suggest`, async/fail-safe, "Suggested next step") |
| Hook config | `~/.claude/clawgate.env` (`CLAWGATE_API_URL`, `CLAWGATE_HOOK_TOKEN`) — shared by both hooks |
| Cluster | homelab-talos **workbench** — `KC=/home/zach/workspace/homelab-infra/workbench-kubeconfig`, namespace `clawgate` |
| ⚠ kubeconfig path | The GitHub origin was renamed **homelab-talos → homelab-infra**. The local **repo dir is still `~/workspace/homelab-talos`** (deploy paths below unchanged), but the **working kubeconfig lives at `~/workspace/homelab-infra/workbench-kubeconfig`** — deploy agents kept using the old `homelab-talos/…-kubeconfig` (gone). Repo checkout ≠ kubeconfig dir. |
| Image | `harbor.homelab.lan/library/clawgate:<ver>` (the live pinned tag is in the deployment) |
| Deploy manifest | `homelab-talos/clusters/workbench/apps/clawgate/deployment.yaml` (Flux GitOps from branch `trunk`) |
| LAN URL (hook + UI) | `http://192.168.50.250:30302` (NodePort) / `clawgate.workbench.lan` — **OPEN, no auth** (trusted LAN); machine endpoints still need bearer `CLAWGATE_HOOK_TOKEN` |
| Public URL (phone) | `https://clawgate.zacx.dev` — fronted by **Authelia 4.39 passwordless passkey** (portal `login.zacx.dev`, user `zach`); no app-level login |
| Nebula URL (laptop) | `http://10.42.0.10:8109` (homelab gateway → clawgate; hook-token auth) |
| Hook events | `PermissionRequest` (kill-switch `CLAWGATE_REMOTE_APPROVAL=off`) + `Stop` (async, kill-switch `CLAWGATE_SUGGEST=off`) — both in `~/.claude/settings.json`, ON by default; the `Stop` array also carries an unrelated tmux task-hook (**preserve it**) |

### Card producers — all share `CLAWGATE_HOOK_TOKEN`
⚠ **Rotation coupling: rotating the token means updating all three, or they fail silently.**
1. The two local hooks (token in `~/.claude/clawgate.env`).
2. The **task-spec drafter** — a homelab kubeclaw CronJob POSTing one daily `type:"permission"`
   digest card to `/api/send`, tool=`Task-spec drafter`, project=`task-drafter-agent`. It reads the
   token from homelab secret **`task-drafter-agent-secrets`** (ns `devpod-task-drafter`), key
   `CLAWGATE_HOOK_TOKEN` — **miss this on a rotation and the daily digest 401s silently.** A daily
   `Task-spec drafter` card is the drafter, NOT a real CC permission prompt. See the
   `close-the-loop` skill's STATE.md.
3. **repo-cos** (`devrc/scripts/repo-cos/clawgate.py`) — on an "approve" reply it POSTs the proposal
   as a durable Task via `POST /api/tasks`. Reads the token from **`~/.claude/clawgate.env`** on the
   workbench (NOT a k8s secret). See the `repo-cos` skill.

### Auth / access (0.7.37 — clawgate has NO human auth of its own)
No magic-link `/login?token=`, no session cookie, no `CLAWGATE_AUTH_TOKEN` /
`CLAWGATE_SESSION_SECRET` (both now orphaned-unused in `clawgate-secrets`), no Traefik basic-auth,
and **no login QR to manage**.
- **Phone / public** → `https://clawgate.zacx.dev`, pass the **Authelia passkey** at
  `https://login.zacx.dev` (user `zach`, already enrolled). Authelia owns auth/SSO now — manage it
  there, not in clawgate. Memory `authelia-passkey-sso`.
- **LAN** → `http://192.168.50.250:30302` or `clawgate.workbench.lan` — open, no auth.
- **Machine endpoints** (`/api/send`, `/api/tasks`, `/api/notify`, `/api/response/{id}`) require the
  `CLAWGATE_HOOK_TOKEN` bearer. Secrets are NOT stored in this skill — retrieve it with
  `grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2`.
- Everything else (the UI, `/ui/*`) is OPEN — behind Authelia publicly, directly reachable on the
  LAN. The hook never has a cookie; the UI never calls `/api/response/{id}`.
- 🔴 **"session auth" is not auth**: `requireSession` is a literal pass-through no-op since 0.7.37
  (`internal/api/auth.go`), so **everything on the LAN NodePort is unauthenticated — including
  `DELETE /tasks/{id}`**. And `requireHookToken` is **enforce-when-set**: with
  `CLAWGATE_HOOK_TOKEN` empty the machine endpoints are wide open too.

---

## status
```bash
KC=/home/zach/workspace/homelab-infra/workbench-kubeconfig
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
KC=/home/zach/workspace/homelab-infra/workbench-kubeconfig
kubectl --kubeconfig $KC -n clawgate logs -f deploy/clawgate | grep --line-buffered -iE 'subscription stored|delivered|push:|request created|decision recorded|could not'
```
(For a live watch that notifies the user as events arrive, use the Monitor tool with this command.)

## deploy a new version

🔴 **ONE commit path: a dedicated git WORKTREE off `origin/trunk`, ending on `trunk` (= live
deploy).** homelab-talos is GitOps from `trunk` — **committing IS deploying.** Do **NOT** commit
from the main checkout: it is permanently dirty with unrelated uncommitted files (`.sops.yaml`,
secrets, WIP), so the old `git pull --rebase` stash/autostash dance autostashes that tree and conflicts on files you never
touched (this corrupted `.sops.yaml` once, 2026-06-24; on 2026-07-30 the same pattern left the base
clone 262 commits behind with three stale-file conflicts). A clean worktree rebases only your staged
paths. **NEVER `git add -A`** — stage explicit clawgate paths.

```bash
VER=0.7.80   # 🔴 FETCH trunk + check the LIVE deployment pin FIRST — Zach ships concurrently and a
             # mutable-tag clobber bit once (memory clawgate-version-before-build). This fired FOR
             # REAL 2026-07-30: 0.7.77 landed from Zach's session WHILE 0.7.78 was being built here.
             # Derive the number from the LIVE pin, NEVER from this file or HANDOFF. (Last known
             # shipped: 0.7.79 — assume stale, verify with the status snippet.)

# 0. fresh worktree off the latest trunk (clean tree — only YOUR changes live here)
cd /home/zach/workspace/homelab-talos && git fetch origin trunk
git worktree add /home/zach/workspace/homelab-trunk -B clawgate-$VER origin/trunk
cd /home/zach/workspace/homelab-trunk/containers/clawgate
#  ... make your code changes here, in the worktree ...

# 1. test (Go + hook bats + e2e) — must be green. A worktree-local `go test` needs app.css built
#    FIRST (it's gitignored; a missing one 404s BOTH TestStaticAssetsServed and TestOpenRoutesNoAuth).
#    🔴 BUILD IT FROM INSIDE containers/clawgate/ — see the CSS-cwd trap below. Correct form:
nix-shell -p tailwindcss --run "cd /home/zach/workspace/homelab-trunk/containers/clawgate && tailwindcss -i ./web/css/input.css -o ./web/static/app.css --minify"
wc -c web/static/app.css   # sanity: ~36 KB. ~5 KB = the cwd trap fired. Or: grep -c '\.h-14' web/static/app.css
nix-shell -p go --run 'go build ./... && go vet ./... && go test -race -cover ./...'
nix-shell -p bats jq --run 'bats hook/tests/clawgate-hook.bats'
make e2e   # 83 pass / 2 skip; a fresh worktree npm-installs e2e/node_modules first
#          # ⚠ if `make e2e` is KILLED (timeout/Ctrl-C) the fixture's teardown doesn't run →
#          # a `clawgate-e2e-pg-*` postgres container LEAKS. They pile up and starve the box
#          # (FAB tests flake under load). Clean: docker rm -f $(docker ps -aq --filter name=clawgate-e2e-pg)

# 2. build (Dockerfile builds Tailwind CSS then the static Go binary, embeds assets) + smoke + push
docker build --build-arg VERSION=$VER -t harbor.homelab.lan/library/clawgate:$VER -t harbor.homelab.lan/library/clawgate:latest .
docker run -d --name cg-smoke -p 8219:8104 -e CLAWGATE_INSECURE_COOKIES=1 harbor.homelab.lan/library/clawgate:$VER && sleep 3
curl -sf http://localhost:8219/health && echo OK; docker rm -f cg-smoke
docker push harbor.homelab.lan/library/clawgate:$VER && docker push harbor.homelab.lan/library/clawgate:latest

# 3. bump the pin, stage explicit paths, commit, rebase (clean tree → no autostash), push
cd /home/zach/workspace/homelab-trunk
sed -i "s#clawgate:[0-9.]\+#clawgate:$VER#" clusters/workbench/apps/clawgate/deployment.yaml
git add <your changed clawgate paths> clusters/workbench/apps/clawgate/deployment.yaml containers/clawgate/HANDOFF.md
git commit -m "..." -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git fetch origin trunk && git rebase origin/trunk && git push origin HEAD:trunk

# 4. reconcile + verify
KC=/home/zach/workspace/homelab-infra/workbench-kubeconfig
flux --kubeconfig $KC reconcile kustomization clawgate --with-source
until kubectl --kubeconfig $KC -n clawgate get pod -l app=clawgate -o jsonpath='{.items[0].spec.containers[0].image}' | grep -q "$VER"; do sleep 4; done
curl -sf http://192.168.50.250:30302/health   # confirm version

# 5. clean up the worktree (force: removes gitignored build artifacts too)
cd /home/zach/workspace/homelab-talos
git worktree remove /home/zach/workspace/homelab-trunk --force && git branch -D clawgate-$VER

# 6. re-sync the base clone — it is write-only and silently falls behind
git -C /home/zach/workspace/homelab-talos fetch origin && git -C /home/zach/workspace/homelab-talos merge --ff-only origin/trunk
```

🔴 **THE CSS-cwd TRAP (cost hours on 2026-07-30 — read before debugging any e2e failure).** Build
`app.css` **from inside `containers/clawgate/`**. Running
`tailwindcss -i ./web/css/input.css -o ./web/static/app.css --minify` from any other cwd makes the
Tailwind config's **relative content globs resolve against the wrong tree**, so it finds no
templates and silently emits a **~5 KB** stylesheet with **zero utility classes** instead of ~36 KB.
Downstream: the FAB renders zero-sized → Playwright reports it `hidden` → **~25 e2e tests fail in a
way that looks exactly like a code regression.** ⚠ **`TestStaticAssetsServed` does NOT catch this**
— it asserts app.css is *served*, not that it contains anything. **The Dockerfile builds its own
CSS, so the IMAGE is never affected** — this only ever breaks the local gate. Always
`wc -c web/static/app.css` (~36 KB) or `grep -c '\.h-14' web/static/app.css` after building.

🔴 **When an e2e run fails, run the PRISTINE-TRUNK baseline BEFORE theorising.** A throwaway
worktree at `origin/trunk` running the same spec is the one control that separates "my change broke
it" from "the box/env is broken". On 2026-07-30 it settled the CSS trap in a single run, after
several rounds of a plausible-but-wrong host-load hypothesis.

**Deploy mechanics (as last run):** the **docker build runs ON the workbench** via
`DOCKER_HOST=ssh://zach@192.168.50.250`, invoked from a local worktree off `origin/trunk` — no local
Docker daemon needed, and the image lands next to harbor. In the deploy gate **`make e2e` is
SKIPPED** (the killed-e2e `clawgate-e2e-pg-*` container leak starves the box) in favour of the
**PG-gated unit tests against a throwaway `postgres:16-alpine`** — same DB coverage without the leak
risk.

**Verifying UI live is SIMPLE — the LAN UI is OPEN (no auth since 0.7.37).** Drive a standalone
Playwright (or curl) directly at the LAN pod `http://192.168.50.250:30302` — no cookie, no auth, no
session injection. Use standalone Playwright, not the shared Playwright MCP browser, which is
usually locked (`Browser is already in use … use --isolated`). To borrow Playwright deps in a fresh
worktree, symlink `e2e/node_modules` to the main checkout's.

## machine (hook-token) Task API — the full producer surface
Routes registered in `internal/api/server.go` `registerNotesRoutes`, handlers in
`internal/api/notes.go`. Token as `Authorization: Bearer <t>` **or** `X-Clawgate-Token: <t>`.

| op | route | notes |
|---|---|---|
| **create** | `POST /api/tasks` | `{directory, title, body, model, repo, branch, privileges, tags}`; `body` required (400); unknown keys silently dropped (no `DisallowUnknownFields`) |
| **read** | `GET /api/tasks[/{id}]` · `GET /api/tasks?tag=a&tag=b` | tag filter ANDs; bogus tag → `200 []`, not an error |
| **edit** | `PATCH /api/tasks/{id}` | content + dispatch config + `tags` (replace) / `addTags` / `removeTags` (merge); **status/provenance/created_at immutable**; **409 if `in_progress`**; `tags`+merge together → 400 (0.7.73/0.7.75) |
| **set-status** | `PATCH /api/tasks/{id}/status` | ANY status incl. `complete`; **NO `in_progress` guard**; broadcasts `task.changed` + fires the `ready_for_review` push (0.7.74) |
| **delete** | `DELETE /api/tasks/{id}` | ⚠ shares `dismissTask`, so it **TEARS DOWN a live dispatched agent pod** (`Provisioner.Destroy`, background best-effort). **No in-progress guard — deliberately** (`TestAPITaskDeleteInProgressAllowed`). Delete a dispatched task only if you mean to kill its agent. `404` if absent — the existence probe is load-bearing, since `DELETE … WHERE id=$1` succeeds with 0 rows (0.7.76) |
| **comment** | `POST /api/tasks/{id}/comments` | `{body}` only; author from the bounded `X-Clawgate-Source` allowlist (`{extension, api, drafter, repo-cos, claude-code}`, unknown → `api`), **NEVER from the body** — `user`/`operator` are structurally unreachable. Markdown; coalesced push on the machine path only (0.7.78) |
| **tag vocab** | `GET /api/tags` | `[{tag,count}]` |
| **push-only** | `POST /api/notify` | notify-only, no approve/deny card (0.7.68) |
| **provenance** | headers on create | `X-Clawgate-Source` + `X-Clawgate-Session-Id` → `source_type` / `source_session_id` + a card chip (0.7.72) |

Status vocabulary is exactly **`open` / `in_progress` / `ready_for_review` / `complete`**
(`notes.ValidStatus`) — there is **no `dismissed`**; dismissing deletes.
**Route-scope distinction:** the session routes (`/tasks/...`, no `/api` prefix) are LAN/UI-only,
and the agent route `PATCH /agent/task/status` **forbids `complete`** — the machine
`PATCH /api/tasks/{id}/status` is the trusted-producer path that allows ALL statuses.

⚠ **Tags are hard-validated: an invalid tag or an unknown `runbook:` is a hard 400, so one bad tag
breaks the whole create.** Grammar in one line: lowercased, ≤20 tags, ≤64 runes each, charset
`[a-z0-9._/-]`, at most one `:`, no empty half. Reserved namespaces are a CLOSED set — `runbook:`
(hard-validated), `initiative:` (soft), `gate:` (**blocks dispatch, 409**), `auto:dispatch` (off).
🔴 That **400 is a load-bearing WIRE CONTRACT** — producers key their fail-open retry on it. Full
grammar, rationale, and the `title`-vs-`directory` rules: `reference/internals.md`.

## hook management
- The PermissionRequest hook is global + on by default. Disable for one session: launch with
  `CLAWGATE_REMOTE_APPROVAL=off`. Inspect: `jq '.hooks.PermissionRequest' ~/.claude/settings.json`.
- Hook test suite:
  `cd /home/zach/workspace/homelab-talos/containers/clawgate && nix-shell -p bats jq --run 'bats hook/tests/clawgate-hook.bats'`.
- **Hook semantics (verified vs docs)**: `PermissionRequest` fires ONLY when approval is actually
  needed; output is
  `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"|"deny"}}}`.
  It has **NO reason/context channel** — an approver comment is record-only (only `PreToolUse` can
  steer the model via `additionalContext`). Any non-approve/reject decision (e.g. an
  `ignore`/dismiss) → the hook defers to the terminal. Any error/timeout/unreachable → defer
  (**fail-safe: behaves as if the hook were absent**).
- Installing the hooks on another host, and the Stop / 💡 Suggestions hook (incl. its
  `bats hook/tests/clawgate-stop-hook.bats` suite): `reference/hooks.md`.

## 🔴 gotchas (do not relearn the hard way)
- **homelab-talos is GitOps from `trunk` and its working tree is permanently messy** — committing =
  deploying live. ALWAYS stage explicit clawgate paths, never `git add -A`, and commit/build from a
  dedicated worktree off `origin/trunk` (see "deploy a new version").
- **Public routing rule**: clawgate runs on the WORKBENCH cluster but is fronted by the homelab +
  production nebula gateways. The homelab gateway nginx block must `proxy_pass` to the workbench
  **NodePort IP `http://192.168.50.250:30302`**, NOT a `.svc.cluster.local` name — a cross-cluster
  DNS name doesn't resolve there and **crashes nginx** (`emerg: host not found in upstream`),
  **taking down ALL nebula-routed services**. The gateway nginx is a `subPath` configmap mount →
  needs a pod restart to reload (which re-handshakes the nebula tunnel; watchdog/route-fixer
  auto-recover). Touch those shared gateway configs additively and carefully. See
  `homelab-talos/claudedocs/nebula-production-to-homelab-routing.md`.
- **WS/SSE over the public route** lives in the nebula gateway nginx on **both** homelab and
  production — restart the `nebula-gateway` DaemonSet on both after editing
  (`reference/architecture.md`).
- **The vendored kubeclaw chart is embedded at BUILD time** — a kubeclaw release doesn't reach
  clawgate-provisioned agents until `make sync-chart` + rebuild + redeploy clawgate. ⚠ vendored is
  0.7.0 vs kubeclaw 0.7.1 → a re-sync is **PENDING**.
- **Alloy has no auto-reloader** — if clawgate metrics vanish from homelab Prometheus, restart it
  first (`reference/telemetry.md`).
- **Red GitHub Actions checks on `homelab-infra` are NOISE** (billing-blocked repo-wide). The real
  gate is the Tekton `clawgate-ci` pipeline — see the `tekton` skill before touching CI.
