#!/usr/bin/env bash
# tmux-task-dashboard.sh - Fuzzy task dashboard across all tmux sessions
# Features: session grouping, stale detection, auto-populated summaries,
# full conversation history search via hidden fzf field
#
# Format: target\tkeywords\tvisible_display
# fzf --with-nth=3.. hides target+keywords but searches all fields

TASK_DIR="${HOME}/.tmux/tasks"
CLAUDE_PROJECTS="${HOME}/.claude/projects"
NOW=$(date +%s)

# ANSI colors
RED='\033[31m'
YELLOW='\033[33m'
GREEN='\033[32m'
DIM='\033[2m'
BOLD='\033[1m'
CYAN='\033[36m'
NC='\033[0m'

# Map a cwd to its Claude project directory
project_dir_for() {
    local cwd="$1"
    echo "${CLAUDE_PROJECTS}/$(echo "$cwd" | tr '/' '-')"
}

# Extract user messages from a Claude session file for keyword search
# Returns space-separated text, max ~1000 chars
extract_keywords() {
    local cwd="$1"
    local session_id="$2"
    local pdir
    pdir=$(project_dir_for "$cwd")
    [[ ! -d "$pdir" ]] && return

    local session_file=""
    # Try exact session ID first
    if [[ -n "$session_id" && "$session_id" != "unknown" && -f "${pdir}/${session_id}.jsonl" ]]; then
        session_file="${pdir}/${session_id}.jsonl"
    else
        # Fall back to most recent session file
        session_file=$(ls -t "$pdir"/*.jsonl 2>/dev/null | head -1)
    fi
    [[ -z "$session_file" || ! -f "$session_file" ]] && return

    # Extract user messages first (short, keyword-rich), then assistant text
    {
        # User prompts — most valuable for search
        tail -500 "$session_file" 2>/dev/null | \
            jq -r 'select(.type == "user" and .userType == "external") |
                .message.content | if type == "string" then . else "" end' 2>/dev/null | \
            tr '\n\t' '  '
        # Assistant text — supplementary context
        tail -200 "$session_file" 2>/dev/null | \
            jq -r 'select(.type == "assistant") |
                [.message.content[]? | select(.type == "text") | .text // ""] | join(" ")' 2>/dev/null | \
            tr '\n\t' '  '
    } | head -c 3000
}

# Extract a short summary from task file or Claude session
get_summary() {
    local win_id="$1"
    local cwd="$2"
    local task_file="${TASK_DIR}/${win_id//[@%]/}.json"

    # Try task state file first
    if [[ -f "$task_file" ]]; then
        local summary
        summary=$(jq -r '.summary // ""' "$task_file" 2>/dev/null | head -c 80 | tr '\n' ' ')
        if [[ -n "$summary" ]]; then
            echo "$summary"
            return
        fi
    fi

    # Fall back: last assistant text from most recent session
    local pdir
    pdir=$(project_dir_for "$cwd")
    local latest
    latest=$(ls -t "$pdir"/*.jsonl 2>/dev/null | head -1)
    [[ -z "$latest" ]] && return

    tail -50 "$latest" 2>/dev/null | \
        jq -r 'select(.type == "assistant") | [.message.content[]? | select(.type == "text") | .text] | join(" ")' 2>/dev/null | \
        tail -1 | head -c 80 | tr '\n' ' '
}

format_idle() {
    local activity="$1"
    if [[ -z "$activity" || "$activity" == "0" ]]; then
        echo "--"
        return
    fi
    local s=$((NOW - activity))
    if (( s < 60 )); then echo "${s}s"
    elif (( s < 3600 )); then echo "$((s / 60))m"
    elif (( s < 86400 )); then echo "$((s / 3600))h"
    else echo "$((s / 86400))d"
    fi
}

# Format a single window line
# Args: target win_id name dir activity full_cwd command bell color_override
format_window() {
    local target="$1" win_id="$2" name="$3" dir="$4" activity="$5"
    local full_cwd="$6" command="$7" bell="$8" color_override="$9"

    # Idle time + staleness color
    local idle
    idle=$(format_idle "$activity")
    local idle_s=0
    [[ -n "$activity" && "$activity" != "0" ]] && idle_s=$((NOW - activity))

    local color="${color_override}"
    local stale_marker=""
    if [[ -z "$color" ]]; then
        if (( idle_s > 86400 )); then
            color="${DIM}${RED}"
            stale_marker=" 💀"
        elif (( idle_s > 3600 )); then
            color="${YELLOW}"
        fi
    fi

    # Claude status indicator
    local status=""
    case "$name" in
        "🔄 "*) status="🔄" ;;
        "⏸ "*)  status="⏸" ;;
        "✅ "*) status="✅" ;;
        *)
            [[ "$command" == claude* ]] && status="●"
            ;;
    esac

    # Get session ID from task file if available
    local session_id=""
    local task_file="${TASK_DIR}/${win_id//[@%]/}.json"
    [[ -f "$task_file" ]] && session_id=$(jq -r '.claude_session // ""' "$task_file" 2>/dev/null)

    # Summary (task file → auto-populated from session)
    local summary
    summary=$(get_summary "$win_id" "$full_cwd")

    # Conversation keywords for search (hidden from display)
    local keywords
    keywords=$(extract_keywords "$full_cwd" "$session_id")

    # Clean display name: strip emoji prefix and trailing ● indicator
    local display_name
    display_name=$(echo "$name" | sed -E 's/^(🔄|⏸|✅) //' | sed 's/ ●$//')
    display_name="${display_name:0:24}"

    # Status column: pad to consistent width
    local st_col
    case "$status" in
        "🔄"|"⏸"|"✅") st_col="$status" ;;
        "●")            st_col="● " ;;
        *)              st_col="  " ;;
    esac

    # Truncate dir to 20 chars
    dir="${dir:0:20}"

    printf "%s\t%s\t${color} %s %-24s  %-20s  %5s  %.55s${stale_marker}${NC}\n" \
        "$target" "$keywords" "$st_col" "$display_name" "$dir" "$idle" "$summary"
}

generate_lines() {
    local has_blocked=false

    # Collect all window data once (avoid multiple tmux calls)
    local tmpfile
    tmpfile=$(mktemp)
    tmux list-windows -a -F '#{session_name}	#{session_name}:#{window_index}	#{window_id}	#{window_name}	#{b:pane_current_path}	#{window_activity}	#{pane_current_path}	#{pane_current_command}	#{window_bell_flag}' > "$tmpfile"

    # Pass 1: Blocked/waiting section (bell flag + claude running)
    while IFS=$'\t' read -r session target win_id name dir activity full_cwd command bell; do
        if [[ "$bell" == "1" && "$command" == claude* ]]; then
            if [[ "$has_blocked" == false ]]; then
                printf "\t\t${BOLD}${RED}── WAITING FOR INPUT ──${NC}\n"
                has_blocked=true
            fi
            format_window "$target" "$win_id" "$name" "$dir" "$activity" "$full_cwd" "$command" "$bell" "${BOLD}${RED}"
        fi
    done < "$tmpfile"

    if [[ "$has_blocked" == true ]]; then
        printf "\t\t\n"
    fi

    # Pass 2: All windows sorted by idle time ASC (most recent activity first)
    # Sort by activity timestamp descending (most recent activity = least idle first)
    sort -t$'\t' -k6,6rn "$tmpfile" | \
    while IFS=$'\t' read -r session target win_id name dir activity full_cwd command bell; do
        format_window "$target" "$win_id" "$name" "$dir" "$activity" "$full_cwd" "$command" "$bell" ""
    done

    rm -f "$tmpfile"
}

# Preview: show task state + recent conversation
PREVIEW_CMD='
    TARGET={1}
    TASK_DIR='"${TASK_DIR}"'
    CLAUDE_PROJECTS='"${CLAUDE_PROJECTS}"'
    CWD=$(tmux display-message -t "$TARGET" -p "#{pane_current_path}" 2>/dev/null)
    PDIR="${CLAUDE_PROJECTS}/$(echo "$CWD" | tr "/" "-")"

    # Task state
    WIN_ID=$(tmux display-message -t "$TARGET" -p "#{window_id}" 2>/dev/null)
    TF="${TASK_DIR}/${WIN_ID//[@%]/}.json"
    if [[ -f "$TF" ]]; then
        echo -e "\033[1mTask State\033[0m"
        echo "  Task:     $(jq -r .task "$TF")"
        echo "  Status:   $(jq -r .status "$TF")"
        echo "  Dir:      $(jq -r .cwd "$TF")"
        echo "  Started:  $(jq -r .started "$TF")"
        echo "  Activity: $(jq -r .last_activity "$TF")"
        echo ""
        echo -e "\033[1mLast Claude Output\033[0m"
        jq -r ".summary // \"(none)\"" "$TF" | fold -s -w 80
    else
        echo -e "\033[1mWindow Info\033[0m"
        echo "  Dir: $CWD"
        CMD=$(tmux display-message -t "$TARGET" -p "#{pane_current_command}" 2>/dev/null)
        echo "  Command: $CMD"
    fi

    # Recent user messages from session
    echo ""
    echo -e "\033[1mRecent Prompts\033[0m"
    LATEST=$(ls -t "$PDIR"/*.jsonl 2>/dev/null | head -1)
    if [[ -n "$LATEST" ]]; then
        tail -500 "$LATEST" | jq -r "select(.type == \"user\" and .userType == \"external\") | .message.content | if type == \"string\" then . elif type == \"array\" then [.[] | select(type == \"object\" and .type == \"text\") | .text] | join(\" \") else \"\" end" 2>/dev/null | grep -v "^$" | tail -5 | while read -r line; do
            echo "  > $(echo "$line" | head -c 120)"
        done
    else
        echo "  (no Claude session found)"
    fi
'

# Generate lines and pipe to fzf
SELECTED=$(generate_lines | \
    fzf --delimiter='\t' \
        --with-nth=3.. \
        --reverse \
        --ansi \
        --header="$(printf ' %-2s %-24s  %-20s  %5s  %s' 'ST' 'TASK' 'DIR' 'IDLE' 'SUMMARY')" \
        --preview="$PREVIEW_CMD" \
        --preview-window=right:45%:wrap \
        --bind='ctrl-x:execute-silent(tmux kill-window -t {1})+reload('"$0"' --lines)' \
        --bind='ctrl-d:execute-silent(tmux kill-window -t {1})+reload('"$0"' --lines)' \
)

# Jump to selected window
if [[ -n "$SELECTED" ]]; then
    TARGET=$(echo "$SELECTED" | cut -f1)
    tmux switch-client -t "$TARGET" 2>/dev/null || tmux select-window -t "$TARGET" 2>/dev/null
fi
