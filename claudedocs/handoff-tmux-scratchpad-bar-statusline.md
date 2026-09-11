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
- **SHIPPED, DEPLOYED AND VERIFIED LIVE on the workbench.** PR #1485 merged (squash `fe4460d6`); PR #1484 merged (squash `799d966c`). Both verified by CONTENT on `origin/main`, never by ancestry — a squash merge never makes the branch head an ancestor.
- Base clone on `main` at `fe4460d6`. `home-manager switch --flake ~/workspace/devrc --impure` → exit 0. Claim `tmux-scratchpad-bar-statusline-1` RELEASED.
- ⚠ **The laptop has NOT been shipped.** Only the workbench was switched. Run `LAPTOP_SSH=zach@10.42.0.100 scripts/ship.sh` (the laptop is nebula-only; a bare `ship.sh` dials its LAN IP and half-ships).
- **Verified live, by observation not inference:**
  - Bar legend **screenshotted on the real bar**: all 20 slots, per-slot colour, live slots `key+count` bold, absent slots (`p`,`n`,`M`) dim `#504945` with no count, positioned between the disk and net pills. This closes the round-2 caveat that pango rendering "needs the operator's real bar" — it renders, no tofu, no markup leakage.
  - `tmux show -g status-right` → `idle-update.sh` + continuum only; `%H:%M` and `#H` gone. `status-left` → session name only; `scratch-status.sh` gone. Continuum interpolation present **exactly once** after the reload.
  - Popups: `tmux list-keys -T root` → **0 at 80%**, 23 display-popup bindings at 90% width. The one remaining `-h "70%"` is the pre-existing fuzzyclaw popup, correctly untouched. Positive control confirmed the counting pattern CAN match 80%.
  - Bar restart confirmed by **PID change** 934021 → 1132952 (never by `pgrep -x`, which returns empty for the wrapped binary).
- **Audit ladder: 2 rounds, ended by operator intervention, NOT on a clean round.** Round 1 (full) found 1 🔴 + 4 🟡 + 4 🟢; round 2 (delta) found 5 🟡 + 4 🟢, all fixed. Round 3 was dispatched and **stopped by the operator**, so the round-3 fixes — the new `nix/programs/tmux/slot-table.nix` and its three replacement guards, the largest structural change in the PR — **shipped unaudited**. Nothing outstanding was deploy-blocking; round 2's verdict was safe-to-merge after 🟡1/🟡4, both fixed.
- ⚠ `drift-check.service` exits **17** (a package's `nix/pkgs/**` source subtree is behind on some host). Pre-existing, unrelated to this work, still open.

## Open investigations — live diagnosis state
_(none — this is a planned feature, not a bug investigation)_

## Next steps (ranked)
1. **Ship to the laptop**: `LAPTOP_SSH=zach@10.42.0.100 scripts/ship.sh`, then restart its bar. 🔴 Read every per-host line, not the final verdict — one skip hides among greens. The block is unconditional, so the laptop gets it too, and `TMUX_TMPDIR` was **never measured there**.
   forcing: none
2. **Click the bar legend once** and confirm the picker opens. It has still never been clicked from the real bar; the picker's terminal hold is pinned structurally only (stdin on `/dev/null` suppresses `read -p`, so the test proves the branch was taken, not that the terminal stayed open).
   forcing: none
3. **Resolve `drift-check.service` rc 17** — a package built from another repo's working tree is behind on a host. Pre-existing.
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

- 🔴 **CI caught a real defect that THREE audit rounds did not, and it was in the audits' own scaffolding.** `test_runtime_shebangs.py::test_no_test_writes_a_usr_bin_env_shebang_at_runtime` went red at `7c449cdd`: `test_scratchpads_block.py` wrote its own shebang inline. The fix round had already reasoned its way to the right *interpreter* (absolute bash, because the test's PATH jail holds only shims and `env` cannot resolve bash) but wrote it at the call site, which the guard forbids. Fixed by routing through `testlib.mockbin.write_exec`, which owns the shebang and rejects a body carrying one. **The audits were reading for defects; CI was reading for repo invariants — different questions.** Verify a fix like this with BOTH controls: the guard red-before/green-after, AND that the shims still RUN (the jail test asserts `seen == _PICKER_BINARIES`, which can only hold if every shim started and logged its own name — a shim that fails to start leaves that set empty).
- 🔴 **A `home-manager switch` by ANOTHER session killed a background monitor mid-run, and the failure mode was a busy-loop, not an error.** `~/.nix-profile` is blanked while a switch writes its two profile generations, so a bare-name `sleep` died `No such file or directory`; with no `set -e` the poll loop span on `gh`. MEASURED 2026-09-11: generations 2155→2156 at 11:50:17→11:50:48, a **~31 s window**, not the ~1 s the note in MEMORY describes. **Any long-running script must call binaries by absolute path out of `/run/current-system/sw/bin`** (the SYSTEM profile, which a user switch does not rewrite) and must refuse to continue if its sleep fails rather than spin. Correlate with `~/.local/state/nix/profiles/profile-*-link` mtimes to prove it.
- 🔴 **A `home-manager switch` does NOT update a RUNNING tmux server — the statusline is server state.** After the switch, `tmux show -g status-right` still returned the OLD value with `%H:%M` and `#H`. The deployed `~/.config/tmux/tmux.conf` was correct the whole time. `tmux source-file ~/.config/tmux/tmux.conf` applies it; **never kill the server** — that destroys every session. This is the "restart the consumer" step with tmux as the consumer, and it is easy to miss because every file-level check passes.
- ⚠ **`tmux list-keys` QUOTES the popup size flags** (`-h "90%" -w "90%"`), and re-orders them relative to the source. A grep for `-w 90%` unquoted matched **1 of 23** real bindings and would have read as "the change did not land". Count both spellings, and run a positive control proving the pattern can match the value you are claiming is absent.
- **`bash-guard.py` evaluates the branch statically, BEFORE the command runs.** A single call doing `git checkout -b X && … && git commit` is refused, because the guard sees `git commit` while the checkout is still on `main`. Split the checkout and the commit into separate calls. It also cannot resolve a shell variable in `git -C $W` and falls back to judging the caller's cwd — **pass `-C` a literal absolute path**.
- **`${PIPESTATUS[0]}` is empty in zsh** — it is `$pipestatus` (lowercase array). A `cmd | tail; echo "EXIT=${PIPESTATUS[0]}"` printed nothing and a `status=behind` refusal read as success until the repo was checked directly. Capture with `out=$(cmd 2>&1); rc=$?` instead.
- **`handoff_doc.py`'s Gotchas section is an APPEND bucket.** A follow-up run that re-sends the whole delta to correct one line in a REPLACE section duplicates every appended bullet (that is what PR #1484 cleaned up). **A correction run must omit the APPEND sections** — a section the delta omits is left untouched.

## How to verify
Post-deploy, all of these were run and passed on the workbench:
1. `readlink -f ~/.config/i3status-rust/scripts/{i3status-scratchpads,scratch-slots.sh}` → both resolve into `/nix/store` (they are `home.file` copies, so they change only on a switch).
2. `~/.config/i3status-rust/scripts/i3status-scratchpads` → valid JSON, `"state":"Idle"`, 20 `<span>` elements.
3. Bar restart: `pgrep -a i3status-rs` **before and after**, confirm the **PID changed**. 🔴 Never `pgrep -x` — home-manager wraps the binary so `comm` is `.i3status-rs-wr` and `-x` always returns empty.
4. `tmux source-file ~/.config/tmux/tmux.conf`, then `tmux show -g status-right` / `status-left`, and `tmux show -g status-right | grep -c continuum_save` → exactly 1.
5. `tmux list-keys -T root | grep -oE '\-[wh] "?[0-9]+%"?' | sort | uniq -c` → zero at 80%.
6. Screenshot the bar (`nix-shell -p maim --run "maim -u out.png"`, `DISPLAY=:0 XAUTHORITY=~/.Xauthority`) and LOOK at it — the legend sits between the disk and net pills. This is the only check that proves pango actually renders.
