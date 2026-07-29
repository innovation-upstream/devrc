#!/usr/bin/env bash
# Scratch slot indicator for tmux status-left.
# Renders the 20 scratch slots as their hotkey letter, colored to match the
# popup border color set in .tmux.conf, so the status bar acts as a legend
# mapping popup color -> hotkey. First 6 (g/G/v/V/p/P) are excluded — the
# original slots predate the legend and don't need visual reminder.
#
# A leading ● flags slots that have a window waiting for user input
# (status="waiting" in fuzzyclaw's task state, filtered against currently
# existing tmux windows so stale entries don't trigger).
#
# Output examples:
#   o O n N w W m M i I u U y Y     — all slots exist, nothing waiting
#   o ●O n N w W m M i I u U y Y    — Orchid has a waiting prompt

# Scratchpad slot table (session:key:color:name) — sourced from the ONE source of
# truth in scratch-slots.sh, then joined for awk (the name field is ignored here).
_d="$(dirname "$0")"
if   [ -f "$_d/scratch-slots.sh" ];      then . "$_d/scratch-slots.sh"
elif [ -f "$_d/tmux-scratch-slots.sh" ]; then . "$_d/tmux-scratch-slots.sh"
fi
slots_str="$(printf '%s ' "${SCRATCH_SLOTS[@]}")"

# Sessions with at least one window waiting for input, space-padded for
# substring matching in awk. Empty if jq / task files are missing.
waiting=""
if command -v jq >/dev/null 2>&1 && compgen -G "$HOME/.tmux/tasks/*.json" >/dev/null; then
    current_wids=" $(tmux list-windows -a -F '#{window_id}' 2>/dev/null | tr '\n' ' ')"
    waiting=" $(jq -r -s --arg wids "$current_wids" '
        map(. as $t | select($t.status == "waiting" and ($wids | contains(" " + $t.window_id + " "))))
        | map(.tmux_session)
        | unique
        | join(" ")
    ' "$HOME"/.tmux/tasks/*.json 2>/dev/null) "
fi

tmux list-sessions -F '#{session_name}' 2>/dev/null \
  | awk -v waiting="$waiting" -v slots_str="$slots_str" '
    BEGIN {
        # session:key:color:name from scratch-slots.sh (name unused here).
        n = split(slots_str, slots, " ")
        for (i = 1; i <= n; i++) {
            split(slots[i], p, ":")
            sess[i]      = p[1]
            key[p[1]]    = p[2]
            color[p[1]]  = p[3]
        }
    }
    { exists[$1] = 1 }
    END {
        sep = ""
        for (i = 7; i <= n; i++) {
            s = sess[i]
            if (s in exists) {
                style  = color[s] ",bold"                              # slot color, bold
                marker = (index(waiting, " " s " ") > 0) ? "●" : ""    # waiting indicator
            } else {
                style  = "#504945"                                     # dim: session not started
                marker = ""
            }
            # Single color-format printf for both states (deduped).
            printf "%s#[fg=%s]%s%s#[default]", sep, style, marker, key[s]
            sep = " "
        }
    }
  '
