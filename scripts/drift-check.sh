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
# 🔴 This script must NEVER mutate either host's working state. It may `git
# fetch` (which writes only remote-tracking refs, never the working tree, the
# index, or any local branch). It must NEVER checkout, switch, merge,
# fast-forward, rebase, reset, stash, clean, commit, or run `home-manager`.
# A deadman that repairs is a deployer with no supervision; a deadman that
# reports is a deadman. `scripts/tests/test_drift_check.py` enforces this
# statically against this file's executable lines.
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
#   3   repo missing on that host
#   4   git fetch failed, or origin/main is missing / HEAD unborn
#   6   local host could not be identified (see detect_role)
#   8   DRIFT — local `main` has DIVERGED or is AHEAD of origin/main (un-pushed
#       commits). 🔴 THE DANGEROUS ONE — ship.sh will skip this host forever.
#   10  DRIFT — local `main` is BEHIND origin/main (just needs a ship)
#   12  DRIFT — the checkout is not ON branch `main`
#   13  host unreachable (ssh failed / timed out)
#
# When several hosts fail, the exit code is the MOST SEVERE, not the first —
# this differs from ship.sh on purpose. ship.sh keeps the first non-zero because
# every host's line is printed anyway and a human is reading them live. Nobody
# is reading a timer's output, so the single number it hands to systemd must be
# the worst thing found, or an un-pushed workbench could hide behind a merely
# behind laptop. Severity order (worst first):
#     8  >  13  >  4  >  3  >  12  >  10
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
#   DRIFT_UNTRACKED_MAX  max untracked paths listed per host (default 10)
set -uo pipefail

# --- Host identity: SOURCED, never copied (see header) ------------------------
_drift_lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/lib/host-role.sh"
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

git fetch origin -q 2>/dev/null || { say "git fetch failed — cannot evaluate drift"; exit 4; }
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
  say "  ship.sh will move it, but anything committed there is invisible to origin/main."
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
  git log --oneline --no-decorate origin/main..main 2>/dev/null | head -n 10 | sed "s|^|[$label]     + |"
  say "  rescue (on that host): git branch <topic> main && git push -u origin <topic>"
  say "  then confirm from ANOTHER host, then: git reset --keep origin/main   (never --hard)"
  exit 8
fi

if [ "${behind:-0}" -gt 0 ]; then
  say "DRIFT — local main is BEHIND origin/main by $behind commit(s) — needs a ship."
  say "  fix: scripts/ship.sh"
  exit 10
fi

if [ "$off_main" = 1 ]; then exit 12; fi

say "✅ clean — on branch main, main == origin/main ($target)"
exit 0
'

rc=0
note_rc() { # note_rc <rc> — keep the MOST SEVERE code seen (see header)
  local new="$1"
  [ "$new" = 0 ] && return 0
  if [ "$(severity "$new")" -gt "$(severity "$rc")" ]; then rc="$new"; fi
}

if [ "$DO_LOCAL" = 1 ]; then
  echo "=== local ($LOCAL_ROLE) ==="
  DRIFT_REPO="$DRIFT_REPO" DRIFT_LABEL="$LOCAL_ROLE" \
    DRIFT_UNTRACKED_MAX="$DRIFT_UNTRACKED_MAX" \
    bash -c "$CHECK"
  note_rc "$?"
  echo
fi

if [ "$DO_REMOTE" = 1 ]; then
  echo "=== remote ($REMOTE_ROLE — $REMOTE_SSH) ==="
  # DRIFT_REPO is deliberately NOT forwarded: it is a local override, and the
  # remote host's repo lives at its own $HOME/workspace/devrc.
  # `bash -s` (piped, not inlined) — see the CHECK header re: zsh.
  printf 'DRIFT_LABEL=%s\nDRIFT_UNTRACKED_MAX=%s\n%s\n' \
    "$REMOTE_ROLE" "$DRIFT_UNTRACKED_MAX" "$CHECK" \
    | ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE_SSH" bash -s
  remrc=$?
  # ssh itself exits 255 on a connection/auth failure — that is "we could not
  # look", which for a deadman must be LOUD, never a green.
  if [ "$remrc" = 255 ]; then
    echo "[$REMOTE_ROLE] UNREACHABLE — ssh to $REMOTE_SSH failed or timed out."
    echo "[$REMOTE_ROLE]   drift on that host CANNOT be evaluated — this is not a pass."
    remrc=13
  fi
  note_rc "$remrc"
  echo
fi

if [ "$rc" = 0 ]; then
  echo "drift-check: no drift — both hosts on branch main at origin/main."
else
  echo "drift-check: DRIFT (rc=$rc) — see per-host lines above."
  echo "  rc3=no-repo  rc4=fetch/origin-main-unavailable  rc6=host-unidentified"
  echo "  rc8=DIVERGED/AHEAD:un-pushed-commits(ship.sh will skip this host forever)"
  echo "  rc10=behind(needs a ship)  rc12=not-on-branch-main  rc13=host-unreachable"
fi
exit "$rc"
