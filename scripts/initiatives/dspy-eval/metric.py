#!/usr/bin/env python3
"""Recap-quality metric — the crux of the eval.

A recap is scored on FOUR dimensions, combined into a 0..1 composite. Two are
DETERMINISTIC (defensible, reproducible, zero model cost) and two are an LLM-judge
(the vllm-recap 7B, blind to which system produced the recap):

  describes_work  (det, 0.30) — the DOCUMENTED weak spot. Penalize doc-meta: a recap
                  that talks about the handoff *doc*/file ("handoff", ".md",
                  "supersedes", "read this handoff", "kickoff message") or opens with
                  meta ("This initiative…", "The goal is…") instead of the substance.
  concision       (det, 0.15) — 1-2 sentences, no bullets/markdown/quotes/newlines.
  faithfulness    (judge+det, 0.35) — no claim/number/name absent from the context
                  (the anti-confabulation intent). A deterministic guard HARD-CAPS the
                  judge score when the recap cites a number/PR# not present in context.
  status_aware    (judge, 0.20) — conveys where the work stands, consistent with
                  the given `momentum`.

composite = 0.30*describes_work + 0.15*concision + 0.35*faithfulness + 0.20*status

LIMITATIONS (documented honestly — see the findings doc):
  * The judge is the SAME 7B model family that generates the recaps → correlated
    blind spots; a stronger independent judge (e.g. a GPT-4-class model) would be
    more trustworthy. The judge is at least BLIND to the producing system.
  * LLM-judge scores wobble even at temperature 0 (vllm batching/sampling) — treat
    composite deltas below ~0.05 as noise, especially at N=8.
  * The det. describes_work regex can false-positive (a recap legitimately about a
    doc feature) or miss doc-meta phrased without a trigger word.
  * The number guard only catches fabricated *numbers*, not subtler hallucinations.
"""
from __future__ import annotations

import json
import re
import urllib.request

# --------------------------------------------------------------------------- #
# Deterministic: describes_work (doc-meta penalty)
# --------------------------------------------------------------------------- #
# Each DISTINCT family that fires subtracts 0.5 (2 families -> 0). These target the
# documented failure: describing the handoff doc/file/meta instead of the work.
_META_PATTERNS = {
    "handoff_word": re.compile(r"\bhand[- ]?off", re.I),
    "md_file": re.compile(r"\.md\b|\bclaudedocs\b", re.I),
    "supersedes": re.compile(r"\bsupersed", re.I),
    "read_doc": re.compile(r"\bread[- ](first|this|the)\b|\bread the (handoff|doc|file)", re.I),
    "kickoff_msg": re.compile(r"\bkickoff message\b|\bcopy[- ]paste\b", re.I),
    "prior_arc": re.compile(r"\bprior arc\b|\bthis handoff\b|\bthis doc(ument)?\b", re.I),
    "meta_opener": re.compile(r"^\s*(this (initiative|session|work|document)|the (goal|aim|objective) (is|of)|this (is|document|summary))\b", re.I),
    "doc_note": re.compile(r"\bthe (doc|document|note|writeup|write-up)\b", re.I),
}


def describes_work_score(recap_text: str) -> tuple[float, list[str]]:
    hits = [name for name, pat in _META_PATTERNS.items() if pat.search(recap_text or "")]
    return max(0.0, 1.0 - 0.5 * len(hits)), hits


# --------------------------------------------------------------------------- #
# Deterministic: concision
# --------------------------------------------------------------------------- #
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_FORMAT_FLAGS = re.compile(r"(^\s*[-*>]\s)|(\n\s*[-*>]\s)|(\*\*)|(^[\"'])|([\"']$)|(\n\n)", re.M)


def concision_score(recap_text: str) -> tuple[float, int, list[str]]:
    t = (recap_text or "").strip()
    if not t:
        return 0.0, 0, ["empty"]
    n = len([s for s in _SENT_SPLIT.split(t) if s.strip()])
    if n <= 2:
        base = 1.0
    elif n == 3:
        base = 0.6
    else:
        base = 0.2
    flags = []
    if _FORMAT_FLAGS.search(t):
        base -= 0.3
        flags.append("markdown/bullets/quotes")
    return max(0.0, min(1.0, base)), n, flags


# --------------------------------------------------------------------------- #
# Deterministic: number-fabrication guard (anti-confab)
# --------------------------------------------------------------------------- #
_NUM = re.compile(r"#?\d+")


def _numbers(text: str) -> set[str]:
    return {m.lstrip("#") for m in _NUM.findall(text or "")}


def fabricated_numbers(ctx: dict, recap_text: str) -> set[str]:
    ctx_blob = json.dumps(ctx, ensure_ascii=False)
    return _numbers(recap_text) - _numbers(ctx_blob)


# --------------------------------------------------------------------------- #
# LLM-judge (blind): faithfulness + status_awareness
# --------------------------------------------------------------------------- #
_JUDGE_SYS = (
    "You are a strict, impartial evaluator of one-to-two-sentence status-board recaps. "
    "You are given a CONTEXT object (JSON) about a software initiative and a candidate "
    "RECAP. Judge ONLY the recap against the context. Respond with a single JSON object "
    "and nothing else: {\"faithfulness\": <0..1>, \"faithfulness_reason\": <short>, "
    "\"status_awareness\": <0..1>, \"status_awareness_reason\": <short>}.\n"
    "faithfulness = 1.0 only if EVERY specific claim, number, name, and status in the "
    "recap is supported by the context; lower it sharply for anything invented or "
    "unsupported. status_awareness = 1.0 if the recap clearly conveys where the work "
    "currently stands (in progress / shipped / blocked / stalled) consistent with the "
    "context's momentum and next_step; 0.0 if it says nothing about current standing."
)

_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def _openai_chat(api_base: str, messages: list[dict], *, max_tokens: int,
                 temperature: float, timeout: float = 60.0) -> str:
    body = json.dumps({
        "model": "recap", "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens, "stream": False,
    }).encode()
    req = urllib.request.Request(
        api_base.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


class Judge:
    """Blind LLM-judge over the vllm-recap OpenAI endpoint (temperature 0)."""

    def __init__(self, api_base: str):
        self.api_base = api_base
        self.failures = 0

    def score(self, ctx: dict, recap_text: str) -> dict:
        user = ("CONTEXT (JSON):\n" + json.dumps(ctx, ensure_ascii=False, indent=2)
                + "\n\nRECAP:\n" + (recap_text or "").strip()
                + "\n\nReturn ONLY the JSON object.")
        msgs = [{"role": "system", "content": _JUDGE_SYS},
                {"role": "user", "content": user}]
        for _ in range(2):
            try:
                raw = _openai_chat(self.api_base, msgs, max_tokens=220, temperature=0.0)
                m = _JSON_OBJ.search(raw)
                obj = json.loads(m.group(0)) if m else {}
                return {
                    "faithfulness": _clip01(obj.get("faithfulness")),
                    "faithfulness_reason": str(obj.get("faithfulness_reason", ""))[:200],
                    "status_awareness": _clip01(obj.get("status_awareness")),
                    "status_reason": str(obj.get("status_awareness_reason", ""))[:200],
                    "ok": True,
                }
            except Exception:  # noqa: BLE001
                continue
        self.failures += 1
        # Neutral fallback so one judge hiccup can't skew the mean silently.
        return {"faithfulness": 0.5, "faithfulness_reason": "JUDGE_FAILED",
                "status_awareness": 0.5, "status_reason": "JUDGE_FAILED", "ok": False}


def _clip01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


# --------------------------------------------------------------------------- #
# Composite
# --------------------------------------------------------------------------- #
WEIGHTS = {"describes_work": 0.30, "concision": 0.15,
           "faithfulness": 0.35, "status_awareness": 0.20}
_FAB_CAP = 0.4  # hard cap on faithfulness when the recap cites an absent number


def score_recap(ctx: dict, recap_text: str, judge: Judge) -> dict:
    dw, dw_hits = describes_work_score(recap_text)
    con, n_sent, con_flags = concision_score(recap_text)
    fab = fabricated_numbers(ctx, recap_text)
    j = judge.score(ctx, recap_text)

    faith = j["faithfulness"]
    if fab:
        faith = min(faith, _FAB_CAP)
    status = j["status_awareness"]

    dims = {"describes_work": dw, "concision": con,
            "faithfulness": faith, "status_awareness": status}
    composite = sum(WEIGHTS[k] * dims[k] for k in WEIGHTS)
    return {
        "composite": composite,
        "dims": dims,
        "detail": {
            "doc_meta_hits": dw_hits,
            "n_sentences": n_sent,
            "concision_flags": con_flags,
            "fabricated_numbers": sorted(fab),
            "judge_faithfulness_raw": j["faithfulness"],
            "judge_ok": j["ok"],
            "faithfulness_reason": j["faithfulness_reason"],
            "status_reason": j["status_reason"],
        },
    }


def make_dspy_metric(judge: Judge):
    """Return a DSPy-compatible metric: (example, pred, trace=None) -> composite float.
    Optimization targets EXACTLY the composite we report."""
    def _m(example, pred, trace=None):
        ctx = example.ctx if hasattr(example, "ctx") else example["ctx"]
        text = getattr(pred, "recap", None) or getattr(pred, "recap_text", "") or ""
        return score_recap(ctx, text, judge)["composite"]
    return _m
