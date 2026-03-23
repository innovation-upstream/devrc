#!/usr/bin/env bash
# tmux-pipe-activity.sh - Manage pipe-pane for background activity tracking
# Usage: tmux-pipe-activity.sh [start|stop|switch|linked|init] [window_id]

ACTIVITY_DIR="${HOME}/.tmux/activity"
mkdir -p "$ACTIVITY_DIR"

ACTION="$1"
WINDOW_ID="$2"

start_pipe() {
    local win_id="$1"
    local pane_id
    pane_id=$(tmux list-panes -t "$win_id" -F '#{pane_id}' 2>/dev/null | head -1)

    if [[ -n "$pane_id" ]]; then
        tmux pipe-pane -t "$pane_id" "~/.config/tmux/activity-receiver.sh '$win_id'"
    fi
}

stop_pipe() {
    local win_id="$1"
    local pane_id
    pane_id=$(tmux list-panes -t "$win_id" -F '#{pane_id}' 2>/dev/null | head -1)

    if [[ -n "$pane_id" ]]; then
        tmux pipe-pane -t "$pane_id"
    fi
}

case "$ACTION" in
    start)
        [[ -n "$WINDOW_ID" ]] && start_pipe "$WINDOW_ID"
        ;;
    stop)
        [[ -n "$WINDOW_ID" ]] && stop_pipe "$WINDOW_ID"
        ;;
    switch)
        # Hot path: only pipe prev→current transition instead of iterating all windows
        SESSION=$(tmux display-message -p '#{session_name}')
        current_win=$(tmux display-message -p '#{window_id}')
        prev_file="${ACTIVITY_DIR}/.prev_${SESSION}"

        # Stop pipe on current window (now focused)
        stop_pipe "$current_win"

        # Start pipe on previous window (just moved to background)
        if [[ -f "$prev_file" ]]; then
            prev_win=$(< "$prev_file")
            [[ "$prev_win" != "$current_win" ]] && start_pipe "$prev_win"
        fi

        # Track current for next switch
        echo "$current_win" > "$prev_file"
        ;;
    linked|init)
        # Cold path: ensure all background windows have pipes
        # Fires on window creation (linked) and session creation (init)
        tmux list-windows -F '#{window_id} #{window_active}' | while read -r win_id is_active; do
            if [[ "$is_active" == "0" ]]; then
                start_pipe "$win_id"
            fi
        done
        ;;
    *)
        echo "Usage: $0 [start|stop|switch|linked|init] [window_id]"
        exit 1
        ;;
esac
