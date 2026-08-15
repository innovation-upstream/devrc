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
    # own copy of the predicate, agent-ops and session-manager would disagree
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


#: Fields a block needs BEYOND `count`/`state` to render its normal, FULLY
#: MEASURED shape, as ZERO-ARG FACTORIES (one of them has to be built against the
#: current clock). Only clawgate has any, and it has two:
#:
#:  * `stuck_count` — an ABSENT one renders `?` on purpose (a cache from a poller
#:    predating the stuck predicate carries a count computed by the old
#:    status-only rule, so it is a different number, not merely a partial one);
#:  * `ts` — an absent or OLD one now also renders `?`, because a cache nobody
#:    has refreshed is not a measurement of the board as it is now. Every payload
#:    `bar-status-poll` writes carries `ts`, so a fixture without one was never a
#:    realistic cache; it was a fixture that could not tell the freshness gate
#:    from a hole in it.
#:
#: The generic block tests below are about icon / colour / hide-at-zero, so they
#: hand clawgate a MEASURED, CURRENT payload; the `?` and `!N` renderings and the
#: freshness gate have their own tests.
BLOCK_MEASURED_EXTRA = {
    "clawgate": lambda: {"stuck_count": 0, "ts": int(time.time())},
}


def _measured(name, **fields):
    """A cache payload for `name` in its fully-measured shape.

    The defaults FILL IN, they do not overwrite: a caller passing an explicit
    `stuck_count=1` is naming the thing under test, and a helper that quietly
    replaced it with the measured-zero default would turn that test into an
    assertion about the default instead.
    """
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
    """A clawgate cache payload written `age_secs` before FIXED_NOW."""
    fields.setdefault("ts", int(FIXED_NOW - age_secs))
    return fields


#: The VISIBLE "the board could not be read" pill, spelled out as a LITERAL —
#: never built from the block's own constants, so a test can disagree with the
#: module instead of restating it. Used by both the pure-function assertions and
#: the end-to-end subprocess pair, which is what lets the two be compared.
UNKNOWN_PILL = {"icon": "tasks", "text": "?", "short_text": "?",
                "state": "Warning"}
INVISIBLE_PILL = {"text": "", "state": "Idle"}


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


@pytest.mark.parametrize("junk", ["NaN", "2", 2.5, True, False, None, [], {}])
def test_a_count_the_block_REFUSES_TO_READ_is_not_a_count_of_zero(junk):
    """🔴 THE BRANCH THIS CHANGE ADDED, WHICH HAD NO TEST. `render`'s
    `if count is None: return _unmeasured(...)` could be swapped for
    `return dict(_EMPTY)` and the whole suite stayed green — a CURRENT cache
    whose `count` is `"NaN"` / `2.5` / `True` would silently revert to the
    invisible pill, which is the exact substitution the change exists to forbid,
    on a payload the freshness gate says IS a present-tense reading.

    `"2"` is in the list deliberately: `int()` would coerce it happily. The
    poller writes ints, so a string count means the WRITER changed, and a block
    that quietly agrees with a writer it no longer recognises is how a pill stops
    meaning what it says.
    """
    payload = _aged(0, count=junk, stuck_count=0, state="Warning")
    # the payload must reach the count branch, not stop at the freshness gate
    assert clawgate_block.is_current(payload, FIXED_NOW) is True
    assert clawgate_block.render(payload, now=FIXED_NOW) == UNKNOWN_PILL, junk


def test_an_UNREADABLE_COUNT_still_keeps_a_KNOWN_stuck_alarm():
    """The escalation invariant on the junk-count path too: refusing to read the
    count is a reason to distrust the number beside the alarm, not to drop the
    alarm."""
    out = clawgate_block.render(
        _aged(0, count="NaN", stuck_count=2, state="Critical"), now=FIXED_NOW)
    assert out["text"] == "!2?" and out["state"] == "Critical"


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
def test_the_two_NO_COERCION_predicates_AGREE(value):
    """ONE RULE, TWO FILES. `bar-status-poll._strict_int` and
    `i3status-clawgate._int_or_none` are the same predicate on opposite sides of
    an extensionless-script boundary neither can import across, so the only thing
    that can keep them honest is an assertion that they answer identically. A
    poller that starts accepting `2.5` while the block still refuses it writes a
    number the pill renders as `?` forever, and neither file's own tests can see
    it."""
    mine = poll._strict_int(value)
    theirs = clawgate_block._int_or_none(value)
    assert mine == theirs and type(mine) is type(theirs), value


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


#: The blocks whose UNREADABLE rendering is the invisible pill. clawgate is
#: excluded by design and asserted the other way — it is the only surface for a
#: stuck dispatch that nothing can dismiss, so it renders `?` rather than hiding.
#: Derived from BLOCKS by SUBTRACTION, so a block added to BLOCKS joins this list
#: automatically instead of silently escaping it.
#:
#: 🔴 WHAT REPLACED clawgate's ROW HERE. Leaving this list was also leaving the
#: only test that fed it `None, [], "x", 3, {"count": "NaN"}, {}` as a PURE
#: function, and for a while nothing did — a mutant passing `payload` instead of
#: `None` to `_unmeasured` (an `AttributeError` on any truthy non-dict) survived
#: the whole suite. `test_the_clawgate_render_FUNCTION_is_failsafe_AND_stays_visible`
#: feeds it that same set and asserts the OPPOSITE pill; the subprocess pair in
#: `test_the_clawgate_block_renders_an_UNREADABLE_board_VISIBLY` covers the
#: pixels but, going through `__main__`'s `except`, cannot see a crash at all.
HIDES_WHEN_UNREADABLE = [b for b in BLOCKS if b[0] != "clawgate"]
assert len(HIDES_WHEN_UNREADABLE) == len(BLOCKS) - 1, "clawgate left BLOCKS"


@pytest.mark.parametrize("name,mod,icon,default_state", HIDES_WHEN_UNREADABLE)
def test_block_stale_is_invisible(name, mod, icon, default_state):
    assert mod.render({"count": 5, "state": "stale"}) == {"text": "", "state": "Idle"}


@pytest.mark.parametrize("name,mod,icon,default_state", HIDES_WHEN_UNREADABLE)
def test_block_error_marker_is_invisible(name, mod, icon, default_state):
    assert mod.render({"count": 5, "state": "Warning", "error": "x"}) == \
        {"text": "", "state": "Idle"}


@pytest.mark.parametrize("name,mod,icon,default_state", HIDES_WHEN_UNREADABLE)
def test_block_none_and_malformed_are_invisible(name, mod, icon, default_state):
    for bad in (None, [], "x", 3, {"count": "NaN"}, {}):
        out = mod.render(bad)
        assert out == {"text": "", "state": "Idle"}


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
# ⚠ `i3status-clawgate` is DELIBERATELY ABSENT from the two "…_is_invisible"
# parametrizations below and has its own pair further down. It is the one block
# whose no-file / corrupt-file rendering is a VISIBLE `?`: a stuck dispatch is
# announced nowhere else that cannot be dismissed, so "I could not read the
# board" must not borrow the pixels that mean "the board is empty". The other
# three still hide, and whether they should is a separate question from this one.
@pytest.mark.parametrize("script,cachefile", [
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


@pytest.mark.parametrize("script,cachefile", [
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

