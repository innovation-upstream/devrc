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
  * `resolve_repo_fullname(repo_name)` → the proposal's local repo basename → its GitHub
    `owner/name` (from `git remote get-url origin`), so the Task carries a dispatch-ready
    `repo` pre-fill. Best-effort/stdlib-only; unknown/unparseable → "" (repo left unset).
  * `post_task(directory, body, *, repo="", model="")` → POST {API_URL}/api/tasks with the
    Bearer hook token, 15s timeout. The payload carries the resolved `repo` (and `model`)
    ONLY when non-empty — a bare call still sends exactly {"directory","body"} (back-compat).
    Returns the created task id (int) on success, else None (logged). NEVER raises.

This is the ONLY new network in the approve path. Everything upstream (the parser) is pure.
Reference impl: homelab-talos …/agent-pods/task-drafter/trigger-configmap.yaml `route_clawgate`.
"""
from __future__ import annotations

import json
import subprocess
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


# ---- repo → GitHub full-name resolver ----------------------------------------------

def _git_remote(path: str, *, timeout: int = 10) -> str:
    """Run `git -C <path> remote get-url origin`, returning the stripped stdout, or "" on any
    failure (non-zero, missing git, timeout, …). Isolated + injectable so tests never shell
    out. Never raises."""
    try:
        proc = subprocess.run(
            ["git", "-C", path, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"git remote for {path} failed: {exc}")
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _parse_github_fullname(url: str) -> str:
    """Parse a git remote URL to `owner/name`. Strips a leading `git@github.com:`,
    `https://github.com/`, `http://github.com/`, or `ssh://git@github.com/`, and a trailing
    `.git`. Anything we don't recognize as a GitHub remote → "" (best-effort)."""
    u = (url or "").strip()
    if not u:
        return ""
    for prefix in ("git@github.com:", "ssh://git@github.com/",
                   "https://github.com/", "http://github.com/"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    else:
        return ""  # not a GitHub remote we can map
    if u.endswith(".git"):
        u = u[:-4]
    parts = [seg for seg in u.strip("/").split("/") if seg]
    if len(parts) != 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def resolve_repo_fullname(repo_name: str, *, repos=None, _run=None) -> str:
    """Map a proposal's repo BASENAME (e.g. `civitai`, `devrc`) → its GitHub `owner/name` by
    reading `git remote get-url origin` on the matching local path. A naive basename is WRONG
    (e.g. `datapacket-talos → civitai/talos-infra`), so we always resolve from the remote.

    `repos` defaults to `scan.DEFAULT_REPOS` (a list of `~`-relative paths); `_run` defaults to
    `_git_remote` — both injectable so the function is pure/testable (no real fs/git needed).
    BEST-EFFORT, stdlib-only, NEVER raises: unknown name / no matching path / no remote /
    unparseable → "" (the Task's repo is then left unset → clawgate's dispatch default)."""
    name = (repo_name or "").strip()
    if not name:
        return ""
    try:
        if repos is None:
            import scan  # lazy — avoids a top-level cycle; picks up monkeypatched value
            repos = scan.DEFAULT_REPOS
        run = _run if _run is not None else _git_remote

        path = None
        for entry in repos or []:
            p = str(Path(str(entry)).expanduser())
            if Path(p).name == name:
                path = p
                break
        if path is None:
            _log(f"no local repo path matches {name!r} — Task repo left unset")
            return ""

        full = _parse_github_fullname(run(path))
        if not full:
            _log(f"could not resolve GitHub full-name for {name!r} — Task repo left unset")
        return full
    except Exception as exc:  # noqa: BLE001
        _log(f"resolve_repo_fullname({name!r}) failed: {exc}")
        return ""


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


def post_task(directory: str, body: str, *, repo: str = "", model: str = "",
              creds: dict | None = None, _post=_post) -> int | None:
    """POST a Task to clawgate's `/api/tasks`. Returns the created task id (int) on success,
    else None. BEST-EFFORT: any error (no creds, unreachable, non-JSON, no id) is logged and
    yields None — NEVER raises. `creds`/`_post` are injectable for tests (no real network).

    `repo` (a GitHub `owner/name`) and `model` pre-fill the Task's dispatch config; each is
    added to the payload ONLY when non-empty, so a bare call still sends exactly the old
    2-key {"directory","body"} payload (backward-compatible)."""
    c = creds if creds is not None else load_creds()
    api = (c.get("CLAWGATE_API_URL") or "").rstrip("/")
    token = c.get("CLAWGATE_HOOK_TOKEN") or ""
    if not api or not token:
        _log("CLAWGATE_API_URL/CLAWGATE_HOOK_TOKEN not set (~/.claude/clawgate.env) — skipping")
        return None

    url = f"{api}/api/tasks"
    payload = {"directory": directory, "body": body}
    if repo:
        payload["repo"] = repo
    if model:
        payload["model"] = model
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
