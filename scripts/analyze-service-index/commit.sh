#!/usr/bin/env bash
# Commit any dirty state in the /analyze-service index store — one repo per SCOPE.
#
# WHY THIS EXISTS
# ---------------
# `~/.claude/analyze-service-index/` is the write-back store of the
# `/analyze-service` slash command (claude/commands/analyze-service.md). It holds
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
# claude/skills/close-the-loop/STATE.md, where opt-in prose steps did not stick
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
# reads a remote from config or environment. Every git subcommand below is local:
# init, config, status, add, commit, rev-parse, ls-files, diff. If you are editing
# this file and reaching for `git push`, stop — see the scope README, and devrc
# commit 60e6d9d, which exists because this class of data had to be scrubbed
# retroactively out of a public repo.
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
#
# Environment:
#   ASI_NO_INIT=1   do not bootstrap a repo for a scope that lacks one; skip it.
#   ASI_BRANCH      branch name for a scope this script initialises (default trunk)
#   ASI_GIT_NAME / ASI_GIT_EMAIL   identity used ONLY when git cannot resolve one
#
# Exit codes: 0 = every scope committed or already clean. 1 = at least one scope
# failed; read the message. It is deliberately never "quietly 0" on an error path.
set -uo pipefail

PROG="analyze-service-index-commit"

say()  { echo "${PROG}: $*"; }
warn() { echo "${PROG}: WARNING: $*" >&2; }
die()  { echo "${PROG}: $*" >&2; exit 1; }

PRINT_PLAN=0
if [ "${1:-}" = "--print-plan" ]; then
  PRINT_PLAN=1
  shift
fi

STORE="${1:-${HOME}/.claude/analyze-service-index}"

ASI_GIT_NAME="${ASI_GIT_NAME:-analyze-service index}"
ASI_GIT_EMAIL="${ASI_GIT_EMAIL:-analyze-service-index@localhost}"
ASI_BRANCH="${ASI_BRANCH:-trunk}"
ASI_NO_INIT="${ASI_NO_INIT:-0}"

command -v git >/dev/null 2>&1 ||
  die "git is not on PATH — refusing to report success"

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
list_scopes() {
  find "$STORE" -mindepth 1 -maxdepth 1 -type d -not -name '.*' -printf '%f\n' | sort
}

# Every *.md on disk in a scope (relative to it), plus everything already tracked.
list_candidates() {
  local scope="$1" is_repo="$2"
  find "$scope" -type f -name '*.md' -not -path "${scope}/.git/*" -printf '%P\n'
  if [ "$is_repo" -eq 1 ]; then
    git -C "$scope" ls-files
  fi
}

# Is this scope its OWN repo? `git rev-parse` walks UP the tree, so if the store
# root or $HOME ever became a repo, every command would silently operate on THAT
# repo instead — committing client-sensitive content into somebody else's
# history. Compare the discovered toplevel against the scope and refuse on a
# mismatch. Echoes: 1 = own repo, 0 = no repo, 2 = inside a DIFFERENT repo.
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
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    scopes_found=1
    scope="${STORE}/${name}"
    st="$(scope_repo_state "$scope")"
    case "$st" in
      1) desc="repo" ;;
      0) desc="$([ "$ASI_NO_INIT" = "1" ] && echo "no repo — would SKIP (ASI_NO_INIT=1)" \
                                          || echo "no repo — would git init -b ${ASI_BRANCH}")" ;;
      2) desc="INSIDE ANOTHER REPO — would refuse" ;;
    esac
    echo "scope:   ${name}  (${desc})"
    list_candidates "$scope" "$([ "$st" = "1" ] && echo 1 || echo 0)" \
      | sort -u | sed 's/^/    /'
  done < <(list_scopes)
  [ "$scopes_found" -eq 1 ] || echo "scope:   (none found under ${STORE})"
  exit 0
fi

# --- Commit one scope ----------------------------------------------------------
# Returns 0 on success (committed or already clean), 1 on failure. Deliberately
# does NOT exit: one bad scope must not stop the others being backed up.
commit_scope() {
  local scope="$1" name="$2"
  local fail_prefix="scope ${name}:"
  local st rc before add_out staged_rc n_changed msg commit_out sha after

  st="$(scope_repo_state "$scope")"

  if [ "$st" = "2" ]; then
    echo "${PROG}: ${fail_prefix} not its own repo — it sits inside $(git -C "$scope" rev-parse --show-toplevel 2>/dev/null). Refusing to commit client-sensitive content into a repository that is not the index scope. Fix the nesting first." >&2
    return 1
  fi

  if [ "$st" = "0" ]; then
    # Nothing to version yet? Then there is nothing to bootstrap either.
    if ! find "$scope" -type f -name '*.md' -print -quit | grep -q .; then
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
      echo "${PROG}: ${fail_prefix} git init failed" >&2
      return 1
    fi
    say "${fail_prefix} initialised a new git repository (branch ${ASI_BRANCH}, no remote)"
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
      echo "${PROG}: ${fail_prefix} could not seed a git identity" >&2
      return 1
    fi
    say "${fail_prefix} seeded a local git identity (${ASI_GIT_NAME} <${ASI_GIT_EMAIL}>)"
  fi

  # rc checked SEPARATELY from output: empty output means "clean" only once rc=0
  # has been seen.
  before="$(git -C "$scope" status --porcelain 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} git status failed (rc=${rc}): ${before}" >&2
    return 1
  fi

  if [ -z "$before" ]; then
    say "${fail_prefix} clean — nothing to commit"
    return 0
  fi

  local paths
  mapfile -t paths < <(list_candidates "$scope" 1 | sort -u)
  if [ "${#paths[@]}" -eq 0 ]; then
    echo "${PROG}: ${fail_prefix} tree is dirty but no candidate paths were enumerated. Dirty state:
${before}" >&2
    return 1
  fi

  add_out="$(git -C "$scope" add -- "${paths[@]}" 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} git add failed (rc=${rc}): ${add_out}" >&2
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
    echo "${PROG}: ${fail_prefix} git diff --cached failed (rc=${staged_rc})" >&2
    return 1
  fi

  n_changed="$(printf '%s\n' "$before" | grep -c .)"
  msg="autocommit: ${n_changed} change(s) in the ${name} analyze-service index

Committed by ${PROG} (systemd --user timer). Working-tree state at the time:

${before}

Staged as explicit pathspecs (never -A/--all/.), then the tree was asserted
clean. See scripts/analyze-service-index/commit.sh in devrc."

  commit_out="$(git -C "$scope" commit -q -m "$msg" 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} git commit failed (rc=${rc}): ${commit_out}" >&2
    return 1
  fi

  sha="$(git -C "$scope" rev-parse --short HEAD 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} could not read HEAD after committing: ${sha}" >&2
    return 1
  fi

  # 🔴 The assertion that makes the allowlist safe.
  after="$(git -C "$scope" status --porcelain 2>&1)"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} git status failed AFTER committing (rc=${rc}): ${after}" >&2
    return 1
  fi
  if [ -n "$after" ]; then
    echo "${PROG}: ${fail_prefix} committed ${sha}, but the tree is STILL DIRTY afterwards:
${after}
  Something is not covered by the *.md allowlist. This is loud on purpose —
  decide deliberately whether it belongs in the index." >&2
    return 1
  fi

  say "${fail_prefix} committed ${sha} — ${n_changed} change(s)"
  return 0
}

# --- Walk every scope ----------------------------------------------------------
warn_about_store_root_files

failed=()
seen=0
while IFS= read -r name; do
  [ -n "$name" ] || continue
  seen=$((seen + 1))
  if ! commit_scope "${STORE}/${name}" "$name"; then
    failed+=("$name")
  fi
done < <(list_scopes)

if [ "$seen" -eq 0 ]; then
  say "no scope directories under ${STORE} — nothing to do"
  exit 0
fi

if [ "${#failed[@]}" -gt 0 ]; then
  die "${#failed[@]} of ${seen} scope(s) FAILED: ${failed[*]}
  The other scopes were still processed. Read the messages above — this unit
  fails rather than reporting a success it did not achieve."
fi

say "ok — ${seen} scope(s) processed"
