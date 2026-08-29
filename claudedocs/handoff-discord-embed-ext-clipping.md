# Handoff: discord-embed-ext-clipping — 2026-08-29

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Fix the discord-embed-ext so enlarged Discord media is not clipped by parent containers. The extension enlarges images/videos beyond Discord's 400x300 cap, but parent elements with `overflow: hidden` and fixed `max-height`/`max-width` constraints clip the enlarged content.

## State now
- Branch: `feat/flake-lock-and-discord-ext` (pushed, not merged)
- Deployed version: **0.2.3** (`~/.local/share/discord-embed-ext/`)
- Source version: **0.2.3** (`scripts/discord-embed-ext/extension/`)
- Commit: `f9929102` (enlarge embed improvements)
- 68 tests pass
- **CLIPPING STILL PRESENT** — parent containers still clip enlarged media

## What was tried (versions 0.2.0–0.2.3)
1. **v0.2.0** — CSS class selectors: `[class*='imageWrapper']`, `[class*='mosaicItem']`, `[class*='attachment']`, `[class*='wrapper-']`, `[class*='imageContainer']`. Did not match all Discord containers.
2. **v0.2.1** — DOM traversal (`clearParentConstraints`) walking up from each media element, setting `overflow: visible !important`, removing `max-height`/`max-width`/`height` via computed styles. Caused **scroll loop cascade** — style changes triggered Discord React re-renders, which triggered MutationObserver, which re-ran constraints.
3. **v0.2.2** — Added `data-dee-cleared` attribute to parents so they're only processed once. Added depth cap of 8. Disconnected observer during style changes. Cascade stopped but **clipping still present** — depth 8 may not reach the actual clipping container, or the containers are higher up.
4. **v0.2.3** — CSS `:has()` selectors targeting ancestors of media elements directly (no class name guessing). Added `wrapper_` (underscore variant). Still **clipping present**.

## Open investigations — live diagnosis state

### Parent container clipping not resolved
- **Symptom:** Enlarged Discord images/videos are cut off by parent containers with `overflow: hidden` and fixed dimensions.
- **Observed:** The CSS `:has()` selectors and class-based selectors target known Discord containers (`imageWrapper`, `mosaicItem`, `attachment`, `wrapper-*`, `wrapper_*`, `imageContainer`), but some parent higher in the DOM still clips.
- **Ruled out:**
  - CSS `!important` alone — Discord uses inline styles and dynamic class names
  - DOM traversal with depth 8 — may not reach the clipping container
  - Observer cascade — fixed with `data-dee-cleared` tracking and disconnect/reconnect
- **Leading hypothesis:** The actual clipping container has a class name not matched by current selectors, OR there's a container with `overflow: hidden` set via inline style or a class pattern not covered. Need to inspect the live Discord DOM to find the exact element.
- **Next probe:** Use browser-bridge to inspect the live DOM on a Discord channel with images — find which ancestor has `overflow: hidden` and what its class name is. Command: `$BB --instance "personal - other" --tab <id> js '(function(){ ... })()'` to walk ancestors and report overflow/max-height values.

## Next steps (ranked)
1. **Inspect live Discord DOM** to find the exact clipping container — which element has `overflow: hidden`, what's its class pattern. Use browser-bridge `js` eval on a channel with images.
2. **Fix the CSS/DOM traversal** based on the actual class pattern found.
3. **Bump version, deploy, verify** the clipping is gone.
4. **Commit and push** the fix to `feat/flake-lock-and-discord-ext`.

🔴 **This list is a WORK QUEUE WITH NO LOCK** — every `/resume` session draws
from it, so a *better* ranked list produces *more* duplicate work, not less.
Make each item cheap to check: name the repo and the files it will touch, and
**mark anything in flight `IN FLIGHT: <repo>#<pr>`** rather than leaving it
looking unclaimed. Worktrees do NOT prevent this.

## Gotchas / decisions / dead-ends
- CSS class selectors (`[class*='wrapper-']`) don't match Discord's actual class names (they use `_` not `-`, and have hashed suffixes)
- DOM traversal causes scroll loops when style changes trigger React re-renders
- Depth cap of 8 may be too shallow — Discord's DOM nesting for message attachments can be deeper
- `getComputedStyle` is unavailable in test environment — functions must guard with `typeof getComputedStyle === "function"`
- The extension's `data-dee-enlarged` attribute prevents re-processing media elements, but `data-dee-cleared` on parents prevents re-processing containers
- Observer must disconnect during style modifications to prevent cascade

## How to verify
1. `node --test scripts/discord-embed-ext/tests/*.test.mjs` — all 68 tests pass
2. `home-manager switch --flake ~/workspace/devrc --impure` — deploy
3. Reload extension at `brave://extensions`
4. Open a Discord channel with image attachments — images should display at full size without clipping
