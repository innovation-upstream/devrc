# clawgate — architecture (Phases 2–4, hardening, env/secrets, e2e)

Read when: you're changing agents/repos/runbooks/privilege behaviour, wiring a new env var,
adding a native tool, or working on the e2e suite. Routine ops don't need this file.

Full point-in-time state, loose ends and gotchas live in
`/home/zach/workspace/homelab-talos/containers/clawgate/HANDOFF.md` (**authoritative — read it
first**) + session memories `clawgate-phase2` / `clawgate-phase3` / `clawgate-runbooks` /
`clawgate-loop-validation` / `authelia-passkey-sso` / `openclaw-exec-sandbox-strips-env` /
`clawgate-version-before-build`.

## Phase 2 — Tasks / Repos / Agents
Three tabs beyond permission approval, each a real SPA route: `/`, `/tasks`, `/repos`, `/agents`.
State is consolidated in an **in-cluster Postgres** (`clawgate-postgres`, ns `clawgate`). With no
`DATABASE_URL` the app falls back to in-memory/file, so `go run` and the docker smoke test work
with Phase 2 disabled.

- **Tasks tab** — freeform tasks (with file attachments) scoped to a working directory (dirs
  sourced from permission-request cwd history). ⚠ code calls them "notes" —
  see `internals.md`.
  - **Tasks as the durable ADJUDICATION QUEUE (0.7.39–0.7.42):** the homelab **task-spec drafter**
    (daily cron, DeepSeek) posts verified specs as **durable Task cards** via `POST /api/tasks`
    (`internal/api/notes.go` `handleAPITaskCreate`). This replaced an ephemeral `/api/send` digest
    that evicted at the 5-min permission TTL (close-the-loop Loop #1). Each card has one-tap
    Dispatch, markdown bodies, and is a collapsed `<details>` disclosure. The drafter card body is
    clean (goal/recommendation + safety + Done/Verifier/Agent + Source-ticket link — no raw JSON,
    no id). See the `close-the-loop` skill's STATE.md loop ledger.
- **Repos tab** — GitHub connection. **No OAuth App**: a **static token** path —
  `CLAWGATE_GITHUB_TOKEN` (the broad `gh` token; kept as-is by operator decision) auto-connects the
  account at startup. The token reaches agents as a **Secret env** (`devpod-secrets` → container
  env via the chart's `envFrom`); repo clones are tokenless (`https://github.com/...`) and auth via
  a git credential helper.
  - 🔴 **The helper reads the token from a FILE `/root/.gh-token`** (written at pod startup by
    `buildHelmValues` `extraInitCommands`), **NOT `$GITHUB_TOKEN` directly** (0.7.49) — because
    **openclaw's exec sandbox STRIPS `$GITHUB_TOKEN` from the agent's shell**. Clone worked from
    the startup script's full env, but the agent's later `git push` got an empty password. File
    access isn't stripped, so both clone and push authenticate. The token is never in a
    ConfigMap/log. Memory `openclaw-exec-sandbox-strips-env`.
  - **`gh` is NOT installed** in the image → PRs open via the REST API. Unspecified branches
    resolve to the repo's GitHub default branch, not hardcoded `main` (0.4.2).
- **Agents tab** — provisions & operates **kubeclaw (OpenClaw) agent pods** from clawgate itself.
  The kubeclaw Helm chart is **vendored + `go:embed`'d** at `internal/agents/chart/kubeclaw/` (a
  **compile-time** copy; re-sync with `make sync-chart` in `containers/clawgate/`), installed via
  the Helm Go SDK + client-go using clawgate's elevated **ServiceAccount `clawgate` + ClusterRole
  `clawgate-agents`** (`rbac.yaml`).
  - 🔑 **Because the chart is embedded at build time, a kubeclaw release doesn't reach
    clawgate-provisioned agents until you `make sync-chart` + rebuild + redeploy clawgate.**
    Already-running agents are unaffected — their release was rendered from whatever chart was
    vendored then.
  - ✅ The re-sync is **DONE**: the vendored `internal/agents/chart/kubeclaw/Chart.yaml` reads
    `version: 0.7.1` (shipped in clawgate 0.7.82), so 0.7.1's networkPolicy fail-loud guards
    (empty-allowlist protection) are in the embedded chart. Re-check `Chart.yaml`, not this line.
  - Cards show live status (start/stop/delete) + recent logs; the detail view streams pod logs
    (SSE, collapsed by default) + a chat box (WebSocket → agent gateway
    `:18789 /v1/chat/completions`).
  - Agents run on **OpenRouter** (`agent.auth.provider: openrouter` + `OPENROUTER_API_KEY`,
    dedicated key) with image **`harbor.homelab.lan/library/clawdbot`** — the fleet's OpenClaw
    build, **NOT plain `openclaw`**, which is stale and rejects the models. Model via
    `CLAWGATE_AGENT_MODEL` (default `openrouter/deepseek/deepseek-v4-flash`, per-agent override
    persisted in `agents.model`).
- **Dispatch modal UX** — renders instantly with animated skeletons; repo + task option lists
  lazy-load (`/ui/agents/repos`, `/ui/agents/notes`); the model field is a typeable combobox
  searched live against OpenRouter (`/api/openrouter/models`, server-cached, debounced); the repo
  selector floats recently-used to the top (localStorage `clawgate.recent.repos`). "Dispatch" kicks
  off (sends the task as the first chat message + persists the exchange); "Save for later"
  provisions at 0 replicas.

## Phase 3 — task status, agent self-service, privilege, Operator
- **Task status** `open|in_progress|ready_for_review|complete` + comment thread (migration 0003,
  `note_comments`). Status badge + selector + comments on each card. **Only the operator/human may
  set `complete`.**
- **Agent self-service** — agents call clawgate back, authenticated by their per-agent
  `HOOKS_TOKEN`. HTTP API `internal/api/agent.go`: `/agent/task`, `/agent/task/comment`,
  `/agent/task/status` (**rejects `complete`**), `/agent/privilege/request`.
- **Privilege profiles** (migrations 0004/0005, `internal/privilege`) — reusable named access
  bundles (k8s RBAC rules + optional env/kubeconfig) granted per-agent. Agents request access; the
  human approves on the Agents tab; **clawgate applies the RBAC LIVE via client-go**
  (`internal/agents/privilege_apply.go`). `rbac.yaml`'s ClusterRole gained
  `clusterroles`/`clusterrolebindings` + `escalate` + `bind`.
- **Operator** (`agents.OperatorName="operator"`) — a reserved always-on privileged agent you chat
  with at **`/operator`** (linked from the Agents tab). It orchestrates tasks/agents/repos.
  Provision via the page button (`POST /operator/provision`); runs on `claude-haiku-4.5`.

### 🔑 NATIVE TOOL MECHANISM — how agents actually use tools
**NOT skills, NOT `mcpServers`, NOT AGENTS.md** — all dead ends on the gateway path (openclaw
`doctor` rejects `mcpServers`/`customInstructions`; filesystem skills don't surface to the model).

The working way (from kubeclaw-cloud) is **native flat function `tools` passed in the request to
the agent gateway's `/v1/responses` endpoint + a client-side call→execute→continue loop** that
dispatches each `function_call` to clawgate's in-process handlers. See
`internal/agents/responses.go` (`runToolLoop`, `ToolDef`, `callResponses`),
`Provisioner.ChatWithTools`, `internal/api/operator.go` `operatorToolDefs()`,
`internal/api/agent.go` `AgentToolDefs()`.

⚠ Requires **OpenClaw 2026.5.7+** — older versions 404 `/v1/responses` and fall back to plain chat.

Operator tools = task/agent/repo control (+ `operator_list_runbooks` / `operator_run_runbook`).
Worker tools = read/comment/status/request-privilege on their bound task (+ `agent_checkpoint`).
The worker kickoff turn also runs the tool loop (`Provisioner.SetAgentToolHooks`).

## Phase 4 — Runbooks (0.4.0–0.4.2, all paths verified live)
Reusable, parameterized, privilege-aware **dispatch templates** — a saved dispatch you run with one
click instead of retyping the modal. Mirrors the `privilege` domain. Memory `clawgate-runbooks`.
- **Domain** `internal/runbooks/` (`Runbook`/`Spec`/`ParamDef`/`Step`/`Run` + Postgres store;
  migration 0006 = `runbooks` + `runbook_runs` audit). `Runbook.Render(values)` does `{{param}}`
  substitution + appends a Steps checklist + "Done when".
- **UI** — a **Runbooks** section in the Agents tab, authored via discrete form fields (params/steps
  as small JSON in an Advanced area; raw `spec` JSON also accepted), one-click **Run** with a param
  form, run-history badge. Routes in `internal/api/runbooks.go` (`/ui/runbooks`, `POST /runbooks`,
  `POST /runbooks/{id}/dispatch`, …).
- **Dispatch** = render → create a task note → `createAndDispatchAgent` → auto-grant the runbook's
  privilege profiles → record a run. **Grant ordering**: grants recorded **before** provision
  (env/kubeconfig folds in), live RBAC applied **after** the namespace/SA exist.
- **Operator**: `operator_list_runbooks` + `operator_run_runbook{name,params,repo?}` native tools —
  "operator, run the X runbook".
- **Checkpoints**: a `Step{requiresApproval:true}` → the worker calls the `agent_checkpoint` tool,
  which files a request in the **same permission inbox** (reuses push + SSE + the Approve/Deny card
  — zero new UI), blocks until you decide, then returns `approved` + guidance. "Approve with
  comment" steers the agent.

## Production hardening — Wave 1 (0.5.0–0.6.0)
Panic-recovery middleware + `safeGo`; security headers (**CSP needs `'unsafe-eval'`** — htmx
`hx-on` uses `new Function`; 0.5.0 broke without it → 0.5.1 hotfix); WebSocket origin locked (was
`["*"]`); checkpoints exempt from the TTL sweep (24h) + 5-consecutive-miss tolerance; `/readyz`
DB-ping as the readinessProbe (liveness stays `/health`); DB `statement_timeout=10s`; perf indexes
(migration 0007); version via ldflags (`/health` JSON + startup log); **htmx error toasts** so
failed mutations are no longer silent.

**Wave 2 = CI + observability** — see `~/.claude/skills/clawgate/reference/telemetry.md`.

**Roadmap (Wave 2 remaining)**: resilience reconciler (stuck-`provisioning` zombies + retryable
kickoff), scale (N+1s / pagination / retention), durability (PITR vs accepting the 24h dump
window). Full backlog in HANDOFF.

## Secrets / env
In `deployment.yaml` + `clawgate-db.enc.yaml` (SOPS): `DATABASE_URL`, `CLAWGATE_ENCRYPTION_KEY`
(AES-GCM for the GitHub token at rest), `GITHUB_TOKEN`, `OPENROUTER_API_KEY`,
`GITHUB_CLIENT_ID/SECRET` (empty — OAuth unused).

Plain envs: `CLAWGATE_AGENT_IMAGE_REPO`; `CLAWGATE_AGENT_IMAGE_TAG` (**`2026.5.7`** — OpenClaw
2026.5.7+ required for native tools; an immutable retag of `test-tools-2026.5.7`);
`CLAWGATE_AGENT_MODEL`; `CLAWGATE_OPERATOR_MODEL` (operator-only,
`openrouter/anthropic/claude-haiku-4.5`); `CLAWGATE_AGENT_CALLBACK_URL` (in-cluster clawgate URL
for agent self-service; empty → default svc); `CLAWGATE_PUBLIC_URL`; `CLAWGATE_REQUEST_TTL` (5m);
`CLAWGATE_TASK_TTL` (idle-task reaper, `off`/`0` disables); `CLAWGATE_TAG_AUTODISPATCH` (off);
`CLAWGATE_FAKE_PROVISIONER` (e2e only). ⚠ `CLAWGATE_INSECURE_COOKIES` is a **dead env var** — no Go
code reads it (verified 2026-08-12); it survives only in `README.md` and `HANDOFF.md`, left over from
the pre-0.7.37 session-cookie model. Setting it does nothing.

**Editing SOPS**: `SOPS_AGE_KEY_FILE=.secrets/age.key sops -e -i <file>` works — `.sops.yaml` was
reconciled (a `clusters/workbench/apps/clawgate/.*.enc.yaml$` rule exists), so the old
`--config /dev/null --age …` workaround is **retired**. NB `harbor-cred.enc.yaml` MAC-mismatches
under the sops 3.13 CLI but Flux decrypts it fine — **leave it**.

## WebSocket / SSE over the public route
The nebula gateway nginx (**homelab + production**) has a dedicated
`map $http_upgrade $clawgate_connection { default upgrade; '' ''; }` + `proxy_set_header Upgrade/Connection` on the clawgate block, so agent chat (WS) and SSE share one upstream.
**Restart the `nebula-gateway` DaemonSet on BOTH clusters after editing it.**

## e2e tests
`containers/clawgate/e2e/` (Playwright, NixOS). Run `make e2e` — it uses
`pkgs.playwright-driver.browsers` and pins `@playwright/test` to the nixpkgs driver, **NOT
`npx playwright install`**; specs self-start clawgate + a throwaway `postgres:16-alpine`.
**83 pass / 2 skip** (the 2 need an in-cluster provisioner). It is the UI regression net — run it
for UI changes.

- 🔴 **A mass failure is more often the CSS-cwd trap or the box than a regression** — build
  `app.css` from `containers/clawgate/`, then run the pristine-`origin/trunk` baseline, BEFORE
  theorising. See the core SKILL.md deploy section.
- **GREENED 2026-07-05**: `login()` calls **`waitAppSettled`** (`helpers/fixtures.ts` — waits for
  no `.htmx-request` + tabs `data-cg-bound` before interacting). That is what killed the FAB
  "detached from DOM" flake — **NOT `networkidle`**, which never fires because the shell holds an
  SSE connection. `playwright.config.ts` has **retries 1 local / 2 CI + a 45s timeout** for
  residual load flakiness.
- The full-mode server sets **`CLAWGATE_FAKE_PROVISIONER=1`** so a **`NoopProvisioner`** registers
  the agent-detail routes without k8s (INERT in prod), letting `e2e/tests/agent-chat.spec.ts` seed
  an agent + task + STRUCTURED transcript and assert app-shell scroll / markdown title / tool chips
  / task modal / gated Send / NO_REPL-filtered.
- ⚠ Agent-detail elements can read "not stable" / "outside viewport" in headless (the live SSE
  connection keeps the layout churning) → use `evaluate(el=>el.click())` / class-toggle assertions,
  **not raw `.click()`** or computed-visibility.
- ⚠ **A KILLED `make e2e` leaks `clawgate-e2e-pg-*` containers** → clean them or the box starves and
  the FAB flakes: `docker rm -f $(docker ps -aq --filter name=clawgate-e2e-pg)`.

## Verify a real agent end-to-end (P0)
Dispatch → `kubectl get pods -A | grep devpod` → pod Running in ~90s → status `running` in the UI →
chat. Cleanup: delete from the UI (helm uninstall + ns delete).
