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

Alongside the live recon, `/analyze-service` keeps a **markdown pointer/nuance sheet per service under `~/.claude`**, so each run front-loads "this bit us with X" instead of re-discovering every gotcha. It holds **pointers + nuance only** — never live state, never re-derived config values.

- **Location:** `~/.claude/analyze-service-index/<scope>/<slug>.md` — local, never inside a cluster repo or `devrc`. But **not "outside git": each `<scope>/` is its own remote-less git repo** (the store root is not one) — see 🔴 **Store safety** before running any git command there.
- **`<scope>`** defaults to the basename of the owning repo root the service resolved into: `datapacket-talos`, `homelab-talos`, else the current working repo's basename — derived from the locate step below, no separate assumption. A scope **need not be a repo**: a ritual owned by no repo, or a client spanning several, may use a plain scope word — a deliberate choice, so say so in the brief.
<!-- resolver-rules:begin — hashed by scripts/tests/test_subsystem_resolver.py. Editing ANYTHING between these markers, including ADDING a bullet, fails that test on purpose: the code implementing these rules is scripts/lib/subsystem_resolver.py and the two must move together. -->
- **`<slug>`** is normalized: lowercase, `_` → `-`, any other char outside `[a-z0-9.-]` → `-`, collapsed, trimmed of leading/trailing `-` — applied identically on read and write **and to `aliases:` before comparing**, so `External DNS` / `externaldns` / `external-dns` land on one file, and so do `image_ingestion` / `image-ingestion`. The `_` fold matters: the index links `_`-spelled `MEMORY.md` slugs (`bastion_config_stale_until_reload_2026_07_08`). **Keep the pre-fold spelling in `aliases:`** — it stays a valid ref and records how the thing is really written.
- **Kind qualification — only when disambiguation is needed.** One slug can name two KINDS of thing (`devrc/repo-cos` is both a code subsystem and the weekly ritual about it): qualify with `<slug>.<kind>.md` (`repo-cos.process.md`), kind ∈ `service` | `process` | `org` | `doc`. A trailing dot-segment is a kind **only if it is in that enum**, else it's part of the slug. 🔴 **Bare `<slug>.md` stays the default: no existing file is renamed, and a scope with no qualified filename behaves exactly as before.**
- **Resolution — ambiguity is an ERROR, never a shadow.** Two tiers; an alias can never outrank a filename:
  1. **Filename tier** — normalized ref vs `<slug>.md` *and* every `<slug>.<kind>.md` in the scope. A ref naming its own kind (`repo-cos.process`) matches only that qualified file.
  2. **Alias tier** — normalized `aliases:` across the scope, consulted **only if tier 1 returned zero hits**.
  One hit → use it. **>1 in a tier → never pick: stop, call the ref ambiguous and list the candidates** (`repo-cos.md` vs `repo-cos.process.md`) for the user to choose. Zero in both → no index yet.
- 🔴 **The EXECUTABLE authority for the two rules above is `scripts/lib/subsystem_resolver.py`** (`normalize_ref`, `split_kind`, `resolve_ref_tiered`). The prose here exists because *you* are the other implementation — but two implementations of one predicate drift, and here the drift is silent: a ref stops resolving and the miss reads as "no index yet". `scripts/tests/test_subsystem_resolver.py::TestCommandDocIsPinned` holds the sentences above as literal substrings alongside the behaviour each asserts, so **rewording either side without the other goes red naming the sentence that moved.** Change both in one commit.
- **Lazy** — a scope dir or service file may not exist yet; it appears only on a confirmed write-back (see "## Write-back (opt-in)").
<!-- resolver-rules:end — deliberately AFTER the last bullet of this list, not before it: an editor appending a rule appends at the END, and a boundary that stops short of the append point leaves the likeliest drift outside the hash. -->


🔴 **Store safety.** The content is **curated, irreplaceable, not re-derivable by re-running recon**, with no off-machine backup. Inside any scope dir:
- **Never `git stash`** — `refs/stash` is repo-**global** and concurrent sessions share this store, so your stash can be popped or dropped by another session. Set work aside with `cp <file> /tmp/…` instead.
- **Never `git reset --hard`, `git clean`, or `git checkout --`** — each destroys curated content that has no other copy.
- **Never add a remote, never push**, and never copy a line into `devrc` (PUBLIC) or any public repo, issue, PR, gist or commit message. devrc `60e6d9d` exists because this data class had to be scrubbed out of a public repo retroactively.
- Each scope's own `README.md` states the policy governing it — **read it before writing there**.

**File schema** (markdown, so prose is surfaced verbatim via Read and reads well in a diff):
<!-- entry-schema:begin — hashed, same contract as resolver-rules above: `SubsystemEntry.from_mapping` + `load_index` implement the identity fields; `subsystem_touch.census` implements `created_by`. -->
- **Front-matter — identity, sensitivity + provenance only:** `service` (canonical name, matches the filename's slug part), `aliases` (alternate spellings, incl. pre-normalization ones), `scope` (owning repo basename or the non-repo scope word — **replaces `repo:`**, which older files still carry and reads as `scope`), `sensitivity` (below), `namespace` (**optional** — keep it for load-bearing k8s infra, `multiple` for umbrella services; **omit rather than write `n/a`**), `kind` (optional; only meaningful on a kind-qualified filename). No machine/location fields.
- **`created_by:` — which writer created the entry**, one of `analyze-service` | `handoff`. Stamped on a NEW file only, never edited afterwards, and **absent means the entry predates the stamp — never fold an absent value into either writer.** It exists to make one question answerable by counting instead of by recollection: *do entries accrue outside infra recon?* Read it with `scripts/lib/subsystem_touch.py --census`; the threshold that would reopen the store design lives in `claudedocs/decision-subsystem-store-rejected-2026-08-11.md`, not here.
<!-- entry-schema:end -->
- 🔴 **`sensitivity:` — fail-safe: absent means sensitive.** One of `client-confidential` | `personal` | `public`; **absent or unrecognized ⇒ `client-confidential`, never public**, and `public` is a deliberate operator claim a recon run may never infer. Live, not hypothetical — entries carry client-identifying infrastructure detail, down to named individuals. Handling rules are in 🔴 **Store safety** above; this spec only **marks**.
- **`## What it is`** — one-line description. For an umbrella/multi-instance service (redis, monitoring, meilisearch) enumerate the instances in prose — it's an index OF instances, not one location.
- **`## Pointers`** — each entry is a path/slug + one-clause why, **never a copy** of the pointed-to content:
  - `manage-* skill:` the matching skill (e.g. `manage-redis`) — invoke it for ops.
  - `MEMORY.md slug(s):` slug filename(s) in the project memory dir (datapacket-talos: `/home/zach/.claude/projects/-home-zach-workspace-civit-datapacket-talos/memory/`).
  - `claudedocs handoff(s):` handoff doc path(s).
- **`## Nuance / work-history`** — dated bullets, newest-first, ≤2 lines each: a gotcha, a lying/misleading status condition, a revert or bump that explains why someone was looking, an incident tie-in. Prune-on-resolve.

**Read at recon START** — front-load the curated recall before re-discovering anything; mechanics in step 1 below. A miss proceeds with today's behavior and may offer to create the file on write-back.

## Recon steps

1. **Locate (deterministic, parallel).** Glob/grep the service name across the repo root(s) to find its directory + manifests: `kustomization.yaml`, `HelmRelease`, `Deployment`/`StatefulSet`/`DaemonSet`, `ConfigMap`, `*values*.yaml`. Identify the **namespace** and the owning **kustomization/Flux Kustomization**. Prefer the Grep/Glob tools; for a broad sweep dispatch an **Explore** subagent returning file paths + key config excerpts (not whole-file dumps).

   Once the owning `<scope>` is known, do the index read described above — resolve the ref, surface `## Pointers` + `## Nuance / work-history` (labelled `from index`) **before** deriving any gotchas below. Locate/config/live still run live every time; an ambiguous ref stops for a choice.

2. **Config.** Read the manifests found. Pull out the load-bearing knobs: image/chart version, replicas/HPA, resources, key env/ConfigMap values, mounted secrets (names only — never print secret contents), exposed routes/services, dependsOn.

3. **Recent changes.** In the owning repo: `git log --oneline -10 -- <service-path>` to surface what last moved (a recent revert/bump is usually why you're looking).

4. **Live state (only if a cluster is reachable — never fabricate).** Pick the context that matches the owning repo (datapacket-talos → `admin@civitai-talos`; homelab-talos → its documented context) and **state which context you used**. Then, read-only:
   - `kubectl -n <ns> get pods,deploy,sts,svc` (+ `--context`)
   - `kubectl -n <ns> get events --sort-by=.lastTimestamp | tail`
   - `flux get helmrelease -n <ns> <name>` / `flux get kustomization` if Flux-managed
   - restarts / not-Ready / recent crashloops worth flagging
   If no context matches, the cluster is unreachable, or access is denied: **say so plainly and skip** — present the static recon and note live state is unverified.

## Output — recon brief

Header line: which index file resolved + hit/miss — e.g. `index: datapacket-talos/redis.md — pointers loaded`, `index: none (first run)`, or `index: AMBIGUOUS — repo-cos.md | repo-cos.process.md (pick one)`.

- **Service** + one-line "what it is".
- **Pointers / nuance** (`from index`): the `## Pointers` + `## Nuance / work-history` surfaced at recon start, if any. Omit if the index missed.
- **Lives at**: repo + path(s) as `file:line` (clickable), namespace, owning kustomization (Phase 1 never caches location).
- **Config**: the load-bearing knobs (version, scale, resources, key values, routes, deps).
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
5. **Write only on explicit confirm.** On confirm, re-read the file (so a concurrent append isn't clobbered), re-apply the change to current bytes, then plain Write to `~/.claude/analyze-service-index/<scope>/<slug>.md` (creating the dir/file if first-ever; use `<slug>.<kind>.md` **only** when a same-slug entry of another kind already exists, and say why in the diff). On a **first-ever** file, stamp `created_by: analyze-service` in the front matter (schema above) — on an append, leave whatever is there. On decline, discard. The write is local and final — **nothing leaves the machine**: no remote, no push. But it is **not** outside git; committing the scope repo is the store's own concern (an out-of-band autocommit), never this command's, so **write the file and run no git command** (🔴 **Store safety**).

**Notable — append-worthy** (matches the "Gotchas" spirit + the `MEMORY.md` "durable lesson, not status" bar):
- A **gotcha**: non-obvious behavior, a lying/misleading status condition, an ephemeral-vs-durable trap, a wrong-looking-but-correct error string.
- A **revert or bump** found in `git log` that explains *why* someone was looking.
- An **incident tie-in**: the recon connected the service to a firing alert / a known `MEMORY.md` slug / a handoff — record the pointer.
- A **new pointer** discovered (a `manage-*` skill or slug the index didn't yet reference).

**NOT notable — never append:** routine healthy state, config values, or anything a pointer target already captures. These are the "Bloat discipline" rules below, applied at the append decision.

**Auto-discovered pointers** (propose in the diff, still confirm-gated — a bad match must be rejectable). Curate the starting set: **propose at most ~5-7 candidates, never a raw match list** — a dump is unusable even though the human confirms each:
- `manage-* skill`: match the service name against skill names/descriptions in `.claude/skills/*/SKILL.md` (e.g. `redis`→`manage-redis`).
- `MEMORY.md slug`: **filename-match first** — propose slugs whose *filename* contains the normalized service token (or an `aliases` entry), e.g. `*redis*.md`; those are the slugs actually ABOUT the service. **Only if that yields <3**, fall back to content-grep of the memory dir, but **rank by mention density and propose only the top few**, never the raw `grep -il` list (it is far too broad: `redis` returns ~90 slugs vs ~15 actually redis-centric).
- `claudedocs handoff`: same — **prefer filenames containing the normalized token**; density-rank a content-grep fallback only if that is too thin, and cap the count.

**Bloat discipline** (mirrors the `MEMORY.md` memory-hygiene rules):
- **Pointers, not copies** (schema above) — domain detail stays in the skill/slug/handoff it points at.
- **NEVER persist live status** — pod counts, Ready/NotReady, canary phase, event tails, current image tag/replica values. Re-derived every run — the single most important anti-bloat rule.
  - **No live probe ⇒ persist the DERIVATION, not the reading.** For a process/ritual entry ("is this still being followed?") there is no `kubectl` two seconds away — so record *how to take the reading and what a stale one looks like*: "liveness = mtime of the exclusions file vs. the timer's last fire; stale ⇒ mtime predates the last two fires." The method is durable; the answer it gave ("last followed 2026-08-01") is live status exactly like a pod count, and stays forbidden.
- **Dated nuance bullets, newest-first, ≤2 lines each.**
- **Prune-on-resolve** — when a gotcha is fixed / incident closed / revert superseded, **remove** the bullet (its durable form lives in the slug/handoff it points to). The index is a live pointer sheet, not an append-only log.

## Then

- **No follow-on** → stop after the brief and wait for direction.
- **Follow-on is investigate/check/explain** → continue inline; the recon is now cached in context.
- **Follow-on is implement/build/fix** → follow the standing rule: dispatch a subagent, ensure test coverage where applicable, work on a feature branch ending in a PR (unless it's a one-liner/throwaway). The brief you just produced is the subagent's grounding.

Pair: `/handoff` (capture what you found — the write-back index complements it: the index is the terse pointer sheet, the handoff carries the detail; don't duplicate), `/find-session` (recover a past session on this service).
