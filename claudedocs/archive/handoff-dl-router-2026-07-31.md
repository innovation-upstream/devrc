# dl-router — shipped + open follow-ups

**Date:** 2026-07-31 · **State:** merged and deployed to both hosts · **Not yet exercised in a browser**

## What shipped

- **PR #220** (`56babdd`) — the subsystem: loopback sidecar (`127.0.0.1:8791`, bearer token,
  systemd user unit), deterministic matcher, SQLite alias/route store, qBittorrent client, yt-dlp
  fetcher, backfill, CLI, MV3 extension, `setup-brave-profile.sh`.
- **PR #224** (`7fe0fb8`) — adversarial-audit remediation. 20 original findings + 10 regressions the
  first fix round introduced + 3 further rounds. Tests grew 341/151 → **581 pytest / 272 node**.
- **PR #228** — `setup-brave-profile.sh` gates on the user-data-dir, not the binary name (headless
  Brave automation on throwaway profiles was blocking the real setup).
- **PR #232** (`b3b58f0`) — matching rewrite driven by the first evening of real use, which produced
  **zero auto-files in nine downloads**. Identity signals (`discord:<channel id>` from the attachment
  URL, `thread:<slug>` from unambiguous forum routes), performer/category directory kinds, a learning
  rule that only learns the discriminating signal, provable-only cross-host referrer carry, and a
  screened-refusal ledger. Five audit rounds; tests **828 pytest / 313 node**.

## Deployed state (2026-07-31, both hosts)

Schema **v4** (migrated from v2 in place; 9 routes + 5 examples preserved — verified on a copy first).
`dirs.toml` classified **performer=24, category=2 (`Bbc`, `other`), unclassified=0**.

⚠ The `dirs classify` generator's one proof rule — *a single word ⇒ category* — is **inverted for this
library**, where performer directories are single-token handles. It filed ten performers as
`category`, which would have taught tag→performer aliases: the exact mislearning PR #232 removed.
Corrected by hand; draft kept at `dirs.toml.draft-bak`. Fix the heuristic before anyone reruns it.

⚠ The store is **WAL-mode** and the main `.sqlite3` file is ~4 KB — all data lives in the `-wal`
sidecar. Copying only the `.sqlite3` yields an empty database (this silently invalidated a migration
test). Back up all three files, or checkpoint first.

Design spec: `claudedocs/dl-router-design-2026-07-30.md` (gitignored — contains host specifics).

## Deploy state (verified 2026-07-31)

- Workbench: unit `active`, 25 dirs indexed, `configured=true`. Store migrated **v1 → v2**
  (`routes.download_id` added, `routed_files` table created).
- Laptop: unit `active`, inert (`library_root` unset → routing endpoints 503). Harmless.
- `~/.config/dl-router/config.toml` exists; **qBittorrent credentials deliberately empty** — the
  backfill refuses to move anything without them, which is the safe default.

## Remaining manual steps (require Brave, or credentials)

1. **Brave fully closed** → `scripts/dl-router/setup-brave-profile.sh --list`, then
   `--profile '<dir>' --dry-run`, then for real. Target profile is display-named "other".
   Sets `download.default_directory` + `prompt_for_download=false` — unavoidable, because
   `onDeterminingFilename` cannot write outside the browser's download root.
2. `brave://extensions` → Developer mode → Load unpacked → `scripts/dl-router/extension/`.
3. Options page → paste `dl-route token`, tick **Enable routing in this profile**, *Test connection*.
4. Backfill only: qBittorrent credentials in `config.toml`, then `plan` → **review the TSV** →
   `apply --dry-run` → apply.

## Open follow-ups (none blocking, all recorded deliberately)

1. **Structural: the picker `reduce`/`apply` pair generates one bug family repeatedly.** Five
   findings across five audit rounds were the same shape — `reduce` returns unchanged state while
   `apply` re-reads the post-state and fires the side effect anyway. Each fix was correct and each
   left the consequence unguarded one step further out. The real fix is for `apply` to act on the
   **transition** rather than the post-state. Deliberately not attempted inside a PR already five
   rounds deep, because that round's own lesson was that mechanism swaps introduce regressions.
2. **`names_match` gaps** (unchanged since first noticed, fail closed): `my.long.title (1)` vs
   `my.long.title` — `uniquify_base` reads `title (1)` as an extension; and names within 1–3 chars
   of `MAX_FILE_NAME=200` can collide or diverge after truncation.
3. **HTTP 200 with `{ok:false}` is treated as success** by the extension. Unreachable against this
   sidecar (every refusal maps to 400/404/409/500 with `detail`), but it is an undocumented contract
   dependency in the swallow-and-learn family.
4. **`chrome.downloads.search` rejection** would leave a `state.pending` entry leaked, since the
   sweep timer never arms. Realistically does not reject.
5. **`Take.mp4` → `Take (12).mp4` over-accept** in `names_match` — irreducible given any uniquify
   tolerance. Pinned by a test named as a design cost so it is not later mistaken for an oversight.

## Verified vs unverified — be honest about this line

**Verified:** sidecar endpoints over a real socket (auth, `Host` allowlist, non-loopback refusal,
path traversal against `..`/absolute/NUL/bidi/symlinked source and destination); the matcher against
the real 25-dir index; the store migration including the crash window; `RLock` behaviour under a
concurrent scan; every fix pinned by a failing-before test.

**Never exercised:** the extension has **never run in Brave**, and **qBittorrent has never been
contacted**. Every extension-side behaviour is proven against a mocked `chrome` API only. Unproven
specifically — that Chrome's own filename sanitiser leaves the name byte-identical to what
`sanitizeFileName` produced (if it differs, `names_match` fails and *every* correction for that file
is refused); that `conflictAction: "uniquify"` produces ` (N)` on this build; that `window.close()`
on a popup is instant (this was masking finding F1); qBittorrent's `hashes=` case sensitivity; and
the backfill against ~1000 real torrents.

**Not one of the ~35 defects found across five audit rounds was caught by the test suite first.**
Every one came from structural analysis. Weight a structural sweep over a green suite here.

## First real checks to run

The first actual download in the routed profile, and a `backfill plan` (never `apply`) against the
live qBittorrent. Those settle what no amount of further auditing can.
