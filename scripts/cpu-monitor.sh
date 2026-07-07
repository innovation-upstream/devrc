#!/usr/bin/env bash
# Monitor CPU and fire desktop notifications on two independent conditions:
#
#   1. Sustained high LOAD  — 1-min load average stays >= threshold for several
#      samples. Catches multi-process saturation (e.g. many parallel jobs).
#   2. Single RUNAWAY proc  — one process holds >= a CPU% for several samples.
#      Catches a stuck/looping process that pegs one core but barely moves the
#      load average (a single hung script adds only ~1 to load on an 8-core box,
#      so the load trigger alone would never see it).
#   3. High TEMPERATURE     — package temp stays >= a threshold for several
#      samples. Catches thermal events the load/runaway triggers are blind to:
#      the chassis running near the thermal ceiling at modest load (failing
#      cooling, dust, aging paste), where throttling becomes the real risk.
#
# All three require the condition to persist across consecutive samples, so
# brief bursts (e.g. a nix build) do not alert. Each has its own state/cooldown.
#
# Run in foreground:   bash scripts/cpu-monitor.sh
# Run detached:        setsid bash scripts/cpu-monitor.sh >/dev/null 2>&1 &
#
# Tunables (env vars, with defaults):
#   CPU_MON_THRESHOLD        load avg that counts as "high"      (default: CPU cores)
#   CPU_MON_INTERVAL         seconds between samples             (default: 30)
#   CPU_MON_SUSTAIN          consecutive high-load samples       (default: 3  -> ~90s)
#   CPU_MON_COOLDOWN         seconds before re-alerting          (default: 300)
#   CPU_MON_RUNAWAY_PCT      per-process CPU%% that counts as a runaway (default: 85)
#   CPU_MON_RUNAWAY_SUSTAIN  consecutive samples same proc stays hot    (default: 6 -> ~3m)
#   CPU_MON_TEMP_THRESHOLD   package temp (°C) that counts as "hot"     (default: 95)
#   CPU_MON_TEMP_SUSTAIN     consecutive hot samples to fire            (default: 3 -> ~90s)
set -euo pipefail

CORES=$(nproc)
THRESHOLD=${CPU_MON_THRESHOLD:-$CORES}
INTERVAL=${CPU_MON_INTERVAL:-30}
SUSTAIN=${CPU_MON_SUSTAIN:-3}
COOLDOWN=${CPU_MON_COOLDOWN:-300}
RUNAWAY_PCT=${CPU_MON_RUNAWAY_PCT:-85}
RUNAWAY_SUSTAIN=${CPU_MON_RUNAWAY_SUSTAIN:-6}
TEMP_THRESHOLD=${CPU_MON_TEMP_THRESHOLD:-95}
TEMP_SUSTAIN=${CPU_MON_TEMP_SUSTAIN:-3}
TEMP_HYSTERESIS=5   # recover only once temp drops this far below the threshold

# Process comms to NEVER alert on — expected heavy hitters (games etc.) whose
# high CPU is not a problem. Case-insensitive SUBSTRING match against the busy
# process's command (so "anno" also catches "Anno1800.exe" under Proton).
# Space-separated; extend via CPU_MON_IGNORE in the systemd service.
IGNORE=${CPU_MON_IGNORE:-anno}

# is_ignored <comm> -> 0 if it matches any IGNORE entry (case-insensitive substring)
is_ignored() {
  local comm_lc want_lc
  comm_lc=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
  for want_lc in $(printf '%s' "$IGNORE" | tr '[:upper:] ' '[:lower:]\n'); do
    [ -n "$want_lc" ] || continue
    case "$comm_lc" in *"$want_lc"*) return 0 ;; esac
  done
  return 1
}

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

# "command (pid) NN% CPU" for the busiest process (lifetime-average %cpu — only
# used as descriptive context in the load alert, not for runaway detection).
top_proc() {
  ps -eo pid=,comm=,%cpu= --sort=-%cpu | awk 'NR==1 {printf "%s (%s) %.0f%% CPU", $2, $1, $3}'
}

# Instantaneous %CPU of the busiest process, from top's SECOND sample (the first
# sample is a lifetime average; the second is a true delta over -d seconds).
# Echoes "PID PCT" when that process is at/above RUNAWAY_PCT, else nothing.
runaway_proc() {
  top -bn2 -d 0.3 -o %CPU 2>/dev/null | awk -v th="$RUNAWAY_PCT" '
    /^[[:space:]]*PID[[:space:]]/ { iter++; next }     # count each sample header
    iter >= 2 && NF >= 12 {                            # first data row of 2nd sample
      if ($9 + 0 >= th + 0) print $1, $9;              # $9 = %CPU, $1 = PID
      exit
    }'
}

# float comparison: is $1 >= $2 ?
load_ge() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a+0 >= b+0) }'; }

# Package temperature in whole °C, read from sysfs (no lm_sensors dependency).
# Prefers coretemp "Package id 0"; falls back to the hottest thermal zone.
package_temp() {
  local hwmon label f best=""
  for hwmon in /sys/class/hwmon/hwmon*; do
    [ -r "$hwmon/name" ] && [ "$(cat "$hwmon/name")" = "coretemp" ] || continue
    for label in "$hwmon"/temp*_label; do
      [ -r "$label" ] || continue
      case "$(cat "$label")" in
        "Package id "*)
          f="${label%_label}_input"
          [ -r "$f" ] && { echo $(( $(cat "$f") / 1000 )); return 0; } ;;
      esac
    done
  done
  # Fallback: hottest thermal zone.
  local t
  for f in /sys/class/thermal/thermal_zone*/temp; do
    [ -r "$f" ] || continue
    t=$(cat "$f")
    if [ -z "$best" ] || [ "$t" -gt "$best" ]; then best="$t"; fi
  done
  [ -n "$best" ] && echo $(( best / 1000 ))
}

# alert <urgency> <summary> <body>
alert() {
  local urgency="$1" summary="$2" body="$3"
  if ensure_desktop_env; then
    notify-send -u "$urgency" -a cpu-monitor -i utilities-system-monitor "$summary" "$body" || true
  else
    # No desktop reachable — fall back to stderr so nothing is silently lost.
    printf '[cpu-monitor] %s | %s\n' "$summary" "$(printf '%s' "$body" | tr '\n' ' ')" >&2
  fi
}

# --- load trigger state ---
high_streak=0
load_alerting=0
load_last_alert=0

# --- runaway trigger state ---
runaway_pid=""
runaway_streak=0
runaway_alerting=0
runaway_last_alert=0

# --- temperature trigger state ---
temp_streak=0
temp_alerting=0
temp_last_alert=0

while :; do
  now=$(date +%s)

  # --- 1. sustained high load ---
  load=$(read_load)
  if load_ge "$load" "$THRESHOLD"; then
    high_streak=$((high_streak + 1))
    # Suppress if the load is dominated by an expected heavy app (game etc.).
    if [ "$high_streak" -ge "$SUSTAIN" ] && ! is_ignored "$(ps -eo comm= --sort=-%cpu | head -1)"; then
      if [ "$load_alerting" -eq 0 ] || [ $((now - load_last_alert)) -ge "$COOLDOWN" ]; then
        alert critical "⚠ High CPU load" \
          "1-min load: ${load} (threshold ${THRESHOLD}, ${CORES} cores)
Top: $(top_proc)"
        load_last_alert=$now
        load_alerting=1
      fi
    fi
  else
    [ "$load_alerting" -eq 1 ] && alert low "✓ CPU load back to normal" "1-min load: ${load}"
    high_streak=0
    load_alerting=0
  fi

  # --- 2. single runaway process ---
  cand=$(runaway_proc || true)
  if [ -n "$cand" ]; then
    # Drop the candidate if it's an expected heavy app (game etc.) — no alert.
    is_ignored "$(ps -o comm= -p "${cand%% *}" 2>/dev/null)" && cand=""
  fi
  if [ -n "$cand" ]; then
    rpid=${cand%% *}
    rpct=${cand##* }
    if [ "$rpid" = "$runaway_pid" ]; then
      runaway_streak=$((runaway_streak + 1))
    else
      runaway_pid="$rpid"
      runaway_streak=1
    fi
    if [ "$runaway_streak" -ge "$RUNAWAY_SUSTAIN" ]; then
      if [ "$runaway_alerting" -eq 0 ] || [ $((now - runaway_last_alert)) -ge "$COOLDOWN" ]; then
        rcomm=$(ps -o comm= -p "$rpid" 2>/dev/null || echo '?')
        rargs=$(ps -o args= -p "$rpid" 2>/dev/null | cut -c1-90 || true)
        retime=$(ps -o etime= -p "$rpid" 2>/dev/null | tr -d ' ' || echo '?')
        alert critical "⚠ Runaway process: ${rcomm}" \
          "PID ${rpid} at ${rpct}% CPU for ~$((runaway_streak * INTERVAL))s (running ${retime})
${rargs}"
        runaway_last_alert=$now
        runaway_alerting=1
      fi
    fi
  else
    [ "$runaway_alerting" -eq 1 ] && alert low "✓ Runaway process cleared" "No process above ${RUNAWAY_PCT}% CPU"
    runaway_pid=""
    runaway_streak=0
    runaway_alerting=0
  fi

  # --- 3. high package temperature ---
  temp=$(package_temp || true)
  if [ -n "$temp" ] && [ "$temp" -ge "$TEMP_THRESHOLD" ]; then
    temp_streak=$((temp_streak + 1))
    if [ "$temp_streak" -ge "$TEMP_SUSTAIN" ]; then
      if [ "$temp_alerting" -eq 0 ] || [ $((now - temp_last_alert)) -ge "$COOLDOWN" ]; then
        alert critical "🌡 High CPU temperature" \
          "Package: ${temp}°C (threshold ${TEMP_THRESHOLD}°C) — throttling risk
Load ${load}, top: $(top_proc)"
        temp_last_alert=$now
        temp_alerting=1
      fi
    fi
  elif [ -n "$temp" ] && [ "$temp" -lt "$((TEMP_THRESHOLD - TEMP_HYSTERESIS))" ]; then
    # Only clear once temp drops a margin below the threshold (avoid flapping).
    [ "$temp_alerting" -eq 1 ] && alert low "✓ CPU temperature back to normal" "Package: ${temp}°C"
    temp_streak=0
    temp_alerting=0
  fi

  sleep "$INTERVAL"
done
