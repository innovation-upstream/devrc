#!/usr/bin/env bash
# Phase-1 acceptance criterion: the pod's digest is the local CLI's digest, for
# EVERY scope (proposal §4 phase 1, §5 "byte-identity … `cmp`, not eyeballing").
#
# ⚠ IT CANNOT BE A BARE `cmp`, AND SAYING SO IS THE POINT.
# `subsystem_recall.render_text` emits exactly one line naming the store root:
#
#     store: /data                       (pod)
#     store: /home/zach/.claude/…        (workbench)
#
# So the two byte streams are provably NOT identical, and a verifier that
# reported them identical would be lying. What is claimed, precisely:
#
#   after replacing THAT ONE LINE with a fixed token on both sides, `cmp`
#   reports the streams byte-identical.
#
# Beside that verdict every PASS line prints `raw-diff-lines` and
# `store-root-lines` — how many lines differ with NO canonicalisation, and how
# many of those are the store-root line — so a reader can see the excuse was
# spent on exactly the lines it claims (0/0 same-root, 2/2 pod-vs-workbench).
# ⚠ Those numbers are EVIDENCE, not a second gate, and the body of the script
# says why: the two can only disagree if `sed` erased a non-store line, which
# the renderer cannot produce. Gating on them would be an unreachable guard
# counted as coverage.
#
# 🔴 CONTROLS. A comparator that always says PASS is indistinguishable from one
# that works, so this script is exercised BOTH ways in
# `scripts/tests/test_subsystem_store_api.py::TestByteIdentityVerifier`:
# identical stores -> PASS, one entry mutated by a single character -> FAIL with
# the scope named. Do not trust a green run of this script that has not been
# preceded by a red one.
#
# Usage:
#   verify-byte-identity.sh --store <local-root> --url http://127.0.0.1:8102 \
#       --token-file <path> [--scope <one>]...
#
# The URL is whatever reaches the pod — a `kubectl port-forward` in phase 1,
# since there is deliberately no ingress. This script does not create it: a
# port-forward that dies mid-run must look like a failure, not like a fixture.

set -euo pipefail

# CDPATH= : see build-push.sh — a set CDPATH makes `cd` echo its destination.
HERE="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RECALL="$HERE/../lib/subsystem_recall.py"

STORE=""
URL=""
TOKEN_FILE=""
SCOPES=()
CANON='  store: <canonicalised>'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --store)      STORE="${2:?}"; shift 2 ;;
    --url)        URL="${2:?}"; shift 2 ;;
    --token-file) TOKEN_FILE="${2:?}"; shift 2 ;;
    --scope)      SCOPES+=("${2:?}"); shift 2 ;;
    -h|--help)    sed -n '2,35p' "$0"; exit 0 ;;
    *) echo "verify: unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$STORE" ]]      || { echo "verify: --store is required" >&2; exit 2; }
[[ -n "$URL" ]]        || { echo "verify: --url is required" >&2; exit 2; }
[[ -n "$TOKEN_FILE" ]] || { echo "verify: --token-file is required" >&2; exit 2; }
[[ -d "$STORE" ]]      || { echo "verify: local store not found: $STORE" >&2; exit 3; }
[[ -f "$TOKEN_FILE" ]] || { echo "verify: token file not found: $TOKEN_FILE" >&2; exit 3; }

TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
[[ -n "$TOKEN" ]] || { echo "verify: token file $TOKEN_FILE is empty" >&2; exit 3; }

if [[ ${#SCOPES[@]} -eq 0 ]]; then
  shopt -s nullglob
  for d in "$STORE"/*/; do SCOPES+=("$(basename "$d")"); done
  shopt -u nullglob
fi

# 🔴 A comparison over zero scopes passes trivially. That zero is the failure,
# not the all-clear — the same rule `drift-check.sh` follows by printing links
# EXAMINED beside links dangling.
if [[ ${#SCOPES[@]} -eq 0 ]]; then
  echo "verify: 0 scopes found under $STORE — nothing was compared, so nothing is verified." >&2
  exit 4
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0

for scope in "${SCOPES[@]}"; do
  local_out="$tmp/local.$scope"
  remote_out="$tmp/remote.$scope"

  # Local: the CLI, unmodified. `--scope` is explicit, which also disables the
  # repo focus window — the pod has no repo to derive one from, so comparing
  # against a windowed local run would compare two different questions.
  set +e
  python3 "$RECALL" --store "$STORE" --scope "$scope" > "$local_out" 2>"$tmp/local.err"
  rc_local=$?
  set -e
  # exit 3 is "nothing readable"; the bytes are still a legitimate render and the
  # remote must reproduce them, so only a HARDER failure aborts this scope.
  if [[ $rc_local -gt 3 ]]; then
    echo "FAIL scope=$scope local CLI exited $rc_local: $(head -1 "$tmp/local.err")"
    fail=$((fail + 1)); continue
  fi

  # 🔴 CF-Connecting-IP is REQUIRED by the server (phase 1.5): it keys the
  # per-client rate limiter on the one header Cloudflare overwrites, and an
  # absent one fails CLOSED rather than being bucketed with every other
  # unidentified caller. This verifier reaches the pod directly (port-forward or
  # in-cluster), so no proxy sets it and it must identify itself. That is not a
  # hole: anything that can address the pod directly has already bypassed the
  # edge, and the header is only ever TRUSTED because Cloudflare is the sole
  # PUBLIC ingress.
  code=$(curl -sS -o "$remote_out" -D "$tmp/hdr" -w '%{http_code}' \
           -H "Authorization: Bearer $TOKEN" \
           -H "CF-Connecting-IP: ${CLIENT_IP:-127.0.0.1}" \
           "$URL/api/v1/recall/$scope" || echo "000")
  if [[ "$code" != "200" ]]; then
    echo "FAIL scope=$scope remote HTTP $code (body: $(head -c 120 "$remote_out"))"
    fail=$((fail + 1)); continue
  fi

  # An empty body compares equal to an empty body. Refuse both.
  if [[ ! -s "$local_out" || ! -s "$remote_out" ]]; then
    echo "FAIL scope=$scope empty render (local=$(wc -c <"$local_out")B remote=$(wc -c <"$remote_out")B)"
    fail=$((fail + 1)); continue
  fi

  # Claim 2's EVIDENCE, computed before claim 1 so a green cmp is never printed
  # without it: how many lines differ with no canonicalisation at all, and how
  # many of those are the store-root line.
  #
  # ⚠ THESE ARE REPORTED, NOT GATED, AND THAT IS DELIBERATE. `raw` can only
  # differ from `store_lines` if the canonicalisation erased a difference that
  # was NOT a store-root line — and `sed` only rewrites lines matching
  # `^  store: `, which MEASURED against the renderer no entry body can produce
  # (every body line is indented deeper than two spaces, checked in all three
  # modes). So a gate on `raw -eq $store_lines` could never fire: it would be an
  # unreachable guard counted as coverage, which `claude/RULES.md` calls out by
  # name. The numbers are printed instead, so a reader can see that the
  # canonicalisation excuse was spent on exactly the lines it claims.
  raw=$(diff "$local_out" "$remote_out" | grep -c '^[<>]' || true)
  store_lines=$(diff "$local_out" "$remote_out" | grep -c '^[<>]   store: ' || true)

  sed "s|^  store: .*|$CANON|" "$local_out"  > "$tmp/l.canon"
  sed "s|^  store: .*|$CANON|" "$remote_out" > "$tmp/r.canon"

  rev=$(awk 'tolower($1)=="x-store-revision:"{print $2}' "$tmp/hdr" | tr -d '\r' | tail -1)

  # PASS is `cmp` on the canonicalised streams — the byte-identity claim itself.
  # The two counts ride along as evidence: 0/0 is the case where both sides read
  # the SAME root (a local self-check), 2/2 is pod-vs-workbench.
  if cmp -s "$tmp/l.canon" "$tmp/r.canon"; then
    echo "PASS scope=$scope bytes=$(wc -c <"$tmp/l.canon") raw-diff-lines=$raw store-root-lines=$store_lines revision=${rev:-unknown}"
    pass=$((pass + 1))
  else
    echo "FAIL scope=$scope canonicalised-cmp=$(cmp -s "$tmp/l.canon" "$tmp/r.canon" && echo same || echo differs) raw-diff-lines=$raw store-lines=$store_lines"
    # 🔴 `|| true` is load-bearing, not decoration. `diff` exits 1 when the files
    # differ — which is ALWAYS true on this branch — and under `set -o pipefail`
    # that status kills the whole script mid-loop, so the remaining scopes are
    # never compared and the summary line never prints. Measured: the first
    # negative-control run reported a FAIL and then simply stopped.
    diff "$local_out" "$remote_out" | head -20 || true
    fail=$((fail + 1))
  fi
done

echo "verify: scopes=${#SCOPES[@]} pass=$pass fail=$fail"
[[ $fail -eq 0 ]] || exit 1
