#!/usr/bin/env python3
"""APPROVE → clawgate Task tests — the third reply intent.

When Zach replies "N. approve" (yes/lgtm/ship it/👍/…) to a digest proposal, repo-cos:
  1. maps position N → the FULL proposal (title/repo/evidence/why/approach/effort/ci_verifiable),
  2. POSTs a durable Task card to his clawgate adjudication+dispatch queue, and
  3. SUPPRESS-ON-SUCCESS: only when the POST returns a task id, records the proposal's evidence
     in state["approved"] so it can't re-nag next week; a FAILED POST is left unsuppressed.

Covers: the deterministic parser (approve tier + its precedence vs dismiss), the clawgate poster
(payload/header/creds/failure), suppress-on-success in scan.py, filter_candidates dropping
approved refs, and --show-exclusions visibility. NO network, NO LLM (clawgate is fully mocked).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import clawgate  # noqa: E402
import exclusions  # noqa: E402
import prescan  # noqa: E402
import scan  # noqa: E402


# The 5-proposal digest Zach ACTUALLY SAW. Positions 1-5.
EMAILED = [
    {"repo": "devrc", "title": "Unskip collector tests",
     "evidence": ["devrc/scripts/collector/collector.py:88"],
     "why": "3 collector tests are skipped", "approach": "unskip + fix the fixtures",
     "effort": "S", "ci_verifiable": True},
    {"repo": "civitai", "title": "Remove 3d-model dead code",
     "evidence": ["civitai/docs/3d-models-followups.md:103", "civitai/src/3d/model.ts:44"],
     "why": "dead feature", "approach": "delete the module", "effort": "M",
     "ci_verifiable": False},
    {"repo": "civitai", "title": "Add missing handler test",
     "evidence": ["civitai/src/api/handler.ts:210"], "why": "untested path",
     "approach": "add a unit test", "effort": "S", "ci_verifiable": True},
    {"repo": "datapacket-talos", "title": "Fix skipped net test",
     "evidence": ["datapacket-talos/test/net_test.go:8"], "why": "skipped",
     "approach": "unskip", "effort": "S", "ci_verifiable": True},
    {"repo": "kubeclaw-embed", "title": "Split large main.go",
     "evidence": ["kubeclaw-embed/main.go:0"], "why": "1200-line file",
     "approach": "extract packages", "effort": "L", "ci_verifiable": False},
]

DEFAULT_REPOS = [
    "~/workspace/devrc",
    "~/workspace/kubeclaw-embed",
    "~/workspace/civit/civitai",
    "~/workspace/civit/datapacket-talos",
]


def _alias():
    return exclusions.build_alias_map(DEFAULT_REPOS)


# ==== 1. PARSER: approve intent ======================================================

def test_approve_collects_full_proposal():
    parsed = exclusions.parse_reply("1. approve\n", EMAILED, alias_map=_alias())
    assert len(parsed["approve"]) == 1
    p = parsed["approve"][0]
    assert p["title"] == "Unskip collector tests"
    assert p["repo"] == "devrc"
    assert p["evidence"] == ["devrc/scripts/collector/collector.py:88"]
    assert p["why"] == "3 collector tests are skipped"
    assert p["approach"] == "unskip + fix the fixtures"
    assert p["effort"] == "S"
    assert p["ci_verifiable"] is True
    # approve does NOT touch exclude/resume/dismiss.
    assert parsed["exclude"] == [] and parsed["resume"] == [] and parsed["dismiss"] == []


def test_lgtm_is_approve():
    parsed = exclusions.parse_reply("2. lgtm\n", EMAILED, alias_map=_alias())
    assert [p["title"] for p in parsed["approve"]] == ["Remove 3d-model dead code"]
    # a proposal's ALL evidence refs are carried (for suppression of every signal).
    assert parsed["approve"][0]["evidence"] == [
        "civitai/docs/3d-models-followups.md:103", "civitai/src/3d/model.ts:44"]


def test_yes_ship_it_is_approve():
    parsed = exclusions.parse_reply("3. yes ship it\n", EMAILED, alias_map=_alias())
    assert [p["repo"] for p in parsed["approve"]] == ["civitai"]
    assert parsed["approve"][0]["evidence"] == ["civitai/src/api/handler.ts:210"]


def test_thumbsup_and_plus_one_and_do_it_approve():
    for kw in ("👍", "+1", "do it", "go ahead", "build it", "looks good"):
        parsed = exclusions.parse_reply(f"4. {kw}\n", EMAILED, alias_map=_alias())
        assert [p["repo"] for p in parsed["approve"]] == ["datapacket-talos"], kw


def test_mixed_approve_and_skip_is_dismiss_negative_wins():
    # "approve but skip the test" — the NEGATIVE (skip) beats approve. Dropping a proposal Zach
    # also said to skip is safer than dispatching it.
    parsed = exclusions.parse_reply("4. approve but skip the test\n", EMAILED, alias_map=_alias())
    assert parsed["approve"] == []
    refs = {r for d in parsed["dismiss"] for r in d["evidence"]}
    assert refs == {"datapacket-talos/test/net_test.go:8"}


def test_approve_does_not_exclude_or_dismiss_repo():
    parsed = exclusions.parse_reply("1. approve\n", EMAILED, alias_map=_alias())
    assert parsed["exclude"] == []
    assert parsed["dismiss"] == []
    # the repo stays fully in scope; only the ONE proposal is queued.


def test_bare_approve_no_emailed_proposals_is_noop():
    # position 1 with NO emailed digest → no proposal to queue → no-op (mirrors dismiss).
    parsed = exclusions.parse_reply("1. approve\n", [], alias_map=_alias())
    assert parsed["approve"] == []
    assert parsed == {"exclude": [], "resume": [], "dismiss": [], "approve": []}


def test_approve_name_only_line_cannot_queue():
    # a name mention with no position carries no proposal → approve can't fire.
    parsed = exclusions.parse_reply("approve the civitai thing\n", EMAILED, alias_map=_alias())
    assert parsed["approve"] == []


def test_resume_beats_approve_on_same_line():
    parsed = exclusions.parse_reply("1. resume this, approve it\n", EMAILED, alias_map=_alias())
    assert parsed["resume"] == ["devrc"]
    assert parsed["approve"] == []


def test_pause_beats_approve_on_same_line():
    # "approve but this is paused" → the repo-pause wins (higher tier than approve).
    parsed = exclusions.parse_reply("1. approve, though it's paused\n", EMAILED,
                                    alias_map=_alias())
    assert {e["repo"] for e in parsed["exclude"]} == {"devrc"}
    assert parsed["approve"] == []


def test_multiple_approvals_in_one_reply():
    reply = "1. approve\n3. lgtm\n"
    parsed = exclusions.parse_reply(reply, EMAILED, alias_map=_alias())
    assert {p["title"] for p in parsed["approve"]} == {
        "Unskip collector tests", "Add missing handler test"}


# ==== 2. CLAWGATE POSTER (mock urllib — NO network) ==================================

def test_load_creds_parses_env_fixture(tmp_path):
    envf = tmp_path / "clawgate.env"
    envf.write_text(
        "# a comment\n"
        "CLAWGATE_API_URL=http://192.168.50.250:30302\n"
        'CLAWGATE_HOOK_TOKEN="tok-123"\n'
        "\n"
        "OTHER=ignored\n")
    creds = clawgate.load_creds(envf)
    assert creds["CLAWGATE_API_URL"] == "http://192.168.50.250:30302"
    assert creds["CLAWGATE_HOOK_TOKEN"] == "tok-123"   # quotes stripped


def test_load_creds_missing_file_is_empty(tmp_path):
    assert clawgate.load_creds(tmp_path / "nope.env") == {}


def test_build_task_body_and_title():
    prop = EMAILED[0]
    title = clawgate.build_task_title(prop)
    body = clawgate.build_task_body(prop)
    assert title == "Unskip collector tests"
    assert body.startswith("**🤖 repo-cos · APPROVED**")
    assert "Unskip collector tests" in body
    assert "3 collector tests are skipped" in body
    assert "**Approach:** unskip + fix the fixtures" in body
    assert "**Repo:** devrc" in body
    assert "**Effort:** S" in body and "CI-verifiable" in body
    assert "`devrc/scripts/collector/collector.py:88`" in body


def test_build_task_title_truncated_to_80():
    long = {"title": "x" * 200}
    assert len(clawgate.build_task_title(long)) == 80


def test_post_task_builds_payload_and_bearer_header(monkeypatch):
    seen = {}

    def fake_post(url, payload, token, timeout=15):
        seen["url"] = url
        seen["payload"] = payload
        seen["token"] = token
        return json.dumps({"id": 4242})

    creds = {"CLAWGATE_API_URL": "http://cg:30302/", "CLAWGATE_HOOK_TOKEN": "tok-xyz"}
    tid = clawgate.post_task("My task", "**body**", creds=creds, _post=fake_post)
    assert tid == 4242
    assert seen["url"] == "http://cg:30302/api/tasks"     # trailing slash collapsed
    assert seen["payload"] == {"directory": "My task", "body": "**body**"}
    assert seen["token"] == "tok-xyz"


def test_post_task_sends_content_type_and_authorization(monkeypatch):
    # exercise the real _post wiring by capturing the urllib.request.Request it builds.
    import urllib.request
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"id": 7}).encode()

    def fake_urlopen(req, timeout=None):
        captured["headers"] = req.headers
        captured["data"] = req.data
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    creds = {"CLAWGATE_API_URL": "http://cg:30302", "CLAWGATE_HOOK_TOKEN": "tok-abc"}
    tid = clawgate.post_task("t", "b", creds=creds)
    assert tid == 7
    # urllib title-cases header keys.
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["headers"]["Authorization"] == "Bearer tok-abc"
    assert captured["method"] == "POST"
    assert json.loads(captured["data"]) == {"directory": "t", "body": "b"}


def test_post_task_no_creds_returns_none():
    assert clawgate.post_task("t", "b", creds={}) is None
    assert clawgate.post_task("t", "b", creds={"CLAWGATE_API_URL": "http://x"}) is None


def test_post_task_failure_returns_none_never_raises():
    def boom(url, payload, token, timeout=15):
        raise OSError("connection refused")

    creds = {"CLAWGATE_API_URL": "http://cg", "CLAWGATE_HOOK_TOKEN": "t"}
    # must NOT raise
    assert clawgate.post_task("t", "b", creds=creds, _post=boom) is None


def test_post_task_non_integer_id_is_failure():
    def no_id(url, payload, token, timeout=15):
        return json.dumps({"ok": True})   # no "id"

    creds = {"CLAWGATE_API_URL": "http://cg", "CLAWGATE_HOOK_TOKEN": "t"}
    assert clawgate.post_task("t", "b", creds=creds, _post=no_id) is None


def test_post_task_unparseable_response_is_failure():
    def junk(url, payload, token, timeout=15):
        return "<html>oops</html>"

    creds = {"CLAWGATE_API_URL": "http://cg", "CLAWGATE_HOOK_TOKEN": "t"}
    assert clawgate.post_task("t", "b", creds=creds, _post=junk) is None


# ==== 3. SUPPRESS-ON-SUCCESS (exclusions.apply_approvals) ============================

def test_apply_approvals_success_suppresses():
    state = {"repos": {}, "dismissed": {}, "approved": {}}
    approvals = exclusions.parse_reply("2. lgtm\n", EMAILED, alias_map=_alias())["approve"]
    first_ref = approvals[0]["evidence"][0]
    exclusions.apply_approvals(state, approvals, {first_ref: 99},
                               now="2026-07-02T08:00:00-05:00")
    # BOTH of proposal #2's evidence refs are suppressed, tagged with the task id.
    assert set(state["approved"]) == {
        "civitai/docs/3d-models-followups.md:103", "civitai/src/3d/model.ts:44"}
    e = state["approved"]["civitai/src/3d/model.ts:44"]
    assert e["clawgate_task_id"] == 99
    assert e["repo"] == "civitai"
    assert e["approved_at"] == "2026-07-02T08:00:00-05:00"


def test_apply_approvals_failed_post_not_suppressed():
    state = {"repos": {}, "dismissed": {}, "approved": {}}
    approvals = exclusions.parse_reply("1. approve\n", EMAILED, alias_map=_alias())["approve"]
    first_ref = approvals[0]["evidence"][0]
    # post_task returned None (failure) → NOT suppressed → re-proposes next week.
    exclusions.apply_approvals(state, approvals, {first_ref: None})
    assert state["approved"] == {}


def test_apply_approvals_mixed_success_and_failure():
    state = {"repos": {}, "dismissed": {}, "approved": {}}
    approvals = exclusions.parse_reply("1. approve\n3. lgtm\n", EMAILED,
                                       alias_map=_alias())["approve"]
    by_repo = {p["repo"]: p for p in approvals}
    task_ids = {
        by_repo["devrc"]["evidence"][0]: 11,         # success
        by_repo["civitai"]["evidence"][0]: None,     # failure
    }
    exclusions.apply_approvals(state, approvals, task_ids)
    # only the devrc one is suppressed.
    assert set(state["approved"]) == {"devrc/scripts/collector/collector.py:88"}


# ==== 4. filter_candidates drops approved refs (combined suppressed-set) =============

def _cand(repo, file, line):
    return prescan.Candidate(repo=repo, kind="marker", file=file, line=line, text="x")


def test_filter_candidates_drops_approved_ref():
    state = {"repos": {}, "dismissed": {},
             "approved": {"devrc/scripts/collector/collector.py:88":
                          {"repo": "devrc", "clawgate_task_id": 5}}}
    cands = [
        _cand("devrc", "scripts/collector/collector.py", 88),   # approved → dropped
        _cand("devrc", "scripts/other.py", 3),                  # kept
    ]
    kept, dropped = exclusions.filter_candidates(cands, state)
    assert [c.ref for c in dropped] == ["devrc/scripts/collector/collector.py:88"]
    assert [c.ref for c in kept] == ["devrc/scripts/other.py:3"]


def test_filter_candidates_combines_dismissed_and_approved():
    state = {"repos": {},
             "dismissed": {"a/x.py:1": {"repo": "a"}},
             "approved": {"b/y.py:2": {"repo": "b", "clawgate_task_id": 7}}}
    cands = [_cand("a", "x.py", 1), _cand("b", "y.py", 2), _cand("c", "z.py", 3)]
    kept, dropped = exclusions.filter_candidates(cands, state)
    assert {c.ref for c in dropped} == {"a/x.py:1", "b/y.py:2"}
    assert [c.ref for c in kept] == ["c/z.py:3"]


# ==== 5. --show-exclusions shows the approved section ================================

def test_approved_entries_sorted():
    state = {"approved": {
        "b/y.py:2": {"repo": "b", "clawgate_task_id": 2, "approved_at": "t2", "reason": "lgtm"},
        "a/x.py:1": {"repo": "a", "clawgate_task_id": 1, "approved_at": "t1", "reason": "approve"},
    }}
    got = exclusions.approved_entries(state)
    assert [e["ref"] for e in got] == ["a/x.py:1", "b/y.py:2"]
    assert got[0]["clawgate_task_id"] == 1


def test_format_state_shows_approved_section():
    state = {"repos": {}, "dismissed": {}, "approved": {
        "devrc/scripts/collector/collector.py:88": {
            "repo": "devrc", "clawgate_task_id": 4242,
            "approved_at": "2026-07-02T08:00:00-05:00", "reason": "1. approve"}}}
    out = exclusions.format_state(state)
    assert "clawgate queue" in out.lower()
    assert "devrc/scripts/collector/collector.py:88" in out
    assert "4242" in out


# ==== 6. scan.py integration (mock clawgate + prescan + llm + feedback) ==============

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


def _prime_scan(tmp_path, monkeypatch, devrc_dir):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    monkeypatch.setattr(exclusions, "LAST_EMAILED_FILE", tmp_path / "last_emailed.json")
    monkeypatch.setattr(exclusions, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(exclusions, "LATEST_FILE", tmp_path / "latest.json")
    monkeypatch.setattr(scan, "DEFAULT_REPOS", [str(devrc_dir)])


def test_scan_approve_posts_and_suppresses_repo_kept(tmp_path, monkeypatch):
    """An approve reply → post_task called with the card + the proposal's evidence suppressed
    in state["approved"], while the repo is NOT excluded and its OTHER candidates still pass."""
    dev = tmp_path / "devrc"
    dev.mkdir()
    (dev / "collector.py").write_text("x\n" * 4 + "# TODO fix collector\n")   # line 5
    (dev / "other.py").write_text("# TODO other\n")                            # line 1
    _prime_scan(tmp_path, monkeypatch, dev)

    # emitted digest: position 1 = a devrc proposal whose evidence is collector.py:5
    (tmp_path / "last_emailed.json").write_text(json.dumps({"proposals": [
        {"repo": "devrc", "title": "collector", "evidence": ["devrc/collector.py:5"],
         "why": "todo", "approach": "fix", "effort": "S", "ci_verifiable": True}]}))

    import feedback as feedback_mod
    import llm
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback",
                        lambda: _FB("1. approve\n"))

    posted = {}

    class _FakeClawgate:
        @staticmethod
        def build_task_title(p):
            return p["title"]

        @staticmethod
        def build_task_body(p):
            return "**🤖 repo-cos · APPROVED**\n" + p["title"]

        @staticmethod
        def post_task(directory, body):
            posted["directory"] = directory
            posted["body"] = body
            return 4242

    # inject the fake clawgate into scan's poster helper via monkeypatching the module import.
    import clawgate as clawgate_mod
    monkeypatch.setattr(clawgate_mod, "build_task_title", _FakeClawgate.build_task_title)
    monkeypatch.setattr(clawgate_mod, "build_task_body", _FakeClawgate.build_task_body)
    monkeypatch.setattr(clawgate_mod, "post_task", _FakeClawgate.post_task)

    seen = {}

    def fake_synth(cands, *, top, model, feedback=None):
        seen["refs"] = {c["ref"] for c in cands}
        return llm.Synthesis(proposals=[_RealishProp("ok", "devrc")], approx_prompt_tokens=1)
    monkeypatch.setattr(llm, "synthesize", fake_synth)

    args = scan.build_parser().parse_args(["--json", "--repos", str(dev)])
    rc = scan.cmd_scan(args)
    assert rc == 0
    # 1) post_task was called with the approved proposal's card.
    assert posted["directory"] == "collector"
    assert posted["body"].startswith("**🤖 repo-cos · APPROVED**")
    # 2) the approved candidate did NOT reach synthesis; the other devrc candidate did.
    assert "devrc/collector.py:5" not in seen["refs"]
    assert "devrc/other.py:1" in seen["refs"]
    # 3) the repo is NOT excluded; the ref IS in state["approved"] with the task id.
    st = exclusions.load_state(tmp_path / "exclusions.json")
    assert st["repos"] == {}
    assert "devrc/collector.py:5" in st["approved"]
    assert st["approved"]["devrc/collector.py:5"]["clawgate_task_id"] == 4242


def test_scan_approve_failed_post_not_suppressed(tmp_path, monkeypatch):
    """post_task returns None (clawgate unreachable) → the proposal is NOT suppressed, so it
    re-proposes next week (its candidate still reaches synthesis)."""
    dev = tmp_path / "devrc"
    dev.mkdir()
    (dev / "collector.py").write_text("x\n" * 4 + "# TODO fix collector\n")   # line 5
    _prime_scan(tmp_path, monkeypatch, dev)
    (tmp_path / "last_emailed.json").write_text(json.dumps({"proposals": [
        {"repo": "devrc", "title": "collector", "evidence": ["devrc/collector.py:5"],
         "effort": "S", "ci_verifiable": True}]}))

    import feedback as feedback_mod
    import llm
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", lambda: _FB("1. approve\n"))

    import clawgate as clawgate_mod
    monkeypatch.setattr(clawgate_mod, "build_task_title", lambda p: p["title"])
    monkeypatch.setattr(clawgate_mod, "build_task_body", lambda p: "body")
    monkeypatch.setattr(clawgate_mod, "post_task", lambda d, b: None)   # FAILURE

    seen = {}

    def fake_synth(cands, *, top, model, feedback=None):
        seen["refs"] = {c["ref"] for c in cands}
        return llm.Synthesis(proposals=[_RealishProp("ok", "devrc")], approx_prompt_tokens=1)
    monkeypatch.setattr(llm, "synthesize", fake_synth)

    args = scan.build_parser().parse_args(["--json", "--repos", str(dev)])
    rc = scan.cmd_scan(args)
    assert rc == 0
    # NOT suppressed → the candidate still reached synthesis (re-proposes).
    assert "devrc/collector.py:5" in seen["refs"]
    st = exclusions.load_state(tmp_path / "exclusions.json")
    assert st["approved"] == {}
