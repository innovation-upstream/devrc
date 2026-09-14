# Handoff: octo-review-tui-legend-and-confirmations — 2026-09-14

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
Make the `nvim-octo` review TUI usable: nothing told the operator how to act in it. Three
operator asks — a legend, an open-in-browser hotkey, and `?` to show the legend.

## State now
✅ **SHIPPED AND THE DEPLOY IS NOW VERIFIED — `#1653`, squash `d4179fbd`.**
🔴 **STILL NOT EXERCISED BY A HUMAN.** Needs a NEW alacritty window (rank 1).

**Deploy, measured after the fact** (the previous revision of this doc asserted "deployed to both
hosts" while `ship.sh` was still running — see the Gotchas entry):
- `ship.sh` rc=0; **2 hosts compared, both at `d4179fbd`**; workbench 598 managed artifacts /
  laptop 557, **0 dangling, 0 stale** on each; both `VERIFIED — on branch main at origin/main
  (clean tree) + switched`. LAN unreachable, fell back to nebula for the laptop, as always.
- **The DEPLOYED artifact on the laptop was probed headlessly** (`/nix/store/31l5l2zn…-nvim-octo`,
  no window raised): `legend rows: pull_request=46 review_diff=20`, and
  `legend key: pull_request=?  review_diff=g?`. So the per-kind key resolution is live on the
  machine that will be used, not merely in the repo.

**What it does:** `?` opens a generated legend of **this buffer's own bindings** on 8 of 9 kinds;
`review_diff` uses **`g?`** so reverse-search survives where real source is read. Merge is bound
again as `\pm` behind a confirmation naming PR, repo and method — a deliberate reversal of the
earlier "merge must be typed" decision, authored by the operator twice. Also confirmed:
`approve_review` (`<C-a>`), `approve_pr` (`\qa`), `submit_review` (`\vs`), `delete_comment`
(`\cd`). Deliberately NOT confirmed (trivially reversible): `close_issue`, `reopen_issue`,
`remove_reviewer`, `remove_assignee`, `remove_label`. A broken `apply_mappings` seam deletes the
`:Octo` command and writes the diagnosis into the visible buffer; exit stays 0.

**Recon that shaped it:** `open_in_browser = <C-b>` ALREADY existed on 5 kinds — that ask was
discoverability, and nothing was added for it. `maplocalleader`/`mapleader` are both `nil` → a
literal `\`, so the legend prints RESOLVED keys. `?` and `g?` were both free in all 9 tables.

**Audit ladder: round 0, round 1 (blind), two fix rounds, ZERO 🔴.** Final sweep 44 mutants /
43 killed, comment-only control SURVIVED, every earlier mutant retained. Suite 176 green.
**The branch's CI red was another effort's doc** (`test_NO_TRACKED_FILE_ASSERTS_the_RETRACTED_
two_entry_boundary` on `claudedocs/handoff-index-store-claims-accuracy.md`), fixed upstream by
`18a95e23`, which the branch predated — **the MERGED tree ran 964 passed**, that guard included.

- **IN FLIGHT: `devrc#1666`** — this document. OPEN, head `1cb19ea8`, `MERGEABLE/UNSTABLE`.
- **This session resolved no clawgate task** — `resolve` exited 5 with a POSITIVE CONTROL (the
  same endpoint answered 3 links for another session, so the board is reachable and the token
  accepted). That narrows it to "a correct id WOULD have resolved"; it is NOT evidence this
  session touched none. No `clawgate-task:` field written.

## Open investigations — live diagnosis state

### 🔴 Nothing here has been seen on a screen
- as-of: 2026-09-13
- **Symptom + exact repro:** n/a — this is an unexercised shipped feature, not a defect. Repro for
  the check: open a NEW alacritty window, click a `repo#N` mention, press `?`.
- **Observed (with values):** every claim below is HEADLESS. `?` bound once on 8 kinds and `g?`
  with zero `?` on `review_diff` (via `vim.fn.maparg`); `\pm` on `pull_request` only; legend rows
  == actually-bound normal-mode keymaps for all 9 kinds (46/21/18/5/20/18/2/2/9 = 141); a
  `pull_request` legend is **46 rows / 52 lines**; confirmation drove `no` → 0 merges 1 prompt,
  `y` → 0 merges, `"  YES  "` → merges, `\cd` → `delete_comment` only after `yes`.
- **Ruled out:** that the legend shows another kind's table — the registered callback was driven
  per kind in a real editor and each produced its own header. via: measurement
- **Ruled out:** that a broken seam leaves a usable half-wired editor — `:Octo` is deleted, so the
  wrapper's `-c "Octo …"` finds nothing. via: measurement
- **Ruled out:** that the diagnosis is visible via `vim.notify` alone — the `E492` forces a
  `Press ENTER` and that keypress wipes the message area, landing on the splash screen. Hence the
  scratch buffer. Confirmed by replaying real TUI bytes through a terminal emulator: the
  diagnosis occupies screen lines 7–21 before any keypress and survives the ENTER. via: measurement
- **Ruled out:** that an ERROR-level `vim.notify` is safe inside a `luafile` — it is promoted to
  a vim error `pcall` CANNOT catch, which aborted the teardown. It is WARN now. via: measurement
- **Leading hypothesis:** none — the mechanism is measured. What is open is only whether it reads
  well on a real screen at the operator's font size.
- **Next probe:** rank 1.

## Next steps (ranked)
1. 🔴 **OPEN A NEW ALACRITTY WINDOW, then click a `repo#N` and press `?`.** The `nvim-octo` store
   path is baked into alacritty's config and a running terminal already resolved the OLD one — an
   existing window will show the pre-merge behaviour and look like the deploy failed. Then press
   `\pm` and confirm the prompt names the PR, repo and `squash`, and that anything other than
   `yes` aborts. Repo: devrc.
   forcing: user — three operator asks, none yet seen on a screen.
2. **Four properties are KNOWN-UNGUARDED — recorded, not silently left.** Each survives a mutant
   green: deleting/inverting `table.sort(rows, M.row_order)` (legend renders in hash order);
   `CONFIRMED_VERBS`' `mode` is outside the pinned ledger (widening `approve_review` to insert
   mode survives, so `<C-a>` would fire while typing a review body); the legend's merge-footer
   SENTENCE is unpinned while `said_yes` is pinned (they can drift, telling the operator `y`
   works when it aborts); `if ok then` → `if true then` survives. Repo: devrc.
   forcing: none
3. **`_G.NvimOcto` is a writable global** — the deliberate observation surface the tests drive. An
   auditor disarmed the confirmation from a probe by reassigning `NvimOcto.ask`. Safe here (pinned
   plugin set, no third-party Lua) and now a stated assumption; revisit if that changes. Repo: devrc.
   forcing: none
4. **`pkgs.luajit` is not pinned to the LuaJIT `pkgs.neovim` embeds.** Same build today
   (`2.1.1785763465` in both, measured); nothing enforces it. Low risk — LuaJIT is 5.1-stable. Repo: devrc.
   forcing: none
5. **`test_runtime_shebangs.py::test_no_test_writes_a_usr_bin_env_shebang_at_runtime` is RED on
   `main`** — 44 hits, all in `scripts/claude-hooks/tests/test_guard_core.py`. Pre-existing and
   confirmed untouched by this arc (`git diff` for that file across the range is empty). Repo: devrc.
   forcing: none

## Gotchas / decisions / dead-ends
- 🔴 **TWO OF THE THREE ASKS WERE ONE PROBLEM.** `open_in_browser` was already bound to `<C-b>` on
  five kinds. The ask was never "add a hotkey" — it was "nothing tells me what this can do", and
  131 bindings were invisible. Check what EXISTS before building what was asked for.
- 🔴 **A `FileType octo` AUTOCMD WOULD HAVE MISSED THE REVIEW SURFACES.** `filetype=octo` is set
  only on PR/issue/discussion buffers; `review_diff`, `file_panel` and `submit_win` get their
  mappings via `utils.apply_mappings(kind, bufnr)` on buffers whose filetype is the diffed FILE's
  language. That is 40+ of the bindings, in exactly the buffers you review in. `apply_mappings` is
  the one seam all nine kinds pass through **and it carries the kind** — which is what a per-buffer
  legend needs. Wrapping a plugin's module function is fragile, so the wrap asserts the seam and
  tears down if it moves.
- 🔴 **`<localleader>vs` IS TWO DIFFERENT ACTIONS BY BUFFER KIND** — `review_start` on
  `pull_request` (harmless) and `submit_review` on the review surfaces (posts to GitHub). Only
  `submit_review` is confirmed. The ledger is keyed on **(action, kind)**, not on the key, and a
  guard asserts `review_start` still carries that lhs so it cannot go vacuous by the key moving.
- 🔴 **A PLAUSIBLE MECHANISM THAT WAS NEVER MEASURED WAS WRONG THREE TIMES IN THIS ARC.**
  (a) `error()` was believed to make nvim refuse to start — it does not. (b) `E492` was predicted
  to overwrite the notify — it does not; messages accumulate, and the real killer is the
  `Press ENTER` wiping the message area. (c) an ERROR `vim.notify` was believed `pcall`-able
  inside a `luafile` — it is promoted to an uncatchable vim error. **Every one was caught by
  driving a real editor, none by reading.**
- 🔴 **A GUARD CAN CARRY THE PRODUCT CLAIM IN ITS NAME WHILE MEASURING ONE LEVEL BELOW IT.**
  `test_the_legend_seam_FAILS_LOUDLY_…` proved only that `error()` escapes `dofile` under luajit,
  not that the TUI refused to start. Round 0 found it by reading the name against the body.
- 🔴 **THE CI RED WAS A DIFFERENT EFFORT'S DOC, AND `main` WAS GREEN.** The branch predated
  `18a95e23`'s fix. **A red check is a claim about the BRANCH, not about the merged tree** — and
  here it cut the opposite way from the usual trap. Read the failing test's NAME, ask whether the
  diff can reach it, then test the MERGED tree.
- ⚠ **The `Press ENTER` prompt on a broken seam is neovim's and cannot be suppressed** while the
  `E492` happens. Removing it would mean redefining `Octo` as inert rather than deleting it, which
  breaks the structural half. One keypress on a failure that should never occur; accepted.
- ⚠ **`\pm` diverges from upstream's meaning for that key** — upstream declares it `merge_pr` =
  "merge **commit** PR"; this dispatches `squash`. Mitigated: the prompt says `method squash?` and
  the legend row says `merge this PR (squash)`. Upstream docs describe the KEY, not the METHOD.
- ⚠ **`scoped-tests.sh` refuses (exit 4) on any `nix/**` or `flake.nix` diff**, so every round of
  this arc ran hand-picked subsets. CI's advisory tiers are what covered the rest.
- ⚠ **Use `nix develop <the branch's worktree>`, not the base clone**, for anything on this
  subsystem: `luajit` joined `gateTools` in this PR, so a base clone predating it has no
  interpreter and the suite fails its own precondition.

- 🔴 **THIS DOC ASSERTED "DEPLOYED TO BOTH HOSTS" WHILE `ship.sh` WAS STILL RUNNING.** It was
  written from the merge, not from the deploy — the same shape as every other error this arc
  produced, and in the one document whose job is to be trusted next session. It happened to be
  true, which is worse than being caught: nothing in the doc distinguished a verified deploy from
  an expected one. **A handoff written mid-operation must say which claims are pending**, or the
  next reader inherits a prediction wearing the clothes of a measurement. The values above were
  added only after `ship.sh` returned rc=0 and the laptop's deployed artifact was probed.

## How to verify
```bash
# 🔴 RANK 1 — a NEW alacritty window is mandatory (the store path is baked into alacritty's
# config, which a running terminal already resolved).
#   click a `repo#N` -> press ?   -> legend headed with THIS buffer's kind and count
#   in a diff buffer -> press g?  -> same, and `?` still reverse-searches
#   press \pm        -> prompt names the PR, the repo and `squash`; anything but yes aborts

# The deployed wrapper carries the feature (run on the host you are testing)
NO=$(grep -oE '/nix/store/[a-z0-9]*-nvim-octo' "$(readlink -f ~/.config/alacritty/alacritty.toml \
  | xargs grep -oE '/nix/store/[a-z0-9]+-alacritty-mention-open' | head -1)" | head -1)
grep -c 'CONFIRMED_VERBS\|LEGEND_LHS' "$NO/bin/nvim-octo"   # non-zero

# Headless, no window: the legend and the confirmation, per kind
NVIM_APPNAME=nvim-octo-check "$NO/bin/nvim-octo" --help 2>/dev/null || true
# (drive octo-init's _G.NvimOcto directly with the wrapper's own nvim --headless; see the PR)

# What shipped, per host
git -C ~/workspace/devrc rev-parse --short HEAD
ssh zach@10.42.0.100 'git -C ~/workspace/devrc rev-parse --short HEAD'
```
