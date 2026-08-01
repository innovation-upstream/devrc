# initiatives agent devpod — deploy, hardening, identity, DB reach

The Phase-1 `/api/ask` agent. Read this when **deploying/bumping the devpod**, changing its
HelmRelease/secret/egress, or debugging a crash-loop / confabulated first-person answer.
Not needed to simply ask a question or to operate the sync/viewer.

Homelab ns **`devpod-initiatives`**, svc **`initiatives-devpod:18789`**, model
`openclaw/initiatives` (**DeepSeek V4 Pro via OpenRouter**). Manifests:
`homelab-talos clusters/homelab/apps/agent-pods/initiatives/`.
Full arc: `handoff-initiatives-agent-phase1-2026-07-24.md`.

## Deploy
GitOps record is on homelab-infra **`origin/trunk`** (verified: the 0.7.x-values hardening
+ `2026.6.11-py` initiatives helmrelease + task-drafter hardening are all ancestors of
`origin/trunk`).

⚠ the LOCAL `~/workspace/homelab-talos` checkout may be **STALE/dirty** (another session
left uncommitted files + a behind `trunk`) — **always base edits on `origin/trunk` (fetch
first), never the local working copy.** agent-pods flux is `suspend=true`, so trunk changes
are applied **SURGICALLY by hand**.

`query.py`/skill ship in devrc → the devpod **autoPulls `main` (~5min)** — merge to devrc
main, done.

HelmRelease / secret / identity-pin (`extraInitCommands`) changes: edit the manifest, then
apply surgically:
```bash
KUBECONFIG=$KC_HOMELAB kubectl apply -f ~/workspace/homelab-talos/clusters/homelab/apps/agent-pods/initiatives/helmrelease.yaml
KUBECONFIG=$KC_HOMELAB kubectl -n flux-system annotate helmrelease initiatives-agent reconcile.fluxcd.io/requestedAt="$(date +%s)" --overwrite
```
The secret is sops-encrypted in git BUT the workbench **lacks the homelab age PRIVATE
key** → apply the live secret from plaintext via `kubectl` (the git `.enc.yaml` is the
GitOps record); encrypt with
`sops --encrypt --age <homelab-recipient> --config /dev/null` (no agent-pods
`creation_rule`).

## Hardening = first-class kubeclaw 0.7.x chart VALUES
(postRenderers + hand-written CNP are **RETIRED**.) The chart (kubeclaw ≥0.7.0) exposes the
three knobs directly in `values`:
- **`securityContext`** — toYaml full-replace → `capabilities.drop:[ALL]` +
  `allowPrivilegeEscalation:false` + seccomp `RuntimeDefault` (CapEff=0 verified).
- **`tls.verify:true`** → `NODE_TLS_REJECT_UNAUTHORIZED=1`, overriding the chart's
  historical hardcoded `0`.
- **`networkPolicy`** — Cilium egress default-deny + per-agent FQDN allowlist:
  kube-dns[+`rules.dns`]/apiserver/`mailbox-postgres:5432`/openrouter.ai/github.com/
  api.github.com/codeload/`*.githubusercontent`/`*.debian.org` on 443+80.

Verify in-pod: `curl openrouter.ai`→200, `curl example.com`→blocked.

This REPLACED the old `spec.postRenderers` strategic-merge (needed because `extraEnv`
duplicate-keys made SSA reject the Deployment) + the standalone `network-policy.yaml` —
both DELETED (functional no-op: identically hardened, cleaner).

⚠ **`networkPolicy.enabled` with an EMPTY allowlist silently bricks egress to DNS+API
only** — always set the full `egress.fqdns`/`endpoints` (kubeclaw 0.7.1 adds a fail-loud
guard for this; 0.7.0 does not).

`cap-drop:[ALL]` is safe **ONLY** because deps are baked (no dpkg at runtime).
**non-root is DEFERRED** — the chart hardcodes `/root/.openclaw|.ssh|.kube` writes with no
`runAsUser` knob (needs a chart change).

## Image `2026.6.11-py` + web-search OFF (the two are COUPLED)
`query.py`'s deps (psycopg2/requests) are **BAKED** into the derived image
(`initiatives/image/Dockerfile`, base `ghcr.io/zacxdev/openclaw-image`) so there is NO
apt-at-init to race the Cilium FQDN/DNS warmup — this is why `cap-drop:[ALL]` is safe.

**`tools.web.search.enabled:false`** is load-bearing for booting 2026.6.11 under the locked
egress: with search on, `openclaw doctor --fix` (pre-gateway) auto-enables a
brave/perplexity plugin and does a blocking `npm view @openclaw/<plugin>` fetch to
`registry.npmjs.org` (**NOT allowlisted**) → doctor hangs → gateway never binds `:18789` →
**crash-loop**. Off = no plugin auto-fetch → doctor completes offline → gateway binds. The
read-only Q&A agent never needs web search anyway; egress stays locked (no npm allowlist
added).

`config.updateCheckOnStart:false` is set belt-and-suspenders. **Rollback tag `2026.6.1-py`**
(a first 2026.6.11 attempt rolled back before web-search-off was found).

The shared `openclaw-image` base rebuilds via `--legacy-peer-deps` (npm arborist `edgesOut`
bug on `node:22-slim`, openclaw-image #3).

**To bump OpenClaw further:** keep web-search off + egress locked, verify the gateway
binds. (Historical: if you ever reintroduce apt-at-init it RACES the Cilium policy — baking
deps mooted this.)

## Agent identity must be PINNED or first-person questions confabulate
OpenClaw ships a generic "figure out who you are" onboarding (`BOOTSTRAP.md` + empty
`IDENTITY.md` in `/data/workspace`). Without a pin, *"what am **I** working on"* made the
agent read BOOTSTRAP and answer about **ITSELF** ("fresh start, who are you?").

Fix (in `extraInitCommands`): `rm BOOTSTRAP.md` + overwrite `AGENTS.md`/`IDENTITY.md` so the
agent is "already born" as the read-only initiatives assistant and "I/me/my/you" = **Zach**.
Clearly-matching Qs ("blocked on me", "whats stalled") routed to the skill fine; ambiguous
first-person ones did not.

## Agent DB reach = least-priv SELECT-only → the audit-log write stays VIEWER-side
The `initiatives_agent` PG role is SELECT-only. `CREATE TABLE IF NOT EXISTS`
(assistant.py's log self-heal) needs CREATE on the schema **even when the table exists** (it
errors before the existence check), so the agent can't write `assistant_log`.
`agent_client._log_agent_ask` writes it from the viewer (full mailbox creds).

**Don't grant the agent role INSERT to "fix" a missing audit row** — check the viewer path.
