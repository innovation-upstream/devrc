#!/usr/bin/env python3
"""MinIO (S3) access for the mail-actions invoice archiver.

The homelab MinIO "archive" tenant is reachable only in-cluster (the `minio`
ClusterIP service in namespace `minio-archive`, port 80 → container 9000). We
bridge to it exactly the way `_db.py` bridges to Postgres: start a
`kubectl port-forward` on an ephemeral local port, talk to an S3 client at
`http://127.0.0.1:<port>` with **path-style** addressing (MinIO requires it),
and tear the forward down on exit.

Credentials resolve, in order:
  1. env  — MINIO_ARCHIVE_ENDPOINT / MINIO_ARCHIVE_ACCESS_KEY / MINIO_ARCHIVE_SECRET_KEY
            (if ACCESS+SECRET are set, no kubectl/secret read is needed; ENDPOINT
            overrides the port-forward and is used verbatim)
  2. k8s secret `minio-archive-config`, key `config.env` — parsed for the
     `export MINIO_ROOT_USER=...` / `export MINIO_ROOT_PASSWORD=...` shell lines.

No secrets are hardcoded. See README for the nix-shell run command.

Usage:
    with MinioArchive() as mc:
        mc.ensure_bucket("taxes-2026-invoices")
        mc.put_object("taxes-2026-invoices", "hetzner.com/2026-06-28-inv.pdf",
                      data_bytes, "application/pdf")
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import socket
import subprocess
import time
from urllib.parse import urlparse

try:
    from minio import Minio
    from minio.error import S3Error
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "minio is required. On NixOS run under:\n"
        "  nix-shell -p \"python3.withPackages(p:[p.minio p.psycopg2 p.requests])\" "
        "--run 'python scripts/mail-actions/extract.py archive-invoices ...'"
    ) from exc

NAMESPACE = "minio-archive"
SERVICE = "svc/minio"
SERVICE_PORT = 80
CONFIG_SECRET = "minio-archive-config"
CONFIG_KEY = "config.env"

# Parse `export KEY=value` (optionally quoted) out of a shell config.env blob.
_EXPORT_RE = re.compile(
    r"""^\s*export\s+(?P<key>\w+)\s*=\s*(?P<val>"[^"]*"|'[^']*'|[^\s#]+)""",
    re.MULTILINE,
)


def _free_local_port() -> int:
    """Ask the OS for a free TCP port (bind to 0, read it back, release)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def parse_config_env(blob: str) -> dict:
    """Extract `export KEY=value` shell assignments into a plain dict (quotes stripped)."""
    out: dict[str, str] = {}
    for m in _EXPORT_RE.finditer(blob):
        val = m.group("val")
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            val = val[1:-1]
        out[m.group("key")] = val
    return out


def _read_creds_from_secret() -> tuple[str, str]:
    """Read MINIO_ROOT_USER / MINIO_ROOT_PASSWORD from the k8s secret's config.env."""
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


class MinioArchive:
    """Context manager: (optional) port-forward → minio S3 client, torn down on exit.

    If MINIO_ARCHIVE_ENDPOINT is set, that endpoint is used verbatim and NO
    port-forward is started (useful for tests or an in-cluster runner). Otherwise a
    `kubectl port-forward svc/minio 0:80` is started and the client points at it.
    """

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        endpoint: str | None = None,
        ready_timeout: float = 20.0,
    ):
        self._access_key = access_key or os.environ.get("MINIO_ARCHIVE_ACCESS_KEY")
        self._secret_key = secret_key or os.environ.get("MINIO_ARCHIVE_SECRET_KEY")
        self._endpoint = endpoint or os.environ.get("MINIO_ARCHIVE_ENDPOINT")
        self._ready_timeout = ready_timeout
        self._pf: subprocess.Popen | None = None
        self.client: "Minio | None" = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "MinioArchive":
        if not (self._access_key and self._secret_key):
            self._access_key, self._secret_key = _read_creds_from_secret()

        if self._endpoint:
            # Explicit endpoint: use verbatim, no port-forward.
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
            host,
            access_key=self._access_key,
            secret_key=self._secret_key,
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
        """'http://127.0.0.1:9000' → ('127.0.0.1:9000', False); bare host:port → (host, False)."""
        if "://" in endpoint:
            u = urlparse(endpoint)
            return u.netloc, u.scheme == "https"
        return endpoint, False

    @property
    def _c(self) -> "Minio":
        if self.client is None:
            raise RuntimeError("MinioArchive used outside its context manager (no client)")
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
    def ensure_bucket(self, bucket: str) -> bool:
        """Create `bucket` if it does not exist. Returns True if it was created."""
        if self._c.bucket_exists(bucket):
            return False
        self._c.make_bucket(bucket)
        return True

    def put_object(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> None:
        """Upload `data` to bucket/key with the given content type."""
        self._c.put_object(
            bucket, key, io.BytesIO(data), length=len(data),
            content_type=content_type,
        )

    def object_exists(self, bucket: str, key: str) -> bool:
        """True if bucket/key already exists (best-effort; False if bucket absent)."""
        try:
            self._c.stat_object(bucket, key)
            return True
        except S3Error:
            return False
