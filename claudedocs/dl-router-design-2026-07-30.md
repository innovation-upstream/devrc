# dl-router — auto-filing download router (design spec)

**Date:** 2026-07-30 · **Status:** approved for implementation · **Target:** `innovation-upstream/devrc` (PUBLIC repo — see §9)

## 1. Problem

Manual download flow today: click a download link on a file-sharing/tube site → Save-As dialog →
navigate the picker to the right subdirectory under the media library root → save. Evidence of the
toil: Brave `Profile 2`'s `savefile.default_directory` is being re-pointed at individual library
subdirectories by hand.

The library root (call it `LIB_ROOT`, configured locally, **not committed**) holds ~25
subject-keyed subdirectories plus a `other/` catch-all plus ~30 loose files at root. Subdirectory
naming is inconsistent across three conventions (`Title Case`, `lower-kebab`, `snake_Case`).

Downloaded filenames are frequently opaque (`0hv9783sdgne5ur3xh53n_source.mp4`,
`8968710-1080p.mp4`) → **the primary matching signal must be page context, not the filename.**

## 2. Locked decisions

| # | Decision | Chosen |
|---|---|---|
| D1 | Write path | **Direct write + sidecar brain.** Browser download root = `LIB_ROOT`; extension's `downloads.onDeterminingFilename` returns `"<dir>/<name>"`. Zero copy, no move, no race. A loopback sidecar service owns the dir index, alias DB, dedupe, and answers match queries. Extension falls back to a cached snapshot if the sidecar is down. |
| D2 | Scope | **Profile-scoped, all downloads.** Active only where enabled in the extension's options page (per-profile by construction, since extension storage is per-profile). Other profiles behave normally. |
| D3 | Confirm UX | **Auto-file above threshold + undo toast** (~8s, shows dir + match reason + `change`). Below threshold → picker. Every correction writes an alias. |
| D4 | Extras in scope | Images + `<video>` elements; yt-dlp for HLS/DASH; dedupe warning; backfill. |
| D5 | Backfill | **Plan-only by default**, qbt-aware apply. Alias seeding from the whole tree (read-only) + a dry-run manifest for the ~30 loose root files. |
| D6 | New dirs | **Proposed in the picker as the top pre-filled entry**, one keypress to create. Never created silently. |
| D7 | Naming | New dirs **Title Case**; existing dirs **never renamed** (matcher normalises across conventions). |
| D8 | Repo | **devrc, neutral code + gitignored data** (§9). |

## 3. Hazards (must be respected by the implementation)

1. **`LIB_ROOT` is a live qBittorrent seeding target.** `qbittorrent-nox` runs as a k8s pod
   (`media-stack/qbittorrent`, workbench cluster, NodePort **30880**, host-network node) with the
   20TB disk bind-mounted at **`/downloads`** inside the container while the host sees
   `/home/zach/hdd-20tb`. qBittorrent's `Session\DefaultSavePath` is the torrents root and its
   `LastSavePath` points *into* the library dir — so the loose root files are probably live torrent
   payloads. A plain `mv` breaks seeding (files go missing → torrent errors).
   **Any move of an existing file must go through qBittorrent's `torrents/setLocation` API when the
   file is torrent-backed.** WebUI requires auth (`/api/v2/auth/login`); credentials live in local
   config, never committed. The host↔container path prefix mapping must be **derived at runtime**
   from `torrents/info[].save_path` — do not hardcode (`LastSavePath` in the config is stale and
   references a mount point that no longer exists).
2. **`onDeterminingFilename` cannot escape the download root** — no `..`, no absolute paths. This is
   why D1 requires setting the profile's `download.default_directory` to `LIB_ROOT` and
   `prompt_for_download=false`. Ship `setup-brave-profile.sh` to do this (refuse to run while Brave
   is running; back up `Preferences` first; list profile display names so the right one is picked).
3. **`suggest()` must not hang.** Chrome will fall back to the default filename if the listener is
   slow. See §6 for the required timeout/fallback ladder.
4. **New downloads are unaffected by hazard 1** — they are new files written by the browser, not
   torrent payloads. Only the backfill touches torrent-backed data.
5. Port **8790 is already in use** on the workbench → sidecar defaults to **8791** (configurable).
6. **`yt-dlp` is not installed** → add to `nix/home.nix` packages.

## 4. Components

```
scripts/dl-router/
  server.py            loopback HTTP sidecar (127.0.0.1:8791, bearer-token auth)
  matcher.py           pure deterministic scoring — no LLM, no network
  store.py             SQLite: aliases, labelled examples, route log
  dirindex.py          mtime-cached scan of LIB_ROOT → normalised keys
  qbt.py               qBittorrent WebUI client + host↔container path mapping
  fetcher.py           yt-dlp path for stream URLs
  backfill.py          plan (manifest) / apply (qbt-aware)
  config.py            loads ~/.config/dl-router/config.toml
  dl-route             CLI entrypoint
  setup-brave-profile.sh
  extension/           MV3 extension (separate from browser-bridge)
    manifest.json service_worker.js content_capture.js
    picker.html/js  toast.js  options.html/js
  tests/               pytest + node --test (*.test.mjs)
  README.md  SKILL.md
```

**Why a separate extension from `browser-bridge`:** different lifecycle and blast radius — a bug in
download routing must not take down the agent command channel. Reuse browser-bridge's *patterns*
(token file, loopback bind, systemd user unit, `X-Restart-Triggers`, test layout), not its code path.

## 5. Sidecar API

All endpoints require `Authorization: Bearer <token>` from `~/.config/dl-router/token` (0600).
Bind **127.0.0.1 only**.

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness |
| GET | `/dirs` | dir index + alias table snapshot (extension caches this; ETag'd) |
| POST | `/match` | `{url, finalUrl, referrer, filename, mime, page:{title,url,tags[],og{},linkText,alt}}` → `{dir, confidence, reason, candidates[], suggestNew, dup, ttlMs}` |
| POST | `/learn` | `{context, chosenDir, autoDir, createdNew}` → persists alias + labelled example |
| POST | `/mkdir` | create a validated new dir under `LIB_ROOT` |
| POST | `/relocate` | `{fromRelPath, toDir}` → validated rename **within** `LIB_ROOT` (undo path) |
| POST | `/fetch` | yt-dlp job for a stream URL → `{jobId}`; `GET /fetch/<id>` for status |
| GET | `/log` | recent routes (feeds the CLI + review) |

## 6. Extension flow and timing

**Context capture** (`content_capture.js`, all frames): on capture-phase `mousedown`/`click`/
`contextmenu` over `a[href]`, `img`, `video`, snapshot
`{href, mediaSrc, linkText, alt, pageUrl, pageTitle, tags[], og{}, jsonLd}` and post to the SW.
Tag extraction is **data-driven**: generic selectors (`meta[property^="og:"]`, JSON-LD
`Person`/`VideoObject`, `[itemprop=name]`, subject/tag anchor patterns) plus a per-site rules table
loaded from local config — adding a site is config, not code.

**Correlating a download to a context.** `DownloadItem` carries no `tabId`, so use a three-tier
ladder, each tier tested:
1. exact match on `item.url` / `item.finalUrl` against a captured `href`/`mediaSrc`;
2. `item.referrer` == a captured `pageUrl`;
3. most recent capture from the active tab within `N` seconds (default 15).

**`onDeterminingFilename` ladder** (hard requirement — `suggest()` always called exactly once):
1. Fire `/match` with a **400ms** timeout (configurable) *and* compute a decision synchronously from
   the in-memory `/dirs` snapshot.
2. Sidecar answers in time → use it. Times out or errors → use the cached decision. No cached
   snapshot → `other/`.
3. Sanitise the dir before use: must match `^[^/\\\x00-\x1f]{1,120}$`, not `.`/`..`, and be a known
   dir or an explicitly approved new one. Sanitise the filename separately.
   `suggest({filename: dir + "/" + safeName, conflictAction: "uniquify"})` — **never** `overwrite`.

**After the decision:** toast via a **popup extension window** (`chrome.windows.create`, small,
always-on-top-ish) rather than an in-page overlay — works on PDF viewer / `chrome://` / sandboxed
pages where injection fails. `chrome.notifications` as a secondary fallback. `change` opens the
picker (type-to-filter, ↑↓, Enter accept, Esc → `other/`, top entry = `+ new dir "<suggested>"`).

**Undo after completion:** if the user picks a different dir after `downloads.onChanged`
`state=complete`, call `/relocate` (same-filesystem rename, instant) and `/learn`.

**Context menus:** `Save to library…` on `link`, `image`, `video`. For `<video>`/HLS/DASH sources →
`/fetch` (yt-dlp with `--cookies-from-browser brave:<profile>`).

## 7. Matcher (deterministic, ordered — no LLM)

Normalisation key: NFKD → strip diacritics → lowercase → drop all non-alphanumerics. This folds
`Title Case`, `lower-kebab`, and `snake_Case` to the same key, which is why D7 leaves existing dirs
alone.

| Rule | Score |
|---|---|
| exact alias hit, site-scoped | 1.00 |
| exact alias hit, global | 0.90 |
| normalised page tag/subject == dir key | 0.85 |
| token-sequence containment (page tag ↔ dir key), scaled by token coverage | 0.60–0.80 |
| filename token match | ≤ 0.50 |
| host prior (last dir used on this host) | +0.05, never decisive |

Guards: fuzzy hits require ≥2 tokens **or** a single token of ≥4 chars (stops a short dir name from
matching random page prose). Auto-file threshold default **0.75**. Ties within 0.05 → picker.
`reason` is always returned and surfaced in the toast so a wrong match is diagnosable.

**Dedupe:** check target dir + whole-tree index for same normalised filename or same `(size, name)`
→ return `dup`. Warn in the toast; **never block or overwrite** (`uniquify` handles collisions).

**No `.dlmeta.json` sidecar files** — route provenance lives in SQLite only. Writing extra files into
the media dirs would pollute them and risks confusing qBittorrent/media scanners.

## 8. Backfill (`dl-route backfill`)

- `plan` — read-only. Walks the tree, seeds the alias table from existing dir names and torrent
  names, and emits a manifest (TSV + JSON) proposing a dir per loose root file, each row tagged
  `qbt` (torrent-backed → `setLocation`), `fs` (plain rename), `NEW` (needs dir creation), or `SKIP`
  (ambiguous — the default for anything below threshold).
- `apply --manifest <path>` — refuses to run without an explicit manifest the user has reviewed.
  Torrent-backed files move via `torrents/setLocation` + verify the torrent stays in a seeding state
  afterwards; non-torrent files move via `os.rename`. Any failure aborts the remaining rows and
  reports. `--dry-run` prints the exact operations.
- Never touches anything outside `LIB_ROOT` and never renames existing dirs.

## 9. Public-repo constraints

`innovation-upstream/devrc` is **public**. Therefore:

- **No library content in the repo.** `LIB_ROOT`, site rules, aliases, the route log, qBittorrent
  credentials, and the sidecar token all live under `~/.config/dl-router/` +
  `~/.local/share/dl-router/`, none of it committed. Config has neutral defaults and the code has no
  hardcoded paths.
- **Synthetic test fixtures only** (`Jane Doe`, `acme-studio`, `example-site.test`). The real-name
  golden set, if used at all, goes in a gitignored local fixture the tests skip when absent.
- **Neutral naming throughout** — "media download router", "subject dir", "library root". The
  CLAUDE.md subsystem entry and README follow the same rule.

## 10. Deployment

- `nix/home.nix`: `dl-router` systemd user service (loopback sidecar, `X-Restart-Triggers` on the
  script hashes so `home-manager switch` restarts it on change), `yt-dlp` package, and the
  `SKILL.md` symlink into `~/.claude/skills/dl-router/`.
- The extension is loaded unpacked per Brave profile. **An extension code change needs a full Brave
  restart**, not just ↻ (same gotcha as browser-bridge).
- `setup-brave-profile.sh` performs the one-time `download.default_directory` /
  `prompt_for_download` change (§3.2).

## 11. Test plan (complete coverage required)

**pytest** — `matcher` (every scoring rule, normalisation across all three naming conventions,
threshold boundaries, tie handling, guard against short-token false positives); `dirindex` (cache
invalidation, unicode/space/bracket names, symlinks, permission errors); `store` (alias upsert,
concurrent writers, migration); `server` (auth required, non-loopback bind refused, every endpoint,
malformed payloads); `security` (**path traversal via dir name**: `../..`, `foo/bar`, absolute,
NUL, control chars, RTL overrides, 300-char names, `.`/`..`; yt-dlp argv built as a list with a
validated http(s) URL, never a shell string); `qbt` (login, path mapping derivation, setLocation,
error paths — against a stub HTTP server, never the live instance); `backfill` (plan
classification, apply on a temp tree with a fake qbt, abort-on-failure, refusal without a manifest);
`fetcher` (job lifecycle, cancellation).

**node --test (`*.test.mjs`)**, matching browser-bridge's layout — `service_worker`: the three-tier
context correlation ladder, the `onDeterminingFilename` timeout→cache→`other/` fallback ladder
(assert `suggest()` is called exactly once in every branch), dir/filename sanitisation, dedupe
surfacing, relocate-after-complete; `content_capture`: tag extraction from fixture HTML (og, JSON-LD,
generic selectors, per-site rules, missing/hostile markup); `picker`: keyboard flow incl. new-dir
entry and Esc; `toast`: render + fallback when window creation fails.

**Everything must run headlessly** — no browser, no HDD, no cluster. All filesystem roots, the qbt
endpoint, and the clock are injectable.

## 12. Non-goals (v1)

Full-tree reorganisation of the ~1000 torrent dirs; renaming existing dirs; LLM-based matching;
cross-host sync of the alias DB; any move outside an approved manifest.
