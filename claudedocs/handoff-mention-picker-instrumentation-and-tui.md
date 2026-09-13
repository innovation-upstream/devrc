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
🔴 **THREE OF THE FOUR ORIGINAL OBJECTIVES ARE NOW CLOSED WITH LIVE EVIDENCE. THE FOURTH — THE
PR REVIEW TUI — IS NOT, AND IT IS THE ONLY REAL BLOCKER.**

| # | objective | state |
|---|---|---|
| 1 | record picks that were dropped | ✅ **2 `via=auto` rows** from real clicks (laptop, 19:38 + 21:55 on 09-12) |
| 2 | centre the picker window | ✅ closed in the earlier arc — ⚠ its WIDTH changed 09-13, new geometry unverified live |
| 3 | click telemetry → `activity.events` | ✅ **9 rows** with the full dim set |
| 4 | open PRs in a TUI, not a browser | 🔴 **NOT closed** — see the investigation block |

Objective 3's evidence (ClickHouse `activity.events`, `text='mention-open'`, 9 rows,
19:08 09-12 → 04:58 09-13 UTC), one row verbatim:
`{"tool":"mention-open","outcome":"picked","repo":"civitai/civitai","platform":"github",`
`"picker_shown":true,"offered_total":393,"rank":3,"plausibility":"below","reason":"selected",`
`"ordered":true,"pinned_above":2,"surface":"tui"}`

**Merged, deployed to BOTH hosts and verified by content on `origin/main` (never by ancestry):**

| PR | squash | what |
|---|---|---|
| **#1619** | `243b3a06` | review window sized per-host in percent of OUTPUT; cell hint deleted |
| **#1632** | `c3ff700c` | picker 120→110 cols — it overflowed the laptop by 28 px |
| **#1633** | `86560804` | `NVIM_APPNAME` isolation — the TUI was loading the operator's packer plugins |
| **#1620** | `f5942a24` | the previous revision of this doc |

`ship.sh` rc=0 three times; the last run converged both hosts at `86560804` and **COMPARED**
them (594/553 managed artifacts, 0 dangling, 0 stale each). `i3-msg reload` run on both; the
review rule is in i3's RUNNING config (`64 ppt 77 ppt` workbench / `90 ppt 90 ppt` laptop), and
screen state was re-read after each reload and was unchanged.

🔴 **AUDIT LADDER ON #1619 DELIBERATELY ENDED after round 2 — read it as ENDED, not CONVERGED.**
Rounds 0, 1 (blind, nine axes) and 2 (delta) each found real defects, all fixed; zero 🔴 in any
round. Ended on the prose-payload criterion with the reason recorded on the PR
(`Audit ladder — DELIBERATELY ENDED` comment), plus the residuals that are OPEN not absent.

- **This session resolved no clawgate task** — `clawgate_handoff.sh resolve` exited 5 with a
  POSITIVE CONTROL (the same endpoint answered 3 links for another session, so the board is
  reachable and the token accepted). That narrows it to "a correct id WOULD have resolved"; it
  is NOT evidence this session touched no task. No `clawgate-task:` field written.
- All `mention-*` claims RELEASED; my two worktrees removed. ⚠ `devrc-picker-rank`
  (`fix/picker-fzf-ranking`) belongs to ANOTHER session — do not touch it; and
  `.claude/worktrees/agent-a8d600896310294ae` is LOCKED by the harness.
- ⚠ The shared checkout moved ~8 times under this session. Re-read `git status` before any write.

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

### ✅ RETIRED — "The review TUI's 90% is unverified against any real screen"
- as-of: 2026-09-12
🔴 **SUPERSEDED 2026-09-12 — the block above it is obsolete in three ways; do not act on it.**
(a) The fix is no longer a flat `90 ppt`: the operator chose **per-host** values, and the
workbench is `64 ppt 77 ppt`. (b) Its leading hypothesis said *"alacritty's cell is roughly 2x
the workbench's"* — **measured wrong**: `TIOCGWINSZ` on each host's alacritty pty gives workbench
**11.0 × 22.0 px** and laptop **19.0 × 37.0 px**, i.e. **1.73× wide, 1.68× tall, 2.90× area**.
(c) Its "Next probe" computes against the WORKSPACE rect, which is the wrong rect — see the
Gotchas entry. The eliminations in that block were each re-checked and are still TRUE; it is the
framing and the arithmetic that moved.

### The rendered grid has never been observed, and one term remains unmeasurable
- as-of: 2026-09-12
- **Symptom + exact repro:** n/a — this is a residual UNKNOWN on a shipped-but-undeployed fix,
  not a live defect. Repro for the check: after `ship.sh` + `i3-msg reload`, click a `repo#N`
  GitHub mention and read the `mention-review` window from `i3-msg -t get_tree`.
- **Observed (with values):** workbench output `3440x1440` / workspace `3440x1413` / bar 27 px;
  laptop output `2256x1504` / workspace `2256x1480` / bar 24 px; i3 **4.25.1** both hosts. Cell
  `11.0 x 22.0` (workbench) and `19.0 x 37.0` (laptop). Under the corrected border model
  (BS_PIXEL, 2 px) the workbench renders **199 x 50** at `77 ppt` **and** at `78 ppt` — the two
  are indistinguishable on screen.
- **Ruled out:** that floats here take i3's `BS_NORMAL` default (titlebar + borders).
  `default_floating_border` is applied only inside `floating_enable()` under `if (automatic)`
  (`floating.c:352-355`), and `for_window … floating enable` is a COMMAND reaching
  `floating_enable(con, false)` (`commands.c:1157`) after the `want_floating` decision
  (`manage.c:746` vs `:462-546`). So the con keeps `default_border pixel 2` → **BS_PIXEL,
  `logical_px(2)`**. Live: the running alacritty windows report `border=pixel`,
  `current_border_width=2`. via: code
- **Ruled out:** that `ppt` resolves against the workspace rect. `cmd_resize_set` multiplies
  `con_get_output(floating_con)->rect`, and i3's own implementing testcase computes against a
  fake OUTPUT. via: code
- **Ruled out:** that `move position center` shares that basis — it does NOT.
  `cmd_move_window_to_center` calls `floating_center(…, con_get_workspace(…)->rect)` for
  `position`; only `move absolute position center` uses `croot->rect`. The chain **sizes against
  the output and places against the workspace.** via: code
- **Leading hypothesis:** none needed — the mechanism is established from source. What is
  genuinely open is only whether the `for_window` FIRES at map time and the window comes up at
  that size, which no audit round can reach.
- **Next probe:** the rank-1 click. 🔴 A floating `deco_rect` could not be measured in ANY of the
  three rounds — the live tree held **zero floating containers** every time (all `floating:
  auto_off`), so the −4/−4 inset is derived from i3's C, not measured. ⚠ The live alacritty
  windows sit in a **tabbed** parent where `con_border_style()` overrides to `BS_NORMAL`, so their
  client height is rect−2, not rect−4; a float's parent is `L_SPLITH` so no override applies —
  the live read is evidence for the border STYLE and the CELL SIZE only.
  `floating_resize`'s increment snapping remains unmodelled.

### ✅ RETIRED — "The rendered grid has never been observed, and one term remains unmeasurable"
- as-of: 2026-09-13
🔴 **HALF of that block is now CLOSED by measurement; do not re-derive either half.** The
decoration term it called unmeasurable — "a floating `deco_rect` was never obtainable, no
floating containers in any live tree" — was measured the moment the PICKER was captured:
client `2280x814` inside rect `2284x818`, i.e. **exactly 2 px per side**, confirming the
BS_PIXEL/`logical_px(2)` model that all three audit rounds could only derive from i3's C.
⚠ Still open from it: the REVIEW window's character grid has never been observed on either host,
and `floating_resize`'s increment snapping remains unmodelled.

### 🔴 Objective 4: the review TUI has never run end to end
- as-of: 2026-09-13
- **Symptom + exact repro:** selecting a row in the mention picker opened a neovim window that
  errored `module 'lyaml' not found`. Repro before the fix: click a `repo#N` on the laptop,
  select a row from the picker.
- **Observed (with values):** `E5108: Lua:
  .../nvim/site/pack/packer/start/qdr.nvim/lua/qdr-nvim/qdr.lua:2: module 'lyaml' not found`,
  then `loop or previous error loading module 'qdr-nvim'`. Reproduced headlessly off the
  wrapper's own neovim (`/nix/store/3x220pvz…-neovim-0.12.5/bin/nvim --headless +qa`).
- **Ruled out:** that octo or the wrapper's own plugin set was at fault — the trace names a
  PACKER-installed plugin of the operator's under `~/.local/share/nvim/site`, loaded because
  neovim's default `packpath` includes that directory whatever `-u` says. via: measurement
- **Ruled out:** that the fix is the missing rock. `default.nix` claims the plugin set "cannot be
  broken by" an editor-config edit; adding `lyaml` would make that ONE plugin load inside a
  review TUI with no business running it and leave every other plugin able to break it next
  time. via: code
- **Ruled out:** that `NVIM_APPNAME` isolation breaks octo — with it set, a headless load emits
  NOTHING, `require("octo")` is `true` and `vim.fn.exists(":Octo")` is `2`, so `setup()` ran and
  still found `gh`. Re-verified on the DEPLOYED laptop artifact
  (`/nix/store/l0x996h4…-nvim-octo`) with `qdr.nvim` still installed. via: measurement
- **Leading hypothesis:** none — fixed and deployed (`86560804`). What is UNVERIFIED is only
  whether a human gets a working review buffer, because the store path is baked into alacritty's
  config and a running terminal resolved the old one.
- **Next probe:** rank 1. A NEW alacritty window, click, select, and `:map <localleader>pm`.

## Next steps (ranked)
1. 🔴 **Confirm the review TUI actually works — OPEN A NEW ALACRITTY WINDOW FIRST.** The
   `nvim-octo` store path is baked into alacritty's config, which a running terminal already
   resolved, so an existing window still execs the OLD wrapper and will reproduce the `lyaml`
   crash. In the new window click a `repo#N`, select a row, and confirm a review buffer loads.
   Then `:map <localleader>pm` **must report `No mapping found`** — the merge-safety assertion,
   never yet checked in a live buffer. Repo: devrc.
   forcing: user — the operator hit the crash; objective 4 has never worked end-to-end.
2. **Verify the picker's new 110-col geometry live.** Predicted `2094x818` rect with 162 px
   margin and a POSITIVE x (it was `2284x818 at x=-14`). Read it from `i3-msg -t get_tree`.
   Repo: devrc.
   forcing: user — the operator reported the overflow; the fix is unverified on a screen.
3. **Add the `adoption-scan` registry row for the click telemetry.** CONFIRMED ABSENT
   (no `mention-open` hit under `scripts/adoption*`). The dims (`surface`, `rank`,
   `plausibility`, `offered_total`, `via`, `pinned_above`) are landing in ClickHouse NOW, so the
   tool built to answer "is this used?" still cannot see the feature built to make usage visible.
   Repo: devrc.
   forcing: none
4. **Decide whether Tier A ranking helps — now ANSWERABLE and unanswered.** `rank` and
   `plausibility` are in the data (`rank:3 plausibility:below`, `rank:2 plausibility:plausible`).
   9 rows is far too few; revisit after weeks of clicks. Chosen `rank` should cluster near 0 and
   `plausibility` skew PLAUSIBLE. Repo: devrc.
   forcing: none
5. **Rename `test_the_WORKBENCH_keeps_rendering_the_size_it_ALREADY_renders`** — the name
   overstates what it pins (the workbench renders 199x50, one column NARROWER than the 200x50 it
   used to). Deferred from audit round 2 because renaming ripples into the red-at-base matrix and
   the 20-mutant ledger; its docstring now tells the reader to read the name narrowly. Repo: devrc.
   forcing: none
6. **Close or merge `#1539`** (`docs/handoff-arc-final-close`) — the SUPERSEDED mention-arc
   handoff naming the obsolete two-PR dependency for main's kill-scanner red. Repo: devrc.
   forcing: none
7. **Prune this arc's agent worktrees** under `.claude/worktrees/agent-*`. ⚠ Skip
   `agent-a8d600896310294ae` (LOCKED — never force it) and anything not yours; the repo holds
   ~238 worktrees belonging to other sessions. Repo: devrc.
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

- 🔴 **A WINDOW SIZED IN CHARACTER CELLS IS NOT SIZED — and the host that masks the bug is the
  one that makes it look safe.** `REVIEW_COLUMNS`/`REVIEW_LINES` are font- and DPI-dependent, so
  one constant cannot fit two displays: 200×50 is 64%×78% on the workbench (fits) and 168%×125%
  on the laptop (overflows). The earlier arc's centering measurement (picker 120×22 → 1324×488)
  was a WORKBENCH reading, and treating its implied cell size as host-independent is exactly what
  made 200×50 look fine.
- 🔴 **i3's `resize set … ppt` RESOLVES AGAINST THE OUTPUT RECT, NOT THE WORKSPACE RECT — and
  `move position center` in the same chain uses the WORKSPACE.** Both established from i3 4.25.1
  source (`cmd_resize_set`, `cmd_move_window_to_center`). The bar's height (27/24 px) is the
  difference, so WIDTH is unaffected and HEIGHT is understated by one bar. This cost a real
  defect: `78 ppt` of the workspace's 1413 looked sub-cell, while against the output's 1440 it is
  1123.2 px — 51 rect rows. **Do not carry one rect over to the other**, and note i3's own test
  suite cannot catch this because its fake outputs carry no bar.
- 🔴 **A `for_window … floating enable` CONTAINER DOES NOT TAKE `default_floating_border`.** That
  default is applied only on the `automatic` path inside `floating_enable()`; a `for_window`
  command reaches `floating_enable(con, false)`, so the con keeps `default_border` — here
  `pixel 2`. Getting this wrong inverted a whole round's arithmetic: with a titlebar the 51st row
  is visible, with a 2 px border it is absorbed, and `77` vs `78` render **identically**. `77` is
  retained only because it is what is committed; **its original rationale is void and was
  deliberately NOT replaced.**
- 🔴 **A PROBE CAN EXERCISE THE REAL CODE WITHOUT POLLUTING THE OPERATOR'S DATA.**
  `MENTION_OPEN_PICKS` redirects the pick ledger and `ACTIVITY_SPOOL_DIR` the activity spool
  (`spool_emit.default_spool_dir`); stub `xdg-open`/`alacritty`/`nvim-octo` onto `PATH` so nothing
  raises a window, and the stub log is the POSITIVE CONTROL that the launch was intercepted.
  Verify the real file ABSENT before AND after. Without the redirects this probe would have
  written a synthetic row into the dataset the arc had just cleaned fixture residue out of.
- 🔴 **THE HOST WAS THE MISSING VARIABLE, AND AN ABSENT FILE NAMES NO MECHANISM.** The previous
  arc read "`picks.jsonl` absent on the workbench" as the defect's signature; the same observable
  also fits "the operator uses the other host". What separated them was asking WHICH host the
  click landed on. Measure per-host state per host.
- 🔴 **AN AUDIT LADDER WHOSE PAYLOAD IS PROSE CANNOT TERMINATE ON ITS OWN.** #1619 ships ~5 lines
  of code and ~150 lines of comment, so (a) the attribution gate is inert — comment lines in
  payload files ARE payload lines, so every round is non-zero by construction — and (b) "fixed a
  defect" and "reworded a warning" are the same edit: round 1's fix introduced a false claim
  *while documenting the term it had just found it was ignoring*, and round 2's fix was entirely
  prose correcting it. **Ended on the stated prose criterion** (no 🔴, blast radius = "a comment
  contains a false sentence", and the recurring SHAPE swept at every site with an enumerated
  search and a positive control). The residuals are written on the PR so they read as OPEN, not
  absent.
- 🔴 **FOUR SWEEP-HARNESS BUGS ON ONE PR, ACROSS FOUR AGENTS, EACH ALREADY BRIEFED ON THE
  EARLIER ONES** — a `^` without `re.MULTILINE` (0 names over a log with 14 failures); a verdict
  regex matching pytest's `short test summary info` banner (SURVIVED over 8 failures); a
  `-k review` filter silently deselecting the workbench guard; and `-q` printing neither
  `collected N items` nor a decorated tail so both regexes missed. **Not one was caught by
  reading more carefully** — every catch came from a control or from cross-checking the failure
  COUNT against a second independently-derived read. A comment-only mutant that must SURVIVE is
  the third control, and it is what makes the kills attributable at all.
- 🔴 **WORKTREE ISOLATION DOES NOT SURVIVE A SESSION RESTART.** An audit agent's worktree was
  removed during an API-limit restart; its cwd silently fell back to the SHARED checkout and it
  ran `git checkout --detach` there before noticing six tool calls later. It restored it and the
  round trip is visible in the reflog (`main` never moved, tree clean, stash stack untouched —
  independently verified afterwards, because an agent's own "cleaned up" claim is not evidence).
  **Re-check `pwd` after any resume, before the first write.**
- ⚠ **The geometry test in `test_mention_open.py` was NOT coverage and is now a negative pin.**
  It used to assert the argv carried `MO.REVIEW_COLUMNS`/`MO.REVIEW_LINES` — an expectation read
  out of the implementation. It now asserts the review spawn carries NO `window.dimensions` at
  all, so nobody silently re-adds a cell hint.
- ⚠ **`nix/graphical.nix:14-15` was STALE and contradicted this PR's deploy step** — it claimed
  writing `~/.config/i3/config` is inert because the system forces `i3 -c /etc/i3.conf`. Measured
  false on both hosts: no such file, no `-c` in i3's cmdline, and `get_version` reports
  `loaded_config_file_name: /home/zach/.config/i3/config`. Corrected in #1619.
- 🔴 **`resume-state.sh` reported three FALSE drift lines on this doc** (#1509, #1569, #1582 as
  "MERGED but framed as open"); the doc states all three as merged and shipped. Its heuristic
  reads a PR reference near in-flight-sounding prose. ⚠ **`#12` and `#74` in this doc are NOT PR
  references** — they are probe inputs from the Tier A plausibility and gh-dash deep-link
  measurements, which is where its "12 of 13 referenced PRs" gap came from.
- 🔴 **The handoff write gate refused `status=behind`, and committing this doc to `main` would
  have been wrong anyway** — devrc's own `CLAUDE.md` forbids committing to `main` in either host
  checkout (a diverged host is silently skipped by `ship.sh` thereafter), and every recent
  handoff landed via PR. Land it on a branch in a throwaway worktree.
- ⚠ **The write gate does NOT always warn when a REPLACE heading drops durable content.** This
  doc's `State now` carried the five squash shas and the live centering measurements; no warning
  fired. They were carried forward under `Gotchas` by reading the diff. A silent run is not
  evidence nothing durable was dropped.

- 🔴 **VERIFYING THE CONTAINER IS NOT VERIFYING THE CONTENTS, AND THIS ARC PAID FOR IT TWICE.**
  Three audit rounds refined the review window's GEOMETRY and each recorded "the live path is
  unverified"; the first real click found a **crash inside** the window, not a sizing problem —
  and the telemetry still said `surface: "tui"`, because alacritty exits 0 whether its `-e`
  payload runs or dies at 127. A measured, centred, correctly-sized window containing a stack
  trace satisfies every assertion this repo can make about it.
- 🔴 **`for_window … floating enable` DOES NOT TAKE `default_floating_border`.** That default is
  applied only on the `automatic` path inside `floating_enable()`; a `for_window` COMMAND reaches
  `floating_enable(con, false)`, so the container keeps `default_border` — here `pixel 2`.
  Getting this backwards inverted a whole round's arithmetic: with a titlebar the 51st row is
  visible, with a 2 px border it is absorbed, so `77 ppt` and `78 ppt` render IDENTICALLY.
  `77` is retained only because it is committed; its original rationale is void and was
  deliberately NOT replaced.
- 🔴 **i3's `resize set … ppt` RESOLVES AGAINST THE OUTPUT RECT; `move position center` IN THE
  SAME CHAIN USES THE WORKSPACE.** The bar's height (27/24 px) is the difference, so width is
  unaffected and height is understated by one bar. i3's own test suite cannot catch this — its
  fake outputs carry no bar. Do not carry one rect over to the other.
- 🔴 **TWO WINDOWS HAD THE SAME DEFECT AND ONLY ONE WAS FOUND BY REASONING.** Both the review
  window and the PICKER were sized in CELLS (`11.0x22.0` px on the workbench, `19.0x37.0` on the
  laptop), so one constant could not fit both displays. The picker was found only because the
  operator said the symptom persisted and the next step was to MEASURE rather than to tune the
  number they had asked for — narrowing the review window would have shrunk a correct window and
  left the actual complaint untouched. Three audit rounds had seen the picker and filed it
  "pre-existing, cosmetic"; it was the window on screen.
- 🔴 **AN AUDIT LADDER WHOSE PAYLOAD IS PROSE CANNOT TERMINATE ON ITS OWN.** #1619 shipped ~5
  lines of code and ~150 of comment, so the attribution gate is inert (comment lines in payload
  files ARE payload lines) and "fixed a defect" and "reworded a warning" are the same edit:
  round 1's fix introduced a false claim *while documenting the term it had just found it was
  ignoring*, and round 2's fix was entirely prose correcting it. Stop on the stated criterion and
  WRITE THE REASON DOWN, or an ended ladder is indistinguishable from a converged one.
- 🔴 **FOUR SWEEP-HARNESS BUGS ON ONE PR, ACROSS FOUR AGENTS EACH BRIEFED ON THE EARLIER ONES:**
  a `^` without `re.MULTILINE` (0 names over a log with 14 failures); a verdict regex matching
  pytest's `short test summary info` banner; a `-k review` filter silently deselecting the
  killing guard; and `-q` printing neither `collected N items` nor a decorated tail. **Not one
  was caught by reading more carefully** — every catch came from a control or from
  cross-checking the failure COUNT against a second, independently-derived read.
- 🔴 **WORKTREE ISOLATION DOES NOT SURVIVE A SESSION RESTART.** An audit agent's worktree was
  removed during an API-limit restart; its cwd silently fell back to the SHARED checkout and it
  ran `git checkout --detach` there, noticing six tool calls later. Independently verified
  afterwards (reflog shows a clean round trip, `main` never moved, stash stack untouched) —
  because an agent's own "cleaned up" claim is not evidence. **Re-check `pwd` after any resume.**
- 🔴 **`xargs -0 command grep` SILENTLY FINDS NOTHING** — `command` is a shell builtin, so xargs
  has nothing to exec, and `2>/dev/null` eats the error. It returned "no consumers of
  PICKER_COLUMNS", which was false. **The only reason it was caught is that the POSITIVE CONTROL
  came back empty too.** Use an absolute `/run/current-system/sw/bin/grep`.
- 🔴 **A CROSS-REFERENCE IS A CLAIM: `format_row` NEVER EXISTED in `mention-open.py`** — the
  wrapper is `picker_header`. The false name sat in a comment, was propagated once while
  REWRITING that comment, and the first draft of the new guard used `hasattr(MO, "format_row")`
  and would have **SKIPPED ITSELF SILENTLY**. Other modules do have a real `format_row`, which is
  exactly why grepping for it looks reassuring. The guard now calls `picker_header` by name.
- ⚠ **A STALE MEASUREMENT REPORTED AS CURRENT STATE.** Objectives 1 and 3 were reported as open
  after the operator's clicks had already closed them — the summary described the last reading
  rather than re-reading. Re-measure before asserting status, including status you measured
  yourself an hour earlier.
- ⚠ **THIS DOC IS NOW CAPPED.** `scripts/tests/test_handoff_doc_size.py` caps every
  `claudedocs/**/handoff-*.md` at `MAX_BYTES` and this doc is **NOT** grandfathered, so it must
  stay under the ceiling — read the number in the test, never restate it. It was 50,476 B before
  this update. `Open investigations` and `Gotchas` APPEND, so the doc only grows: future updates
  should prune a superseded block rather than only adding, and a breach needs a grandfather entry
  (which the test calls a ratchet whose removal is the goal).

## How to verify
```bash
# 🔴 RANK 1 — and a NEW alacritty window is mandatory: the nvim-octo store path is baked into
# alacritty's config, which a running terminal already resolved.
#   click a `repo#N` -> select a row -> a review buffer must LOAD (no `lyaml` trace)
#   then, in that buffer:  :map <localleader>pm      -> must print `No mapping found`

# The deployed wrapper, without opening a window (run on the host you are testing)
NO=$(grep -oE '/nix/store/[a-z0-9]*-nvim-octo' "$(readlink -f ~/.config/alacritty/alacritty.toml \
  | xargs grep -oE '/nix/store/[a-z0-9]+-alacritty-mention-open' | head -1)" | head -1)
grep -n 'NVIM_APPNAME=nvim-octo' "$NO/bin/nvim-octo"      # must be present

# Geometry, live, WITHOUT launching anything — read it while a window happens to be open
i3-msg -t get_tree | python3 -c "import json,sys
def w(n):
  p=n.get('window_properties') or {}
  if p.get('instance') in ('mention-review','mention-open'): print(p['instance'], n['rect'])
  for c in n.get('nodes',[])+n.get('floating_nodes',[]): w(c)
w(json.load(sys.stdin))"
# laptop expectations: review 2030x1353 at +113+88 · picker 2094x818 (was 2284x818 at x=-14)
# 🔴 i3-msg needs DISPLAY, XAUTHORITY and I3SOCK from /proc/$(pgrep -x i3)/environ

# Objectives 1 and 3, both already CLOSED — these re-confirm rather than discover
grep -c '"via": "auto"' ~/.config/mention-open/picks.jsonl    # >0 on the laptop
KUBECONFIG=$KC_HOMELAB kubectl exec -n activity deploy/clickhouse -- \
  clickhouse-client --query "SELECT count() FROM activity.events WHERE text='mention-open'"

# What shipped, per host
git -C ~/workspace/devrc rev-parse --short HEAD
ssh zach@10.42.0.100 'git -C ~/workspace/devrc rev-parse --short HEAD'
```
