#!/usr/bin/env bash

set -v

kubectl config use-context $DEV_CLUSTER

# Create devcerts dir
devCertsDir=$HOME/.dev_certs
[ -d $devCertsDir ] || { echo Missing dev certs ; exit 1; }

linkerd install \
  --identity-trust-anchors-file $devCertsDir/root.crt \
  --identity-issuer-certificate-file $devCertsDir/issuer.crt \
  --identity-issuer-key-file $devCertsDir/issuer.key \
  | tee \
    >(kubectl --context=$DEV_CLUSTER apply -f -)

# Wait for linkerd install
while [ "$(linkerd --context=$DEV_CLUSTER multicluster install)" = "Waiting for control plane to become available" ]; do
    printf '.'
    sleep 1
done

# Install viz dashboard extension
linkerd --context=$DEV_CLUSTER viz install | kubectl apply -f -

# Install multicluster
linkerd --context=$DEV_CLUSTER multicluster install | kubectl --context=$DEV_CLUSTER apply -f -

# Verify linkerd gateway
kubectl --context=$DEV_CLUSTER -n linkerd-multicluster rollout status deploy/linkerd-gateway

# Verify linkerd lb
while [ "$(kubectl --context=$DEV_CLUSTER -n linkerd-multicluster get service \
  -o 'custom-columns=:.status.loadBalancer.ingress[0].ip' \
  --no-headers)" = "<none>" ]; do
    printf '.'
    sleep 1
done

# Link dev cluster with shared dev cluster
linkerd --context=$SHARED_DEV_CLUSTER multicluster link --cluster-name $SHARED_DEV_CLUSTER |
  kubectl --context=$DEV_CLUSTER apply -f -

# Verify clusters are linked
linkerd --context=$DEV_CLUSTER multicluster check
