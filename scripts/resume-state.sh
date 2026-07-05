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
#   no arg      -> newest claudedocs/handoff-*.md in the repo of $PWD
#   slug        -> claudedocs/handoff-<slug>*.md in the repo of $PWD
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
extract_branches(){
  printf '%s\n' "$1" \
    | grep -oE '\b(zach|feat|fix|docs|chore)/[A-Za-z0-9._/-]+' 2>/dev/null \
    | sed -E 's/[.,;:)]+$//' | sort -u
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
  fi
  local url
  url=$(git -C "$REPO" remote get-url origin 2>/dev/null) \
    && SLUG=$(printf '%s' "$url" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
}

# ---------------------------------------------------------------------------
# GIT / PR — always runs; the rock-solid core of the digest.
# ---------------------------------------------------------------------------
DRIFT=()   # lines where live state contradicts the handoff

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
  if have gh && [ -n "$SLUG" ]; then
    local pr j state merged ci
    for pr in $(extract_prs "$text"); do
      j=$(gh pr view "$pr" -R "$SLUG" --json state,mergeable,mergedAt,statusCheckRollup 2>/dev/null) || continue
      [ -z "$j" ] && continue                         # 404 / not a real PR -> silently drop
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
  else
    echo "  (gh unavailable or no remote — PR reconciliation skipped)"
  fi

  # --- branches: does the handoff's named branch still exist / is it merged? ---
  local b tip
  for b in $(extract_branches "$text"); do
    if git -C "$d" rev-parse --verify -q "$b" >/dev/null 2>&1; then
      tip=local
    elif git -C "$d" rev-parse --verify -q "origin/$b" >/dev/null 2>&1; then
      tip="origin/$b"
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
  while read -r line; do
    [[ "$line" == *"***"* ]] && DRIFT+=("firing CRITICAL in $WL_NS:${line%%  \*\*\*}")
  done <<<"$out"
}

# ---------------------------------------------------------------------------
main(){
  resolve "${1:-}"
  echo "## resume-state $(date -u +%FT%TZ)"
  echo "# repo: ${REPO:-?}  slug: ${SLUG:-?}"
  git_pr_block
  workload_block
  alerts_block
  echo "DRIFT"
  if [ "${#DRIFT[@]}" -gt 0 ]; then
    printf '  - %s\n' "${DRIFT[@]}"
  else
    echo "  (none detected — live state matches the handoff's claims)"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
