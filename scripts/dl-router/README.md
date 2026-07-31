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
                          toast + undo               picker (type/↑↓/Enter)
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
| `store.py` | SQLite: aliases, labelled examples, route log, host prior |
| `dirindex.py` | mtime+TTL-cached scan of the library root |
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
| exact alias hit, site-scoped | 1.00 |
| exact alias hit, global | 0.90 |
| normalised page tag/subject == directory key | 0.85 |
| token-sequence containment, scaled by coverage | 0.60–0.80 |
| filename token match | ≤ 0.50 |
| host prior (last directory used on this site) | +0.05, display only |

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

**Toast and picker are popup windows**, not in-page overlays: injection fails
on the PDF viewer, `chrome://` pages and sandboxed frames, which is exactly
where a download often starts. `chrome.notifications` is the secondary
fallback.

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
* **age** — the file was written at or after that routing decision.

**No routing decision on record means no proof, and there is deliberately no
fallback.** If the sidecar restarted between the download and the correction,
the record is gone and the move is refused — there would be nothing left to
check the extension's claim *against*, so any fallback reduces to trusting the
caller on the one code path whose whole purpose is to refuse a move it cannot
prove. The refusal says the record was lost and that the file can be moved by
hand; the next download routes normally.

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
| POST | `/fetch` | yt-dlp job for a stream URL; `GET /fetch/<id>` for status |
| GET | `/log` | recent routing decisions |

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
   set `library_root`.
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
   `Preferences`. It refuses to run while Brave is running, because Brave
   rewrites `Preferences` on exit and would revert the change.
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
dl-route status                      sidecar + index health
dl-route dirs                        list routing targets
dl-route match --filename F --tag T  dry-run the matcher on a context
dl-route log -n 20                   recent routing decisions
dl-route alias list|set|rm           inspect/edit the alias table
dl-route backfill plan [--seed-aliases]  read-only (tree AND alias DB)
dl-route backfill apply --manifest P.tsv [--dry-run]
dl-route fetch URL --dir NAME        queue a yt-dlp job
dl-route token                       print the bearer token
```

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

Nothing about the library is committed. `library_root`, per-site rules,
aliases, the route log, qBittorrent credentials and the bearer token all live
under `~/.config/dl-router/` and `~/.local/share/dl-router/`. The sidecar's
journal lines are metadata only. All fixtures in `tests/` are synthetic.
