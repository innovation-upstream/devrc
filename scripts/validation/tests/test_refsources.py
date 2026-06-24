"""Unit tests for the reference readers (zsh / chrome / tmux / claude)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import refsources as RS  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


# --------------------------------------------------------------------------- #
# zsh history
# --------------------------------------------------------------------------- #
def test_zsh_plain_format():
    recs = RS.read_zsh_history(FIX / "zsh_history_plain.txt")
    cmds = [r["command"] for r in recs]
    assert "git status" in cmds
    assert "echo done" in cmds
    assert all(r["ts"] is None for r in recs)  # plain format has no timestamps
    assert len(recs) == 4


def test_zsh_extended_format_with_timestamps():
    recs = RS.read_zsh_history(FIX / "zsh_history_extended.txt")
    assert recs[0]["command"] == "git status"
    assert recs[0]["ts"] == 1718000000.0
    # multi-line continuation joined
    multiline = [r for r in recs if r["command"].startswith("for f in")]
    assert multiline and "echo $f" in multiline[0]["command"]
    assert "done" in multiline[0]["command"]


def test_zsh_missing_file():
    assert RS.read_zsh_history(Path("/nonexistent/zsh_history")) == []


# --------------------------------------------------------------------------- #
# chrome history
# --------------------------------------------------------------------------- #
def test_chrome_time_conversion():
    # 1601 epoch + microseconds. A known UTC instant round-trips.
    dt = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)
    micros = int((dt - RS._CHROME_EPOCH).total_seconds() * 1_000_000)
    back = RS.chrome_time_to_dt(micros)
    assert abs((back - dt).total_seconds()) < 0.001


def test_read_chrome_history_all():
    recs = RS.read_chrome_history(FIX / "chrome_history.sqlite", copy_first=True)
    urls = {r["url"] for r in recs}
    assert "https://example.com/a" in urls
    assert "https://example.com/b" in urls
    assert "https://old.example.com/x" in urls
    assert len(recs) == 3


def test_read_chrome_history_since_filter():
    # only visits at/after 2026-06-24 should pass; the 2020 visit is dropped.
    since = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    recs = RS.read_chrome_history(FIX / "chrome_history.sqlite", since_epoch=since)
    urls = {r["url"] for r in recs}
    assert "https://old.example.com/x" not in urls
    assert len(recs) == 2


def test_read_chrome_history_missing_db():
    assert RS.read_chrome_history(Path("/nonexistent/History")) == []


# --------------------------------------------------------------------------- #
# tmux
# --------------------------------------------------------------------------- #
def test_read_tmux_tasks(tmp_path):
    (tmp_path / "0.json").write_text(json.dumps({"task": "homelab", "status": "done"}))
    (tmp_path / "1.json").write_text(json.dumps({"task": "devrc"}))
    (tmp_path / "bad.json").write_text("{not valid json")
    tasks = RS.read_tmux_tasks(tmp_path)
    names = {t["task"] for t in tasks}
    assert names == {"homelab", "devrc"}  # bad json skipped


def test_read_tmux_activity(tmp_path):
    (tmp_path / "258").write_text("1782303590\n")
    (tmp_path / "285").write_text("1782300000")
    (tmp_path / "garbage").write_text("notanumber")
    acts = RS.read_tmux_activity(tmp_path)
    windows = {a["window"] for a in acts}
    assert "258" in windows and "285" in windows
    assert "garbage" not in windows


def test_read_tmux_missing():
    assert RS.read_tmux_tasks(Path("/nope")) == []
    assert RS.read_tmux_activity(Path("/nope")) == []


# --------------------------------------------------------------------------- #
# claude jsonl
# --------------------------------------------------------------------------- #
def test_parse_claude_jsonl_excludes_tool_results():
    text = (FIX / "claude_session.jsonl").read_text()
    recs = RS.parse_claude_jsonl(text)
    # 3 type=user lines, but one is a tool_result echo -> excluded. The old 2025
    # one is a real prompt and IS counted here (window filtering is separate).
    assert len(recs) == 3
    for r in recs:
        assert r["session"] == "sess-1"


def test_count_claude_user_msgs_window():
    p = FIX / "claude_session.jsonl"
    since = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
    # within window: 2 real prompts on 2026-06-24 (tool_result + 2025 excluded)
    assert RS.count_claude_user_msgs([p], since_epoch=since) == 2


def test_count_claude_no_window():
    p = FIX / "claude_session.jsonl"
    # all 3 real prompts regardless of date
    assert RS.count_claude_user_msgs([p]) == 3


def test_count_claude_missing_files():
    assert RS.count_claude_user_msgs([Path("/nope.jsonl")]) == 0
