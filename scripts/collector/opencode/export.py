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
  * 🔴 **17 of the store's 20 tables**, measured — `todo`, `session_input`,
    `permission`, `session_share`, `session_message`, `session_context_epoch`,
    `project` and the rest. Only `session`, `message` and `part` are read. An
    earlier revision of this list carried this bullet as "any session-level file
    outside the three tables" and a later one DELETED it, in the very docstring
    whose thesis is that an unnamed omission is worthless.
  * whatever `_shared.iter_*` drops. Those functions project the `data` JSON blob
    onto named fields; this module carries `_data` (the whole parsed blob) so the
    projection is not additionally lossy, but a column `_shared` does not SELECT is
    invisible here too.

🔴 THIS MODULE OPENS NO DATABASE OF ITS OWN. It takes a connection from
`_shared.get_db()`, which opens `mode=ro`. Counting actual `sqlite3.connect` sites
against this store there are THREE, in two files: `_shared.py` (`mode=ro`) and
`scripts/lib/opencode_search.py` twice — once directly and once inside the remote
script it ships to a peer host — **neither** of which is read-only. So this module
would be a fourth. `tests/test_export.py::test_export_opens_no_connection_of_its_own` is what
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
import tempfile
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
        `UnicodeEncodeError` at write time, so the export FAILS OUTRIGHT.
        ⚠ An earlier revision of this note added "…after the output file has been
        truncated, destroying the artifact it was asked to refresh". That was true
        of `Path.write_text` and is NOT true now: `write_artifact` writes to a temp
        and the previous artifact survives — measured. The stale half mattered
        because it credited the wrong mechanism, and a maintainer reading it would
        conclude the temp-file writer is redundant.
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
    """Write atomically and 0600, via a UNIQUE temp in the destination directory.

    🔴 Hazards this closes, each measured on the naive `Path.write_text`:
      * `-o` pointed at a symlink FOLLOWED it and overwrote the target;
      * the write truncates first, so a failure mid-write left a 0-byte file where
        a good artifact used to be;
      * the artifact was created **0644** (process umask). ⚠ An earlier revision
        justified 0600 as "narrower than the 0600 source DB" — **the source is
        0644**, measured, and so is any sqlite file created under this umask. The
        justification was false; the decision stands on its own feet: the artifact
        carries client-confidential session content and 0600 is the right default
        regardless of what the store happens to be.

    🔴 THE TEMP NAME IS UNIQUE, NOT `<out>.tmp`, AND THAT IS THE SECURITY PROPERTY.
    A fixed sibling name is predictable, so an unrelated file already at that path
    was truncated and renamed away — measured: rc 0, the neighbour's content
    destroyed, no warning — and a symlink planted there needed `O_NOFOLLOW` to
    defend. `mkstemp` removes both by construction: it opens `O_EXCL` at 0600 on a
    name nothing can have pre-placed. Preferring construction over a guard also
    removes a flag no test could reach — deleting `O_NOFOLLOW` from the previous
    version was a SURVIVING mutant.

    `os.replace` is inside the `try` because its failure — `-o` naming a directory,
    say — otherwise left the temp on disk holding the full session artifact.
    """
    path = Path(path)
    parent = path.parent if str(path.parent) else Path(".")
    fd, tmpname = tempfile.mkstemp(dir=parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


#: The columns `_shared` filters or orders on. 🔴 A `SELECT 1 FROM <t> LIMIT 1`
#: proves only that the TABLE NAME resolves — an earlier revision did exactly that
#: and MISSED 5 of 12 single-column drifts, because SQLite raises on the missing
#: column inside `_shared`'s own WHERE/ORDER BY, where `_shared` swallows it into an
#: empty iterator and the caller sees "no parts" or even "no such session" — the
#: precise wording this whole exit-code split exists to stop emitting.
_REQUIRED_COLUMNS = {
    S.SESSION_TABLE: ("id", "time_created"),
    S.MESSAGE_TABLE: ("id", "session_id", "time_created", "data"),
    S.PART_TABLE: ("id", "message_id", "session_id", "time_created", "data"),
}


def _store_is_readable(db: Any) -> bool:
    """Can we actually read what the reader reads, as opposed to finding it empty?

    `_shared` returns empty iterators for a store it cannot read, so emptiness is
    ambiguous until this has been asked separately — and asking has to name the
    same columns the reader does, or the answer is narrower than the question.
    """
    try:
        for tbl, cols in _REQUIRED_COLUMNS.items():
            db.execute(
                f"SELECT {', '.join(cols)} FROM {tbl} ORDER BY time_created LIMIT 1"
            ).fetchone()
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
