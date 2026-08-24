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

Six lightbox tests are labelled `REGRESSION`; each was watched to fail against
the pre-fix build before the fix landed. The rest are invariant guards.

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
