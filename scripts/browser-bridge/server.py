#!/usr/bin/env python3
"""browser-bridge server — loopback rendezvous between a Claude skill and the
live Brave browser.

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

Security contract (defeats DNS-rebinding / local malware reaching the socket):
  * Binds 127.0.0.1 only (env `BROWSER_BRIDGE_HOST`, default 127.0.0.1).
  * Bearer-token auth on EVERY endpoint — skill requests and the extension's
    long-poll alike. The secret lives in `~/.config/browser-bridge/token`,
    created 0600 with `secrets.token_urlsafe` on first run. 401 otherwise.
  * Host-header allowlist: only 127.0.0.1 / localhost / ::1 accepted (403
    otherwise) — a DNS-rebind victim page hits us with a foreign Host header.

Config (env):
    BROWSER_BRIDGE_HOST          bind host (default 127.0.0.1 — keep loopback)
    BROWSER_BRIDGE_PORT          bind port (default 8788 — must NOT be 8787)
    BROWSER_BRIDGE_TOKEN_FILE    token path (default ~/.config/browser-bridge/token)
    BROWSER_BRIDGE_CMD_TIMEOUT   seconds a /cmd waits for a result (default 20)
    BROWSER_BRIDGE_POLL_TIMEOUT  seconds a /poll blocks before 204 (default 25)
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --- protocol contract (shared with extension/protocol.js — keep in sync) ---- #
# The op set the bridge accepts. Both sides validate against this exact set; the
# JS side mirrors it in extension/protocol.js (asserted by the extension test).
ALLOWED_OPS = ("getHtml", "eval", "tabs", "nav", "screenshot")

# Per-op required fields (skill-supplied). Absent → 400 bad_request.
REQUIRED_FIELDS = {
    "eval": ("js",),
    "nav": ("url",),
}

MAX_CMD_BODY = 256 * 1024      # a command is tiny (op + a url / a js snippet).
MAX_RESULT_BODY = 32 * 1024 * 1024  # a screenshot data URL can be a few MB.

# A connection is considered "present" if a long-poll landed within this window.
CONNECT_STALE_S = 40.0

_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


# --------------------------------------------------------------------------- #
# Structured logging (JSON lines to stderr, like the receiver's journal usage)
# --------------------------------------------------------------------------- #
def log(event: str, **fields) -> None:
    rec = {"ts": round(time.time(), 3), "event": event, **fields}
    print(json.dumps(rec, ensure_ascii=False, separators=(",", ":")),
          file=sys.stderr, flush=True)


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
# Bridge — transport-agnostic rendezvous core (thread-safe, fully unit-testable)
# --------------------------------------------------------------------------- #
class BridgeTimeout(Exception):
    """A submitted command was not answered within its deadline."""


class NoExtension(Exception):
    """No extension is currently connected to pick up the command."""


class Bridge:
    """Correlates skill commands with extension replies via ids.

    `submit()` (skill side) enqueues a command and blocks for its reply.
    `next_command()` (extension long-poll) dequeues the next pending command.
    `deliver_result()` (extension) completes the matching `submit()`.

    All state is guarded by a single Condition; ThreadingHTTPServer serves each
    request in its own thread, so blocking here is safe and cheap.
    """

    def __init__(self, clock=time.monotonic):
        self._cond = threading.Condition()
        self._outbox: deque[dict] = deque()   # commands awaiting pickup
        self._results: dict[str, dict] = {}    # id -> result payload
        self._waiters: set[str] = set()        # ids with a live submit() waiting
        self._active_polls = 0
        self._last_poll = 0.0
        self._clock = clock

    # --- skill side -------------------------------------------------------- #
    def submit(self, command: dict, timeout: float) -> dict:
        """Enqueue `command` (a dict without id), block for its reply.

        Raises NoExtension if nothing is connected to service it, or
        BridgeTimeout if no reply arrives within `timeout` seconds.
        """
        with self._cond:
            if not self._connected_locked():
                raise NoExtension()
            cid = secrets.token_hex(8)
            cmd = dict(command)
            cmd["id"] = cid
            self._outbox.append(cmd)
            self._waiters.add(cid)
            self._cond.notify_all()  # wake a waiting poller
            deadline = self._clock() + timeout
            while cid not in self._results:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._waiters.discard(cid)
                    self._drop_from_outbox_locked(cid)
                    raise BridgeTimeout()
                self._cond.wait(remaining)
            self._waiters.discard(cid)
            return self._results.pop(cid)

    def _drop_from_outbox_locked(self, cid: str) -> None:
        # Remove a still-unpicked command on timeout so a late poller never
        # dispatches an already-abandoned request.
        self._outbox = deque(c for c in self._outbox if c.get("id") != cid)

    # --- extension side ---------------------------------------------------- #
    def next_command(self, wait_timeout: float):
        """Long-poll: block up to `wait_timeout` for the next command.

        Returns the command dict (with its id) or None on timeout. Marks the
        extension as connected for the duration + a stale window afterwards.
        """
        with self._cond:
            self._active_polls += 1
            self._last_poll = self._clock()
            try:
                deadline = self._clock() + wait_timeout
                while not self._outbox:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return None
                    self._cond.wait(remaining)
                return self._outbox.popleft()
            finally:
                self._active_polls -= 1
                self._last_poll = self._clock()

    def deliver_result(self, cid: str, payload: dict) -> bool:
        """Record a reply for `cid`. Returns False if no submit() awaits it
        (unknown/expired id) so the extension can log-and-drop."""
        with self._cond:
            if cid not in self._waiters:
                return False
            self._results[cid] = payload
            self._cond.notify_all()
            return True

    # --- health ------------------------------------------------------------ #
    def _connected_locked(self) -> bool:
        if self._active_polls > 0:
            return True
        return (self._clock() - self._last_poll) < CONNECT_STALE_S

    @property
    def connected(self) -> bool:
        with self._cond:
            return self._connected_locked()


# --------------------------------------------------------------------------- #
# Command validation
# --------------------------------------------------------------------------- #
def validate_command(body):
    """Return (op, None) if valid, else (None, error_string)."""
    if not isinstance(body, dict):
        return None, "body_not_object"
    op = body.get("op")
    if op not in ALLOWED_OPS:
        return None, "unknown_op"
    for field in REQUIRED_FIELDS.get(op, ()):
        if not body.get(field):
            return None, f"missing_field:{field}"
    return op, None


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
def make_handler(bridge: Bridge, token: str, cmd_timeout: float,
                 poll_timeout: float):
    class Handler(BaseHTTPRequestHandler):
        server_version = "browser-bridge/1"

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

        # --- routes -------------------------------------------------------- #
        def do_GET(self):
            if not self._guard():
                return
            if self.path == "/health":
                self._send(200, {"ok": True,
                                 "extension_connected": bridge.connected})
                return
            if self.path == "/poll":
                cmd = bridge.next_command(poll_timeout)
                if cmd is None:
                    self._send(204)
                else:
                    log("dispatch", id=cmd.get("id"), op=cmd.get("op"))
                    self._send(200, cmd)
                return
            self._send(404, {"ok": False, "error": "not_found"})

        def do_POST(self):
            if not self._guard():
                return
            if self.path == "/cmd":
                self._handle_cmd()
            elif self.path == "/result":
                self._handle_result()
            else:
                self._send(404, {"ok": False, "error": "not_found"})

        def _handle_cmd(self):
            body, err = self._read_body(MAX_CMD_BODY)
            if err:
                self._send(400, {"ok": False, "error": err})
                return
            op, verr = validate_command(body)
            if verr:
                self._send(400, {"ok": False, "error": verr,
                                 "op": body.get("op") if isinstance(body, dict)
                                 else None})
                return
            try:
                result = bridge.submit(body, timeout=cmd_timeout)
            except NoExtension:
                log("cmd_no_extension", op=op)
                self._send(503, {"ok": False, "error": "extension_not_connected"})
                return
            except BridgeTimeout:
                log("cmd_timeout", op=op)
                self._send(504, {"ok": False, "error": "timeout"})
                return
            log("cmd_ok", op=op)
            self._send(200, {"ok": True, "result": result})

        def _handle_result(self):
            body, err = self._read_body(MAX_RESULT_BODY)
            if err:
                self._send(400, {"ok": False, "error": err})
                return
            if not isinstance(body, dict) or "id" not in body:
                self._send(400, {"ok": False, "error": "missing_id"})
                return
            cid = body["id"]
            delivered = bridge.deliver_result(cid, body)
            if not delivered:
                log("result_unknown_id", id=cid)
                self._send(200, {"ok": False, "error": "unknown_id"})
                return
            self._send(200, {"ok": True})

    return Handler


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def build_server(host: str, port: int, bridge: Bridge, token: str,
                 cmd_timeout: float, poll_timeout: float) -> ThreadingHTTPServer:
    handler = make_handler(bridge, token, cmd_timeout, poll_timeout)
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

    bridge = Bridge()
    server = build_server(host, port, bridge, token, cmd_timeout, poll_timeout)
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
