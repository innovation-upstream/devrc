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
