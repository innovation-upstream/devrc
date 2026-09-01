"""Delay the server's REQUEST PATH, to reproduce the guard-arming flake.

Not a test. Loaded with `-p slow_arm`; `scripts/ci-repro/` is outside
`scripts/tests/` so `run-tests.sh` never collects it.

WHAT THIS REPRODUCES
--------------------
`TestAHungRoundTripSAYSWhichSideBlocked` stalls the server at a chosen site and
asserts the client gave up while it was stuck. Its precondition is

    assert stalled.is_set()

sampled at the instant the client's `timeout=CLIENT_BOUND` (0.25 s) expires.
That is an INSTANTANEOUS sample of a race: within those 250 ms the server must
accept the connection, spawn a handler thread, parse the request, authenticate,
meter, resolve the path, read the entry AND reach `_fsync_dir`. On an idle dev
host it does so in a few ms. Under the CI node's concurrency it does not always,
and the guard then fails with

    AssertionError: the server never reached the stall site, so the hang under
    test was NOT the one this test set up

which is a true statement about the RUN and a false accusation of the CODE.

This shim makes that deterministic by delaying the request path BEFORE the stall
site, which is what a contended CI node does by scheduling rather than by sleep.
It does not touch the stall itself, so the mechanism under test is unchanged —
only the arrival time at it.

USAGE
-----
    SLOW_ARM_S=0.5 python3 -m pytest scripts/tests/test_subsystem_store_api.py \
        -p slow_arm --slow-arm-selftest -k TestAHungRoundTripSAYSWhichSideBlocked

`SLOW_ARM_S` — seconds to delay each write request before the handler runs.
Unset or 0 makes the plugin inert, so the same command line is the control. Any
value above the suite's `CLIENT_BOUND` (0.25) should arm the failure.
"""

from __future__ import annotations

import os
import sys
import time


def _delay() -> float:
    raw = os.environ.get("SLOW_ARM_S", "").strip()
    return float(raw) if raw else 0.0


def pytest_addoption(parser):
    parser.addoption(
        "--slow-arm-selftest",
        action="store_true",
        help="fail loudly unless the shim attached and its delay is observable",
    )


def pytest_collection_finish(session):
    delay = _delay()
    module = sys.modules.get("subsystem_store_server")

    if session.config.getoption("--slow-arm-selftest"):
        # 🔴 The instrument's own controls, both watched to fire.
        assert module is not None, (
            "subsystem_store_server is not in sys.modules — the store-api test "
            "module was never imported, so this shim patched NOTHING and the "
            "result below is a fact about the collection filter, not the server"
        )
        assert delay > 0, (
            f"SLOW_ARM_S={os.environ.get('SLOW_ARM_S')!r} gives {delay}s — the "
            "shim would be inert, making this a control rather than a repro"
        )

    if module is None or delay <= 0:
        return

    handler = module.StoreRequestHandler
    real = handler._write

    def slow_write(self, *args, **kwargs):
        # BEFORE the handler body, so the delay lands on the way TO the stall
        # site rather than inside it.
        time.sleep(delay)
        return real(self, *args, **kwargs)

    handler._write = slow_write
    handler.do_POST = handler.do_PUT = handler.do_PATCH = handler.do_DELETE = slow_write
    print(f"\nslow_arm: PATCHED _write +{delay}s", file=sys.stderr)


def pytest_report_header(config):
    delay = _delay()
    if delay <= 0:
        return "slow_arm: INERT (SLOW_ARM_S unset or 0) — this is a CONTROL run"
    return f"slow_arm: ARMED, {delay}s before each write request"
