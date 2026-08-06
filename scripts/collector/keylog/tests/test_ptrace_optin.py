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
