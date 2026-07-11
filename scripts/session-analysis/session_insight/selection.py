#!/usr/bin/env python3
"""selection — pick SETTLED + un-extracted sessions to extract (spec §4/§5).

(Named `selection`, not `select`, so it never shadows the stdlib `select`
module — a same-named file on sys.path[0] breaks urllib/selectors/subprocess.)

All the ClickHouse access is in `select_candidates` (thin I/O); the decision
logic is the pure `choose()` (unit-tested with fixture rows + synthetic mtimes,
no live CH). A session is a CANDIDATE iff, deterministically:

  1. it has a `session-summary` row (Layer A ground truth exists), AND
  2. it is SETTLED — no new `source=claude` activity for N hours AND the
     transcript file's mtime is older than N hours (belt-and-suspenders; the
     message tailer runs on a timer so `ts` can lag the live file), AND
  3. it is not already extracted (argMax over existing `session-insight` rows)
     — with the one exception that a prior `unreadable` row is re-attempted when
     the transcript has since GROWN (mtime newer than the insight's ts).

`--settle-hours 0` disables the settle gate; `--force` re-prepares regardless of
an existing row (and bypasses the settle gate). Reasons for every skip are
returned so a run is auditable (`session, reason`).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Sibling packages live outside this dir — wire them onto sys.path.
_SA_DIR = Path(__file__).resolve().parent.parent            # scripts/session-analysis
_ROOT = _SA_DIR.parent                                       # scripts/
sys.path.insert(0, str(_ROOT / "validation"))               # chquery
sys.path.insert(0, str(_ROOT / "collector" / "claude"))     # _shared
import chquery as Q                                          # noqa: E402
from _shared import ch_ts_to_epoch, iter_transcripts, projects_roots  # noqa: E402

SKIP_REASONS = ("already-extracted", "not-settled", "no-rollup",
                "no-transcript", "over-limit")


# --------------------------------------------------------------------------- #
# Queries (alias-shadow safe — no `AS <alias>` reuses a WHERE-filtered column)
# --------------------------------------------------------------------------- #
def _host_filter(host: str | None) -> str:
    return f" AND host={Q.sql_quote(host)}" if host else ""


def q_rollups(win: int, host: str | None = None) -> str:
    """Latest `session-summary` (ground truth) per session within the window."""
    return (
        "SELECT session, "
        "argMax(host, ingested_at) AS sess_host, "
        "argMax(project, ingested_at) AS project, "
        "argMax(toString(payload), ingested_at) AS payload, "
        "max(ts) AS summary_ts "
        "FROM activity.events "
        f"WHERE source='claude' AND kind='session-summary' AND ts>now()-{win}"
        f"{_host_filter(host)} GROUP BY session"
    )


def q_last_activity(win: int, host: str | None = None) -> str:
    """Newest activity ts per session across ALL source=claude rows (settle gate)."""
    return (
        "SELECT session, max(ts) AS last_ts FROM activity.events "
        f"WHERE source='claude' AND ts>now()-{win}{_host_filter(host)} "
        "GROUP BY session"
    )


def q_extracted(host: str | None = None) -> str:
    """Already-extracted set (WINDOWLESS — a long-ago extraction still counts),
    with the latest `unreadable` flag + newest insight ts per session."""
    return (
        "SELECT session, "
        "argMax(simpleJSONExtractBool(toString(payload),'unreadable'), ingested_at) "
        "AS was_unreadable, "
        "max(ts) AS insight_ts "
        "FROM activity.events "
        f"WHERE source='claude' AND kind='session-insight'{_host_filter(host)} "
        "GROUP BY session"
    )


# --------------------------------------------------------------------------- #
# Pure decision core
# --------------------------------------------------------------------------- #
def _parse_payload(p) -> dict:
    if isinstance(p, dict):
        return p
    if isinstance(p, str) and p:
        try:
            d = json.loads(p)
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def choose(rollups: list[dict], last_activity: dict, extracted: dict,
           transcripts: dict, now_epoch: float, settle_seconds: float,
           limit: int | None, force: bool) -> tuple[list[dict], list[tuple]]:
    """Pure candidate selection.

    * rollups     — [{session, project, payload(str|dict)}] (has-rollup by construction)
    * last_activity — {session: last_ts_epoch}
    * extracted   — {session: {"was_unreadable": bool, "insight_ts": epoch|None}}
    * transcripts — {session: {"path": str, "mtime": epoch}}
    """
    candidates: list[dict] = []
    skips: list[tuple] = []
    gate = settle_seconds > 0

    for r in rollups:
        sess = r.get("session")
        gt = _parse_payload(r.get("payload"))
        tr = transcripts.get(sess)

        # A transcript is required to chunk — check FIRST so a missing file gives
        # the informative `no-transcript` reason rather than `not-settled`.
        if tr is None:
            skips.append((sess, "no-transcript"))
            continue

        end_ts = gt.get("end_ts")
        la = ch_ts_to_epoch(end_ts) if end_ts else None
        if la is None:
            la = last_activity.get(sess)

        if gate and not force:
            act_settled = la is not None and (now_epoch - la) > settle_seconds
            mtime_settled = (now_epoch - tr["mtime"]) > settle_seconds
            if not (act_settled and mtime_settled):
                skips.append((sess, "not-settled"))
                continue

        ex = extracted.get(sess)
        if ex is not None and not force:
            if not ex.get("was_unreadable"):
                skips.append((sess, "already-extracted"))
                continue
            insight_ts = ex.get("insight_ts")
            grew = insight_ts is not None and tr["mtime"] > insight_ts
            if not grew:
                skips.append((sess, "already-extracted"))
                continue

        candidates.append({
            "session": sess,
            "project": r.get("project") or "",
            "cwd": gt.get("cwd") or "",
            "transcript_path": tr["path"],
            "end_ts": end_ts,
            "summary_ts": r.get("summary_ts"),
            "ground_truth": gt,
            "_la": la or 0,
        })

    # Most-recently-active first, then apply the batch cap.
    candidates.sort(key=lambda c: c["_la"], reverse=True)
    if limit is not None and len(candidates) > limit:
        for c in candidates[limit:]:
            skips.append((c["session"], "over-limit"))
        candidates = candidates[:limit]
    for c in candidates:
        c.pop("_la", None)
    return candidates, skips


# --------------------------------------------------------------------------- #
# I/O gather
# --------------------------------------------------------------------------- #
def _transcript_index(roots: list[str]) -> dict:
    """{session_stem: {"path", "mtime"}} for every real transcript."""
    idx: dict = {}
    for path, session in iter_transcripts(roots):
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        # keep the newest transcript if a stem somehow appears twice
        if session not in idx or mtime > idx[session]["mtime"]:
            idx[session] = {"path": path, "mtime": mtime}
    return idx


def select_candidates(client, days: int, settle_hours: float, limit: int | None,
                      force: bool, host: str | None = None,
                      roots: list[str] | None = None,
                      now_epoch: float | None = None) -> tuple[list[dict], list[tuple]]:
    """Query CH + resolve transcript mtimes, then run `choose`."""
    win = days * 86400
    roots = roots if roots is not None else projects_roots()
    now_epoch = now_epoch if now_epoch is not None else time.time()

    rollups = client.rows(q_rollups(win, host))
    last_rows = client.rows(q_last_activity(win, host))
    ext_rows = client.rows(q_extracted(host))

    last_activity = {r["session"]: ch_ts_to_epoch(r.get("last_ts"))
                     for r in last_rows if r.get("session")}
    extracted = {}
    for r in ext_rows:
        s = r.get("session")
        if not s:
            continue
        extracted[s] = {
            "was_unreadable": bool(int(r.get("was_unreadable") or 0)),
            "insight_ts": ch_ts_to_epoch(r.get("insight_ts")),
        }
    transcripts = _transcript_index(roots)
    return choose(rollups, last_activity, extracted, transcripts,
                  now_epoch, settle_hours * 3600.0, limit, force)
