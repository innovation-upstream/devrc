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

🔴 **A suspend wedges every kustomization that `dependsOn` the target, and the
target itself looks perfectly fine while it does.** A suspended kustomization can
never advance its `lastAppliedRevision`, so each dependent sits at
`DependencyNotReady: revision is not up to date` and **stops applying GitOps
changes entirely**, retrying every 30s for as long as the quiesce lasts.

The script now refuses rather than doing that — **read which refusal you got**:
- **exit 5** — it named the dependents. Do not reach for `--force` first: decide
  whether those dependents can afford to stop reconciling for the whole quiesce.
- **exit 6** — it could not *determine* the dependents (the list query failed).
  🔴 That is an unanswered question, **not** "none". A failed query returns the
  same empty set a genuinely-undepended-on kustomization does, which is exactly
  why it is a separate code and not folded into the clean path.
- `--force` overrides either, and prints what it is overriding. When you use it,
  **say in your report that dependents are wedged and name them** — that is the
  fact the next session needs and the one nothing else will surface.

MEASURED 2026-08-30, workbench: quiescing `stash-sense` wedged `aggregator`
(`dependsOn: [media-stack, stash-sense]`) at `trunk@8cb6ff3a` while the source
had moved to `trunk@f93935de`. It was invisible for hours — 0 of the 70 commits
in that range touched aggregator's own path, so nothing had been dropped and
there was no symptom to notice. The *next* aggregator commit would not have
applied.

To check by hand on any cluster:
```bash
KUBECONFIG=$KC_<CLUSTER> kubectl get kustomization -A -o json \
  | jq -r --arg t '<kustomization>' '.items[]
      | select([(.spec.dependsOn // [])[].name] | index($t))
      | "\(.metadata.namespace)/\(.metadata.name)"'
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
- **Any dependents left wedged** (only possible via `--force`) — name them, or say
  plainly that the check could not be made. A quiesce that stops another
  kustomization reconciling is not a self-contained action, and this is the only
  place that fact gets recorded.
- The resume command for when they want it back

## Reference — cluster handles

| Cluster | Handle | Use for |
|---|---|---|
| homelab | `$KC_HOMELAB` | Talos Linux, GitOps via Flux |
| workbench | `$KC_WORKBENCH` | NixOS + k3s, media stack, SGLang |
| prod | `$KC_PROD` | Hetzner k0s, production |

All handles are exported in `~/.zshenv` and injected into opencode's bash tool via the env plugin.
