#!/usr/bin/env bash
# SessionStart: keep the context-loading files in THIS clone fresh, and report
# anything that could not be refreshed.
#
# Why this exists: when all work happens in throwaway worktrees + PRs, the base
# clone is never written to and silently falls behind. That is harmless for
# WRITES -- `git worktree add <path> origin/<branch>` resolves the remote-tracking
# ref, so a clone 700 commits behind still produces a current worktree -- but it
# is dangerous for READS: CLAUDE.md and .claude/skills/** load into the agent's
# context FROM THE WORKING TREE, so a stale clone serves stale,
# authoritative-looking instructions with nothing to indicate it.
#
# What it does NOT do: move HEAD. `merge --ff-only` would need a clean tree and
# would move the branch; refreshing specific paths needs neither and cannot
# conflict. It also never overwrites a path you have locally modified -- those are
# reported and skipped.
#
# Side effect, by design: a refreshed path shows as modified-vs-HEAD in
# `git status` until the clone's branch catches up. That is inherent to fixing
# reads without moving HEAD; the report below names every path it touched so the
# dirt has a documented cause.
#
# Opt out of the write half with BASE_CLONE_NO_REFRESH=1 (report only).

set -uo pipefail

# Paths whose CONTENT is loaded into agent context from the working tree.
REFRESH_PATHS=(CLAUDE.md .claude/skills)

# Guard the VALUE, not just the cd: an empty ROOT sails past `cd "$ROOT" || exit`
# on bash <= 5.2, where `cd ""` is a no-op returning 0.
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$ROOT" ] && [ -d "$ROOT" ] || exit 0

# Only the primary clone can go stale in the way that matters. In a linked
# worktree .git is a FILE holding "gitdir: ..."; in the primary clone it is a dir.
[ -d "$ROOT/.git" ] || exit 0

# Prefer the checked-out branch's own upstream; fall back to origin's default HEAD.
UP="$(git -C "$ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
if [ -z "$UP" ]; then
  UP="$(git -C "$ROOT" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
fi
[ -n "$UP" ] || exit 0

# Bounded: a hung network must not wedge session start.
timeout 12 git -C "$ROOT" fetch --quiet "${UP%%/*}" "${UP#*/}" >/dev/null 2>&1 || true

BEHIND="$(git -C "$ROOT" rev-list --count "HEAD..$UP" 2>/dev/null || echo 0)"
[ -n "$BEHIND" ] || BEHIND=0

# List DIFFERING PATHS, never branch on an exit code: `git diff --quiet <ref> -- <p>`
# exits 0 when <p> exists on NEITHER side, which reads as a reassuring "same" for a
# file that is simply absent. --name-only reports content, so an absent file is
# just not listed.
DIFFERING="$(git -C "$ROOT" diff --name-only "$UP" -- "${REFRESH_PATHS[@]}" 2>/dev/null || true)"

refreshed=(); skipped=(); optout=(); failed=(); approved=()

if [ -n "$DIFFERING" ]; then
  while IFS= read -r p; do
    [ -n "$p" ] || continue

    # NEVER clobber real local work. The right question is not "does this differ
    # from HEAD" but "is this content RECOVERABLE" -- i.e. does the working copy's
    # exact blob already exist in the upstream branch's history for this path? If
    # it does, it is either HEAD's own content (never touched) or the output of an
    # earlier refresh, so overwriting destroys nothing. If the content appears
    # NOWHERE in that history it is unique human work and must be protected.
    #
    # 🔴 The obvious guard -- "differs from HEAD => local edit" -- is WRONG, and it
    # made this hook SELF-BLOCKING: `git checkout <ref> -- <path>` writes the index
    # too, so every path the hook fixed then differed from HEAD forever after and
    # was skipped on every subsequent run. The feature worked exactly once per file
    # and then silently stopped. Measured 2026-08-21, and invisible to an
    # idempotence test that re-runs without upstream moving in between -- the
    # second refresh only breaks once the file goes stale AGAIN.
    known=no
    if [ ! -e "$ROOT/$p" ]; then
      # NEW UPSTREAM: the path does not exist here at all, so there is no local
      # content to protect and the checkout below CREATES it.
      # 🔴 This case used to fall through to hash-object, which FATALS on a missing
      # file; the empty result was then classified FAILED. Net effect: a stale
      # clone never received a newly-added skill, and the report called it an
      # error rather than "this skill is missing here". Measured 2026-08-21 —
      # the whole FAILED bucket was exactly the files `git diff --name-status
      # HEAD origin/trunk` marks `A`.
      known=yes
      cur=""
    elif ! cur="$(git -C "$ROOT" hash-object "$p" 2>/dev/null)" || [ -z "$cur" ]; then
      failed+=("$p")
      continue
    elif [ "$cur" = "$(git -C "$ROOT" rev-parse "HEAD:$p" 2>/dev/null || echo none)" ]; then
      known=yes
    else
      while IFS= read -r c; do
        [ -n "$c" ] || continue
        if [ "$cur" = "$(git -C "$ROOT" rev-parse "$c:$p" 2>/dev/null || echo none)" ]; then
          known=yes
          break
        fi
      done < <(git -C "$ROOT" rev-list -n 100 "$UP" -- "$p" 2>/dev/null)
    fi
    if [ "$known" = no ]; then
      skipped+=("$p")
      continue
    fi

    if [ "${BASE_CLONE_NO_REFRESH:-0}" = "1" ]; then
      optout+=("$p")
      continue
    fi

    approved+=("$p")
  done <<<"$DIFFERING"
fi

# ONE checkout for the whole approved batch — one .git/index.lock acquisition per
# run instead of one per file.
#
# 🔴 Per-file checkout made this hook hostile to itself under concurrency. Two
# sessions starting together each took the lock ~25 times, so they collided
# repeatedly and reported FAILED 12 / FAILED 12 — and an immediate per-file retry
# barely moved it (7/6), because the contention is SUSTAINED for the length of
# both runs, not a narrow window. Measured 2026-08-21. Batching collapses the
# collision surface to a single acquisition; the retry then covers the rare
# genuine overlap.
if [ "${#approved[@]}" -gt 0 ]; then
  if git -C "$ROOT" checkout "$UP" -- "${approved[@]}" >/dev/null 2>&1 \
     || git -C "$ROOT" checkout "$UP" -- "${approved[@]}" >/dev/null 2>&1; then
    refreshed=("${approved[@]}")
  else
    # Batch failed: fall back to per-path so ONE bad path stays attributable
    # instead of condemning the whole set.
    for p in "${approved[@]}"; do
      if git -C "$ROOT" checkout "$UP" -- "$p" >/dev/null 2>&1 \
         || git -C "$ROOT" checkout "$UP" -- "$p" >/dev/null 2>&1; then
        refreshed+=("$p")
      else
        failed+=("$p")
      fi
    done
  fi
fi

n_ref=${#refreshed[@]}; n_skip=${#skipped[@]}; n_opt=${#optout[@]}; n_fail=${#failed[@]}

# Nothing to say when the clone is current. A banner that fires every time gets
# ignored, which is the failure mode this hook exists to prevent.
if [ "$BEHIND" -eq 0 ] && [ "$n_ref" -eq 0 ] && [ "$n_skip" -eq 0 ] && [ "$n_opt" -eq 0 ] && [ "$n_fail" -eq 0 ]; then
  exit 0
fi

repo="$(basename "$ROOT")"
msg="$repo is $BEHIND commit(s) behind $UP"
[ "$n_ref" -gt 0 ] && msg="$msg | refreshed $n_ref context file(s)"
[ "$n_skip" -gt 0 ] && msg="$msg | SKIPPED $n_skip (local edits)"
[ "$n_opt" -gt 0 ] && msg="$msg | STALE $n_opt (refresh opted out)"
[ "$n_fail" -gt 0 ] && msg="$msg | FAILED $n_fail"

ctx="Base-clone sync (working tree vs $UP):
- behind by $BEHIND commit(s); HEAD was NOT moved."

if [ "$n_ref" -gt 0 ]; then
  ctx="$ctx
- REFRESHED from $UP (these now match upstream, and will show as modified-vs-HEAD
  in git status -- that is this hook's doing, not stray WIP):
$(printf '    %s\n' "${refreshed[@]}")"
fi
if [ "$n_skip" -gt 0 ]; then
  ctx="$ctx
- STALE AND NOT REFRESHED: the working copy holds content found nowhere in the
  recent history of $UP, so it is treated as unique local work and left alone.
  What you read from these may be out of date:
$(printf '    %s\n' "${skipped[@]}")"
fi
if [ "$n_opt" -gt 0 ]; then
  ctx="$ctx
- STALE, not refreshed because BASE_CLONE_NO_REFRESH=1 is set (report-only mode).
  What you read from these may be out of date:
$(printf '    %s\n' "${optout[@]}")"
fi
if [ "$n_fail" -gt 0 ]; then
  ctx="$ctx
- FAILED to refresh even after a retry. The usual cause is another session
  starting at the same moment and holding .git/index.lock, which is transient and
  self-heals on the next session; a path that keeps failing is a real problem:
$(printf '    %s\n' "${failed[@]}")"
fi

ctx="$ctx

Only CLAUDE.md and .claude/skills/** are synced; the rest of this tree is still
$BEHIND commit(s) behind. Before relying on any OTHER doc claim that is
load-bearing in a plan -- a limit, a retention figure, an arming state, a 'this is
impossible' -- read it from the ref: git show $UP:<path>
Worktrees are unaffected: they are created from $UP directly."

jq -nc --arg m "$msg" --arg c "$ctx" \
  '{systemMessage: $m, hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $c}}'
