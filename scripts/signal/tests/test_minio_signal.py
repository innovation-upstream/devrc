"""Attachment archiving: key layout, sidecar contents, bucket auto-create, no-op re-upload.

A fake S3 client stands in for MinIO — no port-forward, no network. The fake is
deliberately strict: it records every call, so "re-upload is a no-op" is measured
as PUT COUNT, not inferred from the absence of an error.
"""
import json

import pytest

import _minio


class FakeS3:
    """Minimal `minio.Minio` stand-in with a real object store behind it."""

    def __init__(self, existing_buckets=()):
        self.buckets = set(existing_buckets)
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.puts: list[str] = []
        self.made_buckets: list[str] = []

    def bucket_exists(self, name):
        return name in self.buckets

    def make_bucket(self, name):
        self.buckets.add(name)
        self.made_buckets.append(name)

    def stat_object(self, bucket, key):
        if (bucket, key) not in self.objects:
            raise _minio.S3Error("NoSuchKey", "missing", key, "rid", "hid", None)
        return object()

    def put_object(self, bucket, key, data, length=None, content_type=None):
        self.objects[(bucket, key)] = (data.read(), content_type)
        self.puts.append(key)


@pytest.fixture()
def mc():
    fake = FakeS3()
    client = _minio.MinioSignal(client=fake)
    with client as opened:
        yield opened, fake


# --------------------------------------------------------------------------- #
# Key layout
# --------------------------------------------------------------------------- #
def test_object_key_layout_is_conversation_date_filename():
    key = _minio.object_key(conversation="alice-uuid", timestamp_ms=1723000000101,
                            filename="receipt-scan.webp",
                            attachment_id="att-505")
    assert key == "alice-uuid/2024-08-07/att-505_receipt-scan.webp"


def test_object_key_uses_the_message_timestamp_not_wall_clock():
    """A backfilled attachment must re-derive the SAME key as its first delivery."""
    old = _minio.object_key(conversation="c", timestamp_ms=1600000000000,
                            filename="f.bin", attachment_id="att-old")
    assert old.split("/")[1] == "2020-09-13"


def test_object_key_neutralises_separators_in_untrusted_names():
    key = _minio.object_key(conversation="../../etc", timestamp_ms=1723000000101,
                            filename="a/b c.txt", attachment_id="att-path")
    assert key.count("/") == 2                       # exactly conversation/date/file
    assert ".." not in key.split("/")[0]
    assert key.endswith("a_b_c.txt")


def test_two_attachments_with_the_same_filename_do_not_collide():
    """🔴 The collision that silently DROPPED the second file.

    Two `Screenshot.png` in one conversation on one day. Without the attachment
    id in the key both map to the same object, `put_attachment` sees it already
    exists and skips the write, and the second DB row then points at the FIRST
    file's bytes with a sidecar claiming the second's provenance.
    """
    first = _minio.object_key(conversation="conv-collide", timestamp_ms=1723000000101,
                              filename="Screenshot.png", attachment_id="att-one")
    second = _minio.object_key(conversation="conv-collide", timestamp_ms=1723000000199,
                               filename="Screenshot.png", attachment_id="att-two")
    assert first != second
    assert first.rsplit("/", 1)[0] == second.rsplit("/", 1)[0]   # same day folder


def test_the_same_attachment_redelivered_keeps_one_key():
    """... while redelivery stays idempotent: the Signal id is stable."""
    a = _minio.object_key(conversation="c", timestamp_ms=1723000000101,
                          filename="Screenshot.png", attachment_id="att-stable")
    b = _minio.object_key(conversation="c", timestamp_ms=1723000000101,
                          filename="Screenshot.png", attachment_id="att-stable")
    assert a == b


def test_object_key_requires_the_attachment_id():
    with pytest.raises(ValueError) as exc:
        _minio.object_key(conversation="c", timestamp_ms=1723000000101,
                          filename="f.png", attachment_id="")
    assert "collide" in str(exc.value)


def test_two_same_named_attachments_both_reach_the_store(mc):
    """The behavioural half: both objects exist, with their OWN bytes."""
    client, fake = mc
    k1 = client.put_attachment(conversation="conv-x", timestamp_ms=1723000000101,
                               attachment_id="att-first", filename="Screenshot.png",
                               data=b"first-bytes", content_type="image/png")
    k2 = client.put_attachment(conversation="conv-x", timestamp_ms=1723000000188,
                               attachment_id="att-second", filename="Screenshot.png",
                               data=b"second-bytes", content_type="image/png")
    assert k1 != k2
    assert fake.objects[(_minio.BUCKET, k1)][0] == b"first-bytes"
    assert fake.objects[(_minio.BUCKET, k2)][0] == b"second-bytes"


def test_object_key_falls_back_when_a_segment_is_empty():
    key = _minio.object_key(conversation="", timestamp_ms=1723000000101, filename="",
                            attachment_id="att-empty")
    assert key.startswith("unknown/")
    assert key.endswith("/att-empty_attachment")


def test_conversation_key_distinguishes_groups_from_dms():
    import consumer
    dm = consumer.conversation_key({"source_uuid": "abc-uuid"})
    grp = consumer.conversation_key({"group_id": b"group-three",
                                     "source_uuid": "abc-uuid"})
    assert dm == "abc-uuid"
    assert grp.startswith("group-") and grp != dm


# --------------------------------------------------------------------------- #
# put_attachment
# --------------------------------------------------------------------------- #
def test_bucket_is_auto_created_on_first_use(mc):
    client, fake = mc
    assert fake.made_buckets == []
    client.put_attachment(conversation="conv-1", timestamp_ms=1723000000101,
                          attachment_id="att-a1", filename="a.png", data=b"bytes-a",
                          content_type="image/png")
    assert fake.made_buckets == [_minio.BUCKET]
    # ... and only once, however many attachments follow.
    client.put_attachment(conversation="conv-1", timestamp_ms=1723000000102,
                          attachment_id="att-b2", filename="b.png", data=b"bytes-b",
                          content_type="image/png")
    assert fake.made_buckets == [_minio.BUCKET]


def test_existing_bucket_is_not_recreated():
    fake = FakeS3(existing_buckets=[_minio.BUCKET])
    with _minio.MinioSignal(client=fake) as client:
        client.put_attachment(conversation="conv-2", timestamp_ms=1723000000103,
                              attachment_id="att-c3", filename="c.png", data=b"bytes-c",
                              content_type="image/png")
    assert fake.made_buckets == []


def test_attachment_bytes_and_content_type_land_under_the_key(mc):
    client, fake = mc
    key = client.put_attachment(conversation="conv-3", timestamp_ms=1723000000104,
                                attachment_id="att-d4", filename="photo.heic", data=b"heic-bytes",
                                content_type="image/heic")
    blob, ctype = fake.objects[(_minio.BUCKET, key)]
    assert blob == b"heic-bytes"
    assert ctype == "image/heic"


def test_sidecar_json_carries_the_metadata(mc):
    client, fake = mc
    sidecar = {"conversation": "conv-4", "message_id": 4242,
               "message_timestamp": 1723000000105, "content_type": "application/pdf",
               "signal_attachment_id": "att-sidecar-4"}
    key = client.put_attachment(conversation="conv-4", timestamp_ms=1723000000105,
                                attachment_id="att-sidecar-4", filename="doc.pdf", data=b"%PDF-1.7",
                                content_type="application/pdf", sidecar=sidecar)
    blob, ctype = fake.objects[(_minio.BUCKET, key + _minio.SIDECAR_SUFFIX)]
    assert ctype == "application/json"
    assert json.loads(blob) == sidecar


def test_reupload_of_the_same_key_is_a_noop(mc):
    """Measured as PUT COUNT — 'no error' would not distinguish it from a rewrite."""
    client, fake = mc
    args = dict(conversation="conv-5", timestamp_ms=1723000000106,
                attachment_id="att-dup-5", filename="dup.bin", data=b"first-write",
                content_type="application/octet-stream",
                sidecar={"signal_attachment_id": "att-dup-5"})
    key = client.put_attachment(**args)
    puts_after_first = list(fake.puts)
    assert len(puts_after_first) == 2                     # object + sidecar

    client.put_attachment(**{**args, "data": b"SECOND-WRITE"})
    assert fake.puts == puts_after_first                  # nothing new was written
    assert fake.objects[(_minio.BUCKET, key)][0] == b"first-write"


def test_positive_control_a_different_key_does_write(mc):
    """The zero above is only meaningful if this one moves."""
    client, fake = mc
    client.put_attachment(conversation="conv-6", timestamp_ms=1723000000107,
                          attachment_id="att-e5", filename="one.bin", data=b"x", content_type="a/b")
    before = len(fake.puts)
    client.put_attachment(conversation="conv-6", timestamp_ms=1723000000107,
                          attachment_id="att-f6", filename="two.bin", data=b"y", content_type="a/b")
    assert len(fake.puts) == before + 1


def test_object_exists_is_false_for_an_absent_key(mc):
    client, _fake = mc
    assert client.object_exists("nothing/here/at-all") is False


def test_client_outside_the_context_manager_is_a_clear_error():
    with pytest.raises(RuntimeError) as exc:
        _minio.MinioSignal(client=None)._c
    assert "context manager" in str(exc.value)


def test_bucket_name_is_the_documented_one():
    assert _minio.BUCKET == "signal-attachments"
    assert _minio.MinioSignal(client=FakeS3()).bucket == "signal-attachments"


def test_parse_config_env_reads_quoted_and_bare_values():
    env = _minio.parse_config_env(
        'export MINIO_ROOT_USER="admin-user"\n'
        "export MINIO_ROOT_PASSWORD=bare-secret\n"
        "# export MINIO_IGNORED=nope\n")
    assert env["MINIO_ROOT_USER"] == "admin-user"
    assert env["MINIO_ROOT_PASSWORD"] == "bare-secret"
    assert "MINIO_IGNORED" not in env
