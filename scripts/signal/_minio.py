#!/usr/bin/env python3
"""MinIO (S3) access for Signal attachments.

A deliberate clone of `scripts/mail-actions/_minio.py`: the homelab MinIO
"archive" tenant is reachable only in-cluster (the `minio` ClusterIP service in
namespace `minio-archive`, port 80 → container 9000), so we bridge with an
ephemeral `kubectl port-forward`, talk path-style to `http://127.0.0.1:<port>`,
and tear the forward down on exit.

Credentials resolve, in order:
  1. env — MINIO_ARCHIVE_ENDPOINT / MINIO_ARCHIVE_ACCESS_KEY / MINIO_ARCHIVE_SECRET_KEY
  2. k8s secret `minio-archive-config`, key `config.env`

Layout (bucket `signal-attachments`):

    <conversation>/<YYYY-MM-DD>/<filename>          the attachment bytes
    <conversation>/<YYYY-MM-DD>/<filename>.json     a sidecar with the metadata

`put_attachment()` is idempotent: re-uploading an existing key is a NO-OP, not a
second copy — redelivery replays attachments and must not duplicate objects.

Usage:
    with MinioSignal() as mc:
        mc.put_attachment(conversation="…", timestamp_ms=…, filename="a.jpg",
                          data=b"…", content_type="image/jpeg", sidecar={…})
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    from minio import Minio
    from minio.error import S3Error
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "minio is required. On NixOS run under:\n"
        "  nix-shell -p \"python3.withPackages(p:[p.minio p.psycopg2 p.requests])\" "
        "--run 'python scripts/signal/consumer.py run'"
    ) from exc

NAMESPACE = "minio-archive"
SERVICE = "svc/minio"
SERVICE_PORT = 80
CONFIG_SECRET = "minio-archive-config"
CONFIG_KEY = "config.env"
BUCKET = "signal-attachments"
SIDECAR_SUFFIX = ".json"

_EXPORT_RE = re.compile(
    r"""^\s*export\s+(?P<key>\w+)\s*=\s*(?P<val>"[^"]*"|'[^']*'|[^\s#]+)""",
    re.MULTILINE,
)

# Anything outside this set is replaced in a key segment: a Signal filename or a
# display name can carry slashes, spaces and control characters, and a `/` would
# silently reshape the key layout.
_UNSAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._+-]+")
# A run of dots is collapsed AFTER separators are neutralised, so no key segment
# can carry a `..` traversal fragment even in an object browser that re-splits it.
_DOT_RUN = re.compile(r"\.{2,}")


def _free_local_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def parse_config_env(blob: str) -> dict:
    """Extract `export KEY=value` shell assignments into a plain dict."""
    out: dict[str, str] = {}
    for m in _EXPORT_RE.finditer(blob):
        val = m.group("val")
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        out[m.group("key")] = val
    return out


def _read_creds_from_secret() -> tuple[str, str]:
    import base64

    raw = subprocess.check_output(
        [
            "kubectl", "-n", NAMESPACE, "get", "secret", CONFIG_SECRET,
            "-o", f"jsonpath={{.data.{CONFIG_KEY.replace('.', '\\.')}}}",
        ],
        text=True,
    ).strip()
    blob = base64.b64decode(raw).decode()
    env = parse_config_env(blob)
    user = env.get("MINIO_ROOT_USER")
    password = env.get("MINIO_ROOT_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            f"secret {CONFIG_SECRET}/{CONFIG_KEY} missing MINIO_ROOT_USER/PASSWORD"
        )
    return user, password


def safe_segment(value: str, *, fallback: str = "unknown") -> str:
    """One key path segment, with separators and whitespace neutralised."""
    cleaned = _DOT_RUN.sub("_", _UNSAFE_SEGMENT.sub("_", str(value or ""))).strip("_")
    return cleaned or fallback


def object_key(*, conversation: str, timestamp_ms: int, filename: str,
               attachment_id: str) -> str:
    """`{conversation}/{YYYY-MM-DD}/{attachment_id}_{filename}` — the layout.

    🔴 `attachment_id` IS PART OF THE KEY, and required. Keying on
    `{conversation}/{date}/{filename}` alone collides for the commonest filename
    there is: two `Screenshot.png` in one conversation on one day produce the
    same key, `put_attachment` then sees the object already exists and SKIPS the
    write, and the second attachment's DB row points at the first one's bytes
    while its sidecar misattributes provenance. Silent, and unrecoverable once
    the sender deletes the original.

    The date comes from the message's own timestamp (UTC), NOT from wall-clock
    now: a redelivered or backfilled attachment must land on the same key as the
    first delivery, or idempotency is decided by when the consumer happened to
    run. The Signal attachment id is stable across redelivery for the same
    reason, so the key stays idempotent while becoming unique.
    """
    if not attachment_id:
        raise ValueError(
            "object_key needs the signal attachment id: without it two files of "
            "the same name in one day collide and the second is silently dropped")
    day = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)
    return (f"{safe_segment(conversation)}/{day.strftime('%Y-%m-%d')}/"
            f"{safe_segment(attachment_id, fallback='attachment')}_"
            f"{safe_segment(filename, fallback='attachment')}")


class MinioSignal:
    """Context manager: (optional) port-forward → minio S3 client, torn down on exit.

    If MINIO_ARCHIVE_ENDPOINT is set, that endpoint is used verbatim and NO
    port-forward is started.
    """

    bucket = BUCKET

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        endpoint: str | None = None,
        ready_timeout: float = 20.0,
        client=None,
    ):
        self._access_key = access_key or os.environ.get("MINIO_ARCHIVE_ACCESS_KEY")
        self._secret_key = secret_key or os.environ.get("MINIO_ARCHIVE_SECRET_KEY")
        self._endpoint = endpoint or os.environ.get("MINIO_ARCHIVE_ENDPOINT")
        self._ready_timeout = ready_timeout
        self._pf: subprocess.Popen | None = None
        # An injected client short-circuits every connection concern — this is
        # the seam the hermetic suite drives.
        self.client = client
        self._bucket_checked = False

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "MinioSignal":
        if self.client is not None:
            return self
        if not (self._access_key and self._secret_key):
            self._access_key, self._secret_key = _read_creds_from_secret()

        if self._endpoint:
            host, secure = self._split_endpoint(self._endpoint)
        else:
            local_port = _free_local_port()
            self._pf = subprocess.Popen(
                [
                    "kubectl", "-n", NAMESPACE, "port-forward", SERVICE,
                    f"{local_port}:{SERVICE_PORT}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            self._wait_for_port("127.0.0.1", local_port)
            host, secure = f"127.0.0.1:{local_port}", False

        self.client = Minio(
            host, access_key=self._access_key, secret_key=self._secret_key,
            secure=secure,
        )
        return self

    def __exit__(self, *_exc) -> None:
        if self._pf is not None:
            self._pf.terminate()
            with contextlib.suppress(Exception):
                self._pf.wait(timeout=5)

    @staticmethod
    def _split_endpoint(endpoint: str) -> tuple[str, bool]:
        if "://" in endpoint:
            u = urlparse(endpoint)
            return u.netloc, u.scheme == "https"
        return endpoint, False

    @property
    def _c(self):
        if self.client is None:
            raise RuntimeError("MinioSignal used outside its context manager (no client)")
        return self.client

    def _wait_for_port(self, host: str, port: int) -> None:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if self._pf and self._pf.poll() is not None:
                err = self._pf.stderr.read().decode() if self._pf.stderr else ""
                raise RuntimeError(f"kubectl port-forward exited early:\n{err}")
            with contextlib.suppress(OSError):
                with socket.create_connection((host, port), timeout=1):
                    return
            time.sleep(0.25)
        raise TimeoutError(f"port-forward to {host}:{port} not ready in time")

    # -- operations --------------------------------------------------------
    def ensure_bucket(self, bucket: str | None = None) -> bool:
        """Create the bucket if absent. Returns True if it was created."""
        name = bucket or self.bucket
        if self._c.bucket_exists(name):
            return False
        self._c.make_bucket(name)
        return True

    def object_exists(self, key: str, bucket: str | None = None) -> bool:
        try:
            self._c.stat_object(bucket or self.bucket, key)
            return True
        except S3Error:
            return False

    def put_attachment(self, *, conversation: str, timestamp_ms: int, filename: str,
                       data: bytes, content_type: str, attachment_id: str,
                       sidecar: dict | None = None) -> str:
        """Upload one attachment + its sidecar. Returns the object key.

        Idempotent: an existing key is left alone (both the object and its
        sidecar), so a redelivery re-derives the same key and writes nothing.
        """
        if not self._bucket_checked:
            self.ensure_bucket()
            self._bucket_checked = True
        key = object_key(conversation=conversation, timestamp_ms=timestamp_ms,
                         filename=filename, attachment_id=attachment_id)
        if not self.object_exists(key):
            self._c.put_object(
                self.bucket, key, io.BytesIO(data), length=len(data),
                content_type=content_type,
            )
        sidecar_key = key + SIDECAR_SUFFIX
        if sidecar is not None and not self.object_exists(sidecar_key):
            blob = json.dumps(sidecar, sort_keys=True, default=str).encode()
            self._c.put_object(
                self.bucket, sidecar_key, io.BytesIO(blob), length=len(blob),
                content_type="application/json",
            )
        return key
