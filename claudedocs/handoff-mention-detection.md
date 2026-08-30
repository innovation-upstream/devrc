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
🔴 **ALL FOUR RANKS ARE NOW CLOSED EXCEPT RANK 2 (CI capacity).** Ranks 1 and 3 shipped;
rank 4 is closed **as obsolete** — read its entry before re-opening it, the reasoning is
the deliverable. What is genuinely still open is listed under "Still open" below.

- Branch: `main`, clean. `origin/main` was `e0e29e7b` when this doc was written and is
  `ac64ccb4` as of 2026-08-30 — the moves since are other threads' work, none touching
  the mention feature. It moved **four times during this session's own gate runs**, which
  is why every merge here re-checked the merged tree rather than trusting a branch-green.
- **clawgate is 0.8.19 live** (`clawgatectl health`), pin commit `8503620a` on
  `homelab-infra` trunk, carrying the `#task-N` deeplink fix.

**Still open, with owners:**
- **Rank 2, CI capacity** — untouched, and it BIT this session repeatedly. Diagnosis
  added to that rank.
- **`innovation-upstream/devrc#1099`** — corrects a false 🔴 claim ("the browser layer is
  UNGATED by CI") in three places. Correct and locally verified (526 tests); **BLOCKED by
  the flaky gate**, not by its content. Worktree `devrc-clawgate-ci` is deliberately left
  on disk holding its branch.
- **clawgate #463** — the board-scroll layout bug found by verifying #440 live.
- **clawgate #440** — `ready_for_review`, blocked on #463 for its criterion 1.
- **`homelab-infra` base clone cannot fast-forward** — `merge --ff-only` refuses on a
  dirty `flake.nix` (+ `.claude/skills/deploy/SKILL.md`, untracked files); stuck at
  `93876471` vs trunk `4964d223`. Pre-existing, NOT this session's, and it did not affect
  the deploy (that went through a clean worktree). Left alone: it is someone's uncommitted
  work, and this is the silent-drift shape — a base clone that cannot ff stops receiving
  changes while looking healthy.
- **Both hosts deployed and converged** (`ship.sh` → `ad5274b6`, cross-host agreement,
  0 dangling artifacts on either)

**DONE — merged:**
- `0493e612` (#1011) — the feature. Scanner `scripts/collector/mention_scan.py`;
  detection folded into the EXISTING `scripts/collector/claude/session-tailer.py`;
  handler `scripts/mention-open.py`; two hints in
  `nix/programs/alacritty/default.nix`; tests `scripts/tests/test_mention_scan.py`,
  `test_mention_open.py`, `test_alacritty_hints.py`
- `31cd214d` (#1060) — espanso `ask`/`clarify` collision that had `main` red repo-wide.
  NOT authored here: a competing PR beat this session's #1058, which was **closed** in
  its favour (see Gotchas)
- `e0e29e7b` (#1067) — `claudedocs/mention-detection-as-built.md`
- `686d6ff0` in **homelab-talos** — `tekton-ci` PodSecurity label; live, Flux-reconciled

**VERIFIED LIVE, not inferred:**
- deployed `alacritty.toml` resolves (`readlink -f`) to a store path carrying BOTH hints
- hint mode labels all three shapes in a real terminal: `devrc#1011`, `#370`, `868abc123`
- activating a label DISPATCHES: `#370` raised the rofi picker with both candidates
- handler resolution: `devrc#1011` → GitHub, `868abc123` → ClickUp, `#282828`/`#ff00ff`
  **rejected**
- **operator confirmed `Ctrl+Shift+M` works in a 3-day-old window** — `live_config_reload`
  picked up the symlink swap; no restart needed (I predicted otherwise; wrong)

**BOTH REMAINING INTERACTIONS NOW VERIFIED (2026-08-30), off-screen.** Driven on an
isolated `Xvfb :99` — same `alacritty` binary, same DEPLOYED config
(`/nix/store/9jmni…-alacritty.toml`, reached through a probe `XDG_CONFIG_HOME` holding
only a symlink to `~/.config/alacritty`, so `readlink -f` lands on the identical store
file), real XTEST input, zero impact on the operator's screen. Observable was an
`xdg-open` **capture handler**, not a browser: a probe `XDG_DATA_HOME` +
`mimeapps.list` binding `x-scheme-handler/http{,s}` to a `.desktop` whose `Exec`
appends the URL to a log. Both handlers under test — the URL hint's `xdg-open` and
`mention-open.py`'s (`scripts/mention-open.py:208`) — funnel through it.
- **`mouse.enabled` — hover underlines.** Pointer over `devrc#1011` → that text alone
  renders underlined; the URL on the line above and the plain word below do not.
  Screenshot, not inference.
- **`mouse.enabled` — plain left-click dispatches.** Click on the hovered
  `devrc#1011` → `https://github.com/innovation-upstream/devrc/issues/1011` captured.
- **The mouse path reaches the AMBIGUOUS branch too.** Left-click on a bare `#370`
  raised the rofi picker with both rows; arrowing to row **2** and pressing Return
  opened `…/devrc/issues/370` — so the picker resolves by row CONTENT, not by index.
- **`Ctrl+Shift+O` still works on a plain URL** — the interaction the `hints.enabled`
  array replacement could have silently killed. Hint mode labelled the URL `j` (and
  labelled the mention NOT at all, i.e. the `O` binding is scoped to its own hint);
  pressing `j` captured `https://example.com/URL-HINT-CLICK` — the whole URL, with the
  terminal's line WRAP reassembled.
- **Instrument controls, both watched:** positive — a bare `xdg-open` under the probe
  env captured a line, so the handler can fire at all; negative — hover+click on
  `plainwordnothint` left the log at its previous length, so a captured line means a
  hint fired and not ambient activity.

**Still NOT verified:** that these gestures behave the same in Zach's own
long-running alacritty windows. `live_config_reload` picking up the symlink swap was
confirmed for `Ctrl+Shift+M` in a 3-day-old window, and the config is the same file,
but no mouse gesture has been made in one of those windows.

**IN FLIGHT:** `innovation-upstream/devrc#1057` — someone else's rescue PR. This
session TRIMMED the two superseded mention drafts out of it (`9a09ad58`, plain
fast-forward). Net diff is now 5 legitimate files (`scripts/cleanup-disk.sh` + its gate
test + 3 registrations). Not mine to merge.

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

## Next steps (ranked)
1. ~~**Verify the two unverified interactions**~~ — **DONE 2026-08-30**, see "BOTH
   REMAINING INTERACTIONS NOW VERIFIED" above. Closed by machine observation on an
   isolated Xvfb rather than by an operator report, because the operator was mid-game
   when the check came due (see Gotchas). No repo change; nothing left open here.
2. **CI capacity — the durable fix. STILL OPEN, and it is now failing PRs, not just
   queueing them.** `ZacxDev/homelab-infra`, `clusters/homelab/apps/tekton-pipelines/`.
   Gate pods request far more than they use (nodes at 28–36% CPU while 6 gate pods sat
   `Pending` on `ExceededNodeResources`). Either cap concurrent `devrc-ci` PipelineRuns or
   right-size the requests. Closing condition: a `devrc-ci` run scheduling promptly with
   ≥6 others active.
   **Measured 2026-08-30 on one docs-only PR (#1099), three consecutive runs, three
   different outcomes, none about the diff:**
   - run 1 `e1183352` → **`ERROR`**, not failure: `TaskRunTimeout`, *"failed to finish
     within 1h0m0s"*. ~43 minutes elapsed between run start and the test steps starting
     (17:58Z→18:41Z), leaving them ~17 min before the TaskRun was killed. Surfaced on the
     PR as `COULD NOT RUN: pytests — the gate stopped before this leg reported`.
     🔴 **RETRACTED: an earlier revision of this bullet blamed `seed-nix` for those 43
     minutes. That was INFERENCE FROM A GAP, never a measurement of the step, and it is
     WRONG.** Measured across 114 retained TaskRuns, `seed-nix` is `min 0.0s / p50 0.0s /
     max 129.0s` — a no-op on a warm cache, exactly as its sentinel design intends. The
     gap is **pod SCHEDULING**: Tekton's TaskRun timeout starts at TaskRun creation and
     includes `Pending`, and gate pods were separately measured sitting `Pending` 11–12
     minutes with 5 running + 5 queued. Same root cause, wrong mechanism — and naming the
     step I happened to be able to see would have sent the next person to optimise a step
     that costs nothing.
   - runs 2 and 3 → real verdicts, but red on **`test_subsystem_store_api.py`**, a
     different test each time. Run 3's failure named its own mechanism (that file's tests
     are instrumented for exactly this): `MECHANISM = TRANSPORT`, writer #4's POST raised
     `TimeoutError` at 60.06s. Per-writer elapsed in ONE 8-way race: `0.36s 0.9s 2.03s
     3.34s 4.93s 6.25s` … then **42.94s** and **60.06s**. The `…was lost` arm — the
     real-defect arm — did NOT fire, so the entry lock is not implicated.
   - **ONE dimension, and the premise at the top of this rank is also wrong.** "Gate pods
     request far more than they use" does not hold: the gate requests 2250m/2752Mi (2 CPU
     for the xdist pytest step), and `talos-xr6-r7p` measured **90% CPU requested / 91%
     actual**. It is not over-requesting — it is CONFINED. "Nodes at 28–36%" was true of
     the three nodes the pods **cannot reach**.
   - 🔴 **THE ACTUAL CAUSE — a node pin inherited from a node-local PVC.** The shared
     `nix-store-cache` PVC (`tekton-ci`) is `local-path` / RWO with its PV hard-pinned by
     nodeAffinity to `talos-xr6-r7p`, so every gate pod inherits
     `nodeSelector: kubernetes.io/hostname=talos-xr6-r7p`. The scheduler says it plainly:
     `0/4 nodes are available: 1 Insufficient cpu, 3 node(s) didn't match Pod's node
     affinity/selector`. Twelve-odd idle cores on the other three nodes are structurally
     unreachable.
   - **Control, so this is not a guess:** five open PRs were red simultaneously on
     different tests concentrated in the store-API suite (the tests that stand up a real
     HTTP server), while six others passed at 19,292–19,431 collected. Unrelated diffs,
     different tests, one file family = load, not five regressions.
   - 🔴 **Do NOT "fix" this by widening `test_subsystem_store_api.py`'s timeouts.** That
     file's docstring forbids it in terms, and correctly: widening converts the load case
     into a pass and leaves the defect case looking identical.
   - Cost driver, for whoever takes it: each append holds `_EntryLock` — a blocking
     `fcntl.flock(LOCK_EX)`, no timeout — across a read-modify-write with **two `fsync`s**
     (file, then directory). Eight racers serialise through that on a box running ~19.4k
     tests under xdist.
   - 🔴 **"MOVE THE CACHE TO RWX" IS REJECTED — DO NOT RE-DERIVE IT.** Scoped 2026-08-30
     and it is not a PVC edit, it is "install distributed storage on Talos" first:
     **0 RWX PVCs of 289, 0 RWX PVs of 298, `kubectl get csidrivers` → none.** Every class
     (`local-path`, `local-storage`, six `openebs-*`) is node-local by construction, and
     `ci-priority-classes.yaml:139` already says so. **It was tried and reverted once** —
     `d149c87f` (#111, 2026-07-16) dropped the cache as unschedulable and recorded RWX as
     a follow-up *"needing RWX storage or hard node-pinning"*; pinning is the branch that
     was taken. Costs if anyone revives it: `accessModes`/`storageClassName` are immutable
     on a bound PVC, so migrating means delete-and-recreate, and with
     `reclaimPolicy: Delete` + Flux `prune=true` the cache is **destroyed irreversibly** —
     a revert returns the manifest, not the data. A cold cache is a **correctness**
     failure, not a slowdown: a recorded ablation produced **43 test failures** on a
     revision that passes with it. And 5+ concurrent pods write a shared **SQLite** fetcher
     cache, a git tarball cache, a Go build cache and the nix store's own lock files, with
     **no locking today** beyond a one-time seed sentinel — cross-node SQLite over NFS is a
     corruption hazard the current single-node layout simply does not have.
   - **The pin is not hurting at NORMAL load** — pod-start p90: `naida 14s`, `remix 15s`,
     `auditloop 24s`, `devrc 31s`, vs unpinned `clawgate-ci 13s`. It falls over in a
     BURST (5 running + 5 queued). So the lever is concurrency, not storage.
   - **Best next lever: cap concurrent `devrc-ci` PipelineRuns** — turns a silent 43-minute
     `Pending` into honest queueing, needs no new storage, reversible. A `tekton-supersede`
     CronJob already exists, so part of the mechanism may be there. The **#396 static
     split** (give a pipeline its own cache PVC on another node — done for
     `gitops-validate`, p90 370s → fixed) is the proven local precedent for cross-pipeline
     contention, but it will NOT fix devrc-vs-devrc bursts on its own.
   - Affected surface if anyone does touch the cache: **4 pipelines** (`naida-ux-audit`,
     `remix-ux-audit`, `auditloop-ci`, `devrc-ci`) **+ 4 TriggerTemplate node pins**.
     `gitops-validate` is already on a separate `nix-store-cache-2`; `clawgate-ci`,
     `clawgate-e2e`, `clawgate-ux-audit` and `vetr-infra-guards` deliberately use no nix
     cache and must not be enlisted into one.
3. ~~**Fix `clawgate` deeplink**~~ — **DONE 2026-08-30.** Closing condition was "#440's 6
   criteria, verified by a new spec in `e2e/tests/tasks.spec.ts` shown RED before / GREEN
   after"; measured with the SAME spec file on both sides — **3 failed / 1 passed at
   `3b90b6ee`, 4 passed after**. Merged `homelab-infra#564`, deployed 0.8.19, verified
   live (see the closed investigation above). ⚠ The **handoff's** closing condition is
   met; **#440's own** is not — it sits `ready_for_review` behind the newly-filed #463.
   Successor item is #463, not this rank.
4. ~~**Document the branch-protection escape hatch's asymmetry**~~ — **CLOSED AS OBSOLETE
   2026-08-30. Do not re-open it; the paragraph would be redundant AND slightly harmful.**
   This rank was written before `#1065` merged. Measured today:
   - **The exact text it asks for already exists**, at the site an operator actually
     reads — `scripts/drift-check.sh` (on `main`): *"`gh api -X PATCH
     …/protection/required_status_checks` CANNOT restore the sub-resource after a DELETE
     — it returns non-zero and changes nothing. Restoring needs a full `PUT
     …/branches/main/protection`. That is why the measured break-glass left main
     unprotected despite a restore trap that ran: the rollback path had never been
     executed once."*
   - **A deterministic detector now covers the hazard and is LIVE** — rc 24, merged as
     `#1065`. `drift-check.timer` is `active`+`enabled`; last run 2026-08-30 12:24 CDT.
     Its verdict is the **context count**, never the `protected` flag. Prose hoped
     someone would read it; rc 24 fires 4×/day.
   - Live protection reads healthy: `enforce_admins: true`, `strict: false`, both
     contexts present with `app_id` 4320115 pinning.
   🔴 **And the edit as SPECIFIED is the wrong shape.** `CLAUDE.md` still hands over the
   `DELETE` with no mention of the asymmetry — but "also document the `PUT`" makes a
   one-way, hard-to-reverse operation MORE usable, exactly when someone reaches for it
   under pressure (measured 2026-08-30: the devrc gate failed three consecutive runs of
   an innocent docs PR). It would also have prevented **neither** measured incident: the
   first one *had* a restore trap, it *ran*, and main was left unprotected anyway. If
   anything belongs in `CLAUDE.md` it is one clause saying the DELETE is one-way and that
   rc 24 watches for it — a pointer to the detector, not a copy of the recipe. That is a
   materially different edit and needs its own decision, so it was NOT made under this
   item's authority.

## Gotchas / decisions / dead-ends
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

## How to verify
```bash
# the shipped scanner + handler, no browser opened
/nix/store/*-alacritty-mention-open --print '#370'          # -> both candidates
/nix/store/*-alacritty-mention-open --print 'devrc#1011'    # -> the GitHub issue
/nix/store/*-alacritty-mention-open --print '#282828'       # -> "no mention in the clicked text"

# both required tiers, the tier Tekton actually gates on
cd ~/workspace/devrc && nix build .#checks.x86_64-linux.pytests .#checks.x86_64-linux.nodetests --no-link
```
In a terminal: `Ctrl+Shift+M` labels every mention; `Ctrl+Shift+O` is the URL hint.
🔴 Read the `TOTAL collected=` / `RESULT:` lines out of `nix log <drv>` — an exit code
through a pipe is `tail`'s, not the gate's.
