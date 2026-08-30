#!/usr/bin/env python3
"""Tests for handoff-write-guard.py — the hook that makes the handoff write
non-optional for a session that resumed one.

WHAT THIS FILE IS FOR

  1. 🔴 BOTH DIRECTIONS OF THE ARMING TRIGGER. The NON-matches are as load-bearing as
     the matches: this hook can BLOCK a turn, and arming it off `ls claudedocs/`, a
     grep, a non-handoff `.md`, or a path that resolves nowhere would put a block in
     front of a session that never resumed anything. Every one is a separate case.

  2. 🔴 THE FALSE-POSITIVE KILLER, TESTED AS AN ABSENCE OF ANY STATE CHANGE. A session
     that reads a handoff, reconciles it, reports "nothing moved" and stops must never
     fire — the measurement counted zero legitimate declines, so that path is already
     handled by the skill and this guard must stay out of it. Asserted not just by the
     verdict but by the fire counter NEVER BEING CREATED, so a mutation that reorders
     the work gate below the ladder is visible even if it happens to end up silent.

  3. 🔴 THE LADDER IS DRIVEN WITH LITERALS THE CONSTANTS CANNOT EQUAL. This repo has
     been bitten repeatedly by a fixture whose value equals the constant it tests, so
     `MAX_BLOCKS = 2` / `MAX_FIRES = 3` / `MAX_DOCS = 3` are never checked by something
     that produces 2 or 3 by construction: counters are seeded at 0 and at 9, the doc
     cap is driven with 5 docs, and the decision is watched to MOVE.

  4. 🔴 EVERY "CANNOT MEASURE" PATH IS A NOTICE, NEVER A BLOCK — the empty-result trap
     in RULES.md: a hook that goes silent when it cannot measure reports the same
     observable as one that measured a clean session.

  5. 🔴 THE NON-BLOCKING RUNG IS ASSERTED ON THE OBSERVABLE, NOT ON A KIND STRING.
     `stop_decision` returning "notice" is a SPELLING of non-blocking; the CLI decides
     from the emitted JSON, and `hookSpecificOutput.additionalContext` — which reads as
     non-blocking — is pushed into the same `blockingErrors` array as a block. Every
     such assertion runs the verdict through the real `guard.emit` and asks
     `forces_a_continuation(<the emitted JSON>)`.

  6. 🔴 THE DISMISSAL IS VERIFIED BY RE-READING THE DOC AFTERWARDS. The precedent's
     `--dismiss` shipped broken through three audit rounds because every test drove it
     and then asserted silence, and none of them re-read the thing afterwards — which
     is the one act that re-armed the guard, and also the natural way a human confirms
     a dismissal took. That exact sequence is a case here.

  7. THE HOT PATH. PostToolUse fires after every tool call, so the fast path is
     asserted by COUNTING filesystem calls, not by trusting a comment.

  8. 🔴 THE ANTI-DRIFT LEDGER. The work-detection patterns are a declared verbatim copy
     of clawgate-writeback-guard.py's. `test_the_work_detection_is_byte_identical_to_
     the_precedent` fails when EITHER copy moves, which is the failure mode "one rule,
     one place" is actually about.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(HERE).resolve().parents[2]
HOOK = os.path.abspath(os.path.join(HERE, os.pardir, "handoff-write-guard.py"))
PRECEDENT = os.path.abspath(os.path.join(HERE, os.pardir,
                                         "clawgate-writeback-guard.py"))
HOME_NIX = ROOT / "nix" / "home.nix"
REGISTRAR = ROOT / "scripts" / "claude-hooks" / "register-nudge-hook.py"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


guard = _load("handoff_write_guard_undertest", HOOK)

SESSION = "sess-handoff-write-1"
DOC = "handoff-skill-chain-usage-audit.md"
# Fixed instants, pinned as LITERALS rather than computed here, and all four DISTINCT
# so no assertion can pass by two anchors collapsing onto one value.
READ_TS = "2026-08-20T12:00:00.000000Z"
READ_EPOCH = 1787227200.0
BEFORE_READ_EPOCH = 1787223600.0        # 11:00Z — an hour before the read
WORK_EPOCH = 1787229000.0               # 12:30Z
AFTER_READ_EPOCH = 1787230800.0         # 13:00Z


def forces_a_continuation(out):
    """Would this hook output make the CLI re-query the model instead of ending the
    turn? Transcribed from claude-code 2.1.220's `bin/.claude-wrapped`, function
    `Ycd`, which pushes BOTH shapes into ONE `blockingErrors` array:

        if (F.blockingError)      { … E.push(G); … }
        if (F.additionalContexts) { … E.push(j); … }
        if (E.length > 0) return { blockingErrors: E, preventContinuation: !1 };

    `systemMessage` is absent from that path on purpose: it is yielded as a
    `hook_system_message` MESSAGE and never reaches `E`. The full bundle reads and both
    controls live in clawgate-writeback-guard.py's module docstring, which is the one
    place they are recorded.
    """
    if not isinstance(out, dict):
        return False
    if out.get("decision") == "block":
        return True
    hso = out.get("hookSpecificOutput")
    return bool(isinstance(hso, dict) and hso.get("additionalContext"))


def emitted(capsys, verdict):
    """Run one `(kind, text)` verdict through the REAL writer and return the JSON that
    reached stdout (None when it wrote nothing)."""
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


@pytest.fixture()
def repo(tmp_path):
    """A repo with a real `claudedocs/` dir. The DIRECTORY is what arming requires."""
    d = tmp_path / "repo" / "claudedocs"
    d.mkdir(parents=True)
    return d.parent


def payload(event="PostToolUse", session_id=SESSION, cwd=None, **kw):
    base = {"hook_event_name": event, "session_id": session_id,
            "transcript_path": "/home/zach/.claude/projects/p/a.jsonl",
            "cwd": cwd or "/home/zach/workspace/devrc"}
    base.update(kw)
    return base


def bash(cmd, **kw):
    return payload(tool_name="Bash", tool_input={"command": cmd}, **kw)


def read_tool(path, **kw):
    return payload(tool_name="Read", tool_input={"file_path": path}, **kw)


def state_dir(session=SESSION):
    return guard._state_dir({"session_id": session})


def seed(doc, ts=READ_TS, work=True, session=SESSION):
    """Put a session into the state the Stop gate reads: doc read, work done.

    🔴 The work stamp is a FIXED instant, never `time.time()`. A "now" here would sit
    in the real present and make every fixture retroactively stale — a whole file of
    tests that pass or fail by the calendar.
    """
    sd = state_dir(session)
    os.makedirs(sd, exist_ok=True)
    with open(guard._read_path(sd, guard.doc_key(doc)), "w") as fh:
        json.dump({"doc": str(doc), "first_read_ts": ts}, fh)
    if work:
        guard.record_work(sd, now=WORK_EPOCH)
    return sd


def run_hook(payload_dict, argv=(), env=None):
    """The hook as a REAL process — the only way to make a claim about its exit code
    and about what it does or does not write to stdout/stderr."""
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, HOOK, *argv],
        input="" if payload_dict is None else json.dumps(payload_dict),
        capture_output=True, text=True, env=e, timeout=60)


# --------------------------------------------------------------------------- #
# 1. ARMING — the matches
# --------------------------------------------------------------------------- #
def test_the_Read_tool_on_an_absolute_handoff_path_arms(home, repo):
    doc = str(repo / "claudedocs" / DOC)
    assert guard.handoff_read_docs(read_tool(doc)) == [doc]


def test_a_git_show_off_a_ref_arms_and_resolves_through_the_dash_C(home, repo):
    """🔴 THE SHAPE THIS ARC'S OWN SESSIONS USED. A handoff that lives only on an
    unmerged branch is read as `git -C <repo> show <ref>:claudedocs/<doc>`. Two things
    are asserted at once: the `:` does not get swallowed into the path (so the match
    starts at `claudedocs/`), and the base is taken from `-C` rather than from `cwd`,
    which in a dispatch-hub session names a DIFFERENT repo entirely."""
    cmd = ("git -C %s show origin/zach/skill-chain-usage-audit:claudedocs/%s"
           % (repo, DOC))
    got = guard.handoff_read_docs(bash(cmd, cwd="/nowhere/else"))
    assert got == [str(repo / "claudedocs" / DOC)]


def test_a_quoted_cat_arms_because_the_bash_arm_does_not_strip_quotes(home, repo):
    """🔴 THE DELIBERATE ASYMMETRY WITH `is_work`. Quote-stripping exists to stop a
    command that MENTIONS a work verb counting as one. Applying it here would lose the
    ordinary `cat "$D/claudedocs/handoff-x.md"` — a real blind spot traded for a benign
    over-match. This case is what pins the asymmetry."""
    cmd = 'cat "%s/claudedocs/%s"' % (repo, DOC)
    assert guard.handoff_read_docs(bash(cmd)) == [str(repo / "claudedocs" / DOC)]


def test_the_second_basename_shape_arms(home, repo):
    """`/resume` resolves `claudedocs/handoff-*.md` FIRST and `claudedocs/*HANDOFF*.md`
    second, and the second is a real repo's real handoff (civitai-manager's
    SESSION-HANDOFF.md). A skill that resolves two shapes and a guard that arms on one
    is silent for whichever repo uses the other."""
    doc = str(repo / "claudedocs" / "SESSION-HANDOFF.md")
    assert guard.handoff_read_docs(read_tool(doc)) == [doc]


def test_a_relative_path_resolves_against_cwd(home, repo):
    cmd = "head -50 claudedocs/%s" % DOC
    assert guard.handoff_read_docs(bash(cmd, cwd=str(repo))) == [
        str(repo / "claudedocs" / DOC)]


# --------------------------------------------------------------------------- #
# 1b. ARMING — the NON-matches, which are the load-bearing half
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cmd", [
    "ls claudedocs/",                              # a survey, not a read of one doc
    "ls -t claudedocs/handoff-*.md | head",        # a glob is not a specific doc
    "grep -rn handoff claudedocs/",                # searching, not reading
    "cat claudedocs/notes.md",                     # not a handoff basename
    "cat docs/handoff-format.md",                  # handoff-shaped, wrong directory
    "# cat claudedocs/handoff-x.md",               # the whole line is a comment
    "python3 scripts/lib/handoff_doc.py --repo . --topic x",        # a WRITE, not a read
])
def test_these_bash_commands_do_NOT_arm(home, repo, cmd):
    """🔴 A guard that can BLOCK must be shown NOT to fire, one shape per case."""
    assert guard.handoff_read_docs(bash(cmd, cwd=str(repo))) == []


def test_a_path_after_a_hash_is_a_comment_not_a_read(home, repo):
    """🔴 THE DISCRIMINATOR FOR COMMENT-STRIPPING, built so that dropping the strip
    changes the RESULT rather than merely a count. One command names TWO handoff docs
    — one being read, one mentioned after a `#` — so a mutant that skips the strip
    returns two entries and books a document the session never opened."""
    cmd = "cat claudedocs/handoff-x.md  # and see claudedocs/handoff-y.md"
    got = guard.handoff_read_docs(bash(cmd, cwd=str(repo)))
    assert got == [str(repo / "claudedocs" / "handoff-x.md")]


def test_a_path_whose_directory_does_not_exist_does_NOT_arm(home, tmp_path):
    """🔴 THE QUIET DIRECTION. Arming requires the resolved `claudedocs/` to be real, so
    a dispatch-hub session that names a doc in a repo this host does not have arms
    nothing, rather than booking a document nobody can measure."""
    cmd = "cat %s/nosuchrepo/claudedocs/%s" % (tmp_path, DOC)
    assert guard.handoff_read_docs(bash(cmd)) == []


def test_a_WRITE_of_a_handoff_doc_does_NOT_arm(home, repo):
    """🔴 ONLY READS ARM. A Write/Edit of a handoff doc is what SATISFIES this guard, so
    admitting it here would let one tool call arm and satisfy in the same instant."""
    doc = str(repo / "claudedocs" / DOC)
    for tool in ("Write", "Edit"):
        p = payload(tool_name=tool, tool_input={"file_path": doc})
        assert guard.handoff_read_docs(p) == []
        # …and the positive half in the same breath: it SATISFIES instead.
        assert guard.is_handoff_write(p) is True


def test_a_subagents_read_does_NOT_arm_the_parent(home, repo):
    """🔴 THE ASYMMETRIC RULE, READ HALF. A subagent's tool call wears the PARENT's
    session_id; accepting its read armed the precedent's parent off a doc only the
    subagent touched — measured as a false positive."""
    doc = str(repo / "claudedocs" / DOC)
    out = guard.post_tool_use(read_tool(doc, agent_id="agt-1"))
    assert out["fast_path"] is True and out["recorded"] == []
    assert not os.path.exists(state_dir())


def test_a_subagents_WORK_does_count_once_the_parent_has_read(home, repo):
    """🔴 THE ASYMMETRIC RULE, WORK HALF — the direction the precedent got wrong, which
    deleted its yield on the exact incident it existed for. The parent dispatched the
    subagent; the parent owns the record."""
    sd = seed(str(repo / "claudedocs" / DOC), work=False)
    out = guard.post_tool_use(
        payload(tool_name="Edit", tool_input={"file_path": "/x/y.py"},
                agent_id="agt-1"))
    assert out["work"] is True
    assert guard.work_happened(sd)


def test_a_subagents_handoff_WRITE_also_counts(home, repo):
    """The same asymmetry, on the satisfaction side rather than the work side — and it
    has to hold, or dispatching the write to a subagent would leave the parent blocked
    for a handoff that is on disk. Same justification: the parent dispatched it."""
    sd = seed(str(repo / "claudedocs" / DOC))
    other = str(repo / "claudedocs" / "handoff-elsewhere.md")
    guard.post_tool_use(
        payload(tool_name="Write", tool_input={"file_path": other}, agent_id="agt-1"),
        now=AFTER_READ_EPOCH)
    assert guard._stamp(sd, guard.STATE_WROTE) is not None
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")


# --------------------------------------------------------------------------- #
# 2. THE FALSE-POSITIVE KILLER
# --------------------------------------------------------------------------- #
def test_read_with_no_work_is_silent_and_bumps_NOTHING(home, repo):
    """🔴 Asserted on the STATE as well as the verdict. A verdict-only assertion cannot
    see a mutation that reorders the work gate below the ladder — it would still return
    silent for this input while having spent a fire."""
    sd = seed(str(repo / "claudedocs" / DOC), work=False)
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")
    assert [n for n in os.listdir(sd) if n.startswith("fires-")] == []


def test_a_session_that_never_read_a_handoff_is_silent(home):
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")


# --------------------------------------------------------------------------- #
# 3. THE THREE SATISFACTION ROUTES — unioned, so each is checked ALONE
# --------------------------------------------------------------------------- #
def test_a_handoff_doc_py_run_satisfies(home, repo):
    sd = seed(str(repo / "claudedocs" / DOC))
    guard.record_wrote(sd, now=AFTER_READ_EPOCH)
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")


def test_a_write_to_a_DIFFERENT_handoff_doc_satisfies(home, repo):
    """🔴 TOPIC DRIFT COUNTS AS RECORDED, AND THIS IS THAT DECISION MADE MECHANICAL.
    25 of the 253 measured sessions resumed doc X and wrote doc Y; the measurement
    scored every one RECORDED, because the work IS on disk. A guard keyed to the path
    would block 25 sessions for doing exactly the right thing."""
    sd = seed(str(repo / "claudedocs" / DOC))
    other = str(repo / "claudedocs" / "handoff-clawgatectl-agent-delivery.md")
    assert guard.is_handoff_write(
        payload(tool_name="Write", tool_input={"file_path": other})) is True
    guard.post_tool_use(
        payload(tool_name="Write", tool_input={"file_path": other}),
        now=AFTER_READ_EPOCH)
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")
    assert sd  # the state dir is the one the verdict was read from


def test_the_docs_own_mtime_satisfies_even_with_no_observation(home, repo):
    """The route that catches a write this process never SAW — another tool, another
    process, a `/handoff` in a session that reloaded. Without it the guard would be a
    record of what one hook happened to observe rather than a measurement."""
    doc = repo / "claudedocs" / DOC
    doc.write_text("x")
    os.utime(doc, (AFTER_READ_EPOCH, AFTER_READ_EPOCH))
    seed(str(doc))
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")


def test_an_mtime_BEFORE_the_read_does_not_satisfy(home, repo):
    """The negative control for the route above: a doc that exists and is merely OLD
    must not read as written. Without this, `getmtime` returning any number at all
    would satisfy and the whole route would be inert-but-green."""
    doc = repo / "claudedocs" / DOC
    doc.write_text("x")
    os.utime(doc, (BEFORE_READ_EPOCH, BEFORE_READ_EPOCH))
    seed(str(doc))
    kind, text = guard.stop_decision(payload(event="Stop"))
    assert kind == "block" and DOC in text


def test_a_wrote_stamp_BEFORE_the_read_does_not_satisfy(home, repo):
    """The same boundary on the observation route: a handoff written for a PREVIOUS
    doc, before this one was resumed, is not this doc's record."""
    sd = seed(str(repo / "claudedocs" / DOC))
    guard.record_wrote(sd, now=BEFORE_READ_EPOCH)
    assert guard.stop_decision(payload(event="Stop"))[0] == "block"


def test_a_write_in_the_SAME_tool_call_as_the_read_satisfies(home, repo):
    """🔴 `>=`, not `>`. One Bash call can be both a read and a write
    (`git show …:claudedocs/x.md && python3 …/handoff_doc.py …`) and `post_tool_use`
    stamps both from the SAME `now`, so an equal pair is a write ON that read."""
    doc = str(repo / "claudedocs" / DOC)
    sd = seed(doc, ts=guard.now_iso(READ_EPOCH))
    guard.record_wrote(sd, now=READ_EPOCH)
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")


def test_the_missing_case_blocks_and_names_the_doc_and_the_escape(home, repo):
    seed(str(repo / "claudedocs" / DOC))
    kind, text = guard.stop_decision(payload(event="Stop"))
    assert kind == "block"
    assert DOC in text
    assert "/handoff" in text
    assert guard.HANDOFF_TOOL in text
    # 🔴 The escape must arrive with the session id ALREADY FILLED IN. The precedent
    # measured that an escape the caller has to look something up for is not used.
    assert ("--dismiss %s --session %s" % (guard.doc_key(DOC), SESSION)) in text


# --------------------------------------------------------------------------- #
# 4. THE LADDER
# --------------------------------------------------------------------------- #
def test_the_ladder_moves_block_block_notice_silent(home, repo, capsys):
    """🔴 DRIVEN FROM 0 WITH THE DECISION WATCHED TO MOVE, and every rung's
    non-blockingness asserted on the EMITTED JSON rather than on the kind string.
    Neither the seed (0) nor the assertions mention 2 or 3, so a fixture cannot pass by
    equalling the constant it is testing."""
    seed(str(repo / "claudedocs" / DOC))
    stop = payload(event="Stop")
    rungs = []
    for _ in range(5):
        verdict = guard.stop_decision(stop)
        rungs.append(verdict[0])
        out = emitted(capsys, verdict)
        if verdict[0] == "block":
            assert forces_a_continuation(out) is True
        else:
            assert forces_a_continuation(out) is False
    assert rungs == ["block", "block", "notice", "silent", "silent"]


def test_a_counter_seeded_high_is_already_silent(home, repo):
    """Seeded at NINE — a literal neither MAX_BLOCKS nor MAX_FIRES can equal — so the
    ladder's ceiling is checked without the fixture producing the constant."""
    sd = seed(str(repo / "claudedocs" / DOC))
    with open(guard._fires_path(sd, guard.doc_key(DOC)), "w") as fh:
        fh.write("9")
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")


def test_escalate_is_a_pure_function_of_the_fire_number():
    assert [guard.escalate(n) for n in (1, 2, 3, 4, 9)] == [
        "block", "block", "notice", "silent", "silent"]


# --------------------------------------------------------------------------- #
# 5. CANNOT-MEASURE IS A NOTICE, NEVER A BLOCK
# --------------------------------------------------------------------------- #
def test_an_unreadable_read_stamp_is_a_notice_and_never_blocks(home, repo, capsys):
    """🔴 THE EMPTY-RESULT TRAP. A truncated read stamp leaves no anchor; going silent
    would report the same observable as a session that wrote its handoff. It reports —
    and it may not spend the BLOCK budget, so it is driven up its own ladder and every
    rung is checked against the emitted JSON."""
    sd = seed(str(repo / "claudedocs" / DOC), ts="not-a-timestamp")
    stop = payload(event="Stop")
    kinds = []
    for _ in range(4):
        verdict = guard.stop_decision(stop)
        kinds.append(verdict[0])
        assert forces_a_continuation(emitted(capsys, verdict)) is False
    assert kinds == ["notice", "notice", "notice", "silent"]
    # …and on its OWN counter, so an unmeasurable early Stop cannot spend the budget a
    # measured miss needs later in the same session.
    assert os.path.exists(guard._fires_path(sd, guard.doc_key(DOC), "unknown"))
    assert not os.path.exists(guard._fires_path(sd, guard.doc_key(DOC)))


def test_a_notice_alone_never_forces_a_continuation(home, repo, capsys):
    verdict = ("notice", "hello")
    assert forces_a_continuation(emitted(capsys, verdict)) is False


def test_silent_writes_nothing_at_all(home, capsys):
    assert emitted(capsys, ("silent", "anything")) is None


# --------------------------------------------------------------------------- #
# 6. Stop-event scoping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kw", [
    {"event": "SubagentStop"},
    {"event": "Stop", "agent_id": "agt-1"},
    {"event": "PostToolUse"},
])
def test_these_stop_shaped_payloads_are_refused(home, repo, kw):
    """A subagent's turn never reaches the operator, so it owes them nothing — and a
    non-Stop event is simply not this function's."""
    seed(str(repo / "claudedocs" / DOC))
    assert guard.stop_decision(payload(**kw)) == ("silent", "")


# --------------------------------------------------------------------------- #
# 7. MAX_DOCS, enforced at the WRITER
# --------------------------------------------------------------------------- #
def test_at_most_three_docs_are_tracked_per_session(home, repo):
    """Driven with FIVE docs — a literal MAX_DOCS cannot equal — and asserted on
    `tracked_docs`, i.e. on what the Stop gate can actually see."""
    sd = state_dir()
    for i in range(5):
        guard.record_read(sd, str(repo / "claudedocs" / ("handoff-%d.md" % i)))
    assert len(guard.tracked_docs(sd)) == guard.MAX_DOCS
    assert guard.MAX_DOCS == 3


def test_the_key_is_the_basename_so_one_doc_read_three_ways_books_one_slot(home, repo):
    """🔴 Keying on the SPELLING would book three entries for one document, spend every
    tracking slot, and emit three blocks naming the same file.

    🔴 THE THREE SPELLINGS ARE PAIRWISE DISTINCT AS STRINGS, AND THAT IS THE WHOLE
    TEST. A first version used three spellings that normalise to the same string, so
    the fixture could only ever produce one value and a mutant keying on the FULL PATH
    survived a green assertion — the exact "fixture can only produce the constant's own
    value" shape RULES.md names. These three are the ones a real session produces:
    the base clone's copy, the throwaway worktree's copy that `/handoff` actually
    writes, and a `..`-bearing spelling out of a relative resolution.
    """
    sd = state_dir()
    spellings = [
        str(repo / "claudedocs" / DOC),
        "/tmp/wt-handoff-1234/claudedocs/" + DOC,
        str(repo / "claudedocs" / "sub" / ".." / DOC),
    ]
    assert len(set(spellings)) == 3          # the fixture CAN move; the key must not
    for spelling in spellings:
        guard.record_read(sd, spelling)
    assert len(guard.tracked_docs(sd)) == 1
    assert list(guard.tracked_docs(sd)) == [guard.doc_key(DOC)]


def test_a_re_read_never_moves_the_first_read_timestamp(home, repo):
    sd = state_dir()
    doc = str(repo / "claudedocs" / DOC)
    guard.record_read(sd, doc, now=READ_EPOCH)
    assert guard.record_read(sd, doc, now=AFTER_READ_EPOCH) is False
    assert guard.tracked_docs(sd)[guard.doc_key(DOC)]["first_read_ts"] == \
        guard.now_iso(READ_EPOCH)


# --------------------------------------------------------------------------- #
# 8. --dismiss, INCLUDING the re-read that broke the precedent twice
# --------------------------------------------------------------------------- #
def test_dismiss_silences_the_guard(home, repo):
    seed(str(repo / "claudedocs" / DOC))
    guard.dismiss_main(["--dismiss", DOC, "--session", SESSION])
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")


def test_dismiss_SURVIVES_a_later_read_of_the_same_doc(home, repo):
    """🔴 THE REGRESSION THAT SHIPPED PAST THREE AUDIT ROUNDS OF THE PRECEDENT, because
    every test drove `--dismiss` and then asserted silence and none re-read the thing
    afterwards. Clearing the ledger alone restores the session to its PRE-READ state,
    so the next read re-arms the guard — and the natural way to confirm a dismissal
    took is to look at the doc, which IS a read. The tombstone is what makes the
    promise true, and this is the case that can see it."""
    doc = str(repo / "claudedocs" / DOC)
    seed(doc)
    guard.dismiss_main(["--dismiss", DOC, "--session", SESSION])
    guard.post_tool_use(read_tool(doc))          # <- the confirming re-read
    assert guard.tracked_docs(state_dir()) == {}
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")


def test_dismiss_is_scoped_to_ONE_session(home, repo):
    """A NEW session starts fresh, which is the escape from a dismissal and is exactly
    what the report promises."""
    doc = str(repo / "claudedocs" / DOC)
    seed(doc)
    seed(doc, session="sess-other")
    guard.dismiss_main(["--dismiss", DOC, "--session", SESSION])
    assert guard.stop_decision(payload(event="Stop")) == ("silent", "")
    assert guard.stop_decision(payload(event="Stop",
                                       session_id="sess-other"))[0] == "block"


def test_dismiss_is_scoped_to_ONE_doc(home, repo):
    sd = state_dir()
    for name in (DOC, "handoff-other.md"):
        guard.record_read(sd, str(repo / "claudedocs" / name), now=READ_EPOCH)
    guard.record_work(sd, now=WORK_EPOCH)
    guard.dismiss_main(["--dismiss", DOC, "--session", SESSION])
    kind, text = guard.stop_decision(payload(event="Stop"))
    assert kind == "block"
    assert "handoff-other.md" in text and DOC not in text


def test_the_dismiss_report_is_pinned_as_a_WHOLE_string(home, repo):
    """🔴 A two-word check on this text would be satisfied by its own static prefix.
    The sentence is a claim about the state of the world, so it is pinned whole."""
    k = guard.doc_key(DOC)
    assert guard.dismiss_report(k, SESSION, ["read-" + k], True, ()) == (
        "handoff write-back guard: dismissed `%s` for session %s (cleared read-%s). "
        "It will not ask about `%s` again in session %s, even if the doc is read "
        "again — a NEW session starts fresh." % (k, SESSION, k, k, SESSION))


def test_the_residue_branch_owns_BOTH_halves_of_the_sentence(home, repo):
    """🔴 `removed` is empty both when there was nothing to remove AND when the removal
    FAILED. The precedent printed `nothing to dismiss` over a ledger entry that was
    sitting on disk. Pairing a head that says nothing happened with a promise the state
    cannot keep is the defect; one branch owning both clauses is the fix."""
    k = guard.doc_key(DOC)
    out = guard.dismiss_report(k, SESSION, [], True, ["read-" + k])
    assert "could NOT clear" in out and "nothing was dismissed" in out
    assert "will still ask" in out
    assert "It will not ask" not in out


def test_the_dismissal_is_recorded_in_an_audit_log_outside_the_swept_root(home, repo):
    """🔴 `--dismiss` IS A REAL BYPASS of a deterministic guard. In a hook whose premise
    is that prose lost 19 sessions of 22, gating the bypass on prose and not measuring
    it is the same mistake one level up. The log is also OUTSIDE the per-session root,
    so a session ageing out cannot take the record of its dismissals with it."""
    seed(str(repo / "claudedocs" / DOC))
    guard.dismiss_main(["--dismiss", DOC, "--session", SESSION])
    path = guard._dismissals_path()
    assert not path.startswith(guard._state_root())
    rows = [json.loads(ln) for ln in open(path) if ln.strip()]
    assert rows[-1]["doc_key"] == guard.doc_key(DOC)
    assert rows[-1]["session"] == SESSION
    # A NO-OP dismissal records too, so repeat attempts are visible rather than
    # silently identical to a first one.
    guard.dismiss_main(["--dismiss", DOC, "--session", SESSION])
    assert len([ln for ln in open(path) if ln.strip()]) == 2


def test_dismiss_never_reads_stdin_and_needs_both_flags(home):
    """The CLI mode is decided BEFORE any stdin read: a `--dismiss` invocation comes
    from a Bash tool call, where reading stdin would hang on a terminal forever."""
    p = run_hook(None, argv=["--dismiss", DOC])
    assert p.returncode == 0
    assert p.stdout.startswith("usage: handoff-write-guard.py --dismiss")


def test_a_dots_only_key_cannot_traverse_out_of_the_session_dir(home):
    """🔴 The allowed character set includes `.`, so it must exclude the all-dots
    components — otherwise `--dismiss ..` resolves to the state ROOT rather than to a
    file inside a session dir."""
    assert "/" not in guard.doc_key("..")
    assert set(guard.doc_key("..")) == {"_"}
    assert guard.doc_key("../../etc/passwd") == "passwd"


# --------------------------------------------------------------------------- #
# 9. THE HOT PATH — counted, not asserted from a comment
# --------------------------------------------------------------------------- #
def test_the_fast_path_does_exactly_one_stat_and_nothing_else(home, monkeypatch):
    """PostToolUse fires after EVERY tool call of every session. A session that has
    never read a handoff and is not reading one now must do one `exists` and stop."""
    calls = {"exists": 0, "listdir": 0, "makedirs": 0, "open": 0}
    real_exists, real_listdir = os.path.exists, os.listdir
    real_makedirs, real_open = os.makedirs, open
    monkeypatch.setattr(os.path, "exists",
                        lambda p: (calls.__setitem__("exists", calls["exists"] + 1),
                                   real_exists(p))[1])
    monkeypatch.setattr(os, "listdir",
                        lambda p: (calls.__setitem__("listdir", calls["listdir"] + 1),
                                   real_listdir(p))[1])
    monkeypatch.setattr(os, "makedirs",
                        lambda *a, **k: (calls.__setitem__("makedirs",
                                                           calls["makedirs"] + 1),
                                         real_makedirs(*a, **k))[1])
    monkeypatch.setattr(guard, "open",
                        lambda *a, **k: (calls.__setitem__("open", calls["open"] + 1),
                                         real_open(*a, **k))[1], raising=False)
    out = guard.post_tool_use(bash("git status -s"))
    assert out["fast_path"] is True
    assert calls == {"exists": 1, "listdir": 0, "makedirs": 0, "open": 0}


def test_a_subagent_call_skips_the_arming_regex_entirely(home, monkeypatch):
    """The subagent path stays one `exists` and no `re` work: a subagent's payload
    never contributes docs, so consulting the pattern for it would be pure cost."""
    seen = []

    class _Spy:
        def findall(self, s):
            seen.append(s)
            return []

    monkeypatch.setattr(guard, "HANDOFF_PATH_RX", _Spy())
    guard.post_tool_use(bash("cat claudedocs/%s" % DOC, agent_id="agt-1"))
    assert seen == []
    # POSITIVE CONTROL: the spy CAN see a call, so the empty above is a measurement
    # and not a probe wired to nothing.
    guard.post_tool_use(bash("cat claudedocs/%s" % DOC))
    assert len(seen) == 1


def test_the_hook_spawns_no_subprocess_on_any_path(home, repo):
    """🔴 A CLAIM ABOUT THE MODULE, PINNED STRUCTURALLY. Unlike its precedent this hook
    never needs one — condition 3 is a stat and two file reads — and `subprocess` is
    the single most expensive import a per-tool-call hook can take (3.4 ms, measured on
    the precedent). Pinned by source rather than by a comment, so re-adding it is a
    visible decision."""
    src = open(HOOK).read()
    body = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("import subprocess" in ln for ln in body)
    # POSITIVE CONTROL: the scan CAN see an import in this file.
    assert any(ln.strip() == "import json" for ln in body)


def test_shutil_is_deferred_off_the_hot_path(home):
    """`shutil` is reachable only from `prune`'s removal branch. A module-level import
    would be paid by every tool call of every session."""
    assert guard.shutil is None or "shutil" in sys.modules
    src = open(HOOK).read()
    top = src.split("def _sh(")[0]
    assert "\nimport shutil" not in top


# --------------------------------------------------------------------------- #
# 10. FAIL-OPEN — the whole contract, through a REAL process
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stdin", [
    "", "not json", "null", "[]", '{"hook_event_name":"Stop"}',
    '{"hook_event_name":"Stop","session_id":123}',
    '{"hook_event_name":"PostToolUse","session_id":"s","tool_input":null}',
    '{"hook_event_name":"Nonsense"}',
])
def test_every_malformed_input_exits_0_with_empty_stdout(home, stdin):
    p = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True,
                       text=True, env={**os.environ, "HOME": str(home)}, timeout=60)
    assert p.returncode == 0
    assert p.stdout == ""


def test_an_unwritable_state_root_never_blocks(home, repo, monkeypatch):
    """A guard that raises is felt at the exact moment a session is trying to end."""
    sd = seed(str(repo / "claudedocs" / DOC))

    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(os, "makedirs", boom)
    kind, _ = guard.stop_decision(payload(event="Stop"))
    # It may still report (the ledger is already on disk) but must never raise.
    assert kind in ("block", "notice", "silent")
    assert sd


def test_the_real_process_blocks_end_to_end(home, repo):
    """🔴 THE ONE END-TO-END CASE. Every other assertion here is in-process; this drives
    the actual script over stdin and reads the JSON the CLI would read, so the emit
    contract is checked as a whole rather than by its parts."""
    seed(str(repo / "claudedocs" / DOC))
    p = run_hook(payload(event="Stop"), env={"HOME": str(home)})
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["decision"] == "block" and DOC in out["reason"]
    assert forces_a_continuation(out) is True


# --------------------------------------------------------------------------- #
# 11. prune
# --------------------------------------------------------------------------- #
def test_prune_drops_only_state_older_than_the_ttl(home):
    root = guard._state_root()
    os.makedirs(os.path.join(root, "old"))
    os.makedirs(os.path.join(root, "new"))
    now = 2_000_000_000.0
    os.utime(os.path.join(root, "old"), (now - guard.STATE_TTL_SECS - 60,) * 2)
    os.utime(os.path.join(root, "new"), (now - 60,) * 2)
    assert guard.prune(now=now) == ["old"]
    assert sorted(os.listdir(root)) == ["new"]


# --------------------------------------------------------------------------- #
# 12. THE ANTI-DRIFT LEDGER for the declared copy
# --------------------------------------------------------------------------- #
def test_the_work_detection_is_byte_identical_to_the_precedent():
    """🔴 THE COPY IS DECLARED, SO IT IS PINNED. `claude/RULES.md` says one rule, one
    place — and the alternative here (a shared hook module) would add an import to a
    BLOCKING hook's per-tool-call fast path, whose owners measured its cost across
    several audit rounds. What that rule is actually about is silent DRIFT between
    copies, and this closes exactly that: it fails when EITHER file's copy moves, so
    the two can only change together.

    Compared as SOURCE LINES rather than by importing the precedent, because importing
    it would execute its module body inside this suite for no benefit.
    """
    names = ("_CMD_START", "WORK_BASH_PAT", "QUOTED_PLACEHOLDER", "QUOTED_PAT",
             "COMMENT_PAT")

    def block(path):
        src = open(path).read().splitlines()
        out, taking = {}, None
        for ln in src:
            for n in names:
                if ln.startswith(n + " = "):
                    taking = n
                    out[taking] = []
            if taking is not None:
                out[taking].append(ln.rstrip())
                if ln.rstrip().endswith(")") or (
                        ln.startswith(taking + " = ") and not ln.rstrip().endswith("(")):
                    if ln.count("(") <= ln.count(")"):
                        taking = None
        return out

    mine, theirs = block(HOOK), block(PRECEDENT)
    assert set(mine) == set(names), sorted(set(names) - set(mine))
    assert mine == theirs, "the work-detection copy has drifted from its precedent"


def test_is_work_agrees_with_the_precedent_on_the_shapes_that_matter():
    """A behavioural companion to the byte pin above: a structural check type-checks
    past a wrong argument, so the predicate is also exercised on the shapes the
    precedent's own measurements named."""
    assert guard.is_work(bash('git -C "$DEVRC" commit -m "msg"')) is True
    assert guard.is_work(bash("git -C /lit push")) is True
    assert guard.is_work(bash("gh pr create --fill")) is True
    assert guard.is_work(payload(tool_name="Edit",
                                 tool_input={"file_path": "/a/b.py"})) is True
    assert guard.is_work(bash("echo remember to git commit later")) is False
    assert guard.is_work(bash("grep -rn 'git commit' scripts/")) is False
    assert guard.is_work(bash("git log --oneline -3")) is False
    assert guard.is_work(bash("git status  # git push later")) is False


# --------------------------------------------------------------------------- #
# 13. THE DELIVERY SEAM — a hook that ships unregistered sits INERT
# --------------------------------------------------------------------------- #
def test_home_nix_deploys_this_hook():
    """#452's lesson: a hook can ship to both hosts, report a successful switch, and do
    nothing, with no signal anywhere. Every component tested, the seam owned by nobody."""
    assert 'home.file.".claude/hooks/handoff-write-guard.py"' in HOME_NIX.read_text()


def test_the_registrar_registers_it_on_PostToolUse_and_Stop_and_nowhere_else():
    src = REGISTRAR.read_text()
    assert '"~/.claude/hooks/handoff-write-guard.py"' in src
    assert '"handoff-write-guard.py",' in src          # MANAGED_HOOK_SCRIPTS
    # The event table, read as a literal so a rename of either event is visible.
    line = [ln for ln in src.splitlines()
            if ln.startswith("HANDOFF_GUARD_EVENTS")]
    assert line, "no HANDOFF_GUARD_EVENTS table in the registrar"
    assert line[0].split("=", 1)[1].strip() == '["PostToolUse", "Stop"]'


def test_the_block_text_points_at_a_flow_that_exists():
    """A block message is an instruction to a place; a dead pointer is worse than none."""
    assert (ROOT / guard.HANDOFF_TOOL).is_file()
    assert (ROOT / "claude" / "skills" / "handoff" / "SKILL.md").is_file()


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
