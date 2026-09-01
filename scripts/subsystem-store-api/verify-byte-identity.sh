#!/usr/bin/env bash
# Phase-1 acceptance criterion: the pod's store is the local CLI's store, for
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
# identical, and a verifier that reported them identical would be lying.
#
# 🔴 AND IT CANNOT COMPARE THE WHOLE-SCOPE DIGEST AT ALL — THAT IS THE SECOND
# THING THAT MADE THIS SCRIPT PERMANENTLY RED, AFTER `host:`.
# `subsystem_recall` orders its INDEX **newest-first by entry-file mtime**, and
# picks the digest's one featured BODY the same way (`select_featured`'s
# most-recent fallback). The transport does not preserve mtime — `seed.sh`
# `rsync`s into a stage and `tar`s that into the pod — so two stores holding
# byte-identical entries render their index in a DIFFERENT ORDER and feature a
# DIFFERENT entry. MEASURED 2026-09-01 against the live pod, store
# `~/.claude/analyze-service-index`, over a `kubectl port-forward`:
#
#     FAIL scope=devrc            raw-diff-lines=45   accounted-for=6
#     FAIL scope=cli              raw-diff-lines=8    accounted-for=6
#     PASS scope=storage-resolver (1 entry)
#     FAIL scope=homelab-infra    raw-diff-lines=108  accounted-for=6
#     FAIL scope=datapacket-talos raw-diff-lines=336  accounted-for=6
#
# The `cli` scope is the clean isolation — its index ROWS were identical and the
# only unaccounted difference was one row's POSITION. `claude/RULES.md`: a
# permanently-red gate is worse than no gate.
#
# 🔴 TWO READINGS OF THAT RUN WERE WRONG, AND THIS IS THE CORRECTION.
# The `storage-resolver` line above said `(2 entries)` and the paragraph
# concluded "every passing scope had 2 entries; every failing one had more".
# RE-MEASURED 2026-09-01 on this host, same store, counting entries as
# `subsystem_recall` INDEXES them rather than as files on disk:
#
#     cli=5  devrc=26  datapacket-talos=49  homelab-infra=0  storage-resolver=1
#
# `storage-resolver/` holds `backblaze.md` plus a `README.md`, and a README in a
# scope is correctly NOT indexed — so it is a ONE-entry scope. NO TWO-ENTRY
# SCOPE APPEARS IN THAT RUN AT ALL, so the data could never support a two-entry
# boundary, in either direction.
#
# The boundary that does hold is ARITHMETIC, not measured: a ONE-entry index has
# exactly one possible order and cannot diverge; TWO OR MORE is where the order
# can differ. That is the same argument the ordering fixture in
# `scripts/tests/test_subsystem_store_api.py` makes for itself when it chooses
# FOUR refs — two entries admit only two orders, so a two-entry fixture is one
# coin-flip away from asserting nothing.
#
# And `homelab-infra` was NOT an ordering failure. It holds ZERO indexed entries
# on this host (one `README.md`), so its local render is `status=scope-empty`
# with no INDEX block at all — 102 unaccounted lines that ordering structurally
# cannot produce. That FAIL was a SET difference, the lagging read-through cache
# case described below. THREE of the four FAILs were ordering, not four.
#
# 🔴 SO THE CLAIM IS RESTATED AT THE LEVEL IT CAN HOLD. Byte-identity of the
# RENDER was always a proxy; what phase 1 actually needs is that the pod holds
# the same entries, with the same bytes. Both halves are compared, per scope:
#
#   1. THE SCOPE-LEVEL RENDER, in `mode=list` — no body, so no mtime-selected
#      featured entry — with the index ROWS compared as a SORTED set and the
#      rest of the stream compared verbatim. That covers the prose, the status
#      line, the malformed-file block, the empty-scope notice, and each row's
#      own content (ref, nuance count, sensitivity, the `🔴 N OPEN` badge).
#   2. THE ENTRY SET: the refs the two index blocks list, compared with `comm`.
#      A ref on one side and not the other FAILS and NAMES it.
#   3. EACH ENTRY'S BYTES, as its own `--ref` / `?ref=` render — a narrowing
#      that prints no index at all, so it carries no order. There is NO route
#      serving raw entry bytes; the API only ever returns renders, so "the
#      entry's bytes" is realised as "the entry's own single-ref render".
#      🔴 AND THAT RENDER MUST SAY `status=recalled`, OR THE SCOPE IS REFUSED.
#      A `--ref` run that resolves to no single entry still prints a well-formed
#      report — a NOTICE where the body should be — and still exits 0. See the
#      per-entry arm for the mechanism; without that check `entries=N` counts
#      streams that carry no entry at all.
#
# What is claimed, precisely:
#
#   after replacing THOSE TWO LINES with a fixed token on both sides, removing
#   the remote's SNAPSHOT block, and comparing the index rows as a set rather
#   than as a sequence, `cmp` reports every compared stream byte-identical —
#   and the entry sets are equal.
#
# 🔴 THE `host:` RULE WAS THE FIRST HALF OF THE RED. `store_host_line()` shipped
# when the store became PER-HOST; it names THIS machine, so it differs between
# the workbench and the pod BY CONSTRUCTION and no run of this script could ever
# pass again (`verify: scopes=16 pass=0 fail=16` on a store whose content was
# identical). Closing it left the ordering half above, which is why a fix that
# addressed only `host:` still reported `scopes=5 pass=1 fail=4`.
#
# 🔴 WHAT THIS SCRIPT IS AN ACCEPTANCE CHECK *FOR*, AND WHEN IT IS MEANINGFUL.
# After the phase-1 cutover the POD is canonical and each host's local store is
# a read-through CACHE that may legitimately lag: MEASURED 2026-09-01, scope
# `devrc` held 26 entries locally and 29 on the pod, with nothing wrong. So an
# entry-set difference is an ORDINARY OPERATIONAL STATE in general, and this
# script is only an acceptance check IMMEDIATELY AFTER A SEED/PUSH — which is
# exactly where `cairn-cutover.py` runs it (P4). Run it at any other moment and
# a FAIL on the set arm may be telling you the cache is behind, not that the
# push was lossy. The set arm is NOT weakened to accommodate that: a check that
# tolerated a missing entry could not detect a half-copied seed, which is the
# one thing P4 exists to catch.
#
# Beside that verdict every PASS line prints its ACCOUNTING: `raw-diff-lines`
# (how many lines differ with NO canonicalisation at all, summed over every
# stream compared for the scope) decomposed into `store-root-lines`,
# `host-lines`, `snapshot-block-lines` and `index-order-lines`, plus the
# `accounted-for` sum — so a reader can see the excuse was spent on exactly the
# lines it claims. `entries=` says how many per-entry comparisons were made, so
# a scope that compared no bodies is visible rather than silently green.
# ⚠ Those numbers are EVIDENCE, not a second gate, and the body of the script
# says why. Gating on them would be an unreachable guard counted as coverage.
#
# 🔴 CONTROLS. A comparator that always says PASS is indistinguishable from one
# that works, so this script is exercised BOTH ways in
# `scripts/tests/test_subsystem_store_api.py::TestByteIdentityVerifier`:
# identical stores -> PASS, one entry mutated by a single character -> FAIL with
# the scope named, a shuffled-mtime store -> PASS, an extra entry on one side ->
# FAIL naming the ref, a mutation UNDER a shuffle -> FAIL, and a mutation behind
# an AMBIGUOUS REF -> FAIL naming the status. Do not trust a green run of this
# script that has not been preceded by a red one.
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

# The header block above, printed for `--help`. Derived rather than a hardcoded
# line range: the range was `2,51p` and this header has since grown twice, so a
# literal would silently truncate the argument a reader came for.
print_help() {
  awk 'NR == 1 { next } /^#/ { print; next } { exit }' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --store)      STORE="${2:?}"; shift 2 ;;
    --url)        URL="${2:?}"; shift 2 ;;
    --token-file) TOKEN_FILE="${2:?}"; shift 2 ;;
    --scope)      SCOPES+=("${2:?}"); shift 2 ;;
    -h|--help)    print_help; exit 0 ;;
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
#
# ⚠ THIS IS NOW SENT ONCE PER ENTRY, NOT ONCE PER SCOPE. The per-entry arm
# issues one request per ref, so a 50-entry scope is 51 requests where it used
# to be 1. Against the pod that is bucketed under one client: if the limiter is
# ever tightened, THIS is the caller that will hit it first.
HTTP_CODE=""
fetch_remote() {  # $1 = scope, $2 = out file, rest: curl --data-urlencode args
  local scope="$1" out="$2"
  shift 2
  HTTP_CODE=$(curl -sS -o "$out" -D "$tmp/hdr" -w '%{http_code}' \
                -H "Authorization: Bearer $TOKEN" \
                -H "CF-Connecting-IP: ${CLIENT_IP:-127.0.0.1}" \
                --get "$@" \
                "$URL/api/v1/recall/$scope" < /dev/null || echo "000")
}

# 🔴 THE SAME TWO SEDS ON BOTH SIDES. A canonicalisation applied to one stream
# only is not a canonicalisation, it is an edit — and the `store:`/`host:`
# rules are only honest because the local render is rewritten too.
#
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
# `server.py` emits. Do not read this comment as evidence against it and do not
# go looking for the incident: there wasn't one.
#
# Two structural reasons to measure the length anyway, neither of which needs
# a failure to have happened:
#   * THE LENGTH IS NOT THIS CHECKOUT'S TO ASSUME. This script does not
#     compare against this tree's `server.py`; it compares against whatever
#     image is DEPLOYED, and a fixed-size delete is a bet that those two are
#     the same version. Nothing here can check that bet.
#   * DELETED MUST EQUAL COUNTED. `,+1d` removes lines that never enter the
#     accounting, so its removals could not be reconciled against `raw`; a
#     measured length can. That is the whole point of the sum, and a rule
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
P_RAW=0; P_STORE=0; P_HOST=0; P_SNAP=0; P_BLOCK=0
canon_pair() {  # $1 = local raw, $2 = remote raw, $3 = out prefix -> $3.l, $3.r
  local l="$1" r="$2" o="$3"
  P_RAW=$(diff "$l" "$r" | grep -c '^[<>]' || true)
  P_STORE=$(diff "$l" "$r" | grep -c '^[<>]   store: ' || true)
  P_HOST=$(diff "$l" "$r" | grep -c '^[<>]   host: ' || true)
  P_SNAP=$(grep -c '^🔴 SNAPSHOT, NOT THE SOURCE' "$r" || true)
  P_BLOCK=$(awk '
    /^🔴 SNAPSHOT, NOT THE SOURCE/ { n++; seen = 1; next }
    $0 == ""                       { n++; next }
                                   { exit }
    END { print (seen ? n + 0 : 0) }
  ' "$r")

  local canon_seds=(-e "s|^  store: .*|$CANON|" -e "s|^  host: .*|$CANON_HOST|")
  sed "${canon_seds[@]}" "$l" > "$o.l"
  local remote_seds=("${canon_seds[@]}")
  # `1,0d` is a sed error, not a no-op, so the delete is only added when the
  # block is non-empty.
  if [[ "$P_BLOCK" -gt 0 ]]; then
    remote_seds=(-e "1,${P_BLOCK}d" "${remote_seds[@]}")
  fi
  sed "${remote_seds[@]}" "$r" > "$o.r"
}

# 🔴 SPLIT THE INDEX ROWS OUT OF THE STREAM — the ONE region whose order is a
# function of filesystem metadata the transport does not promise.
#
# `render_listing` emits `["", <head>, *rows]` and then AT MOST one parenthesised
# notice (a page-past-the-end, or "N more entries NOT LISTED"), so a row is a
# line that (a) follows the `INDEX (` head, (b) precedes the next blank line,
# and (c) does not start with `  (`. Everything else — the caveat, the status
# line, the MALFORMED block, the empty-scope notice, the head itself with its
# COUNTS — stays in the frame and is compared VERBATIM, in order.
#
# 🔴 THE NARROWING IS THE POINT. Sorting the whole stream would make this
# comparison a multiset over lines, which passes when a line moves between
# sections. Only the rows are reordered, because only the rows are ordered by
# mtime.
split_index() {  # $1 = in, $2 = frame out, $3 = rows out
  : > "$2"; : > "$3"
  awk -v frame="$2" -v rows="$3" '
    /^INDEX \(/      { inb = 1; print > frame; next }
    inb && $0 == ""  { inb = 0; print > frame; next }
    inb && /^  \(/   { print > frame; next }
    inb              { print > rows;  next }
                     { print > frame }
  ' "$1"
}

diff_lines() {  # $1, $2 -> count of differing lines
  diff "$1" "$2" | grep -c '^[<>]' || true
}

# `render_text`'s FIRST line is `subsystem-recall: status=<status> scope=<scope>`,
# on every status branch including the ones that return immediately — so this
# reads the report's own verdict rather than inferring one from the body.
# Empty output means the line was absent, which is itself a refusable answer.
recall_status() {  # $1 = a render -> its status token, or empty
  awk '/^subsystem-recall: status=/ { s = $2; sub(/^status=/, "", s); print s; exit }' "$1"
}

pass=0
fail=0
entries_compared=0

for scope in "${SCOPES[@]}"; do
  local_out="$tmp/local.$scope"
  remote_out="$tmp/remote.$scope"

  # Local: the CLI, unmodified. `--scope` is explicit, which also disables the
  # repo focus window — the pod has no repo to derive one from, so comparing
  # against a windowed local run would compare two different questions.
  #
  # `--list` is the INDEX ONLY. The default digest would print one entry BODY
  # chosen by mtime, which is the very metadata the transport does not carry.
  set +e
  python3 "$RECALL" --store "$STORE" --scope "$scope" --list \
      > "$local_out" 2>"$tmp/local.err"
  rc_local=$?
  set -e
  # exit 3 is "nothing readable"; the bytes are still a legitimate render and the
  # remote must reproduce them, so only a HARDER failure aborts this scope.
  if [[ $rc_local -gt 3 ]]; then
    echo "FAIL scope=$scope local CLI exited $rc_local: $(head -1 "$tmp/local.err")"
    fail=$((fail + 1)); continue
  fi

  fetch_remote "$scope" "$remote_out" --data-urlencode "mode=list"
  if [[ "$HTTP_CODE" != "200" ]]; then
    echo "FAIL scope=$scope remote HTTP $HTTP_CODE (body: $(head -c 120 "$remote_out"))"
    fail=$((fail + 1)); continue
  fi

  # An empty body compares equal to an empty body. Refuse both.
  if [[ ! -s "$local_out" || ! -s "$remote_out" ]]; then
    echo "FAIL scope=$scope empty render (local=$(wc -c <"$local_out")B remote=$(wc -c <"$remote_out")B)"
    fail=$((fail + 1)); continue
  fi

  rev=$(awk 'tolower($1)=="x-store-revision:"{print $2}' "$tmp/hdr" | tr -d '\r' | tail -1)

  # 🔴 A PAGINATED INDEX IS REFUSED, NOT PARTIALLY COMPARED. Past
  # `LISTING_PAGE_SIZE` the reader pages the index, and BOTH the page membership
  # and the ref column's width (`max(len(ref) for e in report.listing)`, computed
  # PER PAGE) are then functions of the mtime order. Two byte-identical stores
  # would put different refs on page 1 and pad them to different widths, so page
  # 1 is not comparable and the refs beyond it were never read at all. Comparing
  # page 1 alone would be a partial check reported as a clean one; this says so
  # instead. Enlarging the reader's page cap, or teaching this script to walk
  # every page and normalise the padding, are the two ways out.
  #
  # 🔴 STATE THE HEADROOM, NOT JUST "NOT NEEDED TODAY" — the headroom is the
  # number that says WHEN to act, and when it runs out this refusal becomes the
  # permanently-red gate this whole script exists to have removed: the verifier
  # exits 1, `cairn-cutover.py::_acceptance` returns `RC_ACCEPTANCE`, and the
  # cutover refuses with the store left unfrozen. MEASURED 2026-09-01 on this
  # host, `LISTING_PAGE_SIZE = 100`: the largest scope is `datapacket-talos` at
  # 50 files / 49 INDEXED — 49% of the cap, 51 entries of headroom. This arm
  # greps BOTH renders, so the binding side is whichever store is larger; the
  # pod's copy of that scope is the larger one and is NOT measured from here.
  # The store is append-mostly and pruning is manual, so the number only grows.
  paged=$(( $(grep -cE '^INDEX \(.*\(page [0-9]+ of [0-9]+\):$' "$local_out" || true) \
          + $(grep -cE '^INDEX \(.*\(page [0-9]+ of [0-9]+\):$' "$remote_out" || true) ))
  if [[ "$paged" -gt 0 ]]; then
    echo "FAIL scope=$scope index is PAGINATED — this comparator reads ONE index page, and both page membership and the ref column width are mtime-derived, so page 1 is not comparable and the rest was never read. Nothing about this scope was verified."
    fail=$((fail + 1)); continue
  fi

  canon_pair "$local_out" "$remote_out" "$tmp/s"
  raw=$P_RAW; store_lines=$P_STORE; host_lines=$P_HOST
  snapshot_lines=$P_SNAP; snapshot_block=$P_BLOCK
  bytes=$(wc -c <"$tmp/s.l")

  split_index "$tmp/s.l" "$tmp/s.l.frame" "$tmp/s.l.rows"
  split_index "$tmp/s.r" "$tmp/s.r.frame" "$tmp/s.r.rows"
  LC_ALL=C sort "$tmp/s.l.rows" > "$tmp/s.l.rows.sorted"
  LC_ALL=C sort "$tmp/s.r.rows" > "$tmp/s.r.rows.sorted"

  # `$1` of a row is its ref — `listing_line` is `  <ref><pad>  <n> nuance …`.
  # 🔴 THE SET IS TAKEN FROM THE INDEX, NEVER FROM `ls`. A `*.md` file the loader
  # rejects (no `service:`) is a real file that is NOT indexed and is NOT
  # `--ref`-addressable; a bare directory listing would demand a comparison the
  # API cannot answer and report a difference that does not exist. MEASURED: a
  # `README` sitting in a scope is correctly absent from both index blocks.
  awk '{print $1}' "$tmp/s.l.rows" | LC_ALL=C sort > "$tmp/s.l.refs"
  awk '{print $1}' "$tmp/s.r.rows" | LC_ALL=C sort > "$tmp/s.r.refs"

  # 🔴 THE SET ARM RUNS FIRST, AND THAT ORDER IS LOAD-BEARING. The index head
  # names the entry COUNT, so a set difference of unequal size would also fail
  # the frame comparison below and a set difference of equal size would fail the
  # row comparison — either way the set arm would be an unreachable guard
  # reporting a message nobody ever sees. Running it first makes the NAMED ref
  # the finding, which is the actionable one.
  # LC_ALL=C on BOTH the sorts above and the `comm` here: `comm` re-checks the
  # ordering it was promised, and a locale mismatch between the two makes it
  # report "file is not in sorted order" — or, worse, silently miss a ref.
  only_local="$(LC_ALL=C comm -23 "$tmp/s.l.refs" "$tmp/s.r.refs")"
  only_remote="$(LC_ALL=C comm -13 "$tmp/s.l.refs" "$tmp/s.r.refs")"
  if [[ -n "$only_local" || -n "$only_remote" ]]; then
    n_only_local=$(printf '%s' "$only_local" | grep -c . || true)
    n_only_remote=$(printf '%s' "$only_remote" | grep -c . || true)
    echo "FAIL scope=$scope entry SET differs local-only=$n_only_local pod-only=$n_only_remote"
    while IFS= read -r ref; do
      if [[ -n "$ref" ]]; then echo "  ONLY ON THIS HOST: scope=$scope ref=$ref"; fi
    done <<< "$only_local"
    while IFS= read -r ref; do
      if [[ -n "$ref" ]]; then echo "  ONLY ON THE POD:   scope=$scope ref=$ref"; fi
    done <<< "$only_remote"
    fail=$((fail + 1)); continue
  fi

  frame_diff=$(diff_lines "$tmp/s.l.frame" "$tmp/s.r.frame")
  # ⚠ `rows_diff` IS NOT INDEPENDENT DETECTION, AND CLAIMING IT WOULD BE THE
  # "reads as coverage while providing none" failure. Every field on a row is
  # DERIVED from the entry body — the ref, the nuance count, the sensitivity,
  # the `🔴 N OPEN` badge — so any difference it can see is also seen, a step
  # later, by the per-entry arm. Its real job is the ACCOUNTING: `index_order`
  # below is measured as the diff reduction the SORT bought, so a row
  # comparison wired to a constant zero would fold a GENUINE row difference
  # into the reorder excuse and print it as `accounted-for`. It also fails
  # fast, before N per-entry requests, and attributes the difference to the
  # index rather than to one body. `test_a_difference_VISIBLE_IN_THE_INDEX_ROW_
  # is_reported_AS_ONE` pins that — on the count, not on the message, because
  # a mutant that zeroes this still FAILS via the per-entry arm.
  rows_diff=$(diff_lines "$tmp/s.l.rows.sorted" "$tmp/s.r.rows.sorted")
  residual=$((frame_diff + rows_diff))

  # 🔴 THE REORDER RULE IS COUNTED LIKE EVERY OTHER RULE — MEASURED, NOT
  # ASSUMED. `index-order-lines` is the diff reduction the sort actually bought:
  # differing lines AFTER the store/host/snapshot canonicalisation, minus what
  # still differs once the rows are sorted. A rule that erases a difference
  # without a matching count widens the blind spot instead of the gate, which is
  # why `host_lines` was added in the same commit as the `host:` sed and why the
  # snapshot block is counted by lines actually deleted.
  #
  # On a PASS the residual is 0 by construction, so `accounted-for` equals `raw`
  # exactly. The clamp only ever bites on a FAIL, where the identity is not
  # claimed anyway; a negative count in a sum labelled "accounted for" would be
  # a reader-facing lie.
  canon_diff=$(diff_lines "$tmp/s.l" "$tmp/s.r")
  index_order=$((canon_diff - residual))
  if (( index_order < 0 )); then index_order=0; fi

  if [[ "$residual" -ne 0 ]]; then
    accounted=$((store_lines + host_lines + snapshot_block + index_order))
    echo "FAIL scope=$scope scope-level render differs (frame-lines=$frame_diff sorted-row-lines=$rows_diff) raw-diff-lines=$raw store-root-lines=$store_lines host-lines=$host_lines snapshot-block-lines=$snapshot_block index-order-lines=$index_order accounted-for=$accounted"
    # 🔴 `|| true` is load-bearing, not decoration. `diff` exits 1 when the files
    # differ — which is ALWAYS true on this branch — and under `set -o pipefail`
    # that status kills the whole script mid-loop, so the remaining scopes are
    # never compared and the summary line never prints. Measured: the first
    # negative-control run reported a FAIL and then simply stopped.
    diff "$tmp/s.l.frame" "$tmp/s.r.frame" | head -20 || true
    diff "$tmp/s.l.rows.sorted" "$tmp/s.r.rows.sorted" | head -20 || true
    fail=$((fail + 1)); continue
  fi

  # --- Each entry's own bytes, as its own single-ref render ------------------
  # A `--ref` run is a NARROWING: it prints that one entry in full and NO index,
  # so nothing in this stream is ordered by mtime and a plain `cmp` is honest.
  n_entries=0
  entry_fail=""
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    el="$tmp/e.local"; er="$tmp/e.remote"
    set +e
    # `</dev/null`: this loop reads the ref list ON STDIN, so a child that read
    # from stdin would eat the remaining refs and the loop would silently
    # compare fewer entries than it listed.
    python3 "$RECALL" --store "$STORE" --scope "$scope" --ref "$ref" \
        > "$el" 2>"$tmp/local.err" < /dev/null
    rc_entry=$?
    set -e
    if [[ $rc_entry -gt 3 ]]; then
      entry_fail="local CLI exited $rc_entry for ref=$ref: $(head -1 "$tmp/local.err")"
      break
    fi
    fetch_remote "$scope" "$er" --data-urlencode "ref=$ref"
    if [[ "$HTTP_CODE" != "200" ]]; then
      entry_fail="remote HTTP $HTTP_CODE for ref=$ref (body: $(head -c 120 "$er"))"
      break
    fi
    if [[ ! -s "$el" || ! -s "$er" ]]; then
      entry_fail="empty render for ref=$ref (local=$(wc -c <"$el")B remote=$(wc -c <"$er")B)"
      break
    fi
    canon_pair "$el" "$er" "$tmp/e"

    # 🔴 A PER-ENTRY STREAM THAT RENDERED NO ENTRY IS REFUSED, NOT COMPARED —
    # AND THIS IS A FALSE-GREEN, NOT A MISSING NICETY.
    # Every non-`recalled` status prints a well-formed report with a NOTICE
    # where the body should be (`AMBIGUOUS REF …`, `NO SUCH ENTRY …`), and
    # `subsystem_recall._exit_for` returns 0 for all of them — only
    # `*-unreadable` is non-zero — so `rc_entry > 3` above cannot see it. The
    # two notices are then byte-identical whenever the two stores index the
    # same refs, which the SET ARM has already established by the time this
    # loop runs. `cmp` therefore accepts them, and `entries=` counts a body
    # that was never rendered on either side.
    #
    # 🔴 REACHABLE BY A SUPPORTED CONVENTION, NOT ONLY BY A STORE DEFECT.
    # `resolve_ref_tiered` matches a bare ref on `e.slug` alone, so a scope
    # holding `alpha.md` AND `alpha.process.md` indexes the refs `alpha` and
    # `alpha.process` while `--ref alpha` raises `AmbiguousRefError` —
    # `<slug>.<kind>.md` is documented (`KINDS = service|process|org|doc`,
    # worked example `repo-cos.process` in the resolver's own docstring).
    # No scope in the live store uses that shape today; the store is
    # append-mostly, and this was reproduced end-to-end against a pod-shaped
    # stub whose only real difference was ONE CHARACTER inside `alpha.md`:
    # `PASS scope=gadget entries=3 … entries-compared=3`, exit 0.
    #
    # REFUSED, not merely left out of `entries=`. A stream silently dropped
    # from the count is the same coverage-in-name-only shape one level down:
    # the scope would still print PASS while one of its entries had not been
    # compared by anything. The remedy is a store fix (`prune-index` exists for
    # exactly `ref-ambiguous`), and until it lands the honest verdict about
    # this scope is that it was not verified.
    #
    # The server sets the same value in `X-Store-Status` (`_serve_report`, from
    # the same `report.status`), so gating on the header too would be one fact
    # read twice — and would additionally bet on the DEPLOYED image emitting
    # that header, which nothing here can check. The body is the thing being
    # compared, so the body is what is gated on.
    #
    # ⚠ THE TWO HALVES ARE NOT INDEPENDENTLY TESTABLE, AND THIS SAYS SO RATHER
    # THAN READING AS TWO COVERED CASES. Mutating either half away leaves both
    # regression tests GREEN (measured: mutants M6/M7 SURVIVED), because a
    # one-sided status disagreement implies the two stores index DIFFERENT
    # REFS, and the SET ARM above already failed the scope by then. What the
    # remote half is actually for is a served body that is not a render at all
    # — no `subsystem-recall: status=` line, so `st_remote` is empty — which no
    # deployed image has been seen to produce and no test here reaches.
    st_local="$(recall_status "$tmp/e.l")"
    st_remote="$(recall_status "$tmp/e.r")"
    if [[ "$st_local" != "recalled" || "$st_remote" != "recalled" ]]; then
      entry_fail="ref=$ref rendered NO ENTRY BODY — status local=${st_local:-<no status line>} pod=${st_remote:-<no status line>}, expected recalled on both. This entry's bytes were NOT compared and nothing about it is verified."
      break
    fi

    raw=$((raw + P_RAW))
    store_lines=$((store_lines + P_STORE))
    host_lines=$((host_lines + P_HOST))
    snapshot_lines=$((snapshot_lines + P_SNAP))
    snapshot_block=$((snapshot_block + P_BLOCK))
    bytes=$((bytes + $(wc -c <"$tmp/e.l")))
    if ! cmp -s "$tmp/e.l" "$tmp/e.r"; then
      entry_fail="ref=$ref entry bytes differ ($(diff_lines "$tmp/e.l" "$tmp/e.r") canonicalised lines)"
      cp "$tmp/e.l" "$tmp/e.fail.l"; cp "$tmp/e.r" "$tmp/e.fail.r"
      break
    fi
    n_entries=$((n_entries + 1))
  done < "$tmp/s.l.refs"

  accounted=$((store_lines + host_lines + snapshot_block + index_order))

  if [[ -n "$entry_fail" ]]; then
    echo "FAIL scope=$scope $entry_fail"
    if [[ -f "$tmp/e.fail.l" ]]; then
      diff "$tmp/e.fail.l" "$tmp/e.fail.r" | head -20 || true
      rm -f "$tmp/e.fail.l" "$tmp/e.fail.r"
    fi
    fail=$((fail + 1)); continue
  fi

  entries_compared=$((entries_compared + n_entries))
  # `entries=` is EVIDENCE of the same kind as the counts, and for the same
  # reason `drift-check.sh` prints links EXAMINED beside links dangling: a scope
  # whose entry set is empty on both sides compares no bodies, and a green that
  # compared nothing must be readable as one. `snapshot-line` likewise: a `0`
  # means the remote served no stamp, which is true and expected against a
  # pre-0.3.0 image, so failing on it would make this script permanently red
  # until a deploy lands.
  echo "PASS scope=$scope entries=$n_entries bytes=$bytes raw-diff-lines=$raw store-root-lines=$store_lines host-lines=$host_lines snapshot-line=$snapshot_lines snapshot-block-lines=$snapshot_block index-order-lines=$index_order accounted-for=$accounted revision=${rev:-unknown}"
  pass=$((pass + 1))
done

echo "verify: scopes=${#SCOPES[@]} pass=$pass fail=$fail entries-compared=$entries_compared"
[[ $fail -eq 0 ]] || exit 1
