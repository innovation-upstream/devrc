# Proposal: Per-Workspace Tmux Server Multiplexing

**Date:** 2026-08-14
**Status:** Draft
**Author:** opencode

---

## Problem

20 scratch sessions with letter codenames create cognitive load. All i3 workspaces share the same tmux server, so scratch slots are global — `Alt+w` always goes to `scratch11` (wheat) regardless of which workspace you're in. The flat namespace makes it hard to track "which session is for what."

## Proposed Solution

Separate tmux servers per i3 workspace. Each workspace gets its own isolated tmux universe with independent sessions.

**Key change:** dedicated hotkeys `$mod+Ctrl+<n>` open a terminal pre-attached to tmux server `ws<n>`.

```
$mod+1          → switch to i3 workspace 1
$mod+Ctrl+1     → open terminal + attach to tmux server ws1
$mod+Enter      → open terminal (generic, no tmux)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        i3 window manager                     │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│  WS 1    │  WS 2    │  WS 3    │  WS 4    │  WS 5-10       │
├──────────┼──────────┼──────────┼──────────┼─────────────────┤
│ tmux     │ tmux     │ tmux     │ tmux     │ tmux            │
│ server   │ server   │ server   │ server   │ server          │
│ ws1      │ ws2      │ ws3      │ ws4      │ ws5-ws10        │
├──────────┼──────────┼──────────┼──────────┼─────────────────┤
│ 20 slots │ 20 slots │ 20 slots │ 20 slots │ 20 slots each   │
│ scratch  │ scratch  │ scratch  │ scratch  │ scratch         │
│ scratch2 │ scratch2 │ scratch2 │ scratch2 │ scratch2        │
│ ...      │ ...      │ ...      │ ...      │ ...             │
│ scratch20│ scratch20│ scratch20│ scratch20│ scratch20       │
└──────────┴──────────┴──────────┴──────────┴─────────────────┘
```

**Isolation:** Sessions, windows, panes are fully independent across servers. `scratch11` in ws1 is a different session than `scratch11` in ws2.

**Hotkeys:** `Alt+<letter>` scratch bindings operate within the current server only. Same keys, same colors, same codenames — but scoped to whichever server you're attached to.

## Implementation

### 1. i3 config (`nix/i3/config.nix`)

Add 10 bindings for `$mod+Ctrl+<n>`:

```bash
# Tmux server launchers: $mod+Ctrl+<n> → terminal + attach to ws<n>
# Workspace 0 maps to server ws10 (matches $mod+0 → workspace 10)
bindsym $mod+Ctrl+1 exec --no-startup-id alacritty --class ws1 -e sh -c 'TMUX_SERVER=ws1 exec tmux -L ws1 a'
bindsym $mod+Ctrl+2 exec --no-startup-id alacritty --class ws2 -e sh -c 'TMUX_SERVER=ws2 exec tmux -L ws2 a'
bindsym $mod+Ctrl+3 exec --no-startup-id alacritty --class ws3 -e sh -c 'TMUX_SERVER=ws3 exec tmux -L ws3 a'
bindsym $mod+Ctrl+4 exec --no-startup-id alacritty --class ws4 -e sh -c 'TMUX_SERVER=ws4 exec tmux -L ws4 a'
bindsym $mod+Ctrl+5 exec --no-startup-id alacritty --class ws5 -e sh -c 'TMUX_SERVER=ws5 exec tmux -L ws5 a'
bindsym $mod+Ctrl+6 exec --no-startup-id alacritty --class ws6 -e sh -c 'TMUX_SERVER=ws6 exec tmux -L ws6 a'
bindsym $mod+Ctrl+7 exec --no-startup-id alacritty --class ws7 -e sh -c 'TMUX_SERVER=ws7 exec tmux -L ws7 a'
bindsym $mod+Ctrl+8 exec --no-startup-id alacritty --class ws8 -e sh -c 'TMUX_SERVER=ws8 exec tmux -L ws8 a'
bindsym $mod+Ctrl+9 exec --no-startup-id alacritty --class ws9 -e sh -c 'TMUX_SERVER=ws9 exec tmux -L ws9 a'
bindsym $mod+Ctrl+0 exec --no-startup-id alacritty --class ws10 -e sh -c 'TMUX_SERVER=ws10 exec tmux -L ws10 a'
```

**Why `--class ws<n>`:** allows i3 rules to auto-assign terminals to workspaces or apply per-server styling (optional, not required).

**Why `sh -c 'TMUX_SERVER=ws1 exec tmux ...'`:** passes the server name as an env var so tmux config can display it in the status bar.

### 2. tmux config (`.tmux.conf`)

Show server name in status-left:

```bash
# Before:
set -g status-left '#{?client_prefix,#[bg=#d79921],#[bg=#83a598]}#[fg=#282828,bold] #S #[default] #(~/.config/tmux/scratch-status.sh) '

# After:
set -g status-left '#{?client_prefix,#[bg=#d79921],#[bg=#83a598]}#[fg=#282828,bold] #S [#{TMUX_SERVER:-?}] #[default] #(~/.config/tmux/scratch-status.sh) '
```

**Width impact:** `#S` → `#S [ws1]` adds ~6 chars. Total ~59 chars, well within 90-char `status-left-length` budget.

### 3. No changes needed

- **scratch-status.sh** — works per-server automatically (reads current server's sessions)
- **scratch-slots.sh** — same 20 slots, independent per server
- **tmux-resurrect/continuum** — each server gets its own state dir automatically (`~/.tmux/resurrect/` is per-socket)
- **Alt+<letter> scratch hotkeys** — generated from slot table, work within current server

## Hotkey Reference

| Binding | Action |
|---|---|
| `$mod+<n>` | Switch to i3 workspace n |
| `$mod+Ctrl+<n>` | Open terminal + attach to tmux server ws<n> |
| `$mod+Return` | Open terminal (generic, no tmux) |
| `$mod+Shift+<n>` | Move container to workspace n |
| `Alt+<letter>` | Toggle scratch session (within current server) |

## Workflow Example

```
# Start homelab work
$mod+Ctrl+1          # → terminal opens, attached to ws1
Alt+g                # → scratch (grove) in ws1

# Switch to devrc work
$mod+Ctrl+2          # → terminal opens, attached to ws2
Alt+w                # → scratch11 (wheat) in ws2 — independent from ws1's wheat

# Quick check on homelab
$mod+1               # → switch to workspace 1 (ws1 terminal visible)
# ... or ...
$mod+Ctrl+1          # → new terminal attached to ws1
```

## Trade-offs

| Aspect | Benefit | Cost |
|---|---|---|
| **Isolation** | Sessions don't collide across workspaces | Can't easily share a session between workspaces |
| **Cognitive load** | Each server is a focused context (20 slots, not 200) | Need to remember which server you're in |
| **Status visibility** | Server name shown in status bar | Slightly wider status bar |
| **Hotkey consistency** | Same keys work everywhere | Muscle memory must learn `$mod+Ctrl+<n>` |
| **State persistence** | Each server persists independently | 10× resurrect state dirs |

## What This Does NOT Change

- `$mod+Return` stays as generic terminal (no tmux)
- Existing workspace switching (`$mod+1-0`)
- Existing move-container bindings (`$mod+Shift+1-0`)
- Scratch slot table, colors, codenames
- Session-manager, agent-ops, bar-status-poll (all read from current server)
- Two hosts (workbench + laptop) — each host gets its own set of servers

## Migration Path

1. **Add i3 bindings** — non-destructive, new hotkeys only
2. **Update tmux status-left** — shows server name
3. **Test with one server** — `$mod+Ctrl+1` → verify ws1 works
4. **Roll out incrementally** — add more servers as needed
5. **Optional: auto-assign workspaces** — i3 rules like `for_window [class="ws1"] move to workspace 1`

**Rollback:** Remove the `$mod+Ctrl+<n>` bindings and revert status-left. No data loss — servers persist until manually killed.

## Open Questions

1. **Workspace 0 mapping** — `$mod+Ctrl+0` → ws10 (matches `$mod+0` → workspace 10). Acceptable, or use `$mod+Ctrl+minus` for ws10?

2. **Server lifecycle** — should servers auto-start on boot (systemd user service), or only exist when a terminal is attached?

3. **Cross-server peek** — if you're in ws1 and need to see ws5's sessions, run `tmux -L ws5 list-sessions`. Acceptable, or need a quick-switch hotkey?

4. **Naming convention** — `ws1`-`ws10` matches workspace numbers. Alternative: `w1`-`w10` (shorter socket names).

---

**Next steps:** Review this proposal, then implement if approved.
