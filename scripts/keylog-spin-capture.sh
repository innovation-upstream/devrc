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
# Requires kernel.yama.ptrace_scope=0 to attach to a non-descendant process.
# This is NOT uniform across the fleet: the workbench reads 0, the LAPTOP reads
# 1 (verified 2026-07-30). On a host with 1, py-spy can never attach, so this
# exits early rather than retrying a guaranteed failure every 5 minutes.
set -euo pipefail

THRESH=${KEYLOG_SPIN_THRESHOLD:-20}   # percent of one core
WINDOW=${KEYLOG_SPIN_WINDOW:-3}       # sample seconds
MAX_FAILS=${KEYLOG_SPIN_MAX_FAILS:-3} # give up after this many INCOMPLETE dumps
OUT="${XDG_CACHE_HOME:-$HOME/.cache}/keylog-spin"
SENTINEL="$OUT/.captured"
GIVEUP="$OUT/.giveup"
FAILCOUNT="$OUT/.failcount"

# One capture is enough — don't accumulate dumps forever.
[[ -f $SENTINEL ]] && exit 0
# Repeated INCOMPLETE dumps (no frames, or a pass that errored) mean something
# structural is wrong — no ptrace permission, py-spy broken, target churning.
# Stop rather than looping every 5 min forever.
[[ -f $GIVEUP ]] && exit 0

# Hard precondition: without same-UID ptrace, every attach fails. Bail QUIETLY
# — no dump file, no toast, no retry churn. Nothing here is fixable at runtime.
ptrace_scope=$(cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || echo 0)
if [[ $ptrace_scope != 0 ]]; then
  exit 0
fi

# Only now create state — a quiet gate exit must leave no directory behind.
mkdir -p "$OUT"

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
  # py-spy PAUSES the target by default (ptrace-stop), and keylog is a data
  # collector — a long pause drops keystrokes. `--nonblocking` is NOT an option
  # here: py-spy rejects it outright when combined with --native ("Can't get
  # native stack traces with the --nonblocking option", rc=1), so an earlier
  # revision silently produced no native frames at all. Since the native frames
  # ARE the diagnostic (they're what showed the healthy thread parked in
  # select() inside libc), keep the blocking attach and bound the freeze with
  # `timeout` instead — a hard ceiling on how long capture can stall.
  echo "--- py-spy dump ---"
  if command -v py-spy >/dev/null 2>&1; then
    timeout 10 py-spy dump --pid "$pid" 2>&1 || echo "py-spy dump failed or timed out"
  else
    echo "py-spy not on PATH"
  fi
  echo
  echo "--- py-spy dump (native frames; blocking, 15s cap) ---"
  if command -v py-spy >/dev/null 2>&1; then
    timeout 15 py-spy dump --pid "$pid" --native 2>&1 || echo "native dump failed or timed out"
  fi
} >"$dump" 2>&1

# Disarm ONLY on a complete capture: a real `Thread N` frame AND no pass having
# reported a failure. A transient py-spy failure must not burn the ONE capture
# this mechanism exists for — but a PARTIAL dump (plain pass OK, native pass
# dead) must not silently count as success either, since the native frames are
# the diagnostic being sought.
# `Thread N (idle):` is a HEADER py-spy prints unconditionally — it appears even
# when zero Python frames resolve, and py-spy still exits 0, so a header alone
# is NOT evidence of a usable capture. Require an actual frame line too:
# py-spy indents frames as `    func (file.py:123)` / `    sym (libc.so.6)`.
# Without this, a frameless-but-successful dump disarms the one-shot watcher on
# a useless artifact (reproduced 6/6 against a short-lived target).
got_stack=false
if grep -qE '^Thread [0-9]+ ' "$dump" && grep -qE '^[[:space:]]+[^[:space:]]+ \(' "$dump"; then
  got_stack=true
fi
had_failure=false; grep -qiE 'failed or timed out|not on PATH' "$dump" && had_failure=true

notify_urgency=normal
if [[ $got_stack == true && $had_failure == false ]]; then
  touch "$SENTINEL"
  rm -f "$FAILCOUNT"
  armed_note="complete capture — watcher disarmed (rm $SENTINEL to re-arm)"
  notify_urgency=critical
else
  # Bounded retry. Unbounded retries would mean a stackless dump + a
  # non-expiring critical toast every 5 minutes, forever — strictly worse than
  # the one-shot disarm this replaced.
  # Sanitize: under `set -u`, a garbage/multiline .failcount would make the
  # arithmetic abort BEFORE the .failed rename and before incrementing, so
  # .giveup becomes unreachable and dumps pile up every 5min instead.
  # The read must be guarded, not just error-suppressed: `<"$FAILCOUNT"` on a
  # missing file fails the whole command substitution, which under `set -e`
  # aborts here — before the .failed rename and before the increment — leaving
  # .giveup unreachable. That is the exact failure this block exists to prevent.
  prev=0
  if [[ -r $FAILCOUNT ]]; then
    prev=$(tr -cd '0-9' <"$FAILCOUNT" | head -c 6)
    [[ -n ${prev:-} ]] || prev=0
  fi
  fails=$(( prev + 1 ))
  echo "$fails" >"$FAILCOUNT"
  mv "$dump" "$dump.failed"
  dump="$dump.failed"
  if [[ $fails -ge $MAX_FAILS ]]; then
    touch "$GIVEUP"
    armed_note="INCOMPLETE x$fails — giving up (rm $GIVEUP and $FAILCOUNT to retry)"
    notify_urgency=critical
  else
    armed_note="INCOMPLETE ($fails/$MAX_FAILS) — still armed, will retry"
  fi
fi

# Surface it — but only escalate to a non-expiring critical toast on a real
# capture or on final give-up. Intermediate retries stay quiet in the log.
if command -v notify-send >/dev/null 2>&1 && [[ $notify_urgency == critical ]]; then
  notify-send -u critical "keylog spin ${pct}% — $armed_note" "$dump" || true
fi
echo "captured $dump (cpu=${pct}%) — $armed_note"
