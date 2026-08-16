"""Shared fixtures for the Signal suites. Hermetic: no Postgres, MinIO, or network.

`scripts/signal/` is put on `sys.path` here (the mail-actions pattern) so every
suite imports the modules under test directly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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
