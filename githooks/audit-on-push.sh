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
#   - the repository being graded is NOT a pytest temp fixture tree
#   - branch is a FEATURE branch (NEVER trunk/main/master)
#   - the branch is not a synthetic local test ref pushed at a throwaway remote
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

# --- gate 0.5: never grade a pytest temp fixture tree ----------------------
# 🔴 MEASURED over the 14 days to 2026-08-25: FIVE hook-fired audit runs, THREE
# of them launched from inside cwds shaped like
#     /tmp/.../pytest-of-zach/pytest-0/test_the_far_side_fixture_woul0/…
# — a devrc test fixture built a throwaway repo, pushed it at a throwaway bare
# remote, and this worker graded the FIXTURE's diff with a real headless
# `claude` call. Four non-productive runs cost 167,977 output tokens (summed
# from the telemetry `output_tokens` of those runs).
#
# 🔴 THE CHECK IS ON THE REPOSITORY ROOT, NOT ON cwd, and the difference is the
# whole point: this worker is handed REPO_ROOT as $3 by githooks/pre-push and
# `cd`s to it below, so by the time the audit runs cwd has been overwritten.
# cwd tells you where the push was typed; REPO_ROOT tells you what is about to
# be graded, and only the second decides whether the audit is worth a token.
#
# TWO HALVES, BOTH KEPT ON PURPOSE:
#   * PATH — the tmp shapes pytest actually produces by default.
#   * ENV  — `--basetemp=<dir>` puts the tree at ANY path the caller likes, so
#     no path pattern can be exhaustive. pytest exports PYTEST_CURRENT_TEST
#     (during a test) and PYTEST_VERSION (>=8.0, for the whole session) into
#     every child process, so the env half holds for a basetemp under any name.
# Neither half subsumes the other: the env is absent when a stale fixture path
# is pushed from a plain shell after the run, and the path is unrecognisable
# when basetemp is custom.
is_pytest_fixture_tree() {
  case "$1" in
    */pytest-of-*|*/pytest-of-*/*) return 0 ;;
    /tmp/pytest-*|/tmp/pytest-*/*) return 0 ;;
    */pytest-basetemp|*/pytest-basetemp/*) return 0 ;;
    */pytest_basetemp|*/pytest_basetemp/*) return 0 ;;
  esac
  # $TMPDIR is where pytest actually roots `pytest-of-*` when it is set; the
  # literal `/tmp` arms above do not cover a relocated TMPDIR.
  case "${TMPDIR:-}" in
    ""|/|/tmp|/tmp/) : ;;
    *) case "$1" in "${TMPDIR%/}"/pytest-*) return 0 ;; esac ;;
  esac
  return 1
}

# 🔴 THE SENTINELS ARE LOAD-BEARING, not decoration.
# `scripts/tests/test_audit_on_push_fixture_guard.py` builds its RED-AT-BASE
# baseline by deleting exactly the lines between them, so the mutation it scores
# is THIS guard and nothing else. It deliberately does NOT wrap the helper
# function above: a mutant that removes a guard together with the machinery it
# calls dies for the wrong reason. Keep the markers on their own lines, keep the
# helper OUTSIDE them, and do not put anything between them that the rest of the
# script depends on.
# >>> GUARD:fixture-tree
if is_pytest_fixture_tree "$REPO_ROOT"; then
  log "repo_root=$REPO_ROOT is a pytest temp fixture tree; skip"
  exit 0
fi
if [ -n "${PYTEST_CURRENT_TEST:-}${PYTEST_VERSION:-}" ]; then
  log "repo_root=$REPO_ROOT pushed from inside a running pytest (PYTEST_CURRENT_TEST/PYTEST_VERSION set); skip"
  exit 0
fi
# <<< GUARD:fixture-tree

[ -n "$REPO_ROOT" ] && cd "$REPO_ROOT" 2>/dev/null || exit 0

# --- gate 1: feature-branch filter (NEVER trunk/main/master) ---------------
# Determine the branch being pushed from the ref-update lines on stdin; fall
# back to the current symbolic HEAD.
# The ref-update line is `<local ref> <local sha> <remote ref> <remote sha>`;
# the 4th field is git's OWN answer to "does the remote already have this ref"
# (all-zero = it does not), which gate 1.5 below reads. It used to be discarded.
BRANCH=""
REMOTE_SHA=""
while read -r local_ref _ _ remote_sha; do
  case "$local_ref" in
    refs/heads/*) BRANCH="${local_ref#refs/heads/}"; REMOTE_SHA="${remote_sha:-}"; break ;;
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

# --- gate 1.5: synthetic local test ref / throwaway fixture remote ----------
# Second, INDEPENDENT net over the same 2026-08-25 finding. One of the four
# non-productive runs graded `test/prepush-pc-r3`, and
# `git ls-remote --heads origin 'test/prepush-pc*'` is EMPTY — that branch has
# never existed upstream. Gate 1's allowlist waved it through on its trailing
# `*/*` arm, which matches any slash-bearing name at all.
#
# 🔴 WHAT THIS DELIBERATELY DOES NOT DO: skip on "no upstream" ALONE. The FIRST
# push of a real `fix/…` or `feat/…` branch also has no upstream and also names
# a ref the remote lacks — and that push is exactly the one the audit exists
# for. A guard keyed on absence alone would read as "quieter" while silently
# deleting the feature. So the trigger is absence AND a positive tell:
#
#   (a) STRUCTURAL — the push DESTINATION is a filesystem path inside a temp
#       tree, i.e. a fixture's throwaway bare remote. Nothing in this fleet
#       legitimately pushes there, and it needs no network call to decide.
#   (b) NAME-SHAPED BACKSTOP — the branch lives in a `test/`-style namespace
#       AND git said on stdin that the remote does not have the ref AND no
#       upstream is configured. This half is walkable by renaming the branch;
#       it is defence in depth behind (a) and gate 0.5, not the primary.
is_throwaway_remote() {
  local _u
  _u="${1#file://}"
  [ -n "$_u" ] || return 1
  case "$_u" in
    /tmp/*|/var/tmp/*|/dev/shm/*) return 0 ;;
  esac
  is_pytest_fixture_tree "$_u" && return 0
  case "${TMPDIR:-}" in
    ""|/|/tmp|/tmp/) : ;;
    *) case "$_u" in "${TMPDIR%/}"/*) return 0 ;; esac ;;
  esac
  return 1
}

# All-zero remote sha (40 hex zeros for sha1, 64 for sha256) = the remote does
# not have this ref. `tr -d '0'` covers both widths without pinning a length.
remote_lacks_ref() {
  [ -z "$REMOTE_SHA" ] && return 0
  [ -z "$(printf '%s' "$REMOTE_SHA" | tr -d '0')" ] && return 0
  return 1
}

# See the sentinel note at gate 0.5 — same contract, same test file.
# >>> GUARD:synthetic-ref
if is_throwaway_remote "$URL"; then
  log "branch=$BRANCH remote=$URL is a throwaway/temp-tree remote; skip"
  exit 0
fi

case "$BRANCH" in
  test/*|tests/*|testing/*)
    if [ -z "$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)" ] \
       && remote_lacks_ref; then
      log "branch=$BRANCH is a synthetic local test ref (no upstream, absent on remote); skip"
      exit 0
    fi ;;
esac
# <<< GUARD:synthetic-ref

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

# NEXT_STEP_NUDGE_OFF: this is a headless run whose "operator" is the parser below, which
# keeps the verdict line and drops the rest. The Stop next-step nudge would spend an extra
# turn against AUDIT_TIMEOUT writing a line nothing here reads.
AUDIT_OUT="$(cd "$REPO_ROOT" && NEXT_STEP_NUDGE_OFF=1 timeout "$AUDIT_TIMEOUT" claude -p "$PROMPT" \
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
