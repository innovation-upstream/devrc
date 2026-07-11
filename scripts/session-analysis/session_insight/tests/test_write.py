"""write — emit argv shape (subprocess mocked), ts = rollup end_ts, idempotency
skip + --force re-emit, unreadable rows ARE emitted."""
import json

import prepare
import write


def _valid(session, **over):
    p = {
        "schema_version": 1, "session": session, "underlying_goal": "g",
        "goal_categories": ["infra"], "outcome": "mostly_achieved",
        "session_type": "feature_build", "claude_helpfulness": 4,
        "friction_counts": {}, "friction_detail": [], "primary_success": "s",
        "brief_summary": "did it", "automation_opportunity": None,
        "recurring_toil": None, "workflow_gap": None,
        "unreadable": False, "unreadable_reason": "",
    }
    p.update(over)
    return p


def _setup_run(tmp_path, monkeypatch, run_id="runW", result=None):
    monkeypatch.setenv("INSIGHT_STATE_DIR", str(tmp_path / "state"))
    tr = tmp_path / "t.jsonl"
    tr.write_text(json.dumps({"type": "user", "message": {"role": "user",
                  "content": "hi"}}) + "\n", encoding="utf-8")
    cand = {"session": "sessW", "project": "devrc",
            "cwd": "/home/zach/workspace/devrc", "transcript_path": str(tr),
            "end_ts": "2026-07-10 12:00:00.000",
            "summary_ts": "2026-07-10 11:00:00.000", "ground_truth": {}}
    prepare.prepare_run([cand], [], run_id)
    rdir = prepare.results_dir(run_id)
    payload = result if result is not None else _valid("sessW")
    (rdir / "sessW.result.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_id


class _Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, check=None):
        self.calls.append(argv)


def test_emit_argv_shape_and_ts(tmp_path, monkeypatch):
    run_id = _setup_run(tmp_path, monkeypatch)
    runner = _Runner()
    summary = write.write_run(run_id, emit_bin="/fake/emit", runner=runner)
    assert summary["emitted"] == ["sessW"]
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    assert argv[0] == "/fake/emit"
    assert argv[1:7] == ["source=claude", "kind=session-insight",
                         "b64:session=sessW", "b64:project=devrc",
                         "b64:cwd=/home/zach/workspace/devrc", "b64:text=did it"]
    assert argv[7].startswith("b64:payload=")
    assert argv[8] == "ts=2026-07-10 12:00:00.000"        # rollup end_ts, not emit-time
    # payload round-trips
    payload = json.loads(argv[7][len("b64:payload="):])
    assert payload["session"] == "sessW" and payload["outcome"] == "mostly_achieved"


def test_build_emit_args_exact():
    args = write.build_emit_args("s", "proj", "/c", "summary text",
                                 {"session": "s", "x": 1}, "2026-01-01 00:00:00.000")
    assert args == [
        "source=claude", "kind=session-insight", "b64:session=s",
        "b64:project=proj", "b64:cwd=/c", "b64:text=summary text",
        'b64:payload={"session":"s","x":1}', "ts=2026-01-01 00:00:00.000",
    ]


def test_build_emit_args_omits_ts_when_none():
    args = write.build_emit_args("s", "p", "/c", "t", {"session": "s"}, None)
    assert not any(a.startswith("ts=") for a in args)


def test_idempotency_skip_and_force_reemit(tmp_path, monkeypatch):
    # keep=True so the per-session purge doesn't remove the result between writes
    # — this test isolates the idempotency-marker / --force contract from cleanup.
    run_id = _setup_run(tmp_path, monkeypatch)
    runner = _Runner()
    write.write_run(run_id, keep=True, emit_bin="/fake/emit", runner=runner)
    assert len(runner.calls) == 1

    # second write with no --force → skipped (no delete path, just a local marker)
    runner2 = _Runner()
    summary2 = write.write_run(run_id, keep=True, emit_bin="/fake/emit", runner=runner2)
    assert runner2.calls == []
    assert summary2["skipped_already_emitted"] == ["sessW"]

    # --force re-emits (append-only; argMax-newer wins downstream)
    runner3 = _Runner()
    summary3 = write.write_run(run_id, keep=True, force=True,
                               emit_bin="/fake/emit", runner=runner3)
    assert len(runner3.calls) == 1
    assert summary3["emitted"] == ["sessW"]


def test_marker_checkpointed_after_each_emit(tmp_path, monkeypatch):
    """The emitted-marker is written after EACH successful emit, so a crash
    mid-fan-out never re-emits an already-shipped session on re-run."""
    # two sessions; the SECOND emit raises → the first must already be checkpointed.
    monkeypatch.setenv("INSIGHT_STATE_DIR", str(tmp_path / "state"))
    tr = tmp_path / "t.jsonl"
    tr.write_text("{}\n", encoding="utf-8")
    cands = [{"session": s, "project": "devrc", "cwd": "/c",
              "transcript_path": str(tr), "end_ts": "2026-07-10 12:00:00.000",
              "summary_ts": "2026-07-10 11:00:00.000", "ground_truth": {}}
             for s in ("s1", "s2")]
    prepare.prepare_run(cands, [], "runC")
    rdir = prepare.results_dir("runC")
    for s in ("s1", "s2"):
        (rdir / f"{s}.result.json").write_text(json.dumps(_valid(s)), encoding="utf-8")

    class _BoomOnSecond:
        def __init__(self):
            self.calls = []

        def __call__(self, argv, check=None):
            self.calls.append(argv)
            # argv[2] is b64:session=… ; fail the SECOND distinct emit
            if len(self.calls) == 2:
                raise RuntimeError("boom")

    runner = _BoomOnSecond()
    summary = write.write_run("runC", keep=True, emit_bin="/fake/emit", runner=runner)
    assert len(summary["emitted"]) == 1
    assert len(summary["failed"]) == 1
    # the marker already holds the first (successfully emitted) session
    assert set(write._load_emitted("runC")) == set(summary["emitted"])

    # re-run: the checkpointed session is skipped, only the failed one retries.
    runner2 = _Runner()
    summary2 = write.write_run("runC", keep=True, emit_bin="/fake/emit", runner=runner2)
    assert summary2["skipped_already_emitted"] == summary["emitted"]
    assert len(runner2.calls) == 1          # only the previously-failed session


def test_unreadable_row_is_emitted(tmp_path, monkeypatch):
    unread = _valid("sessW", unreadable=True, unreadable_reason="transcript truncated",
                    outcome="", session_type="", claude_helpfulness=0,
                    underlying_goal="", goal_categories=[], primary_success="",
                    brief_summary="")
    run_id = _setup_run(tmp_path, monkeypatch, result=unread)
    runner = _Runner()
    summary = write.write_run(run_id, emit_bin="/fake/emit", runner=runner)
    assert summary["emitted"] == ["sessW"]        # NOT dropped
    argv = runner.calls[0]
    payload = json.loads(argv[7][len("b64:payload="):])
    assert payload["unreadable"] is True


def test_clean_purges_on_fully_clean_run(tmp_path, monkeypatch):
    run_id = _setup_run(tmp_path, monkeypatch)
    write.write_run(run_id, clean=True, emit_bin="/fake/emit", runner=_Runner())
    assert not prepare.staging_dir(run_id).exists()
    assert not prepare.results_dir(run_id).exists()


# --------------------------------------------------------------------------- #
# FIX 2 — the FULL emit line stays under PIPE_BUF (4096B) for a maxed payload
# --------------------------------------------------------------------------- #
def _max_payload(session="sessMAX"):
    """Every free-text field at (well beyond) its documented limit — the shape
    the audit measured at ~4160B on the wire."""
    long = "x" * 800
    return {
        "schema_version": 1, "session": session,
        "underlying_goal": long,
        "goal_categories": ["infra", "deploy", "feature"],
        "outcome": "mostly_achieved", "session_type": "feature_build",
        "claude_helpfulness": 4,
        "friction_counts": {k: 3 for k in
                            ("wrong_approach", "repeated_correction", "tool_error",
                             "permission_block", "context_loss", "hallucination",
                             "missing_info", "env_breakage", "slow_feedback")},
        "friction_detail": [long, long, long, long, long],
        "primary_success": long,
        "brief_summary": long,
        "automation_opportunity": {"present": True, "description": long,
                                   "trigger": long, "leverage": "high",
                                   "evidence": long},
        "recurring_toil": {"present": True, "description": long,
                           "category": "debugging", "frequency_hint": long},
        "workflow_gap": {"present": True, "description": long,
                         "kind": "missing_tool"},
        "unreadable": False, "unreadable_reason": "",
    }


def test_max_payload_exceeds_pipe_buf_before_fit():
    """Guard: without bounding, the maxed payload's emit line is > PIPE_BUF —
    otherwise the FIX-2 test below would pass vacuously."""
    p = _max_payload()
    args = write.build_emit_args("sessMAX", "devrc", "/home/zach/workspace/devrc",
                                 p["brief_summary"], p, "2026-07-10 12:00:00.000")
    assert write._emit_line_bytes(args) > write.PIPE_BUF


def test_fit_payload_keeps_emit_line_under_pipe_buf():
    p = _max_payload()
    fitted = write.fit_payload("sessMAX", "devrc",
                               "/home/zach/workspace/devrc", p,
                               "2026-07-10 12:00:00.000")
    args = write.build_emit_args("sessMAX", "devrc", "/home/zach/workspace/devrc",
                                 fitted.get("brief_summary", ""), fitted,
                                 "2026-07-10 12:00:00.000")
    line = write._emit_line_bytes(args)
    assert line < write.PIPE_BUF, line
    assert line <= write.EMIT_LINE_BUDGET, line
    # truncation is VISIBLE and the payload is still schema-valid + valid JSON.
    import schema
    assert schema.validate(fitted) == []
    dumped = json.dumps(fitted, ensure_ascii=False)   # must round-trip
    assert json.loads(dumped)["session"] == "sessMAX"
    assert write._TRUNC_MARK in dumped   # something was visibly truncated


def test_fit_payload_noop_when_small():
    small = _valid("s")
    out = write.fit_payload("s", "p", "/c", small, "2026-01-01 00:00:00.000")
    assert out == small
    assert write._TRUNC_MARK not in json.dumps(out)


def test_write_bounds_the_emitted_line(tmp_path, monkeypatch):
    """End-to-end: a maxed result flowing through write_run emits a < PIPE_BUF
    line, and the model's confabulation-free counts (none in payload) are intact."""
    run_id = _setup_run(tmp_path, monkeypatch, result=_max_payload("sessW"))
    runner = _Runner()
    write.write_run(run_id, keep=True, emit_bin="/fake/emit", runner=runner)
    argv = runner.calls[0]
    assert write._emit_line_bytes(argv[1:]) < write.PIPE_BUF


# --------------------------------------------------------------------------- #
# FIX 3 — private perms (0700 dirs / 0600 files) + per-session purge-after-emit
# --------------------------------------------------------------------------- #
def _mode(path):
    import os
    import stat
    return stat.S_IMODE(os.stat(path).st_mode)


def test_state_dirs_and_files_are_private(tmp_path, monkeypatch):
    run_id = _setup_run(tmp_path, monkeypatch)
    root = prepare.state_root()
    sdir = prepare.staging_dir(run_id)
    rdir = prepare.results_dir(run_id)
    # every dir level 0700 (root, staging/, results/, <run-id>/)
    for d in (root, root / "staging", root / "results", sdir, rdir):
        assert _mode(d) == 0o700, (d, oct(_mode(d)))
    # input.json + manifest.json written 0600
    assert _mode(sdir / "sessW.input.json") == 0o600
    assert _mode(sdir / "manifest.json") == 0o600
    # the emitted-marker is 0600 too
    write.write_run(run_id, keep=True, emit_bin="/fake/emit", runner=_Runner())
    assert _mode(rdir / "emitted.json") == 0o600


def test_per_session_purge_after_emit(tmp_path, monkeypatch):
    run_id = _setup_run(tmp_path, monkeypatch)
    sdir = prepare.staging_dir(run_id)
    rdir = prepare.results_dir(run_id)
    assert (sdir / "sessW.input.json").exists()
    assert (rdir / "sessW.result.json").exists()

    summary = write.write_run(run_id, emit_bin="/fake/emit", runner=_Runner())
    assert summary["emitted"] == ["sessW"]
    assert summary["purged"] == ["sessW"]
    # the emitted session's scrubbed input + its result are gone …
    assert not (sdir / "sessW.input.json").exists()
    assert not (rdir / "sessW.result.json").exists()
    # … but the run dir survives (marker + manifest remain for auditing).
    assert (rdir / "emitted.json").exists()
    assert (sdir / "manifest.json").exists()


def test_keep_opts_out_of_purge(tmp_path, monkeypatch):
    run_id = _setup_run(tmp_path, monkeypatch)
    write.write_run(run_id, keep=True, emit_bin="/fake/emit", runner=_Runner())
    assert (prepare.staging_dir(run_id) / "sessW.input.json").exists()
    assert (prepare.results_dir(run_id) / "sessW.result.json").exists()


def test_unemitted_session_is_retained_not_purged(tmp_path, monkeypatch):
    # two sessions; only s1 has a result → s2 is missing and must NOT be purged.
    monkeypatch.setenv("INSIGHT_STATE_DIR", str(tmp_path / "state"))
    tr = tmp_path / "t.jsonl"
    tr.write_text("{}\n", encoding="utf-8")
    cands = [{"session": s, "project": "devrc", "cwd": "/c",
              "transcript_path": str(tr), "end_ts": "2026-07-10 12:00:00.000",
              "summary_ts": "2026-07-10 11:00:00.000", "ground_truth": {}}
             for s in ("s1", "s2")]
    prepare.prepare_run(cands, [], "runR")
    rdir = prepare.results_dir("runR")
    sdir = prepare.staging_dir("runR")
    (rdir / "s1.result.json").write_text(json.dumps(_valid("s1")), encoding="utf-8")

    summary = write.write_run("runR", emit_bin="/fake/emit", runner=_Runner())
    assert summary["emitted"] == ["s1"]
    assert summary["missing"] == ["s2"]
    assert "s2" in summary["retained"]
    # s1 purged, s2's scrubbed input retained (it needs a re-run).
    assert not (sdir / "s1.input.json").exists()
    assert (sdir / "s2.input.json").exists()
