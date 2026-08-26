---
name: adoption-scan
description: "Report which shipped productivity tools are actually USED and YIELDING versus idle or DEAD. Use for \"is anyone using X\", \"which tools are dead\", \"did the tool catch anything\", or whether a shipped tool stuck."
argument-hint: "[--days N] [--insight-days N] [--dead-days N] [--host H] [--json] — optional; defaults to --days 14 --insight-days 30"
allowed-tools: Bash
---

# /adoption-scan — are the shipped tools USED + YIELDING?

Runs the read-only `adoption-scan.py` report. It answers the measured failure mode ("opt-in commands didn't stick → pivoted to autonomous loops") deterministically off `activity.events`:

- **ADOPTION** — invocations per tracked tool in the window, outcome breakdown, this-half vs prior-half trend, and a loud **DEAD** flag at 0 uses (opt-in tools are the ones most at risk of silently dying).
- **IMPACT** — the outcome MIX that evidences a real catch: verify-agent `fail|incomplete` (false-greens), obs-read `matched-nothing` (silent-zeros), ticket-status `already-done` (non-tasks dissolved).
- **DRAFTER FRICTION TREND** — `permission_block` + `wrong_approach` over two windows (DIRECTIONAL — window confounds).

Reader-only; degrades to a clean notice when telemetry is unconfigured. Needs the `CLICKHOUSE_*` reader creds (see the `activity` skill). Args: `$ARGUMENTS` (passed through; default `--days 14`).

```bash
python3 ~/workspace/devrc/scripts/session-analysis/adoption-scan.py $ARGUMENTS
```

Honesty: an invocation COUNT proves USE, not VALUE — only the outcome mix gestures at value, and the friction trend is directional. Present the DEAD opt-in tools first (retire or auto-trigger candidates), then the impact mix, then the friction direction.

## 🔴 Coverage limit — it can only see tools that EMIT, so a missing row ≠ a used tool
The `REGISTRY` (`adoption-scan.py:95-163`) tracks 9 items. Every one is a **devrc-owned wrapper
that calls `collector/invocation.py:emit_invocation`** — `verify-agent-work`, `obs-read`,
`ticket-status`, `playwright-nixos`, `opencode-dispatch`, `browser-bridge` (+ two slash-command
rows and the drafter cwd heuristic). **Anything that does not emit is invisible, and no registry
row can change that.**

**Known uncovered: the `make ux-audit` harnesses** (naida / vetr / auditloop-self / remix /
civitai-manager — see `ux-audit-loops`). They run from a Makefile inside those repos, not through
a devrc wrapper, and emit **nothing**. So this scan **cannot** answer "is `make ux-audit` actually
used". 🔴 **Do NOT paper over that with a `via="tool"` row**: with no emitter the count is a
structural 0, and 0 uses raises the **loud ⚠ DEAD flag** (`:506`, printed `:583,594`) — the tool would
confidently report a possibly-daily harness as DEAD. A zero here is indistinguishable from "wired
to nothing", which is the exact failure this report exists to avoid.

**The real fix is an emitter, not a row** (a separate change in each producer repo): have the
ux-audit runner call `emit_invocation("ux-audit", outcome, …)`, confirm a positive control moves
the number off 0, *then* add the registry line. Until then, treat ux-audit adoption as
**unmeasured** and say so — `ux-audit-loops` proposes an indirect proxy instead (re-run
`activity-scan --days 7` and watch manual QA browser-time drop), which is directional, not a count.
