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
  * `build_tags(proposal)` → the clawgate task tags for a proposal. Today that is at most
    one `initiative:<slug>` (clawgate 0.7.75+), built from the slug the digest ALREADY
    resolved and persisted — never re-resolved here.
  * `post_task(directory, body, *, repo="", model="", tags=None)` → POST {API_URL}/api/tasks
    with the Bearer hook token, 15s timeout. The payload carries the resolved `repo`,
    `model` and `tags` ONLY when non-empty — a bare call still sends exactly
    {"directory","body"} (back-compat). Returns the created task id (int) on success, else
    None (logged). NEVER raises.

This is the ONLY new network in the approve path. Everything upstream (the parser) is pure.
Reference impl: homelab-talos …/agent-pods/task-drafter/trigger-configmap.yaml `route_clawgate`.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CLAWGATE_ENV = Path("~/.claude/clawgate.env").expanduser()
POST_TIMEOUT = 15  # seconds — a hung clawgate must not stall the weekly run
TITLE_MAX = 80

# ---- clawgate task-tag grammar (clawgate 0.7.75+, claudedocs/clawgate-task-tags-spec.md §3)
# lowercase; charset [a-z0-9._/-]; at most ONE ':' separating namespace:value; <=64 runes
# per tag; <=20 tags per task. A tag that violates the grammar 400s the WHOLE request, so
# we enforce it client-side and DROP anything that can't be made valid.
TAG_MAX_LEN = 64
TAG_MAX_COUNT = 20
_TAG_BODY_RE = re.compile(r"^[a-z0-9._/-]+$")
_WS_RE = re.compile(r"\s+")


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


# ---- task tags -----------------------------------------------------------------------

def normalize_tag(tag) -> str | None:
    """Normalize ONE tag to clawgate's grammar, or None if it can't be made valid.

    lowercase → trim → collapse internal whitespace to `-`, then validate: at most one
    `:` (namespace separator), each side non-empty and drawn from `[a-z0-9._/-]`, whole
    tag <=64 runes. Anything else is DROPPED, never mangled: silently rewriting
    `remix:clip_editor` into some other tag would produce a routing key that points at
    the wrong thing, which is exactly the failure a tag is supposed to prevent."""
    try:
        t = _WS_RE.sub("-", str(tag or "").strip().lower()).strip("-")
    except Exception:  # noqa: BLE001
        return None
    if not t:
        return None
    if len(t) > TAG_MAX_LEN:
        _log(f"dropping tag {t!r} — {len(t)} runes exceeds the {TAG_MAX_LEN}-rune cap")
        return None
    parts = t.split(":")
    if len(parts) > 2:
        _log(f"dropping tag {t!r} — more than one ':' (namespace:value only)")
        return None
    for part in parts:
        if not part or not _TAG_BODY_RE.match(part):
            _log(f"dropping tag {t!r} — not [a-z0-9._/-]")
            return None
    return t


def normalize_tags(tags) -> list[str]:
    """Normalize a tag list: drop the invalid, de-duplicate, sort, cap at 20. Never raises.

    Sorted so the payload is deterministic (stable tests, stable clawgate render)."""
    out: set[str] = set()
    for raw in tags or []:
        t = normalize_tag(raw)
        if t:
            out.add(t)
    clean = sorted(out)
    if len(clean) > TAG_MAX_COUNT:
        _log(f"capping {len(clean)} tags to the first {TAG_MAX_COUNT}")
        clean = clean[:TAG_MAX_COUNT]
    return clean


def build_tags(proposal) -> list[str]:
    """The clawgate task tags for an APPROVED proposal. Never raises; [] on any doubt.

    Today: at most ONE `initiative:<slug>` tag. The slug is the one the digest ALREADY
    resolved and persisted into `last_emailed.json` (`proposal["initiative"]`) — it is
    deliberately NOT re-resolved here, so the tag matches the `↳ relates to:` breadcrumb
    Zach saw when he approved, and the approve path gains no new I/O (no cross-cluster
    read of the initiatives store). An older `last_emailed.json` with no `initiative`
    field simply yields no tag. It is then filtered by `routing.taggable_slug` (drops
    document/date/id/filler slugs) and normalized to the tag grammar.

    🔴 The emitted tag ALWAYS equals `initiative:<ledger-slug>` byte-for-byte, enforced
    STRUCTURALLY: the normalized result is compared to the exact tag we asked for and
    dropped on ANY difference (see the guard below). The denylist alone is not enough —
    it polices only one of `normalize_tag`'s mutations.

    ── SEAM: a future `runbook:<name>` tag ────────────────────────────────────────────
    Deliberately NOT emitted today. `runbook:` is HARD-validated by clawgate
    (`runbooks.GetRunbookByName`) so an unknown name 400s the POST — and there is
    currently exactly one runbook (`perf-deep-dive`, a loop already measured as
    non-converging) with no machine endpoint to list/validate names against. When
    clawgate grows a `GET /api/runbooks` (or the runbook set becomes worth routing to),
    slot it in HERE: resolve the name, verify it against that endpoint, and append
    `f"runbook:{name}"` to `raw` below. Do NOT emit an unverified runbook name — the
    fail-open retry in post_task would silently strip the whole tag list.
    ───────────────────────────────────────────────────────────────────────────────────
    """
    try:
        p = proposal or {}
        slug = p.get("initiative") if isinstance(p, dict) else getattr(p, "initiative", None)
        slug = str(slug or "").strip()
        if not slug:
            return []
        import routing  # lazy: keeps `import clawgate` free of the router module
        keep = routing.taggable_slug(slug)
        if not keep:
            return []
        raw = [f"initiative:{keep}"]
        tags = normalize_tags(raw)
        # 🔴 STRUCTURAL JOIN GUARD. The initiatives-side join matches on `tag == slug`
        # byte-for-byte, so the ONLY acceptable outcome is the exact tag we asked for.
        # `normalize_tag` performs THREE mutations — lowercase, whitespace→`-`, and a
        # `-` strip — and the `routing` denylist only polices the first (`not-lowercase`).
        # A slug like `foo bar`, `x  y` or `trailing-dash-` would therefore survive the
        # denylist and be emitted REWRITTEN (`initiative:foo-bar`), joining to nothing.
        # No live slug has that shape today, so pinning it with another denylist rule
        # would only re-encode a SNAPSHOT of the store. Comparing the normalizer's
        # OUTPUT to its input instead makes the invariant hold structurally: any future
        # slug, and any fourth mutation added to `normalize_tag`, is caught here. A drop
        # is the correct degradation — a missing tag is cheap, a tag that joins to
        # nothing is a lie (and the untagged Task is still posted either way).
        want = [f"initiative:{keep}"]
        if tags != want:
            _log(f"dropping initiative tag for slug {keep!r} — the tag grammar would "
                 f"emit {tags!r}, not {want!r}; an emitted tag must equal its ledger "
                 f"slug byte-for-byte or the initiatives-side join misses")
            return []
        return tags
    except Exception as exc:  # noqa: BLE001 - a tag must NEVER cost an approval
        _log(f"build_tags failed (posting untagged): {exc}")
        return []


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
              tags=None, creds: dict | None = None, _post=_post) -> int | None:
    """POST a Task to clawgate's `/api/tasks`. Returns the created task id (int) on success,
    else None. BEST-EFFORT: any error (no creds, unreachable, non-JSON, no id) is logged and
    yields None — NEVER raises. `creds`/`_post` are injectable for tests (no real network).

    `repo` (a GitHub `owner/name`) and `model` pre-fill the Task's dispatch config; `tags`
    (clawgate 0.7.75+) is the routing-key list. Each is added to the payload ONLY when
    non-empty, so a bare call still sends exactly the old 2-key {"directory","body"}
    payload (backward-compatible).

    🔴 TAGGING MUST NEVER COST AN APPROVAL. repo-cos only suppresses a proposal's evidence
    when the POST returns a task id, so a tag-induced 400 would silently lose the approval
    for a week. Two defences: (1) `normalize_tags` drops anything that can't satisfy
    clawgate's grammar before it reaches the wire; (2) if a TAGGED post still fails with
    HTTP 400, we retry EXACTLY ONCE with the `tags` key removed — a tagged-post failure
    degrades to an untagged Task, never to no Task. Both the failure and the retry are
    logged.

    The retry is scoped to 400 ALONE because 400 is the ONLY grammar-rejection code
    (clawgate task-tags spec §6: an invalid tag 400s the whole request). Every other
    status is not a tag problem, so a second request is at best doomed and at worst
    harmful: 401/403 (rotated hook token), 404 (wrong URL) and 429 (rate limit) just burn
    a second doomed call, and 408 — a proxy request-timeout raised AFTER the origin may
    already have created the Task — is the one shape where retrying could double-POST two
    Task cards. Connection errors and 5xx are likewise not retried: one hung weekly run
    must not turn into a retry loop.

    ⚠ KNOWN DIVERGENCE — `400` is a CROSS-REPO WIRE CONTRACT WITH NO TEST ON EITHER SIDE.
    "an invalid tag ⇒ HTTP 400" lives in clawgate's task-tags spec §6, in clawgate's Go
    handler, and here. Nothing on either side asserts it, and clawgate ships from a
    different repo on its own cadence. If clawgate ever moves tag rejection to 422
    (unprocessable) or 409, this backstop stops firing and repo-cos SILENTLY degrades
    from *losing the tag* to *losing the approval* — the proposal is not suppressed, so
    it just re-nags a week later and the only trace is one `POST … failed: HTTP 422`
    line in the weekly run log. So: the literal `400` below is LOAD-BEARING, not
    incidental. Widening the retry set is not free either (see the 408 double-POST
    hazard above) — the fix if clawgate changes is to update this code to match the new
    code, not to retry everything."""
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
    clean_tags = normalize_tags(tags)
    if clean_tags:
        payload["tags"] = clean_tags
    try:
        raw = _post(url, payload, token)
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        _log(f"POST {url} failed: HTTP {exc.code} {exc.reason}")
        # ONLY 400 is the tag-grammar rejection (spec §6). Any other code — incl. 401/403/
        # 404/429, and especially 408 where the origin may already have created the Task —
        # is not a tag problem and must not earn a second request.
        if not (clean_tags and exc.code == 400):
            return None
        # FAIL-OPEN BACKSTOP: exactly one untagged retry. Losing the tag is cheap; losing
        # the approval (it would silently re-nag next week) is not.
        _log(f"retrying ONCE without tags {clean_tags} — a tag must never cost an approval")
        untagged = {k: v for k, v in payload.items() if k != "tags"}
        try:
            raw = _post(url, untagged, token)
        except urllib.error.HTTPError as exc2:
            _log(f"untagged retry also failed: HTTP {exc2.code} {exc2.reason}")
            return None
        except Exception as exc2:  # noqa: BLE001
            _log(f"untagged retry also failed: {exc2}")
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
