# DSPy recap-quality eval (report-only, NOT production)

A measured evaluation of whether **DSPy** (program + optimize) would improve the
initiatives recap generator over the current hand-written prompt in
`scripts/initiatives/recap.py`. This directory is a self-contained eval harness. It
is **NOT wired into the production sync** and DSPy is **not** a repo dependency.

Findings: `claudedocs/dspy-recap-eval-2026-07-23.md`.

## Files
- `dataset.py`   — curate a reproducible train/test split of real `recap_context`
  inputs from a frozen `initiative-scan --days 30 --json` capture (`scan-days30.json`).
  Guarantees the documented weak cases land in the test set. Freezes `eval_set.json`.
- `metric.py`    — the recap-quality metric: 2 deterministic dims (doc-meta penalty,
  concision) + 2 blind LLM-judge dims (faithfulness, status-awareness) → composite.
- `baseline.py`  — faithful reproduction of the production recap.py prompt + budget.
- `dspy_program.py` — `dspy.Signature` + Predict/ChainOfThought modules.
- `run_eval.py`  — orchestrates baseline + 4 DSPy variants; self-manages the
  vllm-recap port-forward; scores everything with the same metric → `results.json`.
- `variance.py`  — noise-floor probe (judge-only vs generation+judge repeats).

## Run it (NixOS)
```sh
# 1. venv (dspy isn't in nixpkgs). Native wheels need libstdc++/zlib from nix:
nix-shell -p python3 uv --run "uv venv --python 3.12 .venv && . .venv/bin/activate && uv pip install dspy-ai openai"
# 2. env.sh activates the venv + sets LD_LIBRARY_PATH for the native wheels.
. ./env.sh
# 3. port-forward vllm-recap (ephemeral local port; :8000 is taken on workbench):
KUBECONFIG=$KC_HOMELAB kubectl -n promptver port-forward svc/vllm-recap 8731:8000 &
# 4. run (self-manages its own PF if --api-base omitted):
python run_eval.py --api-base http://127.0.0.1:8731/v1
python variance.py --api-base http://127.0.0.1:8731/v1 -k 5
```

LM: homelab `vllm-recap` serving `Qwen/Qwen2.5-7B-Instruct-AWQ` (served id `recap`),
OpenAI-compatible. The same model is used to generate AND to judge (a documented
limitation — see the findings doc).
