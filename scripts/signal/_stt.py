#!/usr/bin/env python3
"""Speech-to-text over the homelab self-hosted ASR endpoint (the `scripts/stt` API).

The endpoint is the same one the `stt` shell helper drives:
`POST {url}/v1/audio/transcriptions` with a multipart `file` field, optional
`language` form field, optional bearer token. The response JSON carries the
transcript in `text` (OpenAI-compatible shape).

Pure-ish: the network call is isolated in the injectable `transport`, and the
response validation in `extract_text`, so both are unit-testable without a
network — the same shape `mail-actions/llm.py` uses. `requests` is imported
inside the default transport, never at module scope: the hermetic suites fail
any test module that imports it (conftest `_no_live_network`).

🔴 THE URL IS CONFIGURATION, NOT A LITERAL — the caller (consumer.py's CLI
shell) resolves `STT_API_URL`/`STT_API_TOKEN` from the environment and passes
them in. The default here is the same nebula gateway route `scripts/stt` ships
with, because `transcribe` is an OPERATOR command run from the workbench CLI
(like `draft`), not from the pod. The pod never calls this module.
"""
from __future__ import annotations

import json

DEFAULT_URL = "http://10.42.0.10:8118"
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
DEFAULT_TIMEOUT = 600.0  # a 1h meeting can take minutes on CPU ASR


class SttError(RuntimeError):
    """Raised when the ASR endpoint's response cannot be read as a transcript."""


def extract_text(resp: dict) -> str:
    """Pull the transcript text out of an ASR response. Raises SttError."""
    if not isinstance(resp, dict):
        raise SttError(f"ASR response is not an object: {type(resp).__name__}")
    if resp.get("detail"):
        raise SttError(f"ASR endpoint returned an error detail: {resp['detail']}")
    text = resp.get("text")
    if not isinstance(text, str) or not text.strip():
        raise SttError("ASR response carries no transcript text")
    return text


def transcribe(data: bytes, filename: str, *, language: str | None = None,
               url: str | None = None, token: str | None = None,
               transport=None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """POST one audio file to the ASR endpoint. Returns the parsed JSON dict.

    `transport(method, url, *, files=None, form=None, headers=None,
    timeout=None)` is injectable for tests; the default builds the real
    multipart POST with `requests` (imported inside this branch — never at
    module scope).
    """
    base = (url or DEFAULT_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    files = {"file": (filename or "recording.audio", data)}
    form = {"language": language} if language else None
    if transport is None:
        def transport(method, req_url, *, files=None, form=None,
                      headers=None, timeout=None):
            import requests
            return requests.request(method, req_url, data=form, files=files,
                                    headers=headers, timeout=timeout)
    resp = transport("POST", base + TRANSCRIPTIONS_PATH, files=files,
                     form=form, headers=headers, timeout=timeout)
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "json"):
        return resp.json()
    return json.loads(resp)