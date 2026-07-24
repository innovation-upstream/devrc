#!/usr/bin/env python3
"""Baseline = a faithful reproduction of the PRODUCTION recap generator.

Uses the production `recap.build_messages(ctx)` (SYSTEM_PROMPT = RECAP_INSTRUCTIONS +
ANTI_CONFABULATION_CONTRACT) and the production generation budget (temperature 0.2,
max_tokens 160) against the same vllm-recap endpoint. This is exactly what
`recap.VllmClient.generate` sends — so scoring it measures current production quality.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import recap  # noqa: E402  (production module)
from metric import _openai_chat  # noqa: E402


def production_recap(ctx: dict, api_base: str) -> str:
    """One production-equivalent chat call -> recap text."""
    messages = recap.build_messages(ctx)
    text = _openai_chat(api_base, messages,
                        max_tokens=recap.RECAP_MAX_TOKENS,
                        temperature=recap.RECAP_TEMPERATURE)
    return (text or "").strip()
