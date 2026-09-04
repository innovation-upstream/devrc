"""Why a server thread did not answer — a `MECHANISM =` verdict for a CI log.

🔴 DIAGNOSTICS, NOT A MITIGATION. Nothing here retries, drains, sleeps, or moves a
bound. A test that failed still fails, exactly as loudly. What this adds is the one
fact the bare assertion cannot carry.

The problem it exists for: **a client-side timeout is the observable that the most
mechanisms share, so on its own it identifies none of them.** `scripts/ci-repro/
README.md` records the store-api half of this; the cairn half reaches the same dead
end from the other side. `server.py:_replace_bytes` fsyncs the file and then the
parent directory INSIDE the request, before the response is written, and fsync blocks
in uninterruptible D-state bounded by nothing. When that outlasts the client's bound
the client reports the store UNREACHABLE, and the gate reads

    AssertionError: cairn: the write did NOT happen — ... unreachable: timed out
    assert 7 == 0

which is a sentence about the write CODE for what was an I/O stall. Measured
2026-09-02 on `devrc-ci-jfg67`, and reproduced on the dev host with the store on
**tmpfs** — so this is a latency dependency, not a filesystem one, and siting the
store off the contended disk (`testlib.store_siting`) shrinks the probability
without removing it.

🔴 SOURCE LINES ONLY — NEVER THE FILENAME, AND THAT IS A FIX, NOT A STYLE CHOICE.
`test_subsystem_store_api.py:_HUNG_SERVER_RULES` carries a documented KNOWN DEFECT:
it matches its tokens against `traceback.format_stack`, which RENDERS EACH FRAME'S
FILENAME, so a checkout whose PATH contains a token misclassifies every hang —
confidently and wrongly, which is worse than no verdict. It was reproduced there by
accident: a worktree named `devrc-fsync` turned an entry-lock stall into
`SERVER_BLOCKED_IN_FSYNC`. This module classifies on the frame's SOURCE LINE and
FUNCTION NAME and never on its path, and
`test_hang_mechanism.py::test_a_checkout_PATH_containing_a_token_does_not_decide_the
_verdict` pins that.
"""
from __future__ import annotations

import sys
import threading
import traceback
import types

# Token -> mechanism, matched against a frame's SOURCE LINE and FUNCTION NAME.
#
# Ordered, and the FIRST match on the innermost matching frame wins. These are the
# unbounded waits a store-server handler can sit in; each blocks in a way no socket
# timeout reaches, because a socket timeout does not bound a syscall.
RULES: tuple[tuple[str, str], ...] = (
    ("fsync", "SERVER_BLOCKED_IN_FSYNC"),
    ("flock", "SERVER_BLOCKED_ON_ENTRY_LOCK"),
    ("_EntryLock", "SERVER_BLOCKED_ON_ENTRY_LOCK"),
    ("_audit_lock", "SERVER_BLOCKED_IN_AUDIT_SINK"),
)

# socketserver's per-connection worker. Its presence is what makes a thread a
# HANDLER rather than the accept loop.
_HANDLER_MARKER = "process_request_thread"
_ACCEPT_MARKER = "serve_forever"


def _frames(frame: types.FrameType) -> list[tuple[str, str]]:
    """`(function_name, source_line)` for every frame, INNERMOST FIRST.

    The filename is dropped here rather than filtered later, so no caller can
    reintroduce the path-matching defect this module exists to avoid.
    """
    summary = traceback.StackSummary.extract(
        traceback.walk_stack(frame), capture_locals=False
    )
    return [(f.name, f.line or "") for f in summary]


def classify(frames: list[tuple[str, str]]) -> str:
    """The mechanism for ONE thread, from its `(name, source_line)` pairs.

    Scans innermost-first and returns the first rule that matches either half of a
    frame. `BLOCKED_ELSEWHERE` when the thread is parked somewhere unledgered — a
    real answer, and deliberately not a guess at which rule was "closest".
    """
    for name, line in frames:
        for token, mechanism in RULES:
            if token in line or token in name:
                return mechanism
    return "BLOCKED_ELSEWHERE"


def report(note: str = "") -> str:
    """A `MECHANISM = ...` headline plus every non-main thread's stack.

    🔴 THE HEADLINE ASKS ONE QUESTION: **is any server thread parked in a known
    unbounded wait right now?** It is not a consensus of the threads, and it must
    not be, because two servers are live in these tests — the store server and the
    shim in front of it — and at the moment a write times out the shim's handler is
    legitimately parked in `urlopen` waiting on the store. A rule that demanded the
    handlers AGREE would answer `AMBIGUOUS` for the textbook case this exists to
    name. So the headline is a ledgered mechanism found on ANY handler — and when
    several are live at once, the one appearing FIRST IN `RULES`, which is a
    tie-break for determinism and NOT a claim that it is the more important of
    them. Every handler's own verdict is printed beside the headline, so a reader
    can check the choice rather than take it.

    When no handler is parked in a ledgered wait the headline says so
    (`NO_LEDGERED_STALL`) rather than naming a mechanism it did not observe. That is
    the honest answer for a stall that had already cleared by the time the assertion
    ran, and for a genuine code failure — the two are NOT distinguished here, and
    this docstring does not claim they are.
    """
    current = sys._current_frames()
    main_ident = threading.main_thread().ident
    handlers: list[tuple[str, str]] = []
    accept_parked = False
    stacks: list[str] = []

    for thread in threading.enumerate():
        frame = current.get(thread.ident)
        if frame is None or thread.ident == main_ident:
            continue
        frames = _frames(frame)
        blob = " ".join(name for name, _ in frames)
        stacks.append(
            f"--- thread {thread.name!r} daemon={thread.daemon} ---\n"
            + "".join(traceback.format_stack(frame))
        )
        if _HANDLER_MARKER in blob:
            handlers.append((thread.name, classify(frames)))
        elif _ACCEPT_MARKER in blob:
            accept_parked = True

    found = {mechanism for _, mechanism in handlers} - {"BLOCKED_ELSEWHERE"}
    if found:
        # Deterministic when several ledgered mechanisms are live at once: take the
        # earliest in RULES, so the verdict does not depend on thread order.
        order = [m for _, m in RULES]
        verdict = sorted(found, key=order.index)[0]
    elif handlers:
        verdict = "NO_LEDGERED_STALL"
    elif accept_parked:
        verdict = "NEVER_ACCEPTED"
    else:
        verdict = "NO_SERVER_THREAD_ALIVE"

    per_handler = " ".join(f"{n}={m}" for n, m in handlers) or "-"
    return (
        f"\nMECHANISM = {verdict}   "
        f"(handler threads={len(handlers)} [{per_handler}], "
        f"accept loop parked={accept_parked})"
        f"{note}\n" + "".join(stacks)
    )
