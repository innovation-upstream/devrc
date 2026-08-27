# Handoff: extensions-pattern-analysis — 2026-08-26 (corrected 2026-08-27)

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ Measured 2026-08-27: the `devrc` scope holds 3 entries (`signal`, `obs`, `testlib`)
and **nothing on extensions** — `--search 'extension deploy'` returns no match. This
doc is currently the only written record of the deployment tiers.

## Goal
Trace and analyse the extensions deployment pattern in devrc — the mechanisms that
manage how browser extensions and managed files reach their live paths, and the
specific patterns used for the browser-bridge extension.

## Provenance — read this before trusting anything below

The 2026-08-26 session was **exploratory only**: no code was modified, no PR opened, no
clawgate task (the session ID was unset). It wrote its findings to
`/tmp/opencode/handoff/extensions-pattern-analysis.md` and **never landed them in the
repo** — so for a day the only copy of this analysis sat in `/tmp`, unversioned and
one sweep from gone.

🔴 **Its claims did not survive a fact-check.** They were re-read on 2026-08-27
against `nix/home.nix` — confirmed byte-identical to `origin/main` first, so the
working-tree copy was authoritative — and then adversarially re-audited. Each
correction is marked `CORRECTED` inline with what the source actually says. The
architecture was substantially right; the errors were in the details a reader acts on.

The corrections are marked inline rather than counted here **on purpose**. The first
version of this correction said "six", and the audit then found a seventh and an
eighth (`rm -df`, and a false claim about `rm -rf` following symlinks) — a total
maintained in parallel with the thing it counts will drift, so count the `CORRECTED`
markers rather than trusting a number in this paragraph.

Every line number below is against `nix/home.nix` at `origin/main` as of 2026-08-27
(3,873 lines). Line numbers rot — treat them as a starting offset for a grep, not an
address.

## Findings — the deployment tiers

devrc deploys extensions and managed files by three mechanisms, each chosen to match
the consumer's constraints.

### Tier 1: real-file directory copy (MV3 extensions)

Used by **browser-bridge** (`home.activation.browserBridgeExtension`, lines 594-746)
and **discord-embed-ext** (`home.activation.discordEmbedExtension`, lines 773-857) —
extensions Brave loads unpacked from disk.

Shared by both:
```
git-tracked source → cp -rL → temp dir ($$-suffixed) → move into place → live dir
```
- `cp -rL` resolves `/nix/store` symlinks to real files
- PID-suffixed temp names (`.new.$$` / `.old.$$`) plus a sweep of leftover temp dirs,
  which spares a dir whose owning PID is still **live** so a concurrent activation's
  in-flight temp survives — but see the third divergence below: the two extensions
  treat their OWN pid oppositely, on purpose
- `chmod -R u+rwX` before removing a store-derived tree, because a `cp -rL` from the
  read-only store yields a 0555 tree that `rm -rf` cannot unlink — which under
  activation's `set -eu` would abort the whole switch
  - 🔴 **CORRECTED.** The original doc wrote `rm -df`. There is no `rm -df` anywhere in
    `home.nix`; the command is `rm -rf`, and the `chmod` exists precisely because
    `rm -rf` fails without it.
  - ⚠ Not "before **every** `rm -rf`" — line 677 (`rm -rf "$bbBak"` on the fallback
    path) has no preceding `chmod`. Every other site pairs them (623/624, 627/628,
    685/686, 744/745, and 790/791 in discord's `deeScrub`). Since browser-bridge's
    sweep skips its own pid (line 620), a same-pid 0555 leftover is the one shape that
    reaches 677 unchmodded. **Not exercised by this session** — flagged as a possible
    source-side gap, not asserted as a live bug.

🔴 **CORRECTED — the two extensions do NOT share the swap, and this is the error most
likely to cause harm.** The original doc gave one Tier-1 pipeline containing
`mv -T --exchange` and attributed it to both extensions. `home.nix:759` says the
opposite in as many words:

> This copy is deliberately weaker than browser-bridge's: no `--exchange` two-attempt
> TOCTOU dance, because nothing here is serving traffic mid-switch — the failure it
> must avoid is a HALF-WRITTEN extension directory, not a torn handoff between a live
> server and its client.

So:

| | browser-bridge | discord-embed-ext |
|---|---|---|
| swap | `mv -T --exchange` (renameat2 `RENAME_EXCHANGE`) | `mv` old aside, then `mv` new in |
| target ever absent? | **no** on the atomic path — but **yes** on the fallback below | **yes**, briefly, by design |
| retry | 2-attempt loop re-deciding the branch | none |
| symlink-at-target guard | `[ -d "$bbDst" ] && [ ! -L "$bbDst" ]` (662) | `deeScrub()` helper (787-792) |
| own pid in the sweep | **spared** (620) | **deliberately NOT spared** (806-812) |

**Why the swap differs:** browser-bridge's live path is a server holding a long-poll
connection to a running service worker; an absent directory mid-switch is a torn
handoff. discord-embed-ext serves nothing during a switch, so a brief absence is
acceptable and the simpler code is the deliberate choice.

🔴 **Why the sweep differs — the third divergence, and the one most likely to be
"unified" by mistake.** browser-bridge's sweep skips `$$` (line 620). discord's
carries a 🔴 comment forbidding exactly that (806-812):

> 🔴 DO NOT skip our OWN pid here. A `.old.$$`/`.new.$$` can be the wreckage of an
> EARLIER interrupted run that happened to get this pid, and sparing it means
> `mv -T "$deeDst" "$deeStash"` later fails with `Directory not empty` -> `false` ->
> the whole switch aborts.

Both blocks also sweep an EMPTY pid suffix on purpose, because `[ -d "/proc/" ]` is
TRUE and such a dir would otherwise be spared forever (616-617, 802-803).

🔴 **CORRECTED — "never leaves the target absent" is too strong even for
browser-bridge.** There is a documented fallback — the branch at 671-698, entered
when the attempt at 669 fails — for a filesystem or coreutils without
`RENAME_EXCHANGE`, and it prints:

> browser-bridge: atomic directory exchange failed at $bbDst … Falling back to the
> rename-away swap, which **BRIEFLY leaves $bbDst absent** — a Brave reload during that
> window can fail. Re-run the switch if it did.

The comment at 664-668 is worth reading before diagnosing one of these: a failure there
is *not* necessarily "no RENAME_EXCHANGE" — EXDEV/ENOSPC/EACCES/EBUSY and an old
coreutils all land in the same branch and are indistinguishable, which is why the code
reports what `mv` said rather than asserting a cause.

**Why not store symlinks?** `home.file … recursive = true` would deploy the tree as
read-only `/nix/store` symlinks, and whether Chromium's unpacked-extension loader
accepts a tree of dangling-into-the-store symlinks is unmeasured. `home.nix:545-551`
is explicit that this must be MEASURED against live Brave and that a wrong guess costs
a full Brave restart with unrecoverable tabs — so the copy sidesteps the question
entirely and is equally git-immune. Cost: the whole tree is rewritten on every switch.

Two designs were tried and rejected (the bullets at 568-578, inside the commentary
at 566-583), both worth knowing before "improving" this:
- **hash-suffixed dir + symlink flip** — an unpacked extension's ID derives from its
  absolute directory path, so a target whose name changes every switch risks changing
  `chrome.runtime.id` every switch, destroying the id-stability that `browser ping`
  depends on.
- **`rm -rf "$bbDst"` then `mv -T`** — measured to DELETE the deployed tree outright
  under two concurrent activations (3/3 trials).

### Tier 2: store symlinks (`home.file`)

Standard home-manager: read-only symlinks into `/nix/store`. Used for Claude Code
skills, hooks, config.

- `force = true` overwrites a pre-existing *unmanaged* file on first switch
- 🔴 **Critical limitation**: `force = true` **cannot** displace a hand-placed regular
  file. `home.activation.dropStaleClaudeHooks` (line 1882) is
  `lib.hm.dag.entryBefore ["checkLinkTargets"]` and removes the stale file first;
  `opencodeDropStaleConfig` (1904) and `opencodeDropStaleActivityPlugin` (1933) do the
  same for opencode's config and plugin.
- 🔴 **CORRECTED — there are 9 uses of `force = true`, not 14.** 14 is the count of
  *mentions*; 5 of those are comments, and **all five** say `force = true` is absent or
  insufficient (1125 "alone is NOT enough", 1475 and 1508 "No `force = true`", 1870 and
  1895 "is NOT sufficient") — so the mention count moves *away* from the truth, not just
  past it. The assignments are at lines 1054, 1070, 1074, 1098, 1138, 1157, 1824, 1832,
  1844. Reproduce with `grep -cE '^\s*force = true;' nix/home.nix`, never a bare
  `grep -c 'force = true'`.

### Tier 3: out-of-store symlinks (`mkOutOfStoreSymlink`)

Points at the **live git checkout** — edits apply immediately with no
`home-manager switch`. Used where the source of truth is a subsystem in the repo and
the git-immunity of Tiers 1/2 would be an obstacle rather than a feature.

Applied to:
- `browser` skill → `~/.claude/skills/browser/` (1169, 1171) **and**
  `~/.config/opencode/skills/browser/` (1850, 1852)
- `dl-router` skill → `~/.config/opencode/skills/` (1854, 1856) and `~/.claude/skills/`
  (2469, 2471); plus `dl-route` on PATH at `~/.local/bin/dl-route` (2476), which is
  **not** a skills-dir deploy
- `opencode` skill + `opencode-dispatch` (1186, 1188, 1191)
- close-the-loop's **writable ledger** `STATE.md` / `ARCHIVE.md` (1119, 1121, 1837,
  1839) — a deliberate exception because the skill WRITES to them every run, so they
  must not be store symlinks
- 🔴 **CORRECTED — `claim-work` is not a skill and is not deployed to either skills
  directory.** It is a single `mkOutOfStoreSymlink` to `~/.local/bin/claim-work`
  (the `mkOutOfStoreSymlink` is at 1207; 1206 is its `home.file` key), i.e. on PATH. The tier is right; the deploy target in the original doc
  was wrong.

## The browser-bridge extension specifically

`scripts/browser-bridge/`. MV3 extension that drives Zach's live Brave from Claude
Code / opencode.

```
Claude Code / opencode
       ↓ POST /cmd (bearer token)
server.py (127.0.0.1:8788)
       ↓ GET /poll (long-poll)
service_worker.js (MV3)
       ↓ chrome.* APIs + CDP
Brave tab
```

**Key files** (all verified present 2026-08-27):
- `extension/service_worker.js` — chrome.* glue (CDP attach/detach, tab targeting,
  polling loop)
- `extension/protocol.js` — pure protocol logic (op set, validation, envelopes,
  backoff, emulation)
- `extension/build_id.js` — generated `BUILD_MARKER` literal, so the service worker
  reports its actual code version rather than the directory Chrome loaded
- `server.py` — loopback HTTP server (command queue, instance registry, bearer auth);
  port default 8788, and the source notes it must **not** be 8787
- `SKILL.md` — 135 lines (matches; there is an enforced byte ceiling on this file,
  owned by its own test)
- `reference/` — 🔴 **CORRECTED: 12 `.md` files plus a `sites/` subdirectory** (13
  entries, 14 git-tracked files). The original doc said 15. `sites/` holds
  `_index.json` and `civitai.com.md`.

**Skill** deployed via Tier 3 to both `~/.claude/skills/browser/` and
`~/.config/opencode/skills/browser/`. **Extension** deployed via Tier 1 to
`~/.local/share/browser-bridge-ext/`.

## Gotchas / decisions / dead-ends
- 🔴 **Flake trap**: flakes only see git-TRACKED files, so a NEW extension file that
  has not been `git add`ed is silently omitted from the deployed tree — a partially
  updated extension with no error anywhere. Documented for both extensions (553-557,
  764-768) and for `claude/skills/` in CLAUDE.md. `git add` before switching.
- 🔴 **`rm -f` vs `rm -rf` at line 714 — and the original doc got the REASON wrong.**
  It said *"`rm -rf` on a symlink follows and deletes the target — must use `rm -f` for
  symlink removal."* **That is false.** Tested 2026-08-27 on GNU coreutils 9.11:
  `rm -rf <symlink-to-dir>` removes the **link** and leaves the target directory and
  its contents intact. `rm` does not follow symlinks for deletion, with or without
  `-r`. The claim appears nowhere in `home.nix` — it is the `chmod -R` hazard below,
  misapplied to `rm`.
  The source's actual reason (705-713) is a **TOCTOU**: the `elif` at 700 tested the
  path, so by the time line 714 runs a concurrent activation may have installed a
  **directory** there. `rm -rf` would delete it silently with rc=0 — *"measured, 5/5"*
  — which is the hazard the loop exists to close. `rm -f` REFUSES a directory
  (rc=1, tree intact — measured, **no trial count given**), removes a plain file, and
  on a symlink drops the LINK not its target. `|| true` keeps the refusal non-fatal so
  iteration 2 re-decides and takes the atomic branch.
  discord's `deeScrub` uses `rm -f` on a symlink for a different, simpler reason
  (786): *"A symlink has no tree to make writable, so it is simply unlinked."*
- 🔴 `chmod -R` follows a symlink-to-directory and rewrites the modes of the **target**
  tree. Measured in a sandbox: a symlinked destination left the repo directory 555→755
  and its file 444→644. Guarded by `[ ! -L ]` in browser-bridge and by `deeScrub()` in
  discord-embed-ext.
- Re-pointing Brave at the deployed path is a **manual operator step**, once per
  profile (brave://extensions → remove the repo-path entry → Load unpacked → the
  `~/.local/share/…` directory). Nix cannot register an unpacked extension with Brave;
  it can only keep the directory correct.
- **DRY debt, knowingly** (754-758): discord-embed-ext repeats browser-bridge's shape
  rather than sharing it. The source states the trigger for paying it down: *"If a
  THIRD extension wants this, extract the helper first instead of pasting again."*
  There is no third extension today, so the trigger has **not** fired — and note that
  the two copies have already diverged in behaviour. 🔴 **The table above lists the
  divergences; count them there, do not trust a number in this sentence.** At least the
  swap and the own-pid sweep policy differ, each deliberately and each with a source
  comment saying why, so an extraction that unifies them reintroduces a hazard the
  source explicitly closed. Read every row before extracting anything.
- The **clawgate** extension is a different model entirely — it lives in
  `homelab-talos`, is deployed via worktree, and is not managed by nix.

## How to verify

The 2026-08-26 session claimed "no verification needed — analysis only". That framing
is what let six wrong claims stand for a day: an analysis doc makes checkable claims,
and "read the source files at the paths cited" is not a check anyone ran.

Re-verify with these, cheapest first:
```bash
R=~/workspace/devrc
git -C $R diff --stat HEAD origin/main -- nix/home.nix     # empty ⇒ tree copy authoritative
grep -cE '^\s*force = true;' $R/nix/home.nix               # 9, not 14
ls $R/scripts/browser-bridge/reference/*.md | wc -l        # 12
grep -n -- '--exchange' $R/nix/home.nix                    # browser-bridge only
grep -n 'mkOutOfStoreSymlink' $R/nix/home.nix              # tier-3 census

# the rm-on-a-symlink claim, which the ORIGINAL doc got backwards — run it, don't
# reason about it. Expect: link gone, target dir and its contents intact.
T=$(mktemp -d); mkdir "$T/real"; : > "$T/real/f"; ln -s "$T/real" "$T/link"
rm -rf "$T/link"; ls -d "$T/real" "$T/real/f"; rm -rf "$T"
```

🔴 Nothing here was verified by running a `home-manager switch`. Every claim above is
read from the source; the deploy paths' **behaviour** under a real switch is asserted
by the in-source measurement comments, not re-measured by this session. That is a
weaker claim than "verified" and is stated as such deliberately.

## Open

- The subsystem index has no entry for extensions (measured above). If this analysis
  is worth keeping past this doc's life, `/analyze-service` is what writes one — that
  is a confirm-gated act at the end of a session, not something to do in passing.
