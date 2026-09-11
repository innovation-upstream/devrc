# Handoff: telemetry-deadman-hardening — 2026-08-11

## Goal
Stop `browser-bridge` false-alarming as DEAD in the activity deadman. The ask was
"raise the budget"; measurement showed no budget can work, so the fix was to give
the source a cadence. That exposed two further defects, each shipped as its own PR.

## State now
- **Branch:** `main`. No open PRs from this work. Base clone was 1 behind
  `origin/main` at handoff time (`ab99245` #411, unrelated) — `git -C ~/workspace/devrc
  merge --ff-only origin/main`.
- **Nothing is in flight.** All three PRs merged, deployed to both hosts via
  `scripts/ship.sh`, and verified live.

| PR | merge | what |
|---|---|---|
| #388 | `3acd041` | 900s liveness heartbeat in `scripts/browser-bridge/server.py` (`HEARTBEAT_INTERVAL_S`, `kind="heartbeat"`) |
| #393 | `a67f795` | `PRESENCE_SOURCES` allowlist + `STATE_PRESENCE_STALLED` in `scripts/collector/deadman.py`; `bar-status-poll` consumer changes |
| #399 | `4c83cbd` | `scripts/testlib/nolaunch.py` + `scripts/tests/conftest.py` — the suite no longer acts on the live host |

**Verified live (not inferred):**
- Heartbeat cadence, 6h window, both hosts: 24 beats each, mean gap **exactly 900s**.
- `python3 scripts/collector/deadman.py` → `state: ok  evaluated=17  dead=0`, rc 0.
- Test-suite host safety, base-clone path, 3 hazard files, 50/50 green:
  **22 real launches before → 0 after** (7 `ddcutil`, 6 `openrgb`, 5 `notify-send`,
  4 `systemd-run`), measured with an interceptor whose positive control was watched.

## Open verification debt
Nothing is mid-diagnosis. These are claims that are **not yet verified**, recorded so
nobody assumes they are.

### The presence-stall detector has never fired on real unseeded data
- **What is unverified:** `STATE_PRESENCE_STALLED` (fires when a host keeps emitting
  >`PRESENCE_STALL_HOURS`=72 after its last human-driven row). Its **false-positive**
  rate is measured — zero across 169 hourly points, against a worst observed real
  stall of 39.8h (workbench) / 8.9h (laptop), so 72h is ~1.8×. Its **true-positive**
  behaviour on a real X-session crash is not: every observation is a seeded replay.
- **Next probe (opportunistic, not urgent):** next time X or the keylogger genuinely
  dies on a host, run `python3 scripts/collector/deadman.py --json` and confirm the
  verdict is `presence-stalled` naming that host, and that the `tlm` pill shows it.

### Budget tightening for `laptop/browser-bridge` is a prediction, not a measurement
- `budget_active_hours` was 25.0h right after deploy because the 14-day window still
  holds the pre-heartbeat sparse gaps. It **should** collapse toward the 2h floor as
  those age out (heartbeat p99 gap ≈ 3 buckets).
- **Next probe (~2026-08-25):** `python3 scripts/collector/deadman.py` and read
  `laptop/browser-bridge` — expect `budget_h ≈ 2.0`. If it is still >10h, the
  allowlist is excluding more active time than modelled; re-run the sweep in
  "How to verify".

### #393's trade is real and should be watched
- Presence-defined active time buys quiet nights at the cost of **slower true
  detections**: no pair got faster, 10 of 17 got slower. `workbench/tool` 36h→96h,
  `laptop/zsh` 24h→72h, and `workbench/browser-bridge` 12h→24h — which partially
  erodes the heartbeat #388 added one commit earlier.
- Not compensated on purpose (fixing it means retuning the heartbeat interval or the
  floor — a separate measured change). If a real death is ever noticed late, this is
  the first thing to look at.

### #399 left three gaps open ON PURPOSE (stated, not closed)
- `hazard_hits` is a **literal-name** scan: misses `"${N}-send"`, `"systemd" + "-run"`,
  wrapper functions. Absolute paths are caught.
- Suites **outside `scripts/tests`** are unprotected by construction (the conftest
  covers that directory only). The survey found nothing to protect there today.
- `systemctl` **read** verbs still exec the real binary by design (their output is
  branched on); mutating verbs are recorded and swallowed.

## Next steps (ranked)
1. **Nothing is required.** All three shipped and verified; no follow-up is blocking.
2. `git -C ~/workspace/devrc merge --ff-only origin/main` (base clone 1 behind).
3. ~2026-08-25: check the `laptop/browser-bridge` budget collapsed to the floor (above).
4. If the slower-detection trade bites, reconsider `PRESENCE_STALL_HOURS` or the
   heartbeat interval — both are single measured constants with their derivation
   named inline.

## Gotchas / decisions / dead-ends
- 🔴 **"Raise the budget" was measured and rejected**, don't re-derive it: silence was
  371 buckets vs a worst-ever 170; K=4 → 340, still red; the 48h cap buys ~2 days.
  Also rejected with numbers: **max-gap basing** (a past `workbench/keys` outage would
  inflate the most continuous source 2h→27h) and a **density/Bernoulli bound** (lands
  *below* the observed p99 on all 17 pairs — emissions are bursty).
- 🔴 **`kind="heartbeat"`, never `"cmd"`** — `session-analysis/adoption-scan.py` counts
  `source='browser-bridge' AND kind='cmd'` as the USAGE signal; heartbeats on `cmd`
  would report ~96 phantom browser-skill uses/day.
- 🔴 **`newest_event_age_minutes` cannot see a per-host presence blackout** — it is a
  global max across hosts and sources, so surviving agent rows pin it at ~1 min. An
  earlier docstring claimed it was the mitigation for that blind spot; it is not.
- **Two consumers, two different predicates, both live:** `deadman.PRESENCE_SOURCES`
  (who counts as the operator being present) and `deadman.MACHINE_CADENCE` +
  `cadence_predicate_sql` (what is machine-generated, imported by `scripts/agent-ops`
  for its freshness panel). Do **not** unify them — `claude`/`tool` are operator-driven
  for agent-ops but explicitly not presence for the deadman.
- **Harness traps that made instruments lie in this session** (all cost real time):
  `cp -a` carries `__pycache__` so a byte-length-identical mutant runs the ORIGINAL
  source and every mutant reads "survived" (use `python -B` + `PYTHONDONTWRITEBYTECODE=1`
  on a purged copy); pytest **collection order** can pre-arm a session autouse fixture
  and mask a mutant completely; a test resolving a sibling module through HOME-based
  `DEVRC_DIR` reads the **base clone**, not its branch; and a stub whose *output* a test
  branches on must emulate the real binary or it masks the discriminator.
- **The shared base clone has other sessions in it.** Mid-session another one switched
  the checkout off my branch and fast-forwarded `main`, silently discarding an edit.
  `git branch --show-current` immediately before any write; `git reflog` diagnoses it.

## How to verify
```bash
# 1. deadman is healthy on the new code
python3 ~/workspace/devrc/scripts/collector/deadman.py            # expect: state: ok ... dead=0, rc 0

# 2. the heartbeat is actually beating at 900s on BOTH hosts
python3 - <<'PY'
import os, sys; sys.path.insert(0, os.path.expanduser('~/workspace/devrc/scripts/collector'))
import deadman as D; cfg = D.resolve_config()
print(D.fetch_buckets(cfg['url'], cfg['user'], cfg['password'],
  "SELECT host, count() AS beats, round(dateDiff('second', min(ts), max(ts))/(count()-1)) AS mean_gap_s "
  "FROM activity.events WHERE source='browser-bridge' AND kind='heartbeat' "
  "AND ts > now() - INTERVAL 6 HOUR GROUP BY host ORDER BY host FORMAT TSV", 20.0))
PY
# expect mean_gap_s = 900 for laptop and workbench

# 3. the test suite no longer touches the host — POSITIVE-CONTROL the interceptor first
#    (shim scripts that log $TOAST_LOG and launch nothing, first on PATH)
#    then: pytest scripts/tests/test_rig_control.py test_monitor_blackout.py test_notifs.py
#    expect 50 passed and ZERO lines in $TOAST_LOG. A zero from an unproven
#    interceptor means nothing — make it record a known launch first.

# 4. authoritative gates
cd ~/workspace/devrc && nix build .#checks.x86_64-linux.pytests   # read TOTAL/RESULT, not rc
nix build .#checks.x86_64-linux.nodetests
```
