#!/usr/bin/env python3
"""backfill — re-emit all historical OpenCode sessions and messages.

Clears both tailer state files, then runs session tailer then message tailer
sequentially. Since state is empty, both tailers emit everything in the DB.

Usage:
    python3 backfill.py [--db PATH]
"""
from __future__ import annotations

import sys
from pathlib import Path


def _add_opencode_to_path() -> None:
    """Ensure the opencode package directory is on sys.path for imports."""
    _dir = str(Path(__file__).parent)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)


def run(db_path: Path | None = None) -> int:
    _add_opencode_to_path()

    import tailer as T  # noqa: E402
    import session_tailer as ST  # noqa: E402

    # Clear state files so both tailers re-emit everything
    for sp in (ST.state_path(), T.state_path()):
        try:
            sp.unlink(missing_ok=True)
        except OSError:
            pass

    # Run session tailer first (session summaries)
    rc = ST.run(db_path=db_path)
    if rc != 0:
        return rc

    # Run message tailer (per-message events)
    rc = T.run(db_path=db_path)
    return rc


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill all OpenCode activity events")
    parser.add_argument("--db", type=Path, default=None, help="Override DB path")
    args = parser.parse_args()
    raise SystemExit(run(db_path=args.db))
