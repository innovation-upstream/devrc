# `emulate` — device emulation for real mobile testing

Read this when you need to test a page as a phone/tablet, or when you hit
`not_owned_tab`, `unknown_preset:*`, `invalid_emulation:*`, or
`emulate_needs_device_or_params`.

Requires extension **0.5.0+**. On an older build the op fails with `unknown_op`
(see `errors.md` — a reload ↻ is unreliable, it needs a FULL Brave restart).
The `documentPredatesEmulation` envelope hint needs **0.6.0+**; on 0.5.0 `emulate`
simply never carries it (the trap is still there, unannounced).

---

## The 30-second version

```bash
browser open https://example.com     # emulation is OWNED-TAB-ONLY
browser emulate iphone-15            # ← replies documentPredatesEmulation: true …
browser nav https://example.com      # ← … so RE-NAV: the document is rebuilt emulated
browser screenshot                   # ← captured at 393×852 @3x, as an iPhone
browser click '#menu'                # ← dispatched as a TOUCH tap
browser emulate --reset              # ← undoes it, viewport included (0.8.1, #319)
                                     #   --reset --recreate replaces the tab
                                     #   instead; THE TAB ID CHANGES.
```

`browser emulate --list` prints the preset names without touching the browser.

⚠ **`browser open` with no URL will NOT work as the first step**, despite what
this recipe said before 0.6.0: the tab it creates sits at `about:blank`, and
`chrome.debugger` may only attach to `http:`/`https:`
(`CDP_ATTACHABLE_SCHEMES`), so `emulate` on it is refused with
`cdp_attach_refused:about:`. Open the URL, emulate, then **re-`nav` to the same
URL** — which is exactly what the hint tells you to do. (Established from the
attach predicate and pinned by a unit test; not re-measured live.)

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
browser open <url>                # emulate needs an http/https tab to attach to
browser emulate iphone-15         # → documentPredatesEmulation: true
browser nav <url>                 # the document is RE-BUILT under emulation
# ... now read / click / screenshot
```

If the tab is already on the page you want, **re-`nav` to it** after `emulate`
(`browser nav <same-url>`) before reading or interacting. `nav` on an emulated tab
goes through CDP inside the session that has already applied the overrides, so the
new document is created with touch enabled and with the mobile UA visible to any
load-time sniffing.

⚠ The natural-looking order — `open <url>` → `emulate` → interact — is the WRONG
one; the missing step is the re-`nav`. `browser click`
still dispatches touch *events* on such a tab (that is driven by the stored
emulation state, not by the page's API surface), so a tap can work while the
page's own `'ontouchstart' in window` feature-detect has already sent it down the
desktop branch.

**Since 0.6.0 the envelope warns you** — `emulate` returns
`documentPredatesEmulation: true` + an `emulationNote` when the tab already holds a
document that was not built under this emulation. See "The
`documentPredatesEmulation` hint" below for exactly when it fires and clears.

---

## Why it is sticky — and how `--reset` undoes it (#319)

CDP emulation overrides are **session-scoped**: they die the instant the debugger
detaches. The bridge **always** detaches (`withCdpSession`'s `finally` — that is a
load-bearing invariant; a leaked attachment is a stuck banner plus an open
surface). So a naive `emulate` would set a viewport that had already evaporated
before the next command ran.

Instead, `emulate` **stores** the device state per tab, and **every op that
attaches CDP re-applies it inside its own session, before doing its work.**

🔴 **This page used to claim a safety property here that does not exist** — that
because the overrides die at detach, "between ops the tab is NOT emulated", so a
crashed agent could never leave the operator's browser distorted. **Measured false**
(2026-08-03, laptop, extension 0.7.2, fresh-tab control):

| step | `innerWidth` |
| --- | --- |
| fresh tab, never emulated (**control** — proves the read path works) | **1124** |
| after `emulate iphone-15` | **393** |
| after `emulate --reset` | **393** ← not restored |
| after `--reset` **and** a re-`nav` | **393** ← still not restored |

The build under test genuinely sent and reported a clear
(`cleared: [Emulation.clearDeviceMetricsOverride,
Emulation.setTouchEmulationEnabled, Emulation.setUserAgentOverride]`) and the size
survived it. That measurement is correct, and it is why PR #320 was closed. What
it did **not** establish is *why* — and the reason turns out to be the fix.

Everything else *does* revert on its own: `devicePixelRatio`, `maxTouchPoints`,
`pointer: coarse`, `prefers-color-scheme`, `userAgent`, `timeZone` — **because a
CDP override dies with the debugger session that set it**, not because a clear was
sent. **Only the viewport size is sticky.** (`'ontouchstart' in window` also stays
true on a document that was *built* emulated — that one is document-creation
residue, and a re-`nav` clears it.)

### The mechanism (established 2026-08-04)

Measured against a **throwaway Brave 147.0.7727.56 under Xvfb**, driven over the
raw DevTools websocket with no extension in the loop, with a never-emulated control
tab read every round:

| what was done | victim `innerWidth` | control |
| --- | --- | --- |
| `setDeviceMetricsOverride{393×852}` in session A, then **detach** | **394** | 1055 |
| `clearDeviceMetricsOverride` in a **fresh** session B, then detach | **394** ← no-op | 1055 |
| `setDeviceMetricsOverride{…}` **then** `clearDeviceMetricsOverride` in session B | **1055** ← restored | 1055 |

🔴 **`Emulation.clearDeviceMetricsOverride` does nothing when the session sending
it never set an override itself.** It returns success either way. Every previous
attempt to undo this sent the clear from a fresh session — so it was a no-op, and
the acknowledgement made it look like it had worked.

**That also explains the dpr-vs-width asymmetry**, which was the open question in
#319. The two values live in different places even though one call sets both:
`devicePixelRatio`, touch, UA, media and timezone are **renderer-side session
state** that dies at detach; the viewport size additionally resizes the
**browser-side render widget**, and that resize is undone *only* by an explicit
clear from a session that has emulation armed. Nothing does that at detach — so
the size, and only the size, survives. (Inference from the behaviour above, not
from reading Chromium's source. The table is the finding.)

The arming params do not matter — `{393×852}`, `{800×600}`, `{1×1}` and
`{0,0,0,false}` all made the following clear restore the true width. The shipped
pair uses `{width:0,height:0,deviceScaleFactor:0,mobile:false}` because it was
measured **not to resize anything on its own** (still 394 in-session after arming),
so there is no intermediate flash to a wrong size.

### What `--reset` does now (0.8.1)

`emulate --reset` drops the stored state **and then** attaches one CDP session and
sends `EMULATION_RESET_CDP_STEPS` — the arming override, then the clear. It
reports them:

```json
{ "reset": true, "wasEmulating": {…}, "restored": true,
  "cleared": ["Emulation.setDeviceMetricsOverride",
              "Emulation.clearDeviceMetricsOverride"] }
```

Three properties worth knowing:

* **Both steps ride one session.** Splitting them reintroduces the bug exactly.
* **It is unconditional** — it fires even when this service worker holds no state
  for the tab. That is the case that matters: an evicted MV3 worker forgets the
  state while the tab stays physically stuck. Measured safe on a never-emulated
  tab (2/2 runs left it at its true width).
* **It is best-effort.** A closed/discarded tab or a privileged scheme makes the
  attach fail; you get `restored: false` + `restoreError` and no exception, because
  "stop emulating" did succeed.

`--reset --recreate` **stays**, and is still the right tool twice over: it is the
remedy that needs no CDP at all, and it is the **only** one for a tab the extension
can no longer reach — an un-upgraded build, or a tab orphaned by a `SIGKILL`'d
agent that never ran its reset.

```
browser emulate --reset --recreate
```

→ resets, opens a **fresh tab at the same url** owned by the same session, closes
the stuck one, and reports the **new tab id** (it changes — later ops route to it).
It refuses on a non-http(s) url, and always opens the replacement *before* closing
the original, so no failure path leaves you with no tab. Plain `--reset` never
swaps tab ids.

⚠ **Not verified against the operator's live Brave.** Everything above was
measured in a throwaway instance and in unit tests against a browser model
calibrated to those measurements. See the PR for the exact live-verification
commands.

**Consequence to plan around:** a page that re-measures the viewport *later* — on
a `resize` listener, or well after load — sees whatever the tab's physical size now
is, not necessarily the emulation you asked for. Drive the page with bridge ops
rather than expecting a stable state while you sit idle.

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
not yours".

⚠ **If you passed `--tab <id>` you captured from `browser open`, suspect the
SESSION ID before you suspect the flag.** Until 2026-08-01 the fallback id was
keyed on `$PPID`, so `T=$(browser open … | …)` registered ownership under a
*different* id than the following `browser --tab "$T" emulate` presented — and
the old message ("drop `--tab`") pointed straight at the wrong thing. The CLI now
prints the session id the failing call presented; run `browser --print-session-id`
both inside the same `$( … )` you used for the `open` and directly, and compare.
Full account: `~/workspace/devrc/scripts/browser-bridge/reference/tabs-instances.md`
→ "The subshell hazard".

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
[--recreate] --list`.

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
**never attaches the debugger** — so the emulation is never applied. What you get
back is a **MIXTURE**, which is worse than either pure case:

| on a `text`/`html`/`js` read of an emulated tab | value |
| --- | --- |
| `navigator.userAgent`, `devicePixelRatio`, `maxTouchPoints`, `matchMedia('(pointer: coarse)')`, timezone | **real / desktop** — the overrides died at detach |
| `innerWidth`, every `getBoundingClientRect` | **emulated** — the widget is physically that size (see "Why it is sticky" above) |

So the page you read is laid out at the phone **width** while every capability
signal says desktop — a document no real device would produce. This is the trap the
feature is most likely to spring: screenshot a phone layout, then read `text` and
reason about that hybrid. Use `--wake`.

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

## The `documentPredatesEmulation` hint (0.6.0+)

Docs are the weaker protection: the envelope reaches a model that read none of
this. So `emulate` self-announces the create-time trap the same way a read
self-announces the read-path trap — **one idiom, a boolean plus `emulationNote`**:

```jsonc
{ "tabId": 42, "url": "https://example.com",
  "emulation": { "preset": "iphone-15", "width": 393, … },
  "applied": ["Emulation.setDeviceMetricsOverride", …],
  "note": "sticky per tab: …",
  // ↓ only when the trap applies
  "documentPredatesEmulation": true,
  "emulationNote": "This tab already had a document loaded BEFORE these overrides
                    were applied … `\"ontouchstart\" in window` stays false and
                    `typeof TouchEvent` stays \"undefined\" … REMEDY: `browser nav
                    <url>` now that emulation is on …" }
```

**When it fires.** Both must hold:

1. the tab's URL is a **committed document** — anything other than `about:blank`
   (with or without a fragment/query), `about:newtab`, `chrome://newtab/`,
   `brave://newtab/`; and
2. that document was **not created by this bridge under an emulation with the same
   create-time signature**. The signature is the part of the state a document can
   only pick up by being *built* under it: touch on/off + `maxTouchPoints`,
   `mobile`, and whether a UA override is in force. It is deliberately wider than
   the two measured properties — a spurious hint costs one re-`nav`, a missed one
   costs a wrong conclusion about the site.

| sequence | hint on the last `emulate`? |
|---|---|
| `open <url>` → `emulate` | ✅ fires — the page loaded un-emulated |
| `nav <url>` (un-emulated) → `emulate` | ✅ fires |
| `emulate` → `nav <url>` → `emulate` (same device) | — silent, the document was built emulated |
| `emulate` → `nav` → `emulate --touch=false` | ✅ fires — different create-time state |
| `emulate` → `nav` → `emulate --reset` → `emulate` (same device) | — silent; `--reset` does not un-build the document |
| `emulate --reset` (any time) | — never; a reset applies nothing |
| tab with no emulation at all | — the envelope is unchanged, no new field |

**When the record clears.** The "what was this document built under" record is
per-tab and dies with the tab: `close`, `chrome.tabs.onRemoved`,
`chrome.tabs.onReplaced` (prerender swap), and a tab that vanished out-of-band
(`owned_tab_gone`). It deliberately **survives `emulate --reset`** — a reset stops
re-applying overrides, but the document it already built still has touch installed,
and forgetting that would make the next identical `emulate` cry wolf.

⚠ **Honest limitation: only the bridge's own `nav` updates the record.** Any other
navigation is unobserved, so the record can go stale and the hint stay silent on a
document that really does predate the overrides. Concretely, all of these:

* **your own `browser click` / `browser key`** that follows a link or submits a
  form — the new document commits *after* that op's CDP session detaches, so it is
  created **un-emulated**, while the record still says "built emulated". This is the
  one an agent is most likely to hit and least likely to recognise;
* a navigation the operator performs by hand in the tab;
* a page-initiated one (meta-refresh, a JS `location` assignment, a redirect chain
  that lands somewhere else);
* a `nav` whose `Page.navigate` **resolves with an `errorText`** (DNS failure, etc.)
  rather than throwing: the record is still written, though what committed is a
  Chrome error page. `chrome.tabs.onUpdated` was deliberately not
used: it cannot distinguish the bridge's own CDP navigation from an out-of-band one
without a second piece of mutable state to get wrong. **If in doubt, re-`nav`** —
it is idempotent and cheap.

⚠ **What is and is not verified:** the fire/clear behaviour above is covered by
unit tests against a **mocked** `chrome.debugger` (`tests/emulation.test.mjs`), and
every one of those guards was mutation-tested. The *underlying* fact the note
asserts — that `ontouchstart`/`TouchEvent` are missing before a re-`nav` and
present after — is the live measurement in the table at the top of this page, taken
once, on `example.com`, with `iphone-15`. Exactly two properties were measured;
neither this page nor the note claims that list is exhaustive.

---

## Available to the autonomous agent, default-on (#316)

`emulate` is mapped in `OP_TO_SERVER` **and** listed in `ALLOWED_OPS_DEFAULT` in
`opencode/tools/browser_tool_impl.mjs`, so the `browser agent` model can call it
with no opt-in. The typed fields it may pass are `device`, `width`, `height`,
`deviceScaleFactor`, `mobile`, `maxTouchPoints`, `orientation`, `colorScheme` and
`reset`.

**`geo`, `touch`, `userAgent` and `timezone` are deliberately NOT offered to the
agent** — the operator CLI keeps all four. `geo` spoofs the operator's location,
which is unrelated to the viewport question and is a fingerprinting surface handed
to a model reading untrusted pages. `userAgent` and `timezone` are the same class:
both are identity the page can read, both are attacker-*reachable* (the model
picks them after reading a page it does not control), and emulation is **sticky
for the whole run**, so a prompt-injected UA rides along on every later `nav` —
including to an authenticated site. A **device preset still sets a matching UA
server-side**, so the legitimate mobile-testing path is untouched; what is removed
is the model choosing an arbitrary one. `touch` is merely redundant (a preset
carries its own touch support, and a raw `maxTouchPoints` turns touch on by
itself). `userAgent`/`timezone` are refused client-side BY NAME
(`emulation_field_operator_only:<field>`) before anything is sent, because they
were declared args until now and a model could still try one; `geo`/`touch` were
never declared and stay on the whitelist's silent-drop path.

Bounds are mirrored from `EMULATION_LIMITS` and enforced client-side before the
request is sent, with the same `invalid_emulation:<field>` vocabulary.

It was excluded until #316 on the stated grounds that it "leaves sticky per-tab
state that outlives the op". **That observation was correct; the conclusion drawn
from it was not** — and the counter-argument originally offered here (that the
overrides are session-scoped, `withCdpSession` always detaches, so a crashed agent
could not leave the operator's browser distorted) is **measured false for the
viewport**: the emulated size survives the detach and a re-navigation (see the
table above). The non-viewport overrides *do* die at detach as described. What
actually bounds the blast radius is not detach semantics: the ownership gate
(`OWNED_TAB_ONLY_OPS`) confines the op to the tab the agent's own run opened, and
the wrapper closes that tab on every exit path — and **closing is what un-sticks
the viewport**. The residual is a SIGKILL that bypasses the trap, which orphans
the run's own tab stuck at the emulated size.
