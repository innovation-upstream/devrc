#!/usr/bin/env bash

devCluster=${K3D_CLUSTER:-dev-cluster}

k3d cluster create $devCluster --volume $HOME/workspace:/home/$USER/workspace \
  --no-image-volume \
  # first server argflags from https://github.com/rancher/k3d/issues/133#issuecomment-770418986
  --k3s-server-arg \
  '--kubelet-arg=eviction-hard=imagefs.available<1%,nodefs.available<1%' \
  --k3s-server-arg \
  '--kubelet-arg=eviction-minimum-reclaim=imagefs.available=1%,nodefs.\
  available=1%' \
  # Fixes for nix from \
  # https://discourse.nixos.org/t/how-to-setup-kubernetes-k3d-on-nixos/13574
  --k3s-server-arg "--kube-proxy-arg=conntrack-max-per-core=0" \
  --k3s-agent-arg "--kube-proxy-arg=conntrack-max-per-core=0"
  --volume "${HOME}/.k3d/registries.yaml:/etc/rancher/k3s/registries.yaml" \
#k3d kubeconfig merge $devCluster --kubeconfig-switch-context

