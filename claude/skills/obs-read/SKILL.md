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

## Prior work first — the store is keyed the way your query is
Before an incident/perf dig (not before a one-off number read), ask what a past
session already diagnosed. The subsystem store is keyed by **metric / service /
namespace** — exactly what `--preset` and `--query` name — so obs-read's own
arguments ARE the retrieval keys. Scope is a **function of `--cluster`**, never a
judgement call:

```bash
CLUSTER=dpprod                          # the same --cluster you are about to pass
Q='node_network_receive_drop_total'     # the metric / service / namespace you are about to query
case "$CLUSTER" in
  dpprod)                    SCOPE=datapacket-talos ;;
  homelab|workbench|nebula)  SCOPE=homelab-talos ;;
esac
if command -v cairn >/dev/null; then cairn search "$Q" --scope "$SCOPE"; else echo "skipped: cairn unavailable"; fi
```

- 🔴 **Keep the `if … then … else … fi` form.** `command -v cairn && …` exits
  non-zero when cairn is absent (1 in bash/zsh, **127 in dash**) and reads as the
  observability step failing; a bare `if` with no `else` skips SILENTLY. The `else`
  echo is load-bearing.
- **No `cairn sync` prefix** — `cairn search` syncs itself; a prefix fetches the
  whole store twice.
- 🔴 **Explicit `--scope`, never `--all-scopes`.** `--all-scopes` derives a scope
  from the cwd's git repo and exits **rc 2** outside one (obs-read is documented to
  run from any cwd), and it would answer a homelab question out of a CLIENT
  cluster's scope — §Safety's wrong-cluster trap through a new door. `--scope`
  needs no git cwd.
- An empty result is a fact about the QUERY before it is a fact about the store:
  try the metric, then the service/namespace, then the subsystem (`monitoring`,
  `prom-stack`, `pyroscope`, `prometheus-stack`) before concluding nothing is recorded.
- Everything returned is `RECALL, NOT LIVE OBSERVATION` — a remedy that has since
  landed reads exactly like one that has not. It is a pointer to verify with the
  query you were going to run anyway, not a substitute for it.

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
- 🔴 **FOURTH CASE, and it INFLATES rather than empties: `--kind instant` still issues a
  RANGE query, so a `count by (<label>)` UNIONS that label across every evaluation
  instant.** Each instant carries its own `[window]` lookback, so the series set you get
  back spans `--since` **plus** the window, not the window. Measured 2026-09-07 asking for
  distinct taskrun pods in 24h: **3,787** returned — a 48h union — against a true
  **1,591**. Nothing errors and the number is entirely plausible, which is what makes it
  expensive. **For a distinct-count, use the scalar form** — `count(count by (pod) (…))` —
  **and read it at ONE instant** (`--since 10m` with the real lookback inside the range
  selector). **The tell is the `POINTS` column**: a value beside `POINTS 251` is a matrix
  row, not an instant reading. Same shape whenever a `by (…)` label is high-cardinality
  and short-lived — pods, taskruns, request ids.
- ⚠ **A high-cardinality `by (…)` over a long window can also just be REFUSED**: Loki caps
  a single query at `maximum number of series (5000)`. Chunking the window is the obvious
  workaround and the dangerous one — if your extractor scores a failed chunk as empty, a
  partial scan prints as a confident total. **Narrow with a stream selector instead**
  (`{ns="x", pod=~"<prefix>-.*"}`), which keeps each query under the cap and usually scopes
  it to the question you were actually asking.

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

## The `--json` row schema — read a label from `labels`, NEVER from `metric`

🔴 **`rows[].metric` (prometheus) and `rows[].stream` (loki streams) are RENDERED
DISPLAY STRINGS** — `{a=1, b=2}` — built for the human-readable table. They are
NOT objects and they are NOT Prometheus's `data.result[].metric`, despite the
name. Field-accessing them returns nothing, silently, while `row_count` sits in
the same document saying the query matched.

**`rows[].labels` is the label set as a `{str: str}` dict.** Use it:

```bash
obs-read --cluster dpprod --backend prometheus --kind instant --json \
  --query 'kube_job_status_start_time{cluster="dp-1"}' \
  | jq -r '.rows[] | "\(.labels.job_name) \(.labels.namespace)"'
```

Present on prometheus **vector** and **matrix** rows and on loki **matrix** and
**streams** rows. Absent on prometheus **scalar**/**string** results, which have
no label set at all — so read it with `.get("labels", {})` / `.labels? // {}`
rather than assuming every row carries one.

🔴 **Read `row_count` and `matched_nothing` before concluding anything from an
empty parse.** A zero from your own parser is a fact about your parser; those two
fields are the tool's own answer, and they disagreed with a hand-written parser
three times in a row once.
