# Handoff: dl-router-discord-media — 2026-09-02

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
Make Discord media easy to save into the library. Discord is **59% of dl-router's live
traffic** (20 of 34 routed downloads; all 5 identity aliases are `discord:` channel rows)
and the routing half has been Discord-specific since the first evening — but the **save**
side was still wrong for it in three ways.

## State now

All three PRs MERGED and shipped. Both hosts converged and switched.

| PR | squash | what |
|---|---|---|
| #1110 | `911af220` | save the file that was POSTED, not the proxy thumbnail; stable ledger key for a signed CDN URL; Discord attachments bypass the yt-dlp branch |
| #1113 | `4fdead27` | the sibling `discord-embed-ext` handoff doc — crop VERIFIED LIVE + three corrections |
| #1149 | `67f674f6` | `media` may be an ORDERED LIST of accessors, so ONE rule covers an anchor-linked image AND a direct video |

Verified on `origin/main` **by content, never ancestry** (a squash merge never makes the
branch head an ancestor): `ledgerSourceKey` / `discordSourceKey` / `preferOriginalUrl` each
present once, both `MAX_MEDIA_ACCESSORS` read 8, manifest `0.3.2`, `SKILL.md` documents the
list form.

**Deployed:** `scripts/ship.sh` converged both hosts (`rc=0`, 0 dangling / 0 stale each).
⚠ The workbench's built generation is `origin/main` **PLUS** an uncommitted
`nix/pkgs/default.nix` from another session — the two hosts share a sha but not content.
Left alone on the operator's instruction; noted so a later "both hosts verified" is not read
as byte-parity.

🔴 **NOT verified at the consumer.** MEASURED after the operator reported reloading:

| | |
|---|---|
| dl-router bytes in the repo tree | `0.3.2`, written 2026-08-31 02:41 |
| dl-router last reloaded (Brave's own `Preferences` record) | 2026-08-30 21:39 — **five hours BEFORE those bytes existed** |
| Brave main process | up since 2026-08-28 23:53 — no restart at all |
| `Preferences` last flushed to disk | 2026-08-31 11:59, so a reload that day WOULD have been recorded |

**Brave is still running dl-router 0.3.1.** The 21:39 reload picked up 0.3.1 from #1110;
0.3.2 landed after it. `brave://extensions` reading `0.3.2` is the check — that is what the
manifest bump exists for. A `↻` is documented as unreliable here: it often leaves the OLD
service worker alive because the long-poll keeps it running. A FULL Brave restart is the fix.

The sibling `discord-embed-ext` is unaffected and current (0.3.0 in the tree since 08-29,
reloaded after that), so its own open item needs no restart.

## Open investigations — live diagnosis state

### Is `preferOriginalUrl` a no-op in production?
- **Symptom + exact repro:** not a failure — an unverifiable feature. The context menu now
  prefers an anchor's `href` over an `<img src>` when both are Discord attachments with the
  same path. Nothing records whether that swap has ever fired.
- **Observed (with values):** all **20** live Discord rows in the route log are
  `cdn.discordapp.com` (origin host), query-param key set `('ex','hm','is')` on every one,
  extensions `.mov` ×9 / `.mp4` ×11 — **zero images**. So the proxy-host branch has no live
  instance behind it. The DEFECT is proven independently: at the pre-change commit the new
  test downloads `…a.png?format=webp&width=550`, the proxy copy.
- **Ruled out — validating the fix from the route log.** The corpus contains no proxy-host
  row at all, so it cannot distinguish "the swap works" from "the swap never fires".
  via: measurement — all 20 Discord rows queried directly out of the live SQLite route
  log; host was `cdn.discordapp.com` on every one, 0 on `media.discordapp.net`.
- **Ruled out — reaching the original by rewriting a proxy URL's host.** The rewrite would
  drop a signature belonging to the other host, so it cannot be a safe substitute for
  reading the anchor.
  via: measurement — the two hosts carry DIFFERENT query params: all 20 live cdn rows have
  the key set `('ex','hm','is')`, while proxy URLs carry `format`/`width`.
  ⚠ What is NOT established: whether an unsigned cdn URL actually 403s. No live attachment
  URL was ever exercised. The elimination rests on the param difference, not on a probe.
- **Leading hypothesis:** genuinely unknown. It may be correct and simply never exercised,
  because Discord video (which is what actually gets downloaded here) already carries the
  origin URL in `src`.
- **Next probe (run verbatim, with a Discord tab open in Brave `Profile 2`):**
  ```js
  [...document.querySelectorAll('img[src*="discordapp"]')].slice(0,3)
    .map(e => [new URL(e.src).host, e.closest('a') && new URL(e.closest('a').href).host])
  ```
  Proxy host in `src` + origin host on the anchor ⇒ the premise holds and the fix matters.
  Same host in both ⇒ `preferOriginalUrl` is a harmless no-op and should be recorded as one.
- 🔴 **The extension has NO logging facility.** Adding one is a real unstarted task, not a
  one-liner; that is why this question has no data-driven answer today.

## Next steps (ranked)

1. **Fully restart Brave and confirm `brave://extensions` reads `0.3.2`.**
   forcing: user — the operator asked to ship and reported reloading; the reload is
   MEASURED not to have taken (Brave's own record predates the bytes by five hours), so the
   shipped fix is not running.
   Nothing else in this doc can be checked until this is done.
2. **Settle the DOM premise with the probe above, then decide the Discord player rule.**
   Repo `devrc`, file `~/.config/dl-router/config.toml` (NOT committed — config, not code).
   forcing: none
   ```toml
   [site_rules."discord.com".player]
   container = "[class^=mediaWrapper]"
   media = [
     { element = 'a[href^="https://cdn.discordapp.com/attachments"]', attr = "href" },
     { element = "video", attr = "src" },
   ]
   ```
   🔴 **Order matters: switch FIRST, write the rule SECOND.** A list rule against the OLD
   sidecar is `ConfigError` → `load_degraded` → `library_root` unset → **every routing
   endpoint 503**, not just the button. Reverting is the mirror image: remove the rule
   before rolling the sidecar back.
   ⚠ Discord's class names are hashed and rotate every deploy. `[data-dee-enlarged="1"]`
   (written by the sibling `discord-embed-ext`) is the churn-proof alternative, at the cost
   of coupling two extensions.
3. **Give the extension a logging facility, so "is this a no-op in production?" becomes
   answerable for this subsystem at all.** Repo `devrc`,
   `scripts/dl-router/extension/`. forcing: none

## Gotchas / decisions / dead-ends

- 🔴 **Two ledger-key writers, one reader.** `playerDownload` (write), `haveUrl` (read) and
  `buildMatchPayload` (a DIFFERENT rule). The Discord host-fold landed on the reader and on
  ONE of the two writers and they immediately disagreed — a lookup asked for a string the
  writer never stored. Both now go through `ledgerSourceKey`. **`buildMatchPayload` is
  deliberately NOT folded in**: it arbitrates between two candidate keys and yields `""` for
  an ordinary download so the sidecar falls back to the full URL. Folding it in would mint a
  key for EVERY download on every site — a mutant that does so is caught by three
  pre-existing tests, so that boundary is pinned rather than asserted.
- 🔴 **`store.source_url_key` keeps the query ON PURPOSE and is right to.** On the file
  hosts it was written for, the query IS the asset identity. Discord is the exception
  (`ex`/`is`/`hm` are a rotating signature), so the exception lives in `discordSourceKey`.
  Do not weaken `source_url_key` for everyone.
- 🔴 **A hand-copied constant in two languages will drift silently.** `MAX_MEDIA_ACCESSORS`
  is in `config.py` AND `player_buttons.js`. MEASURED: bumping only the Python side 8→9 left
  BOTH suites fully green — the sidecar would accept a 9-entry rule the extension rejects,
  giving no button and no error. Now pinned by a test that reads the JS source. The repo had
  already paid for this shape once (`fixtures/name_cases.json`, 991 divergences).
- 🔴 **A menu test asserting only `downloads.length === 0` is VACUOUS.** Hit twice in one
  PR: a mutant making the branch do NOTHING AT ALL survived a fully green 527-test suite.
  Any new `onMenuClicked` case must pin the POSITIVE half — that `/fetch` was POSTed, and
  with WHICH url. Fixture trap: `auto: false` queues the picker and POSTs nothing, so a
  below-threshold fixture cannot exercise the path it names.
- 🔴 **`tests/source_hygiene.test.mjs` requires every `extension/*.js` to be plain ASCII.**
  Emoji comment markers fail the whole suite. `route_core.js` documents the rule near
  `TITLE_SPLIT`; use `--` not an em dash.
- **The `isinstance(acc, dict)` check in `config.py` stops a CRASH LOOP, not just a bad
  message.** `load_degraded` catches `ConfigError` and nothing else, and the unit is
  `Restart=always`/`RestartSec=10`; without it a `media = ["video"]` typo raises
  `AttributeError` and crash-loops six times a minute with nothing listening.
- **`.envrc` is `use opencode`, so a bare `python3 -m pytest` says "No module named
  pytest".** Use `nix develop ~/workspace/devrc -c python3 -m pytest <paths> -q`.
  `gate.sh` exits 3 for exactly this — a MISSING ENVIRONMENT, not a code failure.
- **The dev-host tier is not the tier the merge gates on.** `gate.sh` does not invoke
  `nix build`. Run `nix build .#checks.x86_64-linux.{pytests,nodetests}` **ONE AT A TIME** —
  a combined invocation produces false failures. A combined GREEN is trustworthy; a combined
  RED is not, until re-checked alone. This box routinely has 3–5 other sessions building
  the same derivations; waiting for a clear store is an indefinite block, so run under
  contention and re-run solo only on a red.
- **Diff against the MERGE BASE, not `origin/main`.** A branch a few commits behind shows
  main's newer work as deletions — this session nearly reported ~3,900 phantom deletions
  across `drift-check.sh`/`run-tests.sh`/`handoff_doc.py`.

## How to verify

1. `nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"` — 537 pass
   (the glob MUST be quoted; an unquoted directory yields a bogus `# tests 1`).
2. `nix develop ~/workspace/devrc -c python3 -m pytest scripts/dl-router/tests -q` — 1018+.
3. Content check on `main` (never ancestry — squash merges break it):
   `git show origin/main:scripts/dl-router/extension/manifest.json | grep version` → `0.3.2`,
   and both `MAX_MEDIA_ACCESSORS` declarations read `8`.
4. The consumer check that is still OUTSTANDING: full Brave restart →
   `brave://extensions` reads **Media Download Router 0.3.2**. Still `0.3.1` ⇒ the restart
   did not take.
