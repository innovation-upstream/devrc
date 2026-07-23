#!/usr/bin/env python3
"""Unit tests for the PURE surface-only tagging helper `related_initiative_slug`.

Offline: no live DB, no port-forward. The helper loads the shared router
(`scripts/initiatives/route.py`) by explicit importlib path and ranks a mail
subject against a fixture `initiatives.current` set, returning the top slug ONLY
when confident. Loading route runs the scan's lazy `import chquery` (needs
`requests` + `scripts/validation` on sys.path) exactly as the initiatives
`test_route.py` does in the same hermetic sandbox.

Contract under test (the tagging rule the task specifies):
  - confident top match           → that initiative's slug
  - non-confident / no overlap    → None
  - empty store / empty subject    → None
  - ANY exception in the router    → None (swallowed; caller keeps going)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import extract  # noqa: E402


# Fixture initiatives — the router only reads slug/repo/title.
INITIATIVES = [
    {"slug": "clawgate-chat-polish", "repo": "/repo/devrc",
     "title": "Clawgate chat polish"},
    {"slug": "activity-telemetry", "repo": "/repo/devrc",
     "title": "Activity telemetry pipeline"},
    {"slug": "mail-actions-routing", "repo": "/repo/devrc",
     "title": "Mail actions routing"},
]


def test_confident_match_returns_slug():
    slug = extract.related_initiative_slug(
        "Re: clawgate chat polish feedback", INITIATIVES)
    assert slug == "clawgate-chat-polish"


def test_single_distinctive_slug_token_is_confident():
    # One slug hit, but 'telemetry' is unique to that initiative → confident.
    slug = extract.related_initiative_slug(
        "question about telemetry retention", INITIATIVES)
    assert slug == "activity-telemetry"


def test_non_confident_subject_returns_none():
    # No slug/title token overlaps any initiative → no match.
    slug = extract.related_initiative_slug(
        "Your parcel has shipped", INITIATIVES)
    assert slug is None


def test_empty_store_returns_none():
    assert extract.related_initiative_slug("clawgate chat polish", []) is None


def test_empty_subject_returns_none():
    assert extract.related_initiative_slug("", INITIATIVES) is None
    assert extract.related_initiative_slug(None, INITIATIVES) is None


def test_router_exception_is_swallowed(monkeypatch, capsys):
    def _boom():
        raise RuntimeError("scan matcher exploded")

    monkeypatch.setattr(extract, "_route", _boom)
    slug = extract.related_initiative_slug("clawgate chat polish", INITIATIVES)
    assert slug is None
    # Failure reported to stderr, never raised.
    assert "initiative routing failed" in capsys.readouterr().err


def test_rank_matches_exception_is_swallowed(monkeypatch):
    # Even if rank_matches itself blows up mid-call, the helper returns None.
    class _BadRoute:
        @staticmethod
        def rank_matches(*a, **k):
            raise ValueError("bad rank")

    monkeypatch.setattr(extract, "_route", lambda: _BadRoute)
    assert extract.related_initiative_slug("clawgate chat polish", INITIATIVES) is None
