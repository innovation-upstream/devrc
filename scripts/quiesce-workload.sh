#!/usr/bin/env bash
# quiesce — suspend a Flux kustomization and scale its deployment to 0.
#
# Deterministic primitive for killing a flux-managed workload. The agent
# figures out WHICH kustomization/deployment; this script does the action.
#
# Usage:
#   quiesce-workload.sh [--force] <cluster> <namespace> <kustomization> [deployment]
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
# 🔴 SUSPENDING A KUSTOMIZATION WEDGES EVERY KUSTOMIZATION THAT dependsOn IT.
# A suspended kustomization can never advance its lastAppliedRevision, so any
# dependent sits at `DependencyNotReady: revision is not up to date` FOREVER —
# it stops applying GitOps changes entirely, retries every 30s, and nothing
# about the target workload looks wrong. MEASURED 2026-08-30 on the workbench
# cluster: quiescing `stash-sense` silently wedged `aggregator` (dependsOn
# [media-stack, stash-sense]) for hours; it went unnoticed because the pinned
# revision happened to still be current for its own path, so NOTHING had been
# dropped yet and there was no symptom to see. The next aggregator commit
# would simply not have applied.
#
# So this script REFUSES to suspend a kustomization that has dependents, and
# refuses just as loudly when it CANNOT TELL whether it has any — an empty
# answer from a failed query is indistinguishable from a genuine "none", and
# must never read as the all-clear. `--force` overrides either refusal and
# prints what it is overriding.
#
# Exit codes:
#   0  success — kustomization suspended, deployment scaled to 0
#   1  usage / bad cluster name
#   2  flux suspend failed
#   3  kubectl scale failed
#   4  verification failed (pod not terminating)
#   5  refused — other kustomizations dependsOn this one (use --force)
#   6  refused — could NOT determine dependents (use --force)
#
# To resume:  resume-workload.sh <cluster> <namespace> <kustomization> [deployment]
set -euo pipefail

FORCE=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
set -- ${ARGS+"${ARGS[@]}"}

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 [--force] <cluster> <namespace> <kustomization> [deployment]" >&2
  echo "  cluster: homelab | workbench | prod" >&2
  echo "  --force: suspend even if other kustomizations dependsOn this one" >&2
  exit 1
fi

CLUSTER="$1"
NAMESPACE="$2"
KUSTOMIZATION="$3"
DEPLOYMENT="${4:-$KUSTOMIZATION}"
FLUX_NS="${FLUX_NS:-flux-system}"

case "$CLUSTER" in
  homelab)   KC="${KC_HOMELAB:?KC_HOMELAB not set}" ;;
  workbench) KC="${KC_WORKBENCH:?KC_WORKBENCH not set}" ;;
  prod)      KC="${KC_PROD:?KC_PROD not set}" ;;
  *) echo "Unknown cluster: $CLUSTER (expected homelab | workbench | prod)" >&2; exit 1 ;;
esac

# ---- pre-flight: who dependsOn this kustomization? -------------------------
# Runs BEFORE the suspend on purpose: once suspended, the dependents are
# already wedged and reporting it is too late to be a guard.
echo "→ Checking what dependsOn $KUSTOMIZATION ($FLUX_NS) on $CLUSTER..."
set +e
KS_JSON=$(KUBECONFIG="$KC" kubectl get kustomizations -A -o json 2>&1)
KS_RC=$?
set -e

DEPENDENTS=""
DEP_RC=0
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
        # dependsOn[].namespace defaults to the DEPENDENT own namespace
        if (dep.get("namespace") or own) != tns:
            continue
        print("%s/%s" % (own, md.get("name", "")))
' "$KUSTOMIZATION" "$FLUX_NS" 2>&1)
  DEP_RC=$?
  set -e
fi

if [[ $KS_RC -ne 0 || $DEP_RC -ne 0 ]]; then
  echo "" >&2
  echo "🔴 COULD NOT DETERMINE DEPENDENTS of $KUSTOMIZATION." >&2
  if [[ $KS_RC -ne 0 ]]; then
    echo "   'kubectl get kustomizations -A' failed (rc=$KS_RC):" >&2
    printf '   %s\n' "$KS_JSON" | head -5 >&2
  else
    echo "   could not parse the kustomization list (rc=$DEP_RC):" >&2
    printf '   %s\n' "$DEPENDENTS" | head -5 >&2
  fi
  echo "   This is NOT 'no dependents' — it is an unanswered question, and a" >&2
  echo "   suspend under it can wedge a dependent with no way to notice." >&2
  if [[ $FORCE -eq 0 ]]; then
    echo "   Refusing. Re-run with --force to suspend anyway." >&2
    exit 6
  fi
  echo "   --force given: suspending anyway, dependents UNKNOWN." >&2
elif [[ -n "$DEPENDENTS" ]]; then
  echo "" >&2
  echo "🔴 $(printf '%s\n' "$DEPENDENTS" | grep -c .) kustomization(s) dependsOn $FLUX_NS/$KUSTOMIZATION:" >&2
  printf '%s\n' "$DEPENDENTS" | sed 's/^/     /' >&2
  echo "   Suspending $KUSTOMIZATION pins its revision, so each of these will sit" >&2
  echo "   at 'DependencyNotReady: revision is not up to date' and STOP applying" >&2
  echo "   GitOps changes until it is resumed. Nothing about the target workload" >&2
  echo "   will look wrong while that is true." >&2
  if [[ $FORCE -eq 0 ]]; then
    echo "   Refusing. Either resume/retarget those first, or re-run with --force" >&2
    echo "   and remember: resume-workload.sh $CLUSTER $NAMESPACE $KUSTOMIZATION" >&2
    exit 5
  fi
  echo "   --force given: suspending anyway. Those dependents are now wedged." >&2
else
  echo "  none — no kustomization dependsOn $FLUX_NS/$KUSTOMIZATION"
fi

echo "→ Suspending kustomization $KUSTOMIZATION ($FLUX_NS) on $CLUSTER..."
if ! KUBECONFIG="$KC" flux suspend kustomization "$KUSTOMIZATION" -n "$FLUX_NS"; then
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
