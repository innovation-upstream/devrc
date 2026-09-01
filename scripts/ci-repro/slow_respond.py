"""Stall the store-api server's response path, to reproduce the RAW-SOCKET flake.

Not a test. Loaded explicitly with `-p slow_respond` (see README); nothing
imports it, and `scripts/ci-repro/` is outside `scripts/tests/` so `run-tests.sh`
never collects it.

WHAT THIS REPRODUCES, AND HOW IT DIFFERS FROM `slowfsync.c`
----------------------------------------------------------
`slowfsync.c` stalls one fsync past `HANG_TIMEOUT` (60.0) and lands on the
`fetch(...)`/`http.client` sites, which raise `TimeoutError`.

The raw-socket sites are a SEPARATE and much more sensitive population. They
read with `sock.settimeout(settle)` where `settle` is **3.0**, and swallow the
timeout — so a stall of just over 3 s (twenty times smaller than the one
`slowfsync.c` has to manufacture, and correspondingly far more common under the
disk contention that PR #1181 measured) makes the client return an EMPTY buffer
and sail on. No exception is raised anywhere; the test fails later, on a count
assertion, with a message about the opposite condition.

So this shim delays the response rather than the fsync, and by seconds rather
than by a minute.

USAGE
-----
    SLOW_RESPOND_S=5 python3 -m pytest scripts/tests/test_subsystem_store_api.py \
        -p slow_respond -k TestTheBackstopNeverSendsASecondResponse

`SLOW_RESPOND_S` — seconds to sleep before the first byte of each delayed
response. Unset or 0 makes the plugin completely inert, so the same command line
gives the control run.

`SLOW_RESPOND_PATHS` — comma-separated request-path prefixes to delay; default
`/api/v1/`. `/healthz` is deliberately NOT delayed by default: the positive
controls that prove the reader can see TWO responses pipeline two `/healthz`
GETs, and delaying those would blunt the very control that keeps the verdict
honest.

🔴 THE INSTRUMENT IS VALIDATED BEFORE ITS VERDICT IS READ. `--slow-respond-selftest`
asserts the patch actually applied and that the delay is observable; a shim that
silently failed to attach would report a clean green that means nothing.
"""

from __future__ import annotations

import os
import sys
import time

_PATCHED: list[str] = []


def _delay_seconds() -> float:
    raw = os.environ.get("SLOW_RESPOND_S", "").strip()
    if not raw:
        return 0.0
    return float(raw)


def _delayed_prefixes() -> tuple[str, ...]:
    raw = os.environ.get("SLOW_RESPOND_PATHS", "/api/v1/")
    return tuple(p for p in (s.strip() for s in raw.split(",")) if p)


def pytest_addoption(parser):
    parser.addoption(
        "--slow-respond-selftest",
        action="store_true",
        help="fail loudly unless the shim attached and its delay is observable",
    )


def pytest_collection_finish(session):
    """Attach AFTER collection: the test module loads `server.py` by path at
    import time and registers it as `subsystem_store_server`, so the module
    object does not exist any earlier."""
    delay = _delay_seconds()
    module = sys.modules.get("subsystem_store_server")

    if session.config.getoption("--slow-respond-selftest"):
        # 🔴 Negative control for the instrument itself. Both of these have
        # been watched to fire: run without `-k` matching the store-api file and
        # the module is absent; typo the env var and the delay is 0.
        assert module is not None, (
            "subsystem_store_server is not in sys.modules — the store-api test "
            "module was never imported, so this shim patched NOTHING and any "
            "result below is a fact about the collection filter, not the server"
        )
        assert delay > 0, (
            f"SLOW_RESPOND_S={os.environ.get('SLOW_RESPOND_S')!r} gives a delay of "
            f"{delay}s — the shim would be inert and the run would be a control, "
            "not a reproduction"
        )

    if module is None or delay <= 0:
        return

    handler = module.StoreRequestHandler
    real = handler._respond
    prefixes = _delayed_prefixes()

    def slow_respond(self, *args, **kwargs):
        # Delay BEFORE the first byte goes out, which is where a blocked
        # write-path syscall actually sits. `self.path` is the request target.
        if any(str(getattr(self, "path", "")).startswith(p) for p in prefixes):
            time.sleep(delay)
        return real(self, *args, **kwargs)

    handler._respond = slow_respond
    _PATCHED.append(f"{handler.__name__}._respond +{delay}s on {prefixes}")
    print(f"\nslow_respond: PATCHED {_PATCHED[-1]}", file=sys.stderr)


def pytest_report_header(config):
    delay = _delay_seconds()
    if delay <= 0:
        return "slow_respond: INERT (SLOW_RESPOND_S unset or 0) — this is a CONTROL run"
    return f"slow_respond: ARMED, {delay}s before each response on {_delayed_prefixes()}"
