#!/usr/bin/env bash
# Toggle picom background blur on/off via SIGUSR1 config re-read.
# State file tracks the current blur state across restarts.
set -euo pipefail

STATE_FILE="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/picom-blur-state"
PICOM_CONF="${HOME}/.config/picom/picom.conf"

if [[ ! -f "$PICOM_CONF" ]]; then
    notify-send -u critical "Blur toggle" "picom.conf not found"
    exit 1
fi

if [[ -f "$STATE_FILE" ]] && grep -q "off" "$STATE_FILE"; then
    CURRENT="off"
else
    CURRENT="on"
fi

if [[ "$CURRENT" == "on" ]]; then
    sed -i 's/strength = [0-9]*/strength = 0/' "$PICOM_CONF"
    echo "off" > "$STATE_FILE"
    notify-send -t 1500 " " "Blur off"
else
    sed -i 's/strength = [0-9]*/strength = 5/' "$PICOM_CONF"
    echo "on" > "$STATE_FILE"
    notify-send -t 1500 " " "Blur on"
fi

pkill -USR1 picom || true
