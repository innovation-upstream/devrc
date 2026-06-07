#!/usr/bin/env bash
# tmux-task-resume.sh - PreToolUse hook to mark window as active
# Sets JSON task status paused → running on the first tool call after Claude
# resumes work. Does not rename the window (the tab tracks cwd via auto-rename).
command -v fuzzyclaw &>/dev/null && exec fuzzyclaw hook resume

# Fallback: inline implementation if binary not in PATH
[[ -z "$TMUX_PANE" ]] && exit 0

WIN_ID=$(tmux display-message -t "$TMUX_PANE" -p '#{window_id}' 2>/dev/null) || exit 0
TASK_FILE="${HOME}/.tmux/tasks/${WIN_ID//[@%]/}.json"
[[ -f "$TASK_FILE" ]] || exit 0

# Mark the task running again. Status is the single source of truth read by the
# dashboard, counters, and scratch indicator — the window name is left to tmux
# automatic-rename so the tab keeps tracking cwd.
TMP=$(mktemp)
if jq --arg now "$(date -Iseconds)" \
      '.status = "running" | .last_activity = $now' "$TASK_FILE" > "$TMP" 2>/dev/null; then
    mv "$TMP" "$TASK_FILE"
else
    rm -f "$TMP"
fi

exit 0
