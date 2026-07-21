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
# === DELTA-SCOPING (cheap continuous runs) =================================
# A processed-state cache (DRAFTER_STATE_FILE, ticket_id -> date_updated) makes
# each run cheap: a ticket is processed ONLY IF it is NEW (not in state) or
# CHANGED (its live date_updated is newer than the stored value). Unchanged
# tickets are skipped ("skipped N unchanged"). State is updated after each ticket
# is handled (processed OR baselined), so the next run sees them as known.
#
# FIRST RUN (empty/missing/corrupt state): does NOT process all ~74 — it processes
# at most DRAFTER_MAX_TICKETS and BASELINES the remainder (records their current
# date_updated without running the model), so they count as "seen" and are only
# processed later when they actually change. A corrupt/unreadable state file is
# treated as empty (never crashes).
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
#   DRAFTER_MODEL         model for the headless claude -p pass (def haiku). Alias
#                         (haiku|sonnet|opus) or full id. Haiku is the cheap shadow
#                         default; the deterministic safety gate (below) compensates
#                         for Haiku's measured intent-ambiguity blind spot.
#   DRAFTER_MAX_TICKETS   cap tickets PROCESSED per run (def 25). This is a
#                         backstop, NOT a per-run target: with delta-scoping
#                         (below) a steady-state run processes only the handful
#                         of NEW/CHANGED tickets. The cap matters on the FIRST
#                         run (empty state): instead of processing all ~74 it
#                         processes the cap and BASELINES the rest (records their
#                         date_updated as "seen" so they're only re-processed
#                         when they change). Set 0 to disable the cap.
#   DRAFTER_STATE_FILE    processed-state cache, ticket_id -> date_updated, used
#                         for delta-scoping (def $OUT_DIR/processed.json)
#   DRAFTER_TIMEOUT       seconds budget per ticket's claude -p call (def 240)
#   DRAFTER_OUT_DIR       where shadow queues are written (def ~/.claude/task-spec-drafter)
#   DRAFTER_LOG_FILE      run log                          (def $OUT_DIR/drafter.log)
#   CLICKUP_VIEW_ID       triage view (def 6-901111220963-1 = "To Schedule")
#   CLAWGATE_API_URL / CLAWGATE_HOOK_TOKEN — reused from ~/.claude/clawgate.env
#
#   DRAFTER_EMAIL         on|off — email the day's digest (the review surface, def on)
#   DRAFTER_EMAIL_TO      digest recipient (def owner)
#   DRAFTER_EMAIL_DRYRUN  1 -> render the digest email to a file, send NOTHING (proofs)
#   REPO_COS_PROD_KUBECONFIG — relay kubeconfig for the digest send (reuses repo-cos)
#
# === REVIEW SURFACE: daily email digest ====================================
# On every run that processes ≥1 ticket, the human summary (counts + per-ticket
# classification + one-line why + drafted spec for TASKs) is EMAILED (reusing
# repo-cos's DKIM-signed postfix relay). In SHADOW this is the whole point: the
# triage lands in Zach's inbox to adjudicate, while clawgate/dispatch stay silent.
#
set -uo pipefail

# --- locate self + companions ----------------------------------------------
# Unset CDPATH + silence cd: with CDPATH set in the caller's env, a relative
# `cd` prints the resolved dir to stdout, which the command substitution would
# capture as a spurious extra line and corrupt SELF_DIR. (The systemd unit env
# has no CDPATH, but be robust for hand-runs too.)
SELF_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROMPT_FILE="$SELF_DIR/drafter-prompt.md"
SEND_HELPER="$SELF_DIR/send_digest.py"
CLICKUP_CLI="${CLICKUP_CLI:-$HOME/.claude/skills/clickup/query.mjs}"
CLICKUP_ACCOUNTS="${CLICKUP_ACCOUNTS:-$HOME/.claude/skills/clickup/accounts.json}"

# --- config ----------------------------------------------------------------
CLAWGATE_CONF="${CLAWGATE_CONF_FILE:-$HOME/.claude/clawgate.env}"
DRAFTER_CONF="${DRAFTER_CONF_FILE:-$HOME/.claude/task-spec-drafter.env}"
[ -f "$CLAWGATE_CONF" ] && { set -a; . "$CLAWGATE_CONF" 2>/dev/null || true; set +a; }
[ -f "$DRAFTER_CONF" ]  && { set -a; . "$DRAFTER_CONF"  2>/dev/null || true; set +a; }

DRAFTER_MODE="${DRAFTER_MODE:-shadow}"
DRAFTER_MODEL="${DRAFTER_MODEL:-haiku}"
DRAFTER_MAX_TICKETS="${DRAFTER_MAX_TICKETS:-25}"
DRAFTER_TIMEOUT="${DRAFTER_TIMEOUT:-240}"
DRAFTER_OUT_DIR="${DRAFTER_OUT_DIR:-$HOME/.claude/task-spec-drafter}"
DRAFTER_STATE_FILE="${DRAFTER_STATE_FILE:-$DRAFTER_OUT_DIR/processed.json}"
DRAFTER_LOG_FILE="${DRAFTER_LOG_FILE:-$DRAFTER_OUT_DIR/drafter.log}"
CLICKUP_VIEW_ID="${CLICKUP_VIEW_ID:-6-901111220963-1}"
API_URL="${CLAWGATE_API_URL:-http://192.168.50.250:30302}"
HOOK_TOKEN="${CLAWGATE_HOOK_TOKEN:-}"
HOST="${CLAUDE_HOST:-$(hostname 2>/dev/null || echo unknown)}"

CIVITAI_REPO="${CIVITAI_REPO:-/home/zach/workspace/civit/civitai}"
PROD_KUBECONFIG="${PROD_KUBECONFIG:-/home/zach/workspace/civit/datapacket-talos/prod-kubeconfig}"

# --- daily email digest (the SHADOW review surface) ------------------------
# Fires in BOTH shadow and on: the day's triage is emailed so Zach adjudicates
# from his inbox. Reuses repo-cos's DKIM-signed postfix relay (send_digest.py ->
# repo-cos/email_send.py), so the relay needs REPO_COS_PROD_KUBECONFIG + kubectl.
#   DRAFTER_EMAIL         on|off — send the digest email at all      (def on)
#   DRAFTER_EMAIL_TO      recipient                                   (def owner)
#   DRAFTER_EMAIL_DRYRUN  1 -> render the email to a file, send NOTHING (proof runs)
DRAFTER_EMAIL="${DRAFTER_EMAIL:-on}"
DRAFTER_EMAIL_TO="${DRAFTER_EMAIL_TO:-zachlowden1@gmail.com}"
DRAFTER_EMAIL_DRYRUN="${DRAFTER_EMAIL_DRYRUN:-0}"
# Relay kubeconfig for the email path (production cluster; same one repo-cos uses).
export REPO_COS_PROD_KUBECONFIG="${REPO_COS_PROD_KUBECONFIG:-$HOME/workspace/homelab-talos/production-kubeconfig}"

# Tight READ-ONLY allowlist for the headless pipeline pass. Only verbs that read
# state appear here — no apply/edit/delete/scale/commit/push/comment. This is the
# enforcement complement to the prompt's HARD CONSTRAINTS. Override via env if a
# verification source needs a verb not listed.
DRAFTER_ALLOWED_TOOLS="${DRAFTER_ALLOWED_TOOLS:-Read,Glob,Grep,WebFetch,Bash(node *query.mjs get*),Bash(node *query.mjs comments*),Bash(node *query.mjs search*),Bash(git -C * log*),Bash(git -C * show*),Bash(git -C * diff*),Bash(git -C * grep*),Bash(git log*),Bash(gh pr list*),Bash(gh pr view*),Bash(gh search*),Bash(gh api*),Bash(kubectl get*),Bash(kubectl logs*),Bash(kubectl describe*),Bash(kubectl top*),Bash(curl -s*),Bash(grep*),Bash(rg*),Bash(jq*),Bash(cat*),Bash(echo*),Bash(date*),Bash(env*)}"

mkdir -p "$DRAFTER_OUT_DIR" 2>/dev/null || true
# The delta-state file may live outside OUT_DIR (e.g. ~/.local/state/...); make
# sure its parent exists so update_state's atomic .tmp+mv never fails.
mkdir -p "$(dirname "$DRAFTER_STATE_FILE")" 2>/dev/null || true
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$DRAFTER_LOG_FILE" >&2; }

# === DETERMINISTIC SAFETY-ESCALATION GATE ==================================
# Structural (regex/keyword) escalation that does NOT trust the model's
# self-assessment. A measured test showed Haiku runs the verify tools fine but
# lacks the judgment to flag intent-ambiguity (it confidently mis-drafted the
# safety-critical "Civitai Link on .red" cert ticket as a high-confidence TASK
# with no safety_flag; Opus correctly said NEEDS-DECISION). So for the dangerous
# classes we pin the label in code, not in the prompt.
#
# Rule: scan the ticket text (title + body + comments) AND the model's own
# verification/spec text. If it matches a RISK category, FORCE the record to
# classification=NEEDS-DECISION, autonomy=needs-Zach, never auto-dispatch,
# overriding whatever the model returned, and stamp safety_flag + gate_fired.
#
# Categories (word-boundary, case-insensitive). Tune the lists here.
GATE_RE_SECURITY='\b(cert|certs|tls|ssl|mtls|mta-?sts|secret|secrets|token|tokens|credential|credentials|password|passwd|auth|authn|authz|rbac|vuln|vulnerability|cve|disclosure|exploit|x\.509|x509|\.red)\b'
GATE_RE_MONEY='\b(buzz|currency|payment|payments|refund|refunds|withdraw|withdrawal|payout|payouts|billing|invoice|stripe|paypal|subscription|chargeback|wallet|merch)\b'
GATE_RE_DESTRUCTIVE='\b(delete|deletes|deletion|drop|truncate|migration|migrations|rollback|restore|prod|production|scale ?down|scale-down|evict|eviction|wipe|purge|destroy|terraform destroy|drop table|force ?push)\b'

# safety_gate <ticket_text_file> <model_json>  -> prints possibly-rewritten json
# Returns the json on stdout. Sets nothing else.
safety_gate() {
  local txt_file="$1" json="$2"
  # Combine ticket text with the model's verification/recommendation/spec so the
  # gate sees both the raw inbound and whatever reality the model surfaced.
  local model_text
  model_text="$(printf '%s' "$json" | jq -r '[.title,.verification,.recommendation,.spec.goal,.spec.done]|map(select(.!=null))|join(" ")' 2>/dev/null)"
  local scan
  scan="$(printf '%s\n%s' "$(cat "$txt_file" 2>/dev/null)" "$model_text")"

  local cats=""
  printf '%s' "$scan" | grep -Eiq "$GATE_RE_SECURITY"     && cats="${cats}security/secrets, "
  printf '%s' "$scan" | grep -Eiq "$GATE_RE_MONEY"        && cats="${cats}money, "
  printf '%s' "$scan" | grep -Eiq "$GATE_RE_DESTRUCTIVE"  && cats="${cats}destructive/prod-mutation, "
  cats="${cats%, }"

  if [ -z "$cats" ]; then
    # no risk match: pass through, just mark gate_fired=false for audit
    printf '%s' "$json" | jq -c '. + {gate_fired:false}'
    return 0
  fi

  # RISK match: force escalation, overriding the model. Preserve the model's
  # original classification for audit (gate_override_from), blank the spec
  # (it is no longer a dispatchable TASK), and stamp/extend the safety_flag.
  printf '%s' "$json" | jq -c \
    --arg cats "$cats" '
      . as $orig
      | .gate_fired = true
      | .gate_categories = ($cats | split(", "))
      | .gate_override_from = ($orig.classification // "?")
      | .classification = "NEEDS-DECISION"
      | (.confidence) = (if (.confidence=="high") then "medium" else (.confidence // "low") end)
      | .spec = {goal:"",done:"",owner:(.spec.owner // ""),autonomy:"needs-Zach"}
      | .recommendation = ((.recommendation // "") | if .=="" then "" else . + " " end) + "ESCALATED by deterministic safety gate (" + $cats + "): risk keywords present; needs Zach. Do NOT auto-dispatch."
      | .safety_flag = (
          (if (($orig.safety_flag // "")|length>0) then ($orig.safety_flag + " | ") else "" end)
          + "deterministic gate fired [" + $cats + "]: structural risk match — intent must be verified by a human before any action (model self-assessment not trusted for these classes)."
        )
    '
}

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

log "=== run $RUN_TS mode=$DRAFTER_MODE model=$DRAFTER_MODEL view=$CLICKUP_VIEW_ID ==="

# --- fetch the triage queue (paginated /view/<id>/task) --------------------
fetch_queue() {
  local page=0 last=false
  while [ "$last" != "true" ]; do
    local body
    body="$(curl -sf --max-time 30 -H "Authorization: $CU_TOKEN" \
      "https://api.clickup.com/api/v2/view/$CLICKUP_VIEW_ID/task?page=$page" 2>/dev/null)" || break
    # emit "id\tstatus\tname\tdate_updated" per task, set last from last_page
    printf '%s' "$body" | node -e '
      let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{
        let j;try{j=JSON.parse(d)}catch(e){process.exit(3)}
        for(const t of (j.tasks||[])) process.stdout.write(`${t.id}\t${(t.status&&t.status.status)||""}\t${(t.name||"").replace(/\s+/g," ").trim()}\t${t.date_updated||t.date_created||""}\n`);
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

# === DELTA-SCOPING: load processed-state, classify each ticket =============
# State file: {"<ticket_id>": "<date_updated_ms>", ...}. Robust to missing /
# empty / corrupt (treated as empty object {} — never crashes).
STATE_OBJ="$(jq -ec 'if type=="object" then . else {} end' "$DRAFTER_STATE_FILE" 2>/dev/null)"
if [ -z "$STATE_OBJ" ]; then
  [ -f "$DRAFTER_STATE_FILE" ] && log "WARN: state file unreadable/corrupt ($DRAFTER_STATE_FILE) — treating as empty"
  STATE_OBJ='{}'
fi
STATE_COUNT="$(printf '%s' "$STATE_OBJ" | jq -r 'length')"
FIRST_RUN=false
[ "${STATE_COUNT:-0}" -eq 0 ] && FIRST_RUN=true
log "delta: state has $STATE_COUNT known ticket(s)$( [ "$FIRST_RUN" = true ] && echo ' (FIRST RUN — empty state)')"

# Partition the fetched queue into: TO_PROCESS (new/changed, subject to cap) and
# TO_BASELINE (everything we won't run this round but want to record as seen so
# it isn't reprocessed until it changes). Unchanged tickets are simply skipped.
# We emit two TSV streams to temp files using the live STATE_OBJ for lookups.
CAP="${DRAFTER_MAX_TICKETS:-0}"
PROCESS_TSV="$DRAFTER_OUT_DIR/.process.$$"
BASELINE_TSV="$DRAFTER_OUT_DIR/.baseline.$$"
: > "$PROCESS_TSV"; : > "$BASELINE_TSV"
SKIPPED=0; NEW_OR_CHANGED=0; BASELINED=0
while IFS=$'\t' read -r TID TSTATUS TNAME TUPD; do
  [ -n "$TID" ] || continue
  PREV="$(printf '%s' "$STATE_OBJ" | jq -r --arg id "$TID" '.[$id] // ""')"
  # new (no prior) OR changed (live date_updated strictly newer than stored).
  if [ -z "$PREV" ] || { [ -n "$TUPD" ] && [ "$TUPD" -gt "$PREV" ] 2>/dev/null; }; then
    if [ "$CAP" -gt 0 ] && [ "$NEW_OR_CHANGED" -ge "$CAP" ]; then
      # Over the cap this run: baseline it (record date_updated as seen) so a
      # first-run / surge doesn't blow up cost; it'll be picked up when it changes.
      printf '%s\t%s\n' "$TID" "$TUPD" >> "$BASELINE_TSV"; BASELINED=$((BASELINED+1))
    else
      printf '%s\t%s\t%s\t%s\n' "$TID" "$TSTATUS" "$TNAME" "$TUPD" >> "$PROCESS_TSV"
      NEW_OR_CHANGED=$((NEW_OR_CHANGED+1))
    fi
  else
    SKIPPED=$((SKIPPED+1))
  fi
done <<< "$QUEUE_TSV"

TO_PROCESS="$(cat "$PROCESS_TSV" 2>/dev/null)"
PROC_TOTAL="$(printf '%s\n' "$TO_PROCESS" | grep -c . || true)"
log "delta: $PROC_TOTAL to process (new/changed), $BASELINED baselined (over cap=$CAP), skipped $SKIPPED unchanged"

# --- per-ticket pipeline ---------------------------------------------------
: > "$QUEUE_JSONL"
N=0; FAIL=0
PROMPT_BODY="$(cat "$PROMPT_FILE")"

# update_state <ticket_id> <date_updated> — merge into STATE_OBJ and persist
# atomically. Called after a ticket is processed OR baselined.
update_state() {
  local id="$1" upd="$2"
  STATE_OBJ="$(printf '%s' "$STATE_OBJ" | jq -c --arg id "$id" --arg u "$upd" '.[$id]=$u')"
  printf '%s\n' "$STATE_OBJ" > "$DRAFTER_STATE_FILE.tmp.$$" && mv -f "$DRAFTER_STATE_FILE.tmp.$$" "$DRAFTER_STATE_FILE"
}

# Baseline the over-cap / first-run remainder up front (record as seen, no model).
if [ -s "$BASELINE_TSV" ]; then
  while IFS=$'\t' read -r BID BUPD; do
    [ -n "$BID" ] || continue
    update_state "$BID" "$BUPD"
  done < "$BASELINE_TSV"
  log "delta: baselined $BASELINED ticket(s) into state (recorded as seen, not processed)"
fi
rm -f "$BASELINE_TSV" 2>/dev/null || true

if [ "$PROC_TOTAL" -eq 0 ]; then
  log "delta: nothing new/changed to process this run (skipped $SKIPPED unchanged)"
fi

GATE_HITS=0
TICKET_TXT="$DRAFTER_OUT_DIR/.ticket-text.$$"
while IFS=$'\t' read -r TID TSTATUS TNAME TUPD <&3; do
  [ -n "$TID" ] || continue
  N=$((N+1))
  log "[$N/$PROC_TOTAL] $TID  ($TSTATUS)  ${TNAME:0:60}"

  # Gather the ticket text (title + body + comments) for the DETERMINISTIC safety
  # gate. Read-only clickup CLI; failures degrade to title-only (the title still
  # catches the .red cert + buzz/currency cases). This text is independent of the
  # model — the gate must not depend on the model surfacing the risk.
  {
    printf '%s\n' "$TNAME"
    node "$CLICKUP_CLI" get "$TID" 2>/dev/null
    node "$CLICKUP_CLI" comments "$TID" --threads 2>/dev/null
  } > "$TICKET_TXT" 2>/dev/null || printf '%s\n' "$TNAME" > "$TICKET_TXT"

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
      --model "$DRAFTER_MODEL" \
      --allowedTools "$DRAFTER_ALLOWED_TOOLS" \
      </dev/null 2>>"$DRAFTER_LOG_FILE")"
  RC=$?
  if [ $RC -ne 0 ]; then
    log "  ! claude rc=$RC (timeout/error) — emitting error record"
    ERR_JSON="$(jq -nc --arg id "$TID" --arg t "$TNAME" --arg s "$TSTATUS" \
      '{ticket_id:$id,title:$t,status:$s,classification:"ERROR",confidence:"low",verification:"claude pass failed (timeout/error)",correlations:[],recommendation:"re-run",spec:{goal:"",done:"",owner:"",autonomy:""},safety_flag:""}')"
    safety_gate "$TICKET_TXT" "$ERR_JSON" >> "$QUEUE_JSONL"
    FAIL=$((FAIL+1))
    continue
  fi

  # Extract the json block (fenced or bare), validate, normalize.
  JSON="$(printf '%s' "$RAW" | awk '
    /```json/{f=1;next} /```/{if(f){f=0}} f{print}')"
  [ -n "$JSON" ] || JSON="$(printf '%s' "$RAW" | sed -n '/^[[:space:]]*{/,/^[[:space:]]*}[[:space:]]*$/p')"
  if printf '%s' "$JSON" | jq -e . >/dev/null 2>&1; then
    # Normalize, then run the DETERMINISTIC safety gate (overrides the model for
    # risk classes — Haiku's self-assessment is not trusted here).
    NORM="$(printf '%s' "$JSON" | jq -c --arg id "$TID" '. + {ticket_id:$id}')"
    MODEL_CLS="$(printf '%s' "$NORM" | jq -r '.classification // "?"')"
    GATED="$(safety_gate "$TICKET_TXT" "$NORM")"
    printf '%s\n' "$GATED" >> "$QUEUE_JSONL"
    if [ "$(printf '%s' "$GATED" | jq -r '.gate_fired')" = "true" ]; then
      GATE_HITS=$((GATE_HITS+1))
      GCATS="$(printf '%s' "$GATED" | jq -r '.gate_categories|join(",")')"
      log "  -> $MODEL_CLS  ⛔ GATE FIRED [$GCATS] -> forced NEEDS-DECISION (needs-Zach)"
    else
      log "  -> $MODEL_CLS"
    fi
    # delta: record this ticket-version as processed so it isn't reprocessed
    # until its date_updated changes. Only on a SUCCESSFUL classification — an
    # ERROR/timeout deliberately leaves state untouched so the ticket retries
    # next run.
    update_state "$TID" "$TUPD"
  else
    log "  ! unparseable output — emitting error record (state untouched, will retry)"
    ERR_JSON="$(jq -nc --arg id "$TID" --arg t "$TNAME" --arg s "$TSTATUS" \
      '{ticket_id:$id,title:$t,status:$s,classification:"ERROR",confidence:"low",verification:"unparseable claude output",correlations:[],recommendation:"re-run",spec:{goal:"",done:"",owner:"",autonomy:""},safety_flag:""}')"
    safety_gate "$TICKET_TXT" "$ERR_JSON" >> "$QUEUE_JSONL"
    FAIL=$((FAIL+1))
  fi
done 3<<< "$TO_PROCESS"
rm -f "$TICKET_TXT" "$PROCESS_TSV" 2>/dev/null || true

PROCESSED="$N"
log "processed=$PROCESSED parse_failures=$FAIL gate_escalations=$GATE_HITS skipped_unchanged=$SKIPPED baselined=$BASELINED queue=$QUEUE_JSONL"

# delta no-op short-circuit: if nothing was new/changed this run, there is no new
# queue content. Don't clobber latest.{jsonl,md} (Zach's accumulating adjudication
# surface) with an empty file, and don't POST an empty card to clawgate.
if [ "$PROCESSED" -eq 0 ]; then
  rm -f "$QUEUE_JSONL" "$QUEUE_SUMMARY" 2>/dev/null || true
  log "delta: 0 new/changed — kept previous latest.{jsonl,md}; nothing sent."
  log "=== run $RUN_TS done (delta no-op: skipped $SKIPPED unchanged, $BASELINED baselined) ==="
  exit 0
fi

# --- build the human summary (counts by class + the genuine/decision items) -
{
  echo "# Task-spec drafter — shadow queue $RUN_TS"
  echo
  echo "Source: ClickUp triage view \`$CLICKUP_VIEW_ID\` · queue $TOTAL · processed $PROCESSED new/changed · skipped $SKIPPED unchanged · baselined $BASELINED · model=$DRAFTER_MODEL · mode=$DRAFTER_MODE · deterministic-gate escalations: $GATE_HITS"
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

# --- daily digest email (the SHADOW review surface) ------------------------
# Emails the human summary (QUEUE_SUMMARY: counts + per-ticket classification +
# one-line why + drafted spec for TASKs + suppressed one-liners) so Zach reviews
# the day's triage in his inbox. Fires in BOTH shadow and on — it is the review
# surface regardless of mode; it NEVER dispatches or mutates anything. Best-effort:
# a send failure logs + continues (never wedges / fails the run). Reuses repo-cos's
# relay via send_digest.py; DRAFTER_EMAIL_DRYRUN=1 renders to a file, sends nothing.
if { [ "$DRAFTER_EMAIL" = "on" ] || [ "$DRAFTER_EMAIL" = "1" ]; } \
   && command -v python3 >/dev/null 2>&1 && [ -f "$SEND_HELPER" ]; then
  MODE_TAG="$([ "$DRAFTER_MODE" = "shadow" ] && echo SHADOW || echo LIVE)"
  EMAIL_SUBJECT="task-drafter $MODE_TAG digest — $N_TASK would-dispatch, $N_DEC need-decision"
  if [ "$DRAFTER_EMAIL_DRYRUN" = "1" ]; then
    DIGEST_RENDER="$DRAFTER_OUT_DIR/digest-email-$RUN_TS.txt"
    if python3 "$SEND_HELPER" --subject "$EMAIL_SUBJECT" --body-file "$QUEUE_SUMMARY" \
         --to "$DRAFTER_EMAIL_TO" --dry-run --out "$DIGEST_RENDER" >>"$DRAFTER_LOG_FILE" 2>&1; then
      log "digest email DRY-RUN rendered -> $DIGEST_RENDER (nothing sent): $EMAIL_SUBJECT"
    else
      log "digest email dry-run render FAILED (best-effort; see log)"
    fi
  else
    if python3 "$SEND_HELPER" --subject "$EMAIL_SUBJECT" --body-file "$QUEUE_SUMMARY" \
         --to "$DRAFTER_EMAIL_TO" >>"$DRAFTER_LOG_FILE" 2>&1; then
      log "digest emailed to $DRAFTER_EMAIL_TO: $EMAIL_SUBJECT"
    else
      log "digest email FAILED (best-effort; queue still written): $EMAIL_SUBJECT"
    fi
  fi
elif [ "$DRAFTER_EMAIL" = "on" ] || [ "$DRAFTER_EMAIL" = "1" ]; then
  log "digest email skipped: python3 or send helper unavailable (queue still written)"
else
  log "digest email disabled (DRAFTER_EMAIL=$DRAFTER_EMAIL)"
fi

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
