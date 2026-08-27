#!/usr/bin/env python3
"""Gate on `cairn who` — the task -> session -> window -> transcript join.

WHAT THIS FILE IS DEFENDING. Every hop resolved before the command existed; the
defect class is not "a hop breaks" but "two different answers get printed the
same way". Four pairs are indistinguishable at a glance and each is pinned here:

  * a window that is GONE      vs a tmux scan that never RAN;
  * a task that does NOT EXIST vs a clawgate we could not ASK;
  * a task with NO SESSIONS    vs sessions none of which could be LOCATED;
  * a session id that is a uuid vs one that is not.

🔴 THESE ARE REGRESSION GUARDS FOR A MEASURED REALITY, NOT INVARIANTS. The
motivating observation: on 2026-08-27 the task this command was built for had
three sessions, TWO of which had no live window and both of which had a 6 MB
transcript sitting on disk. A resolver keyed on tmux alone answers "nobody" for
that task. The fixture below is that shape.

Every identifier here is INVENTED. Real session ids, paths and task titles are
read at run time and appear in no fixture — the `CLAUDE.md` PUBLIC-repo rule.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import cairn_who as W  # noqa: E402

# Invented, and deliberately shaped like the real thing.
FAKE_UUID = "11111111-2222-4333-8444-555555555555"
FAKE_UUID_2 = "66666666-7777-4888-8999-000000000000"
#: 🔴 NOT a uuid. Measured on a live scan: 2 of 41 windows carried a token of
#: this shape in `claude_session_id`. A join that assumes uuid matches nothing
#: and reports a clean "no live window".
FAKE_OPAQUE_ID = "ses_aBcDeF1234567890xyz"


def _task(sessions, **kw):
    obj = {"title": "a synthetic task", "status": "open", "sessions": sessions}
    obj.update(kw)
    return obj


def _session_row(sid, **kw):
    row = {"sessionId": sid, "role": "worked", "project": "proj-one",
           "cwd": "/tmp/proj-one", "host": "node-a",
           "lastSeenAt": "2000-01-01T00:00:00Z"}
    row.update(kw)
    return row


def _window(sid, **kw):
    w = {"claude_session_id": sid, "host": "workbench", "session": "scratch9",
         "window_index": "3", "window_id": "@903", "pane_id": "%903",
         "codename": "Onyx", "hotkey": "o", "path": "/tmp/proj-one"}
    w.update(kw)
    return w


def _scan(windows, host="workbench", **hostkw):
    """A host block shaped like the real one — INCLUDING the honesty fields.

    🔴 `reachable`, `windows_measured` and `windows_error` are published per
    host by `session-manager` and were the fields the first version of this
    module ignored. A fixture that omits them cannot see that class of bug.
    """
    block = {"windows": list(windows), "reachable": True,
             "windows_measured": True, "windows_error": None}
    block.update(hostkw)
    return {"hosts": {host: block}}


def _resolve(task_obj, windows=None, transcripts=None, window_exc=None,
             scan_kw=None, **kw):
    """Drive `resolve` with every hop injected — no network, no tmux, no disk."""
    def fetch(task, timeout=0):
        return task_obj

    def wins(timeout=0, host=None):
        if window_exc:
            raise window_exc
        return W._index_windows(_scan(windows or [], **(scan_kw or {})))

    def find(sid, root=None):
        got = (transcripts or {}).get(sid)
        return Path(got) if got else None

    return W.resolve("42", task_fetcher=fetch, window_fetcher=wins,
                     transcript_finder=find, **kw)


# --------------------------------------------------------------------------- #
# The two halves are INDEPENDENT
# --------------------------------------------------------------------------- #


def test_a_session_with_NO_window_but_a_transcript_is_still_LOCATED():
    """🔴 THE MOTIVATING CASE. A tmux window is transient; a transcript is not.

    Measured on the real task this command was built for: 2 of its 3 sessions
    had no live window and both had a multi-megabyte transcript on disk. A
    resolver that treats "no window" as "not found" answers "nobody" for almost
    every task older than the current uptime.
    """
    r = _resolve(_task([_session_row(FAKE_UUID)]),
                 windows=[], transcripts={FAKE_UUID: "/tmp/t/a.jsonl"})
    s = r.sessions[0]
    assert s.window is None
    assert s.transcript == Path("/tmp/t/a.jsonl")
    assert s.located, "a durable transcript did not count as located"
    assert r.state == W.WHO_RESOLVED
    assert r.exit_code == W.EXIT_OK


def test_a_session_with_a_window_but_NO_transcript_is_still_LOCATED():
    """The mirror image — a brand-new session whose transcript has not landed."""
    r = _resolve(_task([_session_row(FAKE_UUID)]),
                 windows=[_window(FAKE_UUID)], transcripts={})
    s = r.sessions[0]
    assert s.window is not None and s.transcript is None
    assert s.located
    assert r.state == W.WHO_RESOLVED


def test_sessions_recorded_but_NEITHER_half_resolves_is_its_own_state():
    """Not `no-sessions`, and not success. A real gap, reported as one."""
    r = _resolve(_task([_session_row(FAKE_UUID)]), windows=[], transcripts={})
    assert r.state == W.WHO_UNLOCATED
    assert r.exit_code == W.EXIT_UNLOCATED
    assert r.exit_code != W.EXIT_OK


# --------------------------------------------------------------------------- #
# 🔴 UNMEASURED is not "none"
# --------------------------------------------------------------------------- #


def test_a_FAILED_tmux_scan_is_UNMEASURED_and_never_reads_as_no_window():
    """🔴 THE SILENT-ZERO GUARD, and the reason `windows_measured` exists.

    The laptop asleep, off the nebula, or slow makes the scan fail. Rendering
    that as an empty window column asserts "this session is running nowhere",
    which is a claim nothing measured. The durable half must still be reported,
    because it is independent of tmux being reachable.
    """
    r = _resolve(_task([_session_row(FAKE_UUID)]),
                 transcripts={FAKE_UUID: "/tmp/t/a.jsonl"},
                 window_exc=W.WhoError("session-manager did not answer within 60s"))
    s = r.sessions[0]
    assert s.windows_measured is False
    assert r.windows_reason and "did not answer" in r.windows_reason

    out = W.render(r)
    assert "UNMEASURED" in out
    assert "none live" not in out, (
        "an unmeasured scan rendered as a positive 'no window' claim:\n" + out)
    # the durable half survived the transient half's failure
    assert "/tmp/t/a.jsonl" in out
    assert r.state == W.WHO_RESOLVED


def test_no_windows_flag_reports_UNMEASURED_rather_than_pretending_it_looked():
    """Opting out of the scan must not manufacture a negative finding."""
    r = _resolve(_task([_session_row(FAKE_UUID)]),
                 transcripts={FAKE_UUID: "/tmp/t/a.jsonl"}, skip_windows=True)
    assert r.sessions[0].windows_measured is False
    assert "UNMEASURED" in W.render(r)
    assert "none live" not in W.render(r)


def test_a_MEASURED_scan_with_no_match_DOES_claim_none_live():
    """CONTROL for the two above. Without this they would pass on a resolver
    that never claims anything, which is the other way to be useless."""
    r = _resolve(_task([_session_row(FAKE_UUID)]),
                 windows=[_window(FAKE_UUID_2)],
                 transcripts={FAKE_UUID: "/tmp/t/a.jsonl"})
    assert r.sessions[0].windows_measured is True
    out = W.render(r)
    assert "none live" in out
    assert "UNMEASURED" not in out


# --------------------------------------------------------------------------- #
# "No answer" is not "the answer is no"
# --------------------------------------------------------------------------- #


def test_ONE_unreachable_host_makes_an_ABSENT_window_unmeasured():
    """🔴 REGRESSION GUARD — the defect this command's own docstring forbade.

    `session-manager` leaves an unreachable host's `windows` as an empty list
    and still exits 0 when the OTHER host answered, so indexing only `windows`
    turned "the laptop is asleep" into a confident "this session is running
    nowhere". Demonstrated on the same fixture, laptop down vs up: `none live`
    became a real window. `ConnectTimeout=4` makes a sleeping laptop fail FAST,
    so this is the everyday case, not an edge.
    """
    payload = {"hosts": {
        "laptop": {"reachable": False, "windows": []},
        "workbench": {"reachable": True, "windows": [_window(FAKE_UUID_2)]},
    }}

    def wins(timeout=0, host=None):
        return W._index_windows(payload)

    r = W.resolve("42", task_fetcher=lambda t, timeout=0: _task([_session_row(FAKE_UUID)]),
                  window_fetcher=wins, transcript_finder=lambda s, root=None: None)
    assert r.sessions[0].windows_measured is False, (
        "an absent window was claimed as measured while a host was unreachable")
    out = W.render(r)
    assert "UNMEASURED" in out and "none live" not in out
    assert "laptop" in (r.windows_reason or ""), r.windows_reason


def test_a_MATCHED_window_stays_a_positive_finding_even_with_a_host_down():
    """CONTROL. Partial-scan honesty must not erase findings it DID make.

    Without this, the fix for the guard above could degrade every window to
    UNMEASURED whenever any host is down — throwing away real answers to avoid
    claiming a false absence.
    """
    payload = {"hosts": {
        "laptop": {"reachable": False, "windows": []},
        "workbench": {"reachable": True, "windows": [_window(FAKE_UUID)]},
    }}
    r = W.resolve("42", task_fetcher=lambda t, timeout=0: _task([_session_row(FAKE_UUID)]),
                  window_fetcher=lambda timeout=0, host=None: W._index_windows(payload),
                  transcript_finder=lambda s, root=None: None)
    s = r.sessions[0]
    assert s.window is not None and s.windows_measured is True
    assert "scratch9:3" in W.render(r)


def test_UNLOCATED_does_not_fire_when_the_live_half_was_never_measured():
    """🔴 Exit 6 documents a GENUINE gap, so it requires having looked.

    With the live half unmeasured, "nothing located" means "no transcript, and
    the window is unknown" — indeterminate. Firing exit 6 made the
    machine-readable surface assert what the human-readable one disclaimed.
    """
    r = _resolve(_task([_session_row(FAKE_UUID)]), transcripts={},
                 skip_windows=True)
    assert not r.sessions[0].located
    assert r.state != W.WHO_UNLOCATED
    assert r.exit_code == W.EXIT_OK
    assert "UNMEASURED" in W.render(r)


def test_session_manager_EXIT_UNAVAILABLE_is_not_read_as_a_measured_zero():
    """🔴 rc 4 arrives WITH a full JSON report of zero windows.

    `session-manager` documents 4 as "NO requested host could be reached — the
    0 is unmeasured", and its header says a caller must never read success off
    a truncated run. The old guard (`rc != 0 and not out.strip()`) let it
    through because the output was present and well-formed.
    """
    def runner(cmd, timeout):
        return W.SM_EXIT_UNAVAILABLE, json.dumps(_scan([])), "all hosts down"

    with pytest.raises(W.WhoError) as exc:
        W.live_windows(runner=runner, script=Path(__file__))
    assert "UNMEASURED" in str(exc.value)


def test_session_manager_EXIT_EMPTY_IS_a_measured_zero():
    """CONTROL, and the opposite code. 3 means every host answered and the
    answer is genuinely none — raising on it would make a quiet fleet look
    like an outage."""
    def runner(cmd, timeout):
        return W.SM_EXIT_EMPTY, json.dumps(_scan([])), ""

    idx, unmeasured = W.live_windows(runner=runner, script=Path(__file__))
    assert idx == {} and unmeasured == []


def test_a_nonzero_scan_WITH_output_is_still_refused():
    """The `and`/`or` seam. An unrecognised non-zero code carrying output must
    not be waved through just because JSON happened to be printed."""
    def runner(cmd, timeout):
        return 99, "", "something broke"

    with pytest.raises(W.WhoError):
        W.live_windows(runner=runner, script=Path(__file__))


def test_a_BAD_task_id_is_not_reported_as_an_outage():
    """🔴 rc 2 is clawgate ANSWERING with a 400. A typo is the likeliest
    failure of all, and calling it `clawgate-unreachable` printed "nothing was
    asked" directly above clawgate's own 400."""
    def runner(cmd, timeout):
        return 2, "", "400 Bad Request — bad id"

    with pytest.raises(W.BadTaskId):
        W.fetch_task("not-a-number", runner=runner)

    r = W.resolve("x", task_fetcher=lambda t, timeout=0: (_ for _ in ()).throw(
        W.BadTaskId("clawgatectl refused the id")))
    assert r.state == W.WHO_BAD_TASK_ID
    assert r.exit_code == W.EXIT_USAGE
    assert r.exit_code not in (W.EXIT_NO_CLAWGATE, W.EXIT_NO_TASK)
    assert "not the network" in W.render(r)


def test_find_transcript_forwards_its_ROOT(tmp_path):
    """The real locator, exercised directly — every other test injects a fake.

    Dropping the `root=` passthrough survived the suite, which means claim
    "reuses transcript_search" was verified only by reading the source.
    """
    proj = tmp_path / "-tmp-proj"
    proj.mkdir()
    (proj / f"{FAKE_UUID}.jsonl").write_text("{}\n")
    assert W.find_transcript(FAKE_UUID, root=tmp_path) == proj / f"{FAKE_UUID}.jsonl"
    assert W.find_transcript(FAKE_UUID_2, root=tmp_path) is None


def test_task_not_found_and_clawgate_unreachable_are_DIFFERENT_states_and_codes():
    """🔴 Both print an empty result; one of them is a lie.

    `clawgatectl` documents 4 = not found and 3/6 = auth/network, so the code is
    read rather than the message text — a reworded diagnostic must not silently
    turn "unreachable" into "no such task".
    """
    def missing(task, timeout=0):
        raise W.TaskNotFound("clawgate has no task 42")

    def down(task, timeout=0):
        raise W.ClawgateUnreachable("clawgatectl exited 6 — dial tcp: i/o timeout")

    a = W.resolve("42", task_fetcher=missing)
    b = W.resolve("42", task_fetcher=down)
    assert a.state == W.WHO_NO_TASK and a.exit_code == W.EXIT_NO_TASK
    assert b.state == W.WHO_NO_CLAWGATE and b.exit_code == W.EXIT_NO_CLAWGATE
    assert a.state != b.state and a.exit_code != b.exit_code
    assert "NOT 'the task has no sessions'" in W.render(b)


def test_a_task_with_no_sessions_is_a_REAL_state_not_a_failure():
    """A task filed in the web UI has none. Exit 0, and said in words."""
    r = _resolve(_task([]))
    assert r.state == W.WHO_NO_SESSIONS
    assert r.exit_code == W.EXIT_OK
    out = W.render(r)
    assert "no session is recorded" in out
    assert "not a lookup failure" in out


def test_every_state_has_an_exit_code_and_the_success_ones_are_distinct():
    """LEDGER, discovered by INTROSPECTION rather than re-listed.

    🔴 The first version enumerated a literal 5-element set, so its own
    docstring claim — "a state added without a code would silently map to 0" —
    was false twice over: it could not see a new `WHO_*` constant, and the
    mapping had a `.get(..., EXIT_OK)` default that swallowed one. Mutating the
    default to 99 survived. The map is now total (a `KeyError` on an unmapped
    state) and the states come from the module.
    """
    states = {v for n, v in vars(W).items()
              if n.startswith("WHO_") and isinstance(v, str)}
    assert len(states) >= 6, f"state discovery broke: {states}"
    codes = {s: W.WhoReport(task="t", state=s).exit_code for s in states}
    assert codes[W.WHO_RESOLVED] == 0 and codes[W.WHO_NO_SESSIONS] == 0
    failing = {s: c for s, c in codes.items() if c != 0}
    assert len(set(failing.values())) == len(failing), (
        f"two failure states share an exit code: {failing}")
    # 🔴 No silent default: an unmapped state must RAISE, not report success.
    with pytest.raises(KeyError):
        W.WhoReport(task="t", state="a-state-nobody-mapped").exit_code


# --------------------------------------------------------------------------- #
# The join key
# --------------------------------------------------------------------------- #


def test_a_NON_UUID_session_id_still_joins():
    """🔴 REGRESSION GUARD. 2 of 41 live windows carried a `ses_…` token.

    A join that validates or normalises uuid shape matches nothing for those and
    reports a clean "no live window" — the failure that looks like a fact.
    """
    r = _resolve(_task([_session_row(FAKE_OPAQUE_ID)]),
                 windows=[_window(FAKE_OPAQUE_ID)])
    assert r.sessions[0].window is not None, "a non-uuid session id did not join"
    assert r.sessions[0].window.address == "scratch9:3"


def test_the_join_is_CASE_SENSITIVE_on_both_sides():
    """Ids are opaque tokens, not identifiers to be folded.

    Lowercasing one side only is the classic half-normalised join; folding both
    would make two genuinely different ids collide.
    """
    r = _resolve(_task([_session_row(FAKE_OPAQUE_ID.upper())]),
                 windows=[_window(FAKE_OPAQUE_ID)])
    assert r.sessions[0].window is None, "ids were matched case-insensitively"


def test_a_session_row_with_a_BLANK_id_is_dropped_not_carried():
    """A blank id matches every blank window id — that is an invented result."""
    r = _resolve(_task([_session_row(""), _session_row("   "),
                        _session_row(FAKE_UUID)]),
                 windows=[_window(FAKE_UUID)])
    assert [s.session_id for s in r.sessions] == [FAKE_UUID]


def test_a_window_with_no_session_id_never_enters_the_index():
    idx, _ = W._index_windows(_scan([
        _window(None), _window(""), _window(FAKE_UUID)]))
    assert set(idx) == {FAKE_UUID}


def test_two_windows_sharing_a_session_id_resolve_DETERMINISTICALLY():
    """A split pane or a re-attach can produce two rows for one session.

    Last-writer-wins makes the answer depend on host iteration order, which is a
    dict order rather than a fact. First wins, and it is pinned so the choice is
    visible rather than incidental.
    """
    first = _window(FAKE_UUID, session="scratch1", window_index="1")
    second = _window(FAKE_UUID, session="scratch2", window_index="2")
    for _ in range(5):
        idx, _ = W._index_windows(_scan([first, second]))
        assert idx[FAKE_UUID].address == "scratch1:1"


def test_windows_are_indexed_across_ALL_hosts_not_just_the_first():
    payload = {"hosts": {
        "workbench": {"windows": [_window(FAKE_UUID)], "reachable": True},
        "laptop": {"windows": [_window(FAKE_UUID_2, host="laptop")],
                   "reachable": True},
    }}
    idx, _ = W._index_windows(payload)
    assert set(idx) == {FAKE_UUID, FAKE_UUID_2}
    assert idx[FAKE_UUID_2].host == "laptop"
    # 🔴 The row's OWN `host` wins over the `hosts` key. Every fixture used to
    # set them equal, so swapping the operands was indistinguishable.
    odd = {"hosts": {"workbench": {"reachable": True,
                                   "windows": [_window(FAKE_UUID, host="elsewhere")]}}}
    only, _ = W._index_windows(odd)
    assert only[FAKE_UUID].host == "elsewhere"


def test_a_host_block_with_no_windows_key_does_not_crash_the_index():
    """A host that failed to scan reports no `windows`; that is a state."""
    payload = {"hosts": {"laptop": {"reachable": False},
                         "workbench": {"windows": [_window(FAKE_UUID)],
                                       "reachable": True}}}
    idx, unmeasured = W._index_windows(payload)
    assert set(idx) == {FAKE_UUID}
    assert unmeasured == ["laptop"], (
        f"an unreachable host was not reported as unmeasured: {unmeasured}")


# --------------------------------------------------------------------------- #
# Rendering — an absence must be spelled
# --------------------------------------------------------------------------- #


def test_render_never_leaves_a_finding_BLANK():
    """Both halves absent, and both said out loud rather than omitted."""
    r = _resolve(_task([_session_row(FAKE_UUID)]), windows=[], transcripts={})
    out = W.render(r)
    assert "none live" in out
    assert "not found" in out
    for line in out.splitlines():
        assert not line.rstrip().endswith(("window", "transcript")), \
            f"a label was printed with no value: {line!r}"


def test_render_carries_BOTH_the_window_index_and_the_stable_ids():
    """The index is what a human types; the id survives renumbering."""
    r = _resolve(_task([_session_row(FAKE_UUID)]), windows=[_window(FAKE_UUID)])
    out = W.render(r)
    assert "scratch9:3" in out
    assert "@903" in out and "%903" in out
    assert "Onyx" in out and "(Onyx, o)" in out


def test_the_json_surface_round_trips_and_marks_the_unmeasured_half():
    r = _resolve(_task([_session_row(FAKE_UUID)]),
                 transcripts={FAKE_UUID: "/tmp/t/a.jsonl"},
                 window_exc=W.WhoError("laptop unreachable"))
    d = json.loads(json.dumps(r.to_dict()))
    assert d["sessions"][0]["windows_measured"] is False
    assert d["sessions"][0]["window"] is None
    assert d["windows_reason"] == "laptop unreachable"
    assert d["sessions"][0]["transcript"] == "/tmp/t/a.jsonl"


def test_a_transcript_lookup_failure_degrades_ONE_session_not_the_report():
    """One unreadable project dir must not cost the other sessions' answers."""
    def boom(sid, root=None):
        if sid == FAKE_UUID:
            raise OSError("permission denied")
        return Path("/tmp/t/b.jsonl")

    def fetch(task, timeout=0):
        return _task([_session_row(FAKE_UUID), _session_row(FAKE_UUID_2)])

    r = W.resolve("42", task_fetcher=fetch,
                  window_fetcher=lambda timeout=0, host=None: ({}, []),
                  transcript_finder=boom)
    assert r.sessions[0].transcript is None
    assert r.sessions[1].transcript == Path("/tmp/t/b.jsonl")
    assert any("permission denied" in n for n in r.notes)
    assert r.state == W.WHO_RESOLVED


# --------------------------------------------------------------------------- #
# The external hops, without the externals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("rc,expect", [
    (4, "has no task"),
    (3, "exited 3"),
    (6, "exited 6"),
    (7, "exited 7"),
    (8, "exited 8"),
])
def test_fetch_task_reads_clawgatectl_EXIT_CODES_not_its_prose(rc, expect):
    """The codes are documented; the message wording is not a contract."""
    # 🔴 STDOUT IS NON-EMPTY ON PURPOSE. The first version fed `out=""` for
    # every code, so the `not out.strip()` fallback produced an identical
    # message and deleting the whole exit-code table SURVIVED — the test named
    # for "reads exit codes, not prose" did not pin the table at all.
    def runner(cmd, timeout):
        return rc, '{"id": 42}', "some diagnostic"

    with pytest.raises(W.WhoError) as exc:
        W.fetch_task("42", runner=runner)
    assert expect in str(exc.value)


def test_fetch_task_refuses_an_empty_id_before_shelling_out():
    """An empty path parameter is the doubled-slash 301 class clawgatectl names."""
    called = []

    def runner(cmd, timeout):
        called.append(cmd)
        return 0, "{}", ""

    for bad in ("", "   "):
        with pytest.raises(W.WhoError):
            W.fetch_task(bad, runner=runner)
    assert not called, "an empty id reached the network"


def test_fetch_task_rejects_non_json_rather_than_returning_a_blank():
    """An Authelia portal page is a 200 with HTML — never a task with no sessions."""
    def runner(cmd, timeout):
        return 0, "<html>login</html>", ""

    with pytest.raises(W.WhoError) as exc:
        W.fetch_task("42", runner=runner)
    assert "non-JSON" in str(exc.value)


def test_live_windows_reports_a_nonzero_scan_with_no_output_as_an_ERROR():
    """Not an empty index — that would render as 'no session is anywhere'."""
    def runner(cmd, timeout):
        return 1, "", "tmux: no server running"

    with pytest.raises(W.WhoError) as exc:
        W.live_windows(runner=runner, script=Path(__file__))
    assert "no server running" in str(exc.value)


def test_live_windows_does_NOT_pass_lean(tmp_path):
    """🔴 `--lean` omits `pane_id`, `window_id` and `codename`.

    Three of the four things this command prints. Pinned because `--lean` is the
    faster flag and an obvious future 'optimisation'.
    """
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return 0, json.dumps(_scan([])), ""

    W.live_windows(runner=runner, script=Path(__file__))
    assert "--lean" not in seen["cmd"]
    assert "--json" in seen["cmd"]


def test_live_windows_forwards_a_host_filter_only_when_asked():
    seen = []

    def runner(cmd, timeout):
        seen.append(cmd)
        return 0, json.dumps(_scan([])), ""

    W.live_windows(runner=runner, script=Path(__file__))
    W.live_windows(runner=runner, script=Path(__file__), host="laptop")
    assert "--host" not in seen[0]
    assert seen[1][seen[1].index("--host") + 1] == "laptop"


def _code_strings(path: Path) -> list[str]:
    """Every string literal in EXECUTABLE code — docstrings excluded.

    🔴 THIS HELPER EXISTS BECAUSE THE FIRST VERSIONS OF THE TWO GUARDS BELOW
    WERE SPELLED, NOT STRUCTURAL. They grepped the raw source, so they fired on
    the module's own PROSE — the docstring sentence explaining what `tmux
    switch-client` accepts, and the comment saying never to merge the streams.
    A guard that a comment can trip is a guard a reword can also silence, in
    both directions. Parsing and dropping docstrings makes the claim structural:
    it is about what the code DOES, not about which words appear in the file.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_the_helper_that_makes_these_guards_structural_actually_drops_docstrings(tmp_path):
    """CONTROL. Without this, `_code_strings` returning [] passes both guards.

    A helper that silently yields nothing turns two assertions into no
    assertions, which is the exact shape it was written to remove.
    """
    f = tmp_path / "m.py"
    f.write_text('"""a docstring saying switch-client."""\nX = "a code string"\n')
    got = _code_strings(f)
    assert got == ["a code string"], got


def test_the_two_streams_are_captured_SEPARATELY():
    """`clawgatectl` guarantees stdout is JSON and nothing else ever is.

    Merging stderr in would throw that guarantee away at the one place that
    relies on it, so the runner must never be asked to combine them.
    """
    import ast

    src = (LIB / "cairn_who.py").read_text(encoding="utf-8")
    assert "2>&1" not in " ".join(_code_strings(LIB / "cairn_who.py"))
    # the structural half: no `stderr=` keyword anywhere in a call
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                assert kw.arg != "stderr", "a call redirects stderr"


def test_who_never_raises_or_focuses_a_window():
    """🔴 The operator's screen is not this command's to take (RULES.md).

    `who` prints an address; moving there is the human's call. A future 'jump
    to it' convenience belongs behind an explicit flag, not in a resolver.
    """
    code = " ".join(_code_strings(LIB / "cairn_who.py"))
    for verb in ("switch-client", "select-window", "windowactivate",
                 "i3-msg", "wmctrl", "attach-session"):
        assert verb not in code, f"who reaches for {verb!r} — that is screen theft"


def test_who_owns_its_own_timeout_default_and_accepts_the_flag_AFTER_the_subcommand():
    """🔴 `cairn`'s top-level `--timeout` is 20s, tuned for an HTTP fetch.

    `who` shells into tmux on two hosts, and `DEFAULT_TIMEOUT` here carries a
    docstring reasoning specifically about a sleeping laptop. Inheriting the
    store's bound meant the documented rationale was not what shipped — and
    `cairn who --timeout N` was rejected outright, because the flag only existed
    BEFORE the subcommand where nobody would type it.
    """
    import subprocess as sp

    cairn = REPO_ROOT / "scripts" / "cairn"
    help_out = sp.run([sys.executable, str(cairn), "who", "--help"],
                      capture_output=True, text=True, timeout=60).stdout
    assert "--timeout" in help_out, "who does not accept --timeout after the subcommand"
    assert f"default {W.DEFAULT_TIMEOUT}" in help_out, (
        f"the help advertises a default that is not who's own:\n{help_out}")
