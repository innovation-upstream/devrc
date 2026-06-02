#!/usr/bin/env bash
# Monitor 1-minute load average and fire a desktop notification on sustained high load.
#
# "Sustained" means the threshold must be exceeded for several consecutive samples,
# so a brief burst (e.g. a nix build) does not alert — only a real runaway does.
# The alert names the top CPU process so you can see the culprit at a glance.
#
# Run in foreground:   bash scripts/cpu-monitor.sh
# Run detached:        setsid bash scripts/cpu-monitor.sh >/dev/null 2>&1 &
#
# Tunables (env vars, with defaults):
#   CPU_MON_THRESHOLD   load avg that counts as "high"   (default: number of CPU cores)
#   CPU_MON_INTERVAL    seconds between samples          (default: 30)
#   CPU_MON_SUSTAIN     consecutive high samples to fire (default: 3  -> ~90s)
#   CPU_MON_COOLDOWN    seconds to wait before re-alert  (default: 300)
set -euo pipefail

CORES=$(nproc)
THRESHOLD=${CPU_MON_THRESHOLD:-$CORES}
INTERVAL=${CPU_MON_INTERVAL:-30}
SUSTAIN=${CPU_MON_SUSTAIN:-3}
COOLDOWN=${CPU_MON_COOLDOWN:-300}

# notify-send needs a display + session bus. If we were launched without them
# (systemd, cron, detached shell), borrow them from a running i3 process.
ensure_desktop_env() {
  [ -n "${DISPLAY:-}" ] && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && return 0
  local pid
  pid=$(pgrep -u "$(id -u)" -x i3 | head -1) || true
  [ -z "$pid" ] && pid=$(pgrep -u "$(id -u)" -x i3bar | head -1) || true
  [ -z "$pid" ] && return 1
  local env_file="/proc/$pid/environ"
  [ -r "$env_file" ] || return 1
  local d b
  d=$(tr '\0' '\n' < "$env_file" | grep -m1 '^DISPLAY=' || true)
  b=$(tr '\0' '\n' < "$env_file" | grep -m1 '^DBUS_SESSION_BUS_ADDRESS=' || true)
  [ -n "$d" ] && export "${d?}"
  [ -n "$b" ] && export "${b?}"
}

# 1-minute load average (first field of /proc/loadavg).
read_load() { cut -d' ' -f1 < /proc/loadavg; }

# "pid command %cpu" for the single busiest process.
top_proc() {
  ps -eo pid=,comm=,%cpu= --sort=-%cpu | awk 'NR==1 {printf "%s (%s) %.0f%% CPU", $2, $1, $3}'
}

# float comparison: is $1 >= $2 ?
load_ge() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a+0 >= b+0) }'; }

alert() {
  local load="$1" culprit="$2" urgency="$3" summary="$4"
  local body="1-min load: ${load} (threshold ${THRESHOLD}, ${CORES} cores)
Top: ${culprit}"
  if ensure_desktop_env; then
    notify-send -u "$urgency" -a cpu-monitor -i utilities-system-monitor "$summary" "$body" || true
  else
    # No desktop reachable — fall back to stderr so nothing is silently lost.
    printf '[cpu-monitor] %s — %s | %s\n' "$summary" "$load" "$culprit" >&2
  fi
}

high_streak=0
alerting=0          # currently in an alerted (high) state
last_alert=0        # epoch of last notification, for cooldown

while :; do
  load=$(read_load)
  if load_ge "$load" "$THRESHOLD"; then
    high_streak=$((high_streak + 1))
    if [ "$high_streak" -ge "$SUSTAIN" ]; then
      now=$(date +%s)
      if [ "$alerting" -eq 0 ] || [ $((now - last_alert)) -ge "$COOLDOWN" ]; then
        alert "$load" "$(top_proc)" critical "⚠ High CPU load"
        last_alert=$now
        alerting=1
      fi
    fi
  else
    # Recovered: notify once if we had been alerting, then reset.
    if [ "$alerting" -eq 1 ]; then
      alert "$load" "$(top_proc)" low "✓ CPU load back to normal"
    fi
    high_streak=0
    alerting=0
  fi
  sleep "$INTERVAL"
done
