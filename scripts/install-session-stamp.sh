#!/usr/bin/env bash
#
# install-session-stamp.sh — install the prepare-commit-msg session-id stamper
# into ONE repo's common .git/hooks/.
#
# DRY-RUN BY DEFAULT. Prints what it would do and changes nothing unless
# --apply is passed. That is the same operator-act shape as
# scripts/sync-skill-tiers.py, and for the same reason: this modifies a
# developer's git behaviour, so arming it is a decision, not a side effect of
# merging a PR.
#
# 🔴 WHY NOT githooks/install.sh. That installer sets core.hooksPath GLOBALLY and,
# by its own header, arms the devrc pre-push TEST GATE by default — the devrc#322
# shape, where a pre-push hook running the suite in the worktree it was pushing
# rewrote the branch. Installing a message stamper must not drag a blocking test
# gate onto the box with it. This script touches exactly one file in one repo and
# never writes git config at all.
#
# 🔴 ONE INSTALL COVERS EVERY WORKTREE. Hooks live in the COMMON git dir, which
# every `git worktree add` of a clone shares — so a single install serves all of
# this box's devrc worktrees rather than needing one per tree.
set -euo pipefail

# shellcheck disable=SC1007
DIR="$(CDPATH= cd -P -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DEFAULT="$(dirname "$DIR")"

usage() {
  cat <<'USAGE'
usage: install-session-stamp.sh [--repo <path>] [--apply|--uninstall] [--force]

  --repo <path>   repo to install into (default: this script's own checkout)
  --apply         actually install (default is dry-run)
  --uninstall     remove a previously installed hook
  --force         replace an existing FOREIGN prepare-commit-msg hook

Exit codes:
  0  ok (or dry-run completed)
  2  bad usage
  3  target is not a git repo
  4  a foreign prepare-commit-msg exists and --force was not given
USAGE
}

REPO="$REPO_DEFAULT"
MODE="dry"
FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:?--repo needs a path}"; shift 2 ;;
    --apply) MODE="apply"; shift ;;
    --uninstall) MODE="uninstall"; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! COMMON="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
  echo "install-session-stamp: not a git repo: $REPO" >&2
  exit 3
fi

HOOKS="$COMMON/hooks"
TARGET="$HOOKS/prepare-commit-msg"
SOURCE="$REPO_DEFAULT/githooks/prepare-commit-msg"

echo "install-session-stamp:"
echo "  repo:    $REPO"
echo "  common:  $COMMON"
echo "  target:  $TARGET"
echo "  source:  $SOURCE"

# 🔴 A repo-local core.hooksPath OVERRIDES .git/hooks, so an install into
# .git/hooks would be silently inert. CLAUDE.md records this value as volatile on
# this box, so it is measured here rather than assumed — and reported either way.
LOCAL_HOOKSPATH="$(git -C "$REPO" config --local --get core.hooksPath || true)"
GLOBAL_HOOKSPATH="$(git config --global --get core.hooksPath || true)"
if [ -n "$LOCAL_HOOKSPATH" ] || [ -n "$GLOBAL_HOOKSPATH" ]; then
  echo "  ⚠ core.hooksPath is SET (local='${LOCAL_HOOKSPATH:-}' global='${GLOBAL_HOOKSPATH:-}')."
  echo "    git will read hooks from there, NOT from $HOOKS — this install would be INERT."
  echo "    Unset it, or install into that directory instead."
else
  echo "  core.hooksPath: unset (git will read $HOOKS) ✓"
fi

if [ "$MODE" = "uninstall" ]; then
  if [ -L "$TARGET" ] && [ "$(readlink -f "$TARGET")" = "$(readlink -f "$SOURCE")" ]; then
    rm -f "$TARGET"; echo "  UNINSTALLED"
  else
    echo "  nothing to uninstall (no devrc-managed hook at that path)"
  fi
  exit 0
fi

if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  if [ -L "$TARGET" ] && [ "$(readlink -f "$TARGET")" = "$(readlink -f "$SOURCE")" ]; then
    echo "  already installed (symlink -> source) ✓"
    exit 0
  fi
  if [ "$FORCE" -ne 1 ]; then
    echo "  ✘ a FOREIGN prepare-commit-msg already exists. Look at it before replacing:" >&2
    echo "      $TARGET" >&2
    echo "    Re-run with --force to replace it." >&2
    exit 4
  fi
fi

if [ "$MODE" = "dry" ]; then
  echo "  DRY-RUN — would symlink $TARGET -> $SOURCE"
  echo "  re-run with --apply to install"
  exit 0
fi

mkdir -p "$HOOKS"
ln -sfn "$SOURCE" "$TARGET"
chmod +x "$SOURCE"
echo "  INSTALLED (symlink, so edits in the checkout apply with no re-install)"
