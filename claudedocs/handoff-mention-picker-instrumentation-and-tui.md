# Handoff: mention-picker-instrumentation-and-tui — 2026-09-12

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
The mention picker (`scripts/mention-open.py`) shipped its plausibility ranking in #1509,
but nothing measures whether the ranking helps and the operator's usage is not being
recorded. Four operator asks: (1) record picks that are currently dropped, (2) center the
picker window, (3) ship click telemetry to `activity.events`, (4) open PRs in a TUI
instead of a browser.

## State now
🔴 **RANK 1 IS EXERCISED — `picks.jsonl` EXISTS AND GREW FROM A REAL HUMAN CLICK.** Measured
2026-09-12 on the **laptop**: 37 → 38 rows, newest
`{"n": 581, "repo": "civitai/cli", "t": 1789249676.336, "via": "picker"}` at 14:27:56, ~48 s
before the confirming probe. The arc's premise is closed: the file that was absent all night is
recording the operator's picks.

**The host was the missing variable, and it resolves the old diagnosis.** The workbench's
`picks.jsonl` is STILL absent and that is **not** a broken fix — the operator clicks on the
laptop. Proven by driving the real handler's auto-open arm against the workbench's live source,
fully isolated (`MENTION_OPEN_PICKS` + `ACTIVITY_SPOOL_DIR` redirected; `xdg-open`, `alacritty`
and `nvim-octo` stubbed on `PATH` so nothing opened and no row touched the operator's data):
it wrote `{"n": 1291, "repo": "innovation-upstream/devrc", "via": "auto"}` and emitted the click
row `outcome=auto-open repo=innovation-upstream/devrc platform=github picker_shown=false
surface=tui`. Real `picks.jsonl` verified ABSENT before and after the probe.

⚠ **`via: "auto"` has still never been written by a HUMAN.** All 3 post-#1569 rows on the laptop
are `via: "picker"`; the 35 older rows carry no `via` at all. The auto arm is measured working as
CODE, not as a click path through the nix wrapper.

**NEW DEFECT, found by the operator the moment the TUI was first used:** *"the new tui opens far
too big and overflows the screen, its unusable"*. Root cause — `REVIEW_COLUMNS = 200` /
`REVIEW_LINES = 50` (`scripts/mention-open.py`, passed as `-o window.dimensions.*`) size the
window in **character cells**, while the constraint is a workspace in **pixels**. Measured:
workbench workspace `3440x1413` at ~96 DPI absorbs it; laptop `2256x1480`, panel
`eDP-1 2256x1504` at 285x190 mm (~201 DPI) cannot. **Both hosts resolve
`~/.config/alacritty/alacritty.toml` to the SAME nix store path and it has no `[font]` section**,
so the font size is Alacritty's default on both and DPI is the only difference. And
`nix/i3/config.nix` had **no rule matching `instance="mention-review"` at all** — only the shared
`for_window [class="float"] floating enable` at line 85, which floats it and nothing more.

- **IN FLIGHT: devrc#1619** `fix/mention-review-window-ppt`, head `d1f80d4a`, `MERGEABLE` /
  `UNSTABLE`. Adds
  `for_window [class="float" instance="mention-review"] floating enable, resize set 90 ppt 90 ppt, move position center`
  and demotes the cell constants to 140x40 as a pre-resize hint. Operator chose this direction
  from three offered (ppt rule / shrink constants / compute cells at spawn).
- **Audit round 0 was IN FLIGHT at session end** — dispatched read-only against #1619, result not
  yet read. Rounds 1+ have NOT run.
- **NOT DEPLOYED, and merging will not deploy it.** `nix/i3/config.nix` is a `home.file` target:
  the sequence is merge → `scripts/ship.sh` → `i3-msg reload` on the affected host. The overflow
  is on the LAPTOP, so that is where the reload matters.
- Tekton on #1619: all three checks still `pending` at session end (`devrc-pytests`,
  `devrc-nodetests`, `devrc-cairn-client-runs`).
- **This session resolved no clawgate task** — `clawgate_handoff.sh resolve` exited 5 (0 tasks).
  An unknown session id answers 200 with an empty array, so that is NOT evidence this session
  touched no task. No `clawgate-task:` field written either way.
- ⚠ The shared checkout moved mid-session: local `main` went from `7e000e6b` to `b55720e8` and
  ended `behind 1` against `origin/main`, by other sessions. Re-read `git status` before any write.

## Open investigations — live diagnosis state

### Tier B pick-learning recorded almost nothing despite heavy operator use
- **Symptom + exact repro:** operator reports "many picks on workbench, as recently as a
  few minutes ago". Measured: `~/.config/mention-open/picks.jsonl` ABSENT on the
  workbench (0 rows loaded via `load_picks()`); the laptop has 7 rows.
- **Observed (with values):** `ls -la ~/.config/mention-open/` shows only
  `known_ranges.json` (12912 B), `known_repos.json` (18153 B), `known_universe.json`
  (11629 B), newest mtime `2026-09-11 20:32:34`. No picks file at any mtime.
  `XDG_CONFIG_HOME` is unset, so `PICKS_PATH` resolves to
  `/home/zach/.config/mention-open/picks.jsonl` — the directory exists and
  `os.access(..., W_OK)` is True.
- **Ruled out:** the deployed artifact is stale / predates #1509 — the hint command is
  `/nix/store/aj7rn7ci3vizlr0byq28l8ki2lc8rlmv-alacritty-mention-open`, which `exec`s
  `/home/zach/workspace/devrc/scripts/mention-open.py` (the WORKING TREE), and that file
  contains 13 occurrences of `record_pick`. via: command
- **Ruled out:** the write path is broken — `record_pick("innovation-upstream/devrc",
  "1291", path=<tmp>)` wrote `{"n": 1291, "repo": "innovation-upstream/devrc", "t": ...}`
  correctly. via: measurement
- **Ruled out:** an unwritable home silently swallowing an OSError — parent dir exists and
  is writable, measured above. via: measurement
- **Ruled out:** the URL gate rejecting everything — `repo_of_github_url` returns the repo
  for both `/pull/N` and `/issues/N` github.com URLs (and correctly `''` for clawgate and
  ClickUp URLs, which are not repos). via: measurement
- **ROOT CAUSE (found, not hypothesis):** `scripts/mention-open.py:2439` is
  `return open_url(candidates[0]["url"])` — the single-candidate AUTO-OPEN path, which
  returns WITHOUT calling `record_pick`. The only `record_pick` call is at `:2507`, on the
  picker-selection path. So every unambiguous click — the common, fast path — teaches
  Tier B nothing. Confirmed by `grep -n "return open_url"`: exactly three sites, and only
  the `:2508` one is preceded by a record. via: code
- **Next probe:** none needed for diagnosis; the fix is dispatched (agent 1). The
  open question is now a MEASUREMENT one: whether the #1509 ranking actually helps, which
  cannot be answered until rank+class are recorded at pick time.

(none — every diagnosis this arc opened was closed. The one remaining UNKNOWN is not a
diagnosis but an unexercised path: see rank 1.)

### ✅ RETIRED — "Tier B pick-learning recorded almost nothing despite heavy operator use"
🔴 **CLOSED 2026-09-12. Do not re-open it, and do not re-run its probes.** The block above it in
this doc states the root cause correctly (`record_pick` missing on the single-candidate auto-open
path) and #1569 fixed it. What that block could NOT know, and what closes it:
- **Its framing was host-blind.** It measured `picks.jsonl` ABSENT on the workbench and read that
  as the defect's signature. The file is absent on the workbench because **the operator clicks on
  the laptop**, where the file has existed and grown throughout. The absence was real and the
  inference from it was wrong — an empty result could not distinguish "broken" from "wrong host",
  and the block named only one mechanism.
- **Recording is now confirmed from a real click** (37 → 38 rows, `via: "picker"`), and the auto
  arm is confirmed by isolated probe. Both values are in `## State now`.
- The four eliminations in the original block were all re-checked as still TRUE; it is the
  QUESTION they served that had moved, not the answers.

### The review TUI's 90% is unverified against any real screen
- **Symptom + exact repro:** operator, verbatim: *"the new tui opens far too big and overflows the
  screen, its unusable"*. Repro: on the laptop, click any `repo#N` GitHub mention in alacritty.
- **Observed (with values):** `REVIEW_COLUMNS = 200`, `REVIEW_LINES = 50` in
  `scripts/mention-open.py`, emitted as `-o window.dimensions.columns=200 -o
  window.dimensions.lines=50`. Laptop workspace rect `{x:0, y:24, width:2256, height:1480}`;
  panel `eDP-1 2256x1504+0+0`, `285mm x 190mm` → ~201 DPI. Workbench workspace
  `{x:0, y:27, width:3440, height:1413}`, no `Xft.dpi` set (default 96). Both hosts'
  `alacritty.toml` → `/nix/store/ibp7dwvhrq1r33cah3m4nf4ihg1l6rs6-alacritty.toml`, with **no
  `[font]` section**. Pre-#1619 `nix/i3/config.nix` matched `mention-review` with nothing but
  `for_window [class="float"] floating enable` (line 85).
- **Ruled out:** a font-size difference between the hosts — both resolve `alacritty.toml` to the
  same store path and neither sets a font size. via: measurement
- **Ruled out:** an existing i3 rule sizing or centering the review window — `grep` of
  `nix/i3/config.nix` found rules for `class="float"` (line 85) and
  `instance="mention-open"` (line 111) only, none for `mention-review`. via: command
- **Ruled out:** the workbench being the affected host — its workspace is 3440 wide and the
  operator's click that produced the complaint landed on the laptop (picks row timestamp matches).
  via: measurement
- **Leading hypothesis:** at ~201 DPI alacritty's cell is roughly 2x the workbench's, so 200
  columns demands roughly 2x the laptop's 2256 px width. **The exact cell size was NOT measured** —
  doing so needs a window spawned on the operator's screen, which was declined as screen theft.
  The cells-vs-pixels mismatch is established independently of the factor, which is why the fix
  does not depend on it.
- **Next probe:** after `ship.sh` + `i3-msg reload` on the laptop, click a mention and run
  `i3-msg -t get_tree`, reading the `mention-review` window's rect against the workspace's
  `2256x1480`. Expect ~`2030x1332`. 🔴 `i3-msg` needs `DISPLAY`, `XAUTHORITY` and `I3SOCK` out of
  `/proc/$(pgrep -x i3)/environ`, and `-t get_config` returns RAW TEXT on this i3, not JSON.

## Next steps (ranked)
1. 🔴 **Read audit round 0 on #1619, then merge and DEPLOY it — the operator is currently unable
   to use the review TUI.** The ladder is unfinished: round 0 was dispatched and its result never
   read, and rounds 1+ never ran. After merge the deploy is `scripts/ship.sh` then `i3-msg reload`
   — merging alone changes nothing, because `nix/i3/config.nix` is a `home.file` target.
   **IN FLIGHT: devrc#1619.** Repo: devrc.
   forcing: user — the operator reported the TUI unusable this session, verbatim.
2. 🔴 **Confirm the 90% actually fits, on the LAPTOP, by looking at it.** Nobody has seen the
   window on either display; `90` is a judgement, and #1619's guards deliberately pin the UNITS
   and a 1..100 range so tuning the number does not go red. After the reload, click a `repo#N`
   mention and read the window rect out of `i3-msg -t get_tree` against the workspace rect
   (`2256x1480`). Repo: devrc.
   forcing: user — same report; the fix is unverified against the symptom that motivated it.
3. **Drive `via: "auto"` once from a real click.** Every recorded row is `via: "picker"`. The auto
   arm is measured working as code (see State now) but never through the alacritty hint wrapper,
   which carries the display manager's environment rather than a shell's. Click an unambiguous
   `owner/repo#N` and confirm a row lands with `"via": "auto"`. Repo: devrc.
   forcing: none
4. **Add the `adoption-scan` registry row for the click telemetry.** Flagged by #1569's author as
   not done. Until it exists the dims (`surface`, `rank`, `plausibility`, `offered_total`, `via`)
   are invisible to adoption sweeps, so "is this being used?" cannot be answered by the tool built
   to answer it. Repo: devrc.
   forcing: none
5. **Confirm a real click row reaches `activity.events`.** The rail is live and the SHAPE is now
   verified — an isolated probe produced a well-formed spool line with `surface=tui` — but no real
   row has been observed landing in ClickHouse. Deliberately not faked; a synthetic row would
   pollute the dataset this arc had to clean fixture residue out of. Repo: devrc.
   forcing: none
6. **Decide whether Tier A ranking actually helps.** Answerable after a few weeks of real clicks
   now that rank/class/total-offered are recorded. Query: chosen `rank` should cluster near 0 and
   chosen `plausibility` skew PLAUSIBLE. Repo: devrc.
   forcing: none
7. **Close or merge #1539** (`docs/handoff-arc-final-close`, still OPEN, ci=pending). It is the
   SUPERSEDED mention-arc handoff — the one naming the obsolete two-PR dependency for main's
   kill-scanner red, which `c0bbd6d9` actually fixed by a third route. Repo: devrc.
   forcing: none
8. **#1582 merged WITHOUT a sandbox-tier CI verdict** (operator instruction: "skip ci, merge and
   ship"). Dev-host evidence was strong but the `nix build` tier never reported on `afb0d3d2`.
   `main-green-check` (4-hourly, reproduces before alerting) is the backstop. Repo: devrc.
   forcing: none
9. **Prune this arc's agent worktrees** under `.claude/worktrees/agent-*` — the original five
   (`a489445`, `a6b7160`, `a254aa5`, `a7271e3`, `a68b437`) plus `ac2db25ce490f5826` (the #1619
   implementation) and `abf7897c06f346ee6` (the round-0 audit). Cosmetic. ⚠ The repo holds ~150
   agent worktrees belonging to other sessions; prune only these. Repo: devrc.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **A `record_pick` that exists is not a `record_pick` that runs.** The function, its
  tests, its constants and its docstrings were all correct and complete; the defect was a
  single missing call on the path that carries most of the traffic. "The feature is
  implemented" and "the feature observes anything" were independent claims, and only the
  live per-host file told them apart. A DTO field is not a guard; a defined function is
  not a code path.
- 🔴 **Do NOT add `move position center` to `nix/i3/config.nix:85`.** That rule is
  `for_window [class="float"] floating enable` and `class="float"` is shared by every
  other float in the system — centering there moves windows nobody asked to move. The
  picker spawns as `alacritty --class float,mention-open` (`PICKER_CLASS` at
  `mention-open.py:1282`), so the targeted discriminator is `instance="mention-open"`.
  The working precedent for centering is `config.nix:27` (the Yad "Rig Controls" rule).
- 🔴 **The telemetry privacy line, and it is already drawn in code.**
  `scripts/collector/mention_scan.py:180` makes the dedupe key
  `platform:raw[@owner/repo]` — so an owner/repo the operator MENTIONED already reaches
  the `activity.events` spool today. What must never reach it is the picker's OFFERED SET
  (the ~392-row universe in `known_universe.json`/`known_repos.json`), which holds private
  repo names and is the file behind the #1283 disclosure. **Emit what was touched, never
  what was offered.** Operator decision 2026-09-12: plain `owner/repo`, not hashed and not
  public-only.
- **Operator decision 2026-09-12, settled:** record auto-opens as pick signal but TAG them
  by path, so scoring can weight or exclude them later without losing the data.
- **Operator decision 2026-09-12, settled:** the PR TUI scope is FULL review AND merge.
  The blast radius of one-keystroke merge in a repo with no blocking gate was raised
  before the decision and accepted; the mitigation is a confirmation step on destructive
  verbs, not a narrower scope.
- 🔴 **Tier A (#1509 plausibility ranging) IS working — measured live, so nobody re-opens
  it.** Against the real 393-row `known_ranges.json` on the workbench: `#12` →
  68 PLAUSIBLE / 93 BELOW / 232 IMPOSSIBLE; `#1291` → 5 / 156 / 232; `#999999` →
  0 / 161 / 232. The candidate set genuinely collapses as N grows and degrades gracefully
  at the absurd end. ⚠ The `UNKNOWN=0` in that probe is an ARTIFACT of iterating the
  ranges table's own keys — every key has an entry by construction. The picker iterates
  the UNIVERSE, so that figure says nothing about the picker's row set.
- ⚠ **Ranking quality is still UNMEASURED.** Nothing records where in the offered list the
  chosen row sat, so "does the #1509 ordering help?" has no data behind it. That is why
  rank + class + total-offered were added to agent 1's brief after dispatch.
- **The laptop lacked `known_ranges.json`** because its
  `mention-known-repos-refresh.timer` last ran 2026-09-11 07:01, ~13h BEFORE #1509 merged
  at 20:28; next fire 07:01. Self-heals — not a defect, and not host drift.
- 🔴 **`main`'s kill-scanner red was fixed by a THIRD route, not the two PRs a previous
  handoff pointed at.** That doc framed it as needing `#1534` (rewords
  `handoff-tmux-webapp.md`) and `#1539` (drops the literal from its own text), each
  fixing one offender, neither greening `main` alone. What actually landed was
  `c0bbd6d9 fix(guard-core): stop the kill scanners reading claudedocs/ — SIX docs red-ed
  main in two hours and every fix was itself a doc`. Measured green at `db7bf3ff`
  (`2 passed`). **The lesson: every per-doc fix was itself a doc, so the fix rate could
  never catch the break rate — the scanner's SCOPE was the defect, not any document.**
  Do not re-derive the two-PR dependency; it is obsolete.
- **This session resolved no clawgate task** — `clawgate_handoff.sh resolve` exited 5
  (0 tasks). An unknown session id answers 200 with an empty array, so that is NOT
  evidence this session touched no task; no `clawgate-task:` field was written either way.
- ⚠ **This is a SHARED checkout and it moved under this session** — `origin/main` advanced
  from `622fc2d8` to `db7bf3ff` mid-session, and the working tree's one dirty file
  disappeared, by another session's action. Re-read `git status` before any write.

- 🔴 **THE PIPE TRAP FIRED AGAIN AND WAS CAUGHT ONLY BY LOOKING — measured, with the exact
  bytes.** An agent's `gate.sh … | tail -40` produced, in full:
  `gate: === pytest === (full log: …)` / `GATE_EXIT=0` / `[exited with code 0]`. That
  `GATE_EXIT=0` is **`tail`'s** status over a gate that had just been **killed mid-pytest**,
  and the background harness independently summarised it as *"completed (exit code 0)"*.
  **No `RESULT:` line was ever printed.** Two independent numbers both said green for a run
  that never finished. `gate.sh` prints a bounded summary itself and its exit status is
  authoritative — there is no reason to pipe it, ever.
- 🔴 **DISJOINT FILES ARE NOT SAFETY, and this arc produced the textbook case.** #1562's
  merge with `main` had **zero** file overlap (14 upstream files, 6 of the branch's) — and
  upstream had changed `scripts/tests/test_no_real_launchers.py`, a **repo-wide scanner over
  test files**, while the branch added three test files it had never seen. Zero overlap,
  direct interaction. Running that scanner against the MERGED tree (129 passed) is what
  turned it from an assumption into evidence.
- 🔴 **`main`'s kill-scanner red was fixed by a THIRD route, not the two PRs the previous
  arc's handoff named.** It framed it as needing `#1534` and `#1539`, one offender each,
  neither greening `main` alone. What landed was `c0bbd6d9 fix(guard-core): stop the kill
  scanners reading claudedocs/`. **Every per-doc fix was itself a doc, so the fix rate could
  never catch the break rate — the scanner's SCOPE was the defect, not any document.**
  Measured green afterwards. Do not re-derive the two-PR dependency; it is obsolete.

- 🔴 **PR-REVIEW TUI RESEARCH — the field was measured, so nobody re-surveys it.**
  Recommendation: **octo.nvim**, the only candidate clearing FULL review+merge AND
  deep-linking to one PR.
  - **gh-dash cannot deep-link** — `cmd/root.go` declares `cobra.MaximumNArgs(1)` but the
    `Run` body **never reads `args`**; it goes straight to cwd-derived repos. The config
    `filters` workaround was measured at 7 points: a bare number ranks the target first but
    never ISOLATES it (`#74` → 51 results), and gh-dash re-sorts anyway. **A click would land
    on a list, not the PR.** It also has no request-changes action
    (`prview/action.go` enumerates approve/close/reopen/merge only). Credit where due: its
    merge CONFIRMATION (literal `Y`+Enter, `prssection.go:82-110`) is the best in the field.
  - **No merge at all:** tuicr, prr, gh-review.nvim (its README says so explicitly).
    **ghui:** no deep-link, no reopen.
  - 🔴 **The reason octo is right is not features — it is that `Octo <N> <owner/repo>`
    resolves the PR-vs-ISSUE ambiguity server-side.** `mention-open.py` builds `/pull/{id}`
    for EVERY GitHub mention and GitHub redirects `/pull/<issue>` → `/issues/<issue>`, so
    the URL cannot say which it is. `utils.open_buffer` (`utils.lua:315`) fires one GraphQL
    `issueOrPullRequest` and dispatches on `__typename`. **Passing a URL would open a wrong
    buffer for every issue mention; passing the NUMBER cannot.**
  - Reviewing needs no checkout (`reviews/init.lua:214` gates that on `use_local_fs`,
    default false). nixpkgs `octo-nvim` is a bare `buildVimPlugin` with **no declared
    runtime deps** — plenary, a picker and the colorscheme must be on the packpath
    explicitly. `pkgs.neovim.override { configure = …; }` was VERIFIED to instantiate; the
    `writeShellApplication` wrapper was NOT.
- 🔴 **octo has NO merge confirmation** — `commands.lua:2365` calls `gh.pr.merge(opts)` with
  no prompt, bound by default to `<localleader>pm`, `psm`, `prm`, `pk` and `<C-r>` in the
  picker. **Operator decision 2026-09-12:** `mappings_disable_default = true`, re-declare
  only non-destructive keys, so merge requires typing `:Octo pr merge`.
- 🔴 **Two octo defaults are WRONG FOR THIS REPO and would bite on first use.** Upstream
  `default_merge_method = "merge"` produces a merge commit, but all 5 of this repo's most
  recent merges are squashes — so the first TUI merge would silently produce the wrong
  commit shape. And `default_delete_branch` must stay `false`: this repo has
  `delete_branch_on_merge=true`, so deleting a STACKED PARENT's branch auto-closes the child
  PR and **GitHub refuses to reopen it**.
- 🔴 **A POST-SPAWN FALLBACK FOR THE TUI IS STRUCTURALLY IMPOSSIBLE — the pre-flight is the
  only thing that can work.** alacritty 0.17.0 exits **0** whether its `-e` command runs or
  exits 127, and `Popen` never waits, so a missing `nvim-octo` gives a window that flashes
  and vanishes, invisible to the caller. `shutil.which` BEFORE the spawn is mandatory, not
  defensive polish — which is exactly why `pick()` already pre-flights `fzf`.
- 🔴 **The TUI/browser selector must be a MARKER FILE, never an env var.** The handler is
  spawned by the alacritty hint with the **display manager's** environment, so a shell-set
  variable never reaches it. `~/.server-mode` is the existing precedent in this repo.
- **Three ledgers go red on the octo integration, and one is subtle:**
  `nix/programs/alacritty/default.nix`'s `makeBinPath` is parsed by
  `test_mention_open.py:1235` for `pkgs.X` names — ⚠ **a local `let`-bound package is not
  spelled `pkgs.X` and will not parse**, so `nvim-octo` must come through an overlay to
  genuinely BE `pkgs.nvim-octo`. Also `PROVIDER`/`SPAWNABLE_EXECUTABLES`/`EXPECTED_ARGV0` in
  that file, and `nolaunch.py`'s two-way pin (it belongs in `ACKNOWLEDGED_UNSTUBBED` with a
  written reason — it is only ever alacritty's `-e` payload, and alacritty is already
  stubbed).
- ⚠ **The research measured package versions against the REGISTRY nixpkgs, not this flake's
  pin** (`flake.lock` is ~4 days older, same 26.11pre branch). Its `builtins.getFlake` of
  the pinned input was interrupted and returned nothing. Re-verify availability against the
  pin before relying on a version.
- ⚠ **UNVERIFIED, do not build on it:** whether alacritty 0.17 disambiguates two hints with
  identical regexes by `mouse.mods` (the proposed Shift-click-for-browser escape hatch).
- 🔴 **The frozen mirror vs the pod, hit live.** `subsystem_touch.py` reads
  `~/.claude/analyze-service-index` (the FROZEN mirror) and reported `NO ENTRY — scripts`,
  while `cairn recall` (synced cache) showed a live `scripts` entry with 4 bullets.
  **Following the probe's nomination would have created a duplicate entry.** Trust
  `cairn recall` over the probe's entry-existence claim.

- 🔴 **EVERY CI RED IN THIS ARC WAS UNREACHABLE FROM THE DIFF THAT CARRIED IT, EXCEPT ONE.**
  Two stale bases (`test_every_site_writing_its_OWN_runner_bound_is_in_the_ledger`, then
  `test_no_new_dead_paths` — both already fixed on `main`), two known flakes (the sha-prefix
  one, and a pty-driven real-fzf test), and exactly ONE genuine defect (#1582's runtime
  shebang). That is the measured ~42% non-success rate in practice. **Read the failing
  test's NAME and ask whether your diff can reach it before debugging anything.**
- 🔴 **`test_no_new_dead_paths` took TWO fixes on `main` (`7aa06ad1`, then `5f492f99`), and
  two agents "disagreed" because they measured either side of the first one.** Neither was
  wrong. An agent measured one reference green and reported "already fixed upstream",
  asserting the CLASS was closed having measured one INSTANCE. Re-measure at the tip.
- 🔴 **`rerere` SILENTLY RECORDS INTO THE SHARED `rr-cache`, AND A REPLAY IS INVISIBLE BY
  DESIGN.** Committing a prepared integration merge stored postimages repo-globally; the
  real merge afterwards would have been auto-resolved with the conflict never shown,
  defeating a rebuild-and-re-measure instruction. Removed by CONTENT, never by timestamp
  (a timestamp correlation nearly deleted another session's entry). **The general rule is
  "anything under the common git dir leaks" — `rr-cache` is that mechanism, not a new one.**
  The counter-discipline: on the real merge, EXPECT to see the conflict; a silent
  auto-resolve is a signal to check the cache, not good news. (Verified: `Recorded
  preimage`, never "Resolved using previous resolution"; no new postimage after committing
  with `rerere.enabled=false`.)
- 🔴 **A MUTATION ROW WHOSE ANCHOR MATCHES MORE THAN ONCE IS REFUSED — AND SCORES A SILENT
  VACUOUS PASS.** Three of `open_tui`'s four arms were the byte-identical line
  `return open_browser(url), CLICK_SURFACE_BROWSER`, so a row anchored on that string
  matched 3x and was refused; an arm could carry NO mutant with nothing saying so. Anchor
  on unique preceding context and verify each row APPLIES (1x) before trusting a score.
- 🔴 **AN ASSERTION THAT SUBSCRIPTS WHAT A DELETION-MUTANT DELETES CAN NEVER BE ATTRIBUTED.**
  The mutant raises `KeyError`, pytest renders no message, and the row scores
  `KILLED-WRONG-REASON`. Use `.get` plus a named prefix token.
- 🔴 **THE SCRATCHPAD IS SHARED AND IT COLLIDED TWICE, IN TWO DIFFERENT WAYS.** (a) One
  agent's `mutate.py` at the scratchpad ROOT was overwritten by another's battery and it
  ran the wrong one, catching it only by reading the test names — it would otherwise have
  reported someone else's 7/7 as its own 10/10. (b) A second agent then relocated its
  SCRIPT, launched the relocated run while its ORIGINAL was still mutating the SAME
  WORKTREE, and its negative control read a tree with a live mutant in it. **Two shared
  resources, and fixing one implied nothing about the other.** Fixes: per-agent directories,
  a SUBJECT/SCRIPT/TREE banner printed before any score, and **sequencing on the tree** (wait
  on the prior run's output file — never `pkill -f`, which matches the waiting script itself).
- 🔴 **A SCORE IS PORTABLE BETWEEN BATTERIES; A SUBJECT IS NOT.** `7/7` says nothing about
  whose seven. Reading a score without its provenance is asserting provenance where it
  should be measured — the same defect class as the hardcoded surface literal, one level up.
- 🔴 **AN AGENT FABRICATED TWO TEST COUNTS**, writing measured-sounding figures before its
  monitor fired. One was wrong (`656` vs `653`); the other was right BY LUCK, which is
  worse, because it would have gone unnoticed. Disclosed unprompted. **Never state a count
  that is not visible in a tool result; say "not yet reported".**
- 🔴 **`gh pr diff --name-only` is three-dot; `git diff A..HEAD` is TWO-dot and spans
  main's commits too.** An agent used the two-dot form against a stale base to ask "does my
  branch touch this file?", got the opposite answer, and caught it only on re-read.
- 🔴 **A GREP THAT REQUIRES A VERSION SUFFIX MISSES AN UNVERSIONED STORE PATH.** I reported
  `nvim-octo` as absent from the deployed wrapper using `(nvim-octo|fzf|alacritty)-[0-9.]*`;
  it was present all along under `/nix/store/…-nvim-octo` with no version. Grep's answer is
  a claim about grep's VIEW.
- 🔴 **`i3-msg -t get_config` RETURNS RAW TEXT, NOT JSON** on this i3 — parsing it as JSON
  throws. And the Bash tool has **no X environment**: `i3-msg` needs `DISPLAY=:0`,
  `XAUTHORITY`, and `I3SOCK` (read them out of `/proc/$(pgrep -x i3)/environ`).
- 🔴 **i3's `move position center` CENTRES WITHIN THE WORKSPACE AREA, NOT THE OUTPUT** — the
  bar's 27px is excluded. Compute the expectation from the workspace rect or a correct rule
  reads as a 13px failure.
- 🔴 **PYTEST WRAPS `FAILED` LINES IN ANSI**, so `grep '^FAILED'` returns NOTHING on a log
  that plainly failed. Strip escapes before reading a test name out of a gate log.
- **A pipe destroys the gate's verdict, measured AGAIN.** `gate.sh … | tail` printed
  `GATE_EXIT=0` — `tail`'s status — over a run that had been KILLED mid-pytest, with no
  `RESULT:` line ever emitted. Two independent numbers both said green for a run that never
  finished.
- 🔴 **ROUND 0 FOUND WHAT NO CORRECTNESS ROUND ASKS, TWICE.** On #1582 it found `--browser`
  was unreachable in production (the alacritty hint regex cannot emit a leading `-`, and the
  PR added no second hint entry) AND was the sole cause of the cross-PR conflict — deleting
  it made the diff purely additive. On #1594 it surfaced a 3-line alternative (pin
  `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` in `_git_env`) to a 290-line fix, which I measured
  green independently at **933 passed**.
- 🔴 **THE DATE PIN WAS MEASURED AND REJECTED, ON A STATED GROUND — do not re-propose it.**
  A pinned date makes the fixture sha deterministic forever, so an unrelated fixture-content
  edit drawing an ambiguous prefix fails **100%**, not 0.17% — a latent permanently-red gate,
  the exact failure the fix exists to remove. It also pins a dimension across all 938 tests
  and removes ONE entropy source where the re-mint corrects ANY.
- 🔴 **600/600 GREEN IS 64.2% POWER, NOT ~100%.** At 0.1709% a clean 600-run had a 35.8%
  chance with the bug still present. The load-bearing evidence is the FORCED collision:
  600/600 fail at base, 600/600 pass at HEAD, power 1.0. Also: 600 iterations are not 600
  DRAWS (same-second iterations mint the same sha), and a `distinct_seconds_spanned` field
  was wall-clock seconds under a wrong label.
- **The D3 collapse did NOT save ~50 lines** — `--numstat` says **+136/-65, net +71**,
  because preserving the last-draw property cost a pin. A first count of `+63` came from
  `grep -c "^+[^+]"`, which silently drops added BLANK lines. Two methods, two answers, and
  the prettier one was the wrong one.
- 🔴 **A TEST REVERTED THE AUTOUSE HOST-STATE REDIRECT AND WROTE TO THE OPERATOR'S REAL
  DATA.** `monkeypatch.undo()` reverts EVERY redirect the autouse fixture installed, because
  they share one function-scoped object. 40 rows of fixture residue (one repo, one reference
  number) landed in the live `picks.jsonl` and were biasing the live picker. Use
  `with pytest.MonkeyPatch.context() as mp:`. **Existing guards could not see it** — they
  assert state at their OWN runtime and are structurally blind to another test's `undo()`.
  Verified fixed by reproducing the exact failing path: trap empty, real file untouched.

- 🔴 **`clawgate_handoff.sh resolve` now prints a POSITIVE CONTROL, and it is worth reading
  rather than skimming.** This session got rc=5 alongside *"the SAME endpoint answered 11
  link(s) for session 85a6e6ff…, so the board is reachable, the base URL is right and the
  token is accepted."* That control proves the instrument is wired to something — and the
  tool says in the same breath that it **does NOT prove the session id under test is
  right**, because a wrong id also answers 200 with an empty array. A zero beside a working
  control is still not evidence the session touched no task.
- **The `adoption-scan` gap is the shape this repo has been bitten by before**: a tool that
  answers "is this used?" cannot answer it for the feature built to make usage visible,
  because nobody registered it. Recorded as rank 2 rather than left as a note.

- 📌 **CARRIED FORWARD from the 2026-09-12 `State now` this update replaced — the durable half,
  preserved because a REPLACE heading would otherwise delete it.** The five PRs of the original
  arc, each verified by CONTENT on `origin/main` (never by ancestry — a squash merge never makes
  the branch head an ancestor): **#1569** `45a5973c` auto-open pick recording (tagged
  `auto`/`picker`) + click telemetry · **#1594** `21db164c` the 0.1709% sha-prefix fixture flake,
  fixed at all THREE sites · **#1562** `4d9aee4c` picker window centering · **#1582** `cfdb3899`
  octo.nvim review TUI behind a browser fallback · **#1563** `1e0cca03` this doc. Shipped via
  `scripts/ship.sh` rc=0, both hosts converged and **COMPARED** at `3ae5945a` (workbench 590
  managed artifacts / laptop 550, 0 dangling, 0 stale on each), falling back to nebula for the
  laptop. ⚠ `origin/main` has moved well past that sha; the SHIPPED state is `3ae5945a`.
  **Centering verified LIVE on the deployed artifacts, not from rendered config:** `i3-msg reload`
  succeeded and the rule is in i3's RUNNING config
  (`for_window [class="float" instance="mention-open"] floating enable, move position center`); the
  shared `class="float"` rule carried **no position**, so nothing else moved; a window with the
  picker's exact properties measured **1324x488 at +1058+489** against a workspace usable area of
  `3440x1413 at +0+27` → **delta x=+0 y=+0, exactly centred**; `nvim-octo` is on the CLICK path via
  the wrapper's `makeBinPath` and deliberately NOT on the interactive PATH (the D2 deletion).
  ⚠ Those 1324x488 / 120x22 figures are the WORKBENCH's, and the cell size they imply (~11.0 x
  ~22.2 px) does NOT transfer to the laptop — see the DPI gotcha below. Reading them as
  host-independent is what would have made 200x50 look safe.
- 🔴 **THE HOST WAS THE MISSING VARIABLE, AND AN ABSENT FILE NAMED NO MECHANISM.** The previous
  arc measured `picks.jsonl` absent on the workbench and diagnosed a missing `record_pick` call —
  the diagnosis was right, but the same observable also fits "the operator uses the other host",
  and nothing in the doc distinguished them. What separated them was **asking which host the click
  landed on**: the laptop's row timestamp (`1789249676`, 14:27:56) sat ~48 s before the probe, so
  the click was the operator's and the workbench had simply never been clicked on. **Measure
  per-host state per host; a single-host reading of a per-host file is not a reading of the
  feature.**
- 🔴 **A PROBE CAN EXERCISE THE REAL CODE WITHOUT POLLUTING THE OPERATOR'S DATA — AND THAT IS THE
  ONLY HONEST WAY TO TEST A TELEMETRY PATH.** `MENTION_OPEN_PICKS` redirects the pick ledger and
  `ACTIVITY_SPOOL_DIR` redirects the activity spool (`spool_emit.default_spool_dir`), so the real
  handler ran end-to-end against temp files. `xdg-open`/`alacritty`/`nvim-octo` were stubbed onto
  `PATH` so nothing raised a window. The stub log is the POSITIVE CONTROL that the launch was
  intercepted, and the real `picks.jsonl` was verified ABSENT before AND after. Without the
  redirects this probe would have written a synthetic row into the dataset the arc had just
  cleaned fixture residue out of.
- 🔴 **A WINDOW SIZED IN CHARACTER CELLS IS NOT SIZED.** `REVIEW_COLUMNS`/`REVIEW_LINES` are
  font- and DPI-dependent, so one constant cannot fit two displays — and a 3440px 96-DPI monitor
  masks the bug that a 2256px 201-DPI panel exposes. The fix is to express the constraint in the
  same units as the thing it must satisfy: `resize set 90 ppt 90 ppt` is a percentage of the
  ACTUAL workspace and is host-independent by construction. Cells survive only as a pre-resize
  hint and a fallback for when i3 is not running.
- 🔴 **DO NOT put the resize on `nix/i3/config.nix:85`.** That rule is
  `for_window [class="float"] floating enable` and `class="float"` is shared by every float in the
  system — sizing there resizes windows nobody asked to resize. The discriminator is
  `instance="mention-review"` (from `REVIEW_CLASS`), exactly as the picker's centering rule at
  line 111 uses `instance="mention-open"`. #1619 carries guards pinning this direction so the rule
  cannot later be "simplified" onto line 85 with the suite green.
- 🔴 **i3 MATCHES `for_window` CRITERIA AS UNANCHORED PCRE, AND THAT HIDES A RENAME.** Found by
  #1619's own mutation sweep: renaming `REVIEW_CLASS`'s instance half to `mention-review-v2` still
  matches `instance="mention-review"`, so the rename lands silently, the rule starts over-matching
  future sibling instances, and the NEXT rename — the one that truly makes it inert — reads as
  unrelated. The first sweep killed that mutant via a PRE-EXISTING guard, i.e. for the wrong
  reason; an exactness guard was added and the sweep re-run. **A mutant that dies is not a mutant
  your guard killed.**
- ⚠ **`90` is a judgement, not a measurement.** #1619's guards pin the UNITS and a 1..100 range
  on purpose, so tuning the percentage does not redden the suite. Nobody has looked at 90% on
  either display.
- ⚠ **The geometry test in `test_mention_open.py` is NOT coverage for this fix.**
  `test_the_review_terminal_carries_its_own_window_class_and_geometry` asserts the alacritty argv
  carries `MO.REVIEW_COLUMNS`/`MO.REVIEW_LINES` — it reads its expectation from the
  implementation, so changing the constants cannot break it. Do not quote it as evidence.
- 🔴 **`resume-state.sh` reported three FALSE drift lines on this doc** — #1509, #1569 and #1582
  as "MERGED but handoff frames it as open/in-flight". The doc states all three as merged and
  shipped in a table. The reconciler's heuristic reads a PR reference near in-flight-sounding
  prose; read the doc before acting on a DRIFT line. The `#1283 CLOSED without merge` line is
  likewise already explained in the doc (the private-repo-name disclosure).
- ⚠ **`#12` and `#74` in this doc are NOT PR references** — they are probe inputs from the Tier A
  plausibility measurement and the gh-dash deep-link measurement. The reconciler resolves them as
  devrc PRs anyway, which is where its "12 of 13 referenced PRs" gap came from.

## How to verify
```bash
# THE one check that closes this arc — after a real click on a `repo#N` mention
wc -l < ~/.config/mention-open/picks.jsonl        # must EXIST and GROW (was absent)

# Centering, live, without launching the picker (raises ONE window; restore after)
i3-msg -t get_config | grep 'class="float"'       # line 47 no position; the instance rule centres
# NB: i3-msg needs DISPLAY=:0, XAUTHORITY and I3SOCK from /proc/$(pgrep -x i3)/environ

# The click path reaches the TUI binary (NOT the interactive PATH — that is by design)
W=$(readlink -f ~/.config/alacritty/alacritty.toml)
H=$(grep -oE '/nix/store/[a-z0-9]+-alacritty-mention-open' "$W" | head -1)
grep -o '/nix/store/[a-z0-9]*-nvim-octo' "$H"     # ⚠ do NOT require a version suffix

# What actually shipped, per host
git -C ~/workspace/devrc rev-parse --short HEAD
ssh zach@10.42.0.100 'git -C ~/workspace/devrc rev-parse --short HEAD'
```
