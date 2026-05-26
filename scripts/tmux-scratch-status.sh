#!/usr/bin/env bash
# Scratch slot indicator for tmux status-left.
# Renders the 6 scratch slots as their hotkey letter, colored to match the
# popup border color set in .tmux.conf, so the status bar acts as a legend
# mapping popup color -> hotkey.
#
# Output: g G v V p P   (slot dimmed when session doesn't exist)

tmux list-sessions -F '#{session_name}' 2>/dev/null \
  | awk '
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
                printf "%s#[fg=%s,bold]%s#[default]", sep, color[s], key[s]
            } else {
                printf "%s#[fg=#504945]%s#[default]", sep, key[s]
            }
            sep = " "
        }
    }
  '
