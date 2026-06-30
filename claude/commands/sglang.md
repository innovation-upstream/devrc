---
name: sglang
description: "Manage SGLang inference server on workbench cluster: swap models, check status, port-forward, scale, view logs"
---

# /sglang - SGLang Server Management

## Triggers
- Changing the served model on SGLang
- Checking SGLang server status or health
- Setting up port-forward for local access
- Viewing server logs or GPU utilization
- Scaling SGLang up/down (GPU contention with ComfyUI/Ollama)
- Troubleshooting OOM or scheduling issues

## Usage
```
/sglang                           Show status (pod, model, GPU, health)
/sglang model <model>             Change served model (e.g. hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4)
/sglang port-forward              Start port-forward (localhost:30000 -> svc:80)
/sglang logs [--tail N]           View server logs
/sglang restart                   Restart the pod (re-downloads model if changed)
/sglang up                        Scale to 1 replica (frees GPU from ComfyUI if needed)
/sglang down                      Scale to 0 replicas
/sglang gpu                       Show GPU allocation across workbench cluster
```

## Infrastructure

### Cluster Access
- **Kubeconfig**: `/home/zach/workspace/homelab-talos/workbench-kubeconfig`
- **Namespace**: `promptver`
- **Deployment**: `sglang-server`
- **Service**: `sglang-server` (ClusterIP, port 80 -> container 30000)
- **Node**: `nixos` (workbench is the local machine)

### Deployment File
`/home/zach/workspace/homelab-talos/clusters/workbench/apps/promptver/sglang-server/deployment.yaml`

### Current Configuration
- **Image**: `docker.io/lmsysorg/sglang:latest`
- **Model**: Check deployment YAML for current model (changes frequently)
- **GPU**: 1x NVIDIA RTX 5080 (16GB) — shared with ComfyUI and promptver-worker
- **Context**: 4096 tokens
- **KV cache**: fp8_e5m2
- **Attention**: flashinfer
- **CUDA graph**: disabled (memory constraints)
- **Flux reconcile**: disabled on this deployment (manual apply required)
- **Metrics**: Prometheus at `/metrics`

### GPU Contention
The workbench node has 3 GPUs shared across services. SGLang needs 1 GPU.
Common contenders:
- `comfyui` (promptver namespace) — 1 GPU
- `promptver-worker` — 1 GPU
- `content-junction` (tryonhaulcentral namespace) — 1 GPU

If SGLang is Pending with `Insufficient nvidia.com/gpu`, check which pods hold GPUs:
```bash
kubectl --kubeconfig /home/zach/workspace/homelab-talos/workbench-kubeconfig get pods -A -o json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for pod in data['items']:
    ns = pod['metadata']['namespace']
    name = pod['metadata']['name']
    phase = pod['status'].get('phase','')
    for c in pod['spec'].get('containers',[]):
        lim = c.get('resources',{}).get('limits',{}) or {}
        gpu = lim.get('nvidia.com/gpu')
        if gpu:
            print(f'{phase:12s} {ns}/{name}  gpu={gpu}')
"
```

To free a GPU from ComfyUI (prevents Flux from respawning it):
```bash
KC="/home/zach/workspace/homelab-talos/workbench-kubeconfig"
kubectl --kubeconfig $KC annotate deployment comfyui -n promptver kustomize.toolkit.fluxcd.io/reconcile=disabled --overwrite
kubectl --kubeconfig $KC scale deployment comfyui -n promptver --replicas=0
```

### Ollama Contention
Ollama runs on the same host and may hold GPU memory. Check with `pgrep -af ollama`.
Kill with `pkill ollama` before scaling SGLang up if GPU is exhausted.

## Behavioral Flow

### /sglang (status)
1. Check pod status: `kubectl get pods -n promptver -l app=sglang-server`
2. Check current model from deployment args
3. Check health: `curl -s http://localhost:30000/health` (if port-forward active)
4. Report: pod state, model, GPU usage, health

### /sglang model <model>
1. Read current deployment YAML
2. Update `--model-path` in the launch command args
3. Update the comment above the launch command to describe the new model
4. Adjust `--mem-fraction-static` based on model size:
   - 1.7B or smaller: 0.30
   - 4B: 0.50
   - 8B-AWQ/GPTQ (4-bit): 0.80
   - 8B fp16: WILL NOT FIT on 16GB GPU — reject with explanation
5. Commit the deployment YAML change to homelab-talos repo and push to trunk
6. Reconcile Flux: `flux reconcile source git cluster-config && flux reconcile kustomization promptver` (use `--kubeconfig` flag)
   - If `flux` CLI is unavailable, apply directly: `kubectl apply -f deployment.yaml`
7. Wait for rollout: watch pod status until Running + Ready (startup probe allows 5 min)
8. Check logs for successful model load (look for health check 200s)
9. Verify health: `curl -s http://localhost:30000/health` (if port-forward active)

**Model naming conventions:**
- HuggingFace models: `hugging-quants/Meta-Llama-3.1-8B-Instruct-AWQ-INT4`, `Qwen/Qwen3-8B-AWQ`
- Pre-quantized required for 16GB GPU if model > 4B params
- AWQ models auto-detected by SGLang (uses `awq_marlin` kernel)

**16GB GPU model fit guide:**
| Model | Quantization | Weights ~GB | Fits? |
|-------|-------------|-------------|-------|
| Qwen3-1.7B | fp16 | 3.5 | Yes |
| Qwen3-4B | fp16 | 8 | Yes |
| Qwen3-8B | fp16 | 16 | No (no room for KV cache) |
| Qwen3-8B-AWQ | 4-bit | 5 | Yes |
| Qwen3-8B-GPTQ | 4-bit | 5 | Yes |
| Llama-3.1-8B | fp16 | 16 | No |
| Llama-3.1-8B-Instruct-AWQ-INT4 | 4-bit | 5 | Yes |

### /sglang port-forward
```bash
kubectl --kubeconfig /home/zach/workspace/homelab-talos/workbench-kubeconfig \
  port-forward -n promptver svc/sglang-server 30000:80 &>/dev/null &
```
Verify: `curl -s http://localhost:30000/health`

### /sglang up
1. Check GPU availability (see GPU Contention section)
2. If GPU unavailable, identify holder and ask user before evicting
3. Commit and push any pending deployment YAML changes to homelab-talos
4. Reconcile Flux or apply directly: `kubectl apply -f deployment.yaml`
5. Scale deployment to 1 if needed: `kubectl scale deployment sglang-server -n promptver --replicas=1`
6. Wait for pod Running + Ready (startup probe allows 5 min for model download)
7. Verify logs show health check 200s

### /sglang down
1. Scale to 0: `kubectl scale deployment sglang-server -n promptver --replicas=0`
2. Optionally commit replica count change (or leave as runtime-only)

### /sglang restart
1. Delete the pod (deployment recreates it): `kubectl delete pod -n promptver -l app=sglang-server`
2. Wait for new pod Running + Ready

## DSPy Integration

When SGLang is running and port-forwarded, simulations use it via:
```bash
# DSPY_MODEL must be "openai/" + the --model-path from the deployment
DSPY_MODEL=openai/<model-path> python simulation.py -s office_worker
```

The `DSPY_MODEL` env var must be `openai/` prefixed and match the `--model-path` served by SGLang.
Check the deployment YAML for the current model path.
For Qwen3 models, thinking mode is auto-disabled via `chat_template_kwargs`.

Switching back to Ollama:
```bash
# Start ollama, then run without DSPY_MODEL (defaults to ollama_chat/qwen3:8b)
ollama serve &
python simulation.py -s office_worker
```

## Key Commands Reference
```bash
KC="/home/zach/workspace/homelab-talos/workbench-kubeconfig"

# Status
kubectl --kubeconfig $KC get pods -n promptver -l app=sglang-server
kubectl --kubeconfig $KC logs -n promptver -l app=sglang-server --tail=20

# Apply changes
kubectl --kubeconfig $KC apply -f /home/zach/workspace/homelab-talos/clusters/workbench/apps/promptver/sglang-server/deployment.yaml

# Scale
kubectl --kubeconfig $KC scale deployment sglang-server -n promptver --replicas=1
kubectl --kubeconfig $KC scale deployment sglang-server -n promptver --replicas=0

# Port-forward
kubectl --kubeconfig $KC port-forward -n promptver svc/sglang-server 30000:80

# Health
curl -s http://localhost:30000/health
curl -s http://localhost:30000/v1/models

# GPU audit
nvidia-smi
```

## Multi-Cluster (Homelab as Second Backend)

The homelab cluster has its own SGLang server at `sglang.homelab.lan` (exposed via Traefik IngressRoute).
It normally serves `Qwen/Qwen3-1.7B` for promptver, but can be toggled to match the workbench model for simulation load balancing.

### Toggle Script
```bash
# In wth-is-happening repo
./scripts/toggle-homelab-sglang.sh sim        # Switch homelab to match workbench model
./scripts/toggle-homelab-sglang.sh promptver   # Restore to Qwen3-1.7B
./scripts/toggle-homelab-sglang.sh status      # Show current state
```

### Running with Both Backends
```bash
# Set SGLANG_API_BASE_2 to enable round-robin across both SGLang servers
DSPY_MODEL=openai/<model> SGLANG_API_BASE_2=http://sglang.homelab.lan \
  .venv/bin/python simulation.py -s office_worker
```

When `SGLANG_API_BASE_2` is set, simulation.py wraps both endpoints in a `RoundRobinLM` that cycles requests across them. When unset, behavior is identical to single-backend.

### Homelab Infrastructure
- **Kubeconfig**: `/home/zach/workspace/homelab-talos/homelab-kubeconfig`
- **Deployment**: `clusters/homelab/apps/promptver/sglang-server/deployment.yaml`
- **IngressRoute**: `sglang.homelab.lan` -> `sglang-server:80` (Traefik)
- **DNS**: Workbench CoreDNS forwards `homelab.lan` -> 192.168.50.94 -> Traefik 192.168.50.95
- **Health**: `curl http://sglang.homelab.lan/health`

### Important
- `sim` mode is ephemeral (kubectl patch, not committed). Flux reconcile restores promptver model automatically.
- Both servers must serve the **same model** for round-robin to produce consistent results.
- The toggle script reads the workbench deployment to determine which model to mirror.

## Boundaries

**Will:**
- Change the served model and apply the deployment
- Manage scaling, port-forwarding, and restarts
- Diagnose GPU contention and OOM issues
- Commit and push deployment changes to homelab-talos

**Will Not:**
- Serve models that exceed 16GB GPU memory without quantization
- Evict other GPU workloads without user confirmation
- Modify other deployments in the promptver namespace
- Change the SGLang container image version without discussion
