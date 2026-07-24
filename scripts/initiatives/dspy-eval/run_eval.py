#!/usr/bin/env python3
"""Orchestrate the DSPy-vs-hand-prompt recap eval.

Phases (all scored by the SAME metric.score_recap on the SAME test set):
  1. baseline            — production recap.py prompt, one call (metric_source of truth)
  2. dspy_zeroshot       — dspy.Predict(InitiativeRecap), no optimization
  3. dspy_zeroshot_cot   — dspy.ChainOfThought(InitiativeRecap), no optimization
  4. dspy_bootstrap      — Predict compiled with BootstrapFewShot on the train set
  5. dspy_bootstrap_cot  — CoT compiled with BootstrapFewShot (optional)

Manages its own kubectl port-forward to vllm-recap unless --api-base is given.
Emits results.json (every recap + per-dimension score) and prints a summary +
side-by-side. Honest by construction: it reports whatever the metric measures.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import dspy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # production recap.py
sys.path.insert(0, str(HERE))

import recap  # noqa: E402
from baseline import production_recap  # noqa: E402
from dataset import load_eval_set  # noqa: E402
from dspy_program import PredictRecap, build_examples  # noqa: E402
from metric import Judge, make_dspy_metric, score_recap  # noqa: E402


# --------------------------------------------------------------------------- #
# Port-forward (self-managed, ephemeral) — mirrors recap.VllmClient
# --------------------------------------------------------------------------- #
class PortForward:
    def __init__(self, namespace="promptver", service="svc/vllm-recap", svc_port=8000):
        self.ns, self.svc, self.svc_port = namespace, service, svc_port
        self._pf = None
        self.api_base = None

    def __enter__(self):
        port = recap._free_local_port()
        self._pf = subprocess.Popen(
            ["kubectl", "-n", self.ns, "port-forward", self.svc, f"{port}:{self.svc_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self._pf.poll() is not None:
                raise RuntimeError("port-forward exited: " +
                                   (self._pf.stderr.read().decode() if self._pf.stderr else ""))
            with contextlib.suppress(OSError):
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    self.api_base = f"http://127.0.0.1:{port}/v1"
                    return self
            time.sleep(0.25)
        raise TimeoutError("port-forward not ready")

    def __exit__(self, *_):
        if self._pf:
            self._pf.terminate()
            with contextlib.suppress(Exception):
                self._pf.wait(timeout=5)


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #
DIMS = ["describes_work", "concision", "faithfulness", "status_awareness"]


def score_system(name, recaps: dict, test_records, judge) -> dict:
    """recaps: {slug: text} -> per-item + aggregate scores."""
    items = []
    for r in test_records:
        slug = r["slug"]
        text = recaps[slug]
        s = score_recap(r["ctx"], text, judge)
        items.append({"slug": slug, "momentum": r["momentum"], "recap": text, **s})
    agg = {"composite": _mean([i["composite"] for i in items])}
    for d in DIMS:
        agg[d] = _mean([i["dims"][d] for i in items])
    return {"system": name, "aggregate": agg, "items": items}


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def gen_with_program(program, test_records) -> dict:
    out = {}
    for r in test_records:
        from dspy_program import ctx_to_input
        pred = program(initiative_context=ctx_to_input(r["ctx"]))
        out[r["slug"]] = (getattr(pred, "recap", "") or "").strip()
    return out


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default=None,
                    help="OpenAI base (…/v1). Default: self-managed port-forward.")
    ap.add_argument("--max-demos", type=int, default=4)
    ap.add_argument("--metric-threshold", type=float, default=0.7)
    ap.add_argument("--skip-cot", action="store_true")
    ap.add_argument("--out", default=str(HERE / "results.json"))
    a = ap.parse_args(argv)

    data = load_eval_set()
    train_rec, test_rec = data["train"], data["test"]

    pf_ctx = PortForward() if not a.api_base else contextlib.nullcontext()
    with pf_ctx as pf:
        api_base = a.api_base or pf.api_base
        print(f"endpoint: {api_base}")
        judge = Judge(api_base)

        # DSPy LM at the PRODUCTION generation budget (temp 0.2, max_tokens 160).
        lm = dspy.LM("openai/recap", api_base=api_base, api_key="EMPTY",
                     temperature=recap.RECAP_TEMPERATURE, max_tokens=recap.RECAP_MAX_TOKENS,
                     cache=False)  # off: so repeated runs reveal true generation variance
        dspy.configure(lm=lm)
        dspy_metric = make_dspy_metric(judge)

        results = []
        timings = {}
        llm_calls = {}

        def _ncalls():
            return len(lm.history)

        # 1) baseline (production prompt) — uses its own urllib call, not the dspy LM.
        t0 = time.time()
        base_recaps = {r["slug"]: production_recap(r["ctx"], api_base) for r in test_rec}
        timings["baseline"] = time.time() - t0
        results.append(score_system("baseline", base_recaps, test_rec, judge))

        # 2) dspy zero-shot Predict
        c0 = _ncalls(); t0 = time.time()
        zs = PredictRecap(cot=False)
        results.append(score_system("dspy_zeroshot", gen_with_program(zs, test_rec),
                                     test_rec, judge))
        timings["dspy_zeroshot"] = time.time() - t0
        llm_calls["dspy_zeroshot"] = _ncalls() - c0

        # 3) dspy zero-shot CoT
        if not a.skip_cot:
            c0 = _ncalls(); t0 = time.time()
            zc = PredictRecap(cot=True)
            results.append(score_system("dspy_zeroshot_cot",
                                        gen_with_program(zc, test_rec), test_rec, judge))
            timings["dspy_zeroshot_cot"] = time.time() - t0
            llm_calls["dspy_zeroshot_cot"] = _ncalls() - c0

        # 4) BootstrapFewShot-optimized Predict
        from dspy.teleprompt import BootstrapFewShot
        train_ex = build_examples(train_rec)
        c0 = _ncalls(); t0 = time.time()
        tele = BootstrapFewShot(metric=dspy_metric, metric_threshold=a.metric_threshold,
                                max_bootstrapped_demos=a.max_demos,
                                max_labeled_demos=a.max_demos)
        compiled = tele.compile(student=PredictRecap(cot=False), trainset=train_ex)
        opt_calls = _ncalls() - c0
        opt_time = time.time() - t0
        n_demos = len(compiled.gen.demos) if hasattr(compiled.gen, "demos") else 0
        c0 = _ncalls()
        results.append(score_system("dspy_bootstrap",
                                    gen_with_program(compiled, test_rec), test_rec, judge))
        timings["dspy_bootstrap_optimize"] = opt_time
        llm_calls["dspy_bootstrap_optimize"] = opt_calls
        llm_calls["dspy_bootstrap_demos_kept"] = n_demos

        # 5) BootstrapFewShot-optimized CoT (optional)
        if not a.skip_cot:
            c0 = _ncalls(); t0 = time.time()
            tele2 = BootstrapFewShot(metric=dspy_metric, metric_threshold=a.metric_threshold,
                                     max_bootstrapped_demos=a.max_demos,
                                     max_labeled_demos=a.max_demos)
            compiled_cot = tele2.compile(student=PredictRecap(cot=True), trainset=train_ex)
            timings["dspy_bootstrap_cot_optimize"] = time.time() - t0
            llm_calls["dspy_bootstrap_cot_optimize"] = _ncalls() - c0
            results.append(score_system("dspy_bootstrap_cot",
                                        gen_with_program(compiled_cot, test_rec),
                                        test_rec, judge))

        payload = {
            "n_test": len(test_rec), "n_train": len(train_rec),
            "judge_failures": judge.failures,
            "timings_sec": timings, "llm_calls": llm_calls,
            "weights": {"describes_work": 0.30, "concision": 0.15,
                        "faithfulness": 0.35, "status_awareness": 0.20},
            "results": results,
        }
        Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    _print_summary(payload)
    print(f"\nwrote {a.out}")
    return 0


def _print_summary(payload):
    print("\n" + "=" * 78)
    print(f"RECAP QUALITY — N_test={payload['n_test']}  judge_failures={payload['judge_failures']}")
    print("=" * 78)
    hdr = f"{'system':<22} {'composite':>9} " + " ".join(f"{d[:9]:>9}" for d in DIMS)
    print(hdr); print("-" * len(hdr))
    for r in payload["results"]:
        a = r["aggregate"]
        print(f"{r['system']:<22} {a['composite']:>9.3f} " +
              " ".join(f"{a[d]:>9.3f}" for d in DIMS))
    print("\nLLM calls:", json.dumps(payload["llm_calls"]))
    print("Timings(s):", json.dumps({k: round(v, 1) for k, v in payload["timings_sec"].items()}))


if __name__ == "__main__":
    raise SystemExit(main())
