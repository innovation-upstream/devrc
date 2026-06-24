# browser-ext — Chrome MV3 browser-activity collector

Tracks the active tab (full URL + title), active-duration, and focus/idle
transitions, and POSTs each event to a localhost receiver that writes it into
the activity-collector spool (v1 emit format) for the existing daemon to ship.

Full URL capture, local-only — consistent with the full-content self-instrumentation
choice. Nothing leaves the host from here; shipping is the daemon's decision and
is gated on an authenticated ClickHouse.

## Components
- `manifest.json` — MV3 manifest. Permissions: `tabs`, `webNavigation`, `idle`;
  host permission for `http://127.0.0.1:8787/*` only.
- `service_worker.js` — background worker. Listens on `chrome.tabs.onActivated`,
  `chrome.webNavigation.onCommitted`, `chrome.tabs.onUpdated` (title), 
  `chrome.windows.onFocusChanged`, and `chrome.idle.onStateChanged`. Computes
  active-duration across tab switches (persisted in `chrome.storage.session` so
  it survives MV3 worker suspension) and POSTs events to the receiver.
- `receiver.py` — stdlib `http.server` bound to `127.0.0.1:8787`. Accepts
  `POST /event`, maps the JSON to a v1 spool record, appends to the spool.
  Shares `../keylog/spool_emit.py` (single source of truth for the line format).

## localhost endpoint contract
`POST http://127.0.0.1:8787/event`, `Content-Type: application/json`:

```json
{ "kind": "nav" | "focus",
  "url":   "https://full/url",      // full URL (nav events)
  "title": "tab title",
  "active_ms": 1234,                  // ms the previous tab was focused
  "state": "focused|blurred|idle|active|locked",  // focus events
  "ts": 1719240000000 }              // client epoch ms
```

Receiver writes:
```
source=browser  kind=<nav|focus>  text=<url>  app=<chromium|brave>
payload={"title":…,"active_ms":…,"state":…,"client_ts":…}
```
`GET /health` → `{"ok":true}`.

## Load unpacked (chromium / brave)
1. Start the receiver:
   `nix-shell -p python3 --run "python3 scripts/collector/browser-ext/receiver.py"`
   (or enable the staged `browser-activity-receiver` user service).
2. Open `chrome://extensions` (or `brave://extensions`).
3. Toggle **Developer mode** (top-right).
4. **Load unpacked** → select `scripts/collector/browser-ext/`.
5. Browse. Switch tabs / navigate; `GET http://127.0.0.1:8787/health` confirms the
   receiver is up, and `tail -f $ACTIVITY_SPOOL_DIR/current.log` shows records.

To point at a TEST spool while validating:
`ACTIVITY_SPOOL_DIR=/tmp/activity-test-spool python3 receiver.py`

## Config (receiver env)
- `ACTIVITY_SPOOL_DIR` — spool dir (default `~/.local/state/activity/spool`).
- `BROWSER_RECEIVER_HOST` / `BROWSER_RECEIVER_PORT` — bind (default `127.0.0.1:8787`).
  Keep the host on loopback.
- `BROWSER_APP` — app label written to records (default `chromium`; set `brave`).

## Verification status
- Receiver: fully unit-tested (event→fields, real loopback POST→spool round-trip
  through `collector.parse_line`, arbitrary content incl. unicode/quotes/newlines/
  a fake password, bad-JSON 400, wrong-path 404). See `tests/test_receiver.py`.
- Manifest + service-worker logic: validated (manifest JSON parses, MV3 schema
  fields present; `buildEvent` payload shape mirrored by the receiver test).
- The end-to-end **load-unpacked in a real browser** step is a MANUAL step (MV3
  service workers are not reliably driveable headlessly). Follow "Load unpacked"
  above to complete it.
