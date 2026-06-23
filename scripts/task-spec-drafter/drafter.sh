#!/usr/bin/env bash
#
# drafter.sh — continuous deep-context task-spec drafter v1 (SHADOW-first).
#
# Source: TICKET (ClickUp "To Schedule" triage queue).
#
# For each ticket in the live ClickUp triage queue it runs the validated
# deep-context pipeline (ENRICH -> VERIFY vs live git/PRs/metrics/config ->
# CORRELATE on verified links -> CLASSIFY -> DRAFT only genuine TASKs; safety
# rule: can't-verify-intent -> flag NEEDS-DECISION, never draft a harmful task)
# via a headless `claude -p` call that has read-only access to clickup + gh +
# kubectl. It emits a structured shadow queue (JSONL + a human summary) and
# routes the genuine/decision-worthy items to clawgate.
#
# This is a VERIFIER / TRIAGE layer, not a task-factory: most inbound dissolves
# on verification; it surfaces only the few that are decision-ready.
#
# === SAFETY: read-only + shadow-first ======================================
#   * Makes NO writes to ClickUp / repos / cluster. Sources are read-only and
#     the claude pass is launched with --permission-mode plan.
#   * Dispatches NOTHING. It only produces a queue + (optionally) a clawgate notice.
#   * Default mode is SHADOW: write the queue to a file and LOG "would send",
#     send nothing live, until you flip DRAFTER_MODE=on.
#
# === Flag (mirrors githooks/audit-on-push.sh) ==============================
# Config sourced from ~/.claude/task-spec-drafter.env if present, else env, else
# defaults below. Master knob DRAFTER_MODE:
#   off    — do nothing
#   shadow — run the full pipeline, write the queue, LOG what it WOULD send to
#            clawgate, send NOTHING  (DEFAULT)
#   on     — run + actually POST the triage summary to clawgate
#
# Other knobs:
#   DRAFTER_MAX_TICKETS   cap tickets processed per run (def 0 = all)
#   DRAFTER_TIMEOUT       seconds budget per ticket's claude -p call (def 240)
#   DRAFTER_OUT_DIR       where shadow queues are written (def ~/.claude/task-spec-drafter)
#   DRAFTER_LOG_FILE      run log                          (def $OUT_DIR/drafter.log)
#   CLICKUP_VIEW_ID       triage view (def 6-901111220963-1 = "To Schedule")
#   CLAWGATE_API_URL / CLAWGATE_HOOK_TOKEN — reused from ~/.claude/clawgate.env
#
set -uo pipefail

# --- locate self + companions ----------------------------------------------
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="$SELF_DIR/drafter-prompt.md"
CLICKUP_CLI="${CLICKUP_CLI:-$HOME/.claude/skills/clickup/query.mjs}"
CLICKUP_ACCOUNTS="${CLICKUP_ACCOUNTS:-$HOME/.claude/skills/clickup/accounts.json}"

# --- config ----------------------------------------------------------------
CLAWGATE_CONF="${CLAWGATE_CONF_FILE:-$HOME/.claude/clawgate.env}"
DRAFTER_CONF="${DRAFTER_CONF_FILE:-$HOME/.claude/task-spec-drafter.env}"
[ -f "$CLAWGATE_CONF" ] && { set -a; . "$CLAWGATE_CONF" 2>/dev/null || true; set +a; }
[ -f "$DRAFTER_CONF" ]  && { set -a; . "$DRAFTER_CONF"  2>/dev/null || true; set +a; }

DRAFTER_MODE="${DRAFTER_MODE:-shadow}"
DRAFTER_MAX_TICKETS="${DRAFTER_MAX_TICKETS:-0}"
DRAFTER_TIMEOUT="${DRAFTER_TIMEOUT:-240}"
DRAFTER_OUT_DIR="${DRAFTER_OUT_DIR:-$HOME/.claude/task-spec-drafter}"
DRAFTER_LOG_FILE="${DRAFTER_LOG_FILE:-$DRAFTER_OUT_DIR/drafter.log}"
CLICKUP_VIEW_ID="${CLICKUP_VIEW_ID:-6-901111220963-1}"
API_URL="${CLAWGATE_API_URL:-http://192.168.50.250:30302}"
HOOK_TOKEN="${CLAWGATE_HOOK_TOKEN:-}"
HOST="${CLAUDE_HOST:-$(hostname 2>/dev/null || echo unknown)}"

CIVITAI_REPO="${CIVITAI_REPO:-/home/zach/workspace/civit/civitai}"
PROD_KUBECONFIG="${PROD_KUBECONFIG:-/home/zach/workspace/civit/datapacket-talos/prod-kubeconfig}"

# Tight READ-ONLY allowlist for the headless pipeline pass. Only verbs that read
# state appear here — no apply/edit/delete/scale/commit/push/comment. This is the
# enforcement complement to the prompt's HARD CONSTRAINTS. Override via env if a
# verification source needs a verb not listed.
DRAFTER_ALLOWED_TOOLS="${DRAFTER_ALLOWED_TOOLS:-Read,Glob,Grep,WebFetch,Bash(node *query.mjs get*),Bash(node *query.mjs comments*),Bash(node *query.mjs search*),Bash(git -C * log*),Bash(git -C * show*),Bash(git -C * diff*),Bash(git -C * grep*),Bash(git log*),Bash(gh pr list*),Bash(gh pr view*),Bash(gh search*),Bash(gh api*),Bash(kubectl get*),Bash(kubectl logs*),Bash(kubectl describe*),Bash(kubectl top*),Bash(curl -s*),Bash(grep*),Bash(rg*),Bash(jq*),Bash(cat*),Bash(echo*),Bash(date*),Bash(env*)}"

mkdir -p "$DRAFTER_OUT_DIR" 2>/dev/null || true
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$DRAFTER_LOG_FILE" >&2; }

RUN_TS="$(date '+%Y%m%dT%H%M%S')"
QUEUE_JSONL="$DRAFTER_OUT_DIR/queue-$RUN_TS.jsonl"
QUEUE_SUMMARY="$DRAFTER_OUT_DIR/queue-$RUN_TS.md"

# --- gate 0: master flag ---------------------------------------------------
case "$DRAFTER_MODE" in
  off|OFF|0|false|no) log "DRAFTER_MODE=off; nothing to do"; exit 0 ;;
  shadow|on) ;;
  *) log "unknown DRAFTER_MODE=$DRAFTER_MODE; treating as off"; exit 0 ;;
esac

# --- preflight -------------------------------------------------------------
command -v claude >/dev/null 2>&1 || { log "FATAL: claude CLI missing"; exit 1; }
command -v node   >/dev/null 2>&1 || { log "FATAL: node missing"; exit 1; }
command -v jq     >/dev/null 2>&1 || { log "FATAL: jq missing"; exit 1; }
command -v curl   >/dev/null 2>&1 || { log "FATAL: curl missing"; exit 1; }
[ -f "$PROMPT_FILE" ]   || { log "FATAL: prompt missing at $PROMPT_FILE"; exit 1; }
[ -f "$CLICKUP_CLI" ]   || { log "FATAL: clickup CLI missing at $CLICKUP_CLI"; exit 1; }

# ClickUp API token (read from the clickup skill's accounts.json) — used only to
# fetch the view list; the per-ticket pipeline uses the clickup skill CLI itself.
CU_TOKEN="$(node -e "const a=require('$CLICKUP_ACCOUNTS');const acc=a.accounts[a.defaultAccount];process.stdout.write(acc.apiToken)" 2>/dev/null)"
[ -n "$CU_TOKEN" ] || { log "FATAL: could not read ClickUp token from $CLICKUP_ACCOUNTS"; exit 1; }

log "=== run $RUN_TS mode=$DRAFTER_MODE view=$CLICKUP_VIEW_ID ==="

# --- fetch the triage queue (paginated /view/<id>/task) --------------------
fetch_queue() {
  local page=0 last=false
  while [ "$last" != "true" ]; do
    local body
    body="$(curl -sf --max-time 30 -H "Authorization: $CU_TOKEN" \
      "https://api.clickup.com/api/v2/view/$CLICKUP_VIEW_ID/task?page=$page" 2>/dev/null)" || break
    # emit "id\tstatus\tname" per task, set last from last_page
    printf '%s' "$body" | node -e '
      let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{
        let j;try{j=JSON.parse(d)}catch(e){process.exit(3)}
        for(const t of (j.tasks||[])) process.stdout.write(`${t.id}\t${(t.status&&t.status.status)||""}\t${(t.name||"").replace(/\s+/g," ").trim()}\n`);
        process.stderr.write((j.last_page?"LAST":"MORE")+"\n");
      });' 2>"$DRAFTER_OUT_DIR/.lastpage"
    grep -q LAST "$DRAFTER_OUT_DIR/.lastpage" 2>/dev/null && last=true
    page=$((page+1))
    [ "$page" -gt 20 ] && break   # safety cap
  done
  rm -f "$DRAFTER_OUT_DIR/.lastpage" 2>/dev/null || true
}

QUEUE_TSV="$(fetch_queue)"
TOTAL="$(printf '%s\n' "$QUEUE_TSV" | grep -c . || true)"
[ "${TOTAL:-0}" -gt 0 ] || { log "FATAL: triage queue empty / unreachable"; exit 1; }
log "fetched $TOTAL tickets from triage view"

if [ "${DRAFTER_MAX_TICKETS:-0}" -gt 0 ]; then
  QUEUE_TSV="$(printf '%s\n' "$QUEUE_TSV" | head -n "$DRAFTER_MAX_TICKETS")"
  log "capped to first $DRAFTER_MAX_TICKETS tickets (DRAFTER_MAX_TICKETS)"
fi

# --- per-ticket pipeline ---------------------------------------------------
: > "$QUEUE_JSONL"
N=0; FAIL=0
PROMPT_BODY="$(cat "$PROMPT_FILE")"

while IFS=$'\t' read -r TID TSTATUS TNAME <&3; do
  [ -n "$TID" ] || continue
  N=$((N+1))
  log "[$N/$TOTAL] $TID  ($TSTATUS)  ${TNAME:0:60}"

  PROMPT="$PROMPT_BODY

---
## THIS TICKET
ticket_id: $TID
title: $TNAME
current_status: $TSTATUS

Environment for your tools:
  CLICKUP_CLI: node $CLICKUP_CLI get $TID   |   node $CLICKUP_CLI comments $TID --threads
  CIVITAI_REPO: $CIVITAI_REPO
  PROD_KUBECONFIG: $PROD_KUBECONFIG

Run the five-step pipeline on ticket $TID now and output ONLY the json block."

  # The pipeline NEEDS its read-only tools to actually execute non-interactively
  # (the whole point is verifying vs live git/PRs/metrics — `--permission-mode
  # plan` blocks execution and the model reasons from the title only, the exact
  # failure mode this design exists to kill). So we grant a TIGHT allowlist of
  # read-only commands via --allowedTools instead of bypassing permissions. The
  # prompt's HARD CONSTRAINTS forbid writes; this allowlist enforces it too (no
  # apply/edit/delete/commit/push/comment verbs are listed).
  #
  # NB: </dev/null so the headless claude never reads the loop's stdin (the
  # ticket list); without it, claude consumes the rest of the queue and the loop
  # exits after one iteration.
  RAW="$(timeout "$DRAFTER_TIMEOUT" \
    env KUBECONFIG="$PROD_KUBECONFIG" \
    claude -p "$PROMPT" \
      --allowedTools "$DRAFTER_ALLOWED_TOOLS" \
      </dev/null 2>>"$DRAFTER_LOG_FILE")"
  RC=$?
  if [ $RC -ne 0 ]; then
    log "  ! claude rc=$RC (timeout/error) — emitting error record"
    jq -nc --arg id "$TID" --arg t "$TNAME" --arg s "$TSTATUS" \
      '{ticket_id:$id,title:$t,status:$s,classification:"ERROR",confidence:"low",verification:"claude pass failed (timeout/error)",correlations:[],recommendation:"re-run",spec:{goal:"",done:"",owner:"",autonomy:""},safety_flag:""}' \
      >> "$QUEUE_JSONL"
    FAIL=$((FAIL+1))
    continue
  fi

  # Extract the json block (fenced or bare), validate, normalize.
  JSON="$(printf '%s' "$RAW" | awk '
    /```json/{f=1;next} /```/{if(f){f=0}} f{print}')"
  [ -n "$JSON" ] || JSON="$(printf '%s' "$RAW" | sed -n '/^[[:space:]]*{/,/^[[:space:]]*}[[:space:]]*$/p')"
  if printf '%s' "$JSON" | jq -e . >/dev/null 2>&1; then
    printf '%s' "$JSON" | jq -c --arg id "$TID" '. + {ticket_id:$id}' >> "$QUEUE_JSONL"
    CLS="$(printf '%s' "$JSON" | jq -r '.classification // "?"')"
    log "  -> $CLS"
  else
    log "  ! unparseable output — emitting error record"
    jq -nc --arg id "$TID" --arg t "$TNAME" --arg s "$TSTATUS" \
      '{ticket_id:$id,title:$t,status:$s,classification:"ERROR",confidence:"low",verification:"unparseable claude output",correlations:[],recommendation:"re-run",spec:{goal:"",done:"",owner:"",autonomy:""},safety_flag:""}' \
      >> "$QUEUE_JSONL"
    FAIL=$((FAIL+1))
  fi
done 3<<< "$QUEUE_TSV"

PROCESSED="$N"
log "processed=$PROCESSED parse_failures=$FAIL queue=$QUEUE_JSONL"

# --- build the human summary (counts by class + the genuine/decision items) -
{
  echo "# Task-spec drafter — shadow queue $RUN_TS"
  echo
  echo "Source: ClickUp triage view \`$CLICKUP_VIEW_ID\` · processed $PROCESSED of $TOTAL · mode=$DRAFTER_MODE"
  echo
  echo "## Classification counts"
  echo
  jq -r '.classification' "$QUEUE_JSONL" | sort | uniq -c | sort -rn | sed 's/^/    /'
  echo
  echo "## Action-worthy (TASK / NEEDS-DECISION / VERIFY / DUPLICATE / safety-flagged)"
  echo
  jq -r 'select(.classification=="TASK" or .classification=="NEEDS-DECISION" or .classification=="VERIFY" or .classification=="DUPLICATE" or (.safety_flag|length>0))
         | "### [\(.classification)] \(.ticket_id) — \(.title)\n- confidence: \(.confidence)  age: \(.age_days // "?")d  status: \(.status // "?")\n- verified: \(.verification)\n" +
           (if (.correlations|length>0) then "- correlations: \(.correlations|join("; "))\n" else "" end) +
           (if (.classification=="TASK") then "- SPEC goal: \(.spec.goal)\n- SPEC done(verifier): \(.spec.done)\n- SPEC owner: \(.spec.owner)  autonomy: \(.spec.autonomy)\n" else "- recommendation: \(.recommendation)\n" end) +
           (if (.safety_flag|length>0) then "- ⚠ SAFETY: \(.safety_flag)\n" else "" end)' \
     "$QUEUE_JSONL"
  echo
  echo "## Suppressed (FYI / STALE-close / ALREADY-DONE, one-liners)"
  echo
  jq -r 'select(.classification=="FYI" or .classification=="STALE-close" or .classification=="ALREADY-DONE")
         | "- [\(.classification)] \(.ticket_id) \(.title) — \(.recommendation // .verification)"' \
     "$QUEUE_JSONL"
} > "$QUEUE_SUMMARY"

log "summary written: $QUEUE_SUMMARY"
ln -sf "$(basename "$QUEUE_JSONL")"  "$DRAFTER_OUT_DIR/latest.jsonl"
ln -sf "$(basename "$QUEUE_SUMMARY")" "$DRAFTER_OUT_DIR/latest.md"

# --- route to clawgate (or shadow-log) -------------------------------------
N_TASK="$(jq -r 'select(.classification=="TASK")|.ticket_id' "$QUEUE_JSONL" | grep -c . || true)"
N_DEC="$(jq -r 'select(.classification=="NEEDS-DECISION" or (.safety_flag|length>0))|.ticket_id' "$QUEUE_JSONL" | grep -c . || true)"
N_VER="$(jq -r 'select(.classification=="VERIFY")|.ticket_id' "$QUEUE_JSONL" | grep -c . || true)"
SUMMARY_LINE="$N_TASK genuine TASK, $N_DEC needs-decision, $N_VER verify (of $PROCESSED inbound)"

# Build a compact context array: one line per action-worthy item.
CONTEXT_JSON="$(jq -sc '
  map(select(.classification=="TASK" or .classification=="NEEDS-DECISION" or .classification=="VERIFY" or (.safety_flag|length>0)))
  | map("[\(.classification)] \(.ticket_id): " + (if .classification=="TASK" then .spec.goal else .recommendation end)
        + (if (.safety_flag|length>0) then "  ⚠\(.safety_flag)" else "" end))
  | (["Source: ClickUp triage · \($n)"] + .)' \
  --arg n "$SUMMARY_LINE" "$QUEUE_JSONL" 2>/dev/null)"
[ -n "$CONTEXT_JSON" ] || CONTEXT_JSON='["(no action-worthy items)"]'

if [ "$DRAFTER_MODE" = "shadow" ]; then
  log "SHADOW: would POST to clawgate — $SUMMARY_LINE"
  log "SHADOW: clawgate context would be:"
  printf '%s\n' "$CONTEXT_JSON" | jq -r '.[]' | sed 's/^/  /' | tee -a "$DRAFTER_LOG_FILE" >&2
  log "=== run $RUN_TS done (shadow, nothing sent) ==="
  exit 0
fi

# on: actually notify clawgate.
[ -n "$HOOK_TOKEN" ] || { log "no CLAWGATE_HOOK_TOKEN; cannot send; queue still written"; exit 0; }
PAYLOAD="$(jq -nc \
  --arg tool "Task-spec drafter" \
  --arg command "ClickUp triage ($PROCESSED tickets)" \
  --arg input "$SUMMARY_LINE" \
  --arg host "$HOST" \
  --arg project "task-spec-drafter" \
  --arg cwd "$DRAFTER_OUT_DIR" \
  --argjson context "$CONTEXT_JSON" \
  '{type:"permission",tool:$tool,command:$command,input:$input,host:$host,project:$project,cwd:$cwd,context:$context}')"

if curl -sf --max-time 15 -X POST "$API_URL/api/send" \
     -H 'Content-Type: application/json' \
     -H "Authorization: Bearer $HOOK_TOKEN" \
     -d "$PAYLOAD" >>"$DRAFTER_LOG_FILE" 2>&1; then
  log "SENT to clawgate — $SUMMARY_LINE"
else
  log "clawgate POST failed (unreachable?) — $SUMMARY_LINE (no retry; queue still written)"
fi
log "=== run $RUN_TS done (mode=on) ==="
exit 0
