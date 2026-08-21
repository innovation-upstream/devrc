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

# PR numbers: bare `#<digits>` requires >=2 digits (drops stray `#5` prose refs);
# a github .../pull/<digits> URL is always taken (its intent is unambiguous).
extract_prs(){
  { printf '%s\n' "$1" | grep -oE '#[0-9]{2,}' | tr -d '#'
    printf '%s\n' "$1" | grep -oE 'pull/[0-9]+'  | grep -oE '[0-9]+'
  } 2>/dev/null | sort -un
}

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
handoff_says_inflight(){ # $1=pr number  $2=handoff path
  [ -f "$2" ] || return 1
  grep -iE "#$1([^0-9]|$)" "$2" 2>/dev/null \
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

git_pr_block(){
  echo "GIT/PR"
  local d="$REPO"
  if [ ! -d "$d/.git" ]; then echo "  (not a git repo: $d)"; return; fi

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

  [ -n "$HANDOFF" ] && echo "  handoff: $(basename "$HANDOFF")" || echo "  handoff: (none found — git-only)"
  [ -n "$HANDOFF" ] || return
  local text; text=$(cat "$HANDOFF")

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
  local prs; prs=$(extract_prs "$text")
  if have gh && [ -n "$SLUG" ]; then
    local pr j state merged ci n_try=0 n_ok=0
    for pr in $prs; do
      n_try=$((n_try+1))
      j=$(gh pr view "$pr" -R "$SLUG" --json state,mergeable,mergedAt,statusCheckRollup 2>/dev/null) || continue
      [ -z "$j" ] && continue                         # 404 / not a real PR -> silently drop
      n_ok=$((n_ok+1))
      state=$(printf '%s' "$j" | jq -r '.state')
      ci=$(printf '%s' "$j" | jq -r '[.statusCheckRollup[]?.conclusion]
             | if any(.=="FAILURE" or .=="ERROR") then "red"
               elif length>0 and all(.=="SUCCESS") then "green" else "pending" end')
      if [ "$state" = OPEN ]; then printf '  PR #%s %-6s ci=%s\n' "$pr" "$state" "$ci"
      else printf '  PR #%s %s\n' "$pr" "$state"; fi
      # DRIFT: the handoff frames this PR as open/in-flight but it already landed...
      if [ "$state" = MERGED ] && handoff_says_inflight "$pr" "$HANDOFF"; then
        DRIFT+=("PR #$pr MERGED but handoff frames it as open/in-flight (do the follow-on)")
      fi
      # ...or a referenced PR was CLOSED without merging (abandoned — always notable)
      [ "$state" = CLOSED ] && DRIFT+=("PR #$pr CLOSED without merge (was the handoff plan abandoned?)")
      [ "$state" = OPEN ] && [ "$ci" = red ] && DRIFT+=("PR #$pr OPEN with RED ci")
    done
    if [ "$n_try" -gt 0 ] && [ "$n_ok" -eq 0 ]; then
      echo "  (gh answered for 0 of $n_try referenced PR(s))"
      UNRECONCILED+=("gh answered for 0 of $n_try referenced PR(s) — offline, unauthenticated, no access, or none is a real PR")
    elif [ "$n_ok" -lt "$n_try" ]; then
      UNRECONCILED+=("gh answered for $n_ok of $n_try referenced PR(s) — the rest were not reconciled")
    fi
  else
    echo "  (gh unavailable or no remote — PR reconciliation skipped)"
    # Only a GAP if the handoff actually referenced PRs. A handoff naming none
    # has nothing for gh to answer, so the absence of gh costs no coverage and
    # must not downgrade an otherwise honest clean result.
    if [ -n "$prs" ]; then
      UNRECONCILED+=("gh unavailable or no remote — $(printf '%s\n' "$prs" | grep -c .) referenced PR(s) were never checked")
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
  text=$(cat "$HANDOFF")
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
  local text id
  text=$(cat "$HANDOFF")
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

  # The doc's mtime is the "when this was written" clock. It is the doc's own
  # last write, so a doc UPDATED after a comment correctly stops counting it.
  local mt counts newer unreadable total
  mt=$(stat -c %Y "$HANDOFF" 2>/dev/null)
  if [ -z "$mt" ]; then
    printf '  task #%s  status=%s\n' "$id" "$status"
    UNRECONCILED+=("could not read the handoff doc's mtime — comments newer than it were NOT counted for clawgate task #$id")
  else
    counts=$(clawgate_new_comments "$json" "$mt")
    read -r newer unreadable total <<<"$counts"
    if [ "${total:-0}" -lt 0 ]; then
      printf '  task #%s  status=%s  comments=(absent)\n' "$id" "$status"
      UNRECONCILED+=("clawgate's answer for task #$id carried no comments array — comments newer than the doc were NOT counted")
      newer=0
    else
      printf '  task #%s  status=%s  comments=%s (%s newer than the doc)\n' \
        "$id" "$status" "$total" "$newer"
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
main(){
  resolve "${1:-}"
  echo "## resume-state $(date -u +%FT%TZ)"
  echo "# repo: ${REPO:-?}  slug: ${SLUG:-?}"
  git_pr_block
  workload_block
  alerts_block
  clawgate_block
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
    if [ "${#UNRECONCILED[@]}" -gt 0 ]; then
      printf '  ! %s\n' "${UNRECONCILED[@]}"
    fi
  elif [ -z "$HANDOFF" ]; then
    # No handoff resolved => nothing was reconciled. Saying "live state matches
    # the handoff's claims" here is a LIE, and the reassuring shape of it is the
    # actual harm: a caller reads it as a clean bill of health for an initiative
    # whose doc was never loaded. Name the absence instead.
    echo "  (no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"
  elif [ "${#UNRECONCILED[@]}" -gt 0 ]; then
    # A handoff loaded and nothing contradicted it — but a source went
    # unanswered, so "no drift" is not a finding about live state, it is a
    # finding about the part of live state we managed to see.
    echo "  (nothing detected, but a source did not answer — NOT a clean bill of health)"
    printf '  ! %s\n' "${UNRECONCILED[@]}"
  else
    echo "  (none detected — live state matches the handoff's claims)"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
