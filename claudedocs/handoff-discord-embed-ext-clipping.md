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
Enlarged Discord media must not be cropped by ancestors with `overflow: hidden`.

## State now — the fix is written and gated; it is NOT verified in Brave

- Branch `feat/flake-lock-and-discord-ext`, PR **devrc#1010**, head `1b85eaea`.
- `mergeable: MERGEABLE`, `mergeStateStatus: BLOCKED` — blocked only on the two
  required Tekton checks, both `PENDING` at hand-off time.
- Extension version **0.3.0**. Deployed copy is still whatever `home-manager` last
  installed — a merge does not deploy; the sequence is merge → pull → `switch` →
  **full Brave restart** → reload at `brave://extensions`.

## 🔴 The thing that was actually wrong

The clipping was a symptom. `0e1db9e6` ("replace subagent version with validated
code") did the opposite of its message: its extension tree is **byte-identical to
`origin/rescue/discord-embed-ext-concurrent`**, the rebuild the index recorded as
having LOST, and it replaced `eaf68c96` (#804), the browser-validated one. Three of
its four justifications are false against #804 as landed — #804 has zero `:has(` and
injects no stylesheet, its regex matches neither `/emojis/` nor `/stickers/` nor
`/avatars/`, and `/external/` was correct (`/embeds/` matches no real Discord path,
so v0.2.3 silently stopped enlarging every externally-linked embed). Only the
overflow gap was real.

**A commit message is not evidence of provenance — `git diff <commit> <branch>` is.**
v0.2.3 also reached `main` a second way, riding on `be1585c3` (#1013, "feat/gradient
rgb"), which was branched off this branch and carried the whole delta with it. An
unrelated PR title is not evidence of scope either.

## What #1010 does

Restores #804's engine, then adds the half it genuinely lacked:
`unclipAncestors()` / `reclipAncestors()` clear **every** clipping ancestor, never
`auto`/`scroll` (scroll containers — forcing those to `visible` is what broke
Discord's scroller in v0.2.1–0.2.3), and stop at the message row so chrome is out of
reach by construction. Each clear records the prior inline `overflow`+priority and
`forget()` restores it exactly. `findMessageContainer` moved into `embed_enlarge.js`
so the lightbox and the unclip walk share one boundary.

Removed as dead: `save_button.js` (dl-router needs a bearer token on every endpoint
incl. `/healthz`, so the probe 401s and the button never mounts; `POST /match` is
classify-only), the 0-byte `service_worker.js`, and `tests/fixtures/` (the losing
implementation's duplicate).

## Verification state — read this before claiming anything

| claim | status |
|---|---|
| unit tests | 134/134 green; **16/16 red** at `eaf68c96` with the same tests+harness |
| mutation sweep | 10 mutants, **10/10 killed**, each by its own named test |
| sandbox tier (what Tekton gates) | `nodetests` **PASS** on the merged tree, discord suite `tests=134 pass=134 floor=128` |
| dev-host tier | node 1366/1366; pytest 2 failures, both reproduced at the unmodified branch tip — `test_opencode_engine` (opencode 1.18.21 vs pinned 1.18.18) and `test_espanso_detect` (`:acq`/`:dacq` collision). Neither is in a file this touches. |
| **live in Brave** | 🔴 **NOT DONE.** Nothing here proves the crop is gone on a real page. |

🔴 One mutant **SURVIVED** the first sweep and the fixture was fixed, not the score:
deleting the message-boundary break changed nothing, because the only ancestor above
the row was the `auto` scroller the predicate already rejects — the guard was
**unreachable**. The rig now carries chrome with `overflow: hidden` above the row.
If you touch that walk, keep that element or the guard silently stops being tested.

## Next steps (ranked)

1. **Merge #1010** once the two Tekton checks report. They were `PENDING` for the
   whole session; a run that hits `timeouts.tasks` leaves them pending forever and
   only a fresh push clears it.
2. **Deploy and verify live** — the only outstanding correctness question.
   `ship.sh` → full Brave restart → `brave://extensions` reload → open a channel with
   image attachments → confirm `#dee-enlarge-css` is present and the image is uncropped.
3. **Unblock the live probe first if you want to measure rather than eyeball it.**
   Two separate blockers, both measured 2026-08-29:
   - the extension injects in NEITHER bridge-reachable profile (`#dee-enlarge-css`
     absent, 0 elements marked) — it may simply not be `Load unpacked`ed there;
   - the profile holding Discord (`personal - other`) has a browser-bridge build that
     cannot inject into `discord.com` (`Cannot access contents of the page`). Both
     bridge instances report `extension_stale: true` vs expected build `aada672ff3`.
     A full Brave restart after a `switch` is what clears that.
4. **Pre-existing reds, unrelated to this work, someone should own**: the pinned
   opencode version (1.18.18 vs the 1.18.21 on PATH) and the espanso `:acq`/`:dacq`
   search-term collision. Both fail the pytest tier today on this branch *and* at its
   unmodified tip.

🔴 **This list is a WORK QUEUE WITH NO LOCK** — every `/resume` session draws
from it, so a *better* ranked list produces *more* duplicate work, not less.
Make each item cheap to check: name the repo and the files it will touch, and
**mark anything in flight `IN FLIGHT: <repo>#<pr>`** rather than leaving it
looking unclaimed. Worktrees do NOT prevent this.

## Gotchas / decisions / dead-ends

- **Dead ends, do not re-derive**: class-substring selectors (`imageWrapper`,
  `mosaicItem`, `attachment`, `wrapper-*`, `wrapper_*`, `imageContainer`), CSS
  `:has(> …)` (reaches only the *immediate* parent), and an unbounded
  `clearParentConstraints` style sweep. All three are in v0.2.3 and none of them work.
- `getComputedStyle().height` is a **used px value**, essentially never the string
  `"auto"` — v0.2.3's `cs.height !== "auto"` therefore fired on every ancestor.
- The test harness had two faithfulness holes that made unclip tests vacuous until
  fixed: `FakeComputedStyle` had no `getPropertyPriority`, and the computed-style seam
  did not expand the `overflow` shorthand to longhands the way a real browser does.
- `.envrc` here is `use opencode`, so a bare `python3 -m pytest` says "No module named
  pytest". Use `nix develop <repo> -c …`. `gate.sh` exits 3 for exactly this.
- The base clone `~/workspace/devrc` moved branches twice during this session by other
  sessions. Work in a worktree; do not trust its checked-out branch.

## How to verify
1. `node --test scripts/discord-embed-ext/tests/*.test.mjs` — 134 pass
2. `nix build .#checks.x86_64-linux.{nodetests,pytests}` — the tier Tekton gates on;
   `gate.sh` alone does NOT run it
3. `home-manager switch --flake ~/workspace/devrc --impure`, then a FULL Brave restart
4. Open a Discord channel with image attachments — uncropped, and clicking one opens
   the lightbox with working `+` / `−` / `Reset` / `‹` / `›` buttons
