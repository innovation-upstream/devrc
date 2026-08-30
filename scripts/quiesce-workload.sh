#!/usr/bin/env bash
# quiesce — suspend a Flux kustomization and scale its deployment to 0.
#
# Deterministic primitive for killing a flux-managed workload. The agent
# figures out WHICH kustomization/deployment; this script does the action.
#
# Usage:
#   quiesce-workload.sh <cluster> <namespace> <kustomization> [deployment]
#
# Examples:
#   quiesce-workload.sh workbench media-stack stash-sense
#   quiesce-workload.sh homelab monitoring prometheus-prometheus
#
# Cluster is matched against the kubeconfig handles exported in .zshenv:
#   homelab   → $KC_HOMELAB
#   workbench → $KC_WORKBENCH
#   prod      → $KC_PROD
#
# Exit codes:
#   0  success — kustomization suspended, deployment scaled to 0
#   1  usage / bad cluster name
#   2  flux suspend failed
#   3  kubectl scale failed
#   4  verification failed (pod not terminating)
#
# To resume:  resume-workload.sh <cluster> <namespace> <kustomization> [deployment]
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <cluster> <namespace> <kustomization> [deployment]" >&2
  echo "  cluster: homelab | workbench | prod" >&2
  exit 1
fi

CLUSTER="$1"
NAMESPACE="$2"
KUSTOMIZATION="$3"
DEPLOYMENT="${4:-$KUSTOMIZATION}"

case "$CLUSTER" in
  homelab)   KC="${KC_HOMELAB:?KC_HOMELAB not set}" ;;
  workbench) KC="${KC_WORKBENCH:?KC_WORKBENCH not set}" ;;
  prod)      KC="${KC_PROD:?KC_PROD not set}" ;;
  *) echo "Unknown cluster: $CLUSTER (expected homelab | workbench | prod)" >&2; exit 1 ;;
esac

echo "→ Suspending kustomization $KUSTOMIZATION (flux-system) on $CLUSTER..."
if ! KUBECONFIG="$KC" flux suspend kustomization "$KUSTOMIZATION" -n flux-system; then
  echo "ERROR: flux suspend failed" >&2
  exit 2
fi

echo "→ Scaling deployment $DEPLOYMENT in $NAMESPACE to 0..."
if ! KUBECONFIG="$KC" kubectl scale deployment "$DEPLOYMENT" -n "$NAMESPACE" --replicas=0; then
  echo "ERROR: kubectl scale failed" >&2
  exit 3
fi

echo "→ Verifying pod termination..."
sleep 2
PODS=$(KUBECONFIG="$KC" kubectl get pods -n "$NAMESPACE" -l "app=$DEPLOYMENT" --no-headers 2>/dev/null || true)
if echo "$PODS" | grep -q Terminating; then
  echo "OK: pod terminating"
elif echo "$PODS" | grep -q "No resources found"; then
  echo "OK: no pods remaining"
elif [[ -z "$PODS" ]]; then
  echo "OK: no matching pods found (label may differ — check manually)"
else
  echo "WARNING: pods still present:" >&2
  echo "$PODS" >&2
  exit 4
fi

echo ""
echo "Done. Workload $KUSTOMIZATION quiesced on $CLUSTER."
echo "To resume: resume-workload.sh $CLUSTER $NAMESPACE $KUSTOMIZATION $DEPLOYMENT"
