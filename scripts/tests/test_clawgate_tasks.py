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


def _task(tid, status, title, agent="none", **kw):
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
    t.update(kw)
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


# =========================================================================== #
# §C2 — 🔴 THE GRACE WINDOW, i.e. the defect that shipped.
#
# `no_agent`, `agent_error` and `not_kicked_off` were appended with NO time gate:
# `stuck_reasons` returned `["no_agent"]` the instant `task["agent"]` was not a
# dict. Measured live 2026-08-13: 19 tasks, `agent` non-null on ZERO of them —
# the server does not populate the link at all, even for a healthy
# `status=running, kickedOff=true` agent. So EVERY task entering `in_progress`
# read stuck from second zero for its entire dispatch, and the only reason no one
# had seen it is that the board happened to hold no `in_progress` task.
#
# 🔴 EVERY FIXTURE IN §C ABOVE IS TWO HOURS OLD, which is why the whole suite
# stayed green through the bug: not one test pinned the timing. These do.
# =========================================================================== #
GRACE = 900          # the pinned value, asserted against the module below


def _aged_task(tid, title, task_age, agent="none", created_age=None):
    """An `in_progress` task DISPATCHED `task_age` seconds ago.

    Both timestamps move by default, which is the shape of a task filed and
    dispatched together. `created_age` sets `createdAt` INDEPENDENTLY, for the
    case that matters most: a task that queued for hours before being dispatched
    (measured live — see `test_a_LONG_QUEUED_task_dispatched_NOW_is_not_stuck`).
    """
    return _task(tid, cg.IN_PROGRESS, title, agent,
                 createdAt=_iso(NOW - (task_age if created_age is None
                                       else created_age)),
                 updatedAt=_iso(NOW - task_age))


#: (age, is_stuck) — two points either side of the boundary plus a middle, per
#: `claude/RULES.md`: one measurement is not a claim about a threshold. The
#: boundary is EXCLUSIVE (`> threshold`), matching `agent_idle` above, so 900 is
#: the last second still considered healthy.
BOUNDARY = [(0, False), (60, False), (899, False), (900, False),
            (901, True), (3600, True)]


# --- the live defect, with its own name ------------------------------------- #
def test_the_compound_live_case_null_agent_plus_YOUNG_task_is_not_stuck():
    """🔴 THE SHIPPED BUG, in one test.

    This is the exact shape of every in-flight dispatch on the live board: the
    task says `in_progress`, the `agent` key is present and `null`, and the task
    was created moments ago. Before the fix this returned `["no_agent"]`.
    """
    t = _aged_task(720, "dispatched five seconds ago", 5, None)
    assert cg.stuck_reasons(t, NOW) == []
    assert cg.is_stuck(t, NOW) is False


def test_the_compound_live_case_null_agent_plus_OLD_task_IS_stuck():
    """The other half of the pair: the grace window delays the alarm, it does
    not remove it. An hour in with still no agent is the real signal."""
    t = _aged_task(721, "an hour in, still no agent object", 3600, None)
    assert cg.stuck_reasons(t, NOW) == ["no_agent"]
    assert cg.is_stuck(t, NOW) is True


# --- boundary, per newly-graced disjunct ------------------------------------ #
@pytest.mark.parametrize("age,stuck", BOUNDARY)
def test_boundary_no_agent_is_graced_by_the_TASK_age(age, stuck):
    t = _aged_task(722, "no agent object", age, None)
    assert cg.stuck_reasons(t, NOW) == (["no_agent"] if stuck else [])


@pytest.mark.parametrize("age,stuck", BOUNDARY)
def test_boundary_agent_error_is_graced_by_the_AGENT_age(age, stuck):
    # `activity` stays fresh so `agent_idle`/`activity_unknown` cannot supply the
    # verdict — this asserts THIS disjunct, not "something fired".
    a = _agent(status="error", activity=NOW - 5, createdAt=_iso(NOW - age))
    t = _task(723, cg.IN_PROGRESS, "reconciler gave up", a)
    assert cg.stuck_reasons(t, NOW) == (["agent_error"] if stuck else [])


@pytest.mark.parametrize("age,stuck", BOUNDARY)
def test_boundary_not_kicked_off_is_graced_by_the_AGENT_age(age, stuck):
    a = _agent(kicked=False, activity=NOW - 5, createdAt=_iso(NOW - age))
    t = _task(724, cg.IN_PROGRESS, "provisioned, not yet started", a)
    assert cg.stuck_reasons(t, NOW) == (["not_kicked_off"] if stuck else [])


def test_the_module_threshold_is_what_the_boundary_table_was_written_against():
    # 🔴 Pins the table to the code. Retuning the constant without revisiting
    # these points would leave them asserting a boundary that no longer exists,
    # green and meaningless.
    assert cg.STUCK_THRESHOLD_SECS == GRACE
    assert [a for a, s in BOUNDARY if not s][-1] == GRACE
    assert [a for a, s in BOUNDARY if s][0] == GRACE + 1


# --- 🔴 THE NEGATIVE CONTROL that would have caught the shipped bug --------- #
@pytest.mark.parametrize("age", [0, 1, 60, 300, 899, 900])
def test_negative_control_a_healthy_dispatch_never_flags_inside_the_window(age):
    """A dispatch doing exactly what a healthy dispatch does, sampled across the
    whole window. Every one of these returned `["no_agent"]` before the fix if
    the agent was unlinked, which is the only shape the live server produces."""
    # (a) the live shape: unlinked agent
    assert cg.stuck_reasons(_aged_task(725, "unlinked", age, None), NOW) == []
    # (b) the shape the server is supposed to produce: a linked, working agent
    a = _agent(activity=NOW - min(age, 30), createdAt=_iso(NOW - age))
    t = _task(726, cg.IN_PROGRESS, "linked and working", a)
    assert cg.stuck_reasons(t, NOW) == []


def test_negative_control_a_whole_healthy_BOARD_reports_zero_stuck():
    # The roll-up, not just the predicate: a board of freshly dispatched tasks
    # must produce stuck_count 0 and NOT escalate the bar.
    board = [_aged_task(730 + i, "fresh dispatch %d" % i, 10 * i, None)
             for i in range(6)]
    att = cg.attention(board, NOW)
    assert att["stuck_count"] == 0
    assert att["stuck"] == []
    assert att["count"] == 0 and att["state"] == "Idle"


# --- 🔴 THE POSITIVE CONTROL: the detector still fires, per disjunct -------- #
def test_positive_control_every_disjunct_still_fires_at_its_OWN_reason():
    """🔴 REPORT THIS PAIR, never the live zero alone. The live board holds no
    `in_progress` task, so the live stuck count is 0 and proves nothing about
    whether the detector works. These five are the control that does.

    Each case is built so exactly ONE disjunct can explain it — a fixture that
    trips two lets a broken branch be certified by its neighbour.
    """
    unknown = _agent(activity=None, createdAt=_iso(NOW - 4000))
    unknown["updatedAt"] = None
    cases = {
        "no_agent": _aged_task(740, "wedged, no agent", 4000, None),
        "agent_error": _task(741, cg.IN_PROGRESS, "wedged, errored",
                             _agent(status="error", activity=NOW - 5,
                                    createdAt=_iso(NOW - 4000))),
        "not_kicked_off": _task(742, cg.IN_PROGRESS, "wedged, never started",
                                _agent(kicked=False, activity=NOW - 5,
                                       createdAt=_iso(NOW - 4000))),
        "agent_idle": _task(743, cg.IN_PROGRESS, "wedged, silent four hours",
                            _agent(activity=NOW - 14400)),
        "activity_unknown": _task(744, cg.IN_PROGRESS, "wedged, no timestamp",
                                  unknown),
    }
    got = {name: cg.stuck_reasons(t, NOW) for name, t in cases.items()}
    assert got == {name: [name] for name in cases}, got
    # …and the vocabulary is exactly what the controls produce — no reason in
    # STUCK_REASONS is unreachable, none produced is undocumented.
    assert set(cases) == set(cg.STUCK_REASONS)
    # The roll-up sees all five as one wedged board.
    att = cg.attention(list(cases.values()), NOW)
    assert att["stuck_count"] == 5 and att["count"] == 5


# --- WHICH clock: the wrong-timestamp-field mutants ------------------------- #
def test_a_LONG_QUEUED_task_dispatched_NOW_is_not_stuck():
    """🔴 FOUND BY LIVE OBSERVATION, after the first version of this fix.

    Measured 2026-08-13: task 177 had `createdAt` 10,200s old and `updatedAt` 88s
    old — filed 2.8 hours earlier, DISPATCHED 88 seconds earlier. Grading it on
    `createdAt` puts it 11x past a 15-minute window from the instant it starts,
    so it reads STUCK for its entire run: the original defect, restored for
    exactly the tasks that waited longest in the queue.

    Two of the three live `in_progress` tasks had the two clocks EQUAL, so a
    single sample would have "confirmed" `createdAt`. The clocks only disagree
    for a task that queued — which is the common case, not the corner one.
    """
    t = _aged_task(770, "queued 2.8h, dispatched 88s ago", 88, None,
                   created_age=10200)
    assert cg.stuck_reasons(t, NOW) == []
    # …and it DOES flag once the dispatch itself is old, so the grace is a delay
    # and not an exemption for long-queued work.
    old = _aged_task(771, "queued 2.8h, dispatched 2h ago", 7200, None,
                     created_age=17400)
    assert cg.stuck_reasons(old, NOW) == ["no_agent"]


@pytest.mark.parametrize("age,stuck", BOUNDARY)
def test_the_boundary_is_measured_on_the_DISPATCH_clock_not_the_creation_one(
        age, stuck):
    # The whole boundary table again, with `createdAt` pinned FAR outside the
    # window at every point. If the creation clock were consulted, every one of
    # these would flag and the `False` rows would all fail.
    t = _aged_task(772, "long-queued, dispatched `age` ago", age, None,
                   created_age=90000)
    assert cg.stuck_reasons(t, NOW) == (["no_agent"] if stuck else [])


def test_dispatch_age_prefers_updatedAt_and_falls_back_to_createdAt():
    assert cg.dispatch_age_secs(
        {"createdAt": _iso(NOW - 9000), "updatedAt": _iso(NOW - 30)},
        NOW) == pytest.approx(30, abs=1)
    # fallback: no updatedAt at all
    assert cg.dispatch_age_secs({"createdAt": _iso(NOW - 300)}, NOW) == \
        pytest.approx(300, abs=1)
    # fallback: an UNPARSEABLE updatedAt must not shadow a good createdAt
    assert cg.dispatch_age_secs(
        {"createdAt": _iso(NOW - 300), "updatedAt": "not-a-timestamp"},
        NOW) == pytest.approx(300, abs=1)
    assert cg.dispatch_age_secs({}, NOW) is None
    assert cg.dispatch_age_secs(None, NOW) is None
    # clamped, like every other age in this module
    assert cg.dispatch_age_secs({"updatedAt": _iso(NOW + 500)}, NOW) == 0.0


def test_the_two_task_clocks_are_DISTINGUISHABLE_by_this_suite():
    # 🔴 A fixture whose fields collide cannot tell the two apart — which is
    # precisely how the wrong field shipped. Pin that the helper really does move
    # them independently.
    t = _aged_task(773, "distinct clocks", 60, None, created_age=50000)
    assert cg.created_age_secs(t, NOW) == pytest.approx(50000, abs=1)
    assert cg.dispatch_age_secs(t, NOW) == pytest.approx(60, abs=1)
    assert cg.created_age_secs(t, NOW) != cg.dispatch_age_secs(t, NOW)


def test_the_AGENT_clock_wins_over_the_task_clock_when_an_agent_exists():
    """🔴 KILLS: reading `task["createdAt"]` for a disjunct about the agent.

    An OLD task that has just been handed a BRAND NEW agent is a retry, not a
    wedge — the agent has had no time to kick off. A mutant using the task's age
    calls this stuck; the correct clock calls it healthy.
    """
    a = _agent(kicked=False, activity=NOW - 5, createdAt=_iso(NOW - 10))
    t = _task(750, cg.IN_PROGRESS, "old task, freshly re-dispatched", a,
              createdAt=_iso(NOW - 86400))
    assert cg.stuck_reasons(t, NOW) == []


def test_the_TASK_clock_is_the_fallback_when_the_agent_has_no_createdAt():
    """A server that drops `createdAt` from the agent degrades to a coarser
    clock, not to no clock. KILLS: returning None (-> always stuck) instead of
    falling back."""
    young = _agent(kicked=False, activity=NOW - 5)
    del young["createdAt"]
    old = _agent(kicked=False, activity=NOW - 5)
    del old["createdAt"]
    # 🔴 The two task clocks are set FAR APART deliberately. A fixture where
    # `createdAt` and `updatedAt` agree cannot tell which one the fallback reads
    # — a mutant swapping it to `created_age_secs` SURVIVED against exactly such
    # a fixture. Here the task was filed 25 hours ago and dispatched 30s ago, so
    # only the DISPATCH clock gives the answer under test.
    assert cg.stuck_reasons(
        _aged_task(751, "long-queued, just dispatched, agent has no clock",
                   30, young, created_age=90000), NOW) == []
    assert cg.stuck_reasons(
        _aged_task(752, "old dispatch, agent has no clock", 9000, old,
                   created_age=90000), NOW) == ["not_kicked_off"]


@pytest.mark.parametrize("bad", [None, "", "not-a-timestamp", 12345, []])
def test_an_UNREADABLE_task_clock_errs_toward_the_alarm(bad):
    """🔴 "Cannot tell how old this is" must never render as "healthy" — the
    same rule `fmt_idle` follows for an unmeasured idle time. This is the one
    place the grace window is allowed to fail OPEN, and it is safe precisely
    because both timestamps are measured present on 22/22 live tasks: a None
    here means the server changed, which is worth an alarm.

    🔴 BOTH clocks are corrupted. Setting only `createdAt` left `updatedAt` at
    the fixture's default, which is readable and 3600s old — so the task flagged
    via the ORDINARY past-the-window path and this test passed without ever
    reaching the branch it names.
    """
    t = _task(753, cg.IN_PROGRESS, "no readable timestamps at all", None,
              createdAt=bad, updatedAt=bad)
    assert cg.dispatch_age_secs(t, NOW) is None, "fixture must reach the None path"
    assert cg.stuck_reasons(t, NOW) == ["no_agent"]


@pytest.mark.parametrize("bad", [None, "", "not-a-timestamp", 12345, []])
def test_an_unreadable_updatedAt_alone_falls_back_rather_than_alarming(bad):
    # The discriminating twin: one broken clock is a FALLBACK, not an alarm.
    t = _task(754, cg.IN_PROGRESS, "readable createdAt only", None,
              createdAt=_iso(NOW - 30), updatedAt=bad)
    assert cg.dispatch_age_secs(t, NOW) == pytest.approx(30, abs=1)
    assert cg.stuck_reasons(t, NOW) == []


def test_created_age_secs_reads_the_createdAt_field_and_clamps_at_zero():
    assert cg.created_age_secs({"createdAt": _iso(NOW - 300)}, NOW) == \
        pytest.approx(300, abs=1)
    # 🔴 Clock skew reads as "just now", never as a negative age that would
    # silently satisfy any `>` comparison.
    assert cg.created_age_secs({"createdAt": _iso(NOW + 500)}, NOW) == 0.0
    assert cg.created_age_secs({}, NOW) is None
    assert cg.created_age_secs(None, NOW) is None
    # It must read `createdAt` and NOT `updatedAt` — the two are distinct in
    # every fixture here for exactly this reason.
    assert cg.created_age_secs(
        {"createdAt": _iso(NOW - 300), "updatedAt": _iso(NOW - 9999)},
        NOW) == pytest.approx(300, abs=1)


# --- the two disjuncts that are deliberately NOT graced --------------------- #
def test_agent_idle_is_NOT_shadowed_by_the_creation_grace():
    """🔴 REACHABILITY. `agent_idle` has its own clock and must still fire for a
    YOUNG agent that has nonetheless gone silent — if the new grace were applied
    to it too, this branch would become unreachable for exactly the dispatches
    worth catching early."""
    a = _agent(activity=NOW - 4000, createdAt=_iso(NOW - 10))
    t = _task(760, cg.IN_PROGRESS, "young agent, long silent", a)
    assert cg.stuck_reasons(t, NOW) == ["agent_idle"]


def test_activity_unknown_is_NOT_shadowed_by_the_creation_grace():
    a = _agent(activity=None, createdAt=_iso(NOW - 10))
    a["updatedAt"] = None
    t = _task(761, cg.IN_PROGRESS, "young agent, unreadable timestamps", a)
    assert cg.stuck_reasons(t, NOW) == ["activity_unknown"]


def test_graced_disjuncts_accumulate_together_once_past_the_window():
    a_young = _agent(status="error", kicked=False, activity=NOW - 5,
                     createdAt=_iso(NOW - 10))
    a_old = _agent(status="error", kicked=False, activity=NOW - 5,
                   createdAt=_iso(NOW - 4000))
    assert cg.stuck_reasons(
        _task(762, cg.IN_PROGRESS, "young, error + not kicked", a_young),
        NOW) == []
    assert cg.stuck_reasons(
        _task(763, cg.IN_PROGRESS, "old, error + not kicked", a_old),
        NOW) == ["agent_error", "not_kicked_off"]


def test_the_threshold_argument_still_overrides_every_graced_disjunct():
    # A caller-supplied threshold must move ALL the gates, not just the idle one
    # — otherwise the module has two thresholds and one of them is invisible.
    t = _aged_task(764, "two minutes in, no agent", 120, None)
    assert cg.stuck_reasons(t, NOW) == []
    assert cg.stuck_reasons(t, NOW, threshold=60) == ["no_agent"]
    a = _agent(kicked=False, activity=NOW - 5, createdAt=_iso(NOW - 120))
    t2 = _task(765, cg.IN_PROGRESS, "two minutes in, not kicked off", a)
    assert cg.stuck_reasons(t2, NOW) == []
    assert cg.stuck_reasons(t2, NOW, threshold=60) == ["not_kicked_off"]


# --- the threshold itself --------------------------------------------------- #
def test_threshold_is_fifteen_minutes():
    assert cg.STUCK_THRESHOLD_SECS == 900
    # 🔴 The old name is GONE, not aliased. An alias would let a consumer keep
    # importing the name that meant "only the idle clock" while the constant now
    # gates every disjunct — two names for one number is how the divergence this
    # module exists to prevent gets back in.
    assert not hasattr(cg, "AGENT_IDLE_THRESHOLD_SECS")


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
    assert cg.attention([], NOW)["threshold_secs"] == cg.STUCK_THRESHOLD_SECS
    assert cg.attention([], NOW, threshold=60)["threshold_secs"] == 60


def test_attention_declares_its_schema():
    assert cg.attention([], NOW)["schema"] == cg.SCHEMA


# --- 🔴 the STATE, which is what the bar actually renders ------------------- #
def test_a_stuck_dispatch_ESCALATES_the_state_above_plain_pending():
    """🔴 The bar could not express a stuck dispatch. `state` was
    `"Warning" if count else "Idle"`, so a board with a wedge and a board without
    one produced the same string, hence the same colour — of the three surfaces
    this predicate was built for, the one meant to be glanceable was blind.

    The three states must be PAIRWISE DISTINCT, which is the property that makes
    the bar able to show a difference at all.
    """
    idle = cg.attention([], NOW)["state"]
    pending = cg.attention([_task(810, "open", "queued", None)], NOW)["state"]
    wedged = cg.attention([_task(811, cg.IN_PROGRESS, "wedged",
                                 _agent(activity=NOW - 14400))],
                          NOW)["state"]
    assert (idle, pending, wedged) == ("Idle", "Warning", "Critical")
    assert len({idle, pending, wedged}) == 3


def test_the_escalation_is_driven_by_STUCK_not_by_a_big_count():
    # KILLS: escalating on `count > N`. A busy-but-healthy board stays Warning
    # however long it gets; one wedge on a small board goes Critical.
    busy = [_task(820 + i, "open", "queued %d" % i, None) for i in range(25)]
    assert cg.attention(busy, NOW)["state"] == "Warning"
    busy.append(_task(899, cg.IN_PROGRESS, "the one wedge",
                      _agent(activity=NOW - 14400)))
    assert cg.attention(busy, NOW)["state"] == "Critical"


def test_a_healthy_in_progress_board_does_NOT_escalate():
    # The negative control for the escalation: in-flight work is not an alarm.
    board = [_task(830, cg.IN_PROGRESS, "working", _agent(activity=NOW - 30)),
             _task(831, "open", "queued", None)]
    assert cg.attention(board, NOW)["state"] == "Warning"
    assert cg.attention(board, NOW)["stuck_count"] == 0


# --- 🔴 schema_ok: ONE reading of a cached roll-up -------------------------- #
@pytest.mark.parametrize("obj,ok", [
    ({"schema": 2}, True),
    ({"schema": 3}, True),                 # a newer poller is still readable
    ({"schema": 1}, False),                # 🔴 THE DISAGREEMENT: agent-ops used
                                           # `is None` and passed this; session-
                                           # manager used `>=` and failed it
    ({"schema": 0}, False),
    ({}, False),                           # absent — a pre-predicate cache
    ({"schema": None}, False),
    ({"schema": "2"}, False),              # a string is not a version
    ({"schema": 2.0}, False),              # nor is a float
    ({"schema": True}, False),             # 🔴 a bool is not an int here
    (None, False), ("x", False), (7, False), ([], False),
])
def test_schema_ok_is_the_single_reading_of_a_cached_rollup(obj, ok):
    assert cg.schema_ok(obj) is ok


def test_the_bool_rejection_is_REACHABLE_and_not_merely_shadowed(monkeypatch):
    """🔴 A GUARD THAT CANNOT EXECUTE IS NOT A GUARD. `True` is an int in Python,
    so at SCHEMA 2 the `>= SCHEMA` comparison already rejects `{"schema": True}`
    (`True >= 2` is False) and the explicit bool check never runs — a mutant
    deleting it SURVIVED this whole suite, and the parametrized case asserting
    `({"schema": True}, False)` passed for the wrong reason.

    Lowering SCHEMA to 1 makes the bool check the ONLY thing standing between a
    JSON `true` and a "current cache" verdict, so this exercises the branch
    instead of being satisfied by its neighbour.
    """
    monkeypatch.setattr(cg, "SCHEMA", 1)
    # Positive control for the fixture: at SCHEMA 1 a real 1 now passes, proving
    # the lowered comparison is what is being reached past.
    assert cg.schema_ok({"schema": 1}) is True
    # …and the bool is still rejected, which ONLY the isinstance check can do.
    assert cg.schema_ok({"schema": True}) is False
    monkeypatch.setattr(cg, "SCHEMA", 0)
    assert cg.schema_ok({"schema": 0}) is True
    assert cg.schema_ok({"schema": False}) is False


def test_schema_ok_accepts_what_attention_itself_writes():
    # 🔴 The seam: the writer and the reader must agree. A SCHEMA bump that
    # forgot this would make every fresh cache read as unmeasured.
    assert cg.schema_ok(cg.attention([], NOW)) is True
    assert cg.schema_ok(cg.attention([], NOW, threshold=60)) is True


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
def test_the_dead_unmeasured_constructor_is_GONE_not_merely_unused():
    """It had no caller in any consumer — only this suite exercised it, which is
    the shape of a test keeping dead code alive. Pinned as absent so it cannot
    drift back in unaccompanied by the producer that needs it."""
    assert not hasattr(cg, "unmeasured")


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
    """🔴 THE FIXTURE MUST CONTAIN A SECRET, or this test asserts nothing.

    It used to write a file with no token in it at all, so there was no sentinel
    for "the value must not appear" to catch: a mutant raising
    `KeyError("CLAWGATE_HOOK_TOKEN in %s" % env)` — which dumps the WHOLE parsed
    env, every credential in it, into the exception text — survived the entire
    722-test suite. A leak test whose fixture holds nothing to leak is green by
    construction.

    So the file now carries other secret-shaped values under the wrong NAME (the
    lookup still misses, so it still raises) and the assertion is two-sided: the
    exception names the missing variable, and quotes NONE of the values it read.
    """
    f = tmp_path / "clawgate.env"
    f.write_text("CLAWGATE_API_URL=http://10.0.0.1:1234\n"
                 # Pairwise-distinct sentinels, all secret-shaped, none of them
                 # the key being looked up — so parsing succeeds and the lookup
                 # is what fails, with a populated `env` sitting right there.
                 "CLAWGATE_HOOK_TOKN=sentinel-typo-key-9d41f2\n"
                 "CLAWGATE_ADMIN_PASSWORD=sentinel-admin-pw-7b03ae\n"
                 "UNRELATED_API_KEY=sentinel-unrelated-key-4c85dd\n")
    with pytest.raises(KeyError) as ei:
        cg.read_clawgate_env(str(f))
    text = str(ei.value)
    assert "CLAWGATE_HOOK_TOKEN" in text
    for secret in ("sentinel-typo-key-9d41f2", "sentinel-admin-pw-7b03ae",
                   "sentinel-unrelated-key-4c85dd"):
        assert secret not in text, \
            "read_clawgate_env leaked a value from the env file into its KeyError"


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
