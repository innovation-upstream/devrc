#!/usr/bin/env bash
# Fleet status sweep — deterministic replacement for the prose standup skill.
# Runs under bash (sidesteps the non-interactive-zsh gotchas), reduces every
# query at the source (only digests reach stdout), and splits alerts by the
# `cluster` label so a fan-in Alertmanager is never misattributed.
#
# Usage: standup.sh [all|repos|deploys|alerts|state|local|initiatives] (default: all)
# Output: a status line, an ACTIONS block (only items needing a human), and a
# Filtered: line. Designed to be run by the `standup` skill and read verbatim.
set -uo pipefail
SCOPE="${1:-all}"
KT="--request-timeout=12s"

# Local checkouts that seed the PR sweep. This is a SEED, not the scope: the
# sweep also discovers every other repo you have an open PR in (see PR_SEARCH_*),
# because STATUS makes a fleet claim and must only make one it measured.
# Overridable (colon-separated) so the tests can drive a throwaway repo set.
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
[ -n "${STANDUP_REPOS:-}" ] && IFS=: read -r -a REPOS <<< "$STANDUP_REPOS"
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
# --- fleet widening for the PR sweep -----------------------------------------
# The repo set is DISCOVERED from your open PRs rather than hard-coded, because
# the skill's own description promises a fleet sweep and a hard-coded list
# silently under-reports every repo not on it. One search call, then one
# `gh pr list` per repo (run concurrently, bounded by PR_JOBS).
PR_SEARCH_LIMIT="${STANDUP_PR_SEARCH_LIMIT:-300}"
PR_JOBS="${STANDUP_PR_JOBS:-6}"
# Release-bot repos: high PR volume, no human signal. An explicitly enumerated
# list (NOT a pattern) — an unknown repo is in scope by default. Printed on the
# Filtered: line so an excluded repo is never a silent omission.
PR_REPO_EXCLUDE=(ZacxDev/homebrew-tap)
# alertnames that are known/expected noise on dp-1 (filtered from criticals)
NOISE_RE='TargetDown|KubeHpaMaxedOut'
# initiative ledger (the cross-session "what's in flight" view, telemetry-OFF for
# speed/no-creds; the full telemetry view is the /initiative-scan command).
ISCAN="/home/zach/workspace/devrc/scripts/session-analysis/initiative-scan.py"
IDAYS=14

ACT=()        # action lines (need a human)
PR_OPEN=0 PR_READY=0 PR_RED=0 PR_CONFLICT=0
PR_REPOS=0 PR_ERR=0 PR_SCOPE="not scanned" PR_DISCOVERY="not scanned" PR_TRUNC=0
DEP_WAVE=0 DEP_STUCK=0
INIT_ACTIVE=0 INIT_SLOW=0 INIT_STALL=0
declare -A CRIT          # cluster -> "name,name"
declare -A FIRING        # cluster -> count

have(){ command -v "$1" >/dev/null 2>&1; }

# Records are separated by US (0x1f), NOT tab. Tab is an IFS *whitespace*
# character, so `IFS=$'\t' read` COLLAPSES a run of tabs — an empty
# `reviewDecision` (the normal state of a PR nobody has reviewed) shifted every
# later field left by one, leaving `author` empty, so the `author == $ME` test
# dropped EVERY such PR. That is what printed "0 ready, 0 red" while a single
# in-scope repo held 8 flagged PRs. 0x1f is not IFS whitespace: empty fields
# survive. Do not "simplify" this back to \t.
US=$'\x1f'

# One `gh pr list` per repo. Emits US-separated records on stdout:
#   COUNT<US><n-open-non-draft>
#   FLAG<US><num><US><ci><US><mergeable><US><reviewDecision><US><author>
#   ERR                                  (repo unreadable — never silently 0)
_pr_scan_repo(){
  gh pr list -R "$1" --state open --limit 100 \
    --json number,mergeable,reviewDecision,statusCheckRollup,isDraft,author \
    --jq '[.[] | select(.isDraft|not)] as $p
          | "COUNT\u001f\($p|length)",
            ($p[]
             | {n:.number, m:(.mergeable//""), r:(.reviewDecision//""), a:(.author.login//""),
                # statusCheckRollup mixes two node types: a CheckRun carries
                # `conclusion`, a StatusContext carries `state` and has
                # conclusion=null. Reading only `.conclusion` made every
                # StatusContext FAILURE invisible — 5 genuinely-red PRs in one
                # repo read as "pending". Normalise both into one verdict.
                ci:([.statusCheckRollup[]? | (.conclusion // .state // "")]
                    | if any(.=="FAILURE" or .=="ERROR") then "red"
                      elif length>0 and all(.=="SUCCESS") then "green" else "pending" end)}
             | select(.ci=="red" or (.r=="APPROVED" and .m=="MERGEABLE") or .m=="CONFLICTING")
             | "FLAG\u001f\(.n)\u001f\(.ci)\u001f\(.m)\u001f\(.r)\u001f\(.a)")' \
    2>/dev/null || echo ERR
}

# Discover every repo with an open PR of yours, minus the enumerated release-bot
# repos, unioned with the local checkouts. First line is
# `META<US><ok|failed><US><0|1 truncated>`; every later line is a slug.
# The metadata rides stdout because the caller reads this through a process
# substitution — a variable set here would be set in a SUBSHELL and lost, which
# is exactly how a degraded scan would come back looking like a fleet sweep.
_pr_repo_set(){
  local -a local_slugs=()
  local d url slug
  for d in "${REPOS[@]}"; do
    [ -d "$d/.git" ] || continue
    url=$(git -C "$d" remote get-url origin 2>/dev/null) || continue
    slug=$(printf '%s' "$url" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
    [ -n "$slug" ] && local_slugs+=("$slug")
  done
  local found rc disc=ok trunc=0
  found=$(gh search prs --author=@me --state=open --limit "$PR_SEARCH_LIMIT" \
            --json repository --jq '.[].repository.nameWithOwner' 2>/dev/null); rc=$?
  if [ "$rc" -ne 0 ]; then
    disc=failed; found=""
  elif [ "$(printf '%s\n' "$found" | grep -c .)" -ge "$PR_SEARCH_LIMIT" ]; then
    trunc=1
  fi
  printf 'META%s%s%s%s\n' "$US" "$disc" "$US" "$trunc"
  { printf '%s\n' "${local_slugs[@]}"; printf '%s\n' "$found"; } | grep . | sort -u \
    | while read -r slug; do
        local skip=0 x
        for x in "${PR_REPO_EXCLUDE[@]}"; do [ "$slug" = "$x" ] && skip=1; done
        [ "$skip" = 0 ] && printf '%s\n' "$slug"
      done
}

scan_prs(){
  have gh && gh auth status >/dev/null 2>&1 || { echo "  (gh not authed — PRs skipped)"; PR_SCOPE="gh unavailable"; return; }
  local -a slugs=()
  local f1 f2 f3
  PR_DISCOVERY=failed PR_TRUNC=0
  while IFS="$US" read -r f1 f2 f3; do
    if [ "$f1" = META ]; then PR_DISCOVERY="$f2"; PR_TRUNC="${f3:-0}"; continue; fi
    [ -n "$f1" ] && slugs+=("$f1")
  done < <(_pr_repo_set)
  PR_REPOS=${#slugs[@]}
  [ "$PR_REPOS" = 0 ] && { echo "  (no repos resolved — PRs skipped)"; PR_SCOPE="no repos resolved"; return; }

  # fan out, bounded — one gh call per repo, results to per-repo files so the
  # parent (not a subshell) does the accumulating.
  local tmpd; tmpd=$(mktemp -d) || return
  local -a pids=()
  local i
  for i in "${!slugs[@]}"; do
    _pr_scan_repo "${slugs[$i]}" > "$tmpd/$i.out" &
    pids+=($!)
    if [ "${#pids[@]}" -ge "$PR_JOBS" ]; then wait "${pids[0]}" 2>/dev/null; pids=("${pids[@]:1}"); fi
  done
  wait

  local kind num ci m r author
  for i in "${!slugs[@]}"; do
    slug="${slugs[$i]}"
    [ -f "$tmpd/$i.out" ] || continue
    while IFS="$US" read -r kind num ci m r author; do
      case "$kind" in
        COUNT) PR_OPEN=$((PR_OPEN + num)) ;;
        ERR)   echo "  $slug: skipped (gh err)"; PR_ERR=$((PR_ERR+1)) ;;
        FLAG)
          [ "$author" = "$ME" ] || continue   # only YOUR PRs count + action (others aren't your concern)
          if [ "$ci" = "red" ]; then
            PR_RED=$((PR_RED+1))
            ACT+=("$slug PR #$num red CI → fix (civitai report-only previews are known-noise)")
          fi
          if [ "$r" = "APPROVED" ] && [ "$m" = "MERGEABLE" ]; then
            PR_READY=$((PR_READY+1))
            ACT+=("$slug PR #$num approved+mergeable → merge")
          fi
          if [ "$m" = "CONFLICTING" ]; then
            PR_CONFLICT=$((PR_CONFLICT+1))
            ACT+=("$slug PR #$num conflicting → rebase")
          fi
          ;;
      esac
    done < "$tmpd/$i.out"
  done
  rm -rf "$tmpd"

  # Scope string for STATUS — it must describe what was ACTUALLY measured.
  PR_SCOPE="$PR_REPOS repos"
  [ "$PR_TRUNC" = 1 ] && PR_SCOPE="≥$PR_REPOS repos (search capped at $PR_SEARCH_LIMIT — counts are a floor)"
  [ "$PR_DISCOVERY" = failed ] && PR_SCOPE="$PR_REPOS LOCAL repos only — fleet discovery FAILED"
  [ "$PR_ERR" -gt 0 ] && PR_SCOPE="$PR_SCOPE, $PR_ERR unreadable"
  echo "  scanned $PR_REPOS repos ($PR_OPEN open PRs; ${PR_RED} red / ${PR_READY} ready / ${PR_CONFLICT} conflicting are yours)"
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

# --- local host health (systemd --user) --------------------------------------
# Any user unit in a FAILED state, plus the last-run result + age of the key
# timer-backed services. Folded in from the retired `agent-ops` TUI: of that
# dashboard's seven panels this was the only one with no other owner (live runs
# and the clawgate queue went to `session-manager`, PRs and cluster alerts were
# already here, momentum to /initiative-scan, the mail and clawgate counts are
# bar pills). It is LOCAL-only by nature — it describes the host you are on.
#
# `unit:label` — label is what gets printed. A unit not installed on this host
# (LoadState=not-found, e.g. the workbench-only ones on the laptop) prints
# "absent" and is NOT a finding; that distinction is the whole reason this reads
# `show` per unit instead of trusting `--failed` alone.
LOCAL_UNITS=(
  "repo-cos.service:repo-cos"
  "mail-actions-archive.service:mail-archive"
  "bar-status-poll.service:bar-poll"
  "claude-activity-source.service:claude-src"
  "activity-collector.service:collector"
)
# ';'-separated override (the unit specs themselves contain ':'), so the tests
# can drive a stub systemctl over a throwaway unit set.
[ -n "${STANDUP_LOCAL_UNITS:-}" ] && IFS=';' read -r -a LOCAL_UNITS <<< "$STANDUP_LOCAL_UNITS"
# 🔴 UNSET, not 0 — these are MEASUREMENTS, and a measurement that never
# happened must not be spellable as "0 failed". They are assigned only on the
# branches that actually read an answer out of systemctl; every reader below
# renders `n/a` for the empty string. LOCAL_DEGRADED records that at least one
# probe could not be taken at all, which suppresses the unqualified all-clear.
LOCAL_FAILED=""; LOCAL_BAD=""; LOCAL_DEGRADED=0

# seconds -> compact age ("45s" / "12m" / "3h" / "4d"); "?" when unknown.
_rel_age(){
  local s=${1:-}
  s=${s%%.*}
  case "$s" in ''|*[!0-9-]*) printf '?'; return;; esac
  if   [ "$s" -lt 60 ];    then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ];  then printf '%dm' $((s / 60))
  elif [ "$s" -lt 86400 ]; then printf '%dh' $((s / 3600))
  else                          printf '%dd' $((s / 86400)); fi
}

scan_local(){
  have systemctl || {
    echo "  (systemctl unavailable — skipped)"; LOCAL_DEGRADED=1; return; }
  local up raw rc failed nfailed spec unit label show load active sub result
  local runflag mono age mark
  up=$(awk '{print int($1)}' /proc/uptime 2>/dev/null)

  # 1. failed user units. Only tokens ending in a real unit suffix are kept, so
  #    a stray header or legend line can never leak in as a "failed unit".
  #
  # 🔴 CAPTURE THE EXIT STATUS. `have systemctl` only proves the BINARY exists;
  #    it says nothing about the user manager being reachable. With no
  #    XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS — an ssh non-login shell, a
  #    system unit, a container — systemctl is on PATH and exits 1 with
  #    "Failed to connect to user scope bus" on stderr and NOTHING on stdout.
  #    Piping straight into awk discards that status, so the empty stdout used
  #    to render "✓ all user units healthy" and "Local 0 failed", byte-identical
  #    to a genuinely healthy host. Same defect class as a count travelling
  #    without its discriminant (the bar pill's `?`): an unmeasurable answer is
  #    NOT a zero. So: read the raw output, branch on rc, and on failure leave
  #    LOCAL_FAILED unset so no downstream line can spell it as a measured 0.
  raw=$(systemctl --user --failed --plain --no-legend --no-pager 2>/dev/null); rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '  %-14s %s\n' "units" "— systemctl n/a (user manager unreachable)"
    LOCAL_DEGRADED=1
  else
    failed=$(printf '%s\n' "$raw" \
      | awk '$1 ~ /\.(service|timer|socket|mount|path|scope|slice|target|device|automount|swap)$/ {print $1}')
    if [ -z "${failed:-}" ]; then
      LOCAL_FAILED=0
      printf '  %-14s %s\n' "units" "✓ all user units healthy"
    else
      nfailed=$(printf '%s\n' "$failed" | grep -c .)
      LOCAL_FAILED=$nfailed
      printf '  %-14s %s\n' "units" "✗ $nfailed failed: $(printf '%s' "$failed" | tr '\n' ' ')"
      ACT+=("$nfailed failed user unit(s): $(printf '%s' "$failed" | tr '\n' ' ')→ journalctl --user -u <unit> -n 50")
    fi
  fi

  # 2. key timer-backed services — last-run result + age.
  for spec in "${LOCAL_UNITS[@]}"; do
    unit=${spec%%:*}; label=${spec#*:}
    show=$(systemctl --user show "$unit" \
      -p LoadState -p ActiveState -p SubState -p Result \
      -p ExecMainExitTimestampMonotonic -p ExecMainStartTimestampMonotonic \
      2>/dev/null); rc=$?
    # A unit that is genuinely not installed answers rc=0 with
    # LoadState=not-found. A NON-ZERO rc means the question was never asked, so
    # it is "n/a", not "absent" — "absent" is a positive claim about this host.
    if [ "$rc" -ne 0 ]; then
      printf '  %-14s %s\n' "$label" "— systemctl n/a"
      LOCAL_DEGRADED=1
      continue
    fi
    # first unit we could actually read -> the unhealthy tally is now a real
    # measurement and may be spelled as a number.
    [ -z "${LOCAL_BAD}" ] && LOCAL_BAD=0
    load=$(printf '%s\n' "$show" | awk -F= '$1=="LoadState"{print $2}')
    active=$(printf '%s\n' "$show" | awk -F= '$1=="ActiveState"{print $2}')
    sub=$(printf '%s\n' "$show" | awk -F= '$1=="SubState"{print $2}')
    result=$(printf '%s\n' "$show" | awk -F= '$1=="Result"{print $2}')
    if [ -z "${show:-}" ] || [ "$load" = not-found ] || [ "$load" = masked ]; then
      printf '  %-14s %s\n' "$label" "— absent"
      continue
    fi
    runflag=0
    { [ "$active" = active ] || [ "$active" = activating ]; } && [ "$sub" = running ] && runflag=1
    if [ "$runflag" = 1 ]; then
      mono=$(printf '%s\n' "$show" | awk -F= '$1=="ExecMainStartTimestampMonotonic"{print $2}')
    else
      mono=$(printf '%s\n' "$show" | awk -F= '$1=="ExecMainExitTimestampMonotonic"{print $2}')
    fi
    # monotonic µs since boot -> wall age. 0/absent means it has never reached
    # that point, which is "never run", NOT "0s ago".
    age="never run"
    if [ -n "${mono:-}" ] && [ "${mono:-0}" -gt 0 ] 2>/dev/null && [ -n "${up:-}" ]; then
      age=$(( up - mono / 1000000 )); [ "$age" -lt 0 ] && age=0
      if [ "$runflag" = 1 ]; then age="up $(_rel_age "$age")"; else age="$(_rel_age "$age") ago"; fi
    fi
    # tri-state, deliberately: failed / non-success Result -> bad; success or a
    # live daemon -> ok; anything else UNKNOWN rather than assumed healthy.
    if [ "$active" = failed ] || { [ -n "${result:-}" ] && [ "$result" != success ]; }; then
      mark="${result:-failed}"; LOCAL_BAD=$((LOCAL_BAD + 1))
      ACT+=("user unit $unit is ${mark} → journalctl --user -u $unit -n 50")
    elif [ "$result" = success ] || [ "$runflag" = 1 ]; then
      mark=$([ "$runflag" = 1 ] && echo running || echo ok)
    else
      mark="${active:-?}"
    fi
    printf '  %-14s %-11s %s\n' "$label" "$mark" "$age"
  done
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
# (fast, no creds); the full telemetry view is `/initiative-scan`. Skips gracefully if
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
  echo "  in-flight: ${INIT_ACTIVE:-0} active · ${INIT_SLOW:-0} slowing · ${INIT_STALL:-0} stalled  (telemetry off — full: /initiative-scan)"

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
      ACT+=("initiative $slug owed/held → /initiative-scan")
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
if [ "$SCOPE" = all ] || [ "$SCOPE" = local ];   then echo "# local";   scan_local;   fi
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
PR_STATUS="PRs ${PR_OPEN} open across ${PR_SCOPE} (${PR_READY} ready, ${PR_RED} red, ${PR_CONFLICT} conflicting — yours)"
{ [ "$SCOPE" = all ] || [ "$SCOPE" = repos ]; } || PR_STATUS="PRs not scanned"
LOCAL_STATUS=""
# `n/a`, never 0, when the probe could not be taken — see LOCAL_FAILED's comment.
{ [ "$SCOPE" = all ] || [ "$SCOPE" = local ]; } && \
  LOCAL_STATUS=" · Local ${LOCAL_FAILED:-n/a} failed/${LOCAL_BAD:-n/a} unhealthy"
echo "STATUS: ${PR_STATUS} · Deploys ${DEP_WAVE} mid-wave/${DEP_STUCK} stuck · Alerts ${ALERT_LINE:-n/a}${INIT_STATUS}${LOCAL_STATUS}"
# 🔴 A degraded section must never fall through to the unqualified all-clear:
# "nothing needs you" off a probe that asked nothing is the loudest lie this
# script can print.
#
# ⚠ SCOPE, stated because an earlier wording here overclaimed. This list covers
# PRs and local host health ONLY -- `deploys`, `alerts`, `state` and
# `initiatives` contribute nothing to it, so it is NOT "every section that could
# not measure". And it is printed in the `elif` below, so when ACTIONS is
# non-empty the degradation is not NAMED at all. Nothing is spelled as a
# measured zero either way -- the discriminant survives in the STATUS line
# above (`Local n/a failed/...`) -- but the naming is lost, and a comment
# promising more than the code delivers is how the next reader stops checking.
DEGRADED=()
{ [ "$PR_ERR" -gt 0 ] || [ "$PR_DISCOVERY" = failed ]; } && DEGRADED+=("PRs: ${PR_SCOPE}")
[ "${LOCAL_DEGRADED:-0}" = 1 ] && DEGRADED+=("local host health: systemctl/user manager unreachable")
if [ "${#ACT[@]}" -gt 0 ]; then
  echo "ACTIONS"
  printf '  - %s\n' "${ACT[@]}"
elif [ "${#DEGRADED[@]}" -gt 0 ]; then
  # Never print an unqualified all-clear off a scan that could not see everything.
  DEG_MSG=$(IFS=$'\1'; printf '%s' "${DEGRADED[*]}")
  echo "Nothing flagged IN WHAT WAS SCANNED — coverage was degraded (${DEG_MSG//$'\1'/; })."
else
  # ONE branch, and it always names the scope: a `standup.sh alerts` run that
  # printed a bare "All clear" would be claiming something about the four
  # sections it never looked at. One path so the phrase is testable.
  echo "All clear in scope '$SCOPE' — nothing needs you."
fi
echo "Filtered: dp-1 fan-in split by cluster · known-noise (${NOISE_RE//|/, }) · release-bot repos (${PR_REPO_EXCLUDE[*]}) · KubeJobFailed=accumulated history · submodel-GPU disk = not prod"
