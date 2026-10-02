#!/usr/bin/env bash
# cairn-receipt — the PATH `cairn`, plus ONE telemetry row per invocation.
#
# WHY THIS EXISTS — A MEASURED ZERO, NOT A SUSPECTED ONE
# ------------------------------------------------------
# `activity.events` held **0** rows about cairn against **1,425** invocations
# proven to exist in Claude Code transcripts. So every question of the form "is a
# recalled bullet ever used" had to be answered by mining transcript prose, and
# every proxy available that way is a SPELLED one — walkable by rewording, and in
# the one case that mattered saturated by a mandate rather than by use (the
# `/resume` skill REQUIRES the report to echo what it recalled, so "a printed ref
# reappears later" fires 451/454 = 99.3% and measures compliance). A row at the
# moment of the call is a structural answer instead of a textual guess.
#
# 🔴 WHY THE WRAPPER IS AT THE *BIN SEAM* AND NOT IN `scripts/cairn-ops/read.sh`.
# The documented spelling `read.sh recall` is **93 of 1,425 invocations (~6%)** of
# real traffic; essentially all the rest is a bare `cairn recall --repo <p>`.
# Instrumenting the wrapper would therefore have measured a sixteenth of the
# population and reported it as the population. Two further facts made the bin
# seam the only honest place:
#   * `read.sh` ends in `exec cairn …`, so it HAS no after-the-call moment in
#     which to record an outcome; and
#   * its header pins "STDOUT IS THE CLIENT'S, BYTE FOR BYTE", because `/resume`,
#     `/handoff` and `/analyze-service` all diff recall output against prior
#     captures. A wrapper that captured stdout to count it would put that contract
#     at risk for the 6%.
# Every caller reaches the client through PATH, so ONE wrapper here covers
# `read.sh`, the bare spelling and anything written later — `claude/RULES.md`'s
# "one rule, one place", applied before the second copy exists rather than after.
#
# 🔴 THE CLIENT ITSELF IS THE WRONG HOME, AND THAT IS A POLICY FACT. `cairn` is a
# PUBLIC OSS repo; a host's activity-spool path and session-id variables are not
# its business, and `ZacxDev/cairn`'s own rules forbid host particulars. devrc owns
# `.local/bin/cairn` (`nix/home.nix`), so devrc can instrument it without asking
# the upstream client to carry local telemetry.
#
# CONTRACT — WHAT THIS MUST NEVER DO
# ----------------------------------
#  1. 🔴 NEVER ALTER THE CLIENT'S STDOUT, STDERR OR EXIT CODE. The client is run as
#     a child with fds INHERITED — not captured, not teed, not piped — so its
#     bytes, its `isatty()` answer and its SIGPIPE behaviour under the very common
#     `| head` form are exactly what they were without this file. The row goes to
#     the local spool, never to a stream the caller reads. `$?` is saved before
#     anything else runs and is this script's own exit status.
#  2. 🔴 FAIL-OPEN, SILENTLY. A missing, unreadable or broken `emit` must cost the
#     operator nothing: every telemetry path is `|| true`-equivalent and writes to
#     neither stream. Breaking `cairn` to record that `cairn` ran would be a
#     strictly worse trade than having no telemetry at all.
#  3. 🔴 NO NETWORK, NO INTERPRETER. `emit` is pure shell appending one O_APPEND
#     line to the spool; the collector daemon ships it out of band. This runs on the
#     `/resume` hot path, so there is no python, no jq and no HTTP here.
#  4. 🔴 NO FREE TEXT — AND THIS IS ENFORCED BY CHARSET, NOT BY INTENT. A
#     `cairn search <query>` query is operator content and must not reach
#     telemetry. The verb is read ONLY from `$1`, so a query (always a later
#     operand) is structurally unreachable; and `--scope`/`--repo` values are
#     stripped to `[A-Za-z0-9._/-]`, so a value that is secretly free text arrives
#     mangled or empty rather than verbatim. A charset is checkable; "I did not
#     mean to log it" is not.
#
# ROW SHAPE — `source=tool kind=invocation`, which is NOT a free choice
# --------------------------------------------------------------------
#     source=tool  kind=invocation  text=cairn  session=<id>
#     payload = {"tool":"cairn","outcome":"ok|error","verb":…,"scope":…,"repo":…}
#     exit_code=<client's>  duration_ms=<wall>
#
# `source='tool' kind='invocation'` is the EXISTING adoption signal for shipped
# tools, read by `scripts/session-analysis/adoption-scan.py`, and `scripts/collector/
# invocation.py::build_fields` is the shape this mirrors: the tool name goes in
# `text` as well as the payload so a consumer can group without parsing JSON.
# ⚠ `invocation.py` is deliberately NOT imported — it is not deployed on this host
# (`nix/home.nix` ships `emit`, `collector.py`, `spool_emit` and friends, not it),
# which is the same reason `scripts/claude-hooks/hook_telemetry.py` reuses the
# PATTERN and not the MODULE. Reusing the pattern across a deployment boundary is
# the honest form of "one rule, one place" here; importing would be a no-op.
#
# ⚠ WHAT THIS ROW DOES *NOT* CARRY, NAMED SO NOBODY READS IT AS COVERED. No entry
# count, no bullets-printed count, no output byte count — all three are properties
# of the client's STDOUT, and contract 1 forbids reading it. The original ranked
# item asked for them; they were dropped on an operator decision once `exec` and the
# byte-for-byte pin were measured, in exchange for 100% of traffic instead of ~6%.
# Anyone who needs those must either get the client to self-report them or accept
# the stdout risk; do not quietly add a capture here.
#
# ⚠ AND A ROW IS LOST IF THE CLIENT NEVER RETURNS. The row is written AFTER the
# child exits, because `exit_code` and `duration_ms` do not exist before then. A
# SIGKILLed or indefinitely-hung client therefore leaves no row — so these counts
# are a floor on invocations, not a census. Emitting first would invert the gap and
# lose the outcome instead; the outcome was judged worth more.
set -uo pipefail

# 🔴 SUBSTITUTED BY `nix/home.nix` AT DEPLOY TIME. The checkout carries the
# placeholder, so running this file straight out of the repo must REFUSE rather
# than guess at a client — a silent fallback to `cairn` on PATH would make this
# script invoke ITSELF, which is an unbounded recursion and not a degraded mode.
# The env override exists for the test suite, which points it at a stub.
CAIRN_RECEIPT_REAL="${CAIRN_RECEIPT_REAL_BIN:-@CAIRN_REAL@}"

# 🔴 THE SENTINEL IS ASSEMBLED FROM TWO ADJACENT LITERALS, AND THAT IS LOAD-BEARING
# RATHER THAN CUTE. `substitute` rewrites EVERY occurrence of the placeholder in
# this file. The first cut of this guard spelled it whole in a `case` pattern, so
# the DEPLOYED file compared the real store path against the real store path, the
# pattern matched, and the wrapper refused **every** invocation at exit 70 —
# `cairn` broken outright, while the checkout copy still passed its own tests.
# MEASURED on the built derivation; the regression is
# `test_cairn_receipt.py::test_a_SUBSTITUTED_wrapper_does_not_refuse`.
# ⚠ THE WIDER CLASS: a guard that must RECOGNISE a token cannot SPELL that token
# in a file something rewrites. Shell concatenates adjacent quoted strings, so the
# two halves below are one string at runtime and no contiguous match at build time.
_CAIRN_RECEIPT_PLACEHOLDER='@CAIRN''_REAL@'

# 🔴 TWO DISTINCT REFUSALS, ONE CODE. "Never deployed" and "the client is gone"
# need different remedies in the message, but both mean NO CLIENT RAN, so they
# share exit 70 — a caller branching on the status wants that single fact.
# 🔴 AND `-x` IS THE REAL PREDICATE, not the sentinel comparison: it also catches a
# substitution that produced a path that does not exist, a garbage-collected store
# path, and a value an operator set by hand with a typo. A silent fallback to
# `cairn` on PATH is NOT an option in any of these cases — this script IS that
# path, so falling back would make it exec itself forever.
if [ "$CAIRN_RECEIPT_REAL" = "$_CAIRN_RECEIPT_PLACEHOLDER" ]; then
  printf 'cairn-receipt: the real-client path was never substituted (%s).\n' \
    "$CAIRN_RECEIPT_REAL" >&2
  printf 'cairn-receipt: this script is DEPLOYED by nix/home.nix, which replaces that placeholder. To run it from the checkout, set CAIRN_RECEIPT_REAL_BIN=<path to the real cairn>.\n' >&2
  exit 70
elif [ ! -x "$CAIRN_RECEIPT_REAL" ]; then
  printf 'cairn-receipt: the real cairn client is not executable: %s\n' \
    "$CAIRN_RECEIPT_REAL" >&2
  printf 'cairn-receipt: nothing was run. If this names a /nix/store path it may have been garbage-collected — re-run the home-manager switch.\n' >&2
  exit 70
fi

#: Where `emit` lives. `nix/home.nix` deploys it at this path; the override is for
#: the suite. Absence is a NO-OP, never an error — contract 2.
CAIRN_RECEIPT_EMIT="${CAIRN_RECEIPT_EMIT_BIN:-$HOME/.config/activity-collector/emit}"

# --------------------------------------------------------------------------- #
# Field extraction. Everything here runs BEFORE the client, so it must be cheap
# and it must not consume stdin.
# --------------------------------------------------------------------------- #

#: Strip everything outside the scope/path charset, then cap. Contract 4: this is
#: what makes "no free text" a property rather than a promise.
_safe() {
  local s="${1-}"
  s="${s//[^A-Za-z0-9._\/-]/}"
  printf '%s' "${s:0:200}"
}

#: JSON string body for an ALREADY-`_safe`d value. The charset excludes `"` and
#: `\`, so no escaping is reachable — this exists so a future widening of `_safe`
#: cannot silently emit malformed JSON.
_json() {
  local s="${1-}"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

# 🔴 THE VERB IS `$1` AND ONLY `$1`, WHICH IS A SAFETY PROPERTY AS WELL AS A
# SIMPLIFICATION. cairn's CLI is `cairn <verb> [flags…]`, so the verb is argv[1].
# Scanning for "the first argument that is not a flag" instead would pick up a
# FLAG VALUE on `cairn --scope devrc recall`, and — far worse — it would make the
# first operand of `cairn search <query>` reachable. An unusual ordering here
# records an empty verb, which is the safe direction.
_verb=""
case "${1-}" in
  "" | -*) _verb="" ;;
  *) _verb="$(_safe "${1-}")" ;;
esac

# `--scope`/`--repo` in both spellings. Only these two keys are ever read, so no
# positional operand can be captured.
_scope=""
_repo=""
_prev=""
for _arg in "$@"; do
  case "$_prev" in
    --scope) _scope="$(_safe "$_arg")" ;;
    --repo) _repo="$(_safe "$_arg")" ;;
  esac
  case "$_arg" in
    --scope=*) _scope="$(_safe "${_arg#--scope=}")" ;;
    --repo=*) _repo="$(_safe "${_arg#--repo=}")" ;;
  esac
  _prev="$_arg"
done

#: Wall clock in ms, or "" when unavailable. 🔴 `$EPOCHREALTIME` is rendered with
#: the LOCALE's decimal separator, so a `.`-only parse returns a wrong number
#: under e.g. `LC_NUMERIC=de_DE`. Both separators are handled here rather than
#: forcing `LC_ALL=C`, because this process's locale is INHERITED BY THE CLIENT and
#: changing it could change the client's own output — which contract 1 forbids.
_now_ms() {
  local t="${EPOCHREALTIME-}" sec frac
  [ -n "$t" ] || { printf ''; return 0; }
  case "$t" in
    *.*) sec="${t%%.*}"; frac="${t#*.}" ;;
    *,*) sec="${t%%,*}"; frac="${t#*,}" ;;
    *) sec="$t"; frac="000000" ;;
  esac
  frac="${frac}000000"
  frac="${frac:0:6}"
  # `10#` so a leading zero is not read as octal.
  printf '%s' "$(( 10#${sec:-0} * 1000 + 10#${frac} / 1000 ))" 2>/dev/null || printf ''
}

_t0="$(_now_ms)"

# --------------------------------------------------------------------------- #
# 🔴 THE CALL. fds are INHERITED — no pipe, no capture, no redirection. This line
# is the whole of contract 1.
# --------------------------------------------------------------------------- #
"$CAIRN_RECEIPT_REAL" "$@"
_rc=$?

# --------------------------------------------------------------------------- #
# The receipt. Everything from here is best-effort and must not touch $_rc.
# --------------------------------------------------------------------------- #
_emit_receipt() {
  [ -n "${CAIRN_RECEIPT_DISABLE-}" ] && return 0
  # `-x` rather than `-f`: a non-executable or absent emit is the telemetry-off
  # no-op, and a DIRECTORY at that path must not become a failed exec.
  [ -x "$CAIRN_RECEIPT_EMIT" ] || return 0

  local t1 dur outcome session payload
  t1="$(_now_ms)"
  dur=""
  if [ -n "$_t0" ] && [ -n "$t1" ]; then
    dur="$(( t1 - _t0 ))"
    # A non-monotonic clock (NTP step, suspend) can make this negative; a negative
    # duration is worse than none because it reads as a measurement.
    [ "$dur" -ge 0 ] 2>/dev/null || dur=""
  fi

  # `outcome` is COARSE on purpose: the exact code is already in `exit_code`, and
  # a second encoding of it would be a field that can disagree with itself.
  if [ "$_rc" -eq 0 ]; then outcome="ok"; else outcome="error"; fi

  # Same precedence as `scripts/lib/clawgate_handoff.sh`: an opencode run is not a
  # Claude Code session and must not borrow its id.
  session="${OPENCODE_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}"

  payload="$(printf '{"tool":"cairn","outcome":"%s","verb":"%s","scope":"%s","repo":"%s"}' \
    "$(_json "$outcome")" "$(_json "$_verb")" "$(_json "$_scope")" "$(_json "$_repo")")"

  set -- source=tool kind=invocation \
    "b64:text=cairn" \
    "b64:payload=$payload" \
    "exit_code=$_rc"
  [ -n "$dur" ] && set -- "$@" "duration_ms=$dur"
  [ -n "$session" ] && set -- "$@" "b64:session=$session"

  # 🔴 BOTH STREAMS DISCARDED AND THE STATUS SWALLOWED. `emit` is bounded
  # best-effort itself, but this script must not acquire a way to fail or to speak.
  "$CAIRN_RECEIPT_EMIT" "$@" >/dev/null 2>&1 || true
  return 0
}

_emit_receipt || true

exit "$_rc"
