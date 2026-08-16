#!/usr/bin/env python3
"""Tests for clawgate-writeback-guard.py — the hook that makes the clawgate task
write-back non-optional.

WHAT THIS FILE IS FOR

  1. 🔴 BOTH DIRECTIONS OF THE TRIGGER. The four NON-matches (`task ls`,
     `/api/tasks?summary=1`, a bare `/api/tasks`, `task get <non-numeric>`) are as
     load-bearing as the matches: this hook can BLOCK a turn, and arming it off a
     board survey would put a block in front of a session that never picked anything
     up. Every one of them is a separate case here.

  2. 🔴 THE FALSE-POSITIVE KILLER, TESTED AS AN ABSENCE OF THE LIVE READ. The SKILL's
     own step 2 is "EVALUATE and report to Zach, do NOT flip status" — a
     read-and-evaluate-only session must never fire. That is asserted not just by the
     verdict but by the reader NEVER BEING CALLED, so a mutation that reorders the
     work gate below the live read is visible even if it happens to end up silent.

  3. 🔴 THE LADDER IS DRIVEN WITH LITERALS THE CONSTANTS CANNOT EQUAL. This repo has
     been bitten five times by a fixture whose value equals the constant it tests, so
     `MAX_BLOCKS = 2` is never checked by something that produces 2 by construction:
     the counter is seeded at 0 and at 8 (neither of which is 2 or 3) and the
     decision is watched to MOVE from block to context to silence.

  4. 🔴 EVERY "CANNOT MEASURE" PATH IS A NOTICE, NEVER A BLOCK. A hook that goes
     silent when the board is unreachable reports the same observable as a hook that
     measured a clean card, which is the empty-result trap in RULES.md.

  5. THE HOT PATH. PostToolUse fires after every tool call, so the fast path is
     driven through a REAL subprocess with stub `clawgatectl` and `curl` on PATH that
     LOG every invocation — "no subprocess work" is a claim about the process, and an
     in-process assertion cannot make it.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
# `scripts/` on sys.path so `testlib.mockbin` — the ONE definition of "write an
# executable stub" in this repo — is importable. A hand-written `#!/usr/bin/env bash`
# stub is DEAD in the nix build sandbox and test_runtime_shebangs.py fails the gate
# for one.
sys.path.insert(0, os.path.abspath(os.path.join(HERE, os.pardir, os.pardir)))
from testlib import mockbin  # noqa: E402

ROOT = Path(HERE).resolve().parents[2]
HOOK = os.path.abspath(os.path.join(HERE, os.pardir, "clawgate-writeback-guard.py"))
HOME_NIX = ROOT / "nix" / "home.nix"
REGISTRAR = ROOT / "scripts" / "claude-hooks" / "register-nudge-hook.py"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


guard = _load("clawgate_writeback_guard_undertest", HOOK)

SESSION = "sess-writeback-1"
# A read timestamp with a shape the board really produces (RFC3339, Z, microseconds).
READ_TS = "2026-08-15T12:00:00.000000Z"
READ_EPOCH = 1786795200.0  # the same instant, pinned as a LITERAL, not computed here
READ_PLUS_1H = 1786798800.0


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


def payload(event="PostToolUse", session_id=SESSION, **kw):
    base = {"hook_event_name": event, "session_id": session_id,
            "transcript_path": "/home/zach/.claude/projects/p/a.jsonl",
            "cwd": "/home/zach/workspace/devrc"}
    base.update(kw)
    return base


def bash(cmd, **kw):
    return payload(tool_name="Bash", tool_input={"command": cmd}, **kw)


def state_dir():
    return guard._state_dir({"session_id": SESSION})


def seed(task_id=193, ts=READ_TS, work=True):
    """Put a session into the state the Stop gate reads: task read, work done."""
    sd = state_dir()
    os.makedirs(sd, exist_ok=True)
    with open(guard._read_path(sd, task_id), "w") as fh:
        json.dump({"task_id": task_id, "first_read_ts": ts}, fh)
    if work:
        guard.record_work(sd)
    return sd


def task(status="open", comments=(), task_id=193):
    return {"id": task_id, "status": status, "title": "t", "body": "b",
            "comments": list(comments)}


def comment(author="claude-code", created="2026-08-15T13:00:00.000000Z", **kw):
    c = {"id": 1, "noteId": 193, "author": author, "body": "done",
         "createdAt": created}
    c.update(kw)
    return c


class Reader:
    """A stubbed live read that RECORDS whether it was called — so "never fires" can
    be asserted as "never even measured", not merely as a quiet verdict."""

    def __init__(self, result=None, raises=None):
        self.result, self.raises, self.calls = result, raises, []

    def __call__(self, task_id, timeout=None):
        self.calls.append((task_id, timeout))
        if self.raises is not None:
            raise self.raises
        return self.result


def run_hook(data, home_dir, path_extra=None, timeout=30):
    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    if path_extra:
        env["PATH"] = str(path_extra) + os.pathsep + env.get("PATH", "")
    return subprocess.run([sys.executable, HOOK],
                          input=json.dumps(data) if data is not None else "",
                          capture_output=True, text=True, timeout=timeout, env=env)


# =========================================================================== #
# 1. TRIGGER MATCHING — both directions
# =========================================================================== #
@pytest.mark.parametrize("cmd,expected", [
    ("clawgatectl task get 193", [193]),
    ("clawgatectl task get 7 | jq .", [7]),
    ("clawgatectl  task   get  42", [42]),
    ('curl -sf http://board.invalid/api/tasks/194 -H "Authorization: Bearer x"',
     [194]),
    ("curl -sf http://board.invalid/api/tasks/194/comments", [194]),
    ("HOOK=$(grep tok f); curl -s http://board.invalid/api/tasks/12 | jq .", [12]),
])
def test_a_read_of_a_specific_task_arms_the_guard(cmd, expected):
    assert guard.task_read_ids(bash(cmd)) == expected


@pytest.mark.parametrize("cmd", [
    # 🔴 THE FOUR NAMED NON-MATCHES. A board survey is not a pickup, and a guard
    # armed by one would block a turn that claimed nothing.
    "clawgatectl task ls --summary --status open --limit 3",
    "curl -sf http://board.invalid/api/tasks?summary=1",
    "curl -sf http://board.invalid/api/tasks",
    "clawgatectl task get abc",
    # ...and the neighbours that would fall to a sloppier pattern
    "clawgatectl task get",
    "curl -sf http://board.invalid/api/tasks/193abc",
    "clawgatectl task create --body 'get 193'",
    "echo 'read /api/tasksfoo now'",
])
def test_a_listing_or_a_non_numeric_arg_does_NOT_arm_the_guard(cmd):
    assert guard.task_read_ids(bash(cmd)) == []


def test_a_non_bash_payload_has_no_command_to_match():
    assert guard.task_read_ids(payload(tool_name="Read",
                                       tool_input={"file_path": "/api/tasks/1"})) == []


def test_two_ids_in_one_command_are_both_recorded_and_deduplicated():
    cmd = ("clawgatectl task get 193; "
           "curl -s http://board.invalid/api/tasks/194/comments; "
           "clawgatectl task get 193")
    assert guard.task_read_ids(bash(cmd)) == [193, 194]


# =========================================================================== #
# 2. WORK DETECTION
# =========================================================================== #
@pytest.mark.parametrize("data", [
    payload(tool_name="Edit", tool_input={"file_path": "/x"}),
    payload(tool_name="Write", tool_input={"file_path": "/x"}),
    payload(tool_name="NotebookEdit", tool_input={"notebook_path": "/x"}),
    bash("git commit -m 'x'"),
    bash("git -C /home/zach/workspace/devrc commit -m 'x'"),
    bash("git push -u origin feat/x"),
    bash("gh pr create --fill --base main"),
])
def test_real_work_sets_the_work_flag(data):
    assert guard.is_work(data) is True


@pytest.mark.parametrize("data", [
    payload(tool_name="Read", tool_input={"file_path": "/x"}),
    payload(tool_name="Grep", tool_input={"pattern": "commit"}),
    bash("git log --oneline -3 | grep commit"),
    bash("git status -s"),
    bash("gh pr list --state open"),
    bash("clawgatectl task get 193"),
])
def test_a_look_around_is_not_work(data):
    assert guard.is_work(data) is False


# =========================================================================== #
# 3. THE POSTTOOLUSE FAST PATH
# =========================================================================== #
def test_an_untracked_session_reading_nothing_takes_the_fast_path(home):
    out = guard.post_tool_use(bash("ls -la"))
    assert out["fast_path"] is True
    assert not os.path.exists(state_dir())


def _io_spy(monkeypatch):
    """Record every filesystem call the hook makes, and every subprocess it spawns.

    🔴 Counting, not trusting a comment: an earlier hook in this repo shipped with its
    throttle consulted AFTER the subprocess spawn while its own comment claimed the
    opposite. The spies wrap the REAL functions, so the hook still behaves normally
    and the recording is the only difference.
    """
    calls = []
    for name in ("makedirs", "listdir", "replace", "open"):
        real = getattr(guard.os, name)
        monkeypatch.setattr(guard.os, name,
                            (lambda r, n: lambda *a, **k: (
                                calls.append((n, str(a[0]))), r(*a, **k))[1])(real, name))
    real_exists = guard.os.path.exists
    monkeypatch.setattr(guard.os.path, "exists",
                        lambda p, *a, **k: (calls.append(("exists", str(p))),
                                            real_exists(p, *a, **k))[1])
    # `subprocess` is a DEFERRED import (Stop path only), so the module attribute may
    # still be None. Force it bound before patching — a spy that skipped the patch
    # because the attribute was None would report a reassuring zero from nothing.
    sp = guard._sp()
    real_run = sp.run
    monkeypatch.setattr(sp, "run",
                        lambda *a, **k: (calls.append(("subprocess", str(a[0]))),
                                         real_run(*a, **k))[1])
    return calls


def _mine(calls):
    """Only the calls that touch THIS hook's state root — pytest and the stdlib use
    the same functions, so an unfiltered count would be measuring the harness."""
    root = guard._state_root()
    return [c for c in calls if c[0] == "subprocess" or root in c[1]]


def test_the_fast_path_does_exactly_one_stat_and_nothing_else(home, monkeypatch):
    """🔴 THE ORDERING, PINNED AS A COUNT. A session that has never read a clawgate
    task and is not reading one now must cost exactly ONE `os.path.exists` and spawn
    nothing — no directory creation, no state read, no client. Every write below the
    gate is unreachable for it."""
    calls = _io_spy(monkeypatch)
    guard.post_tool_use(bash("ls -la"))
    assert _mine(calls) == [("exists", state_dir())], calls
    assert [c for c in calls if c[0] == "subprocess"] == []


def test_the_positive_control_for_that_count(home, monkeypatch):
    """🔴 The one-call reading above is only meaningful if the spy CAN see more. The
    same instrumentation on a payload that IS a task read must record writes — a
    reassuring count is indistinguishable from a spy wired to nothing."""
    calls = _io_spy(monkeypatch)
    guard.post_tool_use(bash("clawgatectl task get 193"))
    kinds = [c[0] for c in _mine(calls)]
    assert "makedirs" in kinds and "replace" in kinds, calls
    assert len(_mine(calls)) > 1


def test_the_work_regex_is_NOT_evaluated_on_the_fast_path(home, monkeypatch):
    """The other half of the gate: `is_work` is reachable only past it. Made to raise
    so a mutation that hoists it above the return is loud rather than merely slower."""
    guard.post_tool_use(bash("clawgatectl task get 193", session_id="armed"))
    monkeypatch.setattr(guard, "is_work",
                        lambda d: (_ for _ in ()).throw(
                            AssertionError("is_work ran on the fast path")))
    # An UNTRACKED session: the gate returns before the work regex is consulted.
    assert guard.post_tool_use(bash("ls -la",
                                    session_id="never-touched-the-board")
                               )["fast_path"] is True
    # POSITIVE CONTROL: the ARMED session takes the same payload past the gate, so the
    # raiser proves it is reachable at all rather than never being installed.
    with pytest.raises(AssertionError):
        guard.post_tool_use(bash("ls -la", session_id="armed"))


def test_the_fast_path_spawns_no_subprocess_at_all(home, tmp_path):
    """Driven through a REAL subprocess with stub `clawgatectl` and `curl` on PATH
    that log every invocation. "No subprocess work" is a claim about the process."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "spawned.log"
    for name in ("clawgatectl", "curl"):
        mockbin.write_exec(bindir / name,
                           'echo "%s $@" >> %s\nprintf "{}\\n"\n'
                           % (name, json.dumps(str(log))))
    p = run_hook(bash("ls -la"), home, path_extra=bindir)
    assert p.returncode == 0
    assert p.stdout == ""
    assert not log.exists(), log.read_text()
    assert not os.path.exists(os.path.join(str(home), ".cache",
                                           "claude-clawgate-writeback"))


def _imported_modules(payload_obj, home_dir):
    """The module names CPython actually imported for one hook run, taken from
    `-X importtime`. Measured, not asserted — the deferral is a cost claim and a cost
    claim needs a measurement."""
    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    p = subprocess.run([sys.executable, "-X", "importtime", HOOK],
                       input=json.dumps(payload_obj), capture_output=True,
                       text=True, timeout=60, env=env)
    return {line.rsplit("|", 1)[-1].strip()
            for line in p.stderr.splitlines() if line.startswith("import time:")}


def test_the_fast_path_does_not_even_IMPORT_subprocess_or_shutil(home):
    """🔴 The two most expensive imports in this file — measured with `-X importtime`
    at 3.4 ms and 3.7 ms — are Stop-only, and the PostToolUse path must not pay them.
    Asserted on what CPython actually loaded, not on where the `import` line sits."""
    mods = _imported_modules(bash("ls -la"), home)
    assert "re" in mods and "json" in mods          # the path CANNOT avoid these
    assert "subprocess" not in mods
    assert "shutil" not in mods


def test_the_positive_control_for_that_deferral(home):
    """The Stop path DOES import both — so the absence above is the deferral working,
    rather than an importtime parser that matches nothing."""
    guard.post_tool_use(bash("clawgatectl task get 193"))
    guard.record_work(state_dir())
    # ...and something for the prune to actually remove: `shutil` is loaded inside the
    # removal itself, so a Stop with nothing stale to sweep legitimately never needs it.
    stale = os.path.join(guard._state_root(), "ancient")
    os.makedirs(stale, exist_ok=True)
    os.utime(stale, (1.0, 1.0))
    mods = _imported_modules(payload("Stop"), home)
    assert "subprocess" in mods
    assert "shutil" in mods
    assert not os.path.exists(stale)


def test_a_read_records_the_first_timestamp_and_never_moves_it(home):
    """A re-read an hour later must NOT move the window this hook measures over: the
    question is "was there a comment since you FIRST looked", and a moving anchor
    would let a session re-read its way out of the gate."""
    guard.post_tool_use(bash("clawgatectl task get 193"), now=READ_EPOCH)
    guard.post_tool_use(bash("clawgatectl task get 193"), now=READ_PLUS_1H)
    ids = guard.tracked_ids(state_dir())
    assert ids == {193: "2026-08-15T12:00:00Z"}


def test_work_is_only_recorded_after_a_read(home):
    guard.post_tool_use(payload(tool_name="Edit", tool_input={"file_path": "/x"}))
    assert not os.path.exists(state_dir())
    guard.post_tool_use(bash("clawgatectl task get 193"))
    guard.post_tool_use(payload(tool_name="Edit", tool_input={"file_path": "/x"}))
    assert guard.work_after_read(state_dir()) is True


def test_at_most_five_task_ids_are_tracked_per_session(home):
    for tid in (11, 12, 13, 14, 15, 16, 17):
        guard.post_tool_use(bash("clawgatectl task get %d" % tid))
    assert sorted(guard.tracked_ids(state_dir())) == [11, 12, 13, 14, 15]


# =========================================================================== #
# 4. writeback_state — the live measurement, decomposed
# =========================================================================== #
def test_no_comments_at_all_is_the_measured_failure():
    assert guard.writeback_state(task(comments=[]), READ_TS) == "missing"


def test_a_claude_code_comment_since_the_read_silences_it():
    c = comment(created="2026-08-15T13:00:00.000000Z")
    assert guard.writeback_state(task(comments=[c]), READ_TS) == "written"


def test_only_an_OLDER_comment_still_counts_as_missing():
    """An hour before the read — well outside any clock-skew allowance."""
    c = comment(created="2026-08-15T11:00:00.000000Z")
    assert guard.writeback_state(task(comments=[c]), READ_TS) == "missing"


def test_a_comment_inside_the_skew_allowance_counts_as_written():
    """🔴 The fixture is 30 s and 3600 s — neither can equal
    CLOCK_SKEW_ALLOWANCE_SECS, so this cannot pass by being the constant."""
    near = comment(created="2026-08-15T11:59:30.000000Z")   # 30 s before
    far = comment(created="2026-08-15T11:00:00.000000Z")    # 3600 s before
    assert guard.writeback_state(task(comments=[near]), READ_TS) == "written"
    assert guard.writeback_state(task(comments=[far]), READ_TS) == "missing"


@pytest.mark.parametrize("status", ["ready_for_review", "complete"])
def test_a_card_someone_already_closed_is_left_alone(status):
    assert guard.writeback_state(task(status=status, comments=[]), READ_TS) == "closed"


@pytest.mark.parametrize("status", ["open", "in_progress"])
def test_an_open_or_in_progress_card_is_still_measured(status):
    assert guard.writeback_state(task(status=status, comments=[]), READ_TS) == "missing"


def test_a_comment_by_someone_else_is_not_this_agent_writing_back():
    for author in ("user", "api", "drafter", "repo-cos", "extension"):
        c = comment(author=author, created="2026-08-15T13:00:00.000000Z")
        assert guard.writeback_state(task(comments=[c]), READ_TS) == "missing", author


def test_a_RETRACTED_comment_is_not_a_write_back():
    c = comment(created="2026-08-15T13:00:00.000000Z", body="", retracted=True)
    assert guard.writeback_state(task(comments=[c]), READ_TS) == "missing"


def test_an_unparseable_comment_timestamp_resolves_toward_SILENCE():
    """The comment demonstrably exists; only its timestamp is unreadable. Resolving
    that toward a BLOCK would spend the operator's turn on a formatting change at the
    far end of a wire this hook does not own."""
    c = comment(created="not-a-timestamp")
    assert guard.writeback_state(task(comments=[c]), READ_TS) == "written"


def test_an_unparseable_READ_timestamp_is_unknown_not_missing():
    assert guard.writeback_state(task(comments=[]), "garbage") == "unknown"


@pytest.mark.parametrize("bad", [None, "a string", 42, [1, 2]])
def test_a_task_payload_that_is_not_an_object_is_unknown(bad):
    assert guard.writeback_state(bad, READ_TS) == "unknown"


def test_a_comments_field_that_is_not_a_list_is_unknown():
    t = task()
    t["comments"] = {"oops": 1}
    assert guard.writeback_state(t, READ_TS) == "unknown"


def test_a_missing_comments_key_is_an_empty_board_not_an_error():
    t = task()
    del t["comments"]
    assert guard.writeback_state(t, READ_TS) == "missing"


# =========================================================================== #
# 5. parse_ts
# =========================================================================== #
@pytest.mark.parametrize("s", [
    "2026-08-15T12:00:00.000000Z",
    "2026-08-15T12:00:00Z",
    "2026-08-15T12:00:00.000000000Z",   # Go RFC3339Nano: nine fractional digits
    "2026-08-15T12:00:00+00:00",
    "2026-08-15T07:00:00-05:00",
    # NAIVE — no offset at all. The board always sends one, but a proxy or a hand-run
    # curl need not, and reading a naive stamp as LOCAL time would shift the cutoff by
    # the host's offset and silence a genuinely missing write-back.
    "2026-08-15T12:00:00",
])
def test_every_shape_the_board_emits_parses_to_the_same_instant(s):
    assert guard.parse_ts(s) == READ_EPOCH


@pytest.mark.parametrize("s", [None, "", "   ", "garbage", 42, "2026-13-45T99:99:99Z"])
def test_an_unparseable_timestamp_is_None_not_an_exception(s):
    assert guard.parse_ts(s) is None


# =========================================================================== #
# 6. THE FALSE-POSITIVE KILLER — no work after read
# =========================================================================== #
def test_a_read_and_evaluate_only_session_NEVER_fires(home):
    """🔴 The SKILL's own step 2. Asserted as "the board was never even read", so a
    mutation that moves the work gate below the live read is visible."""
    seed(work=False)
    r = Reader(result=task(comments=[]))
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert (kind, text) == ("silent", "")
    assert r.calls == []


def test_the_positive_control_for_that_absence(home):
    """The same fixture WITH the work flag must fire — otherwise the test above is
    green because the harness is wired to nothing."""
    seed(work=True)
    r = Reader(result=task(comments=[]))
    kind, _ = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "block"
    assert [c[0] for c in r.calls] == [193]


def test_a_read_then_NO_work_never_fires_END_TO_END(home):
    """The same guard as above, but driven through `post_tool_use` rather than a
    hand-seeded state dir — found by a differently-built mutation sweep, which showed
    that forcing the work gate TRUE inside `post_tool_use` survived every test here
    because they all seeded the flag directly and never exercised the writer."""
    guard.post_tool_use(bash("clawgatectl task get 193"))
    guard.post_tool_use(bash("git status -s"))
    guard.post_tool_use(bash("gh pr list --state open"))
    guard.post_tool_use(payload(tool_name="Read", tool_input={"file_path": "/x"}))
    assert guard.work_after_read(state_dir()) is False
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")
    assert r.calls == []


def test_a_state_file_with_no_usable_timestamp_is_DROPPED(home):
    """A corrupt state file must not become a task the hook nags about: without a
    first-read timestamp there is no window to measure over, so there is nothing to
    say. Silence, not an UNVERIFIED notice about this hook's own bookkeeping."""
    sd = state_dir()
    os.makedirs(sd, exist_ok=True)
    for tid, ts in ((193, None), (194, ""), (195, 42)):
        with open(guard._read_path(sd, tid), "w") as fh:
            json.dump({"task_id": tid, "first_read_ts": ts}, fh)
    guard.record_work(sd)
    assert guard.tracked_ids(sd) == {}
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")
    assert r.calls == []


def test_a_session_that_never_read_a_task_is_silent(home):
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")
    assert r.calls == []


# =========================================================================== #
# 7. THE STOP VERDICTS
# =========================================================================== #
def test_a_comment_written_since_the_read_self_suppresses_the_guard(home):
    seed()
    r = Reader(result=task(comments=[comment(created="2026-08-15T13:00:00.000000Z")]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")


def test_an_older_comment_alone_still_blocks(home):
    seed()
    r = Reader(result=task(comments=[comment(created="2026-08-15T11:00:00.000000Z")]))
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "block"
    assert "193" in text


@pytest.mark.parametrize("status", ["ready_for_review", "complete"])
def test_a_closed_card_is_silent_end_to_end(home, status):
    seed()
    r = Reader(result=task(status=status, comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")


def test_the_block_reason_names_the_id_the_timestamp_and_BOTH_fix_commands(home):
    seed()
    r = Reader(result=task(comments=[]))
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "block"
    # 🔴 Pinned as the WHOLE literal command line, not as the presence of the words
    # "comment" and "status" — a guard on words is walkable by rewording, and this
    # text is the entire product of the hook.
    assert "clawgatectl task comment 193 --body" in text
    assert "clawgatectl task status 193 ready_for_review" in text
    assert READ_TS in text
    assert "MISSING" in text


def test_the_wall_clock_budget_stops_the_live_reads(home):
    """🔴 A Stop hook that hangs is felt at the exact moment a session is trying to
    end. The budget is driven with 3.0 — a value neither STOP_BUDGET_SECS (8.0) nor
    PER_TASK_TIMEOUT_SECS (5.0) can equal, so this cannot pass by being the constant."""
    for tid in (11, 12, 13, 14, 15):
        seed(task_id=tid)
    ticks = iter([0.0, 0.0, 9.0, 9.0, 9.0, 9.0])
    r = Reader(result=task(comments=[]))
    guard.stop_decision(payload("Stop"), reader=r, budget=3.0,
                        clock=lambda: next(ticks))
    assert [c[0] for c in r.calls] == [11]
    # ...and the per-task timeout is the SMALLER of the ceiling and what is left of
    # the budget, not the ceiling: 3.0 remaining under a 5.0 ceiling.
    assert r.calls[0][1] == 3.0


def test_the_positive_control_for_that_budget(home):
    """The same five tasks with time to spare must ALL be read — otherwise the single
    read above is a harness that never had a second iteration to cut off."""
    for tid in (11, 12, 13, 14, 15):
        seed(task_id=tid)
    r = Reader(result=task(comments=[]))
    guard.stop_decision(payload("Stop"), reader=r, budget=3.0,
                        clock=lambda: 0.0)
    assert [c[0] for c in r.calls] == [11, 12, 13, 14, 15]


def test_several_offending_tasks_are_reported_in_one_decision(home):
    seed(task_id=193)
    seed(task_id=194)
    r = Reader(result=task(comments=[]))
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "block"
    assert "clawgatectl task status 193 ready_for_review" in text
    assert "clawgatectl task status 194 ready_for_review" in text
    assert [c[0] for c in r.calls] == [193, 194]


# =========================================================================== #
# 8. THE LIVE READ FAILING — a notice, NEVER a block
# =========================================================================== #
def test_an_unreachable_board_emits_a_NON_BLOCKING_notice(home):
    seed()
    r = Reader(raises=guard.LiveReadError("connection refused"))
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "context"
    assert kind != "block"
    assert "193" in text and "could not be reached" in text
    assert "connection refused" in text


def test_an_unreachable_board_NEVER_blocks_at_any_rung(home):
    """🔴 Driven up the whole ladder. The first two rungs are where a MEASURED miss
    blocks, so an all-unknown session reaching them and staying non-blocking is the
    real assertion."""
    seed()
    kinds = []
    for _ in range(5):
        r = Reader(raises=guard.LiveReadError("boom"))
        kinds.append(guard.stop_decision(payload("Stop"), reader=r)[0])
    assert kinds == ["context", "context", "context", "silent", "silent"]
    assert "block" not in kinds


def test_a_reader_raising_something_unexpected_is_also_only_a_notice(home):
    seed()
    r = Reader(raises=RuntimeError("kaboom"))
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "context"
    assert "kaboom" in text


def test_an_unreadable_task_payload_is_a_notice_not_a_block(home):
    seed()
    r = Reader(result="this is not a task object")
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "context"
    assert "UNVERIFIED" in text


# =========================================================================== #
# 9. THE ESCALATION LADDER
# =========================================================================== #
def test_the_ladder_constants_are_what_the_docstring_claims():
    assert guard.MAX_BLOCKS == 2
    assert guard.MAX_FIRES == 3
    assert guard.MAX_TASKS == 5


@pytest.mark.parametrize("fire,rung", [
    (1, "block"), (2, "block"), (3, "context"), (4, "silent"), (9, "silent"),
])
def test_escalate_maps_each_fire_number_to_its_rung(fire, rung):
    assert guard.escalate(fire) == rung


def test_the_ladder_end_to_end_on_one_task(home):
    """🔴 Literal expected sequence, pinned from the ladder in the docstring — NOT
    derived from MAX_BLOCKS/MAX_FIRES, which is how a test comes to agree with a
    mutated implementation."""
    seed()
    kinds = []
    for _ in range(5):
        r = Reader(result=task(comments=[]))
        kinds.append(guard.stop_decision(payload("Stop"), reader=r)[0])
    assert kinds == ["block", "block", "context", "silent", "silent"]


def test_a_counter_seeded_far_past_the_cap_is_silent(home):
    """🔴 THE FIXTURE-EQUALS-CONSTANT CONTROL. 8 is a value MAX_BLOCKS (2) and
    MAX_FIRES (3) cannot equal, so a mutant that hardcodes either literal cannot
    survive this: the next fire is 9 and the only correct answer is silence."""
    sd = seed()
    with open(guard._fires_path(sd, 193), "w") as fh:
        fh.write("8")
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")


def test_a_counter_seeded_at_zero_still_blocks(home):
    """The other end of the same control: 0 is also not 2 or 3, and the output MOVES."""
    sd = seed()
    with open(guard._fires_path(sd, 193), "w") as fh:
        fh.write("0")
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r)[0] == "block"


def test_the_ladder_is_per_task_id_not_per_session(home):
    seed(task_id=193)
    sd = state_dir()
    with open(guard._fires_path(sd, 193), "w") as fh:
        fh.write("8")
    seed(task_id=194)
    r = Reader(result=task(comments=[]))
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "block"
    assert "task status 194" in text
    assert "task status 193" not in text


# =========================================================================== #
# 9b. STATE HOUSEKEEPING
# =========================================================================== #
def test_prune_drops_stale_session_state_and_keeps_fresh(home):
    """One directory per session, forever, is an unbounded cache.

    🔴 The fixtures BRACKET the TTL from both sides — 5 days must survive and 20 days
    must not — so a TTL moved in EITHER direction past that bracket goes red. Ages of
    1 h / 5 d / 20 d / 40 d; none of them can equal STATE_TTL_SECS (14 days), so no
    fixture here can pass by being the constant it tests."""
    root = guard._state_root()
    now = 1786795200.0
    ages = {"fresh": 3600, "recent": 5 * 86400,
            "stale": 20 * 86400, "ancient": 40 * 86400}
    for name, age in ages.items():
        os.makedirs(os.path.join(root, name), exist_ok=True)
        os.utime(os.path.join(root, name), (now - age, now - age))
    removed = guard.prune(now=now)
    assert sorted(removed) == ["ancient", "stale"]
    assert sorted(os.listdir(root)) == ["fresh", "recent"]


def test_prune_on_a_missing_root_is_not_an_error(home):
    assert guard.prune() == []


def test_the_Stop_path_prunes_and_the_prune_runs_AFTER_the_verdict(home, tmp_path):
    """The verdict must not wait on housekeeping — and a prune that raises must not
    swallow a verdict that has already been written."""
    seed()
    root = guard._state_root()
    os.makedirs(os.path.join(root, "ancient"), exist_ok=True)
    old = 1.0
    os.utime(os.path.join(root, "ancient"), (old, old))
    b = tmp_path / "prunebin"
    b.mkdir()
    mockbin.write_exec(b / "clawgatectl",
                       "printf '%%s\\n' '%s'\n" % json.dumps(task(comments=[])))
    p = run_hook(payload("Stop"), home, path_extra=b)
    assert json.loads(p.stdout)["decision"] == "block"
    assert not os.path.exists(os.path.join(root, "ancient"))
    # ...and THIS session's own state survived the prune it just triggered.
    assert os.path.exists(state_dir())


# =========================================================================== #
# 10. EVENT SCOPE
# =========================================================================== #
def test_SubagentStop_is_refused(home):
    """🔴 A subagent's turn never reaches the operator, so it owes them nothing —
    next-step-nudge.py refuses it for the same reason."""
    seed()
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("SubagentStop"), reader=r) == ("silent", "")
    assert r.calls == []


def test_a_payload_carrying_an_agent_id_is_refused(home):
    seed()
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop", agent_id="ag-1"),
                               reader=r) == ("silent", "")
    assert r.calls == []


def test_SubagentStop_produces_no_output_through_the_real_process(home, tmp_path):
    seed()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    mockbin.write_exec(bindir / "clawgatectl",
                       "printf '%%s\\n' '%s'\n" % json.dumps(task(comments=[])))
    p = run_hook(payload("SubagentStop"), home, path_extra=bindir)
    assert p.returncode == 0
    assert p.stdout == ""


# =========================================================================== #
# 11. THE PROCESS CONTRACT — fail-open, always
# =========================================================================== #
@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null", '{"a":1}',
                                   '{"hook_event_name":"Stop"}',
                                   '{"hook_event_name":"Stop","session_id":{"x":1}}',
                                   '{"hook_event_name":"PostToolUse"}'])
def test_malformed_input_exits_0_and_says_nothing(home, stdin):
    env = dict(os.environ)
    env["HOME"] = str(home)
    p = subprocess.run([sys.executable, HOOK], input=stdin,
                       capture_output=True, text=True, timeout=30, env=env)
    assert p.returncode == 0, p.stderr
    assert p.stdout == ""
    assert p.stderr == ""


def test_an_internal_exception_exits_0_with_an_empty_stdout(home, monkeypatch,
                                                            capsys):
    """🔴 The fail-open backstop itself, not a proxy for it: `stop_decision` is made
    to raise and main() must still exit 0 with nothing on stdout."""
    monkeypatch.setattr(guard, "stop_decision",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(guard.sys, "stdin",
                        io.StringIO(json.dumps(payload("Stop"))))
    with pytest.raises(SystemExit) as e:
        guard.main()
    assert e.value.code == 0
    assert capsys.readouterr().out == ""


def test_the_positive_control_for_that_backstop(home, monkeypatch, capsys):
    """Without the injected exception the same wiring MUST produce a block — so the
    empty stdout above is the backstop working, not a harness wired to nothing."""
    seed()
    monkeypatch.setattr(guard, "live_task",
                        lambda tid, timeout=None: task(comments=[]))
    monkeypatch.setattr(guard.sys, "stdin",
                        io.StringIO(json.dumps(payload("Stop"))))
    with pytest.raises(SystemExit) as e:
        guard.main()
    assert e.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "clawgatectl task comment 193 --body" in out["reason"]


# =========================================================================== #
# 12. THE EMITTED JSON — the shapes read out of the installed CLI's own schema
# =========================================================================== #
def test_block_is_a_TOP_LEVEL_decision_object(capsys):
    guard.emit("block", "why")
    out = json.loads(capsys.readouterr().out)
    assert out == {"decision": "block", "reason": "why"}


def test_context_is_a_hookSpecificOutput_arm_naming_Stop(capsys):
    guard.emit("context", "fyi")
    out = json.loads(capsys.readouterr().out)
    assert out == {"hookSpecificOutput": {"hookEventName": "Stop",
                                          "additionalContext": "fyi"}}


def test_silent_writes_nothing_at_all(capsys):
    guard.emit("silent", "ignored")
    assert capsys.readouterr().out == ""


# =========================================================================== #
# 13. THE LIVE READ ITSELF — real subprocesses, real fallback
# =========================================================================== #
def _bin(tmp_path):
    d = tmp_path / "livebin"
    d.mkdir()
    return d


def _isolated_path(monkeypatch, bindir):
    """PATH containing ONLY the stubs, so a real `clawgatectl` on the dev host cannot
    answer for an absent one and make the fallback untestable.

    🔴 EVERY STUB BELOW USES SHELL BUILTINS ONLY (`echo`, `read`, `exit`) — with PATH
    stripped to one directory, an external `cat` or `printf` is NOT FOUND, and the
    failure is nearly invisible: `>>` creates the log file before command lookup, so
    the stub still exits 0 and the test reads an EMPTY log as "the hook sent nothing".
    That is exactly how this file's token-in-argv assertion first passed for the wrong
    reason. Measured: the same stub with an external `cat` logged 0 bytes of a 5-line
    config the hook demonstrably sent.
    """
    monkeypatch.setenv("PATH", str(bindir))


def _sh_json(obj):
    """Emit a JSON blob from a stub using only the `echo` builtin."""
    return "echo '%s'\n" % json.dumps(obj)


def _sh_capture_stdin(path):
    """Copy stdin into `path` using only `read`/`echo` builtins."""
    return ("while IFS= read -r __l; do echo \"$__l\" >> %s; done\n"
            % json.dumps(str(path)))


def test_live_task_prefers_clawgatectl(tmp_path, monkeypatch):
    b = _bin(tmp_path)
    mockbin.write_exec(b / "clawgatectl", _sh_json(task(comments=[])))
    _isolated_path(monkeypatch, b)
    assert guard.live_task(193, timeout=5)["id"] == 193


def test_live_task_falls_back_to_curl_when_clawgatectl_is_ABSENT(tmp_path,
                                                                monkeypatch):
    """🔴 The laptop today: its homelab-talos checkout predates cmd/clawgatectl, so
    nix does not build the binary and the hook must still be able to measure."""
    b = _bin(tmp_path)
    argv_log = tmp_path / "curl-argv.log"
    cfg_log = tmp_path / "curl-cfg.log"
    mockbin.write_exec(b / "curl",
                       'echo "$@" >> %s\n' % json.dumps(str(argv_log))
                       + _sh_capture_stdin(cfg_log)
                       + _sh_json(task(comments=[], task_id=194)))
    _isolated_path(monkeypatch, b)
    envf = tmp_path / "clawgate.env"
    envf.write_text("CLAWGATE_API_URL=http://board.invalid:1\n"
                    "CLAWGATE_HOOK_TOKEN=tok-SECRET-123\n")
    got = guard.live_task(194, timeout=5, env_path=str(envf))
    assert got["id"] == 194
    cfg = cfg_log.read_text()
    # POSITIVE CONTROL FIRST: the config log must be non-empty, or the argv assertion
    # below is satisfied by a stub that captured nothing.
    assert "http://board.invalid:1/api/tasks/194" in cfg
    assert "tok-SECRET-123" in cfg
    # 🔴 ...and only then: THE TOKEN MUST NOT BE IN ARGV. This hook runs after every
    # turn, and an argv is readable by every process on the box through /proc.
    assert "tok-SECRET-123" not in argv_log.read_text()
    assert "-K -" in argv_log.read_text()


def test_live_task_falls_back_when_clawgatectl_EXITS_NONZERO(tmp_path, monkeypatch):
    b = _bin(tmp_path)
    mockbin.write_exec(b / "clawgatectl", "echo 'boom' >&2\nexit 6\n")
    mockbin.write_exec(b / "curl",
                       _sh_capture_stdin(tmp_path / "sink.log")
                       + _sh_json(task(comments=[], task_id=195)))
    _isolated_path(monkeypatch, b)
    envf = tmp_path / "clawgate.env"
    envf.write_text("CLAWGATE_API_URL=http://board.invalid:1\n"
                    "CLAWGATE_HOOK_TOKEN=t\n")
    assert guard.live_task(195, timeout=5, env_path=str(envf))["id"] == 195


def test_live_task_raises_when_curl_EXITS_NONZERO(tmp_path, monkeypatch):
    """An unreachable board is the commonest shape of this: curl exits 7/22 and the
    hook must report "could not measure", never "the card is clean"."""
    b = _bin(tmp_path)
    mockbin.write_exec(b / "curl",
                       _sh_capture_stdin(tmp_path / "sink2.log") + "exit 7\n")
    _isolated_path(monkeypatch, b)
    envf = tmp_path / "clawgate.env"
    envf.write_text("CLAWGATE_API_URL=http://board.invalid:1\n"
                    "CLAWGATE_HOOK_TOKEN=t\n")
    with pytest.raises(guard.LiveReadError) as e:
        guard.live_task(199, timeout=5, env_path=str(envf))
    assert "curl rc=7" in str(e.value)


def test_live_task_raises_when_there_is_no_client_at_all(tmp_path, monkeypatch):
    b = _bin(tmp_path)
    _isolated_path(monkeypatch, b)
    envf = tmp_path / "clawgate.env"
    envf.write_text("CLAWGATE_API_URL=http://board.invalid:1\n"
                    "CLAWGATE_HOOK_TOKEN=t\n")
    with pytest.raises(guard.LiveReadError) as e:
        guard.live_task(196, timeout=5, env_path=str(envf))
    assert "neither clawgatectl nor curl" in str(e.value)


def test_live_task_raises_when_the_env_file_has_no_credentials(tmp_path,
                                                               monkeypatch):
    b = _bin(tmp_path)
    mockbin.write_exec(b / "curl", _sh_capture_stdin(tmp_path / "s.log")
                       + _sh_json({}))
    _isolated_path(monkeypatch, b)
    with pytest.raises(guard.LiveReadError) as e:
        guard.live_task(197, timeout=5, env_path=str(tmp_path / "nope.env"))
    assert "has no API url/token" in str(e.value)
    # 🔴 ...and it names WHICH client failed first. A `clawgatectl` that exists but
    # exits non-zero must not be reported as "not on PATH" — a diagnosis pointing at
    # the wrong subsystem. Found by a LIVE probe, not by a stub.
    assert "clawgatectl not on PATH" in str(e.value)


def test_a_FAILING_clawgatectl_is_not_reported_as_an_ABSENT_one(tmp_path,
                                                                monkeypatch):
    b = _bin(tmp_path)
    mockbin.write_exec(b / "clawgatectl", "echo 'no token file' >&2\nexit 3\n")
    _isolated_path(monkeypatch, b)
    with pytest.raises(guard.LiveReadError) as e:
        guard.live_task(197, timeout=5, env_path=str(tmp_path / "nope.env"))
    msg = str(e.value)
    assert "clawgatectl rc=3" in msg and "no token file" in msg
    assert "not on PATH" not in msg


def test_live_task_raises_on_unparseable_stdout(tmp_path, monkeypatch):
    b = _bin(tmp_path)
    mockbin.write_exec(b / "clawgatectl", "echo 'not json'\n")
    _isolated_path(monkeypatch, b)
    with pytest.raises(guard.LiveReadError):
        guard.live_task(198, timeout=5, env_path=str(tmp_path / "nope.env"))


def test_the_env_file_parser_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "e"
    f.write_text("# a comment\n\nCLAWGATE_API_URL=http://x:1\nnot-a-pair\n"
                 "CLAWGATE_HOOK_TOKEN=abc\n")
    conf = guard._env_file(str(f))
    assert conf == {"CLAWGATE_API_URL": "http://x:1", "CLAWGATE_HOOK_TOKEN": "abc"}


def test_a_missing_env_file_is_an_empty_config_not_an_exception(tmp_path):
    assert guard._env_file(str(tmp_path / "absent")) == {}


# =========================================================================== #
# 14. END TO END through the real process, with a stub board
# =========================================================================== #
def test_the_measured_failure_reproduced_end_to_end(home, tmp_path):
    """#193's exact shape: read the card, do work, stop — and the board still shows
    an `open` card with zero comments."""
    b = tmp_path / "e2ebin"
    b.mkdir()
    mockbin.write_exec(b / "clawgatectl",
                       "printf '%%s\\n' '%s'\n" % json.dumps(task(comments=[])))
    assert run_hook(bash("clawgatectl task get 193"), home,
                    path_extra=b).stdout == ""
    assert run_hook(payload(tool_name="Edit", tool_input={"file_path": "/x"}),
                    home, path_extra=b).stdout == ""
    p = run_hook(payload("Stop"), home, path_extra=b)
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["decision"] == "block"
    assert "clawgatectl task comment 193 --body" in out["reason"]


def test_the_same_session_goes_quiet_once_the_comment_exists(home, tmp_path):
    b = tmp_path / "e2ebin2"
    b.mkdir()
    body = json.dumps(task(comments=[comment(created="2099-01-01T00:00:00.000000Z")]))
    mockbin.write_exec(b / "clawgatectl", "printf '%%s\\n' '%s'\n" % body)
    run_hook(bash("clawgatectl task get 193"), home, path_extra=b)
    run_hook(payload(tool_name="Edit", tool_input={"file_path": "/x"}), home,
             path_extra=b)
    p = run_hook(payload("Stop"), home, path_extra=b)
    assert p.returncode == 0
    assert p.stdout == ""


# =========================================================================== #
# 15. THE DELIVERY SEAM — a hook nix does not deploy is a hook that does not exist
# =========================================================================== #
def test_home_nix_deploys_this_hook():
    """🔴 This repo's standing trap: a new file the flake does not carry is silently
    omitted and the switch still succeeds."""
    src = HOME_NIX.read_text()
    assert 'home.file.".claude/hooks/clawgate-writeback-guard.py"' in src
    assert "../scripts/claude-hooks/clawgate-writeback-guard.py" in src


def test_the_registrar_registers_it_on_PostToolUse_and_Stop():
    src = REGISTRAR.read_text()
    body = src.split('"""', 2)[-1]      # tables only, not the docstring's prose
    assert "clawgate-writeback-guard.py" in body
    assert 'WRITEBACK_EVENTS = ["PostToolUse", "Stop"]' in body


def test_the_hook_file_is_executable_and_parses():
    assert os.path.exists(HOOK)
    compile(open(HOOK).read(), HOOK, "exec")
