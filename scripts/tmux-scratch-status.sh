#!/usr/bin/env bash
# Scratch slot indicator for tmux status-left.
# Renders the 6 scratch slots as their hotkey letter, colored to match the
# popup border color set in .tmux.conf, so the status bar acts as a legend
# mapping popup color -> hotkey.
#
# A leading ● flags slots that have a window waiting for user input
# (status="waiting" in fuzzyclaw's task state, filtered against currently
# existing tmux windows so stale entries don't trigger).
#
# Output examples:
#   g G v V p P     — all slots exist, nothing waiting
#   g ●G v V p P    — Gold (scratch2) has a waiting prompt
#   g G v V p P     — slot dimmed (gray) when session doesn't exist

# Sessions with at least one window waiting for input, space-padded for
# substring matching in awk. Empty if jq / task files are missing.
waiting=""
if command -v jq >/dev/null 2>&1 && compgen -G "$HOME/.tmux/tasks/*.json" >/dev/null; then
    current_wids=" $(tmux list-windows -a -F '#{window_id}' 2>/dev/null | tr '\n' ' ')"
    waiting=" $(jq -r -s --arg wids "$current_wids" '
        map(. as $t | select($t.status == "waiting" and ($wids | contains(" " + $t.window_id + " "))))
        | map(.tmux_session)
        | unique
        | join(" ")
    ' "$HOME"/.tmux/tasks/*.json 2>/dev/null) "
fi

tmux list-sessions -F '#{session_name}' 2>/dev/null \
  | awk -v waiting="$waiting" '
    BEGIN {
        # slot key : session : color (matches popup -s border color in .tmux.conf)
        n = split("scratch:g:#b8bb26 scratch2:G:#d79921 scratch3:v:#b16286 scratch4:V:#83a598 scratch5:p:#cc241d scratch6:P:#689d6a", slots, " ")
        for (i = 1; i <= n; i++) {
            split(slots[i], p, ":")
            sess[i]      = p[1]
            key[p[1]]    = p[2]
            color[p[1]]  = p[3]
        }
    }
    { exists[$1] = 1 }
    END {
        sep = ""
        for (i = 1; i <= n; i++) {
            s = sess[i]
            if (s in exists) {
                marker = (index(waiting, " " s " ") > 0) ? "●" : ""
                printf "%s#[fg=%s,bold]%s%s#[default]", sep, color[s], marker, key[s]
            } else {
                printf "%s#[fg=#504945]%s#[default]", sep, key[s]
            }
            sep = " "
        }
    }
  '
