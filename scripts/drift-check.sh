#!/usr/bin/env bash
# drift-check — PASSIVE deadman for the devrc two-host fleet.
#
# Answers one question, unattended, on a timer: "is either host silently no
# longer receiving changes?" It REPORTS. It never fixes.
#
# 🔴 THAT QUESTION HAS TWO HALVES, and for a long time this file only asked the
# first. GIT PARITY (is the checkout still tracking origin/main?) and HOST PARITY
# (is what the checkout describes actually DEPLOYED and the same on both
# machines?) are independent. Every skill on the laptop was a dangling symlink
# into a garbage-collected /nix/store path while `git log` matched origin/main
# exactly — perfect git parity, zero host parity, and this script said clean.
# See "the per-host HOST-PARITY routine" below and exit codes 14 and 15.
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
# is an ALLOWLIST, not a keyword blocklist: a `git` invocation it can RESOLVE
# STATICALLY must name one of a fixed set of read-only subcommands. The check is
# anchored at each COMMAND SEPARATOR (`;`, `&&`, `||`, `|`, `$(`), not at the
# start of the line — so `say "hi" && git checkout main` is caught — and it
# recurses through wrappers (`timeout`, `flock`, `stdbuf`, `ionice`, `nice`) and
# through `ssh <target> …` / `bash -c` / `sh -c`, which each hide a command line
# inside their arguments. `ssh <target> git checkout …` matters most: it mutates
# THE OTHER HOST, this script's primary hazard, and `ssh` is stubbed out of every
# behavioural test, so it was invisible to both layers until #371.
#
# 🔴 WHAT THE SCANNER CANNOT SEE — an accurate claim beats a reassuring one:
#   * a command word produced by EXPANSION (`$g checkout main`, `eval "$cmd"`).
#     The scanner flags the alias where it can see one being made (`g=git`), but
#     an alias built from parts, or read from a file, resolves to nothing static.
#   * anything inside a string that only becomes code on the FAR SIDE of the ssh
#     hop. The $CHECK payload is scanned because it is literal text in this file;
#     a payload assembled at runtime would not be. `require_int` plus `%q` on the
#     two interpolated values is what covers that, not the scanner.
# The BEHAVIOURAL layer (`test_run_against_diverged_repo_mutates_nothing`) closes
# the local-checkout half of both holes, and closes it for shapes nobody has
# enumerated. Neither layer covers a runtime-built mutation of the REMOTE host.
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
#   2   usage error (unknown flag, non-integer tunable), or a RUN THAT CHECKED
#       NO HOST AT ALL — either because the flags asked for none (`--no-local
#       --no-remote`) or because the only host it was asked to look at could not
#       be reached. rc 0 from a run that observed nothing is the vacuous green
#       this whole subsystem exists to prevent, so it is never emitted.
#   3   repo missing on that host
#   4   git fetch failed, or origin/main is missing / HEAD unborn
#   6   local host could not be identified (see detect_role)
#   8   DRIFT — local `main` has DIVERGED or is AHEAD of origin/main (un-pushed
#       commits). 🔴 THE DANGEROUS ONE — ship.sh will skip this host forever.
#   10  DRIFT — local `main` is BEHIND origin/main (just needs a ship)
#   12  DRIFT — the checkout is not ON branch `main`
#   13  remote host unreachable for $DRIFT_UNREACHABLE_ESCALATE CONSECUTIVE runs
#   14  DRIFT — a host has MANAGED SYMLINKS THAT RESOLVE TO NOTHING. 🔴 The one
#       git is structurally blind to: every skill on the laptop dangled into a
#       garbage-collected /nix/store path while the checkout was byte-identical
#       to origin/main. Git parity is not host parity.
#   15  DRIFT — HOST PARITY: the two hosts' settings.json top-level KEY SETS or
#       their enabledPlugins differ, or a host has a plugin enabled that is not
#       installed there.
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
#     8  >  14  >  13  >  6  >  4  >  3  >  12  >  15  >  10
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
#   DRIFT_DANGLING_MAX   max dangling symlinks listed per host (default 10, integer)
#   DRIFT_PARITY_ROOTS   space-separated dirs, relative to $HOME, scanned for
#                        dangling MANAGED symlinks (default ".claude .config/opencode")
#   DRIFT_MANAGED_PREFIX what counts as a MANAGED symlink target (default
#                        "/nix/store/"). Exists so the test suite can build a
#                        fixture tree; the default is the only correct value on
#                        a real host, and is NOT forwarded over ssh.
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
DRIFT_DANGLING_MAX="${DRIFT_DANGLING_MAX:-10}"
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
require_int DRIFT_DANGLING_MAX "$DRIFT_DANGLING_MAX"
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
    # 14 (a host's managed symlinks resolve to nothing) sits between 8 and 13.
    # It is BELOW 8 because rc 8 means work exists on exactly one machine and a
    # careless fix destroys it, whereas a broken deployment is repaired by a
    # switch with nothing to lose. It is ABOVE 13 because it is a host we DID
    # observe, saying something is wrong — 13 only says we could not look.
    14) echo 65 ;;
    13) echo 60 ;;
    # 6 cannot arrive here today (the script exits 6 before any host leg runs),
    # but it is a code this file OWNS, and an owned code with no case would rank
    # 99 — above rc 8 — which is the wrong answer for "I could not identify the
    # local host" versus "a host has un-pushed commits".
    6)  echo 58 ;;
    4)  echo 55 ;;
    3)  echo 50 ;;
    12) echo 40 ;;
    # 15 ranks BELOW 12 and above 10: a key-set or plugin difference is a real
    # divergence, but ship.sh does not fix it and it costs the operator a
    # capability, not a commit. 14 ranks just under 8 — see the table in the
    # header for why un-pushed commits still outrank a broken deployment.
    15) echo 35 ;;
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

# ── The per-host HOST-PARITY routine ──────────────────────────────────────────
# 🔴 GIT PARITY IS NOT HOST PARITY. The CHECK above answers "is this host still
# receiving commits?" — and it answered YES, correctly, for the whole period in
# which every skill on the laptop was a dangling symlink into a garbage-collected
# /nix/store path. `git log` matched, `git status` was clean, origin/main was the
# checked-out HEAD, and ~/.claude/skills/*/SKILL.md resolved to nothing. A
# deadman that reports "clean" for that host is the vacuous green this subsystem
# exists to prevent, one level up from the one it was built for.
#
# So this payload reports the differences that MATTER between the two machines
# and are invisible to git:
#
#   1. DANGLING MANAGED SYMLINKS. home-manager deploys by symlinking into
#      /nix/store. A link whose target no longer exists is a file the operator
#      believes is deployed and which is not there. rc 14.
#   2. settings.json TOP-LEVEL KEY SET. Compared across hosts by the driver.
#   3. enabledPlugins, and any plugin ENABLED BUT NOT INSTALLED. rc 15.
#
# 🔴 WHAT "MANAGED" MEANS HERE — STRUCTURAL, NOT SPELLED. A managed link is one
# whose IMMEDIATE target starts with /nix/store/. Nothing hardcodes `skills/`,
# so a new managed subtree is covered the day it is added. Three consequences
# fall out of that definition for free, and each one is a false positive this
# check would otherwise have produced:
#   * ~/.claude/debug/latest points at a SIBLING transcript file and is
#     routinely stale — Claude Code runtime state, not a deployment. Not a store
#     target, so it is not counted. (It is dangling on the workbench right now.)
#   * ~/.claude/skills/clickup/ is a standalone git checkout with ~176 pnpm
#     symlinks under node_modules/, all pointing at RELATIVE paths. Legitimately
#     unmanaged, and excluded by the store-target rule even before pruning.
#   * mkOutOfStoreSymlink links (the `browser` skill) point INTO the store at a
#     path that is itself a symlink to the working tree. First hop is a store
#     path, so they ARE examined — and `[ -e ]` follows the whole chain, so a
#     broken out-of-store link is caught too.
# Directories are pruned for SPEED (never for correctness) when they are named
# node_modules or contain a .git — a nested checkout is by construction not
# home-manager's. Measured on the workbench: 0.43s over ~18500 entries.
#
# 🔴 THE COUNT OF LINKS EXAMINED IS REPORTED ALONGSIDE THE COUNT THAT DANGLED,
# ALWAYS. "0 dangling" from a scan that walked 0 links is indistinguishable from
# a clean host, and is exactly how a scanner wired to nothing reads as a pass.
# The pair is the claim; neither number alone is.
#
# 🔴 NO `find`. The laptop resolves `find` to BUSYBOX, which does not implement
# `-xtype` — and rejects it by printing usage to stderr and EXITING 0. So
# `find -xtype l | wc -l` yields a confident 0 dangling on that host, forever,
# from a check wired to nothing. Measured 2026-08-11. The walk below is bash
# builtins plus `readlink`, which behaves identically under both.
#
# READ-ONLY BY CONSTRUCTION: readlink, sed, sort, tr, head and shell builtins.
# It writes nothing and creates nothing.
#
# Variables that appear inside $(( )) are UPPERCASE on purpose: the test suite
# tokenizes `$(( lower + 1 ))` as a command word in command position, and an
# uppercase name is dropped by that filter instead of needing a ledger entry
# declaring an arithmetic operand to be "prose".
PARITY='
set -uo pipefail
label="${DRIFT_LABEL:-host}"
maxd="${DRIFT_DANGLING_MAX:-10}"
psay() { echo "[$label] $*"; }

# What makes a symlink MANAGED. The default is the only correct value in
# production — home-manager deploys by pointing into the nix store, and
# test_the_managed_prefix_defaults_to_the_nix_store pins it. It is a variable
# solely so the suite can build a fixture tree it fully controls: a HEALTHY
# managed link needs a target that both matches the prefix AND exists, which no
# test can arrange under a real /nix/store. Deliberately NOT forwarded to the
# remote host — production always uses the default there, and every value this
# script sends over ssh is a value that has to be proved safe.
mprefix="${DRIFT_MANAGED_PREFIX:-/nix/store/}"

P_EXAMINED=0
P_DANGLED=0
p_list=""

# Loop and local variable names are UPPERCASE throughout this payload for the
# same reason the arithmetic operands are: the suite tokenizes `for x in …` and
# `local v` with `x`/`v` in command position, and an uppercase name is dropped by
# its `[a-z]…` filter. The alternative is a ledger entry per variable declaring
# it "prose", which would be widening an accounting guard to fit new code.
p_walk() { # p_walk <dir> — recurse, counting managed symlinks and dead ones
  local E BASE T
  for E in "$1"/* "$1"/.*; do
    BASE="${E##*/}"
    [ "$BASE" = "." ] && continue
    [ "$BASE" = ".." ] && continue
    [ -e "$E" ] || [ -L "$E" ] || continue
    if [ -L "$E" ]; then
      T="$(readlink "$E")"
      case "$T" in
        "$mprefix"*)
          P_EXAMINED=$(( P_EXAMINED + 1 ))
          if [ ! -e "$E" ]; then
            P_DANGLED=$(( P_DANGLED + 1 ))
            p_list="$p_list$E -> $T
"
          fi
          ;;
      esac
      continue
    fi
    [ -d "$E" ] || continue
    [ "$BASE" = "node_modules" ] && continue
    [ -e "$E/.git" ] && continue
    p_walk "$E"
  done
}

# Roots are relative to $HOME so the whole payload is exercisable against a
# fixture home in the test suite without a single $HOME-conditional skip.
roots="${DRIFT_PARITY_ROOTS:-.claude .config/opencode}"
P_ROOTS_SEEN=0
for R in $roots; do
  if [ -d "$HOME/$R" ]; then
    P_ROOTS_SEEN=$(( P_ROOTS_SEEN + 1 ))
    p_walk "$HOME/$R"
  fi
done

p_rc=0
if [ "$P_ROOTS_SEEN" = 0 ]; then
  psay "managed symlinks: NOT EVALUATED — none of the roots exist ($roots)"
  psay "  a scan that examined nothing is not a clean scan; it is no scan."
else
  psay "managed symlinks: examined=$P_EXAMINED dangling=$P_DANGLED (roots: $roots)"
fi
if [ "$P_DANGLED" -gt 0 ]; then
  psay "🔴 DRIFT — $P_DANGLED of $P_EXAMINED managed symlink(s) point at a path that does not exist."
  psay "  home-manager believes these are deployed. They resolve to nothing."
  printf "%s" "$p_list" | head -n "$maxd" | sed "s|^|[$label]     x |"
  if [ "$P_DANGLED" -gt "$maxd" ]; then
    psay "    ... and $(( P_DANGLED - maxd )) more"
  fi
  psay "  fix (on that host): home-manager switch --flake ~/workspace/devrc --impure"
  p_rc=14
fi

# --- settings.json: KEY NAMES ONLY --------------------------------------------
# 🔴 Never the values. This file holds tokens, hook command lines and permission
# rules, and this output goes to a systemd journal.
#
# 🔴 THE EXTRACTOR HAS A FORMAT DEPENDENCY, AND IT FAILS LOUD. Top-level keys are
# read as the 2-space-indented lines Claude Code writes. Cross-checked against
# json.load on the real 14 KB workbench file (2026-08-11): identical 11-key set.
# If the file is ever minified the extractor yields NOTHING — and an empty result
# is reported as UNEVALUATED, never as "no divergence", because those two are the
# same observation to a diff and only one of them is good news.
# 🔴 EACH EXTRACTION IS SPLIT FROM ITS NORMALISATION, ON PURPOSE. The obvious
# one-liner `x="$(sed … | sort | tr …)"` hides `sort` and `tr` from the reverse
# PATH guard in the suite: that tokenizer does not honour a backslash-escaped quote
# inside double quotes, so the whole pipeline collapses into one `sed` segment
# and the two commands after the pipes are never seen. The guard then passes
# while the unit PATH goes unchecked for them — a guard that cannot see a
# command is not accounting for it. Extract, then normalise on its own line,
# where every command word sits in plain command position.
#
# The sed scripts are SINGLE-quoted (the same quote-dance CHECK already uses)
# rather than double-quoted with a backslash-escaped quote, for the same reason:
# with `\"` the tokenizer ends the quoted run early and the brace in the address
# reads as a command separator, leaving the trailing `/p` looking like a command.
norm_set() { # norm_set <newline-list> -> sorted, space-separated, or "" if empty
  [ -n "$1" ] || return 0
  printf "%s\n" "$1" | sort | tr "\n" " "
}

set_file="$HOME/.claude/settings.json"
skeys="UNEVALUATED"
eplug="UNEVALUATED"
if [ -r "$set_file" ]; then
  k="$(sed -n '"'"'s/^  "\([^"]*\)":.*/\1/p'"'"' "$set_file")"
  if [ -n "$k" ]; then
    skeys="$(norm_set "$k")"
    # enabledPlugins may legitimately be absent — that is a FACT, not a failure.
    if [ -n "$(sed -n '"'"'/^  "enabledPlugins":/p'"'"' "$set_file")" ]; then
      eplist="$(sed -n '"'"'/^  "enabledPlugins": {/,/^  }/p'"'"' "$set_file")"
      eplist="$(printf "%s\n" "$eplist" | sed -n '"'"'s/^    "\([^"]*\)":.*/\1/p'"'"')"
      eplug="$(norm_set "$eplist")"
      [ -n "$eplug" ] || eplug="NONE"
    else
      eplug="NONE"
    fi
  else
    psay "settings.json: NOT EVALUATED — no 2-space top-level keys found in $set_file"
  fi
else
  psay "settings.json: NOT EVALUATED — $set_file is missing or unreadable"
fi

inst_file="$HOME/.claude/plugins/installed_plugins.json"
iplug="UNEVALUATED"
if [ -r "$inst_file" ]; then
  iplist="$(sed -n '"'"'/^  "plugins": {/,$p'"'"' "$inst_file")"
  iplist="$(printf "%s\n" "$iplist" | sed -n '"'"'s/^    "\([^"]*\)":.*/\1/p'"'"')"
  iplug="$(norm_set "$iplist")"
  [ -n "$iplug" ] || iplug="NONE"
fi

# --- enabled but NOT installed (per-host; needs no second host) ---------------
if [ "$eplug" != UNEVALUATED ] && [ "$eplug" != NONE ] && [ "$iplug" != UNEVALUATED ]; then
  ghost=""
  for PL in $eplug; do
    case " $iplug " in
      *" $PL "*) ;;
      *) ghost="$ghost $PL" ;;
    esac
  done
  if [ -n "$ghost" ]; then
    psay "🔴 DRIFT — plugin(s) ENABLED in settings.json but NOT installed:$ghost"
    psay "  fix (on that host): claude plugin install <plugin>"
    [ "$p_rc" = 0 ] && p_rc=15
  fi
fi

# FACT lines are the machine-readable half — the driver diffs them ACROSS hosts,
# which is the only place a key-set difference can be seen at all.
echo "[$label] FACT settings-keys $skeys"
echo "[$label] FACT enabled-plugins $eplug"
echo "[$label] FACT installed-plugins $iplug"
echo "[$label] PARITY-RC=$p_rc"
'

# The payload actually shipped to each host: the git CHECK in a SUBSHELL (so its
# many `exit`s end the subshell and not the run) followed by the parity scan.
# Composed rather than run as two ssh legs so an unreachable host is still ONE
# missed connection and ONE bump of the streak counter.
#
# The exit status is the GIT verdict, byte-for-byte what it always was — every
# pinned rc in the suite is a statement about that number. The parity verdict
# rides back on the PARITY-RC= line and is folded in by the driver through the
# same severity() table, so there is exactly one severity ranking in this file.
PAYLOAD="(
$CHECK
)
_drift_git_rc=\$?
$PARITY
exit \$_drift_git_rc"

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

# --- Reading the parity facts back off a host's output ------------------------
# Each host's payload prints its own verdict AND a few `FACT <name> <values>`
# lines. The per-host verdict (dangling links, enabled-but-absent plugins) needs
# only that host. A KEY-SET or enabledPlugins DIFFERENCE is not a property of
# either host alone — it exists only between them — so it is computed here, from
# both outputs, and only when both were actually obtained.
LOCAL_OUT=""
REMOTE_OUT=""

fact_of() { # fact_of <host-output> <fact-name> -> the value list, or "" if absent
  printf '%s\n' "$1" | sed -n "s/^\[[^]]*\] FACT $2 //p" | head -n 1
}

parity_rc_of() { # parity_rc_of <host-output> -> that host's parity rc (0 if none)
  # A host that printed no PARITY-RC line (an old drift-check.sh on the far side,
  # a truncated stream) yields 0 — deliberately: an ABSENT verdict must not
  # invent a failure. The absence is still visible, because the FACT lines are
  # missing too and the cross-host block then reports NOT COMPARED.
  local V
  V="$(printf '%s\n' "$1" | sed -n 's/^\[[^]]*\] PARITY-RC=//p' | head -n 1)"
  case "$V" in ''|*[!0-9]*) echo 0 ;; *) echo "$V" ;; esac
}

only_in() { # only_in <set-a> <set-b> -> members of a absent from b
  local X OUT=""
  for X in $1; do
    case " $2 " in
      *" $X "*) ;;
      *) OUT="$OUT $X" ;;
    esac
  done
  printf '%s' "$OUT"
}

# --- Consecutive-unreachable streak (see "UNREACHABLE IS NOT DRIFT" above) -----
# The ONLY file this script writes, and it lives outside every repo.
#
# 🔴 KNOWN AND ACCEPTED BOUNDS — documented rather than engineered away, because
# both cost more to fix than they cost to have, and both err in the SAFE
# direction (they delay an escalation; neither can invent one):
#
#  1. NOT ATOMIC. `streak_bump` is a read-modify-write with no lock, so two runs
#     overlapping in the same instant can both read N and both write N+1,
#     losing a miss. Measured on this host: 20 concurrent bumps landed 10; an
#     earlier round measured 9 on the same code, i.e. the loss is real and
#     non-deterministic, and the DIRECTION is what matters, not the number.
#     It is only
#     reachable when an operator hand-runs the script at the same moment the
#     timer fires (the timer itself is a single serialised oneshot at a 6h
#     cadence), and the effect is UNDERCOUNTING — escalation arrives later,
#     never earlier, so it cannot produce a false alarm. An atomic
#     write-temp-then-`mv` would need `mv`, which the passivity scanner
#     correctly classes as destructive, and a lock would need `flock`, which is
#     one of the wrapper shapes the scanner now recurses through; both trade a
#     real hardening of this file for a bound that only bites a human racing a
#     6-hourly timer.
#
#  2. A HAND-RUN SHARES THE COUNTER WITH THE TIMER. There is one file per remote
#     role, not one per invocation, so `scripts/drift-check.sh` typed at a
#     prompt bumps or resets the same streak the unit is keeping. Two
#     consequences worth knowing before you read an alert: hand-running while
#     the laptop is OFF pushes the streak up and can trip rc 13 sooner than the
#     6h cadence implies, and hand-running while it is ON resets a genuine
#     streak the timer had accumulated. That is deliberate — the streak is a
#     property of "how long has this host been unreachable", not of who asked —
#     but it does mean a hand-run is not a read-only observation of the ladder.
_streak_file() { printf '%s\n' "$DRIFT_STATE_DIR/unreachable-${1:-remote}"; }

streak_bump() { # streak_bump <role> -> new streak, or -1 if it cannot be persisted
  local f prev next
  f="$(_streak_file "$1")"
  mkdir -p "$DRIFT_STATE_DIR" 2>/dev/null || { echo -1; return 0; }
  prev="$(cat "$f" 2>/dev/null || true)"
  case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
  next=$(( prev + 1 ))
  # 🔴 `2>/dev/null` FIRST, then the target. Redirections are applied left to
  # right, and the shell reports a FAILED redirection on whatever fd 2 is at
  # that moment: written the other way round (`> "$f" 2>/dev/null`) an
  # unwritable state dir leaks a raw `drift-check.sh: line NNN: …: Permission
  # denied` into the journal, unprefixed, between the `[host]`-prefixed lines.
  # The fail-closed limb still fires either way; this only fixes the noise.
  printf '%s\n' "$next" 2>/dev/null > "$f" || { echo -1; return 0; }
  echo "$next"
}

streak_reset() { # streak_reset <role> — the host answered; the run of misses ends
  local f
  f="$(_streak_file "$1")"
  [ -d "$DRIFT_STATE_DIR" ] || return 0
  printf '0\n' 2>/dev/null > "$f" || true
}

if [ "$DO_LOCAL" = 1 ]; then
  echo "=== local ($LOCAL_ROLE) ==="
  # Captured rather than streamed so the FACT lines can be diffed against the
  # other host's. stderr is deliberately NOT captured — it still goes straight
  # to the terminal/journal, so a fetch failure's git stderr keeps arriving
  # exactly as before.
  LOCAL_OUT="$(DRIFT_REPO="$DRIFT_REPO" DRIFT_LABEL="$LOCAL_ROLE" \
    DRIFT_UNTRACKED_MAX="$DRIFT_UNTRACKED_MAX" \
    DRIFT_DANGLING_MAX="$DRIFT_DANGLING_MAX" \
    bash -c "$PAYLOAD")"
  note_rc "$?"
  [ -n "$LOCAL_OUT" ] && printf '%s\n' "$LOCAL_OUT"
  note_rc "$(parity_rc_of "$LOCAL_OUT")"
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
  REMOTE_OUT="$(printf 'DRIFT_LABEL=%q\nDRIFT_UNTRACKED_MAX=%q\nDRIFT_DANGLING_MAX=%q\n%s\n' \
    "$REMOTE_ROLE" "$DRIFT_UNTRACKED_MAX" "$DRIFT_DANGLING_MAX" "$PAYLOAD" \
    | ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE_SSH" bash -s)"
  remrc=$?
  [ -n "$REMOTE_OUT" ] && printf '%s\n' "$REMOTE_OUT"
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
    note_rc "$(parity_rc_of "$REMOTE_OUT")"
    mark_checked "$REMOTE_ROLE (remote)"
  fi
  echo
else
  mark_unchecked "$REMOTE_ROLE (remote, --no-remote)"
fi

# ── CROSS-HOST PARITY ─────────────────────────────────────────────────────────
# 🔴 A DIFFERENCE IS NOT A PROPERTY OF EITHER HOST. Both machines can be
# internally consistent — every symlink resolving, every enabled plugin present —
# and still disagree about which keys settings.json has or which plugins are on.
# That is only visible from here, with both outputs in hand.
#
# 🔴 AND IT IS ONLY VISIBLE WITH BOTH. One host checked is not "no divergence
# found", it is "divergence not looked for", and the two must never print the
# same way. An unreachable laptop lands here with an empty REMOTE_OUT and gets
# the SKIPPED branch — it does not silently contribute a clean parity verdict.
echo "=== host parity ($LOCAL_ROLE vs $REMOTE_ROLE) ==="
L_KEYS="$(fact_of "$LOCAL_OUT" settings-keys)"
R_KEYS="$(fact_of "$REMOTE_OUT" settings-keys)"
L_EPLUG="$(fact_of "$LOCAL_OUT" enabled-plugins)"
R_EPLUG="$(fact_of "$REMOTE_OUT" enabled-plugins)"

if [ -z "$L_KEYS" ] || [ -z "$R_KEYS" ]; then
  echo "[parity] NOT COMPARED — needs a fact set from EACH host; obtained from: ${CHECKED:-none}."
  echo "[parity]   this is not 'the hosts agree'. Nothing was compared."
elif [ "$L_KEYS" = UNEVALUATED ] || [ "$R_KEYS" = UNEVALUATED ]; then
  echo "[parity] NOT COMPARED — a host could not read or parse its settings.json (see its line above)."
else
  # KEY NAMES ONLY, never values — this output reaches the journal.
  l_only="$(only_in "$L_KEYS" "$R_KEYS")"
  r_only="$(only_in "$R_KEYS" "$L_KEYS")"
  if [ -n "$l_only" ] || [ -n "$r_only" ]; then
    echo "[parity] DRIFT — settings.json top-level KEY SETS differ (names only; no values shown):"
    [ -n "$l_only" ] && echo "[parity]   only on $LOCAL_ROLE:$l_only"
    [ -n "$r_only" ] && echo "[parity]   only on $REMOTE_ROLE:$r_only"
    note_rc 15
  else
    # Counted into a variable first, not interpolated as `$( … | wc -w )` inside
    # the message: a command substitution ENDS the printer segment, leaving the
    # rest of the sentence looking like a command line to the suite's tokenizer.
    N_KEYS="$(printf '%s' "$L_KEYS" | wc -w)"
    echo "[parity] settings.json top-level key sets AGREE ($N_KEYS key names on each host)."
  fi

  if [ "$L_EPLUG" = UNEVALUATED ] || [ "$R_EPLUG" = UNEVALUATED ]; then
    echo "[parity] enabledPlugins NOT COMPARED — unreadable on at least one host."
  else
    le="$L_EPLUG"; re="$R_EPLUG"
    [ "$le" = NONE ] && le=""
    [ "$re" = NONE ] && re=""
    el_only="$(only_in "$le" "$re")"
    er_only="$(only_in "$re" "$le")"
    if [ -n "$el_only" ] || [ -n "$er_only" ]; then
      echo "[parity] DRIFT — enabledPlugins differ:"
      [ -n "$el_only" ] && echo "[parity]   enabled only on $LOCAL_ROLE:$el_only"
      [ -n "$er_only" ] && echo "[parity]   enabled only on $REMOTE_ROLE:$er_only"
      echo "[parity]   fix: claude plugin install <plugin> on the host that lacks it."
      note_rc 15
    else
      echo "[parity] enabledPlugins AGREE."
    fi
  fi
fi
echo

# 🔴 The summary states WHAT WAS CHECKED. It previously said "both hosts on
# branch main at origin/main" regardless — including for a --no-remote run that
# looked at one host, and for a --no-local --no-remote run that looked at none.
if [ "$rc" = 0 ]; then
  if [ -n "$CHECKED" ]; then
    # 🔴 No phrasing here may name a host this run did not contact — the wording
    # it replaced said "both hosts" unconditionally, and read as coverage the run
    # never had (`test_a_single_host_run_does_not_claim_both_hosts`). The same
    # trap now exists one level down: the CROSS-HOST comparison needs a fact set
    # from each machine, so a clean rc here is not a claim that it ran.
    echo "drift-check: no drift on the host(s) CHECKED: $CHECKED — each on branch main at"
    echo "  origin/main, with every managed symlink resolving. The CROSS-HOST comparison is"
    echo "  a separate claim: read the [parity] block above for whether it ran at all."
  else
    # 🔴 CHECKED NOTHING, AND SAID SO — but used to hand systemd a 0 anyway.
    # Reachable as `--no-local` with the remote unreachable BELOW the escalation
    # threshold: the text says "this is not a clean bill of health" and the exit
    # code says "clean". systemd reads the code. It is the same shape the
    # `--no-local --no-remote` refusal above is already rc 2 for, so it gets the
    # same code: a run that observed no host is a usage outcome, not a verdict.
    #
    # 🔴 GUARDED ON rc = 0, deliberately. This can only ever turn a ZERO into a
    # 2 — it can never rewrite a real verdict, so an rc 8 stays 8 and still
    # reaches OnFailure. (`test_local_rc8_still_wins_when_the_remote_is_
    # unreachable` and `test_checked_nothing_does_not_rewrite_a_real_verdict`.)
    #
    # NOT reachable from the timer, whose ExecStart passes no flags — with both
    # legs on, an unreachable remote still leaves the local host CHECKED. This
    # is consistency between the two "looked at nothing" paths, not a live bug.
    echo "drift-check: NO HOST WAS SUCCESSFULLY CHECKED — this is not a clean bill of health."
    echo "  refusing to exit 0 for a run that produced no verdict about any host."
    [ -n "$UNCHECKED" ] && echo "drift-check: NOT checked: $UNCHECKED"
    exit 2
  fi
else
  echo "drift-check: DRIFT (rc=$rc) — see per-host lines above."
  echo "  checked: ${CHECKED:-none}"
  echo "  rc3=no-repo  rc4=fetch/origin-main-unavailable  rc6=host-unidentified"
  echo "  rc8=DIVERGED/AHEAD:un-pushed-commits(ship.sh will skip this host forever)"
  echo "  rc10=behind(needs a ship)  rc12=not-on-branch-main"
  echo "  rc13=remote unreachable for >=$DRIFT_UNREACHABLE_ESCALATE consecutive runs"
  echo "  rc14=managed symlinks resolve to nothing (needs a home-manager switch on that host)"
  echo "  rc15=host parity: settings.json key sets / enabledPlugins differ, or enabled-but-not-installed"
fi
[ -n "$UNCHECKED" ] && echo "drift-check: NOT checked: $UNCHECKED"
exit "$rc"
