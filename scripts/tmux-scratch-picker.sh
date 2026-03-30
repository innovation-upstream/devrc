#!/usr/bin/env bash
# Scratch picker — list existing scratch-* sessions, toggle or create new ones
# Bound to Alt+Shift+G in tmux

current_session=$(tmux display-message -p '#{session_name}')

# If we're inside a scratch session, just detach
if [[ "$current_session" == scratch* ]]; then
    tmux detach-client
    exit 0
fi

# List scratch sessions + option to create new
selected=$(
    {
        tmux list-sessions -F '#{session_name}' 2>/dev/null | grep '^scratch' | sort
        echo "[+ new scratchpad]"
    } | fzf --prompt="scratch> " \
            --header="enter: toggle | type to filter/create" \
            --reverse \
            --height=100%
)

[ -z "$selected" ] && exit 0

if [ "$selected" = "[+ new scratchpad]" ]; then
    selected="scratch-$(date +%s)"
fi

tmux attach-session -t "$selected" 2>/dev/null || exec tmux new-session -s "$selected"
