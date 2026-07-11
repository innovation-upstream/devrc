#!/usr/bin/env python3
"""write — consolidate result.json files → validate → `emit` to ClickHouse.

The write path is `emit` (spec §10), NOT a direct authed INSERT: consistency
with every other source, the always-running collector daemon batches / offline-
buffers / retries / stamps `host`, and no writer creds live in this script. The
row lands within a collector flush interval (fine for a manual tool).

Per validated result it runs (via the shared `emit` binary):

    emit source=claude kind=session-insight \\
         b64:session=<session> b64:project=<project> b64:cwd=<cwd> \\
         b64:text=<brief_summary> b64:payload=<json(payload)> ts=<session end_ts>

`ts` is the session's `end_ts` from the Layer A rollup (already a ClickHouse
wall-clock UTC string), falling back to the summary row's own ts — the insight
sits at the session's END instant, aligned with the pipeline's ts-is-UTC
contract, NOT emit time. UNREADABLE sessions ARE emitted (unreadable=true, empty
qualitative fields, populated reason) so the skip is durable and idempotency can
reason about them. Re-emits are append-only (readers argMax on ingested_at);
there is NO delete path. A local `emitted.json` marker skips a session already
emitted for THIS run unless `--force`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "collector" / "claude"))
from _shared import emit_path  # noqa: E402

from schema import SCHEMA_VERSION
from consolidate import consolidate
from prepare import load_manifest, staging_dir, results_dir


def build_emit_args(session: str, project: str, cwd: str, brief_summary: str,
                    payload: dict, ts: str | None) -> list[str]:
    """Exact argv (minus the emit binary) for one session-insight row."""
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    args = [
        "source=claude",
        "kind=session-insight",
        f"b64:session={session}",
        f"b64:project={project}",
        f"b64:cwd={cwd}",
        f"b64:text={brief_summary}",
        f"b64:payload={payload_json}",
    ]
    if ts:
        args.append(f"ts={ts}")
    return args


# --------------------------------------------------------------------------- #
# Local "already emitted this run" marker (idempotency without a CH round-trip)
# --------------------------------------------------------------------------- #
def _emitted_marker(run_id: str) -> Path:
    return results_dir(run_id) / "emitted.json"


def _load_emitted(run_id: str) -> set:
    try:
        data = json.loads(_emitted_marker(run_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data) if isinstance(data, list) else set()


def _save_emitted(run_id: str, sessions: set) -> None:
    path = _emitted_marker(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(sessions)), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def write_run(run_id: str, force: bool = False, clean: bool = False,
              emit_bin: str | None = None, runner=None) -> dict:
    manifest = load_manifest(run_id)
    expected = [s["session"] for s in manifest["sessions"]]
    meta = {s["session"]: s for s in manifest["sessions"]}

    con = consolidate(expected, manifest["results_dir"])
    emit_bin = emit_bin or emit_path()
    runner = runner or subprocess.run
    already = _load_emitted(run_id)

    emitted: list[str] = []
    skipped_dupe: list[str] = []
    for item in con["emitted_ok"]:
        sess = item["session"]
        if sess in already and not force:
            skipped_dupe.append(sess)
            continue
        m = meta.get(sess, {})
        payload = item["payload"]
        payload.setdefault("session", sess)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        ts = m.get("end_ts") or m.get("summary_ts")
        args = build_emit_args(sess, m.get("project", "") or "",
                               m.get("cwd", "") or "",
                               payload.get("brief_summary", "") or "",
                               payload, ts)
        runner([emit_bin, *args], check=True)
        emitted.append(sess)

    _save_emitted(run_id, already | set(emitted))

    summary = {
        "run_id": run_id,
        "emitted": emitted,
        "skipped_already_emitted": skipped_dupe,
        "missing": con["missing"],
        "conflicts": con["conflicts"],
        "rejected": con["rejected"],
        "warnings": {i["session"]: i["warnings"]
                     for i in con["emitted_ok"] if i["warnings"]},
    }

    fully_clean = not (con["missing"] or con["conflicts"] or con["rejected"])
    if clean and fully_clean:
        for d in (staging_dir(run_id), results_dir(run_id)):
            shutil.rmtree(d, ignore_errors=True)
        summary["cleaned"] = True
    return summary
