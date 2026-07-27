#!/usr/bin/env python3
"""Live web viewer over the Phase-1 initiatives store.

PHASE 3 of the "initiatives consolidation" feature. A self-contained web page (stdlib
`http.server`, NO web framework, inline vanilla JS/CSS, no external/CDN assets) that
renders the CURRENT initiatives from the homelab `mailbox` Postgres, with momentum
badges, a per-initiative goal/summary line, next-step, open PRs (titles, not bare
numbers), and a LIVE tmux overlay (which tmux session is on each initiative right now).
It is the durable, browser-viewable counterpart to the ephemeral agent-ops TUI.

Interaction (all client-side over the embedded JSON / `/api/initiatives.json`):
  - a **flat / grouped** toggle (FLAT is the default, most-recently-active first, with a
    repo label on each card; grouped-by-repo is one click away). Persisted in localStorage.
  - a **search box** filtering cards by substring across slug/title/summary/repo/momentum.
  - **click-to-expand** each card → a detail view fetched from `/api/initiative` that
    LIVE-reads the handoff doc off disk (full Next-steps list + Open investigations + all
    open-PR titles + the doc path + docs history), falling back to the stored fields.
  - a header **↻ refresh** button → `POST /refresh` runs a fresh sync (single-flighted +
    debounced ~60s), then the page re-fetches and re-renders.

Data (two layers, both best-effort per request):
  1. The STORE — `initiatives.latest` (rows from the most recent snapshot only, so NO
     aged-out "ghosts"). Read via `mail-actions/_db.py`'s kubectl port-forward. Falls back
     to an inline `WHERE snapshot_id=(SELECT max(id) …)` query if the `latest` view doesn't
     exist yet (i.e. before the next sync recreates the schema).
  2. The LIVE tmux overlay — attached at RENDER TIME from THIS host's tmux server, reusing
     the scan's machinery. Deliberately NOT stored in Postgres. Absent if there's no tmux
     server (best-effort).

Layering mirrors sync.py / route.py: the pure render transform (`build_model` /
`model_to_json` / `render_html`) and the pure detail/summary parse are separated from all
I/O (the DB read, the tmux read, the refresh subprocess, the HTTP server), so they are
unit-testable with fixtures — no live DB, no live tmux, no sockets, no subprocess.

Serving:
  Routes: `/` (HTML), `/healthz` (200/ok — process liveness, NOT the DB),
  `/api/initiatives.json` (the JSON the page is built from), `/api/initiative?repo=&slug=`
  (one initiative's live detail), and `POST /refresh` (trigger a sync now). Binds
  LAN/localhost only by default; NOT wired into the public homelab gateway — internal work
  data. A short in-process cache (a few seconds) avoids hammering the port-forward on rapid
  refreshes. A DB outage renders a clear error page and keeps serving (never crash-loops).

Requires (for the live read; the pure render path needs none of these):
    KUBECONFIG  — homelab kubeconfig (the DB is only reachable via kubectl port-forward)
    kubectl     — on PATH
    psycopg2    — python dep
On NixOS run under:
    nix-shell -p "python3.withPackages(p:[p.psycopg2 p.requests])" \
      --run "python scripts/initiatives/viewer.py --host 127.0.0.1 --port 8899"
"""
from __future__ import annotations

import argparse
import contextlib
import html
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# The scan we borrow the tmux machinery + the deterministic doc parsers from (hyphenated
# filename → importlib, not import).
SCAN_PATH = Path(__file__).resolve().parents[1] / "session-analysis" / "initiative-scan.py"
# chquery lives here; the scan adds it to sys.path on import, mirror that so the scan's
# top-level `import chquery` resolves regardless of cwd.
VALIDATION_DIR = Path(__file__).resolve().parents[1] / "validation"

# The shared mailbox-Postgres helper (kubectl port-forward + psycopg2 + DSN-from-secret).
MAILDB_PATH = Path(__file__).resolve().parents[1] / "mail-actions" / "_db.py"

# The pure, stdlib-only grounded next-step recommender (attached to each view in build_model).
NEXTSTEP_PATH = Path(__file__).resolve().parent / "nextstep.py"

# The clawgate dispatcher (POST /api/dispatch → create a Task). Viewer-side ONLY: the clawgate
# token lives here (LAN-bound, Zach's user), NOT in the in-cluster devpod. Loaded lazily.
DISPATCH_PATH = Path(__file__).resolve().parent / "dispatch.py"

# The sync wrapper the ↻ refresh button shells out to (it already does the nix-shell +
# sops cred decrypt + scan + store write). Running it as a subprocess sidesteps the
# `systemctl --user`-from-a-service dbus/XDG_RUNTIME_DIR complexity.
RUN_SYNC_PATH = Path(__file__).resolve().parent / "run-sync.sh"

# The rich display columns the viewer reads (present on both `initiatives.latest` and the
# base `initiative_snapshot` table, so the inline fallback selects the SAME set + captured_at).
DISPLAY_COLUMNS = [
    "slug", "repo", "title", "summary", "momentum", "last_touch", "next_step", "commits",
    "commits_unknown", "merged_prs", "open_prs", "session_count", "telem_events",
    "current_doc", "open_investigations", "docs", "recent_messages", "recent_commits",
]

# Session-derived discovery columns (added by the scan+sync `v4`/`v5` migrations):
# `undocumented` is TRUE for session-only cards (no handoff doc); `source` is
# `doc|session|both`; `opening_message` is the thread's origin/genesis prompt (v5). They are
# OPTIONAL — an un-migrated store (pre-migration `latest` view, or a base table without the
# ALTERs) simply lacks them, so the viewer detects their presence and SELECTs them only when
# they exist, defaulting `undocumented=False`/`opening_message=""` (→ the card renders in the
# main board with no `start ›` line, nothing lost).
OPTIONAL_COLUMNS = ["undocumented", "source", "opening_message", "search_text"]

# Momentum ordering + badges — SAME ranks/glyphs the scan uses (active→stalled→unknown).
MOMENTUM_RANK = {"active": 0, "slowing": 1, "stalled": 2, "unknown": 3}
MOMENTUM_BADGE = {
    "active": ("●", "active"),    # ●
    "slowing": ("◐", "slowing"),  # ◐
    "stalled": ("○", "stalled"),  # ○
    "unknown": ("·", "unknown"),  # ·
}

# The page's client-side auto-refresh cadence (seconds) — re-fetches the JSON and re-renders
# in place (keeps the live tmux overlay + freshness current WITHOUT resetting the search box,
# the flat/grouped toggle, or any expanded cards). The store itself is synced ~15min by the
# timer; the ↻ button forces one on demand.
REFRESH_SECONDS = 30
DEFAULT_HOST = "192.168.50.250"  # workbench-LAN bind (eth1); use --host 127.0.0.1 for local
DEFAULT_PORT = 8899
CACHE_TTL_SECONDS = 5.0

# Refresh (↻) debounce + single-flight: ignore a refresh if a sync ran within this many
# seconds ("just synced Xs ago" instead of re-running); a hard ceiling so a hung scan can't
# wedge the request forever (matches the sync unit's TimeoutStartSec).
REFRESH_MIN_INTERVAL = 60.0
REFRESH_TIMEOUT = 300

# Upper bound on a live handoff read (GET /api/initiative). Handoffs are KBs; this caps
# a pathological/huge file so the detail read can't spike the viewer's memory.
MAX_DOC_BYTES = 512 * 1024


# --------------------------------------------------------------------------- #
# Lazy imports of the two borrowed modules (single-sourced; not reimplemented).
# --------------------------------------------------------------------------- #
_scan_mod = None


def _scan():
    """Load initiative-scan.py by explicit path and cache it (for the tmux machinery +
    the deterministic handoff parsers `parse_summary`/`parse_all_next_steps`/
    `parse_open_investigations`/`parse_handoff_title`).

    Lazy + side-effect-light: the scan's top-level `import chquery` only runs the first
    time this is called. `chquery` needs `requests` + the `scripts/validation` dir on
    sys.path; we add the latter idempotently, mirroring route.py."""
    global _scan_mod
    if _scan_mod is None:
        vdir = str(VALIDATION_DIR)
        if vdir not in sys.path:
            sys.path.insert(0, vdir)
        spec = importlib.util.spec_from_file_location("initiative_scan_for_viewer", SCAN_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {SCAN_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _scan_mod = mod
    return _scan_mod


def _import_maildb():
    """Load MailDB from scripts/mail-actions/_db.py by EXPLICIT importlib path.

    Do NOT put mail-actions/ on sys.path — its `llm.py` shadows other modules and breaks
    callers (documented in the repo CLAUDE.md; sync.py/route.py/repo-cos hit the same
    trap). `_db.py` imports only stdlib+psycopg2, so a standalone load is safe."""
    spec = importlib.util.spec_from_file_location("initiatives_viewer_maildb", MAILDB_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {MAILDB_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MailDB


# The pure next-step recommender, loaded by EXPLICIT importlib path (the package's sibling
# cross-load convention — NOT sys.path). Lazy + cached so a `build_model` over N views loads
# it once. nextstep.py is stdlib-only + PURE, so a standalone load is free of side effects.
_nextstep_mod = None


def _nextstep():
    global _nextstep_mod
    if _nextstep_mod is None:
        spec = importlib.util.spec_from_file_location("initiatives_viewer_nextstep", NEXTSTEP_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {NEXTSTEP_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _nextstep_mod = mod
    return _nextstep_mod


_dispatch_mod = None


def _dispatch():
    """Load the clawgate dispatcher sibling by EXPLICIT importlib path (same convention).
    Lazy so a viewer that never dispatches pays nothing and doesn't need dispatch.py present
    at import; `dispatch.dispatch_initiative` is the POST /api/dispatch entry point."""
    global _dispatch_mod
    if _dispatch_mod is None:
        spec = importlib.util.spec_from_file_location("initiatives_viewer_dispatch", DISPATCH_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {DISPATCH_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _dispatch_mod = mod
    return _dispatch_mod


# The sibling read-only Q&A assistant (POST /api/ask). Loaded by EXPLICIT importlib path
# (not a top-level `import assistant`) so the viewer<->assistant pair has no import cycle
# and merely importing the viewer stays cheap. assistant itself lazily loads the viewer
# only for its handoff reader, so the answer path here (which passes pre-loaded views) does
# not re-enter this module.
ASSISTANT_PATH = Path(__file__).resolve().parent / "assistant.py"
_assistant_mod = None

# Cap the POST body the server will read for /api/ask (a question is a short string; this
# guards against a client streaming an unbounded body).
MAX_ASK_BODY_BYTES = 64 * 1024


def _assistant():
    global _assistant_mod
    if _assistant_mod is None:
        spec = importlib.util.spec_from_file_location(
            "initiatives_viewer_assistant", ASSISTANT_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {ASSISTANT_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _assistant_mod = mod
    return _assistant_mod


def default_ask(question: str, provider) -> dict:
    """Answer a question with the READ-ONLY assistant, reusing the provider's CACHED
    snapshot (its `flat` views + `live_unmatched`) so no second DB port-forward is opened,
    plus the env-configured local model (`vllm-recap`). Best-effort: a store-read error
    surfaces as the assistant's graceful error result; a model outage degrades to the
    deterministic answer. There is NO write/dispatch path here — the assistant only reads."""
    model, error = provider.snapshot()
    if model is None:
        return {"ok": False, "intent": "error", "sources": [],
                "answer": f"Couldn't read the initiatives store ({error or 'no data'})."}
    return _assistant().ask(
        question, views=model.get("flat", []),
        unmatched=model.get("live_unmatched", []))


# The initiatives AGENT client (POST /api/ask → the OpenClaw devpod gateway). Loaded by
# explicit importlib path (same convention as the assistant), lazily so a viewer without the
# agent enabled pays nothing. The agent is an UPGRADE over `default_ask`: when
# INITIATIVES_AGENT_ENABLED is set it drives the answer via the model-selects-tools devpod;
# on ANY failure `agent_ask` returns None and we fall back to the deterministic assistant.
AGENT_CLIENT_PATH = Path(__file__).resolve().parent / "agent_client.py"
_agent_client_mod = None


def _agent_client():
    global _agent_client_mod
    if _agent_client_mod is None:
        spec = importlib.util.spec_from_file_location(
            "initiatives_viewer_agent_client", AGENT_CLIENT_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {AGENT_CLIENT_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _agent_client_mod = mod
    return _agent_client_mod


def build_asker(provider, env=None):
    """Build the `POST /api/ask` callable. When the agent is disabled (env), returns None so
    `make_handler` uses `default_ask` (the deterministic regex assistant). When enabled,
    returns an asker that tries the AGENT first (reusing the provider's cached views for the
    grounded `sources`) and FALLS BACK to `default_ask` if the agent is unreachable — the
    viewer always answers, degrading gracefully devpod-down."""
    env = os.environ if env is None else env
    agent = _agent_client()
    if not agent.agent_config(env)["enabled"]:
        return None

    def asker(question: str) -> dict:
        model, _err = provider.snapshot()
        views = model.get("flat", []) if model else []
        result = None
        try:
            result = agent.agent_ask(question, views=views, env=env)
        except Exception as exc:  # noqa: BLE001 - the agent path must never break /api/ask
            sys.stderr.write(f"viewer: agent_ask raised ({type(exc).__name__}: {exc}); "
                             f"falling back to the deterministic assistant\n")
        return result if result is not None else default_ask(question, provider)

    return asker


def build_stream_asker(provider, env=None):
    """Build the `POST /api/ask/stream` callable — the STREAMING sibling of `build_asker`.
    Returns `stream_asker(question) -> Iterator[dict]`, a generator yielding SSE-shaped frames
    (`{"delta": ...}` chunks then a final `{"done": True, "sources": [...], "answer": ...}`).

    When the agent is enabled and answers, its token-by-token stream is threaded through. When
    the agent is disabled, unreachable, raises, OR returns an EMPTY answer, it falls back to the
    deterministic `default_ask` — emitted as a SINGLE `{delta}` + `{done}` so the browser path
    is uniform (it always streams) and the sidebar ALWAYS answers, devpod-down. Returned
    UNCONDITIONALLY (never None) — the fallback makes the endpoint always usable."""
    env = os.environ if env is None else env
    agent = _agent_client()

    def stream_asker(question: str):
        model, _err = provider.snapshot()
        views = model.get("flat", []) if model else []
        gen = None
        try:
            gen = agent.agent_stream(question, views=views, env=env)
        except Exception as exc:  # noqa: BLE001 - the agent path must never break the endpoint
            sys.stderr.write(f"viewer: agent_stream raised ({type(exc).__name__}: {exc}); "
                             f"falling back to the deterministic assistant\n")
            gen = None
        if gen is not None:
            produced = False
            for chunk in gen:
                if chunk.get("delta"):
                    produced = True
                    yield chunk
                elif chunk.get("done"):
                    if produced or chunk.get("answer"):
                        yield chunk
                        return
                    # EMPTY agent answer → fall through to the deterministic fallback below.
                    break
                else:
                    yield chunk
        # Fallback: agent disabled, gen is None, raised, or produced an empty answer.
        res = default_ask(question, provider)
        yield {"delta": res.get("answer", "")}
        yield {"done": True, "sources": res.get("sources", []),
               "answer": res.get("answer", ""), "intent": res.get("intent")}

    return stream_asker


# --------------------------------------------------------------------------- #
# I/O — read the store, then attach the live tmux overlay.
# --------------------------------------------------------------------------- #
def load_latest() -> list[dict]:
    """Read the current initiatives from `initiatives.latest` → list of row dicts.

    Prefers the `latest` view (newest snapshot only, no ghosts). If that view doesn't
    exist yet (before the next sync recreates the schema), transparently falls back to
    an inline `WHERE snapshot_id=(SELECT max(id) …)` query over the base table — the
    same rows the view would return. Raises on an unreachable store; the provider turns
    that into a graceful error page rather than crashing the server."""
    import psycopg2
    import psycopg2.extras

    MailDB = _import_maildb()
    with MailDB() as db:
        with db.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                # Select the optional discovery columns ONLY when the `latest` view actually
                # exposes them (post-`v4` migration), so a pre-migration view doesn't error.
                opt = _present_columns(cur, "latest", OPTIONAL_COLUMNS)
                cols = ", ".join(DISPLAY_COLUMNS + opt)
                cur.execute(f"SELECT {cols}, captured_at FROM initiatives.latest")
                rows = [dict(r) for r in cur.fetchall()]
            except psycopg2.Error:
                # View absent (or otherwise unqueryable): the transaction is now aborted,
                # so roll back before the fallback query on the same connection.
                db.conn.rollback()
                base_opt = _present_columns(cur, "initiative_snapshot", OPTIONAL_COLUMNS)
                icols = ", ".join(f"i.{c}" for c in DISPLAY_COLUMNS + base_opt)
                cur.execute(
                    f"SELECT {icols}, s.captured_at "
                    "FROM initiatives.initiative_snapshot i "
                    "JOIN initiatives.snapshots s ON s.id = i.snapshot_id "
                    "WHERE i.snapshot_id = (SELECT max(id) FROM initiatives.snapshots)"
                )
                rows = [dict(r) for r in cur.fetchall()]
            attach_recaps(db.conn, rows)
            return rows


def _present_columns(cur, relation: str, wanted: list[str]) -> list[str]:
    """Return the subset of `wanted` columns that ACTUALLY exist on `initiatives.<relation>`
    (a table or a view — `information_schema.columns` covers both), preserving `wanted`'s
    order. Used to add the optional discovery columns to the SELECT only when the store has
    been migrated to expose them; on an un-migrated store the set is empty and the query
    stays exactly as it was. `information_schema` always exists, so this never aborts the
    transaction the way SELECTing a missing column would."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'initiatives' AND table_name = %s "
        "AND column_name = ANY(%s)",
        (relation, list(wanted)),
    )
    have = {r[0] if not isinstance(r, dict) else r["column_name"] for r in cur.fetchall()}
    return [c for c in wanted if c in have]


def attach_recaps(conn, rows: list[dict]) -> bool:
    """Best-effort: LEFT-JOIN the standalone `initiatives.recaps` cache onto the loaded
    rows by (repo, slug), setting each row's `identity` / `status` (and the legacy `recap`,
    None when absent). Kept OUT of the `latest`/`current` views on purpose (the recap cache
    persists per-(repo,slug) across snapshots, not as a per-snapshot column) — so the viewer
    joins it here instead. `identity` is the primary "what this is" line (fallback chain
    identity → recap → summary); `status` is the secondary "current: …" line.

    Strictly additive + fail-soft: if the recaps table doesn't exist yet (Phase B not
    deployed) or the read errors, every row simply keeps identity/status/recap=None and the
    card falls back to `summary`. The identity/status columns may also be absent on a store
    written by the pre-split code (before the additive ALTERs ran); we transparently fall
    back to selecting just `recap` so an un-migrated store still renders its old recap. A
    `to_regclass` guard + a rollback on error keep the connection usable. Returns True if
    any recap fields were attached, False otherwise."""
    import psycopg2
    import psycopg2.extras

    for r in rows:
        r.setdefault("identity", None)
        r.setdefault("status", None)
        r.setdefault("recap", None)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('initiatives.recaps')")
            reg = cur.fetchone()
            if reg is None or reg[0] is None:
                return False
        by_key = _read_recap_rows(conn)
    except psycopg2.Error:
        with contextlib.suppress(Exception):
            conn.rollback()
        return False
    for r in rows:
        rec = by_key.get((r.get("repo"), r.get("slug")))
        if not rec:
            continue
        if rec.get("identity"):
            r["identity"] = rec["identity"]
        if rec.get("status"):
            r["status"] = rec["status"]
        if rec.get("recap"):
            r["recap"] = rec["recap"]
    return True


def _read_recap_rows(conn) -> dict:
    """Read the recaps cache -> {(repo, slug): {"identity","status","recap"}}. Prefers the
    identity/status columns (the split store); if they don't exist yet (pre-split store,
    before the additive ALTERs ran) the SELECT errors, so we roll back and fall back to the
    legacy `recap`-only shape (identity/status = None → viewer falls back to summary)."""
    import psycopg2
    import psycopg2.extras

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT repo, slug, identity, status, recap "
                        "FROM initiatives.recaps")
            return {(r["repo"], r["slug"]): {
                "identity": r.get("identity"), "status": r.get("status"),
                "recap": r.get("recap")} for r in cur.fetchall()}
    except psycopg2.Error:
        with contextlib.suppress(Exception):
            conn.rollback()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT repo, slug, recap FROM initiatives.recaps")
            return {(r["repo"], r["slug"]): {
                "identity": None, "status": None, "recap": r.get("recap")}
                for r in cur.fetchall()}


def attach_tmux(initiatives: list[dict]) -> list[dict]:
    """Attach live tmux sessions to each initiative (mutates `tmux_sessions`/`tmux_tasks`)
    and RETURN the list of live claude panes that matched NO initiative (each
    `{"id", "title", "repo"}`) — the "everything else running" catch-all the board must
    surface honestly. Returns `[]` if the overlay is absent (no tmux server / any failure).

    Reuses the scan's machinery verbatim: `collect_tmux_panes` reads THIS host's panes,
    `match_tmux_to_initiatives` links each pane's title to an initiative in its repo AND
    returns the unmatched claude panes (live work the ledger doesn't cover — a new thread
    or a handoff not yet written). The viewer must run ON the host whose tmux we want to
    see (that's where its systemd unit lives). Fully best-effort — no tmux server, no scan
    import, any error → overlay absent + empty unmatched, never fatal."""
    try:
        scan = _scan()
        panes = scan.collect_tmux_panes()
        if not panes:
            return []  # no tmux server on this host → overlay absent (not an error)
        repos = scan.discover_repos()
        wt_map = scan.worktree_canonical_map(repos)
        codenames = scan.load_scratch_codenames()
        return scan.match_tmux_to_initiatives(initiatives, panes, repos, wt_map, codenames)
    except Exception:  # noqa: BLE001 - the overlay is a nicety, never a hard dependency
        return []


# --------------------------------------------------------------------------- #
# Pure render transform (rows -> model -> JSON). No I/O — unit-tested with fixtures.
# --------------------------------------------------------------------------- #
def _as_utc(dt) -> datetime | None:
    """Coerce a value to a tz-aware UTC datetime (psycopg2 returns tz-aware; a naive
    datetime is assumed UTC). None / non-datetime -> None."""
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def rel_age(dt, now: datetime) -> str:
    """A compact 'time since' string: 'now', '5m', '3h', '2d', '4w'. None -> '—'.

    Clamps a slightly-future timestamp (clock skew between the DB and this host) to
    'now' rather than emitting a negative age."""
    d = _as_utc(dt)
    if d is None:
        return "—"  # —
    secs = (now - d).total_seconds()
    if secs < 60:
        return "now"
    mins = secs / 60
    if mins < 60:
        return f"{int(mins)}m"
    hours = mins / 60
    if hours < 24:
        return f"{int(hours)}h"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d"
    return f"{int(days / 7)}w"


def momentum_badge(momentum: str | None) -> tuple[str, str]:
    """(glyph, label) for a momentum value; falls back to the 'unknown' badge."""
    return MOMENTUM_BADGE.get(momentum or "unknown", MOMENTUM_BADGE["unknown"])


def _short_repo(repo: str | None) -> str:
    return os.path.basename(str(repo).rstrip("/")) if repo else "(unknown repo)"


def _norm_docs(docs) -> list[dict]:
    """The stored `docs` jsonb -> a stable list of {path, date} dicts (str-coerced)."""
    out: list[dict] = []
    for d in docs or []:
        if isinstance(d, dict):
            out.append({"path": str(d.get("path") or ""),
                        "date": (str(d["date"]) if d.get("date") else None)})
    return out


# Card-FACE prompt filtering (Phase A precision, Problem 2). The stored
# `recent_messages` list is kept COMPLETE (the card expand shows it verbatim, and Phase B
# will consume it) — only the SINGLE line shown on the card FACE is filtered to the
# most-recent SUBSTANTIVE prompt, skipping low-signal boilerplate like `dispatch` /
# `proceed` / `yes` that says nothing about what an initiative IS.
FACE_MIN_CHARS = 15
# Exact-match (post-normalization) trivial prompts: agent-pipeline ritual words / bare
# acks that carry no topic. An explicit set so tuning FACE_MIN_CHARS can never let one of
# these through.
TRIVIAL_PROMPTS = frozenset({
    "dispatch", "proceed", "submitted", "yes", "y", "go", "ok", "okay",
    "continue", "merged", "done", "next", "sure", "approved", "lgtm",
})


def _is_trivial_prompt(text: str) -> bool:
    """A low-signal card-FACE prompt: an exact known boilerplate ack (`dispatch`/`proceed`/
    `yes`…) or too short to describe the work (< FACE_MIN_CHARS). Punctuation-insensitive +
    case-folded, so `Proceed.` and `dispatch!` both count. PURE — used ONLY for FACE
    selection; the stored `recent_messages` list is never filtered by it."""
    norm = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    if not norm:
        return True
    if norm in TRIVIAL_PROMPTS:
        return True
    return len((text or "").strip()) < FACE_MIN_CHARS


def pick_face_message(recent_messages: list[dict]) -> dict | None:
    """The single message to show on a card's FACE: the most-recent SUBSTANTIVE prompt.

    `recent_messages` is newest-first (as stored). Returns the first non-trivial one
    (`_is_trivial_prompt`); if EVERY message is trivial, falls back to the most-recent one
    (never blank when there's any message). None only for an empty list. The full list is
    left intact for the expand — this only picks the face line."""
    msgs = [m for m in (recent_messages or []) if isinstance(m, dict)]
    if not msgs:
        return None
    for m in msgs:
        if not _is_trivial_prompt(str(m.get("text") or "")):
            return m
    return msgs[0]


def _initiative_view(ini: dict, now: datetime) -> dict:
    """One store row (+ any attached tmux_sessions) -> a flat, template-ready view dict."""
    momentum = ini.get("momentum") or "unknown"
    glyph, label = momentum_badge(momentum)
    open_prs = ini.get("open_prs") or []
    tmux = sorted(ini.get("tmux_sessions") or [])
    # The matched live pane's task summary (render-time tmux overlay, viewer-side only —
    # not stored). First title if a session is open, else "".
    tmux_tasks = [str(t) for t in (ini.get("tmux_tasks") or []) if str(t).strip()]
    repo = ini.get("repo")
    # The COMPLETE recent-prompt list (newest-first, as stored) — the expand renders it
    # verbatim. The card FACE shows only `face_message` (the most-recent substantive one).
    recent_messages = [
        {"text": str(m.get("text") or ""), "ts": m.get("ts")}
        for m in (ini.get("recent_messages") or []) if isinstance(m, dict)
    ]
    view = {
        "slug": ini.get("slug") or "(no slug)",
        "repo": repo or "",
        "repo_name": _short_repo(repo),
        "title": ini.get("title") or "",
        "summary": (ini.get("summary") or "").strip(),
        # The LLM recap split (Phase B). `identity` = the STABLE "what this is" line
        # (sourced from the handoff description, cached on the handoff hash — the primary
        # card line); `status` = the VOLATILE "current: …" line (sourced from recent
        # activity). Fallback chain for the primary line is identity → recap → summary, so
        # a card is never blank during rollout. `recap` = the legacy single-field recap
        # (identity mirror), kept for back-compat. All from the standalone recaps cache
        # (attached in load_latest); untrusted text (rendered via the JSON island +
        # textContent, like everything else).
        "identity": (ini.get("identity") or "").strip(),
        "status": (ini.get("status") or "").strip(),
        "recap": (ini.get("recap") or "").strip(),
        "momentum": momentum,
        "momentum_rank": MOMENTUM_RANK.get(momentum, 9),
        "badge_glyph": glyph,
        "badge_label": label,
        "last_touch": _as_utc(ini.get("last_touch")),
        "age": rel_age(ini.get("last_touch"), now),
        "next_step": (ini.get("next_step") or "").strip(),
        "commits": ini.get("commits") or 0,
        "commits_unknown": bool(ini.get("commits_unknown")),
        "merged_prs": ini.get("merged_prs") or 0,
        "open_prs": [
            {"number": p.get("number"), "title": p.get("title", "")}
            for p in open_prs if isinstance(p, dict)
        ],
        "session_count": ini.get("session_count") or 0,
        "telem_events": ini.get("telem_events") or 0,
        "current_doc": ini.get("current_doc") or "",
        "open_investigations": [
            str(x) for x in (ini.get("open_investigations") or [])
        ],
        "docs": _norm_docs(ini.get("docs")),
        "tmux_sessions": tmux,
        # Phase A card-legibility signals. `recent_messages` = the user's own recent
        # prompts (newest-first, from the store); `recent_commits` = recent commit
        # subjects; `live_task` = the open tmux session's task (render-time overlay).
        "recent_messages": recent_messages,
        # The single most-recent SUBSTANTIVE prompt for the card FACE (boilerplate like
        # `dispatch`/`proceed` skipped; falls back to the newest when all are trivial).
        # `recent_messages` above stays complete for the expand.
        "face_message": pick_face_message(recent_messages),
        "recent_commits": [str(x) for x in (ini.get("recent_commits") or [])],
        # `live_task` = the first matched pane's task (kept for the detail endpoint +
        # back-compat); `live_tasks` = ALL matched panes' tasks so an initiative hosting
        # MORE than one live session shows every session's task (one line each), not just
        # the first. Both derive from the same de-duped `tmux_tasks` overlay.
        "live_task": tmux_tasks[0] if tmux_tasks else "",
        "live_tasks": tmux_tasks,
        # Session-first discovery flags (v4). `undocumented` TRUE = a session-only card (no
        # handoff doc) → the SPA routes it to the collapsed "Emerging / undocumented" lane
        # instead of the main board; `source` (doc|session|both) is the provenance. Both
        # default to the "documented" reading when a row predates the migration (missing key
        # → undocumented False), so an un-migrated store shows everything in the main board.
        "undocumented": bool(ini.get("undocumented")),
        "source": str(ini.get("source") or ""),
        # The thread's ORIGIN (genesis) prompt (v5) — the card's `start ›` line. Empty on a
        # pre-v5 store (OPTIONAL_COLUMNS: missing key → "") so the line simply isn't rendered.
        "opening_message": str(ini.get("opening_message") or "").strip(),
        # SEARCH-ONLY (v6): the user's full turn text across the session(s). Fed to the
        # client-side search blob (matchQ) so a keyword typed mid-session is findable, but
        # NEVER rendered on the card (it's the whole session — the card already shows the
        # opening + latest). "" on a pre-v6 store (OPTIONAL_COLUMNS: missing key → "").
        "search_text": str(ini.get("search_text") or ""),
    }
    # A GROUNDED, read-only next-step recommendation derived from the just-assembled view
    # (reads next_step/open_prs/open_investigations/face_message/status/momentum). None when
    # no field supports one. Targets the "Emerging" gap: session-only cards have no parsed
    # `next_step`, so this suggests one from real signal. `recommend_next_step` is PURE +
    # never raises; a load hiccup degrades to None (the card just shows no suggestion).
    try:
        view["recommended_next_step"] = _nextstep().recommend_next_step(view)
    except Exception:  # noqa: BLE001 - a recommendation is additive; never break the render
        view["recommended_next_step"] = None
    return view


def _flat_sort_key(v: dict):
    """Flat ordering: most-recently-active first (last_touch DESC), a None last_touch
    sorts last, momentum then slug as stable tiebreaks."""
    ts = v["last_touch"].timestamp() if v["last_touch"] else float("-inf")
    return (-ts, v["momentum_rank"], v["slug"])


def _session_natural_key(sess_id: str) -> tuple:
    """Order `<session>-<window>` ids naturally: '1','8-1','8-3','Pool2','Pool10'.

    Mirrors the scan's `_tmux_session_sort_key` (peel a trailing `-<window>`, then split
    the session into a non-digit prefix + numeric suffix so a numeric tail sorts by VALUE
    and the window index tiebreaks). Kept LOCAL so `build_model` stays pure — no scan
    import in the render transform (it is unit-tested with no scan / requests / psycopg2)."""
    name = sess_id or ""
    win = -1
    session = name
    mw = re.match(r"^(.*)-(\d+)$", name)
    if mw:
        session, win = mw.group(1), int(mw.group(2))
    m = re.match(r"^(.*?)(\d*)$", session)
    prefix = m.group(1) if m else session
    num = int(m.group(2)) if (m and m.group(2)) else -1
    return (prefix, num, win, name)


def _unmatched_view(u: dict) -> dict:
    """One unmatched live claude pane (`{"id","title","repo"}` from
    `match_tmux_to_initiatives`) -> a flat, template-ready view dict. `repo` is the full
    path (as the scan returns it, possibly None); `repo_name` is the short label used for
    the grouped section header (matches the initiative cards' repo label)."""
    repo = u.get("repo")
    return {
        "id": str(u.get("id") or "?"),
        "title": (str(u.get("title") or "")).strip(),
        "repo": repo or "",
        "repo_name": _short_repo(repo),
    }


def build_live_unmatched(unmatched) -> list[dict]:
    """PURE: the `match_tmux_to_initiatives` unmatched list -> the render model's
    `live_unmatched`: de-duped (by id+title, like the CLI section) view dicts, sorted by
    repo then natural session id so the page can group them by repo. A non-list input
    (e.g. a fake tmux hook returning a bool, or None) yields `[]` — the section then
    simply doesn't render."""
    if not isinstance(unmatched, list):
        return []
    seen: set = set()
    out: list[dict] = []
    for u in unmatched:
        if not isinstance(u, dict):
            continue
        v = _unmatched_view(u)
        dedupe_key = (v["id"], v["title"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(v)
    out.sort(key=lambda v: (v["repo_name"].lower(), _session_natural_key(v["id"])))
    return out


def build_model(rows: list[dict], now: datetime | None = None,
                unmatched=None) -> dict:
    """PURE: store rows (+ any attached tmux) -> BOTH a grouped and a flat render model.

    `repos` groups initiatives by repo (within a repo: momentum then recency; repos
    ordered by their most-active initiative then name). `flat` is one stream of ALL
    initiatives ordered most-recently-active first (the default view; each card carries
    a repo label). `live_unmatched` is the "everything else running" catch-all: live
    claude panes that matched NO initiative (from `attach_tmux`), so the board shows ALL
    running threads (the tagged initiatives + the uncovered sessions), not just the tagged
    few. `captured_at` (the snapshot's freshness) drives the footer. An empty row list
    yields an empty (but well-formed) model — never raises."""
    now = now or datetime.now(timezone.utc)

    # The snapshot freshness = the newest captured_at across the rows (they should all
    # share one snapshot, but max() is robust to a mixed read).
    captured_ats = [_as_utc(r.get("captured_at")) for r in rows]
    captured_at = max((c for c in captured_ats if c is not None), default=None)

    views = [_initiative_view(r, now) for r in rows]

    by_repo: dict[str | None, list[dict]] = {}
    for r, v in zip(rows, views):
        by_repo.setdefault(r.get("repo"), []).append(v)

    repos: list[dict] = []
    for repo_path, inis in by_repo.items():
        inis.sort(key=lambda v: (
            v["momentum_rank"],
            -(v["last_touch"].timestamp() if v["last_touch"] else 0.0),
            v["slug"],
        ))
        repos.append({
            "repo": repo_path,
            "name": _short_repo(repo_path),
            "best_rank": min(v["momentum_rank"] for v in inis),
            "initiatives": inis,
        })
    repos.sort(key=lambda g: (g["best_rank"], g["name"]))

    flat = sorted(views, key=_flat_sort_key)
    live_unmatched = build_live_unmatched(unmatched)

    return {
        "generated_at": now,
        "captured_at": captured_at,
        "captured_age": rel_age(captured_at, now) if captured_at else None,
        "total": len(rows),
        "repo_count": len(repos),
        "repos": repos,
        "flat": flat,
        "live_unmatched": live_unmatched,
    }


def model_to_json(model: dict | None, error: str | None) -> dict:
    """The `/api/initiatives.json` payload (datetimes isoformatted via json default=str)."""
    if error is not None or model is None:
        return {"ok": False, "error": error or "no data", "repos": [], "flat": [],
                "live_unmatched": []}
    return {
        "ok": True,
        "generated_at": model["generated_at"],
        "captured_at": model["captured_at"],
        "captured_age": model["captured_age"],
        "total": model["total"],
        "repo_count": model["repo_count"],
        "repos": model["repos"],
        "flat": model["flat"],
        "live_unmatched": model.get("live_unmatched", []),
    }


# --------------------------------------------------------------------------- #
# Detail — one initiative's live handoff read (with a path-traversal guard) + parse.
# --------------------------------------------------------------------------- #
def parse_doc_detail(text: str) -> dict:
    """PURE: a handoff doc's text -> its key sections (via the scan's deterministic
    parsers, single-sourced — no reimplementation). Goal/summary, the FULL Next-steps
    list (not just the lead item), Open investigations, and the title."""
    scan = _scan()
    return {
        "title": scan.parse_handoff_title(text),
        "summary": scan.parse_summary(text),
        "next_steps": scan.parse_all_next_steps(text),
        "open_investigations": scan.parse_open_investigations(text),
    }


def safe_doc_path(repo: str, current_doc: str,
                  repos: list[str] | None = None) -> Path | None:
    """Resolve `current_doc` to a real path ONLY if it is safe to read: contained under
    `<repo>/claudedocs/` (realpath-resolved, so `..`/symlink escapes are rejected), the
    repo is a known/discovered repo when `repos` is supplied, and the file exists. None
    otherwise. Both `repo` and `current_doc` come from the STORE (not user query input),
    but this is defense-in-depth against a traversal via a poisoned stored path."""
    if not repo or not current_doc:
        return None
    try:
        repo_real = Path(repo).resolve()
    except Exception:  # noqa: BLE001
        return None
    if repos is not None and not any(
        _safe_resolve(r) == repo_real for r in repos
    ):
        return None
    claudedocs = (repo_real / "claudedocs").resolve()
    doc = _safe_resolve(current_doc)
    if doc is None:
        return None
    if claudedocs not in doc.parents:  # must live directly/indirectly under claudedocs/
        return None
    if not doc.is_file():
        return None
    return doc


def _safe_resolve(p: str) -> Path | None:
    try:
        return Path(p).resolve()
    except Exception:  # noqa: BLE001
        return None


def read_doc_detail_live(repo: str, current_doc: str,
                         repos: list[str] | None = None) -> dict | None:
    """I/O: validate the handoff path, read it off disk, and parse its sections. None on a
    failed guard / missing file / read error (the caller falls back to the stored fields).

    When `repos` is not supplied, resolves it from `_discover_repos_safe()` so the
    known-repo allowlist in `safe_doc_path` ACTUALLY runs (best-effort — if the scan
    can't load, discovery is None and only realpath-containment guards the read). The
    read is bounded to `MAX_DOC_BYTES` so a pathological file can't spike memory."""
    if repos is None:
        repos = _discover_repos_safe()
    path = safe_doc_path(repo, current_doc, repos)
    if path is None:
        return None
    try:
        with path.open("r", errors="replace") as f:
            text = f.read(MAX_DOC_BYTES)  # bounded read (handoffs are KBs; cap is generous)
    except OSError:
        return None
    try:
        return parse_doc_detail(text)
    except Exception:  # noqa: BLE001 - a scan-import hiccup must not 500 the endpoint
        return None


def _discover_repos_safe() -> list[str] | None:
    """Best-effort `discover_repos()` for the traversal guard; None if the scan can't load."""
    try:
        return _scan().discover_repos()
    except Exception:  # noqa: BLE001
        return None


def _find_view(model: dict, repo: str, slug: str) -> dict | None:
    for v in model.get("flat") or []:
        if v.get("repo") == repo and v.get("slug") == slug:
            return v
    return None


def build_detail(model: dict | None, error: str | None, repo: str, slug: str,
                 doc_reader=read_doc_detail_live) -> dict:
    """PURE-ish: (model, repo, slug) -> the `/api/initiative` payload. Starts from the
    STORED fields (first next-step + open_investigations + PRs + docs) and OVERLAYS the
    live handoff read when it succeeds (full Next-steps list + Open investigations +
    fresher summary/title). `doc_reader` is injectable so the merge is unit-testable with
    no disk. Unknown (repo, slug) -> ok:false so the endpoint 404s."""
    if error is not None or model is None:
        return {"ok": False, "error": error or "no data"}
    view = _find_view(model, repo, slug)
    if view is None:
        return {"ok": False, "error": f"no initiative for repo={repo!r} slug={slug!r}"}

    detail = {
        "ok": True,
        "repo": view["repo"],
        "repo_name": view["repo_name"],
        "slug": view["slug"],
        "title": view["title"],
        "summary": view.get("summary") or "",
        # The recap split flows through the detail endpoint too (from the view; the live
        # handoff read below refreshes `summary`/`next_steps`, never identity/status).
        "identity": view.get("identity") or "",
        "status": view.get("status") or "",
        "recap": view.get("recap") or "",
        "current_doc": view.get("current_doc") or "",
        "open_prs": view["open_prs"],
        "docs": view.get("docs") or [],
        "next_steps": [view["next_step"]] if view.get("next_step") else [],
        "open_investigations": view["open_investigations"],
        # Phase A signals flow through the detail endpoint too (stored on the view — the
        # live handoff read below never overrides them).
        "recent_messages": view.get("recent_messages") or [],
        "recent_commits": view.get("recent_commits") or [],
        "live_task": view.get("live_task") or "",
        "live_tasks": view.get("live_tasks") or [],
        "live": False,
    }

    live = None
    try:
        live = doc_reader(view["repo"], view.get("current_doc") or "")
    except Exception:  # noqa: BLE001 - a read failure just means "use the stored fields"
        live = None
    if live:
        detail["live"] = True
        if live.get("summary"):
            detail["summary"] = live["summary"]
        if live.get("title"):
            detail["title"] = live["title"]
        if live.get("next_steps"):
            detail["next_steps"] = live["next_steps"]
        if live.get("open_investigations"):
            detail["open_investigations"] = live["open_investigations"]
    return detail


# --------------------------------------------------------------------------- #
# HTML rendering — self-contained, inline CSS + vanilla JS, gruvbox, no external assets.
# --------------------------------------------------------------------------- #
_CSS = """
:root{
  --bg:#282828; --bg1:#3c3836; --bg2:#504945; --fg:#ebdbb2; --fg2:#d5c4a1;
  --gray:#928374; --red:#fb4934; --green:#b8bb26; --yellow:#fabd2f;
  --blue:#83a598; --aqua:#8ec07c; --orange:#fe8019;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font-family:"JetBrainsMono Nerd Font","JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;
  font-size:14px;line-height:1.5;padding:1.2rem}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
header{display:flex;flex-wrap:wrap;align-items:center;gap:.6rem;
  border-bottom:1px solid var(--bg2);padding-bottom:.6rem;margin-bottom:1rem}
header h1{font-size:1.15rem;margin:0;color:var(--yellow)}
header .meta{color:var(--gray);font-size:.85rem}
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-left:auto}
.toggle{display:inline-flex;border:1px solid var(--bg2);border-radius:4px;overflow:hidden}
.tbtn{background:var(--bg1);color:var(--fg2);border:0;padding:.3rem .7rem;cursor:pointer;
  font:inherit;font-size:.82rem}
.tbtn:hover{background:var(--bg2)}
.tbtn.active{background:var(--blue);color:var(--bg)}
.search{background:var(--bg1);color:var(--fg);border:1px solid var(--bg2);border-radius:4px;
  padding:.3rem .55rem;font:inherit;font-size:.82rem;min-width:12rem}
.search:focus{outline:1px solid var(--blue)}
.search-count{color:var(--gray);font-size:.76rem;white-space:nowrap}
.search-count.none{color:var(--orange)}
.rbtn{background:var(--bg1);color:var(--aqua);border:1px solid var(--bg2);border-radius:4px;
  padding:.3rem .7rem;cursor:pointer;font:inherit;font-size:.82rem}
.rbtn:hover:not(:disabled){background:var(--bg2)}
.rbtn:disabled{opacity:.6;cursor:progress}
.rbtn.spin{animation:pulse 1s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.5}50%{opacity:1}}
.rmsg{color:var(--gray);font-size:.78rem}
.repo{margin:0 0 1.4rem}
.repo > h2{font-size:.95rem;margin:0 0 .5rem;color:var(--aqua);
  border-bottom:1px dotted var(--bg2);padding-bottom:.25rem}
.repo > h2 .count{color:var(--gray);font-weight:normal;font-size:.8rem;margin-left:.4rem}
.ini{background:var(--bg1);border-left:3px solid var(--gray);border-radius:4px;
  padding:.55rem .7rem;margin:0 0 .5rem;cursor:pointer}
.ini:hover{background:#40393622}
.ini.active{border-left-color:var(--green)}
.ini.slowing{border-left-color:var(--yellow)}
.ini.stalled{border-left-color:var(--gray)}
.ini.open{outline:1px solid var(--bg2)}
.ini .row1{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem}
.badge{font-weight:bold}
.badge.active{color:var(--green)}
.badge.slowing{color:var(--yellow)}
.badge.stalled{color:var(--gray)}
.badge.unknown{color:var(--gray)}
.slug{font-weight:bold;color:var(--fg)}
.title{color:var(--fg2)}
.repo-label{font-size:.75rem;color:var(--aqua);background:var(--bg2);border-radius:3px;
  padding:.02rem .4rem}
.age{color:var(--gray);font-size:.82rem;margin-left:auto}
.summary{margin-top:.3rem;color:var(--fg);font-size:.86rem}
.status{margin-top:.2rem;color:var(--fg2);font-size:.84rem}
.status .lbl{color:var(--yellow);margin-right:.2rem}
.start{margin-top:.25rem;color:var(--fg2);font-size:.84rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.start .lbl{color:var(--orange);margin-right:.2rem}
.msg{margin-top:.3rem;color:var(--fg);font-size:.86rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.msg .lbl{color:var(--aqua);margin-right:.2rem}
.live-task{margin-top:.25rem;color:var(--green);font-size:.84rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.live-task .lbl{color:var(--green);font-weight:bold;margin-right:.2rem}
.commit{margin-top:.25rem;color:var(--gray);font-size:.8rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.commit .lbl{color:var(--blue);margin-right:.2rem}
.tags{margin-top:.3rem;display:flex;flex-wrap:wrap;gap:.35rem;align-items:center}
.tag{font-size:.78rem;padding:.05rem .4rem;border-radius:3px;background:var(--bg2);color:var(--fg2)}
.tag.tmux{background:#665c54;color:var(--green)}
.tag.pr{background:var(--bg2);color:var(--blue)}
.next{margin-top:.3rem;color:var(--fg2);font-size:.86rem}
.next b{color:var(--orange);font-weight:normal}
/* A GROUNDED but INFERRED next-step suggestion (only on cards with no documented next_step —
   i.e. the Emerging gap). Visually distinct from a real `next` line: the label reads "(suggested)"
   and a muted italic hint names where it was grounded, so it never masquerades as a written step. */
.next.suggested b{color:var(--yellow)}
.next .hint{color:var(--gray);font-style:italic;font-size:.78rem;margin-left:.4rem}
.dispatch-btn{margin-top:.35rem;font:inherit;font-size:.78rem;cursor:pointer;
  background:var(--bg2);color:var(--aqua);border:1px solid var(--bg2);border-radius:3px;
  padding:.1rem .5rem}
.dispatch-btn:hover:not(:disabled){border-color:var(--aqua)}
.dispatch-btn:disabled{cursor:default;opacity:.7}
.dispatch-status{margin-left:.5rem;color:var(--gray);font-size:.78rem}
.dispatch-status.err{color:var(--red)}
.dispatch-status.ok{color:var(--green)}
.detail{margin-top:.5rem;padding-top:.5rem;border-top:1px dotted var(--bg2)}
.detail-summary{color:var(--fg);font-size:.86rem;margin-bottom:.4rem}
.detail-h{color:var(--orange);font-size:.8rem;margin:.4rem 0 .15rem;text-transform:uppercase;
  letter-spacing:.04em}
.detail-list{margin:.1rem 0 .3rem;padding-left:1.1rem;color:var(--fg2);font-size:.84rem}
.detail-list li{margin:.1rem 0}
.detail-doc{color:var(--gray);font-size:.78rem;margin-top:.35rem;word-break:break-all}
.detail-err{color:var(--red);font-size:.82rem}
.empty{color:var(--gray);padding:2rem 0}
/* "Live sessions — not tied to an initiative": the everything-else-running catch-all.
   Visually SECONDARY to the initiatives (dimmer, denser) but scannable at 30+ rows. */
.unmatched{margin:1.6rem 0 0;border-top:1px solid var(--bg2);padding-top:.8rem}
.unmatched > h2{font-size:.9rem;margin:0;color:var(--fg2);cursor:pointer;
  display:flex;align-items:baseline;gap:.4rem;user-select:none}
.unmatched > h2:hover{color:var(--fg)}
.unmatched > h2 .chev{color:var(--gray);font-size:.75rem;width:.8rem}
.unmatched > h2 .count{color:var(--gray);font-weight:normal;font-size:.8rem}
.unmatched > h2 .hint{color:var(--gray);font-weight:normal;font-size:.75rem;margin-left:.2rem}
.unmatched-body{margin-top:.6rem}
.u-repo{margin:0 0 .5rem}
.u-repo > h3{font-size:.8rem;margin:0 0 .2rem;color:var(--aqua);font-weight:normal}
.u-repo > h3 .count{color:var(--gray);margin-left:.35rem}
.u-row{display:flex;align-items:baseline;gap:.5rem;padding:.05rem 0 .05rem .6rem;
  font-size:.82rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.u-id{color:var(--green);flex:0 0 auto}
.u-title{color:var(--fg2);overflow:hidden;text-overflow:ellipsis}
.u-title.untitled{color:var(--gray);font-style:italic}
/* "Emerging / undocumented": session-only cards (no handoff doc). Visually SECONDARY to the
   documented board — a subtle dashed divider, a dimmer/gray header — and collapsed by default.
   The caret + count read as one obviously-clickable control (whole h2 is the hit target). */
.emerging{margin:1.8rem 0 0;border-top:1px dashed var(--bg2);padding-top:.8rem}
.emerging > h2{font-size:.9rem;margin:0;color:var(--gray);cursor:pointer;
  display:flex;align-items:baseline;gap:.4rem;user-select:none}
.emerging > h2:hover{color:var(--fg2)}
.emerging > h2 .chev{color:var(--yellow);font-size:.75rem;width:.8rem}
.emerging > h2 .count{color:var(--gray);font-weight:normal;font-size:.8rem}
.emerging-cap{color:var(--gray);font-size:.75rem;margin:.25rem 0 .1rem 1.2rem;font-style:italic}
.emerging-body{margin-top:.6rem}
/* Cards in the lane read a touch dimmer than the main board (still legible on hover/expand). */
.emerging-body .ini{opacity:.85}
.emerging-body .ini:hover,.emerging-body .ini.open{opacity:1}
.err{background:#442222;border:1px solid var(--red);color:var(--fg);
  padding:1rem;border-radius:4px}
.err b{color:var(--red)}
footer{margin-top:1.5rem;padding-top:.6rem;border-top:1px solid var(--bg2);
  color:var(--gray);font-size:.8rem}
footer .live{color:var(--green)}
/* The read-only Q&A sidebar (💬 ask). A fixed right-hand panel, hidden until toggled;
   gruvbox to match the cards. Untrusted answer text is textContent-only in the JS. */
.abtn{background:var(--bg1);color:var(--yellow);border:1px solid var(--bg2);border-radius:4px;
  padding:.3rem .7rem;cursor:pointer;font:inherit;font-size:.82rem}
.abtn:hover{background:var(--bg2)}
.abtn.active{background:var(--yellow);color:var(--bg)}
.chat{position:fixed;top:0;right:0;bottom:0;width:min(30rem,92vw);z-index:20;
  background:var(--bg);border-left:1px solid var(--bg2);box-shadow:-8px 0 24px #0006;
  display:flex;flex-direction:column;padding:.8rem}
.chat[hidden]{display:none}
.chat-head{display:flex;align-items:center;gap:.5rem;border-bottom:1px solid var(--bg2);
  padding-bottom:.5rem;margin-bottom:.5rem}
.chat-title{color:var(--yellow);font-weight:bold;font-size:.9rem}
.chat-x{margin-left:auto;background:var(--bg1);color:var(--fg2);border:1px solid var(--bg2);
  border-radius:4px;padding:.15rem .5rem;cursor:pointer;font:inherit;font-size:.8rem}
.chat-x:hover{background:var(--bg2);color:var(--red)}
.chat-log{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:.7rem;padding:.2rem 0}
.chat-empty{color:var(--gray);font-size:.82rem;padding:.6rem .2rem}
.qa{display:flex;flex-direction:column;gap:.25rem}
.qa .q{color:var(--aqua);font-size:.84rem;white-space:pre-wrap;word-break:break-word}
.qa .q .who{color:var(--gray);margin-right:.3rem}
.qa .a{color:var(--fg);font-size:.86rem;white-space:pre-wrap;word-break:break-word;
  background:var(--bg1);border-left:3px solid var(--green);border-radius:4px;padding:.4rem .55rem}
.qa .a.pending{color:var(--gray);border-left-color:var(--gray);font-style:italic}
.qa .a.err{border-left-color:var(--red)}
.qa .a p{margin:.3rem 0}
.qa .a p:first-child{margin-top:0}
.qa .a p:last-child{margin-bottom:0}
.qa .a ul,.qa .a ol{margin:.3rem 0;padding-left:1.2rem}
.qa .a li{margin:.12rem 0}
.qa .a h4{color:var(--yellow);font-size:.9rem;margin:.45rem 0 .2rem}
.qa .a strong{color:var(--fg);font-weight:bold}
.qa .a em{font-style:italic}
.qa .a a{color:var(--blue);text-decoration:underline}
.qa .a code{background:var(--bg2);border-radius:3px;padding:.02rem .3rem;font-size:.82em;
  font-family:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace}
.qa .a pre{background:var(--bg2);border-radius:4px;padding:.4rem .55rem;overflow-x:auto;
  margin:.4rem 0}
.qa .a pre code{background:none;padding:0}
.qa .srcs{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.1rem}
.qa .src{font-size:.74rem;padding:.03rem .4rem;border-radius:3px;background:var(--bg2);
  color:var(--blue)}
.qa .srcs .lbl{color:var(--gray);font-size:.74rem}
.chat-form{display:flex;gap:.4rem;margin-top:.5rem}
.chat-input{flex:1;background:var(--bg1);color:var(--fg);border:1px solid var(--bg2);
  border-radius:4px;padding:.4rem .55rem;font:inherit;font-size:.84rem}
.chat-input:focus{outline:1px solid var(--yellow)}
.chat-input:disabled{opacity:.6}
.chat-send{background:var(--bg1);color:var(--green);border:1px solid var(--bg2);
  border-radius:4px;padding:.4rem .8rem;cursor:pointer;font:inherit;font-size:.84rem}
.chat-send:hover:not(:disabled){background:var(--bg2)}
.chat-send:disabled{opacity:.55;cursor:progress}
.chat-hint{color:var(--gray);font-size:.72rem;margin-top:.4rem;text-align:center}
""".strip()


# PURE, DOM-FREE recency-bucketing logic — kept in its OWN snippet so the node test can
# `eval` it standalone (SINGLE source of truth: `_JS` embeds this verbatim via the
# __RECENCY_JS__ placeholder). Buckets an initiative's `last_touch` epoch into a recency
# key relative to a supplied "now", both in MILLISECONDS.
#
# Buckets are ROLLING now-relative windows on the AGE `now - last_touch` — each card lands
# in the NARROWEST window its age falls into (newest→oldest):
#   hour       age <  1h                             (Past hour)
#   day        age <  24h    (i.e. 1h ≤ age < 24h)   (Past 24 hours)
#   three_days age <  72h                            (Past 3 days)
#   week       age <  7d                             (Past week)
#   older      age >= 7d                             (Older)
#   unknown    ts is null / NaN                      (guard; shouldn't happen)
# Rolling windows are pure DURATION math (`now - ts` vs fixed spans), so — unlike the old
# calendar/local-midnight scheme — they are timezone- AND DST-independent: no local-midnight
# or `new Date(y,mo,d-n)` arithmetic is needed, and the same age buckets identically in every
# viewer's tz. `bucketizeRecency` groups a PRE-FILTERED, ALREADY last_touch-DESC-sorted view
# list into ordered, NON-EMPTY buckets, preserving input order within each bucket (so
# within-bucket order stays newest-first) — the one unit the render path and node test exercise.
_RECENCY_JS = r"""
var HOUR_MS = 3600000, DAY_MS = 86400000;
var RECENCY_BUCKETS = [
  {key:'hour',       label:'Past hour'},
  {key:'day',        label:'Past 24 hours'},
  {key:'three_days', label:'Past 3 days'},
  {key:'week',       label:'Past week'},
  {key:'older',      label:'Older'},
  {key:'unknown',    label:'Unknown'}
];
function recencyBucketKey(tsMs, nowMs){
  if(tsMs == null || isNaN(tsMs)) return 'unknown';
  var age = nowMs - tsMs;
  if(age < HOUR_MS)      return 'hour';        // < 1h
  if(age < 24 * HOUR_MS) return 'day';         // < 24h
  if(age < 72 * HOUR_MS) return 'three_days';  // < 72h
  if(age < 7 * DAY_MS)   return 'week';         // < 7d
  return 'older';
}
function parseLastTouch(raw){
  // `last_touch` is serialized server-side via json default=str → a SPACE-separated
  // "YYYY-MM-DD HH:MM:SS.ffffff+00:00" (NOT ISO-8601 'T'). V8 parses that leniently but the
  // ECMAScript date parse of a non-standard string is engine-defined (Firefox returns NaN),
  // so normalize the space→'T' to a valid ISO string before Date() — robust in every engine.
  if(!raw) return null;
  var t = new Date(String(raw).replace(' ', 'T')).getTime();
  return isNaN(t) ? null : t;
}
function bucketizeRecency(views, nowMs){
  var groups = {};
  views.forEach(function(v){
    var ts = parseLastTouch(v.last_touch);
    var b = recencyBucketKey(ts, nowMs);
    (groups[b] = groups[b] || []).push(v);
  });
  var out = [];
  RECENCY_BUCKETS.forEach(function(bk){
    var items = groups[bk.key];
    if(items && items.length) out.push({key:bk.key, label:bk.label, items:items});
  });
  return out;
}
""".strip()


# PURE, DOM-FREE partition of the flat initiative stream into the MAIN board vs. the
# "Emerging / undocumented" lane — kept in its OWN snippet (like _RECENCY_JS) so the node
# test evals it directly, exercising the ACTUAL page code. A card is EMERGING iff its
# `undocumented` flag is truthy (a session-only, doc-less card the scan auto-discovered);
# everything else (documented / enriched: source doc|both, or a pre-migration card with the
# flag absent → falsy) stays in `documented`. Input order is preserved within each list, so a
# last_touch-DESC input yields a last_touch-DESC emerging lane (newest-first) for free.
# Substituted into _JS at the __PARTITION_JS__ placeholder at module load.
_PARTITION_JS = r"""
function partitionInitiatives(views){
  var documented = [], emerging = [];
  (views || []).forEach(function(v){
    if(v && v.undocumented) emerging.push(v); else documented.push(v);
  });
  return {documented: documented, emerging: emerging};
}
""".strip()


# PURE, DOM-FREE card-search predicate — kept in its OWN snippet (like _RECENCY_JS) so the
# node test evals it directly, exercising the ACTUAL page code rather than a Python replica.
# `q` is the ALREADY-lowercased, trimmed query (matchQ still re-normalizes internally so it is
# correct standalone). Empty query → every card matches. Otherwise a per-card blob is built
# from: slug, title, summary + the recap split (identity/status/recap), the ORIGIN prompt
# (`opening_message`), EVERY recent-message text (so a card is findable by any prompt, not just
# the face line), the SEARCH-ONLY full session text (`search_text` — a keyword typed mid-session
# is findable even though it's never rendered), the parsed/suggested next_step, repo_name,
# momentum, and the live tmux task. Substituted into _JS at the __MATCH_JS__ placeholder at
# module load. Used for BOTH the main board and the Emerging lane.
#
# The matcher is FUZZY but a strict SUPERSET of the old exact/substring test (so "404"/"img"
# still work): query is split into whitespace tokens, and a card matches iff EVERY token
# matches the blob, where a token matches if ANY of — (1) it is a SUBSTRING of the blob (the
# fast path / old behaviour); (2) for tokens ≥4 chars, its Levenshtein edit distance to SOME
# blob word is ≤ min(2, floor(len/4)) (typo tolerance); (3) for tokens ≥5 chars, it is an
# ordered SUBSEQUENCE of some blob word (partial-typing tolerance, e.g. "annce" ⊂
# "announcement"). Tokens <4 chars use substring only (avoid noise); 4-char tokens allow
# substring + edit-distance but NOT subsequence (which over-matches at that length). Both query and blob are
# lowercased + diacritic-stripped. The blob's distinct words are tokenized ONCE per card per
# query (bounded), and Levenshtein early-exits past the cap, so it stays fast over ~41 cards ×
# a ~6KB blob. It errs toward the exact/substring path — it does NOT match everything.
_MATCH_JS = r"""
function matchQ(v, q){
  if(!q) return true;
  function norm(s){
    s = String(s == null ? '' : s).toLowerCase();
    return s.normalize ? s.normalize('NFD').replace(/[\u0300-\u036f]/g, '') : s;
  }
  var msg = (v.recent_messages || []).map(function(m){ return (m && m.text) || ''; }).join(' ');
  var hay = norm((v.slug||'') + ' ' + (v.title||'') + ' ' + (v.identity||'') + ' ' +
             (v.status||'') + ' ' + (v.recap||'') + ' ' + (v.summary||'') + ' ' +
             (v.opening_message||'') + ' ' + (v.next_step||'') + ' ' +
             (v.repo_name||'') + ' ' + (v.momentum||'') + ' ' +
             msg + ' ' + (v.search_text||'') + ' ' + (v.live_task||''));
  var tokens = norm(q).split(/\s+/).filter(Boolean);
  if(!tokens.length) return true;   // whitespace-only query → all cards (parity with empty)
  // Bounded Levenshtein: true iff edit-distance(a,b) <= max, early-exiting once a whole DP
  // row exceeds max (any monotone path to the corner passes through that row, so the final
  // distance can only be ≥ that row's minimum).
  function withinEdit(a, b, max){
    var la = a.length, lb = b.length;
    if(Math.abs(la - lb) > max) return false;
    var prev = new Array(lb + 1);
    for(var j = 0; j <= lb; j++) prev[j] = j;
    for(var i = 1; i <= la; i++){
      var cur = new Array(lb + 1);
      cur[0] = i;
      var rowMin = cur[0];
      var ca = a.charCodeAt(i - 1);
      for(var jj = 1; jj <= lb; jj++){
        var cost = ca === b.charCodeAt(jj - 1) ? 0 : 1;
        var del = prev[jj] + 1, ins = cur[jj - 1] + 1, sub = prev[jj - 1] + cost;
        var mn = del < ins ? del : ins; if(sub < mn) mn = sub;
        cur[jj] = mn;
        if(mn < rowMin) rowMin = mn;
      }
      if(rowMin > max) return false;
      prev = cur;
    }
    return prev[lb] <= max;
  }
  // Is `t` an ordered subsequence of word `w`?
  function isSubseq(t, w){
    if(t.length > w.length) return false;
    var i = 0;
    for(var j = 0; j < w.length && i < t.length; j++){
      if(t.charCodeAt(i) === w.charCodeAt(j)) i++;
    }
    return i === t.length;
  }
  // Distinct blob words, computed lazily ONCE per card per query and bounded.
  var wordSet = null;
  function words(){
    if(wordSet) return wordSet;
    wordSet = [];
    var seen = Object.create(null);
    var parts = hay.split(/[^a-z0-9]+/);
    for(var k = 0; k < parts.length && wordSet.length < 4000; k++){
      var w = parts[k];
      if(w && !seen[w]){ seen[w] = 1; wordSet.push(w); }
    }
    return wordSet;
  }
  function tokenMatches(t){
    if(hay.indexOf(t) !== -1) return true;   // fast path: substring (exact/partial) — old behaviour
    if(t.length < 4) return false;           // short tokens: substring only (avoid fuzzy noise)
    var cap = Math.min(2, Math.floor(t.length / 4));
    // Subsequence tolerance only for tokens >= 5: 4-char subsequences over-match noisily
    // (e.g. "test" ⊂ "greatest", "acme" ⊂ "acknowledgement"). Edit-distance still covers
    // 4-char typos; subsequence is for partial-typing longer words ("annce" ⊂ "announcement").
    var allowSub = t.length >= 5;
    var ws = words();
    for(var k = 0; k < ws.length; k++){
      var w = ws[k];
      if(cap > 0 && withinEdit(t, w, cap)) return true;
      if(allowSub && isSubseq(t, w)) return true;
    }
    return false;
  }
  for(var i = 0; i < tokens.length; i++){
    if(!tokenMatches(tokens[i])) return false;   // AND across tokens
  }
  return true;
}
""".strip()


# PURE, DOM-FREE markdown→HTML renderer for the Q&A answer — kept in its OWN snippet (like
# _RECENCY_JS) so the node test can eval it directly, exercising the ACTUAL page code rather
# than a Python replica. XSS discipline: the source is HTML-ESCAPED FIRST, then a LIMITED,
# fixed transform set runs on the already-escaped text — so model text can NEVER emit an
# unescaped tag. Only http/https links become anchors (a `javascript:` URL stays inert text).
# Substituted into _JS at the __MARKDOWN_JS__ placeholder at module load.
_MARKDOWN_JS = r"""
function mdEscape(s){
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function mdToHtml(src){
  if(src == null) return '';
  var text = mdEscape(src);
  // Pull fenced ```code``` and inline `code` OUT first (as placeholders) so the inline
  // bold/italic/link transforms never touch code spans. Content is already HTML-escaped.
  var blocks = [], inlines = [];
  text = text.replace(/```[^\n]*\n?([\s\S]*?)```/g, function(m, code){
    blocks.push(code.replace(/\n+$/, ''));
    return '@@CB' + (blocks.length - 1) + '@@';
  });
  text = text.replace(/`([^`\n]+)`/g, function(m, code){
    inlines.push(code);
    return '@@IC' + (inlines.length - 1) + '@@';
  });
  function inline(s){
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    s = s.replace(/\b_([^_\n]+)_\b/g, '<em>$1</em>');
    // links: ONLY http/https become anchors; the href was HTML-escaped above (safe in attr).
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function(m, label, href){
      return '<a href="' + href + '" target="_blank" rel="noopener">' + label + '</a>';
    });
    return s;
  }
  var lines = text.split('\n'), out = [], para = [], listType = null;
  function flushPara(){ if(para.length){ out.push('<p>' + para.join('<br>') + '</p>'); para = []; } }
  function closeList(){ if(listType){ out.push('</' + listType + '>'); listType = null; } }
  for(var i = 0; i < lines.length; i++){
    var ln = lines[i], t = ln.trim();
    var h = ln.match(/^(#{1,4})\s+(.*)$/);
    var ul = ln.match(/^\s*[-*]\s+(.*)$/);
    var ol = ln.match(/^\s*\d+\.\s+(.*)$/);
    if(/^@@CB\d+@@$/.test(t)){        // a fenced code block on its own line
      flushPara(); closeList(); out.push(t);
    } else if(h){
      flushPara(); closeList(); out.push('<h4>' + inline(h[2]) + '</h4>');
    } else if(ul){
      flushPara();
      if(listType !== 'ul'){ closeList(); out.push('<ul>'); listType = 'ul'; }
      out.push('<li>' + inline(ul[1]) + '</li>');
    } else if(ol){
      flushPara();
      if(listType !== 'ol'){ closeList(); out.push('<ol>'); listType = 'ol'; }
      out.push('<li>' + inline(ol[1]) + '</li>');
    } else if(t === ''){
      flushPara(); closeList();
    } else {
      closeList(); para.push(inline(ln));
    }
  }
  flushPara(); closeList();
  var html = out.join('');
  html = html.replace(/@@CB(\d+)@@/g, function(m, n){
    return '<pre><code>' + blocks[+n] + '</code></pre>'; });
  html = html.replace(/@@IC(\d+)@@/g, function(m, n){
    return '<code>' + inlines[+n] + '</code>'; });
  return html;
}
""".strip()


# The whole page's client-side behaviour: parse the embedded JSON, render
# flat|grouped|recency, filter by search, expand a card (live detail fetch), refresh (POST
# /refresh), and auto-refresh the data in place. Vanilla JS, no framework, no external
# assets. Untrusted text is written via textContent (never innerHTML) so it can't inject
# markup. The __RECENCY_JS__ placeholder is substituted at module load with _RECENCY_JS.
_JS = r"""
(function(){
  __RECENCY_JS__
  __PARTITION_JS__
  __MATCH_JS__
  __MARKDOWN_JS__
  var el0 = document.getElementById('idata');
  var data;
  try { data = JSON.parse(el0.textContent); }
  catch(e){ data = {ok:false, error:'bad payload', repos:[], flat:[], live_unmatched:[]}; }

  // v2 storage key: bumped from 'initiatives-view' when the default flipped flat→recency, so
  // browsers that persisted the OLD default ('flat') under the v1 key start fresh on the new
  // 'recency' default instead of being pinned to a stale stored 'flat'. (An explicit later
  // choice is still remembered under the v2 key.)
  var VIEW_KEY = 'initiatives-view-v2';
  var UNMATCHED_KEY = 'initiatives-unmatched-collapsed';
  // The "Emerging / undocumented" lane (session-only cards) is COLLAPSED by default — it is a
  // triage bucket, secondary to the documented board. Only an explicit '1' opens it.
  var EMERGING_KEY = 'EMERGING_OPEN';
  // 3-way view toggle: 'recency' (by last_touch, DEFAULT) | 'flat' | 'grouped' (by repo).
  // Persisted in localStorage; an unknown/legacy value falls back to the recency default.
  var VALID_VIEWS = {flat:1, grouped:1, recency:1};
  var storedView = localStorage.getItem(VIEW_KEY);
  var state = { view: VALID_VIEWS[storedView] ? storedView : 'recency', q: '' };
  // The catch-all "live sessions" section is collapsible; remember the user's choice.
  // Default EXPANDED (the whole point is to see every running thread) — set to '1' to collapse.
  var unmatchedCollapsed = localStorage.getItem(UNMATCHED_KEY) === '1';
  // Emerging lane collapsed unless the user opened it before (default false = collapsed).
  var emergingOpen = localStorage.getItem(EMERGING_KEY) === '1';
  // Documented-only totals for the header (recomputed each render; exclude the emerging lane).
  var docTotal = 0, docRepoTotal = 0, emergingTotal = 0;
  var expanded = {};     // key -> true
  var detailCache = {};  // key -> detail payload

  var app = document.getElementById('app');
  var searchInput = document.getElementById('search');
  var footer = document.getElementById('foot');
  var btnFlat = document.getElementById('view-flat');
  var btnGrouped = document.getElementById('view-grouped');
  var btnRecency = document.getElementById('view-recency');
  var btnRefresh = document.getElementById('refresh');
  var refreshMsg = document.getElementById('refresh-msg');
  var countEl = document.getElementById('count');
  var searchCountEl = document.getElementById('search-count');

  function key(v){ return (v.repo || '') + '::' + (v.slug || ''); }

  // matchQ (the pure card-search predicate) is inlined from the _MATCH_JS snippet above.

  function el(tag, cls, txt){
    var e = document.createElement(tag);
    if(cls) e.className = cls;
    if(txt != null) e.textContent = txt;
    return e;
  }

  // basis -> a short human hint (mirrors nextstep.basis_label, so the card + the card body
  // read identically). Unknown basis → '' (no hint shown).
  var BASIS_HINTS = {
    'handoff': 'from your handoff', 'open-pr': 'from an open PR',
    'investigation': 'from an open investigation', 'focus': 'from your last prompt',
    'status': 'from current status', 'stalled': 'stalled'
  };
  function basisHint(basis){ return BASIS_HINTS[basis] || ''; }

  // POST /api/dispatch {repo, slug} → create a clawgate Task for this initiative's suggested
  // next step. Disables the button + shows an inline status. The clawgate token lives on the
  // viewer (server-side); nothing RUNS until Zach taps "Dispatch" inside clawgate.
  function dispatchNextStep(v, btn, dstat){
    btn.disabled = true;
    dstat.className = 'dispatch-status';
    dstat.textContent = 'dispatching…';
    fetch('/api/dispatch', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({repo: v.repo, slug: v.slug})
    }).then(function(r){ return r.json().then(function(j){ return {ok: r.ok, j: j}; }); })
      .then(function(res){
        if(res.ok && res.j && res.j.ok){
          dstat.className = 'dispatch-status ok';
          dstat.textContent = 'task #' + res.j.task_id +
            ' created — tap Dispatch in clawgate to run';
        } else {
          dstat.className = 'dispatch-status err';
          dstat.textContent = (res.j && res.j.error) || 'dispatch failed';
          btn.disabled = false;  // allow a retry on failure
        }
      })
      .catch(function(){
        dstat.className = 'dispatch-status err';
        dstat.textContent = 'dispatch failed (network)';
        btn.disabled = false;
      });
  }

  function renderDetail(det, d, v){
    det.innerHTML = '';
    if(!d || !d.ok){
      det.appendChild(el('div', 'detail-err', (d && d.error) || 'detail unavailable'));
      return;
    }
    // Lead with the STABLE identity ("what this is"), falling back to the (possibly
    // live-refreshed) handoff summary; then the VOLATILE status ("current: …") beneath it.
    var lead = d.identity || (v && v.identity) || d.summary;
    if(lead) det.appendChild(el('div', 'detail-summary', lead));
    var dstatus = d.status || (v && v.status) || '';
    if(dstatus){
      var stx = el('div', 'status');
      stx.appendChild(el('span', 'lbl', 'current ›'));
      stx.appendChild(document.createTextNode(' ' + dstatus));
      det.appendChild(stx);
    }
    var dtasks = (d.live_tasks && d.live_tasks.length) ? d.live_tasks
                 : (d.live_task ? [d.live_task] : []);
    if(dtasks.length){
      det.appendChild(el('div', 'detail-h', dtasks.length > 1 ? 'Live sessions' : 'Live session'));
      dtasks.forEach(function(t){ det.appendChild(el('div', 'detail-summary', t)); });
    }
    var rmsgs = d.recent_messages || (v && v.recent_messages) || [];
    if(rmsgs.length){
      det.appendChild(el('div', 'detail-h', 'Recent messages'));
      var ulm = el('ul', 'detail-list');
      rmsgs.forEach(function(m){ ulm.appendChild(el('li', null, (m && m.text) || '')); });
      det.appendChild(ulm);
    }
    var ns = d.next_steps || [];
    if(ns.length){
      det.appendChild(el('div', 'detail-h', 'Next steps'));
      var ul = el('ul', 'detail-list');
      ns.forEach(function(s){ ul.appendChild(el('li', null, s)); });
      det.appendChild(ul);
    }
    var oi = d.open_investigations || [];
    if(oi.length){
      det.appendChild(el('div', 'detail-h', 'Open investigations'));
      var ul2 = el('ul', 'detail-list');
      oi.forEach(function(s){ ul2.appendChild(el('li', null, s)); });
      det.appendChild(ul2);
    }
    var rcom = d.recent_commits || (v && v.recent_commits) || [];
    if(rcom.length){
      det.appendChild(el('div', 'detail-h', 'Recent commits'));
      var ulc = el('ul', 'detail-list');
      rcom.forEach(function(s){ ulc.appendChild(el('li', null, s)); });
      det.appendChild(ulc);
    }
    var prs = d.open_prs || (v && v.open_prs) || [];
    if(prs.length){
      det.appendChild(el('div', 'detail-h', 'Open PRs'));
      var ul3 = el('ul', 'detail-list');
      prs.forEach(function(p){
        ul3.appendChild(el('li', null,
          (p.number != null ? ('#' + p.number + ' ') : '') + (p.title || '')));
      });
      det.appendChild(ul3);
    }
    if(d.current_doc){
      det.appendChild(el('div', 'detail-doc',
        (d.live ? 'handoff (live read): ' : 'handoff: ') + d.current_doc));
    }
    var docs = d.docs || [];
    if(docs.length > 1){
      det.appendChild(el('div', 'detail-h', 'Docs history'));
      var ul4 = el('ul', 'detail-list');
      docs.forEach(function(x){
        ul4.appendChild(el('li', null, (x.date || '?') + '  ' + (x.path || '')));
      });
      det.appendChild(ul4);
    }
  }

  function loadDetail(v, det){
    var k = key(v);
    if(detailCache[k]){ renderDetail(det, detailCache[k], v); return; }
    det.textContent = 'loading…';
    fetch('/api/initiative?repo=' + encodeURIComponent(v.repo) +
          '&slug=' + encodeURIComponent(v.slug))
      .then(function(r){ return r.json(); })
      .then(function(d){ detailCache[k] = d; renderDetail(det, d, v); })
      .catch(function(){ renderDetail(det, {ok:false, error:'detail unavailable'}, v); });
  }

  function card(v){
    var k = key(v);
    var c = el('div', 'ini ' + (v.momentum || 'unknown'));
    c.setAttribute('data-key', k);

    var row1 = el('div', 'row1');
    row1.appendChild(el('span', 'badge ' + (v.momentum || 'unknown'),
                        v.badge_glyph + ' ' + v.badge_label));
    row1.appendChild(el('span', 'slug', v.slug));
    if(v.title) row1.appendChild(el('span', 'title', v.title));
    // Show the repo label whenever repo isn't the section header — i.e. flat AND recency
    // (both mix repos in one stream); grouped uses the repo as its section heading instead.
    if(state.view !== 'grouped' && v.repo_name)
      row1.appendChild(el('span', 'repo-label', v.repo_name));
    row1.appendChild(el('span', 'age', 'updated ' + v.age + ' ago'));
    c.appendChild(row1);

    // The primary "what this is" line: the STABLE identity recap when present, else the
    // legacy single recap, else the deterministic handoff summary (fallback chain
    // identity → recap → summary, so a card is never blank during rollout). textContent
    // only via el().
    var primary = v.identity || v.recap || v.summary;
    if(primary) c.appendChild(el('div', 'summary', primary));

    // The secondary "current: …" line: the VOLATILE status recap (what's in progress /
    // recently done / blocked). Omitted when empty. Sits directly under the identity line
    // so "what it is" reads first, "what's happening now" second. textContent-only.
    if(v.status){
      var st = el('div', 'status');
      st.appendChild(el('span', 'lbl', 'current ›'));
      st.appendChild(document.createTextNode(' ' + v.status));
      c.appendChild(st);
    }

    // The thread's ORIGIN (genesis) prompt — the ORIGINAL ask that started the initiative
    // (v5). Shown between "what's happening now" (current ›) and the latest prompt (you ›),
    // so the card reads identity → current → start → you. Omitted when empty (pre-v5 store or
    // a doc initiative with no folded session). textContent-only, never innerHTML.
    if(v.opening_message){
      var op = el('div', 'start');
      op.appendChild(el('span', 'lbl', 'start ›'));
      op.appendChild(document.createTextNode(' ' + v.opening_message));
      c.appendChild(op);
    }

    // The user's own most-recent SUBSTANTIVE prompt — the highest-signal "what is this"
    // line (summary + latest message read well together). `face_message` skips low-signal
    // boilerplate (dispatch/proceed/yes); the expand still shows the full recent_messages
    // list. textContent-only, never innerHTML.
    var face = v.face_message;
    if(face && face.text){
      var m = el('div', 'msg');
      m.appendChild(el('span', 'lbl', 'you ›'));
      m.appendChild(document.createTextNode(' ' + face.text));
      c.appendChild(m);
    }

    // Live tmux session task summaries (render-time overlay). When an initiative hosts
    // MORE than one live session, show EVERY session's task (one line each), not just the
    // first — `live_tasks` is the full de-duped list (`live_task` is only its first item).
    var ltasks = (v.live_tasks && v.live_tasks.length) ? v.live_tasks
                 : (v.live_task ? [v.live_task] : []);
    ltasks.forEach(function(task){
      var lt = el('div', 'live-task');
      lt.appendChild(el('span', 'lbl', 'live ›'));
      lt.appendChild(document.createTextNode(' ' + task));
      c.appendChild(lt);
    });

    var tags = el('div', 'tags');
    (v.tmux_sessions || []).forEach(function(s){
      tags.appendChild(el('span', 'tag tmux', '[tmux:' + s + ']'));
    });
    (v.open_prs || []).forEach(function(p){
      var label = (p.number != null ? ('#' + p.number) : 'PR') + (p.title ? (' ' + p.title) : '');
      var t = el('span', 'tag pr', label);
      if(p.title) t.title = p.title;
      tags.appendChild(t);
    });
    // The numeric stat strip (commit/merged/session/event counts) is intentionally
    // OMITTED — low-signal on the card. PR titles + the commit subject below stay (they
    // are descriptive, not bare counts).
    c.appendChild(tags);

    // A small hint of the most-recent commit subject, when present.
    var rc = v.recent_commits || [];
    if(rc.length && rc[0]){
      var cm = el('div', 'commit');
      cm.appendChild(el('span', 'lbl', 'commit ›'));
      cm.appendChild(document.createTextNode(' ' + rc[0]));
      c.appendChild(cm);
    }

    if(v.next_step){
      var nx = el('div', 'next');
      nx.appendChild(el('b', null, 'next'));
      nx.appendChild(document.createTextNode(' › ' + v.next_step));
      c.appendChild(nx);
    }

    // GROUNDED (but inferred) next-step suggestion — shown ONLY when there is no documented
    // `next_step` (the Emerging gap). Rendered distinctly ("next (suggested) ›") with a muted
    // hint naming where it was grounded. All text via el()/textContent — never innerHTML.
    var rec = v.recommended_next_step;
    if(!v.next_step && rec && rec.text){
      var sg = el('div', 'next suggested');
      sg.appendChild(el('b', null, 'next (suggested)'));
      sg.appendChild(document.createTextNode(' › ' + rec.text));
      var hint = basisHint(rec.basis);
      if(hint) sg.appendChild(el('span', 'hint', hint));
      c.appendChild(sg);

      // One-tap dispatch of the suggested step to a clawgate Task (only when a suggestion
      // exists). Two human gates: this button, then "Dispatch" inside clawgate itself.
      var drow = el('div');
      var btn = el('button', 'dispatch-btn', 'Dispatch next step');
      var dstat = el('span', 'dispatch-status');
      btn.addEventListener('click', function(ev){
        ev.stopPropagation();  // don't toggle the card expand
        dispatchNextStep(v, btn, dstat);
      });
      drow.appendChild(btn);
      drow.appendChild(dstat);
      c.appendChild(drow);
    }

    var det = el('div', 'detail');
    det.style.display = 'none';
    c.appendChild(det);

    c.addEventListener('click', function(ev){
      if(ev.target.closest('a')) return;
      if(expanded[k]){ delete expanded[k]; det.style.display = 'none'; c.classList.remove('open'); }
      else { expanded[k] = true; det.style.display = 'block'; c.classList.add('open'); loadDetail(v, det); }
    });
    if(expanded[k]){ det.style.display = 'block'; c.classList.add('open'); loadDetail(v, det); }
    return c;
  }

  function matchUnmatched(u, q){
    if(!q) return true;
    var hay = ((u.id||'') + ' ' + (u.title||'') + ' ' + (u.repo_name||'')).toLowerCase();
    return hay.indexOf(q) !== -1;
  }

  // The "everything else running" catch-all: live claude sessions that map to NO
  // initiative. Grouped by repo, collapsible, visually secondary. Rendered BELOW the
  // initiatives so the board honestly shows ALL running threads. Empty list → no section.
  function renderUnmatched(q){
    var rows = (data.live_unmatched || []).filter(function(u){ return matchUnmatched(u, q); });
    if(!rows.length) return;  // nothing uncovered (or all filtered out) → no section

    var sec = el('section', 'unmatched');
    var h = el('h2');
    h.appendChild(el('span', 'chev', unmatchedCollapsed ? '▸' : '▾'));
    h.appendChild(document.createTextNode('Live sessions — not tied to an initiative'));
    h.appendChild(el('span', 'count', '(' + rows.length + ')'));
    h.appendChild(el('span', 'hint', 'open work the ledger doesn’t cover'));
    sec.appendChild(h);

    var body = el('div', 'unmatched-body');
    body.style.display = unmatchedCollapsed ? 'none' : 'block';
    // rows arrive pre-sorted by (repo, session) so same-repo runs are contiguous → group.
    var curRepo = null, group = null;
    rows.forEach(function(u){
      var name = u.repo_name || '(unknown repo)';
      if(name !== curRepo){
        curRepo = name;
        group = el('div', 'u-repo');
        var rh = el('h3', null, name);
        group.appendChild(rh);
        body.appendChild(group);
      }
      var row = el('div', 'u-row');
      row.appendChild(el('span', 'u-id', '[' + (u.id || '?') + ']'));
      var title = (u.title || '').trim();
      row.appendChild(el('span', 'u-title' + (title ? '' : ' untitled'),
                         title || '(untitled)'));
      group.appendChild(row);
    });
    sec.appendChild(body);

    h.addEventListener('click', function(){
      unmatchedCollapsed = !unmatchedCollapsed;
      localStorage.setItem(UNMATCHED_KEY, unmatchedCollapsed ? '1' : '0');
      body.style.display = unmatchedCollapsed ? 'none' : 'block';
      sec.querySelector('.chev').textContent = unmatchedCollapsed ? '▸' : '▾';
    });
    app.appendChild(sec);
  }

  // The "Emerging / undocumented" lane: session-only cards the scan auto-discovered that have
  // NO handoff doc. Rendered as a SEPARATE, muted, collapsed-by-default section below the main
  // board so it never dilutes the documented ledger, but nothing is dropped (Zach triages it).
  // Uses the SAME card renderer as the board so momentum/age/live-session/next-step all render.
  // Input `all` is already last_touch-DESC (from the flat stream), so cards stay newest-first.
  function renderEmerging(q, all){
    if(!all || !all.length) return;   // no session-only cards → no lane at all
    var rows = all.filter(function(v){ return matchQ(v, q); });

    // While a search is ACTIVE and it matches cards in this lane, force the lane open so the
    // matches are visible — otherwise a hit inside the collapsed Emerging lane stays hidden
    // (the count would say "N shown" with the card invisible). This does NOT persist to
    // EMERGING_KEY, so clearing the search restores the user's saved collapsed preference.
    var filtering = !!(q && q.length);
    var showBody = emergingOpen || (filtering && rows.length > 0);

    var sec = el('section', 'emerging');
    var h = el('h2');
    h.appendChild(el('span', 'chev', showBody ? '▾' : '▸'));
    h.appendChild(document.createTextNode('Emerging / undocumented'));
    h.appendChild(el('span', 'count', '(' + rows.length + ')'));
    sec.appendChild(h);
    sec.appendChild(el('div', 'emerging-cap',
      'Active Claude sessions without a handoff doc — auto-detected, may include one-offs.'));

    var body = el('div', 'emerging-body');
    body.style.display = showBody ? 'block' : 'none';
    rows.forEach(function(v){ body.appendChild(card(v)); });
    sec.appendChild(body);

    h.addEventListener('click', function(){
      emergingOpen = !emergingOpen;
      localStorage.setItem(EMERGING_KEY, emergingOpen ? '1' : '0');
      body.style.display = emergingOpen ? 'block' : 'none';
      sec.querySelector('.chev').textContent = emergingOpen ? '▾' : '▸';
    });
    app.appendChild(sec);
  }

  function updateChrome(){
    btnFlat.classList.toggle('active', state.view === 'flat');
    btnGrouped.classList.toggle('active', state.view === 'grouped');
    if(btnRecency) btnRecency.classList.toggle('active', state.view === 'recency');
    if(countEl){
      // Counts reflect the MAIN board only — emerging (session-only) cards are excluded
      // (surfaced separately, so the "in flight" number stays the documented ledger).
      var txt = docTotal + ' in flight across ' + docRepoTotal + ' repos';
      var nu = (data.live_unmatched || []).length;
      // Surface the uncovered live sessions in the header so the board's honesty is visible
      // at a glance (tagged initiatives + N untracked live threads).
      if(nu) txt += ' · ' + nu + ' live session' + (nu === 1 ? '' : 's') + ' untracked';
      if(emergingTotal) txt += ' · ' + emergingTotal + ' emerging';
      countEl.textContent = txt;
    }
    footer.innerHTML = '';
    footer.appendChild(el('span', 'live', 'live sessions ● realtime'));
    var age = data.captured_age ? (data.captured_age + ' ago') : 'unknown';
    footer.appendChild(document.createTextNode(' · store synced ' + age +
      ' · click a card to expand'));
  }

  function render(){
    app.innerHTML = '';
    if(!data.ok){
      docTotal = 0; docRepoTotal = 0; emergingTotal = 0;
      app.appendChild(el('div', 'err', 'store unreachable: ' + (data.error || '')));
      updateChrome();
      return;
    }
    var q = state.q.trim().toLowerCase();
    // Split the flat stream: DOCUMENTED/enriched cards drive the main board (all three views
    // + the header counts); EMERGING (session-only, undocumented) cards go to their own
    // collapsed lane below. Nothing is dropped — Zach triages the lane himself.
    var parts = partitionInitiatives(data.flat || []);
    var docFlat = parts.documented;
    var emergingAll = parts.emerging;
    // Header totals count the documented board only (unfiltered), so they read as the ledger
    // size regardless of the search box. Distinct repos among the documented cards.
    docTotal = docFlat.length;
    var repoSet = {};
    docFlat.forEach(function(v){ repoSet[v.repo || ''] = 1; });
    docRepoTotal = Object.keys(repoSet).length;
    emergingTotal = emergingAll.length;

    var shown = 0;
    if(state.view === 'flat'){
      var wrap = el('div', 'flat');
      docFlat.forEach(function(v){
        if(matchQ(v, q)){ wrap.appendChild(card(v)); shown++; }
      });
      app.appendChild(wrap);
    } else if(state.view === 'recency'){
      // Group the (search-filtered) DOCUMENTED stream into rolling now-relative recency
      // buckets. `docFlat` inherits data.flat's last_touch-DESC order and bucketizeRecency
      // preserves it within each bucket, so cards stay newest-first per bucket. Empty buckets
      // are omitted. Headers read like the repo-group headers (label + count).
      var rviews = docFlat.filter(function(v){ return matchQ(v, q); });
      bucketizeRecency(rviews, Date.now()).forEach(function(g){
        var sec = el('section', 'repo');
        var h = el('h2', null, g.label);
        h.appendChild(el('span', 'count', String(g.items.length)));
        sec.appendChild(h);
        g.items.forEach(function(v){ sec.appendChild(card(v)); shown++; });
        app.appendChild(sec);
      });
    } else {
      (data.repos || []).forEach(function(g){
        // Exclude emerging cards from the grouped board too (they live in the lane); a group
        // that is entirely emerging/filtered simply doesn't render.
        var vis = (g.initiatives || []).filter(function(v){
          return !v.undocumented && matchQ(v, q);
        });
        if(!vis.length) return;
        var sec = el('section', 'repo');
        var h = el('h2', null, g.name);
        h.appendChild(el('span', 'count', String(vis.length)));
        sec.appendChild(h);
        vis.forEach(function(v){ sec.appendChild(card(v)); shown++; });
        app.appendChild(sec);
      });
    }
    // "N shown / M" reflects EVERY searchable card (main board + emerging lane) so the count
    // is honest even when the query only matches cards inside the collapsed lane. `shown` is
    // the matched board cards; the emerging lane is filtered by the SAME matchQ.
    var emergingShown = emergingAll.filter(function(v){ return matchQ(v, q); }).length;
    var totalCards = docFlat.length + emergingAll.length;
    var totalShown = shown + emergingShown;
    if(totalShown === 0 && !q){
      app.appendChild(el('div', 'empty', 'No initiatives in the latest snapshot.'));
    } else if(totalShown === 0){
      app.appendChild(el('div', 'empty', 'No initiatives match "' + state.q + '".'));
    }
    updateSearchCount(totalShown, totalCards, q);
    // The "Emerging / undocumented" lane renders BELOW the main board (session-only cards the
    // scan auto-discovered). Collapsed by default; respects the same search filter when open.
    renderEmerging(q, emergingAll);
    // The catch-all live-sessions section renders BELOW the initiatives (honestly showing
    // every running thread, not just the tagged few). Respects the same search filter.
    renderUnmatched(q);
    updateChrome();
  }

  // The small "N shown / M" indicator next to the search box. Only shown while a query is
  // active (empty query = the whole board, so no count needed); a zero-match query flags the
  // 'none' state (color cue) alongside the "no matches" empty state the board already renders.
  function updateSearchCount(shownN, total, q){
    if(!searchCountEl) return;
    if(!q){ searchCountEl.textContent = ''; searchCountEl.className = 'search-count'; return; }
    searchCountEl.textContent = shownN + ' shown / ' + total;
    searchCountEl.className = 'search-count' + (shownN === 0 ? ' none' : '');
  }

  function refetch(){
    return fetch('/api/initiatives.json')
      .then(function(r){ return r.json(); })
      .then(function(d){ data = d; detailCache = {}; render(); });
  }

  function doRefresh(){
    btnRefresh.disabled = true;
    btnRefresh.classList.add('spin');
    refreshMsg.textContent = 'refreshing…';
    fetch('/refresh', {method: 'POST'})
      .then(function(r){ return r.json().then(function(j){ return {code: r.status, j: j}; }); })
      .then(function(res){
        var j = res.j || {};
        refreshMsg.textContent = j.message || (res.code < 400 ? 'done' : 'error');
        return refetch();
      })
      .catch(function(){ refreshMsg.textContent = 'refresh failed'; })
      .then(function(){
        btnRefresh.disabled = false;
        btnRefresh.classList.remove('spin');
        setTimeout(function(){ refreshMsg.textContent = ''; }, 5000);
      });
  }

  btnFlat.addEventListener('click', function(){
    state.view = 'flat'; localStorage.setItem(VIEW_KEY, 'flat'); render();
  });
  btnGrouped.addEventListener('click', function(){
    state.view = 'grouped'; localStorage.setItem(VIEW_KEY, 'grouped'); render();
  });
  if(btnRecency) btnRecency.addEventListener('click', function(){
    state.view = 'recency'; localStorage.setItem(VIEW_KEY, 'recency'); render();
  });
  // Debounce the filter (~150ms) so a fast typist doesn't re-render the whole board on every
  // keystroke; the input value is the source of truth so the last keystroke always wins.
  var searchTimer = null;
  searchInput.addEventListener('input', function(){
    if(searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(function(){ state.q = searchInput.value; render(); }, 150);
  });
  btnRefresh.addEventListener('click', doRefresh);

  // ---- Read-only Q&A sidebar (💬 ask) -------------------------------------------------
  // Single-turn: POST the question to /api/ask, render the grounded answer + the sources
  // (which initiatives it drew from). All untrusted server text via textContent (el()).
  var askToggle = document.getElementById('ask-toggle');
  var chat = document.getElementById('chat');
  var chatClose = document.getElementById('chat-close');
  var chatLog = document.getElementById('chat-log');
  var chatForm = document.getElementById('chat-form');
  var chatInput = document.getElementById('chat-input');
  var chatSend = document.getElementById('chat-send');
  var askInFlight = false;

  function chatEmptyHint(){
    if(chatLog.children.length === 0){
      chatLog.appendChild(el('div', 'chat-empty',
        'Ask about your initiatives — e.g. “what’s blocked on me?”, “status of clawgate”, ' +
        '“what’s stalled?”, or “which initiative does <thing> belong to?”. Read-only.'));
    }
  }
  function clearEmptyHint(){
    var h = chatLog.querySelector('.chat-empty');
    if(h) chatLog.removeChild(h);
  }
  function openChat(){
    chat.hidden = false;
    askToggle.classList.add('active');
    chatEmptyHint();
    chatInput.focus();
  }
  function closeChat(){
    chat.hidden = true;
    askToggle.classList.remove('active');
  }
  if(askToggle) askToggle.addEventListener('click', function(){
    if(chat.hidden) openChat(); else closeChat();
  });
  if(chatClose) chatClose.addEventListener('click', closeChat);
  document.addEventListener('keydown', function(ev){
    if(ev.key === 'Escape' && !chat.hidden) closeChat();
  });

  function renderSources(container, sources){
    if(!sources || !sources.length) return;
    var wrap = el('div', 'srcs');
    wrap.appendChild(el('span', 'lbl', 'sources:'));
    sources.forEach(function(s){
      var label = (s && s.slug) ? s.slug : '?';
      if(s && s.repo) label += ' · ' + s.repo;
      wrap.appendChild(el('span', 'src', label));
    });
    container.appendChild(wrap);
  }

  function submitQuestion(q){
    if(askInFlight || !q) return;
    askInFlight = true;
    clearEmptyHint();
    chatInput.value = '';
    chatInput.disabled = true;
    chatSend.disabled = true;

    var block = el('div', 'qa');
    var qEl = el('div', 'q');
    qEl.appendChild(el('span', 'who', 'you ›'));
    qEl.appendChild(document.createTextNode(' ' + q));
    block.appendChild(qEl);
    var aEl = el('div', 'a pending', 'thinking…');
    block.appendChild(aEl);
    chatLog.appendChild(block);
    chatLog.scrollTop = chatLog.scrollHeight;

    // STREAM the answer token-by-token from the SSE endpoint. Each SSE event is a
    // `data: {json}\n\n` frame: {delta} appends+re-renders the (markdown) answer, {done}
    // renders the grounded sources, {error} marks the bubble. The rendered answer goes
    // through mdToHtml (which ESCAPES first) — never raw model text into innerHTML. The
    // `you ›` question stays plain text (createTextNode above).
    var answer = '', gotDelta = false, srcsDone = false, settled = false;

    function applyDelta(piece){
      if(piece == null) return;
      if(!gotDelta){ aEl.classList.remove('pending'); gotDelta = true; }
      answer += piece;
      aEl.innerHTML = mdToHtml(answer);
      chatLog.scrollTop = chatLog.scrollHeight;
    }
    function markErr(text){
      aEl.classList.remove('pending');
      aEl.classList.add('err');
      if(!answer) aEl.textContent = text;
    }
    function handleEvent(chunk){
      var line = chunk;
      if(line.indexOf('data:') === 0) line = line.slice(5);
      line = line.replace(/^\s+/, '');
      if(!line) return;
      var msg;
      try { msg = JSON.parse(line); } catch(e){ return; }
      if(msg.delta != null) applyDelta(msg.delta);
      if(msg.error){ markErr('error: ' + msg.error); }
      if(msg.done){
        aEl.classList.remove('pending');
        if(answer) aEl.innerHTML = mdToHtml(answer);
        else if(!gotDelta) markErr('no answer');
        if(!srcsDone){ renderSources(block, msg.sources); srcsDone = true; }
      }
    }
    function settle(){
      if(settled) return; settled = true;
      askInFlight = false;
      chatInput.disabled = false;
      chatSend.disabled = false;
      chatInput.focus();
      chatLog.scrollTop = chatLog.scrollHeight;
    }

    fetch('/api/ask/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: q})
    }).then(function(res){
      if(!res.ok){ markErr('request failed'); settle(); return; }
      // No ReadableStream support → read the whole body, split into SSE frames (degrade).
      if(!res.body || !res.body.getReader){
        return res.text().then(function(t){
          t.split('\n\n').forEach(handleEvent); settle();
        });
      }
      var reader = res.body.getReader(), decoder = new TextDecoder(), buffer = '';
      function pump(){
        return reader.read().then(function(r){
          if(r.done){
            if(buffer.trim()) handleEvent(buffer);
            settle();
            return;
          }
          buffer += decoder.decode(r.value, {stream: true});
          var parts = buffer.split('\n\n');
          buffer = parts.pop();
          parts.forEach(handleEvent);
          return pump();
        });
      }
      return pump();
    }).catch(function(){
      markErr('request failed — is the viewer still up?');
      settle();
    });
  }

  if(chatForm) chatForm.addEventListener('submit', function(ev){
    ev.preventDefault();
    submitQuestion((chatInput.value || '').trim());
  });

  setInterval(function(){ refetch().catch(function(){}); }, __REFRESH_MS__);

  searchInput.value = state.q;
  render();
})();
""".strip()

# Inline the pure recency-bucketing snippet into the page JS (single source of truth: the
# node test evals _RECENCY_JS directly, the page runs this substituted copy).
_JS = _JS.replace("__RECENCY_JS__", _RECENCY_JS)
_JS = _JS.replace("__PARTITION_JS__", _PARTITION_JS)
_JS = _JS.replace("__MATCH_JS__", _MATCH_JS)
_JS = _JS.replace("__MARKDOWN_JS__", _MARKDOWN_JS)


def _e(s) -> str:
    """HTML-escape any value (str/None/number) for safe interpolation."""
    return html.escape("" if s is None else str(s))


def _embed_json(payload: dict) -> str:
    """Serialize a payload for a <script type=application/json> island, neutralizing any
    markup so untrusted text (titles/summaries/PR titles) can't break out of the script
    element or inject a tag. `\\uXXXX` escapes are valid JSON and JSON.parse restores them."""
    s = json.dumps(payload, default=str, ensure_ascii=False)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace(" ", "\\u2028").replace(" ", "\\u2029"))


def render_html(model: dict | None, error: str | None = None,
                refresh: int = REFRESH_SECONDS) -> str:
    """PURE: a render model (or an error) -> a complete, self-contained HTML page.

    The OK page embeds the model as a JSON island + inline JS that renders flat|grouped,
    filters by search, expands cards (live detail fetch), and refreshes. A None model /
    non-None error renders a clear server-side error box (no JS needed) while STILL serving
    a valid page, so a DB blip degrades gracefully."""
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>initiatives</title>'
        f'<style>{_CSS}</style></head><body>'
    )

    if error is not None or model is None:
        body = (
            '<header><h1>initiatives</h1>'
            '<span class="meta">live viewer</span></header>'
            '<div class="err"><b>store unreachable</b> — could not read the '
            'initiatives store this refresh. Retrying automatically.'
            f'<br><small>{_e(error or "no data")}</small></div>'
            '<footer>the page auto-refreshes; the store is populated by the '
            'initiatives-sync timer (~15min) or the ↻ refresh button.</footer>'
        )
        return head + body + "</body></html>"

    payload = _embed_json(model_to_json(model, None))
    header = (
        '<header>'
        '<h1>initiatives</h1>'
        '<span class="meta" id="count"></span>'
        '<div class="controls">'
        '<div class="toggle" role="group" aria-label="view">'
        '<button id="view-flat" class="tbtn" type="button">flat</button>'
        '<button id="view-grouped" class="tbtn" type="button">grouped</button>'
        '<button id="view-recency" class="tbtn" type="button">recency</button>'
        '</div>'
        '<input id="search" class="search" type="search" placeholder="filter…" '
        'autocomplete="off" spellcheck="false" aria-label="filter initiatives">'
        '<span id="search-count" class="search-count" aria-live="polite"></span>'
        '<button id="refresh" class="rbtn" type="button" '
        'title="run a fresh sync now">↻ refresh</button>'
        '<span id="refresh-msg" class="rmsg"></span>'
        '<button id="ask-toggle" class="abtn" type="button" '
        'title="ask a read-only question about your initiatives">💬 ask</button>'
        '</div>'
        '</header>'
    )
    # The read-only Q&A sidebar (collapsible; hidden until toggled). A transcript of
    # single-turn Q&A + an input. Untrusted answer/source text is written via textContent
    # in the JS (never innerHTML), same discipline as the cards.
    chat = (
        '<aside id="chat" class="chat" hidden aria-label="initiatives assistant">'
        '<div class="chat-head">'
        '<span class="chat-title">ask · read-only</span>'
        '<button id="chat-close" class="chat-x" type="button" title="close">✕</button>'
        '</div>'
        '<div id="chat-log" class="chat-log"></div>'
        '<form id="chat-form" class="chat-form">'
        '<input id="chat-input" class="chat-input" type="text" autocomplete="off" '
        'spellcheck="false" placeholder="what\'s blocked on me?" '
        'aria-label="ask the initiatives assistant">'
        '<button id="chat-send" class="chat-send" type="submit">send</button>'
        '</form>'
        '<div class="chat-hint">read-only · answers cite the initiatives they use</div>'
        '</aside>'
    )
    js = _JS.replace("__REFRESH_MS__", str(int(refresh) * 1000))
    return (
        head + header +
        '<main id="app"></main>' + chat +
        '<footer id="foot"></footer>'
        '<script id="idata" type="application/json">' + payload + '</script>'
        '<script>' + js + '</script>'
        '</body></html>'
    )


# --------------------------------------------------------------------------- #
# Refresh controller — single-flight + debounced subprocess sync (the ↻ button).
# --------------------------------------------------------------------------- #
def _kill_process_group(proc) -> None:
    """SIGTERM then (if it lingers) SIGKILL the process's WHOLE group; reap it. Best-effort."""
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _run_sync_subprocess(script: Path, timeout: int) -> tuple[int, str]:
    """Run run-sync.sh as a subprocess (it does its own nix-shell + sops + scan + write).
    Returns (returncode, trailing-stderr). Inherits the viewer unit's env (KUBECONFIG,
    PATH incl. sops/gh/kubectl).

    Runs in its OWN session/process group (`start_new_session=True`) so a timeout can kill
    the ENTIRE tree — subprocess.run's timeout would SIGKILL only the `bash` child, orphaning
    the `nix-shell → python sync.py → kubectl port-forward` grandchildren (which would then
    pile up under the next refresh/timer sync). On TimeoutExpired we SIGTERM/SIGKILL the whole
    group and re-raise (the controller turns it into an error result)."""
    proc = subprocess.Popen(["bash", str(script)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        _out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        raise
    return proc.returncode, (err or "").strip()[-500:]


class RefreshController:
    """Serializes on-demand syncs: only ONE runs at a time (single-flight), and a refresh
    within `min_interval` seconds of the last one is DEBOUNCED ("just synced Xs ago")
    instead of re-running, so the button can't hammer git/gh/ClickHouse/Postgres. Runs the
    sync via a subprocess (`runner`, injectable for tests) so a ~15-30s sync doesn't block
    other requests (the ThreadingHTTPServer serves it on its own thread)."""

    def __init__(self, script: Path = RUN_SYNC_PATH,
                 min_interval: float = REFRESH_MIN_INTERVAL,
                 timeout: int = REFRESH_TIMEOUT,
                 runner=_run_sync_subprocess,
                 now_fn=time.monotonic):
        self._script = script
        self._min_interval = min_interval
        self._timeout = timeout
        self._runner = runner
        self._now = now_fn
        self._lock = threading.Lock()
        self._running = False
        self._last_at: float | None = None

    def refresh(self) -> dict:
        with self._lock:
            if self._running:
                return {"ok": False, "status": "in_progress",
                        "message": "a sync is already in progress"}
            now = self._now()
            if self._last_at is not None and (now - self._last_at) < self._min_interval:
                ago = int(now - self._last_at)
                return {"ok": True, "status": "debounced",
                        "message": f"just synced {ago}s ago", "age_seconds": ago}
            self._running = True
        rc, err = 1, ""
        try:
            rc, err = self._runner(self._script, self._timeout)
        except Exception as exc:  # noqa: BLE001 - timeout/spawn failure → error result
            rc, err = 1, f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._running = False
                self._last_at = self._now()
        if rc == 0:
            return {"ok": True, "status": "synced", "message": "sync complete"}
        # Log the stderr tail server-side (journal) — do NOT return it to the
        # unauthenticated client (avoid leaking internal paths/creds hints).
        if err:
            sys.stderr.write(f"viewer: refresh sync failed (rc={rc}): {err}\n")
        return {"ok": False, "status": "error", "message": f"sync failed (rc={rc})"}


# --------------------------------------------------------------------------- #
# Data provider — reads the store + tmux with a short TTL cache; thread-safe.
# --------------------------------------------------------------------------- #
class DataProvider:
    """Fetches (store rows + live tmux) and builds a render model, cached for `ttl`
    seconds so rapid page refreshes don't each open a fresh kubectl port-forward.

    `snapshot()` returns `(model, error)`: on success `(model, None)`, on any read
    failure `(None, "<message>")` — the server renders the error inline and keeps
    serving (no crash-loop). `invalidate()` drops the cache so the NEXT snapshot re-reads
    (used right after a ↻ refresh writes a new store snapshot). Thread-safe.
    """

    def __init__(self, ttl: float = CACHE_TTL_SECONDS,
                 loader=load_latest, tmux=attach_tmux,
                 now_fn=lambda: datetime.now(timezone.utc)):
        self._ttl = ttl
        self._loader = loader
        self._tmux = tmux
        self._now = now_fn
        self._lock = threading.Lock()
        self._cached: tuple[dict | None, str | None] | None = None
        self._fetched_at = 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None
            self._fetched_at = 0.0

    def snapshot(self) -> tuple[dict | None, str | None]:
        with self._lock:
            if self._cached is not None and (time.monotonic() - self._fetched_at) < self._ttl:
                return self._cached
            try:
                rows = self._loader()
                # best-effort; mutates rows in place AND returns the live claude panes that
                # matched no initiative (build_model coerces a non-list to [] → no section).
                unmatched = self._tmux(rows)
                result: tuple[dict | None, str | None] = (
                    build_model(rows, self._now(), unmatched=unmatched), None)
            except Exception as exc:  # noqa: BLE001 - any read failure → graceful error page
                result = (None, f"{type(exc).__name__}: {exc}")
            self._cached = result
            self._fetched_at = time.monotonic()
            return result


# --------------------------------------------------------------------------- #
# HTTP layer — a thin BaseHTTPRequestHandler over a pure `route_request`.
# --------------------------------------------------------------------------- #
def route_request(path: str, provider, method: str = "GET", query: dict | None = None,
                  refresh_controller=None, body: bytes | None = None,
                  asker=None, dispatcher=None) -> tuple[int, str, bytes]:
    """PURE-ish request router: (path, provider, method, query, refresh, body, asker,
    dispatcher) -> (status, content_type, body bytes). Separated from the socket handler so
    it's unit-testable with a fake provider / controller / asker / dispatcher (no server, no
    DB, no subprocess, no network).

    `/healthz` is deliberately store-independent (PROCESS liveness). `POST /refresh` runs
    a single-flighted+debounced sync then invalidates the provider cache on success.
    `/api/initiative` returns one initiative's live detail. `POST /api/ask` runs the
    READ-ONLY Q&A assistant (`asker(question)`) — it never mutates or dispatches. `POST
    /api/dispatch` (`dispatcher(view)`) is the ONE write-adjacent endpoint: it creates a
    clawgate Task for an initiative's grounded next step — but nothing runs until Zach taps
    Dispatch inside clawgate (a second human gate), and the clawgate token is server-side."""
    if path == "/healthz":
        return 200, "text/plain; charset=utf-8", b"ok\n"

    if method == "POST" and path == "/api/ask":
        # READ-ONLY natural-language Q&A over the store. Same LAN trust model as the rest of
        # the viewer (no new auth) — but there is NO write path: the assistant only reads the
        # initiatives store + calls the local model to phrase an answer. Deliberately
        # unauthenticated, LAN-bound (Zach's call), abuse bounded by the input cap + the
        # assistant's own read-only nature.
        question = _parse_ask_question(body)
        if not question:
            return (400, "application/json; charset=utf-8",
                    json.dumps({"ok": False, "error": "missing 'question'"}).encode("utf-8"))
        if asker is None:
            return (503, "application/json; charset=utf-8",
                    json.dumps({"ok": False, "error": "assistant unavailable"}).encode("utf-8"))
        try:
            result = asker(question)
        except Exception as exc:  # noqa: BLE001 - never let an ask error kill the request
            sys.stderr.write(f"viewer: /api/ask failed: {type(exc).__name__}: {exc}\n")
            result = {"ok": False, "answer": "the assistant hit an error answering that.",
                      "sources": [], "intent": "error"}
        payload = {"ok": bool(result.get("ok", True)),
                   "answer": result.get("answer", ""),
                   "sources": result.get("sources", []),
                   "intent": result.get("intent")}
        return (200, "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))

    if method == "POST" and path == "/api/dispatch":
        # Create a clawgate Task for an initiative's GROUNDED next step. Same LAN-trust model
        # as /refresh (no new auth): the viewer binds LAN/localhost only, and the clawgate
        # token is SERVER-SIDE (viewer-held), never exposed to the browser or the in-cluster
        # devpod. This is write-ADJACENT, not a state mutation: it only enqueues an adjudicable
        # Task card — Zach still taps "Dispatch" inside clawgate (a second human gate) before
        # anything runs. Wrapped like /api/ask so a dispatch failure never 500s uncaught.
        repo, slug = _parse_dispatch_body(body)
        if not repo or not slug:
            return (400, "application/json; charset=utf-8",
                    json.dumps({"ok": False, "error": "missing 'repo'/'slug'"}).encode("utf-8"))
        model, _err = provider.snapshot()
        flat = (model or {}).get("flat", []) if model else []
        view = next(
            (v for v in flat if v.get("slug") == slug
             and (repo == v.get("repo") or repo == v.get("repo_name"))),
            None)
        if view is None:
            return (404, "application/json; charset=utf-8",
                    json.dumps({"ok": False,
                                "error": f"no initiative matching slug={slug!r}"}).encode("utf-8"))
        try:
            # Resolve the lazy sibling INSIDE the try so a dispatch.py import failure degrades
            # to a graceful 502 (not the outer handler's caught-500) — honouring the contract
            # in this branch's header comment that a dispatch failure never 500s uncaught.
            dispatch = dispatcher if dispatcher is not None else _dispatch().dispatch_initiative
            result = dispatch(view)
        except Exception as exc:  # noqa: BLE001 - never let a dispatch error kill the request
            sys.stderr.write(f"viewer: /api/dispatch failed: {type(exc).__name__}: {exc}\n")
            result = {"ok": False, "task_id": None, "error": f"{type(exc).__name__}"}
        if result.get("ok"):
            return (200, "application/json; charset=utf-8",
                    json.dumps({"ok": True, "task_id": result.get("task_id")}).encode("utf-8"))
        return (502, "application/json; charset=utf-8",
                json.dumps({"ok": False,
                            "error": result.get("error") or "dispatch failed"}).encode("utf-8"))

    if method == "POST" and path == "/refresh":
        # Deliberately UNAUTHENTICATED: the viewer binds LAN/localhost only (not the public
        # gateway), so /refresh is LAN-trusted by design (Zach's call). Abuse is bounded by
        # the controller's single-flight + ~60s debounce + the sync's own idempotency —
        # NOT by auth/token/localhost-gating (intentionally none).
        if refresh_controller is None:
            return (503, "application/json; charset=utf-8",
                    json.dumps({"ok": False, "status": "disabled",
                                "message": "refresh not available"}).encode("utf-8"))
        result = refresh_controller.refresh()
        if result.get("status") == "synced":
            try:
                provider.invalidate()
            except Exception:  # noqa: BLE001 - a missing invalidate() must not 500
                pass
        code = {"synced": 200, "debounced": 200, "in_progress": 409,
                "error": 500, "disabled": 503}.get(result.get("status"), 200)
        return (code, "application/json; charset=utf-8",
                json.dumps(result).encode("utf-8"))

    if path in ("/", ""):
        model, error = provider.snapshot()
        return 200, "text/html; charset=utf-8", render_html(model, error).encode("utf-8")

    if path == "/api/initiatives.json":
        model, error = provider.snapshot()
        payload = json.dumps(model_to_json(model, error), default=str,
                             ensure_ascii=False, indent=2)
        return 200, "application/json; charset=utf-8", payload.encode("utf-8")

    if path == "/api/initiative":
        q = query or {}
        repo = _first_qs(q, "repo")
        slug = _first_qs(q, "slug")
        model, error = provider.snapshot()
        detail = build_detail(model, error, repo, slug)
        code = 200 if detail.get("ok") else 404
        return (code, "application/json; charset=utf-8",
                json.dumps(detail, default=str, ensure_ascii=False).encode("utf-8"))

    return 404, "text/plain; charset=utf-8", b"not found\n"


def _first_qs(query: dict, name: str) -> str:
    v = query.get(name)
    if isinstance(v, list):
        return v[0] if v else ""
    return v if isinstance(v, str) else ""


def _parse_ask_question(body: bytes | None) -> str:
    """Extract the `question` string from a JSON POST body. "" on any parse failure /
    non-string / empty — the route turns that into a 400. Pure + defensive."""
    if not body:
        return ""
    try:
        obj = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError):
        return ""
    if not isinstance(obj, dict):
        return ""
    q = obj.get("question")
    return q.strip() if isinstance(q, str) else ""


def _parse_dispatch_body(body: bytes | None) -> tuple[str, str]:
    """Extract `(repo, slug)` from a JSON POST body for POST /api/dispatch. ("", "") on any
    parse failure / non-string / empty — the route turns a missing field into a 400. Pure +
    defensive (mirrors _parse_ask_question)."""
    if not body:
        return "", ""
    try:
        obj = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError):
        return "", ""
    if not isinstance(obj, dict):
        return "", ""
    repo = obj.get("repo")
    slug = obj.get("slug")
    return (repo.strip() if isinstance(repo, str) else "",
            slug.strip() if isinstance(slug, str) else "")


def make_handler(provider, refresh_controller=None, asker=None, stream_asker=None,
                 dispatcher=None):
    """Build a BaseHTTPRequestHandler subclass bound to `provider` + `refresh_controller`
    + `asker` (the READ-ONLY Q&A callable for POST /api/ask; defaults to `default_ask`
    over this provider) + `stream_asker` (the STREAMING generator for POST /api/ask/stream;
    None ⇒ that endpoint 503s) + `dispatcher` (the POST /api/dispatch callable `view ->
    {ok,task_id,error}`; None ⇒ the route lazily loads `dispatch.dispatch_initiative`). All
    are injectable so the routes are testable without a model / network / clawgate token."""
    if asker is None:
        asker = lambda q: default_ask(q, provider)  # noqa: E731

    class Handler(BaseHTTPRequestHandler):
        server_version = "initiatives-viewer/2.0"

        def _serve(self, write_body: bool, method: str, body: bytes | None = None) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                status, ctype, resp = route_request(
                    parsed.path, provider, method=method, query=query,
                    refresh_controller=refresh_controller, body=body, asker=asker,
                    dispatcher=dispatcher)
            except Exception as exc:  # noqa: BLE001 - never let a handler error kill the thread
                status, ctype = 500, "text/plain; charset=utf-8"
                resp = f"internal error: {type(exc).__name__}: {exc}\n".encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(resp)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if write_body:
                self.wfile.write(resp)

        def _read_body(self) -> bytes:
            """Read the request body (capped at MAX_ASK_BODY_BYTES) so it can be parsed
            (POST /api/ask) AND the connection stays usable. An over-cap body is truncated
            — the JSON parse then simply fails and the route 400s."""
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return b""
            return self.rfile.read(min(n, MAX_ASK_BODY_BYTES))

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            self._serve(write_body=True, method="GET")

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(write_body=False, method="GET")

        def do_POST(self) -> None:  # noqa: N802
            body = self._read_body()
            if urlparse(self.path).path == "/api/ask/stream":
                self._serve_ask_stream(body)
                return
            self._serve(write_body=True, method="POST", body=body)

        def _send_json(self, status: int, obj: dict) -> None:
            payload = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _serve_ask_stream(self, body: bytes | None) -> None:
            """STREAM the READ-ONLY Q&A answer as Server-Sent Events (token-by-token).
            Same LAN trust model + read-only nature as POST /api/ask — no auth, no write path;
            abuse bounded by the input cap + the assistant's read-only design (Zach's call).
            The non-stream POST /api/ask JSON contract is UNCHANGED for curl/tests/other callers."""
            question = _parse_ask_question(body)
            if not question:
                self._send_json(400, {"ok": False, "error": "missing 'question'"})
                return
            if stream_asker is None:
                self._send_json(503, {"ok": False, "error": "assistant unavailable"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")   # defeat any reverse-proxy buffering
            self.send_header("Connection", "close")
            self.end_headers()

            def sse(obj) -> None:
                self.wfile.write(
                    ("data: " + json.dumps(obj, ensure_ascii=False, default=str) + "\n\n")
                    .encode("utf-8"))
                self.wfile.flush()

            try:
                for chunk in stream_asker(question):
                    sse(chunk)
            except Exception as exc:  # noqa: BLE001 - never let a stream error kill the thread
                sys.stderr.write(
                    f"viewer: /api/ask/stream failed: {type(exc).__name__}: {exc}\n")
                try:
                    sse({"error": type(exc).__name__})
                except Exception:  # noqa: BLE001 - client likely hung up; nothing to do
                    pass

        def log_message(self, fmt, *args) -> None:  # quiet: one compact line to stderr
            sys.stderr.write("viewer: %s - %s\n" % (self.address_string(), fmt % args))

    return Handler


def serve(host: str, port: int, provider: DataProvider | None = None,
          refresh_controller: RefreshController | None = None) -> None:
    """Start the blocking HTTP server on host:port with a threaded handler."""
    provider = provider or DataProvider()
    refresh_controller = refresh_controller or RefreshController()
    # asker=None ⇒ make_handler defaults to the deterministic regex assistant. When
    # INITIATIVES_AGENT_ENABLED is set, build_asker returns the agent-backed asker (with a
    # graceful fall-back to that same deterministic path).
    asker = build_asker(provider)
    stream_asker = build_stream_asker(provider)
    httpd = ThreadingHTTPServer(
        (host, port),
        make_handler(provider, refresh_controller, asker=asker, stream_asker=stream_asker))
    httpd.daemon_threads = True
    sys.stderr.write(f"viewer: serving on http://{host}:{port}/ "
                     f"(/, /healthz, /api/initiatives.json, /api/initiative, POST /refresh, "
                     f"POST /api/ask, POST /api/ask/stream, POST /api/dispatch)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live web viewer over the initiatives store (initiatives.latest + "
                    "a live tmux overlay). Binds LAN/localhost only.")
    p.add_argument("--host", default=os.environ.get("INITIATIVES_VIEWER_HOST", DEFAULT_HOST),
                   help=f"bind address (default {DEFAULT_HOST}; use 127.0.0.1 for local-only)")
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("INITIATIVES_VIEWER_PORT", DEFAULT_PORT)),
                   help=f"bind port (default {DEFAULT_PORT})")
    p.add_argument("--ttl", type=float, default=CACHE_TTL_SECONDS,
                   help=f"in-process data cache TTL seconds (default {CACHE_TTL_SECONDS})")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    serve(a.host, a.port, DataProvider(ttl=a.ttl), RefreshController())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
