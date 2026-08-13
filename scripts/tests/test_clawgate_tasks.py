"""scripts/lib/clawgate_tasks.py — the ONE "does this need the operator" rule.

🔴 WHY THE CONTROL FIXTURES ARE CONSTRUCTED AND NOT SAMPLED. The live board at
the time this was written held 17 tasks — 8 open, 3 ready_for_review, 6 complete
— and **ZERO `in_progress`**. So a live run of the stuck detector returns 0, and
that zero is indistinguishable from a detector wired to nothing. Every disjunct
below therefore has a fixture that MUST report stuck (the positive control) and
a near-miss twin that must NOT (the negative control), so the pair is what is
reported, never the live zero alone.

🔴 EVERY VALUE HERE IS SYNTHETIC AND PAIRWISE DISTINCT. This repo is public and
real clawgate task titles are client work: no title, agent name, repo or id here
came off the live board. Distinctness is deliberate — a fixture whose fields
collide lets a mutation that reads the wrong field pass.
"""
import importlib.machinery
import importlib.util
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib" / "clawgate_tasks.py"


def _load():
    loader = importlib.machinery.SourceFileLoader("clawgate_tasks_ut", str(LIB))
    spec = importlib.util.spec_from_file_location("clawgate_tasks_ut", str(LIB),
                                                  loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cg = _load()

NOW = 1_800_000_000.0


def _iso(epoch):
    import datetime
    return (datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%fZ"))


def _agent(status="running", kicked=True, activity=NOW - 10, **kw):
    a = {"id": 4101, "name": "widget-forge", "namespace": "devpod-widget-forge",
         "displayName": "Widget forge", "status": status, "noteId": 9001,
         "repo": "example-org/sample-widget", "repoBranch": "topic/sample",
         "model": "synthetic-model", "kickedOff": kicked,
         "errorMessage": "", "lastMessageAt": None,
         "lastActivityAt": None if activity is None else _iso(activity),
         "createdAt": _iso(NOW - 7200), "updatedAt": _iso(NOW - 3600)}
    a.update(kw)
    return a


def _task(tid, status, title, agent="none"):
    t = {"id": tid, "directory": "/synthetic/path/%d" % tid, "title": title,
         "status": status, "tags": [], "model": "synthetic-model",
         "repo": "example-org/sample-widget", "repoBranch": "topic/sample",
         "sourceType": "api", "commentCount": 0, "attachmentCount": 0,
         "createdAt": _iso(NOW - 7200), "updatedAt": _iso(NOW - 3600),
         # 🔴 The key is ALWAYS present on 0.7.86 (measured live) — `null` means
         # "no agent linked", an ABSENT key means an older server. `"none"` here
         # is the sentinel for "leave the key out entirely".
         }
    if agent != "none":
        t["agent"] = agent
    return t


# =========================================================================== #
# §A — timestamps
# =========================================================================== #
def test_parse_api_ts_round_trips_the_go_z_form():
    assert cg.parse_api_ts("2026-07-30T23:34:18.640843Z") == pytest.approx(
        1785454458.640843, abs=1e-3)


def test_parse_api_ts_accepts_an_explicit_offset():
    z = cg.parse_api_ts("2026-07-30T23:34:18.640843Z")
    off = cg.parse_api_ts("2026-07-30T21:34:18.640843-02:00")
    assert z == off


def test_parse_api_ts_truncates_nanosecond_precision_instead_of_failing():
    # Go can emit 9 fractional digits; fromisoformat rejects >6. Refusing would
    # manufacture an `activity_unknown` alarm over a digit count.
    assert cg.parse_api_ts("2026-07-30T23:34:18.640843123Z") == pytest.approx(
        cg.parse_api_ts("2026-07-30T23:34:18.640843Z"), abs=1e-3)


def test_parse_api_ts_treats_a_naive_stamp_as_utc_not_local():
    # Assuming local time would move every idle reading by the host's offset.
    assert (cg.parse_api_ts("2026-07-30T23:34:18")
            == cg.parse_api_ts("2026-07-30T23:34:18Z"))


@pytest.mark.parametrize("junk", [None, "", "   ", 17, [], {}, "not-a-date",
                                  "2026-13-45T99:99:99Z"])
def test_parse_api_ts_returns_none_for_junk_and_never_raises(junk):
    assert cg.parse_api_ts(junk) is None


def test_agent_activity_ts_prefers_last_activity_over_updated_at():
    a = _agent(activity=NOW - 30)
    a["updatedAt"] = _iso(NOW - 5000)
    assert cg.agent_activity_ts(a) == pytest.approx(NOW - 30, abs=1)


def test_agent_activity_ts_falls_back_to_updated_at():
    a = _agent(activity=None)
    a["updatedAt"] = _iso(NOW - 44)
    assert cg.agent_activity_ts(a) == pytest.approx(NOW - 44, abs=1)


def test_agent_activity_ts_none_when_neither_field_parses():
    a = _agent(activity=None)
    a["updatedAt"] = "garbage"
    assert cg.agent_activity_ts(a) is None


def test_agent_idle_secs_clamps_clock_skew_to_zero_rather_than_going_negative():
    t = _task(1, cg.IN_PROGRESS, "future stamp", _agent(activity=NOW + 600))
    assert cg.agent_idle_secs(t, NOW) == 0.0


def test_fmt_idle_none_is_a_question_mark_not_zero():
    # An unmeasured idle time must never render as a fresh one.
    assert cg.fmt_idle(None) == "?"
    assert cg.fmt_idle(0) == "0s"


@pytest.mark.parametrize("secs,want", [(5, "5s"), (59, "59s"), (60, "1m"),
                                        (960, "16m"), (3600, "1h"),
                                        (14400, "4h"), (86400, "1d")])
def test_fmt_idle_buckets(secs, want):
    assert cg.fmt_idle(secs) == want


# =========================================================================== #
# §B — the pending half of the predicate
# =========================================================================== #
def test_pending_states_is_exactly_open_and_ready_for_review():
    assert cg.PENDING_TASK_STATES == frozenset({"open", "ready_for_review"})


@pytest.mark.parametrize("status,want", [("open", True),
                                          ("ready_for_review", True),
                                          ("in_progress", False),
                                          ("complete", False)])
def test_is_pending_per_status(status, want):
    assert cg.is_pending(_task(1, status, "sample")) is want


@pytest.mark.parametrize("junk", [None, "x", 3, [], ("open",)])
def test_is_pending_tolerates_junk(junk):
    assert cg.is_pending(junk) is False


# =========================================================================== #
# §C — 🔴 THE STUCK PREDICATE. One positive control PER DISJUNCT, each with the
# near-miss twin that must stay silent, so a disjunct cannot be certified by
# another disjunct's fixture firing.
# =========================================================================== #
def test_control_null_agent_fires():
    t = _task(701, cg.IN_PROGRESS, "dispatch with no agent object", None)
    assert cg.stuck_reasons(t, NOW) == ["no_agent"]
    assert cg.is_stuck(t, NOW) is True


def test_control_agent_error_fires():
    t = _task(702, cg.IN_PROGRESS, "agent gave up",
              _agent(status="error", activity=NOW - 5))
    assert cg.stuck_reasons(t, NOW) == ["agent_error"]


def test_control_not_kicked_off_fires():
    t = _task(703, cg.IN_PROGRESS, "provisioned, never started",
              _agent(kicked=False, activity=NOW - 5))
    assert cg.stuck_reasons(t, NOW) == ["not_kicked_off"]


def test_control_idle_beyond_threshold_fires():
    t = _task(704, cg.IN_PROGRESS, "silent for four hours",
              _agent(activity=NOW - 14400))
    assert cg.stuck_reasons(t, NOW) == ["agent_idle"]
    assert cg.agent_idle_secs(t, NOW) == pytest.approx(14400, abs=1)


def test_control_activity_unknown_fires():
    a = _agent(activity=None)
    a["updatedAt"] = None
    t = _task(705, cg.IN_PROGRESS, "running with no readable timestamp", a)
    assert cg.stuck_reasons(t, NOW) == ["activity_unknown"]
    # And it ships a None idle time rather than a fabricated 0.
    assert cg.agent_idle_secs(t, NOW) is None


def test_negative_control_healthy_in_progress_is_silent():
    t = _task(706, cg.IN_PROGRESS, "healthy mid-run", _agent(activity=NOW - 60))
    assert cg.stuck_reasons(t, NOW) == []
    assert cg.is_stuck(t, NOW) is False


@pytest.mark.parametrize("status", ["open", "ready_for_review", "complete"])
def test_negative_control_the_in_progress_gate_wins_over_every_disjunct(status):
    # 🔴 REACHABILITY, from the other side: a `complete` task's agent is
    # expected to be idle forever, and a `no_agent` open task is the norm. If
    # the gate were dropped, EVERY board would read as full of stuck work.
    for agent in (None, _agent(status="error"), _agent(kicked=False),
                  _agent(activity=NOW - 99999)):
        t = _task(707, status, "not in progress", agent)
        assert cg.stuck_reasons(t, NOW) == []


def test_disjuncts_accumulate_rather_than_short_circuiting():
    # A reader gets every reason that applies, not the first one found.
    t = _task(708, cg.IN_PROGRESS, "error and never kicked off",
              _agent(status="error", kicked=False, activity=NOW - 99999))
    assert cg.stuck_reasons(t, NOW) == ["agent_error", "not_kicked_off",
                                        "agent_idle"]


def test_every_named_reason_is_reachable_from_some_fixture():
    # 🔴 The vocabulary and the branches pin each other: a reason that no input
    # can produce is dead documentation, and a branch that produces a reason
    # outside the tuple is invisible to anything rendering it.
    a_unknown = _agent(activity=None)
    a_unknown["updatedAt"] = None
    produced = set()
    for t in (_task(1, cg.IN_PROGRESS, "a", None),
              _task(2, cg.IN_PROGRESS, "b", _agent(status="error")),
              _task(3, cg.IN_PROGRESS, "c", _agent(kicked=False)),
              _task(4, cg.IN_PROGRESS, "d", _agent(activity=NOW - 99999)),
              _task(5, cg.IN_PROGRESS, "e", a_unknown)):
        produced.update(cg.stuck_reasons(t, NOW))
    assert produced == set(cg.STUCK_REASONS)


# --- the threshold itself --------------------------------------------------- #
def test_threshold_is_fifteen_minutes():
    assert cg.AGENT_IDLE_THRESHOLD_SECS == 900


@pytest.mark.parametrize("idle,stuck", [
    (0, False), (149, False),          # healthy runs measured at 138s and 149s
    (899, False), (900, False),        # 🔴 the boundary is EXCLUSIVE
    (901, True), (960, True),          # "idle 16m" — the honest false-alarm case
    (14400, True),                     # the observed four-hour wedge
])
def test_threshold_measured_at_the_boundary_and_well_either_side(idle, stuck):
    # One measurement is not a claim about a threshold: this walks the boundary
    # and a point an order of magnitude to each side of it.
    t = _task(709, cg.IN_PROGRESS, "idle sweep", _agent(activity=NOW - idle))
    assert cg.is_stuck(t, NOW) is stuck


def test_threshold_is_injectable_so_a_caller_can_widen_it():
    t = _task(710, cg.IN_PROGRESS, "idle 20m", _agent(activity=NOW - 1200))
    assert cg.is_stuck(t, NOW) is True
    assert cg.is_stuck(t, NOW, threshold=3600) is False


def test_a_missing_agent_key_is_treated_as_no_agent():
    # An absent key means an OLD server; a null means no agent linked. Both are
    # "nothing is demonstrably running", which is the alarm-side reading.
    t = _task(711, cg.IN_PROGRESS, "server predates the agent key")
    assert cg.stuck_reasons(t, NOW) == ["no_agent"]


def test_a_non_dict_agent_is_treated_as_no_agent():
    t = _task(712, cg.IN_PROGRESS, "agent is a string", "widget-forge")
    assert cg.stuck_reasons(t, NOW) == ["no_agent"]


def test_kicked_off_is_identity_checked_not_truthy_checked():
    # `kickedOff: 1` / `"true"` are not the API's boolean; treating them as
    # kicked-off would silence the disjunct on a shape change.
    for junk in (1, "true", "yes"):
        t = _task(713, cg.IN_PROGRESS, "odd kickedOff",
                  _agent(kicked=junk, activity=NOW - 5))
        assert "not_kicked_off" in cg.stuck_reasons(t, NOW)


# =========================================================================== #
# §D — the roll-up
# =========================================================================== #
def _board():
    """A synthetic board: 2 open, 1 review, 1 healthy in_progress, 1 stuck,
    1 complete. Ids and titles pairwise distinct."""
    a_unknown = _agent(activity=None)
    a_unknown["updatedAt"] = None
    return [
        _task(801, "open", "first queued item", None),
        _task(802, "open", "second queued item", None),
        _task(803, "ready_for_review", "finished, awaiting a look", None),
        _task(804, cg.IN_PROGRESS, "healthy run", _agent(activity=NOW - 30)),
        _task(805, cg.IN_PROGRESS, "wedged run", _agent(activity=NOW - 14400)),
        _task(806, "complete", "already done", _agent(activity=NOW - 99999)),
    ]


def test_attention_counts_pending_plus_stuck_and_discriminates_both():
    att = cg.attention(_board(), NOW)
    assert att["count"] == 4
    assert att["pending_count"] == 3
    assert att["stuck_count"] == 1
    assert att["count"] == att["pending_count"] + att["stuck_count"]


def test_attention_enumerates_ready_for_review_rather_than_folding_it_in():
    # 🔴 The reported failure: three finished tasks appeared in NO list on any
    # surface — the count moved and nothing said what had finished.
    att = cg.attention(_board(), NOW)
    assert att["ready_for_review"] == [{"id": 803,
                                        "title": "finished, awaiting a look"}]
    assert [o["id"] for o in att["open"]] == [801, 802]


def test_attention_stuck_rows_carry_reasons_and_idle_seconds():
    att = cg.attention(_board(), NOW)
    (s,) = att["stuck"]
    assert s["id"] == 805 and s["reasons"] == ["agent_idle"]
    assert s["agent_idle_secs"] == pytest.approx(14400, abs=1)


def test_attention_excludes_the_healthy_in_progress_task_entirely():
    att = cg.attention(_board(), NOW)
    ids = ([o["id"] for o in att["open"]]
           + [r["id"] for r in att["ready_for_review"]]
           + [s["id"] for s in att["stuck"]])
    assert 804 not in ids and 806 not in ids


def test_attention_empty_board_is_a_measured_idle_zero():
    att = cg.attention([], NOW)
    assert att["count"] == 0 and att["state"] == "Idle"
    assert att["detail"] == "no pending tasks"
    assert att["stuck_count"] == 0


def test_attention_tolerates_junk_elements():
    att = cg.attention([None, "x", 3, {"id": 7, "status": "open"}], NOW)
    assert att["count"] == 1 and att["open"] == [{"id": 7, "title": "(no title)"}]


@pytest.mark.parametrize("junk", [{"error": "nope"}, None, "x", 7])
def test_attention_raises_on_a_non_list_payload(junk):
    with pytest.raises(ValueError):
        cg.attention(junk, NOW)


def test_attention_carries_the_threshold_it_used():
    assert cg.attention([], NOW)["threshold_secs"] == cg.AGENT_IDLE_THRESHOLD_SECS
    assert cg.attention([], NOW, threshold=60)["threshold_secs"] == 60


def test_attention_declares_its_schema():
    assert cg.attention([], NOW)["schema"] == cg.SCHEMA


# --- the detail string ------------------------------------------------------ #
def test_detail_states_its_own_truncation():
    # 🔴 The old string read "11 task(s) awaiting: <six ids>" — six of eleven,
    # with nothing saying five were missing.
    board = [_task(900 + i, "open", "queued %d" % i, None) for i in range(11)]
    att = cg.attention(board, NOW)
    assert att["detail_total"] == 11
    assert att["detail_shown"] == cg.DETAIL_MAX_IDS
    assert att["detail_truncated"] is True
    assert "(+5 more)" in att["detail"]


def test_detail_is_not_marked_truncated_when_it_names_everything():
    att = cg.attention([_task(910, "open", "only one", None)], NOW)
    assert att["detail_truncated"] is False
    assert "more)" not in att["detail"]


def test_detail_never_drops_a_stuck_id_to_the_cap():
    # 🔴 The cap must not be able to hide the rarest and most urgent class.
    board = [_task(920 + i, "open", "queued %d" % i, None) for i in range(20)]
    board.append(_task(999, cg.IN_PROGRESS, "wedged",
                       _agent(activity=NOW - 14400)))
    att = cg.attention(board, NOW)
    assert "!#999" in att["detail"]
    assert att["detail"].index("#999") < att["detail"].index("#920")


def test_detail_orders_review_ahead_of_open():
    board = ([_task(930 + i, "open", "queued %d" % i, None) for i in range(10)]
             + [_task(950, "ready_for_review", "finished", None)])
    att = cg.attention(board, NOW)
    assert att["detail"].index("#950") < att["detail"].index("#930")


def test_detail_head_breaks_the_count_down_by_class():
    att = cg.attention(_board(), NOW)
    assert "4 need you (2 open, 1 review, 1 stuck)" in att["detail"]


# --- the unmeasured marker -------------------------------------------------- #
def test_unmeasured_publishes_none_never_zero():
    u = cg.unmeasured("could not reach the API")
    assert u["count"] is None and u["stuck_count"] is None
    assert u["open"] is None and u["ready_for_review"] is None
    assert u["state"] == "Unknown"


# =========================================================================== #
# §E — credentials
# =========================================================================== #
def test_read_clawgate_env_parses_and_strips(tmp_path):
    f = tmp_path / "clawgate.env"
    f.write_text("# a comment\n\nCLAWGATE_API_URL=http://10.0.0.1:1234/ \n"
                 " CLAWGATE_HOOK_TOKEN = synthetic-token-value \n")
    base, token = cg.read_clawgate_env(str(f))
    assert base == "http://10.0.0.1:1234"
    assert token == "synthetic-token-value"


def test_read_clawgate_env_defaults_the_base_url(tmp_path):
    f = tmp_path / "clawgate.env"
    f.write_text("CLAWGATE_HOOK_TOKEN=synthetic-token-value\n")
    base, _ = cg.read_clawgate_env(str(f))
    assert base == cg.DEFAULT_API_URL


def test_read_clawgate_env_raises_naming_the_variable_not_a_value(tmp_path):
    f = tmp_path / "clawgate.env"
    f.write_text("CLAWGATE_API_URL=http://10.0.0.1:1234\n")
    with pytest.raises(KeyError) as ei:
        cg.read_clawgate_env(str(f))
    assert "CLAWGATE_HOOK_TOKEN" in str(ei.value)


def test_the_token_never_reaches_the_url(tmp_path):
    # 🔴 A credential in a URL lands in argv, proxy logs and error strings. It
    # travels in an Authorization header or not at all.
    f = tmp_path / "clawgate.env"
    f.write_text("CLAWGATE_API_URL=http://10.0.0.1:1234\n"
                 "CLAWGATE_HOOK_TOKEN=sentinel-must-not-appear\n")
    base, token = cg.read_clawgate_env(str(f))
    url = cg.tasks_url(base)
    assert token == "sentinel-must-not-appear"
    assert "sentinel-must-not-appear" not in url


def test_tasks_url_uses_the_summary_form():
    # 🔴 Item 4: 190,385 bytes -> 7,058 on the live board (27x), measured
    # against 0.7.86. `?summary=1` swaps `body` for commentCount/attachmentCount
    # and keeps `agent`, which the stuck predicate needs.
    assert cg.tasks_url("http://example.invalid:1/") == \
        "http://example.invalid:1/api/tasks?summary=1"
    assert "summary=1" in cg.TASKS_PATH


def test_tasks_path_does_not_pin_a_status_filter():
    # A repeatable ?status= exists on 0.7.86 and 400s on an unknown value —
    # correct for a producer, wrong for a poller, whose pill would go dark the
    # day a status is added server-side. Filtering happens client-side.
    assert "status=" not in cg.TASKS_PATH


# =========================================================================== #
# §F — the module is PURE (no clock, no network of its own)
# =========================================================================== #
def test_the_predicate_takes_its_clock_from_the_caller():
    t = _task(760, cg.IN_PROGRESS, "idle sweep", _agent(activity=NOW - 1000))
    assert cg.is_stuck(t, NOW) is True
    # Same task, evaluated a moment after the activity: not stuck.
    assert cg.is_stuck(t, NOW - 1000 + 5) is False


def test_module_imports_nothing_that_talks_to_the_network():
    src = LIB.read_text()
    for banned in ("import urllib", "import requests", "import socket",
                   "import subprocess"):
        assert banned not in src, banned


def test_now_is_never_defaulted_to_wall_clock_inside_the_predicate():
    # A default `now=time.time()` would make the predicate untestable at a
    # boundary and unreproducible in a report.
    with pytest.raises(TypeError):
        cg.stuck_reasons(_task(1, cg.IN_PROGRESS, "x", None))
    assert time.time  # (the module under test must not need this at all)
