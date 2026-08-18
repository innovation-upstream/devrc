"""Shared fixtures for the Signal suites. Hermetic: no Postgres, MinIO, or network.

`scripts/signal/` is put on `sys.path` here (the mail-actions pattern) so every
suite imports the modules under test directly.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# 🔴 POPPED AT MODULE SCOPE, ABOVE EVERY IMPORT OF `consumer` — a fixture is far
# too late. `HEARTBEAT_INTERVAL`/`MAX_AGE`/`PATH` are parsed at consumer.py
# IMPORT time, and the test modules import consumer at their own module scope,
# i.e. during collection. An autouse fixture runs after all of that, so the
# first version of this cleanup was completely INERT while carrying a docstring
# claiming it had been measured and closed — the failing claim was the
# dangerous half, not the missing cleanup.
#
# Both symptoms, measured at the time this was written:
#   SIGNAL_HEARTBEAT_MAX_AGE=10  -> 1 failed  (10.0 >= 2 * 30.0)
#   SIGNAL_HEARTBEAT_INTERVAL=abc -> 10 COLLECTION ERRORS, suite interrupted
# A suite whose configuration is decided by the ambient environment is blind on
# exactly that dimension, and a host exporting either turns the gate red for a
# reason that has nothing to do with the code.
for _var in ("SIGNAL_HEARTBEAT_PATH", "SIGNAL_HEARTBEAT_INTERVAL",
             "SIGNAL_HEARTBEAT_MAX_AGE"):
    os.environ.pop(_var, None)

SIGNAL_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"

for _p in (str(SIGNAL_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fakepg  # noqa: E402


def load_corpus() -> dict:
    """The envelope corpus, minus its `_README` key."""
    raw = json.loads((FIXTURES / "envelopes.json").read_text(encoding="utf-8"))
    corpus = {k: v for k, v in raw.items() if not k.startswith("_")}
    if not corpus:
        raise AssertionError(
            "HARNESS BROKEN: the envelope corpus is empty — every parse assertion "
            "would pass vacuously")
    return corpus


@pytest.fixture()
def corpus() -> dict:
    return load_corpus()


@pytest.fixture()
def db():
    """A `SignalDB` on the sqlite substrate, schema applied."""
    import _signal_db

    conn_db = fakepg.open_db(_signal_db)
    yield conn_db
    conn_db.conn.close()


@pytest.fixture()
def recording():
    """A `(SignalDB, RecordingConn)` pair that executes nothing."""
    import _signal_db

    return fakepg.recording_db(_signal_db)


@pytest.fixture(autouse=True)
def _operator_approval_env(monkeypatch):
    """Most suites act AS THE OPERATOR, so the approval token is present.

    `approve_draft()` refuses without `SIGNAL_APPROVAL_TOKEN` — the operator-only
    variable that keeps a drafting agent from approving its own draft. Setting it
    here keeps every other suite readable; `test_approval_gate.py` deletes it
    explicitly and asserts the refusal, so the guard is still measured.
    """
    monkeypatch.setenv("SIGNAL_APPROVAL_TOKEN", "operator-shell-token")


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """Fail loudly if any test reaches for `requests`.

    The suites inject every transport; a test that quietly imported `requests`
    and hit the network would make the "hermetic" claim false without failing.
    """
    import builtins

    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        # A test that installs a FAKE `requests` in sys.modules is fine — that is
        # the injected-transport path. Reaching for the REAL library is not.
        if name == "requests" and "requests" not in sys.modules:
            raise AssertionError(
                "a Signal test imported `requests` — every transport in these "
                "suites is injected; nothing here may touch the network")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
