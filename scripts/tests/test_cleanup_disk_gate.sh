#!/usr/bin/env bash
# Regression guard for scripts/cleanup-disk.sh's --apply gate.
#
# WHY THIS EXISTS. The script is `allow`-rated by the opencode ledger
# (scripts/opencode/opencode.jsonc) while two of its own commands are `deny`-rated:
#
#   bash <anywhere>/cleanup-disk.sh                          -> allow [*]
#   rm -rf /home/zach/.local/share/NuGet                     -> deny  [*rm -rf /*]
#   rm -rf /home/zach/.local/share/dp-prod-ssr-regression-*  -> deny  [*rm -rf /*]
#
# The rules match on the command STRING and guard_core.py cannot see inside an
# invoked script file, so a tracked script launders whatever it runs. The gate
# does not close that (`--apply` is allow-rated too — see the file's own
# comment); it makes an ACCIDENTAL invocation inert. This guard pins THAT
# property, which is the only one actually claimed.
#
# 🔴 The assertion is BEHAVIOURAL, not a grep. Audit round 2 was burned by a
# harness that logged to stderr, which the script's `2>/dev/null` swallowed —
# it reported "sudo NOT invoked" for a script that DID invoke it. So the stubs
# below log to a FILE, and the POSITIVE CONTROL runs first: if --apply does not
# produce calls, the harness is wired to nothing and a zero from the bare run
# proves nothing. A zero is only meaningful beside a non-zero from the same
# instrument.
set -uo pipefail

# 🔴 `CDPATH=` and `>/dev/null`: with CDPATH set in the environment (it is, on
# this host), `cd` ECHOES its target directory, so a bare `$(cd … && pwd)`
# captures TWO lines and $ROOT becomes the path twice, newline-separated.
# Every invocation then dies "No such file or directory" -> rc 127, which
# reads as "the script under test failed" rather than "the harness is
# broken". Measured here; it is why this guard was red on its own first run.
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
SCRIPT="$ROOT/scripts/cleanup-disk.sh"
FAILED=0

fail() { echo "  FAIL: $*"; FAILED=1; }
pass() { echo "  ok: $*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LOG="$TMP/calls.log"

# Stub every tool that mutates state. Logging to a FILE is load-bearing.
#
# 🔴 `#!/bin/sh`, NOT `#!/usr/bin/env bash`. `/usr/bin/env` exists on the NixOS
# dev host and does NOT exist in the nix build sandbox — the tier that actually
# gates merges. `patchShebangs` rewrites shebangs in the SOURCE tree; it cannot
# touch a file a test writes at RUNTIME, so a stub written with an env shebang
# is green on the tier people look at and red only on the tier that blocks the
# merge. `scripts/tests/test_runtime_shebangs.py` is the repo-wide guard for
# exactly this, and it caught this file on its first gate run. Same reasoning and
# same fix as `test_release_wrapper.sh`. Bodies below must stay POSIX sh.
for t in rm find go pnpm sudo journalctl df tail awk; do
  printf '#!/bin/sh\necho "%s $*" >> "%s"\nexit 0\n' "$t" "$LOG" > "$TMP/$t"
  chmod +x "$TMP/$t"
done

# 🔴 WHAT THE RESTRICTED PATH DOES AND DOES NOT DO. The script is invoked with
# PATH set to the stub dir alone. That stops a command resolved THROUGH PATH from
# reaching a real binary. It does NOT mean "no real binary is reachable" — an
# earlier version of this comment said exactly that, and audit round 5 falsified
# it with one line: `/home/zach/.nix-profile/bin/rm -f "$CANARY"` above the gate
# bypasses PATH entirely, REALLY DELETED the canary during the guard run, as the
# operator, and the guard still printed "all checks passed".
#
# 🔴 Nor does the log see everything. Only a STUBBED command writes a line, so
# `destructive_calls()` counts stubbed invocations, not "any command the script
# invokes" — another claim an earlier comment here made and round 5 falsified.
# The script's own dominant idiom (`cmd … && echo … || echo …`, `… || true`)
# ABSORBS a 127, so an unstubbed command neither logs nor trips `set -e` nor
# moves the rc assertion. Shell redirection (`: > file`) is invisible for the
# same reason — no external binary is involved at all.
#
# What this guard therefore pins, honestly: for the commands `cleanup-disk.sh`
# actually invokes through PATH — which the STUB DRIFT CHECK below forces the
# stub list to cover — a bare invocation performs none of them. That is the
# property the --apply gate claims. It is not a proof that a bare invocation is
# inert against an adversarial edit, and it must not be read as one.
# 🔴 BASH_BIN is resolved to an ABSOLUTE path ONCE, before PATH is restricted.
# A prefix assignment (`PATH=… bash …`) performs the command lookup with the NEW
# PATH, so the interpreter itself becomes unfindable and every invocation dies
# `command not found: bash` — rc 127, which reads as "the script failed" rather
# than "the harness broke".
BASH_BIN="$(command -v bash)"
[ -x "$BASH_BIN" ] || { echo "  FAIL: cannot resolve bash"; exit 1; }

run_script() { PATH="$TMP" "$BASH_BIN" "$SCRIPT" "$@" >/dev/null 2>&1; }

# 🔴 `grep -c` PRINTS "0" and EXITS 1 when there are no matches, so the obvious
# `grep -c … || echo 0` emits "0\n0" and every downstream `[ "$n" -eq 0 ]`
# explodes with "integer expected". Caught by this guard's own first run.
# Capture the count, discard the status.
#
# 🔴 This counts LOGGED lines, and only a STUBBED command logs. It was previously
# `^(rm|find|go|pnpm) `, four hardcoded names; counting every line removed that
# regex, but round 5 showed the enumeration merely MOVED into the stub list —
# an unstubbed command still writes nothing and is still invisible. The honest
# statement is therefore: this counts invocations of the commands the stub list
# covers. What makes that adequate rather than arbitrary is the STUB DRIFT CHECK
# below, which fails if `cleanup-disk.sh` grows a PATH-resolved command the stub
# list does not carry — so the two cannot silently drift apart, which is the
# thing that made the old regex rot.
destructive_calls() {
  [ -s "$LOG" ] || { echo 0; return; }
  local n
  n="$(wc -l < "$LOG" 2>/dev/null)" || true
  echo "${n:-0}"
}
sudo_calls() {
  local n
  n="$(grep -cE '^sudo ' "$LOG" 2>/dev/null)" || true
  echo "${n:-0}"
}

# --------------------------------------------------------------------------- #
# STUB DRIFT CHECK — the structural half, and the reason the count above is not
# just another enumeration.
#
# 🔴 Round 5's finding: replacing the `^(rm|find|go|pnpm) ` regex with "count
# every logged line" did NOT make the detector structural — it moved the
# enumeration into the stub list. A sixth cleanup step using `rmdir`,
# `truncate` or `nix-collect-garbage` would be unstubbed, would log nothing,
# and (because the script's own idiom `cmd … || echo …` absorbs a 127) would
# neither trip `set -e` nor move the rc assertion. The guard would stay green
# while a bare invocation destroyed data — the exact property its title claims.
#
# So: derive the PATH-resolved commands the script invokes and require the stub
# list to cover them. Drift then fails HERE, naming the command, instead of
# silently widening the blind spot. This is deliberately over-inclusive — a name
# that is not really a command costs one stub entry, whereas a missed one costs
# the guard's meaning.
#
# ⚠ It cannot see a command invoked by ABSOLUTE PATH (that bypasses PATH, and
# round 5 showed such a mutant really deletes files during the run) or a pure
# shell redirection. Both are named in the header; neither is closed here.
STUBBED=" rm find go pnpm sudo journalctl df tail awk "
# 🔴 One line, deliberately. Written across three lines the embedded NEWLINES
# break the `*" $word "*` match — a word adjacent to a line break is not
# surrounded by spaces, so `exit` was reported as drift on this check's first
# run. Keep it single-line, or normalise the whitespace before matching.
SHELL_WORDS=" if then else elif fi for do done while case esac echo printf exit return local set unset export cd test true false read eval exec shift break continue function in "
drift=""
while read -r word; do
  [ -n "$word" ] || continue
  case "$STUBBED" in *" $word "*) continue;; esac
  case "$SHELL_WORDS" in *" $word "*) continue;; esac
  drift="$drift $word"
done <<EOF
$(grep -oE '^[[:space:]]*[a-z][a-z0-9_-]*' "$SCRIPT" | tr -d '[:blank:]' | sort -u)
EOF
if [ -z "$drift" ]; then
  pass "stub list covers every PATH-resolved command in cleanup-disk.sh"
else
  fail "cleanup-disk.sh invokes command(s) the stub list does not cover:$drift
       Add them to the stub loop, or this guard is blind to whatever they do."
fi

echo "== 1. POSITIVE CONTROL: --apply must actually invoke the destructive tools =="
: > "$LOG"
run_script --apply
n_apply="$(destructive_calls)"
if [ "$n_apply" -gt 0 ]; then
  pass "--apply invoked $n_apply destructive command(s) — the harness CAN observe calls"
else
  fail "--apply invoked NOTHING. The harness is wired to nothing, so every zero below is vacuous."
fi

echo "== 2. THE GATE: a bare invocation must delete nothing =="
: > "$LOG"
run_script
rc=$?
n_bare="$(destructive_calls)"
[ "$n_bare" -eq 0 ] && pass "bare run made 0 destructive calls" \
  || fail "bare run made $n_bare destructive call(s) — the gate is bypassed"
[ "$rc" -eq 0 ] && pass "bare run exits 0" || fail "bare run exited $rc"

echo "== 3. NEAR-MISS ARGS must fail SAFE (dry run), never fall through to deletion =="
for arg in "--apply=1" "-apply" "--APPLY" "x--applyx" "--help" "" "foo bar"; do
  : > "$LOG"
  # shellcheck disable=SC2086
  run_script $arg
  n="$(destructive_calls)"
  [ "$n" -eq 0 ] && pass "arg '${arg:-<none>}' -> dry run" \
    || fail "arg '${arg:-<none>}' made $n destructive call(s)"
done

echo "== 4. No sudo is invoked, with or without --apply =="
for mode in "" "--apply"; do
  : > "$LOG"
  # shellcheck disable=SC2086
  run_script $mode
  n="$(sudo_calls)"
  [ "$n" -eq 0 ] && pass "mode '${mode:-bare}' invoked sudo 0 times" \
    || fail "mode '${mode:-bare}' invoked sudo $n time(s)"
done

echo "== 5. An env var alone must NOT arm the gate =="
for env_try in "APPLY=1" "APPLY=yes" "_a=--apply"; do
  : > "$LOG"
  env "$env_try" PATH="$TMP" "$BASH_BIN" "$SCRIPT" >/dev/null 2>&1
  n="$(destructive_calls)"
  [ "$n" -eq 0 ] && pass "env '$env_try' did not arm the gate" \
    || fail "env '$env_try' armed the gate ($n destructive call(s))"
done

# 🔴 DO NOT print `RESULT: PASS (exit=0)` here. That grammar is RESERVED: it has
# exactly one writer, `run-tests.sh:150` behind an EXIT trap, and that
# single-writer property is what CLAUDE.md leans on when a pipe destroys the
# exit status. This file emitted it at column 0 INSIDE the runner's stdout,
# creating a second writer — and `test_gate_exit_truthfulness.py` takes the
# FIRST regex match, so it read this test's forged PASS while the runner's own
# trailing line said FAIL. A red run reported green through the gate's own
# truth-telling channel. Exit status alone is what `_run_shell_test_body`
# consumes; the wording below is deliberately outside the reserved grammar.
if [ "$FAILED" -eq 0 ]; then
  echo "cleanup-disk gate guard: all checks passed"; exit 0
else
  echo "cleanup-disk gate guard: FAILURES above"; exit 1
fi
