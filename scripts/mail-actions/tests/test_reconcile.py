"""Thread-aware reconciliation tests (Feature 1 supersede + Feature 2 owner-close).

All offline: a fake in-memory MailDB that implements the new reconcile surface
(supersede_open_actions, close_actions_done, fetch_open_actions_min,
fetch_owner_messages) and a counting fake llm.extract. No Postgres, no LLM.

Synthetic References chains exercise thread_key grouping the same way real Gmail
threads do (root has no threading headers; replies carry References starting with the
root's message_id)."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import extract  # noqa: E402
import llm  # noqa: E402


class FakeMailDB:
    """In-memory MailDB with the full reconcile surface.

    `mail` rows are inbound/owner messages; `actions` are mail_actions rows that the
    test can preload (with thread_key/status/received_at/id) to model existing state."""

    def __init__(self, rows, actions=None):
        self._mail = {}
        for r in rows:
            r = dict(r)
            r.setdefault("processed_at", None)
            r.setdefault("labels", [])
            r.setdefault("raw", None)
            self._mail[r["id"]] = r
        # actions keyed by mail_id (the live table's UNIQUE col); each carries its own id.
        self.actions = {}
        for a in (actions or []):
            a = dict(a)
            a.setdefault("status", "open")
            a.setdefault("thread_key", None)
            a.setdefault("id", a["mail_id"])
            self.actions[a["mail_id"]] = a
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ensure_schema(self):
        pass

    def fetch_unprocessed(self, limit=None):
        out = [dict(r) for r in self._mail.values() if r["processed_at"] is None]
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
        a = dict(row)
        a.setdefault("thread_key", None)
        a.setdefault("status", "open")
        a.setdefault("id", row["mail_id"])
        self.actions[row["mail_id"]] = a
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

    def commit(self):
        self.commits += 1


class CountingLLM:
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


def _run(monkeypatch, fake_db, fake_llm, json=False):
    import _db
    monkeypatch.setattr(_db, "MailDB", lambda *a, **k: fake_db)
    monkeypatch.setattr(llm, "extract", fake_llm)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    args = types.SimpleNamespace(dry_run=False, limit=150, model=None,
                                 emit_clawgate=False, json=json)
    rc = extract.cmd_run(args)
    assert rc == 0


# --- Feature 1: cross-run thread supersede -------------------------------------

def test_feature1_newer_msg_supersedes_existing_older_open_action(monkeypatch):
    # An OPEN action already exists for thread "M0" from an OLDER message (ts=100).
    # A new reply (ts=300, References starts <M0>) arrives → its action is inserted and
    # the older open action is superseded by the timestamp-guarded UPDATE.
    rows = [
        {"id": 3, "message_id": "<M2>", "from_addr": "robin.hayes@brightco.example.com",
         "subject": "Re: thread", "received_at": 300, "category": "personal",
         "headers": {"References": "<M0> <M1>"}, "text_body": "reply2"},
    ]
    existing = [
        {"mail_id": 1, "id": 1, "thread_key": "M0", "received_at": 100,
         "status": "open"},
    ]
    db = FakeMailDB(rows, actions=existing)
    fake_llm = CountingLLM(action_required=True)
    _run(monkeypatch, db, fake_llm)

    assert db.actions[1]["status"] == "superseded"   # older open action retired
    assert db.actions[3]["status"] == "open"          # the reply's new action is live
    assert db.actions[3]["thread_key"] == "M0"        # stored on the new row


def test_feature1_does_not_supersede_a_newer_open_action(monkeypatch):
    # An OPEN action exists for thread "M0" from a NEWER message (ts=500). An OLDER
    # reply (ts=300) of the same thread is processed → its action is inserted but the
    # newer open action is NOT superseded (received_at < guard fails).
    rows = [
        {"id": 3, "message_id": "<M2>", "from_addr": "robin.hayes@brightco.example.com",
         "subject": "Re: thread", "received_at": 300, "category": "personal",
         "headers": {"References": "<M0> <M1>"}, "text_body": "older reply"},
    ]
    existing = [
        {"mail_id": 9, "id": 9, "thread_key": "M0", "received_at": 500,
         "status": "open"},
    ]
    db = FakeMailDB(rows, actions=existing)
    fake_llm = CountingLLM(action_required=True)
    _run(monkeypatch, db, fake_llm)

    assert db.actions[9]["status"] == "open"   # newer open action untouched
    assert db.actions[3]["status"] == "open"   # the older reply's action still inserts


def test_feature1_summary_counts_cross_run_supersede(monkeypatch, capsys):
    import json as _json
    rows = [
        {"id": 3, "message_id": "<M2>", "from_addr": "x@y.com", "subject": "Re: t",
         "received_at": 300, "category": "personal",
         "headers": {"References": "<M0>"}, "text_body": "reply"},
    ]
    existing = [
        {"mail_id": 1, "id": 1, "thread_key": "M0", "received_at": 100,
         "status": "open"},
    ]
    db = FakeMailDB(rows, actions=existing)
    _run(monkeypatch, db, CountingLLM(action_required=True), json=True)
    summary = _json.loads(capsys.readouterr().out)
    assert summary["superseded"] == 1
    assert summary["action_required"] == 1


# --- Feature 2: auto-close on owner reply --------------------------------------

OWNER = "zachlowden1@gmail.com"


def test_feature2_owner_reply_after_action_closes_it():
    # Open action on thread "M0" arrived at ts=100. Owner replied in the same thread at
    # ts=200 (References starts <M0>) → the action is closed (done).
    rows = [
        {"id": 50, "from_addr": OWNER, "message_id": "<own1>", "received_at": 200,
         "headers": {"References": "<M0>"}},
    ]
    existing = [
        {"mail_id": 1, "id": 1, "thread_key": "M0", "received_at": 100,
         "status": "open"},
    ]
    db = FakeMailDB(rows, actions=existing)
    closed = extract.reconcile_owner_replies(db, {OWNER})
    assert closed == 1
    assert db.actions[1]["status"] == "done"


def test_feature2_owner_reply_older_than_action_does_not_close():
    # Owner message PREDATES the action (ts 50 < 100) → must NOT close (timestamp guard:
    # a stale owner message can't close a fresh action from a later inbound reply).
    rows = [
        {"id": 51, "from_addr": OWNER, "message_id": "<own2>", "received_at": 50,
         "headers": {"References": "<M0>"}},
    ]
    existing = [
        {"mail_id": 1, "id": 1, "thread_key": "M0", "received_at": 100,
         "status": "open"},
    ]
    db = FakeMailDB(rows, actions=existing)
    closed = extract.reconcile_owner_replies(db, {OWNER})
    assert closed == 0
    assert db.actions[1]["status"] == "open"


def test_feature2_owner_reply_unrelated_thread_does_not_close():
    rows = [
        {"id": 52, "from_addr": OWNER, "message_id": "<own3>", "received_at": 200,
         "headers": {"References": "<OTHER>"}},
    ]
    existing = [
        {"mail_id": 1, "id": 1, "thread_key": "M0", "received_at": 100,
         "status": "open"},
    ]
    db = FakeMailDB(rows, actions=existing)
    closed = extract.reconcile_owner_replies(db, {OWNER})
    assert closed == 0
    assert db.actions[1]["status"] == "open"


def test_feature2_legacy_null_thread_key_action_skipped():
    # A legacy open action with thread_key=NULL can't be thread-matched → never closed,
    # even if an owner message shares no key with it.
    rows = [
        {"id": 53, "from_addr": OWNER, "message_id": "<own4>", "received_at": 200,
         "headers": {"References": "<M0>"}},
    ]
    existing = [
        {"mail_id": 1, "id": 1, "thread_key": None, "received_at": 100,
         "status": "open"},
    ]
    db = FakeMailDB(rows, actions=existing)
    closed = extract.reconcile_owner_replies(db, {OWNER})
    assert closed == 0
    assert db.actions[1]["status"] == "open"


def test_feature2_runs_at_start_of_cmd_run_and_counts(monkeypatch, capsys):
    import json as _json
    # Owner reply (ts=200) on thread M0 closes the pre-existing open action (ts=100).
    # No inbound survivors, so the LLM is never called.
    rows = [
        {"id": 50, "from_addr": OWNER, "message_id": "<own1>", "received_at": 200,
         "category": "personal", "headers": {"References": "<M0>"},
         "text_body": "my reply", "processed_at": "STAMPED"},  # already processed → not a survivor
    ]
    existing = [
        {"mail_id": 1, "id": 1, "thread_key": "M0", "received_at": 100,
         "status": "open"},
    ]
    db = FakeMailDB(rows, actions=existing)
    fake_llm = CountingLLM(action_required=True)
    _run(monkeypatch, db, fake_llm, json=True)
    summary = _json.loads(capsys.readouterr().out)
    assert summary["closed"] == 1
    assert db.actions[1]["status"] == "done"
    assert fake_llm.calls == []   # reconcile-only run, no extraction


# --- owner-from inbound survivor → labeled 'sent', no LLM ----------------------

def test_owner_from_survivor_labeled_sent_no_llm(monkeypatch):
    # A survivor whose from_addr is an owner address must be labeled 'sent' and never
    # reach the LLM (defensive guard for forwarded/BCC'd owner mail that is via_gmail).
    rows = [
        {"id": 60, "from_addr": OWNER, "message_id": "<mine@x>",
         "subject": "my own mail", "received_at": 100, "category": "personal",
         "headers": {}, "text_body": "I wrote this"},
    ]
    db = FakeMailDB(rows)
    fake_llm = CountingLLM(action_required=True)
    _run(monkeypatch, db, fake_llm)

    assert fake_llm.calls == []
    assert db._mail[60]["labels"] == ["sent"]
    assert db.actions == {}


def test_owner_addrs_env_override(monkeypatch):
    monkeypatch.setenv("MAIL_ACTIONS_OWNER_ADDRS", " Foo@Bar.com , baz@qux.io ")
    assert extract.owner_addrs() == {"foo@bar.com", "baz@qux.io"}


def test_owner_addrs_default(monkeypatch):
    monkeypatch.delenv("MAIL_ACTIONS_OWNER_ADDRS", raising=False)
    got = extract.owner_addrs()
    assert "zachlowden1@gmail.com" in got
    assert "zach@civitai.com" in got
    assert "zacxdev@gmail.com" in got
