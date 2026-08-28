#!/usr/bin/env bash
#
# tmux-snapshot-push.sh — feed clawgate's cross-host tmux read model.
#
# WHAT THIS IS
# ------------
# clawgate runs in a pod on the workbench cluster. tmux sockets are unix sockets
# on the workbench and laptop HOSTS. The deployment has no hostPath, no
# hostNetwork, no hostPID and no nodeName, so the pod structurally cannot see
# them (measured against the live spec 2026-08-28). The read model therefore has
# to be DELIVERED from a host, outbound — which also means no inbound access to
# either machine, no privileged pod, and no SSH credential living in a pod on an
# unauthenticated LAN surface.
#
# 🔴 THIS SCRIPT IS DELIBERATELY A DUMB PIPE. It posts `session-manager --json`
# output VERBATIM and the SERVER normalises it (`internal/tmux`). That is not
# laziness: the producer spells a tmux session `session`, which is already
# clawgate's word for a Claude Code session, so the payload arrives carrying a
# vocabulary collision. The rename to `tmuxSessionName` lives server-side where
# it is tested, rather than in a shell script on two machines. Do NOT start
# reshaping the payload here — every field you touch is a second place the
# schema can drift.
#
# ONE AGENT, NOT ONE PER HOST. The design doc specified a unit on each host;
# `session-manager --json` already collects BOTH (it SSHes to the laptop), so a
# single workbench-side timer covers the fleet. The server's schema is per-host,
# so a second reporter can be added later with no server change.
#
# Exit codes (distinct on purpose — "something went wrong" would not tell an
# operator whether to look at the collector, the network, or the server):
#   0  pushed
#   2  no usable credentials
#   3  the collector failed, or produced something that is not a document
#   4  the push could not reach the server (transport)
#   5  the server rejected the push (HTTP >= 400)

set -euo pipefail

API_DEFAULT="http://192.168.50.250:30302"
CONF_FILE="${CLAWGATE_CONF_FILE:-$HOME/.claude/clawgate.env}"
# Bound the collector. It SSHes to the laptop, so a wedged remote must not pin
# this unit open until the systemd TimeoutStartSec kills the whole cgroup.
COLLECT_TIMEOUT="${TMUX_PUSH_COLLECT_TIMEOUT:-90}"
CURL_TIMEOUT="${TMUX_PUSH_CURL_TIMEOUT:-30}"

log() { printf 'tmux-snapshot-push: %s\n' "$*"; }

# ── credentials ──────────────────────────────────────────────────────────────
# 🔴 THE ENVIRONMENT WINS OVER THE FILE, and that direction is load-bearing.
# `clawgate-stop-hook.sh` sources this same file with `set -a`, which makes the
# FILE beat the environment — the opposite of clawgatectl's documented
# precedence. The measured consequence: exporting CLAWGATE_API_URL to aim a
# probe somewhere harmless did nothing and the probe silently POSTed to
# PRODUCTION, leaving a stray entry in the real queue. Reading the file only for
# keys the environment has not already set means `CLAWGATE_API_URL=... this
# script` goes where you told it. `CLAWGATE_CONF_FILE` redirects the file itself.
read_conf_key() {
  local key="$1"
  [ -r "$CONF_FILE" ] || return 0
  # Last assignment wins, matching shell sourcing. Strips surrounding quotes.
  sed -n "s/^${key}=//p" "$CONF_FILE" | tail -1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

API_URL="${CLAWGATE_API_URL:-$(read_conf_key CLAWGATE_API_URL)}"
API_URL="${API_URL:-$API_DEFAULT}"
API_URL="${API_URL%/}"
TOKEN="${CLAWGATE_HOOK_TOKEN:-$(read_conf_key CLAWGATE_HOOK_TOKEN)}"

if [ -z "$TOKEN" ]; then
  # Not a warning to be ignored: the write route is behind requireHookToken, so
  # with no token this can only ever 401. Fail loudly rather than push nothing
  # on a timer forever.
  log "no CLAWGATE_HOOK_TOKEN in the environment or $CONF_FILE — refusing to push"
  exit 2
fi

# ── scratch ──────────────────────────────────────────────────────────────────
# 🔴 Per-run mktemp, never a fixed name. Two runs sharing a path is the silent
# collision that makes one report a result computed against the other's data.
umask 077
WORK="$(mktemp -d "${TMPDIR:-/tmp}/tmux-snapshot-push.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT INT TERM

PAYLOAD="$WORK/snapshot.json"
COLLECT_ERR="$WORK/collect.err"
CURL_CFG="$WORK/curl.cfg"
BODY="$WORK/response.json"

# ── collect ──────────────────────────────────────────────────────────────────
SM="${TMUX_PUSH_COLLECTOR:-$HOME/workspace/devrc/scripts/session-manager}"
if [ ! -x "$SM" ]; then
  log "collector not executable: $SM"
  exit 3
fi

# 🔴 stdout and stderr to SEPARATE files. A merged capture would splice the
# collector's diagnostics into the JSON document and the failure would present
# as a malformed payload rather than as the collector complaining.
if ! timeout "$COLLECT_TIMEOUT" "$SM" --json >"$PAYLOAD" 2>"$COLLECT_ERR"; then
  rc=$?
  log "collector failed (rc=$rc): $(tr '\n' ' ' <"$COLLECT_ERR" | cut -c1-400)"
  exit 3
fi

# A shallow "did the collector actually produce a document" gate — NOT a copy of
# the server's schema validation, which stays the single authority for what a
# valid payload is. This only distinguishes "the collector emitted an error page
# / nothing" from "the server rejected a real document", because those two need
# completely different fixes and the HTTP status alone cannot tell them apart.
if ! python3 -c '
import json,sys
d=json.load(open(sys.argv[1]))
if not isinstance(d.get("hosts"), dict) or not d["hosts"]:
    raise SystemExit("no non-empty `hosts` object")
' "$PAYLOAD" 2>"$WORK/parse.err"; then
  log "collector output is not a session-manager document: $(tr '\n' ' ' <"$WORK/parse.err" | cut -c1-300)"
  exit 3
fi

BYTES=$(wc -c <"$PAYLOAD")

# ── push ─────────────────────────────────────────────────────────────────────
# 🔴 The token goes in a 0600 config file, never in argv. Everything on this box
# can read /proc/<pid>/cmdline.
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" >"$CURL_CFG"

set +e
HTTP=$(curl -sS --config "$CURL_CFG" \
  --max-time "$CURL_TIMEOUT" \
  -X POST \
  -H 'Content-Type: application/json' \
  --data-binary "@$PAYLOAD" \
  -o "$BODY" -w '%{http_code}' \
  "$API_URL/api/tmux/snapshot" 2>"$WORK/curl.err")
CURL_RC=$?
set -e

if [ "$CURL_RC" -ne 0 ]; then
  log "push to $API_URL failed (curl rc=$CURL_RC): $(tr '\n' ' ' <"$WORK/curl.err" | cut -c1-300)"
  exit 4
fi

if [ "$HTTP" -ge 400 ]; then
  # 404 is the one worth naming: the server predates the snapshot routes, which
  # is a deploy-order problem (server first), not a payload problem.
  if [ "$HTTP" = "404" ]; then
    log "server at $API_URL has no /api/tmux/snapshot route (HTTP 404) — it predates the read model; deploy the server first"
  else
    log "server rejected the push (HTTP $HTTP): $(tr '\n' ' ' <"$BODY" | cut -c1-300)"
  fi
  exit 5
fi

log "pushed ${BYTES}B to $API_URL (HTTP $HTTP): $(tr -d '\n' <"$BODY" | cut -c1-200)"
