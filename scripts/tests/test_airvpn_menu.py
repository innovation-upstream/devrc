"""Unit tests for the HOST AirVPN bar block — airvpn-menu, i3status-airvpn, and the
poller's parse_airvpn / parse_wg_dump.

All OFFLINE: rofi / network / sudo / cluster are never touched. The rofi UI itself
isn't unit-testable, so the LOGIC is factored into pure functions and tested here:
  - the live-status header formatter (down / verified / leak / port-down / error),
  - the manifest parse + merge-with-live-load + sort,
  - the config-switch rewrite (correct swap + malformed-input rejection),
  - the exit-IP leak verdict (verified / leak / unknown, ipinfo + ipleak shapes),
  - the forwarded-port verdict,
  - the credential-free block render (off / verified / unknown / leak / port-down /
    stale / corrupt / missing),
  - the poller's parse_airvpn normalizer + parse_wg_dump.
The `--dump-menu` / `--dry-run ACTION` CLI paths are exercised via subprocess (no
network). Mirrors scripts/tests/test_media_menu.py.

    run:  pytest scripts/tests/test_airvpn_menu.py
"""
import importlib.machinery
import importlib.util
import json
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


am = _load("airvpn-menu", "airvpn_menu")
blk = _load("i3status-airvpn", "i3status_airvpn")
poll = _load("bar-status-poll", "bar_status_poll_airvpn")


SAMPLE_CONF = """\
[Interface]
PrivateKey = QI0R2rjE0z6qQ0abcSECRETprivatekeyGOESHERE00000000=
Address = 10.128.0.2/32
DNS = 10.128.0.1
PostUp = ip route add %i-endpoint via gw

[Peer]
PublicKey = OLDpubkeyOLDpubkeyOLDpubkeyOLDpubkeyOLDpub00=
PresharedKey = PSKpskPSKpskPSKpskPSKpskPSKpskPSKpskPSKpsk00=
Endpoint = 185.9.19.106:1637
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 15
"""


# --------------------------------------------------------------------------- #
# header formatter
# --------------------------------------------------------------------------- #
def test_header_down_is_neutral_and_prompts_connect():
    h = am.format_header({"up": False})
    assert "off" in h.lower() and "Connect" in h


def test_header_up_verified_green_shows_server_and_exit():
    h = am.format_header({"up": True, "country_code": "ca", "server": "Beemim",
                          "verdict": "verified", "exit_ip": "1.2.3.4",
                          "handshake_age": 125})
    assert h.startswith("🟢")
    assert "AirVPN CA" in h and "Beemim" in h and "1.2.3.4" in h
    assert "hs 2m" in h


def test_header_leak_is_red():
    h = am.format_header({"up": True, "verdict": "leak", "exit_ip": "203.0.113.9"})
    assert h.startswith("🔴")
    assert "LEAK" in h and "203.0.113.9" in h


def test_header_port_down_is_yellow():
    h = am.format_header({"up": True, "country_code": "ca", "server": "Beemim",
                          "fwd_verdict": "down", "fwd_port": 12345})
    assert h.startswith("🟡")
    assert "12345" in h and "DOWN" in h


def test_header_up_unverified_marked():
    h = am.format_header({"up": True, "country_code": "ca", "server": "X",
                          "verdict": "unknown", "exit_ip": "1.1.1.1"})
    assert h.startswith("🟡")
    assert "unverified" in h


def test_header_error_and_nondict_never_raise():
    assert am.format_header(None, err="TimeoutError").startswith("⚠")
    assert am.format_header("nonsense").startswith("⚠")


# --------------------------------------------------------------------------- #
# menu construction
# --------------------------------------------------------------------------- #
def test_menu_offers_connect_when_down_disconnect_when_up():
    down = am.build_menu(False)
    up = am.build_menu(True)
    assert down[0][1] == am.A_CONNECT
    assert up[0][1] == am.A_DISCONNECT
    # the other four entries are identical + in the same order
    assert [a for _, a in down[1:]] == [a for _, a in up[1:]]
    assert [a for _, a in down[1:]] == [am.A_SWITCH, am.A_VERIFY, am.A_FWD, am.A_STATUS]


# --------------------------------------------------------------------------- #
# manifest parse / merge / sort
# --------------------------------------------------------------------------- #
MANIFEST = {
    "wg_pubkey": "SHAREDpubSHAREDpubSHAREDpubSHAREDpubSHARE00=",
    "servers": [
        {"name": "Beemim", "country": "Austria", "country_code": "at",
         "city": "Vienna", "endpoint_ip": "37.120.155.178"},
        {"name": "Alderamin", "country": "Austria", "city": "Vienna",
         "endpoint_ip": "185.9.19.106"},
        {"name": "NoIP", "country": "Nowhere"},              # dropped (no endpoint)
        "junk",                                              # dropped (non-dict)
    ],
}


def test_parse_manifest_hoists_shared_pubkey_and_drops_bad_rows():
    rows = am.parse_manifest(MANIFEST)
    assert len(rows) == 2
    assert all(r["wg_pubkey"] == MANIFEST["wg_pubkey"] for r in rows)
    assert {r["name"] for r in rows} == {"Beemim", "Alderamin"}


def test_parse_manifest_rejects_non_manifest():
    with pytest.raises(ValueError):
        am.parse_manifest({"nope": 1})
    with pytest.raises(ValueError):
        am.parse_manifest([])


def test_merge_load_attaches_live_load_and_sorts_least_loaded_first():
    rows = am.parse_manifest(MANIFEST)
    api = {"servers": [
        {"public_name": "Beemim", "currentload": 80, "users": 40, "health": "ok"},
        {"public_name": "Alderamin", "currentload": 12, "users": 5,
         "health": "warning"},
    ]}
    merged = am.merge_load(rows, api)
    by = {r["name"]: r for r in merged}
    assert by["Beemim"]["load"] == 80 and by["Alderamin"]["load"] == 12
    srt = am.sort_servers(merged, by="load")
    assert srt[0]["name"] == "Alderamin"   # 12% before 80%


def test_merge_load_unknown_load_sorts_last():
    rows = am.parse_manifest(MANIFEST)
    api = {"servers": [{"public_name": "Beemim", "currentload": 50}]}
    merged = am.merge_load(rows, api)
    srt = am.sort_servers(merged, by="load")
    assert srt[0]["name"] == "Beemim"      # known load first
    assert srt[-1]["name"] == "Alderamin"  # unknown load last


def test_sort_by_country_is_alphabetical():
    rows = am.merge_load(am.parse_manifest(MANIFEST), None)
    srt = am.sort_servers(rows, by="country")
    assert [r["name"] for r in srt] == ["Alderamin", "Beemim"]  # same country, by name


def test_server_label_flags_unhealthy():
    rows = am.merge_load(am.parse_manifest(MANIFEST),
                         {"servers": [{"public_name": "Beemim", "currentload": 5,
                                       "health": "warning"}]})
    label = am.server_label(next(r for r in rows if r["name"] == "Beemim"))
    assert "Beemim" in label and "⚠" in label


# --------------------------------------------------------------------------- #
# validators + config switch rewrite
# --------------------------------------------------------------------------- #
def test_endpoint_validator():
    assert am.is_valid_endpoint("185.9.19.106:1637")
    assert not am.is_valid_endpoint("185.9.19.106")            # no port
    assert not am.is_valid_endpoint("999.9.19.106:1637")       # octet > 255
    assert not am.is_valid_endpoint("185.9.19.106:70000")      # port > 65535
    assert not am.is_valid_endpoint("evil; rm -rf:1637")
    assert not am.is_valid_endpoint(None)


def test_pubkey_validator():
    assert am.is_valid_pubkey("PyLCXAQT8KkM4T+dUsOQfn+Ub3pGxfGlxkIApuig+hk=")
    assert not am.is_valid_pubkey("tooshort=")
    assert not am.is_valid_pubkey("has spaces in it aaaaaaaaaaaaaaaaaaaaaaaa0=")
    assert not am.is_valid_pubkey(None)


def test_rewrite_conf_swaps_only_peer_endpoint_and_pubkey():
    new_ep = "37.120.155.178:1637"
    new_pk = "NEWpubNEWpubNEWpubNEWpubNEWpubNEWpubNEWpub0="
    out = am.rewrite_conf(SAMPLE_CONF, new_ep, new_pk)
    assert "Endpoint = 37.120.155.178:1637" in out
    assert "PublicKey = " + new_pk in out
    # untouched: the private key, address, DNS, PSK, allowed-ips all survive
    assert "PrivateKey = QI0R2rjE0z6qQ0abcSECRETprivatekeyGOESHERE00000000=" in out
    assert "Address = 10.128.0.2/32" in out
    assert "DNS = 10.128.0.1" in out
    assert "PresharedKey = PSKpskPSKpskPSKpskPSKpskPSKpskPSKpskPSKpsk00=" in out
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in out
    # exactly one Endpoint + one PublicKey line
    assert out.count("Endpoint = ") == 1
    assert out.count("PublicKey = ") == 1
    # the old values are gone
    assert "185.9.19.106:1637" not in out
    assert "OLDpubkey" not in out


def test_rewrite_conf_rejects_malformed():
    good_pk = "NEWpubNEWpubNEWpubNEWpubNEWpubNEWpubNEWpub0="
    with pytest.raises(ValueError):
        am.rewrite_conf(SAMPLE_CONF, "not-an-endpoint", good_pk)
    with pytest.raises(ValueError):
        am.rewrite_conf(SAMPLE_CONF, "37.120.155.178:1637", "badkey")
    with pytest.raises(ValueError):
        am.rewrite_conf("[Interface]\nPrivateKey = x\n", "37.120.155.178:1637",
                        good_pk)   # no Endpoint line


def test_build_endpoint():
    assert am.build_endpoint("1.2.3.4", 1637) == "1.2.3.4:1637"
    assert am.build_endpoint("1.2.3.4", "51820") == "1.2.3.4:51820"


# --------------------------------------------------------------------------- #
# exit-IP leak verdict
# --------------------------------------------------------------------------- #
def test_verdict_verified_when_exit_country_matches_server():
    geo = {"ip": "37.120.155.178", "country": "AT", "org": "AS1 hoster"}
    v = am.exit_ip_verdict(geo, {"server_cc": "at", "home_ip": "203.0.113.9"})
    assert v == "verified"


def test_verdict_verified_when_exit_matches_server_entry_ip():
    geo = {"ip": "37.120.155.178", "country": "XX"}
    v = am.exit_ip_verdict(geo, {"server_entry_ip": "37.120.155.178",
                                 "server_cc": "at"})
    assert v == "verified"


def test_verdict_leak_when_exit_is_home_ip():
    geo = {"ip": "203.0.113.9", "country": "US"}
    v = am.exit_ip_verdict(geo, {"home_ip": "203.0.113.9", "server_cc": "at"})
    assert v == "leak"


def test_verdict_leak_when_exit_country_is_home_country():
    geo = {"country_code": "us", "ip": "8.8.8.8", "isp_name": "Comcast"}  # ipleak shape
    v = am.exit_ip_verdict(geo, {"home_cc": "US", "server_cc": "at"})
    assert v == "leak"


def test_verdict_unknown_without_reference_or_geo():
    assert am.exit_ip_verdict(None, {"server_cc": "at"}) == "unknown"
    assert am.exit_ip_verdict({"ip": "1.2.3.4"}, {}) == "unknown"
    # country mismatch with no home reference -> unknown (don't cry wolf)
    assert am.exit_ip_verdict({"ip": "9.9.9.9", "country": "DE"},
                              {"server_cc": "at"}) == "unknown"


# --------------------------------------------------------------------------- #
# forwarded-port verdict
# --------------------------------------------------------------------------- #
def test_forwarded_port_verdict():
    assert am.forwarded_port_verdict(None, None) == "na"
    assert am.forwarded_port_verdict(0, True) == "na"
    assert am.forwarded_port_verdict(12345, True) == "ok"
    assert am.forwarded_port_verdict(12345, False) == "down"
    assert am.forwarded_port_verdict(12345, None) == "unknown"
    assert am.forwarded_port_verdict("bogus", True) == "na"


# --------------------------------------------------------------------------- #
# block render (i3status-airvpn) — cache payload -> block dict
# --------------------------------------------------------------------------- #
def test_render_off_is_neutral_visible():
    r = blk.render({"up": False})
    assert r["state"] == "Idle" and r["text"] == "VPN off"


def test_render_up_verified_neutral():
    r = blk.render({"up": True, "country_code": "ca", "verdict": "verified",
                    "handshake_age": 30})
    assert r["state"] == "Idle"
    assert r["text"].startswith("AirVPN CA")
    assert "?" not in r["text"]


def test_render_up_unknown_marks_but_stays_neutral():
    r = blk.render({"up": True, "country_code": "ca", "verdict": "unknown"})
    assert r["state"] == "Idle" and r["text"].endswith("?")


def test_render_leak_is_critical():
    r = blk.render({"up": True, "country_code": "ca", "verdict": "leak"})
    assert r["state"] == "Critical" and "LEAK" in r["text"]


def test_render_port_down_is_warning():
    r = blk.render({"up": True, "country_code": "ca", "verdict": "verified",
                    "fwd_verdict": "down"})
    assert r["state"] == "Warning" and "port" in r["text"]


def test_render_stale_error_missing_corrupt_soft_yellow():
    for payload in ({"state": "stale"}, {"error": "boom"}, None, "garbage", 42):
        r = blk.render(payload)
        assert r["state"] == "Warning" and r["text"] == "VPN?"


# --------------------------------------------------------------------------- #
# poller: parse_airvpn normalizer + parse_wg_dump
# --------------------------------------------------------------------------- #
def test_parse_airvpn_down_carries_home_reference():
    f = poll.parse_airvpn({"up": False, "home_ip": "203.0.113.9", "home_cc": "us"})
    assert f == {"up": False, "home_ip": "203.0.113.9", "home_cc": "us"}


def test_parse_airvpn_up_flattens_and_defaults():
    probe = {"up": True,
             "server": {"name": "Beemim", "country": "Austria", "country_code": "AT"},
             "exit": {"ip": "37.120.155.178", "country": "AT", "org": "AS1"},
             "verdict": "verified", "handshake_age": 12,
             "server_entry_ip": "37.120.155.178",
             "fwd_port": 12345, "fwd_verdict": "ok", "killswitch": True}
    f = poll.parse_airvpn(probe)
    assert f["up"] and f["server"] == "Beemim" and f["country_code"] == "at"
    assert f["exit_ip"] == "37.120.155.178" and f["verdict"] == "verified"
    assert f["fwd_port"] == 12345 and f["fwd_verdict"] == "ok"
    assert f["killswitch"] is True


def test_parse_airvpn_sanitizes_bad_verdicts():
    f = poll.parse_airvpn({"up": True, "verdict": "bogus", "fwd_verdict": "weird"})
    assert f["verdict"] == "unknown" and f["fwd_verdict"] == "na"
    # killswitch defaults to the up-state when unspecified
    assert f["killswitch"] is True


def test_parse_airvpn_rejects_non_dict():
    with pytest.raises(ValueError):
        poll.parse_airvpn("nope")


def test_parse_wg_dump_extracts_endpoint_and_handshake():
    import time as _t
    hs = int(_t.time()) - 20
    dump = (
        "PRIViface\tPUBiface\t51820\toff\n"
        "PyLCXAQpub\t(none)\t37.120.155.178:1637\t0.0.0.0/0\t%d\t1000\t2000\t15\n"
        % hs
    )
    w = poll.parse_wg_dump(dump)
    assert w["peer_endpoint_ip"] == "37.120.155.178"
    assert 15 <= w["handshake_age"] <= 30
    assert w["peers"] == 1


def test_parse_wg_dump_empty_and_garbage():
    assert poll.parse_wg_dump("")["peer_endpoint_ip"] is None
    assert poll.parse_wg_dump(None)["handshake_age"] is None
    assert poll.parse_wg_dump("just one line")["peers"] == 0


# --------------------------------------------------------------------------- #
# CLI: --dump-menu + --dry-run (offline, no rofi, no network, no sudo)
# --------------------------------------------------------------------------- #
def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "airvpn-menu"), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=15)


def test_dump_menu_down_lists_connect():
    r = _run("--dump-menu")
    assert r.returncode == 0
    assert "connect" in r.stdout and "disconnect" not in r.stdout
    assert len(r.stdout.strip().splitlines()) == len(am.build_menu(False))


def test_dump_menu_up_lists_disconnect():
    r = _run("--dump-menu", "--up")
    assert "disconnect" in r.stdout and "\tconnect" not in r.stdout


def test_dry_run_connect_disconnect():
    assert "airvpn-sudo up" in _run("--dry-run", "connect").stdout
    assert "airvpn-sudo down" in _run("--dry-run", "disconnect").stdout


def test_dry_run_switch_builds_validated_sudo_call():
    out = _run("--dry-run", "switch").stdout
    assert "airvpn-sudo switch" in out
    # the emitted endpoint must be a well-formed ip:port
    tok = out.split("airvpn-sudo switch", 1)[1].split()
    assert am.is_valid_endpoint(tok[0])
    assert am.is_valid_pubkey(tok[1])


def test_dry_run_verify_and_fwd():
    assert "exit_ip_verdict" in _run("--dry-run", "verify").stdout
    assert "forwarded_port_verdict" in _run("--dry-run", "fwd").stdout
