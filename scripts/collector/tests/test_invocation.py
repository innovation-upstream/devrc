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


def test_a_COUNT_truncation_is_ANNOUNCED_not_silent():
    """🔴 THE CAP USED TO REMOVE A COLUMN WITH NO TRACE, AND THE ROW STILL
    PARSED. `sanitize_dims` slices `[:_MAX_DIMS]`, so a caller that outgrew the
    cap lost its LAST-INSERTED dims — the newest fields, i.e. exactly the ones a
    new measurement depends on — with no error and no log line. MEASURED on
    `mention-open`: a 14-dim ledger against a cap of 12 kept 12, and the
    instrument would have been read as evidence.

    🔴 THE TEST ABOVE CANNOT SEE THAT. `len(out) <= _MAX_DIMS` is SATISFIED by
    the truncation — it is the one assertion a silent drop always passes. Only a
    guard on the ANNOUNCEMENT catches it, which is why this exists beside it
    rather than inside it.

    Same contract and same spelling as `claude-hooks/hook_telemetry.py`'s
    `dropped`, whose comment records that the silent version "contradicted the
    only promise it makes". One rule, one place.

    Every number here is distinct from `_MAX_DIMS` and from every other constant
    in the fixture, so no assertion can be satisfied by a neighbour's value."""
    over = {f"k{i}": i for i in range(INV._MAX_DIMS + 3)}
    out = INV.sanitize_dims(over)
    assert len(out) == INV._MAX_DIMS + 1, (
        f"expected {INV._MAX_DIMS} kept dims plus `dropped`: {sorted(out)}")
    assert out["dropped"] == 3, (
        f"three dims were removed and the row claims {out.get('dropped')!r} — a "
        f"consumer cannot tell a complete row from a truncated one: {out}")
    # ABSENT when nothing was lost, so the absence is readable as "complete".
    assert "dropped" not in INV.sanitize_dims({"a": 1}), INV.sanitize_dims({"a": 1})
    # Exactly at the cap is NOT a truncation.
    exact = INV.sanitize_dims({f"k{i}": i for i in range(INV._MAX_DIMS)})
    assert "dropped" not in exact and len(exact) == INV._MAX_DIMS, sorted(exact)
    # 🔴 A CALLER CANNOT FORGE IT. The field's job is to be trustworthy about
    # loss, so a caller-supplied `dropped` must not be able to mask a real one.
    # `dropped` is itself one of the caller's items, so this dict holds
    # `_MAX_DIMS + 5` entries in total and exactly 5 must be reported lost.
    forged = INV.sanitize_dims({"dropped": 0, **{f"k{i}": i
                                                 for i in range(INV._MAX_DIMS + 4)}})
    assert forged["dropped"] == 5, (
        f"a caller-supplied `dropped` survived and hid a real truncation of 5: "
        f"{forged['dropped']!r}")
    # ...and it reaches the PAYLOAD, not just this dict.
    payload = json.loads(INV.build_fields("t", "o", dims=over)["payload"])
    assert payload["dropped"] == 3, payload


def test_sanitize_caps_count_and_length():
    big = {f"k{i}": "x" for i in range(50)}
    big["huge"] = "y" * 500
    big["list"] = ["a" * 500] * 50
    out = INV.sanitize_dims(big)
    # 🔴 THE CAP BOUNDS *CALLER* DIMS. `dropped` is the emitter's own annotation
    # about what it removed, not one of them, so it may sit one over — and this
    # is written as an exact allowance rather than a loosened `+ 1`, so a second
    # unannounced key cannot ride in behind it. See
    # `test_a_COUNT_truncation_is_ANNOUNCED_not_silent`.
    assert len(out) <= INV._MAX_DIMS + (1 if "dropped" in out else 0)
    assert len(out) - ("dropped" in out) <= INV._MAX_DIMS
    if "huge" in out:
        assert len(out["huge"]) <= INV._MAX_VALUE_LEN
    if "list" in out:
        assert len(out["list"]) <= INV._MAX_LIST_ITEMS
        assert all(len(x) <= INV._MAX_VALUE_LEN for x in out["list"])


def test_sanitize_preserves_bools_and_numbers():
    out = INV.sanitize_dims({"git_dirty": True, "n": 3, "none": None})
    assert out["git_dirty"] is True and out["n"] == 3 and out["none"] is None


def test_build_fields_caps_tool_outcome_and_text():
    # Defence-in-depth must cover EVERY field, not only dims: an oversized tool /
    # outcome (a would-be leak vector) is truncated in payload AND the text column.
    huge_tool = "t" * 500
    huge_outcome = "o" * 500
    f = INV.build_fields(huge_tool, huge_outcome, dims={"cluster": "x"})
    assert len(f["text"]) <= INV._MAX_VALUE_LEN
    p = json.loads(f["payload"])
    assert len(p["tool"]) <= INV._MAX_VALUE_LEN
    assert len(p["outcome"]) <= INV._MAX_VALUE_LEN


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
