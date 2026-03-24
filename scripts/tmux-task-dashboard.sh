#!/usr/bin/env bash
# tmux-task-dashboard.sh - Fuzzy task dashboard across all tmux sessions
# Features: stale detection, auto-populated summaries,
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
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

# ── Pre-compute caches to avoid per-window subprocess spawns ──

declare -A TASK_CACHE      # win_id -> full json
declare -A KEYWORD_CACHE   # cwd -> keywords text
declare -A SUMMARY_CACHE   # cwd -> summary text (fallback when no task file)

# Load all task state files into memory at once
for tf in "$TASK_DIR"/*.json; do
    [[ -f "$tf" ]] || continue
    content=$(<"$tf")
    wid=$(basename "$tf" .json)
    TASK_CACHE["$wid"]="$content"
done

# Pre-compute keywords and summaries per unique cwd
precompute_for_cwd() {
    local cwd="$1"
    [[ -n "${KEYWORD_CACHE[$cwd]+x}" ]] && return  # already cached
    local pdir="${CLAUDE_PROJECTS}/$(echo "$cwd" | tr '/' '-')"
    if [[ ! -d "$pdir" ]]; then
        KEYWORD_CACHE["$cwd"]=""
        SUMMARY_CACHE["$cwd"]=""
        return
    fi
    local latest
    latest=$(ls -t "$pdir"/*.jsonl 2>/dev/null | head -1)
    if [[ -z "$latest" ]]; then
        KEYWORD_CACHE["$cwd"]=""
        SUMMARY_CACHE["$cwd"]=""
        return
    fi
    # Extract keywords: rg scans full file (~14ms) for complete conversation coverage
    KEYWORD_CACHE["$cwd"]=$(
        {
            # User prompts — full history, keyword-rich
            rg '"type":"user"' "$latest" 2>/dev/null | \
                jq -r '.message.content | if type == "string" then . else "" end' 2>/dev/null | \
                tr '\n\t' '  '
            # Assistant text — last 200 lines for recent context
            tail -200 "$latest" 2>/dev/null | \
                rg '"type":"assistant"' 2>/dev/null | \
                jq -r '[.message.content[]? | select(.type == "text") | .text // ""] | join(" ")' 2>/dev/null | \
                tr '\n\t' '  '
        } | head -c 3000
    )
    # Extract summary: last assistant text
    SUMMARY_CACHE["$cwd"]=$(
        tail -50 "$latest" 2>/dev/null | \
            rg '"type":"assistant"' 2>/dev/null | \
            jq -r '[.message.content[]? | select(.type == "text") | .text] | join(" ")' 2>/dev/null | \
            tail -1 | head -c 80 | tr '\n' ' '
    )
}

generate_lines() {
    local has_blocked=false

    # Collect all window data in one tmux call
    local tmpfile
    tmpfile=$(mktemp)
    tmux list-windows -a -F '#{session_name}	#{session_name}:#{window_index}	#{window_id}	#{window_name}	#{b:pane_current_path}	#{window_activity}	#{pane_current_path}	#{pane_current_command}	#{window_bell_flag}' > "$tmpfile"

    # Pre-compute caches for all unique cwds
    while IFS=$'\t' read -r _ _ _ _ _ _ full_cwd _ _; do
        precompute_for_cwd "$full_cwd"
    done < "$tmpfile"

    # Pass 1: Blocked/waiting section (bell flag + claude running)
    while IFS=$'\t' read -r _ target win_id name dir activity full_cwd command bell; do
        if [[ "$bell" == "1" && "$command" == claude* ]]; then
            if [[ "$has_blocked" == false ]]; then
                printf "\t\t${BOLD}${RED}── WAITING FOR INPUT ──${NC}\n"
                has_blocked=true
            fi
            format_line "$target" "$win_id" "$name" "$dir" "$activity" "$full_cwd" "$command" "${BOLD}${RED}"
        fi
    done < "$tmpfile"

    [[ "$has_blocked" == true ]] && printf "\t\t\n"

    # Pass 2: All windows sorted by idle time ASC (most recent activity first)
    sort -t$'\t' -k6,6rn "$tmpfile" | \
    while IFS=$'\t' read -r _ target win_id name dir activity full_cwd command bell; do
        format_line "$target" "$win_id" "$name" "$dir" "$activity" "$full_cwd" "$command" ""
    done

    rm -f "$tmpfile"
}

format_line() {
    local target="$1" win_id="$2" name="$3" dir="$4" activity="$5"
    local full_cwd="$6" command="$7" color_override="$8"

    # Inline idle time calculation (no subshell)
    local idle="--" idle_s=0
    if [[ -n "$activity" && "$activity" != "0" ]]; then
        idle_s=$((NOW - activity))
        if (( idle_s < 60 )); then idle="${idle_s}s"
        elif (( idle_s < 3600 )); then idle="$((idle_s / 60))m"
        elif (( idle_s < 86400 )); then idle="$((idle_s / 3600))h"
        else idle="$((idle_s / 86400))d"
        fi
    fi

    # Color from override or staleness
    local color="${color_override}" stale_marker=""
    if [[ -z "$color" ]]; then
        if (( idle_s > 86400 )); then
            color="${DIM}${RED}"; stale_marker=" 💀"
        elif (( idle_s > 3600 )); then
            color="${YELLOW}"
        fi
    fi

    # Status indicator (inline, no subshell)
    local status="" st_col="  "
    case "$name" in
        "🔄 "*) status="🔄"; st_col="🔄" ;;
        "⏸ "*)  status="⏸";  st_col="⏸" ;;
        "✅ "*) status="✅"; st_col="✅" ;;
        *)      [[ "$command" == claude* ]] && { status="●"; st_col="● "; } ;;
    esac

    # Task file lookup from cache (no jq subprocess)
    local wid_clean="${win_id//[@%]/}"
    local task_json="${TASK_CACHE[$wid_clean]}"
    local summary="" session_id=""
    if [[ -n "$task_json" ]]; then
        # Extract fields with parameter expansion where possible, fall back to jq
        summary=$(echo "$task_json" | jq -r '.summary // ""' 2>/dev/null)
        summary="${summary:0:80}"
        summary="${summary//$'\n'/ }"
        session_id=$(echo "$task_json" | jq -r '.claude_session // ""' 2>/dev/null)
    fi

    # Fall back to session-based summary if task file has none
    if [[ -z "$summary" ]]; then
        summary="${SUMMARY_CACHE[$full_cwd]}"
    fi

    # Keywords from cache (may use session-specific file if available)
    local keywords="${KEYWORD_CACHE[$full_cwd]}"
    # If we have a specific session ID not matching the latest, extract separately
    # (skip this optimization — the latest file is good enough for most cases)

    # Clean display name: strip emoji prefix and trailing ● (inline)
    local display_name="$name"
    case "$display_name" in
        "🔄 "*) display_name="${display_name#🔄 }" ;;
        "⏸ "*)  display_name="${display_name#⏸ }" ;;
        "✅ "*) display_name="${display_name#✅ }" ;;
    esac
    display_name="${display_name% ●}"
    display_name="${display_name:0:24}"
    dir="${dir:0:20}"

    printf "%s\t%s\t${color} %s %-24s  %-20s  %5s  %.55s${stale_marker}${NC}\n" \
        "$target" "$keywords" "$st_col" "$display_name" "$dir" "$idle" "$summary"
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
