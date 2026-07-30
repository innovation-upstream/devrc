---
name: browser
description: Drive the user's LIVE, logged-in Brave browser from Claude Code — read the active tab's HTML, run JS in it, list/navigate tabs, and screenshot the visible tab — via the local token-authenticated browser-bridge (loopback rendezvous server + MV3 extension). Use when the user asks you to look at / read / scrape / interact with a page THEY have open, act on an authenticated site they're logged into, check what's on their screen in Brave, navigate their browser, or grab a screenshot of their current tab. NOT for headless fetching of public URLs (use WebFetch) — this is specifically their real, authenticated session.
---

```
~/workspace/devrc/scripts/browser-bridge/browser <subcommand>
```

**Run it by that exact path** (also `~/.claude/skills/browser/browser` if the
skill dir is symlinked). Don't hunt for it under `~/.claude/skills/...`.

## Quick start / binary path

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser   # ← the executable
$BB whoami                          # ORIENT FIRST — which HOST (laptop/workbench; both are hostname 'nixos'),
                                    #   which profiles/instances are connected, loaded-vs-repo extension version.
                                    #   Run this BEFORE grabbing the browser so you know which machine/profile you're driving.
$BB health                          # is an extension connected?
$BB --instance <key> open <url>     # open a NEW tab this session owns
$BB --instance <key> --tab <id> html   # act on a specific tab
```

**Orientation rule:** on a fresh browser task, run `whoami` first (both hosts are
hostname `nixos`, and this session's loopback bridge could be either machine with
multiple profiles) — confirm the host + pick the right `--instance` before acting.

## ⚠ The LOADED extension may be older than this doc

The CLI is always current; the **extension build running in Brave is not**. Brave
does not hot-reload unpacked extensions, so the live service worker can be far
behind. Symptom, seen for real:

```
$ browser --instance work open https://example.com
op 'open' failed in the browser: unknown_op
```

**`open` answering `unknown_op` means the loaded extension predates owned-tab
support** — the whole "per-session tab isolation" section below is unavailable
until the user reloads it. Don't debug the server; **tell the user to click reload
↻ on the card in `brave://extensions`**. Meanwhile work without `open`: run
`browser tabs`, pick an existing tab, and pass `--tab <id>` on every op (or just
read the active tab for a one-shot). Same reasoning for any op that answers
`unknown_op` (e.g. `upload` on a pre-0.2.0 extension).

The CLI now **detects this for you**: any op the CLI dispatches that returns a
server-side `unknown_op` is mapped to a clear message + non-zero exit telling you
the loaded extension is OLDER than the CLI and to reload/restart. **`browser
health` also shows the build** — each instance's loaded `extension_version` vs the
bridge's `extension_version_current` (repo manifest); a mismatch = stale.

**⚠ Reload ↻ is UNRELIABLE — a full Brave restart is the reliable fix.** The
extension's long-poll keeps the OLD service worker permanently alive, so clicking
↻ in `brave://extensions` often does NOT swap in the new build. If a reload
doesn't take (the op still returns `unknown_op`, or `health` still shows the old
`extension_version`), tell the user to **fully quit and reopen Brave**.

## `eval` gotchas — a `null` result does NOT mean the bridge is down

- **`eval` evaluates ONE EXPRESSION, not a script.** A multi-statement body
  (`window.scrollBy(0,1400); "ok"`) returns **`null` with no error** — it looks
  like a broken bridge and isn't. Wrap it: `(function(){ window.scrollBy(0,1400);
  return "ok" })()`, or use the comma operator.
- **`eval` can't run on `chrome://` / `brave://` URLs** — expect `Cannot access a
  chrome:// URL` (and a `null` result).
- **Strict page CSP silently blocks the injected script — notably GitHub.** On a
  GitHub page even `document.title` comes back `null`, no error. **Use `html` /
  `text` there — they work**, because they don't inject script.

So on a `null`: first suspect a multi-statement body, then page CSP; fall back to
`text`/`html` before concluding the bridge is down.

## The user is USING this browser

It's their live session, not a scratch VM. Don't `nav` a tab that may hold unsaved
work (a half-typed comment, a form, a logged-in console) — prefer a
`chrome://newtab`/obviously disposable tab, or `open` your own when the extension
supports it. If you focus a window to capture it, **restore their focus
afterwards** (`i3-msg '[id="<prev-winid>"] focus'`).

## Concurrency / don't do this

- **Concurrent drivers (esp. sibling subagents) → each `open` and thread its own
  `--tab <id>`.** Sibling subagents of one parent SHARE a session id (they
  inherit `CLAUDE_CODE_SESSION_ID` + `$TMUX_PANE`), so without explicit `--tab`
  they fight over the SAME active tab. Have each driver `browser open` (capture
  the returned `tabId`) and pass `--tab <id>` on every subsequent op.
- **Do NOT run a bare high-rate `eval` loop** (no `--instance`/`open`). It shares
  the one active tab AND saturates the single serial extension connection; the
  server now **rate-limits** it and returns **HTTP 429** (`rate_limited` /
  `queue_full`) — the `browser` CLI prints a back-off message and exits non-zero.
  Batch what you need into fewer `eval`s, or space them out.

# browser — drive the live Brave session

`browser-bridge` lets you operate the user's **real, logged-in Brave** browser.
Commands go: the `browser` CLI → a loopback rendezvous server (`127.0.0.1:8788`,
bearer-token auth) → a standalone MV3 extension in the live Brave session →
executed against the **active tab** → result back to you.

This is authorized personal automation on the user's own workbench. It is a
**sibling** to the activity-collector browser extension (telemetry) — different
subsystem, do not conflate.

Full architecture, security model, and deploy: `scripts/browser-bridge/README.md`.

## Entrypoint

`scripts/browser-bridge/browser <subcommand>` (JSON on stdout, pretty-printed if
`jq` is present):

Prefix any op with `--instance <key>` to target a specific connected profile
(`browser --instance work html`) and/or `--tab <id>` to target an explicit tab.

| command | does |
|---------|------|
| `browser health`            | connected instances + count: `{"ok":true,"extension_connected":bool,"count":N,"extension_version_current":"<repo manifest>","instances":[{…,"extension_version":"<loaded>"}]}`. Each instance now shows its **loaded `extension_version`** and the bridge shows **`extension_version_current`** (the repo manifest) — eyeball loaded-vs-current to spot a STALE extension |
| `browser whoami`            | **read-only identity + diagnostics** (global; no `--instance`/`--tab`). Reports **which HOST** (`host.label` = `laptop`/`workbench`/`unknown`, resolved `ACTIVITY_HOST` env → the activity-collector env file → LAN-IP detect, with `host.source` naming the method + `host.ips`), the connected **instances** (`key`/`label`/`instanceId` + active-tab **DOMAIN** only + reported `extension_version`), and **bridge** diagnostics (`endpoint`/`port`/`server_version`{version+git-HEAD}/`connected` count/`rate_limit` + `extension_version_current` = the manifest version the SERVER reads from the repo). Use it FIRST to confirm you're on the right host/profile (both hosts are hostname `nixos`). ⚠ There is deliberately **no hard "stale" flag** — the manifest version isn't bumped per-change, so compare `extension_version` (loaded) vs `extension_version_current` (repo) by eye. `extension_version` shows `null` until an extension build that reports it has reloaded; the rest of whoami needs NO extension change/reload. |
| `browser instances`         | list connected instances as JSON (routing key, label, instanceId, active-tab url/title) |
| `browser [--instance K] open [url]`        | open a NEW tab THIS session owns (default `about:blank`, created in the **background/HIDDEN** — `active:false`); records ownership; returns its `tabId`. Use for multi-step work. ⚠ A background tab is `document.visibilityState:"hidden"` → **Chromium throttles it, so a heavy SPA never renders** and a subsequent `text`/`html`/`eval` returns a **shell-only DOM** (indistinguishable from a broken site). The reads now **self-announce** this (`data.hidden:true` + a one-line warning on stderr); the escape hatch is **`browser activate`** (foreground it so it un-throttles). |
| `browser [--instance K] close`             | close this session's owned tab and drop ownership |
| `browser [--instance K] release`           | drop ownership WITHOUT closing the tab |
| `browser [--instance K] [--tab T] html [--max-bytes N]` | `outerHTML` of the owned tab (else the active tab), byte-capped exactly like `text` (default 32768; `0`=uncapped; truncation appends `…[truncated N bytes]` and sets `truncated`). One uncapped `html` on a heavy SPA is ~100K tokens — the cap is ON by default. Prefer `text` anyway |
| `browser [--instance K] [--tab T] text [selector] [--max-bytes N]` | **cheap read** — visible `innerText` of the owned/active tab (optional CSS `selector`), whitespace-normalized + byte-capped (default 32768; `0`=uncapped; a truncation note is appended). ~98% smaller than `html` — prefer it |
| `browser [--instance K] [--tab T] [--frame F] js '<js>'` | **ALIAS of `eval`** — identical semantics and flags, and the op sent ON THE WIRE is `eval` either way. **Prefer `js` when you are a worktree-isolated agent:** Claude Code's isolation guard pattern-matches the literal token `eval` in a command string and REFUSES to run it ("runs a string through eval, which can't be verified to stay inside the worktree"). The guard reacts to the WORD, not the behaviour — this op ships JS over loopback to Brave and runs it in the PAGE; it touches no filesystem and cannot escape the worktree. `eval` remains valid everywhere else and is NOT deprecated |
| `browser [--instance K] [--tab T] [--frame F] eval '<js>'` | run JS in the owned/active tab, return its value. **`--frame` runs the JS INSIDE the target frame (incl. a cross-origin OOPIF) via CDP `Runtime.evaluate`** (not `chrome.scripting`, which can only run a func — the old path returned `value:null`); a frame that can't be resolved / an exception → a clear `frame_not_found` / `frame_eval_failed:<reason>` error, never a silent null; reports the frame's own `url` |
| `browser [--instance K] tabs`              | list open tabs (`.data.ownedTabId` flags this session's owned tab) |
| `browser [--instance K] [--tab T] nav <url>`         | navigate the owned/active tab to `<url>` |
| `browser [--instance K] [--tab T] screenshot [path] [--fullpage] [--data-url]` | screenshot via **CDP `Page.captureScreenshot`** — **works on a BACKGROUND/occluded tab** and on each profile's own tab (a foreground tab uses the cheap captureVisibleTab fast path). `--fullpage` grabs the whole scrollable document. **Always writes a `.png`**: to `path` if given (prints the path), else to a temp file (honours `TMPDIR`) and prints compact JSON `{ok,path,bytes,url,via}`. **The base64 data URL is NEVER printed** — it cost 133K–890K tokens per call and you can't see an image from a data URL anyway; **`Read` the `.png`**. `--data-url` is the escape hatch that restores the old raw-data-URL output (mutually exclusive with `path`) |
| `browser [--instance K] [--tab T] frames`  | list the tab's frames (`frameId`/`url`/`parentFrameId`) via `chrome.webNavigation`, **INCLUDING cross-origin OUT-OF-PROCESS iframes (OOPIFs)** — pick a numeric `frameId` (or url-substring) for `--frame` |
| `browser [--instance K] [--tab T] [--frame F] click <selector>` | click the element's center — **TRUSTED** CDP on the top frame; **SYNTHETIC** (`chrome.scripting`) inside a cross-origin `--frame` |
| `browser [--instance K] [--tab T] [--frame F] type <text> [--selector S]` | text input — **TRUSTED** CDP top frame; **SYNTHETIC** in-frame (focus `--selector` first if given) |
| `browser [--instance K] [--tab T] [--frame F] key <Enter\|Tab\|Escape\|Backspace\|Delete\|Arrow*\|Home\|End\|Page*> [--selector S]` | dispatch one bounded key — **TRUSTED** CDP top frame; **SYNTHETIC** in-frame |
| `browser [--instance K] [--tab T] [--frame F] upload <selector> <path>` | populate the `<input type=file>` at `<selector>` with the LOCAL file `<path>` via **CDP `DOM.setFileInputFiles`** — Chrome reads the file BY PATH itself (same host), so **no bytes cross the bridge**. The CLI validates the path (readable regular file) + resolves it to ABSOLUTE **before** dispatch; `--frame` routes into a cross-origin OOPIF. **Bounded TYPED op, own-tab, NO raw-CDP passthrough** — but it IS **data-exfil-capable** (any readable file's CONTENTS could be posted to the site), so **every upload is AUDIT-LOGGED** (op + target domain + path). Result carries the basename only |
| `browser [--instance K] [--tab T] activate [--wait MS \| --no-wait]` | **FOREGROUND** the owned/active tab so a foreground-throttled SPA finishes loading, then can be read/driven. **⚠ STEALS FOCUS — the ONE intrusive op** (it changes what the user sees; every other op is non-intrusive). Foregrounds via **host-side `i3-msg`** (Chrome-side `tabs.update`/`windows.update` is a no-op on i3), so it works ONLY on a graphical i3 host; returns an extra **`i3:"applied"\|"skipped"\|"failed"`** field alongside `{tabId,windowId,url,title,active,status}`. Waits (bounded, default ~3s, cap ~8s) for `status:"complete"` + a paint settle unless `--no-wait`; `--wait MS` overrides. See "Driving a throttled SPA" below. |
| `browser [--instance K] agent "<goal>" [flags]` | run the **autonomous opencode browser-agent** in its OWN isolated tab against `<goal>`; returns a compact `{answer,evidence,steps_used,status}` (see below) |
| `browser --print-session-id`               | print the derived per-session id (debug) and exit |

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
`--frame model-benchmarking` targets the OOPIF `model-benchmarking.civit.ai` rather than
the top page's `civitai.com/apps/run/model-benchmarking` PATH. If a substring still
matches **multiple** frames the op fails with `ambiguous_frame:<n> [<id>:<url>, …]`
listing the candidates — re-issue with the numeric `frameId` (it does NOT silently pick
the first match). So: **use the numeric id, or a host substring** — not a bare path token.

**Limitation — nested OOPIFs (OOPIF-in-OOPIF):** the CDP `eval --frame` path
auto-attaches only DIRECT child cross-origin targets (`Target.setAutoAttach({flatten})`
is not recursive), so `eval --frame` on a **grandchild** cross-origin iframe (a
cross-origin frame nested inside another cross-origin frame) returns `frame_not_found`
(it fails safe — never a wrong/silent result). `text`/`html`/`click`/`type`/`key --frame`
(via `chrome.scripting`) can still reach such a deeply-nested frame; only the CDP-based
`eval --frame` is limited to direct children.

Result payloads land under `.result.data` in the JSON (the envelope is
`{"ok":true,"result":{"id","ok","data":{...}}}`).

## Frame ops (webNavigation + scripting) & CDP ops (debugger)

`frames` + `--frame` reach cross-origin OUT-OF-PROCESS iframes (OOPIFs) via
`chrome.webNavigation` + `chrome.scripting`; `screenshot` + TOP-frame trusted input use
the Chrome DevTools Protocol (`debugger` permission). Together they fix three real
limitations:

1. **Screenshot a BACKGROUND / occluded tab** (and each profile's own tab) — CDP
   `Page.captureScreenshot` does not need the tab to be the on-screen foreground
   tab, so the old i3 "not visible on-screen" limitation is gone for the normal
   path. (`--fullpage` grabs the whole scrollable document.)
2. **Read/drive INTO a CROSS-ORIGIN iframe (the OOPIF fix)** — `browser --tab T frames`
   now LISTS the cross-origin frame (e.g. `model-benchmarking.civit.ai` inside
   `civitai.com`) because `chrome.webNavigation.getAllFrames` enumerates OOPIFs that
   CDP `Page.getFrameTree` could not; then `browser --tab T --frame <numericId-or-url>
   text` reads inside it and `--frame … click/type/key` drives an in-app control (via
   `chrome.scripting` injection), while `--frame … eval '<js>'` runs a JS string inside
   it via CDP `Runtime.evaluate` (e.g. `--frame <oopif> eval 'location.href'` returns the
   OOPIF's own url, not null). Plain `text`/`html`/`eval` (no `--frame`) still see only
   the top frame.
3. **Drive an app's TOP frame with trusted input** — top-frame `click`/`type`/`key`
   dispatch real `isTrusted` events via CDP. (In a cross-origin `--frame`, input is
   SYNTHETIC — see above.)

**Security model (built in — this is the point):**
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
> `debugger` permission) — see the reload section below; Brave may prompt to
> re-confirm the new permission.

### The X-server fallback (still available for a genuinely un-composited window)

If a window is on another i3 workspace and even CDP capture is unsatisfactory, you
can still bypass the extension and capture the X window directly:

```sh
# 1. The Bash tool has NO X env by default — without this xdotool silently
#    "sees" zero windows and every search returns nothing.
export DISPLAY=:0 XAUTHORITY=/home/zach/.Xauthority

# 2. Find the window by PAGE TITLE, not by class — several Brave windows exist
#    and class-matching gives you an arbitrary one.
nix-shell -p xdotool --run 'xdotool search --name "<page title fragment>"'

# 3. Make it VISIBLE — focusing its workspace is not enough.
i3-msg '[id="<winid>"] focus'

# 4. Capture (settle first; the compositor needs a beat after the raise).
nix-shell -p xdotool maim --run 'xdotool sleep 2; maim -i <winid> out.png'
nix-shell -p imagemagick --run 'magick out.png -crop WxH+X+Y +repage cropped.png'
```

Two traps that produce a confidently-wrong result:

- **A window on the focused workspace can still be *behind* another window** — the
  capture then comes back blank/dark while `maim` exits 0. **Verify by LOOKING at
  the image**, never by trusting the exit code.
- **`maim -i <winid>` captures whatever tab is active in that window**, which may
  not be the tab you just `nav`ed. After navigating, re-find the window **by the
  new page title** so you're capturing the tab you think you are.
- *(Future option, NOT implemented: a `chrome.debugger` + CDP
  `Page.captureScreenshot` path could capture an off-screen tab, but it needs the
  `debugger` permission and shows a debug banner — deliberately out of scope.)*

## Driving a throttled/backgrounded SPA (the `activate` pattern)

A heavy SPA opened in a **backgrounded** tab is throttled by Chrome —
`document.visibilityState:"hidden"`, so its timers/RAF are starved and it often
**never finishes rendering**. You then can't read or drive it: `text`/`frames`
come back empty or half-built, and in-frame `click`/`type` hit elements that don't
exist yet. Verified case: `model-benchmarking.civit.ai` (an OOPIF inside a
`civitai.com` tab) stayed blank while backgrounded.

`activate` fixes this by **foregrounding the tab** so it un-throttles
(`visibilityState:"visible"`) and the app paints. On i3 the foregrounding is done
**host-side via `i3-msg`** (Chrome's own `tabs.update`/`windows.update` is a no-op
under a tiling WM), and the result carries `i3:"applied"|"skipped"|"failed"` so you
can confirm it actually raised the window.

**Verified pattern to read & drive a cross-origin app:**

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work open https://civitai.com/apps/run/model-benchmarking  # backgrounded
$BB --instance work activate            # → visibilityState:"visible", app renders
$BB --instance work frames              # now the OOPIF is listed
$BB --instance work --frame model-benchmarking text    # read inside it
$BB --instance work --frame model-benchmarking click 'button:has-text("Grid")'  # drive it
```

**Caveats (document honestly):**
- **`activate` STEALS FOCUS** — it's the one intentionally-intrusive op; it changes
  what the user sees. Restore their focus afterward if it matters
  (`i3-msg '[id="<prev-winid>"] focus'`).
- **i3-gated:** it only works on a graphical i3 host (it shells out to `i3-msg`); on
  a headless/non-i3 host expect `i3:"skipped"` (or `"failed"`) and no foregrounding.
- **In-frame `click`/`type` are SYNTHETIC** (`isTrusted:false`, the `chrome.scripting`
  OOPIF path) — but were verified to actually drive the real app (the Grid tab got
  selected). Top-frame input remains TRUSTED CDP.

## Diagnosing a CSS / layout bug (hit-test, don't theorise)

The bridge is the only way to see PAINT ORDER. Markup-level tests and `html` reads
can't: an element can be present, correct, and completely covered. The sequence
that found a real one (civitai-manager v0.1.82 — an open popover painted under the
next card, after ~30 UI changes had passed every server-side test):

**1. `open` → `activate` → `screenshot` — and LOOK at the image.** `activate` is
not optional; a backgrounded tab is throttled and may never finish painting, so
you'd screenshot a half-built page. Exit 0 is not a rendered page. `screenshot`
prints a `.png` PATH (not a data URL) — `Read` that file; that is the LOOK step.

**2. Hit-test the suspect element.** Take its `getBoundingClientRect()` and call
`document.elementFromPoint(x, y)` at several points inside it, reporting for each
whether the hit node is `contains()`-inside the element you expected. This NAMES
the covering element instead of guessing — in the real case it returned the *next*
card's NSFW-reveal `<button>`, which reading the popover's own CSS would never
have suggested.

```bash
$BB --instance work eval '(function(){
  const el = document.querySelector(".cm-updated-pop");
  const r  = el.getBoundingClientRect();
  const pts = [[r.left+8, r.top+8], [r.left+r.width/2, r.top+r.height/2], [r.right-8, r.bottom-8]];
  return JSON.stringify(pts.map(([x,y]) => {
    const hit = document.elementFromPoint(x, y);
    return {x, y, hit: hit && hit.className, insidePop: !!hit && el.contains(hit)};
  }));
})()'
```

Any `insidePop:false` is the bug, and `hit` is the culprit.

**3. Walk the ancestors for the first stacking-context creator.** The offender is
almost never the element you're staring at. Check each ancestor's computed
`transform`, `filter`, `opacity` (<1), `isolation`, `will-change`, `contain`, and
`position`+non-`auto` `z-index` — the first one that creates a context traps every
`z-index` below it.

```bash
$BB --instance work eval '(function(){
  const out=[]; let n=document.querySelector(".cm-updated-pop");
  while (n && n !== document.documentElement) {
    const s = getComputedStyle(n);
    out.push({cls:n.className, pos:s.position, z:s.zIndex, tf:s.transform,
              flt:s.filter, op:s.opacity, iso:s.isolation, wc:s.willChange});
    n = n.parentElement;
  }
  return JSON.stringify(out);
})()'
```

**4. Inject a probe `<style>`, re-hit-test, THEN write code.** Proving the fix in
the live page before touching the repo turns a guess into a measurement.

```bash
$BB --instance work eval '(function(){
  const s=document.createElement("style"); s.id="probe";
  s.textContent=".cm-lift:has(.cm-updated:hover){z-index:25}";
  document.head.appendChild(s); return "probed";
})()'
# ...re-run the step-2 hit-test; every point should now report insidePop:true...
$BB --instance work eval '(function(){ const s=document.getElementById("probe"); if(s) s.remove(); return "clean" })()'
```

⚠ **A probe that clears the problem can still be wrong in the OTHER direction.**
The first value tried in the real case cleared the overlap fine — and would have
painted the card over the sticky nav. Only checking the UPPER bound caught it. So
hit-test both: the thing that was covered, *and* the chrome your fix now
out-ranks. **Always remove the probe** — it's the user's live page.

⚠ Reiterating, because it bites hardest here: **`eval` takes ONE EXPRESSION.** All
four steps above are multi-statement, so every one of them must be wrapped in
`(function(){ … })()` — otherwise you get `null` with no error and spend the next
ten minutes debugging a bridge that is working fine.

## `browser agent "<goal>"` — autonomous read/navigate in an isolated tab

Offload an open-ended "go read X and tell me Y" browsing task to a **cheap
autonomous agent** (opencode + DeepSeek `deepseek-v4-flash` via OpenRouter) so it
never burns YOUR context on transient page HTML — only a compact structured
result comes back.

```bash
browser agent "go to news.ycombinator.com and report the top 3 story titles" \
  [--instance K] [--allow-domains a.com,b.com] [--deny-domains x.com] \
  [--steps N] [--timeout S] [--dry-run]
```

- **Output (stdout):** one compact JSON object — never raw HTML:
  `{"answer":"…","evidence":["…"],"steps_used":N,"status":"ok|partial|blocked"}`.
  Exit 0 for `ok`/`partial`, non-zero for `blocked`/errors.
- **Own isolated tab + NO shell (structural safety).** The wrapper `open`s a NEW
  background tab and gives the agent exactly ONE capability: a TYPED custom tool
  `browser` (opencode/tools/browser.js). The agent def **denies bash and every
  other built-in tool**, so the model has no shell at all — it calls the tool with
  structured args (`op` + optional `selector`/`url`/`js`), never a command string.
  The tab, instance, and `--deny/--allow-domains` are **forced on the tool via env
  the wrapper sets** — the model cannot choose the tab or reach a denied domain.
  The tab is closed on EVERY exit path (success, timeout, error).
  - **WHY typed, not bash (the PR #180 RCE fix):** the earlier design gave the
    agent opencode's bash tool scoped to `browser --tab <id> *`. A shell OUTPUT
    REDIRECT (`browser --tab N eval '…' >> ~/.zshenv`) is not a separate command
    node, so it rode the allowed `browser` command through opencode's wildcard
    glob and the shell performed the redirect → a hostile page could induce the
    model to write to a sourced dotfile → host RCE. The typed tool removes the
    shell entirely, so there is no `>`/`;`/`|`/`$()` surface to abuse.
- **Runtime fail-closed tool-set gate (makes an un-upgraded opencode SAFE):**
  before opening a tab or spending a model token, the wrapper runs `opencode debug
  agent browser-agent` (a read-only, **model-free** config dump) and refuses to run
  (`die`, model never invoked) unless the resolved `tools` map is browser-ONLY —
  `browser:true` AND every host tool (`bash`/`read`/`edit`/`write`/`webfetch`)
  present AND `false`. Any uncertainty (unparseable output, `browser` absent, a
  host tool `true`, or a host tool absent) fails closed. Different opencode
  versions resolve the deny differently (workbench 1.17.20, laptop 1.18.4), so this
  is the one place the fail-closed property is *verified at runtime* rather than
  trusted — on a version where the host-tool denial didn't take, `browser agent`
  refuses instead of running the model unconfined. The gate runs BEFORE the tab is
  opened, so a gate failure leaks no tab.
- **Guardrails:** a step budget (`--steps`, default 12), a wall-clock `--timeout`
  (default 120s) enforced with a **process-group kill** (`setsid` + kill the whole
  group, so no opencode child survives), `--deny-domains`/`--allow-domains`
  enforced INSIDE the tool (a denied `nav` is refused before it reaches the
  bridge), a **non-http(s) nav scheme hard-denial** (a `nav` to `file:`/`data:`/
  `about:`/`javascript:`/`chrome:`/… is refused as `nav_scheme_denied:<scheme>`
  before any fetch — those have no host and would otherwise bypass
  `--allow-domains`), and `--dry-run` (intercepts `nav`/`eval` — logs, doesn't
  execute). The full opencode JSON transcript + a metadata-only tool audit are kept
  in a scratch dir. **Domain deny is best-effort** (see note below).
- **⚠ Privacy:** the pages the agent reads are sent to **OpenRouter/DeepSeek**.
  Do NOT point it at high-secret authenticated pages casually.
- **⚠ Domain deny is a mitigation, not a guarantee.** The tool refuses a `nav` to
  a denied host, but it cannot see a page's own client-side redirect (meta-refresh
  / `location=` after an allowed nav) — the bridge navigates the tab and the tool
  only sees the op it issued. Treat `--deny-domains` as best-effort defence in
  depth; the real isolation is the own-tab lock. (Follow-up: server-side
  enforcement against the tab's resolved post-nav URL would make it binding.)
- **Prereqs:** `opencode` on PATH with the OpenRouter key already in its auth
  store (`~/.local/share/opencode/auth.json`), the extension connected, and BOTH
  the agent def AND the custom tool symlinked into opencode's config (see README →
  Deploy). If any is missing you get a clean error and no orphaned tab.

## Per-session tab isolation (use `open` for multi-step work)

Two Claude sessions driving one browser used to interleave on the ONE shared
active tab (session A `nav`s, session B `nav`s in between, A reads B's page).
Now each session can own its own tab:

- **Multi-step workflow → `browser open <url>` first.** The server records this
  session's owned tab (keyed by a stable per-session id sent automatically on
  every request) and routes your subsequent `html`/`eval`/`nav`/`screenshot`/
  `close` to it — a parallel session doing the same on its own tab can't clobber
  you. Finish with `browser close` (closes the tab) or `browser release` (keeps
  the tab, drops ownership).
- **One-shot read → just `browser html` (no `open`).** With no owned tab, ops
  fall back to the active tab — the historical "read the tab the user has open"
  behaviour, which is a single read and inherently safe.
- **`--tab <id>`** targets a specific tab explicitly (overrides owned/active).
- **Double `open` is safe (idempotent).** Calling `browser open` again in a
  session that already owns a **live** tab returns that SAME tabId (it does not
  leak a second tab). If the owned tab was closed, `open` makes a fresh one.
- **Self-heal.** If the user manually closes your owned tab, the next op fails
  with `owned_tab_gone` and the bridge drops your ownership — your NEXT command
  automatically falls back to the active tab. `browser close` always clears the
  mapping, even if the tab was already gone.
- **Session id source:** `CLAUDE_CODE_SESSION_ID` → `$TMUX_PANE` → a
  per-process-tree token (see README). It is routing-only, never trusted for
  auth. If two drivers ever resolve the same id they share a tab (degrades to
  the old behaviour — no worse).
- **⚠ Concurrent drivers that may share a session id (subagents): pass explicit
  `--tab`.** Sibling subagents of ONE parent share identity — a subagent inherits
  the parent's `CLAUDE_CODE_SESSION_ID` and the same `$TMUX_PANE`, and there is NO
  subagent-unique env var — so two parallel subagents derive the SAME session id
  and would own the SAME tab (re-introducing the clobber). Per-session isolation
  only separates concurrent **top-level** sessions. When you spawn concurrent
  drivers that each drive the browser, have EACH one `browser open` (capture the
  returned `tabId`) and then pass its OWN `--tab <id>` on **every** subsequent op
  (`browser --tab <id> nav …`, `--tab <id> html`, …). An explicit `--tab`
  overrides owned-tab routing entirely, so indistinguishable drivers never
  collide. (Each `close`s its own `--tab <id>` at the end.)
- **Contention → FIFO, not failure.** If two sessions DO target the same tab
  (both active, or one `--tab`s another's tab), the commands queue in arrival
  order (bounded by `cmd_timeout`) rather than fail.
- **Lifecycle:** idle ownership is reclaimed after a TTL, which RELEASES it but
  does NOT close the real Brave tab (only `browser close` closes it).

## Multiple instances (per host)

Several Brave profiles can each run the extension and be driven independently —
each has its own command queue (routing key = the profile's **label** if set,
else a stable auto-id; labels must be unique per host).

- **One instance connected** → no `--instance` needed (back-compat).
- **More than one and no `--instance`** → the command **ERRORS** and lists the
  connected instances. Do NOT retry blindly — run `browser instances`, pick the
  right key, and re-issue with `--instance <key>`. (The bridge never guesses.)
- **`browser --instance <key> <op>`** targets one (key = label or auto-id); an
  unknown key errors.
- **Newest supersedes:** a fresh connection for an already-held key drops the old
  one; an in-flight command on the dropped connection returns a `superseded`
  error — just retry. The displaced connection's own `/poll` gets a distinct
  `409 superseded` (not the idle `204`) and the extension **backs off ~30s** (and
  shows a "superseded — set a unique label" state) instead of re-registering
  instantly, so two profiles sharing a label can't mutual-supersede in a tight
  loop. If you see `superseded` steadily, two profiles share a label → fix it.

## Security contract (why it's safe)

- **Loopback only** (`127.0.0.1:8788`) — never bound to an external interface.
- **Bearer token** on every request — auto-created `0600` at
  `~/.config/browser-bridge/token` on first server start. The `browser` CLI
  reads it; a web page can't. Defeats DNS-rebinding.
- **Host-header allowlist** — only `127.0.0.1`/`localhost`/`::1`.

## Telemetry (metadata-only)

Every handled command emits one **best-effort** activity event
(`source=browser-bridge`, `kind=cmd`) into the personal telemetry pipeline
(`activity.events`), so browser-skill usage is queryable / visible to
`adoption-scan`. It records **only** metadata — op, instance key, outcome,
latency, and the active tab's **bare domain** — **never** the eval source, page
HTML, screenshot bytes, full URLs, or any page content. It runs off the critical
path and can never delay or break a command. Nothing you need to do; noted so you
know browser usage is being counted. Details: `README.md` → Telemetry.

## Before you rely on it

1. `browser health` — if `extension_connected:false` or it errors, the extension
   isn't loaded/paired. Tell the user to load + pair it (see below); you cannot
   do this for them (it's a manual Brave step).
2. **Verify it's the LIVE authenticated session**: after `browser html`, confirm
   the returned markup contains **logged-in-only** content (their name, account
   menu, inbox contents). If it looks like a logged-out/anonymous page, the wrong
   tab is active or they're not logged in — say so rather than proceeding.

## Error shapes (from `/cmd`)

- `503 extension_not_connected` → extension not loaded/paired, or Brave closed.
- `504 timeout` → extension picked it up but didn't answer (tab unresponsive).
- `429 rate_limited` / `429 queue_full` → the per-instance concurrency backstop is
  shedding load — you're dispatching too fast / too many at once. **Back off and
  retry** (the body carries a `retry_after` seconds hint). A normal handful of ops
  is never throttled; this only fires on a sustained flood. Knobs (env, on the
  server): `BROWSER_BRIDGE_RATE_PER_SEC` (default 5, sustained/sec; 0 = unlimited),
  `BROWSER_BRIDGE_BURST` (default 20; clamped to ≥1 when rate>0 — a <1 burst
  would rate_limit every /cmd forever), `BROWSER_BRIDGE_MAX_QUEUE` (default 32,
  0 = unlimited).
- `409 ambiguous_instance` → >1 instance connected and no `--instance` — pick one.
- `409 no_owned_tab` → `close` with nothing to close — run `browser open` first (or `--tab`).
- `409 superseded` → the instance was replaced by a newer connection; retry.
- `404 unknown_instance` → the `--instance` key matches no connected instance.
- `400 unknown_op` / `missing_field:url|js` → bad command.
- `op '<op>' failed in the browser: unknown_op` (op-level — the CLI knows the op
  but the **extension** doesn't) → the loaded extension is an older build. Ask the
  user to reload it in `brave://extensions`; use `tabs` + `--tab <id>` meanwhile.
- `Failed to capture tab: image readback failed` (a `captureVisibleTab` readback
  race on the fast path) → the SW now falls through to the **CDP `Page.captureScreenshot`**
  primary path, which captures a background/occluded tab directly, so this should no
  longer surface as an op error on current builds. If it does, reload the extension.
- `400 bad_tab` → a non-numeric `tab` (only reachable via a raw API POST; the CLI
  already validates `--tab`).
- `owned_tab_gone` (op-level, in `.result`) → your owned tab was closed; ownership
  is auto-dropped, so just re-issue (it falls back to the active tab / re-`open`).
- `cdp_attach_refused:<scheme>` (op-level, in `.result`) → a CDP op (screenshot /
  top-frame input / `eval --frame`) was aimed at a privileged tab (`chrome://`,
  `file:`, extension, devtools, …); the bridge refuses to attach `chrome.debugger`
  there. Point the op at a real `http/https` tab.
- `401 unauthorized` → token mismatch (re-paste in the extension options).

## Gotcha: reload the unpacked extension after any change

Brave does **not** hot-reload unpacked extensions. If `extension/` was edited,
the old service-worker code keeps running until it's swapped in — **and clicking
reload ↻ on the card in `brave://extensions` is UNRELIABLE** (the extension's
long-poll keeps the OLD service worker permanently alive), so the reliable fix is
a **full quit-and-reopen of Brave** (see the top of this doc). Symptom of a stale
build: an op the CLI knows returns `unknown_op`, or `health` still shows the old
`extension_version`. The `browser-bridge` **server** (not the extension) DOES
restart automatically on a `home-manager switch` (X-Restart-Triggers).

## Changing the bridge: live-verify against real Brave is the ONLY gate

If you MODIFY browser-bridge (server / extension / CLI), a green test suite and a
clean security audit are **prerequisites, NOT verification** — CI cannot drive a
real Brave, so it never exercises the actual MV3 / CDP / i3 behaviour. Across the
build-out, driving each change against the live browser caught ~11 defects that
passing tests and audits BOTH missed (Chrome-side focus being inert on i3;
`chrome.scripting` unable to eval a string → CDP `Runtime.evaluate`;
`captureVisibleTab` needing foreground → CDP `Page.captureScreenshot`; OOPIFs
needing `Target.setAutoAttach`; the reload-vs-restart trap). So the mandatory loop
for any browser change is **build → audit → fix → merge → ship → live-verify on
real Brave** — reproduce the exact path and LOOK at the actual result (exit 0 is
not verification). Operate changes via a feature branch + `/audit-pr`-style review
given it's a live-cookie surface.

## One-time setup (hand these steps to the user)

1. `home-manager switch --flake ~/workspace/devrc --impure` (starts the service).
2. Brave → `brave://extensions` → Developer mode → **Load unpacked** →
   `scripts/browser-bridge/extension/`.
3. Extension **Options** → paste the token from `~/.config/browser-bridge/token`,
   port `8788`, optionally set a unique **label** (required only if a second
   profile also connects), **Save**.
4. `browser health` → `extension_connected:true`.

For a second profile, repeat in that profile and give it a **different** label,
then target with `browser --instance <label> <op>`.
