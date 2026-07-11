---
name: analyze-service
description: "Recon a service/subsystem fast: locate where it lives, load its config, check its live state and recent changes — then optionally do the follow-on task. Replaces hand-typed 'analyze the {redis,minio,flux,bastion,monitoring,…} setup, then …' recon."
argument-hint: "<service> [then <follow-on task>] — e.g. 'redis', 'externaldns then bump the chart', 'monitoring'"
allowed-tools: Bash, Read, Write, Grep, Glob, Agent
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

## Cache / index (local pointer & nuance layer)

Alongside the live recon, `/analyze-service` keeps a **local, out-of-repo, gitignored** markdown pointer/nuance sheet per service, so each run front-loads "this bit us with X" instead of re-discovering every gotcha from scratch. The sheet holds **pointers + nuance only** — never live state, never re-derived config values.

- **Location:** `~/.claude/analyze-service-index/<repo-slug>/<service>.md` — a LOCAL cache under the user's home `.claude`, **not** versioned in any cluster repo (nothing to gitignore inside a repo — it never lands there).
- **`<repo-slug>`** is the basename of the owning repo root the service resolved into: `datapacket-talos`, `homelab-talos`, or (if it lives in neither infra repo) the current working repo's basename. Derived from the same locate step below — no separate assumption. If those infra-repo roots ever move, this slug derivation moves with them.
- **`<service>`** is normalized: lowercase, any char outside `[a-z0-9._-]` → `-`, collapsed — applied identically on read and write so `External DNS` / `externaldns` / `external-dns` all resolve to one `external-dns.md`. Match `$ARGUMENTS` against the filename **and** the file's `aliases:` before deciding a service has no index yet.
- **Lazy — nothing pre-created.** `~/.claude/analyze-service-index/` may not exist; the dir + a service file appear only on a confirmed write-back (see "## Write-back (opt-in)").

**File schema** (markdown, so prose is surfaced verbatim via Read and reads well in a diff):
- **Front-matter — identity only:** `service` (canonical name, matches filename), `aliases` (alternate spellings), `repo` (owning repo basename — human note), `namespace` (human note; may be `multiple` for umbrella services). No machine/location fields.
- **`## What it is`** — one-line description. For an umbrella/multi-instance service (redis, monitoring, meilisearch) enumerate the instances in prose — it's an index OF instances, not one location.
- **`## Pointers`** — each entry is a path/slug + one-clause why, **never a copy** of the pointed-to content:
  - `manage-* skill:` the matching skill (e.g. `manage-redis`) — invoke it for ops.
  - `MEMORY.md slug(s):` slug filename(s) in the project memory dir (datapacket-talos: `/home/zach/.claude/projects/-home-zach-workspace-civit-datapacket-talos/memory/`).
  - `claudedocs handoff(s):` handoff doc path(s).
- **`## Nuance / work-history`** — dated bullets, newest-first, ≤2 lines each: a gotcha, a lying/misleading status condition, a revert or bump that explains why someone was looking, an incident tie-in. Prune-on-resolve.

**Read at recon START.** When an index file exists for the resolved service, surface its `## Pointers` + `## Nuance / work-history` **before** re-discovering gotchas below — front-load the curated recall, then run locate/config/live live as usual. Label these `from index` in the brief. A read miss (no file) just proceeds with today's behavior and may offer to create the file on write-back.

## Recon steps

1. **Locate (deterministic, parallel).** Glob/grep the service name across the repo root(s) to find its directory + manifests: `kustomization.yaml`, `HelmRelease`, `Deployment`/`StatefulSet`/`DaemonSet`, `ConfigMap`, `*values*.yaml`. Identify the **namespace** and the owning **kustomization/Flux Kustomization**. Prefer the Grep/Glob tools; for a broad sweep dispatch an **Explore** subagent and have it return file paths + the key config excerpts (not whole-file dumps).

   Once the owning repo (hence `<repo-slug>`) is known, **read `~/.claude/analyze-service-index/<repo-slug>/<service>.md` if it exists and surface its `## Pointers` + `## Nuance / work-history` first** (labelled `from index`), before deriving any gotchas below — this is the front-load. The locate/config/live steps still run live every time; the index only supplies pointers and prior nuance, never a cached location or status. A miss just proceeds.

2. **Config.** Read the manifests found. Pull out the load-bearing knobs: image/chart version, replicas/HPA, resources, key env/ConfigMap values, mounted secrets (names only — never print secret contents), exposed routes/services, dependsOn.

3. **Recent changes.** In the owning repo: `git log --oneline -10 -- <service-path>` to surface what last moved (a recent revert/bump is usually why you're looking).

4. **Live state (only if a cluster is reachable — never fabricate).** Pick the context that matches the owning repo (datapacket-talos → `admin@civitai-talos`; homelab-talos → its documented context) and **state which context you used**. Then, read-only:
   - `kubectl -n <ns> get pods,deploy,sts,svc` (+ `--context`)
   - `kubectl -n <ns> get events --sort-by=.lastTimestamp | tail`
   - `flux get helmrelease -n <ns> <name>` / `flux get kustomization` if Flux-managed
   - restarts / not-Ready / recent crashloops worth flagging
   If no context matches, the cluster is unreachable, or access is denied: **say so plainly and skip** — present the static recon and note live state is unverified.

## Output — recon brief

Header line: which index file was read + hit/miss — e.g. `index: datapacket-talos/redis.md — pointers loaded` or `index: none (first run)`.

- **Service** + one-line "what it is".
- **Pointers / nuance** (`from index`): the `## Pointers` + `## Nuance / work-history` surfaced at recon start, if any — curated recall to follow for detail, not this-run observation. Omit if the index missed.
- **Lives at**: repo + path(s) as `file:line` (clickable), namespace, owning kustomization. Always **re-derived live** (Phase 1 never caches location).
- **Config**: the load-bearing knobs (version, scale, resources, key values, routes, deps). Always **re-derived live**.
- **Live**: pod/HR/kustomization status + anything unhealthy — or "unverified (no cluster access)".
- **Recent changes**: last few commits touching it, flag any revert/bump.
- **Gotchas**: anything non-obvious you hit (lying status conditions, stale comments, ephemeral-vs-durable, etc).

Provenance honesty: nuance/pointers are `from index`; location/config are `re-derived live`; live state is `live @ <context> <timestamp>` or `unverified (no cluster access)`. Never present index recall as live observation.

Keep it dense — file:line over prose. No marketing language; flag uncertainty.

## Write-back (opt-in)

Recon stays **read-only by default** — the index is mutated only when a run surfaces something notable AND the user confirms, shown as a **diff first**. Never silent-mutate.

1. Run the recon brief (read-only) as usual.
2. **After** the brief, evaluate whether it surfaced anything **notable** (below).
3. Nothing notable → **do nothing**, say `index unchanged`.
4. A proposed change → present it as a **unified diff** against the current index file (or "new file" for first-ever), one compact block, and ask a single yes/no: *"append this to the index? (y/N)"*.
5. **Write only on explicit confirm.** On confirm, re-read the file (so a concurrent append isn't clobbered), re-apply the change to current bytes, then plain Write to `~/.claude/analyze-service-index/<repo-slug>/<service>.md` (creating the dir/file if first-ever). On decline, discard — the recon result already stood on its own. The write is local and final; there is no commit/worktree step (the file is outside every repo).

**Notable — append-worthy** (matches the "Gotchas" spirit + the `MEMORY.md` "durable lesson, not status" bar):
- A **gotcha**: non-obvious behavior, a lying/misleading status condition, an ephemeral-vs-durable trap, a wrong-looking-but-correct error string.
- A **revert or bump** found in `git log` that explains *why* someone was looking.
- An **incident tie-in**: the recon connected the service to a firing alert / a known `MEMORY.md` slug / a handoff — record the pointer.
- A **new pointer** discovered (a `manage-*` skill or slug the index didn't yet reference).

**NOT notable — never append:**
- Routine **healthy** state (pods Ready, canary Succeeded, no events) — live status, belongs nowhere durable.
- Config **values** (replica counts, image tags, env) — re-derived live, never persisted.
- Anything already captured verbatim by a pointer target — add/keep the pointer, don't copy the content.

**Auto-discovered pointers** (propose in the diff, still confirm-gated — a bad grep match must be rejectable):
- `manage-* skill`: match the service name against skill names/descriptions in `.claude/skills/*/SKILL.md` (e.g. `redis`→`manage-redis`).
- `MEMORY.md slug`: grep the project memory dir for slugs mentioning the service; propose top matches.
- `claudedocs handoff`: grep `claudedocs/` filenames for the service.

**Bloat discipline** (mirrors the `MEMORY.md` memory-hygiene rules):
- **Pointers, not copies** — `## Pointers` is paths/slugs + a one-clause why; domain detail stays in the skill/slug/handoff it points at.
- **NEVER persist live status** — pod counts, Ready/NotReady, canary phase, event tails, current image tag/replica values. Re-derived every run. This is the single most important anti-bloat rule.
- **Dated nuance bullets, newest-first, ≤2 lines each.**
- **Prune-on-resolve** — when a gotcha is fixed / incident closed / revert superseded, **remove** the bullet (its durable form lives in the slug/handoff it points to). The index is a live pointer sheet, not an append-only log.

## Then

- **No follow-on** → stop after the brief and wait for direction.
- **Follow-on is investigate/check/explain** → continue inline; the recon is now cached in context.
- **Follow-on is implement/build/fix** → follow the standing rule: dispatch a subagent, ensure test coverage where applicable, work on a feature branch ending in a PR (unless it's a one-liner/throwaway). The brief you just produced is the subagent's grounding.

Pair: `/handoff` (capture what you found — the write-back index complements it: the index is the terse pointer sheet, the handoff carries the detail; don't duplicate), `/find-session` (recover a past session on this service).
