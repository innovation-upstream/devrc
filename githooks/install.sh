#!/usr/bin/env bash
#
# install.sh — point GLOBAL git at devrc's tracked githooks/ dir.
#
# Sets `git config --global core.hooksPath <this dir>` so the version-controlled
# pre-push dispatcher runs for every repo that does NOT override core.hooksPath
# locally. It composes with repo-local .git/hooks/pre-push (chains to it first).
#
# The AUDIT flag defaults to SHADOW (installing changes nothing about the audit
# side of your push UX until you flip AUDIT_ON_PUSH=on). The TEST GATE, however,
# defaults to ON *in the devrc repo only* — devrc pushes will run the Python
# suite and block on a genuine failure (TESTS_ON_PUSH; DEVRC_SKIP_TESTS=1 to
# override a single push). It is a no-op in every other repo.
# Disable everything with: githooks/install.sh --uninstall
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- #782: the test gate occupies a push, so the push needs SSH keepalives ----
# 🔴 MEASURED 2026-08-26, twice independently. github.com closes an IDLE
# `git-receive-pack` session after ~360 s (361 s in both runs; the second also
# gave `rc=255` and `Connection to github.com closed by remote host.`). git
# opens AND negotiates the connection BEFORE it runs `pre-push` — measured, not
# inferred: `ssh-launch 04:12:04Z` then `hook START 04:12:05Z` — so the
# connection sits idle for the hook's whole runtime.
#
# `tests-on-push.sh` is exactly such a hook. Once the suite passes ~360 s the
# connection is already gone when git tries to send the pack, and the push dies
# with SIGPIPE:
#
#     hook sleeps 420 s, no keepalive -> push rc=141, branch ABSENT
#     hook sleeps 420 s, keepalive=30 -> push rc=0,   branch CREATED
#
# Same hook duration, one variable. This is NOT flaky — it is a hard threshold,
# and it gets hit more often the longer the suite grows.
#
# 🔴 IT PRESENTS AS A NETWORK FLAKE, WHICH IS WHY IT WENT UNDIAGNOSED. The hook
# prints its own `✅ devrc test suite passed.` AFTER the connection has already
# died, so the screen reads as success; only `git ls-remote` tells the truth.
#
# WHY HERE AND NOT IN ~/.ssh/config: that file is a per-host, unmanaged plain
# file (not a home-manager symlink), so a fix placed there protects one machine
# and never ships. This installer already writes global git config one line
# below, and the population that runs it is exactly the population exposed to
# the bug — the hook that creates the hazard installs the mitigation.
#
# 🔴 THE INTERVAL MUST STAY WELL UNDER THE ~360 s CLOSE. A keepalive longer than
# the server's idle timeout is not a weaker fix, it is NO fix — pinned by
# scripts/tests/test_push_keepalive.py.
KEEPALIVE_OPT="ServerAliveInterval=30"
KEEPALIVE_SSH="ssh -o $KEEPALIVE_OPT"

if [ "${1:-}" = "--uninstall" ]; then
  current="$(git config --global --get core.hooksPath || true)"
  if [ "$current" = "$DIR" ]; then
    git config --global --unset core.hooksPath
    echo "uninstalled: global core.hooksPath cleared (was $DIR)"
  else
    echo "nothing to do: global core.hooksPath is '${current:-<unset>}', not '$DIR'"
  fi
  # Only remove the sshCommand WE wrote. Someone else's belongs to them.
  cur_ssh="$(git config --global --get core.sshCommand || true)"
  if [ "$cur_ssh" = "$KEEPALIVE_SSH" ]; then
    git config --global --unset core.sshCommand
    echo "uninstalled: global core.sshCommand cleared (was $KEEPALIVE_SSH)"
  elif [ -n "$cur_ssh" ]; then
    echo "left alone: global core.sshCommand is '$cur_ssh', which this installer did not write"
  fi
  exit 0
fi

chmod +x "$DIR/pre-push" "$DIR/audit-on-push.sh" "$DIR/tests-on-push.sh" 2>/dev/null || true

prev="$(git config --global --get core.hooksPath || true)"
if [ -n "$prev" ] && [ "$prev" != "$DIR" ]; then
  echo "WARNING: global core.hooksPath was already set to: $prev"
  echo "         overwriting with: $DIR"
  echo "         (your previous global hooks dir will no longer run; move its hooks here if needed)"
fi
git config --global core.hooksPath "$DIR"

# --- #782 mitigation: keep the push's SSH connection alive across the gate ----
# Three cases, and the middle one is the reason this is not a bare `git config`:
# clobbering somebody's existing sshCommand (a jump host, a pinned key, a
# wrapper) to fix our own hook would be a worse bug than the one being fixed.
prev_ssh="$(git config --global --get core.sshCommand || true)"
if [ -z "$prev_ssh" ]; then
  git config --global core.sshCommand "$KEEPALIVE_SSH"
  echo "installed: global core.sshCommand -> $KEEPALIVE_SSH (keeps a push alive across the test gate; #782)"
elif [ "$prev_ssh" = "$KEEPALIVE_SSH" ] || [[ "$prev_ssh" == *"ServerAliveInterval"* ]]; then
  : # already carries a keepalive — leave whatever they chose alone
else
  echo "WARNING: global core.sshCommand is already set and carries NO keepalive:"
  echo "           $prev_ssh"
  echo "         Leaving it alone rather than clobbering it — but a devrc push whose"
  echo "         test gate runs longer than ~360s WILL die with SIGPIPE (#782)."
  echo "         Add the option yourself, e.g.:"
  echo "           git config --global core.sshCommand '$prev_ssh -o $KEEPALIVE_OPT'"
fi

# Seed the flag config file at shadow if it doesn't exist yet.
CONF="$HOME/.claude/audit-on-push.env"
if [ ! -f "$CONF" ]; then
  mkdir -p "$(dirname "$CONF")"
  cp "$DIR/audit-on-push.env.example" "$CONF" 2>/dev/null || true
  echo "seeded $CONF (AUDIT_ON_PUSH=shadow — sends nothing until you flip it to 'on')"
fi

echo "installed: global core.hooksPath -> $DIR"
echo "active hooks: $(ls "$DIR" | grep -vE '\.(sh|md|example)$' | tr '\n' ' ')"
echo
echo "Audit flag is SHADOW by default (logs what it WOULD send, sends nothing)."
echo "  watch shadow decisions: tail -f ~/.claude/audit-on-push.log"
echo "  go live:  echo 'AUDIT_ON_PUSH=on' >> ~/.claude/audit-on-push.env"
echo "  back off: set AUDIT_ON_PUSH=off in ~/.claude/audit-on-push.env"
echo
echo "Test gate is ON by default IN DEVRC ONLY (devrc pushes run the Python"
echo "suite + block on a genuine failure; no-op elsewhere)."
echo "  warn-only: set TESTS_ON_PUSH=shadow in ~/.claude/audit-on-push.env"
echo "  disable:   set TESTS_ON_PUSH=off   in ~/.claude/audit-on-push.env"
echo "  skip one push: DEVRC_SKIP_TESTS=1 git push …"
echo
echo "  uninstall global hook: $DIR/install.sh --uninstall"
