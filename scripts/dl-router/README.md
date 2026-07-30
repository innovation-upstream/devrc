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
| host prior (last directory used on this site) | +0.05, ranking only |

Guards:

* a fuzzy hit needs **≥2 tokens, or one token of ≥4 characters** — otherwise a
  short directory name matches random page prose;
* the **host prior is never decisive**: it cannot create a candidate, and the
  auto-file threshold is tested against the pre-bonus score;
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
completes calls `/relocate` (a same-filesystem rename, instant) and `/learn`.

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
| POST | `/relocate` | validated rename **within** the library root |
| POST | `/fetch` | yt-dlp job for a stream URL; `GET /fetch/<id>` for status |
| GET | `/log` | recent routing decisions |

---

## Backfill

The library root is a **live qBittorrent seeding target**. A plain `mv` of a
torrent payload makes the files vanish from qBittorrent's point of view and
seeding stops. So:

* `dl-route backfill plan` — **read-only** with respect to the tree. Seeds the
  alias table from existing directory names and torrent names, then proposes a
  directory per loose root file and writes a manifest (TSV to read, JSON to
  apply). Each row is tagged `qbt` (torrent-backed → `setLocation`), `fs`
  (plain rename), `NEW` (needs directory creation) or `SKIP`.
* `dl-route backfill apply --manifest <path>` — refuses to run without an
  explicit manifest you have reviewed. Torrent-backed rows move via
  `torrents/setLocation` and the torrent is re-checked afterwards; anything
  else moves via `os.rename`. Any failure aborts the remaining rows.
  `--dry-run` prints the exact operations.

`SKIP` is the default and the safe answer. In particular, **if qBittorrent is
unreachable, or its host↔container path mapping cannot be derived, every row is
SKIP** — without the torrent list nothing can prove a file is not a live
payload. The path mapping is derived at runtime by correlating
`torrents/info[].save_path` against paths that exist on the host; it is
deliberately not read from qBittorrent's stored config, whose `LastSavePath`
references a mount point that no longer exists.

The backfill has no page context, so there the **filename stem is the subject
signal** (the live download path keeps the weak ≤0.50 filename cap, where it
competes with real page context). The threshold is unchanged, so an opaque
name still lands on `SKIP`.

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
dl-route backfill plan               read-only; writes a manifest
dl-route backfill apply --manifest P [--dry-run]
dl-route fetch URL --dir NAME        queue a yt-dlp job
dl-route token                       print the bearer token
```

---

## Tests

Fully headless: no browser, no HDD, no cluster, no network. Filesystem roots,
the qBittorrent endpoint and the clock are all injectable, and **the live
qBittorrent instance is never contacted**.

```
nix-shell -p python312Packages.pytest --run "python3 -m pytest scripts/dl-router/tests -q"
nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"
```

The security tests are the ones to keep green: the path-traversal table is
asserted against **both** `safety.py` and `extension/sanitize.js` (they must
agree), and the yt-dlp contract asserts an argv **list** with a validated
http(s) URL and a `--` terminator — never a shell string.

---

## Privacy

Nothing about the library is committed. `library_root`, per-site rules,
aliases, the route log, qBittorrent credentials and the bearer token all live
under `~/.config/dl-router/` and `~/.local/share/dl-router/`. The sidecar's
journal lines are metadata only. All fixtures in `tests/` are synthetic.
