#!/usr/bin/env bash
# tmux-task-dashboard.sh - Fuzzy task dashboard across all tmux sessions
# Shows task name, directory, idle time, and last Claude activity summary
# Output format: target<TAB>display fields (fzf uses --with-nth to hide target)

TASK_DIR="${HOME}/.tmux/tasks"
NOW=$(date +%s)

generate_lines() {
    tmux list-windows -a -F '#{session_name}:#{window_index}	#{window_id}	#{window_name}	#{b:pane_current_path}	#{window_activity}	#{session_attached}' | \
    while IFS=$'\t' read -r target win_id name dir activity attached; do
        # Calculate idle time
        idle="--"
        if [[ -n "$activity" && "$activity" != "0" ]]; then
            s=$((NOW - activity))
            if (( s < 60 )); then idle="${s}s"
            elif (( s < 3600 )); then idle="$((s / 60))m"
            elif (( s < 86400 )); then idle="$((s / 3600))h"
            else idle="$((s / 86400))d"; fi
        fi

        # Read task state summary if available
        task_file="${TASK_DIR}/${win_id//[@%]/}.json"
        summary=""
        if [[ -f "$task_file" ]]; then
            summary=$(jq -r '.summary // ""' "$task_file" 2>/dev/null | head -c 60 | tr '\n' ' ')
        fi

        # Format: target<TAB>visible columns
        printf "%s\t%-30s  %-18s  %5s  %s\n" \
            "$target" "$name" "$dir" "$idle" "$summary"
    done
}

# Generate lines and pipe to fzf
SELECTED=$(generate_lines | \
    fzf --delimiter='\t' \
        --with-nth=2.. \
        --reverse \
        --ansi \
        --header='TASK                            DIR                 IDLE   LAST ACTIVITY' \
        --preview='f="'"$TASK_DIR"'/{+1}"; f="${f//:*/}"; for tf in '"$TASK_DIR"'/*.json; do
            wid=$(jq -r ".window_id" "$tf" 2>/dev/null)
            target=$(echo {+1} | cut -d: -f1)
            tsess=$(jq -r ".tmux_session" "$tf" 2>/dev/null)
            tidx=$(jq -r ".window_index" "$tf" 2>/dev/null)
            check="$tsess:$tidx"
            if [[ "$check" == {+1} ]]; then
                echo "Task: $(jq -r .task "$tf")"
                echo "Status: $(jq -r .status "$tf")"
                echo "Dir: $(jq -r .cwd "$tf")"
                echo "Started: $(jq -r .started "$tf")"
                echo "Last Activity: $(jq -r .last_activity "$tf")"
                echo ""
                echo "Summary:"
                jq -r .summary "$tf"
                exit 0
            fi
        done
        echo "No task state file for this window"' \
        --preview-window=down:8:wrap \
        --bind='ctrl-x:execute-silent(tmux kill-window -t {+1})+abort' \
)

# Jump to selected window
if [[ -n "$SELECTED" ]]; then
    TARGET=$(echo "$SELECTED" | cut -f1)
    tmux switch-client -t "$TARGET" 2>/dev/null || tmux select-window -t "$TARGET" 2>/dev/null
fi
