"""Unit tests for the merged notifications pill + notification center.

All OFFLINE: dunstctl/rofi are never touched. The rofi/dunstctl side-effects
aren't unit-testable, so the LOGIC is factored into pure functions and tested:
  - dunstctl-history JSON parse (unwrap the {type,data} field wrappers),
  - unseen-count vs the seen-marker (incl. marker-absent -> ALL unseen),
  - has-critical-among-unseen + max-id,
  - the pill render decision (paused / unseen>0+critical-color / else empty),
  - the notif-center row formatter + build_rows action wiring,
and the `--dump` (notif-center) + standalone (pill) CLI paths via subprocess.
Mirrors scripts/tests/test_media_menu.py.

    run:  pytest scripts/tests/test_notifs.py
"""
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


nf = _load("i3status-notifs", "i3status_notifs")
nc = _load("notif-center", "notif_center")


# --------------------------------------------------------------------------- #
# fixtures: a realistic `dunstctl history` object (newest-first, {type,data})
# --------------------------------------------------------------------------- #
def _wrap(entry):
    """Wrap a plain dict into dunst's per-field {type,data} shape."""
    types = {"id": "i", "summary": "s", "body": "s", "appname": "s",
             "urgency": "s", "timestamp": "x"}
    return {k: {"type": types.get(k, "s"), "data": v} for k, v in entry.items()}


def _history(entries):
    """A full `dunstctl history` JSON object wrapping the given plain entries."""
    return {"type": "aa{sv}", "data": [[_wrap(e) for e in entries]]}


ENTRIES = [
    {"id": 42, "summary": "Build failed", "body": "CI red on main",
     "appname": "gh", "urgency": "CRITICAL", "timestamp": 5_000_000},
    {"id": 41, "summary": "PR merged", "body": "feat/bar #101",
     "appname": "gh", "urgency": "NORMAL", "timestamp": 4_000_000},
    {"id": 40, "summary": "Sync done", "body": "", "appname": "syncthing",
     "urgency": "LOW", "timestamp": 3_000_000},
]


# --------------------------------------------------------------------------- #
# history parse (unwrap {type,data})
# --------------------------------------------------------------------------- #
def test_parse_history_unwraps_fields_newest_first():
    parsed = nf.parse_history(_history(ENTRIES))
    assert [e["id"] for e in parsed] == [42, 41, 40]
    assert parsed[0]["summary"] == "Build failed"
    assert parsed[0]["urgency"] == "CRITICAL"
    assert parsed[0]["timestamp"] == 5_000_000
    assert isinstance(parsed[0]["id"], int)


def test_parse_history_empty_and_malformed_are_safe():
    assert nf.parse_history({"data": [[]]}) == []
    assert nf.parse_history({}) == []
    assert nf.parse_history("nonsense") == []
    assert nf.parse_history({"data": "wrong"}) == []


def test_unwrap_entry_defaults_on_missing_fields():
    e = nf.unwrap_entry({"summary": {"type": "s", "data": "hi"}})
    assert e["summary"] == "hi"
    assert e["id"] == 0 and e["urgency"] == "NORMAL" and e["timestamp"] == 0
    assert nf.unwrap_entry("junk")["id"] == 0


# --------------------------------------------------------------------------- #
# unseen vs marker (incl. marker-absent -> ALL unseen)
# --------------------------------------------------------------------------- #
def test_unseen_marker_absent_counts_all():
    parsed = nf.parse_history(_history(ENTRIES))
    assert len(nf.unseen_entries(parsed, None)) == 3


def test_unseen_marker_mid_counts_only_newer():
    parsed = nf.parse_history(_history(ENTRIES))
    unseen = nf.unseen_entries(parsed, 41)   # only id 42 is > 41
    assert [e["id"] for e in unseen] == [42]


def test_unseen_marker_at_or_above_top_counts_none():
    parsed = nf.parse_history(_history(ENTRIES))
    assert nf.unseen_entries(parsed, 42) == []
    assert nf.unseen_entries(parsed, 99) == []


def test_unseen_bad_input_is_empty():
    assert nf.unseen_entries(None, None) == []


# --------------------------------------------------------------------------- #
# has_critical + max_id
# --------------------------------------------------------------------------- #
def test_has_critical_true_when_any_critical():
    parsed = nf.parse_history(_history(ENTRIES))
    assert nf.has_critical(parsed) is True
    # only the critical one is newer than marker 41 -> still critical
    assert nf.has_critical(nf.unseen_entries(parsed, 41)) is True


def test_has_critical_false_without_critical():
    non_crit = [e for e in nf.parse_history(_history(ENTRIES))
                if e["urgency"] != "CRITICAL"]
    assert nf.has_critical(non_crit) is False
    assert nf.has_critical([]) is False
    assert nf.has_critical(None) is False


def test_max_id():
    parsed = nf.parse_history(_history(ENTRIES))
    assert nf.max_id(parsed) == 42
    assert nf.max_id([]) == 0
    assert nf.max_id(None) == 0


# --------------------------------------------------------------------------- #
# pill render decision
# --------------------------------------------------------------------------- #
def test_render_paused_shows_muted_bell_neutral():
    b = nf.render(paused=True, count=5, critical=True)   # paused wins over count
    assert nf.MUTED_GLYPH in b["text"]
    assert nf.BELL_GLYPH not in b["text"]
    assert b["state"] == "Idle"


def test_render_unseen_critical_is_red_bell_with_count():
    b = nf.render(paused=False, count=3, critical=True)
    assert nf.BELL_GLYPH in b["text"]
    assert "3" in b["text"]
    assert b["state"] == "Critical"


def test_render_unseen_noncritical_is_neutral_bell():
    b = nf.render(paused=False, count=2, critical=False)
    assert nf.BELL_GLYPH in b["text"] and "2" in b["text"]
    assert b["state"] == "Idle"


def test_render_zero_is_plain_bell():
    # idle (nothing unseen, DND off) now renders a plain neutral bell — ALWAYS
    # visible so it stays the click-target for the notification center.
    b = nf.render(paused=False, count=0, critical=False)
    assert nf.BELL_GLYPH in b["text"]
    assert nf.MUTED_GLYPH not in b["text"]
    assert not any(ch.isdigit() for ch in b["text"])   # no count badge
    assert b["state"] == "Idle"
    assert b == {"text": " %s " % nf.BELL_GLYPH,
                 "short_text": " %s " % nf.BELL_GLYPH, "state": "Idle"}


# --------------------------------------------------------------------------- #
# notif-center row formatter + build_rows wiring
# --------------------------------------------------------------------------- #
def test_age_str_buckets():
    assert nc.age_str(0) == "now"
    assert nc.age_str(5_000_000) == "5s"
    assert nc.age_str(180_000_000) == "3m"
    assert nc.age_str(2 * 3600_000_000) == "2h"
    assert nc.age_str(4 * 86400_000_000) == "4d"
    assert nc.age_str("bad") == "?"


def test_format_entry_row_has_summary_body_meta_and_colored_dot():
    e = nf.parse_history(_history(ENTRIES))[0]   # the CRITICAL one, ts=5s
    now = 65_000_000                             # 60s after the notification
    row = nc.format_entry_row(e, now)
    assert "Build failed" in row
    assert "CI red on main" in row
    assert "gh · 1m" in row                      # appname · age
    assert nc.RED in row                         # critical dot is red
    assert "●" in row


def test_format_entry_row_escapes_pango_metacharacters():
    e = {"id": 1, "summary": "a < b & c > d", "body": "x & y",
         "appname": "app", "urgency": "NORMAL", "timestamp": 0}
    row = nc.format_entry_row(e, 0)
    assert "&amp;" in row and "&lt;" in row and "&gt;" in row
    # the raw metacharacters must not survive unescaped in the user text
    assert "a < b" not in row


def test_build_rows_prepends_two_action_rows_then_history():
    parsed = nf.parse_history(_history(ENTRIES))
    rows = nc.build_rows(parsed, 10_000_000)
    assert rows[0][1] == ("toggle-dnd",)
    assert rows[1][1] == ("clear",)
    assert [r[1] for r in rows[2:]] == [("pop", 42), ("pop", 41), ("pop", 40)]
    assert len(rows) == 2 + len(parsed)


def test_build_rows_empty_history_is_just_actions():
    rows = nc.build_rows([], 0)
    assert len(rows) == 2
    assert rows[0][1] == ("toggle-dnd",) and rows[1][1] == ("clear",)


# --------------------------------------------------------------------------- #
# CLI paths via subprocess (offline: dunstctl absent/erroring -> fail-safe)
# --------------------------------------------------------------------------- #
def _run(script, *args, env=None):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=15, env=env)


def test_pill_standalone_emits_valid_json_and_never_crashes():
    # dunstctl may be present or absent on the test host; either way the pill
    # must exit 0 and print ONE valid i3status-rust json object.
    r = _run("i3status-notifs")
    assert r.returncode == 0
    obj = json.loads(r.stdout.strip())
    assert "text" in obj and "state" in obj


def test_notif_center_dump_lists_action_rows():
    r = _run("notif-center", "--dump")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert any(line.startswith("toggle-dnd\t") for line in lines)
    assert any(line.startswith("clear\t") for line in lines)
