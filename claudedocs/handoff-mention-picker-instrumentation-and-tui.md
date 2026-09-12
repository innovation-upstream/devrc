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
- Branch: `main`, SHARED checkout, moving constantly (observed `b42ac7c3` → `db7bf3ff` →
  `b1abf6b1` → `5dac6ec2` within this session). Re-read `git status` before any write.
- 🔴 `main` is **GREEN** again — the kill-scanner red is fixed (see the gotcha below).
- **Four PRs open, none merged, nothing deployed:**
  - **#1569** `feat/mention-pick-recording-and-click-telemetry` — the auto-open recording
    fix + click telemetry. Agent still running at last observation.
  - **#1562** `feat/mention-picker-center` — centers the picker float. Merged with current
    `main` (`c9b45236`); its gate was re-launched unpiped and had **no `RESULT:` line yet**.
  - **#1563** `docs/handoff-mention-picker-instrumentation` — this doc.
  - octo.nvim review TUI — **dispatched, no branch yet**.
- **Five claims HELD:** `mention-pick-recording-autoopen` · `mention-picker-center` ·
  `mention-click-telemetry` · `mention-pr-tui-research` · `mention-pr-tui-octo-integration`
- 🔴 **NO GATE VERDICT EXISTS FOR ANY OF THIS WORK.** Both running gates are
  could-not-vouch, not green. `#1562`'s five i3/launcher files are green on its merged tree
  (129 passed), which is a claim about those files only.
- Deploy/verify status: **nothing deployed, nothing observed live.** No `home-manager
  switch`, no `ship.sh`. Picker centering is verified against the RENDERED i3 config only —
  nobody has seen the window land centered.

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

## Next steps (ranked)
1. **Land #1569** (auto-open recording + telemetry), then **#1562** (centering).
   Repo: devrc. Both need a gate verdict first — neither has one.
   forcing: user — operator asked for instrumentation and reported the left-pinned window.
2. **Land the octo.nvim TUI PR** once the agent opens it. 🔴 It rewrites the same two
   `open_url` call sites as #1569 (`mention-open.py:2439` and `:2507`), so whichever lands
   second must TEST-MERGE and read the merged result of both sites — a clean `git merge` is
   not a clean merge. Repo: devrc.
   forcing: user — operator asked for a TUI instead of the browser.
3. **`ship.sh`, then verify the click path on the deployed wrapper** — `picks.jsonl` must
   GROW on the workbench after a click, including from an auto-open. That file staying
   absent is the whole reason this arc exists.
   forcing: user — the recording fix is unverifiable until it is deployed and clicked.
4. **Operator-only live checks nothing hermetic can cover** — `:map <localleader>pm` must
   report *No mapping found* (the merge-safety assertion), `nvim-octo <repo> <N>` on an
   ISSUE number must open an issue buffer (the auto-detect control), and one real hint click.
   forcing: none
5. **Release the five claims** as each item lands: `claim-work --release <slug>`.
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

## How to verify
```bash
# Tier A ranging, headless, counts only — NEVER paste the rows (private repo names)
cd ~/workspace/devrc && nix develop . -c python3 -c '
import importlib.util,collections
s=importlib.util.spec_from_file_location("mo","scripts/mention-open.py")
mo=importlib.util.module_from_spec(s); s.loader.exec_module(mo)
r=mo.load_known_ranges(); print("rows",len(r))
for N in ("12","1291"):
    print(N, collections.Counter(mo.plausibility_class(N,r.get(k)) for k in r))'

# Tier B is recording (the whole point of this arc) — after agent 1 lands + a switch
wc -l < ~/.config/mention-open/picks.jsonl        # must GROW after clicking a mention
# and an auto-open must now appear, tagged, not only picker selections

# The two kill-scanner guards that were red (now green)
nix develop . -c python3 -m pytest scripts/claude-hooks/tests/test_guard_core.py \
  -k "every_kill_server_call_site_in_the_repo_is_classified or no_tracked_shell_text_writes_a_kill_this_guard_would_deny" -q

# Picker centering — RENDERED CONFIG ONLY, never raise a window
grep -n 'mention-open' nix/i3/config.nix

# Claims still held by this arc
claim-work --list | grep -E 'mention-(pick|picker|click|pr-tui)'
```
