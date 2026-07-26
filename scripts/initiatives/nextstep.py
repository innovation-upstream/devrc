#!/usr/bin/env python3
"""Grounded next-step RECOMMENDATION — a PURE, read-only derivation over one initiative view.

Phase-2a of the initiatives subsystem. This module answers a single question — "what is the
logical NEXT STEP on this initiative?" — but does so under a strict ANTI-CONFABULATION
contract: the recommendation text is always a close paraphrase/quote of an ACTUAL field on
the view dict, never invented. If nothing on the view supports a step, we return None (the
card then shows nothing, the chat says "not enough info") rather than fabricate one.

This deliberately targets the "Emerging" (session-only, `undocumented=True`) gap: those
cards have NO parsed handoff `next_step`, so the board can't tell the user what to do next.
We derive a grounded suggestion from whatever real signal the view carries instead.

Derivation priority (FIRST match wins — most-authoritative first):
  1. `next_step`            → the parsed handoff step        (basis="handoff", DOCUMENTED)
  2. `open_prs[0]`          → land the open PR               (basis="open-pr",       INFERRED)
  3. `open_investigations[0]` → resolve the open question    (basis="investigation", INFERRED)
  4. `face_message.text`    → continue the last prompt       (basis="focus",         INFERRED)
  5. `status`               → follow up on current status    (basis="status",        INFERRED)
  6. momentum == "stalled"  → decide resume-or-drop          (basis="stalled",       INFERRED)
  7. else                   → None

Only basis="handoff" is a *parsed* step Zach already wrote down; every other basis is an
INFERRED suggestion the derivation synthesized from a real field — the UI/chat surface that
distinction (a documented `next_step` renders unchanged; a suggestion is labelled).

PURE: stdlib only, no imports of viewer/assistant/DB — so it's trivially unit-testable and
safe to load from the viewer's per-view build loop.
"""
from __future__ import annotations

# Cap the recommendation text so a runaway field can't blow up a card / a model prompt.
# Mirrors assistant._trim's ellipsis behaviour so the two surfaces read identically.
_TEXT_MAX = 200

# basis -> a short, human hint the card/chat shows next to the suggestion so the user knows
# WHERE it came from (and, implicitly, that it is inferred rather than a documented step).
_BASIS_LABELS = {
    "handoff": "from your handoff",
    "open-pr": "from an open PR",
    "investigation": "from an open investigation",
    "focus": "from your last prompt",
    "status": "from current status",
    "stalled": "stalled",
}


def _trim(text, n: int = _TEXT_MAX) -> str:
    """Trim to a sane cap with an ellipsis — mirrors assistant._trim (single behaviour across
    the two surfaces). None/blank → ""."""
    s = (text or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def basis_label(basis: str) -> str:
    """basis -> a short human hint (e.g. "from your handoff"). Unknown basis → the basis
    string itself (defensive; never raises)."""
    return _BASIS_LABELS.get(basis, str(basis or ""))


def recommend_next_step(view: dict) -> dict | None:
    """Derive a GROUNDED next-step recommendation for one initiative view.

    Returns `{"text": str, "basis": str}` where `text` is a close paraphrase/quote of a real
    view field (trimmed) and `basis` names which field it came from, or None when no field
    supports a recommendation. NEVER invents a step. See the module docstring for the fixed
    derivation priority. Pure; never raises (a malformed view degrades to None)."""
    if not isinstance(view, dict):
        return None

    # 1. Documented parsed handoff step — the most authoritative, used verbatim.
    next_step = (view.get("next_step") or "").strip()
    if next_step:
        return {"text": _trim(next_step), "basis": "handoff"}

    # 2. An open PR — the obvious next action is to review/land it.
    open_prs = view.get("open_prs") or []
    pr = next((p for p in open_prs if isinstance(p, dict)), None)
    if pr is not None:
        num = pr.get("number")
        title = (pr.get("title") or "").strip()
        head = f"Review/land open PR #{num}" if num is not None else "Review/land the open PR"
        text = f"{head} {title}".rstrip() if title else head
        return {"text": _trim(text), "basis": "open-pr"}

    # 3. An open investigation — resolve the outstanding question.
    investigations = [str(x).strip() for x in (view.get("open_investigations") or [])
                      if str(x).strip()]
    if investigations:
        return {"text": _trim(f"Resolve: {investigations[0]}"), "basis": "investigation"}

    # 4. The user's most-recent substantive prompt — pick the thread back up.
    face = view.get("face_message")
    face_text = (face.get("text") or "").strip() if isinstance(face, dict) else ""
    if face_text:
        return {"text": _trim(f"Continue where you left off: {face_text}"), "basis": "focus"}

    # 5. The current status line — follow up on whatever it describes.
    status = (view.get("status") or "").strip()
    if status:
        return {"text": _trim(f"Follow up on: {status}"), "basis": "status"}

    # 6. Nothing actionable, but it's stalled — surface the resume-or-drop decision (only when
    #    we have an `age` to ground "last touched N ago"; otherwise there is nothing to say).
    if (view.get("momentum") or "") == "stalled":
        age = (view.get("age") or "").strip()
        if age:
            return {"text": _trim(
                f"Decide whether to resume or drop this (stalled — last touched {age} ago)"),
                "basis": "stalled"}

    # 7. No field supports a grounded recommendation.
    return None
