#!/usr/bin/env bash
# Monitor CPU and fire desktop notifications on two independent conditions:
#
#   1. Sustained high LOAD  — 1-min load average stays >= threshold for several
#      samples. Catches multi-process saturation (e.g. many parallel jobs).
#   2. Single RUNAWAY proc  — one process holds >= a CPU% for several samples.
#      Catches a stuck/looping process that pegs one core but barely moves the
#      load average (a single hung script adds only ~1 to load on an 8-core box,
#      so the load trigger alone would never see it).
#
# Both require the condition to persist across consecutive samples, so brief
# bursts (e.g. a nix build) do not alert. Each has its own state and cooldown.
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
set -euo pipefail

CORES=$(nproc)
THRESHOLD=${CPU_MON_THRESHOLD:-$CORES}
INTERVAL=${CPU_MON_INTERVAL:-30}
SUSTAIN=${CPU_MON_SUSTAIN:-3}
COOLDOWN=${CPU_MON_COOLDOWN:-300}
RUNAWAY_PCT=${CPU_MON_RUNAWAY_PCT:-85}
RUNAWAY_SUSTAIN=${CPU_MON_RUNAWAY_SUSTAIN:-6}

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

while :; do
  now=$(date +%s)

  # --- 1. sustained high load ---
  load=$(read_load)
  if load_ge "$load" "$THRESHOLD"; then
    high_streak=$((high_streak + 1))
    if [ "$high_streak" -ge "$SUSTAIN" ]; then
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

  sleep "$INTERVAL"
done
