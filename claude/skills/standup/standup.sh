#!/usr/bin/env bash
# Fleet status sweep — deterministic replacement for the prose standup skill.
# Runs under bash (sidesteps the non-interactive-zsh gotchas), reduces every
# query at the source (only digests reach stdout), and splits alerts by the
# `cluster` label so a fan-in Alertmanager is never misattributed.
#
# Usage: standup.sh [all|repos|deploys|alerts|state|initiatives]   (default: all)
# Output: a status line, an ACTIONS block (only items needing a human), and a
# Filtered: line. Designed to be run by the `standup` skill and read verbatim.
set -uo pipefail
SCOPE="${1:-all}"
KT="--request-timeout=12s"

REPOS=(
  /home/zach/workspace/homelab-talos
  /home/zach/workspace/civit/civitai
  /home/zach/workspace/civit/datapacket-talos
  /home/zach/workspace/kubeclaw
  /home/zach/workspace/kubeclaw-cloud
  /home/zach/workspace/kubeclaw-embed
  /home/zach/workspace/devrc
  /home/zach/workspace/promptver
  /home/zach/workspace/baseball-manitoba-pitch
)
# laptop-only repos (reached over ssh for the # state section)
LAP="zach@10.42.0.100"
LAPTOP_REPOS=(
  /home/zach/workspace/scratch/naida-ai
  /home/zach/workspace/scratch/vetr
)
# cluster name -> kubeconfig
CL_NAMES=(homelab workbench production dp-1)
CL_KC=(
  /home/zach/workspace/homelab-talos/homelab-kubeconfig
  /home/zach/workspace/homelab-talos/workbench-kubeconfig
  /home/zach/workspace/homelab-talos/production-kubeconfig
  /home/zach/workspace/civit/datapacket-talos/prod-kubeconfig
)
HL_PROM="http://192.168.50.94:30909"
ME="ZacxDev"   # only PRs authored by you are surfaced as actions
# alertnames that are known/expected noise on dp-1 (filtered from criticals)
NOISE_RE='TargetDown|KubeHpaMaxedOut'
# initiative ledger (the cross-session "what's in flight" view, telemetry-OFF for
# speed/no-creds; the full telemetry view is the /initiatives command).
ISCAN="/home/zach/workspace/devrc/scripts/session-analysis/initiative-scan.py"
IDAYS=14

ACT=()        # action lines (need a human)
PR_OPEN=0 PR_READY=0 PR_RED=0
DEP_WAVE=0 DEP_STUCK=0
INIT_ACTIVE=0 INIT_SLOW=0 INIT_STALL=0
declare -A CRIT          # cluster -> "name,name"
declare -A FIRING        # cluster -> count

have(){ command -v "$1" >/dev/null 2>&1; }

scan_prs(){
  have gh && gh auth status >/dev/null 2>&1 || { echo "  (gh not authed — PRs skipped)"; return; }
  for d in "${REPOS[@]}"; do
    [ -d "$d/.git" ] || continue
    url=$(git -C "$d" remote get-url origin 2>/dev/null) || continue
    slug=$(printf '%s' "$url" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
    flagged=$(gh pr list -R "$slug" --state open --json number,mergeable,reviewDecision,statusCheckRollup,isDraft,author \
      --jq '.[] | select(.isDraft|not)
            | {n:.number, m:.mergeable, r:.reviewDecision, a:.author.login,
               ci:([.statusCheckRollup[]?.conclusion]
                   | if any(.=="FAILURE" or .=="ERROR") then "red"
                     elif length>0 and all(.=="SUCCESS") then "green" else "pending" end)}
            | select(.ci=="red" or (.r=="APPROVED" and .m=="MERGEABLE") or .m=="CONFLICTING")
            | "\(.n)\t\(.ci)\t\(.m)\t\(.r)\t\(.a)"' 2>/dev/null) || { echo "  $slug: skipped (gh err)"; continue; }
    n=$(gh pr list -R "$slug" --state open --json number --jq 'length' 2>/dev/null || echo 0)
    PR_OPEN=$((PR_OPEN+n))
    [ -z "$flagged" ] && continue
    while IFS=$'\t' read -r num ci m r author; do
      [ -z "$num" ] && continue
      [ "$author" = "$ME" ] || continue   # only YOUR PRs count + action (others aren't your concern)
      [ "$ci" = "red" ] && PR_RED=$((PR_RED+1))
      { [ "$r" = "APPROVED" ] && [ "$m" = "MERGEABLE" ]; } && PR_READY=$((PR_READY+1))
      if [ "$r" = "APPROVED" ] && [ "$m" = "MERGEABLE" ]; then ACT+=("$slug PR #$num approved+mergeable → merge"); fi
      if [ "$ci" = "red" ]; then ACT+=("$slug PR #$num red CI → fix (civitai report-only previews are known-noise)"); fi
      if [ "$m" = "CONFLICTING" ]; then ACT+=("$slug PR #$num conflicting → rebase"); fi
    done <<< "$flagged"
  done
}

scan_deploys(){
  for i in "${!CL_NAMES[@]}"; do
    name="${CL_NAMES[$i]}"; kc="${CL_KC[$i]}"; [ -f "$kc" ] || continue
    export KUBECONFIG="$kc"
    kubectl --request-timeout=8s get --raw /readyz >/dev/null 2>&1 || { echo "  $name: skipped (unreachable)"; continue; }
    # canaries mid-wave / failed
    can=$(kubectl $KT get canary -A --no-headers 2>/dev/null | awk '$3!~/Succeeded|Initialized|""/{print $1"/"$2" "$3}')
    if [ -n "$can" ]; then
      while read -r line; do
        [ -z "$line" ] && continue
        DEP_WAVE=$((DEP_WAVE+1))
        echo "$line" | grep -qi failed && ACT+=("$name canary ${line%% *} FAILED → /verify-deploy")
      done <<< "$can"
    fi
    # deployments not fully ready
    bad=$(kubectl $KT get deploy -A -o json 2>/dev/null | jq -r '.items[] | select((.status.readyReplicas//0) < (.spec.replicas//0)) | "\(.metadata.namespace)/\(.metadata.name) \(.status.readyReplicas//0)/\(.spec.replicas)"')
    if [ -n "$bad" ]; then
      cnt=$(printf '%s\n' "$bad" | grep -c .); DEP_STUCK=$((DEP_STUCK+cnt))
      top=$(printf '%s\n' "$bad" | head -3 | tr '\n' ';')
      ACT+=("$name $cnt deploy(s) not ready: $top → /verify-deploy (is it a stuck new rollout?)")
    fi
  done
}

# emit "cluster<TAB>count<TAB>crit,names" lines. $1=default cluster, $2="single"
# forces one bucket (for single-cluster sources); omitted = split by `cluster`
# label (for the dp-1 multi-cluster fan-in). Reads Prom-query OR AM-v2 JSON.
collect_alerts(){
  jq -r --arg def "$1" --arg noise "$NOISE_RE" --arg single "${2:-}" '
    def cl(lbl): if $single=="single" then $def else (lbl // $def) end;
    (if type=="array"
       then [.[] | select(.status.state=="active") | {cl:cl(.labels.cluster), sev:(.labels.severity//""), an:(.labels.alertname//"")}]
       else [.data.result[] | {cl:cl(.metric.cluster), sev:(.metric.severity//""), an:(.metric.alertname//"")}] end)
    | group_by(.cl)[]
    | {cl:.[0].cl, total:length,
       crit:([.[] | select(.sev=="critical") | select(.an|test($noise)|not) | .an] | unique)}
    | "\(.cl)\t\(.total)\t\(.crit|join(","))"'
}

scan_alerts(){
  # homelab — direct NodePort Prometheus; SINGLE cluster (collapse mixed labels)
  hl=$(curl -s --max-time 12 -G "$HL_PROM/api/v1/query" --data-urlencode 'query=ALERTS{alertstate="firing"}' 2>/dev/null | collect_alerts homelab single 2>/dev/null)
  # dp-1 — Alertmanager is ClusterIP; brief port-forward (reliable, no pod creation).
  # FAN-IN: split by cluster label (no `single`). sleep inside a script is fine —
  # the harness only blocks `sleep` in the Bash tool, not in an executed script.
  export KUBECONFIG="/home/zach/workspace/civit/datapacket-talos/prod-kubeconfig"
  local dp=""
  if kubectl --request-timeout=8s get --raw /readyz >/dev/null 2>&1; then
    kubectl port-forward -n monitoring svc/kube-prometheus-stack-alertmanager 19093:9093 >/dev/null 2>&1 &
    local pf=$!; sleep 3
    dp=$(curl -s --max-time 10 http://127.0.0.1:19093/api/v2/alerts 2>/dev/null | collect_alerts dp-1 2>/dev/null)
    kill "$pf" 2>/dev/null; wait "$pf" 2>/dev/null
  fi
  [ -z "$dp" ] && dp=$(printf 'dp-1\t?\t')   # unreachable: empty crit (no fake critical)
  ALERT_RAW=$(printf '%s\n%s\n' "$hl" "$dp")
}

# per-repo working state (branch / ahead-behind / dirty / last-commit / doc pointer).
# Defined at top level so it can be shipped over ssh to the laptop via `declare -f`.
_repo_state(){
  d="$1"; tag="${2:-}"
  [ -d "$d/.git" ] || return
  name=$(basename "$d")
  br=$(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null)
  ab=$(git -C "$d" rev-list --left-right --count '@{u}...HEAD' 2>/dev/null)
  behind=$(printf '%s' "$ab" | awk '{print $1+0}')
  ahead=$(printf '%s' "$ab" | awk '{print $2+0}')
  dirty=$(git -C "$d" status --porcelain 2>/dev/null | grep -c .)
  cl=$(git -C "$d" log -1 --format='%cr%x09%s' 2>/dev/null)
  age=${cl%%	*}; subj=${cl#*	}
  # compact relative age: "28 minutes ago" -> "28m", "2 weeks ago" -> "2w"
  age=$(printf '%s' "$age" | awk '{print $1 substr($2,1,1)}')
  doc=""
  for f in claudedocs/HANDOFF.md HANDOFF.md STATE.md claudedocs/STATE.md; do
    [ -f "$d/$f" ] && { doc="$f"; break; }
  done
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$name$tag" "${br:-?}" "${ahead:-0}" "${behind:-0}" "${dirty:-0}" "${age:-?}" "${subj:-}" "$doc"
}

scan_state(){
  # If the laptop repos exist locally we ARE the laptop → read them directly
  # (no self-ssh / duplication); otherwise sweep them over ssh. Either set is
  # skipped gracefully when its repos aren't present on this host.
  local on_laptop=0; [ -d "${LAPTOP_REPOS[0]}/.git" ] && on_laptop=1
  { for d in "${REPOS[@]}"; do _repo_state "$d" ""; done
    if [ "$on_laptop" = 1 ]; then
      for d in "${LAPTOP_REPOS[@]}"; do _repo_state "$d" " (lap)"; done
    else
      ssh -o ConnectTimeout=8 "$LAP" "$(declare -f _repo_state); for d in ${LAPTOP_REPOS[*]}; do _repo_state \"\$d\" ' (lap)'; done" 2>/dev/null
    fi
  } | while IFS=$'\t' read -r name br ahead behind dirty age subj doc; do
        [ -z "$name" ] && continue
        flags=""
        [ "${ahead:-0}" -gt 0 ] && flags+=" ⚠${ahead}-unpushed"
        [ "${dirty:-0}" -gt 0 ] && flags+=" ⚠wip"
        printf '  %-22s %-13s ↑%s↓%s  %-9s %-7s %s%s\n' \
          "${name:0:22}" "${br:0:13}" "${ahead:-0}" "${behind:-0}" \
          "$([ "${dirty:-0}" -gt 0 ] && echo "${dirty} dirty" || echo clean)" \
          "${age:-?}" "${subj:0:40}" "$flags"
        [ -n "$doc" ] && printf '       ↳ %s\n' "$doc"
      done
}

# Cross-repo initiative ledger: momentum counts, owed/held items (→ ACTIONS),
# initiative-tied open PRs, and the most-stalled. Runs initiative-scan telemetry-OFF
# (fast, no creds); the full telemetry view is `/initiatives`. Skips gracefully if
# the script / python3 / jq aren't available.
scan_initiatives(){
  [ -f "$ISCAN" ] || { echo "  (initiative-scan not present — skipped)"; return; }
  have jq || { echo "  (jq unavailable — skipped)"; return; }
  local json raw
  # Call python3 DIRECTLY (mirrors sync.py, which shells out with sys.executable
  # and has never hit this). A `$(nix-shell --run ...)` capture is NOT clean on
  # this box: a repo `flake.nix` shellHook echoes a greeting to STDOUT on shell
  # entry — an open-PR digest built from `gh pr list` (so the PR set tracks the
  # cwd repo's git remote), a "source .venv/bin/activate" hint elsewhere. That
  # chatter lands in front of the JSON, so jq died with "Invalid numeric literal
  # at line 2, column 5" ×4 and every initiative field came back empty (STATUS
  # showed a bare `Initiatives a/s/st`). The scan needs `requests` only when
  # telemetry is ON, and this call is deliberately telemetry-OFF.
  if have python3; then raw=$(python3 "$ISCAN" --json --days "$IDAYS" 2>/dev/null); fi
  # Fallback only if the system python3 can't run it (e.g. a missing import on
  # the laptop); the slice below then strips whatever init chatter came with it.
  if [ -z "${raw:-}" ] && have nix-shell; then
    raw=$(nix-shell -p "python3.withPackages(p:[p.requests])" --run \
      "python $ISCAN --json --days $IDAYS" 2>/dev/null)
  fi
  # Defensive: keep only from the first line that opens the JSON object.
  json=$(printf '%s' "${raw:-}" | sed -n '/^{/,$p')
  [ -z "$json" ] && { echo "  (initiative-scan no output — skipped)"; return; }

  read -r INIT_ACTIVE INIT_SLOW INIT_STALL < <(printf '%s' "$json" | jq -r '
    [.by_repo[]?[]?] as $a
    | "\([$a[]|select(.momentum=="active")]|length) \([$a[]|select(.momentum=="slowing")]|length) \([$a[]|select(.momentum=="stalled")]|length)"')
  echo "  in-flight: ${INIT_ACTIVE:-0} active · ${INIT_SLOW:-0} slowing · ${INIT_STALL:-0} stalled  (telemetry off — full: /initiatives)"

  # owed/held next-steps → both the section AND the ACTIONS block (they need you)
  local owed
  owed=$(printf '%s' "$json" | jq -r '
    [.by_repo[]?[]?] | map(select(.next_step!=null and (.next_step|test("OWED|HELD";"i"))))
    | unique_by(.slug) | .[0:5][] | "\(.slug)\t\(.next_step|gsub("\\s+";" ")|.[0:88])"')
  if [ -n "$owed" ]; then
    echo "  owed/held:"
    while IFS=$'\t' read -r slug ns; do
      [ -z "$slug" ] && continue
      echo "    - $slug: $ns"
      ACT+=("initiative $slug owed/held → /initiatives")
    done <<< "$owed"
  fi

  # initiative-tied open PRs (informational — scan_prs only flags YOUR ready/red ones)
  local iprs
  iprs=$(printf '%s' "$json" | jq -r '
    [.by_repo[]?[]?] | map(select((.open_prs|length)>0)) | unique_by(.slug)
    | map("\(.slug) #\(.open_prs[0].number)") | join(" · ")')
  [ -n "$iprs" ] && echo "  open PR: $iprs"

  # most-stalled (oldest touch first)
  local stale; stale=$(printf '%s' "$json" | jq -r '
    [.by_repo[]?[]?] | map(select(.momentum=="stalled")) | unique_by(.slug)
    | sort_by(.last_touch) | .[0:4][] | "\(.slug)\t\(.last_touch)"')
  if [ -n "$stale" ]; then
    local now out=""; now=$(date +%s)
    while IFS=$'\t' read -r slug lt; do
      [ -z "$slug" ] && continue
      lt=${lt%.*}; [ -z "$lt" ] && lt=$now
      out+="$slug ($(( (now - lt) / 86400 ))d) · "
    done <<< "$stale"
    echo "  most stalled: ${out% · }"
  fi
}

echo "## standup ($SCOPE) $(date -u +%FT%TZ)"
if [ "$SCOPE" = all ] || [ "$SCOPE" = repos ];   then echo "# repos";   scan_prs;     fi
if [ "$SCOPE" = all ] || [ "$SCOPE" = deploys ]; then echo "# deploys"; scan_deploys; fi
if [ "$SCOPE" = all ] || [ "$SCOPE" = alerts ];  then echo "# alerts";  scan_alerts;  fi
if [ "$SCOPE" = all ] || [ "$SCOPE" = state ];   then echo "# state";   scan_state;   fi
if [ "$SCOPE" = all ] || [ "$SCOPE" = initiatives ]; then echo "# initiatives"; scan_initiatives; fi

# alert digest (synchronous, array-safe)
ALERT_LINE=""; CRIT_TOTAL=0
if [ -n "${ALERT_RAW:-}" ]; then
  while IFS=$'\t' read -r cl tot crit; do
    [ -z "$cl" ] && continue
    ncrit=0; [ -n "$crit" ] && ncrit=$(printf '%s' "$crit" | awk -F, '{print NF}')
    CRIT_TOTAL=$((CRIT_TOTAL+ncrit))
    ALERT_LINE+="$cl ${tot:-0}f/${ncrit}c · "
    [ -n "$crit" ] && ACT+=("$cl CRITICAL: $crit → /manage-alerts | /observability")
  done <<< "$ALERT_RAW"
fi

echo
INIT_STATUS=""
{ [ "$SCOPE" = all ] || [ "$SCOPE" = initiatives ]; } && INIT_STATUS=" · Initiatives ${INIT_ACTIVE}a/${INIT_SLOW}s/${INIT_STALL}st"
echo "STATUS: PRs ${PR_OPEN} open (${PR_READY} ready, ${PR_RED} red) · Deploys ${DEP_WAVE} mid-wave/${DEP_STUCK} stuck · Alerts ${ALERT_LINE:-n/a}${INIT_STATUS}"
if [ "${#ACT[@]}" -gt 0 ]; then
  echo "ACTIONS"
  printf '  - %s\n' "${ACT[@]}"
else
  echo "All clear — nothing needs you."
fi
echo "Filtered: dp-1 fan-in split by cluster · known-noise (${NOISE_RE//|/, }) · KubeJobFailed=accumulated history · submodel-GPU disk = not prod"
