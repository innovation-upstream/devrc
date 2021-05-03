#!/usr/bin/env bash

BLUE='\033[1;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color
BOLDNC="${NC}\033[1m"

# Custom Dev Env Init
if test -f $DEVRC_DIR/.devrc; then
  source $DEVRC_DIR/.devrc
  # Try to catch if the .devrc is misconfigured
  if ! command -v bazel &> /dev/null; then
    printf "${BLUE}bazel ${RED}command was not found after loading ${BOLDNC}${DEVRC_DIR}/.devrc! \
${RED}Please ensure you are sourcing ${BOLDNC}${DEVRC_DIR}/cmd/profile.sh ${RED}or initializing nvm in \
${BOLDNC}${DEVRC_DIR}/.devrc${NC}. \n(See ${DIR}/.devrc.default for a working example)\n"
  fi
else
  printf "Using ${BLUE}${DEVRC_DIR}/.devrc${BOLDNC}. Copy ${DIR}/.devrc.default into ${DIR}/.devrc if you
  would like to further configure your shell.\n"
  source ${DEVRC_DIR}/.devrc.default
fi
