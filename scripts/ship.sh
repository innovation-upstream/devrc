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
# Skips are per-host and non-fatal to the run: if one host cannot fast-forward,
# it is reported with the blocking files named and the OTHER host is still
# converged.
#
# Exit codes:
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
# WHAT IT CANNOT SEE, so nobody reads more into a green than is there: a managed
# path REPLACED by a real file of the same name resolves fine and is not
# reported. This answers "does every managed path resolve", not "is every
# managed path the store link nix intended".
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

# 0) Refuse a mid-merge / conflicted tree OUTRIGHT, before anything else.
#    This must run even when HEAD is already AT origin/main, because that path
#    short-circuits the merge entirely and would otherwise fall straight through
#    to the switch. `home-manager switch --flake` builds from the WORKING TREE,
#    not the commit, so conflict markers in any managed non-nix file
#    (claude/RULES.md, claude/commands/*, hooks, scripts/*) would be DEPLOYED TO
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

if [ "$DO_LOCAL" = 1 ]; then
  echo "=== local ($SHIP_ROLE) ==="
  SHIP_REPO="$SHIP_REPO" SHIP_NO_SWITCH="$SHIP_NO_SWITCH" bash -c "$CONVERGE" || rc=$?
  echo
fi

if [ "$DO_REMOTE" = 1 ]; then
  echo "=== remote ($REMOTE_ROLE — $REMOTE_SSH) ==="
  # Pass the switch toggle remotely; SHIP_REPO stays host-default ($HOME/workspace/devrc).
  if ssh -o ConnectTimeout=10 "$REMOTE_SSH" "SHIP_NO_SWITCH=$SHIP_NO_SWITCH; $CONVERGE"; then :; else
    remrc=$?
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

if [ "$rc" = 0 ]; then
  echo "ship: converged + verified at origin/main (local=$SHIP_ROLE remote=$REMOTE_ROLE)."
else
  echo "ship: incomplete (rc=$rc) — see per-host lines above."
  echo "  rc3=no-repo  rc4=fetch/origin-main-unavailable  rc5=skipped:conflicted-tree(merge in progress)"
  echo "  rc6=host-unidentified"
  echo "  rc7=skipped:cannot-fast-forward(local changes in the way)  rc8=skipped:diverged(needs rebase)"
  echo "  rc9=switch-failed  rc11=verify-failed(git-state)  rc12=consumer-broken(managed artifacts do not resolve)"
fi
exit "$rc"
