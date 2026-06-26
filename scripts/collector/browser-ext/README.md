# browser-ext — Chrome MV3 browser-activity collector

Tracks the active tab (full URL + title), active-duration, scroll engagement,
and focus/idle transitions, and POSTs each event to a localhost receiver that
writes it into the activity-collector spool (v1 emit format) for the existing
daemon to ship.

Full URL capture, local-only — consistent with the full-content self-instrumentation
choice. Nothing leaves the host from here; shipping is the daemon's decision and
is gated on an authenticated ClickHouse.

## Components
- `manifest.json` — MV3 manifest. Permissions: `tabs`, `webNavigation`, `idle`,
  `storage`. Host permission for `http://127.0.0.1:8787/*` only. **A
  `content_scripts` entry injects `content_scroll.js` into all `http(s)://*`
  pages** for scroll capture — this broadens the extension's host access to ALL
  sites (intended; the script only reads scroll geometry and never touches page
  content, and nothing leaves localhost).
- `service_worker.js` — background worker. Listens on `chrome.tabs.onActivated`,
  `chrome.webNavigation.onCommitted`, `chrome.tabs.onUpdated` (title),
  `chrome.windows.onFocusChanged`, `chrome.idle.onStateChanged`,
  `chrome.runtime.onMessage` (scroll updates), and `chrome.tabs.onRemoved`.
  Computes active-duration across tab switches and stores per-tab scroll metrics
  (both persisted in `chrome.storage.session` so they survive MV3 worker
  suspension); folds the LEAVING tab's scroll metrics into its `nav` event.
- `content_scroll.js` — content script (all http/https pages). Thin DOM/chrome
  wiring: throttled `scroll` listener → pure tracker → throttled
  `chrome.runtime.sendMessage` updates + a final flush on
  `visibilitychange`/`pagehide`. Best-effort; swallows errors when the SW is
  asleep.
- `scroll_track.js` — PURE scroll-engagement accumulator (no `window`/`document`/
  `chrome`, no `Date.now()`; loaded by the content script via dynamic `import()`
  and listed in `web_accessible_resources`). Unit-tested in plain Node.
- `receiver.py` — stdlib `http.server` bound to `127.0.0.1:8787`. Accepts
  `POST /event`, maps the JSON to a v1 spool record, appends to the spool.
  Shares `../keylog/spool_emit.py` (single source of truth for the line format).

### Scroll metrics (per page view, on the `nav` event)
- `scroll_pct` — MAX reading depth reached, `round(100*(scrollY+innerHeight)/
  max(1, scrollHeight))` clamped 0–100; monotonic per view (scrolling back up
  does not lower it).
- `scroll_ms` — accumulated ACTIVE-scroll time: sum of scrolling bursts, where
  consecutive throttled samples <1s apart extend the current burst (idle reading
  time between scrolls is not counted).

## localhost endpoint contract
`POST http://127.0.0.1:8787/event`, `Content-Type: application/json`:

```json
{ "kind": "nav" | "focus",
  "url":   "https://full/url",      // full URL (nav events)
  "title": "tab title",
  "active_ms": 1234,                  // ms the previous tab was focused
  "scroll_pct": 88,                   // max reading depth % of the leaving page
  "scroll_ms": 12500,                 // active-scroll time on the leaving page
  "state": "focused|blurred|idle|active|locked",  // focus events
  "ts": 1719240000000 }              // client epoch ms
```

Receiver writes:
```
source=browser  kind=<nav|focus>  text=<url>  app=<chromium|brave>
payload={"title":…,"active_ms":…,"state":…,"scroll_pct":…,"scroll_ms":…,"client_ts":…}
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
- Active-time accounting: the idle/blur-aware accumulator lives in the PURE
  `active_time.js` (no `chrome.*`, no `Date.now()` — all timestamps passed in)
  and is unit-tested in `tests/active_time.test.mjs`. Run with Node's built-in
  runner; pass a glob (a bare directory positional is treated as a module on
  Node ≥22, so glob or run from inside the dir):
  `nix-shell -p nodejs --run "node --test 'scripts/collector/browser-ext/tests/**/*.test.mjs'"`.
- Scroll engagement: the PURE `scroll_track.js` (same no-`chrome`/no-`Date.now()`
  discipline) is unit-tested in `tests/scroll_track.test.mjs` (max-depth
  monotonicity, 0–100 clamp, burst accrual with the >1s gap rule, snapshot/reset).
  `content_scroll.js` (DOM/chrome wiring) and the SW message→nav fold are NOT
  exercised headlessly — verify in the load-unpacked step below.
- The end-to-end **load-unpacked in a real browser** step is a MANUAL step (MV3
  service workers are not reliably driveable headlessly). Follow "Load unpacked"
  above to complete it.
