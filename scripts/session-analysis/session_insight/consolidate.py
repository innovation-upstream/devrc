#!/usr/bin/env python3
"""consolidate — merge/verify the result.json files from an extraction run.

Makes a partial or duplicated (subagent fan-out) run SAFE: only clean, unique,
schema-valid results are handed to write.py for emitting. Invoked by write.py
before emitting, and echoed as a checklist in the `activity` skill.

Rules (spec §9):
  * UNION by `session` — exactly one result per expected candidate session.
  * MISSING  — an expected session with no result.json → reported, NOT emitted
               (a re-run picks it up).
  * CONFLICT — two result files for the same session → keep NEITHER, flag it
               (forces a clean re-run for that session).
  * SCHEMA   — each result validated via schema.validate; a failing result is
               QUARANTINED (moved to `<results>/rejected/`) and reported.
  * ORPHAN   — a result whose echoed `session` matches NO expected candidate
               (mis-named file, stale re-run, wrong run-id) → reported so it is
               never silently dropped; NOT emitted.

Returns `{emitted_ok, missing, conflicts, rejected, orphans}`.
"""
from __future__ import annotations

import json
from pathlib import Path

from schema import validate, vocab_warnings

_SUFFIX = ".result.json"


def _session_from_name(path: Path) -> str:
    name = path.name
    return name[:-len(_SUFFIX)] if name.endswith(_SUFFIX) else path.stem


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as e:
        return None, f"unparseable JSON: {e}"


def _scan(results_path: Path) -> dict:
    """{session_key: [(path, payload|None, err|None)]} for every *.result.json
    directly under results_path (the `rejected/` subdir is excluded). The key is
    the payload's own `session` when present (so a fan-out duplicate written to a
    DIFFERENT filename is still detected as a conflict), else the filename stem."""
    groups: dict = {}
    if not results_path.is_dir():
        return groups
    for p in sorted(results_path.glob(f"*{_SUFFIX}")):
        if p.parent.name == "rejected":
            continue
        payload, err = _load(p)
        if isinstance(payload, dict) and payload.get("session"):
            key = payload["session"]
        else:
            key = _session_from_name(p)
        groups.setdefault(key, []).append((p, payload, err))
    return groups


def consolidate(expected_sessions: list[str], results_path,
                quarantine: bool = True) -> dict:
    results_path = Path(results_path)
    groups = _scan(results_path)

    emitted_ok: list[dict] = []
    missing: list[str] = []
    conflicts: list[str] = []
    rejected: list[dict] = []

    expected_set = set(expected_sessions)
    orphans: list[dict] = [
        {"session": key, "paths": [str(p) for p, _pl, _e in entries]}
        for key, entries in sorted(groups.items())
        if key not in expected_set
    ]

    for session in expected_sessions:
        entries = groups.get(session, [])
        if not entries:
            missing.append(session)
            continue
        if len(entries) > 1:
            # keep NEITHER — quarantine every duplicate so a clean re-run is forced
            conflicts.append(session)
            for path, _payload, _err in entries:
                _quarantine(path, quarantine)
            continue
        path, payload, err = entries[0]
        if err:
            rejected.append({"session": session, "path": str(path), "errors": [err]})
            _quarantine(path, quarantine)
            continue
        errs = validate(payload)
        if errs:
            rejected.append({"session": session, "path": str(path), "errors": errs})
            _quarantine(path, quarantine)
            continue
        emitted_ok.append({
            "session": session,
            "payload": payload,
            "path": str(path),
            "warnings": vocab_warnings(payload),
        })

    return {"emitted_ok": emitted_ok, "missing": missing,
            "conflicts": conflicts, "rejected": rejected, "orphans": orphans}


def _quarantine(path: Path, do_move: bool) -> None:
    if not do_move:
        return
    rej = path.parent / "rejected"
    rej.mkdir(parents=True, exist_ok=True)
    try:
        path.replace(rej / path.name)
    except OSError:
        pass
