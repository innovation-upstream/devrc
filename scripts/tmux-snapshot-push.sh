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
#   5  the server did not accept the push (any non-2xx)
#   6  the collection was TORN — a host was reachable but its windows were not
#      measured, so pushing would overwrite a good snapshot with a false zero
#
# 🔴 A non-zero exit is the ONLY alarm this unit has, by design: it deliberately
# wires no OnFailure toast (see nix/home.nix), so a persistent failure surfaces
# via `systemctl --user --failed`, which `/standup` reads. Keep these distinct
# and keep them non-zero.

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
  # Last assignment wins, as sourcing would. 🔴 THE ANCHOR ACCEPTS THE TWO
  # SPELLINGS A SOURCEABLE FILE ACTUALLY CARRIES — a leading `export ` and
  # leading whitespace. An earlier version anchored on a bare `^KEY=` while its
  # comment claimed it matched shell sourcing, and it did not: this file's own
  # header says it is sourced by `clawgate-hook.sh`, so adding `export ` to it is
  # an ordinary edit that would have made THIS reader return empty and exit 2
  # every two minutes while the hook kept working — one file, two readers,
  # silently disagreeing.
  #
  # Remaining known divergence, deliberate: a value is taken literally, so no
  # `$VAR` expansion and no `#` comment stripping. Both would need a real parser,
  # and the file holds a URL and an opaque token.
  sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}${key}=//p" "$CONF_FILE" \
    | tail -1 \
    | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
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
# 🔴 CAPTURE THE STATUS BEFORE ANY OTHER COMMAND RUNS, AND NOT INSIDE `if !`.
# `if ! cmd; then rc=$?` reads the status of the NEGATED pipeline, which is 0
# exactly when the branch is taken — so the old form logged `rc=0` for every
# failure. The timeout case was the damaging one: rc=0 AND empty stderr, i.e. a
# log line carrying no information at all, from the one path most likely to
# happen (a wedged ssh to a sleeping laptop). That defeats this script's own
# distinct-exit-code doctrine at the header.
set +e
timeout "$COLLECT_TIMEOUT" "$SM" --json >"$PAYLOAD" 2>"$COLLECT_ERR"
COLLECT_RC=$?
set -e
if [ "$COLLECT_RC" -ne 0 ]; then
  # 124 is `timeout`'s own "I killed it" status and it has no stderr of its own,
  # so name it rather than printing an empty diagnostic.
  if [ "$COLLECT_RC" -eq 124 ]; then
    log "collector TIMED OUT after ${COLLECT_TIMEOUT}s (rc=124) — $SM did not finish"
  else
    log "collector failed (rc=$COLLECT_RC): $(tr '\n' ' ' <"$COLLECT_ERR" | cut -c1-400)"
  fi
  exit 3
fi

# Two gates, and only the first is about the document being a document.
#
# The `hosts` check DOES duplicate the server's first check
# (`SnapshotsFromSessionManager`) — an earlier comment claimed it did not, which
# was simply wrong. It is duplicated on purpose and only here: it is what
# separates "the collector emitted an error page / nothing" from "the server
# rejected a real document", two failures needing completely different fixes
# that a bare HTTP status cannot tell apart. Everything else about schema
# validity stays the server's, and this client is strictly looser.
#
# 🔴 THE SECOND GATE IS THE ONE THAT MATTERS, AND IT EXISTS BECAUSE THIS CLIENT
# IS THE LAST PLACE THAT STILL HAS THE FACT.
#
# `session-manager` deliberately publishes `windows_measured` SEPARATELY from
# `reachable`, so an unmeasured zero is distinguishable from a real one. The
# server's `hostReport` decodes only `reachable` and `windows` — the
# discriminant is discarded at ingest. Since the table is a latest-per-host
# upsert with no reaper, a host that was reachable but whose `list-windows` call
# failed (they are two independent tmux/ssh calls; either can fail alone) would
# be stored as a MEASURED zero, replacing a good snapshot. Nothing downstream
# could ever tell.
#
# So: refuse the push when a host is reachable but its window enumeration did
# not happen. Note what is deliberately NOT refused — `reachable: false` (a
# sleeping laptop is a real state change, and the server keeps the flag), and a
# genuinely measured zero (a workbench after reboot, before tmux starts, which
# the OnStartupSec run will hit). A missing `windows_measured` is treated as
# measured: this collector always emits it, and blocking on an absent field
# would freeze the feeder against a collector that never existed.
if ! python3 -c '
import json,sys
d=json.load(open(sys.argv[1]))
hosts=d.get("hosts")
if not isinstance(hosts, dict) or not hosts:
    raise SystemExit("no non-empty `hosts` object")
torn=[h for h,r in hosts.items()
      if isinstance(r, dict)
      and r.get("reachable") is True
      and r.get("windows_measured") is False
      and not (r.get("windows") or [])]
if torn:
    raise SystemExit(
        "host(s) %s are reachable but their windows were NOT measured; "
        "pushing would overwrite a good snapshot with an unmeasured zero"
        % ", ".join(sorted(torn)))
' "$PAYLOAD" 2>"$WORK/parse.err"; then
  MSG=$(tr '\n' ' ' <"$WORK/parse.err" | cut -c1-300)
  case "$MSG" in
    *"NOT measured"*)
      log "refusing to push: $MSG"
      exit 6
      ;;
    *)
      log "collector output is not a session-manager document: $MSG"
      exit 3
      ;;
  esac
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

# 🔴 SUCCESS IS 2xx, NOT "anything under 400", and the difference is a silent
# forever-failure. The old `[ "$HTTP" -ge 400 ]` accepted:
#   * a 3xx — there is no `-L`, so pointing CLAWGATE_API_URL at any hostname
#     that redirects (an ingress, `clawgate.zacx.dev`) makes curl return the
#     redirect, store NOTHING, and this script log "pushed" and exit 0. At a
#     2-minute cadence, with no consumer watching the read model, that is
#     invisible indefinitely.
#   * `000`, curl's code when it never got a status line.
#   * an EMPTY string — `[ "" -ge 400 ]` writes "integer expected" to stderr and
#     is FALSE, and `set -e` does not fire on a test used as an `if` condition
#     (measured), so it fell through to the success path too.
# A case on the literal covers all three without any numeric comparison.
case "$HTTP" in
  2[0-9][0-9]) : ;;  # accepted
  404)
    # Worth naming: a server predating the snapshot routes is a deploy-order
    # problem (server first), not a payload problem.
    log "server at $API_URL has no /api/tmux/snapshot route (HTTP 404) — it predates the read model; deploy the server first"
    exit 5
    ;;
  3[0-9][0-9])
    log "server at $API_URL REDIRECTED (HTTP $HTTP) and the snapshot was NOT stored — this script does not follow redirects; point CLAWGATE_API_URL at the origin, not an ingress"
    exit 5
    ;;
  *)
    log "push not accepted (HTTP '${HTTP}'): $(tr '\n' ' ' <"$BODY" | cut -c1-300)"
    exit 5
    ;;
esac

log "pushed ${BYTES}B to $API_URL (HTTP $HTTP): $(tr -d '\n' <"$BODY" | cut -c1-200)"
