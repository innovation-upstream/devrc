#!/usr/bin/env bash
# Black out the external monitor WITHOUT powering it off, by driving the
# DDC/CI backlight (VCP feature 0x10 "Brightness") to 0. The panel stays
# awake (no DPMS, no signal loss) — it just emits no light.
#
# NOTE: unlike a DPMS blank, this does NOT wake on keypress/mouse. Restore
# happens either automatically after the timer fires, or by re-running with
# `restore`.
#
# Usage:
#   monitor-blackout.sh           # black out now, auto-restore after 8h
#   monitor-blackout.sh 2h        # black out now, auto-restore after 2h (systemd time span)
#   monitor-blackout.sh restore   # restore prior brightness now, cancel pending timer
#   monitor-blackout.sh status    # show pending restore timer, if any
set -euo pipefail

DDC=$(command -v ddcutil) || { echo "ddcutil not found on PATH" >&2; exit 1; }
UNIT=monitor-blackout-restore
STATE="${XDG_RUNTIME_DIR:-/tmp}/monitor-blackout.state"   # "bus:brightness"

# First DDC/CI bus that actually ANSWERS a brightness (VCP 0x10) read.
# With >1 monitor connected, some panels enumerate an i2c bus but EIO on read
# (e.g. the ASUS VK278 over HDMI), so a blind "first bus" grabs a dead one and
# the blackout fails. Probe each detected bus and return the first that responds.
detect_bus() {
  local b
  for b in $("$DDC" detect --brief 2>/dev/null | grep -o 'i2c-[0-9]\+' | grep -o '[0-9]\+$'); do
    if "$DDC" --bus "$b" getvcp 10 --brief >/dev/null 2>&1; then
      printf '%s\n' "$b"; return 0
    fi
  done
  return 1
}

get_brightness() { # $1=bus  -> current VCP 0x10 value
  "$DDC" --bus "$1" getvcp 10 --brief 2>/dev/null | awk '{print $4}'
}

cancel_timer() {
  systemctl --user stop    "${UNIT}.timer"   2>/dev/null || true
  systemctl --user reset-failed "${UNIT}.service" "${UNIT}.timer" 2>/dev/null || true
}

blackout() {
  local dur="${1:-8h}" bus cur
  bus=$(detect_bus); [ -n "${bus:-}" ] || { echo "no DDC/CI monitor detected" >&2; exit 1; }
  cur=$(get_brightness "$bus"); [ -n "${cur:-}" ] || { echo "could not read brightness on bus $bus" >&2; exit 1; }
  printf '%s:%s\n' "$bus" "$cur" > "$STATE"

  "$DDC" --bus "$bus" setvcp 10 0

  cancel_timer
  # Schedule restore as a transient user timer so it survives this shell exiting.
  # Absolute $DDC path is baked in so no PATH is needed when the timer fires.
  systemd-run --user --unit="$UNIT" --on-active="$dur" --timer-property=AccuracySec=1s \
    "$DDC" --bus "$bus" setvcp 10 "$cur" >/dev/null
  echo "Monitor blacked out (bus $bus, was $cur/100). Auto-restore in $dur — or: $0 restore"
}

restore() {
  cancel_timer
  local bus="" cur=""
  [ -f "$STATE" ] && IFS=: read -r bus cur < "$STATE" || true
  bus="${bus:-$(detect_bus)}"; cur="${cur:-60}"
  [ -n "$bus" ] || { echo "no DDC/CI monitor detected" >&2; exit 1; }
  "$DDC" --bus "$bus" setvcp 10 "$cur"
  rm -f "$STATE"
  echo "Monitor restored (bus $bus -> $cur/100)."
}

status() {
  if systemctl --user is-active --quiet "${UNIT}.timer"; then
    systemctl --user list-timers "${UNIT}.timer" --no-pager 2>/dev/null | sed -n '1,2p'
  else
    echo "No pending restore timer."
  fi
}

case "${1:-}" in
  restore) restore ;;
  status)  status ;;
  "")      blackout 8h ;;
  *)       blackout "$1" ;;
esac
