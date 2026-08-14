#!/usr/bin/env bash
# Scratch slot indicator for tmux status-left.
# Renders the 20 scratch slots as their hotkey letter, colored to match the
# popup border color set in .tmux.conf, so the status bar acts as a legend
# mapping popup color -> hotkey. First 6 (g/G/v/V/p/P) are excluded — the
# original slots predate the legend and don't need visual reminder.
#
# 🔴 THE ● WAITING MARKER IS GONE, and this is the note that stops it coming
# back. It keyed on fuzzyclaw's `status == "waiting"`, and that field could not
# answer: measured 2026-08-14 across 407 task files — 301 `done`, 87 `paused`,
# 18 `running`, and exactly ONE `waiting`. So the marker rendered nothing while
# `session-manager` measured FIVE windows probably waiting on the operator
# (1 context-exhausted, 1 selection-menu, 3 trailing-question). A marker that
# says "nothing needs you" when five things do is worse than no marker.
#
# It was NOT migrated to the agent activity ledger's waiting signal, which is
# strictly better: the operator states they do not use this marker. A surface
# nobody reads does not earn a 45s timer and a cache — and the honest fix for a
# dead lying indicator is deletion, not a better lie. See
# claudedocs/spec-agent-activity-ledger.md §5 for the inversion this declines.
#
# The LEGEND is deliberately kept: it maps popup colour -> hotkey, which is a
# different feature that never depended on fuzzyclaw.
#
# Output example:
#   o O n N w W m M i I u U y Y     — all slots exist
#
# Scratchpad slot table (session:key:color:name) — sourced from the ONE source of
# truth in scratch-slots.sh, then joined for awk (the name field is ignored here).
_d="$(dirname "$0")"
if   [ -f "$_d/scratch-slots.sh" ];      then . "$_d/scratch-slots.sh"
elif [ -f "$_d/tmux-scratch-slots.sh" ]; then . "$_d/tmux-scratch-slots.sh"
fi
slots_str="$(printf '%s ' "${SCRATCH_SLOTS[@]}")"

tmux list-sessions -F '#{session_name}' 2>/dev/null \
  | awk -v slots_str="$slots_str" '
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
                style = color[s] ",bold"                               # slot color, bold
            } else {
                style = "#504945"                                      # dim: session not started
            }
            printf "%s#[fg=%s]%s#[default]", sep, style, key[s]
            sep = " "
        }
    }
  '
