#!/usr/bin/env python3
"""receiver — localhost bridge: browser extension → activity-collector spool.

A tiny stdlib http.server that accepts the MV3 extension's POSTs at
`/event` and writes each as a v1 emit-format record into the local spool, so the
existing collector daemon ships browser activity unchanged. Bound to 127.0.0.1
only — it must never be reachable off-host.

Incoming JSON (from service_worker.js buildEvent):
    { kind: "nav"|"focus", url, title, active_ms, state, ts }

Emitted spool record (v1 contract):
    source=browser  kind=<nav|focus>  text=<url>  app=<browser>
    payload=<json: title, active_ms, state>

Why a separate service (not folded into collector.py): the collector daemon's
job is rotate→ship→ClickHouse and it has NO inbound network surface by design;
bolting an HTTP listener onto it would couple a privileged shipper to an
attacker-reachable socket. The receiver is a thin, separately-restartable
write-only adapter — failure of one does not take down the other. It shares the
spool_emit module with the keylogger so the v1 line format has a single source
of truth.

Config (env):
    ACTIVITY_SPOOL_DIR    spool dir (default ~/.local/state/activity/spool)
    BROWSER_RECEIVER_HOST  bind host (default 127.0.0.1 — keep loopback)
    BROWSER_RECEIVER_PORT  bind port (default 8787)
    BROWSER_APP            app label for records (default "chromium")
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# spool_emit lives in the sibling keylog/ dir (single source of truth for the
# v1 line format). Use the INVOKED path — do NOT .resolve(): home-manager
# symlinks each deployed file to a flat /nix/store object, so resolving discards
# the browser-ext/ ↔ keylog/ sibling layout and breaks this import at runtime.
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "keylog"))
import spool_emit as SE  # noqa: E402

MAX_BODY = 64 * 1024  # a nav event is tiny; cap to avoid abuse.


def event_to_fields(evt: dict, app_default: str) -> dict:
    """Map an incoming extension event dict → v1 emit fields.

    `text` is the full URL (full-content choice). title/active_ms/state plus
    scroll engagement (scroll_pct/scroll_ms) go into payload JSON. `app` is the
    browser label (chromium/brave). Robust to missing keys — anything absent
    becomes empty/0.
    """
    kind = evt.get("kind") or "nav"
    if kind not in ("nav", "focus"):
        kind = "nav"
    payload = {
        "title": evt.get("title", "") or "",
        "active_ms": int(evt.get("active_ms") or 0),
        "state": evt.get("state", "") or "",
        # Scroll engagement for this page view (nav events). Default 0 when the
        # extension didn't send them (focus events, older client, no scrolling).
        "scroll_pct": int(evt.get("scroll_pct") or 0),
        "scroll_ms": int(evt.get("scroll_ms") or 0),
    }
    if evt.get("ts"):
        payload["client_ts"] = evt["ts"]
    return {
        "source": "browser",
        "kind": kind,
        "text": evt.get("url", "") or "",
        "app": evt.get("app") or app_default,
        "project": "",
        "session": "",
        "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }


def make_handler(spool_dir: Path, app_default: str):
    class Handler(BaseHTTPRequestHandler):
        # Silence default stderr logging spam; the daemon journal is enough.
        def log_message(self, *a):  # noqa: A003
            pass

        def _reply(self, code: int, body: bytes = b""):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # The page making the fetch is a privileged extension worker; still,
            # only loopback is bound. Allow the extension origin generically.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            if self.path == "/health":
                self._reply(200, b'{"ok":true}')
            else:
                self._reply(404)

        def do_POST(self):
            if self.path != "/event":
                self._reply(404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._reply(400)
                return
            if length <= 0 or length > MAX_BODY:
                self._reply(400)
                return
            raw = self.rfile.read(length)
            try:
                evt = json.loads(raw.decode("utf-8"))
                if not isinstance(evt, dict):
                    raise ValueError("not an object")
            except (ValueError, UnicodeDecodeError):
                self._reply(400, b'{"error":"bad json"}')
                return
            fields = event_to_fields(evt, app_default)
            SE.emit(fields, spool_dir=spool_dir)
            self._reply(200, b'{"ok":true}')

    return Handler


def main(argv=None) -> int:
    spool_dir = SE.default_spool_dir()
    host = os.environ.get("BROWSER_RECEIVER_HOST", "127.0.0.1")
    port = int(os.environ.get("BROWSER_RECEIVER_PORT", "8787"))
    app_default = os.environ.get("BROWSER_APP", "chromium")

    handler = make_handler(spool_dir, app_default)
    server = ThreadingHTTPServer((host, port), handler)
    print(
        f"browser-receiver: listening on http://{host}:{port}/event "
        f"spool={spool_dir} app={app_default}",
        file=sys.stderr, flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
