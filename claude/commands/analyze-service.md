---
name: analyze-service
description: "Recon a service/subsystem fast: locate where it lives, load its config, check its live state and recent changes — then optionally do the follow-on task. Replaces hand-typed 'analyze the {redis,minio,flux,bastion,monitoring,…} setup, then …' recon."
argument-hint: "<service> [then <follow-on task>] — e.g. 'redis', 'externaldns then bump the chart', 'monitoring'"
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# /analyze-service — pre-cached subsystem recon

Goal: kill the repeated hand-typed "analyze the X setup" recon. Re-derive the map **live** every run (don't trust a frozen registry — config + cluster are the source of truth), present a tight recon brief, then proceed to the follow-on if one was given.

Input: `$ARGUMENTS`. Split it into:
- **service** — the subsystem to recon (first token / quoted phrase, e.g. `redis`, `externaldns`, `monitoring`, `bastion`, `flux`, `minio`, `tekton`).
- **follow-on** — anything after `then` / `,` / `&&` (optional). Empty → recon only, then wait for direction.

## Where to look (infra repo roots — discover, don't assume)
- `/home/zach/workspace/homelab-talos` (homelab cluster gitops)
- `/home/zach/workspace/civit/datapacket-talos` (civitai production gitops; rich grounding in `clusters/production/apps/AGENTIC_LEVERAGE.md`)
- the current working repo, if it's neither of the above
If the service obviously belongs to one repo, scope there; if ambiguous, search both and say which repo owns it.

## Recon steps

1. **Locate (deterministic, parallel).** Glob/grep the service name across the repo root(s) to find its directory + manifests: `kustomization.yaml`, `HelmRelease`, `Deployment`/`StatefulSet`/`DaemonSet`, `ConfigMap`, `*values*.yaml`. Identify the **namespace** and the owning **kustomization/Flux Kustomization**. Prefer the Grep/Glob tools; for a broad sweep dispatch an **Explore** subagent and have it return file paths + the key config excerpts (not whole-file dumps).

2. **Config.** Read the manifests found. Pull out the load-bearing knobs: image/chart version, replicas/HPA, resources, key env/ConfigMap values, mounted secrets (names only — never print secret contents), exposed routes/services, dependsOn.

3. **Recent changes.** In the owning repo: `git log --oneline -10 -- <service-path>` to surface what last moved (a recent revert/bump is usually why you're looking).

4. **Live state (only if a cluster is reachable — never fabricate).** Pick the context that matches the owning repo (datapacket-talos → `admin@civitai-talos`; homelab-talos → its documented context) and **state which context you used**. Then, read-only:
   - `kubectl -n <ns> get pods,deploy,sts,svc` (+ `--context`)
   - `kubectl -n <ns> get events --sort-by=.lastTimestamp | tail`
   - `flux get helmrelease -n <ns> <name>` / `flux get kustomization` if Flux-managed
   - restarts / not-Ready / recent crashloops worth flagging
   If no context matches, the cluster is unreachable, or access is denied: **say so plainly and skip** — present the static recon and note live state is unverified.

## Output — recon brief

- **Service** + one-line "what it is".
- **Lives at**: repo + path(s) as `file:line` (clickable), namespace, owning kustomization.
- **Config**: the load-bearing knobs (version, scale, resources, key values, routes, deps).
- **Live**: pod/HR/kustomization status + anything unhealthy — or "unverified (no cluster access)".
- **Recent changes**: last few commits touching it, flag any revert/bump.
- **Gotchas**: anything non-obvious you hit (lying status conditions, stale comments, ephemeral-vs-durable, etc).

Keep it dense — file:line over prose. No marketing language; flag uncertainty.

## Then

- **No follow-on** → stop after the brief and wait for direction.
- **Follow-on is investigate/check/explain** → continue inline; the recon is now cached in context.
- **Follow-on is implement/build/fix** → follow the standing rule: dispatch a subagent, ensure test coverage where applicable, work on a feature branch ending in a PR (unless it's a one-liner/throwaway). The brief you just produced is the subagent's grounding.

Pair: `/handoff` (capture what you found), `/find-session` (recover a past session on this service).
