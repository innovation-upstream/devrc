"""consolidate — union / missing / conflict / schema-quarantine over a fake
results/<run-id>/ dir (mocks the subagent fan-out by dropping result.json files)."""
import json

import consolidate


def _valid(session):
    return {
        "schema_version": 1, "session": session, "underlying_goal": "g",
        "goal_categories": ["infra"], "outcome": "fully_achieved",
        "session_type": "feature_build", "claude_helpfulness": 4,
        "friction_counts": {}, "friction_detail": [], "primary_success": "s",
        "brief_summary": "b", "automation_opportunity": None,
        "recurring_toil": None, "workflow_gap": None,
        "unreadable": False, "unreadable_reason": "",
    }


def _drop(dir_, name, payload):
    p = dir_ / f"{name}.result.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_clean_union(tmp_path):
    _drop(tmp_path, "s1", _valid("s1"))
    _drop(tmp_path, "s2", _valid("s2"))
    r = consolidate.consolidate(["s1", "s2"], tmp_path)
    assert sorted(i["session"] for i in r["emitted_ok"]) == ["s1", "s2"]
    assert r["missing"] == [] and r["conflicts"] == [] and r["rejected"] == []


def test_missing_reported_not_emitted(tmp_path):
    _drop(tmp_path, "s1", _valid("s1"))
    r = consolidate.consolidate(["s1", "s2"], tmp_path)
    assert [i["session"] for i in r["emitted_ok"]] == ["s1"]
    assert r["missing"] == ["s2"]


def test_conflict_neither_emitted(tmp_path):
    # two files whose payloads both claim session s1 → conflict, both quarantined
    _drop(tmp_path, "s1", _valid("s1"))
    _drop(tmp_path, "s1-dup", _valid("s1"))
    r = consolidate.consolidate(["s1"], tmp_path)
    assert r["conflicts"] == ["s1"]
    assert r["emitted_ok"] == []
    assert (tmp_path / "rejected" / "s1.result.json").exists()


def test_schema_invalid_quarantined(tmp_path):
    bad = _valid("s1"); bad["outcome"] = "bogus"
    _drop(tmp_path, "s1", bad)
    r = consolidate.consolidate(["s1"], tmp_path)
    assert r["emitted_ok"] == []
    assert r["rejected"] and r["rejected"][0]["session"] == "s1"
    assert (tmp_path / "rejected" / "s1.result.json").exists()


def test_unparseable_quarantined(tmp_path):
    p = tmp_path / "s1.result.json"
    p.write_text("{not json", encoding="utf-8")
    r = consolidate.consolidate(["s1"], tmp_path)
    assert r["rejected"] and "unparseable" in r["rejected"][0]["errors"][0]


def test_no_quarantine_when_disabled(tmp_path):
    bad = _valid("s1"); bad["outcome"] = "bogus"
    _drop(tmp_path, "s1", bad)
    consolidate.consolidate(["s1"], tmp_path, quarantine=False)
    assert (tmp_path / "s1.result.json").exists()   # left in place
    assert not (tmp_path / "rejected").exists()
