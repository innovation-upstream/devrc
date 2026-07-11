#!/usr/bin/env python3
"""prepare — build the per-session `input.json` the live session extracts from.

For each candidate (from selection.py) this deterministically:
  1. reads the transcript (raw JSONL) and SCRUBS secrets (scrub.py) BEFORE
     anything else touches it,
  2. CHUNKS the scrubbed text on a char budget WITHOUT ever splitting a single
     message (transcript line = one message; a small overlap carries the last
     message into the next chunk for continuity),
  3. attaches the Layer A rollup verbatim as `ground_truth`,
  4. embeds the schema block + anti-confabulation contract (schema.py),
  5. writes `staging/<run-id>/<session>.input.json` (+ a run `manifest.json`).

Staging + results live under `~/.local/state/activity/insights/` (created 0700 —
they hold scrubbed full transcripts). No LLM here; no network beyond select's
CH read. Everything below `read_and_scrub`/`chunk_text`/`build_input` is pure
given a transcript on disk, so it is unit-testable with a tiny fixture file.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets as _secrets
from pathlib import Path

from schema import SCHEMA_VERSION, schema_block
from scrub import scrub

DEFAULT_CHUNK_CHARS = 24000   # ≈ 6k tokens; never splits a message

_INSTRUCTIONS = (
    "MAP-REDUCE. For each entry in `chunks`, note the qualitative observations "
    "(goal signals, friction moments, successes, automation/toil/gap candidates). "
    "Then REDUCE the per-chunk notes + `ground_truth` into ONE result.json "
    "conforming to `schema`. Counts come from `ground_truth` — NEVER recount. "
    "Write the result to `result_path`. If the transcript is too degraded to "
    "judge, set unreadable=true + a one-line unreadable_reason and leave the "
    "qualitative fields empty (honesty over fabrication)."
)


# --------------------------------------------------------------------------- #
# State dirs (0700 — hold scrubbed transcripts)
# --------------------------------------------------------------------------- #
def state_root() -> Path:
    explicit = os.environ.get("INSIGHT_STATE_DIR")
    if explicit:
        return Path(explicit)
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    return base / "activity" / "insights"


def staging_dir(run_id: str) -> Path:
    return state_root() / "staging" / run_id


def results_dir(run_id: str) -> Path:
    return state_root() / "results" / run_id


def new_run_id() -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_secrets.token_hex(2)}"


def _ensure_private(path: Path) -> None:
    """Create `path` (and EVERY level from state_root down) mode 0700 — these
    dirs hold scrubbed full transcripts. A plain `mkdir(parents=True)` creates the
    intermediate `staging/`|`results/` (and even `state_root`) at the umask
    (typically 0755, group/world-readable); we force 0700 on every level so a
    scrubbed-but-incomplete transcript on disk is never group/world-readable."""
    root = state_root()
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
        cur = root
        for part in path.relative_to(root).parts:
            cur = cur / part
            os.chmod(cur, 0o700)
    except (OSError, ValueError):
        # ValueError: path not under root (custom INSIGHT_STATE_DIR layout) —
        # still lock down the leaf, best-effort.
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def _write_private(path: Path, text: str) -> None:
    """Write `text` to `path` mode 0600 (holds scrubbed transcript content)."""
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def read_and_scrub(path: str, redact_public_ips: bool = False) -> tuple[str, dict, bool]:
    """Read a transcript file and scrub secrets. Returns
    `(scrubbed_text, redaction_counts, unreadable)`. `unreadable=True` when the
    file can't be opened or is empty (an honest signal, never fabricated)."""
    try:
        with open(path, errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return "", {}, True
    if not raw.strip():
        return "", {}, True
    clean, counts = scrub(raw, redact_public_ips=redact_public_ips)
    return clean, counts, False


def chunk_text(text: str, budget: int = DEFAULT_CHUNK_CHARS,
               overlap: int = 1) -> list[str]:
    """Split `text` into ordered chunks bounded by `budget` chars, NEVER splitting
    a line (= one transcript message). Each chunk after the first begins with the
    last `overlap` line(s) of the previous chunk for continuity. A single line
    longer than the budget becomes its own (oversized) chunk rather than being
    split."""
    lines = [ln for ln in text.split("\n") if ln.strip() != ""]
    if not lines:
        return []
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for ln in lines:
        cost = len(ln) + 1
        if cur and size + cost > budget:
            chunks.append("\n".join(cur))
            cur = cur[-overlap:] if overlap > 0 else []
            size = sum(len(x) + 1 for x in cur)
        cur.append(ln)
        size += cost
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def build_input(candidate: dict, run_id: str, chunk_chars: int = DEFAULT_CHUNK_CHARS,
                redact_public_ips: bool = False) -> dict:
    """Build the input.json content for one candidate (reads its transcript)."""
    clean, counts, unreadable = read_and_scrub(
        candidate["transcript_path"], redact_public_ips=redact_public_ips)
    chunks = chunk_text(clean, budget=chunk_chars)
    session = candidate["session"]
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "session": session,
        "project": candidate.get("project") or "",
        "cwd": candidate.get("cwd") or "",
        "ground_truth": candidate.get("ground_truth") or {},
        "redaction_counts": counts,
        "transcript_unreadable": unreadable,
        "chunk_count": len(chunks),
        "chunks": [{"idx": i, "text": c} for i, c in enumerate(chunks)],
        "schema": schema_block(),
        "anti_confabulation_contract": schema_block()["anti_confabulation_contract"],
        "instructions": _INSTRUCTIONS,
        "result_path": str(results_dir(run_id) / f"{session}.result.json"),
    }


# --------------------------------------------------------------------------- #
# Run (I/O)
# --------------------------------------------------------------------------- #
def prepare_run(candidates: list[dict], skips: list[tuple], run_id: str,
                chunk_chars: int = DEFAULT_CHUNK_CHARS,
                redact_public_ips: bool = False, meta: dict | None = None) -> dict:
    """Write every input.json + a manifest.json; return the manifest dict."""
    sdir = staging_dir(run_id)
    rdir = results_dir(run_id)
    _ensure_private(sdir)
    _ensure_private(rdir)

    sessions_meta = []
    for cand in candidates:
        payload = build_input(cand, run_id, chunk_chars, redact_public_ips)
        input_path = sdir / f"{cand['session']}.input.json"
        _write_private(
            input_path, json.dumps(payload, ensure_ascii=False, indent=2))
        sessions_meta.append({
            "session": cand["session"],
            "project": cand.get("project") or "",
            "cwd": cand.get("cwd") or "",
            "end_ts": cand.get("end_ts"),
            "summary_ts": cand.get("summary_ts"),
            "input_path": str(input_path),
            "result_path": payload["result_path"],
            "chunk_count": payload["chunk_count"],
            "redaction_counts": payload["redaction_counts"],
            "transcript_unreadable": payload["transcript_unreadable"],
        })

    manifest = {
        "run_id": run_id,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "staging_dir": str(sdir),
        "results_dir": str(rdir),
        "chunk_chars": chunk_chars,
        "redact_public_ips": redact_public_ips,
        "meta": meta or {},
        "sessions": sessions_meta,
        "skips": [[s, r] for s, r in skips],
    }
    _write_private(sdir / "manifest.json",
                   json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def load_manifest(run_id: str) -> dict:
    path = staging_dir(run_id) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))
