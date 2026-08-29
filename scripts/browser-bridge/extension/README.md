# browser-bridge extension (MV3)

A standalone Manifest V3 extension that connects the user's live Brave session to
the local `browser-bridge` server so a Claude Code skill can drive the active
tab. This is a **sibling** to the activity collector's extension — do not confuse
or merge them.

## Files

| file | role |
|------|------|
| `manifest.json`     | MV3 manifest (permissions, icons, background SW, options page). Its `version` is **partly generated** — see *Versioning* below |
| `service_worker.js` | long-poll loop + chrome.* op executors (needs real Brave) |
| `protocol.js`       | pure op-set / validation / envelope / backoff + registration payload (unit-tested) |
| `build_id.js`       | **GENERATED** — the `BUILD_MARKER` literal that travels with the CODE (#324). Regenerate with `python3 scripts/browser-bridge/gen-build-marker.py`; CI fails if it is stale |
| `options.html/js`   | one-time setup: bearer token + port + optional **label** → `chrome.storage.local` |
| `icons/icon.svg`    | gruvbox bridge/link glyph — the SVG source |
| `icons/icon-{16,32,48,128}.png` | rasterised icons wired into the manifest (regenerate with `rsvg-convert`, see `../README.md`) |

## Versioning — `<release>.<build>`

`manifest.json`'s version has two halves, owned by different parties:

    0.8.1.43738
    └─┬─┘ └─┬─┘
      │     └── BUILD component — GENERATED, = first 4 hex chars of BUILD_MARKER
      └──────── RELEASE base — yours, bump by hand, needs a changelog block below

**Why the build half exists.** The build marker is the fail-closed staleness
authority, but it is invisible in `brave://extensions`, where a human only ever
sees a version. Two profiles once both read `0.8.1` while running different
code: the version was hand-bumped, so it did not move between builds and could
not separate them. The build component is derived from the marker, so it moves
whenever the code moves — a stale profile is now legible **at a glance, without
running anything**.

    $ python3 scripts/browser-bridge/gen-build-marker.py         # regenerate both
    $ python3 scripts/browser-bridge/gen-build-marker.py --check  # verify both

Four hex chars, not five: Chrome caps each dotted component at 65535 and
`0xFFFF` is exactly 65535. A wider component yields a manifest Chrome **refuses
to load**, which presents exactly like a dead bridge.

🔴 **An ALL-CLEAR (`extension_stale: false`) comes from the MARKER alone.** The
verdict is asymmetric and this half is the one that matters: no version-shaped
signal can ever produce `false`, because the version describes the manifest that
was LOADED, not the code that is running. A `true` *can* additionally come from
a version disagreement (`server.py:883-884`, `:887-891`) — so "it compares the
marker and nothing else" would be wrong, and an earlier draft of this section
said exactly that. When the two disagree, believe the marker.

🔴 **The marker deliberately does NOT hash the version value.** It hashes every
other byte of the manifest, but the version is derived FROM the marker, so
hashing it would make the derivation a recurrence with no fixpoint.

Consequence worth knowing, stated precisely because the loose version of it
misleads: a version-only edit does not move the marker, so hand-editing the
version cannot fake a new build. But **`--check` only validates the BUILD
component** — it derives its expectation from the very manifest it is checking,
so it re-reads a forged release base as the base and passes. Editing
`0.8.1.43738` to `9.9.9.43738` leaves `--check` printing OK on both lines. What
catches a forged base is `test_manifest_version_matches_the_declared_build`
under pytest, against the changelog below. **Do not treat `--check` as the
anti-forgery gate before a local switch; it is blind to that.**

Bumping the **release** base (`0.8.1` → `0.9.0`) still requires a
`> **0.9.0 — …**` changelog block below, gated by
`test_manifest_version_matches_the_declared_build`. The build component needs no
block — that gate compares the base only.

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

🔴 **`instanceId` is NOT a did-the-reload-take signal.** MEASURED 2026-08-04 on
BOTH hosts and all FOUR profiles: every profile kept its `instanceId` verbatim
through a per-profile **Remove + Load unpacked** that demonstrably swapped the
executing code — the build marker moved in every one (three profiles
`73f5438f18f395d2` → `04bbd6f9c695141d`; the workbench's `personal - other`
`null` → `04bbd6f9c695141d`, its manifest version moving 0.7.1 → 0.8.1 too).
An earlier claim that the id "changes only on Remove + Load unpacked" is
therefore **false**, and reaching for it to check whether a reload took gives a
confident wrong answer — precisely the failure class the build marker (#324)
exists to close. **The only signal that answers that question is the build
marker** (`buildMarker` from `ping`; `extension_build` vs
`extension_build_current`, surfaced as the `extension_stale` verdict).

⚠ **Scope:** that measurement establishes what `instanceId` does NOT tell you.
What actually *does* regenerate it is **unestablished** — a fresh profile and an
explicitly cleared `chrome.storage.local` were not tested. Don't invent one.

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
anywhere**. (Same trap as `claude/skills/`, documented in the repo CLAUDE.md.)

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
   the only "before" reading you can take, and **Remove wipes it**. (`ping`
   cannot give it to you: the loaded build is 0.2.0, which has no `ping` op and
   answers `unknown_op`.) You can also **compute what it should be** from the
   path — see [The path→id derivation](#the-pathid-derivation-measured) below —
   so this reading is now a confirmation, not the only source of truth. Then, if
   the path is under `~/workspace/devrc/…`, click **Remove** (you will have to
   re-enter that profile's token/port/label — hence step 7). Do **not** expect
   the `instanceId` to change: it was measured on 2026-08-04 to survive a
   Remove + Load unpacked on all four profiles, so it is no evidence the re-add
   happened. Read the `buildMarker` for that (step 8).
5. **Load unpacked** → select `~/.local/share/browser-bridge-ext/`.
   (`Ctrl+L` in the GTK file chooser lets you type the path.)
6. Confirm any permission re-prompt (`debugger`, `webNavigation`).
7. Extension card → ⋯ → **Options**: paste the token from
   `~/.config/browser-bridge/token`, port `8788`, set the profile's **label**
   (`work` / `personal` — must be unique per profile), **Save**.
8. Verify from a shell — this is the whole point of the change:
   ```bash
    browser --instance <label> ping   # → {"pong":true,"extensionVersion":"0.7.1",
                                      #     "id":"<ext-id>","ops":[…,"ping","context"]}
   browser whoami                    # → that instance: extension_stale:false
                                     #    + extension_id, and
                                     #    bridge.extension_dir_expected
   ```
   `unknown_op` from `ping` means the OLD build is still loaded — go to the
   reload section below.
9. **Record this profile's `id`** (from `ping`, or `extension_id` in `whoami`)
   and **compare it against both** the id you wrote down in step 4 (it must
   DIFFER — the load path changed) **and the id computed from the new path**
   (it must MATCH). For `~/.local/share/browser-bridge-ext` that computed value
   is `bgbkamdlkdleahpgdgmjipjbgmepgenk`; for the repo path
   `~/workspace/devrc/scripts/browser-bridge/extension` it is
   `pkkoninbaeicfalpdkkmcknhnacjjjpi`. Thereafter a *changed* id means the
   profile got re-pointed at a different directory — the one thing the version
   fields cannot tell you. (The server still does not compute an expected id;
   that is a deliberate open follow-up, not an oversight — see below.)

Repeat 2–9 in the other profile's window. The profiles are independent: one can
be on the new path while the other is still on the repo path.

#### The path→id derivation (MEASURED)

An unpacked extension's `chrome.runtime.id` is derived from the **absolute
directory path only** — no profile component. The derivation is
`sha256(path)` → first 32 hex chars → each nibble `0-f` mapped to `a-p`:

```python
h = hashlib.sha256(path.encode()).hexdigest()[:32]
ext_id = "".join(chr(ord("a") + int(c, 16)) for c in h)
```

Measured 2026-08-01, three independent ways:

1. **Reproduced by computation.** With the extension loaded from
   `/home/zach/workspace/devrc/scripts/browser-bridge/extension`, the laptop
   reported `pkkoninbaeicfalpdkkmcknhnacjjjpi`; the formula reproduces that
   string exactly.
2. **Predicted, then confirmed.** Before re-pointing, the formula predicted
   `bgbkamdlkdleahpgdgmjipjbgmepgenk` for
   `/home/zach/.local/share/browser-bridge-ext`. After the operator re-pointed,
   `ping` returned exactly that.
3. **Path only, no profile component.** Both laptop profiles on the repo path
   reported the SAME id, and after migrating both reported the same
   deployed-path id. The **workbench** — a different host, same absolute repo
   path — reports `pkkoninbaeicfalpdkkmcknhnacjjjpi` too.

⚠ **Scope of the measurement:** Brave/Chromium on these two NixOS hosts, for
**unpacked** extensions, at two paths. Nothing here was measured for packed
extensions or for any other browser — do not assume it carries over.

**Operational consequence:** the id is now **predictable in advance** from the
target path, so you can know what it *should* be rather than only comparing
before-vs-after. The "before" reading off the `brave://extensions` card is still
worth taking **before you click Remove** (Remove wipes `chrome.storage.local`),
but it is no longer the only thing standing between you and an unfalsifiable
migration.

### Rollback (if the deployed directory will not load)

The repo copy is never removed, so rollback is the same flow pointed the other
way:

1. `brave://extensions` → **Remove** the `~/.local/share/browser-bridge-ext/` card.
2. **Load unpacked** → `~/workspace/devrc/scripts/browser-bridge/extension/`.
3. ⋯ → **Options**: re-paste the token, port `8788`, and the profile's label.
4. `browser --instance <label> ping` to confirm it answers.

⚠ **Rollback is not free.** Remove costs that profile its token, port and label,
which is why step 3 is mandatory. Per profile. You are also back on the
git-mutable path. What it does **not** cost is the `instanceId` — measured
2026-08-04, four of four profiles came back with the SAME auto-id after a
Remove + Load unpacked, so an unchanged id is **not** evidence the rollback
failed to take. Use `ping`'s `buildMarker` (step 4) for that.

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
  # new build → {"pong":true,"extensionVersion":"…","buildMarker":"…","id":"…",…}
  # old build → op 'ping' returned unknown_op …                  (non-zero exit)
# --instance matters: with two profiles connected, a bare call gets
# 409 ambiguous_instance rather than an answer.
```

🔴 **Read `buildMarker`, not `extensionVersion` — and check EVERY profile.**
`extensionVersion` is `chrome.runtime.getManifest().version`, read off the
on-disk manifest at call time, and `id` is derived from the load PATH. Both
therefore describe the **directory**, not the code that is executing.

> **MEASURED 2026-08-04 (laptop, #324).** Two Brave profiles loading the SAME
> directory reported an identical `extension_id`, an identical `0.7.3`, and
> `extension_stale: false` — while one was executing `main` and the other an
> unmerged 0.7.2 build whose source was present **on no disk** (`grep -ra` over
> the whole deployed tree and the whole repo found nothing). Three further facts
> from that session, all re-measured at the time:
>
> * **A full browser restart is NOT sufficient.** Brave was genuinely fresh —
>   oldest browser process 392 s old, deploy 9 h earlier — and still ran the old
>   code. Restarting the `browser-bridge` service did not help either.
> * **Per-profile Remove + Load unpacked at `brave://extensions` IS what
>   reloads it.** After doing it in both profiles, both ran `main`.
> * **Staleness is PER PROFILE.** One profile was current and the other was not,
>   at the same instant, on one host, from one directory. Checking one profile
>   tells you nothing about the other.
>
> The mechanism (how a freshly-started browser executes a service worker whose
> source is on no disk) is still **UNEXPLAINED**. The operational facts above
> are measured; do not let a plausible story about MV3 worker caching harden
> into a finding.

If `ping` still reports the old `buildMarker` after ↻, do the **per-profile
Remove + Load unpacked** — a full Brave quit/reopen has been measured NOT to be
enough (never `pkill` Brave — tabs are not restorable).

> **CONTRACT for an extension change that must be provably loaded:** bump
> `manifest.json`'s `version`, add a `> **X.Y.Z — …**` changelog block below
> (gated), and **regenerate the build marker**:
>
> ```bash
> python3 scripts/browser-bridge/gen-build-marker.py    # rewrites extension/build_id.js
> ```
>
> The **build marker is now the mechanical discriminator** and it needs no
> per-change cleverness: `BUILD_MARKER` is a generated literal in `build_id.js`
> that `service_worker.js` **imports**, so it is frozen into the loaded module
> graph and travels with the CODE. A stale worker reports the stale marker by
> construction, and the server compares it against the marker in the deployed
> source (`bridge.extension_build_current`) to produce `extension_stale` — which
> **fails closed**: either side missing a marker yields `null`, never `false`.
> `false` now means *verified current*.
>
> A behavioural discriminator (a new op name, a new `ping` field, a specific
> string the old build cannot emit) is still worth adding when one is natural —
> it exercises the changed line rather than a reported value, and it is the only
> check that survives a spoofed or mis-generated marker. But it is no longer the
> only thing standing between you and a false all-clear. (`ping` itself exists
> because "is the new build loaded?" was once unfalsifiable, which cost three
> full Brave restarts in a single session.)

> **0.8.1 — `emulate --reset` actually UNDOES the viewport (#319).** The reset
> branch was a Map delete that sent nothing; it now attaches one CDP session and
> sends `Emulation.setDeviceMetricsOverride{width:0,height:0,deviceScaleFactor:0,
> mobile:false}` followed by `Emulation.clearDeviceMetricsOverride`, and reports
> them as `cleared` + `restored`. **Both steps are required**: measured
> 2026-08-04 in a throwaway Brave 147.0.7727.56 under Xvfb over raw CDP, a bare
> `clearDeviceMetricsOverride` sent from a session that did not itself set an
> override is a **no-op that reports success** — which is why PR #320 changed
> nothing. Arming the session first makes the clear resize the widget back.
> That also explains the dpr-vs-width asymmetry #319 could not: dpr/touch/UA/
> media/timezone are renderer-side session state that dies at detach, while the
> size additionally resizes the browser-side render widget.
>
> New response fields are the discriminator — an older build cannot emit them:
>
> ```bash
> browser open https://example.com && browser emulate iphone-15
> browser emulate --reset          # read `.cleared` / `.restored`
>   # ≤0.8.0 → {"reset":true,"wasEmulating":{…},"note":"…NOTHING WAS SENT…"}   (no `cleared`)
>   # 0.8.1  → {"reset":true,"restored":true,
>   #           "cleared":["Emulation.setDeviceMetricsOverride",
>   #                      "Emulation.clearDeviceMetricsOverride"], …}
> browser js --wake 'innerWidth'   # ← the REAL check: back to the desktop width
> ```
>
> `--reset --recreate` is unchanged and still needed: it needs no CDP, and it is
> the only remedy for an un-upgraded build or a tab orphaned by a `SIGKILL`'d
> agent. ⚠ Verified in a scratch Brave and in unit tests against a browser model
> calibrated to the measurements above — **not** against the operator's live
> profile.

> **0.8.0 — the BUILD MARKER: `ping` gains `buildMarker`, and `extension_stale`
> is computed from it instead of from the version (#324).** New generated file
> `build_id.js` exporting a `BUILD_MARKER` literal, imported by
> `service_worker.js` and sent on every `/poll` as `X-Bridge-Ext-Build`. The
> discriminator is the new `ping` field itself — an older build cannot fake it:
>
> ```bash
> browser --instance <label> ping
>   # ≤0.7.3 → {"pong":true,"extensionVersion":"0.7.3","id":"…","ops":[…]}   (no buildMarker)
>   # 0.8.0  → {"pong":true,"extensionVersion":"0.8.0","buildMarker":"<hex>","id":"…","ops":[…]}
> browser whoami   # each instance: extension_build + extension_stale
>                  # bridge.extension_build_current = the deployed source's marker
> ```
>
> On a profile still running ≤0.7.3 the marker is absent, so `extension_stale`
> reads **`null`** (undecidable) rather than `false` — that is the fail-closed
> behaviour, not a bug. It becomes `false` only once a profile reports a marker
> matching the deployed one.

> **0.7.3 — the `emulate --reset` note stops claiming the viewport was restored.**
> Prose only; no wire-op or behaviour change, so `ping`'s `ops` list cannot
> discriminate it. The version bump exists because the corrected note shipped in
> #321 with NO bump, which made it undetectable — and because 0.7.2 (an unmerged
> branch build) was live on the laptop, so on-disk 0.7.1 read as OLDER than the
> running build and `extension_stale` went true for both profiles on a tree that
> was actually current. 0.7.2 is deliberately skipped: it was never on `main`.
> The discriminator is the note text itself:
>
> ```bash
> browser open https://example.com && browser emulate iphone-15
> browser emulate --reset          # read `.note`
>   # ≤0.7.1 → "emulation stopped. Nothing had to be undone — CDP overrides die at
>   #           debugger detach, so the tab was already un-emulated between ops."
>   #           (the "already un-emulated" half is false for the viewport)
>   # 0.7.3  → "…NOTHING WAS SENT TO THE BROWSER…" + says the viewport is NOT
>   #           restored and points at --reset --recreate
> ```
>
> (Quoted verbatim from `4ca5ed5:extension/service_worker.js`. 0.7.2's note — the
> one that said the overrides were CLEARED — never shipped on `main`.)
>
> 🔴 The viewport stickiness itself is UNFIXED and the mechanism is unexplained. On
> the 0.7.2 branch build `Emulation.clearDeviceMetricsOverride` was sent and
> acknowledged and the size survived it, along with a re-nav — so the clears were
> NOT carried into 0.7.3, which sends nothing on reset. Replacing the tab is the
> only known remedy (`emulate --reset --recreate`). See #319.

> **0.7.1 — `text --annotated` inside `--frame`.** No new wire op, so `ping`'s
> `ops` list is byte-identical to 0.7.0's and cannot discriminate this build.
> The discriminator is the **capability itself**, and it is exact — it exercises
> the changed line rather than trusting a version string:
>
> ```bash
> browser --instance <label> frames                 # pick a real frameId
> browser --instance <label> text --annotated --frame <frameId>
>   # 0.7.0 → annotated_with_frame_unsupported: --annotated is not supported with --frame
>   # 0.7.1 → the per-element `elements[]` payload (frame-relative CSS paths)
> ```
>
> Run it against a page that HAS an iframe: on a frameless page both builds fail
> the same way (`frame_not_found`), which discriminates nothing.

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
- [x] **The `id` changes when the load path changes** — MEASURED 2026-08-01, and
      the id is now computable from the path in advance (see "The path→id
      derivation (MEASURED)" above). ⚠ **You still cannot get the "before" value
      from `ping`** — the build loaded from the repo path is 0.2.0, which has no
      `ping` op and answers `unknown_op`. Read the **ID shown on the extension's
      card in `brave://extensions`** (enable Developer mode; the card shows both
      the ID and the load path) **before clicking Remove**, since Remove wipes
      that profile's `chrome.storage.local`. Expected values:
      repo path → `pkkoninbaeicfalpdkkmcknhnacjjjpi`, deployed path →
      `bgbkamdlkdleahpgdgmjipjbgmepgenk`.
- [x] **Same path, two profiles — SAME id.** Measured on both laptop profiles,
      twice (both on the repo path, then both on the deployed path), and the
      workbench reports the same repo-path id. The hash takes the absolute path
      only; there is no per-profile component.
- [ ] **`ping` is inert:** running it does not change the focused tab, the
      focused window, or any page (it touches no tab at all).
- [ ] `browser html` on a logged-in tab returns markup containing logged-in-only
      content (proves the live authenticated session).
- [ ] `browser eval 'document.title'` returns the active tab's title.
- [ ] `browser context` returns `{url, domain, path, searchParams, title, tabId}` without
      touching the DOM.
- [ ] `browser text` and `browser html` envelopes include `domain`, `path`,
      `searchParams`, and `tabId` alongside `url` and `title`.
- [ ] `browser text --annotated` on a page with links returns structured elements with
      `{text, path, tag, attrs, precedingText, followingText}`.
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
      `model-benchmarking.example.test`). `browser --tab <id> frames` MUST now LIST the
      cross-origin `model-benchmarking.example.test` frame (the whole point — CDP
      getFrameTree missed it); `browser --tab <id> --frame <numericId-or-url> text`
      returns THAT frame's innerText (plain `text` shows only the top frame).
- [ ] **`eval --frame` actually evaluates INSIDE the frame (the #190 fix):** on the same
      OOPIF page, `browser --tab <id> frames` → note the cross-origin frame's numeric id.
      `browser --tab <id> --frame <oopif-id> eval 'location.href'` returns
      `https://model-benchmarking.example.test/...` (PROOF eval ran inside the OOPIF) — NOT
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
