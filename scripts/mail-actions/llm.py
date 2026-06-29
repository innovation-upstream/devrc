#!/usr/bin/env python3
"""Stage 2 — LLM extraction over Stage-1 survivors.

Sends subject + from + a truncated body to an OpenRouter chat model and extracts a
STRICT JSON action object. Pure-ish: the network call is isolated in `_call_openrouter`
so the parser/validator (`parse_extraction`) and the sanity guard are unit-testable
without a key or network.

JSON contract returned per mail:
    {
      "action_required": bool,
      "who": str,
      "ask": str,           # one sentence
      "deadline": str|null,
      "amount": str|null,
      "confidence": float,  # 0..1
      "reason": str
    }
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
BODY_TRUNCATE = 4000  # chars of text_body sent to the model (bounds token cost)

SYSTEM_PROMPT = (
    "You triage a personal inbox for ACTION-REQUIRED email. Given one email's sender, "
    "subject and a truncated body, decide if the recipient must DO something (reply, "
    "pay, sign, submit info, approve, schedule) — as opposed to FYI/marketing/receipts. "
    "Return ONLY a JSON object, no prose, with EXACTLY these keys: "
    "action_required (bool), who (string: the person/org awaiting action), "
    "ask (string: one sentence describing what to do; empty string if none), "
    "deadline (string or null), amount (string or null, e.g. '$1,200'), "
    "confidence (number 0..1), reason (short string). "
    "Bias: receipts/newsletters/digests/status updates are NOT action_required."
)

REQUIRED_KEYS = ("action_required", "who", "ask", "deadline", "amount", "confidence", "reason")


@dataclass(frozen=True)
class Extraction:
    action_required: bool
    who: str
    ask: str
    deadline: str | None
    amount: str | None
    confidence: float
    reason: str

    def as_row(self) -> dict:
        return {
            "who": self.who,
            "ask": self.ask,
            "deadline": self.deadline,
            "amount": self.amount,
            "confidence": self.confidence,
            "reason": self.reason,
        }


class ExtractionError(ValueError):
    """Raised when the model output cannot be parsed into a valid Extraction."""


def _coerce_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return bool(v)


def _coerce_float01(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError) as exc:
        raise ExtractionError(f"confidence not a number: {v!r}") from exc
    return max(0.0, min(1.0, f))


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


def parse_extraction(text: str) -> Extraction:
    """Parse + validate raw model text into an Extraction. Raises ExtractionError.

    Applies the deterministic sanity guard: action_required=True with an empty `ask`
    is downgraded to action_required=False (an FYI), because an action with no stated
    ask is not actionable.
    """
    try:
        obj = json.loads(_strip_to_json(text))
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ExtractionError("JSON is not an object")
    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        raise ExtractionError(f"missing keys: {missing}")

    action = _coerce_bool(obj["action_required"])
    ask = str(obj.get("ask") or "").strip()
    # sanity guard: action with no ask → downgrade to fyi.
    if action and not ask:
        action = False

    def _opt(v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    return Extraction(
        action_required=action,
        who=str(obj.get("who") or "").strip(),
        ask=ask,
        deadline=_opt(obj.get("deadline")),
        amount=_opt(obj.get("amount")),
        confidence=_coerce_float01(obj.get("confidence")),
        reason=str(obj.get("reason") or "").strip(),
    )


def build_user_prompt(*, from_addr: str, subject: str, body: str) -> str:
    body = (body or "")[:BODY_TRUNCATE]
    return (
        f"From: {from_addr}\n"
        f"Subject: {subject}\n"
        f"---\n{body}"
    )


def _call_openrouter(model: str, user_prompt: str, api_key: str, timeout: float = 60.0) -> str:
    """POST to OpenRouter; return the assistant message content. Network-only — mocked in tests."""
    import requests

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "devrc-mail-actions",
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


def extract(
    *,
    from_addr: str,
    subject: str,
    body: str,
    model: str | None = None,
    api_key: str | None = None,
    _caller=_call_openrouter,
) -> Extraction:
    """Run one extraction with a single malformed-output retry. `_caller` is injectable."""
    model = model or os.environ.get("MAIL_ACTIONS_MODEL", DEFAULT_MODEL)
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    prompt = build_user_prompt(from_addr=from_addr, subject=subject, body=body)
    last_err: Exception | None = None
    for _ in range(2):  # one retry on malformed output
        raw = _caller(model, prompt, api_key)
        try:
            return parse_extraction(raw)
        except ExtractionError as exc:
            last_err = exc
    raise ExtractionError(f"model output invalid after retry: {last_err}")
