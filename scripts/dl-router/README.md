# dl-router — media download router

Files browser downloads straight into the right subject directory of a local
media library, using **page context** rather than the filename, and asks only
when it is unsure.

The manual flow it replaces: click a download link → Save-As dialog → navigate
the picker to the right subdirectory → save. The evidence it was worth
automating: the browser profile's save directory was being re-pointed at
individual library subdirectories by hand.

---

## How it works

```
  page click ──► content_capture.js ──► service_worker.js ──► /match ──► sidecar
   (tags, og,     (all frames,          (correlate download        (dir index,
    JSON-LD)       capture phase)        to context, then           aliases,
                                         suggest() once)            matcher)
                                              │
                                              ▼
                                  suggest("<dir>/<name>")
                                              │
                          auto-filed ─────────┴───────── unsure
                                │                          │
                          toast + undo            picker (type/↑↓/Enter/click)
                                └──────── correction ──────┘
                                              ▼
                                    /learn → alias + example
```

**Direct write, no copy, no move.** The browser's download directory *is* the
library root, and the extension answers Chrome's `onDeterminingFilename` with
`"<subject dir>/<name>"`. Nothing is written twice and there is no post-hoc
move to race with.

**A sidecar owns the brain.** The directory index, alias table, dedupe and the
matcher live in a loopback service, not the extension. The extension keeps a
cached snapshot, so a sidecar outage degrades to "route from cache" — never to
a hung download.

**Deterministic matching, no LLM.** Ordered rules over a normalisation key that
folds the three naming conventions that coexist in a real library
(`Title Case`, `lower-kebab`, `snake_Case` → the same key), which is why
existing directories are never renamed.

---

## Layout

| File | Role |
|---|---|
| `server.py` | loopback HTTP sidecar (127.0.0.1:8791, bearer auth) |
| `matcher.py` | deterministic scoring — no LLM, no network, no I/O |
| `safety.py` | the one place a page-derived string becomes a path component |
| `store.py` | SQLite: aliases (+ provenance), examples, route log, host prior, picker-assigned kinds |
| `dirkinds.py` | performer/category classification + the draft generator |
| `dirindex.py` | mtime+TTL-cached scan of the library root; whole-tree file index for dedupe + per-directory counts |
| `qbt.py` | qBittorrent WebUI client + runtime-derived path mapping |
| `fetcher.py` | yt-dlp jobs for HLS/DASH sources |
| `backfill.py` | `plan` (read-only) / `apply` (reviewed manifest only) |
| `config.py` | `~/.config/dl-router/config.toml` loader |
| `dl-route` | CLI |
| `setup-brave-profile.sh` | the one-time browser profile change |
| `extension/` | MV3 extension (separate from browser-bridge) |
| `tests/` | pytest + `node --test` — fully headless |

Why a **separate extension from `browser-bridge`**: different lifecycle and
blast radius. A bug in download routing must not take down the agent command
channel. It reuses browser-bridge's *patterns* (token file, loopback bind,
systemd user unit, test layout), not its code path.

---

## Matching

Normalisation key: NFKD → strip diacritics → casefold → drop non-alphanumerics.

| Rule | Score |
|---|---|
| identity-signal alias hit (Discord channel, forum thread) | 1.00 |
| exact alias hit, site-scoped | 1.00 |
| exact alias hit, global | 0.90 |
| normalised page tag/subject == directory key | 0.85 |
| token-sequence containment, scaled by coverage | 0.60–0.80 |
| filename token match | ≤ 0.50 |
| host prior (last directory used on this site) | +0.05, display only |

### Identity signals — the deterministic half

Page scraping has a hard floor: a single-page app gives a content script
nothing to read. The first evening of real traffic hit it — most downloads came
from a chat CDN whose captured context was completely empty (`tags: []`,
`title: ''`, `pageUrl: ''`), and scored 0.00.

But the URL is structured. `identity_signals()` derives site-scoped alias keys
from it and nothing else:

| Signal | Key | Scope |
|---|---|---|
| Discord attachment (`cdn.discordapp.com`, `media.discordapp.net`) | `discord:<channel id>` | `discord.com` |
| forum thread slug, **anchored** to a thread route | `thread:<slug>` | that forum's host |

A slug is the path segment immediately after an **unambiguous** thread route
(`/threads/`, `/thread/`, `showthread.php`, `viewtopic.php`) — and nowhere else.
`/topic/`, `/topics/` and `/t/` are deliberately not anchors; see below. That is positional on
purpose. The first version took "the deepest segment with ≥2 words", and when a
thread's own slug was a single word the *section* one level up won instead
(`/forums/general-discussion/threads/aster.99/` → `general discussion`), so one
correction taught the router that an entire forum section meant one directory,
at 1.00, with auto-file. A superlative over candidates ("deepest that
qualifies", "longest that survives") picks the wrong thing exactly when the
subject is short, and short subjects are the common case.

Identity keys are **near-verbatim**: stopwords are kept, because
`aster-vale-new-set` and `aster-vale-set` folding to one key silently repointed
a live alias.

The first download from a channel or thread has no signal, opens the picker,
and confirming it writes the key. Every later download from the same channel
matches at **1.00 with nothing scraped from any page** — so a UI change on the
source site cannot break routing that already works.

The **same function** produces the keys the matcher looks up and the keys the
learner writes, so "what we match on" and "what we learn" cannot drift apart.
The URL table is a shared fixture asserted against *both* `matcher.py` and
`extension/route_core.js`.

**Subject ordering** follows from the same evidence: the URL thread slug leads,
then the page title with the site's own branding removed (**the first**
surviving segment, not the longest — "longest wins" returned the *site* name
whenever the subject was one word and the site's display name did not resemble
its hostname), and the tag list trails both. On the one forum download that *did* capture context, the tag list
was the forum's section names and other posters' usernames while the subject
sat in the slug and the title.

### Cross-host referrer carry

A download from a paired file host has no context of its own — the page is a
download button. The forum thread that sent the user there does have one, and it
is carried **only when the link is provable**, and there is exactly one proof:
a captured click on another page whose `href`/`mediaSrc` is this page. When the
opener tab is known, the donor must additionally be in it — `openerTabId` can
only *narrow* the proof, never supply one.

An opener-tab-only branch existed briefly and was **deleted, not tightened**.
It took the newest capture from the opener tab with nothing binding it to the
navigation that opened the tab, which is "the last thread I saw in that tab"
wearing the word "provable" — and it went wrong on the ordinary pattern of
opening a file host in a new tab and carrying on browsing. There is no time
window either: a window is a guess about how fast someone browses. An
unprovable link goes to the picker, which is the point.

### Directory kinds

The library is not purely subject-keyed. `~/.config/dl-router/dirs.toml` (never
committed) classifies each directory:

```toml
performer = ["Ada Lovelace"]     # a person or group
category  = ["Field Recordings"] # unattributed material, filed by topic
```

* **Only a `performer` directory may auto-file.** A `category` always opens the
  picker whatever it scores — a tag legitimately identifies the category, but a
  tag is a weak claim about any *one* file.
* **An unclassified directory never auto-files either.** Absence of a
  classification is not permission.
* `dl-route dirs classify` drafts the file from the live index with the reason
  on every line, so the review action is "move this line", not "type every
  directory name". **Everything starts in `category`** — nothing available to a
  generator distinguishes a person's name from a topic, and `category` is the
  side that asks, so a skimmed review leaves directories that ask too often
  rather than directories that auto-file into the wrong place.
* Creating a directory through the picker **asks which kind it is** — otherwise
  the new directory would silently interrupt every future download into it.

Guards:

* a fuzzy hit needs **≥2 tokens, or one token of ≥4 characters** — otherwise a
  short directory name matches random page prose;
* the **host prior is never decisive**. Candidate ordering, the auto-file
  threshold and the tie margin all read the **pre-bonus** score, so the prior
  cannot create a candidate, cannot change which candidate wins, cannot carry
  one over the threshold, and cannot manufacture the margin that would suppress
  the tie-break. All it may do is put its directory first among candidates that
  are *already exactly tied* — and such a pair is inside the tie margin by
  definition, so the picker opens anyway. The `+0.05` survives on the candidate
  list purely so the reason string can show the prior was consulted;
* the top two within `tie_margin` → picker, never a coin flip;
* `reason` is always returned and shown in the toast, so a wrong match is
  diagnosable rather than mysterious.

Auto-file threshold defaults to **0.75**. Below it the download lands in the
catch-all directory and the picker opens — an unconfirmed guess never quietly
pollutes a subject directory, and the picker's Esc is then a no-op rather than
a move.

**Dedupe** checks the target directory and the whole-tree index for the same
normalised filename (and `(size, name)`). It **warns and never blocks or
overwrites** — `conflictAction: "uniquify"` handles real collisions.

Route provenance lives in SQLite only. There are deliberately **no
`.dlmeta.json` sidecar files**: extra files inside the media directories would
pollute them and risk confusing qBittorrent and media scanners.

---

## Learning — only the discriminating signal

A correction is the only place the router gains knowledge, so it is also the
only place it can gain *wrong* knowledge. The first version wrote an alias for
the first three subject phrases of the context plus one at global scope; on a
forum page that meant a section name and two other posters' usernames, one of
them global. Four rows had to be deleted by hand, and nothing surfaced them.

What `/learn` writes now depends on what the directory **is**:

| Directory kind | Learns |
|---|---|
| `performer` | identity signals (Discord channel, thread slug) and a subject name found in the page title. **Never a tag.** |
| `category` | the above, plus tag → directory aliases — site-scoped, capped, and only from an explicit confirmation |
| unclassified | identity signals only |

**The correction path never learns a global alias.** A global alias applies on
every site at once: the widest blast radius the store has, and the least
evidence supports it. Two deliberate, explicitly-invoked exceptions remain, and
both show up flagged in `alias review`: `dl-route alias set --site '*' --force`,
and `backfill plan --seed-aliases` (which has no site to scope to — the seeds
come from directory and torrent names, not from a page).

**The catch-all learns nothing at all.** Sending a download there is "not any
of these" — the absence of a subject rather than evidence of one.

Every candidate key is screened first — **including the identity signals**. A
badly derived identity is still an identity as far as the store is concerned,
and it lands at 1.00 *with* auto-file rather than at 0.85 without, so the worst
row the router can write must not be the one row nothing checks. Structured
keys skip only the two rules that describe a word rather than a source
(minimum length, handle shape).

The rules describe the *failure class* rather than the four strings that caused it — and every rule is measured
from data the router already holds, because a vocabulary of "bad words" would
be both unmaintainable and un-committable to a public repo:

* shorter than 4 characters;
* seen on **two or more different directories** in the labelled examples — the
  signature of site chrome (a section name, an uploader's username), which
  appears on everything, while a subject tag appears on one directory's pages;
* part of the **site's own name**;
* every word of it already appears in two or more library directories, which is
  what a taxonomy reads like and a person's name does not;
* it reads as a handle — a word with a number stuck on the end
  (`poster1988`). Narrowed from "a single token containing any digit", which
  refused legitimate stage names.

A refusal is a **durable fact, not an event**. It is recorded in the store
against its `(key, site, directory)` and listed permanently by
`dl-route alias review` — which shows both halves: what was learned (evidence,
provenance, hit count, riskiest first, global and suspicious rows flagged) and
what was *refused* ("these will NEVER auto-file", with the reason and how many
times it has recurred). An over-strict screen that nobody can see is the same
failure as an over-loose one, so both are inspectable in one command.

The extension notifies **once per fact**, the first time a refusal is recorded.
That shape was arrived at the hard way: three successive attempts filtered the
*event* harder — the notification was first silent, then fired on every routine
catch-all filing, then fired forever for a permanently-refused identity — and
each fix carried the next defect, because the event is the wrong unit. What is
worth saying is "this source will never be learned", which is true once. A
suppression map in the extension cannot hold that either: MV3 tears the service
worker down after ~30 s idle, so the map would empty and the notifications
would resume. The store is the only durable place, and the notification is
demoted to a one-time pointer at `alias review`.

---

## The extension

**Context capture** (`content_capture.js`, all frames, capture phase) snapshots
`{href, mediaSrc, linkText, alt, pageUrl, pageTitle, tags[], og{}}` on
mousedown/click/contextmenu over `a[href]`, `img`, `video`. Tag extraction is
data-driven: Open Graph, JSON-LD `Person`/`VideoObject`, `[itemprop=name]`,
`meta[name=keywords]`, plus a **per-site rules table from config** — adding a
site is config, not code.

**Correlating a download to a context.** A `DownloadItem` carries no `tabId`,
so there is a three-tier ladder:

1. exact match on `item.url`/`item.finalUrl` against a captured `href`/`mediaSrc`;
2. `item.referrer` equals a captured `pageUrl`;
3. most recent capture from the active tab within `capture_window_s`.

**The `onDeterminingFilename` ladder** — `suggest()` is called **exactly once**
on every path and never hangs (Chrome silently falls back to the default
filename if the listener is slow):

1. fire `/match` with a 400 ms timeout **and** compute a decision synchronously
   from the cached `/dirs` snapshot;
2. sidecar answers in time → use it; times out or errors → use the cached
   decision; no cached snapshot → the catch-all directory;
3. sanitise the directory (must be a known one, one path component, no
   traversal/control/bidi characters) and the filename separately, then
   `suggest({filename: dir + "/" + name, conflictAction: "uniquify"})` — never
   `overwrite`.

**The picker is an in-page overlay, with the popup window as an automatic
fallback.** The overlay is an iframe of `picker.html` inside a **closed** shadow
root, injected by `picker_overlay.js` — a second file on the content-script
declaration `content_capture.js` already has, so it needs no new permissions.
Framing an extension page from a web page does need `picker.html` in
`web_accessible_resources`; its module imports are not exposed.

It is the **same page, running the same reducer**. The overlay does not
reimplement the picker — that is the point, and a test asserts
`picker_overlay.js` contains none of the picker's vocabulary. Being a separate
document also means page CSS cannot reach the picker or vice versa; the shadow
root is what stops the page styling or discovering the host element.

Delivery is decided per download, with **two gates**, and every failure falls
back to the window:

1. **the content script answers.** `chrome.tabs.sendMessage` rejects with
   "receiving end does not exist" where no content script runs — one check
   covering `brave://`/`chrome://`, the PDF viewer (an ordinary `https` URL
   whose document is a plugin), the Web Store, `view-source:`, `file://`, a
   discarded tab, and a page that has not reached `document_idle`. A tab that
   has *closed* — the self-closing file-host tab — fails one step earlier, at
   `chrome.tabs.get`.
2. **the frame reports itself ready.** A content-script-injected iframe is
   subject to the *page's* CSP, so a strict `frame-src` blocks the load while
   every DOM call in gate 1 still succeeds. Only an extension context that
   actually booted can send `dlr:picker-ready`. Without this gate such a site
   would leave an empty overlay and no window — a download with no picker,
   which is the one outcome that is not allowed.

**Gate 2 proves the frame booted. It proves nothing a millisecond later.** An
overlay is a node in a document the extension does not own, and `openPicker`
has already returned true — so every way it can vanish has a route back to a
window, or the download ends up unasked:

* the tab **closes** (`chrome.tabs.onRemoved`) or **navigates**
  (`chrome.tabs.onUpdated`, keyed on `status === "loading"` so a single-page
  app's `pushState` does not yank the picker into a window);
* the **page removes the host node** — `document.body.innerHTML = …`, a DOM
  sanitiser, a framework re-render, or hostile script. A `MutationObserver` on
  `body`, armed only while an overlay is live, reports `dlr:overlay-lost`.
* a **second download into the same tab**. The content script keeps exactly one
  overlay and evicts the incumbent, so the worker refuses the second overlay
  outright and gives it a window: two questions, both asked, neither lost.

Re-delivery is idempotent — the record is removed first, so two triggers
produce one window — and it re-asks the *same* question, from the `info` the
worker kept.

The overlay is **never dismissed by an outside click or by losing focus**. That
is exactly why `chrome.action.openPopup()` was rejected: an action popup
dismisses on blur, silently discarding the pick and leaving the file in the
catch-all. The iframe cannot close itself, so the picker sends
`dlr:picker-closed` and the worker tears the overlay down — falling back to
`sender.tab.id` so a pick made after an MV3 teardown still removes it, and the
content script sweeps a stray host by id if it was re-injected in between.

The overlay's tab is **raised** (`tabs.update` + `windows.update`) once it is
up: the popup window it replaces was created `focused: true`, and the toast's
`change` makes this concrete — the user is looking at the toast's own window
while the overlay goes to the download's tab.

**Two mitigations against a page framing the picker.** `picker.html` has to be
web-accessible to be framed at all, so a page could otherwise embed it, point it
at a recent download id, and clickjack two clicks — one to take the "+ new dir"
row, one to answer the kind prompt — into a `/mkdir` and a `/relocate`.
Click-to-select is what made two blind clicks sufficient.

1. **`use_dynamic_url: true`** rotates the resource URL per browser session, so
   an arbitrary page cannot construct the URL to frame in the first place. The
   framed page's own module graph (`picker.js`, `sanitize.js`, `route_core.js`)
   is listed alongside it, because under a rotating origin its relative imports
   resolve against that origin.
2. **A pick from an unrecognised subframe is refused.** Our own overlay is a
   subframe too, so the discriminator is the per-open id: unguessable, issued by
   the worker, and never visible to the page because the shadow root holding the
   frame is **closed**. The popup-window picker is the top frame of its own tab
   and carries no id, which is why the test is on `frameId` rather than on an id
   being present.

The second is the authorisation check; the first removes the ability to load the
surface at all.

**The overlay registry is persisted in `chrome.storage.session`**, and that is
not incidental. MV3 tears the worker down after ~30 s idle, and choosing a
directory is the slowest thing the user does here — so the picker routinely
outlives the worker that opened it. An in-memory-only registry meant the
subframe guard saw an empty map on the far side of a teardown and **refused a
legitimate pick**, discarding the choice precisely when the user had been
deliberating longest; and the tab watchers found no overlay to rescue when its
tab closed. `storage.session` has exactly the right lifetime: it survives the
teardown and dies with the browser, which is also when every overlay dies. Same
lesson as `Store.record_screened` — a fact that must outlive a teardown does not
live in service-worker memory.

**The toast is still a popup window**, unconditionally: it is a passive
notification with an eight-second life, and it has no pick to lose.
`chrome.notifications` remains the last rung under both.

**The picker is keyboard-first and mouse-capable.** Type to filter, arrows to
move, Enter to accept, Esc to leave it in the catch-all; a **click** on a row
accepts that row. Click is implemented in the reducer as "move the highlight
there, then Enter", so it reuses the Enter branch verbatim — the kind prompt,
the new-directory sub-question and the refusals all come from one place. A
click while the list is still loading or is known to be unavailable is
**dropped, not deferred**: Enter defers because a typed query survives the list
arriving, but a click's intent is a screen position, and honouring it against a
different list would choose an arbitrary directory or create one.

Each row shows **how many files that directory already holds**. The tally comes
from the `/dirs` snapshot, as a by-product of the whole-tree walk `FileIndex`
already performs for dedupe — never a scan of its own, and `dir_counts()`
deliberately does not refresh, so `/dirs` can never block on a walk of the
library. `/match` warms it on every download.

**The counts are deliberately not part of the `/dirs` ETag.** That ETag's job is
"the routing configuration changed" — the directories, their kinds, the aliases,
the threshold — because the extension's cached fallback matcher runs off exactly
that. A count changes on every completed download, and `FileIndex` is TTL-cached
so the same unchanged library can answer with two different counts a minute
apart; an ETag that changes when nothing changed is not a validator. The cost of
that choice is that a `304` carries no body, so the extension asks for the
snapshot **without `If-None-Match` on the picker path** — the one request whose
freshness a human is waiting on — and revalidates everywhere else.

**Undo after completion**: choosing a different directory after the download
completes calls `/relocate` (a same-filesystem rename, instant) and then
`/learn` — in that order, and the alias is written **only if the move actually
happened**.

`/relocate` is the one endpoint that moves a pre-existing file inside a live
seeding target, so it is not unconditional. It refuses unless it can prove this
router created the file, by **two independent proofs, both required**:

* **identity** — the file's name is the name of that download, modulo
  `uniquify`'s ` (1)` suffix. (Binding to the *directory* instead would let one
  routing decision authorise moving any file that happened to share the folder,
  and would break every correction, because a below-threshold match is
  deliberately filed into the catch-all while `/match` logged the candidate.)
* **age** — the file was written at or after that routing decision. (This
  holds for a genuine download and for a *completed* payload already on disk.
  An in-progress torrent is still writing pieces, so its mtime is current and
  the age test passes vacuously for it — which is exactly why identity is not
  optional.)

**No routing decision on record means no proof, and there is deliberately no
fallback.** With no record to check the extension's claim *against*, any
fallback reduces to trusting the caller on the one code path whose whole
purpose is to refuse a move it cannot prove. A download reaches that state when
the sidecar was unreachable when it started (so `/match` never ran for it), no
`downloadId` was sent, or the route log has been cleared — **not** because the
sidecar restarted: the route log is persistent SQLite and every decision is
committed. That one file gets moved by hand; anything routed while the sidecar
was reachable corrects normally.

A file that fails either proof is **not moved**, the refusal is surfaced rather
than swallowed, and no alias is learned from a move that did not happen.

**Profile scoping**: routing is off until enabled on that profile's options
page. Extension storage is per-profile, so every other profile behaves exactly
like stock Brave.

---

## Sidecar API

All endpoints require `Authorization: Bearer <token>` from
`~/.config/dl-router/token` (0600, auto-created). Bound to **127.0.0.1 only** —
`build_server` refuses any other address, with no override.

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness + index summary |
| GET | `/dirs` | directory index + aliases + site rules (ETag'd) |
| POST | `/match` | page context → `{dir, confidence, reason, candidates, suggestNew, dup, auto, ttlMs}` |
| POST | `/learn` | persist a correction (alias + labelled example + host prior) |
| POST | `/mkdir` | create a validated new directory |
| POST | `/relocate` | rename **within** the library root, only for a file this router provably created |
| POST | `/dedupe` | confirm a duplicate by exact size + a bounded head/tail hash, after the download landed |
| POST | `/discard` | remove a **proven** duplicate this router just wrote (five proofs; trash by default) |
| POST | `/fetch` | yt-dlp job for a stream URL; `GET /fetch/<id>` for status |
| GET | `/have?url=` | source-URL ledger lookup — "have I already downloaded this?" |
| GET | `/log` | recent routing decisions |

---

## Dedupe — size first, a bounded hash to confirm

The original check matched on the normalised **filename stem**. Against this
traffic that is a dead signal: the filenames are random per download, so it
fired **zero times in seventeen real downloads** while the library held groups
of same-size files it structurally could not see.

Three signals now, ranked, and each labelled in `kind`:

| `kind` | meaning | confirmable? |
|---|---|---|
| `name+size` | same stem **and** the same byte count | yes |
| `size` | different name, same exact byte count — **the signal that matters here** | yes |
| `name` | same stem, different byte count | no: different lengths are definitively different bytes |

**The size bucket is free.** `FileIndex` already stats every file during the
walk it already performs, so bucketing by exact size is a second dict built
from a number it already had.

**Where the hashing happens, and why it is not on `/match`.** `/match` runs
inside `onDeterminingFilename`, *before Chrome has written a byte* — the file
does not exist, so there is nothing to hash, and `totalBytes` is frequently `0`
there so even the size is unreliable. That settles the 400 ms budget question
by construction rather than by tuning: `/match` does a dict lookup and reports
a *possible* duplicate; `POST /dedupe`, called from `downloads.onChanged` after
completion, is the authoritative answer. Nothing is waiting on it, so a slow
answer costs a late toast, not a misfiled download.

**The digest is bounded and never reads a whole file**: 128 KiB from each end
plus the size, into a `blake2b`. Constant cost regardless of a multi-GB file.
The two windows **meet rather than overlap** — the tail start is clamped to the
end of the head window — so a file between 128 KiB and 256 KiB is read in full
(still ≤ 256 KiB) instead of having its tail skipped. Guarding the tail read
with `size > head + tail` instead left everything past 128 KiB unhashed for
exactly that band, and two files differing only in their *last byte* confirmed
as duplicates; `test_the_last_byte_is_always_hashed_at_every_size` walks the
boundary.

The honest cost of the bound: two files sharing their first and last 128 KiB
and differing only in the middle are reported as duplicates. That is affordable
only because a duplicate is a **warning with a keep button**, never an automatic
destruction — `tests/test_dedupe.py` pins the bound behaviourally, so widening
it to a whole-file read breaks a named test.

Digests are cached on `(path, size, mtime)`, so a library file is hashed once
rather than once per colliding download, and at most `MAX_DUP_CANDIDATES` (8)
same-size candidates are hashed per check. A file that vanished, is unreadable,
or is empty has **no** digest and confirms nothing.

### Duplicate handling: warn, never destroy silently

The file is **kept and filed normally**. A confirmed duplicate opens a toast
naming the library file it duplicates, offering `delete` and `keep`. That toast
does **not** auto-close (a timer would answer a question one of whose buttons
deletes a file), `Escape` is *keep*, and a refused delete leaves the toast open
showing the sidecar's own reason.

`POST /discard` is the **only destructive operation in this subsystem**. The
checks, and what each is actually worth:

1. **containment** — both paths, **and the trash destination**, through
   `safe_rel_path`. The destination used not to be: `os.makedirs(exist_ok=True)`
   follows a symlink, so a symlinked `.dl-router-trash` put the file outside the
   library root entirely.
2. **the source is not a live payload** — the seeding guard, see below.
3. **identity + age + recency** — the same evidence `/relocate` demands,
   without its `routed_files` short-circuit, and the routing decision must be
   under an hour old. Worth less than it reads (see the caveat below), which is
   why the route row is now **consumed**: one routing decision authorises at
   most one discard. It previously authorised an unbounded series, because
   `new.mp4`, `new (1).mp4` and `new (2).mp4` all satisfy `names_match` against
   a route recorded as `new.mp4`.
4. **the kept file genuinely predates the download** — it must be older than
   the routing decision by at least `MTIME_SLACK_S`. Without this the identity
   proof could not tell which half of a **uniquify pair** was the new copy, and
   `/discard` could be pointed at the original and would remove it. The two
   mtime windows are deliberately disjoint so no file can satisfy both.
   The two paths must also be **different inodes** (`st_dev`, `st_ino`, not
   resolved paths — `resolve()` collapses symlinks but not hardlinks).
5. **the two files are read IN FULL and compared byte for byte** — see below.
   The kept copy must also be a **complete** file, and both are re-stat'd after
   the comparison and must be unchanged, so a file still being written is
   refused rather than renamed out from under its writer.

**Read check 3 sceptically.** The *age* half passes vacuously against an
in-progress torrent (its mtime is current, which `_prove_owned`'s docstring has
always conceded), and `names_match` tolerates `uniquify`'s ` (N)` by design. So
identity is filename-with-slack, not exact. What actually binds a delete is
**filename-with-slack + under an hour + one-use-per-decision + check 4's
timestamp + check 5's re-proof**.

### Sampling is a warning. The delete is gated on a full comparison.

This took three rounds to get right, and the shape of the mistake is worth
recording: a digest designed as a cheap *warning* kept being promoted into a
*proof* that authorised destruction, and each round shored it up with another
`stat`-derived signal.

qBittorrent **preallocates**, so an in-progress payload has its full final
`st_size` from creation, and under "download first and last pieces first" its
head and tail are the finished bytes. First that defeated the head+tail digest.
Then `st_blocks` was added — which only catches `ftruncate`-style *sparse*
files. qBittorrent's "pre-allocate disk space for all files" uses
`posix_fallocate`, which reserves **real extents**: identical size, identical
block count, identical ends. Measured:

```
complete    blocks*512 = 8388608   _looks_preallocated = False
fallocated  blocks*512 = 8388608   _looks_preallocated = False
head+tail digests identical : True
```

**The information is not in the metadata.** So two things changed:

* the digest now also reads **eight 128 KiB mid-file samples** at offsets
  derived from the size (~1.25 MiB total, still constant). An unfilled extent
  reads as zeros, and an all-zero mid sample is *direct* evidence of one — a
  finished media file does not contain a 128 KiB run of zeros. This fixes the
  **warning**, so the toast stops offering a delete for this pair at all.
* `/discard` no longer uses the digest as its proof. It **reads both files in
  full and compares them byte for byte**. Sampling cannot carry a destructive
  decision: eight samples catch a 40%-complete payload with overwhelming
  probability but a 99%-complete one only about 8% of the time, and deleting
  the finished copy to keep a 99% copy still destroys data. No bounded read
  *proves* two multi-GB files identical; it only ever fails to disprove it.

The full comparison is affordable precisely because `/discard` is not `/match`:
it is rare, the user asked for it, and nothing is waiting on a 400 ms budget.
It runs **after** every cheap check, so only a request that has already earned
it pays. A comparison that runs out of its budget (`[dedupe] verify_timeout_s`,
180 s) returns "could not determine" — a **refusal**, never an all-clear, which
is why `files_identical` returns three states rather than a bool.

The kept file must additionally be non-sparse, have no all-zero mid sample,
and — when qBittorrent is configured — report `progress == 1`. A file already
in `.dl-router-trash/` is rejected as proof too: containment is not visibility,
and two discards could otherwise empty the library of a file by proving the
second against the first's corpse.

### The seeding guard is the payload check, **not** the trash

**Trash protects your bytes and does nothing for seeding.** qBittorrent seeds
by *path*, so renaming a payload into `.dl-router-trash/` breaks the torrent
exactly as an `unlink` would. `backfill apply` already refuses to move anything
live qBittorrent cannot prove is not a payload — and that is the *reversible*
operation, so the asymmetry was backwards.

`/discard` therefore refuses:

* a **hardlinked** file (`st_nlink > 1`) — the standard layout is a payload
  hardlinked into a subject directory; a browser download is always `nlink 1`;
* a **symlink** — `safe_rel_path` resolves, so the discard would remove the
  target and leave the link dangling;
* a **sparse** file — preallocated and partly written;
* and, when qBittorrent **is** configured, anything live state says is a
  payload, or anything it cannot corroborate at all (including qBittorrent
  being unreachable). Credentials are deliberately empty on this host, so the
  three local structural checks are what carry the weight there.

And then it does not `unlink`. The file is renamed into `.dl-router-trash/`
inside the library root: an atomic same-filesystem move, hidden so both index
scans skip it, inspectable with `ls`, reversible with `mv`. Set
`[dedupe] delete_mode = "unlink"` to opt into a real delete.

**Be precise about what the trash guarantees**: it protects the operator's
bytes — every refusal path above is one `mv` from recovery — and it does *not*
protect a seed, because qBittorrent seeds by path. It also has no retention, no
size cap and no reporting: nothing sweeps it and `dl-route status` does not
mention it, so it grows until emptied by hand (`du -sh …/.dl-router-trash`). A
cross-filesystem library root cannot use it at all — the move fails closed with
an `EXDEV` explanation rather than falling back to a non-atomic copy-then-delete.

---

## Source-URL ledger

Schema **v5** adds `source_urls`, keyed on a normalised URL (lower-case scheme
and host, no default port, no fragment; path and query kept verbatim, because a
query string usually *is* the asset identity). `/match` records every routed
download's source URL, `/relocate` re-points the row after a correction, and
`GET /have?url=` answers "already downloaded, in `<dir>`". The primary key is
the lookup index; a second index on `download_id` serves the write side.

This is **groundwork only** — there is no UI. A later change will use it to
badge "already have this" on a page before downloading.

---

## Backfill

The library root is a **live qBittorrent seeding target**. A plain `mv` of a
torrent payload makes the files vanish from qBittorrent's point of view and
seeding stops. So:

* `dl-route backfill plan` — **read-only against the tree AND the alias
  database**. It works out which aliases the existing directory and torrent
  names would seed and uses them *in memory*; `--seed-aliases` is what actually
  persists them into the store that drives live routing. It then proposes a
  directory per loose root file and writes a manifest (**TSV — the artefact you
  review and edit** — plus a JSON copy). Each row is tagged `qbt`
  (torrent-backed → `setLocation`), `fs` (**proven** not torrent-backed → plain
  rename), `NEW` (needs directory creation) or `SKIP`, and carries a `signal`
  column saying what the proposal actually rests on (`alias` / `filename` /
  `none`).
* `dl-route backfill apply --manifest <path>.tsv` — refuses to run without an
  explicit manifest you have reviewed. **Edit the `action` column and it takes
  effect**: the TSV is a first-class manifest, and pointing `apply` at the JSON
  after editing the TSV is refused rather than silently running the unedited
  plan. Torrent-backed rows move via `torrents/setLocation` and the torrent is
  re-verified afterwards — **waiting out the `moving` state**, because
  `setLocation` returns as soon as the request is accepted, not when the
  payload has arrived. Any failure aborts the remaining rows. `--dry-run`
  prints the exact operations.

**`apply` re-derives everything against live qBittorrent before it touches a
row.** The manifest's `move` and `torrent_hash` are plan-time values, and a
torrent can be added, removed or moved in between; a row whose live
classification disagrees with the manifest aborts the run and tells you to
re-plan. A client is therefore required whenever *anything* is going to move,
not only for rows the plan labelled `qbt`.

`SKIP` is the default and the safe answer, and **absence of proof is never
treated as proof**:

* qBittorrent unreachable, or its host↔container mapping underivable → every
  row is `SKIP`.
* Torrents exist but their **file lists** could not be read → no row may be
  `fs`. The index knows a torrent's files, not just its `content_path`, because
  a multi-file or no-root-folder torrent's payload sits *directly at the
  library root* — exactly this tool's target population — and reading absence
  from a partial index as "not torrent-backed" is a plain rename of a live
  seeding payload.
* A reachable qBittorrent with **no torrents at all** is positive proof, and
  `fs` is then genuinely safe.

The path mapping is derived at runtime by correlating
`torrents/info[].save_path` against paths that exist on the host. It is
deliberately not read from qBittorrent's stored config (whose `LastSavePath`
references a mount point that no longer exists), it needs **more than one
corroborating torrent** and an outright winner, and it must be able to express
the library root — a mapping that cannot is worse than none, because it would
classify every loose file as not-torrent-backed.

The backfill has no page context, so the only signal that may carry a row is an
**explicit alias** on the filename stem (seeded from a directory or torrent
name, or hand-set) — recorded knowledge rather than a guess about an opaque
filename. The filename itself stays under the spec's **≤0.50 cap**, so a
filename-only row cannot reach the 0.75 threshold and is labelled `filename` in
the manifest.

Nothing outside the library root is ever touched, and existing directories are
never renamed.

---

## Setup

1. **Configure.** `cp config.example.toml ~/.config/dl-router/config.toml` and
   set `library_root`. Then classify the directories, or nothing will ever
   auto-file:
   ```
   dl-route dirs classify --out ~/.config/dl-router/dirs.toml
   $EDITOR ~/.config/dl-router/dirs.toml     # move the wrong lines
   ```
2. **Start the sidecar.** `home-manager switch` installs the
   `dl-router` systemd user service. Check with `dl-route status`.
3. **Point the browser profile at the library.** With Brave **fully closed**:
   ```
   ./setup-brave-profile.sh --list                     # find the profile
   ./setup-brave-profile.sh --profile 'Profile N' --dry-run
   ./setup-brave-profile.sh --profile 'Profile N'
   ```
   This sets `download.default_directory` and `savefile.default_directory` to
   the library root and turns off `prompt_for_download`, after backing up
   `Preferences`. It refuses to run while a browser is **using that
   user-data-dir**, because Brave rewrites `Preferences` on exit and would
   revert the change — and it says which pid to quit. It checks for an open fd
   under the directory, a browser main process whose `--user-data-dir` resolves
   there (or that has none, i.e. the default one), and a `SingletonLock` whose
   pid is still alive. An instance on a *different* `--user-data-dir` (headless
   automation on a throwaway profile) and a stale lock left by a crash do not
   count. `--list` and `--dry-run` write nothing and are not gated at all.
   If it cannot read a process table it still refuses;
   `DL_ROUTER_ASSUME_BROWSER_CLOSED=1` overrides *that* case only.
4. **Load the extension.** `brave://extensions` → Developer mode → Load
   unpacked → this directory's `extension/`.
5. **Enable it for that profile.** Open the extension's Options page, paste the
   token from `dl-route token`, confirm the port, tick *Enable routing in this
   profile*, and hit *Test connection*.
6. **Optionally add qBittorrent credentials** to `config.toml` — only the
   backfill needs them.

> An extension **code** change needs a **full Brave restart**, not just the
> reload button — the same gotcha as browser-bridge.

---

## CLI

```
dl-route status                      sidecar + index health + kind counts
dl-route dirs                        list routing targets + their kinds
dl-route dirs classify [--out P]     DRAFT the directory-kind file
dl-route match --filename F --tag T  dry-run the matcher on a context
dl-route log -n 20                   recent routing decisions
dl-route alias list|set|rm           inspect/edit the alias table
dl-route alias review [--json]       what was LEARNED, with evidence + hits
dl-route backfill plan [--seed-aliases]  read-only (tree AND alias DB)
dl-route backfill apply --manifest P.tsv [--dry-run]
dl-route fetch URL --dir NAME        queue a yt-dlp job
dl-route token                       print the bearer token
```

`alias list` prints `*` for a global alias, and `alias rm --site '*'` accepts
it — what is displayed is what is accepted. (`--site ''` still works.) `alias
set` refuses a key that trips the suspicious-key screen unless you pass
`--force`, and structured keys round-trip verbatim:
`dl-route alias rm 'discord:<channel id>' --site discord.com`.

---

## Tests

Fully headless: no browser, no HDD, no cluster, no network. Filesystem roots,
the qBittorrent endpoint and the clock are all injectable, and **the live
qBittorrent instance is never contacted**.

```
nix-shell -p 'python312.withPackages(ps:[ps.pytest])' --run "python3 -m pytest scripts/dl-router/tests -q"
nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"
```

Run both from the repo root. `python312.withPackages` (not
`python312Packages.pytest`) is what actually guarantees the interpreter running
the suite is the one pytest was built for; the bare-package form only works by
accident when the ambient `python3` happens to be the same minor version. The
node glob **must be quoted** — `node --test <dir>` treats the directory as a
single test file and fails.

The security tests are the ones to keep green: the path-traversal table is
asserted against **both** `safety.py` and `extension/sanitize.js` (they must
agree), and the yt-dlp contract asserts an argv **list** with a validated
http(s) URL and a `--` terminator — never a shell string.

**One table, two implementations.** The hostile-input cases live in
`tests/fixtures/name_cases.json`; `test_security.py` and `sanitize.test.mjs`
both load it. They used to be two hand-copied literal lists, which agreed with
each other and both passed while the implementations disagreed on 991 inputs
neither list contained. After touching either implementation, re-run the
differential fuzzer (it needs both interpreters, so it is a script, not a
collected test):

```
nix-shell -p nodejs python312 --run "python3 scripts/dl-router/tests/difffuzz.py"
```

It must print `0 divergence(s)`. Where the two languages' primitives differ
(JS `trim()` strips U+FEFF, Python's does not; Python treats U+0085 as
whitespace, JS does not; `urlsplit` and `new URL` disagree about what a host
is), the rule is written out explicitly in **both** files rather than
delegating to either standard library.

---

## Privacy

Nothing about the library is committed. `library_root`, the directory-kind
classification (`dirs.toml` — a list of the operator's private directory
names), per-site rules, aliases, the route log, qBittorrent credentials and the
bearer token all live under `~/.config/dl-router/` and
`~/.local/share/dl-router/`. The sidecar's journal lines are metadata only. All
fixtures in `tests/` are synthetic, including the URL table — no real channel
id, host or forum appears anywhere in this repo.
