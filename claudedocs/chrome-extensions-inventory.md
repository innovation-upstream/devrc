# Chrome-extension project inventory — 2026-07-30

Survey of every USER chrome-extension project under `~/workspace` (found via
`manifest.json`, excluding `node_modules`, `.git`, build outputs, and
`~/workspace/brave-profile/**` — those are Brave's own system extensions, not
projects). All are Manifest V3.

## Extensions

| Name (manifest) | Path | Repo model | Purpose (one line) | Deploy model | Status |
|---|---|---|---|---|---|
| **Browser Bridge (command channel)** `0.2.0` | `devrc/scripts/browser-bridge/extension` | devrc-nested | Drive Zach's live logged-in Brave from Claude (html/text/eval/tabs/nav/screenshot/click/type/key/upload/activate/frames) | home-manager (server as `systemd --user`; extension load-unpacked per profile) | **active — THE one** |
| **Activity Collector (browser)** `1.4.0` | `devrc/scripts/collector/browser-ext` | devrc-nested | Self-instrumentation: reports active-tab URL/title + scroll engagement to the localhost activity collector | home-manager (collector daemon; extension load-unpacked) | active (telemetry) |
| **tab-board** `1.0.0` | `~/workspace/tab-board` | standalone repo | New-tab session board: close all tabs, reopen from a spatially-stable grid; event-only worker | standalone load-unpacked | active |
| **Tab Switcher** `1.0` | `~/workspace/chrome-tab-switch` | standalone repo | Keyboard tab switch / move / MRU-jump commands | standalone load-unpacked | active (utility) |
| **CtrlWheel** `1.0` | `~/workspace/ctrl-wheel-extension` | loose dir (no `.git`) | Ctrl + Wheel-Click enhancer | standalone load-unpacked | active — see duplicate note |
| **Autoscroll Speed Controller** `1.0` | `~/workspace/scroller-extension` | loose dir (no `.git`) | Control autoscroll speed with Ctrl + Mouse-Wheel-Click | standalone load-unpacked | active — see duplicate note |
| **Double Click Enhancer** `1.0` | `~/workspace/double-click-menu-extension` | loose dir (no `.git`) | Double-click menu: autoscroll, element deletion, CSS injection | standalone load-unpacked | active (utility) |
| **Clank Select** `2.0.0` | `~/workspace/browser-extensions/clawdbot-element-sender` | nested in `browser-extensions` repo | Select page elements → send to AI agents via Matrix for code analysis | standalone load-unpacked | unknown (older side-project) |
| **Structured Downloader** `1.0` | `~/workspace/gogram/gogram-extension` | nested in `gogram` repo | Structured downloader (downloads/storage/clipboard/scripting) for gogram | standalone load-unpacked | active |
| **Structured Downloader (old)** `1.0` | `~/workspace/gogram/gogram-extension-old` | nested in `gogram` repo | Prior version of the above | none | **cruft — superseded** (see below) |
| **Stock Chat Assistant** `1.0` | `~/workspace/portfolio-chat-fe/browser-extension` | nested in `portfolio-chat-fe` repo | Analyze stock tickers from any website | standalone load-unpacked | unknown (project side-extension) |
| **clawgate task capture** `1.5.0` | `~/workspace/clawgate-extension/containers/clawgate/extension` | worktree of `homelab-talos` (`clawgate-ext-local`) | Hotkey → capture overlay → durable clawgate Task card | **load-unpacked from that worktree, on BOTH hosts** — NOT shipped by Flux or the clawgate container. Loaded in a SUBSET of profiles (2 of 5 on workbench, 2 of 6 on laptop as of 2026-08-12) and each must be reloaded separately; enumerate them with the sweep in the `clawgate` skill's `reference/extension.md` rather than assuming | active |

## Notes / non-extensions
- **`~/workspace/scrape-video/manifest.json` is NOT a browser extension.** It's a
  `url_to_file` data map for a Go video-scraper CLI (`main.go`/`scrape.go`/`go.mod`
  in the same dir). Excluded from the table; noted so it isn't mistaken for one.
- **The `clawgate task capture` extension is tracked in the `homelab-talos` repo**, so it appears
  under every checked-out worktree of that repo with identical content — those are the SAME project,
  not several projects. Only one of them is the load path:
  - `~/workspace/clawgate-extension/containers/clawgate/extension` (branch `clawgate-ext-local`) —
    **the load path**, the one listed above
  - ⚠ **Re-verified 2026-08-12:** the `~/workspace/clawgate-deploy` (`clawgate-0.7.77`) and
    `~/workspace/homelab-trunk` (`clawgate-ext-capture`) worktrees previously listed here **no longer
    exist** — absent from disk and from `git worktree list`. The cleanup-candidate entry below that
    hands you a `worktree remove ~/workspace/homelab-trunk` will fail; it is already done.
  Re-check with `git -C ~/workspace/homelab-talos worktree list` rather than trusting this list —
  it went stale once already.

## Cleanup candidates (FLAGGED — NOT deleted)
Nothing was deleted. Each below is a candidate with the reason + a suggested SAFE
removal command for Zach to run (or hand to the cleanup pass). **Verify before
running** — especially check for uncommitted changes.

1. **`~/workspace/gogram/gogram-extension-old` — superseded duplicate.**
   Verified `background.js`, `content.js`, `popup.html`, and `popup.js` all DIFFER
   from the current `gogram-extension/` (same manifest name/version `1.0`), i.e.
   `-old` is the prior copy kept side-by-side. It lives INSIDE the `gogram` git
   repo (not a worktree), so removal is an ordinary tracked-file delete:
   ```bash
   git -C ~/workspace/gogram rm -r gogram-extension-old
   # (or plain `rm -rf` if you'd rather not commit the deletion)
   ```

2. **`~/workspace/homelab-trunk` — leftover git worktree of `homelab-talos`.**
   CONFIRMED a worktree (not a standalone repo): `git -C ~/workspace/homelab-talos
   worktree list` shows `homelab-trunk → [clawgate-ext-capture]`. Per the clawgate
   skill, `homelab-trunk` is a leftover from the clawgate deploy/capture flow; its
   `containers/clawgate/extension` is just that worktree's copy of the tracked
   extension. Safe removal of the WHOLE leftover worktree:
   ```bash
   # check first for uncommitted work in the worktree, then:
   git -C ~/workspace/homelab-talos worktree remove ~/workspace/homelab-trunk
   # add --force only if it reports the worktree is dirty/locked AND you've confirmed
   # nothing there is worth keeping
   ```
   (`clawgate-deploy` [`clawgate-0.7.77`] and `clawgate-extension`
   [`clawgate-ext-local`] are ALSO worktrees of `homelab-talos`, but they look like
   the active deploy + working extension checkouts — left for Zach to judge, NOT
   flagged for removal here.)

## Possible duplication (NOT flagged for deletion — needs Zach's call)
- **`ctrl-wheel-extension` (CtrlWheel) vs `scroller-extension` (Autoscroll Speed
  Controller)** overlap heavily — both are "Ctrl + Mouse-Wheel-Click" autoscroll
  utilities living as loose (non-git) dirs. They may be two iterations of the same
  idea. Worth Zach confirming whether one supersedes the other before either is
  loaded; not deleting, since neither is version-tracked and intent is unclear.
