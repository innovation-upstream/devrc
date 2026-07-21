#!/usr/bin/env bash
# notify-failure.sh <failed-unit-name>
#
# Fired by the `notify-failure@.service` systemd-user TEMPLATE unit, which is
# instanced by OnFailure=notify-failure@%n.service on the important user units
# (see nix/home.nix + nix/graphical.nix). When a wired unit enters the `failed`
# state, systemd starts notify-failure@<unit>.service, whose ExecStart runs this
# with the failed unit name (%i) as $1.
#
# Zach reasons THROUGH the agent-facing automation layer, so a silently-dead
# timer/collector is the worst failure mode — this makes it LOUD: a sticky
# (urgency=critical) desktop toast pointing at the unit's journal.
#
# ROBUST WHEN HEADLESS: on a host with no X/dunst session (or when the graphical
# gate is off) it logs a line to the journal and exits 0 — it must NEVER error
# (an erroring OnFailure handler is itself an invisible failure). Mirrors the
# DISPLAY/DBUS-borrow trick scripts/cpu-monitor.sh uses so the toast reaches the
# desktop from this (systemd) context. Deterministic notification mechanism
# reused from cpu-monitor (notify-send), NOT a new one.
set -uo pipefail

unit="${1:-unknown.unit}"

log() { printf '[notify-failure] %s\n' "$*" >&2; }

# Gate: only toast on a graphical host. home-manager sets
# NOTIFY_FAILURE_GRAPHICAL=1 in the template unit's Environment ONLY on graphical
# hosts (mirrors how dunst/espanso key off `graphical` in home.nix). The unit is
# installed everywhere; on a headless host this just no-ops the toast.
if [ "${NOTIFY_FAILURE_GRAPHICAL:-0}" != "1" ]; then
  log "unit '$unit' FAILED (headless — no toast). Inspect: journalctl --user -u $unit -e"
  exit 0
fi

# notify-send needs a display + session bus. Under a systemd user unit we usually
# have neither, so borrow them from a running i3 process (same approach as
# cpu-monitor.sh). Returns 0 only once a session bus is reachable.
ensure_desktop_env() {
  [ -n "${DISPLAY:-}" ] && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && return 0
  local pid
  pid=$(pgrep -u "$(id -u)" -x i3 | head -1) || true
  [ -z "$pid" ] && pid=$(pgrep -u "$(id -u)" -x i3bar | head -1) || true
  [ -z "$pid" ] && return 1
  local env_file="/proc/$pid/environ"
  [ -r "$env_file" ] || return 1
  local d b x
  d=$(tr '\0' '\n' < "$env_file" | grep -m1 '^DISPLAY=' || true)
  b=$(tr '\0' '\n' < "$env_file" | grep -m1 '^DBUS_SESSION_BUS_ADDRESS=' || true)
  x=$(tr '\0' '\n' < "$env_file" | grep -m1 '^XAUTHORITY=' || true)
  [ -n "$d" ] && export "${d?}"
  [ -n "$b" ] && export "${b?}"
  [ -n "$x" ] && export "${x?}"
  [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]
}

if ensure_desktop_env; then
  # urgency=critical -> sticky in the dunstrc (timeout 0) so a failure is not
  # missed. -a sets the app name; the body carries the exact journal command.
  notify-send -u critical -a notify-failure -i dialog-error \
    "⚠ ${unit} failed" \
    "A user unit entered the failed state.
journalctl --user -u ${unit} -e" \
    || log "notify-send failed for '$unit' (see: journalctl --user -u $unit)"
else
  # No reachable desktop session — fall back to the journal so nothing is lost.
  log "unit '$unit' FAILED (no desktop session). Inspect: journalctl --user -u $unit -e"
fi
exit 0
