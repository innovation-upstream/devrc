# auditloop UI / design system + the meta-run dogfood

Read this when **writing UI** in auditloop (token/component conventions are durable and
binding) or when reproducing the meta-run that generated the redesign findings.

## UI / design system (redesign 2026-07-20, PRs #27–#34)
A meta-run (auditloop audited its OWN UI → "information overload") drove an 8-PR redesign.
**Tailwind bumped 4.3.2 → 4.3.3** (latest; no v5 exists).

`static/input.css` now defines `@theme` semantic tokens (`info`/`success`/`warning`/`danger`
+ `-fg`, `brand-hover`, `card-hover`, `--radius-sm/lg/xl`, `--font-mono`, motion
easings/durations) + an `@layer components` set (`.card`/`.card-interactive`,
`.btn-primary`/`.btn-secondary`/`.btn-accent`, `.section-title`,
`.badge`+`.badge-{info,success,warning,danger}`) + `motion-safe:`-gated keyframes
(`animate-enter`/`animate-fade`/`animate-live`) over a `prefers-reduced-motion: reduce`
baseline.

**🟡 Durable convention:**
- New UI uses these tokens/component classes, **NOT raw `blue/red/emerald/amber`** utilities.
- All motion is `motion-safe:`-gated.
- **NEVER animate an htmx self-poll root** — it re-fires every 3s = a blink.
- Progressive disclosure = native `<details>` accordions.

Redesigned views:
- **dashboard** — first-class project cards (favicon/monogram + run-screenshot carousel +
  auth/status/stats).
- **target** — overview header + `<details>`-collapsed config (Auth accordion auto-opens when
  `auth_mode=login`).
- **run** — a professional report (exec summary → P2 "Changes since" → worst-first per-page
  cards → "Deeper analysis" = eval+notes).

Audit doc: `claudedocs/design-system-audit-2026-07-19.md`.

## Meta-run dogfood — run the persona evaluator on auditloop's OWN UI
How the redesign findings were generated (**~$0.28/pass**):

1. `make ux-audit` captures 9 views.
2. Boot a LOCAL **persistent** DEV_MODE auditloop with the **REAL `OPENROUTER_API_KEY`** +
   sqlite/fs storage + `CRAWL_ALLOW_LOOPBACK`.
3. Create a plugin target (mint token).
4. Re-run the walk with `AUDITLOOP_PUSH_URL` + `AUDITLOOP_PUSH_TOKENS` pushing to it.
5. `POST /api/runs/{id}/evaluate` (personas `first-time-nontechnical` +
   `skeptical-evaluator`) + Draft UX notes.
6. Collect via the read API or the sqlite DB.

Extract the real key from the k8s secret:
```bash
KUBECONFIG=~/workspace/homelab-infra/workbench-kubeconfig kubectl get secret auditloop-secrets -n auditloop \
  -o jsonpath='{.data.OPENROUTER_API_KEY}' | base64 -d
```

Findings → `claudedocs/meta-run-ux-findings-2026-07-18.md`.
