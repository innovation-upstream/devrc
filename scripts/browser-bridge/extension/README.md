# browser-bridge extension (MV3)

A standalone Manifest V3 extension that connects the user's live Brave session to
the local `browser-bridge` server so a Claude Code skill can drive the active
tab. This is a **sibling** to the activity collector's extension — do not confuse
or merge them.

## Files

| file | role |
|------|------|
| `manifest.json`     | MV3 manifest (permissions, icons, background SW, options page) |
| `service_worker.js` | long-poll loop + chrome.* op executors (needs real Brave) |
| `protocol.js`       | pure op-set / validation / envelope / backoff + registration payload (unit-tested) |
| `options.html/js`   | one-time setup: bearer token + port + optional **label** → `chrome.storage.local` |
| `icons/icon.svg`    | gruvbox bridge/link glyph — the SVG source |
| `icons/icon-{16,32,48,128}.png` | rasterised icons wired into the manifest (regenerate with `rsvg-convert`, see `../README.md`) |

## Per-session tab targeting (open/close + injected tabId)

The op executors run against a **target tab**, not always the active one. When
the server injects a `tabId` (the calling Claude session owns a tab, or passed
`--tab`), `getHtml`/`eval`/`nav`/`screenshot`/`close` run against **that** tab
(`chrome.tabs.get(tabId)` → `chrome.scripting.executeScript({target:{tabId}})` /
`chrome.tabs.update(tabId,…)` / `chrome.tabs.remove(tabId)`). With no injected
`tabId` they fall back to the active tab (`chrome.tabs.query({active:true,
lastFocusedWindow:true})`) — the historical single-session behaviour. Two new ops
back this:

- **`open`** → `chrome.tabs.create({url: url||"about:blank", active:false})` and
  returns the real `tabId`. It creates the tab in the **background** (`active:
  false`) so parallel sessions each opening a tab don't fight over the
  foreground; the server records it as that session's owned tab.
  **Idempotent re-open:** when the server passes `reuseTabId` (the session already
  owns a tab), the SW `chrome.tabs.get(reuseTabId)`s it and returns that SAME tab
  (`{tabId, url, reused:true}`) when it's still live — so a double `open` does NOT
  create a second tab that would be orphaned/leaked. If the reuse tab is gone,
  the SW falls through and creates a fresh one.
- **`close`** → `chrome.tabs.remove(tabId)` (the server injects the owned tabId;
  the SW errors `missing_tabId` if it's absent). **Idempotent:** if the tab was
  already closed out-of-band, `chrome.tabs.remove` rejects and the SW returns
  `{closed:tabId, alreadyGone:true}` (a success) so the server cleanly drops the
  stale ownership rather than surfacing a spurious error.

**Screenshot is a VISIBLE-tab op (fundamental limitation):**
`chrome.tabs.captureVisibleTab` captures the **on-screen composited pixels of the
window's foreground tab** — it fundamentally **cannot** capture a tab that isn't
visible on-screen. The **actual foreground tab** captures fine. For a target tab
that isn't active the SW makes a **best-effort** attempt — briefly activate,
**settle** until painted, capture, then **restore** the previously-active tab (a
short flicker, never silently the wrong tab). **On i3 this commonly fails,**
though: activating a tab does NOT guarantee its Brave *window* is raised (Chrome
can't force i3 to raise a window), so an owned/background tab's window is often
off-screen, the tab never composites, and the capture keeps returning
`"image readback failed"` no matter how many times we retry — a **permanent**
condition for that tab, not the transient paint race the retry recovers.
**Use `text`/`html`/`eval` for a background tab** (incl. the `browser agent`'s OWN
background tab); those read the tab regardless of visibility.

*Background-tab settle + retry (transient recovery):* a JUST-activated tab that IS
visible but hasn't painted its first frame returns `"image readback failed"`. The
SW (1) waits for the tab to reach `status:"complete"` **plus a paint settle
(~350ms)** so the FIRST capture usually succeeds, and (2) **retries** on a
transient error (bounded — a few tries). **Retries respect Chrome's ~2/sec
`captureVisibleTab` quota:** the API is throttled to ~2 calls/sec (~500ms), so
retries are **spaced ≥~600ms** apart (a quota hit —
`MAX_CAPTURE_VISIBLE_TAB_CALLS_PER_SECOND` — waits a full ~1s window) — a faster
retry would just re-trip the quota instead of recovering.

*Exhausted-retry → clear, actionable error:* when the quota-spaced retries are
**exhausted** on a persistent readback error (the occluded-window case above), the
op maps it via `mapCaptureFailure` to a caller-actionable message
(`screenshot unavailable: the target tab is not visible on-screen … use
'text'/'html'/'eval' which work on background tabs`) instead of the opaque
`image readback failed`. The mapping runs **only post-exhaustion** (a readback a
retry could still recover is never wrongly reported "unavailable"), and the quota
error keeps its own message. *(Future opt-in, NOT implemented: a `chrome.debugger`
+ CDP `Page.captureScreenshot` path could capture an off-screen tab, but it needs
the `debugger` permission and shows a debug banner.)*

**Reload the extension** after changing this to take effect. The classifier
(`isTransientCaptureError` / `isOcclusionCaptureError`), the retry
(`captureWithRetry`), the error mapping (`mapCaptureFailure`), the settle
(`waitForCaptureReady`) and the activate→capture→restore orchestration
(`screenshotWithRestore`, restore on success AND failure) are pure + unit-tested
in `protocol.js`; `service_worker.js` supplies the chrome.* side effects.

If an owned tab was closed out-of-band, `chrome.tabs.get` throws and the op
returns an `owned_tab_gone` error envelope. On that signal the **server drops the
session's ownership immediately** (self-heal → the next op falls back to the
active tab), rather than waiting for the TTL to reclaim it.

## Multiple instances (label)

Each profile that loads this extension is one **instance**. On first run the SW
generates a stable auto-id (`crypto.randomUUID()`) and persists it in
`chrome.storage.local` (`instanceId`) — it survives reloads/restarts within that
profile. The server routes commands per instance, keyed by the **label** (set in
Options) if present, else the auto-id. **Give each profile a unique label** so
`browser --instance <label>` can target it. The SW sends its identity on every
`/poll` (via `X-Bridge-Instance-Id` / `X-Bridge-Label` headers, plus a
best-effort active-tab snapshot for `browser instances`) and echoes its
`instanceId` in each `/result`.

**Duplicate-label safety.** If two profiles end up sharing one label (a
misconfig), the server keeps only the newest and answers the displaced worker's
`/poll` with a distinct `409 superseded` (not the idle `204`). On that signal the
SW does **not** re-register instantly — it sets a `superseded` flag in
`chrome.storage.local`, logs a `console.warn` ("superseded … give each profile a
UNIQUE label"), and **backs off ~30 s** before trying again (it auto-recovers if
the other instance goes away). This deliberately breaks the mutual-supersede
**livelock** two same-label workers would otherwise spin in. The header helpers
also **cap** the active-tab url/title to 2048 chars so a pathological URL can't
overflow the server's header-line limit and fail the poll. (The pure classifier
+ cap live in `protocol.js` and ARE unit-tested; the back-off itself runs in the
SW and can only be checked in a real browser — see the checklist below.)

## Load it (and reload after every change)

1. Brave → `brave://extensions`
2. Toggle **Developer mode** (top-right).
3. **Load unpacked** → select this `extension/` directory.
4. Click the extension's **Options** (⋯ menu → Options), paste the token from
   `~/.config/browser-bridge/token`, set port `8788`, **Save**.

⚠ **Brave does not hot-reload unpacked extensions.** After editing any file here,
click the **reload** ↻ button on the extension's card in `brave://extensions`,
or the service worker keeps running the old code.

> **MANDATORY reload after the CDP change:** the manifest now requests the
> `debugger` permission (for the CDP ops — screenshot/frames/click/type/key +
> `--frame` reads). A permission change is NOT hot-applied — reload the unpacked
> extension in `brave://extensions`, and Brave may prompt you to **re-confirm the
> new `debugger` permission**. Until you do, the CDP ops fail.

## Permissions (maximal — can be scoped later)

`scripting`, `tabs`, `activeTab`, `alarms`, `storage`, `debugger`, and
`host_permissions: ["<all_urls>"]`. `<all_urls>` + `scripting` is what lets the
worker run in whatever tab is active. If you only ever drive a known set of
sites, scope `host_permissions` to those origins and reload.

### `debugger` — the CDP ops (screenshot/frames/click/type/key + `--frame`)

`chrome.debugger` is the biggest-blast-radius permission, so the CDP layer is
tightly bounded (all decision logic is pure + unit-tested in `protocol.js`):

- **Own-tab attach ONLY.** A CDP op attaches `chrome.debugger` ONLY to the
  server-injected owned/`--tab` tab, and **refuses to attach to a privileged
  surface** (`chrome://`, `chrome-extension://`, `devtools:`, `file:`) — validated
  *before* the attach (`assertCdpAttachable`). The autonomous agent's tab is forced,
  so it can never attach to another tab/profile.
- **Always detach.** Every op is attach→run→**detach** (a `finally`, so a thrown op
  still detaches); `chrome.debugger.onDetach` clears an out-of-band detach. No
  leaked attachment / stuck banner.
- **Typed commands only.** The SW maps each bounded op to a FIXED set of CDP methods;
  there is NO generic "run this CDP method" endpoint reachable by a caller/model.
- **Banner tradeoff:** Brave shows "an extension is debugging this browser" while a
  CDP op runs. Attach is per-op to keep that window tiny; simple top-frame
  `text`/`html`/`eval`/foreground-`screenshot` take the non-CDP path (no banner).

## Stable extension ID (optional)

Unpacked extensions get a per-path random ID. To pin a stable ID (e.g. so an
allowlist elsewhere can reference it), generate a keypair and add its public key
as a top-level `"key"` in `manifest.json`:

```bash
# generate a private key + derive the manifest "key" (base64 SPKI):
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out key.pem
openssl rsa -in key.pem -pubout -outform DER | base64 -w0   # → paste as manifest "key"
```

Left out of the committed manifest (MVP) — the bridge does not depend on the ID.

## Manual test checklist (what the unit tests can't cover)

The pure logic in `protocol.js` is unit-tested (`../tests/protocol.test.mjs`).
The chrome.* glue needs a real browser — verify by hand after loading:

- [ ] With the server running + token pasted, `browser health` reports
      `extension_connected:true` within ~1 min (or after a reload).
- [ ] `browser html` on a logged-in tab returns markup containing logged-in-only
      content (proves the live authenticated session).
- [ ] `browser eval 'document.title'` returns the active tab's title.
- [ ] `browser tabs` lists your open tabs.
- [ ] `browser nav https://example.com` navigates the active tab.
- [ ] `browser screenshot /tmp/shot.png` writes a real PNG of the visible tab.
- [ ] Stop the server → `browser health` fails / `extension_connected:false`
      after the stale window; restart → it reconnects on the next poll.
- [ ] Load the extension in a **second profile**, give each a unique label →
      `browser instances` lists both; `browser html` (no `--instance`) errors and
      lists them; `browser --instance <label> html` returns that profile's tab.
- [ ] **Duplicate-label back-off:** give BOTH profiles the *same* label. One
      worker should log `superseded … unique label` (DevTools → its service
      worker console) and go quiet (~30 s between attempts), NOT spin — journald
      for `browser-bridge` should show at most an occasional `supersede`, not a
      flood. Fix one label → both settle and `browser instances` lists two again.
- [ ] `browser open https://example.com` opens a NEW background tab and returns
      its `tabId`; a following `browser html` reads THAT tab (not the previously
      active one); `browser close` closes it.
- [ ] **Idempotent open (no orphan):** `browser open` twice in one session →
      the second returns the SAME `tabId` (`reused:true`), and `brave://` shows
      only ONE new tab (not two). `browser close` then closes that single tab.
- [ ] **Self-heal:** `browser open` a tab, then close it MANUALLY in Brave. The
      next `browser html` returns an `owned_tab_gone` error; the one AFTER that
      succeeds against the active tab (ownership was auto-dropped). `browser open`
      again creates a fresh owned tab.
- [ ] **Subagent escape hatch:** two concurrent drivers that share a session id
      (e.g. sibling subagents) each `browser open`, capture the `tabId`, and run
      every op with `browser --tab <id> …`. Confirm each reads/navigates only its
      OWN tab — the explicit `--tab` overrides the shared owned-tab routing.
- [ ] **Visible-tab screenshot works:** focus a normal tab, `browser screenshot
      /tmp/vis.png` writes a real PNG of that foreground tab (captureVisibleTab fast
      path — no debugger banner).
- [ ] **Background-tab screenshot now WORKS (CDP):** `browser --instance <key> open
      <url>` → `browser --instance <key> --tab <id> screenshot /tmp/bg.png`. Even on
      i3 with the owned tab's window NOT raised, CDP `Page.captureScreenshot` writes
      a real PNG (a brief "an extension is debugging this browser" banner flashes).
      `--fullpage` captures the whole scrollable document.
- [ ] **Two profiles each screenshot independently:** two Brave profiles (distinct
      labels), each `open` + `screenshot --tab <id>` its own tab → each writes its
      OWN tab's PNG even though only one profile is foreground.
- [ ] **Read INTO a cross-origin iframe:** open a page with a cross-origin iframe
      (e.g. `civitai.com` embedding `model-benchmarking.civit.ai`). `browser --tab
      <id> frames` lists the iframe; `browser --tab <id> --frame <frameId-or-url>
      text` returns the iframe's innerText (plain `text` shows only the top frame).
- [ ] **Trusted click drives an in-app control:** `browser --tab <id> [--frame <f>]
      click "<selector>"` reaches an in-app tab/button; `type`/`key Enter` fill +
      submit. Confirm the app reacts as to a human click.
- [ ] **CDP attach is refused on a privileged tab:** point `--tab` at a
      `chrome://`/extension page and run a CDP op → it fails with
      `cdp_attach_refused:<scheme>` and NEVER attaches the debugger.
- [ ] **No leaked debugger banner:** after any CDP op completes (success OR error),
      the "an extension is debugging this browser" banner disappears (always-detach).
- [ ] **Two-session isolation (the fix):** open two Claude sessions (each in its
      own tmux pane). In each, `browser open` a DIFFERENT url, then interleave
      `browser nav …` / `browser html` between the sessions. Confirm neither
      clobbers the other — each `html` returns its OWN tab's page, never the other
      session's. (`browser --print-session-id` in each shows the distinct ids.)
- [ ] The toolbar shows the bridge/link icon (manifest `action.default_icon`).
