#!/usr/bin/env bash
# tmux-idle-color.sh - Calculate window tab color based on idle time
# Called by tmux window-status-format for each non-current window

WINDOW_IDX="$1"
WINDOW_NAME="$2"
WINDOW_FLAGS="$3"
BELL_FLAG="$4"           # "1" if bell active, "0" otherwise
WINDOW_ACTIVITY="$5"     # Unix timestamp of last activity

# Strip '#' activity flag - we show activity via color instead
WINDOW_FLAGS="${WINDOW_FLAGS//#/}"

# Calculate idle time from last activity
if [[ -z "$WINDOW_ACTIVITY" || "$WINDOW_ACTIVITY" == "0" ]]; then
  IDLE_SECS=99999
else
  NOW=$(date +%s)
  IDLE_SECS=$((NOW - WINDOW_ACTIVITY))
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

# Bell overrides idle color with bright magenta + bold
STYLE="fg=$COLOR,bg=default"
if [[ "$BELL_FLAG" == "1" ]]; then
  STYLE="fg=#d3869b,bg=default,bold"  # bright magenta - demands attention
fi

# Output formatted window tab
echo "#[$STYLE] $WINDOW_IDX:$WINDOW_NAME$WINDOW_FLAGS "
