#!/usr/bin/env python3
"""insights.py must be LOUD and SPECIFIC when a query fails.

THE BUG THIS PINS (measured 2026-08-02, laptop → nebula CH endpoint)
-------------------------------------------------------------------
    $ insights.py --days 3 --json
      insights: telemetry unavailable (timed out); nothing to report.   exit 0
    $ <same creds, same server> SELECT count(DISTINCT session) …  →  66

The pipeline was healthy. `q_messages` was being OvercommitTracker-killed by a
memory-capped ClickHouse (`Code: 241 MEMORY_LIMIT_EXCEEDED … while reading
column text`, or a stall past the 15s client timeout). Every one of those
outcomes arrived at the same `except Exception` → one "telemetry unavailable"
line → **exit 0**, so nothing downstream could distinguish:

    (a) telemetry not configured    — fine, optional
    (b) server unreachable          — real failure
    (c) server rejected the query   — real failure, and the loudest, because
                                      the data IS there and the tool is lying

...and because all three queries were in ONE try block, a failure in the first
meant the other two never ran and their sections silently rendered as zeros.

HARNESS VALIDATION (RULES: "validate the harness against a known-bad state").
`test_harness_negative_control_*` prove the harness can observe both the
classification and the exit code before any green result here is believed.

POSITIVE CONTROL (RULES: "where the reassuring answer is a zero").
`len(failed_queries) == 0` is the reassuring answer on the happy path.
`test_positive_control_failed_query_counter_moves` requires that same counter to
read exactly 1 and exactly 3 when queries do fail. Reported as a pair.

Environment: pure Python, no network, no `node` — runs identically in the
hermetic sandbox and on a dev host. No skipif; nothing here can silently skip.
"""
from __future__ import annotations

import io
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

SA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SA))
sys.path.insert(0, str(SA.parent / "validation"))
import chquery as Q  # noqa: E402
import insights as I  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
def _summaries():
    return [{"session": "s1", "sess_host": "laptop", "project": "devrc",
             "payload": json.dumps({"tool_counts": {"Bash": 3}, "git_commits": 1,
                                    "user_message_count": 2,
                                    "assistant_message_count": 2})}]


def _insights():
    return [{"session": "s1", "payload": json.dumps(
        {"outcome": "shipped", "claude_helpfulness": 4})}]


# The message stream is aggregated SERVER-SIDE, so gather() issues six queries.
PLAN = ["summaries", "messages", "commands", "first_words", "themes", "insights"]


def _query_name(sql: str) -> str:
    if "session-summary" in sql:
        return "summaries"
    if "session-insight" in sql:
        return "insights"
    if "splitByWhitespace" in sql:
        return "commands"
    if "extract(" in sql:
        return "first_words"
    if "countIf(match" in sql:
        return "themes"
    return "messages"


_ROLLUPS = {
    "messages": [{"kind": "prompt", "host": "laptop", "day": "2026-08-01", "n": 1},
                 {"kind": "command", "host": "laptop", "day": "2026-08-01", "n": 1}],
    "commands": [{"cmd": "/standup", "n": 1}],
    "first_words": [{"w": "fix", "n": 1}],
    "themes": [{"t0": 1, "t1": 0, "t2": 0, "t3": 0, "t4": 0,
                "t5": 0, "t6": 0, "t7": 0, "t8": 0, "t9": 0}],
}


class SelectiveClient:
    """Answers each query normally unless it is named in `fail_with`."""

    def __init__(self, fail_with: dict | None = None):
        self.fail_with = fail_with or {}
        self.seen: list[str] = []

    def rows(self, sql):
        name = _query_name(sql)
        data = ({"summaries": _summaries(), "insights": _insights()}
                .get(name) or _ROLLUPS[name])
        self.seen.append(name)
        exc = self.fail_with.get(name)
        if exc is not None:
            raise exc
        return data


MEMORY_LIMIT_BODY = (
    "Code: 241. DB::Exception: (total) memory limit exceeded: would use 2.31 GiB "
    "(attempt to allocate chunk of 10.44 MiB bytes), current RSS: 2.50 GiB, "
    "maximum: 2.50 GiB. … (while reading column text) … (MEMORY_LIMIT_EXCEEDED)"
)


def _http_error(status=500, body=MEMORY_LIMIT_BODY):
    return urllib.error.HTTPError(
        "http://ch/", status, "Internal Server Error", {},
        io.BytesIO(body.encode()))


# --------------------------------------------------------------------------- #
# HARNESS NEGATIVE CONTROLS
# --------------------------------------------------------------------------- #
def test_harness_negative_control_a_plain_exception_is_still_caught():
    """KNOWN-BAD shape: a client that raises a bare RuntimeError on everything
    must still be seen as a total failure. If this ever passes silently, the
    harness is not observing gather()'s error path at all."""
    class Boom:
        def rows(self, sql):
            raise RuntimeError("connection refused")

    with pytest.raises(I.TelemetryUnavailable):
        I.gather(Boom(), days=14, host=None)


def test_harness_negative_control_capsys_sees_the_exit_code_and_stderr(capsys):
    """The exit-code assertions below are only evidence if main()'s return value
    and stderr are actually reaching this test. Prove it with a case whose
    answer is unambiguous and independent of the taxonomy: --days 0."""
    rc = I.main(["--days", "0"])
    assert rc == 2
    assert "must be positive" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# chquery: unreachable vs query-failed
# --------------------------------------------------------------------------- #
def test_http_error_is_classified_as_a_QUERY_error_with_the_ch_code():
    """RED at origin/main: urlopen raises HTTPError, which the old `_request`
    never caught — it escaped as a bare HTTPError (an OSError), and insights.py
    turned every OSError into 'telemetry unavailable'."""
    def opener(req, timeout=None):
        raise _http_error()

    c = Q.CHClient(Q.CHConn(url="http://ch", user="u", password="p"), opener=opener)
    with pytest.raises(Q.CHQueryError) as ei:
        c.rows("SELECT 1")
    assert ei.value.code == 241
    assert ei.value.http_status == 500
    assert "MEMORY_LIMIT_EXCEEDED" in str(ei.value)
    # ...and it is NOT the unreachable case.
    assert not isinstance(ei.value, Q.CHUnreachable)


def test_timeout_is_classified_as_UNREACHABLE():
    """RED at origin/main (no such class)."""
    def opener(req, timeout=None):
        raise TimeoutError("timed out")

    c = Q.CHClient(Q.CHConn(url="http://ch", user="u", password="p"), opener=opener)
    with pytest.raises(Q.CHUnreachable):
        c.rows("SELECT 1")


def test_connection_refused_is_classified_as_UNREACHABLE():
    def opener(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    c = Q.CHClient(Q.CHConn(url="http://ch", user="u", password="p"), opener=opener)
    with pytest.raises(Q.CHUnreachable) as ei:
        c.rows("SELECT 1")
    assert "http://ch" in str(ei.value)      # names WHERE it failed


def test_both_error_classes_stay_runtimeerror_for_old_callers():
    """INVARIANT GUARD (held before this change too): validation callers do
    `except RuntimeError`. Keep that contract."""
    assert issubclass(Q.CHQueryError, RuntimeError)
    assert issubclass(Q.CHUnreachable, RuntimeError)


def test_ch_error_code_extraction():
    assert Q.ch_error_code("Code: 241. DB::Exception: nope") == 241
    assert Q.ch_error_code("no code here") is None


# --------------------------------------------------------------------------- #
# gather(): partial degradation is REPORTED, never dropped
# --------------------------------------------------------------------------- #
def test_partial_failure_names_the_failed_query_and_keeps_the_rest():
    """RED at origin/main: one failing query aborted the whole gather."""
    c = SelectiveClient({"messages": Q.CHQueryError("boom", code=241)})
    d = I.gather(c, days=14, host=None)
    assert d["status"] == "degraded"
    assert list(d["failed_queries"]) == ["messages"]
    assert "241" in d["failed_queries"]["messages"] or "boom" in d["failed_queries"]["messages"]
    # every OTHER query still ran and still populated its section
    assert c.seen == PLAN
    assert d["sessions"] == 1
    assert d["tool_counts"] == {"Bash": 3}
    assert d["outcomes"] == {"shipped": 1}


def test_all_queries_failing_raises_query_failed_not_unavailable():
    """RED at origin/main. 'Reachable but rejecting' must not read as an outage."""
    c = SelectiveClient({n: Q.CHQueryError("boom") for n in PLAN})
    with pytest.raises(I.TelemetryQueryFailed) as ei:
        I.gather(c, days=14, host=None)
    assert set(ei.value.errors) == set(PLAN)


def test_unreachable_aborts_immediately_without_trying_the_rest():
    """No point paying three timeouts when nothing can succeed."""
    c = SelectiveClient({"summaries": Q.CHUnreachable("timed out")})
    with pytest.raises(I.TelemetryUnreachable):
        I.gather(c, days=14, host=None)
    assert c.seen == ["summaries"]


def test_positive_control_failed_query_counter_moves():
    """POSITIVE CONTROL for the `0` that the happy path reports.

    `len(failed_queries) == 0` is only meaningful once the same counter has been
    shown to produce a non-zero. Reported as the pair 0 / 1 / 3.
    """
    ok = I.gather(SelectiveClient(), days=14, host=None)
    assert len(ok["failed_queries"]) == 0 and ok["status"] == "ok"

    one = I.gather(SelectiveClient({"insights": Q.CHQueryError("x")}),
                   days=14, host=None)
    assert len(one["failed_queries"]) == 1

    with pytest.raises(I.TelemetryQueryFailed) as ei:
        I.gather(SelectiveClient({n: Q.CHQueryError("x") for n in PLAN}),
                 days=14, host=None)
    assert len(ei.value.errors) == len(PLAN)


# --------------------------------------------------------------------------- #
# render(): a degraded report cannot look like a quiet one
# --------------------------------------------------------------------------- #
def test_render_shouts_about_a_failed_query():
    d = I.gather(SelectiveClient({"messages": Q.CHQueryError("Code: 241 boom")}),
                 days=14, host=None)
    txt = I.render(d)
    assert "DEGRADED REPORT" in txt
    assert "messages" in txt
    assert "TOP PROMPT THEMES" in txt          # names the missing sections
    assert "incomplete, not low" in txt.lower() or "INCOMPLETE, not low" in txt


def test_failed_layer_b_query_is_not_reported_as_no_data():
    """The 'no qualitative insights yet' message must NOT appear when the Layer B
    query FAILED — an empty result and a failed query are different facts."""
    d = I.gather(SelectiveClient({"insights": Q.CHQueryError("boom")}),
                 days=14, host=None)
    txt = I.render(d)
    assert "no qualitative insights yet" not in txt
    assert "UNAVAILABLE" in txt


def test_render_html_carries_the_degraded_banner():
    d = I.gather(SelectiveClient({"messages": Q.CHQueryError("boom")}),
                 days=14, host=None)
    assert "DEGRADED" in I.render_html(d)


def test_healthy_report_has_no_banner():
    """Mutation guard: the banner must be conditional, not always-on."""
    d = I.gather(SelectiveClient(), days=14, host=None)
    assert "DEGRADED" not in I.render(d)
    assert "DEGRADED" not in I.render_html(d)


# --------------------------------------------------------------------------- #
# main(): three states, three exit codes, error in --json
# --------------------------------------------------------------------------- #
def _run_main(monkeypatch, capsys, *, argv, conn_raises=None, gather_raises=None,
              client_fail=None):
    if conn_raises is not None:
        monkeypatch.setattr(I.Q.CHConn, "from_env",
                            classmethod(lambda cls, env=None: (_ for _ in ()).throw(conn_raises)))
    else:
        monkeypatch.setattr(I.Q.CHConn, "from_env",
                            classmethod(lambda cls, env=None: I.Q.CHConn(
                                url="http://ch", user="u", password="p")))
        monkeypatch.setattr(I.Q, "CHClient",
                            lambda conn: SelectiveClient(client_fail or {}))
    if gather_raises is not None:
        monkeypatch.setattr(I, "gather",
                            lambda *a, **k: (_ for _ in ()).throw(gather_raises))
    rc = I.main(argv)
    return rc, capsys.readouterr()


def test_exit_0_and_status_not_configured(monkeypatch, capsys):
    rc, cap = _run_main(monkeypatch, capsys, argv=["--json"],
                        conn_raises=RuntimeError("CLICKHOUSE_URL not set"))
    assert rc == 0
    assert json.loads(cap.out)["status"] == "not-configured"
    assert "NOT CONFIGURED" in cap.err


def test_exit_3_and_status_unreachable(monkeypatch, capsys):
    """RED at origin/main: this exited 0."""
    rc, cap = _run_main(monkeypatch, capsys, argv=["--json"],
                        gather_raises=I.TelemetryUnreachable("timed out"))
    assert rc == 3
    body = json.loads(cap.out)
    assert body["status"] == "unreachable"
    assert "timed out" in body["error"]
    assert "UNREACHABLE" in cap.err


def test_exit_4_and_status_query_failed(monkeypatch, capsys):
    """RED at origin/main: this exited 0 with 'telemetry unavailable'."""
    rc, cap = _run_main(monkeypatch, capsys, argv=["--json"],
                        client_fail={n: Q.CHQueryError("Code: 241 boom") for n in PLAN})
    assert rc == 4
    body = json.loads(cap.out)
    assert body["status"] == "query-failed"
    assert set(body["errors"]) == set(PLAN)
    assert "QUERY FAILED" in cap.err


def test_exit_5_and_status_degraded(monkeypatch, capsys):
    """RED at origin/main: a partial failure was not representable at all."""
    rc, cap = _run_main(monkeypatch, capsys, argv=["--json"],
                        client_fail={"messages": Q.CHQueryError("boom")})
    assert rc == 5
    body = json.loads(cap.out)
    assert body["status"] == "degraded"
    assert list(body["failed_queries"]) == ["messages"]
    assert "DEGRADED" in cap.err


def test_exit_0_on_a_healthy_run(monkeypatch, capsys):
    rc, cap = _run_main(monkeypatch, capsys, argv=["--json"])
    assert rc == 0
    body = json.loads(cap.out)
    assert body["status"] == "ok"
    assert body["failed_queries"] == {}
    assert "DEGRADED" not in cap.err


def test_every_state_has_a_distinct_exit_code():
    """The three states must be SEPARABLE — same code twice would defeat it."""
    codes = [I.EXIT_OK, I.EXIT_UNREACHABLE, I.EXIT_QUERY_FAILED, I.EXIT_DEGRADED]
    assert len(set(codes)) == 4
    assert I.EXIT_NOT_CONFIGURED == I.EXIT_OK   # deliberate: not a failure


# --------------------------------------------------------------------------- #
# The query itself: `text` must never be row-streamed again
# --------------------------------------------------------------------------- #
def test_no_query_row_streams_the_text_column():
    """RED at origin/main. THE fix: the server cannot materialize this column
    into a result set under its memory ceiling — measured, every window from 1d
    to 14d timed out while `count()` answered in 0.20s. So no query may SELECT
    `text` as an output column; it may only be consumed inside an aggregate."""
    assert not hasattr(I, "q_messages")
    for sql in (I.q_summaries(1209600), I.q_message_counts(1209600),
                I.q_top_commands(1209600), I.q_first_words(1209600),
                I.q_prompt_themes(1209600), I.q_insights(2592000)):
        select = sql.split(" FROM ", 1)[0]
        # strip function calls, then look for a bare `text` identifier
        stripped = re.sub(r"\w+\([^()]*text[^()]*\)", "", select)
        assert not re.search(r"(?<![a-zA-Z_(])text(?![a-zA-Z_])", stripped), sql


def test_theme_sql_is_generated_from_the_python_regexes():
    """One rule, one place: a THEMES edit must move the SQL, or the two drift
    and the report quietly reports different numbers than the code says."""
    sql = I.q_prompt_themes(100)
    assert len(I.theme_aliases()) == len(I.THEMES)
    for i, pat in enumerate(I.THEMES.values()):
        assert f"AS t{i}" in sql
        assert Q.sql_quote(pat) in sql


def test_word_pattern_matches_the_python_word_regex():
    assert I._WORD_PATTERN == I._WORD_RX.pattern


def test_command_token_sql_splits_on_any_whitespace_like_python():
    """`splitByChar(' ')` would NOT match Python's str.split()."""
    assert "splitByWhitespace(trimBoth(text))[1]" in I.q_top_commands(100)


def test_queries_are_ordered_so_the_top_n_is_reproducible():
    assert "ORDER BY n DESC, cmd ASC" in I.q_top_commands(100)
    assert "ORDER BY n DESC, w ASC" in I.q_first_words(100)


# --------------------------------------------------------------------------- #
# Server-side rollups == the old client-side loop
# --------------------------------------------------------------------------- #
def _raw_messages():
    """Fixture with pairwise-distinct fields: two hosts, two days, both kinds,
    ties in the leaderboards, and themes that must NOT appear (count 0)."""
    return [
        {"kind": "prompt", "host": "laptop", "ts": "2026-08-01 09:00:00",
         "text": "fix the failing deploy on the k8s cluster"},
        {"kind": "prompt", "host": "workbench", "ts": "2026-08-01 10:00:00",
         "text": "Fix the broken test"},
        {"kind": "prompt", "host": "laptop", "ts": "2026-08-02 11:00:00",
         "text": "rebase the branch and push"},
        {"kind": "command", "host": "laptop", "ts": "2026-08-01 12:00:00",
         "text": "/standup now"},
        {"kind": "command", "host": "workbench", "ts": "2026-08-02 13:00:00",
         "text": "/handoff"},
        {"kind": "command", "host": "workbench", "ts": "2026-08-02 14:00:00",
         "text": "/standup"},
    ]


def _stats_from_raw(raw):
    from test_insights import rollups_from_messages          # noqa: PLC0415
    r = rollups_from_messages(raw)
    return I.aggregate_message_stats(r["messages"], r["commands"],
                                     r["first_words"], r["themes"])


def test_server_side_rollups_match_the_client_side_loop():
    """EQUIVALENCE: driving aggregate() through the new server-side path must
    produce the same numbers as the original raw-row loop over the same data.

    (Live cross-check, separately: over 200 REAL rows the SQL and the Python
    agreed EXACTLY on themes, first-words and command tokens — see the PR body.)
    """
    raw = _raw_messages()
    old = I.aggregate(_summaries(), raw, [], 14, None)
    new = I.aggregate(_summaries(), [], [], 14, None, message_stats=_stats_from_raw(raw))

    for key in ("messages", "prompts", "commands", "activity_by_day", "hosts"):
        assert new[key] == old[key], key
    # leaderboards: same (key → count) mapping; order may differ only on ties
    assert dict(new["top_commands"]) == dict(old["top_commands"])
    assert dict(new["top_first_words"]) == dict(old["top_first_words"])
    assert dict(new["top_themes"]) == dict(old["top_themes"])


def test_positive_control_the_equivalence_fixture_is_not_empty():
    """POSITIVE CONTROL: the equivalence test compares dicts, and two EMPTY
    dicts compare equal — so it would pass just as well against a fixture that
    produces nothing. Pin literal non-zero counts."""
    s = _stats_from_raw(_raw_messages())
    assert s["prompts"] == 3 and s["commands"] == 3
    assert dict(s["top_commands"]) == {"/standup": 2, "/handoff": 1}
    assert dict(s["top_first_words"]) == {"fix": 2, "rebase": 1}
    assert len(s["top_themes"]) >= 2
    assert s["activity_by_day"] == {"2026-08-01": 3, "2026-08-02": 3}
    assert s["hosts"]["laptop"] == {"messages": 3, "prompts": 2, "commands": 1}


def test_zero_count_themes_are_dropped_like_counter_most_common():
    stats = I.aggregate_message_stats(
        [], [], [], [{f"t{i}": (3 if i == 2 else 0) for i in range(len(I.THEMES))}])
    assert stats["top_themes"] == [(I.theme_aliases()[2], 3)]
