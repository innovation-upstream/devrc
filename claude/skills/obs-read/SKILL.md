---
name: obs-read
description: "Query Prometheus/Loki/Pyroscope on a named cluster in ONE deterministic call (port-forward -> query -> teardown), with a LOUD silent-zero guard. Use for metrics/logs/profiles during an incident or perf dig, the 5xx / error-rate / latency / CPU-saturation reads, or \"is X actually zero or did my query just miss\"."
argument-hint: "--cluster homelab|workbench|dpprod|nebula (--preset NAME | --backend B --query 'EXPR') [--since 30m] [--json] | --list-presets"
allowed-tools: Bash
---

# /obs-read — deterministic observability queries with a silent-zero guard

Runs **`~/workspace/devrc/scripts/obs-read`**, which owns the whole
`kubectl port-forward -> query -> teardown` cycle against
**Prometheus / Loki / Pyroscope** on an explicit cluster,
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

🔴 **Use the ABSOLUTE path — `obs-read` is NOT on `$PATH`, and it lives in the
`devrc` repo, not in whatever repo you are working in.** A bare `scripts/obs-read`
resolves against the current repo and fails there (this sent a 2026-07-27 session
back to a hand-rolled `kubectl port-forward`). It is a self-contained script and
runs correctly from any cwd.

```bash
OBS=~/workspace/devrc/scripts/obs-read

# discover the preset library (validated vs unvalidated + source)
$OBS --list-presets

# a surveyed, validated preset
$OBS --cluster dpprod --preset dp-5xx-rate
$OBS --cluster dpprod --preset dp-code-breakdown --json
$OBS --cluster dpprod --preset dp-trpc-errors --since 1h

# ad-hoc raw query (must name the backend)
$OBS --cluster homelab --backend prometheus --query 'sum(up)'
$OBS --cluster homelab --backend loki --query '{namespace="monitoring"}' --since 5m
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
- 🔴 **A PARTIAL result set is the third case, and the guard above does not cover it.
  Loki emits NO sample for a bucket with no matching lines**, so a range query over a
  sparse stream returns far fewer points than `(end-start)/step` — measured 6 where 49
  were expected. That reads as downsampling or a server limit; it is **absent-means-zero**,
  and the non-zero points are the whole truth. **Compute the expected sample count and
  compare** before drawing any conclusion from a short series, and for "is it still
  happening?" prefer a Prometheus **counter** (dense — `increase()` over the window)
  to a Loki `count_over_time`. Same trap in the time axis: `count_over_time[1h]`
  evaluated at instant T covers **T-1h → T**, so a bucket labelled `16:00` can be
  reporting a 15:31 incident.

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
  wiring in `~/workspace/devrc/scripts/obs-read`; tests are
  `~/workspace/devrc/scripts/tests/test_obs_read.py`. (Both paths are in the
  **devrc** repo — every relative path in this doc is relative to that clone.)
- `--since` applies to range/profile queries (Loki, Pyroscope, `--kind range`).
- Signal-safe teardown: kubectl runs in its own session and is torn down by
  killing the process group on success/error/SIGINT/SIGTERM (no leaked tunnel).
- Local-port race NARROWED, not closed: `_free_port` is TOCTOU by construction
  (the probe socket closes before kubectl binds), so `PortForward.__enter__`
  makes at most `PF_ATTEMPTS` (3) attempts **in total — 1 initial + 2 retries**,
  re-picking the port when — and only when — kubectl died with a bind collision,
  reaping each failed attempt's process first. Every other failure (missing
  service, wrong namespace, RBAC denial, backend never ready) still surfaces on
  the FIRST attempt with kubectl's own message unchanged.
- 🔴 **Residual window (reproduced):** the retry fires only if our kubectl's
  collision-exit is seen before the readiness probe gets *any* HTTP answer on
  that port — so when the racing winner starts serving first, obs-read attaches
  to **its** tunnel: cross-cluster that is a wrong-cluster answer the
  silent-zero guard cannot catch (the result is non-empty). Don't fan out
  concurrent obs-read runs across *different* clusters.
- 🔴 **Second, non-racing mechanism (reproduced): our kubectl may never exit at
  all.** `_free_port` binds **IPv4 only**, but kubectl's `--address` defaults to
  `localhost` — both `127.0.0.1` and `[::1]` — and it counts *any* successful
  listener as success. So a **v4-only, non-kubectl** thief makes kubectl's v4
  bind fail, its v6 bind succeed: it prints `Forwarding from [::1]:P`, writes
  **nothing to stderr, and never exits** (measured: alive at 30 s). There is no
  collision-exit to classify, so no retry — the probe hits the *interloper* on
  127.0.0.1, giving a readiness timeout, or a wrong answer if it speaks HTTP.
  Two concurrent obs-read runs are **unlikely but NOT immune**: kubectl's v4 and
  v6 binds are separate calls and it fails only if *neither* succeeds, so if the
  winner has taken 127.0.0.1 but not yet [::1], the loser binds v6 and both stay
  alive and silent. Normally the winner holds both and the loser gets a clean
  collision the retry handles.
- Closing both properly means parsing kubectl's own `Forwarding from …` line
  (today `DEVNULL`) **in addition to** the HTTP probe — the line proves *we* own
  the port at bind time, not that the backend answers, so it does not replace
  readiness. Require the **127.0.0.1** line for our own port, or the v6-only case
  above still reads as success; and DRAIN that pipe (kubectl writes a
  `Handling connection for P` line per connection and blocks at 64 KiB unread).
- Known limitation (documented, unchanged): a matched-nothing result still exits
  0 — check the `--json` `matched_nothing`/`warning` fields to fail a pipeline.
