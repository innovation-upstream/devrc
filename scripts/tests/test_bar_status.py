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
media_block = _load("i3status-media", "i3status_media")


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
# poller parse: media (qBittorrent behind the gluetun AirVPN sidecar)
# The pill is qBit VPN status + ↓/↑ speed ONLY; parse_media emits render fields.
# --------------------------------------------------------------------------- #
def test_parse_media_connected_active_shows_speeds():
    # connected + actively transferring -> neutral pill with the CA label + speeds.
    out = poll.parse_media({"connection_status": "connected",
                            "dl_info_speed": 151903, "up_info_speed": 412349})
    assert out["state"] == "Idle"                 # connected == neutral (decision 5)
    assert out["icon"] == "net_down"              # differs from Mullvad's net_vpn
    assert out["text"].startswith("CA ")
    assert "↓" in out["text"] and "↑" in out["text"]
    assert "148K" in out["text"] and "402K" in out["text"]


def test_parse_media_connected_idle_is_hidden():
    # connected but no transfer -> hidden (empty, invisible block).
    out = poll.parse_media({"connection_status": "connected",
                            "dl_info_speed": 0, "up_info_speed": 0})
    assert out == {"text": "", "state": "Idle"}


def test_parse_media_firewalled_is_red():
    # firewalled = API reachable but forwarded port not open (the AirVPN-fixed
    # regression) -> RED, always shown.
    out = poll.parse_media({"connection_status": "firewalled",
                            "dl_info_speed": 0, "up_info_speed": 0})
    assert out["state"] == "Critical"
    assert out["icon"] == "net_down"
    assert "firewalled" in out["text"] and out["text"].startswith("CA")


def test_parse_media_unknown_status_is_soft_warning():
    out = poll.parse_media({"connection_status": "connecting",
                            "dl_info_speed": 0, "up_info_speed": 0})
    assert out["state"] == "Warning" and "CA" in out["text"]


def test_parse_media_country_label_is_configurable():
    out = poll.parse_media({"connection_status": "firewalled"}, country="US")
    assert out["text"].startswith("US")


def test_parse_media_malformed_toplevel_raises():
    # non-dict payload -> raise (caller -> stale marker -> soft-yellow qBit?).
    with pytest.raises(ValueError):
        poll.parse_media([1, 2, 3])


def test_media_uses_distinct_signal():
    assert poll.SIGNALS["media"] == 16
    assert 16 not in {poll.SIGNALS["clawgate"], poll.SIGNALS["mail"],
                      poll.SIGNALS["alerts"], poll.SIGNALS["civitai"]}


def test_fetch_media_stale_when_creds_missing(monkeypatch):
    # Missing creds file -> source() turns the read error into a stale marker
    # (never crashes, never spawns a request with junk creds).
    monkeypatch.setenv("MEDIA_ENV", "/no/such/media.env")
    out = poll.source("media", poll.fetch_media)
    assert out["state"] == "stale" and out["count"] == 0
    assert out["source"] == "media"


# --------------------------------------------------------------------------- #
# media block render: alarms (not hides) on stale/firewalled per decision 5
# --------------------------------------------------------------------------- #
def test_media_block_passes_through_connected_active():
    payload = poll.parse_media({"connection_status": "connected",
                                "dl_info_speed": 151903, "up_info_speed": 412349})
    out = media_block.render(payload)
    assert out["icon"] == "net_down" and out["state"] == "Idle"
    assert out["text"] == payload["text"]


def test_media_block_hides_when_connected_idle():
    payload = poll.parse_media({"connection_status": "connected",
                                "dl_info_speed": 0, "up_info_speed": 0})
    assert media_block.render(payload) == {"text": "", "state": "Idle"}


def test_media_block_firewalled_is_red():
    payload = poll.parse_media({"connection_status": "firewalled"})
    out = media_block.render(payload)
    assert out["state"] == "Critical" and "firewalled" in out["text"]


def test_media_block_stale_is_soft_yellow():
    # poller-stale marker -> soft yellow `qBit?` (NOT red, NOT hidden).
    out = media_block.render({"count": 0, "state": "stale", "detail": "x"})
    assert out == {"icon": "net_down", "text": "qBit?",
                   "short_text": "qBit?", "state": "Warning"}


def test_media_block_error_marker_is_soft_yellow():
    out = media_block.render({"state": "Idle", "text": "CA ↓1K ↑1K",
                              "error": "boom"})
    assert out["state"] == "Warning" and out["text"] == "qBit?"


def test_media_block_missing_or_malformed_is_soft_yellow():
    # decision 5: stale/missing -> soft yellow (this block ALARMS, unlike the
    # hide-at-zero count blocks which go invisible on a missing cache).
    for bad in (None, [], "x", 3):
        out = media_block.render(bad)
        assert out == {"icon": "net_down", "text": "qBit?",
                       "short_text": "qBit?", "state": "Warning"}


def test_media_block_subprocess_missing_file_is_soft_yellow(tmp_path):
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / "i3status-media")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert json.loads(r.stdout)["text"] == "qBit?"


def test_mock_run_writes_media(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda name: None)
    info_f = tmp_path / "info.json"
    info_f.write_text(json.dumps({"connection_status": "connected",
                                  "dl_info_speed": 151903,
                                  "up_info_speed": 412349}))
    rc = poll.main(["--mock-media", str(info_f)])
    assert rc == 0
    media = json.loads((tmp_path / "media.json").read_text())
    assert media["state"] == "Idle" and media["source"] == "media"
    assert "↓" in media["text"]


def test_mock_run_malformed_media_writes_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda name: None)
    bad = tmp_path / "bad.json"
    bad.write_text('[1,2,3]')
    rc = poll.main(["--mock-media", str(bad)])
    assert rc == 0
    media = json.loads((tmp_path / "media.json").read_text())
    assert media["state"] == "stale" and media["count"] == 0


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
# icon = the i3status-rust named icon for count-only blocks, or None for the
# alert blocks (which carry a literal nf-md-alert glyph in the text instead).
BLOCKS = [
    ("clawgate", clawgate_block, "tasks", "Warning"),
    ("mail", mail_block, "mail", "Warning"),
    ("alerts", alerts_block, None, "Critical"),
    ("civitai", civitai_block, None, "Critical"),
]


def _expected_text(mod, count):
    # The alert blocks (i3status-alerts / -civitai) prepend a literal nf-md-alert
    # GLYPH to the text; i3status-civitai additionally prefixes the `civ` LABEL.
    # The count-only blocks (clawgate/mail) carry neither.
    parts = []
    glyph = getattr(mod, "ALERT_GLYPH", None)
    if glyph:
        parts.append(glyph)
    label = getattr(mod, "LABEL", None)
    if label:
        parts.append(label)
    parts.append(str(count))
    return " ".join(parts)


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_hides_at_zero(name, mod, icon, default_state):
    out = mod.render({"count": 0, "state": "Idle"})
    assert out == {"text": "", "state": "Idle"}
    assert "icon" not in out            # truly invisible: no icon either


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_visible_and_coloured_when_positive(name, mod, icon, default_state):
    out = mod.render({"count": 3, "state": default_state})
    exp = _expected_text(mod, 3)
    if getattr(mod, "ALERT_GLYPH", None):
        # alert blocks carry the glyph in the text, NOT the i3status-rust `icon`
        assert "icon" not in out
        assert mod.ALERT_GLYPH in out["text"]
    else:
        assert out["icon"] == icon
    assert out["text"] == exp and out["short_text"] == exp
    assert out["state"] == default_state


def test_civitai_block_labels_count_distinctly():
    # The civitai block must be visually distinguishable from homelab alerts:
    # its text carries the `civ` label prefix, alerts' does not.
    civ = civitai_block.render({"count": 317, "state": "Critical"})
    hl = alerts_block.render({"count": 317, "state": "Critical"})
    assert civ["text"] == "%s civ 317" % civitai_block.ALERT_GLYPH
    assert civ["state"] == "Critical"
    assert hl["text"] == "%s 317" % alerts_block.ALERT_GLYPH
    assert "civ" not in hl["text"]
    assert civ["text"] != hl["text"]


# red_above threshold — neutral at/below the standing backlog, red only above it.
ALERT_BLOCKS = [("alerts", alerts_block), ("civitai", civitai_block)]


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_neutral_at_or_below_baseline(name, mod):
    for count in (25, 30):  # <= red_above=30
        out = mod.render({"count": count, "state": "Critical"}, red_above=30)
        assert out["state"] == "Idle"          # visible but NOT coloured
        assert mod.ALERT_GLYPH in out["text"]  # still shown (not hidden)
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


# icon=None => alert blocks (glyph in text, no `icon` field); the given `text`
# is the trailing (glyph-stripped) portion the rendered text must end with.
@pytest.mark.parametrize("script,cachefile,icon,text", [
    ("i3status-clawgate", "clawgate.json", "tasks", "2"),
    ("i3status-mail", "mail.json", "mail", "2"),
    ("i3status-alerts", "alerts.json", None, "2"),
    ("i3status-civitai", "civitai.json", None, "civ 2"),
])
def test_block_subprocess_positive_renders(tmp_path, script, cachefile, icon, text):
    (tmp_path / cachefile).write_text(json.dumps(
        {"count": 2, "state": "Warning", "detail": "x"}))
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    if icon is None:
        assert "icon" not in out
        assert out["text"].endswith(text)
    else:
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


# --------------------------------------------------------------------------- #
# poller: edge_decision — the PURE rising-edge latch (given prev latch + count +
# threshold -> (should_toast, new_latch)). Offline, deterministic, no side effects.
# --------------------------------------------------------------------------- #
def test_edge_decision_rising_edge_fires_once():
    # not latched, count crosses above threshold -> fire + latch.
    assert poll.edge_decision(False, 31, 30) == (True, True)


def test_edge_decision_steady_state_does_not_retoast():
    # already latched + still above -> NO re-toast, stays latched.
    assert poll.edge_decision(True, 40, 30) == (False, True)
    assert poll.edge_decision(True, 31, 30) == (False, True)


def test_edge_decision_at_or_below_threshold_resets_latch():
    # count == threshold is NOT "above" -> latch clears (so next crossing fires).
    assert poll.edge_decision(True, 30, 30) == (False, False)
    assert poll.edge_decision(True, 5, 30) == (False, False)
    assert poll.edge_decision(False, 30, 30) == (False, False)


def test_edge_decision_re_fires_after_drop_and_recross():
    # full cycle: fire -> steady -> drop (reset) -> re-cross fires again.
    should1, latch1 = poll.edge_decision(False, 31, 30)   # rising edge
    assert (should1, latch1) == (True, True)
    should2, latch2 = poll.edge_decision(latch1, 35, 30)  # steady above
    assert (should2, latch2) == (False, True)
    should3, latch3 = poll.edge_decision(latch2, 10, 30)  # dropped -> reset
    assert (should3, latch3) == (False, False)
    should4, latch4 = poll.edge_decision(latch3, 31, 30)  # re-cross -> fires
    assert (should4, latch4) == (True, True)


def test_edge_decision_threshold_zero_is_zero_to_positive():
    # clawgate/mail rule: threshold 0 -> any count>0 is the rising edge.
    assert poll.edge_decision(False, 1, 0) == (True, True)     # 0 -> 1 fires
    assert poll.edge_decision(True, 3, 0) == (False, True)     # still >0, quiet
    assert poll.edge_decision(True, 0, 0) == (False, False)    # back to 0, reset
    assert poll.edge_decision(False, 0, 0) == (False, False)   # stays at 0


# --------------------------------------------------------------------------- #
# poller: latch persistence across invocations (sidecar file)
# --------------------------------------------------------------------------- #
def test_latch_defaults_false_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    assert poll.read_latch("alerts") is False


def test_latch_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    poll.write_latch("alerts", True)
    assert poll.read_latch("alerts") is True
    poll.write_latch("alerts", False)
    assert poll.read_latch("alerts") is False


def test_latch_corrupt_file_reads_false(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    (tmp_path / "alerts.toast-state").write_text("{ not json")
    assert poll.read_latch("alerts") is False


# --------------------------------------------------------------------------- #
# poller: evaluate_edge_toast — the decision + latch + fire orchestration.
# fire/read/write are injected so this stays fully OFFLINE (no dunstify, no disk).
# --------------------------------------------------------------------------- #
def _latch_store(initial=False):
    """An in-memory latch backend + a fire-recorder for evaluate_edge_toast."""
    state = {"latched": initial}
    fired = []
    return (state, fired,
            lambda name: state["latched"],
            lambda name, v: state.__setitem__("latched", v),
            lambda *a, **k: fired.append((a, k)) or True)


ALERTS_SPEC = {"threshold": 30, "urgency": "normal", "summary": "s",
               "action": "xdg-open http://x"}
MAIL_SPEC = {"threshold": 0, "urgency": "low", "summary": "m", "action": None}


def test_eval_fires_once_on_rising_edge():
    state, fired, rd, wr, fr = _latch_store(initial=False)
    out = poll.evaluate_edge_toast(
        "alerts", {"count": 31, "state": "Critical", "detail": "31 firing"},
        ALERTS_SPEC, fire=fr, read=rd, write=wr)
    assert out == (True, True)
    assert state["latched"] is True
    assert len(fired) == 1
    # body carries the source detail; action is the block's target.
    args, kw = fired[0]
    assert "31 firing" in args[2]
    assert kw["action_cmd"] == "xdg-open http://x"


def test_eval_steady_state_does_not_refire():
    state, fired, rd, wr, fr = _latch_store(initial=True)
    out = poll.evaluate_edge_toast(
        "alerts", {"count": 45, "state": "Critical"},
        ALERTS_SPEC, fire=fr, read=rd, write=wr)
    assert out == (False, True)
    assert fired == []                       # no toast on steady state


def test_eval_drop_resets_latch_no_fire():
    state, fired, rd, wr, fr = _latch_store(initial=True)
    out = poll.evaluate_edge_toast(
        "alerts", {"count": 5, "state": "Warning"},
        ALERTS_SPEC, fire=fr, read=rd, write=wr)
    assert out == (False, False)
    assert state["latched"] is False
    assert fired == []


def test_eval_zero_to_positive_fires_for_mail_and_clawgate():
    state, fired, rd, wr, fr = _latch_store(initial=False)
    out = poll.evaluate_edge_toast(
        "mail", {"count": 2, "state": "Warning", "detail": "2 open"},
        MAIL_SPEC, fire=fr, read=rd, write=wr)
    assert out == (True, True) and len(fired) == 1
    assert fired[0][1]["action_cmd"] is None        # mail toast has no action


def test_eval_skips_stale_and_error_payloads_without_touching_latch():
    for payload in ({"count": 99, "state": "stale"},
                    {"count": 99, "state": "Critical", "error": "boom"}):
        state, fired, rd, wr, fr = _latch_store(initial=False)
        out = poll.evaluate_edge_toast("alerts", payload, ALERTS_SPEC,
                                       fire=fr, read=rd, write=wr)
        assert out is None
        assert fired == [] and state["latched"] is False


def test_eval_skips_malformed_payloads():
    for bad in (None, [], "x", 3, {"count": "NaN"}):
        state, fired, rd, wr, fr = _latch_store(initial=False)
        out = poll.evaluate_edge_toast("alerts", bad, ALERTS_SPEC,
                                       fire=fr, read=rd, write=wr)
        assert out is None and fired == []


def test_eval_is_failsafe_when_fire_raises():
    # A dunstify/notify failure must NOT crash the decision path: evaluate_edge_
    # toast still latches, and the exception is swallowed by the caller's wrapper.
    def boom(*a, **k):
        raise RuntimeError("no display")
    state, fired, rd, wr, _ = _latch_store(initial=False)
    with pytest.raises(RuntimeError):
        # evaluate itself does not swallow (the live main() wraps it) — but the
        # latch is written BEFORE the fire, so state is consistent even on failure.
        poll.evaluate_edge_toast("alerts", {"count": 31, "state": "Critical"},
                                 ALERTS_SPEC, fire=boom, read=rd, write=wr)
    assert state["latched"] is True             # latch persisted before the fire


# --------------------------------------------------------------------------- #
# poller: fire_toast is fully fail-safe (offline) — a broken toast never raises
# --------------------------------------------------------------------------- #
def test_fire_toast_skips_without_session_bus(monkeypatch):
    # No session bus reachable (headless / laptop) -> skip, return False, no raise.
    monkeypatch.setattr(poll, "_borrow_desktop_env", lambda env: {})
    assert poll.fire_toast("normal", "s", "b", action_cmd="xdg-open x") is False


def test_fire_toast_swallows_launcher_failure(monkeypatch):
    monkeypatch.setattr(poll, "_borrow_desktop_env",
                        lambda env: {"DBUS_SESSION_BUS_ADDRESS": "unix:x"})

    def boom(argv):
        raise OSError("systemd-run missing")
    assert poll.fire_toast("normal", "s", "b", runner=boom) is False


def test_fire_toast_dispatches_with_action(monkeypatch):
    monkeypatch.setattr(poll, "_borrow_desktop_env",
                        lambda env: {"DBUS_SESSION_BUS_ADDRESS": "unix:x",
                                     "DISPLAY": ":0"})
    captured = {}
    assert poll.fire_toast("normal", "sum", "body", action_cmd="xdg-open URL",
                           runner=lambda a: captured.update(argv=a)) is True
    argv = captured["argv"]
    assert argv[0] == "systemd-run" and "--user" in argv
    assert "bash" in argv and argv[-1] == "xdg-open URL"       # action is last arg
    joined = " ".join(argv)
    assert "-A open,Open" in joined                            # clickable action
    assert "--setenv=DISPLAY=:0" in argv
