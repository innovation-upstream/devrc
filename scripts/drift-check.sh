#!/usr/bin/env bash
# drift-check — PASSIVE deadman for the devrc two-host fleet.
#
# Answers ONE question, unattended, on a timer: "is either host silently no
# longer receiving changes?" It REPORTS. It never fixes.
#
# ── WHY THIS EXISTS ───────────────────────────────────────────────────────────
# `scripts/ship.sh` converges both hosts and is correct: a host it cannot
# fast-forward is SKIPPED and left exactly as found (rc=8 skipped:diverged). But
# NOTHING RUNS SHIP ON A SCHEDULE. So a host that starts getting skipped stops
# receiving every subsequent change while continuing to look completely healthy —
# same commits in `git log`, same green `home-manager` generation, no error
# anywhere. The only detector was "a human happens to ship something and reads
# the per-host lines".
#
# That has now happened twice:
#   2026-08-06  two un-pushed commits on the workbench blocked it for hours; the
#               regrowth timer would have fired on 08-11 running the very bug the
#               undelivered commit fixed.
#   2026-08-09  THREE un-pushed commits on the workbench (rescued as #366), found
#               only because someone shipped something unrelated. Alongside them,
#               7 untracked files — including a handoff doc for the stranded work.
#
# ── PASSIVE MEANS PASSIVE ─────────────────────────────────────────────────────
# 🔴 This script must NEVER mutate either host's CHECKOUT. It may `git fetch`
# (which writes remote-tracking refs, never the working tree, the index, or any
# local branch — though note `fetch` does trigger git's own `gc --auto`, so
# "this script never causes a gc" would be a claim it cannot make). It must
# NEVER checkout, switch, merge, fast-forward, rebase, reset, stash, clean,
# commit, or run `home-manager`. A deadman that repairs is a deployer with no
# supervision; a deadman that reports is a deadman.
#
# `scripts/tests/test_drift_check.py` enforces that STATICALLY, and the scanner
# is an ALLOWLIST, not a keyword blocklist: every `git` invocation in executable
# code must be one of a fixed set of read-only subcommands, and the check is
# anchored at each COMMAND SEPARATOR (`;`, `&&`, `||`, `|`, `$(`), not at the
# start of the line — so `say "hi" && git checkout main` is caught.
#
# THE ONE FILE THIS SCRIPT WRITES is the consecutive-unreachable counter under
# $DRIFT_STATE_DIR (default $XDG_STATE_HOME/drift-check). It lives outside every
# repo, and `test_the_only_files_the_deadman_writes_are_the_streak_counters`
# holds that ledger.
#
# ── HOST IDENTITY ─────────────────────────────────────────────────────────────
# Both machines report hostname `nixos`, so identity comes from local IPv4
# addresses. That predicate is NOT reimplemented here — it is sourced from
# scripts/lib/host-role.sh, the same file ship.sh sources. A second copy would
# drift and be wrong, which is exactly how ship.sh's host detection was broken
# before (it hardcoded "local == workbench" and SSH'd to itself).
#
# ── EXIT CODES ────────────────────────────────────────────────────────────────
# Deliberately aligned with ship.sh so the two read consistently: a code means
# the same thing in both, and codes ship.sh owns for actions this script does not
# take (5 conflicted-tree, 7 cannot-ff, 9 switch-failed, 11 verify-failed) are
# left UNUSED rather than repurposed.
#
#   2   usage error (unknown flag, non-integer tunable, or a flag combination
#       that would check nothing at all)
#   3   repo missing on that host
#   4   git fetch failed, or origin/main is missing / HEAD unborn
#   6   local host could not be identified (see detect_role)
#   8   DRIFT — local `main` has DIVERGED or is AHEAD of origin/main (un-pushed
#       commits). 🔴 THE DANGEROUS ONE — ship.sh will skip this host forever.
#   10  DRIFT — local `main` is BEHIND origin/main (just needs a ship)
#   12  DRIFT — the checkout is not ON branch `main`
#   13  remote host unreachable for $DRIFT_UNREACHABLE_ESCALATE CONSECUTIVE runs
#
# ── UNREACHABLE IS NOT DRIFT (the alerting policy) ────────────────────────────
# 🔴 The timer runs on the WORKBENCH ONLY (gated on the ~/.server-mode marker in
# nix/home.nix), so its remote leg always ssh's to the LAPTOP — a machine that is
# routinely shut, asleep, or off-LAN. If every such run failed the unit, the
# operator would get the same sticky critical toast as a genuine rc 8 up to 4×
# a day, and would learn to ignore the one alert that must keep its meaning.
#
# So an unreachable remote is REPORTED on every run but only ESCALATES to rc 13
# after $DRIFT_UNREACHABLE_ESCALATE consecutive misses (default 4 = ~24h at the
# 6h cadence). The streak is persisted in $DRIFT_STATE_DIR and is RESET the
# moment the host answers — including when it answers with drift.
#
# 🔴 This is deliberately the only softening. Below the threshold the remote leg
# contributes NOTHING to the exit code — it does not mask the local leg, so a
# local rc 8 with an unreachable laptop still exits 8, still fails the unit and
# still toasts (`test_local_rc8_still_wins_when_the_remote_is_unreachable`). And
# if the streak cannot be persisted at all, "how long" is unknowable and the run
# escalates immediately rather than going quiet.
#
# When several hosts fail, the exit code is the MOST SEVERE, not the first —
# this differs from ship.sh on purpose. ship.sh keeps the first non-zero because
# every host's line is printed anyway and a human is reading them live. Nobody
# is reading a timer's output, so the single number it hands to systemd must be
# the worst thing found, or an un-pushed workbench could hide behind a merely
# behind laptop. Severity order (worst first):
#     8  >  13  >  6  >  4  >  3  >  12  >  10
# (6 is unreachable through this path today — the script exits 6 directly before
# any per-host leg runs — but the order is documented for every code it owns,
# and severity() ranks it rather than falling through to the unknown-code slot.)
# Per-host lines are ALWAYS printed for every host, whatever the code.
#
# Untracked files are counted and listed per host as INFORMATION only — they
# never change the exit code. They are the same loss class (work sitting on one
# host that no other host and no backup has) and cost nothing to report.
#
# ── USAGE ─────────────────────────────────────────────────────────────────────
#   scripts/drift-check.sh                 # check this host + the other one
#   scripts/drift-check.sh --no-remote     # this host only (no ssh)
#   scripts/drift-check.sh --no-local      # the other host only
#   scripts/drift-check.sh --detect-role   # print detected local role, exit 0
#
# Env overrides:
#   SHIP_ROLE    force the local role (workbench|laptop) — shared with ship.sh
#   REMOTE_SSH   ssh target for the OTHER host (default derived from role)
#   LAPTOP_SSH   back-compat: applies ONLY when the remote host is the laptop
#   DRIFT_REPO   repo path checked on the LOCAL host (default $HOME/workspace/devrc)
#   DRIFT_UNTRACKED_MAX  max untracked paths listed per host (default 10, integer)
#   DRIFT_UNREACHABLE_ESCALATE  consecutive unreachable runs before rc 13 (default 4)
#   DRIFT_STATE_DIR  where the unreachable streak is persisted
#                    (default ${XDG_STATE_HOME:-$HOME/.local/state}/drift-check)
set -uo pipefail

# --- Host identity: SOURCED, never copied (see header) ------------------------
# The source path is symlink-resolved: invoked through a symlink, an unresolved
# ${BASH_SOURCE[0]} would look for lib/ next to the SYMLINK and not find it.
_drift_self="${BASH_SOURCE[0]}"
_drift_resolved="$(readlink -f "$_drift_self" 2>/dev/null || true)"
[ -n "$_drift_resolved" ] && _drift_self="$_drift_resolved"
_drift_lib="$(cd "$(dirname "$_drift_self")" 2>/dev/null && pwd)/lib/host-role.sh"
if [ ! -r "$_drift_lib" ]; then
  echo "drift-check: cannot read $_drift_lib — host identity cannot be resolved." >&2
  exit 6
fi
# shellcheck source=lib/host-role.sh
. "$_drift_lib"

if [ "${1:-}" = "--detect-role" ]; then
  if [ "$#" -ge 2 ]; then detect_role "$2"; else detect_role "$(local_ipv4s | tr '\n' ' ')"; fi
  exit 0
fi

DRIFT_REPO="${DRIFT_REPO:-$HOME/workspace/devrc}"
DRIFT_UNTRACKED_MAX="${DRIFT_UNTRACKED_MAX:-10}"
DRIFT_UNREACHABLE_ESCALATE="${DRIFT_UNREACHABLE_ESCALATE:-4}"
DRIFT_STATE_DIR="${DRIFT_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/drift-check}"

# 🔴 Both tunables are INTERPOLATED INTO A SCRIPT THAT RUNS ON THE OTHER HOST
# (piped to `bash -s` over ssh), so a non-integer value is remote code execution
# on a machine this script is otherwise forbidden to touch. Operator-controlled,
# hence low exploitability — but it is a passivity hole on the far side of the
# ssh hop, which the static scanner structurally cannot see. Validate here; the
# printf below ALSO uses %q, deliberately belt-and-braces.
require_int() { # require_int <name> <value>
  case "$2" in
    ''|*[!0-9]*) echo "drift-check: $1 must be a non-negative integer, got: $2" >&2; exit 2 ;;
  esac
}
require_int DRIFT_UNTRACKED_MAX "$DRIFT_UNTRACKED_MAX"
require_int DRIFT_UNREACHABLE_ESCALATE "$DRIFT_UNREACHABLE_ESCALATE"

DO_LOCAL=1
DO_REMOTE=1
for a in "$@"; do
  case "$a" in
    --no-remote|--no-laptop) DO_REMOTE=0 ;;
    --no-local)  DO_LOCAL=0 ;;
    --detect-role) : ;;   # handled above
    # Print the contiguous comment block after the shebang (no line numbers to drift).
    -h|--help)   awk 'NR>1 { if (/^#/) print; else exit }' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

# Refuse the combination that looks at nothing. Without this, `--no-local
# --no-remote` printed "no drift — both hosts on branch main at origin/main" and
# exited 0 having checked neither host: a green from a checker wired to nothing,
# which is precisely the failure this whole subsystem exists to prevent.
if [ "$DO_LOCAL" = 0 ] && [ "$DO_REMOTE" = 0 ]; then
  echo "drift-check: --no-local and --no-remote together check NOTHING." >&2
  echo "  refusing to print a pass for a run that looked at no host." >&2
  exit 2
fi

LOCAL_ROLE="$(resolve_local_role)"
if [ "$LOCAL_ROLE" != workbench ] && [ "$LOCAL_ROLE" != laptop ]; then
  echo "drift-check: could not identify this host (role='$LOCAL_ROLE')." >&2
  echo "  local IPv4s: $(local_ipv4s | tr '\n' ' ')" >&2
  echo "  expected a workbench ($WORKBENCH_IP_PRIMARY) or laptop ($LAPTOP_IP_PRIMARY) address." >&2
  echo "  override with SHIP_ROLE=workbench|laptop to force." >&2
  exit 6
fi
REMOTE_ROLE="$(remote_role_of "$LOCAL_ROLE")"
REMOTE_SSH="$(remote_ssh_of "$LOCAL_ROLE")"

# severity <rc> -> a comparable number; higher = worse. Unknown codes rank above
# every known one so a NEW failure mode can never be silently outranked into
# invisibility by a merely-behind host.
severity() {
  case "${1:-0}" in
    0)  echo 0 ;;
    8)  echo 70 ;;
    13) echo 60 ;;
    # 6 cannot arrive here today (the script exits 6 before any host leg runs),
    # but it is a code this file OWNS, and an owned code with no case would rank
    # 99 — above rc 8 — which is the wrong answer for "I could not identify the
    # local host" versus "a host has un-pushed commits".
    6)  echo 58 ;;
    4)  echo 55 ;;
    3)  echo 50 ;;
    12) echo 40 ;;
    10) echo 30 ;;
    *)  echo 99 ;;
  esac
}

# ── The per-host CHECK routine ────────────────────────────────────────────────
# Run identically on each host: locally via `bash -c`, remotely by PIPING it to
# `bash -s` over ssh. It is piped rather than inlined because BOTH hosts' login
# shell is zsh, and zsh does not word-split an unbraced `$var` — an inlined
# multi-command script silently behaves differently there. `bash -s` removes the
# interpreter from the equation entirely.
#
# READ-ONLY BY CONSTRUCTION: the only git commands here are `fetch`, `rev-parse`,
# `rev-list`, `symbolic-ref`, `show-ref`, `ls-files` and `log`. `fetch` writes
# remote-tracking refs only.
CHECK='
set -uo pipefail
repo="${DRIFT_REPO:-$HOME/workspace/devrc}"
label="${DRIFT_LABEL:-host}"
maxu="${DRIFT_UNTRACKED_MAX:-10}"
say() { echo "[$label] $*"; }

[ -d "$repo/.git" ] || [ -f "$repo/.git" ] || { say "no repo at $repo"; exit 3; }
cd "$repo" || { say "no repo at $repo"; exit 3; }

# The stderr of git fetch is CAPTURED AND REPRINTED, never discarded: rc 4 is a
# recurring code (key rotation, DNS, host-key churn) and for a unit whose only
# output is the journal, that message is the ONLY diagnostic there will ever be.
fetch_err=$(git fetch origin -q 2>&1) || {
  say "git fetch failed — cannot evaluate drift"
  printf "%s\n" "$fetch_err" | sed "s|^|[$label]   git: |"
  exit 4
}
target=$(git rev-parse -q --verify origin/main) || {
  say "no origin/main after a successful fetch — remote/branch misconfigured."
  say "  check: git -C $repo remote -v ; git -C $repo branch -r"
  exit 4
}

# --- INFORMATION (never affects the exit code) --------------------------------
untracked=$(git ls-files --others --exclude-standard 2>/dev/null)
if [ -n "$untracked" ]; then
  n=$(printf "%s\n" "$untracked" | wc -l | tr -d " ")
  say "untracked: $n file(s) — present on this host only, in no commit and no backup"
  printf "%s\n" "$untracked" | head -n "$maxu" | sed "s|^|[$label]     - |"
  [ "$n" -gt "$maxu" ] && say "    ... and $(( n - maxu )) more"
else
  say "untracked: 0"
fi

# --- Which branch is checked out? ---------------------------------------------
branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo DETACHED)
off_main=0
if [ "$branch" != "main" ]; then
  off_main=1
  say "DRIFT — checkout is on '"'"'$branch'"'"', not on branch main."
  # 🔴 NO VERDICT HERE. Whether ship.sh will move this checkout back to main
  # depends on the state of local main, which has not been computed yet: if main
  # is AHEAD, ship.sh skips the host entirely and moves nothing. The advisory is
  # therefore printed at each exit point below, where it can be true.
fi

# --- Where is the LOCAL main branch relative to origin/main? ------------------
# Checked whatever is checked out: local main can be diverged while HEAD sits on
# some feature branch, and that is still a host that will be skipped forever.
if ! git show-ref --verify --quiet refs/heads/main; then
  say "DRIFT — no local main branch exists in this checkout."
  exit 12
fi
main=$(git rev-parse -q --verify refs/heads/main) || {
  say "cannot resolve refs/heads/main"; exit 4; }

counts=$(git rev-list --left-right --count origin/main...main 2>/dev/null) || {
  say "cannot compare main to origin/main"; exit 4; }
behind=$(printf "%s" "$counts" | awk "{print \$1}")
ahead=$(printf "%s" "$counts" | awk "{print \$2}")

if [ "${ahead:-0}" -gt 0 ]; then
  # AHEAD or DIVERGED — the dangerous one. ship.sh cannot fast-forward this host,
  # so it is skipped on EVERY future run and silently stops receiving changes.
  if [ "${behind:-0}" -gt 0 ]; then
    say "🔴 DRIFT — local main has DIVERGED: $ahead un-pushed commit(s), $behind behind."
  else
    say "🔴 DRIFT — local main is AHEAD by $ahead un-pushed commit(s)."
  fi
  say "  ship.sh SKIPS this host (rc=8) and will keep skipping it — it is receiving NOTHING."
  if [ "$off_main" = 1 ]; then
    say "  the checkout is ALSO off main (on '"'"'$branch'"'"') and ship.sh will NOT move it back:"
    say "  it skips this host before touching the checkout at all."
  fi
  git log --oneline --no-decorate origin/main..main 2>/dev/null | head -n 10 | sed "s|^|[$label]     + |"
  say "  rescue (on that host): git branch <topic> main && git push -u origin <topic>"
  say "  then confirm from ANOTHER host, then: git reset --keep origin/main   (never --hard)"
  exit 8
fi

# 🔴 ORDER IS LOAD-BEARING, in BOTH directions.
#   * off-main is checked AFTER the ahead/diverged block, because un-pushed
#     commits on main while HEAD sits on a feature branch are the rc 8 shape —
#     hoisting this above the ahead block reports rc 12 and the FALSE advisory
#     "ship.sh will move it" for a host ship.sh would skip forever.
#     (`test_off_main_with_diverged_main_is_rc8` is the regression pin.)
#   * off-main is checked BEFORE the behind block, because the severity table
#     this file publishes ranks 12 above 10; returning 10 for a host that is BOTH
#     off main and behind would contradict its own ordering.
if [ "$off_main" = 1 ]; then
  if [ "${behind:-0}" -gt 0 ]; then
    say "  local main is also BEHIND by $behind — ship.sh can still fast-forward it and"
    say "  land the checkout back on main; anything committed on '"'"'$branch'"'"' stays invisible to origin/main."
  else
    say "  ship.sh will land the checkout back on main; anything committed on"
    say "  '"'"'$branch'"'"' is invisible to origin/main."
  fi
  exit 12
fi

if [ "${behind:-0}" -gt 0 ]; then
  say "DRIFT — local main is BEHIND origin/main by $behind commit(s) — needs a ship."
  say "  fix: scripts/ship.sh"
  exit 10
fi

say "✅ clean — on branch main, main == origin/main ($target)"
exit 0
'

rc=0
note_rc() { # note_rc <rc> — keep the MOST SEVERE code seen (see header)
  local new="$1"
  [ "$new" = 0 ] && return 0
  if [ "$(severity "$new")" -gt "$(severity "$rc")" ]; then rc="$new"; fi
}

# --- What did this run actually LOOK at? --------------------------------------
# Tracked so the summary can never again claim "both hosts" for a run that
# checked one, or none.
CHECKED=""
UNCHECKED=""
mark_checked()   { CHECKED="${CHECKED:+$CHECKED, }$1"; }
mark_unchecked() { UNCHECKED="${UNCHECKED:+$UNCHECKED, }$1"; }

# --- Consecutive-unreachable streak (see "UNREACHABLE IS NOT DRIFT" above) -----
# The ONLY file this script writes, and it lives outside every repo.
_streak_file() { printf '%s\n' "$DRIFT_STATE_DIR/unreachable-${1:-remote}"; }

streak_bump() { # streak_bump <role> -> new streak, or -1 if it cannot be persisted
  local f prev next
  f="$(_streak_file "$1")"
  mkdir -p "$DRIFT_STATE_DIR" 2>/dev/null || { echo -1; return 0; }
  prev="$(cat "$f" 2>/dev/null || true)"
  case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
  next=$(( prev + 1 ))
  printf '%s\n' "$next" > "$f" 2>/dev/null || { echo -1; return 0; }
  echo "$next"
}

streak_reset() { # streak_reset <role> — the host answered; the run of misses ends
  local f
  f="$(_streak_file "$1")"
  [ -d "$DRIFT_STATE_DIR" ] || return 0
  printf '0\n' > "$f" 2>/dev/null || true
}

if [ "$DO_LOCAL" = 1 ]; then
  echo "=== local ($LOCAL_ROLE) ==="
  DRIFT_REPO="$DRIFT_REPO" DRIFT_LABEL="$LOCAL_ROLE" \
    DRIFT_UNTRACKED_MAX="$DRIFT_UNTRACKED_MAX" \
    bash -c "$CHECK"
  note_rc "$?"
  mark_checked "$LOCAL_ROLE (local)"
  echo
else
  mark_unchecked "$LOCAL_ROLE (local, --no-local)"
fi

if [ "$DO_REMOTE" = 1 ]; then
  echo "=== remote ($REMOTE_ROLE — $REMOTE_SSH) ==="
  # DRIFT_REPO is deliberately NOT forwarded: it is a local override, and the
  # remote host's repo lives at its own $HOME/workspace/devrc.
  # `bash -s` (piped, not inlined) — see the CHECK header re: zsh.
  # %q, not %s — these two values are executed on ANOTHER host (see require_int).
  printf 'DRIFT_LABEL=%q\nDRIFT_UNTRACKED_MAX=%q\n%s\n' \
    "$REMOTE_ROLE" "$DRIFT_UNTRACKED_MAX" "$CHECK" \
    | ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE_SSH" bash -s
  remrc=$?
  # ssh itself exits 255 on a connection/auth failure — that is "we could not
  # look". For a deadman that must never read as a pass... but it must not read
  # as DRIFT either: see "UNREACHABLE IS NOT DRIFT" in the header.
  if [ "$remrc" = 255 ]; then
    echo "[$REMOTE_ROLE] ssh to $REMOTE_SSH failed or timed out."
    remrc=13
  fi

  if [ "$remrc" = 13 ]; then
    streak="$(streak_bump "$REMOTE_ROLE")"
    echo "[$REMOTE_ROLE] UNREACHABLE — drift on that host was NOT evaluated. This is not a pass."
    if [ "$streak" -lt 0 ]; then
      echo "[$REMOTE_ROLE]   the consecutive-miss counter under $DRIFT_STATE_DIR could not be"
      echo "[$REMOTE_ROLE]   persisted, so 'for how long' is unknowable — ESCALATING (rc 13)."
      note_rc 13
      mark_unchecked "$REMOTE_ROLE (remote, UNREACHABLE — streak unknown, escalated)"
    elif [ "$streak" -ge "$DRIFT_UNREACHABLE_ESCALATE" ]; then
      echo "[$REMOTE_ROLE]   🔴 $streak CONSECUTIVE unreachable checks (threshold $DRIFT_UNREACHABLE_ESCALATE) —"
      echo "[$REMOTE_ROLE]   that is no longer 'the laptop is shut'. ESCALATING (rc 13)."
      note_rc 13
      mark_unchecked "$REMOTE_ROLE (remote, UNREACHABLE x$streak — escalated)"
    else
      echo "[$REMOTE_ROLE]   $streak/$DRIFT_UNREACHABLE_ESCALATE consecutive — NOT escalated: a laptop that is"
      echo "[$REMOTE_ROLE]   off, asleep or off-LAN is the expected cause and must not look like drift."
      echo "[$REMOTE_ROLE]   At $DRIFT_UNREACHABLE_ESCALATE consecutive misses this becomes rc 13 and fails the unit."
      mark_unchecked "$REMOTE_ROLE (remote, UNREACHABLE $streak/$DRIFT_UNREACHABLE_ESCALATE — not yet escalated)"
    fi
  else
    # It answered — with a verdict, good or bad. The run of misses is over.
    streak_reset "$REMOTE_ROLE"
    note_rc "$remrc"
    mark_checked "$REMOTE_ROLE (remote)"
  fi
  echo
else
  mark_unchecked "$REMOTE_ROLE (remote, --no-remote)"
fi

# 🔴 The summary states WHAT WAS CHECKED. It previously said "both hosts on
# branch main at origin/main" regardless — including for a --no-remote run that
# looked at one host, and for a --no-local --no-remote run that looked at none.
if [ "$rc" = 0 ]; then
  if [ -n "$CHECKED" ]; then
    echo "drift-check: no drift on the host(s) CHECKED: $CHECKED — each on branch main at origin/main."
  else
    echo "drift-check: NO HOST WAS SUCCESSFULLY CHECKED — this is not a clean bill of health."
  fi
else
  echo "drift-check: DRIFT (rc=$rc) — see per-host lines above."
  echo "  checked: ${CHECKED:-none}"
  echo "  rc3=no-repo  rc4=fetch/origin-main-unavailable  rc6=host-unidentified"
  echo "  rc8=DIVERGED/AHEAD:un-pushed-commits(ship.sh will skip this host forever)"
  echo "  rc10=behind(needs a ship)  rc12=not-on-branch-main"
  echo "  rc13=remote unreachable for >=$DRIFT_UNREACHABLE_ESCALATE consecutive runs"
fi
[ -n "$UNCHECKED" ] && echo "drift-check: NOT checked: $UNCHECKED"
exit "$rc"
