---
name: obs-read
description: "One-command, cluster-aware observability query tool. Collapses the hand-rebuilt chain (kubectl port-forward -> ad-hoc PromQL/LogQL -> inline python parse -> teardown) into a single deterministic call over Prometheus/Loki/Pyroscope, with a LOUD silent-zero guard so a wrong service/label can never masquerade as a real 0. Use for querying metrics/logs/profiles during an incident or perf dig, the 5xx/error-rate/latency/CPU-saturation reads, or any 'is X actually zero or did my query just miss'."
argument-hint: "--cluster homelab|workbench|dpprod|nebula (--preset NAME | --backend B --query 'EXPR') [--since 30m] [--json] | --list-presets"
allowed-tools: Bash
---

# /obs-read — deterministic observability queries with a silent-zero guard

Runs `scripts/obs-read`, which owns the whole `kubectl port-forward -> query ->
teardown` cycle against **Prometheus / Loki / Pyroscope** on an explicit cluster,
parses the result into a readable table (or `--json`), and — the whole point —
makes the **silent-zero** trap impossible to miss: an empty result set is
rendered as a LOUD warning, never as a clean `0`, while a series whose value is
genuinely 0 renders normally.

## Safety
- **`--cluster` is REQUIRED** (`homelab|workbench|dpprod|nebula`) → maps to the
  pre-exported kubeconfig handle (`$KC_HOMELAB` / `$KC_WORKBENCH` / `$KC_DPPROD`
  / `$KC_NEBULA`). There is **no default cluster** — a missing handle is a clear
  error, never a silent wrong-cluster. `dpprod` is a CLIENT prod cluster.
- Read-only (query APIs only). Bounded timeouts; the port-forward is torn down on
  success, error, and signal.

## Usage
```bash
# discover the preset library (validated vs unvalidated + source)
scripts/obs-read --list-presets

# a surveyed, validated preset
scripts/obs-read --cluster dpprod --preset dp-5xx-rate
scripts/obs-read --cluster dpprod --preset dp-code-breakdown --json
scripts/obs-read --cluster dpprod --preset dp-trpc-errors --since 1h

# ad-hoc raw query (must name the backend)
scripts/obs-read --cluster homelab --backend prometheus --query 'sum(up)'
scripts/obs-read --cluster homelab --backend loki --query '{namespace="monitoring"}' --since 5m
```

## The silent-zero guard
- **MATCHED NOTHING** (zero series / zero rows / empty matrix / empty profile) →
  a prominent `⚠ QUERY MATCHED NOTHING — likely a wrong label/service name, NOT a
  confirmed zero` banner on stderr. Treat it as "check the metric/label exists",
  not as a real 0.
- **matched, value 0** → rendered normally with a `note: … a REAL zero`.
- **expected-absence presets** (e.g. `homelab-alerts-firing`, where empty = "no
  alerts firing" = healthy) carry an `absence_ok` flag, so an empty result renders
  a calm `✓ OK — nothing firing` instead of the ⚠ banner — the guard stays loud
  only where empty is genuinely suspicious.

## Presets
Seeded from **real** queries surveyed out of the datapacket skills
(`investigate-dp-errors`, `heap-snapshot`, `civitai-signals`, `pyroscope`).
`--list-presets` tags each `validated` (lifted verbatim from a `file:line`
source) or `UNVALIDATED` (a standard/built-in query not lifted from a session —
e.g. the `ALERTS` firing-alert and cAdvisor per-pod-CPU presets, and the
pyroscope render preset whose endpoint/profile-type is best-effort). Prefer a
validated preset; treat unvalidated ones as starting points.

## Notes
- Operated deterministically — no LLM in the path. Extend the preset library or
  wiring in `scripts/obs-read`; tests are `scripts/tests/test_obs_read.py`.
- `--since` applies to range/profile queries (Loki, Pyroscope, `--kind range`).
- Signal-safe teardown: kubectl runs in its own session and is torn down by
  killing the process group on success/error/SIGINT/SIGTERM (no leaked tunnel).
- Known limitations (documented, unchanged): a matched-nothing result still exits
  0 (check the `--json` `matched_nothing`/`warning` fields to fail a pipeline);
  `_free_port` has a sub-ms TOCTOU window (kubectl fails loudly, surfaced via its
  captured stderr, if the port is taken).
