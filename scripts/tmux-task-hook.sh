#!/usr/bin/env bash
# tmux-task-hook.sh - Claude Code Stop hook (thin wrapper for fuzzyclaw)
# Called by Claude Code on every Stop event
command -v fuzzyclaw &>/dev/null && exec fuzzyclaw hook stop

# Fallback: inline implementation if binary not in PATH
INPUT=$(cat)
TASK_DIR="${HOME}/.tmux/tasks"
mkdir -p "$TASK_DIR"

[[ -z "$TMUX_PANE" ]] && exit 0

HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[[ "$HOOK_ACTIVE" == "true" ]] && exit 0

WIN_ID=$(tmux display-message -t "$TMUX_PANE" -p '#{window_id}' 2>/dev/null) || exit 0
WIN_NAME=$(tmux display-message -t "$TMUX_PANE" -p '#{window_name}' 2>/dev/null) || exit 0
SESSION_NAME=$(tmux display-message -t "$TMUX_PANE" -p '#{session_name}' 2>/dev/null) || exit 0
WIN_IDX=$(tmux display-message -t "$TMUX_PANE" -p '#{window_index}' 2>/dev/null) || exit 0
CWD_DIR=$(tmux display-message -t "$TMUX_PANE" -p '#{pane_current_path}' 2>/dev/null) || exit 0

CLAUDE_SESSION=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null)
LAST_MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // ""' 2>/dev/null | head -c 200 | tr '\n' ' ')
NOW=$(date -Iseconds)

TASK_NAME=$(echo "$WIN_NAME" | sed -E 's/^(🔄|⏸|✅) //')
TASK_NAME=$(echo "$TASK_NAME" | sed -e 's/ ●$//' -e 's/^●$//')
[[ -z "$TASK_NAME" ]] && TASK_NAME=$(basename "$CWD_DIR")

TASK_FILE="${TASK_DIR}/${WIN_ID//[@%]/}.json"

ORIG_TASK="$TASK_NAME"
STARTED="$NOW"
if [[ -f "$TASK_FILE" ]]; then
    ORIG_TASK=$(jq -r '.task // ""' "$TASK_FILE" 2>/dev/null)
    STARTED=$(jq -r '.started // ""' "$TASK_FILE" 2>/dev/null)
    [[ -z "$ORIG_TASK" || "$ORIG_TASK" == "●" ]] && ORIG_TASK="$TASK_NAME"
    [[ -z "$STARTED" ]] && STARTED="$NOW"
fi

tmux rename-window -t "$WIN_ID" "⏸ ${ORIG_TASK}" 2>/dev/null

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
