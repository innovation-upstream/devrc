#!/usr/bin/env bash
# Unit tests for the civitai `release` wrapper in ../../.zshrc.
# Extracts just the `_release_run` synchronous core (between the marker comments)
# and drives it with fake `git`/`npm`/`notify-send` binaries on $PATH, asserting
# ordering + arguments via a shared order-log the stubs append to.
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
BIN="$SANDBOX/bin"
mkdir -p "$BIN"

cat > "$BIN/git" <<'EOF'
#!/usr/bin/env bash
# `git rev-parse --show-toplevel` → the fake repo root (guard input).
if [ "$1" = "rev-parse" ]; then echo "${FAKE_REPO_ROOT:-}"; exit 0; fi
printf 'git %s\n' "$*" >> "$ORDER_LOG"   # record fetch (and anything else)
exit 0
EOF

cat > "$BIN/npm" <<'EOF'
#!/usr/bin/env bash
printf 'npm %s\n' "$*" >> "$ORDER_LOG"
exit "${NPM_RC:-0}"
EOF

cat > "$BIN/notify-send" <<'EOF'
#!/usr/bin/env bash
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

echo
if [ "$FAIL" -eq 0 ]; then echo "ALL PASS"; exit 0; else echo "FAILURES"; exit 1; fi
