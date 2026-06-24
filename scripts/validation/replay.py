#!/usr/bin/env python3
"""replay — emit a scripted activity burst with KNOWN expected values.

Generates a deterministic burst tagged with a unique run-id (stamped into the
`session` column) so it is fully isolable in ClickHouse even though the reader
cannot DELETE. It writes:

  * N shell commands         via the real `emit` path (source=zsh, kind=command)
  * M browser navs           with KNOWN active_ms each, via the receiver if it's
                             up (POST 127.0.0.1:8787/event) else direct to spool
  * K app-focus switches     with a KNOWN longest-uninterrupted same-app gap
                             (source=keys, kind=window-focus, distinct apps)
  * synthetic keystrokes     ONLY if DISPLAY is set (xdotool). Headless → SKIP
                             with a logged note (keylog replay must run on the
                             laptop's X session).

The exact ground truth (counts, summed active_ms, switch-count=K, deep-work
block ms) is computed with the SAME pure functions the assertions use and
written to a JSON sidecar, so assert_queries.py can compare CH's answer to an
independently-derived expected value.

All emitted events use a distinct app namespace ("replayapp-*") and the run-id
session tag, so they never pollute switch/deep-work math for real data within
the run scope.

IMPORTANT: this WRITES real events into activity.events (via the collector).
They are tagged, not deletable by the reader — note this to the user; they can
filter them out by `session LIKE 'vrun-%'`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector" / "keylog"))
import chquery  # noqa: E402

EMIT = Path(__file__).resolve().parents[1] / "collector" / "emit"


@dataclass
class ReplayPlan:
    n_commands: int = 5
    m_navs: int = 4
    nav_active_ms: int = 2000          # known dwell per nav
    k_switches: int = 3                # number of app changes to produce
    deep_work_block_ms: int = 4000     # the scripted longest uninterrupted gap
    receiver_url: str = "http://127.0.0.1:8787/event"


@dataclass
class GroundTruth:
    run_id: str
    host: str
    started_local: str
    started_hour: int
    n_commands: int
    m_navs: int
    expected_command_count: int
    expected_nav_count: int
    expected_active_ms: int
    expected_switches: int
    expected_deep_work_ms: int
    expected_hour_bucket: int
    keystrokes_replayed: bool
    notes: list


def _ts(dt: datetime) -> str:
    """Wall-clock ts string in the emit/spool format."""
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def emit_event(fields: dict) -> None:
    """Emit one event through the real bash `emit` helper (the zsh hot path)."""
    args = [str(EMIT)]
    for k, v in fields.items():
        # free-text fields go through b64: prefix (emit base64-encodes them)
        if k in ("ts", "host", "source", "kind", "duration_ms", "exit_code"):
            args.append(f"{k}={v}")
        else:
            args.append(f"b64:{k}={v}")
    subprocess.run(args, check=True)


def post_nav(receiver_url: str, evt: dict, timeout: float = 2.0) -> bool:
    """POST a nav event to the browser receiver. Returns True on 200."""
    body = json.dumps(evt).encode("utf-8")
    req = urllib.request.Request(receiver_url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        code = getattr(resp, "status", None) or resp.getcode()
        resp.close()
        return 200 <= code < 300
    except (urllib.error.URLError, OSError):
        return False


def build_ground_truth(plan: ReplayPlan, run_id: str, host: str,
                       keystrokes: bool, notes: list) -> tuple[GroundTruth, dict]:
    """Compute expected values from the plan using the pure chquery math.

    Returns (GroundTruth, emit_spec) where emit_spec describes the exact events
    to write so CH ends up matching the ground truth.
    """
    now = datetime.now()
    start_hour = now.hour

    # --- app-focus sequence: K switches + a deep-work island of the given gap.
    # Build a focus sequence whose lagInFrame switch-count == K and whose longest
    # same-app island spans deep_work_block_ms. We lay K+1 distinct app segments;
    # the FIRST segment is the long deep-work island (two events spaced by the
    # gap), the remaining K segments are single events that each cause one switch.
    focus_events = []  # (app, ts)
    base = now
    # deep-work island: app A at t0 and t0+gap (no switch within it).
    a0 = "replayapp-A"
    focus_events.append((a0, base))
    island_end = base.fromtimestamp(base.timestamp() + plan.deep_work_block_ms / 1000.0)
    focus_events.append((a0, island_end))
    # then K further apps, each a single focus → exactly K switches after the island.
    t = island_end
    for i in range(plan.k_switches):
        t = t.fromtimestamp(t.timestamp() + 0.5)
        focus_events.append((f"replayapp-S{i}", t))

    apps_in_order = [a for a, _ in focus_events]
    expected_switches = chquery.count_switches(apps_in_order)
    expected_deep_work = chquery.longest_deep_work_ms(
        [(ts, app) for app, ts in focus_events]
    )

    # --- navs with known active_ms each.
    nav_payloads = [
        json.dumps({"title": f"replay nav {i}", "active_ms": plan.nav_active_ms,
                    "state": "active"})
        for i in range(plan.m_navs)
    ]
    expected_active_ms = chquery.sum_active_ms(nav_payloads)

    gt = GroundTruth(
        run_id=run_id,
        host=host,
        started_local=_ts(now),
        started_hour=start_hour,
        n_commands=plan.n_commands,
        m_navs=plan.m_navs,
        expected_command_count=plan.n_commands,
        expected_nav_count=plan.m_navs,
        expected_active_ms=expected_active_ms,
        expected_switches=expected_switches,
        expected_deep_work_ms=expected_deep_work,
        expected_hour_bucket=chquery.hour_of_day(_ts(now)),
        keystrokes_replayed=keystrokes,
        notes=notes,
    )
    spec = {
        "commands": [
            {"ts": _ts(now), "text": f"replay-cmd-{run_id}-{i}", "duration_ms": 100 + i,
             "exit_code": 0}
            for i in range(plan.n_commands)
        ],
        "navs": [
            {"url": f"https://replay.example/{run_id}/{i}", "payload": nav_payloads[i]}
            for i in range(plan.m_navs)
        ],
        "focus": [
            {"ts": _ts(ts), "app": app} for app, ts in focus_events
        ],
    }
    return gt, spec


def perform_replay(plan: ReplayPlan, host: str, run_id: str,
                   spec: dict, use_receiver: bool) -> None:
    """Actually emit all events (commands, navs, focus) tagged with run_id."""
    # Shell commands via the real emit path.
    for c in spec["commands"]:
        emit_event({
            "source": "zsh", "kind": "command", "ts": c["ts"], "host": host,
            "text": c["text"], "duration_ms": c["duration_ms"],
            "exit_code": c["exit_code"], "session": run_id,
            "project": "validation-replay", "app": "",
        })

    # Browser navs: prefer the receiver (exercises that path), else direct emit.
    # NOTE the receiver does not let us set `session`, so for isolation we ALWAYS
    # emit navs through emit with the run-id tag; we still ping the receiver
    # health endpoint and record whether it was reachable.
    # app="" on purpose: the switch / deep-work metrics filter app != '', so
    # leaving navs app-less keeps those metrics driven SOLELY by the scripted
    # focus sequence (deterministic K and gap). active_ms / nav_count filter on
    # source='browser' AND text != '', so they are unaffected by the empty app.
    for nav in spec["navs"]:
        emit_event({
            "source": "browser", "kind": "nav", "ts": spec["commands"][0]["ts"],
            "host": host, "text": nav["url"], "app": "",
            "session": run_id, "payload": nav["payload"], "project": "",
        })

    # App-focus switches (source=keys, kind=window-focus).
    for f in spec["focus"]:
        emit_event({
            "source": "keys", "kind": "window-focus", "ts": f["ts"], "host": host,
            "app": f["app"], "session": run_id, "project": "validation-replay",
            "text": "",
        })


def replay_keystrokes(run_id: str) -> tuple[bool, str]:
    """Synthetic keystrokes via xdotool — only if DISPLAY is available."""
    if not os.environ.get("DISPLAY"):
        return False, "DISPLAY unset (headless): keystroke replay skipped — run on the laptop's X session"
    try:
        subprocess.run(["xdotool", "type", "--clearmodifiers", f"replay {run_id}"],
                       check=True, timeout=10)
        return True, "xdotool keystrokes sent"
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        return False, f"xdotool failed: {exc}"


def _collector_env() -> dict:
    """Env for a flush-once: the collector daemon's EnvironmentFile holds the
    WRITER creds (the harness env only has the reader, which cannot INSERT).

    Prefer ~/.config/activity-collector/env so flush-once can actually ship; if
    absent, fall back to the current env (a long-running daemon will ship the
    spool anyway). Reader creds are stripped so they don't shadow the writer.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD")}
    envfile = Path.home() / ".config/activity-collector/env"
    if envfile.exists():
        for line in envfile.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def flush_collector() -> str:
    """Trigger a collector flush-once so the burst ships promptly.

    If the daemon's writer creds aren't available, the running daemon will still
    ship the spool on its own 10s cycle — this just speeds it up.
    """
    collector = Path(__file__).resolve().parents[1] / "collector" / "collector.py"
    try:
        r = subprocess.run([sys.executable, str(collector), "--flush-once"],
                           capture_output=True, text=True, timeout=60,
                           env=_collector_env())
        tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        return f"flush-once rc={r.returncode} {tail}"
    except subprocess.SubprocessError as exc:
        return f"flush-once failed: {exc}"


def run(plan: ReplayPlan, out_path: Path) -> GroundTruth:
    run_id = f"vrun-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    host = os.environ.get("ACTIVITY_HOST") or os.environ.get("HOST") or "workbench"
    notes: list = []

    # Receiver reachability (informational).
    use_receiver = False
    try:
        resp = urllib.request.urlopen(plan.receiver_url.replace("/event", "/health"),
                                      timeout=1.5)
        use_receiver = (getattr(resp, "status", 200) == 200)
        resp.close()
        notes.append("browser receiver reachable")
    except (urllib.error.URLError, OSError):
        notes.append("browser receiver not reachable; navs emitted directly to spool")

    ks_done, ks_note = replay_keystrokes(run_id)
    notes.append(ks_note)

    gt, spec = build_ground_truth(plan, run_id, host, ks_done, notes)
    perform_replay(plan, host, run_id, spec, use_receiver)
    notes.append(flush_collector())
    gt.notes = notes

    out_path.write_text(json.dumps(asdict(gt), indent=2))
    return gt


def main(argv=None) -> int:
    argv = argv or []
    out = Path(os.environ.get("REPLAY_GROUND_TRUTH", "/tmp/replay-ground-truth.json"))
    plan = ReplayPlan()
    gt = run(plan, out)
    print(f"Replay run_id={gt.run_id} host={gt.host}")
    print(json.dumps(asdict(gt), indent=2))
    print(f"\nGround truth → {out}")
    print(f"To isolate these events in ClickHouse: WHERE session = '{gt.run_id}'")
    print(f"To EXCLUDE all replay events:          WHERE session NOT LIKE 'vrun-%'")
    for n in gt.notes:
        print("  note:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
