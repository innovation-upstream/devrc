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

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
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
                last_activity_ts=TS_NOW, window_id="@41", tmux_pid="4025325",
                host="workbench", transcript_path="/t/a.jsonl")
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
                          "last_activity_ts", "window_id", "tmux_pid", "host",
                          "transcript_path"}
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
    r = rec(window_id=None, tmux_pid=None, host="", transcript_path=None)
    assert r["window_id"] is None and r["tmux_pid"] is None
    assert r["host"] is None and r["transcript_path"] is None


def test_a_tmux_record_is_keyed_on_its_WINDOW_and_a_pane_less_one_on_its_SESSION():
    """🔴 KILLS: keying every record on the session id.

    fuzzyclaw named files `<index>.json` and so let two files claim one window,
    which cost that window its record entirely. One file per window means a
    window that hosts three sessions in a day holds one record, not three.
    """
    assert AL.record_filename(rec()) == "claude-41.json"
    assert AL.record_filename(rec(window_id=None)) == "claude-s-sess-aaaa.json"
    # a second session in the SAME window overwrites rather than accumulating
    assert AL.record_filename(rec(session_id="sess-bbbb")) == "claude-41.json"


def test_a_filename_can_never_escape_its_directory():
    """KILLS: interpolating an id into a path without sanitising. `window_id`
    reaches this as JSON written by a process on another host, so the assertion
    is the property that matters — the joined path stays in the directory — not
    the spelling of the substitution."""
    for hostile in ("../../etc/passwd", "/etc/shadow", "a/b", ".."):
        name = AL.record_filename(rec(window_id=hostile))
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
    assert AL.write_record(rec(window_id="@52", session_id="sess-bbbb"),
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
    with open(os.path.join(d, "claude-41.json")) as fh:
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
    assert sorted(os.listdir(tmp_path)) == ["claude-41.json"]


def test_prune_removes_the_old_keeps_the_fresh_and_REPORTS_what_it_examined(
        tmp_path):
    """🔴 `examined` travels beside `removed`, always. "0 removed" from a sweep
    that walked an empty directory and "0 removed, 3 examined, none old enough"
    are different facts and only the second is a clean bill of health.
    """
    d = str(tmp_path)
    AL.write_record(rec(window_id="@1",
                        last_activity_ts=AL.now_iso(NOW - 8 * 86400)),
                    directory=d)
    AL.write_record(rec(window_id="@2", last_activity_ts=AL.now_iso(NOW)),
                    directory=d)
    out = AL.prune(directory=d, max_age_secs=7 * 86400, now=NOW)
    assert (out["examined"], out["removed"], out["kept"]) == (2, 1, 1)
    assert os.listdir(d) == ["claude-2.json"]


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
    """A clawgate agent has no pane. It is not a tmux row and must not be
    silently attached to one — it goes to `unjoinable`, counted separately."""
    out = AL.filter_live([rec(window_id=None)], LIVE, tmux_pid="4025325")
    assert out["records"] == [] and out["no_window"] == 1
    assert len(out["unjoinable"]) == 1


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
                                 "session_ids": ["new", "old"]}]
    # order-independent: the newest wins whichever way round they arrive
    assert AL.index_by_window([newer, older])["index"]["@41"]["session_id"] \
        == "new"


def test_the_index_is_silent_when_there_is_nothing_to_contend():
    out = AL.index_by_window([rec(), rec(window_id="@52")])
    assert set(out["index"]) == {"@41", "@52"} and out["conflicts"] == []
