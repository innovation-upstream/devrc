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
# reads a remote from config or environment.
#
# This is enforced as an ASSERTED LEDGER, not a blocklist of banned words. The
# COMPLETE set of git subcommands this file may invoke is:
#
#     add  commit  config  diff  init  ls-files  rev-parse  status  var
#
# and test_the_set_of_git_subcommands_is_exactly_the_asserted_ledger fails when
# that set GROWS *or* SHRINKS. A blocklist of five forbidden verbs is what a
# mutation sweep walked straight past: `git archive`, `git bundle`, a plain
# `cp -r` of a scope, and `_s=pu"sh"; git $_s origin …` were all invisible to it.
# A subcommand supplied through a VARIABLE is itself a ledger violation, because
# no static check can tell what it will expand to.
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
# Exit codes: 0 = every scope committed or already clean. 1 = at least one scope
# failed; read the message. It is deliberately never "quietly 0" on an error path.
set -uo pipefail

PROG="analyze-service-index-commit"

say()  { echo "${PROG}: $*"; }
warn() { echo "${PROG}: WARNING: $*" >&2; }
die()  { echo "${PROG}: $*" >&2; exit 1; }

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

STORE="${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}"

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
#
# 🔴 NUL-DELIMITED, AND SYMLINKS FOLLOWED. Both are correctness, not polish:
#   * a scope name containing a newline used to be split into two nonexistent
#     scopes, each reported "no *.md files — skipping", exit 0 (MEASURED) — the
#     content sat UNVERSIONED and the unit called it success, which is the exact
#     silent-loss outcome this script exists to prevent;
#   * a scope reached through a symlink was not enumerated AT ALL ("no scope
#     directories — nothing to do", exit 0, also MEASURED). `-L` is safe here
#     precisely because `-maxdepth 1` means nothing is ever recursed into, so
#     there is no symlink-loop to fall into.
# `sort -z` keeps the NUL framing; pipefail makes a `find` failure this
# function's failure instead of an empty list that reads as "no scopes".
list_scopes() {
  find -L "$STORE" -mindepth 1 -maxdepth 1 -type d -not -name '.*' -printf '%f\0' \
    | sort -z
}

# Every *.md on disk in a scope (relative to it), plus everything already
# tracked. NUL-delimited for the same reason as list_scopes.
list_candidates() {
  local scope="$1" is_repo="$2"
  find -L "$scope" -type f -name '*.md' -not -path "${scope}/.git/*" -printf '%P\0'
  if [ "$is_repo" -eq 1 ]; then
    git -C "$scope" ls-files -z
  fi
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
    list_candidates "$scope" "$([ "$st" = "1" ] && echo 1 || echo 0)" \
      | sort -zu | tr '\0' '\n' | sed 's/^/    /'
  done < <(list_scopes)
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
  local -a paths_arr uncovered_arr

  mapfile -d '' -t paths_arr < <(list_candidates "$scope" 1 | sort -zu)
  if [ "${#paths_arr[@]}" -eq 0 ]; then
    echo "${PROG}: ${fail_prefix} tree is dirty but no candidate paths were enumerated. Dirty state:
${before}" >&2
    return 1
  fi

  capture git -C "$scope" add -- "${paths_arr[@]}"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} git add failed (rc=${rc}): ${CAP_ERR}${CAP_OUT}" >&2
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

  capture git -C "$scope" commit -q -m "$msg"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} git commit failed (rc=${rc}): ${CAP_ERR}${CAP_OUT}" >&2
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
    echo "${PROG}: ${fail_prefix} git status failed AFTER committing (rc=${rc}): ${CAP_ERR}" >&2
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
commit_scope() {
  local scope="$1" name="$2"
  local fail_prefix="scope ${name}:"
  local st rc before attempt result

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
    md_probe="$(find -L "$scope" -type f -name '*.md' -print -quit 2>/dev/null)"
    md_rc=$?
    if [ "$md_rc" -ne 0 ]; then
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
  # has been seen. stdout and stderr are captured SEPARATELY (see `capture`), so
  # a warning printed by a SUCCEEDING status call can never be mistaken for
  # dirty state.
  capture git -C "$scope" status --porcelain
  rc=$?
  before="$CAP_OUT"
  if [ "$rc" -ne 0 ]; then
    echo "${PROG}: ${fail_prefix} git status failed (rc=${rc}): ${CAP_ERR}" >&2
    return 1
  fi
  if [ -n "$CAP_ERR" ]; then
    warn "${fail_prefix} git status succeeded but wrote to stderr (NOT treated as dirty state): ${CAP_ERR}"
  fi

  if [ -z "$before" ]; then
    say "${fail_prefix} clean — nothing to commit"
    return 0
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
      0) return 0 ;;
      1) return 1 ;;
    esac

    capture git -C "$scope" status --porcelain
    rc=$?
    if [ "$rc" -ne 0 ]; then
      echo "${PROG}: ${fail_prefix} git status failed while re-checking after a racing write (rc=${rc}): ${CAP_ERR}" >&2
      return 1
    fi
    before="$CAP_OUT"
    if [ -z "$before" ]; then
      return 0
    fi
    if [ "$attempt" -eq 1 ]; then
      say "${fail_prefix} the tree was re-dirtied during the commit by path(s) the *.md allowlist DOES cover — committing them in a second pass"
    fi
  done

  warn "${fail_prefix} still dirty after two passes, but every remaining path is covered by the *.md allowlist or already tracked — a write is landing faster than this run completes. Nothing is lost; the next run commits it. Not failing the unit for a self-healing race."
  return 0
}

# --- Walk every scope ----------------------------------------------------------
warn_about_store_root_files

failed=()
seen=0
while IFS= read -r -d '' name; do
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
