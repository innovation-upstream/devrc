#!/usr/bin/env bash
# Catch the keylog.service CPU spin in the act.
#
# keylog.service was measured pinning a full core (96% CPU over a 3s sample,
# ~2 kernel ticks — i.e. an almost pure-userspace loop making no syscalls)
# sustained over 24h+, with 33.1M nonvoluntary context switches. A restart
# drops it to 0%, so the spin ACCUMULATES over runtime rather than being
# inherent to the design. That makes it hard to catch: by the time you notice,
# restarting to "fix" it destroys the evidence.
#
# A healthy dump (captured 2026-07-30, 38min uptime, 0% CPU) looks like:
#
#   Thread N (idle): "MainThread"
#       send_and_recv (Xlib/protocol/display.py:561)
#       enable_context (Xlib/ext/record.py:238)
#       run (keylog.py:325)
#   Thread M (idle): "Thread-1 (_idle_loop)"
#       wait (threading.py:359)
#
# The main thread lives inside enable_context() for its entire life, so the
# spin is somewhere in that frame. This samples CPU and, the first time it
# crosses the threshold, dumps the Python stack so the culprit frame is on
# disk waiting for you. Self-disables after one capture (delete the sentinel
# to re-arm).
#
# Requires kernel.yama.ptrace_scope=0 to attach to a non-descendant process;
# NixOS defaults it to 1. Without it, the capture logs the failure instead.
set -euo pipefail

THRESH=${KEYLOG_SPIN_THRESHOLD:-20}   # percent of one core
WINDOW=${KEYLOG_SPIN_WINDOW:-3}       # sample seconds
OUT="${XDG_CACHE_HOME:-$HOME/.cache}/keylog-spin"
SENTINEL="$OUT/.captured"

mkdir -p "$OUT"

# One capture is enough — don't accumulate dumps forever.
[[ -f $SENTINEL ]] && exit 0

pid=$(systemctl --user show keylog.service -p MainPID --value 2>/dev/null || echo 0)
[[ ${pid:-0} -gt 0 ]] || exit 0
[[ -r /proc/$pid/stat ]] || exit 0

# utime+stime in USER_HZ ticks (100/s), sampled across WINDOW seconds.
read_ticks() { awk '{print $14+$15}' "/proc/$1/stat" 2>/dev/null || echo 0; }
read_stime() { awk '{print $15}' "/proc/$1/stat" 2>/dev/null || echo 0; }
t0=$(read_ticks "$pid"); s0=$(read_stime "$pid")
sleep "$WINDOW"
t1=$(read_ticks "$pid"); s1=$(read_stime "$pid")

pct=$(( (t1 - t0) * 100 / (WINDOW * 100) ))
[[ $pct -lt $THRESH ]] && exit 0

stamp=$(date +%Y%m%d-%H%M%S)
dump="$OUT/spin-$stamp.txt"

{
  echo "=== keylog spin capture $stamp ==="
  echo "pid=$pid  cpu=${pct}%  threshold=${THRESH}%  window=${WINDOW}s"
  echo "etime=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')"
  echo "ptrace_scope=$(cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || echo '?')"
  echo
  echo "--- /proc/$pid/status (context switches) ---"
  grep -E '^(voluntary|nonvoluntary)_ctxt_switches|^VmRSS|^Threads' "/proc/$pid/status" 2>/dev/null || true
  echo
  # The diagnostic that matters: a near-zero stime delta means a userspace
  # loop making no syscalls (rules out a spinning select/poll on the X socket).
  echo "--- kernel-vs-user split over ${WINDOW}s ---"
  echo "utime+stime delta: $(( t1 - t0 )) ticks"
  echo "stime delta:       $(( s1 - s0 )) ticks"
  echo
  echo "--- py-spy dump ---"
  if command -v py-spy >/dev/null 2>&1; then
    py-spy dump --pid "$pid" 2>&1 || echo "py-spy failed (ptrace_scope=1? needs 0 for non-descendants)"
  else
    echo "py-spy not on PATH"
  fi
  echo
  echo "--- py-spy dump (native frames) ---"
  if command -v py-spy >/dev/null 2>&1; then
    py-spy dump --pid "$pid" --native 2>&1 || echo "native dump failed"
  fi
} >"$dump" 2>&1

touch "$SENTINEL"

# Surface it — this is the whole point, don't let it rot in a cache dir.
if command -v notify-send >/dev/null 2>&1; then
  notify-send -u critical "keylog spin captured (${pct}%)" "$dump" || true
fi
echo "captured $dump (cpu=${pct}%)"
