# Gametape — Claude Code session review (2026-07-24, trailing 4 days)

Second gametape run (prior: `gametape-session-review-2026-07-20.md`). Window: 2026-07-20 → 24.

## Method
- **Corpus:** 83 settled, un-extracted sessions from the last 4 days (all workbench — the
  laptop had nothing new in-window), via the Layer-B `session-insight` pipeline. 4 giant
  sessions capped to a 42-chunk head/mid/tail sample; 8 extraction subagents; 0 unreadable.
- **Caveat that shapes everything below:** this window is **dominated by the headless
  `task-spec-drafter` fleet** — ~63 short `claude -p` runs, one per ClickUp ticket. The
  drafter fires *per ticket*, so it produces many sessions; this is not "76% of your work,"
  it's "one automation fires often and each fire is a session." That's *why* fixing it is
  high-leverage: the fix is cheap/deterministic and improves every future run.

## Scorecard
- **Outcomes (n=84):** 88% fully / 7% mostly / 4% partially / 1% not — the drafter reaches
  correct, honest verdicts (dissolves already-done tickets, escalates NEEDS-DECISION rather
  than fabricating specs).
- **Mean helpfulness 4.27/5** (down from 4.85 last run) — not a regression in quality; the
  mix shifted to haiku triage runs that "clearly help" (4) rather than "materially drive" (5).
- **The deterministic friction counts tell the story:** **`permission_block` = 57** (was **1**
  last run) and `wrong_approach` = 89 — almost entirely the drafter being denied its read-only
  verbs and retrying `cd && git` → `git -C`.

## The finding: your ticket-triage drafter works, but the harness is starving it
Every theme below is the same drafter, hamstrung four ways. Recurrence = distinct sessions.

| # | Pattern | Sessions | Fix |
|---|---------|:---:|-----|
| 1 | **"Is this ticket already done / where does it live?" reconstructed by hand** — every run hand-iterates 5–15 near-identical `git log --all --grep` / `gh pr list` / `find` permutations (the bulk of the tool_errors are empty searches) | 44 | A deterministic **`ticket-status <CU-id>` primitive** (see below) |
| 2 | **Allowlist/shell-guard blocks the drafter's own read-only verbs in headless** — `gh pr --search`, `kubectl get`, `git branch/rev-parse/for-each-ref`, compound `&&`, `$VAR` expansion all hit "requires approval," which a `claude -p` run **cannot grant**; 6–30 blocked calls/run, silently-failed PR cross-checks, some runs interrupted with no record | 32 | Whitelist the literal read verbs (expansion/pipe-safe) for the headless drafter context |
| 3 | **Env-handles don't resolve in `claude -p`** — `$CIVITAI`/`$KC_DPPROD` (the `.zshenv` handles shipped for *interactive* agents) aren't in the non-interactive shell, so nearly every run wastes calls on `cd civitai && git` → `git -C $CIVITAI` failures before falling back to absolute paths | 23 | Inject absolute repo roots into the drafter prompt, or export the handles into the `claude -p` env |
| 4 | **"Is commit X merged AND deployed/serving?" resolver missing** — reconstructed each time from `branch --contains` + `merge-base --is-ancestor` + reflog + image-hash checks; also blurs partial-vs-full-fix judgment | 22 | Fold into the `ticket-status` primitive (merge + deploy state) |
| 5 | **No code-symbol index** — locating the relevant function in large civitai service files takes dozens of find/grep/read-offset probes | 15 | Give the drafter serena/ctags (or a prebuilt symbol index) |
| 6 | **ClickUp structural metadata ignored** — title-only / bodyless / milestone-container / Phase-2 tickets burn a full code-probe before dissolving to NEEDS-DECISION/FYI | 8 | Upfront metadata short-circuit (type/child-count/empty-body) + cross-ticket dependency lookup before code search |

### The highest-leverage build: `ticket-status <CU-id>`
Themes 1+4 (44+22 sessions) are one missing deterministic primitive. Given a ClickUp ticket id, resolve **prior-art + ship-state** in one call:
- **First-class:** grep the `CU <id>` commit convention (the decisive, fastest signal whenever it's followed — sessions confirmed it) across `--all`, plus `gh pr list --search`.
- Then: is the matching commit **merged to trunk** and **deployed/serving** (the theme-4 chain).
- Return a structured verdict (ALREADY-DONE / PARTIAL / NOT-FOUND + evidence links).
This is a specialized cousin of `obs-read` / the existing `/verify-deploy` — deterministic, testable, and it collapses the drafter's dominant per-run toil while sharpening its judgments.

### Correctness bug worth fixing regardless of leverage
- **Anti-confabulation failure (1 session, `3d4df59b`):** for a live-incident ticket the drafter emitted "no git commits, merged PRs, or live cluster alerts found" having run **zero tools** — a fabricated verification (safe bucket, wrong process). Add a gate: **require ≥1 real read before emitting a non-`unreadable` record.**

## Tail — single-session but real (interactive arcs, not the drafter)
- **Incident probe-ladder classifier** — a clawgate-502 dig took ~11 hand-built kubectl steps to reach "CNI outage, not app bug"; the probe ladder (pod→svc-IP / pod→host-apiserver / pod→pod / DNS) belongs in the incident toolkit as a one-shot "app-bug vs CNI-outage" classifier.
- **Dev-clone schema drift** — the weekly Sunday dev-DB clone vs mid-week prod migrations silently turns newer PR-previews into all-model-page 404s (swallowed Prisma P2022); auto-reconcile prod migrations onto the dev clone or flag affected PRs.
- **Release-cycle manual driving** — the per-package "watch CI → merge Version PR → wait for OIDC publish → verify live on npm" loop was hand-driven across 4 releases.
- **Doc-vs-code gap** — no check that shipped component CSS matches `MARKUP.md` defaults; 4 components shipped unstyled until a real-app dogfood caught it.
- **Pre-merge typecheck gate** — an unrelated PR's type break reached main and blocked ALL releases (echoes last run's stale-artifact theme + the shipped `/verify-agent`).

## How this run differs from 2026-07-20
Last run's gametape → we built general tools (`verify-agent`, `obs-read`, `playwright-nixos`).
This run says the highest-leverage work is **hardening your own autonomous drafter** — it runs
constantly but is starved by permission/env gaps and re-derives the same ticket→code lookup
every time. Themes 2+3 (the harness/env fixes) are pure config, deterministic, and the
cheapest wins; the `ticket-status` primitive (themes 1+4) is the biggest build.

## Honest caveats
Leverage tags are model-surfaced, not measured savings. Recurrence is heuristic keyword
clustering. The window is skewed by the drafter's high run-frequency (per-ticket), so it
dominates the sample — real, but read it as "fix the thing that fires often," not "this is
most of your work."
