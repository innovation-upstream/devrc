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
