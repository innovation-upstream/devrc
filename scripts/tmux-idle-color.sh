#!/usr/bin/env bash
# tmux-idle-color.sh - Calculate window tab color based on idle time
# Called by tmux window-status-format for each non-current window

WINDOW_IDX="$1"
WINDOW_NAME="$2"
WINDOW_FLAGS="$3"
BELL_FLAG="$4"  # "1" if bell active, "0" otherwise

# Get last focused timestamp for this specific window
LAST_FOCUSED=$(tmux display-message -t ":$WINDOW_IDX" -p '#{@window_last_focused}' 2>/dev/null)

# Default to now if no timestamp (newly created window)
if [[ -z "$LAST_FOCUSED" || "$LAST_FOCUSED" == "" ]]; then
  LAST_FOCUSED=$(date +%s)
fi

NOW=$(date +%s)
IDLE_SECS=$((NOW - LAST_FOCUSED))

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

# Bell overrides idle color with bright magenta + bold
STYLE="fg=$COLOR"
if [[ "$BELL_FLAG" == "1" ]]; then
  STYLE="fg=#d3869b,bold"  # bright magenta - demands attention
fi

# Output formatted window tab
echo "#[$STYLE] $WINDOW_IDX:$WINDOW_NAME$WINDOW_FLAGS "
