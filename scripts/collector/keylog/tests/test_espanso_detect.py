"""Unit tests for EspansoDetector: direct triggers + Ctrl+Space search UI.

The detector reconstructs espanso firings from the raw keystroke stream (the
only place they are observable, since espanso erases both trigger and expansion).
These tests pin the documented semantics: prefix collisions, no double-emit on
espanso's trailing backspaces, focus-reset, and best-effort search attribution.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import espanso_triggers as ET       # noqa: E402
from espanso_detect import EspansoDetector  # noqa: E402

# A representative slice of the live config. :date is a prefix of :datetime
# (prefix-collision case); the workflow triggers carry labels/search_terms used
# by the fuzzy search-attribution tests.
BASE = {"matches": [
    {"label": "Today's date", "replace": "{{d}}",
     "search_terms": ["today", "calendar"], "trigger": ":date"},
    {"label": "Date and time", "replace": "{{dt}}",
     "search_terms": ["timestamp"], "trigger": ":datetime"},
    {"label": "Recommend next steps ranked by leverage", "replace": "...",
     "search_terms": ["ranked", "leverage"], "trigger": ":rnx"},
    {"label": "Dispatch subagent", "replace": "...",
     "search_terms": ["dispatch", "delegate"], "trigger": ":ds"},
]}
DEFAULT = {"search_shortcut": "CTRL+SPACE"}

APP, SESS = "kitty", "win-1"


def _det():
    return EspansoDetector(ET.load_triggers(BASE, DEFAULT))


def _type(det, s, *, app=APP, session=SESS, now=0.0):
    """Feed a string char-by-char, returning ALL emitted events."""
    out = []
    for i, ch in enumerate(s):
        out.extend(det.feed_char(ch, app=app, session=session, now=now + i))
    return out


# -- direct triggers ---------------------------------------------------------
def test_direct_trigger_at_buffer_end():
    d = _det()
    evs = _type(d, ":date")
    assert len(evs) == 1
    assert evs[0].trigger == ":date"
    assert evs[0].method == "direct"
    assert evs[0].inferred is False
    assert evs[0].app == APP and evs[0].session == SESS


def test_trigger_after_other_chars():
    d = _det()
    evs = _type(d, "foo:rnx")
    assert [e.trigger for e in evs] == [":rnx"]


def test_prefix_collision_emits_shorter_first():
    # Typing ":datetime" must emit ":date" (mirrors espanso firing on the prefix);
    # ":datetime" never forms because firing consumes the trigger.
    d = _det()
    evs = _type(d, ":datetime")
    assert [e.trigger for e in evs] == [":date"]


def test_backspaces_after_trigger_do_not_reemit_but_retype_does():
    d = _det()
    evs1 = _type(d, ":date")
    assert [e.trigger for e in evs1] == [":date"]
    # espanso backspaces the trigger away → no-ops on the (cleared) ring.
    evs2 = _type(d, "\b\b\b\b\b", now=10.0)
    assert evs2 == []
    # A genuinely retyped trigger fires again.
    evs3 = _type(d, ":date", now=20.0)
    assert [e.trigger for e in evs3] == [":date"]


def test_plain_typing_emits_nothing():
    d = _det()
    assert _type(d, "hello world, no triggers here") == []


def test_focus_change_resets_ring():
    d = _det()
    # ":da" under window A, then "te" under window B → the ":date" sequence is
    # broken across the focus boundary, so nothing fires.
    out = []
    for i, ch in enumerate(":da"):
        out.extend(d.feed_char(ch, app="A", session="wA", now=i))
    for i, ch in enumerate("te"):
        out.extend(d.feed_char(ch, app="B", session="wB", now=10 + i))
    assert out == []


# -- Ctrl+Space search UI ----------------------------------------------------
def test_search_open_term_then_enter_emits_one_search_event():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    assert _type(d, "today", now=1.0) == []          # accumulates, no emit yet
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=6.0))
    assert len(evs) == 1
    ev = evs[0]
    assert ev.method == "search"
    assert ev.inferred is True
    assert ev.search_term == "today"


def test_search_term_does_not_feed_direct_ring():
    # Typing ":date" WHILE in search-mode must NOT fire a direct event.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    evs = _type(d, ":date", now=1.0)
    assert evs == []  # captured as a search term, not a direct trigger


def test_search_fuzzy_unique_match_attributes():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "leverage", now=1.0)  # matches ONLY :rnx (label + search_terms)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=9.0))
    assert len(evs) == 1
    assert evs[0].trigger == ":rnx"
    assert evs[0].inferred is True
    assert evs[0].search_term == "leverage"


def test_search_fuzzy_zero_match_still_emits_with_term():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "zzzzz", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=6.0))
    assert len(evs) == 1
    assert evs[0].trigger is None
    assert evs[0].search_term == "zzzzz"


def test_search_fuzzy_multiple_match_is_ambiguous():
    # "date" is a substring of BOTH :date and :datetime → ambiguous → trigger None.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "date", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=5.0))
    assert len(evs) == 1
    assert evs[0].trigger is None
    assert evs[0].search_term == "date"


def test_search_flush_on_idle_without_enter():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "today", now=1.0)
    # No Enter; the idle sweep closes it.
    evs = d.flush_idle(now=100.0, idle_seconds=2.0)
    assert len(evs) == 1
    assert evs[0].method == "search"
    assert evs[0].search_term == "today"
    # Not idle yet → nothing.
    assert EspansoDetector(ET.load_triggers(BASE, DEFAULT)).flush_idle(0, 2) == []


def test_search_flush_on_focus_change_without_enter():
    d = _det()
    d.feed_search_open(app="A", session="wA", now=0.0)
    for i, ch in enumerate("today"):
        d.feed_char(ch, app="A", session="wA", now=1 + i)
    # Focus moves to another window before Enter → search closes and emits.
    evs = d.feed_char("x", app="B", session="wB", now=10.0)
    assert len(evs) == 1
    assert evs[0].method == "search"
    assert evs[0].search_term == "today"
    assert evs[0].app == "A"  # attributed to the window where search happened


def test_empty_trigger_set_is_inert():
    d = EspansoDetector(ET.TriggerSet())
    assert _type(d, ":date anything :rnx") == []
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    # An empty search close is a phantom → suppressed (no trigger=None row).
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=1.0))
    assert evs == []


# -- FIX 1: bounded search-mode + phantom-empty suppression ------------------
def test_search_term_over_cap_aborts_without_emitting():
    # A non-espanso Ctrl+Space that keeps typing past the cap is a misfire:
    # search-mode aborts silently (no method=search row for ordinary text).
    from espanso_detect import SEARCH_TERM_MAX
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    long_term = "x" * (SEARCH_TERM_MAX + 5)
    assert _type(d, long_term, now=1.0) == []
    # search-mode is off again; a subsequent close produces nothing.
    assert list(d.feed_char("\n", app=APP, session=SESS, now=200.0)) == []
    # And the direct ring is live again (typed text is no longer swallowed).
    assert [e.trigger for e in _type(d, ":date", now=300.0)] == [":date"]


def test_ctrl_space_then_empty_close_suppresses_phantom():
    # Accidental Ctrl+Space then Escape with nothing typed → NO phantom row.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    evs = list(d.feed_char("\x1b", app=APP, session=SESS, now=1.0))
    assert evs == []


def test_search_whitespace_only_close_suppressed():
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "   ", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=5.0))
    assert evs == []


def test_search_short_attributed_term_still_emits():
    # Short term that uniquely attributes → event as before (unchanged).
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "leverage", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=9.0))
    assert len(evs) == 1
    assert evs[0].trigger == ":rnx"
    assert evs[0].inferred is True


def test_search_short_unattributed_term_emits_trigger_none():
    # Short term matching nothing → legit "real search we couldn't attribute".
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "zzzzz", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=6.0))
    assert len(evs) == 1
    assert evs[0].trigger is None
    assert evs[0].inferred is True
    assert evs[0].search_term == "zzzzz"


# -- FIX 3: espanso search window steals X focus -----------------------------
# espanso's Ctrl+Space search bar opens as its OWN window (WM_CLASS
# .espanso-wrapped) and STEALS X focus. The FIRST query char therefore arrives
# under app=".espanso-wrapped", a focus change from the original window. That
# must NOT close search-mode with an empty term (the live bug: the search path
# produced ZERO events because search died before the query was typed).
ESPANSO_WIN = ".espanso-wrapped"


def test_focus_steal_by_espanso_window_keeps_search_and_attributes():
    # Ctrl+Space recorded under Alacritty; espanso's window then steals focus and
    # the query "leverage" is typed INTO .espanso-wrapped; Enter closes it.
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    # Query chars arrive under the STOLEN focus (espanso's own window).
    evs = _type(d, "leverage", app=ESPANSO_WIN, session="win-esp", now=1.0)
    assert evs == []  # accumulates, nothing emitted mid-term
    out = list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=9.0))
    assert len(out) == 1
    ev = out[0]
    assert ev.method == "search"
    assert ev.inferred is True
    assert ev.search_term == "leverage"
    assert ev.trigger == ":rnx"  # "leverage" uniquely maps to :rnx
    # Attributed to the ORIGIN window (where Ctrl+Space was pressed), NOT the
    # ".espanso-wrapped" window that stole focus mid-search.
    assert ev.app == "Alacritty"
    assert ev.session == "win-term"


def test_focus_steal_ambiguous_term_still_emits_trigger_none():
    # Same focus-steal path, but an ambiguous term ("date" ⊂ :date and :datetime)
    # → trigger=None yet the event is still emitted (real search, unattributed).
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    _type(d, "date", app=ESPANSO_WIN, session="win-esp", now=1.0)
    out = list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=6.0))
    assert len(out) == 1
    assert out[0].trigger is None
    assert out[0].search_term == "date"
    assert out[0].method == "search"
    assert out[0].app == "Alacritty"  # origin window preserved


def test_focus_steal_multichar_window_hop_accumulates_full_term():
    # Every char of the term lands under the espanso window (no per-char focus
    # thrash re-closing search); the FULL term survives to attribution.
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    for i, ch in enumerate("clarify"):
        assert d.feed_char(ch, app=ESPANSO_WIN, session="win-esp", now=1 + i) == []
    out = list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=20.0))
    assert len(out) == 1
    assert out[0].search_term == "clarify"
    assert out[0].app == "Alacritty"  # origin window preserved


def test_focus_change_to_nonespanso_window_still_closes_search():
    # A focus change to a genuinely DIFFERENT non-espanso window (e.g. the
    # browser) is a real abandon: search closes under the OLD context and does
    # NOT keep accumulating into the new window.
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    _type(d, "today", app="Alacritty", session="win-term", now=1.0)
    # Focus jumps to Brave before Enter → close under the old context.
    out = list(d.feed_char("x", app="Brave-browser", session="win-brave", now=10.0))
    assert len(out) == 1
    assert out[0].method == "search"
    assert out[0].search_term == "today"  # the "x" did NOT get appended
    assert out[0].app == "Alacritty"      # closed under the window search began in
    # Search-mode is off; the stray "x" and a following Enter emit nothing more.
    assert list(d.feed_char("\n", app="Brave-browser", session="win-brave", now=11.0)) == []


def test_espanso_focus_steal_does_not_break_direct_fires():
    # Regression: after a focus-steal search completes, direct triggers under a
    # normal window still fire (search state fully reset).
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    _type(d, "leverage", app=ESPANSO_WIN, session="win-esp", now=1.0)
    list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=9.0))
    evs = _type(d, ":date", app="Alacritty", session="win-term", now=20.0)
    assert [e.trigger for e in evs] == [":date"]


def test_empty_close_still_suppressed_under_espanso_focus_steal():
    # Focus steal to the espanso window, then Escape with nothing typed → still a
    # phantom empty close → suppressed (prior fix preserved through this path).
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    out = list(d.feed_char("\x1b", app=ESPANSO_WIN, session="win-esp", now=1.0))
    assert out == []


# -- FIX 2: caret-navigation resets the direct ring --------------------------
def test_notify_navigation_resets_direct_ring():
    # ":da" then a caret move then "te" → ":date" was NOT typed contiguously,
    # so nothing fires (mirrors espanso resetting its buffer on nav keys).
    d = _det()
    assert _type(d, ":da") == []
    d.notify_navigation()
    evs = _type(d, "te", now=10.0)
    assert evs == []


def test_notify_navigation_leaves_search_mode_intact():
    # A nav key during search must NOT drop the accumulated term.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "today", now=1.0)
    d.notify_navigation()
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=8.0))
    assert len(evs) == 1
    assert evs[0].search_term == "today"


# -- FIX 3: MULTI-WORD search terms ------------------------------------------
# The /espanso-audit run of 2026-08-05 found 46 of 173 keylog espanso rows
# unattributed, and 19+ of them were MULTI-WORD queries: "ssh work", "ssh lap",
# "ss wor", "ssh worc", "civit prod". `_term_matches` required the whole term to
# be a substring of a SINGLE word (of the trigger, of a label word, or of one
# search_term), so a term containing a space could never match ANY snippet — the
# four :ssh* snippets and :cgf/:subk therefore read as dead when they are not.
# Fix: tokenize on whitespace and require EVERY token to match by the old rules.
# These fixtures carry the REAL labels/search_terms from nix/home.nix.
SSH_BASE = {"matches": [
    {"label": "SSH workbench (nebula)", "replace": "...", "trigger": ":sshwn",
     "search_terms": ["ssh", "workbench", "wb", "nebula", "mesh", "remote"]},
    {"label": "SSH workbench (LAN)", "replace": "...", "trigger": ":sshwl",
     "search_terms": ["ssh", "workbench", "wb", "lan", "local"]},
    {"label": "SSH laptop (nebula)", "replace": "...", "trigger": ":sshln",
     "search_terms": ["ssh", "laptop", "framewo", "nebula", "mesh", "remote"]},
    {"label": "SSH laptop (LAN)", "replace": "...", "trigger": ":sshll",
     "search_terms": ["ssh", "laptop", "framewo", "lan", "local"]},
]}

CIVIT_BASE = {"matches": [
    {"label": "civitai main web app repo path", "replace": "...", "trigger": ":cc",
     "search_terms": ["civitai", "repo", "web", "app"]},
    {"label": "civitai datapacket-talos path", "replace": "...", "trigger": ":cdp",
     "search_terms": ["civitai"]},
    {"label": "civitai gpu-fleet path", "replace": "...", "trigger": ":cgf",
     "search_terms": ["civitai"]},
    {"label": "civitai dp prod kubeconfig path", "replace": "...", "trigger": ":cpk",
     "search_terms": ["civitai"]},
    {"label": "civitai submodel dc 03 kubeconfig path", "replace": "...",
     "trigger": ":subk",
     "search_terms": ["civitai", "gpu", "submodel", "dc 03"]},
]}


def _det_for(base):
    return EspansoDetector(ET.load_triggers(base, DEFAULT))


def test_multiword_term_matches_each_workbench_ssh_snippet():
    # RED before the fix: "ssh work" is not a substring of any single word, so
    # _term_matches returned False for EVERY trigger. Now both workbench
    # snippets match ("ssh" ⊂ the trigger, "work" ⊂ label word "workbench").
    d = _det_for(SSH_BASE)
    assert d._term_matches("ssh work", ":sshwn") is True
    assert d._term_matches("ssh work", ":sshwl") is True
    # ...and the LAPTOP ones legitimately do not — no "work" anywhere in them.
    assert d._term_matches("ssh work", ":sshln") is False
    assert d._term_matches("ssh work", ":sshll") is False


def test_multiword_ssh_work_stays_ambiguous_by_design():
    # HONEST result: "ssh work" now matches TWO snippets (nebula + LAN), and
    # _attribute's "exactly one match else None" rule is deliberately unchanged,
    # so the row still lands trigger=None. The win is that the term is no longer
    # a GUARANTEED zero-match — a disambiguating token resolves it (below).
    d = _det_for(SSH_BASE)
    assert d._attribute("ssh work") is None
    assert d._attribute("ssh work lan") == ":sshwl"
    assert d._attribute("ssh work nebula") == ":sshwn"


def test_multiword_ssh_lap_matches_only_laptop_snippets():
    d = _det_for(SSH_BASE)
    assert d._term_matches("ssh lap", ":sshln") is True
    assert d._term_matches("ssh lap", ":sshll") is True
    assert d._term_matches("ssh lap", ":sshwn") is False


def test_multiword_civit_prod_attributes_uniquely():
    # RED before the fix (returned None). "civit" ⊂ "civitai", "prod" ⊂ the
    # label word "prod" — and ONLY :cpk carries "prod", so it resolves.
    d = _det_for(CIVIT_BASE)
    assert d._attribute("civit prod") == ":cpk"


def test_multiword_end_to_end_through_search_ui():
    d = _det_for(CIVIT_BASE)
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "civit prod", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=20.0))
    assert len(evs) == 1
    assert evs[0].trigger == ":cpk"
    assert evs[0].search_term == "civit prod"
    assert evs[0].method == "search" and evs[0].inferred is True


def test_multiword_all_tokens_required():
    # Every token must match — one bogus token disqualifies the snippet.
    d = _det_for(CIVIT_BASE)
    assert d._term_matches("civit zzzzz", ":cpk") is False
    assert d._attribute("civit zzzzz") is None


def test_single_word_term_behaviour_unchanged():
    # The single-token path must be byte-for-byte the old semantics.
    d = _det_for(CIVIT_BASE)
    assert d._term_matches("prod", ":cpk") is True
    assert d._term_matches("prod", ":cc") is False
    assert d._attribute("prod") == ":cpk"
    assert d._attribute("civitai") is None      # matches all 5 → ambiguous
    assert d._attribute("submodel") == ":subk"
    # ...and on the original fixture set too.
    d2 = _det()
    assert d2._attribute("leverage") == ":rnx"
    assert d2._attribute("date") is None
    assert d2._attribute("zzzzz") is None


def test_multiword_term_with_extra_whitespace_is_tokenized():
    d = _det_for(CIVIT_BASE)
    assert d._attribute("  civit   prod  ") == ":cpk"


# --------------------------------------------------------------------------- #
# LIVE-CONFIG guards — these read nix/home.nix, NOT a fixture.
#
# Everything above pins detector SEMANTICS against hand-written fixtures. That
# is structurally blind to the config itself: a fixture keeps asserting whatever
# was true the day it was typed, so deleting a snippet's search_terms in
# nix/home.nix leaves every test above green. But `label` + `search_terms` ARE
# the interface — 168 of 173 measured fires go through the Ctrl+Space search UI
# — so a snippet whose terms stop resolving is a DEAD snippet, and the next
# /espanso-audit prunes it for recording zero fires.
#
# So these guards parse the real file. It is scraped, not nix-evaluated: every
# snippet is a single-line `{ trigger = "…"; … }` record, and a regex over that
# needs no nix binary in the sandbox. A MISSING file FAILS rather than skips —
# the whole point is that this cannot go quietly green.
# --------------------------------------------------------------------------- #
import re  # noqa: E402
import pytest  # noqa: E402

HOME_NIX = Path(__file__).resolve().parents[4] / "nix" / "home.nix"

_NIX_REC = re.compile(r"^\s*\{\s*trigger\s*=\s*\"((?:[^\"\\]|\\.)*)\"\s*;")
_NIX_LABEL = re.compile(r"label\s*=\s*\"((?:[^\"\\]|\\.)*)\"\s*;")
_NIX_REPLACE = re.compile(r"replace\s*=\s*\"((?:[^\"\\]|\\.)*)\"\s*;")
_NIX_TERMS = re.compile(r"search_terms\s*=\s*\[([^\]]*)\]")
_NIX_STR = re.compile(r"\"((?:[^\"\\]|\\.)*)\"")


def _live_base():
    """The espanso match set as written in nix/home.nix, in base.yml dict shape.

    CAVEAT: `replace` here is the RAW nix source, so a path snippet reads
    "${workspace}/…" pre-interpolation. That is fine — the detector never
    consults `replace` (see `_token_matches`), and the fields it DOES consult
    (trigger, label, search_terms) were verified byte-identical to the base.yml
    nix actually generates (2026-08-05, via `nix build …#homeConfigurations.zach
    .activationPackage` + espanso_triggers.load_triggers over the emitted YAML).
    """
    if not HOME_NIX.exists():
        pytest.fail(f"nix/home.nix not found at {HOME_NIX} — this guard cannot "
                    f"be allowed to skip; fix the path.")
    matches = []
    for line in HOME_NIX.read_text(encoding="utf-8").splitlines():
        m = _NIX_REC.match(line)
        if not m:
            continue
        lab = _NIX_LABEL.search(line)
        rep = _NIX_REPLACE.search(line)
        ter = _NIX_TERMS.search(line)
        matches.append({
            "trigger": m.group(1),
            "replace": rep.group(1) if rep else "",
            "label": lab.group(1) if lab else "",
            "search_terms": _NIX_STR.findall(ter.group(1)) if ter else [],
        })
    return {"matches": matches}


def _live_det():
    return EspansoDetector(ET.load_triggers(_live_base(), DEFAULT))


def test_live_scraper_observes_the_real_config():
    """POSITIVE CONTROL for the scraper the three guards below depend on.

    Without this, a regex that silently matched NOTHING would make every guard
    below vacuously true (empty trigger set → nothing to contradict). Pin a
    non-trivial count and two long-standing snippets with known metadata.
    """
    base = _live_base()
    trigs = [m["trigger"] for m in base["matches"]]
    assert len(trigs) >= 20, f"scraper found only {len(trigs)} snippets: {trigs}"
    assert len(set(trigs)) == len(trigs), "duplicate trigger in nix/home.nix"
    assert ":date" in trigs and "dashbaord" in trigs
    by_trig = {m["trigger"]: m for m in base["matches"]}
    assert by_trig[":date"]["search_terms"] == ["today", "calendar"]
    assert by_trig[":kickoff"]["label"] == "Kickoff message for next session"


# The queries the 2026-08-05 /espanso-audit measured him actually typing (plus
# the single-word prefixes the search bar sees mid-type). EVERY one must land on
# :mt alone. Goes RED if :mt's search_terms are removed, or if a future snippet
# makes any of them ambiguous.
_MT_TERMS = [
    "meantime", "mean", "while", "parallel", "queue", "tee", "wait",
    "blocked", "idle", "tee up", "queue up", "in the meantime",
    "what can we do", "while that runs",
]


def test_live_mt_search_terms_all_resolve_to_mt():
    d = _live_det()
    unresolved = {}
    for term in _MT_TERMS:
        got = d._attribute(term)
        if got != ":mt":
            competing = [t for t in d.ts.triggers if d._term_matches(term, t)]
            unresolved[term] = (got, competing)
    assert not unresolved, (
        "these search terms no longer resolve to :mt "
        "(term -> (attributed, competing snippets)): " + repr(unresolved)
    )
    # "up" alone is a substring of :eos's search_term "update", so the two-token
    # queries are the ones that could silently drift — assert them explicitly.
    assert d._attribute("tee up") == ":mt"
    assert d._attribute("queue up") == ":mt"
    # ...and the trigger really is present with the label leading on "Meantime".
    assert ":mt" in d.ts.triggers
    assert d.ts.meta[":mt"]["label"].lower().startswith("meantime")


# Resolutions that held BEFORE :mt existed and must still hold. A new snippet's
# search_terms are the classic way to make an existing one ambiguous.
_EXISTING_RESOLUTIONS = [
    ("rit", ":eos"), ("kick", ":kickoff"), ("kic", ":kickoff"),
    ("recom", ":rna"), ("recommend", ":rna"),
    ("limit", ":lr"), ("resume", ":lr"), ("restored", ":lr"),
    ("feedback", ":acq"), ("dispatch", ":acq"), ("process", ":acq"),
    ("cc", ":cc"), ("kubecl", ":kuc"), ("spine", ":csc"), ("orch", ":cmo"),
    ("home", ":hlt"), ("prod", ":cpk"), ("datap", ":cdp"), ("civit prod", ":cpk"),
]


def test_live_existing_resolutions_not_made_ambiguous():
    d = _live_det()
    bad = {}
    for term, want in _EXISTING_RESOLUTIONS:
        got = d._attribute(term)
        if got != want:
            bad[term] = (want, got, [t for t in d.ts.triggers if d._term_matches(term, t)])
    assert not bad, (
        "search terms regressed (term -> (expected, actual, matching snippets)): "
        + repr(bad)
    )


def test_live_triggers_have_no_prefix_or_suffix_collisions():
    """espanso longest-matches; the detector emits the SHORTEST match.

    Where one trigger is a prefix or suffix of another the two disagree, so the
    keylog signal misattributes. Pin zero such pairs — and prove the checker can
    SEE one, so the zero is a real zero and not a check wired to nothing.
    """
    def pairs(trigs):
        found = set()
        for a in trigs:
            for b in trigs:
                if a == b:
                    continue
                if a.startswith(b):
                    found.add((b, a, "prefix"))
                if a.endswith(b):
                    found.add((b, a, "suffix"))
        return found

    live = [m["trigger"] for m in _live_base()["matches"]]
    # POSITIVE CONTROL: seed a known-bad set and require the checker to report it.
    control = pairs(live + [":m", "x:mt"])
    assert (":m", ":mt", "prefix") in control and (":mt", "x:mt", "suffix") in control, (
        "collision checker failed its positive control — its zero below would "
        "mean nothing"
    )
    assert pairs(live) == set(), f"colliding triggers in nix/home.nix: {pairs(live)}"
