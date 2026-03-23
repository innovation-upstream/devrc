#!/usr/bin/env bash
# tmux-task-resume.sh - PreToolUse hook to mark window as active
# Flips ⏸ → 🔄 on the first tool call after Claude resumes work
# Idempotent: skips if already 🔄 or no ⏸ prefix

[[ -z "$TMUX_PANE" ]] && exit 0

WIN_NAME=$(tmux display-message -t "$TMUX_PANE" -p '#{window_name}' 2>/dev/null) || exit 0

case "$WIN_NAME" in
    "⏸ "*)
        tmux rename-window -t "$TMUX_PANE" "🔄 ${WIN_NAME#⏸ }" 2>/dev/null
        ;;
esac

exit 0
