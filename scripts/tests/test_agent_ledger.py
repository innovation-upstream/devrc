#!/usr/bin/env python3
"""Tests for `scripts/lib/agent_ledger.py` — the agent activity ledger.

Every guard here was watched to fail against a deliberately broken copy of the
function it pins; the mutation matrix is in the PR body, not restated per test.

🔴 WHAT THIS FILE DELIBERATELY DOES NOT ASSERT: the tmux server pid that
`read_command` fetches live. It is present on a dev host and absent in the nix
build sandbox, so an assertion on its VALUE would pass in one tier and fail in the
other — the two-tier trap in claude/RULES.md. The pid's PARSING is pinned from
fixture strings instead (both branches), and the end-to-end test asserts only what
is true in both tiers.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, os.pardir)))
from testlib import mockbin  # noqa: E402
_MODULE = os.path.join(_HERE, os.pardir, "lib", "agent_ledger.py")


def _load():
    loader = importlib.machinery.SourceFileLoader("agent_ledger_undertest",
                                                  _MODULE)
    spec = importlib.util.spec_from_file_location(
        "agent_ledger_undertest", _MODULE, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


AL = _load()

NOW = 1755000000.0            # a fixed epoch; every age in this file is derived
TS_NOW = AL.now_iso(NOW)


def rec(**kw):
    """A valid record with overridable fields.

    🔴 The defaults are PAIRWISE DISTINCT — `@41` / `4025325` / a session id that
    is neither — so a mutant that returns the wrong field cannot satisfy an
    assertion by accident. A fixture whose fields share a value certifies nothing.
    """
    base = dict(runtime="claude", session_id="sess-aaaa",
                last_activity_ts=TS_NOW, window_id="@41", pane_id="%77",
                tmux_pid="4025325", host="workbench",
                transcript_path="/t/a.jsonl")
    base.update(kw)
    return AL.build_record(**base)


# =========================================================================== #
# the record
# =========================================================================== #
def test_build_record_carries_exactly_the_documented_fields():
    """🔴 The FIELD LEDGER, failing when the set grows OR shrinks. A field that
    vanishes turns the reader's join into a permanent None; one that appears
    undocumented is a wire contract nobody reviewed."""
    assert set(rec()) == {"schema", "runtime", "session_id",
                          "last_activity_ts", "window_id", "pane_id",
                          "tmux_pid", "host", "transcript_path"}
    assert rec()["schema"] == AL.SCHEMA


@pytest.mark.parametrize("missing", ["runtime", "session_id",
                                     "last_activity_ts"])
def test_a_record_that_cannot_be_joined_RAISES_rather_than_writing_a_hollow_one(
        missing):
    """🔴 KILLS: `return None` or a partial dict on a missing key.

    A record with no `session_id` is exactly the row the ClickHouse join cannot
    resolve — writing it would restore the #419 symptom (`claude_session_id` on 0
    rows) underneath a ledger reporting records live.
    """
    with pytest.raises(ValueError):
        rec(**{missing: ""})


def test_the_optional_fields_are_None_not_empty_string():
    """`None` means "does not apply" everywhere in session-manager. An empty
    string is falsy in Python and truthy in JSON-consumer terms, and the row's
    `claude_session_id` fallback branches on truthiness."""
    r = rec(window_id=None, pane_id=None, tmux_pid=None, host="",
            transcript_path=None)
    assert r["window_id"] is None and r["tmux_pid"] is None
    assert r["pane_id"] is None
    assert r["host"] is None and r["transcript_path"] is None


def test_a_record_is_keyed_on_its_PANE_falling_back_to_window_then_session():
    """🔴 KILLS: keying on the session id (a file per session ever run — the
    fuzzyclaw rot), and KILLS: keying on the window (see the test below).

    Three tiers, and each is reachable: a tmux pane, a tmux record with no pane
    id, and a runtime with no tmux presence at all.
    """
    assert AL.record_filename(rec()) == "claude-p77.json"
    assert AL.record_filename(rec(pane_id=None)) == "claude-41.json"
    assert AL.record_filename(
        rec(pane_id=None, window_id=None)) == "claude-s-sess-aaaa.json"
    # a second session in the SAME pane overwrites rather than accumulating
    assert AL.record_filename(rec(session_id="sess-bbbb")) == "claude-p77.json"


def test_two_CLAUDE_PANES_IN_ONE_WINDOW_get_two_files_not_one():
    """🔴 THE CO-TENANCY BUG, pinned. Keyed on the window, two live agents
    sharing a window overwrite each other: the throttle is session-scoped, so
    alternating writers never throttle, the single file ends up naming whichever
    wrote last, and `index_by_window` cannot see any of it because there is only
    ever one file to compare. `row["claude_session_id"]` is the sole carrier into
    the ClickHouse join, so the window silently resolved to an arbitrary one of
    its two agents.

    Two files, one `window_id`, is exactly the shape the conflict detector
    already reports. KILLS: dropping `pane_id` from the file key.
    """
    a = rec(pane_id="%77", session_id="agent-a")
    b = rec(pane_id="%78", session_id="agent-b")
    assert AL.record_filename(a) != AL.record_filename(b)
    assert a["window_id"] == b["window_id"] == "@41"
    idx = AL.index_by_window([a, b])
    assert idx["conflicts"] and idx["conflicts"][0]["claimants"] == 2
    assert idx["conflicts"][0]["session_ids"] == ["agent-a", "agent-b"]


def test_two_alternating_sessions_in_ONE_PANE_still_both_write(tmp_path):
    """The measured failure that motivated the key change, now confined to the
    case where it is CORRECT: alternating writers in the same pane really are a
    handover, and each must claim the pane immediately. 10 writes, 10 land."""
    d = str(tmp_path)
    for i in range(10):
        out = AL.write_record(
            rec(session_id="sess-%s" % ("aaaa" if i % 2 else "bbbb"),
                last_activity_ts=AL.now_iso(NOW + i)),
            directory=d, throttle_secs=30, now=NOW + i)
        assert out["written"] is True, i
    assert os.listdir(d) == ["claude-p77.json"]


def test_a_filename_can_never_escape_its_directory():
    """KILLS: interpolating an id into a path without sanitising. `window_id`
    reaches this as JSON written by a process on another host, so the assertion
    is the property that matters — the joined path stays in the directory — not
    the spelling of the substitution."""
    for hostile in ("../../etc/passwd", "/etc/shadow", "a/b", ".."):
        name = AL.record_filename(rec(pane_id=hostile))
        assert os.path.dirname(os.path.join("/ledger", name)) == "/ledger"
        assert os.sep not in name


def test_the_wire_invariant_ONE_line_terminated():
    """🔴 THE WHOLE READ PROTOCOL RESTS ON THIS. Records are read as
    `cat dir/*.json`, so a body with an embedded newline splits into two lines
    (one of them junk) and a body with no trailing newline welds itself onto the
    next file's record. KILLS: dropping the `\\n`, or `json.dumps(..., indent=2)`.
    """
    line = AL.encode_record(rec(transcript_path="/t/a\nb.jsonl"))
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert json.loads(line)["transcript_path"] == "/t/a\nb.jsonl"


def test_valid_record_rejects_an_unknown_schema():
    """KILLS: reading a future record under today's field meanings."""
    good = rec()
    assert AL.valid_record(good)
    assert not AL.valid_record(dict(good, schema=AL.SCHEMA + 1))
    assert not AL.valid_record(dict(good, session_id=""))
    assert not AL.valid_record("not a dict")


# =========================================================================== #
# write + prune
# =========================================================================== #
def test_write_then_read_back_through_the_REAL_read_argv(tmp_path):
    """🔴 THE POSITIVE CONTROL, and it drives the command that SHIPS.

    A reader that returns 0 records is indistinguishable from a reader wired to
    nothing — which is precisely the state the default view was in after #419. So
    before any test may believe a zero, this one has to observe a non-zero come
    back through `read_argv` + `parse_ledger`, the same two functions the live
    path uses. A hand-rolled `cat` here would certify the hand-rolled `cat`.
    """
    d = str(tmp_path)
    assert AL.write_record(rec(), directory=d)["written"] is True
    assert AL.write_record(rec(window_id="@52", pane_id="%78",
                               session_id="sess-bbbb"),
                           directory=d)["written"] is True
    proc = subprocess.run(list(AL.read_argv(abs_dir=d)),
                          capture_output=True, text=True, timeout=10)
    parsed = AL.parse_ledger(proc.stdout)
    assert parsed["measured"] is True
    assert parsed["seen"] == 2 and parsed["unparseable"] == 0
    assert {r["window_id"] for r in parsed["records"]} == {"@41", "@52"}


def test_the_negative_control_an_EMPTY_directory_is_measured_not_missing(tmp_path):
    """The other half of the control above: the same command over a directory
    with no records still prints its sentinel, so `measured` is True and `seen`
    is a real 0 rather than the None of a read that never happened."""
    proc = subprocess.run(list(AL.read_argv(abs_dir=str(tmp_path))),
                          capture_output=True, text=True, timeout=10)
    parsed = AL.parse_ledger(proc.stdout)
    assert parsed["measured"] is True and parsed["seen"] == 0


def test_a_nonexistent_directory_is_also_measured_and_empty(tmp_path):
    """A host that has never run an agent has no ledger directory. That is
    `0 records`, not an error — `exit 0` in the command is what makes it so."""
    proc = subprocess.run(
        list(AL.read_argv(abs_dir=str(tmp_path / "never-created"))),
        capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert AL.parse_ledger(proc.stdout)["measured"] is True


def test_the_throttle_suppresses_the_SAME_session_and_never_a_DIFFERENT_one(
        tmp_path):
    """🔴 THE ONE THAT MATTERS. KILLS: throttling on the file's mtime, or on the
    timestamp alone without comparing `session_id`.

    Throttling a repeat write from the same session is the point (PostToolUse
    fires on every tool call). Throttling a write from a DIFFERENT session would
    keep the departed session's id winning the join for a full interval — one
    session's ClickHouse history published under another session's window.
    """
    d = str(tmp_path)
    AL.write_record(rec(), directory=d, now=NOW)
    same = AL.write_record(rec(last_activity_ts=AL.now_iso(NOW + 5)),
                           directory=d, throttle_secs=30, now=NOW + 5)
    assert same["written"] is False and same["reason"] == "throttled"

    other = AL.write_record(
        rec(session_id="sess-bbbb", last_activity_ts=AL.now_iso(NOW + 6)),
        directory=d, throttle_secs=30, now=NOW + 6)
    assert other["written"] is True, (
        "a new session in this window must claim it immediately")
    with open(os.path.join(d, "claude-p77.json")) as fh:
        assert json.loads(fh.read())["session_id"] == "sess-bbbb"


def test_the_throttle_expires(tmp_path):
    """Measured at two points, not one: inside the interval it suppresses,
    outside it writes. A guard pinned at a single point is not a claim about a
    threshold."""
    d = str(tmp_path)
    AL.write_record(rec(), directory=d, now=NOW)
    assert AL.write_record(rec(last_activity_ts=AL.now_iso(NOW + 29)),
                           directory=d, throttle_secs=30,
                           now=NOW + 29)["written"] is False
    assert AL.write_record(rec(last_activity_ts=AL.now_iso(NOW + 31)),
                           directory=d, throttle_secs=30,
                           now=NOW + 31)["written"] is True


def test_write_leaves_no_temp_file_behind(tmp_path):
    """The reader globs `*.json`; a leaked `.ledger.*.tmp` would not match, but a
    leaked file in the ledger's own directory is the rot fuzzyclaw died of."""
    AL.write_record(rec(), directory=str(tmp_path))
    assert sorted(os.listdir(tmp_path)) == ["claude-p77.json"]


def test_prune_removes_the_old_keeps_the_fresh_and_REPORTS_what_it_examined(
        tmp_path):
    """🔴 `examined` travels beside `removed`, always. "0 removed" from a sweep
    that walked an empty directory and "0 removed, 3 examined, none old enough"
    are different facts and only the second is a clean bill of health.
    """
    d = str(tmp_path)
    AL.write_record(rec(pane_id="%1",
                        last_activity_ts=AL.now_iso(NOW - 8 * 86400)),
                    directory=d)
    AL.write_record(rec(pane_id="%2", last_activity_ts=AL.now_iso(NOW)),
                    directory=d)
    out = AL.prune(directory=d, max_age_secs=7 * 86400, now=NOW)
    assert (out["examined"], out["removed"], out["kept"]) == (2, 1, 1)
    assert os.listdir(d) == ["claude-p2.json"]


def test_prune_KEEPS_a_record_whose_timestamp_it_cannot_read(tmp_path):
    """🔴 KILLS: treating an unparseable age as an old one. Pruning is the
    destructive path, so an unreadable record must survive and be COUNTED."""
    d = str(tmp_path)
    with open(os.path.join(d, "claude-99.json"), "w") as fh:
        fh.write("{not json at all}\n")
    out = AL.prune(directory=d, max_age_secs=1, now=NOW)
    assert (out["examined"], out["removed"], out["unparseable"]) == (1, 0, 1)
    assert os.listdir(d) == ["claude-99.json"]


def test_prune_on_a_missing_directory_reports_an_error_not_a_clean_sweep(
        tmp_path):
    out = AL.prune(directory=str(tmp_path / "nope"), now=NOW)
    assert out["error"] and out["examined"] == 0


# =========================================================================== #
# the read protocol
# =========================================================================== #
def test_the_read_command_expands_HOME_on_the_MACHINE_THAT_RUNS_IT():
    """🔴 KILLS: baking this host's absolute home into the command.

    The same string runs locally and, via `shlex.join`, inside a shell on the
    laptop. A literal `/home/zach/...` would work by coincidence today and break
    the moment either host's home differs — silently, as "no records".
    """
    cmd = AL.read_command()
    assert '"$HOME"/' in cmd
    assert os.path.expanduser("~") not in cmd


def test_the_read_command_starts_with_its_own_positive_control():
    """The sentinel is what makes an empty answer readable. KILLS: dropping the
    `echo`, which would make every empty ledger indistinguishable from a
    swallowed command."""
    assert AL.read_command().startswith("echo ")
    assert AL.SENTINEL in AL.read_command()


def test_the_sentinel_survives_sh_which_a_leading_hash_would_not():
    """🔴 REGRESSION GUARD on a bug this file had before it shipped: a sentinel
    beginning with `#` is a COMMENT to `sh`, so `echo` printed an empty line and
    the sentinel never arrived — indistinguishable from a host that did not
    answer. Pins the property (no shell metacharacter), not the spelling."""
    assert not any(c in AL.SENTINEL for c in "#;&|<>$`'\"()* \t")
    proc = subprocess.run(["sh", "-c", "echo %s" % AL.SENTINEL],
                          capture_output=True, text=True, timeout=5)
    assert proc.stdout.strip() == AL.SENTINEL


def test_an_abs_dir_containing_a_space_or_a_quote_is_still_read(tmp_path):
    """The command is a SHELL string, so the one injectable parameter is quoted
    with `shlex.quote` rather than concatenated."""
    d = tmp_path / "a dir with 'quotes'"
    d.mkdir()
    AL.write_record(rec(), directory=str(d))
    proc = subprocess.run(list(AL.read_argv(abs_dir=str(d))),
                          capture_output=True, text=True, timeout=10)
    assert len(AL.parse_ledger(proc.stdout)["records"]) == 1


def test_no_sentinel_is_UNMEASURED_with_every_count_NULL():
    """🔴 THE FABRICATED ZERO, refused. Empty stdout from a swallowed command and
    a host with no records are the same bytes; only the sentinel separates them,
    and without it every count is None rather than 0."""
    for raw in ("", "some unrelated output\n", None):
        out = AL.parse_ledger(raw)
        assert out["measured"] is False
        assert out["seen"] is None and out["unparseable"] is None
        assert out["records"] == [] and out["tmux_pid"] is None


def test_the_sentinel_line_carries_the_tmux_pid_and_tolerates_its_absence():
    """Both branches, from fixtures — see this file's header for why the LIVE pid
    is never asserted."""
    with_pid = AL.parse_ledger("%s 4025325\n" % AL.SENTINEL)
    assert with_pid["measured"] is True and with_pid["tmux_pid"] == "4025325"
    # a host with no tmux server: read, but the generation cannot be checked
    without = AL.parse_ledger("%s \n" % AL.SENTINEL)
    assert without["measured"] is True and without["tmux_pid"] is None


def test_junk_and_wrong_schema_lines_are_COUNTED_not_silently_dropped():
    raw = "\n".join([
        "%s 7" % AL.SENTINEL,
        json.dumps(rec()),
        "{ this is not json",
        json.dumps(dict(rec(window_id="@52"), schema=999)),
        "",
    ])
    out = AL.parse_ledger(raw)
    assert out["seen"] == 3 and out["unparseable"] == 2
    assert len(out["records"]) == 1


def test_a_record_line_that_happens_to_repeat_the_sentinel_is_not_a_new_header():
    """The header is the FIRST sentinel line; a later one is data (or junk) and
    must not reset the parse and re-zero the counts."""
    raw = "%s 7\n%s\n%s\n" % (AL.SENTINEL, json.dumps(rec()), AL.SENTINEL)
    out = AL.parse_ledger(raw)
    assert out["tmux_pid"] == "7"
    assert len(out["records"]) == 1 and out["unparseable"] == 1


# =========================================================================== #
# the live filter — the join guard
# =========================================================================== #
LIVE = {"@41": ("scratch7", "3"), "@52": ("misc", "5")}


def test_an_unmeasured_window_list_keeps_NOTHING_and_counts_NOTHING():
    """🔴 Both mistakes have shipped in this tool before, on this exact join:
    returning the unfiltered set publishes records for windows that may not
    exist, and returning an empty set with `status: ok` publishes a measured zero
    for a measurement nobody took."""
    out = AL.filter_live([rec()], None, tmux_pid="4025325",
                         unmeasured_reason="list-windows did not answer")
    assert out["status"] == "unmeasured"
    assert out["records"] == []
    for key in ("seen", "live", "not_live", "generation_mismatch",
                "generation_unchecked", "no_window"):
        assert out[key] is None, key
    assert "list-windows" in out["error"]


def test_a_record_for_a_dead_window_is_dropped_and_counted():
    out = AL.filter_live([rec(window_id="@999")], LIVE, tmux_pid="4025325")
    assert out["live"] == 0 and out["not_live"] == 1 and out["records"] == []


def test_a_record_from_an_OLDER_TMUX_SERVER_is_dropped():
    """🔴 THE GENERATION GUARD. tmux window ids restart at `@0` when the server
    does, so after a reboot yesterday's `@41` record and today's `@41` window
    collide. Without this the fresh window inherits a dead session's id and a
    multi-day age — a confident wrong value, which is the one thing this tool
    must not emit. KILLS: joining on `window_id` alone.
    """
    out = AL.filter_live([rec(tmux_pid="111")], LIVE, tmux_pid="4025325")
    assert out["records"] == []
    assert out["generation_mismatch"] == 1 and out["not_live"] == 0


def test_a_matching_generation_is_KEPT_and_not_counted_as_unchecked():
    """The positive half of the guard above — it must let the live case through,
    or it would be an unreachable guard that passes every mutation by rejecting
    everything."""
    out = AL.filter_live([rec()], LIVE, tmux_pid="4025325")
    assert out["live"] == 1 and out["generation_unchecked"] == 0
    assert out["records"][0]["session_id"] == "sess-aaaa"


def test_an_UNCHECKABLE_generation_is_kept_but_declared():
    """🔴 KEPT, and visible. A record written before the field existed, or read
    from a host with no tmux server, is being TRUSTED — and a reader is entitled
    to know how many of the live rows rest on that. KILLS: counting it as
    verified, and KILLS: dropping it silently."""
    for record, pid in ((rec(tmux_pid=None), "4025325"), (rec(), None)):
        out = AL.filter_live([record], LIVE, tmux_pid=pid)
        assert out["live"] == 1 and out["generation_unchecked"] == 1


def test_a_record_with_no_window_is_set_ASIDE_never_joined():
    """A Claude run outside tmux has no window. It is not a tmux row and must
    not be silently attached to one — it is COUNTED under `no_window` and
    dropped. A count, not a list: an earlier revision returned the records too
    and every caller discarded them."""
    out = AL.filter_live([rec(window_id=None)], LIVE, tmux_pid="4025325")
    assert out["records"] == [] and out["no_window"] == 1


def test_the_counts_account_for_every_record_seen():
    """🔴 STRUCTURAL: nothing may disappear between `seen` and the dispositions.
    A record that is neither kept nor counted under a rejection reason is a
    record the report lost."""
    records = [rec(), rec(window_id="@999"), rec(tmux_pid="111"),
               rec(window_id=None)]
    out = AL.filter_live(records, LIVE, tmux_pid="4025325")
    assert out["seen"] == 4
    assert (out["live"] + out["not_live"] + out["generation_mismatch"]
            + out["no_window"]) == out["seen"]


# =========================================================================== #
# the window index
# =========================================================================== #
def test_two_records_claiming_one_window_resolve_to_the_NEWEST_and_are_reported():
    """🔴 Deliberately DIFFERENT from fuzzyclaw's resolution, which dropped the
    contested window entirely. Keying on `window_id` means a duplicate can only
    come from a filename collision or a hand-edited ledger, so the newest wins —
    but a silent tie-break is how a wrong session id ships without a trace, so
    the conflict is reported either way."""
    older = rec(session_id="old", last_activity_ts=AL.now_iso(NOW - 600))
    newer = rec(session_id="new", last_activity_ts=AL.now_iso(NOW))
    out = AL.index_by_window([older, newer])
    assert out["index"]["@41"]["session_id"] == "new"
    assert out["conflicts"] == [{"window_id": "@41", "claimants": 2,
                                 "session_ids": ["new", "old"],
                                 "runtimes": ["claude"]}]
    # order-independent: the newest wins whichever way round they arrive
    assert AL.index_by_window([newer, older])["index"]["@41"]["session_id"] \
        == "new"


def test_the_index_is_silent_when_there_is_nothing_to_contend():
    out = AL.index_by_window([rec(), rec(window_id="@52")])
    assert set(out["index"]) == {"@41", "@52"} and out["conflicts"] == []


# =========================================================================== #
# the throttle predicate, and the reader-side newline fix
# =========================================================================== #
def test_is_throttled_is_the_ONE_definition_write_record_uses(tmp_path):
    """🔴 The predicate is public because the HOOK consults it before spawning
    `tmux` — the hot path, where most `PostToolUse` calls are throttled. Two
    copies of a throttle rule is how the cheap path and the correct path drift
    apart, so this pins that `write_record` agrees with it rather than carrying
    its own.
    """
    d = str(tmp_path)
    AL.write_record(rec(), directory=d, now=NOW)
    path = os.path.join(d, "claude-p77.json")

    assert AL.is_throttled(path, "sess-aaaa", 30, now=NOW + 5) is True
    assert AL.write_record(rec(last_activity_ts=AL.now_iso(NOW + 5)),
                           directory=d, throttle_secs=30,
                           now=NOW + 5)["written"] is False

    # a DIFFERENT session is never throttled — both agree
    assert AL.is_throttled(path, "sess-bbbb", 30, now=NOW + 5) is False
    # ...and neither is any session once the interval has passed
    assert AL.is_throttled(path, "sess-aaaa", 30, now=NOW + 31) is False
    # no throttle configured, or no record yet: nothing to suppress
    assert AL.is_throttled(path, "sess-aaaa", None, now=NOW) is False
    assert AL.is_throttled(os.path.join(d, "nope.json"), "x", 30,
                           now=NOW) is False


def test_a_record_with_NO_TRAILING_NEWLINE_does_not_eat_its_neighbour(tmp_path):
    """🔴 REGRESSION GUARD on a class the reader now closes. `cat` concatenates
    bytes, so a file lacking its final newline welds onto the next file in the
    glob and BOTH records are lost — measured on the first draft: 3 written, 1
    parsed, 2 counted unparseable. `write_record` always terminates its line, but
    it will not be the only writer (spec §4 adds opencode and clawgate), and
    "every writer remembers" is a convention, not a guarantee.

    KILLS: reverting `awk 1` to `cat` in `read_command`.
    """
    d = str(tmp_path)
    for i, sess in enumerate(("a", "b", "c")):
        body = json.dumps(rec(pane_id="%%%d" % i, session_id="sess-%s" % sess))
        # deliberately UNTERMINATED — the shape the guard exists for
        with open(os.path.join(d, "claude-p%d.json" % i), "w") as fh:
            fh.write(body)
    proc = subprocess.run(list(AL.read_argv(abs_dir=d)),
                          capture_output=True, text=True, timeout=10)
    parsed = AL.parse_ledger(proc.stdout)
    assert parsed["seen"] == 3 and parsed["unparseable"] == 0
    assert {r["session_id"] for r in parsed["records"]} == {
        "sess-a", "sess-b", "sess-c"}


def test_prune_reaps_a_leaked_temp_file_which_only_it_can_see(tmp_path):
    """🔴 `write_record` cleans its temp file on a Python exception, but a
    SIGKILL between `mkstemp` and `os.replace` leaks one — and prune otherwise
    only ever looks at `*.json`, so that leak would be PERMANENT. Reaped by mtime
    because a temp file carries no parseable record.

    The fresh temp is left alone, so this is not "delete every temp".
    """
    d = str(tmp_path)
    old = os.path.join(d, ".ledger.abc.tmp")
    new = os.path.join(d, ".ledger.def.tmp")
    for path, mtime in ((old, NOW - 8 * 86400), (new, NOW)):
        with open(path, "w") as fh:
            fh.write("half a re")
        os.utime(path, (mtime, mtime))
    out = AL.prune(directory=d, max_age_secs=7 * 86400, now=NOW)
    assert out["temps_removed"] == 1
    assert os.path.exists(new) and not os.path.exists(old)
    # temps are NOT counted as records — `examined` describes the ledger
    assert out["examined"] == 0


# =========================================================================== #
# the degraded-tmux path — a pane with no window
# =========================================================================== #
def test_a_pane_with_NO_WINDOW_does_not_land_in_the_pane_file(tmp_path):
    """🔴 REGRESSION GUARD, and the regression was introduced by the fix that
    added pane keying. `$TMUX_PANE` is set but `tmux display-message` can still
    fail — a 2s timeout under load, a dead or restarted server — leaving a record
    with a pane and NO `window_id`.

    Keyed on the pane alone it OVERWRITES the good record in place, and the
    window loses `age_secs`, `claude_session_id` and its `stale` bucket outright:
    the #419 symptom, reproduced silently, and rendered as a *measured*
    `no_window` rejection rather than as a fault. Worst on `Stop`, the last event
    before a window goes idle, so it would persist for the whole idle period.

    KILLS: dropping `and window_id` from `filename_for`'s pane branch.
    """
    good = rec(pane_id="%77", window_id="@41")
    degraded = rec(pane_id="%77", window_id=None, tmux_pid=None)
    assert AL.record_filename(good) == "claude-p77.json"
    assert AL.record_filename(degraded) == "claude-s-sess-aaaa.json"

    d = str(tmp_path)
    AL.write_record(good, directory=d)
    AL.write_record(degraded, directory=d)
    assert sorted(os.listdir(d)) == ["claude-p77.json", "claude-s-sess-aaaa.json"]

    # ...and the consequence at the CONSUMING site: the window is still joinable
    proc = subprocess.run(list(AL.read_argv(abs_dir=d)),
                          capture_output=True, text=True, timeout=10)
    filt = AL.filter_live(AL.parse_ledger(proc.stdout)["records"],
                          {"@41": ("scratch7", "3")}, tmux_pid="4025325")
    assert (filt["seen"], filt["live"], filt["no_window"]) == (2, 1, 1)
    assert filt["records"][0]["window_id"] == "@41"


def test_pane_filename_is_the_SAME_SPELLING_the_write_path_uses():
    """The hook predicts the pane file before it knows the window, so the check
    and the write must agree on the name. KILLS: a second format string."""
    assert AL.pane_filename("claude", "%77") == "claude-p77.json"
    assert AL.pane_filename("claude", "%77") == AL.record_filename(
        rec(pane_id="%77", window_id="@41"))


def test_the_pane_file_is_namespaced_BY_RUNTIME():
    """🔴 KILLS: hardcoding `"claude"` inside `pane_filename`.

    Latent today — Claude is the only writer — but spec §4 adds opencode next,
    and opencode runs in tmux panes too. Two runtimes sharing a pane's filename
    would make them overwrite each other's records, which is the co-tenancy bug
    again with a different pair of writers. The runtime handling MOVED into this
    function when the hook's pre-tmux prediction needed it, so it is pinned here
    rather than left to the writer that will trip on it.
    """
    assert AL.pane_filename("opencode", "%77") == "opencode-p77.json"
    assert AL.pane_filename("claude", "%77") != AL.pane_filename("opencode",
                                                                 "%77")
    # ...and the full builder agrees, so the prediction and the write stay in
    # step for a non-claude runtime too
    assert AL.record_filename(rec(runtime="opencode")) == "opencode-p77.json"


def test_the_tie_break_resolves_on_the_record_read_FIRST():
    """🔴 The docstring CLAIMS ties resolve on the record read first. That claim
    was unpinned, so `>` -> `>=` in `index_by_window` survived — silently
    inverting it to last-wins. A comment is a claim too.

    Identical timestamps, distinct session ids, both orders asserted.
    """
    ts = AL.now_iso(NOW)
    a = rec(pane_id="%1", session_id="first", last_activity_ts=ts)
    b = rec(pane_id="%2", session_id="second", last_activity_ts=ts)
    assert AL.index_by_window([a, b])["index"]["@41"]["session_id"] == "first"
    assert AL.index_by_window([b, a])["index"]["@41"]["session_id"] == "second"
    # ...and it is still reported as contested either way round
    assert AL.index_by_window([a, b])["conflicts"][0]["claimants"] == 2


# =========================================================================== #
# the --write CLI, and writer 2 (opencode)
# =========================================================================== #
def _stub_tmux(bindir, answer="@41|4025325"):
    """A stub `tmux` on PATH so the CLI can resolve a window hermetically.

    🔴 Without one the CLI takes the DEGRADED path — `$TMUX_PANE` set, tmux
    unable to answer — and correctly writes a session-keyed file instead of a
    pane-keyed one. That is the regression guard from `filename_for` doing its
    job, and it is why a fixture that fakes only the pane tests the wrong path.
    Via `testlib.mockbin`, which owns the shebang (`#!/usr/bin/env bash` is dead
    in the nix sandbox).
    """
    return str(mockbin.write_exec(Path(str(bindir)) / "tmux",
                                  "printf '%s\\n'\n" % answer))


def _cli(*args, env=None, directory=None):
    """Drive the REAL CLI as `ledger.js` does — argv, no shell."""
    e = dict(os.environ)
    e.pop("TMUX_PANE", None)
    e.update(env or {})
    argv = [sys.executable, _MODULE, "--write", *args]
    if directory:
        argv += ["--directory", str(directory)]
    return subprocess.run(argv, capture_output=True, text=True, timeout=30,
                          env=e)


def test_the_CLI_writes_a_record_a_runtime_that_cannot_IMPORT_us_can_reach(
        tmp_path):
    """🔴 THE WHOLE POINT OF THE CLI. `scripts/opencode/plugin/ledger.js` is
    JavaScript and cannot import this module, so it spawns it. If it
    re-implemented the record instead, writer 2 would drift from writer 1 and
    from the reader while all three looked correct — the failure this module's
    docstring names. The JS carries arguments; this carries the structure.
    """
    proc = _cli("--runtime", "opencode", "--session", "oc-1",
                directory=tmp_path)
    assert proc.returncode == 0, proc.stderr
    written = json.loads(open(os.path.join(str(tmp_path),
                                           "opencode-s-oc-1.json")).read())
    assert written["runtime"] == "opencode"
    assert written["session_id"] == "oc-1"
    assert written["schema"] == AL.SCHEMA
    # ...and it is a record the READER accepts, not merely valid JSON
    assert AL.valid_record(written)


def test_the_CLI_record_is_readable_through_the_SHIPPING_read_path(tmp_path):
    """End-to-end: what the opencode plugin writes is what session-manager
    reads. A writer test that stops at the file proves the file."""
    _cli("--runtime", "opencode", "--session", "oc-2", directory=tmp_path)
    proc = subprocess.run(list(AL.read_argv(abs_dir=str(tmp_path))),
                          capture_output=True, text=True, timeout=10)
    parsed = AL.parse_ledger(proc.stdout)
    assert parsed["measured"] is True and len(parsed["records"]) == 1
    assert parsed["records"][0]["runtime"] == "opencode"


def test_the_CLI_refuses_a_record_with_no_session_rather_than_writing_a_hollow_one(
        tmp_path):
    """Same rule as `build_record`: no session id means nothing can resolve the
    ClickHouse join, and a hollow record is worse than none. Exit code is
    HONEST even though the only caller ignores it — a CLI that always exits 0
    is untestable."""
    proc = _cli("--runtime", "opencode", "--session", "", directory=tmp_path)
    assert proc.returncode == 1
    assert os.listdir(tmp_path) == []


def test_the_CLI_throttles_on_a_repeat_from_the_SAME_session(tmp_path):
    """The hot path: `tool.execute.after` fires on every opencode tool call."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub_tmux(bindir)
    env = {"TMUX_PANE": "%77",
           "PATH": "%s:%s" % (bindir, os.environ["PATH"])}
    first = _cli("--runtime", "opencode", "--session", "oc-3", env=env,
                 directory=tmp_path)
    assert first.returncode == 0
    before = open(os.path.join(str(tmp_path), "opencode-p77.json")).read()
    second = _cli("--runtime", "opencode", "--session", "oc-3", env=env,
                  directory=tmp_path)
    assert second.returncode == 0
    assert open(os.path.join(str(tmp_path),
                             "opencode-p77.json")).read() == before


def test_TWO_RUNTIMES_in_one_pane_do_not_overwrite_each_other(tmp_path):
    """🔴 THE GUARD THAT WAS BUILT BEFORE ITS WRITER EXISTED. `pane_filename` is
    namespaced by runtime, so a pane that ran Claude and later opencode holds
    ONE record each rather than the second silently replacing the first. This
    became reachable the moment writer 2 landed; it was pinned in advance.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub_tmux(bindir)
    env = {"TMUX_PANE": "%77",
           "PATH": "%s:%s" % (bindir, os.environ["PATH"])}
    _cli("--runtime", "opencode", "--session", "oc-4", env=env,
         directory=tmp_path)
    AL.write_record(rec(runtime="claude", pane_id="%77", session_id="cl-4"),
                    directory=str(tmp_path))
    assert sorted(n for n in os.listdir(tmp_path) if n.endswith(".json")) == [
        "claude-p77.json", "opencode-p77.json"]


def test_a_cross_runtime_conflict_NAMES_THE_RUNTIMES():
    """🔴 The commonest real conflict is cross-runtime, and two opaque session
    ids do not say so. `claude, opencode` is the difference between "which agent
    owns this window" and two UUIDs a reader cannot act on."""
    a = rec(runtime="claude", pane_id="%77", session_id="cl-5",
            last_activity_ts=AL.now_iso(NOW - 600))
    b = rec(runtime="opencode", pane_id="%78", session_id="oc-5",
            last_activity_ts=AL.now_iso(NOW))
    out = AL.index_by_window([a, b])
    assert out["index"]["@41"]["runtime"] == "opencode"   # newest wins
    conflict = out["conflicts"][0]
    assert conflict["runtimes"] == ["claude", "opencode"]
    assert conflict["claimants"] == 2


def test_tmux_context_lives_HERE_so_both_writers_share_one_resolver():
    """🔴 It moved out of the Claude hook when the CLI became its second caller.
    A window/pid resolver copied into each writer is the duplicated predicate
    that ends up wrong at one of its sites."""
    assert callable(AL.tmux_context)
    # no pane => no tmux call at all, and a PAIR of nulls rather than half an
    # answer (a window with no generation is silently trusted downstream)
    called = []
    assert AL.tmux_context(runner=lambda a: called.append(a),
                           pane="") == (None, None)
    assert called == []
