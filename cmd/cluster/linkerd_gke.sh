#!/usr/bin/env bash

# This script is for installing and configuring linkerd in a GKE cluster

set -v

# Create devcerts dir
devCertsDir=$HOME/.dev_certs
[ -d $devCertsDir ] || mkdir $devCertsDir

linkerd install \
  --identity-trust-anchors-file $devCertsDir/root.crt \
  --identity-issuer-certificate-file $devCertsDir/issuer.crt \
  --identity-issuer-key-file $devCertsDir/issuer.key \
  | tee \
    >(kubectl --context=$SHARED_DEV_CLUSTER apply -f -)

for ctx in $SHARED_DEV_CLUSTER; do
  echo "Checking cluster: ${ctx} .........\n"
  linkerd --context=${ctx} check || break
  echo "-------------\n"
done

# Install viz dashboard extension
linkerd --context=$SHARED_DEV_CLUSTER viz install | kubectl apply -f -

# Install multicluster
linkerd --context=$SHARED_DEV_CLUSTER multicluster install | \
  kubectl --context=$SHARED_DEV_CLUSTER apply -f -

# Verify linkerd gateway
kubectl --context=$SHARED_DEV_CLUSTER -n linkerd-multicluster \
    rollout status deploy/linkerd-gateway

# Verify linkerd lb
while [ "$(kubectl --context=$SHARED_DEV_CLUSTER -n linkerd-multicluster get service \
  -o 'custom-columns=:.status.loadBalancer.ingress[0].ip' \
  --no-headers)" = "<none>" ]; do
    printf '.'
    sleep 1
done
