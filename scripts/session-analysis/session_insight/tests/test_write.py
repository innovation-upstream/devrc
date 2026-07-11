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
    run_id = _setup_run(tmp_path, monkeypatch)
    runner = _Runner()
    write.write_run(run_id, emit_bin="/fake/emit", runner=runner)
    assert len(runner.calls) == 1

    # second write with no --force → skipped (no delete path, just a local marker)
    runner2 = _Runner()
    summary2 = write.write_run(run_id, emit_bin="/fake/emit", runner=runner2)
    assert runner2.calls == []
    assert summary2["skipped_already_emitted"] == ["sessW"]

    # --force re-emits (append-only; argMax-newer wins downstream)
    runner3 = _Runner()
    summary3 = write.write_run(run_id, force=True, emit_bin="/fake/emit", runner=runner3)
    assert len(runner3.calls) == 1
    assert summary3["emitted"] == ["sessW"]


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
