"""Unit tests for chquery: pure math + query builders + the HTTP client (mocked).

No test hits a real ClickHouse — the client opener is faked.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chquery as Q  # noqa: E402


# --------------------------------------------------------------------------- #
# Pure: switch count (matches CH lagInFrame(app,1,app), seed=self)
# --------------------------------------------------------------------------- #
def test_count_switches_basic():
    # A A B C C A -> switches at A->B, B->C, C->A = 3
    assert Q.count_switches(["A", "A", "B", "C", "C", "A"]) == 3


def test_count_switches_first_row_never_switch():
    # single app, many rows -> 0 switches
    assert Q.count_switches(["X", "X", "X"]) == 0
    assert Q.count_switches(["X"]) == 0


def test_count_switches_empty_and_filtered():
    assert Q.count_switches([]) == 0
    # empty apps filtered (app != '' upstream)
    assert Q.count_switches(["", "A", "", "B", ""]) == 1


def test_count_switches_every_row_changes():
    assert Q.count_switches(["A", "B", "C", "D"]) == 3


# --------------------------------------------------------------------------- #
# Pure: deep-work block (gaps-and-islands)
# --------------------------------------------------------------------------- #
def test_longest_deep_work_simple_island():
    # one island of app A spanning 4000ms, then short switches
    base = 1_000_000
    events = [
        (base, "A"),
        (base + 4000, "A"),     # same island, 4000ms span
        (base + 4500, "B"),     # switch
        (base + 5000, "C"),     # switch
    ]
    assert Q.longest_deep_work_ms(events) == 4000


def test_longest_deep_work_picks_max_island():
    base = 0
    events = [
        (base, "A"), (base + 1000, "A"),          # 1000ms
        (base + 1500, "B"), (base + 9500, "B"),    # 8000ms  <- longest
        (base + 10000, "C"),
    ]
    assert Q.longest_deep_work_ms(events) == 8000


def test_longest_deep_work_unsorted_input():
    events = [(5000, "A"), (1000, "A"), (3000, "A")]
    assert Q.longest_deep_work_ms(events) == 4000


def test_longest_deep_work_empty():
    assert Q.longest_deep_work_ms([]) == 0
    assert Q.longest_deep_work_ms([(1000, "")]) == 0


def test_longest_deep_work_datetime_input():
    a = datetime(2026, 6, 24, 12, 0, 0)
    b = datetime(2026, 6, 24, 12, 0, 3)  # 3000ms later, same app
    assert Q.longest_deep_work_ms([(a, "A"), (b, "A")]) == 3000


# --------------------------------------------------------------------------- #
# RETIRED: browser active_ms — the per-page active-engagement metric and its
# query builder / pure summer have been removed (structurally wrong on i3;
# attention is now derived downstream from i3 focus). Guard against a regression
# re-introducing them.
# --------------------------------------------------------------------------- #
def test_browser_active_ms_helpers_are_gone():
    assert not hasattr(Q, "q_browser_active_ms")
    assert not hasattr(Q, "sum_active_ms")


# --------------------------------------------------------------------------- #
# Pure: hour-of-day bucketing (timezone behaviour)
# --------------------------------------------------------------------------- #
def test_hour_of_day_reads_local_wallclock_literal():
    # ts is the host's LOCAL wall clock string; toHour reads the literal hour.
    # 07:59 local -> bucket 7 (NOT shifted to UTC 12).
    assert Q.hour_of_day("2026-06-24 07:59:21.226") == 7
    assert Q.hour_of_day("2026-06-24 00:00:00.000") == 0
    assert Q.hour_of_day("2026-06-24 23:30:00") == 23


def test_hour_of_day_datetime():
    assert Q.hour_of_day(datetime(2026, 6, 24, 14, 5)) == 14


def test_hour_histogram():
    ts = [
        "2026-06-24 07:00:00.000",
        "2026-06-24 07:30:00.000",
        "2026-06-24 08:00:00.000",
    ]
    assert Q.hour_histogram(ts) == {7: 2, 8: 1}


# --------------------------------------------------------------------------- #
# Query builders: shape / scoping
# --------------------------------------------------------------------------- #
def test_run_scope_quotes():
    assert Q.run_scope("vrun-abc") == "session = 'vrun-abc'"


def test_sql_quote_escapes():
    assert Q.sql_quote("a'b") == "'a\\'b'"


def test_builders_contain_verbatim_logic():
    where = Q.run_scope("vrun-x")
    assert "lagInFrame(app, 1, app)" in Q.q_app_switches(where)
    assert "sum(is_switch)" in Q.q_longest_deep_work_ms(where)
    assert "toHour(ts)" in Q.q_hour_histogram(where)
    assert "source = 'zsh'" in Q.q_command_count(where)
    assert "source = 'browser'" in Q.q_nav_count(where)
    # scope is applied
    assert "session = 'vrun-x'" in Q.q_event_count(where)


# --------------------------------------------------------------------------- #
# HTTP client (mocked opener)
# --------------------------------------------------------------------------- #
class FakeResp:
    def __init__(self, body=b"", status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def close(self):
        pass


def make_client(body, status=200):
    captured = {}

    def opener(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return FakeResp(body if isinstance(body, bytes) else body.encode(), status)

    conn = Q.CHConn(url="http://fake:30123", user="activity_reader", password="pw")
    return Q.CHClient(conn, opener=opener), captured


def test_client_scalar_int():
    client, cap = make_client("42\n")
    assert client.scalar("SELECT count()") == 42
    # FORMAT TSV appended, user/key headers set
    assert "FORMAT+TSV" in cap["url"] or "FORMAT%20TSV" in cap["url"]
    hdrs = {k.lower(): v for k, v in cap["headers"].items()}
    assert hdrs.get("x-clickhouse-user") == "activity_reader"
    assert hdrs.get("x-clickhouse-key") == "pw"


def test_client_scalar_null():
    client, _ = make_client("\\N\n")
    assert client.scalar("SELECT x") is None


def test_client_rows_jsoneachrow():
    body = '{"hour":7,"events":2}\n{"hour":8,"events":1}\n'
    client, _ = make_client(body)
    rows = client.rows("SELECT ...")
    assert rows == [{"hour": 7, "events": 2}, {"hour": 8, "events": 1}]


def test_client_raises_on_non_2xx():
    client, _ = make_client("boom", status=500)
    with pytest.raises(RuntimeError):
        client.scalar("SELECT 1")


def test_conn_from_env_requires_url():
    with pytest.raises(RuntimeError):
        Q.CHConn.from_env(env={})


def test_conn_from_env_reads_creds():
    c = Q.CHConn.from_env(env={
        "CLICKHOUSE_URL": "http://h:30123/",
        "CLICKHOUSE_USER": "activity_reader",
        "CLICKHOUSE_PASSWORD": "secret",
    })
    assert c.url == "http://h:30123"  # trailing slash stripped
    assert c.password == "secret"
    assert c.fq_table == "activity.events"
