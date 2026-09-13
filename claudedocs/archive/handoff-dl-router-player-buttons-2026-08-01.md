# Handoff: dl-router player buttons — 2026-08-01

## What shipped
- **Player buttons feature** merged to `main` (commit `6346b21`)
  - `player_buttons.js` — content script running inside cross-origin embed OOPIFs
  - Two-layer rule system: context rules on page hosts, player rules on embed hosts
  - "Already have this" badge via source URL ledger
  - `state.pending` dedupe latch via `chrome.storage.local`
  - 989 pytest + 508 node tests passing
- **Player rules configured** for simpcity.cr (context) + turbo.cr (player) in `~/.config/dl-router/config.toml`
- **5 stale branches consolidated** — audit, dedupe-focus-ledger, matching, picker-overlay, profile-guard all deleted (superseded by squash-merged PRs #220–#244)
- **Extension consolidated** to single location: `scripts/dl-router/extension/`
- **SKILL.md, CLAUDE.md, README.md** updated with player buttons documentation

## Verified working
- "Save to library" buttons render on turbo.cr video embeds on simpcity.cr
- Sidecar running, `configured=True`, `dirs=27`

## What's next
1. **Test the click flow** — actually click a "Save to library" button and confirm it routes to the right directory
2. **Test the "already have this" badge** — download a video, then check the button shows "In library" on reload
3. **Test dedupe** — download the same video twice and confirm the toast warns
4. **Add more site rules** — other forums/sites with embedded video will need their own context + player rules
5. **PR the doc updates** — SKILL.md/CLAUDE.md/README.md changes are uncommitted on main

## Key architecture decisions
- Player rules are keyed on the **embed host** (e.g. turbo.cr), not the page host (e.g. simpcity.cr), because the content script runs inside the OOPIF where the `<video>` element lives
- Media URLs are signed and rotate — player_buttons.js reads them AT CLICK TIME, never caches
- The widget mounts in a **closed shadow root** to prevent the page from styling/hiding it
- Only HTML5 video with accessible `<video>` elements are supported (DRM players won't work)

## Config reference
```toml
[site_rules."simpcity.cr".context]
subject = [".p-title-value"]

[site_rules."turbo.cr".player]
container = ".plyr"
media = { element = "#main-video", attr = "src" }
mount = ".video-wrapper"
label = "Save to library"
```

## Gotchas for next session
- Extension code changes need **full Brave restart** (not ↻) — MV3 long-poll keeps old service workers alive
- Background tabs are throttled — `activate` before visual verification, `wake` before JS reads
- Closed shadow DOM means `js` can't read button text — use screenshots for visual checks
- The repo is public; never commit real paths, host names, or filenames
