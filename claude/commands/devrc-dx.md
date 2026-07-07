---
name: devrc-dx
description: "Analyze and improve developer experience across NixOS, home-manager, i3, tmux, and shell configuration"
---

# /devrc-dx - Developer Experience Analyzer

## Triggers
- Requests to analyze or improve desktop/terminal/shell configuration
- Questions about keybindings, theming, or workflow consistency
- Performance issues with status bars, compositors, or shell startup
- Requests to audit config for dead code, duplication, or drift

## Usage
```
/devrc-dx [scope] [action]

Scopes:
  all              Full stack analysis (default)
  tmux             Tmux config, plugins, scripts, status bar, task management
  i3               i3 config, i3blocks, keybindings, bar
  shell            Zsh, bash, direnv, session variables, PATH
  theme            Gruvbox consistency across all components
  nix              Home-manager and NixOS config hygiene
  tasks            Claude Code task management system (hooks, dashboard, state)

Actions:
  analyze          Analyze and report issues (default)
  fix              Analyze then implement all fixes
  verify           Run all verification checks on current state
  audit            Lightweight check for common issues only
```

## Behavioral Flow

### 1. Discover Configuration
Read all config sources — do NOT assume locations, discover them:

**Home-manager (devrc repo at ~/workspace/devrc):**
- `nix/home.nix` — services (espanso, dunst), packages, session variables, sessionPath, file symlinks
- `nix/programs/` — per-program configs (tmux, zsh, git, alacritty, direnv, fzf, ranger, bash, neovim)
- `nix/pkgs/` — language tooling (lang/) and system packages (tools/)
- `nix/sessionVariables.nix` — FZF, editor, LSP, Playwright env vars
- `nix/system/` — staged NixOS config changes + apply scripts
- `.tmux.conf` — extra tmux config loaded via `builtins.readFile` in tmux/default.nix
- `.zshrc` — extra zsh config loaded via `builtins.readFile` in zsh/default.nix
- `scripts/` — tmux scripts (task-hook, task-resume, activity-receiver — thin wrappers over fuzzyclaw; scratch-picker, scratch-status, scratch-monitor, claude-counters, initiatives), i3blocks-*, dictation
- `nix/pkgs/tools/tmux-fuzzyclaw.nix` — Nix buildGoModule package for fuzzyclaw Go binary

**Claude Code task management system (upstream: [ZacxDev/tmux-fuzzyclaw](https://github.com/ZacxDev/tmux-fuzzyclaw)):**
- `fuzzyclaw` — Go binary (Bubble Tea TUI) replacing bash dashboard/idle/pipe scripts
  - `fuzzyclaw dashboard` — Alt+F TUI: live table, preview pane, two-pass search, multi-select
  - `fuzzyclaw idle-update` — batch window color update (replaces idle-update.sh)
  - `fuzzyclaw pipe <start|stop|switch|linked|init>` — pipe-pane management (replaces pipe-activity.sh)
  - `fuzzyclaw hook <stop|resume>` — hook handler (replaces most of task-hook.sh/task-resume.sh logic)
  - `fuzzyclaw status` — one-line output for tmux status-right
  - `fuzzyclaw search <query>` — CLI global search across conversation history
  - `fuzzyclaw export <cwd>` — markdown session export
- `scripts/tmux-task-hook.sh` — Thin wrapper: `exec fuzzyclaw hook stop` with bash fallback
- `scripts/tmux-task-resume.sh` — Thin wrapper: `exec fuzzyclaw hook resume` with bash fallback
- `scripts/tmux-activity-receiver.sh` — Kept as bash (pipe-pane stdin constraint)
- `~/.claude/settings.json` — Hook configuration (PreToolUse, Stop, PermissionRequest)
- `~/.tmux/tasks/*.json` — Task state files keyed by tmux window ID
- For fuzzyclaw-specific development/debugging, use `/fuzzyclaw` skill in the tmux-fuzzyclaw repo

**System (NixOS at /etc/nixos/):**
- `configuration.nix` — system packages, services, i3, PipeWire audio, NVIDIA GPU, k3s, networking
- `i3config.nix` — i3 keybindings, launcher, bar config (nix string returning i3 config)
- `i3blocks.nix` — status bar block definitions (nix string returning i3blocks INI)
- `i3blocks-scripts/` — compiled/shell status bar scripts (bandwidth, battery, calendar, cpu_usage, etc.)

### 2. Analyze Each Layer
For each scope, check for these categories of issues:

**Consistency:**
- Keybinding style (vim hjkl vs arrow vs mixed) across i3, tmux, neovim
- Theme colors (Gruvbox palette) across tmux status, i3bar, dunst, alacritty
- Naming conventions across nix files
- Status emoji conventions: 🔄 (active), ⏸ (paused), ✅ (done), ● (claude running)

**Performance:**
- Shell commands running per-pane that should run once (xset, PATH, env vars)
- Status bar scripts forking excessively (per-window vs batch)
- Dashboard render time (target: <50ms for 50 windows with Bubble Tea)
- Hook execution time (PreToolUse fires on every tool call — must be fast)
- Two-pass search: instant substring on cached fields + async batch ripgrep deep scan (~66ms for 1.2GB)
- Pre-caching patterns: per-cwd not per-window for JSONL extraction
- Single batch `rg -i -l --max-count=1 --glob=*.jsonl` across all cwds for search

**Correctness:**
- Dead references (signals to wrong process, unused packages)
- Hardcoded paths that should use nix variables (`config.home.homeDirectory`)
- Duplicate config (direnv hooks, bell handlers, PATH entries)
- Commented-out code that should be removed or restored
- Task status is JSON-only: hooks write `status` to `~/.tmux/tasks/<wid>.json` and never rename the window. The tab name is left to tmux `automatic-rename` so it tracks cwd. Status emoji is NOT in the window name.
- Task name fallback: strip `●` indicator, fall back to directory basename
- Auto-rename must stay ON: nothing in the hook path may call `tmux rename-window` — it permanently disables per-window automatic-rename and freezes the tab against cd

**Completeness:**
- Missing keybindings for installed tools (flameshot, i3lock, rofi, etc.)
- Missing notification daemon or screen lock
- Missing `focus-events`, `automatic-rename`, or other quality-of-life settings
- Missing `xss-lock` for suspend/idle lock protection

**Security:**
- No screen lock on suspend/idle
- Unprotected secrets in config files

### 3. Report Findings
Group by impact level:

- **High Impact** — Workflow gaps, incorrect behavior, performance problems
- **Medium Impact** — Inconsistencies, duplication, missing polish
- **Low Impact** — Style, minor optimizations, nice-to-haves

For each finding, include:
- What's wrong and why it matters
- Which file(s) are affected
- Concrete fix (code snippet or description)

### 4. Implement Fixes (if `fix` action)
- Edit devrc files directly (home-manager scope)
- For `/etc/nixos/` changes: stage files in `nix/system/` with an apply script
  (no sudo available — write to devrc, user applies with `sudo bash nix/system/apply-*.sh`)
- IMPORTANT: sed-based apply scripts are fragile. For complex multiline changes,
  write the complete replacement file to `nix/system/` and `cp` it in the apply script
  rather than using sed to surgically edit configuration.nix
- Commit with descriptive message
- Remind user to run `home-manager switch` and/or `sudo nixos-rebuild switch`

### 5. Verify (if `verify` action)
Prefer the one-shot block below — it runs every check in a single bash call and prints
`PASS`/`FAIL` per line, so you don't fan out into 20 separate tool round-trips. Fall back to
the per-category snippets only when a check FAILs and you need to investigate why.

```bash
ok(){ printf '%-42s %s\n' "$1" "$([ "$2" = 0 ] && echo PASS || echo FAIL)"; }
# tmux
tmux show-option -g focus-events | grep -q on; ok "focus-events on" $?
tmux list-keys | grep -q fuzzyclaw; ok "Alt+F fuzzyclaw bound" $?
tmux list-keys -T root | grep -Eq "M-[gGvVpP]"; ok "6 scratch slot bindings" $?
# fuzzyclaw + hooks
command -v fuzzyclaw >/dev/null; ok "fuzzyclaw in PATH" $?
jq -e '.hooks' ~/.claude/settings.json >/dev/null 2>&1; ok "claude hooks configured" $?
# desktop
pgrep -x dunst >/dev/null; ok "dunst running" $?
! pgrep -x picom >/dev/null; ok "picom NOT running (NVIDIA)" $?
command -v rofi >/dev/null; ok "rofi installed" $?
# shell
grep -q CDPATH ~/.config/zsh/.zshrc 2>/dev/null; ok "CDPATH set" $?
# config deploy
grep -q "duration = 0" ~/.config/alacritty/alacritty.toml 2>/dev/null; ok "alacritty bell off" $?
```

Then report the table. For deeper inspection of any FAIL, use the per-category snippets below.

**Tmux checks:**
```bash
tmux show-option -g automatic-rename-format   # should include ● indicator
tmux show-option -g focus-events              # should be on
tmux show-option -g status-right              # should contain idle-update.sh
tmux show-option -g status-left               # should call scratch-status.sh
tmux list-keys | grep fuzzyclaw               # Alt+F binding (the dashboard)
tmux list-keys | grep "prefix.*/"             # fuzzy search binding
tmux list-keys -T root | grep -E "M-[gGvVpP]" # 6 scratch slot bindings, must use -S not -s
~/.config/tmux/scratch-status.sh              # render the 6-slot indicator (should run <10ms)
ls ~/.tmux/activity/.prev_*                   # pipe switch state tracking
```

**Task management checks:**
```bash
# Fuzzyclaw binary
which fuzzyclaw                               # should be in PATH
fuzzyclaw version                             # verify build

# Hook configuration
jq '.hooks' ~/.claude/settings.json           # PreToolUse + Stop hooks
ls -la ~/.config/tmux/task-{hook,resume}.sh   # symlinks exist

# Hook behavior (via fuzzyclaw) — writes JSON status, does NOT rename the window
WID=$(tmux display-message -p '#{window_id}')
echo '{"session_id":"test","stop_hook_active":false,"last_assistant_message":"test"}' | \
  TMUX_PANE=$(tmux display-message -p '#{pane_id}') fuzzyclaw hook stop
jq -r '.status' ~/.tmux/tasks/${WID//[@%]/}.json   # should print: paused
tmux display-message -p '#{automatic-rename}'      # should still be 1 (tab tracks cwd)
rm -f ~/.tmux/tasks/${WID//[@%]/}.json             # clean up test artifact

# Fuzzyclaw subcommands
fuzzyclaw status                              # one-line summary
fuzzyclaw idle-update                         # batch window colors
fuzzyclaw search "test query"                 # CLI search

# Dashboard (launch in popup)
tmux display-popup -E -w 90% -h 70% 'fuzzyclaw dashboard'

# Task state
ls ~/.tmux/tasks/                             # JSON state files
jq . ~/.tmux/tasks/*.json | head -20          # verify structure
```

**Desktop checks:**
```bash
pgrep dunst && echo "dunst: running"
pgrep xss-lock && echo "xss-lock: running" || systemctl --user status xss-lock
which rofi && echo "rofi: installed"
# picom should NOT be running (conflicts with NVIDIA forceFullCompositionPipeline)
pgrep picom && echo "WARNING: picom running (causes flicker with NVIDIA)"
```

**i3 checks (after nixos-rebuild + i3 reload):**
```bash
grep 'rofi\|flameshot gui\|Shift+x.*i3lock\|+5%\|Shift+h move' /etc/i3.conf
```

**Shell checks:**
```bash
grep -c "_direnv_hook" ~/.config/zsh/.zshrc        # should be 0
grep "CDPATH" ~/.config/zsh/.zshrc                 # should include civit
grep "exec zsh" ~/.bashrc                          # should have exec
cat ~/.nix-profile/etc/profile.d/hm-session-vars.sh | grep PATH  # sessionPath entries
```

**Config deployment checks:**
```bash
grep "duration = 0" ~/.config/alacritty/alacritty.toml   # bell disabled
find ~/.config/espanso -name "*.yml" | xargs grep "mtfc"  # should find nothing
```

Report results as a table with Pass/Fail/Pending status.

## Config Stack Reference

### Theme: Gruvbox Dark
| Element | Color | Hex |
|---------|-------|-----|
| Background | Dark | `#282828` |
| Foreground | Light | `#ebdbb2` |
| Border/subtle | Gray | `#504945` |
| Accent/active | Cyan | `#83a598` |
| Warning/prefix | Yellow | `#d79921` |
| Error/critical | Red | `#cc241d` |
| Success/fresh | Green | `#b8bb26` |
| Dim green | Active | `#98971a` |
| Aqua | Warm | `#689d6a` |
| Orange | Idle | `#d65d0e` |
| Info/special | Purple | `#b16286` |
| Ancient/gray | Dormant | `#665c54` |

### Key Conventions
| Context | Modifier | Style |
|---------|----------|-------|
| i3 focus | Alt+hjkl | vim-style |
| i3 move | Alt+Shift+hjkl | vim-style |
| i3 resize | hjkl (in mode) | vim-style |
| i3 launcher | Alt+D | rofi -show drun |
| i3 lock | Alt+Shift+X | i3lock -c 282828 |
| i3 screenshot | Print / Alt+Print | flameshot gui / full |
| tmux prefix | Ctrl-A | |
| tmux panes | prefix+hjkl | vim-style |
| tmux windows | prefix+n/p (repeatable) | 800ms repeat |
| tmux windows 10+ | prefix+e/r/t/y/u/i/o | windows 10-16 |
| tmux search | prefix+/ | fzf all windows by path |
| tmux fuzzyclaw TUI | Alt+F | Go Bubble Tea dashboard: live table, search, preview, multi-select |
| tmux copy mode | vi keys | Ctrl+hjkl for fast nav |
| tmux scratch slots | Alt+g/G/v/V/p/P | 6 persistent popups (see Scratch Slots) |
| tmux lazygit | prefix+g | 90% popup |
| tmux k9s | prefix+K | 95% popup |

### Scratch Slots
Six persistent popup sessions, each with a color-themed border and a memorable title
(name starts with the hotkey letter and evokes the slot's color). The status-left
indicator (`scripts/tmux-scratch-status.sh`) shows all 6 slots as their hotkey
letter colored to match the slot's popup border; dimmed when the session doesn't
exist yet. A leading `●` (in slot color) marks any scratch whose windows include
one in fuzzyclaw `status="waiting"` (permission prompt or other input request),
filtered against currently-existing tmux window IDs so stale state files don't
flag dead windows. Output: `g G v V p P` becomes `g ●G v V p P` when scratch2
has a waiting prompt.

| Key | Session | Title | Color | Hex |
|-----|---------|-------|-------|-----|
| Alt+g       | scratch  | grove  | green  | #b8bb26 |
| Alt+Shift+G | scratch2 | Gold   | yellow | #d79921 |
| Alt+v       | scratch3 | violet | purple | #b16286 |
| Alt+Shift+V | scratch4 | Vapor  | cyan   | #83a598 |
| Alt+p       | scratch5 | poppy  | red    | #cc241d |
| Alt+Shift+P | scratch6 | Pool   | aqua   | #689d6a |

Toggle (no prefix): hotkey opens the popup if not focused, detaches if currently inside.
Internal session names stay `scratchN` so the `bind-key s` choose-tree filter
(`scratch*` glob) and `scripts/tmux-scratch-picker.sh` (M-T) keep working.
Border color set via `display-popup -S 'fg=COLOR'` — NOT `-s` (see Gotchas).
Title is the `-T ' name '` argument (renders at the top of the rounded border).

**Monitor popup (Alt+m):** `scripts/tmux-scratch-monitor.sh` is a live HUD showing
the last few lines from all 6 scratches at once (auto-refresh every 2s, dismiss
with q/Esc). Each section has a colored header in its slot color and a line
count that adapts to popup height. Strips Claude's input-box chrome (the two
───── separators wrapping the input prompt) so the visible content is
conversation/progress, not the model+ctx status bar. Use for monitoring
parallel Claude sessions without cycling through scratch hotkeys. Like the
slot hotkeys, M-m detaches first if pressed inside a scratch so popups don't nest.

**Aggregate counters in status-right:** `scripts/tmux-claude-counters.sh`
renders `N🔄 N⏸ N●` (running / paused / waiting Claude windows across all
sessions) at the left of status-right. Each segment dim-grays when zero. Pairs
with the per-slot `●` flag in status-left: the slot legend tells you *which*
scratch needs attention; the counter tells you the *magnitude* of work in flight.

**agent-ops dashboard ($mod+i):** `scripts/agent-ops` is the read-only
"mission-control" dashboard — real open PRs (`gh pr list` per repo, TTL-cached),
live agent runs (each row = the pane's actual task from its title + scratch
codename + a busy marker), momentum/next-step + recently-merged (initiative-scan),
and health (bar-status caches). Launched from i3 as a floating alacritty
(`class="float"`). It replaced the old fuzzyclaw-summary `tmux-initiatives.sh`
Alt+i HUD. Pairs with the monitor popup: M-m shows *what's happening right now*
(live capture-pane); agent-ops shows *what each session is working on*.

### Task Management System
| Component | File | Purpose |
|-----------|------|---------|
| Fuzzyclaw binary | `~/workspace/tmux-fuzzyclaw/` → `fuzzyclaw` | Go Bubble Tea TUI, CLI subcommands |
| Nix package | `nix/pkgs/tools/tmux-fuzzyclaw.nix` | `buildGoModule` with vendored deps |
| Stop hook | `scripts/tmux-task-hook.sh` | Thin wrapper → `exec fuzzyclaw hook stop` (bash fallback) |
| Resume hook | `scripts/tmux-task-resume.sh` | Thin wrapper → `exec fuzzyclaw hook resume` (bash fallback) |
| Activity receiver | `scripts/tmux-activity-receiver.sh` | Kept as bash (pipe-pane stdin constraint) |
| Dashboard | `fuzzyclaw dashboard` | Alt+F Bubble Tea TUI: live table, preview, two-pass search |
| Idle updater | `fuzzyclaw idle-update` | Batch window color update via tmux status-right |
| Pipe manager | `fuzzyclaw pipe` | pipe-pane start/stop/switch/linked/init |
| Auto-rename | `.tmux.conf` | `#{b:pane_current_path}#{?#{m:claude*,...}, ●,}` — tab = basename + ● when claude runs; tracks cwd, NOT touched by hooks |
| Hook config | `~/.claude/settings.json` | PreToolUse → task-resume.sh, Stop → task-hook.sh |
| State files | `~/.tmux/tasks/<wid>.json` | task, status, cwd, summary, claude_session, timestamps |

**Dashboard:** `fuzzyclaw dashboard` (Alt+F) is the Bubble Tea TUI — the only bound dashboard.
Feature lists, controls, data flow, and performance targets are documented in the
`~/workspace/tmux-fuzzyclaw` repo, not duplicated here. From the devrc side, what matters:
hooks call the thin wrappers in `scripts/`, the binary is packaged via
`nix/pkgs/tools/tmux-fuzzyclaw.nix`, and hook config lives in `~/.claude/settings.json`.
The old fzf popup (`scripts/tmux-task-dashboard.sh`, formerly Alt+c) is **no longer bound** —
dead code still symlinked by `home.nix`; safe to remove when convenient.

### File Ownership
| Scope | Managed By | Location |
|-------|-----------|----------|
| Shell, editor, tmux, git | home-manager | ~/workspace/devrc |
| Dunst, espanso | home-manager (services) | ~/workspace/devrc/nix/home.nix |
| Fuzzyclaw binary | home-manager (nix buildGoModule) | ~/workspace/tmux-fuzzyclaw/ → PATH |
| Task scripts | home-manager (file symlinks) | ~/workspace/devrc/scripts/ → ~/.config/tmux/ |
| Claude hooks | Claude settings | ~/.claude/settings.json |
| Task state | Runtime | ~/.tmux/tasks/*.json |
| i3, i3blocks, system pkgs | NixOS | /etc/nixos/ |
| Staged NixOS changes | devrc repo | ~/workspace/devrc/nix/system/ |
| Audio (PipeWire) | NixOS | /etc/nixos/configuration.nix |
| GPU (NVIDIA beta) | NixOS | /etc/nixos/configuration.nix |

### Tmux Idle-Fade Color Scale
| Idle Time | Color | Hex |
|-----------|-------|-----|
| <10 min | bright green | `#b8bb26` |
| 10-30 min | green | `#98971a` |
| 30-60 min | aqua | `#689d6a` |
| 1-2 hr | yellow | `#d79921` |
| 2-4 hr | orange | `#d65d0e` |
| 4-8 hr | red | `#cc241d` |
| 8-24 hr | purple | `#b16286` |
| >24 hr | gray | `#665c54` |

### Known Issues / Gotchas
- `hm-session-vars.sh` is sourced per-shell via `.zshrc`, causing PATH entries from
  `home.sessionPath` to duplicate in nested shells. Not a regression (same issue existed
  with the old manual PATH append). Would require moving sourcing to `.zprofile` to fix.
- `/etc/nixos/` requires sudo — apply scripts in `nix/system/` use `cp` for complete file
  replacements and `sed -i` only for single-line changes. Prefer `cp` over `sed` for
  multiline modifications to avoid the kind of partial-delete bugs that sed can cause.
- i3 config is a nix string (`''...''`) in `i3config.nix`, not using home-manager's
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
  causing window and i3blocks flicker on workspace switching.
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

> Fuzzyclaw internals (TUI rendering, ripgrep search, dashboard data flow, Lipgloss footguns)
> live in the **`~/workspace/tmux-fuzzyclaw`** repo — see its `CLAUDE.md` and `/fuzzyclaw`
> command. Don't duplicate them here; this command only owns the devrc-side integration
> (hook wrappers in `scripts/`, the nix package, hook config in `~/.claude/settings.json`).

## Tool Coordination
- **Read/Glob/Grep**: Discover and analyze all config files
- **Agent (Explore)**: Deep codebase exploration for unfamiliar config patterns
- **Edit/Write**: Apply fixes to devrc files
- **Bash**: Stage system files, check running state, run verification commands
  - `tmux show-option -g <opt>` — check tmux config
  - `tmux list-keys | grep <pattern>` — verify bindings
  - `pgrep <service>` — check desktop services
  - `grep <pattern> /etc/i3.conf` — verify deployed i3 config
  - `systemctl --user status <service>` — check user services
  - `jq '.hooks' ~/.claude/settings.json` — verify hook configuration

## Boundaries

**Will:**
- Analyze all layers of the desktop/terminal stack holistically
- Identify cross-cutting issues (theme drift, binding conflicts, duplicated config)
- Implement fixes in devrc and stage system changes with apply scripts
- Verify all changes against live system state
- Preserve existing workflows and keybinding muscle memory
- Optimize task management system performance and reliability

**Will Not:**
- Run `sudo` commands directly — stage files for user to apply
- Remove keybindings without providing alternatives
- Change modifier keys (Alt for i3, Ctrl-A for tmux) without explicit request
- Make assumptions about hardware — read actual config for GPU, audio, display setup
- Use `sed` for complex multiline edits in apply scripts — write complete files instead
