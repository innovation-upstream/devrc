#!/usr/bin/env bash

# Create and switch to new cluster TODO: Make this wait for dockerd to start
# (currently there is a race condition between this line being executed and dockerd having enough time to start)
k3d cluster create dev-cluster --volume $HOME/workspace:/home/$USER/workspace --no-image-volume --volume "${HOME}/.k3d/registries.yaml:/etc/rancher/k3s/registries.yaml"
