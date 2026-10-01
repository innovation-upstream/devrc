#!/usr/bin/env python3
"""Stage 2 of the call-recording pipeline — LLM extraction over a transcript.

Sends a (truncated) meeting transcript to an OpenRouter chat model and extracts
a STRICT JSON list of actionable items. Pure-ish: the network call is isolated
in the injectable `_caller`, and the parser/validator (`parse_items`) is
unit-testable without a key or network — the same shape `mail-actions/llm.py`
uses, adapted to a transcript: one meeting yields ZERO OR MORE items, not a
single action_required decision.

JSON contract returned:
    {"items": [
        {"task": str, "who": str|null, "due": str|null, "confidence": 0..1}
    ]}
"""
from __future__ import annotations

import json
import os
import re

DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
#: chars of transcript sent to the model (bounds token cost). A 1h call
#: transcribes to ~30-50k chars; this keeps the request inside one context
#: window for the cheap default model. Items named late in a longer call are
#: lost to truncation — the full transcript stays in the DB either way.
TRANSCRIPT_TRUNCATE = 30000

ITEM_KEYS = ("task", "who", "due", "confidence")

SYSTEM_PROMPT = (
    "You read the transcript of a recorded meeting (Signal call) and extract "
    "every ACTION ITEM — things a participant agreed to DO, decisions that "
    "commit someone to work, deadlines set, or follow-ups promised. Ignore "
    "small talk, and do not invent tasks nobody mentioned. Return ONLY a JSON "
    "object, no prose, with EXACTLY this shape: "
    '{"items": [{"task": string (one imperative sentence), "who": string or '
    'null (who committed to it, as named in the transcript), "due": string or '
    'null (deadline as stated, verbatim if one was given), '
    '"confidence": number 0..1}]}. '
    "Bias: an ambiguous maybe is not a task — drop it rather than guess."
)


class ExtractionError(ValueError):
    """Raised when the model output cannot be parsed into valid items."""


def _strip_to_json(text: str) -> str:
    """Pull the first {...} block out of a possibly-fenced model reply."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ExtractionError("no JSON object found in model output")
    return text[start : end + 1]


def parse_items(text: str) -> list:
    """Parse + validate raw model text into a list of item dicts.

    Every item must carry all ITEM_KEYS; unknown keys are dropped (the model
    occasionally adds commentary fields), `who`/`due` collapse to None when
    empty, and `confidence` is clamped to 0..1.
    """
    try:
        obj = json.loads(_strip_to_json(text))
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        raise ExtractionError("JSON must be an object with an 'items' array")
    items = []
    for raw in obj["items"]:
        if not isinstance(raw, dict):
            raise ExtractionError("every item must be a JSON object")
        missing = [k for k in ITEM_KEYS if k not in raw]
        if missing:
            raise ExtractionError(f"item missing keys: {missing}")
        try:
            conf = max(0.0, min(1.0, float(raw["confidence"])))
        except (TypeError, ValueError) as exc:
            raise ExtractionError(
                f"confidence not a number: {raw['confidence']!r}") from exc
        task = str(raw["task"] or "").strip()
        if not task:
            raise ExtractionError("item has an empty 'task'")
        items.append({
            "task": task,
            "who": str(raw["who"] or "").strip() or None,
            "due": str(raw["due"] or "").strip() or None,
            "confidence": conf,
        })
    return items


def build_user_prompt(*, transcript: str) -> str:
    return (transcript or "")[:TRANSCRIPT_TRUNCATE]


def _call_openrouter(model: str, user_prompt: str, api_key: str,
                     timeout: float = 120.0) -> str:
    """POST to OpenRouter; return the assistant message content. Network-only."""
    import requests

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "devrc-signal-recordings",
        },
        json={
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def extract_items(*, transcript: str, model: str | None = None,
                  api_key: str | None = None, _caller=None) -> list:
    """Run one extraction with a single malformed-output retry.

    `model` defaults to `$SIGNAL_EXTRACT_MODEL` then DEFAULT_MODEL; the API
    key comes from `$OPENROUTER_API_KEY` (RuntimeError when absent — the
    caller turns that into the CLI's usual refusal).
    """
    model = model or os.environ.get("SIGNAL_EXTRACT_MODEL") or DEFAULT_MODEL
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    _caller = _caller or _call_openrouter
    prompt = build_user_prompt(transcript=transcript)
    last_err: Exception | None = None
    for _ in range(2):  # one retry on malformed output
        raw = _caller(model, prompt, api_key)
        try:
            return parse_items(raw)
        except ExtractionError as exc:
            last_err = exc
    raise ExtractionError(f"model output invalid after retry: {last_err}")