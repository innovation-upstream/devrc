#!/usr/bin/env bash

# Install Linkerd cli
curl -sL run.linkerd.io/install | sh

# Install step cli
# https://smallstep.com/cli/
wget https://github.com/smallstep/cli/releases/download/v0.15.14/step-cli_0.15.14_amd64.deb
sudo dpkg -i step-cli_0.15.14_amd64.deb

# Install step-ca
wget https://github.com/smallstep/certificates/releases/download/v0.15.11/step-ca_0.15.11_amd64.deb
sudo dpkg -i step-ca_0.15.11_amd64.deb
