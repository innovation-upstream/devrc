#!/usr/bin/env bash
# tmux-restore-observe.sh — capture the evidence a reboot produces about the
# post-boot workspace restore chain, so ONE reboot answers the open question.
#
# The open question (claudedocs/handoff-tmux-restore-chain.md): the boot unit
# `tmux-session-restore.service` is ordered against NOTHING tmux-related, and on
# a cold boot the script's own `tmux new-session` is what starts the tmux server
# — which sources tmux.conf, which loads continuum, which fires its own restore
# concurrently. Expected damage is duplicated or MISPLACED windows.
#
# It is a READER. It starts nothing, orders nothing, and adds no boot-path unit
# — deliberately: three defects in this arc were introduced by changing a boot
# path nobody had observed, so the observation must not perturb what it observes.
#
#   tmux-restore-observe.sh pre    # BEFORE the reboot — durable baseline
#   tmux-restore-observe.sh post   # AFTER  the reboot — evidence + verdict
#   tmux-restore-observe.sh verdict <pre> <post>   # re-read a stored pair
#
# Captures land in $TMUX_RESTORE_OBSERVE_DIR (default ~/.cache/tmux-restore-observe),
# outside the repo, so a branch switch or a reboot cannot take the baseline away.
#
# 🔴 WHAT THE DISCRIMINATOR IS, AND WHAT IT IS NOT
#
# It is `(session, window_index)` against the layout that was ACTUALLY REPLAYED
# — the newest `tmux_resurrect_*.txt` older than this boot, resolved at `post`
# time. Two earlier designs were measured wrong on this host and are recorded so
# nobody re-derives them:
#
#   * NOT duplicate window NAMES. `automatic-rename-format` is the basename of
#     the pane's cwd, so every window of a session sitting in one repo shares a
#     name BY CONFIGURATION. Measured 2026-09-06 on a HEALTHY live workspace:
#     10 duplicate (session, name) groups (it was 9 four hours earlier — the
#     number drifts with the workspace and is not the claim). The claim is that
#     the count is reliably NON-ZERO, so a name-keyed check reports RACE on a
#     perfect restore.
#   * NOT the baseline's window COUNT. continuum autosaves every 15 minutes and
#     the reboot is unscheduled, so a count captured hours earlier describes a
#     layout that is not the one replayed. Measured: the count moved 56 -> 55
#     within two hours of a baseline being taken.
#
# `(session, window_index)` is what the restore script itself targets
# (`tmux send-keys -t <sess>:<win>`), so it is the identity whose duplication or
# displacement actually causes the damage. Window names are captured and printed
# for the human but are NEVER a verdict input, because automatic-rename makes
# them volatile within seconds of a pane starting.

set -uo pipefail

OBS_DIR="${TMUX_RESTORE_OBSERVE_DIR:-$HOME/.cache/tmux-restore-observe}"
PLAN="${TMUX_RESTORE_PLAN:-$HOME/.config/initiatives/restore-plan.json}"
RESURRECT_DIR="${TMUX_RESURRECT_DIR:-$HOME/.tmux/resurrect}"
RESTORE_LOG="${TMUX_RESTORE_LOG:-$HOME/.cache/tmux-session-restore.log}"
UNIT=tmux-session-restore.service

# 🔴 BOOT TIME COMES FROM /proc/stat, NOT `uptime -s`. `-s` is a procps
# extension: coreutils' `uptime` rejects it with `invalid option -- 's'` and
# prints nothing to stdout, so the capture silently recorded an EMPTY boot_time
# and every verdict on it returned "no reboot can be proven". Measured inside
# this repo's own dev shell, where coreutils' uptime shadows procps'. `btime` is
# the kernel's own boot epoch, is always present on Linux, and formats to the
# byte-identical string `uptime -s` produces.
boot_epoch() {
  local b
  b=$(awk '/^btime /{print $2; exit}' /proc/stat 2>/dev/null)
  if [ -n "$b" ]; then printf '%s\n' "$b"; return 0; fi
  b=$(uptime -s 2>/dev/null) && [ -n "$b" ] && date -d "$b" +%s 2>/dev/null && return 0
  return 1
}

boot_time() {
  local e
  e=$(boot_epoch) || { echo "UNMEASURED reason=no-btime-in-proc-stat-and-no-uptime-s"; return; }
  date -d "@$e" '+%Y-%m-%d %H:%M:%S' 2>/dev/null ||
    echo "UNMEASURED reason=could-not-format-btime"
}

RC_CLEAN=0
RC_RACE=1
RC_USAGE=2
RC_INCONCLUSIVE=3
RC_MISSING=4
RC_NO_WORKSPACE=5

die() { printf 'tmux-restore-observe: %s\n' "$*" >&2; exit $RC_USAGE; }

# --------------------------------------------------------------------------- #
# Readers. Each prints `key=value`, or `key=UNMEASURED reason=...`. Never a bare
# zero for something that could not be read: a zero window count from a dead
# tmux server and a zero from an empty server are different findings.
# --------------------------------------------------------------------------- #

# The layout continuum replayed at boot: the newest save STRICTLY OLDER than the
# boot. Derived from the boot rather than taken from the baseline, because a
# save made minutes before an unscheduled reboot is the one that gets replayed
# and no baseline can know which that will be.
replayed_layout() {
  # best_t starts BELOW every possible mtime, not at 0: a file whose mtime is
  # exactly the epoch is a legitimate candidate, and `t > 0` silently skipped it.
  local boot_epoch=$1 f best_t=-1 best=""
  for f in "$RESURRECT_DIR"/tmux_resurrect_*.txt; do
    [ -f "$f" ] || continue
    local t
    t=$(stat -c '%Y' "$f" 2>/dev/null) || continue
    if [ "$t" -lt "$boot_epoch" ] && [ "$t" -gt "$best_t" ]; then
      best_t=$t; best=$f
    fi
  done
  printf '%s\n' "$best"
}

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
  # 🔴 `-L` (dereference) IS LOAD-BEARING, NOT TIDINESS. `$PLAN` is a symlink
  # onto `restore-plans/restore-plan_<ts>.json` (tmux-session-restore.py
  # `cmd_save`), and GNU `stat` uses **lstat** by default — MEASURED: on a
  # symlink whose target was stamped 12:00:00, bare `stat -c '%y'` reported
  # 22:41:47, the moment the LINK was repointed. Without `-L` this reports when
  # the pointer moved rather than when the plan was written, which is a
  # different fact wearing the same name. `[ -f ]` above needs no flag — `test`
  # dereferences already.
  echo "plan_mtime=$(stat -Lc '%y' "$PLAN")"
  # One writer: python emits the whole block or none of it, so a partial parse
  # cannot leave a value AND an UNMEASURED marker for the same key.
  local out
  if out=$(python3 - "$PLAN" <<'PY'
import collections, json, pathlib, sys
p = json.loads(pathlib.Path(sys.argv[1]).read_text())
c = collections.Counter(e.get("bind_source", "") or "(none)" for e in p)
print(f"plan_entries={len(p)}")
print(f"plan_bound={sum(1 for e in p if e.get('session_id'))}")
print("plan_bind_sources=" + ",".join(f"{k}:{v}" for k, v in sorted(c.items())))
PY
  ); then
    printf '%s\n' "$out"
  else
    echo "plan_entries=UNMEASURED reason=unparseable-json"
  fi
}

emit_host() {
  # devrc manages two hosts. A baseline from one compared against the other
  # renders a confident verdict about nothing, so identity is captured and the
  # verdict refuses on a mismatch.
  local id="(unknown)"
  [ -r /etc/machine-id ] && id=$(cat /etc/machine-id)
  echo "host_machine_id=$id"
  echo "host_name=${ACTIVITY_HOST:-$(uname -n)}"
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
  local opt v
  for opt in @continuum-save-last-timestamp @continuum-boot @continuum-restore; do
    v=$(tmux show-options -gqv "$opt" 2>/dev/null)
    echo "opt_${opt#@}=${v:-(unset)}"
  done
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

# --------------------------------------------------------------------------- #
# Sections. TAB-separated. tmux REFUSES a window name containing a tab or a
# newline (measured — `invalid window name`), so an INVENTORY row cannot be
# split by its own content.
#
# ⚠ That is a claim about window NAMES only, NOT about the layout file: measured
# on one real save, 54 pane records occupied 58 physical lines, because a pane's
# trailing command field can carry embedded newlines. Both pane readers key on
# `$1=="pane"`, so a continuation line is skipped rather than misread — but the
# cwd reader parses exactly these rows, so the property it relies on is the
# `$1` key, not line-per-record.
#
# ⚠ And ORDERING DOES NOT PROTECT A PARSER. An earlier version of this comment
# claimed journal content "can never pollute" a structured section because every
# structured section precedes the JOURNAL. It could: `section()` re-opened its
# block at any later matching header. That is fixed in `section()` itself, which
# is what makes the property true — not the ordering.
# --------------------------------------------------------------------------- #

emit_inventory() {
  if ! tmux has-session 2>/dev/null; then
    echo "# UNMEASURED — no tmux server responding"
    return
  fi
  tmux list-windows -a -F '#{session_name}	#{window_index}	#{window_name}' 2>/dev/null | sort
}

emit_observed_ids() {
  tmux has-session 2>/dev/null || { echo "# UNMEASURED — no tmux server responding"; return; }
  tmux list-windows -a -F '#{session_name}	#{window_index}' 2>/dev/null | sort
}

emit_observed_cwd() {
  tmux has-session 2>/dev/null || { echo "# UNMEASURED — no tmux server responding"; return; }
  tmux list-panes -a -F '#{session_name}	#{window_index}	#{pane_current_path}' 2>/dev/null | sort -u
}

# tmux-resurrect line formats (measured against a live save):
#   window <TAB> session <TAB> window_index <TAB> :name <TAB> ...
#   pane   <TAB> session <TAB> window_index <TAB> ... <TAB> :cwd <TAB> 0|1 <TAB> cmd ...
#
# 🔴 THE PANE cwd IS NOT AT A FIXED FIELD INDEX. Measured on one real save: 49
# pane lines carry it at field 8 and 5 carry it at field 7, all with 11 fields —
# so a positional read silently returns a neighbouring field. Reading `$8` gave
# `1` for those five and the misplacement arm then reported a healthy workspace
# as MISPLACED. It is located by CONTENT instead: the `:`-prefixed absolute path
# whose NEXT field is the 0|1 pane_active flag. The successor test is what stops
# a pane_title that happens to begin with `:/` from being taken as the cwd.
layout_ids() { awk -F'\t' '$1=="window" {print $2"\t"$3}' "$1" | sort; }

# 🔴 THE POST-BOOT WORKSPACE HAS TWO WRITERS, NOT ONE. continuum replays the
# layout, and `tmux-session-restore.py` then runs `new-window -t <sess>:<idx>`
# for anything in its plan that is missing (:743) and `send-keys "cd <plan cwd>
# && claude --resume"` (:755). So a plan id absent from the layout is a window
# the restore CREATED ON PURPOSE, and a cwd matching the PLAN's is the restore
# working correctly — neither is the boot race.
#
# Measured on this host: at a plan/layout skew of 46 min the layout-only
# expectation produced 1 false RACE id and 4 false MISPLACEMENT rows; at 91 min,
# 1 and 5. Skew is normally ~40 s (the post-save hook refreshes the plan), but
# the restore tolerates 2 h BY DESIGN (`--staleness-check 2`) and a silently
# dead save chain is this instrument's own premise — so the false-positive
# regime is exactly the degraded state it exists to observe.
plan_ids() {
  python3 - "$1" 2>/dev/null <<'PY' || true
import json, pathlib, sys
for e in json.loads(pathlib.Path(sys.argv[1]).read_text()):
    s, w = e.get("session"), e.get("window")
    if s and w is not None:
        print(f"{s}\t{w}")
PY
}

plan_cwds() {
  python3 - "$1" 2>/dev/null <<'PY' || true
import json, pathlib, sys
for e in json.loads(pathlib.Path(sys.argv[1]).read_text()):
    s, w, c = e.get("session"), e.get("window"), e.get("cwd")
    if s and w is not None and c:
        print(f"{s}\t{w}\t{c}")
PY
}

_layout_cwd_awk='
  function cwd_of(   i) {
    for (i = 1; i <= NF; i++)
      if ($i ~ /^:\// && (i + 1) <= NF && $(i + 1) ~ /^[01]$/) return substr($i, 2)
    return ""
  }'

layout_cwds() {
  awk -F'\t' "$_layout_cwd_awk"'
    $1=="pane" { c = cwd_of(); if (c != "") print $2 "\t" $3 "\t" c }' "$1" | sort -u
}

# A pane line the rule cannot read is DROPPED from the comparison, and a silent
# drop is how a misplacement goes unseen. Counted so the verdict can say so.
layout_cwd_unparsed() {
  awk -F'\t' "$_layout_cwd_awk"'
    $1=="pane" { if (cwd_of() == "") n++ } END { print n + 0 }' "$1"
}

capture() {
  local kind=$1 out=$2
  {
    echo "# tmux-restore-observe $kind"
    echo "captured_at=$(date -Is)"
    echo "boot_time=$(boot_time)"
    echo "boot_epoch=$(boot_epoch || echo UNMEASURED)"
    echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
    emit_host
    emit_layout
    emit_plan
    emit_tmux
    emit_unit

    if [ "$kind" = post ]; then
      # Resolved HERE, not in verdict(), so the post file is self-contained and
      # `verdict <pre> <post>` stays a pure function of two files.
      local boot_epoch replayed
      boot_epoch=$(boot_epoch) || boot_epoch=""
      replayed=""
      [ -n "$boot_epoch" ] && replayed=$(replayed_layout "$boot_epoch")
      if [ -n "$replayed" ]; then
        echo "replayed_layout_file=$replayed"
        echo "replayed_layout_mtime=$(stat -c '%y' "$replayed")"
        echo "replayed_layout_windows=$(grep -c $'^window\t' "$replayed")"
        echo "replayed_layout_cwd_unparsed=$(layout_cwd_unparsed "$replayed")"
        local lastlink
        lastlink=$(readlink -f "$RESURRECT_DIR/last" 2>/dev/null || true)
        echo "resurrect_last_link=${lastlink:-(none)}"
      else
        echo "replayed_layout=UNMEASURED reason=no-resurrect-save-older-than-this-boot"
      fi
    fi

    echo "--- INVENTORY (session<TAB>index<TAB>window_name) ---"
    emit_inventory
    echo "--- OBSERVED-IDS (session<TAB>index) ---"
    emit_observed_ids
    echo "--- OBSERVED-CWD (session<TAB>index<TAB>cwd) ---"
    emit_observed_cwd

    if [ "$kind" = post ]; then
      if [ -n "${replayed:-}" ]; then
        echo "--- EXPECTED-IDS (session<TAB>index) ---"
        layout_ids "$replayed"
        echo "--- EXPECTED-CWD (session<TAB>index<TAB>cwd) ---"
        layout_cwds "$replayed"
        # The skew between the two writers is what decides how many windows the
        # restore legitimately adds. Printed so the reader can see it, and
        # subtracted below so it cannot masquerade as the race.
        if [ -f "$PLAN" ]; then
          # `-L` on the PLAN only: it is a symlink (see `emit_plan`), while
          # `$replayed` is always a real `tmux_resurrect_*.txt` off the glob in
          # `replayed_layout`. Without it the skew measures the moment the
          # pointer was repointed against the layout's write time — two writers
          # that are no longer the two this line claims to compare.
          echo "plan_layout_skew_seconds=$((  $(stat -Lc '%Y' "$PLAN") - $(stat -c '%Y' "$replayed") ))"
        fi
      fi
      if [ -f "$PLAN" ]; then
        echo "--- PLAN-IDS (session<TAB>index) ---"
        plan_ids "$PLAN" | sort
        echo "--- PLAN-CWD (session<TAB>index<TAB>cwd) ---"
        plan_cwds "$PLAN" | sort -u
      fi
      # 🔴 THE FAILURE THE 2026-09-06 REBOOT ACTUALLY PRODUCED, and which every
      # comparison in this file was blind to: the unit RAN, exited 0, sent 43
      # `claude --resume` lines, and started NOTHING. Windows and ids were
      # perfect, so an id-keyed verdict reads clean while the workspace is 54
      # bare shells. The two numbers that separate those cases are the sends
      # the unit logged and the panes actually running claude.
      echo "sends_logged=$(journalctl --user -u "$UNIT" -b --no-pager 2>/dev/null \
        | grep -c 'claude --resume' || true)"
      if tmux has-session 2>/dev/null; then
        echo "claude_panes_live=$(tmux list-panes -a -F '#{pane_current_command}' 2>/dev/null \
          | grep -cx claude || true)"
      else
        echo "claude_panes_live=UNMEASURED reason=no-tmux-server-responding"
      fi
      echo "--- JOURNAL ($UNIT, this boot) ---"
      journalctl --user -u "$UNIT" -b --no-pager 2>&1 || echo "(journal unavailable)"
      echo "--- RESTORE LOG (tail) ---"
      tail -40 "$RESTORE_LOG" 2>&1 || echo "(no restore log)"
    fi
  } >"$out" 2>&1
}

get() { sed -n "s/^$2=//p" "$1" | head -1; }

# Body of a named section, up to the next `--- ` header. Comment rows (`# …`)
# are dropped: they are the UNMEASURED markers the emitters write.
section() {
  # 🔴 FIRST MATCHING BLOCK ONLY. An earlier version re-evaluated `inside` at
  # every `--- ` line, so a LATER line reproducing a section header re-opened
  # the block — demonstrated: a JOURNAL line spelling `--- OBSERVED-IDS ...`
  # injected a row into the comparison and produced a false RACE EVIDENCE.
  # Ordering does not protect a parser; `done` is what protects it.
  awk -v want="$2" '
    /^--- / { if (done) { inside = 0; next }
              inside = (index($0, "--- " want) == 1)
              if (inside) seen = 1
              else if (seen) done = 1
              next }
    inside && $0 !~ /^#/ && NF { print }
  ' "$1"
}

verdict() {
  local pre=$1 post=$2
  local pre_boot post_boot rc_incomplete=0

  echo
  echo "=== VERDICT ==="
  pre_boot=$(get "$pre" boot_time)
  post_boot=$(get "$post" boot_time)
  echo "pre  boot_time=$pre_boot  (captured $(get "$pre" captured_at), host $(get "$pre" host_name))"
  echo "post boot_time=$post_boot  (captured $(get "$post" captured_at), host $(get "$post" host_name))"

  local pre_host post_host
  pre_host=$(get "$pre" host_machine_id); post_host=$(get "$post" host_machine_id)
  if [ -n "$pre_host" ] && [ -n "$post_host" ] && [ "$pre_host" != "$post_host" ]; then
    echo "INCONCLUSIVE — the baseline was captured on a DIFFERENT HOST than this"
    echo "  capture. devrc manages two, and their workspaces are unrelated."
    return $RC_INCONCLUSIVE
  fi

  if [ -z "$pre_boot" ] || [ -z "$post_boot" ]; then
    echo "INCONCLUSIVE — a capture carries no boot_time, so no reboot can be proven."
    return $RC_INCONCLUSIVE
  fi
  if [ "$pre_boot" = "$post_boot" ]; then
    echo "INCONCLUSIVE — boot_time is unchanged, so NO REBOOT happened between the"
    echo "  two captures. Nothing below is evidence about a boot."
    return $RC_INCONCLUSIVE
  fi

  # --- did the workspace come back at all? --------------------------------- #
  local obs_windows
  obs_windows=$(get "$post" tmux_windows)
  if [ -z "$obs_windows" ]; then
    echo "🔴 TOTAL RESTORE FAILURE — no tmux server was responding when this"
    echo "  capture ran. Not 'could not decide': the workspace is not there."
    echo "  unit Result=$(get "$post" unit_Result) ExecMainStatus=$(get "$post" unit_ExecMainStatus)"
    unit_ran_line "$post"
    return $RC_NO_WORKSPACE
  fi

  # --- what SHOULD have come back ------------------------------------------ #
  local replayed
  replayed=$(get "$post" replayed_layout_file)
  if [ -z "$replayed" ]; then
    echo "INCONCLUSIVE — no resurrect save older than this boot was found, so"
    echo "  there is nothing the restore could have replayed to compare against."
    echo "  ($(get "$post" replayed_layout))"
    return $RC_INCONCLUSIVE
  fi
  echo "replayed layout: $replayed"
  echo "  saved $(get "$post" replayed_layout_mtime) — $(get "$post" replayed_layout_windows) windows"
  local lastlink
  lastlink=$(get "$post" resurrect_last_link)
  if [ -n "$lastlink" ] && [ "$lastlink" != "(none)" ] && [ "$lastlink" != "$replayed" ]; then
    # States the observation, not a cause. An `last` pointing at a NEWER file
    # means a save landed after this boot; pointing at an OLDER one means a
    # write/relink was interrupted. The script does not know which without
    # comparing mtimes, so it does not claim one.
    echo "  note: resurrect's 'last' link points at a DIFFERENT file: $lastlink"
    echo "  The pre-boot file above is the one this verdict compares against."
  fi
  echo "observed: $obs_windows windows / $(get "$post" tmux_sessions) sessions"
  unit_ran_line "$post"

  local exp_f obs_f
  exp_f=$(mktemp); obs_f=$(mktemp)
  section "$post" "EXPECTED-IDS" | sort > "$exp_f"
  section "$post" "OBSERVED-IDS" | sort > "$obs_f"
  if [ ! -s "$exp_f" ] || [ ! -s "$obs_f" ]; then
    echo "INCONCLUSIVE — an id section is empty (expected=$(wc -l <"$exp_f")"\
         "observed=$(wc -l <"$obs_f")), so the comparison would be vacuous."
    rm -f "$exp_f" "$obs_f"
    return $RC_INCONCLUSIVE
  fi

  local extra missing plan_f created
  plan_f=$(mktemp)
  section "$post" "PLAN-IDS" | sort > "$plan_f"
  extra=$(comm -13 "$exp_f" "$obs_f")
  missing=$(comm -23 "$exp_f" "$obs_f")

  # Windows the RESTORE created on purpose (in its plan, absent from the layout)
  # are not the race. Reported separately rather than dropped, because "the
  # restore added 3 windows" is information, not noise.
  if [ -s "$plan_f" ] && [ -n "$extra" ]; then
    created=$(printf '%s\n' "$extra" | sort | comm -12 - "$plan_f")
    extra=$(printf '%s\n' "$extra" | sort | comm -23 - "$plan_f")
    [ -n "$created" ] && {
      echo
      echo "the restore itself created $(printf '%s\n' "$created" | grep -c .) window(s)"
      echo "  present in its plan but not in the replayed layout — expected, not the race:"
      printf '%s\n' "$created" | sed 's/^/  /'
    }
  fi
  rm -f "$exp_f" "$obs_f"

  # --- misplacement: right id, wrong working directory --------------------- #
  # 🔴 `want`/`got`, NOT `exp` — `exp` is an awk BUILT-IN (exponential) and
  # using it as an array name is a syntax error. Measured: the whole arm then
  # emitted nothing, on stderr, and the verdict read NO RACE OBSERVED. A silent
  # false clean is exactly what this instrument must never produce, which is why
  # the awk status is checked below instead of trusting an empty result.
  # 🔴 THE CWD PAIR NEEDS ITS OWN VACUITY GUARD. The id sections have one; these
  # did not, so an empty OBSERVED-CWD produced an empty `moved` and rendered as
  # "in the right place", rc 0. Measured on the shipped script: an emptied
  # section AND the emitter's own `# UNMEASURED — no tmux server responding`
  # marker (which `section()` strips as a comment) BOTH returned a clean
  # verdict. Reachable because list-windows and list-panes are two separate tmux
  # calls: the server dying between them leaves ids populated and cwds empty.
  local obs_cwd_n exp_cwd_n
  obs_cwd_n=$(section "$post" "OBSERVED-CWD" | grep -c . || true)
  exp_cwd_n=$(section "$post" "EXPECTED-CWD" | grep -c . || true)
  if [ "$obs_cwd_n" = 0 ] || [ "$exp_cwd_n" = 0 ]; then
    echo
    echo "🔴 MISPLACEMENT NOT MEASURED — a working-directory section is empty"
    echo "   (expected=$exp_cwd_n observed=$obs_cwd_n). The ids below were still"
    echo "   compared, but nothing here can tell you whether a window came back"
    echo "   in the right PLACE. This is not a clean misplacement result."
    rc_incomplete=1
  fi

  local moved moved_rc
  # Three inputs, because a window has TWO legitimate destinations: the cwd the
  # layout recorded, and the cwd the restore's own plan `cd`s to. Only a cwd
  # matching NEITHER is a misplacement. stderr is kept — a bare `awk exit N`
  # with the reason discarded is the same sin as a bare zero.
  # 🔴 ONE TAGGED STREAM, not three file operands. Splitting on the file
  # boundary (`FNR==1 {part++}`) MISALIGNS when an input is empty — FNR never
  # reaches 1 for that file, so the next section is silently read as the
  # previous one's role. The tag travels with the row and cannot shift.
  moved=$(
    { section "$post" "PLAN-CWD"     | sort -u | sed 's/^/P\t/'
      section "$post" "EXPECTED-CWD" | sort    | sed 's/^/W\t/'
      section "$post" "OBSERVED-CWD" | sort    | sed 's/^/G\t/'
    } | awk -F'\t' '
      NF < 4 { next }
      $1=="P" { if (!(($2 SUBSEP $3 SUBSEP $4) in seenp)) {
                  plan[$2 SUBSEP $3] = plan[$2 SUBSEP $3] "|" $4
                  seenp[$2 SUBSEP $3 SUBSEP $4] = 1 } ; next }
      $1=="W" { if (!(($2 SUBSEP $3 SUBSEP $4) in seenw)) {
                  want[$2 SUBSEP $3] = want[$2 SUBSEP $3] "|" $4
                  seenw[$2 SUBSEP $3 SUBSEP $4] = 1 } ; next }
      $1=="G" { got[$2 SUBSEP $3] = got[$2 SUBSEP $3] "|" $4 }
      END { for (k in got) {
              if (!(k in want)) continue
              if (got[k] == want[k]) continue
              if ((k in plan) && got[k] == plan[k]) continue  # restore cd-ed here on purpose
              split(k, a, SUBSEP)
              print a[1] "\t" a[2] "\t" want[k] "  ->  " got[k] } }')
  moved_rc=$?
  local unparsed
  unparsed=$(get "$post" replayed_layout_cwd_unparsed)
  if [ -n "$unparsed" ] && [ "$unparsed" != 0 ]; then
    echo
    echo "⚠ $unparsed pane line(s) in the replayed layout carried no readable cwd,"
    echo "   so those windows are OUTSIDE the misplacement check below. It covers"
    echo "   the rest, not all of them."
  fi
  # ⚠ UNPINNED — recorded rather than dressed up as coverage. NO FIXTURE REACHES
  # IT, and that is the honest extent of the claim: an earlier version of this
  # comment said "unreachable today", which was an overclaim. It covers only
  # awk-WIDE failure — `section()` is also awk, so a broken awk empties the id
  # sections and the vacuity guard returns INCONCLUSIVE first (that path IS
  # pinned, by `test_a_broken_awk_degrades_to_INCONCLUSIVE_not_to_clean`). It
  # does NOT cover a failure specific to THIS invocation, which is the one shape
  # that would land here: awk works, `section` works, and this pipeline alone
  # dies on a signal or a ulimit. stderr is deliberately NOT discarded, so when
  # that happens the operator gets the reason and not just a number.
  # A mutation that neuters this branch therefore SURVIVES, knowingly.
  if [ "$moved_rc" != 0 ]; then
    echo
    echo "🔴 MISPLACEMENT CHECK COULD NOT RUN (awk exit $moved_rc) — this verdict"
    echo "   is INCOMPLETE. An empty result from a failed comparison is not a"
    echo "   clean one; do not read the outcome below as covering misplacement."
    rc_incomplete=1
  fi

  local rc=$RC_CLEAN
  if [ "${rc_incomplete:-0}" = 1 ]; then rc=$RC_INCONCLUSIVE; fi

  # --- did the restore's OWN WORK land? ------------------------------------ #
  # Windows coming back is continuum's job; resuming the conversations is this
  # unit's. They fail independently, and on 2026-09-06 the second failed
  # completely while the first was flawless.
  local sends live
  sends=$(get "$post" sends_logged)
  live=$(get "$post" claude_panes_live)
  if [ -n "$sends" ] && [ "$sends" != 0 ]; then
    # 🔴 PREFIX match, not equality. The emitters write `key=UNMEASURED
    # reason=...` (see the `claude_panes_live` reader above), and `get` returns
    # everything after `key=`, so `[ "$live" = UNMEASURED ]` was NEVER true in
    # production. MEASURED by reverting this line: it fell through to the `-lt`
    # arm, an INTEGER comparison against a sentence. That does NOT abort — the
    # shell prints `integer expected`, the test evaluates FALSE, and control
    # lands in the `else`, which reports
    #   resumes: UNMEASURED reason=... pane(s) running claude vs 43 send(s)
    # and returns RC_CLEAN. So the one path whose whole job is to say "I do not
    # know" instead returned a confident PASS.
    case "$live" in
      ''|UNMEASURED*)
      echo
      echo "⚠ the unit logged $sends resume(s) but the live claude-pane count is"
      echo "   UNMEASURED, so whether any of them landed is UNKNOWN."
      rc=$RC_INCONCLUSIVE
      ;;
      *)
    if [ "$live" -lt "$sends" ]; then
      echo
      echo "🔴 THE RESUMES DID NOT LAND — the unit logged $sends send(s) and only"
      echo "   $live pane(s) are running claude."
      echo "   MEASURED 2026-09-06: 43 sent, 0 resumed, unit Result=success. The"
      echo "   cause was NOT keystrokes discarded by an unready pane — that was"
      echo "   the first diagnosis and the journal refuted it. The unit had"
      echo "   STARTED ITS OWN tmux server (no other existed on a cold boot);"
      echo "   the sends landed in it, then systemd tore down the unit's cgroup"
      echo "   and took the server with it."
      echo "   This is INDEPENDENT of the window comparison below: the windows"
      echo "   can be perfect and the workspace still empty."
      rc=$RC_RACE
    else
      echo "resumes: $live pane(s) running claude vs $sends send(s) logged"
      echo "   ⚠ claude_panes_live is a WHOLE-HOST count: it includes panes you"
      echo "      started by hand and panes the unit SKIPPED as already running,"
      echo "      so it can exceed \$sends without every send having landed."
    fi
      ;;
    esac
  fi
  if [ -n "$extra" ]; then
    echo
    echo "🔴 RACE EVIDENCE — windows exist that the replayed layout does NOT contain"
    echo "   (session <TAB> index):"
    printf '%s\n' "$extra" | sed 's/^/  /'
    rc=$RC_RACE
  fi
  if [ -n "$moved" ]; then
    echo
    echo "🔴 MISPLACEMENT — a window id came back holding a DIFFERENT working"
    echo "   directory than the layout recorded. This is the damaging case: the"
    echo "   restore targets \`<session>:<index>\`, so a resume lands in the wrong"
    echo "   window. (session <TAB> index <TAB> expected -> observed):"
    printf '%s\n' "$moved" | sed 's/^/  /'
    rc=$RC_RACE
  fi
  if [ -n "$missing" ]; then
    echo
    echo "⚠ WINDOWS MISSING — the replayed layout contains ids that did not come"
    echo "   back. A restore failure, not the duplication race:"
    printf '%s\n' "$missing" | sed 's/^/  /'
    # Reported independently of the race arms, and only WEAKENS the code when
    # nothing worse fired — an earlier revision nested this under `rc == 0`, so
    # a single race line made every missing window unreportable.
    [ "$rc" = "$RC_CLEAN" ] && rc=$RC_MISSING
  fi

  if [ "$rc" = "$RC_CLEAN" ]; then
    echo
    echo "NO RACE OBSERVED on this boot — every replayed window id came back"
    echo "exactly once, in the right place, with no extras."
    echo "🔴 That is ONE boot, under this boot's timing and load. It does not close"
    echo "  the question; it is one negative sample of a timing assumption with no"
    echo "  ordering."
  fi

  echo
  echo "Read the journal block in $post before concluding — it carries the unit's"
  echo "own account of what it did, which no comparison above can substitute for."
  return $rc
}

# `Result=success ExecMainStatus=0` is byte-identical for a unit that NEVER RAN
# (measured on a never-started user unit), so the run/no-run fact gets its own
# line rather than being left to a reader who will assume the reassuring one.
unit_ran_line() {
  local started rc_main
  started=$(get "$1" unit_InactiveExitTimestamp)
  rc_main=$(get "$1" unit_ExecMainStatus)
  if [ -z "$started" ]; then
    echo "🔴 the boot unit has NOT RUN this boot (InactiveExitTimestamp empty)."
    echo "  Its timer is OnActiveSec=45s — if you ran this immediately after login,"
    echo "  wait and re-run. 'Result=success' says nothing here: it reads the same"
    echo "  for a unit that never started."
  elif [ -n "$rc_main" ] && [ "$rc_main" != 0 ]; then
    # 🔴 `Result` is systemd's verdict on the UNIT; `ExecMainStatus` is the
    # PROCESS's exit code, and for a Type=oneshot they disagree routinely.
    # Measured on this host at the time of writing: Result=success with
    # ExecMainStatus=1 — and the handoff records this unit exiting 1 on EVERY
    # boot from 2026-08-04 until #1297+#1309. Leaving that in a parenthetical
    # is how the most common failure state reads as a success.
    echo "🔴 the boot unit RAN AND FAILED — ExecMainStatus=$rc_main (ran at $started)."
    echo "  'Result=$(get "$1" unit_Result)' is systemd's verdict on the UNIT, not on the"
    echo "  process: for a Type=oneshot the two disagree routinely. Whatever the"
    echo "  comparison below says, the restore did not complete its own work."
  else
    echo "boot unit ran at $started and its process exited 0 (Result=$(get "$1" unit_Result))"
  fi
}

# --------------------------------------------------------------------------- #
mkdir -p "$OBS_DIR" || die "cannot create $OBS_DIR"
cmd=${1:-}
case "$cmd" in
  pre)
    out="$OBS_DIR/pre-$(date -u +%Y%m%dT%H%M%SZ).txt"
    capture pre "$out"
    [ -s "$out" ] || die "capture wrote nothing to $out"
    ln -sfn "$out" "$OBS_DIR/pre-latest.txt"
    # The doc points a post-reboot reader at this copy, so `pre` is its producer
    # — otherwise it is a path nothing creates and nothing refreshes, and a later
    # revision of this script silently leaves the operator running the old one.
    if [ -r "${BASH_SOURCE[0]}" ]; then
      cp -f "${BASH_SOURCE[0]}" "$OBS_DIR/tmux-restore-observe.sh" &&
        chmod +x "$OBS_DIR/tmux-restore-observe.sh"
    fi
    echo "wrote $out  (and $OBS_DIR/pre-latest.txt)"
    grep -E '^(boot_time|host_name|layout_file|layout_windows|plan_entries|plan_bound|tmux_sessions|tmux_windows)=' "$out"
    echo
    echo "Now reboot. Afterwards run:  $0 post"
    ;;
  post)
    pre="$OBS_DIR/pre-latest.txt"
    [ -e "$pre" ] || die "no baseline at $pre — run '$0 pre' BEFORE the reboot (a post-only capture cannot answer the question)"
    out="$OBS_DIR/post-$(date -u +%Y%m%dT%H%M%SZ).txt"
    capture post "$out"
    [ -s "$out" ] || die "capture wrote nothing to $out"
    echo "wrote $out"
    verdict "$(readlink -f "$pre")" "$out"
    ;;
  extract)
    # The layout readers, on any save file. Their own surface because the pane
    # cwd is located by CONTENT and that rule has already been wrong once; a
    # reader you cannot run against a fixture is a reader nothing pins.
    [ $# -eq 2 ] || die "usage: $(basename "$0") extract <layout-file>"
    [ -f "$2" ] || die "no such layout file: $2"
    echo "--- EXPECTED-IDS (session<TAB>index) ---"
    layout_ids "$2"
    echo "--- EXPECTED-CWD (session<TAB>index<TAB>cwd) ---"
    layout_cwds "$2"
    echo "cwd_unparsed=$(layout_cwd_unparsed "$2")"
    ;;
  verdict)
    [ $# -eq 3 ] || die "usage: $(basename "$0") verdict <pre-file> <post-file>"
    [ -f "$2" ] || die "no such pre-file: $2"
    [ -f "$3" ] || die "no such post-file: $3"
    verdict "$2" "$3"
    ;;
  *)
    die "usage: $(basename "$0") pre|post|verdict <pre> <post>|extract <layout>
  rc 0 clean · 1 race/misplacement · 2 usage · 3 could-not-decide · 4 windows missing · 5 no workspace at all"
    ;;
esac
