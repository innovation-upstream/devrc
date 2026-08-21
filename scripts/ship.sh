#!/usr/bin/env bash
# ship — converge BOTH NixOS hosts (workbench + laptop) to origin/main + verify.
#
# Agent-callable deterministic deploy primitive. Replaces the manual,
# error-prone per-host ritual (fetch -> land on main -> home-manager switch ->
# verify) with one idempotent command, so a config change lands identically on
# both machines in a single tool call.
#
# 🔴 NO STASHING, EVER. This script must never run `git stash`, `--autostash`,
# or `reset --hard`. The stash stack is repo-GLOBAL — shared across every
# worktree of a repo — so stashing here reaches OUTSIDE the checkout being
# converged. That is not theoretical: on 2026-07-30 the old stash/pop dance ran
# against the devrc main checkout, stashed changes belonging to a DIFFERENT
# worktree (feat/dl-router), fast-forwarded, then could not pop — leaving `DU`
# conflicts, an un-switched host, and another worktree's in-flight work stranded
# in stash@{0}. See claude/RULES.md -> "git stash is repo-GLOBAL".
#
# The replacement is `git merge --ff-only`, and that is the whole point: a
# fast-forward CANNOT conflict and CANNOT autostash. It either advances the
# branch cleanly or REFUSES — and a refusal is a correct, informative signal
# about that host, not a problem to bulldoze. A dirty tree whose changes do not
# overlap the incoming commits still converges untouched; one that does overlap
# causes that host to be SKIPPED with the blocking files named. We never mutate
# the user's tree to force progress.
#
# One documented exception, which git itself imposes: GITIGNORED files are not
# protected by a fast-forward and cannot be detected as blockers, so an ignored
# file the incoming commits also touch WILL be overwritten. We cannot refuse on
# it (ignored build artifacts collide routinely and harmlessly), so it is
# reported loudly instead of clobbered silently — see warn_ignored_overwrites.
#
# Host identity: BOTH machines have hostname `nixos`, so we CANNOT tell them
# apart by hostname. Instead we detect the physical host from its local IPv4
# addresses (see detect_role) and derive the REMOTE (the other host) from that.
# This is direction-agnostic: run it from EITHER host and it converges local +
# the other one. Historically it hardcoded "local == workbench" and an SSH
# target that was actually the laptop's own address — so running it on the
# laptop mislabelled the laptop as workbench and SSH'd to itself while the real
# workbench was never converged. Detection fixes that.
#
# Scope: home-manager (user-level) — the bulk of this repo's changes.
# It does NOT run `sudo nixos-rebuild` (needs an interactive password);
# system/i3 changes are surfaced as a remaining manual step, not attempted.
# It does NOT copy any file around by hand. ~/.claude/skills/ used to be rsynced
# workbench -> laptop, from a time when skills were per-host and unmanaged. They
# are now a `home.file` entry in nix/home.nix, so the per-host `home-manager
# switch` above already deploys them — and the rsync had become actively
# destructive: `rsync -a` implies `-l`, copying each store symlink with its link
# text VERBATIM, so it replaced the laptop's links into its OWN home-manager
# closure with links into the WORKBENCH's store path. That path does not exist on
# the laptop, so all 15 ~/.claude/skills/*/SKILL.md were left DANGLING seconds
# after the laptop's own switch had created them correctly — while ship.sh
# printed "skills synced". Pinned by test_ship_never_rsyncs_a_home_manager_
# managed_path in scripts/tests/test_ship_converge.py: nothing home-manager
# manages may also be hand-copied here, in either direction.
#
# Verifier (cheap + automatic), in two independent halves:
#
#   GIT + DEPLOY — each host ends ON the `main` BRANCH at HEAD == origin/main
#   AND `home-manager switch` exits 0. It is not enough for HEAD to merely equal
#   main's commit — a feature branch whose tip is an ancestor of origin/main
#   could be fast-forwarded to that commit and pass a commit-only check while
#   leaving the host stranded on the feature branch with a stale local `main`.
#   So we explicitly `git checkout main` and land there. Diverged local `main`
#   (un-pushed commits) is reported and that host's switch is skipped — never
#   auto-rebased.
#
#   CONSUMER — every path home-manager's own manifest says it deployed on that
#   host must actually RESOLVE there (verify_managed_artifacts, rc 12). The half
#   above is a claim about the DEPLOY; this one is the only claim about what the
#   host ends up holding, and the two are independent: the laptop passed the
#   first for months while its entire ~/.claude/skills/ dangled. Reported per
#   host with the count of what was EXAMINED, never a bare "0 dangling".
#
#   CURRENCY — and every path that resolves must serve the content the REPO is
#   at (verify_managed_currency, rc 13). The resolution half reads the manifest
#   out of the host's own active generation, so its reference point moves with
#   the host: a machine on a three-week-old generation has a three-week-old
#   manifest whose links all resolve, and it passes green. Measured 2026-08-19 —
#   the workbench served the pre-#611 ~/.claude/RULES.md while ship printed
#   "488 checked, 0 dangling, 0 absent" and "✅ VERIFIED … + switched". Three
#   questions, three exit codes, none of them a substitute for another.
#
# 🔴 SELF-SUPERSESSION — this script's own source is one of the things it ships.
# The first thing a run does is fast-forward the working tree, and that tree
# CONTAINS scripts/ship.sh. Two independent failures follow, both MEASURED:
#
#   * THE VERDICT IS COMPUTED BY THE COPY THAT WAS JUST REPLACED. The CONVERGE
#     payload is expanded into a variable near the top of this file, long before
#     the fast-forward, and it is that STRING which runs locally and is sent over
#     ssh. 2026-08-19: the run that DELIVERED #620 (4548e6b -> c7eb5c3, the
#     commit that added verify_managed_currency) printed no currency line at all
#     and exited 0; an immediate second run printed "347 repo-sourced examined,
#     0 stale". The operator was handed a green produced by the code they had
#     just replaced — the one shape of green this file exists to refuse.
#
#   * BASH RE-READS A SCRIPT FROM A BYTE OFFSET after every external command, so
#     a writer that overwrites this file IN PLACE while it runs truncates or
#     splices it. Measured 2026-08-20: a script that `cp`s over itself loses
#     everything past that point and exits 0 — at 45 of 45 swept offsets, and at
#     ship.sh's own byte geometry. Replaced by a same-length file it resumes
#     INSIDE the new bytes and runs a splice of the two versions (a padding line
#     executed as the bare command `yyyyy`), also exit 0. Wrapping the body in a
#     brace group made the identical experiment run the ORIGINAL to completion.
#
#     🔴 BUT NOT VIA `git`, AND THE OBVIOUS INSTRUMENT SAYS OTHERWISE. Comparing
#     st_ino across a fast-forward reports the SAME inode and reads as "git
#     overwrites in place" — it is wrong, because a freed inode number is
#     immediately reused. Holding an OPEN FD across `git merge --ff-only` shows
#     what actually happens: the fd still yields the OLD bytes with st_nlink=0,
#     i.e. git UNLINKS AND RECREATES. A running bash keeps its own unlinked
#     inode, so a fast-forward cannot corrupt the run — only stale it. Three
#     end-to-end fixture runs agree (incoming copy +27 KB, −30 KB, same size:
#     all completed cleanly on the pre-fix script).
#
# Both halves are closed here — the first being a LATENT hazard closed cheaply,
# not a reproducing one:
#   1. the whole executable body is ONE BRACE GROUP ending in `exit`, so bash
#      must parse this file to EOF before running any of it and can never re-read
#      it. It defends against an IN-PLACE writer (`cp` without
#      --remove-destination, `install`, `>>`, an editor that truncates), NOT
#      against git — see the measurement above; claiming otherwise would be a
#      comment the code contradicts. 🔴 The group opens immediately below and
#      closes on the last line — do not add anything after the closing brace, and
#      do not remove the trailing `exit`.
#   2. this file (and lib/host-role.sh) is fingerprinted before the local leg and
#      again after it. If it changed, the run RE-EXECS the new copy with
#      $SHIP_SELF_GEN incremented, BEFORE the remote leg — so the remote host
#      gets the new CONVERGE payload too. That counter is the loop guard: past
#      SHIP_SELF_MAX_GEN the run REFUSES (rc 20) rather than exec'ing again.
#      Cost, stated rather than hidden: on a run that ships a change to ship.sh,
#      `home-manager switch` runs TWICE on the local host. That is the price of
#      never printing a verdict the running code did not compute.
#
# 🔴 CROSS-HOST AGREEMENT — "each host reached origin/main" is NOT "the two hosts
# hold the same commit". Each leg fetches independently, so origin/main is a
# MOVING target across a run. MEASURED 2026-08-19, in the very same run as above:
#
#     [nixos] fast-forwarded main 4548e6b -> c7eb5c3      (workbench)
#     [nixos] fast-forwarded main 4548e6b -> e7ceb1f      (laptop)
#     ship: converged + verified at origin/main (local=workbench remote=laptop)
#
# #619 merged between the two fetches. Both hosts passed every per-host check —
# each really was at origin/main as IT saw it — and the fleet was nonetheless in
# two different states. rc 11 structurally cannot see this: it compares HEAD
# against the `target` THAT LEG captured, so a target that moved afterwards is
# invisible to it, and no other check compares the hosts to each other at all.
# So each converged host now EMITS the sha it landed on (`ship-landed-sha`), and
# the final verdict is a claim about those two shas being EQUAL — printed with
# the sha and with the number of hosts compared beside it. Disagreement, or
# fewer than two shas from a two-host run, is rc 19 and gets its own code
# because the operator action differs from every per-host code: re-run ship.
#
# 🔴 What that does NOT claim: that the agreed sha is the NEWEST origin/main.
# Both hosts can agree on a commit origin has since moved past — nothing here
# re-fetches after the fact, and a run cannot outrun a remote that keeps moving.
# It answers "are the two machines in ONE state", which is the question the old
# verdict line got wrong; "is that state current" is drift-check's rc 10.
#
# Skips are per-host and non-fatal to the run: if one host cannot fast-forward,
# it is reported with the blocking files named and the OTHER host is still
# converged.
#
# Exit codes:
#   2  usage error: an unknown argument, or a run asked to check NO host at all
#      (`--no-local --no-remote`). Raised before any host is touched, so the rc
#      legend printed on failure is never reached on this path — it is in both
#      ledgers anyway, because a ledger with a known hole is not a ledger.
#   3  repo missing on that host
#   4  git fetch failed, or origin/main is missing / HEAD unborn
#   5  SKIPPED — an unresolved merge/cherry-pick is in progress (conflicted
#      tree). NEVER switched: home-manager builds from the WORKING TREE, so
#      conflict markers in a managed file would be deployed to both hosts.
#   6  local host could not be identified (see detect_role)
#   7  SKIPPED — cannot fast-forward: local modifications / untracked files
#      overlap the incoming commits and would be overwritten. (This code
#      previously meant "stash-pop conflict", which can no longer happen now
#      that nothing is ever stashed; it is repurposed, not retired.)
#   8  SKIPPED — local main has diverged (un-pushed commits); needs a rebase
#   9  home-manager switch failed
#   11 post-switch verification failed (git state: wrong branch or wrong commit)
#   12 post-switch CONSUMER check failed — paths home-manager deployed on that
#      host do not resolve there (dangling/absent), or the manifest listing them
#      could not be read, in which case NOTHING was examined and the run proves
#      nothing about that host. Both spellings are RED on purpose.
#   13 post-switch CURRENCY check failed — the managed paths RESOLVE (12 passed)
#      but their content is an OLDER version of the repo source: this host is
#      serving a stale home-manager generation. Deliberately distinct from 12,
#      because the operator action is different — 12 is a repair, 13 is a
#      re-switch. Also RED when nothing comparable was examined.
#   19 the two hosts converged to DIFFERENT commits, or their agreement could
#      not be checked at all. Every per-host code above is a claim about ONE
#      machine; this is the only claim about the two of them TOGETHER, and both
#      can be green while the fleet sits in two states (see CROSS-HOST AGREEMENT
#      above). Also RED when a two-host run got a landed sha from fewer than two
#      hosts: an agreement that was not compared is not an agreement.
#   20 THIS SCRIPT was replaced by its own run and the new copy could not be run:
#      the re-exec budget was exhausted (ship.sh kept changing under the run),
#      the new copy is unreadable, or the self-check could not be measured at
#      all. Three spellings, one code, because they are one operator action —
#      re-run ship by hand — and because a run that cannot tell whether its own
#      source changed must not print a verdict either way.
#
# Usage:
#   scripts/ship.sh              # converge local host + the other (remote) host
#   scripts/ship.sh --no-remote  # local host only  (alias: --no-laptop)
#   scripts/ship.sh --no-local   # remote host only
#   scripts/ship.sh --no-switch  # land on main + verify git state, SKIP home-manager (test/dry-run)
#   scripts/ship.sh --detect-role # print detected local role (workbench|laptop|unknown) and exit
#
# Env overrides:
#   SHIP_ROLE     force the local role (workbench|laptop) when detection fails/differs
#   REMOTE_SSH    ssh target for the OTHER host (default derived from role)
#   LAPTOP_SSH    back-compat: ssh target used ONLY when the remote host is the laptop
#   SHIP_REPO     repo path the CONVERGE routine operates on (default $HOME/workspace/devrc)
#   SHIP_NO_SWITCH=1  same as --no-switch: run full git-landing logic, skip home-manager switch
#   SHIP_SELF_GEN     re-exec generation counter — set BY this script when it
#                     re-execs a copy of itself that its own run installed. Never
#                     set it by hand: a value >= SHIP_SELF_MAX_GEN tells the run
#                     it has already spent its budget, so a superseded script
#                     will refuse (rc 20) instead of picking up the new logic.
#
# 🔴 EVERYTHING BELOW IS ONE BRACE GROUP, closed on the final line. See
# SELF-SUPERSESSION above: this file rewrites itself mid-run, and bash re-reads a
# script from a byte offset, so an unwrapped body gets spliced or truncated.
{
set -uo pipefail

# --- Canonical per-host identity (both hosts share hostname `nixos`) -----------
# 🔴 detect_role / local_ipv4s / the IP + SSH constants are SOURCED from
# scripts/lib/host-role.sh, not defined here. scripts/drift-check.sh (the passive
# drift deadman) needs the IDENTICAL predicate, and a second copy of it would
# drift and be wrong — which is precisely how host identity used to be broken
# (see the "Host identity" paragraph in the header). One rule, one place.
# The source path is SYMLINK-RESOLVED first. Unresolved, ${BASH_SOURCE[0]} is
# whatever path invoked us, so running ship.sh through a symlink (~/bin/ship, a
# PATH shim) would look for lib/ next to the SYMLINK and exit 6 — on the one tool
# whose whole job is recovering a host that is already broken.
_ship_self="${BASH_SOURCE[0]}"
_ship_resolved="$(readlink -f "$_ship_self" 2>/dev/null || true)"
[ -n "$_ship_resolved" ] && _ship_self="$_ship_resolved"
_ship_lib="$(cd "$(dirname "$_ship_self")" 2>/dev/null && pwd)/lib/host-role.sh"
if [ -r "$_ship_lib" ]; then
  # shellcheck source=lib/host-role.sh
  . "$_ship_lib"
else
  # 🔴 DEGRADED RECOVERY MODE. ship.sh is the tool you reach for when a host is
  # already broken, so a missing lib must not be an unconditional dead end: it
  # used to carry inline constants, which made `SHIP_ROLE=workbench ship.sh`
  # work regardless. That escape hatch is restored — but ONLY as an escape
  # hatch, and it deliberately duplicates NOTHING that can drift:
  #   * detect_role is NOT redefined here. It is the predicate that has already
  #     been wrong once, `test_host_identity_has_exactly_one_definition` pins it
  #     to exactly one file, and a second copy is the failure mode itself. In
  #     this mode there is no detection at all — SHIP_ROLE is mandatory.
  #   * the SSH constants are NOT copied either (ship.sh once shipped an SSH
  #     target that pointed at the host itself). REMOTE_SSH must be given
  #     explicitly if the remote leg is wanted.
  if [ -z "${SHIP_ROLE:-}" ]; then
    echo "ship: cannot read $_ship_lib — host identity cannot be resolved." >&2
    echo "  recovery: re-run with an explicit role, e.g." >&2
    echo "    SHIP_ROLE=workbench $0 --no-remote" >&2
    echo "    SHIP_ROLE=laptop REMOTE_SSH=zach@<workbench-ip> $0" >&2
    exit 6
  fi
  echo "ship: WARNING — $_ship_lib is missing; running in degraded recovery mode." >&2
  echo "  role is taken from SHIP_ROLE='$SHIP_ROLE'; no host detection is performed." >&2
  local_ipv4s()      { :; }
  resolve_local_role() { echo "${SHIP_ROLE:-unknown}"; }
  remote_role_of()   { case "${1:-}" in workbench) echo laptop ;; laptop) echo workbench ;; *) echo "" ;; esac; }
  remote_ssh_of()    { echo "${REMOTE_SSH:-}"; }
  WORKBENCH_IP_PRIMARY="<lib unavailable>"
  LAPTOP_IP_PRIMARY="<lib unavailable>"
fi

# Hidden mode: print the role detected from an injected IP list (for tests) or,
# with no list, from this machine's own addresses. Prints role, exits 0.
if [ "${1:-}" = "--detect-role" ]; then
  # Detection lives in the lib and is not reimplemented in degraded mode, so this
  # probe is simply unavailable there — said out loud rather than crashing on an
  # undefined function.
  if ! command -v detect_role >/dev/null 2>&1; then
    echo "ship: --detect-role needs $_ship_lib, which is missing (degraded mode)." >&2
    exit 6
  fi
  # With an explicit (even empty) IP-list arg, detect from it (testable);
  # with no second arg, detect from THIS machine's own addresses.
  if [ "$#" -ge 2 ]; then detect_role "$2"; else detect_role "$(local_ipv4s | tr '\n' ' ')"; fi
  exit 0
fi

SHIP_REPO="${SHIP_REPO:-$HOME/workspace/devrc}"
SHIP_NO_SWITCH="${SHIP_NO_SWITCH:-0}"
DO_LOCAL=1
DO_REMOTE=1
for a in "$@"; do
  case "$a" in
    --no-remote|--no-laptop) DO_REMOTE=0 ;;   # skip the OTHER (remote) host
    --no-local)  DO_LOCAL=0 ;;                # skip THIS (local) host
    --no-switch) SHIP_NO_SWITCH=1 ;;
    --detect-role) : ;;                        # handled above
    # Print the contiguous comment block after the shebang. Range-proof: no
    # hardcoded line numbers to drift as the header grows.
    -h|--help)   awk 'NR>1 { if (/^#/) print; else exit }' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

# 🔴 A run that was asked to check NO host is a usage error, not a success.
# rc 0 from a run that observed nothing is the vacuous green this whole file is
# built to refuse, and drift-check.sh already spells the same refusal as its own
# rc 2 ("a RUN THAT CHECKED NO HOST AT ALL"). Caught here rather than at the
# verdict so nothing downstream has to special-case an empty scope.
if [ "$DO_LOCAL" = 0 ] && [ "$DO_REMOTE" = 0 ]; then
  echo "ship: --no-local and --no-remote together leave NO host to converge." >&2
  echo "  0 hosts in scope cannot produce a verdict about either machine." >&2
  exit 2
fi

# --- Resolve local role (detection, override-able) + derive the remote target --
SHIP_ROLE="$(resolve_local_role)"
if [ "$SHIP_ROLE" != workbench ] && [ "$SHIP_ROLE" != laptop ]; then
  echo "ship: could not identify this host (role='$SHIP_ROLE')." >&2
  echo "  local IPv4s: $(local_ipv4s | tr '\n' ' ')" >&2
  echo "  expected a workbench ($WORKBENCH_IP_PRIMARY) or laptop ($LAPTOP_IP_PRIMARY) address." >&2
  echo "  override with SHIP_ROLE=workbench|laptop to force." >&2
  exit 6
fi

# Derived in lib/host-role.sh: remote_role_of flips the role, remote_ssh_of
# applies $REMOTE_SSH unconditionally and the back-compat $LAPTOP_SSH ONLY when
# the remote host really is the laptop (from the laptop it would point at itself).
REMOTE_ROLE="$(remote_role_of "$SHIP_ROLE")"
REMOTE_SSH="$(remote_ssh_of "$SHIP_ROLE")"

# In degraded recovery mode the SSH defaults are deliberately absent (see above),
# so the remote leg needs an explicit target or must be skipped. Say which.
if [ "$DO_REMOTE" = 1 ] && [ -z "$REMOTE_SSH" ]; then
  echo "ship: no ssh target for the remote host ($REMOTE_ROLE)." >&2
  echo "  pass REMOTE_SSH=user@host, or run with --no-remote to converge this host only." >&2
  exit 6
fi

# --- Self-supersession fingerprint --------------------------------------------
# See SELF-SUPERSESSION in the header. Watched: this script, and the lib it
# sources. Both live inside the repo a local converge fast-forwards, so both can
# be replaced by the very run that is reading them.
#
# 🔴 The budget is a CONSTANT, deliberately not env-overridable: it exists to
# bound a loop, and a loop guard an operator can widen from the environment is a
# loop guard the next incident turns off. One re-exec is enough to pick up the
# copy this run just installed; a SECOND change means the tree is still moving
# under us and no copy of ship.sh can vouch for the result.
SHIP_SELF_MAX_GEN=1
SHIP_SELF_GEN="${SHIP_SELF_GEN:-0}"
case "$SHIP_SELF_GEN" in ''|*[!0-9]*) SHIP_SELF_GEN=0 ;; esac
SHIP_SELF_WATCH=("$_ship_self")
[ -r "$_ship_lib" ] && SHIP_SELF_WATCH+=("$_ship_lib")

# ship_self_fingerprint — one "<path> <crc> <bytes>" line per watched file, or
# "<path> UNMEASURED" when it cannot be digested.
#
# `cksum` reads the file WHOLE and is POSIX (a BusyBox applet too, though this
# only ever runs on the local host). It is used in preference to `$(cat "$f")`
# because command substitution STRIPS trailing newlines — an edit that only adds
# or removes them would compare equal, i.e. a superseded script reported as
# unchanged. The size field is carried alongside the CRC for the same reason:
# two claims are harder to collide than one.
#
# 🔴 UNMEASURED is a distinct outcome, not a quiet skip. "unchanged" and "never
# looked" must never print the same, so the caller counts it and refuses (rc 20)
# rather than inheriting a comparison it did not make.
ship_self_fingerprint() {
  local f sum
  for f in "${SHIP_SELF_WATCH[@]}"; do
    if sum=$(cksum < "$f" 2>/dev/null) && [ -n "$sum" ]; then
      printf '%s %s\n' "$f" "$sum"
    else
      printf '%s UNMEASURED\n' "$f"
    fi
  done
}
SHIP_SELF_BEFORE="$(ship_self_fingerprint)"

# ship_self_check "$@" — re-take the fingerprint and act on the difference.
# 🔴 Pass the SCRIPT's arguments: inside a function "$@" is the FUNCTION's, and
# the re-exec must carry --no-remote/--no-switch/... through unchanged.
# Either returns (nothing was superseded), execs the new copy, or exits 20.
ship_self_check() {
  local i n f b a
  local unmeasured=0 changed=0 detail="" names=""
  local -a after_lines=() before_lines=()
  n=${#SHIP_SELF_WATCH[@]}
  mapfile -t after_lines < <(ship_self_fingerprint)
  mapfile -t before_lines <<< "$SHIP_SELF_BEFORE"
  for ((i = 0; i < n; i++)); do
    f="${SHIP_SELF_WATCH[$i]}"
    b="${before_lines[i]:-}"
    a="${after_lines[i]:-}"
    names="$names${names:+, }$f"
    if [ "${b##* }" = UNMEASURED ] || [ "${a##* }" = UNMEASURED ]; then
      unmeasured=$((unmeasured + 1))
      continue
    fi
    if [ "$b" != "$a" ]; then
      changed=$((changed + 1))
      detail="$detail    $f: ${b#"$f "} -> ${a#"$f "}
"
    fi
  done

  if [ "$unmeasured" != 0 ]; then
    echo "ship: ❌ SELF-CHECK NOT MEASURED — $unmeasured of $n watched files could not be digested." >&2
    echo "  watched: $names" >&2
    echo "  cksum(1) is what answers 'did my own source change under me'. With no answer this run" >&2
    echo "  cannot tell a verdict IT computed from one the superseded copy computed, and a" >&2
    echo "  comparison that was never made must not read as 'unchanged'." >&2
    echo "  re-run ship by hand." >&2
    exit 20
  fi

  # 🔴 The examined count rides on the success line too. "0 superseded" out of 0
  # files compared is the same reassuring zero as "0 dangling" out of 0 checked.
  if [ "$changed" = 0 ]; then
    echo "ship: self-check — $n files compared ($names), 0 superseded"
    return 0
  fi

  echo "ship: 🔴 SUPERSEDED — this run's own fast-forward replaced the script running it:"
  printf '%s' "$detail"
  echo "  everything printed above was computed by the OLD copy: the CONVERGE payload is expanded"
  echo "  into a variable before the fast-forward, so neither this host nor the remote one has yet"
  echo "  run the logic this run just delivered."
  if [ "$SHIP_SELF_GEN" -ge "$SHIP_SELF_MAX_GEN" ]; then
    echo "ship: ❌ SUPERSEDED AGAIN at generation $SHIP_SELF_GEN (budget $SHIP_SELF_MAX_GEN) — refusing to re-exec." >&2
    echo "  ship.sh keeps changing under this run, so no copy of it can vouch for the result." >&2
    echo "  re-run ship by hand once the tree has stopped moving." >&2
    exit 20
  fi
  if [ ! -r "$_ship_self" ]; then
    echo "ship: ❌ SUPERSEDED but the new copy at $_ship_self is not readable — refusing to guess." >&2
    exit 20
  fi
  SHIP_SELF_GEN=$((SHIP_SELF_GEN + 1))
  export SHIP_SELF_GEN
  echo "ship: re-executing the NEW copy (generation $SHIP_SELF_GEN of $SHIP_SELF_MAX_GEN)."
  echo "  the remote leg has NOT run yet, so it will be sent the new CONVERGE payload."
  echo
  exec bash "$_ship_self" "$@"
}

# Self-contained converge routine, run identically on each host (local via
# bash -c, remote via ssh). Single source of truth for the sequence.
CONVERGE='
set -uo pipefail
repo="${SHIP_REPO:-$HOME/workspace/devrc}"
no_switch="${SHIP_NO_SWITCH:-0}"
host=$(hostname 2>/dev/null || echo local)
[ -n "$host" ] || host=local
cd "$repo" || { echo "[$host] no repo at $repo"; exit 3; }
git fetch origin -q || { echo "[$host] git fetch failed"; exit 4; }
# Validate the target rather than letting a raw `fatal:` leak and get
# misclassified downstream (a missing origin/main would otherwise fail the
# ancestry test and be reported as "diverged", which it is not).
target=$(git rev-parse -q --verify origin/main) || {
  echo "[$host] no origin/main after a successful fetch — remote/branch misconfigured."
  echo "[$host]   check: git -C $repo remote -v ; git -C $repo branch -r"
  exit 4
}

# blocking_files — paths that are locally modified/staged/untracked AND are also
# touched by the incoming commits. That intersection is exactly the set a
# checkout or fast-forward would have to overwrite. Computed as a deterministic
# set operation; we never parse git error prose to decide anything.
#   --no-renames is REQUIRED: with rename detection on (the default) an upstream
#   rename f -> g reports only the destination g, so a local edit to f would
#   intersect with nothing and the actionable message would come out EMPTY.
#   Disabling it reports both sides of the rename, which is what actually gets
#   touched on disk.
blocking_files() {
  inc=$(mktemp); loc=$(mktemp)
  git diff --name-only --no-renames HEAD origin/main 2>/dev/null | sort -u > "$inc"
  { git diff --name-only
    git diff --cached --name-only
    git ls-files --others --exclude-standard
  } 2>/dev/null | sort -u > "$loc"
  comm -12 "$inc" "$loc"
  rm -f "$inc" "$loc"
}

# skip_cannot_ff <reason> <git-stderr> — report an actionable SKIP and exit 7.
# Deliberately does NOT mutate the tree: no stash, no checkout --force, no
# reset. A host we cannot safely converge is left exactly as we found it.
skip_cannot_ff() {
  echo "[$host] SKIPPED — cannot fast-forward to origin/main: $1"
  blockers=$(blocking_files)
  if [ -n "$blockers" ]; then
    echo "[$host]   blocking files (locally changed AND changed upstream):"
    echo "$blockers" | sed "s|^|[$host]     - |"
  fi
  [ -n "${2:-}" ] && echo "$2" | sed "s|^|[$host]   git: |"
  echo "[$host]   ship never stashes: the stash is repo-GLOBAL and would reach into other worktrees."
  echo "[$host]   resolve on that host, then re-run ship:"
  echo "[$host]     keep your work:   git -C $repo commit <file>...   (or move it to a worktree)"
  echo "[$host]     take upstream:    git -C $repo checkout origin/main -- <file>..."
  exit 7
}

# warn_ignored_overwrites — gitignored files are INVISIBLE to both
# blocking_files (--exclude-standard) and to git itself: a fast-forward will
# silently overwrite an ignored file that the incoming commits touch, with no
# refusal. We cannot skip on this (an ignored build artifact colliding is
# normal and harmless), so we warn loudly instead of clobbering in silence.
warn_ignored_overwrites() {
  inc=$(mktemp); ign=$(mktemp)
  git diff --name-only --no-renames HEAD origin/main 2>/dev/null | sort -u > "$inc"
  git ls-files --others --ignored --exclude-standard 2>/dev/null | sort -u > "$ign"
  hits=$(comm -12 "$inc" "$ign")
  rm -f "$inc" "$ign"
  if [ -n "$hits" ]; then
    echo "[$host] WARNING — gitignored files that the incoming commits also change."
    echo "[$host]   git does NOT protect ignored files; these were overwritten:"
    echo "$hits" | sed "s|^|[$host]     - |"
  fi
}

# --- The post-switch CONSUMER check -------------------------------------------
# ma_manifest — path of the home-files tree of THIS host'"'"'s current
# home-manager generation, i.e. the authoritative list of every path
# home-manager deployed here. Two locations are probed because home-manager has
# used both; the first that exists wins. The trailing slash on the -d test is
# load-bearing: home-files is itself a SYMLINK into the store.
ma_manifest() {
  ma_state="${XDG_STATE_HOME:-$HOME/.local/state}"
  for ma_c in "$ma_state/home-manager/gcroots/current-home/home-files" \
              "$ma_state/nix/profiles/home-manager/home-files"; do
    if [ -d "$ma_c/" ]; then echo "$ma_c"; return 0; fi
  done
  return 1
}

# verify_managed_artifacts — assert that everything home-manager says it
# deployed on this host actually RESOLVES here. Returns 0/1; prints its own line.
#
# WHY: a deploy reporting success is a claim about the DEPLOY, not about the
# CONSUMER. Since some unknown date the laptop has had a 100% broken
# ~/.claude/skills/ — every managed symlink pointing into the WORKBENCH'"'"'s
# /nix/store, which does not exist there (still true when this was written:
# 46 dangling of 289 managed paths, measured 2026-08-11) — and THREE layers
# reported healthy the whole time: ship.sh printed "skills synced" while causing
# it (the rsync above, now removed), drift-check.sh only ever compares git refs,
# and the rsync'"'"'s own comment asserted the opposite of the truth. Prose in
# claude/RULES.md already carried the lesson, from a different incident, and did
# not prevent this one. Removing the rsync stops the cause; this stops the next
# instance of the CLASS from going unnoticed for months.
#
# STRUCTURAL, NOT SPELLED. The path set comes from home-manager'"'"'s own manifest,
# never from a hardcoded "skills/" — so the same check catches the identical
# break in commands/, hooks/, the opencode mirrors, or any home.file target
# added tomorrow, and it needs no exclusion list for unmanaged content nested
# inside a managed directory (~/.claude/skills/clickup/ is a standalone git
# checkout whose node_modules is full of pnpm symlinks; the manifest simply
# never mentions it).
#
# WHAT IT CANNOT SEE, so nobody reads more into a green than is there. This
# answers ONE question — "does every managed path resolve" — and the blind spots
# are NOT limited to the first one, which is how this paragraph used to read:
#   * a managed path REPLACED by a real file of the same name resolves fine and
#     is not reported. (Live on the workbench 2026-08-20: 19 of the 20 entries
#     under ~/.config/opencode/commands/ were regular files, not store links.)
#   * 🔴 STALENESS is structurally invisible. Every path here — the manifest AND
#     the links it lists — comes from THIS host'"'"'s CURRENTLY-ACTIVE generation, so
#     the reference point MOVES WITH THE HOST. A machine sitting on a three-week
#     -old generation has a three-week-old manifest whose links all resolve, and
#     passes with a perfect green. Measured 2026-08-19: the workbench served the
#     pre-#611 ~/.claude/RULES.md while this printed "488 checked, 0 dangling, 0
#     absent". It asks "is this host consistent WITH ITSELF", never "is this host
#     running what origin/main says it should" — that is verify_managed_currency
#     (rc 13) below, which is why both run and neither replaces the other.
#
# 🔴 EVERY exit from here that did not examine files is RED, never a quiet pass.
# "0 dangling" out of 0 examined is precisely the reassuring zero that let this
# run for months, so the success line always carries the EXAMINED count too.
# 🔴 GNU-only find flags are banned here. MEASURED 2026-08-11: over `ssh <laptop>`
# `find` resolves to a BusyBox applet, which has no -printf — the first draft
# used it and reported "checked=1 dangling=0" for a host with 46 dangling links.
# This routine is what runs over ssh on the remote host, so the remote leg is
# exactly where a GNU-ism silently zeroes the count.
verify_managed_artifacts() {
  ma_hf=$(ma_manifest)
  if [ -z "$ma_hf" ]; then
    ma_state="${XDG_STATE_HOME:-$HOME/.local/state}"
    echo "[$host] ❌ MANAGED ARTIFACTS NOT CHECKED — cannot locate the home-manager file manifest."
    echo "[$host]   looked for home-files under:"
    echo "[$host]     - $ma_state/home-manager/gcroots/current-home"
    echo "[$host]     - $ma_state/nix/profiles/home-manager"
    echo "[$host]   0 artifacts were examined, so this run proves NOTHING about the consumer."
    return 1
  fi

  ma_list=$(mktemp)
  find "$ma_hf/" -mindepth 1 ! -type d > "$ma_list" 2>/dev/null
  ma_checked=0; ma_dangling=0; ma_absent=0; ma_unparsed=0; ma_report=""
  while IFS= read -r ma_p; do
    ma_rel="${ma_p#"$ma_hf/"}"
    [ -n "$ma_rel" ] || continue
    # A leading slash means the prefix strip failed, i.e. find changed its
    # output shape. Counting those as OK would turn the whole walk green.
    case "$ma_rel" in
      /*) ma_unparsed=$((ma_unparsed + 1)); continue ;;
    esac
    ma_checked=$((ma_checked + 1))
    ma_t="$HOME/$ma_rel"
    [ -e "$ma_t" ] && continue
    if [ -L "$ma_t" ]; then
      ma_dangling=$((ma_dangling + 1))
      ma_report="$ma_report$ma_rel -> $(readlink "$ma_t") (dangling)
"
    else
      ma_absent=$((ma_absent + 1))
      ma_report="$ma_report$ma_rel (absent)
"
    fi
  done < "$ma_list"
  rm -f "$ma_list"

  if [ "$ma_unparsed" != 0 ]; then
    echo "[$host] ❌ MANAGED ARTIFACTS NOT CHECKED — could not derive home-relative paths"
    echo "[$host]   from the manifest ($ma_unparsed entries under $ma_hf did not carry that prefix)."
    echo "[$host]   find(1) changed its output shape; the walk is unreliable, not clean."
    return 1
  fi
  if [ "$ma_checked" = 0 ]; then
    echo "[$host] ❌ MANAGED ARTIFACTS NOT CHECKED — the manifest at $ma_hf listed NO files."
    echo "[$host]   a zero here is a broken probe, not a clean host: home-manager always"
    echo "[$host]   deploys at least .zshenv/.profile. Check that find(1) supports"
    echo "[$host]   -mindepth and that home-files is traversable."
    return 1
  fi

  ma_bad=$((ma_dangling + ma_absent))
  if [ "$ma_bad" = 0 ]; then
    echo "[$host] ✅ managed artifacts resolve — $ma_checked checked, 0 dangling, 0 absent"
    return 0
  fi

  # Deliberately the SAME "N checked, M dangling, K absent" shape as the success
  # line: the examined count must be legible on both, and one parser must read
  # either. A failure line that dropped it would hide the vacuous-zero case.
  echo "[$host] ❌ MANAGED ARTIFACTS BROKEN — $ma_checked checked, $ma_dangling dangling, $ma_absent absent"
  echo "[$host]   home-manager deployed these paths; on this host they do not resolve:"
  printf "%s" "$ma_report" | head -12 | sed "s|^|[$host]     - |"
  ma_more=$((ma_bad - 12))
  [ "$ma_more" -gt 0 ] && echo "[$host]     ... and $ma_more more"
  echo "[$host]   a link into ANOTHER host store path means something copied this path"
  echo "[$host]   between hosts AFTER the switch — home-manager must be the only writer."
  echo "[$host]   repair on that host:  home-manager switch --flake $repo --impure"
  return 1
}

# verify_managed_currency — assert that what this host is SERVING is what the
# repo currently SAYS, not merely that it is internally consistent. Returns 0/1;
# prints its own line. This is the second, independent half of the consumer
# check and it exists because the first one CANNOT see the following:
#
# MEASURED 2026-08-19. The workbench served the pre-#611 ~/.claude/RULES.md
# (store path k1001c6...) while the repo working tree sat at origin/main with the
# new content, and verify_managed_artifacts printed "488 checked, 0 dangling, 0
# absent" — a perfect green — because every path it consults, manifest included,
# is read out of the hosts OWN currently-active generation. An old generation is
# perfectly self-consistent. "Resolves" and "is current" are different questions
# and they get different exit codes (12 vs 13) because they are different
# operator actions: one is a repair, the other is a re-switch.
#
# HOW, without a name mapping. Comparing a deployed path to "its" repo source
# would need a manifest-path -> repo-path table, and every such table is a
# hardcoded spelling that rots. Instead the comparison is by CONTENT, using git
# as the oracle, which needs no table at all:
#   * a home.file deployed verbatim from a repo file has BYTE-IDENTICAL content,
#     so its git blob id equals that of some file in the working tree -> CURRENT;
#   * if the blob is not in the working tree but IS in this repos object store,
#     the host is serving a HISTORICAL version of a repo file -> STALE. After the
#     `git fetch` above, everything that has ever been on main is reachable here;
#   * if the blob is unknown to the repo entirely, the artifact was GENERATED, not
#     copied (the nvim/zsh/systemd/i3 files home-manager renders itself, the
#     opencode AGENTS.md and generated commands) -> NOT REPO-SOURCED, excluded,
#     because it carries no evidence either way. Measured on the workbench:
#     347 repo-sourced, 122 generated, 16 out-of-store, 3 dirs, of 488.
# Structural, exactly like the resolution check above: nothing is spelled, so the
# same routine covers skills/, hooks/, the opencode mirrors and any home.file
# target added tomorrow.
#
# 🔴 mkOutOfStoreSymlink TARGETS ARE EXCLUDED, and counted separately. Those
# resolve BACK INTO the repo working tree (the browser + dl-router skills, the
# close-the-loop ledger), so comparing them against the repo source is vacuously
# true — they can NEVER be stale. Counting them would inflate the examined number
# with checks incapable of detecting anything, which is the same lie as a bare
# "0 stale". The arbiter is where `readlink -f` terminates: inside the repo ->
# vacuous, anywhere else -> real evidence. (claude/RULES.md, "readlink is the
# arbiter".) The EXAMINED number printed is repo-sourced only, and 0 of those is
# RED, never a quiet pass.
#
# 🔴 GNU-only flags are banned here for the same measured reason as above: this
# routine runs over ssh on the laptop, where `find` is a BusyBox applet. Only
# git, find, grep, sort, cut, wc and readlink -f are used, no -printf, no stat
# format, no sha256sum (git hash-object is the digest on BOTH sides, so the two
# can never disagree about how bytes are hashed).
verify_managed_currency() {
  # 🔴 A PRECONDITION, not a verification guard, and deliberately labelled as
  # such: it is UNREACHABLE while verify_managed_artifacts runs first and exits
  # 12 on the same condition, so it is NOT tested and must not be counted as
  # coverage. It stays because an empty $mc_hf would make the walk below read
  # `find "/" -mindepth 1` and traverse the entire filesystem.
  mc_hf=$(ma_manifest)
  if [ -z "$mc_hf" ]; then
    echo "[$host] ❌ CURRENCY NOT CHECKED — cannot locate the home-manager file manifest."
    echo "[$host]   0 artifacts were examined, so this run proves NOTHING about currency."
    return 1
  fi
  mc_repo=$(readlink -f "$repo" 2>/dev/null || echo "$repo")

  mc_list=$(mktemp); mc_rels=$(mktemp); mc_paths=$(mktemp)
  mc_vacuous=0; mc_dirs=0
  find "$mc_hf/" -mindepth 1 ! -type d > "$mc_list" 2>/dev/null
  while IFS= read -r mc_p; do
    mc_rel="${mc_p#"$mc_hf/"}"
    [ -n "$mc_rel" ] || continue
    # A leading slash means find(1) changed its output shape. rc 12 OWNS that
    # diagnosis and has already exited on it, so this is a skip, not a second
    # guard — an unreachable duplicate would be untested code claiming coverage.
    # If it ever did run, dropping the entries lands on the zero-examined guard
    # below, which is red.
    case "$mc_rel" in
      /*) continue ;;
    esac
    mc_t="$HOME/$mc_rel"
    # Non-resolving paths are rc 12s job, not ours; it runs first and is fatal.
    [ -e "$mc_t" ] || continue
    if [ -d "$mc_t/" ]; then mc_dirs=$((mc_dirs + 1)); continue; fi
    mc_r=$(readlink -f "$mc_t" 2>/dev/null || echo "")
    case "$mc_r" in
      "$mc_repo"/*) mc_vacuous=$((mc_vacuous + 1)); continue ;;
    esac
    printf "%s\n" "$mc_rel" >> "$mc_rels"
    printf "%s\n" "$mc_t"   >> "$mc_paths"
  done < "$mc_list"
  rm -f "$mc_list"

  # --- digest both sides with the SAME function ------------------------------
  mc_blobs=$(mktemp)
  if [ -s "$mc_paths" ]; then
    git -C "$repo" hash-object --stdin-paths < "$mc_paths" > "$mc_blobs" 2>/dev/null
  fi
  mc_np=$(wc -l < "$mc_paths"); mc_nb=$(wc -l < "$mc_blobs")
  if [ "$mc_np" -ne "$mc_nb" ]; then
    echo "[$host] ❌ CURRENCY NOT CHECKED — git hash-object returned $mc_nb digests for $mc_np paths."
    echo "[$host]   the walk is unreliable, not clean; nothing about currency is proven."
    rm -f "$mc_rels" "$mc_paths" "$mc_blobs"
    return 1
  fi

  mc_tracked=$(mktemp); mc_srcpaths=$(mktemp); mc_srcblobs=$(mktemp)
  git -C "$repo" ls-files > "$mc_tracked" 2>/dev/null
  while IFS= read -r mc_f; do
    [ -f "$repo/$mc_f" ] && printf "%s\n" "$repo/$mc_f"
  done < "$mc_tracked" > "$mc_srcpaths"
  if [ -s "$mc_srcpaths" ]; then
    git -C "$repo" hash-object --stdin-paths < "$mc_srcpaths" 2>/dev/null | sort -u > "$mc_srcblobs"
  fi
  if [ ! -s "$mc_srcblobs" ]; then
    echo "[$host] ❌ CURRENCY NOT CHECKED — read NO source files out of $repo."
    echo "[$host]   with an empty reference set every artifact would look stale (or none would);"
    echo "[$host]   either way the comparison is wired to nothing. Check git ls-files there."
    rm -f "$mc_rels" "$mc_paths" "$mc_blobs" "$mc_tracked" "$mc_srcpaths" "$mc_srcblobs"
    return 1
  fi

  # --- classify --------------------------------------------------------------
  mc_pairs=$(mktemp); mc_uniq=$(mktemp); mc_cur=$(mktemp); mc_unk=$(mktemp)
  mc_staleblobs=$(mktemp); mc_pat=$(mktemp); mc_rows=$(mktemp)
  exec 9< "$mc_rels"
  while IFS= read -r mc_b; do
    IFS= read -r mc_one <&9 || mc_one=""
    printf "%s %s\n" "$mc_b" "$mc_one"
  done < "$mc_blobs" > "$mc_pairs"
  exec 9<&-

  sort -u "$mc_blobs" > "$mc_uniq"
  : > "$mc_cur"
  [ -s "$mc_uniq" ] && grep -Fxf "$mc_srcblobs" "$mc_uniq" > "$mc_cur"
  if [ -s "$mc_cur" ]; then
    grep -Fxvf "$mc_cur" "$mc_uniq" > "$mc_unk"
  else
    cat "$mc_uniq" > "$mc_unk"
  fi
  : > "$mc_staleblobs"
  if [ -s "$mc_unk" ]; then
    # "<sha> blob <size>" for a known object, "<sha> missing" otherwise.
    git -C "$repo" cat-file --batch-check < "$mc_unk" 2>/dev/null \
      | grep " blob " | cut -d" " -f1 > "$mc_staleblobs"
  fi

  # Anchored patterns (a blob id is pure hex, so it carries no regex metachars)
  # so a 40-hex string inside a PATH can never be mistaken for a digest column.
  mc_current=0
  while IFS= read -r mc_b; do printf "^%s \n" "$mc_b"; done < "$mc_cur" > "$mc_pat"
  [ -s "$mc_pat" ] && mc_current=$(grep -c -E -f "$mc_pat" "$mc_pairs")
  mc_stale=0
  while IFS= read -r mc_b; do printf "^%s \n" "$mc_b"; done < "$mc_staleblobs" > "$mc_pat"
  : > "$mc_rows"
  if [ -s "$mc_pat" ]; then
    grep -E -f "$mc_pat" "$mc_pairs" > "$mc_rows"
    mc_stale=$(wc -l < "$mc_rows")
  fi
  mc_sourced=$((mc_current + mc_stale))
  mc_generated=$((mc_np - mc_sourced))
  mc_gen=$(readlink -f "$mc_hf" 2>/dev/null || echo "$mc_hf")
  rm -f "$mc_rels" "$mc_paths" "$mc_blobs" "$mc_tracked" "$mc_srcpaths" \
        "$mc_srcblobs" "$mc_pairs" "$mc_uniq" "$mc_cur" "$mc_unk" \
        "$mc_staleblobs" "$mc_pat"

  # 🔴 The vacuous zero, in its currency spelling: no repo-sourced artifact means
  # nothing comparable was looked at, whatever the other counters say.
  if [ "$mc_sourced" = 0 ]; then
    echo "[$host] ❌ CURRENCY NOT CHECKED — 0 repo-sourced artifacts examined"
    echo "[$host]   ($mc_np resolved, $mc_generated not repo-sourced, $mc_vacuous out-of-store, $mc_dirs dirs)."
    echo "[$host]   Nothing deployed here has content matching ANY file in $repo, so the"
    echo "[$host]   comparison examined nothing. That is a broken probe, not a current host."
    rm -f "$mc_rows"
    return 1
  fi

  if [ "$mc_stale" = 0 ]; then
    echo "[$host] ✅ managed artifacts CURRENT — $mc_sourced repo-sourced examined, 0 stale ($mc_generated not repo-sourced, $mc_vacuous out-of-store, $mc_dirs dirs)"
    rm -f "$mc_rows"
    return 0
  fi

  # Same counter shape on the failure line as on the success line, so one parser
  # reads either and the examined count is never dropped from the bad case.
  echo "[$host] ❌ MANAGED ARTIFACTS STALE — $mc_sourced repo-sourced examined, $mc_stale stale ($mc_generated not repo-sourced, $mc_vacuous out-of-store, $mc_dirs dirs)"
  echo "[$host]   these resolve fine, but their CONTENT is an older version of a file in $repo:"
  cut -d" " -f2- "$mc_rows" | head -12 | sed "s|^|[$host]     - |"
  mc_more=$((mc_stale - 12))
  [ "$mc_more" -gt 0 ] && echo "[$host]     ... and $mc_more more"
  echo "[$host]   generation being served: $mc_gen"
  echo "[$host]   repo HEAD: $(git -C "$repo" rev-parse --short HEAD 2>/dev/null)"
  echo "[$host]   this host is running an OLD home-manager generation, or its switch did"
  echo "[$host]   not take. It is NOT a broken link — rc 12 passed. Re-switch on that host:"
  echo "[$host]     home-manager switch --flake $repo --impure"
  rm -f "$mc_rows"
  return 1
}

# 0) Refuse a mid-merge / conflicted tree OUTRIGHT, before anything else.
#    This must run even when HEAD is already AT origin/main, because that path
#    short-circuits the merge entirely and would otherwise fall straight through
#    to the switch. `home-manager switch --flake` builds from the WORKING TREE,
#    not the commit, so conflict markers in any managed non-nix file
#    (claude/RULES.md, claude/skills/**, hooks, scripts/*) would be DEPLOYED TO
#    BOTH HOSTS and then reported as VERIFIED. Resolving a merge is a human
#    decision; ship never guesses at one.
conflicted=$(git diff --name-only --diff-filter=U 2>/dev/null)
if git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1 \
   || git rev-parse -q --verify CHERRY_PICK_HEAD >/dev/null 2>&1 \
   || git rev-parse -q --verify REVERT_HEAD >/dev/null 2>&1 \
   || [ -n "$conflicted" ]; then
  echo "[$host] SKIPPED — an unresolved merge/cherry-pick is in progress (conflicted tree)."
  if [ -n "$conflicted" ]; then
    echo "[$host]   unmerged paths:"
    echo "$conflicted" | sed "s|^|[$host]     - |"
  fi
  echo "[$host]   NOT switching: home-manager builds from the WORKING TREE, so conflict"
  echo "[$host]   markers in a managed file would be deployed to both hosts."
  echo "[$host]   resolve on that host, then re-run ship:"
  echo "[$host]     git -C $repo status ; resolve ; git -C $repo commit"
  echo "[$host]     or abandon it:  git -C $repo merge --abort"
  exit 5
fi

# 1) Land ON the `main` BRANCH (not merely main'"'"'s commit).
#    NOTE: never `checkout -B main` when a local main already exists — that
#    RESETS the branch and would silently destroy un-pushed commits. We only
#    create main when it is genuinely absent.
branch=$(git symbolic-ref --quiet --short HEAD || echo "")
if [ "$branch" != "main" ]; then
  if git show-ref --verify --quiet refs/heads/main; then
    coerr=$(git checkout main -q 2>&1) || skip_cannot_ff "could not switch to the main branch" "$coerr"
  else
    coerr=$(git checkout -b main --track origin/main -q 2>&1) || skip_cannot_ff "could not create a local main branch" "$coerr"
  fi
  # Surface checkout warnings on SUCCESS too — notably the detached-HEAD
  # "you are leaving N commits behind" notice, which is the users only chance
  # to rescue un-referenced commits before they become unreachable.
  [ -n "$coerr" ] && echo "$coerr" | sed "s|^|[$host]   git: |"
fi

# 2) Fast-forward main to origin/main. Two distinct refusals, told apart
#    deterministically by an ancestry test BEFORE we touch anything:
#      not an ancestor -> diverged/un-pushed commits            -> exit 8
#      ancestor but merge refuses -> local changes in the way   -> exit 7
head=$(git rev-parse -q --verify HEAD) || {
  echo "[$host] HEAD is unborn (no commits) — cannot converge this checkout."
  exit 4
}
if [ "$head" = "$target" ]; then
  echo "[$host] main already at origin/main ($target)"
elif ! git merge-base --is-ancestor HEAD origin/main; then
  echo "[$host] SKIPPED — local main has diverged from origin/main (un-pushed commits) — never auto-rebased."
  echo "[$host]   inspect on that host: git -C $repo log --oneline origin/main..HEAD"
  exit 8
else
  # A fast-forward is possible, so only overlapping local changes can block it.
  # merge.autoStash is forced OFF: this repo sets rebase.autoStash=true globally
  # (nix/programs/git), and no autostash may EVER sneak into this path.
  warn_ignored_overwrites
  if mergeerr=$(git -c merge.autoStash=false merge --ff-only origin/main -q 2>&1); then
    echo "[$host] fast-forwarded main $head -> $target"
  else
    skip_cannot_ff "local changes would be overwritten by the incoming commits" "$mergeerr"
  fi
fi

# 3) home-manager switch (skippable for tests/dry-run).
if [ "$no_switch" = 1 ]; then
  echo "[$host] (SHIP_NO_SWITCH) skipping home-manager switch"
else
  log=$(mktemp /tmp/ship-hm.XXXXXX.log)
  if ! home-manager switch --flake "$repo" --impure >"$log" 2>&1; then
    echo "[$host] home-manager switch FAILED:"; tail -4 "$log"; exit 9
  fi
fi

# 4) Verify the GIT state: must be ON branch main AND HEAD == origin/main.
now=$(git rev-parse HEAD)
nowbranch=$(git symbolic-ref --quiet --short HEAD || echo "DETACHED")
if [ "$now" != "$target" ] || [ "$nowbranch" != "main" ]; then
  echo "[$host] ❌ VERIFY FAILED — branch=$nowbranch HEAD=$now origin/main=$target"; exit 11
fi

# 5) Verify the CONSUMER. Steps 3 and 4 together only establish that the deploy
#    RAN and that the source it built from is the right commit — both were true
#    on the laptop for the entire time its ~/.claude/skills/ was 100% dangling.
#    This is the step that looks at what the host actually has.
verify_managed_artifacts || exit 12

# 5b) Verify the CONSUMER is CURRENT. Step 5 answers "does every managed path
#     resolve" against a manifest read out of THIS HOST S OWN generation, so an
#     old generation is self-consistent and passes it. This is the only step
#     whose reference point is the REPO rather than the host.
verify_managed_currency || exit 13

# 5c) Publish the sha THIS host landed on, so the driver can compare the two
#     hosts against EACH OTHER (rc 19). Every check above is per-host and passes
#     happily while the two machines hold different commits, because each leg
#     fetched its own origin/main and each is right about itself.
#     🔴 Machine-readable on purpose: the driver parses this exact line, so its
#     shape is a contract. It is emitted only HERE, after every per-host check
#     has passed — a host that did not finish converging must contribute no sha,
#     so "fewer than two shas" stays distinguishable from "two that agree".
echo "[$host] ship-landed-sha $now"

# 6) Verdict. Name the dirty state explicitly: converging a dirty tree is the
#    NORMAL supported path, and home-manager builds from the WORKING TREE — so
#    what got deployed is origin/main PLUS that WIP, not origin/main. Saying so
#    is the difference between an honest verifier and a misleading one.
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  echo "[$host] ✅ VERIFIED — on branch main at origin/main + switched"
  echo "[$host]   NOTE: tree is DIRTY — what was built/deployed is origin/main + local WIP."
else
  echo "[$host] ✅ VERIFIED — on branch main at origin/main (clean tree) + switched"
fi
'

rc=0
LOCAL_SHA=""
REMOTE_SHA=""

# ship_landed_sha <capture-file> — the sha that leg reported landing on, or "".
# 🔴 Anchored on the WHOLE line. A 40-hex string turns up in this output in prose
# too (a "generation being served" store path, a git error, a blocking-file
# name), and reading one of those as the answer would produce a confident WRONG
# verdict instead of a missing one — which is strictly worse, because the missing
# case is itself red (rc 19).
ship_landed_sha() {
  sed -n 's/^\[[^]]*\] ship-landed-sha \([0-9a-f][0-9a-f]*\)$/\1/p' "$1" | tail -1
}

if [ "$DO_LOCAL" = 1 ]; then
  echo "=== local ($SHIP_ROLE) ==="
  # `| tee`, not capture-then-print: a home-manager switch takes minutes and an
  # operator watching a silent terminal cannot tell it from a hang. stdout only,
  # so stderr keeps its own stream untouched.
  # 🔴 The status comes from PIPESTATUS[0], never $?: a pipeline reports the LAST
  # command's status and `tee` always succeeds, so `$?` here would be a hardcoded
  # 0 over any per-host skip. Same lesson as scripts/run-tests.sh GUARD 6, from
  # the other side of the pipe.
  cap_local=$(mktemp /tmp/ship-local.XXXXXX)
  SHIP_REPO="$SHIP_REPO" SHIP_NO_SWITCH="$SHIP_NO_SWITCH" bash -c "$CONVERGE" | tee "$cap_local"
  lrc=${PIPESTATUS[0]}
  [ "$lrc" = 0 ] || rc=$lrc
  LOCAL_SHA=$(ship_landed_sha "$cap_local")
  rm -f "$cap_local"
  echo
fi

# 🔴 BEFORE the remote leg, not after: the local fast-forward above may have
# replaced this very script, and if it did, the CONVERGE payload about to be sent
# over ssh is the superseded one. Re-execing here means the remote host receives
# the logic this run delivered. See SELF-SUPERSESSION in the header.
ship_self_check "$@"
echo

if [ "$DO_REMOTE" = 1 ]; then
  echo "=== remote ($REMOTE_ROLE — $REMOTE_SSH) ==="
  # Pass the switch toggle remotely; SHIP_REPO stays host-default ($HOME/workspace/devrc).
  cap_remote=$(mktemp /tmp/ship-remote.XXXXXX)
  ssh -o ConnectTimeout=10 "$REMOTE_SSH" "SHIP_NO_SWITCH=$SHIP_NO_SWITCH; $CONVERGE" | tee "$cap_remote"
  remrc=${PIPESTATUS[0]}
  REMOTE_SHA=$(ship_landed_sha "$cap_remote")
  rm -f "$cap_remote"
  if [ "$remrc" != 0 ]; then
    # Keep the FIRST non-zero code so a local skip is not masked by a later
    # remote failure — the distinct codes are the signal callers act on.
    [ "$rc" = 0 ] && rc=$remrc
    echo "[$REMOTE_ROLE] converge exited $remrc"
  fi

  # NOTE: no post-switch file sync happens here, deliberately. ~/.claude/skills/
  # is deployed by the remote `home-manager switch` above (home.file in
  # nix/home.nix); the rsync that used to live here re-broke it every run. See
  # the header. Do not reintroduce a copy of any home-manager-managed path.
  echo
fi

# --- Do the two hosts agree with EACH OTHER on ONE commit? (rc 19) -------------
# See CROSS-HOST AGREEMENT in the header. Every code above is a per-host claim;
# this is the only cross-host one, and on 2026-08-19 every per-host claim was
# true while the two machines held different commits.
hosts_in_scope=0
[ "$DO_LOCAL" = 1 ]  && hosts_in_scope=$((hosts_in_scope + 1))
[ "$DO_REMOTE" = 1 ] && hosts_in_scope=$((hosts_in_scope + 1))
hosts_reporting=0
[ -n "$LOCAL_SHA" ]  && hosts_reporting=$((hosts_reporting + 1))
[ -n "$REMOTE_SHA" ] && hosts_reporting=$((hosts_reporting + 1))
agreed_sha=""
scope_label="local=$SHIP_ROLE remote=$REMOTE_ROLE"
[ "$DO_REMOTE" = 0 ] && scope_label="local=$SHIP_ROLE"
[ "$DO_LOCAL" = 0 ]  && scope_label="remote=$REMOTE_ROLE"

if [ "$hosts_in_scope" -lt 2 ]; then
  # Scoped by the operator (--no-remote / --no-local). Not red — but the claim
  # the verdict is allowed to make shrinks with the scope, and it says so rather
  # than letting a one-host run read like a two-host one.
  echo "ship: cross-host agreement NOT COMPARED — $hosts_in_scope host in scope, $hosts_reporting reported a landed sha."
elif [ "$hosts_reporting" -lt 2 ]; then
  echo "ship: ❌ CROSS-HOST AGREEMENT NOT COMPARED — $hosts_reporting of 2 hosts reported a landed sha."
  echo "  local($SHIP_ROLE)=${LOCAL_SHA:-<none>}  remote($REMOTE_ROLE)=${REMOTE_SHA:-<none>}"
  echo "  a host that reported none did not finish converging, so 'both at origin/main' is"
  echo "  UNPROVEN, not true. A comparison over fewer than two shas is the reassuring zero"
  echo "  this line exists to refuse."
  [ "$rc" = 0 ] && rc=19
elif [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  echo "ship: ❌ HOSTS DISAGREE — 2 hosts compared, 2 distinct commits:"
  echo "  local  ($SHIP_ROLE) landed on $LOCAL_SHA"
  echo "  remote ($REMOTE_ROLE) landed on $REMOTE_SHA"
  echo "  origin/main MOVED between the two fetches (a PR merged mid-run). Each host is"
  echo "  internally consistent and each really is at origin/main AS IT SAW IT — which is why"
  echo "  every per-host check above passed — but the fleet is in TWO states, not one."
  echo "  re-run ship: the second pass lands both on the newer commit."
  [ "$rc" = 0 ] && rc=19
else
  agreed_sha="$LOCAL_SHA"
fi

if [ "$rc" = 0 ]; then
  # 🔴 The verdict names the SHA and the number of hosts compared. "converged +
  # verified at origin/main" was true of each host separately on 2026-08-19 and
  # false of the fleet; a verdict that cannot be wrong that way has to carry the
  # thing the hosts agreed ON, not the moving ref they each resolved.
  if [ -n "$agreed_sha" ]; then
    echo "ship: converged + verified — 2 hosts compared, both at $agreed_sha ($scope_label)."
  else
    echo "ship: converged + verified at ${LOCAL_SHA:-$REMOTE_SHA} — 1 host ($scope_label); cross-host agreement NOT COMPARED."
  fi
else
  echo "ship: incomplete (rc=$rc) — see per-host lines above."
  echo "  rc2=usage(unknown arg, or no host in scope)"
  echo "  rc3=no-repo  rc4=fetch/origin-main-unavailable  rc5=skipped:conflicted-tree(merge in progress)"
  echo "  rc6=host-unidentified"
  echo "  rc7=skipped:cannot-fast-forward(local changes in the way)  rc8=skipped:diverged(needs rebase)"
  echo "  rc9=switch-failed  rc11=verify-failed(git-state)  rc12=consumer-broken(managed artifacts do not resolve)"
  echo "  rc13=consumer-stale(managed artifacts resolve but serve OLD content — re-switch that host)"
  echo "  rc19=hosts-disagree(the two hosts landed on DIFFERENT commits, or agreement was not compared)"
  echo "  rc20=superseded(this run replaced its own script and could not re-run it — re-run ship)"
fi
exit "$rc"
}
