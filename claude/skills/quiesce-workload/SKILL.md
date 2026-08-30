---
name: quiesce-workload
description: "Suspend a Flux workload (scale to 0) or resume. Finds cluster from PID or pod"
argument-hint: "<name or PID> — e.g. 'stash-sense', '3828249', 'prometheus'"
---

# /quiesce-workload — suspend or resume a Flux-managed workload

Input: `$ARGUMENTS`. This is either:
- A **service/pod name** (e.g. `stash-sense`, `prometheus`)
- A **PID** on the local machine (e.g. `3828249`)
- A bare keyword to search for across clusters

The script handles the deterministic action; you handle the investigation.

## Step 1 — resolve the target

### If input is a PID

```bash
# Get the container's cgroup to find the pod UID
cat /proc/<PID>/cgroup | head -5

# The cgroup path contains the pod UID (the long hex string after /pod/)
# Search all clusters for that pod UID:
KUBECONFIG=$KC_HOMELAB kubectl get pods -A -o json | jq -r '.items[] | select(.metadata.uid == "<UID>") | "\(.metadata.namespace) \(.metadata.name)"'
KUBECONFIG=$KC_WORKBENCH kubectl get pods -A -o json | jq -r '.items[] | select(.metadata.uid == "<UID>") | "\(.metadata.namespace) \(.metadata.name)"'
```

Also check the process command for clues:
```bash
ps -p <PID> -o pid,ppid,cmd --no-headers
```

### If input is a name or keyword

Search all three clusters:
```bash
KUBECONFIG=$KC_HOMELAB kubectl get pods -A | grep -i "<name>"
KUBECONFIG=$KC_WORKBENCH kubectl get pods -A | grep -i "<name>"
KUBECONFIG=$KC_PROD kubectl get pods -A | grep -i "<name>"
```

### Once you have namespace + pod name, find the deployment and kustomization

```bash
# Get the deployment name from the pod's ownerReferences
KUBECONFIG=$KC_<CLUSTER> kubectl get pod <pod-name> -n <namespace> -o json | jq -r '.metadata.ownerReferences[] | select(.kind=="ReplicaSet") | .name'

# Strip the replicaset suffix to get the deployment name
# e.g. stash-sense-56d7bcbb9d-zkpzb → deployment is stash-sense

# Find the matching Flux kustomization
KUBECONFIG=$KC_<CLUSTER> kubectl get kustomizations -A | grep -i "<deployment-or-part-of-it>"
```

🔴 **The kustomization name may not match the deployment name exactly.** Match by substring or check the kustomization's `spec.targetRef`.

## Step 2 — run the script

Once you have: `<cluster> <namespace> <kustomization> [deployment]`

### To quiesce (suspend + scale to 0):

```bash
bash ~/workspace/devrc/scripts/quiesce-workload.sh <cluster> <namespace> <kustomization> [deployment]
```

### To resume (unsuspend + scale back up):

```bash
bash ~/workspace/devrc/scripts/resume-workload.sh <cluster> <namespace> <kustomization> [deployment] [replicas]
```

The `deployment` argument is needed only when it differs from the kustomization name. `replicas` defaults to 1.

## Step 3 — report

State what was done:
- Which cluster, namespace, kustomization
- The CPU/memory it was consuming (from the initial `ps` or pod metrics)
- That it's terminating / scaled to 0
- The resume command for when they want it back

## Reference — cluster handles

| Cluster | Handle | Use for |
|---|---|---|
| homelab | `$KC_HOMELAB` | Talos Linux, GitOps via Flux |
| workbench | `$KC_WORKBENCH` | NixOS + k3s, media stack, SGLang |
| prod | `$KC_PROD` | Hetzner k0s, production |

All handles are exported in `~/.zshenv` and injected into opencode's bash tool via the env plugin.
