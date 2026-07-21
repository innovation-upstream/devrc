"""Unit tests for espanso_triggers: parse the config into the trigger model.

Covers replace-before-trigger key ordering, snippets with/without search_terms,
`search_shortcut` parsing, and graceful handling of a missing file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import espanso_triggers as ET  # noqa: E402
import espanso_detect as ED  # noqa: E402

# A base.yml shaped exactly like home-manager renders it: `replace:` appears
# BEFORE `trigger:`, and some matches omit label / search_terms.
BASE = {
    "matches": [
        {  # replace-before-trigger ordering, full metadata
            "label": "Today's date",
            "replace": "{{mydate}}",
            "search_terms": ["today", "calendar"],
            "trigger": ":date",
        },
        {  # trigger with NO label and NO search_terms
            "replace": "dashboard",
            "trigger": "dashbaord",
        },
        {  # date+time — a superstring of :date, exercises the prefix collision
            "label": "Date and time",
            "replace": "{{mydt}}",
            "search_terms": ["timestamp"],
            "trigger": ":datetime",
        },
    ]
}
DEFAULT = {"backend": "Clipboard", "search_shortcut": "CTRL+SPACE"}


def test_parses_all_triggers_regardless_of_key_order():
    ts = ET.load_triggers(BASE, DEFAULT)
    assert set(ts.triggers) == {":date", "dashbaord", ":datetime"}
    assert ts.max_len == len(":datetime")


def test_meta_label_and_search_terms():
    ts = ET.load_triggers(BASE, DEFAULT)
    assert ts.meta[":date"]["label"] == "Today's date"
    assert ts.meta[":date"]["search_terms"] == ["today", "calendar"]
    assert ts.meta[":date"]["replace"] == "{{mydate}}"


def test_meta_defaults_when_missing():
    ts = ET.load_triggers(BASE, DEFAULT)
    # dashbaord has no label / search_terms in the config.
    assert ts.meta["dashbaord"]["label"] == ""
    assert ts.meta["dashbaord"]["search_terms"] == []
    assert ts.meta["dashbaord"]["replace"] == "dashboard"


def test_search_shortcut_ctrl_space():
    ts = ET.load_triggers(BASE, DEFAULT)
    ctrl, keysym = ts.search_shortcut
    assert ctrl is True
    assert keysym == 0x20  # space keysym


def test_search_shortcut_parse_variants():
    assert ET.parse_search_shortcut("CTRL+SPACE") == (True, 0x20)
    assert ET.parse_search_shortcut("ALT+SPACE") == (False, 0x20)
    assert ET.parse_search_shortcut("CTRL+E") == (True, ord("e"))
    # Unparseable / empty → the CTRL+SPACE default.
    assert ET.parse_search_shortcut("") == (True, 0x20)
    assert ET.parse_search_shortcut(None) == (True, 0x20)


def test_missing_file_yields_empty_no_raise():
    ts = ET.load_triggers("/no/such/base.yml", "/no/such/default.yml")
    assert ts.triggers == []
    assert ts.meta == {}
    # search_shortcut still defaults sanely (detector stays inert either way).
    assert ts.search_shortcut == (True, 0x20)
    assert ts.max_len == 0


def test_triggers_plural_list_supported():
    base = {"matches": [{"replace": "x", "triggers": [":a", ":b"]}]}
    ts = ET.load_triggers(base, {})
    assert set(ts.triggers) == {":a", ":b"}


def test_path_based_load_roundtrip(tmp_path):
    """A real YAML file on disk parses identically (needs PyYAML)."""
    import pytest
    if ET._yaml is None:
        pytest.skip("PyYAML not available in this interpreter")
    base_f = tmp_path / "base.yml"
    default_f = tmp_path / "default.yml"
    base_f.write_text(
        "matches:\n"
        "- replace: '{{mydate}}'\n"
        "  search_terms:\n  - today\n"
        "  trigger: ':date'\n"
        "  label: Today's date\n",
        encoding="utf-8",
    )
    default_f.write_text("search_shortcut: CTRL+SPACE\n", encoding="utf-8")
    ts = ET.load_triggers(str(base_f), str(default_f))
    assert ts.triggers == [":date"]
    assert ts.meta[":date"]["search_terms"] == ["today"]
    assert ts.search_shortcut == (True, 0x20)


# --------------------------------------------------------------------------- #
# SSH-connect snippets (:sshwn/:sshwl/:sshln/:sshll) — shaped exactly like
# home-manager renders them (`replace:` before `trigger:`), covering parse,
# direct-fire detection, and the by-design 2-way search ambiguity.
# --------------------------------------------------------------------------- #
SSH_BASE = {
    "matches": [
        {
            "label": "SSH workbench (nebula)",
            "replace": "ssh zach@10.42.0.30",
            "search_terms": ["ssh", "workbench", "wb", "nebula", "mesh", "remote"],
            "trigger": ":sshwn",
        },
        {
            "label": "SSH workbench (LAN)",
            "replace": "ssh zach@192.168.50.250",
            "search_terms": ["ssh", "workbench", "wb", "lan", "local"],
            "trigger": ":sshwl",
        },
        {
            "label": "SSH laptop (nebula)",
            "replace": "ssh zach@10.42.0.100",
            "search_terms": ["ssh", "laptop", "framework", "nebula", "mesh", "remote"],
            "trigger": ":sshln",
        },
        {
            "label": "SSH laptop (LAN)",
            "replace": "ssh zach@192.168.50.155",
            "search_terms": ["ssh", "laptop", "framework", "lan", "local"],
            "trigger": ":sshll",
        },
    ]
}

_SSH_EXPECTED = {
    ":sshwn": ("ssh zach@10.42.0.30", "SSH workbench (nebula)",
               ["ssh", "workbench", "wb", "nebula", "mesh", "remote"]),
    ":sshwl": ("ssh zach@192.168.50.250", "SSH workbench (LAN)",
               ["ssh", "workbench", "wb", "lan", "local"]),
    ":sshln": ("ssh zach@10.42.0.100", "SSH laptop (nebula)",
               ["ssh", "laptop", "framework", "nebula", "mesh", "remote"]),
    ":sshll": ("ssh zach@192.168.50.155", "SSH laptop (LAN)",
               ["ssh", "laptop", "framework", "lan", "local"]),
}


def test_ssh_snippets_parse_replace_label_search_terms():
    ts = ET.load_triggers(SSH_BASE, DEFAULT)
    assert set(ts.triggers) == set(_SSH_EXPECTED)
    for trig, (replace, label, terms) in _SSH_EXPECTED.items():
        assert ts.meta[trig]["replace"] == replace
        assert ts.meta[trig]["label"] == label
        assert ts.meta[trig]["search_terms"] == terms


def test_ssh_snippets_direct_fire():
    ts = ET.load_triggers(SSH_BASE, DEFAULT)
    for trig in _SSH_EXPECTED:
        det = ED.EspansoDetector(ts)
        events = []
        for ch in trig:
            events.extend(
                det.feed_char(ch, app="term", session="s1", now=0.0)
            )
        assert len(events) == 1, f"{trig} should fire exactly one event"
        ev = events[0]
        assert isinstance(ev, ED.EspansoEvent)
        assert ev.trigger == trig
        assert ev.method == "direct"
        assert ev.inferred is False


def test_ssh_search_attribution_is_ambiguous_by_design():
    # Both a nebula and a LAN snippet share "laptop"/"workbench"/"wb", so a
    # search on any of those terms matches TWO snippets. `_attribute` returns
    # None for >1 match — this is the INTENDED behaviour (the user is shown a
    # 2-item espanso picker to choose nebula vs LAN), NOT a bug.
    ts = ET.load_triggers(SSH_BASE, DEFAULT)
    det = ED.EspansoDetector(ts)
    assert det._attribute("laptop") is None      # :sshln + :sshll
    assert det._attribute("workbench") is None   # :sshwn + :sshwl
    assert det._attribute("wb") is None           # :sshwn + :sshwl
