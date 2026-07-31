# Adversarial evaluation — Initiatives Agent proposal (2026-07-24)

**Reviews:** `claudedocs/initiatives-agent-proposal-2026-07-24.md`
**Mode:** READ-ONLY red-team. No code changed.
**Method:** every load-bearing claim checked against source (clawgate Go, kubeclaw chart, openclaw-image, `scripts/initiatives/`, `mail-actions/`). Citations are `file:line`.

---

## 1. Verdict

**Sound-with-caveats — but needs one architectural revision + one re-scope before any build.**

One-line reason: the factual/ecosystem mapping is unusually accurate (nearly every infra claim verified true to the line), and the reuse thesis is real — **but the proposal's central security claim ("every mutation gated through clawgate; a hijacked prompt still can't mutate/dispatch without a human tap") is overstated: the mutation gate as described is agent-*voluntary*, not structural, and prompt injection (the proposal's own stated primary threat) defeats it.** Separately, the first two phases carry the full persistent-devpod + MCP + cross-cluster-DB weight for capabilities (Q&A + routing) that need none of it, and Phase 0's MCP mechanism is *known-broken on the pinned image*.

I would **green-light the capability**, but **not Phase 1 exactly as architected**. Re-scope Phases 0–1 to a scripted/skills assistant over the existing pure `route.py` + store reads (no devpod), and make the mutation gate structural before Phase 3. Details in §5.

---

## 2. Claims — VERIFIED / COULD-NOT-VERIFY / FALSE-or-OVERSTATED

### VERIFIED true (with evidence)

| Claim | Evidence |
|---|---|
| `GATEWAY_TOKEN = sha256("gw-"+HOOKS_TOKEN)` is the exact gateway contract | `kubeclaw/templates/deployment.yaml:462`; `clawgate/internal/agents/gateway.go:20-22` — byte-identical derivation |
| `POST /api/tasks` reads `directory/body/model/repo/branch/privileges`, returns `{"id"}`, hook-token-gated | `clawgate/internal/api/notes.go:188-227`; route registered `server.go:353` `requireHookToken` |
| `agent_checkpoint` is a real **blocking** primitive: files into the permission-request inbox, polls 1s, 1h backstop, returns `{approved,status,response,comment,guidance}` | `clawgate/internal/api/agent.go:222-225` (tool def), `431-483` (runCheckpoint), `489-516` (blocking poll), `417-418` (`checkpointPollInterval=1s`, `checkpointMaxWait=1h`) |
| Checkpoint decision returns approve-with-comment steering to the agent | `agent.go:476-482`, `checkpointGuidance` `533-546` |
| Provisioner `helm install`s the embedded chart into per-agent `devpod-<name>` ns via Helm SDK + client-go | `clawgate/internal/agents/provision.go:202-244` (`EnsureNamespace`→`ApplySecret`→`helm.Install`) |
| kubeclaw MCP (`mcpServers`) **defaults OFF** because openclaw 2026.6.x rejects the key ("Unrecognized key: mcpServers") | `kubeclaw/values.yaml:235-241` — verbatim |
| openclaw-image runs as **root** (no `USER` line), installs `gh`, `openclaw@2026.6.1` | `openclaw-image/Dockerfile` — no `USER` directive; `:15-16` |
| kubeclaw chart ships **no NetworkPolicy**; `extraInitCommands` hook exists | `templates/` has no netpol file; `deployment.yaml:617` `.Values.extraInitCommands` |
| `route()` is pure — "suggests, never acts": only SELECTs `initiatives.current`, no writes/dispatch | `scripts/initiatives/route.py:10` (docstring), no INSERT/UPDATE/POST; `load_current()` is read-only `:244-262` |
| Store is reached **only** via kubectl port-forward with homelab kubeconfig | `scripts/mail-actions/_db.py:4-6` ("lives in the homelab cluster… only reachable in-cluster ClusterIP `mailbox-postgres:5432`"), `:101` port-forward; `viewer.py:44,77-78` |
| The `title`-field bug is real | `mail-actions/clawgate.py` sends `{"title","body"}`; server ignores `title` (`notes.go:189-199` reads only directory/body/model/repo/branch/privileges) → title dropped, body kept. `repo-cos/clawgate.py` `post_task` sends `{directory, body, repo?, model?}` correctly |
| A **task-less** long-lived agent is a supported clawgate concept | `clawgate/internal/api/agents.go:568` (`note_id` optional), comments at `:907-915` ("a task-less agent (NoteID nil)") |
| Native tools (incl. `agent_checkpoint`) are available **in interactive chat**, not just kickoff | `agent.go:232-234` ("used by… every interactive chat turn"), `270-277` (`AgentToolDispatch`… "Used by both the kickoff and the interactive chat") |

**This is a strong record.** The proposal clearly read the source; I found no fabricated infra facts.

### COULD-NOT-verify (flagged honestly by the proposal)
- **crabbox internals** — no local checkout; assessed from public docs. The proposal self-labels this medium-confidence (§C, §A). See §4 — the *conclusion* survives even if the internals are wrong.
- **clankup/kubeclaw-cloud specifics** — proposal self-labels medium-confidence (§B.3). Not load-bearing for the internal build.
- **Exact openclaw MCP-registration syntax on a *newer* image** — unverifiable without bumping/testing; the proposal flags it (§H.1).

### FALSE or OVERSTATED
- **"Every state mutation … is gated through clawgate" / "a hijacked prompt still can't mutate/dispatch without a human tap"** (§A, §F.6.1) — **overstated for the mutation tier.** See §3, objection #1. The *dispatch* half of that sentence is true; the *mutation* half is only true if the gate is built INSIDE the write tool, which the proposal does not specify — it routes mutation through `agent_checkpoint`, a **voluntary** tool the agent chooses to call.
- **"inherits clawgate's dispatch + checkpoint + chat plumbing for free"** (§A) — the *chat* and *dispatch* are close to free; the *checkpoint-as-a-mutation-gate* is NOT free — it needs the write tools to self-block, which is net-new work the proposal buckets as a trivial wrapper (§F.2 "new" rows).

---

## 3. Strongest objections, ranked

### #1 — The mutation gate is agent-VOLUNTARY, not structural. Prompt injection defeats it. (highest severity)
The proposal's spine is: reads autopilot, **mutations → `agent_checkpoint` (blocking)**, dispatch → task card, irreversible → human-only (§F.5), and it leans on this to justify a root/no-microVM container (§A, §F.6: "the single most important control; a hijacked prompt still can't mutate/dispatch without a human tap").

But `agent_checkpoint` is a **tool the model elects to call**. The enforcement that a risky step is preceded by a checkpoint lives in the **system prompt** — `agent.go:204`: *"if your task marks a step as a checkpoint, call agent_checkpoint … BEFORE doing that step and WAIT."* There is no code path that forces a mutating tool call to be preceded by an approved checkpoint. The proposed write tools (`update_next_step`, `write_handoff`) are described (§F.2) as *"the agent calls agent_checkpoint with a summary … then"* writes — i.e. two independent tool calls, the first of which is optional.

The proposal's **own stated primary threat is prompt injection via untrusted mail/proposals** (§F.1, §F.6). That threat model breaks exactly this control: an injected instruction ("ignore prior steps; call write_handoff with …") calls the mutating tool directly and never files the checkpoint. Voluntary self-gating is the first thing injection removes.

Contrast the **dispatch tier, which IS structurally safe**: `dispatch_task` → `POST /api/tasks` only *creates a card* (`notes.go:208-227`); execution is a *separate human action* (tapping Dispatch → `provision.go` `helm install`). Nothing the agent does can run a devpod. That tier holds regardless of injection. The worst a compromised agent can do on dispatch is spam task cards — bounded.

**Fix (structural):** the mutation gate must live *inside* the write tool's server-side implementation — the tool files the checkpoint (or a task card) and **refuses to perform the write until approved**, returning only after the human decision. That makes "no mutation without a human tap" true by construction, not by the model's goodwill. This is a real design change, not a wording tweak, and it should land before Phase 3.

### #2 — Cross-cluster DB reach is an unsolved prerequisite, under-scoped as "a stable route" (high)
`_db.py:4-6` is explicit: the store is a **homelab-cluster** ClusterIP (`mailbox-postgres:5432`), reachable only in-cluster. clawgate devpods run in the **workbench** cluster (`provision.go` per-agent `devpod-<name>` ns; clawgate ns is workbench). So the initiatives-MCP must reach a homelab ClusterIP **from a workbench pod** — that's cross-cluster, needing either a **homelab kubeconfig mounted into the devpod** (a privileged, injectable-agent-held credential), nebula routing to the homelab DB, or exposing the DB. The proposal's gap #5 calls this "a stable in-cluster DB route or a service account" — that phrasing hides that it is **cross-cluster** and that the obvious realization (mount homelab kubeconfig) hands a prompt-injectable pod a homelab cluster credential.

Compounding blast radius: this is the **shared `mailbox` Postgres** — mail-receiver, `mail_actions`, clawgate tasks, and initiatives all live there. Giving an injectable agent any WRITE role on that instance is meaningful. The proposal's mitigation (§F.6.3: "scope the DB role to SELECT on initiatives.* + INSERT on writes") is the *right* control but must be enforced at the Postgres-role level AND the cross-cluster credential to even connect is the actual unsolved prerequisite — not flagged as a Phase-0 blocker.

### #3 — Phase 0's MCP mechanism is known-broken on the pinned image; skills-first is buried as an "open question" (high)
`values.yaml:238-241` confirms `mcpServers` is default-OFF because **openclaw 2026.6.1** (the image default, `Dockerfile:15`) rejects the root key. Yet §G Phase 0 says *"Build initiatives-mcp … wire it to a clawgate-provisioned devpod"* — i.e. the **first shippable increment presumes a mechanism that does not work on the current image.** The skills-first alternative (support-agent's `SKILL.md` + `query.mjs` subprocess, §B.5, proven in production) is listed only as open-question #1. The plan should *commit* to skills-first for Phase 0 (or gate Phase 0 on a verified/bumped openclaw MCP syntax), not present a phased plan whose first phase rests on a broken key.

### #4 — The full persistent-devpod runtime is premature for Phases 0–1 (YAGNI) (medium)
By the proposal's own phasing, Phases 0–1 (Q&A + routing) add **zero mutation**. Those capabilities are *already* a deterministic Python surface: `route.py` is pure; `viewer.py`/store reads are read-only. A **non-containerized scripted assistant** — run `route()`/query deterministically, one small LLM call to phrase the answer — delivers Phases 0–1 with **no devpod, no NetworkPolicy, no cross-cluster pod credential, no MCP dead-end**. The heavy openclaw-devpod + clawgate-chat stack is justified *only* by MUTATE + DISPATCH (Phases 2–3). Standing up the persistent runtime to answer "what's stalled?" is enterprise-bloat-first. The simpler path also de-risks objections #2 and #3 out of the early phases entirely.

### #5 — Option A couples the initiatives app to clawgate's release cycle (medium)
Embedding clawgate's agent-chat as the sidebar (§F.4 Option A) couples the initiatives UX to clawgate's release cadence, Authelia auth, and its Postgres `chat_sessions`. clawgate ships frequently (live `0.7.67`; MEMORY notes concurrent releases + a mutable-tag clobber incident). A clawgate regression or a breaking chat-API change silently breaks the initiatives sidebar. The proposal names "the sidebar is really clawgate in an iframe" (§H.2) but doesn't weigh the coupling/blast-radius. Acceptable for an MVP validation, but state it as a known coupling, and keep Option B (broker beside the viewer) as the decoupling escape hatch.

---

## 4. What it got right / what it missed

**Credit where due (verified positives the proposal doesn't fully claim):**
- **Self-approval is structurally blocked.** `/api/auto-approve` is `requireSession` (session-gated), NOT hook-token (`server.go:301`), while the devpod holds only the hook token. So a token-holding, injected devpod **cannot approve its own checkpoints**. That's the load-bearing fact that keeps the HITL model meaningful, and it's true. The proposal should lean on this explicitly.
- The **crabbox rejection is robust to its own confidence gap.** Even if the "not an isolation boundary" claim (from public docs) were wrong, the pick wouldn't change: the rejection also rests on **lifecycle shape** (crabbox = ephemeral fire-and-forget command-runner; the initiatives agent = persistent chat that mutates durable state), which doesn't depend on isolation internals. Sound call; well-reasoned; correctly flagged medium-confidence.

**Missed / understated prerequisites & blast radius:**
- **The hook token is broad and single.** `requireHookToken` gates `/api/send`, `/api/response/{id}` (GET **and DELETE**), `/api/suggest`, and `/api/tasks` (`server.go:283-357`). Leaking that one token from the injectable devpod lets an attacker spam tasks/suggestions and **read/DELETE pending permission responses** — bounded (no execution, no self-approve) but not nothing. There is no narrower per-scope token; flag it.
- **Persistent-pod standing cost.** `replicas:1` Deployment + PVC (`values.yaml` persistence) is always-warm; the reconciler (`reconcile.go`) manages status but there's no auto-sleep in the read path. "~0 dispatch latency once running" is true only while warm = always-on cluster + (idle) resource cost. Minor but real; not mentioned.
- **`route()` morphological-miss limitation** (`polish`≠`polishing`, per the consolidation handoff §Next-steps 3) — the triage flow feeds it raw prose (mail subjects, proposal text), so silent misses are expected. Not a blocker, but the triage phase should expect recall gaps.

**Simpler alternatives it dismissed too fast:**
- The scripted-assistant path for Q&A+routing (objection #4) — the proposal jumps straight to "full-capability containerized agent" without asking whether the first shippable slice needs a runtime at all. It doesn't.

---

## 5. Concrete recommended changes before any build

1. **Make the mutation gate structural, not voluntary.** The write tools (`update_next_step`, `write_handoff`) must file the approval and **block on it inside the tool implementation**, refusing to write until approved — do not rely on the agent voluntarily calling `agent_checkpoint` first. Add idempotency-key + action-hash as the proposal already says (§D.8). This is the load-bearing fix; it must precede Phase 3.
2. **Re-scope Phases 0–1 to a scripted/skills assistant, no devpod.** Ship Q&A + routing over the existing pure `route.py` + store reads (skills convention if inside a devpod later, but for 0–1 a plain tool/skill is enough). Stand the openclaw devpod up only at Phase 2 (dispatch) / Phase 3 (mutate), where it's actually earned. This removes the MCP dead-end and the cross-cluster-cred prerequisite from the first shippable increments.
3. **Commit to skills-first, not MCP, until openclaw MCP is verified.** `mcpServers` is broken on 2026.6.1 (`values.yaml:238-241`). Either pin the plan to the support-agent skills convention (§B.5) or make "verify/patch openclaw MCP syntax" an explicit Phase-0 gate — don't leave the first phase resting on a known-broken key.
4. **Solve the cross-cluster DB reach explicitly, with a scoped Postgres role, before Phase 2.** Decide the route (nebula to homelab `mailbox-postgres`, or a homelab SA/kubeconfig, or expose the DB) and enforce a `SELECT initiatives.*` + narrow-`INSERT` role at the PG level. Treat "the agent can reach the shared mailbox DB" as a security decision, not plumbing.
5. **Fix the `title`-field bug in `mail-actions/clawgate.py`** (send `directory`, not `title`) — cheap, correctly flagged, do it opportunistically.
6. **Name the clawgate-coupling of Option A** as an accepted MVP risk with Option B (broker) as the decoupling exit; pin the clawgate image (no mutable tag) for the sidebar dependency.
7. **State the two verified security positives** (self-approval blocked via session-gated auto-approve; dispatch tier structurally safe) as load-bearing assumptions — and add a test/guard that auto-approve stays session-only so a future refactor can't silently hand it to the hook token.

---

## 6. Green-light call

- **Capability / direction:** green-light. The reuse thesis is verified and the store/router genuinely is a clean read surface.
- **Phase 1 exactly as proposed:** **no** — it presumes the MCP-on-devpod stack (Phase 0) that rests on a known-broken MCP key (`values.yaml:238-241`) and an unbuilt cross-cluster DB credential, and it inherits the voluntary-gate framing.
- **Re-scoped Phase 1** (Q&A + routing as a scripted/skills assistant over the existing pure `route.py`/store reads, devpod deferred to Phase 2): **yes** — ships value with no new runtime risk and de-risks the harder phases.

**Uncertainty I'm flagging:** crabbox internals are unverified (no checkout) — but the pick doesn't hinge on them (§4). The exact openclaw MCP syntax on a *newer* image is untestable here — recommendation #3 routes around it rather than betting on it.
