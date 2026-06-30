---
name: gpu-operator-check
description: "Analyze NVIDIA GPU Operator helm chart integration across GPU fleet clusters and check for newer versions with breaking changes"
---

# /gpu-operator-check - NVIDIA GPU Operator Version Analysis

## Triggers
- Questions about NVIDIA GPU operator version, upgrade path, or compatibility
- Requests to check for newer GPU operator releases
- Pre-upgrade analysis and breaking change assessment
- GPU operator helm chart configuration review

## Usage
```
/gpu-operator-check [action]

Actions:
  (default)        Full analysis: current state + latest version + breaking changes
  current          Show current deployed version and per-cluster configuration
  latest           Check for latest available version only
  upgrade-plan     Generate upgrade plan with risk assessment
```

## Behavioral Flow

### 1. Analyze Current Integration
Read the helm chart integration to determine deployed state:

1. **Base chart**: Read `clusters/gpu-fleet/charts/nvidia-gpu-operator.yaml` for:
   - HelmRepository source URL
   - HelmRelease version
   - Base values (driver, toolkit, devicePlugin, cdi, dcgmExporter settings)

2. **Per-cluster overlays**: Check overlay patches in each cluster's `charts-kustomization.yaml`:
   - `clusters/submodel-dc-02-b-overlay/charts-kustomization.yaml`
   - `clusters/submodel-dc-02-c-overlay/charts-kustomization.yaml`
   - Look for version pins, timeout overrides, CDI overrides, OpenAPI validation settings

3. **Containerd integration**: Note the custom k0s containerd config chain:
   - `clusters/gpu-fleet/bootstrap/containerd-with-nvidia.toml` (import file)
   - `clusters/gpu-fleet/bootstrap/patch-containerd.toml` (runtime config)
   - Toolkit writes to `/etc/k0s/nvidia-generated-unused.yaml`, imported via `/etc/k0s/containerd.d/nvidia-containerd.toml`

4. **Build version matrix**: Show version + patches per cluster in a table

### 2. Check Latest Version
Use WebSearch to find the latest available version:

- Search ArtifactHub: `nvidia gpu-operator helm chart latest version [current year]`
- Search GitHub releases: `nvidia gpu-operator releases`
- Search NVIDIA docs: `nvidia gpu-operator release notes`

### 3. Identify Breaking Changes
Fetch and analyze release notes for all versions between current and latest:

- WebFetch `https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/release-notes.html`
- Extract: breaking changes, deprecations, removed features, helm value changes
- Pay special attention to changes affecting:
  - **CDI behavior** (fleet explicitly disables CDI)
  - **Containerd configuration method** (fleet uses custom k0s containerd chain)
  - **Toolkit environment variables** (CONTAINERD_CONFIG, CONTAINERD_SOCKET, etc.)
  - **Driver and devicePlugin defaults**
  - **CRD schema changes** (fleet uses disableOpenAPIValidation on some clusters)

### 4. Assess Upgrade Risk
For each breaking change, evaluate impact against fleet configuration:

| Risk Level | Criteria |
|------------|----------|
| **High** | Directly conflicts with current config (e.g., CDI default flip, containerd method change) |
| **Medium** | Requires explicit value override to maintain behavior |
| **Low** | New feature, opt-in only |
| **None** | Removed feature not in use |

### 5. Generate Recommendations
- Which values need to be added/changed in base chart or overlays
- Recommended rollout order (dc-02-c → dc-02-b → dc-02-a → h100-02)
- Whether dc-02-b/c overlay version pins need updating

## Fleet Context

### Cluster Topology
| Cluster | Nodes | GPU | Role |
|---------|-------|-----|------|
| submodel-h100-02 | 2 | H100 | Video generation |
| submodel-dc-02-a | 50 | RTX 4090 | Primary DC-02 |
| submodel-dc-02-b | 15 | RTX 4090 | Secondary DC-02 |
| submodel-dc-02-c | 5 | RTX 4090 | Tertiary DC-02 (test first) |

### Key Design Decisions
- CDI is **explicitly disabled** fleet-wide via overlay patches (`cdi.enabled: false`)
- Containerd uses a **custom import chain** for k0s compatibility
- All clusters use **overlay patches** for version pin, timeout (15m), CDI disabled, and OpenAPI validation skip
- Base chart remains at 25.3.4 — actual deployed version is controlled per-cluster via overlay `op: replace` on `/spec/chart/spec/version`
- Rollout order: smallest cluster first (dc-02-c → dc-02-b → h100-02 → dc-02-a)

### Upgrade Procedure (Proven)
1. Add/update nvidia-gpu-operator patch in the cluster's `charts-kustomization.yaml` overlay
2. Patch includes: version pin, 15m timeout, `disableOpenAPIValidation: true`, `cdi.enabled: false`
3. Commit, push, then `flux reconcile source git cluster-config && flux reconcile kustomization charts`
4. Verify: `kubectl get helmrelease nvidia-gpu-operator -n gpu-operator` shows upgrade succeeded
5. Verify: `kubectl get pods -n gpu-operator` — all Running (transient ImageInspectError on dcgm-exporter during containerd restart is normal, self-resolves)
6. Verify spine-controller: delete pod, wait for 2/2 Running, confirm `nvidia-smi` shows expected driver version from inside controller container
7. Driver version for chart 25.10.1: **570.211.01**

### Upgrade Notes
- 15m timeout is needed for large clusters (50 nodes); smaller clusters finish faster but timeout doesn't hurt
- `disableOpenAPIValidation: true` required for CRD schema compatibility during upgrade
- Transient containerd socket errors during toolkit restart are expected — pods retry and recover
- Spine controller readiness probe may take a few minutes to pass after pod recreation; container is functional before probe passes

### Key Files
| File | Purpose |
|------|---------|
| `clusters/gpu-fleet/charts/nvidia-gpu-operator.yaml` | Base HelmRelease + HelmRepository (v25.3.4) |
| `clusters/gpu-fleet/charts/kustomization.yaml` | Charts kustomization including GPU operator |
| `clusters/submodel-h100-02-overlay/charts-kustomization.yaml` | h100-02 version/timeout/CDI override |
| `clusters/submodel-dc-02-a-overlay/charts-kustomization.yaml` | dc-02-a version/timeout/CDI override |
| `clusters/submodel-dc-02-b-overlay/charts-kustomization.yaml` | dc-02-b version/timeout/CDI override |
| `clusters/submodel-dc-02-c-overlay/charts-kustomization.yaml` | dc-02-c version/timeout/CDI override |
| `clusters/gpu-fleet/bootstrap/containerd-with-nvidia.toml` | Containerd import for nvidia config |
| `clusters/gpu-fleet/bootstrap/patch-containerd.toml` | Full NVIDIA runtime configuration |

## Tool Coordination
- **Read/Glob**: Analyze current helm chart and overlay configurations
- **WebSearch**: Find latest version on ArtifactHub, GitHub, NVIDIA docs
- **WebFetch**: Pull detailed release notes from NVIDIA documentation
- **Grep**: Search for GPU operator references across cluster overlays

## Boundaries

**Will:**
- Analyze current GPU operator integration across all clusters
- Check for newer versions and identify breaking changes
- Assess upgrade risk against fleet-specific configuration
- Recommend upgrade approach and required config changes

**Will Not:**
- Modify any cluster configuration without explicit request
- Apply upgrades — only analyze and recommend
- Make assumptions about containerd compatibility without checking docs
