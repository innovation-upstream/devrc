#!/usr/bin/env bash
# tmux-activity-emit.sh — thin activity-telemetry shipper for tmux.
#
# Fired from tmux focus-change hooks (after-select-window / client-session-changed).
# Reads the focused window/session straight from tmux (and the existing per-window
# task state in ~/.tmux/tasks/*.json when present) and `emit`s ONE event to the
# activity spool. The collector daemon ships it to ClickHouse.
#
# This REUSES the existing tmux activity machinery (pipe-activity / tasks); it does
# not rebuild any tracking — it only forwards focus changes into the telemetry spool.
#
# Usage: tmux-activity-emit.sh <kind>     where kind = window-focus | session

set -u

EMIT="${HOME}/.config/activity-collector/emit"
[[ -x "$EMIT" ]] || exit 0

KIND="${1:-window-focus}"

# Pull focus context from tmux. If not in a tmux client context, bail quietly.
read -r SESSION WINDOW_ID WINDOW_INDEX WINDOW_NAME PANE_PATH < <(
    tmux display-message -p '#{session_name} #{window_id} #{window_index} #{window_name} #{pane_current_path}' 2>/dev/null
) || exit 0
[[ -n "${SESSION:-}" ]] || exit 0

CWD="${PANE_PATH:-$PWD}"

# project = git repo basename of the focused pane's cwd, else cwd basename, else "".
PROJECT=""
if root=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null); then
    PROJECT="${root##*/}"
elif [[ -n "$CWD" ]]; then
    PROJECT="${CWD##*/}"
fi

# Reuse existing task state if this window has one (task name + status enrich the
# payload without rebuilding anything).
TASK_JSON="${HOME}/.tmux/tasks/${SESSION}.json"
PAYLOAD_ARGS=( "window_id=${WINDOW_ID}" "window_index=${WINDOW_INDEX}" )
if [[ -f "$TASK_JSON" ]]; then
    PAYLOAD_ARGS+=( "task_file=${SESSION}.json" )
fi

"$EMIT" \
    source=tmux "kind=${KIND}" \
    "b64:text=${WINDOW_NAME}" "b64:cwd=${CWD}" \
    "b64:project=${PROJECT}" "b64:session=${SESSION}" \
    "b64:app=tmux" \
    "${PAYLOAD_ARGS[@]}"
