"""Invoice-archiver tests — all offline, no DB, no MinIO, no network.

Covers (per the build spec):
  - PDF attachment extraction from a synthetic multipart/mixed RFC822 message;
    and a message with no attachment → none.
  - Candidate detection: billing+PDF → yes; non-billing+invoice-named PDF → yes
    (filename signal); non-billing + non-invoice PDF → no; no PDF → no.
  - Bucket derivation, vendor-domain extraction, object-key sanitization
    (spaces, slashes, missing-filename → message_id fallback).
  - Sidecar JSON shape/keys.
  - Idempotency SQL predicate: a row already carrying `invoice-archived` is excluded
    by fetch_unarchived's WHERE clause (predicate logic tested against a fake set).
  - The MinIO client MOCKED in the archive subcommand orchestration — no live upload.
"""
import json
import sys
import types
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import archive as a  # noqa: E402
import extract  # noqa: E402

FAKE_PDF = b"%PDF-1.4\nfake invoice body\n%%EOF\n"


# --------------------------------------------------------------------------- #
# Message builders
# --------------------------------------------------------------------------- #
def _msg_with_pdf(from_addr, subject, filename, ctype="application/pdf"):
    m = MIMEMultipart("mixed")
    m["From"] = from_addr
    m["Subject"] = subject
    m["Date"] = "Sun, 28 Jun 2026 08:20:00 +0000"
    m["Message-ID"] = "<abc123@example.com>"
    m.attach(MIMEText("See attached invoice.", "plain"))
    subtype = ctype.split("/", 1)[1] if "/" in ctype else "octet-stream"
    part = MIMEApplication(FAKE_PDF, _subtype=subtype)
    if filename is not None:
        part.add_header("Content-Disposition", "attachment", filename=filename)
    m.attach(part)
    return m.as_bytes()


def _msg_no_attachment(from_addr, subject):
    m = MIMEMultipart("mixed")
    m["From"] = from_addr
    m["Subject"] = subject
    m.attach(MIMEText("Just text, no files.", "plain"))
    return m.as_bytes()


# --------------------------------------------------------------------------- #
# Attachment extraction
# --------------------------------------------------------------------------- #
def test_extract_pdf_attachment_returns_filename_and_bytes():
    raw = _msg_with_pdf("billing@hetzner.com", "Your invoice",
                        "Hetzner_2026-06-28.pdf")
    atts = a.extract_pdf_attachments(raw)
    assert len(atts) == 1
    assert atts[0].filename == "Hetzner_2026-06-28.pdf"
    assert atts[0].data == FAKE_PDF


def test_extract_pdf_by_content_type_without_pdf_suffix():
    # filename lacks .pdf but content-type is application/pdf → still extracted.
    raw = _msg_with_pdf("billing@hetzner.com", "inv", "document")
    atts = a.extract_pdf_attachments(raw)
    assert len(atts) == 1
    assert atts[0].data == FAKE_PDF


def test_extract_no_attachment_returns_empty():
    raw = _msg_no_attachment("a@b.com", "hello")
    assert a.extract_pdf_attachments(raw) == []


def test_extract_ignores_non_pdf_attachment():
    raw = _msg_with_pdf("a@b.com", "s", "photo.png", ctype="image/png")
    assert a.extract_pdf_attachments(raw) == []


# --------------------------------------------------------------------------- #
# Candidate detection
# --------------------------------------------------------------------------- #
def _atts(filename):
    return [a.PdfAttachment(filename=filename, data=FAKE_PDF)]


def test_candidate_billing_sender_plus_pdf():
    assert a.is_archive_candidate(
        from_addr="billing@hetzner.com", subject="Your monthly bill",
        attachments=_atts("statement.pdf"),
    )


def test_candidate_non_billing_but_invoice_named_pdf():
    # filename signal rescues a non-billing sender.
    assert a.is_archive_candidate(
        from_addr="someone@randomco.com", subject="hi",
        attachments=_atts("invoice_x.pdf"),
    )


def test_not_candidate_non_billing_non_invoice_pdf():
    assert not a.is_archive_candidate(
        from_addr="someone@randomco.com", subject="trip photos",
        attachments=_atts("vacation.pdf"),
    )


def test_not_candidate_no_pdf():
    assert not a.is_archive_candidate(
        from_addr="billing@hetzner.com", subject="invoice", attachments=[],
    )


def test_candidate_billing_subject_regex_signal():
    # non-allowlisted sender, no invoice-named file, but transactional subject.
    assert a.is_archive_candidate(
        from_addr="noreply@notify.cloudflare.com", subject="Your invoice is ready",
        attachments=_atts("statement-june.pdf"),
    )


# --------------------------------------------------------------------------- #
# Bucket / vendor / key derivation
# --------------------------------------------------------------------------- #
def test_bucket_for_year():
    assert a.bucket_for(datetime(2026, 6, 28)) == "taxes-2026-invoices"
    assert a.bucket_for(datetime(2024, 1, 1)) == "taxes-2024-invoices"


def test_vendor_domain_two_label():
    assert a.vendor_domain("billing@hetzner.com") == "hetzner.com"


def test_vendor_domain_subdomain_collapsed():
    assert a.vendor_domain("noreply@notify.cloudflare.com") == "cloudflare.com"


def test_vendor_domain_missing():
    assert a.vendor_domain(None) == "unknown-vendor"
    assert a.vendor_domain("garbage") == "unknown-vendor"


def test_object_key_basic():
    key = a.object_key(
        vendor="hetzner.com", dt=datetime(2026, 6, 28),
        filename="Hetzner_2026.pdf", message_id="<x@y>",
    )
    assert key == "hetzner.com/2026-06-28-Hetzner_2026.pdf"


def test_object_key_sanitizes_spaces_and_slashes():
    key = a.object_key(
        vendor="acme.com", dt=datetime(2026, 6, 28),
        filename="../../etc/  weird   invoice .pdf", message_id="<x@y>",
    )
    # path components stripped, whitespace collapsed.
    assert key == "acme.com/2026-06-28-weird invoice .pdf"
    assert "/" not in key.split("/", 1)[1]  # no slash inside the object portion


def test_object_key_missing_filename_uses_message_id():
    key = a.object_key(
        vendor="acme.com", dt=datetime(2026, 6, 28),
        filename=None, message_id="<msg-42@mail.acme.com>",
    )
    assert key == "acme.com/2026-06-28-msg-42@mail.acme.com.pdf"


def test_object_key_missing_both_filename_and_msgid():
    key = a.object_key(vendor="acme.com", dt=datetime(2026, 6, 28),
                       filename="", message_id=None)
    assert key == "acme.com/2026-06-28-message.pdf"


# --------------------------------------------------------------------------- #
# invoice_date
# --------------------------------------------------------------------------- #
def test_invoice_date_from_header():
    dt = a.invoice_date("Sun, 28 Jun 2026 08:20:00 +0000", None)
    assert dt.year == 2026 and dt.month == 6 and dt.day == 28


def test_invoice_date_accepts_datetime_passthrough():
    # Postgres timestamptz comes back as a datetime, not a string.
    dt_in = datetime(2026, 6, 28, 8, 20, tzinfo=timezone.utc)
    assert a.invoice_date(dt_in, None) is dt_in


def test_invoice_date_falls_back_to_received_at():
    rcv = datetime(2025, 3, 1, tzinfo=timezone.utc)
    assert a.invoice_date(None, rcv) == rcv
    assert a.invoice_date("not a date", rcv) == rcv


# --------------------------------------------------------------------------- #
# Sidecar shape
# --------------------------------------------------------------------------- #
def test_sidecar_metadata_shape():
    sc = a.sidecar_metadata(
        vendor="hetzner.com", from_addr="billing@hetzner.com",
        dt=datetime(2026, 6, 28), amount="$5.00", subject="Invoice",
        message_id="<x@y>", mail_id=22321,
    )
    assert set(sc) == {
        "vendor", "from_addr", "date", "amount", "subject", "message_id", "mail_id",
    }
    assert sc["vendor"] == "hetzner.com"
    assert sc["date"] == "2026-06-28"
    assert sc["amount"] == "$5.00"
    assert sc["mail_id"] == 22321
    # must be JSON-serializable
    json.loads(json.dumps(sc))


def test_sidecar_amount_nullable():
    sc = a.sidecar_metadata(
        vendor="v", from_addr="a@b.com", dt=datetime(2026, 1, 1), amount=None,
        subject="s", message_id="<m>", mail_id=1,
    )
    assert sc["amount"] is None


# --------------------------------------------------------------------------- #
# Idempotency predicate — a row already labeled invoice-archived is excluded.
# --------------------------------------------------------------------------- #
def _predicate(row):
    """Mirror of fetch_unarchived's WHERE clause in Python, for unit testing the
    selection logic without a live DB:
        via_gmail AND raw IS NOT NULL AND NOT ('invoice-archived' = ANY(labels))
    """
    labels = row.get("labels") or []
    return (
        bool(row.get("via_gmail"))
        and row.get("raw") is not None
        and a.ARCHIVED_LABEL not in labels
    )


def test_idempotency_excludes_already_archived_row():
    archived = {"via_gmail": True, "raw": b"x", "labels": ["fyi", "invoice-archived"]}
    fresh = {"via_gmail": True, "raw": b"x", "labels": ["fyi"]}
    no_raw = {"via_gmail": True, "raw": None, "labels": []}
    not_gmail = {"via_gmail": False, "raw": b"x", "labels": []}

    assert _predicate(archived) is False  # already archived → excluded
    assert _predicate(fresh) is True       # not yet archived → included
    assert _predicate(no_raw) is False     # no raw message → excluded
    assert _predicate(not_gmail) is False  # not via_gmail → excluded


# --------------------------------------------------------------------------- #
# Orchestration with MOCKED MinIO + fake DB (no live upload, no network).
# --------------------------------------------------------------------------- #
class FakeMinio:
    """Captures put_object / ensure_bucket calls; mirrors MinioArchive's surface."""

    def __init__(self):
        self.buckets = set()
        self.objects = {}  # (bucket, key) -> (bytes, content_type)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ensure_bucket(self, bucket):
        created = bucket not in self.buckets
        self.buckets.add(bucket)
        return created

    def put_object(self, bucket, key, data, content_type):
        self.objects[(bucket, key)] = (data, content_type)


class FakeMailDB:
    """Mirrors _db.MailDB's archiver surface; tracks labels in memory."""

    def __init__(self, rows):
        self._rows = [dict(r) for r in rows]
        for r in self._rows:
            r.setdefault("labels", [])
        self.labeled = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetch_unarchived(self, limit=None):
        out = [
            dict(r) for r in self._rows
            if r.get("via_gmail") and r.get("raw") is not None
            and a.ARCHIVED_LABEL not in (r.get("labels") or [])
        ]
        return out[:limit] if limit is not None else out

    def amount_for_mail(self, mail_id):
        return None

    def add_label(self, mail_id, label):
        self.labeled.append((mail_id, label))

    def commit(self):
        self.commits += 1


def _archive_rows():
    return [
        # candidate: billing sender + PDF
        {"id": 22321, "message_id": "<h@x>", "from_addr": "billing@hetzner.com",
         "subject": "Your Hetzner invoice", "received_at": None,
         "date_header": "Sun, 28 Jun 2026 08:20:00 +0000", "via_gmail": True,
         "raw": _msg_with_pdf("billing@hetzner.com", "Your Hetzner invoice",
                              "Hetzner_2026-06-28.pdf"), "labels": ["fyi"]},
        # NOT a candidate: no pdf
        {"id": 99, "message_id": "<n@x>", "from_addr": "friend@gmail.com",
         "subject": "lunch?", "received_at": None, "date_header": None,
         "via_gmail": True, "raw": _msg_no_attachment("friend@gmail.com", "lunch?"),
         "labels": []},
        # already archived → excluded by fetch
        {"id": 7, "message_id": "<a@x>", "from_addr": "billing@hetzner.com",
         "subject": "old invoice", "received_at": None, "date_header": None,
         "via_gmail": True, "raw": _msg_with_pdf("billing@hetzner.com", "old",
                                                 "old.pdf"),
         "labels": ["invoice-archived"]},
    ]


def test_cmd_archive_uploads_and_labels(monkeypatch, capsys):
    fake_db = FakeMailDB(_archive_rows())
    fake_mc = FakeMinio()

    import _db
    import _minio
    monkeypatch.setattr(_db, "MailDB", lambda *aa, **kk: fake_db)
    monkeypatch.setattr(_minio, "MinioArchive", lambda *aa, **kk: fake_mc)

    args = types.SimpleNamespace(dry_run=False, limit=None, json=True)
    rc = extract.cmd_archive(args)
    assert rc == 0

    # one candidate (hetzner) → one PDF + one sidecar uploaded, bucket created, labeled.
    assert ("taxes-2026-invoices",
            "hetzner.com/2026-06-28-Hetzner_2026-06-28.pdf") in fake_mc.objects
    sidecar_key = ("taxes-2026-invoices",
                   "hetzner.com/2026-06-28-Hetzner_2026-06-28.pdf.json")
    assert sidecar_key in fake_mc.objects
    sc_bytes, sc_ct = fake_mc.objects[sidecar_key]
    assert sc_ct == "application/json"
    sc = json.loads(sc_bytes)
    assert sc["mail_id"] == 22321 and sc["vendor"] == "hetzner.com"

    assert fake_db.labeled == [(22321, "invoice-archived")]
    out = capsys.readouterr().out
    summary = json.loads(out)
    assert summary["candidates"] == 1
    assert summary["pdfs_uploaded"] == 1
    assert summary["sidecars"] == 1
    assert summary["labeled"] == 1
    assert summary["errors"] == 0


def test_cmd_archive_dry_run_no_uploads_no_labels(monkeypatch, capsys):
    fake_db = FakeMailDB(_archive_rows())
    fake_mc = FakeMinio()

    import _db
    import _minio
    monkeypatch.setattr(_db, "MailDB", lambda *aa, **kk: fake_db)
    # If dry-run wrongly instantiates MinIO, this would surface; make it explode.
    monkeypatch.setattr(_minio, "MinioArchive",
                        lambda *aa, **kk: (_ for _ in ()).throw(
                            AssertionError("dry-run must not touch MinIO")))

    args = types.SimpleNamespace(dry_run=True, limit=None, json=True)
    rc = extract.cmd_archive(args)
    assert rc == 0

    assert fake_db.labeled == []  # no label writes
    assert fake_mc.objects == {}  # no uploads
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["candidates"] == 1
    assert out["summary"]["mode"] == "dry-run"
    assert out["candidates"][0]["mail_id"] == 22321
    assert out["candidates"][0]["bucket"] == "taxes-2026-invoices"


def test_cmd_archive_upload_error_skips_label(monkeypatch, capsys):
    fake_db = FakeMailDB(_archive_rows())

    class ExplodingMinio(FakeMinio):
        def put_object(self, bucket, key, data, content_type):
            raise RuntimeError("simulated S3 failure")

    import _db
    import _minio
    monkeypatch.setattr(_db, "MailDB", lambda *aa, **kk: fake_db)
    monkeypatch.setattr(_minio, "MinioArchive", lambda *aa, **kk: ExplodingMinio())

    args = types.SimpleNamespace(dry_run=False, limit=None, json=True)
    rc = extract.cmd_archive(args)
    assert rc == 0

    # upload failed → mail NOT labeled (so it retries next run), error counted.
    assert fake_db.labeled == []
    summary = json.loads(capsys.readouterr().out)
    assert summary["errors"] >= 1
    assert summary["labeled"] == 0
