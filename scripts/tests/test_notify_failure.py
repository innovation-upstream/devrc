"""Tests for scripts/notify-failure.sh — the systemd OnFailure toast handler.

Hermetic: drives the bash script as a subprocess with an injected fake
`notify-send` on PATH and (for the graphical case) DISPLAY/DBUS pre-set so the
handler's desktop-env probe short-circuits without needing a real i3/X session.

Two contracts under test:
  * headless (NOTIFY_FAILURE_GRAPHICAL unset/0): exits 0, fires NO toast, logs a
    journal hint to stderr — never errors (an erroring OnFailure handler is
    itself an invisible failure).
  * graphical (NOTIFY_FAILURE_GRAPHICAL=1 + a reachable session bus): calls
    notify-send with the failed unit name in the summary.
"""
import os
import shutil
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
_HANDLER = os.path.join(_HERE, "..", "notify-failure.sh")


def _make_fake_notify_send(dir_path):
    """Write a stub `notify-send` that records argv to <dir>/notify-send.args."""
    out = os.path.join(dir_path, "notify-send.args")
    stub = os.path.join(dir_path, "notify-send")
    # $@ / %s\n are literal bash; only `out` is interpolated (via concatenation
    # so no Python %-formatting touches the bash body).
    # Resolve bash to an absolute path: the nix flake-check sandbox has no
    # /usr/bin/env, so a `#!/usr/bin/env bash` stub would fail to exec there.
    _bash = shutil.which("bash") or "/bin/bash"
    body = "#!" + _bash + "\nprintf '%s\\n' \"$@\" > '" + out + "'\n"
    with open(stub, "w") as fh:
        fh.write(body)
    os.chmod(stub, 0o755)
    return out


def _run(env_extra, unit="repo-cos.service"):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        ["bash", _HANDLER, unit],
        capture_output=True, text=True, env=env, timeout=15)


def test_headless_no_toast_exits_zero(tmp_path):
    """No graphical gate → exit 0, journal hint on stderr, notify-send never run."""
    argfile = _make_fake_notify_send(str(tmp_path))
    r = _run({
        "PATH": "%s:%s" % (tmp_path, os.environ.get("PATH", "")),
        "NOTIFY_FAILURE_GRAPHICAL": "0",
        # ensure the desktop probe can't accidentally find a bus
        "DISPLAY": "", "DBUS_SESSION_BUS_ADDRESS": "",
    })
    assert r.returncode == 0
    assert "repo-cos.service" in r.stderr
    assert "journalctl --user -u repo-cos.service" in r.stderr
    assert not os.path.exists(argfile)  # NO toast fired


def test_graphical_gate_unset_is_headless(tmp_path):
    """Missing NOTIFY_FAILURE_GRAPHICAL behaves like headless (default 0)."""
    argfile = _make_fake_notify_send(str(tmp_path))
    env = {"PATH": "%s:%s" % (tmp_path, os.environ.get("PATH", "")),
           "DISPLAY": "", "DBUS_SESSION_BUS_ADDRESS": ""}
    env.pop("NOTIFY_FAILURE_GRAPHICAL", None)
    r = _run(env)
    assert r.returncode == 0
    assert not os.path.exists(argfile)


def test_graphical_fires_toast_with_unit_name(tmp_path):
    """Graphical + a reachable session bus → notify-send called with the unit."""
    argfile = _make_fake_notify_send(str(tmp_path))
    r = _run({
        "PATH": "%s:%s" % (tmp_path, os.environ.get("PATH", "")),
        "NOTIFY_FAILURE_GRAPHICAL": "1",
        # Pre-set both so ensure_desktop_env short-circuits (no i3/pgrep needed).
        "DISPLAY": ":0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }, unit="mail-actions-archive.service")
    assert r.returncode == 0
    assert os.path.exists(argfile), "notify-send should have been invoked"
    with open(argfile) as fh:
        args = fh.read()
    assert "mail-actions-archive.service" in args
    assert "critical" in args  # urgency=critical (sticky)


def test_default_unit_when_no_arg(tmp_path):
    """No arg → 'unknown.unit', still exits 0 (never errors)."""
    _make_fake_notify_send(str(tmp_path))
    env = dict(os.environ)
    env.update({"PATH": "%s:%s" % (tmp_path, os.environ.get("PATH", "")),
                "NOTIFY_FAILURE_GRAPHICAL": "0",
                "DISPLAY": "", "DBUS_SESSION_BUS_ADDRESS": ""})
    r = subprocess.run(["bash", _HANDLER], capture_output=True, text=True,
                       env=env, timeout=15)
    assert r.returncode == 0
    assert "unknown.unit" in r.stderr
