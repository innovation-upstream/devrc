#!/usr/bin/env bash
#
# audit-on-push.sh — backgrounded worker for the global pre-push hook.
#
# Runs the existing /audit-pr adversarial audit headlessly against the diff being
# pushed, and routes ONLY 🔴/🟡 findings to clawgate (the phone). Clean / 🟢-only
# audits are suppressed so there is no noise.
#
# It is invoked detached by githooks/pre-push, so it must NEVER write to the
# terminal and NEVER affect the push exit code (the push already returned by the
# time the LLM call runs).
#
# === Flag (default = shadow; nothing reaches the phone until you flip it) =====
# Config is sourced from ~/.claude/audit-on-push.env if present, else env, else
# the defaults below. The single knob is AUDIT_ON_PUSH:
#   off    — do nothing at all (cheapest; not even the filters run past the gate)
#   shadow — run all filters + the audit, LOG what it WOULD send, send NOTHING
#            (DEFAULT — safe to install, changes nothing about the push UX)
#   on     — run + actually POST 🔴/🟡 findings to clawgate
#
# Other knobs (env or ~/.claude/audit-on-push.env):
#   AUDIT_MIN_LINES   diff line threshold; below this the audit is skipped (def 40)
#   AUDIT_TIMEOUT     seconds budget for the headless claude call          (def 300)
#   AUDIT_LOG_FILE    where shadow/decision logging goes   (def ~/.claude/audit-on-push.log)
#   CLAWGATE_API_URL / CLAWGATE_HOOK_TOKEN — reused from ~/.claude/clawgate.env
#
# Trigger gates (ALL must pass or it exits 0 silently):
#   - flag != off
#   - branch is a FEATURE branch (NEVER trunk/main/master)
#   - diff (HEAD vs merge-base with upstream/default) >= AUDIT_MIN_LINES changed
#
set -uo pipefail

REMOTE="${1:-}"
URL="${2:-}"
REPO_ROOT="${3:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# --- config ----------------------------------------------------------------
CLAWGATE_CONF="${CLAWGATE_CONF_FILE:-$HOME/.claude/clawgate.env}"
AUDIT_CONF="${AUDIT_CONF_FILE:-$HOME/.claude/audit-on-push.env}"
[ -f "$CLAWGATE_CONF" ] && { set -a; . "$CLAWGATE_CONF" 2>/dev/null || true; set +a; }
[ -f "$AUDIT_CONF" ]    && { set -a; . "$AUDIT_CONF"    2>/dev/null || true; set +a; }

AUDIT_ON_PUSH="${AUDIT_ON_PUSH:-shadow}"
AUDIT_MIN_LINES="${AUDIT_MIN_LINES:-40}"
AUDIT_TIMEOUT="${AUDIT_TIMEOUT:-300}"
AUDIT_LOG_FILE="${AUDIT_LOG_FILE:-$HOME/.claude/audit-on-push.log}"
API_URL="${CLAWGATE_API_URL:-http://192.168.50.250:30302}"
HOOK_TOKEN="${CLAWGATE_HOOK_TOKEN:-}"
HOST="${CLAUDE_HOST:-$(hostname 2>/dev/null || echo unknown)}"

mkdir -p "$(dirname "$AUDIT_LOG_FILE")" 2>/dev/null || true
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"$AUDIT_LOG_FILE" 2>/dev/null || true; }

# --- gate 0: master flag ---------------------------------------------------
case "$AUDIT_ON_PUSH" in
  off|OFF|0|false|no) exit 0 ;;
  shadow|on) ;;
  *) log "unknown AUDIT_ON_PUSH=$AUDIT_ON_PUSH; treating as off"; exit 0 ;;
esac

[ -n "$REPO_ROOT" ] && cd "$REPO_ROOT" 2>/dev/null || exit 0

# --- gate 1: feature-branch filter (NEVER trunk/main/master) ---------------
# Determine the branch being pushed from the ref-update lines on stdin; fall
# back to the current symbolic HEAD.
BRANCH=""
while read -r local_ref _ _ _; do
  case "$local_ref" in
    refs/heads/*) BRANCH="${local_ref#refs/heads/}"; break ;;
  esac
done
[ -n "$BRANCH" ] || BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
[ -n "$BRANCH" ] || { log "no branch resolved; skip"; exit 0; }

case "$BRANCH" in
  trunk|main|master|develop|HEAD)
    log "branch=$BRANCH is protected/non-feature; skip"; exit 0 ;;
esac
# Positive allowlist of feature-branch shapes; anything else is skipped to be
# conservative (no audit on weird detached/special refs).
case "$BRANCH" in
  zach/*|feat/*|feat-*|feature/*|fix/*|fix-*|hotfix/*|bug/*|bugfix/*|chore/*|refactor/*|wip/*|*/*) ;;
  *) log "branch=$BRANCH not a recognized feature branch; skip"; exit 0 ;;
esac

# --- gate 2: non-trivial diff (HEAD vs merge-base with default upstream) ----
# Find a sensible base: upstream tracking branch, else origin/<default>, else
# origin/trunk|main|master, else the parent commit.
default_remote_head() {
  git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null | sed 's#^refs/remotes/##'
}
BASE=""
# Prefer the DEFAULT branch (trunk/main/master) — that's what a PR diffs against.
# We deliberately do NOT use @{upstream} as the primary base: once a feature
# branch tracks its own remote (`git push -u`), @{upstream} resolves to the
# branch's own tip and merge-base(HEAD, upstream) == HEAD -> a bogus empty diff.
dh="$(default_remote_head)"
for ref in "$dh" origin/trunk origin/main origin/master trunk main master; do
  [ -n "$ref" ] || continue
  # Skip an upstream that is just this same branch.
  case "$ref" in *"$BRANCH") continue ;; esac
  if git rev-parse --verify --quiet "$ref" >/dev/null 2>&1; then
    BASE="$(git merge-base HEAD "$ref" 2>/dev/null || true)"
    [ -n "$BASE" ] && [ "$BASE" != "$(git rev-parse HEAD)" ] && break
    BASE=""
  fi
done
# Fall back to @{upstream} ONLY if it isn't this branch's own remote ref.
if [ -z "$BASE" ]; then
  up="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
  case "$up" in
    ""|*"$BRANCH") : ;;
    *) BASE="$(git merge-base HEAD "$up" 2>/dev/null || true)" ;;
  esac
fi
[ -n "$BASE" ] || BASE="$(git rev-parse --verify --quiet 'HEAD^' 2>/dev/null || true)"
[ -n "$BASE" ] || { log "branch=$BRANCH no base for diff; skip"; exit 0; }

CHANGED_LINES="$(git diff --numstat "$BASE"...HEAD 2>/dev/null \
  | awk '{ a=($1=="-"?0:$1); d=($2=="-"?0:$2); s+=a+d } END { print s+0 }')"
if [ "${CHANGED_LINES:-0}" -lt "$AUDIT_MIN_LINES" ]; then
  log "branch=$BRANCH diff=$CHANGED_LINES < $AUDIT_MIN_LINES lines; skip (trivial)"
  exit 0
fi

log "branch=$BRANCH base=${BASE:0:8} diff=$CHANGED_LINES lines mode=$AUDIT_ON_PUSH — running audit"

# --- run the audit headlessly ----------------------------------------------
command -v claude >/dev/null 2>&1 || { log "claude CLI missing; skip"; exit 0; }

PROMPT="/audit-pr current

You are running NON-INTERACTIVELY from a git pre-push hook. Audit the diff of the
current branch ($BRANCH) against its base. Follow the /audit-pr checklist exactly
(risks, regressions, assumptions, gaps, bugs, issues, behaviour changes, leaks,
second-order consequences). Do NOT modify any files. Do NOT merge.

Output format — STRICT, machine-read by the hook:
  Line 1: a verdict token, one of: VERDICT:SAFE | VERDICT:FIX_REQUIRED | VERDICT:REWORK
  Then, ONLY the 🔴 (deploy-blocking) and 🟡 (should-fix) findings, one per line,
  each as: <emoji> <file:line> — <one-line why it matters>
  Omit 🟢 nits entirely. If there are no 🔴/🟡 findings, output exactly:
  VERDICT:SAFE
  CLEAN"

AUDIT_OUT="$(cd "$REPO_ROOT" && timeout "$AUDIT_TIMEOUT" claude -p "$PROMPT" \
  --permission-mode plan 2>>"$AUDIT_LOG_FILE")"
RC=$?
if [ $RC -ne 0 ]; then
  log "branch=$BRANCH claude exited rc=$RC (timeout/error); no notification"
  exit 0
fi

# --- parse: keep only 🔴/🟡 lines + verdict; suppress clean/🟢-only ----------
VERDICT="$(printf '%s\n' "$AUDIT_OUT" | grep -m1 -oE 'VERDICT:[A-Z_]+' || echo 'VERDICT:UNKNOWN')"
FINDINGS="$(printf '%s\n' "$AUDIT_OUT" | grep -E '^[[:space:]]*(🔴|🟡)' || true)"
N_FINDINGS="$(printf '%s' "$FINDINGS" | grep -c . || true)"

if [ "${N_FINDINGS:-0}" -eq 0 ]; then
  log "branch=$BRANCH verdict=$VERDICT findings=0 (clean / 🟢-only) — suppressed, no notification"
  exit 0
fi

VERDICT_HUMAN="${VERDICT#VERDICT:}"
SUMMARY="$N_FINDINGS finding(s) on $BRANCH — verdict: $VERDICT_HUMAN"
log "branch=$BRANCH verdict=$VERDICT findings=$N_FINDINGS mode=$AUDIT_ON_PUSH"
log "FINDINGS:\n$FINDINGS"

# --- route to clawgate (or shadow-log) -------------------------------------
if [ "$AUDIT_ON_PUSH" = "shadow" ]; then
  log "SHADOW: would POST to clawgate — $SUMMARY"
  exit 0
fi

# on: actually notify. Build the context array (verdict + each finding line).
command -v jq >/dev/null 2>&1 || { log "jq missing; cannot build payload; skip send"; exit 0; }
command -v curl >/dev/null 2>&1 || { log "curl missing; cannot send; skip"; exit 0; }
[ -n "$HOOK_TOKEN" ] || { log "no CLAWGATE_HOOK_TOKEN; cannot send; skip"; exit 0; }

PROJECT="$(basename "$REPO_ROOT" 2>/dev/null || echo repo)"
CONTEXT_JSON="$(printf '%s\n' "Verdict: $VERDICT_HUMAN" "$FINDINGS" \
  | jq -R . | jq -sc .)"

PAYLOAD="$(jq -nc \
  --arg tool "PR audit" \
  --arg command "push $BRANCH" \
  --arg input "$SUMMARY" \
  --arg host "$HOST" \
  --arg project "$PROJECT" \
  --arg cwd "$REPO_ROOT" \
  --argjson context "$CONTEXT_JSON" \
  '{type:"permission",tool:$tool,command:$command,input:$input,host:$host,project:$project,cwd:$cwd,context:$context}')"

if curl -sf --max-time 15 -X POST "$API_URL/api/send" \
     -H 'Content-Type: application/json' \
     -H "Authorization: Bearer $HOOK_TOKEN" \
     -d "$PAYLOAD" >>"$AUDIT_LOG_FILE" 2>&1; then
  log "SENT to clawgate — $SUMMARY"
else
  log "clawgate POST failed (unreachable?) — $SUMMARY (no retry)"
fi
exit 0
