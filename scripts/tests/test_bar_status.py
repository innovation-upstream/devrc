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
import ast
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
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
airvpn_block = _load("i3status-airvpn", "i3status_airvpn")
telemetry_block = _load("i3status-telemetry", "i3status_telemetry")
# The ONE definition of "this cache is too old to present as a measurement",
# which every block above loads as a co-located sibling. A real `.py`, so it
# imports by path like any module — the blocks are extensionless and cannot.
freshness = _load("bar_freshness.py", "bar_freshness")

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
def _agent(status="running", kicked=True, activity="2026-08-12T11:59:30Z"):
    """A synthetic clawgate 0.7.86 agent object (public repo: nothing here came
    off the real board)."""
    return {"id": 5501, "name": "sample-forge", "status": status,
            "kickedOff": kicked, "lastActivityAt": activity,
            "updatedAt": activity}


# The clock the parse tests evaluate against: 2026-08-12T12:00:00Z.
_NOW = 1786536000.0


def test_parse_clawgate_counts_pending_states():
    tasks = [
        {"id": 1, "status": "open"},
        {"id": 2, "status": "ready_for_review"},
        # 🔴 in_progress WITH A LIVE AGENT is genuinely not on the human — that
        # much of the old predicate was right, and stays right.
        {"id": 3, "status": "in_progress", "agent": _agent()},
        {"id": 4, "status": "complete"},
        {"id": 5, "status": "dismissed"},
    ]
    out = poll.parse_clawgate(tasks, now=_NOW)
    assert out["count"] == 2
    assert out["state"] == "Warning"
    assert "#1" in out["detail"] and "#2" in out["detail"]


def test_parse_clawgate_counts_a_stuck_in_progress_task():
    # 🔴 THE BUG. The poller used to exclude `in_progress` by name — "an agent
    # is working = not on the human" — which is exactly the state a dead
    # dispatch is stranded in. Four hours idle, agent still `running`.
    tasks = [{"id": 8, "status": "in_progress",
              "agent": _agent(activity="2026-08-12T08:00:00Z")}]
    out = poll.parse_clawgate(tasks, now=_NOW)
    assert out["count"] == 1 and out["stuck_count"] == 1
    assert out["stuck"][0]["reasons"] == ["agent_idle"]
    assert out["stuck"][0]["agent_idle_secs"] == pytest.approx(14400, abs=1)
    assert "1 stuck" in out["detail"]


def test_parse_clawgate_zero_is_neutral():
    out = poll.parse_clawgate([{"id": 9, "status": "complete"}], now=_NOW)
    assert out["count"] == 0
    assert out["state"] == "Idle"


def test_parse_clawgate_empty_list():
    out = poll.parse_clawgate([], now=_NOW)
    assert out["count"] == 0 and out["state"] == "Idle"
    assert out["detail"] == "no pending tasks"
    # A measured zero on BOTH halves, and the schema that says so.
    assert out["pending_count"] == 0 and out["stuck_count"] == 0
    assert out["schema"] == poll.CG.SCHEMA


def test_parse_clawgate_enumerates_ready_for_review():
    # The count moving without naming what finished is the reported failure.
    out = poll.parse_clawgate(
        [{"id": 11, "status": "ready_for_review", "title": "finished item"}],
        now=_NOW)
    assert out["ready_for_review"] == [{"id": 11, "title": "finished item"}]


def test_parse_clawgate_delegates_every_judgement_to_the_shared_module():
    # 🔴 THE SEAM: the poller supplies a clock and nothing else. If it grew its
    # own copy of the predicate, the poller and session-manager would disagree
    # with the bar again.
    import ast
    import inspect
    src = inspect.getsource(poll.parse_clawgate)
    assert "CG.attention" in src
    # Strip the docstring — prose about the predicate is fine, a second
    # implementation of it is not.
    fn = ast.parse(src).body[0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(fn.body[0].value, ast.Constant)
                           and isinstance(fn.body[0].value.value, str)) else fn.body
    code = "\n".join(ast.unparse(n) for n in body)
    # The statuses are taken from the shared module rather than re-spelled here
    # — test_clawgate_predicate_single_source.py would (correctly) flag a
    # literal set of them in this file as a second copy of the predicate.
    for status in sorted(poll.CG.PENDING_TASK_STATES
                         | {poll.CG.IN_PROGRESS, "complete"}):
        assert status not in code, status


def test_parse_clawgate_tolerates_junk_elements():
    out = poll.parse_clawgate([None, "x", 3, {"status": "open", "id": 7}],
                              now=_NOW)
    assert out["count"] == 1


def test_fetch_clawgate_asks_for_the_summary_form_and_keeps_the_token_out_of_it(
        monkeypatch):
    """🔴 TWO claims on one seam, because a mutation sweep found the URL
    unguarded: the poller must go through the SHARED url builder (a hardcoded
    `/api/tasks` here would silently re-fetch ~27x the bytes every 45s and drift
    from agent-ops), and the credential must travel in a HEADER — a token in a
    URL lands in argv, proxy logs and error strings."""
    seen = {}

    def fake_http_json(url, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers or {}
        return []

    monkeypatch.setattr(poll, "_http_json", fake_http_json)
    monkeypatch.setattr(poll.CG, "read_clawgate_env",
                        lambda *a, **k: ("http://cg.invalid:1",
                                         "sentinel-token-value"))
    poll.fetch_clawgate()
    assert seen["url"] == "http://cg.invalid:1/api/tasks?summary=1"
    assert seen["url"] == poll.CG.tasks_url("http://cg.invalid:1")
    assert "sentinel-token-value" not in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer sentinel-token-value"


def test_a_failing_clawgate_fetch_never_leaks_the_token_into_the_cache(
        monkeypatch, tmp_path):
    """The `stale` marker formats the exception. Pin that the credential cannot
    reach it — this payload is written to disk and read by two other tools."""
    monkeypatch.setattr(poll.CG, "read_clawgate_env",
                        lambda *a, **k: ("http://cg.invalid:1",
                                         "sentinel-token-value"))

    def boom(url, headers=None, timeout=None):
        raise OSError("connection refused to %s" % url)

    monkeypatch.setattr(poll, "_http_json", boom)
    payload = poll.source("clawgate", poll.fetch_clawgate)
    assert payload["state"] == "stale"
    assert "sentinel-token-value" not in json.dumps(payload)


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
def _media_cache(qbit_payload):
    """`parse_media`'s facts, STAMPED as the poller would stamp them.

    🔴 `poll.source()` puts an integer `ts` on every payload it writes, and
    `i3status-media` now refuses to present an unstamped or old one as a reading
    of the tunnel. Going through the real `source()` rather than adding a `ts`
    by hand keeps the fixture the shape the writer actually produces.
    """
    return poll.source("media", lambda: poll.parse_media(qbit_payload))


def test_media_block_passes_through_connected_active():
    payload = _media_cache({"connection_status": "connected",
                            "dl_info_speed": 151903, "up_info_speed": 412349})
    out = media_block.render(payload)
    assert out["icon"] == "net_down" and out["state"] == "Idle"
    assert out["text"] == payload["text"]


def test_media_block_hides_when_connected_idle():
    payload = _media_cache({"connection_status": "connected",
                            "dl_info_speed": 0, "up_info_speed": 0})
    assert media_block.render(payload) == {"text": "", "state": "Idle"}


def test_media_block_firewalled_is_red():
    payload = _media_cache({"connection_status": "firewalled"})
    out = media_block.render(payload)
    assert out["state"] == "Critical" and "firewalled" in out["text"]


def test_a_media_cache_nobody_REWROTE_stops_showing_live_transfer_speeds():
    """🔴 THE DEFECT, on this block. It always ALARMED on the stale MARKER, so
    it looked immune — but nothing here read `ts`, and alarming on the marker
    only covers the outage the poller is alive to report. Measured against the
    shipped block: a day-old cache kept rendering `CA ↓148K ↑402K` as a
    present-tense fact about a tunnel nobody had looked at.
    """
    payload = _media_cache({"connection_status": "connected",
                            "dl_info_speed": 151903, "up_info_speed": 412349})
    live = media_block.render(payload)
    frozen = media_block.render(dict(payload, ts=payload["ts"] - 86_400))
    assert "↓" in live["text"] and live["state"] == "Idle"
    assert frozen == {"icon": "net_down", "text": "qBit?",
                      "short_text": "qBit?", "state": "Warning"}
    assert frozen != live


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


#: Fields EVERY block now needs beyond `count`/`state` to render its normal,
#: FULLY MEASURED shape, as ZERO-ARG FACTORIES (they have to be built against the
#: current clock).
#:
#:  * `ts` — an absent or OLD one renders the block's UNMEASURED pill, because a
#:    cache nobody has refreshed is not a measurement of anything as it is now.
#:    Every payload `bar-status-poll` writes carries `ts`, so a fixture without
#:    one was never a realistic cache; it was a fixture that could not tell the
#:    freshness gate from a hole in it. This applied to clawgate alone until this
#:    change and applies to all seven blocks now.
#:  * `stuck_count` (clawgate only) — an ABSENT one renders `?` on purpose (a
#:    cache from a poller predating the stuck predicate carries a count computed
#:    by the old status-only rule, so it is a different number, not merely a
#:    partial one).
#:
#: The generic block tests below are about icon / colour / hide-at-zero, so they
#: hand every block a MEASURED, CURRENT payload; the `?` renderings and the
#: freshness gate have their own tests.
_MEASURED_EVERY_BLOCK = ("ts",)
BLOCK_MEASURED_EXTRA = {
    "clawgate": lambda: {"stuck_count": 0},
}


def _measured(name, **fields):
    """A cache payload for `name` in its fully-measured shape.

    The defaults FILL IN, they do not overwrite: a caller passing an explicit
    `stuck_count=1` is naming the thing under test, and a helper that quietly
    replaced it with the measured-zero default would turn that test into an
    assertion about the default instead.
    """
    fields.setdefault("ts", int(time.time()))
    extra = BLOCK_MEASURED_EXTRA.get(name)
    if extra is not None:
        for key, value in extra().items():
            fields.setdefault(key, value)
    return fields


#: A `now` that is nothing like a default. Freshness is a function of
#: `now - ts`, and a fixture built on `now = 0` (or on a `ts` equal to the
#: constant under test) cannot distinguish a working subtraction from a missing
#: one — so every freshness test below is anchored here and offsets from it.
FIXED_NOW = 1_800_000_000.0


def _aged(age_secs, **fields):
    """A cache payload written `age_secs` before FIXED_NOW.

    🔴 `ts` is set as an INT, not a float. `bar_freshness.int_or_none` treats a
    non-integral `ts` as "not written by a poller we recognise", so a fixture
    built on a bare `time.time()` short-circuits EVERY case to the unmeasured
    pill and looks exactly like a code defect. That cost a wrong diagnosis once.
    """
    fields.setdefault("ts", int(FIXED_NOW - age_secs))
    return fields


#: The VISIBLE "the board could not be read" pill, spelled out as a LITERAL —
#: never built from the block's own constants, so a test can disagree with the
#: module instead of restating it. Used by both the pure-function assertions and
#: the end-to-end subprocess pair, which is what lets the two be compared.
UNKNOWN_PILL = {"icon": "tasks", "text": "?", "short_text": "?",
                "state": "Warning"}
INVISIBLE_PILL = {"text": "", "state": "Idle"}

# --------------------------------------------------------------------------- #
# 🔴 THE WHOLE BAR, not one block: every cache the poller writes, and what each
# block renders for it in the two shapes that must never be confusable.
#
# `i3status-clawgate` grew a freshness gate and a visible `?` in PR #490. The
# other six had the same defect and none of the gate: measured 2026-08-14 against
# the OPERATOR'S OWN cache payloads, a file aged 24h rendered BYTE-IDENTICALLY to
# the live one in six of seven blocks — `alerts` announcing 39 firing alerts,
# `civitai` 146 on a CLIENT PROD cluster, `media` live transfer speeds, `airvpn`
# a dim "tunnel deliberately off", `mail` and `telemetry` an invisible all-clear.
# None of them had read `ts` at all.
# --------------------------------------------------------------------------- #
ALL_BLOCKS = [
    ("clawgate", clawgate_block),
    ("mail", mail_block),
    ("alerts", alerts_block),
    ("civitai", civitai_block),
    ("media", media_block),
    ("airvpn", airvpn_block),
    ("telemetry", telemetry_block),
]

#: The nf-md-alert triangle the two alert blocks prepend. A LITERAL codepoint,
#: not `mod.ALERT_GLYPH` — a test that reads the constant it is checking cannot
#: notice the constant changing.
_GLYPH = "\U000f002a"

#: 🔴 What each block renders when it CANNOT MEASURE, as LITERALS. Every entry
#: was read off the shipped block and typed out here; none is computed from the
#: module, so this table can disagree with the code rather than restate it.
UNMEASURED_PILL = {
    "clawgate": {"icon": "tasks", "text": "?", "short_text": "?",
                 "state": "Warning"},
    "mail": {"icon": "mail", "text": "?", "short_text": "?",
             "state": "Warning"},
    "alerts": {"text": _GLYPH + " ?", "short_text": _GLYPH + " ?",
               "state": "Warning"},
    "civitai": {"text": _GLYPH + " civ ?", "short_text": _GLYPH + " civ ?",
                "state": "Warning"},
    "media": {"icon": "net_down", "text": "qBit?", "short_text": "qBit?",
              "state": "Warning"},
    # ⚠ THE ONE PILL WHOSE DISCRIMINANT IS COLOUR ALONE. The airvpn block is an
    # icon BUTTON: its steady state is `up: false` -> a dim neutral icon with no
    # text, and its unreadable state is the same icon in soft yellow. So `text`
    # is empty in both and only `state` separates them. That is the block's
    # existing design (a hidden pill cannot be clicked to open the menu), not
    # something this change introduced — but it means the assertions below must
    # compare WHOLE PILLS, never just text, or airvpn passes them vacuously.
    "airvpn": {"icon": "net_vpn", "text": "", "short_text": "",
               "state": "Warning"},
    "telemetry": {"text": "tlm ?", "short_text": "tlm ?", "state": "Warning"},
}

#: 🔴 What each block renders for a MEASURED, CURRENT, entirely quiet reading.
#: The pill the table above must never be equal to — that inequality IS the
#: defect this change fixes, stated as a relationship rather than as a spelling.
MEASURED_ALL_CLEAR_PILL = {
    "clawgate": {"text": "", "state": "Idle"},
    "mail": {"text": "", "state": "Idle"},
    "alerts": {"text": "", "state": "Idle"},
    "civitai": {"text": "", "state": "Idle"},
    "media": {"text": "", "state": "Idle"},          # connected + idle
    "airvpn": {"icon": "net_vpn", "text": "", "short_text": "",
               "state": "Idle"},                      # tunnel deliberately off
    "telemetry": {"text": "", "state": "Idle"},
}


def _quiet_payload(name, age_secs=0):
    """A MEASURED, entirely quiet cache payload for `name`, aged `age_secs`.

    Per-block because the state blocks are not counts: `media` says "connected
    and idle" with an empty `text`, `airvpn` says "tunnel off" with `up: False`.
    Both are readings, both are reassuring, and both must stop being what an
    unreadable cache looks like.
    """
    base = _aged(age_secs)
    if name == "media":
        base.update({"text": "", "short_text": "", "state": "Idle"})
    elif name == "airvpn":
        base.update({"up": False})
    elif name == "clawgate":
        base.update({"count": 0, "stuck_count": 0, "state": "Idle"})
    else:
        base.update({"count": 0, "state": "Idle"})
    return base


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
    # 🔴 A MEASURED zero, which is the only thing allowed to hide. The payload
    # goes through `_measured` so clawgate gets the current `ts` its freshness
    # gate requires — an unmeasured cache has its own, VISIBLE rendering and
    # must not be able to reach this assertion.
    out = mod.render(_measured(name, count=0, state="Idle"))
    assert out == {"text": "", "state": "Idle"}
    assert "icon" not in out            # truly invisible: no icon either


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_visible_and_coloured_when_positive(name, mod, icon, default_state):
    out = mod.render(_measured(name, count=3, state=default_state))
    exp = _expected_text(mod, 3)
    if getattr(mod, "ALERT_GLYPH", None):
        # alert blocks carry the glyph in the text, NOT the i3status-rust `icon`
        assert "icon" not in out
        assert mod.ALERT_GLYPH in out["text"]
    else:
        assert out["icon"] == icon
    assert out["text"] == exp and out["short_text"] == exp
    assert out["state"] == default_state


# --------------------------------------------------------------------------- #
# 🔴 The clawgate block and the STUCK half — the surface that could not show it.
# --------------------------------------------------------------------------- #
def test_the_bar_RENDERS_A_STUCK_DISPATCH_DIFFERENTLY_from_plain_pending():
    """🔴 THE MEASURED DEFECT. `2 open` and `2 open + 1 STUCK` both rendered
    `{"text": "3", "state": "Warning"}` — byte-identical. The bar is one of the
    three surfaces the stuck predicate was built for and it could not express
    the one condition worth walking to a terminal for.

    Everything about the two blocks must differ: the text AND the colour.
    """
    plain_pending = clawgate_block.render(
        _measured("clawgate", count=3, state="Warning"))
    with_stuck = clawgate_block.render(
        _measured("clawgate", count=3, stuck_count=1, state="Critical"))
    assert plain_pending["text"] == "3"
    assert with_stuck["text"] == "3!1"
    assert plain_pending["text"] != with_stuck["text"]
    assert plain_pending["state"] != with_stuck["state"]
    assert with_stuck["state"] == "Critical"
    assert with_stuck["short_text"] == with_stuck["text"]


@pytest.mark.parametrize("stuck,expected", [
    (0, "5"),          # a MEASURED all-clear renders nothing extra
    (1, "5!1"),
    (4, "5!4"),
    (None, "5?"),      # key present but null -> unmeasured
])
def test_the_stuck_half_of_the_block_text(stuck, expected):
    assert clawgate_block.render(_measured(
        "clawgate", count=5, stuck_count=stuck,
        state="Warning"))["text"] == expected


def test_a_cache_with_NO_stuck_key_renders_unmeasured_not_a_clean_count():
    """🔴 A cache written by a poller predating the stuck predicate carries a
    `count` computed by the old status-only rule — a DIFFERENT number, not a
    partial one. Rendering it as a clean count is the substitution of an unread
    queue for an empty one. The measured zero and the absent key must not look
    alike."""
    absent = clawgate_block.render(
        _aged(0, count=5, state="Warning"), now=FIXED_NOW)
    measured_zero = clawgate_block.render(
        _aged(0, count=5, stuck_count=0, state="Warning"), now=FIXED_NOW)
    assert absent["text"] == "5?"
    assert measured_zero["text"] == "5"
    assert absent["text"] != measured_zero["text"]


@pytest.mark.parametrize("junk", ["x", [], {}, None, True, False, 2.5])
def test_a_malformed_stuck_count_reads_as_unmeasured_never_as_zero(junk):
    # 🔴 Fail-safe in the HONEST direction: garbage is "cannot tell" (`?`), never
    # "none" (a bare count). `True`/`False` are in here deliberately — a bool is
    # an int in Python, so `int(True) == 1` would render a fabricated stuck
    # dispatch and `int(False) == 0` a fabricated all-clear.
    out = clawgate_block.render(
        _aged(0, count=2, stuck_count=junk, state="Warning"), now=FIXED_NOW)
    assert out["text"] == "2?", junk


def test_the_stuck_block_still_hides_at_a_MEASURED_zero():
    # The calm contract for a MEASURED reading is unchanged: an empty board is
    # an invisible pill, and a stuck count cannot make one visible (a stuck row
    # is counted IN `count`, so `count == 0` with `stuck_count == 2` is an
    # incoherent payload, not a wedge — it must not light the bar).
    assert clawgate_block.render(
        _aged(0, count=0, stuck_count=2, state="Critical"),
        now=FIXED_NOW)["text"] == ""


# --------------------------------------------------------------------------- #
# 🔴 The freshness gate: a cache nobody refreshed is not a reading of NOW.
# --------------------------------------------------------------------------- #
#: The widest gap a HEALTHY poller can leave between two writes, from the unit
#: itself (nix/graphical.nix): `OnUnitActiveSec = 45s` re-arms after each run and
#: `TimeoutStartSec = 90` bounds the run. Pinned as a LITERAL derived from those
#: two numbers rather than imported from the block, so this test can disagree
#: with the block's constant instead of restating it.
WORST_HEALTHY_GAP_SECS = 45 + 90


@pytest.mark.parametrize("age,current", [
    (0, True),                        # just written
    (WORST_HEALTHY_GAP_SECS, True),   # the worst a healthy poller can do
    (599, True),
    (600, True),                      # the last age still considered current
    (601, False),                     # the first that is not
    (3600, False),
    (86_400, False),                  # the poller has been dead for a day
])
def test_a_cache_the_poller_STOPPED_REFRESHING_is_not_a_current_reading(
        age, current):
    """🔴 `render` never read `ts` at all, so a cache frozen days ago rendered a
    confident, current-looking count — a poller that dies while the board is
    clean pins the pill to "clean" forever, and a dead measuring apparatus is
    exactly when a wedge is least visible elsewhere.

    The ages bracket the constant from BOTH sides and include a middle, so this
    cannot pass against a gate that is merely present-and-always-true.
    """
    payload = _aged(age, count=7, stuck_count=0, state="Warning")
    assert clawgate_block.is_current(payload, FIXED_NOW) is current
    out = clawgate_block.render(payload, now=FIXED_NOW)
    assert (out["text"] == "7") is current
    assert (out["text"] == "?") is not current


def test_the_freshness_window_is_far_outside_normal_poller_jitter():
    """The constant is a claim about the unit, so assert the relationship rather
    than the number: anything a healthy poller can produce must be current, and
    the window must not be so wide that a dead poller goes unreported for hours.
    """
    assert clawgate_block.MAX_CACHE_AGE_SECS > WORST_HEALTHY_GAP_SECS * 2
    assert clawgate_block.MAX_CACHE_AGE_SECS <= 3600


# --------------------------------------------------------------------------- #
# 🔴 ONE DEFINITION OF "TOO OLD", and the one that only LOOKS like it.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
def test_every_block_shares_ONE_freshness_definition(name, mod):
    """🔴 The gate is a predicate, and a predicate open-coded at seven sites is
    typically wrong at six of them in the same direction. Every block must be
    holding the SAME module object, not a copy and not a re-spelling."""
    assert mod.fresh.MAX_CACHE_AGE_SECS == freshness.MAX_CACHE_AGE_SECS
    assert mod.fresh.unmeasured is not None
    # the block loads it from its OWN directory, which is what makes the nix
    # symlink below load-bearing
    assert mod.fresh.__name__ == "_bar_freshness"


def _renders_as_measured(name, mod, age):
    """Does `name` still present its own ALARM cache as a CURRENT reading at
    `age` seconds old? Asked of the RENDER, not of any constant."""
    payload, measured_pill, _frozen = ALARM_CACHES[name]
    return mod.render(_aged(age, **payload), now=FIXED_NOW) == measured_pill


@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
def test_every_block_measures_the_SAME_WINDOW_as_the_shared_one(name, mod):
    """🔴 STRUCTURAL, BECAUSE THE SPELLED VERSION LET THE MUTANT THROUGH.

    This test used to assert `"MAX_CACHE_AGE_SECS = 600" not in <source>`, i.e.
    it rejected a copy of the CORRECT value and permitted every wrong one: a
    mutant setting clawgate's re-export to 900 passed 499 of 499. A guard that
    can be satisfied by changing the number it exists to protect is a spelling,
    not a structure.

    Three checks, none of which mentions a number:
      (i)  BEHAVIOURAL — bisect the age at which THIS block stops presenting its
           own alarm cache as current, and require it to equal the shared
           window. Catches a block that re-implements the comparison, passes its
           own `max_age`, or gates on a different field entirely. This is the
           only one of the three that would notice a `<`/`<=` slip.
      (ii) PUBLISHED CONSTANTS — any int a block exports whose name mentions an
           age must BE the shared value, whatever the shared value is. This is
           the 900-mutant's grave.
      (iii) NO LITERALS — via the AST, no block may bind an age-named constant
           to a literal at all, which is the original intent stated so that it
           cannot be satisfied by picking a different literal.
    """
    window = freshness.MAX_CACHE_AGE_SECS
    # (i) bisect the block's own flip point. The bracket is asserted first: a
    # bisection whose ends do not straddle the boundary would "find" one anyway.
    lo, hi = 0, 100_000
    assert _renders_as_measured(name, mod, lo), name
    assert not _renders_as_measured(name, mod, hi), name
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if _renders_as_measured(name, mod, mid):
            lo = mid
        else:
            hi = mid
    assert lo == window, (name, "last age still presented as measured", lo)

    # (ii) every age constant the block PUBLISHES is the shared one
    for attr, value in vars(mod).items():
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if "AGE" in attr.upper() or "SECS" in attr.upper():
            assert value == window, (name, attr, value)

    # (iii) ...and none of them is written as a literal in the first place
    tree = ast.parse((SCRIPTS / dict(BLOCK_SOURCE_FILES)[name]).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if "AGE" in target.id.upper() or "SECS" in target.id.upper():
                assert not isinstance(node.value, ast.Constant), \
                    (name, target.id, "an age bound to a literal has left the "
                                      "shared definition behind")

    src = (SCRIPTS / dict(BLOCK_SOURCE_FILES)[name]).read_text()
    body = "\n".join(line for line in src.splitlines()
                     if not line.lstrip().startswith("#"))
    assert body.count("fresh.unmeasured") >= 1, name


#: name -> the extensionless script implementing it. Hand-written, and pinned
#: two-way against the block registry so neither can gain an entry silently.
BLOCK_SOURCE_FILES = [
    ("clawgate", "i3status-clawgate"), ("mail", "i3status-mail"),
    ("alerts", "i3status-alerts"), ("civitai", "i3status-civitai"),
    ("media", "i3status-media"), ("airvpn", "i3status-airvpn"),
    ("telemetry", "i3status-telemetry"),
]
assert [n for n, _ in BLOCK_SOURCE_FILES] == [n for n, _ in ALL_BLOCKS]


def test_every_block_that_loads_the_sibling_is_DEPLOYED_beside_it():
    """🔴 THE SEAM A UNIT TEST CANNOT SEE. Each block loads `bar_freshness.py`
    out of its OWN directory — which at runtime is
    `~/.config/i3status-rust/scripts`, populated entirely by `home.file` entries
    in `nix/graphical.nix`. The tests here load blocks from the repo, where the
    sibling is always present, so nothing else in this file can notice a missing
    symlink. Without it EVERY count pill on a healthy workbench renders `?`.

    Two-way: every block that loads the sibling must be deployed, AND the
    sibling itself must be deployed, under the SAME host gate. (A flake also
    silently omits an untracked file, which is why `bar_freshness.py` had to be
    `git add`ed before the first switch.)
    """
    nix = (SCRIPTS.parent / "nix" / "graphical.nix").read_text()
    assert '.config/i3status-rust/scripts/bar_freshness.py"' in nix, \
        "bar_freshness.py has no home.file entry — every count pill would be `?`"
    assert "../scripts/bar_freshness.py" in nix
    for name, script in BLOCK_SOURCE_FILES:
        src = (SCRIPTS / script).read_text()
        assert "_load_freshness" in src, name
        entry = '.config/i3status-rust/scripts/%s"' % script
        assert entry in nix, "%s loads the sibling but is not deployed" % name
    # the sibling must not be deployed on a NARROWER gate than its consumers:
    # both are !isLaptop, so a laptop has neither rather than blocks with no
    # module to load.
    for line in nix.splitlines():
        if "scripts/bar_freshness.py\"" in line and "home.file" in line:
            assert "!isLaptop" in line, line
            break
    else:
        raise AssertionError("no home.file line for bar_freshness.py")


def test_the_two_TOO_OLD_constants_measure_DIFFERENT_THINGS():
    """🔴 THE DUPLICATE THAT IS NOT ONE, examined rather than merged.

    `bar_freshness.MAX_CACHE_AGE_SECS` (600) and
    `bar-status-poll.TELEMETRY_UNKNOWN_GRACE` (1800) both read as "how old is
    too old", and they were the two hand-spelled age numbers in this subsystem.
    They are different quantities with different subjects:

      * MAX_CACHE_AGE_SECS asks "is the WRITER alive?" — nobody has rewritten
        this file, so no reading is being taken at all. Derived from the timer
        (45s re-arm + 60s systemd accuracy + 90s run ceiling = 195s worst
        healthy gap); anything at or below that fires on healthy jitter.
      * TELEMETRY_UNKNOWN_GRACE asks "how long has a LIVING writer been saying
        it cannot tell?" — the poller is stamping fresh payloads every 45s and
        the ClickHouse query is what keeps failing. Its job is to debounce a
        restart, so it must be far longer than one.

    Collapsing them breaks whichever question loses, so the invariant is an
    ORDERING with room in it, not equality — and NOT two restated literals,
    which would just move the duplication into this file.
    """
    gate = freshness.MAX_CACHE_AGE_SECS
    grace = poll.TELEMETRY_UNKNOWN_GRACE
    assert gate != grace, "if these ever became one number, one of the two " \
                          "questions above stopped being asked"
    # the writer-liveness gate must fire FIRST: a frozen poller is a fact about
    # every source, and must not wait out a single source's backend debounce.
    assert gate < grace
    # ...and the gate must still be out of reach of healthy jitter, while the
    # grace must be well clear of a restart-length blackout.
    assert gate > WORST_HEALTHY_GAP_SECS * 2
    assert grace >= gate * 2


@pytest.mark.parametrize("age,current", [
    (0, True),
    (WORST_HEALTHY_GAP_SECS, True),
    (599, True),
    (600, True),          # the last age still considered current
    (601, False),         # the first that is not
    (86_400, False),
])
def test_the_SHARED_freshness_boundary(age, current):
    """The gate itself, at the boundary from both sides plus a middle — so it
    cannot pass against a comparison that is merely present and always-true, or
    against one that is off by a tick in either direction."""
    payload = {"ts": int(FIXED_NOW - age), "count": 1}
    assert freshness.is_current(payload, FIXED_NOW) is current
    assert freshness.unmeasured(payload, FIXED_NOW) is not current
    assert freshness.cache_age_secs(payload, FIXED_NOW) == float(age)


@pytest.mark.parametrize("recorded,expected", [
    # a loud reading keeps its colour and gains the marker
    ({"text": "39", "short_text": "39", "state": "Critical"},
     {"text": "39?", "short_text": "39?", "state": "Critical"}),
    ({"text": "5", "short_text": "5", "state": "Warning"},
     {"text": "5?", "short_text": "5?", "state": "Warning"}),
    # a VISIBLE but neutral reading is floored at Warning: an unreadable cache
    # is never Idle, whatever colour it wore while someone was measuring it
    ({"text": "20", "short_text": "20", "state": "Idle"},
     {"text": "20?", "short_text": "20?", "state": "Warning"}),
    ({"text": "20", "short_text": "20", "state": "Good"},
     {"text": "20?", "short_text": "20?", "state": "Warning"}),
    # a junk state is not a colour to preserve
    ({"text": "20", "short_text": "20", "state": "Chartreuse"},
     {"text": "20?", "short_text": "20?", "state": "Warning"}),
    # short_text is derived from text when the reading did not carry one
    ({"text": "LEAK", "state": "Critical"},
     {"text": "LEAK?", "short_text": "LEAK?", "state": "Critical"}),
])
def test_carry_forward_MARKS_a_reading_without_QUIETENING_it(recorded, expected):
    """🔴 THE ONE DEFINITION of how an unmeasured cache renders a reading it
    still holds, exercised on its own so the seven blocks' expectations are not
    the only thing pinning it. `fallback` must not be reached for any of these.
    """
    sentinel = {"text": "FALLBACK", "state": "Warning"}
    out = freshness.carry_forward(recorded, sentinel)
    assert out == expected, recorded
    assert out != sentinel


@pytest.mark.parametrize("recorded", [
    None, {}, [], "x", 3, True, 2.5,
    {"text": "", "state": "Critical"},      # invisible: nothing to carry
    {"text": None, "state": "Critical"},    # junk text is not a reading
    {"state": "Critical"},                  # ...nor is an absent one
])
def test_carry_forward_INVENTS_NOTHING_when_there_is_no_reading(recorded):
    """The other half of the decision: only a RECORDED alarm is carried. A
    payload that recorded nothing — a marker, which writes `count: 0` over the
    last reading, a missing file, a measured zero — falls back to the block's
    bare `?`, never to a fabricated number or a borrowed colour."""
    fallback = {"icon": "mail", "text": "?", "short_text": "?",
                "state": "Warning"}
    out = freshness.carry_forward(recorded, fallback)
    assert out == fallback, recorded
    assert out is not fallback, "must be a copy: a block hands in its constant"


def test_carry_forward_KEEPS_THE_ICON_a_state_pill_renders_by():
    """`media` and `airvpn` carry their identity in the i3status-rust `icon`
    field, not in the text. A carry that dropped it would render a bare `LEAK?`
    with no VPN glyph — a different pill in the same pixels."""
    out = freshness.carry_forward(
        {"icon": "net_vpn", "text": "LEAK", "short_text": "LEAK",
         "state": "Critical"}, {"icon": "net_vpn", "text": "", "state": "Warning"})
    assert out["icon"] == "net_vpn"
    assert out["text"] == "LEAK?" and out["state"] == "Critical"


@pytest.mark.parametrize("pill,loud", [
    ({"state": "Critical"}, True),
    ({"state": "Warning"}, True),
    ({"state": "Idle"}, False),
    ({"state": "Good"}, False),
    ({"state": "Info"}, False),
    ({"state": "critical"}, False),        # i3status-rust states are cased
    ({}, False),
    (None, False),
    ("Critical", False),
])
def test_is_loud_answers_only_for_a_pill_that_says_LOOK_AT_ME(pill, loud):
    assert freshness.is_loud(pill) is loud


@pytest.mark.parametrize("ts", [None, "1786759794", True, False, 2.5, [], {}])
def test_an_ABSENT_OR_JUNK_ts_is_never_a_fresh_one(ts):
    """`True` is here deliberately: a bool is an int in Python, so a coercing
    check would read it as epoch second 1 — an age of ~57 years passing as a
    timestamp."""
    payload = {"count": 7}
    if ts is not None:
        payload["ts"] = ts
    assert freshness.cache_age_secs(payload, FIXED_NOW) is None
    assert freshness.is_current(payload, FIXED_NOW) is False
    assert freshness.unmeasured(payload, FIXED_NOW) is True


@pytest.mark.parametrize("payload,marker", [
    ({"state": "stale", "count": 0}, True),
    ({"error": "boom"}, True),
    ({"error": "", "state": "Idle"}, False),   # falsy error is not a marker
    ({"state": "Idle", "count": 3}, False),
    ({}, False),
    # 🔴 A NON-DICT IS NOT A MARKER — it is a missing or corrupt FILE, which is
    # nobody's statement about anything. Only a direct call reaches this: every
    # caller in the tree checks the type first, so a mechanically-generated
    # sweep found `return False` -> `return True` here SURVIVING the whole
    # suite. It is pinned rather than deleted because `is_marker` is a public
    # helper — `i3status-airvpn` sits one term away from calling it standalone —
    # and a helper that raises on a shape its own module hands around is a trap
    # for the next caller.
    (None, False), ([], False), (["a"], False), ("x", False), (3, False),
])
def test_is_marker_answers_only_for_what_the_POLLER_said(payload, marker):
    assert freshness.is_marker(payload) is marker, payload


def test_a_CLOCK_SKEWED_cache_reads_as_just_now_not_as_ancient():
    """A `ts` in the FUTURE (NTP step, a host with a fast clock) clamps to age 0
    rather than going negative — a negative age passing an `age <= max` check by
    accident is the same class of luck as a coerced bool."""
    future = {"ts": int(FIXED_NOW + 10_000), "count": 1}
    assert freshness.cache_age_secs(future, FIXED_NOW) == 0.0
    assert freshness.is_current(future, FIXED_NOW) is True


@pytest.mark.parametrize("ts", [None, "1786759794", True, False, 2.5, [], {}])
def test_an_ABSENT_OR_JUNK_ts_is_not_a_fresh_one(ts):
    """Every payload `bar-status-poll` writes carries an integer `ts`, so
    anything else means the file did not come from a poller we recognise. That
    is a reason to distrust the reading, never to assume it is fresh. `True` is
    in here deliberately: a bool is an int in Python, so a coercing check would
    read `True` as epoch second 1 — an age of ~57 years passing as a timestamp.
    """
    payload = {"count": 7, "stuck_count": 0, "state": "Warning"}
    if ts is not None:
        payload["ts"] = ts
    assert clawgate_block.cache_age_secs(payload, FIXED_NOW) is None
    assert clawgate_block.is_current(payload, FIXED_NOW) is False
    assert clawgate_block.render(payload, now=FIXED_NOW)["text"] == "?"


def test_a_measurement_OUTAGE_never_makes_a_KNOWN_alarm_QUIETER():
    """🔴 A stale reading is a reason to trust a number less, NOT to downgrade an
    alarm it already raised. The compound case this whole change is about is a
    dispatch wedging WHILE the poller is dead: the last thing we knew was `2
    stuck`, and dropping that to a bare `?` (or to an invisible pill) would let a
    measurement outage silence a live wedge.

    `parse_telemetry` draws the same line — an UNKNOWN verdict carrying a
    non-zero count stays Critical and keeps the count.

    ⚠ SCOPE: this exercises the FROZEN-FILE branch only — an old `ts` on a cache
    nobody rewrote. The other outage shape, where the poller writes a `stale()`
    marker OVER the cache, is a different code path and lives in
    `test_a_clawgate_OUTAGE_carries_the_LAST_KNOWN_stuck_count_forward`. It was
    FALSE until that fix landed, while this test's name claimed it generally.
    """
    stale_wedge = clawgate_block.render(
        _aged(86_400, count=24, stuck_count=2, state="Critical"), now=FIXED_NOW)
    stale_calm = clawgate_block.render(
        _aged(86_400, count=24, stuck_count=0, state="Warning"), now=FIXED_NOW)
    assert stale_wedge["text"] == "!2?"
    assert stale_wedge["state"] == "Critical"
    assert stale_wedge["short_text"] == stale_wedge["text"]
    # the trailing `?` is what says "not a current reading" — it must survive
    assert stale_wedge["text"].endswith("?")
    # and a stale CALM reading is NOT allowed to borrow that alarm
    assert stale_calm["text"] == "?" and stale_calm["state"] == "Warning"
    assert stale_wedge["state"] != stale_calm["state"]


def test_a_stale_cache_carrying_stuck_rows_still_names_them(tmp_path):
    """The same claim end to end, through the real script and the real cache
    path — the pure function above cannot see a `main()` that drops `now`."""
    (tmp_path / "clawgate.json").write_text(json.dumps(
        {"count": 24, "stuck_count": 2, "state": "Critical",
         "ts": int(time.time()) - 86_400}))
    out = _run_clawgate_block(tmp_path)
    assert out["text"] == "!2?" and out["state"] == "Critical"


# --------------------------------------------------------------------------- #
# 🔴 `render` AS A PURE FUNCTION — the half a subprocess cannot measure.
#
# The end-to-end group at the bottom of this file runs the block as a SUBPROCESS,
# and `i3status-clawgate.__main__` catches every exception and prints a
# BYTE-IDENTICAL `?` pill. So those tests cannot tell "rendered an unreadable
# cache correctly" from "crashed on the way there": replacing the whole body of
# `render()` with `raise RuntimeError` leaves all five of them green. These call
# `render()` directly, where a raise is a raise — which is also what makes the
# module's "Fail-safe throughout: it never raises, whatever the file contains" a
# TESTED claim rather than a docstring.
#
# ⚠ LABEL: this group is an INVARIANT GUARD, not regression coverage. The
# shipped block already answers all of these correctly, so it is green on the
# pre-change tree; what it kills is the mutant. Named in the PR: `render` raising
# unconditionally, and `_unmeasured(payload)` instead of `_unmeasured(None)` for
# a non-dict payload — both of which survived the entire suite before it existed.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [
    None,             # no file at all, or one `load()` could not parse
    [],               # valid JSON, wrong shape — and FALSY
    ["a", "list"],    # ...and a TRUTHY one: `payload or {}` keeps the list
    {},               # a dict that says nothing at all
    "x",              # a bare JSON string (truthy)
    "",               # ...and a falsy one
    3,                # a bare JSON number (truthy)
    0,                # ...and a falsy one
    True,             # a bare JSON bool — an int in Python
    2.5,              # a bare JSON float
    {"count": "NaN"},  # a dict whose count is unreadable, and with no `ts`
])
def test_the_clawgate_render_FUNCTION_is_failsafe_AND_stays_visible(bad):
    """Both halves in one assertion, because the module makes both claims:
    it does not RAISE, and it does not HIDE.

    🔴 The truthy non-dicts are the load-bearing rows. `_unmeasured` reads
    `(payload or {}).get("stuck_count")`, so handing it a truthy non-dict is an
    `AttributeError` — `[]` and `""` and `0` fall back to `{}` and would pass a
    mutant that `["a", "list"]`, `"x"` and `3` kill.
    """
    out = clawgate_block.render(bad, now=FIXED_NOW)   # must not raise
    assert out == UNKNOWN_PILL, bad
    assert out != INVISIBLE_PILL, bad


# ⚠ The clawgate-only version of this test — `render`'s `if count is None:
# return _unmeasured(...)` swapped for `return dict(_EMPTY)` — is now the
# `clawgate` row of `test_a_count_ANY_block_REFUSES_TO_READ_is_not_a_count_of_zero`
# above, which runs the same junk set against every count block. It was left
# clawgate-only when the branch was copied into five more of them, and a mutation
# sweep found the mail and telemetry copies unguarded.


def test_an_UNREADABLE_COUNT_still_keeps_a_KNOWN_stuck_alarm():
    """The escalation invariant on the junk-count path too: refusing to read the
    count is a reason to distrust the number beside the alarm, not to drop the
    alarm."""
    out = clawgate_block.render(
        _aged(0, count="NaN", stuck_count=2, state="Critical"), now=FIXED_NOW)
    assert out["text"] == "!2?" and out["state"] == "Critical"


@pytest.mark.parametrize("outage", ["frozen", "marker"])
@pytest.mark.parametrize("stuck", [1, 2, 9])
def test_ONE_stuck_dispatch_survives_an_OUTAGE_as_loudly_as_two(outage, stuck):
    """🔴 FOUND BY A MUTATION SWEEP, NOT BY REVIEW. Every fixture pinning the
    carried stuck alarm used `stuck_count: 2`, so `_unmeasured`'s `if stuck > 0`
    could be changed to `if stuck > 1` and the whole suite stayed green — a
    SINGLE wedged dispatch dropping from `!1?`/Critical to a bare `?`/Warning
    the moment the poller stopped, which is the exact downgrade `_unmeasured`
    exists to refuse. One is the count a threshold slip hides behind; nine is a
    value no constant in this module can be.

    BOTH outage shapes, because they reach `_unmeasured` by different routes:
    the FROZEN file still holds its own `stuck_count`, while the MARKER is
    handed one by `bar-status-poll.carry_stuck_forward`. A guard on one of them
    says nothing about the other.
    """
    if outage == "frozen":
        payload = _aged(86_400, count=22, stuck_count=stuck, state="Critical")
    else:
        payload = _aged(0, count=0, state="stale", stuck_count=stuck)
    out = clawgate_block.render(payload, now=FIXED_NOW)
    assert out["text"] == "!%d?" % stuck, (outage, out)
    assert out["short_text"] == out["text"], (outage, out)
    assert out["state"] == "Critical", (outage, out)
    assert out != UNKNOWN_PILL, (outage, stuck)


# --------------------------------------------------------------------------- #
# 🔴 The OTHER outage shape: the poller writes `stale` OVER the last reading.
#
# `test_a_measurement_OUTAGE_never_makes_a_KNOWN_alarm_QUIETER` above asserts the
# general claim in its NAME but exercises only the FROZEN-FILE branch (nobody
# rewrote the cache, so the old `stuck_count` is still on disk). On the branch
# the module names as PRIMARY — clawgate unreachable, so `bar-status-poll`
# writes a `stale()` marker OVER the cache — the claim was FALSE, measured:
#
#     t0  poller healthy, 2 stuck                  -> `24!2`  Critical
#     t1  clawgate down, stale() overwrites        -> `?`      Warning  <- QUIETER
#
# `carry_stuck_forward` is the fix: the marker inherits the previous cache's
# `stuck_count`, so the pill renders `!2?` and the alarm survives the outage that
# would otherwise have silenced it. These tests are RED on the pre-change tree.
# --------------------------------------------------------------------------- #
def _clawgate_unreachable():
    raise RuntimeError("clawgate unavailable")


@pytest.fixture
def _poll_into(tmp_path, monkeypatch):
    """Drive the REAL writer against a throwaway cache dir.

    🔴 `BAR_STATUS_DIR` and `signal_bar` are both redirected: the live cache in
    `~/.cache/bar-status` is the operator's, and `signal_bar` would `pkill
    -RTMIN+11` the running bar.
    """
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda _n: None)
    return tmp_path


def test_a_clawgate_OUTAGE_carries_the_LAST_KNOWN_stuck_count_forward(_poll_into):
    """🔴 THE SEAM, driven through the real writer and the real renderer: a
    healthy poll, then a failing one, then the pixel.

    A dispatch does not become un-stuck because clawgate stopped answering, so
    the alarm must survive the outage — and the trailing `?` is what keeps that
    honest, marking the carried 2 as the last readable poll rather than a fresh
    measurement.
    """
    healthy = poll.run_source("clawgate", lambda: {
        "count": 24, "stuck_count": 2, "state": "Critical", "detail": "2 stuck"})
    assert clawgate_block.render(healthy)["text"] == "24!2"

    outage = poll.run_source("clawgate", _clawgate_unreachable)
    assert outage["state"] == "stale", outage
    assert outage["stuck_count"] == 2, "the outage destroyed a live alarm"

    # ...and it is on DISK, not merely in the returned dict — the cache is what
    # the block reads, and `write_status` is the overwrite this guards against.
    on_disk = json.loads((_poll_into / "clawgate.json").read_text())
    assert on_disk["stuck_count"] == 2, on_disk
    rendered = clawgate_block.render(on_disk)
    assert rendered["text"] == "!2?", rendered
    assert rendered["state"] == "Critical", rendered
    assert rendered["text"].endswith("?")


def test_a_FIRST_EVER_poll_that_FAILS_invents_no_alarm(_poll_into):
    """No previous cache to carry: the honest answer is a bare `?`, and above all
    not a fabricated `!N?`. This is also the new-host / freshly-GC'd-cache path,
    so it runs on every machine's first poll."""
    out = poll.run_source("clawgate", _clawgate_unreachable)
    assert "stuck_count" not in out, out
    assert clawgate_block.render(out) == UNKNOWN_PILL


def test_a_RECOVERED_poll_REPLACES_the_carried_alarm(_poll_into):
    """🔴 The carry is not a RATCHET. Once clawgate answers again its own
    measurement wins outright — including a measured all-clear, which must take
    the pill back to calm. A carry that could only ever add would turn one
    outage during one wedge into a permanently Critical bar."""
    poll.run_source("clawgate", lambda: {
        "count": 24, "stuck_count": 2, "state": "Critical"})
    carried = poll.run_source("clawgate", _clawgate_unreachable)
    assert carried["stuck_count"] == 2

    recovered = poll.run_source("clawgate", lambda: {
        "count": 0, "stuck_count": 0, "state": "Idle"})
    assert recovered["stuck_count"] == 0
    assert clawgate_block.render(recovered) == INVISIBLE_PILL

    # and a subsequent outage now carries NOTHING, because nothing is known
    again = poll.run_source("clawgate", _clawgate_unreachable)
    assert "stuck_count" not in again, again
    assert clawgate_block.render(again) == UNKNOWN_PILL


@pytest.mark.parametrize("prev,expected", [
    (None, None),                    # first-ever poll: no cache at all
    ({}, None),                      # a cache that says nothing
    ("not a dict", None),            # a cache of the wrong shape
    ({"stuck_count": 0}, None),      # a MEASURED all-clear is not an alarm
    ({"stuck_count": -1}, None),     # incoherent; nothing to escalate
    ({"stuck_count": 2}, 2),
    ({"stuck_count": 7}, 7),
    ({"stuck_count": True}, None),   # a bool is not a reading (int in Python)
    ({"stuck_count": 2.5}, None),    # no coercion: 2.5 stuck dispatches is nobody's measurement
    ({"stuck_count": "2"}, None),    # ...nor is a string int() would swallow
    ({"stuck_count": None}, None),
])
def test_carry_stuck_forward_only_carries_a_REAL_previous_alarm(prev, expected):
    marker = poll.stale("clawgate unavailable")
    out = poll.carry_stuck_forward(marker, prev)
    assert out is marker, "it must mutate the payload it was handed"
    if expected is None:
        assert "stuck_count" not in out, prev
    else:
        assert out["stuck_count"] == expected, prev


@pytest.mark.parametrize("payload", [
    {"count": 24, "stuck_count": 0, "state": "Critical"},  # a measured all-clear
    {"count": 24, "stuck_count": 1, "state": "Critical"},  # a measured alarm
    {"count": 0, "state": "Idle"},                         # a pre-predicate cache
])
def test_carry_stuck_forward_NEVER_REWRITES_A_REAL_MEASUREMENT(payload):
    """The guard that keeps this from being a lie generator. A READABLE payload
    speaks for itself — including the third row, whose absent `stuck_count` is
    what makes the pill render `?` rather than a clean count. Overwriting that
    from a stale neighbour would put a number in the cache nobody measured."""
    before = dict(payload)
    poll.carry_stuck_forward(payload, {"stuck_count": 9})
    assert payload == before


@pytest.mark.parametrize("own", [0, 1, 5])
def test_carry_stuck_forward_NEVER_CARRIES_BACKWARDS(own):
    """A marker that already carries its OWN `stuck_count` has been measured,
    however partially, and an OLDER cache must not overwrite it. Carrying
    backwards is how a wedge that has since been resolved gets resurrected — and
    a `0` of its own is the sharpest case, because that is precisely the reading
    an unguarded carry would replace with an alarm."""
    out = poll.carry_stuck_forward(
        {"count": 0, "state": "stale", "stuck_count": own}, {"stuck_count": 5})
    assert out["stuck_count"] == own


def test_an_ERROR_MARKER_carries_forward_too():
    """`stale()` is not the only unreadable shape: a payload carrying `error`
    reaches `_unmeasured` by the same door in the block, so it must reach the
    carry by the same door here."""
    out = poll.carry_stuck_forward({"count": 0, "error": "boom"},
                                   {"stuck_count": 3})
    assert out["stuck_count"] == 3


@pytest.mark.parametrize("value", [
    0, 1, 2, 7, -1, 10 ** 9, True, False, None, 2.5, 0.0, "2", "x", [], {},
])
def test_the_two_NO_COERCION_predicates_ARE_ONE(value):
    """ONE RULE, ONE COPY — it used to be two, kept in step by this test.

    `bar-status-poll._strict_int` was a hand-written twin of the block's
    predicate, and its docstring justified the duplication with "the two sit on
    opposite sides of an extensionless-script boundary that neither can import
    across". That barrier stopped existing when `bar_freshness.py` was extracted
    as a plain `.py` — this poller already loads three siblings by explicit path
    — so the reason did not survive checking and the copy is gone.

    The values still go through both names (a rename that silently pointed one
    of them at `int` would pass an identity check on the module object but fail
    here on `2.5`/`True`), and the identity is asserted so a re-spelled copy
    cannot come back looking equal.
    """
    mine = poll._strict_int(value)
    theirs = clawgate_block._int_or_none(value)
    assert mine == theirs and type(mine) is type(theirs), value
    # ⚠ NOT `is freshness.int_or_none`: every consumer loads the sibling by path
    # and gets its OWN module object, so identity across consumers is false by
    # construction. What is checkable — and what a re-spelled copy would break —
    # is that each name IS its own loaded module's function, and that both
    # modules were loaded from the ONE file.
    assert poll._strict_int is poll.FRESH.int_or_none
    assert clawgate_block._int_or_none is clawgate_block.fresh.int_or_none
    one_file = str(SCRIPTS / "bar_freshness.py")
    assert poll.FRESH.__file__ == one_file
    assert clawgate_block.fresh.__file__ == one_file


def test_the_block_renders_what_attention_ACTUALLY_writes():
    """🔴 THE SEAM between the predicate and the pixel. Both halves were tested
    in isolation and the pair was still broken; these drive the real roll-up into
    the real renderer so no hand-written fixture can paper over a key rename.
    """
    import importlib.machinery
    import importlib.util
    lib = SCRIPTS / "lib" / "clawgate_tasks.py"
    ldr = importlib.machinery.SourceFileLoader("_cg_seam", str(lib))
    cg = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location("_cg_seam", str(lib), loader=ldr))
    ldr.exec_module(cg)

    now = 1_800_000_000.0
    import datetime

    def iso(e):
        return (datetime.datetime.fromtimestamp(e, datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

    def task(tid, status, agent="none", age=7200):
        t = {"id": tid, "title": "synthetic %d" % tid, "status": status,
             "createdAt": iso(now - age)}
        if agent != "none":
            t["agent"] = agent
        return t

    healthy = [task(901, "open", None), task(902, "ready_for_review", None),
               # a freshly dispatched, unlinked task — the live shape
               task(903, cg.IN_PROGRESS, None, age=30)]
    wedged = healthy + [task(904, cg.IN_PROGRESS, None, age=9000)]

    # 🔴 Through the POLLER'S OWN WRITER, not a hand-stamped dict. `attention()`
    # emits no `ts` — `bar-status-poll.source()` adds it — so the block's
    # freshness gate depends on a field the predicate does not produce. That is a
    # seam between two components that are each tested alone, and the shape this
    # repo has been bitten by: hand-stamping `ts` here would test the fixture.
    # `source()` is what actually writes the cache, so drive it.
    h = clawgate_block.render(poll.source("clawgate",
                                          lambda: cg.attention(healthy, now)))
    w = clawgate_block.render(poll.source("clawgate",
                                          lambda: cg.attention(wedged, now)))
    assert h == {"icon": "tasks", "text": "2", "short_text": "2",
                 "state": "Warning"}
    assert w == {"icon": "tasks", "text": "3!1", "short_text": "3!1",
                 "state": "Critical"}


def test_civitai_block_labels_count_distinctly():
    # The civitai block must be visually distinguishable from homelab alerts:
    # its text carries the `civ` label prefix, alerts' does not.
    civ = civitai_block.render(_measured("civitai", count=317, state="Critical"))
    hl = alerts_block.render(_measured("alerts", count=317, state="Critical"))
    assert civ["text"] == "%s civ 317" % civitai_block.ALERT_GLYPH
    assert civ["state"] == "Critical"
    assert hl["text"] == "%s 317" % alerts_block.ALERT_GLYPH
    assert "civ" not in hl["text"]
    assert civ["text"] != hl["text"]


def test_the_two_alert_blocks_stay_distinguishable_when_UNMEASURED_TOO():
    """A `?` that does not say WHICH cluster went unreadable is barely better
    than an invisible pill — the operator has two alert pills and the label is
    the only thing that tells them apart. `civ` must survive onto the `?`.
    """
    civ = civitai_block.render(None)
    hl = alerts_block.render(None)
    assert civ["text"] == "%s civ ?" % _GLYPH
    assert hl["text"] == "%s ?" % _GLYPH
    assert civ["text"] != hl["text"]
    assert "civ" not in hl["text"]


# red_above threshold — neutral at/below the standing backlog, red only above it.
ALERT_BLOCKS = [("alerts", alerts_block), ("civitai", civitai_block)]


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_neutral_at_or_below_baseline(name, mod):
    for count in (25, 30):  # <= red_above=30
        out = mod.render(_measured(name, count=count, state="Critical"),
                         red_above=30)
        assert out["state"] == "Idle"          # visible but NOT coloured
        assert mod.ALERT_GLYPH in out["text"]  # still shown (not hidden)
        assert str(count) in out["text"]


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_colours_when_over_baseline(name, mod):
    out = mod.render(_measured(name, count=31, state="Critical"), red_above=30)
    assert out["state"] == "Critical"          # above the baseline -> red


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_zero_is_backward_compatible(name, mod):
    # default (no threshold) still colours whenever count > 0
    assert mod.render(_measured(name, count=1,
                                state="Critical"))["state"] == "Critical"


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_still_hides_at_zero(name, mod):
    assert mod.render(_measured(name, count=0, state="Idle"), red_above=30) == \
        {"text": "", "state": "Idle"}


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
@pytest.mark.parametrize("red_above", [0, 30, 340, 10_000])
def test_the_backlog_BASELINE_cannot_quieten_an_UNREADABLE_cache(name, mod,
                                                                 red_above):
    """🔴 `red_above` says how many alerts are BORING. It says nothing about a
    reading that does not exist, so it must not reach the `?` pill.

    The tempting mutant is to fold the unmeasured case into the count path,
    where `count = 0 <= red_above` would render `Idle` — the neutral colour —
    and a big enough baseline would neutralise every blackout. `10_000` is here
    because it is nothing like either shipped threshold: a fixture whose value
    equals the constant it tests cannot see the difference.
    """
    out = mod.render(None, red_above=red_above)
    assert out["state"] == "Warning", red_above
    assert out["text"].endswith("?")
    assert out != {"text": "", "state": "Idle"}


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
def test_red_above_arg_parsing(name, mod, monkeypatch):
    monkeypatch.setattr(sys, "argv", [name, "--red-above", "42"])
    assert mod._red_above_arg() == 42
    monkeypatch.setattr(sys, "argv", [name])            # absent -> 0
    assert mod._red_above_arg() == 0
    monkeypatch.setattr(sys, "argv", [name, "--red-above", "nan"])  # junk -> 0
    assert mod._red_above_arg() == 0


# --------------------------------------------------------------------------- #
# 🔴 THE DEFECT, ASSERTED ACROSS THE WHOLE BAR: a cache that could not be read
# never renders as a cache that was read and said nothing.
#
# There used to be a `HIDES_WHEN_UNREADABLE = [b for b in BLOCKS if b[0] !=
# "clawgate"]` here, and three parametrizations pinning `{"text": "", "state":
# "Idle"}` — the MEASURED all-clear pill — as the correct rendering for a stale
# marker, an error marker, a missing file and a corrupt one. That list was the
# defect written down as an expectation. It is replaced by its own negation,
# widened from the four count blocks to all SEVEN cache-backed blocks.
#
# ⚠ LABEL, honestly: for `clawgate`, `media` and `airvpn` these are INVARIANT
# GUARDS — those three already discriminated on the MARKER paths, and stay green
# on the pre-change tree for the marker/missing rows. They are REGRESSION
# coverage for `mail`, `alerts`, `civitai` and `telemetry`, and for the FROZEN
# row (`test_a_cache_the_poller_STOPPED_REWRITING_*`) they are regression
# coverage for all seven but clawgate. The red-at-base matrix is in the PR.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
def test_a_poller_STALE_marker_is_not_a_measured_all_clear(name, mod):
    """`bar-status-poll.stale()` is written when the SOURCE did not answer — the
    kubeconfig, the port-forward, Alertmanager, clawgate, qBit. That is a
    statement about our ability to look, never about what is there."""
    out = mod.render(_aged(0, count=0, state="stale", detail="unreachable"),
                     now=FIXED_NOW)
    assert out == UNMEASURED_PILL[name], out
    assert out != MEASURED_ALL_CLEAR_PILL[name]


@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
def test_an_ERROR_marker_is_not_a_measured_all_clear(name, mod):
    # `count: 0` because that is what `stale()` actually writes — the count on a
    # marker is not a reading. A marker that still CARRIES a number is a
    # different claim (the alarm must survive the outage) and has its own tests:
    # `test_a_clawgate_OUTAGE_carries_the_LAST_KNOWN_stuck_count_forward` and
    # `test_a_FROZEN_telemetry_cache_KEEPS_the_dead_sources_it_last_counted`.
    out = mod.render(_aged(0, count=0, state="Warning", error="boom"),
                     now=FIXED_NOW)
    assert out == UNMEASURED_PILL[name], out
    assert out != MEASURED_ALL_CLEAR_PILL[name]


@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
@pytest.mark.parametrize("bad", [
    None,             # no file at all, or one `load()` could not parse
    [],               # valid JSON, wrong shape — and FALSY
    ["a", "list"],    # ...and a TRUTHY one
    {},               # a dict that says nothing at all
    "x",              # a bare JSON string (truthy)
    "",               # ...and a falsy one
    3,                # a bare JSON number (truthy)
    0,                # ...and a falsy one
    True,             # a bare JSON bool — an int in Python
    2.5,              # a bare JSON float
])
def test_a_MISSING_or_corrupt_cache_is_not_a_measured_all_clear(name, mod, bad):
    """The truthy non-dicts are the load-bearing rows: any `payload.get(...)`
    a block reaches before its type check raises on them, and `__main__` would
    swallow that into a pill indistinguishable from a correct one."""
    out = mod.render(bad, now=FIXED_NOW)          # must not raise
    assert out == UNMEASURED_PILL[name], (name, bad, out)
    assert out != MEASURED_ALL_CLEAR_PILL[name]


@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
def test_a_cache_the_poller_STOPPED_REWRITING_is_not_a_current_reading(name, mod):
    """🔴 THE HALF NO BLOCK BUT clawgate HAD AT ALL. Not one of these files read
    the `ts` that `bar-status-poll.source()` stamps on every payload, so a dead
    poller did not make the bar go quiet — it FROZE it, and every pill went on
    presenting its last reading as a present-tense fact.

    Both directions are asserted against the SAME payload, so this cannot pass
    against a gate that is merely present and always-true: fresh renders the
    quiet reading, a day old renders the `?`.
    """
    fresh_out = mod.render(_quiet_payload(name, age_secs=0), now=FIXED_NOW)
    frozen_out = mod.render(_quiet_payload(name, age_secs=86_400), now=FIXED_NOW)
    assert fresh_out == MEASURED_ALL_CLEAR_PILL[name], fresh_out
    assert frozen_out == UNMEASURED_PILL[name], frozen_out
    assert fresh_out != frozen_out


# --------------------------------------------------------------------------- #
# 🔴 THE OPERATOR'S DECISION, ACROSS THE WHOLE BAR: an outage may make a reading
# less TRUSTED; it may never make a recorded alarm QUIETER.
#
# The first version of this change got the freshness gate right and then threw
# the reading away: a frozen cache still holding `39` firing alerts rendered a
# bare `󰀪 ?`/Warning, a client-prod `146` rendered `󰀪 civ ?`, a firewalled qBit
# tunnel rendered `qBit?`, and a recorded `LEAK` — the one condition the
# killswitch exists to make loud — rendered a soft-yellow icon. Four downgrades,
# each defended by "cannot tell is only ever a Warning".
#
# The rule is now: alerts do not resolve because the poller died, the trailing
# `?` already marks the number as not-currently-measured, and the cost is
# asymmetric — a false-quiet on a leak is far worse than a false-loud. So a
# recorded alarm is CARRIED, marked. A MEASURED quiet board still hides.
# --------------------------------------------------------------------------- #
#: How loud each i3status-rust state is. Used to assert a RELATIONSHIP (the
#: frozen pill is never quieter than the live one) rather than a spelling.
_LOUDNESS = {"Idle": 0, "Good": 0, "Info": 1, "Warning": 2, "Critical": 3}

#: name -> (the LOUDEST realistic cache payload in the shape `bar-status-poll`
#: writes, the pill it renders while MEASURED, the pill it must render once the
#: poller has STOPPED REWRITING it). Every pill is a LITERAL, read off the
#: operator's decision and typed out here — none is computed from the module, so
#: this table can disagree with the code rather than restate it.
ALARM_CACHES = {
    "clawgate": (
        {"count": 22, "stuck_count": 2, "state": "Critical"},
        {"icon": "tasks", "text": "22!2", "short_text": "22!2",
         "state": "Critical"},
        # ⚠ clawgate carries only the STUCK half: its count is the EXPECTED
        # steady state, not an alarm. The other six carry their count because
        # for them a non-zero count IS the alarm.
        {"icon": "tasks", "text": "!2?", "short_text": "!2?",
         "state": "Critical"}),
    "mail": (
        {"count": 5, "state": "Warning"},
        {"icon": "mail", "text": "5", "short_text": "5", "state": "Warning"},
        {"icon": "mail", "text": "5?", "short_text": "5?", "state": "Warning"}),
    "alerts": (
        {"count": 39, "state": "Critical"},
        {"text": _GLYPH + " 39", "short_text": _GLYPH + " 39",
         "state": "Critical"},
        {"text": _GLYPH + " 39?", "short_text": _GLYPH + " 39?",
         "state": "Critical"}),
    "civitai": (
        {"count": 146, "state": "Critical"},
        {"text": _GLYPH + " civ 146", "short_text": _GLYPH + " civ 146",
         "state": "Critical"},
        {"text": _GLYPH + " civ 146?", "short_text": _GLYPH + " civ 146?",
         "state": "Critical"}),
    "media": (
        # what `parse_media` writes for connection_status == "firewalled" — the
        # forwarded port is down, which is the regression AirVPN was chosen for
        {"icon": "net_down", "text": "CA ⚠ firewalled", "short_text": "CA !",
         "state": "Critical"},
        {"icon": "net_down", "text": "CA ⚠ firewalled", "short_text": "CA !",
         "state": "Critical"},
        {"icon": "net_down", "text": "CA ⚠ firewalled?", "short_text": "CA !?",
         "state": "Critical"}),
    "airvpn": (
        {"up": True, "verdict": "leak", "country_code": "CA"},
        {"icon": "net_vpn", "text": "LEAK", "short_text": "LEAK",
         "state": "Critical"},
        {"icon": "net_vpn", "text": "LEAK?", "short_text": "LEAK?",
         "state": "Critical"}),
    "telemetry": (
        {"count": 3, "state": "Critical"},
        {"text": "tlm 3", "short_text": "tlm 3", "state": "Critical"},
        {"text": "tlm 3?", "short_text": "tlm 3?", "state": "Critical"}),
}
assert sorted(ALARM_CACHES) == sorted(n for n, _ in ALL_BLOCKS)


@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
def test_a_measurement_OUTAGE_never_makes_a_KNOWN_alarm_QUIETER_in_ANY_block(
        name, mod):
    """🔴 THE OPERATOR'S RULE, one block at a time, on the FROZEN-FILE path.

    Both directions come from the SAME payload, so this cannot pass against a
    renderer that marks everything, or one that marks nothing: fresh renders the
    alarm plainly, a day old renders the same alarm MARKED.
    """
    payload, measured_pill, frozen_pill = ALARM_CACHES[name]
    live = mod.render(_aged(0, **payload), now=FIXED_NOW)
    frozen = mod.render(_aged(86_400, **payload), now=FIXED_NOW)
    assert live == measured_pill, (name, live)
    assert frozen == frozen_pill, (name, frozen)
    # ...and the three properties the literals above are an instance of:
    assert frozen["text"].endswith("?"), name        # marked as not current
    assert _LOUDNESS[frozen["state"]] >= _LOUDNESS[live["state"]], name
    assert frozen != UNMEASURED_PILL[name], name     # not the bare `?`
    assert frozen != MEASURED_ALL_CLEAR_PILL[name], name


@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
def test_a_MEASURED_quiet_board_still_HIDES_when_frozen(name, mod):
    """The other half of the decision, and the reason it is not "always show the
    last reading": a cache that recorded NOTHING has no alarm to carry, so it
    falls through to the bare `?` — visible, but not pretending to a number."""
    frozen_quiet = mod.render(_quiet_payload(name, age_secs=86_400),
                              now=FIXED_NOW)
    assert frozen_quiet == UNMEASURED_PILL[name], (name, frozen_quiet)
    _payload, _measured, frozen_alarm = ALARM_CACHES[name]
    assert frozen_quiet != frozen_alarm, name


@pytest.mark.parametrize("name,mod", ALERT_BLOCKS)
@pytest.mark.parametrize("red_above", [0, 30, 340, 10_000])
def test_the_backlog_BASELINE_cannot_ERASE_a_CARRIED_count(name, mod, red_above):
    """`red_above` may still say a CARRIED count is boring — it may not delete
    it, and it may not return the pill to a neutral colour it no longer earns.

    `10_000` is nothing like either shipped threshold, and 39 is nothing like
    either: a fixture whose value equals the constant it tests cannot see the
    difference.
    """
    out = mod.render(_aged(86_400, count=39, state="Critical"),
                     red_above=red_above, now=FIXED_NOW)
    assert "39" in out["text"], (name, red_above)
    assert out["text"].endswith("?"), (name, red_above)
    assert out["state"] in ("Warning", "Critical"), (name, red_above)


@pytest.mark.parametrize("name,mod", [("media", media_block),
                                      ("airvpn", airvpn_block)])
def test_the_STATE_blocks_carry_an_ALARM_but_not_a_NEUTRAL_reading(name, mod):
    """⚠ THE ONE DELIBERATE ASYMMETRY, pinned so it is a decision and not a gap.

    The count blocks carry any non-zero count. `media` and `airvpn` carry only a
    LOUD reading (`bar_freshness.is_loud`), because their neutral readings are
    not alarms — day-old transfer speeds are noise, and `airvpn` has already
    spent the `?` character on a different meaning (`CA?` = "up, exit
    unverified"), so carrying `CA` forward as `CA?` would collide with a
    MEASURED state. Both fall back to a Warning pill, so nothing gets quieter.
    """
    neutral = {"media": {"icon": "net_down", "text": "CA ↓0B ↑2.9M",
                         "short_text": "↓0B ↑2.9M", "state": "Idle"},
               "airvpn": {"up": True, "verdict": "verified",
                          "country_code": "CA"}}[name]
    frozen = mod.render(_aged(86_400, **neutral), now=FIXED_NOW)
    assert frozen == UNMEASURED_PILL[name], (name, frozen)
    # ...while the LOUD reading of the same block IS carried
    payload, _measured, frozen_alarm = ALARM_CACHES[name]
    assert mod.render(_aged(86_400, **payload), now=FIXED_NOW) == frozen_alarm


#: The blocks that render a COUNT (media/airvpn are state pills and have no
#: count branch at all). Derived by SUBTRACTION from the registry, so a new count
#: block joins automatically rather than silently escaping the guard below.
COUNT_BLOCKS = [b for b in ALL_BLOCKS if b[0] not in ("media", "airvpn")]
assert len(COUNT_BLOCKS) == len(ALL_BLOCKS) - 2


@pytest.mark.parametrize("name,mod", COUNT_BLOCKS)
@pytest.mark.parametrize("junk", ["NaN", "2", 2.5, True, False, None, [], {}])
def test_a_count_ANY_block_REFUSES_TO_READ_is_not_a_count_of_zero(name, mod,
                                                                  junk):
    """🔴 FOUND BY THE MUTATION SWEEP, NOT BY REVIEW. `if count is None: return
    <unmeasured>` could be swapped for `return dict(_EMPTY)` in `i3status-mail`
    and `i3status-telemetry` and the ENTIRE suite stayed green — a CURRENT cache
    whose `count` is unreadable would silently revert to the invisible pill,
    which is the exact substitution this change exists to forbid, on a payload
    the freshness gate says IS a present-tense reading. clawgate had this test;
    the blocks it was copied to did not, and the copy is what the sweep caught.

    `"2"` is in the list deliberately: `int()` would coerce it happily. The
    poller writes ints, so a string count means the WRITER changed, and a block
    that quietly agrees with a writer it no longer recognises is how a pill stops
    meaning what it says. `True`/`False` are here because a bool is an int in
    Python, so a coercing check fabricates a 1 or a 0.
    """
    payload = _aged(0, count=junk, state="Warning")
    if name == "clawgate":
        payload["stuck_count"] = 0
    # the payload must reach the COUNT branch, not stop at the freshness gate
    assert freshness.unmeasured(payload, FIXED_NOW) is False
    out = mod.render(payload, now=FIXED_NOW)
    assert out == UNMEASURED_PILL[name], (name, junk, out)
    assert out != MEASURED_ALL_CLEAR_PILL[name], (name, junk)


@pytest.mark.parametrize("name,mod", ALL_BLOCKS)
def test_every_block_render_is_FAILSAFE_when_called_DIRECTLY(name, mod):
    """🔴 THE HALF A SUBPROCESS CANNOT MEASURE, for every block at once.

    Each block's `__main__` catches every exception and prints a pill that is
    BYTE-IDENTICAL to the one a correct `render` produces for an unreadable
    cache. So `render` replaced wholesale by `raise RuntimeError` leaves every
    end-to-end test in this file green — measured on clawgate, where five
    parametrizations stayed green under exactly that mutant. Only a direct call
    can tell a computed answer from a swallowed crash, which is what makes each
    module's "it never raises, whatever the file contains" a tested claim.
    """
    for payload in (None, [], ["a"], {}, "x", 3, True, 2.5,
                    {"count": "NaN"}, {"ts": "nope"}, {"ts": None},
                    _aged(0, count=1, state="Warning"),
                    _aged(10**9, count=1, state="Warning")):
        out = mod.render(payload, now=FIXED_NOW)      # must not raise
        assert isinstance(out, dict) and "state" in out, (name, payload)


@pytest.mark.parametrize("name,mod,icon,default_state", BLOCKS)
def test_block_defaults_state_when_missing_or_idle(name, mod, icon, default_state):
    # A positive count with a missing/Idle state must still colour (never neutral).
    #
    # 🔴 `_measured` IS LOAD-BEARING HERE, and this test passed for the wrong
    # reason without it. A bare `{"count": 1}` carries no `ts`, so clawgate
    # rendered the UNKNOWN pill — whose state is also `Warning`, which is also
    # clawgate's `default_state`. The assertion was green while measuring a
    # completely different code path from the one it names.
    out = mod.render(_measured(name, count=1))
    assert out["state"] == default_state
    assert out["text"] != "?", "measured payload rendered as unmeasured"
    out2 = mod.render(_measured(name, count=1, state="Idle"))
    assert out2["state"] == default_state


# --------------------------------------------------------------------------- #
# block scripts: end-to-end subprocess against a fixture cache dir
# --------------------------------------------------------------------------- #
# ⚠ `i3status-clawgate` USED to be absent from the two "…_is_invisible"
# parametrizations below, because it was the only block whose no-file /
# corrupt-file rendering was a VISIBLE `?`. That is now every block's rendering,
# so the parametrization is the full list and the assertion is inverted.
#
# ⚠ AND THESE CANNOT SEE A CRASH. They drive each block as a SUBPROCESS, and
# every `__main__` catches all exceptions and prints a BYTE-IDENTICAL unmeasured
# pill. They prove the PIXEL; `test_every_block_render_is_FAILSAFE_when_called_
# DIRECTLY` is the other half and neither is sufficient alone.
BLOCK_SCRIPTS = [
    ("i3status-clawgate", "clawgate.json", "clawgate"),
    ("i3status-mail", "mail.json", "mail"),
    ("i3status-alerts", "alerts.json", "alerts"),
    ("i3status-civitai", "civitai.json", "civitai"),
    ("i3status-media", "media.json", "media"),
    ("i3status-airvpn", "airvpn.json", "airvpn"),
    ("i3status-telemetry", "telemetry.json", "telemetry"),
]
# 🔴 Pinned two-way against the block registry, so a block added to one and not
# the other is a failure rather than a silent hole. Both lists are hand-written
# on purpose — deriving one from the other would make this assertion vacuous.
assert [n for _, _, n in BLOCK_SCRIPTS] == [n for n, _ in ALL_BLOCKS]


@pytest.mark.parametrize("script,cachefile,name", BLOCK_SCRIPTS)
def test_block_subprocess_missing_file_is_VISIBLE(tmp_path, script, cachefile,
                                                  name):
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == UNMEASURED_PILL[name], (name, out)
    assert out != MEASURED_ALL_CLEAR_PILL[name]


# icon=None => alert blocks (glyph in text, no `icon` field); the given `text`
# is the trailing (glyph-stripped) portion the rendered text must end with.
@pytest.mark.parametrize("script,cachefile,icon,text", [
    ("i3status-clawgate", "clawgate.json", "tasks", "2"),
    ("i3status-mail", "mail.json", "mail", "2"),
    ("i3status-alerts", "alerts.json", None, "2"),
    ("i3status-civitai", "civitai.json", None, "civ 2"),
])
def test_block_subprocess_positive_renders(tmp_path, script, cachefile, icon, text):
    (tmp_path / cachefile).write_text(json.dumps(_measured(
        cachefile.removesuffix(".json"),
        count=2, state="Warning", detail="x")))
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


@pytest.mark.parametrize("script,cachefile,name", BLOCK_SCRIPTS)
def test_block_subprocess_corrupt_json_is_VISIBLE(tmp_path, script, cachefile,
                                                  name):
    (tmp_path / cachefile).write_text("{ this is not json ")
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == UNMEASURED_PILL[name], (name, out)
    assert out != MEASURED_ALL_CLEAR_PILL[name]


@pytest.mark.parametrize("script,cachefile,name", BLOCK_SCRIPTS)
def test_block_subprocess_FROZEN_cache_is_VISIBLE(tmp_path, script, cachefile,
                                                  name):
    """🔴 THE DEFECT, END TO END AND THROUGH THE REAL CLOCK. The pure-function
    tests inject `now`; nothing they do can catch a `main()` that drops it or a
    `render` whose default `now` is wrong. Here the cache is genuinely stamped a
    day in the past and the block reads its own wall clock."""
    payload = _quiet_payload(name, age_secs=0)
    payload["ts"] = int(time.time()) - 86_400
    (tmp_path / cachefile).write_text(json.dumps(payload))
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == UNMEASURED_PILL[name], (name, out)
    assert out != MEASURED_ALL_CLEAR_PILL[name]


@pytest.mark.parametrize("script,cachefile,name", BLOCK_SCRIPTS)
def test_block_subprocess_FRESH_quiet_cache_stays_QUIET(tmp_path, script,
                                                        cachefile, name):
    """🔴 THE OTHER DIRECTION, and the one that keeps this change from being a
    regression: a healthy, quiet bar must stay quiet. A fix that made every pill
    visible would pass every assertion above and ruin the calm bar."""
    payload = _quiet_payload(name, age_secs=0)
    payload["ts"] = int(time.time())
    (tmp_path / cachefile).write_text(json.dumps(payload))
    env = dict(os.environ, BAR_STATUS_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(SCRIPTS / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == MEASURED_ALL_CLEAR_PILL[name], (name, out)
    assert out != UNMEASURED_PILL[name]


@pytest.mark.parametrize("script,cachefile,name", BLOCK_SCRIPTS)
def test_a_block_that_cannot_load_the_SIBLING_renders_the_VISIBLE_pill(
        tmp_path, script, cachefile, name):
    """🔴 AN ADVERTISED FALLBACK THAT COULD NOT FIRE, now measured rather than
    documented. Seven docstrings, six `__main__` comments, `nix/graphical.nix`
    and the `bar` skill all claimed a block that cannot load `bar_freshness.py`
    "falls through to its `?` pill". It could not: `fresh = _load_freshness()`
    ran BARE at module level, outside `__main__`'s `try`, so the block died with
    a `FileNotFoundError`, exit 1 and EMPTY stdout — never the pill. An
    unreachable guard reads as protection while providing none.

    The load is now DEFERRED (`except: fresh = None`), and this drives the case
    the documents describe: the block ALONE in a directory with no sibling,
    against a cache that is FRESH AND QUIET — the payload a permissive
    degradation would render as the invisible all-clear.

    It kills three mutants at once, which is why it is one test:
      * `__main__`'s fallback reverted to the invisible pill (or `_OFF`)
        -> the assertion below sees the quiet pill;
      * `_load_freshness` degraded to a permissive stub (`unmeasured -> False`)
        -> same, and that is the whole defect reinstated;
      * the deferral removed (back to a bare module-level load)
        -> exit 1 and unparseable stdout.

    The POSITIVE CONTROL is in the same test: the identical cache, with the
    sibling present, must render the QUIET pill. Without it a block hard-wired
    to `?` would pass.
    """
    alone = tmp_path / "no-sibling"
    alone.mkdir()
    shutil.copy(SCRIPTS / script, alone / script)
    assert not (alone / "bar_freshness.py").exists()

    cache = tmp_path / "cache"
    cache.mkdir()
    payload = _quiet_payload(name, age_secs=0)
    payload["ts"] = int(time.time())
    (cache / cachefile).write_text(json.dumps(payload))
    env = dict(os.environ, BAR_STATUS_DIR=str(cache))

    r = subprocess.run([sys.executable, str(alone / script)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (name, r.returncode, r.stderr)
    out = json.loads(r.stdout)
    assert out == UNMEASURED_PILL[name], (name, out)
    assert out != MEASURED_ALL_CLEAR_PILL[name], name

    # POSITIVE CONTROL: the same script, the same cache, sibling present.
    shutil.copy(SCRIPTS / "bar_freshness.py", alone / "bar_freshness.py")
    r2 = subprocess.run([sys.executable, str(alone / script)],
                        capture_output=True, text=True, env=env)
    assert r2.returncode == 0, (name, r2.stderr)
    assert json.loads(r2.stdout) == MEASURED_ALL_CLEAR_PILL[name], name


# --------------------------------------------------------------------------- #
# 🔴 The clawgate block END TO END: an unreadable board is VISIBLE.
#
# ⚠ THESE CANNOT SEE A CRASH. They drive the block as a SUBPROCESS, and
# `__main__`'s `except` prints a BYTE-IDENTICAL `?` pill — so this whole group
# passes with the entire body of `render()` replaced by `raise RuntimeError`
# (measured: 5/5 parametrizations still green). What they prove is the pixel the
# operator sees; what they CANNOT prove is that the block computed it. The pure
# `render()` assertions above (`test_the_clawgate_render_FUNCTION_*`) are the
# other half of that pair, and neither is sufficient alone.
# --------------------------------------------------------------------------- #
def _run_clawgate_block(cache_dir):
    env = dict(os.environ, BAR_STATUS_DIR=str(cache_dir))
    r = subprocess.run([sys.executable, str(SCRIPTS / "i3status-clawgate")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("body", [
    None,                                  # no file at all
    "{ this is not json ",                 # corrupt
    '["a", "list"]',                       # valid JSON, wrong shape
    '{"state": "stale", "count": 0, "ts": 1786759794}',
    '{"error": "clawgate unavailable", "count": 0, "ts": 1786759794}',
])
def test_the_clawgate_block_renders_an_UNREADABLE_board_VISIBLY(tmp_path, body):
    """🔴 THE MEASURED DEFECT (2026-08-14, against the shipped block). Every one
    of these rendered `{"text": "", "state": "Idle"}` — byte-identical to the
    hide-at-zero all-clear for an empty board. The pill therefore went dark in
    precisely the two situations where a wedged dispatch is least visible
    anywhere else: clawgate unreachable (the poller writes `stale`) and the
    poller itself dead (no file, or a file nobody can parse).

    This block is the only surface for a stuck dispatch that nothing can
    dismiss — the critical toast is one-shot and dies with dunst, the
    notification badge clears on `seen`, `session-manager` is on-demand — so
    "cannot tell" and "nothing to tell" must not be the same pixels.
    """
    if body is not None:
        (tmp_path / "clawgate.json").write_text(body)
    out = _run_clawgate_block(tmp_path)
    assert out == UNKNOWN_PILL
    assert out != {"text": "", "state": "Idle"}


def test_the_clawgate_block_end_to_end_positive_control(tmp_path):
    """The POSITIVE CONTROL for the pair above: the same subprocess, the same
    cache path, a payload that IS readable — so a `?` from that test is evidence
    about the payload and not about a block wired to nothing.

    Three readable payloads, three different renderings.
    """
    path = tmp_path / "clawgate.json"
    now = int(time.time())

    path.write_text(json.dumps(
        {"count": 0, "stuck_count": 0, "state": "Idle", "ts": now}))
    empty_board = _run_clawgate_block(tmp_path)

    path.write_text(json.dumps(
        {"count": 22, "stuck_count": 0, "state": "Warning", "ts": now}))
    backlog = _run_clawgate_block(tmp_path)

    path.write_text(json.dumps(
        {"count": 24, "stuck_count": 2, "state": "Critical", "ts": now}))
    wedged = _run_clawgate_block(tmp_path)

    assert empty_board == {"text": "", "state": "Idle"}
    assert backlog["text"] == "22" and backlog["state"] == "Warning"
    assert wedged["text"] == "24!2" and wedged["state"] == "Critical"
    # …and none of the three is the unreadable pill, which is what makes the
    # `?` above a measurement rather than this block's only trick.
    assert UNKNOWN_PILL not in (empty_board, backlog, wedged)


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


# --------------------------------------------------------------------------- #
# 🔴 THE STUCK TOAST, and why it needs a latch of its OWN.
# --------------------------------------------------------------------------- #
STUCK_SPEC_NAME = "clawgate_stuck"


def _both_specs():
    s = poll._toast_specs()
    return s["clawgate"], s[STUCK_SPEC_NAME]


def test_the_stuck_toast_FIRES_WHILE_THE_COUNT_LATCH_IS_ALREADY_SET():
    """🔴 THE MEASURED DEFECT. The clawgate toast uses threshold 0 with a LEVEL
    latch, and the live board sits permanently above zero (13 when this was
    written), so its latch is ALWAYS set. A dispatch wedging is then a 13 -> 14
    increment on an already-latched source: no toast, same colour on the bar,
    nothing anywhere. The one event worth interrupting for could not interrupt.

    So the stuck toast gets its own latch on its own number. This drives BOTH
    specs over the same payload with the count latch already set.
    """
    cg_spec, stuck_spec = _both_specs()
    latches = {"clawgate": True, STUCK_SPEC_NAME: False}   # count already latched
    fired = []
    rd = lambda n: latches[n]                                  # noqa: E731
    wr = lambda n, v: latches.__setitem__(n, v)                # noqa: E731
    fr = lambda *a, **k: fired.append(a[1])                    # noqa: E731

    payload = {"count": 14, "stuck_count": 1, "state": "Critical",
               "detail": "14 need you (12 open, 1 review, 1 stuck)"}
    # the ordinary clawgate toast stays quiet — steady state, correctly
    assert poll.evaluate_edge_toast("clawgate", payload, cg_spec,
                                    fire=fr, read=rd, write=wr) == (False, True)
    # …and the stuck toast fires anyway. THIS is the independence.
    assert poll.evaluate_edge_toast(STUCK_SPEC_NAME, payload, stuck_spec,
                                    fire=fr, read=rd, write=wr) == (True, True)
    assert len(fired) == 1 and "STUCK" in fired[0]
    assert latches == {"clawgate": True, STUCK_SPEC_NAME: True}


def test_the_two_clawgate_latches_are_SEPARATE_FILES():
    # Structural: one latch path per spec name. Sharing a file would recreate the
    # coupling this exists to break, and no behavioural test of the pure decision
    # function would notice.
    assert poll._latch_path("clawgate") != poll._latch_path(STUCK_SPEC_NAME)


def test_the_stuck_toast_does_not_refire_while_the_wedge_persists():
    _, stuck_spec = _both_specs()
    state, fired, rd, wr, fr = _latch_store(initial=True)
    out = poll.evaluate_edge_toast(STUCK_SPEC_NAME,
                                   {"count": 9, "stuck_count": 2,
                                    "state": "Critical"},
                                   stuck_spec, fire=fr, read=rd, write=wr)
    assert out == (False, True) and fired == []


def test_the_stuck_latch_clears_when_the_wedge_does_and_can_refire():
    _, stuck_spec = _both_specs()
    state, fired, rd, wr, fr = _latch_store(initial=True)
    # resolved: stuck back to a MEASURED zero -> latch clears, no toast
    assert poll.evaluate_edge_toast(
        STUCK_SPEC_NAME, {"count": 9, "stuck_count": 0, "state": "Warning"},
        stuck_spec, fire=fr, read=rd, write=wr) == (False, False)
    assert fired == []
    # a NEW wedge then re-fires, even though `count` never moved
    assert poll.evaluate_edge_toast(
        STUCK_SPEC_NAME, {"count": 9, "stuck_count": 1, "state": "Critical"},
        stuck_spec, fire=fr, read=rd, write=wr) == (True, True)
    assert len(fired) == 1


def test_an_ABSENT_stuck_count_SKIPS_rather_than_clearing_the_latch():
    """🔴 An absent key is NOT a measured zero. A cache from a poller predating
    the stuck predicate carries no `stuck_count`; reading that as 0 would clear
    the latch and re-toast a still-wedged dispatch the moment a current poller
    wrote a real number again."""
    _, stuck_spec = _both_specs()
    state, fired, rd, wr, fr = _latch_store(initial=True)
    out = poll.evaluate_edge_toast(STUCK_SPEC_NAME,
                                   {"count": 9, "state": "Warning"},
                                   stuck_spec, fire=fr, read=rd, write=wr)
    assert out is None
    assert fired == [] and state["latched"] is True    # untouched


@pytest.mark.parametrize("bad", [None, "NaN", [], {}, "x"])
def test_a_MALFORMED_stuck_count_also_skips(bad):
    # ⚠ SCOPE, stated because the two surfaces differ on purpose: the POLLER
    # keeps its pre-existing `int()` coercion, so a numeric STRING ("2") is
    # accepted here, while the BLOCK's renderer is strict and shows `?` for it.
    # The poller is the write path and this leniency predates the stuck predicate
    # — it is not widened here, and nothing in the pipeline produces a string
    # count. Non-numeric and absent values skip on BOTH, which is the property
    # that matters: a latch is never cleared by something nobody measured.
    _, stuck_spec = _both_specs()
    state, fired, rd, wr, fr = _latch_store(initial=True)
    assert poll.evaluate_edge_toast(
        STUCK_SPEC_NAME, {"count": 9, "stuck_count": bad, "state": "Warning"},
        stuck_spec, fire=fr, read=rd, write=wr) is None
    assert fired == [] and state["latched"] is True


@pytest.mark.parametrize("marker", [
    {"count": 0, "state": "stale", "detail": "clawgate unavailable"},
    {"count": 0, "error": "clawgate unavailable"},
])
def test_a_CARRIED_stuck_count_CANNOT_fire_or_clear_a_toast_latch(marker):
    """🔴 THE SEAM `carry_stuck_forward` OPENS, closed here. It puts a
    `stuck_count` onto a payload that never had one — a shape the rising-edge
    gate had never seen — and that number is NOT a measurement. If it reached the
    gate, a wedge riding out a clawgate outage would re-toast a critical
    notification every 45s for as long as the outage lasted, latch or no latch.

    `evaluate_edge_toast` returns before reading any count for a stale/error
    payload, so the property holds by construction; this pins it, because the
    construction is one `return None` away from not holding.
    """
    _, stuck_spec = _both_specs()
    carried = poll.carry_stuck_forward(dict(marker), {"stuck_count": 4})
    assert carried["stuck_count"] == 4, "fixture did not reach the carry"
    for latched in (True, False):
        state, fired, rd, wr, fr = _latch_store(initial=latched)
        assert poll.evaluate_edge_toast(STUCK_SPEC_NAME, carried, stuck_spec,
                                        fire=fr, read=rd, write=wr) is None
        assert fired == [] and state["latched"] is latched


def test_the_stuck_toast_is_LOUDER_than_the_ordinary_pending_one():
    cg_spec, stuck_spec = _both_specs()
    assert cg_spec["urgency"] == "normal"
    assert stuck_spec["urgency"] == "critical"
    assert stuck_spec["summary"] != cg_spec["summary"]
    assert stuck_spec["count_key"] == "stuck_count"
    assert cg_spec.get("count_key", "count") == "count"


def test_BOTH_clawgate_toasts_reach_the_dispatch_loop_from_ONE_payload(
        monkeypatch):
    """🔴 THE SEAM. `_dispatch_all` iterates POLLED SOURCES, and there is no
    `clawgate_stuck` poller — a derived spec that nothing dispatches is a toast
    that can never fire, and every unit test of the decision function above would
    still pass. This drives the real loop with the real spec table.
    """
    seen = {}

    def fake_eval(name, payload, spec, **kw):
        seen[name] = (payload, spec)
        return (True, True)

    monkeypatch.setattr(poll, "evaluate_edge_toast", fake_eval)
    payload = {"count": 4, "stuck_count": 1, "state": "Critical", "detail": "d"}
    poll._dispatch_all([("clawgate", payload)])

    assert sorted(seen) == ["clawgate", STUCK_SPEC_NAME], sorted(seen)
    # Both read the SAME payload…
    assert seen["clawgate"][0] is seen[STUCK_SPEC_NAME][0] is payload
    # …and gate on DIFFERENT numbers in it.
    assert seen[STUCK_SPEC_NAME][1]["count_key"] == "stuck_count"
    assert seen["clawgate"][1].get("count_key", "count") == "count"


def test_a_derived_spec_is_NOT_dispatched_when_its_source_did_not_poll(
        monkeypatch):
    # A source that failed to poll produces no payload for the loop; the derived
    # toast must not be dispatched against nothing.
    fired = []
    monkeypatch.setattr(poll, "evaluate_edge_toast",
                        lambda name, *a, **k: fired.append(name))
    poll._dispatch_all([("mail", {"count": 1, "state": "Warning"})])
    assert fired == ["mail"], fired


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


def _stamp(payload):
    """Stamp a `parse_*` result the way `bar-status-poll.source()` does.

    🔴 `parse_telemetry` returns FACTS; `source()` is what puts the integer `ts`
    on them before `write_status` writes the file a block reads. The block now
    refuses to present an unstamped payload as a current reading, so a test that
    renders a bare `parse_*` result is rendering something no poller ever wrote.
    An INT, because `int_or_none` will not coerce a float.
    """
    payload["ts"] = int(time.time())
    return payload


def test_parse_telemetry_clean_is_idle_and_invisible():
    out = poll.parse_telemetry(_verdict(count=0, detail="17 source(s) fresh"),
                               now=1000)
    assert out["count"] == 0 and out["state"] == "Idle"
    assert out["unknown"] is False
    assert telemetry_block.render(_stamp(out)) == {"text": "", "state": "Idle"}


def test_parse_telemetry_dead_source_is_critical_and_visible():
    """POSITIVE CONTROL for the pair above — same path, non-zero count."""
    out = poll.parse_telemetry(
        _verdict(count=2, detail="workbench/opencode silent 19.0h active"),
        now=1000)
    assert out["count"] == 2 and out["state"] == "Critical"
    blk = telemetry_block.render(_stamp(out))
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
    blk = telemetry_block.render(_stamp(out))
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
    assert telemetry_block.render(_stamp(neg))["text"] == "", neg
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
    # 🔴 Every toast spec gates on a payload something actually POLLS. A spec is
    # no longer 1:1 with a source — `clawgate_stuck` is a second, independently
    # latched toast over the clawgate payload — so the relationship to assert is
    # "the payload this spec reads comes from a polled source", via `source`.
    # The previous `set(specs) <= set(names)` spelled that as "every spec name IS
    # a source name", which a derived spec cannot satisfy however correct it is.
    specs = poll._toast_specs()
    for spec_name, spec in specs.items():
        assert spec.get("source", spec_name) in names, \
            "toast spec %r reads a payload nothing polls" % spec_name
    # …and the derived specs are a pinned LEDGER of their own: one appearing
    # unreviewed is a toast nobody decided to fire.
    derived = {n: s["source"] for n, s in specs.items() if s.get("source")}
    assert derived == {"clawgate_stuck": "clawgate"}, derived
    # A derived spec must gate on a DIFFERENT number than its source's own spec,
    # or it is a duplicate toast that fires on the same edge.
    for spec_name, src in derived.items():
        assert specs[spec_name].get("count_key") != \
            specs[src].get("count_key", "count"), spec_name


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
    #
    # 🔴 THE GRACE PERIOD IS THE POLLER'S, AND IT IS NOT THE FRESHNESS GATE.
    # The payload is STAMPED here, so it is a CURRENT cache written by a LIVING
    # poller that merely could not evaluate — which is exactly the distinction
    # `bar_freshness.MAX_CACHE_AGE_SECS` (600s, "is the writer alive?") and
    # `TELEMETRY_UNKNOWN_GRACE` (1800s, "how long has a living writer been
    # unable to tell?") measure separately. Without the stamp this assertion
    # would pass for the wrong reason: an unstamped payload renders `tlm ?` too,
    # so the debounce would look broken-or-working identically.
    assert first["unknown"] is False
    assert telemetry_block.render(_stamp(first)) == {"text": "", "state": "Idle"}
    # ...but a persistent one must become visible, carrying `unknown_since`
    # forward across the oneshot poller's restarts.
    later = poll.parse_telemetry(_verdict(state="unreachable"), prev=first,
                                 now=1000 + 1800, grace=1800,
                                 unknown_states=UNKNOWN_STATES)
    assert later["unknown_since"] == 1000
    assert later["unknown"] is True
    assert telemetry_block.render(_stamp(later)) == {
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


# --------------------------------------------------------------------------- #
# 🔴 THE DEADMAN'S OWN DEADMAN. This block exists to make silence visible, and
# it was silent about its own silence.
#
# This group REPLACES `test_telemetry_block_hides_on_missing_and_poller_stale`,
# which asserted the defect as the contract: a missing cache, a `stale` marker,
# an `error` marker carrying FIVE dead sources, and an unreadable count all had
# to render `{"text": "", "state": "Idle"}` — the same pixels as a fully healthy
# pipeline. The old mapping was deliberate and its stated ground was that "the
# unit's OnFailure toast covers a fetch that raised". That ground does not exist:
# `bar-status-poll.source()` converts every fetch exception into a `stale()`
# marker, `main()` returns 0, and `__main__` ends `except Exception: sys.exit(0)`
# — so the service EXITS SUCCESSFULLY on exactly the failure the marker records
# and `OnFailure=` never fires. A STOPPED timer raises no failure at all, so it
# cannot cover the frozen-cache case even in principle.
# --------------------------------------------------------------------------- #
def test_the_poller_EXITS_ZERO_on_the_failure_the_marker_records(monkeypatch,
                                                                 tmp_path):
    """🔴 THE LOAD-BEARING FACT behind the group above, measured rather than
    asserted from the docstring it replaces. If this is ever false — if a failing
    source really does fail the unit — then `OnFailure` IS a compensating control
    and the group below is arguing from a premise that no longer holds.
    """
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(poll, "signal_bar", lambda _n: None)

    def _boom():
        raise RuntimeError("clickhouse unreachable")

    payload = poll.run_source("telemetry", _boom)
    assert payload["state"] == "stale", payload
    # the marker is on disk...
    on_disk = json.loads((tmp_path / "telemetry.json").read_text())
    assert on_disk["state"] == "stale"
    # ...and the process that wrote it reports SUCCESS, so nothing downstream of
    # the unit's exit status can know the fetch failed.
    monkeypatch.setattr(poll, "SOURCES", (("telemetry", _boom),))
    assert poll.main([]) == 0


def test_the_deadman_block_now_has_a_DEADMAN_OF_ITS_OWN():
    """🔴 THE DEFECT. Four ways of not knowing, one of which used to be the only
    one this block could express. All four must be visible, and none may render
    as the healthy pipeline."""
    healthy = telemetry_block.render(_aged(0, count=0, state="Idle"),
                                     now=FIXED_NOW)
    assert healthy == {"text": "", "state": "Idle"}

    missing = telemetry_block.render(None, now=FIXED_NOW)
    marker = telemetry_block.render(_aged(0, state="stale", count=0),
                                    now=FIXED_NOW)
    frozen = telemetry_block.render(_aged(86_400, count=0, state="Idle"),
                                    now=FIXED_NOW)
    unknown = telemetry_block.render(_aged(0, count=0, unknown=True,
                                           state="Warning"), now=FIXED_NOW)
    for name, pill in (("missing", missing), ("marker", marker),
                       ("frozen", frozen), ("unknown", unknown)):
        assert pill == {"text": "tlm ?", "short_text": "tlm ?",
                        "state": "Warning"}, (name, pill)
        assert pill != healthy, name


def test_a_FROZEN_telemetry_cache_KEEPS_the_dead_sources_it_last_counted():
    """🔴 A MEASUREMENT OUTAGE MUST NOT MAKE A KNOWN ALARM QUIETER — the same
    line `i3status-clawgate._unmeasured` draws for `stuck_count`. A source does
    not come back to life because the poller stopped looking, so three dead
    sources stay three, Critical, with the trailing `?` marking the number as
    the last readable poll rather than a fresh measurement.

    ⚠ SCOPE, stated because the name would otherwise overclaim: this is the
    FROZEN-FILE branch, where the old payload is still on disk. On the MARKER
    branch `stale()` writes `count: 0` OVER the last reading, so the pill is a
    bare `tlm ?` — louder than the empty pill it used to be, but not the full
    carry-forward `bar-status-poll.carry_stuck_forward` gives clawgate.
    """
    frozen = telemetry_block.render(_aged(86_400, count=3, state="Critical"),
                                    now=FIXED_NOW)
    assert frozen["text"] == "tlm 3?"
    assert frozen["state"] == "Critical"
    assert frozen["short_text"] == frozen["text"]
    assert frozen["text"].endswith("?")
    # ...and a frozen QUIET reading may not borrow that alarm
    calm = telemetry_block.render(_aged(86_400, count=0, state="Idle"),
                                  now=FIXED_NOW)
    assert calm["text"] == "tlm ?" and calm["state"] == "Warning"
    assert frozen["state"] != calm["state"]
    # ...and the live reading of the SAME count is not marked as uncertain
    live = telemetry_block.render(_aged(0, count=3, state="Critical"),
                                  now=FIXED_NOW)
    assert live["text"] == "tlm 3" and "?" not in live["text"]


@pytest.mark.parametrize("age,expected_text,expected_state", [
    (0, "tlm 3", "Critical"),          # measured: the death is the reading
    (86_400, "tlm 3?", "Critical"),    # frozen: the same death, marked
])
def test_a_MEASURED_death_outranks_an_UNKNOWN_at_the_PILL_too(
        age, expected_text, expected_state):
    """🔴 THE SAME DOWNGRADE, ONE BRANCH FURTHER IN. `parse_telemetry` decided
    this at the WRITER — its fourth case, "A MEASURED DEATH OUTRANKS AN
    UNCERTAINTY" — and the renderer contradicted it: `unknown` was checked
    BEFORE the count, so a payload carrying both rendered `tlm ?`/Warning and
    silently dropped three measured deaths.

    It was invisible against a current-schema cache (the poller clears `unknown`
    whenever the count is non-zero) and reachable from any cache written before
    that rule existed — i.e. exactly the shape of a defect that survives review
    and then fires once, during an incident, on an old file.

    A count is evidence; an uncertainty is not evidence against it.
    """
    out = telemetry_block.render(
        _aged(age, count=3, unknown=True, state="Critical"), now=FIXED_NOW)
    assert out["text"] == expected_text, out
    assert out["state"] == expected_state, out
    # ...and the uncertainty ALONE, with nothing measured dead, still says `?`
    quiet_unknown = telemetry_block.render(
        _aged(0, count=0, unknown=True, state="Warning"), now=FIXED_NOW)
    assert quiet_unknown == {"text": "tlm ?", "short_text": "tlm ?",
                             "state": "Warning"}


@pytest.mark.parametrize("age,expected_text", [(0, "tlm 3"), (86_400, "tlm 3?")])
@pytest.mark.parametrize("cache_state", [None, "Idle", "Chartreuse"])
def test_a_MEASURED_DEATH_COLOURS_whatever_the_CACHE_calls_its_state(
        cache_state, age, expected_text):
    """🔴 FOUND BY A MUTATION SWEEP, NOT BY REVIEW.
    `test_block_defaults_state_when_missing_or_idle` is parametrized over
    `BLOCKS`, which is clawgate/mail/alerts/civitai — telemetry is NOT in it. So
    `_reading_pill`'s `state = "Critical"` fallback could be changed to
    `"Warning"` and the entire suite stayed green: three measured dead sources
    rendering soft-yellow whenever the cache's own `state` is absent, calm, or
    junk — i.e. any cache from a writer this block no longer recognises.

    Both ages, because the carried pill takes its colour from the same helper:
    a downgrade here is a downgrade on the outage path too. `Critical` is a
    LITERAL read off the operator's decision, not from the module — a source
    that has stopped emitting is the loudest thing this pill says.
    """
    payload = _aged(age, count=3)
    if cache_state is not None:
        payload["state"] = cache_state
    out = telemetry_block.render(payload, now=FIXED_NOW)
    assert out["text"] == expected_text, (cache_state, out)
    assert out["short_text"] == out["text"], (cache_state, out)
    assert out["state"] == "Critical", (cache_state, out)


@pytest.mark.parametrize("bad", [
    None, [], ["a", "list"], {}, "x", "", 3, 0, True, 2.5, {"count": "NaN"},
])
def test_the_telemetry_render_FUNCTION_is_failsafe_AND_stays_visible(bad):
    """🔴 A DIRECT call. `__main__` catches everything and prints a
    BYTE-IDENTICAL `tlm ?`, so `render` replaced wholesale by `raise` is
    invisible to every subprocess test of this block. The truthy non-dicts are
    the load-bearing rows: `_unmeasured` reaches for `.get("count")`.
    """
    out = telemetry_block.render(bad, now=FIXED_NOW)     # must not raise
    assert out == {"text": "tlm ?", "short_text": "tlm ?",
                   "state": "Warning"}, bad
    assert out != {"text": "", "state": "Idle"}, bad


def test_telemetry_block_main_emits_one_json_line(tmp_path, monkeypatch):
    monkeypatch.setenv("BAR_STATUS_DIR", str(tmp_path))
    (tmp_path / "telemetry.json").write_text(json.dumps(
        {"count": 3, "state": "Critical", "unknown": False,
         "ts": int(time.time())}))
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

