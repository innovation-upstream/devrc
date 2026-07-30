#!/usr/bin/env python3
"""clawgate LINKED TASKS — the consumer side of repo-cos's `initiative:<slug>` task tags.

repo-cos (and any other producer) tags an approved clawgate Task with `initiative:<slug>`,
where the slug is the initiatives ledger slug VERBATIM (the tag pipeline guarantees the tag
is either exact or absent — it never emits a rewritten slug). This module is the consumer:
it reads the clawgate task queue ONCE per board render and groups the tasks by that tag, so
the viewer can (a) show an initiative's linked Tasks in its expanded card detail and (b)
guard `⤴ dispatch` against minting a duplicate card for work that is already queued.

Contract — mirrors `dispatch.py`'s (the sibling that WRITES tasks) exactly:
  * stdlib only, no new runtime deps;
  * BEST-EFFORT / NEVER RAISES — clawgate unreachable, missing or unreadable creds, a
    timeout, malformed JSON, or a rolled-back clawgate with no `tags` field all degrade to
    "no linked tasks", logged to stderr. A board render must never block on or fail because
    of clawgate;
  * a SHORT timeout (`FETCH_TIMEOUT`), because this sits on the render path;
  * credential loading is REUSED from `dispatch.py` (`load_creds` → ~/.claude/clawgate.env),
    not reimplemented — one parser, one behaviour across the writer and the reader.

ONE fetch per render, not one per card: `GET /api/tasks` returns the whole queue (~5 tasks),
which is then grouped into a `slug -> [task view]` map. There is deliberately NO per-card
HTTP call (the board carries ~140 cards).

Public surface:
  * `clawgate_base_url(creds, env)` → the API base (env override → creds → LAN NodePort).
  * `fetch_tasks(...)`             → the raw task list, `[]` on ANY failure.
  * `initiative_slugs(task)`       → the task's `initiative:` tag values, EXACT (no case-fold,
                                     no normalization — `initiative:Foo` keys `Foo`, which
                                     therefore never matches the slug `foo`).
  * `task_view(task, base_url)`    → the small render dict {id,title,status,open,url}.
  * `group_by_slug(tasks, base)`   → `slug -> [task view]` (a task with several initiative
                                     tags lands under each; an untagged task lands nowhere).
  * `open_task_count(views)`       → how many of a card's linked tasks are still LIVE work.
  * `linked_tasks_map(...)`        → the orchestrator the viewer calls: fetch + group, `{}`
                                     on any failure.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The clawgate API base. Env override first (same convention as the rest of the subsystem —
# INITIATIVES_SYNC_DAYS, RECAP_MODEL, …), then the creds file's own CLAWGATE_API_URL (so the
# reader and the writer agree by default), then the workbench LAN NodePort.
CLAWGATE_URL_ENV = "INITIATIVES_CLAWGATE_URL"
DEFAULT_CLAWGATE_URL = "http://192.168.50.250:30302"

# The reserved namespace repo-cos writes. §3 of the tag spec: `initiative:<slug>`, soft-
# validated (charset/length only), the initiatives side does the join — this module IS that join.
INITIATIVE_TAG_PREFIX = "initiative:"

# SHORT by design: this read sits on the board render path, so a hung clawgate must cost a
# few seconds at most and then degrade to "no linked tasks".
FETCH_TIMEOUT = 4  # seconds

# Statuses that count as LIVE work for the dispatch guard — a CLOSED set, so anything else
# (`complete`, a dismissed/renamed/unknown status, a missing status) is treated as NOT
# blocking. Fail-open on purpose: a clawgate that renames a status must never wedge dispatch.
# `ready_for_review` is included because such a task is still unfinished work in the queue —
# dispatching a second card for it would be the exact duplicate this guard exists to prevent.
OPEN_STATUSES = ("open", "in_progress", "ready_for_review")

# Credential loading is reused from the sibling WRITER (one parser for ~/.claude/clawgate.env),
# loaded by explicit importlib path — the package's cross-load convention, NOT sys.path.
_DISPATCH_PATH = Path(__file__).resolve().parent / "dispatch.py"
_dispatch_mod = None


def _log(msg: str) -> None:
    print(f"  tasks: {msg}", file=sys.stderr)


def _dispatch():
    global _dispatch_mod
    if _dispatch_mod is None:
        spec = importlib.util.spec_from_file_location(
            "initiatives_tasks_dispatch", _DISPATCH_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {_DISPATCH_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _dispatch_mod = mod
    return _dispatch_mod


def load_creds(path=None) -> dict:
    """~/.claude/clawgate.env → dict, via `dispatch.load_creds` (SINGLE-SOURCED — the writer
    and the reader must never disagree about how the creds file parses). {} if the sibling
    can't even be loaded. Never raises."""
    try:
        return _dispatch().load_creds(path)
    except Exception as exc:  # noqa: BLE001 - a sibling-load hiccup is "no creds", not a crash
        _log(f"could not load creds: {exc}")
        return {}


# ---- configuration -----------------------------------------------------------------

def clawgate_base_url(creds: dict | None = None, env=None) -> str:
    """The clawgate API base URL, trailing slash stripped.

    Precedence: `$INITIATIVES_CLAWGATE_URL` (the env knob) → the creds file's
    `CLAWGATE_API_URL` → `DEFAULT_CLAWGATE_URL` (the workbench LAN NodePort). Pure + never
    raises; a non-mapping `creds`/`env` degrades to the default."""
    try:
        e = env if env is not None else os.environ
        override = str(e.get(CLAWGATE_URL_ENV) or "").strip()
        if override:
            return override.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    try:
        from_creds = str((creds or {}).get("CLAWGATE_API_URL") or "").strip()
        if from_creds:
            return from_creds.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_CLAWGATE_URL


# ---- HTTP read ---------------------------------------------------------------------

def _get(url: str, token: str, *, timeout: int = FETCH_TIMEOUT) -> str:
    """Isolated network GET (stdlib urllib) — injected/mocked in tests. Returns the body."""
    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = getattr(resp, "status", None)
        if code is not None and int(code) != 200:
            raise OSError(f"HTTP {code}")
        return resp.read().decode("utf-8")


def fetch_tasks(*, creds: dict | None = None, env=None, timeout: int = FETCH_TIMEOUT,
                getter=None) -> list[dict]:
    """`GET {base}/api/tasks` (Bearer hook token) → the raw task list.

    BEST-EFFORT: missing/unreadable creds, an unreachable clawgate, a non-200, a timeout, or
    a body that isn't a JSON array all log to stderr and return `[]`. NEVER raises — a board
    render must not depend on clawgate being up."""
    c = creds if creds is not None else load_creds()
    token = ""
    try:
        token = str(c.get("CLAWGATE_HOOK_TOKEN") or "")
    except Exception:  # noqa: BLE001 - a non-mapping creds is just "no creds"
        token = ""
    if not token:
        _log("CLAWGATE_HOOK_TOKEN not set (~/.claude/clawgate.env) — no linked tasks")
        return []

    url = f"{clawgate_base_url(c, env)}/api/tasks"
    get = getter if getter is not None else _get
    try:
        raw = get(url, token, timeout=timeout)
    except urllib.error.HTTPError as exc:
        _log(f"GET {url} failed: HTTP {exc.code} {exc.reason}")
        return []
    except Exception as exc:  # noqa: BLE001
        _log(f"GET {url} failed: {exc}")
        return []

    try:
        obj = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        _log(f"GET {url} returned unparseable JSON ({exc})")
        return []
    if not isinstance(obj, list):
        _log(f"GET {url} returned {type(obj).__name__}, expected a list — no linked tasks")
        return []
    return [t for t in obj if isinstance(t, dict)]


# ---- pure grouping -----------------------------------------------------------------

def initiative_slugs(task: dict) -> list[str]:
    """A task's `initiative:` tag values, in order, de-duplicated.

    EXACT by construction: the prefix match is case-sensitive and the value is returned
    VERBATIM (no lowercasing, no trimming beyond the surrounding whitespace of the whole
    tag). The dict lookup in `group_by_slug` is therefore exact too — `initiative:Foo` keys
    `Foo` and can never satisfy a card whose slug is `foo`, and `initiative:foo` never
    matches `foo-bar`. A rolled-back clawgate with NO `tags` field, or a non-list `tags`,
    yields `[]`. Pure, never raises."""
    try:
        tags = (task or {}).get("tags")
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        t = tag.strip()
        if not t.startswith(INITIATIVE_TAG_PREFIX):
            continue
        slug = t[len(INITIATIVE_TAG_PREFIX):]
        if slug and slug not in out:
            out.append(slug)
    return out


def is_open(task: dict) -> bool:
    """Is this task still LIVE work (and therefore a reason not to mint a duplicate)?
    `OPEN_STATUSES` is a closed set — `complete`, a dismissed/unknown/missing status → False."""
    try:
        return str((task or {}).get("status") or "") in OPEN_STATUSES
    except Exception:  # noqa: BLE001
        return False


def task_view(task: dict, base_url: str = "") -> dict:
    """One clawgate task → the small dict the board renders: `{id, title, status, open, url}`.

    `title` prefers the (newer) `title` field and falls back to `directory`, which is how
    every current producer smuggles the title through (tag spec §9). `url` deep-links to the
    task's card in clawgate (`{base}/tasks#task-<id>` — the card element carries
    `id="task-<id>"`); it is "" when there's no id or no base. Pure, never raises."""
    t = task or {}
    tid = t.get("id")
    if isinstance(tid, bool) or not isinstance(tid, int):
        tid = None
    title = str(t.get("title") or "").strip() or str(t.get("directory") or "").strip()
    base = (base_url or "").rstrip("/")
    return {
        "id": tid,
        "title": title,
        "status": str(t.get("status") or ""),
        "open": is_open(t),
        "url": f"{base}/tasks#task-{tid}" if (base and tid is not None) else "",
    }


def group_by_slug(tasks, base_url: str = "") -> dict[str, list[dict]]:
    """PURE: a clawgate task list → `slug -> [task view]`, keyed on the EXACT
    `initiative:<slug>` tag value.

    A task carrying several `initiative:` tags appears under EACH slug; a task with no
    `initiative:` tag (or no `tags` field at all) appears nowhere; several tasks on one
    initiative accumulate in one list (input order preserved). A slug matching no card is
    simply an unused key — the viewer looks cards up in this map, never the reverse. Never
    raises: a non-list input, or a non-dict entry, is skipped."""
    out: dict[str, list[dict]] = {}
    if not isinstance(tasks, list):
        return out
    for task in tasks:
        if not isinstance(task, dict):
            continue
        slugs = initiative_slugs(task)
        if not slugs:
            continue
        view = task_view(task, base_url)
        for slug in slugs:
            out.setdefault(slug, []).append(view)
    return out


def open_task_count(views) -> int:
    """How many of a card's linked task views are still LIVE work (drives the dispatch guard)."""
    if not isinstance(views, list):
        return 0
    return sum(1 for v in views if isinstance(v, dict) and v.get("open"))


# ---- orchestrator ------------------------------------------------------------------

def linked_tasks_map(*, creds: dict | None = None, env=None, timeout: int = FETCH_TIMEOUT,
                     fetcher=None) -> dict[str, list[dict]]:
    """The one call the viewer makes per board render: fetch the clawgate queue ONCE and
    group it into `slug -> [task view]`.

    `{}` on ANY failure (no creds, clawgate down, non-200, malformed JSON, no tags field) —
    which renders the board exactly as it does today, with no linked-task info and no error
    surface. NEVER raises."""
    try:
        c = creds if creds is not None else load_creds()
        fetch = fetcher if fetcher is not None else fetch_tasks
        tasks = fetch(creds=c, env=env, timeout=timeout)
        return group_by_slug(tasks, clawgate_base_url(c, env))
    except Exception as exc:  # noqa: BLE001 - best-effort: a failure is an empty map, not a raise
        _log(f"linked_tasks_map failed: {type(exc).__name__}: {exc}")
        return {}
