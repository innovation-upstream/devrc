#!/usr/bin/env bash
# Commit any dirty state in the /analyze-service index store — one repo per SCOPE.
#
# WHY THIS EXISTS
# ---------------
# `~/.claude/analyze-service-index/` is the write-back store of the
# `/analyze-service` skill (claude/skills/analyze-service/SKILL.md). It holds
# curated, hand-confirmed recon nuance — gotchas, incident tie-ins, pointers —
# one file per service under a per-repo scope directory. Measured 2026-08-06 on
# the workbench: 20 files, 56,862 bytes, mtimes running through that day. It had
# no history, no backup and no host sync, so a single bad Write from any agent
# silently destroyed content that is NOT re-derivable by re-running recon.
#
# WHY A TIMER AND NOT A PROSE INSTRUCTION
# ---------------------------------------
# The store is written by an agent's Write tool mid-recon, so no git operation
# happens naturally. The obvious alternative — appending "then commit" to the
# write-back protocol — is the exact mechanism MEASURED to fail here: see
# claudedocs/close-the-loop/STATE.md, where opt-in prose steps did not stick
# and the response was to move to autonomous loops. A backup that depends on an
# agent remembering is not a backup. PRINCIPLES.md: prefer the deterministic fix.
#
# ONE REPO PER SCOPE, NOT ONE FOR THE STORE
# -----------------------------------------
# The store root is deliberately NOT a repo. Each `<scope>/` directory under it is
# its own independent repository. A store-root repo would silently absorb every
# scope added later, and each scope must be able to carry its own policy. So this
# script DISCOVERS scope directories and commits each one independently; one
# scope failing does not stop the others being backed up, but it does make the
# whole run fail.
#
# 🔴 NO REMOTE. NO PUSH. NO NETWORK.
# ----------------------------------
# The scopes contain client-identifying infrastructure detail — public IPs and
# ports for client hosts, internal hostnames, a named client engineer. This
# script therefore never adds a remote, never pushes, never fetches, and never
# reads a remote from config or environment.
#
# 🔴 THE PRIMARY CONTROL IS CONTAINMENT, NOT DETECTION. Three fix rounds tried to
# make exfiltration *visible* to a static check and each round was evaded a new
# way. What actually holds is the unit's sandbox in nix/home.nix — ProtectSystem
# =strict + ProtectHome=tmpfs + InaccessiblePaths=/dev/shm (together: no writable
# path in the namespace but the store) and PrivateNetwork=true (no route off-box
# at all). MEASURED 2026-08-06 under that sandbox: `cp -r <scope> <dir>` fails
# "Read-only file system", and bash's builtin `/dev/tcp` egress — which no PATH
# restriction can reach — fails where it succeeds uncontained.
#
# ⚠ THE PARENTHETICAL ABOVE ONCE NAMED ONLY THE FIRST TWO DIRECTIVES, AND WAS
# MEASURABLY FALSE: `/dev/shm` was writable, world-shared and host-persistent
# under exactly that pair (2026-08-07 — `cp -r` rc=0, files still on the host
# after the unit exited). The comment was not merely stale; it was load-bearing,
# cited in the test file as the reason a hardcoded-path exfil mutant was allowed
# to survive by design. Read that as a standing warning about this whole block:
# every claim here is about a namespace nothing in this repo can construct at
# test time, so it is only ever as true as the last `systemd-run --user`
# measurement behind it.
#
# 🔴 AND AMBIENT GIT CONFIG IS NEUTRALISED, because it bypassed everything above
# WITHOUT EDITING THIS FILE AT ALL. MEASURED: a global `core.hooksPath`
# post-commit hook (or `init.templateDir` baking one into the repo this script
# bootstraps) copied a scope out of the store while this script printed
# "committed … ok — 1 scope(s) processed" and exited 0. No static reading of this
# file could ever have seen it — the payload was in ~/.gitconfig. See the
# GIT_CONFIG_* block below.
#
# The static ledger below is a SECONDARY check. The COMPLETE set of git
# subcommands this file may invoke is:
#
#     add  check-ignore  commit  config  diff  init  ls-files  rev-parse
#     status  var
#
# and test_the_set_of_git_subcommands_is_exactly_the_asserted_ledger fails when
# that set GROWS *or* SHRINKS. A `git` that is NOT in command position is a
# violation too, rather than being skipped — skipping it is what made `env git
# push`, `timeout … git push`, `eval "git push"`, `xargs git push`, `{ git push;
# }` and `\git push` invisible (MEASURED: 8 wrapper forms survived).
#
# 🔴 BE HONEST ABOUT THE LEDGER'S BLIND SPOT: it reads *git* call sites, so it
# cannot see exfiltration that never invokes git. A plain `cp -r "$scope" …` is
# invisible to it and always will be. An earlier revision of this comment claimed
# "an allowlist has no such blind spot" — that was false, and it is exactly why
# the containment above, not this ledger, is the control being relied on.
#
# If you are editing this file and reaching for `git push`, stop — see the scope
# README, and devrc commit 60e6d9d, which exists because this class of data had
# to be scrubbed retroactively out of a public repo.
#
# 🔴 STAGING IS EXPLICIT, ALWAYS
# ------------------------------
# Never `git add -A`, `--all` or `.` (claude/RULES.md → Git Workflow). Per scope
# this enumerates the UNION of (a) `*.md` files on disk and (b) already-tracked
# files, and passes every one as a literal pathspec. That single call covers new,
# modified AND deleted paths — `git add <deleted-tracked-path>` stages the removal
# (MEASURED, git 2.x: rc=0, `D <path>`), so no `-u` and no `-A` is needed. An
# untracked file that is not `*.md` is therefore NEVER staged.
#
# 🔴 AND THE TREE MUST BE CLEAN AFTERWARDS
# ----------------------------------------
# Because staging is a filtered allowlist, an unexpected file would otherwise sit
# in a scope uncommitted forever while this script kept reporting success. So
# after committing it re-checks and FAILS LOUDLY if anything is still dirty. That
# is the design: surprises become a failed unit (and, via
# OnFailure=notify-failure@%n.service, a desktop toast) rather than either a
# silent omission or a blind sweep-everything-in commit.
#
# 🔴 AN EMPTY `git status` IS NOT PROOF OF A CLEAN TREE
# -----------------------------------------------------
# It is also what a FAILED status call produces. RULES.md → "an EMPTY RESULT
# cannot distinguish two mechanisms". Every git invocation below has its exit
# code checked separately from its output, and no branch treats empty output as
# clean without having first seen rc=0.
#
# Run by hand or via systemd:
#   systemctl --user start analyze-service-index-commit.service
#   scripts/analyze-service-index/commit.sh [STORE_DIR]
#   scripts/analyze-service-index/commit.sh --print-plan [STORE_DIR]  # no writes
#   scripts/analyze-service-index/commit.sh [STORE_DIR] --print-plan  # same thing
#
# Environment:
#   ASI_NO_INIT=1   do not bootstrap a repo for a scope that lacks one; skip it.
#   ASI_BRANCH      branch name for a scope this script initialises (default trunk)
#   ASI_GIT_NAME / ASI_GIT_EMAIL   identity used ONLY when git cannot resolve one
#
# Exit codes: 0 = every scope committed or already clean AND the store root was
# fully enumerated. 1 = at least one scope failed, or the scope list itself is a
# partial view of the store (see store_enum_verdict); read the message. It is
# deliberately never "quietly 0" on an error path — and "no scopes found" counts
# as 0 only once the walk that found none is known to have SUCCEEDED.
set -uo pipefail

PROG="analyze-service-index-commit"

say()  { echo "${PROG}: $*"; }
warn() { echo "${PROG}: WARNING: $*" >&2; }
die()  { echo "${PROG}: $*" >&2; exit 1; }

# ONE place that formats a failed git call: <fail_prefix> <subcommand> <rc>
# [detail]. This is consolidation for its own sake (RULES.md: one rule, one
# place) and it is also what keeps the static ledger honest — the ledger now
# treats EVERY `git` token that is not in command position as a violation, and
# pins the handful of legitimate prose mentions as an explicit set. Fourteen
# hand-written "git <verb> failed" strings would have made that set unreadable;
# collapsing them here leaves three.
git_failed() {
  echo "${PROG}: $1 git $2 failed (rc=$3)${4:+: $4}" >&2
}

# A REAL argument loop, not a positional test. `--print-plan` used to be honoured
# only as $1, so `commit.sh <STORE> --print-plan` silently took the COMMITTING
# path — a dry run that writes is the worst possible failure mode for this
# script (MEASURED: it initialised a repo and committed). Order must not matter,
# and an unrecognised option must be an error rather than a silently-ignored
# word that lands in $STORE.
PRINT_PLAN=0
POSITIONAL=()
while [ $# -gt 0 ]; do
  case "$1" in
    --print-plan) PRINT_PLAN=1; shift ;;
    --) shift; while [ $# -gt 0 ]; do POSITIONAL+=("$1"); shift; done ;;
    -*) die "unknown option: $1
  usage: commit.sh [--print-plan] [STORE_DIR]" ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

if [ "${#POSITIONAL[@]}" -gt 1 ]; then
  die "expected at most one STORE argument, got ${#POSITIONAL[@]}: ${POSITIONAL[*]}
  usage: commit.sh [--print-plan] [STORE_DIR]"
fi

# 🔴 A GIVEN-BUT-EMPTY STORE IS A BUG, NOT A REQUEST FOR THE DEFAULT. `:-`
# cannot tell "no argument" from "an argument that is the empty string", so
# `commit.sh ""` — the shape a caller produces when its own path computation
# fails — would silently `git init`, `git add` and `git commit` in the
# OPERATOR'S REAL STORE instead of the directory it meant. This is the same
# class as ship.sh's SHIP_REPO and drift-check.sh's DRIFT_REPO; see
# scripts/tests/test_repo_path_defaults.py, which pins all SIX sites in both
# directions and asserts each guard actually STOPS the run, not merely warns.
if [ "${#POSITIONAL[@]}" -eq 1 ] && [ -z "${POSITIONAL[0]}" ]; then
  die "STORE argument was given but is EMPTY.
  That is a caller bug, not a request for the default — an empty value would
  silently resolve to \${HOME}/.claude/analyze-service-index and commit in the
  operator's real store. Pass no argument for the default, or pass a path."
fi
STORE="${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}"

ASI_GIT_NAME="${ASI_GIT_NAME:-analyze-service index}"
ASI_GIT_EMAIL="${ASI_GIT_EMAIL:-analyze-service-index@localhost}"
ASI_BRANCH="${ASI_BRANCH:-trunk}"
ASI_NO_INIT="${ASI_NO_INIT:-0}"

command -v git >/dev/null 2>&1 ||
  die "the version-control binary is not on PATH — refusing to report success"

# --- 🔴 NEUTRALISE THE AMBIENT REPO POINTERS ------------------------------------
# WHICH repository, before WHICH config. Everything below this line reaches git
# as `git -C "$scope" …`, and `-C` is the weakest possible claim about where a
# command lands: GIT_DIR OVERRIDES IT.
#
# MEASURED 2026-08-22 against a throwaway decoy repo with a linked worktree, on
# git 2.55.0, running THIS script unchanged:
#
#     GIT_DIR=<decoy>/.git/worktrees/wt  commit.sh <store>
#       -> scope_repo_state: `git -C "$scope" rev-parse --show-toplevel` honours
#          GIT_DIR and takes the CWD as the work tree, so it returns "$scope"
#          ITSELF -> state 1, "it IS its own repo".
#       -> that skips BOTH the `git init` bootstrap AND the "not its own repo"
#          refusal, and every later `git -C "$scope" add/commit` writes into the
#          DECOY's gitdir, index and branch. The decoy's `decoy/target` moved to
#          `autocommit: 3 change(s) in the some-scope analyze-service index`
#          while this script printed "committed <sha>" and exited 0.
#
# That is the 2026-08-21 incident's mechanism (scripts/testlib/gitenv.py) arriving
# at the one program in this repo whose whole job is to COMMIT. `run-tests.sh`,
# `run-node-tests.sh` and `gate.sh` already strip these for the test tiers; this
# is the same strip AT THE WRITER, so a caller that never goes through a runner —
# a systemd unit, an operator's shell, a future script — cannot spoof it either.
#
# THE SET IS NOT CHOSEN HERE. `scripts/testlib/gitenv.py::REPO_POINTER_VARS` owns
# it and test_git_repo_isolation.py pins this spelling against it in BOTH
# directions, exactly as it does for the four other clearers. Every name below
# can make git resolve a DIFFERENT repository, index or object store than the
# `-C` says.
#
# UNCONDITIONAL: there is no workflow in which this unit should be aimed at a
# repository by inherited environment.
DEVRC_GIT_REPO_POINTERS=(
  GIT_DIR                            # the repository itself; beats -C
  GIT_WORK_TREE                      # the working tree
  GIT_COMMON_DIR                     # where refs/config actually live
  GIT_INDEX_FILE                     # the index that staging writes
  GIT_OBJECT_DIRECTORY               # where new objects are written
  GIT_ALTERNATE_OBJECT_DIRECTORIES   # extra object stores
  GIT_NAMESPACE                      # the ref namespace refs land in
  GIT_PREFIX                         # hook-injected pathspec prefix
  GIT_GRAFT_FILE                     # repo-scoped grafts
  GIT_SHALLOW_FILE                   # repo-scoped shallow list
  GIT_CONFIG                         # legacy: the config file a write lands in
)
# ⚠ The trailing comments above deliberately avoid the word this file's own
# ledger test scans for. `_script_code_lines` strips comment-ONLY lines, so a
# trailing comment naming a subcommand reads as an unplaceable call site and
# fails test_every_unplaceable_git_token_is_pinned_prose — which is the right
# outcome for that test, and a wording constraint here rather than a pin there.
unset "${DEVRC_GIT_REPO_POINTERS[@]}"

# --- 🔴 AND TWO MORE THAT ARE THIS SCRIPT'S PROBLEM ALONE -----------------------
# These are NOT on GUARD 9's shared ledger and must not be added to it. GUARD 9's
# remit is "which repository does a command LAND in", and neither of these can
# redirect one — which is exactly why they are invisible to it, and exactly why
# they still break THIS script. A ledger is only as wide as the question it asks.
#
# MEASURED 2026-08-22 (git 2.55.0), each on its own against a decoy:
#
#   GIT_CEILING_DIRECTORIES — it stops the upward discovery walk EARLY. It cannot
#     point git at another repo (with a ceiling naming an UNRELATED repo,
#     `rev-parse --show-toplevel` still returns the enclosing one). But stopping
#     the walk is precisely how `scope_repo_state` is made to answer 0 instead of
#     2, and 0 means BOOTSTRAP. A scope genuinely nested inside a foreign
#     checkout went from
#         rc=1  "scope inner: not its own repo — it sits inside <foreign>. Refusing…"
#     to
#         rc=0  "scope inner: initialised a new repository … ok — 1 scope(s) processed"
#     with a `.git` planted INSIDE somebody else's working tree. Nothing lands in
#     foreign HISTORY, so this is not the exfiltration shape — but the refusal
#     that test_a_leaked_pointer_does_not_defeat_the_nested_scope_refusal calls
#     "THE GUARD'S REAL JOB" is silently converted into its opposite, and the run
#     still prints ok. An earlier version of this comment called that "the safe
#     direction" and stopped there; that was true about exfiltration and
#     misleading about everything else.
#
#   GIT_TEMPLATE_DIR — the ENVIRONMENT twin of `init.templateDir`, which the
#     GIT_CONFIG block below already pins off. Neutralising the config route and
#     leaving the env route open is the asymmetry worth naming: `GIT_CONFIG_GLOBAL
#     =/dev/null` does not touch a variable. MEASURED: a template
#     `hooks/post-commit` IS copied into the repository this script bootstraps and
#     PERSISTS there. It did not fire, because GIT_CONFIG_KEY_0 redirects
#     core.hooksPath — so this is a planted-but-dormant payload, armed the moment
#     anyone runs git in that scope by hand, which is exactly what an operator
#     does to inspect a backup.
#
# Kept in a SEPARATE array on purpose: `DEVRC_GIT_REPO_POINTERS` above is pinned
# two-way against `testlib/gitenv.py::REPO_POINTER_VARS`, so quietly widening it
# here would break that pin — and widening the shared ledger would change GUARD 9
# for every runner on the strength of a defect that is local to this script.
ASI_LOCAL_GIT_POINTERS=(
  GIT_CEILING_DIRECTORIES            # stops discovery early -> refusal becomes bootstrap
  GIT_TEMPLATE_DIR                   # env twin of init.templateDir; plants files in the new repo
)
unset "${ASI_LOCAL_GIT_POINTERS[@]}"

# --- 🔴 NEUTRALISE AMBIENT GIT CONFIG ------------------------------------------
# This block exists because the no-exfiltration guarantee was bypassable WITHOUT
# TOUCHING THIS FILE. MEASURED 2026-08-06 (git 2.55.0), end to end:
#
#   ~/.gitconfig: [core] hooksPath = <attacker dir>   →  a post-commit hook ran
#   and copied a scope's client-identifying content out of the store, while this
#   script printed "committed <sha>" and "ok — 1 scope(s) processed" and exited
#   0. `[init] templateDir = <dir>` does the same by baking the hook into the
#   repository THIS SCRIPT bootstraps.
#
# The fix deliberately does NOT enumerate dangerous keys (hooksPath, fsmonitor,
# filter.*.clean, diff.external, …) — enumerating badness is the mistake the
# ledger already made twice. It removes the whole ambient surface instead:
#
#   GIT_CONFIG_NOSYSTEM=1     ignore /etc/gitconfig entirely
#   GIT_CONFIG_GLOBAL=/dev/null   ignore ~/.gitconfig and ~/.config/git/config
#                             entirely — EVERY key, not a chosen subset
#
# That leaves only per-repo `.git/config` and `.git/hooks`, which an errant agent
# Write could still plant inside a scope — squarely in the threat model, since
# the whole premise is that agents write into this store. So hooks are pinned off
# for every invocation via GIT_CONFIG_COUNT (equivalent to `-c`, and MEASURED to
# override even a repo-local `core.hooksPath`), pointing at a directory that is
# empty by construction. VERIFIED: with this in place an ambient hook, a
# template-baked hook and a directly-planted .git/hooks/post-commit all fail to
# fire.
#
# Each of the two controls is pinned by a test that ONLY it can satisfy —
# test_no_ambient_global_config_reaches_git_at_all for the GLOBAL neutralisation,
# test_a_hook_planted_inside_the_scope_does_not_fire for the hooksPath pin. That
# separation was not free: a mutation sweep found that deleting either one killed
# NO test, because each independently blocked the other's fixture.
#
# ⚠ `init.templateDir` (KEY_1) is the exception and is deliberately left as
# REDUNDANT belt-and-braces: with GLOBAL neutralised there is no remaining source
# for it, so no single-mutant test can observe it. It is kept because it still
# holds if the GLOBAL line is ever removed. Stated plainly rather than implied,
# so nobody later mistakes it for a covered control.
#
# Consequence worth stating: commits are no longer attributed to the operator's
# global git identity. That is intentional — the identity of a machine backup
# should not vary by host — and it is what makes
# test_an_identity_is_seeded_when_git_cannot_resolve_one environment-independent
# instead of passing in a sandbox and failing on a host with a system gitconfig.
ASI_NOHOOKS="$(mktemp -d "${TMPDIR:-/tmp}/asi-nohooks.XXXXXX")" ||
  die "could not create the empty hooks directory — refusing to run with hooks live"
# ALL enumeration goes to FILES, not pipes, for two independent reasons:
#   * find's exit code survives (a pipe discards it — see list_candidates; a
#     process substitution discards it just as completely — see list_scopes);
#   * NUL-delimited output survives. `$(…)` silently drops NUL bytes ("warning:
#     command substitution: ignored null byte in input"), which would corrupt
#     exactly the framing that makes newline-containing paths safe.
ASI_CANDFILE="$(mktemp "${TMPDIR:-/tmp}/asi-cands.XXXXXX")" &&
  ASI_SORTED="$(mktemp "${TMPDIR:-/tmp}/asi-sorted.XXXXXX")" &&
  ASI_IGNORED="$(mktemp "${TMPDIR:-/tmp}/asi-ignored.XXXXXX")" &&
  ASI_SCOPEFILE="$(mktemp "${TMPDIR:-/tmp}/asi-scopes.XXXXXX")" &&
  ASI_SYMFILE="$(mktemp "${TMPDIR:-/tmp}/asi-syms.XXXXXX")" ||
  die "could not create the temporary files this run needs"
cleanup() {
  rm -rf "$ASI_NOHOOKS" "$ASI_CANDFILE" "$ASI_SORTED" "$ASI_IGNORED" \
         "$ASI_SCOPEFILE" "$ASI_SYMFILE"
}
trap cleanup EXIT

export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0=core.hooksPath   GIT_CONFIG_VALUE_0="$ASI_NOHOOKS"
export GIT_CONFIG_KEY_1=init.templateDir GIT_CONFIG_VALUE_1="$ASI_NOHOOKS"

# A host that has never run /analyze-service has no store. Legitimate
# nothing-to-do, not an error: this unit is installed on every host.
if [ ! -d "$STORE" ]; then
  say "no store at ${STORE} — nothing to do"
  exit 0
fi

# --- Scope discovery -----------------------------------------------------------
# Immediate subdirectories of the store, each of which is its own repo.
#
# Dot-directories are excluded, and that is not cosmetic: if the store root ever
# became a repo itself, a bare `-type d` walk enumerates `.git` AS A SCOPE and
# the script starts reasoning about git's own internals (FOUND by the nested-repo
# negative control, which printed `scope .git: ... skipping`). A scope is a
# repo-slug; none of them begin with a dot.
#
# 🔴 NUL-DELIMITED. That is correctness, not polish: a scope name containing a
# newline used to be split into two nonexistent scopes, each reported "no *.md
# files — skipping", exit 0 (MEASURED) — the content sat UNVERSIONED and the unit
# called it success, which is the exact silent-loss outcome this script exists to
# prevent. `sort -z` keeps the NUL framing; pipefail makes a `find` failure this
# function's failure instead of an empty list that reads as "no scopes".
#
# 🔴 A SYMLINKED SCOPE IS NOT SUPPORTED, AND THAT IS A DECISION, NOT AN
# OVERSIGHT. Earlier rounds put `-L` here so a symlinked scope would enumerate,
# and a test asserted it worked. Under the unit's sandbox it does NOT, and the
# failure is the silent kind. MEASURED 2026-08-07 with `systemd-run --user`
# carrying the unit's exact directives, one real scope plus one symlinked scope:
#
#     contained    → "ok — 1 scope(s) processed", exit 0, and the symlink
#                    target has NO .git at all — never versioned, no warning
#     uncontained  → "ok — 2 scope(s) processed", target versioned
#
# The mechanism is ProtectHome=tmpfs: the symlink's target lives elsewhere under
# $HOME, which does not exist inside the namespace, so the link dangles, `find
# -L … -type d` does not match it, and the scope vanishes. The suite runs
# uncontained and therefore CANNOT see this — it is the config-blind-suite
# hazard in RULES.md, and it is why the old test was green while production was
# broken.
#
# Making it genuinely work would mean widening BindPaths to cover symlink
# targets, i.e. handing the unit writable paths outside the store — the exact
# hazard the exact-list assertion in the test file now pins shut — for a feature
# with zero users. So: both bind lists stay frozen at one entry each, symlinks
# are refused, and the refusal is LOUD (see list_scope_symlinks). Silence is the
# one outcome that is not allowed.
#
# 🔴 AND THE EXIT CODE IS OBSERVABLE, BECAUSE THE RESULT GOES TO A FILE.
# This used to be `find … | sort -z` read as `< <(list_scopes)`, and a process
# substitution discards the function's rc as completely as a pipe discards
# find's — the two rc-laundering shapes this file has now been bitten by three
# times. MEASURED 2026-08-10 on this host (GNU findutils 4.10.0; note the
# INTERACTIVE `find` here is aliased to bfs 4.1.1, which is NOT what a script
# gets — bfs returns 0 on the same fixture, so an interactive spot-check lies):
# with the store root at mode 0300 over a real scope holding `svc.md`, find
# exits 1 and prints nothing, and the run said
#
#     "no scope directories under <store> — nothing to do"   (rc=0)
#
# i.e. a store that could not be READ was indistinguishable from a store that
# was EMPTY. That is this file's own header — "an EMPTY RESULT cannot
# distinguish two mechanisms" — failing on the outermost walk of all.
#
# The rc is now returned to the caller, which records it in ASI_SCOPE_ENUM_RC.
# Note this walk is `-maxdepth 1`, so MEASURED the only reachable failure is the
# root itself and the list then comes back EMPTY (an unreadable *child* dir does
# not error a depth-1 walk: rc=0, child still listed). The loop still runs over
# whatever WAS enumerated rather than aborting on the rc — protect first, alarm
# second, exactly as commit_once's header and the #372 probe fix require — so a
# future partial list is committed rather than refused.
list_scopes() {
  local out="$1" rc=0
  find "$STORE" -mindepth 1 -maxdepth 1 -type d -not -name '.*' -printf '%f\0' \
    > "$out" || rc=$?
  sort -z "$out" -o "$out" || return $?
  return "$rc"
}

# Symlinks sitting where a scope directory should be. `-type l` under the
# default `-P`, deliberately: it matches a link to a real directory AND a
# dangling one. `-xtype l` would match only the dangling case — which is what
# the link becomes INSIDE the sandbox but not what it looks like on the host or
# in the test suite, so a guard built on it would be unobservable in exactly the
# environment that has to prove it works.
#
# Same file-not-pipe treatment as list_scopes, and for the same reason: a
# symlinked scope is a NAMED FAILURE, so an enumeration that silently returned
# nothing would convert that named failure back into the silence it replaced.
list_scope_symlinks() {
  local out="$1" rc=0
  find "$STORE" -mindepth 1 -maxdepth 1 -type l -not -name '.*' -printf '%f\0' \
    > "$out" || rc=$?
  sort -z "$out" -o "$out" || return $?
  return "$rc"
}

# --- The store-level analogue of enum_verdict ----------------------------------
# enum_verdict (below) covers an incomplete walk INSIDE one scope. This covers an
# incomplete walk OF THE STORE, which is strictly worse: a scope that cannot be
# listed is never even reached, so not one of the per-scope guards runs and the
# only remaining signal is this one. Same shape as enum_verdict deliberately —
# record during the walk, judge afterwards, never refuse up front.
ASI_SCOPE_ENUM_RC=0
ASI_SYMLINK_ENUM_RC=0
store_enum_verdict() {
  [ "$ASI_SCOPE_ENUM_RC" -eq 0 ] && [ "$ASI_SYMLINK_ENUM_RC" -eq 0 ] && return 0
  echo "${PROG}: scope enumeration of ${STORE} was INCOMPLETE (find rc: directories=${ASI_SCOPE_ENUM_RC}, symlinks=${ASI_SYMLINK_ENUM_RC}). Every scope that COULD be listed has been processed, so nothing readable was skipped — but the store root itself could not be fully read, so an unknown number of scopes were never even looked at and may hold unversioned index files. A store that cannot be READ must never be reported as a store that is EMPTY. Check the permissions on ${STORE} itself." >&2
  return 1
}

# Every *.md on disk in a scope (relative to it), plus everything already
# tracked, NUL-delimited, written to the file named by $1.
#
# 🔴 NO `-L`, AND NO `-H` EITHER. A round of this PR "fixed" symlinked-scope
# enumeration by putting `-L` on this walk too, which made it descend INTO
# symlinked subdirectories of a scope and emit paths beyond them. git then
# refuses the pathspec — `fatal: pathspec 'linkdir/inner.md' is beyond a
# symbolic link` — so `git add` exits 128, the scope FAILS, and MEASURED it
# never self-heals: byte-identical failure on the next run, with the whole scope
# left unversioned forever.
#
# The `-H` that replaced it existed for ONE reason: to follow a scope that was
# itself a symlink. Symlinked scopes are now refused outright (see list_scopes),
# so `$scope` can never be a symlink and `-H` differs from the default in no
# case this script can reach. It is dropped rather than left in place, because a
# flag whose stated justification no longer exists reads as a supported case and
# invites someone to re-add `-L` to "finish the job". All three walks in this
# file — this one, the md_probe, and scope discovery — now answer the same
# question the same way.
#
# 🔴 AND THE EXIT CODE IS CHECKED. This used to be piped straight into `sort -zu`
# by its callers, which discards find's rc — the same empty-result-reads-as-clean
# confusion this file's own header rails against. Writing to a file instead of a
# pipe is what makes the rc observable.
list_candidates() {
  local out="$1" scope="$2" is_repo="$3" rc=0
  find "$scope" -type f -name '*.md' -not -path "${scope}/.git/*" -printf '%P\0' \
    > "$out" || rc=$?
  if [ "$rc" -ne 0 ]; then
    return "$rc"
  fi
  if [ "$is_repo" -eq 1 ]; then
    git -C "$scope" ls-files -z >> "$out" || return $?
  fi
  return 0
}

# Run a git command capturing stdout and stderr SEPARATELY, preserving the exit
# code. 🔴 Folding stderr into stdout with `2>&1` CORRUPTS every predicate built
# on the output: `git status --porcelain` can exit 0 while writing a warning
# (MEASURED with an unreadable subdirectory — rc=0, empty stdout, one warning
# line on stderr), and with `2>&1` that warning became the "dirty state". A
# genuinely CLEAN tree was then reported as dirty, the wrong cause was named,
# the change count was inflated, and the warning text was embedded verbatim in
# the commit message. Diagnostics belong in error messages, never in data.
CAP_OUT=""
CAP_ERR=""
capture() {
  local errfile rc
  errfile="$(mktemp "${TMPDIR:-/tmp}/asi-stderr.XXXXXX")" || return 125
  CAP_OUT="$("$@" 2>"$errfile")"
  rc=$?
  CAP_ERR="$(cat "$errfile")"
  rm -f "$errfile"
  return "$rc"
}

# Is this scope its OWN repo? `git rev-parse` walks UP the tree, so if the store
# root or $HOME ever became a repo, every command would silently operate on THAT
# repo instead — committing client-sensitive content into somebody else's
# history. Compare the discovered toplevel against the scope and refuse on a
# mismatch. Echoes: 1 = own repo, 0 = no repo, 2 = inside a DIFFERENT repo.
#
# 🔴 THIS FUNCTION IS THE SPOOFABLE ONE, and the thing that makes it honest is
# NOT in this function: it is the DEVRC_GIT_REPO_POINTERS `unset` at the top of
# the file. An inherited GIT_DIR makes the `rev-parse` below answer "$scope"
# whatever the truth is, which reports state 1 — its own repo — for a scope that
# is a bare directory sitting inside somebody else's checkout, so BOTH the
# bootstrap and the state-2 refusal are skipped and the commits land in the
# foreign repo. Do not move that `unset`, and do not add a caller that re-sets a
# pointer between it and here.
scope_repo_state() {
  local scope="$1" top
  top="$(git -C "$scope" rev-parse --show-toplevel 2>/dev/null)"
  if [ $? -ne 0 ] || [ -z "$top" ]; then
    echo 0
  elif [ "$(realpath "$top")" = "$(realpath "$scope")" ]; then
    echo 1
  else
    echo 2
  fi
}

# --- Stray content at the store root -------------------------------------------
# The root is not a repo, so anything with real content sitting there is NOT
# versioned. README.md is the expected signpost; anything else is a warning, not
# a failure — a permanently-red gate is worse than no gate (RULES.md), but a
# silently-unversioned service file is exactly the hazard this work exists to
# close, so it must be visible.
#
# ⚠ THIS ONE DELIBERATELY STILL DISCARDS find's rc, AND THAT IS ARGUED, NOT
# OVERLOOKED. It walks the IDENTICAL path at the IDENTICAL depth as list_scopes
# (`find "$STORE" -mindepth 1 -maxdepth 1`), differing only in `-type f` vs
# `-type d`, so its failure set is exactly list_scopes' failure set: it cannot
# fail on a run where list_scopes succeeds. Any rc it could report is therefore
# already reported, loudly and non-zero, by store_enum_verdict — a second alarm
# for the same fact would be noise, and this path is warning-only with no
# refuse/skip decision hanging off it. 🔴 That reasoning dies the moment the two
# walks stop sharing a root or a depth: if you change either one, this needs its
# own rc check.
warn_about_store_root_files() {
  local strays
  strays="$(find "$STORE" -mindepth 1 -maxdepth 1 -type f -name '*.md' \
              -not -name 'README.md' -printf '%f\n' | sort)"
  if [ -n "$strays" ]; then
    warn "these files sit at the STORE ROOT and are therefore NOT versioned
  (the root is deliberately not a repo — move them into a scope directory):
$(printf '%s\n' "$strays" | sed 's/^/    /')"
  fi
}

# --- --print-plan: pure text, no mutation --------------------------------------
# Mirrors claude-log-rotate's --print-config, so the policy (which scopes, what
# gets staged, what does not) is testable without committing anything.
if [ "$PRINT_PLAN" -eq 1 ]; then
  echo "store:   ${STORE}"
  echo "remote:  none — this script never adds one, never pushes, never fetches"
  echo "staging: explicit pathspecs only — never -A, never --all, never ."
  scopes_found=0
  # Both walks' rcs are recorded here too. A plan that prints "(none found)" over
  # a store it could not READ is the same silent zero as the real run's, and it
  # is the more dangerous of the two: --print-plan is what an operator reaches
  # for to ask "what is in there?".
  list_scope_symlinks "$ASI_SYMFILE" || ASI_SYMLINK_ENUM_RC=$?
  list_scopes "$ASI_SCOPEFILE" || ASI_SCOPE_ENUM_RC=$?
  # Symlinks first, matching the real walk. A plan that quietly omits an entry
  # which WILL fail the next real run describes a store that does not exist.
  while IFS= read -r -d '' name; do
    [ -n "$name" ] || continue
    scopes_found=1
    echo "scope:   ${name}  (SYMLINK — would FAIL; symlinked scopes are not supported)"
  done < "$ASI_SYMFILE"
  while IFS= read -r -d '' name; do
    [ -n "$name" ] || continue
    scopes_found=1
    scope="${STORE}/${name}"
    st="$(scope_repo_state "$scope")"
    # `desc` is deliberately reset every iteration and the case has a `*)`
    # default: it used to be a global with no default, so an unexpected state
    # silently REUSED the previous scope's description — a plan that quietly
    # describes the wrong scope is worse than one that admits confusion.
    desc="unknown"
    case "$st" in
      1) desc="repo" ;;
      0) desc="$([ "$ASI_NO_INIT" = "1" ] && echo "no repo — would SKIP (ASI_NO_INIT=1)" \
                                          || echo "no repo — would git init -b ${ASI_BRANCH}")" ;;
      2) desc="INSIDE ANOTHER REPO — would refuse" ;;
      *) desc="UNKNOWN repo state (${st}) — would refuse" ;;
    esac
    echo "scope:   ${name}  (${desc})"
    if list_candidates "$ASI_CANDFILE" "$scope" \
         "$([ "$st" = "1" ] && echo 1 || echo 0)"; then
      sort -zu "$ASI_CANDFILE" | tr '\0' '\n' | sed 's/^/    /'
    else
      echo "    (could not enumerate this scope — it would FAIL, not be skipped)"
    fi
  done < "$ASI_SCOPEFILE"
  # The "(none found)" line is only truthful once the walk is known to have
  # SUCCEEDED. Order matters: the verdict is consulted before the empty-store
  # sentence is allowed to be printed at all.
  if ! store_enum_verdict; then
    echo "scope:   (enumeration INCOMPLETE — the plan above is a PARTIAL view of ${STORE})"
    exit 1
  fi
  [ "$scopes_found" -eq 1 ] || echo "scope:   (none found under ${STORE})"
  exit 0
fi

# --- Stage, commit, and assert the tree is clean afterwards --------------------
# Split out of commit_scope so the benign-race retry below has ONE implementation
# to call twice rather than a second copy of the staging rule (RULES.md: one
# rule, one place).
#
#   0 = committed, tree clean afterwards
#   2 = committed, but the tree was RE-DIRTIED during the commit and every path
#       still dirty is COVERED (matches *.md, or is already tracked) — benign,
#       retryable, self-healing; NOT an allowlist gap
#   1 = failure; the specific cause has already been reported to stderr
commit_once() {
  local scope="$1" name="$2" fail_prefix="$3" before="$4"
  local rc staged_rc n_changed msg sha after rec path
  local -a paths_arr uncovered_arr ignored_arr

  # 🔴 AN INCOMPLETE ENUMERATION IS RECORDED, NOT ABORTED ON.
  # find's exit code used to be discarded entirely (piped into `sort -zu`), so a
  # scope containing an unreadable subdirectory enumerated a PARTIAL list and the
  # run reported success — content that could not be seen was silently never
  # staged, which is the failure this whole unit exists to prevent.
  #
  # But refusing outright is worse, not better: an unreadable `sub/` would then
  # stop a perfectly readable `alpha.md` from ever being backed up, i.e. the
  # guard would cause the data loss it is meant to catch. So commit what WAS
  # enumerated — that content is now safe — and fail the scope afterwards so the
  # incompleteness is loud. Protect first, then alarm.
  #
  # This is the SECOND enumeration of the scope in a dirty run — commit_scope
  # already did one before its clean-tree early return, which is what makes the
  # alarm reachable on a CLEAN run too. Re-enumerating here is not redundant:
  # attempt 2 of the retry loop must see a tree that was re-dirtied since
  # attempt 1. Deliberately re-derives ASI_ENUM_RC from scratch rather than
  # OR-ing into it, so a condition that has cleared stops alarming.
  ASI_ENUM_RC=0
  list_candidates "$ASI_CANDFILE" "$scope" 1 || ASI_ENUM_RC=$?
  sort -zu "$ASI_CANDFILE" > "$ASI_SORTED"
  mapfile -d '' -t paths_arr < "$ASI_SORTED"
  if [ "${#paths_arr[@]}" -eq 0 ]; then
    echo "${PROG}: ${fail_prefix} tree is dirty but no candidate paths were enumerated. Dirty state:
${before}" >&2
    return 1
  fi

  # 🔴 IGNORED INDEX FILES ARE THEIR OWN NAMED ERROR, CHECKED BEFORE STAGING.
  # A `*.md` line in a scope's .gitignore makes every index file unstageable.
  # `git add` then fails with "The following paths are ignored by one of your
  # .gitignore files", which reads as a tooling problem and buries the only fact
  # that matters: THOSE FILES WILL NEVER BE VERSIONED. MEASURED: rc=1 on every
  # run, byte-identical, forever — a permanently-red gate, which RULES.md says is
  # worse than no gate because it trains the operator to ignore the toast.
  #
  # Pre-filtering here reports the real cause by name and leaves the index
  # completely untouched. (The "half-staged index" this was also reported to
  # cause did NOT reproduce on git 2.55.0 — `git add` validates every pathspec
  # before staging any of it, so nothing is staged. The permanent redness is
  # real; the half-staging was not, and is not claimed here.)
  git -C "$scope" check-ignore -z --stdin < "$ASI_SORTED" > "$ASI_IGNORED" 2>/dev/null
  rc=$?
  # rc 0 = at least one path is ignored, 1 = none are, >1 = the call itself
  # failed. Only 1 is the clean case; rc=0 with an empty file must NOT read as
  # "nothing ignored" — that is the empty-result confusion again.
  if [ "$rc" -gt 1 ]; then
    git_failed "$fail_prefix" check-ignore "$rc"
    return 1
  fi
  if [ "$rc" -eq 0 ]; then
    mapfile -d '' -t ignored_arr < "$ASI_IGNORED"
    if [ "${#ignored_arr[@]}" -gt 0 ]; then
      echo "${PROG}: ${fail_prefix} an index file is gitignored and will therefore never be versioned:
$(printf '    %s\n' "${ignored_arr[@]}")
  A .gitignore inside a scope silently un-backs-up the very content this unit
  exists to protect. Nothing was staged. Remove the pattern, or move the file
  out of the index — do not use -f." >&2
      return 1
    fi
  fi

  capture git -C "$scope" add -- "${paths_arr[@]}"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    git_failed "$fail_prefix" add "$rc" "${CAP_ERR}${CAP_OUT}"
    return 1
  fi

  # Did the explicit allowlist actually pick anything up? A dirty tree that
  # stages to nothing means every dirty path was filtered out — report it,
  # never return success.
  git -C "$scope" diff --cached --quiet
  staged_rc=$?
  if [ "$staged_rc" -eq 0 ]; then
    echo "${PROG}: ${fail_prefix} tree is dirty but NOTHING staged — every dirty path was filtered out by the *.md allowlist. Look at it by hand; do NOT widen the filter blindly. Dirty state:
${before}" >&2
    return 1
  elif [ "$staged_rc" -gt 1 ]; then
    git_failed "$fail_prefix" "diff --cached" "$staged_rc"
    return 1
  fi

  n_changed="$(printf '%s\n' "$before" | grep -c .)"
  msg="autocommit: ${n_changed} change(s) in the ${name} analyze-service index

Committed by ${PROG} (systemd --user timer). Working-tree state at the time:

${before}

Staged as explicit pathspecs (never -A/--all/.), then the tree was asserted
clean. See scripts/analyze-service-index/commit.sh in devrc."

  capture git -C "$scope" commit -q -m "$msg"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    git_failed "$fail_prefix" commit "$rc" "${CAP_ERR}${CAP_OUT}"
    return 1
  fi

  capture git -C "$scope" rev-parse --short HEAD
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} could not read HEAD after committing: ${CAP_ERR}" >&2
    return 1
  fi
  sha="$CAP_OUT"

  # 🔴 The assertion that makes the allowlist safe.
  capture git -C "$scope" status --porcelain
  rc=$?
  after="$CAP_OUT"
  if [ "$rc" -ne 0 ]; then
    git_failed "$fail_prefix" "status (AFTER committing)" "$rc" "$CAP_ERR"
    return 1
  fi
  if [ -z "$after" ]; then
    say "${fail_prefix} committed ${sha} — ${n_changed} change(s)"
    return 0
  fi

  # PARTITION what is still dirty rather than blaming the allowlist for all of
  # it. The old code reported "Something is not covered by the *.md allowlist"
  # for ANY leftover dirt — including a plain `.md` file that IS covered and had
  # simply been written between `git add` and `git commit`. That is a race the
  # design positively guarantees (the store is written by agents at arbitrary
  # times), so it produced a failed unit and a sticky critical toast pointing at
  # the wrong cause, on a run that lost no data at all.
  uncovered_arr=()
  while IFS= read -r -d '' rec; do
    # `-z` records are "XY<space>PATH". A rename also emits a bare continuation
    # record holding the ORIGINAL path; the primary record already covers it.
    [ "${#rec}" -gt 3 ] || continue
    case "$rec" in
      ??\ *) path="${rec:3}" ;;
      *) continue ;;
    esac
    case "$path" in
      *.md) continue ;;
    esac
    if git -C "$scope" ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
      continue
    fi
    uncovered_arr+=("$path")
  done < <(git -C "$scope" status --porcelain -z 2>/dev/null)

  if [ "${#uncovered_arr[@]}" -eq 0 ]; then
    return 2
  fi

  echo "${PROG}: ${fail_prefix} committed ${sha}, but the tree is STILL DIRTY afterwards:
${after}
  These path(s) are neither matched by the *.md allowlist nor already tracked:
$(printf '    %s\n' "${uncovered_arr[@]}")
  This is loud on purpose — decide deliberately whether they belong in the index." >&2
  return 1
}

# --- Commit one scope ----------------------------------------------------------
# Returns 0 on success (committed or already clean), 1 on failure. Deliberately
# does NOT exit: one bad scope must not stop the others being backed up.

# Every point at which commit_scope is about to report success runs through
# here. A scope whose candidate list could not be fully read is a FAILURE even
# though the commit landed — the readable content is safe, but part of the scope
# is unaccounted for and may hold index files that will never be versioned.
# Reporting 0 here is precisely the "empty result read as clean" this file's
# header forbids.
ASI_ENUM_RC=0
enum_verdict() {
  [ "$ASI_ENUM_RC" -eq 0 ] && return 0
  echo "${PROG}: $1 candidate enumeration was INCOMPLETE (find rc=${ASI_ENUM_RC}). Everything that COULD be read has been committed, so nothing readable was lost — but part of this scope could not be listed and may hold unversioned index files. Check directory permissions inside the scope." >&2
  return 1
}

commit_scope() {
  local scope="$1" name="$2"
  local fail_prefix="scope ${name}:"
  local st rc before attempt result
  ASI_ENUM_RC=0

  st="$(scope_repo_state "$scope")"

  if [ "$st" = "2" ]; then
    echo "${PROG}: ${fail_prefix} not its own repo — it sits inside $(git -C "$scope" rev-parse --show-toplevel 2>/dev/null). Refusing to commit client-sensitive content into a repository that is not the index scope. Fix the nesting first." >&2
    return 1
  fi

  if [ "$st" = "0" ]; then
    # Nothing to version yet? Then there is nothing to bootstrap either.
    #
    # find's rc is checked SEPARATELY from its output. Piping it into `grep -q .`
    # discarded the exit code, so an UNREADABLE scope produced no output, read as
    # "no *.md files", and was skipped with a success message — the same
    # empty-result-means-clean confusion the header warns about, on the one path
    # whose whole job is to notice new content.
    local md_probe md_rc
    # No `-L` and no `-H`, matching list_candidates exactly: the probe decides
    # whether to CREATE a repository and list_candidates decides what can be
    # STAGED, so they must answer the same question. `-L` here would make the
    # probe see content beyond a symlink that list_candidates cannot stage and
    # git cannot track — a repository bootstrapped and then failed on daily.
    md_probe="$(find "$scope" -type f -name '*.md' -print -quit 2>/dev/null)"
    md_rc=$?
    # 🔴 PROTECT FIRST, ALARM SECOND — HERE TOO, NOT ONLY IN commit_once.
    # A bad probe rc alone used to `return 1` before `git init` ever ran, which
    # is the "refuse outright" that commit_once's own header forbids: MEASURED
    # 2026-08-09 on a NEW scope holding a readable `alpha.md` beside an
    # unreadable `sub/` (GNU findutils 4.10.0 — `find … -print -quit` exits 1
    # *and* prints `alpha.md`, because `-quit` does not clear an error that has
    # already occurred), the scope was left with NO `.git` at all and alpha.md
    # was never versioned, on that run or any later one. The guard meant to stop
    # a silent skip was instead causing the data loss it exists to catch, and it
    # is the un-versioned-forever half that matters — the alarm fired either way.
    #
    # So the probe's rc only REFUSES when nothing readable was found. Once a
    # readable index file exists the repo is bootstrapped and that content is
    # committed; the incompleteness is not swallowed — `list_candidates` below
    # re-derives it into ASI_ENUM_RC and `enum_verdict` fails the scope with
    # "candidate enumeration was INCOMPLETE", on this run and on every clean run
    # after it, until a chmod lands.
    if [ "$md_rc" -ne 0 ] && [ -z "$md_probe" ]; then
      echo "${PROG}: ${fail_prefix} could not enumerate *.md files (find rc=${md_rc}) — refusing to report a clean skip" >&2
      return 1
    fi
    if [ -z "$md_probe" ]; then
      say "${fail_prefix} no repo and no *.md files — skipping"
      return 0
    fi
    if [ "$ASI_NO_INIT" = "1" ]; then
      say "${fail_prefix} no repo (ASI_NO_INIT=1) — skipping"
      return 0
    fi
    # Bootstrap deliberately, and say so loudly. Skipping instead would recreate
    # the exact silent gap this script exists to close: a new scope would sit
    # unversioned indefinitely while the unit reported success every day.
    if ! git init -q -b "$ASI_BRANCH" "$scope"; then
      git_failed "$fail_prefix" init 1
      return 1
    fi
    say "${fail_prefix} initialised a new repository (branch ${ASI_BRANCH}, no remote)"
  fi

  # A timer must never block on a GPG passphrase prompt. Pin per-repo so a future
  # global commit.gpgsign=true cannot wedge the unit.
  if ! git -C "$scope" config commit.gpgsign false; then
    echo "${PROG}: ${fail_prefix} could not set commit.gpgsign" >&2
    return 1
  fi

  # Supply an identity ONLY if git cannot already resolve one (MEASURED: `git var
  # GIT_COMMITTER_IDENT` exits 128 with "Committer identity unknown" when unset).
  # A systemd unit cannot answer git's "please tell me who you are".
  if ! git -C "$scope" var GIT_COMMITTER_IDENT >/dev/null 2>&1; then
    if ! git -C "$scope" config user.name "$ASI_GIT_NAME" ||
       ! git -C "$scope" config user.email "$ASI_GIT_EMAIL"; then
      echo "${PROG}: ${fail_prefix} could not seed a commit identity" >&2
      return 1
    fi
    say "${fail_prefix} seeded a local commit identity (${ASI_GIT_NAME} <${ASI_GIT_EMAIL}>)"
  fi

  # rc checked SEPARATELY from output: empty output means "clean" only once rc=0
  # has been seen. stdout and stderr are captured SEPARATELY (see `capture`), so
  # a warning printed by a SUCCEEDING status call can never be mistaken for
  # dirty state.
  capture git -C "$scope" status --porcelain
  rc=$?
  before="$CAP_OUT"
  if [ "$rc" -ne 0 ]; then
    git_failed "$fail_prefix" status "$rc" "$CAP_ERR"
    return 1
  fi
  if [ -n "$CAP_ERR" ]; then
    warn "${fail_prefix} the status call succeeded but wrote to stderr (NOT treated as dirty state): ${CAP_ERR}"
  fi

  # 🔴 ENUMERATE BEFORE THE CLEAN-TREE EARLY RETURN, OR THE ALARM IS UNREACHABLE.
  # This used to live only inside commit_once, which a clean tree never reaches.
  # MEASURED on a scope holding a readable `alpha.md` and an unreadable `sub/`
  # containing `hidden.md`:
  #
  #   run 1 (tree dirty)  → alarms, rc=1                      ← the only alarm
  #   run 2 (tree clean)  → "clean — nothing to commit", rc=0
  #   run 3 (tree clean)  → "clean — nothing to commit", rc=0  ← forever
  #
  # and `git ls-files` held `alpha.md` alone while `sub/hidden.md` sat on disk,
  # unversioned, with the unit reporting success every day. One toast, once,
  # months before anyone looks — then permanent silence over live data loss.
  #
  # Running it here makes a permanently-unreadable scope permanently RED. That is
  # deliberate and it is NOT the "permanently-red gate" RULES.md warns about: the
  # content genuinely is unversioned, a human must act, and it clears the instant
  # a `chmod` lands. A gate nobody can clear trains you to ignore it; this one
  # goes green the moment the thing it names is fixed.
  ASI_ENUM_RC=0
  list_candidates "$ASI_CANDFILE" "$scope" 1 || ASI_ENUM_RC=$?

  if [ -z "$before" ]; then
    say "${fail_prefix} clean — nothing to commit"
    enum_verdict "$fail_prefix"; return $?
  fi

  # TWO passes at most. commit_once returns 2 when the tree was re-dirtied
  # during the commit by paths the allowlist DOES cover — a race the design
  # permits and which the next run would fix anyway. Retrying once turns a
  # spurious daily failure into a completed backup; still-racing after two
  # passes is a warning, not a failed unit, because nothing has been lost.
  for attempt in 1 2; do
    commit_once "$scope" "$name" "$fail_prefix" "$before"
    result=$?
    case "$result" in
      0) enum_verdict "$fail_prefix"; return $? ;;
      1) return 1 ;;
    esac

    capture git -C "$scope" status --porcelain
    rc=$?
    if [ "$rc" -ne 0 ]; then
      git_failed "$fail_prefix" "status (re-checking after a racing write)" "$rc" "$CAP_ERR"
      return 1
    fi
    before="$CAP_OUT"
    if [ -z "$before" ]; then
      enum_verdict "$fail_prefix"; return $?
    fi
    if [ "$attempt" -eq 1 ]; then
      say "${fail_prefix} the tree was re-dirtied during the commit by path(s) the *.md allowlist DOES cover — committing them in a second pass"
    fi
  done

  # 🔴 EXIT 0 HERE IS A DELIBERATE JUDGEMENT CALL, SO IT IS PINNED.
  # Nothing has been lost — every remaining path is covered and the next run
  # commits it — so failing the unit would produce a critical toast for a
  # non-event. But an unpinned judgement call is one refactor from silently
  # inverting, and both mutants of this branch (returning 1 instead, and changing
  # the two-pass cadence) survived the previous round's suite.
  #
  # ASI-RACE-UNSETTLED is a GREPPABLE MARKER, not decoration. Exit 0 means a
  # chronically-racing scope is invisible in `systemctl status` forever; the
  # marker is what makes `journalctl --user -u analyze-service-index-commit |
  # grep ASI-RACE-UNSETTLED` answer "is this happening every day?". A scope that
  # never settles is a real problem — it just is not a DATA-LOSS problem, which
  # is what this unit's failure signal is reserved for.
  warn "${fail_prefix} ASI-RACE-UNSETTLED still dirty after two passes, but every remaining path is covered by the *.md allowlist or already tracked — a write is landing faster than this run completes. Nothing is lost; the next run commits it. Not failing the unit for a self-healing race."
  enum_verdict "$fail_prefix"; return $?
}

# --- Walk every scope ----------------------------------------------------------
warn_about_store_root_files

failed=()
seen=0

# 🔴 ENUMERATE BOTH WALKS FIRST, RECORDING EACH rc — THEN PROCESS, THEN JUDGE.
# Not "enumerate, and bail if the rc is bad": that would recreate #372's refusal
# one level up, throwing away every scope that WAS listed because one entry of
# the store root could not be read. Protect first, then alarm.
list_scope_symlinks "$ASI_SYMFILE" || ASI_SYMLINK_ENUM_RC=$?
list_scopes "$ASI_SCOPEFILE" || ASI_SCOPE_ENUM_RC=$?

# 🔴 A SYMLINK WHERE A SCOPE SHOULD BE IS A NAMED FAILURE — FIRST, AND ALWAYS.
# It runs before the directory walk and counts towards `seen` on purpose: a
# store holding ONLY a symlinked scope would otherwise fall through to the
# `seen -eq 0` branch and exit 0 with "no scope directories — nothing to do",
# which is the silent success this whole unit exists to eliminate. Refusing is
# the decision (see list_scopes); refusing QUIETLY is not on the menu.
while IFS= read -r -d '' name; do
  [ -n "$name" ] || continue
  seen=$((seen + 1))
  echo "${PROG}: scope ${name}: is a SYMLINK, and symlinked scopes are NOT supported — its content is NOT being versioned.
  Under this unit's sandbox (ProtectHome=tmpfs) the target does not exist inside
  the namespace, so the link dangles and the scope silently disappears from
  enumeration: MEASURED as \"ok — N scope(s) processed\", exit 0, and no commit
  ever made. Failing here is the only honest signal.
  Fix it by moving the real directory into ${STORE}/${name} — do not re-add \`-L\`
  to the scope walk, which restores the silent version of this bug." >&2
  failed+=("$name")
done < "$ASI_SYMFILE"

while IFS= read -r -d '' name; do
  [ -n "$name" ] || continue
  seen=$((seen + 1))
  if ! commit_scope "${STORE}/${name}" "$name"; then
    failed+=("$name")
  fi
done < "$ASI_SCOPEFILE"

# 🔴 THE CLEAN ZERO IS GATED ON THE WALK HAVING SUCCEEDED — THAT IS THE FIX.
# "no scope directories — nothing to do", rc=0, was ALSO what an unreadable
# store root produced (MEASURED 2026-08-10, store at mode 0300 over a real scope
# holding svc.md): a reassuring zero from a check that could not see anything.
# So the verdict is consulted BEFORE that sentence may be printed, and an
# incomplete walk exits non-zero whether or not any scope was reachable.
enum_incomplete=0
store_enum_verdict || enum_incomplete=1

if [ "$seen" -eq 0 ] && [ "$enum_incomplete" -eq 0 ]; then
  say "no scope directories under ${STORE} — nothing to do"
  exit 0
fi

if [ "${#failed[@]}" -gt 0 ]; then
  die "${#failed[@]} of ${seen} scope(s) FAILED: ${failed[*]}
  The other scopes were still processed. Read the messages above — this unit
  fails rather than reporting a success it did not achieve."
fi

# The scopes that WERE reachable are done and their content is safe; the run
# still fails, because "ok — N scope(s) processed" over a store whose scope list
# is a partial view is a success this run did not achieve.
if [ "$enum_incomplete" -ne 0 ]; then
  exit 1
fi

say "ok — ${seen} scope(s) processed"
