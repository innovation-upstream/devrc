#!/usr/bin/env bash
# resume — unsuspend a Flux kustomization and scale its deployment back up.
#
# Inverse of quiesce-workload.sh. Restores a workload that was suspended + scaled
# to 0 by the quiesce script.
#
# Usage:
#   resume-workload.sh <cluster> <namespace> <kustomization> [deployment] [replicas]
#
# Examples:
#   resume-workload.sh workbench media-stack stash-sense
#   resume-workload.sh homelab monitoring prometheus-prometheus 2
#
# Exit codes:
#   0  success — kustomization resumed, deployment scaled up
#   1  usage / bad cluster name
#   2  flux resume failed
#   3  kubectl scale failed
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <cluster> <namespace> <kustomization> [deployment] [replicas]" >&2
  echo "  cluster: homelab | workbench | prod" >&2
  echo "  replicas: defaults to 1" >&2
  exit 1
fi

CLUSTER="$1"
NAMESPACE="$2"
KUSTOMIZATION="$3"
DEPLOYMENT="${4:-$KUSTOMIZATION}"
REPLICAS="${5:-1}"

case "$CLUSTER" in
  homelab)   KC="${KC_HOMELAB:?KC_HOMELAB not set}" ;;
  workbench) KC="${KC_WORKBENCH:?KC_WORKBENCH not set}" ;;
  prod)      KC="${KC_PROD:?KC_PROD not set}" ;;
  *) echo "Unknown cluster: $CLUSTER (expected homelab | workbench | prod)" >&2; exit 1 ;;
esac

echo "→ Resuming kustomization $KUSTOMIZATION (flux-system) on $CLUSTER..."
if ! KUBECONFIG="$KC" flux resume kustomization "$KUSTOMIZATION" -n flux-system; then
  echo "ERROR: flux resume failed" >&2
  exit 2
fi

echo "→ Scaling deployment $DEPLOYMENT in $NAMESPACE to $REPLICAS..."
if ! KUBECONFIG="$KC" kubectl scale deployment "$DEPLOYMENT" -n "$NAMESPACE" --replicas="$REPLICAS"; then
  echo "ERROR: kubectl scale failed" >&2
  exit 3
fi

echo ""
echo "Done. Workload $KUSTOMIZATION resumed on $CLUSTER (replicas=$REPLICAS)."
echo "Flux will reconcile to the desired state on its next loop."
