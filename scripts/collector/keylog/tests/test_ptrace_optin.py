"""Regression tests for keylog._allow_any_ptracer — the PR_SET_PTRACER_ANY opt-in
that lets the sibling spin-capture watcher (py-spy) attach under Yama
ptrace_scope=1 without persisting ptrace_scope=0 host-wide.

RED before the feature: keylog._allow_any_ptracer does not exist, so import/attr
resolution fails and every test here errors. GREEN after.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import keylog  # noqa: E402


class _FakeLibc:
    """Records prctl() calls; tolerates .restype/.argtypes assignment."""

    def __init__(self, rc=0):
        self.calls = []
        self._rc = rc

        def prctl(option, arg2, a3, a4, a5):
            self.calls.append((option, arg2, a3, a4, a5))
            return self._rc

        self.prctl = prctl


def _install_fake_cdll(monkeypatch, fake, expect_called=True):
    called = {"n": 0}

    def fake_cdll(name, *a, **kw):
        called["n"] += 1
        return fake

    monkeypatch.setattr("ctypes.CDLL", fake_cdll)
    return called


def test_optin_calls_prctl_with_ptracer_any(monkeypatch):
    # PR_SET_PTRACER = 0x59616d61, PR_SET_PTRACER_ANY = (unsigned long)-1.
    import ctypes

    monkeypatch.setenv("KEYLOG_ALLOW_ANY_PTRACER", "1")
    fake = _FakeLibc(rc=0)
    _install_fake_cdll(monkeypatch, fake)

    keylog._allow_any_ptracer()

    assert len(fake.calls) == 1, "prctl must be called exactly once"
    option, arg2, a3, a4, a5 = fake.calls[0]
    assert option == 0x59616D61, "PR_SET_PTRACER constant"
    assert arg2 == ctypes.c_ulong(-1).value, "PR_SET_PTRACER_ANY = (unsigned long)-1"
    assert (a3, a4, a5) == (0, 0, 0)


def test_gate_off_is_a_noop(monkeypatch):
    monkeypatch.delenv("KEYLOG_ALLOW_ANY_PTRACER", raising=False)

    def boom(*a, **kw):
        raise AssertionError("ctypes.CDLL must not be touched when gate is off")

    monkeypatch.setattr("ctypes.CDLL", boom)
    keylog._allow_any_ptracer()  # must return without touching libc


def test_gate_accepts_truthy_spellings(monkeypatch):
    for val in ("1", "true", "YES", "on", " On "):
        fake = _FakeLibc(rc=0)
        monkeypatch.setenv("KEYLOG_ALLOW_ANY_PTRACER", val)
        _install_fake_cdll(monkeypatch, fake)
        keylog._allow_any_ptracer()
        assert len(fake.calls) == 1, f"{val!r} should enable the opt-in"


def test_gate_rejects_falsey_spellings(monkeypatch):
    for val in ("0", "false", "no", "", "off", "garbage"):
        monkeypatch.setenv("KEYLOG_ALLOW_ANY_PTRACER", val)

        def boom(*a, **kw):
            raise AssertionError(f"{val!r} must not enable the opt-in")

        monkeypatch.setattr("ctypes.CDLL", boom)
        keylog._allow_any_ptracer()


def test_fail_soft_on_libc_error(monkeypatch):
    # An EINVAL/older-kernel/missing-libc path must NEVER raise — the collector
    # must keep running even if the opt-in cannot be made.
    monkeypatch.setenv("KEYLOG_ALLOW_ANY_PTRACER", "1")

    def raising_cdll(*a, **kw):
        raise OSError("libc.so.6 not found")

    monkeypatch.setattr("ctypes.CDLL", raising_cdll)
    keylog._allow_any_ptracer()  # no exception => pass


def test_nonzero_rc_does_not_raise(monkeypatch):
    # prctl returning non-zero (e.g. EINVAL on an ancient kernel) is logged, not fatal.
    monkeypatch.setenv("KEYLOG_ALLOW_ANY_PTRACER", "1")
    fake = _FakeLibc(rc=-1)
    _install_fake_cdll(monkeypatch, fake)
    keylog._allow_any_ptracer()
    assert len(fake.calls) == 1


def test_nonzero_rc_logs_errno(monkeypatch, capsys):
    # The rc != 0 path must be OBSERVABLE, not just non-fatal: it prints the
    # errno so a failed opt-in is diagnosable. Pins mutant M7 (`if rc != 0:` ->
    # `if False:`), which fails soft AND silently — with the log gone this branch
    # emits nothing and the assertion below goes red.
    monkeypatch.setenv("KEYLOG_ALLOW_ANY_PTRACER", "1")
    fake = _FakeLibc(rc=-1)
    _install_fake_cdll(monkeypatch, fake)
    keylog._allow_any_ptracer()
    err = capsys.readouterr().err
    assert "errno=" in err, "a non-zero rc must be logged with its errno="
    assert "failed" in err.lower()


def test_main_invokes_the_optin(monkeypatch, tmp_path):
    # The helper is well-tested in ISOLATION; its WIRING into main() is the seam
    # nobody owned. Pins mutant M4 (delete `_allow_any_ptracer()` from main): with
    # the call removed the recorder never fires and this goes red. We stub out the
    # X dependency (absent in the hermetic env) and make KeyLogger construction
    # raise an expected X-lifecycle OSError so main() returns 0 after the opt-in.
    import types

    called = {"n": 0}
    monkeypatch.setattr(
        keylog, "_allow_any_ptracer",
        lambda: called.__setitem__("n", called["n"] + 1),
    )

    # main() does `from Xlib.error import ...` before constructing the logger, and
    # python-xlib is not installed in the hermetic test env. Inject a stub package
    # so the import resolves without a real X server.
    fake_xlib = types.ModuleType("Xlib")
    fake_xlib_error = types.ModuleType("Xlib.error")

    class _CCE(Exception):
        pass

    class _DCE(Exception):
        pass

    fake_xlib_error.ConnectionClosedError = _CCE
    fake_xlib_error.DisplayConnectionError = _DCE
    fake_xlib.error = fake_xlib_error
    monkeypatch.setitem(sys.modules, "Xlib", fake_xlib)
    monkeypatch.setitem(sys.modules, "Xlib.error", fake_xlib_error)

    def boom(*a, **kw):
        raise OSError("no X server in the test sandbox")

    monkeypatch.setattr(keylog, "KeyLogger", boom)
    monkeypatch.setenv("ACTIVITY_SPOOL_DIR", str(tmp_path / "spool"))

    rc = keylog.main([])

    assert called["n"] == 1, "main() must invoke _allow_any_ptracer exactly once"
    assert rc == 0


def test_revoke_clears_the_optin(monkeypatch):
    # The revoke path calls prctl(PR_SET_PTRACER, 0) and, on success, flips the
    # bookkeeping flag back off so it does not fire again every 60s.
    fake = _FakeLibc(rc=0)
    _install_fake_cdll(monkeypatch, fake)
    monkeypatch.setattr(keylog, "_ptracer_opt_in_active", True, raising=False)
    keylog._revoke_any_ptracer()
    assert len(fake.calls) == 1, "revoke must call prctl exactly once"
    option, arg2, a3, a4, a5 = fake.calls[0]
    assert option == 0x59616D61, "PR_SET_PTRACER constant"
    assert arg2 == 0, "clearing sets the ptracer to 0 (no allowed tracer)"
    assert (a3, a4, a5) == (0, 0, 0)
    assert keylog._ptracer_opt_in_active is False


def test_maybe_revoke_gated_on_flag_and_sentinel(monkeypatch, tmp_path):
    # _maybe_revoke_ptracer must NOT revoke unless BOTH (a) we actually opted in
    # and (b) the watcher is done (a .captured or .giveup sentinel exists).
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    revoked = {"n": 0}
    monkeypatch.setattr(keylog, "_revoke_any_ptracer",
                        lambda: revoked.__setitem__("n", revoked["n"] + 1))

    # opt-in inactive, no sentinel -> no revoke
    monkeypatch.setattr(keylog, "_ptracer_opt_in_active", False, raising=False)
    keylog._maybe_revoke_ptracer()
    assert revoked["n"] == 0

    # opt-in active but watcher NOT done -> no revoke
    monkeypatch.setattr(keylog, "_ptracer_opt_in_active", True, raising=False)
    keylog._maybe_revoke_ptracer()
    assert revoked["n"] == 0

    # opt-in active AND watcher done -> revoke fires
    d = tmp_path / "keylog-spin"
    d.mkdir()
    (d / ".captured").write_text("")
    keylog._maybe_revoke_ptracer()
    assert revoked["n"] == 1
