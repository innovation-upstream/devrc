# activity ClickHouse — memory-ceiling proposal (2026-08-02)

**Status:** read-only investigation. Nothing was changed, committed, or applied. All
commands below are staged for Zach.

**Investigation host:** laptop (nebula-only). Cluster via `KUBECONFIG=$KC_NEBULA`
(`~/.kube/homelab-nebula.yaml`). ClickHouse via `http://10.42.0.10:30123` with the
`default` (admin) creds decrypted read-only from
`homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml` into a mode-0600
scratchpad curl config (deleted at the end of the session; no secret value appears in
this document).

---

## 0. Headline — the premise needs correcting

> "Any broad scan of `payload` is now unreliable."

**Measured: `activity.events` is 27.09 MiB on disk / 161 MiB uncompressed / 479,221 rows
/ 74 granules total.** The `payload` column is 13.68 MiB compressed, 96.85 MiB
uncompressed **for the entire table, all time**. A full scan of it needs tens of
megabytes, not gigabytes.

Proof, from `system.query_log` — a query that was *killed* with code 241 had actually
allocated **33.70 MiB**:

```
ExceptionWhileProcessing  1211ms  33.70 MiB  76.99 MiB read
  SELECT source, kind, count() rows, formatReadableSize(sum(le…
```

and re-running the same shape with an explicit `max_memory_usage=268435456` **succeeded
in one pass**.

The queries are not the problem, the telemetry table is not the problem, and the
append-only amplification is not the memory problem either.

**The problem is that ~92 GiB of ClickHouse's own `system.*` log tables are being merged
continuously inside a 2.5 GiB budget.** `system.trace_log` is **69.40 GiB / 3.77 billion
rows**; `system.text_log` is **22.57 GiB / 370 million rows**. Neither has a TTL. At the
moment of measurement **23 merges were in flight**, holding ~1.03 GiB of the 2.5 GiB
budget, and the server had logged **74,790 `MEMORY_LIMIT_EXCEEDED` errors in 4 hours of
uptime** (~5/sec). Your analysis queries are collateral damage: they lose the
`OvercommitTracker` lottery against merges, which is exactly why the failure is
intermittent and load-dependent rather than deterministic.

This is a **self-sustaining feedback loop**, not a capacity shortfall:

```
tight memory budget
  → heavy allocation churn
    → memory profiler writes Memory/MemoryPeak traces  (99% of trace_log)
      → trace_log/text_log grow
        → more + bigger merges of those tables
          → merges eat the memory budget and FAIL with code 241
            → each failure logs Error lines into text_log …  ↺
```

---

## 1. Measured facts

Every fact below is **measured** unless labelled *inferred*.

### 1.1 Where the 2.5 GiB actually comes from — **ClickHouse's own `max_server_memory_usage`, set explicitly in a Flux-managed ConfigMap**

Three layers, all confirmed:

| Layer | Value | Source | Command |
|---|---|---|---|
| Container `resources.limits.memory` → cgroup limit | **3 GiB** | `clusters/homelab/apps/activity/clickhouse.yaml` | `kubectl get pod -n activity -o jsonpath='{.items[0].spec.containers[0].resources}'` → `{"limits":{"cpu":"2","memory":"3Gi"},"requests":{"cpu":"100m","memory":"512Mi"}}` |
| ClickHouse `max_server_memory_usage` | **2684354560 B = 2.50 GiB** | `clusters/homelab/apps/activity/configmap-memory.yaml` | `SELECT name, value, changed FROM system.server_settings WHERE name LIKE '%memory%'` → `max_server_memory_usage 2684354560 changed=1` |
| Per-query `max_memory_usage` | **0 (unlimited)** | image default | same table, `system.settings` |

The `maximum: 2.50 GiB` in your error is **exactly** `max_server_memory_usage`. It is a
hand-set value in `configmap-memory.yaml`, **not** a cgroup-derived default and **not** a
per-query limit. The `(total)` in `(total) memory limit exceeded` confirms it is the
server-wide tracker, not a query limit.

Confounder worth knowing: the cgroup watcher can clamp *below* that value.
`CGroupMemoryUsed = 3,213,586,432` of `CGroupMemoryTotal = 3,221,225,472` — **99.76% of
the 3 GiB cgroup is occupied**, mostly page cache, while `MemoryResident` is only 2.09
GiB. With `memory_worker_use_cgroup=1` the tracker reads cgroup pressure, so the
effective ceiling is `min(2.5 GiB, cgroup-derived)`. The comment in
`configmap-memory.yaml` already anticipated this; the loosened `0.95/0.99` ratios are why
it usually lands on 2.5 GiB rather than lower.

```bash
KUBECONFIG=$KC_NEBULA kubectl get pod -n activity -o jsonpath='{.items[0].spec.containers[0].resources}'
curl -s --user "default:$PW" --data-binary \
  "SELECT metric, value FROM system.asynchronous_metrics WHERE metric IN ('CGroupMemoryUsed','CGroupMemoryTotal','MemoryResident') FORMAT TSV" \
  http://10.42.0.10:30123/
# CGroupMemoryTotal  3221225472
# CGroupMemoryUsed   3213586432
# MemoryResident     2245955584
```

**Also measured — CPU is pegged, not just memory.** `kubectl top pod -n activity` →
`1992m` against a `2000m` limit. `max_threads` resolves to `auto(2)`. The pod is
CPU-throttled essentially continuously by the same merge storm.

### 1.2 Node capacity — ample headroom

```bash
KUBECONFIG=$KC_NEBULA kubectl top nodes
KUBECONFIG=$KC_NEBULA kubectl describe node talos-uvh-gtj | sed -n '/Allocated resources/,/Events/p'
KUBECONFIG=$KC_NEBULA kubectl get node talos-uvh-gtj -o jsonpath='{.status.allocatable.memory}'
```

| | value |
|---|---|
| `talos-uvh-gtj` allocatable memory | **32,069,948 Ki ≈ 30.6 GiB** |
| current actual usage | **13,111 Mi (41%)** |
| memory **requests** on the node | **12,750 Mi (40%)** |
| memory **limits** on the node | 28,096 Mi (89%, overcommitted — expected) |
| CPU limits | 16,480m (103%, overcommitted) |

Raising the activity CH limit from 3 Gi to 6–8 Gi is comfortably affordable on requests
(the pod requests only 512 Mi and requests are what scheduling honours). It does push
node limit-overcommit higher, which is already the norm here.

### 1.3 Table facts — `activity.events` is trivially small

```bash
curl -s --user "default:$PW" --data-binary "
SELECT sum(rows), formatReadableSize(sum(bytes_on_disk)),
       formatReadableSize(sum(data_uncompressed_bytes)),
       round(sum(data_uncompressed_bytes)/sum(data_compressed_bytes),2), count()
FROM system.parts WHERE database='activity' AND table='events' AND active FORMAT TSV" http://10.42.0.10:30123/
```

**479,221 rows · 27.09 MiB on disk · 27.07 MiB compressed · 161.08 MiB uncompressed ·
compression 5.95× · 18 active parts.**

Per partition:

| partition | rows | parts | on disk | uncompressed |
|---|---|---|---|---|
| 202604 | 15 | 2 | 4.11 KiB | 3.89 KiB |
| 202605 | 447 | 1 | 30.91 KiB | 156.74 KiB |
| 202606 | 56,951 | 2 | 1.23 MiB | 8.98 MiB |
| 202607 | 395,384 | 6 | 18.14 MiB | 118.66 MiB |
| 202608 | 26,428 | 8 | 7.68 MiB | 33.28 MiB |

Part counts are healthy (18 active parts total). Per column (`system.parts_columns`):

| column | compressed | uncompressed | ratio |
|---|---|---|---|
| **payload** | 13.68 MiB | **96.85 MiB** | 7.1 |
| **text** | 9.67 MiB | 32.58 MiB | 3.4 |
| cwd | 94.47 KiB | 5.16 MiB | 55.9 |
| session | 327.04 KiB | 3.40 MiB | 10.7 |
| ts | 754.30 KiB | 3.17 MiB | 4.3 |
| app | 223.91 KiB | 3.08 MiB | 14.1 |
| everything else | < 2 MiB each | | |

`payload` + `text` = **86% of uncompressed bytes**, so yes they dominate — but the whole
table is 161 MiB, so "dominant" here means 129 MiB, not gigabytes. Codecs are already
`ZSTD(3)` on both and `Delta(8), ZSTD(1)` on `ts`.

**TTL:** the table's declared TTL is `toDateTime(ts) + toIntervalDay(180)` — matches the
180d the skill documents. **It has never fired.** Earliest row is `2026-04-27 16:29:23`,
i.e. **98 days old**; first eviction would be ~2026-10-24.

```bash
curl … --data-binary "SELECT min(ts), max(ts), dateDiff('day', min(ts), now()) FROM activity.events FORMAT TSV"
# 2026-04-27 16:29:23.430   2026-08-03 00:50:45.042   98
```

### 1.4 The actual disk and memory hogs — `system.*` logs

```bash
curl … --data-binary "
SELECT database, table, sum(rows), formatReadableSize(sum(bytes_on_disk))
FROM system.parts WHERE active GROUP BY database, table ORDER BY sum(bytes_on_disk) DESC FORMAT TSV"
```

| database.table | rows | on disk |
|---|---|---|
| **system.trace_log** | **3,768,731,650** | **69.40 GiB** |
| **system.text_log** | **369,931,675** | **22.57 GiB** |
| system.metric_log | 3,400,212 | 1.15 GiB |
| system.asynchronous_metric_log | 778,882,298 | 834.23 MiB |
| system.latency_log | 3,415,277 | 57.10 MiB |
| system.part_log | 414,497 | 35.88 MiB |
| system.query_log | 219,461 | 32.44 MiB |
| system.processors_profile_log | 964,869 | 30.25 MiB |
| **activity.events** | 479,250 | **27.09 MiB** |

`activity.events` is **0.03%** of the data in its own database server.

On-disk confirmation (the PVC nominally requests **50 Gi**):

```bash
KUBECONFIG=$KC_NEBULA kubectl exec -n activity deploy/clickhouse -- \
  sh -c 'du -sh /var/lib/clickhouse/store /var/log/clickhouse-server; df -h /var/lib/clickhouse'
# 120.4G  /var/lib/clickhouse/store
# 1.5G    /var/log/clickhouse-server
# /dev/nvme0n1p1  931.1G  313.5G  617.5G  34%
```

**120.4 GB used against a 50 Gi PVC request.** `openebs-nvme-1tb` is node-local hostpath
with no quota enforcement, so it silently overruns onto the node NVMe — which is shared
with plausible's ClickHouse (170 GB). `DiskPressure=False` on all four nodes today, so
this is a latent risk, not a live one. *Inferred:* at the current growth rate this
becomes a node-level problem before it becomes an activity-telemetry problem.

**TTLs on the system log tables — measured, there are none** except
`processors_profile_log`:

```bash
curl … --data-binary "SELECT name, engine_full FROM system.tables WHERE database='system'
  AND name IN ('trace_log','text_log','metric_log','asynchronous_metric_log','query_log','latency_log','processors_profile_log') FORMAT TSV"
```

| table | TTL |
|---|---|
| trace_log | **none** |
| text_log | **none** |
| metric_log | **none** |
| asynchronous_metric_log | **none** |
| query_log | **none** |
| latency_log | **none** |
| processors_profile_log | `event_date + 30 DAY` |

### 1.5 Why they are that big

**`trace_log` — 99% memory-profiler traces.** Measured on today's partition:

```bash
curl … --data-binary "SELECT trace_type, count() FROM system.trace_log WHERE event_date = today()
  GROUP BY trace_type SETTINGS max_threads=1 FORMAT TSV"
# Real         35,220
# CPU           4,691
# Memory    1,276,978
# MemoryPeak 1,276,966
```

`memory_profiler_step = 4194304` (4 MiB, the image default) writes a stack trace every
4 MiB a memory tracker allocates. With 23 concurrent merges churning gigabytes, that is
a firehose.

**`text_log` — enabled at `trace` level by the stock image, and 60% of its content is
the merge storm describing itself:**

```bash
KUBECONFIG=$KC_NEBULA kubectl exec -n activity deploy/clickhouse -- \
  grep -n -A9 '<text_log>' /etc/clickhouse-server/config.xml
# <text_log> … <level>trace</level> </text_log>
# and <logger><level>trace</level>
```

Only `docker_related_config.xml` and the repo's `memory.xml` are in `config.d/` —
everything else is stock image config.

```bash
curl … --data-binary "SELECT logger_name, count() FROM system.text_log
  WHERE event_time > now()-interval 20 minute GROUP BY logger_name ORDER BY count() DESC LIMIT 12 SETTINGS max_threads=1 FORMAT TSV"
# MergeTreeSequentialSource                87,387
# MergeTask::PrepareStage                  13,694
# system.metric_log … (MergerMutator)      12,633
# MemoryTracker                             8,391
# DiskLocal                                 8,043
# …
# activity.events …                         1,061   <- the actual workload: 0.7%
```

Levels in that 20-minute window: `Debug` 115,510 · `Trace` 40,850 · `Error` 12,504.

### 1.6 The memory thieves, caught in the act

```bash
curl … --data-binary "SELECT database, table, count(), formatReadableSize(sum(memory_usage)),
  formatReadableSize(sum(total_size_bytes_compressed)) FROM system.merges GROUP BY database, table FORMAT TSV"
# system  metric_log  13   599.84 MiB   457.30 MiB
# system  text_log    10   391.48 MiB    19.33 GiB
# system  trace_log    2    36.21 MiB    43.58 GiB
```

**23–25 concurrent merges holding ~1.03 GiB of the 2.5 GiB budget, none of them on
`activity.events`.** Meanwhile:

```bash
curl … --data-binary "SELECT name, value FROM system.errors WHERE value>0 ORDER BY value DESC LIMIT 6 FORMAT TSV"
# MEMORY_LIMIT_EXCEEDED  74790
# ABORTED                    9
# SOCKET_TIMEOUT             8
```

74,790 code-241 events across `uptime() = 14,285s` ≈ **5.2 per second**. Almost all of
those are merges failing and being retried forever.

And the background pool is sized for a much bigger box:

```bash
curl … --data-binary "SELECT name, value FROM system.server_settings
  WHERE name IN ('background_pool_size','background_merges_mutations_concurrency_ratio','mark_cache_size','uncompressed_cache_size') FORMAT TSV"
# background_pool_size                            16
# background_merges_mutations_concurrency_ratio     2      -> 32 merge slots
# mark_cache_size                          5368709120      -> 5 GiB nominal
# uncompressed_cache_size                  8589934592      -> 8 GiB nominal
```

**32 merge slots and 13 GiB of nominal cache configured inside a 2-core / 2.5 GiB pod.**
The caches are clamped by `cache_size_to_ram_max_ratio=0.3` (the repo's fix), but the
merge pool is not clamped by anything.

I reproduced the reported failure **twice during this investigation**, on
`system.trace_log` and on `activity.events`:

```
Code: 241 … would use 2.50 GiB … current RSS: 2.13 GiB, maximum: 2.50 GiB.
OvercommitTracker decision: Query was selected to stop … While executing AggregatingTransform.
```

The second one was a `GROUP BY source, kind` over the **27 MiB** `activity.events` table.

### 1.7 Is the 202607 spike genuine growth or amplification? — **both, but mostly genuine**

```bash
curl … --data-binary "SELECT toYYYYMM(ts) m, kind, count() rows, uniqExact(session) sess,
  round(count()/greatest(uniqExact(session),1),1) amp FROM activity.events WHERE source='claude'
  GROUP BY m, kind ORDER BY m, kind SETTINGS max_threads=1 FORMAT TSV"
```

| month | kind | rows | distinct sessions | rows/session |
|---|---|---|---|---|
| 202605 | session-summary | 13 | 13 | **1.0** |
| 202606 | session-summary | 1,372 | 329 | **4.2** |
| **202607** | **session-summary** | **31,686** | **477** | **66.4** |
| 202608 | session-summary | 311 | 42 | 7.4 |
| 202607 | session-insight | 164 | 164 | 1.0 |
| 202607 | prompt | 12,319 | 461 | 26.7 *(genuine — many turns/session)* |
| 202607 | command | 280 | 174 | 1.6 |

**The amplification is real and it regressed sharply between June and July** — 1.0 →
4.2 → **66.4** rows per session. The `session_summary_rows_bounded` invariant (>24
rows/session/24h) is genuinely violated.

But it is **not** what drove the partition:

```bash
curl … --data-binary "SELECT toYYYYMM(ts) m, source, count() FROM activity.events
  GROUP BY m, source ORDER BY m, count() DESC SETTINGS max_threads=1 FORMAT TSV"
```

| source | 202606 | 202607 |
|---|---|---|
| keys | 22,050 | 103,906 |
| i3 | 15,301 | 102,267 |
| tmux | 4,616 | 63,432 |
| **browser-bridge** | — | **47,800** *(new source)* |
| claude | 5,383 | 44,449 |
| browser | 9,124 | 27,548 |
| **opencode** | — | **2,840** *(new)* |
| zsh | 477 | 1,841 |
| **tool** | — | **1,301** *(new)* |
| **total** | 56,951 | 395,384 |

`session-summary` is **31,686 of 395,384 July rows = 8.0%**. The other 92% is genuine
capture growth plus three sources that did not exist in June (`browser-bridge`,
`opencode`, `tool`). **Verdict: the 202607 spike is genuine growth; amplification is a
real but minor (8%) contributor — and neither matters for memory, because the whole
partition is 18.14 MiB.**

**Root cause of the amplification, located** —
`~/workspace/devrc/scripts/collector/claude/session-tailer.py:536-555`:

```python
def emit_decision(prev, sig, mtime, now, settle_s, interim_s):
    if prev and prev.get("sig") == sig:
        return (False, "unchanged")
    idle = now - mtime
    if settle_s <= 0 or idle >= settle_s:
        return (True, "settled")          # <-- NO rate limit, does not consult emitted_at
    ...
    if interim_s > 0 and (now - last) >= interim_s:
        return (True, "interim")          # <-- rate-limited to 4h
```

The `interim` path is rate-limited by `emitted_at`; **the `settled` path is not**. So a
long-lived `claude --resume` session that is worked in bursts separated by >20 minutes
emits **one full rollup per burst, forever**. A many-day resumed session with ~570 such
bursts produces the observed max of 572 rows. Each emit also re-reads and re-summarises
the *whole* transcript, so payload grows with every repeat.

### 1.8 Is `ORDER BY` / partitioning making `payload` scans worse? — **No.**

```bash
curl … --data-binary "EXPLAIN indexes=1 SELECT count() FROM activity.events WHERE kind='session-summary' FORMAT TSV"
# PrimaryKey  Condition: true   Parts: 19/19   Granules: 74/74
```

`ORDER BY (host, source, ts)` means a filter on `kind` or `session` prunes nothing — but
**the entire table is 74 granules**. There is nothing to prune. Monthly partitioning is
fine (5 partitions, 18 active parts). Codecs are already appropriate.

*Inferred, not measured:* if the table were 100× bigger, moving `kind` into the sort key
would matter. At 27 MiB it is not worth a migration.

---

## 2. Options

### Option A — Stop the `system.*` log flood and reclaim the space **(the actual fix)**

**What changes.** Two halves, deliberately separated by blast radius:

**A1 — cluster-side, no Flux, immediate.** `TRUNCATE` the runaway system log tables. Frees
~92 GiB and collapses the merge queue within minutes.

**A2 — Flux commit.** A new `config.d/logs.xml` ConfigMap that (a) drops the logger from
`trace` to `warning`, (b) removes `text_log` entirely, (c) disables the memory profiler,
(d) puts short TTLs on the remaining log tables, and (e) shrinks the merge pool from 32
slots to something a 2-core pod can serve. Without A2, A1 regrows in ~4 weeks.

**Blast radius.** Confined to the `activity` namespace. A2 restarts the ClickHouse pod
(`strategy: Recreate` — a ~30–60s gap during which the collector buffers to its spool and
retries; that path is designed for it). Losing `text_log`/`trace_log` costs you
ClickHouse *self*-diagnostics; `journalctl`-equivalent stderr logs and `query_log` remain.

**Reversibility.** A2 is **reversible** — revert the commit, Flux re-reconciles. A1 is
**irreversible but worthless to keep**: it destroys 92 GiB of ClickHouse's own profiler
traces and debug logs. **Zero telemetry rows are affected.**

**Repo.** A1 = cluster-side SQL only. A2 = `homelab-talos` Flux commit (**commit = deploy**).

### Option B — Raise the memory (and CPU) limits

**What changes.** `clickhouse.yaml` `limits.memory` 3 Gi → 6 Gi, `limits.cpu` 2 → 4;
`configmap-memory.yaml` `max_server_memory_usage` 2.5 GiB → 5 GiB.

**Blast radius.** Node `talos-uvh-gtj`: memory requests unchanged unless you also raise
`requests` (currently 512 Mi); node limit-overcommit goes 89% → ~99%. Actual node usage
is 41%, so there is real headroom. Pod restart.

**Reversibility.** **Reversible** — one commit revert.

**Repo.** `homelab-talos` Flux commit.

**Honest assessment.** This buys headroom but **does not fix anything**. The merge storm
is unbounded — 3.77 billion trace rows will happily consume 5 GiB too, and a bigger cache
budget means a bigger page-cache footprint fighting the cgroup watcher. Doing B *without*
A is buying a bigger bucket for a running tap. Doing B *after* A is cheap insurance and
lets you drop the awkward `cache_size_to_ram_max_ratio=0.3` workaround.

### Option C — Fix the amplification at the emitter (devrc)

**What changes.** `scripts/collector/claude/session-tailer.py` — rate-limit the `settled`
branch of `emit_decision()` the same way `interim` already is. A minimum re-emit interval
of 60 min directly enforces the documented `session_summary_rows_bounded` (>24
rows/session/24h) invariant.

**Blast radius.** devrc only. Ships via `scripts/ship.sh`; `X-Restart-Triggers` restarts
the daemons. Worst case a rollup is up to an hour staler than today. The read contract is
already `argMax(…, ingested_at)` per session, so fewer rows changes no query result.

**Reversibility.** **Reversible** — revert the commit and re-ship.

**Repo.** `devrc` (PR → main → `ship.sh`). **Separate blast radius from A/B entirely.**

**Honest assessment.** This is correct-and-worth-doing but it **has zero effect on the
memory ceiling**. It removes ~26 MB of duplicated payload from a 27 MiB table. Do it
because the invariant is being violated and the data is dirty, not because it buys
headroom. Per the repo's standing default this warrants a subagent, a test for
`emit_decision()` (which is already pure and clock-injectable — cheap to test), and a PR.

### Option D — Query-side hardening (devrc)

**What changes.** `scripts/validation/chquery.py` sends explicit
`max_threads=1, max_memory_usage=<256MiB>` and retries code-241 with backoff.

**Blast radius.** devrc only; affects `insights.py`, `activity-scan.py`,
`initiative-scan.py`, `validate.py`.

**Reversibility.** **Reversible.**

**Repo.** `devrc`.

**Honest assessment.** A stopgap that makes analysis *survivable* while the server is
sick, and mildly good hygiene regardless. It cannot be a fix: the `OvercommitTracker`
kills whatever query it picks, and I measured a query being killed at 33.70 MiB. **Do not
ship this instead of A.**

### Option E (evaluated and rejected) — shrink `activity.events`

Shorter TTL, dropping duplicate `session-summary` rows, better codecs. **Rejected on
measurement:** the table is 27.09 MiB, the 180d TTL has never fired (data is 98 days
old), and the codecs are already ZSTD(3). Maximum conceivable saving ≈ 20 MiB against a
120 GB store. It would also be the **only** option that loses telemetry data. Not worth
considering.

---

## 3. Recommendation

**Do A1 first, then A2, then C. Hold B in reserve; skip D and E.**

Reasoning:

1. **A1 is the cheapest reversible-in-effect step and the highest leverage.** One SQL
   statement, no commit, no restart, no telemetry touched, frees 92 GiB and drains the
   merge queue that is holding ~1 GiB of a 2.5 GiB budget. You will know within ten
   minutes whether the code-241s stop. If it does not help, you have learned that cheaply
   and nothing is lost.
2. **A2 makes it stick.** A1 alone regrows: 3.77 B trace rows accumulated in ~5 weeks.
3. **B does not distinguish "the server has more memory" from "the queries are correct".**
   Both statements are separately true here: the server is genuinely under-resourced for
   32 merge slots, **and** the queries were always correct — they needed 5–34 MiB. Raising
   the limit would mask the merge storm for a while and leave a 120 GB store growing on a
   node-local hostpath with no quota. Take B *after* A, as headroom, not as the fix.
4. **C is a real bug in a different repo** with a different blast radius, worth its own PR,
   and it should not be bundled with any of the above or credited with fixing memory.

### What I would do FIRST — the cheapest reversible step

```bash
KUBECONFIG=$KC_NEBULA kubectl exec -n activity deploy/clickhouse -- \
  clickhouse-client --user default --password "$PW" --query "TRUNCATE TABLE system.trace_log"
```

One table, 69.40 GiB, 100% ClickHouse's own memory-profiler stack traces, zero telemetry
value. Then wait ~5 minutes and re-read `system.errors` and `system.merges`.

---

## 4. Staged commands and diffs

Get the admin password into `$PW` once (it is never echoed):

```bash
export PW=$(nix-shell -p sops --run 'SOPS_AGE_KEY_FILE=/home/zach/workspace/homelab-talos/.secrets/age.key \
  sops -d --extract '"'"'["stringData"]["admin-password"]'"'"' \
  /home/zach/workspace/homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml')
export KUBECONFIG=$KC_NEBULA
ch() { kubectl exec -n activity deploy/clickhouse -- clickhouse-client --user default --password "$PW" --query "$1"; }
```

### Step 1 — A1: reclaim (cluster-side, no Flux)

```bash
# Baseline BEFORE, so you can prove the change did something:
ch "SELECT name, value FROM system.errors WHERE name='MEMORY_LIMIT_EXCEEDED'"
ch "SELECT count(), formatReadableSize(sum(memory_usage)) FROM system.merges"
kubectl exec -n activity deploy/clickhouse -- du -sh /var/lib/clickhouse/store

# Reclaim, biggest first:
ch "TRUNCATE TABLE system.trace_log"          # 69.40 GiB
ch "TRUNCATE TABLE system.text_log"           # 22.57 GiB
ch "TRUNCATE TABLE system.metric_log"         #  1.15 GiB
ch "TRUNCATE TABLE system.asynchronous_metric_log"   # 834 MiB

# Verify AFTER (give it ~5 min; the error counter is cumulative since start,
# so read the DELTA, not the absolute):
ch "SELECT database, table, count(), formatReadableSize(sum(memory_usage)) FROM system.merges GROUP BY database, table"
ch "SELECT name, value FROM system.errors WHERE name='MEMORY_LIMIT_EXCEEDED'"
kubectl exec -n activity deploy/clickhouse -- du -sh /var/lib/clickhouse/store

# The real acceptance test — run the query that was failing, repeatedly:
for i in 1 2 3 4 5; do
  ch "SELECT source, kind, count(), sum(length(payload)) FROM activity.events GROUP BY source, kind FORMAT Null" \
    && echo "run $i ok" || echo "run $i FAILED"
done
```

### Step 2 — A2: make it permanent (Flux commit, `homelab-talos`, branch `trunk` = deploy)

New file `clusters/homelab/apps/activity/configmap-logs.yaml`:

```yaml
---
# Bound ClickHouse's OWN observability. Measured 2026-08-02: with the stock image
# config (logger level=trace, text_log at trace, memory profiler at 4 MiB, 32 merge
# slots) system.trace_log reached 69.40 GiB / 3.77e9 rows and system.text_log 22.57
# GiB / 3.70e8 rows inside a 2.5 GiB memory budget. 23 concurrent merges of those
# tables held ~1.03 GiB of the budget and the server logged 74,790
# MEMORY_LIMIT_EXCEEDED in 4h — starving real queries via the OvercommitTracker.
# activity.events itself is 27 MiB.
apiVersion: v1
kind: ConfigMap
metadata:
  name: clickhouse-logs-config
  namespace: activity
data:
  logs.xml: |
    <clickhouse>
        <!-- stock image ships trace; 60% of text_log was the merge storm describing itself -->
        <logger>
            <level>warning</level>
        </logger>

        <!-- 99% of trace_log was trace_type=Memory/MemoryPeak from the 4 MiB
             memory-profiler step. Nothing here consumes profiler traces. -->
        <text_log remove="remove"/>
        <trace_log remove="remove"/>

        <!-- Keep the useful ones, but bounded. -->
        <metric_log>
            <ttl>event_date + INTERVAL 7 DAY DELETE</ttl>
        </metric_log>
        <asynchronous_metric_log>
            <ttl>event_date + INTERVAL 7 DAY DELETE</ttl>
        </asynchronous_metric_log>
        <query_log>
            <ttl>event_date + INTERVAL 14 DAY DELETE</ttl>
        </query_log>
        <part_log>
            <ttl>event_date + INTERVAL 14 DAY DELETE</ttl>
        </part_log>
        <latency_log remove="remove"/>
        <processors_profile_log remove="remove"/>

        <!-- 2-core pod: 16*2 = 32 concurrent merge slots was ~13x oversubscribed. -->
        <background_pool_size>4</background_pool_size>
        <background_merges_mutations_concurrency_ratio>2</background_merges_mutations_concurrency_ratio>
    </clickhouse>
```

Optionally also kill the memory profiler at the profile level (belt and braces; only
matters if you keep `trace_log`) — `users.d` via the same mount pattern, or simply rely on
`<trace_log remove="remove"/>` above.

Wire it in — `clusters/homelab/apps/activity/kustomization.yaml`:

```diff
 resources:
   - namespace.yaml
   - secrets.enc.yaml
   - configmap.yaml
   - configmap-memory.yaml
+  - configmap-logs.yaml
   - clickhouse.yaml
   - nodeport.yaml
```

`clusters/homelab/apps/activity/clickhouse.yaml`:

```diff
             - name: memory-config
               mountPath: /etc/clickhouse-server/config.d/memory.xml
               subPath: memory.xml
+            - name: logs-config
+              mountPath: /etc/clickhouse-server/config.d/logs.xml
+              subPath: logs.xml
@@
         - name: memory-config
           configMap:
             name: clickhouse-memory-config
+        - name: logs-config
+          configMap:
+            name: clickhouse-logs-config
```

Apply:

```bash
git -C $HOMELAB checkout -b activity-ch-log-bounds origin/trunk   # or a worktree
# … edit the three files …
git -C $HOMELAB add clusters/homelab/apps/activity/configmap-logs.yaml \
                    clusters/homelab/apps/activity/kustomization.yaml \
                    clusters/homelab/apps/activity/clickhouse.yaml
git -C $HOMELAB commit -m "activity: bound ClickHouse's own system logs (trace_log hit 69 GiB inside a 2.5 GiB budget)"
# open a PR, merge to trunk (commit = deploy), then:
KUBECONFIG=$KC_NEBULA flux reconcile kustomization apps --with-source

# VERIFY (do not stop at "rollout succeeded"):
KUBECONFIG=$KC_NEBULA kubectl rollout status -n activity deploy/clickhouse
ch "SELECT name, value FROM system.server_settings WHERE name='background_pool_size'"      # expect 4
ch "SELECT count() FROM system.tables WHERE database='system' AND name='text_log'"          # expect 0 new writes
ch "SELECT database, table, formatReadableSize(sum(bytes_on_disk)) FROM system.parts WHERE active GROUP BY database, table ORDER BY 3 DESC"
# and re-run the 5x acceptance loop from Step 1.
```

🔴 **Two things I could NOT verify and you must check on the live pod, not from this doc:**
1. `remove="remove"` is the documented ClickHouse idiom for deleting a `config.d` section,
   but I did **not** test it against 25.7.8.71. If the pod fails to start, the rollback is
   below and takes one revert.
2. `<ttl>` inside a `*_log` block is applied when ClickHouse **creates** the table. For
   tables that already exist, you likely need an explicit
   `ALTER TABLE system.metric_log MODIFY TTL event_date + INTERVAL 7 DAY` — confirm with
   `SELECT engine_full FROM system.tables WHERE database='system' AND name='metric_log'`
   after the restart and issue the ALTERs if the TTL is absent.

### Step 3 — C: fix the emitter (devrc, separate PR)

Sketch of the change to `scripts/collector/claude/session-tailer.py`:

```diff
+# Minimum interval between SETTLED re-emits for one session. The settle gate alone
+# lets a bursty multi-day `claude --resume` emit one full rollup per work burst;
+# measured 2026-08-02, July averaged 66.4 session-summary rows per session
+# (31,686 rows / 477 sessions), violating validation/invariants.py's
+# session_summary_rows_bounded (>24 rows/session/24h). 60 min enforces that bound.
+DEFAULT_RESETTLE_MINUTES = 60.0
+
 def emit_decision(prev, sig, mtime, now, settle_s, interim_s):
     if prev and prev.get("sig") == sig:
         return (False, "unchanged")
     idle = now - mtime
     if settle_s <= 0 or idle >= settle_s:
-        return (True, "settled")
+        last = prev.get("emitted_at") if prev else None
+        if not isinstance(last, (int, float)) or (now - last) >= resettle_s:
+            return (True, "settled")
+        return (False, "settled-throttled")
```

Because `emit_decision()` is already pure with an injected clock, the regression test is
cheap — and it must be shown **red at the pre-change ref**: assert that a transcript
settling twice within `resettle_s` emits once, and that today's code emits twice.

Ship: PR → main → `~/workspace/devrc/scripts/ship.sh`. Verify with

```bash
curl … --data-binary "SELECT count()/uniqExact(session) FROM activity.events
  WHERE source='claude' AND kind='session-summary' AND ts > now() - interval 3 day FORMAT TSV"
```

after 3 days — it should trend toward ≤ 24, not be asserted on day one.

### Step 4 (optional) — B: headroom

`clusters/homelab/apps/activity/clickhouse.yaml`:

```diff
           resources:
             requests:
               cpu: "100m"
-              memory: "512Mi"
+              memory: "1Gi"
             limits:
-              cpu: "2000m"
-              memory: "3Gi"
+              cpu: "4000m"
+              memory: "6Gi"
```

`configmap-memory.yaml`:

```diff
-        <max_server_memory_usage>2684354560</max_server_memory_usage>
+        <max_server_memory_usage>5368709120</max_server_memory_usage>
```

Node check before committing:

```bash
KUBECONFIG=$KC_NEBULA kubectl describe node talos-uvh-gtj | sed -n '/Allocated resources/,/Events/p'
# memory requests must stay comfortably under 30.6 GiB allocatable (12,750 Mi today)
```

---

## 5. Rollback paths

| Option | Rollback | Cost |
|---|---|---|
| **A1** (TRUNCATE) | **None — irreversible.** The truncated data is ClickHouse's own profiler traces and debug log lines. It is not backed up, and nothing consumes it. If you want a safety net, `SELECT count() FROM system.trace_log WHERE event_date >= today()-1` first, or `ALTER TABLE system.trace_log DROP PARTITION` month-by-month instead of TRUNCATE, keeping the current month. | Loss of ClickHouse self-diagnostics for past incidents. No telemetry loss. |
| **A2** (log-bounds ConfigMap) | `git revert` the trunk commit → `flux reconcile kustomization apps --with-source` → pod restarts with stock logging. If the pod fails to **start** (bad XML), Flux will not self-heal — revert the commit and, if you need it up immediately, `kubectl delete configmap -n activity clickhouse-logs-config` and let the deployment roll (the subPath mount will then fail, so revert the commit first). | ~1 min pod gap; collector spools and retries. |
| **B** (limits) | `git revert` → reconcile. | One pod restart. |
| **C** (emitter) | `git revert` in devrc → `ship.sh`. Already-emitted rows are unaffected; the read path dedupes on `argMax(…, ingested_at)`. | One switch on both hosts. |
| **D** (query settings) | `git revert` in devrc. | None. |

---

## 6. Honest risks

- **Does any option lose telemetry data? Only Option E, which I rejected.** A1/A2 delete
  ClickHouse's *own* `system.*` logs — `activity.events` is untouched by every recommended
  step. C changes only *future* emission volume of a row kind whose read contract already
  dedupes.
- **Is the deleted data recoverable? No, and it does not need to be.** There is no backup
  of `system.trace_log`; there is also no consumer. If you ever want the diagnostics, keep
  `query_log` and `part_log` (Step 2 does) — those are the ones that answer "why was this
  query slow".
- **A2 makes the server quieter, which is a real trade.** Dropping the logger to `warning`
  and removing `text_log`/`trace_log` means a future ClickHouse pathology is harder to
  debug from inside the server. Mitigation: the container's stderr still goes to
  `kubectl logs`, and `query_log`/`part_log`/`system.errors` survive.
- **I have not verified `remove="remove"` on 25.7.8.71, and I have not verified that
  `<ttl>` applies to pre-existing system tables.** Both are flagged inline in Step 2 with
  the check that settles them. Do not treat the pod rolling out as verification — run the
  5× acceptance loop.
- **A1 will not instantly zero the `MEMORY_LIMIT_EXCEEDED` counter.** `system.errors` is
  cumulative since server start. Read the **delta** over a few minutes, or restart the pod
  after A2 and read it fresh. *Inferred:* the merge queue should drain within minutes of
  the truncate, but I have not observed a truncate on this instance.
- **The 120.4 GB store on a nominally-50Gi node-local hostpath PVC is a separate latent
  problem** that A1/A2 resolve as a side effect. It shares `talos-uvh-gtj`'s NVMe with
  plausible's 170 GB ClickHouse; 313.5 G of 931 G used, `DiskPressure=False` on all nodes
  today. Worth a Prometheus alert on `openebs-nvme-1tb` free space regardless of what you
  choose here.
- **CPU is at 1992m of a 2000m limit.** I did not investigate whether that persists after
  A1 — *inferred* that it is the merge storm and will drop, but that is an inference, and
  it is the second thing to re-measure after Step 1.
- **The `session_summary_rows_bounded` invariant is currently failing and will keep
  failing until C ships.** If `validate.py` is being read as a health signal, it is
  currently reporting a true failure that is unrelated to the memory incident. Do not let
  fixing one make you believe you fixed the other.
