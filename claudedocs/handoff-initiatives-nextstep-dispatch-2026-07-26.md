# Handoff — Initiatives: next-step recommendation + dispatch (Phase 2a), 2026-07-26

**Next session's focus:** integrate a **logical next-step recommendation + one-tap dispatch** into the
initiatives subsystem — the read→act loop the whole thing points at. Everything below the "Next
session" heading is the plan; above it is the state you're resuming from. Operate via the **`initiatives`**
+ **`clawgate`** skills (both updated this session).

---

## What shipped this session (the arc)

1. **Initiatives agent Phase 1 — read-only, model-driven.** Retired the brittle regex intent-classifier;
   `/api/ask` is now a **model-driven OpenClaw devpod** (`devpod-initiatives`, `openclaw/initiatives`,
   **DeepSeek V4 Pro via OpenRouter**). The MODEL picks which deterministic skill-tool(s) to run
   (`scripts/initiatives/skills/query.py`, reusing assistant.py's pure `run_tool`/`build_facts`/
   `sources_of`); store via `_db.py` direct in-cluster mode + a **least-priv `initiatives_agent`
   SELECT-only PG role**. Viewer `/api/ask` proxies to the gateway (`agent_client.py`), **streams** the
   answer (`/api/ask/stream`, SSE) and **renders markdown**, falling back to the deterministic regex
   assistant when the devpod is down. Audit-logged (`intent=agent`).
2. **Session/activity-first discovery** (the big scan rework). Replaced `claudedocs/handoff-*.md`-glob
   discovery with `(repo, topic)` groups mined from Claude sessions + telemetry — topic = the transcript
   **`ai-title`** — behind a noise floor; handoff docs of ANY filename anchor by title. `source=doc|both|
   session`; session-only → `undocumented=true` → viewer **"Emerging" lane** (collapsed). Store **v4**.
   Fixed civitai-manager ("Pool 6") being invisible. 0 doc-slug churn.
3. **Hardening**, now via **kubeclaw 0.7.x chart VALUES** (postRenderers retired): egress default-deny
   CiliumNetworkPolicy, `securityContext` (cap-drop:[ALL] — safe because deps are baked into the
   `2026.6.11-py` image), `tls.verify:true`, `tools.web.search.enabled:false`. OpenClaw **2026.6.11**
   adopted (works because web-search-off avoids the doctor's npm plugin-fetch hang under locked egress).
4. **kubeclaw 0.6.0 → 0.7.0 → 0.7.1**: 0.6.0 = native `mcp.servers` key + `update.checkOnStart` (compat
   w/ latest OpenClaw); 0.7.0 = hardening as first-class values (securityContext/TLS/NetworkPolicy);
   0.7.1 = netpol fail-loud-on-empty-allowlist guard + full GitHub host doc. All audited.
5. **openclaw-image rebuild fixed** (PR #3): `--legacy-peer-deps` on the nested matrix-bot-sdk install
   (npm arborist `edgesOut` bug on node:22-slim's npm 10.9.8) — the base builds from source again.
6. **clawgate 0.7.69**: re-synced its vendored kubeclaw chart to 0.7.0 + rebuilt/redeployed.
7. **task-drafter hardened** (fleet rollout start): egress CNP + securityContext + tls.verify via 0.7.1
   values; NO cap-drop (non-baked 2026.6.1 image); web-search off; validated end-to-end (a real ticket
   drafted under the locked egress).

## Current live state (concrete)
- **Initiatives agent:** `devpod-initiatives` (homelab), image `2026.6.1-py`→now **`2026.6.11-py`**,
  DeepSeek V4 Pro, hardened via 0.7.x values, store **v4**, session-first discovery + Emerging lane.
- **Viewer:** http://192.168.50.250:8899 (workbench eth1, NOT .94). `/api/ask` = agent (stream+markdown),
  fallback = regex assistant on `vllm-recap` Qwen-7B.
- **task-drafter:** hardened, image `2026.6.1`, web-search off, netpol `task-drafter-egress`.
- **kubeclaw:** trunk **0.7.1** (`fbd13aa`). **clawgate:** **0.7.69** (embeds kubeclaw **0.7.0** — 0.7.1
  re-sync+rebuild PENDING). **openclaw-image** ghcr tags: `2026.6.1`, `2026.6.1-py`, `2026.6.11`,
  `2026.6.11-py`.
- Merged to devrc `main` + homelab-infra `trunk` throughout; the initiatives feature is 3 commits +
  hardening; task-drafter = `34a786b3`.

---

## NEXT SESSION — next-step recommendation + dispatch INTO initiatives (Phase 2a)

**Goal:** each initiative (documented AND emerging) surfaces a **recommended logical next step**, and a
**one-tap dispatch** that kicks that step off as a clawgate Task. This closes the read→act loop and is the
natural Phase 2 — but scoped to the STRUCTURALLY-SAFE tiers first (recommend = read-only; dispatch = a
card a human taps), deferring the write tier behind the structural gate the eval requires.

### The three tiers (ship in order; risk rises with each)
1. **Recommend next step (READ-ONLY — ship first, no new risk).** For each initiative derive/recommend
   the next logical step from data already in the store: the handoff doc's `next_step` (parsed), the
   session's `last-prompt` (current focus), momentum/state, open_investigations — PLUS the agent's
   synthesis (the OpenClaw agent reads the initiative's context and recommends). Add a
   `recommend_next_step(initiative)` skill-tool (or an agent synthesis prompt over the existing grounded
   tools). Especially valuable for **emerging/undocumented** initiatives (no handoff doc → infer the next
   step from sessions). Surface: a "next step" line per viewer card + an agent-chat answer ("what should I
   do next on X?"). Pure read — no state change, no new gate.
2. **Dispatch (STRUCTURALLY SAFE — a card, human-tapped).** One-tap → POST a **clawgate Task**
   (`POST /api/tasks`, `Authorization: Bearer $CLAWGATE_HOOK_TOKEN`; body `{directory:<repo>, body:<the
   next-step spec + the initiative's evidence>, model:<deepseek per [[task-dispatch-default-deepseek]]>,
   repo?, branch?}` — send `directory` NOT `title`, the known contract bug). Creates a durable card;
   NOTHING runs until Zach taps **Dispatch** (→ clawgate `helm install`s a worker devpod). This is the
   repo-cos pattern (`scripts/repo-cos/clawgate.py` `post_task` gets the contract right). The eval
   confirmed the **dispatch tier is structurally safe** (the agent only creates a card), so this needs NO
   structural write-gate. New capability = the agent/viewer gets a scoped clawgate hook token to POST
   tasks. Surface: a "Dispatch next step" button per viewer card + a chat action.
3. **Write (DEFERRED — needs the structural server-side gate).** Updating an initiative's `next_step` /
   writing-or-updating a handoff doc is the ONLY part that mutates durable state. Per the eval
   (`claudedocs/initiatives-agent-proposal-eval-2026-07-24.md` §3/§5), this needs a **structural
   server-side write-gate** (the tool refuses to write until a human approves — NOT the voluntary
   `agent_checkpoint`, which prompt-injection defeats) + the least-priv role widened narrowly. Do this
   LAST, only after 1+2 prove the loop.

### Why this order / what's already in place
- The store has the data; `query.py`/assistant tools + the agent exist; `route.py` already matches
  signals→initiatives (useful for shaping a dispatchable task). So tier 1 is mostly a synthesis surface;
  tier 2 is the repo-cos `/api/tasks` wiring + a scoped token. Tiers 1+2 are additive with no new
  security surface beyond "the agent can POST a task card" (bounded — worst case = spam cards, no
  execution; the eval verified `/api/auto-approve` is session-gated so a token-holding injected agent
  can't self-approve/execute).
- A natural first cut: wire it for the **Emerging lane** — an emerging initiative's biggest value is
  "here's the next step, dispatch it or promote it to a documented initiative."

### Guardrails to carry
- Recommendation is grounded (from store/session data + cited sources) — same anti-confabulation
  discipline as the read-only agent; never invent a next step not supported by the data.
- Dispatch = card only; keep the human tap.
- If you add write (tier 3): structural gate, not voluntary.

---

## Open items / loose ends (triage next session)
- **clawgate embed is at kubeclaw 0.7.0, not 0.7.1** — a re-sync (`make sync-chart`) + clawgate rebuild is
  pending to give newly-provisioned agents the 0.7.1 netpol fail-guards. Low urgency (existing agents
  unaffected).
- **task-drafter web-search** was disabled (its VERIFY never used it — confirmed against its SKILL). If
  Zach wants it back, allowlist `registry.npmjs.org` + `api.search.brave.com` and re-enable.
- **Workbench/client devpods NOT hardened** — deliberately left (per-agent egress mapping; `neverdat-sales`
  are client agents = different risk class). Needs Zach's direction per agent.
- **Emerging-lane precision** — kept the floor (Zach triages); tunable via `MIN_SESSION_TURNS`. Slug
  quality is ugly (over-merged token strings) — an audit 🟡; only affects the collapsed lane.
- **Audit 🟡s on discovery** (non-blocking): add `DISTINCT ON (repo,slug)` to the `latest` view
  (defense-in-depth vs the scan's no-dup guarantee); batch the `git rev-parse` fork-burst.
- **homelab-talos main checkout is dirty** — another session left ~42 uncommitted files (remix/joycaption/
  claudedocs + a STALE `initiatives/helmrelease.yaml` at `2026.6.1-py` that diverges from live/trunk).
  All my commits were based on `origin/trunk` (matches live); the dirty tree will confuse the next
  hand-edit — Zach should reconcile/discard it.
- **Unused ghcr tags** `2026.6.11`/`2026.6.11-py` left pushed (harmless; ready images).

## Verify-before-acting (memory-is-hypothesis)
Before Phase 2a, re-confirm live: the initiatives agent answers (`curl /api/ask/stream`), the store is v4
+ fresh (sync green), civitai-manager on the board + the Emerging lane populated, clawgate's `POST /api/tasks`
contract (`directory` not `title`). Standing release autonomy applies (merge+ship without gating, KEEP the
audit/verify gates) — see [[release-autonomy-standing]].
