# ClickHouse A1 — executed 2026-08-03, and what it did NOT fix

Companion to `clickhouse-headroom-proposal-2026-08-02.md`. That file proposed;
this file records what was actually run and measured. Every number here came
from a command in this session.

## What was run

Admin creds via SOPS (`admin-password`), `KUBECONFIG=$KC_NEBULA`,
`kubectl exec -n activity deploy/clickhouse -- clickhouse-client`.

```
TRUNCATE TABLE system.text_log                   -> ok
TRUNCATE TABLE system.metric_log                 -> ok
TRUNCATE TABLE system.asynchronous_metric_log    -> ok
TRUNCATE TABLE system.trace_log                  -> FAILED, code 359
```

🔴 **`TRUNCATE system.trace_log` does not work as written in the proposal.**
ClickHouse refuses it:

```
Code: 359. Table or Partition in system.trace_log was not dropped.
Reason:
1. Size (74.67 GB) is greater than max_[table/partition]_size_to_drop (50.00 GB)
2. File '/var/lib/clickhouse/flags/force_drop_table' intended to force DROP doesn't exist
```

Partition-by-partition was tried next and is **also insufficient on its own** —
the partitions were `202606` 12.11 GiB, `202607` **54.07 GiB**, `202608` 3.36 GiB,
so `202607` alone still exceeds the 50 GB guard. What worked:

```
ALTER TABLE system.trace_log DROP PARTITION 202606     # ok
ALTER TABLE system.trace_log DROP PARTITION 202608     # ok
kubectl exec … -- touch /var/lib/clickhouse/flags/force_drop_table
ALTER TABLE system.trace_log DROP PARTITION 202607     # ok
```

The force flag is **consumed by ClickHouse after one use** — `/var/lib/clickhouse/flags/`
was verified empty afterwards, so it does not linger as a standing hazard.

## Measured before → after

| metric | before | after |
|---|---|---|
| `/var/lib/clickhouse/store` | **112.4 GB** | **524.2 MB** |
| `system.trace_log` | 69.54 GiB | 15.15 KiB |
| `system.text_log` | 22.65 GiB | truncated |
| merges in flight | 20 holding 489.22 MiB | **0 holding 0 B** |
| server RSS | 2.49–2.50 GiB (at the 2.5 GiB ceiling) | **1.20 GiB** |
| `activity.events` | 482,454 rows | 482,565 rows (still ingesting — untouched) |

`MEMORY_LIMIT_EXCEEDED` was **113,378** immediately before the truncate (it was
74,790 when the proposal was written a few hours earlier — it was accelerating).
The counter is cumulative since server start, so read the delta, not the absolute.

## 🔴 What A1 did NOT fix — the failure MOVED, it did not vanish

`insights.py --days 14` after A1, from the **laptop** (nebula endpoint
`10.42.0.10:30123`): **3 of 6 runs still failed.**

The failure mode changed, which is the useful part:

| | before A1 | after A1 |
|---|---|---|
| exception | `Code: 241` memory limit exceeded | `Code: 209` **Timeout exceeded while writing to socket** |
| server RSS | 2.50 GiB | 1.20 GiB |

So the memory problem is genuinely resolved and a **different** problem was
underneath it.

**The discriminating control — same server, same code, same minute:**

| host | endpoint | result |
|---|---|---|
| workbench | `192.168.50.94:30123` (LAN) | **6/6 ok** |
| laptop | `10.42.0.10:30123` (nebula) | **3/6 ok** |

So the residue is the **nebula path**, not ClickHouse.

**Raising the client timeout makes it WORSE, so it is not slowness.** With
`CLICKHOUSE_HTTP_TIMEOUT=90` the laptop went to **1/6** (from 3/6 at the 15 s
default). Timings are bimodal: successful runs complete in **~400 ms**, failures
stall to the full timeout (90 s, 180 s) and then die. A longer timeout only buys
a longer wait before the same failure.

Bimodal success-or-stall on the larger response, over a tunnel, is the shape of
an **MTU blackhole** — small responses pass, large ones stall. That is a
hypothesis, **not** a measurement: it was not confirmed. To settle it, test the
nebula path with `ping -M do -s <size>` sweeps or compare `tracepath` MTU
against the tunnel's configured MTU. Do not treat the MTU explanation as
established until that is run.

## Status of the proposal's other options

- **A2** (Flux commit: bound logger level, drop `text_log`/`trace_log`, TTLs,
  `background_pool_size=4`) — **NOT done.** A1 alone regrows: 3.77 B trace rows
  accumulated in ~5 weeks, so expect ~4 weeks of headroom before this recurs.
  A2 is what makes A1 stick.
- **B** (raise memory limits) — correctly held in reserve. RSS is now 1.20 GiB
  against a 2.5 GiB ceiling, so there is no case for it today.
- **C** (emitter amplification fix at `scripts/collector/claude/session-tailer.py:545-546`)
  — still open, still worth doing; it was 8% of July rows, not the main driver.

## Lesson

The proposal's headline diagnosis was right and my own earlier one was wrong: I
had blamed `activity.events` volume and its `payload` column. `activity.events`
is **27 MiB**, 0.03% of the instance. But A1 being right did not make it
sufficient — a second, unrelated fault (the nebula path) was masked underneath
the first, and only showed up once the first was cleared. Fixing the loudest
cause revealed the next one rather than producing a green system.
