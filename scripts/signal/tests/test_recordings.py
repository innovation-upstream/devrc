"""The call-recording pipeline — schema state, the ASR/LLM transports, and the CLI.

Hermetic like every suite here: transports are injected, nothing touches the
network, the DB is the sqlite substrate. The pieces under test:

  * `SignalDB.list_recordings` / `get_recording` / `record_transcription` /
    `record_tasked` — the processing state on `signal.call_recordings`
    (the MUTE behaviour of the two read surfaces is `test_group_exclusions.py`'s
    job; this file owns the state machine).
  * `_stt` — the ASR transport and response contract.
  * `_extract` — the LLM extraction contract (JSON items, retry, refusals).
  * `clawgate.build_recording_task_payload` / `emit_task` — card shape + token gate.
  * The four CLI commands driven through `consumer.main()` (the `_cli` pattern
    from test_group_exclusions.py), including every refusal exit code.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

import _extract
import _stt
import clawgate
import consumer

PEER_UUID = "44444444-4444-4444-8444-444444444444"
PEER_PHONE = "+15550007"


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
def _seed_recording(db, *, audio: bool = True, signal_att_id: str = "att-rec-1",
                    filename: str = "team-sync.m4a",
                    timestamp: int = 5_000) -> int:
    """One message from a peer carrying one (audio) attachment.

    Returns `(message_row_id, attachment_row_id)` — the archive helper needs the
    message id, every CLI command takes the attachment row id.
    """
    msg_id = db.upsert_message({
        "message_timestamp": timestamp,
        "source_uuid": PEER_UUID,
        "source_number": PEER_PHONE, "source_name": "Dana",
        "message_type": "direct_message", "body": "recording of our sync",
        "group_id": None, "is_outbound": False,
    })
    db.upsert_attachment(msg_id, {
        "id": signal_att_id,
        "content_type": "audio/mp4" if audio else "image/jpeg",
        "filename": filename, "size": 2048,
    })
    row_id = db.conn.rows(
        "SELECT id FROM signal.attachments WHERE signal_attachment_id = ?",
        (signal_att_id,))[0]["id"]
    return msg_id, row_id


def _mark_archived(db, msg_id: int, signal_att_id: str,
                   key: str = "recordings/team-sync.m4a") -> None:
    db.record_attachment_object(msg_id, signal_att_id, "signal-attachments", key)


def _cli(monkeypatch, db, argv):
    """Run `consumer.main(argv)` against the hermetic db. Returns the exit code."""
    class _Ctx:
        def __enter__(self_inner):
            return db

        def __exit__(self_inner, *exc):
            return False

    monkeypatch.setattr(consumer, "SignalDB", lambda *a, **kw: _Ctx())
    monkeypatch.setattr(db, "ensure_schema", lambda *a, **kw: None, raising=False)
    return consumer.main(argv)


class _FakeMinio:
    def __init__(self, blobs):
        self.blobs = blobs
        self.reads = []

    def get_attachment(self, key, bucket=None):
        self.reads.append((bucket, key))
        if key not in self.blobs:
            raise KeyError(key)
        return self.blobs[key]


# --------------------------------------------------------------------------- #
# DB state machine
# --------------------------------------------------------------------------- #
def test_recordings_lists_only_audio_newest_first_with_null_status(db):
    audio_msg, audio_row = _seed_recording(db)
    _, older_row = _seed_recording(db, signal_att_id="att-rec-2",
                                   filename="older-call.m4a", timestamp=4_000)
    _seed_recording(db, audio=False, signal_att_id="att-photo-1",
                    filename="snapshot.jpg")
    _mark_archived(db, audio_msg, "att-rec-1")

    rows = db.list_recordings()
    ids = [r["row_id"] for r in rows]
    assert len(ids) == 2, "the jpeg must not list"
    assert ids[0] == audio_row and ids[1] == older_row, "newest first"
    assert rows[0]["filename"] == "team-sync.m4a"
    assert rows[0]["status"] is None, "unprocessed recordings read null"
    assert rows[0]["minio_key"] == "recordings/team-sync.m4a"
    assert rows[0]["display_name"] == "Dana"
    assert rows[0]["group_name"] is None


def test_transcription_roundtrip_and_then_tasking(db):
    _, row_id = _seed_recording(db)

    updated = db.record_transcription(
        row_id, text="we agreed Bob files the report",
        transcript_json={"text": "we agreed Bob files the report"},
        ts=9_000)
    assert updated["status"] == "transcribed"
    assert updated["transcript_text"] == "we agreed Bob files the report"
    assert updated["transcript_at"] == 9_000

    # 🔧 The --force redo overwrites in place — one row, not two.
    db.record_transcription(row_id, text="redo", transcript_json={"text": "redo"},
                            ts=9_100)
    assert db.conn.count("call_recordings") == 1
    assert db.get_recording(row_id)["transcript_text"] == "redo"

    tasked = db.record_tasked(row_id, items_json=[{"task": "file the report"}],
                              task_ref="muster:2 card(s)", ts=9_200)
    assert tasked["status"] == "tasked"
    assert tasked["task_ref"] == "muster:2 card(s)"
    assert tasked["items_json"] == [{"task": "file the report"}]


def test_transcription_of_an_unarchived_row_still_stores(db):
    """record_transcription never demands MinIO state — the transcript is the
    record; grabbing bytes is a separate step."""
    _, row_id = _seed_recording(db)
    updated = db.record_transcription(row_id, text="t", transcript_json={}, ts=1)
    assert updated["status"] == "transcribed"


# --------------------------------------------------------------------------- #
# _stt — the ASR transport contract
# --------------------------------------------------------------------------- #
def test_stt_extract_text_happy_and_error_paths():
    assert _stt.extract_text({"text": "hello"}) == "hello"
    with pytest.raises(_stt.SttError, match="detail"):
        _stt.extract_text({"detail": "model busy"})
    with pytest.raises(_stt.SttError, match="no transcript"):
        _stt.extract_text({"text": "   "})
    with pytest.raises(_stt.SttError, match="not an object"):
        _stt.extract_text(["nope"])


def test_stt_transcribe_builds_the_multipart_call():
    seen = {}

    def transport(method, url, *, files=None, form=None, headers=None,
                  timeout=None):
        seen.update(method=method, url=url, files=files, form=form,
                    headers=headers, timeout=timeout)
        return {"text": "ok"}

    resp = _stt.transcribe(b"AUDIOBYTES", "call.m4a", language="en",
                           url="http://asr.example:1/", token="tok-1",
                           transport=transport)
    assert resp == {"text": "ok"}
    assert seen["method"] == "POST"
    assert seen["url"] == "http://asr.example:1" + _stt.TRANSCRIPTIONS_PATH
    assert seen["files"]["file"][0] == "call.m4a"
    assert seen["files"]["file"][1] == b"AUDIOBYTES"
    assert seen["form"] == {"language": "en"}
    assert seen["headers"] == {"Authorization": "Bearer tok-1"}


def test_stt_transcribe_omits_language_and_token_when_absent():
    seen = {}

    def transport(method, url, *, files=None, form=None, headers=None,
                  timeout=None):
        seen.update(form=form, headers=headers)
        return {"text": "ok"}

    _stt.transcribe(b"x", "call.m4a", transport=transport)
    assert seen["form"] is None
    assert seen["headers"] == {}


# --------------------------------------------------------------------------- #
# _extract — the LLM contract
# --------------------------------------------------------------------------- #
def _items_json(**over):
    item = {"task": "file the report", "who": "Bob", "due": "friday",
            "confidence": 0.9}
    item.update(over)
    for k in list(item):
        if k not in ("task", "who", "due", "confidence"):
            item.pop(k)
    return json.dumps({"items": [item]})


def test_extract_parse_items_plain_and_fenced():
    plain = _items_json()
    assert _extract.parse_items(plain) == [
        {"task": "file the report", "who": "Bob", "due": "friday",
         "confidence": 0.9}]
    fenced = "```json\n" + plain + "\n```"
    assert _extract.parse_items(fenced) == _extract.parse_items(plain)


def test_extract_parse_items_rejects_bad_shapes():
    with pytest.raises(_extract.ExtractionError, match="no JSON"):
        _extract.parse_items("no braces at all")
    with pytest.raises(_extract.ExtractionError, match="items"):
        _extract.parse_items('{"other": []}')
    with pytest.raises(_extract.ExtractionError, match="missing keys"):
        _extract.parse_items('{"items": [{"task": "x"}]}')
    with pytest.raises(_extract.ExtractionError, match="empty 'task'"):
        _extract.parse_items('{"items": [{"task": "", "who": null, '
                             '"due": null, "confidence": 1}]}')


def test_extract_parse_items_normalises_and_clamps():
    items = _extract.parse_items(
        '{"items": [{"task": "  do it  ", "who": "", "due": null, '
        '"confidence": 7, "commentary": "ignored"}]}')
    assert items == [{"task": "do it", "who": None, "due": None,
                      "confidence": 1.0}]


def test_extract_items_retries_once_then_succeeds(monkeypatch):
    monkeypatch.delenv("SIGNAL_EXTRACT_MODEL", raising=False)
    calls = []

    def flaky(model, prompt, api_key):
        calls.append(model)
        return "garbage" if len(calls) == 1 else _items_json()

    items = _extract.extract_items(transcript="the meeting",
                                   api_key="key-test", _caller=flaky)
    assert len(calls) == 2, "one malformed-output retry"
    assert calls == [_extract.DEFAULT_MODEL] * 2
    assert items[0]["task"] == "file the report"


def test_extract_items_refuses_without_an_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        _extract.extract_items(transcript="t", _caller=lambda *a: "")


def test_extract_user_prompt_is_truncated():
    assert len(_extract.build_user_prompt(transcript="x" * 40_000)) \
        == _extract.TRANSCRIPT_TRUNCATE


# --------------------------------------------------------------------------- #
# clawgate — card shape + token gate
# --------------------------------------------------------------------------- #
def test_recording_task_payload_shape():
    payload = clawgate.build_recording_task_payload(
        recording_id=51, conversation="Dana", item={
            "task": "file the report", "who": "Bob", "due": "friday",
            "confidence": 0.9},
        transcript="full transcript " * 100)
    assert set(payload) == {"directory", "body"}
    assert len(payload["directory"]) <= clawgate.TITLE_MAX
    assert "file the report" in payload["directory"]
    assert "51" in payload["directory"]
    assert "Bob" in payload["body"] and "friday" in payload["body"]
    assert "call recording #51" in payload["body"]


def test_recording_task_payload_handles_null_who_and_due():
    payload = clawgate.build_recording_task_payload(
        recording_id=7, conversation="?", item={
            "task": "book the room", "who": None, "due": None,
            "confidence": 0.5},
        transcript="t")
    assert "book the room" in payload["directory"]
    assert "due" not in payload["directory"].split("#7")[1]
    assert "Who: ?" in payload["body"] and "Due: -" in payload["body"]


def test_emit_task_is_a_graceful_noop_without_a_token(monkeypatch):
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    # No `requests` fake installed: if emit_task reached for it, the import
    # would fail — the same resolves-nothing assertion the draft card makes.
    assert clawgate.emit_task({"directory": "x", "body": "y"}) is False


def test_emit_task_posts_to_the_task_service_when_a_token_is_set(monkeypatch):
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-rec-1")
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", "http://env-task.example:34567")
    monkeypatch.delenv("CLAWGATE_API_URL", raising=False)
    calls = []

    class Resp:
        def raise_for_status(self):
            calls.append("raised")

    module = types.ModuleType("requests")

    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return Resp()

    module.post = post
    monkeypatch.setitem(sys.modules, "requests", module)

    assert clawgate.emit_task({"directory": "📋 card", "body": "b"}) is True
    assert calls[0]["url"] == "http://env-task.example:34567/api/tasks"
    assert calls[0]["headers"]["Authorization"] == "Bearer tok-rec-1"


# --------------------------------------------------------------------------- #
# The CLI commands, driven through main()
# --------------------------------------------------------------------------- #
def test_cli_recordings_prints_json(db, monkeypatch, capsys):
    _, row_id = _seed_recording(db)
    assert _cli(monkeypatch, db, ["recordings"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["row_id"] for r in rows] == [row_id]
    assert rows[0]["status"] is None


def test_cli_grab_writes_the_archived_bytes(db, monkeypatch, capsys, tmp_path):
    msg_id, row_id = _seed_recording(db)
    _mark_archived(db, msg_id, "att-rec-1")
    fake = _FakeMinio({"recordings/team-sync.m4a": b"FAKEAUDIO"})
    monkeypatch.setattr(consumer, "_open_minio", lambda: fake)
    out = tmp_path / "call.m4a"

    rc = _cli(monkeypatch, db, ["grab", str(row_id), "-o", str(out)])
    assert rc == 0
    assert out.read_bytes() == b"FAKEAUDIO"
    assert fake.reads == [("signal-attachments", "recordings/team-sync.m4a")]
    report = json.loads(capsys.readouterr().out)
    assert report["bytes"] == len(b"FAKEAUDIO") and report["row_id"] == row_id


def test_cli_grab_refuses_an_unknown_row_with_exit_4(db, monkeypatch, capsys):
    assert _cli(monkeypatch, db, ["grab", "999999"]) == 4
    assert "refused" in capsys.readouterr().err


def test_cli_grab_refuses_an_unarchived_row_with_exit_4(db, monkeypatch, capsys):
    _, row_id = _seed_recording(db)          # never _mark_archived
    assert _cli(monkeypatch, db, ["grab", str(row_id)]) == 4
    assert "never archived" in capsys.readouterr().err


def _fake_stt_module(calls):
    mod = types.ModuleType("_stt")

    def transcribe(data, filename, *, language=None, url=None, token=None,
                   transport=None, timeout=None):
        calls.append({"data": data, "filename": filename, "language": language,
                      "url": url, "token": token})
        return {"text": "Bob will file the report by friday"}

    mod.transcribe = transcribe
    mod.extract_text = _stt.extract_text
    return mod


def test_cli_transcribe_happy_path_stores_the_transcript(
        db, monkeypatch, capsys, tmp_path):
    msg_id, row_id = _seed_recording(db)
    _mark_archived(db, msg_id, "att-rec-1")
    monkeypatch.setattr(consumer, "_open_minio",
                        lambda: _FakeMinio({"recordings/team-sync.m4a":
                                            b"FAKEAUDIO"}))
    calls = []
    monkeypatch.setitem(sys.modules, "_stt", _fake_stt_module(calls))
    monkeypatch.setenv("STT_API_URL", "http://asr.example:2")
    monkeypatch.setenv("STT_API_TOKEN", "tok-asr-1")

    rc = _cli(monkeypatch, db, ["transcribe", str(row_id), "--language", "en"])
    assert rc == 0
    assert calls[0]["url"] == "http://asr.example:2"
    assert calls[0]["token"] == "tok-asr-1"
    assert calls[0]["language"] == "en"
    assert calls[0]["data"] == b"FAKEAUDIO"
    out = capsys.readouterr()
    assert "Bob will file the report by friday" in out.out

    row = db.get_recording(row_id)
    assert row["status"] == "transcribed"
    assert row["transcript_text"] == "Bob will file the report by friday"


def test_cli_transcribe_twice_needs_force(db, monkeypatch, capsys):
    """The transport runs ONCE — a second run without --force replays the
    stored transcript instead of re-billing the ASR endpoint."""
    msg_id, row_id = _seed_recording(db)
    _mark_archived(db, msg_id, "att-rec-1")
    monkeypatch.setattr(consumer, "_open_minio",
                        lambda: _FakeMinio({"recordings/team-sync.m4a":
                                            b"FAKEAUDIO"}))
    calls = []
    monkeypatch.setitem(sys.modules, "_stt", _fake_stt_module(calls))
    db.record_transcription(row_id, text="already done",
                            transcript_json={"text": "already done"}, ts=1)

    assert _cli(monkeypatch, db, ["transcribe", str(row_id)]) == 0
    assert calls == [], "no ASR call without --force"
    assert "already transcribed" in capsys.readouterr().out

    assert _cli(monkeypatch, db, ["transcribe", str(row_id), "--force"]) == 0
    assert len(calls) == 1, "--force re-runs the transport"


def test_cli_transcribe_refuses_an_unarchived_row_with_exit_4(
        db, monkeypatch, capsys):
    _, row_id = _seed_recording(db)          # never _mark_archived
    assert _cli(monkeypatch, db, ["transcribe", str(row_id)]) == 4
    assert "never archived" in capsys.readouterr().err


def test_cli_transcribe_refuses_non_audio_with_exit_3(db, monkeypatch, capsys):
    _, row_id = _seed_recording(db, audio=False, signal_att_id="att-photo-2",
                             filename="snapshot.jpg")
    assert _cli(monkeypatch, db, ["transcribe", str(row_id)]) == 3
    assert "not audio" in capsys.readouterr().err


def test_cli_transcribe_refuses_an_unknown_row_with_exit_4(db, monkeypatch,
                                                           capsys):
    assert _cli(monkeypatch, db, ["transcribe", "424242"]) == 4


def _fake_extract_module(items):
    mod = types.ModuleType("_extract")
    mod.DEFAULT_MODEL = _extract.DEFAULT_MODEL
    mod.ExtractionError = _extract.ExtractionError
    mod.extract_items = lambda *, transcript, model=None, api_key=None, \
        _caller=None: items
    return mod


def test_cli_tasks_without_a_transcript_refuses_with_exit_3(db, monkeypatch,
                                                            capsys):
    _, row_id = _seed_recording(db)
    monkeypatch.setitem(sys.modules, "_extract", _fake_extract_module([]))
    assert _cli(monkeypatch, db, ["tasks", str(row_id)]) == 3
    assert "transcribe" in capsys.readouterr().err


def test_cli_tasks_without_a_token_extracts_but_posts_nothing(db, monkeypatch,
                                                              capsys):
    """🔴 The token gate degrades NOTIFICATION, never the record — and the row
    must NOT read `tasked`, or the status would claim cards that do not exist."""
    monkeypatch.delenv("CLAWGATE_HOOK_TOKEN", raising=False)
    _, row_id = _seed_recording(db)
    db.record_transcription(row_id, text="the transcript",
                            transcript_json={"text": "the transcript"}, ts=1)
    items = [{"task": "file the report", "who": "Bob", "due": "friday",
              "confidence": 0.9}]
    monkeypatch.setitem(sys.modules, "_extract", _fake_extract_module(items))

    rc = _cli(monkeypatch, db, ["tasks", str(row_id)])
    assert rc == 0
    out = capsys.readouterr()
    assert "CLAWGATE_HOOK_TOKEN unset" in out.err
    report = json.loads(out.out)
    assert report["cards_posted"] == 0 and report["items"] == items
    assert db.get_recording(row_id)["status"] == "transcribed", (
        "an unposted extraction must not stamp the row as tasked")


def test_cli_tasks_posts_cards_and_stamps_the_row(db, monkeypatch, capsys):
    monkeypatch.setenv("CLAWGATE_HOOK_TOKEN", "tok-rec-2")
    monkeypatch.setenv("CLAWGATE_TASK_API_URL", "http://env-task.example:34567")
    _, row_id = _seed_recording(db)
    db.record_transcription(row_id, text="the transcript",
                            transcript_json={"text": "the transcript"}, ts=1)
    items = [{"task": "file the report", "who": "Bob", "due": "friday",
              "confidence": 0.9},
             {"task": "book the room", "who": None, "due": None,
              "confidence": 0.4}]
    monkeypatch.setitem(sys.modules, "_extract", _fake_extract_module(items))
    posted = []

    class Resp:
        def raise_for_status(self):
            pass

    module = types.ModuleType("requests")
    cards = []

    def post(url, headers=None, json=None, timeout=None):
        cards.append(json)
        return Resp()

    module.post = post
    monkeypatch.setitem(sys.modules, "requests", module)

    rc = _cli(monkeypatch, db, ["tasks", str(row_id)])
    assert rc == 0
    assert len(cards) == 2, "one card per extracted item"
    assert all(c["directory"] and c["body"] for c in cards)
    report = json.loads(capsys.readouterr().out)
    assert report["cards_posted"] == 2
    row = db.get_recording(row_id)
    assert row["status"] == "tasked"
    assert row["items_json"] == items


def test_cli_tasks_refuses_when_extraction_fails(db, monkeypatch, capsys):
    """A missing OPENROUTER_API_KEY is a refusal (exit 3), not a traceback."""
    _, row_id = _seed_recording(db)
    db.record_transcription(row_id, text="the transcript",
                            transcript_json={}, ts=1)
    mod = types.ModuleType("_extract")
    mod.ExtractionError = _extract.ExtractionError
    mod.DEFAULT_MODEL = _extract.DEFAULT_MODEL

    def boom(*, transcript, model=None, api_key=None, _caller=None):
        raise RuntimeError("OPENROUTER_API_KEY not set")

    mod.extract_items = boom
    monkeypatch.setitem(sys.modules, "_extract", mod)
    assert _cli(monkeypatch, db, ["tasks", str(row_id)]) == 3
    assert "refused" in capsys.readouterr().err