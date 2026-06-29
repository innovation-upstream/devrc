"""Run-loop routing tests for cmd_run's thread-dedup + invoice short-circuit.

All offline: a fake in-memory MailDB (incl. fetch_raw) and a counting fake
llm.extract — no Postgres, no MinIO, no OpenRouter. Verifies:
  - a 3-message thread → exactly ONE llm.extract call (the most-recent) + two
    `superseded` labels;
  - an invoice survivor → ZERO llm.extract calls + an `invoice` label;
  - a normal lone survivor → one llm.extract call + an action (or fyi) label;
  - the run summary reports the new invoice / superseded counters.
"""
import sys
import types
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import archive  # noqa: E402
import extract  # noqa: E402
import llm  # noqa: E402

FAKE_PDF = b"%PDF-1.4\nfake invoice body\n%%EOF\n"


def _raw_with_pdf(from_addr, subject, filename):
    m = MIMEMultipart("mixed")
    m["From"] = from_addr
    m["Subject"] = subject
    m["Message-ID"] = "<inv@x>"
    m.attach(MIMEText("See attached invoice.", "plain"))
    part = MIMEApplication(FAKE_PDF, _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=filename)
    m.attach(part)
    return m.as_bytes()


class FakeMailDB:
    """Mirrors _db.MailDB's run surface, in memory; rows may carry a `raw` value."""

    def __init__(self, rows):
        self._mail = {}
        for r in rows:
            r = dict(r)
            r.setdefault("processed_at", None)
            r.setdefault("labels", [])
            r.setdefault("raw", None)
            self._mail[r["id"]] = r
        self.actions = {}
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ensure_schema(self):
        pass

    def fetch_unprocessed(self, limit=None):
        out = [dict(r) for r in self._mail.values() if r["processed_at"] is None]
        # Most-recent-first; rows here use received_at as the sort key (DESC).
        out.sort(key=lambda r: (r.get("received_at") or 0), reverse=True)
        return out[:limit] if limit is not None else out

    def fetch_raw(self, mail_id):
        return self._mail[mail_id].get("raw")

    def mark_processed(self, mail_id, label):
        r = self._mail[mail_id]
        if label not in r["labels"]:
            r["labels"].append(label)
        r["processed_at"] = "STAMPED"

    def insert_action(self, row):
        if row["mail_id"] in self.actions:
            return False
        self.actions[row["mail_id"]] = row
        return True

    def commit(self):
        self.commits += 1


class CountingLLM:
    """Records every llm.extract call; returns action-required by default."""

    def __init__(self, action_required=True):
        self.calls = []
        self._ar = action_required

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self._ar:
            return llm.Extraction(
                action_required=True, who="W", ask="do thing", deadline=None,
                amount=None, confidence=0.9, reason="r",
            )
        return llm.Extraction(
            action_required=False, who="", ask="", deadline=None, amount=None,
            confidence=0.2, reason="fyi",
        )


def _run(monkeypatch, fake_db, fake_llm):
    import _db
    monkeypatch.setattr(_db, "MailDB", lambda *a, **k: fake_db)
    monkeypatch.setattr(llm, "extract", fake_llm)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    args = types.SimpleNamespace(dry_run=False, limit=150, model=None,
                                 emit_clawgate=False, json=False)
    rc = extract.cmd_run(args)
    assert rc == 0


def test_thread_dedup_one_llm_call_two_superseded(monkeypatch, capsys):
    # 3 messages of one thread, plus a lone unrelated survivor. received_at orders
    # them so the thread's NEWEST (reply2, id=3) is processed first.
    rows = [
        {"id": 1, "message_id": "<M0>", "from_addr": "lauren@naidacom.com",
         "subject": "Sales Audit Excel Template", "received_at": 100,
         "category": "personal", "headers": {}, "text_body": "root"},
        {"id": 2, "message_id": "<M1>", "from_addr": "lauren@naidacom.com",
         "subject": "Re: Sales Audit Excel Template", "received_at": 200,
         "category": "personal", "headers": {"References": "<M0>"},
         "text_body": "reply1"},
        {"id": 3, "message_id": "<M2>", "from_addr": "lauren@naidacom.com",
         "subject": "Re: Sales Audit Excel Template", "received_at": 300,
         "category": "personal", "headers": {"References": "<M0> <M1>"},
         "text_body": "reply2"},
    ]
    db = FakeMailDB(rows)
    fake_llm = CountingLLM(action_required=True)
    _run(monkeypatch, db, fake_llm)

    # Exactly one LLM call, for the most-recent message (reply2, id=3).
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["body"] == "reply2"
    # The newest got an action; the older two are superseded.
    assert db._mail[3]["labels"] == ["action-required"]
    assert db._mail[2]["labels"] == ["superseded"]
    assert db._mail[1]["labels"] == ["superseded"]
    assert set(db.actions) == {3}


def test_invoice_survivor_skips_llm_and_labels_invoice(monkeypatch, capsys):
    rows = [
        {"id": 10, "message_id": "<inv@x>", "from_addr": "billing@hetzner.com",
         "subject": "Your invoice", "received_at": 100, "category": "personal",
         "headers": {}, "text_body": "invoice body",
         "raw": _raw_with_pdf("billing@hetzner.com", "Your invoice",
                              "invoice_123.pdf")},
    ]
    db = FakeMailDB(rows)
    fake_llm = CountingLLM(action_required=True)
    # Sanity: the same definition the archiver uses flags this as a candidate.
    assert archive.is_archive_candidate(
        from_addr="billing@hetzner.com", subject="Your invoice",
        attachments=archive.extract_pdf_attachments(rows[0]["raw"]),
    )
    _run(monkeypatch, db, fake_llm)

    assert fake_llm.calls == []                  # LLM never called
    assert db._mail[10]["labels"] == ["invoice"]
    assert db.actions == {}                       # no action row


def test_invoice_via_monkeypatched_candidate(monkeypatch):
    # Alternate path: force is_archive_candidate True regardless of attachments.
    rows = [
        {"id": 11, "message_id": "<x@x>", "from_addr": "x@y.com",
         "subject": "anything", "received_at": 100, "category": "personal",
         "headers": {}, "text_body": "body", "raw": b"%PDF-1.4 stub"},
    ]
    db = FakeMailDB(rows)
    fake_llm = CountingLLM(action_required=True)
    monkeypatch.setattr(archive, "is_archive_candidate", lambda **kw: True)
    _run(monkeypatch, db, fake_llm)

    assert fake_llm.calls == []
    assert db._mail[11]["labels"] == ["invoice"]


def test_normal_lone_survivor_one_llm_call_action(monkeypatch):
    rows = [
        {"id": 20, "message_id": "<lone@x>", "from_addr": "sales@zenpayments.com",
         "subject": "Application Incomplete", "received_at": 100,
         "category": "personal", "headers": {}, "text_body": "complete it"},
    ]
    db = FakeMailDB(rows)
    fake_llm = CountingLLM(action_required=True)
    _run(monkeypatch, db, fake_llm)

    assert len(fake_llm.calls) == 1
    assert db._mail[20]["labels"] == ["action-required"]
    assert set(db.actions) == {20}


def test_normal_lone_survivor_fyi(monkeypatch):
    rows = [
        {"id": 21, "message_id": "<lone2@x>", "from_addr": "a@b.com",
         "subject": "fyi note", "received_at": 100, "category": "personal",
         "headers": {}, "text_body": "nothing to do"},
    ]
    db = FakeMailDB(rows)
    fake_llm = CountingLLM(action_required=False)
    _run(monkeypatch, db, fake_llm)

    assert len(fake_llm.calls) == 1
    assert db._mail[21]["labels"] == ["fyi"]
    assert db.actions == {}


def test_summary_reports_invoice_and_superseded_counters(monkeypatch, capsys):
    rows = [
        # thread of two (one superseded) ...
        {"id": 1, "message_id": "<T0>", "from_addr": "a@b.com", "subject": "t",
         "received_at": 100, "category": "personal", "headers": {},
         "text_body": "root"},
        {"id": 2, "message_id": "<T1>", "from_addr": "a@b.com", "subject": "Re: t",
         "received_at": 200, "category": "personal",
         "headers": {"References": "<T0>"}, "text_body": "reply"},
        # ... plus an invoice
        {"id": 3, "message_id": "<inv@x>", "from_addr": "billing@hetzner.com",
         "subject": "Invoice", "received_at": 300, "category": "personal",
         "headers": {}, "text_body": "inv",
         "raw": _raw_with_pdf("billing@hetzner.com", "Invoice", "invoice.pdf")},
    ]
    db = FakeMailDB(rows)
    fake_llm = CountingLLM(action_required=True)

    import json as _json
    import _db
    monkeypatch.setattr(_db, "MailDB", lambda *a, **k: db)
    monkeypatch.setattr(llm, "extract", fake_llm)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    args = types.SimpleNamespace(dry_run=False, limit=150, model=None,
                                 emit_clawgate=False, json=True)
    extract.cmd_run(args)
    summary = _json.loads(capsys.readouterr().out)

    assert summary["invoice"] == 1
    assert summary["superseded"] == 1
    assert summary["action_required"] == 1   # the thread's newest (T1)
    assert summary["survivors"] == 3
