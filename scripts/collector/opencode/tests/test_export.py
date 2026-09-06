"""Tests for opencode/export.py — one canonical artifact per session.

Run: python -m pytest scripts/collector/opencode/tests/test_export.py -v

🔴 THE DETERMINISM TESTS USE A FIXTURE WITH A DELIBERATE `time_created` TIE.
Measured store-wide 2026-09-06 (691 sessions / 17,679 messages / 77,671 parts):
**5 part tie-groups covering 10 rows in 5 sessions**, zero message-level ties. So
about 1 part in 7,767 — rare enough that a sampled session will not supply one,
which is why the fixture manufactures it. (An earlier revision of this docstring
said "zero ties … real data cannot exhibit the bug" from a 3.7% sample stated at
host scope. It was false, and it argued for deleting the sort.)
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

COLLECTOR = Path(__file__).resolve().parent.parent.parent
OPENCODE = COLLECTOR / "opencode"
for _p in (str(COLLECTOR), str(OPENCODE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import export as E  # noqa: E402


def _build_db(path: Path, *, tie: bool = False, two_messages: bool = False,
              text_payload: str | None = None) -> sqlite3.Connection:
    """A minimal store with the three tables `_shared` reads.

    `tie=True` gives two parts of one message an IDENTICAL `time_created`, with
    ids that sort opposite to insertion order. `two_messages=True` does the same
    one level up, for the message sequence.
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
    # 'mz' inserted FIRST, must sort SECOND when the message times tie.
    if two_messages:
        db.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                   ("mz", "s1", 1000, 1000, json.dumps({"role": "assistant"})))
        db.execute("INSERT INTO message VALUES (?,?,?,?,?)",
                   ("ma", "s1", 1000, 1000, json.dumps({"role": "user"})))
        for mid in ("mz", "ma"):
            db.execute("INSERT INTO part VALUES (?,?,?,?,?,?)",
                       (f"p-{mid}", mid, "s1", 3000, 3000,
                        json.dumps({"type": "text", "text": mid})))
        db.commit()
        db.row_factory = sqlite3.Row
        return db

    db.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        ("m1", "s1", 1000, 1000, json.dumps({"role": "assistant"})),
    )
    t_b = 2000 if tie else 2001
    db.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        ("zzz", "m1", "s1", t_b, t_b, json.dumps({"type": "tool", "tool": "bash",
                                                  "state": {"out": "second"}})),
    )
    db.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        ("aaa", "m1", "s1", 2000, 2000,
         json.dumps({"type": "text", "text": text_payload if text_payload else "first"})),
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
    """🔴 The task body's ASSUMPTION was that `text` carries the receipts.

    Measured false store-wide: **0 of 21,749** tool parts have `text`; their
    content is in `_data`/`state`. An exporter serialising `text` alone ships an
    artifact with zero tool calls in it, so this pins the field that carries them.
    """
    db = _build_db(tmp_path / "b.db")
    recs = [json.loads(ln) for ln in E.export_session(db, "s1").splitlines()]
    tool = [r for r in recs if r["part"]["type"] == "tool"][0]
    assert tool["part"]["_data"]["state"] == {"out": "second"}, "the payload survives"
    assert tool["part"]["tool"] == "bash"


# --------------------------------------------------------------------------- #
# criterion 3 — byte-determinism, on the ties real data will not supply
# --------------------------------------------------------------------------- #
def test_two_runs_are_byte_identical(tmp_path):
    db = _build_db(tmp_path / "c.db")
    a = E.export_session(db, "s1")
    b = E.export_session(db, "s1")
    assert hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest()


def test_ordering_is_total_when_PART_time_created_ties(tmp_path):
    db = _build_db(tmp_path / "d.db", tie=True)
    ids = [json.loads(ln)["part"]["id"]
           for ln in E.export_session(db, "s1").splitlines()]
    assert ids == ["aaa", "zzz"], (
        f"parts sharing a time_created must order by id, not SQLite row order; got {ids}")


def test_ordering_is_total_when_MESSAGE_time_created_ties(tmp_path):
    """The message half of `_order_key`, which nothing covered before.

    A one-message fixture leaves the message sort unexercised: deleting it was a
    SURVIVING mutant.
    """
    db = _build_db(tmp_path / "d2.db", two_messages=True)
    mids = [json.loads(ln)["message"]["id"]
            for ln in E.export_session(db, "s1").splitlines()]
    assert mids == ["ma", "mz"], (
        f"messages sharing a time_created must order by id; got {mids}")


def test_the_tie_fixtures_actually_tie(tmp_path):
    """Positive control: if the fixtures do not tie, both ordering tests are vacuous."""
    db = _build_db(tmp_path / "e.db", tie=True)
    ptimes = {json.loads(ln)["part"]["time_created"]
              for ln in E.export_session(db, "s1").splitlines()}
    assert len(ptimes) == 1, "the part fixture must actually tie"
    db2 = _build_db(tmp_path / "e2.db", two_messages=True)
    mtimes = {json.loads(ln)["message"]["time_created"]
              for ln in E.export_session(db2, "s1").splitlines()}
    assert len(mtimes) == 1, "the message fixture must actually tie"


def test_sort_keys_survives_differing_dict_insertion_order():
    """`sort_keys=True` was asserted by NOTHING — deleting it left the suite green.

    Two records with identical content but opposite key INSERTION order must
    render to identical bytes. Without `sort_keys` json.dumps preserves insertion
    order and these two differ.
    """
    a = {"session": {}, "message": {}, "part": {"b": 1, "a": 2}}
    b = {"session": {}, "message": {}, "part": {"a": 2, "b": 1}}
    assert list(a["part"]) != list(b["part"]), "the fixture must differ in key order"
    assert E.render(iter([a])) == E.render(iter([b])), (
        "sort_keys must make key insertion order irrelevant")


def test_mixed_type_time_created_does_not_raise(tmp_path):
    """`int < str` raises in Python 3; the SQL ORDER BY this replaced could not.

    Live data is `integer` throughout, so this guards schema drift in a module
    whose base layer promises to tolerate it.
    """
    db = _build_db(tmp_path / "mt.db")
    db.execute("UPDATE part SET time_created = '2026-01-01' WHERE id = 'zzz'")
    db.commit()
    text = E.export_session(db, "s1")          # must not raise TypeError
    assert len(text.splitlines()) == 2


# --------------------------------------------------------------------------- #
# encoding — both hazards are latent (0 of 77,671 live parts) and destructive
# --------------------------------------------------------------------------- #
def test_a_lone_surrogate_does_not_destroy_the_output_file(tmp_path):
    """`ensure_ascii=False` raised UnicodeEncodeError AFTER truncating the target."""
    dbp = tmp_path / "sur.db"
    _build_db(dbp, text_payload="broken \ud83d emoji")
    out = tmp_path / "art.ndjson"
    out.write_text("PREVIOUS GOOD ARTIFACT\n")
    rc = E.main(["s1", "-o", str(out), "--db", str(dbp)])
    assert rc == 0
    assert out.read_text().strip() != "PREVIOUS GOOD ARTIFACT", "it should have been replaced"
    assert len(out.read_text().splitlines()) == 2


def test_line_separators_do_not_shatter_records(tmp_path):
    """U+2028/2029/0085 passed through raw make `splitlines()` yield unparseable
    fragments while the file is still valid NDJSON — so every consumer written to
    this suite's own idiom breaks. Escapes are explicit: a literal in source is
    invisible and would not survive a copy-paste."""
    payload = "a\u2028b\u2029c\u0085d"
    assert len(payload.splitlines()) == 4, "the payload must really be splitline-hostile"
    db = _build_db(tmp_path / "ls.db", text_payload=payload)
    text = E.export_session(db, "s1")
    assert len(text.splitlines()) == 2, "one line per record, even with U+2028/2029/0085"
    for ln in text.splitlines():
        json.loads(ln)


# --------------------------------------------------------------------------- #
# criterion 4 — no second reader. ONE implementation, driven by both the guard
# and its control, so disarming the guard cannot leave the control green.
# --------------------------------------------------------------------------- #
def _connect_offenders(src: str) -> list[str]:
    """Every way this module could open a database of its own.

    🔴 Shared by the guard AND its negative control on purpose. An earlier revision
    re-implemented the pattern inside the control, so mutating the guard's own
    matcher left the suite GREEN — a control that validates a COPY of the
    instrument certifies nothing about the instrument.
    """
    tree = ast.parse(src)
    bad = []
    aliases = {"connect"}
    for node in ast.walk(tree):
        # `from sqlite3 import connect as _c` — record the local name
        if isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            for a in node.names:
                if a.name == "connect":
                    aliases.add(a.asname or a.name)
        # `getattr(sqlite3, "conn" + "ect")`
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "getattr":
            bad.append("getattr on a module — could resolve connect dynamically")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None)
            if name in aliases:
                bad.append(f"call to {name}")
    return bad


def test_export_opens_no_connection_of_its_own():
    src = (OPENCODE / "export.py").read_text()
    assert not _connect_offenders(src), "export.py must take its connection from _shared"
    assert "import _shared as S" in src


@pytest.mark.parametrize("bad_src", [
    "import sqlite3\ndb = sqlite3.connect('x')\n",
    "from sqlite3 import connect as _c\ndb = _c('x')\n",
    "import sqlite3\ndb = getattr(sqlite3, 'conn' + 'ect')('x')\n",
])
def test_the_offender_detector_can_actually_fire(bad_src):
    """Negative control, driven through the SAME function the guard uses.

    All three shapes are refactors a maintainer could plausibly reach for; the
    first was the only one an earlier revision caught.
    """
    assert _connect_offenders(bad_src), f"must flag: {bad_src!r}"


# --------------------------------------------------------------------------- #
# criterion 1 / CLI — one artifact, and the empty cases are distinguishable
# --------------------------------------------------------------------------- #
def test_cli_writes_exactly_one_artifact(tmp_path):
    dbp = tmp_path / "f.db"
    _build_db(dbp)
    out = tmp_path / "art.ndjson"
    assert E.main(["s1", "-o", str(out), "--db", str(dbp)]) == 0
    assert len(list(tmp_path.glob("*.ndjson"))) == 1
    assert len(out.read_text().splitlines()) == 2


def test_unknown_session_and_empty_session_are_different_exit_codes(tmp_path):
    dbp = tmp_path / "g.db"
    db = _build_db(dbp)
    db.execute("INSERT INTO session (id, project_id, directory, title, time_created, "
               "time_updated) VALUES (?,?,?,?,?,?)", ("s2", "p", "/d", "t", 1, 1))
    db.commit()
    assert E.main(["nope", "--db", str(dbp)]) == E.EXIT_NO_SUCH_SESSION
    assert E.main(["s2", "--db", str(dbp)]) == E.EXIT_SESSION_EMPTY


def test_an_unreadable_store_is_not_reported_as_a_typo(tmp_path):
    """🔴 `_shared` swallows OperationalError and returns empty iterators, so a
    renamed table used to surface as `no such session` — blaming the caller for a
    broken store. 11 of 691 live sessions genuinely have zero parts, so rc 4 is a
    code that really fires and must not be overloaded."""
    dbp = tmp_path / "drift.db"
    db = _build_db(dbp)
    db.execute("ALTER TABLE session RENAME TO session_old")
    db.commit()
    db.close()
    assert E.main(["s1", "--db", str(dbp)]) == E.EXIT_STORE_UNREADABLE

    notdb = tmp_path / "not.db"
    notdb.write_text("this is not a sqlite database at all")
    assert E.main(["s1", "--db", str(notdb)]) == E.EXIT_STORE_UNREADABLE


# --------------------------------------------------------------------------- #
# criterion 6 — read-only, plus the artifact's own permissions
# --------------------------------------------------------------------------- #
def test_shared_opens_read_only(tmp_path):
    dbp = tmp_path / "h.db"
    _build_db(dbp)
    import _shared as S
    conn = S.get_db(dbp)
    assert conn is not None
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE nope (x INTEGER)")


def test_artifact_is_0600_and_refuses_to_follow_a_symlink(tmp_path):
    """The source DB is 0600 and the artifact carries the same content; the naive
    write produced 0644 and followed a symlink onto its target."""
    dbp = tmp_path / "perm.db"
    _build_db(dbp)
    out = tmp_path / "art.ndjson"
    assert E.main(["s1", "-o", str(out), "--db", str(dbp)]) == 0
    mode = stat.S_IMODE(os.stat(out).st_mode)
    assert mode == 0o600, f"artifact must not be world-readable, got {oct(mode)}"

    # 🔴 Assert the PROPERTY (the target is not overwritten), not a MECHANISM
    # (that it raises). An earlier revision of this test demanded OSError and
    # failed against a correct implementation: writing to a temp file and
    # `os.replace`-ing it REPLACES THE SYMLINK ITSELF, which is the safe outcome
    # and does not raise. The naive `write_text` followed the link and destroyed
    # the target — that is what must stay fixed.
    victim = tmp_path / "victim.txt"
    victim.write_text("DO NOT OVERWRITE")
    link = tmp_path / "link.ndjson"
    link.symlink_to(victim)
    assert E.main(["s1", "-o", str(link), "--db", str(dbp)]) == 0
    assert victim.read_text() == "DO NOT OVERWRITE", "the symlink target must survive"
    assert not link.is_symlink(), "the symlink is replaced, not followed"
    assert stat.S_IMODE(os.stat(link).st_mode) == 0o600


# --------------------------------------------------------------------------- #
# criterion 5 — the docstring names the omissions
# --------------------------------------------------------------------------- #
def test_docstring_names_what_is_excluded():
    doc = E.__doc__ or ""
    for token in ("tool-output/", "snapshot/", "storage/"):
        assert token in doc, f"the docstring must name {token} as excluded"
    assert "NO parts produces NO record" in doc, (
        "a message with zero parts is invisible in the artifact — 38 of 17,679 "
        "live messages — and the docstring must say so")
