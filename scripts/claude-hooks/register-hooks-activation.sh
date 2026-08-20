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
#      / "removed duplicate hook registrations: …" / "… no change") is echoed
#      line by line, prefixed. A silent activation step is how the original
#      failure went unnoticed for a full deploy cycle. 🔴 The capture is
#      `2>&1`, which is load-bearing: the registrar sends its WARNINGs to
#      stderr — a rejected $DEVRC_HOOK_PYTHON, a managed hook command in a
#      shape it cannot pin, a duplicate registration it declined to remove —
#      and this is the only thing that puts them in the switch log. Never
#      split the streams here or those warnings become invisible again.
#   3. IT DECIDES NOTHING ABOUT settings.json. Every read/merge/write belongs
#      to the registrar. That registrar has THREE surfaces of different widths:
#      an APPEND surface (its own command tables, never touching a hook it does
#      not own, so the clawgate Stop hook that drives remote approval survives);
#      a wide REWRITE surface that normalises the INTERPRETER TOKEN of a
#      devrc-managed hook command to an absolute /nix/store python; and a
#      narrow DE-DUP surface that deletes a second registration of a script it
#      owns on the same event under the same matcher, keeping the first. Read
#      its module docstring before changing any of them; the claim this file
#      used to make — "strictly APPEND-ONLY" — is no longer true and deleting
#      the rewrite path would silently reopen the failure it closes (a hook
#      firing mid-switch dies with `python3: command not found`, and bash-guard
#      fails OPEN when it does). The de-dup path is what makes a
#      `home-manager rollback` — which re-runs an OLDER registrar that cannot
#      see the pinned spelling and appends a second copy of everything —
#      recoverable rather than permanent. This wrapper adds no second writer —
#      it must never grow one.
#
#      🔴 The interpreter the registrar writes is derived from its OWN
#      sys.executable, so the `[python]` argument below is load-bearing beyond
#      "something that can run python": home.nix passes
#      ${pkgs.python312}/bin/python3, and that store path is what ends up in
#      settings.json. Passing a blinking profile path here would pin the hooks
#      to a blinking path.
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
