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
    BROWSER_BRIDGE_PING_TIMEOUT  seconds a `ping` /cmd waits when the target
                                 instance is IDLE (default 2). `ping` is the
                                 DIAGNOSTIC op — the thing you run FIRST to ask
                                 "is the loaded extension current?" — so on an
                                 idle instance it must answer "no" fast. When the
                                 instance has work in flight the deadline is
                                 DERIVED from that work instead (never above
                                 CMD_TIMEOUT) so a busy profile is never reported
                                 as dead — see Registry._effective_timeout_locked.
                                 Applies to `ping` ONLY.
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

import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
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
# `wake` UN-THROTTLES the owned/target tab via CDP (Emulation.setFocusEmulation
# Enabled + Page.setWebLifecycleState) WITHOUT moving focus — the non-intrusive
# remedy for "the background tab never rendered". It is tab-scoped and dispatched
# like any other tab-scoped op, with an optional `waitMs` passthrough (the settle
# the extension holds the un-throttle for). CRITICALLY, and unlike `activate`, it
# does NOT trigger the host-side i3 foregrounding below — nothing in the `wake`
# path can take the operator's screen.
# `activate` foregrounds the owned/target tab (chrome.tabs.update{active} +
# chrome.windows.update{focused}) AND (host-side, below) raises the Brave window
# via i3-msg. It is the ONE op that STEALS the operator's screen and is a LAST
# RESORT — `wake` is the answer for throttling. Tab-scoped, dispatched like the
# other tab-scoped ops (its optional `waitMs` is a passthrough field).
# `upload` is a TYPED CDP op (DOM.setFileInputFiles): it populates an
# <input type=file> with a local file whose ABSOLUTE path Chrome reads ITSELF
# (same host) — so NO file bytes cross the bridge. It dispatches + tab-scopes
# exactly like the other CDP ops (own-tab, #189-bounded, scheme-checked); the
# server stays op-agnostic about the CDP mechanics and only forwards the typed
# selector/path/frame. It IS a data-exfil-capable action (an EXPLICIT operator
# decision to allow the autonomous agent any path) → the server AUDIT-LOGS every
# upload (op + target domain + the file path — local metadata, never file CONTENT).
# `ping` is the extension BUILD-FRESHNESS tell (mirrors extension/protocol.js):
# a no-tab, no-page op whose only job is to be a NAME an older build does not
# know, so probing it answers "is the new build loaded?" with unknown_op vs a
# version — instead of two version strings a human has to eyeball.
# `emulate` puts the SESSION'S OWN tab into device emulation (viewport +
# deviceScaleFactor, touch, mobile UA including UA-Client-Hints metadata, and
# media/geolocation/timezone) for real mobile testing. The overrides are
# re-applied by the extension inside every subsequent CDP session because CDP
# emulation dies at debugger detach — which is also why a crashed agent cannot
# leave the operator's browser distorted (see extension/protocol.js EMULATION).
# It is OWNED-TAB-ONLY (below): an agent must never be able to resize a tab the
# operator is using.
ALLOWED_OPS = ("getHtml", "text", "eval", "tabs", "nav", "screenshot",
               "open", "close", "frames", "click", "type", "key", "wake",
               "activate", "upload", "ping", "emulate", "context")

# Ops handled ENTIRELY server-side (never dispatched to the extension). `release`
# relinquishes a session's owned-tab mapping without touching the real Brave tab.
# Not part of the shared JS contract (the extension never sees these).
SERVER_OPS = ("release",)

# Ops that act on ONE specific tab and therefore participate in per-tab FIFO
# serialization (see Registry.submit). `open` (creates a tab), `tabs` (lists all)
# and `release` (server-side) do NOT contend for a single tab.
TAB_SCOPED_OPS = frozenset({"getHtml", "text", "eval", "nav", "screenshot",
                            "close", "frames", "click", "type", "key",
                            "wake", "activate", "upload", "emulate", "context"})

# Ops that may run ONLY against a tab the calling session OWNS (opened via `open`).
# This is the emulation blast-radius rule, enforced at the one place that knows who
# owns what.
#
# Every other tab-scoped op degrades gracefully to "the active tab" when the session
# owns nothing — that is the useful one-shot "read the tab I have open" path, and a
# read is inherently safe. `emulate` is NOT safe that way: it resizes the viewport,
# rewrites the user agent and turns the mouse into a finger. Applied to the tab the
# OPERATOR is looking at, an agent would be reshaping the human's browser. So the
# fallback is removed for it: no owned tab (or a `--tab` pointing anywhere else)
# → a named `not_owned_tab` refusal, never a guess.
#
# Kept as a SET rather than an `if op == "emulate"` because the next op with this
# property must land here and not grow a second copy of the predicate.
OWNED_TAB_ONLY_OPS = frozenset({"emulate"})

# Per-op required fields (skill-supplied). Absent → 400 bad_request. NOTE: `close`
# takes NO skill-supplied field — the server injects the caller's owned tabId (or
# a --tab override); it errors with `no_owned_tab` if the session owns nothing.
REQUIRED_FIELDS = {
    "eval": ("js",),
    "nav": ("url",),
    "click": ("selector",),   # CDP trusted click needs the element selector
    "type": ("text",),        # CDP trusted type needs the text to insert
    "key": ("key",),          # CDP key event needs the key name
    "upload": ("selector", "path"),  # file-input selector + the ABSOLUTE local path
}

MAX_CMD_BODY = 256 * 1024      # a command is tiny (op + a url / a js snippet).
MAX_RESULT_BODY = 32 * 1024 * 1024  # a screenshot data URL can be a few MB.

# A connection is considered "present" if a long-poll landed within this window.
CONNECT_STALE_S = 40.0

# The extension's own hard self-bound on ONE command's execution
# (EXEC_OP_BUDGET_MS in extension/protocol.js). `execute()` never exceeds it and
# never throws, so it is the yardstick for "could a healthy extension still
# legitimately be working on this?". Mirrored here rather than imported because
# the two live in different languages/processes; `test_exec_budget_matches_the_
# extension` reads protocol.js and fails if they ever drift.
EXEC_OP_BUDGET_S = 18.0

# Slack added on top of the execution budget to cover the result POST and the
# next poll turnaround (RESULT_BUDGET_MS is 10s but a healthy POST is ms).
WEDGE_GRACE_S = 2.0

# `ping`'s own /cmd deadline, in seconds. NOT a bare literal at the call site and
# NOT sharing CMD_TIMEOUT: `ping` is the documented FIRST thing you run when you
# suspect the loaded extension is stale, and a diagnostic that takes 20s to say
# "no" is one an operator learns to skip. MEASURED 2026-08-02 over a 2-day window:
# 34 pings, 13 failures (38.2% — the worst rate of any op), of which 6 timed out at
# exactly 20,000ms; the 21 healthy ones averaged 3-4ms.
#
# 🔴 THIS IS THE IDLE DEADLINE ONLY. It is NOT a tuned value that has to dominate
# every op's ceiling — the BUSY case is handled STRUCTURALLY, in submit()'s
# `fast_timeout` gate, which is where the real invariant lives. Read that first.
#
# The distinction the gate draws:
#   * instance has NO outstanding command  -> a ping that does not answer in 2s is
#     genuine evidence of a wedged/dead service worker. Fail FAST. Healthy pings
#     measured at 3-4ms, so 2s is ~500x headroom.
#   * instance HAS outstanding work        -> the poll loop is serial, so the ping
#     simply cannot be dequeued yet. Waiting is CORRECT; the deadline is derived
#     from the work in flight, never from this constant.
#
# So this value never has to exceed ACTIVATE_WAIT_MAX_MS / CDP_OP_BUDGET_MS /
# EXEC_OP_BUDGET_MS — a previous revision of this comment claimed it did, and was
# wrong twice over (it named 8s as "the caller-requestable ceiling" while both
# `screenshot --fullpage` at 15s and `wake --wait 6000` composed with an 8s CDP
# attach exceed it). Deriving the deadline from observed in-flight work removes
# the whole class instead of trying to out-bid it.
#
# Override with BROWSER_BRIDGE_PING_TIMEOUT (raises the IDLE deadline only).
PING_TIMEOUT_DEFAULT = 2.0


def _env_float(name: str, default: float) -> float:
    """A float from the environment, falling back to `default` on absent OR
    unparseable. A malformed knob must not stop the bridge from starting — a
    typo'd BROWSER_BRIDGE_PING_TIMEOUT would otherwise take the whole service
    down at import time with a ValueError."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default

# How long a routing key stays in `known_instances`/`missing` after it stops
# polling. Without a forget path the registry remembers every key for the whole
# process lifetime, so an operator who normally runs ONE profile would get a
# permanent `other: DISCONNECTED` nag from `browser health` forever — a warning
# that is always on is a warning nobody reads. 24h is long enough to still name a
# profile that dropped during a working day, short enough that a profile you
# genuinely stopped using ages out.
KNOWN_FORGET_S = 86400.0

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

# Host-side i3 foregrounding for the `activate` op (see i3_foreground below).
# The Chrome-side activate (chrome.tabs.update{active}+windows.update{focused})
# is a NO-OP for actual VISIBILITY under a tiling WM: a backgrounded tab stays
# `document.visibilityState:"hidden"`, so a foreground-throttled SPA never
# renders. Focusing the matching Brave X11 window via `i3-msg` is what actually
# raises it + switches workspace, un-throttling the tab. Bounded by this timeout;
# any failure is non-fatal (the Chrome-side activate result still returns).
I3_MSG_TIMEOUT = float(os.environ.get("BROWSER_BRIDGE_I3_TIMEOUT", "1.5"))
# Cap on the UNTRUSTED (page-controlled) tab-title fragment used in the i3
# criteria — bounds a pathological title before it is re.escape'd.
I3_TITLE_MAX = 80

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
# The extension's own manifest version (chrome.runtime.getManifest().version),
# reported on every /poll so `whoami` can surface which extension build is loaded
# per instance. Optional — a legacy extension that predates this simply omits it
# (the field stays null in whoami). URL-encoded like the other identity headers.
HDR_EXT_VERSION = "X-Bridge-Ext-Version"
# The extension's own `chrome.runtime.id`. For an UNPACKED extension this is
# derived from the absolute directory Brave loaded it from, so it is the ONLY
# field that distinguishes a repo-path load from a deployed
# ~/.local/share/browser-bridge-ext/ load — both report the same manifest
# version. Optional, exactly like HDR_EXT_VERSION (a build predating it simply
# omits it → the field stays null). URL-encoded like the other identity headers.
# The path→id derivation is MEASURED (2026-08-01): sha256(absolute path), first
# 32 hex chars, each nibble 0-f mapped to a-p, with NO per-profile component.
# Scope: Brave/Chromium on both NixOS hosts, unpacked extensions, two paths.
#     h = hashlib.sha256(path.encode()).hexdigest()[:32]
#     ext_id = "".join(chr(ord("a") + int(c, 16)) for c in h)
# The server nonetheless still only REPORTS the id and never computes an
# expected one — turning this into a real path check is a behaviour change that
# needs its own PR (see extension/README.md "The path→id derivation (MEASURED)").
HDR_EXT_ID = "X-Bridge-Ext-Id"

# Server-side bounds on EVERY extension-supplied /poll string. protocol.js
# already caps them, but a client-side cap only binds an honest client — these
# values arrive over HTTP and are echoed back in every /health, /instances and
# /whoami response, so they get truncated here too.
#   IDENTITY  — instance id, label, ext version, ext id. Generous: a real
#               manifest version is <20 chars and a Chrome extension id is 32.
#   ACTIVE_TAB — the active tab's url/title, which are legitimately long.
#               Mirrors protocol.js MAX_HEADER_VALUE_CHARS.
MAX_IDENTITY_CHARS = 256
MAX_ACTIVE_TAB_CHARS = 2048

_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# --------------------------------------------------------------------------- #
# whoami identity / diagnostics (read-only) — see the GET /whoami handler
# --------------------------------------------------------------------------- #
# A cheap, human-readable server version so `whoami` can report which build is
# answering. Bumped by hand on a meaningful change (it is NOT the extension's
# manifest version). The best-effort git short-HEAD (below) pins the exact commit.
SERVER_VERSION = "whoami-1 (2026-07-30)"

# The repo checkout — identical absolute path on both hosts (workbench + laptop).
# Used to read the extension manifest by its ABSOLUTE path (server.py is deployed
# as a flattened /nix/store symlink, so Path(__file__) does NOT sit next to the
# extension tree — cf. the receiver's documented no-.resolve() gotcha) and to run
# the best-effort git short-HEAD. Both are strictly best-effort → None when absent.
_REPO_DIR = Path.home() / "workspace" / "devrc"
_EXT_MANIFEST_PATH = (_REPO_DIR / "scripts" / "browser-bridge" / "extension"
                      / "manifest.json")

# The STABLE, git-immune deploy target for the unpacked extension, written by
# home-manager activation (a real copy, not a store symlink — see nix/home.nix).
# Brave should be pointed HERE, not at the repo tree: loading it out of the repo
# means any other session's `git checkout`/branch switch swaps the extension code
# out from under a live verification (measured — it silently reverted a staged
# build mid-session). Preferred over the repo manifest when it exists, because it
# is what Brave actually loads; the repo path stays as the fallback so a host that
# has not switched yet (or a bare checkout) keeps reporting a version.
_DEPLOYED_EXT_DIR = Path.home() / ".local" / "share" / "browser-bridge-ext"
_DEPLOYED_EXT_MANIFEST = _DEPLOYED_EXT_DIR / "manifest.json"

# ACTIVITY_HOST source-of-truth file (the activity-collector's env). Parsed as a
# fallback host signal when ACTIVITY_HOST is not in the server's own environment.
_ACTIVITY_COLLECTOR_ENV = Path.home() / ".config" / "activity-collector" / "env"

# LAN-IP → host label, mirroring ship.sh detect_role: primary 192.168.50.x plus
# the 10.42.0.x nebula fallbacks. Precedence (primary before secondary, workbench
# before laptop within a pass) matches ship.sh so both agree on a host's identity.
_HOST_IP_ORDER = (
    ("192.168.50.250", "workbench"),
    ("192.168.50.155", "laptop"),
    ("10.42.0.30", "workbench"),
    ("10.42.0.100", "laptop"),
)


def _normalize_host_label(value) -> str:
    """A host label is only ever `laptop` or `workbench`; anything else → ""."""
    v = (value or "").strip().lower()
    return v if v in ("laptop", "workbench") else ""


def _parse_activity_host(text) -> str:
    """Extract the ACTIVITY_HOST value from an activity-collector env file body
    (`KEY=value` lines; quotes stripped). "" when absent/unparseable."""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ACTIVITY_HOST="):
            val = line[len("ACTIVITY_HOST="):].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            return val.strip()
    return ""


def _host_from_ips(ips) -> str:
    """The host label implied by a machine's IPv4 list, by ship.sh precedence, or
    "" when none of the known LAN/nebula addresses is present."""
    ipset = set(ips or [])
    for ip, label in _HOST_IP_ORDER:
        if ip in ipset:
            return label
    return ""


def local_ipv4s() -> list:
    """Best-effort non-loopback IPv4 addresses of THIS machine (sorted, unique).

    Stdlib-only + never raises: combines the hostname's A records with the
    default-route source address (a UDP `connect` that sends no packets), and
    drops loopback. Empty list if nothing resolves — whoami then reports the host
    from the env/file signals or `unknown`."""
    import socket
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                out.add(ip)
    except Exception:  # noqa: BLE001 — best-effort.
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 9))   # no packet sent; picks the egress iface
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                out.add(ip)
        finally:
            s.close()
    except Exception:  # noqa: BLE001 — best-effort.
        pass
    return sorted(out)


def resolve_host(env=None, collector_env_path=None, ips=None) -> dict:
    """Identify which host the bridge is running on: {label, source, ips}.

    Resolution order (each input is injectable so this is fully unit-testable):
      (a) ACTIVITY_HOST in `env`                    → source="activity_host_env"
      (b) ACTIVITY_HOST= parsed from the activity-  → source="activity_collector_file"
          collector env file (if readable)
      (c) IP-detect from the machine's LAN IPv4s    → source="ip"
          (mirrors ship.sh detect_role's mapping)
      else                                          → label="unknown", source="unknown"

    `label` ∈ {laptop, workbench, unknown}. `ips` is the machine's non-loopback
    IPv4s (or the injected list). Never raises."""
    env = env if env is not None else os.environ
    ips = list(ips) if ips is not None else local_ipv4s()

    label = _normalize_host_label(env.get("ACTIVITY_HOST"))
    if label:
        return {"label": label, "source": "activity_host_env", "ips": ips}

    path = (collector_env_path if collector_env_path is not None
            else _ACTIVITY_COLLECTOR_ENV)
    try:
        p = Path(path)
        text = p.read_text(encoding="utf-8") if p.is_file() else None
    except Exception:  # noqa: BLE001 — unreadable file → skip to IP detection.
        text = None
    if text:
        label = _normalize_host_label(_parse_activity_host(text))
        if label:
            return {"label": label, "source": "activity_collector_file",
                    "ips": ips}

    label = _host_from_ips(ips)
    if label:
        return {"label": label, "source": "ip", "ips": ips}

    return {"label": "unknown", "source": "unknown", "ips": ips}


def git_short_head(repo=None, timeout: float = 1.0):
    """Best-effort short git HEAD of the repo checkout, or None when unavailable.

    subprocess with an ARGV LIST + shell=False (no shell surface) and a short
    timeout; ANY failure (git absent, not a repo, timeout, nonzero) → None. Never
    fatal — `whoami` still returns without it."""
    repo = Path(repo) if repo is not None else _REPO_DIR
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            shell=False, capture_output=True, timeout=timeout, text=True)
    except Exception:  # noqa: BLE001 — best-effort.
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    head = (proc.stdout or "").strip()
    return head or None


def _read_manifest_version(path):
    """The `version` string in the manifest.json at `path`, or None (missing /
    unreadable / malformed). Best-effort — never raises."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        v = data.get("version")
        return v if isinstance(v, str) else None
    except Exception:  # noqa: BLE001 — best-effort.
        return None


def manifest_version(path=None):
    """The version Brave is EXPECTED to have loaded: the deployed extension's
    manifest (`~/.local/share/browser-bridge-ext/`) when present, else the repo
    manifest. Absolute paths on purpose — do NOT .resolve() a symlinked
    server.py; it is deployed as a flattened /nix/store symlink and does not sit
    next to either extension tree. None when neither is readable."""
    if path is not None:
        return _read_manifest_version(path)
    return (_read_manifest_version(_DEPLOYED_EXT_MANIFEST)
            or _read_manifest_version(_EXT_MANIFEST_PATH))


def annotate_staleness(instances, expected):
    """Add an explicit loaded-vs-expected verdict to each instance descriptor,
    IN PLACE, and return the list. `expected` is the manifest version the server
    expects (manifest_version(), which may itself be None).

    `extension_stale` is a yes/no, not two strings to eyeball:
      True  — this instance reports a version that differs from `expected`
              (the loaded build is not the deployed one → reload/restart it)
      False — they match
      None  — undecidable: the instance predates version reporting, or no
              manifest is readable. NEVER guess in that case.

    A False here means the VERSION matches; it is not proof the code matches
    (an unbumped change looks identical). That is exactly why the extension also
    carries the `ping` op: a new op name cannot be faked by an old build.
    """
    for inst in instances:
        loaded = inst.get("extension_version")
        inst["extension_version_expected"] = expected
        inst["extension_stale"] = (
            None if (not expected or not loaded) else loaded != expected)
    return instances


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


def _emulate_extra(body) -> dict:
    """METADATA-ONLY telemetry fields for an `emulate` command.

    Deliberately narrow, and the exclusions are the interesting part:
      * the UA STRING is not emitted — it is long, operator-supplied free text, and
        the preset name already identifies it for every non-raw call;
      * geolocation COORDINATES are not emitted — an emulated lat/lon is a location
        the operator chose to pretend to be at, which is not the bridge's business
        to record. Only whether one was set.
      * no URL, no title, no page content (the generic contract for every event).
    What IS emitted is what makes a stuck override diagnosable later: the preset
    name and the viewport.
    """
    if not isinstance(body, dict):
        return {}
    if body.get("reset"):
        return {"emu_reset": True}
    out = {"emu_device": body.get("device") or "raw"}
    for src, dst in (("width", "emu_width"), ("height", "emu_height"),
                     ("dsf", "emu_dsf")):
        v = body.get(src)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[dst] = v
    for src, dst in (("mobile", "emu_mobile"), ("touch", "emu_touch")):
        v = body.get(src)
        if isinstance(v, bool):
            out[dst] = v
    out["emu_geo"] = bool(body.get("geo"))
    return out


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


def _emit_diag_event(op: str, t0: float) -> None:
    """One metadata-only event for a read-only DIAGNOSTIC GET (/whoami, /health).

    A thin, deliberately narrow wrapper over emit_cmd_event: op + outcome + latency
    and NOTHING else. No key (these endpoints take no --instance) and NO domain —
    they are global, describing every connected profile at once, so there is no
    single active domain to attribute and emitting per-profile domains would widen
    the privacy contract. Best-effort like every other emit: it cannot raise.
    """
    emit_cmd_event(op=op, key="", outcome="ok",
                   duration_ms=int((time.monotonic() - t0) * 1000),
                   domain="", exit_code=0)


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


class NotOwnedTab(Exception):
    """An OWNED_TAB_ONLY_OPS op (today: `emulate`) targeted a tab this session does
    not own — it owns none at all, or `--tab` named a different one.

    DISTINCT from NoOwnedTab on purpose. NoOwnedTab means "you have nothing to act
    on"; this means "that tab is not yours", which is a refusal with a security
    reason behind it and deserves its own name in the logs, the HTTP body and the
    CLI's guidance. Collapsing the two would tell an operator to run `browser open`
    when the real problem is that they pointed `--tab` at their own window."""


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
                 "pending", "rl_tokens", "rl_last", "extension_version",
                 "extension_id", "last_poll_wall", "lost_logged",
                 "last_dispatch", "inflight")

    def __init__(self, key, instance_id, label, now, burst=0.0,
                 extension_version=None, extension_id=None):
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
        # The extension's manifest version, best-effort from the /poll identity
        # header (None until a build that reports it polls). Surfaced by whoami.
        self.extension_version = extension_version
        # chrome.runtime.id — WHICH DIRECTORY this extension was loaded from
        # (path-derived for an unpacked extension). None until a build that
        # reports it polls. Reported only; never compared to a computed expected.
        self.extension_id = extension_id
        # Concurrency backstop (per-instance, guarded by the Registry's _cond):
        #   pending   = admitted /cmd dispatches not yet completed (depth cap).
        #   rl_tokens = token-bucket balance; starts FULL so the whole burst is
        #               available immediately, refilled at rate_per_sec up to
        #               `burst`. rl_last = last refill clock reading.
        self.pending = 0
        self.rl_tokens = float(burst)
        self.rl_last = now
        # --- silent-drop detector (see _live_instances_locked) --------------- #
        # `last_poll` is on the registry's MONOTONIC clock, which is meaningless
        # to a human reading `browser health`. Keep a wall-clock copy purely for
        # the "last seen 18:02:34" rendering; never used for liveness math.
        self.last_poll_wall = time.time()
        # Edge-trigger for the `instance_lost` / `instance_connected` log events —
        # log ONCE per transition, not once per health probe.
        self.lost_logged = False
        # The last command handed to this instance that has NOT produced a result:
        # {"id","op","at"} or None. This is the field that would have NAMED
        # `frames` as the wedging op on 2026-07-29 without any code-reading.
        # ⚠ DIAGNOSTIC ONLY — do NOT do deadline math on it. It is overwritten by
        # each new enqueue (so it names the NEWEST outstanding command, not the
        # oldest) and its `at` is wall-clock `time.time()`, not the registry's
        # injectable monotonic clock. `inflight` below is the field for timing.
        self.last_dispatch = None
        # cid -> MONOTONIC enqueue reading, for every command handed to this
        # instance that has not yet produced a result. Unlike `last_dispatch` this
        # keeps EVERY outstanding command, so `min(...)` is the age of the oldest
        # one — which is what tells a legitimately BUSY extension apart from a
        # WEDGED one (see Registry.submit's `fast_timeout`).
        self.inflight: dict[str, float] = {}


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
                         active_tab=None, extension_version=None,
                         extension_id=None) -> Instance:
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
                            burst=self._burst,
                            extension_version=extension_version,
                            extension_id=extension_id)
            self._instances[key] = inst
        else:
            inst.label = label
            inst.key = key
        if active_tab is not None:
            inst.active_tab = active_tab
        # Only overwrite a known version — a legacy poll (no header) must not wipe
        # a version an earlier poll already reported.
        if extension_version is not None:
            inst.extension_version = extension_version
        if extension_id is not None:
            inst.extension_id = extension_id
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
        # Blast-radius gate for OWNED_TAB_ONLY_OPS. The session must own a tab AND
        # the resolved target must BE that tab — so an explicit `--tab <id>` cannot
        # be used to reach around ownership onto one of the operator's own tabs.
        if op in OWNED_TAB_ONLY_OPS and (owned is None or resolved != owned):
            raise NotOwnedTab()
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
             active_tab=None, extension_version=None, extension_id=None):
        """Long-poll for `instance_id`/`label`: register the instance, then block
        up to `wait_timeout` for ITS next command. Returns the command dict (with
        its id), None on idle timeout, or the SUPERSEDED sentinel if this
        connection got displaced by a newer one sharing its routing key (the HTTP
        layer maps that to a distinct 409 so the extension backs off — not 204)."""
        with self._cond:
            inst = self._register_locked(instance_id, label, active_tab,
                                         extension_version, extension_id)
            inst.active_polls += 1
            inst.last_poll = self._clock()
            inst.last_poll_wall = time.time()
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
                inst.last_poll_wall = time.time()

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

    def _effective_timeout_locked(self, inst, timeout, fast_timeout):
        """The deadline for a command that wants to FAIL FAST on a wedge without
        false-negativing a merely BUSY extension. Returns `timeout` unchanged when
        `fast_timeout` is None (every op except `ping`).

        🔴 THIS IS THE REAL INVARIANT behind the ping deadline. The extension's
        poll loop is strictly SERIAL (service_worker.js: `await execute()` ->
        `await postResult()` -> next `pollOnce()`), so `ping` skips the per-tab
        FIFO but still cannot be DEQUEUED until the work ahead of it finishes.
        Timing out while that work is legitimately in progress reports a healthy
        profile as dead — and the documented remedy for "dead" is a FULL Brave
        restart of the operator's live session.

        The signal is `inst.inflight`: every command handed to this instance that
        has not produced a result, stamped on the registry's MONOTONIC clock.
          * empty            -> nothing can be ahead of the ping. A ping that then
                                fails to answer within `fast_timeout` IS the wedge.
          * non-empty        -> allow the work in flight its own budget. Each
                                command is self-bounded by EXEC_OP_BUDGET_S, and
                                they drain serially, so N of them can legitimately
                                take N * EXEC_OP_BUDGET_S; subtract how long the
                                oldest has already been outstanding.

        CLAMPED ON BOTH SIDES, and both clamps are load-bearing:
          * never above `timeout` (cmd_timeout) — so this can NEVER be slower than
            the behaviour that predates the whole fast-ping change;
          * never below `fast_timeout` — so a wedged instance whose budget has gone
            negative still gets its full fast deadline rather than an instant fail.

        WHY NOT `inst.last_dispatch`, which looks like it carries the same fact:
        it is overwritten by every new enqueue, so it names the NEWEST outstanding
        command rather than the oldest — a fresh enqueue behind a wedged one makes
        the wedge look young. Its `at` is also wall-clock `time.time()`, not this
        clock, so it cannot be compared against a monotonic deadline (and an NTP
        step would move it). It stays a DIAGNOSTIC field; `inflight` does timing.
        """
        if fast_timeout is None:
            return timeout
        if not inst.inflight:
            return fast_timeout
        age = self._clock() - min(inst.inflight.values())
        budget = len(inst.inflight) * EXEC_OP_BUDGET_S + WEDGE_GRACE_S - age
        return max(fast_timeout, min(timeout, budget))

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
               session_id=None, tab=None, fast_timeout: float = None) -> dict:
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
        NotOwnedTab for an OWNED_TAB_ONLY_OPS op (`emulate`) aimed at a tab this
        session does not own,
        BridgeSuperseded if the servicing instance is dropped mid-flight, or
        BridgeTimeout on no reply within `timeout` (the upper bound that keeps a
        FIFO-queued command from blocking forever).

        `fast_timeout` (used only by `ping`) asks for a SHORTER deadline when the
        target instance has nothing in flight, so a wedge is reported quickly
        without ever false-negativing a busy one. See _effective_timeout_locked —
        that is where the invariant lives.
        """
        op = command.get("op")
        with self._cond:
            inst = self._resolve_target_locked(target)
            # BEFORE our own enqueue: the work already in flight is what decides
            # whether a stalled reply means "wedged" or "not our turn yet".
            timeout = self._effective_timeout_locked(inst, timeout, fast_timeout)
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
            # Declared BEFORE the try so the finally can always reference it: the
            # FIFO wait can raise before a cid exists.
            cid = None
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
                # Outstanding-work clock for _effective_timeout_locked. Released
                # in the finally below, paired exactly like `pending`.
                inst.inflight[cid] = self._clock()
                # Remember what we handed this instance. Cleared only when a
                # RESULT comes back, so if the instance goes silent this field
                # still names the command it never answered — the single fact
                # that turns the next silent drop from inference into evidence.
                inst.last_dispatch = {"id": cid, "op": op, "at": time.time()}
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
                # Answered → no longer the outstanding command.
                if (inst.last_dispatch or {}).get("id") == cid:
                    inst.last_dispatch = None
                self._record_ownership_locked(inst, op, session_id, tab_id,
                                              result)
                return result
            finally:
                # Release the admitted queue slot (balances _admit_locked's
                # pending bump) on EVERY exit — normal return, timeout, or
                # supersede — so the depth cap can never leak a phantom slot.
                inst.pending -= 1
                # Same structural pairing for the outstanding-work clock. A
                # stranded `inflight` entry would be WORSE than a stranded pending
                # slot: it would make the instance look permanently busy, and
                # `ping` would never fast-fail on that instance again.
                if cid is not None:
                    inst.inflight.pop(cid, None)
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
            alive = (inst.active_polls > 0
                     or (now - inst.last_poll) < CONNECT_STALE_S)
            # Edge-triggered drop/return detector. Evaluated here because this is
            # the ONE place liveness is decided — a separate reaper thread would
            # be a second definition of "live" that could disagree with this one.
            #
            # Before this, a silent drop left NO trace anywhere: the server logs
            # `dispatch`/`cmd_ok` but had no event for "an instance I knew about
            # stopped polling", so a drop was invisible unless somebody happened
            # to send a command into it (the operator's second drop of 2026-07-31
            # produced no journal line at all for exactly that reason).
            if alive:
                live.append(inst)
                if inst.lost_logged:
                    inst.lost_logged = False
                    log("instance_connected", key=inst.key,
                        instance_id=inst.instance_id)
            elif not inst.lost_logged:
                inst.lost_logged = True
                d = inst.last_dispatch or {}
                log("instance_lost", key=inst.key,
                    instance_id=inst.instance_id,
                    stale_s=round(now - inst.last_poll, 1),
                    # The last command dispatched to it that never came back —
                    # empty when it went quiet while idle.
                    last_op=d.get("op") or "", last_id=d.get("id") or "")
        return live

    def _known_instances_locked(self):
        """Every routing key this process has seen and not superseded, each with
        whether it is live RIGHT NOW.

        This is what makes a dead named instance visible. `extension_connected`
        is a bare OR over live instances, so one healthy profile reports the
        bridge as up while `work` has been gone for an hour; it cannot be
        redefined without breaking callers that legitimately ask "is anything
        up", so the honest answer is carried ALONGSIDE it.
        """
        live_ids = {id(i) for i in self._live_instances_locked()}
        now = self._clock()
        out = []
        for inst in self._instances.values():
            if inst.superseded:
                continue
            # Age out a key nobody has used in a day — see KNOWN_FORGET_S.
            if id(inst) not in live_ids and (now - inst.last_poll) > KNOWN_FORGET_S:
                continue
            d = inst.last_dispatch or {}
            out.append({
                "key": inst.key, "label": inst.label,
                "instanceId": inst.instance_id,
                "connected": id(inst) in live_ids,
                "last_seen": _iso_utc(inst.last_poll_wall),
                "last_seen_age_s": round(self._clock() - inst.last_poll, 1),
                "last_unanswered_op": d.get("op") or None,
            })
        return out

    def known_snapshot(self):
        """(known_instances, missing) — every seen key + the subset now gone."""
        with self._cond:
            known = self._known_instances_locked()
        return known, [k for k in known if not k["connected"]]

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
        # extension_version (best-effort, from the /poll X-Bridge-Ext-Version
        # header; None until a build that reports it polls) is surfaced in /health
        # + /instances too so `browser health` shows which extension BUILD is loaded
        # — the signal that distinguishes a stale extension from a missing op.
        # extension_id (chrome.runtime.id) says WHICH DIRECTORY that build was
        # loaded from — version alone cannot tell a repo-path load from a
        # deployed-path one, since both report the same manifest version.
        return {"key": inst.key, "label": inst.label,
                "instanceId": inst.instance_id, "activeTab": inst.active_tab,
                "extension_version": inst.extension_version,
                "extension_id": inst.extension_id}

    def snapshot(self):
        """A list of currently-live instances (key/label/instanceId/activeTab)."""
        with self._cond:
            return [self._describe(i) for i in self._live_instances_locked()]

    @staticmethod
    def _describe_whoami(inst: Instance) -> dict:
        """A METADATA-ONLY per-instance descriptor for whoami: the routing
        key/label/instanceId, the active tab's BARE DOMAIN (never the full URL —
        #173 metadata-only), the reported extension_version (None if the
        instance's extension predates version reporting) and its extension_id
        (chrome.runtime.id — which DIRECTORY the build was loaded from; local
        metadata, not browsing data)."""
        at = inst.active_tab if isinstance(inst.active_tab, dict) else {}
        url = at.get("url")
        domain = ""
        if isinstance(url, str) and url:
            try:
                domain = urlsplit(url).hostname or ""
            except Exception:  # noqa: BLE001
                domain = ""
        return {"key": inst.key, "label": inst.label,
                "instanceId": inst.instance_id,
                "activeTabDomain": domain,
                "extension_version": inst.extension_version,
                "extension_id": inst.extension_id}

    def whoami_snapshot(self):
        """Per-live-instance whoami descriptors (metadata only — domain, not URL)."""
        with self._cond:
            return [self._describe_whoami(i)
                    for i in self._live_instances_locked()]

    def rate_limit_config(self) -> dict:
        """The per-instance concurrency-backstop knobs, for whoami diagnostics."""
        return {"per_sec": self._rate_per_sec, "burst": self._burst,
                "max_queue": self._max_queue}

    @property
    def connected(self) -> bool:
        with self._cond:
            return bool(self._live_instances_locked())


# --------------------------------------------------------------------------- #
# Small scalar guards (module-level so they're directly unit-testable)
# --------------------------------------------------------------------------- #
def _iso_utc(wall: float) -> str:
    """A wall-clock epoch as an ISO-8601 UTC string, for human-readable
    `last seen` rendering. Never used for liveness math (that is monotonic)."""
    try:
        return (datetime.datetime.fromtimestamp(wall, datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"))
    except Exception:  # noqa: BLE001
        return ""


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
# Host-side i3 foregrounding for `activate` (untrusted-title-safe, best-effort)
# --------------------------------------------------------------------------- #
# The `activate` op sets the tab active Chrome-side, which makes the tab's Brave
# X11 window TITLE become the tab's title. The SERVER (same host as the browser)
# then focuses THAT Brave window via `i3-msg` so i3 actually raises it + switches
# workspace — the step that turns activation from a no-op into real visibility.
#
# SECURITY — the title is UNTRUSTED (page-controlled; a hostile page the agent
# visited can set `document.title` to ANYTHING). The bound MUST be: the worst
# outcome is "focuses the wrong Brave window or no window", NEVER command
# execution and NEVER focusing a non-Brave window. That is enforced by:
#   * subprocess with an ARGV LIST + shell=False (no shell → no `;`/`|`/`$()`/
#     redirection surface at all). NEVER os.system / shell=True.
#   * The i3 criteria `title=` value is a REGEX delimited by double-quotes inside
#     the criteria. re.escape() neutralises regex metacharacters but does NOT
#     escape the `"`/`[`/`]`/`\` that STRUCTURE an i3 criteria — a `"` could close
#     the quoted value early and let trailing text be read as an i3 command
#     (e.g. `exec`). So we STRIP those structural chars (and control chars) BEFORE
#     re.escape. After that the value cannot break out of `title="..."`.
#   * A `class="Brave-browser"` constraint so a matched window is always Brave.
#   * A length cap + a bounded timeout; failure/timeout is swallowed (non-fatal).
# i3-only chars that i3 uses to delimit a criteria/value. Stripped from the
# UNTRUSTED title BEFORE re.escape so the value cannot escape its `title="..."`
# quoting (re.escape does not touch `"`). Removing them at worst makes the title
# match a different window or none — never a breakout.
_I3_STRUCTURAL = str.maketrans("", "", '"[]\\')


def _sanitize_i3_title(title) -> str:
    """Reduce an UNTRUSTED tab title to a safe i3 criteria fragment.

    Strips control chars / newlines AND the i3-criteria structural chars
    (``"[]\\``), then caps length. The remaining text is re.escape'd by the
    caller so regex metacharacters match literally. Returns "" when nothing
    usable remains (the caller then SKIPS — there is nothing to focus)."""
    if not isinstance(title, str):
        return ""
    # Drop C0 control chars (incl. \n/\r/\t) and DEL.
    cleaned = "".join(ch for ch in title if ch >= " " and ch != "\x7f")
    cleaned = cleaned.translate(_I3_STRUCTURAL)
    return cleaned.strip()[:I3_TITLE_MAX]


def i3_focus_argv(title):
    """Build the argv LIST for `i3-msg` to focus the Brave window whose title
    matches `title`, or None when there is no usable title (→ skip).

    shell=False BY CONSTRUCTION (a list, never a shell string). The criteria is
    `[class="Brave-browser" title="<re.escape'd fragment>"] focus`, so the worst
    case is "focuses the wrong Brave window or none" — never a non-Brave window,
    never code execution. See the module block above for the threat model."""
    safe = _sanitize_i3_title(title)
    if not safe:
        return None
    criteria = '[class="Brave-browser" title="%s"] focus' % re.escape(safe)
    return ["i3-msg", criteria]


# Well-known absolute locations for `i3-msg`, tried IN ORDER when it is not on
# PATH. The browser-bridge systemd --user service runs with a MINIMAL PATH
# (python3 + coreutils) that does NOT include /run/current-system/sw/bin, where
# i3-msg actually lives — so `shutil.which("i3-msg")` returns None IN-SERVICE
# even on a graphical i3 host, and `activate` silently reported i3:"skipped".
# Resolving i3-msg by these absolute paths makes the host-side foregrounding
# work regardless of the (possibly minimal) service PATH — belt-and-suspenders
# with the nix unit's PATH fix (which puts ${pkgs.i3}/bin on PATH).
_I3_MSG_FALLBACKS = (
    "/run/current-system/sw/bin/i3-msg",
    str(Path.home() / ".nix-profile" / "bin" / "i3-msg"),
)


def _resolve_i3_msg():
    """Absolute path to an executable `i3-msg`, or None if none is found.

    Prefers `shutil.which` (honours PATH), then falls back to the first
    well-known absolute location (see _I3_MSG_FALLBACKS) that EXISTS and is
    EXECUTABLE. The fallback is what makes host-side i3 foregrounding work under
    the browser-bridge systemd --user service, whose minimal PATH omits
    /run/current-system/sw/bin."""
    found = shutil.which("i3-msg")
    if found:
        return found
    for cand in _I3_MSG_FALLBACKS:
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def i3_available() -> bool:
    """True when host-side i3 foregrounding is meaningful: a graphical session
    (DISPLAY set) AND a resolvable `i3-msg` (on PATH or a well-known absolute
    fallback — see _resolve_i3_msg). A headless / non-i3 host → False → the
    `activate` op SKIPS the i3 step gracefully (no error)."""
    if not os.environ.get("DISPLAY"):
        return False
    return _resolve_i3_msg() is not None


def i3_foreground(title, *, timeout: float = I3_MSG_TIMEOUT) -> str:
    """Best-effort host-side i3 foregrounding of the Brave window matching the
    (UNTRUSTED) `title`. Returns a small metadata state for the caller:

      * "skipped" — no graphical/i3 session (i3-msg absent or no DISPLAY), or no
        usable title. NOT an error — the Chrome-side activate still returns.
      * "applied" — i3-msg ran and exited 0.
      * "failed"  — i3-msg errored, timed out, or exited nonzero (non-fatal).

    The title is sanitized + re.escape'd (see i3_focus_argv) and the call is
    shell=False + timeout-bounded, so a hostile title can at worst focus the
    wrong Brave window / none — never execute a command."""
    if not i3_available():
        return "skipped"
    argv = i3_focus_argv(title)
    if argv is None:
        return "skipped"
    # Resolve i3-msg to an ABSOLUTE path for argv[0] so the call works even when
    # i3-msg is not on the (minimal systemd --user) PATH. i3_available() above
    # already confirmed it resolves; re-check defensively. The criteria (argv[1])
    # is built + sanitized + re.escape'd by i3_focus_argv — UNCHANGED here.
    i3_msg = _resolve_i3_msg()
    if i3_msg is None:
        return "skipped"
    argv[0] = i3_msg
    try:
        proc = subprocess.run(argv, shell=False, capture_output=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001 — best-effort; any failure is non-fatal.
        log("activate_i3_failed", reason="exception")
        return "failed"
    if getattr(proc, "returncode", 1) == 0:
        return "applied"
    log("activate_i3_failed", reason="nonzero", rc=getattr(proc, "returncode", None))
    return "failed"


def _result_title(result) -> str:
    """The tab title from an `activate` result envelope ({id,ok,data:{title}}),
    or "" if absent. This is the UNTRUSTED, page-controlled string."""
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("title"), str):
            return data["title"]
    return ""


def _annotate_i3(result, state: str) -> None:
    """Record the i3-foregrounding outcome as a small metadata field on the
    activate result's data ({...,"i3":"applied"|"skipped"|"failed"}) so the
    caller knows whether the window was actually raised. Never emits the title."""
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        result["data"]["i3"] = state


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
                 poll_timeout: float, ping_timeout: float = None):
    # `ping` gets its OWN, much shorter deadline — see PING_TIMEOUT_DEFAULT and the
    # BROWSER_BRIDGE_PING_TIMEOUT entry in the module docstring. Resolved here (not
    # at the call site) so build_server callers that predate the parameter keep
    # working and still get the fast ping.
    if ping_timeout is None:
        ping_timeout = _env_float("BROWSER_BRIDGE_PING_TIMEOUT",
                                  PING_TIMEOUT_DEFAULT)

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
            """(instance_id, label, active_tab, ext_version, ext_id) from the
            /poll headers.

            Missing instance id → "" (registry assigns the legacy synthetic id).
            Label + active-tab + ext-version + ext-id strings are URL-encoded by
            the extension (header values must be ASCII-safe) and decoded here. A
            legacy extension that omits either optional header → None for it.
            """
            # EVERY extension-supplied string is bounded HERE, server-side.
            # protocol.js caps them with capHeaderValue, but a client-side cap
            # binds only an HONEST extension: these values arrive over HTTP and
            # are echoed back in every /health, /instances and /whoami response.
            # Identity strings (instance id / label / ext version / ext id) get
            # the tight cap; the active-tab url/title get the larger one, since a
            # legitimate URL is long (it mirrors protocol.js MAX_HEADER_VALUE_CHARS).
            #
            # ⚠ NOTE FOR ANYONE CHANGING /poll (e.g. the long-poll disconnect
            # fix): `instance_id` is the ROUTING KEY (`key = label or
            # instance_id` in _register_locked), and it is TRUNCATED here. Today
            # it is a crypto.randomUUID() — 36 chars, nowhere near the cap — so
            # this is inert. But if ids ever grow, or gain a per-connection
            # suffix, two distinct connections could truncate to the SAME string
            # and collide into one registry slot, which presents as a phantom
            # supersede rather than as an obvious length bug. Raise the cap or
            # hash instead of truncating if that day comes.
            instance_id = (self.headers.get(HDR_INSTANCE_ID)
                           or "").strip()[:MAX_IDENTITY_CHARS]
            raw_label = self.headers.get(HDR_LABEL) or ""
            label = (unquote(raw_label).strip()[:MAX_IDENTITY_CHARS]
                     if raw_label else "")
            au = self.headers.get(HDR_ACTIVE_URL)
            at = self.headers.get(HDR_ACTIVE_TITLE)
            active = None
            if au or at:
                active = {
                    "url": unquote(au)[:MAX_ACTIVE_TAB_CHARS] if au else None,
                    "title": unquote(at)[:MAX_ACTIVE_TAB_CHARS] if at else None}
            raw_ext = self.headers.get(HDR_EXT_VERSION)
            ext_version = (unquote(raw_ext).strip()[:MAX_IDENTITY_CHARS]
                           if raw_ext else None)
            raw_id = self.headers.get(HDR_EXT_ID)
            ext_id = (unquote(raw_id).strip()[:MAX_IDENTITY_CHARS]
                      if raw_id else None)
            return instance_id, label, active, ext_version, ext_id

        def _whoami(self) -> dict:
            """Assemble the read-only whoami snapshot: which HOST + which browser
            profiles/instances + bridge diagnostics. Metadata only (host label,
            instance labels, active-tab DOMAINs, versions — NEVER page content),
            so it is safe to expose exactly like /health (bearer + Host guarded,
            NOT rate-limited). Reached only after _guard() in do_GET."""
            expected = manifest_version()
            insts = annotate_staleness(registry.whoami_snapshot(), expected)
            rl = registry.rate_limit_config()
            srv_host, srv_port = (self.server.server_address[0],
                                  self.server.server_address[1])
            return {
                "ok": True,
                "host": resolve_host(),
                "bridge": {
                    "endpoint": f"http://{srv_host}:{srv_port}",
                    "port": srv_port,
                    # SERVER_VERSION const + the best-effort git short-HEAD (null
                    # when the repo/git is unavailable — never fatal).
                    "server_version": {"version": SERVER_VERSION,
                                       "git": git_short_head()},
                    "connected": len(insts),
                    "rate_limit": {"per_sec": rl["per_sec"],
                                   "burst": rl["burst"],
                                   "max_queue": rl["max_queue"]},
                    # The manifest version the server expects Brave to have
                    # loaded (deployed dir, else repo). Each instance additionally
                    # carries `extension_stale` — an explicit true/false/null
                    # verdict rather than two strings to compare by eye. null
                    # means undecidable, never "fine".
                    "extension_version_current": expected,
                    # The deploy directory Brave SHOULD have been pointed at.
                    # Reported so an operator can read it next to each instance's
                    # path-derived `extension_id`. The path→id derivation is now
                    # MEASURED (see HDR_EXT_ID) — sha256(path)[:32] with each
                    # nibble mapped a-p — so an expected id COULD be computed
                    # from this field, turning `extension_stale` into a real
                    # path check instead of a version-string comparison. It is
                    # deliberately not done here: that is a behaviour change
                    # needing its own PR and review. Today: compare ids across
                    # time (before/after a re-point), or against the value you
                    # compute yourself from the target path.
                    "extension_dir_expected": str(_DEPLOYED_EXT_DIR),
                },
                "instances": insts,
            }

        # --- routes -------------------------------------------------------- #
        def do_GET(self):
            if not self._guard():
                return
            t0 = time.monotonic()
            path = urlsplit(self.path).path
            if path == "/health":
                # extension_version_current = the manifest version the SERVER
                # expects Brave to have loaded (deployed dir, else repo). Each
                # instance additionally carries the explicit `extension_stale`
                # verdict (true/false/null) so this is a yes/no, not an eyeball
                # comparison of two strings.
                expected = manifest_version()
                insts = annotate_staleness(registry.snapshot(), expected)
                known, missing = registry.known_snapshot()
                # `extension_connected` is DELIBERATELY left as-is: it is a bare
                # OR over live instances ("is anything up"), which is what
                # existing callers ask of it, and silently redefining it to
                # "everything I ever saw is up" would break them. The dishonesty
                # it enables — one healthy profile masking a `work` that dropped
                # an hour ago — is fixed by carrying the truth alongside:
                # `known_instances` (every key seen this process-lifetime, each
                # with `connected`) and `missing` (the ones now gone). `browser
                # health` renders `work: DISCONNECTED (last seen …)` from these.
                self._send(200, {"ok": True,
                                 "extension_connected": bool(insts),
                                 "count": len(insts),
                                 "extension_version_current": expected,
                                 "instances": insts,
                                 "known_instances": known,
                                 "missing": missing})
                # The ORIENTATION ops emit too — measured 2026-08-02, `whoami`
                # (38 calls) and `health` (15) appeared NOWHERE in activity.events
                # despite being the documented first thing you run, so the only
                # structured source could not see 53 invocations.
                #
                # Emitted AFTER the response, exactly like the /cmd path: same
                # best-effort + metadata-only contract (see emit_cmd_event and the
                # PRIVACY CONTRACT above). Deliberately NO domain: /health and
                # /whoami are GLOBAL — they describe every connected profile at
                # once, so there is no single active domain to attribute, and
                # emitting one per profile would widen the contract to "which sites
                # are open in each of your browsers". `key` is likewise empty:
                # these endpoints take no --instance.
                _emit_diag_event("health", t0)
                return
            if path == "/instances":
                insts = registry.snapshot()
                self._send(200, {"ok": True, "count": len(insts),
                                 "instances": insts})
                return
            if path == "/whoami":
                self._send(200, self._whoami())
                _emit_diag_event("whoami", t0)   # see the /health emit above
                return
            if path == "/poll":
                (instance_id, label, active, ext_version,
                 ext_id) = self._poll_identity()
                cmd = registry.poll(instance_id, label, poll_timeout,
                                    active_tab=active,
                                    extension_version=ext_version,
                                    extension_id=ext_id)
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
            # The upload file PATH is captured for the AUDIT log/event (see the
            # ALLOWED_OPS note). It is local metadata (never file content); logging
            # it is acceptable and required for traceability of this exfil-capable
            # action. `body` still carries it (target/tab are popped, path is not).
            upload_path = body.get("path") if op == "upload" else None
            # Captured BEFORE submit so the fields are still present regardless of
            # which path (ok / refused / throttled) the request takes below.
            emulate_extra = _emulate_extra(body) if op == "emulate" else None
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
                # `ping` alone asks for the fast-fail-when-idle deadline. Named
                # HERE so no other op can pick it up by accident; the decision of
                # what it actually resolves to is _effective_timeout_locked's,
                # because only that runs under the lock that can read in-flight
                # work. Every other op passes fast_timeout=None and is untouched.
                result = registry.submit(
                    body, timeout=cmd_timeout, target=target,
                    session_id=session_id, tab=tab,
                    fast_timeout=(ping_timeout if op == "ping" else None))
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
                extra = {"reason": e.reason, "sess": sess}
                if emulate_extra:
                    extra.update(emulate_extra)
                if op == "upload":
                    # AUDIT even a throttled upload attempt (it never reached the
                    # browser, so no file was read — but the attempt is traceable).
                    extra["path"] = upload_path
                    log("upload", outcome="throttled", domain="",
                        path=upload_path, key=(target or ""))
                emit_cmd_event(
                    op=op, key=(target or ""), outcome="throttled",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    domain="", exit_code=1, extra=extra)
                return
            except NoOwnedTab:
                outcome, exit_code = "no_owned_tab", 1
                log("cmd_no_owned_tab", op=op)
                self._send(409, {"ok": False, "error": "no_owned_tab"})
            except NotOwnedTab:
                # The blast-radius refusal for OWNED_TAB_ONLY_OPS. Never reached the
                # extension, so nothing was emulated — the operator's tab is
                # untouched, which is the entire point of the gate.
                outcome, exit_code = "not_owned_tab", 1
                log("cmd_not_owned_tab", op=op)
                self._send(409, {"ok": False, "error": "not_owned_tab"})
            except NoExtension:
                outcome, exit_code = "no_extension", 1
                log("cmd_no_extension", op=op)
                # Carry the keys we HAVE seen so "nothing connected" can say
                # WHICH profile went away and when, instead of leaving the
                # operator to guess whether Brave was ever wired up at all.
                known, _missing = registry.known_snapshot()
                self._send(503, {"ok": False,
                                 "error": "extension_not_connected",
                                 "known_instances": known})
            except AmbiguousInstance as e:
                outcome, exit_code = "ambiguous", 1
                log("cmd_ambiguous", op=op, count=len(e.instances))
                self._send(409, {"ok": False, "error": "ambiguous_instance",
                                 "instances": e.instances})
            except UnknownInstance as e:
                outcome, exit_code = "unknown_instance", 1
                log("cmd_unknown_instance", op=op, target=e.target)
                known, _missing = registry.known_snapshot()
                self._send(404, {"ok": False, "error": "unknown_instance",
                                 "target": e.target, "instances": e.instances,
                                 "known_instances": known})
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
                if op == "activate":
                    # Chrome-side activate only set the tab active WITHIN its
                    # window (a no-op for real visibility under i3 — the tab stays
                    # document.hidden). Focus the matching Brave X11 WINDOW via
                    # i3-msg so i3 raises it + switches workspace → the throttled
                    # SPA un-throttles and renders. Best-effort + bounded; the
                    # title is UNTRUSTED (page-controlled) and handled shell=False
                    # + re.escape'd inside i3_foreground. Skipped gracefully off i3.
                    state = i3_foreground(_result_title(result))
                    _annotate_i3(result, state)
                    log("activate_i3", state=state)
                self._send(200, {"ok": True, "result": result})
            # Off the critical path: the HTTP response is already sent. Metadata-
            # only + best-effort — cannot delay or break the command (the key is
            # the skill's routing target, empty for the implicit single-instance
            # case). See emit_cmd_event / the PRIVACY + BEST-EFFORT contracts.
            # `upload` additionally carries the file PATH (audit metadata) + a
            # dedicated structured log line — this op is exfil-capable so EVERY
            # outcome is traceable (the path is local metadata, never file content).
            extra = {"path": upload_path} if op == "upload" else emulate_extra
            if op == "upload":
                log("upload", outcome=outcome, domain=domain, path=upload_path,
                    key=(target or ""))
            emit_cmd_event(op=op, key=(target or ""), outcome=outcome,
                           duration_ms=int((time.monotonic() - t0) * 1000),
                           domain=domain, exit_code=exit_code, extra=extra)

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
                 cmd_timeout: float, poll_timeout: float,
                 ping_timeout: float = None) -> ThreadingHTTPServer:
    handler = make_handler(registry, token, cmd_timeout, poll_timeout,
                           ping_timeout)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True  # let shutdown not hang on a blocked long-poll
    return server


# ⚠ FORWARD-LOOKING, and scoped accordingly in the message below.
#
# From extension 0.4.0 onward, the extension races its own /poll fetch against a
# wall-clock budget (POLL_BUDGET_MS, 40s, in extension/protocol.js). That budget
# must strictly EXCEED this server's poll_timeout, or every long-poll aborts
# client-side just before the server's 204 and the extension backoff-spins
# instead of long-polling. Equality is a genuine race, hence `>=` below.
#
# Extensions BEFORE 0.4.0 — including 0.3.1, the build currently deployed — do
# `pollOnce` as a bare unbounded `await fetch` with no AbortController and no
# budget, so for them this misconfiguration has NO client-side abort: the poll
# simply outlives the server's 204. The warning must not tell an operator (who is
# by definition already debugging when they read it) that today's extension will
# backoff-spin, because today's will not.
#
# The two sides live in different languages and different processes, so nothing
# can enforce the relationship. Saying it out loud at startup is the fix.
EXTENSION_POLL_BUDGET_S = 40.0


def _warn_poll_timeout_vs_extension_budget(poll_timeout: float) -> None:
    if poll_timeout >= EXTENSION_POLL_BUDGET_S:
        log("config_warning",
            reason="poll_timeout_exceeds_extension_poll_budget",
            poll_timeout=poll_timeout,
            extension_poll_budget_s=EXTENSION_POLL_BUDGET_S,
            applies_to_extension="0.4.0+",
            detail="BROWSER_BRIDGE_POLL_TIMEOUT is at or above the /poll budget "
                   "used by extension 0.4.0 and later (POLL_BUDGET_MS in "
                   "extension/protocol.js). On those builds every poll will "
                   "abort client-side and the extension will backoff-spin; "
                   "builds before 0.4.0 do not bound the poll fetch at all and "
                   "are unaffected. Lower BROWSER_BRIDGE_POLL_TIMEOUT, or raise "
                   "POLL_BUDGET_MS (needs a full Brave restart).")


def main(argv=None) -> int:
    host = os.environ.get("BROWSER_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("BROWSER_BRIDGE_PORT", "8788"))
    cmd_timeout = float(os.environ.get("BROWSER_BRIDGE_CMD_TIMEOUT", "20"))
    poll_timeout = float(os.environ.get("BROWSER_BRIDGE_POLL_TIMEOUT", "25"))
    ping_timeout = _env_float("BROWSER_BRIDGE_PING_TIMEOUT",
                              PING_TIMEOUT_DEFAULT)
    token_file = default_token_file()
    token = load_or_create_token(token_file)

    registry = Registry()
    server = build_server(host, port, registry, token, cmd_timeout,
                          poll_timeout, ping_timeout)
    log("listening", host=host, port=port, token_file=str(token_file),
        cmd_timeout=cmd_timeout, poll_timeout=poll_timeout,
        ping_timeout=ping_timeout)
    _warn_poll_timeout_vs_extension_budget(poll_timeout)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
