#!/usr/bin/env bash

# This script must remain idempotent

DEVRC_DIR=${DEVRC_DIR:-$PWD}

. ${DEVRC_DIR}/nix/bin/source-nix.sh

${DEVRC_DIR}/nix/bin/channels.sh

${DEVRC_DIR}/nix/bin/init-home-manager.sh

TMPDIR=/var/tmp home-manager switch

