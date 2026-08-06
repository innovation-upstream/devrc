#!/usr/bin/env bash
# rig-control — a single-toggle sleep/wake panel (and CLI) for two workbench
# subsystems:
#   * chassis RGB on/off  (MSI motherboard headers, OpenRGB device 2 — scoped so
#                          the keyboard/mouse are never touched)
#   * monitor blackout / restore  (DDC-CI backlight; delegates to monitor-blackout.sh)
#
# GUI: a single dark-themed yad button that toggles between sleep and wake.
# The dialog closes after each action so the label refreshes on next open.
# State is tracked in a file so the bar block can read it too.
#
# Usage:
#   rig-control            # open the yad panel (default)
#   rig-control sleep      # rgb-off + blackout (non-blocking fade)
#   rig-control wake       # rgb-on + restore (non-blocking fade)
#   rig-control status     # print "sleeping" or "awake"
#   rig-control gui        # same as bare invocation
#
# Legacy aliases (kept for backward compat / bar scripts):
#   rig-control rgb-on     = wake (RGB only)
#   rig-control rgb-off    = sleep (RGB only)
#   rig-control blackout   = sleep (monitor only)
#   rig-control restore    = wake (monitor only)
set -euo pipefail

SELF="$(readlink -f "$0")"
DIR="$(dirname "$SELF")"

# --- config ------------------------------------------------------------------
RGB_DEVICE="${RIG_RGB_DEVICE:-2}"
RGB_ON_MODE="${RIG_RGB_ON_MODE:-static}"
RGB_ON_COLOR="${RIG_RGB_ON_COLOR:-FFFFFF}"
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/rig-control"
STATE_FILE="$STATE_DIR/state"

# --- helpers -----------------------------------------------------------------
mkdir -p "$STATE_DIR"

notify() {
  command -v notify-send >/dev/null 2>&1 && notify-send -t 2500 "rig-control" "$1" 2>/dev/null || true
}

is_asleep() { [[ -f "$STATE_FILE" ]] && read -r _state < "$STATE_FILE" && [[ "$_state" == "sleeping" ]]; }

# --- chassis RGB (OpenRGB device 2 only) ------------------------------------
rgb_on() {
  openrgb --device "$RGB_DEVICE" --mode "$RGB_ON_MODE" --color "$RGB_ON_COLOR" >/dev/null 2>&1
}

rgb_off() {
  openrgb --device "$RGB_DEVICE" --mode static --color 000000 >/dev/null 2>&1
}

# --- monitor (delegate to the DDC-CI blackout script) -----------------------
blackout()    { "$DIR/monitor-blackout.sh" 8h; }
blackout_bg() { "$DIR/monitor-blackout.sh" fade 8h & }
restore()     { "$DIR/monitor-blackout.sh" restore; }
restore_bg()  { "$DIR/monitor-blackout.sh" fade-restore & }

# --- compound actions -------------------------------------------------------
do_sleep() {
  echo "sleeping" > "$STATE_FILE"
  rgb_off
  blackout_bg
  notify "Sleep mode activated"
}

do_wake() {
  echo "awake" > "$STATE_FILE"
  rgb_on
  restore
  notify "Wake mode activated"
}

# --- GUI --------------------------------------------------------------------
# The dialog closes after the action runs (exit code 1 from --button), so the
# label refreshes on next open. yad FBTN keeps the dialog open with a stale
# label — unacceptable for a toggle.
gui() {
  local label action
  if is_asleep; then
    label="☀️  Wake Mode"
    action="wake"
  else
    label="🌙  Sleep Mode"
    action="sleep"
  fi

  GTK_THEME=Adwaita-dark yad --title="Rig Controls" --window-icon=preferences-desktop \
      --form --columns=1 --width=280 --center --on-top \
      --text="<b>Rig Controls</b>" \
      --field="$label:FBTN" "bash -c '$SELF $action; kill \$YAD_PID'" \
      --button="Close:0"
}

# --- CLI dispatch -----------------------------------------------------------
case "${1:-gui}" in
  sleep)     do_sleep ;;
  wake)      do_wake ;;
  status)    is_asleep && echo "sleeping" || echo "awake" ;;
  rgb-on)    rgb_on;  notify "Chassis RGB on" ;;
  rgb-off)   rgb_off; notify "Chassis RGB off" ;;
  blackout)  blackout; notify "Monitor blacked out (8h)" ;;
  restore)   restore;  notify "Monitor restored" ;;
  gui|"")    gui ;;
  *) echo "usage: rig-control [sleep|wake|status|rgb-on|rgb-off|blackout|restore|gui]" >&2; exit 2 ;;
esac
