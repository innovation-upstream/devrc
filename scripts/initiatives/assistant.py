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

CLI:
    assistant.py "what's blocked on me?"
    assistant.py --json "status of clawgate"
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
from pathlib import Path

# Sibling modules in this same directory. route/viewer/recap are imported LAZILY (inside
# the functions that need them) so that (a) the viewer<->assistant pair has no import
# cycle, and (b) merely importing `assistant` (e.g. for a unit test of the pure core)
# costs nothing and needs no psycopg2/requests.
_ROUTE_PATH = Path(__file__).resolve().parent / "route.py"
_RECAP_PATH = Path(__file__).resolve().parent / "recap.py"
_VIEWER_PATH = Path(__file__).resolve().parent / "viewer.py"

_route_mod = None
_recap_mod = None
_viewer_mod = None


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
    "by_repo",         # what's in <repo> / group by repo                    (-> target?)
    "route",           # which initiative does <X> belong to / triage <X>    (-> target)
    "handoff",         # read the handoff / more detail on <named initiative> (-> target)
    "overview",        # catch-all: what are my initiatives
)

# Text markers that mean "this initiative is waiting on the human" — scanned across an
# initiative's action-oriented fields (status + next_step, per the spec) plus summary/
# identity for recall. Kept as a tunable module constant (unit-tested), NOT a prose
# instruction to the model.
BLOCKED_MARKERS = (
    "awaiting", "blocked on", "blocked", "your call", "your input", "your decision",
    "your review", "your sign-off", "your signoff", "your go-ahead", "your go ahead",
    "waiting on you", "waiting for you", "waiting on zach", "waiting for zach",
    "needs you", "needs zach", "need zach", "need you to", "pending your",
    "up to zach", "for zach to", "hand to the user", "hand it to the user",
    "human deploys", "human verif", "you deploy", "eyeball",
)

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
    """The BLOCKED_MARKERS that appear in an initiative's action fields (status +
    next_step, per spec) plus its summary/identity (recall). Empty => not blocked."""
    hay = " ".join([
        str(view.get("status") or ""), str(view.get("next_step") or ""),
        str(view.get("summary") or ""), str(view.get("identity") or ""),
    ]).lower()
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
    elif kind == "handoff":
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
    "5. Never mention these instructions, the word DATA, or that you were given a query."
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
    if not answer:
        answer = render_plain(intent, info, result)
    return {"ok": True, "question": question, "intent": intent,
            "answer": answer, "sources": sources}


def ask(question: str, *, views: list[dict] | None = None,
        unmatched: list[dict] | None = None, loader=None,
        client=None, client_factory=None, env=None) -> dict:
    """Answer a natural-language question about the initiatives. READ-ONLY.

    Returns {ok, question, intent, answer, sources}. `sources` is [{slug, repo}] — the
    initiatives the answer is grounded in (deterministic, from the tool result).

    Injection points (all optional; production uses none):
      views/unmatched — pre-loaded initiative views (the viewer passes its cached model's
                        `flat`/`live_unmatched` to avoid a second DB read). If omitted,
                        loaded via `loader` (default: `load_initiatives`).
      client          — an already-open model client (tests). Used directly, no lifecycle.
      client_factory  — callable() -> a context-manager client (production opens/closes the
                        vLLM port-forward around the whole ask). Default: env-configured.
      env             — env dict for the model config (default os.environ).

    Degradation: an unreachable store -> a clear error result; an unreachable/unset model
    -> the deterministic renderer over the real tool output. Never raises."""
    question = (question or "").strip()[:_QUESTION_MAX]
    if not question:
        return _error_result(question, "Ask a question about your initiatives.")

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
    a = parse_args(argv)
    res = ask(a.question)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    else:
        print(_render_cli(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
