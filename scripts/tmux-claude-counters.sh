#!/usr/bin/env bash
# Aggregate Claude-window counters for tmux status-right.
#
# Output: " 2🔄 3⏸ 1● "  — total running / paused / waiting Claude windows
# across all tmux sessions, with each segment dim-gray when zero so the
# bar layout stays fixed-width.
#
# Counts are sourced from fuzzyclaw's per-window task files
# (~/.tmux/tasks/*.json) and filtered against currently-existing tmux
# window IDs so closed-but-not-cleaned-up windows don't inflate the
# numbers.

if ! command -v jq >/dev/null 2>&1; then exit 0; fi
if ! compgen -G "$HOME/.tmux/tasks/*.json" >/dev/null; then exit 0; fi

current_wids=" $(tmux list-windows -a -F '#{window_id}' 2>/dev/null | tr '\n' ' ')"

counts=$(jq -r -s --arg wids "$current_wids" '
    map(. as $t | select($wids | contains(" " + $t.window_id + " ")))
    | reduce .[] as $t (
        {running: 0, paused: 0, waiting: 0};
        if   $t.status == "running" then .running += 1
        elif $t.status == "paused"  then .paused  += 1
        elif $t.status == "waiting" then .waiting += 1
        else . end
      )
    | "\(.running) \(.paused) \(.waiting)"
' "$HOME"/.tmux/tasks/*.json 2>/dev/null)

read -r running paused waiting <<< "$counts"

# Active color when count > 0; dim gray when 0
color_for() {
    [[ "$1" == "0" ]] && echo "#665c54" || echo "$2"
}

printf "#[fg=%s]%d🔄#[default] #[fg=%s]%d⏸#[default] #[fg=%s]%d●#[default]" \
    "$(color_for "$running" "#b8bb26")" "$running" \
    "$(color_for "$paused"  "#83a598")" "$paused" \
    "$(color_for "$waiting" "#cc241d")" "$waiting"
