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

if [ "${1:-}" = "--uninstall" ]; then
  current="$(git config --global --get core.hooksPath || true)"
  if [ "$current" = "$DIR" ]; then
    git config --global --unset core.hooksPath
    echo "uninstalled: global core.hooksPath cleared (was $DIR)"
  else
    echo "nothing to do: global core.hooksPath is '${current:-<unset>}', not '$DIR'"
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
