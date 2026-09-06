#!/usr/bin/env bash
#
# transcript-push.sh — feed clawgate's Claude Code TRANSCRIPT read model.
#
# WHAT THIS IS
# ------------
# clawgate runs in a pod on the workbench cluster. Claude Code transcripts are
# ordinary files on each HOST's filesystem (`~/.claude/projects/<slug>/<uuid>.jsonl`).
# The deployment has no hostPath, no hostNetwork and no nodeName, so the pod
# structurally cannot read them. Delivery is therefore OUTBOUND from the host,
# exactly like `tmux-snapshot-push.sh` — which also means no inbound access to
# either machine and no credential living in a pod on an unauthenticated LAN.
#
# 🔴 ONE UNIT PER HOST, WHICH IS THE OPPOSITE OF ITS SIBLING, AND THE REASON IS
# NOT STYLE. `tmux-snapshot-push.sh` runs on the workbench ONLY because its
# collector already reaches BOTH machines over ssh, so a second reporter would
# have the two fighting over every row. Transcripts have no such collector: the
# files are local, unreadable from the other host, and there is no cross-host
# enumeration to share. The read model is keyed on a Claude Code SESSION ID,
# which is globally unique, so two hosts pushing disjoint session sets cannot
# collide — a session only ever lives on one machine at a time.
#
# 🔴 A BOUNDED TAIL, NEVER A TRANSCRIPT. Measured on this fleet 2026-09-04: 315
# transcript files were modified within 24h, 456 MB in total, the largest single
# file 23 MB. Shipping whole files is not a size to be tuned down, it is the
# wrong shape. What is sent is the last TAIL_BYTES of each file with the leading
# partial record dropped, and the payload SAYS whether it was cut, because a
# consumer that cannot tell "the whole session" from "the end of it" will state
# the first while showing the second.
#
# 🔴 THE DEDUPE PRE-FLIGHT IS WHAT MAKES THE STEADY STATE CHEAP, AND IT IS
# SERVER-AUTHORITATIVE ON PURPOSE. Before pushing anything this asks
# `GET /api/transcripts/digest` what the server already holds and skips every
# session whose tail hashes to the same value. The alternative considered and
# REJECTED was a host-side state file of "what I sent last time": it is wrong
# whenever the two sides disagree, and they disagree in both directions — a
# database restore or a fresh deploy leaves the server empty while the host
# believes everything is delivered (silent permanent data loss, invisible), and
# a wiped host cache re-pushes everything (merely expensive). Asking the server
# is correct in both directions and needs no state on this machine at all.
#
# Exit codes (distinct on purpose — "something went wrong" would not tell an
# operator whether to look at this host, the network, or the server):
#   0  pushed, or nothing had changed since the last run
#   2  no usable credentials
#   3  the transcript directory is missing/unreadable, or the builder failed
#   4  a request could not reach the server (transport)
#   5  the server did not accept a request (any non-2xx)
#
# 🔴 A non-zero exit is the ONLY alarm this unit has, by design: it deliberately
# wires no OnFailure toast (see nix/home.nix), because it runs on a timer and a
# sustained outage — the laptop asleep, clawgate mid-redeploy — would otherwise
# fire a do-not-disturb-defeating toast on every tick and burn down the one alert
# channel that has to keep its meaning. Keep these codes distinct and non-zero.
#
# 🔴 READ-ONLY, AND THAT IS A SECURITY PROPERTY RATHER THAN A DESCRIPTION. This
# script opens transcript files for reading and posts bytes. It executes nothing
# on any host, writes nothing outside its own mktemp scratch, and takes no input
# from the server beyond a list of hashes it compares for equality. The clawgate
# side is symmetric: the ingest stores a document and the chat view renders one.

set -euo pipefail

API_DEFAULT="http://192.168.50.250:30302"
CONF_FILE="${CLAWGATE_CONF_FILE:-$HOME/.claude/clawgate.env}"
CURL_TIMEOUT="${TRANSCRIPT_PUSH_CURL_TIMEOUT:-30}"
BUILD_TIMEOUT="${TRANSCRIPT_PUSH_BUILD_TIMEOUT:-120}"

# Where the transcripts live. Overridable so the tests never touch the real one.
PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}"

# This host's name as the read model will record it. `ACTIVITY_HOST` is the
# fleet's existing per-host handle (the activity collector sets it on both
# machines), so reusing it keeps one answer to "which box is this" rather than
# minting a second.
HOST_NAME="${TRANSCRIPT_PUSH_HOST:-${ACTIVITY_HOST:-$(uname -n)}}"

# 🔴 THESE THREE BOUNDS MUST STAY UNDER THE SERVER'S OWN, NOT AT THEM. The
# server enforces MaxTailBytes=262144 and MaxSessionsPerPush=8 and REJECTS a push
# that exceeds either — a rejection changes nothing server-side, so a client tuned
# exactly to the limit turns any rounding disagreement into a feeder that fails
# every single tick while looking correctly configured.
TAIL_BYTES="${TRANSCRIPT_PUSH_TAIL_BYTES:-196608}"     # 192 KiB; server cap 256 KiB
MAX_PER_PUSH="${TRANSCRIPT_PUSH_MAX_SESSIONS:-6}"      # server cap 8
# How far back to consider a transcript at all. 24h keeps a session readable the
# morning after; older ones are past the server's retention anyway.
MAX_AGE_HOURS="${TRANSCRIPT_PUSH_MAX_AGE_HOURS:-24}"
# How many recent files to even HASH per run. Hashing is the only per-file cost
# in the steady state and it is bounded by TAIL_BYTES, so this is generous.
MAX_CANDIDATES="${TRANSCRIPT_PUSH_MAX_CANDIDATES:-200}"

log() { printf 'transcript-push: %s\n' "$*"; }

# ── credentials ──────────────────────────────────────────────────────────────
# 🔴 THE ENVIRONMENT WINS OVER THE FILE, and that direction is load-bearing —
# the same rule, for the same measured reason, as the sibling feeder.
# `clawgate-stop-hook.sh` sources this file with `set -a`, which makes the FILE
# beat the environment; the consequence measured there was a probe aimed at a
# harmless address silently POSTing to PRODUCTION. Reading the file only for keys
# the environment has not already set means `CLAWGATE_API_URL=... this script`
# goes where you told it. `CLAWGATE_CONF_FILE` redirects the file itself.
read_conf_key() {
  local key="$1"
  [ -r "$CONF_FILE" ] || return 0
  # Last assignment wins, as sourcing would, and the anchor accepts the two
  # spellings a sourceable file actually carries — a leading `export ` and
  # leading whitespace.
  sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}${key}=//p" "$CONF_FILE" \
    | tail -1 \
    | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

API_URL="${CLAWGATE_API_URL:-$(read_conf_key CLAWGATE_API_URL)}"
API_URL="${API_URL:-$API_DEFAULT}"
API_URL="${API_URL%/}"
TOKEN="${CLAWGATE_HOOK_TOKEN:-$(read_conf_key CLAWGATE_HOOK_TOKEN)}"

if [ -z "$TOKEN" ]; then
  # Not a warning to be ignored: both routes are behind requireHookToken, so with
  # no token this can only ever 401. Fail loudly rather than push nothing on a
  # timer for ever.
  log "no CLAWGATE_HOOK_TOKEN in the environment or $CONF_FILE — refusing to push"
  exit 2
fi

if [ ! -d "$PROJECTS_DIR" ]; then
  # A real state on a host where Claude Code has never run. Distinct from every
  # other failure because the fix is "nothing is wrong", not "look at the pipe".
  log "no transcript directory at $PROJECTS_DIR — nothing to feed"
  exit 3
fi

# ── scratch ──────────────────────────────────────────────────────────────────
# 🔴 Per-run mktemp, never a fixed name. Two runs sharing a path is the silent
# collision that makes one report a result computed against the other's data.
umask 077
WORK="$(mktemp -d "${TMPDIR:-/tmp}/transcript-push.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT INT TERM

DIGEST="$WORK/digest.json"
PAYLOAD="$WORK/payload.json"
CURL_CFG="$WORK/curl.cfg"
BODY="$WORK/response.json"

# 🔴 The token goes in a 0600 config file, never in argv. Everything on this box
# can read /proc/<pid>/cmdline.
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" >"$CURL_CFG"

# ── 1. the dedupe pre-flight ─────────────────────────────────────────────────
set +e
HTTP=$(curl -sS --config "$CURL_CFG" \
  --max-time "$CURL_TIMEOUT" \
  -H 'Accept: application/json' \
  -o "$DIGEST" -w '%{http_code}' \
  "$API_URL/api/transcripts/digest" 2>"$WORK/curl.err")
CURL_RC=$?
set -e
if [ "$CURL_RC" -ne 0 ]; then
  log "digest request to $API_URL failed (curl rc=$CURL_RC): $(tr '\n' ' ' <"$WORK/curl.err" | cut -c1-300)"
  exit 4
fi

# 🔴 SUCCESS IS 2xx, NOT "anything under 400". The sibling script's header
# records why, with three measured failures behind it: a 3xx is followed by
# nothing (there is no `-L`), `000` is curl's no-status-line code, and an EMPTY
# string makes `[ "" -ge 400 ]` write "integer expected" and evaluate FALSE. A
# case on the literal covers all three with no numeric comparison.
#
# 🔴 AND A FAILED PRE-FLIGHT IS FATAL RATHER THAN "ASSUME NOTHING IS STORED".
# Treating it as an empty digest would be the expensive direction — every
# session re-pushed on every tick for as long as the endpoint is unreachable,
# which is precisely when the server is least able to absorb it.
case "$HTTP" in
  2[0-9][0-9]) : ;;
  404)
    log "server at $API_URL has no /api/transcripts/digest route (HTTP 404) — it predates the transcript read model; deploy the server first"
    exit 5
    ;;
  30[0-35-9]|3[1-9][0-9])
    log "server at $API_URL REDIRECTED the digest request (HTTP $HTTP) — this script does not follow redirects; point CLAWGATE_API_URL at the origin, not an ingress"
    exit 5
    ;;
  *)
    log "digest request not accepted (HTTP '${HTTP}'): $(tr '\n' ' ' <"$DIGEST" | cut -c1-300)"
    exit 5
    ;;
esac

# ── 2. build the payload ─────────────────────────────────────────────────────
# 🔴 stdout and stderr to SEPARATE files. A merged capture would splice the
# builder's diagnostics into the JSON document, and the failure would present as
# a malformed payload rather than as the builder complaining.
#
# Exit 10 = nothing to push (every candidate already matches the server's hash).
# That is the ORDINARY steady-state outcome, not a failure, and it is signalled
# by a code rather than by an empty file so it cannot be confused with a builder
# that produced nothing because it crashed.
BUILDER="${TRANSCRIPT_PUSH_BUILDER:-$(dirname "$(readlink -f "$0")")/lib/build_transcript_push.py}"
if [ ! -r "$BUILDER" ]; then
  log "builder not readable: $BUILDER"
  exit 3
fi

set +e
timeout "$BUILD_TIMEOUT" python3 "$BUILDER" \
  --projects-dir "$PROJECTS_DIR" \
  --digest "$DIGEST" \
  --host "$HOST_NAME" \
  --tail-bytes "$TAIL_BYTES" \
  --max-sessions "$MAX_PER_PUSH" \
  --max-age-hours "$MAX_AGE_HOURS" \
  --max-candidates "$MAX_CANDIDATES" \
  >"$PAYLOAD" 2>"$WORK/build.err"
BUILD_RC=$?
set -e
if [ "$BUILD_RC" -eq 10 ]; then
  log "nothing to push — every recent session already matches the server's digest"
  exit 0
fi
if [ "$BUILD_RC" -ne 0 ]; then
  if [ "$BUILD_RC" -eq 124 ]; then
    log "builder TIMED OUT after ${BUILD_TIMEOUT}s (rc=124)"
  else
    log "builder failed (rc=$BUILD_RC): $(tr '\n' ' ' <"$WORK/build.err" | cut -c1-400)"
  fi
  exit 3
fi

BYTES=$(wc -c <"$PAYLOAD")

# ── 3. push ──────────────────────────────────────────────────────────────────
set +e
HTTP=$(curl -sS --config "$CURL_CFG" \
  --max-time "$CURL_TIMEOUT" \
  -X POST \
  -H 'Content-Type: application/json' \
  --data-binary "@$PAYLOAD" \
  -o "$BODY" -w '%{http_code}' \
  "$API_URL/api/transcripts" 2>"$WORK/curl.err")
CURL_RC=$?
set -e

if [ "$CURL_RC" -ne 0 ]; then
  log "push to $API_URL failed (curl rc=$CURL_RC): $(tr '\n' ' ' <"$WORK/curl.err" | cut -c1-300)"
  exit 4
fi

case "$HTTP" in
  2[0-9][0-9]) : ;;
  404)
    log "server at $API_URL has no /api/transcripts route (HTTP 404) — it predates the transcript read model; deploy the server first"
    exit 5
    ;;
  30[0-35-9]|3[1-9][0-9])
    log "server at $API_URL REDIRECTED the push (HTTP $HTTP) and NOTHING was stored — this script does not follow redirects; point CLAWGATE_API_URL at the origin, not an ingress"
    exit 5
    ;;
  *)
    log "push not accepted (HTTP '${HTTP}'): $(tr '\n' ' ' <"$BODY" | cut -c1-300)"
    exit 5
    ;;
esac

log "pushed ${BYTES}B to $API_URL (HTTP $HTTP): $(tr -d '\n' <"$BODY" | cut -c1-200)"
