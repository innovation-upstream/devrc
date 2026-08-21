#!/usr/bin/env bash
# resume-state.sh — INITIATIVE-scoped, on-demand live-state reconciler for /resume.
#
# Given one handoff doc (an initiative), it reconciles the doc's claims against
# FRESH live state and emits a compact digest: GIT/PR, WORKLOAD, ALERTS, DRIFT.
# On-demand (never cached — resume must see reality, not a stale snapshot),
# scoped to just this initiative's slice (standup.sh already covers the fleet).
#
# Modeled on standup.sh: bash (sidesteps the non-interactive-zsh gotchas),
# reduces every query at the source (only the digest reaches stdout), and
# degrades SILENTLY when a source is unreachable/absent rather than faking state.
#
# Usage: resume-state.sh [topic-slug | path/to/handoff.md]
#   no arg      -> newest claudedocs/handoff-*.md in the repo of $PWD,
#                  else newest claudedocs/*HANDOFF*.md (SESSION-HANDOFF.md &c.)
#   slug        -> claudedocs/handoff-<slug>*.md in the repo of $PWD,
#                  else the no-arg chain above
#   handoff path-> that file; the target repo is derived FROM the path
#
# v1 workload/alerts target datapacket (prod-kubeconfig at <repo>/prod-kubeconfig);
# everywhere else it degrades to the (always-run) GIT/PR block.
set -uo pipefail

KT="--request-timeout=8s"
# alertnames that are known/expected noise (mirrors standup.sh) — dropped from criticals
NOISE_RE='TargetDown|KubeHpaMaxedOut'

have(){ command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# Extraction heuristics — kept as pure, side-effect-free functions so the test
# harness can source this file and assert them on fixture text.  Each reads its
# subject text as $1 and prints one result per line (deduped, sorted).
# ---------------------------------------------------------------------------

# PR references, ATTRIBUTED TO A REPO. Emits one `<slug>\t<number>` line per
# reference; `-` in the slug field means the reference names no repo.
#
# 🔴 THIS USED TO EMIT BARE NUMBERS AND THE CALLER RESOLVED THEM ALL AGAINST THE
# LOCAL REPO. `grep -oE '#[0-9]{2,}'` cannot see a qualifier, so the `#247` in
# `civitai/civitai-app-starters#247` arrived indistinguishable from a bare
# `#247` and was looked up in whatever repo happened to be cwd. Measured
# 2026-08-20 resuming a datapacket-talos handoff that references
# `civitai/cli#423`, `civitai/civitai#4158`, `civitai/civitai-app-starters#247`
# and `civitai/civitai-orchestration#311`: the DRIFT block emitted **18 lines**
# of `PR #NNN MERGED but handoff frames it as open/in-flight (do the follow-on)`,
# every one of them talos-infra's own unrelated PR of that number.
#
# That is not noise, it is FABRICATION — a confident instruction to go do a
# follow-on that does not exist, which is strictly worse than the silence it
# replaced. Same disease as the phantom-branch case documented on
# extract_branches, one surface over.
#
# Three shapes, in precedence order:
#   (a) `owner/repo#N`  -> owner/repo. Any digit count: a qualifier IS the
#       statement of intent, so the >=2-digit prose filter has nothing to do.
#   (b) a github `.../pull/N` URL -> the owner/repo in the URL.
#   (c) a bare `#N` (>=2 digits) -> `-`, i.e. UNATTRIBUTED. The caller decides
#       whether this repo can claim it; this function must not guess.
#
# (c) runs over text with every (a)/(b) form DELETED, so a qualified ref's
# number can never re-enter as a bare one — that deletion is the actual fix.
#
# A slug whose repo half carries a file extension is dropped: `claudedocs/x.md#12`
# is a line anchor, not a PR. Same asymmetry extract_branches trades on — the
# omission costs one unchecked ref, the alternative asserts a fact about a repo
# that does not exist. Cost: a real repo named `owner/thing.com` is unreachable.
extract_pr_refs(){
  { printf '%s\n' "$1" \
      | grep -oE '(^|[^A-Za-z0-9._/#-])[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#[0-9]+' \
      | sed -E 's/^[^A-Za-z0-9]+//; s/#/\t/'
    printf '%s\n' "$1" \
      | grep -oE 'github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/pull/[0-9]+' \
      | sed -E 's%^github\.com/%%; s%/pull/%\t%'
    printf '%s\n' "$1" \
      | sed -E 's%https?://[^[:space:]]*%%g; s%[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#[0-9]+%%g' \
      | grep -oE '#[0-9]{2,}' | tr -d '#' | sed -E 's/^/-\t/'
  } 2>/dev/null \
    | grep -vE '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]*\.[A-Za-z]{1,6}	' \
    | sort -u
}

# Every referenced PR NUMBER, repo-blind. Kept because two call sites only need
# the count, and delegating keeps ONE extraction to reason about (and to break).
extract_prs(){ extract_pr_refs "$1" | cut -f2 | sort -un; }

# branch tokens: the conventional prefixes only (zach/ feat/ fix/ docs/ chore/),
# starting at a word boundary so "notafix/x" doesn't match; trailing sentence
# punctuation is stripped.
#
# 🔴 THESE PREFIXES ARE ALSO ORDINARY DIRECTORY NAMES, so a handoff that merely
# QUOTES A PATH used to mint a phantom branch — and the branch loop then printed
# "referenced by handoff no longer exists (merged & pruned?)", i.e. a fabricated
# fact, which is worse than the silence it replaced. Both shapes were measured
# live, one in each repo the SESSION-HANDOFF fallback newly reaches:
#
#   civitai-manager  `docs/configuration.md`            -> a real 15 KB FILE
#   naida-ai         /home/zach/workspace/scratch/…     -> yielded zach/workspace/…
#
# Two string filters, and one repo-aware check in the branch loop below:
#
#  1. The leading boundary excludes `/`. `\b` matched after a slash, so ANY
#     absolute path containing /zach/ or /docs/ produced a token. The class is
#     otherwise exactly \b's: `.` and `-` still delimit, so `my-fix/x` matches
#     as before and `notafix/x` still does not. `/` is the only change.
#  2. A trailing FILE EXTENSION disqualifies the token — `\.[A-Za-z]{1,6}$`,
#     alphabetic only, so `.md`/`.json`/`.sh`/`.tsx` drop while a version-ish
#     suffix like `fix/v1.2` or `fix/thing.v2` survives.
#
# Failure directions are asymmetric and this trades in the safe one: dropping a
# real branch costs one drift check silently, while inventing one puts a false
# statement in front of someone deciding what to do next.
#
# 🔴 …BUT AN OMISSION IS NOT FREE EITHER, AND FILTER 1 OVERSHOT. Excluding `/`
# also killed every reference that legitimately CARRIES a slash-bearing prefix —
# `origin/fix/x`, `upstream/feat/x`, `refs/heads/fix/x`, and GitHub `/tree/`
# URLs. Measured across 211 real handoff docs: one live casualty,
# `origin/zach/engaged-models-client-store` in datapacket-talos, which is the
# ONLY form that branch appears in and IS genuinely gone — so the old code's
# DRIFT line was right and the new code was silently mute. In a go/no-go tool
# that is the same disease as the fabrication, just the other polarity: it reads
# as "no drift".
#
# So STRIP the ref-ish prefix first, then match. Each stripped form is a strong
# POSITIVE signal that what follows is a ref, which is exactly what a bare
# filesystem path lacks — `/home/zach/workspace/…` still yields nothing.
#
# ⚠ `/compare/` is deliberately NOT stripped, though an earlier revision did.
# It was redundant (a compare URL's `main...feat/x` already matches, because the
# `.` satisfies the boundary) AND unpinned (deleting it left all 55 tests
# green) AND strictly worse on a compare whose LEFT side carries a slash:
# stripping turned `…/compare/zach/a...zach/b` into the junk token
# `zach/a...zach/b`, where not stripping yields `zach/b` — the head of the
# compare, which is the ref a reader means. Untested defensive code that makes
# one real case worse is not defence.
#
# ⚠ The `origin|upstream` rule's leading bound is LOAD-BEARING, but not for the
# case you would guess. `/home/zach/repos/origin/fix/x` is NOT the reason:
# strip `origin/` there and `fix` is still preceded by `/`, which the grep
# boundary rejects anyway — both spellings yield nothing. The bound earns its
# place on a remote-like segment glued to a WORD inside a path. Measured with
# the bound loosened to `s#(origin|upstream)/##g`:
#
#   /home/zach/repos/origin/fix/x  -> []        (identical — cannot discriminate)
#   /var/log/my-origin/fix/x       -> [fix/x]   <- FABRICATED out of a path
#   .origin/fix/x                  -> [fix/x]
#
# Accepted cost: a genuinely remote-qualified `my-origin/fix/x` yields nothing.
# That is an omission, and fabrication is the worse direction, so the bound
# stays. (An earlier revision of this comment named the `/origin/` case, which
# measurement showed proves nothing.)
extract_branches(){
  printf '%s\n' "$1" \
    | sed -E '
        s#https?://[^[:space:]]*/tree/# #g
        s#refs/(heads|remotes)/# #g
        s#(^|[^A-Za-z0-9._/-])(origin|upstream)/#\1#g
      ' \
    | grep -oE '(^|[^A-Za-z0-9_/])(zach|feat|fix|docs|chore)/[A-Za-z0-9._/-]+' 2>/dev/null \
    | sed -E 's/^[^A-Za-z]+//; s/[.,;:)]+$//' \
    | grep -vE '\.[A-Za-z]{1,6}$' \
    | sort -u
}

# candidate workload tokens: [a-z][a-z0-9-]{3,} (len>=4). Deliberately loose —
# they are ONLY ever used by INTERSECTING with REAL k8s deployment names, so any
# junk token (e.g. "trunk", "metrics") that isn't also a deployment harmlessly drops.
extract_tokens(){
  printf '%s\n' "$1" | grep -oE '[a-z][a-z0-9-]{3,}' 2>/dev/null | sort -u
}

# Does the handoff frame PR #<n> as still OPEN / in-flight? The high-value resume
# drift is "the doc says this is open, but it merged/closed while I was away." We
# require an explicit in-flight marker near the ref rather than flagging EVERY
# referenced-and-now-merged PR — real handoffs routinely list already-merged PRs
# (see this repo's rightsizing handoff), so a blanket "merged => drift" is pure noise.
#
# $3 (optional) is the ref's owner/repo. When given we look FIRST at lines
# spelling `owner/repo#N`, because a doc that cites several repos can easily
# carry two different `#423`s and the framing of one says nothing about the
# other. Only if no qualified line exists do we fall back to the repo-blind
# match — which is exactly right for a bare ref, whose whole nature is that the
# doc never qualified it.
handoff_says_inflight(){ # $1=pr number  $2=handoff path  $3=owner/repo (optional)
  [ -f "$2" ] || return 1
  local lines="" q
  if [ -n "${3:-}" ]; then
    q=$(printf '%s' "$3" | sed -E 's/[][\\.^$*+?(){}|]/\\&/g')
    lines=$(grep -iE "$q#$1([^0-9]|$)" "$2" 2>/dev/null)
  fi
  [ -z "$lines" ] && lines=$(grep -iE "#$1([^0-9]|$)" "$2" 2>/dev/null)
  printf '%s\n' "$lines" \
    | grep -qiE 'open|in.?flight|awaiting|pending|not yet merged|to merge|mergeable|review|wip|draft|blocked'
}

# ---------------------------------------------------------------------------
# Resolve target repo + handoff doc.
# ---------------------------------------------------------------------------
REPO="" HANDOFF="" SLUG=""
resolve(){
  local arg="${1:-}"
  if [ -n "$arg" ] && [ -f "$arg" ]; then          # explicit handoff path
    HANDOFF=$(realpath "$arg")
    REPO=$(git -C "$(dirname "$HANDOFF")" rev-parse --show-toplevel 2>/dev/null) \
      || REPO=$(dirname "$HANDOFF")
  else
    REPO=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null) || REPO="$PWD"
    if [ -n "$arg" ]; then                          # topic slug
      HANDOFF=$(ls -t "$REPO"/claudedocs/handoff-"$arg"*.md 2>/dev/null | head -1)
    fi
    [ -z "$HANDOFF" ] && HANDOFF=$(ls -t "$REPO"/claudedocs/handoff-*.md 2>/dev/null | head -1)
    # FALLBACK for repos that name their handoff in caps — civitai-manager uses
    # claudedocs/SESSION-HANDOFF.md, which the lowercase glob above misses, and
    # a missed handoff is not a quiet failure: the DRIFT block below used to
    # print "(none detected — live state matches the handoff's claims)" after
    # reconciling against NOTHING. A false green.
    #
    # Reach, deliberately narrow: basenames under claudedocs/ containing the
    # literal, UPPERCASE substring HANDOFF and ending .md, i.e.
    # SESSION-HANDOFF.md, HANDOFF.md, HANDOFF-2026-08-04.md. claudedocs/ in
    # these repos is mostly design/audit docs (*-DESIGN.md,
    # SECURITY-AUDIT-*.md, LAUNCH-*.md, *-RESEARCH.md) and none of those
    # contains HANDOFF, so none can resolve as one.
    #
    # ⚠ THE ONE THING PROTECTING THE LOWERCASE FAMILY IS THAT THIS IS TRIED
    # LAST. An earlier version of this comment also credited bash's
    # case-sensitive globbing — that reason is inert: this line is only ever
    # reached when the lowercase globs found NOTHING, so there is nothing left
    # to poach whatever the case rules are. If you reorder these three lines,
    # case-sensitivity will not save you.
    #
    # The topic slug gets no fallback of its own on purpose: an unmatched slug
    # already falls through this chain, so `resume-state.sh session` in a repo
    # whose only handoff is SESSION-HANDOFF.md still finds it, and there is
    # nothing for a slug to disambiguate when the family holds one file.
    [ -z "$HANDOFF" ] && HANDOFF=$(ls -t "$REPO"/claudedocs/*HANDOFF*.md 2>/dev/null | head -1)
  fi
  local url
  url=$(git -C "$REPO" remote get-url origin 2>/dev/null) \
    && SLUG=$(printf '%s' "$url" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
}

# ---------------------------------------------------------------------------
# GIT / PR — always runs; the rock-solid core of the digest.
# ---------------------------------------------------------------------------
DRIFT=()         # lines where live state contradicts the handoff
UNRECONCILED=()  # sources that did NOT answer — an empty DRIFT means less when
                 # this is non-empty, and the digest has to say so

# ---------------------------------------------------------------------------
# HANDOFF FRESHNESS — is the working-tree copy the copy we should be reading?
#
# 🔴 A HANDOFF READ OUT OF A SHARED WORKING TREE IS A GUESS ABOUT WHAT IT SAYS.
# Measured 2026-08-20: the datapacket-talos primary clone served a handoff **276
# lines behind `origin/trunk`**, and the whole resume was framed on it. It was
# caught by luck. That repo's own CLAUDE.md records the same class twice more —
# a clone once served a SKILL.md 692 commits stale — because the clone's checked
# out branch is unpredictable and its local refs are routinely far behind.
#
# Prose in the skill cannot fix this: a step that must be remembered is a step
# that gets skipped. So the reconciler does it, every run, and REPORTS WHICH
# COPY IT READ. Sets three globals:
#
#   HANDOFF_TEXT    the authoritative text every later block extracts from
#   HANDOFF_NOTE    the freshness clause printed on the `handoff:` line
#   HANDOFF_ALT     a /tmp path holding the OTHER copy, when they differ
#
# Which copy wins is decided by a fact, not a heuristic: if the working-tree
# file is UNMODIFIED relative to HEAD, then any difference from origin is the
# branch being behind, and origin is authoritative. If it carries uncommitted
# edits, it is this session's work-in-progress and stays authoritative — but
# loudly, because reconciling against unpushed text is its own trap.
HANDOFF_TEXT="" HANDOFF_NOTE="" HANDOFF_ALT=""
handoff_freshness(){
  [ -n "$HANDOFF" ] || return 0
  HANDOFF_TEXT=$(cat "$HANDOFF")
  local d="$REPO"
  git -C "$d" rev-parse --git-dir >/dev/null 2>&1 || {
    HANDOFF_NOTE="working-tree copy — origin freshness UNCHECKED (not a git repo)"; return 0; }
  git -C "$d" remote get-url origin >/dev/null 2>&1 || {
    HANDOFF_NOTE="working-tree copy — origin freshness UNCHECKED (no origin remote)"; return 0; }

  # Bounded, non-interactive, and never a prompt. A fetch that cannot complete
  # must cost seconds, not a hung resume.
  if [ -z "${RESUME_STATE_SKIP_FETCH:-}" ]; then
    GIT_TERMINAL_PROMPT=0 \
    GIT_SSH_COMMAND='ssh -oBatchMode=yes -oConnectTimeout=5 -oStrictHostKeyChecking=accept-new' \
      timeout 25 git -C "$d" fetch --quiet origin >/dev/null 2>&1 \
      || HANDOFF_NOTE="[fetch failed; compared against refs already on disk] "
  else
    HANDOFF_NOTE="[fetch skipped; compared against refs already on disk] "
  fi

  local db c
  db=$(git -C "$d" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)
  db=${db#origin/}
  if [ -z "$db" ]; then
    for c in trunk main master; do
      git -C "$d" rev-parse --verify -q "origin/$c" >/dev/null 2>&1 && { db="$c"; break; }
    done
  fi
  [ -n "$db" ] || { HANDOFF_NOTE="${HANDOFF_NOTE}working-tree copy — origin freshness UNCHECKED (no origin/<default-branch> ref)"; return 0; }

  local rel
  rel=$(git -C "$d" ls-files --full-name --error-unmatch -- "$HANDOFF" 2>/dev/null)
  if [ -z "$rel" ]; then
    HANDOFF_NOTE="${HANDOFF_NOTE}working-tree copy — untracked here, nothing on origin/$db to compare"; return 0
  fi

  # 🔴 PROVE THE PATH EXISTS AT THE REF FIRST. `git diff --quiet <ref> -- <p>`
  # exits 0 when <p> exists on NEITHER side, so used alone it reports a
  # reassuring "matches origin/$db" for a file that has never been on that
  # branch at all. cat-file -e is what makes the comparison mean anything.
  local ref="origin/$db"
  if ! git -C "$d" cat-file -e "$(printf '%s:%s' "$ref" "$rel")" 2>/dev/null; then
    HANDOFF_NOTE="${HANDOFF_NOTE}working-tree copy — not on $ref (local-only/uncommitted doc)"; return 0
  fi

  local rtext ln_local ln_remote
  rtext=$(git -C "$d" show "$(printf '%s:%s' "$ref" "$rel")" 2>/dev/null)
  if [ "$rtext" = "$HANDOFF_TEXT" ]; then
    HANDOFF_NOTE="${HANDOFF_NOTE}working-tree copy (identical to $ref)"; return 0
  fi
  ln_local=$(printf '%s\n' "$HANDOFF_TEXT" | grep -c '')
  ln_remote=$(printf '%s\n' "$rtext" | grep -c '')

  # 🔴 THE FILE WE HAND OVER IS ALWAYS THE $ref TEXT, in BOTH branches,
  # because $ref is the copy the reader cannot otherwise open — the working-tree
  # copy is a path they already have. An earlier revision wrote the LOCAL text
  # here on the stale branch, so the digest said "read this" while pointing at
  # the very stale copy it had just warned about. The label states which text it
  # is; the branches differ only in whether it is the authoritative one.
  local alt; alt=$(mktemp "/tmp/resume-handoff-XXXXXX.md")
  printf '%s\n' "$rtext" > "$alt"
  HANDOFF_ALT="$alt"
  if git -C "$d" diff --quiet -- "$rel" 2>/dev/null; then
    # unmodified vs HEAD => the difference is the BRANCH being behind origin
    HANDOFF_TEXT="$rtext"
    HANDOFF_NOTE="${HANDOFF_NOTE}🔴 $ref copy (the working-tree copy is STALE: ${ln_local} lines local vs ${ln_remote} on $ref)"
    DRIFT+=("handoff doc in the working tree is STALE vs $ref (${ln_local} vs ${ln_remote} lines) — this digest reconciled the $ref copy, readable at $alt; READ THAT ONE, the local file is not what the last session wrote")
  else
    HANDOFF_NOTE="${HANDOFF_NOTE}⚠ working-tree copy, which has UNCOMMITTED edits and differs from $ref (${ln_local} lines local vs ${ln_remote} on $ref)"
    UNRECONCILED+=("handoff doc has uncommitted local edits and differs from $ref (${ln_local} vs ${ln_remote} lines) — reconciled the LOCAL copy; the $ref text is at $alt")
  fi
}

git_pr_block(){
  echo "GIT/PR"
  local d="$REPO"
  # `rev-parse --git-dir`, not `-d "$d/.git"`: in a WORKTREE `.git` is a FILE
  # holding a gitdir: pointer, so the directory test called a perfectly good
  # checkout "not a git repo" and skipped every git/PR/branch check in it.
  if ! git -C "$d" rev-parse --git-dir >/dev/null 2>&1; then
    echo "  (not a git repo: $d)"
    # 🔴 AND SAY SO IN THE VERDICT. Returning here skips PR reconciliation,
    # branch existence, ahead/behind — everything this block contributes. Left
    # unrecorded, DRIFT went on to print "(none detected — live state matches
    # the handoff's claims)" for a run that checked NOTHING: a clean bill of
    # health issued by a doctor who never came. Hit live 2026-08-20 by passing
    # an explicit handoff path outside any repo.
    UNRECONCILED+=("$d is not a git repo — no branch, ahead/behind or PR reconciliation ran at all")
    return
  fi

  # working state (same shape as standup.sh _repo_state)
  local br ab behind ahead dirty cl age subj
  br=$(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null)
  ab=$(git -C "$d" rev-list --left-right --count '@{u}...HEAD' 2>/dev/null)
  behind=$(printf '%s' "$ab" | awk '{print $1+0}')
  ahead=$(printf '%s' "$ab" | awk '{print $2+0}')
  dirty=$(git -C "$d" status --porcelain 2>/dev/null | grep -c .)
  cl=$(git -C "$d" log -1 --format='%cr%x09%s' 2>/dev/null)
  age=${cl%%$'\t'*}; subj=${cl#*$'\t'}
  age=$(printf '%s' "$age" | awk '{print $1 substr($2,1,1)}')
  printf '  %s  branch %s  ↑%s↓%s  %s  last %s: %s\n' \
    "$(basename "$d")" "${br:-?}" "${ahead:-0}" "${behind:-0}" \
    "$([ "${dirty:-0}" -gt 0 ] && echo "${dirty} dirty" || echo clean)" \
    "${age:-?}" "${subj:0:60}"

  if [ -n "$HANDOFF" ]; then
    # The `handoff:` line names the FILE and nothing else — several callers and
    # the /resume skill match it exactly. Which COPY of that file was read is a
    # separate claim, so it gets its own line rather than being smuggled onto
    # the end of this one.
    echo "  handoff: $(basename "$HANDOFF")"
    echo "  handoff-read: ${HANDOFF_NOTE:-working-tree copy}"
    [ -n "$HANDOFF_ALT" ] && echo "  handoff-other-copy: $HANDOFF_ALT"
  else
    echo "  handoff: (none found — git-only)"
  fi
  [ -n "$HANDOFF" ] || return
  local text="$HANDOFF_TEXT"

  # --- PRs: reconcile every referenced PR against gh; drop 404s (false positives) ---
  #
  # 🔴 COUNT WHAT ACTUALLY ANSWERED. `|| continue` swallows every gh failure
  # identically — offline, unauthenticated, rate-limited, no access, or a prose
  # ref that is not a real PR — and prints NOTHING. Without these counters a run
  # where gh answered for zero of five referenced PRs was indistinguishable from
  # a run that reconciled them all and found nothing, and DRIFT then printed
  # "(none detected — live state matches the handoff's claims)": the same false
  # green, one layer up from the missing-handoff case.
  #
  # We deliberately do NOT try to attribute the cause. gh returns non-zero for
  # a genuine 404 and for an auth/network failure alike, so any classifier here
  # would be guessing; the honest report is the RATIO plus the list of causes it
  # could be. A partial answer is reported too — otherwise one unreachable PR
  # hides behind four that worked.
  local refs; refs=$(extract_pr_refs "$text")

  # 🔴 CAN A BARE `#N` BE CLAIMED BY THIS REPO? Only when the doc gives no
  # reason to think otherwise. A handoff that cites a FOREIGN repo anywhere has
  # demonstrated it talks about more than one, and a bare number in such a doc
  # is genuinely ambiguous — the author knew which repo they meant and did not
  # write it down. Resolving it locally is a coin flip recorded as a finding.
  #
  # So: foreign qualified ref present (or no local slug at all) => every bare
  # ref is UNATTRIBUTED and is REPORTED as such, never looked up. Reporting is
  # the point — a silent skip would rebuild the same false green one layer down.
  local foreign
  foreign=$(printf '%s\n' "$refs" \
    | awk -F'\t' -v me="${SLUG:-}" 'NF==2 && $1!="-" && tolower($1)!=tolower(me){print $1}' \
    | sort -u | grep -c . )
  local bare_ok=1
  { [ -z "${SLUG:-}" ] || [ "$foreign" -gt 0 ]; } && bare_ok=0

  if have gh; then
    local slug num target label j state ci n_try=0 n_ok=0 n_unattr=0 unattr_nums=""
    while IFS=$'\t' read -r slug num; do
      [ -z "${num:-}" ] && continue
      if [ "$slug" = "-" ]; then
        if [ "$bare_ok" -eq 0 ]; then
          # COLLECTED, not printed one-per-line. A real handoff carries dozens
          # of bare refs (34 on the doc this fix was measured against), and 34
          # near-identical lines is its own wall of noise — the thing the gap
          # banner exists to cut through. One line, every number on it.
          n_unattr=$((n_unattr+1))
          unattr_nums="${unattr_nums}#${num} "
          continue
        fi
        target="$SLUG"; label="#$num"
      else
        target="$slug"; label="$slug#$num"
      fi
      n_try=$((n_try+1))
      j=$(gh pr view "$num" -R "$target" --json state,mergeable,mergedAt,statusCheckRollup 2>/dev/null) || continue
      [ -z "$j" ] && continue                         # 404 / not a real PR -> silently drop
      n_ok=$((n_ok+1))
      state=$(printf '%s' "$j" | jq -r '.state')
      ci=$(printf '%s' "$j" | jq -r '[.statusCheckRollup[]?.conclusion]
             | if any(.=="FAILURE" or .=="ERROR") then "red"
               elif length>0 and all(.=="SUCCESS") then "green" else "pending" end')
      if [ "$state" = OPEN ]; then printf '  PR %s %-6s ci=%s\n' "$label" "$state" "$ci"
      else printf '  PR %s %s\n' "$label" "$state"; fi
      # DRIFT: the handoff frames this PR as open/in-flight but it already landed...
      if [ "$state" = MERGED ] && handoff_says_inflight "$num" "$HANDOFF" "$([ "$slug" = "-" ] || printf '%s' "$slug")"; then
        DRIFT+=("PR $label MERGED but handoff frames it as open/in-flight (do the follow-on)")
      fi
      # ...or a referenced PR was CLOSED without merging (abandoned — always notable)
      [ "$state" = CLOSED ] && DRIFT+=("PR $label CLOSED without merge (was the handoff plan abandoned?)")
      [ "$state" = OPEN ] && [ "$ci" = red ] && DRIFT+=("PR $label OPEN with RED ci")
    done <<<"$refs"
    if [ "$n_try" -gt 0 ] && [ "$n_ok" -eq 0 ]; then
      echo "  (gh answered for 0 of $n_try referenced PR(s))"
      UNRECONCILED+=("gh answered for 0 of $n_try referenced PR(s) — offline, unauthenticated, no access, or none is a real PR")
    elif [ "$n_ok" -lt "$n_try" ]; then
      UNRECONCILED+=("gh answered for $n_ok of $n_try referenced PR(s) — the rest were not reconciled")
    fi
    if [ "$n_unattr" -gt 0 ]; then
      if [ -z "${SLUG:-}" ]; then
        printf '  PR UNATTRIBUTED (%s bare ref(s); this checkout has no origin remote) — not resolved: %s\n' \
          "$n_unattr" "${unattr_nums% }"
      else
        printf '  PR UNATTRIBUTED (%s bare ref(s); the doc also names %s other repo(s)) — not resolved against %s: %s\n' \
          "$n_unattr" "$foreign" "$SLUG" "${unattr_nums% }"
      fi
      UNRECONCILED+=("$n_unattr bare #N ref(s) could not be attributed to a repo — the doc names other repos, so resolving them against ${SLUG:-this checkout} would invent findings; qualify them as owner/repo#N to reconcile")
    fi
  else
    echo "  (gh unavailable — PR reconciliation skipped)"
    # Only a GAP if the handoff actually referenced PRs. A handoff naming none
    # has nothing for gh to answer, so the absence of gh costs no coverage and
    # must not downgrade an otherwise honest clean result.
    if [ -n "$refs" ]; then
      UNRECONCILED+=("gh unavailable — $(printf '%s\n' "$refs" | grep -c .) referenced PR(s) were never checked")
    fi
  fi

  # --- branches: does the handoff's named branch still exist / is it merged? ---
  local b tip
  for b in $(extract_branches "$text"); do
    # ORDER IS DELIBERATE: being a real BRANCH wins over being a real PATH.
    # The tracked-path probe is the repo-aware half of the anti-fabrication
    # filter documented on extract_branches — it catches the extensionless
    # cases the string filters cannot see (a quoted `docs/architecture`
    # directory, say). But it used to run FIRST, which meant a token that was
    # both a live branch AND a tracked path got silently dropped. No such
    # collision exists across 2893 real branches today, so this reorder loses
    # nothing measurable; it just removes a latent wrong-drop, and the
    # anti-fabrication guarantee is unchanged because a token still has to fail
    # BOTH branch lookups before the path probe can rescue it from GONE.
    if git -C "$d" rev-parse --verify -q "$b" >/dev/null 2>&1; then
      tip=local
    elif git -C "$d" rev-parse --verify -q "origin/$b" >/dev/null 2>&1; then
      tip="origin/$b"
    elif git -C "$d" cat-file -e "HEAD:$b" 2>/dev/null; then
      continue                                      # a tracked PATH, not a branch
    else
      echo "  branch $b: GONE (deleted or never local)"
      DRIFT+=("branch $b referenced by handoff no longer exists (merged & pruned?)")
      continue
    fi
    if git -C "$d" merge-base --is-ancestor "$b" HEAD 2>/dev/null \
       || git -C "$d" branch --merged 2>/dev/null | grep -qw "$b"; then
      echo "  branch $b: merged into HEAD"
    else
      echo "  branch $b: exists ($tip)"
    fi
  done
}

# ---------------------------------------------------------------------------
# WORKLOAD — best-effort, v1 = datapacket / any repo with a prod-kubeconfig.
# Intersects handoff tokens with REAL deployment names; reports readiness + canary.
# ---------------------------------------------------------------------------
WL_NS=""   # first matched namespace -> scopes the alerts block
workload_block(){
  echo "WORKLOAD"
  local kc="$REPO/prod-kubeconfig"
  if [ ! -f "$kc" ]; then echo "  (no prod-kubeconfig for $(basename "$REPO") — skipped)"; return; fi
  export KUBECONFIG="$kc"
  if ! kubectl $KT get --raw /readyz >/dev/null 2>&1; then echo "  (cluster unreachable — skipped)"; return; fi
  [ -n "$HANDOFF" ] || { echo "  (no handoff — nothing to scope to)"; return; }

  local text tokens dep_json can_raw
  text="$HANDOFF_TEXT"                 # the copy handoff_freshness chose, not blindly the working tree
  tokens=$(extract_tokens "$text")
  dep_json=$(kubectl $KT get deploy -A -o json 2>/dev/null)
  [ -z "$dep_json" ] && { echo "  (no deployments listed — skipped)"; return; }

  # exact-match real deploy names against the candidate tokens (junk tokens drop)
  local matched
  matched=$(printf '%s' "$dep_json" \
    | jq -r '.items[] | "\(.metadata.namespace)\t\(.metadata.name)\t\(.status.readyReplicas//0)\t\(.spec.replicas//0)"' \
    | while IFS=$'\t' read -r ns name ready want; do
        grep -qxF "$name" <<<"$tokens" && printf '%s\t%s\t%s\t%s\n' "$ns" "$name" "$ready" "$want"
      done)

  if [ -z "$matched" ]; then echo "  (no handoff-named deployments found live)"; return; fi
  local ns name ready want
  while IFS=$'\t' read -r ns name ready want; do
    [ -z "$name" ] && continue
    [ -z "$WL_NS" ] && WL_NS="$ns"
    if [ "${want:-0}" -gt 0 ] && [ "${ready:-0}" -lt "${want:-0}" ]; then
      printf '  %s/%s  %s/%s  NOT READY\n' "$ns" "$name" "$ready" "$want"
      DRIFT+=("deployment $ns/$name is $ready/$want (not fully ready)")
    else
      printf '  %s/%s  %s/%s\n' "$ns" "$name" "$ready" "$want"
    fi
  done <<<"$matched"

  # canary phase for handoff-named canaries (reuse standup's canary shape)
  can_raw=$(kubectl $KT get canary -A --no-headers 2>/dev/null)
  if [ -n "$can_raw" ]; then
    local cns cname cstatus
    while read -r cns cname cstatus _; do
      [ -z "$cname" ] && continue
      grep -qxF "$cname" <<<"$tokens" || continue
      printf '  canary %s/%s  %s\n' "$cns" "$cname" "$cstatus"
      case "$cstatus" in
        Succeeded|Initialized|"") ;;
        *) DRIFT+=("canary $cns/$cname phase=$cstatus (mid-wave or failed)") ;;
      esac
    done <<<"$can_raw"
  fi
}

# ---------------------------------------------------------------------------
# ALERTS — best-effort; reuse standup's Alertmanager port-forward, FILTER to the
# matched namespace only. Degrades silently.
# ---------------------------------------------------------------------------
alerts_block(){
  echo "ALERTS"
  if [ -z "$WL_NS" ]; then echo "  (no scoped namespace — skipped)"; return; fi
  local kc="$REPO/prod-kubeconfig"
  [ -f "$kc" ] || { echo "  (no kubeconfig — skipped)"; return; }
  export KUBECONFIG="$kc"
  kubectl $KT get --raw /readyz >/dev/null 2>&1 || { echo "  (cluster unreachable — skipped)"; return; }

  kubectl port-forward -n monitoring svc/kube-prometheus-stack-alertmanager 19093:9093 >/dev/null 2>&1 &
  local pf=$!; sleep 3
  local out
  out=$(curl -s --max-time 10 http://127.0.0.1:19093/api/v2/alerts 2>/dev/null \
    | jq -r --arg ns "$WL_NS" --arg noise "$NOISE_RE" '
        [ .[] | select(.status.state=="active")
              | select(.labels.namespace==$ns)
              | {an:(.labels.alertname//"?"), sev:(.labels.severity//"")} ]
        | if length==0 then "  (none firing in \($ns))"
          else ( .[] | "  " + .sev + " " + .an
                       + (if (.sev=="critical" and (.an|test($noise)|not)) then "  ***" else "" end) )
          end' 2>/dev/null)
  kill "$pf" 2>/dev/null; wait "$pf" 2>/dev/null
  if [ -z "$out" ]; then echo "  (alertmanager unreachable — skipped)"; else echo "$out"; fi
  # feed real criticals into DRIFT
  #
  # ⚠ NOTHING IN THE TEST SUITE REACHES THIS BLOCK. The hermetic fixtures carry
  # no prod-kubeconfig, so alerts_block always returns at the `WL_NS` guard;
  # inverting the CRITICAL test below survives all 56 tests. Pre-existing gap,
  # recorded so the suite's green is not read as covering it — the change here
  # is hygiene, verified by reading, not by a guard.
  #
  # A full `if`, not `[[ … ]] && DRIFT+=(…)`. The `&&` form is the same class of
  # bug that made `main` exit 1 when it found drift with nothing unreconciled:
  # a false test leaves the compound returning 1, and here that is the last
  # statement of the LOOP and so of the function. It is harmless TODAY only
  # because alerts_block is not the last thing `main` runs — i.e. it is one
  # reordering away from being a bug, which is not a property worth relying on.
  while read -r line; do
    if [[ "$line" == *"***"* ]]; then
      DRIFT+=("firing CRITICAL in $WL_NS:${line%%  \*\*\*}")
    fi
  done <<<"$out"
}

# ---------------------------------------------------------------------------

# Gaps are the thing a reader skips. They used to print as bare `  ! …` lines
# directly beneath a wall of `  - …` findings, and 2026-08-20 they were duly
# missed — the skill's own text warns that a findings list is complete "only if
# no ! line sits beside it", which is a lot to ask of prose formatted to look
# identical to the findings. Give them a rule and a shouted header so the eye
# cannot slide past, and keep the `!` prefix the docs key on.
print_gaps(){
  [ "${#UNRECONCILED[@]}" -gt 0 ] || return 0
  echo "  ═══════════════════════════════════════════════════════════════"
  echo "  !! GAPS (${#UNRECONCILED[@]}) — SOURCES THAT DID NOT ANSWER."
  echo "  !! Anything above is therefore INCOMPLETE, not a clean bill of health."
  printf '  ! %s\n' "${UNRECONCILED[@]}"
  echo "  ═══════════════════════════════════════════════════════════════"
}

main(){
  resolve "${1:-}"
  echo "## resume-state $(date -u +%FT%TZ)"
  echo "# repo: ${REPO:-?}  slug: ${SLUG:-?}"
  handoff_freshness
  git_pr_block
  workload_block
  alerts_block
  echo "DRIFT"
  if [ "${#DRIFT[@]}" -gt 0 ]; then
    printf '  - %s\n' "${DRIFT[@]}"
    # Print gaps ALONGSIDE findings too: a source that never answered is easy to
    # miss when real drift is on screen, and "here are 3 findings" reads as a
    # complete list unless the incompleteness is stated next to it.
    #
    # ⚠ A full `if` block, NOT `[ … ] && printf`. This is the last statement of
    # the branch, so a false test would make `main` — and the script — exit 1
    # on every run that found drift and had nothing unreconciled. Caught by
    # test_a_genuinely_missing_branch_is_still_reported, which asserts rc 0.
    print_gaps
  elif [ -z "$HANDOFF" ]; then
    # No handoff resolved => nothing was reconciled. Saying "live state matches
    # the handoff's claims" here is a LIE, and the reassuring shape of it is the
    # actual harm: a caller reads it as a clean bill of health for an initiative
    # whose doc was never loaded. Name the absence instead.
    echo "  (no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"
    print_gaps
  elif [ "${#UNRECONCILED[@]}" -gt 0 ]; then
    # A handoff loaded and nothing contradicted it — but a source went
    # unanswered, so "no drift" is not a finding about live state, it is a
    # finding about the part of live state we managed to see.
    echo "  (nothing detected, but a source did not answer — NOT a clean bill of health)"
    print_gaps
  else
    echo "  (none detected — live state matches the handoff's claims)"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
