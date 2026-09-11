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
| tmux scratch slots | Alt+ 20 keys (g/G/v/V/p/P/o/O/n/N/w/W/m/M/i/I/u/U/y/Y) | 20 persistent popups (see below) |
| tmux lazygit | prefix+g | 90% popup |
| tmux k9s | prefix+K | 95% popup |

## Scratch slots
Twenty persistent popup sessions, each with a color-themed border and a memorable
title (name starts with the hotkey letter and evokes the slot's color). The slot
table is `scripts/tmux-scratch-slots.sh` — the single source of truth; the popup
bindings are GENERATED from it by `nix/programs/tmux/default.nix`, so the six
rows below are an excerpt, not the set.

**The colour legend lives in the i3status-rust BAR, not in tmux.** The block
script is `scripts/i3status-scratchpads` (wired up as `scratchpadsBlock` in
`nix/graphical.nix`): it shows every slot as its hotkey letter in the slot's own
colour with the session's window count (`g2`), dimmed to `#504945` with no count
when that session does not exist, and `scratch ?` when it could not measure at
all. Left-click opens the scratchpad picker in a float terminal.

It USED to be a `status-left` interpolation (`scripts/tmux-scratch-status.sh`),
which could fit only 14 of the 20 slots inside `status-left-length 90`. That
script is retained as a debugging/fallback renderer but is no longer called from
anywhere. The old leading `●` "waiting" marker is GONE — it keyed on fuzzyclaw
`status`, which cannot answer (see `scripts/tmux-scratch-status.sh`'s header).

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
the last few lines from every scratch session at once — it sources the slot
table, so that is all 20, and its per-section line count adapts to the popup
height (auto-refresh every 2s, dismiss
with q/Esc). Each section has a colored header in its slot color and a line
count that adapts to popup height. Strips Claude's input-box chrome (the two
───── separators wrapping the input prompt) so the visible content is
conversation/progress, not the model+ctx status bar. Use for monitoring
parallel Claude sessions without cycling through scratch hotkeys. Like the
slot hotkeys, M-m detaches first if pressed inside a scratch so popups don't nest.

**Aggregate counters in status-right — REMOVED 2026-08-14, and the thing they
"paired with" is gone too.** `scripts/tmux-claude-counters.sh` rendered
`N🔄 N⏸ N●` (running / paused / waiting Claude windows) at the left of
status-right; all three counts came from fuzzyclaw's task `status`, which cannot
answer (measured across 407 task files: 301 done, 87 paused, 18 running, ONE
waiting — so the bar read `0●` while session-manager measured 5 windows waiting
on the operator). The per-slot `●` flag in status-left that this paragraph used
to pair it with is gone for the same reason. Today `status-right` expands to a
single space: `idle-update.sh` and continuum's `continuum_save.sh` are both
side-effect interpolations that print nothing. "Which window needs me" is the
`session-manager` skill's question now, not the status line's.

**agent-ops dashboard — RETIRED.** `scripts/agent-ops` was the read-only
"mission-control" TUI (open PRs, live agent runs, momentum, health), launched
from i3 as a floating alacritty, from tmux `prefix+A`, and from the ▦ bar button.
All three launchers are gone and the script is deleted. Where its panels went:
live runs + the clawgate queue → `session-manager`; PRs, cluster alerts and **local
systemd health** → `syshealth --systemd`; momentum → `/initiative-scan`; the counts → bar
pills. Its `/proc` Claude-session detector — the one piece with no equivalent
elsewhere — is now `scripts/lib/claude_sessions.py`, feeding the ▦ pill
(`i3status-claude-runs`), which stays as an indicator with no click.
For *what's happening right now* use the monitor popup (M-m, live capture-pane);
for *what each session is working on* use `session-manager`.

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
