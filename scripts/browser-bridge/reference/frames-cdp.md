# Frames, cross-origin OOPIFs, and CDP

**Load this when:** a `--frame` op returned `frame_not_found:<url>`,
`ambiguous_frame:<n>`, `frame_eval_failed:<reason>`, `oopif_depth_cap:5`,
`oopif_target_cap:50`, or `cdp_attach_refused:<scheme>` · a `--frame` read came back
as the TOP page instead of the iframe you meant · `eval --frame` returned
`value:null` · you need to read or drive something inside a cross-origin iframe ·
you need to know why in-frame `click`/`type` is `isTrusted:false` · Brave is showing
"an extension is debugging this browser" and you want to know why.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## What reaches what

`frames` + `--frame` reach cross-origin OUT-OF-PROCESS iframes (OOPIFs) via
`chrome.webNavigation` + `chrome.scripting`; `screenshot` + TOP-frame trusted input use
the Chrome DevTools Protocol (`debugger` permission). Together they fix three real
limitations:

1. **Screenshot a BACKGROUND / occluded tab** (and each profile's own tab) — CDP
   `Page.captureScreenshot` does not need the tab to be the on-screen foreground
   tab, so the old i3 "not visible on-screen" limitation is gone for the normal
   path. (`--fullpage` grabs the whole scrollable document.)
2. **Read/drive INTO a CROSS-ORIGIN iframe (the OOPIF fix)** — `browser --tab T frames`
   LISTS the cross-origin frame (e.g. `model-benchmarking.example.test` inside
   `civitai.com`) because `chrome.webNavigation.getAllFrames` enumerates OOPIFs that
   CDP `Page.getFrameTree` could not; then `browser --tab T --frame <numericId-or-url>
   text` reads inside it and `--frame … click/type/key` drives an in-app control (via
   `chrome.scripting` injection), while `--frame … eval '<js>'` runs a JS string inside
   it via CDP `Runtime.evaluate` (e.g. `--frame <oopif> eval 'location.href'` returns the
   OOPIF's own url, not null). Plain `text`/`html`/`eval` (no `--frame`) still see only
   the top frame.
3. **Drive an app's TOP frame with trusted input** — top-frame `click`/`type`/`key`
   dispatch real `isTrusted` events via CDP. (In a cross-origin `--frame`, input is
   SYNTHETIC — see below.)

## How `--frame` routes

`--frame <F>` (a **numeric** `frameId` from `frames`, or a URL substring) routes a
`text`/`html`/`click`/`type`/`key` INTO that frame via `chrome.scripting` — which
reaches CROSS-ORIGIN out-of-process iframes (OOPIFs). A frame-scoped fixed-func read
runs in an **isolated world** (DOM-capable; it does not see that frame's page globals).
**`eval --frame` is the exception: it runs the JS string via CDP `Runtime.evaluate`**
(`chrome.scripting` can only run a serialized func, so the old path evaluated nothing and
returned `value:null`) — same-process frames in an isolated world, a cross-origin OOPIF
in its own flat session; a resolve/exec failure is a clear `frame_not_found` /
`frame_eval_failed` error, never a silent null.
In-frame `click`/`type`/`key` dispatch **SYNTHETIC** events (`isTrusted:false`, the
reachable OOPIF path — drives most apps); the result carries `trusted:false` for
`--frame` input and `trusted:true` for top-frame CDP input. A `--frame` op reports the
FRAME's own `url` (so you can confirm you read the intended frame, not the top).

**Picking `--frame` reliably (avoid the wrong frame):** prefer a **numeric `frameId`**
from `frames` — it always wins and is never ambiguous. A URL substring is resolved
**HOST-first** (matched against each frame's hostname before its path), so
`--frame model-benchmarking` targets the OOPIF `model-benchmarking.example.test` rather than
the top page's `civitai.com/apps/run/model-benchmarking` PATH. If a substring still
matches **multiple** frames the op fails with `ambiguous_frame:<n> [<id>:<url>, …]`
listing the candidates — re-issue with the numeric `frameId` (it does NOT silently pick
the first match). So: **use the numeric id, or a host substring** — not a bare path token.
**⚠ The numeric-id escape hatch does NOT apply to `eval`/`upload --frame` on a
cross-origin OOPIF** — see the duplicate-URL limitation below.

## Nested OOPIFs (OOPIF-in-OOPIF) — supported, with hard caps

`Target.setAutoAttach` is not recursive (it attaches only a session's DIRECT child
targets), which is why `eval --frame` on a **grandchild** cross-origin iframe used to
return `frame_not_found`. It now **re-arms auto-attach on each attached child session**,
walking the cascade DOWN the frame tree until the wanted frame's target appears — so a
grandchild (and deeper) cross-origin frame IS reachable by `eval --frame` and by the
OOPIF branch of `upload --frame`. Because a hostile page can nest/spawn frames without
limit, the descent is **hard-bounded** and every bound fails LOUD (never a silent
truncation, never a hang):

| failure | error | meaning |
|---------|-------|---------|
| frame never attaches within the bounded wait (5 s **hard** ceiling, checked every iteration; 600 ms quiet-window settle, restarted on each new `setAutoAttach`) | `frame_not_found:<url> cascade[…]` | the frame isn't there — **plus a bounded diagnostic**, see below |
| nesting deeper than **5** levels below the tab | `oopif_depth_cap:5` | we deliberately stopped descending |
| more than **50** attached targets for one op | `oopif_target_cap:50` | frame-spamming page; work is bounded |
| two attached frames share the target URL | `ambiguous_frame:<n> [<sessionId>:<url>, …]` | **no escape hatch — see below**; it never silently picks one |

Both caps are named constants in `extension/protocol.js` (`OOPIF_MAX_DEPTH`,
`OOPIF_MAX_TARGETS`, `OOPIF_SETTLE_MS`, `OOPIF_WAIT_MS`) and the whole cascade stays well
under the per-op CDP budget. `text`/`html`/`click`/`type`/`key --frame` reach a nested
frame as they always did (they use `chrome.scripting`, not CDP).

**Every discovered target is filtered before it can ever be an eval target** — the
recursion removed the old implicit "one level below a validated tab" boundary, so the
boundary is now explicit: **own tab** (`chrome.debugger.onEvent` is global; a foreign
`source.tabId` is dropped — and a caller that omits the tab fails CLOSED), **`iframe`
targets only** (so a page's `new Worker(location.href)` can neither deny service by
forcing `ambiguous_frame` nor capture your JS in a worker global), and **http/https
only** (the same scheme gate the top tab passes, so a `chrome-extension://` child — any
extension's web-accessible resource — can never become an eval target one level down).

**⚠ Duplicate-URL OOPIFs are UNREACHABLE by `eval`/`upload --frame`, and the numeric
`frameId` does NOT help.** The CDP path matches the frame purely by URL (a numeric
webNavigation frameId has no 1:1 CDP target mapping), so two identical
`<iframe src="https://embed.example/widget">` — a duplicated ad/widget slot, which is
common — both match and the op fails `ambiguous_frame` with no way to pick one. That is a
deliberate refusal, not a silent wrong-frame, but it IS a dead end: use
`text`/`html`/`click`/`type`/`key --frame` (which resolve by numeric frameId and are
unaffected), or change the page selector. A parent-chain tiebreak is a filed follow-up.

**Every OOPIF failure carries a bounded `cascade[…]` diagnostic** naming the loop exit
(`match`/`settle`/`deadline`/`depth-cap`/`target-cap`), which sessions were auto-attached,
and — for up to 20 observed targets — the target `type`, whether `source.tabId` was
present/matched, whether the parent session was known, the computed depth, and why each
was dropped (`drop:type`/`drop:scheme`/`drop:foreign-tab`/`drop:unowned`/`drop:dup`).
It is **caller-facing text only** — telemetry stays metadata-only. Read it before
theorising about a `frame_not_found`.

**✅ LIVE-VERIFIED against real Brave** (`tests/fixtures/oopif-rig/`, both checks). A
grandchild OOPIF evaluates correctly, and on the 7-level deep rig depth 3 and depth 5
resolve while depth 6 is refused with `oopif_depth_cap:5` and a full trace showing four
chained sub-session auto-attaches. **`OOPIF_MAX_DEPTH = 5` is a real, measured guarantee**
— not a contingent one.

**📌 Discovered Chrome behaviour (cost a full verify round — remember it):
Chrome does NOT populate `source.tabId` on SUB-session `Target.attachedToTarget` events.**
They carry `sessionId` only. An own-tab check that requires `tabId` therefore silently
eats every level-2+ event and the whole cascade goes **inert** — it returns
`frame_not_found` and *never* a depth cap, because no nested session is ever recorded.
Ownership is consequently proven by **session parentage**: an event whose
`source.sessionId` is a session this cascade itself attached is ours. `tabId` stays
authoritative when present; an event proving neither is dropped. If you ever add a
listener-side own-tab gate to a flat-mode CDP cascade, this is the trap.

The `cascade[…]` trace is also a useful **deterministic tell for whether an extension
reload took**: an old build answers a nested-OOPIF miss with a bare
`frame_not_found:<url>`, a new build answers with the same error **plus** the trace.

## CDP security model (built in — this is the point)

- **Frame enumeration + injection are STRICTLY own-tab-scoped.** `getAllFrames` and
  `executeScript` are tab-scoped, so a model-supplied `--frame` can only ever resolve
  to / inject into a frame of the session's owned/`--tab` tab — never another tab.
- **CDP attach is STRICTLY own-tab-scoped.** The server routes a CDP op only to the
  session's owned/`--tab` tab; the extension attaches `chrome.debugger` ONLY to that
  tab and **refuses to attach to a privileged surface** (`chrome://`, extension,
  `devtools:`, `file:`) — the attach is validated *before* it happens. The
  autonomous agent's tab is FORCED, so it can never attach to another tab/profile.
- **NO raw-CDP passthrough.** The agent's typed tool exposes ONLY the bounded ops
  with typed scalars — there is no `cdp`/`method`/`params` field, so the model can
  never send an arbitrary CDP command (no `Page.navigate file://`, no `Browser.*`,
  no exfil `Runtime.evaluate`). Arbitrary CDP would reintroduce an RCE-class hole.
- **Always detach.** Every CDP op is attach→run→**detach** (a `finally`, so a thrown
  op still detaches) — no leaked attachment / stuck banner. An out-of-band detach is
  handled too.
- **Metadata-only telemetry** still holds: op/domain only — never frame URLs, typed
  text, eval source, or screenshot bytes.

**Tradeoff — the debug banner:** while a CDP op runs, Brave shows an "an extension
is debugging this browser" banner. Attach is per-op (attach → run → detach) to keep
that window tiny; a simple top-frame `text`/`html`/`eval`/foreground-`screenshot`
takes the lighter non-CDP path and shows no banner.

> **After updating the extension you MUST reload it** (the manifest gained the
> `debugger` permission) — see `~/workspace/devrc/scripts/browser-bridge/reference/errors.md`; Brave may prompt to
> re-confirm the new permission.

Result payloads land under `.result.data` in the JSON (the envelope is
`{"ok":true,"result":{"id","ok","data":{...}}}`).
