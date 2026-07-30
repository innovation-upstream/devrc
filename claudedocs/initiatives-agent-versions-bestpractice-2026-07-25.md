# Initiatives Agent — Version Currency + Containerized-Agent Best-Practice Audit

**Date:** 2026-07-25
**Scope:** (1) Are we on the latest OpenClaw / kubeclaw / base-image versions? (2) Does OUR initiatives devpod follow current (2026) best practice for containerized AI agents?
**Method:** Read our baseline from source, then web-verified every version + best-practice claim against live sources (accessed 2026-07-25). Live cluster posture checked against the homelab Talos cluster.
**Builds on:** `initiatives-agent-proposal-2026-07-24.md` §D and `…-eval-2026-07-24.md`. Where those still hold I cite them; where new evidence changes the picture I flag it (notably: the "MCP is dead on 2026.6.x" claim is **partly wrong** — see §MCP).

---

## VERDICT (one line each)

- **Latest versions?** ⚠ **No, but close.** kubeclaw is on its latest tag (v0.5.2, HEAD==tag). OpenClaw is **~10 patches behind on our own 2026.6 line** (pinned `2026.6.1`; latest 2026.6-line patch is **2026.6.11**, and the npm `latest` tag has moved to **2026.7.1**). `node:22-slim` is a floating tag on a still-supported LTS (EOL 2027-04-30) — currency is fine, reproducibility is not (unpinned).
- **Best practice?** ❌ **No — three hard gaps for a network-reachable agent.** No egress NetworkPolicy (**#1 gap**, verified none live), `NODE_TLS_REJECT_UNAUTHORIZED=0` disables all TLS verification, and the pod runs as **root with an empty securityContext** (no cap-drop/seccomp/runAsNonRoot; namespace has no Pod-Security labels). Our RBAC + least-priv DB role + credential handling **are** best-practice. Given the threat model (prompt-injection on read-only curated tools, single-tenant), these are all **cheap config fixes**, not a runtime re-platform.

---

## 1. Version currency table

| Component | Ours | Latest (verified 2026-07-25) | Gap | Action |
|---|---|---|---|---|
| **OpenClaw** (npm/image) | `2026.6.1` (Dockerfile ARG + image tag) | npm `latest` = **2026.7.1**; latest 2026.6-line patch = **2026.6.11**; beta 2026.7.2-beta.3 | ~10 patch releases behind on our line; 1 minor behind `latest` | **Bump to `2026.6.11`** (same minor line → security/bug fixes, low breaking risk). Treat 2026.7.1 as a **separate, tested** migration — it ships "breaking changes" + a new default model (GPT-5.6/ClawRouter). |
| **kubeclaw chart** | `v0.5.2` | **v0.5.2** (HEAD==tag; tags: v0.4.0/v0.5.0/v0.5.1/v0.5.2) | **None — we're current** | No bump available. The gaps below are things the **latest chart still lacks** → author them in the chart (it's Zach's repo). |
| **Base image** | `node:22-slim` (floating) | Node 22 LTS, active-supported; latest 22.x line got a security release ~2026-07-27 (CVE-2026-21717 fixed 22.22.2) | Floating tag → currency OK, **reproducibility not pinned**; Node 22 → Maintenance LTS Oct 2026, EOL 2027-04-30 | Pin a digest for reproducibility; rebuild picks up 22.x security fixes. Plan Node 24 before ~Q1 2027. Consider distroless/non-root base (see §BP-3). |
| **matrix-bot-sdk** (npm, in image) | floating (`npm install`, unpinned) | n/a | unpinned | Low priority; pin when you pin the image. |

**Adversarial flags on versions I could NOT fully verify:**
- **Exact "latest stable" OpenClaw is ambiguous across sources.** One aggregator said "2026.6.11 remains latest stable" while the npm `latest` dist-tag reads **2026.7.1**; GitHub's releases page returned **HTTP 403** to the fetcher, so I relied on search snippets + release aggregators (releasebot/newreleases/releases.sh), not the primary release list. Treat "2026.7.1 = latest, 2026.6.11 = latest on our line" as high-but-not-primary-sourced. **Verify locally before bumping:** `npm view openclaw dist-tags` and `npm view openclaw@2026.6 version`.
- **Whether bumping breaks our config is untested here.** OpenClaw silently reverts a schema-invalid `openclaw.json` to last-good (our chart's `config.onRevert: warn` exists precisely for this). Any bump must be validated with `openclaw doctor --fix` + confirming the gateway starts on the new pin.

---

## 2. Best-practice scorecard (scoring OUR devpod, not the generic proposal)

Threat model (unchanged from the eval, and correct): **prompt-injection → misuse of curated read-only tools**, single-tenant, on Zach's own infra. **Not** untrusted/model-generated code execution. That threat model makes **egress + credential-scoping + non-root/cap-drop** the high-leverage controls and **microVM kernel isolation optional**.

| # | Control (2026 best practice, cited) | What WE do | Verdict |
|---|---|---|---|
| BP-1 | **Egress: default-deny NetworkPolicy + allowlist.** "Block all outbound by default; whitelist only required endpoints" — the single most-emphasized agent control; without it a compromised/injected pod can exfiltrate or reach C2 ([Northflank](https://northflank.com/blog/how-to-sandbox-ai-agents), [KodeKloud](https://kodekloud.com/blog/running-ai-agents-safely-inside-kubernetes/), [Calico default-deny](https://docs.tigera.io/calico/latest/network-policy/get-started/kubernetes-default-deny)) | **Nothing.** `kubectl get netpol -n devpod-initiatives` → *No resources found* (verified live). Chart ships no NetworkPolicy template (`ls templates/` — none). Pod can reach anything on the network. | ❌ **Gap #1** |
| BP-2 | **TLS verification on.** Zero-trust egress assumes verified TLS to the allowlisted endpoints; disabling cert validation invites MITM on model/DB/GitHub traffic. | `NODE_TLS_REJECT_UNAUTHORIZED=0` **hardcoded** in `kubeclaw/templates/deployment.yaml:1058-1059` → every Node process (incl. OpenRouter + GitHub HTTPS) skips cert validation, cluster-wide for the pod. | ❌ **Gap** |
| BP-3 | **Non-root, drop capabilities, seccomp, restricted PSS.** Run as non-root (USER directive), `cap-drop: ALL`, `allowPrivilegeEscalation:false`, seccomp RuntimeDefault; enforce Pod Security **restricted** profile ([OX Security](https://www.ox.security/blog/container-security-best-practices/), [decryptiondigest hardening checklist](https://www.decryptiondigest.com/blog/container-security-hardening-docker-kubernetes), [Blaxel container-escape](https://blaxel.ai/blog/container-escape)) | Image has **no USER** (runs root); pod `securityContext` is **`{}`** (verified live — no runAsNonRoot, no cap-drop, no seccomp, no readOnlyRootFilesystem); namespace has **no `pod-security.kubernetes.io/*` labels** (restricted profile not enforced — verified live). | ❌ **Gap** (image writes `/root/...` so non-root needs image work; cap-drop/seccomp/no-privesc are free) |
| BP-4 | **Credentials: never expose raw secrets to the model; scope to least privilege; secrets via env/file at init, tool boundary holds the token** ([slavadubrov runtime](https://slavadubrov.github.io/blog/2026/05/26/ai-agent-runtime/), proposal §D.4) | **Strong.** DB reached with a dedicated **`initiatives_agent` SELECT-only** Postgres role via `MAILBOX_PG_DSN` (helmrelease:83-92); OpenRouter key + DSN via `existingSecret`/`secretKeyRef`, not baked; defensive Claude-cred cleanup in `extraInitCommands`. Read-only agent → no write creds to leak. | ✅ **Meets** |
| BP-5 | **RBAC least-privilege** (minimal permissions, scope to namespace) ([itsecurityguru](https://www.itsecurityguru.org/2026/05/02/securing-ai-agent-orchestration-enterprise-best-practices-2026/)) | **Strong, tighter than siblings.** Namespace-scoped read-only Role only; `clusterAdmin/orchestrator/clusterReadOnly` all disabled; `infraTools` off (no kubectl/flux in image). Uses the direct in-cluster DB route → needs **no** cluster RBAC (helmrelease:146-157). Deliberately tighter than the task-drafter. | ✅ **Meets** |
| BP-6 | **RuntimeClass (Kata/gVisor)** for kernel isolation — recommended specifically for **untrusted code execution / multi-tenant**; "acceptable for compute workloads where you control the code" ([Northflank](https://northflank.com/blog/how-to-sandbox-ai-agents), [K8s Agent Sandbox](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/), [systemshardening RuntimeClass](https://www.systemshardening.com/articles/kubernetes/runtimeclass-gvisor-kata/)) | **Not used**, and **not cheaply available here**: homelab exposes only the `nvidia` RuntimeClass (verified: `kubectl get runtimeclass` → `nvidia` only). No Kata/gVisor handler installed; adding one to immutable Talos nodes is non-trivial. | ⚠ **Partial / N-A** — correctly skipped given the threat model (curated tools, not untrusted code). Contra the proposal's "cheap defense-in-depth," it is **not cheap on this cluster**. De-prioritize. |
| BP-7 | **HITL / write-gate** — durable, tiered by reversibility; the gate must be **structural** (enforced in the tool), not agent-voluntary ([digitalapplied](https://www.digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026), [getclaw](https://getclaw.sh/blog/human-in-the-loop-ai-agents-approvals-2026), eval §3 #1) | **Phase-1 = read-only** (`AGENTS.md` + skill both hard-pin "strictly READ-ONLY, never write/dispatch/act"; DB role is SELECT-only → structurally cannot mutate). So the write-gate is **enforced by the data plane today**, which is the correct Phase-1 posture. | ✅ **Meets (Phase 1)** — but see §Phase-2 note |
| BP-8 | **MCP exposure** — MCP is the standard tool interface but a real attack surface; keep tool servers local-only, authenticated, input-validated ([Wiz](https://www.wiz.io/academy/ai-security/model-context-protocol-security), [CSA](https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/)) | We use the **skills/subprocess convention** (`query.py` invoked from the pinned skill), **not** MCP. Reads are parameterized SQL through the SELECT-only role. | ✅ **Meets** — and the "we *can't* use MCP" rationale is now **partly outdated** (see below). |

### On MCP — correcting the prior proposal/eval

The proposal (§B.2/§H.1) and eval (§3 #3) both state MCP is a **dead-end on 2026.6.x** because openclaw rejects `mcpServers`. Verified today, that's **half right**:

- The rejection is real, but the cause is a **wrong key name**, not missing support. OpenClaw's schema key is **`mcp.servers`** (CLI `openclaw mcp add`), documented at [docs.openclaw.ai/cli/mcp](https://docs.openclaw.ai/cli/mcp); the chart emits the legacy **root** `mcpServers` key, which the schema rejects ("Unrecognized key"). MCP config support requires **app ≥ 2026.3.24** — our `2026.6.1` already qualifies ([issue #32583](https://github.com/openclaw/openclaw/issues/32583), docs).
- So **native MCP is technically available on our pinned image** — it's a **chart-emission bug** (emit `mcp.servers`, not `mcpServers`), fixable without any OpenClaw bump.
- **Should we switch to MCP? No — not for this agent.** Newer OpenClaw actually *tightened* MCP security (2026.7.2: "scope MCP server connections to their requesting session"; MCP is a heavily-CVE'd surface per Wiz/CSA). For a **read-only, single-tool** assistant, the skills/subprocess path is simpler and has a smaller attack surface. Keep skills. The correction matters only if **Phase 2** wants richer typed tool exposure — then MCP-via-`mcp.servers` is a real option, not a blocker.

---

## 3. What's new since the 2026-07-24 proposal (verified still-current + additions)

- The proposal's §D best-practice sources (Northflank, slavadubrov, digitalapplied, getclaw, Wiz, CSA) **all still resolve and still reflect current guidance** as of 2026-07-25 — egress-first, threat-model-driven isolation, structural HITL. No reversal.
- **New:** the **Kubernetes SIG "Agent Sandbox" project** ([kubernetes.io, 2026-03-20](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/); [agent-sandbox.sigs.k8s.io](https://agent-sandbox.sigs.k8s.io/)) — a `Sandbox` CRD with **first-class gVisor/Kata** and scale-to-zero for stateful singleton agents. **In development, not GA (March 2026), and explicitly aimed at *untrusted code execution* / multi-tenant** — i.e. **not** what our curated-tool agent needs. Worth tracking, not adopting now. Its *recommended controls* (NetworkPolicy, non-root) are exactly BP-1/BP-3 above and **do** apply to us.
- **Reinforced consensus:** "no single control prevents container escape — layer overlapping controls"; **default-deny egress is the sustainable starting point**; **PSS restricted profile** as the k8s baseline ([OX Security](https://www.ox.security/blog/container-security-best-practices/), [KodeKloud](https://kodekloud.com/blog/running-ai-agents-safely-inside-kubernetes/)).

---

## 4. Ranked recommendations (highest-leverage first)

Legend: **cheap** = config-only, hours; **involved** = image/infra work. "Phase-2 weight" = matters *more* once the agent gains write/dispatch.

1. **[BP-1 · CHEAP · do first] Add a default-deny egress NetworkPolicy allowlisting only what the agent needs.** This is the #1 gap and the highest-leverage single control. Concrete homelab fix — a `NetworkPolicy` in the chart (or a raw manifest in the helmrelease) on `devpod-initiatives`:
   - `Egress` allow → **`mailbox-postgres.mailbox.svc.cluster.local:5432`** (the SELECT-only store, via podSelector/namespaceSelector on the `mailbox` ns), **kube-dns UDP/TCP 53**, **OpenRouter** (`openrouter.ai:443` — IP/ipBlock or an egress-gateway/FQDN policy if using Cilium), and **github.com:443** (autoPull clone/fetch).
   - `Egress` **default-deny everything else**; `Ingress` default-deny (nothing needs to reach it — Q&A is initiated by the agent/gateway, not inbound).
   - This closes the prompt-injection exfiltration path even for a fully-hijacked pod. **Verify Cilium/Calico FQDN support on the homelab CNI first** — if the CNI can't do FQDN egress, allowlist by resolved IP or route model/GitHub traffic through a known egress IP. *(Flag: I did not verify which CNI/FQDN capability the homelab runs — check before writing the policy.)*

2. **[BP-2 · CHEAP] Remove / scope `NODE_TLS_REJECT_UNAUTHORIZED=0`.** It's hardcoded in the chart deployment (`deployment.yaml:1058`), disabling TLS verification for *all* the pod's HTTPS (OpenRouter, GitHub, DB if TLS). If it exists to tolerate an internal self-signed endpoint, scope it via `NODE_EXTRA_CA_CERTS` (trust that one CA) instead of globally disabling verification. Chart change; benefits every devpod, so coordinate as a kubeclaw PR.

3. **[BP-3 · CHEAP half] Add a restrictive `securityContext` even while staying root-in-image.** Free wins that don't need image changes: `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`, `readOnlyRootFilesystem` where the workload tolerates it. Then **[INVOLVED]** rebuild the image with a non-root `USER` (the image writes `/root/.openclaw` → needs a writable home relocation) and finally **label the namespace `pod-security.kubernetes.io/enforce: restricted`**. Sequence: securityContext floor now → non-root image → PSS-restricted enforce.

4. **[Version · CHEAP] Bump the pinned OpenClaw `2026.6.1 → 2026.6.11`** (latest patch on our line; security/bug fixes, low breaking risk). Validate with `openclaw doctor --fix` + gateway-starts before rolling. Hold `2026.7.1` as a *separate, tested* migration (breaking changes; new default model — moot for us since we pin `deepseek-v4-pro`, but validate config schema). Confirm exact latest with `npm view openclaw dist-tags` first (aggregator sources conflicted).

5. **[Version · CHEAP] Pin the base image by digest** (`node:22-slim@sha256:…`) for reproducibility; keep a Renovate/periodic rebuild so 22.x security fixes (e.g. the ~2026-07-27 22.x security release, CVE-2026-21717) land. Node 22 is safe until 2027-04-30; schedule a Node 24 evaluation before then.

6. **[BP-7 · Phase-2 weight, INVOLVED — defer until write/dispatch exists] Make the write-gate STRUCTURAL before Phase 2.** Today read-only is enforced by the SELECT-only DB role + pinned prompt — correct for Phase 1. The eval's §3 #1 finding still holds: when Phase 2 adds mutation/dispatch, the gate must be enforced **inside the tool** (server-side blocking approval via clawgate `agent_checkpoint` / task card), **never** by the agent voluntarily calling a checkpoint tool — prompt injection removes voluntary self-gating. Keep dispatch structurally safe (task-card-only; execution is a separate human tap). **This is the control that matters most once capabilities grow.**

7. **[BP-6 · defer / likely skip] RuntimeClass (Kata/gVisor).** Correctly optional for this threat model, and **not cheap here** — homelab has no Kata/gVisor handler (only `nvidia`), and installing one on immutable Talos is real work. Revisit only if the agent ever executes untrusted/model-generated code, or track the K8s Agent Sandbox project as it approaches GA.

**Also opportunistic (from the eval, still valid):** fix the `mail-actions/clawgate.py` `title`-field bug (send `directory`, not `title`); the correction that native MCP is available via `mcp.servers` (chart emits the wrong key) — not needed for Phase 1, relevant if Phase 2 wants typed MCP tools.

---

## Sources (all accessed 2026-07-25)

**Versions**
- OpenClaw npm latest / calver: [newreleases.io/npm/openclaw](https://newreleases.io/project/npm/openclaw/release/2026.5.7), [npm openclaw versions](https://www.npmjs.com/package/openclaw?activeTab=versions), [releasebot](https://releasebot.io/updates/openclaw), [releases.sh/openclaw](https://releases.sh/openclaw/openclaw)
- OpenClaw release notes (2026.6.5 MCP tool-result coercion; 2026.7.1 breaking/GPT-5.6/ClawRouter; 2026.7.2 MCP session-scoping, plugin `--force`): [github.com/openclaw/openclaw/releases/tag/v2026.6.5](https://github.com/openclaw/openclaw/releases/tag/v2026.6.5), [openclaw.com.au/updates](https://openclaw.com.au/updates)
- MCP config key `mcp.servers` + min app 2026.3.24: [docs.openclaw.ai/cli/mcp](https://docs.openclaw.ai/cli/mcp), [issue #32583](https://github.com/openclaw/openclaw/issues/32583), [issue #24008](https://github.com/openclaw/openclaw/issues/24008)
- Node 22 LTS EOL 2027-04-30 / security: [nodejs.org v22.20.0](https://nodejs.org/en/blog/release/v22.20.0), [nodejs.org July 2026 security](https://nodejs.org/en/blog/vulnerability/july-2026-security-releases), [HeroDevs EOL dates](https://www.herodevs.com/blog-posts/node-js-end-of-life-dates-you-should-be-aware-of)

**Best practice**
- Egress/default-deny + agent sandboxing: [Northflank — sandbox AI agents 2026](https://northflank.com/blog/how-to-sandbox-ai-agents), [KodeKloud — AI agents in K8s](https://kodekloud.com/blog/running-ai-agents-safely-inside-kubernetes/), [Calico default-deny](https://docs.tigera.io/calico/latest/network-policy/get-started/kubernetes-default-deny)
- Container hardening (non-root, cap-drop, PSS restricted, distroless): [OX Security](https://www.ox.security/blog/container-security-best-practices/), [decryptiondigest checklist](https://www.decryptiondigest.com/blog/container-security-hardening-docker-kubernetes), [Blaxel — container escape](https://blaxel.ai/blog/container-escape)
- RuntimeClass / microVM isolation: [systemshardening RuntimeClass](https://www.systemshardening.com/articles/kubernetes/runtimeclass-gvisor-kata/), [K8s Agent Sandbox blog](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox/), [agent-sandbox.sigs.k8s.io](https://agent-sandbox.sigs.k8s.io/)
- HITL + MCP security (carried from proposal §D, re-confirmed current): [digitalapplied HITL](https://www.digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026), [getclaw approvals](https://getclaw.sh/blog/human-in-the-loop-ai-agents-approvals-2026), [Wiz MCP security](https://www.wiz.io/academy/ai-security/model-context-protocol-security), [CSA agentic MCP best practices](https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/)

**Live cluster evidence (homelab Talos, 2026-07-25):** `kubectl get runtimeclass` → only `nvidia`; `kubectl get netpol -n devpod-initiatives` → none; pod `securityContext` → `{}`; namespace → no `pod-security.kubernetes.io/*` labels; `initiatives-devpod` pod 4/4 Running. Local: `git -C kubeclaw describe --tags` → `v0.5.2` (HEAD==tag); `NODE_TLS_REJECT_UNAUTHORIZED=0` at `kubeclaw/templates/deployment.yaml:1058`.
