#!/usr/bin/env python3
"""clawgate POSTER — turn an APPROVED repo-cos proposal into a durable clawgate Task card.

When Zach replies "N. approve" to a digest, `exclusions.parse_reply` maps position N to the
FULL proposal and hands it here. We POST it to his existing clawgate Tasks queue (the durable
adjudication+dispatch surface) so it lands as a one-tap-Dispatch card, and — on a SUCCESSFUL
post only — its evidence refs are suppressed so it won't re-nag next week.

Design (mirrors the rest of repo-cos: best-effort, never raises, stdlib-only):
  * `load_creds()` parses ~/.claude/clawgate.env (simple KEY=VALUE) → {CLAWGATE_API_URL,
    CLAWGATE_HOOK_TOKEN}. Missing file / keys → {} (→ post_task no-ops to None).
  * `build_task_body(proposal)` → clean markdown card matching the homelab task-drafter's
    style (lead `**🤖 repo-cos · APPROVED**`, then the goal, structured fields, evidence).
  * `post_task(directory, body)` → POST {API_URL}/api/tasks with the Bearer hook token, 15s
    timeout. Returns the created task id (int) on success, else None (logged). NEVER raises.

This is the ONLY new network in the approve path. Everything upstream (the parser) is pure.
Reference impl: homelab-talos …/agent-pods/task-drafter/trigger-configmap.yaml `route_clawgate`.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CLAWGATE_ENV = Path("~/.claude/clawgate.env").expanduser()
POST_TIMEOUT = 15  # seconds — a hung clawgate must not stall the weekly run
TITLE_MAX = 80


def _log(msg: str) -> None:
    print(f"  clawgate: {msg}", file=sys.stderr)


# ---- credentials -------------------------------------------------------------------

def load_creds(path: Path | None = None) -> dict:
    """Parse ~/.claude/clawgate.env (simple `KEY=VALUE`, `#` comments, optional quotes) into
    a dict. Returns only the keys we use ({CLAWGATE_API_URL, CLAWGATE_HOOK_TOKEN} when
    present). Missing/unreadable file → {} (post_task then no-ops). Never raises."""
    p = path or CLAWGATE_ENV
    creds: dict[str, str] = {}
    try:
        if not p.exists():
            return {}
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                creds[key] = val
    except Exception as exc:  # noqa: BLE001
        _log(f"could not read {p}: {exc}")
        return {}
    return creds


# ---- card body ---------------------------------------------------------------------

def build_task_title(proposal: dict) -> str:
    """The Task title = the proposal title, trimmed to <=80 chars (clawgate `directory`)."""
    title = str((proposal or {}).get("title") or "").strip()
    if not title:
        title = "repo-cos approved proposal"
    return title[:TITLE_MAX]


def build_task_body(proposal: dict) -> str:
    """Clean markdown card for the clawgate Task. Matches the task-drafter card style:
    lead badge, the goal (why), then structured fields, ending with the evidence refs.
    NO raw json dump — the operator adjudicates + dispatches, they don't read scratch output.
    """
    p = proposal or {}
    title = str(p.get("title") or "").strip()
    why = str(p.get("why") or "").strip()
    approach = str(p.get("approach") or "").strip()
    repo = str(p.get("repo") or "").strip()
    effort = str(p.get("effort") or "").strip().upper()
    ci = bool(p.get("ci_verifiable"))
    evidence = [str(e).strip() for e in (p.get("evidence") or []) if str(e).strip()]

    out: list[str] = ["**🤖 repo-cos · APPROVED**", ""]
    # goal line: the title, then the "why" underneath if present.
    if title:
        out.append(f"**{title}**")
    if why:
        out.append(why)
    if approach:
        out.append(f"\n**Approach:** {approach}")
    if repo:
        out.append(f"\n**Repo:** {repo}")
    if effort:
        note = " · CI-verifiable" if ci else " · needs manual verification"
        out.append(f"\n**Effort:** {effort}{note}")
    if evidence:
        out.append("\n**Evidence:**")
        for ref in evidence:
            out.append(f"- `{ref}`")
    return "\n".join(out)


# ---- HTTP post ---------------------------------------------------------------------

def _post(url: str, payload: dict, token: str, *, timeout: int = POST_TIMEOUT) -> str:
    """Isolated network POST (stdlib urllib) — mocked in tests. Returns the response body."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def post_task(directory: str, body: str, *, creds: dict | None = None,
              _post=_post) -> int | None:
    """POST a Task to clawgate's `/api/tasks`. Returns the created task id (int) on success,
    else None. BEST-EFFORT: any error (no creds, unreachable, non-JSON, no id) is logged and
    yields None — NEVER raises. `creds`/`_post` are injectable for tests (no real network)."""
    c = creds if creds is not None else load_creds()
    api = (c.get("CLAWGATE_API_URL") or "").rstrip("/")
    token = c.get("CLAWGATE_HOOK_TOKEN") or ""
    if not api or not token:
        _log("CLAWGATE_API_URL/CLAWGATE_HOOK_TOKEN not set (~/.claude/clawgate.env) — skipping")
        return None

    url = f"{api}/api/tasks"
    payload = {"directory": directory, "body": body}
    try:
        raw = _post(url, payload, token)
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        _log(f"POST {url} failed: HTTP {exc.code} {exc.reason}")
        return None
    except Exception as exc:  # noqa: BLE001
        _log(f"POST {url} failed: {exc}")
        return None

    # Parse the {"id": <n>} response. A 2xx with no parseable id still counts as a failure
    # for suppression purposes — we only suppress when we have a concrete task id to record.
    try:
        obj = json.loads(raw)
        tid = obj.get("id")
        if isinstance(tid, bool) or not isinstance(tid, int):
            _log(f"POST {url} returned no integer id (got {raw[:120]!r}) — not suppressing")
            return None
        return tid
    except Exception as exc:  # noqa: BLE001
        _log(f"POST {url} succeeded but response unparseable ({exc}) — not suppressing")
        return None
