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
there is NO delete path. A local `emitted.json` marker (checkpointed after EACH
successful emit, so a mid-fan-out crash never re-emits on re-run) skips a session
already emitted for THIS run unless `--force`.

The emit line is bounded to < PIPE_BUF (4096B) before emitting: `emit` appends
one `printf '%s\\n'` line, and only a sub-PIPE_BUF write is atomic under O_APPEND
— on the always-active workbench a longer line could interleave with a
concurrent emit and corrupt the row. `fit_payload` truncates the free-text
fields (visibly) until the FULL line fits (see below).

After a session emits, its `input.json` + `result.json` are purged (per-session
cleanup); residue only survives for sessions that genuinely need a re-run
(missing/conflict/quarantined). `--clean` purges the whole run; `--keep` opts out.
"""
from __future__ import annotations

import base64
import copy
import json
import os
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
# PIPE_BUF budget — keep the FULL emit line atomic-appendable (< 4096B)
# --------------------------------------------------------------------------- #
# `emit` appends the row as a single `printf '%s\n'` write. On a local fs a write
# whose total size is < PIPE_BUF (4096B) is atomic under O_APPEND, so concurrent
# emits never interleave; a longer line can tear. A payload with EVERY documented
# field at its limit base64-encodes to ~4160B > PIPE_BUF, so we bound it here.
PIPE_BUF = 4096
EMIT_LINE_BUDGET = 3800          # target for the full line — leaves >290B headroom
_HOST_ESTIMATE = 40              # emit auto-fills host=<hostname>; be generous
_TRUNC_MARK = "…[truncated]"


def _emit_line_bytes(args: list[str]) -> int:
    """Byte length of the FULL line `emit` would append for `args`: b64: values
    base64-encoded (base64 inflates ~4/3), ts/host auto-filled when absent, plus
    the trailing newline — i.e. the exact unit the PIPE_BUF guarantee applies to."""
    parts = ["v1"]
    have_ts = have_host = False
    for kv in args:
        key, _, val = kv.partition("=")
        if key.startswith("b64:"):
            enc = base64.b64encode(val.encode("utf-8")).decode("ascii")
            parts.append(f"{key}={enc}")
        else:
            if key == "ts":
                have_ts = True
            elif key == "host":
                have_host = True
            parts.append(kv)
    if not have_ts:
        parts.append("ts=0000-00-00 00:00:00.000")     # 23-char stand-in
    if not have_host:
        parts.append("host=" + "h" * _HOST_ESTIMATE)
    return len("\t".join(parts).encode("utf-8")) + 1    # +1 for the '\n'


def _truncate(s: str, cap: int) -> str:
    """Truncate `s` to ~`cap` chars with a visible marker (honest truncation).
    cap<=0 collapses a non-empty string to just the marker."""
    if not isinstance(s, str) or not s:
        return s
    if cap <= 0:
        return _TRUNC_MARK
    if len(s) <= cap:
        return s
    return s[:max(0, cap - len(_TRUNC_MARK))] + _TRUNC_MARK


def fit_payload(session: str, project: str, cwd: str, payload: dict,
                ts: str | None, budget: int = EMIT_LINE_BUDGET) -> dict:
    """Return a copy of `payload` with FREE-TEXT fields truncated (in a fixed
    priority order, with a visible marker) until the full emit line is < budget.
    Never touches identity fields or `ground_truth` counts (there are none in the
    payload — counts live in Layer A). Keeps the stored JSON valid (a required
    `description` collapses to the marker, still non-empty). No-op when it fits."""
    payload = copy.deepcopy(payload)

    def _len() -> int:
        brief = payload.get("brief_summary", "") or ""
        return _emit_line_bytes(
            build_emit_args(session, project, cwd, brief, payload, ts))

    if _len() <= budget:
        return payload

    # Priority 1: brief_summary (also the `text` column — it appears TWICE).
    for cap in (600, 400, 250, 150, 80, 40, 0):
        if isinstance(payload.get("brief_summary"), str):
            payload["brief_summary"] = _truncate(payload["brief_summary"], cap)
        if _len() <= budget:
            return payload

    # Priority 2: the enriched objects' free text (description/evidence/trigger/…).
    for cap in (600, 400, 250, 150, 80, 40, 0):
        for key in ("automation_opportunity", "recurring_toil", "workflow_gap"):
            obj = payload.get(key)
            if isinstance(obj, dict):
                for f in ("evidence", "description", "trigger", "frequency_hint"):
                    if isinstance(obj.get(f), str):
                        obj[f] = _truncate(obj[f], cap)
        if _len() <= budget:
            return payload

    # Priority 3: friction_detail items.
    for cap in (200, 120, 60, 20, 0):
        fd = payload.get("friction_detail")
        if isinstance(fd, list):
            payload["friction_detail"] = [_truncate(str(x), cap) for x in fd]
        if _len() <= budget:
            return payload

    # Last resort: remaining free text so the line ALWAYS fits.
    for cap in (200, 100, 40, 0):
        for f in ("underlying_goal", "primary_success"):
            if isinstance(payload.get(f), str):
                payload[f] = _truncate(payload[f], cap)
        if _len() <= budget:
            return payload

    return payload


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
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _purge_session(run_id: str, session: str) -> None:
    """Remove ONE session's scrubbed input + its result from disk (best-effort)."""
    for p in (staging_dir(run_id) / f"{session}.input.json",
              results_dir(run_id) / f"{session}.result.json"):
        try:
            p.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def write_run(run_id: str, force: bool = False, clean: bool = False,
              keep: bool = False, emit_bin: str | None = None, runner=None) -> dict:
    manifest = load_manifest(run_id)
    expected = [s["session"] for s in manifest["sessions"]]
    meta = {s["session"]: s for s in manifest["sessions"]}

    con = consolidate(expected, manifest["results_dir"])
    emit_bin = emit_bin or emit_path()
    runner = runner or subprocess.run
    already = _load_emitted(run_id)

    emitted: list[str] = []
    failed: list[dict] = []
    for item in con["emitted_ok"]:
        sess = item["session"]
        if sess in already and not force:
            continue                       # already emitted this run (see skipped_dupe)
        m = meta.get(sess, {})
        payload = item["payload"]
        payload.setdefault("session", sess)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        ts = m.get("end_ts") or m.get("summary_ts")
        # Bound the payload so the FULL emit line stays atomic-appendable.
        payload = fit_payload(sess, m.get("project", "") or "",
                              m.get("cwd", "") or "", payload, ts)
        args = build_emit_args(sess, m.get("project", "") or "",
                               m.get("cwd", "") or "",
                               payload.get("brief_summary", "") or "",
                               payload, ts)
        try:
            runner([emit_bin, *args], check=True)
        except Exception as e:  # noqa: BLE001 — one bad emit must not abort the run
            failed.append({"session": sess, "error": f"{type(e).__name__}: {e}"})
            continue
        emitted.append(sess)
        # Checkpoint after EACH emit (mirror the session-tailer pattern) so a
        # mid-fan-out crash never re-emits an already-shipped session on re-run.
        _save_emitted(run_id, already | set(emitted))

    if not emitted:                       # ensure the marker exists even on a no-op
        _save_emitted(run_id, already)

    # A session already in the marker but NOT re-emitted this run is a durable
    # "already emitted" skip. Its result.json may have been purged on the prior
    # run, so it can surface in con["missing"] — reconcile against the marker so a
    # done session is never mis-reported as needing a re-run.
    skipped_dupe = sorted(s for s in expected if s in already and s not in emitted)
    real_missing = [s for s in con["missing"] if s not in already]

    # Per-session cleanup: purge every DONE session (emitted now or previously)
    # that does NOT need a re-run; retain the rest with an explicit reason.
    needs_rerun: dict = {}
    for sess in real_missing:
        needs_rerun[sess] = "missing (no result.json — re-run to extract)"
    for sess in con["conflicts"]:
        needs_rerun[sess] = "conflict (duplicate result — clean re-run required)"
    for r in con["rejected"]:
        needs_rerun[r["session"]] = "rejected (schema-invalid; quarantined)"
    for f in failed:
        needs_rerun.setdefault(f["session"], "emit-failed (retry)")

    done = (already | set(emitted)) - set(needs_rerun)
    purged: list[str] = []
    if not keep:
        for sess in sorted(done):
            _purge_session(run_id, sess)
            purged.append(sess)

    summary = {
        "run_id": run_id,
        "emitted": emitted,
        "skipped_already_emitted": skipped_dupe,
        "missing": real_missing,
        "conflicts": con["conflicts"],
        "rejected": con["rejected"],
        "orphans": con.get("orphans", []),
        "failed": failed,
        "warnings": {i["session"]: i["warnings"]
                     for i in con["emitted_ok"] if i["warnings"]},
        "purged": purged,
        "retained": needs_rerun,
    }

    fully_clean = not (real_missing or con["conflicts"]
                       or con["rejected"] or failed)
    if clean and fully_clean:
        for d in (staging_dir(run_id), results_dir(run_id)):
            shutil.rmtree(d, ignore_errors=True)
        summary["cleaned"] = True
    return summary
