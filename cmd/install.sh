#!/usr/bin/env bash

# This script must remain idempotent

sudo apt update
sudo apt-get update

# Docker Install
# Prereqs
if [ -z $(command -v docker) ];
then
  sudo apt install -y apt-transport-https ca-certificates curl \
    software-properties-common build-essential
  # Add Docker GPG Key to apt
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
  # Add Docker repo to apt
  sudo add-apt-repository "deb [arch=amd64] \
    https://download.docker.com/linux/ubuntu focal stable"
  # Install Docker CE
  sudo apt install -y docker-ce
  # Add user to docker group
  sudo usermod -aG docker ${USER}
fi

if [ -z $(command -v nix) ];
then
  sh <(curl -L https://nixos.org/nix/install) --no-daemon
fi

. ${DEVRC_DIR}/nix/bin/source-nix.sh

${DEVRC_DIR}/nix/bin/channels.sh

${DEVRC_DIR}/nix/bin/init-home-manager.sh

TMPDIR=/var/tmp home-manager switch

