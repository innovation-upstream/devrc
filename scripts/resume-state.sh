#!/usr/bin/env bash
# resume-state.sh — INITIATIVE-scoped, on-demand live-state reconciler for /resume.
#
# Given one handoff doc (an initiative), it reconciles the doc's claims against
# FRESH live state and emits a compact digest: SKILL, GIT/PR, WORKLOAD, ALERTS,
# DRIFT. (SKILL answers a different question from the rest — "are the
# INSTRUCTIONS I am executing current?" — see skill_block.)
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
#                  else the no-arg chain above — REPORTED AS A GAP when that
#                  fallback had >1 candidate to choose from, because "newest" is
#                  only the contract when nothing was asked for
#   handoff path-> that file; the target repo is derived FROM the path. Also
#                  matched when the path is quoted INSIDE a prose argument,
#                  which is the form /resume passes through ("…; handoff: <p>")
#                  — but ONLY for a token shaped like the handoff population
#                  itself (claudedocs/handoff-*.md, claudedocs/*HANDOFF*.md).
#                  A bare `README.md` in prose is NOT a handoff reference.
#
# v1 workload/alerts target datapacket (prod-kubeconfig at <repo>/prod-kubeconfig);
# everywhere else it degrades to the (always-run) GIT/PR block.
set -uo pipefail

KT="--request-timeout=8s"
# alertnames that are known/expected noise (mirrors standup.sh) — dropped from criticals
NOISE_RE='TargetDown|KubeHpaMaxedOut'

have(){ command -v "$1" >/dev/null 2>&1; }

# The clawgate<->handoff seam: the front-matter parser and the drift rules,
# SHARED with /handoff's writer so the two cannot disagree about what a
# `clawgate-task:` field is. Sourced (not re-implemented) — see that file's
# header. Its pure functions are asserted on fixture text by
# scripts/tests/test_resume_state_clawgate.py exactly as `extract_prs` is.
# 🔴 A FAILED SOURCE IS RECORDED, NOT SWALLOWED. This script runs without
# `set -e`, so a missing lib would leave every `clawgate_*` call reporting
# "command not found" (127) — and the block below reads a non-zero from the
# parser as "this doc names no task", i.e. the absent tool would render as a
# clean, reassuring absence. Exactly the false green the digest exists to stop.
# shellcheck source=lib/clawgate_handoff.sh
CLAWGATE_LIB="$(dirname "${BASH_SOURCE[0]}")/lib/clawgate_handoff.sh"
CLAWGATE_LIB_OK=1
# shellcheck source=/dev/null
. "$CLAWGATE_LIB" 2>/dev/null || CLAWGATE_LIB_OK=0

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
# 🔴 $2 IS THE TEXT, NOT A PATH, AND THAT SIGNATURE IS THE FIX. It took a path
# and grepped the FILE, so `git_pr_block` extracted the PR refs from
# `$HANDOFF_TEXT` — the copy `handoff_freshness` chose — and then asked a
# DIFFERENT copy of the document whether the doc framed them as in-flight. Both
# directions were measured on a stale clone against a bare origin:
#
#   false clean   origin copy says `acme/widget#999 is OPEN and awaiting merge`,
#                 stale local says nothing -> `PR … MERGED` and NO drift line,
#                 under a header announcing it had read the origin copy;
#   fabrication   stale local says OPEN, origin says `LANDED; the follow-on is
#                 already done` -> the drift line fires anyway. The comment at
#                 the top of extract_branches calls this the worse direction.
#
# Taking TEXT removes the class from this helper permanently: there is no path
# left for a caller to hand it, so no caller can hand it the wrong one. The
# guard that failed to see this (it only looked for content reads written
# INLINE) now also ledgers every helper that RECEIVES $HANDOFF and reads it —
# see test_only_handoff_freshness_READS_the_working_tree_copy.
handoff_says_inflight(){ # $1=pr number  $2=handoff TEXT  $3=owner/repo (optional)
  [ -n "$2" ] || return 1
  local lines="" q
  if [ -n "${3:-}" ]; then
    q=$(printf '%s' "$3" | sed -E 's/[][\\.^$*+?(){}|]/\\&/g')
    lines=$(printf '%s\n' "$2" | grep -iE "$q#$1([^0-9]|$)" 2>/dev/null)
  fi
  [ -z "$lines" ] && lines=$(printf '%s\n' "$2" | grep -iE "#$1([^0-9]|$)" 2>/dev/null)
  printf '%s\n' "$lines" \
    | grep -qiE 'open|in.?flight|awaiting|pending|not yet merged|to merge|mergeable|review|wip|draft|blocked'
}

# ---------------------------------------------------------------------------
# Resolve target repo + handoff doc.
# ---------------------------------------------------------------------------
REPO="" HANDOFF="" SLUG=""

# `claudedocs/$2` inside every LINKED WORKTREE of the clone that contains $1.
#
# 🔴 THE CLONE IS AN ARGUMENT, NOT `$PWD`. Handoff docs land in linked worktrees
# by construction — `claude/RULES.md` makes worktree isolation the standing
# default for any agent that modifies files — so `<repo>/claudedocs/handoff-x.md`
# routinely names a base clone that has never held the file, while a sibling
# worktree of the SAME clone does. MEASURED 2026-08-31 (#1164): that miss sent
# the run down the newest-of-N fallback and it reconciled a DIFFERENT
# INITIATIVE, PR states and DRIFT findings included.
#
# What this must NOT become is a search of whatever repo the caller happens to
# be standing in. `embedded_md_path`'s absolute-token restriction exists because
# serving a same-named doc out of THIS repo is the wrong-initiative bug itself;
# enumerating $PWD's worktrees would reintroduce it one level down. So the
# caller passes the directory the TOKEN named, and every path returned is a
# worktree of that same clone — the guarantee survives.
#
# 🔴 THAT IS A CONSTRAINT ON THE CALLER, AND THIS FUNCTION CANNOT ENFORCE IT.
# It was violated at the SECOND call site the day it shipped: the relative
# re-anchor passed `$root` — the cwd's repo — for ANY token, so
# `other-repo/claudedocs/<base>` typed inside `devrc` was answered out of
# `devrc`'s worktrees with no gap (audit of #1197, F1; measured). The
# `$mine` gate in `embedded_md_path` is what holds it now: `$root` may be
# passed here only for a token that names THIS tree or names no tree at all.
#
#   exit 0 + one path    exactly one worktree holds it
#   exit 2 + N paths     several do; the caller must NOT pick
#   exit 1 + nothing     none does, or $1 is not inside a git repo
#
# 🔴 AMBIGUITY IS NOT A TIE TO BREAK. Two worktrees of one clone holding the
# same handoff basename are two different revisions of it — usually a branch and
# its base — and choosing by mtime or by list order would put the whole digest
# on a coin flip, silently. The chooser this file already regrets is the
# newest-of-N fallback; this one refuses instead and says what it saw.
worktrees_holding(){
  local dir="$1" base="$2" cands n
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  # ⚠ THIS LINE IS DEFENSIVE AND UNCOVERED — say so rather than let it read as
  # tested. Mutating it away is EQUIVALENT: a directory outside any checkout
  # makes `git worktree list` fail, whose suppressed output is empty, which
  # returns 1 anyway. It states the precondition; the `-d` test above is what
  # actually handles absence. (mutation_battery_resume_state.py records this.)
  git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || return 1
  cands=$(git -C "$dir" worktree list --porcelain 2>/dev/null \
    | sed -n 's#^worktree ##p' \
    | while IFS= read -r w; do
        [ -f "$w/claudedocs/$base" ] && printf '%s\n' "$w/claudedocs/$base"
      # 🔴 `LC_ALL=C` IS THE PIN, NOT A TIDY-UP. Bare `sort` collates under the
      # caller's locale: under `en_US.UTF-8` punctuation and case fold away, so
      # `devrc-A/…` vs `devrc-a/…` vs `devrc.b/…` order DIFFERENTLY from
      # codepoint order. The tests build their expected list with Python
      # `sorted()`, which is codepoint order — i.e. C order — so an unpinned
      # `sort` is a live impl/test divergence that the ASCII-lowercase fixtures
      # cannot see, because they collate identically either way.
      done | LC_ALL=C sort -u)
  n=$(printf '%s\n' "$cands" | grep -c .)
  [ "$n" -eq 0 ] && return 1
  printf '%s\n' "$cands"
  [ "$n" -eq 1 ] && return 0
  return 2
}

# A .md path EMBEDDED IN PROSE. Prints it, or nothing.
#
# The /resume skill passes its topic argument through VERBATIM, and that
# argument's documented form is prose that carries the doc:
#
#   "continue the app-listing work; handoff: /abs/claudedocs/handoff-app-listing.md"
#
# `[ -f "$arg" ]` is false for that whole string, so resolve()'s explicit-path
# branch never fired, the slug glob interpolated the entire sentence and matched
# nothing, and the run silently reconciled the newest UNRELATED handoff instead
# (#684). The caller named the file. Reading it out is not a heuristic about
# what they meant — it is the thing they said.
#
# 🔴 THE ACCEPTED TOKEN IS SCOPED TO THE HANDOFF POPULATION, NOT TO ".md", AND
# THAT IS THE WHOLE GUARD. The first version of this function accepted any
# existing `*.md` token, which turned #684's silent-wrong-document failure into
# a DIFFERENT silent-wrong-document failure — worse, because it fires on
# perfectly ordinary English:
#
#   resume-state.sh "rewrite the README.md section then resume the listing work"
#     handoff: README.md
#     DRIFT  (none detected — live state matches the handoff's claims)
#
# Measured, along with a backticked `docs/ARCHITECTURE.md`, a `keep.md` in a cwd
# subdirectory, and every one of the nine DECOY_DOCS this module's own test suite
# carries. And backticks are in the strip set below, so the fleet convention of
# code-quoting a path in prose makes it MORE likely, not less.
#
# ⚠ NOT in that list, though the audit put it there: a bare `resume-state.sh
# wanted.md` resolving a root `wanted.md` over a slug that would have matched.
# Measured on all three revisions — main 732db793, #690 3b70baaa, and here — it
# is identical, because `[ -f "$arg" ]` takes it first and always has. The scan
# cannot shadow a slug: a SINGLE-token argument that exists has already been
# claimed by the path branch, and one that does not exist fails the scan's own
# `-f` test. Pinned by test_a_single_token_md_ARGUMENT_is_the_explicit_path_branch
# so the correction stays checkable.
#
# 🔴 THE SAME FILE ALREADY RULES THIS OUT ONE SCREEN UP. `extract_branches`
# disqualifies any token with a trailing file extension — "so `.md`/`.json`…
# drop" — because "inventing one puts a false statement in front of someone
# deciding what to do next". Harvesting from prose exactly what that guard
# refuses to harvest is the same disease at a bigger blast radius: a branch
# token costs one wrong DRIFT line, a handoff token costs the whole digest.
#
# So the token must name a member of the population resolve() itself globs, i.e.
# match `subsystem_recall.HANDOFF_GLOBS` at its tail:
#
#   immediate parent directory  ==  claudedocs
#   basename                    ==  handoff-*.md   or   *HANDOFF*.md
#
# 🔴 BOTH halves, not either. The auditor proposed OR; AND is strictly stronger
# and costs nothing, because `claudedocs/` in these repos is mostly design and
# audit docs — `SOME-DESIGN.md`, `SECURITY-AUDIT-*.md`, `HANDBOOK.md` — and this
# module already carries them as DECOY_DOCS precisely because they must never
# resolve as a handoff. Under OR, prose naming one of them would resolve it.
# The uppercase glob is deliberately the caps-family one, so `HANDBOOK.md` (HAND,
# not HANDOFF) stays out for the same reason it does in the fallback chain.
#
# The `-f` test stays: a doc that was renamed or lives in another checkout must
# fall through to the warned fallback rather than resolve to a path that is not
# there. FIRST match wins — a prose argument naming two docs is not a case this
# can adjudicate, and the first is at least the one the caller wrote first.
# (Pinned: `test_the_FIRST_of_two_prose_paths_wins`. A `break`->`continue`
# mutant survived all 94 tests before that existed.)
#
# One layer of surrounding punctuation is stripped so `(…/x.md)`, `` `…/x.md` ``
# and `…/x.md,` resolve; a token that survives with anything else attached is
# left alone rather than guessed at.
#
# 🔴 THREE OUTCOMES, NOT TWO, and the third is why this returns a CODE rather
# than just a path. "The caller named a handoff and it is not there" is a
# different fact from "the caller named no handoff at all", and collapsing them
# is how the fix for #684 re-created #684's harm one round later: in a repo with
# a single handoff, an argument naming a doc that does NOT exist resolved that
# single unrelated doc, silently, with a clean DRIFT all-clear. See the
# `named_missing` block in resolve().
#
#   exit 0 + the path   resolved
#   exit 2 + the TOKEN  a handoff-shaped path was named and does not exist
#   exit 1 + nothing    no handoff-shaped token in the argument at all
#   exit 3 + the TOKEN, then one path per line: the named path is not on disk
#                       and SEVERAL worktrees of its own clone hold that
#                       basename, so nothing was chosen. See `worktrees_holding`.
embedded_md_path(){
  local tok hit="" miss="" base dir noglob="" root=""
  local amb="" ambig="" wt="" wrc=0 mine="" ydir=""
  # The repo of $PWD, resolved ONCE. Used only to re-anchor a RELATIVE token
  # that named a real doc from one directory up — see the clause below.
  root=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null) || root=""
  # ⚠ `for tok in $1` is UNQUOTED on purpose — that is the word split. It is also
  # a PATHNAME EXPANSION, and the subject is arbitrary prose: an argument
  # containing `*` would otherwise expand against the cwd and hand this loop a
  # directory listing, out of which any stray .md would resolve as "the doc the
  # caller named". Split, don't glob.
  case $- in *f*) ;; *) noglob=1; set -f ;; esac
  for tok in $1; do
    tok=${tok#[\`\'\"\(\[\<]}
    tok=${tok%[\`\'\"\)\]\>,\;]}
    base=${tok##*/}
    dir=${tok%/*}
    # ⚠ A token with no `/` leaves `dir` EQUAL TO THE TOKEN rather than empty.
    # That is deliberately NOT special-cased: the only slash-less token whose
    # `dir` can pass the test below is the literal `claudedocs`, and its
    # `base` is `claudedocs` too, which the second test rejects. A
    # `[ "$dir" = "$tok" ] && dir=""` line was written here first and then
    # DELETED — mutating it away survived all 115 tests, i.e. it guarded
    # nothing, and a guard that reads as load-bearing while doing nothing is
    # worse than its absence.
    case "$dir" in */claudedocs|claudedocs) ;; *) continue ;; esac
    case "$base" in handoff-*.md|*HANDOFF*.md) ;; *) continue ;; esac
    [ -f "$tok" ] && { hit="$tok"; break; }
    # 🔴 THE NAMED TREE'S OWN CLONE, INCLUDING ITS LINKED WORKTREES. `<X>` is the
    # token with `/claudedocs/<base>` stripped, i.e. the checkout the caller
    # actually wrote down. Only if THAT is itself a git checkout do we enumerate
    # its worktrees — so a doc is still only ever served out of the clone that
    # was named, which is the guarantee the absolute-token restriction below
    # exists to hold. `<X>` outside a repo, or a token whose parent is the bare
    # `claudedocs`, leaves this inert rather than guessing.
    #
    # ⚠ `<X>` NEED NOT BE A WORKTREE ROOT. `git -C` resolves from any directory
    # inside the checkout, and the paths tested are always `<worktree
    # root>/claudedocs/<base>` — the only place this tool's own convention puts a
    # handoff. A token naming a deeper directory therefore re-anchors upward
    # WITHIN its clone, which is the same scope, not a wider one.
    amb=""
    case "$dir" in
      */claudedocs)
        wt=$(worktrees_holding "${dir%/claudedocs}" "$base"); wrc=$?
        [ "$wrc" -eq 0 ] && { hit="$wt"; break; }
        [ "$wrc" -eq 2 ] && amb="$wt"
        ;;
    esac
    # 🔴 DOES THE TOKEN NAME *THIS* TREE? Everything below the `case` on `$tok`
    # re-anchors on `$root` — the repo of `$PWD` — and `$root` is only a
    # defensible anchor for a token that was never a claim about some OTHER
    # tree. Two shapes qualify and nothing else does:
    #
    #   `claudedocs/<base>`          <Y> is empty: the token names no tree at
    #                                all, so the cwd's is the only one it can
    #                                mean (typed from a subdirectory).
    #   `<…>/<root's name>/claudedocs/<base>`
    #                                <Y>'s LAST component is this checkout's own
    #                                directory name — #1159's kickoff-template
    #                                shape, `<repo>/claudedocs/handoff-<x>.md`
    #                                pasted inside `<repo>`.
    #
    # 🔴 MEASURED 2026-09-01 (audit of #1197, round 1) — WITHOUT THIS TEST THE
    # WORKTREE SEARCH ABOVE IS SCOPED AND THE ONE BELOW IS NOT. In a fixture
    # holding `devrc` (with linked worktree `devrc-topic` carrying the doc) and
    # a sibling `other-repo` that does NOT carry it,
    #
    #   cd devrc && resume-state.sh "other-repo/claudedocs/handoff-only-in-worktree.md"
    #
    # printed `handoff: handoff-only-in-worktree.md`, `# repo: …/devrc-topic`
    # and NO gap: a doc served out of a clone the caller did not name, silently.
    # That is exactly the wrong-initiative harm #1164 exists to remove, and the
    # 🔴 comments above and in `worktrees_holding` both promise it cannot
    # happen. `other-repo` does not resolve from the cwd at all — it is a
    # SIBLING of the repo, not a subdirectory — so "re-anchor only if <Y> is
    # itself a checkout" does NOT catch it; the discriminator has to be the
    # NAME, not the resolvability.
    #
    # 🔴 AND THE NAME IS ALL IT IS — A RESIDUAL, SAID OUT LOUD. This does NOT
    # mean "a foreign tree is never re-anchored": the test is `<Y>`'s LAST
    # COMPONENT, so ANY relative token whose directory part ends in a component
    # spelled like this checkout re-anchors here, wherever it actually points.
    #
    # 🔴 AND RE-ANCHORING IS NOT "RESOLVES THIS REPO'S COPY WITH NO GAP" — that
    # is what this paragraph used to say, and it was narrower than the behaviour
    # in the direction of the harm (audit of #1197, round 3). Re-anchoring hands
    # the token to the SAME resolution a token naming this tree honestly gets,
    # worktree search included, so there are THREE legs and only two are
    # gapless. MEASURED 2026-09-01 from a checkout named `devrc`, with both
    # `backup/devrc/claudedocs/<base>` and `../elsewhere/devrc/claudedocs/<base>`:
    #
    #   base clone holds <base>       -> resolves the base clone's copy, no gap
    #   ONLY a linked worktree does   -> resolves OUT OF THAT WORKTREE, which is
    #                                    another branch's checkout and is what
    #                                    the `# repo:` line then names; no gap
    #   SEVERAL worktrees do          -> nothing is chosen, and the AMBIGUITY
    #                                    gap fires naming them
    #
    # It is the unavoidable cost of a name-based discriminator, not an
    # oversight, and it is bounded: a foreign tree with a DIFFERENT last
    # component misses, the compare is case-SENSITIVE so `DEVRC/claudedocs/…`
    # misses, and an ABSOLUTE token never re-anchors at all. Widening the
    # discriminator needs one that a foreign SIBLING fails, which `-d` does not
    # (above). All three legs are pinned — the first two by
    # `test_a_FOREIGN_tree_whose_LAST_COMPONENT_matches_this_repo_STILL_re_anchors`
    # (parametrised over WHICH tree holds the doc, and asserting the `# repo:`
    # line, because the `handoff:` line is a basename and cannot tell them
    # apart), the third by `test_the_residual_ALSO_hits_the_AMBIGUITY_gap_when_
    # SEVERAL_worktrees_hold_it` — and stated in SKILL.md, so nobody reads the
    # gate as stronger than it is.
    #
    # ⚠ THIS NARROWS A LEGITIMATE-BUT-AMBIGUOUS CASE, DELIBERATELY. A relative
    # token naming a SIBLING WORKTREE OF THE SAME CLONE — `devrc-topic/
    # claudedocs/x.md` typed from `devrc` — now misses. It is the safe
    # direction: the run prints the `!` gap naming what it could not find
    # instead of a confident digest, and the absolute form
    # (`/…/devrc-topic/claudedocs/x.md`) still resolves through the scoped
    # search above. Widening it back means finding a discriminator that a
    # FOREIGN sibling fails, which `-d` does not.
    #
    # ⚠ It also closes the pre-existing single-tree half of the same hole:
    # #1159's plain `$root/claudedocs/$base` re-anchor fired for ANY <Y>, so
    # `other-repo/claudedocs/<base>` already resolved this repo's own copy
    # before any worktree search existed. Strictly stronger, same reason.
    mine=""
    case "$dir" in
      claudedocs) mine=1 ;;
      */claudedocs)
        ydir=${dir%/claudedocs}
        # `<repo>//claudedocs/<base>` — what a `"${d}/claudedocs/…"` splice
        # emits when `$d` already ends in `/`, and a spelling of the SAME
        # kickoff shape. Without this strip `${ydir##*/}` is the empty string,
        # which can never equal a repo's directory name, so a token that
        # unambiguously names this tree missed. It cannot widen the
        # discriminator: dropping trailing `/` can only ever expose a component
        # that was already there, never turn a foreign name into this one.
        while [ "$ydir" != "${ydir%/}" ]; do ydir=${ydir%/}; done
        if [ -n "$root" ] && [ "${ydir##*/}" = "${root##*/}" ]; then mine=1; fi
        ;;
    esac
    # 🔴 A RELATIVE token anchored one level ABOVE the repo still names a real
    # doc — and this is not a hypothetical shape: `/handoff`'s own kickoff
    # template emits `<repo>/claudedocs/handoff-<topic>.md`, which resolves from
    # the repo's PARENT and NOT from the repo, where the kickoff is pasted.
    # MEASURED 2026-08-30: that miss sent the run down the newest-of-90 fallback
    # and it reconciled a DIFFERENT INITIATIVE — PR states, DRIFT lines and all —
    # with only the `!` gap naming the file it could not find. Re-anchor on the
    # repo root and try again before calling it a miss.
    #
    # 🔴 RELATIVE ONLY, and that restriction is the load-bearing half. An
    # ABSOLUTE token that is not on disk stays a miss: the caller named a
    # specific tree, so serving a same-named doc out of THIS repo would be the
    # very wrong-initiative bug this clause exists to remove, reintroduced one
    # level down and harder to see. `$root` is empty outside a git repo, which
    # disables the clause rather than guessing.
    #
    # ⚠ `[ -n "$root" ]` is DEFENSIVE AND UNCOVERED — say so rather than let it
    # read as tested. Dropping it SURVIVED the mutation sweep: with `$root`
    # empty the test becomes `[ -f "/claudedocs/$base" ]`, and killing that
    # needs a literal `/claudedocs/handoff-*.md` at the filesystem ROOT, which
    # no fixture here can create. It is kept because it is correct, not because
    # anything proves it fires. The other four mutants of this clause are
    # killed, each by a named test.
    case "$tok" in
      /*) ;;
      # 🔴 `$mine` GATES BOTH CLAUSES BELOW — the re-anchor AND the worktree
      # search — and gating only one is a real, measured blind spot rather than
      # a theoretical one. They are mutants X1 and X2, one per clause. X1
      # SURVIVED the first battery run over 178 tests: every foreign-token
      # fixture kept its doc in a WORKTREE, so the ungated re-anchor had nothing
      # to find and no test could tell the two clauses apart. The fixture that
      # separates them puts the doc in the BASE CLONE with no worktree
      # (`test_a_FOREIGN_relative_token_is_not_re_anchored_on_THIS_repos_own_copy`).
      #
      # 🔴 DO NOT REFORMAT THE NEXT LINE — X1's mutation anchor is that whole
      # line verbatim, `*)` and `if` together (`60c893b7` split it to insert a
      # comment and the row silently went to 0x). This comment is no longer the
      # guard: `scripts/tests/test_mutation_battery_anchors.py` fails the gate
      # on ANY battery anchor that stops occurring exactly once.
      *) if [ -n "$mine" ] && [ -n "$root" ] && [ -f "$root/claudedocs/$base" ]; then
           hit="$root/claudedocs/$base"; break
         fi
         # …and the same worktree treatment on the same anchor, under the same
         # `$mine` gate. A relative token that names THIS tree (or names no tree
         # at all) was never a claim about a specific OTHER tree — that is
         # exactly why it may be re-anchored — so widening it from `$root` to
         # `$root`'s clone changes the reach by no repos, only by worktrees.
         # Skipped when the clause above already found the token ambiguous, so a
         # single cause cannot report two different candidate sets.
         if [ -n "$mine" ] && [ -z "$amb" ]; then
           wt=$(worktrees_holding "$root" "$base"); wrc=$?
           [ "$wrc" -eq 0 ] && { hit="$wt"; break; }
           [ "$wrc" -eq 2 ] && amb="$wt"
         fi ;;
    esac
    # 🔴 A GLOB IS NOT A DOCUMENT. `set -f` above stops `claudedocs/handoff-*.md`
    # from expanding, which is correct — but it then reaches `[ -f ]` as a
    # LITERAL filename that can never exist, and recording it as `miss` states
    # "the caller named a specific document" about a pattern that names a
    # class. Since #1164 part 2 that costs the whole digest: `named_missing`
    # suppresses the fallback chain, so the run reconciles NOTHING. The literal
    # `claudedocs/handoff-*.md` appears in /resume's own SKILL.md prose, which
    # this script's argument carries through VERBATIM — no count here on
    # purpose: it read "twice" against a MEASURED 4 occurrences on 3 lines, and
    # a number nothing enforces is one edit from being wrong again. A token
    # carrying a shell metacharacter is dropped from the miss bookkeeping
    # instead — the run degrades to the ordinary no-match path it took before
    # the scan existed.
    case "$tok" in
      *'*'*|*'?'*|*'['*) continue ;;
    esac
    # Shaped like a handoff reference, but not on disk. Remember the FIRST such
    # token: it is the caller's stated intent, and the run is about to ignore it.
    # Its candidate set (empty unless the search above found SEVERAL) travels
    # with it, so the gap can say what it saw rather than only that it failed.
    [ -n "$miss" ] || { miss="$tok"; ambig="$amb"; }
  done
  [ -n "$noglob" ] && set +f
  if [ -n "$hit" ]; then printf '%s\n' "$hit"; return 0; fi
  [ -n "$miss" ] || return 1
  if [ -n "$ambig" ]; then printf '%s\n%s\n' "$miss" "$ambig"; return 3; fi
  printf '%s\n' "$miss"
  return 2
}

resolve(){
  local arg="${1:-}" path="" unresolved="" named_missing="" named_ambig="" fam="" rc=0
  if [ -n "$arg" ]; then
    if [ -f "$arg" ]; then path="$arg"              # explicit handoff path
    else                                            # …or one quoted inside prose
      path=$(embedded_md_path "$arg"); rc=$?
      # rc 2 = a handoff path WAS named and is not on disk. Keep it: the gap
      # below owes the caller that fact regardless of what the fallback finds.
      # rc 3 = the same, PLUS the several worktree copies that were found and
      # deliberately not chosen between. Line 1 is the token, the rest are paths.
      [ "$rc" -eq 3 ] && {
        named_missing=${path%%$'\n'*}; named_ambig=${path#*$'\n'}; path=""; }
      [ "$rc" -eq 2 ] && { named_missing="$path"; path=""; }
      [ "$rc" -eq 1 ] && path=""
    fi
  fi
  if [ -n "$path" ]; then
    HANDOFF=$(realpath "$path")
    REPO=$(git -C "$(dirname "$HANDOFF")" rev-parse --show-toplevel 2>/dev/null) \
      || REPO=$(dirname "$HANDOFF")
  else
    REPO=$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null) || REPO="$PWD"
    # 🔴 A HANDOFF THAT WAS NAMED AND IS NOT THERE DOES NOT FALL BACK — the
    # whole chain below is skipped and HANDOFF stays EMPTY.
    #
    # `named_missing` used to be recorded and then IGNORED: the fallbacks ran
    # anyway, so the run printed the gap AND a complete, confident digest —
    # GIT/PR state, referenced PR states, a CLAWGATE block and DRIFT findings —
    # about a document nobody asked for. MEASURED 2026-08-31 (#1164): naming a
    # doc that lives in a linked worktree produced a full reconciliation of an
    # unrelated initiative, and a reader who skims to DRIFT (which is what the
    # /resume skill tells them to read) gets findings about the wrong work. The
    # gap line is honest and it is not enough; the digest under it is the harm.
    #
    # 🔴 SCOPED TO `named_missing`, NOT TO `unresolved`. A bare basename or a
    # topic slug is not a claim about a FILE — `resume-state.sh
    # handoff-alpha-2026-01-01.md` in a repo holding exactly that doc is a
    # MEASURED case where the fallback serves precisely what the reader wanted,
    # and `resume-state.sh session` in civitai-manager is blessed by name 30
    # lines below. Widening this guard to `unresolved` would break both. Only a
    # handoff-SHAPED path (see `embedded_md_path`) sets `named_missing`.
    #
    # No exit code and no refusal: the script has never had one and every caller
    # would have to learn it. An empty HANDOFF already routes to the existing,
    # tested branch that says NOTHING was reconciled.
    if [ -z "$named_missing" ]; then
      if [ -n "$arg" ]; then                          # topic slug
        HANDOFF=$(ls -t "$REPO"/claudedocs/handoff-"$arg"*.md 2>/dev/null | head -1)
        # An argument was SUPPLIED and did not resolve. Everything below this point
        # is a fallback, and a fallback is only silent-safe with NO argument, where
        # "newest" IS the contract. Remember that here, before the fallbacks run.
        [ -z "$HANDOFF" ] && unresolved=1
      fi
      # `fam` records WHICH glob produced the answer, because the fallback only
      # ever chooses WITHIN one family — the caps glob is not even reached while
      # the lowercase one matches. Counting the union instead says "the newest of
      # 2 … MOVES between runs" over a repo holding one lowercase and one caps
      # doc, where the lowercase glob has exactly one member and the choice is
      # therefore DETERMINISTIC. That sentence would be false, which the rule
      # below forbids.
      if [ -z "$HANDOFF" ]; then
        HANDOFF=$(ls -t "$REPO"/claudedocs/handoff-*.md 2>/dev/null | head -1)
        [ -n "$HANDOFF" ] && fam='handoff-*.md'
      fi
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
      if [ -z "$HANDOFF" ]; then
        HANDOFF=$(ls -t "$REPO"/claudedocs/*HANDOFF*.md 2>/dev/null | head -1)
        [ -n "$HANDOFF" ] && fam='*HANDOFF*.md'
      fi
    fi
    # 🔴 AN ARGUMENT THAT RESOLVED NOTHING ALWAYS WARNS. THE COUNT ONLY DECIDES
    # WHETHER ONE EXTRA CLAUSE IS TRUE.
    #
    # This is the rule the previous two rounds each applied to ONE input class
    # and not the other, narrowing the warning both times:
    #
    #   the count answers  "did the fallback have to CHOOSE?"
    #   it cannot answer   "did the caller name something the tool overrode?"
    #
    # Keying the whole warning on the count made a one-handoff repo swallow an
    # explicit-path miss (fixed by `named_missing`), and then — same shape, other
    # class — swallow a SLUG that matched nothing whenever the resolving family
    # held one file. That second one is issue #684's own reproduction, silent
    # again: a supplied topic, no match, a different document reconciled under
    # "(none detected — live state matches the handoff's claims)".
    #
    # So the miss is reported unconditionally, and the "newest of N … MOVES
    # between runs" clause — which is only TRUE when something was discarded —
    # is appended only when the resolving family holds >=2. That keeps the rule
    # this block is built on: EVERY CLAUSE MUST BE TRUE OF EVERY RUN THAT
    # REACHES IT. (An earlier message claimed "no .md path was quoted in it
    # either", which was false whenever one was quoted and merely failed a
    # filter; the test that "passed" did so only because `$arg` is echoed back.)
    #
    # A no-argument run sets neither flag and stays silent — there, newest IS
    # the contract, and that is the whole reason this is not unconditional.
    #
    # ⚠ ONE APPEND SITE, ON PURPOSE. Two sites produced two near-duplicate lines
    # for a single cause (the named path AND the generic "nothing resolved",
    # both naming the same file), which reads as a duplicated gap and invites
    # exactly the "is the count wrong?" question an audit then has to spend a
    # round on. One cause, one line — and the `!! GAPS (N)` header cannot
    # disagree with what is printed, because N is the array length and this is
    # the only thing that grows it here.
    if [ -n "$named_missing" ] || [ -n "$unresolved" ]; then
      # Count within the family that ACTUALLY RESOLVED (see `fam` above) — the
      # fallback never chooses across families, so the union would overstate.
      local n_cand=0 lead rest moves=""
      local n_amb=0 n_shown=0 list_amb="" cand
      [ -n "$fam" ] && n_cand=$(ls -t "$REPO"/claudedocs/$fam 2>/dev/null | grep -c .)
      # The ambiguous lead REPLACES the plain named-missing one rather than
      # adding a second line — one cause, one line, per the note above. It says
      # only what was measured: the token is not a file, that basename exists at
      # exactly these paths, and nothing was chosen. No clause here claims which
      # of them the caller meant, because nothing here knows.
      if [ -n "$named_ambig" ]; then
        # ⚠ THE ENUMERATION IS CAPPED; THE COUNT IS NOT. MEASURED 2026-08-31 on
        # this host's own devrc clone: 142 linked worktrees, with ONE handoff
        # basename present in 28 of them. Listing all 28 makes a ~2.5 KB single
        # line inside the one block whose entire job is to be read — the same
        # way a permanently-red gate destroys the gate. Four is enough to choose
        # from. The "and N more" clause is appended ONLY when there are more, so
        # every clause stays true of every run that reaches it; `$n_amb` is
        # always the real total, so the count never shrinks with the list.
        #
        # 🔴 WHICH FOUR IT SHOWS IS NOT ARBITRARY. This sentence's own advice is
        # "pass the worktree's own path", so four paths nobody would ever pass
        # is the least actionable list it could print. MEASURED 2026-09-01 on
        # this host's real devrc clone: `handoff-discord-embed-ext-rescue.md`
        # exists in 28 worktrees, 27 of them EPHEMERAL agent checkouts under
        # `.claude/worktrees/agent-*`, and exactly ONE human-named
        # (`devrc-handoff-cairn`) — the only candidate anyone would ever pass.
        #
        # ⚠ SAY WHAT THE MEASUREMENT ACTUALLY SHOWED, because the two halves of
        # this fix interact and the obvious story is wrong. Under the AMBIENT
        # `en_US.UTF-8` — which is what the unpinned `sort` above used to
        # collate with — `devrc-handoff-cairn` came back at position **28 of
        # 28** and was hidden inside `and 24 more`. Under the `LC_ALL=C` now
        # pinned above it sorts FIRST, so that particular instance is already
        # shown without this pass. This pass is still the structural fix: C
        # order puts `<repo>/.claude/…` above any sibling whose name sorts after
        # `<repo>/`, so a human worktree that happens to be named that way is
        # hidden again, and nothing about a sort order makes a disposable
        # checkout a better suggestion than a real one.
        #
        # Human-named worktrees are enumerated FIRST; within each class the
        # `LC_ALL=C sort` order from `worktrees_holding` is preserved, so a
        # candidate set with no agent checkouts in it produces exactly the list
        # it did before. `$n_amb` is counted over EVERY candidate in the first
        # pass, so the count is untouched by the reordering.
        local pref_amb="" eph_amb=""
        while IFS= read -r cand; do
          [ -n "$cand" ] || continue
          n_amb=$((n_amb + 1))
          case "$cand" in
            */.claude/worktrees/agent-*) eph_amb="$eph_amb$cand"$'\n' ;;
            *)                           pref_amb="$pref_amb$cand"$'\n' ;;
          esac
        done <<<"$named_ambig"
        while IFS= read -r cand; do
          [ -n "$cand" ] || continue
          [ "$n_shown" -lt 4 ] || break
          list_amb="${list_amb:+$list_amb, }$cand"; n_shown=$((n_shown + 1))
        done <<<"$pref_amb$eph_amb"
        [ "$n_amb" -gt "$n_shown" ] \
          && list_amb="$list_amb, and $((n_amb - n_shown)) more"
        # 🔴 "of that clone" WAS NOT TRUE OF EVERY RUN THAT REACHED HERE, which
        # is what the rule above this block forbids. The candidates come from
        # whichever clone the search used — `<X>`'s for an `<X>/claudedocs/…`
        # token, the cwd's for a re-anchored relative one — and "that clone"
        # reads as "the clone you named", which a bare `claudedocs/<base>` token
        # never named at all. "the clone that path resolves against" is true of
        # all three shapes and names the same thing in each.
        lead="requested handoff \"$named_missing\" — NO SUCH FILE, and $(basename "$named_missing") exists in $n_amb worktrees of the clone that path resolves against ($list_amb), so NONE was chosen."
      elif [ -n "$named_missing" ]; then
        lead="requested handoff \"$named_missing\" — NO SUCH FILE (renamed, moved, or in another checkout?)."
      else
        lead="requested \"$arg\" — nothing in it resolved to a handoff doc under $REPO/claudedocs."
      fi
      if [ -z "$HANDOFF" ]; then
        rest=" NOTHING was reconciled; the DRIFT section below is about no document at all."
      else
        [ "$n_cand" -gt 1 ] && moves=" It is the newest of $n_cand, and which one that is depends on commit times, so it MOVES between runs."
        # 🔴 MECHANICAL CLAIMS ONLY. This used to add "a DIFFERENT document from
        # the one you asked for, so nothing below is scoped to what was asked
        # for" — an IDENTITY claim the tool has no evidence for, and FALSE in the
        # shape the fallback chain exists to serve. Measured:
        #
        #   resume-state.sh handoff-alpha-2026-01-01.md   (a bare basename, which
        #   is what a user pastes) in a repo holding exactly that doc printed
        #   "FELL BACK to handoff-alpha-2026-01-01.md, a DIFFERENT document from
        #   the one you asked for" — the same filename on both sides of the
        #   sentence, called different, over a digest that had reconciled
        #   precisely what the reader wanted;
        #
        #   resume-state.sh session   in civitai-manager, whose only doc IS
        #   SESSION-HANDOFF.md — the invocation blessed by name 30 lines above.
        #
        # Same class as the retired "no .md path was quoted in it either", and it
        # broke the rule stated at the top of this block. The reader already has
        # the `handoff:` line and the "nothing in it resolved" clause; what they
        # do NOT have is a tool claiming to know which document they meant.
        rest=" The digest FELL BACK to $(basename "$HANDOFF").$moves Re-run naming the doc's path, or with no argument to take newest deliberately."
      fi
      UNRECONCILED+=("$lead$rest")
    fi
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
#   HANDOFF_REF     the ref the authoritative text came FROM, or "" for the
#   HANDOFF_REL     working tree — with its repo-relative path. Set ONLY on the
#                   stale-branch path, i.e. only when the text did not come from
#                   the file on disk. Anything that needs to DATE the text it
#                   read has to know this: the local branch's `git log` is the
#                   history of a copy nobody reconciled. See clawgate_block.
#
# Which copy wins is decided by a fact, not a heuristic: if the working-tree
# file is UNMODIFIED relative to HEAD, then any difference from origin is the
# branch being behind, and origin is authoritative. If it carries uncommitted
# edits, it is this session's work-in-progress and stays authoritative — but
# loudly, because reconciling against unpushed text is its own trap.
HANDOFF_TEXT="" HANDOFF_NOTE="" HANDOFF_ALT="" HANDOFF_REF="" HANDOFF_REL=""

# --- shared with skill_block: ONE fetch policy, ONE default-branch rule -----
#
# 🔴 ONE RULE, ONE PLACE. Two consumers now need "bring origin up to date,
# bounded, never interactive" and "which ref is origin's default branch" — and a
# predicate open-coded at two sites is wrong at one of them sooner or later. The
# fetch is additionally MEMOISED per directory: `handoff_freshness` and
# `skill_block` usually name the same checkout, and a resume must not pay two
# network round-trips to answer one question. The memo stores the RESULT, not
# merely "attempted", because a second caller reading a cached 0 over a fetch
# that FAILED would print "compared against fresh refs" about refs that are
# whatever was already on disk.
#
#   rc 0  fetched (or already fetched this run)
#   rc 1  fetch attempted and failed
#   rc 2  fetch deliberately skipped ($RESUME_STATE_SKIP_FETCH)
declare -A FETCH_RC=()
bounded_fetch(){
  local d="$1"
  # 🔴 AN EMPTY DIRECTORY IS AN EMPTY ARRAY SUBSCRIPT, AND BASH TREATS THAT AS
  # AN ERROR — `FETCH_RC[]: bad array subscript`, twice, and the run exits 1.
  # A digest must NEVER fail a resume over its own bookkeeping. Not reachable
  # today (every caller guards `$d` first), which is exactly why it needs to be
  # here: found by a mutant that removed one of those guards, where the whole
  # script died instead of falling through to the next check.
  [ -n "$d" ] || return 1
  [ -n "${FETCH_RC[$d]:-}" ] && return "${FETCH_RC[$d]}"
  local rc=0
  if [ -n "${RESUME_STATE_SKIP_FETCH:-}" ]; then
    rc=2
  else
    # Bounded, non-interactive, and never a prompt. A fetch that cannot complete
    # must cost seconds, not a hung resume.
    GIT_TERMINAL_PROMPT=0 \
    GIT_SSH_COMMAND='ssh -oBatchMode=yes -oConnectTimeout=5 -oStrictHostKeyChecking=accept-new -oServerAliveInterval=30 -oServerAliveCountMax=6' \
      timeout 25 git -C "$d" fetch --quiet origin >/dev/null 2>&1 || rc=1
  fi
  FETCH_RC[$d]=$rc
  return "$rc"
}

# origin's default branch for $1, or "" when it cannot be determined.
default_branch(){
  local d="$1" db c
  db=$(git -C "$d" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)
  db=${db#origin/}
  if [ -z "$db" ]; then
    for c in trunk main master; do
      git -C "$d" rev-parse --verify -q "origin/$c" >/dev/null 2>&1 && { db="$c"; break; }
    done
  fi
  printf '%s' "$db"
}

handoff_freshness(){
  [ -n "$HANDOFF" ] || return 0
  HANDOFF_TEXT=$(cat "$HANDOFF")
  local d="$REPO"
  git -C "$d" rev-parse --git-dir >/dev/null 2>&1 || {
    HANDOFF_NOTE="working-tree copy — origin freshness UNCHECKED (not a git repo)"; return 0; }
  git -C "$d" remote get-url origin >/dev/null 2>&1 || {
    HANDOFF_NOTE="working-tree copy — origin freshness UNCHECKED (no origin remote)"; return 0; }

  bounded_fetch "$d"
  case $? in
    1) HANDOFF_NOTE="[fetch failed; compared against refs already on disk] " ;;
    2) HANDOFF_NOTE="[fetch skipped; compared against refs already on disk] " ;;
  esac

  local db
  db=$(default_branch "$d")
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
    # …and record WHERE that text came from, because a consumer that dates the
    # doc must date the copy it actually read: the local branch's log describes
    # the stale file this line just replaced.
    HANDOFF_REF="$ref" HANDOFF_REL="$rel"
    HANDOFF_NOTE="${HANDOFF_NOTE}🔴 $ref copy (the working-tree copy is STALE: ${ln_local} lines local vs ${ln_remote} on $ref)"
    DRIFT+=("handoff doc in the working tree is STALE vs $ref (${ln_local} vs ${ln_remote} lines) — this digest reconciled the $ref copy, readable at $alt; READ THAT ONE, the local file is not what the last session wrote")
  else
    HANDOFF_NOTE="${HANDOFF_NOTE}⚠ working-tree copy, which has UNCOMMITTED edits and differs from $ref (${ln_local} lines local vs ${ln_remote} on $ref)"
    UNRECONCILED+=("handoff doc has uncommitted local edits and differs from $ref (${ln_local} vs ${ln_remote} lines) — reconciled the LOCAL copy; the $ref text is at $alt")
  fi
}

# ---------------------------------------------------------------------------
# SKILL FRESHNESS — are the INSTRUCTIONS this session is executing current?
#
# 🔴 A SKILL IS LOADED ONCE, FROM THE DEPLOYED COPY, AND NEVER RE-READ.
# `/resume` reads `~/.claude/skills/resume/SKILL.md`, which nix manages: it is a
# symlink into either the working tree (`mkOutOfStoreSymlink`) or a /nix/store
# copy written by the last `home-manager switch`. Neither tracks `origin/main`,
# and `git pull` moves NEITHER. So an agent can execute a superseded procedure
# for a whole session with nothing able to say so.
#
# Measured 2026-08-25: a session invoked `/resume` at 17:19 and carried out its
# step 6 — claim a work item with `gh pr list`, push the branch early. That step
# had been replaced by `scripts/claim-work.sh` in #847 ("claim-work is the lock.
# This is a COMMAND, not a habit."). The session followed the old text and never
# noticed, because nothing in the system compares the loaded copy to origin.
#
# 🔴 THE OPERAND IS THE DEPLOYED FILE, NOT THE WORKING TREE. The working-tree
# copy is not what Claude read; comparing it would answer a question nobody
# asked. And WHICH of the two the deployed path is must be settled by
# `readlink -f` — the repo's own rule — never by a diff: byte-identical files
# prove nothing there, because identity can simply mean they are ONE file.
#
# 🔴 SCOPE, STATED HONESTLY: this runs at resume START. It catches "the copy you
# loaded was ALREADY behind". It cannot catch a skill change that lands MID-
# session — which is exactly what happened above (#847 landed 2h20m after that
# session began). That case remains open.
#
# Every way this can fail to measure emits a `!` GAP and prints COULD NOT
# MEASURE. It must never print a reassuring all-clear it did not earn: the
# defect being closed here IS a silent pass.
skill_block(){
  echo "SKILL"
  local name
  # 🔴 `${x-default}`, NOT `${x:-default}`. UNSET means "nobody overrode it, so
  # check /resume" — the default that makes this fire without being asked. SET
  # BUT EMPTY means "check none", an explicit act with an explicit line printed
  # for it. Collapsing those two with `:-` would make "check nothing" impossible
  # to express, and every hermetic caller would have to fake a ~/.claude.
  #
  # ONE site, deliberately. Spelling `${RESUME_STATE_SKILL-resume}` twice made
  # the second copy's `-`-vs-`:-` UNOBSERVABLE — the guard below always
  # returned first — so a mutant of it survived the whole suite. A predicate
  # that cannot disagree with itself is a predicate nobody has to keep right.
  local want="${RESUME_STATE_SKILL-resume}"
  if [ -z "$want" ]; then
    echo "  skill-read: (RESUME_STATE_SKILL is empty — NO skill was checked; the instructions this session is executing were not compared against origin)"
    return
  fi
  # Unquoted on purpose: the override is a space-separated list of skill names,
  # so other skills can borrow this check. bash word-splits it; there is no
  # glob-shaped skill name, and `set -f` is not in force here.
  for name in $want; do skill_freshness "$name"; done
}

skill_freshness(){
  local name="$1"
  local cdir="${RESUME_STATE_CLAUDE_DIR:-$HOME/.claude}"
  local dep="$cdir/skills/$name/SKILL.md"
  local rel="claude/skills/$name/SKILL.md"

  # 🔴 `-e` FOLLOWS SYMLINKS, so it is FALSE for a DANGLING one — and a dangling
  # managed link is a live failure mode here (2026-08-11: 46 of 139 on the
  # laptop, all into a garbage-collected /nix/store path, over a clean
  # checkout). Left as `[ ! -e ]` alone, that case reported "no deployed copy",
  # which reads as "this skill was never deployed" and sends the reader to
  # entirely the wrong fix. `-L` separates them.
  if [ ! -e "$dep" ] && [ ! -L "$dep" ]; then
    printf '  skill-read: %s — COULD NOT MEASURE (no deployed copy at %s)\n' "$name" "$dep"
    UNRECONCILED+=("the /$name skill has no deployed copy at $dep, so the instructions this session is executing were NOT compared against origin — their age is UNKNOWN")
    return
  fi

  # 🔴 `readlink -f` IS THE ARBITER, and it answers two questions at once: does
  # the path resolve at all (a dangling link into a GC'd store path is a live
  # failure mode on these hosts), and WHERE does it terminate — which is a
  # THREE-way answer, not two: a checkout (mkOutOfStoreSymlink, live), /nix/store
  # (a home.file copy, replaced only by a switch), or NEITHER.
  local real
  real=$(readlink -f "$dep" 2>/dev/null)
  if [ -z "$real" ] || [ ! -f "$real" ]; then
    printf '  skill-read: %s — COULD NOT MEASURE (%s resolves nowhere)\n' "$name" "$dep"
    UNRECONCILED+=("the /$name skill's deployed path $dep resolves nowhere — a dangling symlink into a garbage-collected /nix/store path? Whatever this session loaded, its age could not be measured")
    return
  fi

  local d="" live=0 cand
  d=$(git -C "$(dirname "$real")" rev-parse --show-toplevel 2>/dev/null) || d=""
  if [ -n "$d" ]; then
    live=1                                   # mkOutOfStoreSymlink into a checkout
  else
    # A store copy carries no history of its own, so the comparison needs a
    # checkout of the source. Named explicitly, then the canonical handle, then
    # the conventional path — and if none of them is a git repo, that is a GAP,
    # not a default.
    for cand in "${RESUME_STATE_SKILL_REPO:-}" "${DEVRC:-}" "$HOME/workspace/devrc"; do
      [ -n "$cand" ] || continue
      d=$(git -C "$cand" rev-parse --show-toplevel 2>/dev/null) && break
      d=""
    done
  fi
  # 🔴 THREE PROVENANCES, because the ACTION differs and a two-way label told
  # one of them to do something that cannot work. A path that resolves neither
  # into a checkout nor into /nix/store is a FOREIGN file someone hand-placed —
  # the new-host case — and CLAUDE.md is explicit that `home.file.force` does
  # NOT clobber one. Calling it a "store copy … only a home-manager switch
  # replaces it" sends the reader to run a switch that will leave the file
  # exactly where it is (or fail), and the comparison below still reports it
  # against origin as though nix had put it there.
  #
  # 🔴 TWO PARTS, because they are spliced into sentences with different shapes.
  # `$prov` is a NOUN PHRASE WITH ITS ARTICLE — it goes inside other people's
  # clauses, including a `COULD NOT MEASURE (…)` parenthetical, so it must carry
  # no parentheses of its own and must read correctly after "is". `$prov_note`
  # is the REMEDY, a sentence in its own right, appended only where there is room
  # for one. Fused into a single string they produced "…is a UNMANAGED file at
  # X — …(home.file.force does not clobber a foreign file); remove it and
  # re-switch and no git checkout of its source could be found", i.e. nested
  # parens inside the parenthetical and an imperative spliced mid-clause.
  local prov prov_note=""
  if [ "$live" -eq 1 ]; then
    prov="the live working-tree copy at $real"
  else
    case "$real" in
      /nix/store/*)
        prov="a store copy at $real"
        prov_note="only a home-manager switch replaces it" ;;
      *)
        prov="an UNMANAGED file at $real"
        prov_note="neither a checkout nor /nix/store, so home-manager will NOT replace it — home.file.force does not clobber a foreign file, so remove it and re-switch" ;;
    esac
  fi
  local prov_full="$prov${prov_note:+ — $prov_note}"
  if [ -z "$d" ]; then
    printf '  skill-read: %s — COULD NOT MEASURE (no git checkout of the skill source found; %s)\n' "$name" "$prov"
    UNRECONCILED+=("the /$name skill's deployed copy is $prov and no git checkout of its source could be found (tried \$RESUME_STATE_SKILL_REPO, \$DEVRC, ~/workspace/devrc) — its age against origin is UNKNOWN")
    return
  fi

  if ! git -C "$d" remote get-url origin >/dev/null 2>&1; then
    printf '  skill-read: %s — COULD NOT MEASURE (%s has no origin remote; %s)\n' "$name" "$d" "$prov"
    UNRECONCILED+=("the /$name skill could not be compared: $d has no origin remote — the deployed instructions' age is UNKNOWN")
    return
  fi

  local pre=""
  bounded_fetch "$d"
  case $? in
    1) pre="[fetch failed; compared against refs already on disk] " ;;
    2) pre="[fetch skipped; compared against refs already on disk] " ;;
  esac

  local db
  db=$(default_branch "$d")
  if [ -z "$db" ]; then
    printf '  skill-read: %s%s — COULD NOT MEASURE (no origin/<default-branch> ref in %s; %s)\n' "$pre" "$name" "$d" "$prov"
    UNRECONCILED+=("the /$name skill could not be compared: $d has no origin/<default-branch> ref — the deployed instructions' age is UNKNOWN")
    return
  fi
  local ref="origin/$db"

  # On the live path the tracked name is asked of git rather than assumed: a
  # checkout may hold the skill somewhere this constant does not name.
  if [ "$live" -eq 1 ]; then
    local tracked
    tracked=$(git -C "$d" ls-files --full-name --error-unmatch -- "$real" 2>/dev/null) \
      && rel="$tracked"
  fi

  # 🔴 PROVE THE PATH EXISTS AT THE REF FIRST — a comparison against an absent
  # operand reports SAME, not MISSING.
  if ! git -C "$d" cat-file -e "$(printf '%s:%s' "$ref" "$rel")" 2>/dev/null; then
    printf '  skill-read: %s%s — COULD NOT MEASURE (%s is not on %s; %s)\n' "$pre" "$name" "$rel" "$ref" "$prov"
    UNRECONCILED+=("the /$name skill could not be compared: $rel does not exist on $ref — the deployed instructions' age is UNKNOWN")
    return
  fi

  local dep_hash tip_hash
  dep_hash=$(git -C "$d" hash-object -- "$real" 2>/dev/null)
  tip_hash=$(git -C "$d" rev-parse "$(printf '%s:%s' "$ref" "$rel")" 2>/dev/null)
  if [ -z "$dep_hash" ] || [ -z "$tip_hash" ]; then
    printf '  skill-read: %s%s — COULD NOT MEASURE (could not hash the deployed copy or the %s blob)\n' "$pre" "$name" "$ref"
    UNRECONCILED+=("the /$name skill could not be compared: git could not hash the deployed copy or the $ref blob for $rel — the deployed instructions' age is UNKNOWN")
    return
  fi
  if [ "$dep_hash" = "$tip_hash" ]; then
    printf '  skill-read: %s%s — deployed copy is CURRENT with %s (%s)\n' "$pre" "$name" "$ref" "$prov"
    return
  fi

  # The deployed copy differs. On the LIVE path that can mean this session's own
  # uncommitted edits, which is not staleness — same fork the handoff check
  # makes, and it must not be called STALE.
  #
  # ⚠ IT GOES TO `UNRECONCILED`, AND THAT IS A DELIBERATE IMPRECISION, RECORDED
  # RATHER THAN FIXED. An audit is right that the gap banner reads "SOURCES THAT
  # DID NOT ANSWER" while this source answered perfectly well; it is a caveat,
  # not a silence. It stays because `handoff_freshness` files its IDENTICAL case
  # ("handoff doc has uncommitted local edits …") in the same array, and moving
  # only this one would make two instances of one situation behave differently —
  # the disagreement is worse than the imprecision. It also errs safe: the run
  # withholds the all-clear while you are executing unpushed instructions. If
  # the banner is ever split into "did not answer" vs "answered with a caveat",
  # BOTH sites move together.
  if [ "$live" -eq 1 ] && ! git -C "$d" diff --quiet -- "$rel" 2>/dev/null; then
    printf '  skill-read: %s⚠ %s — deployed copy is the working tree, which has UNCOMMITTED edits and differs from %s\n' "$pre" "$name" "$ref"
    UNRECONCILED+=("the /$name skill you are executing has UNCOMMITTED local edits and differs from $ref — these instructions are unpushed, so nobody else is running them")
    return
  fi

  # DIRECTION AND SIZE, not merely "differs": walk the commits that touched this
  # path on $ref and find the one whose blob the deployed copy IS. Its index is
  # how many commits the deployed copy is behind FOR THIS PATH.
  #
  # The walk is CAPPED so a long-lived path cannot turn a resume into a
  # thousand `rev-parse` calls. Hitting the cap is NOT a clean answer — it means
  # "older than the newest N", printed as such. The cap is env-overridable
  # purely so the capped branch is reachable from a test with a 3-commit
  # fixture; a guard no test can reach is a guard nobody has watched work.
  # 🔴 VALIDATE ONCE, BEFORE THE LOOP. `[ "$scanned" -gt "$cap" ]` with a
  # non-numeric cap prints `[: abc: integer expected` to STDERR **once per
  # commit walked** — the only unredirected stderr in this block — and then
  # treats the test as false, so the cap silently does not apply. A digest that
  # scribbles on stderr is one a caller cannot cleanly capture. Fall back to the
  # default and say so as a gap, rather than half-applying a value nobody meant.
  #
  # 🔴 `0*`, NOT `0` — A LEADING ZERO IS THE SAME HAZARD AS A ZERO. `[` reads
  # its operands as decimal, so `00` and `007` are ACCEPTED by a `|0)` arm and
  # `[ 1 -gt 00 ]` is TRUE: the walk caps on the very first commit, which is
  # exactly the state the zero arm exists to prevent, and the digest then prints
  # "older than the newest 00 commit(s)". Measured. `run-tests.sh` rejects
  # leading zeros for the same reason; this matches it. `0*` covers `0`, `00`
  # and `007` in one pattern and leaves `200` alone.
  local cap="${RESUME_STATE_SKILL_SCAN_CAP:-200}"
  case "$cap" in
    ''|*[!0-9]*|0*)
      UNRECONCILED+=("RESUME_STATE_SKILL_SCAN_CAP=$cap is not a positive integer without a leading zero — the /$name skill's history walk used the default 200 instead")
      cap=200 ;;
  esac
  local behind=0 found="" capped="" c h scanned=0
  while read -r c; do
    scanned=$((scanned+1))
    if [ "$scanned" -gt "$cap" ]; then capped=1; break; fi
    h=$(git -C "$d" rev-parse -q --verify "$(printf '%s:%s' "$c" "$rel")" 2>/dev/null) || h=""
    if [ "$h" = "$dep_hash" ]; then found="$c"; break; fi
    behind=$((behind+1))
  #
  # ⚠ NO `--follow`, RECORDED RATHER THAN FIXED. A rename of the skill path
  # truncates this walk at the rename, so a deployed copy older than the rename
  # reports "matches NO commit" instead of a number. That degrades the report
  # from "N behind" to "not current, provenance unknown" — never to a false
  # CURRENT, because the tip-hash comparison above returns before the walk is
  # reached. `--follow` buys the number at the cost of git's rename heuristics
  # deciding which file this is, on a path that moves roughly never.
  #
  # 🔴 THE CLAIM THIS COMMENT USED TO MAKE WAS FALSE, which is worse than making
  # none: it named a test that exercised NO rename. Its fixture did
  # `git mv REL REL.tmp` and back in one commit, which git records as a plain
  # `M` — the walk was never truncated, the output was the ordinary
  # "2 commit(s) BEHIND", and the only assertion ("CURRENT" absent) passed for
  # that reason. The real shape is a file that ORIGINATES at another path and is
  # renamed in; `test_a_renamed_skill_path_truncates_the_walk_never_to_CURRENT`
  # builds that and asserts the truncation is what happens.
  done < <(git -C "$d" log --format=%H "$ref" -- "$rel" 2>/dev/null)

  local newest
  newest=$(git -C "$d" log -1 --format='%h %s' "$ref" -- "$rel" 2>/dev/null)
  local howto="read it with: git -C $d show $ref:$rel"

  if [ -n "$found" ]; then
    printf '  skill-read: %s🔴 %s — deployed copy is %s commit(s) BEHIND %s for %s (newest it lacks: %s) [%s]\n' \
      "$pre" "$name" "$behind" "$ref" "$rel" "$newest" "$prov_full"
    DRIFT+=("the /$name skill THIS SESSION IS EXECUTING is STALE: the deployed copy at $dep is $behind commit(s) behind $ref for $rel (newest it lacks: $newest) — $howto and follow THAT text, not the loaded one; only a home-manager switch replaces the deployed copy")
  elif [ -n "$capped" ]; then
    # 🔴 SAY WHAT YOU MEASURED. The cap means the WALK ran out of budget, which
    # is a fact about this scan and about nothing else: the deployed copy may be
    # an ancient release, and the distance is simply unknown. An earlier
    # revision routed this case through the "built from a tree that was never
    # pushed" sentence below — a cause it had not measured and mostly does not
    # hold. Not stale-vs-current either way: it is NOT current, and by an
    # amount this run declined to compute.
    #
    # 🔴 "OLDER THAN" AND "RAISE THE CAP FOR THE NUMBER" WERE BOTH FALSE, in the
    # same reachable case. MEASURED: 5 commits touch the path, the deployed
    # content was never committed anywhere, cap=2 → this branch. The copy is not
    # "older than" those commits — it is not in that path's history AT ALL — and
    # raising the cap to 500 does not yield a distance, it yields
    # "matches NO commit", i.e. no number, ever. A hedge that promises nothing
    # is fine; one that promises something unobtainable sends the reader to run
    # a command that cannot answer.
    #
    # So both halves now state only the measurement: this run looked at the
    # newest $cap commit(s) and did not find it there.
    local how="was NOT among the newest $cap commit(s) touching $rel on $ref"
    printf '  skill-read: %s🔴 %s — deployed copy %s; the scan stopped at its cap, so its DISTANCE was not measured; newest on %s: %s [%s]\n' \
      "$pre" "$name" "$how" "$ref" "$newest" "$prov_full"
    # ⚠ NAMES THE EFFECTIVE CAP, NOT THE ENV VAR. Printing
    # `RESUME_STATE_SKILL_SCAN_CAP=$cap` here read `=200` on a run where the
    # variable was UNSET, and `=200` again on a run where it was `abc` — while
    # the gap line two lines away correctly quoted `=abc`. One run, two numbers,
    # both presented as the setting. And it names $dep, like its two siblings.
    DRIFT+=("the /$name skill THIS SESSION IS EXECUTING $how — the deployed copy at $dep is NOT current, and this run stopped at its cap without searching further; raising RESUME_STATE_SKILL_SCAN_CAP widens the search, which may yield a distance or may report that no commit matches at all; $howto and follow THAT text, not the loaded one")
  else
    # The walk went all the way back and found nothing: the deployed content is
    # not any commit of this path on $ref.
    #
    # 🔴 THE CAUSE IS HEDGED, because THREE shapes reach here and only one is
    # the obvious one. (a) built from an uncommitted tree; (b) built from a
    # branch that is PUSHED but not merged — which this repo's own CLAUDE.md
    # recommends, since `home-manager switch --flake ~/workspace/devrc` is how
    # you validate a nix edit end to end; (c) THE CONTENT PREDATES A RENAME of
    # this path, so the walk above cannot see it (no `--follow`, see the note
    # there) — and in that case the content IS on $ref, at another path.
    #
    # Each successive revision of this sentence has been false of the case
    # discovered next: "was built from a tree that was never pushed" was false
    # of (b), and "was built from a tree that is not on $ref" is false of (c).
    # So the wording now names the OBSERVATION (this walk did not find it) and
    # lists the causes as possibilities, rather than asserting any of them.
    local how="matches NO commit of $rel on $ref"
    printf '  skill-read: %s🔴 %s — deployed copy %s; newest on %s: %s [%s]\n' \
      "$pre" "$name" "$how" "$ref" "$newest" "$prov_full"
    DRIFT+=("the /$name skill THIS SESSION IS EXECUTING $how — this walk could not place the deployed copy at $dep in that path's history, so what you loaded is not what $ref says today; it may be uncommitted, on a branch that has not merged, or older than a rename of this path (the walk has no --follow); $howto to compare")
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
      # `$text` — the SAME copy the refs above were extracted from. It used to
      # pass "$HANDOFF", so the refs came from the authoritative text and their
      # framing from whatever happened to be on disk.
      if [ "$state" = MERGED ] && handoff_says_inflight "$num" "$text" "$([ "$slug" = "-" ] || printf '%s' "$slug")"; then
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
# CLAWGATE — reconcile the handoff's recorded task against the LIVE board.
#
# /handoff records `clawgate-task: <id>` as YAML front matter at the top of the
# doc (see claude/skills/handoff/SKILL.md and scripts/lib/clawgate_handoff.sh).
# This reads it back and asks the board two questions: what STATUS does the task
# carry now, and how many comments POSTDATE the doc?
#
# 🔴 EVERY WAY THIS CAN FAIL PRINTS A `!` GAP, because the alternative is the
# false green this whole script exists to avoid. `clawgatectl` missing, a 401, a
# task that does not exist and a server that dropped the field all produce the
# same observable — nothing to reconcile — and reporting that as "no drift"
# states a fact about the board that was never measured.
#
# ⚠ THE ONE CASE THAT IS **NOT** A GAP: a handoff with no `clawgate-task:` field
# at all. Nothing asked clawgate anything, so its silence costs no coverage —
# exactly the rule git_pr_block applies when a handoff references no PRs. It
# still gets an explicit line, because "this doc names no task" and "the task is
# fine" are different statements and the digest must not let one read as the other.
#
# clawgatectl rather than a hand-rolled curl: it reads the token from
# ~/.claude/clawgate.env itself, never puts it in argv, and returns exit codes
# that distinguish unreachable (6) from auth (3) from not-found (4) — which is
# the difference between "the board is down" and "that task is gone".
clawgate_block(){
  echo "CLAWGATE"
  if [ "$CLAWGATE_LIB_OK" -ne 1 ]; then
    echo "  (the shared parser $CLAWGATE_LIB could not be sourced — NOTHING was reconciled)"
    UNRECONCILED+=("scripts/lib/clawgate_handoff.sh could not be sourced — the handoff's clawgate task, if any, was never read")
    return
  fi
  if [ -z "$HANDOFF" ]; then echo "  (no handoff — nothing to reconcile)"; return; fi
  # 🔴 `$HANDOFF_TEXT`, NOT `cat "$HANDOFF"` — the copy handoff_freshness CHOSE.
  # Every other block reads it; this one read the working tree, and the two
  # differ exactly when it matters. MEASURED on a fixture whose branch is behind
  # origin: the digest printed `handoff-read: 🔴 origin/base copy (the
  # working-tree copy is STALE …)` and then
  # `(no clawgate-task: field in this handoff …)` with ZERO gaps — because the
  # STALE local copy carried no field while the copy it announced it had
  # reconciled carried `clawgate-task: 193`, and "no field" is the one case
  # deliberately exempt from gapping. So the block declined to ask the board and
  # nothing said so: the false-clean this block exists to prevent, reached
  # through the block itself.
  local text id
  text="$HANDOFF_TEXT"
  if ! id=$(clawgate_task_field "$text"); then
    if clawgate_field_present "$text"; then
      echo "  (the handoff's clawgate-task: field is UNREADABLE — no task was fetched)"
      UNRECONCILED+=("the handoff carries an unreadable clawgate-task: field — clawgate was never asked, so the task's state is UNKNOWN")
    else
      echo "  (no clawgate-task: field in this handoff — nothing to reconcile; this says NOTHING about the board)"
    fi
    return
  fi

  if ! have clawgatectl; then
    echo "  (clawgatectl not on PATH — task #$id NOT checked)"
    UNRECONCILED+=("clawgate task #$id was NOT checked (clawgatectl not on PATH) — its status is UNKNOWN, not fine")
    return
  fi
  local json rc
  json=$(clawgatectl task get "$id" 2>/dev/null); rc=$?
  if [ "$rc" -ne 0 ] || [ -z "$json" ]; then
    echo "  (clawgatectl exit $rc — task #$id NOT checked)"
    UNRECONCILED+=("clawgate did not answer for task #$id (clawgatectl exit $rc: 3=auth 4=no such task 6=unreachable 8=non-JSON) — its status is UNKNOWN, not fine")
    return
  fi
  local status
  status=$(printf '%s' "$json" | jq -r '.status // empty' 2>/dev/null)
  if [ -z "$status" ]; then
    echo "  (clawgate answered for #$id with no readable status)"
    UNRECONCILED+=("clawgate's answer for task #$id carried no readable status — UNKNOWN, not fine")
    return
  fi
  # 🔴 A STATUS OUTSIDE THE VOCABULARY IS A GAP, NOT A QUIET PASS. clawgate_drift_lines
  # decides on `complete`/`ready_for_review` and is silent otherwise, so a FIFTH
  # status would render exactly like a healthy one — and clawgate's own
  # taskstatus.go records an incident where adding a constant left a suite green.
  # The reading continues (the comment count is still worth having) but the
  # digest must never call an unknown state "no drift".
  if ! clawgate_known_status "$status"; then
    UNRECONCILED+=("clawgate task #$id has status '$status', which this reconciler does not know (it knows: $CLAWGATE_TASK_STATUSES) — whether that means drift is UNKNOWN")
  fi

  # 🔴 WHICH CLOCK "WHEN THE DOC WAS WRITTEN" MEANS, and mtime is the wrong
  # answer in this repo's own standard workflow. `git worktree add` / `git clone`
  # stamp every checked-out file at checkout time, so in a FRESH worktree — which
  # CLAUDE.md mandates for commit-bound work — every comment predates the doc and
  # the count is a silent zero. The doc's last COMMIT date is content-derived and
  # survives a checkout, so it is preferred; mtime is the fallback and says so
  # with a `!` gap naming the clock it used.
  #
  # ⚠ A tracked doc edited but not yet committed reads OLDER than reality on the
  # git clock, so comments between the commit and the edit are counted as new.
  # That over-reports, which is the direction this module errs in everywhere
  # else: a spurious "read these comments" costs a glance, a missed one costs the
  # thing the reconciler exists to catch.
  #
  # 🔴 NO `-d "$REPO/.git"` PRECONDITION — in a WORKTREE `.git` is a FILE, so
  # that test is false in exactly the checkout this fix exists for, and the
  # clock would fall straight back to the mtime a checkout just reset. Ask git
  # instead and read the answer: an empty result means "no commit for this
  # path", whatever the reason.
  #
  # 🔴 AND IT DATES THE COPY THAT WAS READ, WHICH IS NOT ALWAYS THE LOCAL ONE.
  # When handoff_freshness took the text from `origin/<default>` because this
  # branch is behind, the local `git log -1 -- <path>` describes the STALE file
  # that was discarded — an older date, so the count over-reports rather than
  # silences, but it is a date for a document nobody reconciled. `HANDOFF_REF`
  # is set exactly when that happened, so the log is asked on that ref and the
  # printed clock names it. Leaving this implicit was the second half of the
  # same bug as the `text=` line above.
  local mt clock counts newer unreadable total
  clock=""
  if [ -n "$HANDOFF_REF" ] && [ -n "$HANDOFF_REL" ]; then
    mt=$(git -C "$REPO" log -1 --format=%ct "$HANDOFF_REF" -- "$HANDOFF_REL" 2>/dev/null)
    [ -n "$mt" ] && clock="last commit on $HANDOFF_REF"
  else
    mt=$(git -C "$REPO" log -1 --format=%ct -- "$HANDOFF" 2>/dev/null)
    [ -n "$mt" ] && clock="last commit"
  fi
  # 🔴 THE MTIME FALLBACK IS NOT AVAILABLE WHEN THE TEXT CAME FROM A REF. It is
  # the mtime of the file on disk — the copy that was DISCARDED — and it is
  # typically NEWER than the ref's commit, so using it here would SILENCE
  # comments rather than over-report them: the unsafe direction, in the one case
  # where the reconciler already knows it is reading someone else's copy.
  # Reachable when the ref carries no commit for that path (a shallow or grafted
  # clone). So: no date at all, cutoff 0, every comment counted as newer, and a
  # gap that says why. Loud and over-reporting beats quiet and wrong.
  if [ -z "$clock" ] && [ -n "$HANDOFF_REF" ]; then
    mt=0
    clock="UNDATED ($HANDOFF_REF carries no commit for this path)"
    UNRECONCILED+=("the handoff text came from $HANDOFF_REF but that ref carries no commit date for it (shallow or grafted clone?) — every comment is counted as newer rather than dating the DISCARDED local copy, which would silence them")
  fi
  if [ -z "$clock" ]; then
    mt=$(stat -c %Y "$HANDOFF" 2>/dev/null)
    clock="file mtime"
    if [ -n "$mt" ]; then
      UNRECONCILED+=("the handoff doc has no commit date, so comments were counted against its FILE MTIME — a checkout, copy or rsync resets that, and would make every comment read as older than the doc")
    fi
  fi
  if [ -z "$mt" ]; then
    printf '  task #%s  status=%s\n' "$id" "$status"
    UNRECONCILED+=("could not read any date for the handoff doc — comments newer than it were NOT counted for clawgate task #$id")
  else
    counts=$(clawgate_new_comments "$json" "$mt")
    read -r newer unreadable total <<<"$counts"
    # ⚠ `total` is -1 only for a comments field that is present and NOT an
    # array. An ABSENT one is a real zero — the field is `omitempty` on the
    # server, so most tasks have no key at all. See clawgate_new_comments.
    if [ "${total:-0}" -lt 0 ]; then
      printf '  task #%s  status=%s  comments=(unreadable)\n' "$id" "$status"
      UNRECONCILED+=("clawgate's answer for task #$id carried no comments array — comments newer than the doc were NOT counted")
      newer=0
    else
      # The clock is NAMED in the line, not just in a gap: "0 newer" means
      # something different depending on which date it was measured against.
      printf '  task #%s  status=%s  comments=%s (%s newer than the doc, by %s)\n' \
        "$id" "$status" "$total" "$newer" "$clock"
      if [ "${unreadable:-0}" -gt 0 ]; then
        UNRECONCILED+=("$unreadable comment(s) on clawgate task #$id carry an unparseable timestamp — the '$newer newer than the doc' count is a FLOOR")
      fi
    fi
  fi

  local line
  while IFS= read -r line; do
    [ -n "$line" ] && DRIFT+=("$line")
  done < <(clawgate_drift_lines "$id" "$status" "${newer:-0}")
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
  # FIRST, deliberately: this is a claim about the INSTRUCTIONS being executed,
  # and it has to be read before the findings those instructions produce.
  skill_block
  git_pr_block
  workload_block
  alerts_block
  clawgate_block
  echo "DRIFT"
  # 🔴 UNCONDITIONAL, AND FIRST. This notice used to live in the `elif` chain
  # below, which meant ANY finding suppressed it — and the SKILL block made that
  # reachable on an ordinary path: a stale deployed skill is a finding that has
  # nothing to do with the handoff, so a run with NO handoff doc and a stale
  # skill printed a bare `-` list under a header the /resume skill defines as
  # "lines where live state contradicts the handoff", with no handoff
  # reconciled at all. Measured side by side against 200e6383 on one fixture.
  #
  # The two facts are INDEPENDENT — "findings exist" and "nothing was
  # reconciled" — so they get independent lines. It leads because it FRAMES
  # whatever follows: findings read differently once you know no doc was
  # loaded. A full `if`, not `[ … ] && echo`, for the reason the branch below
  # documents.
  if [ -z "$HANDOFF" ]; then
    echo "  (no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"
  fi
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
    # whose doc was never loaded. The notice itself is printed ABOVE, for every
    # run with no handoff; this branch exists only to withhold the all-clear and
    # still print the gaps.
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
