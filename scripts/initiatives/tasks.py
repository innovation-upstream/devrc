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
  * a hard WALL-CLOCK deadline (`FETCH_DEADLINE`) + a response SIZE CAP, because this sits on
    the render path;
  * credential loading is REUSED from `dispatch.py` (`load_creds` → ~/.claude/clawgate.env),
    not reimplemented — one parser, one behaviour across the writer and the reader.

ONE fetch per render, not one per card: `GET /api/tasks` returns the whole queue (10 tasks /
94,428 bytes when measured 2026-08-11), which is then grouped into a `slug -> [task view]`
map. There is deliberately NO per-card HTTP call (the board carries ~140 cards).

🔴 WHY A WALL-CLOCK DEADLINE AND NOT `urlopen(timeout=)` (the audit finding that got this
rewritten). `urlopen(timeout=N)` is a PER-SOCKET-OPERATION timeout, not a deadline on the
call. Measured against the first cut (`timeout=4`), on top of a 0.80s cold-render baseline:

    connection refused                                 +0.010s   (the only case tested → shipped)
    blackhole (accepts, never replies)                 +4.005s   (~6x the whole render)
    slow-drip (1 byte / 2s, each read inside the 4s)  +130s      — AND IT STILL SUCCEEDED

so a dribbling clawgate stalled the board without bound. The fix is three-layered:
  1. `_run_with_deadline` runs the whole fetch (DNS + connect + headers + body) in a DAEMON
     thread and joins for at most `FETCH_DEADLINE` wall-clock seconds. That is a real deadline
     covering every phase, whatever the socket does — including an injected getter;
  2. `_get` additionally re-checks the wall clock between body chunks — reading with `read1`,
     which returns whatever is already available instead of blocking for a full `READ_CHUNK`
     — and caps the response at `MAX_RESPONSE_BYTES`, so an abandoned worker thread
     TERMINATES itself instead of leaking while it dribbles. This used to be `read`, which
     made the re-check unreachable for any body ≥`READ_CHUNK` (64 KiB) and leaked a live
     thread + its FDs per abandoned fetch; that was excused as "the payload is only ~16 KB"
     until the payload was re-measured at 94,428 bytes for 10 tasks on 2026-08-11. `Notes.List`
     has no `LIMIT` and rows carry full task bodies, so it grows with retained history — the
     excuse was always going to expire, the guard is what has to hold;
  3. `LinkedTaskCache` (below) means the deadline is paid at most once per `CACHE_TTL_SECONDS`,
     not on every cold render — and a failed refresh keeps serving the LAST GOOD map.

The read is ALSO issued OUTSIDE `DataProvider._lock` (see `viewer.DataProvider.snapshot`), so a
slow clawgate can never serialize `/`, `/api/initiatives.json`, `/api/initiative` and `/api/ask`
behind it.

Public surface:
  * `clawgate_base_url(creds, env)` → the API base (env override → creds → LAN NodePort).
  * `fetch_tasks_result(...)`      → `(ok, tasks)` — `ok=False` distinguishes a FAILED read
                                     from a genuinely empty queue (what serve-stale needs).
  * `fetch_tasks(...)`             → the raw task list, `[]` on ANY failure (thin wrapper).
  * `initiative_slugs(task)`       → the task's `initiative:` tag values, EXACT (no case-fold,
                                     no normalization — `initiative:Foo` keys `Foo`, which
                                     therefore never matches the slug `foo`).
  * `task_view(task, base_url)`    → the small render dict {id,title,status,open,url}.
  * `group_by_slug(tasks, base)`   → `slug -> [task view]` (a task with several initiative
                                     tags lands under each; an untagged task lands nowhere).
  * `open_task_count(views)`       → how many of a card's linked tasks are still LIVE work.
  * `linked_tasks_map_result(...)` → `(ok, map)`: fetch + group, keeping the ok flag.
  * `linked_tasks_map(...)`        → the uncached orchestrator: fetch + group, `{}` on failure.
  * `cached_linked_tasks_map(...)` → what the VIEWER calls: the above behind `LinkedTaskCache`
                                     (own TTL + serve-stale-on-failure + single-flight).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
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

# SHORT by design: this read is a DECORATION on a triage page, not core data, so a hung
# clawgate must cost this much WALL-CLOCK time at most (once per CACHE_TTL_SECONDS) and then
# degrade to the last good map / "no linked tasks". Enforced by `_run_with_deadline`, which
# covers every phase (DNS, connect, headers, body) — NOT by `urlopen(timeout=)`, which is
# per-socket-operation and therefore unbounded against a slow drip (see the module docstring).
FETCH_DEADLINE = 2.0  # seconds, wall clock, for the WHOLE fetch

# Per-socket-operation timeout INSIDE the worker thread. This is not the deadline (the join
# is); it exists so an abandoned worker eventually dies instead of holding a socket forever.
SOCKET_TIMEOUT = 2.0  # seconds

# Response size cap. `Notes.List` has no `LIMIT` and each row carries the task's full body, so
# the payload grows with retained history — it only ever goes up.
#
# MEASURED 2026-08-11 against clawgate 0.7.85: `GET /api/tasks` = 94,428 bytes for 10 tasks.
# That is a dated MEASUREMENT, not a standing property — write it that way, because the
# previous note here ("~16 KB for today's 6 tasks", "1 MiB is ~60x today's payload") rotted
# into a false safety argument: it was cited as the reason the read loop below could not
# leak an abandoned worker, and by the time it was re-measured the real body had crossed
# READ_CHUNK and the leak was live. On that date 1 MiB was ~11x the payload, and 94,428 B
# already spans two READ_CHUNKs.
#
# The cap itself stands regardless of the number: an unbounded `resp.read()` on the render
# path is a memory/latency amplifier we don't need.
MAX_RESPONSE_BYTES = 1 << 20  # 1 MiB
READ_CHUNK = 64 * 1024        # max bytes per read1() — the wall clock is re-checked per chunk

# The linked-task cache's OWN TTL — deliberately LONGER than the board's 5s `CACHE_TTL_SECONDS`
# so a hung clawgate costs `FETCH_DEADLINE` at most once per 30s rather than every cold render,
# and so a transient blip serves the last good map instead of flickering the links away. The
# client compensates for the staleness it introduces on the dispatch path by optimistically
# incrementing a card's open-task count after its own successful dispatch (viewer.py
# `noteDispatched`), so two taps in quick succession are still guarded.
CACHE_TTL_SECONDS = 30.0

# Statuses that count as LIVE work for the dispatch guard — a CLOSED set, so anything else
# (`complete`, or a renamed/unknown/missing status) is treated as NOT blocking. Fail-open on
# purpose: a clawgate that renames a status must never wedge dispatch. clawgate's vocabulary
# is exactly {open, in_progress, ready_for_review, complete} (clawgate
# `internal/notes/notes.go`), so today this set is "everything except `complete`" — but it is
# written as a closed ALLOW-list, not as `!= complete`, so a future status is non-blocking by
# default. `ready_for_review` is included because such a task is still unfinished work in the
# queue — dispatching a second card for it would be the exact duplicate this guard prevents.
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

def _run_with_deadline(fn, deadline: float, *, label: str = "clawgate fetch"):
    """Run `fn()` under a REAL WALL-CLOCK DEADLINE and return its result.

    Raises `TimeoutError` if `fn` hasn't finished within `deadline` seconds, and re-raises
    whatever `fn` raised otherwise.

    This — not `urlopen(timeout=)` — is what bounds the fetch. A socket timeout applies to
    each individual operation, so a peer that dribbles a byte just inside the timeout keeps
    the call alive indefinitely (measured: 130s through a `timeout=4` urlopen, and it still
    *succeeded*). Joining a worker thread bounds DNS + connect + headers + body together,
    regardless of what the peer does, and applies to an injected getter too.

    The worker is a DAEMON thread, so an abandoned one can never hold up interpreter exit;
    `_get` re-checks the clock between chunks — using `read1`, which returns whatever is
    already buffered rather than blocking for a full `READ_CHUNK` — so once it reaches the
    BODY it terminates promptly after being abandoned. That is the whole of what `read1`
    buys, and it is real: `read` made termination depend on how long the peer took to
    deliver 64 KiB, which leaked live workers once the payload crossed `READ_CHUNK`.

    🔴 It does NOT make termination bounded, and do not read it as such. `read1` covers the
    body phase only; DNS + connect + HEADERS are still a blocking `urlopen`, and
    `SOCKET_TIMEOUT` is per-OPERATION, so a peer that dribbles the response HEADER one byte
    at a time — every send inside the timeout, so no per-op timeout ever fires — holds the
    worker open indefinitely. That is the same `_slow_drip` shape recorded two paragraphs
    above at +130s, and it is unchanged by this module's `read1`. Measured 2026-08-11 by
    adversarial audit: >60s past a 2.0s deadline AT PRODUCTION CONSTANTS, identical before
    and after `read1`. The blackhole peer that sends nothing at all is the BEST case
    (~0.00s), not the worst — `SOCKET_TIMEOUT` fires promptly there.

    ⚠ `LinkedTaskCache`'s single-flight guard does NOT hold this to one thread: it releases
    `_refresh_lock` when `loader()` RETURNS — i.e. at the deadline — not when the worker
    dies, so any worker outliving the TTL lets the next refresh start another. Measured
    2026-08-11: 7 concurrent `initiatives-clawgate-fetch` threads. Bounding the connect and
    header phases is the open work; this docstring previously claimed both bounds and both
    claims were false."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - ferried to the caller verbatim
            box["error"] = exc

    th = threading.Thread(target=target, name="initiatives-clawgate-fetch", daemon=True)
    th.start()
    th.join(deadline)
    if th.is_alive():
        raise TimeoutError(f"{label} exceeded its {deadline}s wall-clock deadline")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _get(url: str, token: str, *, deadline: float = FETCH_DEADLINE,
         max_bytes: int = MAX_RESPONSE_BYTES, clock=time.monotonic) -> str:
    """Isolated network GET (stdlib urllib) — injected/mocked in tests. Returns the body.

    Runs INSIDE the deadline thread, so its own guards are belt-and-braces for cleanup rather
    than the deadline itself: a per-operation `SOCKET_TIMEOUT`, a wall-clock re-check between
    body chunks (so an abandoned worker stops dribbling — this is why the body is read with
    `read1`, see the loop), and a `max_bytes` cap — the queue endpoint has no server-side
    `LIMIT` and rows carry full task bodies, so an unbounded `resp.read()` on the render path
    is an amplifier we decline.

    `clock` is a SEAM, defaulting to the real monotonic clock, and matches the one
    `LinkedTaskCache` already takes as `now`. It exists because the per-operation socket
    timeout below is `min(SOCKET_TIMEOUT, deadline)`: whenever the caller picks a deadline
    under `SOCKET_TIMEOUT` the two become EQUAL, so a `read1` that blocks past the deadline is
    a coin-flip between this loop's re-check and `socket.timeout` — and `socket.timeout` IS
    `TimeoutError` on py3.10+, so the two are not even distinguishable by type. Nothing in
    production cares which fires (both degrade to "no linked tasks"), but a test that asserts
    WHICH guard ended the read cannot separate them in real time. Injecting the clock lets a
    test pin the deadline DECISION while leaving the socket timeout at its maximum, instead of
    racing the two. Do not use it to extend a deadline — it decides one, it does not widen it.
    """
    started = clock()
    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=min(SOCKET_TIMEOUT, deadline)) as resp:
        code = getattr(resp, "status", None)
        if code is not None and int(code) != 200:
            raise OSError(f"HTTP {code}")
        chunks: list[bytes] = []
        total = 0
        while True:
            if (clock() - started) >= deadline:
                raise TimeoutError(
                    f"body read exceeded the {deadline}s wall-clock deadline "
                    f"after {total} bytes")
            # `read1`, NOT `read`. `read(n)` on a BufferedReader blocks until it has ALL n
            # bytes or hits EOF, so the wall-clock re-check above is unreachable for as long
            # as one whole `READ_CHUNK` is in flight — for any body ≥64 KiB an abandoned
            # worker outlived its deadline by however long the peer took to deliver the
            # chunk. `read1(n)` returns as soon as SOME data is available, so the loop comes
            # back to the clock promptly.
            #
            # The size argument is UNCHANGED and is deliberately an over-read BY ONE:
            # `(max_bytes + 1) - total` lets `total` reach `max_bytes + 1`, which is what
            # makes the check below DETECT an oversized body instead of silently truncating
            # it at the cap. `read1` returns *at most* that many bytes, never more, so the
            # cap semantics are identical.
            chunk = resp.read1(min(READ_CHUNK, (max_bytes + 1) - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise OSError(
                    f"response exceeded the {max_bytes}-byte cap — refusing to buffer it")
        return b"".join(chunks).decode("utf-8", "replace")


def fetch_tasks_result(*, creds: dict | None = None, env=None,
                       deadline: float = FETCH_DEADLINE,
                       getter=None) -> tuple[bool, list[dict]]:
    """`GET {base}/api/tasks` (Bearer hook token) → `(ok, tasks)`.

    `ok` is the bit that `[]` alone cannot carry: `(True, [])` is "clawgate answered and the
    queue has no tagged tasks", `(False, [])` is "the read FAILED". `LinkedTaskCache` needs
    that distinction to serve the last good map on a failure instead of dropping the links.

    BEST-EFFORT: missing/unreadable creds, an unreachable clawgate, a non-200, a blown
    deadline, an oversized body, or a body that isn't a JSON array all log to stderr and
    return `(False, [])`. NEVER raises — a board render must not depend on clawgate."""
    c = creds if creds is not None else load_creds()
    token = ""
    try:
        token = str(c.get("CLAWGATE_HOOK_TOKEN") or "")
    except Exception:  # noqa: BLE001 - a non-mapping creds is just "no creds"
        token = ""
    if not token:
        _log("CLAWGATE_HOOK_TOKEN not set (~/.claude/clawgate.env) — no linked tasks")
        return False, []

    # 🔴 `?summary=1`, on the BOARD RENDER path. It swaps each task's (unbounded)
    # `body` for `commentCount`/`attachmentCount` and keeps everything else —
    # this module reads only `id`, `title`, `directory`, `status` and `tags`, all
    # of which survive it. Measured 2026-08-13 on the live board: 217,379 B full
    # vs 8,088 B summary, a 27x cut. The headroom under MAX_RESPONSE_BYTES had
    # fallen to 4.8x (from 11x when that cap was chosen) and the comment there
    # warns the payload "only ever goes up"; this restores ~130x instead of
    # raising the cap. `?summary=1` is a query flag an older clawgate ignores, so
    # it degrades to the full payload rather than failing.
    url = f"{clawgate_base_url(c, env)}/api/tasks?summary=1"
    get = getter if getter is not None else _get
    try:
        # THE deadline: bounds every phase of the call (and any injected getter), unlike the
        # per-operation socket timeout this replaced.
        raw = _run_with_deadline(lambda: get(url, token, deadline=deadline), deadline,
                                 label=f"GET {url}")
    except urllib.error.HTTPError as exc:
        _log(f"GET {url} failed: HTTP {exc.code} {exc.reason}")
        return False, []
    except TimeoutError as exc:
        _log(f"GET {url} abandoned: {exc}")
        return False, []
    except Exception as exc:  # noqa: BLE001
        _log(f"GET {url} failed: {exc}")
        return False, []

    try:
        obj = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        _log(f"GET {url} returned unparseable JSON ({exc})")
        return False, []
    if not isinstance(obj, list):
        _log(f"GET {url} returned {type(obj).__name__}, expected a list — no linked tasks")
        return False, []
    return True, [t for t in obj if isinstance(t, dict)]


def fetch_tasks(*, creds: dict | None = None, env=None, deadline: float = FETCH_DEADLINE,
                getter=None) -> list[dict]:
    """`fetch_tasks_result` without the ok flag — the raw task list, `[]` on ANY failure."""
    return fetch_tasks_result(creds=creds, env=env, deadline=deadline, getter=getter)[1]


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
    `OPEN_STATUSES` is a closed set — `complete`, or an unknown/missing status → False."""
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
    raises: a non-list input, or a non-dict entry, is skipped.

    ⚠ LATENT RISK, deliberately not fixed: the ledger's identity is `(repo, slug)` but this
    map is keyed on the SLUG ALONE, so two initiatives in different repos that happened to
    share a slug would see each other's tasks. Measured 2026-07-30: `initiatives.latest`
    49 rows / 49 DISTINCT slugs and `initiatives.current` 144 / 144 — zero collisions in
    either view. Closing it means widening the tag namespace to `initiative:<repo>/<slug>`
    on the PRODUCER side (repo-cos + this repo's dispatch.py) plus a migration for every
    already-emitted tag. Not worth it for a decoration; revisit if the store ever shows a
    duplicate slug:
        select slug, count(*) from initiatives.latest group by slug having count(*) > 1;"""
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

def linked_tasks_map_result(*, creds: dict | None = None, env=None,
                            deadline: float = FETCH_DEADLINE,
                            fetcher=None) -> tuple[bool, dict[str, list[dict]]]:
    """Fetch the clawgate queue ONCE and group it into `slug -> [task view]`, keeping the ok
    flag → `(ok, map)`.

    `(False, {})` on ANY failure (no creds, clawgate down, non-200, blown deadline, oversized
    or malformed body). `fetcher` is injectable: it may return either the `(ok, tasks)` tuple
    (`fetch_tasks_result`, the default) or a bare task list (in which case not raising counts
    as success). NEVER raises."""
    try:
        c = creds if creds is not None else load_creds()
        fetch = fetcher if fetcher is not None else fetch_tasks_result
        got = fetch(creds=c, env=env, deadline=deadline)
        if isinstance(got, tuple):
            ok, tasks = got
        else:
            ok, tasks = True, got
        return bool(ok), group_by_slug(tasks, clawgate_base_url(c, env))
    except Exception as exc:  # noqa: BLE001 - best-effort: a failure is an empty map, not a raise
        _log(f"linked_tasks_map failed: {type(exc).__name__}: {exc}")
        return False, {}


def linked_tasks_map(*, creds: dict | None = None, env=None,
                     deadline: float = FETCH_DEADLINE, fetcher=None) -> dict[str, list[dict]]:
    """`linked_tasks_map_result` without the ok flag — the UNCACHED fetch+group, `{}` on any
    failure. The viewer calls `cached_linked_tasks_map` instead; this stays the plain
    orchestrator for the CLI/tests."""
    return linked_tasks_map_result(creds=creds, env=env, deadline=deadline, fetcher=fetcher)[1]


# ---- cache -------------------------------------------------------------------------

class LinkedTaskCache:
    """The linked-task join's OWN cache — independent of the board's 5s snapshot cache.

    Three properties, each closing one of the amplifiers the audit measured:

    * **TTL** (`ttl`, default `CACHE_TTL_SECONDS` = 30s): the fetch deadline is paid at most
      once per TTL, not on every cold render. Without this a hung clawgate re-stalls the board
      every 5s forever.
    * **SERVE-STALE-ON-FAILURE**: a failed refresh keeps serving the LAST GOOD map. A transient
      blip must not make an initiative's linked tasks — and with them the dispatch guard —
      flicker away; the guard is exactly the thing that should NOT disappear when the service
      it guards against is briefly unreachable. `ok=False` is what makes this distinguishable
      from a genuinely empty queue, which is why the fetch carries that flag.
    * **SINGLE-FLIGHT**: at most one refresh runs at a time (a non-blocking lock). A concurrent
      caller gets the current value IMMEDIATELY rather than queueing behind a slow clawgate —
      which, together with the read living outside `DataProvider._lock`, is what stops one hung
      fetch from serializing unrelated board requests.

    Thread-safe. Never raises: `get()` degrades to whatever it last had (or `{}`)."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS, now=time.monotonic):
        self._ttl = ttl
        self._now = now
        self._value: dict[str, list[dict]] = {}
        self._have = False          # has a refresh EVER succeeded? (→ is `_value` real)
        self._attempted_at = None   # monotonic ts of the last refresh ATTEMPT (ok or not)
        self._state_lock = threading.Lock()
        self._refresh_lock = threading.Lock()

    def invalidate(self) -> None:
        """Drop the cached map so the next `get()` re-reads (the ↻ refresh path / tests)."""
        with self._state_lock:
            self._value = {}
            self._have = False
            self._attempted_at = None

    def get(self, loader) -> dict[str, list[dict]]:
        """`loader() -> (ok, map)`; returns the fresh map, the last good one, or `{}`.

        Note the TTL gates the last ATTEMPT, not the last SUCCESS: a failing clawgate is
        retried once per TTL, not on every single request."""
        with self._state_lock:
            if self._attempted_at is not None and (self._now() - self._attempted_at) < self._ttl:
                return self._value
            current = self._value
        if not self._refresh_lock.acquire(blocking=False):
            # A refresh is already in flight. Waiting for it would reintroduce exactly the
            # serialization this design exists to remove, so serve what we have right now.
            return current
        try:
            ok, fresh = loader()
        except Exception as exc:  # noqa: BLE001 - a loader failure is a failed refresh
            _log(f"linked-task refresh raised: {type(exc).__name__}: {exc}")
            ok, fresh = False, {}
        finally:
            self._refresh_lock.release()
        with self._state_lock:
            self._attempted_at = self._now()
            if ok:
                self._value = fresh
                self._have = True
            elif self._have:
                _log("clawgate read failed — serving the last good linked-task map (stale)")
            return self._value


# The process-wide cache the viewer uses. Module-level (not per-DataProvider) so it survives
# a provider rebuild and so every code path shares one clawgate budget.
_CACHE = LinkedTaskCache()


def cached_linked_tasks_map(*, creds: dict | None = None, env=None,
                            deadline: float = FETCH_DEADLINE, fetcher=None,
                            cache=None) -> dict[str, list[dict]]:
    """The one call the VIEWER makes per board render: `slug -> [task view]`, cached.

    Wraps `linked_tasks_map_result` in `LinkedTaskCache` (own 30s TTL + serve-stale-on-failure
    + single-flight). `{}` until the first success; the last good map thereafter, even while
    clawgate is down. NEVER raises — the board renders exactly as it does today on failure."""
    c = cache if cache is not None else _CACHE
    try:
        return c.get(lambda: linked_tasks_map_result(
            creds=creds, env=env, deadline=deadline, fetcher=fetcher))
    except Exception as exc:  # noqa: BLE001 - best-effort to the very last line
        _log(f"cached_linked_tasks_map failed: {type(exc).__name__}: {exc}")
        return {}
