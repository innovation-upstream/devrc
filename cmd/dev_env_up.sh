#!/usr/bin/env bash

devCluster="dev-cluster"

# Create and switch to new cluster TODO: Make this wait for dockerd to start
# (currently there is a race condition between this line being executed and dockerd having enough time to start)
k3d cluster create $devCluster --volume $HOME/workspace:/home/$USER/workspace --no-image-volume --volume "${HOME}/.k3d/registries.yaml:/etc/rancher/k3s/registries.yaml"
k3d kubeconfig merge $devCluster --kubeconfig-switch-context

