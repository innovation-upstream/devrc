# devrc-dx — known issues / gotchas

- `hm-session-vars.sh` is sourced per-shell via `.zshrc`, causing PATH entries from
  `home.sessionPath` to duplicate in nested shells. Not a regression (same issue existed
  with the old manual PATH append). Would require moving sourcing to `.zprofile` to fix.
- `/etc/nixos/` requires sudo — apply scripts in `nix/system/` use `cp` for complete file
  replacements and `sed -i` only for single-line changes. Prefer `cp` over `sed` for
  multiline modifications to avoid the kind of partial-delete bugs that sed can cause.
- The i3 config is a nix string (`''...''`) in `nix/i3/config.nix`, not home-manager's
  `xsession.windowManager.i3.config`. This means no nix store path interpolation in
  the i3 config — tool paths must be in PATH, not referenced by store path.
- `automatic-rename-format '#{b:pane_current_path}#{?#{m:claude*,...}, ●,}'` shows directory
  basename with ● when claude is running, and tracks cwd live. TUI apps that set the
  terminal title will override it (but `allow-rename off` blocks most of those).
- tmux config reload (`tmux source-file`) is required after `home-manager switch` for
  changes to take effect in existing sessions. New sessions pick up changes automatically.
- Task hooks must NEVER call `tmux rename-window` — it permanently disables per-window
  automatic-rename and freezes the tab against cd. This was the old behavior (emoji prefix
  ⏸/🔄/✅ in the name) and caused stale frozen tabs; it was removed in favor of JSON-only
  status. If tabs ever freeze again, the culprit is a stray rename-window; un-stick existing
  windows by iterating them with `set-window-option -t <win> automatic-rename on`.
- `display-popup -s style` sets the popup BODY style (all content inside the popup
  renders with that fg/bg). For border-only color, use `-S border-style` (capital S).
  Mixing them up causes every character in the popup to render in the slot color —
  was a real footgun on the scratch bindings before being corrected. The global
  `popup-border-style` is overridden by `-S` per-popup.
- Picom compositor is disabled — conflicts with NVIDIA `forceFullCompositionPipeline`,
  causing window and status-bar flicker on workspace switching.
- Multi-byte UTF-8 emoji (🔄⏸✅) must use `sed -E` alternation `(🔄|⏸|✅)` not
  character classes `[🔄⏸✅]` which fail on multi-byte sequences. (Now mostly vestigial:
  hooks no longer put emoji in window names, so the strip in `tmux-task-hook.sh` is a
  no-op — kept only for backward compat with any lingering pre-migration names.)
- Task file `task` field may contain stale values like `●` from before the name extraction
  fix. The hook now falls back to directory basename when stored task is empty or `●`.
- Status emoji (⏸/🔄/✅/●) lives ONLY in `~/.tmux/tasks/<wid>.json` `status`, read by the
  dashboard (`StatusIndicator` ← `Task.Status`), `fuzzyclaw status`, the status-right
  counters, the scratch-status `●` flag, and the initiatives view. There is no second
  copy in the window name. `resume` flips status `paused→running`; `stop` sets `paused`;
  `notification` (permission prompt) sets `waiting`; `session-end` sets `done`.
- Fuzzyclaw hook scripts (`task-hook.sh`, `task-resume.sh`) try `exec fuzzyclaw hook` first,
  falling back to inline bash logic if the binary isn't in PATH. Both paths are backward-compatible.
- Task status is JSON-only: hooks write `status` to `~/.tmux/tasks/<wid>.json` and never
  rename the window. The tab name is left to tmux `automatic-rename` so it tracks cwd.
  Status emoji is NOT in the window name.
- 🔴 **A client repo's browser tests reporting `Tests no tests` / 0-of-N on this host is
  PLAYWRIGHT LINE SKEW, and the cure is ALREADY BUILT — run `scripts/playwright-nixos`
  rather than hand-rolling an env override.** `PLAYWRIGHT_BROWSERS_PATH` is a deliberate
  GLOBAL, SINGLE-VERSION default on the flake's current `playwright-driver`, so a project
  pinned to another line resolves zero test files instead of erroring usefully.
  `scripts/playwright-nixos` reads the project's OWN installed playwright, selects the
  matching `playwright-driver-<M>_<m>` bundle, and asserts the chromium revision; its
  `--select` prints the whole decision. **Verified 2026-10-01** against `civitai/civitai`
  (pins playwright 1.57.0 → chromium-1200, against a default bundle on chromium-1228): its
  `geometry` tier ran **0** files under the default and **89/89** through the wrapper.
  🔴 **Two wrong fixes, and BOTH report success**, so neither is self-correcting: symlinking
  the default bundle under a `chromium_headless_shell-<other-rev>` directory name, and
  `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` aimed at that same binary. Each runs a *different*
  Chromium build than the project's pin selects, and the second is worse than it looks —
  it bypasses the revision assertion that is the entire value of the wrapper. Both were
  used in one session before the wrapper was found; the suite verdicts happened to match,
  which is luck, not validation.
  The mechanism, the 1:1 version→build mapping and the driver-registry design are already
  documented in `nix/sessionVariables.nix`, `claudedocs/playwright-nixos.md` and the
  script's own header — deliberately not restated here. **This entry exists for ONE reason:
  none of those is reachable from the client repo where the failure is actually hit, and
  `playwright-nixos` is not on `PATH`, so the failure gets re-diagnosed from scratch.**
