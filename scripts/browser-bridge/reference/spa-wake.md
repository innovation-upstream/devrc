# Throttled / backgrounded tabs — the `wake` pattern

**Load this when:** a `text`/`html` read came back empty, half-built or shell-only ·
a read reported `data.hidden:true` or printed a hidden-tab warning on stderr · an SPA
looks stuck "Loading…" · `frames` lists no OOPIF you know is there · in-frame
`click`/`type` hits elements that "don't exist yet" · you are about to conclude a site
is BROKEN from a browser read · before driving any heavy JS app in an `open`ed tab.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## The trap

A heavy SPA opened in a **backgrounded** tab is throttled by Chrome —
`document.visibilityState:"hidden"`, so it gets **no animation frames at all** and
~1 Hz timers, and it often **never finishes rendering**. You then can't read or
drive it: `text`/`frames` come back empty or half-built, and in-frame
`click`/`type` hit elements that don't exist yet. Verified case:
`model-benchmarking.civit.ai` (an OOPIF inside a `civitai.com` tab) stayed blank
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

## When you actually need `activate` (rare)

`activate` is still the honest answer when something needs the REAL foreground: a
browser permission prompt, a native file picker, or verifying with your own eyes.
**It STEALS the operator's screen** — telemetry caught a session calling it 1–5 times
per minute, grabbing the screen on nearly every interaction. If you must, call it
**once per TAB, never per read**, and restore focus afterward
(`i3-msg '[id="<prev-winid>"] focus'`). It foregrounds via host-side `i3-msg`
(Chrome-side `tabs.update`/`windows.update` is a no-op on i3), so it is i3-gated
(`i3:"skipped"` off a graphical i3 host) and is **not available to the autonomous
browser-agent at all**.

**Caveat:** in-frame `click`/`type` are SYNTHETIC (`isTrusted:false`, the
`chrome.scripting` OOPIF path) — but were verified to actually drive the real app
(the Grid tab got selected). Top-frame input remains TRUSTED CDP.
