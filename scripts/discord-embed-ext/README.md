# discord-embed-ext

An unpacked MV3 content-script extension that lifts Discord's ~400×300 cap on
native media embeds, and opens the media in a lightbox (zoom, pan, and
navigation across the images **in that message**) when you click it.

Content scripts only. **Zero `permissions`, zero `host_permissions`** — it talks
to nothing, local or remote.

| file | what it does |
|---|---|
| `extension/embed_enlarge.js` | finds Discord media, walks up to the element that caps its size, overrides the cap, marks it `data-dee-enlarged="1"` |
| `extension/lightbox.js` | click-to-open overlay in a closed shadow root: zoom (keys, wheel, buttons), drag-to-pan, per-message navigation |
| `extension/manifest.json` | MV3; matches `discord.com` only |
| `tests/fake_discord_dom.mjs` | the synthetic DOM both test files run against |

## Why it does not key off Discord's class names

Discord ships hashed, build-generated class names (`imageWrapper__74e4d`), so
anything matching them literally breaks on the next CSS reshuffle. Instead:

* **media detection** is by URL host **and path prefix** — `cdn.discordapp.com` or
  `media.discordapp.net`, followed by `/attachments/` or `/external/`;
* **the size cap** is found by walking up at most 8 ancestors and taking the
  first whose *computed* `max-width` ≤ 500px or `max-height` ≤ 400px;
* **the message boundary** is the first ancestor whose class *contains* the
  substring `message`, or whose id starts with `chat-messages-`.

The last one is a heuristic and the most likely thing to rot. It has a
`MESSAGE_WALK_DEPTH` of 15 and falls back to treating the media as its own only
sibling, so the failure mode is "navigation does nothing", never a crash.

Each of those three properties is pinned by a test that was watched to fail
without it — the substring match against a hashed class (`message__74e4d`), the
`chat-messages-` row id, and both sides of each walk-depth constant. An earlier
version of this file asserted the anti-rot property in prose while no test
pinned it: `cls === "message"` passed the whole suite.

Clicking a `<video>` deliberately does **not** open the lightbox: the element
*is* its own controls, so a click on play/scrub/volume would open an overlay
instead of doing what you asked. Video is enlarged and plays inline. Modified
clicks (Ctrl/Cmd/Shift/Alt, middle, right) are always left to the browser.

🔴 **A cap is only a cap if it is a `px` length.** `getComputedStyle().maxWidth`
returns the string `"100%"` for a percentage cap, and `parseFloat("100%")` is
`100` — under the 500px threshold. Reading it that way made the walk latch the
first ancestor with `max-width:100%`, which is ubiquitous and often shared
layout, and write `!important` overrides onto it with no undo.

Only a **non-negative px length** counts. Fractional values are deliberately
included, because `calc()` and flex layout genuinely produce them — do not "tidy"
the pattern to integers-only: `399.5px` is a real cap, and rejecting it silently
stops the extension enlarging a legitimately capped embed. A percentage, `none`,
a `calc()` string and a **negative** length are all rejected; CSS forbids a
negative `max-width`, and accepting one made the walk latch onto that ancestor.
Every one of those directions is pinned by a test.

(An earlier version of this paragraph claimed negative values were deliberate and
pinned. They were neither — the branch was unreachable and no test touched it.)

🔴 **The path prefix is load-bearing, not tidiness.** The same CDN host serves
avatars, server icons, emojis, stickers, banners, role icons, clan badges and
48×48 `/media/` decorations. Measured against two real logged-in channels on
2026-08-24, a host-only pattern matched **59 of 60** `<img>`/`<video>` on the page
and would have enlarged **10 user avatars**, while matching **zero** actual
attachments. Widen this pattern only against a real client, never against
`tests/fake_discord_dom.mjs` — every URL in that fixture is already an
attachment, so it cannot show you this class of mistake.

## Install

Nix deploys the extension to a stable path outside the git tree on every
`home-manager switch`:

    ~/.local/share/discord-embed-ext

That path is deliberately **not** in `~/workspace/devrc`: Brave loads an unpacked
extension from disk continuously, and a checkout or rebase in the repo would
otherwise swap its code out from under a running browser.

Registering it with Brave is a **one-time manual step** — nix cannot do it:

1. `brave://extensions` → enable **Developer mode**
2. **Load unpacked** → `~/.local/share/discord-embed-ext`
3. Reload any open Discord tab.

After a `switch` that changes the extension, click **Reload** on the card (or
restart Brave). A `git pull` alone changes nothing — see `CLAUDE.md`,
"Merged ≠ deployed".

🔴 A new file under `extension/` must be `git add`ed before it will deploy at
all: flakes only see git-tracked files, so an untracked file is silently omitted
from the deployed tree with no error anywhere.

## Tests

    node --test scripts/discord-embed-ext/tests/*.test.mjs

Gated by `scripts/run-node-tests.sh` (suite `scripts/discord-embed-ext/tests`),
which is one of the two required merge tiers. Note `node --test <dir>` does
**not** work — pass the files.

Tests labelled `REGRESSION` were each watched to fail against the build that
lacked the fix. The rest are invariant guards — do not count them as regression
coverage.

🔴 **An earlier version of this file claimed six such tests for the lightbox.
There were five.** The sixth ("navigating re-applies the transform") was credited
from a RED control that carried all six defects at once, so it failed on a
different assertion in the same test. Isolate a mutant before claiming it is
pinned. The transform is genuinely pinned now.

The suite is mutation-swept: **86 semantic mutants, 84 killed** — operand swaps,
branch inversions, constants moved in **both** directions, guard removals. A
positive control (reverting the media pattern to host-only) dies, so the harness
can go red. The two survivors are ONE equivalence class counted properly — the
`nodeType === 1` filter on the childList arm and on the attributes arm. Both are
defence in depth: `scan()` returns 0 for any root it cannot query, and an
attribute record only ever targets an Element.

🔴 **Read that number correctly.** It is a claim about the 86 mutants somebody
constructed, never about the suite. Every audit that went looking found one more,
and the list is NUMBERED so the count cannot drift from it again — it already did
so twice: the sentence said "four" while the list named six, was corrected to
"six", and a clause was appended in the same commit without moving the number.
The defect this paragraph is about, regrown inside the paragraph about it, twice.
Count the items; do not trust a total anyone wrote in prose, this one included.

1. "25 mutants, 24 killed, the survivor is equivalent" — an independent audit
   built its own battery and found **eight** more survivors, none equivalent.
2. Corrected to 36 — a third audit, with 163 mutants, found a 37th.
3. Corrected to 46 — a fourth found a 47th *and* a whole vacuous class (below).
4. A fifth found the same module-scope entry-point gap in the OTHER content
   script, and corrected "four" voided guards to seven.
5. A sixth found four checked-in numbers disagreeing with each other.
6. A seventh found the `didDrag` latch pinned on its SET and not its RELEASE —
   which reads as fully pinned.
7. An eighth found the backdrop's own `wheel` and `mousedown` listeners could be
   deleted with the suite green, leaving scroll-to-zoom and drag-to-pan inert.

If you change this code, add the mutant for the failure mode you are introducing,
and add a numbered line here if a sweep total ever turns out to be wrong again.
A passing sweep only means nobody has imagined the next one yet.

🔴 **The fixture must not normalise away what the code normalises.** A real HTML
document reports `tagName` **UPPERCASE**; `tests/fake_discord_dom.mjs` used to
lowercase it, which made **seven** `.toLowerCase()` calls across both content
scripts untested — any one could be deleted with the suite green, and each deletion makes
the extension completely inert in Brave. The fake now reports uppercase. Ask of
any new fixture: what does it smooth over that production does not?

## What was deliberately dropped

An earlier build had a **Save** button posting to the dl-router sidecar on
`127.0.0.1:8791`. It never worked: it sent no `Authorization` header, and
dl-router requires a bearer token on every endpoint, so the health probe got 401,
the button never mounted, and nothing ever appeared on screen. `POST /match` also
only *classifies* a download — it saves nothing — so the success path would have
reported "Saved!" for a save that never happened.

It was removed rather than fixed. Routing Discord media into the library is
dl-router's job, and it belongs behind dl-router's own extension and its token,
not re-implemented here. The removed code is on the `rescue/discord-embed-ext`
branch if that integration is ever wanted.
