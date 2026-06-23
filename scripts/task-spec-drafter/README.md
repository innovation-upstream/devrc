# Continuous deep-context task-spec drafter — v1 (SHADOW-first)

A **verifier / triage layer** over inbound, not a task-factory. It autonomously
cross-checks each inbound ticket against reality and surfaces only the genuine
few, decision-ready — so Zach stops processing raw inbound (read → cross-check →
decide per signal) and instead glances at a small, pre-verified queue.

This is v1 of the validated 10x lever (`close-the-loop` STATE.md, 2026-06-23):
on a real 8-ticket batch the **deep-context** pipeline scored **8/8 correct vs
the naive title-only drafter's ~2/8** — and crucially it *prevented harmful
misfires* (the naive run would have drafted "add a Meilisearch backup cron",
which would have crashed a deliberately-suspended Meilisearch). The reframe: of 8
inbound "tickets" only **1** was a genuine dispatch-ready task; the rest dissolved
on verification (already done / stale / underspecified / deliberately-off).

**Source in v1:** `TICKET` (ClickUp). Output: a structured queue routed to
clawgate. **SHADOW by default** — it writes the queue + logs "would send" and
**sends nothing, dispatches nothing, writes nothing** until you flip it on.

## What it does (per ticket — the validated pipeline)

1. **ENRICH** — full body + ALL comments + status + created/last-activity (age) +
   assignees + linked tickets/PRs (via the `clickup` skill CLI). Never the title alone.
2. **VERIFY current state** vs reality:
   - already fixed? → `git log` / `gh pr list --search --state all` in `civitai`
   - still firing? → live metrics/alerts via `KUBECONFIG=…/prod-kubeconfig`
   - intentionally off / constrained? → config/state check
3. **CORRELATE** on VERIFIED links only (shared refs / same root cause), never
   temporal coincidence; flag duplicates (distinguished from "adjacent").
4. **CLASSIFY**: `TASK` / `FYI` / `STALE-close` / `ALREADY-DONE` / `VERIFY` /
   `NEEDS-DECISION` / `DUPLICATE`.
5. **DRAFT / RECOMMEND** — a dispatch-ready spec (goal / done(verifier) / owner /
   autonomy) ONLY for genuine `TASK`s; for the rest, the recommendation.
6. **SAFETY RULE** — if it can't verify *why* something is/isn't being done, it
   flags `NEEDS-DECISION` rather than draft a confident, possibly-harmful task
   (the meili-cron lesson). Drafting is gated behind verification, by construction.

## Model + the deterministic safety-escalation gate

The per-ticket reasoning pass runs on **Haiku by default** (`DRAFTER_MODEL=haiku`;
override to `sonnet`/`opus` or a full id). Haiku is ~cents not dollars per ticket.

**Why a structural gate is mandatory.** A measured test showed Haiku runs the
verify tools fine but **lacks the judgment to flag intent-ambiguity**: it
confidently mis-drafted the safety-critical "Civitai Link on `.red`" cert ticket
as a high-confidence `TASK` with **no** `safety_flag` (Opus correctly said
`NEEDS-DECISION`), and missed it even with more tool turns. So we **do not trust
the model's self-assessment** for the dangerous classes. The fix is structural
(code), not prompt/model.

**What the gate does** (`safety_gate()` in `drafter.sh`). After the model emits
its record, a deterministic step scans the **ticket text (title + body + all
comments) AND the model's own verification/spec text** for risk keywords
(word-boundary, case-insensitive). If any RISK category matches, it **overrides
the model**: forces `classification = NEEDS-DECISION`, `spec.autonomy =
needs-Zach`, blanks the dispatchable spec, downgrades a `high` confidence to
`medium`, and stamps `safety_flag` + audit fields (`gate_fired`,
`gate_categories`, `gate_override_from`). It runs independently of what the model
returned — even an `ERROR`/timeout record passes through it.

Risk categories (tune the regexes at the top of `drafter.sh`):

| Category | Keywords (abbrev.) |
|---|---|
| **security/secrets** | cert, tls, ssl, mtls, mta-sts, secret, token, credential, password, auth, authn/authz, rbac, vuln, cve, disclosure, exploit, x509, `.red` |
| **money** | buzz, currency, payment, refund, withdraw, payout, billing, invoice, stripe, paypal, subscription, chargeback, wallet, merch |
| **destructive/prod-mutation** | delete, drop, truncate, migration, rollback, restore, prod/production, scale down, evict, wipe, purge, destroy, drop table, force push |

The gate is intentionally **conservative (escalate-on-touch)**: `migration` /
`delete` / `prod` are common dev words, so the destructive category fires often.
That bias is the point — a false escalation costs one human glance; a false
auto-dispatch on a `.red`-cert / Blue-Buzz-currency / prod-`delete` ticket is the
harm this exists to prevent. Verified to catch both Opus-safety-rule cases (the
`.red` cert ticket → security, the Blue-Buzz currency ticket → money) **even
though Haiku alone did not flag them.**

## Files (all in devrc — harness artifacts, NOT a project repo)

| File | Purpose |
|---|---|
| `scripts/task-spec-drafter/drafter.sh` | Orchestrator — fetches the queue, runs the per-ticket headless pass, writes the shadow queue, routes to clawgate (shadow/on). |
| `scripts/task-spec-drafter/drafter-prompt.md` | The five-step pipeline prompt (the LLM reasoning core). Read-only HARD CONSTRAINTS up top. |
| `scripts/task-spec-drafter/task-spec-drafter.env.example` | Copy to `~/.claude/task-spec-drafter.env`. Master knob `DRAFTER_MODE`. |
| `scripts/task-spec-drafter/systemd/*.{service,timer}` | Daily user timer (09:15 local). |

It deliberately **mirrors `devrc/githooks/audit-on-push.sh`** (the audit-on-push
hook): same shadow/flag pattern, same `~/.claude/clawgate.env` + `/api/send`
clawgate path, same "log what it would send" shadow behavior. It does **NOT**
modify the deployed `standup-triage` CronJob — it reuses its patterns (the
ClickUp `/view/<id>/task` fetch, the "To Schedule" view `6-901111220963-1`, the
propose-only philosophy) but lives entirely in devrc.

## How the verify step runs (headless claude)

The VERIFY+CLASSIFY+DRAFT step is LLM reasoning over gathered context, invoked
via headless `claude -p` once **per ticket**. It is given a **tight read-only
allowlist** (`--allowedTools`) so its verification tools actually execute
non-interactively — the `clickup` CLI (get/comments), `git -C civitai log/show/grep`,
`gh pr list/view/search`, `kubectl get/logs/describe` — but **no** write verbs
(no apply/edit/delete/scale/commit/push/comment). `--permission-mode plan` was
rejected: it blocks tool execution, so the model reasons from the title only —
the exact failure mode this design exists to kill. Read-only is enforced by both
the prompt's HARD CONSTRAINTS and the allowlist.

## Run it

```bash
# One-off, cheap (3 tickets), shadow, scratch output dir:
DRAFTER_MODE=shadow DRAFTER_MAX_TICKETS=3 DRAFTER_OUT_DIR=/tmp/drafter \
  /home/zach/workspace/devrc/scripts/task-spec-drafter/drafter.sh

# Full live queue, shadow (default), real output dir (~/.claude/task-spec-drafter):
/home/zach/workspace/devrc/scripts/task-spec-drafter/drafter.sh
```

Output per run, in `DRAFTER_OUT_DIR` (default `~/.claude/task-spec-drafter/`):
- `queue-<ts>.jsonl` — one structured record per ticket (the machine queue)
- `queue-<ts>.md` — human summary: counts by class + the action-worthy items + suppressed one-liners
- `latest.jsonl` / `latest.md` — symlinks to the newest run
- `drafter.log` — run log; in shadow it logs the exact clawgate payload it *would* send

## Enable: shadow → on

Default is shadow (safe). To go live (route the triage summary to clawgate/phone):

```bash
cp /home/zach/workspace/devrc/scripts/task-spec-drafter/task-spec-drafter.env.example \
   ~/.claude/task-spec-drafter.env
# edit ~/.claude/task-spec-drafter.env: set DRAFTER_MODE=on
```

`on` still **only sends a notification** — it never dispatches an agent and never
writes to ClickUp/repos/cluster. The clawgate notice is an informational
`permission` card listing the action-worthy items (TASK / NEEDS-DECISION /
VERIFY + any safety flags). Kill-switch: set `DRAFTER_MODE=off` or `shadow`.

## Schedule

Simplest mechanism on this (devrc) host = a **user systemd timer**, daily at
09:15 local (before standup-triage's 10:30 slot):

```bash
mkdir -p ~/.config/systemd/user
ln -sf /home/zach/workspace/devrc/scripts/task-spec-drafter/systemd/task-spec-drafter.service ~/.config/systemd/user/
ln -sf /home/zach/workspace/devrc/scripts/task-spec-drafter/systemd/task-spec-drafter.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now task-spec-drafter.timer
systemctl --user list-timers task-spec-drafter.timer     # confirm next run
```

(NB: this host currently runs headless / server-mode; a *user* timer needs the
user session/linger active — `loginctl enable-linger zach` — or run it from the
graphical session. The timer is shadow-by-default so enabling it changes nothing
until `DRAFTER_MODE=on`.)

**Prod path (alternative, documented not built):** the deployed `standup-triage`
CronJob is the natural home for an in-cluster version (same ClickUp view, same
GitOps ConfigMap-script pattern, ClickHouse for the accuracy loop). That would
swap headless-claude for the OpenRouter call standup-triage already uses. v1
keeps the build in devrc per the brief; graduating to the CronJob is the prod
step once shadow proves out.

## Graduation path (tickets stay GATED)

Autonomy is earned per source, not granted:

1. **TICKET (this source) — GATED.** Stays shadow until the **adjudication ratio**
   proves out: over ≥N runs, what fraction of drafted `TASK`s does Zach approve
   without rewriting, and how often does a `NEEDS-DECISION`/safety flag turn out
   correct? A spec is leverage only if adjudicating beats rewriting. Until that
   ratio is measured and good, tickets never auto-dispatch — at most they reach
   the clawgate queue (`DRAFTER_MODE=on`) for one-tap human adjudication.
2. **ALERTS — earned auto-dispatch.** In the proof, live alerts drafted **4/4**
   (signal *is* state, nothing to misverify), so the alert source is the first
   candidate to graduate to auto-dispatch. (Not in v1; v1 is TICKET-only.)
3. **EMAIL — added last.** Highest-context, lowest-structure source; add only
   after TICKET + ALERTS are stable.

The gate is the validated fleet default (`close-the-loop` STATE.md): **gate every
prod mutation; an action class earns unattended action only via measured
acceptance (≥90% over ≥N).** Blast-radius does not auto-grant autonomy;
trust-history does.

## Honest caveats (what still needs work before flipping off shadow)

- **Cost per run.** Each ticket = one headless `claude -p` pass with real tool
  calls (git/gh/kubectl reads + ClickUp). On Opus that is **dollars, not cents,
  per ticket** — the full ~74-ticket queue is materially more expensive than
  standup-triage's ~$0.04/day OpenRouter run. Mitigations: `DRAFTER_MAX_TICKETS`,
  same-day idempotency (skip already-classified — *not yet implemented*, see below),
  and/or porting the reasoning step to a cheaper model (OpenRouter, as
  standup-triage does) for the prod CronJob path. **Measure a full-queue run's
  cost before scheduling daily over the whole queue.**
- **Headless-claude reliability.** Per-ticket calls can timeout or emit
  unparseable output under load; the script degrades gracefully (emits an `ERROR`
  record, continues) but those tickets are simply skipped that run. No retry yet.
- **Verification is best-effort, not exhaustive.** The model reaches the sources
  it judges relevant; it can miss a fix in an unsearched repo or a metric it
  didn't query. `confidence: low` flags when a source was unreachable. Treat
  `confidence: medium/low` TASKs as drafts to sanity-check, not ground truth.
- **No accuracy loop yet.** standup-triage measures itself by re-reading what
  humans decided (the free-label verifier). This v1 has **no** such write-back —
  the adjudication ratio for graduation must be tracked manually (or built next:
  record drafts → later read whether Zach acted on each). Until then,
  "graduation" is a human judgement over the shadow log.
- **No de-dup across runs.** Re-runs re-classify the whole queue (cost + the
  same items resurface). standup-triage's `<!-- marker -->` idempotency is not
  ported (it can't write the marker — read-only). A run-to-run "already
  classified this ticket-version" cache is the obvious next addition.
- **clawgate notice shape.** In `on` mode the summary arrives as a `permission`
  card (same path as audit-on-push) — it leaves a card to clear and is not yet a
  first-class "triage queue" surface in clawgate. Fine for v1; a dedicated tab is
  future work.

## Proof run

See `claudedocs/task-spec-drafter-proof-2026-06-23.md` for the end-to-end
shadow-run output on the live ClickUp queue (the structured queue produced,
spot-checks vs reality, and confirmation that nothing was sent/dispatched/written).
