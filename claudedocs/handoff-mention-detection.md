---
clawgate-task: 440
---
# Handoff: mention-detection — 2026-08-30

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Detect clawgate/GitHub/ClickUp references in agent output, emit telemetry, and make
them clickable in the terminal the way a URL already is. **SHIPPED AND VERIFIED LIVE.**

## State now
🔴 **RANKS 1–8 ARE CLOSED. ONLY RANK 9 REMAINS, AND IT IS HUMAN-ONLY.** Everything this
effort was carrying is merged and verified by CONTENT; nothing is deployed-pending.

**Merged and content-verified 2026-09-01:**
- `innovation-upstream/devrc#1201` → squash `3a8ef2f0` — the rank-8 handoff. Doc blob
  `d3ef5d45` is byte-identical on `origin/main`. Both required checks were SUCCESS.
- `ZacxDev/homelab-infra#616` → squash `437cd091` — rank 7's rescued `deploy/SKILL.md`
  lesson. Blob `83a4112f` on `trunk`; corrected framing present, the false "two repos"
  claim **0 occurrences**.
- `ZacxDev/homelab-infra#617` → squash `884aae25` — the comment correction. On `trunk`:
  `THIS IS LOAD-BEARING` present, `THIS LOOKS UNNECESSARY` **0 occurrences**. Merged
  `UNSTABLE` — its two reds were the `COULD NOT RUN` pair; `clawgate-e2e` was SUCCESS.
- Claims `mention-detection-7` and `-8` RELEASED.

**Nothing needed deploying, and that was CHECKED not assumed.** `nix/home.nix` deploys
only `claudedocs/close-the-loop/{STATE,ARCHIVE}.md` out of `claudedocs/` — handoff docs
are NOT managed paths — and #617 is clawgate Go source, which reaches nothing running
without a pin bump. So "merged" and "deployed" coincide here only because there is
nothing to deploy. clawgate remains **0.8.21**.

**`~/workspace/homelab-talos` is now CLEAN** — `0 ahead, 0 behind`, no tracked
modifications, carrying #616's corrected `deploy/SKILL.md` (`83a4112f`). It had been
unable to `merge --ff-only` since 2026-08-31. The recovery path was re-verified
IMMEDIATELY before the restore, not in the survey that motivated it: working copy
`402cd671` matched `c40261bc:.claude/skills/deploy/SKILL.md` on origin.

**Both loose ends closed:**
- ⚠ **The stranded `claudedocs/handoff-limewire-torrent-comps.md` needed NO rescue — it
  was already safe.** Its blob `df6219f2` is byte-identical inside `c40261bc`
  (`rescue/workbench-dirty-tree-2026-08-31`), already on origin. 🔴 **This doc's own
  earlier text was misleading about that**: it enumerated the rescue's exclusions
  (`go.mod`, `opencode.json`, `tests/`, three `__pycache__/`) in a way that read as
  "untracked files were left out" — the rescue DID capture this `claudedocs/` file. The
  uniqueness check cost one command and stopped a pointless second copy.
- ✅ **`clawgate-e2e-pg-35881` REMOVED.** Not on an assumption of idleness: positive
  control **6** rows from `pg_stat_activity` (proving the query can see backends)
  against **0** other client backends, plus no established host connections on 35881.
  Port freed; no other `clawgate-e2e-pg-*` remain.

⚠ **Observed, deliberately NOT acted on — someone else's live resources:**
- container `wizardly_agnesi` (`clawgate-audit:1`) **Up 4 days** — same leaked shape as
  the one removed, but an audit image that may be a running job. Find its owner first.
- `docker system df`: **772 local volumes, 37.39 GB, 100% reclaimable** — dwarfs
  anything the container cost. `scripts/diagnose-nix-disk.sh` sits untracked in devrc,
  so disk pressure may already be someone's thread.
- devrc `main` still carries another session's dirty `nix/programs/alacritty/
  default.nix`, untouched throughout, including across two fast-forwards and a
  `reset --keep`. Each fast-forward was checked for collision with it first.

## Open investigations — live diagnosis state

### ~~clawgate deeplink `#task-N` is inert~~ — FIXED, deployed 0.8.19, verified live
**CLOSED 2026-08-30.** `taskHashScript()` shipped in `homelab-infra#564` (squash
`9ff37992`) and reached the running pod as **clawgate 0.8.19** (pin commit `8503620a`).
The leading hypothesis below was **CORRECT** and is now confirmed by the fix working.

Verified against the deployed pod, not a fixture — headless Chromium at 1280×720 driven
straight at the LAN NodePort:
```
ARRIVE  #task-253  inViewport=true  data-task-hash-focus=true  scrollY 0 -> 20736
HASHCHG #task-440  inViewport=true  data-task-hash-focus=true  scrollY -> 1204
PREVIOUS #task-253 data-task-hash-focus=null   NO-RELOAD sentinel="kept"   CONSOLE ERRORS: []
```

🔴 **The live check found a SECOND, PRE-EXISTING bug that the e2e structurally could not
— now clawgate task #463 (open).** The bottom of the board cannot be scrolled to:
`scrollHeight` 21,997 vs maxScroll 21,277, and at maxScroll the last card sits at
`rect.top +6,510` — **60 of 248 cards unreachable by ANY scroll** (wheel, `scrollTo`,
`scrollIntoView`). Control proving it is not the new handler: measured on `/tasks` with
**no fragment**, so the handler never armed, and it reproduces with that code inert.
So **#440 is `ready_for_review`, NOT `complete`** — its criterion 1 is satisfied for the
reachable 76% of the board and is unsatisfiable for the rest until #463 lands.

The original diagnosis is kept below because #463 inherits its layout context.

- **Symptom + exact repro (as measured BEFORE the fix):** open
  `https://clawgate.zacx.dev/tasks#task-370`. The board
  loads; the page does NOT scroll to or focus task 370. Reachable from the shipped
  feature: click a bare `#N` mention → rofi picker → choose the clawgate candidate.
- **Observed (with values):**
  - the DOM id is correct — `internal/ui/notes.go:459` emits `ID("task-"+ids)`
  - `GET /tasks` → `handleIndex` (`internal/api/server.go:384`) serves only the
    **document shell**; cards arrive in a LATER htmx fetch, URL built client-side at
    `internal/ui/components.go:2875-2876` (`/ui/tasks`, optional `?tag=` filters)
  - `grep` for `location.hash|hashchange|scrollIntoView` across `internal/` + `static/`
    returns **exactly one hit**, `components.go:2176`, an unrelated dropdown option
- **Ruled out:** an auth problem (the fragment survives the `login.zacx.dev` redirect);
  a wrong-selector problem (the id is right).
- **Leading hypothesis:** the browser resolves the fragment at initial document load,
  before any card exists, and nothing re-applies it after htmx settles.
- **Next probe:** none needed for diagnosis — the work is the fix. #440 carries 6
  acceptance criteria, non-goals pinning the operator's two decisions (scroll+highlight,
  client-side), and the verifier. Criterion 4 is the sharp one: a fragment naming a task
  NOT in the rendered set (tag filter active) must not fail silently.

### The dirty `homelab-talos` working tree — MEASURED, awaiting an operator decision
Not a bug; an unresolved **disposition**. Full evidence is under rank 7. The one thing that
must not be lost:

- **Symptom + exact repro:** `git -C ~/workspace/homelab-talos merge --ff-only origin/trunk`
  refuses. `git status --porcelain` shows ` M .claude/skills/deploy/SKILL.md`, ` M flake.nix`
  plus untracked scratch.
- **Observed (with values):**
  - `git rev-list --left-right --count HEAD...origin/trunk` → **`0	1`** (0 ahead, 1 behind).
    The refusal is the dirty tree, NOT divergence.
  - `git hash-object flake.nix` → `f58b97ce…`; `HEAD:flake.nix` and `origin/trunk:flake.nix`
    are both `4058416e…`. Scanned **every** commit on **every** branch touching the path
    (11 commits): **no match**. Same for `.claude/skills/deploy/SKILL.md`
    (working `402cd671…` vs committed `64977409…`).
  - `git log --all -S'__dp_status_cache' -- flake.nix` in this repo → **empty**; the same
    search in `datapacket-talos` → **`527b9ec32` / `4e3201fbb` "feat(dev): cache GitHub
    status in shell hook (#956)"**. The blob is in **neither** repo's object store, so it
    is a hand/agent port, not a copy of any revision.
  - `gh repo view ZacxDev/homelab-talos` → **does not resolve**. `kubectl get gitrepository
    -A` → `flux-system` reads `ssh://git@github.com/ZacxDev/homelab-infra.git` branch
    `trunk`.
- **Ruled out:**
  - *"stale cruft, safe to restore"* — killed by the all-branches blob scan above.
  - *"diverged, needs the preserve→`reset --keep` rescue"* — killed by `0 ahead`.
  - *"a separate homelab-talos repo exists"* — killed by the `gh repo view` miss plus the
    Flux `GitRepository` URL.
- **Leading hypothesis:** someone (or an agent) ported datapacket's dev-shell PR-status hook
  into this flake and lost #465's dependency block in the merge, then left it uncommitted.
- **Next probe:** none needed for diagnosis — this is a decision. If it proceeds, the
  question to answer first is whether the #465 block can be restored *on top of* the port
  (`git show origin/trunk:flake.nix` holds the good copy of that block).

### The `#task-<id>` deeplink reaches a collapsed-Done card LIVE but not HERMETICALLY
Not a broken feature — an unexplained divergence that makes "it works" a claim about an
environment rather than about the code. Both readings are measured; neither is inferred.

- **Symptom + exact repro:** navigate to `http://192.168.50.250:30302/tasks#task-<id>` for a
  task whose status is `complete` (it lives inside the collapsed `Done` `<details>`). Live it
  lands. In the e2e harness, with the handler's open removed, it does not.
- **Observed (with values):**
  - **LIVE on 0.8.19** (before the fix): `detailsOpen: true`, `inViewport: true`,
    `viewportRatio: 1`, `hashFocus: "true"`, **`hashOpened: null`** — the attribute did not
    exist, so nothing in the app opened it.
  - **LIVE on 0.8.20** (after the fix, same probe, task-407): identical, except
    **`hashOpened: "0"`** — the attribute now exists (proving the new code runs) and the
    handler found **nothing closed to open**. The browser had already opened it.
  - **HERMETIC** (`e2e/tests/tasks.spec.ts`, 24 seeded cards, full mode, Docker, 0 skipped),
    handler removed: `toBeInViewport` fails — **"viewport ratio 0"**. The card is unreachable.
    With the handler: `1 passed`.
- **Ruled out:**
  - *"the browser never auto-expands"* — killed by the live readings above, twice, on two
    versions.
  - *"the handler is what makes it work live"* — killed by `hashOpened: "0"` on 0.8.20.
  - *"the e2e is skipping"* — killed by `0 skipped` on every leg, with Docker up.
- **Leading hypothesis:** a timing/size difference. The live board is far taller (252 cards)
  and settles more slowly, so a native fragment resolution may land AFTER the cards exist,
  where the hermetic run's deterministic `waitAppSettled` does not give the browser that
  opportunity. **Untested.**
- **Next probe, verbatim:** seed the hermetic harness with a board large enough to match the
  live document height and re-run the mutant leg — if the card becomes reachable without the
  handler, size/timing is confirmed as the variable:
  ```bash
  # in containers/clawgate, after raising seedTasks(server.baseURL, 24) to ~250 in the new spec
  bash e2e/run.sh tasks.spec.ts -g "opens the collapsed Done section itself"
  ```

### RESOLVED 2026-09-01 — the live-vs-hermetic deeplink divergence does not exist
The block above ("The `#task-<id>` deeplink reaches a collapsed-Done card LIVE but not
HERMETICALLY") is **closed**, and its leading hypothesis — a timing/size difference,
the live board being taller and settling more slowly — is **REFUTED**. So is #468's
own premise. The prescribed next probe (seed the harness to ~250 cards) was NOT run:
it tests document size, which is not the variable, and it would have "confirmed size"
without naming a mechanism.

- **What is actually true.** Chromium expands a closed `<details>` for fragment
  **NAVIGATION** only, and **never** for a script-driven `scrollIntoView`. Measured
  over both binaries in the pinned `playwright-driver` bundle — `chromium-1228` **and**
  `chromium_headless_shell-1228`, checked separately because they are different
  binaries and the e2e skew workaround uses the latter — on a page whose target card is
  injected AFTER load, exactly as htmx does here:

  | action | closed `<details>` opens? |
  |---|---|
  | nothing (control) | no |
  | `scrollIntoView()` | **no** |
  | `scrollIntoView({block:'center',behavior:'smooth'})` | **no** |
  | `p.open = true` (what #468 shipped) | **yes** |
  | `location.hash=` / `<a href="#…">` click / `assign` / `replace` | yes |
  | fragment present at **parse** time | yes |
  | fragment present, target injected after **DOMContentLoaded** | **no** |

  Cards here arrive in a later htmx fetch — after `DOMContentLoaded` — so the browser's
  fragment anchor is already abandoned by the time the card exists.

- **Live confirmation, deployed 0.8.21, FRESH browser profile:** `/tasks#task-208` and
  `/tasks#task-407` both read `data-task-hash-opened="1"` — *the handler* opened it.
  Negative control, `/tasks` with no fragment: Done closed, card at `top 23048`,
  `inViewport: false`, no focus attribute. The server itself serves
  `<details data-task-section="Done" class="group/sec">` with **no** `open` attribute
  (61 tasks inside), so the closed starting state is read off the response, not assumed.

- **Why the two readings ever differed:** the recorded live `data-task-hash-opened="0"`
  came from a browser profile whose Done section was **already open**. A fresh profile
  reads `1` and behaves **identically** to the hermetic harness. Board size, document
  height and settle timing are all eliminated.

- **Consequence, and it upgrades #468:** the fix is not insurance against a
  hypothetical future browser change. On 0.8.19 a deeplink to any completed task was
  **broken for every visitor with a clean profile**. That is a real shipped failure it
  fixed. The e2e spec's `data-task-hash-opened >= 1` assertion is confirmed correct and
  was deliberately left unchanged.

- **Reusable probe:** `cdp2.py` (arms driven from OUTSIDE the page) and `live.py` in
  this session's scratchpad. Rebuild rather than hunt for them — the shape is a closed
  `<details>`, a card injected after load, one action per run, and a
  `Page.addScriptToEvaluateOnNewDocument` load counter.

### CLOSED — nothing is mid-diagnosis in this effort
Every investigation this doc opened is resolved: the deeplink deeplink-inert bug (rank 3,
shipped), #463 (refuted), the dirty-clone disposition (rank 7, executed), and the
live-vs-hermetic divergence (rank 8, refuted — see the RESOLVED block above, which
stands unchanged). Rank 9 is a grading decision, not a diagnosis. **A future session
should not read this section as work in progress.**

## Next steps (ranked)
🔴 Numbering is STABLE on purpose — `claim-work --slug-for <this doc> <rank>` derives
from it, so re-ranking silently re-points every live claim. Ranks 1–8 are CLOSED and are
carried forward verbatim rather than deleted, for exactly that reason.

1. ~~**Verify the two unverified interactions**~~ — **DONE**, merged as `#1086`. Closed by
   machine observation on an isolated Xvfb, not an operator report.
   forcing: none — closed, retained only so the rank numbering stays stable.
2. **CI capacity** — 🔴 **OWNED ELSEWHERE. DO NOT WORK IT HERE.** Canonical doc is
   `claudedocs/handoff-ci-speedup.md`, with live claims `ci-speedup-1` and `ci-speedup-2`.
   Its ordering contradicts the obvious one and its unpin measurement dwarfs the mapper —
   both under "CI capacity" in Gotchas below.
   forcing: gate — a required check is failing innocent PRs (measured: three consecutive
   runs of a docs-only PR, none about the diff). Real and external; simply not ours.
3. ~~**Fix `clawgate` deeplink**~~ — **DONE.** `#564` merged, 0.8.19 deployed, verified
   live. The handoff's closing condition (a spec RED before / GREEN after) is met;
   **#440's own is not** — see rank 6.
   forcing: none — closed.
4. ~~**Document the branch-protection escape hatch's asymmetry**~~ — **CLOSED AS OBSOLETE.**
   Read its reasoning before re-opening: the text already exists in `drift-check.sh`, rc 24
   detects the hazard and is live, and the edit as specified would make a one-way operation
   more usable. Under "Rank 4" in Gotchas.
   forcing: none — closed as obsolete, deliberately not done.
5. ~~**Merge `devrc#1099`**~~ — **DONE 2026-08-31**, squash `6bf866fe`. Closing condition
   met by content, not ancestry: all three files are blob-identical on `origin/main`.
   **The two blockers this doc recorded had both evaporated before I touched it** — the
   flaky gate was green (both required checks `SUCCESS` at 03:12:49Z, after someone pushed
   `246d0522` paying the skill byte ceiling), and the `handoff-tmux-webapp.md` semantic
   conflict was moot because main's commits since the branch point touched **zero** files
   the branch touches. Merged-tree gate run anyway: tier 1 (devShell) 19,910 passed / 3
   skipped / 0 failed against floor 18,383; tier 2 `nix` pytests + nodetests both
   `RESULT: PASS`, built one at a time. Then **shipped** — see the deploy gotcha below.
   forcing: none — closed.
6. ~~**Fix clawgate #463, then close #440**~~ — **CLOSED 2026-08-31 AS REFUTED. There is no
   defect.** Measured live on 0.8.19, headless Chromium, both viewports (1280×720 and
   390×844). The arithmetic closes with no residue: the 62 "unreachable" cards are **59**
   inside the deliberately collapsed `Done` `<details>` plus **3** final-screenful cards
   that are fully visible at the bottom of the viewport. `trulyOffscreenAtMaxScroll` = **0**
   at both viewports, and the last RENDERED card sits at bottom 532 ≤ 720 (desktop) and
   656 ≤ 844 (phone) — criterion 1 already passes.
   - **The under-reporting box, named:** an ancestor-chain walk from the last card found
     `spill: 0` everywhere except `<details class="group/sec">`, which reports
     `offsetHeight: 48` (its `<summary>`) while its child grid extends **6,769px** past it.
     Control: `d.open = true` → `offsetHeight` 48 → 6,817 and document `scrollHeight`
     22,261 → 29,030.
   - **Why the original measurement saw phantom geometry:** Chromium implements
     `::details-content` with **`content-visibility: hidden`**, not `display: none` —
     measured via `getComputedStyle(d, '::details-content')`, not assumed. Collapsed
     descendants keep a queryable layout, so any script that enumerates
     `article[id^="task-"]` and compares rects counts them as laid out.
     🔴 **`document.scrollHeight` was always CORRECT.**
   - 🔴 **#463's criterion 2 is unsatisfiable as written** — "cards with document-y beyond
     maxScroll == 0" is impossible for *any* scrollable page, since the final screenful
     always has `top > maxScroll` while being fully visible. A criterion that cannot be
     met by a correct page is a trap for whoever picks it up.
   - **#440 is NOT blocked.** `/tasks#task-208` (status `complete`, inside the collapsed
     section) lands today: `scrollY 28310`, `rectTop 390`, `inViewport true`,
     `data-task-hash-focus="true"`.
   - **The real finding, now clawgate #468:** `taskHashScript` never touches `<details>` —
     verified by reading the whole handler (no `.open`, no `details` reference; its only
     scroll call is `el.scrollIntoView(...)`). The deeplink works because **Chromium**
     auto-expands a closed `<details>` when a fragment targets content inside it. Nothing
     pins that, so a browser change would silently regress deeplinks to every completed
     task — the exact failure #440 existed to remove.
   forcing: none — closed as refuted, with #468 carrying the residue.

7. ~~**Merge `ZacxDev/homelab-infra#616`**~~ — **DONE 2026-09-01**, squash `437cd091`.
   Closed by CONTENT: blob `83a4112f` on `trunk`, corrected framing present, false
   "two repos" claim 0 occurrences. The follow-up it named is also done — that clone's
   `deploy/SKILL.md` was restored and the clone fast-forwarded, so it is clean again.
   forcing: none — closed.
8. ~~**Merge `ZacxDev/homelab-infra#617`**~~ — **DONE 2026-09-01**, squash `884aae25`.
   Merged `UNSTABLE` deliberately: both reds said `COULD NOT RUN: … stopped before any
   leg reported`, and `clawgate-e2e` — the check that actually exercises that file —
   was SUCCESS at 127 tests / 2 skipped. ⚠ Whether `clawgate-ci` is *required* on
   `trunk` is STILL UNMEASURED: `GET /branches/trunk/protection` 403s on that private
   repo without GitHub Pro, so "not required" remains inferred from #613 having merged
   red, never read from protection.
   forcing: none — closed.
9. **Grade clawgate #440 and #468** — both `ready_for_review`, both blocked on a HUMAN.
   🔴 An agent must not close either: #440's criteria were written by a previous session
   and #468's by another, so grading is self-grading whichever way the
   `## Acceptance criteria` detector reads. **Evidence for the grader, now on the task
   as a comment:** #468's stated premise is refuted, which makes its fix MORE
   load-bearing — it repaired a live failure (0.8.19 was broken for every clean-profile
   visitor) rather than pinning a browser detail. #440's own blocker (#463) remains
   refuted. **This is the only open item in the effort.**
   forcing: none — nothing external waits on the status flip.

## Gotchas / decisions / dead-ends
- 🔴 **A task's own MEASUREMENT can be the artifact — check what your selector can SEE
  before trusting a count.** #463 was filed off a real, careful, numerically specific
  measurement that was nonetheless wrong, because `querySelectorAll` returns elements
  inside a closed `<details>` and their rects are non-zero under `content-visibility:
  hidden`. The count was honest; the *set* was wrong. **Ask which of the elements your
  query returns are not actually rendered** — `el.closest('details:not([open]))` was the
  whole diagnosis, and partitioning by it made 62 resolve into 59 + 3 with no residue.
  Generalises past `<details>`: any visibility mechanism that preserves layout
  (`content-visibility`, `visibility: hidden`, an offscreen transform) feeds phantom
  geometry to a rect-comparing script.
- 🔴 **A criterion that a CORRECT system cannot satisfy is a trap, not a bar.** #463's
  criterion 2 ("cards with document-y beyond maxScroll == 0") is unsatisfiable for any
  scrollable page — the last screenful always has `top > maxScroll`. An agent picking it
  up would have "fixed" it by expanding or deleting content. When writing criteria, ask
  what a healthy system scores.
- ⚠ **The clawgate e2e nix shell is version-skewed and `run.sh` cannot launch a browser
  as configured** — `e2e/shell.nix` says `@playwright/test` and nixpkgs
  `playwright-driver` must match, but the client is pinned **1.59.1** (wants chromium
  revision **1217**) while `playwright-driver.browsers` now ships **1228**. Workaround
  that worked: launch with an explicit `executablePath` at
  `…-playwright-browsers/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell`
  plus `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true`. **Pre-existing, unfiled, and NOT
  investigated** — recorded here so the next person does not spend the same 20 minutes
  concluding the suite is unrunnable. It does not affect `tekton/clawgate-e2e`, which
  builds its own environment.
- 🔴 **Merging #1099 changed NOTHING for the agents it was written for until the switch —
  and the stale copy still asserted the very claim the PR deleted.** `claude/skills/**`
  deploys as a `home.file` **copy**, so `readlink -f ~/.claude/skills/clawgate/reference/
  extension.md` terminated in `/nix/store` and the live text was the OLD one: the false
  assertion present, **0** occurrences of `clawgate-e2e`. A doc-only merge feels finished
  at the squash; here it was half done. After `ship.sh`: store path moved on **both**
  hosts (`zrapkwjp…` → `yclky1ds…`, identical), false assertion **0**, `clawgate-e2e` **4**.
  Report the deploy and the consumer read as two claims.
- 🔴 **The blocker a handoff records is a HYPOTHESIS about the past, and both of #1099's
  had expired.** The doc said "blocked by the flaky gate" and "check for a semantic
  conflict"; by the time I read it the gate was green and the overlap was gone. Re-measure
  a recorded blocker before planning around it — the cost of not doing so is a session
  spent defeating an obstacle that is not there.
- ⚠ **`origin/main` moved mid-gate (`57b010fb` → `9a7c4338`), invalidating a merged-tree
  run that had just finished.** With `strict: false` and main moving every ~20 min, a full
  re-gate per move is unwinnable. What worked: diff the new base against the old, and
  re-run only the surface that could interact — here the repo-wide doc/skill/rules
  scanners (561 passed), since the new commit was docs-only and file-disjoint. Say which
  subset you re-ran and why, rather than implying a full re-gate.
- ⚠ **A red gate can be a MISSING ENVIRONMENT, not a code failure.** My first tier-1 run
  reported `RESULT: FAIL (exit=3)` — `run-tests.sh` refusing because `logrotate` was off
  PATH, because I ran `gate.sh` outside the flake devShell. It says so explicitly and
  prints the `nix develop … run-tests.sh` line to use. Read the reason before treating a
  red as a verdict on the diff.
- ⚠ **GitHub code search is BLIND on `ZacxDev/homelab-infra`** — a positive control for
  `filename:flake.nix` returned `total_count: 0`. An empty code-search result there proves
  nothing about the repo. Use `git/trees/<ref>?recursive=1` instead; that is how
  `clawgate-e2e-pipeline.yaml` was confirmed to exist.
- 🔴 **A terminal-UI interaction can be verified WITHOUT taking the operator's screen —
  `Xvfb` + the deployed config + an `xdg-open` capture handler.** This is the pattern
  that closed rank 1, and it generalises to any alacritty/hint/`xdg-open` behaviour.
  Three pieces: (1) `Xvfb :99` and launch the REAL binary on it, so XTEST input is real
  input and no `--window` false-negative is possible; (2) a probe `XDG_CONFIG_HOME`
  containing **only** `mimeapps.list` + a **symlink** to `~/.config/alacritty`, so the
  config under test is provably the deployed store file (`readlink -f` it and say so) —
  copying the config would have tested a copy; (3) a probe `XDG_DATA_HOME` with a
  `.desktop` whose `Exec` appends `%u` to a log, bound to `x-scheme-handler/http{,s}`,
  so "it opened the right URL" is a grep instead of a browser tab. Run the positive
  control (`xdg-open` under the probe env) BEFORE trusting any silence from it.
  ⚠ It does NOT cover the operator's already-running windows — say that separately.
- 🔴 **`PREV_WS` went stale inside two minutes, and the first attempt put a window on
  the operator's game.** The recorded `PREV_WS=1`/`PREV_WIN=…` were read, then the
  probe terminal was launched ~2 min later — by which point the focused workspace was
  **4** with a fullscreen game on it and `xprintidle` reporting **1 ms**, i.e. hands on
  the keyboard. The new window landed on THAT workspace (i3 opens on the focused one,
  not the one you remembered) at 0×0 behind the game. Restoring to the remembered
  workspace would have been a SECOND theft — yanking them out of the game — so the
  right move was to kill the probe window and leave the workspace alone. **Re-read
  focus/idle immediately before the raise, not in the survey that motivated it, and
  check `xprintidle` before driving XTEST at all** — synthetic keys would otherwise
  have gone into whatever they were doing.
- 🔴 **`hints.enabled` is an ARRAY — declaring it REPLACES alacritty's built-in default.**
  No merge. Adding the mention hint without re-declaring the URL hint verbatim would
  silently kill URL clicking, with no error. `test_alacritty_hints.py` pins it.
- 🔴 **The hint regex is deliberately LOOSER than the scanner.** Rust's regex crate has
  no lookaround, so `{1,6}` swallows a whole hex colour rather than `{1,5}` matching five
  digits of `#282828` and offering "task 28282". The handler is the authority.
- 🔴 **`xdotool key --window <id>` uses `XSendEvent`, which winit/Alacritty IGNORES.**
  Two automated click tests reported **false negatives** before the third worked; it even
  echoed a stray `^A` into the shell. Only XTEST (`xdotool key`, no `--window`, against
  the FOCUSED window) delivers real input. Stopping at attempt two would have reported a
  working feature as broken.
- 🔴 **The branch-protection escape hatch does NOT round-trip.** `DELETE
  …/protection/required_status_checks` cannot be undone with `PATCH` — it 404s
  "Required status checks not enabled". Re-enabling needs a full `PUT …/protection`
  with the ENTIRE object reconstructed (`enforce_admins`, `strict`, and the `app_id`
  pinning). A restore-in-a-trap reported OK and had silently failed; `main` would have
  been left permanently unprotected. Capture the full config BEFORE deleting.
- **#1058 was closed in favour of #1060** — a competing fix. Mine relaxed the guard to
  tolerate ambiguity; #1060 removed the ambiguity via `_AMBIGUOUS_TERM_OWNER`, keeping
  BOTH picker rows and attribution. Decider: `ask` is the highest-traffic term in the
  config (58 fires); #1058 would have permanently blinded telemetry on it. `search_terms`
  serves two consumers with opposite needs — the picker wants recall, `_attribute` wants
  precision.
- **Two merges went AROUND the gate**, not through it (#1060, #1011) — via the escape
  hatch, with a local both-tier `nix build` substituting. #1067 is the only one that
  passed the real gate; Tekton and the local run agreed to the exact test counts.
- 🔴 **My own error worth not repeating:** #1011 verified the `#task-N` URL *resolved*
  and the DOM id *existed*, then INFERRED navigation and recorded "anchor: verified and
  used". Nothing ever loaded the page and watched it move. **An id that exists is not an
  anchor that works.** That is what #440 now is.
- **Alacritty `keyboard.bindings` `chars` serialise as TOML LITERAL strings** (`''`)
  on the current flake pin, where the older deployed copy had basic strings. Proven
  pre-existing by a before/after control, NOT caused by #1011. Whether Alacritty still
  honours `chars` in that form is **unverified** — that's Ctrl+Backspace and word-motion.

- 🔴 **CARRIED FORWARD from `State now` before a replace dropped it — rank 1's RESULT, which
  is a measurement and not status.** Both alacritty interactions verified **off-screen** on
  an isolated `Xvfb :99`: same binary, same DEPLOYED config
  (`/nix/store/9jmni…-alacritty.toml`, reached via a probe `XDG_CONFIG_HOME` symlinking
  `~/.config/alacritty`, so `readlink -f` lands on the identical store file), real XTEST
  input. **`mouse.enabled` hover** underlines the mention alone — not the URL above it, not
  the plain word below. **Plain left-click** on `devrc#1011` captured
  `https://github.com/innovation-upstream/devrc/issues/1011`. **A bare `#370`** raised the
  rofi picker and **row 2** opened `…/issues/370`, so the picker resolves by row CONTENT,
  not index. **`Ctrl+Shift+O`** labelled the URL `j` and left the mention unlabelled;
  activating it captured the whole URL **with the terminal line wrap reassembled**.
  Controls both watched: positive (a bare `xdg-open` under the probe env fires) and
  negative (hover+click on plain text leaves the log unchanged).
  ⚠ Still NOT verified: these gestures in Zach's own long-running alacritty windows.
- 🔴 **CARRIED FORWARD — the `seed-nix` RETRACTION, so it is not lost with rank 2's old
  text.** An earlier revision blamed `seed-nix` for the 43 minutes before the test steps
  started. That was **INFERENCE FROM A GAP, never a measurement of the step, and it is
  WRONG**: across 114 retained TaskRuns `seed-nix` is `min 0.0s / p50 0.0s / max 129.0s`, a
  no-op on a warm cache. The gap is **pod SCHEDULING** — Tekton's TaskRun timeout starts at
  creation and includes `Pending`, and gate pods were measured `Pending` 11–12 min with 5
  running + 5 queued. Naming the step I could see would have sent the next person to
  optimise a step that costs nothing.
- 🔴 **CI capacity (rank 2) — three fixes ruled out with the measurement that killed each.
  Do not re-derive them.** Moved here from the ranked item so a future `State now` replace
  cannot drop it.
  - **RWX migration — DEAD.** Not a PVC edit; "install distributed storage on Talos"
    first. **0 CSI drivers, 0 RWX PVs of 294, 0 RWX PVCs of 289**, each with a positive
    control so the zeros are absences and not broken queries. Tried and reverted once
    already (`d149c87f`, #111). The volume holds **69 GB / 25,664 store paths** (the PVC
    *requests* 30Gi; `local-path` enforces no quota). 🔴 **RWO was never the blocker** —
    six pods share it concurrently, because RWO is per-**NODE**, not per-pod. A per-node
    `hostPath` cache is separately forbidden by Talos PodSecurity `baseline`.
  - **Concurrency cap — DEAD.** `tekton-supersede` already covers `devrc-ci` (**43** runs
    carry a supersede key). It cancels older runs sharing a key; the 10 pods measured were
    **10 distinct PRs**, correctly not superseded. Capping queues real work rather than
    removing redundant work. ⚠ A first query returned `0` only because it used label value
    `devrc-ci` instead of `devrc-ci-pipeline` — a positive control over all pipelines
    caught it. Never read a bare zero here without one.
  - **#396-style static split — DEAD, and it would DEMOTE the pipeline it was meant to
    rescue:** the only viable node fits **1–2** concurrent gate pods against the ~**5**
    devrc gets today. Nearly proposed on the strength of the precedent; arithmetic stopped
    it.
  - **The owning effort already tried the UNPIN, and that is the real lever** — queue wait
    `17.2m/22.5m → 0.1m`, wall clock `39.1m median → 17.4m`. Reverted because a
    `DirectoryOrCreate` hostPath is created **root-owned**: `opening lock file
    "/nix/var/nix/db/big-lock": Permission denied`, **75 occurrences / 42 tests per PR**,
    against **0** in the PVC-era control. Retry is gated on an ownership probe **on a
    scratch pipeline**, never on `devrc-ci`.
  - **A path→target mapper is worth ~1.7x alone but ~3.6x after `scripts/tests` is
    decomposed**, so decomposition comes FIRST and must be **measured** — that effort's
    regex classifier over-classifies and is explicitly untrusted. Estimate history:
    **3x → 1.7x → uncertain**.
- 🔴 **Rank 4 — why the branch-protection paragraph must NOT be written.** The text it asks
  for already exists at the site an operator reads (`scripts/drift-check.sh`: `PATCH`
  cannot restore the sub-resource after a `DELETE`; restoring needs a full `PUT`). A
  deterministic detector covers the hazard and is **live** — rc 24, merged as `#1065`,
  `drift-check.timer` `active`+`enabled`. And the edit **as specified** would make a
  one-way operation *more* usable exactly when someone reaches for it under pressure, while
  preventing **neither** recorded incident — the first one *had* a restore trap, it *ran*,
  and main was left unprotected anyway.
- 🔴 **THE PROCESS FAILURE OF THIS SESSION, and it is the reusable part: rank 2 was
  investigated for a full session before anyone ran `claim-work --list`.** Two live claims
  (`ci-speedup-1`, 1d old; `ci-speedup-2`, 3h old) and a more advanced handoff already
  existed. Nothing reached the cluster and no duplicate PR was opened, so the cost was
  research effort — but the sweep is ONE command and would have reordered the whole effort
  from the start. Ranks 1 and 3 *were* claimed; rank 2 was not, because it was reached by
  drifting forward from a finished item rather than by picking one off the list. **The
  sweep belongs at the moment you START an item, not at the moment you formally adopt it.**
- 🔴 **Verifying a terminal UI without taking the operator's screen — `Xvfb` + the deployed
  config + an `xdg-open` capture handler.** (1) `Xvfb :99`, launch the REAL binary so XTEST
  input is real input; (2) a probe `XDG_CONFIG_HOME` holding **only** `mimeapps.list` and a
  **symlink** to `~/.config/alacritty`, so the config under test is provably the deployed
  store file (`readlink -f` it and say so) — copying it would test a copy; (3) a probe
  `XDG_DATA_HOME` with a `.desktop` whose `Exec` appends `%u` to a log, bound to
  `x-scheme-handler/http{,s}`, so "it opened the right URL" is a grep instead of a browser
  tab. Run the positive control BEFORE trusting any silence from it.
- 🔴 **`PREV_WS` went stale inside two minutes.** Focus/idle were recorded, then a probe
  window was launched ~2 min later — by which point the focused workspace held a
  fullscreen game with `xprintidle` at **1 ms**. Restoring to the remembered workspace
  would have been a SECOND theft. **Re-read focus/idle immediately before the raise, and
  check `xprintidle` before driving XTEST at all.**
- 🔴 **Two vacuous-assertion traps in the deeplink e2e, both avoided and both worth
  reusing.** `toHaveClass(/card-enter/)` cannot fail — *every* card ships with that class.
  `getAnimations().length` cannot return to zero — the animation fills `both`, so a
  finished animation stays in the list forever. The spec filters on
  `playState === 'running'` instead.
- **My own first draft of the deeplink handler was wrong twice, and the SPEC caught both,
  not re-reading the code.** (a) "not found" is meaningless until the list has rendered
  once — the handler ran while parsing the shell, before `#tasks-list` had issued its
  `hx-trigger="load"` fetch, so every id looked absent: it toasted "not on this board" and
  dropped the fragment *before the cards existed*. (b) The filter-clear retry re-enters
  through the settle it triggers, so it needed a once-per-fragment latch.
- 🔴 **`clawgate-ci` does NOT run Playwright — but `clawgate-e2e`, a SEPARATE check, DOES**
  (`clawgate e2e passed — 122 tests, 2 skipped` on #564). The skill and `reference/
  extension.md` said the browser layer was "UNGATED by CI"; that is FALSE and `#1099`
  corrects it. ⚠ **RUNS is not BLOCKS** — whether `clawgate-e2e` is *required* is
  unmeasured: `GET /branches/trunk/protection` 403s on that private repo without GitHub
  Pro.

- 🔴 **A handoff's recorded BLOCKER is a hypothesis about the past, and three of this
  effort's were dead on arrival.** #1099's "blocked by the flaky gate" (green by the time
  it was read), its "check for a semantic conflict in `handoff-tmux-webapp.md`" (main's
  commits since the branch point touched zero files the branch touched), and rank 7's
  "cannot fast-forward / may be stale cruft" (0 ahead, and both files novel). **Re-measure a
  recorded blocker before planning around it** — the cost of not doing so is a session spent
  defeating an obstacle that is not there.
- 🔴 **`claim-work --subject` is a NO-OP on a claim you already hold.** It prints
  `✅ THIS IS YOURS — carry on. Nothing to do.` and **silently keeps the old subject**, so a
  claim whose premise you have just refuted goes on advertising that premise to every other
  session. Measured 2026-08-31. The fix is `--release` then re-claim with the new `--subject`;
  there is no in-place edit.
- 🔴 **A red required check is not automatically about your diff — READ WHICH TEST.**
  `tekton/devrc-pytests` failed #1168 on `test_mkdir_refuses_unsafe_names[..]`, a
  real-process test driving a LIVE HTTP server in `dl-router`, against a diff of **one
  markdown file**. The discriminating control, not a re-run: the `nix` pytests derivation on
  the MERGED tree locally reported `collected=19942 passed=19939 skipped=3 failed=0`, and
  Tekton's red run collected the **identical 19942** — same tree, same test set, one
  real-process test differing. The re-run then reported those exact numbers. **Compare the
  collected counts**; they are what tells you it is the same suite.
- ⚠ **A gate failing on a MISSING ENVIRONMENT looks like a code failure.** `scripts/gate.sh`
  returned `RESULT: FAIL (exit=3)` purely because `logrotate` was off PATH — it was run
  outside the flake devShell. It says so explicitly and prints the
  `nix develop … run-tests.sh` line to use. Read the reason before treating a red as a
  verdict on the diff.
- 🔴 **GitHub code search is BLIND on `ZacxDev/homelab-infra`** — a positive control for
  `filename:flake.nix` returned `total_count: 0`. An empty code-search result there proves
  nothing. Use `gh api repos/…/git/trees/<ref>?recursive=1`.

- ⚠ **`origin/main` moves several times an hour here** — measured **six times during one earlier
  session's own gate runs**, and this session re-gated merged trees four times for the same
  reason. With `strict: false` on branch protection, a green check is a claim about the PR's
  BRANCH, never about the tree the merge creates, so gating the merged tree stays manual.
- 🔴 **The store-api CI "flake" is fsync CONTENTION — named, reproducible, and now SHIPPED as
  a tool.** `scripts/ci-repro/slowfsync.c` + its README (devrc `0c333846`) make it fail on
  demand on the dev host in ~70 s. Mechanism: `server.py:_replace_bytes` fsyncs BEFORE the
  response is written, so under disk load the client's `HANG_TIMEOUT` (60 s) expires and the
  gate reports a **code failure for an I/O stall**; the suite's own classifier prints
  `MECHANISM = SERVER_BLOCKED_IN_FSYNC` unprompted. **Do not raise `HANG_TIMEOUT` again** and
  **do not "fix" it with CPU/memory requests** — k8s requests govern CPU and memory, not IOPS.
- 🔴 **FOUR audit rounds on that PR, and THREE of them found the error in the PREVIOUS
  round's fix.** R1 found a false storage claim; R2 found my correction was also false
  (contention set 7, not 12; and the lever still could not move the failing write); R3 found
  my citation fix had shipped a THIRD wrong line citation, invalidated by its own commit.
  **Recounting was never the fix.** What held was structural: replace literal numbers with
  runnable derivation commands, and cite by NAME never by line. Read `scripts/ci-repro/`
  before quoting any figure in it — it says so about itself.
- 🔴 **A line citation into a file you are EDITING is a defect generator.** Three instances in
  one PR. The comment block shifts the lines it cites, so even a fresh recount goes stale
  before it is committed.
- 🔴 **`claim-work --subject` is a NO-OP on a claim you already hold** — rc 0, "THIS IS YOURS",
  old subject silently kept. A refuted premise went on advertising itself to other sessions.
  Fix: `--release` then re-claim. Recorded in `claude/skills/handoff/reference/shared-queue.md`.
- 🔴 **Merging clawgate code to `trunk` deploys NOTHING** — the pin is an immutable literal tag
  with no Flux image automation. #613 merged and changed nothing running until the 0.8.20 pin
  bump. "Merged" and "deployed" are separate claims; `clawgatectl health` is the arbiter.
- 🔴 **An instrument that fails QUIET is worse than one that fails loud.** `slowfsync.c`'s
  first version discarded `sleep()`'s return, so a signal could shorten the stall while
  printing an identical success line — an under-delivered stall would produce a PASSING run
  reading as "not reproducible", i.e. it lied in exactly the direction that makes you abandon
  the investigation. It now reports MEASURED elapsed.
- 🔴 **Assertion ORDER decides whether a guard can run at all.** In #468's spec the
  discriminating assertion sat after `toBeInViewport`; under the mutant that check fails first
  and the guard never executes — green for the wrong reason, and still green if deleted. Put
  the discriminating assertion FIRST, then the user-visible one.
- ⚠ **Two `tekton` e2e/gate failures this session were NOT the diff** — devrc's on
  `test_mkdir_refuses_unsafe_names` / `test_subsystem_store_api.py`, clawgate's on
  `clawgate health check did not pass on port <N> within 15000ms`. The discriminating control
  both times: the SAME failure on a DIFFERENT sha's run. Different tests, one shape — a
  wall-clock bound under CI load.
- ⚠ **`clawgate-e2e` is NOT a required check on homelab-infra `trunk`** — measured: #613
  merged with it red (after diagnosing the red as unrelated). "RUNS is not BLOCKS" was
  previously unmeasured; it is now measured, in the negative.
- ⚠ **The JS in `taskHashScript` lives inside a Go BACKTICK raw string**, so a backtick in a
  comment terminates the literal and breaks the build. Caught immediately, but non-obvious.
- ⚠ **devrc `nix/pkgs/default.nix` was never unsaved work** — it was open PR #1135 applied in
  the tree. Checking a dirty file against open PRs before "rescuing" it cost one command and
  avoided inventing a problem. #1135 has since merged.

- 🔴 **A CODE COMMENT CAN ARGUE FOR DELETING THE CODE IT DESCRIBES — and that is the
  expensive direction to be wrong in.** `openAncestorDetails` shipped with
  "🔴 THIS LOOKS UNNECESSARY, AND THAT IS THE POINT … Chromium auto-expands a closed
  `<details>` when scrollIntoView targets content inside it". Measured false on both
  pinned binaries. A maintainer trimming "redundant insurance" would have broken the
  deeplink for every completed task, with a green suite — the spec asserts
  `data-task-hash-opened >= 1`, which the deletion also removes the means to satisfy.
  **Read a comment that tells you its own code is unnecessary as a claim to test.**
- 🔴 **The cheap discriminating control beat the recorded next probe, and the recorded
  probe would have produced a CONFIDENT WRONG ANSWER.** The doc prescribed seeding the
  harness to ~250 cards to test size. Size is not the variable; a bigger seed would
  plausibly have flipped the result and been recorded as "size confirmed". What
  actually discriminated was a 20-line static page with one action per run. **When a
  handoff hands you a probe, ask what it VARIES before running it.**
- 🔴 **`--dump-dom --virtual-time-budget` STALLS across a same-document navigation and
  reports a page that never ran as `pending`** — indistinguishable from a page whose
  script threw. Two arms (`location.hash=`, synthetic anchor click) read `pending` at
  budgets of 15s, 30s and 60s and looked like failures; under a real-time CDP driver
  both were **`true`**. The instrument, not the browser. Drive CDP directly when a
  measurement involves navigation.
- 🔴 **`location.assign` can silently RELOAD, which measures a different arm while
  looking like the one you meant.** A parse-time fragment expands the `<details>`; a
  post-load one does not — so a reload would have "confirmed" the navigation arm using
  the parse-time mechanism. The control is a
  `Page.addScriptToEvaluateOnNewDocument` counter: every arm here held
  `documentLoads: 1`. **Any nav-based browser measurement needs a reload counter.**
- 🔴 **A FRESH BROWSER PROFILE IS PART OF THE EXPERIMENT.** The whole retracted
  divergence traces to one live reading taken in a profile whose Done section was
  already open. Per-tab/profile UI state is invisible in the reading and survives a
  same-tab hash navigation. **Use a throwaway `--user-data-dir` per run, and say you
  did** — "measured live" without it is a claim about that profile.
- 🔴 **`echo "rc=$?"` after a PIPE reports the LAST command's status — I hit the
  documented trap anyway.** `go build … | head -20; echo BUILD_RC=$?` printed
  `BUILD_RC=0` for a build I had not read. Re-run captured `out=$(…); rc=$?` → the real
  rc. **Never put a pipe between a build and its status.**
- 🔴 **Validate a build the same way as any other instrument — both directions.** For
  #617 the negative control was injecting a backtick into JS living inside a Go
  backtick raw string: `rc 1`, `syntax error: unexpected name backtick`; restored,
  `rc 0`. A comment-only change that "still builds" proves nothing until the build has
  been watched to fail.
- 🔴 **A rescued doc's FRAMING can be false while its lesson is true — verify the
  claims before landing someone else's prose.** The rescued `deploy/SKILL.md` addition
  blamed two repos holding byte-identical trees. `ZacxDev/homelab-talos` **does not
  resolve**; there is ONE repo, `homelab-infra`, with many local checkouts — the
  derivation shipped in #616 lists **17**, exactly one on `trunk` and one detached, and
  the long-lived clone *named* `homelab-talos` has `homelab-infra` as its origin. The
  hazard is the **CHECKOUT**, not the repo. Landing it as written would have sent a
  reader hunting a repo that does not exist.
- ⚠ **`clawgatectl task list --status complete` produced NO OUTPUT and exit 0** — a
  silent zero. Getting a completed task id came from parsing `/ui/tasks` for the
  `data-task-section="Done"` block instead. Unfiled; do not read that command's silence
  as "no completed tasks".
- ⚠ **A `COULD NOT RUN: … stopped before any leg reported` check is posted as
  `failure`, not `error`** — so a rollup that only reads conclusions calls it a failing
  diff. Read `.statuses[].description`, and get a control from an unrelated open PR
  before spending anything on it.

- 🔴 **`handoff_doc.py --push` CAN NO LONGER LAND ON devrc `main` — branch protection
  rejects it, and the commit is left on LOCAL main.** Measured 2026-09-01:
  `GH006: Protected branch update failed … 2 of 2 required status checks are expected`,
  `status=push-failed`, commit `d5778150` local-only — precisely the state `ship.sh`
  skips silently. The tool says so and hands over preserve → verify → `reset --keep`,
  which worked: branch pushed, sha confirmed on origin by an INDEPENDENT `ls-remote`
  (not the push's own word), `main` reset back. **Expect this every time now** — plan on
  a topic branch + PR, or run `/handoff` from a WORKTREE so `main` is never touched.
- 🔴 **`Next steps` is a REPLACE section, so a delta that lists only the OPEN ranks
  DELETES the closed ones — and rank number is half a `claim-work` slug's identity.**
  Nearly shipped: a delta carrying only 7–9 would have removed ranks 1–6 and silently
  re-pointed every live claim on this doc. Caught by the tool's own
  `DROPS N line(s) that look DURABLE` warning. **Always carry the closed ranks forward
  verbatim.**
- 🔴 **CHECK WHETHER A "STRANDED" FILE IS ALREADY SAFE BEFORE RESCUING IT — one command,
  and the answer here was yes.** `handoff-limewire-torrent-comps.md` looked like classic
  unsaved work (untracked, 3 days old, in a shared clone). `git log --all
  --diff-filter=A -- <path>` found it already inside `c40261bc` on origin, blob-identical.
  A second rescue would have created a competing copy of another session's work and read
  as a new finding. **`--diff-filter=A` plus a blob compare is the whole check.**
- 🔴 **DO NOT read "idle" from a query that FAILED — that is an empty result standing in
  for two different mechanisms.** The first `pg_stat_activity` probe on the leaked
  container errored `role "postgres" does not exist`; treating that as "no connections"
  would have been a coin flip. The real role came from `POSTGRES_USER` in the container
  env, and the answer was reported as a PAIR: positive control **6** rows (proving the
  query sees backends) against **0** other client backends. Never quote the zero alone.
- ⚠ **zsh does not word-split, and it silently produced a wrong `gh` invocation.**
  `for spec in "repo 1201" …; set -- $spec` left `$1` holding the WHOLE string and `$2`
  empty, so every call died `argument required when using the --repo flag`. Loud here;
  the same construct returns a confident wrong VALUE elsewhere. Use a literal list or
  `${=var}`.
- ⚠ **A `COULD NOT RUN: … stopped before any leg reported` check posts as `failure`, not
  `error`** — so any rollup reading conclusions calls it a failing diff. Read
  `.statuses[].description`, and get a control from an unrelated open PR before spending
  anything on it: `clawgate-ci` was red the same way on `#450`, which cannot have caused it.
- 🔴 **STANDING POLICY, not a one-off: a dirty tree you did not dirty is MEASURE-AND-
  REPORT ONLY** — no commit, restore, checkout or delete (operator, 2026-08-31). Moved
  here from a ranked item because ranks get replaced and this must not vanish with one.
  This effort departed from it exactly once, for `flake.nix`/`SKILL.md`, and ONLY because
  the operator was asked and chose the disposition explicitly. **That is not precedent.**
  Absent a fresh decision: measure, preserve without touching the tree, hand it over. The
  preserve technique that honours the policy — temporary `GIT_INDEX_FILE` + a push
  straight to a remote ref — touches no working tree, index or local ref.
- 🔴 **`rescue/workbench-dirty-tree-2026-08-31` (`c40261bc`, on origin) is now the ONLY
  copy of two things** — do not delete it: the DROPPED `flake.nix` port
  (`c40261bc:flake.nix` = `f58b97ce`, which deletes #465's dependency-closure block and
  must not be landed as-is), and `claudedocs/handoff-limewire-torrent-comps.md`
  (`df6219f2`), which is otherwise only an untracked file in one clone.

## How to verify
```bash
# all three merges, by CONTENT — a squash never makes the head an ancestor, so
# `merge-base --is-ancestor` returns false forever and is the wrong instrument here.
git -C $DEVRC rev-parse origin/main:claudedocs/handoff-mention-detection.md   # d3ef5d45…
R=~/workspace/homelab-talos
git -C $R rev-parse origin/trunk:.claude/skills/deploy/SKILL.md               # 83a4112f…
git -C $R show origin/trunk:containers/clawgate/internal/ui/components.go \
  | grep -c 'THIS LOOKS UNNECESSARY'                                          # must be 0

# the rank-8 finding, live. Expect data-task-hash-opened="1" on a FRESH profile —
# a "0" means the profile already had Done open, the exact trap that produced the
# retracted divergence. Throwaway --user-data-dir every run.
clawgatectl health          # 0.8.21
```
🔴 Run **both** tiers before claiming a merge is safe, and name the tier and base sha:
`scripts/gate.sh` is the dev host; `nix build .#checks.x86_64-linux.{pytests,nodetests}`
is what Tekton gates on. **Build them ONE AT A TIME** — a combined invocation contends
on the nix store and produces false failures.
