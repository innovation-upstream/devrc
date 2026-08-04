#!/usr/bin/env bash
# tmux-post-save.sh — tmux-resurrect @resurrect-hook-post-save callback.
#
# Runs tmux-session-restore.py save in the background so the hook never blocks
# continuum's own save cycle.  Captures the claude-session-to-window mapping
# every 15 minutes (continuum's save interval), keeping the restore plan fresh.
#
# Deployed to ~/.config/tmux/tmux-post-save.sh via home-manager.
set -euo pipefail

RESTORE_SCRIPT="${HOME}/workspace/devrc/scripts/tmux-session-restore.py"
LOG="${HOME}/.cache/tmux-session-restore.log"

mkdir -p "$(dirname "$LOG")"

# Bail if the script doesn't exist (new host, before first deploy)
[[ -x "$RESTORE_SCRIPT" ]] || exit 0

# Run save in background; stdout/stderr to log.
# disown so the hook's shell can exit cleanly.
"$RESTORE_SCRIPT" save >>"$LOG" 2>&1 &
disown
