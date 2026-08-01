---
name: auditloop
description: Operate auditloop — the generic UX-audit crawler web app (auditloop.zacx.dev) spun out of the ux-audit-loops harness. A Go+chromedp PWA that crawls a site (multi-viewport screenshots + axe a11y + console/network), captures DETERMINISTIC signals (perf/web-vitals, layout smells, broken links), diffs runs over time + trends findings, drafts multi-model vision-LLM UX notes, audits logged-in sites via encrypted login recipes, ingests curated funnels pushed by external harnesses (naida/vetr/itself), and exposes a machine read API (per-user Bearer keys) so CI/naida pull findings back. Deploy (./deploy.sh + Flux image-automation), targets/runs, plugin tokens, login recipes, the read API, LLM cost tracking + Grafana dashboard/alert, the self-audit harness, secrets/kubeconfigs, and the live-bug playbook. Use when the user mentions auditloop, auditloop.zacx.dev, deploying/operating it, its plugin push API, its read API, its cost dashboard, or the auditloop self-harness.
---

# auditloop operations

A **generic UX-audit crawler web app** — a Go single-binary (web + worker roles) chromedp
crawler, gomponents+htmx+Tailwind **PWA**, spun out of the naida/vetr `ux-audit-loops`
harness to be a hosted hub. Cross-session state: memory `auditloop-project` (read first).
Sibling harnesses feed it — see the `ux-audit-loops` skill.

| | |
|---|---|
| Repo / source | `ZacxDev/auditloop` (default `main`) · `~/workspace/auditloop` (Go 1.26 module `auditloop`) |
| Live | https://auditloop.zacx.dev · GoTrue https://auditloop-auth.zacx.dev |
| Clusters | **app** on homelab-talos **workbench** (ns `auditloop`); **GoTrue** on **homelab** (ns `supabase-auditloop`); public routing via **production+homelab** nebula gateways |
| Kubeconfigs | `~/workspace/homelab-infra/{workbench,homelab,production}-kubeconfig` — **NOT the repo root** |
| Image | `harbor.homelab.lan/library/auditloop:main-<ts>` (Flux image-automation picks newest) · current live: check the pod |
| GitOps | app manifests in `homelab-infra` (checked out at `~/workspace/homelab-talos`) `clusters/workbench/apps/auditloop/`; GoTrue `clusters/homelab/apps/supabase-auditloop/`; monitoring `clusters/homelab/apps/auditloop-monitoring/` |
| Auth | Supabase (self-hosted GoTrue), **invite-only**; smoke user `smoke@auditloop.zacx.dev` |
| Test | `go test ./...` (incl. hermetic chromium e2e) · `make ux-audit` (the self-audit harness) |

**Reference files** (`~/.claude/skills/auditloop/reference/`, source
`~/workspace/devrc/claude/skills/auditloop/reference/`) — read on demand:
- `persona-evaluator.md` — Phases 1–4 detail (personas, tables, migrations 0039–0063, routes, metrics, DOM/a11y grounding, token tiers). Read when working ON the evaluator/driver.
- `internal-naida-drive.md` — driving a private in-cluster target via `AUDITLOOP_INTERNAL_ALLOW_HOSTS`. Read when driving a ClusterIP app or auditing that allowlist.
- `ui-and-meta-run.md` — design-system tokens/conventions + the meta-run dogfood recipe. Read when writing auditloop UI.

## What it does (P0–P5, all live)
- **P0/P1** — generic same-origin BFS crawl (chromedp), multi-viewport (desktop 1440 + mobile 390) screenshots + **axe a11y** + **origin-classified console/network** → MinIO. **SSRF-guarded** (`internal/crawler/ssrf.go` + runtime `intercept.go` guards redirect hops). Crawl only verified target domains. **Per-run favicon capture** (#33/#34, `internal/crawler/favicon.go`): best-effort/non-fatal server-side fetch of the attacker-influenced favicon URL, SSRF-safe — `GuardConfig.CheckURL` before connect + **IP-pinning dialer** (closes DNS-rebind) + no-redirect (3xx dropped) + **raster-only** (`DetectContentType`, SVG/HTML rejected) + ≤512 KiB; stored run-scoped `{target}/{run}/favicon.<ext>` (migration 0059 `runs.favicon_key`).
- **P2** — regression diffing vs the previous done run: pure-Go pixel diff (`internal/diff`), new/removed pages, new axe rules, console/network deltas. **Full-page height shifts → "layout changed", NOT a false ~100% regression** (gated on `!SizeChanged`); >24MP captures skip the viz alloc.
- **P3** — opt-in multi-model **vision-LLM draft UX notes** (`internal/llm` OpenRouter + `internal/notes`): each selected model × each page, both viewports + grounded (axe/console/diff) → editable notes side-by-side. Server-side, key-gated, `page_notes` table. The prompt is **purely-visual critique** — the LLM no longer re-narrates axe/console/network/perf (all deterministic now); `Grounding` dropped the a11y/console fields.
- **P4** — **login recipes** (`internal/recipe` + `internal/crypto`): authed crawl behind a same-domain login; creds **AES-256-GCM at rest** (`AUDITLOOP_ENCRYPTION_KEY`), write-only UI, redacted; test-login button.
- **P5** — **plugin push ingestion** (`internal/plugin` + `cmd/auditloop-push`): push-only targets w/ hashed rotatable token; `POST /api/plugins/runs` (Bearer, multipart) → ingested run gets the P2 diff. The push carries optional raw `perf`/`layout` blocks + a run-level **`environment` (lab|staging|prod)**; auditloop computes the perf/layout FINDINGS server-side via `internal/signals` — and **`environment:"lab"` SUPPRESSES the perf findings** (localhost numbers aren't field-representative; raw columns kept, amber run-view banner). The naida/vetr harnesses push `lab`.

### Deterministic signals (shipped, replaced LLM re-narration)
Every page+viewport; native crawl AND pushed runs share `internal/signals` (**one source of
truth for thresholds**).
- **Perf/web-vitals**: LCP/CLS/**TBT (a headless LAB PROXY — no field input, labelled honestly)** via injected buffered PerformanceObserver (`internal/crawler/perf-capture.js`) + page-weight/req-count via CDP; `type=perf` findings on threshold breach. Cols `pages.lcp_ms/cls/tbt_ms/weight_bytes/req_count`.
- **Layout smells** (`internal/crawler/layout-smells.js`): horizontal-overflow, tap-target <44px, text <12px, missing viewport-meta, `<img>` w/o dims → `type=layout` (tap-target/overflow mobile-gated).
- **Broken links**: status-aware severity (`sevForNetwork`: 5xx/first-party-4xx serious, third-party minor).
- **Cold-load crawl**: `network.SetCacheDisabled(true)` so EVERY page+viewport is a cold load (the 2nd viewport was warm-cache → undercounted); page-weight uses `dataReceived` fallback (Chromium reports `encodedDataLength=0` for the main doc).

### Trend view
A per-target findings-count-over-time inline-SVG sparkline above the run list
(`internal/db/trend.go` `TargetFindingTrend`, owner-scoped) — catches slow creep the pairwise
P2 diff can't.

### Read API — machine consumers (per-user, read-only)
Mint a key in the dashboard **"API access"** card (crypto/rand→base64url, sha256-stored,
rotatable, **shown once**); consumer sets **`AUDITLOOP_API_TOKEN`** + `Authorization: Bearer`.
Owner-scoped routes (SQL-scoped, foreign→404):
`GET /api/audit/targets/{id-or-name}/runs` · `…/runs/latest` (→report.json bytes) ·
`/api/audit/runs/{id}` (→report.json) · `/api/audit/artifacts/{key}` (bytes, per-object
owner-checked). Target resolves by **name OR UUID** (symmetric with the name-keyed push).
This is how CI/Tekton + the naida `fetch-findings` helper pull findings back.
```bash
curl -H "Authorization: Bearer $AUDITLOOP_API_TOKEN" https://auditloop.zacx.dev/api/audit/targets/<spec>/runs/latest
```

**LLM cost tracking** — per-run + per-cell USD/tokens + Prometheus metrics (see Monitoring).

## Persona-walkthrough evaluator (Phases 1–4, live — PRs #20–#25, #35–#38)
A SECOND opt-in LLM subsystem *parallel* to P3 vision-notes (they coexist), gated on the SAME
`OPENROUTER_API_KEY` (no new required secret; buttons hidden + routes 503/403 without it).
Runs on `AUDITLOOP_LLM_MODELS[0]` (**ONE model; PERSONA is the axis, not model**). Phase 1
evaluates a completed run as a **task + persona walkthrough** over four code-defined personas;
Phase 2 infers a per-target audit config the owner confirms; Phase 3 DRIVES the app toward a
goal (success **OBSERVED via a success-assertion, NEVER LLM-judged**) and runs the personas
over the driven trace; Phase 4 diffs a walkthrough vs the previous terminal one for a CI
`--fail-on-regression` gate. Migrations through **0063**.
**Full detail (tables, routes, metrics, DOM/a11y grounding, LLM token tiers) →
`reference/persona-evaluator.md`.**

### 🔴 Driver safety model (foreground it)
- **`driving_enabled` default-OFF per-target opt-in** (migration 0054) — enforced at **BOTH**
  the route (403) AND the generator (defense in depth). Driving is off until the owner opts in.
- **Dry-run submit-guard is the DEFAULT** — the Fetch interceptor **aborts (network-layer,
  deterministic) every non-GET/HEAD request** (POST/PUT/PATCH/DELETE). **SCOPED GUARANTEE:**
  covers every TOP-FRAME HTTP(S) request incl. XHR/`fetch`/`sendBeacon`. **Residual (NOT
  covered): WebSocket frames, cross-origin (OOPIF) iframe requests, service-worker requests**
  → for real-submit / prod driving **point at STAGING with a DISPOSABLE account**; do NOT
  treat dry-run as an absolute "never writes".
- **Real submits require the loud DEFAULT-OFF `allow_real_submit` flag** (migration 0058;
  `DryRun = !allow_real_submit`) — two independent opt-ins (drive-at-all vs. mutate-live-data).
- The **login phase is EXEMPT from the mutation submit-guard** (its credential POST must go
  through); the SSRF/same-origin IP-guard stays active the ENTIRE time (every nav + redirect).
  Closed action set, **NO eval**; action budget + per-action/overall timeouts; guard-blocked
  navs never screenshotted.

## Deploy — `./deploy.sh` (Flux-native, no kubectl)
```bash
cd ~/workspace/auditloop
./deploy.sh          # go vet gate → docker build+push harbor main-<ts> (+:latest) → prints watch cmds
# SKIP_VET=1 ./deploy.sh   # skip the vet gate (faster; use after you've tested)
```
Flux **image-automation** rolls it in ~1–2 min: the homelab ImageRepository/ImagePolicy
`auditloop` (`^main-(?P<ts>[0-9]+)$`) picks the newest tag, and **`flux-system-workbench`
ImageUpdateAutomation** bumps the `images:` setter on
`clusters/workbench/flux-system/kustomizations/system/auditloop.yaml` + commits to trunk →
workbench Flux rolls the pod. Wiring lives in `clusters/homelab/flux-system/image-automation/`.
- **⚠ `flux-system-workbench` used a READ-ONLY git key** (never pushed in 202d) — fixed to
  `flux-system-write`. If auto-rollout stalls, check
  `kubectl -n flux-system logs deploy/image-automation-controller | grep read.only` on homelab.
- **Force a roll** (impatient): `flux -n flux-system reconcile image repository auditloop` +
  `... image update flux-system-workbench` (homelab kubeconfig), then reconcile source +
  kustomization `auditloop` (workbench kubeconfig). Watch
  `-n auditloop get pod -l app=auditloop -o jsonpath='{...spec.containers[0].image}'` until it
  == the new tag + ready.
- Harness-only PRs (self-audit, docs) **don't need a deploy** (not in the Go binary).

## Secrets (SOPS, in the `auditloop-secrets` k8s secret + supabase-auditloop secrets)
Edit via a worktree off `origin/trunk` — the main `~/workspace/homelab-talos` checkout is
**~100 commits behind; never edit there**.
```bash
SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
  sops --set '["stringData"]["KEY"] "val"' clusters/workbench/apps/auditloop/secrets.enc.yaml
# then reconcile the `auditloop` ks (workbench). ⚠ a secret change alone does NOT restart the
# pod — `kubectl rollout restart deploy/auditloop` (workbench) to pick up an envFrom change.
```
- App secret: `OPENROUTER_API_KEY` (dedicated key, **$50 cap** — 🔴 chat-pasted, ROTATE),
  `AUDITLOOP_ENCRYPTION_KEY` (32-byte hex, P4 creds), `DATABASE_URL`, `S3_*`
  (endpoint = **bare `minio.homelab.lan`**, NOT a URL — minio-go rejects a scheme),
  `SUPABASE_*`, `AUDITLOOP_LLM_MODELS` default.
- GoTrue jwt-secret ↔ app `SUPABASE_JWT_SECRET` are **load-bearing-equal**; anon key signed by it.

## Auth / access
Supabase invite-only (signup disabled). Smoke login: **`smoke@auditloop.zacx.dev`** / password
in the session scratchpad `auditloop-secrets.env` (🔴 chat-exposed → rotate). Create invited
users via the GoTrue admin API with a `service_role` JWT signed by the jwt-secret. Bearer
header on htmx + `auditloop_at` cookie for full-page nav (naida pattern). `/metrics` is
**public** (Prometheus scrape).

## Monitoring — cost dashboard + alerts (homelab)
Data path: **workbench Alloy scrapes the auditloop pod**
(`clusters/workbench/apps/alloy/configmap.yaml`, a verbatim clawgate-clone block:
`app=auditloop`, `http:8112`, `job=auditloop`) → **remote-writes to homelab Prometheus** →
Grafana + PrometheusRule.
- **⚠ Editing the Alloy config is BLAST-RADIUS** (shared scraper for THC/promptver). Validate
  with `nix-shell -p grafana-alloy --run "alloy fmt <extracted-river>"` BEFORE, then
  `kubectl -n monitoring rollout restart deploy/alloy` (workbench) — the rollout succeeding ==
  config loaded OK; confirm the `auditloop` components evaluate with no errors in the logs. The
  data key is `config.alloy` (strip the nix devshell banner when extracting).
- Grafana **"auditloop"** dashboard (uid `auditloop`) at **grafana.homelab.lan** — 14 cost
  panels (LLM cost total / per-model / $/hr / tokens / cost-per-pass) via a
  `grafana_dashboard:"1"` ConfigMap; Grafana sidecar auto-imports.
- PrometheusRule `auditloop-alerts` (label `release: kube-prometheus-stack`):
  **AuditloopLLMSpendHigh** (`increase(auditloop_notes_cost_usd_total[1h]) > 2`, tunable) +
  **AuditloopScrapeDown** (`up{job="auditloop"}==0`).
- Metrics: `auditloop_notes_cost_usd_total{model}`,
  `auditloop_notes_{prompt,completion}_tokens_total{model}`,
  `auditloop_notes_generated_total{model,status}`, `auditloop_runs_total{status}`,
  `auditloop_plugin_pushes_total{status}`, `auditloop_login_attempts_total{status}`,
  `auditloop_visual_regressions_total`. Persona subsystem:
  `auditloop_eval_{generated_total{persona,status},duration_seconds,cost_usd_total{persona},prompt/completion_tokens_total{persona}}`
  + `auditloop_walkthrough_{runs_total{outcome},steps_total{outcome},duration_seconds,cost_usd_total}`
  + `auditloop_walkthrough_regressions_total`.
- Query prod Prometheus (it's **distroless — no `sh`**):
  `kubectl -n monitoring exec prometheus-kube-prometheus-stack-prometheus-0 -c prometheus -- promtool query instant http://localhost:9090 '<query>'`.

## The producers (all merged)
naida (4 specs), vetr (`vetr-funnel`, PR #108), auditloop **self-audit** (PR #5,
`make ux-audit`), **remix** (`remix-funnel`, homelab-infra #119 — remix's Go/chromedp e2e
harness in `containers/remix/e2e/uxaudit/`, `make ux-audit`), and **civitai-manager**
(`civitai-manager-funnel`) — a Go/chromedp ux-audit harness in `ZacxDev/civitai-manager`
`e2e/uxaudit/` (**a SEPARATE module so chromedp stays out of the shipped binary**),
`make ux-audit`, pushes `environment=lab`.

Each pushes via P5 — enable with `AUDITLOOP_PUSH_URL=https://auditloop.zacx.dev` +
`AUDITLOOP_PUSH_TOKENS='{"<spec>":"<token>"}'` (or `AUDITLOOP_PUSH_TOKEN` for the
single-target remix walk); create a plugin target per spec in the UI → one-time token.
See the `ux-audit-loops` skill.

## Drive an INTERNAL, in-cluster DEV_MODE naida — live 2026-07-18
The hosted auditloop can DRIVE (Phase-3 driver + persona eval) a **private ClusterIP** app —
e.g. an in-cluster DEV_MODE naida at `naida-dev.naida-dev.svc.cluster.local:8080` — viewable
on auditloop.zacx.dev **without exposing it publicly**. The prod SSRF guard blocks private IPs;
the exact-hostname env **`AUDITLOOP_INTERNAL_ALLOW_HOSTS`** (in the auditloop SOPS secret) makes
the private-IP refusal **soft for an exact-allowlisted host only**, while metadata/link-local/
multicast/unspecified stay **HARD-blocked**. **Reversible** — clear the env → all private
blocked again. **Recipe, the three PRs (auditloop #26, homelab-infra #138/#139) and the
live-verified run → `reference/internal-naida-drive.md`.**

## 🔴 a11y gate only works for pushed runs since #18 (2026-07-17)
The P2 a11y-rule delta (`new_a11y_rules`, what the CI `--fail-on-regression` gate keys on)
reads a **top-level `id`** from each stored a11y finding's detail. Pre-#18, `MapPage` wrapped
pushed finding details as `{"detail":..}` (no `id`) → `new_a11y_rules` was **always empty for
every plugin push** (a silent no-op gate). #18 (`internal/plugin/map.go` `a11yDetail`)
preserves a structured pushed a11y detail's `id` and derives one from legacy
`"rule — help"` strings.

So a producer should push a11y findings with a **structured detail carrying `id`** (the raw
axe violation object — what remix does). **Transition:** a target's FIRST diff after #18
deployed compares vs a pre-fix baseline (no ids) → all rules flag "new" once (transient RED,
self-heals next run). Verify a pushed-run gate with: clean baseline → exit 0; a genuinely new
axe rule → `new_a11y_rules` non-empty → exit 2.

## 🔴 Meta-audit finding — the persona evaluator is screenshot-only by default (durable)
The persona evaluator reasons over **screenshots only** unless DOM-grounded, so it is BLIND to
`sr-only` labels, ARIA, `<a>`/`<button>` semantics, and JS focus/keyboard behaviour → it
**invents a11y false positives AND can't confirm its own a11y fixes**. The DOM/a11y grounding
(#35 crawl path + #37 driven path + #36 structural label gate) fixes the **crawl + driven**
paths (the `dropContradicted` gate refutes contradicted findings deterministically).
**⚠ plugin-PUSH runs remain screenshot-only (NOT DOM-grounded)** — treat their a11y/keyboard
findings with skepticism. Refs:
`claudedocs/evaluator-meta-audit-naida-outreach-2026-07-24.md`,
`claudedocs/scoping-dom-a11y-evaluator-grounding-2026-07-24.md`.

## Live-bug playbook
- **PWA service worker MUST NOT intercept navigations** — page routes 302
  (auth/trailing-slash); a cached redirected `/dashboard` shell → *"a redirected response was
  used for a request whose redirect mode is not follow"*. SW now skips `mode==='navigate'` +
  caches only `/static/`. Users with the old SW must **unregister** (DevTools → Application →
  SW) once.
- **Artifacts stream through the app, NOT presigned MinIO** — presigned `minio.homelab.lan`
  URLs are internal-only (browser: `ERR_CERT_AUTHORITY_INVALID`). `handleArtifact` streams from
  either backend over auditloop's public origin; run/diff/login-test views build
  `/artifacts/{key}` (helper `artifactURL`). **Never reintroduce a presign redirect for
  browser-facing artifacts.**
- **`UID` is a reserved shell var** (zsh) — use `USR`/`uid2` in scripts.
- Kubeconfigs are under `~/workspace/homelab-infra/`, **NOT the repo root**.

## Security posture + deferred hardening (all low, from adversarial reviews)
Crypto (P4) + credential non-leakage: clean. Upload-XSS + unauth-DoS (P5): clean. The
**artifact route is now per-object owner-checked** (both the browser `/artifacts/` proxy and
the read-API `/api/audit/artifacts/`), and P4 login-test screenshots were re-keyed
`{target_id}/login-tests/{id}.png` (bind authz to the owning target, not a collidable
name-slug). Deferred (all low): no per-IP throttle on bad plugin/read tokens (per-token rate
limits exist; **both limiters are in-memory single-replica**); `verified_domains` is
user-asserted (real DNS-TXT verification is future — the runtime IP guard closes the practical
SSRF).

## UI / design system (redesign 2026-07-20, PRs #27–#34)
A meta-run (auditloop audited its OWN UI → "information overload") drove an 8-PR redesign.
**Tailwind bumped 4.3.2 → 4.3.3** (latest; no v5 exists). `static/input.css` defines `@theme`
semantic tokens + an `@layer components` set + `motion-safe:`-gated keyframes.
**Durable convention: new UI uses these tokens/component classes, NOT raw
`blue/red/emerald/amber` utilities; all motion is `motion-safe:`-gated; NEVER animate an htmx
self-poll root** (re-fires every 3s = a blink); progressive disclosure = native `<details>`
accordions. Full token/component list, the redesigned views, and the meta-run dogfood recipe
(~$0.28/pass, incl. extracting the real `OPENROUTER_API_KEY` from the k8s secret) →
`reference/ui-and-meta-run.md`. Audit doc:
`claudedocs/design-system-audit-2026-07-19.md`.

## Conventions carried from naida (see naida CLAUDE.md)
gomponents aliases, htmx (`hx-boost` body, wire on `htmx:load`, `hx-boost=false` on JS forms),
cache-busted app.js, dual-dialect sqlite/postgres query layer, atomic claim + startup sweep,
adversarial-review-before-merge on every substantive PR. **Every file-modifying subagent gets a
worktree.**
