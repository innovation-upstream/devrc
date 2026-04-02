#!/usr/bin/env bash

# This script must remain idempotent
#
# First-time setup (no home-manager in PATH yet):
#   nix run home-manager/master -- switch --flake . --impure
#
# After that, just run:
#   ./cmd/install.sh

DEVRC_DIR=${DEVRC_DIR:-$PWD}

. "${DEVRC_DIR}/nix/bin/source-nix.sh"

TMPDIR=/var/tmp home-manager switch --flake "${DEVRC_DIR}" --impure
