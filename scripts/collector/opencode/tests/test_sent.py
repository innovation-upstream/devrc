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


def _add_session(db, sid="s1", title="t", created=1000, updated=1000,
                 directory="/tmp/proj"):
    db.execute(
        "INSERT INTO session (id, project_id, directory, title, time_created,"
        " time_updated) VALUES (?,?,?,?,?,?)",
        (sid, "p1", directory, title, created, updated),
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
    that is the telemetry view, not this one.

    🔴 The assertion is the EXACT line, not membership: both parts share one
    `time_created`, so the output is a (time_created, id) tie resolved by
    `_order_key`. 🔴 The parts are inserted p2 BEFORE p1 — insertion order
    opposite to id order — so BOTH mutants die: deleting the re-sort (SQLite's
    tie order = insertion order) and reversing it. The round-2 audit found
    the previous fixture inserted p1 first, where deletion survives the whole
    suite green; that prose claimed "pinned both directions" and was false.
    """
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000))
    _add_part(db, "p2", "m1", "s1", 1700000001000, _text("attached brief"))
    _add_part(db, "p1", "m1", "s1", 1700000001000, _text("typed instruction"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [f"{_expected_local(1700000001000)}  typed instruction ⏎ attached brief"]


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
    assert "no displayable user messages" in capsys.readouterr().err


def test_only_non_build_session_names_the_filter_and_the_escape_hatch(
    tmp_path, capsys
):
    """A dispatched-subagent session has user messages that are ALL non-build.
    Reporting `session has no user messages` there is FALSE — the messages
    exist, the default filter hid them — and it is the case where the answer
    (the dispatch brief) is exactly what an agent asking for this session
    wants. Round-1 finding 2, reproduced live store-wide: 60 of 223
    sessions-with-user-messages were all-non-build, 0 mixed."""
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000, "explore"))
    _add_part(db, "p1", "m1", "s1", 1700000001000, _text("dispatch brief"))
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == X.EXIT_SESSION_EMPTY
    err = capsys.readouterr().err
    assert "--all-agents" in err
    assert "1 non-build hidden" in err


def test_mixed_textless_and_non_build_counts_both_in_exit_4(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db)
    # non-build user message WITH text → agent-filtered
    _add_message(db, "m1", "s1", 1700000001000, _user("m1", 1700000001000, "probe"))
    _add_part(db, "p1", "m1", "s1", 1700000001000, _text("probe injected"))
    # build user message with NO text part → textless-hidden
    _add_message(db, "m2", "s1", 1700000002000, _user("m2", 1700000002000, "build"))
    _add_part(db, "p2", "m2", "s1", 1700000002000, {"type": "file", "mime": "x"})
    db.commit()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == X.EXIT_SESSION_EMPTY
    err = capsys.readouterr().err
    assert "--all-agents" in err
    assert "no text part" in err


# --------------------------------------------------------------------------- #
# the store-unreadable wraps round 1 added — a store we cannot READ is rc 5,
# never a traceback
# --------------------------------------------------------------------------- #
def test_db_path_to_a_directory_is_exit_5_not_a_traceback(tmp_path, capsys):
    """`--db <directory>`: get_db opens a URI connection that raises. export.py
    wraps this identical call; round 1 found sent.py had dropped the wrap."""
    rc = SE.main(["show", "s1", "--db", str(tmp_path)])
    assert rc == X.EXIT_STORE_UNREADABLE
    assert "cannot open store" in capsys.readouterr().err


def test_session_table_missing_reader_columns_is_exit_5(tmp_path, capsys):
    """`_store_is_readable` probes only (id, time_created) on session; a store
    whose session table carries EXACTLY those passes the probe, and the
    `known` scan then reads columns that do not exist. export.py compensates
    with a wrap round 1 found missing here — a future schema drift must
    produce rc 5, not an IndexError traceback."""
    db = sqlite3.connect(tmp_path / "store.db")
    db.executescript(
        """
        CREATE TABLE session (id TEXT, time_created INTEGER);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT,
                              time_created INTEGER, time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
                           time_created INTEGER, time_updated INTEGER, data TEXT);
        """
    )
    db.execute("INSERT INTO session VALUES ('s1', 1000)")
    db.commit()
    db.close()
    rc = SE.main(["show", "s1", "--db", str(tmp_path / "store.db")])
    assert rc == X.EXIT_STORE_UNREADABLE
    assert "store read failed" in capsys.readouterr().err


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
# here — the tmux-keybind entry point: cwd → session resolution
# --------------------------------------------------------------------------- #
def _user_msg_with_text(db, mid, sid, ts, text, agent="build"):
    _add_message(db, mid, sid, ts, _user(mid, ts, agent))
    _add_part(db, f"p-{mid}", mid, sid, ts, _text(text))


def test_here_exact_directory_match(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db, sid="s1", title="devrc work", updated=5000,
                 directory="/home/zach/workspace/devrc")
    _add_session(db, sid="s2", title="other repo", updated=9000,
                 directory="/home/zach/workspace/other")
    _user_msg_with_text(db, "m1", "s1", 1700000001000, "typed in devrc")
    _user_msg_with_text(db, "m2", "s2", 1700000002000, "typed in other")
    db.commit()
    rc = SE.main(["here", "/home/zach/workspace/devrc",
                  "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out
    # newest session is s2 — the exact match must beat recency
    assert out.startswith("# devrc work  s1\n")
    assert "typed in devrc" in out
    assert "typed in other" not in out


def test_here_ancestor_match_prefers_the_longest(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db, sid="s-root", title="workspace root", updated=9000,
                 directory="/home/zach/workspace")
    _add_session(db, sid="s-proj", title="the project", updated=1000,
                 directory="/home/zach/workspace/devrc")
    _user_msg_with_text(db, "m1", "s-proj", 1700000001000, "project prompt")
    _user_msg_with_text(db, "m2", "s-root", 1700000002000, "workspace prompt")
    db.commit()
    rc = SE.main(["here", "/home/zach/workspace/devrc/scripts/deep",
                  "--db", str(tmp_path / "store.db")])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("# the project  s-proj\n")
    assert "project prompt" in out
    assert "workspace prompt" not in out


def test_here_fallback_is_newest_with_a_stderr_note(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db, sid="s-old", title="old", created=1000, updated=1000,
                 directory="/a")
    _add_session(db, sid="s-new", title="new", created=5000, updated=9000,
                 directory="/b")
    _user_msg_with_text(db, "m1", "s-new", 1700000001000, "newest prompt")
    db.commit()
    rc = SE.main(["here", "/totally/elsewhere",
                  "--db", str(tmp_path / "store.db")])
    assert rc == 0
    cap = capsys.readouterr()
    assert cap.out.startswith("# new  s-new\n")
    assert "newest prompt" in cap.out
    assert "no session directory matches /totally/elsewhere" in cap.err


def test_here_with_no_sessions_at_all_is_exit_3(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    db.commit()
    rc = SE.main(["here", "/anywhere", "--db", str(tmp_path / "store.db")])
    assert rc == X.EXIT_NO_SUCH_SESSION
    assert "no opencode sessions found" in capsys.readouterr().err


def test_here_session_with_no_messages_names_the_filter(tmp_path, capsys):
    db = _build_db(tmp_path / "store.db")
    _add_session(db, sid="s1", directory="/tmp/proj")
    _user_msg_with_text(db, "m1", "s1", 1700000001000, "subagent brief",
                        agent="explore")
    db.commit()
    rc = SE.main(["here", "/tmp/proj", "--db", str(tmp_path / "store.db")])
    assert rc == X.EXIT_SESSION_EMPTY
    cap = capsys.readouterr()
    assert "--all-agents" in cap.err
    # the header still names the session it resolved to
    assert cap.out.startswith("# t  s1\n")


def test_here_trailing_slashes_match_exactly(tmp_path, capsys):
    """The keybind passes tmux's `#{pane_current_path}`, which can carry a
    trailing slash depending on how the pane got there; the stored directory
    may differ the same way. Both are normalised before comparison."""
    db = _build_db(tmp_path / "store.db")
    _add_session(db, sid="s1", directory="/tmp/proj/")
    _user_msg_with_text(db, "m1", "s1", 1700000001000, "typed")
    db.commit()
    rc = SE.main(["here", "/tmp/proj", "--db", str(tmp_path / "store.db")])
    assert rc == 0
    assert "typed" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the guard: sent.py opens NO connection of its own
# --------------------------------------------------------------------------- #
def _connect_callers(src: str) -> list[str]:
    """Every call site that could open a sqlite connection, by spelling.

    Compact version of test_export.py's `_connect_offenders`. The MODULE may be
    imported — round 1's fixes need `sqlite3.DatabaseError` for the store-wrap
    exit codes, exactly export.py's own use — so the guard flags call SHAPES:
    `sqlite3.connect(...)` / bare `connect(...)` calls, the assignment alias
    (`_c = sqlite3.connect`), importing the connect FUNCTION from any sqlite
    module path (`from sqlite3 import connect`, `from sqlite3.dbapi2 import
    connect`), `getattr(<sqlite3-thing>, 'connect')` under any alias or
    attribute chain and via a from-imported submodule binding (round-3/4
    findings), and `partial(...connect)`. The reader connection comes from
    `_shared`; `getattr(sqlite3, 'DatabaseError')` and friends must NOT flag
    (positive controls below). Disclosed holes, each statically undecidable
    for a source AST: a VARIABLE second argument; a local shim module
    re-exporting connect; a receiver that is not lexically a sqlite name
    (`sys.modules['sqlite3']` — `_dotted` returns "" for Subscript receivers
    and the guard passes it).
    """
    tree = ast.parse(src)
    offenders = []
    # names that REFER to a sqlite-family module. From `import sqlite3 [as X]`
    # and `import sqlite3.dbapi2 [as X]`: the bound name (X or the full path).
    # From `from sqlite3 import dbapi2`: the bound name is a SUBMODULE of
    # sqlite3, so its root is a sqlite-thing too (round-4 finding: the map
    # previously walked ast.Import only, and `from sqlite3 import dbapi2`
    # bound `dbapi2` past it).
    sqlite_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "sqlite3" or a.name.startswith("sqlite3."):
                    sqlite_names.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and (
            node.module == "sqlite3" or (node.module or "").startswith("sqlite3.")
        ):
            # a name bound FROM a sqlite-family module is treated as a sqlite
            # thing even when it is the DatabaseError class: getattr over it
            # asking for 'connect' is contrived, and over-flagging is the
            # fail-open direction for this guard
            sqlite_names.update(a.asname or a.name for a in node.names)

    def _dotted(node) -> str:
        """`sqlite3.dbapi2` from Attribute(Name('sqlite3'), 'dbapi2')."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return _dotted(node.value) + "." + node.attr
        return ""

    def _is_sqlite_receiver(node) -> bool:
        """Does the dotted receiver's ROOT name refer to a sqlite thing?

        The ROOT, not the whole dotted string: round-4's survivor
        `getattr(s3.dbapi2, 'connect')` after `import sqlite3 as s3` has
        dotted root `s3` — the previous whole-string match missed it.
        """
        dotted = _dotted(node)
        if not dotted:
            return False
        return dotted.split(".")[0] in sqlite_names or dotted.startswith("sqlite3.")

    for node in ast.walk(tree):
        # the MODULE import is legal (exception types, export.py's own use);
        # importing the connect FUNCTION by any alias is not — from any path
        # that resolves to the sqlite family, dbapi2 included
        if isinstance(node, ast.ImportFrom) and (
            node.module == "sqlite3" or
            (node.module or "").startswith("sqlite3.")
        ):
            offenders += [a.name for a in node.names if a.name == "connect"]
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "connect":
                offenders.append(ast.dump(f))
            elif isinstance(f, ast.Name) and f.id == "connect":
                offenders.append(ast.dump(f))
            # getattr(<sqlite3-thing>, 'connect') — flagged on the LITERAL
            # 'connect' attribute string only (getattr(mod, var) is statically
            # undecidable for a source AST and is disclosed, not caught). The
            # RECEIVER may be the module under any alias or an attribute chain
            # (sqlite3.dbapi2) — round-3 finding 1's two survivors.
            elif isinstance(f, ast.Name) and f.id == "getattr" and len(node.args) >= 2:
                a0, a1 = node.args[0], node.args[1]
                if _is_sqlite_receiver(a0) \
                        and isinstance(a1, ast.Constant) and a1.value == "connect":
                    offenders.append(ast.dump(node))
            # partial(<anything>.connect) — deferring the call is opening it
            elif isinstance(f, ast.Name) and f.id == "partial" and node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.Attribute) and a0.attr == "connect":
                    offenders.append(ast.dump(node))
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
    "import sqlite3\nopen_db = getattr(sqlite3, 'connect')\ndb = open_db('x')\n",
    "from sqlite3.dbapi2 import connect as _c\ndb = _c('x')\n",
    "import sqlite3 as s3\nfrom functools import partial\ndb = partial(s3.connect)('x')\n",
    "import sqlite3 as s3\nopen_db = getattr(s3, 'connect')\ndb = open_db('x')\n",
    "import sqlite3\nopen_db = getattr(sqlite3.dbapi2, 'connect')\ndb = open_db('x')\n",
    "import sqlite3 as s3\nopen_db = getattr(s3.dbapi2, 'connect')\ndb = open_db('x')\n",
    "from sqlite3 import dbapi2\nopen_db = getattr(dbapi2, 'connect')\ndb = open_db('x')\n",
])
def test_connection_guard_flags_each_spelling(bad_src):
    """Negative control for the guard above — it must actually fire. Rows 4-6
    are the round-2 finds (getattr, the dbapi2 path, the partial deferral);
    rows 7-8 are round-3 finding 1's survivors (aliased and attribute-chain
    getattr receivers); rows 9-10 are round-4 finding's survivors: the
    dotted-root miss (s3.dbapi2 after `import sqlite3 as s3`) and the
    from-imported submodule binding the map previously never walked."""
    assert _connect_callers(bad_src), f"guard must flag: {bad_src!r}"


def test_connection_guard_passes_the_shared_pattern():
    """Positive control — the sanctioned spelling must NOT be flagged, and a
    bare module import (exception types only, export.py's own use) too. Every
    guard ARM gets a benign twin so over-tightening is caught: getattr with a
    non-connect attribute, a dbapi2 import of a non-connect name, and a
    partial that defers something innocent."""
    ok_src = "import _shared as S\ndb = S.get_db(None)\n"
    assert not _connect_callers(ok_src)
    ok_import = "import sqlite3\nraise sqlite3.DatabaseError('x')\n"
    assert not _connect_callers(ok_import)
    ok_getattr = "import sqlite3\nerr_cls = getattr(sqlite3, 'DatabaseError')\n"
    assert not _connect_callers(ok_getattr)
    ok_alias = "import sqlite3 as s3\nc = getattr(s3, 'DatabaseError')\n"
    assert not _connect_callers(ok_alias)
    ok_dbapi2 = "from sqlite3.dbapi2 import DatabaseError\nraise DatabaseError('x')\n"
    assert not _connect_callers(ok_dbapi2)
    ok_partial = "from functools import partial\ndoubler = partial(print, 'x')\n"
    assert not _connect_callers(ok_partial)
    ok_assign = "import sqlite3\nerr_cls = sqlite3.DatabaseError\n" \
                "raise err_cls('x')\n"
    assert not _connect_callers(ok_assign)


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
