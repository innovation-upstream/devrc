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
telemetry_block = _load("i3status-telemetry", "i3status_telemetry")

# The deadman states the telemetry pill treats as "cannot tell". Pinned here as
# a LITERAL rather than imported from deadman.py: a test expectation derived
# from the implementation it tests proves nothing (RULES.md).
UNKNOWN_STATES = frozenset(
    {"no-data", "unreachable", "query-failed", "not-configured",
     "misconfigured", "presence-stalled"})


@pytest.fixture(autouse=True)
def _never_reach_the_desktop(monkeypatch):
    """🔴 AUTOUSE, whole file: no test may launch a REAL desktop notification.

    This is not hypothetical hygiene. When the `--mock-*` path started
    dispatching toasts, two long-standing tests here that patched `signal_bar`
    but not `fire_toast` began firing real notifications on the graphical host —
    three launches per suite run, one of them a STICKY CRITICAL
    "🔴 Telemetry source DEAD / laptop/keys silent 9h" naming a host and source
    that exist only in a FIXTURE. `fire_toast` borrows DISPLAY/DBUS from i3's
    environ, so a non-graphical runner is no protection, and the nix-sandbox tier
    cannot see it at all (no systemd-run, no session bus) — the gate stayed green
    while the desktop got spammed with a false alarm about the very pill this
    work exists to make trustworthy.

    Patching the LAUNCHER rather than `fire_toast` keeps `fire_toast` itself real
    and testable (see the test_fire_toast_* group, which exercises it directly),
    while making a real launch structurally unreachable from this file.
    """
    launches = []
    monkeypatch.setattr(poll, "_toast_runner", lambda argv: launches.append(argv))
    return launches


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


# --------------------------------------------------------------------------- #
# poller parse + block: telemetry deadman
#
# 🔴 This is the ONE block whose reassuring answer is a ZERO, so its tests are
# written as PAIRS: every "renders empty" assertion has a sibling that makes the
# same code path produce a visible pill. A renderer wired to nothing would pass
# the first half of each pair and fail the second.
# --------------------------------------------------------------------------- #
def _verdict(state="ok", count=0, detail="", **kw):
    v = {"state": state, "count": count, "detail": detail,
         "evaluated": 17, "rows": 11103, "newest_event_age_minutes": 2}
    v.update(kw)
    return v


def test_telemetry_uses_a_free_signal():
    """INVARIANT GUARD: 17 must not collide with any existing block's signal, or
    `pkill -RTMIN+17` would refresh the wrong pill."""
    assert poll.SIGNALS["telemetry"] == 17
    others = [v for k, v in poll.SIGNALS.items() if k != "telemetry"]
    assert 17 not in others
    assert len(set(poll.SIGNALS.values())) == len(poll.SIGNALS)


def test_parse_telemetry_clean_is_idle_and_invisible():
    out = poll.parse_telemetry(_verdict(count=0, detail="17 source(s) fresh"),
                               now=1000)
    assert out["count"] == 0 and out["state"] == "Idle"
    assert out["unknown"] is False
    assert telemetry_block.render(out) == {"text": "", "state": "Idle"}


def test_parse_telemetry_dead_source_is_critical_and_visible():
    """POSITIVE CONTROL for the pair above — same path, non-zero count."""
    out = poll.parse_telemetry(
        _verdict(count=2, detail="workbench/opencode silent 19.0h active"),
        now=1000)
    assert out["count"] == 2 and out["state"] == "Critical"
    blk = telemetry_block.render(out)
    assert blk["text"] == "tlm 2"
    assert blk["state"] == "Critical"


def test_a_MEASURED_death_outranks_an_unknown_state_at_the_pill():
    """🔴 REGRESSION TEST. `presence-stalled` is the one unknown state that can
    carry real dead pairs: the host's human timeline is frozen so its REMAINING
    sources are unjudgeable, but a source already past its budget when the freeze
    began was measured against real active buckets and is genuinely dead.

    Folding that into `tlm ?` with the count zeroed turned an actionable, named
    `tlm 1` into an unactionable `tlm ?` — the check got QUIETER in exactly the
    outage the stall detector was written to catch.
    """
    v = _verdict(state="presence-stalled", count=1,
                 detail="h1/claude silent 54.6h active (budget 2.0h) — AND "
                        "CANNOT TELL: presence stalled (h1: ...)")
    out = poll.parse_telemetry(v, now=1000, unknown_states=UNKNOWN_STATES)
    assert out["count"] == 1, out
    assert out["state"] == "Critical", out
    assert out["unknown"] is False, out
    # 🔴 Suppression is its OWN flag. `unknown_since` is the grace clock and
    # STAYS SET here (see the clock test below); keying suppression off it is
    # what forced the first fix to clear the clock.
    assert out["suppress_toast"] is False, out
    assert out["unknown_since"] == 1000, out
    assert "h1/claude" in out["detail"] and "UNKNOWN" in out["detail"], out
    blk = telemetry_block.render(out)
    assert (blk["text"], blk["state"]) == ("tlm 1", "Critical"), blk

    # NEGATIVE CONTROL, same state, nothing measured dead -> the `tlm ?` path is
    # untouched. Without this the branch above could be swallowing every stall.
    quiet = poll.parse_telemetry(_verdict(state="presence-stalled", count=0,
                                          detail="nothing measurable"),
                                 now=1000, unknown_states=UNKNOWN_STATES)
    assert quiet["count"] == 0 and quiet["unknown_since"] == 1000, quiet
    assert quiet["state"] == "Warning", quiet
    assert quiet["suppress_toast"] is True, quiet


def test_a_count_excursion_does_NOT_restart_the_cannot_tell_grace_clock():
    """🔴 REGRESSION TEST. `unknown_since` was overloaded as BOTH the grace clock
    and the toast-suppression flag, and the first version of the count>0 branch
    resolved that conflict by clearing it — silently RESETTING the clock and
    re-creating an invisible-green window on a host that had been continuously
    unjudgeable.

    Measured on one continuous `presence-stalled` episode: with count always 0
    the `tlm ?` pill appears at t=1800; a single poll carrying count=1 at t=900
    pushed it out to t=3000, and with the count flapping 1/0/1/0 it was never
    reached at all. Reachable, not hypothetical — a dead pair's baseline erodes
    out of the 14-day window mid-stall, so the count really does fall 1 -> 0.
    """
    kw = dict(grace=1800, unknown_states=UNKNOWN_STATES)
    stalled_0 = _verdict(state="presence-stalled", count=0, detail="x")
    stalled_1 = _verdict(state="presence-stalled", count=1, detail="h1/claude")

    # BASELINE: an uninterrupted episode goes visible exactly at the grace point.
    p = poll.parse_telemetry(stalled_0, now=0, **kw)
    assert p["unknown"] is False
    assert poll.parse_telemetry(stalled_0, prev=p, now=1800, **kw)["unknown"] is True

    # THE EXCURSION: one poll at t=900 carries a measured death, then the count
    # falls back. The clock must still have started at t=0.
    p0 = poll.parse_telemetry(stalled_0, now=0, **kw)
    p900 = poll.parse_telemetry(stalled_1, prev=p0, now=900, **kw)
    assert p900["unknown_since"] == 0, p900
    p1800 = poll.parse_telemetry(stalled_0, prev=p900, now=1800, **kw)
    assert p1800["unknown_since"] == 0, p1800
    assert p1800["unknown"] is True, \
        "a count excursion pushed the `tlm ?` pill past its grace point"
    assert telemetry_block.render(p1800)["text"] == "tlm ?"

    # FLAPPING 1/0/1/0 across the whole grace window must still arrive visible.
    prev = poll.parse_telemetry(stalled_0, now=0, **kw)
    for t, verdict in ((450, stalled_1), (900, stalled_0), (1350, stalled_1)):
        prev = poll.parse_telemetry(verdict, prev=prev, now=t, **kw)
    final = poll.parse_telemetry(stalled_0, prev=prev, now=1800, **kw)
    assert final["unknown_since"] == 0 and final["unknown"] is True, final


def test_a_junk_count_DEGRADES_it_does_not_raise():
    """🔴 Raising here is not the safe option: `source()` turns the exception into
    a `stale` payload, the block renders `stale` as an EMPTY pill, and the
    swallowed exception means the unit's OnFailure toast does not fire either —
    so malformed input would look exactly like "all healthy".

    Measured regression: `count="abc"` and `count={}` on an unknown verdict went
    from a visible `tlm ?` to an invisible block.
    """
    for junk in ("abc", {}, [], None, object()):
        out = poll.parse_telemetry(
            _verdict(state="presence-stalled", count=junk, detail="x"),
            now=1000, unknown_states=UNKNOWN_STATES)
        assert out["count"] == 0, (junk, out)
        assert out["state"] == "Warning", (junk, out)
        assert out["suppress_toast"] is True, (junk, out)
        # ...and the visible pill is still reachable for this episode.
        later = poll.parse_telemetry(
            _verdict(state="presence-stalled", count=junk, detail="x"),
            prev=out, now=1000 + 1800, grace=1800, unknown_states=UNKNOWN_STATES)
        assert telemetry_block.render(later)["text"] == "tlm ?", (junk, later)

    # The clean branch degrades too, rather than raising into a `stale` marker.
    ok_junk = poll.parse_telemetry(_verdict(state="ok", count="abc"), now=1000,
                                   unknown_states=UNKNOWN_STATES)
    assert ok_junk["count"] == 0 and ok_junk["state"] == "Idle", ok_junk

    # 🔴 `int(float("inf"))` raises OverflowError, NOT ValueError, and json.load
    # accepts a bare `Infinity` — so this reaches the parser through a supported
    # entry point and reproduced the exact symptom this test exists for.
    for edge in (float("inf"), float("-inf"), float("nan")):
        out = poll.parse_telemetry(_verdict(state="presence-stalled", count=edge),
                                   now=1000, unknown_states=UNKNOWN_STATES)
        assert out["count"] == 0 and out["state"] == "Warning", (edge, out)

    # A NEGATIVE count is the worst shape: truthy, so the payload said
    # "Critical", while the block renders only count>0 — Critical with an
    # INVISIBLE pill. Clamped, as parse_mail already does.
    neg = poll.parse_telemetry(_verdict(state="ok", count=-5), now=1000,
                               unknown_states=UNKNOWN_STATES)
    assert neg["count"] == 0 and neg["state"] == "Idle", neg
    assert telemetry_block.render(neg)["text"] == "", neg
    # And `evaluated`/`rows` are on the same footing — they are ints in the
    # payload contract and a junk verdict must not crash the poll.
    weird = poll.parse_telemetry({"state": "ok", "count": 0, "evaluated": "x",
                                  "rows": None}, now=1000,
                                 unknown_states=UNKNOWN_STATES)
    assert weird["evaluated"] == 0 and weird["rows"] == 0, weird


@pytest.mark.parametrize("state,count,gate_runs,want_toast", [
    ("ok", 1, True, True),                  # the ordinary dead-source toast
    ("ok", 0, True, False),                 # gate runs, decides not to fire
    ("presence-stalled", 1, True, True),    # a MEASURED death, however uncertain
    ("presence-stalled", 0, False, False),  # SUPPRESSED: must not move the latch
    ("unreachable", 0, False, False),       # SUPPRESSED
])
def test_the_toast_SUPPRESSION_SEAM_decides_correctly(state, count, gate_runs,
                                                      want_toast):
    """🔴 THE SEAM ITSELF, not a helper called around it.

    This decision used to be three inline lines in `main()` that NO test reached:
    `--mock-*` returned before the toast loop and the only test called
    `evaluate_edge_toast` DIRECTLY, bypassing the gate. Three mutants survived
    the whole suite there — skip unconditionally, never skip, key the skip on the
    wrong field — and the first silenced the telemetry toast in EVERY scenario,
    including a plain `ok` with dead sources, with the suite green.

    So the rule now lives in `toast_suppressed`/`dispatch_edge_toast` and is
    exercised through `dispatch_edge_toast` for every quadrant.

    🔴 TWO distinct observations, because they are not the same fact and a single
    one cannot tell the mutants apart: whether the GATE was reached (that is the
    suppression decision) and whether a TOAST fired (that is the gate's own
    rising-edge rule). `ok`+count=0 reaches the gate and fires nothing — asserting
    only "no toast" there would make "skip unconditionally" look correct.
    """
    payload = poll.parse_telemetry(
        _verdict(state=state, count=count, detail="h1/claude silent"),
        now=1000, unknown_states=UNKNOWN_STATES)
    reached, fired = [], []

    def gate(n, p, s):
        reached.append(n)
        return poll.evaluate_edge_toast(
            n, p, s, fire=lambda *a, **kw: fired.append(a) or True,
            read=lambda _n: False, write=lambda _n, _l: None)

    poll.dispatch_edge_toast("telemetry", payload, poll._toast_specs(),
                             evaluate=gate)
    assert bool(reached) is gate_runs, (payload, reached)
    assert bool(fired) is want_toast, (payload, fired)


def test_a_non_telemetry_source_is_never_suppressed():
    """The skip is telemetry-specific. A mutant dropping the name check would
    silence clawgate/mail/alerts too — and their payloads have no
    `suppress_toast` key at all, so the bug would be invisible in this file
    unless a non-telemetry source is exercised through the same seam."""
    assert poll.toast_suppressed("clawgate", {"suppress_toast": True}) is False
    seen = []
    poll.dispatch_edge_toast("clawgate", {"count": 3}, poll._toast_specs(),
                             evaluate=lambda *a: seen.append(a) or (True, True))
    assert len(seen) == 1, seen
    # media deliberately has NO spec (its alarm is in-pill) -> nothing dispatched.
    assert poll.dispatch_edge_toast("media", {"count": 9}, poll._toast_specs(),
                                    evaluate=lambda *a: 1 / 0) is None


def test_the_toast_FIRES_end_to_end_through_the_REAL_cli_path(tmp_path,
                                                              monkeypatch):
    """🔴 END-TO-END through `main()`, because every assertion above still stops
    at a function boundary. `--mock-telemetry` used to `return 0` BEFORE the
    toast step, so no test could observe the dispatch the live poll performs;
    the mock path now runs the same dispatch.

    Verdict carries a measured death on an unknown state — the exact payload the
    regression was about — and the toast must actually fire.

    🔴 `DEVRC_DIR` is pinned at THIS checkout. It defaults to `~/workspace/devrc`,
    so without this the CLI path resolves `UNKNOWN_STATES` from the BASE CLONE's
    deadman — a different commit — and `presence-stalled` silently is not an
    unknown state at all, which sends the whole test down the plain-`ok` branch
    and makes it pass for the wrong reason. It did exactly that when first
    written. The assertion below fails loudly if the resolution ever slips again.
    """
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "DEVRC_DIR", str(SCRIPTS.parent))
    assert "presence-stalled" in poll._deadman_unknown_states(), \
        "the CLI path is resolving UNKNOWN_STATES from the wrong deadman"
    monkeypatch.setattr(poll, "signal_bar", lambda _n: None)
    fired = []
    monkeypatch.setattr(poll, "fire_toast",
                        lambda *a, **kw: fired.append(a) or True)
    vf = tmp_path / "verdict.json"
    vf.write_text(json.dumps({"state": "presence-stalled", "count": 1,
                              "evaluated": 9, "rows": 100,
                              "newest_event_age_minutes": 2,
                              "detail": "h1/claude silent 54.6h active"}))

    assert poll.main(["--mock-telemetry", str(vf), "--toast"]) == 0
    assert len(fired) == 1, fired
    assert "h1/claude" in fired[0][2], fired[0]
    payload = json.loads((tmp_path / "telemetry.json").read_text())
    assert (payload["count"], payload["state"]) == (1, "Critical"), payload

    # NEGATIVE CONTROL through the SAME path: an unknown verdict with nothing
    # measured dead must NOT toast, and must not clear the latch either.
    fired.clear()
    vf.write_text(json.dumps({"state": "presence-stalled", "count": 0,
                              "detail": "nothing measurable"}))
    assert poll.main(["--mock-telemetry", str(vf), "--toast"]) == 0
    assert fired == [], fired
    assert poll.read_latch("telemetry") is True, \
        "a blackout zero cleared the rising-edge latch"


def test_a_mock_run_does_NOT_toast_or_move_the_live_latch(tmp_path, monkeypatch,
                                                          _never_reach_the_desktop):
    """🔴 REGRESSION TEST. The documented debug recipe must be inert.

    `cache_dir()` falls back to ~/.cache/bar-status when BAR_STATUS_DIR is unset,
    and the documented command (`bar-status-poll --mock-alerts a.json
    --mock-mail 3`) does not set it — so once the mock path started dispatching,
    a sub-threshold fixture CLEARED a live rising-edge latch and the next real
    poll (<=45s later) re-toasted a steady-state alert condition. "A blackout
    must never move the latch", reintroduced through the debug path.

    So dispatch is opt-in via `--toast`. Here the latch starts LATCHED and a mock
    run with a sub-threshold fixture must leave it exactly as found.
    """
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda _n: None)
    poll.write_latch("alerts", True)

    af = tmp_path / "alerts.json"
    af.write_text(json.dumps([]))          # zero firing alerts: sub-threshold
    assert poll.main(["--mock-alerts", str(af), "--mock-mail", "3"]) == 0

    assert poll.read_latch("alerts") is True, \
        "a --mock-* run cleared a LIVE rising-edge latch"
    assert _never_reach_the_desktop == [], _never_reach_the_desktop
    # ...and it still did its actual job: the cache file is written.
    assert json.loads((tmp_path / "alerts.json").read_text())["count"] == 0

    # OPT-IN control: with --toast the same run DOES dispatch, so the seam stays
    # reachable from a test rather than being switched off for everyone.
    fired = []
    monkeypatch.setattr(poll, "fire_toast", lambda *a, **kw: fired.append(a) or True)
    poll.write_latch("alerts", False)      # clear, so a crossing is possible
    mf = tmp_path / "many.json"
    mf.write_text(json.dumps(
        [_alert("KubeJobFailed%d" % i, "critical") for i in range(40)]))
    assert poll.main(["--mock-alerts", str(mf), "--toast"]) == 0
    assert len(fired) == 1, fired
    assert poll.read_latch("alerts") is True, "the opt-in run did not latch"


def test_fire_toast_ROUTES_THROUGH_the_patchable_launcher(monkeypatch,
                                                          _never_reach_the_desktop):
    """🔴 THE POSITIVE CONTROL FOR THE AUTOUSE FIXTURE ITSELF.

    Every "no real toast" assertion in this file rests on the fixture patching
    `poll._toast_runner`. That is worth exactly nothing unless `fire_toast`
    actually CALLS it — and a mutant re-inlining the old `subprocess.run` lambda
    left the whole suite green while silently restoring the ability to spam the
    desktop: the fixture still patched an attribute that existed, it was just no
    longer on the path. A patched seam nobody routes through is a harness wired
    to nothing.

    So this drives `fire_toast` for real (with the session-bus check satisfied,
    or it short-circuits before the launcher and the zero means nothing) and
    asserts the recorder SAW the launch.
    """
    monkeypatch.setattr(poll, "_borrow_desktop_env",
                        lambda env: {**env,
                                     "DBUS_SESSION_BUS_ADDRESS": "unix:path=/dev/null",
                                     "DISPLAY": ":0"})
    assert poll.fire_toast("critical", "sum", "body") is True
    assert len(_never_reach_the_desktop) == 1, \
        ("fire_toast did not go through poll._toast_runner — the whole file's "
         "no-real-toast guarantee is void: %r" % _never_reach_the_desktop)
    argv = _never_reach_the_desktop[0]
    assert argv[0] == "systemd-run", argv
    assert "sum" in argv and "body" in argv, argv


def test_dispatch_edge_toast_NEVER_raises(_never_reach_the_desktop):
    """🔴 `dispatch_edge_toast` is now the SOLE fail-safe boundary for both the
    live poll and the mock path, and its "never raises" was unasserted — deleting
    the `contextlib.suppress` left the whole suite green.

    It is load-bearing: `evaluate_edge_toast` genuinely does raise (a fire that
    throws propagates; see test_eval_is_failsafe_when_fire_raises) and the caller
    is what swallows it. A poller that dies here loses the remaining sources.
    """
    def boom(_n, _p, _s):
        raise RuntimeError("dunstify exploded")

    assert poll.dispatch_edge_toast("clawgate", {"count": 3},
                                    poll._toast_specs(), evaluate=boom) is None

    # ...and the LIVE loop keeps going: a raising source must not stop the ones
    # after it from being polled and dispatched.
    seen = []
    assert poll._dispatch_all([("clawgate", {"count": 3}),
                              ("mail", {"count": 2})]) == 0
    poll._dispatch_all([])   # empty is a no-op, not an error
    assert seen == []


def test_the_SOURCES_table_is_a_LEDGER_of_every_polled_source():
    """🔴 A ledger, pinned as a LITERAL, failing when the set GROWS or SHRINKS.

    Hoisting the source table out of `main()` made it testable and also made it
    deletable: a mutant dropping `telemetry` from it survived the whole suite,
    and its effect is that the poller silently stops polling telemetry — the pill
    freezes on its last cache file forever, which is indistinguishable from
    "nothing has changed".

    The relationships are what make this more than a spelling check: every polled
    source needs a bar SIGNAL to refresh its block, and every toast spec needs a
    source to produce the payload it gates on.
    """
    names = [n for n, _fn in poll.SOURCES]
    assert names == ["clawgate", "mail", "alerts", "civitai", "media", "airvpn",
                     "telemetry"], names
    assert len(names) == len(set(names)), "a source is polled twice: %s" % names
    for _n, fn in poll.SOURCES:
        assert callable(fn)
    # Every polled source has a refresh signal, and every signal has a poller.
    assert set(names) == set(poll.SIGNALS), \
        sorted(set(names) ^ set(poll.SIGNALS))
    # Every toast spec belongs to something actually polled.
    assert set(poll._toast_specs()) <= set(names), \
        sorted(set(poll._toast_specs()) - set(names))


def test_the_LIVE_poll_path_dispatches_toasts_too(tmp_path, monkeypatch):
    """🔴 The live loop is what runs on the workbench every 45s, and it was the
    one dispatch site no test reached: a mutant deleting its toast dispatch
    SURVIVED the full suite while the mock path's identical mutant died.

    Both paths now funnel through `_dispatch_all`, and this drives the LIVE one
    with substituted fetchers so the coverage is not merely inherited.
    """
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda _n: None)
    fired = []
    monkeypatch.setattr(poll, "fire_toast",
                        lambda *a, **kw: fired.append(a) or True)
    monkeypatch.setattr(poll, "SOURCES", (
        ("clawgate", lambda: {"count": 2, "state": "Warning",
                              "detail": "2 task(s) awaiting"}),
        ("telemetry", lambda: poll.parse_telemetry(
            {"state": "presence-stalled", "count": 1,
             "detail": "h1/claude silent"},
            now=1000, unknown_states=frozenset(UNKNOWN_STATES))),
    ))

    assert poll.main([]) == 0
    summaries = sorted(a[1] for a in fired)
    assert len(fired) == 2, fired
    assert any("Telemetry" in s for s in summaries), summaries
    assert any("clawgate" in s for s in summaries), summaries

    # NEGATIVE CONTROL on the same path: a suppressed telemetry payload toasts
    # nothing, while the unrelated source still does.
    fired.clear()
    monkeypatch.setattr(poll, "SOURCES", (
        ("telemetry", lambda: poll.parse_telemetry(
            {"state": "unreachable", "count": 0, "detail": "down"},
            now=1000, unknown_states=frozenset(UNKNOWN_STATES))),
    ))
    assert poll.main([]) == 0
    assert fired == [], fired


@pytest.mark.parametrize("state", sorted(UNKNOWN_STATES))
def test_parse_telemetry_unknown_is_not_reported_as_healthy(state):
    """Each non-evaluable state must be carried through as UNKNOWN, never
    collapsed into the clean/Idle branch."""
    out = poll.parse_telemetry(_verdict(state=state, detail="nope"), now=1000,
                               unknown_states=UNKNOWN_STATES)
    assert out["telemetry_state"] == state
    assert out["unknown_since"] == 1000
    assert "UNKNOWN" in out["detail"]


def test_parse_telemetry_unknown_is_grace_gated_then_visible():
    """The pair that proves the grace timer is REACHABLE, not just present."""
    first = poll.parse_telemetry(_verdict(state="unreachable"), now=1000,
                                 grace=1800, unknown_states=UNKNOWN_STATES)
    # A single failed poll must NOT flicker the bar.
    assert first["unknown"] is False
    assert telemetry_block.render(first) == {"text": "", "state": "Idle"}
    # ...but a persistent one must become visible, carrying `unknown_since`
    # forward across the oneshot poller's restarts.
    later = poll.parse_telemetry(_verdict(state="unreachable"), prev=first,
                                 now=1000 + 1800, grace=1800,
                                 unknown_states=UNKNOWN_STATES)
    assert later["unknown_since"] == 1000
    assert later["unknown"] is True
    assert telemetry_block.render(later) == {
        "text": "tlm ?", "short_text": "tlm ?", "state": "Warning"}


def test_parse_telemetry_unknown_clock_resets_after_recovery():
    bad = poll.parse_telemetry(_verdict(state="unreachable"), now=1000,
                               unknown_states=UNKNOWN_STATES)
    good = poll.parse_telemetry(_verdict(count=0), prev=bad, now=2000,
                                unknown_states=UNKNOWN_STATES)
    assert good["unknown_since"] is None
    bad2 = poll.parse_telemetry(_verdict(state="unreachable"), prev=good,
                                now=3000, unknown_states=UNKNOWN_STATES)
    assert bad2["unknown_since"] == 3000      # fresh clock, not the old one


def test_parse_telemetry_junk_verdict_is_unknown_not_healthy():
    """A garbage verdict must NOT read as 'all fresh'."""
    for junk in (None, "x", 3, []):
        out = poll.parse_telemetry(junk, now=1000, unknown_states=UNKNOWN_STATES)
        assert out["telemetry_state"] == "query-failed"
        assert out["unknown_since"] == 1000


def test_telemetry_block_hides_on_missing_and_poller_stale():
    assert telemetry_block.render(None) == {"text": "", "state": "Idle"}
    assert telemetry_block.render({"state": "stale", "count": 0}) == {
        "text": "", "state": "Idle"}
    assert telemetry_block.render({"error": "boom", "count": 5}) == {
        "text": "", "state": "Idle"}
    assert telemetry_block.render({"count": "NaN"}) == {"text": "", "state": "Idle"}


def test_telemetry_block_main_emits_one_json_line(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    (tmp_path / "telemetry.json").write_text(json.dumps(
        {"count": 3, "state": "Critical", "unknown": False}))
    out = subprocess.run([sys.executable, str(SCRIPTS / "i3status-telemetry")],
                         capture_output=True, text=True,
                         env={**os.environ, "BAR_STATUS_DIR": str(tmp_path)})
    assert out.returncode == 0
    blk = json.loads(out.stdout.strip())
    assert blk["text"] == "tlm 3" and blk["state"] == "Critical"


def test_mock_run_writes_telemetry(tmp_path, monkeypatch):
    """The whole write+signal path, driven from a fixture verdict."""
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda name: None)
    f = tmp_path / "verdict.json"
    f.write_text(json.dumps(_verdict(count=1, detail="laptop/keys silent 9h")))
    assert poll.main(["--mock-telemetry", str(f)]) == 0
    written = json.loads((tmp_path / "telemetry.json").read_text())
    assert written["count"] == 1
    assert written["source"] == "telemetry"
    assert telemetry_block.render(written)["text"] == "tlm 1"


def test_mock_run_telemetry_clean_writes_zero(tmp_path, monkeypatch):
    """The zero half of the pair, through the same write path."""
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda name: None)
    f = tmp_path / "verdict.json"
    f.write_text(json.dumps(_verdict(count=0)))
    assert poll.main(["--mock-telemetry", str(f)]) == 0
    written = json.loads((tmp_path / "telemetry.json").read_text())
    assert written["count"] == 0
    assert telemetry_block.render(written)["text"] == ""


def test_telemetry_toast_spec_fires_from_zero():
    spec = poll._toast_specs()["telemetry"]
    assert spec["threshold"] == 0
    assert spec["urgency"] == "critical"
    fired = []
    poll.evaluate_edge_toast(
        "telemetry", _verdict(count=1, detail="d"), spec,
        fire=lambda *a, **kw: fired.append(a), read=lambda n: False,
        write=lambda n, v: None)
    assert len(fired) == 1


def test_telemetry_fetch_stale_when_deadman_module_missing(monkeypatch):
    """FAIL-SAFE: a broken deadman import must become a poller `stale` marker
    (block renders empty, unit's OnFailure covers it) — never a crash."""
    monkeypatch.setattr(poll, "DEVRC_DIR", "/no/such/repo")
    out = poll.source("telemetry", poll.fetch_telemetry)
    assert out["state"] == "stale" and out["count"] == 0


def test_deadman_unknown_states_fallback_is_not_empty(monkeypatch):
    """If the deadman module cannot be loaded the fallback set must still be
    non-empty — an empty set would make EVERY unknown verdict read as healthy,
    which is precisely the failure this check exists to prevent."""
    monkeypatch.setattr(poll, "DEVRC_DIR", "/no/such/repo")
    assert poll._deadman_unknown_states() == UNKNOWN_STATES


def test_the_fallback_state_set_MATCHES_the_deadman_it_stands_in_for():
    """🔴 SEAM GUARD. The fallback above is a hardcoded COPY of
    deadman.UNKNOWN_STATES. A state added to the deadman and not here renders as a
    healthy green pill whenever the import path breaks — the silent green the
    whole telemetry block exists to prevent. This pins the RELATIONSHIP, so the
    set GROWING or SHRINKING on either side fails.
    """
    dm_py = str(SCRIPTS / "collector" / "deadman.py")
    spec = importlib.util.spec_from_file_location("_pin_deadman", dm_py)
    DM = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(DM)

    real = set(DM.UNKNOWN_STATES)
    # The literal pinned at the top of this file is the contract; both the real
    # module and the poller's fallback must equal it exactly.
    assert real == set(UNKNOWN_STATES), (
        "deadman.UNKNOWN_STATES changed without updating this file AND "
        "bar-status-poll's fallback: %s" % sorted(real ^ set(UNKNOWN_STATES)))
    src = (SCRIPTS / "bar-status-poll").read_text()
    for state in sorted(real):
        assert '"%s"' % state in src, (
            "bar-status-poll never mentions the %r state — its fallback set "
            "cannot contain it" % state)

