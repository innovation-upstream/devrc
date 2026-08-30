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
FLUX_NS="${FLUX_NS:-flux-system}"

case "$CLUSTER" in
  homelab)   KC="${KC_HOMELAB:?KC_HOMELAB not set}" ;;
  workbench) KC="${KC_WORKBENCH:?KC_WORKBENCH not set}" ;;
  prod)      KC="${KC_PROD:?KC_PROD not set}" ;;
  *) echo "Unknown cluster: $CLUSTER (expected homelab | workbench | prod)" >&2; exit 1 ;;
esac

echo "→ Resuming kustomization $KUSTOMIZATION ($FLUX_NS) on $CLUSTER..."
if ! KUBECONFIG="$KC" flux resume kustomization "$KUSTOMIZATION" -n "$FLUX_NS"; then
  echo "ERROR: flux resume failed" >&2
  exit 2
fi

echo "→ Scaling deployment $DEPLOYMENT in $NAMESPACE to $REPLICAS..."
if ! KUBECONFIG="$KC" kubectl scale deployment "$DEPLOYMENT" -n "$NAMESPACE" --replicas="$REPLICAS"; then
  echo "ERROR: kubectl scale failed" >&2
  exit 3
fi

# Anything that dependsOn this kustomization was wedged at DependencyNotReady
# while it was suspended (see quiesce-workload.sh's pre-flight). Name them, so
# the operator has something specific to re-check rather than a general hope.
set +e
KS_JSON=$(KUBECONFIG="$KC" kubectl get kustomizations -A -o json 2>/dev/null)
KS_RC=$?
set -e
if [[ $KS_RC -eq 0 ]]; then
  set +e
  DEPENDENTS=$(printf '%s' "$KS_JSON" | python3 -c '
import json, sys
target, tns = sys.argv[1], sys.argv[2]
d = json.load(sys.stdin)
for k in d.get("items", []):
    md = k.get("metadata", {}) or {}
    own = md.get("namespace", "") or ""
    for dep in (k.get("spec", {}) or {}).get("dependsOn") or []:
        if dep.get("name") != target:
            continue
        if (dep.get("namespace") or own) != tns:
            continue
        print("%s/%s" % (own, md.get("name", "")))
' "$KUSTOMIZATION" "$FLUX_NS" 2>/dev/null)
  DEP_RC=$?
  set -e
  if [[ $DEP_RC -eq 0 && -n "$DEPENDENTS" ]]; then
    echo ""
    echo "These dependsOn $KUSTOMIZATION and were wedged while it was suspended —"
    echo "confirm each goes Ready again (they clear on the next reconcile, not instantly):"
    printf '     %s\n' $(printf '%s\n' "$DEPENDENTS")
    echo "  KUBECONFIG=$KC flux get kustomization -n $FLUX_NS"
  fi
else
  echo ""
  echo "NOTE: could not list kustomizations, so any DEPENDENTS of $KUSTOMIZATION" >&2
  echo "      were not checked. That is unknown, not clear." >&2
fi

echo ""
echo "Done. Workload $KUSTOMIZATION resumed on $CLUSTER (replicas=$REPLICAS)."
echo "Flux will reconcile to the desired state on its next loop."
