# Handoff: tmux-scratchpad-bar-statusline — 2026-09-10

## Run this first — the index, one command
```bash
cairn recall --repo /home/zach/workspace/devrc
```

## Goal
Two UX improvements to the scratchpad and tmux statusline:
1. Make the named, colored scratchpad popup hotkeys larger (90%×90% instead of 80%×80%)
2. Remove time/host from tmux status-right, and move the scratchpad status legend (currently in tmux status-left) to the i3status-rust bar — with the full 20-slot list since there's more space there

## State now
- Base clone on `main`, in sync with `origin/main`. 🔴 **This doc's own commit `e9829cc0` landed DIRECTLY ON `main`, against devrc's CLAUDE.md rule, because the checkout was switched out from under this session.** Reflog: `HEAD@{2}` main→`docs/handoff-scratchpad-bar-dispatch` (this session), `HEAD@{3}` a fast-forward `972fbcbd`→`05985792`, then `HEAD@{1}` `docs/…`→main — **that last checkout was another session or a subagent, not this one**. The branch was created and verified, then two proposal runs and two edits happened, and by the confirm-write the branch was gone. The rule that would have caught it is `git branch --show-current` **immediately before the write**, not at branch creation. Mitigation: local `main` == `origin/main`, so the diverged-host/`ship.sh`-skip hazard the rule exists to prevent is NOT present; the empty branch was deleted. Left as-is rather than reverting a pushed docs commit.
- **Rank 1 is CLAIMED**: `claim-work tmux-scratchpad-bar-statusline-1` — "implement 5-file scratchpad popup 90% + bar legend block plan". Release it with `claim-work --release tmux-scratchpad-bar-statusline-1` when the PR lands or the work is abandoned.
- Swept `gh pr list --state open` (30 PRs): **nothing duplicates this work**.
- **IN FLIGHT — implementation dispatched to an isolated-worktree subagent, NOT yet returned.**
  - Worktree: `/home/zach/workspace/devrc/.claude/worktrees/agent-a24e431e3068fb1d1`
  - Branch: `feat/scratchpad-bar-legend-90pct` (at base `972fbcbd`, **no commits yet** as of this write)
  - No PR yet. If the agent is gone when you resume, check `git log origin/feat/scratchpad-bar-legend-90pct` and `gh pr list --head feat/scratchpad-bar-legend-90pct` before re-doing anything.
- Deploy status: **nothing deployed, nothing switched.** Deliberate — see the restart-class gotcha below.
- No clawgate task recorded: `clawgate_handoff.sh resolve` exited **5 (NOTHING RESOLVED)**. Its positive control proved the board is reachable and the token accepted, but an unknown session id also answers `200` with an empty array — so this is **not** a clean bill of health, and no `clawgate-task:` field was written.

### The plan being implemented (6 files, 2 new) — carried forward until the PR lands
Corrections to the original 5-file plan are in Gotchas below; this is the corrected version.
1. **`nix/programs/tmux/default.nix`** — `mkBind` (~line 24): `-w 80% -h 80%` → `90%`, regenerating all 20 popup bindings.
2. **`.tmux.conf`** — four edits: line ~89 (`M-T` scratch-picker popup) `80%`→`90%`; line ~104 (`M-[` most-recent-scratch popup) `80%`→`90%`; line ~186 drop `#(~/.config/tmux/scratch-status.sh)` from `status-left`; line ~193 drop `#[fg=#ebdbb2]%H:%M #[fg=#282828,bg=#83a598] #H ` from `status-right`. 🔴 line ~66 (`fzf-tmux -p 80%,50%`) is OUT of scope.
3. **`scripts/i3status-scratchpads`** (NEW) — Python bar block: parses `SCRATCH_SLOTS` from the slot table by explicit path (3 legs, like `i3status-claude-runs`), queries `tmux list-sessions -F '#{session_name} #{session_windows}'`, renders all 20 slots as `<key><count>` in slot colour + bold via pango `<span>`, dim `#504945` and no count for absent ones, `state` always `Idle`. Cannot-measure renders `scratch ?`, never an empty legend.
4. **`nix/graphical.nix`** — `scratchpadsBlock` (custom, `json = true`, `interval = 30`, no signal), inserted after `diskBlock`; a `scratchPickerCmd` float-terminal click; **two** unconditional `home.file` entries (the script, and the slot table as the sibling `scratch-slots.sh`).
5. **`scripts/tests/test_scratchpads_block.py`** (NEW) — fixture-based unit tests, including the discriminant cases and a well-formed-pango assertion.
6. **Comment corrections** in `scripts/tmux-scratch-status.sh` and `scripts/tmux-scratch-slots.sh` — both describe the former as "the status-left legend", which this change makes false.

## Open investigations — live diagnosis state
_(none — this is a planned feature, not a bug investigation)_

## Next steps (ranked)
1. **Receive and review the subagent's PR** for `feat/scratchpad-bar-legend-90pct` (repo `devrc`; files: `scripts/i3status-scratchpads` NEW, `scripts/tests/test_scratchpads_block.py` NEW, `nix/graphical.nix`, `nix/programs/tmux/default.nix`, `.tmux.conf`, `scripts/tmux-scratch-slots.sh`, `scripts/tmux-scratch-status.sh`). Read its reported mutation matrix and pango positive-control output rather than its "done" claim.
   forcing: user — operator's kickoff message this session asked for this implementation.
2. **Deploy and verify end-to-end** once merged. `home-manager switch --flake ~/workspace/devrc --impure` (or `LAPTOP_SSH=zach@10.42.0.100 scripts/ship.sh` for both hosts), **then restart the bar** — see the restart-class gotcha. Verify: the legend appears after the disk pill with all 20 slots; tmux `status-right` no longer shows time/host; a scratchpad popup is 90%×90%.
   forcing: none
3. **Decide whether 20 slots fit the laptop bar.** The block is unconditional on both hosts; ~50 chars of legend is comfortable on the workbench but the laptop bar is narrower. If it crowds, either gate it `!isLaptop` or drop back to the tmux script's slots-7-onward slice.
   forcing: none

## Gotchas / decisions / dead-ends
- The scratch-status.sh script (`scripts/tmux-scratch-status.sh`) is NOT deleted — it's just no longer called from the tmux statusline. It may still be useful for debugging or as a fallback.
- The bar block is unconditional (both hosts) because scratchpads exist on both hosts, unlike the poller-backed count blocks which are workbench-only.
- No signal needed — the block reads local tmux state directly, no poller/cache. Same pattern as `i3status-claude-runs`.
- Left-click on the bar legend opens `scratch-picker.sh` in a float terminal.
- The continuum-save interpolation (`set -ag status-right`) in `nix/programs/tmux/default.nix` must remain AFTER the simplified status-right — it appends and survives reloads, so the ordering is still correct.

- 🔴 **The plan's load-bearing assumption was unstated and is now MEASURED: per-slot colour inside one bar block works via pango markup.** Nothing in `nix/graphical.nix` or any `scripts/i3status-*` used pango before this, so it was worth proving before building on it. `i3status-rs --version` = **0.36.1**; the man page gives the `custom` block's default format as `"{ $icon|} $text.pango-str() "`. Positive control: a throwaway TOML + command script emitting `{"text":"<span foreground=\"#b8bb26\">g2</span> <span foreground=\"#504945\">G</span>","state":"Idle"}`, run under `timeout 6 i3status-rs <cfg>`, produced the i3bar line
  `{"full_text":" <span foreground=\"#b8bb26\">g2</span> <span foreground=\"#504945\">G</span> ","color":"#93A1A1FF",…,"markup":"pango"}`
  — `markup: "pango"` set, spans passed through **unescaped**. **Corollary: the text IS markup, so any literal `<`/`&`/`>` must be `html.escape`d.**
- **The plan said one `home.file` entry; it needs TWO.** The block script resolves the slot table from its own directory, so `scripts/tmux-scratch-slots.sh` must ALSO be symlinked to `~/.config/i3status-rust/scripts/scratch-slots.sh` — the same sibling-module pattern `claude_sessions.py` uses for `i3status-claude-runs` (`nix/graphical.nix` ~:698). Without it the script's leg-1 lookup misses on a live host.
- **The plan named `.tmux.conf:89` only; `.tmux.conf:104` is the same class of popup.** Line 104 is the `Alt+[` "toggle most recent scratch-\*" popup, also `80% 80%`. Lazygit at line 112 is already `90% 90%`, so 90% is already the house idiom. Deliberately widened to include 104. 🔴 **`.tmux.conf:66` (`fzf-tmux -p 80%,50%`) is NOT in scope** — that is the window switcher, a different feature.
- **The bar click needs a float terminal, not a bare command.** `scratch-picker.sh` is an fzf TUI, and i3status-rust runs clicks through `sh -c` with **no controlling terminal** (the live `i3status-rs` has TTY `?`) — a bare TUI there exits `inappropriate ioctl for device` and the click is a **silent no-op**. Model the click on `syshealthCmd` (`nix/graphical.nix` ~:88): `alacritty --class float,float -o window.dimensions.columns=… -e …`, and interpolate `${home}`, never a literal `~`.
- 🔴 **Deploying this is RESTART-class, not reload-class** (from the cairn `nix` entry, 2026-09-08): i3status-rs reads its TOML **once at startup**, so `ship.sh` + `i3-msg reload` leaves the new block absent on a host reporting a fully successful deploy. A bar/WM restart takes the operator's screen, so it was deliberately left undone — hand it over, do not run it from an agent.
- 🔴 **`pgrep -x i3status-rs` returns EMPTY while the bar is running** — home-manager wraps the binary so the kernel `comm` is `.i3status-rs-wr`, and `-x` matches comm exactly. That zero is a fact about the instrument. Use `pgrep -a i3status-rs`; confirm a restart by the **PID changing** plus `ps -o etimes=`, never by an `-x` count.
- **`scripts/tmux-scratch-status.sh` is NOT deleted** — it stops being called from `status-left` but is kept as a fallback/debug renderer. Its header comment and the `Consumers` list in `scripts/tmux-scratch-slots.sh` both described it as "the status-left legend", which the change makes false; both were in scope to correct. A comment is a claim.
- The continuum autosave interpolation (`set -ag status-right`, appended by `nix/programs/tmux/default.nix`) must stay **after** the plain `set -g status-right`. Editing status-right is safe; introducing a *new* plain `set -g status-right` after it is not. `scripts/tests/test_tmux_continuum_save_interpolation.py` pins this.
- ⚠ On this host `grep` is a function wrapping ugrep and honours `.gitignore` — use `command grep -r` when sweeping for references, or a zero is a claim about grep's view rather than the tree.

## How to verify
1. `nix-instantiate --parse nix/graphical.nix >/dev/null` and `nix-instantiate --parse nix/programs/tmux/default.nix >/dev/null` — syntax only, not sufficient alone.
2. `nix eval --impure --raw ~/workspace/devrc#homeConfigurations.zach.config.home.file.".config/i3status-rust/scripts/i3status-scratchpads".source` — proves the new `home.file` entry actually evaluates (a `--parse` pass does not).
3. `python3 scripts/i3status-scratchpads` — must print valid JSON with `"state":"Idle"`.
4. **Block-level positive control** (the one that matters): throwaway TOML with `command` pointed at the script, `timeout 6 i3status-rs <cfg> >out 2>err`; confirm the emitted i3bar line carries `"markup":"pango"` and the per-slot spans. `rc=124` from `timeout` is expected, not a failure.
5. `nix develop ~/workspace/devrc -c python3 -m pytest scripts/tests/test_scratchpads_block.py scripts/tests/test_claude_runs_block.py scripts/tests/test_tmux_continuum_save_interpolation.py scripts/tests/test_doc_path_rot.py -q`, then `scripts/scoped-tests.sh` (read its `SCOPE:` line; **it is not a gate** and `gate.sh` exits 91 = PARTIAL off anything that is not `SCOPE: FULL`).
6. After deploy: bar shows the 20-slot legend after the disk pill; `tmux show -g status-right` has no `%H:%M`/`#H`; `tmux show -g status-left` has no `scratch-status.sh`; pressing `Alt+g` opens a popup filling 90%×90%.
