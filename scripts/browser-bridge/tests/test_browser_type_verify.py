"""`browser type --expect/--verify` — HEADLESS.

Fully offline: a loopback stub HTTP server stands in for server.py (the same
pattern as ``test_browser_cli_args.py``), so no Brave, no extension, no bridge.
It records every /cmd body and answers a scripted envelope per op, which is what
lets these tests exercise the RETRY loop — the stub can miss twice and then hit.

🔴 WHAT THESE TESTS DO **NOT** COVER. They pin the CLI's control flow against a
stub. They cannot observe the defect that motivated `--expect`: a real
``Input.insertText`` landing in a real renderer and then being overwritten by a
framework re-render. ``reference/security-ops.md`` makes live-verify-on-real-
Brave the mandatory gate for any browser-bridge change precisely because a green
suite here is a prerequisite, never verification. The live commands are listed in
the PR body.

🔴 TWO FIXTURE DIMENSIONS THAT USED TO BE PINNED, AND WHAT THAT COST
--------------------------------------------------------------------
An earlier revision of this file could not see two 🔴 defects in the code it
tested, and in both cases the reason was the FIXTURE, not the assertions:

1. **The simulated field could only APPEND.** ``on_type`` did ``state["v"] +=
   text`` unconditionally, so the no-apply arm — the exact failure the feature
   exists to detect — was never simulated. The retry's "undo" derived the field's
   prior content by stripping a trailing copy of the typed text off the
   READ-BACK, which is sound only when the insert landed. On a field holding
   ``"my password"``, typed into with ``"password"`` and NOT applied, it
   amputated the value to ``"my "`` and then reported that as the page's content.
   ``Field(initial=..., applies=False)`` below is the arm that sees it, and
   ``applies=True`` is its control.
2. **The settle was pinned to ``0`` for every test.** Per the CLI's own header a
   read-back taken in the same tick as the insert reports success even in the
   failing case, so the whole suite ran with the detector disabled — deleting the
   ``sleep`` line left 51/51 green. The fixture now uses a small but NON-ZERO
   settle, and ``test_the_settle_actually_happens`` measures wall time at two
   points so a deleted or skipped sleep is RED.

Run: nix-shell -p python312Packages.pytest curl --run \\
       "pytest scripts/browser-bridge/tests/test_browser_type_verify.py"
"""
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BB = Path(__file__).resolve().parent.parent
CLI = BB / "browser"

# Small but NON-ZERO: the sleep must really run (and really be validated) in
# every test, while 3 attempts x 20ms stays free. The wall-time assertions live
# in test_the_settle_actually_happens, which overrides this.
TEST_SETTLE_S = "0.02"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None,
    reason="the browser CLI is bash and talks to the bridge with curl")


# --------------------------------------------------------------------------- #
# Stub bridge — scriptable per op, records every body.
# --------------------------------------------------------------------------- #
def _ok(data):
    return {"ok": True, "result": {"id": "c", "ok": True, "data": data}}


@pytest.fixture
def bridge(tmp_path):
    """A stub bridge whose reply is chosen by a per-op QUEUE.

    ``b.script("eval", [r1, r2, ...])`` queues responses for that op; the LAST
    entry repeats once the queue drains, so a test only has to describe the
    interesting prefix. A CALLABLE entry is a stateful responder: it gets the
    request body and returns the envelope, which is what lets ``Field`` below
    simulate an input instead of replaying canned reads.
    """
    scripts: dict = {}
    bodies: list = []

    class _H(BaseHTTPRequestHandler):
        def _reply(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode())
            bodies.append(body)
            op = body.get("op")
            queue = scripts.get(op)
            if queue:
                # Pop until one left, then repeat it — "and thereafter" without
                # making every test spell out a tail of identical entries.
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if callable(item):
                    item = item(body)
            elif op == "type":
                item = _ok({"url": "https://a.test", "typed": 5,
                            "frame": None, "trusted": True})
            else:
                item = _ok({"url": "https://a.test", "value": None})
            code, payload = item if isinstance(item, tuple) else (200, item)
            self._reply(code, payload)

        def do_GET(self):
            self._reply(200, {"ok": True, "instances": []})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tok = tmp_path / "token"
    tok.write_text("type-verify-token\n")
    env = dict(os.environ)
    env.update({
        "BROWSER_BRIDGE_HOST": "127.0.0.1",
        "BROWSER_BRIDGE_PORT": str(srv.server_address[1]),
        "BROWSER_BRIDGE_TOKEN_FILE": str(tok),
        "CLAUDE_CODE_SESSION_ID": "pytest-type-verify",
        "HOME": str(tmp_path),
        "BB_TYPE_SETTLE_S": TEST_SETTLE_S,
    })
    # Never inherit the developer's own targeting defaults — that would make the
    # routing assertions pass or fail on the ambient environment.
    for k in ("BB_INSTANCE", "BB_TAB", "BB_FRAME", "BB_TYPE_ATTEMPTS"):
        env.pop(k, None)

    class _B:
        @staticmethod
        def script(op, responses):
            scripts[op] = list(responses)

        @staticmethod
        def run(*args, **kw):
            e = dict(_B.env)
            e.update(kw.pop("extra_env", {}) or {})
            return subprocess.run(["bash", str(CLI), *args], env=e,
                                  capture_output=True, text=True, timeout=120)

        @staticmethod
        def ops():
            return [b.get("op") for b in _B.bodies]

        @staticmethod
        def evals(kind="read"):
            """The `js` payloads of the eval ops, split by what they DO.

            The CLI marks a restore with a `/*bb-restore*/` prefix precisely so a
            wire trace (and this) can tell the two apart."""
            js = [b["js"] for b in _B.bodies if b.get("op") == "eval"]
            restore = [j for j in js if j.startswith("/*bb-restore*/")]
            return restore if kind == "restore" else [j for j in js
                                                      if j not in restore]

    # Assigned AFTER the class body on purpose: a class body does not close over
    # the enclosing function's scope, so `bodies = bodies` in there is a
    # NameError, not a copy (the same note the sibling fixture in
    # test_browser_cli_args.py carries).
    _B.bodies = bodies
    _B.env = env

    try:
        yield _B
    finally:
        srv.shutdown()
        srv.server_close()


def _value(v):
    """An `eval` envelope carrying the read expression's tagged result."""
    return _ok({"url": "https://a.test", "value": v})


def _data(cp):
    """`.result.data` out of the CLI's stdout, asserting stdout parses at all."""
    return json.loads(cp.stdout)["result"]["data"]


def _restore_target(js):
    """The value a `/*bb-restore*/` eval is putting into the field.

    The CLI splices the target in as a JSON string literal between two sentinel
    comments precisely so a stub can read it back out without parsing JS."""
    return json.loads(js.split("var v=", 1)[1].split("/*bb-end*/", 1)[0])


# --------------------------------------------------------------------------- #
# Op-sequence expectations.
#
# EVERY verified type opens with a read of the field — the pre-state the retry's
# undo and `--verify`'s "did anything change?" both rest on. After that each
# attempt is `type` + a read-back, with a restore `eval` between two attempts
# ONLY when the failed attempt actually changed the field (when it did not, there
# is nothing to undo and the op is skipped).
# --------------------------------------------------------------------------- #
def _ops(n, restores=True):
    per = ["type", "eval", "eval"] if restores else ["type", "eval"]
    return ["eval"] + per * (n - 1) + ["type", "eval"]


class Field:
    """A simulated text input: `type` inserts AT THE CARET (append), the read
    eval reports the current value, a restore eval sets it.

    ``applies`` is the dimension the old fixture pinned to True. ``applies=False``
    is a type that does NOT land — the intermittent failure this whole feature
    exists to detect, and the arm in which deriving the field's prior content
    from the READ-BACK destroys real user content.

    ``restore_sticks=False`` is a page that owns the value and writes its own
    back over any restore.
    """

    def __init__(self, bridge, initial="", applies=False, restore_sticks=True,
                 read_tag=None, pre_read=None):
        self.v = initial
        self.applies = applies
        self.restore_sticks = restore_sticks
        self.read_tag = read_tag      # e.g. {"e": "element_not_found"} post-type
        self.pre_read = pre_read      # override for the FIRST read only
        self.reads = 0
        bridge.script("type", [self._on_type])
        bridge.script("eval", [self._on_eval])
        self.bridge = bridge

    def _on_type(self, body):
        if self.applies:
            self.v += body["text"]
        return _ok({"url": "https://a.test", "typed": len(body["text"]),
                    "frame": None, "trusted": True})

    def _on_eval(self, body):
        js = body["js"]
        if js.startswith("/*bb-restore*/"):
            if self.restore_sticks:
                self.v = _restore_target(js)
            return _value({"v": self.v})
        self.reads += 1
        if self.reads == 1 and self.pre_read is not None:
            return _value(self.pre_read)
        if self.reads > 1 and self.read_tag is not None:
            return _value(self.read_tag)
        return _value({"v": self.v})


# --------------------------------------------------------------------------- #
# 0. the unverified default path is untouched
# --------------------------------------------------------------------------- #
def test_plain_type_is_unchanged_and_makes_exactly_one_request(bridge):
    """INVARIANT GUARD (back-compat), not regression coverage.

    Verification now costs THREE wire ops, so turning it on by default would
    change the behaviour of every existing `type` caller. Pin that the default
    path is still ONE request with no verification fields."""
    cp = bridge.run("type", "hello", "--selector", "#q")
    assert cp.returncode == 0, cp.stderr
    assert bridge.ops() == ["type"]
    assert bridge.bodies[0] == {"op": "type", "text": "hello", "selector": "#q"}
    data = _data(cp)
    assert "applied" not in data and "verify" not in data


# --------------------------------------------------------------------------- #
# 1. --expect — the measured defect
# --------------------------------------------------------------------------- #
def test_expect_match_reports_applied_true_after_one_attempt(bridge):
    bridge.script("eval", [_value({"v": ""}), _value({"v": "hello"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 0, cp.stderr
    assert bridge.ops() == ["eval", "type", "eval"], "pre-read, type, read-back"
    data = _data(cp)
    assert data["applied"] is True
    assert data["verify"]["mode"] == "expect"
    assert data["verify"]["state"] == "ok"
    assert data["verify"]["attempts"] == 1
    # Additive: the extension's own fields survive the annotation.
    assert data["typed"] == 5 and data["trusted"] is True


def test_expect_retries_the_whole_type_and_succeeds_on_the_second(bridge):
    """THE POINT OF THE FLAG. The first read-back shows the page's STALE value —
    the measured failure — and the CLI re-types rather than reporting success.

    The stale read equals the PRE-STATE, i.e. the insert did not land at all, so
    there is nothing to undo and no restore op is spent."""
    bridge.script("eval", [_value({"v": "stale"}), _value({"v": "stale"}),
                           _value({"v": "hello"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 0, cp.stderr
    assert bridge.ops() == _ops(2, restores=False)
    assert bridge.evals("restore") == [], "nothing changed; nothing to restore"
    data = _data(cp)
    assert data["applied"] is True
    assert data["verify"]["attempts"] == 2


def test_expect_mismatch_fails_loudly_with_wanted_and_got(bridge):
    bridge.script("eval", [_value({"v": "stale"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 1, f"rc={cp.returncode} out={cp.stdout}"
    # Bounded: 3 attempts, not an unbounded retry against the live browser.
    assert bridge.ops() == _ops(3, restores=False)
    assert "input_not_applied" in cp.stderr
    assert '"hello"' in cp.stderr and '"stale"' in cp.stderr
    # The envelope still lands on stdout AND still parses — a failure must not
    # cost the caller the result.
    data = _data(cp)
    assert data["applied"] is False
    assert data["verify"]["state"] == "miss"
    assert data["verify"]["attempts"] == 3
    assert data["verify"]["got"] == "stale"
    assert data["verify"]["want"] == "hello"


def test_the_retry_bound_is_the_configured_attempt_count(bridge):
    """Mutation control for the bound: change the knob, the op count MUST move.
    A test that only ever sees 3 cannot tell a bound from a coincidence."""
    bridge.script("eval", [_value({"v": "stale"})])
    cp = bridge.run("type", "hi", "--selector", "#q", "--expect", "hello",
                    extra_env={"BB_TYPE_ATTEMPTS": "2"})
    assert cp.returncode == 1
    assert bridge.ops() == _ops(2, restores=False)
    assert _data(cp)["verify"]["attempts"] == 2


@pytest.mark.parametrize("bad", ["0", "three", "-1", "3.5", " 3", "11", "999"])
def test_a_malformed_attempt_count_is_refused_before_any_op(bridge, bad):
    """An unbounded/garbage bound must never reach the loop: this drives the
    user's LIVE browser, and each attempt is up to three wire ops.

    `""` is deliberately NOT in this list: `${BB_TYPE_ATTEMPTS:-3}` treats an
    empty value as unset, so an exported-but-empty variable means "the default",
    not "invalid" — the next test pins that so the omission stays a decision
    rather than a gap."""
    cp = bridge.run("type", "hi", "--selector", "#q", "--expect", "hi",
                    extra_env={"BB_TYPE_ATTEMPTS": bad})
    assert cp.returncode == 1
    assert "BB_TYPE_ATTEMPTS" in cp.stderr
    assert bridge.ops() == [], "nothing may reach the wire on a bad bound"


def test_a_huge_attempt_count_gets_the_rule_not_a_bash_internal(bridge):
    """`[ "$n" -ge 1 ]` on a value past LLONG_MAX prints
    `browser: line NNN: [: integer expected` and the intended message never runs,
    so the caller is handed a bash internal instead of the rule they broke — and
    the comment claiming this knob "prevents an unbounded op storm" imposed no
    ceiling at all."""
    cp = bridge.run("type", "hi", "--selector", "#q", "--expect", "hi",
                    extra_env={"BB_TYPE_ATTEMPTS": "99999999999999999999999"})
    assert cp.returncode == 1
    assert "integer expected" not in cp.stderr, cp.stderr
    assert "BB_TYPE_ATTEMPTS must be an integer 1..10" in cp.stderr
    assert bridge.ops() == []


def test_an_empty_attempt_count_means_the_default_not_an_error(bridge):
    """An exported-but-empty BB_TYPE_ATTEMPTS is common (`export BB_TYPE_ATTEMPTS=`
    in a sourced profile) and must fall back to the default rather than refusing
    every verified type."""
    bridge.script("eval", [_value({"v": "stale"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello",
                    extra_env={"BB_TYPE_ATTEMPTS": ""})
    assert cp.returncode == 1
    assert bridge.ops() == _ops(3, restores=False), "fell back to the default of 3"


# --------------------------------------------------------------------------- #
# 1a. THE SETTLE — a validation hole turned the detector into a rubber stamp
#
# The first version validated with `case ... ""|*[!0-9.]*)`, which accepts
# `0.3.5`, `1.2.3` and a bare `.`; `sleep` then rejected them and the error was
# discarded by `2>/dev/null || true`. So a typo ran with NO SETTLE, rc 0, no
# warning — and per the CLI's own header a read-back taken in the same tick as
# the insert reports success even in the failing case.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["0.3.5", "1.2.3", ".", "abc", "-1", "1e3",
                                 " 3", "0.", "31", "100"])
def test_a_malformed_or_oversized_settle_is_refused_before_any_op(bridge, bad):
    cp = bridge.run("type", "hi", "--selector", "#q", "--expect", "hi",
                    extra_env={"BB_TYPE_SETTLE_S": bad})
    assert cp.returncode == 1, f"{bad!r} was accepted"
    assert "BB_TYPE_SETTLE_S" in cp.stderr
    assert bridge.ops() == [], "nothing may reach the wire without a real settle"


@pytest.mark.parametrize("good", ["0", "0.35", ".5", "3"])
def test_the_accepted_settle_forms(bridge, good):
    """CONTROL for the rejection above: a validator that refuses everything would
    satisfy every arm of the previous test while breaking the feature."""
    bridge.script("eval", [_value({"v": "hello"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello",
                    extra_env={"BB_TYPE_SETTLE_S": good})
    assert cp.returncode == 0, cp.stderr


def test_a_settle_that_fails_to_RUN_stops_the_command(bridge, tmp_path):
    """🔴 The other half of finding 2, and it needs a REACHABLE input.

    The old code was `sleep "$v" 2>/dev/null || true` — the failure discarded. The
    validation above now makes a malformed value unreachable, which is good but
    also means a mutation battery cannot kill the un-swallowing on its own: with
    the value guaranteed valid, `sleep` never fails, so the two spellings are
    equivalent. (Measured: mutant M3 SURVIVED for exactly that reason.)

    So reach it the only way left — shadow `sleep` with one that exits non-zero.
    A settle that did not run must stop the command, not silently read back in
    the same tick as the insert, which reports success in the failing case."""
    stub = tmp_path / "fakebin"
    stub.mkdir()
    (stub / "sleep").write_text("#!/usr/bin/env bash\nexit 7\n")
    (stub / "sleep").chmod(0o755)
    bridge.script("eval", [_value({"v": "hello"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello",
                    extra_env={"PATH": f"{stub}:{bridge.env['PATH']}"})
    assert cp.returncode == 1, f"rc={cp.returncode} out={cp.stdout}"
    assert "settle failed to run" in cp.stderr, cp.stderr
    assert "refusing to read back without it" in cp.stderr
    # The pre-read and the type went out; the read-back did NOT.
    assert bridge.ops() == ["eval", "type"], bridge.ops()


@pytest.mark.parametrize("settle,floor,ceil", [("0.6", 0.5, None),
                                               ("0", None, 0.45)])
def test_the_settle_actually_happens(bridge, settle, floor, ceil):
    """🔴 THE SETTLE IS THE DETECTOR — so something must FAIL when it does not run.

    Every other test here would stay green with the `sleep` line deleted (it was:
    51/51 green with it removed). Wall time is the only observable that moves, so
    it is measured at TWO points: a 0.6s settle must cost at least 0.5s of real
    time, and a 0s settle must not. One point alone cannot tell a real sleep from
    a slow test machine."""
    bridge.script("eval", [_value({"v": "hello"})])
    t0 = time.monotonic()
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello",
                    extra_env={"BB_TYPE_ATTEMPTS": "1",
                               "BB_TYPE_SETTLE_S": settle})
    dt = time.monotonic() - t0
    assert cp.returncode == 0, cp.stderr
    if floor is not None:
        assert dt >= floor, f"a {settle}s settle took only {dt:.3f}s — it did not run"
    if ceil is not None:
        assert dt <= ceil, (f"a {settle}s settle took {dt:.3f}s — the control arm "
                            f"is too slow for the floor arm above to mean anything")


# --------------------------------------------------------------------------- #
# 1b. THE PRE-READ — the retry must not destroy content it never wrote
#
# 🔴 MEASURED-DEFECT REGRESSION. The undo used to be INFERRED: strip a trailing
# copy of the typed text off the read-back. That is only the insert's own tail
# when the insert LANDED — and the case this feature exists to detect is the one
# where it did not, where the read-back IS the pre-existing content. A field
# holding "my password", typed into with "password" and not applied, ends with
# the typed text, so the CLI cut it to "my " and then reported that as the value
# the page held.
# --------------------------------------------------------------------------- #
def test_a_type_that_does_not_land_never_amputates_the_field(bridge):
    """The regression arm. `applies=False` is the no-apply failure; the field
    ends with the typed text purely by coincidence of its content, which is all
    the old inference needed to destroy it."""
    f = Field(bridge, initial="my password", applies=False)
    cp = bridge.run("type", "password", "--selector", "#q",
                    "--expect", "password")
    assert cp.returncode == 1
    assert f.v == "my password", (
        "the CLI must leave the field exactly as it found it; got %r" % f.v)
    # And it must not report content it created itself.
    assert _data(cp)["verify"]["got"] == "my password"
    assert "my " not in cp.stderr.replace("my password", "")


@pytest.mark.parametrize("attempts", ["1", "3"])
def test_a_landing_insert_leaves_exactly_one_copy_however_many_attempts(bridge, attempts):
    """The CONTROL for the arm above, and the compounding regression.

    `applies=True` proves the simulator really does insert (a simulator that
    silently dropped the text would make the no-apply arm pass vacuously), and
    the property is that the field the caller is left with does NOT depend on how
    many times the loop retried. Pre-fix this left "junkDreamDreamDream"."""
    f = Field(bridge, initial="junk", applies=True)
    cp = bridge.run("type", "Dream", "--selector", "#q", "--expect", "Dream",
                    extra_env={"BB_TYPE_ATTEMPTS": attempts})
    assert cp.returncode == 1, cp.stdout
    assert _data(cp)["verify"]["attempts"] == int(attempts)
    assert f.v == "junkDream", (
        "the field must hold ONE copy however many attempts ran; got %r" % f.v)
    assert f.v.count("Dream") == 1


def test_the_restore_target_is_the_measured_pre_state_not_a_stripped_tail(bridge):
    """The mechanism, not just the outcome. The restore must write the value the
    PRE-READ measured — not a substring computed from the post-state, which is
    what cut a user's field down to "my "."""
    Field(bridge, initial="my password", applies=True)
    cp = bridge.run("type", "password", "--selector", "#q",
                    "--expect", "password", extra_env={"BB_TYPE_ATTEMPTS": "2"})
    assert cp.returncode == 1
    restores = bridge.evals("restore")
    assert len(restores) == 1, "exactly one restore, between the two attempts"
    assert _restore_target(restores[0]) == "my password"


def test_the_restore_uses_the_native_setter_and_fires_input(bridge):
    """A plain `e.value = x` is INVISIBLE to React's synthetic-event layer, so a
    controlled input would re-render the old value straight back and the clear
    would silently not happen. The prescribed technique (civitai site-notes) is
    the prototype's native value setter plus a bubbling `input` event."""
    Field(bridge, initial="junk", applies=True)
    bridge.run("type", "Dream", "--selector", "#q", "--expect", "Dream",
               extra_env={"BB_TYPE_ATTEMPTS": "2"})
    js = bridge.evals("restore")[0]
    assert "getOwnPropertyDescriptor" in js and "d.set.call(e,v)" in js
    assert 'new Event("input",{bubbles:true})' in js


def test_a_restore_that_does_not_stick_stops_the_retry(bridge):
    """The page owns the value and puts it straight back. Re-typing would
    CONCATENATE, so the loop must refuse rather than spend its remaining attempts
    making the field worse — and must SAY which of the two it did."""
    Field(bridge, initial="junk", applies=True, restore_sticks=False)
    cp = bridge.run("type", "Dream", "--selector", "#q", "--expect", "Dream")
    assert cp.returncode == 1
    assert bridge.ops() == ["eval", "type", "eval", "eval"], (
        "pre-read, one attempt, one restore, and NO second type")
    data = _data(cp)
    assert data["verify"]["retryStopped"] == "restore_failed"
    assert data["verify"]["attempts"] == 1
    assert data["applied"] is False
    assert "restore_failed" in cp.stderr and "CONCATENATE" in cp.stderr


def test_no_pre_state_means_no_retry(bridge):
    """A pre-read that could not be evaluated (a rate-limited first op, a frame
    that had not resolved yet) leaves nothing to restore TO. Re-typing would
    concatenate with no way to undo it, so the loop refuses after one attempt and
    names the reason — rather than guessing, which is the whole defect above."""
    # Pre-read null (unreadable); the read-back afterwards works and misses.
    bridge.script("eval", [_value(None), _value({"v": "stale"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 1
    assert bridge.ops() == ["eval", "type", "eval"], "one attempt only"
    data = _data(cp)
    assert data["verify"]["retryStopped"] == "no_pre_state"
    assert data["verify"]["attempts"] == 1
    assert "no_pre_state" in cp.stderr


def test_a_successful_verify_reports_no_retry_stop(bridge):
    """`retryStopped` must be ABSENT on the ordinary paths — a field that is
    always present carries no information, and a consumer branching on its
    presence would see every happy path as a stopped retry."""
    bridge.script("eval", [_value({"v": ""}), _value({"v": "hello"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 0, cp.stderr
    assert "retryStopped" not in _data(cp)["verify"]


# --------------------------------------------------------------------------- #
# 1c. bad_target is rc 1 — rc 3 explicitly does not mean "nothing happened"
#
# The extension's focusExpression only checks that the element EXISTS and calls
# .focus(), so a --selector naming a wrapper <div> — what you get by lifting a
# selector out of `--annotated` output — makes the type op report success while
# the insert goes wherever focus actually is. Scoring that as rc 3 ("the primary
# op succeeded and only its follow-up failed") is the worst available answer.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tag", ["element_not_found", "no_editable_target"])
def test_a_bad_selector_is_refused_before_anything_is_typed(bridge, tag):
    bridge.script("eval", [_value({"e": tag})])
    cp = bridge.run("type", "hello", "--selector", "#wrapper", "--expect", "hello")
    assert cp.returncode == 1, f"rc={cp.returncode}: rc 3 would read as 'probably fine'"
    assert bridge.ops() == ["eval"], "the pre-read caught it; NOTHING was typed"
    assert cp.stdout == ""
    assert "bad_target" in cp.stderr and tag in cp.stderr
    assert "NOTHING was typed" in cp.stderr


def test_a_wrapper_div_selector_says_what_to_do_about_it(bridge):
    """The message has to name the actual mistake — the selector points at
    something that cannot hold text — or the reader re-runs the same command."""
    bridge.script("eval", [_value({"e": "no_editable_target"})])
    cp = bridge.run("type", "hello", "--selector", "div.wrap", "--expect", "hello")
    assert "wrapper <div>" in cp.stderr
    assert "--annotated" in cp.stderr
    assert "rc 1, not rc 3" in cp.stderr


@pytest.mark.parametrize("tag", ["element_not_found", "no_editable_target"])
def test_with_NO_selector_an_uneditable_target_is_rc_3_not_a_refusal(bridge, tag):
    """🔴 The SCOPE control for the rule above, and it is not a technicality.

    With no `--selector` the read targets `document.activeElement`, but CDP types
    into whatever the RENDERER has focused — and those differ: focus inside a
    shadow root reports the shadow HOST, a custom element with no `.value` and no
    contenteditable. Refusing there would (a) print an error naming a flag that
    appears nowhere in the caller's command line — the exact defect the BB_TAB
    message had — and (b) call a type that may well have landed a failure.

    So this arm is `unverifiable`/rc 3, and it says how to get a definite
    answer."""
    bridge.script("eval", [_value({"e": tag})])
    cp = bridge.run("type", "hello", "--verify")
    assert cp.returncode == 3, f"rc={cp.returncode}"
    assert "bad_target" not in cp.stderr, "no --selector was given to blame"
    assert "--selector" in cp.stderr, "it must say how to get a definite answer"
    assert "shadow" in cp.stderr
    assert _data(cp)["verify"]["state"] == "unverifiable"


def test_a_target_that_disappears_after_the_type_is_also_rc_1(bridge):
    """Usable before the type, gone after: the page replaced the node. The text
    WAS sent, but it is provably not in what the caller named, so `applied` is
    false and the exit code is 1 — not the "only the check failed" code."""
    Field(bridge, initial="", applies=True,
          read_tag={"e": "element_not_found"})
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 1
    data = _data(cp)
    assert data["verify"]["state"] == "bad_target"
    assert data["applied"] is False
    assert "The text WAS sent" in cp.stderr


# --------------------------------------------------------------------------- #
# 1d. unverifiable is a THIRD state — rc 3, and never folded into "miss"
# --------------------------------------------------------------------------- #
def test_a_csp_blocked_readback_is_unverifiable_not_a_miss(bridge):
    """A strict page CSP makes the injected eval return null (SKILL.md trap 2,
    GitHub). "I could not look" and "the text is not there" need OPPOSITE next
    actions, so null must not be scored as a failed type — and must not be
    retried, which would spend more ops on an unanswerable question.

    This is the NEGATIVE CONTROL for the bad_target tests above: a genuinely
    unreadable page still exits 3, so moving element_not_found to rc 1 did not
    just collapse everything into rc 1."""
    bridge.script("eval", [_value(None)])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 3, f"rc={cp.returncode} err={cp.stderr}"
    assert bridge.ops() == ["eval", "type", "eval"], "no retry on an unverifiable read"
    assert "input_not_verified" in cp.stderr
    assert "CSP" in cp.stderr
    data = _data(cp)
    assert data["applied"] is None, "tri-state: null is 'not verified', not False"
    assert data["verify"]["state"] == "unverifiable"


def test_a_failed_readback_op_is_unverifiable_and_names_the_rate_limit(bridge):
    """The reads are SEPARATE ops and can fail on their own. A verified type is
    3 ops (up to 9 across retries), so a 429 on the follow-up is the realistic
    failure — and it must not be reported as a CSP block, which sends the reader
    to the wrong page."""
    bridge.script("eval", [(200, {"ok": True, "result": {
        "id": "e", "ok": False, "error": "rate_limited"}})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 3
    assert "input_not_verified" in cp.stderr
    assert "rate-limited" in cp.stderr or "rate_limited" in cp.stderr
    assert _data(cp)["applied"] is None


# --------------------------------------------------------------------------- #
# 1e. --verify, and the privacy contract on BOTH flags
# --------------------------------------------------------------------------- #
def test_verify_passes_when_the_typed_text_arrives(bridge):
    bridge.script("eval", [_value({"v": "prefix-"}),
                           _value({"v": "prefix-hello"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--verify")
    assert cp.returncode == 0, cp.stderr
    data = _data(cp)
    assert data["applied"] is True
    assert data["verify"]["mode"] == "contains"


def test_verify_does_not_score_an_unchanged_field_as_applied(bridge):
    """🔴 `typed in got` alone cannot tell "the text landed" from "the text was
    already there": a field pre-holding `hello` plus a type that did NOTHING
    scored rc 0 / `applied:true`. The pre-read is what makes the two
    distinguishable, and an unchanged field is a MISS."""
    Field(bridge, initial="hello", applies=False)
    cp = bridge.run("type", "hello", "--selector", "#q", "--verify")
    assert cp.returncode == 1, "an unchanged field must not read as applied"
    data = _data(cp)
    assert data["applied"] is False
    assert data["verify"]["unchanged"] is True
    assert "byte-identical" in cp.stderr


def test_verify_sees_compounding_that_a_substring_check_cannot(bridge):
    """`"DreamDream"` contains `"Dream"`, so contains-mode passed a field the
    insert had doubled. Counted against the pre-state instead."""
    bridge.script("eval", [_value({"v": ""}), _value({"v": "DreamDream"})])
    cp = bridge.run("type", "Dream", "--selector", "#q", "--verify")
    assert cp.returncode == 1
    data = _data(cp)
    assert data["verify"]["copiesAdded"] == 2
    assert data["applied"] is False
    assert "compounding" in cp.stderr


def test_verify_never_echoes_the_field_value_back(bridge):
    """The `type` op deliberately returns only the LENGTH typed and never echoes
    text (extension/service_worker.js). `--verify` keeps that in EVERY branch: it
    reports a length and, at most, a count."""
    bridge.script("eval", [_value({"v": ""}), _value({"v": "s3cret-token-value"})])
    cp = bridge.run("type", "hello", "--selector", "#q", "--verify")
    assert cp.returncode == 1
    assert "s3cret-token-value" not in cp.stdout
    assert "s3cret-token-value" not in cp.stderr
    data = _data(cp)
    assert "got" not in data["verify"], "contains-mode must not carry the value"
    assert data["verify"]["len"] == len("s3cret-token-value")


def test_verify_does_not_echo_the_field_value_on_a_PASS_either(bridge):
    """🔴 FOUND BY THE MUTATION BATTERY, not by reading the code.

    The test above exercises only the MISS branch — its field does not contain
    the typed text — so adding `got` to contains-mode's **ok** branch survived a
    fully green suite (mutant M13). A privacy guard that covers one of two
    branches reads as coverage while providing none on the other.

    Here the field PASSES and still carries a secret around the typed text."""
    bridge.script("eval", [_value({"v": ""}),
                           _value({"v": "tok-SUPERSECRET-tail"})])
    cp = bridge.run("type", "tok", "--selector", "#q", "--verify")
    assert cp.returncode == 0, cp.stderr
    assert _data(cp)["applied"] is True, "this must be the PASS branch"
    assert "SUPERSECRET" not in cp.stdout
    assert "SUPERSECRET" not in cp.stderr
    assert "got" not in _data(cp)["verify"]


def test_expect_does_not_echo_the_value_on_a_PASS(bridge):
    """🔴 `--expect` used to report `got` on EVERY outcome, justified by "the
    caller already holds the value". On a PASS that echo says nothing the exit
    code did not, and writes the value into stdout and every agent transcript
    that captured it — `--expect hunter2` printed `"got": "hunter2"`."""
    bridge.script("eval", [_value({"v": ""}), _value({"v": "hunter2"})])
    cp = bridge.run("type", "hunter2", "--selector", "#pw", "--expect", "hunter2")
    assert cp.returncode == 0, cp.stderr
    v = _data(cp)["verify"]
    assert "got" not in v and "want" not in v, v
    assert "hunter2" not in cp.stdout
    assert "hunter2" not in cp.stderr
    assert v["len"] == len("hunter2"), "a length still reaches the caller"


def test_expect_still_reports_got_on_a_MISS_and_says_whose_content_it_is(bridge):
    """CONTROL for the test above: the diagnostic `--expect` exists for must
    survive. And the justification is corrected in the message — on a miss `got`
    is the PAGE's content by construction, not an echo of the caller's argument,
    which is exactly why `--verify` exists for secret fields."""
    bridge.script("eval", [_value({"v": "other"})])
    cp = bridge.run("type", "hunter2", "--selector", "#pw", "--expect", "hunter2")
    assert cp.returncode == 1
    assert _data(cp)["verify"]["got"] == "other"
    assert "PAGE" in cp.stderr and "--verify" in cp.stderr


def test_expect_flags_a_pass_where_nothing_actually_changed(bridge):
    """`--expect` asserts the FINAL state, so a field that already held the value
    is a legitimate pass — but the caller should be able to tell that this type
    changed nothing. A boolean, never content."""
    Field(bridge, initial="hello", applies=False)
    cp = bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    assert cp.returncode == 0, cp.stderr
    v = _data(cp)["verify"]
    assert v["state"] == "ok" and v["unchanged"] is True


def test_expect_wins_over_verify_when_both_are_given(bridge):
    """--expect is the strictly stronger assertion; honouring the weaker one
    would silently downgrade what the caller asked for."""
    bridge.script("eval", [_value({"v": ""}), _value({"v": "hello world"})])
    cp = bridge.run("type", "hello", "--selector", "#q",
                    "--verify", "--expect", "hello")
    assert cp.returncode == 1, "contains would have PASSED here; expect must not"
    assert _data(cp)["verify"]["mode"] == "expect"


def test_expect_empty_string_asserts_the_field_is_cleared(bridge):
    """`--expect ''` is a real assertion, so "was the flag given?" is tracked
    separately from "is its value non-empty?"."""
    bridge.script("eval", [_value({"v": ""})])
    cp = bridge.run("type", "x", "--selector", "#q", "--expect", "")
    assert cp.returncode == 0, cp.stderr
    assert _data(cp)["applied"] is True


# --------------------------------------------------------------------------- #
# 1f. the read expression itself, and routing
# --------------------------------------------------------------------------- #
def test_the_reads_target_the_same_selector_the_type_used(bridge):
    bridge.script("eval", [_value({"v": "hello"})])
    bridge.run("type", "hello", "--selector", "#q\"weird", "--expect", "hello")
    reads = bridge.evals("read")
    assert len(reads) >= 2, "a pre-read and a read-back"
    for js in reads:
        # The selector is JSON-escaped into the expression, not concatenated raw.
        assert 'document.querySelector("#q\\"weird")' in js


def test_the_read_without_a_selector_requires_an_EDITABLE_active_element(bridge):
    """`document.activeElement` defaults to <body>. Falling back to its
    textContent would hand back the WHOLE PAGE, and a `--verify` substring check
    against the whole page passes whenever the word appears anywhere on it."""
    bridge.script("eval", [_value({"v": "hello"})])
    bridge.run("type", "hello", "--verify")
    js = bridge.evals("read")[0]
    assert "document.activeElement" in js
    assert "isContentEditable" in js
    assert "no_editable_target" in js


def test_the_read_is_a_single_expression(bridge):
    """`js`/`eval` evaluates ONE expression; a multi-statement body returns null
    with no error (SKILL.md trap 1) — which this code would then have to report
    as a CSP block. Pin the IIFE shape."""
    bridge.script("eval", [_value({"v": "hello"})])
    bridge.run("type", "hello", "--selector", "#q", "--expect", "hello")
    js = bridge.evals("read")[0]
    assert js.startswith("(function(){") and js.endswith("})()")
    assert "\n" not in js


def test_frame_routes_the_pre_read_the_type_and_the_readback(bridge):
    """A --frame type verified against the TOP frame would be worse than no
    verification: it would confirm the wrong document. All THREE ops carry it."""
    bridge.script("eval", [_value({"v": ""}), _value({"v": "hello"})])
    bridge.run("--frame", "7", "type", "hello", "--selector", "#q",
               "--expect", "hello")
    assert [b.get("op") for b in bridge.bodies] == ["eval", "type", "eval"]
    for b in bridge.bodies:
        assert b["frame"] == "7", b


@pytest.mark.parametrize("order", [
    ("type", "--selector", "#q", "--expect", "hello", "hello"),
    ("type", "hello", "--selector", "#q", "--expect", "hello"),
])
def test_expect_works_with_the_flag_before_or_after_the_text(bridge, order):
    """The repo's flag-order invariant (test_browser_cli_args.py) extends to the
    new flags: the two orders must be the same command."""
    bridge.script("eval", [_value({"v": ""}), _value({"v": "hello"})])
    cp = bridge.run(*order)
    assert cp.returncode == 0, cp.stderr
    assert bridge.bodies[1] == {"op": "type", "text": "hello", "selector": "#q"}


def test_unknown_type_flag_names_the_new_flags(bridge):
    cp = bridge.run("type", "hello", "--nope")
    assert cp.returncode == 1
    assert "--expect" in cp.stderr and "--verify" in cp.stderr
    assert bridge.ops() == [], "nothing may reach the wire on a usage error"


def test_every_inline_python_body_in_the_cli_still_compiles():
    """🔴 A literal `'` inside a `python3 -c '…'` body ENDS THE SHELL STRING, and
    `bash -n` MAY NOT NOTICE: whether it does depends on where the quotes happen
    to re-balance further down the file. When they re-balance cleanly the script
    parses fine and every affected subcommand dies at RUNTIME with a SyntaxError
    from a truncated program.

    Both halves measured while writing this PR. The instance that bit: an error
    message written as `'could not confirm'` — `bash -n` reported **SYNTAX OK**
    over a CLI in which 38 of 63 tests then failed at runtime. The positive
    control below: an apostrophe planted in a different body — `bash -n` DID
    catch that one. So `bash -n` is not the guard; this is.

    Extract every single-quoted inline body and `compile()` it. A truncated body
    cannot compile, which is exactly the observable the defect produces.

    POSITIVE CONTROL (run 2026-08-21 by hand, not asserted here — it would mean
    mutating a tracked file): planting `"the read op's own failure"` in
    `_type_classify` made this test RED with
    `does not compile (unterminated string literal …)`, i.e. it fails for ITS
    OWN reason, not because some other assertion tripped."""
    src = CLI.read_text()
    marker = "python3 -c '"
    bodies, i = [], src.find(marker)
    while i != -1:
        start = i + len(marker)
        end = src.find("'", start)
        assert end != -1, "unterminated inline python body"
        bodies.append((src[:start].count("\n") + 1, src[start:end]))
        i = src.find(marker, end)
    assert len(bodies) >= 20, (
        f"only {len(bodies)} inline python bodies found — the extractor stopped "
        "early, so a green result here would prove nothing")
    for line, body in bodies:
        try:
            compile(body, f"{CLI}:{line}", "exec")
        except SyntaxError as exc:
            raise AssertionError(
                f"the inline python at {CLI}:{line} does not compile ({exc}). "
                "The usual cause is a literal apostrophe inside it, which ends "
                "the surrounding shell string; bash -n cannot see it.") from None


def test_the_op_budget_is_documented_where_an_agent_reads_it():
    """A verified type is 3 wire ops, not 1, against a rate-limited channel. A
    budget nobody can discover is how a 429 becomes a mystery.

    SKILL.md is byte-capped (tests/test_skill_size.py), so the detail lives in
    `reference/type-verify.md` and SKILL.md carries the pointer. This asserts the
    whole CHAIN — the pointer exists, the file it names exists, and the number is
    in it — because a pointer to a file without the fact is worse than no
    pointer: it reads as coverage while providing none."""
    help_text = subprocess.run(["bash", str(CLI), "--help"],
                               capture_output=True, text=True, timeout=60).stdout
    skill = (BB / "SKILL.md").read_text()
    topic = BB / "reference" / "type-verify.md"
    assert "3 wire" in help_text and "9" in help_text
    assert "reference/type-verify.md" in skill, "SKILL.md must point at the topic"
    assert topic.exists(), f"{topic} is referenced by SKILL.md but does not exist"
    body = topic.read_text()
    assert "3 wire ops" in body
    assert "BB_TYPE_ATTEMPTS" in body and "BB_TYPE_SETTLE_S" in body
