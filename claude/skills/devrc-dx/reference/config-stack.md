# devrc-dx — config stack reference

The stable facts about the desktop/terminal stack: palette, keybinding conventions,
the scratch-slot system, the task-management wiring, and who owns which file.

## Theme: Gruvbox Dark
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

## Key conventions
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
| tmux scratch slots | Alt+g/G/v/V/p/P | 6 persistent popups (see below) |
| tmux lazygit | prefix+g | 90% popup |
| tmux k9s | prefix+K | 95% popup |

## Scratch slots
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
Border color set via `display-popup -S 'fg=COLOR'` — NOT `-s` (see `gotchas.md`).
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

## Task management system
Upstream: [ZacxDev/tmux-fuzzyclaw](https://github.com/ZacxDev/tmux-fuzzyclaw).

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

Other `fuzzyclaw` subcommands: `status` (one-line output for tmux status-right),
`search <query>` (CLI global search across conversation history),
`export <cwd>` (markdown session export).

The Go binary replaced the old bash dashboard/idle/pipe scripts: `idle-update` took
over from `idle-update.sh`, `pipe` from `pipe-activity.sh`, and `hook stop|resume`
from most of `task-hook.sh` / `task-resume.sh`.

**Dashboard:** `fuzzyclaw dashboard` (Alt+F) is the Bubble Tea TUI — the only bound dashboard.
Feature lists, controls, data flow, and performance targets are documented in the
`~/workspace/tmux-fuzzyclaw` repo, not duplicated here. From the devrc side, what matters:
hooks call the thin wrappers in `scripts/`, the binary is packaged via
`nix/pkgs/tools/tmux-fuzzyclaw.nix`, and hook config lives in `~/.claude/settings.json`.
The old fzf popup (`scripts/tmux-task-dashboard.sh`, formerly Alt+c) is **no longer bound** —
dead code still symlinked by `home.nix`; safe to remove when convenient.

> Fuzzyclaw internals (TUI rendering, ripgrep search, dashboard data flow, Lipgloss footguns)
> live in the **`~/workspace/tmux-fuzzyclaw`** repo — see its `CLAUDE.md` and `/fuzzyclaw`
> skill. Don't duplicate them here; this skill only owns the devrc-side integration
> (hook wrappers in `scripts/`, the nix package, hook config in `~/.claude/settings.json`).

## File ownership
| Scope | Managed By | Location |
|-------|-----------|----------|
| Shell, editor, tmux, git | home-manager | ~/workspace/devrc |
| Dunst, espanso | home-manager (services) | ~/workspace/devrc/nix/home.nix |
| Fuzzyclaw binary | home-manager (nix buildGoModule) | ~/workspace/tmux-fuzzyclaw/ → PATH |
| Task scripts | home-manager (file symlinks) | ~/workspace/devrc/scripts/ → ~/.config/tmux/ |
| Claude hooks | Claude settings | ~/.claude/settings.json |
| Task state | Runtime | ~/.tmux/tasks/*.json |
| i3 config + i3status-rust bar | home-manager | ~/workspace/devrc/nix/i3/config.nix, nix/graphical.nix |
| System pkgs, i3 *enablement*, display-manager | NixOS | /etc/nixos/ |
| Staged NixOS changes | devrc repo | ~/workspace/devrc/nix/system/ |
| Audio (PipeWire) | NixOS | /etc/nixos/configuration.nix |
| GPU (NVIDIA beta) | NixOS | /etc/nixos/configuration.nix |

## Tmux idle-fade color scale
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
