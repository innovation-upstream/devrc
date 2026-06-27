# browser-ext — Chrome MV3 browser-activity collector

Tracks the active tab (full URL + title) and per-page scroll engagement, and
POSTs a `nav` event to a localhost receiver that writes it into the
activity-collector spool (v1 emit format) for the existing daemon to ship.

Full URL capture, local-only — consistent with the full-content self-instrumentation
choice. Nothing leaves the host from here; shipping is the daemon's decision and
is gated on an authenticated ClickHouse.

> **Retired:** per-page `active_ms` (active-engagement time) and the
> `focus`/idle events that drove it have been **removed**. The extension's
> `active_ms` was structurally wrong on the i3 WM (`chrome.idle` measures
> system-wide input and `chrome.windows.onFocusChanged` blur is unreliable on i3,
> so it counted time spent in OTHER apps as browser engagement). True per-domain
> browser attention is now **derived downstream** by intersecting i3
> "Brave-focused" intervals with the active-tab domain timeline (see
> `scripts/validation/` and the Grafana dashboard). Removing the field is a
> noise-reduction cleanup — it takes effect only after an operator reloads the
> unpacked extension in Brave. Nav + scroll capture are unchanged.

## Components
- `manifest.json` — MV3 manifest. Permissions: `tabs`, `webNavigation`,
  `storage` (the `idle` permission was removed with the focus/idle wiring). Host
  permission for `http://127.0.0.1:8787/*` only. **A `content_scripts` entry
  injects TWO scripts — `scroll_track.js` then `content_scroll.js`, in that
  order — into all `http(s)://*` pages** for scroll capture. **Load order
  matters:** content scripts of the same extension share ONE isolated-world
  global scope, so the first script (`scroll_track.js`) publishes its factory on
  `globalThis.__activityScrollTracker` and the second (`content_scroll.js`) reads
  it synchronously — no dynamic `import()` and no `web_accessible_resources`.
  (The old dynamic-import-of-a-web-accessible-resource path was CSP-fragile and
  failed silently, leaving `scroll_pct` stuck at 0.) Injecting content scripts
  broadens host access to ALL sites (intended; the scripts only read scroll
  geometry, never page content, and nothing leaves localhost).
- `service_worker.js` — background worker. Listens on `chrome.tabs.onActivated`,
  `chrome.webNavigation.onCommitted` (top-frame), `chrome.tabs.onUpdated`
  (title), `chrome.runtime.onMessage` (scroll updates), and
  `chrome.tabs.onRemoved`. Tracks the current `{tabId, url, title}` and per-tab
  scroll metrics (both persisted in `chrome.storage.session` so they survive MV3
  worker suspension); folds the LEAVING tab's scroll metrics into its `nav`
  event. **All read-modify-write access to that persisted state goes through the
  serialized store in `state_store.js`** — the worker is thin wiring only.
- `state_store.js` — the SERIALIZED (race-free) tab + scroll store. The chrome
  event handlers fire concurrently and each used to do a non-atomic
  `getState → mutate → setState` against `chrome.storage.session`; they
  interleaved at the `await` points and clobbered each other's writes (a
  double-fired switch — `onActivated` + `onCommitted` for one switch — emitting
  duplicate `nav` events; the scroll map's read-delete-write losing a concurrent
  put for a different tab). The store wraps every state mutation (`onTabChange`,
  `updateTitle`) in a promise-chain async mutex so each runs to completion before
  the next, and a SEPARATE mutex serializes the scroll map's read-delete-write.
  It also SUPPRESSES the redundant second `nav` when an incoming `{tabId,url}`
  equals the current stored one (same-tab *different*-url still emits — that's a
  real in-tab navigation). Storage + `post` are injected so the concurrency logic
  is unit-testable without a real Chrome.
- `content_scroll.js` — content script (all http/https pages), injected SECOND.
  Thin DOM/chrome wiring: a **capture-phase, document-level** `scroll` listener
  (`document.addEventListener("scroll", …, { capture: true, passive: true })`)
  so scroll from ANY element is caught — including SPA inner containers
  (Discord/Gmail/Grafana) that scroll a `div` rather than the document and never
  bubble a `window`-level scroll event. It derives `(pos, viewport, total)` from
  the scrolled element via the pure `deriveScrollGeometry` helper, feeds throttled
  samples to the tracker (which keeps the MAX depth across all qualifying
  scrollers this view), and sends throttled `chrome.runtime.sendMessage` updates
  + a final flush on `visibilitychange`/`pagehide`. Best-effort; swallows errors
  when the SW is asleep.
- `scroll_track.js` — PURE scroll accounting, injected FIRST as a CLASSIC
  (non-ESM) script. No `window`/`document`/`chrome`, no `Date.now()`; no
  top-level `export`/`import`. It publishes its factory on the shared isolated-
  world global (`globalThis.__activityScrollTracker`) plus a pure
  `deriveScrollGeometry(target, doc, win)` helper and the `MIN_SCROLLABLE_RATIO`
  trivial-scroller threshold (1.3 — an inner container must overflow its own
  viewport by ≥1.3× to count, so tiny dropdowns/menus don't report 100% depth).
  Unit-tested in plain Node (the test loads it for its side effect and reads the
  global).
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
{ "kind": "nav",
  "url":   "https://full/url",      // full URL
  "title": "tab title",
  "scroll_pct": 88,                   // max reading depth % of the leaving page
  "scroll_ms": 12500,                 // active-scroll time on the leaving page
  "ts": 1719240000000 }              // client epoch ms
```

Receiver writes:
```
source=browser  kind=nav  text=<url>  app=<chromium|brave>
payload={"title":…,"scroll_pct":…,"scroll_ms":…,"client_ts":…}
```
`GET /health` → `{"ok":true}`. (The receiver is robust to a legacy client that
still sends `active_ms`/`state`/a `focus` kind — those are coerced to a `nav` and
the retired fields are dropped from the payload — but the current extension never
sends them.)

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
  a fake password, a legacy `active_ms`/`state`/`focus` payload coerced+stripped,
  bad-JSON 400, wrong-path 404). See `tests/test_receiver.py`.
- Manifest + service-worker logic: validated (manifest JSON parses, MV3 schema
  fields present; `buildEvent` payload shape mirrored by the receiver test).
- State-store concurrency: `state_store.js` is driven by `tests/state_store.test.mjs`
  with a FAKE in-memory `chrome.storage.session` (async get/set with a delay that
  forces interleave) and a fake `post`. It reproduces the double-fired
  `onActivated`+`onCommitted` switch (asserts exactly one `nav`), the redundant-nav
  suppression, tab-title tracking, the scroll-map read-delete-write serialization,
  and asserts the `nav` payload carries url/title/scroll but NOT the retired
  `active_ms`/`state`. (These tests FAIL if the mutex is neutered.)
- Scroll engagement: the PURE `scroll_track.js` (no-`chrome`/no-`Date.now()`
  discipline) is unit-tested in `tests/scroll_track.test.mjs` (max-depth
  monotonicity, 0–100 clamp, burst accrual with the >1s gap rule, snapshot/reset,
  the classic-script global-publish, and `deriveScrollGeometry`'s document vs
  inner-container branches + the trivial-scroller guard threshold). The remaining
  `content_scroll.js` DOM/listener wiring and the SW message→nav fold are NOT
  exercised headlessly — verify in the load-unpacked step above.
- The end-to-end **load-unpacked in a real browser** step is a MANUAL step (MV3
  service workers are not reliably driveable headlessly). Follow "Load unpacked"
  above to complete it.

Run the JS unit tests with Node's built-in runner (pass a glob — a bare directory
positional is treated as a module on Node ≥22):
`nix-shell -p nodejs --run "node --test 'scripts/collector/browser-ext/tests/**/*.test.mjs'"`.
