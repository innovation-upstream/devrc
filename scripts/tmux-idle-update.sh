#!/usr/bin/env bash
# tmux-idle-update.sh - Batch update all window tab colors based on idle time
# Called once per status-interval from status-right, replacing per-window #() calls
# Sets window-status-format directly on each non-current window

ACTIVITY_DIR="${HOME}/.tmux/activity"
mkdir -p "$ACTIVITY_DIR"

NOW=$(date +%s)
SESSION=$(tmux display-message -p '#{session_name}')
ACTIVE_WIN=$(tmux display-message -p '#{window_id}')

# Update colors for all non-current windows in this session
while IFS='|' read -r win_id win_idx win_name win_flags bell_flag; do
    [[ "$win_id" == "$ACTIVE_WIN" ]] && continue

    # Read activity timestamp
    activity_file="${ACTIVITY_DIR}/${win_id//[@%]/}"
    if [[ -f "$activity_file" ]]; then
        last_activity=$(< "$activity_file")
        idle_secs=$((NOW - last_activity))
    else
        idle_secs=99999
    fi

    # Color thresholds (Gruvbox palette - 8-color scale)
    if (( idle_secs < 600 )); then
        color="#b8bb26"   # bright green - fresh (<10 min)
    elif (( idle_secs < 1800 )); then
        color="#98971a"   # green - active (10-30 min)
    elif (( idle_secs < 3600 )); then
        color="#689d6a"   # aqua - warm (30-60 min)
    elif (( idle_secs < 7200 )); then
        color="#d79921"   # yellow - cooling (1-2 hr)
    elif (( idle_secs < 14400 )); then
        color="#d65d0e"   # orange - idle (2-4 hr)
    elif (( idle_secs < 28800 )); then
        color="#cc241d"   # red - stale (4-8 hr)
    elif (( idle_secs < 86400 )); then
        color="#b16286"   # purple - dormant (8-24 hr)
    else
        color="#665c54"   # gray - ancient (>24 hr)
    fi

    # Bell override
    if [[ "$bell_flag" == "1" ]]; then
        style="fg=$color,bg=default,bold"
    else
        style="fg=$color,bg=default"
    fi

    # Strip activity '#' flag
    win_flags="${win_flags//#/}"

    tmux set-window-option -t "$win_id" window-status-format \
        "#[$style] ${win_idx}:${win_name}${win_flags} " 2>/dev/null
done < <(tmux list-windows -t "$SESSION" -F '#{window_id}|#{window_index}|#{window_name}|#{window_flags}|#{window_bell_flag}')

# Cleanup orphan activity files (throttled to once per 60s)
cleanup_marker="${ACTIVITY_DIR}/.last_cleanup"
do_cleanup=0
if [[ -f "$cleanup_marker" ]]; then
    last_cleanup=$(< "$cleanup_marker")
    (( NOW - last_cleanup > 60 )) && do_cleanup=1
else
    do_cleanup=1
fi

if (( do_cleanup )); then
    echo "$NOW" > "$cleanup_marker"
    # Collect all valid window IDs across all sessions
    declare -A valid
    while read -r wid; do
        valid["${wid//[@%]/}"]=1
    done < <(tmux list-windows -a -F '#{window_id}')

    for f in "$ACTIVITY_DIR"/*; do
        [[ -f "$f" ]] || continue
        fname=$(basename "$f")
        [[ "$fname" == .* ]] && continue  # skip dotfiles (state files)
        if [[ -z "${valid[$fname]+x}" ]]; then
            rm -f "$f"
        fi
    done
fi
