#!/usr/bin/env bash
# tmux-pipe-activity.sh - Manage pipe-pane for background activity tracking
# Usage: tmux-pipe-activity.sh [start|stop|switch] [window_id]

ACTIVITY_DIR="${HOME}/.tmux/activity"
mkdir -p "$ACTIVITY_DIR"

ACTION="$1"
WINDOW_ID="$2"

start_pipe() {
    local win_id="$1"
    local pane_id
    pane_id=$(tmux list-panes -t "$win_id" -F '#{pane_id}' 2>/dev/null | head -1)

    if [[ -n "$pane_id" ]]; then
        # Start piping - on any output, update timestamp file
        tmux pipe-pane -t "$pane_id" "~/.config/tmux/activity-receiver.sh '$win_id'"
    fi
}

stop_pipe() {
    local win_id="$1"
    local pane_id
    pane_id=$(tmux list-panes -t "$win_id" -F '#{pane_id}' 2>/dev/null | head -1)

    if [[ -n "$pane_id" ]]; then
        # Stop piping (no argument to pipe-pane stops it)
        tmux pipe-pane -t "$pane_id"
    fi
}

case "$ACTION" in
    start)
        # Start piping for a specific window
        if [[ -n "$WINDOW_ID" ]]; then
            start_pipe "$WINDOW_ID"
        fi
        ;;
    stop)
        # Stop piping for a specific window
        if [[ -n "$WINDOW_ID" ]]; then
            stop_pipe "$WINDOW_ID"
        fi
        ;;
    switch)
        # Called on window switch - stop pipe on new current, start on all others
        current_win=$(tmux display-message -p '#{window_id}')

        # Stop pipe on current window
        stop_pipe "$current_win"

        # Start pipe on all non-current windows
        tmux list-windows -F '#{window_id} #{window_active}' | while read -r win_id is_active; do
            if [[ "$is_active" == "0" ]]; then
                start_pipe "$win_id"
            fi
        done
        ;;
    init)
        # Initialize all non-current windows (called on session create)
        tmux list-windows -F '#{window_id} #{window_active}' | while read -r win_id is_active; do
            if [[ "$is_active" == "0" ]]; then
                start_pipe "$win_id"
            fi
        done
        ;;
    *)
        echo "Usage: $0 [start|stop|switch|init] [window_id]"
        exit 1
        ;;
esac
