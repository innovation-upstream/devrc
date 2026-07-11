"""schema.validate / vocab_warnings — enum hard-fails, unreadable invariant,
null-vs-husk facets, soft-fail vocab (decision O2)."""
import schema as S


def _valid() -> dict:
    return {
        "schema_version": 1, "session": "s1", "underlying_goal": "ship layer B",
        "goal_categories": ["infra", "feature"], "outcome": "fully_achieved",
        "session_type": "feature_build", "claude_helpfulness": 5,
        "friction_counts": {"wrong_approach": 1}, "friction_detail": ["retried nix build"],
        "primary_success": "shipped the extractor", "brief_summary": "built + tested.",
        "automation_opportunity": {"present": True, "description": "wrap the deploy dance",
                                   "trigger": "hand-typed switch+verify", "leverage": "high",
                                   "evidence": "ran it 3x by hand"},
        "recurring_toil": {"present": True, "description": "manual env export",
                           "category": "env-setup", "frequency_hint": "every session"},
        "workflow_gap": {"present": True, "description": "no status skill",
                         "kind": "missing_tool"},
        "unreadable": False, "unreadable_reason": "",
    }


def test_valid_payload_passes():
    assert S.validate(_valid()) == []


def test_bad_closed_enums_hard_fail():
    for field, bad in (("outcome", "great"), ("session_type", "vibes")):
        p = _valid(); p[field] = bad
        assert any(field in e for e in S.validate(p))
    p = _valid(); p["automation_opportunity"]["leverage"] = "huge"
    assert any("leverage" in e for e in S.validate(p))
    p = _valid(); p["workflow_gap"]["kind"] = "missing_vibes"
    assert any("kind" in e for e in S.validate(p))


def test_helpfulness_range():
    for bad in (0, 6, 3.5, True, "5"):
        p = _valid(); p["claude_helpfulness"] = bad
        assert any("claude_helpfulness" in e for e in S.validate(p))


def test_unreadable_requires_reason():
    p = _valid(); p["unreadable"] = True; p["unreadable_reason"] = ""
    # empty qualitative fields are fine for an unreadable row
    p["outcome"] = ""; p["session_type"] = ""; p["claude_helpfulness"] = 0
    errs = S.validate(p)
    assert any("unreadable_reason" in e for e in errs)
    p["unreadable_reason"] = "transcript truncated"
    assert S.validate(p) == []


def test_reason_must_be_empty_when_readable():
    p = _valid(); p["unreadable_reason"] = "stray reason"
    assert any("unreadable_reason" in e for e in S.validate(p))


def test_null_vs_husk_facets():
    # null facet is valid
    p = _valid(); p["automation_opportunity"] = None; p["recurring_toil"] = None
    p["workflow_gap"] = None
    assert S.validate(p) == []
    # a husk object with no description is rejected (must be null when absent)
    p = _valid(); p["automation_opportunity"] = {"present": False}
    assert any("automation_opportunity.description" in e for e in S.validate(p))


def test_out_of_vocab_categories_soft_fail_only():
    p = _valid()
    p["goal_categories"] = ["quantum"]
    p["friction_counts"] = {"vibes_off": 2}
    p["recurring_toil"]["category"] = "yak-shaving"
    assert S.validate(p) == []                 # NO hard error
    warns = S.vocab_warnings(p)
    assert any("quantum" in w for w in warns)
    assert any("vibes_off" in w for w in warns)
    assert any("yak-shaving" in w for w in warns)


def test_non_dict_rejected():
    assert S.validate([1, 2, 3])
    assert S.validate("nope")


def test_schema_block_self_contained():
    b = S.schema_block()
    assert b["schema_version"] == S.SCHEMA_VERSION
    assert "output-token maximum" in b["anti_confabulation_contract"]
    assert set(b["closed_enums"]["outcome"]) == set(S.OUTCOMES)
    assert "5" in b["helpfulness_anchors"]
