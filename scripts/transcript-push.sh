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

# Defined FIRST: the host-label resolution below is the earliest thing that can
# exit, and it reports through log(). A definition further down would make that
# branch die with "log: command not found" instead of its own message.
log() { printf 'transcript-push: %s\n' "$*"; }

API_DEFAULT="http://192.168.50.250:30302"
CONF_FILE="${CLAWGATE_CONF_FILE:-$HOME/.claude/clawgate.env}"
CURL_TIMEOUT="${TRANSCRIPT_PUSH_CURL_TIMEOUT:-30}"
BUILD_TIMEOUT="${TRANSCRIPT_PUSH_BUILD_TIMEOUT:-120}"

# Where the transcripts live. Overridable so the tests never touch the real one.
PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}"

# This host's name as the read model will record it.
#
# 🔴 THE RULE IS `scripts/lib/host_label.py`, NOT A SHELL EXPANSION, AND THE
# DIFFERENCE WAS A LIVE DEFECT. This line used to be
#
#     HOST_NAME="${TRANSCRIPT_PUSH_HOST:-${ACTIVITY_HOST:-$(uname -n)}}"
#
# whose comment claimed it reused the fleet's per-host handle "rather than
# minting a second". It did not: ACTIVITY_HOST lives in a FILE the unit does not
# source and this script never read, so it fell through to `uname -n` — which is
# **"nixos" on BOTH machines**. Measured on the deployed server: every stored
# transcript row said `host: nixos`, so the column was useless, while the reply
# agent (which does read the file) called the same machine "workbench".
#
# That cost nothing while `host` was display-only. The delta stream makes it a
# CORRECTNESS predicate — an append whose host differs from the stored row's is
# refused, because the stored offset is a position in the other machine's file —
# so the disagreement would have become a permanent reseed loop: this push
# stamping `nixos` back onto every row every 5 minutes, the stream reseeding
# every session every 5 seconds because "the host changed".
#
# `TRANSCRIPT_PUSH_HOST` still wins, for the tests and for a deliberate override.
HOST_LABEL_PY="${TRANSCRIPT_PUSH_HOST_LABEL:-$(dirname "$(readlink -f "$0")")/lib/host_label.py}"
if [ -n "${TRANSCRIPT_PUSH_HOST:-}" ]; then
  HOST_NAME="$TRANSCRIPT_PUSH_HOST"
else
  # 🔴 A FAILURE HERE IS FATAL, NOT A FALLBACK TO `uname -n`. Falling back is what
  # produced the defect above, and it would produce it again silently.
  #
  # 🔴 AND THE DIAGNOSIS IS FORWARDED, NOT DISCARDED. This used to be
  # `2>/dev/null` plus a generic log line, so the journal recorded "could not
  # resolve this host's label" and nothing else. The module's stderr is the only
  # place that names WHICH label was stated, which one the address derived, and
  # WHICH FILE to edit — and the realistic cause of this branch is a stated label
  # that contradicts the machine, where the generic line tells an operator
  # nothing actionable while the unit fails every 5 minutes. Every other consumer
  # of this module already forwards the exception text.
  #
  # stderr goes to a FILE, never `2>&1`: this is a command substitution assigned
  # to `HOST_NAME`, so anything merged onto stdout BECOMES the host name — the
  # exact hazard `host_label.py`'s `__main__` keeps stdout empty for.
  HOST_LABEL_ERR="$(mktemp "${TMPDIR:-/tmp}/transcript-push-hostlabel.XXXXXX")"
  if ! HOST_NAME="$(python3 "$HOST_LABEL_PY" 2>"$HOST_LABEL_ERR")" || [ -z "$HOST_NAME" ]; then
    log "could not resolve this host's label via $HOST_LABEL_PY — refusing to push under a guessed name: $(tr '\n' ' ' < "$HOST_LABEL_ERR")"
    rm -f "$HOST_LABEL_ERR"
    exit 3
  fi
  rm -f "$HOST_LABEL_ERR"
fi

# 🔴 THESE BOUNDS MUST STAY UNDER THE **DEPLOYED** SERVER'S OWN, AND "DEPLOYED"
# IS THE WORD THAT COST US THE FEATURE. A rejection changes nothing server-side,
# so a client tuned above a limit is a feeder that fails EVERY SINGLE TICK while
# looking correctly configured.
#
# 🔴 THE PREVIOUS VALUES WERE TUNED TO A SERVER THAT IS NOT RUNNING, AND
# TRANSCRIPT PUSH WAS 100% DEAD ON BOTH HOSTS FOR ~19 HOURS. The comment here
# asserted "the server enforces MaxSessionsPerPush=128 and MaxPushTailBytes=4
# MiB" and raised the client to 48/3 MiB to match. Measured 2026-09-10, from the
# live server's own refusals:
#
#   workbench: HTTP 413 {"error":"transcript: too many sessions in one push:
#              16 > 8"}          <- the APP's cap is 8, not 128
#   laptop:    HTTP 413 <html>…413 Request Entity Too Large…nginx/1.29.4</html>
#                                <- rejected by the INGRESS, before the app
#
# 228 consecutive failures on the workbench alone, beginning 2026-09-09
# 19:38:52Z — four minutes after #1408 merged (19:34:12Z) and shipped.
#
# 🔴 TWO CEILINGS, NOT ONE, THEY FAIL DIFFERENTLY, AND THE TWO HOSTS DO NOT TAKE
# THE SAME ROUTE — which is why the two hosts failed for two different reasons
# and either one alone would have given a misleading diagnosis:
#
#   workbench -> http://192.168.50.250:30302   (NodePort; no proxy in front)
#                so it reached the app and hit the APP's session cap.
#   laptop    -> http://10.42.0.10:8109        (nebula; an nginx IS in this path)
#                so an oversized body was refused by the PROXY and the app never
#                saw it — the app's limits are irrelevant once that happens.
#
# ✅ THE PROXY'S BYTE LIMIT IS NOW TRACED AND MEASURED, AND THE SENTENCE ABOVE
# THIS ONE USED TO SAY IT WAS NEITHER. Traced 2026-09-16 in
# homelab-talos `clusters/homelab/apps/nebula/gateway/nginx.conf`: the
# `listen 10.42.0.10:8109` block — the laptop's clawgate route — declared NO
# `client_max_body_size` at any level, while fifteen neighbouring blocks each
# declare their own. So nginx's compile-time default of `1m` applied, inherited
# and invisible. The live ConfigMap was byte-identical to git.
#
# Then MEASURED against that exact listener from the laptop, with a deliberately
# INVALID bearer token so a body that reaches the app is refused before anything
# is stored:
#
#     1,048,575 B  -> HTTP 401      (reached clawgate)
#     1,048,576 B  -> HTTP 401      (reached clawgate)
#     1,048,577 B  -> HTTP 413 nginx (refused by the proxy)
#
# Exactly 1 MiB, inclusive. The old "3 MiB was refused, 817,481 B went through"
# pair was consistent with that and could not locate it.
#
# 🔴 THE CEILING IS NO LONGER A HOST-SIDE GUESS AT A SERVER CONSTANT. The tail
# size is now NEGOTIATED: `GET /api/transcripts/digest` — the pre-flight this
# script already calls every tick — carries the server's own
# `limits.maxTailBytes`, and the effective value is min(server, ceiling below).
# A server that does not advertise one gets TAIL_FALLBACK, which is the value
# this script used before, so pointing a new feeder at an old server changes
# nothing. See scripts/lib/transcript_limits.py.
#
# 🔴 STILL VERIFY THE RUNNING SERVER BEFORE RAISING THE CEILING — do not raise it
# because a clawgate PR or release note says the cap moved. `merged` is not
# `deployed`. The negotiation makes that mistake much harder, because the number
# now comes FROM the running server rather than from a belief about it.
TAIL_CEILING="${TRANSCRIPT_PUSH_TAIL_CEILING:-1048576}" # 1 MiB; the most this host will ever read per session
TAIL_FALLBACK="${TRANSCRIPT_PUSH_TAIL_FALLBACK:-196608}" # 192 KiB — what a server advertising nothing gets
# An explicit TRANSCRIPT_PUSH_TAIL_BYTES still wins over the negotiation, for a
# deliberate override and for the tests.
TAIL_BYTES="${TRANSCRIPT_PUSH_TAIL_BYTES:-}"
MAX_PER_PUSH="${TRANSCRIPT_PUSH_MAX_SESSIONS:-8}"      # deployed app cap 8, MEASURED from its refusal
# The AGGREGATE raw-tail budget. Derived from TAIL_BYTES once that is known, and
# never below the 900,000 this script has been using: at the fallback tail the
# derivation is 589,824, so max() keeps today's behaviour EXACTLY unchanged
# against a server that advertises nothing.
MAX_PUSH_BYTES="${TRANSCRIPT_PUSH_MAX_BYTES:-}"
# How far back to consider a transcript at all. 24h keeps a session readable the
# morning after; older ones are past the server's retention anyway.
MAX_AGE_HOURS="${TRANSCRIPT_PUSH_MAX_AGE_HOURS:-24}"
# How many recent files to even HASH per run. Hashing is the only per-file cost
# in the steady state and it is bounded by TAIL_BYTES, so this is generous.
MAX_CANDIDATES="${TRANSCRIPT_PUSH_MAX_CANDIDATES:-200}"


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

# ── 1b. adopt the server's advertised ingest bounds ──────────────────────────
# 🔴 A FAILURE HERE FALLS BACK, IT DOES NOT ABORT. The pre-flight has already
# succeeded, so the feeder is working; refusing to push because one optional
# field could not be read would turn a cosmetic problem into silence — which is
# this pipe's only failure mode and the one the whole suite is built around.
LIMITS_PY="${TRANSCRIPT_PUSH_LIMITS:-$(dirname "$(readlink -f "$0")")/lib/transcript_limits.py}"
if [ -z "$TAIL_BYTES" ]; then
  # stderr to a FILE, never 2>&1: this is a command substitution assigned to a
  # variable that becomes a byte count, so anything merged onto stdout BECOMES
  # that count. Same hazard, same fix, as the host-label resolution above.
  LIMITS_ERR="$(mktemp "${TMPDIR:-/tmp}/transcript-push-limits.XXXXXX")"
  if ! TAIL_BYTES="$(python3 "$LIMITS_PY" "$DIGEST" "$TAIL_CEILING" "$TAIL_FALLBACK" 2>"$LIMITS_ERR")" \
     || [ -z "$TAIL_BYTES" ]; then
    log "could not read the server's advertised tail bound (using $TAIL_FALLBACK): $(tr '\n' ' ' <"$LIMITS_ERR" | cut -c1-200)"
    TAIL_BYTES="$TAIL_FALLBACK"
  fi
  rm -f "$LIMITS_ERR"
fi
if [ -z "$MAX_PUSH_BYTES" ]; then
  MAX_PUSH_BYTES=$(( TAIL_BYTES * 3 ))
  [ "$MAX_PUSH_BYTES" -lt 900000 ] && MAX_PUSH_BYTES=900000
fi

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

# 🔴 THE PUSH IS RETRIED ONCE ON A 413, AT A SMALLER TAIL, AND THE REFUSAL IS
# LOGGED WITH THE NUMBERS THAT CAUSED IT.
#
# Two ceilings can answer 413 and they fail differently — the APP (naming its own
# cap in a JSON body) and the PROXY (an nginx HTML page the app never sees). This
# script cannot tell which without reading the body, and it does not need to: the
# corrective action is the same, and it is the action an operator took by hand
# twice already. On 2026-09-09 a raised session count produced 228 consecutive
# 413s and transcript push was 100% dead on both hosts for ~19 hours, because
# nothing here retried and nothing here backed off.
#
# 🔴 ONE RETRY, NEVER A LOOP. A backoff that keeps halving would turn a
# permanent misconfiguration into a slow, quiet, self-repairing degradation —
# exactly the silence this pipe's failure mode already is. One retry recovers a
# deploy-ordering mistake; a second failure is a real problem and exits non-zero
# so the unit reports it.
ATTEMPT=1
while :; do
  set +e
  timeout "$BUILD_TIMEOUT" python3 "$BUILDER" \
    --projects-dir "$PROJECTS_DIR" \
    --digest "$DIGEST" \
    --host "$HOST_NAME" \
    --tail-bytes "$TAIL_BYTES" \
    --max-sessions "$MAX_PER_PUSH" \
    --max-push-bytes "$MAX_PUSH_BYTES" \
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
    413)
      # 🔴 THE NUMBERS THAT CAUSED IT GO IN THE LOG, EVERY TIME. This refusal is
      # the only place the real ceiling is ever observed, and the last time it
      # fired the journal recorded the HTTP code and not the sizes — so the
      # diagnosis needed a reproduction rather than a read.
      if [ "$ATTEMPT" -ge 2 ] || [ "$TAIL_BYTES" -le "$TAIL_FALLBACK" ]; then
        log "push REFUSED 413 at tail=${TAIL_BYTES}B aggregate=${MAX_PUSH_BYTES}B body=${BYTES}B after ${ATTEMPT} attempt(s): $(tr '\n' ' ' <"$BODY" | cut -c1-300)"
        exit 5
      fi
      NEW_TAIL=$(( TAIL_BYTES / 2 ))
      [ "$NEW_TAIL" -lt "$TAIL_FALLBACK" ] && NEW_TAIL="$TAIL_FALLBACK"
      log "push REFUSED 413 at tail=${TAIL_BYTES}B aggregate=${MAX_PUSH_BYTES}B body=${BYTES}B — retrying once at tail=${NEW_TAIL}B: $(tr '\n' ' ' <"$BODY" | cut -c1-200)"
      TAIL_BYTES="$NEW_TAIL"
      MAX_PUSH_BYTES=$(( TAIL_BYTES * 3 ))
      [ "$MAX_PUSH_BYTES" -lt 900000 ] && MAX_PUSH_BYTES=900000
      ATTEMPT=2
      continue
      ;;
    *)
      log "push not accepted (HTTP '${HTTP}'): $(tr '\n' ' ' <"$BODY" | cut -c1-300)"
      exit 5
      ;;
  esac

  log "pushed ${BYTES}B to $API_URL (HTTP $HTTP, tail=${TAIL_BYTES}B, attempt ${ATTEMPT}): $(tr -d '\n' <"$BODY" | cut -c1-200)"
  break
done
