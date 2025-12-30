#!/usr/bin/env bash
# tmux-idle-color.sh - Calculate window tab color based on idle time
# Called by tmux window-status-format for each non-current window

WINDOW_IDX="$1"
WINDOW_NAME="$2"
WINDOW_FLAGS="$3"
WINDOW_ID="$4"           # Window ID (e.g., @1) for activity file lookup

# Strip '#' activity flag and other noise
WINDOW_FLAGS="${WINDOW_FLAGS//#/}"

# Read last activity timestamp from file (set by pipe-pane receiver)
ACTIVITY_DIR="${HOME}/.tmux/activity"
ACTIVITY_FILE="${ACTIVITY_DIR}/${WINDOW_ID//[@%]/}"

if [[ -f "$ACTIVITY_FILE" ]]; then
  LAST_ACTIVITY=$(cat "$ACTIVITY_FILE" 2>/dev/null)
  NOW=$(date +%s)
  IDLE_SECS=$((NOW - LAST_ACTIVITY))
else
  # No activity file = window hasn't had background output yet
  IDLE_SECS=99999
fi

# Color thresholds (Gruvbox palette)
# < 5min = green, 5-10min = yellow, 10-30min = orange, 30-60min = red, >60min = gray
if (( IDLE_SECS < 300 )); then
  COLOR="#98971a"  # green - recently active
elif (( IDLE_SECS < 600 )); then
  COLOR="#d79921"  # yellow - getting stale
elif (( IDLE_SECS < 1800 )); then
  COLOR="#d65d0e"  # orange - idle
elif (( IDLE_SECS < 3600 )); then
  COLOR="#cc241d"  # red - very idle
else
  COLOR="#665c54"  # dim gray - dormant
fi

# Output formatted window tab
echo "#[fg=$COLOR,bg=default] $WINDOW_IDX:$WINDOW_NAME$WINDOW_FLAGS "
