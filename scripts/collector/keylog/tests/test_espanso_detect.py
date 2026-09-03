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
import espanso_detect as ED         # noqa: E402
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
    # "dat" is a substring of BOTH :date and :datetime → ambiguous → trigger None.
    # It deliberately NAMES neither: the 2026-08-28 exact-name tie-break resolves
    # a term that IS one of the candidate triggers (so bare "date" now lands on
    # :date), and this test is about the ambiguity path, not about that.
    d = _det()
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "dat", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=5.0))
    assert len(evs) == 1
    assert evs[0].trigger is None
    assert evs[0].search_term == "dat"


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
    # Same focus-steal path, but an ambiguous term ("dat" ⊂ :date and :datetime,
    # and NAMING neither — see the exact-name note on the ambiguity test above)
    # → trigger=None yet the event is still emitted (real search, unattributed).
    d = _det()
    d.feed_search_open(app="Alacritty", session="win-term", now=0.0)
    _type(d, "dat", app=ESPANSO_WIN, session="win-esp", now=1.0)
    out = list(d.feed_char("\n", app=ESPANSO_WIN, session="win-esp", now=6.0))
    assert len(out) == 1
    assert out[0].trigger is None
    assert out[0].search_term == "dat"
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
    # The single-token MATCHING path is byte-for-byte the old semantics (the
    # 2026-08-05 multi-word fix changed nothing here). Attribution has since
    # gained one tie-break on the AMBIGUOUS branch only — pinned explicitly
    # below, alongside a term that names nothing and stays ambiguous.
    d = _det_for(CIVIT_BASE)
    assert d._term_matches("prod", ":cpk") is True
    assert d._term_matches("prod", ":cc") is False
    assert d._attribute("prod") == ":cpk"
    assert d._attribute("civitai") is None      # matches all 5 → ambiguous
    assert d._attribute("submodel") == ":subk"
    # ...and on the original fixture set too.
    d2 = _det()
    assert d2._attribute("leverage") == ":rnx"
    # "dat" matches :date AND :datetime and names neither → still ambiguous.
    assert d2._attribute("dat") is None
    # Bare "date" also matches both, but it NAMES :date, so the 2026-08-28
    # exact-name tie-break takes it. This asserted None before that fix; it is
    # the one single-token outcome that moved, and only on the branch that used
    # to return None. Recorded here rather than deleted so the change is visible.
    assert d2._attribute("date") == ":date"
    assert d2._attribute("zzzzz") is None


def test_multiword_term_with_extra_whitespace_is_tokenized():
    d = _det_for(CIVIT_BASE)
    assert d._attribute("  civit   prod  ") == ":cpk"


# -- FIX 4: a term that NAMES one of the snippets it matches ------------------
# 2026-08-28. `:acq` was SPLIT into `:dacq` (dispatch) + `:acq` (the bare ask).
# Matching is by SUBSTRING, so the bare term "acq" then matched BOTH
# (`"acq" in ":dacq"` is True) and `_attribute` returned None — every such fire
# recorded UNATTRIBUTED, live on both hosts. Measured across three trees via the
# module's own scraper: unique at 7aff8471 (pre-split), AMBIGUOUS at 4f1e4c56
# (the split) and still AMBIGUOUS at d1a9fd5e.
#
# The fix is STRUCTURAL, not a rename: when several snippets match and exactly
# one of them is NAMED outright by the term (the term IS that trigger, with or
# without the ':'), that one wins. It closes the whole `:dX` / `:X` class rather
# than this instance. `test_live_bare_trigger_names_resolve_to_their_own_snippet`
# is the live-config half of the same claim.
#
# 🔴 WHICH OF THESE ARE REGRESSION COVERAGE. Measured against 6349a8b9 (the tree
# the bug is live on), with the tests below in place and only the fix reverted:
#   RED at base  — test_bare_acq_resolves_to_acq_not_ambiguous,
#                  test_acq_end_to_end_through_search_ui,
#                  test_live_bare_trigger_names_resolve_to_their_own_snippet
#   GREEN at base — the other four here. They are INVARIANT GUARDS, not
#                  regression coverage: they pin what the tie-break must NOT do
#                  (re-point a unique match, break the picker, break the direct
#                  path, resolve a term naming TWO snippets). Each was
#                  mutation-checked instead — see the PR body.
#
# These fixtures carry the REAL triggers/labels/search_terms from nix/home.nix.
# 🔴 2026-08-29: `:dacq` regained "ask" + "clarifying" there (deliberately — see
# `_AMBIGUOUS_TERM_OWNER`), so this fixture was updated to match. Keeping the
# older, narrower list would have left every test below green while the live
# config had gone ambiguous: a fixture that has drifted from the file it claims
# to mirror asserts the past, and reads as coverage of the present.
ACQ_BASE = {"matches": [
    {"trigger": ":dacq", "replace": "...",
     "label": "Process feedback: dispatch subagent + elicit scope",
     "search_terms": ["ask", "clarifying", "feedback", "dispatch", "process",
                      "elicit", "scope", "include"]},
    # 🔴 2026-09-02: the LABEL grew "and recommend improvements and anything
    # useful to include" in a720d30d. Mirrored here for the same reason the
    # 2026-08-29 note above gives — a fixture that lags the file it claims to
    # mirror asserts the past. This label is exactly what made 'recom' ambiguous
    # against ':rna' on the live config; see the FIX 6 block below.
    {"trigger": ":acq", "replace": "...",
     "label": "ask clarifying questions and recommend improvements and "
              "anything useful to include",
     "search_terms": ["ask", "clarify", "clarifying", "questions"]},
]}


def test_bare_acq_resolves_to_acq_not_ambiguous():
    # RED before the fix: _attribute returned None because "acq" ⊂ ":dacq".
    d = _det_for(ACQ_BASE)
    assert d._term_matches("acq", ":acq") is True
    assert d._term_matches("acq", ":dacq") is True   # the substring collision
    assert d._attribute("acq") == ":acq"
    # The colon-spelled form was never ambiguous and must stay put.
    assert d._attribute(":acq") == ":acq"
    # ...and the OTHER side of the split is untouched, by either spelling.
    assert d._attribute("dacq") == ":dacq"
    assert d._attribute(":dacq") == ":dacq"


def test_acq_split_interface_words_still_resolve():
    # The tie-break must not swallow the words that already resolved uniquely.
    d = _det_for(ACQ_BASE)
    for term, want in [("ask", ":acq"), ("clarify", ":acq"),
                       ("questions", ":acq"), ("feedback", ":dacq"),
                       ("dispatch", ":dacq"), ("elicit", ":dacq")]:
        assert d._attribute(term) == want, term


def test_naming_tiebreak_only_fires_on_the_ambiguous_branch():
    # A term matching several snippets and naming NONE of them stays None — the
    # tie-break is not "pick the shortest" or "pick the first".
    d = _det_for(ACQ_BASE)
    assert sorted(t for t in d.ts.triggers if d._term_matches("ac", t)) == [
        ":acq", ":dacq"]
    assert d._attribute("ac") is None
    # A multi-token term cannot name a trigger, so it is unaffected.
    assert d._attribute("acq zzzzz") is None


def test_naming_two_triggers_at_once_stays_ambiguous():
    # `exactly one` is load-bearing: with both "acq" and ":acq" configured, the
    # term names BOTH, so there is no winner and None is the honest answer.
    # (Mutating `len(exact) == 1` to `if exact` is what this catches.)
    both = {"matches": [
        {"trigger": ":acq", "replace": "...", "label": "colon form",
         "search_terms": []},
        {"trigger": "acq", "replace": "...", "label": "bare form",
         "search_terms": []},
    ]}
    d = _det_for(both)
    assert d._attribute("acq") is None


def test_acq_end_to_end_through_search_ui():
    # The whole point: a real Ctrl+Space search for "acq" must now be ATTRIBUTED
    # rather than land as an unattributed row in activity.events.
    d = _det_for(ACQ_BASE)
    d.feed_search_open(app=APP, session=SESS, now=0.0)
    _type(d, "acq", now=1.0)
    evs = list(d.feed_char("\n", app=APP, session=SESS, now=9.0))
    assert len(evs) == 1
    assert evs[0].trigger == ":acq"
    assert evs[0].search_term == "acq"
    assert evs[0].method == "search" and evs[0].inferred is True


def test_acq_split_direct_triggers_are_unaffected():
    # The DIRECT path is a different mechanism (ring-endswith, shortest match)
    # and ':dacq' does not end with ':acq', so typing either fires itself.
    d = _det_for(ACQ_BASE)
    assert [e.trigger for e in _type(d, "hello :acq")] == [":acq"]
    d2 = _det_for(ACQ_BASE)
    assert [e.trigger for e in _type(d2, "hello :dacq")] == [":dacq"]


def test_naming_tiebreak_does_not_reach_the_picker():
    # Attribution and the espanso PICKER are different questions: the picker
    # lists every match, and this fix must not remove a row. `_term_matches` is
    # what the picker guard reads, and it is untouched.
    d = _det_for(ACQ_BASE)
    assert {t for t in d.ts.triggers if d._term_matches("acq", t)} == {
        ":acq", ":dacq"}


# -- FIX 5: a term two snippets spell ON PURPOSE ------------------------------
# 2026-08-29. `:dacq` regained "ask" and "clarifying" in nix/home.nix, because
# that is how the dispatch snippet is actually searched for. `_names_trigger`
# cannot rescue either term (neither snippet is NAMED "ask"), so both went to
# None and the live guard went red. The two previous responses both deleted the
# terms from `:dacq` — buying a telemetry row with a real picker route, and
# reverted by hand each time.
#
# The fix is a DECLARED owner (`_AMBIGUOUS_TERM_OWNER`), consulted only on the
# already-ambiguous branch and only over the snippets the term genuinely
# reaches. The tests below pin both halves: that it resolves the declared case,
# and — the part that keeps it from becoming "guess something" — that an
# ambiguous term with NO declaration still returns None.
def test_declared_owner_resolves_a_term_both_snippets_spell():
    # RED without _AMBIGUOUS_TERM_OWNER: both are None, because ':dacq' spells
    # "ask" outright and "clarify" ⊂ its "clarifying".
    d = _det_for(ACQ_BASE)
    assert d._term_matches("ask", ":dacq") is True     # the deliberate overlap
    assert d._term_matches("ask", ":acq") is True
    assert d._attribute("ask") == ":acq"
    assert d._term_matches("clarify", ":dacq") is True  # via "clarifying"
    assert d._attribute("clarify") == ":acq"


def test_declared_owner_does_not_reach_the_picker():
    # The whole reason the table exists: BOTH rows must still list. If this ever
    # shrinks to one, the fix has re-made the mistake it was written to undo.
    d = _det_for(ACQ_BASE)
    assert {t for t in d.ts.triggers if d._term_matches("ask", t)} == {
        ":acq", ":dacq"}
    assert {t for t in d.ts.triggers if d._term_matches("clarify", t)} == {
        ":acq", ":dacq"}


def test_undeclared_ambiguous_term_still_resolves_to_none():
    # 🔴 The honesty guard. Mutating the owner lookup to a positional pick
    # (`matched[0]`, "shortest trigger", "first declared") passes every test
    # above and fails here. Silence in the table is NOT a licence to guess.
    d = _det_for(ACQ_BASE)
    assert sorted(t for t in d.ts.triggers if d._term_matches("ac", t)) == [
        ":acq", ":dacq"]
    assert "ac" not in ED._AMBIGUOUS_TERM_OWNER
    assert d._attribute("ac") is None


def test_declared_owner_cannot_invent_a_resolution(monkeypatch):
    # `owner in matched` is load-bearing: a stale entry — a snippet renamed, or
    # a term that no longer reaches it — must go INERT, never point at a snippet
    # the term does not match. Deleting that clause turns this red.
    monkeypatch.setitem(ED._AMBIGUOUS_TERM_OWNER, "ac", ":zzz-not-a-trigger")
    d = _det_for(ACQ_BASE)
    assert ":zzz-not-a-trigger" not in d.ts.triggers
    assert d._attribute("ac") is None


def test_declared_owner_never_repoints_a_unique_match(monkeypatch):
    # 🔴 AN INVARIANT GUARD, NOT REGRESSION COVERAGE — labelled as one because a
    # mutation sweep proved it cannot fail on its own. `owner in matched` makes
    # this property STRUCTURAL: if the owner is among the matches and there is
    # only one match, the owner IS that match, so consulting the table earlier
    # is a no-op. Measured — moving the lookup above the uniqueness check (M4)
    # and above `_names_trigger` (M5) both left this GREEN; the guard that
    # actually killed them is
    # `test_naming_a_trigger_still_beats_the_declared_owner`, and the one that
    # kills a dropped `owner in matched` is
    # `test_declared_owner_cannot_invent_a_resolution`.
    # It stays because it documents the invariant those two rest on. Do not
    # count it as evidence the ordering is tested.
    d = _det_for(ACQ_BASE)
    assert d._attribute("questions") == ":acq"
    monkeypatch.setitem(ED._AMBIGUOUS_TERM_OWNER, "questions", ":dacq")
    assert d._attribute("questions") == ":acq"


def test_naming_a_trigger_still_beats_the_declared_owner(monkeypatch):
    # Precedence: a term that IS a trigger name is a stronger signal than a
    # hand-declared owner, so `_names_trigger` must be consulted first. Swapping
    # the two blocks in `_attribute` turns this red.
    monkeypatch.setitem(ED._AMBIGUOUS_TERM_OWNER, "acq", ":dacq")
    d = _det_for(ACQ_BASE)
    assert d._attribute("acq") == ":acq"


# -- FIX 6: an incidental LABEL word colliding with another snippet's interface -
# 2026-09-02. `a720d30d` — a one-line direct-to-main commit — gave ':acq' the
# label "ask clarifying questions and recommend improvements and anything useful
# to include". `_token_matches` reads the label, so 'recom'/'recommend' began
# matching ':acq' as well as ':rna' and BOTH went to None, turning
# `test_live_existing_resolutions_not_made_ambiguous` red on `main`.
#
# The operator's ruling was to keep the label and relax the gate, so #1247's fix
# was a DECLARED owner — the same hatch as FIX 5. The asymmetry that made ':rna'
# the honest owner: 'recom' and 'recommend' are two of its six declared
# `search_terms` and its label is "Recommend next actions", while on ':acq' the
# word is incidental prose. Neither snippet is NAMED by either term, so
# `_names_trigger` cannot rescue them.
#
# 🔴 THAT ASYMMETRY IS NOW A RULE, NOT TWO TABLE ROWS — see FIX 7 below. The two
# entries have been DELETED: with declared-interface precedence in `_attribute`
# they are unreachable (the declared-narrowed candidate set for these terms is a
# single snippet, so `_attribute` returns before the owner lookup), and this repo
# does not keep entries nothing can reach. The tests in this section therefore
# still pin the RESOLUTION — which is the contract that matters and must not
# regress — while no longer claiming the table is what produces it.
#
# 🔴 WHICH OF THESE ARE REGRESSION COVERAGE, measured at 778dbd2d (the tree the
# bug is live on) with these tests in place and only the two table entries
# removed:
#   RED at base  — test_precedence_resolves_the_recommend_terms,
#                  test_recommend_terms_resolve_on_the_live_config
#   GREEN at base — test_recommend_terms_still_reach_both_picker_rows (an
#                  INVARIANT GUARD: the picker never consulted the table, so it
#                  cannot fail on its own). Mutation-checked instead.
RNA_ACQ_BASE = {"matches": [
    {"trigger": ":acq", "replace": "...",
     "label": "ask clarifying questions and recommend improvements and "
              "anything useful to include",
     "search_terms": ["ask", "clarify", "clarifying", "questions"]},
    {"trigger": ":rna", "replace": "...", "label": "Recommend next actions",
     "search_terms": ["recommend", "recom", "next", "actions", "rank",
                      "leverage"]},
]}


def test_precedence_resolves_the_recommend_terms():
    # RED on the pre-precedence detector with the two ':rna' table entries
    # removed: both are None, because ':acq''s label spells "recommend" and
    # 'recom' is a substring of it. GREEN here with NO table entry for either.
    d = _det_for(RNA_ACQ_BASE)
    assert "recom" not in ED._AMBIGUOUS_TERM_OWNER
    assert "recommend" not in ED._AMBIGUOUS_TERM_OWNER
    assert d._term_matches("recom", ":acq") is True      # the incidental label
    assert d._term_matches("recom", ":rna") is True      # the real interface
    assert d._names_trigger("recom", ":rna") is False    # no outright naming
    assert d._names_trigger("recom", ":acq") is False
    assert d._attribute("recom") == ":rna"
    assert d._attribute("recommend") == ":rna"


def test_recommend_terms_still_reach_both_picker_rows():
    # The point of the table: attribution gets one answer, the picker keeps BOTH
    # rows. If this shrinks to one, the fix has become the label edit the
    # operator declined.
    d = _det_for(RNA_ACQ_BASE)
    for term in ("recom", "recommend"):
        assert {t for t in d.ts.triggers if d._term_matches(term, t)} == {
            ":acq", ":rna"}, term


def test_the_recommend_terms_owe_nothing_to_the_owner_table(monkeypatch):
    """🔴 THE LOAD-BEARING CONTROL, made permanent.

    #1247 resolved these two terms with `_AMBIGUOUS_TERM_OWNER` entries. FIX 7
    deleted them, claiming precedence does the work on its own. This proves the
    claim can never quietly stop being true: point the table at a DIFFERENT
    snippet for both terms and the answer must still be ':rna'.

    It is not a restatement of the test above — that one runs with an empty
    table, this one runs with a table actively arguing for the wrong answer, so
    it fails if precedence is ever moved BELOW the owner lookup rather than
    above it. (Ordering, not existence, is what it pins.)
    """
    d = _det_for(RNA_ACQ_BASE)
    for term in ("recom", "recommend"):
        monkeypatch.setitem(ED._AMBIGUOUS_TERM_OWNER, term, ":acq")
    for term in ("recom", "recommend"):
        assert ED._AMBIGUOUS_TERM_OWNER[term] == ":acq"
        assert ":acq" in [t for t in d.ts.triggers if d._term_matches(term, t)]
        assert d._attribute(term) == ":rna", term


def test_recommend_terms_resolve_on_the_live_config():
    # The hermetic fixture above can drift from nix/home.nix; this reads the
    # real file. ANTI-VACUITY: an empty trigger set would pass vacuously, so
    # assert both snippets are actually present and actually collide.
    d = _live_det()
    assert ":acq" in d.ts.triggers and ":rna" in d.ts.triggers
    # ...and on the LIVE config too, no owner entry is doing this (FIX 7).
    assert "recom" not in ED._AMBIGUOUS_TERM_OWNER
    assert "recommend" not in ED._AMBIGUOUS_TERM_OWNER
    for term in ("recom", "recommend"):
        assert sorted(t for t in d.ts.triggers
                      if d._term_matches(term, t)) == [":acq", ":rna"], term
        assert d._attribute(term) == ":rna", term
    # The collision comes from the LABEL, not from ':acq''s search_terms — if
    # that stops being true this test is no longer covering the reported bug.
    assert "recommend" in d.ts.meta[":acq"]["label"].lower()
    assert not any("recom" in s.lower()
                   for s in d.ts.meta[":acq"].get("search_terms") or [])


def test_every_declared_owner_names_a_real_live_trigger():
    """A typo or a renamed snippet leaves an entry that can never fire.

    ANTI-VACUITY: this reads the LIVE config, so an empty trigger set would make
    it pass with nothing checked — `_live_det` is pinned non-trivial by
    `test_live_scraper_observes_the_real_config`, and asserted again here.
    """
    d = _live_det()
    assert len(d.ts.triggers) > 20, "the live scraper found almost nothing"
    unknown = {term: trig for term, trig in ED._AMBIGUOUS_TERM_OWNER.items()
               if trig not in d.ts.triggers}
    assert not unknown, (
        "these _AMBIGUOUS_TERM_OWNER entries name a trigger that no longer "
        "exists in nix/home.nix, so they are dead: " + repr(unknown))


def test_the_declared_owner_table_is_actually_load_bearing():
    """At least one entry must be DOING something on the live config.

    Without this the whole mechanism can go inert — every term resolving
    uniquely again — and the tests above would keep passing while nothing
    consults the table. That is the state in which someone deletes it as unused,
    and the picker terms go with it. If this fails, the honest fix is to delete
    the now-inert entry, not to keep it as decoration.
    """
    d = _live_det()
    working = []
    for term, trig in ED._AMBIGUOUS_TERM_OWNER.items():
        matched = [t for t in d.ts.triggers if d._term_matches(term, t)]
        if len(matched) > 1 and trig in matched and d._attribute(term) == trig:
            working.append(term)
    assert working, (
        "no _AMBIGUOUS_TERM_OWNER entry resolves an actually-ambiguous live "
        "term — the table is inert and asserts nothing: "
        + repr(dict(ED._AMBIGUOUS_TERM_OWNER)))


# -- FIX 7: DECLARED-INTERFACE PRECEDENCE ------------------------------------ #
# The general form of FIX 6. `_token_matches` reads three sources: the trigger,
# `search_terms`, and the human-readable `label`. The first two are a CLAIM ("this
# word means this snippet"); the label is a DESCRIPTION the operator rewords
# freely. Before this, a reword could silently shadow another snippet's declared
# route and the only remedy was a hand-written `_AMBIGUOUS_TERM_OWNER` row per
# collision — one row per false collision, forever. So: on the ambiguous branch,
# a snippet that reaches the term through its DECLARED interface out-bids one
# that reaches it only through its label.
#
# 🔴 THESE FIXTURES ARE DELIBERATELY NOT THE LIVE CONFIG. FIX 6's live guards
# already cover the reported incident; these must keep testing the RULE after
# nix/home.nix changes, so every word below is invented. Every field is pairwise
# distinct and none equals a constant the assertions name.
#
# 🔴 RED/GREEN MATRIX, measured with PYTHONDONTWRITEBYTECODE=1 against base
# bf5516fc — and measured TWICE, because running these tests unchanged at base
# mostly raises `AttributeError: _term_matches_declared`, which is API absence,
# NOT evidence the behaviour was wrong. So each test's BEHAVIOURAL assertion (its
# `_attribute` / `_term_matches` outcome alone) was also evaluated directly
# against the base detector. What that measured:
#
#   GENUINELY RED at base — the behaviour, not the API, differs:
#     test_label_only_match_does_not_shadow_a_declared_term      None -> ':wid'
#     test_precedence_narrows_three_candidates_to_the_declared_one None -> ':wid'
#     test_precedence_requires_every_token_declared              None -> ':nub'
#     test_the_recommend_terms_owe_nothing_to_the_owner_table   ':acq' -> ':rna'
#       (the only one that is red at base as an ordinary pytest failure too —
#        base returns the owner ':acq' where HEAD returns ':rna')
#
#   RED at base ONLY WITH #1247's TWO ENTRIES REMOVED — i.e. red on the tree that
#   reddened `main`, which is the regression they were written for and the same
#   framing #1247's own matrix used:
#     test_precedence_resolves_the_recommend_terms                None -> ':rna'
#     test_recommend_terms_resolve_on_the_live_config             None -> ':rna'
#   With base's table intact they are GREEN at base — the entries produced the
#   same answer. That is the point of deleting them: the answer now comes from
#   the rule, and these two tests keep the ANSWER pinned either way.
#
#   GREEN at base (INVARIANT GUARDS, labelled as such below; they pin behaviour
#   precedence must NOT change, so by construction they cannot be regression
#   coverage) — leaves_a_genuine_collision_to_the_owner_table,
#   is_inert_when_every_match_is_label_only,
#   never_repoints_a_unique_label_only_match, does_not_reach_the_picker,
#   naming_a_trigger_outright_survives_precedence. Mutation-checked instead.
#   test_token_matches_default_still_reads_the_label is an API-CONTRACT guard for
#   espanso-usage.py's positional caller, not a behaviour claim about either
#   tree; it can only fail on a signature change.

# One term, 'glimmer', reachable three different ways. Only ':wid' DECLARES it.
PRECEDENCE_BASE = {"matches": [
    {"trigger": ":wid", "replace": "...", "label": "Sparkle report",
     "search_terms": ["glimmer", "sheen"]},
    {"trigger": ":pfx", "replace": "...",
     "label": "notes on glimmer as observed downstream",   # incidental PROSE
     "search_terms": ["downstream", "notes"]},
]}


def test_label_only_match_does_not_shadow_a_declared_term():
    """🔴 THE REGRESSION TEST. RED at bf5516fc, GREEN here."""
    d = _det_for(PRECEDENCE_BASE)
    # Both are genuinely matched — this is the ambiguity that used to be fatal.
    assert d._term_matches("glimmer", ":wid") is True
    assert d._term_matches("glimmer", ":pfx") is True
    # ...and neither is NAMED by the term, so the FIX-5 tie-break cannot rescue
    # it, and no table entry exists for it. Precedence is the ONLY thing that can
    # produce an answer here.
    assert d._names_trigger("glimmer", ":wid") is False
    assert d._names_trigger("glimmer", ":pfx") is False
    assert "glimmer" not in ED._AMBIGUOUS_TERM_OWNER
    # The asymmetry precedence reads.
    assert d._term_matches_declared("glimmer", ":wid") is True
    assert d._term_matches_declared("glimmer", ":pfx") is False
    assert d._attribute("glimmer") == ":wid"


def test_precedence_narrows_three_candidates_to_the_declared_one():
    """A SECOND POINT ON THE DIMENSION — two candidates is the boundary case.

    One measurement is not a general claim: with only two snippets the narrowed
    set is a singleton whichever way the code slices it. Three matches, TWO of
    them label-only, is the middle of the range and the case a mutant that
    narrows by 'drop the last match' would survive.
    """
    base = {"matches": [
        {"trigger": ":wid", "replace": "...", "label": "Sparkle report",
         "search_terms": ["glimmer", "sheen"]},
        {"trigger": ":pfx", "replace": "...",
         "label": "notes on glimmer as observed downstream",
         "search_terms": ["downstream", "notes"]},
        {"trigger": ":qzz", "replace": "...",
         "label": "a glimmer of an idea, filed for later",
         "search_terms": ["idea", "filed"]},
    ]}
    d = _det_for(base)
    assert sorted(t for t in d.ts.triggers
                  if d._term_matches("glimmer", t)) == [":pfx", ":qzz", ":wid"]
    assert [t for t in d.ts.triggers
            if d._term_matches_declared("glimmer", t)] == [":wid"]
    assert d._attribute("glimmer") == ":wid"


def test_precedence_requires_every_token_declared():
    """Multi-word: a term is a declared match only if EVERY token is declared.

    RED at bf5516fc. ':vex' declares 'lantern' but reaches 'harbor' only through
    its label, so it is NOT a declared match for the two-token query, and ':nub'
    — which declares both — takes it.
    """
    base = {"matches": [
        {"trigger": ":vex", "replace": "...",
         "label": "harbor lights at dusk",
         "search_terms": ["lantern", "dusk"]},
        {"trigger": ":nub", "replace": "...", "label": "Quay inventory",
         "search_terms": ["harbor", "lantern"]},
    ]}
    d = _det_for(base)
    assert d._term_matches("harbor lantern", ":vex") is True   # label + terms
    assert d._term_matches("harbor lantern", ":nub") is True
    assert d._term_matches_declared("harbor lantern", ":vex") is False
    assert d._term_matches_declared("harbor lantern", ":nub") is True
    assert d._attribute("harbor lantern") == ":nub"
    # ...and the single token ':vex' DOES declare stays symmetric, so the
    # all()-over-tokens really is per-token and not a whole-term shortcut.
    assert d._term_matches_declared("lantern", ":vex") is True


def test_precedence_leaves_a_genuine_collision_to_the_owner_table():
    """🔴 INVARIANT GUARD (green at base) — precedence must NOT take the table's
    job. When BOTH snippets declare the term there is nothing to narrow, so the
    answer stays None unless an owner is declared. This is the 'ask'/'clarify'
    shape, in fixture form: if precedence ever started picking a winner among
    two declared snippets, the live owner table would become unreachable and the
    highest-traffic term in the config would silently re-point.
    """
    base = {"matches": [
        {"trigger": ":vex", "replace": "...", "label": "Harbour lights",
         "search_terms": ["lantern", "dusk"]},
        {"trigger": ":nub", "replace": "...", "label": "Quay inventory",
         "search_terms": ["lantern", "crates"]},
    ]}
    d = _det_for(base)
    assert d._term_matches_declared("lantern", ":vex") is True
    assert d._term_matches_declared("lantern", ":nub") is True
    assert d._attribute("lantern") is None
    # ...and the table still decides it, over the narrowed-to-nothing set.
    ED._AMBIGUOUS_TERM_OWNER["lantern"] = ":nub"
    try:
        assert d._attribute("lantern") == ":nub"
    finally:
        del ED._AMBIGUOUS_TERM_OWNER["lantern"]


def test_precedence_is_inert_when_every_match_is_label_only():
    """🔴 INVARIANT GUARD (green at base). With nothing but labels to go on there
    is no basis to prefer one snippet, so the set is left alone and the answer is
    still None. A mutant that narrows to the (empty) declared set would return
    None here too — which is why the owner-table half below is asserted: it
    proves the candidate set SURVIVED the narrowing step rather than being
    emptied by it.
    """
    base = {"matches": [
        {"trigger": ":vex", "replace": "...", "label": "a lantern by the door",
         "search_terms": ["dusk"]},
        {"trigger": ":nub", "replace": "...", "label": "lantern oil, spare",
         "search_terms": ["crates"]},
    ]}
    d = _det_for(base)
    assert d._term_matches_declared("lantern", ":vex") is False
    assert d._term_matches_declared("lantern", ":nub") is False
    assert d._attribute("lantern") is None
    ED._AMBIGUOUS_TERM_OWNER["lantern"] = ":vex"
    try:
        assert d._attribute("lantern") == ":vex", (
            "the declared-narrowing step emptied the candidate set, so "
            "`owner in matched` can no longer find a legitimately-matched owner")
    finally:
        del ED._AMBIGUOUS_TERM_OWNER["lantern"]


def test_precedence_never_repoints_a_unique_label_only_match():
    """🔴 INVARIANT GUARD (green at base). `('rig', ':sshwn')` and
    `('portable', ':sshln')` resolve through their LABEL by design on the live
    config. Precedence runs only on the ambiguous branch, so a term with exactly
    one match must be untouched even when that match is label-only.
    """
    base = {"matches": [
        {"trigger": ":vex", "replace": "...", "label": "the lantern room",
         "search_terms": ["dusk"]},
        {"trigger": ":nub", "replace": "...", "label": "Quay inventory",
         "search_terms": ["crates"]},
    ]}
    d = _det_for(base)
    assert [t for t in d.ts.triggers if d._term_matches("lantern", t)] == [":vex"]
    assert d._term_matches_declared("lantern", ":vex") is False
    assert d._attribute("lantern") == ":vex"


def test_precedence_does_not_reach_the_picker():
    """🔴 INVARIANT GUARD (green at base). espanso lists EVERY match as a row and
    the user picks one; narrowing is an ATTRIBUTION concept only. If this shrinks
    to one row, precedence has become the label edit the operator declined.
    """
    d = _det_for(PRECEDENCE_BASE)
    assert {t for t in d.ts.triggers
            if d._term_matches("glimmer", t)} == {":wid", ":pfx"}


def test_naming_a_trigger_outright_survives_precedence():
    """Naming implies declaring (`token in trig`), so the FIX-5 tie-break can
    never be the candidate precedence drops. Pinned rather than argued.
    """
    base = {"matches": [
        {"trigger": ":lantern", "replace": "...", "label": "Quay inventory",
         "search_terms": ["crates"]},
        {"trigger": ":xlanterny", "replace": "...", "label": "Dock roster",
         "search_terms": ["lantern", "roster"]},
    ]}
    d = _det_for(base)
    assert d._term_matches_declared("lantern", ":lantern") is True
    assert d._term_matches_declared("lantern", ":xlanterny") is True
    assert d._names_trigger("lantern", ":lantern") is True
    assert d._attribute("lantern") == ":lantern"


def test_token_matches_default_still_reads_the_label():
    """`_token_matches` is called POSITIONALLY by espanso-usage.py's
    reachability probe (`det._token_matches(tok, "", meta)`). The new flag is
    keyword-only WITH a default, so that caller must be unaffected — a
    three-source match by default, declared-only on request.
    """
    meta = {"label": "a lantern by the door", "search_terms": ["dusk"]}
    assert ED.EspansoDetector._token_matches("lantern", ":vex", meta) is True
    assert ED.EspansoDetector._token_matches(
        "lantern", ":vex", meta, use_label=False) is False
    assert ED.EspansoDetector._token_matches(
        "dusk", ":vex", meta, use_label=False) is True
    # The empty-trigger call shape espanso-usage.py actually uses.
    assert ED.EspansoDetector._token_matches("lantern", "", meta) is True


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

    The pinned metadata is READ OFF nix/home.nix, never off the scraper's own
    output — deriving it from the implementation is exactly what would make this
    control vacuous. (`:date` used to be the metadata pin; #351 pruned that
    snippet, so the pin moved to `:sshwn`, whose `search_terms` list was longer
    and therefore a stricter test of the list-splitting regex. The 2026-08-19
    audit then STRIPPED `:sshwn` to direct-trigger-only to make the search UI
    unambiguous, which would have left this control asserting `== []` — i.e.
    exactly the vacuous state the paragraph above forbids, since an empty list
    exercises no splitting at all. So the pin moved again, to `:eos` for a long
    single-word list and `:mt` for MULTI-WORD entries, which are the strictest
    case for the regex. Whenever a pinned snippet's terms are removed, MOVE this
    pin to another long list — never relax it to the empty one.)
    """
    base = _live_base()
    trigs = [m["trigger"] for m in base["matches"]]
    assert len(trigs) >= 20, f"scraper found only {len(trigs)} snippets: {trigs}"
    assert len(set(trigs)) == len(trigs), "duplicate trigger in nix/home.nix"
    assert ":sshwn" in trigs and "dashbaord" in trigs
    by_trig = {m["trigger"]: m for m in base["matches"]}
    assert by_trig[":eos"]["search_terms"] == [
        "end", "session", "wrap", "handoff", "skills", "review", "update",
        "docs", "ritual", "prune", "evict", "bloat"]
    assert by_trig[":eos"]["label"].startswith("End-of-session ritual")
    # Multi-word entries: the case a naive whitespace split would shred.
    assert "in the meantime" in by_trig[":mt"]["search_terms"]
    assert "what can we do" in by_trig[":mt"]["search_terms"]
    # `>=` not `==`: an exact count reds on adding an unrelated :mt term, which
    # breaks nothing this control is about (it exists to prove the list-splitting
    # regex works, and the two multi-word asserts above carry that).
    assert len(by_trig[":mt"]["search_terms"]) >= 13
    assert by_trig[":kickoff"]["label"] == "Kickoff message for next session"
    # 🔴 Every snippet must keep a LABEL. espanso's picker falls back to showing
    # the raw `replace` text as the row description when a label is absent, and
    # a label is also the main thing a query can match. A 2026-08-19 pass
    # stripped the label+search_terms from :sshwn/:sshln — which blanked the
    # picker for 'nebula'/'mesh'/'remote' entirely. `dashbaord` is the one
    # deliberate exception (a typo-correction fired by typing it verbatim).
    # `<=` not `==`: `dashbaord` is a zero-fire typo-correction and pruning it is
    # a legitimate outcome of /espanso-audit, which `==` would turn red.
    labelless = sorted(t for t, m in by_trig.items() if not m["label"])
    assert set(labelless) <= {"dashbaord"}, (
        "these snippets have no label, so espanso will show their raw expansion "
        "as the picker row and most queries cannot reach them: "
        + repr([t for t in labelless if t != "dashbaord"])
    )


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
    # ANTI-VACUITY: emptying the table below left this test green (measured
    # 2026-08-19). A floor, not an exact count — adding terms is fine.
    assert len(_MT_TERMS) >= 14, "_MT_TERMS shrank; this guard weakens silently"
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
    # 'ask' is the HIGHEST-traffic term in the whole config (58 fires in the
    # 2026-08-06..19 window) and was the one term not pinned here. A snippet
    # labelled with the word "task" silently takes it, because 'ask' ⊂ 'task'
    # — that exact mutant survived a full green suite on 2026-08-19.
    ("ask", ":acq"),
    # 2026-08-28: :acq SPLIT into :dacq (dispatch) + :acq (the bare ask), so
    # these three moved to :dacq — a RETARGET, not a relaxation: each still
    # pins a unique resolution, and the pair below pins the split itself.
    # Retargeting is only legitimate because the snippet they name was renamed;
    # had the term merely gone ambiguous, the fix would be the config, not this
    # table (see the ANTI-VACUITY note on the test that reads it).
    ("feedback", ":dacq"), ("dispatch", ":dacq"), ("process", ":dacq"),
    # The split's own guard. 'ask' above must NOT drift to :dacq, and these
    # must NOT drift back to :acq — the failure mode is one snippet's label or
    # search_terms swallowing the other's interface words. `_token_matches`
    # reads the LABEL too, so this fails if either label regains the other's
    # vocabulary, not only if search_terms do.
    ("clarify", ":acq"), ("questions", ":acq"),
    ("elicit", ":dacq"), ("subagent", ":dacq"),
    # The split's OTHER half, missed by the row above and by the prefix/suffix
    # collision guard alike: the bare trigger name "acq" is a SUBSTRING of
    # ":dacq", so it matched both and every such fire landed UNATTRIBUTED. The
    # collision guard cannot see it — ":dacq".endswith(":acq") is False. ADDED,
    # never in place of a row; see the ANTI-VACUITY note below.
    ("acq", ":acq"), ("dacq", ":dacq"),
    ("cc", ":cc"), ("kubecl", ":kuc"), ("spine", ":csc"), ("orch", ":cmo"),
    ("home", ":hlt"), ("prod", ":cpk"), ("datap", ":cdp"), ("civit prod", ":cpk"),
]


def test_live_existing_resolutions_not_made_ambiguous():
    # ANTI-VACUITY: emptying _EXISTING_RESOLUTIONS left this green (measured
    # 2026-08-19), so the cheap way to "fix" a future failure is to delete the
    # row that broke — which is exactly the regression this guard exists to
    # catch. A floor, not an exact count.
    # The floor is the CURRENT row count (26 on 2026-08-28, ratcheted up from a
    # stale 20 that had let four added rows go unprotected). `>=` so adding a
    # row stays green; deleting one goes red, which is the whole point.
    assert len(_EXISTING_RESOLUTIONS) >= 26, (
        "_EXISTING_RESOLUTIONS shrank — a pinned resolution was deleted rather "
        "than fixed; that is the failure mode, not the fix"
    )
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


# REGRESSION coverage for the 2026-08-19 /espanso-audit. On the pre-change
# config each ssh term was AMBIGUOUS (`_attribute` -> None over two snippets
# that both spelled the host), so the fire was recorded UNATTRIBUTED. RED at
# merge-base a29b97b, green here — not an invariant guard.
#
# 🔴 The LAST THREE pin a term the snippet's LABEL does NOT contain, so deleting
# its search_terms kills the test. The first version pinned
# 'unaddressed'/'proceed'/'clawgate', every one of which is spelled in its own
# label — deleting all three snippets' search_terms left the suite fully green.
# ('rig', ':sshwn') and ('portable', ':sshln') resolve via their LABEL by
# design: for those two the label IS the interface, and their search_terms hold
# no unique word at all. They pin the RESOLUTION, not the search_terms — an
# earlier version of this comment claimed every entry was label-independent,
# and a mutation sweep showed these two survive deleting the entry they were
# supposed to guard. Do not count them as search_terms coverage.
_AUDIT_2026_08_19_RESOLUTIONS = [
    ("lap", ":sshll"), ("ssh lap", ":sshll"), ("ssh wor", ":sshwl"),
    ("rig", ":sshwn"), ("portable", ":sshln"),
    ("loose", ":alo"), ("tests", ":pdt"), ("pick up", ":cgt"),
]
# NOT listed: bare "wor". It was never a query he typed (the measured one is the
# two-token "ssh wor"), so asserting it would be an expectation invented from a
# fix rather than read off the search stream. ⚠ The reason this comment used to
# give — "it stays ambiguous with :mt" — is NO LONGER TRUE as of the FIX 7
# declared-interface precedence: ':mt' reaches "wor" only through its label
# ("parallel WORK while that runs") while ':sshwl' DECLARES "workbench", so bare
# "wor" now resolves to ':sshwl'. It stays off this table for the unchanged
# reason (nobody types it), not for the stale one.


def test_live_audit_2026_08_19_resolutions():
    # ANTI-VACUITY: emptying the table left this green (measured 2026-08-19).
    assert len(_AUDIT_2026_08_19_RESOLUTIONS) >= 8, (
        "_AUDIT_2026_08_19_RESOLUTIONS shrank; this guard weakens silently"
    )
    d = _live_det()
    bad = {}
    for term, want in _AUDIT_2026_08_19_RESOLUTIONS:
        got = d._attribute(term)
        if got != want:
            bad[term] = (want, got, [t for t in d.ts.triggers if d._term_matches(term, t)])
    assert not bad, (
        "the 2026-08-19 audit's resolutions regressed "
        "(term -> (expected, actual, matching snippets)): " + repr(bad)
    )


# 🔴 PICKER coverage — a DIFFERENT question from attribution above.
# espanso lists EVERY match as a row and the user picks one; ambiguity means
# "two rows", NOT a dead query. Only `_attribute` needs uniqueness, and only so
# telemetry can name the snippet. Conflating the two is what led a 2026-08-19
# pass to STRIP these labels to force uniqueness — which blanked the picker for
# every word that describes the nebula endpoints ('nebula'/'mesh'/'remote' went
# from 2 rows to 0). Assert the rows stay REACHABLE; do not demand uniqueness.
_PICKER_ROWS = [
    ("nebula", {":sshwn", ":sshln"}),
    ("mesh", {":sshwn", ":sshln"}),
    ("remote", {":sshwn", ":sshln"}),
    ("lan", {":sshwl", ":sshll"}),
    ("ssh", {":sshwn", ":sshwl", ":sshln", ":sshll"}),
]


def test_live_picker_rows_stay_reachable():
    """A query that listed rows must keep listing them, unique or not.

    ANTI-VACUITY first: the relation below is `issubset`, deliberately (rows are
    additive — a new snippet spelling 'nebula' is not a regression), but that
    makes the cheap way to green a future failure "shrink `want`", degrading the
    guard to nothing. Emptying the table, or any one expectation, must fail
    HERE rather than pass silently.
    """
    assert _PICKER_ROWS, "_PICKER_ROWS is empty — this guard would pass vacuously"
    empty = [term for term, want in _PICKER_ROWS if not want]
    assert not empty, (
        "these _PICKER_ROWS entries expect no rows, so they assert nothing "
        "(set().issubset(x) is always True): " + repr(empty)
    )
    d = _live_det()
    bad = {}
    for term, want in _PICKER_ROWS:
        got = {t for t in d.ts.triggers if d._term_matches(term, t)}
        if not want.issubset(got):
            bad[term] = {"expected at least": sorted(want), "actual": sorted(got)}
    assert not bad, (
        "these queries no longer reach snippets they used to list in the "
        "espanso picker (term -> rows): " + repr(bad)
    )


def test_live_triggers_have_no_prefix_or_suffix_collisions():
    """espanso longest-matches; the detector emits the SHORTEST match.

    Where one trigger is a prefix or suffix of another the two disagree, so the
    keylog signal misattributes. Pin zero such pairs — and prove the checker can
    SEE one, so the zero is a real zero and not a check wired to nothing.

    🔴 SCOPE: this covers the DIRECT path ONLY, because that path matches on the
    ring's SUFFIX. It is structurally blind to the SEARCH path, which matches by
    SUBSTRING anywhere — ':acq' is a substring of ':dacq' while
    `":dacq".endswith(":acq")` is False, so that pair passed here for ten days
    while every bare 'acq' search landed unattributed. The search half is
    `test_live_bare_trigger_names_resolve_to_their_own_snippet` below; the two
    together are the claim, neither alone.
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


def test_live_bare_trigger_names_resolve_to_their_own_snippet():
    """Typing a snippet's own name must attribute to THAT snippet.

    The SEARCH-path half of the collision guard above. Search attribution
    matches by SUBSTRING, so one trigger being a substring of another anywhere
    (not just at an end) makes the shorter one's own name ambiguous. Measured
    2026-08-28: ':acq' split into ':dacq' + ':acq' made bare 'acq' match BOTH,
    `_attribute` returned None, and every such fire was recorded UNATTRIBUTED on
    both hosts — with the prefix/suffix guard green throughout.

    The expectation is DERIVED from the live config (every trigger, not a typed
    list), so it cannot be weakened by deleting the row that broke, and a
    27th snippet is covered the day it is added. It goes RED both if the config
    regrows such a pair without the fix and if the tie-break in `_attribute` is
    removed — measured red at 6349a8b9 on 'acq' AND 'alo'.

    ANTI-VACUITY: an empty or tiny trigger set would make this pass while
    checking nothing, and `_attribute` returning the term itself would satisfy
    it trivially — so pin a count floor and prove the checker can go RED, by
    running it over a config that carries a known-bad pair.
    """
    live = _live_base()["matches"]
    trigs = [m["trigger"] for m in live]
    assert len(trigs) >= 20, f"scraper found only {len(trigs)} snippets: {trigs}"

    def unresolved(base):
        d = EspansoDetector(ET.load_triggers(base, DEFAULT))
        bad = {}
        for t in [m["trigger"] for m in base["matches"]]:
            bare = t[1:] if t.startswith(":") else t
            got = d._attribute(bare)
            if got != t:
                bad[bare] = (t, got,
                             sorted(x for x in d.ts.triggers
                                    if d._term_matches(bare, x)))
        return bad

    # NEGATIVE CONTROL: seed a snippet whose trigger is an existing one spelled
    # WITHOUT the colon. Its bare name then names TWO snippets, so the tie-break
    # cannot break it and `_attribute` must stay None — the one bare-name
    # ambiguity that survives the fix, and therefore the case that proves this
    # checker can still go red. (A plain containment pair like ':xmt'/':xmty' is
    # NOT a valid control here: the tie-break resolves it by design, which is
    # exactly what this guard asserts about ':acq' vs ':dacq'.)
    seeded = {"matches": live + [
        {"trigger": "mt", "replace": "...", "label": "seeded control",
         "search_terms": []},
    ]}
    control = unresolved(seeded)
    assert "mt" in control, (
        "the bare-name checker failed its negative control — it did not see "
        "'mt' naming both ':mt' and a seeded 'mt', so its zero below would "
        "mean nothing: " + repr(control)
    )

    bad = unresolved(_live_base())
    assert not bad, (
        "these snippets' own names no longer attribute to them "
        "(name -> (trigger, attributed, matching snippets)): " + repr(bad)
    )
