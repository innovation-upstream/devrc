#!/usr/bin/env bash
# deploy-plugin.sh — Symlink the activity plugin into OpenCode's plugins dir.
#
# Usage: bash scripts/collector/opencode/deploy-plugin.sh
set -euo pipefail

PLUGIN_SRC="$(readlink -f "$(dirname "$0")/activity-plugin.js")"
PLUGIN_DIR="$HOME/.config/opencode/plugins"
PLUGIN_LINK="$PLUGIN_DIR/activity.js"

if [[ ! -f "$PLUGIN_SRC" ]]; then
  echo "ERROR: plugin source not found: $PLUGIN_SRC" >&2
  exit 1
fi

mkdir -p "$PLUGIN_DIR"

# Remove stale regular file or dangling symlink, then create fresh symlink.
if [[ -e "$PLUGIN_LINK" || -L "$PLUGIN_LINK" ]]; then
  rm -f "$PLUGIN_LINK"
fi

ln -s "$PLUGIN_SRC" "$PLUGIN_LINK"

echo "Deployed: $PLUGIN_LINK -> $PLUGIN_SRC"
echo ""
echo "Reload OpenCode to activate the plugin."
