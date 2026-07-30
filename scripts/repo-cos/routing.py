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

SECOND CONSUMER (2026-07-29): the resolved slug is no longer display-only — an
APPROVED proposal now carries it to clawgate as an `initiative:<slug>` task tag
(see clawgate.build_tags). Because a tag is a routing key someone will act on, the
raw store vocabulary is first passed through `taggable_slug()` — a deterministic
DENYLIST that drops slugs which are document/scaffold artefacts rather than real
initiatives. See its docstring for the rules and why each exists.
"""
from __future__ import annotations

import importlib.util
import re
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


# ---- taggable-slug denylist ---------------------------------------------------------
# The `initiatives.current` vocabulary (~139 slugs) is MINED, not curated: the scan adopts
# session titles and handoff-doc filenames verbatim, so alongside real arcs
# (`clawgate-agent-loop-close`, `dp-prod-latency-sweep`) it contains document scaffolding
# (`HANDOFF`, `SESSION-HANDOFF`), bare dates (`2026-07-21`), ClickUp-id salad
# (`868j34n9y-868kf6w7r-complete-mark`) and pure filler (`actionable-next-steps`).
#
# Showing one of those as a `↳ relates to:` breadcrumb is harmless. STAMPING one onto a
# durable clawgate task as `initiative:handoff` is not — a routing key that means nothing
# is worse than no key at all. So the tag path (never the display path) filters the
# vocabulary through the rules below. They are deliberately CONSERVATIVE: a missing tag
# costs nothing, a wrong tag costs trust, so an ambiguous slug is KEPT.
#
# INVARIANT the rules preserve: a slug that survives is emitted VERBATIM as the value half
# of `initiative:<slug>`. `clawgate.normalize_tag` lowercases, so anything not already
# all-lowercase is dropped (`not-lowercase`) rather than mangled — that keeps the emitted
# tag byte-equal to the ledger slug, which is what the initiatives-side join relies on.

# A ClickUp/opaque task id: starts with a digit, >=8 alphanumerics, no separators.
# Matches 868j34n9y / 868kf6w7r / 868f9pd14; does NOT match 0313, 504, or v0.1.73.
_ID_TOKEN_RE = re.compile(r"^\d[0-9a-z]{7,}$")
_TOKEN_SPLIT_RE = re.compile(r"[-_./]+")
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")

# Words that describe a DOCUMENT or a generic process step and never a project. A slug
# made ENTIRELY of these carries no initiative identity. Kept small on purpose — every
# addition risks dropping a real slug, and the router's confidence gate already filters
# most noise upstream.
GENERIC_TOKENS = frozenset({
    # document scaffolding
    "handoff", "handoffs", "session", "sessions", "notes", "note", "doc", "docs",
    "readme", "summary", "kickoff", "draft", "untitled", "misc", "scratch",
    "temp", "tmp", "wip", "todo",
    # process filler
    "next", "previous", "prev", "last", "past", "latest", "current", "new", "final",
    "actionable", "step", "steps", "start", "started", "continue", "complete",
    "completed", "done", "update", "updates", "work", "working", "task", "tasks",
    "item", "items", "thing", "things", "stuff", "progress", "status",
    # english filler the tokenizer leaves behind
    "and", "the", "with", "from", "for", "to", "of", "a", "an", "is", "it", "this",
    "that", "in", "on", "yes", "no", "ok", "few", "more", "some", "both",
    # time words
    "day", "days", "week", "weeks", "month", "months", "hour", "hours", "year",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
})


def slug_tokens(slug: str) -> list[str]:
    """Lowercased word tokens of a slug, split on `- _ . /`. Empty parts dropped."""
    return [t for t in _TOKEN_SPLIT_RE.split((slug or "").strip().lower()) if t]


def slug_drop_reason(slug) -> str | None:
    """Why `slug` must NOT become an `initiative:` tag, or None when it may.

    PURE + deterministic (no I/O, no logging) so every rule is table-testable. Rules,
    each pinned by a real slug from `initiatives.current`:

      * "empty"          — nothing to tag.
      * "too-short"      — <3 chars; can't identify anything.
      * "no-letters"     — a bare date/number, e.g. `2026-07-21`. The scan adopted a
                           dated filename as a card label; it names no initiative.
      * "not-lowercase" — the slug is not ALREADY all-lowercase (`s != s.lower()`), e.g.
                           `HANDOFF`, `SESSION-HANDOFF`, `APP-DISCOVERY-DESIGN`,
                           `HANDOFF-comfyui-session`, `SECURITY-AUDIT-v0.1.64`. The
                           load-bearing reason is the JOIN, not the shouting: the tag
                           grammar forces lowercase (`clawgate.normalize_tag`), so any
                           slug carrying an uppercase letter would be emitted as a tag
                           that no longer equals its ledger slug and the initiatives-side
                           join would silently miss. Dropping is the only degradation
                           that keeps `tag == slug` an invariant — a missing tag is
                           cheap, a tag that joins to nothing is a lie. (Most of these
                           are also doc FILENAMEs adopted verbatim — `HANDOFF.md`,
                           `HANDOFF-comfyui-session.md` — i.e. documents, not arcs.)
      * "opaque-id"      — contains a ClickUp-style id token, e.g.
                           `868j34n9y-868kf6w7r-complete-mark`.
      * "generic"        — EVERY token is document/process filler, e.g.
                           `actionable-next-steps`, `next-session`, `past-sessions-week`.
    """
    s = str(slug or "").strip()
    if not s:
        return "empty"
    if len(s) < 3:
        return "too-short"
    if not _HAS_LETTER_RE.search(s):
        return "no-letters"
    if s != s.lower():
        return "not-lowercase"
    toks = slug_tokens(s)
    if not toks:
        return "empty"
    if any(_ID_TOKEN_RE.match(t) for t in toks):
        return "opaque-id"
    if all(t in GENERIC_TOKENS or t.isdigit() for t in toks):
        return "generic"
    return None


def taggable_slug(slug) -> str | None:
    """The slug if it may become an `initiative:` tag, else None — LOGGING every drop.

    Silent filtering is not acceptable here: if a proposal's breadcrumb shows a slug but
    the task carries no tag, the run log must say which rule dropped it. Never raises."""
    try:
        s = str(slug or "").strip()
        reason = slug_drop_reason(s)
        if reason is None:
            return s
        _log(f"dropped initiative slug {s!r} — not taggable ({reason})")
        return None
    except Exception as exc:  # noqa: BLE001 - best-effort: a tag must never break a post
        _log(f"taggable_slug({slug!r}) failed: {exc}")
        return None
