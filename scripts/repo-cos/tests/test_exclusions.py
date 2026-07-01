"""Deterministic repo-exclusion layer tests — parse_reply, filter_repos, state
persistence, last-emailed resolution, and scan.py integration. No network, no LLM.

The headline case: the EXACT reply that made the context-injection loop fail in practice —
Zach replied "1. this project is paused / … / 5. we are not the code owner for that repo"
and the model re-proposed the paused repos. Here that reply becomes a HARD filter.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import exclusions  # noqa: E402
import digest  # noqa: E402
import scan  # noqa: E402


# The digest Zach ACTUALLY SAW (5 emailed proposals). Positions 1-5 map to these repos.
EMAILED = [
    {"repo": "kubeclaw-cloud", "title": "Wire embed auth", "evidence": ["kubeclaw-cloud/a.go:1"]},
    {"repo": "baseball-manitoba-pitch", "title": "Fix skip", "evidence": ["baseball-manitoba-pitch/x_test.go:3"]},
    {"repo": "homelab-talos", "title": "Pin latest tag", "evidence": ["homelab-talos/d.yaml:9"]},
    {"repo": "kubeclaw-cloud", "title": "Dead code", "evidence": ["kubeclaw-cloud/b.go:22"]},
    {"repo": "civitai-orchestration", "title": "Add test", "evidence": ["civitai-orchestration/c.py:7"]},
]

# The exact real-world reply that the LLM-context path ignored.
SAMPLE_REPLY = (
    "1. this project is paused\n"
    "2. this project is also paused\n"
    "3. those services are paused\n"
    "4. kubeclaw is paused\n"
    "5. we are not the code owner for that repo\n"
)

DEFAULT_REPOS = [
    "~/workspace/devrc",
    "~/workspace/homelab-talos",
    "~/workspace/kubeclaw",
    "~/workspace/kubeclaw-cloud",
    "~/workspace/promptver",
    "~/workspace/baseball-manitoba-pitch",
    "~/workspace/civit/civitai",
    "~/workspace/civit/datapacket-talos",
    "~/workspace/civit/civitai-orchestration",
]


def _alias():
    return exclusions.build_alias_map(DEFAULT_REPOS)


# ---- parse_reply: the headline sample -----------------------------------------------

def test_parse_reply_exact_sample_excludes_all_paused_repos():
    parsed = exclusions.parse_reply(SAMPLE_REPLY, EMAILED, alias_map=_alias())
    excluded = {e["repo"] for e in parsed["exclude"]}
    # positions 1-5 → these repos (kubeclaw-cloud appears at #1 and #4, deduped)
    assert excluded == {
        "kubeclaw-cloud", "baseball-manitoba-pitch",
        "homelab-talos", "civitai-orchestration",
    }
    assert parsed["resume"] == []


def test_parse_reply_not_code_owner_is_permanent():
    parsed = exclusions.parse_reply(SAMPLE_REPLY, EMAILED, alias_map=_alias())
    by_repo = {e["repo"]: e for e in parsed["exclude"]}
    # #5 "we are not the code owner" → civitai-orchestration, permanent
    assert by_repo["civitai-orchestration"]["permanent"] is True
    # paused ones are NOT permanent
    assert by_repo["baseball-manitoba-pitch"]["permanent"] is False
    assert by_repo["homelab-talos"]["permanent"] is False


def test_parse_reply_positional_and_keyword_both_hit_kubeclaw():
    # line 4 "kubeclaw is paused" is BOTH positional #4 (→kubeclaw-cloud) AND names kubeclaw.
    # Positional wins here (#4 → kubeclaw-cloud), and the repo is excluded either way.
    parsed = exclusions.parse_reply("4. kubeclaw is paused\n", EMAILED, alias_map=_alias())
    assert {e["repo"] for e in parsed["exclude"]} == {"kubeclaw-cloud"}


def test_parse_reply_name_only_exclude_no_number():
    # explicit repo-name exclusion without any position number
    parsed = exclusions.parse_reply("civitai is deprecated, drop it\n", None,
                                    alias_map=_alias())
    excl = parsed["exclude"]
    assert len(excl) == 1
    assert excl[0]["repo"] == "civitai"
    assert excl[0]["permanent"] is True  # "deprecated" → permanent


def test_parse_reply_resume_moves_to_resume_list():
    # 'kubeclaw' names the kubeclaw repo exactly (kubeclaw-cloud is a distinct repo).
    parsed = exclusions.parse_reply("resume kubeclaw\n", None, alias_map=_alias())
    assert parsed["resume"] == ["kubeclaw"]
    assert parsed["exclude"] == []


def test_parse_reply_resume_positional():
    parsed = exclusions.parse_reply("2. resume this one\n", EMAILED, alias_map=_alias())
    assert parsed["resume"] == ["baseball-manitoba-pitch"]


def test_parse_reply_resume_beats_exclude_same_repo():
    # a resume line and an exclude line for the same repo → resume wins
    reply = "3. paused\n3. actually resume this\n"
    parsed = exclusions.parse_reply(reply, EMAILED, alias_map=_alias())
    assert parsed["resume"] == ["homelab-talos"]
    assert all(e["repo"] != "homelab-talos" for e in parsed["exclude"])


def test_parse_reply_unparseable_and_empty_never_raise():
    assert exclusions.parse_reply("", EMAILED, alias_map=_alias()) == {"exclude": [], "resume": []}
    assert exclusions.parse_reply(None, EMAILED, alias_map=_alias()) == {"exclude": [], "resume": []}
    # prose with no positional/name anchor → nothing excluded (falls through to LLM context)
    prose = "Great work this week, keep the momentum going on everything!\n"
    parsed = exclusions.parse_reply(prose, EMAILED, alias_map=_alias())
    assert parsed == {"exclude": [], "resume": []}


def test_parse_reply_positional_no_intent_is_ignored():
    # "1. looks good" — a bare positional line with NO exclude/resume keyword does nothing.
    parsed = exclusions.parse_reply("1. looks good, ship it\n", EMAILED, alias_map=_alias())
    assert parsed == {"exclude": [], "resume": []}


def test_parse_reply_out_of_range_position_falls_through_to_name():
    # position 9 doesn't exist, but the line names civitai → still excluded via alias
    parsed = exclusions.parse_reply("9. civitai is paused\n", EMAILED, alias_map=_alias())
    assert {e["repo"] for e in parsed["exclude"]} == {"civitai"}


def test_parse_reply_various_numbering_styles():
    for line in ["1) paused", "2 - paused", "#3 paused", "4: paused"]:
        parsed = exclusions.parse_reply(line + "\n", EMAILED, alias_map=_alias())
        assert len(parsed["exclude"]) == 1, line


# ---- filter_repos -------------------------------------------------------------------

def test_filter_repos_matches_path_and_basename():
    state = {"repos": {"civitai-orchestration": {"permanent": True}}}
    repos = [
        "~/workspace/civit/civitai-orchestration",  # full path, excluded key is basename
        "~/workspace/devrc",                          # kept
    ]
    kept, excluded = exclusions.filter_repos(repos, state)
    assert kept == ["~/workspace/devrc"]
    assert excluded == ["~/workspace/civit/civitai-orchestration"]


def test_filter_repos_bare_basename_excluded():
    state = {"repos": {"homelab-talos": {}}}
    kept, excluded = exclusions.filter_repos(
        ["homelab-talos", "kubeclaw"], state)
    assert kept == ["kubeclaw"]
    assert excluded == ["homelab-talos"]


def test_filter_repos_empty_state_keeps_all():
    repos = ["~/workspace/devrc", "~/workspace/kubeclaw"]
    kept, excluded = exclusions.filter_repos(repos, {"repos": {}})
    assert kept == repos
    assert excluded == []


# ---- persistence round-trip ---------------------------------------------------------

def test_state_load_save_roundtrip(tmp_path):
    p = tmp_path / "exclusions.json"
    state = {"repos": {}}
    parsed = exclusions.parse_reply(SAMPLE_REPLY, EMAILED, alias_map=_alias())
    exclusions.apply(state, parsed, source="reply", now="2026-07-01T08:00:00-05:00")
    exclusions.save_state(state, p)

    loaded = exclusions.load_state(p)
    assert set(loaded["repos"]) == {
        "kubeclaw-cloud", "baseball-manitoba-pitch",
        "homelab-talos", "civitai-orchestration",
    }
    coe = loaded["repos"]["civitai-orchestration"]
    assert coe["permanent"] is True
    assert coe["source"] == "reply"
    assert coe["excluded_at"] == "2026-07-01T08:00:00-05:00"


def test_load_state_missing_file_is_empty(tmp_path):
    assert exclusions.load_state(tmp_path / "nope.json") == {"repos": {}}


def test_load_state_corrupt_file_is_empty(tmp_path):
    p = tmp_path / "exclusions.json"
    p.write_text("{ this is not json ")
    assert exclusions.load_state(p) == {"repos": {}}


def test_load_state_wrong_shape_is_normalized(tmp_path):
    p = tmp_path / "exclusions.json"
    p.write_text(json.dumps(["not", "a", "dict"]))
    assert exclusions.load_state(p) == {"repos": {}}
    p.write_text(json.dumps({"repos": "not-a-dict"}))
    assert exclusions.load_state(p)["repos"] == {}


def test_apply_resume_removes_excluded(tmp_path):
    state = {"repos": {"kubeclaw-cloud": {"permanent": False, "source": "reply"}}}
    exclusions.apply(state, {"exclude": [], "resume": ["kubeclaw-cloud"]})
    assert "kubeclaw-cloud" not in state["repos"]


def test_apply_permanent_sticks_across_refresh():
    # a repo excluded as permanent, then re-touched by a "paused" line, stays permanent.
    state = {"repos": {}}
    exclusions.apply(state, {"exclude": [{"repo": "r", "reason": "not owner", "permanent": True}],
                             "resume": []})
    exclusions.apply(state, {"exclude": [{"repo": "r", "reason": "paused", "permanent": False}],
                             "resume": []})
    assert state["repos"]["r"]["permanent"] is True


# ---- load_last_emailed resolution order ---------------------------------------------

def test_load_last_emailed_prefers_last_emailed_file(tmp_path):
    le = tmp_path / "last_emailed.json"
    hd = tmp_path / "history"
    lt = tmp_path / "latest.json"
    hd.mkdir()
    le.write_text(json.dumps({"proposals": [{"repo": "from-last-emailed"}]}))
    (hd / "20260101-000000.json").write_text(json.dumps({"emailed": True, "proposals": [{"repo": "from-history"}]}))
    lt.write_text(json.dumps({"proposals": [{"repo": "from-latest"}]}))
    got = exclusions.load_last_emailed(last_emailed=le, history_dir=hd, latest=lt)
    assert got["proposals"][0]["repo"] == "from-last-emailed"


def test_load_last_emailed_falls_back_to_history_emailed_true(tmp_path):
    le = tmp_path / "missing.json"
    hd = tmp_path / "history"
    lt = tmp_path / "latest.json"
    hd.mkdir()
    # an older emailed=false and a newer emailed=true — the newest emailed=true wins
    (hd / "20260101-000000.json").write_text(json.dumps({"emailed": False, "proposals": [{"repo": "old-not-emailed"}]}))
    (hd / "20260601-000000.json").write_text(json.dumps({"emailed": True, "proposals": [{"repo": "newest-emailed"}]}))
    lt.write_text(json.dumps({"proposals": [{"repo": "from-latest"}]}))
    got = exclusions.load_last_emailed(last_emailed=le, history_dir=hd, latest=lt)
    assert got["proposals"][0]["repo"] == "newest-emailed"


def test_load_last_emailed_falls_back_to_latest(tmp_path):
    le = tmp_path / "missing.json"
    hd = tmp_path / "history"
    lt = tmp_path / "latest.json"
    hd.mkdir()
    # history exists but nothing was emailed → fall back to latest.json
    (hd / "20260101-000000.json").write_text(json.dumps({"emailed": False, "proposals": [{"repo": "x"}]}))
    lt.write_text(json.dumps({"proposals": [{"repo": "from-latest"}]}))
    got = exclusions.load_last_emailed(last_emailed=le, history_dir=hd, latest=lt)
    assert got["proposals"][0]["repo"] == "from-latest"


def test_load_last_emailed_none_when_nothing(tmp_path):
    got = exclusions.load_last_emailed(
        last_emailed=tmp_path / "a.json",
        history_dir=tmp_path / "h",
        latest=tmp_path / "b.json")
    assert got is None


# ---- digest footer ------------------------------------------------------------------

def test_digest_footer_lists_excluded():
    from datetime import date
    body = digest.render([], today=date(2026, 7, 1),
                         excluded_repos=["kubeclaw-cloud", "homelab-talos"])
    assert "Excluded (paused/not-yours): kubeclaw-cloud, homelab-talos" in body
    assert 'resume <repo>' in body


def test_digest_no_footer_when_none():
    from datetime import date
    body = digest.render([], today=date(2026, 7, 1), excluded_repos=[])
    assert "Excluded" not in body


# ---- scan.py integration (feedback + llm mocked; no network) -------------------------

class _RealishProp:
    def __init__(self, title, repo="r"):
        self.title, self.repo = title, repo
        self.evidence, self.why = [f"{repo}/f.py:1"], "w"
        self.effort, self.approach, self.ci_verifiable = "S", "a", True

    def as_dict(self):
        return {"title": self.title, "repo": self.repo, "evidence": self.evidence,
                "why": self.why, "effort": self.effort, "approach": self.approach,
                "ci_verifiable": self.ci_verifiable}


class _FB:
    def __init__(self, text):
        self.reply_text = text
        self.prev_proposals = []
        self.replied_at = "2026-07-01T08:00:00-05:00"

    def prev_summary(self):
        return []


def _two_repos(tmp_path):
    keep = tmp_path / "keepme"
    drop = tmp_path / "dropme"
    (keep).mkdir()
    (drop).mkdir()
    (keep / "a.py").write_text("# TODO fix keep\n")
    (drop / "b.py").write_text("# TODO fix drop\n")
    return keep, drop


def test_scan_reply_excludes_repo_from_synthesis(tmp_path, monkeypatch):
    """A reply naming 'dropme' → that repo is filtered out and NEVER reaches scan_all/synth."""
    keep, drop = _two_repos(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")
    # point the exclusions module's state + last_emailed at temp files
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    monkeypatch.setattr(exclusions, "LAST_EMAILED_FILE", tmp_path / "last_emailed.json")
    monkeypatch.setattr(exclusions, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(exclusions, "LATEST_FILE", tmp_path / "latest.json")
    # a last_emailed digest where position 1 = the 'dropme' repo
    (tmp_path / "last_emailed.json").write_text(json.dumps(
        {"proposals": [{"repo": "dropme", "title": "x", "evidence": ["dropme/b.py:1"]}]}))
    # DEFAULT_REPOS-derived alias map: include dropme so name-mention also works
    monkeypatch.setattr(scan, "DEFAULT_REPOS", [str(keep), str(drop)])

    import feedback as feedback_mod
    import llm
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback",
                        lambda: _FB("1. this project is paused\n"))

    seen = {}
    orig_scan_all = scan.prescan.scan_all

    def spy_scan_all(repos, limit, caps=None):
        seen["repos"] = list(repos)
        return orig_scan_all(repos, limit, caps)

    monkeypatch.setattr(scan.prescan, "scan_all", spy_scan_all)
    monkeypatch.setattr(llm, "synthesize",
                        lambda cands, *, top, model, feedback=None:
                        llm.Synthesis(proposals=[_RealishProp("ok", "keepme")], approx_prompt_tokens=1))

    args = scan.build_parser().parse_args(
        ["--json", "--repos", f"{keep},{drop}"])
    rc = scan.cmd_scan(args)
    assert rc == 0
    # the excluded repo must NOT have been scanned
    assert str(keep) in seen["repos"]
    assert str(drop) not in seen["repos"]


def test_scan_reply_excludes_repo_reports_in_json(tmp_path, monkeypatch, capsys):
    keep, drop = _two_repos(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    monkeypatch.setattr(exclusions, "LAST_EMAILED_FILE", tmp_path / "last_emailed.json")
    monkeypatch.setattr(exclusions, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(exclusions, "LATEST_FILE", tmp_path / "latest.json")
    (tmp_path / "last_emailed.json").write_text(json.dumps(
        {"proposals": [{"repo": "dropme"}]}))
    monkeypatch.setattr(scan, "DEFAULT_REPOS", [str(keep), str(drop)])

    import feedback as feedback_mod
    import llm
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback",
                        lambda: _FB("1. paused\n"))
    monkeypatch.setattr(llm, "synthesize",
                        lambda cands, *, top, model, feedback=None:
                        llm.Synthesis(proposals=[_RealishProp("ok", "keepme")], approx_prompt_tokens=1))

    args = scan.build_parser().parse_args(["--json", "--repos", f"{keep},{drop}"])
    scan.cmd_scan(args)
    data = json.loads(capsys.readouterr().out)
    assert "dropme" in data["excluded_repos"]


def test_scan_no_feedback_skips_exclusion_parse(tmp_path, monkeypatch):
    keep, drop = _two_repos(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    monkeypatch.setattr(exclusions, "LAST_EMAILED_FILE", tmp_path / "last_emailed.json")
    monkeypatch.setattr(exclusions, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(exclusions, "LATEST_FILE", tmp_path / "latest.json")

    import feedback as feedback_mod
    import llm
    called = {"fetch": False}

    def spy_fetch():
        called["fetch"] = True
        return _FB("1. paused\n")
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", spy_fetch)

    seen = {}
    orig_scan_all = scan.prescan.scan_all

    def spy_scan_all(repos, limit, caps=None):
        seen["repos"] = list(repos)
        return orig_scan_all(repos, limit, caps)
    monkeypatch.setattr(scan.prescan, "scan_all", spy_scan_all)
    monkeypatch.setattr(llm, "synthesize",
                        lambda cands, *, top, model, feedback=None:
                        llm.Synthesis(proposals=[_RealishProp("ok", "keepme")], approx_prompt_tokens=1))

    args = scan.build_parser().parse_args(
        ["--no-feedback", "--json", "--repos", f"{keep},{drop}"])
    scan.cmd_scan(args)
    assert called["fetch"] is False        # fetch skipped
    # both repos scanned (nothing excluded)
    assert str(keep) in seen["repos"] and str(drop) in seen["repos"]


def test_show_exclusions_prints_and_exits(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    (tmp_path / "exclusions.json").write_text(json.dumps(
        {"repos": {"kubeclaw-cloud": {"permanent": False, "source": "reply",
                                      "excluded_at": "2026-07-01T08:00:00-05:00",
                                      "reason": "1. paused"}}}))
    args = scan.build_parser().parse_args(["--show-exclusions"])
    rc = scan.cmd_scan(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "kubeclaw-cloud" in out
    assert "paused" in out


def test_show_exclusions_empty_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "nope.json")
    args = scan.build_parser().parse_args(["--show-exclusions"])
    rc = scan.cmd_scan(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "none" in out.lower()


def test_show_exclusions_flag_default_false():
    args = scan.build_parser().parse_args([])
    assert args.show_exclusions is False


def test_no_llm_smoke_does_not_fetch_feedback_but_still_filters(tmp_path, monkeypatch, capsys):
    """The free Stage-1 smoke test must stay network-less (no IMAP fetch), yet a persisted
    exclusion still drops the repo from the candidate scan."""
    keep, drop = _two_repos(tmp_path)
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    (tmp_path / "exclusions.json").write_text(json.dumps({"repos": {drop.name: {}}}))

    import feedback as feedback_mod
    called = {"fetch": False}

    def spy_fetch():
        called["fetch"] = True
        return _FB("1. paused\n")
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", spy_fetch)

    args = scan.build_parser().parse_args(
        ["--no-llm", "--json", "--repos", f"{keep},{drop}"])
    rc = scan.cmd_scan(args)
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert called["fetch"] is False  # no IMAP on the free smoke path
    # the persisted exclusion still filtered the repo out of the candidate scan
    scanned = {r["repo"] for r in data["repos"]}
    assert drop.name not in scanned
    assert keep.name in scanned


def test_scan_all_excluded_errors(tmp_path, monkeypatch):
    # if every resolved repo is excluded, cmd_scan errors cleanly (rc 2), no synthesis.
    keep, drop = _two_repos(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    # pre-seed BOTH repos as excluded
    (tmp_path / "exclusions.json").write_text(json.dumps(
        {"repos": {keep.name: {}, drop.name: {}}}))
    args = scan.build_parser().parse_args(
        ["--no-feedback", "--json", "--repos", f"{keep},{drop}"])
    rc = scan.cmd_scan(args)
    assert rc == 2
