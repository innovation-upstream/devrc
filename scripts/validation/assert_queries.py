#!/usr/bin/env python3
"""assert_queries — run the dashboard's queries scoped to a replay run and assert.

Loads a replay ground-truth JSON, runs the EXACT dashboard query logic (from
chquery.q_*) scoped to that run's run-id, and asserts each computed value equals
the replay's independently-derived expected value. Targets the high-risk
computations:

  * timezone        — events emitted at a known LOCAL time land in the expected
                      hour-of-day bucket (catches UTC-vs-local bugs).
  * active_ms sum   — equals the scripted dwell.
  * switch count    — lag/neighbour over app changes == K.
  * deep-work block — gaps-and-islands longest run == the scripted gap.
  * counts          — command count == N, nav count == M.

A run-scope (`session = '<run_id>'`) makes every assertion deterministic
regardless of other data in the table.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chquery  # noqa: E402
from chquery import CHClient, CHConn, run_scope  # noqa: E402


@dataclass
class Assertion:
    name: str
    expected: object
    actual: object
    passed: bool
    detail: str = ""


def assert_all(client: CHClient, gt: dict) -> list[Assertion]:
    run_id = gt["run_id"]
    where = run_scope(run_id)
    out: list[Assertion] = []

    def check(name, expected, sql, transform=lambda x: int(x or 0), detail=""):
        try:
            actual = transform(client.scalar(sql))
        except Exception as exc:
            out.append(Assertion(name, expected, None, False, f"query error: {exc}"))
            return
        out.append(Assertion(name, expected, actual, actual == expected, detail))

    check("command_count", gt["expected_command_count"],
          chquery.q_command_count(where))
    check("nav_count", gt["expected_nav_count"],
          chquery.q_nav_count(where))
    check("active_ms_sum", gt["expected_active_ms"],
          chquery.q_browser_active_ms(where))
    check("app_switches", gt["expected_switches"],
          chquery.q_app_switches(where))
    check("deep_work_block_ms", gt["expected_deep_work_ms"],
          chquery.q_longest_deep_work_ms(where))

    # Timezone / hour-of-day bucket: assert ALL replay events fall in the
    # expected local hour bucket (the burst is emitted within one hour).
    try:
        rows = client.rows(chquery.q_hour_histogram(where))
        hours = {int(r["hour"]): int(r["events"]) for r in rows}
        expected_hour = gt["expected_hour_bucket"]
        # Allow the burst to straddle an hour boundary: every populated bucket
        # must be the expected hour or its immediate successor.
        ok = all(h in (expected_hour, (expected_hour + 1) % 24) for h in hours)
        detail = f"buckets={hours} expected_hour={expected_hour}"
        out.append(Assertion("timezone_hour_bucket", expected_hour,
                             sorted(hours), ok, detail))
    except Exception as exc:
        out.append(Assertion("timezone_hour_bucket", gt["expected_hour_bucket"],
                             None, False, f"query error: {exc}"))

    return out


def format_table(results: list[Assertion]) -> str:
    width = max((len(r.name) for r in results), default=12)
    lines = ["", f"{'ASSERTION':<{width}}  RESULT  EXPECTED -> ACTUAL"]
    lines.append(f"{'-'*width}  ------  ------------------")
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        msg = f"{r.expected!r} -> {r.actual!r}"
        if r.detail:
            msg += f"  [{r.detail}]"
        lines.append(f"{r.name:<{width}}  {tag:<6}  {msg}")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = argv or []
    gt_path = Path("/tmp/replay-ground-truth.json")
    for a in argv:
        if a.startswith("--ground-truth="):
            gt_path = Path(a.split("=", 1)[1])
    if not gt_path.exists():
        print(f"ground-truth file not found: {gt_path} (run replay.py first)",
              file=sys.stderr)
        return 2
    gt = json.loads(gt_path.read_text())
    conn = CHConn.from_env()
    client = CHClient(conn)
    results = assert_all(client, gt)
    print(f"Assertions for replay run_id={gt['run_id']} @ {conn.url}")
    print(format_table(results))
    failed = [r for r in results if not r.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} assertions PASS"
          + (f"; {len(failed)} FAIL" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
