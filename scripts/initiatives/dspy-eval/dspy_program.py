#!/usr/bin/env python3
"""DSPy candidate: a Signature for the recap task + Predict / ChainOfThought modules.

The Signature docstring is the OPTIMIZABLE instruction (DSPy's analogue of the
production SYSTEM_PROMPT). It starts at rough parity with production intent so the
comparison isolates what DSPy adds: bootstrapped few-shot demonstrations (and, for
CoT, an intermediate reasoning field). Optimization (BootstrapFewShot) fills the demo
slots with train examples whose recaps the metric scored highly.
"""
from __future__ import annotations

import json

import dspy


class InitiativeRecap(dspy.Signature):
    """Summarize one software initiative for a status board.

    Write a plain-language recap of ONE to TWO sentences: what the initiative is and
    where it currently stands. Present tense; terse and concrete. Describe the
    substance of the WORK directly — never describe the handoff document, the file,
    or the writeup (do not say "handoff", ".md", "supersedes", "read this", "kickoff
    message"), and do not open with meta like "This initiative…" or "The goal is…".
    Write ONLY from the provided context: never invent a count, date, PR number, name,
    or status the context does not contain. If the context is thin, write a single
    honest clause from whatever IS present rather than fabricating detail. No preamble,
    no bullet points, no markdown, no surrounding quotes. Return only the recap text.
    """

    initiative_context: str = dspy.InputField(
        desc="JSON with momentum, summary, next_step, open_investigations, "
             "recent_messages, recent_commits, open_prs")
    recap: str = dspy.OutputField(desc="the 1-2 sentence recap, substance only")


def ctx_to_input(ctx: dict) -> str:
    return json.dumps(ctx, ensure_ascii=False, indent=2, sort_keys=True)


class PredictRecap(dspy.Module):
    def __init__(self, cot: bool = False):
        super().__init__()
        self.gen = (dspy.ChainOfThought(InitiativeRecap) if cot
                    else dspy.Predict(InitiativeRecap))

    def forward(self, initiative_context: str):
        return self.gen(initiative_context=initiative_context)


def build_examples(records: list[dict]) -> list[dspy.Example]:
    """eval-set records -> dspy Examples carrying both the model input and the raw
    ctx (the metric reads ctx; the program reads initiative_context)."""
    out = []
    for r in records:
        ex = dspy.Example(initiative_context=ctx_to_input(r["ctx"]),
                          ctx=r["ctx"], slug=r["slug"]).with_inputs("initiative_context")
        out.append(ex)
    return out
