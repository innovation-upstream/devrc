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
3. **`browser`** — the bash skill entrypoint Claude calls (`html`, `eval`,
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

## Ops

| op | maps to | returns |
|----|---------|---------|
| `getHtml`    | `chrome.scripting` → `document.documentElement.outerHTML` | `{url,title,html}` |
| `text`       | `chrome.scripting` → `(selector?document.querySelector(selector):document.body).innerText`, normalized + byte-capped (`selector`/`maxBytes` optional) | `{url,title,text,truncated}` |
| `eval`       | `chrome.scripting.executeScript` (MAIN world) of `js`     | `{url,value}` |
| `tabs`       | `chrome.tabs.query({})`                                   | `{tabs:[...],ownedTabId}` |
| `nav`        | `chrome.tabs.update(tab,{url})`                           | `{tabId,url}` |
| `screenshot` | **CDP `Page.captureScreenshot`** (png) — works on a BACKGROUND/occluded tab + each profile's own tab; a foreground tab uses the cheap `captureVisibleTab` fast path. `fullpage` grabs the whole document | `{url,dataUrl,via}` |
| `open`       | `chrome.tabs.create({url,active:false})`                 | `{tabId,url}` |
| `close`      | `chrome.tabs.remove(tabId)`                               | `{closed:tabId}` |
| `frames`     | **CDP `Page.getFrameTree`** — the tab's frames incl. cross-origin iframes | `{url,title,frames:[{frameId,url,name,parentId}]}` |
| `click`      | **CDP** `getBoundingClientRect` → `Input.dispatchMouseEvent` press+release (trusted) at the element center; `selector` required, `frame` optional | `{url,clicked,x,y,frame}` |
| `type`       | **CDP `Input.insertText`** (trusted); `text` required, `selector`/`frame` optional (focus first) | `{url,typed,frame}` |
| `key`        | **CDP `Input.dispatchKeyEvent`** (keyDown+keyUp) for one bounded key; `key` required | `{url,key,frame}` |

`open`/`close` are dispatched to the extension; `release` (drop ownership, don't
close the tab) is handled server-side and never reaches the extension.
`frames`/`click`/`type`/`key` + a `--frame` on `getHtml`/`text`/`eval` are the
**CDP (chrome.debugger) ops** (see the CDP section below). `--frame <frameId|
url-substring>` routes a read/click INTO that (possibly cross-origin) frame via a
CDP isolated-world `Runtime.evaluate`. The tab-scoped ops
(`getHtml`/`text`/`eval`/`nav`/`screenshot`/`close`/`frames`/`click`/`type`/`key`)
run against
the calling session's owned tab when it has one (see Session isolation), else the
active tab. `text` is the **cheap read**: it returns visible `innerText` (~KB)
rather than full `outerHTML` (~100s of KB) — the read the opencode browser-agent
uses. The `text` whitespace-normalization + byte-cap live in
`extension/protocol.js` (`normalizeText`, unit-tested); a `--max-bytes` cap
(default 32 KB, `0`=uncapped) truncates with a `…[truncated N bytes]` note.

Server envelope: `POST /cmd` → `200 {"ok":true,"result":{id,ok,data}}`, or a
structured error: `503 extension_not_connected`, `504 timeout`,
`409 ambiguous_instance` (>1 connected, no `target`), `409 no_owned_tab`
(`close` with nothing owned), `409 superseded`, `404 unknown_instance`,
`400 unknown_op|missing_field:<f>`, `400 bad_tab` (a non-numeric/non-scalar
`tab` from a raw caller — the CLI already validates `--tab`), `401 unauthorized`,
`403 bad_host`, `429 rate_limited|queue_full` (the per-instance concurrency
backstop — see below; body carries a `retry_after` hint).

## CDP ops (chrome.debugger): any-frame reads, trusted input, background screenshots

`frames`/`click`/`type`/`key`, `--frame` reads, and `screenshot` use the Chrome
DevTools Protocol via the extension's `debugger` permission. They fix three real
agent failures: (1) `captureVisibleTab` could only grab the foreground tab (can't
screenshot a background tab or two profiles); (2) `text`/`html`/`eval` saw only the
top frame (the target app is a cross-origin iframe); (3) there was no trusted-input
primitive to drive an app.

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
- **Screenshot is a VISIBLE-tab op (fundamental limitation, honest).**
  `captureVisibleTab` captures the **on-screen composited pixels of a window's
  foreground tab** — it fundamentally **cannot** capture a tab that isn't visible
  on-screen. Screenshotting the **actual foreground tab** works fine. For a
  session's **background/owned** tab the bridge makes a **best-effort** attempt —
  briefly activate → **settle until painted** → capture (with retry) → restore the
  previously-active tab (a short flicker) — so it never silently captures the wrong
  tab. **But on the user's i3 tiling WM this commonly fails:** activating a tab does
  NOT guarantee its Brave *window* is raised/composited (Chrome can't force i3 to
  raise a window), so an owned/agent tab's window is often off-screen, the tab never
  composites, and the capture keeps returning `"image readback failed"` no matter
  how many times we retry. This is a **permanent condition for that tab, not the
  transient paint race** the retry recovers.
  - **Settle + retry (transient recovery):** a just-activated tab that IS visible
    but momentarily unpainted returns `"image readback failed"` on the first try;
    the SW waits for `status:"complete"` + a paint settle (~350ms) so the FIRST
    capture usually wins, then **retries** on a transient error. Retries **respect
    Chrome's ~2/sec `captureVisibleTab` quota** (spaced ≥~600ms; a
    `MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND` hit waits a ~1s window) — a faster
    retry would re-trip the quota instead of recovering.
  - **Exhausted-retry → clear, actionable error (not the opaque chrome string).**
    When the quota-spaced retries are **exhausted** on a persistent readback error
    (the occluded-window case above), the op maps it to a caller-actionable message
    — `screenshot unavailable: the target tab is not visible on-screen (background
    or occluded window). captureVisibleTab can only capture the foreground tab;
    bring the tab to the foreground, or use 'text'/'html'/'eval' which work on
    background tabs.` — instead of `image readback failed`. It is returned as the op
    error (non-zero exit via the normal envelope). This is distinct from the
    transient case: a readback a retry could still recover is NOT reported as
    "unavailable" (the mapping runs only post-exhaustion), and the **quota error
    keeps its own message** (it is a throttle, not an occlusion).
  - **Use `text` / `html` / `eval` for a background tab** — they read the tab
    directly regardless of whether it is on-screen, so an agent/owned tab (e.g. the
    `browser agent`'s OWN background tab) should read, not screenshot.
  - *Future opt-in (NOT implemented):* a `chrome.debugger` + CDP
    `Page.captureScreenshot` path COULD capture an off-screen tab, but it needs the
    `debugger` permission and shows a debug banner — deliberately out of scope.
  - The classify + retry + settle + activate→capture→restore logic **and the
    exhausted-retry error mapping** are pure and unit-tested in
    `extension/protocol.js` (`isTransientCaptureError` / `captureWithRetry` /
    `waitForCaptureReady` / `screenshotWithRestore` / `isOcclusionCaptureError` /
    `mapCaptureFailure`); the capture itself needs real Brave, so it stays on the
    manual checklist. Like any `extension/` change, it only takes effect after a
    manual extension **reload** in `brave://extensions`.
- **Backward-compat.** A single session with no `open` (and even no
  `X-Session-Id`) behaves exactly as before: active-tab ops, no `tabId` injected.
  The multi-instance `--instance` targeting, ambiguity/supersede semantics, and
  telemetry are unchanged.

`GET /health` → `{"ok":true,"extension_connected":bool,"count":N,"instances":[{key,label,instanceId,activeTab},…]}`.
`GET /instances` → `{"ok":true,"count":N,"instances":[…]}`.

## Telemetry (activity pipeline)

Each **handled command** (`getHtml`/`text`/`eval`/`tabs`/`nav`/`screenshot`/
`frames`/`click`/`type`/`key`, incl. its error/ambiguous outcomes) emits **one**
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
  any page content. For the CDP ops this specifically means **no frame URLs**
  (only the bare top-level domain), **no typed text** (`type`), and no click
  selector — the domain is derived only from the result's top-level `url`.
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
`click`,`type`,`key`} plus optional `selector`/`url`/`js`/`text`/`key`/`frame`/
`maxBytes` — **never a shell command string, and never a raw-CDP `cdp`/`method`
field** (the CDP ops are bounded typed ops only; see the CDP security model above).

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
- **Runtime fail-closed tool-set gate (this is what makes an un-upgraded /
  other opencode version SAFE).** The denial above is a *property of the resolved
  config*, and different opencode versions resolve it differently (workbench is
  1.17.20, laptop 1.18.4). So BEFORE opening a tab or spending a single model
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
  *opencode version requirement:* an opencode whose `debug agent` reports a
  browser-only tool set (verified on 1.18.4; the resolved `tools` map above). If a
  future/other version can't be confirmed browser-only, upgrade — the gate will
  refuse until it can.
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
object (defensive across the laptop 1.18.4 / workbench 1.17.20 version skew).

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

**⚠ opencode version skew (workbench 1.17.20 vs laptop 1.18.4).** The custom-tool
mechanism (`.opencode/tools/*.js`, `permission: {"*": deny, …}`) is **verified on
1.18.4** (laptop) via `opencode debug agent browser-agent` (resolved tools show
`bash:false … browser:true`) and an end-to-end `opencode debug agent … --tool
browser` run against a fake bridge. It is **NOT verified on 1.17.20** (workbench) —
custom-tool support and the tool-dir name may differ. Mitigations: the wrapper
writes the tool to BOTH `.opencode/tools/` and `.opencode/tool/`; but if 1.17.20
does not support project custom tools at all, `browser agent` will not work there.
**Recommend upgrading workbench to ≥1.18.4** (`opencode upgrade`) before relying on
`browser agent` on that host, and re-running the `opencode debug agent` check.

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
( cd "$S" && opencode debug agent browser-agent ) | python3 -c \
  'import json,sys; t=json.load(sys.stdin)["tools"]; assert t["bash"] is False and t["browser"] is True; print("OK: bash denied, only browser enabled")'
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

Current totals: **157 Python** (`test_server.py` 115 + `test_browser_agent_parse.py`
14 + `test_browser_agent.py` 28) + **121 node** (`protocol.test.mjs` 59 +
`browser_tool.test.mjs` 33 + `cdp_protocol.test.mjs` 29) — the `protocol.test.mjs` cases cover the screenshot
settle+retry decision logic (`isTransientCaptureError` / `captureWithRetry` /
`waitForCaptureReady` / `screenshotWithRestore`) **and the exhausted-retry error
mapping** (`isOcclusionCaptureError` / `mapCaptureFailure`): a persistent readback
(retries exhausted) → the actionable "not visible on-screen" message, a transient
readback that a spaced retry recovers still succeeds (no premature "unavailable"),
and the #182 quota error keeps its own message. The live browser-driving loop (real DeepSeek + real
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
