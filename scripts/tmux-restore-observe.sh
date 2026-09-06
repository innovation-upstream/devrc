#!/usr/bin/env bash
# tmux-restore-observe.sh — capture the evidence a reboot produces about the
# post-boot workspace restore chain, so ONE reboot answers the open question.
#
# The open question (claudedocs/handoff-tmux-restore-chain.md): the boot unit
# `tmux-session-restore.service` is ordered against NOTHING tmux-related, and on
# a cold boot the script's own `tmux new-session` is what starts the tmux server
# — which sources tmux.conf, which loads continuum, which fires its own restore
# concurrently. Expected damage is duplicated or misplaced windows.
#
# It is a READER. It starts nothing, orders nothing, and adds no boot-path unit
# — deliberately: three defects in this arc were introduced by changing a boot
# path nobody had observed, so the observation must not perturb what it observes.
#
#   tmux-restore-observe.sh pre    # BEFORE the reboot — durable baseline
#   tmux-restore-observe.sh post   # AFTER  the reboot — evidence + verdict
#
# Captures land in $TMUX_RESTORE_OBSERVE_DIR (default ~/.cache/tmux-restore-observe),
# outside the repo, so a branch switch or a reboot cannot take the baseline away.

set -uo pipefail

OBS_DIR="${TMUX_RESTORE_OBSERVE_DIR:-$HOME/.cache/tmux-restore-observe}"
PLAN="${TMUX_RESTORE_PLAN:-$HOME/.config/initiatives/restore-plan.json}"
RESURRECT_DIR="${TMUX_RESURRECT_DIR:-$HOME/.tmux/resurrect}"
UNIT=tmux-session-restore.service

die() { printf 'tmux-restore-observe: %s\n' "$*" >&2; exit 2; }

# --- readers ---------------------------------------------------------------
# Each prints `key=value` lines, or `key=UNMEASURED reason=...`. Never a bare
# zero for something that could not be read: a zero window count from a dead
# tmux server and a zero from an empty server are different findings.

newest_layout() {
  ls -t "$RESURRECT_DIR"/tmux_resurrect_*.txt 2>/dev/null | head -1
}

emit_layout() {
  local f
  f=$(newest_layout)
  if [ -z "$f" ]; then
    echo "layout=UNMEASURED reason=no-tmux_resurrect_-file-in-$RESURRECT_DIR"
    return
  fi
  echo "layout_file=$f"
  echo "layout_mtime=$(stat -c '%y' "$f")"
  echo "layout_windows=$(grep -c $'^window\t' "$f")"
  echo "layout_panes=$(grep -c $'^pane\t' "$f")"
}

emit_plan() {
  if [ ! -f "$PLAN" ]; then
    echo "plan=UNMEASURED reason=no-plan-at-$PLAN"
    return
  fi
  echo "plan_file=$PLAN"
  echo "plan_mtime=$(stat -c '%y' "$PLAN")"
  python3 - "$PLAN" <<'PY' || echo "plan_entries=UNMEASURED reason=unparseable-json"
import collections, json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text())
print(f"plan_entries={len(p)}")
print(f"plan_bound={sum(1 for e in p if e.get('session_id'))}")
c = collections.Counter(e.get("bind_source", "") or "(none)" for e in p)
print("plan_bind_sources=" + ",".join(f"{k}:{v}" for k, v in sorted(c.items())))
PY
}

emit_tmux() {
  if ! tmux has-session 2>/dev/null; then
    echo "tmux=UNMEASURED reason=no-tmux-server-responding"
    return
  fi
  echo "tmux_sessions=$(tmux list-sessions -F '#{session_name}' 2>/dev/null | wc -l)"
  echo "tmux_windows=$(tmux list-windows -a -F '#{session_name}' 2>/dev/null | wc -l)"
  echo "tmux_panes=$(tmux list-panes -a -F '#{pane_id}' 2>/dev/null | wc -l)"
  local pid
  pid=$(tmux display-message -p '#{pid}' 2>/dev/null)
  if [ -n "$pid" ] && [ -r "/proc/$pid" ]; then
    echo "tmux_server_pid=$pid"
    echo "tmux_server_started=$(ps -o lstart= -p "$pid" 2>/dev/null | sed 's/^ *//')"
  else
    echo "tmux_server_started=UNMEASURED reason=no-readable-server-pid"
  fi
  for opt in @continuum-save-last-timestamp @continuum-boot @continuum-restore; do
    local v
    v=$(tmux show-options -gqv "$opt" 2>/dev/null)
    echo "opt_${opt#@}=${v:-(unset)}"
  done
}

# The full inventory is what makes duplicates visible; the counts alone cannot.
emit_inventory() {
  if ! tmux has-session 2>/dev/null; then
    echo "# inventory UNMEASURED — no tmux server responding"
    return
  fi
  # TAB-separated: a tmux window name can contain ';' but not a tab.
  tmux list-windows -a -F '#{session_name}	#{window_index}	#{window_name}' 2>/dev/null | sort
}

emit_unit() {
  local raw
  if ! raw=$(systemctl --user show "$UNIT" \
      -p Result -p ExecMainStatus -p NRestarts -p ActiveEnterTimestamp \
      -p InactiveExitTimestamp -p After 2>/dev/null) || [ -z "$raw" ]; then
    echo "unit=UNMEASURED reason=systemctl-show-returned-nothing"
    return
  fi
  printf '%s\n' "$raw" | sed 's/^/unit_/'
}

capture() {
  local kind=$1 out=$2
  {
    echo "# tmux-restore-observe $kind"
    echo "captured_at=$(date -Is)"
    echo "boot_time=$(uptime -s)"
    echo "uptime=$(uptime -p)"
    echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
    emit_layout
    emit_plan
    emit_tmux
    emit_unit
    echo "--- INVENTORY (session;index;window_name) ---"
    emit_inventory
    if [ "$kind" = post ]; then
      echo "--- JOURNAL ($UNIT, this boot) ---"
      journalctl --user -u "$UNIT" -b --no-pager 2>&1 || echo "(journal unavailable)"
      echo "--- RESTORE LOG (tail) ---"
      tail -40 "$HOME/.cache/tmux-session-restore.log" 2>&1 || echo "(no restore log)"
    fi
  } >"$out" 2>&1
}

get() { sed -n "s/^$2=//p" "$1" | head -1; }

verdict() {
  local pre=$1 post=$2
  local pre_boot post_boot exp obs dup
  pre_boot=$(get "$pre" boot_time)
  post_boot=$(get "$post" boot_time)

  echo
  echo "=== VERDICT ==="
  echo "pre  boot_time=$pre_boot  (captured $(get "$pre" captured_at))"
  echo "post boot_time=$post_boot  (captured $(get "$post" captured_at))"
  if [ "$pre_boot" = "$post_boot" ]; then
    echo "INCONCLUSIVE — boot_time is unchanged, so NO REBOOT happened between the"
    echo "  two captures. Nothing below is evidence about a boot."
    return 3
  fi

  exp=$(get "$pre" layout_windows)
  obs=$(get "$post" tmux_windows)
  echo "expected windows (pre-reboot layout the restore replays) = ${exp:-UNMEASURED}"
  echo "observed windows (post-reboot live tmux)                 = ${obs:-UNMEASURED}"
  echo "plan entries (pre) = $(get "$pre" plan_entries)   plan entries (post) = $(get "$post" plan_entries)"
  echo "unit Result=$(get "$post" unit_Result) ExecMainStatus=$(get "$post" unit_ExecMainStatus)"
  echo "tmux server started: $(get "$post" tmux_server_started)"

  # Duplicated (session;window_name) pairs are the race's signature: continuum
  # replays the layout while the restore script's own new-window creates it too.
  dup=$(sed -n '/^--- INVENTORY/,/^--- /p' "$post" | grep -v '^--- ' \
        | awk -F'\t' 'NF==3 {print $1"\t"$3}' | sort | uniq -d)

  if [ -z "${exp:-}" ] || [ -z "${obs:-}" ] || [ "$exp" = UNMEASURED ] || [ "$obs" = UNMEASURED ]; then
    echo "INCONCLUSIVE — a count needed for the comparison was UNMEASURED (see above)."
    return 3
  fi

  local rc=0
  if [ -n "$dup" ]; then
    echo "🔴 RACE EVIDENCE — duplicated (session, window_name) pairs post-reboot:"
    printf '%s\n' "$dup" | sed 's/^/  /'
    rc=1
  fi
  if [ "$obs" -gt "$exp" ]; then
    echo "🔴 RACE EVIDENCE — observed ($obs) EXCEEDS the layout being replayed ($exp)."
    rc=1
  fi
  if [ "$rc" = 0 ]; then
    if [ "$obs" -lt "$exp" ]; then
      echo "⚠ NOT the race, but NOT clean either — observed ($obs) is BELOW the layout ($exp):"
      echo "  windows are MISSING, which is a restore failure, not a duplication race."
      rc=4
    else
      echo "NO RACE OBSERVED on this boot — observed == layout ($obs), no duplicate pairs."
      echo "🔴 That is ONE boot, under this boot's timing and load. It does not close the"
      echo "  question; it is one negative sample of a timing assumption with no ordering."
    fi
  fi
  echo
  echo "Read the journal block in $post before concluding — it carries the unit's"
  echo "own account of what it did, which no count above can substitute for."
  return $rc
}

# --- main ------------------------------------------------------------------
mkdir -p "$OBS_DIR" || die "cannot create $OBS_DIR"
cmd=${1:-}
case "$cmd" in
  pre)
    out="$OBS_DIR/pre-$(date -u +%Y%m%dT%H%M%SZ).txt"
    capture pre "$out"
    ln -sfn "$out" "$OBS_DIR/pre-latest.txt"
    echo "wrote $out  (and $OBS_DIR/pre-latest.txt)"
    grep -E '^(boot_time|layout_windows|layout_panes|plan_entries|plan_bound|tmux_sessions|tmux_windows)=' "$out"
    echo
    echo "Now reboot. Afterwards run:  $0 post"
    ;;
  post)
    pre="$OBS_DIR/pre-latest.txt"
    [ -e "$pre" ] || die "no baseline at $pre — run '$0 pre' BEFORE the reboot (a post-only capture cannot answer the question)"
    out="$OBS_DIR/post-$(date -u +%Y%m%dT%H%M%SZ).txt"
    capture post "$out"
    echo "wrote $out"
    verdict "$(readlink -f "$pre")" "$out"
    ;;
  verdict)
    # Re-read any stored pair without capturing anything new.
    [ $# -eq 3 ] || die "usage: $(basename "$0") verdict <pre-file> <post-file>"
    [ -f "$2" ] || die "no such pre-file: $2"
    [ -f "$3" ] || die "no such post-file: $3"
    verdict "$2" "$3"
    ;;
  *)
    die "usage: $(basename "$0") pre|post|verdict <pre> <post>"
    ;;
esac
