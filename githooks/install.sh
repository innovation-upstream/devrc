#!/usr/bin/env bash
#
# install.sh — point git at devrc's tracked githooks/ dir.
#
# Sets `core.hooksPath` to this directory so the version-controlled pre-push
# dispatcher runs. It composes with a repo-local .git/hooks/pre-push (chains to
# it first).
#
# 🔴 TWO SCOPES, AND THE GLOBAL ONE IS DEAD ON A HOME-MANAGER HOST. This script
# used to do `git config --global core.hooksPath` and nothing else. On both of
# this repo's hosts `~/.config/git/config` is a **nix-store symlink** written by
# `programs.git` in nix/home.nix, so that command cannot succeed:
#
#   error: could not lock config file /home/zach/.config/git/config:
#          Read-only file system
#
# and `set -euo pipefail` aborted the script on it. MEASURED 2026-08-21: the
# workbench had NO pre-push hook installed at all — `core.hooksPath` was set
# repo-locally to `.git/hooks`, which held 14 files, every one of them a
# `*.sample`. The blocking test gate that four separate comments in this repo
# describe as running "on every push" had never run on a single push, and the
# reason was that this script's only success path could not execute on the
# machine it ships to.
#
# So: try GLOBAL, and on failure fall back to REPO-LOCAL on the repo that
# contains this directory (devrc). The fallback is narrower and the script SAYS
# so — the test gate is devrc-only by design, but the audit half is not, and a
# reader must not be left believing every repo is covered.
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

# The repo this githooks/ dir belongs to — the fallback's target. Resolved from
# $DIR, never from the caller's cwd, so `bash /some/where/githooks/install.sh`
# from an unrelated directory still configures the right repo.
REPO="$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null || true)"

if [ "${1:-}" = "--uninstall" ]; then
  removed=0
  current="$(git config --global --get core.hooksPath 2>/dev/null || true)"
  if [ "$current" = "$DIR" ]; then
    if git config --global --unset core.hooksPath 2>/dev/null; then
      echo "uninstalled: global core.hooksPath cleared (was $DIR)"
      removed=1
    else
      echo "WARNING: global core.hooksPath is '$DIR' but could not be cleared" >&2
      echo "         (is ~/.config/git/config a read-only nix-store symlink?)" >&2
    fi
  fi
  if [ -n "$REPO" ]; then
    lcurrent="$(git -C "$REPO" config --local --get core.hooksPath 2>/dev/null || true)"
    if [ "$lcurrent" = "$DIR" ]; then
      if git -C "$REPO" config --local --unset core.hooksPath; then
        echo "uninstalled: repo-local core.hooksPath cleared in $REPO (was $DIR)"
        removed=1
      else
        echo "WARNING: repo-local core.hooksPath in $REPO could not be cleared" >&2
      fi
    fi
  fi
  [ "$removed" = 1 ] || \
    echo "nothing to do: neither the global nor the $REPO local core.hooksPath is '$DIR'"
  exit 0
fi

chmod +x "$DIR/pre-push" "$DIR/audit-on-push.sh" "$DIR/tests-on-push.sh" 2>/dev/null || true

prev="$(git config --global --get core.hooksPath 2>/dev/null || true)"
if [ -n "$prev" ] && [ "$prev" != "$DIR" ]; then
  echo "WARNING: global core.hooksPath was already set to: $prev"
  echo "         overwriting with: $DIR"
  echo "         (your previous global hooks dir will no longer run; move its hooks here if needed)"
fi

SCOPE=""
if git config --global core.hooksPath "$DIR" 2>/dev/null; then
  SCOPE=global
else
  # 🔴 THE FALLBACK, AND IT IS THE NORMAL PATH ON THESE HOSTS — not an edge case.
  # A home-manager-managed ~/.config/git/config is a read-only store symlink, so
  # the global write above fails every time. Fall back to the repo that owns this
  # githooks/ dir. `--local` lives in the COMMON git dir, so every worktree of
  # that repo inherits it (verified 2026-08-21).
  if [ -z "$REPO" ]; then
    echo "FATAL: could not write the GLOBAL git config, and $DIR is not inside a" >&2
    echo "       git repository, so there is no local config to fall back to." >&2
    echo "       Nothing was installed." >&2
    exit 1
  fi
  if git -C "$REPO" config --local core.hooksPath "$DIR"; then
    SCOPE=local
  else
    echo "FATAL: could not write EITHER the global git config or the local config" >&2
    echo "       of $REPO. Nothing was installed." >&2
    exit 1
  fi
fi

# Seed the flag config file at shadow if it doesn't exist yet.
CONF="$HOME/.claude/audit-on-push.env"
if [ ! -f "$CONF" ]; then
  mkdir -p "$(dirname "$CONF")"
  cp "$DIR/audit-on-push.env.example" "$CONF" 2>/dev/null || true
  echo "seeded $CONF (AUDIT_ON_PUSH=shadow — sends nothing until you flip it to 'on')"
fi

if [ "$SCOPE" = global ]; then
  echo "installed: GLOBAL core.hooksPath -> $DIR"
  echo "scope: every repo that does not override core.hooksPath locally."
else
  echo "installed: REPO-LOCAL core.hooksPath -> $DIR   (in $REPO)"
  echo "scope: ⚠ THIS REPO ONLY. The global git config could not be written —"
  echo "       on a home-manager host ~/.config/git/config is a read-only"
  echo "       /nix/store symlink. The devrc test gate is unaffected (it is a"
  echo "       no-op outside devrc anyway); the push AUDIT will not fire for"
  echo "       your other repos until you either add"
  echo "         programs.git.extraConfig.core.hooksPath = \"$DIR\";"
  echo "       to nix/home.nix and switch, or run this per repo:"
  echo "         git -C <repo> config --local core.hooksPath $DIR"
fi
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
echo "  uninstall: $DIR/install.sh --uninstall"
