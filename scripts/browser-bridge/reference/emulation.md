# `emulate` — device emulation for real mobile testing

Read this when you need to test a page as a phone/tablet, or when you hit
`not_owned_tab`, `unknown_preset:*`, `invalid_emulation:*`, or
`emulate_needs_device_or_params`.

Requires extension **0.5.0+**. On an older build the op fails with `unknown_op`
(see `errors.md` — a reload ↻ is unreliable, it needs a FULL Brave restart).

---

## The 30-second version

```bash
browser open https://example.com     # emulation is OWNED-TAB-ONLY
browser emulate iphone-15
browser screenshot                   # ← captured at 393×852 @3x, as an iPhone
browser click '#menu'                # ← dispatched as a TOUCH tap
browser emulate --reset
```

`browser emulate --list` prints the preset names without touching the browser.

---

## Why it is sticky, and the safety property that buys

CDP emulation overrides are **session-scoped**: they die the instant the debugger
detaches. The bridge **always** detaches (`withCdpSession`'s `finally` — that is a
load-bearing invariant; a leaked attachment is a stuck banner plus an open
surface). So a naive `emulate` would set a viewport that had already evaporated
before the next command ran.

Instead, `emulate` **stores** the device state per tab, and **every op that
attaches CDP re-applies it inside its own session, before doing its work.**

✅ **The property this buys, and the reason this design beat holding one long-lived
session open: between ops the tab is NOT emulated.** A crashed agent, a killed
Claude session, or an evicted MV3 service worker cannot leave the operator's real
browser distorted. The worst case is a forgotten entry in an in-memory Map that
dies with the worker — not a Brave tab stuck pretending to be an iPhone with
nothing left running that knows how to undo it.

That is also why `--reset` has nothing to undo. It means **"stop re-applying"**;
the overrides are already gone.

**Consequence to plan around:** a page that re-measures the viewport *later* — on
a `resize` listener, or well after load — sees the real window until your next op
re-applies. Drive the page with bridge ops rather than expecting the emulation to
persist while you sit idle.

---

## Blast radius: owned tabs only

Only a tab **this session opened via `browser open`** may be emulated. Anything
else is refused server-side with `not_owned_tab`, and the command never reaches
the browser.

| you did | result |
|---|---|
| `browser open` then `browser emulate iphone-15` | ✅ |
| `browser emulate iphone-15` with no `open` | ❌ `not_owned_tab` |
| `browser --tab 999 emulate iphone-15` (999 isn't yours) | ❌ `not_owned_tab` |
| `browser --tab <your own tab id> emulate …` | ✅ (same tab) |

Every *other* tab-scoped op falls back to "the active tab" when you own nothing —
that is the useful one-shot read path, and a read is harmless. `emulate` is not:
it resizes the viewport, rewrites the user agent and turns the mouse into a
finger. Applied to the tab the human is looking at, an agent would be reshaping
the operator's browser. So the fallback is removed for this op specifically.

`not_owned_tab` is deliberately **distinct** from `no_owned_tab`: the latter means
"you have nothing to act on" (run `browser open`), the former means "that tab is
not yours" (drop the `--tab`).

---

## The preset table

| preset | CSS viewport | dsf | touch | UA / UA-CH platform |
|---|---|---|---|---|
| `iphone-15` | 393 × 852 | 3 | 5 pts | iOS 17 Safari · `iOS` |
| `iphone-se` | 375 × 667 | 2 | 5 pts | iOS 17 Safari · `iOS` |
| `pixel-8` | 412 × 915 | 2.625 | 5 pts | Chrome 126 Android · `Android` |
| `ipad-mini` | 768 × 1024 | 2 | 5 pts | iPadOS 17 Safari · `iOS` |
| `galaxy-s24` | 360 × 780 | 3 | 5 pts | Chrome 126 Android · `Android` |

**Provenance, stated honestly.** These are the vendors' published *logical*
resolutions — the same figures Chrome DevTools' device list
(`front_end/models/emulation/EmulatedDevices.ts`) uses. Each preset carries a
`source` string in `extension/protocol.js` naming where its numbers came from, and
a test asserts `physical ÷ dsf ≈ the CSS viewport` so a transcription error in
either number fails CI.

⚠ They were **not verified against a live DevTools device list** — the change was
built deliberately without touching live Brave. Treat them as "the published
logical resolution", which is what matters for layout testing, not as "byte-equal
to whatever DevTools ships this month". **If a preset ever disagrees with
DevTools, DevTools wins** — fix `DEVICE_PRESETS` and say so.

### Raw overrides

For anything not in the table, or to tweak a preset:

```bash
browser emulate --width 412 --height 883 --dsf 2.75 --mobile --touch
browser emulate iphone-15 --orientation landscape      # swaps W/H, not just the angle
browser emulate pixel-8 --color-scheme dark --tz Europe/London
browser emulate iphone-15 --geo 51.5074,-0.1278        # LAT,LON[,ACCURACY]
browser emulate --width 390 --height 844 --ua 'Mozilla/5.0 (custom) Mobile'
```

Flags: `--width --height --dsf --mobile/--no-mobile --touch/--no-touch
--max-touch-points N --ua STR --orientation portrait|landscape
--color-scheme light|dark|no-preference --geo LAT,LON[,ACC] --tz ZONE --reset
--list`.

**⚠ A raw `--ua` gets GENERIC UA-Client-Hints metadata**, not a faithful
per-device one — enough that a site never sees your real desktop brands, but not a
convincing individual device. Use a preset when the UA actually matters.

---

## The user-agent half everyone misses

`emulate` always sets **both** `userAgent` **and** `userAgentMetadata`.

Modern sites read `navigator.userAgentData` (UA-Client-Hints) in preference to the
UA string. Setting only the string leaves a page seeing an iPhone UA next to the
operator's real desktop Linux Chrome brands — a combination no real client ever
produces, which trips bot detection on exactly the sites worth testing.

Apple presets carry `brands: []` **on purpose**: Safari does not implement UA-CH
and sends no `Sec-CH-UA` at all. That is correct emulation, not a missing field.

---

## What emulation changes about other ops

### `screenshot` — the fast path is disabled

`screenshot` normally takes a cheap, banner-free `chrome.tabs.captureVisibleTab`
path when the tab is foreground and you did not pass `--fullpage`. That call
**never attaches the debugger**, so on an emulated tab it would return a perfectly
valid PNG of the **un-emulated desktop layout** — a confident wrong answer,
indistinguishable from a correct one, on the single op whose whole job is showing
what the device sees.

So while a tab is emulated, `screenshot` is **forced onto the CDP path**
(`via: "cdp"`, and the result carries an `emulation` summary). You get a debugger
banner; you also get the right picture. `--fullpage` clips using the **emulated**
layout metrics, because the metrics are read after the overrides land.

### `click` — dispatches TOUCH

On a touch-emulated tab, a top-frame `click` dispatches
`Input.dispatchTouchEvent` (touchStart/touchEnd) instead of mouse events —
exactly what DevTools does. The result reports `via: "touch"`.

This is not cosmetic. Chromium synthesizes compatibility *mouse* events from
touch, but never touch events from mouse, so a mobile UI whose handler is
`touchstart` (or a library binding pointer events with `pointerType === "touch"`)
simply never fires under a mouse click — the tap "does nothing" and you report a
broken page that is not broken.

Mouse remains the behaviour on every non-touch-emulated tab, and
`--no-touch` restores it (`via: "mouse"`).

### `nav` — navigates via CDP

On an emulated tab, `nav` uses CDP `Page.navigate` **inside** the session that has
already applied the overrides, instead of `chrome.tabs.update`. A page that sniffs
the UA or measures the viewport at load time must see the emulated values *at
navigation* — otherwise a UA-sniffing site has already served you the desktop
bundle. The result reports `via: "cdp"`.

### `tabs` — surfaces emulated tabs

Emulated tabs carry an `emulation` summary in the listing, and the top-level
`emulatedTabs` array lists their ids. A stuck override is then visible rather than
mysterious.

### `wake` — composes cleanly

`wake` on an emulated tab re-applies the emulation *before* its own focus-emulation
steps, and its teardown only disables **focus** emulation. Waking does not undo the
device, and the device does not interfere with the wake.

---

## Errors

| error | meaning |
|---|---|
| `not_owned_tab` | the target tab isn't one this session `open`ed — see the blast-radius table above |
| `unknown_preset:<name>` | not in `DEVICE_PRESETS`; `browser emulate --list` |
| `emulate_needs_device_or_params` | you passed neither a preset nor any override |
| `invalid_emulation:width` / `:height` / `:dsf` / `:maxTouchPoints` | out of range (1–10000 px, 0.1–10 dsf, 1–16 touch points) |
| `invalid_emulation:width_and_height_together` | one dimension alone — CDP takes both or neither, and guessing the other from the real window would make the result depend on your window size |
| `invalid_emulation:reset_with_params` | `--reset` combined with a device description |
| `invalid_emulation:ua` | empty, >1024 chars, or containing control characters. **CR/LF is refused, never stripped** — the UA is echoed into a request header, so that is a header-injection primitive |
| `invalid_emulation:tz` / `:colorScheme` / `:orientation` / `:geo.*` | bad value; see the flag list above |
| `max_touch_points_without_touch` | `--max-touch-points` with `--no-touch` |
| `unknown_op` | the loaded extension predates 0.5.0 → `errors.md` |

A **failed apply is loud, never partial**: no emulation step is optional, because a
half-applied emulation returns a plausible screenshot of the wrong thing. If a step
fails, the op fails with a normal error envelope and the emulation state is rolled
back, so later ops do not silently retry something you were told had failed.

---

## Telemetry

`emulate` emits the usual metadata-only `activity.events` row plus the preset name
and viewport (`emu_device`, `emu_width`, `emu_height`, `emu_mobile`, `emu_touch`,
`emu_geo`, `emu_reset`).

Deliberately **excluded**: the UA string (long, operator-supplied free text; the
preset name identifies it anyway) and the geolocation **coordinates** — an emulated
lat/lon is a place the operator chose to pretend to be, and only its *presence* is
recorded. No URL, no page content, as everywhere else.

---

## Not available to the autonomous agent

`emulate` is **operator-only**: it is absent from `ALLOWED_OPS_DEFAULT` and
`OP_TO_SERVER` in `opencode/tools/browser_tool_impl.mjs`, so `browser agent`
cannot reach it even via `BROWSER_AGENT_ALLOWED_OPS`. The agent's op set is
deliberately minimal and widening it is a separate decision. Adding it would mean
mapping `emulate → emulate` in `OP_TO_SERVER` and listing it in
`ALLOWED_OPS_DEFAULT`; the ownership gate already confines it to the agent's own
tab, so the question is scope discipline, not safety.
