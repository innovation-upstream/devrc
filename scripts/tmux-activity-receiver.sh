#!/usr/bin/env bash
# tmux-activity-receiver.sh - Receives piped pane output and updates timestamp
# Called by pipe-pane, receives window_id as argument

WINDOW_ID="$1"
ACTIVITY_DIR="${HOME}/.tmux/activity"
ACTIVITY_FILE="${ACTIVITY_DIR}/${WINDOW_ID//[@%]/}"

mkdir -p "$ACTIVITY_DIR"

# Update timestamp on any input, throttled to once per second max
last_update=0
while IFS= read -r line; do
    now=$(date +%s)
    if (( now > last_update )); then
        echo "$now" > "$ACTIVITY_FILE"
        last_update=$now
    fi
done
