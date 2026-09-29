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

Two PRs open and gated, both awaiting review/merge. The earlier three (#1110, #1113,
#1149) are merged and shipped.

| PR | branch | state |
|---|---|---|
| [#1286](https://github.com/innovation-upstream/devrc/pull/1286) | `fix/dl-router-discord-original-url` | 6 commits, MERGEABLE, **audit ladder closed at round 5** |
| [#1290](https://github.com/innovation-upstream/devrc/pull/1290) | `feat/discord-embed-viewport-cap` | 2 commits, **audit round 1 closed** |
| [#1236](https://github.com/innovation-upstream/devrc/pull/1236) | `docs/handoff-dl-router-discord-media` | this doc |

**#1286 — `originalFromPreview`.** `preferOriginalUrl` was inert in production: it reads
Chrome's `info.linkUrl`, which is populated only from an ANCESTOR `<a>`, and a Discord
image attachment has none. Fixed by rewriting the proxy host to the origin host and
dropping the resize knobs, carrying the signature. Also restores `correlateCapture`
tier 1, which the rewrite had made structurally impossible.

**#1290 — viewport cap.** `embed_enlarge.js` removed Discord's 400x300 cap and put
nothing in its place (`max-height: none`), so enlarged media overflowed the window on
every enlarge. Now `max-width: min(100%, 96vw)` / `max-height: 92vh`, declarative, **no
resize listener** — the engine re-evaluates viewport units itself.

🔴 **STILL NOT verified at the consumer, and this is now the gate on BOTH PRs.** One
full restart confirms both: `brave://extensions` should read dl-router **0.3.3** and
discord-embed-ext **0.3.1**. The evidence that the earlier reload did not take, carried
forward because it is the whole reason this step is still open:

| | |
|---|---|
| dl-router 0.3.2 bytes in the repo tree | written 2026-08-31 02:41 |
| dl-router last reloaded (Brave's own `Preferences`, Profile 2) | 2026-08-30 21:39 — ~5 h BEFORE those bytes |
| Brave main process | up since **2026-08-26 16:20**, no restart (an earlier "08-28 23:53" reading was of a different process) |
| Profile 2 `Preferences` last flushed | 2026-09-03 16:39 — so a reload that day WOULD have been recorded |

Both manifests have since been bumped again (0.3.2 -> 0.3.3, 0.3.0 -> 0.3.1), so the
version on screen is now an unambiguous check rather than a guess.

⚠ **Neither PR has been tested against the live Discord client.** Every rendering number
in #1290 — mine and the audit's — comes from a synthetic page carrying the declaration
set, driven headlessly over CDP. #1286's fake-DOM tests observe no layout at all.

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

### RESOLVED — `preferOriginalUrl` was a no-op in production, and now is not

The previous entry asked "is this a no-op?" and answered *genuinely unknown*. It is answered.

- **Symptom + exact repro:** right-click any Discord **image** attachment, "save" via the
  extension menu. The file saved is the resizing proxy's downscaled webp, not the posted
  original — the exact defect #1110 set out to fix, still live for images (video was always fine,
  its `src` is already the origin).
- **Observed (with values):** measured from the page context of a live Discord tab, over 3 image
  attachments in 2 channels and 2 message shapes (single image, mosaic):

  | | measured |
  |---|---|
  | `<img src>` host | `media.discordapp.net` — 3/3 |
  | ancestor `<a>` around the image | **0 of 3** — the clickable ancestor is `div[role=button].clickableWrapper` |
  | `cdn.discordapp.com` anchor present in the same message | 3/3, but a **sibling at depth 9** |
  | same pathname, where pairing is unambiguous (2/3) | `true` |
  | `ex`/`hm`/`is` values across the two hosts | **byte-identical** |

  `preferOriginalUrl(info.srcUrl, info.linkUrl)` therefore hits `if (!linkUrl) return srcUrl`
  (`route_core.js:316`) on every real right-click, because Chrome populates `linkUrl` only from an
  **ancestor** link. The 3rd sample's `samePath=false` was an artifact of the probe pairing a
  mosaic's first anchor with a different image — not a Discord behaviour.
- **Ruled out — "correct but never exercised", the previous entry's leading hypothesis.** It
  cannot fire at all for images.
  via: measurement — 0 ancestor `<a>` elements between the image and its message, 3/3.
- **Ruled out — the previous entry's elimination of host-rewriting, which was WRONG ON ITS
  EVIDENCE.** It claimed "the two hosts carry DIFFERENT query params (cdn: `ex`/`hm`/`is`; proxy:
  `format`/`width`)". They do not: the proxy URL carries `ex`/`hm`/`is` **plus** the resize
  params, with identical signature values, so the rewrite keeps a valid signature.
  via: measurement — parsed both URLs for one asset; key sets and the three signature values
  compared directly.
- **Ruled out — "an unsigned cdn URL might not 403", the gap this doc flagged as never probed.**
  It does fail. Probed with both controls from the page context:

  | | status |
  |---|---|
  | positive control — the message's own cdn anchor | **206** |
  | under test — proxy url, host swapped, resize params dropped | **206** |
  | negative control — same url, signature removed | **network error** |

  via: measurement — three range requests from a live Discord tab. The negative control failed at
  the network layer rather than with a readable 403 (cross-origin, so a non-CORS response is
  opaque); that still discriminates, which is what the control is for.
- **Fix shipped in #1286:** a pure `originalFromPreview()` rewrites proxy host to cdn host and
  drops the resize knobs, carrying the signature. Chosen over a content script, which would need
  a `discord.com` host permission and couple to Discord's hashed class names (they rotate every
  deploy).
- **What is still NOT established:** that Chrome's `info.linkUrl` is absent has been measured at
  the DOM (no ancestor anchor) and follows from documented `contextMenus` semantics, but has
  never been observed from an actual right-click — that needs 0.3.3 running, i.e. the restart.

### RESOLVED — the audit ladder on #1286, and what it cost

Five rounds. Round 1 was a full audit; rounds 2-5 were delta rounds. **Every round after
the first found a defect introduced by the PREVIOUS round's fix, and all of them were
prose.** Worth reading before writing prose fixes in this repo.

- **Round 1 (🔴):** `dl-router/SKILL.md` carried a 🔴 "never rewrite a proxy URL's host"
  rule whose stated reason this PR had measured false. The skill ships via
  `mkOutOfStoreSymlink`, so it is live off the working tree with **no `home-manager
  switch`** — the next agent would have read a false NEVER and reverted the fix.
  It also caught a regression: the rewrite made tier-1 correlation impossible.
- **Round 2:** the round-1 fix landed in the WRONG SECTION — under `## Player buttons`,
  whose TOML `media` list is resolved by `container.querySelectorAll` (a DESCENDANT
  query) and whose path never calls `originalFromPreview`. It declared a working
  accessor futile. Also found the refuted claim surviving at a second site
  (`config.example.toml`); a wider grep found a **third** (a test comment).
- **Round 3:** caught a FALSE CLAIM in round 2's own commit message — the F3 comment fix
  had been destroyed by a `git checkout --` during the mutation battery and the reverted
  file committed. Also: the round-2 `###` heading was never closed, so 45 lines of
  player guidance sat under a context-menu heading.
- **Round 4:** four false sentences, including one asserting the stamp is "inert" on the
  streaming path when it is **decisive** there.
- **Round 5:** verified round 4's chain link-by-link and found it correct, proving
  unreachable the exception it was asked to hunt. Found one remaining false claim of
  mine ("the only test that reaches the swap" — `identity.test.mjs` also does, and
  predates this PR).

🔴 **Both ladders were STOPPED on the escape hatch, not on convergence** — recorded on
each PR. Criteria named each time: no 🔴, blast radius bounded by "the document contains
a false sentence", the recurring shape swept at every site. The newest sentences in both
PRs are **unaudited, not cleared**.

- **Ruled out — that the ledger would split on the rewritten URL.**
  via: measurement — `ledgerSourceKey(proxy)` and `ledgerSourceKey(rewritten)` are the
  identical string, because `discordSourceKey` already folds host AND drops the query;
  `server.py` prefers `ctx.source_key` for every Discord attachment.
- **Ruled out — that the lightbox would inherit #1290's new inline caps.**
  via: code — `lightbox.js` does not `cloneNode`; it builds a fresh element and copies
  only `src` plus `<source>` children, so no inline style crosses over.
- **Ruled out — that `min()` is a risk.**
  via: measurement + code — `manifest_version: 3` implies Chrome >= 88 and `min()`
  shipped in Chrome 79. The cliff is real in shape (an unparseable `setProperty` is a
  silent no-op leaving the prior value, so a non-supporting engine would end with NO
  `max-width` — worse than the `100%` it replaced) but unreachable here.
- **Ruled out — that `object-fit: contain` is what preserves the aspect ratio.**
  via: measurement — 58.88 x 588.80 with `contain`, with `fill`, and with the
  declaration deleted. It IS load-bearing under `display:flex; flex-direction:column`
  (614.39 x 588.80 box), where `fill` squashes. Keep it; do not credit it for the
  block case.

### OPEN — nothing has been seen working in the real client

- **Symptom + exact repro:** not a failure — an unverified feature, twice over.
- **Next probe, in order:** full Brave restart -> confirm `brave://extensions` reads
  dl-router **0.3.3** and discord-embed-ext **0.3.1** -> right-click one Discord image
  attachment and confirm the saved file is the posted original, not a resized webp ->
  enlarge one tall image and confirm it no longer runs off the window.
- 🔴 The extension still has NO logging facility, which is why "did this fire?" needs a
  human at the browser rather than a query.

## Next steps (ranked)

1. **Fully restart Brave; confirm `brave://extensions` reads dl-router 0.3.3 and
   discord-embed-ext 0.3.1.**
   forcing: user — the operator asked to ship; the reload is MEASURED not to have taken
   (no restart since 2026-08-26). Nothing below can be checked until this is done. A `↻`
   is documented as unreliable here; a FULL restart is the fix.
2. **Review and merge [#1286](https://github.com/innovation-upstream/devrc/pull/1286).**
   IN FLIGHT: devrc#1286. Both sandbox tiers green at base `473c1cdc`-era main;
   re-gate on the merged tree if main has moved.
   forcing: regression — the shipped `preferOriginalUrl` is measurably inert, so image
   downloads silently save resized thumbnails today.
3. **Review and merge [#1290](https://github.com/innovation-upstream/devrc/pull/1290).**
   IN FLIGHT: devrc#1290.
   forcing: regression — enlarged media overflows the window on every enlarge.
4. **After the restart, confirm both end-to-end** (one right-click save, one enlarge).
   This is the only step that closes the `info.linkUrl` gap and the live-render gap.
   forcing: gate — both PRs' verification is incomplete without it.
5. **Merge [#1236](https://github.com/innovation-upstream/devrc/pull/1236)** (this doc)
   and remove the worktrees: `devrc-ho-dlr`, `devrc-dlr-orig`, and the agent worktrees
   under `.claude/worktrees/`.
   forcing: none
6. **Decide the Discord player rule** — `~/.config/dl-router/config.toml`, NOT committed.
   The DOM premise is settled; note the anchor is a SIBLING of the image, so an
   `element` accessor must be scoped so `container` encloses it — **and whether it does
   was never measured.** 🔴 Order: switch the sidecar FIRST, write the rule SECOND.
   forcing: none
7. **Give the extension a logging facility.** Still the reason every "does this fire?"
   question costs a live browser probe.
   forcing: none

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

- 🔴 **"Verified in isolation" hit this feature exactly as `RULES.md` describes.** Every unit test
  supplied a `linkUrl`; none owned the seam where Chrome decides whether to supply one. Both
  suites were green over a feature that could not fire. `identity.test.mjs:196` even pinned the
  fallback — correct about the code, wrong about production. #1286 adds a service-worker test
  that asserts WHICH url is downloaded with no `linkUrl` at all: red at `aba48864`
  (`media.discordapp.net/...?format=webp&width=550`), green at HEAD (`cdn.discordapp.com/...`).
- 🔴 **`nix build --rebuild` on a derivation whose build FAILED does not re-run it** — it errors
  `some outputs ... are not valid, so checking is not possible` and **exits 1**, which reads
  exactly like a second test failure. Two "reproductions" of a red were this, and neither ran a
  single test. Plain `nix build` re-runs a failed derivation; keep `--rebuild` for verifying a
  *successful* one.
- 🔴 **`nix log <drv>` returns ONE stored log per derivation path, and the path is a function of
  the source tree** — so consecutive runs of the same tree all read back the same log. The tell
  was a duration identical to the decimal (`390.53s`) across supposedly separate runs. Capture
  each run's own stdout to its own file instead.
- 🔴 **A red `pytests` derivation on this box is a load flake until the wall time says otherwise,
  and the wall time is the instrument.** `test_six_writers_with_a_tiny_busy_timeout_still_land_every_row`
  went red once with `OperationalError('database is locked')`; the whole 1020-test dl-router
  target took **391 s** in that run against **115 s** at base. Load inflates every test in the
  run, an assertion inflates one — and the test's own docstring says it is a deliberate 5 ms
  busy-timeout load reproduction. A genuine re-run was green.
- **Discord's media channels in the route log may no longer be reachable in the client.** Eight
  distinct channels appear in attachment paths; none of them was in the sidebar or DM list when
  probed. Open DMs are where a live image sample was eventually found.
- **The route log has gained one Discord row (21, was 20) and the shape is unchanged**: 100%
  `cdn.discordapp.com`, 100% video (12 mp4 / 9 mov), still zero images. So the log still cannot
  validate any image-path change — that has to come from the DOM or from a real download.

- 🔴 **`nix build --rebuild` does NOT re-run a derivation whose build FAILED** — it exits
  1 with `some outputs ... are not valid, so checking is not possible`, which reads
  exactly like a test failure. Two "reproductions" of a red were this, and neither ran a
  test. Plain `nix build` re-runs it.
- 🔴 **`nix log <drv>` holds ONE log per derivation path, and the path is a function of
  the source tree** — consecutive runs of the same tree read back the SAME log. The tell
  was a duration identical to the decimal (390.53s) across supposedly separate runs.
  Capture each run's stdout to its own file; bind a log to a tree by the drv path
  differing, not by a string you hope appears in it (a source COMMENT never appears in
  test output — that control is vacuous).
- 🔴 **A red `pytests` on this box is a load flake until the WALL TIME says otherwise.**
  `test_six_writers_with_a_tiny_busy_timeout...` went red once; the whole 1020-test
  dl-router target took **391s** against **115s** at base. Load inflates every test, an
  assertion inflates one — and that test is itself a 5ms busy-timeout load reproduction.
- 🔴 **A `git checkout --` restore in a mutation battery will silently revert your
  UNCOMMITTED edits.** That is how a fix was claimed in a commit message and absent from
  the tree. Verify the edit is in the COMMIT (`git show HEAD`), not just the working
  tree.
- 🔴 **`dl-router/extension/*.js` is ASCII-only** (`source_hygiene.test.mjs`); emoji in a
  comment fails the suite. `discord-embed-ext` has NO such rule and legitimately contains
  emoji. Tripped twice in one ladder.
- **`--hide-scrollbars=false` is parsed by Chromium as presence-only and therefore HIDES
  scrollbars** — it produced a false refutation of the `100vw` gutter measurement before
  being dropped.
- **A PR can be auto-created on push** (authored as the repo owner) rather than by
  `gh pr create`; check base/head/commits before assuming which one you are looking at.

## How to verify

1. `nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"` — **551**
   pass. Same for `scripts/discord-embed-ext/tests/*.test.mjs` — **143** pass. The glob
   MUST be quoted; an unquoted directory yields a bogus `# tests 1`.
2. Both sandbox tiers, **one at a time**, on the MERGED tree:
   `nix build .#checks.x86_64-linux.nodetests` and `... .pytests`. Read each runner's own
   `RESULT:` line out of `nix log`, never the exit code — an already-realised derivation
   prints nothing and exits 0.
3. Content check on `main` after each merges (never ancestry — squash merges break it):
   `originalFromPreview` present in `route_core.js`; `ENLARGED_MAX_WIDTH` present in
   `embed_enlarge.js`; manifests read `0.3.3` and `0.3.1`.
4. 🔴 The consumer checks that are STILL OUTSTANDING — full Brave restart, then one
   right-click save on a Discord image, then one enlarge of a tall image.
