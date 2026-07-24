#!/usr/bin/env python3
"""Estimate the metric's NOISE FLOOR so a system-vs-system delta can be read honestly.

Two noise sources:
  judge_only         — re-run the LLM-judge K times over a FIXED set of recaps
                       (baseline's, from results.json). Isolates judge nondeterminism.
  generation+judge   — regenerate the baseline recaps K times (temp 0.2) AND re-judge.
                       Isolates the full run-to-run swing of a single fixed system.

If the composite std here is comparable to the observed baseline-vs-DSPy delta, that
delta is noise. Usage: python variance.py --api-base http://127.0.0.1:PORT/v1 [-k 5]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from baseline import production_recap  # noqa: E402
from dataset import load_eval_set  # noqa: E402
from metric import Judge, score_recap  # noqa: E402


def _composite(recaps, records, judge):
    return statistics.mean(
        score_recap(r["ctx"], recaps[r["slug"]], judge)["composite"] for r in records)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", required=True)
    ap.add_argument("-k", type=int, default=5)
    a = ap.parse_args(argv)

    test = load_eval_set()["test"]
    judge = Judge(a.api_base)

    fixed = {i["slug"]: i["recap"]
             for i in next(r for r in json.load(open(HERE / "results.json"))["results"]
                           if r["system"] == "baseline")["items"]}

    judge_only = [_composite(fixed, test, judge) for _ in range(a.k)]

    gen_judge = []
    for _ in range(a.k):
        regen = {r["slug"]: production_recap(r["ctx"], a.api_base) for r in test}
        gen_judge.append(_composite(regen, test, judge))

    def rep(name, xs):
        print(f"{name:18} mean={statistics.mean(xs):.4f} "
              f"std={statistics.pstdev(xs):.4f} min={min(xs):.4f} max={max(xs):.4f} "
              f"spread={max(xs)-min(xs):.4f}")
        return {"mean": statistics.mean(xs), "std": statistics.pstdev(xs),
                "min": min(xs), "max": max(xs), "runs": xs}

    print(f"noise floor over N_test={len(test)}, K={a.k} repeats")
    out = {"judge_only": rep("judge_only", judge_only),
           "generation+judge": rep("generation+judge", gen_judge),
           "judge_failures": judge.failures}
    (HERE / "variance.json").write_text(json.dumps(out, indent=2))
    print("wrote variance.json")


if __name__ == "__main__":
    raise SystemExit(main())
