"""Unit tests for the bar-status poller + the three i3status-rust block scripts.

All OFFLINE — no network, no cluster, no Postgres. Every test feeds a MOCK input
(mock clawgate /api/tasks JSON, a mock open-mail count, a mock Alertmanager alert
list) and asserts:
  - correct count parsing per source,
  - correct i3status-rust JSON (icon / text / state),
  - HIDE-AT-ZERO (count 0 -> empty, invisible block),
  - FAIL-SAFE (malformed / empty / stale input -> neutral empty block, never a
    crash).

The scripts are extensionless (`bar-status-poll`, `i3status-clawgate`, ...), so
they are loaded via importlib.machinery.SourceFileLoader.

    run:  pytest scripts/tests/test_bar_status.py
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


poll = _load("bar-status-poll", "bar_status_poll")
clawgate_block = _load("i3status-clawgate", "i3status_clawgate")
mail_block = _load("i3status-mail", "i3status_mail")
alerts_block = _load("i3status-alerts", "i3status_alerts")


# --------------------------------------------------------------------------- #
# poller parse: clawgate
# --------------------------------------------------------------------------- #
def test_parse_clawgate_counts_only_pending_states():
    tasks = [
        {"id": 1, "status": "open"},
        {"id": 2, "status": "ready_for_review"},
        {"id": 3, "status": "in_progress"},   # agent working -> NOT operator-pending
        {"id": 4, "status": "complete"},
        {"id": 5, "status": "dismissed"},
    ]
    out = poll.parse_clawgate(tasks)
    assert out["count"] == 2
    assert out["state"] == "Warning"
    assert "#1" in out["detail"] and "#2" in out["detail"]


def test_parse_clawgate_zero_is_neutral():
    out = poll.parse_clawgate([{"id": 9, "status": "complete"}])
    assert out["count"] == 0
    assert out["state"] == "Idle"


def test_parse_clawgate_empty_list():
    out = poll.parse_clawgate([])
    assert out == {"count": 0, "state": "Idle", "detail": "no pending tasks"}


def test_parse_clawgate_tolerates_junk_elements():
    out = poll.parse_clawgate([None, "x", 3, {"status": "open", "id": 7}])
    assert out["count"] == 1


def test_parse_clawgate_malformed_toplevel_raises():
    # A non-list top level is a broken response -> raise (caller -> stale marker).
    with pytest.raises(ValueError):
        poll.parse_clawgate({"error": "nope"})


# --------------------------------------------------------------------------- #
# poller parse: mail
# --------------------------------------------------------------------------- #
def test_parse_mail_positive():
    out = poll.parse_mail(3)
    assert out["count"] == 3 and out["state"] == "Warning"


def test_parse_mail_zero_neutral():
    out = poll.parse_mail(0)
    assert out["count"] == 0 and out["state"] == "Idle"
    assert out["detail"] == "inbox clear"


def test_parse_mail_negative_clamped():
    assert poll.parse_mail(-5)["count"] == 0


# --------------------------------------------------------------------------- #
# poller parse: alerts
# --------------------------------------------------------------------------- #
def _alert(name, sev, state="active"):
    return {"labels": {"alertname": name, "severity": sev},
            "status": {"state": state}}


def test_parse_alerts_counts_warn_and_crit_excludes_housekeeping():
    alerts = [
        _alert("KubeJobFailed", "critical"),
        _alert("CPUThrottlingHigh", "warning"),
        _alert("NodeDiskIOSaturation", "critical"),
        _alert("Watchdog", "none"),          # excluded
        _alert("InfoInhibitor", "none"),     # excluded
        _alert("SomeInfo", "info"),          # excluded (severity)
    ]
    out = poll.parse_alerts(alerts)
    assert out["count"] == 3
    assert out["state"] == "Critical"      # >=1 critical
    assert "3 firing" in out["detail"] and "2 critical" in out["detail"]


def test_parse_alerts_warning_only_is_warning():
    out = poll.parse_alerts([_alert("CPUThrottlingHigh", "warning")])
    assert out["count"] == 1 and out["state"] == "Warning"


def test_parse_alerts_none_firing_neutral():
    out = poll.parse_alerts([_alert("Watchdog", "none")])
    assert out == {"count": 0, "state": "Idle", "detail": "no firing alerts"}


def test_parse_alerts_skips_non_active_state():
    out = poll.parse_alerts([_alert("KubeJobFailed", "critical", state="suppressed")])
    assert out["count"] == 0


def test_parse_alerts_tolerates_junk():
    out = poll.parse_alerts([None, {}, {"labels": "x"}, _alert("X", "warning")])
    assert out["count"] == 1


def test_parse_alerts_malformed_toplevel_raises():
    with pytest.raises(ValueError):
        poll.parse_alerts({"data": []})


# --------------------------------------------------------------------------- #
# poller: source() fail-safe wrapper turns any exception into a stale marker
# --------------------------------------------------------------------------- #
def test_source_wraps_exception_as_stale():
    def boom():
        raise RuntimeError("endpoint down")
    out = poll.source("clawgate", boom)
    assert out["state"] == "stale"
    assert out["count"] == 0
    assert "endpoint down" in out["error"]
    assert out["source"] == "clawgate" and "ts" in out


def test_source_success_stamps_meta():
    out = poll.source("mail", lambda: poll.parse_mail(2))
    assert out["count"] == 2 and out["source"] == "mail" and "ts" in out


# --------------------------------------------------------------------------- #
# poller: --mock end-to-end writes cache files + is fail-safe
# --------------------------------------------------------------------------- #
def test_mock_run_writes_all_three(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    # Silence the bar-signal (no i3status-rs in the test env anyway).
    monkeypatch.setattr(poll, "signal_bar", lambda name: None)

    tasks_f = tmp_path / "tasks.json"
    tasks_f.write_text(json.dumps([{"id": 1, "status": "open"},
                                   {"id": 2, "status": "ready_for_review"}]))
    alerts_f = tmp_path / "alerts.json"
    alerts_f.write_text(json.dumps([_alert("KubeJobFailed", "critical")]))

    rc = poll.main(["--mock-clawgate", str(tasks_f),
                    "--mock-mail", "4",
                    "--mock-alerts", str(alerts_f)])
    assert rc == 0

    cg = json.loads((tmp_path / "clawgate.json").read_text())
    ml = json.loads((tmp_path / "mail.json").read_text())
    al = json.loads((tmp_path / "alerts.json").read_text())
    assert cg["count"] == 2 and cg["state"] == "Warning"
    assert ml["count"] == 4 and ml["state"] == "Warning"
    assert al["count"] == 1 and al["state"] == "Critical"


def test_mock_run_malformed_clawgate_writes_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda name: None)
    bad = tmp_path / "bad.json"
    bad.write_text('{"not":"a list"}')
    rc = poll.main(["--mock-clawgate", str(bad)])
    assert rc == 0
    cg = json.loads((tmp_path / "clawgate.json").read_text())
    assert cg["state"] == "stale" and cg["count"] == 0


# --------------------------------------------------------------------------- #
# block scripts: render() — hide-at-zero + colour-when->0 + fail-safe
# --------------------------------------------------------------------------- #
BLOCKS = [
    ("clawgate", clawgate_block, "tasks", "Warning"),
    ("mail", mail_block, "mail", "Warning"),
    ("alerts", alerts_block, "bell", "Critical"),
]


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_hides_at_zero(name, mod, icon, default_state):
    out = mod.render({"count": 0, "state": "Idle"})
    assert out == {"text": "", "state": "Idle"}
    assert "icon" not in out            # truly invisible: no icon either


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_visible_and_coloured_when_positive(name, mod, icon, default_state):
    out = mod.render({"count": 3, "state": default_state})
    assert out["icon"] == icon
    assert out["text"] == "3" and out["short_text"] == "3"
    assert out["state"] == default_state


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_stale_is_invisible(name, mod, icon, default_state):
    assert mod.render({"count": 5, "state": "stale"}) == {"text": "", "state": "Idle"}


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_error_marker_is_invisible(name, mod, icon, default_state):
    assert mod.render({"count": 5, "state": "Warning", "error": "x"}) == \
        {"text": "", "state": "Idle"}


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_none_and_malformed_are_invisible(name, mod, icon, default_state):
    for bad in (None, [], "x", 3, {"count": "NaN"}, {}):
        out = mod.render(bad)
        assert out == {"text": "", "state": "Idle"}


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_defaults_state_when_missing_or_idle(name, mod, icon, default_state):
    # A positive count with a missing/Idle state must still colour (never neutral).
    out = mod.render({"count": 1})
    assert out["state"] == default_state
    out2 = mod.render({"count": 1, "state": "Idle"})
    assert out2["state"] == default_state


# --------------------------------------------------------------------------- #
# block scripts: end-to-end subprocess against a fixture cache dir
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("script,cachefile", [
    ("i3status-clawgate", "clawgate.json"),
    ("i3status-mail", "mail.json"),
    ("i3status-alerts", "alerts.json"),
])
def test_block_subprocess_missing_file_is_invisible(tmp_path, script, cachefile):
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"text": "", "state": "Idle"}


@pytest.mark.parametrize("script,cachefile,icon", [
    ("i3status-clawgate", "clawgate.json", "tasks"),
    ("i3status-mail", "mail.json", "mail"),
    ("i3status-alerts", "alerts.json", "bell"),
])
def test_block_subprocess_positive_renders(tmp_path, script, cachefile, icon):
    (tmp_path / cachefile).write_text(json.dumps(
        {"count": 2, "state": "Warning", "detail": "x"}))
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["icon"] == icon and out["text"] == "2"


@pytest.mark.parametrize("script,cachefile", [
    ("i3status-clawgate", "clawgate.json"),
    ("i3status-mail", "mail.json"),
    ("i3status-alerts", "alerts.json"),
])
def test_block_subprocess_corrupt_json_is_invisible(tmp_path, script, cachefile):
    (tmp_path / cachefile).write_text("{ this is not json ")
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"text": "", "state": "Idle"}
