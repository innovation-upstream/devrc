#!/usr/bin/env python3
"""CLI entry point for the OpenCode activity source.

Usage:
    python -m opencode [--mode tailer|session|all] [--db PATH] [--backfill] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _add_opencode_to_path() -> None:
    """Ensure the opencode package directory is on sys.path for imports."""
    _dir = str(Path(__file__).parent)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="opencode",
        description="OpenCode activity telemetry tailer CLI",
    )
    parser.add_argument(
        "--mode",
        choices=["tailer", "session", "all"],
        default="all",
        help="Which tailer(s) to run (default: all)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override DB path (default: auto-detect from _shared.opencode_db_path())",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Run session tailer on ALL sessions (ignore state, re-emit everything)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print events instead of emitting",
    )
    return parser.parse_args(argv)


def _clear_state_files() -> None:
    """Remove state files so tailers re-emit everything."""
    import tailer as T  # noqa: E402
    import session_tailer as ST  # noqa: E402
    for sp in (T.state_path(), ST.state_path()):
        try:
            sp.unlink(missing_ok=True)
        except OSError:
            pass


def run(argv: list[str] | None = None) -> int:
    _add_opencode_to_path()
    args = parse_args(argv)

    if args.backfill:
        _clear_state_files()

    import tailer as T  # noqa: E402
    import session_tailer as ST  # noqa: E402

    if args.dry_run:
        import _shared as S  # noqa: E402
        import json as _json

        db = S.get_db(args.db)
        if db is None:
            print("no OpenCode DB found, exiting")
            return 0

        emitted = 0
        try:
            if args.mode in ("session", "all"):
                for session in S.iter_sessions(db):
                    sid = session["id"]
                    messages = list(S.iter_messages(db, sid))
                    all_parts = []
                    for msg in messages:
                        all_parts.extend(S.iter_parts(db, msg["id"]))
                    rollup = ST.build_rollup(session, messages, all_parts)
                    ev = ST.build_event(sid, session.get("directory") or "", rollup)
                    print(_json.dumps(ev, ensure_ascii=False))
                    emitted += 1

            if args.mode in ("tailer", "all"):
                for session in S.iter_sessions(db):
                    sid = session["id"]
                    for msg in S.iter_messages(db, sid):
                        parts = list(S.iter_parts(db, msg["id"]))
                        ev = T.build_event(msg, parts, session)
                        if ev is not None:
                            print(_json.dumps(ev, ensure_ascii=False))
                            emitted += 1
        finally:
            db.close()

        print(f"dry-run: {emitted} events")
        return 0

    rc = 0
    if args.mode in ("tailer", "all"):
        rc = max(rc, T.run(db_path=args.db))
    if args.mode in ("session", "all"):
        rc = max(rc, ST.run(db_path=args.db))
    return rc


if __name__ == "__main__":
    raise SystemExit(run())
