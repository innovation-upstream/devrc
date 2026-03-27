#!/usr/bin/env bash
# Apply i3 config changes: Gruvbox bar colors + scratchpad bindings
set -euo pipefail

cp /home/zach/workspace/devrc/nix/system/i3config.nix /etc/nixos/i3config.nix
echo "Copied i3config.nix to /etc/nixos/"

nixos-rebuild switch
echo "NixOS rebuilt. Run: i3-msg reload"
