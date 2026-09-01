#!/usr/bin/env bash
# Phase-1 acceptance criterion: the pod's digest is the local CLI's digest, for
# EVERY scope (proposal §4 phase 1, §5 "byte-identity … `cmp`, not eyeballing").
#
# ⚠ IT CANNOT BE A BARE `cmp`, AND SAYING SO IS THE POINT.
# `subsystem_recall.render_text` emits exactly one line naming the store root
# and exactly one line naming the machine whose disk was read:
#
#     store: /data                       host: subsystem-store-api-…   (pod)
#     store: /home/zach/.claude/…        host: nixos-<id-prefix>       (workbench)
#
# and the server prepends a transport annotation (the SNAPSHOT block) that the
# local CLI correctly does not emit. So the two byte streams are provably NOT
# identical, and a verifier that reported them identical would be lying. What is
# claimed, precisely:
#
#   after replacing THOSE TWO LINES with a fixed token on both sides and
#   removing the remote's SNAPSHOT block, `cmp` reports the streams
#   byte-identical.
#
# 🔴 THE `host:` RULE IS THE ONE THAT WAS MISSING, AND ITS ABSENCE MADE THIS
# SCRIPT PERMANENTLY RED. `store_host_line()` shipped when the store became
# PER-HOST; it names THIS machine, so it differs between the workbench and the
# pod BY CONSTRUCTION and no run of this script could ever pass again
# (`verify: scopes=16 pass=0 fail=16` on a store whose content was identical).
# `claude/RULES.md`: a permanently-red gate is worse than no gate.
#
# Beside that verdict every PASS line prints its ACCOUNTING: `raw-diff-lines`
# (how many lines differ with NO canonicalisation at all) decomposed into
# `store-root-lines`, `host-lines` and `snapshot-block-lines`, plus the
# `accounted-for` sum — so a reader can see the excuse was spent on exactly the
# lines it claims (0/0/0 same-root same-host, 2/2/2 pod-vs-workbench).
# ⚠ Those numbers are EVIDENCE, not a second gate, and the body of the script
# says why: they can only disagree if `sed` erased a line that was none of the
# three, which the renderer cannot produce. Gating on them would be an
# unreachable guard counted as coverage.
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
CANON_HOST='  host: <canonicalised>'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --store)      STORE="${2:?}"; shift 2 ;;
    --url)        URL="${2:?}"; shift 2 ;;
    --token-file) TOKEN_FILE="${2:?}"; shift 2 ;;
    --scope)      SCOPES+=("${2:?}"); shift 2 ;;
    -h|--help)    sed -n '2,51p' "$0"; exit 0 ;;
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

  # CF-Connecting-IP keys the server's per-client rate limiter — but ONLY when
  # the request arrives from a peer in `$SUBSYSTEM_STORE_TRUSTED_PROXIES`.
  #
  # ⚠ ON THIS SCRIPT'S USUAL PATH IT IS INERT, AND AN EARLIER VERSION OF THIS
  # COMMENT CLAIMED THE OPPOSITE ("REQUIRED by the server … it must identify
  # itself"). A port-forward presents peer `127.0.0.1` — measured out of the
  # pod's own /proc/net/tcp, both ends loopback, because the kubelet enters the
  # pod's network namespace — which is not the allowlisted address, so the
  # server ignores this header and buckets the request under the peer. Nothing
  # here depends on it and omitting it would work exactly as well.
  #
  # It is still sent, deliberately: point `--url` at the nebula gateway instead
  # of a port-forward and the peer IS trusted, at which point this header
  # becomes the client identity and sending an explicit one is correct. One
  # invocation that behaves the same on both paths beats two.
  #
  # 🔴 The header is only ever TRUSTED because Cloudflare is the sole PUBLIC
  # ingress AND the peer is a named proxy. Neither half alone is enough — that
  # was the phase-1.5b defect.
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
  # many of those each named cause accounts for.
  #
  # ⚠ THESE ARE REPORTED, NOT GATED, AND THAT IS DELIBERATE. `raw` can only
  # exceed the sum below if the canonicalisation erased a difference that was
  # none of the three named causes — and `sed` only rewrites lines matching
  # `^  store: ` or `^  host: `, which MEASURED against the renderer no entry
  # body can produce (every body line is indented FOUR OR MORE spaces — the
  # section headings at four, their content at six, and a body line that itself
  # begins `  host: ` renders at eight; checked in all four modes: default,
  # `--ref`, `--search`, `--list`). So a gate on `raw -eq $accounted` could
  # never fire: it would be an unreachable guard counted as coverage, which
  # `claude/RULES.md` calls out by name. The numbers are printed instead, so a
  # reader can see that the canonicalisation excuse was spent on exactly the
  # lines it claims.
  #
  # 🔴 EVERY CANONICALISATION RULE MUST APPEAR IN THIS SUM. A rule that erases a
  # difference without a matching count widens the blind spot instead of the
  # gate — which is why `host_lines` was added in the same commit as the `host:`
  # sed, and why the snapshot block is counted by the number of lines actually
  # deleted rather than assumed to be two.
  raw=$(diff "$local_out" "$remote_out" | grep -c '^[<>]' || true)
  store_lines=$(diff "$local_out" "$remote_out" | grep -c '^[<>]   store: ' || true)
  host_lines=$(diff "$local_out" "$remote_out" | grep -c '^[<>]   host: ' || true)

  # 🔴 THE SNAPSHOT BLOCK IS A TRANSPORT ANNOTATION, NOT PART OF THE REPORT.
  # The server prepends one line dating the COPY it serves (plus a blank
  # separator) to every report — see `server.snapshot_freshness`, and the README
  # for the measured incident that put it there. The local CLI reads the
  # authoritative store and correctly emits no such line, so leaving it in would
  # make this script FAIL on every scope for a difference that is not about the
  # render at all — the same excuse the two lines above already earn.
  #
  # 🔴 THE BLOCK IS MEASURED, NOT ASSUMED TO BE TWO LINES — AND THIS IS
  # HARDENING FOR A SKEW THAT HAS NOT BEEN OBSERVED, NOT A FIX FOR A DEFECT.
  # ⚠ THE PREVIOUS RULE WAS NOT BROKEN. An anchored `,+1d` deletes the banner
  # AND its blank separator, which is exactly the arrangement THIS TREE's
  # `server.py` emits, and `test_every_permitted_difference_is_ACCOUNTED_FOR_
  # not_merely_small` passes on it — before this change and after. Do not read
  # this comment as evidence against it and do not go looking for the incident:
  # there wasn't one. The retraction is recorded in this branch's history.
  #
  # Two structural reasons to measure the length anyway, neither of which needs
  # a failure to have happened:
  #   * THE LENGTH IS NOT THIS CHECKOUT'S TO ASSUME. This script does not
  #     compare against this tree's `server.py`; it compares against whatever
  #     image is DEPLOYED, and a fixed-size delete is a bet that those two are
  #     the same version. Nothing here can check that bet.
  #   * DELETED MUST EQUAL COUNTED. `,+1d` removes lines that never enter the
  #     accounting above, so its removals could not be reconciled against `raw`;
  #     a measured length can. That is the whole point of the sum, and a rule
  #     exempt from it is the blind spot the sum exists to close.
  #
  # So the block is defined as the run of lines at the HEAD of the remote stream
  # that are the banner or are blank, its LENGTH is measured, and exactly that
  # many lines are dropped.
  #
  # Two narrowings keep this from reaching into a report body:
  #   * it is a HEAD run only — a banner appearing anywhere else is left alone
  #     and will fail the comparison, which is correct;
  #   * the run must CONTAIN the banner, so a remote that merely starts with a
  #     blank line has nothing stripped (that is a real difference).
  # An image WITHOUT the stamp is therefore unaffected — the run is empty and
  # nothing is deleted — which is what keeps this runnable against 0.2.0.
  snapshot_lines=$(grep -c '^🔴 SNAPSHOT, NOT THE SOURCE' "$remote_out" || true)
  snapshot_block=$(awk '
    /^🔴 SNAPSHOT, NOT THE SOURCE/ { n++; seen = 1; next }
    $0 == ""                       { n++; next }
                                   { exit }
    END { print (seen ? n + 0 : 0) }
  ' "$remote_out")

  # 🔴 The SAME two seds on both sides. A canonicalisation applied to one stream
  # only is not a canonicalisation, it is an edit — and the `store:`/`host:`
  # rules are only honest because the local render is rewritten too.
  canon_seds=(-e "s|^  store: .*|$CANON|" -e "s|^  host: .*|$CANON_HOST|")
  sed "${canon_seds[@]}" "$local_out" > "$tmp/l.canon"
  remote_seds=("${canon_seds[@]}")
  # `1,0d` is a sed error, not a no-op, so the delete is only added when the
  # block is non-empty.
  if [[ "$snapshot_block" -gt 0 ]]; then
    remote_seds=(-e "1,${snapshot_block}d" "${remote_seds[@]}")
  fi
  sed "${remote_seds[@]}" "$remote_out" > "$tmp/r.canon"

  # Printed beside `raw` so the reader does the subtraction once, here, instead
  # of in their head on every PASS line.
  accounted=$((store_lines + host_lines + snapshot_block))

  rev=$(awk 'tolower($1)=="x-store-revision:"{print $2}' "$tmp/hdr" | tr -d '\r' | tail -1)

  # PASS is `cmp` on the canonicalised streams — the byte-identity claim itself.
  # The counts ride along as evidence: 0/0/0 is the case where both sides read
  # the SAME root on the SAME host (a local self-check), 2/2/2 is
  # pod-vs-workbench.
  if cmp -s "$tmp/l.canon" "$tmp/r.canon"; then
    # `snapshot-line` rides along as EVIDENCE, exactly like the counts above
    # and gated for the same reason they are not: a `0` here means the remote
    # served no stamp, which is true and expected against a pre-0.3.0 image, so
    # failing on it would make this script permanently red until a deploy lands
    # (RULES.md). Printed so a reader can see WHICH of the two the green means.
    echo "PASS scope=$scope bytes=$(wc -c <"$tmp/l.canon") raw-diff-lines=$raw store-root-lines=$store_lines host-lines=$host_lines snapshot-line=$snapshot_lines snapshot-block-lines=$snapshot_block accounted-for=$accounted revision=${rev:-unknown}"
    pass=$((pass + 1))
  else
    echo "FAIL scope=$scope canonicalised-cmp=$(cmp -s "$tmp/l.canon" "$tmp/r.canon" && echo same || echo differs) raw-diff-lines=$raw store-lines=$store_lines host-lines=$host_lines snapshot-block-lines=$snapshot_block accounted-for=$accounted"
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
