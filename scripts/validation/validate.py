#!/usr/bin/env python3
"""validate — runner for the activity-telemetry validation harness.

Always runs:
  * invariants  — SQL sanity battery over ALL of activity.events
  * reconcile   — cross-source diff vs independent records (skips empty sources)

With --replay it ALSO runs:
  * replay      — emit a scripted burst with known ground truth (WRITES events)
  * assert      — run the dashboard queries scoped to the run-id and assert

Requires CLICKHOUSE_URL / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD in the env (the
reader password comes from SOPS — see README). Exit code is non-zero if any
invariant or (when --replay) any assertion FAILs.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import invariants  # noqa: E402
import reconcile  # noqa: E402
from chquery import CHClient, CHConn  # noqa: E402


def main(argv=None) -> int:
    argv = argv or []
    do_replay = "--replay" in argv
    window_hours = 24.0
    for a in argv:
        if a.startswith("--window-hours="):
            window_hours = float(a.split("=", 1)[1])

    conn = CHConn.from_env()
    client = CHClient(conn)

    print("=" * 70)
    print(f"ACTIVITY VALIDATION HARNESS — {conn.fq_table} @ {conn.url}")
    print("=" * 70)

    overall_ok = True

    # --- Invariants (always).
    inv_results = invariants.run_invariants(client)
    print("\n[1] INVARIANT BATTERY")
    print(invariants.format_table(inv_results))
    print("\nCollector/ingestion state: " + invariants.collector_drop_state(client))
    inv_fail = [r for r in inv_results if not r.passed]
    if inv_fail:
        overall_ok = False

    # --- Reconciliation (always).
    print("\n[2] CROSS-SOURCE RECONCILIATION (last %.0fh)" % window_hours)
    rec_results = reconcile.run_reconcile(client, window_hours=window_hours)
    for r in rec_results:
        print("  " + r.line())
    rec_err = [r for r in rec_results if r.skipped and "error" in r.reason]
    if rec_err:
        overall_ok = False

    # --- Replay + assertions (opt-in; writes events).
    if do_replay:
        import replay  # noqa: E402
        import assert_queries  # noqa: E402
        print("\n[3] CONTROLLED REPLAY (writes tagged events)")
        gt = replay.run(replay.ReplayPlan(), Path("/tmp/replay-ground-truth.json"))
        print(f"  run_id={gt.run_id} host={gt.host}")
        for n in gt.notes:
            print("    note:", n)
        # Give the collector a moment to ship, then re-flush + assert with retry.
        import time as _t
        from dataclasses import asdict
        gtd = asdict(gt)
        results = []
        for attempt in range(6):
            results = assert_queries.assert_all(client, gtd)
            if all(r.passed for r in results):
                break
            _t.sleep(2)
            replay.flush_collector()
        print("\n[4] DASHBOARD-QUERY ASSERTIONS")
        print(assert_queries.format_table(results))
        if any(not r.passed for r in results):
            overall_ok = False
        print(f"\n  (replay events tagged session='{gt.run_id}'; "
              "exclude all with session NOT LIKE 'vrun-%')")

    print("\n" + "=" * 70)
    print("OVERALL: " + ("PASS" if overall_ok else "FAIL"))
    print("=" * 70)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
