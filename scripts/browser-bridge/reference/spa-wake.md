# Throttled / backgrounded tabs — the `wake` pattern

**Load this when:** a `text`/`html` read came back empty, half-built or shell-only ·
a read reported `data.hidden:true` or printed a hidden-tab warning on stderr · an SPA
looks stuck "Loading…" · `frames` lists no OOPIF you know is there · in-frame
`click`/`type` hits elements that "don't exist yet" · you are about to conclude a site
is BROKEN from a browser read · before driving any heavy JS app in an `open`ed tab ·
you are measuring anything the page gates on VISIBILITY (timers, refresh loops,
readiness) · an injected `window.__x` hook vanished between reads.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## The trap

A heavy SPA opened in a **backgrounded** tab is throttled by Chrome —
`document.visibilityState:"hidden"`, so it gets **no animation frames at all** and
~1 Hz timers, and it often **never finishes rendering**. You then can't read or
drive it: `text`/`frames` come back empty or half-built, and in-frame
`click`/`type` hit elements that don't exist yet. Verified case:
`model-benchmarking.example.test` (an OOPIF inside a `civitai.com` tab) stayed blank
while backgrounded.

`browser open` creates its tab in the **background** (`active:false`) on purpose, so
every `open`ed heavy app starts throttled.

🔴 **Before you believe any "nothing rendered / no requests fired" reading:**

- **Check `document.visibilityState` FIRST**
  (`browser --tab <id> js 'document.visibilityState'`). If `"hidden"`, that
  reading is MEANINGLESS — a shell-only DOM is indistinguishable from a genuinely
  broken frontend. (Reads self-announce it: `data.hidden:true` + a stderr warning.)
- **Spoofing it afterwards does NOT recover the page.**
  `Object.defineProperty(document,'visibilityState',…)` changes nothing: the
  throttling is browser-enforced and the app's fetch decisions are already made.
  **`wake` is the fix** — not a spoof, and not `activate`.
- 🔴 **"Is this page broken for REAL users?" is not a browser question.** Answer it
  from server-side/real-user evidence — RUM, metrics, pod health, an anonymous
  `curl`. Use the browser probe to EXPLAIN a failure telemetry already shows, never
  to DISCOVER one. An agent once escalated a hidden-tab read to a site-wide
  production outage that was not happening; every "corroborating" check shared the
  identical flaw, and one of the errors it cited was caused by the probe itself.
  **Post-mortem: `~/workspace/devrc/scripts/browser-bridge/README.md` § *Real false-outage report*.**

## Confirm throttling before you diagnose anything

Two checks that stop a throttled tab from reading as a broken product:

- **`readyState` alone is NOT sufficient.** A throttled SPA reaches
  `document.readyState === "complete"` on an empty shell and stays there. Poll
  `readyState` **and a real content selector** (an actual node the page must render),
  and allow **materially longer** than a foreground load before concluding anything.
  Then `wake` and re-read — `wake`, never `activate`.
- **Cross-check a suspected outage with a separate `curl` for the HTTP status.**
  A **200 from `curl` plus an empty DOM in a background tab is THROTTLING, not a
  server fault.** That one command separates the two explanations that otherwise
  look identical from inside the browser.

## `wake` — the fix, and it does NOT take the operator's screen

It attaches CDP to that tab ONLY, turns on `Emulation.setFocusEmulationEnabled`
(+ a best-effort `Page.setWebLifecycleState` thaw) (measured:
`visibilityState` → `visible`, rAF 0/s → 62/s), holds it for a bounded settle
(~1.5s default, cap **6s** via `--wait MS`) so the page paints, then explicitly
disables focus emulation and detaches (the revert is never left to detach). Returns
`{woke,visibilityState,readyState,applied,settleMs,…}`; `woke` is probed from an
ISOLATED world, so a page cannot fake it. **Nothing in this path moves the operator's
screen** — no `i3-msg`, no tab/window focus change.

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work open https://civitai.com/apps/run/model-benchmarking  # backgrounded
$BB --instance work wake                # → woke:true, the app renders
$BB --instance work frames              # now the OOPIF is listed
$BB --instance work --frame model-benchmarking text    # read inside it
$BB --instance work --frame model-benchmarking click 'button:has-text("Grid")'  # drive it
```

**Wake once per PAGE, not per read.** The un-throttled *state* ends when the CDP
session detaches (measured), but the **DOM the page rendered during the wake
window persists** — so a following ordinary `text`/`html` sees the rendered page
on the cheap, banner-free path. Re-wake after a navigation or when the app needs
to do more rendering work.

### 🔴 `wake` is NOT a passive read — one wake fires `visibilitychange` TWICE

`wake` turns on `Emulation.setFocusEmulationEnabled`, which genuinely flips the
document `hidden` → `visible` (that flip is the measured Chromium behaviour the
op is built on — see the rAF/timer/visibility table in `extension/protocol.js`).
It then **tears the emulation down** in a `finally`
(`Emulation.setFocusEmulationEnabled{enabled:false}`, `WAKE_CDP_TEARDOWN`), which
flips the document back `visible` → `hidden`.

**So a single `wake` delivers TWO `visibilitychange` events to the page, not one.**

✅ MEASURED 2026-08-01 — laptop (`192.168.50.155`), Brave profile `personal`,
extension **0.7.0** (`extension_id bgbkamdlkdleahpgdgmjipjbgmepgenk`), a
background `example.com` tab with a counting listener installed via `js`:

| step | `window.__vc` | `document.visibilityState` |
|---|---|---|
| listener installed | 0 | `hidden` |
| harness control — synthetic `dispatchEvent` | 1 | `hidden` |
| reset, then **one `wake`** | **2** | `hidden` (after detach) |
| a **second `wake`** | **4** | `hidden` |
| reset, then `js --wake` — read *inside* the window | **1** | `visible` |
| …the same tab read again *after* teardown | **2** | `hidden` |

The `js --wake` rows are the clearest statement of the mechanism: **one** event
has fired by the time your in-window read runs, and the **second** lands when the
session detaches. (The counter was validated against a synthetic event first —
a listener that cannot count would have reported a false 0.)

Two consequences, in opposite directions:

- ✅ **It is the NON-INTRUSIVE way to exercise visibility-gated behaviour.** Any
  page whose logic is gated on being visible — deferred timers, refresh/keep-alive
  loops, readiness handshakes that never complete while hidden — can be observed
  *and driven* without `activate` and without stealing the operator's screen. A
  woken background tab reaches the same state a foreground one would.
- 🔴 **So every `wake` can RUN REAL PAGE LOGIC. Never treat one as a free look.**
  Observed on a token-refresh page host: each `--wake` observation fired the page's
  `visibilitychange` handler, which issued a **fresh credential mint** — so the act
  of measuring reset the very clock being measured, and the request log contained
  more entries than the page's own retry budget allows. If you are waiting for a
  deadline, a keep-alive wake **past that deadline pushes the deadline out**.

🔴 **If you are counting page-initiated requests/timers/retries, subtract `2 ×
your wake count`, not one per wake** — for a handler bound to `visibilitychange`
generally. A handler that early-returns on `document.hidden` (the common shape)
does its work only on the `visible` edge and so runs **once** per wake; one that
acts on both edges runs **twice**. Read the page's handler before choosing the
multiplier — do not assume.

Practical rule: decide *before* waking whether the thing you are measuring is
visibility-sensitive. If it is, plan the wake schedule (including keep-alive
wakes) as part of the experiment, not as incidental instrumentation.

This is the page-facing half of a point `reference/agent.md` already makes about
the operator-facing half — **"a read is passive, but a wake attaches the debugger
to the operator's live tab"**, which is why `wake` counts as a MUTATING op for the
autonomous agent's dry-run policy. Same conclusion, two blast radii: keep them in
sync.

### 🔴 A background tab can be DISCARDED — injected page state does not survive

Chromium may **discard** a backgrounded tab under memory pressure and **reload it
on activation**. The tab id and the URL are unchanged across the whole cycle.

**A tab that is discarded RIGHT NOW is announced loudly — do not disbelieve it.**
Any CDP op fails fast, *before* `chrome.debugger.attach`, with
`tab_discarded: the target tab was unloaded by Chrome (memory saver) and has no
live renderer to attach to …` (`assertTabCdpReady` in `extension/protocol.js`),
and the load-settle poll treats `discarded` as terminal rather than waiting for a
renderer that will never answer. That error is real and actionable: reload the
tab or bring it to the foreground, then retry.

**What is silent is the RELOAD afterwards.** Once the tab comes back, `discarded`
is false again, the tab id and URL still match, nothing tracks document identity,
and `browser tabs` reports no `discarded` field at all — so a later read looks
completely ordinary. But the document is NEW, and everything you injected into the
old one is gone: a `window.__x` instrumentation hook, a patched `window.fetch`,
accumulated in-page logs. Same failure mode as the RE-THROTTLE-after-reload
section below — a reload makes a new document, and a new document has none of your
state.

**Re-install after any `activate`, any `tab_discarded` you recovered from, or any
suspected discard, and verify the hook is still there before trusting a reading:**

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work --tab "$TAB" js 'typeof window.__mintLog'   # "undefined" ⇒ reinstall
```

A hook that reads `undefined` is the tell. An empty *array* means "installed, no
events" — which is a completely different answer, and the one you must not
confuse with a discarded tab. When the log itself is the evidence, prefer a
storage that survives a bridge drop and re-read it defensively rather than
assuming continuity across a long-running observation.

⚠ **Open question (#273):** whether the emulation bookkeeping survives this cycle.
`documentEmulation` is cleared only on tab *identity* change, and a discard-reload
keeps the tab id — so on an **emulated** tab the `documentPredatesEmulation` hint
may go silently wrong after a discard. Unsettled: it needs a forced discard via
`brave://discards`. Until #273 closes, re-apply `emulate` after a suspected
discard rather than trusting the absence of a warning.

### ⚠ After a RELOAD the tab is RE-THROTTLED — and htmx then silently stops firing

"Once per page" means once per **document**, and a reload makes a new one. Any
navigation or `HX-Refresh`-driven reload puts a background tab back into the
throttled state, so subsequent clicks appear to do nothing — **no error, no
console output, the control just looks broken**. That is the whole tell: a
control that is *silently* inert after a reload is far more likely to be
throttled than broken.

Two agents lost real time to this in one session; one spent three clicks
debugging a control that was fine. **Re-run `wake` after any reload**, not only
after the initial `open`. When a click itself triggers the reload, the wake has
to come after the click, not before it.

## `--wake` — when the read itself must observe live un-throttled state

Use it when you are measuring rAF, or the app hydrates lazily and re-empties while
hidden: put the un-throttle and the read in ONE CDP session.

```bash
$BB --instance work text --wake         # un-throttle + read, same CDP session
$BB --instance work html --wake=3000    # longer settle
$BB --instance work js '…' --wake
```

`--wake` is deliberately opt-in: an ordinary `text`/`html`/`eval` takes the light
`chrome.scripting` path with **no debugger banner**, and routing every read
through CDP would flash "an extension is debugging this browser" on every single
read. `--wake` cannot be combined with `--frame` (refused with
`wake_with_frame_unsupported`) — run `browser wake`, then the frame read.

**World nuance:** `text`/`html --wake` read from an **ISOLATED world** (DOM-capable,
no page globals); `js`/`eval --wake` is **MAIN world** by definition — that is what
`eval` means.

### 🔴 A `js` MEASUREMENT is the unprotected case — assert the page RENDERED

The `browser agent`'s deterministic auto-wake covers **`text` and `html` ONLY**
(`AUTO_WAKE_OPS = ["text","html"]` in `opencode/tools/browser_tool_impl.mjs`); `eval`/`js`
is deliberately **not** in that set. So a `js`-based *measurement* — layout, geometry,
overflow, element counts — can be taken on a **throttled, hidden, never-rendered** tab and
comes back as plausible numbers with no error. Measured 2026-08-02: a bare
`js` on a freshly-`open`ed tab returns `visibilityState:"hidden"`. It cost a
"no horizontal overflow, no wide elements" verdict taken on a page whose SPA content had
never loaded — every number was real and about an empty document.

🔴 **Rule: end every `js` measurement with a render assertion in the SAME expression** — a
content count that MUST be non-zero (`document.querySelectorAll('<the card selector>').length`).
A zero there invalidates the whole reading; without it, empty and correct look identical.
(`eval` *is* in `HIDDEN_SIGNAL_OPS`, so the agent still sees the hidden note — but nothing
un-throttles the tab for it.)

🔴 **`visibilityState` is NOT a check for "did wake work"** — the flip is scoped to the
wake window (`WAKE_CDP_TEARDOWN` reverts focus emulation before detaching), so a
*separate* later `js` read correctly reads `"hidden"` again. The measured table under
"*one wake fires `visibilitychange` TWICE*" above is the authority. Judge a wake by
`woke` + whether the content is actually there, never by a later `visibilityState`.

⚠ **`--wake` on `open` can fail with `cdp_attach_refused:about:`** — the tab was still
`about:blank` because navigation had not completed, and `about:` is not an attachable
scheme. (`<no-scheme>` is the *different* case where the url is absent/uncommitted so
`new URL()` throws — see `cdpSchemeOf` in `extension/protocol.js` and
`reference/errors.md`.) Not a bridge fault: `nav` first (or `nav --wake`), or re-issue
the wake once the URL is real. See `reference/frames-cdp.md`.

## When you actually need `activate` (rare)

`activate` is still the honest answer when something needs the REAL foreground: a
browser permission prompt, a native file picker, or verifying with your own eyes.
**It STEALS the operator's screen** — telemetry caught a session calling it 1–5 times
per minute, grabbing the screen on nearly every interaction. If you must, call it
**once per TAB, never per read**. 🔴 RECORD both axes BEFORE the raise
(`xdotool getactivewindow`; `i3-msg -t get_workspaces | jq -r '.[]|select(.focused).num'`)
— by the time you want them back the values are gone — and restore BOTH afterwards,
on failure too: `i3-msg '[id="<prev-winid>"] focus'` **and** `i3-msg workspace <n>`.
Focusing a window that lives on another workspace switches to it, so restoring the
recorded window usually carries the workspace back — but a criteria command silently
no-ops if that window has closed, and there is nothing to record if the operator was
on an empty workspace. The workspace is the axis that gets left behind, so restore it
explicitly rather than relying on the focus command to do it. It foregrounds via host-side `i3-msg`
(Chrome-side `tabs.update`/`windows.update` is a no-op on i3), so it is i3-gated
(`i3:"skipped"` off a graphical i3 host) and is **not available to the autonomous
browser-agent at all**.

**Caveat:** in-frame `click`/`type` are SYNTHETIC (`isTrusted:false`, the
`chrome.scripting` OOPIF path) — but were verified to actually drive the real app
(the Grid tab got selected). Top-frame input remains TRUSTED CDP.

### The i3 raise is OPT-IN — read the `i3` field before believing the screen moved

`activate` always activates the tab Chrome-side, but the host-side i3 raise is a separate,
consent-gated step. The result reports it as `i3: applied | skipped | failed | withheld`:

- **`withheld`** — the command did not carry `focus:true`, so the raise was never asked
  for and the operator's screen was untouched. The CLI's `--focus` / `--no-focus`
  decides, and the DEFAULT is **on iff stdout is a TTY**: a human typing `browser
  activate` in a terminal gets the raise; an agent (Claude Code's Bash tool, opencode, any
  script) runs with stdout on a pipe and gets the tab activated WITHOUT it. That is a
  structural discriminator — a real property of the process's stdio, not a keyword
  heuristic — and it is overridable in both directions, so a script that genuinely wants
  the screen says `--focus` and says it out loud. Measured: over three weeks all 166
  `activate` calls came from non-interactive callers, and 0 of the 9 interactive
  `browser` commands in the same telemetry were an activate.
- **`skipped`** — this host has no i3, so it could not have raised anything regardless of
  consent. It is reported separately from `withheld` precisely so the `--focus` advice is
  not offered where it would be a dead end.

Either way, `withheld`/`skipped` mean the window was NOT raised — so anything that
genuinely needed the real foreground (a browser permission prompt, a native file picker)
did not happen.
