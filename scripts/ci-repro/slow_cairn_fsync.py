"""Stall `os.fsync` IN THE TEST PROCESS — the cairn half of the gate failure.

**What it answers.** `tekton/devrc-pytests` fails on
`test_cairn_write.py::TestAppendLands::test_a_bullet_is_appended_and_the_status_is_
named` (and its four siblings, which `--dist loadfile` puts on the same worker) with

```
AssertionError: 🔴 cairn: the write did NOT happen — http://127.0.0.1:PORT
                unreachable: timed out
assert 7 == 0
```

on PRs whose diff cannot reach it. Measured on `devrc-ci-jfg67` (2026-09-02),
store root `/tmp/nix-build-devrc-pytests.drv-0/…/popen-gw1/…` — the step
container's ephemeral layer, the same device the store-api half stalls on.

**Why this is a third instrument and not a duplicate.** `slowfsync.c` above delays
the FIRST fsync per process by a hardcoded 65 s, sized against the store-api
suite's `HANG_TIMEOUT` of 60.0. The cairn bound is **`--timeout 5`**, passed by
`run_cairn` to the client subprocess — **twelve times tighter**, and correspondingly
far easier for the contended node to breach. Sizing a repro at 65 s here would work
but would say nothing about how little latency it actually takes.

It also must NOT be `LD_PRELOAD`. In this file the store server runs IN-PROCESS
while the cairn client is a SUBPROCESS, and `LD_PRELOAD` is inherited across
`exec()` — so it would stall the client too, muddying which side timed out. Patching
`os.fsync` in the test process stalls exactly the server, which is the condition
under test. No compiler needed.

**Usage.**

```bash
# control — shim attached but inert, same command line
PYTHONPATH=scripts/ci-repro nix develop . --command python3 -m pytest \
  scripts/tests/test_cairn_write.py::TestAppendLands -q -s -p slow_cairn_fsync

# reproduction — an 8 s stall against the 5 s client bound
SLOW_CAIRN_FSYNC_S=8 PYTHONPATH=scripts/ci-repro nix develop . --command python3 \
  -m pytest scripts/tests/test_cairn_write.py::TestAppendLands -q -s \
  -p slow_cairn_fsync --slow-cairn-fsync-selftest
```

**Measured 2026-09-02** on `origin/main` at `946a51f0`, one test:

| run | result |
|---|---|
| control (`SLOW_CAIRN_FSYNC_S` unset) | `1 passed in 1.04s`, `intercepted_fsyncs=2` |
| reproduction (`SLOW_CAIRN_FSYNC_S=8`) | `1 failed in 5.83s`, text identical to CI |

🔴 **The control's `intercepted_fsyncs=2` is the POSITIVE CONTROL and it is not
decoration.** It proves the append path really does issue the two fsyncs
`_replace_bytes` is documented to make — the file, then the parent directory —
inside the request. A zero there would mean the shim patched nothing and the
reproduction below would be a fact about the collection filter, not about fsync.

⚠ **The store was on TMPFS for both runs** (`/dev/shm/devrc-store-…`), i.e. with
`testlib.store_siting`'s mitigation fully in force. That is the point: this is a
LATENCY dependency, not a filesystem one. Siting the store off the contended disk
makes a breach far less likely; it does not remove the bound, and `store_siting`
falls back to disk in five documented ways without saying so.
"""
from __future__ import annotations

import os
import sys

_REAL_FSYNC = os.fsync
_HITS = {"n": 0}


def _delay() -> float:
    raw = os.environ.get("SLOW_CAIRN_FSYNC_S", "").strip()
    return float(raw) if raw else 0.0


def pytest_addoption(parser):
    parser.addoption(
        "--slow-cairn-fsync-selftest",
        action="store_true",
        help="fail loudly unless the shim attached and actually intercepted an fsync",
    )


def pytest_configure(config):
    delay = _delay()
    if config.getoption("--slow-cairn-fsync-selftest"):
        assert delay > 0, (
            f"SLOW_CAIRN_FSYNC_S={os.environ.get('SLOW_CAIRN_FSYNC_S')!r} gives "
            f"{delay}s — the shim would be inert, making this a CONTROL run and "
            f"not a reproduction"
        )

    import time

    def slow_fsync(fd):
        _HITS["n"] += 1
        if delay > 0:
            # Inside the request and before the response is written, exactly where
            # `server.py:_replace_bytes` issues the real one.
            time.sleep(delay)
        return _REAL_FSYNC(fd)

    os.fsync = slow_fsync


def pytest_unconfigure(config):
    os.fsync = _REAL_FSYNC
    # Always reported, armed or not: a shim that silently failed to attach gives a
    # clean green that means nothing, and a zero here is how you would see it.
    print(
        f"\nslow_cairn_fsync: delay={_delay()}s intercepted_fsyncs={_HITS['n']}",
        file=sys.stderr,
    )
    if config.getoption("--slow-cairn-fsync-selftest"):
        assert _HITS["n"] > 0, (
            "the shim intercepted ZERO fsyncs — nothing under test called os.fsync, "
            "so this run says nothing about the stall it claims to reproduce"
        )


def pytest_report_header(config):
    delay = _delay()
    if delay <= 0:
        return (
            "slow_cairn_fsync: INERT (SLOW_CAIRN_FSYNC_S unset or 0) "
            "— this is a CONTROL run"
        )
    return (
        f"slow_cairn_fsync: ARMED, {delay}s per fsync "
        f"(the cairn client bound is --timeout 5)"
    )
