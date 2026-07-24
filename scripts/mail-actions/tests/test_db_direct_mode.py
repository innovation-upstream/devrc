"""Tests for MailDB's connection-mode selection (direct vs kubectl port-forward).

Offline: psycopg2.connect and subprocess.Popen are mocked, so no real DB, no real
kubectl. We assert:
  * env UNSET  → port-forward path: a `kubectl port-forward` subprocess is spawned and
    the connection targets 127.0.0.1:<local-port> (existing behavior, unchanged).
  * MAILBOX_PG_HOST set → DIRECT path: NO subprocess spawned; connection targets the
    override host (and MAILBOX_PG_PORT, when set).
  * MAILBOX_PG_DIRECT=1 → DIRECT path using the DSN's OWN host/port.
Plus pure kwargs-parsing tests for `_dsn_connect_kwargs`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _db  # noqa: E402

DSN = "postgres://mailer:s3cr3t@mailbox-postgres.mailbox.svc.cluster.local:5432/mailbox"


# -- pure DSN → connect-kwargs -------------------------------------------------

def test_dsn_connect_kwargs_keeps_dsn_host_when_no_override():
    kw = _db._dsn_connect_kwargs(DSN)
    assert kw["host"] == "mailbox-postgres.mailbox.svc.cluster.local"
    assert kw["port"] == 5432
    assert kw["user"] == "mailer"
    assert kw["password"] == "s3cr3t"
    assert kw["dbname"] == "mailbox"


def test_dsn_connect_kwargs_overrides_host_and_port():
    kw = _db._dsn_connect_kwargs(DSN, host="127.0.0.1", port=54321)
    assert kw["host"] == "127.0.0.1"
    assert kw["port"] == 54321
    # credentials still come from the DSN
    assert kw["user"] == "mailer"
    assert kw["dbname"] == "mailbox"


def test_dsn_connect_kwargs_defaults_port_5432_when_absent():
    kw = _db._dsn_connect_kwargs("postgres://u:p@h/db")
    assert kw["port"] == 5432


def test_dsn_connect_kwargs_rejects_non_postgres_scheme():
    try:
        _db._dsn_connect_kwargs("mysql://u:p@h/db")
    except ValueError:
        return
    raise AssertionError("expected ValueError on non-postgres scheme")


# -- direct-target env contract ------------------------------------------------

def test_direct_target_none_when_no_env(monkeypatch):
    for k in ("MAILBOX_PG_HOST", "MAILBOX_PG_PORT", "MAILBOX_PG_DIRECT"):
        monkeypatch.delenv(k, raising=False)
    assert _db._direct_target() is None


def test_direct_target_host_only(monkeypatch):
    monkeypatch.delenv("MAILBOX_PG_DIRECT", raising=False)
    monkeypatch.delenv("MAILBOX_PG_PORT", raising=False)
    monkeypatch.setenv("MAILBOX_PG_HOST", "svc.internal")
    assert _db._direct_target() == ("svc.internal", None)


def test_direct_target_host_and_port(monkeypatch):
    monkeypatch.setenv("MAILBOX_PG_HOST", "svc.internal")
    monkeypatch.setenv("MAILBOX_PG_PORT", "6543")
    assert _db._direct_target() == ("svc.internal", 6543)


def test_direct_target_flag_only_uses_dsn_host(monkeypatch):
    monkeypatch.delenv("MAILBOX_PG_HOST", raising=False)
    monkeypatch.delenv("MAILBOX_PG_PORT", raising=False)
    monkeypatch.setenv("MAILBOX_PG_DIRECT", "1")
    assert _db._direct_target() == (None, None)


def test_direct_target_flag_falsey_is_ignored(monkeypatch):
    monkeypatch.delenv("MAILBOX_PG_HOST", raising=False)
    monkeypatch.setenv("MAILBOX_PG_DIRECT", "0")
    assert _db._direct_target() is None


# -- MailDB.__enter__ mode selection (psycopg2 + subprocess mocked) ------------

class _FakeConn:
    def __init__(self):
        self.autocommit = None
        self.closed = False

    def close(self):
        self.closed = True


def _patch_conn(monkeypatch):
    """Capture the kwargs psycopg2.connect is called with; return a recorder dict."""
    rec = {"connect_kwargs": None, "conn": _FakeConn()}

    def fake_connect(**kwargs):
        rec["connect_kwargs"] = kwargs
        return rec["conn"]

    monkeypatch.setattr(_db.psycopg2, "connect", fake_connect)
    return rec


def _forbid_popen(monkeypatch):
    """Fail loudly if the code spawns any subprocess in direct mode."""
    def boom(*a, **k):  # pragma: no cover - only hit on regression
        raise AssertionError("subprocess.Popen must NOT be called in direct mode")

    monkeypatch.setattr(_db.subprocess, "Popen", boom)


def _clear_direct_env(monkeypatch):
    for k in ("MAILBOX_PG_HOST", "MAILBOX_PG_PORT", "MAILBOX_PG_DIRECT"):
        monkeypatch.delenv(k, raising=False)


def test_direct_mode_host_override_no_port_forward(monkeypatch):
    _clear_direct_env(monkeypatch)
    monkeypatch.setenv("MAILBOX_PG_HOST", "mailbox-postgres.mailbox.svc.cluster.local")
    rec = _patch_conn(monkeypatch)
    _forbid_popen(monkeypatch)  # any Popen call fails the test

    with _db.MailDB(dsn=DSN) as db:
        assert db.conn is rec["conn"]
        assert db._pf is None  # no port-forward process
    kw = rec["connect_kwargs"]
    assert kw["host"] == "mailbox-postgres.mailbox.svc.cluster.local"
    assert kw["port"] == 5432  # from the DSN (no MAILBOX_PG_PORT override)
    assert kw["user"] == "mailer"


def test_direct_mode_flag_uses_dsn_host(monkeypatch):
    _clear_direct_env(monkeypatch)
    monkeypatch.setenv("MAILBOX_PG_DIRECT", "true")
    rec = _patch_conn(monkeypatch)
    _forbid_popen(monkeypatch)

    with _db.MailDB(dsn=DSN):
        pass
    kw = rec["connect_kwargs"]
    # DSN's own host is honored (not 127.0.0.1).
    assert kw["host"] == "mailbox-postgres.mailbox.svc.cluster.local"
    assert kw["port"] == 5432


def test_direct_mode_port_override(monkeypatch):
    _clear_direct_env(monkeypatch)
    monkeypatch.setenv("MAILBOX_PG_HOST", "svc.internal")
    monkeypatch.setenv("MAILBOX_PG_PORT", "6543")
    rec = _patch_conn(monkeypatch)
    _forbid_popen(monkeypatch)

    with _db.MailDB(dsn=DSN):
        pass
    kw = rec["connect_kwargs"]
    assert (kw["host"], kw["port"]) == ("svc.internal", 6543)


class _FakePopen:
    """Stand-in for the kubectl port-forward subprocess."""

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.stderr = None
        self._alive = True

    def poll(self):
        return None  # still running

    def terminate(self):
        self._alive = False

    def wait(self, timeout=None):
        return 0


def test_port_forward_default_when_no_env(monkeypatch):
    _clear_direct_env(monkeypatch)  # nothing set → port-forward path
    rec = _patch_conn(monkeypatch)

    spawned = {}

    def fake_popen(argv, **kwargs):
        spawned["argv"] = argv
        return _FakePopen(argv, **kwargs)

    monkeypatch.setattr(_db.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(_db, "_free_local_port", lambda: 55555)
    # Skip the real socket readiness wait.
    monkeypatch.setattr(_db.MailDB, "_wait_for_port", lambda self, host, port: None)

    with _db.MailDB(dsn=DSN) as db:
        assert db._pf is not None  # a port-forward WAS spawned

    # kubectl port-forward was invoked for the mailbox service on the free local port.
    argv = spawned["argv"]
    assert argv[0] == "kubectl"
    assert "port-forward" in argv
    assert "55555:5432" in argv
    # Connection targets the local forwarded port, not the DSN host.
    kw = rec["connect_kwargs"]
    assert kw["host"] == "127.0.0.1"
    assert kw["port"] == 55555
