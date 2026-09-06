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
  * 🔴 **a message with NO parts produces NO record at all**, because the unit is
    the part. Measured 2026-09-06: 38 of 17,679 messages store-wide. Such a
    message is invisible in the artifact — not empty, absent.
  * whatever `_shared.iter_*` drops. Those functions project the `data` JSON blob
    onto named fields; this module carries `_data` (the whole parsed blob) so the
    projection is not additionally lossy, but a column `_shared` does not SELECT is
    invisible here too.

🔴 THIS MODULE OPENS NO DATABASE OF ITS OWN. It takes a connection from
`_shared.get_db()`, which opens `mode=ro`. Counting actual `sqlite3.connect` sites
against this store there are exactly TWO — `_shared.py` (`mode=ro`) and
`scripts/lib/opencode_search.py`, which is **not** read-only — so this would be a
third. `tests/test_export.py::test_export_opens_no_connection_of_its_own` is what
prevents it, over the AST rather than trusting this comment.

🔴 DETERMINISM RESTS ON A TOTAL ORDER, AND THE READER DOES NOT PROVIDE ONE.
`_shared.iter_messages` and `iter_parts` both `ORDER BY time_created` alone. That
is not a total order: SQLite leaves ties unspecified, so two rows sharing a
timestamp can come back in either order and the artifact's bytes would change
between runs for reasons nothing controls. Every sequence here is therefore
re-sorted on `(time_created, id)`.

⚠ MEASURED 2026-09-06, STORE-WIDE (691 sessions / 17,679 messages / 77,671 parts):
**5 part tie-groups covering 10 rows in 5 distinct sessions**; zero message-level
ties. So ties are RARE — about 1 part in 7,767 — but they are REAL, and an earlier
revision of this comment said "zero ties … real data cannot exhibit the bug" on the
strength of a 2,907-part sample, i.e. 3.7% of the store, stated at host scope. That
was false, and it was the dangerous direction: it invited deleting the sort below as
unfalsifiable defensive code. **The rarity is why the test fixture carries a
DELIBERATE tie — a sampled session will not supply one — not evidence that the sort
is unnecessary.**
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterator

# ⚠ Deliberately NOT `.resolve()`. `_shared.py` records why: this tree ships to
# `~/.config/activity-collector/opencode/` as nix store symlinks, and resolving
# walks INTO the store. The siblings (`tailer.py`, `session_tailer.py`) use the
# unresolved parent for the same reason; matching them is the point.
_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _shared as S  # noqa: E402


EXIT_OK = 0
EXIT_NO_DB = 2
EXIT_NO_SUCH_SESSION = 3
EXIT_SESSION_EMPTY = 4
#: 🔴 A store we cannot READ is not a user typo. `_shared` deliberately swallows
#: `OperationalError` and returns empty iterators ("tolerate schema drift"), which
#: is right for a telemetry tailer and wrong here the moment emptiness is given a
#: MEANING: without this code, a renamed table reports `no such session` and a
#: dropped one reports `has no parts`, blaming the caller for a broken store.
EXIT_STORE_UNREADABLE = 5


def _order_key(row: dict) -> tuple:
    """A total order over rows, that cannot raise on a heterogeneous column.

    `id` breaks a `time_created` tie; it is the primary key, so the pair is total.

    🔴 The leading type-rank exists because Python 3 raises `TypeError` on `int <
    str`, while the SQL `ORDER BY` this replaces cannot fail. `time_created` is
    `integer` for all 77,671 parts and 17,679 messages today — measured — so this
    is defence against schema drift in a module whose base layer explicitly
    promises to tolerate it, not against anything live.
    """
    tc = row.get("time_created")
    rank = (0, float(tc)) if isinstance(tc, (int, float)) and not isinstance(tc, bool) \
        else (1, str(tc))
    return (rank, str(row.get("id") or ""))


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

    `sort_keys` makes two runs byte-identical even if dict insertion order differs.

    🔴 `ensure_ascii=True` IS LOAD-BEARING, NOT A STYLE CHOICE, and an earlier
    revision had it False "to keep the text greppable". Two measured consequences
    of False, both latent today (0 of 77,671 live parts) and both destructive:

      * a **lone surrogate** in the stored blob — exactly what `JSON.stringify`
        emits when an emoji is split across streaming chunks — raises
        `UnicodeEncodeError` at write time, AFTER the output file has been
        truncated. The artifact it was asked to refresh is destroyed.
      * **U+2028 / U+2029 / U+0085** pass through raw. The line stays valid NDJSON,
        but `str.splitlines()` — the idiom this module's own tests use — shatters
        one record into fragments, none of which parse.

    Escaping costs nothing a reader loses: `grep` still matches ASCII, and any JSON
    reader restores the text.
    """
    out = []
    for rec in records:
        out.append(json.dumps(rec, sort_keys=True, ensure_ascii=True, default=str))
    return "".join(line + "\n" for line in out)


def export_session(db: Any, session_id: str) -> str:
    return render(iter_session_records(db, session_id))


def write_artifact(path: str | Path, text: str) -> None:
    """Write atomically, 0600, refusing to follow a symlink.

    🔴 Three separate hazards, all measured on the naive `Path.write_text`:
      * the source DB is **0600** and the artifact carries the same content, but
        `write_text` created it **0644** (process umask) — widening the audience
        for client-confidential session content;
      * `-o` pointed at a symlink FOLLOWED it and overwrote the target;
      * the write truncates first, so any failure mid-write leaves a 0-byte file
        where a good artifact used to be.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def _store_is_readable(db: Any) -> bool:
    """Can we actually read the three tables, as opposed to finding them empty?

    `_shared` returns empty iterators for a store it cannot read, so emptiness is
    ambiguous until this has been asked separately.
    """
    try:
        for tbl in (S.SESSION_TABLE, S.MESSAGE_TABLE, S.PART_TABLE):
            db.execute(f"SELECT 1 FROM {tbl} LIMIT 1").fetchone()
    except (sqlite3.DatabaseError, IndexError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="opencode-export",
        description="Serialise one opencode session to canonical NDJSON.",
    )
    ap.add_argument("session_id")
    ap.add_argument("-o", "--out", help="write here instead of stdout")
    ap.add_argument("--db", help="explicit DB path (default: the discovered store)")
    args = ap.parse_args(argv)

    try:
        db = S.get_db(Path(args.db) if args.db else None)
    except sqlite3.DatabaseError as exc:
        print(f"cannot open store: {exc}", file=sys.stderr)
        return EXIT_STORE_UNREADABLE
    if db is None:
        print("no opencode database found", file=sys.stderr)
        return EXIT_NO_DB

    try:
        if not _store_is_readable(db):
            print("store is unreadable (missing tables or schema drift) — "
                  "this is NOT the same as an unknown session", file=sys.stderr)
            return EXIT_STORE_UNREADABLE

        try:
            text = export_session(db, args.session_id)
        except (sqlite3.DatabaseError, IndexError) as exc:
            print(f"store read failed: {exc}", file=sys.stderr)
            return EXIT_STORE_UNREADABLE

        if not text:
            known = any(s.get("id") == args.session_id for s in S.iter_sessions(db))
            if not known:
                print(f"no such session: {args.session_id}", file=sys.stderr)
                return EXIT_NO_SUCH_SESSION
            print(f"session {args.session_id} has no parts", file=sys.stderr)
            return EXIT_SESSION_EMPTY

        if args.out:
            write_artifact(args.out, text)
        else:
            sys.stdout.write(text)
        return EXIT_OK
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
