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

## State now — VERIFIED LIVE: the crop is gone. One sub-item is still open.

🔴 **Heading kept verbatim so this REPLACES rather than stacking a second `State now`
above the stale one.** Its old text said "NOT verified in Brave". **That is now false for
the crop** — measured 2026-08-30, positive control satisfied. The lightbox half is still
open. Read on.

### Measured 2026-08-30 on a real channel (`personal - other` instance)

One self-consistent probe, `attachments` NON-ZERO so the rest means something:

| measure | value |
|---|---|
| `attachments` (**positive control**) | **14** |
| `marked` | **14 / 14**, and `marked_not_attachment` = **0** |
| non-message media on the page | **129** (100 avatars, 16 icons, 9 clan-badges, 4 emojis) — **0 marked** |
| `max-height` cap lifted | **14 / 14** |
| `unclipped` ancestors | **44** |
| **still clipped by an ancestor (axis-correct)** | **0** ← the claim this work exists for |
| rendered past Discord's 400×300 cap | 10 of 14; the other 4 are natural-size-small and correctly not upscaled |

An earlier independent read on a different tab agreed in shape: attachments 19, marked 19,
unclipped 54. **The 2026-08-24 avatar hazard is closed live** — 129 avatars/icons/emojis on
screen, none touched.

⚠ **The first clip detector had an AXIS bug** and reported a false positive: it flagged an
`overflow-x: hidden; overflow-y: scroll` scroller as clipping, when the element only
overflowed on the *scroll* axis. Test each axis against its OWN overflow property; the
numbers above are from the corrected version.

### 🔴 Three corrections to what this doc used to say

1. **The extension is registered in exactly ONE profile, and NOT at the path this doc named.**
   Brave `Profile 2` (named `other` — the `personal - other` bridge instance) holds it,
   unpacked, id `dffjnoklmild…`, dev mode on. Its registered path is the **repo working
   tree** `scripts/discord-embed-ext/extension`, **not** `~/.local/share/discord-embed-ext`.
   All three copies (nix, repo, `origin/main`) were byte-identical when checked, so no harm
   today — but Brave runs whatever that checkout holds, and this doc's own Gotchas note that
   the base clone's branch gets moved by other sessions. Old step 2 ("Load unpacked it at
   `~/.local/share/discord-embed-ext`") would have created a SECOND registration.
2. **That is why the previous probe read zero.** It probed the `work` instance (Brave
   `Default`), which has **no discord-embed-ext registered at all**. A zero there was
   structural, never a symptom — a stronger statement than the earlier retraction, which
   only said the stylesheet inference was void.
3. **No restart was needed.** The registration's `last_update_time` is 2026-08-29 19:56,
   after the 0.3.0 bytes landed at 14:01 — it was reloaded at `brave://extensions`. The
   Brave process itself had been up since 08-26 and still is.

### 🔴 There is no `Reset` control — this doc's "all five" was wrong

`lightbox.js` builds `−`, a `100%` label, `+`, and `◀`/`▶` (nav arrows only when the
message holds more than one media). Keys: `Escape`, `←`, `→`, `+`/`=`, `-`/`_`. **No reset
button and no reset key exist in 0.3.0.** Do not go looking for one.

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
- ✅ **VERIFIED in Brave 2026-08-30** for the crop: 14 attachments, 14 marked, **0 still
  clipped**, 0 of 129 avatars/icons/emojis touched. Table above.
- 🔴 **The LIGHTBOX is still unverified.** A trusted CDP click on a marked image did not
  create `#dee-lightbox-host`. That is ONE unexplained observation, **not a defect** — see
  the open investigation below, which names the two rival mechanisms and the one read that
  separates them.

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
| **live in Brave — the crop** | ✅ **DONE 2026-08-30.** 14 attachments, 14/14 marked, 44 ancestors unclipped, **0 still clipped**, 0 of 129 avatars/icons/emojis touched. |
| **live in Brave — the lightbox** | 🔴 **NOT DONE.** A trusted click did not open it; two rival mechanisms, undiscriminated. |

🔴 One mutant **SURVIVED** the first sweep and the fixture was fixed, not the score:
deleting the message-boundary break changed nothing, because the only ancestor above
the row was the `auto` scroller the predicate already rejects — the guard was
**unreachable**. The rig now carries chrome with `overflow: hidden` above the row.
If you touch that walk, keep that element or the guard silently stops being tested.

## Next steps (ranked)

1. **Settle the lightbox — the ONLY open correctness question left.** Cheapest first,
   and it needs no tooling: in Brave `Profile 2` (`other`), click any Discord message
   image. A black overlay with `−  100%  +` means it works and this topic is closed.
   If nothing happens, run the hit-test in the open investigation below **before**
   touching `lightbox.js` — the likeliest cause is not in our code.
2. **Do NOT `Load unpacked` anything.** It is already registered in `Profile 2`, from
   `scripts/discord-embed-ext/extension` (the repo tree, not the nix path). A second
   registration is the failure mode here, not the fix. Check
   `brave://extensions` before assuming absence — and note the `work` profile
   (`Default`) has never had it, so probing there measures nothing.
3. **Nothing else is outstanding for this topic.** Both PRs are merged, both hosts
   shipped, and the crop is verified live. Do not re-open the engine work.

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

### CLOSED 2026-08-30 — is the extension running, and is the crop gone? YES.

Measured, positive control satisfied. Numbers in `State now` above. Do not re-derive.
The three things that made the earlier reading unmeasurable, so nobody repeats them:

- The old probe ran against the **`work` instance**, which has **no discord-embed-ext
  registered at all**. Not a stale build, not an injection failure — the extension was
  never there. Probe `personal - other` (Brave `Profile 2`).
- That channel held **0** attachments, so `marked: 0` was what a WORKING extension also
  reports. `attachments > 0` is the positive control and is not optional.
- `#dee-enlarge-css` and `globalThis.__DEE__` are BOTH non-signals: the restored #804
  engine injects no stylesheet, and content scripts run in an isolated world the bridge's
  eval cannot see. Their absence means nothing either way.

⚠ **The `personal - other` bridge blocker is INTERMITTENT, not permanent.** The old text
said that instance "cannot inject into `discord.com` at all". On 2026-08-30 it injected
fine for several probes, then returned `Cannot access contents of the page` again on a
fresh tab, with `extension_stale: true` throughout (build `b817ef1e88267a40` vs expected
`aada672ff3a5ded7`). If a read fails there, retry before concluding anything.

### OPEN — does clicking a marked image open the lightbox?

- **Symptom:** a trusted CDP click on a `[data-dee-enlarged]` image did **not** create
  `#dee-lightbox-host`. Observed once, on one image.
- 🔴 **This is NOT a diagnosis.** An empty result cannot separate the two mechanisms
  below, and picking the one you already suspect is a coin flip you will record as a
  finding.
  1. **Discord's overlay ate the click.** `installAutoStart` walks **UP** from
     `e.target`. Discord renders an anchor over the image; if that anchor is a
     *sibling* of the `<img>` rather than an ancestor, the walk never reaches the
     marked element and the handler correctly does nothing. **Nothing is broken.**
  2. A genuine failure of the click hook.
- **What is already ruled IN:** per-element prep completed. `embed_enlarge.js:248` writes
  `cursor: zoom-in` immediately before `setAttribute(ATTR_ENLARGED, "1")` at :250, and all
  14 elements carried the attribute — so that code path ran to the end on every one.
- 🔴 **The five in-shadow controls cannot be exercised or observed from page JS at all.**
  `lightbox.js:119` attaches the shadow root with `mode: "closed"`, so `host.shadowRoot`
  is `null`. A screenshot is the only programmatic route. `#dee-lightbox-host` itself is
  in the LIGHT dom, so open/closed IS observable — that is the whole budget.
- **The one read that separates the two mechanisms** (read-only, opens nothing):
  ```bash
  BB=~/workspace/devrc/scripts/browser-bridge/browser
  $BB --instance 'personal - other' --tab <id> wake --wait 2000
  $BB --instance 'personal - other' --tab <id> js '(function(){
    var m=document.querySelectorAll("[data-dee-enlarged]"),out=[],vh=innerHeight;
    for(var i=0;i<m.length&&out.length<3;i++){
      var e=m[i],r=e.getBoundingClientRect();
      if(!(r.width>40&&r.height>40))continue;
      var cx=Math.round(r.left+r.width/2),cy=Math.round(r.top+r.height/2);
      if(cy<0||cy>vh)continue;
      var hit=document.elementFromPoint(cx,cy),t=hit,found=false,d=0,walk=[];
      while(t&&d<8){walk.push(t.tagName);
        if(t.getAttribute&&t.getAttribute("data-dee-enlarged")==="1"){found=true;break;}
        t=t.parentElement;d++;}
      out.push({hit_is_the_img:hit===e,walk_up_reaches_marked:found,chain:walk});}
    return out;})()'
  ```
  **`walk_up_reaches_marked: false` ⇒ mechanism 1 — the code is fine, the click never
  had a chance.** `true` with no lightbox on a real click ⇒ mechanism 2, and only then is
  `lightbox.js` worth opening.
- **Cheaper still, and it needs no tooling:** click an image by hand in `Profile 2`. A
  black overlay with `−  100%  +` closes this outright.
