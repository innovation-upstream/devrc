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
🔴 **RANKS 1, 3 AND 4 ARE CLOSED. RANK 2 IS OWNED BY ANOTHER EFFORT — hand over, do not
work it here.** The genuinely open items are ranks 5–7 below.

- Branch: `main`. `origin/main` was `e0e29e7b` when this doc was created; it is `e0f35ce2`
  as of 2026-08-30 and moved **six times during this session's own gate runs**, which is
  why every merge here re-checked the merged tree rather than trusting a branch-green.
- **clawgate 0.8.19 is LIVE** (`clawgatectl health`), pin commit `8503620a` on
  `homelab-infra` trunk, carrying the `#task-N` deeplink fix. Verified against the
  deployed pod, not a fixture.

**Merged this session:**
- `innovation-upstream/devrc#1086` — the two unverified alacritty interactions, verified
  off-screen (rank 1)
- `ZacxDev/homelab-infra#564` → squash `9ff37992` — `taskHashScript()`, the deeplink fix
- `8503620a` in **homelab-infra** — the 0.8.19 pin bump (the deploy itself)
- `innovation-upstream/devrc#1131` → squash `e0f35ce2` — ranks 1/3/4 closed, rank 2's
  diagnosis and hand-over

**Still open, with owners:**
- 🔴 **`innovation-upstream/devrc#1099` — OPEN and BLOCKED.** Corrects a false 🔴 claim
  ("the browser layer is UNGATED by CI") in three places. Correct and locally verified
  (526 tests). `tekton/devrc-nodetests=success`, `tekton/devrc-pytests=failure` — the
  flaky `test_subsystem_store_api.py` family, **not** its content. Its worktree
  `/home/zach/workspace/devrc-clawgate-ci` is deliberately still on disk holding the
  branch. ⚠ It also edits `claudedocs/handoff-tmux-webapp.md`, which `main` has since
  touched — **check for a semantic conflict before merging**, a clean git merge is not a
  clean merge.
- **clawgate #463** (open) — the board-scroll layout bug; blocks #440's criterion 1.
- **clawgate #440** — `ready_for_review`, not `complete`, for exactly that reason.
- **`homelab-infra` base clone cannot fast-forward** — `merge --ff-only` refuses on a
  dirty `flake.nix` (+ `.claude/skills/deploy/SKILL.md`, untracked files); stuck at
  `93876471` vs trunk. **Pre-existing and NOT this session's**; it did not affect the
  deploy, which went through a clean worktree. Left alone deliberately: it is someone's
  uncommitted work. It is the silent-drift shape — a base clone that cannot ff stops
  receiving changes while looking healthy.

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
5. **Merge `devrc#1099`** — `innovation-upstream/devrc`, touches
   `claude/skills/clawgate/SKILL.md`, `claude/skills/clawgate/reference/extension.md`,
   `claudedocs/handoff-tmux-webapp.md`. Blocked only by the flaky gate. Re-check the
   merged tree first: `main` has touched `handoff-tmux-webapp.md` since the branch point.
   Closing condition: merged, verified by content (a squash is never an ancestor).
   forcing: gate — a required check is red on a test the diff cannot reach, and the PR
   corrects a 🔴 claim that is actively misleading agents in three files.
6. **Fix clawgate #463, then close #440** — `ZacxDev/homelab-infra`,
   `containers/clawgate`. 60 of 248 cards are unreachable by any scroll; #440's criterion 1
   is unsatisfiable for 24% of the board until it lands. #463 carries 6 criteria and a
   measurement script shape as its verifier.
   forcing: none — a real user-facing defect, but nothing external is forcing it and
   nobody has asked. Do not let its severity read as urgency.
7. **Decide the `homelab-infra` base clone** — it cannot fast-forward. Someone must say
   whether the dirty `flake.nix` + `.claude/skills/deploy/SKILL.md` are WIP worth a branch
   or stale cruft to discard. 🔴 Hash the working copy against that file's recent commits
   first: byte-identical to an OLDER commit proves a stale orphan, and "restoring" it
   silently reverts everything since.
   forcing: none — not blocking today, which is precisely the failure mode: it stops
   receiving changes while looking healthy.

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

## How to verify
```bash
# the deeplink, live against the deployed pod (LAN UI is open, no auth)
curl -sf http://192.168.50.250:30302/ui/tasks | grep -c 'id="task-[0-9]*"'
clawgatectl health          # must report 0.8.19

# the alacritty hints, off-screen and without taking the operator's screen:
#   Xvfb :99 -> real alacritty -> probe XDG_CONFIG_HOME symlinking ~/.config/alacritty
#   -> probe XDG_DATA_HOME whose .desktop appends %u to a log. Recipe in Gotchas.

# devrc doc subset (the flake devShell carries the gate toolchain; direnv does NOT)
nix develop ~/workspace/devrc -c python3 -m pytest \
  ~/workspace/devrc/scripts/tests/test_handoff_doc.py \
  ~/workspace/devrc/scripts/tests/test_closing_condition_single_source.py -q
```
🔴 Run **both** tiers before claiming a merge is safe, and name the tier and base sha:
`scripts/gate.sh` is the dev host; `nix build .#checks.x86_64-linux.{pytests,nodetests}`
is what Tekton gates on. **Build them ONE AT A TIME** — a combined invocation contends on
the nix store and produces false failures.
