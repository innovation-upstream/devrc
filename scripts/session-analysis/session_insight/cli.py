#!/usr/bin/env python3
"""cli — entrypoint for the Layer B session-insight extractor.

Subcommands (operated via the `activity` skill — see its SKILL.md):

  status   [--days N] [--settle-hours H] [--host H] [--json]
           what's pending — candidate + skip counts. No writes.

  prepare  [--days N] [--limit K] [--settle-hours H] [--force]
           [--chunk-chars C] [--redact-public-ips] [--host H] [--json]
           select settled + un-extracted sessions, scrub, chunk, attach ground
           truth, write staging/<run-id>/*.input.json. Prints the run-id + the
           input paths for the LIVE SESSION to extract from.

  write    --run-id <id> [--force] [--clean] [--json]
           consolidate the result.json files → validate → emit (no CH read).

CH creds come from the env (CLICKHOUSE_URL/USER/PASSWORD — reader creds via
SOPS; see the activity skill), reusing chquery exactly like activity-scan.py.
Degrades gracefully when telemetry is unconfigured/unreachable (message + exit 0).
"""
from __future__ import annotations

# When run as a script, THIS dir is sys.path[0] and it contains `select.py`,
# which would shadow the stdlib `select` module that urllib/selectors import
# lazily (breaking any socket use). Import the real stdlib `select` FIRST — with
# our dir temporarily off the path — so it wins in sys.modules before anything
# below pulls in urllib (via chquery).
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_saved_path = list(_sys.path)
_sys.path = [p for p in _sys.path
             if _os.path.abspath(p or _os.getcwd()) != _HERE]
import select as _stdlib_select  # noqa: F401,E402  (now the real stdlib module)
_sys.path = _saved_path

import argparse  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

# Load select.py under a distinct name: the bare name `select` is a stdlib module
# (imported at interpreter startup), so `import select` would resolve to THAT.
# Loading by file path also runs select.py's sys.path wiring (chquery + _shared).
_sel_spec = importlib.util.spec_from_file_location(
    "si_select", Path(__file__).resolve().parent / "select.py")
sel = importlib.util.module_from_spec(_sel_spec)
_sel_spec.loader.exec_module(sel)

import chquery as Q           # noqa: E402  (put on sys.path by select.py above)
from prepare import new_run_id, prepare_run, DEFAULT_CHUNK_CHARS  # noqa: E402
from write import write_run   # noqa: E402

DEFAULT_DAYS = 14
DEFAULT_LIMIT = 20


def _settle_default() -> float:
    try:
        return float(os.environ.get("INSIGHT_SETTLE_HOURS", "6"))
    except ValueError:
        return 6.0


def _redact_default() -> bool:
    return os.environ.get("INSIGHT_REDACT_IPS", "") not in ("", "0", "false", "no")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def _client_or_none():
    try:
        conn = Q.CHConn.from_env()
    except RuntimeError as e:
        print(f"session_insight: telemetry not configured — {e}", file=sys.stderr)
        return None
    return Q.CHClient(conn)


def _grouped_skips(skips):
    out: dict = {}
    for _s, reason in skips:
        out[reason] = out.get(reason, 0) + 1
    return out


def cmd_status(a) -> int:
    client = _client_or_none()
    if client is None:
        return 0
    try:
        cands, skips = sel.select_candidates(
            client, days=a.days, settle_hours=a.settle_hours, limit=None,
            force=False, host=a.host)
    except Exception as e:  # noqa: BLE001 — telemetry optional
        print(f"session_insight: telemetry unavailable ({e}); nothing to report.",
              file=sys.stderr)
        return 0
    data = {
        "candidates": len(cands),
        "candidate_sessions": [c["session"] for c in cands],
        "skips": _grouped_skips(skips),
        "days": a.days,
        "settle_hours": a.settle_hours,
    }
    if a.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"session_insight status — trailing {a.days}d, settle {a.settle_hours}h")
        print(f"  candidates pending extraction: {len(cands)}")
        for c in cands[:40]:
            print(f"    - {c['session']}  [{c.get('project') or '?'}]")
        if data["skips"]:
            print("  skipped:")
            for reason, n in sorted(data["skips"].items()):
                print(f"    {n:>4}  {reason}")
    return 0


def cmd_prepare(a) -> int:
    client = _client_or_none()
    if client is None:
        return 0
    try:
        cands, skips = sel.select_candidates(
            client, days=a.days, settle_hours=a.settle_hours, limit=a.limit,
            force=a.force, host=a.host)
    except Exception as e:  # noqa: BLE001
        print(f"session_insight: telemetry unavailable ({e}); nothing to prepare.",
              file=sys.stderr)
        return 0
    if not cands:
        print("session_insight: no settled, un-extracted sessions to prepare.",
              file=sys.stderr)
        if a.json:
            print(json.dumps({"run_id": None, "candidates": 0,
                              "skips": _grouped_skips(skips)}, indent=2))
        return 0
    run_id = new_run_id()
    manifest = prepare_run(cands, skips, run_id, chunk_chars=a.chunk_chars,
                           redact_public_ips=a.redact_public_ips,
                           meta={"days": a.days, "limit": a.limit,
                                 "settle_hours": a.settle_hours, "host": a.host,
                                 "force": a.force})
    if a.json:
        print(json.dumps(manifest, indent=2))
    else:
        print(f"prepared run {run_id}")
        print(f"  staging: {manifest['staging_dir']}")
        print(f"  results: {manifest['results_dir']}  (write result.json here)")
        print(f"  {len(manifest['sessions'])} session(s) to extract:")
        for s in manifest["sessions"]:
            print(f"    - {s['session']}  ({s['chunk_count']} chunk(s))  → {s['input_path']}")
        if manifest["skips"]:
            print("  skipped:")
            for reason, n in sorted(_grouped_skips(skips).items()):
                print(f"    {n:>4}  {reason}")
        print(f"\nNow extract each input.json → its result_path, then:\n"
              f"  cli.py write --run-id {run_id}")
    return 0


def cmd_write(a) -> int:
    try:
        summary = write_run(a.run_id, force=a.force, clean=a.clean)
    except FileNotFoundError:
        print(f"session_insight: no such run-id {a.run_id!r} "
              "(run `prepare` first, or check the id).", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"write run {a.run_id}")
        print(f"  emitted: {len(summary['emitted'])}  "
              f"{summary['emitted'] if summary['emitted'] else ''}")
        if summary["skipped_already_emitted"]:
            print(f"  skipped (already emitted): {summary['skipped_already_emitted']}")
        if summary["missing"]:
            print(f"  MISSING (no result.json — re-run): {summary['missing']}")
        if summary["conflicts"]:
            print(f"  CONFLICTS (dup result — neither emitted): {summary['conflicts']}")
        if summary["rejected"]:
            print("  REJECTED (schema-invalid, quarantined):")
            for r in summary["rejected"]:
                print(f"    - {r['session']}: {r['errors']}")
        if summary.get("warnings"):
            print(f"  vocab warnings: {summary['warnings']}")
        if summary.get("cleaned"):
            print("  cleaned staging + results dirs")
    return 0


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Layer B session-insight extractor (deterministic plumbing; "
                    "the live Claude session does the extraction).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _common(sp):
        sp.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"selection window in days (default {DEFAULT_DAYS})")
        sp.add_argument("--settle-hours", type=float, default=_settle_default(),
                        help="a session must be idle this many hours to be a "
                             "candidate (0 disables; env INSIGHT_SETTLE_HOURS)")
        sp.add_argument("--host", default=None, help="restrict to one host label")
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    st = sub.add_parser("status", help="show pending candidates (no writes)")
    _common(st)
    st.set_defaults(func=cmd_status)

    pr = sub.add_parser("prepare", help="select + scrub + chunk + write input.json")
    _common(pr)
    pr.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"max candidates per run (default {DEFAULT_LIMIT})")
    pr.add_argument("--force", action="store_true",
                    help="re-prepare regardless of an existing insight row / settle gate")
    pr.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS,
                    help=f"per-chunk char budget (default {DEFAULT_CHUNK_CHARS})")
    pr.add_argument("--redact-public-ips", action="store_true",
                    default=_redact_default(),
                    help="also redact globally-routable IPs (env INSIGHT_REDACT_IPS)")
    pr.set_defaults(func=cmd_prepare)

    wr = sub.add_parser("write", help="consolidate result.json + emit to ClickHouse")
    wr.add_argument("--run-id", required=True, help="the prepare run-id to write")
    wr.add_argument("--force", action="store_true",
                    help="re-emit even sessions already emitted for this run")
    wr.add_argument("--clean", action="store_true",
                    help="purge the run's staging + results dirs on a fully clean run")
    wr.add_argument("--json", action="store_true", help="machine-readable output")
    wr.set_defaults(func=cmd_write)
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
