---
name: dl-router
description: Operate the media download router — a loopback sidecar plus an MV3 Brave extension that files downloads straight into the right subject directory of a local media library using page context (not the filename), with an undo toast, a keyboard picker, dedupe warnings, a yt-dlp path for HLS/DASH, and a qBittorrent-aware backfill for pre-existing files. Status, query/tune matching, fix a wrong route, add a site rule, run the backfill, restart/debug the sidecar or the extension. Use when the user mentions the download router, dl-route, downloads landing in the wrong folder, auto-filing downloads, the library root, the subject directories, the download picker/toast, or backfilling loose files.
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
503. That is the normal state on a host that has not been set up.

## Where things live

| What | Where |
|---|---|
| code | `scripts/dl-router/` (this repo) |
| config | `~/.config/dl-router/config.toml` (**never committed**) |
| bearer token | `~/.config/dl-router/token` (0600, auto-created) |
| aliases, route log | `~/.local/share/dl-router/dl-router.sqlite3` |
| backfill manifests | `~/.local/share/dl-router/manifests/` |
| service | `systemd --user` unit `dl-router` (from `nix/home.nix`) |
| extension | `scripts/dl-router/extension/`, loaded unpacked per profile |

**Never print the library root, directory names, filenames or the route log
into a commit message, a PR, a doc, or any file in this repo.** The repo is
public; the library is private. Synthetic names only (`Jane Doe`, `acme-studio`,
`example-site.test`).

## Common tasks

**"A download went to the wrong folder."**
```bash
dl-route log -n 20            # each line shows the score and the REASON
```
The reason string is the diagnosis: `alias(site:…)`, `tag=='…'`, `contains '…'
(2/4 tokens)`, `filename tokens […]`, `tie: …`, `+host-prior`. Then either fix
it in the UI (the toast's `change`, which writes an alias via `/learn`) or
directly:
```bash
dl-route alias set "<page tag>" "<Directory>" --site example-site.test
```
Site-scoped aliases score 1.00 and beat everything else. Re-check with
`dl-route match --tag "<page tag>" --site example-site.test`.

**"It keeps asking instead of auto-filing."** The score is under
`auto_threshold` (0.75) or two candidates are within `tie_margin`.
`dl-route match …` shows the candidate list. Prefer adding an alias over
lowering the threshold — the threshold is what keeps a wrong guess out of a
subject directory.

**"It files things into the catch-all folder."** That is the designed
below-threshold behaviour: an unconfident download must not pollute a subject
directory, and the picker's Esc then costs nothing. Fix the *match*, not the
fallback.

**"A site's tags are not being picked up."** Generic extraction (Open Graph,
JSON-LD `Person`/`VideoObject`, `[itemprop=name]`, `meta[name=keywords]`) runs
everywhere. For a site that needs more, add a rule — **config, not code**:
```toml
[site_rules."example-site.test"]
subject = ["a.performer-name"]
tags    = [".tag-list a"]
```
Then restart the sidecar (`systemctl --user restart dl-router`) so the snapshot
carries the new rules. Selector subset: tag, `.class`, `#id`, `[attr]`,
`[attr="v"]`, `[attr^="v"]`, descendant combinators, comma groups.

**"The extension seems dead / changes did nothing."**
1. `dl-route status` — is the sidecar up?
2. Options page → *Test connection* — port and token right? Is *Enable routing
   in this profile* ticked? It is **per-profile** and off by default.
3. **An extension code change needs a FULL Brave restart**, not the reload
   button — same gotcha as browser-bridge.

**"Downloads still show a Save-As dialog."** The profile's
`download.default_directory` is not the library root, or `prompt_for_download`
is still on. Re-run `setup-brave-profile.sh` with Brave **fully closed** (it
refuses otherwise, because Brave rewrites `Preferences` on exit).

## Backfill — the one dangerous path

The library root is a **live qBittorrent seeding target**. Moving a torrent
payload with `mv` breaks seeding.

```bash
dl-route backfill plan                                  # READ-ONLY
dl-route backfill apply --manifest <path>.json --dry-run
dl-route backfill apply --manifest <path>.json
```

* `plan` never writes into the tree. Review the TSV before applying anything.
* `apply` refuses to run without an explicit manifest. Torrent-backed rows move
  via `torrents/setLocation` and the torrent is re-verified afterwards; any
  failure aborts the remaining rows.
* **If qBittorrent is unreachable or the host↔container path mapping cannot be
  derived, every row is `SKIP`** — that is correct, not a bug. Fix the
  credentials in `config.toml` rather than working around it.
* The path mapping is derived at runtime from `torrents/info[].save_path`.
  Do **not** hardcode it and do not read it from qBittorrent's stored config —
  its `LastSavePath` points at a mount that no longer exists.
* Never point `apply` at the real tree to "see what happens". Tests cover it on
  temp trees with a fake qBittorrent.

## Changing the code

```bash
nix-shell -p python312Packages.pytest --run "python3 -m pytest scripts/dl-router/tests -q"
nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"
home-manager switch --flake ~/workspace/devrc --impure   # restarts the sidecar
```

Invariants the tests exist to protect — do not weaken them:

* **`suggest()` is called exactly once on every path and never hangs.** Chrome
  falls back to the default filename if the listener is slow. The timer racing
  the sidecar plus an idempotent `finish()` is what makes this true.
* **Every page-derived string is validated before it becomes a path.**
  `safety.py` and `extension/sanitize.js` must agree; the same hostile-input
  table is asserted against both.
* **yt-dlp is invoked as an argv list with a validated http(s) URL and a `--`
  terminator** — never a shell string. The URL comes from a web page.
* **The sidecar refuses any non-loopback bind**, with no override.
* Tests never contact the live qBittorrent instance or touch the real library.

Editing the sidecar requires a `home-manager switch` (it runs from the nix
store). `SKILL.md` and `dl-route` are out-of-store symlinks and track the
working tree immediately.

## Gotchas

* Port **8791** — 8790 is already taken on the workbench.
* `onDeterminingFilename` **cannot escape the download root**: no `..`, no
  absolute paths. That is precisely why the profile's download directory has to
  *be* the library root.
* Existing directories are **never renamed** (three naming conventions coexist;
  the matcher folds them). New ones are Title Case and never created silently.
* Both hosts are hostname `nixos` — check `dl-route status` to know which one
  you are on.
* The sidecar is inert on a host with no `library_root`; that is fine, not a
  failure to fix.
