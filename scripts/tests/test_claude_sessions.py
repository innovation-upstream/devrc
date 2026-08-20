"""Unit tests for scripts/lib/claude_sessions.py — the Claude-in-tmux detector.

Exercises the PURE detector against mock inputs (a mock tmux-pane dump + process
tree + activity records). Nothing here touches /proc, tmux, the network or the
filesystem. Also asserts fail-safe: missing / malformed / empty inputs degrade
to None or [], never an exception.

PROVENANCE. These cases were written against `scripts/agent-ops`, the
mission-control TUI, and moved here VERBATIM when that TUI was retired and its
detector was extracted into a shared module (the bar pill
`scripts/i3status-claude-runs` is now its only production consumer). Two edits
were unavoidable and are marked ⚠ MOVED where they occur: assertions that
expressed a defect as the TUI's RENDERED TEXT — `rel_age()` /
`render_active_runs()` — could not come along, because those renderers were
deleted with the TUI. In every case the same test still pins the behaviour
numerically, which is the load-bearing half; what is gone is a second,
downstream expression of the identical fact.
"""
import ast
import datetime
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # scripts/
from testlib import skip_dirs  # noqa: E402

_MODULE = os.path.join(_HERE, "..", "lib", "claude_sessions.py")

_spec = importlib.util.spec_from_loader(
    "claude_sessions",
    importlib.machinery.SourceFileLoader("claude_sessions", _MODULE))
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

import re  # noqa: E402  (used by the format/parser seam test below)


# ---------------------------------------------------------------------------
# parse_panes
# ---------------------------------------------------------------------------
def test_parse_panes_wellformed_and_junk():
    # Paths here are SYNTHETIC — this repo is PUBLIC and a checkout path names a
    # client as surely as a hostname does (CLAUDE.md).
    raw = "\n".join([
        "%0|16060|main|1|@4|devrc ●|/home/zach/workspace/devrc|claude|1700000000",
        "%1|16095|main|2|@16|svc-b|/home/zach/workspace/svc-b|zsh|1700000000",
        "garbage line without pipes",
        "%2|notanint|x|1|@7|w|/p|zsh|1700000000",   # bad pid → dropped
        # The PREVIOUS 9-field shape, before `#{start_time}` joined the format.
        # It must be DROPPED, not parsed with every field after `command`
        # shifted one place left — which is the silent way a format change goes
        # wrong (a title would land in `server_start` and bound nothing).
        "%3|17000|s|1|@9|w|/p|zsh",
    ])
    panes = cs.parse_panes(raw)
    assert len(panes) == 2
    assert panes[0]["pane_pid"] == 16060
    assert panes[0]["window_id"] == "@4"        # the per-window age join's key
    assert panes[0]["window_name"] == "devrc"   # trailing ' ●' stripped
    assert panes[0]["server_start"] == "1700000000"   # the age join's era bound
    assert panes[0]["title"] == ""              # no title field → empty, not dropped
    assert panes[1]["window_id"] == "@16"
    assert panes[1]["command"] == "zsh"
    # title stays LAST and absorbs any trailing pipes it contains
    titled = cs.parse_panes("%9|1|s|3|@9|w|/p|claude|1700000000|✳ do|the|thing")
    assert titled[0]["title"] == "✳ do|the|thing"
    assert titled[0]["server_start"] == "1700000000"


def test_parse_panes_empty():
    assert cs.parse_panes("") == []


def test_pane_format_and_parser_agree_field_for_field():
    """🔴 SEAM. The tmux `-F` format and `parse_panes` are one contract split in
    two, joined only by POSITION, and every way of breaking it is SILENT: drop
    `#{window_id}` from the format and the parser reads window_name into
    `window_id`, the age join misses on every row, and the column degrades to
    "—" with nothing red anywhere. So drive the real format through the real
    parser: substitute a DISTINCT sentinel per tmux variable, then assert each
    parsed key holds the sentinel of the variable it is supposed to carry.
    """
    fields = re.findall(r"#\{([a-z_]+)\}", cs.PANE_FORMAT)
    assert len(fields) == len(cs.PANE_FIELDS)
    assert "window_id" in fields                 # the age join's key

    line = cs.PANE_FORMAT
    for i, var in enumerate(fields):
        line = line.replace("#{%s}" % var, "1234" if var == "pane_pid"
                            else "V%d-%s" % (i, var))
    pane = cs.parse_panes(line)[0]
    for i, (var, key) in enumerate(zip(fields, cs.PANE_FIELDS)):
        want = 1234 if var == "pane_pid" else "V%d-%s" % (i, var)
        assert pane[key] == want, (
            "format field #%d %r landed in %r as %r" % (i, var, key, pane[key]))
    # 🔴 The loop above CANNOT catch a reordering of the format, because it
    # derives the expected sentinel from the same position it reads back — swap
    # two `#{...}` and it stays self-consistent and green. MEASURED: a mutant
    # swapping `#{start_time}` and `#{pane_title}` survived everything else in
    # this file. So pin the NAME pairing in full, not two of its entries.
    assert dict(zip(cs.PANE_FIELDS, fields)) == {
        "pane_id": "pane_id",
        "pane_pid": "pane_pid",
        "session": "session_name",
        "window_index": "window_index",
        "window_id": "window_id",
        "window_name": "window_name",
        "path": "pane_current_path",
        "command": "pane_current_command",
        "server_start": "start_time",       # the age join's era bound
        "title": "pane_title",
    }, dict(zip(cs.PANE_FIELDS, fields))
    # …and pane_title stays LAST for a reason that outlives the mapping above:
    # a title may contain '|', so ANY field placed after it is swallowed by the
    # title and silently reads empty — which for `start_time` means an
    # unprovable era and a blank age column on every row.
    assert fields[-1] == "pane_title", fields


# ---------------------------------------------------------------------------
# classify_claude_sessions — the live-Claude detector
# ---------------------------------------------------------------------------
def _proc_index():
    """Mock tree:
      16060 zsh(claude pane) -> 108149 .claude-wrapped -> 200 npm(mcp)  [INCLUDE]
      16095 zsh(plain pane)                                              [EXCLUDE]
      500   zsh(dashboard's own pane) -> 999 (our pid)                   [EXCLUDE]
    """
    return {
        16060: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 100,
                "children": [108149]},
        108149: {"comm": ".claude-wrapped", "ppid": 16060, "state": "R",
                 "age_secs": 90, "children": [200]},
        200: {"comm": "npm exec mcp", "ppid": 108149, "state": "S",
              "age_secs": 80, "children": []},
        16095: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 100,
                "children": []},
        500: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 5,
              "children": [999]},
        999: {"comm": "python3", "ppid": 500, "state": "R", "age_secs": 5,
              "children": []},
    }


def _panes():
    return [
        {"pane_id": "%0", "pane_pid": 16060, "session": "main", "window_index": "1",
         "window_name": "devrc", "path": "/home/zach/workspace/devrc", "command": "claude"},
        {"pane_id": "%1", "pane_pid": 16095, "session": "main", "window_index": "2",
         "window_name": "dp", "path": "/home/zach/ws/dp", "command": "zsh"},
        {"pane_id": "%9", "pane_pid": 500, "session": "main", "window_index": "9",
         "window_name": "self", "path": "/home/zach/workspace/devrc", "command": "python3"},
    ]


def test_classify_includes_claude_excludes_plain_and_own():
    sessions = cs.classify_claude_sessions(
        _panes(), _proc_index(), own_pids={999},
        root_resolver=lambda p: p)  # treat path as its own root
    # the plain zsh pane and the dashboard's own pane are both excluded
    ids = [s["pane_id"] for s in sessions]
    assert ids == ["%0"]
    s = sessions[0]
    assert s["repo"] == "devrc"
    assert s["session"] == "main" and s["window_index"] == "1"
    assert s["busy"] is True          # .claude-wrapped state == R
    # The /proc number is still carried — under its TRUE name, process uptime.
    assert s["proc_age_secs"] == 90
    # …and it is NOT the age column: with no activity index there is no proven
    # last-activity, so the row has none. (Rendering it as an idle time was the bug.)
    assert s["age_secs"] is None


def test_classify_detects_via_foreground_command_when_tree_missing():
    # No proc_index entry at all → falls back to pane_current_command == 'claude'.
    panes = [{"pane_id": "%0", "pane_pid": 7, "session": "s", "window_index": "1",
              "window_name": "w", "path": "/repo", "command": "claude"}]
    sessions = cs.classify_claude_sessions(panes, {}, root_resolver=lambda p: p)
    assert len(sessions) == 1
    assert sessions[0]["busy"] is None      # no proc info → unknown, not a crash


def test_classify_task_and_busy_from_pane_title():
    # busy is derived from the pane_title's leading glyph, task from the rest.
    panes = [
        {"pane_id": "%i", "pane_pid": 7, "session": "sa", "window_index": "1",
         "window_name": "devrc", "path": "/r1", "command": "claude",
         "title": "✳ Investigate remaining 500 errors"},        # sparkle → idle
        {"pane_id": "%b", "pane_pid": 8, "session": "sb", "window_index": "1",
         "window_name": "dp", "path": "/r2", "command": "claude",
         "title": "⠐ Trace and validate external app listing"},  # braille → busy
        {"pane_id": "%e", "pane_pid": 9, "session": "sc", "window_index": "1",
         "window_name": "dp", "path": "/r3", "command": "claude",
         "title": ""},                                            # empty → fallback
    ]
    sessions = cs.classify_claude_sessions(panes, {}, root_resolver=lambda p: p)
    by_pane = {s["pane_id"]: s for s in sessions}
    assert by_pane["%i"]["task"] == "Investigate remaining 500 errors"
    assert by_pane["%i"]["busy"] is False        # ✳ sparkle = idle/awaiting
    assert by_pane["%b"]["task"] == "Trace and validate external app listing"
    assert by_pane["%b"]["busy"] is True         # braille spinner = running
    assert by_pane["%e"]["task"] == ""           # empty title → caller falls back
    assert by_pane["%e"]["busy"] is None         # no glyph, no proc info → unknown


def test_own_pane_is_excluded_EVEN_WHEN_it_is_itself_a_claude_session():
    """🔴 FOUND BY MUTATION, and it was a hole in BOTH suites. Every self-
    exclusion fixture inherited from agent-ops put a plain `python3` in the
    reader's own tree — a pane that would not have been counted anyway. So
    `any(p in own_pids ...)` → `all(...)` changed no assertion in either file
    and the mutant SURVIVED: the exclusion was never once observed to exclude
    anything.

    That is not academic. The consumer this detector was extracted for is a bar
    block, and a Claude session is exactly what sits in the pane a reader may be
    invoked from — counting yourself is the failure, and it inflates by one
    forever. Build the case the old fixture could not: the reader's own tree
    contains a real `.claude-wrapped`, and the row must still not appear.
    """
    proc = {
        700: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 50,
              "children": [701]},
        701: {"comm": ".claude-wrapped", "ppid": 700, "state": "R",
              "age_secs": 40, "children": [702]},
        702: {"comm": "python3", "ppid": 701, "state": "R", "age_secs": 5,
              "children": []},          # ← the reader itself, under a claude
        800: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 50,
              "children": [801]},
        801: {"comm": ".claude-wrapped", "ppid": 800, "state": "S",
              "age_secs": 40, "children": []},
    }
    panes = [
        {"pane_id": "%own", "pane_pid": 700, "session": "s", "window_index": "1",
         "window_name": "own", "path": "/r", "command": "zsh"},
        {"pane_id": "%other", "pane_pid": 800, "session": "s",
         "window_index": "2", "window_name": "other", "path": "/r",
         "command": "zsh"},
    ]
    got = cs.classify_claude_sessions(panes, proc, own_pids={702},
                                      root_resolver=lambda p: p)
    assert [s["pane_id"] for s in got] == ["%other"], got
    # POSITIVE CONTROL: without the exclusion this fixture yields BOTH, so the
    # assertion above is about own_pids and not about %own being unmatchable.
    both = cs.classify_claude_sessions(panes, proc, own_pids=frozenset(),
                                       root_resolver=lambda p: p)
    assert sorted(s["pane_id"] for s in both) == ["%other", "%own"], both


def test_own_pid_chain_walks_ALL_THE_WAY_UP_to_the_pane():
    """🔴 ALSO FOUND BY MUTATION. Every test injected `own_chain=lambda: {999}`,
    so the real walk was never run: replacing `pid = info.get("ppid", 0)` with
    `pid = 0` — which reduces the chain to this process alone — survived the
    whole suite.

    The walk is what makes the exclusion above reachable in production: the
    reader's own pid is several levels below the PANE's pid, and
    `classify_claude_sessions` matches own_pids against the pane's process TREE.
    A chain that stops at self never intersects that tree for any real pane, so
    the block would silently count itself.
    """
    tree = {
        10: {"comm": "zsh", "ppid": 1, "state": "S", "children": [11]},
        11: {"comm": "sh", "ppid": 10, "state": "S", "children": [12]},
        12: {"comm": "python3", "ppid": 11, "state": "R", "children": []},
    }
    me = os.getpid()

    def reader(pid):
        if pid == me:
            return {"comm": "python3", "ppid": 12, "state": "R"}
        return tree.get(pid)

    chain = cs.own_pid_chain(reader=reader)
    assert me in chain
    for ancestor in (12, 11, 10):
        assert ancestor in chain, "the walk stopped before pid %d: %s" % (
            ancestor, sorted(chain))
    # and it TERMINATES rather than looping forever on a cycle
    cyc = {1: {"comm": "a", "ppid": 2}, 2: {"comm": "b", "ppid": 1}}
    assert cs.own_pid_chain(reader=lambda p: cyc.get(p, {"comm": "x", "ppid": 1}))


def test_strip_status_glyph_and_busy_from_title():
    assert cs.strip_status_glyph("✳ Foo bar") == "Foo bar"
    assert cs.strip_status_glyph("⠐ Foo bar") == "Foo bar"
    assert cs.strip_status_glyph("nixos") == "nixos"      # no glyph → unchanged
    assert cs.strip_status_glyph("") == ""
    assert cs.strip_status_glyph(None) == ""
    assert cs.busy_from_title("⠂ working") is True        # braille spinner
    assert cs.busy_from_title("✳ idle") is False          # sparkle
    assert cs.busy_from_title("plain title") is None      # no glyph
    assert cs.busy_from_title("") is None


def test_classify_empty_and_ordering():
    assert cs.classify_claude_sessions([], {}) == []
    # ordering: sort by (repo, session, window_index)
    panes = [
        {"pane_id": "%b", "pane_pid": 2, "session": "z", "window_index": "5",
         "window_name": "", "path": "/b", "command": "claude"},
        {"pane_id": "%a", "pane_pid": 1, "session": "a", "window_index": "1",
         "window_name": "", "path": "/a", "command": "claude"},
    ]
    sessions = cs.classify_claude_sessions(panes, {}, root_resolver=lambda p: p)
    assert [s["repo"] for s in sessions] == ["a", "b"]


# ---------------------------------------------------------------------------
# PER-WINDOW AGE — index_window_activity / window_activity_age / classify
#
# 🔴 REGRESSION. The age column used to render the Claude PROCESS's uptime
# (/proc starttime). Measured on the workbench 2026-08-11, session `scratch12`:
#
#   window  window_id  claude proc uptime   real last_activity   rendered
#   ------  ---------  ------------------   -----------------    --------
#   :1 ▶    @4         4.97d                ~45m ago             4d  ❌
#   :2 ●    @16        4.87d                ~4.79d ago           4d  ✅
#
# Two windows of one session launched in the same burst have near-identical
# process uptimes, so BOTH rows printed one age — and the busy row read as "▶ …
# 4d", simultaneously running and four days abandoned. A dogfooding agent read
# that and told the operator to treat a live window as dead. The fixture below
# reproduces those exact numbers: the two proc uptimes are DISTINCT yet both
# round to "4d", so an implementation that reuses them cannot pass by accident.
# ---------------------------------------------------------------------------
# 🔴 The task strings below are SYNTHETIC and must stay that way. This repo is
# PUBLIC (CLAUDE.md), and the first draft of this fixture was pasted from the
# operator's real panes — it carried three client project codenames into a public
# tree, which neither `test_no_client_hostnames.py` nor `test_no_public_ips.py`
# can see (they match hostnames and IPs; a codename has no structural form).
# The tests need these values DISTINGUISHABLE and pairwise distinct, not real.
#
# `server_start` is tmux's `#{start_time}`, the age join's era bound. It is set
# LONG before every `last_activity` in this module so these cases exercise what
# they were written for rather than tripping the bound; the bound itself is
# driven by test_window_activity_age_rejects_a_previous_servers_record.
_SERVER_START = "1700000000"        # 2023-11-14 — predates every fixture stamp
_SCRATCH12_PANES = [
    {"pane_id": "%4", "pane_pid": 4083112, "session": "scratch12",
     "window_index": "1", "window_id": "@4", "window_name": "svc-a",
     "path": "/home/zach/ws/svc-a", "command": "zsh",
     "server_start": _SERVER_START,
     "title": "⠐ Continue widget-rollup and cache-warm work"},   # braille → busy
    {"pane_id": "%16", "pane_pid": 653899, "session": "scratch12",
     "window_index": "2", "window_id": "@16", "window_name": "svc-b",
     "path": "/home/zach/ws/svc-b", "command": "zsh",
     "server_start": _SERVER_START,
     "title": "✳ Run index-probe on sample-corpus"},             # sparkle → idle
]

_SCRATCH12_PROC = {
    4083112: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 429500,
              "children": [3173016]},
    3173016: {"comm": ".claude-wrapped", "ppid": 4083112, "state": "S",
              "age_secs": 429408, "children": []},               # 4.97d → "4d"
    653899: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 420900,
             "children": [2888458]},
    2888458: {"comm": ".claude-wrapped", "ppid": 653899, "state": "S",
              "age_secs": 420768, "children": []},               # 4.87d → "4d"
}

_BUSY_AGE, _IDLE_AGE = 2730.0, 413912.0        # 45m ago vs 4.79d ago


def _scratch12_activity(now):
    """The two fuzzyclaw task files backing scratch12:1 and :2, as raw bodies."""
    def iso(delta):
        return (datetime.datetime.fromtimestamp(now - delta)
                .astimezone().isoformat())
    return [
        json.dumps({"window_id": "@4", "tmux_session": "scratch12",
                    "window_index": 1, "last_activity": iso(_BUSY_AGE),
                    "task": "dp", "status": "paused"}),
        json.dumps({"window_id": "@16", "tmux_session": "scratch12",
                    "window_index": 2, "last_activity": iso(_IDLE_AGE),
                    "task": "probe", "status": "paused"}),
    ]


def test_classify_ages_are_per_window_not_process_uptime():
    now = 1_786_499_288.0
    index = cs.index_window_activity(_scratch12_activity(now))
    rows = cs.classify_claude_sessions(
        _SCRATCH12_PANES, _SCRATCH12_PROC, root_resolver=lambda p: p,
        activity_index=index, now=now)
    by_win = {r["window_id"]: r for r in rows}
    assert set(by_win) == {"@4", "@16"}
    # Each window carries ITS OWN last-activity, to the second.
    assert round(by_win["@4"]["age_secs"]) == 2730
    assert round(by_win["@16"]["age_secs"]) == 413912
    assert by_win["@4"]["age_secs"] != by_win["@16"]["age_secs"]
    # ⚠ MOVED: these two used to read `rel_age(...) == "45m"` / `"4d"` — the
    # ages as the TUI printed them. `rel_age` was a formatter inside agent-ops
    # and went with it, so the same claim is made on the quantities: one lands
    # in minutes, the other in days, which is what "visibly different" meant.
    assert 40 * 60 < by_win["@4"]["age_secs"] < 50 * 60
    assert 4 * 86400 < by_win["@16"]["age_secs"] < 5 * 86400
    # The busy row is no longer self-contradictory: running AND minutes fresh.
    assert by_win["@4"]["busy"] is True
    # The process uptimes are still there, under their true name — DISTINCT
    # values that both collapse to "4d", which is what made the bug look
    # per-session rather than per-quantity.
    assert by_win["@4"]["proc_age_secs"] == 429408
    assert by_win["@16"]["proc_age_secs"] == 420768
    # ⚠ MOVED: was `rel_age(429408) == rel_age(420768) == "4d"`. The collapse is
    # the point and it is a property of the numbers, not of the formatter: two
    # distinct uptimes that both sit inside the same whole-day bucket.
    assert by_win["@4"]["proc_age_secs"] != by_win["@16"]["proc_age_secs"]
    assert int(429408 // 86400) == int(420768 // 86400) == 4


def test_index_window_activity_keys_by_window_id_and_tolerates_junk():
    idx = cs.index_window_activity([
        json.dumps({"window_id": "@4", "tmux_session": "s", "window_index": 1,
                    "last_activity": "2026-08-11T19:08:55-05:00"}),
        "{not json",                       # unparseable → skipped
        json.dumps([1, 2, 3]),             # not a dict → skipped
        json.dumps({"tmux_session": "s"}),  # no window_id → skipped
        json.dumps({"window_id": "", "tmux_session": "s"}),   # blank → skipped
    ])
    assert set(idx) == {"@4"}
    assert idx["@4"]["session"] == "s" and idx["@4"]["window_index"] == 1
    assert cs.index_window_activity([]) == {}
    assert cs.index_window_activity(None) == {}


def test_index_window_activity_ambiguous_claim_fails_closed():
    same = {"window_id": "@4", "tmux_session": "s", "window_index": 1,
            "last_activity": "2026-08-11T19:08:55-05:00"}
    other = dict(same, last_activity="2026-08-01T00:00:00-05:00")
    # A byte-identical duplicate carries the same answer — not a conflict.
    assert cs.index_window_activity([json.dumps(same),
                                     json.dumps(same)])["@4"] == {
        "session": "s", "window_index": 1,
        "last_activity": "2026-08-11T19:08:55-05:00"}
    # Two files DISAGREEING about one window id → ambiguous → no age at all.
    idx = cs.index_window_activity([json.dumps(same), json.dumps(other)])
    assert idx["@4"] is None
    pane = {"window_id": "@4", "session": "s", "window_index": "1"}
    assert cs.window_activity_age(pane, idx, now=1_786_499_288.0) is None


def test_window_activity_age_requires_session_and_index_to_agree():
    now = 1_786_499_288.0
    ts = datetime.datetime.fromtimestamp(now - 600).astimezone().isoformat()
    idx = cs.index_window_activity([json.dumps(
        {"window_id": "@4", "tmux_session": "scratch12", "window_index": 1,
         "last_activity": ts})])
    ok = {"window_id": "@4", "session": "scratch12", "window_index": "1",
          "server_start": _SERVER_START}
    assert round(cs.window_activity_age(ok, idx, now=now)) == 600

    # Each guard is reached on its OWN: the session check with a MATCHING index,
    # the index check with a MATCHING session — neither can shadow the other.
    wrong_session = dict(ok, session="scratch9")      # index still 1
    assert cs.window_activity_age(wrong_session, idx, now=now) is None
    wrong_index = dict(ok, window_index="2")          # session still scratch12
    assert cs.window_activity_age(wrong_index, idx, now=now) is None
    # An id nobody claims, and an empty/absent index.
    assert cs.window_activity_age(dict(ok, window_id="@99"), idx, now=now) is None
    assert cs.window_activity_age(ok, {}, now=now) is None
    assert cs.window_activity_age(ok, None, now=now) is None


def _one_record_index(now, offset, session="s", window_index=1, window_id="@4"):
    """A one-file activity index whose `last_activity` is `now - offset`."""
    ts = datetime.datetime.fromtimestamp(now - offset).astimezone().isoformat()
    return cs.index_window_activity([json.dumps(
        {"window_id": window_id, "tmux_session": session,
         "window_index": window_index, "last_activity": ts})])


def test_window_activity_age_unparseable_and_future_timestamps():
    now = 1_786_499_288.0
    pane = {"window_id": "@4", "session": "s", "window_index": "1",
            "server_start": _SERVER_START}
    for bad in ("", "yesterday", None, 17864, "2026-13-45T99:99:99"):
        idx = cs.index_window_activity([json.dumps(
            {"window_id": "@4", "tmux_session": "s", "window_index": 1,
             "last_activity": bad})])
        assert cs.window_activity_age(pane, idx, now=now) is None

    # A SMALL forward skew (one NTP step, well inside FUTURE_SKEW_TOLERANCE)
    # still clamps to 0 rather than going negative — the benign case.
    assert cs.window_activity_age(
        pane, _one_record_index(now, -30), now=now) == 0.0
    # …and the boundary itself is inclusive-ish: exactly at the tolerance, clamp.
    assert cs.window_activity_age(
        pane, _one_record_index(now, -cs.FUTURE_SKEW_TOLERANCE), now=now) == 0.0


def test_window_activity_age_rejects_a_timestamp_far_in_the_future():
    """🔴 A future stamp beyond tolerance is REJECTED, not repaired.

    Clamping an untrusted value to `now` renders `rel_age(0.0)` == "0s" — the
    strongest "this window is alive right now" the column can print — from a
    corrupt file or a badly wrong clock. Every other unproven case here fails
    closed to "—"; this one used to fail OPEN, into the most misleading output.

    A quarter-hour is not NTP drift on a host writing both stamps; it is a
    broken clock. The benign-skew case above pins that small skews still clamp,
    so this rejection cannot be satisfied by simply refusing all future stamps.
    """
    now = 1_786_499_288.0
    pane = {"window_id": "@4", "session": "s", "window_index": "1",
            "server_start": _SERVER_START}
    # Stated as a LITERAL quarter-hour first, so this fails at the base commit on
    # the BEHAVIOUR (it returned 0.0) rather than on a missing constant.
    assert cs.window_activity_age(
        pane, _one_record_index(now, -900), now=now) is None
    # ⚠ MOVED: this used to continue into agent-ops' renderer and assert that
    # "0s" never reached the screen. Both `rel_age` and `render_active_runs`
    # were deleted with the TUI. The distinction the render check protected —
    # rejected (None) vs repaired-to-now (0.0) — is asserted directly instead,
    # and it is the stronger of the two: a renderer can only print "0s" if it
    # is handed a 0.0, so pinning that the value is None and NOT 0.0 forecloses
    # the output for every consumer, not just the one that has now gone.
    got = cs.window_activity_age(pane, _one_record_index(now, -900), now=now)
    assert got is None
    assert got != 0.0 and got is not False    # the clamp this guard prevents
    # The boundary is where the constant says it is, not where a literal says.
    just_over = cs.FUTURE_SKEW_TOLERANCE + 1
    assert cs.window_activity_age(
        pane, _one_record_index(now, -just_over), now=now) is None


def test_window_activity_age_rejects_a_previous_servers_record():
    """🔴 THE id-reuse case the (session, window_index) confirmation passed by
    COINCIDENCE — red before the era bound, and it is not a hypothetical.

    Measured on this host 2026-08-11: 8 of 40 live window ids carried fuzzyclaw
    task files written BEFORE the current tmux server started (server up 6.44d,
    files back to 2026-07-07). The confirmation caught all 8 that day, but three
    differed ONLY in session name and one ONLY in window index — so a restart
    that recreates the same layout reproduces a matching pair and the record
    sails through. This fixture IS that pair: same id, same session, same index,
    written 60 days before a server that started 3 days ago.

    Without the bound the row renders `60d` on a window this server created 3
    days ago — a live window reported as two months abandoned, which is exactly
    the class of wrong answer this whole join was rewritten to stop making.
    """
    now = 1_786_499_288.0
    server_start = now - 3 * 86400              # this tmux server booted 3d ago
    pane = {"window_id": "@0", "session": "scratch2", "window_index": "1",
            "server_start": str(int(server_start))}
    idx = _one_record_index(now, 60 * 86400, session="scratch2",
                            window_index=1, window_id="@0")
    # Every confirmation AGREES — id, session and index all match.
    rec = idx["@0"]
    assert rec["session"] == pane["session"]
    assert str(rec["window_index"]) == pane["window_index"]
    # …and the record is still rejected, on the era bound alone.
    assert cs.window_activity_age(pane, idx, now=now) is None
    # ⚠ MOVED: was `rel_age(60 * 86400) == "60d"`, naming the age the operator
    # would have been shown pre-change. The formatter went with the TUI, and a
    # replacement assertion on the literal would be a tautology, so it is
    # DROPPED rather than restated — the rejection asserted on the line above
    # is the claim; that line was only its rendering.

    # POSITIVE CONTROL: the SAME keys with a record written after the server
    # started resolve normally, so the rejection above is about the ERA and not
    # about this fixture being unjoinable for some other reason.
    fresh = _one_record_index(now, 3600, session="scratch2",
                              window_index=1, window_id="@0")
    assert round(cs.window_activity_age(pane, fresh, now=now)) == 3600
    # The boundary: a record written exactly AT server start is in-era.
    at_start = _one_record_index(now, 3 * 86400, session="scratch2",
                                 window_index=1, window_id="@0")
    assert cs.window_activity_age(pane, at_start, now=now) is not None


def test_window_activity_age_fails_closed_when_the_server_era_is_unprovable():
    """No usable `#{start_time}` → the era cannot be proven → no age.

    A tmux that does not know `start_time` renders the field empty (or leaves it
    literal), and a pane dict built by an older caller has no key at all. Every
    such shape must degrade to "—" rather than to an UNBOUNDED join, which is
    the pre-change behaviour this bound replaces.
    """
    now = 1_786_499_288.0
    idx = _one_record_index(now, 600)
    base = {"window_id": "@4", "session": "s", "window_index": "1"}
    # Proven era first, so the rest is a fact about server_start and nothing else.
    assert round(cs.window_activity_age(
        dict(base, server_start=_SERVER_START), idx, now=now)) == 600
    for missing in (None, "", "   ", "#{start_time}", "not-a-number", "0", "-1"):
        pane = dict(base) if missing is None else dict(base, server_start=missing)
        assert cs.window_activity_age(pane, idx, now=now) is None, missing


def test_classify_passes_the_server_era_through_to_every_row():
    """🔴 SEAM. The bound lives in `window_activity_age`, but the value reaches it
    only if `classify_claude_sessions` hands over the pane dict carrying
    `server_start` — a wiring that no unit test of either half can see.

    Drive the real classifier with a real previous-era record and require the
    row's age to blank. Read through the full path (parse_panes → classify) so
    the field survives the FORMAT/PARSER seam too.
    """
    now = 1_786_499_288.0
    started = str(int(now - 3 * 86400))
    panes = [dict(p, server_start=started) for p in _SCRATCH12_PANES]
    raw = "\n".join("|".join(str(p[k]) for k in cs.PANE_FIELDS) for p in panes)
    parsed = cs.parse_panes(raw)
    assert [p["server_start"] for p in parsed] == [started, started], parsed

    stale = _one_record_index(now, 60 * 86400, session="scratch12",
                              window_index=1, window_id="@4")
    rows = cs.classify_claude_sessions(
        parsed, _SCRATCH12_PROC, root_resolver=lambda p: p,
        activity_index=stale, now=now)
    by_win = {r["window_id"]: r for r in rows}
    assert by_win["@4"]["age_secs"] is None, "the era bound never reached the row"
    # ⚠ MOVED: was followed by a render check that "60d" never reached the
    # screen. Asserted on every row's value instead, which is what any renderer
    # would have had to read — and covers the second row the render check did
    # not name.
    assert all(r["age_secs"] is None for r in rows), rows


def test_iso_epoch_reads_a_naive_timestamp_as_LOCAL_time():
    """`_iso_epoch`'s documented contract, pinned rather than described.

    MEASURED COVERAGE GAP: switching this to UTC survived the whole suite,
    because every other fixture writes an offset-carrying stamp. On a non-UTC
    host the mutant shifts EVERY naive-stamped age by the UTC offset silently.

    TZ is forced rather than inherited: the CI sandbox runs UTC, where the two
    readings coincide and the assertion would pass vacuously — a config-blind
    suite pinning the very dimension the bug lives on.

    It is forced with a POSIX TZ STRING ("XXX5" = 5h west, no DST) rather than a
    zone NAME: the nix check sandbox ships no tzdata, so `America/Chicago`
    silently resolves to UTC there and the test goes vacuous in exactly the
    environment that gates the merge. glibc parses the POSIX form with no files
    at all. The `local != utc` assertion below is that guard, kept in place — it
    is what caught the zone-name version on the first authoritative run.
    """
    old_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "XXX5"
        time.tzset()
        naive = "2026-08-11T19:08:55"
        local = datetime.datetime(2026, 8, 11, 19, 8, 55).timestamp()
        utc = datetime.datetime(2026, 8, 11, 19, 8, 55,
                                tzinfo=datetime.timezone.utc).timestamp()
        assert local != utc, "TZ did not take — this test would be vacuous"
        assert cs._iso_epoch(naive) == local
        # The offset-carrying form fuzzyclaw actually writes is unaffected.
        assert cs._iso_epoch("2026-08-11T19:08:55+00:00") == utc
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def test_window_activity_age_compares_session_and_index_ACROSS_types():
    """fuzzyclaw writes JSON; tmux hands us strings — so both sides of both
    confirmations can differ in TYPE while agreeing in value.

    MEASURED COVERAGE GAP: removing the `str()` around `session` survived the
    whole suite, because every fixture was string-on-both-sides. A numeric
    session name (`tmux new -s 12`) is what makes it int-in-file, and the
    failure is silent — that session simply never shows an age.
    """
    now = 1_786_499_288.0
    ts = datetime.datetime.fromtimestamp(now - 600).astimezone().isoformat()
    idx = cs.index_window_activity([json.dumps(
        {"window_id": "@4", "tmux_session": 12, "window_index": 1,
         "last_activity": ts})])
    assert idx["@4"]["session"] == 12          # int in the file …
    pane = {"window_id": "@4", "session": "12", "window_index": "1",
            "server_start": _SERVER_START}     # … str from tmux
    assert round(cs.window_activity_age(pane, idx, now=now)) == 600
    # The complement: a genuine disagreement is still caught across types.
    assert cs.window_activity_age(
        dict(pane, session="13"), idx, now=now) is None
    assert cs.window_activity_age(
        dict(pane, window_index="2"), idx, now=now) is None


# ---------------------------------------------------------------------------
# SEAMS — the parts no unit test above can see
#
# This module exists because the detector used to live inside a TUI that the bar
# pill imported. Extraction only pays off if the tree keeps ONE copy and if the
# copy the operator's bar loads is actually deployed. Neither is a property of
# any function here, so both are pinned structurally, in the shape of
# test_clawgate_predicate_single_source.py.
# ---------------------------------------------------------------------------
_REPO = pathlib.Path(__file__).resolve().parents[2]
_LIB_REL = "scripts/lib/claude_sessions.py"

#: 🔴 The importer ledger. Exactly these files load the shared detector.
#: Two-way on purpose: GROWING means a new surface counts Claude sessions and
#: its behaviour was never reviewed here; SHRINKING means a consumer went back
#: to its own copy, which is the state this module was created to end.
_EXPECTED_IMPORTERS = {
    "scripts/i3status-claude-runs",   # the live bar count pill
}

#: NON-DOC files allowed to mention the module by name WITHOUT importing it,
#: each with its reason. Enumerated, not patterned — an unknown mention in a
#: code file is a finding, because "a file talks about the detector" is how a
#: second one starts.
#:
#: ⚠ SCOPED TO CODE ON PURPOSE. Markdown is excluded from the mention scan
#: entirely (see `_is_doc`). A second detector cannot be born in prose, and the
#: first draft of this guard — which scanned everything — went red the moment
#: five docs were correctly updated to NAME the new module. A gate that fires
#: when someone documents the thing it protects is a gate that gets
#: rubber-stamped, and RULES.md is explicit that a permanently-red gate is worse
#: than no gate.
_MENTION_ONLY = {
    "scripts/lib/claude_sessions.py":            "the module itself",
    "scripts/tests/test_claude_sessions.py":     "this suite",
    "scripts/tests/test_claude_runs_block.py":   "the consumer's suite",
    "nix/graphical.nix":                         "the home.file deploy entry",
    # 🔴 Prose only, and load-bearing prose: session-manager's rendered caveat
    # names this module as the deeper detector its own
    # `pane_current_command =~ /claude/` is shallower than. It must NOT become
    # an importer without review — the two are deliberately different rules,
    # because the /proc walk is not reachable over SSH and session-manager has
    # to report both hosts by ONE rule.
    "scripts/session-manager":                   "names it in a caveat, on purpose",
    # Imports THIS SUITE (not the detector) to read `_SKIP_DIRS` and pin it
    # against the three sibling walkers. It names this module because that is
    # the import; it never loads `scripts/lib/`.
    "scripts/tests/test_skip_dirs_ledger.py":    "pins this suite's skip set",
}

_DOC_SUFFIXES = {".md", ".txt", ".org"}


def _is_doc(rel) -> bool:
    return os.path.splitext(rel)[1].lower() in _DOC_SUFFIXES

#: 🔴 SHARED BASE + THIS SITE'S OWN ADDITIONS, spelled here so the effective set
#: is readable where it is used. The base is `testlib/skip_dirs.GENERATED` —
#: machine-generated directories no walker should read. Before the four skip
#: sets were consolidated this one was missing `.pytest_cache`, so an
#: ordinary `pytest` run wrote `.pytest_cache/v/cache/nodeids` — a JSON list of
#: every collected node id, naming `claude_sessions` 29 times — into the tree
#: this ledger walks, and the ledger went red on an artefact nobody wrote. The
#: operator's checkout was in exactly that state when this was fixed.
#:
#: The two additions are NOT in the base, because the base is also what the two
#: PUBLIC-repo security gates use and neither may inherit them:
#:   .claude      per-host Claude Code state; gitignored, ~41k files locally,
#:                and it is where agent worktrees (full repo copies) live.
#:   claudedocs   committed handoff prose. Excluded HERE only because the
#:                mention scan is scoped to code — `_is_doc` already drops
#:                `.md`, and this makes the walk skip the directory outright.
#:                🔴 `public_ip_scan` / `client_host_scan` MUST keep reading it.
#: `VIRTUALENVS` is granted because this walker has no `git ls-files` tier: it
#: reads whatever is on disk, and `.venv/` is 476 files of vendored pip source
#: in the operator's checkout today.
_SKIP_DIRS = set(skip_dirs.GENERATED | skip_dirs.VIRTUALENVS) | {
    ".claude", "claudedocs"}


def _repo_files():
    """Every file in the tree, as repo-relative paths. NO git.

    🔴 TWO TIERS. This started as `git ls-files` and was green on the dev host
    and RED in the nix build sandbox, where the source is an unpacked copy with
    no `.git` — the exact structural blindness RULES.md names. The sandbox is
    the authoritative gate, so the scan must not need a repository.

    🔴 The skip list is applied to the RELATIVE path, never the absolute one.
    This checkout can itself sit under a skipped directory name (agent worktrees
    live under `.claude/worktrees/`), and matching on absolute parts makes the
    scan walk ZERO files while reporting a perfect green. That is not
    hypothetical — it is the failure `test_clawgate_predicate_single_source`
    records having hit, and `_scan_is_not_walking_nothing` below is the positive
    control for it here.
    """
    for p in sorted(_REPO.rglob("*")):
        rel = p.relative_to(_REPO)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if p.is_file():
            yield rel.as_posix()


def _scan_is_not_walking_nothing(seen):
    """POSITIVE CONTROL for every scan below: a zero is meaningless unless the
    walk demonstrably reaches the files it is supposed to judge."""
    assert len(seen) > 200, "the walk found only %d files — it is scanning the "\
        "wrong root, not finding a clean tree" % len(seen)
    for must in (_LIB_REL, "scripts/i3status-claude-runs", "nix/graphical.nix"):
        assert must in seen, "the walk never reached %s" % must


def _loads_the_detector(text) -> bool:
    """True iff this source actually LOADS the detector, not merely names it.

    🔴 STRUCTURAL, and it had to become so. The first version asked for the two
    substrings "claude_sessions.py" and "SourceFileLoader" anywhere in the file
    — and `scripts/session-manager` satisfied both by coincidence: it names this
    module in a rendered caveat, and it loads a DIFFERENT module
    (`lib/clawgate_tasks.py`) with a loader. A ledger that reports a
    non-importer as an importer is worse than no ledger, because the fix is to
    add the wrong file to the expected set and the real growth then hides
    inside it. So find a `SourceFileLoader(...)` whose MODULE NAME argument
    names this detector — the argument the caller controls and cannot spell by
    accident.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(
            fn, "id", "")
        if name != "SourceFileLoader":
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) \
                and "claude_sessions" in first.value:
            return True
    return False


def test_exactly_these_files_load_the_shared_detector():
    found = set()
    mentions = set()
    seen = set()
    for rel in _repo_files():
        seen.add(rel)
        try:
            text = (_REPO / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "claude_sessions" not in text:
            continue
        if not _is_doc(rel):
            mentions.add(rel)
        if _loads_the_detector(text):
            found.add(rel)
    _scan_is_not_walking_nothing(seen)
    # POSITIVE CONTROL for the detector-of-detectors: the known importer must be
    # found by it, or a green here means the predicate matches nothing at all.
    assert found, "no file was detected as an importer — _loads_the_detector " \
                  "matches nothing, so the ledger below is vacuous"
    found -= set(_MENTION_ONLY)
    assert found == _EXPECTED_IMPORTERS, (
        "the detector's importer set changed.\n"
        "  expected: %s\n  found:    %s\n"
        "GROWING means a new surface detects Claude sessions and was never "
        "reviewed here; SHRINKING means a consumer forked its own copy."
        % (sorted(_EXPECTED_IMPORTERS), sorted(found)))
    unknown = mentions - set(_MENTION_ONLY) - _EXPECTED_IMPORTERS
    assert not unknown, (
        "these files name claude_sessions without importing it: %s — add them "
        "to _MENTION_ONLY with a reason, or make them real importers"
        % sorted(unknown))
    # two-way: a pardon that no longer describes anything must not outlive it
    stale = set(_MENTION_ONLY) - mentions
    assert not stale, (
        "_MENTION_ONLY pardons files that no longer name claude_sessions: %s — "
        "remove them, a rubber stamp must not outlive what it stamped"
        % sorted(stale))


def test_no_importer_re_implements_the_detector():
    """🔴 A shared module does not help if a consumer keeps a private copy —
    that is exactly how the clawgate predicate went wrong at two sites in the
    same direction. Every symbol the module owns must be absent as a DEFINITION
    in every importer."""
    owned = {n.name for n in ast.walk(ast.parse((_REPO / _LIB_REL).read_text()))
             if isinstance(n, ast.FunctionDef)}
    assert "classify_claude_sessions" in owned, owned
    for rel in sorted(_EXPECTED_IMPORTERS):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)}
        clash = owned & defined
        assert not clash, "%s re-implements %s" % (rel, sorted(clash))


def test_the_shared_module_is_DEPLOYED_beside_the_block_that_loads_it():
    """🔴 THE SEAM A UNIT TEST CANNOT SEE, and the one that would have shipped a
    silently broken bar. At runtime the pill is a lone nix-store symlink in
    ~/.config/i3status-rust/scripts; it loads this module out of its OWN
    directory, which is populated entirely by `home.file` entries in
    nix/graphical.nix. Every test above loads the module from the repo, where it
    is always present, so nothing else here can notice a missing symlink — and a
    flake ALSO silently omits an untracked file, so the module has to be
    `git add`ed as well.

    Both halves are asserted: the home.file entry exists, and the file is
    actually visible to the flake.

    ⚠ THE FIRST HALF IS SPELLED, and three real breakages walked past it — see
    `test_the_bar_block_and_EVERY_file_it_needs_deploy_on_the_SAME_hosts`
    below, which owns the structural half now. What stays load-bearing HERE is
    the git-visibility half: whether the flake can see the file at all is not a
    question the nix source can answer.

    ⚠ HOW THE SECOND HALF IS MEASURED DIFFERS BY TIER, deliberately. In the nix
    build sandbox the source tree IS what the flake could see — an untracked
    file is simply not there — so `exists()` is a direct measurement of the
    claim, and it is the only one available (there is no `.git`). On a dev host
    the tree contains untracked files too, so `exists()` alone would be weaker
    than the claim; `git ls-files` is consulted there and the file must be in
    it. Neither tier is vacuous, and the sandbox tier is the authoritative one.
    """
    nix = (_REPO / "nix" / "graphical.nix").read_text(encoding="utf-8")
    assert '.config/i3status-rust/scripts/claude_sessions.py"' in nix, \
        "claude_sessions.py has no home.file entry — the count pill would be `?`"
    assert "../scripts/lib/claude_sessions.py" in nix
    # the consumer must be deployed too, or the module has nothing to serve
    assert '.config/i3status-rust/scripts/i3status-claude-runs"' in nix

    needed = (_LIB_REL, "scripts/i3status-claude-runs")
    for rel in needed:
        assert (_REPO / rel).is_file(), \
            "%s is not in the tree the flake would copy" % rel
    out = subprocess.run(["git", "-C", str(_REPO), "ls-files"] + list(needed),
                         capture_output=True, text=True, timeout=30)
    if out.returncode == 0:          # dev host; in the sandbox there is no .git
        listed = set(out.stdout.split())
        for rel in needed:
            assert rel in listed, (
                "%s is UNTRACKED — `git add` it or the flake ships a switch "
                "that silently lacks it" % rel)


def test_the_retired_TUI_has_no_launcher_anywhere():
    """🔴 REGROWTH GATE. `scripts/agent-ops` was retired: nothing launches it,
    nothing imports it, and it no longer exists. The failure this prevents is a
    launcher outliving its target — a bar button, keybinding or tmux popup that
    execs a path home-manager no longer deploys, which fails silently at click
    time and is invisible to every other test in this repo.

    Scoped to LAUNCH SURFACES rather than the whole tree, so prose that
    truthfully describes the retirement (docs, this docstring) stays legal.
    """
    assert not (_REPO / "scripts" / "agent-ops").exists(), \
        "scripts/agent-ops is back — the detector lives in %s now" % _LIB_REL
    for rel in ("nix/graphical.nix", "nix/i3/config.nix", "nix/home.nix",
                ".tmux.conf"):
        text = (_REPO / rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue          # a comment recording the retirement is fine
            assert "tmux/agent-ops" not in line, "%s: %s" % (rel, line.strip())
            assert "scripts/agent-ops" not in line, "%s: %s" % (rel, line.strip())


# --------------------------------------------------------------------------- #
# 🔴 THE DEPLOY WIRING, ASSERTED STRUCTURALLY.
#
# The guard above (`…is_DEPLOYED_beside_the_block_that_loads_it`) was SPELLED on
# nix source text: `'.config/…/claude_sessions.py"' in nix`. An adversarial
# round drove seven mutations through `nix/graphical.nix` against 710 tests and
# THREE REAL BREAKAGES SURVIVED — every one of them still containing the spelled
# substring, which is why spelling it was never a guard:
#
#   1. the pill's `home.file` gated `lib.mkIf isLaptop` -> the pill is NOT
#      deployed on the workbench, the only host whose bar carries the block;
#   2. `source = ../scripts/lib/clawgate_tasks.py;` with the correct path moved
#      into a trailing comment -> the symlink points at the wrong module;
#   3. `claudeRunsBlock` dropped from the `blocks` list -> the pill is off the
#      bar entirely, while both symlinks still deploy perfectly.
#
# So the parse below reads ASSIGNMENTS, not the file's vocabulary, and pins a
# RELATIONSHIP: everything the bar's block list references must be deployed on
# every host that carries the block, from the right source. `graphical.nix:432`
# even ASSERTS in prose that "the gate is NOT narrower than its consumer's" —
# an untested claim, in a file whose sibling `bar_freshness.py` IS
# `mkIf (!isLaptop)`, so the narrower shape is the local idiom and one edit away.
#
# ⚠ TIERS. This one runs in BOTH — pure text plus a two-point guard evaluator,
# no `nix` binary. Asking nix itself would be ground truth and was written, but
# it cannot run in the tier that gates merges; see the block at the end of this
# file for the probe, its measured result, and why it is not a test here.
# --------------------------------------------------------------------------- #
_GRAPHICAL_NIX = "nix/graphical.nix"
_HOSTS = ("workbench", "laptop")


def _nix_guard_hosts(guard):
    """Which of _HOSTS a `lib.mkIf <guard>` / `lib.optional <guard>` enables.

    Deliberately tiny and deliberately STRICT: an expression it does not
    recognise RAISES rather than defaulting to "both", because defaulting to
    both is exactly how a narrowed gate would slip past. `isLaptop` is the only
    host discriminator graphical.nix takes (nix/home.nix:79 derives it from
    /sys/class/backlight/intel_backlight and threads it in via _module.args).
    """
    g = (guard or "").strip()
    while g.startswith("(") and g.endswith(")"):
        g = g[1:-1].strip()
    if g == "":
        return set(_HOSTS)                 # ungated
    if g == "isLaptop":
        return {"laptop"}
    if g == "!isLaptop":
        return {"workbench"}
    raise AssertionError(
        "unrecognised nix guard %r — extend _nix_guard_hosts rather than "
        "letting an unknown gate read as 'enabled everywhere'" % guard)


def _parse_home_file_entries(text):
    """{attr: {"guard": str, "source": str|None}} for every `home.file."…"`.

    `source = <expr>;` is captured up to its SEMICOLON, so a trailing `#`
    comment cannot contribute — which is precisely the shape of survivor 2.
    """
    out = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r'\s*home\.file\."([^"]+)"\s*=\s*(.*)$', line)
        if not m:
            continue
        attr, rhs = m.group(1), m.group(2).strip()
        g = re.match(r'lib\.mkIf\s+(.+?)\s*\{$', rhs)
        guard = g.group(1) if g else ""
        src = None
        for j in range(i + 1, min(i + 12, len(lines))):
            if re.match(r'\s*\};\s*$', lines[j]):
                break
            sm = re.match(r'\s*source\s*=\s*([^;]+);', lines[j])
            if sm:
                src = sm.group(1).strip()
        out[attr] = {"guard": guard, "source": src}
    return out


def _parse_block_list(text):
    """{identifier: guard} for the bar's `blocks = …` expression.

    The list is a `++` chain of plain lists and `lib.optional(s) <guard> […]`,
    so each segment carries the gate its members inherit.
    """
    m = re.search(r'\n  blocks =\s*\n(.*?);\n', text, re.S)
    assert m, "could not find the `blocks =` assignment in " + _GRAPHICAL_NIX
    body = "\n".join(ln for ln in m.group(1).splitlines()
                     if not ln.strip().startswith("#"))
    out = {}
    for seg in body.split("++"):
        seg = seg.strip()
        if not seg:
            continue
        om = re.match(r'lib\.optionals?\s+(\([^)]*\)|!?\w+)\s*(.*)$', seg, re.S)
        guard, rest = (om.group(1), om.group(2)) if om else ("", seg)
        for ident in re.findall(r'\b([a-z]\w*Block)\b', rest):
            out[ident] = guard
    assert out, "parsed no blocks out of:\n" + body
    return out


def _parse_block_command(text, ident):
    """The `command = "…";` of a named block definition."""
    m = re.search(r'\n  %s = \{(.*?)\n  \};' % re.escape(ident), text, re.S)
    assert m, "no block definition named " + ident
    cm = re.search(r'command\s*=\s*"([^"]+)"', m.group(1))
    assert cm, ident + " has no command"
    return cm.group(1)


def test_the_bar_block_and_EVERY_file_it_needs_deploy_on_the_SAME_hosts():
    """🔴 The relationship, not the vocabulary. Kills all three survivors."""
    text = (_REPO / _GRAPHICAL_NIX).read_text(encoding="utf-8")
    blocks = _parse_block_list(text)
    files = _parse_home_file_entries(text)

    # (1) the block is ON THE BAR at all — survivor 3 deleted it from the list
    #     while leaving every symlink and every spelled string intact.
    assert "claudeRunsBlock" in blocks, (
        "claudeRunsBlock is not in the bar's block list — the pill is gone "
        "from the bar; found %s" % sorted(blocks))
    block_hosts = _nix_guard_hosts(blocks["claudeRunsBlock"])
    assert block_hosts, "the block is enabled on no host at all"

    # (2) what the block EXECUTES must be a deployed file, RESOLVED FROM THE
    #     COMMAND rather than restated here — a rename cannot drift past this.
    cmd = _parse_block_command(text, "claudeRunsBlock")
    assert cmd.startswith("${scriptsDir}/"), cmd
    name = cmd.split("/")[-1]
    needed = {
        ".config/i3status-rust/scripts/" + name: "../scripts/" + name,
        # the sibling module the pill imports out of its own directory
        ".config/i3status-rust/scripts/claude_sessions.py":
            "../scripts/lib/claude_sessions.py",
    }
    for attr, want_source in needed.items():
        assert attr in files, (
            "%s has no home.file entry — the block would exec / import a path "
            "home-manager does not deploy" % attr)
        hosts = _nix_guard_hosts(files[attr]["guard"])
        # (3) NOT NARROWER THAN ITS CONSUMER. `mkIf isLaptop` on either entry
        #     leaves the workbench — the only host with the block — without it.
        assert block_hosts <= hosts, (
            "%s deploys on %s but its block runs on %s: the gate is NARROWER "
            "than its consumer's" % (attr, sorted(hosts), sorted(block_hosts)))
        # (4) …and it points at the right module. Survivor 2 swapped the source
        #     and parked the correct path in a trailing comment.
        assert files[attr]["source"] == want_source, (
            "%s deploys source=%r, expected %r"
            % (attr, files[attr]["source"], want_source))
        assert (_REPO / want_source[3:]).is_file(), want_source


def test_the_guard_evaluator_and_the_parsers_are_not_wired_to_nothing():
    """🔴 POSITIVE CONTROL for the machinery above. Every assertion there reads
    "X is present" / "X covers Y", and all of them pass vacuously if a parser
    returns something empty or uniform. So: the parsers must DISAGREE across
    entries that really do differ in the file, and the guard evaluator must
    return different host sets for different gates.
    """
    text = (_REPO / _GRAPHICAL_NIX).read_text(encoding="utf-8")
    files = _parse_home_file_entries(text)
    blocks = _parse_block_list(text)

    assert _nix_guard_hosts("") == {"workbench", "laptop"}
    assert _nix_guard_hosts("(!isLaptop)") == {"workbench"}
    assert _nix_guard_hosts("isLaptop") == {"laptop"}
    with pytest.raises(AssertionError):
        _nix_guard_hosts("(config.something.else)")

    # ⚠ Every entry named below is deliberately NOT the subject of the test
    # above — a control that restates the claim it is controlling for would go
    # red with it and prove nothing about the machinery.
    #
    # The file really does contain BOTH shapes, so "ungated" is a measurement
    # and not the only value this parser is able to produce…
    guards = {v["guard"] for v in files.values()}
    assert "" in guards and any("isLaptop" in g for g in guards), guards
    # …specifically: disk-explore is ungated, while bar_freshness.py — the
    # sibling-module idiom this one is modelled on — is (!isLaptop).
    assert files[".config/i3status-rust/scripts/disk-explore"]["guard"] == ""
    assert "isLaptop" in files[
        ".config/i3status-rust/scripts/bar_freshness.py"]["guard"]
    # sources are read per entry, not one value echoed back
    assert len({v["source"] for v in files.values() if v["source"]}) > 5

    # the block parser distinguishes gated from ungated members too
    assert _nix_guard_hosts(blocks["memoryBlock"]) == {"workbench", "laptop"}
    assert _nix_guard_hosts(blocks["rigcontrolBlock"]) == {"workbench"}
    assert _nix_guard_hosts(blocks["batteryBlock"]) == {"laptop"}

# --------------------------------------------------------------------------- #
# 🔴 WHY THERE IS NO `nix eval` TEST HERE, having written one and deleted it.
#
# Asking NIX is the ground truth, and a probe that imports nix/graphical.nix
# directly can even measure BOTH host shapes (the flake's
# `homeConfigurations.zach` derives isLaptop from this machine's hardware, so it
# can only ever show one). It works, it is fast, and it agreed with the parse
# above on every mutant:
#
#   nix eval --impure --json --expr 'let
#     pkgs = import <nixpkgs> { }; lib = pkgs.lib;
#     mod = isLaptop: (import /home/zach/workspace/devrc/nix/graphical.nix {
#       config = { home = { homeDirectory = "/home/zach"; }; };
#       inherit pkgs lib isLaptop; isNixOS = true; }).content;
#     isOn = v: if builtins.isAttrs v && (v._type or "") == "if"
#               then v.condition else true;
#     body = v: if builtins.isAttrs v && (v._type or "") == "if"
#               then v.content else v;
#     probe = isLaptop: let m = mod isLaptop; f = m.home.file;
#       pill = f.".config/i3status-rust/scripts/i3status-claude-runs";
#       sib  = f.".config/i3status-rust/scripts/claude_sessions.py"; in {
#       blockCommands = map (b: b.command or "")
#                           m.programs.i3status-rust.bars.top.blocks;
#       pillDeployed = isOn pill; siblingDeployed = isOn sib;
#       pillSource = toString (body pill).source;
#       siblingSource = toString (body sib).source; };
#     in { workbench = probe false; laptop = probe true; }'
#
# It cannot live in this suite, for a reason this repo has already written down
# twice. The nix BUILD SANDBOX has a `nix` binary but with the `nix-command`
# feature disabled, so the test does not fail there — it is skipped, or worse,
# `shutil.which("nix")` reports it available and the eval errors. Either way the
# guard means one thing on a dev host and NOTHING in the tier that gates merges,
# which is exactly the shape `scripts/run-tests.sh`'s EXPECTED_SKIPS block
# records removing twice (test_skill_audit.py's live-corpus pins; test_scrub.py's
# HOME-keyed drift guard) with "fix that instead" attached.
#
# So the guard that ships is the PARSE above — it runs in both tiers, and it was
# mutation-proven against the four real breakages (both `home.file` entries
# gated `mkIf isLaptop`, the sibling's `source` swapped to clawgate_tasks.py with
# the right path parked in a trailing comment, and `claudeRunsBlock` dropped from
# the `blocks` list). Each mutant was confirmed to `nix-instantiate --parse`
# before being scored, and each died here.
#
# Run the eval above by hand when changing the deploy wiring; it is also the
# right check to put in a host-side script (scripts/drift-check.sh) rather than
# in a hermetic suite.
# --------------------------------------------------------------------------- #
