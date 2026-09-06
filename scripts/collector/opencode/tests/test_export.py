"""Tests for opencode/export.py — one canonical artifact per session.

Run: python -m pytest scripts/collector/opencode/tests/test_export.py -v

🔴 THE DETERMINISM TEST USES A FIXTURE WITH A DELIBERATE `time_created` TIE.
Measured on this host 2026-09-06: zero ties across 617 messages and 2,907 parts,
so REAL data cannot exhibit the ordering bug the sort in `export._order_key`
exists to prevent. A determinism test that only read the real store would pass
with that sort deleted — coverage it does not have. The fixture supplies the tie
the real data will not.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

COLLECTOR = Path(__file__).resolve().parent.parent.parent
OPENCODE = COLLECTOR / "opencode"
for _p in (str(COLLECTOR), str(OPENCODE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import export as E  # noqa: E402


def _build_db(path: Path, *, tie: bool = False) -> sqlite3.Connection:
    """A minimal store with the three tables `_shared` reads.

    `tie=True` gives two parts of one message an IDENTICAL `time_created`, with
    ids that sort opposite to insertion order — so an implementation relying on
    SQLite's row order can differ from one imposing a total order.
    """
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, workspace_id TEXT,
                              parent_id TEXT, slug TEXT, directory TEXT, path TEXT,
                              title TEXT, version TEXT, share_url TEXT,
                              summary_additions INTEGER, summary_deletions INTEGER,
                              summary_files INTEGER, summary_diffs TEXT, metadata TEXT,
                              cost REAL, tokens_input INTEGER, tokens_output INTEGER,
                              tokens_reasoning INTEGER, tokens_cache_read INTEGER,
                              tokens_cache_write INTEGER, revert TEXT, permission TEXT,
                              agent TEXT, model TEXT, time_created INTEGER,
                              time_updated INTEGER, time_compacting INTEGER,
                              time_archived INTEGER);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                              time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                           time_created INTEGER, time_updated INTEGER, data TEXT);
        """
    )
    db.execute(
        "INSERT INTO session (id, project_id, directory, title, time_created, time_updated) "
        "VALUES (?,?,?,?,?,?)",
        ("s1", "p1", "/tmp/proj", "t", 1000, 1000),
    )
    db.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        ("m1", "s1", 1000, 1000, json.dumps({"role": "assistant"})),
    )
    t_b = 2000 if tie else 2001
    # 'zzz' is inserted FIRST and must sort SECOND under (time_created, id).
    db.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        ("zzz", "m1", "s1", t_b, t_b, json.dumps({"type": "tool", "tool": "bash",
                                                  "state": {"out": "second"}})),
    )
    db.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        ("aaa", "m1", "s1", 2000, 2000, json.dumps({"type": "text", "text": "first"})),
    )
    db.commit()
    db.row_factory = sqlite3.Row
    return db


# --------------------------------------------------------------------------- #
# criterion 2 — NDJSON, one record per part, carrying what the reader yields
# --------------------------------------------------------------------------- #
def test_one_ndjson_record_per_part_carrying_session_message_and_part(tmp_path):
    db = _build_db(tmp_path / "a.db")
    text = E.export_session(db, "s1")
    lines = text.splitlines()
    assert len(lines) == 2, "one record per part"
    recs = [json.loads(ln) for ln in lines]
    for r in recs:
        assert set(r) == {"session", "message", "part"}
        assert r["session"]["id"] == "s1"
        assert r["message"]["id"] == "m1"
    assert text.endswith("\n")


def test_tool_parts_carry_their_payload_not_just_text(tmp_path):
    """🔴 The task body's own ASSUMPTION was that `text` carries the receipts.

    Measured false: across 12 sessions / 1,055 real parts, tool parts were 378 of
    them and **none** had `text`; their content is in `_data`/`state`. An exporter
    serialising `text` alone would ship an artifact with zero tool calls in it, so
    this pins the field that actually carries them.
    """
    db = _build_db(tmp_path / "b.db")
    recs = [json.loads(ln) for ln in E.export_session(db, "s1").splitlines()]
    tool = [r for r in recs if r["part"]["type"] == "tool"][0]
    assert tool["part"]["text"] is None, "fixture mirrors reality: tool parts have no text"
    assert tool["part"]["_data"]["state"] == {"out": "second"}, "the payload survives"
    assert tool["part"]["tool"] == "bash"


# --------------------------------------------------------------------------- #
# criterion 3 — byte-determinism, including on the tie real data cannot supply
# --------------------------------------------------------------------------- #
def test_two_runs_are_byte_identical(tmp_path):
    db = _build_db(tmp_path / "c.db")
    a = E.export_session(db, "s1")
    b = E.export_session(db, "s1")
    assert hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest()


def test_ordering_is_total_when_time_created_ties(tmp_path):
    """The tie is the whole point — see this module's docstring."""
    db = _build_db(tmp_path / "d.db", tie=True)
    recs = [json.loads(ln) for ln in E.export_session(db, "s1").splitlines()]
    ids = [r["part"]["id"] for r in recs]
    assert ids == ["aaa", "zzz"], (
        "parts sharing a time_created must order by id, not by SQLite's row order "
        f"(insertion order was zzz, aaa; got {ids})"
    )


def test_sort_is_load_bearing_a_time_only_key_would_reverse_this(tmp_path):
    """Positive control for the test above: prove the fixture CAN distinguish.

    Sorting on `time_created` alone over the tied fixture is not guaranteed to
    reproduce the total order — this asserts the fixture's two parts are actually
    tied, so the previous test is testing something.
    """
    db = _build_db(tmp_path / "e.db", tie=True)
    times = {r["part"]["time_created"]
             for r in (json.loads(ln) for ln in E.export_session(db, "s1").splitlines())}
    assert len(times) == 1, "the tie fixture must actually tie, or the ordering test is vacuous"


# --------------------------------------------------------------------------- #
# criterion 4 — no second reader
# --------------------------------------------------------------------------- #
def test_export_opens_no_connection_of_its_own():
    """AST assertion, not a grep: a fourth DB reader is the thing to prevent."""
    src = (OPENCODE / "export.py").read_text()
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = None
            if isinstance(f, ast.Attribute):
                name = f.attr
            elif isinstance(f, ast.Name):
                name = f.id
            if name in {"connect"}:
                offenders.append(ast.dump(node)[:80])
    assert not offenders, f"export.py must take its connection from _shared: {offenders}"
    assert "import _shared" in src


def test_the_ast_check_can_actually_fire():
    """Negative control for the assertion above — an instrument that cannot go red
    is not a check. Feeds it a module that DOES open its own connection."""
    tree = ast.parse("import sqlite3\ndb = sqlite3.connect('x')\n")
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "connect"]
    assert found, "the AST pattern must match a real offender, or it proves nothing"


# --------------------------------------------------------------------------- #
# criterion 1 / CLI — exactly one artifact, and the two empty cases differ
# --------------------------------------------------------------------------- #
def test_cli_writes_exactly_one_artifact(tmp_path, monkeypatch):
    dbp = tmp_path / "f.db"
    _build_db(dbp)
    out = tmp_path / "art.ndjson"
    rc = E.main([str("s1"), "-o", str(out), "--db", str(dbp)])
    assert rc == 0
    assert out.exists()
    assert len(list(tmp_path.glob("*.ndjson"))) == 1
    assert len(out.read_text().splitlines()) == 2


def test_unknown_session_and_empty_session_are_different_exit_codes(tmp_path):
    dbp = tmp_path / "g.db"
    db = _build_db(dbp)
    db.execute("INSERT INTO message VALUES (?,?,?,?,?)",
               ("m2", "s2", 1, 1, json.dumps({})))
    db.execute("INSERT INTO session (id, project_id, directory, title, time_created, "
               "time_updated) VALUES (?,?,?,?,?,?)", ("s2", "p", "/d", "t", 1, 1))
    db.commit()
    assert E.main(["nope", "--db", str(dbp)]) == 3, "unknown session"
    assert E.main(["s2", "--db", str(dbp)]) == 4, "known session, no parts"


# --------------------------------------------------------------------------- #
# criterion 6 — read-only
# --------------------------------------------------------------------------- #
def test_shared_opens_read_only(tmp_path):
    dbp = tmp_path / "h.db"
    _build_db(dbp)
    import _shared as S
    conn = S.get_db(dbp)
    assert conn is not None
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE nope (x INTEGER)")


# --------------------------------------------------------------------------- #
# criterion 5 — the docstring names the omissions
# --------------------------------------------------------------------------- #
def test_docstring_names_what_is_excluded():
    doc = E.__doc__ or ""
    for token in ("tool-output/", "snapshot/", "storage/"):
        assert token in doc, f"the docstring must name {token} as excluded"
