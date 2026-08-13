#!/usr/bin/env bash
# The home-manager ACTIVATION wrapper around register-nudge-hook.py.
#
#   usage: bash register-hooks-activation.sh [registrar-path] [python]
#          defaults: ~/.claude/hooks/register-nudge-hook.py, python3 on PATH
#
# WHY THIS FILE EXISTS AT ALL, rather than four lines inlined in home.nix:
# the bug it closes (#452 shipped a Stop hook that sat INERT on both hosts
# because nothing ever registered it) was invisible precisely because the
# DELIVERY seam had no test. An inline nix string is testable only by scraping
# home.nix; a real script is driven by tests/test_registrar_activation.py with
# the exact bytes that ship.
#
# CONTRACT — all three are pinned by that suite:
#
#   1. 🔴 IT ALWAYS EXITS 0. home-manager activation runs under `set -eu -o
#      pipefail`, so ANY non-zero status here aborts the whole switch. An
#      unregistered hook is an inconvenience; a switch that will not complete
#      blocks every other change on that host, and this repo already has a
#      "failed switch" incident class (CLAUDE.md → Git discipline). Every
#      failure path therefore warns on stderr and returns 0.
#   2. IT SAYS WHAT IT DID. The registrar's own report ("registered hooks: …"
#      / "… no change") is echoed line by line, prefixed. A silent activation
#      step is how the original failure went unnoticed for a full deploy cycle.
#   3. IT DECIDES NOTHING ABOUT settings.json. Every read/merge/write belongs
#      to the registrar, which is strictly APPEND-ONLY and never clobbers a
#      hook it does not own (the clawgate Stop hook drives remote approval;
#      losing it would silently break that). This wrapper adds no second
#      writer — it must never grow one.
#
# The registrar path defaults to the DEPLOYED copy rather than the store
# source deliberately: if the `home.file` entry for it ever stops landing,
# activation says so out loud instead of quietly working from the store, which
# is the same "delivery succeeded, feature absent" shape as the original bug.
set -u

PREFIX="claude-hooks:"
reg="${1:-$HOME/.claude/hooks/register-nudge-hook.py}"
py="${2:-python3}"

emit() { # $1 = stream marker (1|2), $2… = text
  if [ "$1" = "2" ]; then
    shift
    printf '%s %s\n' "$PREFIX" "$*" >&2
  else
    shift
    printf '%s %s\n' "$PREFIX" "$*"
  fi
}

# Echo a captured block line by line so multi-line registrar output stays
# attributable to this step in the switch log.
relay() { # $1 = stream marker, $2 = text block
  [ -n "$2" ] || return 0
  printf '%s\n' "$2" | while IFS= read -r line; do
    emit "$1" "  $line"
  done
}

if [ ! -f "$reg" ]; then
  emit 2 "WARNING registrar not found at $reg — Claude Code hooks were NOT registered in ~/.claude/settings.json. Check its home.file entry in nix/home.nix, then re-switch."
  exit 0
fi

out="$("$py" "$reg" 2>&1)"
rc=$?

if [ "$rc" -ne 0 ]; then
  emit 2 "WARNING registrar exited $rc — ~/.claude/settings.json was left as it was, so some hooks may be unregistered. The switch continues; fix and re-run: $py $reg"
  relay 2 "$out"
  exit 0
fi

relay 1 "$out"
exit 0
