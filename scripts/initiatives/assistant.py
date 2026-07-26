#!/usr/bin/env python3
"""Read-only "initiatives assistant" — natural-language Q&A + routing over the store.

PHASE 1 of the "initiatives agent" (the deliberately RE-SCOPED phase from the red-team
eval `claudedocs/initiatives-agent-proposal-eval-2026-07-24.md`). It is a plain Python
assistant over the EXISTING initiatives store + `route()` + the local model. There is
NO containerized runtime, NO clawgate/openclaw, NO MCP, NO dispatch, and — the load-
bearing property — NO path that MUTATES state, dispatches work, writes a handoff, or
reaches outside the initiatives store. Every tool below is strictly READ-ONLY.

Why read-only matters (respect this boundary): the eval's core finding is that a
write/dispatch agent's safety gate would be *voluntary* (prompt-injection-defeatable).
Phase 1 sidesteps ALL of that: the worst case of a prompt-injected handoff/status the
model reads is a weird ANSWER, never an action. Do not add a mutating tool here.

Shape (robust on a small local model — Qwen2.5-7B via the homelab `vllm-recap` endpoint):
  we do NOT ask the 7B to plan multi-step tool use. Instead the flow is mostly
  DETERMINISTIC and the model is used only for the two things it is good at:
    (a) fuzzy question -> intent/args mapping (ONLY when the deterministic classifier
        is unsure), and
    (b) phrasing a natural, grounded answer over the tool's REAL output.
  The deterministic tool output is the GROUND TRUTH. `sources` (which initiatives the
  answer draws from) is computed deterministically from that output — never from the
  model — so answers stay auditable and anti-confabulation holds even if the model
  hallucinates. If the model or the store is unreachable we DEGRADE GRACEFULLY: the
  deterministic tool result is rendered plainly (or a clear error) — we never crash.

Layering (mirrors route.py / viewer.py): the PURE logic (intent parsing, the tool
queries, the plain renderer, the fact projection) is separated from all I/O (the store
read, the model call) so it is unit-testable with fixtures — no live DB, no live model.

Reuse (do not reimplement):
  - the store read + the normalized initiative "view" dicts: `viewer.load_latest` +
    `viewer.attach_tmux` + `viewer.build_model` (its `flat` list is exactly the shape
    the tools want). Imported lazily to avoid a viewer<->assistant import cycle.
  - routing / name-matching: `route.rank_matches` (the scan's single-sourced token
    matcher) powers both `route_signal` and the fuzzy "which initiative is X" lookup.
  - the handoff read: `viewer.read_doc_detail_live` (the size-capped, traversal-guarded
    reader) — never a raw open().
  - the model client: `recap.VllmClient` + `recap.recap_config` (the OpenAI-compatible
    call over the kubectl port-forward, same endpoint the recap generator uses).

Every ask is AUDIT-LOGGED (best-effort) to a standalone `initiatives.assistant_log` table
the assistant owns — the question, classified intent/target, cited sources, answer, model
id, whether the model was used, and latency — so asks are reviewable after the fact. The
log write can NEVER change or break the answer (the assistant stays read-only w.r.t.
initiatives state); a DB/table failure degrades to a stderr warning. `assistant.py log`
prints the recent asks for review.

CLI:
    assistant.py "what's blocked on me?"
    assistant.py --json "status of clawgate"
    assistant.py log [--limit N] [--json]     # review recent asks (the audit log)
On NixOS (for the live read/model) run under:
    nix-shell -p "python3.withPackages(p:[p.psycopg2 p.requests])" \
      --run 'python scripts/initiatives/assistant.py "what am I working on?"'
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

# Sibling modules in this same directory. route/viewer/recap are imported LAZILY (inside
# the functions that need them) so that (a) the viewer<->assistant pair has no import
# cycle, and (b) merely importing `assistant` (e.g. for a unit test of the pure core)
# costs nothing and needs no psycopg2/requests.
_ROUTE_PATH = Path(__file__).resolve().parent / "route.py"
_RECAP_PATH = Path(__file__).resolve().parent / "recap.py"
_VIEWER_PATH = Path(__file__).resolve().parent / "viewer.py"
# The pure, stdlib-only grounded next-step recommender (the `recommend_next_step` tool).
_NEXTSTEP_PATH = Path(__file__).resolve().parent / "nextstep.py"
# The shared mailbox-Postgres helper (kubectl port-forward + psycopg2 + DSN-from-secret) —
# ONLY used by the audit-log write/read path (the pure Q&A core never touches it).
_MAILDB_PATH = Path(__file__).resolve().parents[1] / "mail-actions" / "_db.py"

_route_mod = None
_recap_mod = None
_viewer_mod = None
_nextstep_mod = None
_maildb_mod = None


def _load_sibling(path: Path, mod_name: str):
    """Load a sibling module by explicit importlib path and return it.

    Used for viewer/recap/route so a `python assistant.py` run (sys.path[0] = this dir)
    AND a test that only inserts this dir both resolve them, without depending on package
    layout — and without importing them at module load (keeping the pure-core import
    side-effect free)."""
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _route():
    global _route_mod
    if _route_mod is None:
        _route_mod = _load_sibling(_ROUTE_PATH, "initiatives_assistant_route")
    return _route_mod


def _recap():
    global _recap_mod
    if _recap_mod is None:
        _recap_mod = _load_sibling(_RECAP_PATH, "initiatives_assistant_recap")
    return _recap_mod


def _viewer():
    global _viewer_mod
    if _viewer_mod is None:
        _viewer_mod = _load_sibling(_VIEWER_PATH, "initiatives_assistant_viewer")
    return _viewer_mod


def _nextstep():
    global _nextstep_mod
    if _nextstep_mod is None:
        _nextstep_mod = _load_sibling(_NEXTSTEP_PATH, "initiatives_assistant_nextstep")
    return _nextstep_mod


def _maildb():
    """Load MailDB from mail-actions/_db.py by EXPLICIT importlib path (NOT via sys.path —
    its sibling llm.py shadows other modules; documented in the repo CLAUDE.md). Mirrors
    sync.py / viewer.py. Invoked ONLY on the audit-log write/read path, never the pure
    Q&A core (so a plain import of `assistant` stays free of psycopg2/kubectl)."""
    global _maildb_mod
    if _maildb_mod is None:
        spec = importlib.util.spec_from_file_location(
            "initiatives_assistant_maildb", _MAILDB_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {_MAILDB_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _maildb_mod = mod.MailDB
    return _maildb_mod


# --------------------------------------------------------------------------- #
# Intents — the fixed, enumerated question shapes the assistant understands.
# --------------------------------------------------------------------------- #
INTENTS = (
    "blocked_on_me",   # what's waiting on me / awaiting my input / my call
    "active",          # what's active / in flight / what am I working on
    "slowing",         # what's slowing / losing momentum
    "stalled",         # what's stalled / stuck / gone quiet
    "most_recent",     # what did I touch last / most recently active
    "live_sessions",   # what's running right now (live tmux)
    "status_of",       # status of / where did I leave <named initiative>   (-> target)
    "recommend_next_step",  # what should I do next on <named initiative>    (-> target)
    "by_repo",         # what's in <repo> / group by repo                    (-> target?)
    "route",           # which initiative does <X> belong to / triage <X>    (-> target)
    "handoff",         # read the handoff / more detail on <named initiative> (-> target)
    "overview",        # catch-all: what are my initiatives
)

# Text markers that mean "this initiative is waiting on the human" — scanned ONLY across an
# initiative's real action/block fields (`_BLOCKED_FIELDS` = status + next_step, per spec).
# Kept as a tunable module constant (unit-tested), NOT a prose instruction to the model.
#
# The set is deliberately narrow: it holds phrases that assert a genuine WAIT on the user
# ("awaiting", "your call", "waiting on you", "pending your", …). It must NOT hold phrases
# that merely DESCRIBE a project's purpose or the deploy/verify PROCESS — on a personal
# board every initiative's purpose mentions "Zach"/"your"/"review", so a descriptive marker
# guarantees a false positive. Retired for exactly that reason (they matched handoff/status
# *process* prose, not a real block): "for zach to" (e.g. "cards for Zach to adjudicate"),
# "eyeball", "human deploys", "human verif", "you deploy".
BLOCKED_MARKERS = (
    "awaiting", "blocked on", "blocked", "your call", "your input", "your decision",
    "your review", "your sign-off", "your signoff", "your go-ahead", "your go ahead",
    "waiting on you", "waiting for you", "waiting on zach", "waiting for zach",
    "needs you", "needs zach", "need zach", "need you to", "pending your",
    "up to zach", "hand to the user", "hand it to the user",
)

# The initiative fields the blocked-scan reads — its REAL action/block surface. Only status
# + next_step: these carry what is actually pending. Deliberately EXCLUDES `identity` (the
# project's durable PURPOSE — e.g. "…decision-ready cards for Zach to adjudicate…") and
# `summary` (descriptive recall text); scanning either turns every purpose that names the
# user into a false "blocked on me". Tunable + unit-tested.
_BLOCKED_FIELDS = ("status", "next_step")

# How many initiatives (max) to hand the model as grounding facts, and per-field trims —
# keep the 7B's context small + on-topic.
_MODEL_FACT_CAP = 14
_FACT_TEXT_TRIM = 220
_QUESTION_MAX = 2000  # cap an incoming question (CLI + /api/ask) — abuse/context guard

# Model call sizing (the recap client defaults to a tiny 160-token cap tuned for a 1-line
# recap; a chat answer wants a little more room, classification almost none).
_SYNTH_MAX_TOKENS = 320
_SYNTH_TEMPERATURE = 0.2
_CLASSIFY_MAX_TOKENS = 64
_CLASSIFY_TEMPERATURE = 0.0


# --------------------------------------------------------------------------- #
# Small pure helpers.
# --------------------------------------------------------------------------- #
def _short(repo) -> str:
    return os.path.basename(str(repo).rstrip("/")) if repo else ""


def _trim(text, n: int) -> str:
    s = (text or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _clean_target(text: str) -> str:
    """Normalize an extracted initiative name / signal: strip trailing punctuation,
    quotes, and filler words a natural question wraps the name in."""
    t = (text or "").strip().strip("?.!,:;\"'` ").strip()
    t = re.sub(r"^(the|my|our)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+(initiative|project|work|thread|effort)$", "", t, flags=re.I)
    t = re.sub(r"\s+(going|doing|coming along|at|now|these days)$", "", t, flags=re.I)
    return t.strip()


# --------------------------------------------------------------------------- #
# Intent classification — DETERMINISTIC first (pure, unit-tested). The model only
# refines an `overview` fallback (see `ask`), never overrides a confident parse.
# --------------------------------------------------------------------------- #
# (pattern, intent, capture-group-is-target) — evaluated in order, most specific first.
# A capturing group, when present, yields the target/signal for that intent.
_PATTERNS: list[tuple[re.Pattern, str, bool]] = [
    # -- routing / triage (needs a signal) --
    (re.compile(r"which initiative(?:\s+does)?\s+(.+?)\s+(?:belong|relate|go|fit)", re.I), "route", True),
    (re.compile(r"where does\s+(.+?)\s+(?:belong|go|fit)", re.I), "route", True),
    (re.compile(r"\b(?:route|triage)\b\s+(?:this\s+)?(.+)", re.I), "route", True),
    (re.compile(r"what initiative is\s+(.+?)\s+(?:part of|related to)", re.I), "route", True),
    # -- handoff / deep detail on a named initiative --
    (re.compile(r"(?:read|open|show)\s+(?:me\s+)?the handoff\s+(?:doc\s+)?(?:for|on|of)\s+(.+)", re.I), "handoff", True),
    (re.compile(r"handoff\s+(?:doc\s+)?(?:for|on|of)\s+(.+)", re.I), "handoff", True),
    (re.compile(r"(?:full\s+)?details?\s+(?:on|for|about)\s+(.+)", re.I), "handoff", True),
    (re.compile(r"tell me (?:more|everything)\s+about\s+(.+)", re.I), "handoff", True),
    # -- blocked on me (the headline ask) --
    (re.compile(r"blocked on (?:me|you|zach)", re.I), "blocked_on_me", False),
    (re.compile(r"waiting (?:on|for) (?:me|you|zach)", re.I), "blocked_on_me", False),
    (re.compile(r"(?:needs?|require[sd]?)\s+(?:my|your)\s+(?:input|call|decision|review|sign)", re.I), "blocked_on_me", False),
    (re.compile(r"what(?:'s| is)?\s+(?:blocking|blocked|waiting)(?:\s+on\s+me)?\b", re.I), "blocked_on_me", False),
    (re.compile(r"what needs (?:me|my)\b", re.I), "blocked_on_me", False),
    (re.compile(r"\b(?:my|your)\s+(?:call|input|decision|sign-?off|go-?ahead)\b", re.I), "blocked_on_me", False),
    (re.compile(r"awaiting\s+(?:me|my|zach)", re.I), "blocked_on_me", False),
    # -- momentum buckets --
    (re.compile(r"\b(?:stalled|stuck|abandoned|gone quiet|dropped|dormant)\b", re.I), "stalled", False),
    (re.compile(r"\b(?:slowing|losing momentum|cooling|slowed down)\b", re.I), "slowing", False),
    # -- live sessions right now --
    (re.compile(r"\blive session", re.I), "live_sessions", False),
    (re.compile(r"running (?:right )?now", re.I), "live_sessions", False),
    (re.compile(r"what(?:'s| is)?\s+running\b", re.I), "live_sessions", False),
    (re.compile(r"\bin tmux\b|tmux session", re.I), "live_sessions", False),
    (re.compile(r"what (?:am i|are we) running", re.I), "live_sessions", False),
    # -- most recently active --
    (re.compile(r"most recent(?:ly)?(?:\s+active)?", re.I), "most_recent", False),
    (re.compile(r"last (?:touched|worked on|active)", re.I), "most_recent", False),
    (re.compile(r"what did i (?:last|just)\b", re.I), "most_recent", False),
    (re.compile(r"\blatest\b", re.I), "most_recent", False),
    # -- recommend the next step on a NAMED initiative (capture the name). BEFORE status_of
    #    so "what should I work on next on X" / "next step for X" win over a status read. --
    (re.compile(r"what (?:should|do) i (?:work on|do) next (?:on|for|with)\s+(.+)", re.I),
     "recommend_next_step", True),
    (re.compile(r"(?:recommend|suggest)\s+(?:a\s+)?next step (?:for|on)\s+(.+)", re.I),
     "recommend_next_step", True),
    (re.compile(r"next step (?:for|on)\s+(.+)", re.I), "recommend_next_step", True),
    # -- status of a NAMED initiative (capture the name) --
    (re.compile(r"status(?:\s+of|\s+on)\s+(.+)", re.I), "status_of", True),
    (re.compile(r"state of\s+(.+)", re.I), "status_of", True),
    (re.compile(r"where did i leave\s+(?:off\s+(?:on|with)\s+)?(.+)", re.I), "status_of", True),
    (re.compile(r"what(?:'s| is)?\s+(?:happening|going on|up)\s+(?:with|on|for)\s+(.+)", re.I), "status_of", True),
    (re.compile(r"update on\s+(.+)", re.I), "status_of", True),
    (re.compile(r"how(?:'s| is| are)\s+(.+?)\s+(?:going|doing|coming along|progressing)", re.I), "status_of", True),
    # -- by repo (optional target repo) --
    (re.compile(r"group(?:ed)? by repo|by repo\b", re.I), "by_repo", False),
    (re.compile(r"(?:initiatives?|work|what(?:'s| is)?)\s+in (?:the\s+)?(.+?)\s+repo", re.I), "by_repo", True),
    (re.compile(r"what(?:'s| is)?\s+in (?:the\s+)?([a-z0-9._-]+)\s*\??$", re.I), "by_repo", True),
    # -- active / in flight / what am I working on --
    (re.compile(r"what am i working on", re.I), "active", False),
    (re.compile(r"in[- ]flight|in flight", re.I), "active", False),
    (re.compile(r"\b(?:active|in progress)\b", re.I), "active", False),
    (re.compile(r"what(?:'s| is)?\s+(?:going on|current|cooking)\b", re.I), "active", False),
    # -- overview / list all --
    (re.compile(r"what are (?:all )?my initiatives|list (?:my )?initiatives|overview|everything", re.I), "overview", False),
]


def classify_intent(question: str) -> dict:
    """PURE: question -> {"intent", "target"}.

    Deterministic keyword/regex matching, most-specific first. `target` is the extracted
    initiative name (status_of/handoff/by_repo) or free signal (route), cleaned; "" when
    the intent takes no argument or none was found. Returns intent="overview" (the safe
    catch-all) when nothing matches — the caller may then ask the model to refine (best-
    effort) before falling back to a broad summary."""
    q = (question or "").strip()
    if not q:
        return {"intent": "overview", "target": ""}
    for pat, intent, is_target in _PATTERNS:
        m = pat.search(q)
        if not m:
            continue
        target = ""
        if is_target and m.groups():
            target = _clean_target(m.group(1))
        return {"intent": intent, "target": target}
    return {"intent": "overview", "target": ""}


# --------------------------------------------------------------------------- #
# READ-ONLY tools — PURE functions over the normalized initiative "view" list
# (viewer.build_model's `flat`). Each returns a structured result whose facts are the
# ground truth for both the model synthesis and the deterministic `sources`/renderer.
# --------------------------------------------------------------------------- #
def _blocking_hits(view: dict) -> list[str]:
    """The BLOCKED_MARKERS that appear in an initiative's REAL action/block fields ONLY
    (`_BLOCKED_FIELDS` = status + next_step, per spec). Empty => not blocked.

    Deliberately does NOT scan `identity`/`summary`: those describe the project's durable
    purpose / recall text, not what's pending — and on a personal board a purpose almost
    always names the user ("…cards for Zach to adjudicate…"), so scanning them manufactures
    false positives. The block signal lives in what's ACTIONABLE (status + next_step)."""
    hay = " ".join(str(view.get(f) or "") for f in _BLOCKED_FIELDS).lower()
    return [mk for mk in BLOCKED_MARKERS if mk in hay]


def tool_blocked_on_me(views: list[dict]) -> dict:
    """Initiatives whose status/next_step (etc.) say they are waiting on the human."""
    hits = []
    for v in views:
        marks = _blocking_hits(v)
        if marks:
            hits.append({"view": v, "markers": marks})
    hits.sort(key=lambda h: _recency_key(h["view"]))
    return {"kind": "blocked_on_me", "initiatives": [h["view"] for h in hits],
            "markers": {h["view"].get("slug"): h["markers"] for h in hits}}


def tool_by_momentum(views: list[dict], momentum: str) -> dict:
    inis = [v for v in views if (v.get("momentum") or "unknown") == momentum]
    inis.sort(key=_recency_key)
    return {"kind": momentum, "initiatives": inis}


def tool_most_recent(views: list[dict], n: int = 1) -> dict:
    ordered = sorted(views, key=_recency_key)
    return {"kind": "most_recent", "initiatives": ordered[: max(1, n)]}


def tool_live_sessions(views: list[dict], unmatched: list[dict] | None) -> dict:
    """Initiatives with a live tmux session right now, plus the untracked live panes."""
    tied = [v for v in views if v.get("live_tasks") or v.get("tmux_sessions")]
    tied.sort(key=_recency_key)
    return {"kind": "live_sessions", "initiatives": tied,
            "untracked": list(unmatched or [])}


def tool_by_repo(views: list[dict], target: str = "") -> dict:
    """Group initiatives by repo; if `target` names a repo, scope to it."""
    want = _short(target).lower() if target else ""
    groups: dict[str, list[dict]] = {}
    for v in views:
        name = v.get("repo_name") or _short(v.get("repo")) or "(unknown)"
        if want and want not in name.lower():
            continue
        groups.setdefault(name, []).append(v)
    for inis in groups.values():
        inis.sort(key=_recency_key)
    flat = [v for inis in groups.values() for v in inis]
    return {"kind": "by_repo", "target": target,
            "groups": {k: groups[k] for k in sorted(groups)}, "initiatives": flat}


def tool_status_of(views: list[dict], target: str) -> dict:
    """Fuzzy-find the named initiative(s) via the SAME token matcher the router uses
    (route.rank_matches over the view dicts, which already carry slug/repo/title), then
    return the FULL views for the top matches — single-sourced name resolution."""
    target = (target or "").strip()
    if not target or not views:
        return {"kind": "status_of", "target": target, "initiatives": [], "ranked": []}
    ranked = _route().rank_matches(target, views, limit=5)
    by_key = {(v.get("repo"), v.get("slug")): v for v in views}
    matched: list[dict] = []
    for r in ranked:
        v = by_key.get((r.get("repo"), r.get("slug")))
        if v is not None:
            matched.append(v)
    # If the matcher found nothing (no shared token), fall back to a plain substring scan
    # so a partial name a human types ("clawgate") still resolves even below the token bar.
    if not matched:
        tl = target.lower()
        matched = [v for v in views
                   if tl in str(v.get("slug") or "").lower()
                   or tl in str(v.get("title") or "").lower()]
        matched.sort(key=_recency_key)
    return {"kind": "status_of", "target": target,
            "initiatives": matched[:5], "ranked": ranked}


def tool_recommend_next_step(views: list[dict], target: str) -> dict:
    """Resolve the named initiative (via tool_status_of — SAME single-sourced name matcher),
    then derive its GROUNDED next-step recommendation (nextstep.recommend_next_step). The
    recommendation text is a close paraphrase/quote of a real view field, never invented; None
    when no field supports one. Read-only; never mutates or dispatches (that's the viewer's
    /api/dispatch endpoint, behind two human gates)."""
    resolved = tool_status_of(views, target)
    inis = resolved["initiatives"]
    if not inis:
        return {"kind": "recommend_next_step", "target": target,
                "initiative": None, "recommendation": None}
    v = inis[0]
    recommendation = None
    try:
        recommendation = _nextstep().recommend_next_step(v)
    except Exception:  # noqa: BLE001 - a recommendation is best-effort; degrade to None
        recommendation = None
    return {"kind": "recommend_next_step", "target": target,
            "initiative": v, "recommendation": recommendation}


def tool_route(views: list[dict], signal: str) -> dict:
    """Delegate to route.rank_matches: which existing initiative(s) does this signal
    belong to? Returns the ranking + the verdict (confident match vs likely new work)."""
    route = _route()
    ranked = route.rank_matches(signal or "", views, limit=5)
    return {"kind": "route", "signal": signal, "ranked": ranked,
            "verdict": route.classify(ranked)}


def tool_overview(views: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = {"active": [], "slowing": [], "stalled": [],
                                      "unknown": []}
    for v in views:
        buckets.setdefault(v.get("momentum") or "unknown", buckets["unknown"]).append(v)
    for inis in buckets.values():
        inis.sort(key=_recency_key)
    return {"kind": "overview", "initiatives": sorted(views, key=_recency_key),
            "buckets": buckets, "total": len(views)}


def tool_read_handoff(views: list[dict], target: str, reader=None) -> dict:
    """Resolve the named initiative (via tool_status_of), then read its handoff doc off
    disk through viewer's size-capped, traversal-guarded reader. `reader` is injectable
    (repo, current_doc) -> detail|None for tests. Read-only; None on any guard failure."""
    resolved = tool_status_of(views, target)
    inis = resolved["initiatives"]
    if not inis:
        return {"kind": "handoff", "target": target, "initiative": None, "detail": None}
    v = inis[0]
    if reader is None:
        reader = _viewer().read_doc_detail_live
    detail = None
    try:
        detail = reader(v.get("repo"), v.get("current_doc"))
    except Exception:  # noqa: BLE001 - a read hiccup degrades to the stored fields
        detail = None
    return {"kind": "handoff", "target": target, "initiative": v, "detail": detail}


def _recency_key(v: dict):
    """Sort key: most-recently-active first (last_touch DESC, None last)."""
    lt = v.get("last_touch")
    ts = None
    if hasattr(lt, "timestamp"):
        try:
            ts = lt.timestamp()
        except Exception:  # noqa: BLE001
            ts = None
    return (-(ts if ts is not None else float("-inf")), v.get("slug") or "")


def run_tool(intent: str, info: dict, views: list[dict],
             unmatched: list[dict] | None) -> dict:
    """Dispatch a classified intent to its READ-ONLY tool. The returned dict's facts are
    the ground truth for both `sources_of` and the model synthesis."""
    target = info.get("target") or ""
    if intent == "blocked_on_me":
        return tool_blocked_on_me(views)
    if intent in ("active", "slowing", "stalled"):
        return tool_by_momentum(views, intent)
    if intent == "most_recent":
        return tool_most_recent(views, n=3)
    if intent == "live_sessions":
        return tool_live_sessions(views, unmatched)
    if intent == "by_repo":
        return tool_by_repo(views, target)
    if intent == "status_of":
        return tool_status_of(views, target)
    if intent == "recommend_next_step":
        return tool_recommend_next_step(views, target)
    if intent == "route":
        return tool_route(views, target)
    if intent == "handoff":
        return tool_read_handoff(views, target)
    return tool_overview(views)


# --------------------------------------------------------------------------- #
# Sources — DETERMINISTIC citation of which initiatives an answer draws from. Computed
# from the tool result, NEVER from the model (the anti-confabulation anchor).
# --------------------------------------------------------------------------- #
def sources_of(result: dict) -> list[dict]:
    """The initiatives the tool result references -> [{slug, repo}] (de-duped, order
    preserved). For `route` these are the ranked candidates; for `handoff` the one
    resolved initiative; otherwise the returned initiative list."""
    out: list[dict] = []
    seen: set = set()

    def _add(slug, repo):
        key = (repo, slug)
        if slug and key not in seen:
            seen.add(key)
            out.append({"slug": slug, "repo": _short(repo)})

    kind = result.get("kind")
    if kind == "route":
        for r in result.get("ranked", []):
            _add(r.get("slug"), r.get("repo"))
    elif kind in ("handoff", "recommend_next_step"):
        v = result.get("initiative")
        if v:
            _add(v.get("slug"), v.get("repo"))
    else:
        for v in result.get("initiatives", []):
            _add(v.get("slug"), v.get("repo"))
    return out


# --------------------------------------------------------------------------- #
# Model grounding — a COMPACT, on-topic facts projection handed to the 7B (kept small).
# --------------------------------------------------------------------------- #
def _fact(v: dict, blocking: list[str] | None = None) -> dict:
    f = {
        "slug": v.get("slug"),
        "repo": v.get("repo_name") or _short(v.get("repo")),
        "momentum": v.get("momentum") or "unknown",
        "updated": v.get("age") or "?",
        "next_step": _trim(v.get("next_step"), _FACT_TEXT_TRIM),
        "status": _trim(v.get("status") or v.get("summary") or v.get("identity"),
                        _FACT_TEXT_TRIM),
        "live": bool(v.get("live_tasks") or v.get("tmux_sessions")),
    }
    if blocking:
        f["waiting_on_you_because"] = ", ".join(blocking)
    return {k: val for k, val in f.items() if val not in ("", None, False)}


def build_facts(result: dict) -> dict:
    """The ground-truth JSON handed to the model — small, on-topic, and derived ONLY from
    the deterministic tool result (so the model has nothing to confabulate from)."""
    kind = result.get("kind")
    markers = result.get("markers") or {}
    if kind == "route":
        ranked = result.get("ranked", [])
        return {
            "kind": kind,
            "signal": result.get("signal"),
            "verdict": result.get("verdict"),
            "candidates": [
                {"slug": r.get("slug"), "repo": _short(r.get("repo")),
                 "score": r.get("score"), "confident": r.get("confident")}
                for r in ranked[:_MODEL_FACT_CAP]
            ],
        }
    if kind == "handoff":
        v = result.get("initiative")
        detail = result.get("detail") or {}
        if not v:
            return {"kind": kind, "target": result.get("target"), "found": False}
        return {
            "kind": kind, "found": True, "initiative": _fact(v),
            "summary": _trim(detail.get("summary") or v.get("summary"), 400),
            "next_steps": [_trim(s, 200) for s in (detail.get("next_steps") or [])][:6],
            "open_investigations":
                [_trim(s, 200) for s in (detail.get("open_investigations") or [])][:6],
        }
    if kind == "recommend_next_step":
        v = result.get("initiative")
        if not v:
            return {"kind": kind, "target": result.get("target"), "found": False,
                    "initiative": None, "recommendation": None}
        rec = result.get("recommendation") or None
        rec_fact = None
        if rec:
            rec_fact = {"text": _trim(rec.get("text"), _FACT_TEXT_TRIM),
                        "basis": rec.get("basis")}
        # grounded_context = the REAL fields the recommendation was (or could be) drawn from —
        # the model phrases over THESE, inventing nothing. Only include fields that exist.
        face = v.get("face_message")
        face_text = _trim(face.get("text"), _FACT_TEXT_TRIM) if isinstance(face, dict) else ""
        ctx = {
            "next_step": _trim(v.get("next_step"), _FACT_TEXT_TRIM),
            "status": _trim(v.get("status"), _FACT_TEXT_TRIM),
            "face_message": face_text,
            "open_investigations":
                [_trim(s, 200) for s in (v.get("open_investigations") or [])][:4],
            "open_prs": [{"number": p.get("number"), "title": _trim(p.get("title"), 120)}
                         for p in (v.get("open_prs") or []) if isinstance(p, dict)][:4],
        }
        ctx = {k: val for k, val in ctx.items() if val not in ("", None, [], {})}
        return {"kind": kind, "target": result.get("target"), "found": True,
                "initiative": _fact(v), "recommendation": rec_fact,
                "grounded_context": ctx}
    if kind == "live_sessions":
        return {
            "kind": kind,
            "live_initiatives": [_fact(v) for v in result.get("initiatives", [])[:_MODEL_FACT_CAP]],
            "untracked_session_count": len(result.get("untracked", [])),
        }
    # filter / momentum / status_of / most_recent / overview / by_repo
    inis = result.get("initiatives", [])
    facts = {
        "kind": kind,
        "count": len(inis),
        "initiatives": [_fact(v, markers.get(v.get("slug"))) for v in inis[:_MODEL_FACT_CAP]],
    }
    if result.get("target"):
        facts["asked_about"] = result["target"]
    return facts


# --------------------------------------------------------------------------- #
# Deterministic answer renderer — the graceful-degradation path (model down/unset) AND
# the fallback when synthesis fails. Pure; unit-tested.
# --------------------------------------------------------------------------- #
def _one_line(v: dict) -> str:
    bits = [str(v.get("slug") or "?")]
    repo = v.get("repo_name") or _short(v.get("repo"))
    if repo:
        bits.append(f"({repo})")
    mom = v.get("momentum")
    if mom and mom != "unknown":
        bits.append(f"· {mom}")
    if v.get("age"):
        bits.append(f"· updated {v['age']} ago")
    line = " ".join(bits)
    tail = _trim(v.get("next_step") or v.get("status") or v.get("summary"), 160)
    return f"{line} — {tail}" if tail else line


def render_plain(intent: str, info: dict, result: dict) -> str:
    """PURE: a readable text answer from the tool result WITHOUT the model."""
    kind = result.get("kind")
    inis = result.get("initiatives", [])

    if kind == "blocked_on_me":
        if not inis:
            return "Nothing looks blocked on you right now."
        lines = [f"{len(inis)} initiative(s) look blocked on you:"]
        lines += [f"  • {_one_line(v)}" for v in inis]
        return "\n".join(lines)

    if kind in ("active", "slowing", "stalled"):
        if not inis:
            return f"No {kind} initiatives right now."
        lines = [f"{len(inis)} {kind} initiative(s):"]
        lines += [f"  • {_one_line(v)}" for v in inis]
        return "\n".join(lines)

    if kind == "most_recent":
        if not inis:
            return "No initiatives found."
        top = inis[0]
        head = f"Most recently active: {_one_line(top)}"
        if len(inis) > 1:
            head += "\nAlso recent:\n" + "\n".join(f"  • {_one_line(v)}" for v in inis[1:])
        return head

    if kind == "live_sessions":
        untracked = result.get("untracked", [])
        if not inis and not untracked:
            return "No live sessions running right now."
        lines = []
        if inis:
            lines.append(f"{len(inis)} initiative(s) have a live session:")
            for v in inis:
                task = (v.get("live_tasks") or [v.get("live_task")] or [""])[0] or ""
                lines.append(f"  • {v.get('slug')} — {_trim(task, 140)}" if task
                             else f"  • {v.get('slug')}")
        if untracked:
            lines.append(f"{len(untracked)} live session(s) not tied to an initiative.")
        return "\n".join(lines)

    if kind == "by_repo":
        groups = result.get("groups") or {}
        if not groups:
            tgt = result.get("target")
            return (f"No initiatives in a repo matching '{tgt}'." if tgt
                    else "No initiatives found.")
        lines = []
        for repo, items in groups.items():
            lines.append(f"{repo} ({len(items)}):")
            lines += [f"  • {_one_line(v)}" for v in items]
        return "\n".join(lines)

    if kind == "status_of":
        if not inis:
            return f"Couldn't find an initiative matching '{result.get('target')}'."
        top = inis[0]
        out = [_one_line(top)]
        if top.get("status"):
            out.append(f"  current › {_trim(top.get('status'), 200)}")
        if top.get("next_step"):
            out.append(f"  next › {_trim(top.get('next_step'), 200)}")
        if len(inis) > 1:
            out.append("Other possible matches: "
                       + ", ".join(v.get("slug") for v in inis[1:]))
        return "\n".join(out)

    if kind == "recommend_next_step":
        v = result.get("initiative")
        rec = result.get("recommendation") or None
        if not v or not rec or not str(rec.get("text") or "").strip():
            return (f"I don't have enough on '{result.get('target')}' to recommend a "
                    "next step.")
        repo = v.get("repo_name") or _short(v.get("repo"))
        hint = _nextstep().basis_label(rec.get("basis"))
        head = f"Recommended next step for {v.get('slug')}"
        if repo:
            head += f" ({repo})"
        return f"{head}: {rec.get('text')}" + (f" ({hint})." if hint else ".")

    if kind == "route":
        ranked = result.get("ranked", [])
        head = f"'{result.get('signal')}' → {result.get('verdict')}"
        if not ranked:
            return head + "\n  (no existing initiative shares a meaningful token.)"
        rows = [f"  • {r.get('slug')} ({_short(r.get('repo'))}) "
                f"score={r.get('score')}{' ✓' if r.get('confident') else ''}"
                for r in ranked]
        return head + "\n" + "\n".join(rows)

    if kind == "handoff":
        v = result.get("initiative")
        if not v:
            return f"Couldn't find an initiative matching '{result.get('target')}'."
        detail = result.get("detail") or {}
        out = [_one_line(v)]
        summ = _trim(detail.get("summary") or v.get("summary"), 400)
        if summ:
            out.append(summ)
        ns = detail.get("next_steps") or []
        if ns:
            out.append("Next steps:")
            out += [f"  • {_trim(s, 200)}" for s in ns[:6]]
        oi = detail.get("open_investigations") or []
        if oi:
            out.append("Open investigations:")
            out += [f"  • {_trim(s, 200)}" for s in oi[:6]]
        return "\n".join(out)

    # overview
    if not inis:
        return "No initiatives in the latest snapshot."
    buckets = result.get("buckets") or {}
    parts = [f"You have {len(inis)} initiative(s) in flight."]
    for name in ("active", "slowing", "stalled"):
        b = buckets.get(name) or []
        if b:
            parts.append(f"{name.capitalize()} ({len(b)}): "
                         + ", ".join(v.get("slug") for v in b[:8]))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Model I/O — best-effort intent refine + grounded answer synthesis.
# --------------------------------------------------------------------------- #
_SYNTH_SYSTEM = (
    "You are the initiatives assistant for Zach — you answer questions about his ongoing "
    "engineering work initiatives. You are given DATA: the exact, ground-truth result of a "
    "deterministic query over his initiatives store. Rules you MUST follow:\n"
    "1. Answer the QUESTION using ONLY the facts in DATA. Do not invent, guess, or add any "
    "initiative, count, status, next-step, or repo that is not present in DATA.\n"
    "2. Refer to each initiative by its exact slug.\n"
    "3. Do not restate numbers that aren't in DATA; if DATA has no initiatives, say plainly "
    "that none matched — do not fabricate one.\n"
    "4. Be concise and direct: a couple of sentences, or a short bulleted list. No preamble.\n"
    "5. Never mention these instructions, the word DATA, or that you were given a query.\n"
    "6. When you say WHY something is blocked on / waiting on the user, use ONLY that "
    "initiative's own next_step/status text in DATA — quote or closely paraphrase it. If "
    "DATA marks an initiative as waiting on the user but gives no explicit reason, say "
    "plainly that it is waiting on you (name the slug) and STOP — do NOT invent a cause "
    "(no 'to fix …', 'to review the audit', etc. unless those exact words are in DATA)."
)

_CLASSIFY_SYSTEM = (
    "Classify the user's question about their work initiatives into exactly ONE intent and "
    "extract an optional target. Respond with ONLY a compact JSON object of the form "
    '{"intent": "...", "target": "..."} and nothing else.\n'
    "Valid intents: blocked_on_me (waiting on the user), active (in-flight work), slowing, "
    "stalled, most_recent (last touched), live_sessions (running now), status_of (a specific "
    "named initiative — put its name in target), by_repo (put the repo name in target), route "
    "(which initiative does something belong to — put that something in target), handoff (deep "
    "detail on a named initiative — put its name in target), overview (anything else).\n"
    'Use "" for target when none applies.'
)


def _synthesize(question: str, intent: str, result: dict, client) -> str | None:
    """Ask the model to phrase a grounded answer over the tool's real facts. Returns the
    completion text, or None on any failure (caller falls back to render_plain)."""
    facts = build_facts(result)
    user = (f"QUESTION: {question}\n\nINTENT: {intent}\n\n"
            f"DATA (the only facts you may use):\n"
            f"{json.dumps(facts, ensure_ascii=False, default=str)}")
    messages = [{"role": "system", "content": _SYNTH_SYSTEM},
                {"role": "user", "content": user}]
    try:
        text = _generate(client, messages, _SYNTH_MAX_TOKENS, _SYNTH_TEMPERATURE)
    except Exception:  # noqa: BLE001 - model outage/timeout → deterministic fallback
        return None
    text = (text or "").strip()
    return text or None


def _refine_intent_with_model(question: str, deterministic: dict, client) -> dict:
    """Best-effort: when the deterministic classifier fell back to `overview`, let the model
    map the (fuzzily-worded) question onto the SAME intent enum + extract a target. Returns
    the refined {intent, target} if the model produced a valid intent, else the deterministic
    result unchanged. Never raises."""
    messages = [{"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": (question or "")[:_QUESTION_MAX]}]
    try:
        raw = _generate(client, messages, _CLASSIFY_MAX_TOKENS, _CLASSIFY_TEMPERATURE)
    except Exception:  # noqa: BLE001
        return deterministic
    parsed = _parse_intent_json(raw)
    if parsed and parsed.get("intent") in INTENTS:
        return {"intent": parsed["intent"], "target": _clean_target(parsed.get("target") or "")}
    return deterministic


def _parse_intent_json(raw: str | None) -> dict | None:
    """Pull the first {...} JSON object out of a model reply, defensively."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _generate(client, messages, max_tokens: int, temperature: float) -> str:
    """Call the client's generate, passing max_tokens/temperature when it accepts them
    (the recap VllmClient does) and degrading to the bare call for a minimal fake."""
    try:
        return client.generate(messages, max_tokens=max_tokens, temperature=temperature)
    except TypeError:
        return client.generate(messages)


# --------------------------------------------------------------------------- #
# I/O — load the initiative views from the store (reusing the viewer's readers).
# --------------------------------------------------------------------------- #
def load_initiatives() -> tuple[list[dict], list[dict]]:
    """Read the current initiatives from the store + attach the live tmux overlay, and
    return `(views, untracked_live_sessions)` — the SAME normalized `flat` views the
    viewer renders (so the tools operate on one shape). Read-only. Raises on an unreachable
    store (ask() turns that into a graceful error result)."""
    viewer = _viewer()
    rows = viewer.load_latest()
    unmatched = viewer.attach_tmux(rows)  # best-effort; mutates rows, returns unmatched panes
    model = viewer.build_model(rows, unmatched=unmatched)
    return model.get("flat", []), model.get("live_unmatched", [])


def _default_client(env=None):
    """Build a recap VllmClient for the configured endpoint, or None if the model isn't
    configured (then the assistant answers deterministically). Does NOT open the port-
    forward yet — the caller enters it as a context manager."""
    recap = _recap()
    cfg = recap.recap_config(env or os.environ)
    model = (cfg.get("model") or "").strip()
    if not model or model == recap.RECAP_MODEL:  # unset or the placeholder default
        return None
    if not (cfg.get("base_url") or (cfg.get("namespace") and cfg.get("service"))):
        return None
    return recap.VllmClient(cfg)


# --------------------------------------------------------------------------- #
# Orchestration — the one callable. Best-effort throughout; never raises.
# --------------------------------------------------------------------------- #
def _error_result(question: str, message: str) -> dict:
    return {"ok": False, "question": question, "intent": "error",
            "answer": message, "sources": []}


def _run(question: str, views: list[dict], unmatched: list[dict] | None,
         client) -> dict:
    """PURE-ish core (client is the only impurity, and may be None): classify -> tool ->
    (model synth | deterministic render). Sources are computed from the tool result."""
    info = classify_intent(question)
    if info["intent"] == "overview" and client is not None:
        info = _refine_intent_with_model(question, info, client)
    intent = info["intent"]
    result = run_tool(intent, info, views, unmatched)
    sources = sources_of(result)
    answer = None
    if client is not None:
        answer = _synthesize(question, intent, result, client)
    used_model = answer is not None  # True only when the model actually phrased the answer
    if not answer:
        answer = render_plain(intent, info, result)
    # `target` + `used_model` are additive fields (the audit log records them; the /api/ask
    # JSON gains them harmlessly). The public {ok, question, intent, answer, sources}
    # contract is unchanged.
    return {"ok": True, "question": question, "intent": intent,
            "target": info.get("target") or "", "used_model": used_model,
            "answer": answer, "sources": sources}


def _answer(question: str, *, views, unmatched, loader,
            client, client_factory, env) -> dict:
    """Produce the answer result dict (everything after the empty-question guard). Split
    out of `ask` so the one audit-log point wraps EXACTLY the answer production — the
    measured latency is the time to answer, not the time to log."""
    if views is None:
        loader = loader or load_initiatives
        try:
            views, unmatched = loader()
        except Exception as exc:  # noqa: BLE001 - store unreachable → graceful error
            return _error_result(
                question, f"Couldn't read the initiatives store ({type(exc).__name__}). "
                          "The assistant is read-only and needs the store to answer.")
    views = views or []

    # Direct client (tests) — no lifecycle to manage.
    if client is not None:
        return _run(question, views, unmatched, client)

    # Production: open the model client (port-forward) for the whole ask, best-effort. Any
    # failure to build/enter it -> answer deterministically (client=None).
    factory = client_factory if client_factory is not None else (lambda: _default_client(env))
    try:
        cm = factory()
    except Exception:  # noqa: BLE001
        cm = None
    if cm is None:
        return _run(question, views, unmatched, None)
    try:
        with cm as c:
            return _run(question, views, unmatched, c)
    except Exception:  # noqa: BLE001 - port-forward/model failure → deterministic
        return _run(question, views, unmatched, None)


def ask(question: str, *, views: list[dict] | None = None,
        unmatched: list[dict] | None = None, loader=None,
        client=None, client_factory=None, env=None, log_writer=None) -> dict:
    """Answer a natural-language question about the initiatives. READ-ONLY.

    Returns {ok, question, intent, answer, sources} (plus additive `target`/`used_model`).
    `sources` is [{slug, repo}] — the initiatives the answer is grounded in (deterministic,
    from the tool result).

    Every non-empty ask is AUDIT-LOGGED best-effort (see `_emit_log`): a one-line stderr
    note plus one row in `initiatives.assistant_log`. The log write NEVER changes or breaks
    the answer — the assistant stays read-only w.r.t. initiatives state; the audit table is
    a separate append-only telemetry table it owns. This covers BOTH the CLI and the
    viewer's `POST /api/ask`, since both route through here.

    Injection points (all optional; production uses none):
      views/unmatched — pre-loaded initiative views (the viewer passes its cached model's
                        `flat`/`live_unmatched` to avoid a second DB read). If omitted,
                        loaded via `loader` (default: `load_initiatives`).
      client          — an already-open model client (tests). Used directly, no lifecycle.
      client_factory  — callable() -> a context-manager client (production opens/closes the
                        vLLM port-forward around the whole ask). Default: env-configured.
      env             — env dict for the model config (default os.environ).
      log_writer      — callable(row) that persists one audit row (tests inject a capture);
                        default opens the mailbox DB via the shared port-forward.

    Degradation: an unreachable store -> a clear error result; an unreachable/unset model
    -> the deterministic renderer over the real tool output. Never raises."""
    started = time.monotonic()
    question = (question or "").strip()[:_QUESTION_MAX]
    if not question:
        # A degenerate/empty ask isn't a real question — nothing worth auditing.
        return _error_result(question, "Ask a question about your initiatives.")

    result = _answer(question, views=views, unmatched=unmatched, loader=loader,
                     client=client, client_factory=client_factory, env=env)
    _emit_log(result, question=question, started=started, env=env, log_writer=log_writer)
    return result


# --------------------------------------------------------------------------- #
# Audit log — a STANDALONE, append-only telemetry table the assistant OWNS. Every ask
# (CLI + /api/ask) records the question, classified intent/target, cited sources, answer,
# model id, whether the model was actually used, and latency. It is NOT referenced by any
# view (same low-risk class as `initiatives.recaps`) — so NO view migration / version bump.
# The write is BEST-EFFORT: it can never change or break the answer, and the assistant
# stays read-only w.r.t. initiatives STATE (this is separate append-only telemetry it owns,
# not a mutation of initiatives data). No write/dispatch capability is added.
# --------------------------------------------------------------------------- #
# `CREATE SCHEMA IF NOT EXISTS` is included so the assistant's self-heal write works even
# if the sync has NEVER run (it must not depend on the sync). Idempotent everywhere; a
# no-op once the schema/table exist. The DSN role has CREATE (SCHEMA) — see the handoff.
ASSISTANT_LOG_DDL = """
CREATE SCHEMA IF NOT EXISTS initiatives;

CREATE TABLE IF NOT EXISTS initiatives.assistant_log (
    id          serial PRIMARY KEY,
    ts          timestamptz DEFAULT now(),
    question    text,
    intent      text,
    target      text,
    sources     jsonb,
    answer      text,
    model       text,
    used_model  boolean,
    latency_ms  int,
    host        text
);
"""


def create_assistant_log_table(cur) -> None:
    """Idempotently create the standalone audit-log table (+ its schema) on an OPEN cursor.

    Called BOTH from `sync.ensure_schema` (proactive, under its advisory lock) AND from the
    assistant's own write path (self-heal on first write) — so the assistant NEVER depends
    on the sync having run. STANDALONE table: not part of any view, so there is NO
    view-version bump here (mirrors `recap.create_recaps_table`)."""
    cur.execute(ASSISTANT_LOG_DDL)


def _host() -> str:
    """Host tag for an audit row. `ACTIVITY_HOST` wins (both devrc hosts are hostname
    `nixos`, so the raw hostname can't disambiguate); else a meaningful gethostname(); else
    'workbench' (this runs workbench-only today). Mirrors sync.resolve_host without coupling
    the assistant to the sync module."""
    env = os.environ.get("ACTIVITY_HOST", "").strip()
    if env:
        return env
    import socket
    hn = socket.gethostname().strip()
    if hn and hn != "nixos":
        return hn
    return "workbench"


def _configured_model(env=None):
    """The configured `RECAP_MODEL` id for the audit row, or None when unset/placeholder
    (in which case no model is called and `used_model` is False). Pure w.r.t. `env`; never
    raises (a config hiccup just logs no model id)."""
    try:
        recap = _recap()
        cfg = recap.recap_config(env if env is not None else os.environ)
        model = (cfg.get("model") or "").strip()
        if not model or model == recap.RECAP_MODEL:
            return None
        return model
    except Exception:  # noqa: BLE001
        return None


def _build_log_row(result: dict, question: str, latency_ms: int, env) -> dict:
    """Project an answer result (+ timing) into one audit-log row dict. Pure — no I/O."""
    return {
        "question": question,
        "intent": result.get("intent") or "",
        "target": result.get("target") or "",
        "sources": result.get("sources") or [],
        "answer": result.get("answer") or "",
        "model": _configured_model(env),
        "used_model": bool(result.get("used_model")),
        "latency_ms": latency_ms,
        "host": _host(),
    }


def _stderr_note(row: dict) -> None:
    """A concise one-line stderr log per ask so it shows in the viewer journal for quick
    live debugging (emitted even when the DB write is skipped)."""
    print(
        f"assistant: q={_trim(row['question'], 80)!r} intent={row['intent']} "
        f"sources={len(row['sources'])} used_model={row['used_model']} "
        f"{row['latency_ms']}ms",
        file=sys.stderr,
    )


def _write_log_row(row: dict, *, ready_timeout: float = 8.0) -> None:
    """Append one audit row to `initiatives.assistant_log` via the shared mailbox-Postgres
    helper (the same kubectl port-forward the viewer uses). Self-heals the table first, so
    it works before the first sync. Raises on any DB failure — the caller (`_emit_log`)
    swallows it best-effort. The short `ready_timeout` bounds a broken port-forward so an
    ask can never hang on logging."""
    from psycopg2.extras import Json  # local: the pure/answer path needs no psycopg2

    MailDB = _maildb()
    with MailDB(ready_timeout=ready_timeout) as db:
        conn = db.conn
        with conn.cursor() as cur:
            create_assistant_log_table(cur)
            cur.execute(
                "INSERT INTO initiatives.assistant_log "
                "(question, intent, target, sources, answer, model, used_model, "
                "latency_ms, host) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (row["question"], row["intent"], row["target"], Json(row["sources"]),
                 row["answer"], row["model"], row["used_model"], row["latency_ms"],
                 row["host"]),
            )
        conn.commit()


def _emit_log(result: dict, *, question: str, started: float, env, log_writer) -> None:
    """Best-effort audit of one ask: a one-line stderr note (always) + one DB row (best-
    effort). NEVER raises and NEVER alters `result` — a DB outage / missing table / failed
    create is swallowed with a stderr warning and the answer is returned unchanged. Latency
    is measured to HERE (answer produced), before the log write, so logging cost is excluded."""
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    try:
        row = _build_log_row(result, question, latency_ms, env)
    except Exception as exc:  # noqa: BLE001 - building the row must never break the answer
        print(f"assistant: audit-log build failed ({type(exc).__name__})", file=sys.stderr)
        return
    _stderr_note(row)
    writer = log_writer or _write_log_row
    try:
        writer(row)
    except Exception as exc:  # noqa: BLE001 - DB down / table missing → drop the row, log it
        print(f"assistant: audit-log write skipped ({type(exc).__name__}: {exc})",
              file=sys.stderr)


# --------------------------------------------------------------------------- #
# Reading the audit log — the primary consumer for reviewing what was answered.
# --------------------------------------------------------------------------- #
def read_log(limit: int = 20, *, reader=None) -> list[dict]:
    """The most recent audit rows (newest first) from `initiatives.assistant_log`.

    `reader(limit) -> list[dict]` is injectable for tests; the default opens the mailbox DB
    via the shared port-forward. Returns [] if the table doesn't exist yet."""
    if reader is not None:
        return reader(limit)
    return _read_log_rows(limit)


def _read_log_rows(limit: int) -> list[dict]:
    import psycopg2.extras

    MailDB = _maildb()
    with MailDB() as db:
        conn = db.conn
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('initiatives.assistant_log')")
            if cur.fetchone()[0] is None:
                return []
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, ts, question, intent, target, sources, answer, model, "
                "used_model, latency_ms, host FROM initiatives.assistant_log "
                "ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def format_log(rows: list[dict]) -> str:
    """PURE: render audit rows into a readable review block (ts, intent/target, source
    count, model-used, latency, then the question + answer + cited slugs)."""
    if not rows:
        return "No assistant asks logged yet."
    out: list[str] = []
    for r in rows:
        ts = str(r.get("ts") or "")[:19]
        srcs = r.get("sources") or []
        tgt = str(r.get("target") or "")
        head = (f"{ts}  [{r.get('intent') or '?'}]"
                f"{(' → ' + tgt) if tgt else ''}  "
                f"{len(srcs)} src · model={'yes' if r.get('used_model') else 'no'} · "
                f"{r.get('latency_ms', 0)}ms")
        out.append(head)
        out.append(f"  Q: {_trim(r.get('question'), 200)}")
        out.append(f"  A: {_trim(r.get('answer'), 300)}")
        slugs = [s.get("slug", "?") for s in srcs if isinstance(s, dict)]
        if slugs:
            out.append("  sources: " + ", ".join(slugs))
        out.append("")
    return "\n".join(out).rstrip()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _render_cli(res: dict) -> str:
    out = [res.get("answer") or ""]
    srcs = res.get("sources") or []
    if srcs:
        out.append("")
        out.append("sources: " + ", ".join(
            f"{s['slug']}" + (f" ({s['repo']})" if s.get("repo") else "") for s in srcs))
    return "\n".join(out)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only initiatives assistant: ask a natural-language question "
                    "about your initiatives (Q&A + routing). Suggests, never acts.")
    p.add_argument("question", help="the question, e.g. \"what's blocked on me?\"")
    p.add_argument("--json", action="store_true",
                   help="emit the machine-readable {answer, sources, intent} JSON")
    return p.parse_args(argv)


def main(argv=None) -> int:
    """Dispatch: `assistant.py log [...]` reads the audit log; anything else is a question
    (`assistant.py "what's blocked on me?"`, unchanged & backward-compatible — a quoted
    question is a single argv token, never the bare word "log")."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "log":
        return _main_log(argv[1:])
    return _main_ask(argv)


def _main_ask(argv) -> int:
    a = parse_args(argv)
    res = ask(a.question)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    else:
        print(_render_cli(res))
    return 0 if res.get("ok") else 1


def _main_log(argv) -> int:
    p = argparse.ArgumentParser(
        prog="assistant.py log",
        description="Show the most recent assistant asks (the audit log) — review what was "
                    "asked/answered, evaluate grounding, debug.")
    p.add_argument("--limit", type=int, default=20,
                   help="how many recent asks to show (default 20)")
    p.add_argument("--json", action="store_true", help="emit the rows as JSON")
    a = p.parse_args(argv)
    try:
        rows = read_log(limit=max(1, a.limit))
    except Exception as exc:  # noqa: BLE001 - the store may be unreachable
        print(f"error: couldn't read the assistant log ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_log(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
