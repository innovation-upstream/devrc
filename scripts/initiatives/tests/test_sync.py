"""Unit tests for the PURE transform in scripts/initiatives/sync.py.

Offline: no live scan, no live DB. We feed `report_to_rows` a fixture `--json`
report dict and assert on the emitted insert-row dicts — epoch→timestamptz (UTC)
conversion, null handling (last_touch / telem_last / date), the JSONB payloads
(open_prs / open_investigations / docs), host tagging, momentum passthrough, and
an empty / telemetry-off report."""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sync  # noqa: E402


# A representative scan `--json` initiative (the shape sync.py consumes). Epochs are
# UNIX seconds; 2026-07-13 12:00:00Z == 1783944000.0.
_TOUCH_EPOCH = 1783944000.0  # 2026-07-13 12:00:00 UTC
_TELEM_EPOCH = 1783857600.0  # 2026-07-12 12:00:00 UTC


def _fixture_initiative(**over):
    ini = {
        "repo": "/home/zach/workspace/devrc",
        "slug": "initiatives-consolidation",
        "title": "Initiatives consolidation Phase 1",
        "date": "2026-07-13",
        "doc_mtime": 1768300000.0,
        "next_step": "eyeball the dry-run output",
        "open_investigations": ["does the router want a JOIN view?"],
        "current_doc": "/home/zach/workspace/devrc/claudedocs/handoff-x.md",
        "docs": [{"path": "/home/zach/workspace/devrc/claudedocs/handoff-x.md",
                  "date": "2026-07-13"}],
        "matching_branches": ["feat/initiatives"],
        "commits": 7,
        "commits_unknown": False,
        "last_commit": _TOUCH_EPOCH,
        "open_prs": [{"number": 135, "title": "feat: initiatives sync"}],
        "merged_prs": 2,
        "session_count": 3,
        "last_session": _TOUCH_EPOCH,
        "telem_events": 42,
        "telem_last": _TELEM_EPOCH,
        "last_touch": _TOUCH_EPOCH,
        "momentum": "active",
    }
    ini.update(over)
    return ini


def _fixture_report(**over):
    rep = {
        "days": 4,
        "telemetry_available": True,
        "tmux_enabled": False,
        "tmux_unmatched": [],
        "repos": ["/home/zach/workspace/devrc"],
        "by_repo": {"/home/zach/workspace/devrc": [_fixture_initiative()]},
        "catchall": {},
    }
    rep.update(over)
    return rep


# --- meta + host tagging ---------------------------------------------------- #
def test_meta_carries_days_and_telemetry():
    meta, _rows = sync.report_to_rows(_fixture_report(), host="workbench")
    assert meta == {"host": "workbench", "days_window": 4, "telemetry_available": True}


def test_host_tag_applied_to_every_row():
    _meta, rows = sync.report_to_rows(_fixture_report(), host="laptop")
    assert rows and all(r["host"] == "laptop" for r in rows)


def test_resolve_host_prefers_activity_host(monkeypatch):
    monkeypatch.setenv("ACTIVITY_HOST", "workbench")
    assert sync.resolve_host() == "workbench"


def test_resolve_host_defaults_to_workbench_when_hostname_is_nixos(monkeypatch):
    monkeypatch.delenv("ACTIVITY_HOST", raising=False)
    monkeypatch.setattr(sync.socket, "gethostname", lambda: "nixos")
    assert sync.resolve_host() == "workbench"


def test_resolve_host_uses_meaningful_hostname(monkeypatch):
    monkeypatch.delenv("ACTIVITY_HOST", raising=False)
    monkeypatch.setattr(sync.socket, "gethostname", lambda: "some-box")
    assert sync.resolve_host() == "some-box"


# --- epoch -> timestamptz (UTC) --------------------------------------------- #
def test_epoch_fields_convert_to_utc_datetimes():
    _meta, rows = sync.report_to_rows(_fixture_report(), host="workbench")
    r = rows[0]
    assert r["last_touch"] == datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    assert r["telem_last"] == datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    assert r["last_touch"].tzinfo is timezone.utc


def test_epoch_accepts_stringified_number():
    # --json runs with default=str; be robust to an epoch that arrives as a str.
    assert sync._epoch_to_dt("1783944000.0") == \
        datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def test_epoch_none_and_unparseable_become_none():
    assert sync._epoch_to_dt(None) is None
    assert sync._epoch_to_dt("not-a-number") is None


# --- null handling ---------------------------------------------------------- #
def test_null_last_touch_and_telem_last_become_none():
    ini = _fixture_initiative(last_touch=None, telem_last=None)
    rep = _fixture_report(by_repo={"/r": [ini]})
    _meta, rows = sync.report_to_rows(rep, host="workbench")
    assert rows[0]["last_touch"] is None
    assert rows[0]["telem_last"] is None


def test_null_doc_date_becomes_none():
    ini = _fixture_initiative(date=None)
    rep = _fixture_report(by_repo={"/r": [ini]})
    _meta, rows = sync.report_to_rows(rep, host="workbench")
    assert rows[0]["doc_date"] is None


def test_doc_date_parses_to_date_object():
    _meta, rows = sync.report_to_rows(_fixture_report(), host="workbench")
    assert rows[0]["doc_date"] == date(2026, 7, 13)


def test_malformed_doc_date_becomes_none():
    ini = _fixture_initiative(date="not-a-date")
    rep = _fixture_report(by_repo={"/r": [ini]})
    _meta, rows = sync.report_to_rows(rep, host="workbench")
    assert rows[0]["doc_date"] is None


# --- JSONB payloads + scalar passthrough ------------------------------------ #
def test_jsonb_fields_preserved_as_python_objects():
    _meta, rows = sync.report_to_rows(_fixture_report(), host="workbench")
    r = rows[0]
    assert r["open_prs"] == [{"number": 135, "title": "feat: initiatives sync"}]
    assert r["open_investigations"] == ["does the router want a JOIN view?"]
    assert r["docs"] == [{"path": "/home/zach/workspace/devrc/claudedocs/handoff-x.md",
                          "date": "2026-07-13"}]


def test_missing_jsonb_fields_default_to_empty_list():
    ini = _fixture_initiative()
    for k in ("open_prs", "open_investigations", "docs"):
        ini.pop(k, None)
    rep = _fixture_report(by_repo={"/r": [ini]})
    _meta, rows = sync.report_to_rows(rep, host="workbench")
    r = rows[0]
    assert r["open_prs"] == [] and r["open_investigations"] == [] and r["docs"] == []


def test_momentum_and_scalars_pass_through():
    _meta, rows = sync.report_to_rows(_fixture_report(), host="workbench")
    r = rows[0]
    assert r["momentum"] == "active"
    assert r["slug"] == "initiatives-consolidation"
    assert r["title"] == "Initiatives consolidation Phase 1"
    assert r["next_step"] == "eyeball the dry-run output"
    assert r["commits"] == 7 and r["merged_prs"] == 2
    assert r["session_count"] == 3 and r["telem_events"] == 42
    assert r["commits_unknown"] is False


def test_commits_unknown_true_passes_through():
    ini = _fixture_initiative(commits_unknown=True, commits=0)
    rep = _fixture_report(by_repo={"/r": [ini]})
    _meta, rows = sync.report_to_rows(rep, host="workbench")
    assert rows[0]["commits_unknown"] is True


def test_missing_int_fields_default_to_zero():
    ini = _fixture_initiative()
    for k in ("commits", "merged_prs", "session_count", "telem_events"):
        ini.pop(k, None)
    rep = _fixture_report(by_repo={"/r": [ini]})
    _meta, rows = sync.report_to_rows(rep, host="workbench")
    r = rows[0]
    assert r["commits"] == 0 and r["merged_prs"] == 0
    assert r["session_count"] == 0 and r["telem_events"] == 0


# --- multi-repo flattening -------------------------------------------------- #
def test_flattens_all_repos_into_one_row_list():
    rep = _fixture_report(by_repo={
        "/home/zach/workspace/devrc": [_fixture_initiative(slug="a"),
                                       _fixture_initiative(slug="b")],
        "/home/zach/workspace/homelab": [_fixture_initiative(
            repo="/home/zach/workspace/homelab", slug="c")],
    })
    _meta, rows = sync.report_to_rows(rep, host="workbench")
    assert len(rows) == 3
    assert {r["slug"] for r in rows} == {"a", "b", "c"}


# --- empty / telemetry-off report ------------------------------------------- #
def test_empty_report_yields_no_rows():
    rep = _fixture_report(by_repo={}, telemetry_available=False)
    meta, rows = sync.report_to_rows(rep, host="workbench")
    assert rows == []
    assert meta["telemetry_available"] is False


def test_telemetry_off_report_still_produces_rows():
    rep = _fixture_report(telemetry_available=False)
    meta, rows = sync.report_to_rows(rep, host="workbench")
    assert meta["telemetry_available"] is False
    assert len(rows) == 1


def test_missing_by_repo_key_is_tolerated():
    meta, rows = sync.report_to_rows({"days": 4, "telemetry_available": True},
                                     host="workbench")
    assert rows == []
    assert meta == {"host": "workbench", "days_window": 4, "telemetry_available": True}


# --- dry-run rendering (smoke: complete + non-crashing) --------------------- #
def test_render_dry_run_includes_count_table_and_full_json():
    meta, rows = sync.report_to_rows(_fixture_report(), host="workbench")
    text = sync.render_dry_run(meta, rows)
    assert "rows to insert: 1" in text
    assert "initiatives-consolidation" in text
    assert "full rows" in text
    # the isoformatted timestamp survives into the full JSON dump
    assert "2026-07-13T12:00:00+00:00" in text


def test_render_dry_run_flags_telemetry_off():
    meta, rows = sync.report_to_rows(_fixture_report(telemetry_available=False,
                                                     by_repo={}), host="workbench")
    text = sync.render_dry_run(meta, rows)
    assert "telemetry OFF" in text
    assert "rows to insert: 0" in text


# --------------------------------------------------------------------------- #
# I/O SQL tests via a fake psycopg2 connection (no port-forward, no Postgres).
# Assert on the emitted SQL: the advisory lock, index DDL, the view guard, the
# retention DELETE, and the snapshot/JSONB insert shape.
# --------------------------------------------------------------------------- #
class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 0
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self._conn.executed.append((norm, params))
        self.rowcount = 0
        self._result = None
        if "to_regclass('initiatives.current')" in norm:
            self._result = (self._conn.view_regclass,)
        elif "obj_description" in norm:
            self._result = (self._conn.view_comment,)
        elif "RETURNING id" in norm:
            self._result = (self._conn.next_snapshot_id,)
        elif norm.startswith("DELETE FROM initiatives.snapshots"):
            self.rowcount = self._conn.delete_rowcount

    def fetchone(self):
        return self._result


class _FakeConn:
    def __init__(self, *, view_regclass=None, view_comment=None,
                 next_snapshot_id=1, delete_rowcount=0):
        self.executed = []
        self.commits = 0
        self.view_regclass = view_regclass
        self.view_comment = view_comment
        self.next_snapshot_id = next_snapshot_id
        self.delete_rowcount = delete_rowcount

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1


def _sqls(conn):
    return [s for s, _ in conn.executed]


def test_ensure_schema_takes_advisory_lock_first():
    conn = _FakeConn()
    sync.ensure_schema(conn)
    first_sql, first_params = conn.executed[0]
    assert "pg_advisory_xact_lock" in first_sql
    assert first_params == (sync.SCHEMA_LOCK_KEY,)
    assert conn.commits == 1


def test_ensure_schema_creates_indexes():
    conn = _FakeConn()
    sync.ensure_schema(conn)
    joined = " ".join(_sqls(conn))
    assert "initiative_snapshot_repo_slug_snap_idx" in joined
    assert "(repo, slug, snapshot_id)" in joined
    assert "initiative_snapshot_snapshot_id_idx" in joined
    assert "snapshots_captured_at_idx" in joined


def test_ensure_schema_fk_cascades():
    conn = _FakeConn()
    sync.ensure_schema(conn)
    joined = " ".join(_sqls(conn))
    assert "REFERENCES initiatives.snapshots(id) ON DELETE CASCADE" in joined


def test_ensure_schema_creates_view_when_absent():
    conn = _FakeConn(view_regclass=None)
    sync.ensure_schema(conn)
    joined = " ".join(_sqls(conn))
    assert "CREATE OR REPLACE VIEW initiatives.current" in joined
    assert "COMMENT ON VIEW initiatives.current" in joined


def test_ensure_schema_skips_view_when_present_and_version_matches():
    conn = _FakeConn(view_regclass="initiatives.current",
                     view_comment=sync.VIEW_COMMENT)
    sync.ensure_schema(conn)
    joined = " ".join(_sqls(conn))
    assert "CREATE OR REPLACE VIEW initiatives.current" not in joined


def test_ensure_schema_recreates_view_when_version_differs():
    conn = _FakeConn(view_regclass="initiatives.current",
                     view_comment="initiatives-sync view v0")
    sync.ensure_schema(conn)
    joined = " ".join(_sqls(conn))
    assert "CREATE OR REPLACE VIEW initiatives.current" in joined


def test_prune_old_snapshots_issues_cascade_delete_and_returns_count():
    conn = _FakeConn(delete_rowcount=3)
    n = sync.prune_old_snapshots(conn, retain_days=90)
    assert n == 3
    sql, params = conn.executed[-1]
    assert sql.startswith("DELETE FROM initiatives.snapshots")
    assert "captured_at < now() - make_interval(days => %s)" in sql
    assert params == (90,)
    assert conn.commits == 1


def test_write_snapshot_inserts_snapshot_then_rows_with_jsonb():
    from psycopg2.extras import Json
    conn = _FakeConn(next_snapshot_id=7)
    meta, rows = sync.report_to_rows(_fixture_report(), host="workbench")
    snap_id = sync.write_snapshot(conn, meta, rows)
    assert snap_id == 7
    # first insert = the snapshots row (RETURNING id), with meta values
    snap_sql, snap_params = conn.executed[0]
    assert "INSERT INTO initiatives.snapshots" in snap_sql
    assert snap_params == ("workbench", 4, True)
    # then one initiative_snapshot insert carrying snapshot_id + JSONB-wrapped fields
    row_sql, row_params = conn.executed[1]
    assert "INSERT INTO initiatives.initiative_snapshot" in row_sql
    assert row_params[0] == 7  # snapshot_id prepended
    # open_prs / open_investigations / docs are wrapped in psycopg2 Json
    jsonb_count = sum(1 for p in row_params if isinstance(p, Json))
    assert jsonb_count == 3
    assert conn.commits == 1
