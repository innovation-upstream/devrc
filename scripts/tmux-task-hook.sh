#!/usr/bin/env bash
# tmux-task-hook.sh - Claude Code Stop hook for tmux task management
# Updates window name with status prefix and writes task state file
# Called by Claude Code on every Stop event (when Claude finishes responding)

INPUT=$(cat)
TASK_DIR="${HOME}/.tmux/tasks"
mkdir -p "$TASK_DIR"

# Skip if not in tmux
[[ -z "$TMUX_PANE" ]] && exit 0

# Skip if hook is re-triggering
HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[[ "$HOOK_ACTIVE" == "true" ]] && exit 0

# Get tmux window info from the pane running this Claude instance
WIN_ID=$(tmux display-message -t "$TMUX_PANE" -p '#{window_id}' 2>/dev/null) || exit 0
WIN_NAME=$(tmux display-message -t "$TMUX_PANE" -p '#{window_name}' 2>/dev/null) || exit 0
SESSION_NAME=$(tmux display-message -t "$TMUX_PANE" -p '#{session_name}' 2>/dev/null) || exit 0
WIN_IDX=$(tmux display-message -t "$TMUX_PANE" -p '#{window_index}' 2>/dev/null) || exit 0
CWD_DIR=$(tmux display-message -t "$TMUX_PANE" -p '#{pane_current_path}' 2>/dev/null) || exit 0

# Extract data from event
CLAUDE_SESSION=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null)
LAST_MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // ""' 2>/dev/null | head -c 200 | tr '\n' ' ')
NOW=$(date -Iseconds)

# Strip known status emoji prefixes only, preserve everything else
TASK_NAME=$(echo "$WIN_NAME" | sed -E 's/^(🔄|⏸|✅) //')
# Strip trailing Claude indicator (or bare ●)
TASK_NAME=$(echo "$TASK_NAME" | sed -e 's/ ●$//' -e 's/^●$//')
# If empty after stripping, fall back to directory basename
[[ -z "$TASK_NAME" ]] && TASK_NAME=$(basename "$CWD_DIR")

# Task state file keyed by tmux window ID (strip @ and % chars for filename)
TASK_FILE="${TASK_DIR}/${WIN_ID//[@%]/}.json"

# Read existing task state to preserve the original task name and start time
ORIG_TASK="$TASK_NAME"
STARTED="$NOW"
if [[ -f "$TASK_FILE" ]]; then
    ORIG_TASK=$(jq -r '.task // ""' "$TASK_FILE" 2>/dev/null)
    STARTED=$(jq -r '.started // ""' "$TASK_FILE" 2>/dev/null)
    # Fall back to extracted name if stored task is empty or just the Claude indicator
    [[ -z "$ORIG_TASK" || "$ORIG_TASK" == "●" ]] && ORIG_TASK="$TASK_NAME"
    [[ -z "$STARTED" ]] && STARTED="$NOW"
fi

# Update window name with paused status prefix
tmux rename-window -t "$WIN_ID" "⏸ ${ORIG_TASK}" 2>/dev/null

# Write task state file
jq -n \
    --arg task "$ORIG_TASK" \
    --arg win_id "$WIN_ID" \
    --arg session "$SESSION_NAME" \
    --arg win_idx "$WIN_IDX" \
    --arg status "paused" \
    --arg cwd "$CWD_DIR" \
    --arg claude_session "$CLAUDE_SESSION" \
    --arg started "$STARTED" \
    --arg last_activity "$NOW" \
    --arg summary "$LAST_MSG" \
    '{
        task: $task,
        window_id: $win_id,
        tmux_session: $session,
        window_index: ($win_idx | tonumber),
        status: $status,
        cwd: $cwd,
        claude_session: $claude_session,
        started: $started,
        last_activity: $last_activity,
        summary: $summary
    }' > "$TASK_FILE"

exit 0
