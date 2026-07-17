"""Idempotency test: a processed row is labelled+stamped so a 2nd pass is a no-op.

Uses a fake in-memory MailDB (no port-forward, no Postgres, no network) injected into
extract.cmd_run, plus a deterministic fake LLM. The fake's fetch_unprocessed() honours
processed_at the way Postgres would, so the second run sees an empty delta.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract  # noqa: E402
import llm  # noqa: E402


class FakeMailDB:
    """Mimics _db.MailDB's surface; tracks labels/processed_at + mail_actions in memory."""

    def __init__(self, rows):
        # rows: list of dict mail rows; we add mutable state columns.
        self._mail = {}
        for r in rows:
            r = dict(r)
            r.setdefault("processed_at", None)
            r.setdefault("labels", [])
            self._mail[r["id"]] = r
        self.actions = {}  # mail_id -> action row
        self.commits = 0

    # context manager
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ensure_schema(self):
        pass

    def fetch_unprocessed(self, limit=None):
        out = [dict(r) for r in self._mail.values() if r["processed_at"] is None]
        out.sort(key=lambda r: r["id"], reverse=True)
        return out[:limit] if limit is not None else out

    def fetch_raw(self, mail_id):
        # The idempotency-test rows have no PDF/raw → never invoices.
        return self._mail[mail_id].get("raw")

    def mark_processed(self, mail_id, label):
        r = self._mail[mail_id]
        if label not in r["labels"]:
            r["labels"].append(label)
        r["processed_at"] = "STAMPED"

    def insert_action(self, row):
        if row["mail_id"] in self.actions:
            return False  # ON CONFLICT DO NOTHING
        r = dict(row)
        r.setdefault("thread_key", None)
        r.setdefault("status", "open")
        r.setdefault("id", row["mail_id"])
        self.actions[row["mail_id"]] = r
        return True

    def supersede_open_actions(self, thread_key, before_received_at):
        n = 0
        for a in self.actions.values():
            if (a.get("thread_key") == thread_key and a.get("status") == "open"
                    and a.get("received_at") is not None
                    and before_received_at is not None
                    and a["received_at"] < before_received_at):
                a["status"] = "superseded"
                n += 1
        return n

    def close_actions_done(self, action_ids):
        ids = set(action_ids)
        n = 0
        for a in self.actions.values():
            if a.get("id") in ids and a.get("status") == "open":
                a["status"] = "done"
                n += 1
        return n

    def fetch_open_actions_min(self):
        return [
            {"id": a["id"], "thread_key": a.get("thread_key"),
             "received_at": a.get("received_at")}
            for a in self.actions.values() if a.get("status") == "open"
        ]

    def fetch_owner_messages(self, owner_addrs):
        addrs = {a.lower() for a in owner_addrs}
        return [
            {"id": r["id"], "headers": r.get("headers"),
             "message_id": r.get("message_id"), "received_at": r.get("received_at")}
            for r in self._mail.values()
            if (r.get("from_addr") or "").strip().lower() in addrs
        ]

    def list_open_actions(self):
        return [a for a in self.actions.values() if a.get("status") == "open"]

    def commit(self):
        self.commits += 1


def _fake_llm_extract(**kwargs):
    """Action-required for the zen thread, fyi otherwise — deterministic, no network."""
    subj = (kwargs.get("subject") or "").lower()
    if "incomplete" in subj or "action required" in subj:
        return llm.Extraction(
            action_required=True, who="Acme Pay",
            ask="Complete the merchant-account application.",
            deadline=None, amount=None, confidence=0.9, reason="incomplete notice",
        )
    return llm.Extraction(
        action_required=False, who="", ask="", deadline=None, amount=None,
        confidence=0.3, reason="fyi",
    )


def _rows():
    return [
        # survivor → action-required
        {"id": 1, "message_id": "<a>", "from_addr": "sales@acmepay.example.com",
         "subject": "Application Incomplete for Acme", "received_at": None,
         "category": "personal", "headers": {}, "text_body": "please complete"},
        # survivor → fyi
        {"id": 2, "message_id": "<b>", "from_addr": "robin.hayes@brightco.example.com",
         "subject": "Re: Sales Audit Excel Template", "received_at": None,
         "category": "personal", "headers": {}, "text_body": "thanks"},
        # bulk drop (alert)
        {"id": 3, "message_id": "<c>", "from_addr": "alerts@goalert.example.com",
         "subject": "Alert FIRING", "received_at": None,
         "category": "alert", "headers": {}, "text_body": "fire"},
    ]


def _run(monkeypatch, fake_db):
    monkeypatch.setattr(extract, "MailDB", lambda *a, **k: fake_db, raising=False)
    # MailDB is imported inside cmd_run via `from _db import MailDB`; patch the source.
    import _db
    monkeypatch.setattr(_db, "MailDB", lambda *a, **k: fake_db)
    monkeypatch.setattr(llm, "extract", _fake_llm_extract)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    args = types.SimpleNamespace(dry_run=False, limit=150, model=None,
                                 emit_clawgate=False, json=True)
    extract.cmd_run(args)


def test_first_pass_classifies_then_second_pass_is_noop(monkeypatch, capsys):
    db = FakeMailDB(_rows())

    # First pass.
    _run(monkeypatch, db)
    capsys.readouterr()

    # State after first pass.
    assert db._mail[1]["labels"] == ["action-required"]
    assert db._mail[2]["labels"] == ["fyi"]
    assert db._mail[3]["labels"] == ["bulk"]
    assert all(r["processed_at"] == "STAMPED" for r in db._mail.values())
    assert set(db.actions) == {1}
    assert len(db.list_open_actions()) == 1

    # Second pass — delta must be empty.
    assert db.fetch_unprocessed() == []
    actions_before = dict(db.actions)
    _run(monkeypatch, db)

    # No new actions, no relabel, action set unchanged.
    assert db.actions == actions_before
    assert db._mail[1]["labels"] == ["action-required"]


def test_insert_action_conflict_is_noop():
    db = FakeMailDB(_rows())
    row = {"mail_id": 1, "message_id": "<a>", "from_addr": "x", "subject": "s",
           "received_at": None, "who": "w", "ask": "a", "deadline": None,
           "amount": None, "confidence": 0.9, "reason": "r"}
    assert db.insert_action(row) is True
    assert db.insert_action(row) is False  # ON CONFLICT DO NOTHING
