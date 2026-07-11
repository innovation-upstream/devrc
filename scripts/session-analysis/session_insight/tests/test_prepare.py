"""prepare — input.json contract: ground-truth embedded verbatim, secrets
scrubbed with counts, chunk boundaries never split a message, schema + anti-
confab block present, files written under staging/<run-id>."""
import json

import prepare


GROUND_TRUTH = {
    "tool_counts": {"Bash": 10, "Edit": 3}, "git_commits": 2, "output_tokens": 12345,
    "end_ts": "2026-07-10 12:00:00.000", "cwd": "/home/zach/workspace/devrc",
    "unreadable": False,
}


def _make_transcript(tmp_path, lines):
    p = tmp_path / "sess.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def _candidate(path):
    return {
        "session": "sessABC", "project": "devrc",
        "cwd": "/home/zach/workspace/devrc", "transcript_path": path,
        "end_ts": "2026-07-10 12:00:00.000", "summary_ts": "2026-07-10 11:00:00.000",
        "ground_truth": GROUND_TRUTH,
    }


def test_chunk_text_never_splits_a_line():
    lines = [f'{{"i":{i},"text":"{"x" * 50}"}}' for i in range(20)]
    text = "\n".join(lines)
    chunks = prepare.chunk_text(text, budget=200, overlap=1)
    assert len(chunks) > 1
    orig = set(lines)
    for c in chunks:
        for ln in c.split("\n"):
            assert ln in orig, "a chunk split a message"


def test_chunk_text_oversized_line_stays_whole():
    big = '{"huge":"' + "z" * 5000 + '"}'
    chunks = prepare.chunk_text(big, budget=200)
    assert len(chunks) == 1 and chunks[0] == big


def test_chunk_overlap_carries_last_message():
    lines = [f"line-{i}" for i in range(6)]
    chunks = prepare.chunk_text("\n".join(lines), budget=20, overlap=1)
    # each chunk after the first starts with the previous chunk's last line
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.split("\n")[0] == prev.split("\n")[-1]


def test_build_input_embeds_ground_truth_and_scrubs(tmp_path, monkeypatch):
    monkeypatch.setenv("INSIGHT_STATE_DIR", str(tmp_path / "state"))
    secret = "sk-ant-" + "a" * 30
    lines = [
        json.dumps({"type": "user", "message": {"role": "user",
                    "content": f"deploy with key {secret}"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant",
                    "content": "ok"}}),
    ]
    path = _make_transcript(tmp_path, lines)
    payload = prepare.build_input(_candidate(path), "run-1", chunk_chars=24000)

    assert payload["ground_truth"] == GROUND_TRUTH          # verbatim
    assert payload["session"] == "sessABC"
    assert payload["chunk_count"] >= 1
    assert payload["redaction_counts"].get("anthropic-key") == 1
    # secret never survives into any chunk
    for ch in payload["chunks"]:
        assert secret not in ch["text"]
        assert "<REDACTED:anthropic-key>" in ch["text"] or ch["idx"] > 0
    # schema + anti-confab block present + self-contained
    assert "output-token maximum" in payload["anti_confabulation_contract"]
    assert payload["schema"]["schema_version"] == prepare.SCHEMA_VERSION
    assert "outcome" in payload["schema"]["closed_enums"]


def test_prepare_run_writes_files_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("INSIGHT_STATE_DIR", str(tmp_path / "state"))
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}})]
    path = _make_transcript(tmp_path, lines)
    manifest = prepare.prepare_run([_candidate(path)], [("skipped1", "not-settled")],
                                   "run-XY")
    sdir = prepare.staging_dir("run-XY")
    assert (sdir / "sessABC.input.json").exists()
    assert (sdir / "manifest.json").exists()
    assert manifest["sessions"][0]["session"] == "sessABC"
    assert manifest["sessions"][0]["end_ts"] == "2026-07-10 12:00:00.000"
    assert manifest["skips"] == [["skipped1", "not-settled"]]
    # results dir path recorded + result_path points inside it
    assert manifest["sessions"][0]["result_path"].endswith("sessABC.result.json")


def test_read_and_scrub_marks_unreadable(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    _text, _counts, unreadable = prepare.read_and_scrub(str(empty))
    assert unreadable is True
    _t, _c, missing = prepare.read_and_scrub(str(tmp_path / "nope.jsonl"))
    assert missing is True
