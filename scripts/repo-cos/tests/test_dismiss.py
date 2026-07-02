#!/usr/bin/env python3
"""Recommendation-level DISMISSAL tests — the repo-vs-recommendation split.

The headline case is Zach's EXACT real reply (positional, against the last emailed digest):
    1. approve
    2. we dont own the 3d model feature, skip
    3. not needed, skip
    4. skip
    5. kubeclaw is paused
Correct interpretation: #2,#3 (civitai) + #4 (datapacket) → DISMISS those recommendations
but KEEP the repos; #5 (kubeclaw-embed) → PAUSE the repo; #1 → approve (no-op). The old
repo-only parser wrongly excluded civitai/datapacket entirely.

Covers: parse_reply precedence, the dismissed-evidence store + persistence, filter_candidates
(ref-format matching prescan), and scan.py integration (a dismissed proposal's candidates are
suppressed BEFORE synthesis while the repo's OTHER candidates still pass). No network, no LLM.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import exclusions  # noqa: E402
import prescan  # noqa: E402
import digest  # noqa: E402
import scan  # noqa: E402


# The 5-proposal digest Zach ACTUALLY SAW. Positions 1-5 → repos
# devrc, civitai, civitai, datapacket-talos, kubeclaw-embed.
EMAILED = [
    {"repo": "devrc", "title": "Pin flake input",
     "evidence": ["devrc/flake.lock:12"]},
    {"repo": "civitai", "title": "Remove 3d-model dead code",
     "evidence": ["civitai/docs/3d-models-followups.md:103",
                  "civitai/src/3d/model.ts:44"]},
    {"repo": "civitai", "title": "Add missing test",
     "evidence": ["civitai/src/api/handler.ts:210"]},
    {"repo": "datapacket-talos", "title": "Fix skipped test",
     "evidence": ["datapacket-talos/test/net_test.go:8"]},
    {"repo": "kubeclaw-embed", "title": "Split large file",
     "evidence": ["kubeclaw-embed/main.go:0"]},
]

# Zach's exact reply.
ZACH_REPLY = (
    "1. approve\n"
    "2. we dont own the 3d model feature, skip\n"
    "3. not needed, skip\n"
    "4. skip\n"
    "5. kubeclaw is paused\n"
)

DEFAULT_REPOS = [
    "~/workspace/devrc",
    "~/workspace/kubeclaw-embed",
    "~/workspace/civit/civitai",
    "~/workspace/civit/datapacket-talos",
]


def _alias():
    return exclusions.build_alias_map(DEFAULT_REPOS)


# ---- headline: Zach's exact reply ---------------------------------------------------

def test_zach_exact_reply_splits_dismiss_from_pause():
    parsed = exclusions.parse_reply(ZACH_REPLY, EMAILED, alias_map=_alias())

    # #5 kubeclaw is paused → REPO exclusion (non-permanent), and it's the ONLY exclude.
    excluded = {e["repo"] for e in parsed["exclude"]}
    assert excluded == {"kubeclaw-embed"}
    kub = next(e for e in parsed["exclude"] if e["repo"] == "kubeclaw-embed")
    assert kub["permanent"] is False

    # civitai + datapacket-talos stay in scope (NOT excluded) — the real bug being fixed.
    assert "civitai" not in excluded
    assert "datapacket-talos" not in excluded

    # #2, #3 (civitai) and #4 (datapacket) → dismissed recommendations, keyed by evidence.
    dismissed_refs = set()
    for d in parsed["dismiss"]:
        dismissed_refs.update(d["evidence"])
    assert dismissed_refs == {
        # #2 both evidence refs
        "civitai/docs/3d-models-followups.md:103",
        "civitai/src/3d/model.ts:44",
        # #3
        "civitai/src/api/handler.ts:210",
        # #4
        "datapacket-talos/test/net_test.go:8",
    }
    # #1 "approve" contributed nothing.
    assert not any("flake.lock" in r for r in dismissed_refs)
    assert parsed["resume"] == []


def test_zach_exact_reply_dismiss_carries_repo_and_reason():
    parsed = exclusions.parse_reply(ZACH_REPLY, EMAILED, alias_map=_alias())
    by_repo = {}
    for d in parsed["dismiss"]:
        by_repo.setdefault(d["repo"], []).extend(d["evidence"])
    assert set(by_repo) == {"civitai", "datapacket-talos"}
    # each dismiss entry keeps the reason line it came from
    reasons = " ".join(d["reason"] for d in parsed["dismiss"])
    assert "skip" in reasons


# ---- precedence -------------------------------------------------------------------

def test_precedence_paused_is_repo_not_dismiss():
    # "kubeclaw is paused" → repo exclusion, NOT a dismissal, even at a position with evidence.
    parsed = exclusions.parse_reply("5. kubeclaw is paused\n", EMAILED, alias_map=_alias())
    assert {e["repo"] for e in parsed["exclude"]} == {"kubeclaw-embed"}
    assert parsed["dismiss"] == []


def test_precedence_not_code_owner_is_repo_permanent():
    # "not the code owner" → repo exclusion, PERMANENT, not a dismissal.
    parsed = exclusions.parse_reply("2. not the code owner\n", EMAILED, alias_map=_alias())
    excl = parsed["exclude"]
    assert len(excl) == 1
    assert excl[0]["repo"] == "civitai"
    assert excl[0]["permanent"] is True
    assert parsed["dismiss"] == []


def test_precedence_not_needed_skip_is_dismiss():
    # "not needed, skip" → dismiss the proposal, repo stays.
    parsed = exclusions.parse_reply("3. not needed, skip\n", EMAILED, alias_map=_alias())
    assert parsed["exclude"] == []
    refs = {r for d in parsed["dismiss"] for r in d["evidence"]}
    assert refs == {"civitai/src/api/handler.ts:210"}


def test_precedence_dont_own_feature_skip_is_dismiss_not_owner():
    # THE key case: "we dont own the 3d model feature, skip" is about a FEATURE — it must NOT
    # match the owner-permanent regex (that's "not owner", not "dont own") and must NOT
    # exclude the repo. It's a recommendation dismissal.
    parsed = exclusions.parse_reply(
        "2. we dont own the 3d model feature, skip\n", EMAILED, alias_map=_alias())
    assert parsed["exclude"] == []          # repo NOT excluded
    refs = {r for d in parsed["dismiss"] for r in d["evidence"]}
    assert refs == {
        "civitai/docs/3d-models-followups.md:103",
        "civitai/src/3d/model.ts:44",
    }


def test_bare_skip_is_dismiss():
    parsed = exclusions.parse_reply("4. skip\n", EMAILED, alias_map=_alias())
    assert parsed["exclude"] == []
    refs = {r for d in parsed["dismiss"] for r in d["evidence"]}
    assert refs == {"datapacket-talos/test/net_test.go:8"}


def test_not_relevant_is_dismiss_not_repo():
    # "not relevant" targets the PROPOSAL, not the repo → dismiss, repo stays.
    parsed = exclusions.parse_reply("3. not relevant\n", EMAILED, alias_map=_alias())
    assert parsed["exclude"] == []
    refs = {r for d in parsed["dismiss"] for r in d["evidence"]}
    assert refs == {"civitai/src/api/handler.ts:210"}


def test_not_ours_is_repo_permanent_not_dismiss():
    # "not ours" (vs "not needed"/"not relevant") is a REPO ownership statement → permanent.
    parsed = exclusions.parse_reply("3. not ours\n", EMAILED, alias_map=_alias())
    assert parsed["dismiss"] == []
    excl = parsed["exclude"]
    assert len(excl) == 1 and excl[0]["repo"] == "civitai" and excl[0]["permanent"] is True


def test_dismiss_without_evidence_is_noop():
    # position 1 (devrc) has evidence, but a name-only line (no position) can't dismiss —
    # there's no proposal to look up.
    parsed = exclusions.parse_reply("skip the civitai thing\n", EMAILED, alias_map=_alias())
    # "civitai" alias resolves the repo, but with no positional proposal there's no evidence,
    # so nothing to dismiss (and skip is not a repo-pause) → no-op.
    assert parsed["dismiss"] == []
    assert parsed["exclude"] == []


def test_resume_beats_dismiss_on_same_line():
    parsed = exclusions.parse_reply("2. resume this, don't skip\n", EMAILED, alias_map=_alias())
    assert parsed["resume"] == ["civitai"]
    assert parsed["dismiss"] == []


# ---- apply + persistence ----------------------------------------------------------

def test_apply_merges_dismiss_into_state():
    state = {"repos": {}, "dismissed": {}}
    parsed = exclusions.parse_reply(ZACH_REPLY, EMAILED, alias_map=_alias())
    exclusions.apply(state, parsed, source="reply", now="2026-07-02T08:00:00-05:00")
    assert set(state["dismissed"]) == {
        "civitai/docs/3d-models-followups.md:103",
        "civitai/src/3d/model.ts:44",
        "civitai/src/api/handler.ts:210",
        "datapacket-talos/test/net_test.go:8",
    }
    e = state["dismissed"]["civitai/src/api/handler.ts:210"]
    assert e["repo"] == "civitai"
    assert e["dismissed_at"] == "2026-07-02T08:00:00-05:00"
    assert "skip" in e["reason"]
    # and only kubeclaw-embed is a repo exclusion
    assert set(state["repos"]) == {"kubeclaw-embed"}


def test_apply_dismiss_accumulates_across_calls():
    state = {"repos": {}, "dismissed": {}}
    exclusions.apply(state, {"exclude": [], "resume": [],
                             "dismiss": [{"evidence": ["a/x.py:1"], "reason": "skip", "repo": "a"}]},
                     now="2026-07-01T00:00:00Z")
    exclusions.apply(state, {"exclude": [], "resume": [],
                             "dismiss": [{"evidence": ["b/y.py:2"], "reason": "skip", "repo": "b"}]},
                     now="2026-07-02T00:00:00Z")
    assert set(state["dismissed"]) == {"a/x.py:1", "b/y.py:2"}
    # re-touching an existing ref keeps its original dismissed_at
    exclusions.apply(state, {"exclude": [], "resume": [],
                             "dismiss": [{"evidence": ["a/x.py:1"], "reason": "skip again", "repo": "a"}]},
                     now="2026-07-03T00:00:00Z")
    assert state["dismissed"]["a/x.py:1"]["dismissed_at"] == "2026-07-01T00:00:00Z"


def test_dismiss_state_roundtrips(tmp_path):
    p = tmp_path / "exclusions.json"
    state = {"repos": {}, "dismissed": {}}
    parsed = exclusions.parse_reply(ZACH_REPLY, EMAILED, alias_map=_alias())
    exclusions.apply(state, parsed, source="reply", now="2026-07-02T08:00:00-05:00")
    exclusions.save_state(state, p)

    loaded = exclusions.load_state(p)
    assert set(loaded["dismissed"]) == set(state["dismissed"])
    assert set(loaded["repos"]) == {"kubeclaw-embed"}


def test_apply_with_older_state_missing_dismissed_key():
    # apply must tolerate a state dict with no "dismissed" (older file already loaded).
    state = {"repos": {"kubeclaw-embed": {"permanent": False}}}
    exclusions.apply(state, {"exclude": [], "resume": [],
                             "dismiss": [{"evidence": ["a/x.py:1"], "reason": "skip", "repo": "a"}]})
    assert state["dismissed"]["a/x.py:1"]["repo"] == "a"


# ---- filter_candidates ------------------------------------------------------------

def _cand(repo, file, line):
    return prescan.Candidate(repo=repo, kind="marker", file=file, line=line, text="x")


def test_filter_candidates_drops_dismissed_ref():
    state = {"repos": {}, "dismissed": {
        "civitai/src/api/handler.ts:210": {"reason": "skip", "repo": "civitai"}}}
    cands = [
        _cand("civitai", "src/api/handler.ts", 210),   # dismissed → dropped
        _cand("civitai", "src/other.ts", 5),           # kept (same repo, diff proposal)
        _cand("datapacket-talos", "test/net_test.go", 8),  # kept
    ]
    kept, dropped = exclusions.filter_candidates(cands, state)
    assert [c.ref for c in dropped] == ["civitai/src/api/handler.ts:210"]
    assert {c.ref for c in kept} == {
        "civitai/src/other.ts:5", "datapacket-talos/test/net_test.go:8"}


def test_filter_candidates_ref_format_matches_prescan():
    # the dismissed key stored from a proposal's evidence must equal a FRESH candidate's .ref
    # so a re-scan of the same signal is suppressed. Build the candidate the way prescan does.
    cand = _cand("civitai", "docs/3d-models-followups.md", 103)
    assert cand.ref == "civitai/docs/3d-models-followups.md:103"
    state = {"repos": {}, "dismissed": {cand.ref: {"reason": "skip", "repo": "civitai"}}}
    kept, dropped = exclusions.filter_candidates([cand], state)
    assert kept == [] and len(dropped) == 1


def test_filter_candidates_file_level_ref_no_line():
    # a large_file/churn candidate has line 0 → ref has no ':line' suffix; dismissal by that
    # bare ref still matches.
    cand = _cand("kubeclaw-embed", "main.go", 0)
    assert cand.ref == "kubeclaw-embed/main.go"
    state = {"repos": {}, "dismissed": {"kubeclaw-embed/main.go": {"repo": "kubeclaw-embed"}}}
    kept, dropped = exclusions.filter_candidates([cand], state)
    assert kept == [] and len(dropped) == 1


def test_filter_candidates_empty_dismissed_keeps_all():
    cands = [_cand("a", "x.py", 1), _cand("b", "y.py", 2)]
    kept, dropped = exclusions.filter_candidates(cands, {"repos": {}, "dismissed": {}})
    assert kept == cands and dropped == []


def test_filter_candidates_accepts_dicts():
    state = {"dismissed": {"a/x.py:1": {}}}
    cands = [{"ref": "a/x.py:1"}, {"ref": "a/y.py:2"}]
    kept, dropped = exclusions.filter_candidates(cands, state)
    assert dropped == [{"ref": "a/x.py:1"}]
    assert kept == [{"ref": "a/y.py:2"}]


# ---- end-to-end: parse → apply → filter (the guarantee) ---------------------------

def test_dismiss_then_rescan_suppresses_same_signal():
    # Simulate the full loop: Zach dismisses #3 (civitai handler test). On the NEXT scan the
    # same signal re-surfaces as a candidate — filter_candidates must drop it while civitai's
    # other candidates survive.
    state = {"repos": {}, "dismissed": {}}
    parsed = exclusions.parse_reply("3. not needed, skip\n", EMAILED, alias_map=_alias())
    exclusions.apply(state, parsed)

    fresh = [
        _cand("civitai", "src/api/handler.ts", 210),   # the dismissed one
        _cand("civitai", "src/new_feature.ts", 12),    # a NEW civitai candidate
    ]
    kept, dropped = exclusions.filter_candidates(fresh, state)
    assert [c.ref for c in dropped] == ["civitai/src/api/handler.ts:210"]
    assert [c.ref for c in kept] == ["civitai/src/new_feature.ts:12"]


# ---- visibility: dismissed_entries + format_state + digest footer -----------------

def test_dismissed_entries_sorted():
    state = {"dismissed": {
        "b/y.py:2": {"reason": "skip", "repo": "b", "dismissed_at": "t2"},
        "a/x.py:1": {"reason": "nah", "repo": "a", "dismissed_at": "t1"},
    }}
    got = exclusions.dismissed_entries(state)
    assert [e["ref"] for e in got] == ["a/x.py:1", "b/y.py:2"]
    assert got[0]["reason"] == "nah"


def test_format_state_shows_dismissed():
    state = {"repos": {}, "dismissed": {
        "civitai/src/api/handler.ts:210": {"reason": "3. not needed, skip",
                                           "repo": "civitai",
                                           "dismissed_at": "2026-07-02T08:00:00-05:00"}}}
    out = exclusions.format_state(state)
    assert "dismissed" in out.lower()
    assert "civitai/src/api/handler.ts:210" in out
    assert "not needed" in out


def test_digest_dismissed_footer():
    from datetime import date
    body = digest.render([], today=date(2026, 7, 2), dismissed_count=3)
    assert "Dismissed 3 past proposal(s)" in body


def test_digest_no_dismissed_footer_when_zero():
    from datetime import date
    body = digest.render([], today=date(2026, 7, 2), dismissed_count=0)
    assert "Dismissed" not in body


# ---- scan.py integration (mock prescan + llm + feedback) --------------------------

class _RealishProp:
    def __init__(self, title, repo="r", evidence=None):
        self.title, self.repo = title, repo
        self.evidence = evidence or [f"{repo}/f.py:1"]
        self.why, self.effort, self.approach, self.ci_verifiable = "w", "S", "a", True

    def as_dict(self):
        return {"title": self.title, "repo": self.repo, "evidence": self.evidence,
                "why": self.why, "effort": self.effort, "approach": self.approach,
                "ci_verifiable": self.ci_verifiable}


class _FB:
    def __init__(self, text):
        self.reply_text = text
        self.prev_proposals = []
        self.replied_at = "2026-07-02T08:00:00-05:00"

    def prev_summary(self):
        return []


def test_scan_dismiss_filters_candidate_but_keeps_repo(tmp_path, monkeypatch):
    """A reply dismissing proposal X → X's evidence candidate is filtered out BEFORE
    synthesis, while the repo's OTHER candidates still pass and the repo is NOT excluded."""
    civ = tmp_path / "civitai"
    civ.mkdir()
    # two markers in civitai: one is the dismissed proposal's evidence, one is unrelated.
    (civ / "handler.ts").write_text("x\n" * 4 + "// TODO handler\n")   # line 5
    (civ / "other.ts").write_text("// TODO other\n")                    # line 1

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    monkeypatch.setattr(exclusions, "LAST_EMAILED_FILE", tmp_path / "last_emailed.json")
    monkeypatch.setattr(exclusions, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(exclusions, "LATEST_FILE", tmp_path / "latest.json")
    monkeypatch.setattr(scan, "DEFAULT_REPOS", [str(civ)])

    # the emailed digest: position 1 = a civitai proposal whose evidence is handler.ts:5
    (tmp_path / "last_emailed.json").write_text(json.dumps({"proposals": [
        {"repo": "civitai", "title": "handler",
         "evidence": ["civitai/handler.ts:5"]}]}))

    import feedback as feedback_mod
    import llm
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback",
                        lambda: _FB("1. not needed, skip\n"))

    seen = {}
    orig = scan.prescan.scan_all

    def spy_scan_all(repos, limit, caps=None):
        return orig(repos, limit, caps)
    monkeypatch.setattr(scan.prescan, "scan_all", spy_scan_all)

    def fake_synth(cands, *, top, model, feedback=None):
        seen["refs"] = {c["ref"] for c in cands}
        return llm.Synthesis(proposals=[_RealishProp("ok", "civitai")], approx_prompt_tokens=1)
    monkeypatch.setattr(llm, "synthesize", fake_synth)

    args = scan.build_parser().parse_args(["--json", "--repos", str(civ)])
    rc = scan.cmd_scan(args)
    assert rc == 0
    # the dismissed candidate did NOT reach synthesis; the other civitai candidate did.
    assert "civitai/handler.ts:5" not in seen["refs"]
    assert "civitai/other.ts:1" in seen["refs"]
    # the repo was NOT excluded (state has no repos entry, dismissed has the ref)
    st = exclusions.load_state(tmp_path / "exclusions.json")
    assert st["repos"] == {}
    assert "civitai/handler.ts:5" in st["dismissed"]


def test_scan_dismiss_reports_count_in_json(tmp_path, monkeypatch, capsys):
    civ = tmp_path / "civitai"
    civ.mkdir()
    (civ / "handler.ts").write_text("x\n" * 4 + "// TODO handler\n")   # line 5
    (civ / "other.ts").write_text("// TODO other\n")

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    monkeypatch.setattr(exclusions, "LAST_EMAILED_FILE", tmp_path / "last_emailed.json")
    monkeypatch.setattr(exclusions, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(exclusions, "LATEST_FILE", tmp_path / "latest.json")
    monkeypatch.setattr(scan, "DEFAULT_REPOS", [str(civ)])
    (tmp_path / "last_emailed.json").write_text(json.dumps({"proposals": [
        {"repo": "civitai", "title": "handler", "evidence": ["civitai/handler.ts:5"]}]}))

    import feedback as feedback_mod
    import llm
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback",
                        lambda: _FB("1. skip\n"))
    monkeypatch.setattr(llm, "synthesize",
                        lambda cands, *, top, model, feedback=None:
                        llm.Synthesis(proposals=[_RealishProp("ok", "civitai")], approx_prompt_tokens=1))

    args = scan.build_parser().parse_args(["--json", "--repos", str(civ)])
    scan.cmd_scan(args)
    data = json.loads(capsys.readouterr().out)
    assert data["dismissed_count"] == 1
    # repo NOT in excluded_repos (the whole point)
    assert "civitai" not in data["excluded_repos"]


def test_scan_persisted_dismiss_filters_on_no_llm_smoke(tmp_path, monkeypatch, capsys):
    """A persisted dismissal drops the candidate even on the free --no-llm smoke path
    (no feedback fetch)."""
    civ = tmp_path / "civitai"
    civ.mkdir()
    (civ / "handler.ts").write_text("x\n" * 4 + "// TODO handler\n")   # line 5
    (civ / "other.ts").write_text("// TODO other\n")

    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    (tmp_path / "exclusions.json").write_text(json.dumps(
        {"repos": {}, "dismissed": {"civitai/handler.ts:5": {"repo": "civitai"}}}))

    import feedback as feedback_mod
    called = {"fetch": False}

    def spy_fetch():
        called["fetch"] = True
        return _FB("1. skip\n")
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", spy_fetch)

    args = scan.build_parser().parse_args(["--no-llm", "--json", "--repos", str(civ)])
    rc = scan.cmd_scan(args)
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert called["fetch"] is False       # no network on the free smoke path
    assert data["dismissed_count"] == 1
    refs = {c["ref"] for c in data["candidates"]}
    assert "civitai/handler.ts:5" not in refs   # dismissed candidate suppressed
    assert "civitai/other.ts:1" in refs         # the rest of the repo still surfaces
