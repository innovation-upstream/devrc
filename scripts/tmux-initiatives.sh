#!/usr/bin/env bash
# Initiatives dashboard for tmux.
# Aggregates ongoing Claude sessions grouped by tmux session, showing the
# project name and fuzzyclaw's LLM-generated summary per window.
# Bound to Alt+i. Auto-refresh every REFRESH seconds. Dismiss with q/Esc.
#
# Visual:
#   🌳 grove                       3 sessions  1●  1🔄  1⏸
#      ● g:2  datapacket-talos     Permission needed: write nix/system/apply…
#      🔄 g:1  civitai-orch         Implementing batch retry handler for stuck jobs
#      ⏸ g:3  starters             Adding TypeScript template  (5h ago)
#
#   🌳 Gold                        2 sessions  1⏸
#      ...

REFRESH=${REFRESH:-3}
STALE_DAYS=${STALE_DAYS:-7}
STALE_SECS=$((STALE_DAYS * 86400))

# session : slot-key : color : title  (parallel order to scratch-status.sh)
SLOTS=(
    "scratch:g:#b8bb26:grove"
    "scratch2:G:#d79921:Gold"
    "scratch3:v:#b16286:violet"
    "scratch4:V:#83a598:Vapor"
    "scratch5:p:#cc241d:poppy"
    "scratch6:P:#689d6a:Pool"
)

ICON_RUN="🔄"
ICON_PAUSE="⏸"
ICON_WAIT="●"

COLOR_RUN="#b8bb26"
COLOR_PAUSE="#83a598"
COLOR_WAIT="#cc241d"
COLOR_OTHER="#a89984"

hex_to_rgb() {
    local h="${1#\#}"
    printf '%d %d %d' "$((16#${h:0:2}))" "$((16#${h:2:2}))" "$((16#${h:4:2}))"
}

ansi_fg() {
    local r g b
    IFS=' ' read -r r g b <<< "$(hex_to_rgb "$1")"
    printf '\033[38;2;%d;%d;%dm' "$r" "$g" "$b"
}

render_group() {
    local sess="$1" key="$2" title="$3" hex="$4" data="$5" smax="$6" now="$7"

    local lines
    lines=$(printf '%s\n' "$data" | awk -F'\t' -v s="$sess" '$1 == s')
    [[ -z "$lines" ]] && return 0

    # Counts in a single awk pass
    local counts
    counts=$(printf '%s\n' "$lines" | awk -F'\t' '
        { total++ }
        $3 == "waiting" { w++ }
        $3 == "running" { r++ }
        $3 == "paused"  { p++ }
        END { printf "%d\t%d\t%d\t%d", total+0, w+0, r+0, p+0 }
    ')
    local total waiting running paused
    IFS=$'\t' read -r total waiting running paused <<< "$counts"

    # Header: tree + group name (slot color), then dim count + colored badges
    local plural="s"; [[ $total -eq 1 ]] && plural=""
    printf '\033[1m%s🌳 %s\033[0m  \033[2m%d session%s\033[0m' \
        "$(ansi_fg "$hex")" "$title" "$total" "$plural"
    [[ $waiting -gt 0 ]] && printf '  %s\033[1m%d%s\033[0m' "$(ansi_fg "$COLOR_WAIT")"  "$waiting" "$ICON_WAIT"
    [[ $running -gt 0 ]] && printf '  %s\033[1m%d%s\033[0m' "$(ansi_fg "$COLOR_RUN")"   "$running" "$ICON_RUN"
    [[ $paused -gt 0 ]]  && printf '  %s\033[1m%d%s\033[0m' "$(ansi_fg "$COLOR_PAUSE")" "$paused"  "$ICON_PAUSE"
    printf '\n'

    # Sort: waiting > running > paused; within rank by last_activity desc
    local sorted
    sorted=$(printf '%s\n' "$lines" | awk -F'\t' '
        $3 == "waiting" { print "1\t" $0 }
        $3 == "running" { print "2\t" $0 }
        $3 == "paused"  { print "3\t" $0 }
    ' | sort -t$'\t' -k1,1n -k7,7r | cut -f2-)

    local stale_count=0
    while IFS=$'\t' read -r _sess wi status task summary lastact; do
        local icon icon_hex
        case "$status" in
            waiting) icon="$ICON_WAIT";  icon_hex="$COLOR_WAIT" ;;
            running) icon="$ICON_RUN";   icon_hex="$COLOR_RUN" ;;
            paused)  icon="$ICON_PAUSE"; icon_hex="$COLOR_PAUSE" ;;
            *)       icon="?";           icon_hex="#665c54" ;;
        esac

        # Compute age once per entry; reused for stale check and display.
        local then=0 diff=0
        if [[ -n "$lastact" ]]; then
            then=$(date -d "$lastact" +%s 2>/dev/null) || then=0
            [[ $then -gt 0 ]] && diff=$((now - then))
        fi

        # Stale paused: don't render, just tally for the footer line.
        if [[ "$status" == "paused" && $then -gt 0 && $diff -gt $STALE_SECS ]]; then
            ((stale_count++))
            continue
        fi

        # Single-line summary; replace embedded newlines with spaces.
        local sum="${summary//$'\n'/ }"
        if [[ ${#sum} -gt $smax ]]; then
            sum="${sum:0:$smax}…"
        fi

        # Label: scratch slots use the hotkey letter; other sessions use session name
        local label
        if [[ -n "$key" ]]; then
            label="$key:$wi"
        else
            label="$_sess:$wi"
        fi

        # Age for paused
        local age=""
        if [[ "$status" == "paused" && $then -gt 0 ]]; then
            local a
            if   [[ $diff -lt 3600 ]];  then a="$((diff / 60))m"
            elif [[ $diff -lt 86400 ]]; then a="$((diff / 3600))h"
            else a="$((diff / 86400))d"
            fi
            age="  \033[2m($a ago)\033[0m"
        fi

        printf '   %s%s\033[0m  \033[2m%-6s\033[0m  \033[1m%-22s\033[0m  \033[2m%s\033[0m%b\n' \
            "$(ansi_fg "$icon_hex")" "$icon" "$label" "$task" "$sum" "$age"
    done <<< "$sorted"

    if [[ $stale_count -gt 0 ]]; then
        printf '   %s%s\033[0m  \033[2m+%d paused >%dd (set STALE_DAYS to widen)\033[0m\n' \
            "$(ansi_fg "$COLOR_PAUSE")" "$ICON_PAUSE" "$stale_count" "$STALE_DAYS"
    fi

    printf '\n'
}

render() {
    printf '\033[H\033[2J'  # clear + home

    if ! command -v jq >/dev/null 2>&1 || ! compgen -G "$HOME/.tmux/tasks/*.json" >/dev/null; then
        printf '\033[2mfuzzyclaw task state unavailable (need jq + ~/.tmux/tasks/*.json)\033[0m\n'
        return
    fi

    local cols rows now current_wids data
    cols=$(tput cols 2>/dev/null || echo 80)
    rows=$(tput lines 2>/dev/null || echo 40)
    now=$(date +%s)
    current_wids=" $(tmux list-windows -a -F '#{window_id}' 2>/dev/null | tr '\n' ' ')"

    # TSV: session, window_index, status, task, summary, last_activity
    data=$(jq -r -s --arg wids "$current_wids" '
        map(. as $t | select(($wids | contains(" " + $t.window_id + " ")) and $t.status != "done"))
        | .[]
        | [
            .tmux_session,
            (.window_index | tostring),
            .status,
            .task,
            ((.summary // "") | gsub("[\\n\\r\\t]"; " ")),
            (.last_activity // "")
          ]
        | @tsv
    ' "$HOME"/.tmux/tasks/*.json 2>/dev/null)

    if [[ -z "$data" ]]; then
        printf '\033[2mNo ongoing initiatives.\033[0m\n'
        return
    fi

    local smax=$((cols - 40))
    [[ $smax -lt 25 ]] && smax=25

    local rendered=""
    for slot in "${SLOTS[@]}"; do
        IFS=':' read -r sess key color title <<< "$slot"
        render_group "$sess" "$key" "$title" "$color" "$data" "$smax" "$now"
        rendered+=" $sess "
    done

    # Other sessions (non-scratch) — group each one
    local others
    others=$(printf '%s\n' "$data" | awk -F'\t' '{print $1}' | sort -u | awk -v r="$rendered" '
        { wrapped = " " $0 " "; if (index(r, wrapped) == 0) print }
    ')
    if [[ -n "$others" ]]; then
        while read -r sess; do
            [[ -z "$sess" ]] && continue
            render_group "$sess" "" "$sess" "$COLOR_OTHER" "$data" "$smax" "$now"
        done <<< "$others"
    fi

    printf '\033[2m[q/Esc: dismiss · auto-refresh %ss]\033[0m' "$REFRESH"
}

# Hide cursor; restore on exit
trap 'printf "\033[?25h\n"; exit 0' INT TERM EXIT
printf '\033[?25l'

while true; do
    render
    if IFS= read -t "$REFRESH" -rsn 1 key; then
        case "$key" in
            q|Q) exit 0 ;;
            $'\e')
                if ! IFS= read -t 0.05 -rsn 2 _trailing; then
                    exit 0
                fi
                ;;
        esac
    fi
done
