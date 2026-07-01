"""LLM synthesis parser/validator tests — no network, no API key.

Covers: good-JSON parse, fenced/prose stripping, JSON repair-retry, the structural
anti-slop filters (drop no-evidence proposals, strip invented refs, hard-cap to `top`,
ci_verifiable-first ordering, effort normalization).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm  # noqa: E402

REFS = {"devrc/scripts/a.py:12", "homelab/x.yaml:3", "civitai/big.ts:0"}

GOOD = """
{"proposals": [
  {"title": "Un-skip the flaky auth test", "repo": "devrc",
   "evidence": ["devrc/scripts/a.py:12"], "why": "restores CI coverage",
   "effort": "S", "approach": "remove the skip mark, fix the fixture", "ci_verifiable": true}
]}
"""


def test_parses_good_json():
    props = llm.parse_proposals(GOOD, top=5, valid_refs=REFS)
    assert len(props) == 1
    assert props[0].title.startswith("Un-skip")
    assert props[0].effort == "S"
    assert props[0].ci_verifiable is True
    assert props[0].evidence == ["devrc/scripts/a.py:12"]


def test_parses_fenced_json():
    props = llm.parse_proposals("```json\n" + GOOD + "\n```", top=5, valid_refs=REFS)
    assert len(props) == 1


def test_parses_with_surrounding_prose():
    props = llm.parse_proposals("Here you go:\n" + GOOD + "\nThanks!", top=5, valid_refs=REFS)
    assert len(props) == 1


def test_invalid_json_raises():
    with pytest.raises(llm.SynthesisError):
        llm.parse_proposals("not json at all", top=5)


def test_missing_proposals_key_raises():
    with pytest.raises(llm.SynthesisError):
        llm.parse_proposals('{"items": []}', top=5)


def test_drops_proposal_with_no_evidence():
    text = '{"proposals": [{"title": "vague idea", "repo": "x", "evidence": [], "why": "", "effort": "M", "approach": "", "ci_verifiable": false}]}'
    props = llm.parse_proposals(text, top=5)
    assert props == []


def test_strips_invented_ref_and_drops_if_empty():
    text = '{"proposals": [{"title": "made up", "repo": "x", "evidence": ["ghost/file.py:99"], "why": "w", "effort": "S", "approach": "a", "ci_verifiable": true}]}'
    props = llm.parse_proposals(text, top=5, valid_refs=REFS)
    assert props == []  # the only ref was invented → dropped


def test_keeps_valid_ref_strips_invented():
    text = ('{"proposals": [{"title": "mixed", "repo": "devrc", '
            '"evidence": ["devrc/scripts/a.py:12", "ghost/file.py:99"], '
            '"why": "w", "effort": "S", "approach": "a", "ci_verifiable": true}]}')
    props = llm.parse_proposals(text, top=5, valid_refs=REFS)
    assert len(props) == 1
    assert props[0].evidence == ["devrc/scripts/a.py:12"]


def test_hard_cap_to_top():
    items = ",".join(
        f'{{"title": "p{i}", "repo": "r", "evidence": ["devrc/scripts/a.py:12"], '
        f'"why": "w", "effort": "M", "approach": "a", "ci_verifiable": false}}'
        for i in range(10)
    )
    props = llm.parse_proposals(f'{{"proposals": [{items}]}}', top=3, valid_refs=REFS)
    assert len(props) == 3


def test_ci_verifiable_sorted_first():
    text = ('{"proposals": ['
            '{"title": "judgey", "repo": "r", "evidence": ["homelab/x.yaml:3"], "why": "w", "effort": "M", "approach": "a", "ci_verifiable": false},'
            '{"title": "testfix", "repo": "r", "evidence": ["devrc/scripts/a.py:12"], "why": "w", "effort": "S", "approach": "a", "ci_verifiable": true}'
            ']}')
    props = llm.parse_proposals(text, top=5, valid_refs=REFS)
    assert props[0].title == "testfix"  # ci_verifiable bubbled to top
    assert props[1].title == "judgey"


def test_effort_normalized():
    text = '{"proposals": [{"title": "t", "repo": "r", "evidence": ["homelab/x.yaml:3"], "why": "w", "effort": "extra-large", "approach": "a", "ci_verifiable": false}]}'
    props = llm.parse_proposals(text, top=5, valid_refs=REFS)
    assert props[0].effort == "M"  # unknown → default M


def test_ref_known_tolerates_line_drop():
    assert llm._ref_known("devrc/scripts/a.py", {"devrc/scripts/a.py:12"})
    assert llm._ref_known("devrc/scripts/a.py:12", {"devrc/scripts/a.py:12"})
    assert not llm._ref_known("other/file.py", {"devrc/scripts/a.py:12"})


# ---- synthesize() with mocked caller (no network) -----------------------------

def test_synthesize_uses_injected_caller():
    calls = {}

    def fake_caller(model, system, user, api_key, timeout=90.0):
        calls["model"] = model
        calls["user"] = user
        return GOOD

    cands = [{"kind": "marker", "ref": "devrc/scripts/a.py:12", "text": "TODO fix"}]
    res = llm.synthesize(cands, top=5, model="m", api_key="k", _caller=fake_caller)
    assert len(res.proposals) == 1
    assert res.model == "m"
    assert res.approx_prompt_tokens > 0
    assert "devrc/scripts/a.py:12" in calls["user"]


def test_synthesize_retries_on_bad_json():
    seq = ["garbage", GOOD]

    def flaky(model, system, user, api_key, timeout=90.0):
        return seq.pop(0)

    cands = [{"kind": "marker", "ref": "devrc/scripts/a.py:12", "text": "t"}]
    res = llm.synthesize(cands, top=5, api_key="k", _caller=flaky)
    assert len(res.proposals) == 1


def test_synthesize_raises_after_retry():
    def always_bad(model, system, user, api_key, timeout=90.0):
        return "still garbage"

    cands = [{"kind": "marker", "ref": "devrc/scripts/a.py:12", "text": "t"}]
    with pytest.raises(llm.SynthesisError):
        llm.synthesize(cands, top=5, api_key="k", _caller=always_bad)


def test_synthesize_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm.synthesize([{"kind": "marker", "ref": "x:1", "text": "t"}], _caller=lambda *a, **k: GOOD)
