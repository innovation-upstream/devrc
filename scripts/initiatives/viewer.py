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

    cols = ", ".join(DISPLAY_COLUMNS)
    MailDB = _import_maildb()
    with MailDB() as db:
        with db.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(f"SELECT {cols}, captured_at FROM initiatives.latest")
                rows = [dict(r) for r in cur.fetchall()]
            except psycopg2.Error:
                # View absent (or otherwise unqueryable): the transaction is now aborted,
                # so roll back before the fallback query on the same connection.
                db.conn.rollback()
                icols = ", ".join(f"i.{c}" for c in DISPLAY_COLUMNS)
                cur.execute(
                    f"SELECT {icols}, s.captured_at "
                    "FROM initiatives.initiative_snapshot i "
                    "JOIN initiatives.snapshots s ON s.id = i.snapshot_id "
                    "WHERE i.snapshot_id = (SELECT max(id) FROM initiatives.snapshots)"
                )
                rows = [dict(r) for r in cur.fetchall()]
            attach_recaps(db.conn, rows)
            return rows


def attach_recaps(conn, rows: list[dict]) -> bool:
    """Best-effort: LEFT-JOIN the standalone `initiatives.recaps` cache onto the loaded
    rows by (repo, slug), setting each row's `recap` (None when absent). Kept OUT of the
    `latest`/`current` views on purpose (recap is a per-(repo,slug) cache that persists
    across snapshots, not a per-snapshot column) — so the viewer joins it here instead.

    Strictly additive + fail-soft: if the recaps table doesn't exist yet (Phase B not
    deployed) or the read errors, every row simply keeps `recap=None` and the card falls
    back to `summary`. A `to_regclass` guard + a rollback on error keep the connection
    usable. Returns True if recaps were attached, False otherwise."""
    import psycopg2
    import psycopg2.extras

    for r in rows:
        r.setdefault("recap", None)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('initiatives.recaps')")
            reg = cur.fetchone()
            if reg is None or reg[0] is None:
                return False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT repo, slug, recap FROM initiatives.recaps")
            by_key = {(r["repo"], r["slug"]): r["recap"] for r in cur.fetchall()}
    except psycopg2.Error:
        with contextlib.suppress(Exception):
            conn.rollback()
        return False
    for r in rows:
        recap = by_key.get((r.get("repo"), r.get("slug")))
        if recap:
            r["recap"] = recap
    return True


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
    return {
        "slug": ini.get("slug") or "(no slug)",
        "repo": repo or "",
        "repo_name": _short_repo(repo),
        "title": ini.get("title") or "",
        "summary": (ini.get("summary") or "").strip(),
        # The LLM recap (Phase B) — the primary "what this is" line on the card FACE, with
        # `summary` as the fallback when no recap exists yet. From the standalone recaps
        # cache, attached in load_latest; untrusted text (rendered via the JSON island +
        # textContent, like everything else).
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
    }


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
.err{background:#442222;border:1px solid var(--red);color:var(--fg);
  padding:1rem;border-radius:4px}
.err b{color:var(--red)}
footer{margin-top:1.5rem;padding-top:.6rem;border-top:1px solid var(--bg2);
  color:var(--gray);font-size:.8rem}
footer .live{color:var(--green)}
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


# The whole page's client-side behaviour: parse the embedded JSON, render
# flat|grouped|recency, filter by search, expand a card (live detail fetch), refresh (POST
# /refresh), and auto-refresh the data in place. Vanilla JS, no framework, no external
# assets. Untrusted text is written via textContent (never innerHTML) so it can't inject
# markup. The __RECENCY_JS__ placeholder is substituted at module load with _RECENCY_JS.
_JS = r"""
(function(){
  __RECENCY_JS__
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
  // 3-way view toggle: 'recency' (by last_touch, DEFAULT) | 'flat' | 'grouped' (by repo).
  // Persisted in localStorage; an unknown/legacy value falls back to the recency default.
  var VALID_VIEWS = {flat:1, grouped:1, recency:1};
  var storedView = localStorage.getItem(VIEW_KEY);
  var state = { view: VALID_VIEWS[storedView] ? storedView : 'recency', q: '' };
  // The catch-all "live sessions" section is collapsible; remember the user's choice.
  // Default EXPANDED (the whole point is to see every running thread) — set to '1' to collapse.
  var unmatchedCollapsed = localStorage.getItem(UNMATCHED_KEY) === '1';
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

  function key(v){ return (v.repo || '') + '::' + (v.slug || ''); }

  function matchQ(v, q){
    if(!q) return true;
    // Search the FULL recent-message list (not just the face line) so a card is findable
    // by any of its prompts, even the ones filtered off the face.
    var msg = (v.recent_messages || []).map(function(m){ return (m && m.text) || ''; }).join(' ');
    var hay = ((v.slug||'') + ' ' + (v.title||'') + ' ' + (v.recap||'') + ' ' +
               (v.summary||'') + ' ' + (v.repo_name||'') + ' ' + (v.momentum||'') + ' ' +
               msg + ' ' + (v.live_task||'')).toLowerCase();
    return hay.indexOf(q) !== -1;
  }

  function el(tag, cls, txt){
    var e = document.createElement(tag);
    if(cls) e.className = cls;
    if(txt != null) e.textContent = txt;
    return e;
  }

  function renderDetail(det, d, v){
    det.innerHTML = '';
    if(!d || !d.ok){
      det.appendChild(el('div', 'detail-err', (d && d.error) || 'detail unavailable'));
      return;
    }
    if(d.summary) det.appendChild(el('div', 'detail-summary', d.summary));
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

    // The primary "what this is" line: the LLM recap when present, else the deterministic
    // handoff summary (never blank when either exists). textContent-only via el().
    var primary = v.recap || v.summary;
    if(primary) c.appendChild(el('div', 'summary', primary));

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

  function updateChrome(){
    btnFlat.classList.toggle('active', state.view === 'flat');
    btnGrouped.classList.toggle('active', state.view === 'grouped');
    if(btnRecency) btnRecency.classList.toggle('active', state.view === 'recency');
    if(countEl){
      var txt = (data.total || 0) + ' in flight across ' + (data.repo_count || 0) + ' repos';
      var nu = (data.live_unmatched || []).length;
      // Surface the uncovered live sessions in the header so the board's honesty is visible
      // at a glance (tagged initiatives + N untracked live threads).
      if(nu) txt += ' · ' + nu + ' live session' + (nu === 1 ? '' : 's') + ' untracked';
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
      app.appendChild(el('div', 'err', 'store unreachable: ' + (data.error || '')));
      updateChrome();
      return;
    }
    var q = state.q.trim().toLowerCase();
    var shown = 0;
    if(state.view === 'flat'){
      var wrap = el('div', 'flat');
      (data.flat || []).forEach(function(v){
        if(matchQ(v, q)){ wrap.appendChild(card(v)); shown++; }
      });
      app.appendChild(wrap);
    } else if(state.view === 'recency'){
      // Group the (search-filtered) flat stream into rolling now-relative recency buckets.
      // `data.flat` is already last_touch-DESC, and bucketizeRecency preserves that order within
      // each bucket, so cards stay newest-first per bucket. Empty buckets are omitted. Headers
      // read like the repo-group headers (label + count) — same `.repo`/`.count` styling.
      var rviews = (data.flat || []).filter(function(v){ return matchQ(v, q); });
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
        var vis = (g.initiatives || []).filter(function(v){ return matchQ(v, q); });
        if(!vis.length) return;
        var sec = el('section', 'repo');
        var h = el('h2', null, g.name);
        h.appendChild(el('span', 'count', String(vis.length)));
        sec.appendChild(h);
        vis.forEach(function(v){ sec.appendChild(card(v)); shown++; });
        app.appendChild(sec);
      });
    }
    if(shown === 0){
      app.appendChild(el('div', 'empty',
        q ? ('No initiatives match "' + state.q + '".')
          : 'No initiatives in the latest snapshot.'));
    }
    // The catch-all live-sessions section renders BELOW the initiatives (honestly showing
    // every running thread, not just the tagged few). Respects the same search filter.
    renderUnmatched(q);
    updateChrome();
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
  searchInput.addEventListener('input', function(){ state.q = searchInput.value; render(); });
  btnRefresh.addEventListener('click', doRefresh);

  setInterval(function(){ refetch().catch(function(){}); }, __REFRESH_MS__);

  searchInput.value = state.q;
  render();
})();
""".strip()

# Inline the pure recency-bucketing snippet into the page JS (single source of truth: the
# node test evals _RECENCY_JS directly, the page runs this substituted copy).
_JS = _JS.replace("__RECENCY_JS__", _RECENCY_JS)


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
        '<button id="refresh" class="rbtn" type="button" '
        'title="run a fresh sync now">↻ refresh</button>'
        '<span id="refresh-msg" class="rmsg"></span>'
        '</div>'
        '</header>'
    )
    js = _JS.replace("__REFRESH_MS__", str(int(refresh) * 1000))
    return (
        head + header +
        '<main id="app"></main>'
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
                  refresh_controller=None) -> tuple[int, str, bytes]:
    """PURE-ish request router: (path, provider, method, query, refresh) -> (status,
    content_type, body bytes). Separated from the socket handler so it's unit-testable
    with a fake provider / controller (no server, no DB, no subprocess).

    `/healthz` is deliberately store-independent (PROCESS liveness). `POST /refresh` runs
    a single-flighted+debounced sync then invalidates the provider cache on success.
    `/api/initiative` returns one initiative's live detail."""
    if path == "/healthz":
        return 200, "text/plain; charset=utf-8", b"ok\n"

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


def make_handler(provider, refresh_controller=None):
    """Build a BaseHTTPRequestHandler subclass bound to `provider` + `refresh_controller`."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "initiatives-viewer/2.0"

        def _serve(self, write_body: bool, method: str) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                status, ctype, body = route_request(
                    parsed.path, provider, method=method, query=query,
                    refresh_controller=refresh_controller)
            except Exception as exc:  # noqa: BLE001 - never let a handler error kill the thread
                status, ctype = 500, "text/plain; charset=utf-8"
                body = f"internal error: {type(exc).__name__}: {exc}\n".encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if write_body:
                self.wfile.write(body)

        def _drain_body(self) -> None:
            """Consume any request body so the connection stays usable (POST /refresh)."""
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                n = 0
            if n > 0:
                self.rfile.read(n)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            self._serve(write_body=True, method="GET")

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(write_body=False, method="GET")

        def do_POST(self) -> None:  # noqa: N802
            self._drain_body()
            self._serve(write_body=True, method="POST")

        def log_message(self, fmt, *args) -> None:  # quiet: one compact line to stderr
            sys.stderr.write("viewer: %s - %s\n" % (self.address_string(), fmt % args))

    return Handler


def serve(host: str, port: int, provider: DataProvider | None = None,
          refresh_controller: RefreshController | None = None) -> None:
    """Start the blocking HTTP server on host:port with a threaded handler."""
    provider = provider or DataProvider()
    refresh_controller = refresh_controller or RefreshController()
    httpd = ThreadingHTTPServer((host, port), make_handler(provider, refresh_controller))
    httpd.daemon_threads = True
    sys.stderr.write(f"viewer: serving on http://{host}:{port}/ "
                     f"(/, /healthz, /api/initiatives.json, /api/initiative, POST /refresh)\n")
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
