"""Unit tests for scripts/media-menu — the rofi action menu for the media block.

All OFFLINE: rofi/network/cluster are never touched. rofi UI itself isn't
unit-testable, so the LOGIC is factored into pure functions and tested here:
  - the live-status header formatter (connected / firewalled / unreachable),
  - the whisparr-category hash filter + active-torrent counter,
  - qBit action URL/param construction (incl. the 5.x pause->stop / resume->start
    rename) and the Whisparr command request,
and the `--dry-run ACTION` / `--dump-menu` CLI paths are exercised via subprocess
(no network). Mirrors scripts/tests/test_bar_status.py.

    run:  pytest scripts/tests/test_media_menu.py
"""
import importlib.machinery
import importlib.util
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


mm = _load("media-menu", "media_menu")


# --------------------------------------------------------------------------- #
# header formatter
# --------------------------------------------------------------------------- #
def test_header_connected_shows_speeds_and_active():
    info = {"connection_status": "connected",
            "dl_info_speed": 488656, "up_info_speed": 1116370}
    h = mm.format_header(info, active=9, country="CA")
    assert h.startswith("🟢")
    assert "AirVPN CA" in h
    assert "↓477K" in h and "↑1.1M" in h
    assert "· 9 active" in h


def test_header_firewalled_is_red_and_flags_port():
    info = {"connection_status": "firewalled",
            "dl_info_speed": 0, "up_info_speed": 0}
    h = mm.format_header(info, active=0, country="CA")
    assert h.startswith("🔴")
    assert "firewalled" in h and "port DOWN" in h


def test_header_unknown_status_is_yellow_and_shown():
    info = {"connection_status": "connecting", "dl_info_speed": 0, "up_info_speed": 0}
    h = mm.format_header(info, country="CA")
    assert h.startswith("🟡")
    assert "(connecting)" in h


def test_header_error_degrades_gracefully():
    h = mm.format_header(None, err="TimeoutError")
    assert h.startswith("⚠")
    assert "TimeoutError" in h
    assert "down" in h.lower()


def test_header_non_dict_payload_never_raises():
    assert mm.format_header("nonsense").startswith("⚠")


# --------------------------------------------------------------------------- #
# whisparr filter + active counter
# --------------------------------------------------------------------------- #
def test_whisparr_hashes_filters_by_category():
    torrents = [
        {"hash": "aaa", "category": "whisparr"},
        {"hash": "bbb", "category": "prowlarr"},
        {"hash": "ccc", "category": "whisparr"},
        {"category": "whisparr"},              # no hash -> skipped
        "junk",                                # non-dict -> skipped
    ]
    assert mm.whisparr_hashes(torrents) == ["aaa", "ccc"]


def test_whisparr_hashes_empty_on_bad_input():
    assert mm.whisparr_hashes(None) == []
    assert mm.whisparr_hashes("x") == []


def test_active_count_counts_only_transferring_states():
    torrents = [
        {"state": "downloading"}, {"state": "uploading"}, {"state": "forcedDL"},
        {"state": "pausedDL"},                 # not active
        {"state": "stoppedUP"},                # not active
        "junk",
    ]
    assert mm.active_count(torrents) == 3
    assert mm.active_count(None) == 0


# --------------------------------------------------------------------------- #
# qBit action / whisparr command construction (5.x rename)
# --------------------------------------------------------------------------- #
def test_pause_maps_to_stop_endpoint():
    path, params = mm.qbit_action_request("pause")
    assert path == "/api/v2/torrents/stop"
    assert params == {"hashes": "all"}


def test_resume_maps_to_start_endpoint():
    path, params = mm.qbit_action_request("resume")
    assert path == "/api/v2/torrents/start"
    assert params == {"hashes": "all"}


def test_forcestart_sets_value_true():
    path, params = mm.qbit_action_request("forcestart", hashes="aaa|bbb")
    assert path == "/api/v2/torrents/setForceStart"
    assert params == {"hashes": "aaa|bbb", "value": "true"}


def test_unknown_action_raises():
    with pytest.raises(ValueError):
        mm.qbit_action_request("delete")


def test_join_hashes():
    assert mm.join_hashes("all") == "all"
    assert mm.join_hashes(["a", "b", "c"]) == "a|b|c"


def test_whisparr_command_request():
    path, body = mm.whisparr_command_request()
    assert path == "/api/v3/command"
    assert body == {"name": "MissingMoviesSearch"}


def test_confirm_gated_actions_are_the_risky_two():
    # restart + mass-grab must be behind a confirm; instant/reversible ones must not.
    assert "vpn-restart" in mm.CONFIRM
    assert "whisparr-search" in mm.CONFIRM
    assert "pause" not in mm.CONFIRM and "resume" not in mm.CONFIRM


# --------------------------------------------------------------------------- #
# CLI: --dump-menu + --dry-run (offline, no rofi, no network)
# --------------------------------------------------------------------------- #
def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "media-menu"), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=15)


def test_dump_menu_lists_all_entries():
    r = _run("--dump-menu")
    assert r.returncode == 0
    assert "open-qbit" in r.stdout
    assert "Pause all torrents\tpause" in r.stdout
    assert "whisparr-search" in r.stdout
    assert len(r.stdout.strip().splitlines()) == len(mm.MENU)


def test_dry_run_pause_hits_stop_endpoint_no_network():
    r = _run("--dry-run", "pause")
    assert r.returncode == 0
    assert "POST /api/v2/torrents/stop" in r.stdout
    assert "hashes=all" in r.stdout


def test_dry_run_resume_hits_start_endpoint():
    r = _run("--dry-run", "resume")
    assert "POST /api/v2/torrents/start" in r.stdout


def test_dry_run_whisparr_search():
    r = _run("--dry-run", "whisparr-search")
    assert "/api/v3/command" in r.stdout
    assert "MissingMoviesSearch" in r.stdout
