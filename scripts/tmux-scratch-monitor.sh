#!/usr/bin/env bash
# Monitor popup: live HUD of the last N lines from all 12 scratch sessions.
# Bound to Alt+m. Auto-refreshes every REFRESH seconds. Dismiss with q/Esc.
#
# Each section renders:
#   ── grove                    (header in slot color; dim if session not started)
#   <last per_section lines of capture-pane>
#
# Per-section line count adapts to popup height so all slots fit without scroll.

REFRESH=${REFRESH:-2}

# The scratchpad slot table (session:key:color:name) is the ONE source of truth in
# scratch-slots.sh — sourced here so this HUD, the Alt+i dashboard, the status-left
# legend, and initiative-scan.py can't drift.
_d="$(dirname "$0")"
if   [ -f "$_d/scratch-slots.sh" ];      then . "$_d/scratch-slots.sh"
elif [ -f "$_d/tmux-scratch-slots.sh" ]; then . "$_d/tmux-scratch-slots.sh"
fi

hex_to_rgb() {
    local h="${1#\#}"
    printf '%d %d %d' "$((16#${h:0:2}))" "$((16#${h:2:2}))" "$((16#${h:4:2}))"
}

render() {
    local rows cols per_section nslots
    rows=$(tput lines 2>/dev/null || echo 40)
    cols=$(tput cols 2>/dev/null || echo 80)
    nslots=${#SCRATCH_SLOTS[@]}

    # Reserve 1 header line + 1 blank line per section + 1 footer line.
    # Available content = rows - (nslots headers + nslots blanks + 1 footer)
    per_section=$(( (rows - (2 * nslots + 1)) / nslots ))
    [[ $per_section -lt 2 ]] && per_section=2

    printf '\033[H\033[2J'  # clear + home

    for slot in "${SCRATCH_SLOTS[@]}"; do
        IFS=':' read -r sess key color name <<< "$slot"
        IFS=' ' read -r r g b <<< "$(hex_to_rgb "$color")"

        if tmux has-session -t "$sess" 2>/dev/null; then
            printf "\033[1;38;2;%d;%d;%dm── %s   %s\033[0m\n" "$r" "$g" "$b" "$key" "$name"
            # Capture enough scrollback to find Claude's input-box boundary,
            # then take last per_section lines of REAL content (chrome stripped).
            # Chrome is two ─────── separator lines wrapping the input box,
            # followed by the model+ctx status bar. Anything above the upper
            # separator is conversation. Non-Claude panes (no separator pair
            # near the bottom) fall through and tail normally.
            tmux capture-pane -t "$sess" -p -S -50 2>/dev/null \
                | awk -v per="$per_section" '
                    { lines[NR] = $0 }
                    END {
                        last_sep = 0; prev_sep = 0
                        for (i = NR; i > NR - 15 && i > 0; i--) {
                            if (lines[i] ~ /^[[:space:]]*─{20,}/) {
                                if (last_sep == 0) { last_sep = i }
                                else { prev_sep = i; break }
                            }
                        }
                        chrome_top = (prev_sep > 0) ? prev_sep \
                                  : (last_sep > 0 ? last_sep : NR + 1)
                        end = chrome_top - 1
                        while (end >= 1 && lines[end] ~ /^[[:space:]]*$/) end--
                        start = end - per + 1
                        if (start < 1) start = 1
                        for (i = start; i <= end; i++) print lines[i]
                    }
                '
        else
            printf "\033[2;38;2;%d;%d;%dm── %s   %s (not started)\033[0m\n" \
                "$r" "$g" "$b" "$key" "$name"
        fi
        printf '\n'
    done

    printf '\033[2m[q/Esc: dismiss · auto-refresh %ss]\033[0m' "$REFRESH"
}

# Hide cursor while running; restore on exit
trap 'printf "\033[?25h\n"; exit 0' INT TERM EXIT
printf '\033[?25l'

while true; do
    render
    # Wait REFRESH seconds for a keypress; on key, decide what to do.
    if IFS= read -t "$REFRESH" -rsn 1 key; then
        case "$key" in
            q|Q) exit 0 ;;
            $'\e')
                # plain Esc (no follow-up) → dismiss; if it's an arrow/escape seq,
                # drain it without acting
                if ! IFS= read -t 0.05 -rsn 2 _trailing; then
                    exit 0
                fi
                ;;
        esac
    fi
done
