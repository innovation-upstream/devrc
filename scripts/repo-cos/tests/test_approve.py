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


# ==== 2b. REPO RESOLVER + dispatch-config payload ====================================

# fake repos whose BASENAMES we control — no real fs/git needed.
_FAKE_REPOS = ["~/ws/devrc", "~/ws/civit/civitai", "~/ws/kubeclaw-cloud"]


def test_resolve_repo_fullname_ssh_remote():
    def run(path):
        assert path.endswith("/ws/civit/civitai")   # ~ expanded, basename matched
        return "git@github.com:Owner/Repo.git"
    assert clawgate.resolve_repo_fullname("civitai", repos=_FAKE_REPOS, _run=run) == "Owner/Repo"


def test_resolve_repo_fullname_https_remote_with_git_suffix():
    run = lambda p: "https://github.com/Owner/Repo.git"
    assert clawgate.resolve_repo_fullname("devrc", repos=_FAKE_REPOS, _run=run) == "Owner/Repo"


def test_resolve_repo_fullname_https_remote_no_git_suffix():
    run = lambda p: "https://github.com/Owner/Repo"
    assert clawgate.resolve_repo_fullname("devrc", repos=_FAKE_REPOS, _run=run) == "Owner/Repo"


def test_resolve_repo_fullname_ssh_scheme_remote():
    run = lambda p: "ssh://git@github.com/Owner/Repo.git"
    assert clawgate.resolve_repo_fullname("devrc", repos=_FAKE_REPOS, _run=run) == "Owner/Repo"


def test_resolve_repo_fullname_unknown_name_returns_empty():
    # a name whose basename matches nothing in repos → "" (never shells out).
    called = []
    run = lambda p: called.append(p) or "git@github.com:Owner/Repo.git"
    assert clawgate.resolve_repo_fullname("nope", repos=_FAKE_REPOS, _run=run) == ""
    assert called == []          # short-circuits before running git


def test_resolve_repo_fullname_empty_name_returns_empty():
    assert clawgate.resolve_repo_fullname("", repos=_FAKE_REPOS, _run=lambda p: "x") == ""
    assert clawgate.resolve_repo_fullname(None, repos=_FAKE_REPOS, _run=lambda p: "x") == ""


def test_resolve_repo_fullname_run_raises_returns_empty():
    def boom(path):
        raise OSError("git not found")
    assert clawgate.resolve_repo_fullname("devrc", repos=_FAKE_REPOS, _run=boom) == ""


def test_resolve_repo_fullname_empty_remote_returns_empty():
    # _run returning "" (no remote / non-zero exit) → "".
    assert clawgate.resolve_repo_fullname("devrc", repos=_FAKE_REPOS, _run=lambda p: "") == ""


def test_resolve_repo_fullname_non_github_remote_returns_empty():
    run = lambda p: "https://gitlab.com/Owner/Repo.git"
    assert clawgate.resolve_repo_fullname("devrc", repos=_FAKE_REPOS, _run=run) == ""


def test_git_remote_wrapper_non_zero_returns_empty(monkeypatch):
    import subprocess

    class _Proc:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert clawgate._git_remote("/nope") == ""


def test_post_task_includes_repo_when_passed():
    seen = {}

    def fake_post(url, payload, token, timeout=15):
        seen["payload"] = payload
        return json.dumps({"id": 5})

    creds = {"CLAWGATE_API_URL": "http://cg", "CLAWGATE_HOOK_TOKEN": "t"}
    tid = clawgate.post_task("d", "b", repo="Owner/Repo", creds=creds, _post=fake_post)
    assert tid == 5
    assert seen["payload"] == {"directory": "d", "body": "b", "repo": "Owner/Repo"}


def test_post_task_includes_model_when_passed():
    seen = {}
    fake_post = lambda u, payload, t, timeout=15: (seen.update(payload=payload)
                                                   or json.dumps({"id": 6}))
    creds = {"CLAWGATE_API_URL": "http://cg", "CLAWGATE_HOOK_TOKEN": "t"}
    clawgate.post_task("d", "b", repo="O/R", model="opus", creds=creds, _post=fake_post)
    assert seen["payload"] == {"directory": "d", "body": "b", "repo": "O/R", "model": "opus"}


def test_post_task_omits_repo_and_model_when_absent():
    # BACKWARD-COMPAT GUARD: a bare call must send EXACTLY the old 2-key payload.
    seen = {}
    fake_post = lambda u, payload, t, timeout=15: (seen.update(payload=payload)
                                                   or json.dumps({"id": 7}))
    creds = {"CLAWGATE_API_URL": "http://cg", "CLAWGATE_HOOK_TOKEN": "t"}
    clawgate.post_task("d", "b", creds=creds, _post=fake_post)
    assert seen["payload"] == {"directory": "d", "body": "b"}


def test_post_approvals_passes_resolved_repo_to_post_task():
    """The caller wiring: _post_approvals_to_clawgate resolves the repo full-name and threads
    it into post_task(repo=...). Inject a fake clawgate module via the `_clawgate` param."""
    import types
    captured = {}

    def _resolve(name):
        captured["resolve_arg"] = name
        return "Owner/Repo"

    def _post_task(directory, body, *, repo="", model="", tags=None):
        captured["directory"] = directory
        captured["repo"] = repo
        captured["tags"] = tags
        return 4242

    fake_cg = types.SimpleNamespace(
        build_task_title=lambda p: p["title"],
        build_task_body=lambda p: "body",
        resolve_repo_fullname=_resolve,
        build_tags=lambda p: [],
        post_task=_post_task,
    )
    approvals = [{"repo": "civitai", "title": "T",
                  "evidence": ["civitai/src/x.ts:1"]}]
    state = {"repos": {}, "dismissed": {}, "approved": {}}
    scan._post_approvals_to_clawgate(approvals, state, _clawgate=fake_cg)

    assert captured["resolve_arg"] == "civitai"
    assert captured["repo"] == "Owner/Repo"
    # suppress-on-success still records the evidence with the returned task id.
    assert "civitai/src/x.ts:1" in state["approved"]


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
    # HERMETICITY NOTE: cmd_scan also calls routing.related_for → route.load_current(), a
    # LIVE cross-cluster read of the homelab mailbox Postgres. It is stubbed suite-wide by
    # the autouse fixture in tests/conftest.py (which also covers test_dismiss /
    # test_exclusions / test_scan_cli, the other suites that drive the real cmd_scan).


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
        def post_task(directory, body, *, repo="", model="", tags=None):
            posted["directory"] = directory
            posted["body"] = body
            posted["repo"] = repo
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
    monkeypatch.setattr(clawgate_mod, "post_task", lambda d, b, **kw: None)   # FAILURE

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


# ==== 7. `initiative:<slug>` TASK TAGS (clawgate 0.7.75+) ============================
# The digest resolves a confident initiative slug per proposal and persists it into
# last_emailed.json; the approve path reads it POSITIONALLY and stamps the clawgate Task
# with `initiative:<slug>`. Load-bearing invariant: TAGGING MUST NEVER COST AN APPROVAL.

_CREDS = {"CLAWGATE_API_URL": "http://cg", "CLAWGATE_HOOK_TOKEN": "t"}

# clawgate's tag grammar (spec §3): lowercase, [a-z0-9._/-], <=1 ':', <=64 runes.
_TAG_RE = __import__("re").compile(r"^[a-z0-9._/-]+(:[a-z0-9._/-]+)?$")


def _assert_valid_tag(tag: str):
    assert _TAG_RE.match(tag), f"{tag!r} violates the clawgate tag grammar"
    assert len(tag) <= clawgate.TAG_MAX_LEN
    assert tag == tag.lower()


# ---- 7a. normalization / grammar ----------------------------------------------------

def test_normalize_tag_lowercases_and_collapses_whitespace():
    assert clawgate.normalize_tag("Initiative:Remix-Composer") == "initiative:remix-composer"
    assert clawgate.normalize_tag("  initiative:dp prod  sweep ") == "initiative:dp-prod-sweep"
    _assert_valid_tag(clawgate.normalize_tag("Initiative:Remix-Composer"))


def test_normalize_tag_drops_disallowed_characters_rather_than_mangling():
    # A tag that can't be made valid is DROPPED — never silently rewritten into a
    # different (wrong) routing key.
    for bad in ("initiative:remix!composer", "initiative:remix,composer",
                "initiative:rémix", "initiative:remix#1", "initiative:a:b:c"):
        assert clawgate.normalize_tag(bad) is None, bad


def test_normalize_tag_drops_over_64_runes():
    slug = "x" * 60
    tag = f"initiative:{slug}"          # 11 + 60 = 71 runes
    assert len(tag) > clawgate.TAG_MAX_LEN
    assert clawgate.normalize_tag(tag) is None
    ok = f"initiative:{'x' * 53}"       # exactly 64
    assert len(ok) == clawgate.TAG_MAX_LEN
    assert clawgate.normalize_tag(ok) == ok


def test_normalize_tag_drops_empty_and_whitespace():
    for bad in ("", "   ", "\t\n", None, "-", "initiative:"):
        assert clawgate.normalize_tag(bad) is None, repr(bad)


def test_normalize_tags_dedupes_sorts_and_caps_at_20():
    out = clawgate.normalize_tags(["b", "A", "a", "  b  "])
    assert out == ["a", "b"]
    many = [f"initiative:i{i}" for i in range(30)]
    capped = clawgate.normalize_tags(many)
    assert len(capped) == clawgate.TAG_MAX_COUNT
    for t in capped:
        _assert_valid_tag(t)


def test_normalize_tags_of_none_is_empty():
    assert clawgate.normalize_tags(None) == []
    assert clawgate.normalize_tags([]) == []


# ---- 7b. build_tags -----------------------------------------------------------------

def test_build_tags_emits_one_initiative_tag():
    tags = clawgate.build_tags({"initiative": "clawgate-agent-loop-close"})
    assert tags == ["initiative:clawgate-agent-loop-close"]
    _assert_valid_tag(tags[0])


def test_build_tags_drops_a_mixed_case_slug_rather_than_lowercasing_it():
    """A mixed-case slug used to be silently lowercased into `initiative:security-audit-
    v0.1.64` — a tag that no longer equals the ledger slug `SECURITY-AUDIT-v0.1.64`, so the
    initiatives-side join misses. The denylist now drops it (`not-lowercase`) so the emitted
    tag is ALWAYS byte-equal to its slug."""
    assert clawgate.build_tags({"initiative": "SECURITY-AUDIT-v0.1.64"}) == []
    assert clawgate.build_tags({"initiative": "HANDOFF-comfyui-session"}) == []
    # the all-lowercase form of the same slug is still tagged, verbatim.
    assert clawgate.build_tags({"initiative": "security-audit-v0.1.64"}) == [
        "initiative:security-audit-v0.1.64"]


def test_build_tags_no_slug_no_tags():
    assert clawgate.build_tags({}) == []
    assert clawgate.build_tags({"initiative": ""}) == []
    assert clawgate.build_tags({"initiative": None}) == []
    assert clawgate.build_tags(None) == []


def test_build_tags_applies_the_denylist():
    for junk in ("HANDOFF", "2026-07-21", "actionable-next-steps",
                 "868j34n9y-868kf6w7r-complete-mark"):
        assert clawgate.build_tags({"initiative": junk}) == [], junk


def test_build_tags_drops_a_slug_too_long_for_the_grammar():
    # A real store slug can exceed the 64-rune tag cap once prefixed → no tag, no 400.
    long_slug = "alert-civitai-disk-investigate-prioritize-remediation-space"
    assert clawgate.build_tags({"initiative": long_slug}) == []


def test_build_tags_never_raises_on_routing_failure(monkeypatch):
    import routing
    monkeypatch.setattr(routing, "taggable_slug",
                        lambda s: (_ for _ in ()).throw(RuntimeError("router exploded")))
    assert clawgate.build_tags({"initiative": "clawgate-agent-loop-close"}) == []


# ---- 7c. the STRUCTURAL join invariant ----------------------------------------------
# `normalize_tag` performs THREE mutations — lowercase, whitespace→`-`, `.strip("-")` —
# and the routing denylist polices only the first (`not-lowercase`). These slugs clear the
# denylist but WOULD be rewritten by the grammar layer, which is exactly the shape that
# breaks the `tag == slug` join. build_tags must drop them, not emit the rewrite.
_SLUGS_THE_GRAMMAR_WOULD_REWRITE = (
    "foo bar",          # whitespace → '-'  (→ initiative:foo-bar)
    "x  y",             # collapsed run of whitespace
    "trailing-dash-",   # .strip('-')        (→ initiative:trailing-dash)
    "-leading-dash",    # NOT a rewrite: .strip('-') runs on the WHOLE tag, whose first
                        # char is the 'i' of `initiative:`, so this one EMITS VERBATIM
                        # as `initiative:-leading-dash`. Kept in the table as the
                        # asymmetry's negative control (the trailing twin IS rewritten).
    "tab\tseparated",   # _WS_RE covers all whitespace, not just ' '
    " padded-slug ",    # NB: build_tags strips first, so this one is NOT a rewrite
)

# Slugs that must still tag, verbatim — the guard has to be a scalpel, not a mute button.
_SLUGS_THAT_MUST_STILL_TAG = (
    "clawgate-agent-loop-close",
    "deploy-comfyui-video-generation",
    "security-audit-v0.1.64",
    "remix/composer",
    "dp_prod.latency",
)


def test_build_tags_emitted_tag_always_equals_its_slug_exactly():
    """🔴 THE INVARIANT, as a PROPERTY over every shape above: build_tags returns either
    NOTHING or exactly `[f"initiative:{slug}"]`. It may never return a third thing (a
    rewritten tag), because the initiatives-side join matches on the slug byte-for-byte
    and a rewritten tag joins to nothing while looking perfectly healthy.

    NB the `.strip()`: `build_tags` (and `routing.taggable_slug`) strip the slug before
    building the tag, so the promise is byte-equality with the STRIPPED slug — which is
    why `" padded-slug "` is in the table above yet legitimately emits
    `initiative:padded-slug`. That is the ONE normalization the guard permits."""
    for slug in _SLUGS_THE_GRAMMAR_WOULD_REWRITE + _SLUGS_THAT_MUST_STILL_TAG:
        tags = clawgate.build_tags({"initiative": slug})
        assert tags in ([], [f"initiative:{slug.strip()}"]), (slug, tags)
    # 🔴 THE POSITIVE HALF — without it the assertion above is satisfiable by a guard that
    # emits NOTHING, EVER. (Verified: mutating build_tags to `return []` for any slug
    # containing '/' or '_' left the whole suite green before this loop existed. A guard
    # that silently over-drops is undetectable in production — a dropped tag is silent BY
    # DESIGN — so the scalpel-not-mute-button property has to be pinned in the tests.)
    for slug in _SLUGS_THAT_MUST_STILL_TAG:
        assert clawgate.build_tags({"initiative": slug}) == [f"initiative:{slug}"], slug


def test_build_tags_drops_every_slug_the_grammar_would_rewrite():
    """The property asserted STRUCTURALLY rather than by enumerating known shapes: derive
    the 'would be rewritten' set from `normalize_tag` ITSELF, then require build_tags to
    emit nothing for each. If someone adds a FOURTH mutation to normalize_tag, the new
    shape lands in `mutating` automatically and this test covers it without being edited —
    which is the whole point of guarding in build_tags instead of adding a denylist rule
    per mutation."""
    import routing
    mutating = []
    for slug in _SLUGS_THE_GRAMMAR_WOULD_REWRITE:
        keep = routing.taggable_slug(slug)
        if keep is None:
            continue  # already handled upstream by the denylist — not this guard's job
        if clawgate.normalize_tag(f"initiative:{keep}") != f"initiative:{keep}":
            mutating.append(slug)
    # guard against a VACUOUS pass: if none of the shapes reach the grammar layer intact,
    # this test proves nothing and the corpus above needs new shapes.
    assert mutating, "no sample slug survives the denylist AND gets rewritten — vacuous"
    for slug in mutating:
        assert clawgate.build_tags({"initiative": slug}) == [], slug


def test_build_tags_logs_why_it_dropped_a_rewritten_tag(capsys):
    # A silent drop would be indistinguishable from "no initiative resolved" in the weekly
    # run log, so the guard must say which slug it refused and what the grammar wanted.
    assert clawgate.build_tags({"initiative": "foo bar"}) == []
    err = capsys.readouterr().err
    assert "foo bar" in err
    assert "initiative:foo-bar" in err


def test_build_tags_guard_never_raises_and_never_costs_the_post():
    """The load-bearing contract: the guard may only ever DROP a tag — never raise, and
    never make the POST fail. The name used to overclaim (it asserted `isinstance(..., list)`
    and never went near `post_task`), so the post half is now actually exercised: every
    guarded shape is carried through the REAL post_task and must still produce a Task id."""
    posted = []

    def fake_post(url, payload, token, timeout=15):
        posted.append(dict(payload))
        return json.dumps({"id": 42})

    for slug in _SLUGS_THE_GRAMMAR_WOULD_REWRITE + _SLUGS_THAT_MUST_STILL_TAG:
        tags = clawgate.build_tags({"initiative": slug})
        assert isinstance(tags, list)
        tid = clawgate.post_task("d", "b", tags=tags, creds=_CREDS, _post=fake_post)
        assert tid == 42, slug            # the approval is never lost to a tag
        for tag in posted[-1].get("tags", []):
            _assert_valid_tag(tag)        # nothing that would 400 ever reaches the wire


# ---- 7c-bis. the guard is PER TAG (the `runbook:` seam is not a trap) ----------------

def test_guard_tags_keeps_a_second_namespace_and_drops_only_the_offender():
    """Following the `runbook:` seam literally — appending `f"runbook:{name}"` to `raw` —
    must emit BOTH tags. Under the original all-or-nothing guard (whole normalized list
    vs whole wanted list) a two-namespace `raw` compared unequal and dropped EVERY tag on
    EVERY Task, silently. Per-tag, one bad tag costs only itself."""
    both = clawgate.guard_tags(["initiative:remix-composer", "runbook:perf-deep-dive"])
    assert both == ["initiative:remix-composer", "runbook:perf-deep-dive"]  # sorted
    for tag in both:
        _assert_valid_tag(tag)
    # a rewritable tag alongside a good one: only the offender is dropped …
    assert clawgate.guard_tags(["initiative:foo bar", "runbook:perf-deep-dive"]) == [
        "runbook:perf-deep-dive"]
    # … in either order, and for a grammar rejection (over-long) too.
    assert clawgate.guard_tags(["runbook:perf-deep-dive", "initiative:" + "x" * 60]) == [
        "runbook:perf-deep-dive"]
    # and the single-tag behaviour build_tags relies on is unchanged.
    assert clawgate.guard_tags(["initiative:foo bar"]) == []
    assert clawgate.guard_tags([]) == []
    assert clawgate.guard_tags(None) == []


def test_guard_tags_drop_logs_one_accurate_reason_not_two(capsys):
    """A >64-rune tag used to log BOTH the cap message and the join-violation message,
    mis-attributing a length drop to the join guard. 3 live slugs hit this every weekly
    run, and that log line is the only trace the drop leaves."""
    assert clawgate.guard_tags(["initiative:" + "x" * 60]) == []
    err = capsys.readouterr().err
    assert "runes exceeds" in err
    assert "must equal its" not in err, err        # NOT the join-violation message
    assert err.count("dropping tag") == 1, err
    # the rewrite case logs the join violation, and only that.
    assert clawgate.guard_tags(["initiative:foo bar"]) == []
    err = capsys.readouterr().err
    assert "initiative:foo-bar" in err
    assert "runes exceeds" not in err
    assert err.count("dropping tag") == 1, err


def test_build_tags_over_long_slug_logs_only_the_cap_reason(capsys):
    # Same honesty fix, through the REAL emission path (a real store slug, 59 chars).
    assert clawgate.build_tags(
        {"initiative": "alert-civitai-disk-investigate-prioritize-remediation-space"}) == []
    err = capsys.readouterr().err
    assert "runes exceeds" in err
    assert "must equal its" not in err, err


# ---- 7d. post_task payload + back-compat --------------------------------------------

def test_post_task_includes_tags_when_passed():
    seen = {}
    fake_post = lambda u, payload, t, timeout=15: (seen.update(payload=payload)  # noqa: E731
                                                   or json.dumps({"id": 5}))
    tid = clawgate.post_task("d", "b", repo="O/R", tags=["initiative:remix-clips"],
                             creds=_CREDS, _post=fake_post)
    assert tid == 5
    assert seen["payload"] == {"directory": "d", "body": "b", "repo": "O/R",
                               "tags": ["initiative:remix-clips"]}


def test_post_task_omits_tags_key_entirely_when_empty():
    # BACKWARD-COMPAT GUARD: byte-for-byte the pre-tags payload.
    seen = {}
    fake_post = lambda u, payload, t, timeout=15: (seen.update(payload=payload)  # noqa: E731
                                                   or json.dumps({"id": 1}))
    clawgate.post_task("d", "b", creds=_CREDS, _post=fake_post)
    assert seen["payload"] == {"directory": "d", "body": "b"}
    clawgate.post_task("d", "b", tags=[], creds=_CREDS, _post=fake_post)
    assert seen["payload"] == {"directory": "d", "body": "b"}
    clawgate.post_task("d", "b", tags=["!!!"], creds=_CREDS, _post=fake_post)
    assert seen["payload"] == {"directory": "d", "body": "b"}   # invalid tag → key absent


# ---- 7e. FAIL-OPEN: a tag must never cost an approval --------------------------------

def _http_error(code):
    import urllib.error
    return urllib.error.HTTPError("http://cg/api/tasks", code, "Bad Request", {}, None)


def test_post_task_retries_once_without_tags_on_400():
    calls = []

    def flaky(url, payload, token, timeout=15):
        calls.append(dict(payload))
        if "tags" in payload:
            raise _http_error(400)
        return json.dumps({"id": 77})

    tid = clawgate.post_task("d", "b", repo="O/R", tags=["initiative:whatever-x"],
                             creds=_CREDS, _post=flaky)
    assert tid == 77                       # the approval is NOT lost
    assert len(calls) == 2                 # EXACTLY one retry
    assert "tags" in calls[0]
    assert "tags" not in calls[1]
    # byte-identical to the pre-tags payload: same keys, same INSERTION ORDER.
    assert calls[1] == {"directory": "d", "body": "b", "repo": "O/R"}
    assert list(calls[1]) == ["directory", "body", "repo"]


def test_post_task_retries_only_on_400_not_other_4xx():
    """400 is the ONLY grammar-rejection code (clawgate task-tags spec §6). 401/403 (rotated
    hook token), 404 (wrong URL) and 429 (rate limit) earn a doomed second request; 408 is
    worse — the origin may already have created the Task, so a retry DOUBLE-POSTS."""
    for code in (401, 403, 404, 408, 429, 451):
        calls = []

        def always(url, payload, token, timeout=15, _c=code):
            calls.append(dict(payload))
            raise _http_error(_c)

        assert clawgate.post_task("d", "b", tags=["initiative:x-y"],
                                  creds=_CREDS, _post=always) is None, code
        assert len(calls) == 1, f"HTTP {code} must NOT be retried (got {len(calls)} calls)"
        assert "tags" in calls[0]


def test_post_task_retry_logs_the_failure_and_the_retry(capsys):
    def flaky(url, payload, token, timeout=15):
        if "tags" in payload:
            raise _http_error(400)
        return json.dumps({"id": 8})

    clawgate.post_task("d", "b", tags=["initiative:x-y"], creds=_CREDS, _post=flaky)
    err = capsys.readouterr().err
    assert "HTTP 400" in err
    assert "retrying ONCE without tags" in err


def test_post_task_untagged_4xx_is_not_retried():
    calls = []

    def always_400(url, payload, token, timeout=15):
        calls.append(dict(payload))
        raise _http_error(400)

    assert clawgate.post_task("d", "b", creds=_CREDS, _post=always_400) is None
    assert len(calls) == 1


def test_post_task_connection_error_does_not_loop():
    calls = []

    def boom(url, payload, token, timeout=15):
        calls.append(dict(payload))
        raise OSError("connection refused")

    assert clawgate.post_task("d", "b", tags=["initiative:x-y"],
                              creds=_CREDS, _post=boom) is None
    assert len(calls) == 1   # NOT a tag problem → no retry, no loop


def test_post_task_5xx_is_not_retried_without_tags():
    calls = []

    def five_hundred(url, payload, token, timeout=15):
        calls.append(dict(payload))
        raise _http_error(503)

    assert clawgate.post_task("d", "b", tags=["initiative:x-y"],
                              creds=_CREDS, _post=five_hundred) is None
    assert len(calls) == 1


def test_post_task_retry_that_also_fails_returns_none():
    calls = []

    def always(url, payload, token, timeout=15):
        calls.append(dict(payload))
        raise _http_error(400)

    assert clawgate.post_task("d", "b", tags=["initiative:x-y"],
                              creds=_CREDS, _post=always) is None
    assert len(calls) == 2   # tagged + one untagged retry, then give up


# ---- 7f. PERSISTENCE: last_emailed.json round-trips the slug -------------------------

def test_write_last_emailed_persists_the_initiative_slug(tmp_path):
    path = tmp_path / "last_emailed.json"
    props = [dict(EMAILED[0]), dict(EMAILED[1])]
    exclusions.write_last_emailed(props, subject="s", generated_at="t",
                                  related=["clawgate-agent-loop-close", None], path=path)
    saved = json.loads(path.read_text())["proposals"]
    assert saved[0]["initiative"] == "clawgate-agent-loop-close"
    assert "initiative" not in saved[1]      # no confident match → no field


def test_write_last_emailed_without_related_is_unchanged(tmp_path):
    path = tmp_path / "last_emailed.json"
    exclusions.write_last_emailed([dict(EMAILED[0])], subject="s", generated_at="t",
                                  path=path)
    saved = json.loads(path.read_text())["proposals"]
    assert "initiative" not in saved[0]


def test_write_last_emailed_skips_attaching_on_a_MISALIGNED_related_list(tmp_path, capsys):
    """A `related` list that isn't index-aligned means the caller reordered/filtered one of
    the two lists — positional stamping would then tag the WRONG initiative. Degrade to NO
    tags (and say so loudly); a missing tag is cheap, a wrong routing key is not."""
    path = tmp_path / "last_emailed.json"
    exclusions.write_last_emailed([dict(EMAILED[0]), dict(EMAILED[1])], subject="s",
                                  generated_at="t", related=["only-first"], path=path)
    saved = json.loads(path.read_text())["proposals"]
    assert "initiative" not in saved[0]
    assert "initiative" not in saved[1]
    err = capsys.readouterr().err
    assert "MISALIGNED" in err
    assert "1 slug(s) for 2 proposal(s)" in err


# ---- 7f-bis. attach_related: the positional-misalignment guard, directly -------------

def test_attach_related_stamps_when_lengths_match():
    props = [{"title": "a"}, {"title": "b"}]
    out = exclusions.attach_related(props, ["arc-one", None])
    assert out is props                       # mutates + returns the same list
    assert props[0]["initiative"] == "arc-one"
    assert "initiative" not in props[1]       # None slug → no field


def test_attach_related_is_a_noop_for_empty_or_none_related():
    for related in (None, []):
        props = [{"title": "a"}, {"title": "b"}]
        exclusions.attach_related(props, related)
        assert all("initiative" not in p for p in props), related


def test_attach_related_skips_everything_and_warns_when_lengths_differ(capsys):
    # TOO SHORT …
    props = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
    exclusions.attach_related(props, ["arc-one", "arc-two"])
    assert all("initiative" not in p for p in props)
    err = capsys.readouterr().err
    assert "MISALIGNED" in err and "2 slug(s) for 3 proposal(s)" in err
    # … and TOO LONG (a filtered proposal list is the likelier future regression).
    props = [{"title": "a"}]
    exclusions.attach_related(props, ["arc-one", "arc-two"])
    assert "initiative" not in props[0]
    assert "MISALIGNED" in capsys.readouterr().err


def test_attach_related_never_raises_on_a_junk_related_value(capsys):
    # best-effort contract: an unsized/odd `related` is logged, never raised.
    props = [{"title": "a"}]
    assert exclusions.attach_related(props, object()) is props
    assert "initiative" not in props[0]
    assert capsys.readouterr().err != ""


def test_approve_payload_carries_the_persisted_slug():
    emailed = [dict(EMAILED[0], initiative="clawgate-agent-loop-close")]
    approvals = exclusions.parse_reply("1. approve\n", emailed, alias_map=_alias())["approve"]
    assert approvals[0]["initiative"] == "clawgate-agent-loop-close"
    assert clawgate.build_tags(approvals[0]) == ["initiative:clawgate-agent-loop-close"]


def test_approve_payload_on_an_OLD_last_emailed_file_yields_no_tag():
    # A last_emailed.json written BEFORE this feature has no `initiative` key: no tag,
    # no crash, and NO re-resolution (the approve path must gain no I/O).
    approvals = exclusions.parse_reply("1. approve\n", EMAILED, alias_map=_alias())["approve"]
    assert approvals[0]["initiative"] == ""
    assert clawgate.build_tags(approvals[0]) == []


# ---- 7g. approve path end-to-end: tagged vs untagged ---------------------------------

def _fake_clawgate(captured, *, tid=4242):
    import types
    return types.SimpleNamespace(
        build_task_title=lambda p: p["title"],
        build_task_body=lambda p: "body",
        resolve_repo_fullname=lambda name: "Owner/Repo",
        build_tags=clawgate.build_tags,
        post_task=lambda directory, body, *, repo="", model="", tags=None: (
            captured.update(directory=directory, repo=repo, tags=tags) or tid),
    )


def test_approve_path_tags_the_task_with_the_persisted_slug():
    captured = {}
    approvals = [{"repo": "civitai", "title": "T", "evidence": ["civitai/src/x.ts:1"],
                  "initiative": "app-blocks-w13-external-listings"}]
    state = {"repos": {}, "dismissed": {}, "approved": {}}
    scan._post_approvals_to_clawgate(approvals, state, _clawgate=_fake_clawgate(captured))
    assert captured["tags"] == ["initiative:app-blocks-w13-external-listings"]
    _assert_valid_tag(captured["tags"][0])
    # the approval is still recorded/suppressed
    assert state["approved"]["civitai/src/x.ts:1"]["clawgate_task_id"] == 4242


def test_approve_path_without_a_slug_sends_no_tags():
    captured = {}
    approvals = [{"repo": "civitai", "title": "T", "evidence": ["civitai/src/x.ts:1"]}]
    state = {"repos": {}, "dismissed": {}, "approved": {}}
    scan._post_approvals_to_clawgate(approvals, state, _clawgate=_fake_clawgate(captured))
    assert captured["tags"] == []
    assert state["approved"]["civitai/src/x.ts:1"]["clawgate_task_id"] == 4242


def test_approve_path_with_a_junk_slug_sends_no_tags():
    captured = {}
    approvals = [{"repo": "civitai", "title": "T", "evidence": ["civitai/src/x.ts:1"],
                  "initiative": "SESSION-HANDOFF"}]
    state = {"repos": {}, "dismissed": {}, "approved": {}}
    scan._post_approvals_to_clawgate(approvals, state, _clawgate=_fake_clawgate(captured))
    assert captured["tags"] == []
    assert state["approved"]["civitai/src/x.ts:1"]["clawgate_task_id"] == 4242


def test_approve_path_tagged_post_that_400s_still_records_the_approval():
    """END-TO-END fail-open: clawgate 400s the tagged payload → the real post_task retries
    untagged → a task id comes back → the evidence is STILL suppressed (no lost approval)."""
    calls = []

    def flaky(url, payload, token, timeout=15):
        calls.append(dict(payload))
        if "tags" in payload:
            raise _http_error(400)
        return json.dumps({"id": 909})

    import types
    fake_cg = types.SimpleNamespace(
        build_task_title=lambda p: p["title"],
        build_task_body=lambda p: "body",
        resolve_repo_fullname=lambda name: "Owner/Repo",
        build_tags=clawgate.build_tags,
        post_task=lambda directory, body, *, repo="", model="", tags=None:
            clawgate.post_task(directory, body, repo=repo, model=model, tags=tags,
                               creds=_CREDS, _post=flaky),
    )
    approvals = [{"repo": "civitai", "title": "T", "evidence": ["civitai/src/x.ts:1"],
                  "initiative": "dp-prod-latency-sweep"}]
    state = {"repos": {}, "dismissed": {}, "approved": {}}
    scan._post_approvals_to_clawgate(approvals, state, _clawgate=fake_cg)
    assert len(calls) == 2
    assert state["approved"]["civitai/src/x.ts:1"]["clawgate_task_id"] == 909
