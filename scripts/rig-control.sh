#!/usr/bin/env bash
# rig-control — a tiny yad control panel (and CLI) for two workbench toggles:
#   * chassis RGB on/off  (MSI motherboard headers, OpenRGB device 2 — scoped so
#                          the keyboard/mouse are never touched)
#   * monitor blackout / restore  (DDC-CI backlight; delegates to monitor-blackout.sh)
#
# Usage:
#   rig-control            # open the yad panel (default)
#   rig-control rgb-on
#   rig-control rgb-off
#   rig-control blackout   # 8h auto-restore
#   rig-control restore
set -euo pipefail

SELF="$(readlink -f "$0")"
DIR="$(dirname "$SELF")"

# --- chassis RGB (OpenRGB device 2 only) ------------------------------------
RGB_DEVICE="${RIG_RGB_DEVICE:-2}"
RGB_ON_MODE="${RIG_RGB_ON_MODE:-static}"
RGB_ON_COLOR="${RIG_RGB_ON_COLOR:-FFFFFF}"   # change to taste (hex, no #), or set a mode like "Rainbow wave"

notify() { command -v notify-send >/dev/null 2>&1 && notify-send -t 2500 "rig-control" "$1" 2>/dev/null || true; }

rgb_on() {
  openrgb --device "$RGB_DEVICE" --mode "$RGB_ON_MODE" --color "$RGB_ON_COLOR" >/dev/null 2>&1
  notify "Chassis RGB on ($RGB_ON_MODE $RGB_ON_COLOR)"
}
rgb_off() {
  openrgb --device "$RGB_DEVICE" --mode static --color 000000 >/dev/null 2>&1
  notify "Chassis RGB off"
}

# --- monitor (delegate to the DDC-CI blackout script) -----------------------
blackout() { "$DIR/monitor-blackout.sh" 8h && notify "Monitor blacked out (auto-restore 8h)"; }
restore()  { "$DIR/monitor-blackout.sh" restore && notify "Monitor restored"; }

# --- GUI --------------------------------------------------------------------
gui() {
  # yad form with in-dialog action buttons (FBTN): clicking runs a command and
  # leaves the panel open, so it behaves like a little control surface.
  yad --title="Rig Controls" --window-icon=preferences-desktop \
      --form --columns=1 --width=280 --center --on-top \
      --text="<b>Chassis RGB &amp; Monitor</b>" \
      --field="🌈   Chassis RGB — On:FBTN"    "bash -c '$SELF rgb-on'" \
      --field="⚫   Chassis RGB — Off:FBTN"   "bash -c '$SELF rgb-off'" \
      --field="🖥️   Monitor — Blackout (8h):FBTN" "bash -c '$SELF blackout'" \
      --field="☀️   Monitor — Restore:FBTN"   "bash -c '$SELF restore'" \
      --button="Close:0"
}

case "${1:-gui}" in
  rgb-on)   rgb_on ;;
  rgb-off)  rgb_off ;;
  blackout) blackout ;;
  restore)  restore ;;
  gui|"")   gui ;;
  *) echo "usage: rig-control [rgb-on|rgb-off|blackout|restore|gui]" >&2; exit 2 ;;
esac
