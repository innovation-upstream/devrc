#!/usr/bin/env python3
"""Serialise ONE opencode session into ONE canonical, byte-deterministic artifact.

Session id in, newline-delimited JSON out: one record per part, each carrying the
session, message and part dicts exactly as `_shared.py` already yields them. The
shape mirrors a Claude Code transcript closely enough to be greppable and diffable
the same way.

Usage:
    python3 export.py <session-id> [-o OUT]      # default: stdout

🔴 WHAT THIS ARTIFACT DOES *NOT* CONTAIN — name the omission, because "the whole
session" is a claim about a population and an unnamed one is worthless:

  * `tool-output/`, `snapshot/` and `storage/` under the opencode data dir. Those
    are real files a session produced and they are DELIBERATELY excluded — this
    reads the DB only. A session that wrote a large tool output has that output
    referenced here and not embedded.
  * whatever `_shared.iter_*` drops. Those functions project the `data` JSON blob
    onto named fields; this module carries `_data` (the whole parsed blob) so the
    projection is not additionally lossy, but a column `_shared` does not SELECT is
    invisible here too.
  * any session-level file outside the `session`/`message`/`part` tables.

🔴 THIS MODULE OPENS NO DATABASE OF ITS OWN. It takes a connection from
`_shared.get_db()`, which opens `mode=ro`. Three readers of this store already
exist (`_shared`, the tailer, `find-session`); a fourth connection path is what
`tests/test_export.py::test_export_opens_no_connection_of_its_own` exists to
prevent, and it asserts over the AST rather than trusting this comment.

🔴 DETERMINISM RESTS ON A TOTAL ORDER, AND THE READER DOES NOT PROVIDE ONE.
`_shared.iter_messages` and `iter_parts` both `ORDER BY time_created` alone. That
is not a total order: SQLite leaves ties unspecified, so two rows sharing a
millisecond can come back in either order and the artifact's bytes would change
between runs for reasons nothing controls. Every sequence here is therefore
re-sorted on `(time_created, id)`.

⚠ MEASURED 2026-09-06: **zero** ties across 617 messages and 2,907 parts on this
host. So the hazard is LATENT, not live — which is precisely why the determinism
test must use a fixture carrying a DELIBERATE tie. Real data cannot exhibit the
bug, so a test that only reads real data would pass whether or not this sort
exists, and would report coverage it does not have.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _shared as S  # noqa: E402


#: The sort key that makes the output a function of the data rather than of
#: SQLite's row order. `id` breaks a `time_created` tie; it is the primary key,
#: so the pair is total.
def _order_key(row: dict) -> tuple:
    return (row.get("time_created") or 0, str(row.get("id") or ""))


def iter_session_records(db: Any, session_id: str) -> Iterator[dict]:
    """Yield one record per part, in a total order, for a single session.

    Each record is `{"session": …, "message": …, "part": …}`. The session dict is
    repeated on every record on purpose: it makes any single line of the artifact
    self-describing, which is what lets `grep` on one line answer "which session
    was this".
    """
    session = None
    for s in S.iter_sessions(db):
        if s.get("id") == session_id:
            session = s
            break
    if session is None:
        return

    for message in sorted(S.iter_messages(db, session_id), key=_order_key):
        for part in sorted(S.iter_parts(db, message["id"]), key=_order_key):
            yield {"session": session, "message": message, "part": part}


def render(records: Iterator[dict]) -> str:
    """Render records as newline-delimited JSON, canonically.

    `sort_keys` is what makes two runs byte-identical even if dict insertion order
    differs between them; `ensure_ascii=False` keeps the text greppable in the
    terminal rather than escaping every non-ASCII byte.
    """
    out = []
    for rec in records:
        out.append(json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str))
    return "".join(line + "\n" for line in out)


def export_session(db: Any, session_id: str) -> str:
    return render(iter_session_records(db, session_id))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="opencode-export",
        description="Serialise one opencode session to canonical NDJSON.",
    )
    ap.add_argument("session_id")
    ap.add_argument("-o", "--out", help="write here instead of stdout")
    ap.add_argument("--db", help="explicit DB path (default: the discovered store)")
    args = ap.parse_args(argv)

    db = S.get_db(Path(args.db) if args.db else None)
    if db is None:
        print("no opencode database found", file=sys.stderr)
        return 2

    text = export_session(db, args.session_id)
    if not text:
        # An unknown session id and a session with no parts are DIFFERENT, and
        # both produce empty output — so say which, rather than exiting 0 on a
        # typo and letting the caller record an empty artifact as a success.
        known = any(s.get("id") == args.session_id for s in S.iter_sessions(db))
        if not known:
            print(f"no such session: {args.session_id}", file=sys.stderr)
            return 3
        print(f"session {args.session_id} has no parts", file=sys.stderr)
        return 4

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
