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
                                 DERIVED from that work instead, so ordinary
                                 concurrency does not report a busy profile as
                                 dead. NOT unconditional: the derived budget is
                                 still capped at CMD_TIMEOUT, so more than
                                 CMD_TIMEOUT of legitimate serial work (N>=2 busy
                                 commands) can still time a ping out — raise
                                 CMD_TIMEOUT if that is your workload. See
                                 Registry._effective_timeout_locked.
                                 Applies to `ping` ONLY.
    BROWSER_BRIDGE_POLL_TIMEOUT  seconds a /poll blocks before 204 (default 25)
    BROWSER_BRIDGE_RATE_PER_SEC  per-instance sustained /cmd dispatch rate
                                 (token-bucket refill, default 5; 0 → unlimited)
    BROWSER_BRIDGE_BURST         per-instance token-bucket burst size (default 20;
                                 clamped to >=1 when RATE_PER_SEC>0, else a <1
                                 burst would rate_limit EVERY /cmd forever)
    BROWSER_BRIDGE_MAX_QUEUE     per-instance pending-command cap (default 32;
                                 0 → unlimited). Over-cap /cmd → HTTP 429.
    BROWSER_BRIDGE_HEARTBEAT_S   seconds between liveness telemetry heartbeats
                                 (default 900; 0 → disabled). See
                                 HEARTBEAT_INTERVAL_S for why the source needs a
                                 cadence at all.
    BROWSER_BRIDGE_I3_TIMEOUT    seconds each host-side `i3-msg` call may take
                                 (default 1.5). See I3_MSG_TIMEOUT.
    BROWSER_BRIDGE_I3_MATCH_WAIT seconds `activate` keeps re-reading the i3 tree
                                 waiting for the Brave window's WM_NAME to catch
                                 up with the activated tab's title (default 1.5;
                                 0 → one read, no wait). See I3_MATCH_WAIT.
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
# It is INSTANCE-SCOPED when the request carries a `target` (i.e. `--instance`):
# ownership is keyed (instance, session), so an unscoped release drops this
# session's tab on every connected profile. See Registry.release_session.
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

# The extension's own bound on posting a result back (RESULT_BUDGET_MS in
# extension/protocol.js). Mirrored for the same reason as EXEC_OP_BUDGET_S, and
# pinned against protocol.js by test_result_budget_matches_the_extension.
RESULT_BUDGET_S = 10.0

# How long an `inflight` entry can still describe a command a healthy extension
# might be working on. After EXEC + RESULT the extension has certainly either
# answered or given up, so an entry older than this says nothing about current
# business and is disregarded (and pruned).
#
# 🔴 THIS IS WHY AN ABANDONED COMMAND IS NOT SIMPLY DROPPED. The SUBMITTER giving
# up (BridgeTimeout at cmd_timeout, default 20s) does NOT free the extension —
# its serial loop keeps executing that command for up to EXEC(18) + RESULT(10) =
# 28s, which is LONGER than cmd_timeout. Popping the entry at submitter-exit
# therefore made the instance look IDLE while it was provably still busy, and the
# next `ping` fast-failed against a healthy extension (measured; in that window it
# was worse than both the old flat 20s and the interim flat 10s). The entry is kept
# and expires on its own instead.
INFLIGHT_STALE_S = EXEC_OP_BUDGET_S + RESULT_BUDGET_S

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

# ---- The session id as a TELEMETRY JOIN KEY (distinct from routing) -------- #
# X-Session-Id is a ROUTING key: any string that is stable per caller works, and
# every tier of the CLI's fallback chain produces one. That is NOT enough to
# record it as an activity.events `session` value, which is a JOIN key — a row
# claiming session X asserts "this is the same session other sources call X".
#
# The id already carries its provenance: `derive_session_id` deliberately tags it
# with the tier that produced it, before the FIRST colon.
#
#   claude:<uuid>          CLAUDE_CODE_SESSION_ID / CLAUDE_SESSION_ID — Claude
#                          Code's own session uuid, which is EXACTLY what
#                          `source='claude'` rows already store. JOINABLE.
#   tmux:%<n>              $TMUX_PANE. A pane id is stable across MANY unrelated
#                          sessions, so writing it into `session` would silently
#                          merge them into one apparent session — strictly worse
#                          than an empty column.
#   sid:<sid>:<starttime>  the POSIX session id. No other source records it.
#   ppid:<pid>:<rand>      the last-resort cached random token.
#   synthetic:<...>        an id the CLI deliberately made up (its recreate-close
#                          presents a throwaway so the close cannot evict the
#                          mapping it just created).
#
# Reading that tag is NOT shape inference: it is a tag the CLI author emits on
# purpose, not a guess from what the value happens to look like. 🔴 What WOULD be
# shape inference — and is forbidden — is deciding from the id's FORM ("looks
# like a uuid → claude"). The tag, or nothing.
#
# FAIL CLOSED. An id with no tag at all (no colon), an empty one, or a tag we do
# not know is reported as SESSION_SRC_UNKNOWN and writes NO session key. In
# particular an UNPREFIXED value is never treated as a bare claude id — the
# opencode browser tool's own default is the literal "browser-agent", and a
# version-skewed caller can send anything.
SESSION_SRC_JOINABLE = "claude"
SESSION_SRC_UNKNOWN = "unknown"
# The CLOSED vocabulary of tier tags `derive_session_id` can emit. Pinned against
# the tags PARSED OUT OF THE CLI SOURCE by test_browser_session_id.py::
# test_the_server_validation_set_equals_the_tags_parsed_from_the_cli, so a new
# tier fails there until it is added here too.
# 🔴 Against the PARSE, not against a retyped literal. This comment previously
# claimed a two-way pin that did not exist — each side was checked against its own
# copy of the list, so a change touching both real files passed (measured: grow
# the CLI by an `opencode:` tier and update its ledger, leave this alone → the
# whole suite green). The failure that hides is silent and fail-closed: the new
# tier normalises to SESSION_SRC_UNKNOWN, loses its id, and the column empties.
#
# 🔴 IT IS A VALIDATION SET, not documentation. The tag arrives on a
# caller-supplied header: without this, `sess_src` is an unbounded-cardinality
# column filled from a string a raw token-holder chooses ("['claude", " claude",
# "CLAUDE", "claud" were all measured landing verbatim). Anything not in here is
# reported as SESSION_SRC_UNKNOWN and carries no id — a new tier needs a CLI
# change, which the two-way pin already forces you to declare.
SESSION_TIER_TAGS = ("claude", "tmux", "sid", "ppid", "synthetic")

# ---- Nested runs: the forwarded id is the PARENT, not the actor ------------ #
# `browser agent "<goal>"` shells out to an opencode agent, and browser-agent
# captures the id of the session that INVOKED it (`--print-session-id`) and
# forwards it as this nested run's X-Session-Id. That is right for ROUTING and
# for the audit trail — the nested tool drives the tab that invoker `open`ed.
# It is WRONG for the `session` column, which means "the agent session that
# ISSUED this command": for a nested run the issuer is the opencode agent, whose
# own id we do not have yet. Attributing those calls to the operator's session
# would fabricate usage in the `session` JOIN column (measured: ~581
# nested tool-call rows in 14d, ~11% of bridge commands).
#
# So the nested tool declares itself. When this header is present we record the
# forwarded id as `payload.origin_session` — the causal PARENT, somewhere nothing
# can mistake it for the actor — and leave `session` EMPTY. Giving the nested
# session an id of its own is a later change.
HDR_SESSION_ORIGIN = "X-Session-Origin"
# The CLOSED ledger of origin tokens, and the marker for a declaration that is
# present but not one of them.
#
# 🔴 PRESENCE IS THE SIGNAL, NOT THE VALUE. Attribution is suppressed whenever the
# header is THERE, whatever it says. An oversized, control-char, empty or unknown
# value means a caller tried to disclaim authorship and we could not read it —
# losing attribution beats fabricating it, so it fails CLOSED and is marked so the
# case is visible in the data rather than silent. Validating the value only after
# deciding to suppress is what keeps this from turning into the id-sanitiser bug
# it mirrors: that one returned "" for a malformed value and fell through to
# writing `session` with the PARENT's id — the exact fabrication this mechanism
# exists to prevent.
ORIGIN_TOKENS = ("browser-agent", "opencode-inherited")
SESSION_ORIGIN_INVALID = "invalid"

# Bound what a caller-supplied header can put on a telemetry row. A value that
# fails this is dropped entirely, never truncated: a truncated join key is a
# WRONG join key, which is the failure this whole change exists to avoid. 200 is
# ~5x a uuid plus its tag.
MAX_SESSION_FIELD = 200

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

# Liveness heartbeat interval, in seconds. 0 (or negative) disables the thread.
#
# WHY A HEARTBEAT EXISTS AT ALL. Every other browser-bridge event is emitted by a
# handled COMMAND, so the source has no cadence: it emits when an agent drives it
# and is otherwise silent for as long as nobody does. That makes it undetectable
# by the activity pipeline's deadman check (scripts/collector/deadman.py), which
# decides "dead" by comparing silence against a MEASURED per-source budget —
# there is no budget that separates "unused" from "down" when normal silence is
# unbounded. Measured 2026-08-11: laptop/browser-bridge had been silent 30.9
# ACTIVE hours, 2.2x the worst lull in its own 14-day history, purely because the
# operator had not run a browser task; the check called it DEAD and was right by
# its own definition and useless by ours.
#
# 900s = 3 of the deadman's 5-minute buckets, so a live bridge emits ~96 rows/day
# and its p99 active-gap collapses to ~3 buckets. The budget then bottoms out on
# that module's 2-ACTIVE-HOUR floor: silence beyond ~2 active hours means the
# unit is genuinely not running, and no amount of not-using-the-browser can
# produce it. Cheap enough to be uninteresting (one local spool append) and
# bounded by the same best-effort contract as every other emit.
HEARTBEAT_INTERVAL_S = float(os.environ.get("BROWSER_BRIDGE_HEARTBEAT_S", "900"))

# Host-side i3 foregrounding for the `activate` op (see i3_foreground below).
# The Chrome-side activate (chrome.tabs.update{active}+windows.update{focused})
# is a NO-OP for actual VISIBILITY under a tiling WM: a backgrounded tab stays
# `document.visibilityState:"hidden"`, so a foreground-throttled SPA never
# renders. Focusing the matching Brave X11 window via `i3-msg` is what actually
# raises it + switches workspace, un-throttling the tab. Bounded by this timeout;
# any failure is non-fatal (the Chrome-side activate result still returns).
I3_MSG_TIMEOUT = float(os.environ.get("BROWSER_BRIDGE_I3_TIMEOUT", "1.5"))
# Cap on the UNTRUSTED (page-controlled) tab-title fragment matched against the
# i3 tree — bounds a pathological title before it is re.escape'd.
I3_TITLE_MAX = 80
# How long to keep re-reading the i3 tree waiting for the Brave window's X11
# WM_NAME to catch up with the tab title Chrome just activated.
#
# 🔴 THE RACE THIS EXISTS FOR. `activate` returns the tab's title from Chrome the
# instant the tab goes active, but the X11 window's WM_NAME is updated by the
# browser process AFTERWARDS. Immediately after an `open` the window therefore
# still advertises the OLD title, so a title-keyed match finds nothing. Measured
# live 2026-08-19: activate #1 (issued right after `open`) matched no window and
# the tab stayed `hidden`; activate #2, once the title had settled, raised it.
# A bounded re-read turns that "unreliable by construction" first activate into a
# normal success — and when the window genuinely is not there, the wait expires
# and the caller is told `no_match` instead of being lied to.
I3_MATCH_WAIT = _env_float("BROWSER_BRIDGE_I3_MATCH_WAIT", 1.5)
# Gap between those re-reads. Each poll is one `i3-msg -t get_tree` (a READ-ONLY
# IPC query — it cannot focus/move/switch anything), so the whole wait costs at
# most ~I3_MATCH_WAIT/I3_MATCH_POLL cheap queries.
I3_MATCH_POLL = 0.2

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

# The BUILD MARKER of the code the instance is actually EXECUTING (#324) — a
# generated literal in extension/build_id.js that the service worker IMPORTS,
# so it is frozen into the loaded module graph and travels with the code. This
# is the only field an ALL-CLEAR can be computed from; the version can never
# certify code as current. Absent from a build that predates the marker → the
# verdict is NEVER false; it is null unless the two versions are both known and
# DISAGREE, which decides true on its own (see annotate_staleness).
HDR_EXT_BUILD = "X-Bridge-Ext-Build"

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

# The build-marker source files, deployed-preferred exactly like the manifests
# above. `build_marker()` reads the LITERAL out of these — it never recomputes a
# hash at request time, because the expected value must be the one baked into
# the tree Brave loads, not a fresh digest of whatever the repo looks like now.
_DEPLOYED_EXT_BUILD_ID = _DEPLOYED_EXT_DIR / "build_id.js"
_EXT_BUILD_ID_PATH = (_REPO_DIR / "scripts" / "browser-bridge" / "extension"
                      / "build_id.js")

# Matches the generated `export const BUILD_MARKER = "<hex>";` literal. Kept in
# sync with gen-build-marker.py's MARKER_RE (the generator owns the derivation;
# this only has to READ the value, and the drift test gates the pair).
_BUILD_MARKER_RE = re.compile(r"""BUILD_MARKER\s*=\s*["']([0-9a-f]+)["']""")

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


def _read_build_marker(path):
    """The BUILD_MARKER literal declared in the build_id.js at `path`, or None
    (missing / unreadable / no literal). Best-effort — never raises."""
    try:
        m = _BUILD_MARKER_RE.search(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — best-effort.
        return None
    return m.group(1) if m else None


def build_marker(path=None):
    """The build marker the code Brave is running SHOULD carry: the deployed
    extension's build_id.js (`~/.local/share/browser-bridge-ext/`) when present,
    else the repo copy. None when neither is readable → the verdict goes
    undecidable, never "current". Mirrors manifest_version()'s
    deployed-before-repo precedence and its no-.resolve() absolute paths."""
    if path is not None:
        return _read_build_marker(path)
    return (_read_build_marker(_DEPLOYED_EXT_BUILD_ID)
            or _read_build_marker(_EXT_BUILD_ID_PATH))


def annotate_staleness(instances, expected, expected_build=None):
    """Add an explicit loaded-vs-expected verdict to each instance descriptor,
    IN PLACE, and return the list.

    `expected` is the manifest version the server expects (manifest_version()).
    `expected_build` is the BUILD MARKER read from the deployed extension source
    (build_marker()). Either may be None.

    🔴 AN ALL-CLEAR IS COMPUTED FROM THE MARKER, NEVER THE VERSION (#324). The
    version cannot answer the question it was being asked. `extension_version`
    is `chrome.runtime.getManifest().version` — it describes the manifest of the
    extension the worker LOADED, and `extension_id` is derived from the load
    PATH, so neither describes the executing code. Measured 2026-08-04: two
    Brave profiles loading the SAME directory reported an identical id, an
    identical `0.7.3` and `extension_stale: false`, while one ran `main` and the
    other an unmerged 0.7.2 build whose source exists on no disk. The build
    marker is a literal in the worker's own imported module graph, so a stale
    worker reports the stale value by construction.

    (Also measured 2026-08-04, on the OTHER host — workbench: profile
    "personal - other" reported `0.7.1` and profile "work" `0.8.1` under one
    `extension_id` — i.e. one load path — while the directory on disk held
    only `0.8.1` (`manifest.json` + `build_id.js`, both stamped 09:31). Evidence
    the version is metadata parsed at extension-LOAD time rather than re-read
    from disk per call. ONE observation, a hypothesis, not established — it
    changes nothing here either way: the version still cannot certify the code.)

    But the uselessness is ASYMMETRIC, and only one direction of it is real. A
    version MATCH proves nothing (the paragraph above is exactly that case). A
    version MISMATCH, both sides known, is positive proof that what was loaded
    is not what is deployed — that direction was never in doubt, so a missing
    marker must not discard it.

    `extension_stale` is a yes/no, not two strings to eyeball:
      True  — the marker this instance reports differs from `expected_build`
              (this profile is running code that is not the deployed code →
              Remove + Load unpacked it, per profile); or the markers agree but
              the reported VERSION disagrees (a nonsense state worth flagging);
              or a marker is MISSING on either side and the two versions are
              both known and disagree (the asymmetry above).
      False — the markers are both present and identical. This, and only this,
              means "verified current".
      None  — 🔴 UNDECIDABLE, and it FAILS CLOSED to here. A marker missing on
              either side (a build predating #324, an unreadable/undeployed
              source tree) with versions that agree, or with either version
              unknown, yields null. NEVER guess, and never let a version match
              stand in for a marker: a False that is not marker-backed is
              exactly the affirmative all-clear that #324 was filed about. Only
              True is ever reachable from versions alone.
    """
    for inst in instances:
        loaded = inst.get("extension_version")
        loaded_build = inst.get("extension_build")
        inst["extension_version_expected"] = expected
        inst["extension_build_expected"] = expected_build
        if not expected_build or not loaded_build:
            # No marker on one side → no all-clear is available. A known version
            # DISAGREEMENT is still positive proof of staleness; agreement, or
            # an unknown version on either side, stays undecidable.
            inst["extension_stale"] = (
                True if expected and loaded and loaded != expected else None)
            continue
        stale = loaded_build != expected_build
        if not stale and expected and loaded and loaded != expected:
            # Markers agree but versions do not: the deployed tree changed its
            # manifest without the marker moving, or a header was spoofed.
            # Either way it is not a verified-current state.
            stale = True
        inst["extension_stale"] = stale
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
# instance routing key, the outcome, the server-side latency, (best-effort) the
# active tab's BARE DOMAIN, the caller's SESSION TIER, and — for the joinable
# tier only — the caller's own agent SESSION ID. We NEVER emit the eval source,
# page HTML, screenshot bytes/data-URLs, a full URL with path/query, or any page
# content. The payload stays tiny.
#
# 🔴 THE SESSION ID IS A DELIBERATE WIDENING (2026-08-18) — read this before
# calling it a leak. Until now the `session` column was EMPTY on every
# browser-bridge row (measured: 0 of 6,937 over 14 days) while claude/opencode/
# keys/tmux/zsh filled it 100%, so a browser-skill call could not be joined to
# the agent session that made it; answering "which sessions used the browser
# skill" meant scanning 1.5M transcript records instead of reading one column.
# What is now emitted is the AGENT SESSION'S OWN OPAQUE HANDLE — the same
# `CLAUDE_CODE_SESSION_ID` that `source='claude'` rows in this very table already
# store, raw. It is NOT page content, is not derived from any page, and is not
# derived from anything the browser saw: it is an identifier the local agent
# harness minted for itself before any browser command existed. Storing it adds
# no information about WHAT was browsed — only about WHO asked. It is stored RAW
# and unhashed on purpose: a hash would make this the ONE source needing
# hex(SHA256()) at query time, and a forgotten join silently returns zero rows,
# which reads as a valid "no sessions matched" answer.
# The tier gate and the origin gate are what keep this honest — see
# SESSION_SRC_JOINABLE and HDR_SESSION_ORIGIN.
#
# 🔴 WHO ACTUALLY READS `session`, STATED CORRECTLY. An earlier draft of these
# comments said "the column adoption-scan and the deadman read". That is FALSE
# and was checked: adoption-scan's browser-bridge entry is `via="source"`, whose
# query selects text/payload/exit_code/duration_ms/ts/host and never `session`;
# the deadman consumes only a row's EXISTENCE. Every shipped `GROUP BY session`
# filters `source='claude'` (or claude+opencode). So NO shipped consumer reads
# this column for `source='browser-bridge'` today — precisely because it has
# always been empty.
# That makes the harm LATENT, not live, and it is still the reason for every
# gate here: a wrong value corrupts the FIRST consumer that reads it, silently,
# and a fabricated session key is indistinguishable from a real one after the
# fact. Absent data is recoverable; wrong-and-plausible data is not.
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


def _clean_session_field(value) -> str:
    """A caller-supplied header value, made safe to put on a telemetry row.

    Returns "" for anything empty, over MAX_SESSION_FIELD, or carrying a control
    character. DROPS rather than truncates — a truncated join key is a wrong join
    key, and a control character would be an unreadable handle in a column other
    tools compare with `=`. (The spool line is base64'd, so this is not an
    injection guard; it is a data-quality one.)
    """
    if not value:
        return ""
    try:
        s = str(value)
    except Exception:  # noqa: BLE001 — telemetry is strictly best-effort.
        return ""
    if len(s) > MAX_SESSION_FIELD:
        return ""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s):
        return ""
    return s


def _split_session_id(session_id):
    """`X-Session-Id` -> (tier, bare id), reading the tag the CLI put there.

    The tag is everything before the FIRST colon; the rest is the id as the
    producing source knows it (`sid:` and `ppid:` ids contain further colons, so
    only the first one may split). Both halves must be non-empty.

    Anything that carries no tag at all — including a bare uuid, which is exactly
    the value that would be most tempting to accept — returns
    (SESSION_SRC_UNKNOWN, ""). That is the fail-closed direction: an id whose
    provenance is unstated must never be promoted to a join key on the strength
    of looking like one.

    The BARE half is what reaches the `session` column, because
    `source='claude'` rows store the bare uuid (the transcript filename stem ==
    CLAUDE_CODE_SESSION_ID). Keeping the tag would force a
    replaceOne(session,'claude:','') at every join site, and a forgotten one
    returns zero rows — which reads as a valid "no sessions matched" answer, the
    same failure the raw-not-hashed decision above exists to avoid.
    """
    s = _clean_session_field(session_id)
    if not s:
        return SESSION_SRC_UNKNOWN, ""
    tier, sep, bare = s.partition(":")
    if not sep or not tier or not bare:
        return SESSION_SRC_UNKNOWN, ""
    # A tag we do not know is not reported verbatim: `sess_src` would otherwise be
    # an unbounded column filled from a caller-supplied header. Both halves are
    # dropped together — an unrecognised tier tells us nothing about what the
    # remainder means, so it must not reach `session` OR `origin_session`.
    if tier not in SESSION_TIER_TAGS:
        return SESSION_SRC_UNKNOWN, ""
    return tier, bare


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
                   domain: str = "", exit_code: int = 0, extra: dict = None,
                   kind: str = "cmd", session_id=None, session_origin=None,
                   attribute_session: bool = False) -> None:
    """Append ONE metadata-only activity event for a handled command.

    Best-effort + fire-and-forget: any failure is swallowed so telemetry can
    never break command handling. See the PRIVACY / BEST-EFFORT contracts above.
    `extra` merges additional METADATA-ONLY keys into the payload (used by the
    throttle path for {reason, sess} — a fixed reason string + a coarse session
    hash, never page content).

    SESSION ATTRIBUTION (see SESSION_SRC_JOINABLE / HDR_SESSION_ORIGIN):
      * `attribute_session=False` (the default) — this call site has no caller
        session at all. Exactly ONE emit is in that category: the heartbeat,
        which a timer produces with no request behind it. NOTHING is added.
        🔴 /whoami and /health are NOT in this category, though this docstring
        said they were until the audit round that moved them: they are operator
        subcommands (`browser whoami` / `browser health`) whose requests carry
        the ordinary session headers, so they pass attribute_session=True like
        any other command — see _emit_diag_event.
      * `attribute_session=True` — `payload.sess_src` ALWAYS records the tier
        parsed off `session_id`, so every row is self-describing about why it
        does or does not carry a key. The `session` COLUMN is filled with the
        BARE id only when that tier is SESSION_SRC_JOINABLE **and** no
        `session_origin` was declared.
      * `session_origin` set — a NESTED run forwarding its invoker's id. `session`
        stays empty; the bare id is recorded as `payload.origin_session` (the
        causal parent) beside `payload.origin`.
      * every field this adds is written AFTER `extra` merges, so no call site can
        overwrite an attribution with a value that did not come from a header.

    🔴 `kind` defaults to "cmd" and MUST stay that way for operator-driven
    commands: `kind='cmd'` is the USAGE signal downstream — session-analysis/
    adoption-scan.py queries source='browser-bridge' AND kind='cmd' to answer
    "is the browser skill actually used". Anything the SERVER emits on its own
    (the heartbeat) has to carry a different kind, or ~96 machine-generated rows
    a day would read as operator usage and the adoption number becomes a lie.
    """
    try:
        se = _load_spool_emit()
        if se is None:
            return
        # METADATA ONLY — op/key/outcome/(bare)domain, plus the caller's session
        # TIER and (joinable tier, non-nested only) its agent session id. Never
        # page content.
        payload = {"op": op, "key": key, "outcome": outcome}
        if domain:
            payload["domain"] = domain
        if extra:
            payload.update(extra)
        rec = {
            "source": "browser-bridge",
            "kind": kind,
            "text": domain or op,
            "duration_ms": int(duration_ms),
            "exit_code": int(exit_code),
        }
        if attribute_session:
            # These keys belong to the headers alone. Clear them first so a stale
            # or smuggled `extra` cannot leave a claim standing that the headers
            # did not make — "written after extra" only wins for keys we WRITE,
            # and the origin keys are conditional.
            for reserved in ("sess_src", "origin", "origin_session"):
                payload.pop(reserved, None)
            tier, bare = _split_session_id(session_id)
            payload["sess_src"] = tier
            # 🔴 BRANCH ON PRESENCE, NEVER ON THE CLEANED VALUE'S TRUTHINESS.
            # `session_origin is None` means the header was absent; ANY present
            # value — including "" — is a caller disclaiming authorship, and must
            # suppress `session` even when we cannot make sense of it. Keying this
            # off `if origin:` instead let a 201-char or control-char origin fall
            # through to the `elif` and write `session` with the PARENT's id.
            if session_origin is not None:
                declared = _clean_session_field(session_origin)
                # The value decides only what we RECORD, never whether to suppress.
                payload["origin"] = (declared if declared in ORIGIN_TOKENS
                                     else SESSION_ORIGIN_INVALID)
                # 🔴 THE SAME TIER GATE AS `session`, and for the same reason: a
                # pane id is stable across many unrelated sessions, so a reader who
                # groups by `origin_session` would merge them exactly as they would
                # on `session`. REACHABLE, not theoretical — browser-agent forwards
                # whatever --print-session-id produced and the opencode tool
                # declares its origin unconditionally, so a `tmux:`/`sid:` parent
                # id genuinely arrives here. The tier is on the row either way, so
                # the suppressed population stays measurable.
                if tier == SESSION_SRC_JOINABLE and bare:
                    payload["origin_session"] = bare
            elif tier == SESSION_SRC_JOINABLE and bare:
                # 🔴 THE TIER GATE. Only the joinable tier may fill the `session`
                # JOIN column; any other tier would merge unrelated sessions
                # under one apparent key. Never widen this to a test on the id's
                # form, and never let an origin-declaring caller reach it.
                rec["session"] = bare
        rec["payload"] = json.dumps(payload, ensure_ascii=False,
                                    separators=(",", ":"))
        se.emit(rec)
    except Exception:  # noqa: BLE001 — strictly best-effort.
        pass


def _emit_diag_event(op: str, t0: float, session_id=None,
                     session_origin=None) -> None:
    """One metadata-only event for a read-only DIAGNOSTIC GET (/whoami, /health).

    A thin, deliberately narrow wrapper over emit_cmd_event: op + outcome + latency
    and NOTHING else. No key (these endpoints take no --instance) and NO domain —
    they are global, describing every connected profile at once, so there is no
    single active domain to attribute and emitting per-profile domains would widen
    the privacy contract. Best-effort like every other emit: it cannot raise.

    🔴 THESE ARE OPERATOR CALLS AND ARE ATTRIBUTED. `browser whoami` / `browser
    health` are subcommands a person runs — the skill documents them as the FIRST
    thing to run — and the CLI sends its ordinary session headers on them, because
    `_curl` is one code path. They are NOT server-originated; only the heartbeat
    is. Leaving them unattributed made ONE operation have TWO outcomes: `whoami`
    reached via POST /cmd got a session, the same `whoami` via GET did not, for
    125 rows / 2.0% of `kind='cmd'` over 14 days. That is a smaller copy of the
    very bug this file's session work exists to fix, so they are attributed the
    same way as any other operator command."""
    emit_cmd_event(op=op, key="", outcome="ok",
                   duration_ms=int((time.monotonic() - t0) * 1000),
                   domain="", exit_code=0, session_id=session_id,
                   session_origin=session_origin, attribute_session=True)


# --------------------------------------------------------------------------- #
# Liveness heartbeat (see HEARTBEAT_INTERVAL_S for WHY this exists)
# --------------------------------------------------------------------------- #
def emit_heartbeat_event(registry) -> None:
    """Emit ONE liveness event: `source=browser-bridge, kind=heartbeat`.

    Deliberately NOT kind="cmd" — see the contract on emit_cmd_event's `kind`.

    WHAT THE ROW PROVES, STATED HONESTLY. The deadman consumes only the row's
    EXISTENCE, so what this detects is "the bridge process is running". The
    `connected` field is DIAGNOSTIC METADATA, not an alarm: no consumer branches
    on it today, so a bridge whose extension has silently dropped still reads
    alive to the deadman. That gap is not a regression — nothing detected an
    extension drop before either (the operator found out when a command failed) —
    but do not describe this heartbeat as covering it. Wiring an alarm to
    `connected` is a separate change with its own consumer.

    It is still worth emitting: it makes a drop visible in `activity.events`
    afterwards, and reading `Registry.connected` here drives the edge-triggered
    instance_lost logging on a timer instead of only when traffic arrives.
    `Registry.connected` is the ONE definition of live in this process, so this
    cannot disagree with what /health reports.

    Metadata-only and best-effort like every other emit: a bool and a count, no
    URL/domain/title/content, and nothing here may raise into the caller.
    """
    try:
        # ONE lock acquisition, not two: `connected` is just `bool(live)` over
        # the same snapshot, and taking the lock twice could report a bool and a
        # count from two different instants.
        live_instances = registry.snapshot()
        live = len(live_instances)
        connected = bool(live)
    except Exception:  # noqa: BLE001 — a registry problem must not kill the thread.
        connected, live = False, 0
    emit_cmd_event(op="heartbeat", key="", outcome="ok", duration_ms=0,
                   domain="", exit_code=0, kind="heartbeat",
                   extra={"connected": connected, "instances": live})


def run_heartbeat(registry, interval: float, stop: threading.Event,
                  emit=None) -> None:
    """Emit a heartbeat NOW, then every `interval` seconds until `stop` is set.

    Emitting immediately (rather than after the first sleep) is what makes a
    restart observable promptly: the deadman's silence measurement resets as soon
    as the unit comes back, so a bounce is not indistinguishable from an outage
    for the first interval.

    `stop.wait(interval)` rather than `time.sleep` so shutdown is immediate
    instead of blocking a service stop for up to 15 minutes. `emit` is injectable
    for tests.
    """
    emit = emit or emit_heartbeat_event
    while True:
        try:
            emit(registry)
        except Exception:  # noqa: BLE001 — strictly best-effort, and this thread
            pass           # must outlive a transient emitter failure.
        if stop.wait(interval):
            return


def start_heartbeat(registry, interval: float = None):
    """Start the heartbeat thread. Returns (thread, stop_event), or (None, None)
    when disabled (interval <= 0 — the documented escape hatch)."""
    interval = HEARTBEAT_INTERVAL_S if interval is None else interval
    if interval <= 0:
        return None, None
    stop = threading.Event()
    t = threading.Thread(target=run_heartbeat, args=(registry, interval, stop),
                         name="browser-bridge-heartbeat", daemon=True)
    t.start()
    return t, stop


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
                 "extension_id", "extension_build", "last_poll_wall",
                 "lost_logged",
                 "last_dispatch", "inflight")

    def __init__(self, key, instance_id, label, now, burst=0.0,
                 extension_version=None, extension_id=None,
                 extension_build=None):
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
        # The BUILD MARKER of the code this instance is actually EXECUTING —
        # a literal in the loaded module graph, reported via X-Bridge-Ext-Build.
        # None until a build that carries it polls (#324). This, not the
        # version, is what `extension_stale` is computed from.
        self.extension_build = extension_build
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
                         extension_id=None, extension_build=None) -> Instance:
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
                            extension_id=extension_id,
                            extension_build=extension_build)
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
        if extension_build is not None:
            inst.extension_build = extension_build
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

    def release_session(self, session_id, target=None) -> int:
        """Drop tab ownership held by `session_id` WITHOUT closing any real tab.
        Returns how many mappings were released.

        `target` (the request's routing hint — a routing key or an instanceId)
        SCOPES the release to that ONE instance. Ownership is keyed
        `(instance_key, session_id)`, so a single session can legitimately own a
        tab on EVERY connected Brave profile at once; an unscoped release drops
        all of them. That is right for "this session is finished" and WRONG for
        `emulate --reset --recreate`, which is fixing one tab on one profile —
        there it would orphan the other profile's owned tab, after which a later
        bare op on that profile falls back to the operator's ACTIVE tab, exactly
        the blast radius the ownership map exists to prevent.

        An unresolvable `target` releases NOTHING (returns 0) rather than falling
        back to the unscoped sweep: silently widening the blast radius when the
        caller asked to narrow it is the failure this argument exists to stop.
        """
        if session_id is None:
            return 0
        with self._cond:
            if target:
                inst = self._find_locked(target)
                # `_find_locked` matches key OR instanceId. If the instance is not
                # known at all, still honour a target that IS an owner key — a
                # profile can disconnect while its ownership row lives on.
                key = inst.key if inst is not None else target
                keys = [k for k in self._owners
                        if k[1] == session_id and k[0] == key]
            else:
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
             active_tab=None, extension_version=None, extension_id=None,
             extension_build=None):
        """Long-poll for `instance_id`/`label`: register the instance, then block
        up to `wait_timeout` for ITS next command. Returns the command dict (with
        its id), None on idle timeout, or the SUPERSEDED sentinel if this
        connection got displaced by a newer one sharing its routing key (the HTTP
        layer maps that to a distinct 409 so the extension backs off — not 204)."""
        with self._cond:
            inst = self._register_locked(instance_id, label, active_tab,
                                         extension_version, extension_id,
                                         extension_build)
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
            # No live submitter — but this may be the ABANDONED command whose
            # inflight entry we deliberately kept (see INFLIGHT_STALE_S). The reply
            # proves the extension is free again, so release it NOW instead of
            # waiting out the staleness window: that is the difference between the
            # next ping fast-failing correctly and being told to wait for nothing.
            for inst in candidates:
                if inst.inflight.pop(cid, None) is not None:
                    # It DID answer, so `last_dispatch` — whose contract is "the
                    # command it never answered", surfaced by health/whoami — must
                    # stop naming it. The normal path clears it on the same
                    # condition; this branch is simply where the abandoned command
                    # reaches the same state.
                    if (inst.last_dispatch or {}).get("id") == cid:
                        inst.last_dispatch = None
                    log("result_after_abandon", id=cid, instance_id=instance_id)
                    break
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

        Entries older than INFLIGHT_STALE_S are DISREGARDED (and pruned): past that
        point a healthy extension has certainly answered or given up, so the entry
        no longer describes current business. This is also what bounds an abandoned
        command's influence — see INFLIGHT_STALE_S for why abandoning does not drop
        the entry outright.

        CLAMPED ON BOTH SIDES, and both clamps are load-bearing:
          * never above `timeout` (cmd_timeout) — so this can NEVER be slower than
            the behaviour that predates the whole fast-ping change. `fast_timeout`
            is clamped into that range FIRST: both it and cmd_timeout are
            operator-settable (BROWSER_BRIDGE_PING_TIMEOUT / _CMD_TIMEOUT) and
            nothing validates the relation, so a fast_timeout ABOVE cmd_timeout
            would otherwise escape the ceiling through the lower clamp below
            (measured: fast=60 / cmd=20 returned 60.0).
          * never below the (clamped) `fast_timeout` — so a wedged instance whose
            budget has gone negative still gets its full fast deadline rather than
            an instant fail.

        WHY NOT `inst.last_dispatch`, which looks like it carries the same fact:
        it is overwritten by every new enqueue, so it names the NEWEST outstanding
        command rather than the oldest — a fresh enqueue behind a wedged one makes
        the wedge look young. Its `at` is also wall-clock `time.time()`, not this
        clock, so it cannot be compared against a monotonic deadline (and an NTP
        step would move it). It stays a DIAGNOSTIC field; `inflight` does timing.
        """
        if fast_timeout is None:
            return timeout
        # 🟢-D: clamp the operator-settable fast deadline into [.., timeout] BEFORE
        # it is used, so the "never above cmd_timeout" claim holds unconditionally.
        fast = min(fast_timeout, timeout)
        now = self._clock()
        self._prune_inflight_locked(inst, now)
        if not inst.inflight:
            return fast
        age = now - min(inst.inflight.values())
        budget = len(inst.inflight) * EXEC_OP_BUDGET_S + WEDGE_GRACE_S - age
        return max(fast, min(timeout, budget))

    @staticmethod
    def _prune_inflight_locked(inst, now):
        """Drop `inflight` entries that can no longer describe current business.

        TWO conditions, and the second is not optional:

        (a) older than INFLIGHT_STALE_S, AND
        (b) NO live submitter is still blocked on it (`cid not in inst.waiters`).

        🔴 WHY (b). INFLIGHT_STALE_S is measured from ENQUEUE, but a queued
        command's EXEC_OP_BUDGET_S does not start until the SERIAL extension
        dequeues it. So age alone says nothing about whether a healthy extension is
        done — precisely when N >= 2, which is the case the `len(inst.inflight) *
        EXEC_OP_BUDGET_S` term above exists to model. Measured with the injected
        clock: 3 commands, cmd_timeout=60, age 29s -> all three pruned while their
        submitters were still blocked, the instance read IDLE, and `ping` fast-
        failed at 2s while the extension legitimately had ~25s of work left. That
        is the exact workload the docstring at BROWSER_BRIDGE_CMD_TIMEOUT tells an
        operator to raise cmd_timeout for, so the advice made it worse.

        `waiters` is exactly "a submitter is still blocked on this cid": added at
        enqueue, discarded on all three exits of the wait loop, and discarded again
        in submit()'s finally so an unexpected raise cannot strand one.

        THE MEMORY BOUND IS NOT WEAKENED, it is split by owner:
          * live-submitter entries are bounded by that submitter's own deadline —
            it unwinds through the finally, which pops the entry;
          * abandoned entries have no one to bound them, which is what
            INFLIGHT_STALE_S is for.
        """
        stale = [c for c, t in inst.inflight.items()
                 if now - t > INFLIGHT_STALE_S and c not in inst.waiters]
        for c in stale:
            del inst.inflight[c]

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
        without false-negativing a busy one under ordinary concurrency (the derived
        budget is still capped at `timeout`, so >`timeout` of legitimate serial work
        can still time it out). See _effective_timeout_locked —
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
            # Set when THIS submitter gives up on a command the extension has
            # already taken. See INFLIGHT_STALE_S: abandoning does not free the
            # extension, so the inflight entry must OUTLIVE us and expire on age.
            abandoned_running = False
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
                # in the finally below, paired exactly like `pending`. Prune here
                # too so entries left behind by abandoned commands cannot pile up
                # on an instance that never receives another ping.
                _now = self._clock()
                self._prune_inflight_locked(inst, _now)
                inst.inflight[cid] = _now
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
                        # Was it still QUEUED, or already taken by the extension?
                        # Queued  -> it will never run; dropping it frees the slot
                        #            honestly and the instance really is that much
                        #            less busy.
                        # Taken   -> the extension is executing it RIGHT NOW and
                        #            will be for up to EXEC+RESULT. We are leaving,
                        #            but the work is not: keep the inflight entry so
                        #            the instance does not read as idle, and let it
                        #            expire on age (INFLIGHT_STALE_S).
                        abandoned_running = not any(c.get("id") == cid
                                                    for c in inst.outbox)
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
                # Same structural pairing for the outstanding-work clock — EXCEPT
                # when we abandoned a command the extension had already taken. Then
                # the work outlives this submitter and the entry must too, or the
                # instance reads as idle while it is provably still executing.
                # A stranded entry would be worse than a stranded pending slot (the
                # instance would look busy forever and `ping` could never fast-fail
                # again), which is why the surviving entry is bounded by age rather
                # than left to a result that may never come.
                if cid is not None and not abandoned_running:
                    inst.inflight.pop(cid, None)
                # `waiters` is discarded on all three exits of the wait loop above;
                # this is the SAFETY NET that makes it structurally leak-proof, like
                # `pending`. It matters because the staleness prune now reads
                # `waiters` as "is a submitter still blocked on this?" — an entry
                # stranded by an unexpected raise inside the loop would otherwise be
                # exempt from pruning forever. discard() is idempotent, so this is a
                # no-op on every normal path.
                if cid is not None:
                    inst.waiters.discard(cid)
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
        # extension_build is the marker of the CODE that is running (#324) —
        # the only one of these three that is not a statement about the load
        # directory, and the one `extension_stale` is computed from.
        return {"key": inst.key, "label": inst.label,
                "instanceId": inst.instance_id, "activeTab": inst.active_tab,
                "extension_version": inst.extension_version,
                "extension_id": inst.extension_id,
                "extension_build": inst.extension_build}

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
                "extension_id": inst.extension_id,
                "extension_build": inst.extension_build}

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
# 🔴 THE SHAPE OF THIS CODE IS DICTATED BY ONE FACT: `i3-msg [criteria] focus`
# EXITS 0 AND REPLIES `[{"success":true}]` WHEN THE CRITERIA MATCHED NOTHING.
# There is no way to tell a real raise from a total miss by reading the exit code
# or that reply, and the previous implementation did exactly that — reporting
# i3:"applied" for zero windows raised. So the flow is FIND (read-only
# `-t get_tree`) → RAISE (focus by the STABLE X11 window id found there) →
# VERIFY (read the tree back and require i3 to agree it is focused). See
# i3_foreground for the state/detail table.
#
# SECURITY — the title is UNTRUSTED (page-controlled; a hostile page the agent
# visited can set `document.title` to ANYTHING). The bound MUST be: the worst
# outcome is "focuses the wrong Brave window or no window", NEVER command
# execution and NEVER focusing a non-Brave window. That is enforced by:
#   * subprocess with an ARGV LIST + shell=False (no shell → no `;`/`|`/`$()`/
#     redirection surface at all). NEVER os.system / shell=True.
#   * 🔴 The title is no longer interpolated into an i3 command AT ALL. It is
#     compiled to a local Python regex (re.escape → a literal, so no ReDoS) and
#     matched against i3's `get_tree` reply in-process. The only two argvs issued
#     are the constant `["i3-msg","-t","get_tree"]` and a focus keyed on an
#     INTEGER window id taken from i3's own reply. The old criteria-breakout
#     surface (a `"` closing the `title="…"` value so trailing text is read as an
#     i3 command such as `exec`) is therefore gone by construction — the strip of
#     the structural chars below is kept as defence in depth.
#   * A `class="Brave-browser"` constraint so a matched window is always Brave.
#   * A length cap + a bounded timeout; failure/timeout is swallowed (non-fatal).
# i3-only chars that i3 uses to delimit a criteria/value. Stripped from the
# UNTRUSTED title BEFORE re.escape as belt-and-braces (see above: the title never
# reaches a criteria any more). Removing them at worst makes the title match a
# different window or none — never a breakout.
_I3_STRUCTURAL = str.maketrans("", "", '"[]\\')


def _sanitize_i3_title(title) -> str:
    """Reduce an UNTRUSTED tab title to a safe match fragment.

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


def i3_title_pattern(title):
    """The REGEX used to find the Brave window for an (UNTRUSTED) tab `title`,
    or None when nothing usable remains (→ skip).

    This is `re.escape(sanitized)`, matched with `re.search` — an unanchored
    literal substring match, which is exactly what i3's own `title="…"` criteria
    (an unanchored PCRE) used to do. The difference is WHERE it is evaluated:
    HERE, in-process, against i3's `get_tree` reply — never interpolated into an
    i3 command.

    🔴 That is a strict security IMPROVEMENT over the criteria this replaced. The
    page-controlled title no longer reaches ANY argv at all: the only two commands
    issued are a constant `-t get_tree` and a focus keyed on an INTEGER X11 window
    id taken from i3's own reply. The structural-char strip + length cap in
    _sanitize_i3_title are kept anyway (defence in depth), and re.escape means the
    compiled pattern is a literal — no metacharacter survives, so no ReDoS."""
    safe = _sanitize_i3_title(title)
    if not safe:
        return None
    return re.escape(safe)


def i3_get_tree_argv():
    """argv LIST for `i3-msg -t get_tree` — the READ-ONLY IPC query that tells us
    what windows EXIST.

    🔴 THIS IS THE ONLY THING THAT CAN ANSWER "DID ANYTHING MATCH?". Issuing
    `focus` and reading its exit code CANNOT: i3 answers a criteria that matched
    ZERO windows with `[{"success":true}]` and rc 0, byte-identical to a real
    raise. `-t get_tree` is a query — it cannot focus, move or switch anything,
    so calling it never touches the operator's screen."""
    return ["i3-msg", "-t", "get_tree"]


def i3_focus_by_id_argv(window_id):
    """argv LIST for `i3-msg [id="<x11-window-id>"] focus`.

    Keyed on the X11 window id — a STABLE handle. WM_NAME changes while the title
    settles (that is the whole bug); a window id does not, so the window we
    matched in the tree is provably the window we focus. `window_id` comes from
    i3's own reply and is re-validated as an int here, so this argv can never
    carry page-controlled text."""
    return ["i3-msg", '[id="%d"] focus' % int(window_id)]


# The X11 WM_CLASS i3 reports for Brave windows. A match is constrained to this
# so the worst case stays "the wrong BRAVE window, or none" — never some other
# application's window.
_I3_BRAVE_CLASSES = ("Brave-browser",)


def _i3_walk(node):
    """Yield every node of an i3 `get_tree` reply, depth-first (tiled + floating)."""
    if not isinstance(node, dict):
        return
    yield node
    for key in ("nodes", "floating_nodes"):
        kids = node.get(key)
        if isinstance(kids, list):
            for kid in kids:
                yield from _i3_walk(kid)


def _i3_node_class(node) -> str:
    props = node.get("window_properties")
    if isinstance(props, dict) and isinstance(props.get("class"), str):
        return props["class"]
    return ""


def i3_find_windows(tree, pattern):
    """Every Brave window in `tree` whose title matches `pattern`.

    Returns a list of (x11_window_id, focused) tuples. An EMPTY list is the state
    `i3-msg … focus` reports as success and this function reports as itself: i3
    has no such window."""
    out = []
    try:
        rx = re.compile(pattern)
    except re.error:
        return out
    for node in _i3_walk(tree):
        wid = node.get("window")
        if not isinstance(wid, int) or isinstance(wid, bool):
            continue  # containers/workspaces carry window=None
        if _i3_node_class(node) not in _I3_BRAVE_CLASSES:
            continue
        name = node.get("name")
        if not isinstance(name, str) or not rx.search(name):
            continue
        out.append((wid, bool(node.get("focused"))))
    return out


def _i3_is_focused(tree, window_id) -> bool:
    """True when the window with X11 id `window_id` is the focused one in `tree`."""
    for node in _i3_walk(tree):
        if node.get("window") == window_id:
            return bool(node.get("focused"))
    return False


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


def _i3_run(argv, *, timeout):
    """Run an i3-msg argv (shell=False, timeout-bounded). Returns
    (returncode, stdout_bytes), or None when it could not be run at all
    (i3-msg unresolvable, timeout, any exception) — all non-fatal.

    Resolves i3-msg to an ABSOLUTE path for argv[0] so the call works even under
    the minimal systemd --user service PATH."""
    i3_msg = _resolve_i3_msg()
    if i3_msg is None:
        return None
    argv = list(argv)
    argv[0] = i3_msg
    try:
        proc = subprocess.run(argv, shell=False, capture_output=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001 — best-effort; any failure is non-fatal.
        return None
    return getattr(proc, "returncode", 1), (getattr(proc, "stdout", b"") or b"")


def _i3_tree(*, timeout):
    """The parsed `i3-msg -t get_tree` reply, or None when it could not be read
    (i3-msg missing/timed out/nonzero, or the reply was not a JSON object)."""
    got = _i3_run(i3_get_tree_argv(), timeout=timeout)
    if got is None:
        return None
    rc, out = got
    if rc != 0:
        return None
    try:
        tree = json.loads(out.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — a malformed reply is "unreadable".
        return None
    return tree if isinstance(tree, dict) else None


def i3_foreground(title, *, timeout: float = None, match_wait: float = None):
    """Host-side i3 foregrounding of the Brave window matching the (UNTRUSTED)
    `title`. Returns a **(state, detail)** pair.

    🔴 `state` MUST reflect WHAT HAPPENED, not whether a command was accepted.
    The bug this replaced: it ran `i3-msg [class="Brave-browser" title="…"] focus`
    and reported "applied" on rc 0 — but i3 exits 0 and replies
    `[{"success":true}]` for a criteria that matched ZERO windows, so "applied"
    was reachable with nothing raised at all. Downstream that read as "the window
    is up", and the only failure signal anyone had said everything was fine.

    So the sequence is now FIND → RAISE → VERIFY, and every step can say no:

      state       detail              meaning
      ─────────── ─────────────────── ─────────────────────────────────────────
      "skipped"   "unavailable"       no DISPLAY / no i3-msg (headless, non-i3)
      "skipped"   "no_title"          nothing usable left of the tab title
      "applied"   "focused"           matched → focused → CONFIRMED focused
      "failed"    "no_match"          i3 has NO Brave window with that title
      "failed"    "tree_unreadable"   `-t get_tree` failed/timed out/unparseable
      "failed"    "focus_error"       the focus command itself errored
      "failed"    "not_focused"       focus accepted, window still not focused

    "applied" is now reachable ONLY via a get_tree that listed the window AND a
    second get_tree that confirms it is focused. `state` keeps the exact three
    values callers already branch on ("applied"/"skipped"/"failed"), with
    not-raised now correctly landing in "failed"; `detail` is additive.

    Non-fatal throughout — the Chrome-side activate result still returns."""
    if timeout is None:
        timeout = I3_MSG_TIMEOUT
    if match_wait is None:
        match_wait = I3_MATCH_WAIT
    if not i3_available():
        return "skipped", "unavailable"
    pattern = i3_title_pattern(title)
    if pattern is None:
        return "skipped", "no_title"

    # 1. FIND. A READ-ONLY get_tree is the only thing that can distinguish
    #    "matched one window" from "matched nothing" — see i3_get_tree_argv.
    #    Re-read for up to match_wait while there is no match: right after an
    #    `open` the window's WM_NAME still holds the OLD title (see I3_MATCH_WAIT).
    #    A tree that cannot be READ is not a race — bail immediately, no retry.
    tree = _i3_tree(timeout=timeout)
    if tree is None:
        log("activate_i3_failed", reason="tree_unreadable")
        return "failed", "tree_unreadable"
    matches = i3_find_windows(tree, pattern)
    deadline = time.monotonic() + max(0.0, match_wait)
    while not matches and time.monotonic() < deadline:
        time.sleep(min(I3_MATCH_POLL, max(0.0, deadline - time.monotonic())))
        tree = _i3_tree(timeout=timeout)
        if tree is None:
            log("activate_i3_failed", reason="tree_unreadable")
            return "failed", "tree_unreadable"
        matches = i3_find_windows(tree, pattern)
    if not matches:
        # The old code's silent lie. Nothing was raised; say so.
        log("activate_i3_failed", reason="no_match")
        return "failed", "no_match"

    # 2. RAISE by STABLE X11 window id — immune to the title settling underneath
    #    us between the tree read and the focus.
    window_id = matches[0][0]
    got = _i3_run(i3_focus_by_id_argv(window_id), timeout=timeout)
    if got is None or got[0] != 0:
        log("activate_i3_failed", reason="focus_error",
            rc=(got[0] if got is not None else None))
        return "failed", "focus_error"

    # 3. VERIFY. rc 0 from `focus` still proves nothing (an id that vanished
    #    between the two calls also answers success:true), so read the tree back
    #    and require i3 to agree the window is focused.
    tree = _i3_tree(timeout=timeout)
    if tree is None:
        log("activate_i3_failed", reason="tree_unreadable")
        return "failed", "tree_unreadable"
    if not _i3_is_focused(tree, window_id):
        log("activate_i3_failed", reason="not_focused")
        return "failed", "not_focused"
    return "applied", "focused"


def _result_title(result) -> str:
    """The tab title from an `activate` result envelope ({id,ok,data:{title}}),
    or "" if absent. This is the UNTRUSTED, page-controlled string."""
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("title"), str):
            return data["title"]
    return ""


def _annotate_i3(result, state: str, detail: str = "") -> None:
    """Record the i3-foregrounding outcome on the activate result's data:

        {..., "i3": "applied"|"skipped"|"failed", "i3_detail": "<why>"}

    `i3` keeps EXACTLY the three values callers already branch on — a consumer
    that treats "failed" as fatal keeps working, and now correctly sees a raise
    that matched nothing (which used to report "applied"). `i3_detail` is the
    additive field that separates "I asked and nothing matched" ("no_match")
    from "i3 is not there" ("unavailable") from a real raise ("focused"); see
    i3_foreground for the full table. Never emits the title."""
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        result["data"]["i3"] = state
        result["data"]["i3_detail"] = detail


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
            """(instance_id, label, active_tab, ext_version, ext_id,
            ext_build) from the /poll headers.

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
            # The build marker (#324) — same tight identity cap as the rest; a
            # build predating it omits the header → None → undecidable verdict.
            raw_build = self.headers.get(HDR_EXT_BUILD)
            ext_build = (unquote(raw_build).strip()[:MAX_IDENTITY_CHARS]
                         if raw_build else None)
            return instance_id, label, active, ext_version, ext_id, ext_build

        def _whoami(self) -> dict:
            """Assemble the read-only whoami snapshot: which HOST + which browser
            profiles/instances + bridge diagnostics. Metadata only (host label,
            instance labels, active-tab DOMAINs, versions — NEVER page content),
            so it is safe to expose exactly like /health (bearer + Host guarded,
            NOT rate-limited). Reached only after _guard() in do_GET."""
            expected = manifest_version()
            expected_build = build_marker()
            insts = annotate_staleness(registry.whoami_snapshot(), expected,
                                       expected_build)
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
                    # The BUILD MARKER the server expects the running code to
                    # carry, read from the deployed extension/build_id.js (#324).
                    # An ALL-CLEAR is computed against THIS and nothing else —
                    # the version above can never certify code as current,
                    # because it (and `extension_id`) describe the load DIRECTORY
                    # rather than the executing code. null here → no verdict can
                    # be false; each is null unless that instance's version is
                    # known and DISAGREES, which still decides true. Either way,
                    # null is the honest answer, not "fine".
                    "extension_build_current": expected_build,
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
                expected_build = build_marker()
                insts = annotate_staleness(registry.snapshot(), expected,
                                           expected_build)
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
                                 "extension_build_current": expected_build,
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
                #
                # The SESSION HEADERS are passed through, though: these are
                # operator subcommands (`browser whoami` / `browser health`), not
                # server-originated rows, so they attribute like any other command
                # — see _emit_diag_event for why excluding them was wrong.
                _emit_diag_event("health", t0,
                                 self.headers.get(HDR_SESSION_ID),
                                 self.headers.get(HDR_SESSION_ORIGIN))
                return
            if path == "/instances":
                insts = registry.snapshot()
                self._send(200, {"ok": True, "count": len(insts),
                                 "instances": insts})
                return
            if path == "/whoami":
                self._send(200, self._whoami())
                _emit_diag_event("whoami", t0,
                                 self.headers.get(HDR_SESSION_ID),
                                 self.headers.get(HDR_SESSION_ORIGIN))   # see the /health emit above
                return
            if path == "/poll":
                (instance_id, label, active, ext_version,
                 ext_id, ext_build) = self._poll_identity()
                cmd = registry.poll(instance_id, label, poll_timeout,
                                    active_tab=active,
                                    extension_version=ext_version,
                                    extension_id=ext_id,
                                    extension_build=ext_build)
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
            # A nested `browser agent` run declares itself here so its forwarded
            # invoker id is never attributed to the invoker as usage. Absent for
            # every ordinary caller. See HDR_SESSION_ORIGIN.
            # 🔴 NO `or None` — that would collapse a PRESENT-but-empty header
            # into "absent" and attribute a call whose caller explicitly
            # disclaimed it. `.get()` returns None only when the header is really
            # missing, which is the distinction the emitter branches on.
            session_origin = self.headers.get(HDR_SESSION_ORIGIN)
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
            # touching the real Brave tab or the extension. `target` (the popped
            # --instance routing hint) SCOPES it to one profile — see
            # release_session: unscoped, it would drop this session's owned tab on
            # every OTHER connected profile too.
            if op == "release":
                n = registry.release_session(session_id, target=target)
                self._send(200, {"ok": True, "result": {
                    "id": None, "ok": True, "data": {"released": n}}})
                emit_cmd_event(
                    op=op, key=(target or ""), outcome="ok",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    domain="", exit_code=0, session_id=session_id,
                    session_origin=session_origin, attribute_session=True)
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
                # NEXT storm is attributable in activity.events (the audit
                # couldn't attribute the 44K flood).
                # `sess` is KEPT alongside the new `session` column, deliberately.
                # The column is filled for the `claude` tier and non-nested calls
                # ONLY — and a flood driven from a tmux/sid/ppid/unknown tier, or
                # from a nested browser-agent run, is exactly the case where you
                # still need SOME stable handle to tell one flooder from two. It
                # is 8 hex, on the throttle path only.
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
                    domain="", exit_code=1, extra=extra, session_id=session_id,
                    session_origin=session_origin, attribute_session=True)
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
                    # title is UNTRUSTED (page-controlled) and never reaches an
                    # i3 command (see i3_title_pattern). Skipped gracefully off i3.
                    # 🔴 `state` is EARNED, not assumed: i3-msg exits 0 even when
                    # the criteria matched nothing, so i3_foreground confirms the
                    # window exists and ends up focused before saying "applied".
                    state, detail = i3_foreground(_result_title(result))
                    _annotate_i3(result, state, detail)
                    log("activate_i3", state=state, detail=detail)
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
                           domain=domain, exit_code=exit_code, extra=extra,
                           session_id=session_id, session_origin=session_origin,
                           attribute_session=True)

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
        ping_timeout=ping_timeout,
        heartbeat_s=HEARTBEAT_INTERVAL_S)
    _warn_poll_timeout_vs_extension_budget(poll_timeout)
    _heartbeat, hb_stop = start_heartbeat(registry)  # daemon thread; stopped below
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if hb_stop is not None:
            hb_stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
