# clawgate — CI, observability, usage telemetry

Read when: metrics/logs are missing, you're adding an event or a dashboard panel, or you're about
to debug a red CI check.

## CI — 🔑 IT MOVED (2026-07-30)
**GitHub Actions is BILLING-BLOCKED repo-wide**: every workflow run fails in seconds with
`steps: 0` and the annotation *"recent account payments have failed or your spending limit needs to
be increased"*.

🔴 **Red Actions checks on `homelab-infra` are NOISE — don't debug them, don't treat them as a
gate.** `.github/workflows/clawgate-ci.yml` remains only as `name: clawgate e2e (manual)` /
`on: workflow_dispatch`.

The real gate is the **Tekton `clawgate-ci` pipeline** on the homelab cluster
(`clusters/homelab/apps/tekton-pipelines/triggers/clawgate-ci-*.yaml`) — one Task with a
Postgres-sidecar `go` step, an `extension` `npm run coverage` step, and a `hook` bats step;
push-only, any branch, path-filtered to `containers/clawgate/`.

Its open sharp edges (branch-creation over-match, unpinned 6Gi PVC, no concurrency control,
`error`-vs-`fail` only on the CSS path) and the 🔴 **"merge → `flux reconcile kustomization tekton-triggers` → confirm →
THEN push"** rule live in the **`tekton` skill** — read it before
touching CI.

**The deploy is still manual.**

## Observability (Wave 2)
Metrics at **`/metrics`** (`internal/metrics`: `build_info`, http, panics,
`push_delivery{result}`, `agent_provision`, checkpoints, `permission_requests`).

**Workbench has no Prometheus** → **Grafana Alloy**
(`clusters/workbench/apps/alloy/configmap.yaml`, `prometheus.scrape "clawgate"`) scrapes the named
`http` port and **remote-writes to the homelab Prometheus** (query at `192.168.50.94:30909`, e.g.
`clawgate_build_info`).

**Alert rules** on homelab: `clusters/homelab/apps/clawgate-monitoring/` — ClawgateDown /
ScrapeMissing / Panics / PushDeliveryFailing.

⚠️ **Alloy has NO auto-reloader**: after editing its configmap,
`kubectl --kubeconfig workbench-kubeconfig -n monitoring rollout restart deploy/alloy`.
**If clawgate metrics vanish from homelab Prometheus, that's the first thing to check.**

## Usage telemetry (0.7.31) — "how Zach actually uses it"
Wired into the EXISTING Grafana stack, **not** a bespoke event table. Two sources of truth;
**Grafana is the surface** — dashboard `clawgate-usage` at `grafana.homelab.lan` (JSON in
`clusters/homelab/flux-system/charts/prom-stack/dashboards/clawgate-usage.json`).

- **Frontend RUM via the Grafana Faro Web SDK** — vendored
  `web/static/vendor/faro-web-sdk.iife.js`, gated on `CLAWGATE_FARO_URL` (empty = OFF, the
  e2e/local default). The browser POSTs to **`https://faro.promptver.com/collect`** (public; key
  `CLAWGATE_FARO_API_KEY` sent as `x-api-key`, **client-public by design** → plain env, not SOPS)
  → homelab Alloy `faro.receiver` → **Loki** (events/logs) + **Tempo** (frontend spans, behind
  `CLAWGATE_FARO_TRACING=1`).
  - 🔴 **CSP gotcha:** `connect-src 'self'` silently blocks the cross-origin POST.
    `api.SetTelemetryConnectSrc(CLAWGATE_FARO_URL)` appends the Faro origin — without it you get
    **zero telemetry and no error**.
  - A `window.cgTrack(name, attrs)` helper (`internal/ui/faro.go`) emits custom events.
- **Custom events** (→ Loki, query `{app_name="clawgate"} | event_name="…"`): `tab.view`,
  `permission.action`, `suggestion.{viewed,copied,dismissed,autosuggest_toggled}`, `chat.sent`,
  `model.switch`, `agent.dispatch`, `runbook.run`.
- **New Prometheus metrics** (server-truth): `clawgate_permission_decisions_total{outcome}`,
  `clawgate_permission_decision_latency_seconds`, `clawgate_suggestion_events_total{action}`,
  `clawgate_runbook_runs_total`.

### Query usage (cross-cluster NodePorts)
```bash
# Loki
curl -sG 'http://192.168.50.94:30310/loki/api/v1/query' \
  --data-urlencode 'query=sum by (event_name)(count_over_time({app_name="clawgate"}[24h]))'
# Prometheus — use increase(metric[24h]); counters reset on each deploy/restart
curl -sG 'http://192.168.50.94:30909/api/v1/query' --data-urlencode 'query=clawgate_build_info'
```

### Early read (2026-06-24) and the decision it drove
clawgate is a high-volume auto-approve firehose (~4k prompts/day, ~97% auto-approved); UI browsing
≈ none; **Suggestions = 0 engagement** (trending toward removal like decision-labeling). Web vitals
are excellent.

**Decision (close-the-loop):** do **not** invest more in standalone 💡 Suggestions polish
(full-transcript scroll-back / Web Push). The plan is to **fold it into the Tasks adjudication
queue as a generative source** (candidate #3, not yet built) rather than iterate it standalone.
Memory `clawgate-loop-validation`.
