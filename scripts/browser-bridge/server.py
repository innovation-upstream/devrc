#!/usr/bin/env python3
"""browser-bridge server — loopback rendezvous between a Claude skill and the
live Brave browser(s).

This is a SIBLING to the activity-collector's `browser-ext/receiver.py`, NOT a
modification of it. Where the receiver is a one-way telemetry sink (extension
--POST--> server --> spool), this adds a *command* channel that lets Claude Code
drive the user's real, logged-in Brave tab:

    Claude skill --HTTP `POST /cmd`--> THIS server --long-poll--> extension
        --> executes in the active Brave tab --> `POST /result` --> back to skill

Transport choice (documented, deliberate): an MV3 service worker cannot bind a
socket, so the local server is the meeting point. We use an **HTTP long-poll
command queue** rather than a WebSocket:

  * The extension SW issues `GET /poll`, which blocks until a command is queued
    (or ~25s, then 204 → immediate re-poll). A pending fetch keeps the MV3
    worker alive, so this IS the keepalive — no RFC6455 ping needed.
  * The skill's `POST /cmd` enqueues a command, waits (bounded) for the matching
    `POST /result`, and returns it.

Long-poll (vs a hand-rolled stdlib WebSocket) was chosen because the whole
rendezvous is then pure `http.server` + `threading` and is FULLY unit-testable
with stdlib alone against an in-process fake extension — no new pip deps, mirror-
ing the receiver's stdlib-only footprint (the nix unit pins python312).

Multiple instances (per host)
-----------------------------
More than one Brave profile on the same host can each run the extension and be
driven independently. The server keeps a **registry of connected instances**,
each with its OWN command queue. Routing:

  * Each instance has a stable auto-id (`crypto.randomUUID()` persisted by the
    extension in `chrome.storage.local`) and an optional user **label**. The
    effective **routing key** = label if non-empty, else the auto-id. Labels are
    the human key and must be unique per host.
  * `/poll` (extension long-poll) identifies the instance via the
    `X-Bridge-Instance-Id` / `X-Bridge-Label` headers; `/result` echoes its
    `instanceId` in the JSON body; `/cmd` (skill) may carry an optional `target`
    routing key. A command is only ever delivered to ITS instance's `/poll`.
  * **Newest supersedes:** if a NEW connection (different auto-id) registers for
    a routing key that already has a live connection, the old one is dropped —
    any in-flight command on it resolves to a `superseded` error (no orphaned
    waiter). This is what fixes 2× contention from a duplicate/stale connection.
    A superseded connection's own blocked `/poll` returns a **distinct signal**
    (`409 superseded`, NOT the idle `204`) so the extension can back off hard
    instead of hot re-registering — two profiles that share a label would
    otherwise mutually supersede at loopback speed (a livelock). The supersede is
    logged ONCE per displacement (at the displacement site), never per poll.
  * Targeting from the skill: exactly one instance → no `target` needed
    (back-compat). More than one and no `target` → an `ambiguous_instance` error
    listing the instances (never a silent pick). Unknown `target` →
    `unknown_instance`. A `target` matches either the routing key or the auto-id.
  * **Back-compat:** an older extension that polls WITHOUT an instance id (no
    handshake) is assigned a single synthetic auto-id (`LEGACY_INSTANCE_ID`) so
    it still works as one unnamed instance — it never crashes the registry.

Security contract (defeats DNS-rebinding / local malware reaching the socket):
  * Binds 127.0.0.1 only (env `BROWSER_BRIDGE_HOST`, default 127.0.0.1).
  * Bearer-token auth on EVERY endpoint — skill requests and the extension's
    long-poll / result POST alike, including the new instance-scoped params. The
    secret lives in `~/.config/browser-bridge/token`, created 0600 with
    `secrets.token_urlsafe` on first run. 401 otherwise.
  * Host-header allowlist: only 127.0.0.1 / localhost / ::1 accepted (403
    otherwise) — a DNS-rebind victim page hits us with a foreign Host header.

Config (env):
    BROWSER_BRIDGE_HOST          bind host (default 127.0.0.1 — keep loopback)
    BROWSER_BRIDGE_PORT          bind port (default 8788 — must NOT be 8787)
    BROWSER_BRIDGE_TOKEN_FILE    token path (default ~/.config/browser-bridge/token)
    BROWSER_BRIDGE_CMD_TIMEOUT   seconds a /cmd waits for a result (default 20)
    BROWSER_BRIDGE_POLL_TIMEOUT  seconds a /poll blocks before 204 (default 25)
    BROWSER_BRIDGE_RATE_PER_SEC  per-instance sustained /cmd dispatch rate
                                 (token-bucket refill, default 5; 0 → unlimited)
    BROWSER_BRIDGE_BURST         per-instance token-bucket burst size (default 20;
                                 clamped to >=1 when RATE_PER_SEC>0, else a <1
                                 burst would rate_limit EVERY /cmd forever)
    BROWSER_BRIDGE_MAX_QUEUE     per-instance pending-command cap (default 32;
                                 0 → unlimited). Over-cap /cmd → HTTP 429.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

# --- protocol contract (shared with extension/protocol.js — keep in sync) ---- #
# The op set the bridge accepts and DISPATCHES to the extension. Both sides
# validate against this exact set; the JS side mirrors it in
# extension/protocol.js (asserted by the extension test). `open`/`close` were
# added for per-session tab isolation (see the Session isolation block below).
# `text` is a cheap read (visible innerText, optional CSS selector + byte cap) —
# a ~98% token cut vs getHtml, dispatched + tab-scoped exactly like getHtml.
# `frames`/`click`/`type`/`key` are the CDP (chrome.debugger) ops (PR: browser-bridge
# CDP): `frames` enumerates a tab's frames; `click`/`type`/`key` dispatch TRUSTED
# input; and a `--frame` param routes a read (getHtml/text/eval) INTO a cross-origin
# frame. They dispatch + tab-scope exactly like the existing tab-scoped ops. The
# server stays op-agnostic about the CDP mechanics — it only routes to the owned/
# target tab and forwards the typed params (frame/selector/text/key) to the extension.
# `activate` foregrounds the owned/target tab (chrome.tabs.update{active} +
# chrome.windows.update{focused}) so a foreground-throttled SPA loads; it is
# tab-scoped and dispatched like the other tab-scoped ops (its optional `waitMs`
# is a passthrough field, not a routing hint). It is the ONE op that STEALS the
# user's foreground focus (documented as intended, best-effort under i3).
ALLOWED_OPS = ("getHtml", "text", "eval", "tabs", "nav", "screenshot",
               "open", "close", "frames", "click", "type", "key", "activate")

# Ops handled ENTIRELY server-side (never dispatched to the extension). `release`
# relinquishes a session's owned-tab mapping without touching the real Brave tab.
# Not part of the shared JS contract (the extension never sees these).
SERVER_OPS = ("release",)

# Ops that act on ONE specific tab and therefore participate in per-tab FIFO
# serialization (see Registry.submit). `open` (creates a tab), `tabs` (lists all)
# and `release` (server-side) do NOT contend for a single tab.
TAB_SCOPED_OPS = frozenset({"getHtml", "text", "eval", "nav", "screenshot",
                            "close", "frames", "click", "type", "key",
                            "activate"})

# Per-op required fields (skill-supplied). Absent → 400 bad_request. NOTE: `close`
# takes NO skill-supplied field — the server injects the caller's owned tabId (or
# a --tab override); it errors with `no_owned_tab` if the session owns nothing.
REQUIRED_FIELDS = {
    "eval": ("js",),
    "nav": ("url",),
    "click": ("selector",),   # CDP trusted click needs the element selector
    "type": ("text",),        # CDP trusted type needs the text to insert
    "key": ("key",),          # CDP key event needs the key name
}

MAX_CMD_BODY = 256 * 1024      # a command is tiny (op + a url / a js snippet).
MAX_RESULT_BODY = 32 * 1024 * 1024  # a screenshot data URL can be a few MB.

# A connection is considered "present" if a long-poll landed within this window.
CONNECT_STALE_S = 40.0

# Session isolation (fixes concurrent-session tab clobbering)
# ----------------------------------------------------------
# Every op historically targeted "the active tab of the last-focused window", so
# two Claude sessions driving one browser instance interleaved on ONE shared tab
# (A does `nav X` then `getHtml`; B does `nav Y` in between; A reads Y). The
# transport was already correct (cid-correlated, no cross-delivery) — the clobber
# was SEMANTIC: no per-session (multi-step workflow) tab isolation.
#
# Fix: a session may `open` its OWN tab; the server records
# `(instance_key, session_id) -> owned_tab_id` and routes that session's
# tab-scoped ops (html/eval/nav/screenshot/close) to ITS tab. A session with no
# owned tab falls back to the active tab (the one-shot "read the tab I have open"
# path — a single read, inherently safe). `--tab <id>` overrides explicitly.
#
# The session_id is supplied by the `browser` skill on EVERY /cmd via the
# X-Session-Id header (derived from CLAUDE_CODE_SESSION_ID, else $TMUX_PANE, else
# a per-process-tree token — see the skill). It is used for ROUTING ONLY and is
# NEVER trusted for auth (bearer + Host still gate every request). If two sessions
# somehow present the SAME id they would share a tab (documented degradation).
#
# Ownership has an idle TTL: a session that stops calling has its mapping
# reclaimed (released) so dead sessions don't leak ownership forever. Reclaim
# RELEASES ownership but deliberately does NOT close the real Brave tab (never
# yank a visible tab out from under the user); explicit `browser close` closes it.
HDR_SESSION_ID = "X-Session-Id"

# Idle seconds after which a session's tab ownership is reclaimed (released, NOT
# closed). Refreshed on every op the session routes through its owned tab.
OWNER_TTL_S = float(os.environ.get("BROWSER_BRIDGE_OWNER_TTL", "900"))

# Concurrency backstop (bounds the damage a runaway caller can do to the SINGLE
# serial extension connection — the audited 44K-eval storm saturated one queue
# with no backpressure). Enforced PER-INSTANCE (the extension is the bottleneck):
#   * a token-bucket RATE limit on accepted /cmd dispatches, and
#   * a MAX_QUEUE cap on an instance's pending (admitted-but-unfinished) commands.
# Defaults are GENEROUS: a handful of ops per burst is NEVER throttled; only a
# sustained high-rate flood (e.g. the observed ~13/sec) is. Escape hatches for
# power users: RATE_PER_SEC=0 disables the rate limit, MAX_QUEUE=0 disables the
# depth cap. An over-limit dispatch is REJECTED with HTTP 429 (caller-visible
# backpressure) — never silently queued forever.
RATE_PER_SEC = float(os.environ.get("BROWSER_BRIDGE_RATE_PER_SEC", "5"))
BURST = float(os.environ.get("BROWSER_BRIDGE_BURST", "20"))
MAX_QUEUE = int(os.environ.get("BROWSER_BRIDGE_MAX_QUEUE", "32"))

# Synthetic routing key for a legacy extension that polls without a handshake
# (no X-Bridge-Instance-Id). All such polls collapse onto one unnamed instance.
LEGACY_INSTANCE_ID = "legacy"

# Sentinel returned by Registry.poll() when THIS connection was superseded by a
# newer connection that claimed its routing key (a duplicate LABEL on this host,
# or a storage reset). DISTINCT from None (idle timeout) so the HTTP layer can
# answer with a distinct signal (409, not the idle 204) and the extension can
# back off hard instead of hot re-registering (which would livelock two
# same-label profiles at loopback speed).
SUPERSEDED = object()

# Request headers the extension uses to identify its instance on /poll.
HDR_INSTANCE_ID = "X-Bridge-Instance-Id"
HDR_LABEL = "X-Bridge-Label"
HDR_ACTIVE_URL = "X-Bridge-Active-Url"
HDR_ACTIVE_TITLE = "X-Bridge-Active-Title"

_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


# --------------------------------------------------------------------------- #
# Structured logging (JSON lines to stderr, like the receiver's journal usage)
# --------------------------------------------------------------------------- #
def log(event: str, **fields) -> None:
    rec = {"ts": round(time.time(), 3), "event": event, **fields}
    print(json.dumps(rec, ensure_ascii=False, separators=(",", ":")),
          file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Activity telemetry (best-effort, fire-and-forget, METADATA-ONLY)
# --------------------------------------------------------------------------- #
# Each handled command emits ONE event into the personal activity pipeline
# (source="browser-bridge", kind="cmd") so browser-skill usage is first-class
# self-telemetry — queryable in ClickHouse activity.events. This is a DISTINCT
# source from the collector's `browser` (nav/scroll) source; keep them separate.
#
# PRIVACY CONTRACT (do not weaken): we emit ONLY metadata — the op name, the
# instance routing key, the outcome, the server-side latency, and (best-effort)
# the active tab's BARE DOMAIN. We NEVER emit the eval source, page HTML,
# screenshot bytes/data-URLs, a full URL with path/query, or any page content.
# The payload stays tiny.
#
# BEST-EFFORT CONTRACT (do not weaken): emitting must never affect command
# handling. The emitter is discovered lazily by absolute path and every failure
# mode (missing module, unwritable spool, any exception) is swallowed — the
# browser command still succeeds. It runs OFF the critical path (after the HTTP
# response is sent) and only does a local spool append (no network, no fork), so
# it can neither delay nor break a request.

# The activity spool emitter (scripts/collector/keylog/spool_emit.py) is the
# single source of truth for the v1 spool line format, so we DON'T hand-roll it.
# server.py is deployed as a FLAT single-file /nix/store symlink by home-manager,
# so Path(__file__) does NOT sit next to the collector tree (and .resolve() would
# only make that worse — cf. the receiver's documented no-.resolve() gotcha). We
# therefore locate the emitter by its stable ABSOLUTE repo path (identical on
# both hosts) and degrade gracefully if it is absent (collector not checked out →
# telemetry simply off). BROWSER_BRIDGE_SPOOL_EMIT overrides the path (tests).
_SPOOL_EMIT_PATH = Path(
    os.environ.get("BROWSER_BRIDGE_SPOOL_EMIT")
    or (Path.home() / "workspace" / "devrc" / "scripts" / "collector"
        / "keylog" / "spool_emit.py")
)
_spool_emit_mod = None
_spool_emit_tried = False


def _load_spool_emit():
    """Import the activity spool emitter by absolute path, once. Returns the
    module or None (best-effort — a missing/broken emitter just disables
    telemetry, never raises)."""
    global _spool_emit_mod, _spool_emit_tried
    if _spool_emit_tried:
        return _spool_emit_mod
    _spool_emit_tried = True
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "browser_bridge_spool_emit", str(_SPOOL_EMIT_PATH))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _spool_emit_mod = mod
    except Exception:  # noqa: BLE001 — telemetry is strictly best-effort.
        _spool_emit_mod = None
    return _spool_emit_mod


def _domain_from_result(result) -> str:
    """BARE hostname from a command result envelope, for telemetry only.

    Reads ONLY a `url`-named field (never HTML/screenshot bytes) and returns its
    hostname component — NO scheme, NO path, NO query, NO port. A data: URL (a
    screenshot) has no hostname → "". Any problem → "".
    """
    try:
        url = None
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict) and isinstance(data.get("url"), str):
                url = data["url"]
            elif isinstance(result.get("url"), str):
                url = result["url"]
        if not url:
            return ""
        return urlsplit(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


def _session_hash(session_id) -> str:
    """A COARSE, non-reversible fingerprint of a session id: first 8 hex of its
    sha256. Used ONLY in the throttle telemetry event so a flood is attributable
    to a session in activity.events WITHOUT ever storing the raw routing id
    (which is itself never page content, but is still an opaque handle we keep
    out of telemetry). Empty id → "" (nothing to attribute)."""
    if not session_id:
        return ""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8]


def emit_cmd_event(op: str, key: str, outcome: str, duration_ms: int,
                   domain: str = "", exit_code: int = 0, extra: dict = None) -> None:
    """Append ONE metadata-only activity event for a handled command.

    Best-effort + fire-and-forget: any failure is swallowed so telemetry can
    never break command handling. See the PRIVACY / BEST-EFFORT contracts above.
    `extra` merges additional METADATA-ONLY keys into the payload (used by the
    throttle path for {reason, sess} — a fixed reason string + a coarse session
    hash, never page content).
    """
    try:
        se = _load_spool_emit()
        if se is None:
            return
        # METADATA ONLY — op/key/outcome/(bare)domain. Never page content.
        payload = {"op": op, "key": key, "outcome": outcome}
        if domain:
            payload["domain"] = domain
        if extra:
            payload.update(extra)
        se.emit({
            "source": "browser-bridge",
            "kind": "cmd",
            "text": domain or op,
            "duration_ms": int(duration_ms),
            "exit_code": int(exit_code),
            "payload": json.dumps(payload, ensure_ascii=False,
                                  separators=(",", ":")),
        })
    except Exception:  # noqa: BLE001 — strictly best-effort.
        pass


# --------------------------------------------------------------------------- #
# Token
# --------------------------------------------------------------------------- #
def default_token_file() -> Path:
    override = os.environ.get("BROWSER_BRIDGE_TOKEN_FILE")
    if override:
        return Path(override)
    return Path.home() / ".config" / "browser-bridge" / "token"


def load_or_create_token(path: Path) -> str:
    """Return the bearer secret, creating it 0600 on first run.

    The file holds a single `secrets.token_urlsafe(32)` line. Directory is
    created 0700. An existing file is read verbatim (stripped) so restarts keep
    the same token and the loaded extension does not need re-pairing.
    """
    path = Path(path)
    if path.exists():
        tok = path.read_text(encoding="utf-8").strip()
        if tok:
            return tok
        # Empty/corrupt → regenerate below.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tok = secrets.token_urlsafe(32)
    # Create with 0600 from the start (avoid a readable window between create and
    # chmod): open with restrictive mode via os.open.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (tok + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    # Belt-and-suspenders in case the file pre-existed with looser perms.
    os.chmod(path, 0o600)
    return tok


# --------------------------------------------------------------------------- #
# Registry — transport-agnostic rendezvous core (thread-safe, unit-testable)
# --------------------------------------------------------------------------- #
class BridgeTimeout(Exception):
    """A submitted command was not answered within its deadline."""


class NoExtension(Exception):
    """No extension is currently connected to pick up the command."""


class AmbiguousInstance(Exception):
    """More than one instance is connected and no target was specified."""

    def __init__(self, instances):
        super().__init__("ambiguous_instance")
        self.instances = instances


class UnknownInstance(Exception):
    """A target routing key/id was given that no live instance matches."""

    def __init__(self, target, instances):
        super().__init__("unknown_instance")
        self.target = target
        self.instances = instances


class BridgeSuperseded(Exception):
    """The instance servicing an in-flight command was superseded by a newer
    connection with the same routing key."""

    def __init__(self, key):
        super().__init__("superseded")
        self.key = key


class NoOwnedTab(Exception):
    """A `close` was issued by a session that owns no tab on the target instance
    (and gave no explicit --tab). Nothing to close → a clear error, not a guess."""


class RateLimited(Exception):
    """A /cmd dispatch was rejected by the per-instance concurrency backstop —
    either the token bucket is empty (`reason="rate_limited"`) or the instance's
    pending-command depth is at the cap (`reason="queue_full"`). Maps to HTTP 429
    with a `retry_after` hint. Raised (and returned) IMMEDIATELY — it never joins
    the turnstile or blocks, so a runaway caller gets fast, deterministic
    backpressure and can never wedge the FIFO turnstile."""

    def __init__(self, reason, retry_after, key):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after
        self.key = key


class Instance:
    """One connected extension: its own command queue + reply correlation.

    All fields are guarded by the owning Registry's single Condition — never
    mutate an Instance outside `Registry._cond`.
    """

    __slots__ = ("key", "instance_id", "label", "outbox", "results", "waiters",
                 "active_polls", "last_poll", "active_tab", "superseded",
                 "pending", "rl_tokens", "rl_last")

    def __init__(self, key, instance_id, label, now, burst=0.0):
        self.key = key
        self.instance_id = instance_id
        self.label = label
        self.outbox: deque[dict] = deque()   # commands awaiting pickup
        self.results: dict[str, dict] = {}    # id -> result payload
        self.waiters: set[str] = set()        # ids with a live submit() waiting
        self.active_polls = 0
        self.last_poll = now
        self.active_tab = None                # {url,title} best-effort from /poll
        self.superseded = False
        # Concurrency backstop (per-instance, guarded by the Registry's _cond):
        #   pending   = admitted /cmd dispatches not yet completed (depth cap).
        #   rl_tokens = token-bucket balance; starts FULL so the whole burst is
        #               available immediately, refilled at rate_per_sec up to
        #               `burst`. rl_last = last refill clock reading.
        self.pending = 0
        self.rl_tokens = float(burst)
        self.rl_last = now


class Registry:
    """A registry of connected extension instances, each independently routable.

    `poll()` (extension long-poll) registers/refreshes an instance and dequeues
    its next command. `submit()` (skill) routes a command to a target instance
    and blocks for its reply. `deliver_result()` (extension) completes the
    matching `submit()`.

    ThreadingHTTPServer serves each request in its own thread; ALL shared
    mutable state lives on the Instances in `_instances` and is guarded by the
    single `_cond` — blocking here is safe and cheap.
    """

    def __init__(self, clock=time.monotonic, owner_ttl=OWNER_TTL_S,
                 rate_per_sec=RATE_PER_SEC, burst=BURST, max_queue=MAX_QUEUE):
        self._cond = threading.Condition()
        self._instances: dict[str, Instance] = {}   # routing key -> Instance
        self._clock = clock
        # Per-instance concurrency backstop knobs (see the RATE_PER_SEC block).
        # rate_per_sec<=0 → rate limit off; max_queue<=0 → depth cap off.
        self._rate_per_sec = float(rate_per_sec)
        self._burst = float(burst)
        # A sub-1 burst while the rate limit is ACTIVE (rate>0) can never hold a
        # whole token, so `rl_tokens < 1.0` is ALWAYS true → EVERY /cmd returns
        # rate_limited forever (a silent total lockout). Clamp it to a sane floor
        # of 1: a burst<1 with rate>0 is a misconfiguration, NOT a disable path
        # (RATE_PER_SEC=0 is the intended "unlimited" escape hatch, honoured
        # regardless of burst). Log once so the operator sees the correction.
        if self._rate_per_sec > 0 and self._burst < 1:
            log("browser_bridge_burst_clamped", requested=self._burst,
                clamped=1.0, rate_per_sec=self._rate_per_sec)
            self._burst = 1.0
        self._max_queue = int(max_queue)
        # Per-session tab ownership: (instance_key, session_id) -> {tab_id, last_seen}.
        # Guarded by _cond like everything else. An idle TTL reclaims dead sessions.
        self._owners: dict[tuple, dict] = {}
        self._owner_ttl = owner_ttl
        # Per-tab FIFO turnstiles: tab_key -> deque of arrival-ordered tickets.
        # A command holding a tab_key's HEAD ticket is the one allowed to be
        # in-flight; the rest block (in arrival order) until it completes. Only
        # commands that target the SAME tab contend; different tabs never block.
        self._tab_queues: dict[tuple, deque] = {}

    # --- registration ------------------------------------------------------ #
    def _register_locked(self, instance_id: str, label: str,
                         active_tab=None) -> Instance:
        if not instance_id:
            # Legacy extension with no handshake → one synthetic unnamed instance.
            instance_id = LEGACY_INSTANCE_ID
        key = label or instance_id
        # A physical instance that previously registered under a DIFFERENT key
        # (e.g. the user just set/changed its label) leaves a stale entry — drop
        # it so it does not linger as a phantom connection.
        for k, other in list(self._instances.items()):
            if k != key and other.instance_id == instance_id:
                self._supersede_locked(other, "relabeled")
        inst = self._instances.get(key)
        if inst is not None and inst.instance_id != instance_id:
            # A DIFFERENT physical instance is claiming this key → newest wins.
            self._supersede_locked(inst, "superseded")
            inst = None
        if inst is None:
            inst = Instance(key, instance_id, label, self._clock(),
                            burst=self._burst)
            self._instances[key] = inst
        else:
            inst.label = label
            inst.key = key
        if active_tab is not None:
            inst.active_tab = active_tab
        return inst

    def _supersede_locked(self, inst: Instance, reason: str) -> None:
        """Drop `inst` from the registry and wake any submit() waiting on it so
        the in-flight command resolves to a clear error (no orphaned waiter)."""
        inst.superseded = True
        if self._instances.get(inst.key) is inst:
            del self._instances[inst.key]
        self._cond.notify_all()
        log("supersede", key=inst.key, instance_id=inst.instance_id,
            reason=reason)

    # --- per-session tab ownership ---------------------------------------- #
    def _reap_owners_locked(self) -> None:
        """Reclaim (release, do NOT close) ownership for sessions idle past the
        TTL. Called on every ownership touch so dead sessions never leak."""
        now = self._clock()
        dead = [k for k, o in self._owners.items()
                if now - o["last_seen"] > self._owner_ttl]
        for k in dead:
            del self._owners[k]
            log("owner_reclaim", key=k[0], session=k[1])

    def _owned_tab_locked(self, inst_key: str, session_id, *, touch: bool):
        """Return the tab_id this (instance, session) owns, or None. If `touch`,
        refresh its idle timer (any op that routes through the tab keeps it
        alive). Reaps expired owners first so a just-expired mapping reads None."""
        self._reap_owners_locked()
        if session_id is None:
            return None
        o = self._owners.get((inst_key, session_id))
        if o is None:
            return None
        if touch:
            o["last_seen"] = self._clock()
        return o["tab_id"]

    def _effective_tab_locked(self, inst, op, session_id, tab):
        """Resolve (tab_id, tab_key) for a command.

        tab_id is what gets injected into the dispatched command (None → the
        extension uses the active tab). tab_key is the per-tab FIFO turnstile key
        (None → the op does not contend for a single tab). Raises NoOwnedTab for a
        `close` that has neither an explicit --tab nor an owned tab.
        """
        owned = self._owned_tab_locked(inst.key, session_id, touch=True)
        if op not in TAB_SCOPED_OPS:
            # open (creates a tab) / tabs (lists all): never injected, never gated.
            return None, None
        resolved = tab if tab is not None else owned
        if op == "close" and resolved is None:
            raise NoOwnedTab()
        # Active-tab ops with no concrete tab still share ONE physical tab per
        # instance, so they serialize under a synthetic "active" key.
        tab_key = (inst.key, resolved if resolved is not None else "active")
        return resolved, tab_key

    def release_session(self, session_id) -> int:
        """Drop ALL tab ownership held by `session_id` (across every instance)
        WITHOUT closing any real tab. Returns how many mappings were released."""
        if session_id is None:
            return 0
        with self._cond:
            keys = [k for k in self._owners if k[1] == session_id]
            for k in keys:
                del self._owners[k]
            if keys:
                log("owner_release", session=session_id, count=len(keys))
            return len(keys)

    def owners_snapshot(self):
        """Test/introspection helper: {(key,session): tab_id} of live owners
        (expired ones reaped first)."""
        with self._cond:
            self._reap_owners_locked()
            return {k: o["tab_id"] for k, o in self._owners.items()}

    # --- extension side ---------------------------------------------------- #
    def poll(self, instance_id: str, label: str, wait_timeout: float,
             active_tab=None):
        """Long-poll for `instance_id`/`label`: register the instance, then block
        up to `wait_timeout` for ITS next command. Returns the command dict (with
        its id), None on idle timeout, or the SUPERSEDED sentinel if this
        connection got displaced by a newer one sharing its routing key (the HTTP
        layer maps that to a distinct 409 so the extension backs off — not 204)."""
        with self._cond:
            inst = self._register_locked(instance_id, label, active_tab)
            inst.active_polls += 1
            inst.last_poll = self._clock()
            try:
                deadline = self._clock() + wait_timeout
                while True:
                    if inst.superseded:
                        return SUPERSEDED
                    if inst.outbox:
                        return inst.outbox.popleft()
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return None
                    self._cond.wait(remaining)
            finally:
                inst.active_polls -= 1
                inst.last_poll = self._clock()

    def deliver_result(self, cid: str, payload: dict,
                       instance_id: str = None) -> bool:
        """Record a reply for command `cid`. If `instance_id` is given, scope the
        lookup to that instance; otherwise (legacy) search every instance by id.
        Returns False if no submit() awaits it (unknown/expired id)."""
        with self._cond:
            candidates = []
            if instance_id:
                inst = self._find_locked(instance_id)
                if inst is not None:
                    candidates = [inst]
            else:
                candidates = list(self._instances.values())
            for inst in candidates:
                if cid in inst.waiters:
                    inst.results[cid] = payload
                    self._cond.notify_all()
                    return True
            return False

    # --- concurrency backstop (per-instance rate limit + queue-depth cap) --- #
    def _admit_locked(self, inst: Instance):
        """Decide whether `inst` may accept ANOTHER /cmd dispatch RIGHT NOW.

        Runs UNDER `_cond` (caller holds it) and is lock-free of any blocking
        wait — it either admits (spends one token) or rejects immediately. On
        admit the CALLER bumps `inst.pending` as the first line of its try (so
        the increment is structurally paired with the releasing finally — see
        `submit`); this method only spends the token and checks the depth cap.
        Returns None to admit, or `(reason, retry_after)`
        to reject (`reason` ∈ {"queue_full","rate_limited"}). Order: the cheap,
        side-effect-free depth cap first (bounds the latency tail), THEN the
        token bucket (which mutates the balance) — so a queue-full rejection
        never wastes a token. Both knobs independently disable at <= 0.

        STRICTLY PER-INSTANCE: every Instance owns its own `pending`/`rl_tokens`,
        so throttling instance A can never throttle instance B.
        """
        # Queue-depth cap: bound pending (admitted-but-unfinished) commands so a
        # flood can't grow the latency tail unboundedly (the audited 10ms→5.5s).
        if self._max_queue > 0 and inst.pending >= self._max_queue:
            return ("queue_full", 1.0)
        # Token bucket: refill by elapsed*rate (capped at burst), spend one.
        if self._rate_per_sec > 0:
            now = self._clock()
            elapsed = now - inst.rl_last
            if elapsed > 0:
                inst.rl_tokens = min(self._burst,
                                     inst.rl_tokens + elapsed * self._rate_per_sec)
                inst.rl_last = now
            if inst.rl_tokens < 1.0:
                # Time until the next whole token is available — a Retry-After hint.
                retry = (1.0 - inst.rl_tokens) / self._rate_per_sec
                return ("rate_limited", retry)
            inst.rl_tokens -= 1.0
        # Admit. The queue slot (inst.pending) is claimed by the caller inside
        # its try, so the increment can never outlive its releasing finally.
        return None

    # --- skill side -------------------------------------------------------- #
    def submit(self, command: dict, timeout: float, target: str = None,
               session_id=None, tab=None) -> dict:
        """Enqueue `command` (a dict without id) to the resolved target instance
        and block for its reply.

        Session isolation: if the calling `session_id` OWNS a tab on the target
        instance (or `tab` is an explicit override), tab-scoped ops route to that
        tabId; otherwise they fall back to the active tab. Tab-scoped commands
        that target the SAME tab are serialized FIFO in arrival order (`open`
        creates a tab and `tabs` lists all — neither contends). `open`/`close`
        also record/drop the session's ownership on success.

        Raises NoExtension / AmbiguousInstance / UnknownInstance if the target
        cannot be resolved, NoOwnedTab for a `close` with nothing to close,
        BridgeSuperseded if the servicing instance is dropped mid-flight, or
        BridgeTimeout on no reply within `timeout` (the upper bound that keeps a
        FIFO-queued command from blocking forever).
        """
        op = command.get("op")
        with self._cond:
            inst = self._resolve_target_locked(target)
            tab_id, tab_key = self._effective_tab_locked(inst, op, session_id,
                                                         tab)
            # Concurrency backstop: admit (or 429) BEFORE joining the turnstile,
            # so a rejected command never enqueues, never waits, and can't wedge
            # the FIFO. Placed AFTER target/tab resolution so their own errors
            # (no_extension / ambiguous / no_owned_tab) win — those never reached
            # the extension, so they don't spend a token or a queue slot. On
            # admit the queue slot (inst.pending) is claimed inside the try
            # below, structurally paired with the finally that releases it.
            verdict = self._admit_locked(inst)
            if verdict is not None:
                reason, retry_after = verdict
                raise RateLimited(reason, retry_after, inst.key)
            # Idempotent `open`: if this session already owns a tab on the target
            # instance, hand the extension that tabId as `reuseTabId`. The SW
            # returns the SAME tab when it is still live (no second real tab → no
            # orphaned/leaked tab) and only creates a fresh one when the old tab
            # is gone (owned_tab_gone) — so a double `open` never orphans a tab.
            reuse_tab_id = None
            if op == "open" and session_id is not None and tab is None:
                reuse_tab_id = self._owned_tab_locked(inst.key, session_id,
                                                      touch=False)
            deadline = self._clock() + timeout
            ticket = object()
            if tab_key is not None:
                self._tab_queues.setdefault(tab_key, deque()).append(ticket)
            try:
                # Claim the admitted queue slot NOW — the FIRST statement inside
                # the try whose finally releases it (`inst.pending -= 1`). Pairing
                # the increment with its decrement in one try/finally makes the
                # balance structurally leak-proof: ANY raise below (BridgeTimeout,
                # BridgeSuperseded, a resolution error, or future code) still
                # unwinds through the finally, so a phantom `pending` slot can
                # never be stranded (which would eventually wedge the depth cap
                # into a permanent queue_full). Still under `_cond` and before any
                # wait, so the depth cap observes the bump atomically.
                inst.pending += 1
                # (1) Wait for this tab's FIFO turn (no-op when tab_key is None).
                if tab_key is not None:
                    while self._tab_queues[tab_key][0] is not ticket:
                        if inst.superseded:
                            raise BridgeSuperseded(inst.key)
                        remaining = deadline - self._clock()
                        if remaining <= 0:
                            raise BridgeTimeout()
                        self._cond.wait(remaining)
                # (2) Our turn: enqueue the command (with the resolved tabId).
                cid = secrets.token_hex(8)
                cmd = dict(command)
                cmd["id"] = cid
                if tab_id is not None:
                    cmd["tabId"] = tab_id
                if reuse_tab_id is not None:
                    cmd["reuseTabId"] = reuse_tab_id
                inst.outbox.append(cmd)
                inst.waiters.add(cid)
                self._cond.notify_all()  # wake this instance's waiting poller
                while cid not in inst.results:
                    if inst.superseded:
                        inst.waiters.discard(cid)
                        raise BridgeSuperseded(inst.key)
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        inst.waiters.discard(cid)
                        inst.outbox = deque(c for c in inst.outbox
                                            if c.get("id") != cid)
                        raise BridgeTimeout()
                    self._cond.wait(remaining)
                inst.waiters.discard(cid)
                result = inst.results.pop(cid)
                self._record_ownership_locked(inst, op, session_id, tab_id,
                                              result)
                return result
            finally:
                # Release the admitted queue slot (balances _admit_locked's
                # pending bump) on EVERY exit — normal return, timeout, or
                # supersede — so the depth cap can never leak a phantom slot.
                inst.pending -= 1
                # (3) Leave the turnstile and wake the next in line.
                if tab_key is not None:
                    q = self._tab_queues.get(tab_key)
                    if q is not None:
                        try:
                            q.remove(ticket)
                        except ValueError:
                            pass
                        if not q:
                            del self._tab_queues[tab_key]
                    self._cond.notify_all()

    def _record_ownership_locked(self, inst, op, session_id, tab_id,
                                 result) -> None:
        """After an op, reconcile the session's ownership mapping.

        `open` records the (re)used tabId; `close` drops the mapping; a
        tab-scoped op whose OWNED tab turns out to be gone drops the stale
        mapping so the session self-heals to the active-tab fallback.
        """
        if session_id is None or not isinstance(result, dict):
            return
        okey = (inst.key, session_id)
        failed = result.get("ok") is False
        # `close` drops ownership UNCONDITIONALLY: the session asked to end its
        # tab, so the desired end-state is "no mapping" whether the remove
        # succeeded OR the tab was already gone (idempotent). Otherwise a tab the
        # user closed out-of-band would wedge the session to a dead id until the
        # TTL reclaimed it, and `close` could never clear it (remove → ok:false).
        if op == "close":
            if self._owners.pop(okey, None) is not None:
                log("owner_close", key=inst.key, session=session_id,
                    tab=tab_id, ok=(not failed))
            return
        if failed:
            # Self-heal: a tab-scoped op that failed because ITS owned tab is
            # gone → drop the stale mapping so the NEXT command falls back to the
            # active tab instead of re-dispatching to the dead tabId for up to
            # OWNER_TTL. Only evict when the dispatched tab IS the session's owned
            # tab — an explicit --tab to some other gone tab must not evict a
            # healthy owned mapping.
            if _is_tab_gone(result.get("error")):
                o = self._owners.get(okey)
                if o is not None and o["tab_id"] == tab_id:
                    del self._owners[okey]
                    log("owner_tab_gone", key=inst.key, session=session_id,
                        tab=tab_id)
            return  # op threw in the page → nothing else to record
        if op == "open":
            data = result.get("data")
            tid = data.get("tabId") if isinstance(data, dict) else None
            if isinstance(tid, int):
                self._owners[okey] = {
                    "tab_id": tid, "last_seen": self._clock()}
                log("owner_open", key=inst.key, session=session_id, tab=tid)
        elif op == "tabs":
            # Annotate the listing with which tab (if any) THIS session owns, so
            # `browser tabs` can flag it. Metadata only (a tabId, never content).
            data = result.get("data")
            if isinstance(data, dict):
                data["ownedTabId"] = self._owned_tab_locked(
                    inst.key, session_id, touch=False)

    # --- resolution / introspection --------------------------------------- #
    def _live_instances_locked(self):
        now = self._clock()
        live = []
        for inst in self._instances.values():
            if inst.superseded:
                continue
            if inst.active_polls > 0 or (now - inst.last_poll) < CONNECT_STALE_S:
                live.append(inst)
        return live

    def _find_locked(self, target: str):
        for inst in self._instances.values():
            if target in (inst.key, inst.instance_id):
                return inst
        return None

    def _resolve_target_locked(self, target) -> Instance:
        live = self._live_instances_locked()
        if not live:
            raise NoExtension()
        if target:
            for inst in live:
                if target in (inst.key, inst.instance_id):
                    return inst
            raise UnknownInstance(target, [self._describe(i) for i in live])
        if len(live) == 1:
            return live[0]
        raise AmbiguousInstance([self._describe(i) for i in live])

    @staticmethod
    def _describe(inst: Instance) -> dict:
        return {"key": inst.key, "label": inst.label,
                "instanceId": inst.instance_id, "activeTab": inst.active_tab}

    def snapshot(self):
        """A list of currently-live instances (key/label/instanceId/activeTab)."""
        with self._cond:
            return [self._describe(i) for i in self._live_instances_locked()]

    @property
    def connected(self) -> bool:
        with self._cond:
            return bool(self._live_instances_locked())


# --------------------------------------------------------------------------- #
# Small scalar guards (module-level so they're directly unit-testable)
# --------------------------------------------------------------------------- #
def _is_tab_gone(err) -> bool:
    """True if an op-level error string signals the target tab no longer exists.

    The extension raises a clean `owned_tab_gone` (its `chrome.tabs.get` on the
    injected tabId failed); we also match chrome's raw "No tab with id" message
    defensively so an older/edge extension build still triggers the self-heal.
    """
    if not isinstance(err, str):
        return False
    e = err.lower()
    return "owned_tab_gone" in e or "no tab with id" in e


def _coerce_tab(tab):
    """Coerce a raw `tab` request field to a non-negative int tab id.

    Returns (tab_id, None) on success or (None, "bad_tab") on an invalid value.
    A raw token-holder could POST `{"op":"getHtml","tab":[1,2]}`; an unhashable
    `tab` would blow up the per-tab turnstile key with an uncaught TypeError → a
    500. This is the server-side safety net (the `browser` CLI already validates
    `--tab` is numeric) so a malformed `tab` yields a clean 400 instead. `bool`
    is rejected explicitly (it is an `int` subclass but never a real tab id).
    """
    if isinstance(tab, bool):
        return None, "bad_tab"
    if isinstance(tab, int):
        return (tab, None) if tab >= 0 else (None, "bad_tab")
    if isinstance(tab, str) and tab.isdigit():
        return int(tab), None
    return None, "bad_tab"


# --------------------------------------------------------------------------- #
# Command validation
# --------------------------------------------------------------------------- #
def validate_command(body):
    """Return (op, None) if valid, else (None, error_string)."""
    if not isinstance(body, dict):
        return None, "body_not_object"
    op = body.get("op")
    if op not in ALLOWED_OPS and op not in SERVER_OPS:
        return None, "unknown_op"
    for field in REQUIRED_FIELDS.get(op, ()):
        if not body.get(field):
            return None, f"missing_field:{field}"
    return op, None


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
def make_handler(registry: Registry, token: str, cmd_timeout: float,
                 poll_timeout: float):
    class Handler(BaseHTTPRequestHandler):
        server_version = "browser-bridge/2"

        # Suppress the default per-request stderr spam; we log structurally.
        def log_message(self, *a):  # noqa: A003
            pass

        # --- helpers ------------------------------------------------------- #
        def _send(self, code: int, obj=None, raw: bytes = None):
            if raw is None:
                raw = (json.dumps(obj, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8")
                       if obj is not None else b"")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if raw and self.command != "HEAD":
                self.wfile.write(raw)

        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            # Strip the port (host:port or [::1]:port).
            if host.startswith("["):
                hostname = host.split("]")[0] + "]"
            else:
                hostname = host.split(":")[0]
            return hostname in _ALLOWED_HOSTS

        def _auth_ok(self) -> bool:
            hdr = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not hdr.startswith(prefix):
                return False
            presented = hdr[len(prefix):].strip()
            return secrets.compare_digest(presented, token)

        def _guard(self) -> bool:
            """Host + auth gate shared by every endpoint. Returns True to
            proceed; has already sent the rejection response on False."""
            if not self._host_ok():
                log("reject", reason="bad_host", host=self.headers.get("Host"),
                    path=self.path)
                self._send(403, {"ok": False, "error": "bad_host"})
                return False
            if not self._auth_ok():
                log("reject", reason="unauthorized", path=self.path)
                self._send(401, {"ok": False, "error": "unauthorized"})
                return False
            return True

        def _read_body(self, cap: int):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None, "bad_length"
            if length <= 0 or length > cap:
                return None, "bad_length"
            raw = self.rfile.read(length)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None, "bad_json"
            return obj, None

        def _poll_identity(self):
            """(instance_id, label, active_tab) from the /poll request headers.

            Missing instance id → "" (registry assigns the legacy synthetic id).
            Label + active-tab strings are URL-encoded by the extension (header
            values must be ASCII-safe) and decoded here.
            """
            instance_id = (self.headers.get(HDR_INSTANCE_ID) or "").strip()
            raw_label = self.headers.get(HDR_LABEL) or ""
            label = unquote(raw_label).strip() if raw_label else ""
            au = self.headers.get(HDR_ACTIVE_URL)
            at = self.headers.get(HDR_ACTIVE_TITLE)
            active = None
            if au or at:
                active = {"url": unquote(au) if au else None,
                          "title": unquote(at) if at else None}
            return instance_id, label, active

        # --- routes -------------------------------------------------------- #
        def do_GET(self):
            if not self._guard():
                return
            path = urlsplit(self.path).path
            if path == "/health":
                insts = registry.snapshot()
                self._send(200, {"ok": True,
                                 "extension_connected": bool(insts),
                                 "count": len(insts),
                                 "instances": insts})
                return
            if path == "/instances":
                insts = registry.snapshot()
                self._send(200, {"ok": True, "count": len(insts),
                                 "instances": insts})
                return
            if path == "/poll":
                instance_id, label, active = self._poll_identity()
                cmd = registry.poll(instance_id, label, poll_timeout,
                                    active_tab=active)
                if cmd is SUPERSEDED:
                    # Distinct from the idle 204: THIS connection was displaced by
                    # a newer one sharing its routing key (duplicate label). Still
                    # bearer+Host guarded (we are past _guard). The extension must
                    # back off — not hot re-poll — to avoid a mutual-supersede
                    # livelock. Not logged here (logged once at the displacement
                    # site) so a backing-off loser never floods the journal.
                    self._send(409, {"ok": False, "error": "superseded"})
                elif cmd is None:
                    self._send(204)
                else:
                    log("dispatch", id=cmd.get("id"), op=cmd.get("op"),
                        key=(label or instance_id or LEGACY_INSTANCE_ID))
                    self._send(200, cmd)
                return
            self._send(404, {"ok": False, "error": "not_found"})

        def do_POST(self):
            if not self._guard():
                return
            path = urlsplit(self.path).path
            if path == "/cmd":
                self._handle_cmd()
            elif path == "/result":
                self._handle_result()
            else:
                self._send(404, {"ok": False, "error": "not_found"})

        def _handle_cmd(self):
            t0 = time.monotonic()
            body, err = self._read_body(MAX_CMD_BODY)
            if err:
                # Malformed request — never reached an instance; not a real op
                # dispatch, so no telemetry (as with health/instances/poll).
                self._send(400, {"ok": False, "error": err})
                return
            op, verr = validate_command(body)
            if verr:
                # Invalid/unknown op — not one of the five real ops → no event.
                self._send(400, {"ok": False, "error": verr,
                                 "op": body.get("op") if isinstance(body, dict)
                                 else None})
                return
            # `target`/`tab` are skill-side routing hints, NOT part of the command
            # the extension executes — strip them before enqueue. `session_id`
            # (X-Session-Id header) routes to the caller's owned tab; it is used
            # for ROUTING ONLY and is NEVER trusted for auth (bearer + Host already
            # gated this request in _guard).
            target = body.pop("target", None)
            tab = body.pop("tab", None)
            if tab is not None:
                # Guard a malformed `tab` from a raw token-holder (the CLI already
                # validates --tab is numeric): a non-scalar would make an
                # unhashable turnstile key → an uncaught TypeError → 500. Coerce
                # to an int here; an invalid value is a clean 400, not a 500.
                tab, terr = _coerce_tab(tab)
                if terr:
                    self._send(400, {"ok": False, "error": terr})
                    return
            session_id = self.headers.get(HDR_SESSION_ID) or None
            outcome, exit_code, domain = "ok", 0, ""
            # `release` is server-side: drop the session's ownership without ever
            # touching the real Brave tab or the extension.
            if op == "release":
                n = registry.release_session(session_id)
                self._send(200, {"ok": True, "result": {
                    "id": None, "ok": True, "data": {"released": n}}})
                emit_cmd_event(
                    op=op, key=(target or ""), outcome="ok",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    domain="", exit_code=0)
                return
            try:
                result = registry.submit(body, timeout=cmd_timeout,
                                         target=target, session_id=session_id,
                                         tab=tab)
            except RateLimited as e:
                # Per-instance concurrency backstop tripped. Distinct structured
                # log + a telemetry event carrying a COARSE session hash so the
                # NEXT storm is attributable in activity.events without storing
                # the raw session id (the audit couldn't attribute the 44K flood).
                # Returns immediately (no turnstile impact) — caller-visible 429
                # backpressure with a Retry-After-style hint in the body.
                sess = _session_hash(session_id)
                log("throttled", op=op, key=e.key, reason=e.reason, sess=sess)
                self._send(429, {"ok": False, "error": e.reason,
                                 "retry_after": round(e.retry_after, 3)})
                emit_cmd_event(
                    op=op, key=(target or ""), outcome="throttled",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    domain="", exit_code=1,
                    extra={"reason": e.reason, "sess": sess})
                return
            except NoOwnedTab:
                outcome, exit_code = "no_owned_tab", 1
                log("cmd_no_owned_tab", op=op)
                self._send(409, {"ok": False, "error": "no_owned_tab"})
            except NoExtension:
                outcome, exit_code = "no_extension", 1
                log("cmd_no_extension", op=op)
                self._send(503, {"ok": False,
                                 "error": "extension_not_connected"})
            except AmbiguousInstance as e:
                outcome, exit_code = "ambiguous", 1
                log("cmd_ambiguous", op=op, count=len(e.instances))
                self._send(409, {"ok": False, "error": "ambiguous_instance",
                                 "instances": e.instances})
            except UnknownInstance as e:
                outcome, exit_code = "unknown_instance", 1
                log("cmd_unknown_instance", op=op, target=e.target)
                self._send(404, {"ok": False, "error": "unknown_instance",
                                 "target": e.target, "instances": e.instances})
            except BridgeSuperseded as e:
                outcome, exit_code = "superseded", 1
                log("cmd_superseded", op=op, key=e.key)
                self._send(409, {"ok": False, "error": "superseded",
                                 "key": e.key})
            except BridgeTimeout:
                outcome, exit_code = "timeout", 1
                log("cmd_timeout", op=op)
                self._send(504, {"ok": False, "error": "timeout"})
            else:
                domain = _domain_from_result(result)
                log("cmd_ok", op=op)
                self._send(200, {"ok": True, "result": result})
            # Off the critical path: the HTTP response is already sent. Metadata-
            # only + best-effort — cannot delay or break the command (the key is
            # the skill's routing target, empty for the implicit single-instance
            # case). See emit_cmd_event / the PRIVACY + BEST-EFFORT contracts.
            emit_cmd_event(op=op, key=(target or ""), outcome=outcome,
                           duration_ms=int((time.monotonic() - t0) * 1000),
                           domain=domain, exit_code=exit_code)

        def _handle_result(self):
            body, err = self._read_body(MAX_RESULT_BODY)
            if err:
                self._send(400, {"ok": False, "error": err})
                return
            if not isinstance(body, dict) or "id" not in body:
                self._send(400, {"ok": False, "error": "missing_id"})
                return
            cid = body["id"]
            instance_id = body.get("instanceId")
            delivered = registry.deliver_result(cid, body,
                                                instance_id=instance_id)
            if not delivered:
                log("result_unknown_id", id=cid, instance_id=instance_id)
                self._send(200, {"ok": False, "error": "unknown_id"})
                return
            self._send(200, {"ok": True})

    return Handler


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def build_server(host: str, port: int, registry: Registry, token: str,
                 cmd_timeout: float, poll_timeout: float) -> ThreadingHTTPServer:
    handler = make_handler(registry, token, cmd_timeout, poll_timeout)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True  # let shutdown not hang on a blocked long-poll
    return server


def main(argv=None) -> int:
    host = os.environ.get("BROWSER_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("BROWSER_BRIDGE_PORT", "8788"))
    cmd_timeout = float(os.environ.get("BROWSER_BRIDGE_CMD_TIMEOUT", "20"))
    poll_timeout = float(os.environ.get("BROWSER_BRIDGE_POLL_TIMEOUT", "25"))
    token_file = default_token_file()
    token = load_or_create_token(token_file)

    registry = Registry()
    server = build_server(host, port, registry, token, cmd_timeout, poll_timeout)
    log("listening", host=host, port=port, token_file=str(token_file),
        cmd_timeout=cmd_timeout, poll_timeout=poll_timeout)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
