#!/usr/bin/env python3
"""SURFACE-ONLY initiative tagging for repo-cos proposals.

Given the proposals llm.synthesize produced, tag each with the EXISTING initiative
it relates to (if any) so the digest can show a `↳ relates to: <slug>` breadcrumb.
It is strictly DISPLAY — no dispatch, no writes, no effect on the exclusions /
approve / clawgate-task flow. A proposal that doesn't confidently match any
initiative simply carries no tag.

Consumes the Phase-2 router (`scripts/initiatives/route.py`, a READ-ONLY view over
the Phase-1 `initiatives.current` store). The store lives in the homelab `mailbox`
Postgres and is read via `route.load_current()` — a kubectl port-forward — so we
call it EXACTLY ONCE per repo-cos run and rank every proposal against that one
in-memory snapshot (never per-proposal).

BEST-EFFORT + SAFE (the load-bearing contract): every entry point that touches the
store or the router is wrapped so ANY failure — store unreachable, no kubeconfig,
import error, malformed rows — is logged to stderr and yields NO tags. The digest
must still render and send byte-for-byte as it did before this feature existed.

Tagging rule (see the router's confidence model): a proposal is tagged with
`ranked[0]["slug"]` ONLY when `ranked[0]["confident"]` is true — the single top
row, not every confident row. Shared multi-token prefixes can make several sibling
initiatives "confident" at once, so surfacing more than the top one would be noise;
a low-confidence best row is dropped entirely rather than shown as a weak guess.

`route.py` is loaded by EXPLICIT importlib path (NOT via sys.path) for the same
reason feedback.py loads `mail-actions/_db.py` that way: the mail-actions dir ships
its own `llm.py` that would SHADOW repo-cos's `llm.py` if that dir hit sys.path and
break synthesis. route.py imports only stdlib at module load (its own scan/db
imports are lazy), so a standalone load is safe.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# The Phase-2 router (scripts/initiatives/route.py). Sibling package dir, loaded by
# explicit path — do NOT add scripts/initiatives OR scripts/mail-actions to sys.path.
ROUTE_PATH = Path(__file__).resolve().parents[1] / "initiatives" / "route.py"

_route_mod = None


def _log(msg: str) -> None:
    print(f"  routing: {msg}", file=sys.stderr)


def _route():
    """Load initiatives/route.py by explicit importlib path and cache it.

    Lazy + cached so importing `routing` costs nothing and the router (which itself
    lazily pulls in the scan's tokenizers on first match) is only paid for when we
    actually tag. Raises ImportError if the file can't be loaded — callers wrap it."""
    global _route_mod
    if _route_mod is None:
        spec = importlib.util.spec_from_file_location("repo_cos_route", ROUTE_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {ROUTE_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _route_mod = mod
    return _route_mod


def signal_text(proposal) -> str:
    """The human-readable text repo-cos has for a proposal, for the router to match on.

    Duck-typed on the fields llm.Proposal exposes (title / why / approach) — the
    title plus its rationale/approach is the richest description we have. Missing
    fields are tolerated (a bare dict or a partial object still works)."""
    parts = []
    for attr in ("title", "why", "approach"):
        val = getattr(proposal, attr, None)
        if val is None and isinstance(proposal, dict):
            val = proposal.get(attr)
        if val:
            parts.append(str(val))
    return "  ".join(parts).strip()


def _proposal_repo(proposal):
    """The proposal's repo scope for the router (None when absent → match all repos)."""
    repo = getattr(proposal, "repo", None)
    if repo is None and isinstance(proposal, dict):
        repo = proposal.get("repo")
    repo = (str(repo).strip() if repo else "")
    return repo or None


def tag_proposals(proposals, initiatives) -> list:
    """PURE: return a related-initiative slug (or None) per proposal, index-aligned.

    No I/O — `initiatives` is an already-loaded snapshot of `initiatives.current`.
    For each proposal we rank the existing initiatives against its signal text
    (scoped to the proposal's repo) and take the TOP row's slug iff it's confident,
    else None. An empty `initiatives` list → all None (nothing to relate to)."""
    route = _route()
    related: list = []
    for p in proposals:
        text = signal_text(p)
        slug = None
        if text and initiatives:
            ranked = route.rank_matches(text, initiatives,
                                        repo=_proposal_repo(p), limit=1)
            if ranked and ranked[0].get("confident"):
                slug = ranked[0].get("slug")
        related.append(slug)
    return related


def related_for(proposals) -> list:
    """BEST-EFFORT: load `initiatives.current` ONCE, tag every proposal, never raise.

    The single entry point repo-cos calls. Any failure — router import error, store
    unreachable, no kubeconfig, bad rows — is logged and degrades to NO tags (a list
    of None the same length as `proposals`) so the digest is unaffected. Returns an
    index-aligned list[str | None]."""
    n = len(proposals)
    if not n:
        return []
    try:
        route = _route()
        initiatives = route.load_current()
    except Exception as exc:  # noqa: BLE001 - best-effort: never break the digest
        _log(f"could not load initiatives (proceeding without tags): {exc}")
        return [None] * n
    try:
        related = tag_proposals(proposals, initiatives)
    except Exception as exc:  # noqa: BLE001 - best-effort: never break the digest
        _log(f"tagging failed (proceeding without tags): {exc}")
        return [None] * n
    tagged = sum(1 for s in related if s)
    _log(f"tagged {tagged}/{n} proposal(s) with a related initiative "
         f"(from {len(initiatives)} in the store)")
    return related
