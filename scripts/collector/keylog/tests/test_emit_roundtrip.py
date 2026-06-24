"""Round-trip: spool_emit builds a v1 line that collector.parse_line accepts,
with arbitrary content (quotes / newlines / unicode / a fake password) surviving
intact — proving the keylogger output ships unchanged through the existing daemon.
"""
import json
import sys
from pathlib import Path

KEYLOG = Path(__file__).resolve().parent.parent
COLLECTOR = KEYLOG.parent  # scripts/collector
sys.path.insert(0, str(KEYLOG))
sys.path.insert(0, str(COLLECTOR))

import spool_emit as SE  # noqa: E402
import collector as C    # noqa: E402


def test_build_line_roundtrips_through_parse_line():
    line = SE.build_line({
        "source": "keys", "kind": "typing",
        "text": "hello", "app": "xterm", "session": "w1",
        "payload": json.dumps({"title": "t", "workspace": "2"}),
    })
    ev = C.parse_line(line)
    assert ev is not None
    assert ev["source"] == "keys"
    assert ev["kind"] == "typing"
    assert ev["text"] == "hello"
    assert ev["app"] == "xterm"
    assert json.loads(ev["payload"])["workspace"] == "2"


def test_arbitrary_content_survives():
    nasty = 'rm -rf "$X"\twith\ttabs\nand a newline \\back\\slash 你好 password123!'
    line = SE.build_line({"source": "keys", "kind": "typing", "text": nasty})
    ev = C.parse_line(line)
    assert ev["text"] == nasty


def test_ts_and_host_autofilled():
    line = SE.build_line({"source": "keys", "kind": "typing", "text": "x"})
    ev = C.parse_line(line)
    assert "ts" in ev
    assert "host" in line  # host present as a plain token


def test_emit_appends_to_spool(tmp_path):
    spool = tmp_path / "spool"
    written = SE.emit(
        {"source": "keys", "kind": "typing", "text": "你好 probe!"},
        spool_dir=spool,
    )
    cur = spool / "current.log"
    assert cur.exists()
    lines = [l for l in cur.read_text(encoding="utf-8").splitlines() if l]
    assert lines == [written]
    ev = C.parse_line(lines[0])
    assert ev["text"] == "你好 probe!"


def test_plain_keys_not_base64(tmp_path):
    line = SE.build_line({
        "source": "keys", "kind": "typing",
        "duration_ms": 5, "text": "z",
    })
    # source/kind/duration_ms are plain; text is base64.
    assert "source=keys" in line
    assert "kind=typing" in line
    assert "duration_ms=5" in line
    assert "b64:text=" in line
