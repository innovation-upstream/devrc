"""select.choose — settled / un-extracted / limit / settle-bypass logic over
fixture rows + synthetic transcript mtimes (no live ClickHouse)."""
import importlib.util
from pathlib import Path

# Load select.py under a distinct name — the bare name `select` is a stdlib
# module (already imported by the interpreter), so a plain `import select` would
# resolve to THAT, not our sibling. Loading by file path sidesteps the clash.
_spec = importlib.util.spec_from_file_location(
    "si_select", Path(__file__).resolve().parent.parent / "select.py")
sel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sel)

NOW = 1_000_000.0
SETTLE = 6 * 3600  # 21600s


def _rollup(session, project="devrc", end_ts=None, cwd="/home/zach/workspace/devrc"):
    payload = {"cwd": cwd}
    if end_ts:
        payload["end_ts"] = end_ts
    return {"session": session, "project": project, "payload": payload,
            "summary_ts": "2026-07-10 10:00:00.000"}


def _tr(mtime):
    return {"path": f"/x/{mtime}.jsonl", "mtime": mtime}


def test_settled_unextracted_is_a_candidate():
    rollups = [_rollup("s1")]
    la = {"s1": NOW - 2 * SETTLE}          # idle well past settle
    trs = {"s1": _tr(NOW - 2 * SETTLE)}    # file also old
    cands, skips = sel.choose(rollups, la, {}, trs, NOW, SETTLE, None, False)
    assert [c["session"] for c in cands] == ["s1"]
    assert cands[0]["project"] == "devrc"
    assert cands[0]["cwd"].endswith("devrc")
    assert skips == []


def test_recent_activity_not_settled():
    rollups = [_rollup("s1")]
    la = {"s1": NOW - 100}                 # just active
    trs = {"s1": _tr(NOW - 2 * SETTLE)}
    cands, skips = sel.choose(rollups, la, {}, trs, NOW, SETTLE, None, False)
    assert cands == []
    assert ("s1", "not-settled") in skips


def test_recent_transcript_mtime_not_settled():
    rollups = [_rollup("s1")]
    la = {"s1": NOW - 2 * SETTLE}
    trs = {"s1": _tr(NOW - 100)}           # ts old but file just written
    cands, skips = sel.choose(rollups, la, {}, trs, NOW, SETTLE, None, False)
    assert cands == []
    assert ("s1", "not-settled") in skips


def test_already_extracted_skipped():
    rollups = [_rollup("s1")]
    la = {"s1": NOW - 2 * SETTLE}
    trs = {"s1": _tr(NOW - 2 * SETTLE)}
    extracted = {"s1": {"was_unreadable": False, "insight_ts": NOW - SETTLE}}
    cands, skips = sel.choose(rollups, la, extracted, trs, NOW, SETTLE, None, False)
    assert cands == []
    assert ("s1", "already-extracted") in skips


def test_prior_unreadable_reattempted_only_when_transcript_grew():
    rollups = [_rollup("grew"), _rollup("stale")]
    la = {"grew": NOW - 2 * SETTLE, "stale": NOW - 2 * SETTLE}
    # both transcripts are settled (mtime older than SETTLE)
    trs = {"grew": _tr(NOW - 2 * SETTLE), "stale": _tr(NOW - 2 * SETTLE)}
    extracted = {
        "grew": {"was_unreadable": True, "insight_ts": NOW - 3 * SETTLE},   # file newer than insight
        "stale": {"was_unreadable": True, "insight_ts": NOW - SETTLE},      # file older than insight
    }
    cands, skips = sel.choose(rollups, la, extracted, trs, NOW, SETTLE, None, False)
    names = [c["session"] for c in cands]
    assert "grew" in names
    assert "stale" not in names
    assert ("stale", "already-extracted") in skips


def test_no_transcript_skipped():
    rollups = [_rollup("s1")]
    la = {"s1": NOW - 2 * SETTLE}
    cands, skips = sel.choose(rollups, la, {}, {}, NOW, SETTLE, None, False)
    assert cands == []
    assert ("s1", "no-transcript") in skips


def test_limit_truncates_with_over_limit_skips():
    rollups = [_rollup("a"), _rollup("b"), _rollup("c")]
    la = {"a": NOW - SETTLE * 2, "b": NOW - SETTLE * 3, "c": NOW - SETTLE * 4}
    trs = {s: _tr(NOW - SETTLE * 2) for s in ("a", "b", "c")}
    cands, skips = sel.choose(rollups, la, {}, trs, NOW, SETTLE, 1, False)
    assert len(cands) == 1
    assert cands[0]["session"] == "a"      # most-recently-active first
    over = [s for s, r in skips if r == "over-limit"]
    assert set(over) == {"b", "c"}


def test_settle_zero_bypasses_gate():
    rollups = [_rollup("s1")]
    la = {"s1": NOW - 10}                   # very recent
    trs = {"s1": _tr(NOW - 10)}
    cands, _ = sel.choose(rollups, la, {}, trs, NOW, 0, None, False)
    assert [c["session"] for c in cands] == ["s1"]


def test_force_bypasses_settle_and_extracted():
    rollups = [_rollup("s1")]
    la = {"s1": NOW - 10}
    trs = {"s1": _tr(NOW - 10)}
    extracted = {"s1": {"was_unreadable": False, "insight_ts": NOW - 5}}
    cands, _ = sel.choose(rollups, la, extracted, trs, NOW, SETTLE, None, True)
    assert [c["session"] for c in cands] == ["s1"]


def test_end_ts_preferred_over_last_activity_for_settle():
    # end_ts is recent → not settled, even though last_activity is old.
    rollups = [_rollup("s1", end_ts="2026-07-11 09:00:00.000")]
    la = {"s1": NOW - 5 * SETTLE}
    trs = {"s1": _tr(NOW - 5 * SETTLE)}
    # now just after end_ts → within settle window
    import _shared
    now = _shared.ch_ts_to_epoch("2026-07-11 09:05:00.000")
    cands, skips = sel.choose(rollups, la, {}, trs, now, SETTLE, None, False)
    assert ("s1", "not-settled") in skips


def test_queries_are_alias_shadow_safe():
    import re
    op = re.compile(r"\b([a-zA-Z_]\w*)\s*(?:>=|<=|=|>|<)")
    for sql in (sel.q_rollups(86400, "workbench"),
                sel.q_last_activity(86400, "workbench"),
                sel.q_extracted("workbench")):
        aliases = set(re.findall(r"\bAS\s+(\w+)", sql))
        where = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
        where_cols = set(op.findall(where))
        assert not (aliases & where_cols), f"alias shadows WHERE col in: {sql}"
