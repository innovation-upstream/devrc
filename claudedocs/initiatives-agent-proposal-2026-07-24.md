# Initiatives Agent — Proposal (containerized full-capability agent + sidebar chat)

**Date:** 2026-07-24
**Status:** Proposal for review. READ-ONLY research; nothing built. `claudedocs/` is untracked — no PR, no code change.
**Author:** research + synthesis pass (Claude).

---

## A. Executive summary + recommendation

Build the **initiatives agent** as an **OpenClaw agent devpod, deployed by the existing `kubeclaw` Helm chart, driven and gated by `clawgate`, and reached through a sidebar chat embedded in the initiatives viewer** — *reusing the `support-agent` operator-chat pattern that clawgate has already re-implemented as a self-hosted service.* The agent's "brain" is not rebuilt: the initiatives **Postgres store** (`initiatives.latest`/`current`/`recaps`) and the pure **`route()`** matcher are exposed to the agent as an **MCP server** (`query_initiatives`, `route_signal`, `read_handoff`, `get_recap`, plus the *mutating* `write_handoff`/`update_next_step` and `dispatch_task`). Reads/routing run autonomously; every **state mutation and every dispatch is gated through clawgate** — either as a durable Task card (`POST /api/tasks`) that you tap "Dispatch" on, or as a blocking `agent_checkpoint` that returns your approve-with-comment steering to the agent.

**Runtime pick: openclaw-container via kubeclaw — NOT crabbox, NOT a from-scratch runtime.** Rationale in one line: crabbox is a *CI/test-execution lease broker* whose own docs say "do not use it as an isolation boundary" and which is built for ephemeral hermetic test runs, not a persistent interactive agent; the openclaw devpod is the runtime clawgate *already* provisions (via the kubeclaw chart's Helm-SDK path), so choosing it means the initiatives agent inherits clawgate's dispatch + checkpoint + chat plumbing for free instead of re-earning it. The one real weakness of that runtime — a plain container running as root with no egress policy — is closed with **modern best-practice hardening** (NetworkPolicy egress allowlist, file-based scoped credentials to dodge the known strip-env gotcha, and optionally a Kata/gVisor RuntimeClass), which matters here because the agent's threat model is **prompt-injection-driven tool misuse** (it triages *untrusted* inbound mail/proposals), not hostile multi-tenant code execution.

**Headline architecture:** `initiatives store + route()` → **initiatives-MCP** (read tools autonomous; write/dispatch tools gated) → **OpenClaw devpod** (kubeclaw) → **clawgate** (task cards + `agent_checkpoint` approvals + existing agent-chat) → **sidebar chat** embedded in `viewer.py`. Reuse over rebuild at every layer.

Confidence: high on the ecosystem mapping and the reuse path (read from source); medium on crabbox internals (public docs only, no local checkout); medium on the exact MCP-registration ergonomics inside openclaw 2026.6.x (the chart ships an MCP configmap but currently defaults it **off** — see the gotcha in §F/§H).

---

## B. Ecosystem findings — the "claw" stack and how the pieces fit

Zach has, in effect, already built most of a self-hosted "dockerized agent + operator chat + approval" platform. The initiatives agent should compose these, not re-derive them.

### B.1 `openclaw-image` — the agent container (the "openclaw container option")
`/home/zach/workspace/openclaw-image/Dockerfile`:
- Base `node:22-slim` (Debian); installs `jq git curl openssh-client gnupg python3 tini rclone poppler-utils unzip` **and `gh`** (GitHub CLI).
- `npm install -g openclaw@${OPENCLAW_VERSION}` (default `2026.6.1`, Renovate-bumped) plus `matrix-bot-sdk`.
- Runs as **root** (writes `/root/.openclaw/...`). README: "designed to be used with the KubeClaw Helm chart for deploying OpenClaw agent devpods."
- So this is the *unit of compute*: an npm-distributed OpenClaw agent runtime in a Debian container. It is a **container, not a microVM** — shared host kernel.

### B.2 `kubeclaw` — deploy the agent on k8s (`ZacxDev/kubeclaw`, chart v0.5.2)
`/home/zach/workspace/kubeclaw/` — Helm chart, "deploying OpenClaw AI agent **devpods**." Key facts from `templates/deployment.yaml` + `values.yaml`:
- The agent runs as a **`kind: Deployment`, `replicas: 1`** — **persistent, not an ephemeral Job**. `values.yaml`: *"Almost always 1 (the agent holds local session state and a single gateway)."* Backed by a **PVC** (`persistence.enabled`, workspace at `/data/workspace`).
- Two ports: **`18789`** (the OpenClaw gateway — OpenAI-compatible `/v1/chat/completions`) and **`18790`** (a skills API endpoint the container serves).
- **Credential model:** an *existing* Secret (`values.yaml` `existingSecret`) with keys `MATRIX_ACCESS_TOKEN, TELEGRAM_BOT_TOKEN, HOOKS_TOKEN, GITHUB_TOKEN, BRAVE_API_KEY, ANTHROPIC_API_KEY, git-ssh-key, …`, delivered via `envFrom`, then **jq-injected** into the OpenClaw config at init. The gateway auth token is **derived**: `GATEWAY_TOKEN = sha256("gw-" + HOOKS_TOKEN)` (deployment.yaml:462) — the exact contract clawgate and support-agent both speak.
- **`extraInitCommands`** hook runs after secret setup, before the gateway starts — *this is precisely where the strip-env credential-file fix belongs* (see §D.4/§B.6).
- **RBAC**: `Role`/`RoleBinding`/`ServiceAccount` + an orchestrator `ClusterRole` and a read-only one; the init even sets `kubectl` creds from the pod SA token. So the devpod can be given cluster reach — powerful, and a thing to scope carefully.
- **MCP**: `values.yaml` has an `mcpServers` block and the chart ships `configmap-clank-task-mcp.yaml` (a typed-task MCP server), **but it defaults OFF** with an explicit note: *"openclaw 2026.6.x rejects the `mcpServers` root key … gateway won't start."* → MCP-tool exposure is designed-for but has a live compatibility caveat to verify (§H).
- **Isolation posture:** no `NetworkPolicy` in the chart; container runs as root; no `RuntimeClass`. This is the gap best-practice hardening closes (§D, §F.6).

### B.3 `kubeclaw-cloud` = **clankup** (`ZacxDev/clankup`) — the SaaS control plane
`/home/zach/workspace/kubeclaw-cloud/` — a Go server (`main.go`, `handlers/`, `internal/`) that is the multi-tenant/cloud version of the stack. It ships **`Dockerfile.clawdbot`** (the agent image variant clawgate actually pulls — `harbor.homelab.lan/library/clawdbot:2026.5.7`), plus **`clankup-dspy`** (a DSPy sidecar for cheap typed structured-output, the same pattern support-agent adopted — see §B.5), **`clankup-stt`** (speech-to-text), and **`client-onboarding`**. It provisions/deploys agents and fronts them with an operator chat. *(Detail here is from directory shape + cross-references; a deeper read of `clankup`'s handlers was in flight and not fully folded in — treat clankup specifics as medium-confidence. It is the "productized" sibling of what we're building internally; the internal build does not need clankup, but it is where the DSPy sidecar source lives.)*

### B.4 `kubeclaw-embed` = **clankstack-embed** — embeddable variant
`/home/zach/workspace/kubeclaw-embed/` — `README.md`, `docker-compose.yml`, `charts/`, plus a **`broker/`** and **`embed/`**. This is the "drop a chat widget into someone else's page" variant: the `embed` is the front-end surface, the `broker` mediates between the embed and the agent runtime. Conceptually the same shape we want (a chat surface embedded in an app), but productized for external embedding; for the internal sidebar we can borrow the *idea* (a broker in front of the gateway) without adopting the whole embed stack.

### B.5 `support-agent` (civitai) — the prior-art blueprint we're copying
`/home/zach/workspace/civit/support-agent*` (four worktrees of `github.com/civitai/support-agent`). This is an **already-built dockerized agent + operator-chat UI**. Extracted pattern:
- **Daemon**: pure Node ESM `node:http` server (`src/server.mjs`), one vanilla-JS SPA (`src/ui.html`) + Tailwind; `/sse`, `/metrics`, REST. Multi-stage Dockerfile, **non-root uid 10001**, `tini` PID-1, creds via K8s Secret `envFrom` (never baked). Deployed as a **`Deployment`, replicas 1, `strategy: Recreate`** behind oauth2-proxy + Traefik.
- **Operator-chat** (`src/lib/operator-chat.mjs`, `operator-agent-client.mjs`, `operator-chat-session.mjs`): a fixed bottom **chatbar** that `POST`s `{message, session_id}` with `Accept: text/event-stream`, consumed in-browser via **`fetch()` + `ReadableStream.getReader()`** (POST-streaming, not `EventSource`). Server side is an **async-generator SSE proxy** to the agent's OpenAI-compatible gateway (`POST ${gatewayUrl}/v1/chat/completions`, bearer `sha256("gw-"+HOOKS_TOKEN)`), re-emitting deltas as `data:{"delta":"…"}`. Conversation persisted to a ClickHouse `ReplacingMergeTree` sessions table (30-day TTL, last-8-turns replay). Gates in order: **same-origin CSRF → per-IP token bucket → operator identity (`x-auth-request-email`) → feature flag → daily budget.**
- **HITL split**: operator-chat itself is *read-only Q&A* (its only "action" is a suggested dashboard filter emitted as a fenced ` ```action ` block). Real mutations live in a **drafter pipeline**: the agent *proposes* structured JSON, an operator **Approve/Reject/feedback**s in a Tickets tab, and only on Approve does the daemon's `executor.js` `switch(action.type)` execute the outbound action — with **pre/at-execution state-hash guards + idempotency markers**.
- **Tools = Claude-Code "skills"** (`.claude/skills/<name>/{SKILL.md,query.mjs}` invoked as subprocesses), not MCP/native-function-calling. ~35 skills (freshdesk, postgres-query, clickhouse-query, stripe, discord, …).
- **Model wiring**: migrated the *structured* drafting step from an OpenClaw agent to an in-pod **DSPy FastAPI sidecar on `localhost:9000`** — commit claims *"~100x cheaper per call, ~30x faster, 100% schema compliance via `dspy.Predict(DraftReply)` typed Literal fields."* Interactive chat stays on the agent gateway; typed extraction goes to DSPy. **Takeaway for us: route()/summary/triage typed outputs → DSPy or a small model; conversational Q&A → the agent.**

### B.6 `clawgate` — the action/approval path (already integrates agent + chat + dispatch)
Source: `/home/zach/workspace/homelab-talos/containers/clawgate/` (Go, gomponents+htmx, Postgres). Live `clawgate:0.7.67` on the workbench cluster, ns `clawgate`; NodePort **30302**, public `clawgate.zacx.dev` behind **Authelia forward-auth** (not basic-auth). **clawgate is, in effect, Zach's self-hosted re-implementation of the support-agent operator-chat + a dispatch/approval layer.** Concretely it already provides:
- **Task cards** — a "Task" is a `notes` row (statuses `open → in_progress → ready_for_review → complete`; agents may never set `complete`). Create via **`POST /api/tasks`** (auth: `Authorization: Bearer $CLAWGATE_HOOK_TOKEN`; body `{directory, body, model?, repo?, branch?, privileges?}`; returns `{"id": <int>}`). This is the repo-cos "approve → durable one-tap Dispatch" path.
- **Agent provisioning** — tapping Dispatch runs `Provisioner.Dispatch` (`internal/agents/provision.go`) which uses the **Helm Go SDK + client-go** to `helm install` the **embedded kubeclaw chart** of the **`clawdbot`** image into a per-agent namespace `devpod-<name>`, then kicks it off with the task body. So clawgate → kubeclaw → openclaw-devpod is a *working, deployed* pipeline today.
- **Agent chat** — WebSocket `GET /agents/{name}/ws`; server proxies to the devpod gateway `http://<name>-devpod.devpod-<name>.svc.cluster.local:18789/v1/chat/completions` with bearer `sha256("gw-"+HOOKS_TOKEN)`; sessions in Postgres `chat_sessions`/`chat_messages`. Same gateway contract as support-agent.
- **`agent_checkpoint`** (native tool, `internal/api/agent.go`) — the agent files a checkpoint (summary), which **reuses the permission-request inbox** as an Approve/Deny card, **blocks the agent** (poll 1s, 1h backstop), and returns `{approved, response, comment, guidance}` — i.e. **your approve-with-comment steering is returned to the agent.** This is the blocking human-in-the-loop primitive for risky/mutating steps.
- **Privilege requests** (`agent_request_privilege`) — a third, non-blocking, human-notified gate.
- Known contract bug to fix opportunistically: `scripts/mail-actions/clawgate.py` posts `{"title", "body"}` but the server only reads `directory/body/model/repo/branch/privileges` → the `title` is silently dropped (`repo-cos/clawgate.py` gets it right with `{directory, body, repo?, model?}`). Flagged for §H.

### B.7 The initiatives "brain" (built this session) — what we reuse verbatim
`/home/zach/workspace/devrc/scripts/initiatives/` + `scripts/session-analysis/initiative-scan.py`.
- **Store** (homelab `mailbox` Postgres, schema `initiatives`; reached via `mail-actions/_db.py` = kubectl port-forward + psycopg2 + DSN-from-secret, needs `KUBECONFIG=$KC_HOMELAB`):
  - `initiatives.snapshots` (append-only capture headers) + `initiatives.initiative_snapshot` (one row per initiative per snapshot). Columns: `host, repo, slug, title, summary, doc_date, momentum, last_touch, next_step, commits, commits_unknown, merged_prs, open_prs(jsonb), session_count, telem_events, telem_last, current_doc, open_investigations(jsonb), docs(jsonb), recent_messages(jsonb), recent_commits(jsonb)`.
  - View **`initiatives.current`** = newest row per (repo, slug) across *all* history (keeps recently-dormant "ghosts" — deliberately, for the router). View **`initiatives.latest`** = rows from the *most recent* snapshot only (for the viewer; no ghosts). Plus **`initiatives.recaps`** (`identity, identity_hash, status, status_hash` — a Layer-B LLM qualitative recap cache, LEFT-JOINed on (repo, slug), fail-soft).
- **Router** `route.py`: `route(signal_text, repo=None, limit=5) -> list[dict]` reads `initiatives.current` and calls the **pure** `rank_matches(...)`, which reuses the scan's `text_tokens`/`slug_tokens`/`best_title_match` scoring (slug_overlap + 0.25·title_overlap; df-uniqueness confidence gate). Returns ranked `{slug, repo, title, score, slug_overlap, title_overlap, matched_tokens, confident}`; `classify(ranked)` → "confident match: <slug>" vs "no confident match — likely new work." **It suggests, never acts.** This is `route_signal` for free.
- **Recaps** `recap.py`: builds `identity` + `status` recaps via a port-forwarded vLLM (OpenAI-compat) client, hashed for idempotent regen, upserted into `initiatives.recaps`.
- **Viewer** `viewer.py`: a stdlib `ThreadingHTTPServer` on **port 8899**; routes `/` (HTML), `/healthz`, `/api/initiatives.json` (the embedded JSON the SPA renders), `/api/initiative?repo=&slug=` (per-card detail incl. a live handoff read). Client-side vanilla-JS SPA with a few-second in-process cache. **Minimal** — good for a read view; not a place to bolt an LLM streaming proxy (§F.4).
- **Scan** `initiative-scan.py`: fuses handoff docs + git + activity telemetry + live tmux into momentum (●active/◐slowing/○stalled), next-step, and the live tmux session; `--json` output; it's what `sync.py` writes into the store.

---

## C. crabbox findings

**What it is (medium confidence — public docs only, no local checkout):** `openclaw/crabbox` is a **remote software-testing / execution *control plane*** in the OpenClaw ecosystem — a Go CLI + optional coordinator that **leases a box, syncs your dirty checkout (rsync), runs a command, streams output, and releases**, with central credential-holding, spend caps, idle expiry, and TTL. Tagline: *"warm a box, sync the diff, run the suite."* Latest public release ~v0.12.0.

Crucially, **crabbox is not itself a sandbox/isolation technology** — it *brokers* other providers. Its docs list providers including AWS EC2 / Azure VM / GCE / Hetzner / **Daytona** (managed, coordinator-brokered), plus direct/delegated: **E2B, Modal Sandbox, Islo, Sprites microVM (SSH), Tensorlake Firecracker, Semaphore CI**. Isolation is whatever the chosen provider gives. Its own README is explicit: *"Do not use it as a replacement for CI, a hostile multi-tenant sandbox, a secrets scrubber, or an **isolation boundary** between mutually untrusted users."* It's positioned for **hermetic test/agent CI evidence** (history, logs, JUnit, screenshots, recordings, PR publishing) via a repo-local **`.crabbox.yaml`** defining warmup/run/cleanup jobs. Defaults seen: idle 30m, TTL 90m, per-lease + monthly spend caps. It sits in an OpenClaw stack sketch as: **mitos** (snapshot-fork microVM engine) → **crabbox** (lease/sync/run) → **crabfleet** (mission control for agent runs).

**Verdict for this proposal:** crabbox is the wrong *shape* for the initiatives agent. We want a **persistent, interactive, chat-fronted agent that mutates durable state**; crabbox is an **ephemeral, fire-and-forget, hermetic command-runner** aimed at tests and agentic *jobs* — and it disclaims being an isolation boundary. It's an excellent tool for a *different* job (running the repo-cos/verify-agent style hermetic checks, or CI-shaped agent tasks that produce evidence), and worth keeping in mind for that. It is not the runtime for a long-lived initiatives assistant.

---

## D. Modern containerized-agent best practice (2026, cited)

The 2026 consensus, grounded in current sources:

**D.1 Isolation is threat-model-driven; containers are fine for *trusted* code, microVMs for *untrusted* code.** The three tiers ([Northflank](https://northflank.com/blog/how-to-sandbox-ai-agents), [emirb](https://emirb.github.io/blog/microvm-2026/)):

| Tier | Boundary | Boot | Overhead | Use when |
|---|---|---|---|---|
| Docker container | Shared kernel (process) | ms | minimal | **Trusted** workloads only |
| gVisor | User-space kernel (syscall interception) | ms | ~10–30% on I/O-heavy | Compute-heavy agents, cheap upgrade over plain containers |
| Firecracker microVM | Own kernel in KVM | ~125ms | <5 MiB/VM | **Untrusted code**, multi-tenant |
| Kata Containers | VMM-backed, OCI-compatible **RuntimeClass** | ~200ms | minimal mem | k8s teams wanting VM isolation without changing workflow |

Repeated guidance: *"For LLM-generated code execution, microVMs (Firecracker, Kata) are the only production-safe isolation layer."* But that's specifically about **executing untrusted/model-generated code**. The initiatives agent runs *curated tools*, not arbitrary attacker code — so the dominant risk is **prompt-injection → tool misuse**, and the highest-leverage controls are egress + credential scoping + HITL, with a RuntimeClass as a cheap defense-in-depth add.

**D.2 Egress/network control is the single most-emphasized control.** [Northflank](https://northflank.com/blog/how-to-sandbox-ai-agents): *"Block all outbound connections by default. Whitelist only required API endpoints."* Plus DNS restriction and segmentation from prod. For k8s that's a **default-deny `NetworkPolicy`** + an allowlist (the store's port-forward target, the model endpoint, clawgate, GitHub).

**D.3 Ephemeral vs persistent.** ([slavadubrov 2026](https://slavadubrov.github.io/blog/2026/05/26/ai-agent-runtime/)) Ephemeral = fresh sandbox per task, strongest isolation, pays cold-start; persistent (pause/resume, snapshot/fork) = warm cache for multi-hour/repeated sessions. **"The session must live *outside* the worker process"** — an append-only event log so a restarted worker can `wake(sessionId) → resume`. For a chat assistant, persistent-with-external-session is the right default (which is exactly clawgate's `chat_sessions` in Postgres).

**D.4 Credentials: never expose raw secrets to the model or sandbox.** The Anthropic pattern ([slavadubrov](https://slavadubrov.github.io/blog/2026/05/26/ai-agent-runtime/)): store secrets in a vault, **wire them into the sandbox at init**, and put an **MCP/proxy broker in front that holds the token and never returns it to agent code** — *"`git push` works; `cat ~/.ssh/id_rsa` does not,"* *"the harness is never made aware of any credentials."* This is the general form of Zach's **strip-env gotcha** (`openclaw-exec-sandbox-strips-env`): the openclaw exec sandbox strips secret env from agent-run commands, so write the secret to a **file** at container init (full env, via `extraInitCommands`) and have the tool read the file. Same principle: the tool boundary, not the env, holds the credential.

**D.5 State/memory: the "three-store" default** ([slavadubrov](https://slavadubrov.github.io/blog/2026/05/26/ai-agent-runtime/)): a **checkpoint DB (Postgres)** for graph/tool/approval state at every step; **git** for code/doc-shaped work; **object storage** for large artifacts. We already have Postgres (initiatives store + clawgate) and git (handoff docs) — no new infra needed.

**D.6 Per-agent k8s orchestration: Job-per-run is the textbook pattern, but adopt k8s only if you already run it.** ([slavadubrov](https://slavadubrov.github.io/blog/2026/05/26/ai-agent-runtime/)) One **Job per session** with `activeDeadlineSeconds`, a workspace PVC, an **MCP sidecar**, a RuntimeClass + NetworkPolicy; *"cold-start is too slow for sub-second startup … only adopt Kubernetes for agents if you already operate it."* Zach does → this is the natural fit, and clawgate's per-agent `devpod-<name>` namespace already realizes "per-agent isolation."

**D.7 MCP is the standard tool-exposure interface — and a real attack surface.** The Nov-2025 MCP spec formalized **OAuth 2.1** for remote servers ([WorkOS](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026)). But 2026 saw 30+ MCP CVEs; scans found large fractions of public MCP servers with **command injection, SSRF, path traversal, and no auth** ([Wiz](https://www.wiz.io/academy/ai-security/model-context-protocol-security), [CSA best-practices](https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/)). Implication: our initiatives-MCP must be **local-only (sidecar/cluster-internal, not public), authenticated, input-validated (parameterized SQL), and egress-scoped** — and the *mutating* tools must be gated, not merely exposed.

**D.8 Human-in-the-loop is durable-async, tiered by reversibility.** ([digitalapplied](https://www.digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026), [slavadubrov](https://slavadubrov.github.io/blog/2026/05/26/ai-agent-runtime/)) The approval is *"a durable sleep that consumes no compute, not a polling loop"*: persist state as `waiting_for_human`, enqueue a task with a TTL, resume from checkpoint on response; use an **idempotency key** and **hash the proposed action** at interrupt, verify at execution. The ecosystem-native framing ([getclaw](https://getclaw.sh/blog/human-in-the-loop-ai-agents-approvals-2026), *built on OpenClaw*) is a **four-tier model by reversibility/exposure**: **Autopilot (read-only)** → **Batch approval (reversible)** → **One-by-one (expensive/mutating)** → **Human-only (irreversible)**, each request showing *source evidence, expected result, downside if wrong*. This maps 1:1 onto clawgate's primitives (§F.5).

---

## E. Runtime comparison + pick

Framing: "which runtime hosts the initiatives agent?" — kubeclaw (deploy openclaw devpods on k8s) vs the raw openclaw container vs crabbox. These aren't fully mutually exclusive (kubeclaw deploys the openclaw container), so the table compares *the realistic deployment options*.

| Dimension | **openclaw-container via kubeclaw** (recommended) | raw openclaw-container (compose/bare) | crabbox |
|---|---|---|---|
| What it is | Helm chart deploying a persistent OpenClaw devpod `Deployment` | The `node:22-slim` OpenClaw image run directly | Lease-broker CLI for ephemeral hermetic runs over pluggable providers |
| Lifecycle | **Persistent** devpod (session state + gateway) | Persistent, hand-managed | **Ephemeral** lease (idle 30m / TTL 90m) |
| Isolation (out of box) | Container, root, no NetworkPolicy/RuntimeClass — **must harden** | Same, plus no RBAC scoping | Delegated to provider (E2B/Modal/Firecracker/…); *disclaims* being an isolation boundary |
| Fit for interactive chat | **Strong** — gateway `:18789`, clawgate already chats to it | Possible, but you rebuild the plumbing | **Poor** — designed for fire-and-forget command runs, not a chat loop |
| Coherence w/ existing stack | **Highest** — clawgate already `helm install`s this exact chart+image | Low — orphan from the deployed pipeline | Low for this use; good for CI/verify-style jobs |
| Dispatch + approval | **Free** via clawgate (`/api/tasks`, `agent_checkpoint`) | Build it | N/A (no HITL chat approval model) |
| MCP tool exposure | Chart supports it (mcpServers/clank-task configmap) — *caveat: default-off on 2026.6.x* | Manual | Not the interface |
| Credential model | Existing-Secret + jq-inject + `extraInitCommands` (strip-env file fix belongs here) | Manual | Coordinator holds provider creds; not app secrets |
| Cost/latency | 1 warm pod; ~0 dispatch latency once running | 1 pod | Per-lease cloud spend; cold lease each run |
| **Verdict** | **Pick** | Rejected (loses the integrated pipeline) | Rejected as the *agent runtime* (right tool for hermetic CI jobs, not a persistent assistant) |

**Pick: openclaw-container via kubeclaw, hardened.** It is the only option that (a) is a persistent interactive runtime, (b) is *already wired* to clawgate's dispatch/checkpoint/chat, and (c) matches best-practice "Job/pod-per-agent with MCP sidecar" once we add the missing NetworkPolicy + RuntimeClass + file-creds. Keep **crabbox in the back pocket** for evidence-producing hermetic *jobs* the initiatives agent might dispatch (e.g. "run the verify gate on repo X"), not as its home.

---

## F. Initiatives-agent architecture

### F.1 Capabilities → design consequence
The four confirmed capabilities decide the shape: (1) **Q&A over initiatives** (read), (2) **dispatch work** (act), (3) **create/update handoffs & durable state** (mutate), (4) **route & triage incoming** (route). Because it reads *and* mutates *and* dispatches *and* ingests untrusted signals, both the **runtime** (§E pick) and a **safe action/approval path** (§F.5) are load-bearing.

### F.2 The brain is already built — expose it as an MCP server (`initiatives-mcp`)
A small **local MCP server** (sidecar to the devpod, or cluster-internal service) wrapping the existing Python. No context re-derivation; the agent calls tools. Proposed tools and their exact backing:

| MCP tool | Backing (existing) | Kind | Gate |
|---|---|---|---|
| `query_initiatives(repo?, slug?, momentum?)` | `SELECT … FROM initiatives.latest` (+ `attach_recaps`) — as `viewer.py` does | read | autopilot |
| `route_signal(text, repo?, limit?)` | `route.route()` → `rank_matches` + `classify` | read | autopilot |
| `read_handoff(repo, slug)` | `viewer.py`'s `/api/initiative` live-doc read (`current_doc`) | read | autopilot |
| `get_recap(repo, slug)` | `initiatives.recaps` (`identity`/`status`) | read | autopilot |
| `list_stalled() / momentum_report()` | `query_initiatives` filtered on `momentum` (○/◐) | read | autopilot |
| `update_next_step(repo, slug, text)` | **new**: write a handoff/`next_step` (state mutation) | mutate | **clawgate `agent_checkpoint`** |
| `write_handoff(repo, slug, body)` | **new**: create/update a `claudedocs/handoff-*.md` (+ git) | mutate | **clawgate checkpoint** (+ git commit) |
| `dispatch_task(directory, body, repo?, model?)` | **existing**: `POST clawgate /api/tasks` (repo-cos pattern) | act | **clawgate Task card → human Dispatch** |

What's **missing** and must be built: the two write tools (`update_next_step`, `write_handoff`) — the store is currently *write-only via `sync.py` from the scan*; there's no human/agent-facing mutation path yet. These should write handoff docs (the durable source of truth the scan already reads) and/or the store, and must be **idempotent + hashed** (§D.8). `dispatch_task` already exists as a contract; the agent just needs the token (file-based, §D.4).

Note the **MCP compatibility caveat** (§B.2/§H): the kubeclaw chart's `mcpServers` is default-off because openclaw 2026.6.x rejected the config key. Verify the current openclaw MCP-registration syntax before committing to in-agent MCP; the fallback is the **support-agent "skills" convention** (`SKILL.md` + `query.mjs` subprocess) which needs no gateway MCP support and is proven in production.

### F.3 Model wiring
Two-speed, copying support-agent: **conversational Q&A** on the OpenClaw agent gateway (interactive, tool-calling); **typed/structured steps** (route classification, triage tagging, recap regen) on a **small model or a DSPy sidecar** (`clankup-dspy` is the existing source) — cheaper, schema-compliant, no agent-loop overhead. `route()` itself is *deterministic* already (no LLM), so triage is: `route_signal` (free, deterministic) → if `classify` says "new work," an LLM step drafts the new-initiative stub for approval.

### F.4 Sidebar chat — reuse, don't rebuild on the stdlib viewer
`viewer.py` is a deliberately minimal stdlib `http.server` (embedded-JSON SPA). **Do not** bolt an LLM streaming proxy, session store, CSRF/rate-limit/budget gates, and WebSocket handling onto it — that's the whole support-agent daemon, and clawgate *already is* that daemon. Two viable options:

- **Option A (recommended, lowest cost): embed clawgate's existing agent-chat as the sidebar.** clawgate already has a dockerized-agent + operator-chat + dispatch + checkpoint, all deployed. Provision one long-lived agent ("initiatives") in clawgate, give its devpod the `initiatives-mcp` tools, and embed clawgate's chat panel (`/agents/initiatives/...`) as a sidebar iframe/panel in the viewer. The viewer stays a read view; the chat is clawgate. Cross-origin/auth handled by Authelia (public) or LAN-trusted. **This reuses the most and builds the least.**
- **Option B (if a tighter in-app feel is needed): a thin support-agent-style broker beside the viewer.** A small Go/Node service (the `kubeclaw-embed` `broker` shape) that does the SSE async-generator proxy to the devpod gateway + a Postgres `chat_sessions` table + the support-agent gate stack, served as a sidebar `POST /chat` next to `viewer.py`. More work; only choose it if embedding clawgate's UI is too coupled.

Recommendation: **start with Option A** (validate the whole loop against clawgate's proven chat), and only graduate to Option B if the in-app UX demands it.

### F.5 Action/approval path (the guardrail, explicit)
Map the four-tier reversibility model (§D.8) onto clawgate's primitives:

- **Autopilot (read-only):** `query_initiatives`, `route_signal`, `read_handoff`, `get_recap`, momentum/stalled reports. No gate. (Sample-audit periodically.)
- **One-by-one / checkpoint (mutating state):** `update_next_step`, `write_handoff` → the agent calls **`agent_checkpoint`** with a summary + the proposed diff; it **blocks** until you Approve/Deny-with-comment; your comment steers it; the request + outcome are written to the task thread. Use idempotency-key + action-hash so a resumed approval applies exactly once.
- **One-by-one / task card (dispatching work):** `dispatch_task` → **`POST /api/tasks`** creates a durable card (evidence in the body); *nothing runs* until you tap **Dispatch**, which `helm install`s the worker devpod. This is the repo-cos pattern, already deployed.
- **Human-only (irreversible):** the agent never does these — no prod changes, no deletes, no secret rotation. It can only *propose* (a task card).

Each gated request must show, per getclaw's rule, **source evidence, expected result, and downside if wrong** — trivially satisfied because the checkpoint/task body carries the initiative slug + the `route()` evidence + the proposed change.

### F.6 Security posture (concrete)
The agent can act on Zach's infra and ingests untrusted mail/proposals → prompt-injection is the primary threat. Controls, in leverage order:
1. **Gate all mutation/dispatch through clawgate** (§F.5) — the single most important control; a hijacked prompt still can't mutate/dispatch without a human tap.
2. **Default-deny `NetworkPolicy`** on the `devpod-initiatives` namespace, allowlisting only: the mailbox-Postgres port-forward target, the model endpoint (OpenRouter/vLLM), clawgate (`:30302`/svc), and `github.com`. Closes the SSRF/exfil path (§D.2, §D.7).
3. **File-based, scoped credentials** via `extraInitCommands` (§D.4) — dodge the strip-env gotcha; the `GITHUB_TOKEN` and clawgate hook token go to `0600` files, not agent-run env. Scope the GitHub token to the repos the agent may touch; scope the DB role to `SELECT` on `initiatives.*` + `INSERT` on what writes need.
4. **MCP server is local-only** (sidecar/cluster-internal, never public), authenticated, **parameterized SQL** only (the store helpers already use psycopg2 params) (§D.7).
5. **RuntimeClass upgrade (defense-in-depth):** run the devpod under **Kata or gVisor** RuntimeClass. Cheap, and it contains a container-escape from any tool bug. Not strictly required (no untrusted *code* execution), but low-cost insurance given the agent's reach.
6. **Scope the pod's k8s RBAC** — the chart can grant an orchestrator ClusterRole; for the initiatives agent grant *read-only* (or none) cluster RBAC. It doesn't need to touch the cluster; it needs the store + clawgate + git.
7. **Reuse support-agent's chat gates** if Option B: same-origin CSRF + per-IP token bucket + identity + daily budget.

---

## G. Phased build plan (smallest shippable first)

**Phase 0 — MCP over the store, read-only (Q&A).** Build `initiatives-mcp` exposing `query_initiatives`, `route_signal`, `read_handoff`, `get_recap` (thin wrappers over the existing `viewer.py`/`route.py`/recap reads; parameterized SQL; local-only). Wire it to a clawgate-provisioned "initiatives" devpod. *Ship = you can chat "what's stalled / where did I leave clawgate" and get grounded answers.* No mutations, no new risk.

**Phase 1 — routing/triage.** Add `route_signal` into a triage flow: feed an inbound signal (a mail subject, a repo-cos proposal) → `route()` → confident match vs new-work; the agent *proposes* (in chat) where it belongs. Still read-only (proposal only). *Ship = "triage this idea" returns the right initiative + evidence.*

**Phase 2 — dispatch via clawgate (gated action).** Add `dispatch_task` → `POST /api/tasks`. Agent drafts a task body with evidence; a **card** appears in clawgate; you tap Dispatch. *Ship = "kick off work on the stalled X initiative" produces a one-tap card.* (Fix the `title`-field contract bug while here.) Credentials become file-based here.

**Phase 3 — state mutation via checkpoint (gated mutate).** Add `update_next_step` + `write_handoff`, each guarded by `agent_checkpoint` (blocking approve-with-comment) + idempotency/action-hash + a git commit for handoff writes. *Ship = "update the handoff for X with what we just decided" → checkpoint card → approve → handoff written.* This closes the loop: the agent maintains its own durable memory under your approval.

**Phase 4 — harden + embed.** NetworkPolicy egress allowlist, scoped RBAC, RuntimeClass (Kata/gVisor); embed the clawgate chat as the viewer sidebar (Option A). *Ship = the sidebar chat in the initiatives app, secured.*

Each phase is independently useful and independently reversible; Phases 0–1 add no mutation risk, Phases 2–3 add risk only behind a human tap.

---

## H. Open questions / risks for Zach

1. **MCP vs skills for tool exposure.** The kubeclaw chart ships an MCP configmap but defaults it **off** because openclaw 2026.6.x rejected the `mcpServers` root key. Do you want to (a) verify/patch the current openclaw MCP-registration syntax and use real MCP, or (b) expose the initiatives tools as support-agent-style **`SKILL.md` + `query.mjs` subprocess skills** (proven, no gateway dependency)? This is the biggest single fork in the plan.
2. **Sidebar = embed clawgate's chat (Option A) or build a broker beside the viewer (Option B)?** Option A reuses the most (clawgate already has agent-chat + dispatch + checkpoint) but the sidebar is really "clawgate in an iframe." Option B is a tighter in-app feel at the cost of rebuilding the support-agent daemon. I recommend A first — do you agree, or is in-app chat UX a hard requirement?
3. **Isolation ceiling.** The agent runs curated tools (not untrusted code), single-tenant, on your own infra — so I've prioritized **egress + credential scoping + HITL** over microVM kernel isolation, with Kata/gVisor as cheap defense-in-depth. Is that the right ceiling, or do you want full microVM (Kata RuntimeClass mandatory / crabbox-style ephemeral per-task) from day one? This trades latency/complexity for containment.

Secondary flags: (4) `mail-actions/clawgate.py` posts a `title` field the server drops — fix opportunistically. (5) The store is reached only via a `kubectl port-forward` (mail-actions `_db.py`) needing `$KC_HOMELAB` — a long-lived agent wants a stable in-cluster DB route or a service account, not a port-forward. (6) crabbox internals are medium-confidence (public docs only); if you want it evaluated as the *hermetic-job* runner for agent-dispatched verify/CI tasks, that's a separate small spike.

---

### Sources (best-practice claims)
- Northflank — [How to sandbox AI agents in 2026](https://northflank.com/blog/how-to-sandbox-ai-agents), [Best code execution sandbox for AI agents 2026](https://northflank.com/blog/best-code-execution-sandbox-for-ai-agents)
- [Your Container Is Not a Sandbox: MicroVM Isolation in 2026 (emirb)](https://emirb.github.io/blog/microvm-2026/)
- [Long-Running AI Agent Runtime in 2026 — sessions, sandboxes, checkpoints, harnesses (slavadubrov)](https://slavadubrov.github.io/blog/2026/05/26/ai-agent-runtime/)
- [Human-in-the-Loop Escalation Design for AI Agents 2026 (digitalapplied)](https://www.digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026)
- [getclaw — Human-in-the-Loop AI Agents: When Approvals Matter in 2026](https://getclaw.sh/blog/human-in-the-loop-ai-agents-approvals-2026) (built on OpenClaw; four-tier approval model)
- MCP security: [Cloud Security Alliance — Agentic MCP Security Best Practices](https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/), [Wiz — Understanding MCP Security 2026](https://www.wiz.io/academy/ai-security/model-context-protocol-security), [WorkOS — Everything about MCP in 2026](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026)
- crabbox: [openclaw/crabbox README](https://github.com/openclaw/crabbox/blob/main/README.md), [crabfleet #56 (stack sketch)](https://github.com/openclaw/crabfleet/issues/56)
