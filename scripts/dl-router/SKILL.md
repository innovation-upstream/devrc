---
name: dl-router
description: Operate the media download router — it files downloads by PAGE CONTEXT, not the filename. Use for: the download router, dl-route, downloads landing in the wrong folder, auto-filing downloads, the download picker/toast, a wrong route or a new site rule, backfilling loose files, the sidecar or extension.
---

# dl-router

Loopback sidecar (`127.0.0.1:8791`, bearer token) + a **separate** MV3 extension
that answers Chrome's `onDeterminingFilename` with `"<subject dir>/<name>"`.
Deterministic matching over page context — no LLM, no network.

Full design and rationale: `scripts/dl-router/README.md`.

## Orient first

```bash
dl-route status          # config path, library root, dir count, sidecar health
systemctl --user status dl-router
```

`dl-route status` reporting `library    (unset ...)` means `library_root` is not
configured — the sidecar answers `/healthz` but every routing endpoint returns
503. That is the normal state on a host that has not been set up, not a failure
to fix.

## Where things live

| What | Where |
|---|---|
| code | `scripts/dl-router/` (this repo) |
| config | `~/.config/dl-router/config.toml` (**never committed**) |
| directory kinds | `~/.config/dl-router/dirs.toml` (**never committed**) |
| bearer token | `~/.config/dl-router/token` (0600, auto-created) |
| aliases, route log | `~/.local/share/dl-router/dl-router.sqlite3` |
| backfill manifests | `~/.local/share/dl-router/manifests/` |
| discarded duplicates | `<library root>/.dl-router-trash/` (hidden; `mv` one back to undo) |
| service | `systemd --user` unit `dl-router` (from `nix/home.nix`) |
| extension | `scripts/dl-router/extension/`, loaded unpacked per profile |
| player rules | `~/.config/dl-router/config.toml` → `[site_rules."<host>".player]` |

🔴 **The repo is public; the library is private. Never print the library root,
directory names, filenames, the route log, alias keys, real channel ids, forum
names or host names into a commit message, a PR, a doc, or any file in this
repo.** Synthetic names only (`Jane Doe`, `acme-studio`, `example-site.test`,
`someforum.test`, made-up snowflakes). This includes the output of
`dl-route dirs classify` and `dl-route alias review` — both are lists of the
operator's private taxonomy.

## Common tasks

**"A download went to the wrong folder."**
```bash
dl-route log -n 20            # each line shows the score and the REASON
```
The reason string is the diagnosis: `alias(site:…)`, `tag=='…'`, `contains '…'
(2/4 tokens)`, `filename tokens […]`, `tie: …`, `+host-prior`. Fix it in the UI
(the toast's `change`, which writes an alias via `/learn`) or directly:
```bash
dl-route alias set "<page tag>" "<Directory>" --site example-site.test
dl-route match --tag "<page tag>" --site example-site.test    # re-check
```
Site-scoped aliases score 1.00 and beat everything else.

**"It keeps asking instead of auto-filing."** Check the reason string first —
four distinct causes, and only one of them is the score:

1. **`unclassified directory '<X>'`** — `~/.config/dl-router/dirs.toml` does not
   list it. An unclassified directory NEVER auto-files. Most likely cause on a
   host that has not run the classifier; `dl-route status` prints
   `unclassified=N` for exactly this. Fix: `dl-route dirs classify --out
   ~/.config/dl-router/dirs.toml`, then edit it — picked up live, no restart.
2. **`category directory — always confirm`** — by design, whatever it scores. Do
   not "fix" this by reclassifying a genuine category as a performer.
3. **`tie: …`** — two candidates within `tie_margin`.
4. score under `auto_threshold` (0.75). `dl-route match …` shows the candidate
   list. Prefer adding an alias over lowering the threshold — the threshold is
   what keeps a wrong guess out of a subject directory.

**"Everything from this chat channel / forum thread opens the picker."** That is
the FIRST download from it, by design. Confirm it once and the identity alias is
written; later downloads match at 1.00 with nothing scraped.
`dl-route alias review` shows evidence, provenance and hit count per row.

**"It learned something wrong."** `dl-route alias review` flags global and
suspicious rows, lists every **refused** candidate with its reason and recurrence
count, and prints the exact removal command. A refused candidate never
auto-files — if one is a real subject, `dl-route alias set '<phrase>' '<Dir>'
--site <host> --force`. A performer directory never learns a tag, and nothing is
ever learned at global scope, so a bad row means either a manual `alias set
--force` or a category confirmation.
```bash
dl-route alias rm '<key>' --site '*'          # `*` == global, as listed
dl-route alias rm 'discord:<channel id>' --site discord.com
```

**"The undo / `change` button says it could not move the file."**
`/relocate` refuses anything it cannot prove this router created — the library
root is a live seeding target and the move is an `os.rename`. It needs the file's
name to match that download's (modulo `uniquify`'s ` (1)`) **and** the file to be
no older than the routing decision. Two refusals are by design, not bugs:

* the file was already on disk (it predates its own routing decision);
* **the download was never routed** — the sidecar was unreachable when it
  started, so `/match` never ran for it (or no `downloadId` was sent, or the
  route log was cleared). A restart does *not* cause this: the route log is
  persistent SQLite and every decision is committed. No fallback here on purpose:
  with no record to check against, anything else would just be trusting the
  caller. Move that one file by hand.

`dl-route log` shows the decision it is checking against.

**"It files things into the catch-all folder."** Designed below-threshold
behaviour: an unconfident download must not pollute a subject directory, and the
picker's Esc costs nothing. Fix the *match*, not the fallback.

**"A site's tags are not being picked up."** Generic extraction (Open Graph,
JSON-LD `Person`/`VideoObject`, `[itemprop=name]`, `meta[name=keywords]`) runs
everywhere. For a site that needs more, add a rule — **config, not code**:
```toml
[site_rules."example-site.test"]
subject = ["a.performer-name"]
tags    = [".tag-list a"]
```
Then `systemctl --user restart dl-router` so the snapshot carries the new rules.
Selector subset: tag, `.class`, `#id`, `[attr]`, `[attr="v"]`, `[attr^="v"]`,
descendant combinators, comma groups.

**"The extension seems dead / changes did nothing."**
1. `dl-route status` — is the sidecar up?
2. Options page → *Test connection* — port and token right? Is *Enable routing in
   this profile* ticked? It is **per-profile** and off by default.
3. The extension is stale — see **FULL Brave restart** under Gotchas.

**"The picker opens in a separate window instead of in the page."** Designed
fallback, not a fault — **a picker that never appears at all is a real fault; a
windowed one is not.** The overlay needs a content script in the tab and a frame
that boots, so it falls back for `brave://`/`chrome://`, the PDF viewer, the Web
Store, `view-source:`, `file://`, a tab that already closed (the self-closing
file-host tab), a page still loading, and any site whose CSP blocks a
`frame-src`. Check the tab's URL first.

It also **converts back to a window** if the overlay stops existing (tab closed
or navigated, page removed the node, a second download needed the same tab) —
the safety net, not a bug: the alternative is a download nobody was asked about.

**If the overlay NEVER works on any site**, suspect `use_dynamic_url` on the
`web_accessible_resources` entry: the framed page's ES-module imports have to
resolve under the rotating origin, which is why `picker.js`, `sanitize.js` and
`route_core.js` are listed next to `picker.html`. Never exercised in a browser.
It fails safe — the frame never boots, gate 2 fires, every picker becomes a
window — so the symptom is "always windowed, never in-page". Drop
`use_dynamic_url` to test the hypothesis; the per-open id still authorises picks
either way.

**"Downloads still show a Save-As dialog."** The profile's
`download.default_directory` is not the library root, or `prompt_for_download` is
still on. Re-run `setup-brave-profile.sh` with Brave **fully closed** — it
refuses otherwise, because Brave rewrites `Preferences` on exit. "Closed" means
*this* profile: the guard asks whether any live process is using this
`--user-data-dir` (open fd / main-process cmdline / live `SingletonLock`), so
headless automation on a throwaway `/tmp` profile does not block it, nor does a
stale lock from a crash. It names the pid to quit. `--list` and `--dry-run` write
nothing and are never gated.

## Player buttons / embedded video downloads

Per-player download buttons let you save embedded video directly from an `<video>`
element (e.g. `example-embed.test` iframes embedded on a forum page like
`someforum.test`). This uses a **two-layer rule system**:

| Rule type | Keyed on | Purpose |
|---|---|---|
| **context rules** | PAGE host (the top-level page) | Extract subject/tags from the page the video is embedded on |
| **player rules** | EMBED host (the iframe serving the video) | Locate the `<video>` element and extract the media URL |

The content script runs **inside the OOPIF** (out-of-process iframe) — that is
where the `<video>` element lives, which is why player rules are keyed on the
embed host, not the page host.

```toml
[site_rules."example-forum.test".context]
subject = [".p-title-value"]

[site_rules."example-embed.test".player]
container = ".plyr"                                  # wrapper the button mounts into
media = { element = "#main-video", attr = "src" }    # <video>/<source> element + attr holding the URL
mount = ".video-wrapper"                             # where the button is inserted
label = "Save to library"                            # button text
```

Find the embed host with `browser frames` (it is the host serving the iframe, not
the page embedding it), then inspect inside it for the video element structure.

**`media` may also be an ORDERED LIST — that is how ONE rule covers an image
AND a video.** `attr` is a single NAME, so a lone pair cannot say "the image's
anchor `href`, but the video's own `src`". The list is tried in order, first
http(s) hit wins, so put the accessor resolving the **best** copy first:

```toml
[site_rules."chat.example.test".player]
container = "[class^=mediaWrapper]"
media = [
  { element = 'a[href^="https://cdn.example-cdn.test/attachments"]', attr = "href" },
  { element = "video", attr = "src" },
]
```

An image's own `src` is often a **downscaled copy from a resizing proxy**, which
is why the anchor accessor goes FIRST in the list above: it is how this rule
reaches the original. 🔴 **That accessor is a DESCENDANT query and it does not
need a wrapping `<a>`.** `safeQuery` resolves each `element` with
`container.querySelectorAll` (`player_buttons.js`), so the anchor only has to be
a descendant of `container` — it does not have to be an ancestor of the image.
⚠ **Confirm your `container` actually encloses it.** What was measured is the
NEGATIVE half only (0 ancestor `<a>` of 3); the origin anchor was nine levels
away from the image, and whether it falls inside any particular `container`
selector has never been measured. Do not delete this accessor on the strength
of the context-menu note below:
🔴 **the player-button path does NOT rewrite anything.** `playerDownload` hands
its `mediaUrl` straight to the download API and never calls
`originalFromPreview()`, so for THIS path the anchor accessor is the only route
to the original.

### The "Save to library…" CONTEXT MENU is a different path with a different rule

🔴 **Scoped to `info.linkUrl`, and it does NOT apply to the `media` list above.**
A browser fills a context menu's `linkUrl` only from an ANCESTOR link, and on
the chat site this was built for an image attachment has none. MEASURED
2026-09-03 on the live client, 3 image attachments across 2 channels and 2
message shapes: ancestor `<a>` elements between the image and its message,
**0 of 3**. The origin anchor is a sibling — reachable by a descendant query,
invisible to `linkUrl`. So the menu path cannot wait for a link and must
rewrite instead.

🔴 **For the menu path, rewriting the proxy URL's host IS the supported route,
and this file used to say the opposite.** It claimed the two hosts carry
different signature parameters; they do not. Measured for one asset: the proxy
URL carries the signature parameters **plus** the resize knobs, with the
signature **values byte-identical** to the origin anchor's, so the rewrite keeps
a valid signature. Probed with both controls — the message's own origin anchor
**206**, the rewritten URL **206**, the same URL with the signature stripped
**fails**. `originalFromPreview()` in `route_core.js` is the one implementation
**of the rewrite**; do not open-code a second.

⚠ **A video's `src` is USUALLY the origin already, but that is not guaranteed
and was measured at zero points** — every video in the live route log was
already on the origin host, so the rewrite simply does not fire for them. It
*will* fire on a proxy-host video src, and that shape is unverified.

⚠ **The menu path has no PLAYER rules — but it is not rule-free.** It is
governed by `[site_rules."<host>".context]`, which `content_capture.js` reads
on the `contextmenu` event; those `subject`/`tags` selectors go in first as the
most specific signal, and the sidecar's `/match` weighs them against the title,
Open Graph, link text and the url-derived signals. So a context rule does not
by itself *decide* the directory — it is the strongest lever a config author
has. 🔴 **If menu downloads keep landing in the catch-all, that context rule is
the first thing to add.**

### Player button details — everything below is about the BUTTON, not the menu

🔴 The heading above closes the context-menu subsection. Without it, this
block, the troubleshooting list and the DEPLOY ORDER note all read as
context-menu guidance — and the menu path has no player rules, no accessors and
no buttons, so every word of it would be filed under a path it cannot apply to.

**Important details**
- The media URL is **signed and rotates** — `player_buttons.js` reads it **at
  click time**, never caches. A stale URL will fail.
- The "Already have this" badge checks the **source URL ledger** on mount
  (`GET /have?url=…`).
- Double-clicks are prevented via `chrome.storage.local` — the button disables
  after click until the download is confirmed or the tab changes.
- Only **HTML5 video with accessible `<video>` elements** is supported. DRM or
  non-standard players (e.g. nested shadow DOM) will not work.

**"Buttons don't appear"**
1. Both context AND player rules must be present in `site_rules` config.
2. Verify the embed host matches the rule key exactly (`browser frames` confirms
   the iframe origin).
3. A **full Brave restart** is required after changing player rule config.
4. 🔴 **ONE malformed accessor kills the WHOLE rule** — check every entry, not
   just the one you edited. Deliberate: a partial button covers only some media
   on the page and looks like the feature working. **Where you see the failure
   depends on WHICH kind of malformed**, and the quiet one is the one to know
   about:
   - a **type/shape** error (empty or missing `element`/`attr`, a non-string, a
     non-table entry, more than 8) is caught by the **sidecar**, loudly — it
     names the offending index, e.g. `player.media[1].attr must be a non-empty
     string`, and surfaces in `dl-route status` and `/healthz`. Look there
     first.
   - a **grammar** error the sidecar deliberately does not check (an
     unsupported selector — `>`, `+`, `~`, `:`, over 300 chars — or an `attr`
     that is not a bare name) is caught only in the extension, which returns no
     rule: **no button and no error anywhere.** That one is silent.

🔴 **DEPLOY ORDER, and it is not symmetric.** The two halves ship by different
mechanisms: the extension loads **unpacked from the working tree** (a `git pull`
plus a **full Brave restart**), while the sidecar runs **from the nix store**
(`home-manager switch` only). Writing a list rule while the OLD sidecar is still
live is a `ConfigError` → `load_degraded` → `library_root` unset → **every
routing endpoint 503**, not just the button. So: **switch first, write the rule
second.** Reverting is the mirror image — **remove the list rule from
`config.toml` BEFORE rolling the sidecar back**, or the rollback takes the whole
sidecar down. `dl-route status` reporting `library (unset …)` right after a
config edit is this, not a new fault.

## Backfill — the one dangerous path

The library root is a **live qBittorrent seeding target**. Moving a torrent
payload with `mv` breaks seeding.

```bash
dl-route backfill plan                                  # READ-ONLY
dl-route backfill plan --seed-aliases                   # ...and persist aliases
dl-route backfill apply --manifest <path>.tsv --dry-run
dl-route backfill apply --manifest <path>.tsv
```

* `plan` writes nothing — not into the tree, and **not into the alias database**.
  It uses the aliases it would seed in memory; `--seed-aliases` persists them.
* **The TSV is the reviewed artefact.** Edit the `action` column to `SKIP` a row
  and it takes effect — `apply` reads the TSV. Pointing `apply` at the `.json`
  after editing the TSV is refused, not silently ignored.
* `apply` refuses to run without an explicit manifest, and **re-derives every row
  against live qBittorrent** before touching anything (the manifest's
  `move`/`torrent_hash` are plan-time values). A disagreement aborts the run and
  asks you to re-plan. Credentials are needed whenever anything is going to move,
  not just for `qbt` rows.
* Torrent-backed rows move via **`torrents/setLocation` — never `mv`** — and are
  re-verified afterwards, **waiting out the `moving` state** (setLocation returns
  before the payload has arrived). Any failure aborts the remaining rows.
* **Absence of proof is never proof.** A row is `SKIP` if qBittorrent is
  unreachable or the path mapping cannot be derived; no row may be `fs` if the
  torrents' FILE lists could not be read, because a no-root-folder torrent's
  payload sits directly at the library root. Correct, not a bug — fix the
  credentials in `config.toml` rather than working around it.
* The path mapping is derived at runtime from `torrents/info[].save_path`, needs
  more than one corroborating torrent, and must be able to express the library
  root. Do **not** hardcode it and do **not** read it from qBittorrent's stored
  config — its `LastSavePath` points at a mount that no longer exists.
* The only signal that may carry a row is an explicit **alias** on the filename
  stem. The filename itself is capped at 0.50 (spec section 7), so a
  filename-only row can never auto-file; the `signal` column says which it is.
* Never point `apply` at the real tree to "see what happens". Tests cover it on
  temp trees with a fake qBittorrent.

## Changing the code

```bash
# from the repo root — both commands are exact, see the two notes below
nix-shell -p 'python312.withPackages(ps:[ps.pytest])' --run "python3 -m pytest scripts/dl-router/tests -q"
nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"
home-manager switch --flake ~/workspace/devrc --impure   # restarts the sidecar
```

* `python312.withPackages(ps:[ps.pytest])`, **not** `python312Packages.pytest` —
  the latter only works when the ambient `python3` happens to be the matching
  minor version.
* The node glob **must be quoted**. `node --test scripts/dl-router/tests` treats
  the directory as one test file and reports a bogus failure.

Editing the sidecar requires a `home-manager switch` (it runs from the nix
store). `SKILL.md` and `dl-route` are out-of-store symlinks and track the working
tree immediately.

**Deploying a matching change is TWO steps, not one.** The extension carries its
own copy of the matcher (`route_core.js`) for the cached fallback, so a
`home-manager switch` alone leaves the OLD service worker running with the old
rules — including, after the directory-kinds change, a `localDecide` with no kind
gate, which will keep auto-filing from cache into a directory you have just
reclassified as a category. Finish with a **FULL Brave restart** (Gotchas), then
re-check `dl-route status`.

### Invariants the tests exist to protect — do not weaken them

* **`suggest()` is called exactly once on every path and never hangs.** Chrome
  falls back to the default filename if the listener is slow. The timer racing
  the sidecar plus an idempotent `finish()` is what makes this true.
* **Every page-derived string is validated before it becomes a path.**
  `safety.py` and `extension/sanitize.js` must agree; the same hostile-input
  table (`tests/fixtures/name_cases.json`) is asserted against both. After
  touching either, re-run the differential fuzzer — it must print
  `0 divergence(s)`:
  ```bash
  nix-shell -p nodejs python312 --run "python3 scripts/dl-router/tests/difffuzz.py"
  ```
* **Identity signals must agree across the two languages too.** `matcher.py` and
  `extension/route_core.js` both derive a Discord channel id and a forum thread
  slug from a URL, and the extension's copy runs exactly when the sidecar is
  unreachable — so a divergence is invisible until it misfiles.
  `tests/fixtures/url_cases.json` is the one table both suites assert. Add a row
  there before adding a URL shape to either implementation.
* **Only a `performer` directory may auto-file**, in the cached fallback as well
  as in the sidecar. Weakening the gate on one side only re-creates a divergence
  that shows up solely when the sidecar is down.
* **Nothing is ever learned at global scope**, and a performer directory never
  learns a tag. That is the fix for the mislearning incident, not a preference.
* **yt-dlp is invoked as an argv list with a validated http(s) URL and a `--`
  terminator** — never a shell string. The URL comes from a web page.
* **The sidecar refuses any non-loopback bind**, with no override.
* **A duplicate is never acted on automatically.** The file is kept and filed;
  the toast asks. `POST /discard` is refused unless all five proofs hold, the
  refusal is surfaced (toast **and** notification), and the default is a move
  into `.dl-router-trash/`. Making any part of this implicit turns a warning into
  a data-loss mechanism.
* **A schema migration must be additive, idempotent and re-runnable after a crash
  between the DDL and the `PRAGMA user_version` bump.** sqlite3 autocommits DDL,
  so there is no transaction around a migration; every step is `IF NOT EXISTS` or
  goes through `ADD_COLUMN_IF_MISSING`. This has bitten here before (v2) and is
  pinned per version.
* Tests never contact the live qBittorrent instance or touch the real library.

## Gotchas

* Port **8791** — 8790 is already taken on the workbench.
* `onDeterminingFilename` **cannot escape the download root**: no `..`, no
  absolute paths. That is precisely why the profile's download directory has to
  *be* the library root.
* **An extension change needs a FULL Brave restart**, not `↻` on the extensions
  page — same lesson as browser-bridge. A reload often leaves the old service
  worker alive (the long-poll keeps it running). The manifest version is bumped
  on every code change specifically so `brave://extensions` can be checked: if it
  still reads the old number, the restart did not take.
* Both hosts are hostname `nixos` — check `dl-route status` to know which one you
  are on.
* Existing directories are **never renamed** (three naming conventions coexist;
  the matcher folds them). New ones are Title Case and never created silently.

### Picker counts / the `/dirs` ETag
* **Per-directory counts are not covered by the `/dirs` ETag**, on purpose: they
  change on every download and `FileIndex` is TTL-cached, so including them would
  make the ETag change when the routing configuration had not. `dl-route
  status`'s `etag` therefore still answers "did the routing config change?". The
  picker's own snapshot request skips `If-None-Match` so it still sees fresh
  counts; nothing else does.
* Counts are **suppressed entirely when the file index hit its `file_index_max`
  cap** — a partial tally of an unknown fraction of the library rendered next to
  a directory name would be a wrong number, which is worse than none.
* Counts are **empty until the file index has been walked**. `/dirs` never starts
  that walk (a whole-tree walk there would blow the extension's 4 s snapshot
  budget on a large library); `/match` warms it on every download. A picker with
  no counts means no `/match` has run in this sidecar process yet.

### Dedupe and `/discard` (the destructive path)
* **Dedupe is size-first, and the hash is deliberately NOT on `/match`.** At
  `onDeterminingFilename` time the downloaded file does not exist, so there is
  nothing to hash and `totalBytes` is often `0`. `/match` reports a *possible*
  duplicate from the free size bucket; `POST /dedupe` after completion is the
  authoritative answer, and that is where all the I/O lives — outside the 400 ms
  budget by construction, not by tuning. Do not move it onto `/match`.
* **The head+tail digest samples 128 KiB from each end, never the middle.** Two
  files of the same length that differ only in the middle read as duplicates.
  That is the price of a constant-cost check on multi-GB media, and it is only
  affordable because the answer is a warning with a `keep` button. If a delete is
  ever made automatic, this bound stops being acceptable.
* **SAMPLING IS A WARNING; THE DELETE IS GATED ON A FULL COMPARISON.** This is
  the single most important invariant in the subsystem and it took three rounds.
  `/dedupe` samples (head + tail + eight 128 KiB mid-file windows); `/discard`
  reads BOTH FILES IN FULL and compares them byte for byte. Do not "optimise"
  that back into a digest comparison — no bounded read proves two multi-GB files
  identical, it only fails to disprove.
* **`POST /discard` is the only destructive path, and the SEEDING guard is the
  payload check — not the trash.** qBittorrent seeds by PATH, so a rename into
  `.dl-router-trash/` breaks a torrent exactly as `unlink` would. `/discard`
  refuses a hardlinked file (`nlink > 1` — the standard payload-into-subject-dir
  layout), a symlink, a sparse file, and — when qBittorrent credentials are set —
  anything live state calls a payload or cannot corroborate at all. Creds are
  deliberately empty on this host, so the three local checks carry it there.
  `backfill apply` demands the same corroboration for a REVERSIBLE move; do not
  let `/discard` end up weaker than it again.
* **`st_blocks` cannot see a fallocated partial.** qBittorrent's "pre-allocate
  disk space for all files" uses `posix_fallocate`, which reserves REAL extents:
  identical size, identical block count, identical head and tail as the finished
  file. Only reading the middle separates them — an all-zero mid sample is an
  unfilled extent. The sparse check catches only the `ftruncate` shape; it is not
  the guard, it is one of several.
* **A verification that runs out of budget is a REFUSAL.** `files_identical`
  returns True / False / **None**; None is "could not determine" and must never
  collapse into either answer. Same for a stat that cannot answer
  (`_looks_preallocated` fails CLOSED).
* **One routing decision authorises ONE discard.** The route row is consumed
  (`discards` table, v6). Unconsumed evidence is a capability, not a proof — one
  downloadId used to remove `new.mp4`, `new (1).mp4` and `new (2).mp4` in turn,
  because `names_match` tolerates the ` (N)` suffix by design.
* **The kept file must PREDATE the routing decision** by at least
  `MTIME_SLACK_S`. That timestamp is the only thing distinguishing the two halves
  of a uniquify pair; the two mtime windows are deliberately disjoint so no file
  can satisfy both. Without it `/discard` could remove the ORIGINAL.
* **Same file = same inode.** The self-check compares `(st_dev, st_ino)`, **not**
  resolved paths — `resolve()` collapses symlinks but not hardlinks. Don't
  "simplify" it back to a path comparison.

### The trash
* **Unbounded and invisible to `dl-route status`.** Nothing sweeps it, nothing
  reports its size, and both index scans skip it by design. Check by hand
  (`du -sh <library root>/.dl-router-trash`) if space goes missing. A
  cross-filesystem library root cannot use it: the move fails closed on EXDEV
  rather than degrading to a non-atomic copy-then-delete.
* **Hidden on purpose**: both index scans skip dot-prefixed names, so its
  contents never become dedupe candidates or routing targets. Do not rename it to
  something visible.
