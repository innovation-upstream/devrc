# auditloop persona-walkthrough evaluator — Phases 1–4

PRs #20–#25, #35–#38. All live. Read this when working ON the evaluator/driver
(phases, tables, migrations, routes, metrics). The **driver safety model** lives in
SKILL.md — it is a destructive-action warning and is deliberately NOT moved here.

A SECOND opt-in LLM subsystem *parallel* to P3 vision-notes (they coexist), gated on the
SAME `OPENROUTER_API_KEY` (no new required secret; buttons hidden + routes 503/403 without
it). Runs on `AUDITLOOP_LLM_MODELS[0]` (**ONE model; PERSONA is the axis, not model**).

Migrations through **0063**: 0059 = `runs.favicon_key` (#33); 0060/0061 = DOM/a11y digest
(#35/#37); 0062/0063 = walkthrough regression (#38).

## Phase 1 — persona walkthrough
`internal/eval`, table `page_evaluations`, migrations **0039–0049**. Evaluates a completed
run as a **task + persona walkthrough**.

Four **code-defined** personas (`eval.Personas`): `first-time-nontechnical` ·
`returning-power-user` · `skeptical-evaluator` · `accessibility-constrained`.

Per (page × persona) STRUCTURED findings
`{comprehension, blockers, frictions, top_fix{selector,change,impact}}` + a **verification
pass** (drops/flags unsubstantiated findings) + a run-level **synthesis "story"**.

- Routes: `POST /api/runs/{id}/evaluate` (body `personas[]`, 503-gated, run must be `done`),
  `GET /runs/{id}/eval-status` (htmx poll).
- Read-API: `GET /api/audit/runs/{id}/evaluation` (owner-scoped).
- Metrics: `auditloop_eval_{generated_total{persona,status},duration_seconds,cost_usd_total{persona},prompt/completion_tokens_total{persona}}`
  (synthesis logs under `_synthesis`).

## Phase 2 — goal inference + per-target config
Table `target_audit_config`, migration **0050**; `internal/eval/infer.go`.

`InferConfig` = ONE synchronous LLM call over the latest done run's landing screenshot +
URL digest → a DRAFT config (product_summary, primary_job, primary_cta, applicable
personas, inferred/confirmed flags) the owner CONFIRMS/edits (hybrid infer-then-confirm).

- Routes: `POST /api/targets/{id}/audit-config/infer`, `POST /api/targets/{id}/audit-config`.
- Read-API: `GET /api/audit/targets/{id}/audit-config`.
- The evaluate trigger **pre-fills** job + personas from the confirmed config.

## Phase 3 — goal-directed DRIVER + personas over the driven trace
`internal/action` (closed no-eval action set), `crawler.Drive` (LLM-planner loop),
`internal/walkthrough`; tables `walkthroughs` + `walkthrough_steps`, migrations **0051–0058**.

The driver DRIVES the app toward a goal and reports a **deterministic** outcome — success is
**OBSERVED via a P4-style success-assertion** (selector / url_contains), **NEVER LLM-judged**;
`outcome ∈ {success, stuck@stepK, failed}` set by the loop.

- Routes: `POST /api/targets/{id}/walkthrough`, `GET /targets/{id}/walkthrough-status`,
  `POST /api/targets/{id}/walkthroughs/{wid}/evaluate` (materializes the driven trace as a
  synthetic run → runs the Phase-1 personas over each driven step, in flow order).
- Read-API: `GET /api/audit/walkthroughs/{id}` (returns `eval_run_id` → pull persona findings
  via `…/runs/{eval_run_id}/evaluation`).
- Metrics: `auditloop_walkthrough_{runs_total{outcome},steps_total{outcome},duration_seconds,cost_usd_total}`.

**Synthetic walkthrough runs (`trigger='walkthrough'`) are EXCLUDED from the P2 baseline
queries, the findings trend, and the run list.**

## Phase 4 — walkthrough regression (PR #38)
A walkthrough is diffed vs the target's **previous terminal walkthrough** (baseline
`walkthroughs.prev_walkthrough_id`, migration **0062**; the diff in `walkthroughs.diff_json`,
**0063**) — the `outcome`/`stuck_step` transition + new/resolved **task-blockers** (only
VERIFIED blockers count).

Read-API `GET /api/audit/walkthroughs/{id}` gains a **`regression` block** (`is_regression`,
`new_task_blockers`, `reason_changed`) so a CI `--fail-on-regression` gate keys on it.
Metric `auditloop_walkthrough_regressions_total`.

## LLM completion-token tiers
All per-call via `llm.WithMaxTokens`, all reuse `OPENROUTER_API_KEY`:

| env | value | scope |
|---|---|---|
| `AUDITLOOP_LLM_MAX_TOKENS` | 1024 | notes |
| `AUDITLOOP_LLM_EVAL_MAX_TOKENS` | 2000 | per-page persona gen/verify |
| `AUDITLOOP_LLM_SYNTH_MAX_TOKENS` | 3000 | run-level synthesis |
| `AUDITLOOP_LLM_DRIVE_MAX_TOKENS` | ≈256 | per-turn driver planner (one action JSON) |

## 🔴 DOM/a11y grounding for the evaluator
PRs **#35** (Phase-1 crawl path) + **#37** (Phase-2 driven path) + issue **#36** (structural
label gate).

The persona evaluator was **screenshot-only**, so it invented a11y false positives. Fix: a
**bounded DOM/a11y digest** is captured per page/step (`internal/crawler/a11y-digest.js` → an
`a11y.json` artifact + `pages.a11y_digest_key`, migrations **0060/0061**; the driver's
per-step digest is carried through `MaterializeWalkthroughRun`), then a **deterministic**
`internal/eval/verify.go` `dropContradicted` gate **drops findings the digest REFUTES** (e.g.
"card not keyboard-operable" when it's an `<a>`; "no label" when an `sr-only <label for>`
exists) — **no LLM call**. #36 made the label refute STRUCTURAL (a `label_source` field on
interactive elements). **Measured ~20% precision lift on objective a11y claims.**

## ✅ #27 — click-nav SSRF hardening
The driver's same-origin host-allowlist is now enforced on **every** paused Document nav
(click-triggered navs included), not just explicit `navigate` actions — `intercept.go`
`checkNav` gates each nav's host against `GuardConfig.hostAllowed` AND the IP guard
(`checkHostIP`), closing the click-can-follow-a-public-off-domain-link gap (observed live:
example.com → iana.org via a click).

## Live-smoke record
Each feature verified against prod: persona eval 6/6 cells + synthesis story on a
remix-funnel run · goal inference drafted an accurate remix config · driver example.com
dry-run → success in 2 steps · PR-B per-step persona findings over the driven trace.
Smoke path = the **smoke-user JWT** in the session scratchpad `login.json` + the
**`remix-ci-reader`** read key.
