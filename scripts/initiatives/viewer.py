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

# The standalone archived-lifecycle store (POST /api/archive + /api/unarchive → the board's
# manual "done / drop" cleanup). Viewer-side ONLY (full mailbox creds, same path as the
# assistant-log write). Loaded lazily by explicit importlib path (the sibling convention).
ARCHIVE_PATH = Path(__file__).resolve().parent / "archive.py"

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


_archive_mod = None


def _archive():
    """Load the standalone archived-lifecycle sibling (`archive.py`) by EXPLICIT importlib path
    (same convention as `_dispatch`). Lazy so a viewer that never archives pays nothing and
    doesn't need archive.py present at import; `archive.archive/unarchive/read_archived` back
    the POST /api/archive + /api/unarchive endpoints and the board's read-time join."""
    global _archive_mod
    if _archive_mod is None:
        spec = importlib.util.spec_from_file_location("initiatives_viewer_archive", ARCHIVE_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {ARCHIVE_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _archive_mod = mod
    return _archive_mod


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
def load_latest() -> tuple[list[dict], list[dict]]:
    """Read the current initiatives from `initiatives.latest` → `(rows, archived)`.

    Prefers the `latest` view (newest snapshot only, no ghosts). If that view doesn't
    exist yet (before the next sync recreates the schema), transparently falls back to
    an inline `WHERE snapshot_id=(SELECT max(id) …)` query over the base table — the
    same rows the view would return. Raises on an unreachable store; the provider turns
    that into a graceful error page rather than crashing the server.

    `archived` is the standalone `initiatives.archived` set, read on the SAME connection
    (like `attach_recaps`) so the board opens no second port-forward. It's BEST-EFFORT: a
    missing table / read hiccup degrades to `[]` (nothing suppressed, board still renders);
    only the `latest`/base read raises. `build_model` joins it in to suppress archived cards
    and to feed the Done view."""
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
            # The archived set, read on the SAME connection (best-effort → [] on any hiccup,
            # so a store that has never been archived-into still renders the full board).
            try:
                archived = _archive().read_archived(db.conn)
            except Exception:  # noqa: BLE001 - archived read is additive; never break load
                archived = []
            return rows, archived


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


def _epoch_or_none(v) -> int | None:
    """Coerce a tmux `activity_ts` (epoch seconds) to an int, or None on any non-integer
    (missing / blank / bad value from an older tmux). Keeps the Live-now sort key robust."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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


# --------------------------------------------------------------------------- #
# Derived triage STATE (board redesign, state model v2). Each view is classified into ONE of
# FOUR mutually-exclusive states — needs_you|stalled|slowing|active — plus a single, always-
# ACTIONABLE `line2`. "live" is DELIBERATELY NOT a state (the owner runs ~19 concurrent agents,
# so a `live` state hijacked the whole board): it is an independent OVERLAY BADGE (`view["live"]`)
# shown on line 1 regardless of the underlying state. PURE + unit-tested with fixtures (no DB).
# --------------------------------------------------------------------------- #
STATE_NEEDS_YOU = "needs_you"
STATE_STALLED = "stalled"
STATE_SLOWING = "slowing"
STATE_ACTIVE = "active"

# Precedence order for grouping/sorting (first = highest). Mirrors the JS `stateRank`.
STATE_PRECEDENCE = (STATE_NEEDS_YOU, STATE_STALLED, STATE_SLOWING, STATE_ACTIVE)

# The single, most-relevant collapsed-card second line is trimmed to a scannable width so the
# card stays two lines regardless of the underlying text length. textContent-safe (the JS
# writes it via el()/textContent, never innerHTML).
LINE2_TRIM = 160

# FALLBACK blocked-marker set — used ONLY when the assistant sibling can't be imported, so
# `derive_state` still classifies `needs_you` offline (e.g. a pure unit test with no siblings
# on the path). It is a VERBATIM copy of `assistant.BLOCKED_MARKERS` / `_BLOCKED_FIELDS`; a
# parity test pins the two together so they can never drift. `assistant._blocking_hits` is the
# SINGLE SOURCE OF TRUTH — this is only the degraded path.
_FALLBACK_BLOCKED_MARKERS = (
    "awaiting", "blocked on", "blocked", "your call", "your input", "your decision",
    "your review", "your sign-off", "your signoff", "your go-ahead", "your go ahead",
    "waiting on you", "waiting for you", "waiting on zach", "waiting for zach",
    "needs you", "needs zach", "need zach", "need you to", "pending your",
    "up to zach", "hand to the user", "hand it to the user",
)
_FALLBACK_BLOCKED_FIELDS = ("status", "next_step")

# FALLBACK severity-marker set — the same degraded-path role as `_FALLBACK_BLOCKED_MARKERS`,
# used ONLY when the assistant sibling can't be imported so `derive_state` can still promote a
# genuinely-urgent card to `needs_you` offline. A VERBATIM copy of `assistant.SEVERITY_MARKERS`
# / `_BLOCKED_FIELDS`; a parity test pins the two together so they can never drift.
# `assistant._severity_hits` is the SINGLE SOURCE OF TRUTH — this is only the degraded path.
_FALLBACK_SEVERITY_MARKERS = (
    "still happening", "still failing", "still broken", "still down", "still erroring",
    "unresolved", "not resolved",
    "out of space", "almost out of space", "disk full",
    "5xx", "499s", "500s", "502", "503", "504",
    "outage", "crashloop", "crash-loop", "oomkill", "oom-kill", "data loss",
    "flapping", "prod is down", "down in prod",
)
_FALLBACK_SEVERITY_FIELDS = ("status", "next_step")


def _fallback_blocking_hits(view: dict) -> list[str]:
    """Local copy of `assistant._blocking_hits` for the assistant-unavailable path."""
    hay = " ".join(str(view.get(f) or "") for f in _FALLBACK_BLOCKED_FIELDS).lower()
    return [mk for mk in _FALLBACK_BLOCKED_MARKERS if mk in hay]


def _fallback_severity_hits(view: dict) -> list[str]:
    """Local copy of `assistant._severity_hits` for the assistant-unavailable path."""
    hay = " ".join(str(view.get(f) or "") for f in _FALLBACK_SEVERITY_FIELDS).lower()
    return [mk for mk in _FALLBACK_SEVERITY_MARKERS if mk in hay]


def _blocking_hits_for(view: dict) -> list[str]:
    """The BLOCKED markers tripped by a view's status/next_step — sourced from
    `assistant._blocking_hits` (the SINGLE source, not re-hardcoded), degrading to the local
    fallback copy when the assistant sibling can't load. Never raises."""
    try:
        return _assistant()._blocking_hits(view)
    except Exception:  # noqa: BLE001 - the assistant is optional here; fall back to local markers
        return _fallback_blocking_hits(view)


def _severity_hits_for(view: dict) -> list[str]:
    """The SEVERITY markers tripped by a view's status/next_step — sourced from
    `assistant._severity_hits` (the SINGLE source, not re-hardcoded), degrading to the local
    fallback copy when the assistant sibling can't load. Never raises. A non-empty result
    promotes the card to `needs_you` even when no blocked-marker is present."""
    try:
        return _assistant()._severity_hits(view)
    except Exception:  # noqa: BLE001 - the assistant is optional here; fall back to local markers
        return _fallback_severity_hits(view)


def _line2_trim(text) -> str:
    s = (text or "").strip()
    return s if len(s) <= LINE2_TRIM else s[: LINE2_TRIM - 1].rstrip() + "…"


def derive_live(view: dict) -> bool:
    """The independent LIVE overlay: True when the initiative has any live tmux session/task.
    NOT a state — a live card still classifies by its underlying momentum/block state; this is
    a badge shown alongside. Decoupled so ~19 concurrent agents don't hijack the board."""
    return bool((view.get("live_tasks") or []) or (view.get("tmux_sessions") or []))


def _blocker_text(view: dict, hits: list[str]) -> str:
    """The status/next_step field that tripped a BLOCKED marker (else whichever is present,
    else the hit phrase). Used as a needs_you line2 fallback."""
    status = (view.get("status") or "").strip()
    next_step = (view.get("next_step") or "").strip()
    for cand in (status, next_step):
        if cand and any(mk in cand.lower() for mk in hits):
            return cand
    return status or next_step or (hits[0] if hits else "")


def _face_or_last_prompt(view: dict) -> str:
    """The card's face message text, or the newest recent-message text — the "last activity"
    fallback for a stalled/slowing card's line2."""
    face = view.get("face_message") or {}
    face_text = (face.get("text") if isinstance(face, dict) else "") or ""
    if not face_text:
        msgs = view.get("recent_messages") or []
        if msgs and isinstance(msgs[0], dict):
            face_text = msgs[0].get("text") or ""
    return face_text


def _derive_line2(view: dict, state: str, hits: list[str]) -> str:
    """The single, always-ACTIONABLE second line (state model v2 — live never overrides it):
      recommended_next_step.text → next_step → (needs_you) blocker text →
      (stalled/slowing) "last: <face/last prompt>" → status → summary.
    Trimmed to LINE2_TRIM, textContent-safe."""
    rec = view.get("recommended_next_step") or {}
    text = (rec.get("text") if isinstance(rec, dict) else "") or ""
    if not text:
        text = (view.get("next_step") or "").strip()
    if not text and state == STATE_NEEDS_YOU:
        text = _blocker_text(view, hits)
    if not text and state in (STATE_STALLED, STATE_SLOWING):
        face = _face_or_last_prompt(view)
        if face:
            text = "last: " + face
    if not text:
        text = (view.get("status") or "").strip()
    if not text:
        text = (view.get("summary") or "").strip()
    return _line2_trim(text)


def derive_needs_reason(view: dict) -> str:
    """PURE: WHY a card is `needs_you` — "blocked" (a genuine wait on the human) vs "severity"
    (an active, unresolved RISK surfaced by SEVERITY_MARKERS), else "" (not needs_you).

    "blocked" wins when both trip: a real wait is the more actionable framing, and the card's
    line2 already leads with the blocker text. Consumed by build_model → the card's `⚠ risk`
    cue + tooltip so a severity-promoted card reads distinctly from a blocked one."""
    if _blocking_hits_for(view):
        return "blocked"
    if _severity_hits_for(view):
        return "severity"
    return ""


def derive_state(view: dict) -> tuple[str, str]:
    """PURE: classify a view into (state, line2). Precedence (first match wins):
    needs_you > stalled > slowing > active. `live` is NOT here — it's a separate badge
    (`derive_live`) so a fleet of concurrent agents doesn't dominate the board.

      - needs_you: `_blocking_hits(view)` OR `_severity_hits(view)` non-empty. The severity
                   path promotes a genuinely-urgent live risk (a client-facing prod disk
                   filling, a week-old "499s still happening") that would otherwise sit
                   silently in `cooling` with "Needs you 0".
      - stalled:   momentum == "stalled" (≥7d since last touch — absolute age bucket).
      - slowing:   momentum == "slowing" (2–7d — the restored yellow "cooling" cue).
      - active:    everything else.

    `line2` is ALWAYS the actionable next-step (see `_derive_line2`) — for a needs_you card it
    leads with the field that tripped a marker (blocked hits preferred, else severity). Unit-
    testable without a DB: the `*_for` helpers degrade to the local marker copies offline."""
    blocked = _blocking_hits_for(view)
    severity = _severity_hits_for(view)
    hits = blocked or severity   # line2 blocker text prefers the genuine wait, else the risk
    momentum = view.get("momentum") or ""
    if blocked or severity:
        state = STATE_NEEDS_YOU
    elif momentum == "stalled":
        state = STATE_STALLED
    elif momentum == "slowing":
        state = STATE_SLOWING
    else:
        state = STATE_ACTIVE
    return state, _derive_line2(view, state, hits)


def _initiative_view(ini: dict, now: datetime) -> dict:
    """One store row (+ any attached tmux_sessions) -> a flat, template-ready view dict."""
    momentum = ini.get("momentum") or "unknown"
    glyph, label = momentum_badge(momentum)
    open_prs = ini.get("open_prs") or []
    tmux = sorted(ini.get("tmux_sessions") or [])
    # The matched live pane's task summary (render-time tmux overlay, viewer-side only —
    # not stored). First title if a session is open, else "".
    tmux_tasks = [str(t) for t in (ini.get("tmux_tasks") or []) if str(t).strip()]
    # Parallel `{task: activity_ts}` overlay from the scan (epoch seconds of the pane's last
    # window activity). Used to build `live_tasks_meta` so Live-now can sort by freshness +
    # show a per-row age WITHOUT changing the back-compat `live_tasks` string list.
    tmux_task_activity = ini.get("tmux_task_activity") or {}
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
        # Activity-carrying mirror of `live_tasks` (aligned + ordered the same): each entry is
        # {task, activity_ts} so buildLiveNow can sort the pinned strip by freshness and label
        # each row with a relative age. `activity_ts` is epoch seconds or None (older tmux).
        # `live_tasks` (strings) stays the back-compat field the `● live` badge/detail read.
        "live_tasks_meta": [
            {"task": t, "activity_ts": _epoch_or_none(tmux_task_activity.get(t))}
            for t in tmux_tasks
        ],
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
    # Derived triage state (needs_you|stalled|slowing|active) + the always-actionable line2 +
    # the independent `live` overlay badge. Depends on the fields assembled above, so it MUST
    # run last. Guarded per-row (mirrors the recommend_next_step guard) so a future bad row
    # degrades ONE card to a safe default instead of failing the whole render.
    try:
        view["state"], view["line2"] = derive_state(view)
        view["live"] = derive_live(view)
        # WHY a needs_you card needs you: "blocked" (a real wait) vs "severity" (an active
        # risk promoted by SEVERITY_MARKERS) vs "" — drives the card's distinct `⚠ risk` cue.
        view["needs_reason"] = derive_needs_reason(view)
    except Exception:  # noqa: BLE001 - per-row isolation: never let one row break build_model
        view["state"], view["line2"], view["live"] = STATE_ACTIVE, "", False
        view["needs_reason"] = ""
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
        # Epoch seconds of the pane's last window activity (or None) — feeds Live-now's
        # freshness sort + per-row age, exactly like a matched row's live_tasks_meta.
        "activity_ts": _epoch_or_none(u.get("activity_ts")),
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


def _archived_map(archived) -> dict[tuple, datetime | None]:
    """`archived` rows -> `{(repo, slug): archived_at}` (UTC-coerced). A non-list (a loader
    that couldn't read the set) yields `{}` → nothing suppressed. PURE + defensive."""
    out: dict[tuple, datetime | None] = {}
    if not isinstance(archived, list):
        return out
    for a in archived:
        if not isinstance(a, dict):
            continue
        out[(a.get("repo"), a.get("slug"))] = _as_utc(a.get("archived_at"))
    return out


def _is_suppressed(row: dict, archived_map: dict) -> bool:
    """A card is SUPPRESSED from the board iff it is archived AND has had no new activity
    since it was archived (`last_touch <= archived_at`). If `last_touch > archived_at` the
    initiative RESURFACES (not suppressed) — archive means "done for now", not "delete". An
    archived row with no usable `archived_at` (None) stays suppressed (best it can do). PURE."""
    arch_at = archived_map.get((row.get("repo"), row.get("slug")), "__absent__")
    if arch_at == "__absent__":
        return False   # not archived
    if arch_at is None:
        return True    # archived but no timestamp → keep it suppressed
    last_touch = _as_utc(row.get("last_touch"))
    if last_touch is None:
        return True    # archived, no activity signal → suppressed
    return last_touch <= arch_at   # resurface iff activity is STRICTLY newer than the archive


def build_archived_view(archived, now: datetime) -> list[dict]:
    """PURE: the `initiatives.archived` rows -> the Done view's render list (newest archived
    first). Each row carries a repo label + a relative "archived Xd ago" age. Untrusted text
    (title/slug/reason) is rendered via textContent in the JS, like every other card. A
    non-list input yields `[]`."""
    if not isinstance(archived, list):
        return []
    out: list[dict] = []
    for a in archived:
        if not isinstance(a, dict):
            continue
        arch_at = _as_utc(a.get("archived_at"))
        out.append({
            "repo": a.get("repo") or "",
            "repo_name": _short_repo(a.get("repo")),
            "slug": a.get("slug") or "(no slug)",
            "title": (a.get("title") or "").strip(),
            "reason": (a.get("reason") or "").strip(),
            "archived_at": arch_at,
            "archived_age": rel_age(arch_at, now) if arch_at else None,
        })
    # Newest archived first; a None archived_at sorts last, slug as a stable tiebreak.
    out.sort(key=lambda a: (
        -(a["archived_at"].timestamp() if a["archived_at"] else float("-inf")),
        a["slug"],
    ))
    return out


def build_model(rows: list[dict], now: datetime | None = None,
                unmatched=None, archived=None) -> dict:
    """PURE: store rows (+ any attached tmux) -> BOTH a grouped and a flat render model.

    `repos` groups initiatives by repo (within a repo: momentum then recency; repos
    ordered by their most-active initiative then name). `flat` is one stream of ALL
    initiatives ordered most-recently-active first (the default view; each card carries
    a repo label). `live_unmatched` is the "everything else running" catch-all: live
    claude panes that matched NO initiative (from `attach_tmux`), so the board shows ALL
    running threads (the tagged initiatives + the uncovered sessions), not just the tagged
    few. `captured_at` (the snapshot's freshness) drives the footer. An empty row list
    yields an empty (but well-formed) model — never raises.

    `archived` is the standalone archived set (Phase 2). Archived cards with no new activity
    since archiving are SUPPRESSED from `repos`/`flat` (and thus the counts + triage chips);
    an archived card whose `last_touch` advanced past its `archived_at` RESURFACES. The full
    archived set (including cards that have aged out of `latest`) rides along as `archived`
    for the board's Done view. A missing/empty set suppresses nothing."""
    now = now or datetime.now(timezone.utc)

    archived_map = _archived_map(archived)

    # The snapshot freshness = the newest captured_at across the rows (they should all
    # share one snapshot, but max() is robust to a mixed read). Computed over ALL rows
    # (freshness is a property of the snapshot, independent of per-card suppression).
    captured_ats = [_as_utc(r.get("captured_at")) for r in rows]
    captured_at = max((c for c in captured_ats if c is not None), default=None)

    # Drop archived-not-resurfaced rows BEFORE building views, so they are absent from the
    # repo groups, the flat stream, and every state count / triage chip consistently.
    rows = [r for r in rows if not _is_suppressed(r, archived_map)]

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
        # The Done view's data (the FULL archived set, newest first) — separate from the
        # board (`repos`/`flat`), which has the archived cards suppressed.
        "archived": build_archived_view(archived, now),
    }


def model_to_json(model: dict | None, error: str | None) -> dict:
    """The `/api/initiatives.json` payload (datetimes isoformatted via json default=str)."""
    if error is not None or model is None:
        return {"ok": False, "error": error or "no data", "repos": [], "flat": [],
                "live_unmatched": [], "archived": []}
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
        "archived": model.get("archived", []),
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
/* A collapsible repo section (the DEFAULT grouped view): the whole h2 is the toggle. */
.repo.collapsible > h2{cursor:pointer;user-select:none;display:flex;align-items:baseline;gap:.4rem}
.repo.collapsible > h2 .chev{color:var(--gray);font-size:.75rem;width:.8rem;flex:0 0 auto}
.repo.collapsible > h2 .count{margin-left:0}
.repo-body{margin-top:.3rem}
/* The sticky cross-repo triage bar — filter chips that narrow every group to one state. */
.triage{position:sticky;top:0;z-index:10;display:flex;flex-wrap:wrap;gap:.4rem;
  background:var(--bg);border-bottom:1px solid var(--bg2);padding:.5rem 0;margin:0 0 1rem}
.chip{background:var(--bg1);color:var(--fg2);border:1px solid var(--bg2);border-radius:999px;
  padding:.2rem .75rem;cursor:pointer;font:inherit;font-size:.8rem;white-space:nowrap}
.chip:hover{background:var(--bg2)}
.chip.active{background:var(--blue);color:var(--bg);border-color:var(--blue)}
/* Chip active colours mirror the momentum semantics: needs_you=orange, stalled=gray,
   cooling=yellow, live=green (the overlay). */
.chip.state-needs_you.active{background:var(--orange);border-color:var(--orange)}
.chip.state-stalled.active{background:var(--gray);border-color:var(--gray)}
.chip.state-slowing.active{background:var(--yellow);border-color:var(--yellow)}
.chip.state-live.active{background:var(--green);border-color:var(--green)}
/* Compact, muted glyph legend under the triage bar — decodes the state glyphs + badges for a
   first-time viewer. Secondary text; wraps on a narrow viewport. */
.legend{color:var(--gray);font-size:.74rem;margin:-.4rem 0 1rem;line-height:1.5}
/* The `⚠ risk` cue on a card promoted to needs_you by an ACTIVE RISK (severity), distinct from
   a blocked wait. Small, orange, low-key so it reads as a badge not a headline. */
.risk-cue{font-size:.68rem;color:var(--orange);border:1px solid var(--orange);border-radius:3px;
  padding:0 .25rem;margin-left:.35rem;white-space:nowrap;font-weight:bold}
/* The "match: …snippet…" reason line shown on a visible card while a search query is active. */
.match-reason{margin-top:.25rem;color:var(--gray);font-size:.76rem;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.match-reason .mr-lbl{color:var(--gray);font-weight:bold;margin-right:.1rem}
.match-reason .mr-fuzzy{font-style:italic;color:var(--gray)}
/* An ARMED destructive button (first tap of the two-tap confirm) — highlighted so the pending
   confirm is obvious; reverts on the second tap / blur / timeout. */
.archive-btn.armed{background:var(--orange);color:var(--bg);border-color:var(--orange);font-weight:bold}
.ini{background:var(--bg1);border-left:3px solid var(--gray);border-radius:4px;
  padding:.55rem .7rem;margin:0 0 .5rem;cursor:pointer}
.ini:hover{background:#40393622}
/* Border-left + badge colour keyed off the DERIVED triage state (v2): needs_you=orange,
   stalled=gray, slowing/cooling=yellow, active=blue. `live` is a separate overlay badge. */
.ini.state-needs_you{border-left-color:var(--orange)}
.ini.state-stalled{border-left-color:var(--gray)}
.ini.state-slowing{border-left-color:var(--yellow)}
.ini.state-active{border-left-color:var(--blue)}
.ini.open{outline:1px solid var(--bg2)}
/* Brief highlight when a card is jumped-to from the Live-now strip (focusCard) — fades on its own
   after ~1s so the eye lands on it without a lingering distraction. */
.ini.flash{animation:iniFlash 1s ease-out}
@keyframes iniFlash{0%{background:#504945}100%{background:var(--bg1)}}
.ini .row1{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem}
.badge{font-weight:bold}
.badge.active{color:var(--green)}
.badge.slowing{color:var(--yellow)}
.badge.stalled{color:var(--gray)}
.badge.unknown{color:var(--gray)}
/* The state glyph badge on line 1 (⚠ needs_you / ◑ stalled / ~ slowing / → active). */
.sbadge{font-weight:bold;margin-right:.05rem}
.sbadge.state-needs_you{color:var(--orange)}
.sbadge.state-stalled{color:var(--gray)}
.sbadge.state-slowing{color:var(--yellow)}
.sbadge.state-active{color:var(--blue)}
.slug{font-weight:bold;color:var(--fg)}
.title{color:var(--fg2)}
.repo-label{font-size:.75rem;color:var(--aqua);background:var(--bg2);border-radius:3px;
  padding:.02rem .4rem}
/* The `● live` overlay badge — INDEPENDENT of state (green), shown when an agent is running. */
.live-badge{font-size:.72rem;color:var(--green);font-weight:bold}
/* A session-only (undocumented) card carries a small "emerging" badge in place of the retired
   standalone Emerging lane — it now sits inline in its repo group. */
.emerging-badge{font-size:.7rem;color:var(--yellow);background:var(--bg2);border-radius:3px;
  padding:.02rem .35rem}
.age{color:var(--gray);font-size:.82rem;margin-left:auto}
/* Line 2 of the collapsed card: the single most-relevant line for the card's state. */
.line2{margin-top:.3rem;color:var(--fg2);font-size:.85rem;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.line2.state-needs_you{color:var(--fg)}
.actions{margin-top:.35rem;display:flex;align-items:center;gap:.4rem}
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
/* Phase-3 [resolve] — on needs_you cards; opens the read-only ask sidebar prefilled with the
   blocker question. Yellow tint (matches the sidebar's chat-title) to read as "ask", distinct
   from the aqua dispatch + muted archive buttons. */
.resolve-btn{font:inherit;font-size:.78rem;cursor:pointer;background:var(--bg2);
  color:var(--yellow);border:1px solid var(--bg2);border-radius:3px;padding:.1rem .5rem}
.resolve-btn:hover:not(:disabled){border-color:var(--yellow)}
.resolve-btn:disabled{cursor:default;opacity:.7}
/* Phase-2 card lifecycle actions: [✓ done] (archive) on every card, [drop] on stalled/cooling
   cards, and [↺ unarchive] in the Done view. Muted by default so they don't compete with the
   card content; the done tint is green, drop is red, matching the "done vs abandon" semantics. */
.archive-btn{font:inherit;font-size:.78rem;cursor:pointer;background:var(--bg2);
  color:var(--fg2);border:1px solid var(--bg2);border-radius:3px;padding:.1rem .5rem}
.archive-btn:hover:not(:disabled){border-color:var(--fg2)}
.archive-btn:disabled{cursor:default;opacity:.7}
.archive-btn.done{color:var(--green)}
.archive-btn.done:hover:not(:disabled){border-color:var(--green)}
.archive-btn.drop{color:var(--red)}
.archive-btn.drop:hover:not(:disabled){border-color:var(--red)}
.archive-btn.unarchive{color:var(--aqua)}
.archive-btn.unarchive:hover:not(:disabled){border-color:var(--aqua)}
.archive-status{margin-left:.4rem;color:var(--gray);font-size:.78rem}
.archive-status.err{color:var(--red)}
/* The Done chip (a MODE, not a state filter) + its active tint. */
.chip.state-done.active{background:var(--green);border-color:var(--green)}
/* The Done view: the archived list, grouped by repo like the board. */
.done-head{display:flex;align-items:baseline;gap:.6rem;margin:0 0 1rem}
.done-head .done-title{font-weight:bold;color:var(--fg)}
.done-head .done-stat{color:var(--gray);font-size:.85rem}
.done-card{background:var(--bg1);border-left:3px solid var(--green);border-radius:4px;
  padding:.55rem .7rem;margin:0 0 .5rem}
.done-card .reason{font-size:.7rem;color:var(--fg2);background:var(--bg2);border-radius:3px;
  padding:.02rem .4rem}
.done-card .reason.reason-dropped{color:var(--red)}
.detail{margin-top:.5rem;padding-top:.5rem;border-top:1px dotted var(--bg2)}
.detail-summary{color:var(--fg);font-size:.86rem;margin-bottom:.4rem}
.detail-h{color:var(--orange);font-size:.8rem;margin:.4rem 0 .15rem;text-transform:uppercase;
  letter-spacing:.04em}
.detail-list{margin:.1rem 0 .3rem;padding-left:1.1rem;color:var(--fg2);font-size:.84rem}
.detail-list li{margin:.1rem 0}
.detail-doc{color:var(--gray);font-size:.78rem;margin-top:.35rem;word-break:break-all}
.detail-err{color:var(--red);font-size:.82rem}
.empty{color:var(--gray);padding:2rem 0}
/* The pinned "Live now" strip — currently-running live sessions, matched or not, sorted
   most-recently-active first. Visually PRIMARY (a green-accented top strip), the first thing seen;
   COLLAPSED to the top few rows by default (a "＋N more" toggle expands) so the board stays
   glanceable. Hidden entirely when there are no live sessions. */
.livenow{margin:.4rem 0 1rem;background:var(--bg1);border:1px solid var(--bg2);
  border-left:3px solid var(--green);border-radius:4px;padding:.6rem .8rem}
.livenow > h2{font-size:.92rem;margin:0 0 .5rem;color:var(--fg);
  display:flex;align-items:baseline;gap:.4rem}
.livenow > h2 .ln-dot{color:var(--green);font-weight:bold}
.livenow > h2 .count{color:var(--gray);font-weight:normal;font-size:.82rem}
.livenow-body{display:flex;flex-direction:column;gap:.1rem}
/* A row keeps task + age + repo/slug CLOSE together (task doesn't stretch to the full width and
   shove the repo to the far right); the task truncates with an ellipsis when long. */
.ln-row{display:flex;align-items:baseline;gap:.4rem;padding:.05rem 0;
  font-size:.84rem;white-space:nowrap;overflow:hidden}
.ln-task{color:var(--fg);overflow:hidden;text-overflow:ellipsis;
  flex:0 1 auto;min-width:0;max-width:min(62ch,70%)}
.ln-age{color:var(--gray);flex:0 0 auto;font-size:.76rem}
.ln-meta{display:inline-flex;align-items:baseline;gap:.4rem;flex:0 0 auto}
.ln-repo{color:var(--aqua);flex:0 0 auto;font-size:.78rem}
.ln-slug{color:var(--green);flex:0 0 auto;font-size:.78rem}
/* An unmatched (below-floor / brand-new) live row reads slightly dimmer than a matched one. */
.ln-row.unmatched .ln-task{color:var(--fg2)}
/* A matched row is clickable — it jumps to + opens its card (focusCard). Cursor + hover cue +
   a visible keyboard-focus ring so it reads as an affordance. */
.ln-row.clickable{cursor:pointer;border-radius:3px;margin:0 -.3rem;padding-left:.3rem;padding-right:.3rem}
.ln-row.clickable:hover{background:#40393622}
.ln-row.clickable:hover .ln-task{text-decoration:underline}
.ln-row.clickable:focus{outline:1px solid var(--bg2);outline-offset:1px}
/* The "＋N more ▸" / "▾ show fewer" collapse toggle — a small muted affordance, not a button box. */
.ln-more{margin-top:.35rem;background:none;border:none;color:var(--aqua);cursor:pointer;
  font:inherit;font-size:.78rem;padding:.1rem 0}
.ln-more:hover{color:var(--fg);text-decoration:underline}
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


# PURE, DOM-FREE state-filter predicate for the sticky triage bar — kept in its OWN snippet
# (like _MATCH_JS) so the node test evals it directly, exercising the ACTUAL page code. `sf` is
# the active triage filter: '' or 'all' → every card; 'live' → the LIVE OVERLAY (card has the
# `live` badge, regardless of its state); otherwise the card's derived `state` must equal it
# (needs_you|stalled|slowing|active). Composes with `matchQ` (AND) in render. Substituted into
# _JS at the __STATEFILTER_JS__ placeholder at module load.
_STATEFILTER_JS = r"""
function matchState(v, sf){
  if(!sf || sf === 'all') return true;   // "All" (or no filter) → every card
  if(sf === 'live') return !!v && !!v.live;   // Live filters by the overlay badge, not state
  return !!v && v.state === sf;
}
""".strip()


# PURE, DOM-FREE Phase-2 archive-lifecycle predicates — kept in their OWN snippet (like
# _STATEFILTER_JS) so the node test evals them directly, exercising the ACTUAL page code.
# `dropEligible(v)` gates the `[drop]` action to STALLED + SLOWING(cooling) cards only (every
# card gets `[✓ done]`; only these two also get `[drop]`). `matchArchived(a, q)` is the Done
# view's search predicate (slug/title/reason/repo). Substituted into _JS at the __ARCHIVE_JS__
# placeholder at module load; the page calls the SAME functions so the tests aren't a replica.
_ARCHIVE_JS = r"""
function dropEligible(v){
  return !!v && (v.state === 'stalled' || v.state === 'slowing');
}
function matchArchived(a, q){
  if(!q) return true;
  var hay = ((a && a.slug || '') + ' ' + (a && a.title || '') + ' ' +
             (a && a.reason || '') + ' ' + (a && a.repo_name || '')).toLowerCase();
  return hay.indexOf(q) !== -1;
}
""".strip()


# PURE, DOM-FREE Phase-3 STATE-MATCHED action MAP + the [resolve] ask helpers — kept in their
# OWN snippet (like _ARCHIVE_JS) so the node tests eval them directly, exercising the ACTUAL
# page code (not a replica). Depends on `dropEligible` (from _ARCHIVE_JS, inlined just above),
# so it is substituted AFTER __ARCHIVE_JS__ and node-eval'd as `_ARCHIVE_JS + _ACTIONS_JS`.
#
#   cardActions(v) -> the ordered action descriptors for a card, keyed off its derived `state`
#                     (the explicit state→actions MAP; `?` = only when a grounded rec exists):
#                       needs_you        -> [resolve] [⤴ dispatch?] [✓ done]
#                       stalled/slowing  -> [⤴ resume?] [drop] [✓ done]   (dropEligible)
#                       active (+legacy) -> [⤴ dispatch?] [✓ done]
#                     The dispatch button is the SAME action either way (same /api/dispatch
#                     wiring); only its LABEL flips to '⤴ resume' on stalled/cooling cards.
#   resolveQuestion(slug) -> the grounded, prefilled question a [resolve] click asks the sidebar.
#   askResolve(v)  -> open the EXISTING ask sidebar (openChat), prefill resolveQuestion(v.slug),
#                     and submit it through the EXISTING chat-submit path (submitQuestion) so the
#                     SSE stream + markdown render happen exactly as a normal ask. Rationale: a
#                     block usually needs Zach's DECISION, so [resolve] routes him to the read-only
#                     agent's grounded synthesis of the blocker + resolution path; if it's
#                     agent-fixable he still has [⤴ dispatch]. Pure client-side convenience over
#                     the ask flow — NO new endpoint. (openChat/chatInput/submitQuestion are IIFE
#                     closures defined later; JS hoisting + call-time resolution makes this safe,
#                     and the node test stubs them to assert the composed question hits submit.)
_ACTIONS_JS = r"""
function cardActions(v){
  var st = (v && v.state) || 'active';
  var hasRec = !!(v && v.recommended_next_step && v.recommended_next_step.text);
  if(st === 'needs_you'){
    var a = [{kind:'resolve'}];
    if(hasRec) a.push({kind:'dispatch', label:'⤴ dispatch'});
    a.push({kind:'done'});
    return a;
  }
  if(dropEligible(v)){                     // stalled or slowing (cooling)
    var b = [];
    if(hasRec) b.push({kind:'dispatch', label:'⤴ resume'});
    b.push({kind:'drop'});
    b.push({kind:'done'});
    return b;
  }
  var c = [];                              // active (and any legacy/unknown state)
  if(hasRec) c.push({kind:'dispatch', label:'⤴ dispatch'});
  c.push({kind:'done'});
  return c;
}
function resolveQuestion(slug){
  return "What's blocking " + (slug || '') + " and what should I do to resolve it?";
}
function askResolve(v){
  openChat();                              // reuse the sidebar open path (same as 💬 ask)
  var q = resolveQuestion(v && v.slug);
  chatInput.value = q;                     // prefill the grounded question (textContent-safe input)
  submitQuestion(q);                       // reuse the existing chat-submit (SSE stream + md render)
}
""".strip()


# PURE, DOM-FREE relative-age → "X ago" suffix helper. Kept in its OWN snippet so the node test
# evals it directly (exercising the ACTUAL page code). Guards the "now"/"just now" case so a
# just-synced store reads "just now" instead of the nonsensical "now ago"; a falsy age → the
# neutral "unknown". Substituted into _JS at the __AGO_JS__ placeholder at module load.
_AGO_JS = r"""
function withAgo(age){
  if(!age) return 'unknown';                    // no timestamp → neutral, never "undefined ago"
  if(age === 'now' || age === 'just now') return 'just now';   // <1m sync: "just now", NOT "now ago"
  return age + ' ago';                          // normal case: "5m ago", "3h ago", "2d ago"
}
""".strip()


# PURE-ish, DOM-free "why did this card match the search" helper. Kept in its OWN snippet so the
# node test evals it directly. `matchSnippet(v, q)` returns {fuzzy:false, text:<window>} when the
# (already-lowercased) query is a SUBSTRING of some card field, {fuzzy:true} when the card only
# matched fuzzily (matchQ passed but no field literally contains the query), or null when there
# is no active query. `snippetWindow` centres a ~60-char ellipsized window on the hit. The field
# order mirrors the spec: title → summary → status → opening → next_step → search_text (hidden
# full session text) → latest recent-message. Substituted into _JS at the __SNIPPET_JS__ marker.
_SNIPPET_JS = r"""
function snippetWindow(text, q, width){
  var s = String(text == null ? '' : text);
  var idx = s.toLowerCase().indexOf(q);
  if(idx === -1) return null;                    // no substring hit in this field
  width = width || 60;
  var pad = Math.max(0, Math.floor((width - q.length) / 2));
  var start = Math.max(0, idx - pad);
  var end = Math.min(s.length, idx + q.length + pad);
  var seg = s.slice(start, end).replace(/\s+/g, ' ').trim();   // collapse whitespace for display
  if(start > 0) seg = '…' + seg;                 // ellipsize a clipped left edge
  if(end < s.length) seg = seg + '…';            // …and a clipped right edge
  return seg;
}
function matchSnippet(v, q){
  if(!v || q == null) return null;
  q = String(q).trim().toLowerCase();
  if(!q) return null;                            // only while a query is active
  var latest = '';
  var msgs = (v.recent_messages || []);
  if(msgs.length){ var m = msgs[msgs.length - 1]; latest = (m && m.text) || ''; }
  var fields = [v.title, v.summary, v.status, v.opening_message, v.next_step,
                v.search_text, latest];
  for(var i = 0; i < fields.length; i++){
    var win = snippetWindow(fields[i], q, 60);
    if(win !== null) return {fuzzy: false, text: win};   // first substring hit wins
  }
  return {fuzzy: true};                          // matchQ said yes but no literal substring
}
""".strip()


# PURE, DOM-based two-tap confirm for a destructive button. Kept in its OWN snippet so the node
# test evals it against a DOM-shim button + injectable timers. First click ARMS the button
# (label → `armedLabel`, `.armed` class) and starts a ~`windowMs` disarm timer; a second click
# WHILE armed disarms + runs `onConfirm`; a `blur` (focus leaves — i.e. clicking elsewhere) or
# the timeout disarms back to `restLabel`. Buttons fire `click` on Enter/Space natively, so this
# is keyboard-safe. `onConfirm` runs the EXISTING action (e.g. archiveCard) VERBATIM — nothing
# about /api/archive changes. Substituted into _JS at the __CONFIRM_JS__ placeholder.
_CONFIRM_JS = r"""
function armConfirm(btn, opts){
  opts = opts || {};
  var armedLabel = opts.armedLabel || 'confirm?';
  var restLabel = (opts.restLabel != null) ? opts.restLabel : btn.textContent;
  var windowMs = opts.windowMs || 3000;
  var onConfirm = opts.onConfirm || function(){};
  var setT = (opts.timers && opts.timers.set) ||
             (typeof setTimeout !== 'undefined' ? setTimeout : null);
  var clrT = (opts.timers && opts.timers.clear) ||
             (typeof clearTimeout !== 'undefined' ? clearTimeout : null);
  var armed = false, timer = null;
  function disarm(){
    if(!armed) return;
    armed = false;
    if(timer != null && clrT){ clrT(timer); }
    timer = null;
    btn.textContent = restLabel;
    if(btn.classList) btn.classList.remove('armed');
  }
  function arm(){
    armed = true;
    btn.textContent = armedLabel;
    if(btn.classList) btn.classList.add('armed');
    if(setT) timer = setT(disarm, windowMs);
  }
  btn.addEventListener('click', function(ev){
    if(ev && ev.stopPropagation) ev.stopPropagation();   // never toggle the card expand
    if(armed){ disarm(); onConfirm(ev); }                // second tap → fire the real action
    else { arm(); }                                       // first tap → arm + await confirm
  });
  btn.addEventListener('blur', function(){ disarm(); });  // clicking elsewhere disarms
  return {arm: arm, disarm: disarm, isArmed: function(){ return armed; }};
}
""".strip()


# PURE, DOM-FREE grouping of the flat initiative stream into collapsible repo sections — kept
# in its OWN snippet so the node test evals it directly (no parseLastTouch/DOM dependency). The
# input `views` is ALREADY last_touch-DESC (build_model's `flat`), so: (a) cards within a repo
# are STABLE-sorted by state precedence (needs_you→stalled→slowing→active), recency preserved
# for ties; (b) repos are ordered by their `needs_you` count DESC, then by most-recent activity —
# which is just the index of the repo's FIRST card in the DESC stream (smaller = more recent).
# `undocumented` cards are NOT segregated (the standalone Emerging lane is retired); they group
# with their repo and the SPA badges them. Substituted into _JS at the __GROUP_JS__ placeholder.
_GROUP_JS = r"""
function stateRank(v){
  var order = {needs_you:0, stalled:1, slowing:2, active:3};
  var r = v ? order[v.state] : undefined;
  return (r == null) ? 3 : r;   // unknown/legacy state sorts with 'active'
}
function stateSort(items){
  // Stable sort by state rank (decorate with the original index so equal ranks keep the
  // input's recency order regardless of the engine's Array.sort stability).
  return items.map(function(v, i){ return [stateRank(v), i, v]; })
    .sort(function(a, b){ return (a[0] - b[0]) || (a[1] - b[1]); })
    .map(function(t){ return t[2]; });
}
function groupByRepo(views){
  var groups = {}, order = [];
  (views || []).forEach(function(v, i){
    var name = (v && v.repo_name) || '(unknown repo)';
    if(!groups[name]){ groups[name] = {name:name, items:[], needs:0, firstIdx:i}; order.push(name); }
    groups[name].items.push(v);
    if(v && v.state === 'needs_you') groups[name].needs++;
  });
  var out = order.map(function(n){ return groups[n]; });
  out.forEach(function(g){ g.items = stateSort(g.items); });
  out.sort(function(a, b){
    if(b.needs !== a.needs) return b.needs - a.needs;   // most needs_you first
    return a.firstIdx - b.firstIdx;                     // then most-recent activity (DESC stream)
  });
  return out;
}
""".strip()


# PURE, DOM-FREE "Live now" builder — kept in its OWN snippet (like _GROUP_JS) so the node test
# evals it directly, exercising the ACTUAL page code (not a Python replica). Substituted into _JS
# at the __LIVENOW_JS__ placeholder at module load; `renderLiveNow` (in the main body) renders
# its output.
#
# `buildLiveNow(flat, unmatched, nowMs)` = the UNION of EVERY currently-running live tmux Claude
# session, so the pinned Live-now strip shows them ALL, matched or not:
#   (1) every card's `live_tasks_meta` (matched panes, {task, activity_ts}) — tagged with that
#       card's slug + repo (falls back to the bare `live_tasks` string list on a pre-meta payload), and
#   (2) `live_unmatched` (panes tied to NO card — the below-floor / brand-new sessions) — untagged,
#       each carrying its own `activity_ts`.
# De-duped by (task, repo) so a task shown on a card and echoed as unmatched appears once (the
# matched, slug-tagged row wins because matched rows are pushed FIRST). Each row carries its
# `activity_ts` (epoch seconds, or null) and a relative `age` string (m/h/d/w) computed vs `nowMs`
# (defaults to Date.now()). Sorted by activity_ts DESC (most-recently-active first); a null activity
# sorts LAST; ties break by (repo, matched-first, task) — so freshness leads, then it reads dense +
# scannable like before. Non-array inputs degrade to [] (the section then simply doesn't render).
_LIVENOW_JS = r"""
function liveAgeStr(ts, nowMs){
  // Compact m/h/d/w freshness. Hours are shown up to 48h (NOT rolled to days at 24h) so a
  // genuinely-stale pane reads '34h' rather than a coarse '1d' — the whole point of the age is to
  // make idle-vs-fresh obvious at a glance. null / non-positive → '' (the row shows no age).
  if(ts == null) return '';
  var t = Number(ts);
  if(!isFinite(t) || t <= 0) return '';
  var secs = (nowMs / 1000) - t;
  if(secs < 0) secs = 0;                              // clock skew → clamp to 'now'
  if(secs < 60) return 'now';
  var mins = secs / 60;
  if(mins < 60) return Math.floor(mins) + 'm';
  var hours = mins / 60;
  if(hours < 48) return Math.floor(hours) + 'h';
  var days = hours / 24;
  if(days < 14) return Math.floor(days) + 'd';
  return Math.floor(days / 7) + 'w';
}
function buildLiveNow(flat, unmatched, nowMs){
  if(nowMs == null) nowMs = Date.now();
  var rows = [], seen = {};
  // repoName is the SHORT display label (dedup + the ln-repo pill); repoPath is the FULL repo
  // path — carried so a MATCHED row can rebuild the card's `key()` (repo+'::'+slug) and
  // focusCard can jump to it. Unmatched rows carry it too but have no slug, so aren't clickable.
  function push(task, repoName, repoPath, slug, matched, id, ts){
    var t = String(task == null ? '' : task).trim();
    if(!t) return;                                   // a session with no task text is not a row
    var rn = String(repoName == null ? '' : repoName).trim() || '(unknown repo)';
    var k = JSON.stringify([t.toLowerCase(), rn.toLowerCase()]);
    if(seen[k]) return;                              // same task+repo once (matched wins, pushed first)
    seen[k] = 1;
    var tn = (ts == null || ts === '' || !isFinite(Number(ts))) ? null : Number(ts);
    rows.push({task:t, repo_name:rn, repo:String(repoPath == null ? '' : repoPath),
               slug:String(slug||''), matched:!!matched,
               id:String(id||''), activity_ts:tn, age:liveAgeStr(tn, nowMs)});
  }
  (Array.isArray(flat) ? flat : []).forEach(function(v){
    if(!v) return;
    var meta = Array.isArray(v.live_tasks_meta) ? v.live_tasks_meta : null;
    if(meta){
      meta.forEach(function(m){ if(m) push(m.task, v.repo_name, v.repo, v.slug, true, '', m.activity_ts); });
    } else {                                         // pre-meta payload: strings, no activity
      (Array.isArray(v.live_tasks) ? v.live_tasks : []).forEach(function(t){
        push(t, v.repo_name, v.repo, v.slug, true, '', null);
      });
    }
  });
  (Array.isArray(unmatched) ? unmatched : []).forEach(function(u){
    if(!u) return;
    push(u.title, u.repo_name, u.repo, '', false, u.id, u.activity_ts);
  });
  rows.sort(function(a, b){
    var an = a.activity_ts == null, bn = b.activity_ts == null;
    if(an !== bn) return an ? 1 : -1;                        // null activity sorts LAST
    if(!an && a.activity_ts !== b.activity_ts){
      return b.activity_ts - a.activity_ts;                  // most-recent (larger epoch) first
    }
    var ar = a.repo_name.toLowerCase(), br = b.repo_name.toLowerCase();   // stable tiebreak
    if(ar < br) return -1;
    if(ar > br) return 1;
    if(a.matched !== b.matched) return a.matched ? -1 : 1;   // matched (slug-tagged) first
    var at = a.task.toLowerCase(), bt = b.task.toLowerCase();
    return at < bt ? -1 : (at > bt ? 1 : 0);
  });
  return rows;
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
  __STATEFILTER_JS__
  __ARCHIVE_JS__
  __ACTIONS_JS__
  __AGO_JS__
  __SNIPPET_JS__
  __CONFIRM_JS__
  __GROUP_JS__
  __LIVENOW_JS__
  __MATCH_JS__
  __MARKDOWN_JS__
  var el0 = document.getElementById('idata');
  var data;
  try { data = JSON.parse(el0.textContent); }
  catch(e){ data = {ok:false, error:'bad payload', repos:[], flat:[], live_unmatched:[]}; }

  // v3 storage key: bumped when the DEFAULT view flipped recency→grouped (Phase-1 board
  // redesign). A browser that persisted an OLD default under the v1/v2 key reads NOTHING under
  // the v3 key, so it starts fresh on the new 'grouped' default instead of being pinned to a
  // stale 'flat'/'recency'. An explicit later choice is still remembered under the v3 key.
  var VIEW_KEY = 'initiatives-view-v3';
  // Per-repo collapse state for the grouped view: a JSON map {repoName: 1} of COLLAPSED repos
  // (absent = expanded, the default). A search/triage filter can force a section open WITHOUT
  // touching this stored preference.
  var REPO_COLLAPSE_KEY = 'initiatives-repo-collapsed';
  // 3-way view toggle: 'grouped' (by repo, DEFAULT) | 'flat' | 'recency' (by last_touch).
  // Persisted in localStorage; an unknown/legacy value falls back to the grouped default.
  var VALID_VIEWS = {flat:1, grouped:1, recency:1};
  var storedView = localStorage.getItem(VIEW_KEY);
  // `doneMode` is the Phase-2 "Done" view MODE (the [✓ Done N] chip) — NOT a triage state; it
  // hides the board and renders the archived list instead. Not persisted (a per-visit view).
  var state = { view: VALID_VIEWS[storedView] ? storedView : 'grouped', q: '', triage: '',
                doneMode: false };
  // Header stats (recomputed each render). `emergingTotal` is the "N emerging" stat; `stateCounts`
  // drives the summary header + the triage chips — the four states are MUTUALLY EXCLUSIVE, plus
  // `live` (the overlay badge) which OVERLAPS them.
  var emergingTotal = 0;
  var stateCounts = {needs_you:0, stalled:0, slowing:0, active:0, live:0};
  var expanded = {};     // key -> true
  var detailCache = {};  // key -> detail payload

  // Per-repo collapse persistence (grouped view). Default expanded; a filter can force-open.
  function readRepoCollapsed(){
    try { return JSON.parse(localStorage.getItem(REPO_COLLAPSE_KEY) || '{}') || {}; }
    catch(e){ return {}; }
  }
  function isRepoCollapsed(name){ return !!readRepoCollapsed()[name]; }
  function setRepoCollapsed(name, collapsed){
    var m = readRepoCollapsed();
    if(collapsed) m[name] = 1; else delete m[name];
    try { localStorage.setItem(REPO_COLLAPSE_KEY, JSON.stringify(m)); } catch(e){}
  }

  // The derived-state glyphs/labels for the two-line card + the triage chips (mirrors the Python
  // STATE_* precedence + the momentum colours the owner knows). ⚠ needs_you (orange) · ◑ stalled
  // (gray) · ~ slowing/"cooling" (yellow) · → active (blue/green). `● live` is the SEPARATE green
  // overlay badge, not a state.
  var STATE_GLYPH = {needs_you:'⚠', stalled:'◑', slowing:'~', active:'→'};
  var STATE_LABEL = {needs_you:'needs you', stalled:'stalled', slowing:'cooling', active:'active'};
  var LIVE_GLYPH = '●';

  var app = document.getElementById('app');
  var liveNowEl = document.getElementById('livenow');
  var triageBar = document.getElementById('triage');
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

  // POST /api/archive {repo, slug, reason} → hide+remember the card (done / drop). On success
  // the card is removed from the DOM for instant feedback and the board is refetched so the
  // counts, triage chips, and Done-chip count reconcile (the server now suppresses it). The
  // write is server-side (mailbox creds); the card resurfaces automatically on new activity.
  function archiveCard(v, btn, stat, reason, cardEl){
    btn.disabled = true;
    stat.className = 'archive-status';
    stat.textContent = reason === 'dropped' ? 'dropping…' : 'archiving…';
    fetch('/api/archive', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({repo: v.repo, slug: v.slug, reason: reason})
    }).then(function(r){ return r.json().then(function(j){ return {ok: r.ok, j: j}; }); })
      .then(function(res){
        if(res.ok && res.j && res.j.ok){
          if(cardEl && cardEl.parentNode) cardEl.parentNode.removeChild(cardEl);
          refetch();  // re-read → counts/chips/Done-count reconcile (card now suppressed)
        } else {
          stat.className = 'archive-status err';
          stat.textContent = (res.j && res.j.error) || 'archive failed';
          btn.disabled = false;  // allow a retry on failure
        }
      })
      .catch(function(){
        stat.className = 'archive-status err';
        stat.textContent = 'archive failed (network)';
        btn.disabled = false;
      });
  }

  // POST /api/unarchive {repo, slug} → restore an archived initiative to the board (the Done
  // view's [↺ unarchive]). On success remove the row from the Done view + refetch so it
  // reappears on the board and the Done-chip count drops.
  function unarchiveCard(a, btn, stat, rowEl){
    btn.disabled = true;
    stat.className = 'archive-status';
    stat.textContent = 'unarchiving…';
    fetch('/api/unarchive', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({repo: a.repo, slug: a.slug})
    }).then(function(r){ return r.json().then(function(j){ return {ok: r.ok, j: j}; }); })
      .then(function(res){
        if(res.ok && res.j && res.j.ok){
          if(rowEl && rowEl.parentNode) rowEl.parentNode.removeChild(rowEl);
          refetch();
        } else {
          stat.className = 'archive-status err';
          stat.textContent = (res.j && res.j.error) || 'unarchive failed';
          btn.disabled = false;
        }
      })
      .catch(function(){
        stat.className = 'archive-status err';
        stat.textContent = 'unarchive failed (network)';
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
    // The thread's ORIGIN prompt (start ›) + the latest substantive prompt (you ›) — moved OFF
    // the collapsed two-line card into the expanded region (Phase-1), nothing lost. Sourced
    // from the view (`v`); the live handoff read never refreshes them. textContent-only.
    var dopen = (v && v.opening_message) || '';
    if(dopen){
      var op = el('div', 'start');
      op.appendChild(el('span', 'lbl', 'start ›'));
      op.appendChild(document.createTextNode(' ' + dopen));
      det.appendChild(op);
    }
    var dface = v && v.face_message;
    if(dface && dface.text){
      var fm = el('div', 'msg');
      fm.appendChild(el('span', 'lbl', 'you ›'));
      fm.appendChild(document.createTextNode(' ' + dface.text));
      det.appendChild(fm);
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

  // Jump to + OPEN a card from elsewhere on the page (the Live-now strip). Every live session is
  // now a first-class card, so a matched Live-now row can focus its card. `repo`+`slug` come from
  // the row; we rebuild the SAME `key()` the cards index on (repo+'::'+slug), locate the card by
  // its `data-key`, expand its grouped repo-section if collapsed, scroll it into view, and open
  // its detail via the EXACT expand path card()'s click uses (expanded[k]=true; show .detail;
  // loadDetail(v, det)) — reused verbatim, never reimplemented. A brief `.flash` lands the eye.
  // Returns true iff a card was found (no-op + false otherwise — e.g. the two untitled sessions).
  function focusCard(repo, slug){
    var k = key({repo: repo, slug: slug});
    // The real view object (so loadDetail/renderDetail get the SAME v the card was built from).
    var v = (data.flat || []).filter(function(x){ return key(x) === k; })[0];
    if(!v) return false;   // no card exists for this session at all — nothing to focus
    var find = function(){
      var cards = document.querySelectorAll ? document.querySelectorAll('.ini') : [];
      for(var i = 0; i < cards.length; i++){
        if(cards[i].getAttribute && cards[i].getAttribute('data-key') === k) return cards[i];
      }
      return null;
    };
    var target = find();
    if(!target){
      // The card exists in the data but is HIDDEN by an active triage filter / Done mode / search —
      // a clickable row must never silently no-op, so clear those, re-render, and retry.
      state.doneMode = false; state.triage = ''; state.q = '';
      if(typeof searchInput !== 'undefined' && searchInput){ searchInput.value = ''; }
      if(typeof render === 'function'){ render(); }
      target = find();
      if(!target) return false;
    }
    // If the card sits in a COLLAPSED grouped repo-section, expand that section first so the card
    // is visible (the card element always exists — only the body's display is toggled).
    var body = target.closest ? target.closest('.repo-body') : null;
    if(body && body.style && body.style.display === 'none'){
      body.style.display = 'block';
      var sec = body.closest ? body.closest('.repo.collapsible') : null;
      var chev = sec && sec.querySelector ? sec.querySelector('.chev') : null;
      if(chev) chev.textContent = '▾';
      // Persist the section as expanded (mirror the header-click) so a re-render keeps it open.
      if(sec){
        var h2 = sec.querySelector ? sec.querySelector('h2') : null;
        var nm = h2 && h2.dataset ? h2.dataset.repo : null;
        if(nm) setRepoCollapsed(nm, false);
      }
    }
    // Open the card detail via the SAME expand path the card click uses.
    var det = target.querySelector ? target.querySelector('.detail') : null;
    if(det && !expanded[k]){
      expanded[k] = true;
      det.style.display = 'block';
      target.classList.add('open');
      loadDetail(v || {repo: repo, slug: slug}, det);
    }
    if(target.scrollIntoView) target.scrollIntoView({behavior: 'smooth', block: 'center'});
    // Brief highlight so the eye lands on the jumped-to card (~1s), self-clearing.
    target.classList.add('flash');
    setTimeout(function(){ target.classList.remove('flash'); }, 1000);
    return true;
  }

  // A TWO-LINE collapsed card (Phase-1 board redesign). Line 1 = state glyph + slug + title +
  // repo label (non-grouped views) + emerging badge + age. Line 2 = the single most-relevant
  // `v.line2` for the card's state, plus the Phase-1 action (the EXISTING dispatch, only when a
  // grounded recommendation exists). Everything else — the identity/current/start/you/live
  // lines, next-steps, investigations, PRs, commits, docs — moves to the click-to-expand detail
  // (loadDetail/renderDetail), nothing lost. Untrusted text is textContent-only via el().
  function card(v){
    var k = key(v);
    var st = v.state || 'active';
    var c = el('div', 'ini state-' + st + (v.live ? ' is-live' : ''));
    c.setAttribute('data-key', k);

    // LINE 1: state glyph + slug + title + repo label + emerging badge + `● live` overlay + age.
    var row1 = el('div', 'row1');
    var sb = el('span', 'sbadge state-' + st, STATE_GLYPH[st] || '→');
    // Glyph tooltip = the state label, plus WHY a needs_you card needs you (blocked wait vs an
    // active risk promoted by SEVERITY_MARKERS) so the cue is discoverable on hover.
    sb.title = (STATE_LABEL[st] || st) +
      (v.needs_reason === 'severity' ? ' · active risk' :
       (v.needs_reason === 'blocked' ? ' · blocked on you' : ''));
    row1.appendChild(sb);
    row1.appendChild(el('span', 'slug', v.slug));
    if(v.title) row1.appendChild(el('span', 'title', v.title));
    // SEVERITY cue: a card promoted to needs_you by an ACTIVE RISK (not a wait) carries a small
    // `⚠ risk` chip, distinct from the blocked line, so the reason reads at a glance.
    if(v.needs_reason === 'severity'){
      var rc = el('span', 'risk-cue', '⚠ risk');
      rc.title = 'active, unresolved risk detected in this card’s status/next-step — surfaced for your attention';
      row1.appendChild(rc);
    }
    // The repo label shows whenever repo isn't the section header — i.e. flat AND recency; the
    // grouped (default) view uses the repo as its collapsible section heading instead.
    if(state.view !== 'grouped' && v.repo_name)
      row1.appendChild(el('span', 'repo-label', v.repo_name));
    // The `● live` overlay badge — INDEPENDENT of state (an agent is running on this initiative
    // right now), so a needs_you/stalled/active card all show it when live.
    if(v.live){
      var lb = el('span', 'live-badge', LIVE_GLYPH + ' live');
      lb.title = 'a live agent session is running on this initiative';
      row1.appendChild(lb);
    }
    // A session-only (undocumented) card is badged inline (the standalone Emerging lane is
    // retired); it groups with its repo like any other card.
    if(v.undocumented){
      var eb = el('span', 'emerging-badge', 'emerging');
      eb.title = 'session-only — discovered from live sessions/telemetry, no handoff doc yet';
      row1.appendChild(eb);
    }
    row1.appendChild(el('span', 'age', v.age));
    c.appendChild(row1);

    // LINE 2: the single most-relevant line for this state (server-derived `v.line2`).
    if(v.line2){
      var l2 = el('div', 'line2 state-' + st);
      l2.appendChild(document.createTextNode(v.line2));
      c.appendChild(l2);
    }

    // SEARCH MATCH REASON — while a query is active, show WHY this (visible) card matched: a
    // ~60-char window around the first substring hit (matchSnippet scans title/summary/status/
    // opening/next-step/hidden-session-text/latest-message), else "(fuzzy match)" when matchQ
    // passed but nothing literally contains the query. Best-effort, textContent-only, query-gated.
    var mq = state.q.trim().toLowerCase();
    if(mq){
      var ms = matchSnippet(v, mq);
      var mr = el('div', 'match-reason');
      mr.appendChild(el('span', 'mr-lbl', 'match: '));
      if(ms && !ms.fuzzy){ mr.appendChild(document.createTextNode(ms.text)); }
      else { mr.appendChild(el('span', 'mr-fuzzy', '(fuzzy match)')); }
      c.appendChild(mr);
    }

    // ACTIONS row — Phase 3: STATE-MATCHED, driven by the pure `cardActions(v)` map (node-tested):
    //   needs_you       -> [resolve] [⤴ dispatch?] [✓ done]
    //   stalled/slowing -> [⤴ resume?] [drop] [✓ done]   (resume = dispatch, resume-framed body)
    //   active          -> [⤴ dispatch?] [✓ done]        ("?" = only when a grounded rec exists)
    // Every button: textContent-only label + stopPropagation (never toggles expand). The
    // dispatch/resume + done/drop wiring reuses dispatchNextStep + archiveCard (POST
    // /api/dispatch + /api/archive) VERBATIM — only the dispatch LABEL flips by state; [resolve]
    // opens + submits the EXISTING ask sidebar (no new endpoint). in-flight/error UX lives on
    // the reused handlers (dispatch/archive) and the sidebar (resolve).
    var drow = el('div', 'actions');
    var astat = el('span', 'archive-status');   // shared inline status for the done/drop buttons
    var rec = v.recommended_next_step;
    cardActions(v).forEach(function(a){
      if(a.kind === 'resolve'){
        // [resolve] — route Zach to the read-only agent's grounded synthesis of the blocker +
        // resolution path (open+prefill+submit the EXISTING ask sidebar). If it's agent-fixable
        // he still has [⤴ dispatch]. The sidebar owns the in-flight/error UX for this action.
        var rbtn = el('button', 'resolve-btn', 'resolve');
        rbtn.title = 'ask the read-only agent what is blocking this and how to resolve it';
        rbtn.addEventListener('click', function(ev){
          ev.stopPropagation();  // don't toggle the card expand
          askResolve(v);
        });
        drow.appendChild(rbtn);
      } else if(a.kind === 'dispatch'){
        // [⤴ dispatch] (active/needs_you) / [⤴ resume] (stalled/cooling) — SAME endpoint + wiring;
        // only the label (a.label) + the server-side task-body framing (dispatch.py) vary.
        var btn = el('button', 'dispatch-btn', a.label);
        btn.title = rec.text + (basisHint(rec.basis) ? '  (' + basisHint(rec.basis) + ')' : '');
        var dstat = el('span', 'dispatch-status');
        btn.addEventListener('click', function(ev){
          ev.stopPropagation();  // don't toggle the card expand
          dispatchNextStep(v, btn, dstat);
        });
        drow.appendChild(btn);
        drow.appendChild(dstat);
      } else if(a.kind === 'done'){
        // [✓ done] — archive (done for now; resurfaces on new activity). DESTRUCTIVE (it sits
        // next to [resume]), so it's TWO-TAP: first click arms ('done?'), a second within ~3s
        // fires; blur/timeout disarms. armConfirm owns the arm/confirm; the action is unchanged.
        var doneBtn = el('button', 'archive-btn done', '✓ done');
        doneBtn.title = 'archive — done for now (reappears if this initiative sees new activity). Click twice to confirm.';
        armConfirm(doneBtn, {armedLabel: 'done?', restLabel: '✓ done',
          onConfirm: function(){ archiveCard(v, doneBtn, astat, 'done', c); }});
        drow.appendChild(doneBtn);
      } else if(a.kind === 'drop'){
        // [drop] — archive as dropped (stalled + cooling only, via cardActions/dropEligible).
        // Also DESTRUCTIVE + adjacent to [resume] → TWO-TAP confirm (arms to 'drop?').
        var dropBtn = el('button', 'archive-btn drop', 'drop');
        dropBtn.title = 'drop — archive this initiative as dropped. Click twice to confirm.';
        armConfirm(dropBtn, {armedLabel: 'drop?', restLabel: 'drop',
          onConfirm: function(){ archiveCard(v, dropBtn, astat, 'dropped', c); }});
        drow.appendChild(dropBtn);
      }
    });
    drow.appendChild(astat);
    c.appendChild(drow);

    // Click the card → expand to today's FULL detail (loadDetail fetches /api/initiative).
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

  // The pinned "Live now" strip. Rendered into #livenow (ABOVE the board, NOT into `app`, so it
  // survives app.innerHTML resets and is the first thing seen). Shows currently-running live tmux
  // Claude sessions — matched to a card (slug-tagged) OR below-floor / unmatched — via buildLiveNow's
  // union+dedup, sorted most-recently-active first with a per-row freshness age. To keep the board
  // GLANCEABLE (the triage chips + first cards must sit near the top, not below a 30-row wall) it
  // COLLAPSES to the top LIVE_PREVIEW_N by default; a "＋(N−6) more ▸" toggle expands to the full
  // sorted list and back. The expand state persists in localStorage (LIVENOW_KEY), default COLLAPSED.
  // Header stays "● Live now (N)" with N = the TOTAL live count. Empty (no live panes) → the strip
  // hides. All untrusted text via textContent (el()), never innerHTML.
  var LIVE_PREVIEW_N = 6;
  var LIVENOW_KEY = 'initiatives-livenow-expanded';
  function liveNowExpanded(){
    try { return localStorage.getItem(LIVENOW_KEY) === '1'; } catch(e){ return false; }
  }
  function setLiveNowExpanded(v){
    try { if(v) localStorage.setItem(LIVENOW_KEY, '1'); else localStorage.removeItem(LIVENOW_KEY); }
    catch(e){}
  }
  function renderLiveNow(){
    if(!liveNowEl) return;
    liveNowEl.innerHTML = '';
    var rows = buildLiveNow(data.flat || [], data.live_unmatched || []);
    if(!rows.length){ liveNowEl.style.display = 'none'; return; }
    liveNowEl.style.display = 'block';
    var total = rows.length;
    var extra = total - LIVE_PREVIEW_N;
    var expanded = liveNowExpanded();
    var shown = (expanded || extra <= 0) ? rows : rows.slice(0, LIVE_PREVIEW_N);
    var h = el('h2');
    h.appendChild(el('span', 'ln-dot', LIVE_GLYPH));
    h.appendChild(document.createTextNode(' Live now'));
    h.appendChild(el('span', 'count', '(' + total + ')'));   // N = TOTAL live, not the shown slice
    liveNowEl.appendChild(h);
    var body = el('div', 'livenow-body');
    shown.forEach(function(r){
      // A MATCHED row (carries slug + the full repo path) is a clickable affordance: every live
      // session is a first-class card now, so clicking the row jumps to + opens that card via
      // focusCard(repo, slug) — reusing the cards' `key()` + expand path. Unmatched rows (no slug
      // → no card to open, e.g. an untitled session) stay non-clickable + muted.
      var clickable = !!(r.matched && r.slug);
      var row = el('div', 'ln-row' + (r.matched ? '' : ' unmatched') + (clickable ? ' clickable' : ''));
      if(clickable){
        row.setAttribute('role', 'button');
        row.setAttribute('tabindex', '0');
        row.title = 'open this session’s card';
        (function(repo, slug){
          function go(){ focusCard(repo, slug); }
          row.addEventListener('click', go);
          row.addEventListener('keydown', function(ev){
            if(ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar'){ ev.preventDefault(); go(); }
          });
        })(r.repo, r.slug);
      }
      row.appendChild(el('span', 'ln-task', r.task));
      if(r.age){ row.appendChild(el('span', 'ln-age', '· ' + r.age)); }   // small muted freshness
      // repo + (matched) slug grouped close to the task, not pushed to the far right. The cryptic
      // tmux session codename (r.id) is DROPPED — repo + task is the signal; the id is low-value.
      var meta = el('span', 'ln-meta');
      meta.appendChild(el('span', 'ln-repo', r.repo_name));
      if(r.slug){ meta.appendChild(el('span', 'ln-slug', r.slug)); }   // matched → its initiative
      row.appendChild(meta);
      body.appendChild(row);
    });
    liveNowEl.appendChild(body);
    if(extra > 0){
      var more = el('button', 'ln-more');
      more.type = 'button';
      more.textContent = expanded ? '▾ show fewer' : ('＋' + extra + ' more ▸');
      more.addEventListener('click', function(){
        setLiveNowExpanded(!expanded);
        renderLiveNow();
      });
      liveNowEl.appendChild(more);
    }
  }

  // The sticky cross-repo triage bar: [⚠ Needs you N] [◑ Stalled N] [~ Cooling N] [ All ]. The
  // old [● Live N] chip is RETIRED — the pinned "Live now" strip above the board is the first-class
  // home for live sessions now, so a redundant triage chip only fragmented the view. Needs-you/
  // Stalled/Cooling filter by `state`; "All" clears. Each composes (AND) with search. Counts come
  // from `stateCounts` (the full data set). Rebuilt each render so counts + the active highlight
  // stay live. No untrusted text.
  function renderTriage(){
    if(!triageBar) return;
    triageBar.innerHTML = '';
    var active = state.triage || '';
    var chips = [
      {k:'needs_you', glyph:STATE_GLYPH.needs_you, label:'Needs you', n:stateCounts.needs_you},
      {k:'stalled',   glyph:STATE_GLYPH.stalled,   label:'Stalled',   n:stateCounts.stalled},
      {k:'slowing',   glyph:STATE_GLYPH.slowing,   label:'Cooling',   n:stateCounts.slowing},
      {k:'',          glyph:'',                    label:'All',       n:null}
    ];
    chips.forEach(function(ch){
      // A state/All chip is only visually active when NOT in Done mode (Done takes over).
      var on = !state.doneMode &&
        ((ch.k === '') ? (active === '' || active === 'all') : (active === ch.k));
      var b = el('button', 'chip state-' + (ch.k || 'all') + (on ? ' active' : ''), null);
      b.type = 'button';
      var txt = (ch.glyph ? ch.glyph + ' ' : '') + ch.label + (ch.n != null ? ' ' + ch.n : '');
      b.textContent = txt;
      b.addEventListener('click', function(){
        state.doneMode = false;   // any board chip exits the Done view
        state.triage = ch.k;
        render();
      });
      triageBar.appendChild(b);
    });
    // The [✓ Done N] chip — a MODE (not a state filter): it hides the board and renders the
    // archived list. N = the archived count. No untrusted text.
    var archivedN = (data.archived || []).length;
    var donB = el('button', 'chip state-done' + (state.doneMode ? ' active' : ''), null);
    donB.type = 'button';
    donB.textContent = '✓ Done ' + archivedN;
    donB.addEventListener('click', function(){
      state.doneMode = true;
      render();
    });
    triageBar.appendChild(donB);
  }

  // The Done view — the archived initiatives (from data.archived, server-sorted newest-first),
  // grouped by repo like the board. Each row shows title + slug + reason + "archived Xd ago"
  // and an [↺ unarchive] button. Search (matchArchived) composes. All untrusted text via
  // textContent (el()). Unarchiving removes the row and refetches (it reappears on the board).
  function renderDoneView(q){
    var allArch = data.archived || [];
    var rows = allArch.filter(function(a){ return matchArchived(a, q); });
    var head = el('div', 'done-head');
    head.appendChild(el('span', 'done-title', 'Done · archived'));
    head.appendChild(el('span', 'done-stat', allArch.length + ' archived'));
    app.appendChild(head);
    if(!rows.length){
      app.appendChild(el('div', 'empty',
        allArch.length ? 'No archived initiatives match the filter.' : 'Nothing archived yet.'));
      updateSearchCount(0, allArch.length, !!q);
      return;
    }
    // Group by repo, preserving the server's newest-first order (first-seen repo = most recent).
    var groups = {}, order = [];
    rows.forEach(function(a){
      var name = a.repo_name || '(unknown repo)';
      if(!groups[name]){ groups[name] = []; order.push(name); }
      groups[name].push(a);
    });
    order.forEach(function(name){
      var sec = el('section', 'repo');
      var h = el('h2', null, name);
      h.appendChild(el('span', 'count', '(' + groups[name].length + ')'));
      sec.appendChild(h);
      groups[name].forEach(function(a){
        var rowEl = el('div', 'done-card');
        var r1 = el('div', 'row1');
        r1.appendChild(el('span', 'slug', a.slug || '(no slug)'));
        if(a.title) r1.appendChild(el('span', 'title', a.title));
        if(a.reason) r1.appendChild(el('span', 'reason reason-' + a.reason, a.reason));
        r1.appendChild(el('span', 'age', (a.archived_age ? withAgo(a.archived_age) : '—')));
        rowEl.appendChild(r1);
        var act = el('div', 'actions');
        var ub = el('button', 'archive-btn unarchive', '↺ unarchive');
        var ustat = el('span', 'archive-status');
        ub.addEventListener('click', function(ev){
          ev.stopPropagation();
          unarchiveCard(a, ub, ustat, rowEl);
        });
        act.appendChild(ub);
        act.appendChild(ustat);
        rowEl.appendChild(act);
        sec.appendChild(rowEl);
      });
      app.appendChild(sec);
    });
    updateSearchCount(rows.length, allArch.length, !!q);
  }

  function updateChrome(){
    btnFlat.classList.toggle('active', state.view === 'flat');
    btnGrouped.classList.toggle('active', state.view === 'grouped');
    if(btnRecency) btnRecency.classList.toggle('active', state.view === 'recency');
    if(countEl){
      // Summary counts: N need you · N stalled · N cooling · N active. The four states are
      // mutually exclusive. The "N emerging" stat + the "N live now" pane count (the same union
      // the pinned Live-now strip shows — EVERY running session, matched or not) ride along; the
      // old "N live" (card-badge) / "N untracked" split is folded into the single "live now".
      var txt = stateCounts.needs_you + ' need you · ' + stateCounts.stalled + ' stalled · ' +
                stateCounts.slowing + ' cooling · ' + stateCounts.active + ' active';
      if(emergingTotal) txt += ' · ' + emergingTotal + ' emerging';
      var lnN = buildLiveNow(data.flat || [], data.live_unmatched || []).length;
      if(lnN) txt += ' · ' + lnN + ' live now';
      countEl.textContent = txt;
    }
    footer.innerHTML = '';
    footer.appendChild(el('span', 'live', 'live sessions ● realtime'));
    // withAgo guards the "now" case so a just-synced store reads "store synced just now",
    // NOT the nonsensical "store synced now ago"; a missing age → "unknown".
    footer.appendChild(document.createTextNode(' · store synced ' + withAgo(data.captured_age) +
      ' · click a card to expand'));
  }

  function render(){
    app.innerHTML = '';
    // The pinned "Live now" strip is refreshed FIRST and lives OUTSIDE `app`, so it stays at the
    // very top, always expanded, regardless of the board view / Done mode / an error state.
    renderLiveNow();
    if(!data.ok){
      emergingTotal = 0;
      stateCounts = {needs_you:0, stalled:0, slowing:0, active:0, live:0};
      renderTriage();
      app.appendChild(el('div', 'err', 'store unreachable: ' + (data.error || '')));
      updateChrome();
      return;
    }
    var q = state.q.trim().toLowerCase();
    var sf = state.triage || '';
    var all = data.flat || [];   // every initiative (documented + emerging inline)

    // State counts over the FULL set — drive the summary header + the triage chips. The four
    // states are mutually exclusive; `live` OVERLAPS them (counted by the overlay badge).
    var counts = {needs_you:0, stalled:0, slowing:0, active:0, live:0};
    all.forEach(function(v){
      if(counts[v.state] != null) counts[v.state]++;
      if(v && v.live) counts.live++;
    });
    stateCounts = counts;
    emergingTotal = all.filter(function(v){ return v && v.undocumented; }).length;

    renderTriage();

    // Done MODE: hide the board, render the archived list instead (search still composes).
    if(state.doneMode){ renderDoneView(q); updateChrome(); return; }

    // A card is visible iff it matches BOTH the search query AND the triage state (compose).
    function visible(v){ return matchQ(v, q) && matchState(v, sf); }
    var filtering = !!(q || sf);
    var shown = 0;

    if(state.view === 'flat'){
      var wrap = el('div', 'flat');
      all.forEach(function(v){ if(visible(v)){ wrap.appendChild(card(v)); shown++; } });
      app.appendChild(wrap);
    } else if(state.view === 'recency'){
      // Bucket the (filtered) flat stream into rolling now-relative recency windows. `all`
      // inherits data.flat's last_touch-DESC order and bucketizeRecency preserves it.
      var rviews = all.filter(visible);
      bucketizeRecency(rviews, Date.now()).forEach(function(g){
        var sec = el('section', 'repo');
        var h = el('h2', null, g.label);
        h.appendChild(el('span', 'count', String(g.items.length)));
        sec.appendChild(h);
        g.items.forEach(function(v){ sec.appendChild(card(v)); shown++; });
        app.appendChild(sec);
      });
    } else {
      // GROUPED (default): collapsible repo sections, ordered by needs_you-count then recency,
      // cards within a repo by state precedence. Under an active filter a section with matches
      // AUTO-EXPANDS (not persisted) and one with zero matches HIDES entirely.
      groupByRepo(all).forEach(function(g){
        var vis = g.items.filter(visible);
        if(!vis.length) return;   // no matches under the filter → hide the section
        var collapsed = isRepoCollapsed(g.name);
        var open = !collapsed || (filtering && vis.length > 0);
        var sec = el('section', 'repo collapsible');
        var h = el('h2');
        // data-repo lets focusCard re-persist this section as expanded when it jumps into it.
        if(h.dataset) h.dataset.repo = g.name; else h.setAttribute('data-repo', g.name);
        h.appendChild(el('span', 'chev', open ? '▾' : '▸'));
        h.appendChild(document.createTextNode(g.name));
        h.appendChild(el('span', 'count', '(' + vis.length + ')'));
        sec.appendChild(h);
        var body = el('div', 'repo-body');
        body.style.display = open ? 'block' : 'none';
        vis.forEach(function(v){ body.appendChild(card(v)); shown++; });
        sec.appendChild(body);
        h.addEventListener('click', function(){
          var c = !isRepoCollapsed(g.name);   // flip the STORED preference
          setRepoCollapsed(g.name, c);
          body.style.display = c ? 'none' : 'block';
          h.querySelector('.chev').textContent = c ? '▸' : '▾';
        });
        app.appendChild(sec);
      });
    }

    var totalCards = all.length;
    if(shown === 0 && !filtering){
      app.appendChild(el('div', 'empty', 'No initiatives in the latest snapshot.'));
    } else if(shown === 0){
      app.appendChild(el('div', 'empty', 'No initiatives match the current filter.'));
    }
    updateSearchCount(shown, totalCards, filtering);
    // Live sessions are shown by the pinned "Live now" strip at the TOP (renderLiveNow, called at
    // the head of render) — no second below-the-board catch-all section any more.
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
    state.doneMode = false; state.view = 'flat'; localStorage.setItem(VIEW_KEY, 'flat'); render();
  });
  btnGrouped.addEventListener('click', function(){
    state.doneMode = false; state.view = 'grouped';
    localStorage.setItem(VIEW_KEY, 'grouped'); render();
  });
  if(btnRecency) btnRecency.addEventListener('click', function(){
    state.doneMode = false; state.view = 'recency';
    localStorage.setItem(VIEW_KEY, 'recency'); render();
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
_JS = _JS.replace("__STATEFILTER_JS__", _STATEFILTER_JS)
_JS = _JS.replace("__ARCHIVE_JS__", _ARCHIVE_JS)
_JS = _JS.replace("__ACTIONS_JS__", _ACTIONS_JS)
_JS = _JS.replace("__AGO_JS__", _AGO_JS)
_JS = _JS.replace("__SNIPPET_JS__", _SNIPPET_JS)
_JS = _JS.replace("__CONFIRM_JS__", _CONFIRM_JS)
_JS = _JS.replace("__GROUP_JS__", _GROUP_JS)
_JS = _JS.replace("__LIVENOW_JS__", _LIVENOW_JS)
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
    # The pinned, ALWAYS-EXPANDED "Live now (N)" strip — every currently-running live tmux Claude
    # session, matched to a card or not. First thing seen (above the triage bar); populated
    # client-side by renderLiveNow from the JSON island (flat + live_unmatched).
    livenow = '<section id="livenow" class="livenow" aria-label="live sessions"></section>'
    # The sticky cross-repo triage bar (chips populated client-side from the live state counts).
    triage = '<nav id="triage" class="triage" aria-label="triage filters"></nav>'
    # A compact, muted legend decoding the state glyphs + badges for a first-time viewer. Static
    # server-rendered text (no untrusted data) — sits just under the triage bar.
    legend = (
        '<div id="legend" class="legend" aria-label="glyph legend">'
        '⚠ needs you · → active · ~ cooling · ◑ stalled · ● live · ✓ done'
        '</div>'
    )
    return (
        head + header + livenow + triage + legend +
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
                loaded = self._loader()
                # load_latest returns `(rows, archived)`; a legacy/injected loader may return
                # just `rows` (archived → [], nothing suppressed). Normalize either shape.
                if isinstance(loaded, tuple):
                    rows, archived = loaded
                else:
                    rows, archived = loaded, []
                # best-effort; mutates rows in place AND returns the live claude panes that
                # matched no initiative (build_model coerces a non-list to [] → no section).
                unmatched = self._tmux(rows)
                result: tuple[dict | None, str | None] = (
                    build_model(rows, self._now(), unmatched=unmatched, archived=archived),
                    None)
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
                  asker=None, dispatcher=None, archiver=None,
                  unarchiver=None) -> tuple[int, str, bytes]:
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

    if method == "POST" and path == "/api/archive":
        # ARCHIVE (hide + remember) an initiative — the board's manual "done / drop" cleanup.
        # Same LAN-trust model as /api/dispatch (no new auth): the viewer binds LAN/localhost
        # only, and this write goes through the SERVER-SIDE mailbox creds (never the browser).
        # Unlike a snapshot mutation this is a REVERSIBLE board-state toggle — the card
        # reappears on new activity, or via the Done view's `[↺ unarchive]`. Wrapped like
        # /api/dispatch so an archive failure never 500s uncaught (502 on a store failure).
        repo, slug, reason = _parse_archive_body(body)
        if not repo or not slug:
            return (400, "application/json; charset=utf-8",
                    json.dumps({"ok": False, "error": "missing 'repo'/'slug'"}).encode("utf-8"))
        # Resolve the card's title from the current snapshot so the Done view can render an
        # aged-out card later. Best-effort — a title miss just stores "".
        model, _err = provider.snapshot()
        flat = (model or {}).get("flat", []) if model else []
        view = next(
            (v for v in flat if v.get("slug") == slug
             and (repo == v.get("repo") or repo == v.get("repo_name"))),
            None)
        title = (view or {}).get("title", "") if view else ""
        try:
            do_archive = archiver if archiver is not None else _archive().archive
            result = do_archive(repo, slug, title, reason or "done")
        except Exception as exc:  # noqa: BLE001 - never let an archive error kill the request
            sys.stderr.write(f"viewer: /api/archive failed: {type(exc).__name__}: {exc}\n")
            result = {"ok": False, "error": f"{type(exc).__name__}"}
        if result.get("ok"):
            # Drop the provider cache so the archived card leaves the board immediately.
            try:
                provider.invalidate()
            except Exception:  # noqa: BLE001 - a missing invalidate() must not 500
                pass
            return (200, "application/json; charset=utf-8",
                    json.dumps({"ok": True}).encode("utf-8"))
        return (502, "application/json; charset=utf-8",
                json.dumps({"ok": False,
                            "error": result.get("error") or "archive failed"}).encode("utf-8"))

    if method == "POST" and path == "/api/unarchive":
        # UNARCHIVE (restore to the board) an initiative — the Done view's `[↺ unarchive]`.
        # Same LAN-trust + wrapped-never-500 contract as /api/archive.
        repo, slug, _reason = _parse_archive_body(body)
        if not repo or not slug:
            return (400, "application/json; charset=utf-8",
                    json.dumps({"ok": False, "error": "missing 'repo'/'slug'"}).encode("utf-8"))
        try:
            do_unarchive = unarchiver if unarchiver is not None else _archive().unarchive
            result = do_unarchive(repo, slug)
        except Exception as exc:  # noqa: BLE001 - never let an unarchive error kill the request
            sys.stderr.write(f"viewer: /api/unarchive failed: {type(exc).__name__}: {exc}\n")
            result = {"ok": False, "error": f"{type(exc).__name__}"}
        if result.get("ok"):
            try:
                provider.invalidate()
            except Exception:  # noqa: BLE001 - a missing invalidate() must not 500
                pass
            return (200, "application/json; charset=utf-8",
                    json.dumps({"ok": True}).encode("utf-8"))
        return (502, "application/json; charset=utf-8",
                json.dumps({"ok": False,
                            "error": result.get("error") or "unarchive failed"}).encode("utf-8"))

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


def _parse_archive_body(body: bytes | None) -> tuple[str, str, str]:
    """Extract `(repo, slug, reason)` from a JSON POST body for POST /api/archive and
    /api/unarchive. `reason` is optional (defaults to "" here; the archive route substitutes
    "done"); a missing repo/slug becomes ("", "", …) → the route 400s. Pure + defensive
    (mirrors _parse_dispatch_body)."""
    if not body:
        return "", "", ""
    try:
        obj = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError):
        return "", "", ""
    if not isinstance(obj, dict):
        return "", "", ""
    repo = obj.get("repo")
    slug = obj.get("slug")
    reason = obj.get("reason")
    return (repo.strip() if isinstance(repo, str) else "",
            slug.strip() if isinstance(slug, str) else "",
            reason.strip() if isinstance(reason, str) else "")


def make_handler(provider, refresh_controller=None, asker=None, stream_asker=None,
                 dispatcher=None, archiver=None, unarchiver=None):
    """Build a BaseHTTPRequestHandler subclass bound to `provider` + `refresh_controller`
    + `asker` (the READ-ONLY Q&A callable for POST /api/ask; defaults to `default_ask`
    over this provider) + `stream_asker` (the STREAMING generator for POST /api/ask/stream;
    None ⇒ that endpoint 503s) + `dispatcher` (the POST /api/dispatch callable `view ->
    {ok,task_id,error}`; None ⇒ the route lazily loads `dispatch.dispatch_initiative`) +
    `archiver`/`unarchiver` (the POST /api/archive + /api/unarchive callables
    `(repo,slug,...) -> {ok,error}`; None ⇒ the route lazily loads `archive.archive` /
    `archive.unarchive`). All are injectable so the routes are testable without a model /
    network / clawgate token / Postgres."""
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
                    dispatcher=dispatcher, archiver=archiver, unarchiver=unarchiver)
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
                     f"POST /api/ask, POST /api/ask/stream, POST /api/dispatch, "
                     f"POST /api/archive, POST /api/unarchive)\n")
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
