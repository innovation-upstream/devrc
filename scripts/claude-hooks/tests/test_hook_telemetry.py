#!/usr/bin/env python3
"""The Stop-hook decision emitter — `scripts/claude-hooks/hook_telemetry.py` and the
two hooks wired to it.

WHAT EACH TEST HERE IS (labelled, because "it passes" is not a category)
------------------------------------------------------------------------
  * the FAIL-OPEN battery (`test_the_*_hook_is_unchanged_when_the_spool_*`)
        REGRESSION coverage for the contract the emitter is allowed to exist under:
        exit 0, byte-identical stdout, empty stderr, under four hostile spool
        states. Each case is watched to be hostile — the row count is asserted to be
        ZERO in the same test — so a case that silently stopped being hostile (a
        directory that became writable, a path that stopped existing) is a red test
        rather than a vacuous green. 🔴 Its own negative control is
        `test_the_failopen_battery_can_go_red`, which drives the same battery against
        an emitter that RAISES and requires the hook to survive that too.
  * `test_a_decision_row_carries_the_entity_id_and_the_decision`,
    `test_could_not_measure_is_not_a_clean_measurement`
        REGRESSION coverage for the three measurement failures this module exists to
        remove — the missing per-entity key, and a could-not-measure folded into a
        clean result. Each asserts the discriminating FIELD, not the presence of a row.
  * `test_captured_text_cannot_reach_the_payload` + its POSITIVE CONTROL
        REGRESSION for the privacy boundary, and the control is not optional: a guard
        that dropped EVERY value would pass the first assertion and emit nothing
        useful, which is the same reassuring zero as a scanner wired to nothing. The
        control feeds the SAME keys an id and watches them arrive.
  * `test_a_stop_writes_exactly_one_row_per_hook` (POSITIVE CONTROL) and
    `test_*_writes_no_row` (NEGATIVE CONTROLS)
        The instrument's own validation, reported as a pair. A zero from an emitter
        wired to nothing is indistinguishable from a correct zero, so no test here
        asserts a zero without a sibling that watches the count move.
  * the two VOCABULARY pins
        SEAM / LEDGER guards, not regression coverage. Three files spell the decision
        vocabulary and two spell reason tokens; nothing but these makes them agree.
        They fail when either side GROWS or SHRINKS.
  * everything else is a unit test of a pure function.

🔴 THIS SUITE NEVER TOUCHES THE INHERITED `$HOME`. Every module load and every
subprocess runs with `HOME` and `ACTIVITY_SPOOL_DIR` pointed inside `tmp_path` — the
hooks resolve their caches from `HOME` at call time, and the emitter resolves the
spool from `ACTIVITY_SPOOL_DIR`, so a suite that forgot either would write into the
operator's own dataset (the defect `scripts/tests/test_activity_spool_isolation.py`
exists for) or delete their nudge state (the one
`scripts/tests/test_hook_suites_do_not_touch_the_inherited_home.py` exists for).

run:  python -m pytest scripts/claude-hooks/tests/test_hook_telemetry.py -q
"""
import ast
import base64
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.abspath(os.path.join(HERE, os.pardir))
ROOT = os.path.abspath(os.path.join(HOOKS, os.pardir, os.pardir))
COLLECTOR = os.path.join(ROOT, "scripts", "collector")

NUDGE = os.path.join(HOOKS, "next-step-nudge.py")
GUARD = os.path.join(HOOKS, "handoff-write-guard.py")

# Fixture ids: pairwise distinct, distinct from every constant the modules name
# (MAX_BLOCKS=2, MAX_FIRES=3, MAX_DOCS=3, MIN_MESSAGE_CHARS=600, TAIL_CHARS=800) and
# distinct from the ids the sibling suites use (193, 200, 201, 307, 911). A fixture
# that can only produce a constant's own value cannot see a mutant that hardcodes it.
SESSION_A = "sess-telem-7f3a"
SESSION_B = "sess-telem-91c5"
DOC_TOPIC = "quokka"

# A realistic captured-text value — the shape the repo forbids: a sentence someone
# actually typed. Synthetic, regenerated to the SHAPE; no real content is in this repo.
PROSE = "I merged it and the gate went green, so please ship it today."


def load(path, name):
    """Import a module by path. Called INSIDE a test, after `$HOME` is redirected:
    these modules resolve `~` at CALL time, but a module cached across tests would
    also memoise its spool-emit lookup, and a fresh load per test is what keeps each
    case independent."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A throwaway `$HOME` + spool, exported into `os.environ` so both this process
    and every subprocess it spawns resolve the same throwaway paths."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(spool))
    monkeypatch.delenv("HOOK_TELEMETRY_OFF", raising=False)
    monkeypatch.delenv("NEXT_STEP_NUDGE_OFF", raising=False)
    return {"home": home, "spool": spool, "tmp": tmp_path}


@pytest.fixture()
def HT(env):
    return load(os.path.join(HOOKS, "hook_telemetry.py"), "hook_telemetry_under_test")


# --------------------------------------------------------------------------- #
# Reading rows back — through the REAL daemon parser, so what is asserted is what
# the collector would ship, not what this file believes the line format is.
# --------------------------------------------------------------------------- #
def rows(spool):
    log = spool / "current.log"
    if not log.exists():
        return []
    if COLLECTOR not in sys.path:
        sys.path.insert(0, COLLECTOR)
    import collector as C  # noqa: PLC0415
    out = []
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        ev = C.parse_line(line)
        assert ev is not None, "daemon could not parse emitted line: %r" % line
        out.append(ev)
    return out


def payloads(spool, hook=None):
    out = []
    for ev in rows(spool):
        assert ev["source"] == "hook" and ev["kind"] == "stop-decision"
        p = json.loads(ev["payload"])
        if hook is None or p.get("hook") == hook:
            p["_text"] = ev.get("text")
            p["_session"] = ev.get("session")
            out.append(p)
    return out


def raw_lines(spool):
    log = spool / "current.log"
    return log.read_text().splitlines() if log.exists() else []


def decoded_line_text(spool):
    """Every byte a line carries, with the base64 fields DECODED.

    🔴 The privacy assertions read THIS, not the payload dict. A leak that landed in
    the `text` column, in `session`, or in a key rather than a value would be invisible
    to a check that only walked `payload.values()` — and base64 makes it invisible to a
    naive grep of the file as well.
    """
    out = []
    for line in raw_lines(spool):
        for tok in line.split("\t"):
            if tok.startswith("b64:"):
                out.append(base64.b64decode(tok.split("=", 1)[1]).decode("utf-8"))
            else:
                out.append(tok)
    return "\n".join(out)


# 🔴 EVERY HOOK RUN IS BOUNDED. A Stop hook that does not return is strictly worse
# than one that crashes — the operator's turn never ends — and the failure mode is
# REAL: `spool_emit.emit` opens the log with a blocking `open(..., "a")`, which never
# returns on a FIFO with no reader (measured: >12 s, no exit). Without a timeout here
# that defect wedges the whole suite instead of failing one test, and a wedged suite
# gets read as an infrastructure problem. Generous enough that only a true hang trips
# it: a hook run is single-digit milliseconds.
_HOOK_TIMEOUT_SECONDS = 30


def run_hook(script, payload, env, extra_env=None, timeout=_HOOK_TIMEOUT_SECONDS):
    e = dict(os.environ)
    e.update(extra_env or {})
    return subprocess.run([sys.executable, script], input=json.dumps(payload),
                          capture_output=True, text=True, env=e, timeout=timeout)


# --------------------------------------------------------------------------- #
# Fixtures that drive the two hooks into a known decision
# --------------------------------------------------------------------------- #
def transcript(env, tools=1):
    """A transcript tail the nudge's `_turn_shape` reads as "the operator asked for
    real work and the turn used tools"."""
    path = env["tmp"] / "transcript.jsonl"
    lines = [json.dumps({"type": "user", "message": {"content":
             "walk the whole tree and rework the resolver, then report what changed"}})]
    for _ in range(tools):
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {}}]}}))
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def firing_stop(env, session=SESSION_A):
    """A Stop payload the nudge FIRES on: long enough, no forward-looking tail."""
    body = ("The resolver now walks the tree in one pass. " * 30)[:900]
    return {"hook_event_name": "Stop", "session_id": session,
            "transcript_path": transcript(env),
            "last_assistant_message": body}


def suppressed_stop(env, session=SESSION_B):
    """A Stop payload the nudge SUPPRESSES: the tail names a next step."""
    p = firing_stop(env, session)
    p["last_assistant_message"] += "\n\nI'll run the gate next and report back."
    return p


def arm_guard(env, session=SESSION_A, topic=DOC_TOPIC):
    """Drive the guard's PostToolUse path into "read a handoff doc, then did work"."""
    docdir = env["tmp"] / "repo" / "claudedocs"
    docdir.mkdir(parents=True, exist_ok=True)
    doc = docdir / ("handoff-%s.md" % topic)
    doc.write_text("resumed\n")
    run_hook(GUARD, {"hook_event_name": "PostToolUse", "session_id": session,
                     "tool_name": "Read", "tool_input": {"file_path": str(doc)},
                     "cwd": str(docdir)}, env)
    run_hook(GUARD, {"hook_event_name": "PostToolUse", "session_id": session,
                     "tool_name": "Edit",
                     "tool_input": {"file_path": str(env["tmp"] / "src.py")},
                     "cwd": str(docdir)}, env)
    # The doc's own mtime is one of the three satisfaction routes, so push it back
    # behind the read or the guard would score the session as already recorded.
    os.utime(str(doc), (1_000_000, 1_000_000))
    return doc


# --------------------------------------------------------------------------- #
# 1. The row answers the questions a transcript scan had to answer
# --------------------------------------------------------------------------- #
def test_a_decision_row_carries_the_entity_id_and_the_decision(env):
    """🔴 THE PER-ENTITY KEY IS THE POINT. A lift figure for a sibling guard was wrong
    (+5.3pp against a correct +10.1pp) because the detector matched ANY task id rather
    than the blocked one — there was no per-entity key on the decision, so the entity
    had to be guessed from prose. This asserts the guard names the doc it was reasoning
    about, in the row, at the moment it decided."""
    arm_guard(env)
    r = run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env)
    assert r.returncode == 0
    assert json.loads(r.stdout)["decision"] == "block"   # it really did fire

    docs = [p for p in payloads(env["spool"], "handoff-write-guard")
            if p["entity_kind"] == "handoff-doc"]
    assert len(docs) == 1, docs
    p = docs[0]
    assert p["entity"] == "handoff-%s.md" % DOC_TOPIC
    assert p["decision"] == "fired"
    assert p["satisfier"] == "handoff-write" and p["satisfied"] is False
    assert p["measured"] is True
    assert p["_text"] == "handoff-write-guard" and p["_session"] == SESSION_A


def test_the_nudge_row_names_the_suppressors_it_found(env):
    """The other half of the same question: which satisfying act was looked for, and
    was one found. A compliance measurement that had to infer this from transcript
    text came back at a bogus 100.0%, because the guard's own message contains the
    strings that define compliance."""
    r = run_hook(NUDGE, suppressed_stop(env), env)
    assert r.returncode == 0 and r.stdout == ""

    p = payloads(env["spool"], "next-step-nudge")[0]
    assert p["decision"] == "suppressed" and p["reason"] == "named-next-step"
    assert p["satisfier"] == "next-step-line" and p["satisfied"] is True
    assert "commits" in p["suppressors"]        # "I'll run the gate next"
    assert p["entity"] == SESSION_B and p["entity_kind"] == "session"


def test_could_not_measure_is_not_a_clean_measurement(env, HT):
    """🔴 THE TWO MUST BE SEPARABLE ON A FIELD, not on a hunch. A guard that could not
    read its ledger and a guard that measured a satisfied doc both stay silent; a
    schema that cannot tell them apart makes every rate wrong by the size of the
    unmeasurable population."""
    HT.emit_decision("h", HT.DECISION_SUPPRESSED, entity="a", measured=True,
                     satisfied=True, spool_dir=env["spool"])
    HT.emit_decision("h", HT.DECISION_UNMEASURED, entity="b", measured=False,
                     satisfied=None, spool_dir=env["spool"])
    clean, unknown = payloads(env["spool"])

    assert clean["decision"] != unknown["decision"]
    assert clean["measured"] is True and unknown["measured"] is False
    assert clean["satisfied"] is True and unknown["satisfied"] is None
    # ...and the pair is NOT separable by "did it fire", which is the filter a naive
    # consumer reaches for first. This is the control that makes the assertion above
    # mean something.
    assert (clean["decision"] == "fired") == (unknown["decision"] == "fired")


def read_record(env, session=SESSION_A):
    ledger = env["home"] / ".cache" / "claude-handoff-write" / "s" / session
    return [p for p in ledger.iterdir() if p.name.startswith("read-")][0]


@pytest.mark.parametrize("transcript_path,reason", [
    ("", "no-transcript-path"),
    ("/binary", "shape-unreadable"),
])
def test_the_nudge_reports_an_unreadable_transcript_as_could_not_measure(
        env, transcript_path, reason):
    """🔴 FOUND BY A MUTATION SWEEP, NOT BY READING. Deleting `decision_for`'s
    `UNMEASURED_REASONS` arm — collapsing the nudge's could-not-measure into
    `suppressed` — SURVIVED a green 53-test suite: the vocabulary pin checks that the
    tokens agree, never that the MAPPING does, and every other test drove a reason on
    a different arm.

    Both unmeasurable shapes are covered. `/binary` is 4 KB of non-JSON, which is the
    case `_turn_shape` returns None for — the same fixture shape as
    `test_fail_open_on_a_binary_transcript` in the nudge's own suite, where the
    consequence was firing rather than mislabelling.
    """
    p = firing_stop(env)
    if transcript_path == "/binary":
        binary = env["tmp"] / "not-a-transcript.bin"
        binary.write_bytes(bytes(range(256)) * 16)
        p["transcript_path"] = str(binary)
    else:
        p["transcript_path"] = ""

    r = run_hook(NUDGE, p, env)
    assert r.returncode == 0 and r.stdout == ""        # it stays silent, as before
    got = payloads(env["spool"], "next-step-nudge")[0]
    assert got["decision"] == "could-not-measure", got
    assert got["measured"] is False and got["reason"] == reason
    # ...and it is NOT the same row a satisfied turn produces. The control that makes
    # the assertion above a distinction rather than a spelling.
    r2 = run_hook(NUDGE, suppressed_stop(env), env)
    assert r2.returncode == 0
    clean = payloads(env["spool"], "next-step-nudge")[1]
    assert clean["decision"] == "suppressed" and clean["measured"] is True


def test_the_guard_reports_an_unreadable_read_STAMP_as_could_not_measure(env):
    """An unparseable read stamp leaves no anchor to compare anything against. The
    guard already refuses to BLOCK on it; this pins that the row says so too, PER DOC
    — the entity is still named, because the record was readable enough to name it."""
    arm_guard(env)
    entry = read_record(env)
    rec = json.loads(entry.read_text())
    rec["first_read_ts"] = "not-a-timestamp"
    entry.write_text(json.dumps(rec))

    r = run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env)
    assert r.returncode == 0
    docs = [p for p in payloads(env["spool"], "handoff-write-guard")
            if p["entity_kind"] == "handoff-doc"]
    assert len(docs) == 1 and docs[0]["decision"] == "could-not-measure"
    assert docs[0]["measured"] is False and docs[0]["satisfied"] is None
    assert docs[0]["reason"] == "ledger-unreadable"
    assert docs[0]["entity"] == "handoff-%s.md" % DOC_TOPIC


def test_an_unreadable_ledger_RECORD_is_not_reported_as_an_empty_ledger(env):
    """🔴 THE SAME MISTAKE ONE LEVEL UP, AND IT IS THE ONE A REVIEWER WOULD MISS.
    `tracked_docs` skips a record it cannot parse, so a session whose only entry is
    truncated is indistinguishable at that call from a session that read no handoff at
    all — and reporting it as `no-tracked-docs` would be exactly the clean-looking
    zero over an unmeasurable state that this whole change exists to remove.

    Its POSITIVE CONTROL is the sibling below: the same code path, a genuinely empty
    ledger, must still say `no-tracked-docs` and `measured=True`."""
    arm_guard(env)
    read_record(env).write_text("{ truncated")

    r = run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env)
    assert r.returncode == 0 and r.stdout == ""
    p = payloads(env["spool"], "handoff-write-guard")[0]
    assert p["decision"] == "could-not-measure" and p["measured"] is False
    assert p["reason"] == "ledger-unreadable"


def test_a_genuinely_empty_ledger_is_still_a_clean_measurement(env):
    """POSITIVE CONTROL for the test above — the arm that must NOT move."""
    arm_guard(env)
    read_record(env).unlink()

    run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env)
    p = payloads(env["spool"], "handoff-write-guard")[0]
    assert p["decision"] == "suppressed" and p["measured"] is True
    assert p["reason"] == "no-tracked-docs"


def test_a_spent_ladder_is_armed_not_suppressed(env):
    """🔴 ELIGIBLE-BUT-WITHHELD IS ITS OWN POPULATION. The guard goes silent after
    MAX_FIRES, and counting those Stops as "nothing was owed" would understate the
    miss rate by exactly the size of the ladder."""
    arm_guard(env)
    stop = {"hook_event_name": "Stop", "session_id": SESSION_A}
    for _ in range(4):
        run_hook(GUARD, stop, env)
    docs = [p for p in payloads(env["spool"], "handoff-write-guard")
            if p["entity_kind"] == "handoff-doc"]
    assert [p["decision"] for p in docs] == ["fired", "fired", "fired", "armed"]
    assert [p["rung"] for p in docs] == ["block", "block", "notice", "silent"]
    assert [p["fire"] for p in docs] == [1, 2, 3, 4]


def test_the_nudge_reports_a_second_stop_as_armed(env):
    """The nudge's own budget: one fire per session. The second Stop is eligible and
    withheld, which is `armed` — not `suppressed`, and not a missing row."""
    p = firing_stop(env)
    assert run_hook(NUDGE, p, env).stdout != ""     # the first one really fires
    assert run_hook(NUDGE, p, env).stdout == ""
    got = payloads(env["spool"], "next-step-nudge")
    assert [g["decision"] for g in got] == ["fired", "armed"]
    assert got[1]["reason"] == "already-fired"


# --------------------------------------------------------------------------- #
# 2. The privacy boundary — structural, with its positive control
# --------------------------------------------------------------------------- #
def test_captured_text_cannot_reach_the_payload(env, HT):
    """🔴 EVERY STRING-TAKING FIELD, FED REALISTIC PROSE. The check reads the DECODED
    line, so a leak into `text`, into `session` or into a payload KEY is caught too —
    a base64 field is invisible to a grep of the file, and a walk of
    `payload.values()` would miss the other two columns entirely."""
    HT.emit_decision(PROSE, PROSE, session=PROSE, entity=PROSE, entity_kind=PROSE,
                     satisfier=PROSE, reason=PROSE,
                     extra={"note": PROSE, PROSE: "x", "list": [PROSE, PROSE]},
                     spool_dir=env["spool"])
    body = decoded_line_text(env["spool"])
    assert PROSE not in body
    for word in PROSE.split():
        assert word not in body.split(), word

    p = payloads(env["spool"])[0]
    # The row still EXISTS — a leak attempt is DROPPED, never turned into silence, and
    # the drop is COUNTED rather than invisible. Eight rejections: hook, decision,
    # entity, entity_kind, satisfier, reason, the `note` value and the prose KEY.
    assert p.get("dropped", 0) == 8, p
    assert "hook" not in p and "entity" not in p and "reason" not in p
    # `session` is a top-level column, not a payload key: it must be absent too.
    assert p["_session"] is None or p["_session"] == ""
    assert p["_text"] == ""
    # The `list` value survives as an EMPTY list rather than vanishing — each member
    # was rejected individually, which is the shape a consumer can tell apart from
    # "no suppressors matched".
    assert p["list"] == []


def test_the_same_fields_DO_carry_an_id(env, HT):
    """🔴 POSITIVE CONTROL for the test above, and it is not optional. A boundary that
    dropped every value would satisfy that assertion while emitting nothing usable —
    the same reassuring zero as a scanner wired to nothing. These are the exact keys
    that were just rejected, fed ids instead."""
    HT.emit_decision("handoff-write-guard", HT.DECISION_FIRED,
                     session=SESSION_A, entity="handoff-%s.md" % DOC_TOPIC,
                     entity_kind="handoff-doc", satisfier="handoff-write",
                     reason="handoff-missing", extra={"fire": 2, "rung": "block"},
                     spool_dir=env["spool"])
    p = payloads(env["spool"])[0]
    assert p["hook"] == "handoff-write-guard"
    assert p["entity"] == "handoff-%s.md" % DOC_TOPIC
    assert p["entity_kind"] == "handoff-doc" and p["satisfier"] == "handoff-write"
    assert p["reason"] == "handoff-missing" and p["fire"] == 2 and p["rung"] == "block"
    assert p["_session"] == SESSION_A
    assert "dropped" not in p


def test_an_extra_cannot_overwrite_a_reserved_payload_key(env, HT):
    """🔴 A SCHEMA CORRUPTION THAT WOULD BE INVISIBLE. Extras are merged after the
    fixed fields, so `extra={"decision": "fired"}` would silently win and produce a row
    whose `decision` no longer means what every consumer reads it as — with a valid
    token in it, so nothing downstream could tell. The collision is rejected and
    COUNTED, and the control below shows a non-colliding extra still arrives."""
    HT.emit_decision("h", HT.DECISION_SUPPRESSED, entity="e1",
                     extra={"decision": "fired", "entity": "e2", "fire": 3},
                     spool_dir=env["spool"])
    p = payloads(env["spool"])[0]
    assert p["decision"] == "suppressed" and p["entity"] == "e1"
    assert p["dropped"] == 2
    assert p["fire"] == 3          # the non-colliding extra still lands


def test_the_guard_row_carries_the_doc_KEY_and_never_the_doc_PATH(env):
    """A resolved handoff path contains `$HOME`. The ledger KEY — a sanitized
    basename — is what identifies the entity, and the path must not ride along."""
    doc = arm_guard(env)
    run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env)
    body = decoded_line_text(env["spool"])
    assert "handoff-%s.md" % DOC_TOPIC in body
    assert str(doc) not in body
    assert str(env["home"]) not in body


@pytest.mark.parametrize("value,admitted", [
    ("handoff-alpha.md", True),
    ("sess-telem-7f3a", True),
    ("could-not-measure", True),
    ("owner/repo#4821", True),
    ("", True),                       # a legitimately empty reason
    ("two words", False),             # the space is the whole guard
    ("a\nb", False),
    ("a\tb", False),
    ("shipped it, gate green", False),
    ("x" * 121, False),               # over the length bound
    (7, True), (True, True), (None, True),
    (10 ** 13, False),
    (object(), False),
])
def test_coerce_admits_ids_and_rejects_prose(HT, value, admitted):
    got = HT.coerce(value)
    assert (got is not HT._DROP) is admitted, (value, got)


def test_coerce_never_calls_str_on_an_arbitrary_object(HT):
    """🔴 A `__str__` that raises is one more way a telemetry helper takes its caller
    down with it, and the way to not have that failure mode is to not have the call."""
    class Hostile:
        def __str__(self):
            raise ValueError("nope")

        def __repr__(self):
            raise ValueError("nope")

    assert HT.coerce(Hostile()) is HT._DROP


def test_emit_swallows_a_spool_layer_that_raises(env, HT, monkeypatch):
    """🔴 THE LAST HANDLER, AND IT IS NOT REACHABLE ANY OTHER WAY. `spool_emit.emit`
    swallows its own `OSError`s, and `coerce` never calls `str()` on an object, so no
    realistic input makes `emit_decision` raise — which is exactly what makes its
    `except` unkillable by a test that only feeds it hostile DATA. Reach it directly.

    The return value is asserted to be the empty string, not merely "it did not
    raise": a mutant that re-raises would be caught by the hook's own outer handler
    and every subprocess assertion would stay green."""
    se = HT.spool_emit_module()
    assert se is not None                                  # positive control

    def boom(*a, **k):
        raise RuntimeError("simulated spool-layer defect")

    monkeypatch.setattr(se, "emit", boom)
    assert HT.emit_decision("h", "fired", spool_dir=env["spool"]) == ""
    assert HT.emit_rows([{"hook": "h", "decision": "fired"}],
                        spool_dir=env["spool"]) == 0


# --------------------------------------------------------------------------- #
# 3. FAIL-OPEN — the contract the emitter is allowed to exist under
# --------------------------------------------------------------------------- #
def hostile_spools(tmp_path):
    """Four spool states a Stop hook can meet on a real box. Each is returned with the
    path to point `ACTIVITY_SPOOL_DIR` at."""
    # (a) MISSING, and uncreatable — the parent is not writable.
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(str(locked), 0o500)
    # (b) PRESENT but not writable.
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(str(ro), 0o500)
    # (c) The spool dir is a FILE — `mkdir` raises, then so does `open` (ENOTDIR).
    afile = tmp_path / "afile"
    afile.write_text("not a directory")
    # (d) `current.log` itself is a DIRECTORY — the open is EISDIR, which is the
    #     closest reachable stand-in for a write that cannot land (a full disk).
    eisdir = tmp_path / "eisdir"
    (eisdir / "current.log").mkdir(parents=True)
    return {"missing-and-uncreatable": locked / "spool", "unwritable-dir": ro,
            "spool-is-a-file": afile, "log-is-a-directory": eisdir}


@pytest.mark.parametrize("case", ["missing-and-uncreatable", "unwritable-dir",
                                  "spool-is-a-file", "log-is-a-directory"])
def test_the_nudge_hook_is_unchanged_when_the_spool_is_broken(env, case):
    """🔴 THE WORST CASE MUST BE IDENTICAL TO THE EMITTER NOT EXISTING. Same payload,
    same stdout, exit 0, empty stderr — under a spool that cannot be written.

    The zero-row assertion is what keeps each case HONEST: a case that quietly stopped
    being hostile would still pass the exit/stdout half while proving nothing."""
    bad = hostile_spools(env["tmp"])[case]
    r = run_hook(NUDGE, firing_stop(env), env,
                 {"ACTIVITY_SPOOL_DIR": str(bad)})
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert r.stderr == ""
    assert rows(env["spool"]) == []          # nothing fell back to the good spool
    assert not (bad / "current.log").is_file() if bad.is_dir() else True


@pytest.mark.parametrize("case", ["missing-and-uncreatable", "unwritable-dir",
                                  "spool-is-a-file", "log-is-a-directory"])
def test_the_guard_still_blocks_when_the_spool_is_broken(env, case):
    """The same battery on the hook that can BLOCK. A telemetry failure must not be
    able to swallow a block — the failure mode that would make this instrumentation
    more expensive than the thing it measures."""
    arm_guard(env)
    bad = hostile_spools(env["tmp"])[case]
    r = run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env,
                 {"ACTIVITY_SPOOL_DIR": str(bad)})
    assert r.returncode == 0
    assert json.loads(r.stdout)["decision"] == "block"
    assert r.stderr == ""
    assert rows(env["spool"]) == []


SHADOW_AT_IMPORT = "raise RuntimeError('telemetry exploded at import')\n"
SHADOW_AT_CALL = (
    "def emit_decision(*a, **k):\n"
    "    raise RuntimeError('telemetry exploded on call')\n"
    "def emit_rows(*a, **k):\n"
    "    raise RuntimeError('telemetry exploded on call')\n"
)


@pytest.mark.parametrize("script,expect", [
    (NUDGE, "hookSpecificOutput"), (GUARD, "decision")])
@pytest.mark.parametrize("body,when", [(SHADOW_AT_IMPORT, "import"),
                                       (SHADOW_AT_CALL, "call")])
def test_a_hook_survives_an_emitter_that_raises(env, tmp_path, script, expect,
                                                body, when):
    """🔴 THE EMITTER'S OWN FAILURE, REACHED TWO DIFFERENT WAYS — and the second one
    is the one that matters. A `hook_telemetry` that blows up AT IMPORT is caught by
    each hook's `_telemetry()`; one that blows up ON CALL is caught only by the
    handler wrapped around the emission in `main()`. Testing only the import case
    leaves that handler unkillable, which is the unreachable-guard shape.

    This also makes the four-case spool battery above mean something: a hook that had
    quietly stopped calling the emitter would pass every one of those, and this — by
    planting a module that cannot be called without raising — would pass too. The pair
    that separates them is `test_a_stop_writes_exactly_one_row_per_hook` (the count
    must move) beside these (a failure must cost nothing). Read as a set, never alone.

    🔴 THE HOOK IS COPIED NEXT TO THE POISONED MODULE, AND `PYTHONPATH` WOULD NOT
    WORK. Python puts the SCRIPT's own directory at `sys.path[0]`, ahead of every
    `PYTHONPATH` entry, so a shadow injected that way loses to the real
    `scripts/claude-hooks/hook_telemetry.py` and the test passes having tested
    nothing. That is how the first draft of this test was written, and it is the same
    reassuring green a harness wired to nothing produces. Copying the hook makes the
    poisoned module the one `sys.path[0]` resolves.
    """
    shadow = tmp_path / ("shadow-" + when)
    shadow.mkdir()
    (shadow / "hook_telemetry.py").write_text(body)
    copied = shadow / os.path.basename(script)
    copied.write_text(open(script, encoding="utf-8").read())
    if script is GUARD:
        arm_guard(env)
    payload = ({"hook_event_name": "Stop", "session_id": SESSION_A}
               if script is GUARD else firing_stop(env))
    r = run_hook(str(copied), payload, env)
    assert r.returncode == 0
    assert expect in json.loads(r.stdout)
    assert r.stderr == ""
    # POSITIVE CONTROL that the poison was actually reached: the run wrote no row,
    # where the identical payload through the real module writes exactly one.
    assert rows(env["spool"]) == []
    r2 = run_hook(script, payload, env)
    assert r2.returncode == 0 and len(rows(env["spool"])) == 1


def test_the_emitter_is_a_silent_no_op_with_no_spool_emit_anywhere(env, HT,
                                                                  monkeypatch):
    """A host without the collector deployed. Not hypothetical: `nix/home.nix` deploys
    the collector conditionally and this module names two candidate directories, both
    of which can be absent."""
    monkeypatch.setattr(HT, "_candidate_dirs", lambda: [])
    monkeypatch.setattr(HT, "_SE", HT._UNRESOLVED)
    assert HT.spool_emit_module() is None
    assert HT.emit_decision("h", "fired", spool_dir=env["spool"]) == ""
    assert rows(env["spool"]) == []


def test_emit_rows_isolates_a_bad_row_from_the_rows_after_it(env, HT):
    """One malformed record must not cost the records behind it — and the return value
    is a COUNT, so a caller can watch the number move rather than trust a bool."""
    n = HT.emit_rows([
        {"hook": "a", "decision": "fired", "entity": "e1"},
        "not a dict",
        {"hook": "b", "decision": "fired", "no_such_kwarg": 1},
        {"hook": "c", "decision": "fired", "entity": "e3"},
    ], spool_dir=env["spool"])
    assert n == 2
    assert [p["hook"] for p in payloads(env["spool"])] == ["a", "c"]


def test_emit_rows_on_an_empty_list_is_zero_and_writes_nothing(env, HT):
    assert HT.emit_rows([], spool_dir=env["spool"]) == 0
    assert HT.emit_rows(None, spool_dir=env["spool"]) == 0
    assert rows(env["spool"]) == []


# --------------------------------------------------------------------------- #
# 4. Instrument validation — the positive/negative control pair, always together
# --------------------------------------------------------------------------- #
def test_a_stop_writes_exactly_one_row_per_hook(env):
    """🔴 POSITIVE CONTROL. A reassuring zero from an emitter wired to nothing is
    indistinguishable from a clean pass, so the count is watched to MOVE — and to move
    by exactly one per hook per Stop, since a duplicated row would double every
    denominator computed from this table."""
    assert rows(env["spool"]) == []
    run_hook(NUDGE, firing_stop(env), env)
    assert len(payloads(env["spool"], "next-step-nudge")) == 1
    arm_guard(env, session=SESSION_B)
    run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_B}, env)
    assert len(payloads(env["spool"], "handoff-write-guard")) == 1
    assert len(rows(env["spool"])) == 2


def test_the_kill_switch_writes_no_row_and_changes_nothing_else(env):
    """NEGATIVE CONTROL, and the lever the latency measurement is taken with: the same
    binary, one environment variable apart."""
    r = run_hook(NUDGE, firing_stop(env), env, {"HOOK_TELEMETRY_OFF": "1"})
    assert r.returncode == 0 and r.stderr == ""
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert rows(env["spool"]) == []


def test_a_post_tool_use_call_writes_no_row(env):
    """NEGATIVE CONTROL. The guard's hot path fires after EVERY tool call of every
    session; a row there would be four orders of magnitude of noise and would load
    `spool_emit` on the path whose cost was measured at ~0.1 ms."""
    arm_guard(env)
    assert rows(env["spool"]) == []


def _imported_modules(script, payload, env):
    """Which modules a REAL hook process imported, read out of `-X importtime`.

    🔴 THE INSTRUMENT IS NOT THE ROW COUNT, AND THAT DISTINCTION IS THE WHOLE TEST.
    "Zero rows on the hot path" was already asserted above and stayed green through the
    defect this pins: the emitter was imported, decided it had nothing to ship, and
    wrote nothing. A count cannot see a cost that produces no output, so the cost is
    read directly — `-X importtime` names every module the interpreter loaded, on
    stderr, for the process that actually ran.
    """
    e = dict(os.environ)
    e["ACTIVITY_SPOOL_DIR"] = str(env["spool"])
    e["HOME"] = str(env["home"])
    r = subprocess.run([sys.executable, "-X", "importtime", script],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=e, timeout=_HOOK_TIMEOUT_SECONDS)
    assert r.returncode == 0, r.stderr
    out = set()
    for line in r.stderr.splitlines():
        if line.startswith("import time:"):
            out.add(line.rsplit("|", 1)[-1].strip())
    # The instrument's own positive control: `-X importtime` reports EVERY import, so a
    # run that parsed nothing (a format change, a flag that stopped working) would hand
    # back an empty set and make every absence assertion below vacuously true.
    assert "json" in out, sorted(out)
    return out


def test_a_post_tool_use_call_does_not_IMPORT_the_emitter(env):
    """🔴 A COST THE ROW COUNT CANNOT SEE — and it was live at `4e384b6f`.

    REGRESSION, not an invariant guard. `0a8034ae` called `_emit_telemetry` from inside
    `elif event == "Stop":`; the round-1 fix moved the call out to its own handler so a
    raising verdict path could not swallow the telemetry, and `main()` then reached it
    on EVERY event. MEASURED at `4e384b6f`: a PostToolUse payload left
    `'hook_telemetry' in sys.modules` True. Nothing went wrong — `telemetry_rows`
    returns `[]` there, so no row was written and
    `test_a_post_tool_use_call_writes_no_row` stayed green — the import was simply paid
    after every tool call of every session, at +0.3-1.6 ms against a path measured at
    ~0.1 ms, by a hook whose own `_stop_token` docstring refuses an `os.urandom(8)`
    (0.0002 ms) on it as too expensive.

    The fix is `_emit_telemetry`'s `if not rows: return 0`. The Stop half below is the
    POSITIVE CONTROL and is not optional: an emitter that had simply stopped being
    importable would satisfy the first assertion while shipping nothing at all.
    """
    doc = arm_guard(env)
    hot = _imported_modules(GUARD, {"hook_event_name": "PostToolUse",
                                    "session_id": SESSION_A, "tool_name": "Read",
                                    "tool_input": {"file_path": str(doc)},
                                    "cwd": str(doc.parent)}, env)
    assert "hook_telemetry" not in hot and "spool_emit" not in hot, sorted(hot)

    stop = _imported_modules(GUARD, {"hook_event_name": "Stop",
                                     "session_id": SESSION_A}, env)
    assert "hook_telemetry" in stop and "spool_emit" in stop, sorted(stop)


def test_a_subagent_stop_is_recorded_as_refused_not_dropped(env):
    """The refusals are the DENOMINATOR, so they are rows — with a reason. A row set
    holding only fires is a numerator wearing a rate, which is the exact shape that
    produced the bogus 100.0%."""
    r = run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A,
                         "agent_id": "agent-telem-4b"}, env)
    assert r.returncode == 0 and r.stdout == ""
    p = payloads(env["spool"], "handoff-write-guard")[0]
    assert p["decision"] == "suppressed" and p["reason"] == "subagent"
    assert p["entity_kind"] == "session" and p["satisfied"] is None


# --------------------------------------------------------------------------- #
# 4b. THE UNMEASURABLE STOP IS STILL A ROW — REGRESSION, red at 0a8034ae
#
# 🔴 WHY THIS BLOCK IS THE MOST LOAD-BEARING IN THE FILE. At 0a8034ae the guard's
# `_emit_telemetry(rows)` sat inside the same `try` as `json.load(sys.stdin)` and the
# whole verdict path, under one `except Exception: pass`. Anything that raised first
# took the telemetry with it, so the Stop wrote NO row — and the population that
# disappeared is exactly the UNMEASURABLE one. A compliance rate computed from the
# surviving rows then reads clean over a guard that was breaking: a numerator with no
# denominator, which is the defect this whole module exists to remove, reintroduced one
# level up. Measured then, with ACTIVITY_SPOOL_DIR pointed at a scratch dir:
#
#     malformed stdin -> handoff-write-guard.py   rc=0  rows=0   <- the row vanished
#     malformed stdin -> next-step-nudge.py       rc=0  rows=1   <- does it right
#     well-formed Stop -> handoff-write-guard.py  rc=0  rows=1   <- positive control
#
# Every test below is RED at 0a8034ae and green at HEAD.
# --------------------------------------------------------------------------- #
HOSTILE_PAYLOADS = {
    "not-json": "not json at all",
    "json-list": '["a","b"]',            # `(data or {}).get` -> AttributeError
    "empty-list": "[]",                  # falsy, so `.get` does NOT raise — still bad
    "json-null": "null",
    "json-string": '"a bare string"',
}


def run_hook_raw(script, raw, env, extra_env=None):
    """Drive a hook with RAW stdin bytes — `run_hook` JSON-encodes, which cannot
    produce the malformed payloads this block is about."""
    e = dict(os.environ)
    e.update(extra_env or {})
    return subprocess.run([sys.executable, script], input=raw, capture_output=True,
                          text=True, env=e, timeout=_HOOK_TIMEOUT_SECONDS)


@pytest.mark.parametrize("script,hook", [(GUARD, "handoff-write-guard"),
                                         (NUDGE, "next-step-nudge")])
@pytest.mark.parametrize("case", sorted(HOSTILE_PAYLOADS))
def test_an_unreadable_payload_is_a_ROW_not_a_silence(env, script, hook, case):
    """🔴 THE REGRESSION. Both hooks, every shape of payload that cannot be decided on.

    The assertion is on the discriminating FIELDS, not on "a row exists": a row that
    arrived saying `suppressed` would be worse than none, because it would add a clean
    observation to the denominator for a Stop nobody could measure."""
    r = run_hook_raw(script, HOSTILE_PAYLOADS[case], env)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    got = payloads(env["spool"], hook)
    assert len(got) == 1, (case, got)
    assert got[0]["decision"] == "could-not-measure"
    assert got[0]["measured"] is False
    assert got[0]["reason"] == "bad-payload"


def test_a_readable_stop_is_still_exactly_one_row(env):
    """POSITIVE CONTROL for the parametrized test above, and it is not optional: a
    guard that emitted a `bad-payload` row for EVERY invocation would satisfy every
    assertion up there while destroying the denominator it exists to protect."""
    r = run_hook_raw(GUARD, json.dumps({"hook_event_name": "Stop",
                                        "session_id": SESSION_B}), env)
    assert r.returncode == 0
    got = payloads(env["spool"], "handoff-write-guard")
    assert len(got) == 1 and got[0]["reason"] == "no-state", got


def test_a_post_tool_use_call_STILL_writes_no_row_after_the_fallback_was_added(env):
    """NEGATIVE CONTROL for the fallback. The `bad-payload` arm fires on an unreadable
    payload REGARDLESS of event — the event name is the thing that could not be read —
    so the obvious way to get it wrong is to widen it into the PostToolUse path, which
    fires after every tool call of every session. A readable PostToolUse payload must
    still be silent."""
    arm_guard(env)
    assert rows(env["spool"]) == []


def test_the_guard_emits_a_row_when_the_DECISION_PATH_raises(env, monkeypatch):
    """🔴 THE THIRD TRIGGER, AND IT IS NOT REACHABLE THROUGH STDIN. A defect inside
    `stop_decision` was swallowed by the same handler, so a Stop that broke MIDWAY
    recorded nothing. Reached directly, the way the nudge's own backstop test does.

    The row is `hook-raised`, not `bad-payload`: the payload was fine and the hook was
    not. Folding them into one token would report an input problem for a code defect.
    """
    import io
    mod = load(GUARD, "guard_raising_main")

    def boom(*a, **k):
        raise RuntimeError("simulated defect inside the decision path")

    monkeypatch.setattr(mod, "stop_decision", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"hook_event_name": "Stop", "session_id": SESSION_A})))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    got = payloads(env["spool"], "handoff-write-guard")
    assert len(got) == 1, got
    assert got[0]["decision"] == "could-not-measure"
    assert got[0]["measured"] is False and got[0]["reason"] == "hook-raised"
    assert got[0]["_session"] == SESSION_A


def test_a_PARTIAL_stop_keeps_the_decisions_it_reached_and_marks_the_break(env,
                                                                          monkeypatch):
    """A raise AFTER some decisions were recorded. Those rows are real observations and
    must survive; the break gets its own row beside them, so a partial Stop can never
    be counted as a complete one. Both halves asserted — keeping the rows without
    marking the break, or marking it while dropping them, each passes half of this."""
    mod = load(GUARD, "guard_partial_main")
    real = mod.stop_decision

    def half(data, rows=None, stop=None):
        real({"hook_event_name": "Stop", "session_id": SESSION_A, "agent_id": "x"},
             rows=rows, stop=stop)
        raise RuntimeError("simulated defect after one decision was recorded")

    import io
    monkeypatch.setattr(mod, "stop_decision", half)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"hook_event_name": "Stop", "session_id": SESSION_A})))
    with pytest.raises(SystemExit):
        mod.main()
    got = payloads(env["spool"], "handoff-write-guard")
    assert [p["reason"] for p in got] == ["subagent", "hook-raised"], got
    # Same Stop, so the same `stop` token — this is what makes the pair countable as
    # ONE Stop rather than two.
    assert got[0]["stop"] == got[1]["stop"] and got[0]["stop"]


def test_a_dismiss_run_is_not_a_stop_and_writes_no_row(env, tmp_path):
    """NEGATIVE CONTROL for the fallback's `cli` arm. `--dismiss` reads no stdin and
    decides no event; a row there would invent a Stop that never happened and inflate
    every denominator by however often the escape hatch is used."""
    r = subprocess.run([sys.executable, GUARD, "--dismiss", "handoff-x.md",
                        "--session", SESSION_A], capture_output=True, text=True,
                       env=dict(os.environ), timeout=_HOOK_TIMEOUT_SECONDS)
    assert r.returncode == 0 and "handoff write-back guard" in r.stdout
    assert rows(env["spool"]) == []


@pytest.mark.parametrize("data,rows_in,completed,cli,want", [
    (None, None, False, False, ["bad-payload"]),          # stdin never parsed
    (["a"], None, False, False, ["bad-payload"]),         # a JSON list
    ("str", None, False, False, ["bad-payload"]),
    (None, None, False, True, []),                        # --dismiss
    ({"hook_event_name": "PostToolUse"}, None, True, False, []),
    ({"hook_event_name": "Stop"}, [], True, False, []),    # decided, nothing to report
    ({"hook_event_name": "Stop"}, [], False, False, ["hook-raised"]),
])
def test_the_fallback_mapping_is_pure_and_total(data, rows_in, completed, cli, want):
    """The mapping alone, with no stdin, no spool and no subprocess — the same split
    `next-step-nudge.decision_for` gets, and for the same reason: the mapping IS the
    semantic content, so it should be testable without any of the machinery."""
    mod = load(GUARD, "guard_fallback_map")
    got = mod.telemetry_rows(data, rows_in, completed=completed, cli=cli)
    assert [r["reason"] for r in got] == want, got
    for row in got:
        assert row["decision"] == "could-not-measure" and row["measured"] is False


# --------------------------------------------------------------------------- #
# 4c. THE CALL SITE'S HALF OF THE PRIVACY BOUNDARY
# --------------------------------------------------------------------------- #
# 🔴 `_SAFE_TOKEN` REJECTS PROSE AND ADMITS LAUNDERED PROSE, AND THE GUARD USED TO
# LAUNDER. `_is_handoff_basename` is `HANDOFF_BASENAME_RX`,
# `(?:^handoff-.*\.md$)|(?:^.*HANDOFF.*\.md$)` — two arms, the second needing no
# prefix, and `.*` in both matches spaces — and the `Read` arm of `handoff_read_docs`
# takes `tool_input.file_path` VERBATIM (only the `Bash` arm goes through
# `HANDOFF_PATH_RX`). `doc_key` then ran `_sanitize`, which maps every disallowed
# character to `_`, producing exactly the shape the emitter admits. Measured at
# 0a8034ae, end to end through the real hook.
#
# ⚠ WHAT THE FIX BOUNDS IS THE REWRITING, AND THESE TESTS ASSERT ONLY THAT. A name that
# never needed laundering — any `snake_case` or `kebab-case` basename, which is how
# this repo names its own handoffs — is still admitted whole, by design and as the
# common case. `hook_telemetry`'s promise 3(c) states that residual; nothing below
# should be read as coverage of it.
# --------------------------------------------------------------------------- #
PROSE_DOC_NAME = ("handoff- acme corp wants the refund before friday, "
                  "escalate to legal.md")


def arm_guard_with_name(env, basename, session):
    """`arm_guard`, but with the doc's basename chosen by the caller."""
    docdir = env["tmp"] / ("repo-" + session) / "claudedocs"
    docdir.mkdir(parents=True, exist_ok=True)
    doc = docdir / basename
    doc.write_text("resumed\n")
    run_hook(GUARD, {"hook_event_name": "PostToolUse", "session_id": session,
                     "tool_name": "Read", "tool_input": {"file_path": str(doc)},
                     "cwd": str(docdir)}, env)
    run_hook(GUARD, {"hook_event_name": "PostToolUse", "session_id": session,
                     "tool_name": "Bash",
                     "tool_input": {"command": "git commit -m x"},
                     "cwd": str(docdir)}, env)
    os.utime(str(doc), (1_000_000, 1_000_000))
    return doc


def test_a_LAUNDERED_doc_name_cannot_reach_the_payload(env):
    """🔴 REGRESSION, red at 0a8034ae — the full click path, not the helper. The whole
    sentence arrived in `entity` as one underscore-joined token."""
    arm_guard_with_name(env, PROSE_DOC_NAME, SESSION_A)
    r = run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env)
    assert json.loads(r.stdout)["decision"] == "block"   # the VERDICT is unchanged
    body = decoded_line_text(env["spool"])
    for word in ("acme", "corp", "refund", "friday", "escalate", "legal"):
        assert word not in body, (word, body)
    p = payloads(env["spool"], "handoff-write-guard")[0]
    # The row still EXISTS and still counts — a dropped entity is not a dropped Stop.
    assert p["decision"] == "fired" and p["entity"] is None
    assert p["unnameable-entity"] is True


def test_an_ORDINARY_doc_name_still_carries_its_key(env):
    """🔴 POSITIVE CONTROL, and it is the whole reason the fix is a round-trip check
    rather than a blanket drop. A boundary that dropped every entity would pass the
    test above and destroy the per-entity key this module was built to add — the exact
    absence that made a lift figure wrong by 4.8pp."""
    arm_guard_with_name(env, "handoff-%s.md" % DOC_TOPIC, SESSION_B)
    run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_B}, env)
    p = payloads(env["spool"], "handoff-write-guard")[0]
    assert p["entity"] == "handoff-%s.md" % DOC_TOPIC
    assert "unnameable-entity" not in p


@pytest.mark.parametrize("basename,admitted", [
    ("handoff-alpha.md", True),
    ("SESSION-HANDOFF.md", True),
    ("handoff-a b.md", False),              # the space `.*` lets through
    ("handoff-a,b.md", False),
    ("handoff-a%20b.md", False),            # `%` is in HANDOFF_PATH_RX, not in the key
    ("handoff-" + "x" * 130 + ".md", False),   # truncated at 120 -> not the name
])
def test_telemetry_entity_admits_only_a_key_that_was_never_rewritten(basename,
                                                                     admitted):
    """The predicate alone. 🔴 The test is NOT "does the key look safe" — a laundered
    key always does — it is "was the key a REWRITING of the name"."""
    mod = load(GUARD, "guard_entity_pred")
    key = mod.doc_key(basename)
    rec = {"doc": "/somewhere/claudedocs/" + basename}
    got = mod.telemetry_entity(key, rec)
    assert (got is not None) is admitted, (basename, key, got)
    if admitted:
        assert got == basename


def test_telemetry_entity_refuses_a_record_it_cannot_check(env):
    """A ledger record with no usable `doc` cannot be checked against its own name, so
    the key is not admitted. The quiet direction: an entity nobody can verify is worth
    less than the guarantee it would cost."""
    mod = load(GUARD, "guard_entity_norec")
    assert mod.telemetry_entity("handoff-x.md", {}) is None
    assert mod.telemetry_entity("handoff-x.md", None) is None
    assert mod.telemetry_entity("handoff-x.md", {"doc": 7}) is None
    # ...and the key must correspond to the record it is filed under.
    assert mod.telemetry_entity("handoff-y.md",
                                {"doc": "/c/claudedocs/handoff-x.md"}) is None


# --------------------------------------------------------------------------- #
# 4d. NOTHING BLOCKS — a hang is strictly worse than not existing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("script,expect", [(NUDGE, "hookSpecificOutput"),
                                           (GUARD, "decision")])
def test_a_FIFO_spool_does_not_hang_the_hook(env, tmp_path, script, expect):
    """🔴 REGRESSION for the contract's blocking half, red at 0a8034ae.

    `spool_emit.emit` opens `current.log` with a blocking `open(..., "a")`. On a FIFO
    with no reader that call NEVER RETURNS — measured at 0a8034ae, the hook had not
    exited after 12 s — so the operator's turn never ends. The module's contract said
    the worst case was "byte-identical to this module not existing" and enumerated
    raising, printing, spawning and socket-opening; it did not say *blocking*, and a
    hang is strictly worse than every case it did name.

    The timeout is the assertion: `subprocess.run` raises `TimeoutExpired` at
    0a8034ae, which is a red test rather than a wedged suite."""
    fifo_dir = tmp_path / ("fifo-" + os.path.basename(script))
    fifo_dir.mkdir()
    os.mkfifo(str(fifo_dir / "current.log"))
    if script is GUARD:
        arm_guard(env)
    payload = ({"hook_event_name": "Stop", "session_id": SESSION_A}
               if script is GUARD else firing_stop(env))
    r = run_hook(script, payload, env, {"ACTIVITY_SPOOL_DIR": str(fifo_dir)},
                 timeout=15)
    assert r.returncode == 0
    assert expect in json.loads(r.stdout)      # the verdict is unchanged
    assert r.stderr == ""
    assert rows(env["spool"]) == []            # nothing fell back to the good spool


def _call_bounded(seconds, fn, *a, **kw):
    """Run `fn` with a hard wall-clock bound, IN THIS PROCESS. Raises on overrun.

    🔴 THIS EXISTS BECAUSE THE TEST BELOW WEDGED A MUTATION SWEEP. Asserting
    `emit_decision(..., spool_dir=<a FIFO>) == ""` in process is safe only while the
    refusal is in place — and a mutation sweep's whole job is to remove it. With the
    call site deleted, `spool_emit`'s blocking `open` never returned and pytest sat
    there forever: not a SURVIVED mutant, not a KILLED one, just a run that never
    finished, which is the least informative outcome available and the one that gets
    read as "the harness is broken". `subprocess.run(timeout=)` covers the hooks
    because they are children; this covers the in-process calls, which have no parent
    to time them out.

    SIGALRM is process-wide and pytest is single-threaded here, so the window is this
    call and nothing else; the alarm is always cancelled in a `finally`.
    """
    import signal

    def _boom(signum, frame):
        raise TimeoutError("call did not return within %ss — it BLOCKED" % seconds)

    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*a, **kw)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_the_fifo_refusal_is_the_ONLY_thing_that_changed(env, HT, tmp_path):
    """POSITIVE CONTROL for the refusal. A guard that refused EVERY spool would pass
    the test above having disabled the emitter entirely — the same reassuring zero as a
    scanner wired to nothing. The pair: a FIFO writes 0, a regular file writes 1, and
    the predicate is asserted directly in both directions.

    🔴 THE `current.log` MUST ALREADY EXIST BEFORE THE ADMITTING ASSERTION, AND THE
    FIRST DRAFT DID NOT DO THAT — the mutation sweep caught it. A spool whose log has
    not been created yet takes the `except OSError: return True` arm, so
    `_spool_target_is_a_regular_file` returns True **without ever reaching the
    `S_ISREG` line**. A mutant replacing that line with `return False` therefore
    SURVIVED this test: every assertion held, because the branch under test was never
    executed. It is the enclosing-condition trap from `claude/RULES.md` — the fixture's
    own state kept the guard's assertion unreachable. So the emit that CREATES the log
    comes first, and the admitting assertion is made against a file proven to exist.

    Every in-process emit here is BOUNDED — see `_call_bounded` for the sweep this
    cost."""
    se = HT.spool_emit_module()
    assert se is not None

    fifo_dir = tmp_path / "fifo-pred"
    fifo_dir.mkdir()
    os.mkfifo(str(fifo_dir / "current.log"))
    assert HT._spool_target_is_a_regular_file(se, fifo_dir) is False
    assert _call_bounded(10, HT.emit_decision, "h", "fired",
                         spool_dir=fifo_dir) == ""

    # The ABSENT arm — a log that does not exist yet must proceed, since that is the
    # ordinary first emit and not an edge case.
    assert HT._spool_target_is_a_regular_file(se, tmp_path / "never-created") is True
    assert _call_bounded(10, HT.emit_decision, "h", "fired", entity="e1",
                         spool_dir=env["spool"]) != ""

    # ...and NOW the S_ISREG arm itself, on a log proven to be a real regular file.
    assert (env["spool"] / "current.log").is_file()
    assert HT._spool_target_is_a_regular_file(se, env["spool"]) is True
    assert _call_bounded(10, HT.emit_decision, "h", "fired", entity="e2",
                         spool_dir=env["spool"]) != ""
    assert len(rows(env["spool"])) == 2


def test_the_in_process_bound_can_itself_go_red(tmp_path):
    """🔴 NEGATIVE CONTROL FOR `_call_bounded`. A timeout helper that silently never
    fires turns the test above back into the thing that wedged the sweep, with every
    assertion still green. Feed it a call that MUST overrun and watch it raise."""
    import time
    with pytest.raises(TimeoutError):
        _call_bounded(0.2, time.sleep, 5)
    # ...and it must not fire on a call that returns in time, or it would fail the
    # suite for reasons that have nothing to do with blocking.
    assert _call_bounded(5, lambda: "fast") == "fast"


# --------------------------------------------------------------------------- #
# 4e. EVERY DROP IS COUNTED — the module's only promise about dropping
# --------------------------------------------------------------------------- #
def test_extras_over_the_cap_are_COUNTED_not_silently_truncated(env, HT):
    """🔴 REGRESSION, red at 0a8034ae. `fields.extend(extras[:_MAX_EXTRA])` truncated
    AFTER `dropped` had been computed, so the keys past the cap vanished with no trace
    — the one place this module dropped a value silently, against the single promise it
    makes about drops. A row over the cap was byte-identical to a caller that never
    passed those keys."""
    n = HT._MAX_EXTRA
    extra = {"k%02d" % i: i for i in range(n + 3)}
    HT.emit_decision("h", HT.DECISION_SUPPRESSED, extra=extra,
                     spool_dir=env["spool"])
    p = payloads(env["spool"])[0]
    assert p["dropped"] == 3, p
    assert sum(1 for k in p if k.startswith("k")) == n


def test_an_extra_at_exactly_the_cap_drops_nothing(env, HT):
    """The boundary from the other side — the control that keeps the count above from
    being satisfied by an off-by-one that over-reports."""
    HT.emit_decision("h", HT.DECISION_SUPPRESSED,
                     extra={"k%02d" % i: i for i in range(HT._MAX_EXTRA)},
                     spool_dir=env["spool"])
    assert "dropped" not in payloads(env["spool"])[0]


# --------------------------------------------------------------------------- #
# 4f. THE DENOMINATOR UNIT — the guard emits one row per DECISION, not per Stop
# --------------------------------------------------------------------------- #
def test_every_row_of_ONE_stop_shares_a_stop_id(env):
    """🔴 WITHOUT THIS THE GUARD HAS NO DENOMINATOR OF ITS OWN. It emits one row per
    tracked DOC, so `count(*)` counts documents and any rate built on it is wrong by
    however many handoffs the session had open. `uniqExact(stop)` is the Stop count.

    Two docs in one Stop, then a SECOND Stop: the first two must share a token and the
    third must not, which is the pair that separates a real id from a constant."""
    docdir = env["tmp"] / "multi" / "claudedocs"
    docdir.mkdir(parents=True)
    for topic in ("alpha", "beta"):
        doc = docdir / ("handoff-%s.md" % topic)
        doc.write_text("resumed\n")
        run_hook(GUARD, {"hook_event_name": "PostToolUse", "session_id": SESSION_A,
                         "tool_name": "Read", "tool_input": {"file_path": str(doc)},
                         "cwd": str(docdir)}, env)
        os.utime(str(doc), (1_000_000, 1_000_000))
    run_hook(GUARD, {"hook_event_name": "PostToolUse", "session_id": SESSION_A,
                     "tool_name": "Bash",
                     "tool_input": {"command": "git commit -m x"},
                     "cwd": str(docdir)}, env)

    run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env)
    first = payloads(env["spool"], "handoff-write-guard")
    assert len(first) == 2, first                  # one row per DOC, not per Stop
    assert first[0]["stop"] == first[1]["stop"] and first[0]["stop"]

    run_hook(GUARD, {"hook_event_name": "Stop", "session_id": SESSION_A}, env)
    allrows = payloads(env["spool"], "handoff-write-guard")
    assert len({p["stop"] for p in allrows}) == 2, [p["stop"] for p in allrows]


def test_the_nudge_carries_no_stop_id_because_its_row_IS_the_stop(env):
    """The asymmetry, asserted rather than left to a comment. A field that would only
    ever hold distinct values buys nothing, and a consumer must be able to read the
    absence as "one row per Stop" instead of as an oversight."""
    run_hook(NUDGE, firing_stop(env), env)
    assert "stop" not in payloads(env["spool"], "next-step-nudge")[0]


# --------------------------------------------------------------------------- #
# 5. Ledger guards — three files spell the vocabulary, nothing else makes them agree
# --------------------------------------------------------------------------- #
def source(path):
    return open(path, encoding="utf-8").read()


def literal_set(src, name):
    """The string literals assigned to `<name> = frozenset({...})` / `(...)`."""
    m = re.search(r"^%s\s*=\s*frozenset\(\{(.*?)\}\)" % re.escape(name),
                  src, re.M | re.S)
    assert m, name
    return set(re.findall(r'"([^"]*)"', m.group(1)))


def test_the_decision_vocabulary_is_pinned_two_way(HT):
    """🔴 THREE FILES SPELL IT. `hook_telemetry` owns the set; both hooks spell the
    four values as literals so a pure decision function still works on a host with no
    collector. Nothing but this makes them agree — and it fails when either side grows
    or shrinks."""
    assert HT.DECISIONS == {"fired", "armed", "suppressed", "could-not-measure"}
    for path in (NUDGE, GUARD):
        src = source(path)
        spelled = set(re.findall(r'^DECISION_[A-Z]+ = "([^"]+)"', src, re.M))
        assert spelled == HT.DECISIONS, (path, sorted(spelled))


def test_the_nudge_reason_vocabulary_is_pinned_two_way_against_decide():
    """🔴 EVERY `no("<token>")` BRANCH, AGAINST THE DECLARED SET. A consumer's
    `WHERE reason IN (…)` silently drops a whole population the day a gate is added
    and nobody widens the vocabulary; this is what charges that."""
    src = source(NUDGE)
    produced = set(re.findall(r'\bno\("([a-z-]+)"', src))
    produced |= {"already-fired", "claim-lost", ""}   # set by main()'s claim arm
    mod = load(NUDGE, "nudge_vocab")
    assert produced, "the parser found no branches — it is wired to nothing"
    assert produced == mod.REASONS, (
        "unaccounted: %s ; stale: %s"
        % (sorted(produced - mod.REASONS), sorted(mod.REASONS - produced)))
    assert mod.ARMED_REASONS <= mod.REASONS
    assert mod.UNMEASURED_REASONS <= mod.REASONS
    assert mod.PRE_SUPPRESSOR_REASONS <= mod.REASONS
    assert mod.ARMED_REASONS.isdisjoint(mod.UNMEASURED_REASONS)


# Which positional argument of each row builder carries the reason token.
_ROW_BUILDERS = {"_session_row": 2, "_doc_row": 5}
# The one name a builder is legitimately called with that is NOT a token: `refuse`'s
# own parameter, which this parser reads from the `refuse("…")` call sites instead.
_REASON_PASS_THROUGH = frozenset({"reason"})


def guard_reason_tokens(path):
    """Every reason token the guard hands to a telemetry row builder. By AST.

    🔴 THIS PARSER WAS A REGEX AND THE REGEX WAS THE BUG. It matched
    `DECISION_[A-Z]+, "<token>"` — adjacency on ONE LINE — so wrapping a call across
    two lines silently dropped that call site from `produced`, and the ledger then
    reported the token as STALE: a pin failing for a formatting change while a genuinely
    missing token would fail identically. `claude/RULES.md` → "parsing output makes its
    FORMAT a dependency you did not pin". The AST has no format.

    Module-level string constants are resolved, so a token spelled as `BAD_PAYLOAD` is
    found without this file carrying a second list of constant names to keep in step.
    🔴 An argument this cannot resolve is a FAILURE, never a skip — a silently ignored
    call site is exactly how a token leaves the ledger without anyone noticing.
    """
    tree = ast.parse(source(path), filename=path)
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = node.value.value

    out, unresolved = set(), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        if name == "refuse":
            arg = node.args[0] if node.args else None
        elif name in _ROW_BUILDERS:
            idx = _ROW_BUILDERS[name]
            arg = node.args[idx] if len(node.args) > idx else None
        else:
            continue
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.add(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in consts:
            out.add(consts[arg.id])
        elif isinstance(arg, ast.Name) and arg.id in _REASON_PASS_THROUGH:
            continue
        else:
            unresolved.append("%s(line %d): %s" % (name, node.lineno,
                                                   ast.dump(arg) if arg else "MISSING"))
    return out, unresolved


def test_the_guards_reason_vocabulary_is_pinned_two_way():
    """The same ledger on the other hook: every token handed to `_session_row` or
    `_doc_row`, against the declared set."""
    produced, unresolved = guard_reason_tokens(GUARD)
    mod = load(GUARD, "guard_vocab")
    assert not unresolved, (
        "a telemetry row builder is called with a reason this pin cannot resolve, so "
        "its token is outside the ledger: %s" % unresolved)
    assert produced, "the parser found no call sites — it is wired to nothing"
    assert produced == mod.REASONS, (
        "unaccounted: %s ; stale: %s"
        % (sorted(produced - mod.REASONS), sorted(mod.REASONS - produced)))
    assert mod.UNMEASURED_SESSION_REASONS <= mod.REASONS


def test_that_ledgers_parser_can_see_a_call_the_regex_version_missed():
    """🔴 NEGATIVE CONTROL FOR THE PARSER ITSELF — the half that makes the pin above
    evidence rather than a claim. A ledger built by a parser that silently skips call
    sites reports a clean set over an incomplete one, and the previous regex did
    exactly that: it required `DECISION_X, "token"` on ONE line.

    Both shapes are fed to the real parser here: the wrapped call the regex could not
    see, and a token spelled as a module constant, which it also could not see. If
    either comes back missing, the ledger is measuring less than it claims to.
    """
    import tempfile
    src = (
        'A_CONSTANT_TOKEN = "via-constant"\n'
        'def f(rows, sid, key, rec):\n'
        '    _doc_row(rows, sid, key, rec, DECISION_FIRED,\n'
        '             "wrapped-across-lines", satisfied=False)\n'
        '    _session_row(rows, sid, A_CONSTANT_TOKEN)\n'
        '    _session_row(rows, sid, "plain-literal")\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(src)
        probe = fh.name
    try:
        got, unresolved = guard_reason_tokens(probe)
        assert not unresolved, unresolved
        assert got == {"wrapped-across-lines", "via-constant", "plain-literal"}, got
        # And the shape it must REFUSE rather than skip: a reason it cannot resolve.
        with open(probe, "a") as fh:
            fh.write('    _session_row(rows, sid, some_unknown_name)\n')
        _, unresolved2 = guard_reason_tokens(probe)
        assert unresolved2, "an unresolvable reason must fail, not be silently dropped"
    finally:
        os.unlink(probe)


def test_this_directorys_conftest_is_a_spool_guard_entry_point():
    """🔴 THE LEAK THIS CHANGE CAUSED IS PINNED BY THIS TEST AND BY NOTHING ELSE.

    Several suites in this directory drive a hook's `main()` IN PROCESS, so a row lands
    wherever the process resolves the spool — and `spool_emit.default_spool_dir()` falls
    back to `${XDG_STATE_HOME:-~/.local/state}/activity/spool`, which the collector ships
    to the operator's production ClickHouse. MEASURED on this branch: `pytest
    test_next_step_nudge.py -k main` with `ACTIVITY_SPOOL_DIR` unset wrote 2 real rows.
    `scripts/run-tests.sh` exports the variable for every target (GUARD 8), so the gate
    never saw it; a bare `pytest` — the documented way to run one of these files — did.

    Deleting or narrowing the conftest's wiring would reopen that with every test in
    this directory still green, which is the definition of a fix pinned by nothing.

    🔴 STRUCTURAL, NOT SPELLED — copied from
    `test_git_repo_isolation.py::test_every_conftest_is_a_second_entry_point`, whose own
    docstring records that a spelled version SURVIVED a mutant moving the import under
    `if False:`: both words were still on the page. What pytest acts on is the module
    OBJECT's attribute, and it must be the plugin's own function, not a same-named
    look-alike.

    ⚠ SCOPE: this pins THIS directory, the one this change touched. Four of the six
    conftests under `scripts/` still have no GUARD 8 second entry point; widening that
    is a change of its own and is deliberately not smuggled in here.
    """
    import importlib.util

    if os.path.join(ROOT, "scripts") not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from testlib import spool_plugin            # noqa: PLC0415

    path = os.path.join(HERE, "conftest.py")
    spec = importlib.util.spec_from_file_location("_devrc_hook_conftest_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    got = getattr(module, "no_real_activity_spool", None)
    assert got is spool_plugin.no_real_activity_spool, (
        "scripts/claude-hooks/tests/conftest.py does not re-export "
        "`no_real_activity_spool` from testlib.spool_plugin, so a bare "
        "`pytest scripts/claude-hooks/tests/...` runs with no session-wide spool "
        "floor and no per-target marker (found: %r)" % (got,))

    # The per-test narrowing fixture is defence in depth beside that floor, and its
    # absence is a real narrowing of protection — asserted as an OBJECT for the same
    # reason as above.
    narrow = getattr(module, "_devrc_hook_spool_isolation", None)
    assert callable(narrow), (
        "the per-test spool-narrowing fixture is gone from this directory's conftest")


def test_the_emitter_is_declared_as_a_hook_library_module():
    """🔴 THE DELIVERY SEAM. A library module beside the hooks must be in the
    registrar's `HOOK_LIBRARY_MODULES` (or the registrar's two-way pin against
    `nix/home.nix` fails), and it must have a `home.file` entry or the flake ships two
    hooks importing a module that is not there — the #452 shape, every component
    tested and the seam owned by nobody."""
    reg = source(os.path.join(HOOKS, "register-nudge-hook.py"))
    libraries = literal_set(reg, "HOOK_LIBRARY_MODULES")
    assert "guard_core.py" in libraries, libraries   # positive control: parser works
    assert "hook_telemetry.py" in libraries, sorted(libraries)
    nix = source(os.path.join(ROOT, "nix", "home.nix"))
    assert 'home.file.".claude/hooks/hook_telemetry.py"' in nix
    assert "../scripts/claude-hooks/hook_telemetry.py" in nix


def test_the_emitter_reaches_spool_emit_in_BOTH_deployed_layouts(env, HT,
                                                                monkeypatch):
    """🔴 TWO LAYOUTS, AND THE HOST ONE IS THE ONE NO TEST WOULD OTHERWISE EXERCISE.
    In the repo the collector is `../collector/keylog`; on the host this file is a
    /nix/store symlink in `~/.claude/hooks/` and the collector is at
    `~/.config/activity-collector/keylog/`. A resolver that only ever ran in the repo
    would ship a module that is inert on both machines and green here."""
    repo_dir, home_dir = HT._candidate_dirs()
    assert repo_dir == os.path.join(COLLECTOR, "keylog")
    assert os.path.isfile(os.path.join(repo_dir, "spool_emit.py"))
    assert home_dir == os.path.join(str(env["home"]), ".config",
                                    "activity-collector", "keylog")

    # Now make ONLY the host layout real, and require the emit to land.
    os.makedirs(home_dir)
    with open(os.path.join(home_dir, "spool_emit.py"), "w") as fh:
        fh.write(open(os.path.join(repo_dir, "spool_emit.py")).read())
    monkeypatch.setattr(HT, "_candidate_dirs", lambda: [home_dir])
    monkeypatch.setattr(HT, "_SE", HT._UNRESOLVED)
    assert HT.emit_decision("h", "fired", entity="e", spool_dir=env["spool"]) != ""
    assert len(rows(env["spool"])) == 1


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
