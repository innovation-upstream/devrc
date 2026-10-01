"""Tests for opencode/sent.py — the user-sent-messages display CLI.

Run: python -m pytest scripts/collector/opencode/tests/test_sent.py -v

🔴 Fixture times are FIXED literals and expectations are computed
independently (same datetime call a human would write), never by calling the
module under test — restating the implementation's own helper as the
expectation is how a broken formatter goes unnoticed.

🔴 THE TIE FIXTURE IS DELIBERATE: `_shared` orders by `time_created` alone,
which is not a total order, and `sent.py` re-sorts on `export._order_key`.
A sampled session will not supply a tie (measured in test_export.py's
docstring: ~1 part in 7,767), so the fixture manufactures one — with ids that
sort OPPOSITE to insertion order, so a missing re-sort cannot pass silently.
"""
from __future__ import annotations

import ast
import datetime
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

COLLECTOR = Path(__file__).resolve().parent.parent.parent
OPENCODE = COLLECTOR / "opencode"
for _p in (str(COLLECTOR), str(OPENCODE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import export as X  # noqa: E402
import sent as SE  # noqa: E402


def _build_db(path: Path, *, with_part_table: bool = True) -> sqlite3.Connection:
    """A minimal store with the tables `_shared` reads (and the tests mutate)."""
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
        """
    )
    if with_part_table:
        db.execute(
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,"
            " time_created INTEGER, time_updated INTEGER, data TEXT)"
        )
    db.row_factory = sqlite3.Row
    return db


def _add_session(db, sid="s1", title="t", created=1000, updated=1000):
    db.execute(
        "INSERT INTO session (id, project_id, directory, title, time_created,"
        " time_updated) VALUES (?,?,?,?,?,?)",
        (sid, "p1", "/tmp/proj", title, created, updated),
    )


def _add_message(db, mid, sid, ts, data):
    db.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (mid, sid, ts, ts, json.dumps(data)),
    )


def _add_part(db, pid, mid, sid, ts, data):
    db.execute(
        "INSERT INTO part VALUES (?,?,?,?,?,?)",
        (pid, mid, sid, ts, ts, json.dumps(data)),
    )


def _user(mid, ts, agent="build"):
    return {"role": "user", "agent": agent, "time": {"created": ts}}


def _text(text):
    return {"type": "text", "text": text}


def _expected_local(epoch_ms: int) -> str:
    """The wall-clock string, computed the way a reader would expect it."""
    return (
        datetime.datetime.fromtimestamp(epoch_ms / 1000, tz=datetime.timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M")
    )


# --------------------------------------------------------------------------- #
# show
# --------------------------------------------------------------------------- #
def test_show_prints_user_messages_in_send_order(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "ma", "s1", 1700000001000, _user("ma", 1700000001000))
    _add_part(db, "pa", "ma", "s1", 1700000001000, _text("first question"))
    _add_message(db, "mb", "s1", 1700000009000, _user("mb", 1700000009000))
    _add_part(db, "pb", "mb", "s1", 1700000009000, _text("second question"))
    # an assistant turn BETWEEN them must not appear, only order the output
    _add_message(db, "mc", "s1", 1700000005000, {"role": "assistant", "agent": "build"})
    _add_part(db, "pc", "mc", "s1", 1700000005000, _text("answer"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"{_expected_local(1700000001000)}  first question",
        f"{_expected_local(1700000009000)}  second question",
    ]


def test_subagent_injected_pseudo_user_hidden_by_default(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000, "build"))
    _add_part(db, "p1", "m1", "s1", 1700000001000, _text("typed by human"))
    _add_message(db, "m2", "s1", 1700000002000, _user("m2", 1700000002000, "explore"))
    _add_part(db, "p2", "m2", "s1", 1700000002000, _text("subagent instruction"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "typed by human" in out
    assert "subagent instruction" not in out


def test_all_agents_shows_injected_messages_tagged(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000, "explore"))
    _add_part(db, "p1", "m1", "s1", 1700000001000, _text("subagent instruction"))
    db.commit()
    rc = SE.main(["show", "s1", "--all-agents", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[explore]" in out
    assert "subagent instruction" in out


def test_user_message_text_is_joined_across_all_text_parts(tmp_path, capsys):
    """A dispatch brief arrives as a SECOND text part; dropping it would show
    half of what the user sent. `tailer.extract_text` takes only the first —
    that is the telemetry view, not this one."""
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000))
    _add_part(db, "p1", "m1", "s1", 1700000001000, _text("typed instruction"))
    _add_part(db, "p2", "m1", "s1", 1700000001000, _text("attached brief"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "typed instruction" in out
    assert "attached brief" in out


def test_non_text_parts_never_render(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000))
    _add_part(db, "p1", "m1", "s1", 1700000001000, _text("typed"))
    _add_part(db, "p2", "m1", "s1", 1700000001000, {"type": "file", "mime": "x"})
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == f"{_expected_local(1700000001000)}  typed"


def test_message_without_any_text_part_is_hidden_and_counted(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000))
    _add_part(db, "p1", "m1", "s1", 1700000001000, {"type": "file", "mime": "x"})
    _add_message(db, "m2", "s1", 1700000002000, _user("m2", 1700000002000))
    _add_part(db, "p2", "m2", "s1", 1700000002000, _text("typed"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    err = capsys.readouterr().err
    assert "1 user message(s) had no text part" in err


def test_tied_time_created_is_a_total_order(tmp_path, capsys):
    """Two user messages sharing a `time_created`, inserted with ids that sort
    opposite to insertion order. Without the `_order_key` re-sort the output
    order depends on SQLite tie behaviour — bytes would change between runs."""
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "mz", "s1", 1700000001000, _user("mz", 1700000001000))
    _add_part(db, "pz", "mz", "s1", 1700000001000, _text("second by id"))
    _add_message(db, "ma", "s1", 1700000001000, _user("ma", 1700000001000))
    _add_part(db, "pa", "ma", "s1", 1700000001000, _text("first by id"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{_expected_local(1700000001000)}  first by id",
        f"{_expected_local(1700000001000)}  second by id",
    ]


def test_multiline_message_stays_one_output_line(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000))
    _add_part(db, "p1", "m1", "s1", 1700000001000, _text("line one\nline two"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    assert "line one" in out[0] and "line two" in out[0]


# --------------------------------------------------------------------------- #
# exit codes — the same family export.py defined, for the same reasons
# --------------------------------------------------------------------------- #
def test_no_such_session_is_exit_3(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db, sid="other")
    db.commit()
    rc = SE.main(["show", "missing", "--db", str(tmp_path / "store.db")])
    assert rc == X.EXIT_NO_SUCH_SESSION


def test_session_with_no_user_messages_is_exit_4(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1000, {"role": "assistant", "agent": "build"})
    _add_part(db, "p1", "m1", "s1", 1000, _text("answer"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == X.EXIT_SESSION_EMPTY


def test_missing_store_is_exit_2(tmp_path, capsys):
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "nonexistent.db")])
    assert rc == X.EXIT_NO_DB


def test_unreadable_store_is_not_reported_as_no_such_session(tmp_path, capsys):
    """A store missing the part table must NOT read as "no such session" —
    that wording blames the caller for a broken store (export.py's reason)."""
    db = _build_db(tmp_path / "store.db", with_part_table=False)
    _add_session(db)
    db.commit()
    db.close()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == X.EXIT_STORE_UNREADABLE


# --------------------------------------------------------------------------- #
# the guard: sent.py opens NO connection of its own
# --------------------------------------------------------------------------- #
def _connect_callers(src: str) -> list[str]:
    """Every call site that could open a sqlite connection, by spelling.

    Compact version of test_export.py's `_connect_offenders`, covering the
    shapes that matter here: `import sqlite3` (the module must not appear at
    all — it has no legitimate use once the connection comes from `_shared`),
    `sqlite3.connect(...)` / bare `connect(...)` calls, and the assignment
    alias (`_c = sqlite3.connect`).
    """
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name == "sqlite3"]
        if isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
            offenders.append(node.module)
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "connect":
                offenders.append(ast.dump(f))
            elif isinstance(f, ast.Name) and f.id == "connect":
                offenders.append(ast.dump(f))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "connect":
            offenders.append(ast.dump(node))
    return offenders


def test_sent_opens_no_connection_of_its_own():
    src = (OPENCODE / "sent.py").read_text()
    assert not _connect_callers(src), "sent.py must take its connection from _shared"


@pytest.mark.parametrize("bad_src", [
    "import sqlite3\ndb = sqlite3.connect('x')\n",
    "from sqlite3 import connect as _c\ndb = _c('x')\n",
    "import sqlite3\n_c = sqlite3.connect\ndb = _c('x')\n",
])
def test_connection_guard_flags_each_spelling(bad_src):
    """Negative control for the guard above — it must actually fire."""
    assert _connect_callers(bad_src), f"guard must flag: {bad_src!r}"


def test_connection_guard_passes_the_shared_pattern():
    """Positive control — the sanctioned spelling must NOT be flagged."""
    ok_src = "import _shared as S\ndb = S.get_db(None)\n"
    assert not _connect_callers(ok_src)


# --------------------------------------------------------------------------- #
# the PATH shim
# --------------------------------------------------------------------------- #
def test_path_shim_execs_sent_help():
    """`scripts/oc-sent --help` must exit 0 and print usage — proves the shim
    resolves the symlink to a real path whose sibling imports work."""
    shim = COLLECTOR.parent / "oc-sent"
    proc = subprocess.run(
        ["bash", str(shim), "--help"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    assert "oc-sent" in proc.stdout
