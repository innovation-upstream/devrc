"""Stage-2 output parser/validator tests (no network, no key)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm  # noqa: E402

GOOD = (
    '{"action_required": true, "who": "Zen Payments", '
    '"ask": "Complete the merchant-account application for Civitai.", '
    '"deadline": "2026-07-05", "amount": null, "confidence": 0.9, '
    '"reason": "Application Incomplete notice asks for missing info."}'
)


def test_parses_good_json():
    ex = llm.parse_extraction(GOOD)
    assert ex.action_required is True
    assert ex.who == "Zen Payments"
    assert ex.deadline == "2026-07-05"
    assert ex.amount is None
    assert 0.0 <= ex.confidence <= 1.0


def test_parses_fenced_json():
    ex = llm.parse_extraction("```json\n" + GOOD + "\n```")
    assert ex.action_required is True


def test_parses_json_with_surrounding_prose():
    ex = llm.parse_extraction("Sure, here you go:\n" + GOOD + "\nHope that helps!")
    assert ex.who == "Zen Payments"


def test_sanity_guard_downgrades_action_with_empty_ask():
    raw = ('{"action_required": true, "who": "X", "ask": "", "deadline": null, '
           '"amount": null, "confidence": 0.8, "reason": "unclear"}')
    ex = llm.parse_extraction(raw)
    assert ex.action_required is False  # downgraded


def test_confidence_clamped():
    raw = ('{"action_required": false, "who": "X", "ask": "n/a", "deadline": null, '
           '"amount": null, "confidence": 5, "reason": "r"}')
    assert llm.parse_extraction(raw).confidence == 1.0


def test_string_bool_coerced():
    raw = ('{"action_required": "true", "who": "X", "ask": "do thing", "deadline": null, '
           '"amount": null, "confidence": "0.5", "reason": "r"}')
    ex = llm.parse_extraction(raw)
    assert ex.action_required is True and ex.confidence == 0.5


def test_malformed_json_raises():
    with pytest.raises(llm.ExtractionError):
        llm.parse_extraction("not json at all")


def test_missing_keys_raises():
    with pytest.raises(llm.ExtractionError):
        llm.parse_extraction('{"action_required": true}')


def test_no_json_object_raises():
    with pytest.raises(llm.ExtractionError):
        llm.parse_extraction("the answer is yes")


def test_extract_retries_once_then_succeeds():
    calls = {"n": 0}

    def fake_caller(model, prompt, key):
        calls["n"] += 1
        return "garbage" if calls["n"] == 1 else GOOD

    ex = llm.extract(from_addr="a@b.com", subject="s", body="b",
                     api_key="test-key", _caller=fake_caller)
    assert ex.action_required is True
    assert calls["n"] == 2  # one retry


def test_extract_raises_after_retry_exhausted():
    def fake_caller(model, prompt, key):
        return "still garbage"

    with pytest.raises(llm.ExtractionError):
        llm.extract(from_addr="a@b.com", subject="s", body="b",
                    api_key="test-key", _caller=fake_caller)


def test_body_truncated_in_prompt():
    prompt = llm.build_user_prompt(from_addr="a@b.com", subject="s", body="x" * 10000)
    assert prompt.count("x") <= llm.BODY_TRUNCATE
