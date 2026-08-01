# `emulate` — device emulation for real mobile testing

Read this when you need to test a page as a phone/tablet, or when you hit
`not_owned_tab`, `unknown_preset:*`, `invalid_emulation:*`, or
`emulate_needs_device_or_params`.

Requires extension **0.5.0+**. On an older build the op fails with `unknown_op`
(see `errors.md` — a reload ↻ is unreliable, it needs a FULL Brave restart).

---

## The 30-second version

```bash
browser open                         # emulation is OWNED-TAB-ONLY (about:blank)
browser emulate iphone-15            # ← emulate FIRST …
browser nav https://example.com      # ← … then LOAD. This order is load-bearing.
browser screenshot                   # ← captured at 393×852 @3x, as an iPhone
browser click '#menu'                # ← dispatched as a TOUCH tap
browser emulate --reset
```

`browser emulate --list` prints the preset names without touching the browser.

🔴 **Two traps, both of which produce a confident wrong answer. Read both before
you trust anything you read off an emulated tab:**

1. **Order:** `emulate` **then** `nav`. Emulating an already-loaded page does not
   give it the touch **API** — see "🔴 Emulate BEFORE you load" immediately below.
2. **Read path:** `text`/`html`/`js` return the REAL desktop DOM; `--wake` is what
   reads the emulated one — see "`text` / `html` / `js` do NOT read the emulated
   page".

---

## 🔴 Emulate BEFORE you load — a live override cannot add the touch API

**Applying `emulate` to a tab that has already committed a document leaves the
page without `ontouchstart` and without `TouchEvent`, while every other mobile
signal reads correctly.** A feature-detecting site therefore concludes it is on a
non-touch device, and an agent reading that concludes the site has no touch
support. Wrong, confidently, and in exactly the shape this feature exists to
prevent.

The cause is where each signal lives. `window.ontouchstart` and the `TouchEvent`
constructor are installed on the global **at document creation** — CDP's
`Emulation.setTouchEmulationEnabled` sets the flag the *next* document is built
with, and cannot retroactively install properties on a global that already exists.
Viewport metrics, media features and the UA/UA-CH values are queried **live** by
the page every time it asks, so overriding them takes effect immediately.

**Measured** (2026-07-31, live Brave, extension 0.5.0, `https://example.com` in an
owned tab, `iphone-15`):

| signal | `emulate` on an ALREADY-LOADED page | after `nav` UNDER emulation |
|---|---|---|
| `innerWidth` × `innerHeight` | ✅ `393` × `852` | ✅ `393` × `852` |
| `devicePixelRatio` | ✅ `3` | ✅ `3` |
| `navigator.maxTouchPoints` | ✅ `5` | ✅ `5` |
| `matchMedia("(pointer:coarse)")` | ✅ `true` | ✅ `true` |
| `matchMedia("(hover:none)")` | ✅ `true` | ✅ `true` |
| `navigator.userAgent` + `userAgentData` | ✅ iPhone / `{mobile:true, platform:"iOS"}` | ✅ same |
| `"ontouchstart" in window` | ❌ **`false`** | ✅ `true` |
| `typeof TouchEvent` | ❌ **`undefined`** | ✅ `"function"` |

So: **anything queried live applies at once; anything installed on the global at
document creation needs a document load.** Today that second set is the touch API
surface. Treat the list as the measured cases, not as proven exhaustive — any
other create-time global would behave the same way.

### The rule

```bash
browser open                      # about:blank — nothing committed yet
browser emulate iphone-15
browser nav <url>                 # the document is BUILT under emulation
# ... now read / click / screenshot
```

If the tab is already on the page you want, **re-`nav` to it** after `emulate`
(`browser nav <same-url>`) before reading or interacting. `nav` on an emulated tab
goes through CDP inside the session that has already applied the overrides, so the
new document is created with touch enabled and with the mobile UA visible to any
load-time sniffing.

⚠ The natural-looking order — `open <url>` → `emulate` → interact — is the WRONG
one, which is why the 30-second recipe above opens `about:blank`. `browser click`
still dispatches touch *events* on such a tab (that is driven by the stored
emulation state, not by the page's API surface), so a tap can work while the
page's own `'ontouchstart' in window` feature-detect has already sent it down the
desktop branch.

**There is no envelope warning for this yet** — the `emulated`/`notEmulatedRead`
annotation covers the read-path trap, not this one. See "Envelope hint (not
implemented)" at the end of this page.

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
| `ipad-mini` | 744 × 1133 | 2 | 5 pts | iPadOS 17 Safari · `iOS` |
| `ipad-mini-2019` | 768 × 1024 | 2 | 5 pts | iPadOS 17 Safari · `iOS` |
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

**⚠ A raw `--ua` gets MINIMAL UA-Client-Hints metadata**, not a faithful
per-device one. `platform`/`model` are **derived from the UA string** (an iPhone UA
gets `platform: "iOS"`, never `"Android"`), and `brands` is left **empty** — there
is no basis to claim a Chromium version from an arbitrary string, and a wrong brand
list is a stronger bot-detection signal than none. Where the UA names no platform
we recognise, `platform` stays `""` rather than being guessed. Use a **preset** when
the UA actually matters.

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

## 🔴 `text` / `html` / `js` do NOT read the emulated page

**The single most important thing on this page.** Read it before you trust a read.

`text`, `html` and the default `js`/`eval` go through `chrome.scripting`, which
**never attaches the debugger** — so the emulation is never applied and they return
the tab's **real, un-emulated DOM**. `innerWidth`, `getBoundingClientRect`,
`matchMedia` and `navigator.userAgent` all come back **desktop**.

That is correct-by-design, not a bug: between ops the tab genuinely is not emulated
(see the safety property above), so the at-rest DOM really *is* desktop. But it is
the trap this feature is most likely to spring — screenshot a phone layout, then
read `text` and reason about a desktop DOM.

**Which reads are emulated:**

| read | path | emulated? |
|---|---|---|
| `text` / `html` / `js` | `chrome.scripting` | ❌ **no** |
| `text --frame` / `html --frame` | `chrome.scripting` | ❌ **no** |
| `text --wake` / `html --wake` / `js --wake` | CDP (`cdpWake` → `withCdp`) | ✅ yes |
| `js --frame` | CDP (`cdpFrameEval` → `withCdp`) | ✅ yes |
| `screenshot`, `click`, `nav`, `wake`, `upload` | CDP | ✅ yes |

**The envelope tells you which one you got.** On a tab that has emulation state,
every read carries either:

```jsonc
{ "emulated": false, "notEmulatedRead": true,
  "emulationNote": "…read the tab's REAL, un-emulated DOM … Re-run with --wake …" }
// or
{ "emulated": true, "emulation": { "preset": "iphone-15", "width": 393, … } }
```

A tab with **no** emulation state gets neither field, so ordinary envelopes are
unchanged.

**So to measure the emulated viewport, use `--wake`:**

```bash
browser js --wake 'innerWidth+"x"+innerHeight'    # → 393x852
browser js 'innerWidth+"x"+innerHeight'           # → the REAL window size
```

Same command, two answers, depending on the flag. That is why the annotation
exists.

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
half-applied emulation returns a plausible screenshot of the wrong thing. A failing
step fails the op with a normal error envelope, and the debugger is still released.

What happens to the stored state depends on **which** apply failed, and the
distinction matters:

* **The initial `emulate` apply** — the state is **rolled back** (to the previous
  emulation, or to none). You were told it failed, so nothing later re-attempts it.
* **A sticky re-apply inside a later op** (`screenshot`, `click`, …) — the state is
  **retained** and the *next* op will try again. That is deliberate: a transient
  failure (a renderer briefly busy, a navigation in flight) should not silently
  un-emulate a tab you asked to be emulated. The cost is that a *persistently*
  failing override fails every subsequent op with the same error until you
  `--reset`, rather than degrading to un-emulated — which is the safer direction,
  since degrading silently is how you get a desktop screenshot labelled as a phone.

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

## Envelope hint (NOT implemented — deliberate, and why)

The lesson from the read-path trap is that **the envelope is the protection that
works regardless of which docs were read**: `notEmulatedRead` + `emulationNote`
warn a model that never opened this file. The document-order trap deserves the
same treatment — `emulate` returning something like
`documentPredatesEmulation: true` when the target tab already has a committed
document (anything other than a fresh `about:blank`), with a note saying "re-`nav`
before reading; the touch API is not installed on this document".

It is **not implemented here on purpose.** The check has to run where the tab's
state is known — `handleEmulate` in `extension/protocol.js` — so it is an
extension code change, and the deployed extension is loaded from
`~/.local/share/browser-bridge-ext/`: shipping it means a manifest bump and a FULL
Brave restart on **both** profiles, which had just been done for 0.5.0. Doing it
inside a docs-and-CLI fix would also have meant claiming a behaviour change that
cannot be live-verified without driving the browser.

**Follow-up, when the next extension bump happens anyway:** add the flag in
`handleEmulate`, surface it in the CLI as a stderr warning next to the existing
`_hidden_warn`, and cover it in `tests/emulation.test.mjs`. Until then this page
and the `README` pointer are the only warning, which is precisely the weaker form
of protection that motivates doing it.

---

## Not available to the autonomous agent

`emulate` is **operator-only**: it is absent from `ALLOWED_OPS_DEFAULT` and
`OP_TO_SERVER` in `opencode/tools/browser_tool_impl.mjs`, so `browser agent`
cannot reach it even via `BROWSER_AGENT_ALLOWED_OPS`. The agent's op set is
deliberately minimal and widening it is a separate decision. Adding it would mean
mapping `emulate → emulate` in `OP_TO_SERVER` and listing it in
`ALLOWED_OPS_DEFAULT`; the ownership gate already confines it to the agent's own
tab, so the question is scope discipline, not safety.
