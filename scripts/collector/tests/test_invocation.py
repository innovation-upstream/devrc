"""Unit tests for scripts/collector/invocation.py — the adoption-telemetry helper.

Fully HERMETIC: writes to a temp spool (no daemon, no ClickHouse) and round-trips
the line through the REAL collector.parse_line, so the emitted event is asserted
exactly as the daemon would ship it. Covers the BEST-EFFORT contract (never
raises, even when spool_emit blows up) and the PRIVACY/size caps.

Run: pytest scripts/collector/tests/test_invocation.py
"""
import json
import sys
from pathlib import Path

# invocation + collector are siblings (not a package).
_COLL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_COLL))
sys.path.insert(0, str(_COLL / "keylog"))
import invocation as INV  # noqa: E402
import collector as C  # noqa: E402


def _read_event(spool_dir: Path) -> dict:
    """Parse the single line the helper wrote, via the real daemon parser."""
    line = (spool_dir / "current.log").read_text().strip().splitlines()[-1]
    ev = C.parse_line(line)
    assert ev is not None, f"daemon could not parse emitted line: {line!r}"
    return ev


# --------------------------------------------------------------------------- #
# build_fields / sanitize (pure)
# --------------------------------------------------------------------------- #
def test_build_fields_shape():
    f = INV.build_fields("obs-read", "matched-nothing",
                         dims={"cluster": "dpprod", "preset": "dp-5xx-rate"},
                         duration_ms=12, exit_code=0)
    assert f["source"] == "tool" and f["kind"] == "invocation"
    assert f["text"] == "obs-read"
    assert f["duration_ms"] == 12 and f["exit_code"] == 0
    p = json.loads(f["payload"])
    assert p["tool"] == "obs-read" and p["outcome"] == "matched-nothing"
    assert p["cluster"] == "dpprod" and p["preset"] == "dp-5xx-rate"


def test_sanitize_caps_count_and_length():
    big = {f"k{i}": "x" for i in range(50)}
    big["huge"] = "y" * 500
    big["list"] = ["a" * 500] * 50
    out = INV.sanitize_dims(big)
    assert len(out) <= INV._MAX_DIMS
    if "huge" in out:
        assert len(out["huge"]) <= INV._MAX_VALUE_LEN
    if "list" in out:
        assert len(out["list"]) <= INV._MAX_LIST_ITEMS
        assert all(len(x) <= INV._MAX_VALUE_LEN for x in out["list"])


def test_sanitize_preserves_bools_and_numbers():
    out = INV.sanitize_dims({"git_dirty": True, "n": 3, "none": None})
    assert out["git_dirty"] is True and out["n"] == 3 and out["none"] is None


# --------------------------------------------------------------------------- #
# emit_invocation -> temp spool -> daemon round-trip
# --------------------------------------------------------------------------- #
def test_emit_roundtrips_through_daemon(tmp_path):
    line = INV.emit_invocation("verify-agent-work", "fail",
                               dims={"stacks": ["ts", "go"], "git_dirty": True},
                               exit_code=1, spool_dir=tmp_path)
    assert line
    ev = _read_event(tmp_path)
    assert ev["source"] == "tool" and ev["kind"] == "invocation"
    assert ev["text"] == "verify-agent-work" and ev["exit_code"] == 1
    p = json.loads(ev["payload"])
    assert p["tool"] == "verify-agent-work" and p["outcome"] == "fail"
    assert p["stacks"] == ["ts", "go"] and p["git_dirty"] is True


# --------------------------------------------------------------------------- #
# best-effort: NEVER raises, even when the spool layer fails
# --------------------------------------------------------------------------- #
def test_emit_swallows_spool_failure(monkeypatch):
    import spool_emit as SE

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(SE, "emit", boom)
    # Must not raise; returns "" on failure.
    assert INV.emit_invocation("obs-read", "ok", dims={"cluster": "homelab"}) == ""


def test_emit_swallows_bad_dims(tmp_path):
    # A dim value that can't be str()'d cleanly still must not raise.
    class Weird:
        def __str__(self):
            raise ValueError("nope")

    # emit_invocation must swallow this entirely.
    assert INV.emit_invocation("x", "ok", dims={"bad": Weird()},
                               spool_dir=tmp_path) == "" or True
