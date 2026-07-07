#!/usr/bin/env bash
# Canonical scratchpad slot table — THE single source of truth for the
# session <-> hotkey <-> color <-> codename mapping, mirroring the tmux/i3
# scratchpad bindings ($mod+Shift+V -> tmux session `scratch4` -> codename `Vapor`).
#
# This file is SOURCED (not executed). Consumers — keep in sync by reading THIS,
# never a private copy:
#   - tmux-scratch-monitor.sh   (Alt+m HUD)
#   - tmux-scratch-status.sh    (status-left legend)
#   - scripts/session-analysis/initiative-scan.py  (parses this file's entries)
#
# Field order per entry:  session:key:color:name
#   session — tmux session name (also the emit key)
#   key     — hotkey letter ($mod+Shift+<key>)
#   color   — hex, matches the popup border color in .tmux.conf
#   name    — human codename shown in the HUD / ledger
# Consumers source this by looking in their own dir for the deployed name first,
# then the repo name (they run from ~/.config/tmux/ deployed, or scripts/ in-repo):
#   _d="$(dirname "$0")"
#   if   [ -f "$_d/scratch-slots.sh" ];      then . "$_d/scratch-slots.sh"
#   elif [ -f "$_d/tmux-scratch-slots.sh" ]; then . "$_d/tmux-scratch-slots.sh"; fi
SCRATCH_SLOTS=(
    "scratch:g:#b8bb26:grove"
    "scratch2:G:#d79921:Gold"
    "scratch3:v:#b16286:violet"
    "scratch4:V:#83a598:Vapor"
    "scratch5:p:#cc241d:poppy"
    "scratch6:P:#689d6a:Pool"
    "scratch7:o:#fe8019:orange"
    "scratch8:O:#d3869b:Orchid"
    "scratch9:n:#458588:navy"
    "scratch10:N:#928374:Nickel"
    "scratch11:w:#ebdbb2:wheat"
    "scratch12:W:#af3a03:Walnut"
)
