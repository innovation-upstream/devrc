"""scan.py orchestration tests — repo resolution, --no-llm smoke path, arg defaults.

These exercise the CLI wiring without any network (--no-llm never touches OpenRouter).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scan  # noqa: E402


def _write(root: Path, rel: str, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_resolve_repos_override_wins():
    repos = scan.resolve_repos("/a, /b ,/c")
    assert repos == ["/a", "/b", "/c"]


def test_resolve_repos_filters_missing(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    # monkeypatch the default list to one real + one missing
    import scan as s
    orig = s.DEFAULT_REPOS
    s.DEFAULT_REPOS = [str(real), str(tmp_path / "missing")]
    try:
        repos = s.resolve_repos(None)
        assert repos == [str(real)]
    finally:
        s.DEFAULT_REPOS = orig


def test_no_llm_mode_prints_candidates_no_network(tmp_path, capsys):
    _write(tmp_path, "a.py", "# TODO fix the thing\n")
    args = scan.build_parser().parse_args(
        ["--no-llm", "--repos", str(tmp_path)])
    rc = scan.cmd_scan(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "pre-scan" in out
    assert "TODO fix the thing" in out
    assert "marker" in out


def test_no_llm_json_mode(tmp_path, capsys):
    _write(tmp_path, "a.py", "# FIXME broken\n")
    args = scan.build_parser().parse_args(
        ["--candidates-only", "--json", "--repos", str(tmp_path)])
    rc = scan.cmd_scan(args)
    out = capsys.readouterr().out
    assert rc == 0
    import json
    data = json.loads(out)
    assert data["capped_total"] >= 1
    assert any("FIXME" in c["text"] for c in data["candidates"])


def test_empty_repos_errors(capsys):
    args = scan.build_parser().parse_args(["--no-llm", "--repos", ""])
    rc = scan.cmd_scan(args)
    assert rc == 2


def test_dry_run_is_default():
    args = scan.build_parser().parse_args([])
    assert args.dry_run is True
    assert args.email is False


def test_default_flags():
    args = scan.build_parser().parse_args([])
    assert args.top == 5
    assert args.limit_candidates == 60
    assert args.model == "deepseek/deepseek-v4-flash"


class _FakeProp:
    def __init__(self, title):
        self._t = title
    def as_dict(self):
        return {"title": self._t, "evidence": ["r/f.py:1"]}


def test_persist_latest_writes_readable_json(tmp_path, monkeypatch):
    # another session reads latest.json → it must exist with the exact proposals + flag
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path)
    scan._persist_latest([_FakeProp("fix the thing")], subject="🧭 test",
                         candidate_count=3, approx_tokens=42, emailed=True)
    import json
    latest = json.loads((tmp_path / "latest.json").read_text())
    assert latest["emailed"] is True
    assert latest["candidate_count"] == 3
    assert latest["proposals"][0]["title"] == "fix the thing"
    assert "generated_at" in latest
    # a dated history copy is also written
    hist = list((tmp_path / "history").glob("*.json"))
    assert len(hist) == 1


def test_persist_latest_never_raises(monkeypatch):
    # best-effort: an unwritable dir must not crash the run
    monkeypatch.setattr(scan, "PERSIST_DIR", Path("/proc/nonexistent/repo-cos"))
    scan._persist_latest([], subject="s", candidate_count=0, approx_tokens=0, emailed=False)


# ---- REPLY-FEEDBACK wiring (feedback + llm mocked; no network) ----------------------

class _RealishProp:
    def __init__(self, title):
        self.title, self.repo = title, "r"
        self.evidence, self.why = ["r/f.py:1"], "w"
        self.effort, self.approach, self.ci_verifiable = "S", "a", True

    def as_dict(self):
        return {"title": self.title, "repo": self.repo, "evidence": self.evidence,
                "why": self.why, "effort": self.effort, "approach": self.approach,
                "ci_verifiable": self.ci_verifiable}


def _prime_llm_path(tmp_path, monkeypatch):
    """Set up a repo with a real candidate + an API key + persist redirect so cmd_scan
    reaches the LLM branch. Returns the sentinel feedback object the fake fetch yields."""
    _write(tmp_path, "a.py", "# TODO fix the thing\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")


def test_scan_wires_feedback_into_synthesize(tmp_path, monkeypatch):
    _prime_llm_path(tmp_path, monkeypatch)
    import feedback as feedback_mod
    import llm

    sentinel = object()
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", lambda: sentinel)

    seen = {}

    def fake_synth(cands, *, top, model, feedback=None):
        seen["feedback"] = feedback
        return llm.Synthesis(proposals=[_RealishProp("do it")], approx_prompt_tokens=10)

    monkeypatch.setattr(llm, "synthesize", fake_synth)

    args = scan.build_parser().parse_args(["--json", "--repos", str(tmp_path)])
    rc = scan.cmd_scan(args)
    assert rc == 0
    assert seen["feedback"] is sentinel  # feedback was fetched AND passed through


def test_scan_json_reports_feedback_applied(tmp_path, monkeypatch, capsys):
    _prime_llm_path(tmp_path, monkeypatch)
    import feedback as feedback_mod
    import llm

    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", lambda: object())
    monkeypatch.setattr(llm, "synthesize", lambda cands, *, top, model, feedback=None:
                        llm.Synthesis(proposals=[_RealishProp("x")], approx_prompt_tokens=1))

    args = scan.build_parser().parse_args(["--json", "--repos", str(tmp_path)])
    scan.cmd_scan(args)
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["feedback_applied"] is True


def test_scan_no_feedback_flag_skips_fetch(tmp_path, monkeypatch, capsys):
    _prime_llm_path(tmp_path, monkeypatch)
    import feedback as feedback_mod
    import llm

    called = {"fetch": False}

    def spy_fetch():
        called["fetch"] = True
        return object()

    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", spy_fetch)

    seen = {}

    def fake_synth(cands, *, top, model, feedback=None):
        seen["feedback"] = feedback
        return llm.Synthesis(proposals=[_RealishProp("y")], approx_prompt_tokens=1)

    monkeypatch.setattr(llm, "synthesize", fake_synth)

    args = scan.build_parser().parse_args(
        ["--no-feedback", "--json", "--repos", str(tmp_path)])
    scan.cmd_scan(args)
    assert called["fetch"] is False       # fetch skipped
    assert seen["feedback"] is None       # synthesize got no feedback
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["feedback_applied"] is False


def test_scan_feedback_fetch_failure_proceeds(tmp_path, monkeypatch):
    # a raising fetch must not crash the run — synthesis proceeds with feedback=None.
    _prime_llm_path(tmp_path, monkeypatch)
    import feedback as feedback_mod
    import llm

    def boom():
        raise RuntimeError("imap exploded")
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", boom)

    seen = {}

    def fake_synth(cands, *, top, model, feedback=None):
        seen["feedback"] = feedback
        return llm.Synthesis(proposals=[_RealishProp("z")], approx_prompt_tokens=1)

    monkeypatch.setattr(llm, "synthesize", fake_synth)

    args = scan.build_parser().parse_args(["--json", "--repos", str(tmp_path)])
    rc = scan.cmd_scan(args)
    assert rc == 0
    assert seen["feedback"] is None


def test_no_feedback_flag_default_false():
    args = scan.build_parser().parse_args([])
    assert args.no_feedback is False


def test_no_fetch_flag_default_false():
    args = scan.build_parser().parse_args([])
    assert args.no_fetch is False


def test_no_fetch_flag_threads_into_scan_all(tmp_path, monkeypatch):
    # --no-fetch must reach prescan.scan_all as fetch=False (working-tree scan).
    _write(tmp_path, "a.py", "# TODO fix the thing\n")
    seen = {}
    real_scan_all = scan.prescan.scan_all

    def spy(repos, limit, caps=None, *, fetch=True):
        seen["fetch"] = fetch
        return real_scan_all(repos, limit, caps, fetch=fetch)

    monkeypatch.setattr(scan.prescan, "scan_all", spy)
    args = scan.build_parser().parse_args(
        ["--no-llm", "--no-fetch", "--repos", str(tmp_path)])
    rc = scan.cmd_scan(args)
    assert rc == 0
    assert seen["fetch"] is False

    # and the default (no flag) threads fetch=True
    seen.clear()
    args = scan.build_parser().parse_args(["--no-llm", "--repos", str(tmp_path)])
    scan.cmd_scan(args)
    assert seen["fetch"] is True


# ---- INITIATIVE THREADING: router → cmd_scan → last_emailed.json -------------------
# 🔴 THE FEATURE'S CENTRAL WIRING, and until now the one part with NO coverage. Both
# HALVES were tested in isolation — test_routing proves `related_for` resolves slugs, and
# test_approve proves the approve path tags from a HAND-WRITTEN `last_emailed.json` — but
# nothing exercised the JOIN between them: that cmd_scan actually carries the resolved
# slugs from the router into the file the approve path later reads. Mutation testing on
# the merged code confirmed the gap: BOTH `related = routing.related_for(...)` → `[None]*n`
# (scan.py:244) and `related=related` → `related=None` at the write_last_emailed call
# (scan.py:381) left the entire 303-test suite green. If the threading silently broke,
# every Task would post untagged and neither the tests nor production would say a word.
#
# This test drives the REAL cmd_scan end to end with only the two true externalities
# stubbed (the LLM, and the SMTP send), and asserts the slug lands in the durable file.
# It is written to FAIL under either mutation above — verify that before trusting it.
#
# `--email` is required because it is the ONLY path that writes last_emailed.json; the
# send itself is stubbed, so no real digest is ever sent.

def test_cmd_scan_threads_resolved_initiative_slugs_into_last_emailed(tmp_path, monkeypatch):
    import json

    import clawgate
    import exclusions
    import feedback as feedback_mod
    import llm
    import routing

    dev = tmp_path / "devrc"
    dev.mkdir()
    (dev / "a.py").write_text("# TODO fix the thing\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(scan, "PERSIST_DIR", tmp_path / "state")
    monkeypatch.setattr(exclusions, "LAST_EMAILED_FILE", tmp_path / "last_emailed.json")
    monkeypatch.setattr(exclusions, "EXCLUSIONS_FILE", tmp_path / "exclusions.json")
    monkeypatch.setattr(feedback_mod, "fetch_last_feedback", lambda: None)

    # RE-PATCH the suite-wide hermeticity stub (conftest.py pins load_current to an EMPTY
    # store). A later setattr on the same function-scoped monkeypatch wins, and both are
    # undone in reverse at teardown — so this stays hermetic: still no live store read.
    route = routing._route()
    monkeypatch.setattr(route, "load_current", lambda: [
        {"slug": "clawgate-agent-loop", "repo": str(dev),
         "title": "Clawgate agent loop close"},
        {"slug": "tekton-pipeline", "repo": str(dev), "title": "Tekton pipeline migration"},
    ])

    # Two proposals: the first matches an initiative confidently, the second matches
    # nothing — so a bug that stamps a CONSTANT slug on everything also fails here.
    matching = _RealishProp("harden the clawgate agent loop")
    unrelated = _RealishProp("zzz unrelated proposal about nothing")
    for p in (matching, unrelated):
        p.repo, p.why, p.approach = "devrc", "", ""
    monkeypatch.setattr(llm, "synthesize", lambda cands, *, top, model, feedback=None:
                        llm.Synthesis(proposals=[matching, unrelated],
                                      approx_prompt_tokens=1))

    # STUB THE SEND. --email is the only branch that writes last_emailed.json; the real
    # send_digest would deliver an actual digest to Zach's inbox.
    import email_send
    sent = {}

    def fake_send(*, subject, body, **kw):
        sent["subject"], sent["body"] = subject, body
        return "repo-cos@inbox.zacx.dev"

    monkeypatch.setattr(email_send, "send_digest", fake_send)

    args = scan.build_parser().parse_args(["--email", "--repos", str(dev)])
    assert scan.cmd_scan(args) == 0
    assert sent, "the digest send was never reached — nothing wrote last_emailed.json"

    # 1) the resolved slug is DURABLE: it reached the file the approve path reads.
    payload = json.loads((tmp_path / "last_emailed.json").read_text())
    props = payload["proposals"]
    assert len(props) == 2
    assert props[0]["initiative"] == "clawgate-agent-loop", props[0]
    # 2) index-aligned, not blanket-stamped: the unmatched proposal carries no slug.
    assert "initiative" not in props[1], props[1]
    # 3) the whole point of persisting it — the approve path can now build the tag.
    assert clawgate.build_tags(props[0]) == ["initiative:clawgate-agent-loop"]
    assert clawgate.build_tags(props[1]) == []
    # 4) and the emailed digest showed the SAME slug as a breadcrumb (the tag must equal
    #    what Zach saw when he approved).
    assert "↳ relates to: clawgate-agent-loop" in sent["body"]
