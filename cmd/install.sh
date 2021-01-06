#!/usr/bin/env sh

sudo apt update

# Install fd
curl -oL https://github.com/sharkdp/fd/releases/download/v8.2.1/fd_8.2.1_amd64.deb
sudo dpkg -i fd_8.2.1_amd64.deb

# Install k3d
curl -s https://raw.githubusercontent.com/rancher/k3d/main/install.sh | bash
# Create and switch to new cluster
k3d cluster create dev-cluster --volume $HOME/workspace:/home/$USER/workspace --switch-context --no-image-volume

# Install helm 3
curl https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 | bash
helm repo add stable https://charts.helm.sh/stable

# Install tilt
curl -fsSL https://raw.githubusercontent.com/tilt-dev/tilt/master/scripts/install.sh | bash

# Install Bazelisk
npm i -g @bazel/bazelisk

# Install python for Bazel pip deps
sudo apt install -y python

# Install Firebase tools
curl -sL https://firebase.tools | bash

