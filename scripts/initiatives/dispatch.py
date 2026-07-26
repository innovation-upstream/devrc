#!/usr/bin/env python3
"""clawgate DISPATCH — turn an initiative's grounded next-step into a durable clawgate Task.

Phase-2b of the initiatives subsystem. The VIEWER (workbench, LAN-bound, Zach's user) holds
the clawgate token and POSTs a Task card here; the in-cluster hardened devpod gets NO token.
Nothing actually RUNS until Zach taps "Dispatch" inside clawgate — so there are TWO human
gates: (1) he taps "Dispatch next step" on a card, (2) he taps "Dispatch" on the resulting
clawgate Task. This module only CREATES the adjudicable card; it never runs an agent.

Mirrors `scripts/repo-cos/clawgate.py` closely (same robustness contract — best-effort,
stdlib urllib, NEVER raises, returns the task id | None), adapted to an initiative VIEW dict
(the `flat` shape `viewer.build_model` produces) instead of a repo-cos proposal:
  * `load_creds()`            → parse ~/.claude/clawgate.env (KEY=VALUE) → {API_URL, TOKEN}.
  * `build_task_title(view)`  → the short DISPLAY LABEL for clawgate's `directory` field.
  * `build_task_body(view, recommendation)` → a clean markdown card (the recommendation +
                                its basis + grounded context from the view ONLY — no invention,
                                no raw json dump).
  * `resolve_repo_fullname(repo_path)` → the view's `repo` IS an absolute local path, so read
                                `git remote get-url origin` on it → GitHub `owner/name` | "".
  * `post_task(directory, body, ...)` → POST {API}/api/tasks (Bearer) → task id | None.
  * `dispatch_initiative(view)` → the convenience orchestrator the viewer endpoint calls:
                                compute rec+title+body+repo, post, return {ok, task_id, error}.

Anti-confabulation carries through: the body is built ONLY from the grounded recommendation
(`nextstep.recommend_next_step`) + real view fields, never invented content.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CLAWGATE_ENV = Path("~/.claude/clawgate.env").expanduser()
POST_TIMEOUT = 15  # seconds — a hung clawgate must not stall the dispatch request
TITLE_MAX = 80

# The pure recommendation deriver, loaded by explicit importlib path (the package's sibling
# cross-load convention — NOT sys.path, whose mail-actions/llm.py would shadow). Lazy so a
# bare `import dispatch` stays cheap and this module has no hard sibling dependency at load.
_NEXTSTEP_PATH = Path(__file__).resolve().parent / "nextstep.py"
_nextstep_mod = None


def _log(msg: str) -> None:
    print(f"  dispatch: {msg}", file=sys.stderr)


def _nextstep():
    global _nextstep_mod
    if _nextstep_mod is None:
        spec = importlib.util.spec_from_file_location(
            "initiatives_dispatch_nextstep", _NEXTSTEP_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {_NEXTSTEP_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _nextstep_mod = mod
    return _nextstep_mod


# ---- credentials -------------------------------------------------------------------

def load_creds(path: Path | None = None) -> dict:
    """Parse ~/.claude/clawgate.env (simple `KEY=VALUE`, `#` comments, optional quotes) into
    a dict. Returns only the keys we use ({CLAWGATE_API_URL, CLAWGATE_HOOK_TOKEN} when
    present). Missing/unreadable file → {} (post_task then no-ops). Never raises. Identical
    parser to repo-cos/clawgate.py (single behaviour across the two posters)."""
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
    `.git`. Anything we don't recognize as a GitHub remote → "" (best-effort). Copied from
    repo-cos/clawgate.py."""
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


def resolve_repo_fullname(repo_path: str, *, _run=None) -> str:
    """Map an initiative view's `repo` (an ABSOLUTE LOCAL PATH) → its GitHub `owner/name` by
    reading `git remote get-url origin` on that path directly.

    Unlike repo-cos (which resolves a basename against a known repo list), the view already
    carries the exact checkout path, so we run git right on it. `_run` defaults to
    `_git_remote` — injectable so the function is pure/testable (no real fs/git needed).
    BEST-EFFORT, stdlib-only, NEVER raises: no path / no remote / unparseable → "" (the
    Task's repo is then left unset → clawgate's dispatch default)."""
    path = (repo_path or "").strip()
    if not path:
        return ""
    try:
        run = _run if _run is not None else _git_remote
        full = _parse_github_fullname(run(path))
        if not full:
            _log(f"could not resolve GitHub full-name for {path!r} — Task repo left unset")
        return full
    except Exception as exc:  # noqa: BLE001
        _log(f"resolve_repo_fullname({path!r}) failed: {exc}")
        return ""


# ---- card title + body -------------------------------------------------------------

def build_task_title(view: dict) -> str:
    """The clawgate `directory` field — a short DISPLAY LABEL (NOT a path), style
    `"<repo_name> · <slug>"`. Falls back to the slug, then a generic label. Trimmed to 80."""
    v = view or {}
    repo_name = str(v.get("repo_name") or "").strip()
    slug = str(v.get("slug") or "").strip()
    if repo_name and slug:
        title = f"{repo_name} · {slug}"
    elif slug:
        title = slug
    else:
        title = "initiative next step"
    return title[:TITLE_MAX]


def build_task_body(view: dict, recommendation: dict | None) -> str:
    """A clean markdown card for the clawgate Task: the grounded recommendation (bold) + a
    line noting its basis (a *parsed handoff step* vs an *inferred* suggestion), then grounded
    context sourced ONLY from the view (repo, momentum+age, status, last prompt, open
    investigations, open PRs) and a trailing source line (slug/repo + the handoff doc path).
    NO invented content, NO raw json dump."""
    v = view or {}
    ns = _nextstep()

    out: list[str] = ["**📌 initiatives · next step**", ""]

    rec = recommendation or {}
    text = str(rec.get("text") or "").strip()
    basis = str(rec.get("basis") or "").strip()
    if text:
        out.append(f"**{text}**")
        if basis == "handoff":
            out.append("_Basis: the parsed next-step from your handoff doc._")
        elif basis:
            out.append(f"_Basis: inferred ({ns.basis_label(basis)})._")
    else:
        out.append("_No grounded next step could be derived for this initiative._")

    # -- grounded context: every line below is a verbatim/real view field, never invented. --
    repo_name = str(v.get("repo_name") or "").strip()
    repo_path = str(v.get("repo") or "").strip()
    momentum = str(v.get("momentum") or "").strip()
    age = str(v.get("age") or "").strip()
    status = str(v.get("status") or "").strip()
    face = v.get("face_message")
    face_text = str(face.get("text") or "").strip() if isinstance(face, dict) else ""
    investigations = [str(x).strip() for x in (v.get("open_investigations") or [])
                      if str(x).strip()]
    open_prs = [p for p in (v.get("open_prs") or []) if isinstance(p, dict)]

    ctx: list[str] = []
    if repo_name:
        ctx.append(f"**Repo:** {repo_name}")
    if momentum:
        ctx.append(f"**Momentum:** {momentum}" + (f" · last touched {age} ago" if age else ""))
    if status:
        ctx.append(f"**Current status:** {status}")
    if face_text:
        ctx.append(f"**Your last prompt:** {face_text}")
    if ctx:
        out.append("")
        out.extend(ctx)

    if investigations:
        out.append("")
        out.append("**Open investigations:**")
        for q in investigations:
            out.append(f"- {q}")

    if open_prs:
        out.append("")
        out.append("**Open PRs:**")
        for p in open_prs:
            num = p.get("number")
            title = str(p.get("title") or "").strip()
            head = f"#{num}" if num is not None else "PR"
            out.append(f"- {head}{(' ' + title) if title else ''}")

    # trailing source line — where this card was grounded (slug/repo + the handoff doc path).
    slug = str(v.get("slug") or "").strip()
    current_doc = str(v.get("current_doc") or "").strip()
    src = f"_source: {slug}" + (f" ({repo_name})" if repo_name else "")
    if current_doc:
        src += f" · {current_doc}"
    elif repo_path:
        src += f" · {repo_path}"
    src += "_"
    out.append("")
    out.append(src)

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
    added to the payload ONLY when non-empty, so a bare call sends exactly {"directory",
    "body"}. OMITTING `model` → clawgate applies its own default (deepseek), which is correct."""
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

    try:
        obj = json.loads(raw)
        tid = obj.get("id")
        if isinstance(tid, bool) or not isinstance(tid, int):
            _log(f"POST {url} returned no integer id (got {raw[:120]!r})")
            return None
        return tid
    except Exception as exc:  # noqa: BLE001
        _log(f"POST {url} succeeded but response unparseable ({exc})")
        return None


# ---- orchestrator ------------------------------------------------------------------

def dispatch_initiative(view: dict, *, creds=None, poster=None) -> dict:
    """Create a clawgate Task for one initiative view's grounded next step. Convenience over
    the pieces above: derive the recommendation (nextstep), build the title + body, resolve
    the repo, and POST. `poster(directory, body, *, repo, creds) -> id|None` is injectable
    (defaults to `post_task`) so tests never hit the network.

    Returns `{"ok": bool, "task_id": int|None, "error": str|None}`. NEVER raises — any failure
    (no grounded recommendation, creds missing, clawgate unreachable) is reported in the dict.
    A view with no grounded next step is `ok=False` with a clear error (no empty Task is
    posted)."""
    try:
        v = view or {}
        ns = _nextstep()
        recommendation = ns.recommend_next_step(v)
        if not recommendation or not str(recommendation.get("text") or "").strip():
            return {"ok": False, "task_id": None,
                    "error": "no grounded next step to dispatch for this initiative"}

        directory = build_task_title(v)
        body = build_task_body(v, recommendation)
        repo = resolve_repo_fullname(v.get("repo") or "")

        post = poster if poster is not None else post_task
        task_id = post(directory, body, repo=repo, creds=creds)
        if not isinstance(task_id, int) or isinstance(task_id, bool):
            return {"ok": False, "task_id": None,
                    "error": "clawgate did not return a task id (unreachable or no creds)"}
        return {"ok": True, "task_id": task_id, "error": None}
    except Exception as exc:  # noqa: BLE001 - best-effort: a failure is a result, not a raise
        return {"ok": False, "task_id": None, "error": f"{type(exc).__name__}: {exc}"}
