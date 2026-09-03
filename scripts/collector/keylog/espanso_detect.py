"""espanso_detect — detect true espanso usage from live keystrokes.

Because espanso erases both the trigger and the expansion (backspace + clipboard
paste) the ONLY place espanso usage is observable is the raw keystroke stream,
BEFORE espanso reacts. `EspansoDetector` watches that stream (fed per-key by the
keylogger, in parallel with the chunker) and emits an `EspansoEvent` the moment
a trigger completes — the deterministic, forward-only usage signal.

Two detection paths:

  1. DIRECT triggers (deterministic). A bounded recent-char ring mirrors what
     espanso itself sees. When the ring ENDS WITH a known trigger we emit and
     CLEAR the ring — exactly as espanso consumes the trigger on firing. Because
     we check after every char and clear on match, the trigger that completes
     FIRST wins: typing ":datetime" emits ":date" (mirrors espanso's prefix
     behaviour), and ":datetime" never forms. Clearing also means espanso's
     trailing backspaces are no-ops (no double-emit), while a genuinely retyped
     trigger fills the ring again and fires again.

  2. Ctrl+Space SEARCH UI (best-effort attribution). On the search shortcut the
     keylogger calls `feed_search_open`; subsequent chars accumulate as the
     search TERM (not the direct ring) until a close boundary (Enter, Escape,
     focus change, or idle). We fuzzy-attribute the term to a snippet; a unique
     match is attributed, and when several match, a term that NAMES exactly one
     of them outright (it IS that trigger, with or without the ':') takes it.
     Otherwise → trigger=None, but the search-open + term are recorded
     regardless. Search events are always `inferred=True`.

Honest limitation: "ring ends with a trigger" ≈ "espanso fired", EXCEPT in
per-app espanso-disabled contexts (still far better than phrase-counting). The
detector NEVER raises out of `feed_*`; the crash-guard also lives at the call
site in keylog.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[a-z0-9]+")
# Chars that terminate the search term when in search mode.
_ENTER = "\n"
_ESCAPE = "\x1b"
_BACKSPACE = "\b"
# A real espanso search query is short. Ctrl+Space is NOT espanso-exclusive
# (IDE completion, emacs set-mark, IME), so a non-espanso Ctrl+Space would
# otherwise enter search-mode and accumulate ordinary typed text unbounded,
# mislabeling it as a method=search row. Past this cap we treat search-mode as
# a misfire and abort it WITHOUT emitting.
SEARCH_TERM_MAX = 64
# espanso's Ctrl+Space search bar opens as its OWN X window that STEALS focus.
# On NixOS its WM_CLASS is observed as ".espanso-wrapped"; other packagings use
# variants, so match by substring rather than the exact class.
ESPANSO_SEARCH_WM_CLASS = ".espanso-wrapped"

# 🔴 SEARCH_TERMS SERVE TWO CONSUMERS THAT WANT OPPOSITE THINGS, and this table
# is where the conflict is resolved instead of being fought out in the config.
# The espanso PICKER wants recall — every word that should list a row, listed on
# every snippet it should list (see `_PICKER_ROWS` in the tests: ambiguity there
# means "two rows", which is the feature). `_attribute` wants precision — one
# snippet, or nothing, so telemetry can name it. A term deliberately spelled on
# two snippets for the picker's sake therefore has NO unique answer to read off
# the config, and before this table the only way to give attribution one was to
# delete the term from the picker — trading a real search route for a telemetry
# row. That trade was made twice (2026-08-28, 2026-08-29) and reverted by hand
# both times.
#
# So: term -> the snippet that OWNS it for attribution, consulted ONLY on the
# already-ambiguous branch and ONLY over the snippets the term genuinely matches
# (`owner in matched` below). Both clauses are load-bearing — the table can
# never re-point a term that resolves uniquely today, and can never invent a
# resolution to a snippet the term does not reach. A term that is ambiguous and
# NOT listed here still resolves to None; silence is not a licence to guess.
#
# 'ask' is the highest-traffic term in the whole config (58 fires, 2026-08-06..19)
# and ':dacq' spells it — plus 'clarifying', which 'clarify' is a substring of —
# because "ask"/"clarify" is how the dispatch snippet is actually searched for.
# ':acq' ("ask clarifying questions") is what a bare 'ask' MEANS.
#
# 'recom'/'recommend' arrived the same way on 2026-09-02 (a720d30d, a one-line
# direct-to-main commit that RED-ed `main`): ':acq' grew the LABEL "ask
# clarifying questions and recommend improvements and anything useful to
# include", and `_token_matches` reads the label, so both terms started matching
# ':acq' as well as ':rna'. #1247 gave them entries here. 🔴 THOSE TWO ENTRIES
# ARE NOW DELETED, because the asymmetry that chose ':rna' as their owner is a
# GENERAL rule, not a per-term fact: the terms are ':rna''s declared INTERFACE
# (two of its six `search_terms`; its label is "Recommend next actions") whereas
# on ':acq' the word is incidental PROSE. That is what `_attribute`'s
# declared-interface precedence now decides on its own — MEASURED against the
# live config with the entries removed, both still resolve to ':rna', and the
# owner branch is not even REACHED for them (the declared-narrowed set is a
# single snippet, so `_attribute` returns before the lookup). An entry nothing
# can reach is dead code, so they went rather than staying as decoration.
# Re-add them only if ':acq' ever DECLARES 'recom' in its own `search_terms` —
# that would be a real collision, and then this table is again the right answer.
#
# 🔴 BLIND SPOT, MEASURED — THE LOOKUP IS AN EXACT-STRING MATCH, SO PREFIXES OF
# AN OWNED TERM ARE NOT OWNED. `_term_matches` is a SUBSTRING test, so a typed
# prefix reaches both snippets and lands on the ambiguous branch, where
# `.get(t)` then misses because the typed string is not a key. Measured
# 2026-08-30 against the real keylog search stream (661 espanso rows): "ask"
# resolves because it is typed EXACTLY, 118 times — but "clar" (1 fire ever)
# now resolves to None, where before ":dacq" spelled "clarifying" it reached
# ":acq". Prefix typing is the norm here, not the exception: "rec"/"recom",
# "kic", "orch" are all how the picker is really driven.
#
# NOT fixed with per-prefix entries — "cla"/"clari"/"clarif" are equally valid
# prefixes and the table cannot enumerate them. The real options are to make the
# lookup prefix-aware (an owner claims a typed term that is a prefix of an owned
# one AND matches that owner) or to accept it. ACCEPTED, on the measurement: one
# fire in the entire recorded history does not justify the mechanism, and a
# prefix-aware lookup can re-point terms the exact form never touched, so it
# needs its own mutation battery.
#
# 🔴 SINCE THE DECLARED-INTERFACE PRECEDENCE STEP (see `_attribute`), THIS TABLE
# ONLY ARBITRATES REAL COLLISIONS — `search_terms` vs `search_terms`. A term one
# snippet DECLARES and another merely spells in its LABEL is now settled by
# precedence before the lookup is reached, so no entry is needed for that class.
# 'ask'/'clarify' are the genuine case and still need it: ':acq' and ':dacq' BOTH
# declare them (':dacq' spells "clarifying", which "clarify" is a substring of),
# so precedence narrows nothing and the table is what decides.
#
# 🔴 STILL ACCEPTED FOR THE TABLE — but note that precedence closes the prefix
# case for the LABEL-COLLISION class, and does so exactly because it is a
# mechanism rather than an enumeration. Re-measured 2026-09-02 with precedence
# in place: "rec"/"reco"/"recomm"/"recomme"/"recommen" all now resolve to ':rna'
# where every one of them was None before, and no table entry names any of them.
# 'clar' remains None: ':acq' and ':dacq' BOTH declare it, so precedence has
# nothing to narrow and the exact-string lookup is still the only hatch. That is
# the residual blind spot, and it is unchanged — widening the lookup is still a
# mechanism change, not a table entry, and would need its own mutation battery.
#
# Same measurement, recorded so it is not re-litigated: "clarifying" has been
# typed ZERO times, so it gets no entry — an owner justified by intuition rather
# than the search stream is exactly what this comment exists to prevent.
_AMBIGUOUS_TERM_OWNER = {
    "ask": ":acq",
    "clarify": ":acq",
}


def _is_espanso_search_window(app) -> bool:
    """True when `app` is espanso's own search window (which steals X focus)."""
    return "espanso" in (app or "").lower()


@dataclass
class EspansoEvent:
    trigger: str | None      # the fired trigger, or None (search w/o unique match)
    method: str              # "direct" | "search"
    inferred: bool           # True for all search attributions
    search_term: str | None  # the typed search query (search method only)
    app: str
    session: str
    workspace: str = ""
    label: str = ""


class EspansoDetector:
    def __init__(self, trigger_set):
        self.ts = trigger_set
        # Ring long enough to hold the longest trigger; >=1 so it is never empty.
        self._maxlen = max(getattr(trigger_set, "max_len", 0), 1)
        self._ring: list[str] = []
        self._app = None
        self._session = None
        self._workspace = ""
        # search-mode state
        self._search = False
        self._search_term: list[str] = []
        # Origin context captured when search OPENS (before espanso's window
        # steals focus). Emitting from these — not the live self._app, which by
        # close time is ".espanso-wrapped" — attributes the event to the window
        # the user was actually working in when they hit Ctrl+Space.
        self._search_app = ""
        self._search_session = ""
        self._search_workspace = ""
        self._last_ts = 0.0

    # -- direct-trigger + search feed ------------------------------------- #
    def feed_char(self, char, *, app, session, now, workspace="") -> list:
        """Feed one resolved character. Returns a list of EspansoEvents (0 or 1)."""
        out: list = []
        try:
            # Focus change: typing moved to another window. Flush an open search
            # under the OLD context, then reset the direct ring (no cross-window
            # false match). EXCEPTION: espanso's Ctrl+Space search bar opens as
            # its OWN window (.espanso-wrapped) and steals focus — while in
            # search-mode a focus change TO that window is EXPECTED, so keep
            # search alive and keep accumulating the term. Only a focus change
            # to a genuinely different NON-espanso window closes the search.
            if self._app is not None and (app != self._app or session != self._session):
                if self._search and not _is_espanso_search_window(app):
                    ev = self._close_search("focus")
                    if ev is not None:
                        out.append(ev)
                self._ring.clear()
            self._app, self._session, self._workspace = app, session, workspace
            self._last_ts = now

            if self._search:
                self._feed_search_char(char, out)
                return out

            # -- direct ring --
            if char == _BACKSPACE:
                if self._ring:
                    self._ring.pop()
                return out
            self._ring.append(char)
            if len(self._ring) > self._maxlen:
                del self._ring[0:len(self._ring) - self._maxlen]
            ev = self._match_direct(app, session, now, workspace)
            if ev is not None:
                out.append(ev)
            return out
        except Exception:
            return out

    def notify_navigation(self) -> None:
        """Caret-navigation / editing key (arrow, Home/End, PageUp/Down, Delete).

        Such a key breaks contiguously-typed text, and espanso resets its own
        buffer on it. Clear the DIRECT ring so a trigger split by a caret move
        (":da" → arrow → "te") cannot assemble into a phantom ":date". Search
        state is deliberately left untouched. Never raises.
        """
        try:
            self._ring.clear()
        except Exception:
            pass

    def feed_search_open(self, *, app, session, now, workspace="") -> None:
        """Enter search-mode (called when keylog sees the Ctrl+Space shortcut)."""
        try:
            self._app, self._session, self._workspace = app, session, workspace
            # Snapshot the ORIGIN context now — before espanso's search window
            # steals focus and the adopted self._app becomes ".espanso-wrapped".
            self._search_app = app
            self._search_session = session
            self._search_workspace = workspace
            self._last_ts = now
            self._ring.clear()
            self._search = True
            self._search_term = []
        except Exception:
            pass

    def flush_idle(self, now, idle_seconds) -> list:
        """Close an idle, unterminated search (called by keylog's idle loop)."""
        out: list = []
        try:
            if self._search and (now - self._last_ts) >= idle_seconds:
                ev = self._close_search("idle")
                if ev is not None:
                    out.append(ev)
        except Exception:
            pass
        return out

    def flush_now(self) -> list:
        """Force-close an open search (e.g. on shutdown)."""
        out: list = []
        try:
            if self._search:
                ev = self._close_search("close")
                if ev is not None:
                    out.append(ev)
        except Exception:
            pass
        return out

    # -- internals -------------------------------------------------------- #
    def _feed_search_char(self, char, out: list) -> None:
        if char == _ENTER:
            ev = self._close_search("enter")
            if ev is not None:
                out.append(ev)
        elif char == _ESCAPE:
            ev = self._close_search("escape")
            if ev is not None:
                out.append(ev)
        elif char == _BACKSPACE:
            if self._search_term:
                self._search_term.pop()
        else:
            self._search_term.append(char)
            if len(self._search_term) > SEARCH_TERM_MAX:
                # Way past a plausible espanso query → this was a non-espanso
                # Ctrl+Space and we've been mislabeling typed text. Abort
                # search-mode WITHOUT emitting and reset search state.
                self._search = False
                self._search_term = []

    def _match_direct(self, app, session, now, workspace):
        if not self.ts.triggers:
            return None
        s = "".join(self._ring)
        # Emit the SHORTEST trigger the ring ends with (the one that completed
        # first / at the current char), mirroring espanso's prefix behaviour.
        best = None
        for trig in self.ts.triggers:
            if trig and s.endswith(trig) and (best is None or len(trig) < len(best)):
                best = trig
        if best is None:
            return None
        # espanso consumes the trigger on firing → clear so trailing backspaces
        # are no-ops and a longer overlapping trigger cannot also match.
        self._ring.clear()
        label = (self.ts.meta.get(best) or {}).get("label", "") or ""
        return EspansoEvent(
            trigger=best, method="direct", inferred=False, search_term=None,
            app=app, session=session, workspace=workspace, label=label,
        )

    def _close_search(self, reason):
        term = "".join(self._search_term)
        self._search = False
        self._search_term = []
        # No real query was typed (accidental Ctrl+Space then Escape/idle/focus)
        # → suppress the phantom empty trigger=None row rather than emit it.
        if not term.strip():
            return None
        trigger = self._attribute(term)
        label = (self.ts.meta.get(trigger) or {}).get("label", "") if trigger else ""
        # Attribute to the ORIGIN window (captured at feed_search_open), NOT the
        # live self._app — after the focus steal that would be ".espanso-wrapped",
        # which is identical for every search and tells us nothing about context.
        return EspansoEvent(
            trigger=trigger, method="search", inferred=True, search_term=term,
            app=self._search_app or "", session=self._search_session or "",
            workspace=self._search_workspace, label=label or "",
        )

    def _attribute(self, term):
        """Fuzzy-attribute a search term to exactly one snippet, else None."""
        t = (term or "").strip().lower()
        if not t:
            return None
        matched = [trig for trig in self.ts.triggers if self._term_matches(t, trig)]
        if len(matched) == 1:
            return matched[0]
        # 🔴 DECLARED-INTERFACE PRECEDENCE. `_token_matches` reads three sources:
        # the trigger, the `search_terms` list, and the human-readable `label`.
        # The first two are the snippet's DECLARED interface — an authored claim
        # that this word means this snippet. The label is a DESCRIPTION, and it
        # is prose the operator rewords freely. So a snippet that names the term
        # in its declared interface outbids one that only happens to SPELL it in
        # a sentence. Measured motivation (2026-09-02, a720d30d): ':acq' grew the
        # label "ask clarifying questions and recommend improvements and anything
        # useful to include", which made 'recom'/'recommend' — two of ':rna''s
        # six declared `search_terms` — ambiguous and unattributable, reddening
        # `test_live_existing_resolutions_not_made_ambiguous` on `main`. A
        # cosmetic reword must not shadow another snippet's declared route.
        #
        # This can only ever SHRINK the candidate set, so it cannot invent a
        # resolution or re-point a term that already resolves uniquely (that
        # branch returned above). When NO candidate matches declaredly the set is
        # left alone: with nothing but labels to go on there is no basis to
        # prefer one, and narrowing to the empty set would lose the answer that
        # `_names_trigger` or `_AMBIGUOUS_TERM_OWNER` can still give. Naming a
        # trigger outright implies a declared match (`token in trig`), so the
        # tie-break below can never be the thing this drops.
        declared = [trig for trig in matched if self._term_matches_declared(t, trig)]
        if declared:
            matched = declared
            if len(matched) == 1:
                return matched[0]
        # Matching is by SUBSTRING (see `_token_matches`), so a term can match a
        # snippet it merely occurs INSIDE. When ":acq" was split into ":dacq" +
        # ":acq" on 2026-08-28 the bare term "acq" started matching BOTH
        # (`"acq" in ":dacq"` is True) and every such fire was recorded
        # UNATTRIBUTED. A term that NAMES one snippet outright — it equals that
        # trigger, with or without the leading ":" — is not ambiguous about
        # which snippet it means, so that snippet wins over the ones that merely
        # contain it. This is consulted ONLY on the already-ambiguous branch, so
        # it can never re-point a term that resolves uniquely today; the tie is
        # broken only when EXACTLY ONE candidate is named outright. (It now runs
        # over the declared-narrowed set — which cannot drop a named candidate,
        # since naming implies declaring.)
        exact = [trig for trig in matched if self._names_trigger(t, trig)]
        if len(exact) == 1:
            return exact[0]
        # Still ambiguous, and no candidate is named outright. A term may be
        # spelled on two snippets ON PURPOSE, for the picker's sake — see
        # `_AMBIGUOUS_TERM_OWNER`. `owner in matched` keeps the declaration
        # honest: it picks among the snippets this term actually reaches and
        # cannot invent one, so a stale entry goes inert rather than wrong.
        owner = _AMBIGUOUS_TERM_OWNER.get(t)
        if owner is not None and owner in matched:
            return owner
        return None

    @staticmethod
    def _names_trigger(term, trig):
        """True when `term` IS this trigger, spelled with or without its ':'."""
        low = (trig or "").lower()
        return bool(low) and term in (low, low[1:] if low.startswith(":") else low)

    def _term_matches(self, term, trig):
        """True when EVERY whitespace-separated token of `term` matches `trig`.

        Multi-word queries are how the search UI is actually driven: the
        /espanso-audit of 2026-08-05 found 46 of 173 keylog rows unattributed,
        19+ of them multi-word ("ssh work", "ssh lap", "ss wor", "civit prod").
        The old rule tested the WHOLE term as a substring of a single word, so
        any term containing a space could never match ANY snippet — which made
        the four :ssh* snippets and :cgf/:subk read as dead when they are not.
        A single-token term takes exactly the old path (all() over one token).
        """
        tokens = (term or "").split()
        if not tokens:
            return False
        meta = self.ts.meta.get(trig) or {}
        return all(self._token_matches(tok, trig, meta) for tok in tokens)

    def _term_matches_declared(self, term, trig):
        """`_term_matches`, but WITHOUT consulting the label.

        i.e. every token reaches `trig` through its DECLARED interface — the
        trigger string or an entry in `search_terms`. Used only by `_attribute`'s
        precedence step; the picker (`_term_matches`) keeps reading the label, so
        a label-matched row is still LISTED, it just stops out-bidding a declared
        one for the single name telemetry records.

        Same rule as `_term_matches`, same helper, one flag — deliberately not a
        second copy of the substring logic.
        """
        tokens = (term or "").split()
        if not tokens:
            return False
        meta = self.ts.meta.get(trig) or {}
        return all(self._token_matches(tok, trig, meta, use_label=False)
                   for tok in tokens)

    @staticmethod
    def _token_matches(token, trig, meta, *, use_label=True):
        """One token vs one snippet, by the original (pre-2026-08-05) rules.

        `use_label=False` restricts the test to the snippet's declared interface
        (trigger + `search_terms`). The default keeps every existing caller —
        `_term_matches`, and `espanso-usage.py`'s reachability probe, which calls
        this positionally — on the original three-source behaviour.
        """
        if token in trig.lower():
            return True
        if use_label:
            label = (meta.get("label") or "").lower()
            for w in _WORD_RE.findall(label):
                if token in w:
                    return True
        for st in meta.get("search_terms") or []:
            if isinstance(st, str) and token in st.lower():
                return True
        return False
