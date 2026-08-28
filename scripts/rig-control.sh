#!/usr/bin/env bash
# rig-control — a single-toggle sleep/wake panel (and CLI) for two workbench
# subsystems:
#   * chassis RGB on/off  (MSI motherboard headers, OpenRGB device 2 — scoped so
#                          the keyboard/mouse are never touched)
#                          Colors shift by time of day:
#                            10am amber → 11am golden → noon turquoise → 2pm blue
#                            → 4pm violet → 5pm pink → 6pm tomato → 8pm orange-red
#                            → 10pm red → midnight dark-red → 3am blackout
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
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/rig-control"
STATE_FILE="$STATE_DIR/state"

# --- helpers -----------------------------------------------------------------
mkdir -p "$STATE_DIR"

notify() {
  command -v notify-send >/dev/null 2>&1 && notify-send -t 2500 "rig-control" "$1" 2>/dev/null || true
}

is_asleep() { [[ -f "$STATE_FILE" ]] && read -r _state < "$STATE_FILE" && [[ "$_state" == "sleeping" ]]; }

# --- chassis RGB (OpenRGB device 2 only) ------------------------------------
# Time-of-day color: amber morning → bright/fun day → warm sunset → red late-night.
# Sleep mode always uses black (000000) regardless of time.
time_color() {
  local h
  h=$(date +%-H)
  case "$h" in
    10)        echo "FFA500" ;;  # 10am  — amber (just woke)
    11)        echo "FFBF00" ;;  # 11am  — golden
    12|13)     echo "00CED1" ;;  # noon  — dark turquoise
    14|15)     echo "1E90FF" ;;  # 2-3pm — dodger blue
    16)        echo "9400D3" ;;  # 4pm   — violet
    17)        echo "FF69B4" ;;  # 5pm   — hot pink
    18|19)     echo "FF6347" ;;  # 6-7pm — tomato (sunset)
    20|21)     echo "FF4500" ;;  # 8-9pm — orange red
    22|23)     echo "FF0000" ;;  # 10-11pm — red
    0|1|2)     echo "8B0000" ;;  # midnight-2am — dark red
    *)         echo "FFA500" ;;  # 3am-9am (sleeping) — amber fallback
  esac
}

rgb_on() {
  openrgb --device "$RGB_DEVICE" --mode "$RGB_ON_MODE" --color "$(time_color)" >/dev/null 2>&1
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
