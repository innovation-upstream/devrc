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
#   monitor-blackout.sh                 # black out now, auto-restore after 8h
#   monitor-blackout.sh 2h              # black out now, auto-restore after 2h (systemd time span)
#   monitor-blackout.sh restore         # restore prior brightness now, cancel pending timer
#   monitor-blackout.sh status          # show pending restore timer, if any
#   monitor-blackout.sh fade [dur]      # fade to black + auto-restore (non-blocking)
#   monitor-blackout.sh fade-restore    # fade back to saved brightness (non-blocking)
#   monitor-blackout.sh get-brightness  # print current VCP 0x10 value (for scripts)
set -euo pipefail

DDC=$(command -v ddcutil) || { echo "ddcutil not found on PATH" >&2; exit 1; }
UNIT=monitor-blackout-restore
STATE="${XDG_RUNTIME_DIR:-/tmp}/monitor-blackout.state"   # "bus:brightness"
FADE_PID_FILE="${XDG_RUNTIME_DIR:-/tmp}/monitor-blackout.fade.pid"

# Number of fade steps and inter-step delay for fade transitions.
# ~560ms per ddcutil call (hard floor at --sleep-multiplier 0.1), so 15 steps
# ≈ 8-9s wall time — perceptible but not sluggish.
FADE_STEPS="${RIG_FADE_STEPS:-15}"
FADE_DELAY="${RIG_FADE_DELAY:-0.05}"

# --- DDC/CI helpers --------------------------------------------------------

# First DDC/CI bus that actually ANSWERS a brightness (VCP 0x10) read.
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

set_brightness() { # $1=bus $2=value  — retries 3x on DDC-CI failure (this LG panel is flaky)
  local bus="$1" val="$2" attempt
  for attempt in 1 2 3; do
    "$DDC" --bus "$bus" setvcp 10 "$val" --noverify 2>/dev/null && return 0
    sleep 0.5
  done
  echo "DDC-CI setvcp failed after 3 attempts on bus $bus (val=$val)" >&2
  return 1
}

cancel_timer() {
  systemctl --user stop    "${UNIT}.timer"   2>/dev/null || true
  systemctl --user reset-failed "${UNIT}.service" "${UNIT}.timer" 2>/dev/null || true
}

schedule_restore() { # $1=bus $2=original_brightness $3=duration
  # Write saved brightness so `restore` can read it; use the script itself
  # (with retry + 0-default) instead of raw ddcutil.
  printf '%s:%s\n' "$1" "$2" > "$STATE"
  systemd-run --user --unit="$UNIT" --on-active="$3" --timer-property=AccuracySec=1s \
    "$0" restore >/dev/null
}

# --- actions ---------------------------------------------------------------

blackout() {
  local dur="${1:-8h}" bus cur
  bus=$(detect_bus) || true
  [ -n "${bus:-}" ] || { echo "no DDC/CI monitor detected" >&2; exit 1; }
  cur=$(get_brightness "$bus") || true
  [ -n "${cur:-}" ] || { echo "could not read brightness on bus $bus" >&2; exit 1; }
  printf '%s:%s\n' "$bus" "$cur" > "$STATE"

  set_brightness "$bus" 0

  cancel_timer
  schedule_restore "$bus" "$cur" "$dur"
  echo "Monitor blacked out (bus $bus, was $cur/100). Auto-restore in $dur — or: $0 restore"
}

restore() {
  cancel_timer
  # Kill any running background fade (sleep was triggered but wake came before it finished)
  if [ -f "$FADE_PID_FILE" ]; then
    local fpid
    fpid=$(cat "$FADE_PID_FILE" 2>/dev/null || true)
    rm -f "$FADE_PID_FILE"
    if [ -n "$fpid" ] && kill -0 "$fpid" 2>/dev/null; then
      kill "$fpid" 2>/dev/null || true
      sleep 0.5
    fi
  fi
  # Cancel again — a fade that completes between first cancel and kill may have
  # re-created the timer via schedule_restore.
  cancel_timer
  local bus="" cur=""
  [ -f "$STATE" ] && IFS=: read -r bus cur < "$STATE" || true
  bus="${bus:-$(detect_bus || true)}"
  # Saved brightness of 0 means blackout read while monitor was already dark
  # (race with prior fade) — fall back to a usable default instead of staying dark.
  [ -n "$cur" ] && [ "$cur" != "0" ] || cur=60
  [ -n "$bus" ] || { echo "no DDC/CI monitor detected" >&2; exit 1; }
  set_brightness "$bus" "$cur"
  rm -f "$STATE"
  echo "Monitor restored (bus $bus -> $cur/100)."
}

fade_to_zero() { # $1=bus $2=from_brightness
  local bus="$1" from="$2" to=0 i val
  for (( i=0; i<=FADE_STEPS; i++ )); do
    val=$(( from + (to - from) * i / FADE_STEPS ))
    set_brightness "$bus" "$val"
    sleep "$FADE_DELAY"
  done
}

fade_from_zero() { # $1=bus $2=to_brightness
  local bus="$1" to="$2" from=0 i val
  for (( i=0; i<=FADE_STEPS; i++ )); do
    val=$(( from + (to - from) * i / FADE_STEPS ))
    set_brightness "$bus" "$val"
    sleep "$FADE_DELAY"
  done
}

fade_blackout() {
  local dur="${1:-8h}" bus cur
  bus=$(detect_bus) || true
  [ -n "${bus:-}" ] || { echo "no DDC/CI monitor detected" >&2; exit 1; }
  cur=$(get_brightness "$bus") || true
  [ -n "${cur:-}" ] || { echo "could not read brightness on bus $bus" >&2; exit 1; }
  printf '%s:%s\n' "$bus" "$cur" > "$STATE"
  echo "$$" > "$FADE_PID_FILE"

  fade_to_zero "$bus" "$cur"

  rm -f "$FADE_PID_FILE"

  cancel_timer
  schedule_restore "$bus" "$cur" "$dur"
  echo "Monitor faded to black (bus $bus, was $cur/100). Auto-restore in $dur — or: $0 restore"
}

fade_restore() {
  cancel_timer
  local bus="" cur=""
  [ -f "$STATE" ] && IFS=: read -r bus cur < "$STATE" || true
  bus="${bus:-$(detect_bus || true)}"
  [ -n "$cur" ] && [ "$cur" != "0" ] || cur=60
  [ -n "$bus" ] || { echo "no DDC/CI monitor detected" >&2; exit 1; }

  fade_from_zero "$bus" "$cur"

  rm -f "$STATE"
  echo "Monitor faded to restore (bus $bus -> $cur/100)."
}

get_brightness_cmd() {
  local bus
  bus=$(detect_bus) || true
  [ -n "${bus:-}" ] || { echo "no DDC/CI monitor detected" >&2; exit 1; }
  get_brightness "$bus"
}

status() {
  if systemctl --user is-active --quiet "${UNIT}.timer"; then
    systemctl --user list-timers "${UNIT}.timer" --no-pager 2>/dev/null | sed -n '1,2p'
  else
    echo "No pending restore timer."
  fi
}

case "${1:-}" in
  restore)       restore ;;
  fade)          fade_blackout "${2:-8h}" ;;
  fade-restore)  fade_restore ;;
  status)        status ;;
  get-brightness) get_brightness_cmd ;;
  "")            blackout 8h ;;
  *)             blackout "$1" ;;
esac
