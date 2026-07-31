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

The `captureVisibleTab` fast path is only used for a tab that is ALREADY the visible
foreground tab (and not `--fullpage`); **the primary screenshot path is now CDP
`Page.captureScreenshot`** (via the `debugger` permission), which captures a
BACKGROUND / occluded / non-foreground tab directly — so the old i3 "not visible
on-screen" occlusion case no longer applies to the normal path, and the earlier
settle/activate-restore/occlusion-mapping helpers were retired along with it. Any
`captureVisibleTab` failure simply falls through to the CDP path.

**Reload the extension** after changing this to take effect. The transient-error
classifier (`isTransientCaptureError` / `isCaptureQuotaError`) and the quota-spaced
retry (`captureWithRetry`) are pure + unit-tested in `protocol.js`; `service_worker.js`
supplies the chrome.* side effects (both the `captureVisibleTab` fast path and the CDP
`Page.captureScreenshot` primary path).

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

## 🔴 Load it from the DEPLOYED path, not this repo directory

**Brave must load `~/.local/share/browser-bridge-ext/`, NOT this directory.**

`home-manager switch` writes a real copy of this tree to
`~/.local/share/browser-bridge-ext/` (`home.activation.browserBridgeExtension` in
`nix/home.nix` — `cp -rL` into a sibling temp dir then a single `mv -T`,
deliberately not store symlinks). devrc is worked on by many concurrent sessions,
and loading the extension out of the working tree means any other session's `git
checkout`, `stash`, branch switch or worktree operation silently swaps the
extension's code out from under a live verification. That is not hypothetical —
it reverted a staged build mid-session on 2026-07-30.

⚠ **Honest scope — this is not "nothing can change it".** A `home-manager
switch` (or `ship.sh`) rewrites the deployed tree from whatever the working tree
holds at that moment, so a concurrent session sitting on another branch can still
swap the extension mid-verification. What the deploy removes is the **silent**
class (a bare checkout with no switch). `browser ping` is what makes the
remaining case detectable.

⚠ **Flake trap: a NEW file here must be `git add`ed before switching.** Flakes
only see git-tracked files, so an untracked new extension file is silently
omitted from the deployed tree — a partially-updated extension with **no error
anywhere**. (Same trap as `claude/commands/`, documented in the repo CLAUDE.md.)

This directory stays the **source** (edit here, `git add` if new,
`home-manager switch`, then reload in Brave). Nothing removes it; a profile still
pointed here keeps working, it is just not git-safe.

### First-time load / re-point (per Brave profile — MANUAL, one-time)

Do this **once for each profile** (work and personal). It cannot be automated:
`brave://extensions` is not scriptable, and Brave must not be killed
(`restore_on_startup` is unset on both profiles — the operator's tabs would not
come back).

1. `home-manager switch --flake ~/workspace/devrc --impure` — creates/refreshes
   `~/.local/share/browser-bridge-ext/`. Confirm:
   `ls ~/.local/share/browser-bridge-ext/manifest.json`
2. In the profile's window: Brave → `brave://extensions`
3. Toggle **Developer mode** (top-right).
4. Find the **Browser Bridge (command channel)** card. **Before touching it,
   write down the `ID` shown on that card** together with its **path** — this is
   the only "before" reading you can take, and it is what makes the path→id
   claim falsifiable in step 9. (`ping` cannot give it to you: the loaded build
   is 0.2.0, which has no `ping` op and answers `unknown_op`.) Then, if the path
   is under `~/workspace/devrc/…`, click **Remove** (this drops that profile's
   `chrome.storage.local` — token/port/label/`instanceId` — hence step 7).
5. **Load unpacked** → select `~/.local/share/browser-bridge-ext/`.
   (`Ctrl+L` in the GTK file chooser lets you type the path.)
6. Confirm any permission re-prompt (`debugger`, `webNavigation`).
7. Extension card → ⋯ → **Options**: paste the token from
   `~/.config/browser-bridge/token`, port `8788`, set the profile's **label**
   (`work` / `personal` — must be unique per profile), **Save**.
8. Verify from a shell — this is the whole point of the change:
   ```bash
   browser --instance <label> ping   # → {"pong":true,"extensionVersion":"0.4.0",
                                     #     "id":"<ext-id>","ops":[…,"ping"]}
   browser whoami                    # → that instance: extension_stale:false
                                     #    + extension_id, and
                                     #    bridge.extension_dir_expected
   ```
   `unknown_op` from `ping` means the OLD build is still loaded — go to the
   reload section below.
9. **Record this profile's `id`** (from `ping`, or `extension_id` in `whoami`)
   somewhere you keep notes, and **compare it against the id you wrote down in
   step 4** — they should DIFFER, because the load path changed. That comparison
   is the only test of the path→id premise this whole migration rests on; if the
   id is unchanged, say so and see the checklist item near the end of this file.
   Thereafter a *changed* id means the profile got re-pointed at a different
   directory — the one thing the version fields cannot tell you. ⚠ The path→id
   derivation is INFERRED from documented Chromium behaviour, not measured here;
   the recorded id is a baseline to compare against, not a computed expectation.
   (Nothing computes an expected id, on purpose: a wrong derivation would raise
   false alarms.)

Repeat 2–9 in the other profile's window. The profiles are independent: one can
be on the new path while the other is still on the repo path.

⚠ **Open question, cheap to settle while you are here:** do the two profiles,
once BOTH point at `~/.local/share/browser-bridge-ext/`, report the SAME id or
different ones? Chromium is documented to derive an unpacked extension's id from
a hash of the **absolute path only**, which would make them identical — but that
is INFERRED on both sides and nothing here has measured it. It does not affect
the step-9 comparison (that is before-vs-after on ONE profile), but please note
which you observe and correct this paragraph.

### Rollback (if the deployed directory will not load)

The repo copy is never removed, so rollback is the same flow pointed the other
way:

1. `brave://extensions` → **Remove** the `~/.local/share/browser-bridge-ext/` card.
2. **Load unpacked** → `~/workspace/devrc/scripts/browser-bridge/extension/`.
3. ⋯ → **Options**: re-paste the token, port `8788`, and the profile's label.
4. `browser --instance <label> ping` to confirm it answers.

⚠ **Rollback is not free.** Remove wipes that profile's
`chrome.storage.local` — token, port, label and the persisted `instanceId` all
go, which is why step 3 is mandatory and why the profile comes back with a NEW
auto-id. Per profile. You are also back on the git-mutable path.

## Reload after every change (and how to know it took)

⚠ **Brave does not hot-reload unpacked extensions.** After editing any file here
(and `home-manager switch`, which refreshes the deployed copy), click the
**reload** ↻ button on the extension's card in `brave://extensions`, or the
service worker keeps running the old code.

⚠ **↻ is UNRELIABLE and silently so**: the extension's long-poll keeps the OLD
MV3 service worker alive, so a reload often no-ops. Never assume it took —
**probe it**:

```bash
browser --instance <label> ping
  # new build → {"pong":true,"extensionVersion":"<manifest version>","id":"…",…}
  # old build → op 'ping' returned unknown_op …                  (non-zero exit)
# --instance matters: with two profiles connected, a bare call gets
# 409 ambiguous_instance rather than an answer.
```

If `ping` still reports the old version after ↻, **fully quit and reopen Brave**
(never `pkill` it — tabs are not restorable).

> **CONTRACT for an extension change that must be provably loaded:** bump
> `manifest.json`'s `version` AND add a new discriminator the old build cannot
> fake — a new op name, or a new field in `ping`'s reply. `ping` itself exists
> because "is the new build loaded?" was previously unfalsifiable, which cost
> three full Brave restarts in a single session. (0.3.0 → 0.3.1 was exactly
> this: adding `id` to `ping`'s reply is a discriminator, so it got a bump.)

> **MANDATORY reload after a permission change:** the manifest requests the
> `debugger` permission (screenshot + TOP-frame trusted input) AND the
> `webNavigation` permission (OOPIF-capable `frames` enumeration). A permission
> change is NOT hot-applied — reload the unpacked extension in `brave://extensions`,
> and Brave may prompt you to **re-confirm the new permissions**. Until you do, the
> affected ops fail.

## Permissions (maximal — can be scoped later)

`scripting`, `tabs`, `activeTab`, `alarms`, `storage`, `debugger`,
`webNavigation`, and `host_permissions: ["<all_urls>"]`. `<all_urls>` + `scripting`
is what lets the worker run in whatever tab is active — and, crucially, inject INTO a
cross-origin out-of-process iframe (OOPIF). If you only ever drive a known set of
sites, scope `host_permissions` to those origins and reload.

### `webNavigation` + `scripting` — OOPIF-capable frames + `--frame` reads/input

`frames` enumerates via **`chrome.webNavigation.getAllFrames`** and `--frame`
reads/input inject via **`chrome.scripting.executeScript({target:{frameIds:[id]}})`**.
This is the fix for the cross-origin-iframe gap: CDP `Page.getFrameTree` from the top
tab target only sees SAME-PROCESS frames, so a cross-origin OOPIF (its own renderer
under site isolation) was invisible — `frames` couldn't list it and `--frame` couldn't
target it. `getAllFrames` enumerates OOPIFs; `scripting` injects into them (given
`<all_urls>`), NO debugger banner. The frame identifier is the **numeric webNavigation
`frameId`** (or a URL substring). In-frame input events are **SYNTHETIC**
(`isTrusted:false`) — the reachable OOPIF path, and enough to drive most apps; TOP-frame
input stays CDP-**trusted**.

**Exception — `eval --frame` uses CDP, not `scripting`.** `chrome.scripting` runs a
serialized FUNCTION; the fixed-func frame ops (`text`/`html`/`click`/`type`/`key`) work
that way, but `eval` is an arbitrary JS STRING, and `new Function(src)`-ing it inside
the frame's isolated world hits the extension CSP / returns `value:null`-as-success — it
never truly evaluates. So `eval --frame` runs via CDP `Runtime.evaluate` in the frame's
execution context (same-process → `Page.createIsolatedWorld`; cross-origin OOPIF →
`Target.setAutoAttach({flatten:true})` flat session, matched by URL), returning the real
value and surfacing exceptions as `frame_eval_failed` — never a silent null. See the
`debugger` section below.

### `debugger` — screenshots + `eval --frame` + TOP-frame trusted input

`chrome.debugger` is the biggest-blast-radius permission, so the CDP layer is
tightly bounded (all decision logic is pure + unit-tested in `protocol.js`). It is used
for `screenshot` (works on a background/occluded tab), **`eval --frame`** (run a JS
string in a specific same-process or cross-origin OOPIF frame — see the exception
above), and TOP-frame trusted `click`/`type`/`key` (no `--frame`):

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
  CDP op runs. Attach is per-op to keep that window tiny; `text`/`html` (top-frame AND
  `--frame`), top-frame `eval`, `frames`, `--frame` input, and foreground-`screenshot`
  all take the non-CDP path (no banner). `eval --frame` DOES attach (it needs
  `Runtime.evaluate` to reach the frame), so it briefly shows the banner — bounded by
  the per-op CDP timeouts and always-detach, like the other CDP ops.

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
- [ ] **The load path is the git-immune one:** the extension card in
      `brave://extensions` shows a path under `~/.local/share/browser-bridge-ext/`,
      NOT under `~/workspace/devrc/`.
- [ ] **`browser --instance <label> ping` answers with the DEPLOYED manifest
      version** (matching `~/.local/share/browser-bridge-ext/manifest.json`), and
      `browser health` / `browser whoami` show `extension_stale:false` for that
      instance. A build older than the `ping` op returns `unknown_op` + a
      non-zero exit instead — the intended "the reload did NOT take" answer.
- [ ] **The `id` changes when the load path changes** (the one claim this whole
      migration rests on, and it is INFERRED, not measured).
      ⚠ **You cannot get the "before" value from `ping`** — the build currently
      loaded from the repo path is 0.2.0, which has no `ping` op and answers
      `unknown_op`. Read the **ID shown on the extension's card in
      `brave://extensions`** instead (enable Developer mode; the card shows both
      the ID and the load path). Capture it **before** you re-point, or the
      comparison becomes impossible. Then re-point to
      `~/.local/share/browser-bridge-ext/` and compare against `ping`'s `id`
      (or that card's ID again). If it is UNCHANGED, `id` does not track the
      directory and the write-ups in this file, `../README.md`,
      `../reference/errors.md` and the repo `CLAUDE.md` must be corrected.
- [ ] **Same path, two profiles — same id or not?** Once BOTH profiles point at
      `~/.local/share/browser-bridge-ext/`, compare their ids. Chromium is
      documented to hash the absolute PATH only (→ they should MATCH), but that
      is inferred, not measured, and this pass gets the answer for free. Record
      what you see and fix the paragraph after step 9 in this file.
- [ ] **`ping` is inert:** running it does not change the focused tab, the
      focused window, or any page (it touches no tab at all).
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
- [ ] **Read INTO a CROSS-ORIGIN (OOPIF) iframe:** open a page embedding a
      cross-origin iframe (e.g. `civitai.com/apps/run/model-benchmarking` embedding
      `model-benchmarking.civit.ai`). `browser --tab <id> frames` MUST now LIST the
      cross-origin `model-benchmarking.civit.ai` frame (the whole point — CDP
      getFrameTree missed it); `browser --tab <id> --frame <numericId-or-url> text`
      returns THAT frame's innerText (plain `text` shows only the top frame).
- [ ] **`eval --frame` actually evaluates INSIDE the frame (the #190 fix):** on the same
      OOPIF page, `browser --tab <id> frames` → note the cross-origin frame's numeric id.
      `browser --tab <id> --frame <oopif-id> eval 'location.href'` returns
      `https://model-benchmarking.civit.ai/...` (PROOF eval ran inside the OOPIF) — NOT
      `value:null`. `--frame 0 eval 'location.href'` returns the TOP url. A bad frame
      (`--frame nope eval '1'`) returns a clear `frame_not_found` error, and a throwing
      expression (`--frame <id> eval 'x.y.z'`) returns `frame_eval_failed:<reason>` —
      neither is a silent null. No instance wedge afterward (`browser health` still OK).
      (A brief debugger banner flashes for `eval --frame` — it's a CDP op now.)
- [ ] **Drive an in-app control INSIDE the cross-origin iframe:** `browser --tab <id>
      --frame <f> click "<selector>"` reaches a control inside the OOPIF; `--frame <f>
      type`/`--frame <f> key Enter` fill + submit. Input is SYNTHETIC in-frame
      (isTrusted:false) — confirm the app reacts. Top-frame (no `--frame`) input stays
      CDP-trusted.
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
