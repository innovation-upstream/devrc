# devrc-dx — per-category verification snippets

Only reach for these when the one-shot block in `SKILL.md` reports a **FAIL** and you
need to see *why*. Running them all is ~20 extra tool round-trips for no new signal.

## Tmux
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

## Task management
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

## Desktop
```bash
pgrep dunst && echo "dunst: running"
pgrep xss-lock && echo "xss-lock: running" || systemctl --user status xss-lock
which rofi && echo "rofi: installed"
# picom should NOT be running (conflicts with NVIDIA forceFullCompositionPipeline)
pgrep picom && echo "WARNING: picom running (causes flicker with NVIDIA)"
```

## i3 (after a home-manager switch + i3 reload)
```bash
grep -E 'rofi|flameshot gui|Shift\+x.*i3lock|\+5%|Shift\+h move' ~/.config/i3/config
```

## Shell
```bash
grep -c "_direnv_hook" ~/.config/zsh/.zshrc        # should be 0
grep "CDPATH" ~/.config/zsh/.zshrc                 # should include civit
grep "exec zsh" ~/.bashrc                          # should have exec
cat ~/.nix-profile/etc/profile.d/hm-session-vars.sh | grep PATH  # sessionPath entries
```

## Config deployment
```bash
grep "duration = 0" ~/.config/alacritty/alacritty.toml   # bell disabled
find ~/.config/espanso -name "*.yml" | xargs grep "mtfc"  # should find nothing
```

Report results as a table with Pass/Fail/Pending status.
