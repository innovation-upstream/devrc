#!/usr/bin/env bash

devCluster="dev-cluster"

# Create and switch to new cluster TODO: Make this wait for dockerd to start
# (currently there is a race condition between this line being executed and dockerd having enough time to start)
# flags from https://github.com/rancher/k3d/issues/133#issuecomment-770418986
k3d cluster create $devCluster --volume $HOME/workspace:/home/$USER/workspace \
  --no-image-volume --k3s-server-arg '--kubelet-arg=eviction-hard=imagefs.available<1%,nodefs.available<1%' --k3s-server-arg '--kubelet-arg=eviction-minimum-reclaim=imagefs.available=1%,nodefs.available=1%' --volume "${HOME}/.k3d/registries.yaml:/etc/rancher/k3s/registries.yaml"
k3d kubeconfig merge $devCluster --kubeconfig-switch-context

