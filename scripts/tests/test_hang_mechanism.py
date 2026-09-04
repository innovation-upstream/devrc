#!/usr/bin/env python3
"""`testlib.hang_mechanism` — the verdict that separates an I/O stall from a bug.

🔴 WHY THIS NEEDS TESTS OF ITS OWN: A CLASSIFIER THAT ANSWERS THE SAME THING TO
EVERY HANG IS WORSE THAN NO CLASSIFIER. It reads as evidence, it appears in a CI
log next to a real failure, and it stops the next person looking. So these pull the
verdicts APART — each case asserts the mechanism it expects AND that the rival
verdict is absent, because "contains SERVER_BLOCKED_IN_FSYNC" passes for a function
that returns that string unconditionally.

The path case is the one that motivated putting this in `testlib` rather than
copying the store-api classifier: that one matches its tokens against
`traceback.format_stack`, which renders each frame's FILENAME, so a checkout whose
path contains a token misclassifies every hang. It is documented there as a known,
unfixed defect. This module must not inherit it, and a test — not a comment — is
what keeps that true.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from testlib import hang_mechanism  # noqa: E402


# --------------------------------------------------------------------------
# classify() — the per-thread verdict, from (function name, source line) pairs.
# --------------------------------------------------------------------------


def test_a_frame_that_calls_fsync_is_named_as_an_fsync_stall():
    frames = [("_replace_bytes", "os.fsync(fh.fileno())"), ("do_POST", "self._append()")]
    assert hang_mechanism.classify(frames) == "SERVER_BLOCKED_IN_FSYNC"


def test_the_INNERMOST_ledgered_frame_wins_not_an_outer_one():
    """Innermost-first, because the outer frames are the CALLERS of the wait.

    A stack that entered through the entry lock and is now parked in fsync is an
    fsync stall; reporting the outermost match would name the lock and send a
    reader to the wrong subsystem.
    """
    frames = [
        ("_replace_bytes", "os.fsync(fd)"),      # innermost — where it is parked
        ("_write_entry", "with _EntryLock(path):"),  # outer — how it got there
    ]
    verdict = hang_mechanism.classify(frames)
    assert verdict == "SERVER_BLOCKED_IN_FSYNC"
    assert verdict != "SERVER_BLOCKED_ON_ENTRY_LOCK"


def test_an_unledgered_park_is_BLOCKED_ELSEWHERE_and_not_a_nearest_guess():
    frames = [("_proxy", "urllib.request.urlopen(req, timeout=15)")]
    assert hang_mechanism.classify(frames) == "BLOCKED_ELSEWHERE"


def test_the_entry_lock_and_the_audit_sink_are_told_APART_from_fsync():
    """Three ledgered mechanisms, three different answers.

    🔴 Asserting only that each is non-empty would pass for a classifier that
    answered `SERVER_BLOCKED_IN_FSYNC` to all three — the exact failure this file
    exists to prevent — so each case also asserts the others are NOT returned.
    """
    lock = hang_mechanism.classify([("_write", "with _EntryLock(p):")])
    audit = hang_mechanism.classify([("_emit", "with self._audit_lock:")])
    fsync = hang_mechanism.classify([("_replace", "os.fsync(fd)")])
    assert lock == "SERVER_BLOCKED_ON_ENTRY_LOCK"
    assert audit == "SERVER_BLOCKED_IN_AUDIT_SINK"
    assert fsync == "SERVER_BLOCKED_IN_FSYNC"
    assert len({lock, audit, fsync}) == 3, (
        "the classifier collapsed three distinct mechanisms onto fewer verdicts"
    )


# --------------------------------------------------------------------------
# The path defect. This is the reason this module exists separately.
# --------------------------------------------------------------------------


def test_a_checkout_PATH_containing_a_token_does_not_decide_the_verdict():
    """🔴 THE DEFECT `test_subsystem_store_api.py` DOCUMENTS AS KNOWN AND UNFIXED.

    A worktree named `devrc-fsync` — the likely name for a checkout in which
    someone is fixing this very flake — turned an unrelated stall into
    `SERVER_BLOCKED_IN_FSYNC` there, because the tokens are matched against
    `traceback.format_stack`, which renders each frame's filename.

    This builds exactly that situation: a frame whose FILENAME contains `fsync`
    and whose source line does not. The control is the first assertion — it proves
    the token really is present in the rendered stack, so a passing verdict below
    is the classifier ignoring the path rather than the fixture failing to contain
    it. Without that control this test would pass against a tree where the setup
    silently stopped working.
    """
    source = "def parked(probe):\n    return probe()\n"
    namespace: dict = {}
    exec(compile(source, "/tmp/devrc-fsync/server.py", "exec"), namespace)

    captured: dict = {}

    def probe():
        frame = sys._getframe()
        captured["frames"] = hang_mechanism._frames(frame)
        captured["rendered"] = "".join(traceback.format_stack(frame))
        return None

    namespace["parked"](probe)

    # CONTROL: the token IS in the rendered stack, via the path alone.
    assert "fsync" in captured["rendered"], (
        "the fixture did not put `fsync` into the rendered stack at all, so the "
        "assertion below would pass vacuously"
    )
    # And it is NOT in what the classifier actually reads.
    for name, line in captured["frames"]:
        assert "fsync" not in line, f"a source line leaked the path: {line!r}"
    assert hang_mechanism.classify(captured["frames"]) != "SERVER_BLOCKED_IN_FSYNC", (
        "a checkout path containing `fsync` decided the verdict — this module has "
        "re-acquired the defect it was split out to avoid"
    )


# --------------------------------------------------------------------------
# report() — the headline, against REAL parked threads.
# --------------------------------------------------------------------------


@pytest.fixture
def parked_fsync():
    """A live thread genuinely blocked inside `os.fsync`, in a handler frame.

    Named `process_request_thread` because that is what `report()` uses to tell a
    handler from the accept loop — the same marker socketserver produces.
    """
    release = threading.Event()
    started = threading.Event()
    real_fsync = os.fsync

    def blocking_fsync(fd):
        started.set()
        release.wait(30)
        return None

    def process_request_thread():
        fd = os.open(os.devnull, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    os.fsync = blocking_fsync
    thread = threading.Thread(target=process_request_thread, daemon=True)
    thread.start()
    assert started.wait(30), "the fixture thread never reached the stall site"
    try:
        yield
    finally:
        release.set()
        thread.join(30)
        os.fsync = real_fsync
        assert not thread.is_alive(), "the parked fixture thread was not reaped"


def test_a_REAL_thread_parked_in_fsync_is_reported_as_an_fsync_stall(parked_fsync):
    report = hang_mechanism.report()
    assert "MECHANISM = SERVER_BLOCKED_IN_FSYNC" in report, report
    assert "handler threads=1" in report, report


def test_the_headline_NAMES_the_stall_even_when_another_handler_is_elsewhere(
    parked_fsync,
):
    """🔴 THE CASE A CONSENSUS RULE WOULD GET WRONG, AND IT IS THE REAL ONE.

    `test_cairn_write.py` runs TWO servers — the store and the shim in front of it
    — so when a write times out there are two handler threads and they legitimately
    disagree: the shim is parked in `urlopen`, the store in `fsync`. Measured on the
    reproduction: `handler threads=2 [...=BLOCKED_ELSEWHERE ...=SERVER_BLOCKED_IN_
    FSYNC]`. A rule that required the handlers to agree would answer `AMBIGUOUS` for
    the textbook case the classifier exists to name.
    """
    proceed = threading.Event()
    entered = threading.Event()

    def process_request_thread():        # a second handler, parked elsewhere
        entered.set()
        proceed.wait(30)

    other = threading.Thread(target=process_request_thread, daemon=True)
    other.start()
    try:
        assert entered.wait(30)
        report = hang_mechanism.report()
        assert "handler threads=2" in report, report
        assert "MECHANISM = SERVER_BLOCKED_IN_FSYNC" in report, report
        assert "AMBIGUOUS" not in report, report
        assert "BLOCKED_ELSEWHERE" in report, (
            "the per-handler breakdown must still show the dissenting thread so a "
            "reader can check the headline rather than take it"
        )
    finally:
        proceed.set()
        other.join(30)
        assert not other.is_alive()


def test_no_parked_handler_is_NOT_reported_as_a_stall():
    """🔴 THE VERDICT MUST BE ABLE TO SAY 'I DID NOT SEE ONE'.

    A stall that cleared before the assertion ran, and a genuine code failure, both
    land here. The classifier does not distinguish them and must not pretend to —
    naming a mechanism it did not observe is the failure mode that makes a
    diagnostic worse than none.
    """
    report = hang_mechanism.report()
    assert "SERVER_BLOCKED_IN_FSYNC" not in report, report
    assert "MECHANISM = " in report, report


def test_the_note_is_carried_into_the_report():
    """The caller's context (store path, filesystem) must survive into the log."""
    assert "store=/somewhere fs=tmpfs" in hang_mechanism.report(
        "\nstore=/somewhere fs=tmpfs"
    )
