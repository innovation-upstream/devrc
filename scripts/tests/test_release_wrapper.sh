#!/usr/bin/env bash
# Unit tests for the civitai `release` wrapper in ../../.zshrc.
# Extracts just the `_release_run` synchronous core (between the marker comments)
# and drives it with fake `git`/`npm`/`notify-send` binaries on $PATH, asserting
# ordering + arguments via a shared order-log the stubs append to.
#
# NOT unit-tested here (inherently environment-bound, verified by reading the
# code + `zsh -n`): the `/dev/tty` completion write and the `</dev/null &!`
# stdin-detached backgrounding in `release` — both need a real controlling tty /
# job-control shell. What IS covered: the civitai-repo guard, fetch→release
# ordering, the low/critical notify branches (proving the failure branch is
# taken and carries the rc), the concurrency lock (a second concurrent run
# refuses without calling npm), and that the lock is released after a run so a
# later run proceeds.
#   run: bash scripts/tests/test_release_wrapper.sh
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ZSHRC="$HERE/../../.zshrc"

FAIL=0
pass(){ printf '  ok  %s\n' "$1"; }
fail(){ printf '  FAIL %s\n' "$1"; FAIL=1; }
# assert `$2` (multiline) contains a line exactly equal to `$3`
has(){ grep -qxF "$3" <<<"$2" && pass "$1" || { fail "$1"; printf '     missing line: [%s] in\n[%s]\n' "$3" "$2"; }; }
# assert `$2` (multiline) does NOT contain a line exactly equal to `$3`
lacks(){ grep -qxF "$3" <<<"$2" && { fail "$1"; printf '     unexpected line: [%s]\n' "$3"; } || pass "$1"; }
# assert `$2` contains substring `$3`
contains(){ case "$2" in *"$3"*) pass "$1";; *) fail "$1"; printf '     [%s] missing substring [%s]\n' "$2" "$3";; esac; }

# ── extract & source only the _release_run function ──────────────────────
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
FN="$SANDBOX/release_fn.sh"
sed -n '/# >>> _release_run >>>/,/# <<< _release_run <<</p' "$ZSHRC" > "$FN"
if ! grep -q '_release_run()' "$FN"; then
  echo "FAIL: could not extract _release_run from $ZSHRC"; exit 1
fi
# shellcheck source=/dev/null
source "$FN"

# ── fake binaries earlier on PATH ────────────────────────────────────────
# 🔴 `#!/bin/sh`, NOT `#!/usr/bin/env bash`. This script had never been run by
# any gate; the first run of it inside `nix build .#checks…pytests` was RED,
# because the sandbox has no `/usr/bin/env` and all three stubs failed to exec.
# The damage was not merely red: case 1 ("outside a civitai repo → refuses")
# went GREEN for the wrong reason — the `git` stub could not run, so
# `rev-parse --show-toplevel` produced nothing and the guard refused for a
# reason the test was not measuring. Same trap `scripts/testlib/mockbin.py`
# exists to stop, in the one file that could not import it (it is bash).
BIN="$SANDBOX/bin"
mkdir -p "$BIN"

cat > "$BIN/git" <<'EOF'
#!/bin/sh
# `git rev-parse --show-toplevel` → the fake repo root (guard input).
if [ "$1" = "rev-parse" ]; then echo "${FAKE_REPO_ROOT:-}"; exit 0; fi
printf 'git %s\n' "$*" >> "$ORDER_LOG"   # record fetch (and anything else)
exit 0
EOF

cat > "$BIN/npm" <<'EOF'
#!/bin/sh
printf 'npm %s\n' "$*" >> "$ORDER_LOG"
exit "${NPM_RC:-0}"
EOF

cat > "$BIN/notify-send" <<'EOF'
#!/bin/sh
printf 'notify %s\n' "$*" >> "$ORDER_LOG"
exit 0
EOF

chmod +x "$BIN/git" "$BIN/npm" "$BIN/notify-send"
export PATH="$BIN:$PATH"
export HOME="$SANDBOX/home"   # isolate the ~/.cache/civitai-release log dir
mkdir -p "$HOME"

reset_log(){ ORDER_LOG="$SANDBOX/order.log"; export ORDER_LOG; : > "$ORDER_LOG"; }

echo "== case 1: outside a civitai repo → refuses, never calls npm =="
reset_log
export FAKE_REPO_ROOT="$SANDBOX/some/other-project"
NPM_RC=0 _release_run >/dev/null 2>&1; rc=$?
[ "$rc" -ne 0 ] && pass "returns non-zero outside civitai" || fail "returns non-zero outside civitai"
OUT=$(cat "$ORDER_LOG")
lacks "npm not invoked outside civitai" "$OUT" "npm run release"
lacks "notify not fired outside civitai" "$OUT" "notify -u low ✅ civitai release complete"

echo "== case 2: inside civitai, npm exits 0 → fetch→release order, low notify, rc 0 =="
reset_log
export FAKE_REPO_ROOT="$SANDBOX/civitai"
NPM_RC=0 _release_run >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && pass "returns 0 on success" || fail "returns 0 on success (got $rc)"
OUT=$(cat "$ORDER_LOG")
# ordering: git fetch must precede npm run release
FIRST=$(head -1 "$ORDER_LOG"); SECOND=$(sed -n '2p' "$ORDER_LOG")
has  "git fetch is first"          "$FIRST"  "git fetch origin --quiet"
has  "npm run release is second"   "$SECOND" "npm run release"
has  "low-urgency notify fired"    "$OUT"    "notify -u low ✅ civitai release complete"

echo "== case 3: inside civitai, npm exits 3 → critical notify w/ rc, returns rc =="
reset_log
export FAKE_REPO_ROOT="$SANDBOX/civitai"
NPM_RC=3 _release_run >/dev/null 2>&1; rc=$?
[ "$rc" -eq 3 ] && pass "propagates npm's non-zero rc (3)" || fail "propagates npm rc (got $rc)"
OUT=$(cat "$ORDER_LOG")
has      "npm was invoked"            "$OUT" "npm run release"
contains "critical-urgency notify"   "$OUT" "notify -u critical"
contains "notify mentions rc=3"       "$OUT" "rc=3"
lacks    "low notify NOT fired on failure" "$OUT" "notify -u low ✅ civitai release complete"

# lock dir lives beside the log (default: $HOME/.cache/civitai-release/.lock)
LOCK="$HOME/.cache/civitai-release/.lock"

echo "== case 4: lock is released after a run (cases 2/3 already ran) =="
[ ! -d "$LOCK" ] && pass "lock not held after a completed run" || fail "lock leaked after run: $LOCK"

echo "== case 5: lock already held → refuses, never calls npm =="
reset_log
export FAKE_REPO_ROOT="$SANDBOX/civitai"
mkdir -p "$LOCK"   # simulate a concurrent release holding the lock
NPM_RC=0 _release_run >/dev/null 2>&1; rc=$?
[ "$rc" -ne 0 ] && pass "returns non-zero when lock is held" || fail "returns non-zero when lock is held (got $rc)"
OUT=$(cat "$ORDER_LOG")
lacks "npm not invoked while lock held" "$OUT" "npm run release"
[ -d "$LOCK" ] && pass "pre-existing lock left intact (not stolen)" || fail "pre-existing lock was removed"
rmdir "$LOCK" 2>/dev/null

echo "== case 6: after the lock is freed, a subsequent run proceeds =="
reset_log
export FAKE_REPO_ROOT="$SANDBOX/civitai"
NPM_RC=0 _release_run >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && pass "run proceeds once lock is free" || fail "run proceeds once lock is free (got $rc)"
OUT=$(cat "$ORDER_LOG")
has "npm invoked on the follow-up run" "$OUT" "npm run release"
[ ! -d "$LOCK" ] && pass "lock released again after follow-up run" || fail "lock leaked after follow-up run"

echo
if [ "$FAIL" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILURES"; exit 1; fi
