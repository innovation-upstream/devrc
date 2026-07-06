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
civitai_block = _load("i3status-civitai", "i3status_civitai")


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
# poller: civitai source (separate CLIENT-prod alerts block, own kubeconfig)
# --------------------------------------------------------------------------- #
def test_civitai_uses_distinct_signal():
    # signal 14 must be free of the existing three so pkill -RTMIN+14 refreshes
    # exactly the civitai block (11/12/13 are clawgate/mail/alerts).
    assert poll.SIGNALS["civitai"] == 14
    assert 14 not in {poll.SIGNALS["clawgate"], poll.SIGNALS["mail"],
                      poll.SIGNALS["alerts"]}


def test_civitai_parses_same_severity_filter_as_alerts():
    # civitai reuses parse_alerts, so warn|critical count + Critical state hold.
    alerts = [
        _alert("KubeJobFailed", "critical"),
        _alert("CPUThrottlingHigh", "warning"),
        _alert("Watchdog", "none"),          # excluded (housekeeping)
        _alert("InfoInhibitor", "none"),     # excluded (housekeeping)
        _alert("SomeInfo", "info"),          # excluded (severity)
    ]
    out = poll.parse_alerts(alerts)
    assert out["count"] == 2 and out["state"] == "Critical"


def test_civitai_fetch_stale_when_kubeconfig_missing(monkeypatch):
    # Missing client kubeconfig -> stale (never spawns kubectl, never crashes).
    monkeypatch.setattr(poll, "CIVITAI_KUBECONFIG", "/no/such/kubeconfig")
    out = poll.source("civitai", poll.fetch_civitai)
    assert out["state"] == "stale" and out["count"] == 0
    assert out["source"] == "civitai"


def test_mock_run_writes_civitai(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda name: None)
    alerts_f = tmp_path / "civ.json"
    alerts_f.write_text(json.dumps([
        _alert("KubeJobFailed", "critical"),
        _alert("CPUThrottlingHigh", "warning"),
    ]))
    rc = poll.main(["--mock-civitai", str(alerts_f)])
    assert rc == 0
    civ = json.loads((tmp_path / "civitai.json").read_text())
    assert civ["count"] == 2 and civ["state"] == "Critical"
    assert civ["source"] == "civitai"


def test_mock_run_malformed_civitai_writes_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda name: None)
    bad = tmp_path / "bad.json"
    bad.write_text('{"not":"a list"}')
    rc = poll.main(["--mock-civitai", str(bad)])
    assert rc == 0
    civ = json.loads((tmp_path / "civitai.json").read_text())
    assert civ["state"] == "stale" and civ["count"] == 0


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
    ("civitai", civitai_block, "bell", "Critical"),
]


def _expected_text(mod, count):
    # i3status-civitai prefixes the count with its `civ` LABEL; the others don't.
    label = getattr(mod, "LABEL", None)
    return ("%s %d" % (label, count)) if label else str(count)


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_hides_at_zero(name, mod, icon, default_state):
    out = mod.render({"count": 0, "state": "Idle"})
    assert out == {"text": "", "state": "Idle"}
    assert "icon" not in out            # truly invisible: no icon either


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_visible_and_coloured_when_positive(name, mod, icon, default_state):
    out = mod.render({"count": 3, "state": default_state})
    exp = _expected_text(mod, 3)
    assert out["icon"] == icon
    assert out["text"] == exp and out["short_text"] == exp
    assert out["state"] == default_state


def test_civitai_block_labels_count_distinctly():
    # The civitai block must be visually distinguishable from homelab alerts:
    # its text carries the `civ` label prefix, alerts' does not.
    civ = civitai_block.render({"count": 317, "state": "Critical"})
    hl = alerts_block.render({"count": 317, "state": "Critical"})
    assert civ["text"] == "civ 317" and civ["state"] == "Critical"
    assert hl["text"] == "317"
    assert civ["text"] != hl["text"]


# red_above threshold — neutral at/below the standing backlog, red only above it.
ALERT_BLOCKS = [("alerts", alerts_block), ("civitai", civitai_block)]


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_neutral_at_or_below_baseline(name, mod):
    for count in (25, 30):  # <= red_above=30
        out = mod.render({"count": count, "state": "Critical"}, red_above=30)
        assert out["state"] == "Idle"          # visible but NOT coloured
        assert out["icon"] == "bell"           # still shown (not hidden)
        assert str(count) in out["text"]


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_colours_when_over_baseline(name, mod):
    out = mod.render({"count": 31, "state": "Critical"}, red_above=30)
    assert out["state"] == "Critical"          # above the baseline -> red


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_zero_is_backward_compatible(name, mod):
    # default (no threshold) still colours whenever count > 0
    assert mod.render({"count": 1, "state": "Critical"})["state"] == "Critical"


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_still_hides_at_zero(name, mod):
    assert mod.render({"count": 0, "state": "Idle"}, red_above=30) == \
        {"text": "", "state": "Idle"}


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_arg_parsing(name, mod, monkeypatch):
    monkeypatch.setattr(sys, "argv", [name, "--red-above", "42"])
    assert mod._red_above_arg() == 42
    monkeypatch.setattr(sys, "argv", [name])            # absent -> 0
    assert mod._red_above_arg() == 0
    monkeypatch.setattr(sys, "argv", [name, "--red-above", "nan"])  # junk -> 0
    assert mod._red_above_arg() == 0


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
    ("i3status-civitai", "civitai.json"),
])
def test_block_subprocess_missing_file_is_invisible(tmp_path, script, cachefile):
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"text": "", "state": "Idle"}


@pytest.mark.parametrize("script,cachefile,icon,text", [
    ("i3status-clawgate", "clawgate.json", "tasks", "2"),
    ("i3status-mail", "mail.json", "mail", "2"),
    ("i3status-alerts", "alerts.json", "bell", "2"),
    ("i3status-civitai", "civitai.json", "bell", "civ 2"),
])
def test_block_subprocess_positive_renders(tmp_path, script, cachefile, icon, text):
    (tmp_path / cachefile).write_text(json.dumps(
        {"count": 2, "state": "Warning", "detail": "x"}))
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["icon"] == icon and out["text"] == text


@pytest.mark.parametrize("script,cachefile", [
    ("i3status-clawgate", "clawgate.json"),
    ("i3status-mail", "mail.json"),
    ("i3status-alerts", "alerts.json"),
    ("i3status-civitai", "civitai.json"),
])
def test_block_subprocess_corrupt_json_is_invisible(tmp_path, script, cachefile):
    (tmp_path / cachefile).write_text("{ this is not json ")
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"text": "", "state": "Idle"}
