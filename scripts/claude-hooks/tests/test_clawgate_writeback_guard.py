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

  6. 🔴 THE NON-BLOCKING RUNG IS ASSERTED ON THE OBSERVABLE, NOT ON A KIND STRING.
     `stop_decision` returning the string "context" was the whole of the old proof
     that a rung "did not block" — and it was a SPELLED guard in the exact sense
     RULES.md names: the string said non-blocking while the JSON it produced
     (`hookSpecificOutput.additionalContext`) was pushed by the CLI into the same
     `blockingErrors` array as `decision:"block"`, forcing a third continuation. A
     test on the internal name structurally could not see that. Every such assertion
     now runs the verdict through `guard.emit` and asks
     `forces_a_continuation(<the emitted JSON>)`, whose rule is transcribed from the
     installed bundle (see the module docstring of the hook).
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
# The last work event: 30 min after the read, 30 min before the "written" comment at
# 13:00. Distinct from BOTH so a fixture cannot pass by collapsing the two anchors.
WORK_TS = "2026-08-15T12:30:00.000000Z"
WORK_EPOCH = 1786797000.0


def forces_a_continuation(out):
    """Would this hook output make the CLI re-query the model instead of ending the
    turn? Transcribed from claude-code 2.1.220's `bin/.claude-wrapped`, function
    `Ycd`, which pushes BOTH shapes into one `blockingErrors` array:

        if (F.blockingError)      { … E.push(G); … }
        if (F.additionalContexts) { … E.push(j); … }
        if (E.length > 0) return { blockingErrors: E, preventContinuation: !1 };

    and whose caller continues the loop with `stopHookActive:!0` for any non-empty
    `blockingErrors`. `systemMessage` is absent from that path on purpose: it is
    yielded as a `hook_system_message` MESSAGE and never reaches `E`.
    """
    if not isinstance(out, dict):
        return False
    if out.get("decision") == "block":
        return True
    hso = out.get("hookSpecificOutput")
    return bool(isinstance(hso, dict) and hso.get("additionalContext"))


def emitted(capsys, verdict):
    """Run one `(kind, text)` verdict through the REAL writer and return the JSON that
    reached stdout (None when it wrote nothing). The point is that no test asserts
    "non-blocking" against an internal kind string — see item 6 in the module
    docstring."""
    guard.emit(*verdict)
    raw = capsys.readouterr().out
    return json.loads(raw) if raw.strip() else None


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


def seed(task_id=193, ts=READ_TS, work=True, work_ts=WORK_EPOCH, session=SESSION):
    """Put a session into the state the Stop gate reads: task read, work done.

    🔴 The work stamp is a FIXED instant (WORK_TS), never `time.time()`. The Stop gate
    compares board comments against the LAST WORK EVENT, so a "now" here would sit in
    the real present and make every fixture comment retroactively stale — a whole file
    of tests that pass or fail by the calendar.
    """
    sd = guard._state_dir({"session_id": session})
    os.makedirs(sd, exist_ok=True)
    with open(guard._read_path(sd, task_id), "w") as fh:
        json.dump({"task_id": task_id, "first_read_ts": ts}, fh)
    if work:
        guard.record_work(sd, now=work_ts)
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
    # Previously FAIL-OPEN under-matches: both ship something, neither was work.
    bash("gh pr merge 512 --squash"),
    bash("gh release create v0.7.90 --notes 'x'"),
    # ...and the shapes the command-position anchor must still admit, or narrowing
    # the regex would have silently traded seven false positives for a dead hook.
    bash("git status -s && git commit -m ok"),
    bash("cd /tmp; git push"),
    bash("for f in a b; do git commit -m x; done"),
    bash("if true; then git push; fi"),
    bash("(git commit -m x)"),
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


@pytest.mark.parametrize("cmd", [
    # 🔴 THE SEVEN MEASURED OVER-MATCHES. `is_work` used to search for its literal
    # text ANYWHERE in the command, and these exact strings live in this repo's own
    # RULES.md and CLAUDE.md — so grepping for them is a routine act that armed a hook
    # able to BLOCK the turn. Every one of them TALKS about work; none does any.
    "grep -rn 'git commit' scripts/",
    "rg 'gh pr create' claude/skills/",
    "git log --grep='git push'",
    "ls -la  # then git commit",
    "echo 'remember to git commit later'",
    'echo "reminder: git push before you stop"',
    "echo remember to git commit later",
    # ...and the neighbours a sloppier narrowing would readmit
    "rg -n 'gh pr merge' docs/",
    "git log --oneline --grep='gh release create'",
    # 🔴 THE CASES THAT NEED THE STRIPPER AND NOT JUST THE ANCHOR. A shell separator
    # INSIDE a quoted string is not a separator — but the command-position regex has
    # no way to know that, so it reads `'…; git commit'` as a fresh command and the
    # over-match comes straight back. Found by a mutation sweep: bypassing
    # `_strip_literals` SURVIVED until these three existed.
    "echo 'first do a thing; git commit -m x'",
    'echo "build it && git push"',
    "rg -n 'foo | git push' claude/",
])
def test_TALKING_about_work_is_not_DOING_work(cmd):
    """🔴 The single negative case this file used to carry did not contain the literal
    at all, so the whole over-match class was untested. `_strip_literals` blanks quoted
    runs and `#` comments; the command-position anchor rejects the unquoted echo."""
    assert guard.is_work(bash(cmd)) is False


def test_the_positive_control_for_the_literal_stripper():
    """🔴 Nine `False`s are indistinguishable from an `is_work` wired to nothing. The
    same commands with the verb moved OUT of the quotes must all be True — and the
    stripper must not eat a real `git commit -m 'msg'`, whose message IS quoted."""
    assert guard.is_work(bash("git commit -m 'wire up the grep for git commit'")) \
        is True
    assert guard.is_work(bash("grep -rn foo scripts/ && git push")) is True
    assert guard._strip_literals("echo 'git commit' # git push") .strip() == "echo"


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
def test_an_unreachable_board_emits_a_notice_that_LETS_THE_TURN_END(home, capsys):
    """🔴 ASSERTED ON THE EMITTED JSON. The old version of this test asserted the
    internal kind string `"context"` and called that non-blocking — while the JSON it
    stood for forced a continuation. Only the writer's output can answer this."""
    seed()
    r = Reader(raises=guard.LiveReadError("connection refused"))
    verdict = guard.stop_decision(payload("Stop"), reader=r)
    text = verdict[1]
    assert "193" in text and "could not be reached" in text
    assert "connection refused" in text
    out = emitted(capsys, verdict)
    assert forces_a_continuation(out) is False, out
    assert out == {"systemMessage": text}


def test_an_unreachable_board_costs_ZERO_forced_continuations_at_ANY_rung(home,
                                                                          capsys):
    """🔴 Driven up the whole ladder and read as JSON at every rung. The first two
    rungs are where a MEASURED miss blocks, so an all-unknown session reaching them
    and STILL letting the turn end is the real assertion — and it is the one that was
    false before: three `additionalContext` emissions were three forced
    continuations."""
    seed()
    forced = []
    for _ in range(5):
        r = Reader(raises=guard.LiveReadError("boom"))
        v = guard.stop_decision(payload("Stop"), reader=r)
        forced.append(forces_a_continuation(emitted(capsys, v)))
    assert forced == [False, False, False, False, False]


def test_the_positive_control_for_that_zero(home, capsys):
    """🔴 `forces_a_continuation` returning False five times is indistinguishable from
    a predicate wired to nothing. The SAME predicate on a MEASURED miss must return
    True — and on the literal shape the old code emitted, which is the mutant this
    whole finding is about."""
    seed()
    r = Reader(result=task(comments=[]))
    v = guard.stop_decision(payload("Stop"), reader=r)
    assert forces_a_continuation(emitted(capsys, v)) is True
    assert forces_a_continuation(
        {"hookSpecificOutput": {"hookEventName": "Stop",
                                "additionalContext": "fyi"}}) is True


def test_a_reader_raising_something_unexpected_is_also_only_a_notice(home, capsys):
    seed()
    r = Reader(raises=RuntimeError("kaboom"))
    v = guard.stop_decision(payload("Stop"), reader=r)
    assert "kaboom" in v[1]
    assert forces_a_continuation(emitted(capsys, v)) is False


def test_an_unreadable_task_payload_is_a_notice_not_a_block(home, capsys):
    seed()
    r = Reader(result="this is not a task object")
    v = guard.stop_decision(payload("Stop"), reader=r)
    assert "UNVERIFIED" in v[1]
    assert forces_a_continuation(emitted(capsys, v)) is False


def test_an_unreachable_board_does_not_spend_the_BLOCK_budget(home, capsys):
    """🔴 THE TWO COUNTERS. A board down for the first three Stops used to exhaust the
    per-task ladder, so a genuinely missing write-back could never be blocked later in
    that session — the hook's whole purpose, defeated by an outage. The measured-miss
    counter is now separate: after three unmeasurable Stops the FOURTH, with the board
    back, must still block."""
    seed()
    for _ in range(3):
        guard.stop_decision(payload("Stop"),
                            reader=Reader(raises=guard.LiveReadError("boom")))
    r = Reader(result=task(comments=[]))
    v = guard.stop_decision(payload("Stop"), reader=r)
    out = emitted(capsys, v)
    assert out["decision"] == "block", out
    assert "task status 193 ready_for_review" in out["reason"]


def test_an_unmeasurable_board_goes_quiet_rather_than_notifying_forever(home,
                                                                        capsys):
    """The other side of that separation: its own counter is still a CAP, so an
    outage cannot produce one notice per Stop indefinitely. 3 notices, then silence —
    driven with a range of 6, a length neither MAX_FIRES (3) nor MAX_BLOCKS (2)."""
    seed()
    wrote = []
    for _ in range(6):
        v = guard.stop_decision(payload("Stop"),
                                reader=Reader(raises=guard.LiveReadError("boom")))
        wrote.append(emitted(capsys, v) is not None)
    assert wrote == [True, True, True, False, False, False]


# =========================================================================== #
# 9. THE ESCALATION LADDER
# =========================================================================== #
def test_the_ladder_constants_are_what_the_docstring_claims():
    assert guard.MAX_BLOCKS == 2
    assert guard.MAX_FIRES == 3
    assert guard.MAX_TASKS == 5


@pytest.mark.parametrize("fire,rung", [
    (1, "block"), (2, "block"), (3, "notice"), (4, "silent"), (9, "silent"),
])
def test_escalate_maps_each_fire_number_to_its_rung(fire, rung):
    assert guard.escalate(fire) == rung


def test_the_ladder_end_to_end_on_one_task(home, capsys):
    """🔴 Literal expected sequence, pinned from the ladder in the docstring — NOT
    derived from MAX_BLOCKS/MAX_FIRES, which is how a test comes to agree with a
    mutated implementation. Read as the EMITTED JSON, so "the third rung relents" is
    a claim about what the CLI receives rather than about a string this file chose."""
    seed()
    shapes = []
    for _ in range(5):
        r = Reader(result=task(comments=[]))
        out = emitted(capsys, guard.stop_decision(payload("Stop"), reader=r))
        shapes.append(None if out is None
                      else ("block" if "decision" in out else sorted(out)[0]))
    assert shapes == ["block", "block", "systemMessage", None, None]


def test_the_ladder_costs_EXACTLY_TWO_forced_continuations(home, capsys):
    """🔴 THE NUMBER IN THE DOCSTRING, MEASURED. It said two and delivered three: the
    third rung's `additionalContext` went into the CLI's `blockingErrors` array
    exactly like a block. Counted here off the emitted JSON, over a 5-Stop run whose
    length equals neither MAX_BLOCKS (2) nor MAX_FIRES (3)."""
    seed()
    forced = 0
    for _ in range(5):
        r = Reader(result=task(comments=[]))
        v = guard.stop_decision(payload("Stop"), reader=r)
        forced += bool(forces_a_continuation(emitted(capsys, v)))
    assert forced == 2


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


def test_a_SUBAGENTS_tool_call_never_arms_the_PARENT_session(home):
    """🔴 PROBE C, MEASURED AS A FALSE POSITIVE BEFORE THIS FIX. The bundle builds a
    PostToolUse payload as `{...Kf(i, void 0, o), hook_event_name:"PostToolUse", …}`,
    and `Kf`'s `session_id` is `t ?? kt()` with `t` undefined — so a tool call made
    inside a dispatched subagent arrives carrying the PARENT's session id, with only
    `agent_id` to tell them apart. The parent reads the card, the subagent does the
    editing, and the parent's Stop then blocked on work it never did."""
    guard.post_tool_use(bash("clawgatectl task get 193"))
    out = guard.post_tool_use(payload(tool_name="Edit", tool_input={"file_path": "/x"},
                                      agent_id="agent_012xyz"))
    assert out["work"] is False
    assert guard.work_after_read(state_dir()) is False
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")
    assert r.calls == []


def test_the_positive_control_for_the_agent_id_refusal(home):
    """The identical payload WITHOUT `agent_id` must arm it — otherwise the refusal
    above is green because nothing in this fixture could ever set the flag."""
    guard.post_tool_use(bash("clawgatectl task get 193"))
    out = guard.post_tool_use(payload(tool_name="Edit",
                                      tool_input={"file_path": "/x"}))
    assert out["work"] is True
    assert guard.work_after_read(state_dir()) is True


def test_a_subagents_READ_also_does_not_arm_the_parent(home):
    """The other half: the refusal is checked before the state dir is even computed,
    so a subagent surveying the board cannot create a ledger for its parent."""
    guard.post_tool_use(bash("clawgatectl task get 193", agent_id="agent_012xyz"))
    assert not os.path.exists(state_dir())


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


def test_the_relenting_rung_is_a_systemMessage_and_NOTHING_ELSE(capsys):
    """🔴 THE WHOLE OF FINDING 1, PINNED AS THE EXACT OBJECT. `systemMessage` is the
    only field in the CLI's Stop-hook output schema that reaches the operator without
    entering `blockingErrors`. Asserted as EQUALITY, not membership: an
    `additionalContext` key added alongside would restore the forced continuation
    while every "contains systemMessage" check stayed green."""
    guard.emit("notice", "fyi")
    out = json.loads(capsys.readouterr().out)
    assert out == {"systemMessage": "fyi"}


def test_emit_can_NEVER_produce_an_additionalContext_on_any_kind(capsys):
    """🔴 The negative half, swept over every kind the ladder can return plus two it
    cannot. `additionalContext` is not a channel this hook may use on Stop, and a
    future rung that reached for it would be a silent regression of finding 1."""
    for kind in ("block", "notice", "silent", "context", "whatever"):
        guard.emit(kind, "text-%s" % kind)
        raw = capsys.readouterr().out
        assert "additionalContext" not in raw, kind
        assert "hookSpecificOutput" not in raw, kind


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


# =========================================================================== #
# 16. THE FOUR MEASURED FALSE-POSITIVE PROBES, AND THE ESCAPE THAT ACTUALLY WORKS
#
# 🔴 The work flag is SESSION-wide. Nothing in a PostToolUse payload says which task
# an edit belongs to, so two of these four shapes STILL fire — and they are pinned
# here as behaviour rather than left to be rediscovered. What changed is that the
# escape is now a mechanism: one command, named in the block text with the session id
# already filled in, that clears the task for good. The old escape ("say so in one
# line and stop") was measured NOT to work — saying something changes no state, so
# the next Stop re-blocked with identical text.
# =========================================================================== #
def run_cli(args, home_dir):
    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    return subprocess.run([sys.executable, HOOK] + list(args), input="",
                          capture_output=True, text=True, timeout=30, env=env)


def test_PROBE_A_work_in_a_DIFFERENT_repo_still_fires_and_dismiss_ends_it(home):
    """🔴 PROBE A, and an honest one: this IS still a false positive. Read task 193,
    then edit and commit somewhere else entirely — the hook cannot tell, and blocks.
    What it must NOT do is keep blocking after the operator says the work was not for
    this card, which is exactly what "say so and stop" did."""
    guard.post_tool_use(bash("clawgatectl task get 193"))
    guard.post_tool_use(payload(tool_name="Edit",
                                tool_input={"file_path": "/other/repo/x.py"}))
    guard.post_tool_use(bash("git -C /other/repo commit -m 'unrelated'"))
    kind, text = guard.stop_decision(payload("Stop"),
                                     reader=Reader(result=task(comments=[])))
    assert kind == "block"
    # The escape is a COMMAND, and it carries this session's id already resolved —
    # a model cannot see its own hook payload, so a placeholder would be unrunnable.
    assert "--dismiss 193 --session %s" % SESSION in text
    assert "say so in one line and stop" not in text
    guard.dismiss(state_dir(), 193)
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")
    assert r.calls == []


def test_PROBE_B_writing_back_only_ONE_of_two_surveyed_cards(home):
    """🔴 PROBE B. Survey 193 and 194, work, write back 194 only. 194 goes quiet on
    its own — the live read sees its comment. 193 still fires, and the block text
    names ONLY 193, so the operator is told exactly which card to dismiss."""
    guard.post_tool_use(bash("clawgatectl task get 193"), now=READ_EPOCH)
    guard.post_tool_use(bash("clawgatectl task get 194"), now=READ_EPOCH)
    guard.post_tool_use(payload(tool_name="Edit", tool_input={"file_path": "/x"}),
                        now=WORK_EPOCH)

    def reader(task_id, timeout=None):
        if task_id == 194:
            return task(task_id=194,
                        comments=[comment(created="2026-08-15T13:00:00.000000Z")])
        return task(task_id=193, comments=[])

    kind, text = guard.stop_decision(payload("Stop"), reader=reader)
    assert kind == "block"
    assert "task status 193 ready_for_review" in text
    assert "task status 194" not in text
    assert "--dismiss 193 --session %s" % SESSION in text


def test_PROBE_D_a_command_that_merely_MENTIONS_a_commit_does_not_fire(home):
    """🔴 PROBE D. Read the card, then `echo 'remember to git commit later'`. That was
    measured to block; it is now not even work. Driven through `post_tool_use` rather
    than `is_work` so the writer is exercised, which is where a previous sweep found a
    forced-true mutant surviving every unit-level test in this file."""
    guard.post_tool_use(bash("clawgatectl task get 193"))
    guard.post_tool_use(bash("echo 'remember to git commit later'"))
    guard.post_tool_use(bash("grep -rn 'gh pr create' claude/skills/"))
    assert guard.work_after_read(state_dir()) is False
    r = Reader(result=task(comments=[]))
    assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")
    assert r.calls == []


def test_dismiss_removes_the_read_and_BOTH_counters(home):
    sd = seed()
    guard.bump_fires(sd, 193)
    guard.bump_fires(sd, 193, "unknown")
    removed = guard.dismiss(sd, 193)
    assert sorted(removed) == ["fires-193", "read-193", "unknown-193"]
    assert guard.tracked_ids(sd) == {}


def test_dismiss_is_PER_TASK_and_leaves_its_neighbours_alone(home):
    """🔴 A dismissal that swept the session would silence cards the operator never
    mentioned — the failure mode a blunt "clear everything" escape would have."""
    seed(task_id=193)
    sd = seed(task_id=194)
    guard.dismiss(sd, 193)
    assert sorted(guard.tracked_ids(sd)) == [194]
    r = Reader(result=task(comments=[]))
    kind, text = guard.stop_decision(payload("Stop"), reader=r)
    assert kind == "block"
    assert [c[0] for c in r.calls] == [194]
    assert "task status 194 ready_for_review" in text


def test_dismiss_on_an_absent_task_is_not_an_error(home):
    sd = seed(task_id=193)
    assert guard.dismiss(sd, 999) == []
    assert sorted(guard.tracked_ids(sd)) == [193]


def test_the_dismiss_command_from_the_block_text_RUNS_and_ENDS_the_nagging(home,
                                                                           tmp_path):
    """🔴 THE ESCAPE, EXERCISED AS A REAL SUBPROCESS FROM THE EXACT TEXT THE MODEL IS
    GIVEN. The command is extracted from the block reason rather than retyped here —
    a hand-written invocation would still pass if the text advertised a different
    flag, which is precisely how "say so and stop" survived being useless."""
    b = tmp_path / "dismissbin"
    b.mkdir()
    mockbin.write_exec(b / "clawgatectl",
                       "printf '%%s\\n' '%s'\n" % json.dumps(task(comments=[])))
    run_hook(bash("clawgatectl task get 193"), home, path_extra=b)
    run_hook(payload(tool_name="Edit", tool_input={"file_path": "/x"}), home,
             path_extra=b)
    first = run_hook(payload("Stop"), home, path_extra=b)
    reason = json.loads(first.stdout)["reason"]
    line = [ln.strip() for ln in reason.splitlines() if "--dismiss" in ln]
    assert len(line) == 1, reason
    args = line[0].split()[2:]            # drop `python3 <script>`
    assert args[0] == "--dismiss"
    p = run_cli(args, home)
    assert p.returncode == 0, p.stderr
    assert "dismissed task 193" in p.stdout
    # ...and the NEXT Stop is silent, which is the thing the old escape could not do.
    again = run_hook(payload("Stop"), home, path_extra=b)
    assert again.returncode == 0
    assert again.stdout == ""


def test_the_positive_control_for_that_dismissal(home, tmp_path):
    """Without the dismiss step the same sequence blocks a SECOND time — so the
    silence above is the mechanism working, not a session that had gone quiet anyway
    (the ladder still has a rung left at this point)."""
    b = tmp_path / "dismissbin2"
    b.mkdir()
    mockbin.write_exec(b / "clawgatectl",
                       "printf '%%s\\n' '%s'\n" % json.dumps(task(comments=[])))
    run_hook(bash("clawgatectl task get 193"), home, path_extra=b)
    run_hook(payload(tool_name="Edit", tool_input={"file_path": "/x"}), home,
             path_extra=b)
    run_hook(payload("Stop"), home, path_extra=b)
    second = run_hook(payload("Stop"), home, path_extra=b)
    assert json.loads(second.stdout)["decision"] == "block"


def test_the_cli_refuses_a_dismiss_without_a_session_rather_than_guessing(home):
    """🔴 There is no way for this process to learn the caller's session id, so a
    default would land the dismissal on some other session's ledger. It prints usage
    and touches nothing."""
    sd = seed()
    p = run_cli(["--dismiss", "193"], home)
    assert p.returncode == 0
    assert "usage:" in p.stdout
    assert sorted(guard.tracked_ids(sd)) == [193]


@pytest.mark.parametrize("args", [
    ["--dismiss", "not-a-number", "--session", SESSION],
    ["--dismiss", "193", "--session", ""],
])
def test_the_cli_survives_junk_arguments(home, args):
    sd = seed()
    p = run_cli(args, home)
    assert p.returncode == 0
    assert p.stderr == ""
    assert sorted(guard.tracked_ids(sd)) == [193]


def test_the_cli_mode_never_reads_stdin(home):
    """🔴 A model runs this from a Bash tool call, where stdin is a terminal or empty.
    A dismiss path that fell through to `json.load(sys.stdin)` would hang the turn it
    was supposed to release. Driven with stdin held OPEN and never written to."""
    seed()
    p = subprocess.Popen([sys.executable, HOOK, "--dismiss", "193",
                          "--session", SESSION],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True,
                         env={**os.environ, "HOME": str(home)})
    try:
        out, _ = p.communicate(timeout=20)   # no input written; the pipe stays open
    except subprocess.TimeoutExpired:
        p.kill()
        raise AssertionError("the CLI mode blocked on stdin")
    assert p.returncode == 0
    assert "dismissed task 193" in out


# =========================================================================== #
# 17. THE COMPARISON ANCHOR — the last WORK event, not the read
#
# 🔴 THIS IS THE FIX FOR A GUARD THAT WAS DISARMED BY THE RITUAL IT ENFORCES. The
# clawgate skill posts a "Starting" comment right after the read and BEFORE the work.
# Anchored on the read, that comment satisfied the check at pickup, so on every
# session that followed the ritual the hook could never observe a missing COMPLETION
# write-back — a forward yield of zero on exactly the compliant population.
# =========================================================================== #
def test_a_PRE_START_comment_no_longer_disarms_the_guard(home):
    """The measured shape: read at 12:00, "Starting" comment at 12:01, work at 12:30,
    Stop. The comment predates the work, so the completion write-back is still owed."""
    starting = comment(created="2026-08-15T12:01:00.000000Z", body="Starting")
    assert guard.writeback_state(task(comments=[starting]), READ_TS,
                                 work_ts=WORK_TS) == "missing"


def test_the_full_compliant_ritual_is_still_satisfied(home):
    """Starting -> work -> Done -> Stop. The Done comment is newer than the work, so
    the guard goes quiet — the behaviour that must NOT regress while fixing the one
    above."""
    starting = comment(created="2026-08-15T12:01:00.000000Z", body="Starting")
    done = comment(created="2026-08-15T13:00:00.000000Z", body="Done")
    assert guard.writeback_state(task(comments=[starting, done]), READ_TS,
                                 work_ts=WORK_TS) == "written"


def test_the_anchor_moves_with_the_work_and_NOT_with_the_read(home):
    """🔴 Three distinct instants, none of which can be produced by collapsing the
    other two: read 12:00, comment 12:45, work 12:30 vs work 13:30. The SAME comment
    must read as written against the earlier work and missing against the later one —
    a mutant that anchors on the read alone gives the same answer to both."""
    c = comment(created="2026-08-15T12:45:00.000000Z")
    t = task(comments=[c])
    assert guard.writeback_state(t, READ_TS, work_ts="2026-08-15T12:30:00Z") \
        == "written"
    assert guard.writeback_state(t, READ_TS, work_ts="2026-08-15T13:30:00Z") \
        == "missing"


def test_a_work_stamp_OLDER_than_the_read_does_not_LOOSEN_the_cutoff(home):
    """The anchor is the LATER of the two. A stale or clock-skewed work stamp must not
    be able to move the cutoff backwards and admit a comment from before the read."""
    old = comment(created="2026-08-15T11:00:00.000000Z")
    assert guard.writeback_state(task(comments=[old]), READ_TS,
                                 work_ts="2026-08-15T10:00:00Z") == "missing"


@pytest.mark.parametrize("bad", [None, "", "1", "garbage", 42])
def test_an_UNREADABLE_work_stamp_falls_back_to_the_read_anchor(home, bad):
    """Fail-QUIET, deliberately: a truncated write, or state left by the build that
    wrote the literal "1", must not invent a stricter cutoff out of an unreadable
    file. `"1"` is in here because it is exactly what the previous format stored."""
    c = comment(created="2026-08-15T13:00:00.000000Z")
    assert guard.writeback_state(task(comments=[c]), READ_TS, work_ts=bad) == "written"


def test_the_skew_allowance_absorbs_a_push_right_after_the_comment(home):
    """🔴 THE NOISE BOUND, MEASURED. Comment at 12:45:00, work at 12:46:00 — 60 s
    later, inside CLOCK_SKEW_ALLOWANCE_SECS — is still satisfied. 12:50:00, 300 s
    later, is not. Neither fixture interval (60 s / 300 s) can equal the constant
    (120 s), so this cannot pass by being it."""
    c = comment(created="2026-08-15T12:45:00.000000Z")
    t = task(comments=[c])
    assert guard.writeback_state(t, READ_TS, work_ts="2026-08-15T12:46:00Z") \
        == "written"
    assert guard.writeback_state(t, READ_TS, work_ts="2026-08-15T12:50:00Z") \
        == "missing"


def test_record_work_stores_the_MOST_RECENT_work_not_the_first(home):
    guard.post_tool_use(bash("clawgatectl task get 193"), now=READ_EPOCH)
    guard.post_tool_use(payload(tool_name="Edit", tool_input={"file_path": "/x"}),
                        now=WORK_EPOCH)
    guard.post_tool_use(payload(tool_name="Write", tool_input={"file_path": "/y"}),
                        now=READ_PLUS_1H)
    assert guard.last_work_ts(state_dir()) == "2026-08-15T13:00:00Z"


def test_MULTI_TURN_work_fires_once_per_turn_and_then_STOPS(home, capsys):
    """🔴 THE COST OF THE ANCHOR, BOUNDED AND MEASURED RATHER THAN DISCOVERED LATER.
    Work spanning several turns, with a Done comment each time that the NEXT turn's
    work then outdates, fires once per turn — until the per-task ladder is spent. Four
    turns: block, block, systemMessage, silence. Driven with 4, which is neither
    MAX_BLOCKS (2) nor MAX_FIRES (3)."""
    sd = seed(work=False)
    done_at = "2026-08-15T12:45:00.000000Z"
    shapes = []
    for i in range(4):
        # each turn: more work, landing well past the last comment + the skew allowance
        guard.record_work(sd, now=WORK_EPOCH + 3600 * (i + 1))
        r = Reader(result=task(comments=[comment(created=done_at)]))
        out = emitted(capsys, guard.stop_decision(payload("Stop"), reader=r))
        shapes.append(None if out is None
                      else ("block" if "decision" in out else sorted(out)[0]))
    assert shapes == ["block", "block", "systemMessage", None]


def test_MULTI_TURN_work_that_KEEPS_writing_back_never_fires_at_all(home):
    """The positive control for that noise: the same four turns, each with a comment
    NEWER than that turn's work, stay silent. So the firing above is the anchor
    doing its job, not an unconditional per-turn nag."""
    sd = seed(work=False)
    for i in range(4):
        worked = WORK_EPOCH + 3600 * (i + 1)
        guard.record_work(sd, now=worked)
        fresh = guard.now_iso(worked + 60)
        r = Reader(result=task(comments=[comment(created=fresh)]))
        assert guard.stop_decision(payload("Stop"), reader=r) == ("silent", "")


# =========================================================================== #
# 18. THE TWO HANG BOUNDS, EXERCISED AT THEIR DEFAULTS
#
# 🔴 The ladder tests inject `budget=3.0` and a fake clock, so for a while NOTHING
# executed these constants: `STOP_BUDGET_SECS 8.0 -> 600.0` and
# `PER_TASK_TIMEOUT_SECS 5.0 -> 300.0` both SURVIVED a mutation sweep, and the CLI's
# own hook timeout (600 000 ms) is the only other thing bounding a Stop.
# =========================================================================== #
def test_the_DEFAULT_stop_budget_is_what_cuts_the_reads_off(home):
    """Clock ticks 0.0 / 7.9 / 8.1 bracket STOP_BUDGET_SECS (8.0) from both sides and
    equal neither it nor PER_TASK_TIMEOUT_SECS (5.0). At 8.0 exactly one task is read
    with 0.1 s left; at 600.0 all five are read with the full 5.0 ceiling; at anything
    below 7.9 none is read at all."""
    for tid in (11, 12, 13, 14, 15):
        seed(task_id=tid)
    ticks = iter([0.0, 7.9, 8.1, 8.1, 8.1, 8.1])
    r = Reader(result=task(comments=[]))
    guard.stop_decision(payload("Stop"), reader=r, clock=lambda: next(ticks))
    assert [c[0] for c in r.calls] == [11]
    assert r.calls[0][1] == pytest.approx(0.1)


def test_a_read_is_NOT_started_with_exactly_zero_budget_left(home):
    """🔴 THE BOUNDARY ITSELF. `remaining <= 0` vs `remaining < 0` differ at exactly
    one point, and every other budget test sits off it — so flipping the comparison
    SURVIVED a sweep. At `remaining == 0.0` the loop must break: admitting the read
    would hand the client a `timeout=0.0`, which is a spawn that can only fail."""
    for tid in (11, 12):
        seed(task_id=tid)
    ticks = iter([0.0, 8.0, 8.0, 8.0])
    r = Reader(result=task(comments=[]))
    guard.stop_decision(payload("Stop"), reader=r, clock=lambda: next(ticks))
    assert r.calls == []


def test_the_positive_control_for_that_boundary(home):
    """One microsecond of budget on the other side of it, and the read happens."""
    for tid in (11, 12):
        seed(task_id=tid)
    ticks = iter([0.0, 7.999999, 8.0, 8.0, 8.0])
    r = Reader(result=task(comments=[]))
    guard.stop_decision(payload("Stop"), reader=r, clock=lambda: next(ticks))
    assert [c[0] for c in r.calls] == [11]


def test_the_DEFAULT_per_task_timeout_is_the_ceiling_on_one_read(home):
    """With the whole default budget available the ceiling is what bounds a single
    read — 5.0, not 8.0. Paired with the 3.0 case below, which MOVES the number, so a
    mutant that hardcodes the ceiling cannot survive both."""
    seed(task_id=11)
    r = Reader(result=task(comments=[]))
    guard.stop_decision(payload("Stop"), reader=r, clock=lambda: 0.0)
    assert r.calls[0][1] == 5.0


def test_the_two_hang_bounds_are_the_numbers_the_docstring_states():
    """🔴 Pinned as a PAIR with the derived worst case, because the file used to
    advertise 8.0 as the bound while `_via_curl` waits `timeout + 2` on a read that
    the budget check admits before it starts: 8.0 + 5.0 + 2 = 15.0 s."""
    assert guard.STOP_BUDGET_SECS == 8.0
    assert guard.PER_TASK_TIMEOUT_SECS == 5.0
    assert guard.CURL_KILL_MARGIN_SECS == 2
    worst = (guard.STOP_BUDGET_SECS + guard.PER_TASK_TIMEOUT_SECS
             + guard.CURL_KILL_MARGIN_SECS)
    assert worst == 15.0


def test_the_source_states_the_TRUE_hang_bound_not_the_budget_alone():
    """The comment above the constants is a claim like any other. It said 8.0 was the
    bound; the arithmetic says 15.0."""
    src = open(HOOK).read()
    assert "STOP_BUDGET_SECS + PER_TASK_TIMEOUT_SECS + 2  =  15.0 s" in src


class _FakeProc:
    returncode = 0
    stdout = json.dumps({"id": 194, "status": "open", "comments": []})
    stderr = ""


def test_the_curl_wait_OUTLIVES_curls_own_max_time(tmp_path, monkeypatch):
    """🔴 THE `+ CURL_KILL_MARGIN_SECS`, PINNED AS TWO DIFFERENT NUMBERS. Deleting it
    survived a sweep: every test drove curl through a stub that returned instantly, so
    nothing ever compared the two. `max-time = 5` inside the config and `timeout=7` on
    the wait cannot both come from one literal."""
    seen = {}

    class _Fake:
        TimeoutExpired = subprocess.TimeoutExpired

        @staticmethod
        def run(argv, **kw):
            seen.update(kw)
            seen["argv"] = argv
            return _FakeProc()

    monkeypatch.setattr(guard, "_sp", lambda: _Fake)
    envf = tmp_path / "clawgate.env"
    envf.write_text("CLAWGATE_API_URL=http://board.invalid:1\n"
                    "CLAWGATE_HOOK_TOKEN=t\n")
    guard._via_curl(194, 5, env_path=str(envf))
    assert seen["timeout"] == 7
    cfg_lines = [ln.strip() for ln in seen["input"].splitlines()]
    assert "max-time = 5" in cfg_lines
    # ...and the two options that make a failure LOOK like one: without `fail` a 404
    # body is parsed as a task, without `silent` curl's progress meter lands in stdout.
    assert "fail" in cfg_lines
    assert "silent" in cfg_lines
    assert 'url = "http://board.invalid:1/api/tasks/194"' in cfg_lines


def test_the_positive_control_for_that_pair(tmp_path, monkeypatch):
    """A different timeout must move BOTH numbers — otherwise the assertion above is
    satisfied by two constants that merely happen to differ by 2."""
    seen = {}

    class _Fake:
        TimeoutExpired = subprocess.TimeoutExpired

        @staticmethod
        def run(argv, **kw):
            seen.update(kw)
            return _FakeProc()

    monkeypatch.setattr(guard, "_sp", lambda: _Fake)
    envf = tmp_path / "clawgate.env"
    envf.write_text("CLAWGATE_API_URL=http://board.invalid:1\n"
                    "CLAWGATE_HOOK_TOKEN=t\n")
    guard._via_curl(194, 11, env_path=str(envf))
    assert seen["timeout"] == 13
    assert "max-time = 11" in [ln.strip() for ln in seen["input"].splitlines()]


# =========================================================================== #
# 19. THE SMALL SURFACES THAT HAD NO COVERAGE AT ALL
# =========================================================================== #
@pytest.mark.parametrize("raw,want", [
    ("plain-session_1.2", "plain-session_1.2"),
    ("../../etc/passwd", ".._.._etc_passwd"),
    ("a/b c;d$(x)", "a_b_c_d__x_"),
    ("", ""),
])
def test_sanitize_replaces_every_unsafe_character(raw, want):
    """🔴 This is the only thing standing between an attacker-shaped `session_id` and
    a path join. It had ZERO coverage: both "return `str(part)` unchanged" and
    dropping the `[:120]` survived a sweep."""
    assert guard._sanitize(raw) == want


def test_sanitize_truncates_at_120_characters():
    """Driven with 500 and 119 — neither can equal the 120 it pins."""
    assert len(guard._sanitize("x" * 500)) == 120
    assert len(guard._sanitize("y" * 119)) == 119


def test_sanitize_coerces_a_non_string():
    assert guard._sanitize(193) == "193"


@pytest.mark.parametrize("session", [None, 42, {"x": 1}, ["a"], ""])
def test_a_session_id_that_is_not_a_usable_string_has_no_state_dir(session):
    """🔴 The `isinstance(session, str)` bail survived removal: without it a dict id
    is `str()`-ed into a path and the hook silently keeps state under a key no other
    call can reproduce. Asserted directly rather than through main()'s empty stdout,
    which a mutant produces just as happily."""
    assert guard._state_dir({"session_id": session}) is None


def test_the_positive_control_for_that_bail():
    sd = guard._state_dir({"session_id": "real-session"})
    assert sd is not None and sd.endswith("real-session")


@pytest.mark.parametrize("raw,want", [
    ("boom\r\nmore", "boom more"),
    ("  spaced   out  ", "spaced out"),
    ("\x1b[31mred\x1b[0m", "\x1b[31mred\x1b[0m"),   # not a newline: length-capped only
    (None, ""),
])
def test_scrub_collapses_third_party_stderr(raw, want):
    assert guard._scrub(raw) == want


def test_scrub_truncates_and_a_failing_client_cannot_flood_the_transcript(tmp_path,
                                                                          monkeypatch):
    """🔴 `proc.stderr` is an unfiltered pipe out of a binary this hook does not own,
    spliced into text an operator reads. Driven with 400 bytes of newline-separated
    junk: it must come back on ONE line and at most 120 characters."""
    b = _bin(tmp_path)
    mockbin.write_exec(b / "clawgatectl",
                       "echo 'l1' >&2\necho 'l2' >&2\n"
                       + "".join("echo '%s' >&2\n" % ("x" * 40) for _ in range(10))
                       + "exit 9\n")
    _isolated_path(monkeypatch, b)
    with pytest.raises(guard.LiveReadError) as e:
        guard.live_task(197, timeout=5, env_path=str(tmp_path / "nope.env"))
    # The surfacing error is the SECOND client's, carrying the first's message
    # inside `(first client: …)` — so the scrubbed text is bounded on both sides
    # rather than running to the end of the string.
    msg = str(e.value)
    assert "clawgatectl rc=9" in msg
    tail = msg.split("clawgatectl rc=9 ", 1)[1].rsplit(")", 1)[0]
    assert "\n" not in tail and "\r" not in tail
    assert len(tail) == 120, tail
    assert tail.startswith("l1 l2 ")
    assert msg.count("\n") == 0
