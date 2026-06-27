# activity-validation-harness

A **deterministic validation harness** for the personal activity-telemetry
pipeline. It proves two things:

1. the data captured into ClickHouse `activity.events` is faithful, and
2. the Grafana dashboard's queries compute correct values.

It does so without trusting the (thin, freshly-started) historical data: a
**controlled replay** writes a scripted burst with KNOWN ground truth, tagged by
a unique run-id, and the assertions compare ClickHouse's answer to that known
value over the run-id scope.

```
replay.py ──emit──► spool ──collector──► activity.events
   │ (records known counts/switches/deep-work/hour)
   └──► /tmp/replay-ground-truth.json
                                            ▲
assert_queries.py ──dashboard queries, scoped to run-id──┘  (== ground truth?)

invariants.py   — SQL sanity battery over ALL rows (PASS/FAIL table)
reconcile.py    — diff each source vs an independent record
validate.py     — runner (invariants + reconcile always; replay+assert with --replay)
```

## Components
- **`chquery.py`** — ClickHouse HTTP read client (creds from env, password never
  hardcoded) + the EXACT dashboard query builders (scoped + reduced to a scalar)
  + pure-Python re-implementations of switch-count, deep-work gaps-and-islands,
  and hour-of-day bucketing.
- **`replay.py`** — emits N shell commands (via the real `emit`), M browser navs
  (with known scroll metrics), K app-focus switches with a known longest gap, and
  (only with `$DISPLAY`) synthetic xdotool keystrokes. Records ground truth to
  JSON. Events are tagged `session=vrun-...`.
- **`assert_queries.py`** — runs the dashboard queries scoped to the run-id and
  asserts each equals the replay's expected value (counts, switches, deep-work,
  **timezone hour-bucket**).
- **`invariants.py`** — SQL sanity checks over all rows: no future ts (with a
  tz-slack window — see below), `duration_ms >= 0`, `duration_ms` within the 24h
  garbage cap, only expected host/source values, ts not clock-skewed-ancient,
  and the **`derived_attention_consistent`** check (see *Retired metric* below).
  Surfaces ingestion lag as a collector-health proxy.

### Retired metric: browser extension `active_ms`
The browser extension's per-page `active_ms` was **structurally wrong on the i3
host** and has been **retired** (no longer trusted or displayed):
`chrome.idle` measures *system-wide* input — so typing in the terminal kept the
browser counted as "active" — and `chrome.windows.onFocusChanged` blur is
unreliable on i3. It counted time-spent-in-OTHER-apps as browser engagement (a
10-min stretch focused entirely on Alacritty logged as ~60 min of Brave
"active"; 2026-06-27 11:00–12:00 UTC read ~144 min "active" against only 12.4 min
of actual i3 Brave focus). Because the metric is **retired, not hidden**, its two
guards — `active_ms_capped` and `per_host_hour_active_cap` — and their constants
(`ACTIVE_MS_CAP`, `ACTIVE_CAP_WINDOW_HOURS`) were **removed**: there is nothing
to guard once the metric isn't trusted/shown.

**Replacement — true per-domain browser attention (i3-derived):** computed
downstream by intersecting **i3 Brave-focused intervals** (the OS-level truth of
when the browser actually had focus; dwell-capped at 30 min, matching the
dashboard's i3-dwell panels) with the **active-tab domain timeline** from
browser `nav` events (`domain(text)` from each nav's ts to the next nav's ts),
summing the overlap per domain. Brave only (the extension runs only in Brave;
Chromium focus has no domain data → excluded); laptop-only. This powers the
dashboard panel **"Browser attention by domain (i3-derived, s)"**.

The new **`derived_attention_consistent`** invariant is its deterministic guard:
over a trailing 48h window it asserts the derived metric's two structural bounds
(which the broken `active_ms` violated): (1) sum-per-domain attention ≤ total i3
Brave dwell (+2% tol) — an intersection is a subset of the i3 Brave intervals —
and (2) no single domain exceeds wall-clock. Vacuously PASSes on a host with no
GUI data. The extension no longer emits `active_ms` or focus/idle events at all
(removed in the browser-ext cleanup; takes effect after an operator Brave reload),
so the replay/assert path no longer scripts or checks browser `active_ms`.
- **`reconcile.py`** — for a recent window, diffs each source against an
  independent existing record: zsh ↔ `~/.zsh_history`, browser ↔ Chrome/Brave
  `History` sqlite (read from a copy — the live DB is locked), tmux ↔
  `~/.tmux/tasks` + `~/.tmux/activity`, claude ↔ `~/.claude/projects/**/*.jsonl`.
  A source with no data reports "skipped", never a failure.
- **`refsources.py`** — the independent reference readers (pure parsers).
- **`tests/`** — pytest unit coverage for ALL the pure logic (no live CH).

## Credentials (reader, via SOPS — never hardcoded)
The harness reads `CLICKHOUSE_URL` / `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD`
from the environment. Pull the reader password from SOPS:

```sh
git -C ~/workspace/homelab-talos show \
  origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml > /tmp/x.yaml
export CLICKHOUSE_PASSWORD=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
  sops -d --extract '["stringData"]["reader-password"]' /tmp/x.yaml)
export CLICKHOUSE_USER=activity_reader
```

## Running

### On the workbench (LAN)
```sh
export CLICKHOUSE_URL=http://192.168.50.94:30123
# ... CLICKHOUSE_USER / CLICKHOUSE_PASSWORD as above ...
python3 scripts/validation/validate.py            # invariants + reconcile only
python3 scripts/validation/validate.py --replay   # also write a burst + assert
```

### On the laptop (nebula)
```sh
export CLICKHOUSE_URL=http://10.42.0.10:30123
export ACTIVITY_HOST=laptop
python3 scripts/validation/validate.py --replay
```

**Keystroke replay needs an X session.** On a headless host (`$DISPLAY` unset)
the synthetic-keystroke part is SKIPPED with a logged note; run `--replay` on the
**laptop's X session** to exercise the keylog path end-to-end.

### Tests
```sh
nix-shell -p python312Packages.pytest --run "pytest scripts/validation/tests -q"
```

## Replay cleanup
The reader cannot `DELETE`. Replay events are tagged `session = 'vrun-...'`, so:
- isolate one run:  `WHERE session = '<run_id>'`
- exclude ALL replay data from dashboards/queries: `WHERE session NOT LIKE 'vrun-%'`

A privileged user can purge them with
`ALTER TABLE activity.events DELETE WHERE session LIKE 'vrun-%'` (not run by the
harness — it never requires write creds).

## Timezone (load-bearing finding)
`emit`/`spool_emit` stamp `ts` with the host's **local wall clock**
(`date +"%Y-%m-%d %H:%M:%S.%3N"`), and the `ts` column is a bare `DateTime64(3)`
with **no column timezone**, while the ClickHouse server runs in **UTC**.

- `toHour(ts)` / `toStartOfHour(ts)` therefore read the LOCAL hour off the stored
  wall-clock value, so the heatmap buckets to **local** hour-of-day — confirmed
  by the replay (events emitted at local 08:10 land in bucket 8, not UTC 13).
- BUT `now()` / `today()` are UTC. Any `ts <= now()` comparison mixes a local
  wall-clock against a UTC clock — a "future ts" check would never flag a
  west-of-UTC host and could false-positive an east-of-UTC one. The
  `no_future_ts` invariant therefore allows a `FUTURE_SLACK_HOURS` window and
  the ingestion-lag readout notes the local-vs-UTC offset is baked in.
- `$__timeFilter(ts)` in Grafana similarly compares stored local wall-clock
  against the dashboard's (UTC-derived) range bounds, so a Grafana range of
  "last 1h" will be offset by the local-UTC delta. Worth keeping in mind when
  reading the live dashboard.
