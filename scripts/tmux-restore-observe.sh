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
#     name BY CONFIGURATION. Measured 2026-09-06: 9 duplicate (session, name)
#     groups in a healthy live workspace. A name-keyed check reports RACE on a
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
  echo "plan_mtime=$(stat -c '%y' "$PLAN")"
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
# Sections. TAB-separated: tmux REFUSES a window name containing a tab or a
# newline (measured — `invalid window name`), so a row cannot be split by its
# own content. Every structured section precedes the free-text JOURNAL, so
# journal content can never pollute one.
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
  awk -v want="$2" '
    /^--- / { inside = (index($0, "--- " want) == 1); next }
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
    echo "  note: resurrect's 'last' link now points at $lastlink — a save landed"
    echo "  after this boot. The pre-boot file above is still the one replayed."
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

  local extra missing
  extra=$(comm -13 "$exp_f" "$obs_f")
  missing=$(comm -23 "$exp_f" "$obs_f")
  rm -f "$exp_f" "$obs_f"

  # --- misplacement: right id, wrong working directory --------------------- #
  # 🔴 `want`/`got`, NOT `exp` — `exp` is an awk BUILT-IN (exponential) and
  # using it as an array name is a syntax error. Measured: the whole arm then
  # emitted nothing, on stderr, and the verdict read NO RACE OBSERVED. A silent
  # false clean is exactly what this instrument must never produce, which is why
  # the awk status is checked below instead of trusting an empty result.
  local moved moved_rc
  moved=$(awk -F'\t' '
    NR==FNR { if (!(($1 SUBSEP $2 SUBSEP $3) in seen)) {
                want[$1 SUBSEP $2] = want[$1 SUBSEP $2] "|" $3
                seen[$1 SUBSEP $2 SUBSEP $3] = 1 }
              next }
    { got[$1 SUBSEP $2] = got[$1 SUBSEP $2] "|" $3 }
    END { for (k in got) if ((k in want) && got[k] != want[k]) {
            split(k, a, SUBSEP); print a[1] "\t" a[2] "\t" want[k] "  ->  " got[k] } }
  ' <(section "$post" "EXPECTED-CWD" | sort) <(section "$post" "OBSERVED-CWD" | sort) 2>/dev/null)
  moved_rc=$?
  local unparsed
  unparsed=$(get "$post" replayed_layout_cwd_unparsed)
  if [ -n "$unparsed" ] && [ "$unparsed" != 0 ]; then
    echo
    echo "⚠ $unparsed pane line(s) in the replayed layout carried no readable cwd,"
    echo "   so those windows are OUTSIDE the misplacement check below. It covers"
    echo "   the rest, not all of them."
  fi
  # ⚠ UNPINNED, AND BELIEVED UNREACHABLE TODAY — recorded rather than dressed up
  # as coverage. `section()` above is also awk, so a broken or missing awk
  # empties both id sections and the vacuity guard returns INCONCLUSIVE before
  # control ever gets here. That degradation IS pinned, by
  # `test_a_broken_awk_degrades_to_INCONCLUSIVE_not_to_clean`; this branch is a
  # backstop for the day `section` stops being awk, and no fixture reaches it.
  # A mutation that neuters it therefore SURVIVES, correctly and knowingly.
  if [ "$moved_rc" != 0 ]; then
    echo
    echo "🔴 MISPLACEMENT CHECK COULD NOT RUN (awk exit $moved_rc) — this verdict"
    echo "   is INCOMPLETE. An empty result from a failed comparison is not a"
    echo "   clean one; do not read the outcome below as covering misplacement."
    rc_incomplete=1
  fi

  local rc=$RC_CLEAN
  if [ "${rc_incomplete:-0}" = 1 ]; then rc=$RC_INCONCLUSIVE; fi
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
  local started
  started=$(get "$1" unit_InactiveExitTimestamp)
  if [ -z "$started" ]; then
    echo "🔴 the boot unit has NOT RUN this boot (InactiveExitTimestamp empty)."
    echo "  Its timer is OnActiveSec=45s — if you ran this immediately after login,"
    echo "  wait and re-run. 'Result=success' says nothing here: it reads the same"
    echo "  for a unit that never started."
  else
    echo "boot unit ran at $started (Result=$(get "$1" unit_Result), ExecMainStatus=$(get "$1" unit_ExecMainStatus))"
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
