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

🔴 **Heading kept verbatim so this REPLACES rather than stacking a second `State now`
above the stale one.** Both its clauses are still literally true — but it now understates:
the work is MERGED and SHIPPED. Read on.

- **#1010 MERGED** as squash `2a8a8982` (2026-08-29T19:00:39Z). **#1023 MERGED** as
  `8e33bf1d` — it had to go first, see below. **#1024 CLOSED** as my own duplicate.
- Verified on `origin/main` **by content**, never ancestry (a squash merge makes the
  branch head permanently not-an-ancestor): manifest `0.3.0`, `unclipAncestors` +
  `reclipAncestors` both present, extension dir is exactly
  `embed_enlarge.js / lightbox.js / manifest.json / icons`, SUITES floor `|2|128`.
  Re-confirmed at `c8223366`, 8+ commits later — the work survived.
- **Shipped**: `ship.sh` converged both hosts, 0 dangling / 0 stale artifacts each,
  cross-host agreement on one sha. Deployed copy is byte-identical to `origin/main`
  and reports `0.3.0`.
- 🔴 **NOT verified in Brave.** Nobody has yet seen an uncropped image. See the
  open investigation below — the probe I used earlier is now structurally invalid.

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

1. **Verify live in Brave** — the only open correctness question for this work.
   Full Brave restart → `brave://extensions` → confirm *Discord Embed Enlarge 0.3.0*
   is loaded from `~/.local/share/discord-embed-ext` → open a channel **with image
   attachments** → run the probe above and require `attachments > 0` → confirm the
   image is uncropped and clicking it opens the lightbox with working
   `+` / `−` / `Reset` / `‹` / `›` (all five were dead in v0.2.3).
2. **If it is NOT loaded**, `Load unpacked` it once at `~/.local/share/discord-embed-ext`.
   Nix keeps that directory correct but cannot register it with Brave.
3. **Nothing else is outstanding for this topic.** Both PRs are merged, both hosts
   shipped and verified. Do not re-open the engine work without a live reading first.

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

- 🔴 **A commit message is not evidence of provenance — `git diff <commit> <branch>` is.**
  `0e1db9e6` ("replace subagent version with validated code") did the opposite of its
  message: its tree is byte-identical to `origin/rescue/discord-embed-ext-concurrent`,
  the rebuild the index recorded as having LOST. Three of its four justifications were
  false against `eaf68c96` as landed. v0.2.3 then reached `main` a second way riding on
  `be1585c3` (#1013, "feat/gradient rgb") — **an unrelated PR title is not evidence of
  scope either.**
- 🔴 **I duplicated work by not sweeping open PRs first.** I wrote #1024 (espanso +
  opencode pin) when #1023/#1021/#1022/#1015 already existed. RULES.md says to sweep
  `gh pr list --state open`; I did not. #1023 was a superset and I closed mine. The
  cross-check was still worth something — two independent derivations agreed exactly on
  `PINNED_VERSION = "1.18.21"` and on the renumbered-claim counts in all five files
  spot-checked.
- 🔴 **My espanso config fix was later SUPERSEDED, deliberately.** #1023 removed `"ask"`
  from `:dacq`'s `search_terms`; `31cd214d` (#1060) put it back and instead gave
  `espanso_detect.py` a **declared owner** mechanism — the picker keeps the route and
  attribution gets an owner, rather than the config losing a search term. Current main:
  `search_terms = ["ask" "clarifying" "feedback" "dispatch" "process" "elicit" "scope"
  "include"]`. Do not "re-fix" this by deleting `ask` again.
- **A blanket sed of version literals is the fix the opencode pin explicitly forbids.**
  `4cf15644` landed one on this branch (44/44 lines) and failed CI — it renumbers the
  dated COST measurements and the `TaskTool.execute` internals into false claims that
  read like fresh readings. Dropped in the merge, after verifying the branch had made
  **0** changes to those 16 files so taking main's side discarded only the sed.
- **The store-api tests are a known load flake.** Three different tests from
  `scripts/tests/test_subsystem_store_api.py` failed across three unrelated commits in
  one afternoon (15s HTTP timeouts under xdist). #1015/#1023 fixed the scheduling
  timeout. If CI goes red there, check that file before your diff.
- **Mutation testing found the guard that mattered was UNREACHABLE.** Deleting the
  message-boundary break survived a green suite, because the only ancestor above the row
  in the fixture was the `auto` scroller `clipsOverflow` already rejects. The rig now
  carries `overflow: hidden` chrome above the row. **Keep that element** or the guard
  silently stops being tested.
- **Dead ends, do not re-derive:** class-substring selectors (`imageWrapper`,
  `mosaicItem`, `attachment`, `wrapper-*`, `wrapper_*`, `imageContainer`), CSS
  `:has(> …)` (reaches only the immediate parent), and an unbounded style sweep. All
  three are v0.2.3 and none work. `getComputedStyle().height` is a used px value, never
  the string `"auto"`, so v0.2.3's `cs.height !== "auto"` fired on every ancestor.

## How to verify

1. `node --test scripts/discord-embed-ext/tests/*.test.mjs` — 134 pass
2. `nix build .#checks.x86_64-linux.{nodetests,pytests}` — the tier Tekton gates on;
   `gate.sh` alone does **not** run it
3. Content check on main (never ancestry — squash merges break it):
   `git show origin/main:scripts/discord-embed-ext/extension/manifest.json | grep version`
   → `0.3.0`, and `ls` that tree → exactly `embed_enlarge.js lightbox.js manifest.json icons`
4. The live probe in the open-investigation block above, with `attachments > 0` as its
   positive control.
## Open investigations — live diagnosis state

### Is the extension actually running in Brave, and is the crop gone?
- **Symptom + exact repro:** unknown — this has never been observed either way at
  0.3.0. Repro path: open a Discord channel that contains real message
  **attachments** (not avatars), look at whether the image is cropped, then click it.
- **Observed (with values):** probed the live `work` profile, tab `1642098896`
  (`#notes`), 2026-08-29 after the ship:
  `{"attachments_present": 0, "cdn_path_segments": {"avatars": 21, "icons": 35},
  "dee_global": "undefined", "marked": 0, "unclipped": 0, "zoom_cursor": 0}`.
- 🔴 **Ruled out — and this kills my own earlier conclusion.** The previous handoff
  said "the extension injects in NEITHER reachable profile
  (`#dee-enlarge-css` absent, 0 elements marked)". **That inference is void at
  0.3.0.** The restored #804 engine injects **no stylesheet at all** — `grep -c
  'createElement("style")'` on `extension/embed_enlarge.js` is **0**. Only the
  rejected v0.2.3 had `dee-enlarge-css`. So a missing stylesheet is the EXPECTED
  reading now and is evidence of nothing.
- **Also ruled out as a signal:** `typeof globalThis.__DEE__` from the bridge. Content
  scripts run in an ISOLATED world, so the bridge's eval cannot see `__DEE__` whether
  or not the extension is loaded. `undefined` there is not absence either.
- **Why the counts are unmeasured, not zero:** that channel contains **0**
  `/attachments/` or `/external/` media. `applyOverride` only ever marks message
  media, so `marked: 0` is what a WORKING extension also reports there. An empty
  result cannot distinguish the two mechanisms.
- **Leading hypothesis:** unknown, genuinely. It may be working and simply
  unobserved. It may not be `Load unpacked`ed — that is a manual step nix cannot do,
  and the tree was replaced at a new path by the deploy.
- **Next probe (run verbatim, on a channel WITH attachments):**
  ```bash
  BB=~/workspace/devrc/scripts/browser-bridge/browser
  $BB --instance work --tab <id> wake --wait 1500
  $BB --instance work --tab <id> js '(function(){
    var segs={},els=document.querySelectorAll("img,video,source");
    for(var i=0;i<els.length;i++){var s=els[i].getAttribute("src")||"";
      if(!/discordapp/.test(s))continue;
      try{var u=new URL(s);var k=u.pathname.split("/")[1]||"root";segs[k]=(segs[k]||0)+1;}catch(e){}}
    return {segments:segs,
            attachments:(segs.attachments||0)+(segs.external||0),
            marked:document.querySelectorAll("[data-dee-enlarged]").length,
            unclipped:document.querySelectorAll("[data-dee-unclipped]").length};})()'
  ```
  🔴 **`attachments` must be NON-ZERO before `marked` means anything.** If it is 0 you
  measured nothing — find another channel. That pair IS the positive control.
- **Blocker on the other profile:** the `personal - other` instance's browser-bridge
  extension cannot inject into `discord.com` at all (`Cannot access contents of the
  page`). Both bridge instances reported `extension_stale: true` vs expected build
  `aada672ff3`. A full Brave restart after a switch is what clears that.
