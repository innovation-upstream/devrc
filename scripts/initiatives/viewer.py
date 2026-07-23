#!/usr/bin/env python3
"""Live web viewer over the Phase-1 initiatives store.

PHASE 3 of the "initiatives consolidation" feature. A self-contained, auto-refreshing
web page (stdlib `http.server`, NO web framework) that renders the CURRENT initiatives
from the homelab `mailbox` Postgres — grouped by repo, with momentum badges, next-step,
open PRs, and a LIVE tmux overlay (which tmux session is on each initiative right now).
It is the durable, browser-viewable counterpart to the ephemeral agent-ops TUI.

Data (two layers, both best-effort per request):
  1. The STORE — `initiatives.latest` (rows from the most recent snapshot only, so NO
     aged-out "ghosts" — unlike `initiatives.current`, which the router wants). Read via
     `mail-actions/_db.py`'s kubectl port-forward. Falls back to an inline
     `WHERE snapshot_id=(SELECT max(id) …)` query if the `latest` view doesn't exist yet
     (i.e. before the next sync recreates the schema).
  2. The LIVE tmux overlay — attached at RENDER TIME from THIS host's tmux server, reusing
     the scan's machinery (`collect_tmux_panes` / `match_tmux_to_initiatives` / …) rather
     than reimplementing it. Deliberately NOT stored in Postgres (the durable-vs-live split
     the agent-ops dashboard uses). Absent if there's no tmux server (best-effort).

Layering mirrors sync.py / route.py: the pure render transform (`build_model` /
`render_html`) is separated from all I/O (the DB read, the tmux read, the HTTP server),
so it is unit-testable with fixtures — no live DB, no live tmux, no sockets.

Serving:
  Routes: `/` (HTML), `/healthz` (200/ok — process liveness, NOT the DB), and
  `/api/initiatives.json` (the JSON the page is built from). Binds LAN/localhost only by
  default; NOT wired into the public homelab gateway — this is internal work data. A short
  in-process cache (a few seconds) avoids hammering the port-forward on rapid refreshes.
  A DB outage renders a clear error page and keeps serving (never crash-loops).

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
import html
import importlib.util
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# The scan we borrow the tmux machinery from (hyphenated filename → importlib, not import).
SCAN_PATH = Path(__file__).resolve().parents[1] / "session-analysis" / "initiative-scan.py"
# chquery lives here; the scan adds it to sys.path on import, mirror that so the scan's
# top-level `import chquery` resolves regardless of cwd.
VALIDATION_DIR = Path(__file__).resolve().parents[1] / "validation"

# The shared mailbox-Postgres helper (kubectl port-forward + psycopg2 + DSN-from-secret).
MAILDB_PATH = Path(__file__).resolve().parents[1] / "mail-actions" / "_db.py"

# The rich display columns the viewer reads (present on both `initiatives.latest` and the
# base `initiative_snapshot` table, so the inline fallback selects the SAME set + captured_at).
DISPLAY_COLUMNS = [
    "slug", "repo", "title", "momentum", "last_touch", "next_step", "commits",
    "commits_unknown", "merged_prs", "open_prs", "session_count", "telem_events",
    "current_doc", "open_investigations",
]

# Momentum ordering + badges — SAME ranks/glyphs the scan uses (active→stalled→unknown).
MOMENTUM_RANK = {"active": 0, "slowing": 1, "stalled": 2, "unknown": 3}
MOMENTUM_BADGE = {
    "active": ("●", "active"),    # ●
    "slowing": ("◐", "slowing"),  # ◐
    "stalled": ("○", "stalled"),  # ○
    "unknown": ("·", "unknown"),  # ·
}

# Auto-refresh cadence for the page (seconds) — matches the "sync is hourly, but tmux is
# live" story: a 30s refresh keeps the tmux overlay near-live without hammering the DB (the
# provider cache absorbs the store reads).
REFRESH_SECONDS = 30
DEFAULT_HOST = "192.168.50.94"  # workbench-LAN bind; override with --host 127.0.0.1 for local
DEFAULT_PORT = 8899
CACHE_TTL_SECONDS = 5.0


# --------------------------------------------------------------------------- #
# Lazy imports of the two borrowed modules (single-sourced; not reimplemented).
# --------------------------------------------------------------------------- #
_scan_mod = None


def _scan():
    """Load initiative-scan.py by explicit path and cache it (for the tmux machinery).

    Lazy + side-effect-light: the scan's top-level `import chquery` only runs the first
    time the tmux overlay is attached. `chquery` needs `requests` + the
    `scripts/validation` dir on sys.path; we add the latter idempotently, mirroring
    route.py."""
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
                return [dict(r) for r in cur.fetchall()]
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
            return [dict(r) for r in cur.fetchall()]


def attach_tmux(initiatives: list[dict]) -> bool:
    """Attach live tmux sessions to each initiative (mutates `tmux_sessions`). Returns
    True if the overlay was applied, False if absent (no tmux server / any failure).

    Reuses the scan's machinery verbatim: `collect_tmux_panes` reads THIS host's panes,
    `match_tmux_to_initiatives` links each pane's title to an initiative in its repo. The
    viewer must run ON the host whose tmux we want to see (that's where its systemd unit
    lives). Fully best-effort — no tmux server, no scan import, any error → overlay simply
    absent, never fatal."""
    try:
        scan = _scan()
        panes = scan.collect_tmux_panes()
        if not panes:
            return False  # no tmux server on this host → overlay absent (not an error)
        repos = scan.discover_repos()
        wt_map = scan.worktree_canonical_map(repos)
        codenames = scan.load_scratch_codenames()
        scan.match_tmux_to_initiatives(initiatives, panes, repos, wt_map, codenames)
        return True
    except Exception:  # noqa: BLE001 - the overlay is a nicety, never a hard dependency
        return False


# --------------------------------------------------------------------------- #
# Pure render transform (rows -> model -> HTML). No I/O — unit-tested with fixtures.
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


def _initiative_view(ini: dict, now: datetime) -> dict:
    """One store row (+ any attached tmux_sessions) -> a flat, template-ready view dict."""
    momentum = ini.get("momentum") or "unknown"
    glyph, label = momentum_badge(momentum)
    open_prs = ini.get("open_prs") or []
    tmux = sorted(ini.get("tmux_sessions") or [])
    return {
        "slug": ini.get("slug") or "(no slug)",
        "title": ini.get("title") or "",
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
        "tmux_sessions": tmux,
    }


def build_model(rows: list[dict], now: datetime | None = None) -> dict:
    """PURE: store rows (+ any attached tmux) -> a grouped, sorted render model.

    Groups initiatives by repo; within a repo sorts by momentum (active→stalled→unknown)
    then recency (newest last_touch first); orders repos by their most-active initiative
    then by name, so the busiest repos surface first. `captured_at` (the snapshot's
    freshness, carried on every row) drives the 'updated Xm ago' footer. An empty row
    list yields an empty (but well-formed) model — never raises."""
    now = now or datetime.now(timezone.utc)

    # The snapshot freshness = the newest captured_at across the rows (they should all
    # share one snapshot, but max() is robust to a mixed read).
    captured_ats = [_as_utc(r.get("captured_at")) for r in rows]
    captured_at = max((c for c in captured_ats if c is not None), default=None)

    by_repo: dict[str | None, list[dict]] = {}
    for r in rows:
        by_repo.setdefault(r.get("repo"), []).append(_initiative_view(r, now))

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

    return {
        "generated_at": now,
        "captured_at": captured_at,
        "captured_age": rel_age(captured_at, now) if captured_at else None,
        "total": len(rows),
        "repo_count": len(repos),
        "repos": repos,
    }


def model_to_json(model: dict | None, error: str | None) -> dict:
    """The `/api/initiatives.json` payload (datetimes isoformatted via json default=str)."""
    if error is not None or model is None:
        return {"ok": False, "error": error or "no data", "repos": []}
    return {
        "ok": True,
        "generated_at": model["generated_at"],
        "captured_at": model["captured_at"],
        "captured_age": model["captured_age"],
        "total": model["total"],
        "repo_count": model["repo_count"],
        "repos": model["repos"],
    }


# --------------------------------------------------------------------------- #
# HTML rendering — self-contained, inline CSS, gruvbox-ish, no external assets.
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
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:.6rem;
  border-bottom:1px solid var(--bg2);padding-bottom:.6rem;margin-bottom:1rem}
header h1{font-size:1.15rem;margin:0;color:var(--yellow)}
header .meta{color:var(--gray);font-size:.85rem}
.repo{margin:0 0 1.4rem}
.repo > h2{font-size:.95rem;margin:0 0 .5rem;color:var(--aqua);
  border-bottom:1px dotted var(--bg2);padding-bottom:.25rem}
.repo > h2 .count{color:var(--gray);font-weight:normal;font-size:.8rem;margin-left:.4rem}
.ini{background:var(--bg1);border-left:3px solid var(--gray);border-radius:4px;
  padding:.55rem .7rem;margin:0 0 .5rem}
.ini.active{border-left-color:var(--green)}
.ini.slowing{border-left-color:var(--yellow)}
.ini.stalled{border-left-color:var(--gray)}
.ini .row1{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem}
.badge{font-weight:bold}
.badge.active{color:var(--green)}
.badge.slowing{color:var(--yellow)}
.badge.stalled{color:var(--gray)}
.badge.unknown{color:var(--gray)}
.slug{font-weight:bold;color:var(--fg)}
.title{color:var(--fg2)}
.age{color:var(--gray);font-size:.82rem;margin-left:auto}
.tags{margin-top:.3rem;display:flex;flex-wrap:wrap;gap:.35rem;align-items:center}
.tag{font-size:.78rem;padding:.05rem .4rem;border-radius:3px;background:var(--bg2);color:var(--fg2)}
.tag.tmux{background:#665c54;color:var(--green)}
.tag.pr{background:var(--bg2);color:var(--blue)}
.tag.stat{background:transparent;color:var(--gray);padding-left:0}
.next{margin-top:.3rem;color:var(--fg2);font-size:.86rem}
.next b{color:var(--orange);font-weight:normal}
.invs{margin:.25rem 0 0;padding-left:1.1rem;color:var(--gray);font-size:.8rem}
.empty{color:var(--gray);padding:2rem 0}
.err{background:#442222;border:1px solid var(--red);color:var(--fg);
  padding:1rem;border-radius:4px}
.err b{color:var(--red)}
footer{margin-top:1.5rem;padding-top:.6rem;border-top:1px solid var(--bg2);
  color:var(--gray);font-size:.8rem}
""".strip()


def _e(s) -> str:
    """HTML-escape any value (str/None/number) for safe interpolation."""
    return html.escape("" if s is None else str(s))


def _render_initiative(v: dict) -> str:
    m = v["momentum"]
    parts = [f'<div class="ini {_e(m)}">']
    # Row 1: badge · slug · title · age
    parts.append('<div class="row1">')
    parts.append(f'<span class="badge {_e(m)}">{_e(v["badge_glyph"])} {_e(v["badge_label"])}</span>')
    parts.append(f'<span class="slug">{_e(v["slug"])}</span>')
    if v["title"]:
        parts.append(f'<span class="title">{_e(v["title"])}</span>')
    parts.append(f'<span class="age">updated {_e(v["age"])} ago</span>')
    parts.append('</div>')

    # Tags: tmux sessions, open PRs, and a couple of stat chips.
    tags: list[str] = []
    for sess in v["tmux_sessions"]:
        tags.append(f'<span class="tag tmux">[tmux:{_e(sess)}]</span>')
    for pr in v["open_prs"]:
        num = pr["number"]
        label = f'#{num}' if num is not None else 'PR'
        title = f' {_e(pr["title"])}' if pr["title"] else ''
        tags.append(f'<span class="tag pr" title="{_e(pr["title"])}">{_e(label)}{title}</span>')
    commits = "?" if v["commits_unknown"] else v["commits"]
    stat = f'{commits} commits · {v["merged_prs"]} merged · {v["session_count"]} sess · {v["telem_events"]} ev'
    tags.append(f'<span class="tag stat">{_e(stat)}</span>')
    parts.append(f'<div class="tags">{"".join(tags)}</div>')

    # Next step.
    if v["next_step"]:
        parts.append(f'<div class="next"><b>next</b> &rsaquo; {_e(v["next_step"])}</div>')

    # Open investigations (first few).
    if v["open_investigations"]:
        lis = "".join(f"<li>{_e(x)}</li>" for x in v["open_investigations"][:3])
        parts.append(f'<ul class="invs">{lis}</ul>')

    parts.append('</div>')
    return "".join(parts)


def render_html(model: dict | None, error: str | None = None,
                refresh: int = REFRESH_SECONDS) -> str:
    """PURE: a render model (or an error) -> a complete, self-contained HTML page.

    Auto-refreshes via <meta http-equiv=refresh>; all CSS inline; no external assets. A
    None model / non-None error renders a clear inline error box (the store was
    unreachable) while STILL serving a valid page, so a DB blip degrades gracefully."""
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta http-equiv="refresh" content="{int(refresh)}">'
        '<title>initiatives</title>'
        f'<style>{_CSS}</style></head><body>'
    )
    body: list[str] = []

    if error is not None or model is None:
        body.append('<header><h1>initiatives</h1>'
                    '<span class="meta">live viewer</span></header>')
        body.append(
            '<div class="err"><b>store unreachable</b> — could not read the '
            'initiatives store this refresh. Retrying automatically.'
            f'<br><small>{_e(error or "no data")}</small></div>')
        body.append('<footer>the page auto-refreshes; the store is populated by the '
                    'hourly initiatives-sync timer.</footer>')
        return head + "".join(body) + "</body></html>"

    total, repo_count = model["total"], model["repo_count"]
    body.append(
        '<header><h1>initiatives</h1>'
        f'<span class="meta">{total} in flight across {repo_count} repos</span>'
        '</header>')

    if not model["repos"]:
        body.append('<div class="empty">No initiatives in the latest snapshot. '
                    '(The store may be empty, or every initiative aged out of the '
                    'scan window.)</div>')
    for group in model["repos"]:
        body.append('<section class="repo">')
        body.append(f'<h2>{_e(group["name"])}'
                    f'<span class="count">{len(group["initiatives"])}</span></h2>')
        for v in group["initiatives"]:
            body.append(_render_initiative(v))
        body.append('</section>')

    # Footer: freshness from the snapshot's captured_at + the sync cadence.
    if model["captured_at"] is not None:
        cap = model["captured_at"].strftime("%Y-%m-%d %H:%M UTC")
        fresh = f'updated {_e(model["captured_age"])} ago · snapshot {_e(cap)} · hourly sync'
    else:
        fresh = 'no snapshot captured_at · hourly sync'
    gen = model["generated_at"].strftime("%H:%M:%S UTC")
    body.append(f'<footer>{fresh} · tmux overlay is live · '
                f'page rendered {_e(gen)}, auto-refresh {int(refresh)}s</footer>')

    return head + "".join(body) + "</body></html>"


# --------------------------------------------------------------------------- #
# Data provider — reads the store + tmux with a short TTL cache; thread-safe.
# --------------------------------------------------------------------------- #
class DataProvider:
    """Fetches (store rows + live tmux) and builds a render model, cached for `ttl`
    seconds so rapid page refreshes don't each open a fresh kubectl port-forward.

    `snapshot()` returns `(model, error)`: on success `(model, None)`, on any read
    failure `(None, "<message>")` — the server renders the error inline and keeps
    serving (no crash-loop). Thread-safe: the ThreadingHTTPServer serves requests
    concurrently, so the cache is guarded by a lock and only ONE refresh runs at a time.
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

    def snapshot(self) -> tuple[dict | None, str | None]:
        with self._lock:
            if self._cached is not None and (time.monotonic() - self._fetched_at) < self._ttl:
                return self._cached
            try:
                rows = self._loader()
                self._tmux(rows)  # best-effort; mutates rows in place
                result: tuple[dict | None, str | None] = (build_model(rows, self._now()), None)
            except Exception as exc:  # noqa: BLE001 - any read failure → graceful error page
                result = (None, f"{type(exc).__name__}: {exc}")
            self._cached = result
            self._fetched_at = time.monotonic()
            return result


# --------------------------------------------------------------------------- #
# HTTP layer — a thin BaseHTTPRequestHandler over a pure `route_request`.
# --------------------------------------------------------------------------- #
def route_request(path: str, provider) -> tuple[int, str, bytes]:
    """PURE-ish request router: (path, provider) -> (status, content_type, body bytes).

    Separated from the socket handler so it's unit-testable with a fake provider (no
    server, no DB). `/healthz` is deliberately independent of the store — it reports
    PROCESS liveness (200/ok) so a DB outage doesn't take the unit 'unhealthy' and
    trigger a needless restart; the store's health surfaces on the page itself."""
    if path == "/healthz":
        return 200, "text/plain; charset=utf-8", b"ok\n"
    if path in ("/", ""):
        model, error = provider.snapshot()
        return 200, "text/html; charset=utf-8", render_html(model, error).encode("utf-8")
    if path == "/api/initiatives.json":
        model, error = provider.snapshot()
        payload = json.dumps(model_to_json(model, error), default=str,
                             ensure_ascii=False, indent=2)
        return 200, "application/json; charset=utf-8", payload.encode("utf-8")
    return 404, "text/plain; charset=utf-8", b"not found\n"


def make_handler(provider):
    """Build a BaseHTTPRequestHandler subclass bound to `provider`."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "initiatives-viewer/1.0"

        def _serve(self, write_body: bool) -> None:
            path = urlparse(self.path).path
            try:
                status, ctype, body = route_request(path, provider)
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

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            self._serve(write_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._serve(write_body=False)

        def log_message(self, fmt, *args) -> None:  # quiet: one compact line to stderr
            sys.stderr.write("viewer: %s - %s\n" % (self.address_string(), fmt % args))

    return Handler


def serve(host: str, port: int, provider: DataProvider | None = None) -> None:
    """Start the blocking HTTP server on host:port with a threaded handler."""
    provider = provider or DataProvider()
    httpd = ThreadingHTTPServer((host, port), make_handler(provider))
    httpd.daemon_threads = True
    sys.stderr.write(f"viewer: serving on http://{host}:{port}/ "
                     f"(/, /healthz, /api/initiatives.json)\n")
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
    serve(a.host, a.port, DataProvider(ttl=a.ttl))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
